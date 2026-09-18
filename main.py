import csv
import io
import os
import time

import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import analytics
import demo_data
from stripe_metrics import (
    charges_to_records,
    compute_metrics,
    compute_metrics_from_records,
    fetch_recent_charges,
)

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

app = FastAPI(title="metrics-dash")

_cache = {"charges": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 60


def get_charges():
    """Fetches charges from Stripe, cached briefly to avoid hammering the API
    every time the dashboard reloads a tab."""
    if not stripe.api_key:
        raise HTTPException(400, "STRIPE_SECRET_KEY not set in .env")

    now = time.time()
    if _cache["charges"] is not None and now - _cache["fetched_at"] < CACHE_TTL_SECONDS:
        return _cache["charges"]

    try:
        charges = fetch_recent_charges()
    except stripe.error.AuthenticationError:
        raise HTTPException(401, "Invalid Stripe API key")

    _cache["charges"] = charges
    _cache["fetched_at"] = now
    return charges


@app.get("/api/status")
def status():
    if not stripe.api_key:
        return {"connected": False, "reason": "STRIPE_SECRET_KEY not set in .env"}
    try:
        account = stripe.Account.retrieve()
        return {"connected": True, "account": account.get("id")}
    except stripe.error.AuthenticationError:
        return {"connected": False, "reason": "Invalid Stripe API key"}


def get_records(demo: bool = False):
    if demo:
        return demo_data.generate_demo_records(), "eur"
    charges = get_charges()
    currency = charges[0].currency if charges else "eur"
    return charges_to_records(charges), currency


@app.get("/api/metrics")
def metrics(demo: bool = False):
    if demo:
        records, currency = get_records(demo=True)
        return compute_metrics_from_records(records, currency)
    return compute_metrics(get_charges())


@app.get("/api/growth")
def growth(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.growth_metrics(records)


@app.get("/api/rfm")
def rfm(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.rfm_segments(records)


@app.get("/api/cohorts")
def cohorts(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.cohort_retention(records)


@app.get("/api/forecast")
def forecast(periods: int = 3, demo: bool = False):
    records, _ = get_records(demo)
    return analytics.forecast_revenue(records, periods_ahead=periods)


@app.get("/api/weekday")
def weekday(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.revenue_by_weekday(records)


@app.get("/api/concentration")
def concentration(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.revenue_concentration(records)


@app.get("/api/newvsreturning")
def new_vs_returning(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.new_vs_returning_by_month(records)


@app.get("/api/loyalty")
def loyalty(demo: bool = False):
    records, _ = get_records(demo)
    return analytics.loyalty_metrics(records)


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
def export_monthly_csv(demo: bool = False):
    records, _ = get_records(demo)
    return _csv_response(analytics.growth_metrics(records)["monthly"], "metrics-dash-monthly.csv")


@app.get("/api/export/daily.csv")
def export_daily_csv(demo: bool = False):
    records, _ = get_records(demo)
    return _csv_response(analytics.growth_metrics(records)["daily"], "metrics-dash-daily.csv")


@app.get("/api/export/rfm.csv")
def export_rfm_csv(demo: bool = False):
    records, _ = get_records(demo)
    return _csv_response(analytics.rfm_segments(records), "metrics-dash-rfm.csv")


@app.get("/api/export/cohorts.csv")
def export_cohorts_csv(demo: bool = False):
    records, _ = get_records(demo)
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
