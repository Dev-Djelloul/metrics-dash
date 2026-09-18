"""
Fetches Stripe charges and turns them into the business metrics
shown on the dashboard. Kept deliberately simple/readable since
the goal of this project is to learn how the Stripe API works.
"""
from collections import defaultdict
from datetime import datetime, timezone

import stripe


def fetch_recent_charges(api_key: str, limit_pages: int = 5):
    """Pulls up to `limit_pages` pages (100 each) of succeeded charges.

    `api_key` is passed explicitly (rather than relying on the module-level
    `stripe.api_key`) because this now runs per logged-in user: mutating a
    shared global would race between concurrent requests from different
    users."""
    charges = []
    starting_after = None
    for _ in range(limit_pages):
        page = stripe.Charge.list(
            limit=100,
            starting_after=starting_after,
            api_key=api_key,
        )
        charges.extend(page.data)
        if not page.has_more:
            break
        starting_after = page.data[-1].id
    return [c for c in charges if c.status == "succeeded" and not c.refunded]


def compute_metrics_from_records(records, currency="eur"):
    """Computes the Overview tab's headline metrics from the normalized
    record format shared with the analytics module."""
    if not records:
        return {
            "total_revenue": 0,
            "order_count": 0,
            "avg_order_value": 0,
            "currency": currency,
            "revenue_by_day": [],
            "top_customers": [],
        }

    total = sum(r["amount"] for r in records)
    order_count = len(records)

    revenue_per_day = defaultdict(float)
    revenue_per_customer = defaultdict(float)
    for r in records:
        revenue_per_day[r["created"].strftime("%Y-%m-%d")] += r["amount"]
        revenue_per_customer[r["customer"]] += r["amount"]

    revenue_by_day = [
        {"date": day, "amount": round(amount, 2)}
        for day, amount in sorted(revenue_per_day.items())
    ]
    top_customers = sorted(
        ({"name": name, "amount": round(amount, 2)} for name, amount in revenue_per_customer.items()),
        key=lambda x: x["amount"],
        reverse=True,
    )[:5]

    return {
        "total_revenue": round(total, 2),
        "order_count": order_count,
        "avg_order_value": round(total / order_count, 2),
        "currency": currency,
        "revenue_by_day": revenue_by_day,
        "top_customers": top_customers,
    }


def charges_to_records(charges):
    """
    Normalizes raw Stripe Charge objects into plain dicts so the
    analytics modules (growth, RFM, cohorts, forecast) don't need to
    know anything about the Stripe SDK's object shape.
    """
    records = []
    for c in charges:
        customer_key = (
            c.billing_details.email
            or c.billing_details.name
            or c.customer
            or "unknown"
        )
        # Pays de facturation (ISO 3166-1 alpha-2, ex: "FR"). Absent si le
        # client n'a pas renseigné d'adresse complète à l'achat — pas toujours
        # garanti selon comment le checkout Stripe est configuré.
        country = None
        if c.billing_details.address:
            country = c.billing_details.address.country
        records.append(
            {
                "customer": customer_key,
                "amount": c.amount / 100,
                "created": datetime.fromtimestamp(c.created, tz=timezone.utc),
                "country": country,
                "currency": c.currency,
            }
        )
    return records
