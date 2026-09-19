"""
Client pour Cloudflare D1 via son API REST (https://developers.cloudflare.com/api/operations/cloudflare-d1-query-database).

Pourquoi l'API REST et pas le binding D1 natif : ce backend tourne dans un
Cloudflare Container (process Python classique), pas dans un Worker — les
bindings D1 ne sont accessibles que depuis un Worker. L'API REST HTTP,
elle, est utilisable depuis n'importe où, y compris ce container.

Modèle de données : `users` porte l'identité (Google et/ou email), tandis
que `stripe_connections` porte l'accès à un compte Stripe — les deux sont
volontairement séparées depuis l'ajout de la connexion Google, pour qu'une
personne puisse s'identifier sans avoir encore connecté Stripe (mode démo
en attendant), puis relier son compte Stripe ensuite sans perdre son
identité/session.
"""
import os

import httpx

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


def _column_names(table: str) -> set[str]:
    return {row["name"] for row in query(f"PRAGMA table_info({table})")}


def ensure_schema():
    """Crée les tables si elles n'existent pas, et migre l'ancien schéma
    mono-table (users.stripe_user_id NOT NULL) vers le nouveau si besoin.
    Idempotent, sûr à appeler à chaque démarrage."""
    query(
        """
        CREATE TABLE IF NOT EXISTS stripe_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            stripe_user_id TEXT UNIQUE NOT NULL,
            stripe_access_token TEXT NOT NULL,
            account_name TEXT,
            sector TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    query(
        """
        CREATE TABLE IF NOT EXISTS users_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_sub TEXT UNIQUE,
            email TEXT,
            name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    existing_users_columns = _column_names("users") if _table_exists("users") else set()
    if "stripe_user_id" in existing_users_columns:
        # Ancien schéma détecté (users.stripe_user_id NOT NULL, table unique) :
        # migre vers users_v2 + stripe_connections en conservant les id pour
        # que les sessions déjà émises (qui encodent user_id) restent valides.
        query("INSERT OR IGNORE INTO users_v2 (id, created_at) SELECT id, created_at FROM users")
        query(
            """
            INSERT OR IGNORE INTO stripe_connections
                (user_id, stripe_user_id, stripe_access_token, account_name, sector)
            SELECT id, stripe_user_id, stripe_access_token, account_name, sector FROM users
            """
        )
        query("DROP TABLE users")

    if not _table_exists("users"):
        query("ALTER TABLE users_v2 RENAME TO users")

    if "picture" not in _column_names("users"):
        # Photo de profil Google (URL), ajoutée après coup : migration
        # simple, une seule colonne nullable, pas de table à réconcilier.
        query("ALTER TABLE users ADD COLUMN picture TEXT")

    ensure_shopify_table()


def _table_exists(name: str) -> bool:
    rows = query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", [name]
    )
    return bool(rows)


_USER_SELECT = """
    SELECT
        users.id AS id,
        users.google_sub AS google_sub,
        users.email AS email,
        users.name AS name,
        users.picture AS picture,
        users.created_at AS created_at,
        stripe_connections.stripe_user_id AS stripe_user_id,
        stripe_connections.stripe_access_token AS stripe_access_token,
        stripe_connections.account_name AS account_name,
        stripe_connections.sector AS sector
    FROM users
    LEFT JOIN stripe_connections ON stripe_connections.user_id = users.id
"""


def get_user(user_id: int) -> dict | None:
    rows = query(_USER_SELECT + " WHERE users.id = ?", [user_id])
    return rows[0] if rows else None


def get_user_by_stripe_id(stripe_user_id: str) -> dict | None:
    """Utilisé par le webhook Stripe : les événements portent l'id du compte
    connecté (`event.account`), pas notre id interne."""
    rows = query(
        _USER_SELECT + " WHERE stripe_connections.stripe_user_id = ?", [stripe_user_id]
    )
    return rows[0] if rows else None


def upsert_google_user(
    google_sub: str, email: str | None, name: str | None, picture: str | None = None
) -> int:
    """Crée ou retrouve l'utilisateur identifié par son compte Google.
    N'implique aucune connexion Stripe — juste une identité/session."""
    query(
        """
        INSERT INTO users (google_sub, email, name, picture)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(google_sub) DO UPDATE SET
            email = excluded.email,
            name = excluded.name,
            picture = excluded.picture
        """,
        [google_sub, email, name, picture],
    )
    rows = query("SELECT id FROM users WHERE google_sub = ?", [google_sub])
    return rows[0]["id"]


def link_stripe_connection(
    user_id: int, stripe_user_id: str, access_token: str, account_name: str | None
) -> None:
    """Relie un compte Stripe à un utilisateur déjà identifié (typiquement
    via Google). Un utilisateur n'a qu'une seule connexion Stripe à la fois,
    et réciproquement un compte Stripe n'est relié qu'à un seul utilisateur
    à la fois — deux contraintes UNIQUE (user_id, stripe_user_id), donc deux
    clauses ON CONFLICT : sans la seconde, reconnecter un compte Stripe déjà
    lié à un ancien utilisateur (ex: un test standalone antérieur) violait
    la contrainte sur stripe_user_id et faisait échouer l'écriture."""
    query(
        """
        INSERT INTO stripe_connections (user_id, stripe_user_id, stripe_access_token, account_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            stripe_user_id = excluded.stripe_user_id,
            stripe_access_token = excluded.stripe_access_token,
            account_name = excluded.account_name
        ON CONFLICT(stripe_user_id) DO UPDATE SET
            user_id = excluded.user_id,
            stripe_access_token = excluded.stripe_access_token,
            account_name = excluded.account_name
        """,
        [user_id, stripe_user_id, access_token, account_name],
    )


def upsert_standalone_stripe_user(
    stripe_user_id: str, access_token: str, account_name: str | None
) -> int:
    """Connexion Stripe sans identité Google préalable (parcours historique :
    le bouton "Se connecter avec Stripe" utilisé seul, sans passer par
    Google). Crée un utilisateur minimal (sans email/nom) puis sa connexion
    Stripe. Si ce stripe_user_id est déjà relié à un utilisateur existant,
    le retrouve au lieu d'en recréer un (reconnexion)."""
    existing = get_user_by_stripe_id(stripe_user_id)
    if existing:
        link_stripe_connection(existing["id"], stripe_user_id, access_token, account_name)
        return existing["id"]

    # google_sub reste factice (jamais un vrai "sub" Google) pour cet
    # utilisateur "Stripe seul" — sert uniquement à réutiliser l'upsert
    # idempotent de upsert_google_user sans dupliquer sa logique.
    user_id = upsert_google_user(f"stripe:{stripe_user_id}", None, account_name)
    link_stripe_connection(user_id, stripe_user_id, access_token, account_name)
    return user_id


def set_user_sector(user_id: int, sector: str) -> None:
    query("UPDATE stripe_connections SET sector = ? WHERE user_id = ?", [sector, user_id])


def ensure_shopify_table():
    """Table séparée (pas de migration à prévoir, contrairement à
    stripe_connections/users) : ajoutée après coup, jamais présente dans un
    ancien schéma."""
    query(
        """
        CREATE TABLE IF NOT EXISTS shopify_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            shop_domain TEXT NOT NULL,
            access_token TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def get_shopify_connection(user_id: int) -> dict | None:
    rows = query(
        "SELECT shop_domain, access_token FROM shopify_connections WHERE user_id = ?", [user_id]
    )
    return rows[0] if rows else None


def link_shopify_connection(user_id: int, shop_domain: str, access_token: str) -> None:
    query(
        """
        INSERT INTO shopify_connections (user_id, shop_domain, access_token)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            shop_domain = excluded.shop_domain,
            access_token = excluded.access_token
        """,
        [user_id, shop_domain, access_token],
    )
