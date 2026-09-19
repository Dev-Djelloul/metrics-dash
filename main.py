import csv
import hashlib
import hmac as hmac_lib
import io
import os
import re
import threading
import time
from urllib.parse import urlencode

import httpx
import stripe
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import analytics
import auth
import db
import demo_data
from shopify_metrics import (
    fetch_canonical_shop_domain,
    fetch_orders as fetch_shopify_orders,
    orders_to_records,
)
from stripe_metrics import (
    charges_to_records,
    compute_metrics_from_records,
    compute_mrr,
    fetch_active_subscriptions,
    fetch_recent_charges,
)

load_dotenv()

# Force un rebuild de l'image Docker (donc un vrai redémarrage du conteneur)
# après une rotation de STRIPE_SECRET_KEY : un `wrangler secret put` seul ne
# relit pas la variable dans un conteneur déjà démarré — même leçon que pour
# les secrets D1 rencontrée plus tôt dans ce projet. (rebuild #2, suite à la
# découverte du vrai compte Stripe plateforme)
# Cette clé identifie *ta plateforme* auprès de Stripe (nécessaire pour
# échanger le code OAuth contre le jeton d'un utilisateur). Elle ne sert
# plus à lire les charges de qui que ce soit directement — chaque
# utilisateur a désormais son propre jeton, obtenu via Stripe Connect.
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_CONNECT_CLIENT_ID = os.getenv("STRIPE_CONNECT_CLIENT_ID")
SESSION_SECRET = os.getenv("SESSION_SECRET")
# Secret du endpoint webhook (distinct de la clé API) : Stripe le donne
# quand on crée le endpoint dans le dashboard Connect, sert uniquement à
# vérifier que les événements reçus viennent bien de Stripe.
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Connexion Google : sert uniquement à identifier la personne (qui es-tu ?),
# séparément de la connexion Stripe (à quelles données as-tu accès ?) — les
# deux étaient confondues dans un seul bouton avant l'ajout de Google.
# (Changement trivial pour forcer un rebuild du container et lui faire
# relire GOOGLE_CLIENT_ID/SECRET désormais relayés par src/index.ts.)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# Connexion Shopify : deuxième source de ventes possible en plus de Stripe
# (une boutique en ligne, avec ses propres commandes). Les commandes Shopify
# rejoignent les charges Stripe dans get_all_records() — même client final,
# deux canaux de vente, un seul dashboard.
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")
SHOPIFY_API_SECRET = os.getenv("SHOPIFY_API_SECRET")

app = FastAPI(title="metrics-dash")

_cache: dict[int, dict] = {}  # user_id -> {"charges": [...], "fetched_at": float}
_shopify_cache: dict[int, dict] = {}  # user_id -> {"orders": [...], "fetched_at": float}
CACHE_TTL_SECONDS = 60

# Secteurs proposés à la connexion : Stripe ne fournit aucune info
# d'activité par transaction (une charge = montant/client/date, rien de
# plus), donc on la demande une fois, déclarativement, plutôt que de
# dépendre du MCC du compte Stripe (souvent absent ou peu fiable).
SECTORS = [
    "E-commerce / vente au détail",
    "SaaS / logiciel",
    "Conseil / services professionnels",
    "Formation / éducation",
    "Restauration / hôtellerie",
    "Santé / bien-être",
    "Média / création de contenu",
    "Association / non lucratif",
    "Autre",
]

# Repères sectoriels indicatifs (ordres de grandeur généralement admis pour
# ces types d'activité), PAS calculés depuis les données des utilisateurs
# de metrics-dash — trop peu nombreux pour une moyenne significative. Servent
# de point de comparaison approximatif, pas une vérité statistique.
SECTOR_BENCHMARKS = {
    "E-commerce / vente au détail": {"repeat_customer_pct": 30, "avg_days_between_purchases": 45, "top_share_pct": 65},
    "SaaS / logiciel": {"repeat_customer_pct": 75, "avg_days_between_purchases": 30, "top_share_pct": 70},
    "Conseil / services professionnels": {"repeat_customer_pct": 40, "avg_days_between_purchases": 60, "top_share_pct": 75},
    "Formation / éducation": {"repeat_customer_pct": 25, "avg_days_between_purchases": 90, "top_share_pct": 60},
    "Restauration / hôtellerie": {"repeat_customer_pct": 35, "avg_days_between_purchases": 20, "top_share_pct": 55},
    "Santé / bien-être": {"repeat_customer_pct": 45, "avg_days_between_purchases": 35, "top_share_pct": 60},
    "Média / création de contenu": {"repeat_customer_pct": 50, "avg_days_between_purchases": 30, "top_share_pct": 65},
    "Association / non lucratif": {"repeat_customer_pct": 20, "avg_days_between_purchases": 120, "top_share_pct": 70},
    "Autre": {"repeat_customer_pct": 35, "avg_days_between_purchases": 45, "top_share_pct": 65},
}


@app.on_event("startup")
def on_startup():
    try:
        db.ensure_schema()
    except Exception as exc:
        # D1 non configuré (ex: dev local sans les secrets Cloudflare) :
        # le mode démo doit rester utilisable, donc on ne bloque pas le
        # démarrage — la connexion Stripe Connect sera juste indisponible.
        print(f"[metrics-dash] D1 indisponible au démarrage ({exc}) — mode démo uniquement.")


def get_current_user(request: Request) -> dict | None:
    if not SESSION_SECRET:
        return None
    token = request.cookies.get("session")
    if not token:
        return None
    user_id = auth.verify_session_token(token, SESSION_SECRET)
    if user_id is None:
        return None
    try:
        user = db.get_user(user_id)
    except Exception:
        return None
    if user is None:
        return None
    try:
        shopify = db.get_shopify_connection(user_id)
    except Exception:
        shopify = None
    if shopify:
        user["shopify_shop_domain"] = shopify["shop_domain"]
        user["shopify_access_token"] = shopify["access_token"]
    return user


@app.get("/api/status")
def status(user: dict | None = Depends(get_current_user)):
    if not user:
        return {
            "authenticated": False,
            "connected": False,
            "reason": "Non connecté",
            "connect_configured": bool(STRIPE_CONNECT_CLIENT_ID),
            "google_configured": bool(GOOGLE_CLIENT_ID),
            "shopify_configured": bool(SHOPIFY_API_KEY),
        }
    stripe_connected = bool(user.get("stripe_user_id"))
    shopify_connected = bool(user.get("shopify_access_token"))
    return {
        "authenticated": True,
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
        "member_since": user.get("created_at"),
        "connected": stripe_connected or shopify_connected,
        "stripe_connected": stripe_connected,
        "account": user.get("account_name") or user.get("stripe_user_id"),
        "shopify_connected": shopify_connected,
        "shop_domain": user.get("shopify_shop_domain"),
        "sector": user.get("sector"),
        "connect_configured": bool(STRIPE_CONNECT_CLIENT_ID),
        "shopify_configured": bool(SHOPIFY_API_KEY),
    }


@app.get("/api/sectors")
def sectors():
    return {"sectors": SECTORS}


@app.get("/api/benchmarks")
def benchmarks(sector: str = None):
    return SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["Autre"])


@app.post("/api/sector")
def set_sector(payload: dict = Body(...), user=Depends(get_current_user)):
    sector = (payload or {}).get("sector", "").strip()
    if not sector or sector not in SECTORS:
        raise HTTPException(400, "Secteur invalide")
    if not user:
        raise HTTPException(401, "Non connecté")
    db.set_user_sector(user["id"], sector)
    return {"ok": True, "sector": sector}


@app.get("/auth/login")
def auth_login(request: Request):
    if not STRIPE_CONNECT_CLIENT_ID:
        raise HTTPException(500, "STRIPE_CONNECT_CLIENT_ID non configuré côté serveur")
    if not get_current_user(request):
        # Google est désormais le préalable obligatoire à toute connexion de
        # données (Stripe/Shopify/Power BI) — plus de création d'identité
        # "standalone" à la volée comme avant son ajout.
        return RedirectResponse("/?auth_error=login_required_before_stripe")

    # Cloudflare termine le HTTPS à la périphérie et transmet au conteneur
    # en clair : request.url_for() voit donc un schéma "http" et générerait
    # une redirect_uri qui ne correspond pas à celle enregistrée sur Stripe
    # (toujours en https). On force le schéma plutôt que de le déduire de
    # la requête interne.
    redirect_uri = str(request.url_for("auth_callback")).replace("http://", "https://", 1)

    # Stripe réserve le scope "read_only" aux comptes plateforme activés
    # spécifiquement pour ça (sur demande à leur support) — indisponible
    # par défaut. "read_write" est donc la seule option immédiatement
    # utilisable ; le code de l'app n'appelle que des endpoints Stripe en
    # lecture (Charge.list, Account.retrieve), jamais d'écriture.
    params = urlencode(
        {
            "response_type": "code",
            "client_id": STRIPE_CONNECT_CLIENT_ID,
            "scope": "read_write",
            "redirect_uri": redirect_uri,
        }
    )
    return RedirectResponse(f"https://connect.stripe.com/oauth/authorize?{params}")


# Un code OAuth Stripe est à usage unique. On a observé dans les logs que
# certaines requêtes vers /auth/callback arrivent en double (même code, même
# seconde) — vraisemblablement un comportement de l'infrastructure Cloudflare
# (retry/duplication au niveau Worker/Container), pas du navigateur (le bug
# persiste identique sur Chrome et Firefox, en navigation privée). La
# deuxième requête se fait alors rejeter par Stripe ("code ne vous
# appartient pas") même si la première a réussi. On mémorise donc le
# résultat par code pour que la deuxième requête réutilise la session déjà
# créée au lieu de renvoyer le code (déjà consommé) à Stripe.
_oauth_code_results: dict[str, int] = {}  # code -> user_id
_oauth_code_lock = threading.Lock()


def _session_redirect(user_id: int) -> RedirectResponse:
    session_token = auth.create_session_token(user_id, SESSION_SECRET)
    response = RedirectResponse("/")
    response.set_cookie(
        "session",
        session_token,
        max_age=auth.SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.get("/auth/callback", name="auth_callback")
def auth_callback(request: Request, code: str = None, error: str = None):
    if error:
        return RedirectResponse(f"/?auth_error={error}")
    if not code:
        raise HTTPException(400, "Code OAuth manquant")
    if not SESSION_SECRET:
        raise HTTPException(500, "SESSION_SECRET non configuré côté serveur")

    # Session déjà ouverte (typiquement via Google) : ce callback vient alors
    # relier Stripe à cette identité existante, pas en créer une nouvelle.
    # Sans session, on reste sur le parcours historique "Stripe seul".
    current_user = get_current_user(request)

    # Verrou global (pas seulement une vérification de cache) : deux requêtes
    # avec le même code peuvent arriver quasi simultanément (voir commentaire
    # ci-dessus), donc une simple lecture du cache avant d'appeler Stripe ne
    # suffit pas à empêcher la deuxième de partir en parallèle. Le volume de
    # connexions est trop faible pour que sérialiser tout /auth/callback pose
    # un problème de performance.
    with _oauth_code_lock:
        if code in _oauth_code_results:
            return _session_redirect(_oauth_code_results[code])

        try:
            token_response = stripe.OAuth.token(grant_type="authorization_code", code=code)
        except Exception as exc:
            print(f"[metrics-dash] Échec de l'échange du code OAuth : {type(exc).__name__}: {exc}")
            return RedirectResponse("/?auth_error=oauth_failed")

        stripe_user_id = token_response["stripe_user_id"]
        access_token = token_response["access_token"]

        try:
            account = stripe.Account.retrieve(api_key=access_token)
            account_name = (
                (account.get("business_profile") or {}).get("name")
                or account.get("email")
                or stripe_user_id
            )
        except Exception:
            account_name = stripe_user_id

        try:
            if current_user:
                db.link_stripe_connection(current_user["id"], stripe_user_id, access_token, account_name)
                user_id = current_user["id"]
            else:
                user_id = db.upsert_standalone_stripe_user(stripe_user_id, access_token, account_name)
        except Exception as exc:
            print(f"[metrics-dash] Échec de l'écriture en base D1 : {type(exc).__name__}: {exc}")
            return RedirectResponse("/?auth_error=db_failed")

        _oauth_code_results[code] = user_id

    return _session_redirect(user_id)


@app.get("/auth/google/login")
def auth_google_login(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "GOOGLE_CLIENT_ID non configuré côté serveur")

    redirect_uri = str(request.url_for("auth_google_callback")).replace("http://", "https://", 1)
    params = urlencode(
        {
            "response_type": "code",
            "client_id": GOOGLE_CLIENT_ID,
            "scope": "openid email profile",
            "redirect_uri": redirect_uri,
            # Sans ça, un compte déjà "souvenu" par le navigateur saute
            # l'écran de sélection — gênant pour tester avec plusieurs comptes.
            "prompt": "select_account",
        }
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@app.get("/auth/google/callback", name="auth_google_callback")
def auth_google_callback(request: Request, code: str = None, error: str = None):
    if error:
        return RedirectResponse(f"/?auth_error={error}")
    if not code:
        raise HTTPException(400, "Code OAuth manquant")
    if not SESSION_SECRET:
        raise HTTPException(500, "SESSION_SECRET non configuré côté serveur")

    redirect_uri = str(request.url_for("auth_google_callback")).replace("http://", "https://", 1)
    try:
        token_response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        userinfo = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        userinfo.raise_for_status()
        profile = userinfo.json()
    except Exception as exc:
        print(f"[metrics-dash] Échec de l'échange du code Google : {type(exc).__name__}: {exc}")
        return RedirectResponse("/?auth_error=oauth_failed")

    try:
        user_id = db.upsert_google_user(
            profile["sub"], profile.get("email"), profile.get("name"), profile.get("picture")
        )
    except Exception as exc:
        print(f"[metrics-dash] Échec de l'écriture en base D1 : {type(exc).__name__}: {exc}")
        return RedirectResponse("/?auth_error=db_failed")

    return _session_redirect(user_id)


_SHOP_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")


@app.get("/auth/shopify/login")
def auth_shopify_login(request: Request, shop: str = None):
    if not SHOPIFY_API_KEY:
        raise HTTPException(500, "SHOPIFY_API_KEY non configuré côté serveur")
    if not get_current_user(request):
        return RedirectResponse("/?auth_error=login_required_before_shopify")
    if not shop or not _SHOP_DOMAIN_RE.match(shop):
        raise HTTPException(400, "Domaine de boutique invalide (attendu : xxx.myshopify.com)")

    redirect_uri = str(request.url_for("auth_shopify_callback")).replace("http://", "https://", 1)
    params = urlencode(
        {
            "client_id": SHOPIFY_API_KEY,
            # read_orders suffit : on ne fait que lire les commandes pour
            # les métriques, jamais les modifier.
            "scope": "read_orders",
            "redirect_uri": redirect_uri,
        }
    )
    return RedirectResponse(f"https://{shop}/admin/oauth/authorize?{params}")


@app.get("/auth/shopify/callback", name="auth_shopify_callback")
def auth_shopify_callback(request: Request, shop: str = None, code: str = None, hmac: str = None):
    current_user = get_current_user(request)
    if not current_user:
        return RedirectResponse("/?auth_error=login_required_before_shopify")
    if not code or not shop or not _SHOP_DOMAIN_RE.match(shop):
        raise HTTPException(400, "Paramètres Shopify manquants ou invalides")

    # Vérifie que la requête vient bien de Shopify : signature HMAC calculée
    # sur les query params (hors hmac lui-même) avec le secret de l'app,
    # même principe que la signature webhook Stripe.
    params = dict(request.query_params)
    params.pop("hmac", None)
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    expected_hmac = hmac_lib.new(
        SHOPIFY_API_SECRET.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac or not hmac_lib.compare_digest(expected_hmac, hmac):
        raise HTTPException(400, "Signature Shopify invalide")

    try:
        token_response = httpx.post(
            f"https://{shop}/admin/oauth/access_token",
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "code": code,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
    except Exception as exc:
        print(f"[metrics-dash] Échec de l'échange du code Shopify : {type(exc).__name__}: {exc}")
        return RedirectResponse("/?auth_error=oauth_failed")

    # Le `shop` de l'URL peut être un domaine périmé (boutique renommée depuis) :
    # Shopify continue d'accepter l'autorisation OAuth dessus, mais on veut
    # stocker et utiliser le domaine canonique pour les appels API et l'affichage.
    canonical_domain = fetch_canonical_shop_domain(shop, access_token)

    try:
        db.link_shopify_connection(current_user["id"], canonical_domain, access_token)
    except Exception as exc:
        print(f"[metrics-dash] Échec de l'écriture en base D1 : {type(exc).__name__}: {exc}")
        return RedirectResponse("/?auth_error=db_failed")

    _shopify_cache.pop(current_user["id"], None)
    return RedirectResponse("/")


@app.get("/auth/logout")
def auth_logout():
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response


@app.get("/auth/stripe/disconnect")
def auth_stripe_disconnect(request: Request, user: dict | None = Depends(get_current_user)):
    """Déconnecte Stripe seul, en gardant l'identité Google et Shopify
    intactes — utile pour retester une connexion sans tout recommencer."""
    if user:
        db.unlink_stripe_connection(user["id"])
        _cache.pop(user["id"], None)
    return RedirectResponse("/")


@app.get("/auth/shopify/disconnect")
def auth_shopify_disconnect(request: Request, user: dict | None = Depends(get_current_user)):
    if user:
        db.unlink_shopify_connection(user["id"])
        _shopify_cache.pop(user["id"], None)
    return RedirectResponse("/")


def get_charges_for_user(user: dict):
    """Fetches charges for one logged-in user, cached briefly per user to
    avoid hammering Stripe every time that user reloads a tab. Le webhook
    Stripe invalide ce cache dès qu'un paiement arrive, donc ce TTL n'est
    plus qu'un filet de sécurité (si le webhook n'est pas configuré ou rate
    un événement), pas le seul mécanisme de fraîcheur."""
    now = time.time()
    cached = _cache.get(user["id"])
    if cached is not None and now - cached["fetched_at"] < CACHE_TTL_SECONDS:
        return cached["charges"]

    try:
        charges = fetch_recent_charges(api_key=user["stripe_access_token"])
    except stripe.error.AuthenticationError:
        raise HTTPException(401, "Jeton Stripe invalide ou révoqué — reconnecte-toi.")

    _cache[user["id"]] = {"charges": charges, "fetched_at": now}
    return charges


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Reçoit les événements Stripe Connect (un paiement chez un utilisateur
    connecté) pour invalider son cache immédiatement, plutôt que d'attendre
    jusqu'à CACHE_TTL_SECONDS — les métriques suivent le paiement en quasi
    temps réel au lieu du polling toutes les 60s."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(500, "STRIPE_WEBHOOK_SECRET non configuré côté serveur")

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Signature webhook invalide")

    # Les événements Stripe Connect portent l'id du compte connecté dans
    # `event.account` (absent des événements sur le compte plateforme lui-même).
    stripe_account_id = event.get("account")
    if stripe_account_id:
        try:
            user = db.get_user_by_stripe_id(stripe_account_id)
        except Exception:
            user = None
        if user:
            _cache.pop(user["id"], None)

    return {"received": True}


def get_shopify_orders_for_user(user: dict) -> list[dict]:
    """Même logique de cache que get_charges_for_user, côté Shopify."""
    now = time.time()
    cached = _shopify_cache.get(user["id"])
    if cached is not None and now - cached["fetched_at"] < CACHE_TTL_SECONDS:
        return cached["orders"]

    orders = fetch_shopify_orders(
        shop_domain=user["shopify_shop_domain"], access_token=user["shopify_access_token"]
    )
    _shopify_cache[user["id"]] = {"orders": orders, "fetched_at": now}
    return orders


def get_all_records(demo: bool, user: dict | None) -> list[dict]:
    """Fetches and normalizes every record for this user/demo, toutes devises
    confondues, sans filtre de date — utilisé pour lister les devises
    disponibles et comme base avant filtrage par get_records()."""
    if demo:
        return demo_data.generate_demo_records()
    if user:
        records = []
        # Chaque source est indépendante : si l'une échoue (jeton révoqué,
        # boutique injoignable...), ça ne doit jamais faire planter les
        # autres — avant, une seule source en erreur cassait tout /api/metrics,
        # y compris les données de la source qui fonctionnait.
        if user.get("stripe_access_token"):
            try:
                records.extend(charges_to_records(get_charges_for_user(user)))
            except Exception as exc:
                print(f"[metrics-dash] Échec de récupération Stripe : {type(exc).__name__}: {exc}")
        if user.get("shopify_access_token"):
            # Deux canaux de vente pour le même business : Stripe (paiements
            # directs) et Shopify (boutique en ligne) s'additionnent dans le
            # même chiffre d'affaires plutôt que de s'exclure.
            try:
                records.extend(orders_to_records(get_shopify_orders_for_user(user)))
            except Exception as exc:
                print(f"[metrics-dash] Échec de récupération Shopify : {type(exc).__name__}: {exc}")
        if records:
            return records
    raise HTTPException(401, "Non connecté — connecte-toi avec Stripe/Shopify ou utilise le mode démo.")


def get_records(demo: bool, start: str, end: str, user: dict | None, currency: str = None):
    """Fetches and normalizes records, then filters by currency and by the
    date range picker's filter (start/end, both optional "YYYY-MM-DD").

    Un compte peut encaisser dans plusieurs devises (ex: EUR et USD) — les
    additionner reviendrait à sommer des unités différentes comme si
    c'était la même, donc on isole toujours une seule devise à la fois
    plutôt que de les mélanger ou de les convertir (pas de taux de change
    fiable disponible ici)."""
    records = get_all_records(demo, user)

    available = sorted({r.get("currency", "eur") for r in records}) or ["eur"]
    selected = currency if currency in available else available[0]

    records = [r for r in records if r.get("currency", "eur") == selected]
    records = analytics.filter_by_date(records, start, end)
    return records, selected


@app.get("/api/currencies")
def currencies(demo: bool = False, user=Depends(get_current_user)):
    records = get_all_records(demo, user)
    return {"currencies": sorted({r.get("currency", "eur") for r in records}) or ["eur"]}


@app.get("/api/metrics")
def metrics(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, currency = get_records(demo, start, end, user, currency)
    return compute_metrics_from_records(records, currency)


@app.get("/api/growth")
def growth(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.growth_metrics(records)


@app.get("/api/rfm")
def rfm(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.rfm_segments(records)


@app.get("/api/cohorts")
def cohorts(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.cohort_retention(records)


@app.get("/api/forecast")
def forecast(
    periods: int = 3, demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)
):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.forecast_revenue(records, periods_ahead=periods)


@app.get("/api/forecast/orders")
def forecast_orders(
    periods: int = 3, demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)
):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.forecast_order_count(records, periods_ahead=periods)


@app.get("/api/forecast/newcustomers")
def forecast_new_customers(
    periods: int = 3, demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)
):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.forecast_new_customers(records, periods_ahead=periods)


@app.get("/api/weekday")
def weekday(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.revenue_by_weekday(records)


@app.get("/api/concentration")
def concentration(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.revenue_concentration(records)


@app.get("/api/newvsreturning")
def new_vs_returning(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.new_vs_returning_by_month(records)


@app.get("/api/loyalty")
def loyalty(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.loyalty_metrics(records)


@app.get("/api/alerts")
def alerts(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    """Alertes simples calculées à la volée (pas de notif email — nécessiterait
    un service d'envoi qu'on n'a pas encore) : un déclin marqué du CA d'un
    mois sur l'autre, ou un nombre significatif de clients à risque/perdus."""
    records, _ = get_records(demo, start, end, user, currency)
    result = []

    monthly = analytics.growth_metrics(records)["monthly"]
    if monthly:
        last = monthly[-1]
        if last["mom_growth_pct"] is not None and last["mom_growth_pct"] <= -20:
            result.append(
                {
                    "type": "revenue_drop",
                    "severity": "warning",
                    "month": last["month"],
                    "pct": abs(last["mom_growth_pct"]),
                    "message": f"CA de {last['month']} en baisse de {abs(last['mom_growth_pct'])}% vs le mois précédent.",
                }
            )

    rfm_rows = analytics.rfm_segments(records)
    at_risk = [r for r in rfm_rows if "risque" in r["segment"] or "Perdus" in r["segment"]]
    if rfm_rows and len(at_risk) / len(rfm_rows) >= 0.3:
        pct = round(len(at_risk) / len(rfm_rows) * 100)
        result.append(
            {
                "type": "churn_risk",
                "severity": "warning",
                "pct": pct,
                "at_risk_count": len(at_risk),
                "total": len(rfm_rows),
                "message": f"{pct}% des clients sont à risque ou perdus ({len(at_risk)}/{len(rfm_rows)}).",
            }
        )

    return {"alerts": result}


@app.get("/api/geo")
def geo(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return analytics.revenue_by_country(records)


@app.get("/api/mrr")
def mrr(demo: bool = False, user=Depends(get_current_user)):
    """MRR (Monthly Recurring Revenue) : n'a de sens que pour de vrais
    abonnements Stripe (objets Subscription), pas pour les paiements
    ponctuels analysés partout ailleurs dans l'app. Le mode démo simule
    des achats ponctuels, donc pas d'abonnements à agréger ici."""
    if demo:
        return {"available": False, "reason": "demo", "by_currency": []}
    if not user:
        raise HTTPException(401, "Non connecté — connecte-toi avec Stripe ou utilise le mode démo.")
    if not user.get("stripe_access_token"):
        # Important : ne JAMAIS passer api_key=None à l'API Stripe — la
        # librairie retombe silencieusement sur la clé globale du compte
        # plateforme (stripe.api_key), ce qui renverrait les abonnements du
        # compte plateforme lui-même plutôt que rien.
        return {"available": False, "reason": "stripe_not_connected", "by_currency": []}

    try:
        subscriptions = fetch_active_subscriptions(api_key=user["stripe_access_token"])
    except stripe.error.AuthenticationError:
        raise HTTPException(401, "Jeton Stripe invalide ou révoqué — reconnecte-toi.")

    if not subscriptions:
        return {"available": False, "reason": "no_subscriptions", "by_currency": []}

    return {"available": True, "by_currency": compute_mrr(subscriptions)}


def _csv_response(rows: list[dict], filename: str) -> Response:
    """Turns a list of flat dicts into a downloadable CSV, the format
    Power BI's "Get Data > Web" connector reads with zero extra setup
    (unlike nested JSON, which needs a bit of M code to flatten)."""
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/monthly.csv")
def export_monthly_csv(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return _csv_response(analytics.growth_metrics(records)["monthly"], "metrics-dash-monthly.csv")


@app.get("/api/export/daily.csv")
def export_daily_csv(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return _csv_response(analytics.growth_metrics(records)["daily"], "metrics-dash-daily.csv")


@app.get("/api/export/rfm.csv")
def export_rfm_csv(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    return _csv_response(analytics.rfm_segments(records), "metrics-dash-rfm.csv")


@app.get("/api/export/cohorts.csv")
def export_cohorts_csv(demo: bool = False, start: str = None, end: str = None, currency: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user, currency)
    rows = [
        {
            "cohort_month": cohort["cohort_month"],
            "cohort_size": cohort["cohort_size"],
            "month_offset": r["month_offset"],
            "active": r["active"],
            "pct": r["pct"],
            "revenue": r["revenue"],
        }
        for cohort in analytics.cohort_retention(records)
        for r in cohort["retention"]
    ]
    return _csv_response(rows, "metrics-dash-cohorts.csv")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
