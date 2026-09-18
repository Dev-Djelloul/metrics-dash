import csv
import io
import os
import time
from urllib.parse import urlencode

import stripe
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import analytics
import auth
import db
import demo_data
from stripe_metrics import charges_to_records, compute_metrics_from_records, fetch_recent_charges

load_dotenv()

# Cette clé identifie *ta plateforme* auprès de Stripe (nécessaire pour
# échanger le code OAuth contre le jeton d'un utilisateur). Elle ne sert
# plus à lire les charges de qui que ce soit directement — chaque
# utilisateur a désormais son propre jeton, obtenu via Stripe Connect.
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_CONNECT_CLIENT_ID = os.getenv("STRIPE_CONNECT_CLIENT_ID")
SESSION_SECRET = os.getenv("SESSION_SECRET")

app = FastAPI(title="metrics-dash")

_cache: dict[int, dict] = {}  # user_id -> {"charges": [...], "fetched_at": float}
CACHE_TTL_SECONDS = 60


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
        return db.get_user(user_id)
    except Exception:
        return None


@app.get("/api/status")
def status(user: dict | None = Depends(get_current_user)):
    if user:
        return {"connected": True, "account": user.get("account_name") or user.get("stripe_user_id")}
    return {
        "connected": False,
        "reason": "Non connecté",
        "connect_configured": bool(STRIPE_CONNECT_CLIENT_ID),
    }


@app.get("/auth/login")
def auth_login(request: Request):
    if not STRIPE_CONNECT_CLIENT_ID:
        raise HTTPException(500, "STRIPE_CONNECT_CLIENT_ID non configuré côté serveur")

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


@app.get("/auth/callback", name="auth_callback")
def auth_callback(code: str = None, error: str = None):
    if error:
        return RedirectResponse(f"/?auth_error={error}")
    if not code:
        raise HTTPException(400, "Code OAuth manquant")
    if not SESSION_SECRET:
        raise HTTPException(500, "SESSION_SECRET non configuré côté serveur")

    try:
        token_response = stripe.OAuth.token(grant_type="authorization_code", code=code)
    except stripe.oauth_error.OAuthError:
        return RedirectResponse("/?auth_error=oauth_failed")
    except Exception as exc:
        return Response(
            f"Erreur lors de l'échange du code OAuth :\n\n{type(exc).__name__}: {exc}",
            status_code=500,
            media_type="text/plain",
        )

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
        user_id = db.upsert_user(stripe_user_id, access_token, account_name)
    except Exception as exc:
        # Affiche l'erreur directement dans le navigateur plutôt qu'un 500
        # générique : les logs du conteneur (stdout Python) ne remontent
        # pas dans `wrangler tail`, qui ne couvre que le Worker routeur.
        return Response(
            f"Erreur lors de l'écriture en base D1 :\n\n{type(exc).__name__}: {exc}",
            status_code=500,
            media_type="text/plain",
        )

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


@app.get("/auth/logout")
def auth_logout():
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response


def get_charges_for_user(user: dict):
    """Fetches charges for one logged-in user, cached briefly per user to
    avoid hammering Stripe every time that user reloads a tab."""
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


def get_records(demo: bool, start: str, end: str, user: dict | None):
    """Fetches and normalizes records, then applies the date range picker's
    filter (start/end, both optional "YYYY-MM-DD") if set."""
    if demo:
        records, currency = demo_data.generate_demo_records(), "eur"
    elif user:
        charges = get_charges_for_user(user)
        currency = charges[0].currency if charges else "eur"
        records = charges_to_records(charges)
    else:
        raise HTTPException(401, "Non connecté — connecte-toi avec Stripe ou utilise le mode démo.")

    records = analytics.filter_by_date(records, start, end)
    return records, currency


@app.get("/api/metrics")
def metrics(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, currency = get_records(demo, start, end, user)
    return compute_metrics_from_records(records, currency)


@app.get("/api/growth")
def growth(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.growth_metrics(records)


@app.get("/api/rfm")
def rfm(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.rfm_segments(records)


@app.get("/api/cohorts")
def cohorts(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.cohort_retention(records)


@app.get("/api/forecast")
def forecast(
    periods: int = 3, demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)
):
    records, _ = get_records(demo, start, end, user)
    return analytics.forecast_revenue(records, periods_ahead=periods)


@app.get("/api/weekday")
def weekday(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.revenue_by_weekday(records)


@app.get("/api/concentration")
def concentration(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.revenue_concentration(records)


@app.get("/api/newvsreturning")
def new_vs_returning(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.new_vs_returning_by_month(records)


@app.get("/api/loyalty")
def loyalty(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.loyalty_metrics(records)


@app.get("/api/geo")
def geo(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return analytics.revenue_by_country(records)


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
def export_monthly_csv(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return _csv_response(analytics.growth_metrics(records)["monthly"], "metrics-dash-monthly.csv")


@app.get("/api/export/daily.csv")
def export_daily_csv(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return _csv_response(analytics.growth_metrics(records)["daily"], "metrics-dash-daily.csv")


@app.get("/api/export/rfm.csv")
def export_rfm_csv(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    return _csv_response(analytics.rfm_segments(records), "metrics-dash-rfm.csv")


@app.get("/api/export/cohorts.csv")
def export_cohorts_csv(demo: bool = False, start: str = None, end: str = None, user=Depends(get_current_user)):
    records, _ = get_records(demo, start, end, user)
    rows = [
        {
            "cohort_month": cohort["cohort_month"],
            "cohort_size": cohort["cohort_size"],
            "month_offset": r["month_offset"],
            "active": r["active"],
            "pct": r["pct"],
        }
        for cohort in analytics.cohort_retention(records)
        for r in cohort["retention"]
    ]
    return _csv_response(rows, "metrics-dash-cohorts.csv")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
