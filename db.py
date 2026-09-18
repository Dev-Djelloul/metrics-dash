"""
Client pour Cloudflare D1 via son API REST (https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database).

Pourquoi l'API REST et pas le binding D1 natif : ce backend tourne dans un
Cloudflare Container (process Python classique), pas dans un Worker — les
bindings D1 ne sont accessibles que depuis un Worker. L'API REST HTTP,
elle, est utilisable depuis n'importe où, y compris ce container.
"""
import os

import httpx

# Force un rebuild de l'image Docker (donc un vrai redémarrage du
# conteneur) : les secrets Cloudflare mis à jour via `wrangler secret put`
# ne sont relus qu'au démarrage du conteneur, pas à chaud sur un process
# déjà en cours d'exécution.
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_D1_DATABASE_ID = os.getenv("CF_D1_DATABASE_ID")

_BASE_URL = "https://api.cloudflare.com/client/v4"


def _configured() -> bool:
    return bool(CF_ACCOUNT_ID and CF_API_TOKEN and CF_D1_DATABASE_ID)


def query(sql: str, params: list | None = None) -> list[dict]:
    """Runs a SQL statement against D1 and returns the result rows as dicts."""
    if not _configured():
        raise RuntimeError(
            "D1 non configuré : CF_ACCOUNT_ID / CF_API_TOKEN / CF_D1_DATABASE_ID manquants."
        )

    url = f"{_BASE_URL}/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
        json={"sql": sql, "params": params or []},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"Requête D1 échouée : {data.get('errors')}")

    return data["result"][0]["results"]


def ensure_schema():
    """Crée la table `users` si elle n'existe pas déjà (idempotent, sûr à
    appeler à chaque démarrage)."""
    query(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_user_id TEXT UNIQUE NOT NULL,
            stripe_access_token TEXT NOT NULL,
            account_name TEXT,
            sector TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # ALTER TABLE ... ADD COLUMN échoue si la colonne existe déjà : on
    # l'ignore pour rester idempotent sur une base créée avant l'ajout du
    # secteur d'activité.
    try:
        query("ALTER TABLE users ADD COLUMN sector TEXT")
    except Exception:
        pass


def upsert_user(stripe_user_id: str, access_token: str, account_name: str | None) -> int:
    """Crée l'utilisateur s'il n'existe pas, ou met à jour son jeton s'il
    se reconnecte (ex: après avoir révoqué puis ré-autorisé l'accès côté
    Stripe). Retourne son id interne."""
    query(
        """
        INSERT INTO users (stripe_user_id, stripe_access_token, account_name)
        VALUES (?, ?, ?)
        ON CONFLICT(stripe_user_id) DO UPDATE SET
            stripe_access_token = excluded.stripe_access_token,
            account_name = excluded.account_name
        """,
        [stripe_user_id, access_token, account_name],
    )
    rows = query("SELECT id FROM users WHERE stripe_user_id = ?", [stripe_user_id])
    return rows[0]["id"]


def get_user(user_id: int) -> dict | None:
    rows = query("SELECT * FROM users WHERE id = ?", [user_id])
    return rows[0] if rows else None


def set_user_sector(user_id: int, sector: str) -> None:
    query("UPDATE users SET sector = ? WHERE id = ?", [sector, user_id])
