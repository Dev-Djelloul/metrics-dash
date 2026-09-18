"""
Fetches Stripe charges and turns them into the business metrics
shown on the dashboard. Kept deliberately simple/readable since
the goal of this project is to learn how the Stripe API works.
"""
from collections import defaultdict
from datetime import datetime, timezone

import stripe


def fetch_recent_charges(limit_pages: int = 5):
    """Pulls up to `limit_pages` pages (100 each) of succeeded charges."""
    charges = []
    starting_after = None
    for _ in range(limit_pages):
        page = stripe.Charge.list(
            limit=100,
            starting_after=starting_after,
        )
        charges.extend(page.data)
        if not page.has_more:
            break
        starting_after = page.data[-1].id
    return [c for c in charges if c.status == "succeeded" and not c.refunded]


def compute_metrics(charges):
    if not charges:
        return {
            "total_revenue": 0,
            "order_count": 0,
            "avg_order_value": 0,
            "currency": "usd",
            "revenue_by_day": [],
            "top_customers": [],
        }

    currency = charges[0].currency
    total_cents = sum(c.amount for c in charges)
    order_count = len(charges)

    revenue_per_day = defaultdict(int)
    revenue_per_customer = defaultdict(int)

    for c in charges:
        day = datetime.fromtimestamp(c.created, tz=timezone.utc).strftime("%Y-%m-%d")
        revenue_per_day[day] += c.amount

        label = (
            c.billing_details.name
            or c.billing_details.email
            or c.customer
            or "Unknown"
        )
        revenue_per_customer[label] += c.amount

    revenue_by_day = [
        {"date": day, "amount": cents / 100}
        for day, cents in sorted(revenue_per_day.items())
    ]

    top_customers = sorted(
        (
            {"name": name, "amount": cents / 100}
            for name, cents in revenue_per_customer.items()
        ),
        key=lambda x: x["amount"],
        reverse=True,
    )[:5]

    return {
        "total_revenue": total_cents / 100,
        "order_count": order_count,
        "avg_order_value": (total_cents / order_count) / 100,
        "currency": currency,
        "revenue_by_day": revenue_by_day,
        "top_customers": top_customers,
    }


def compute_metrics_from_records(records, currency="eur"):
    """Same output shape as compute_metrics(), but works on the normalized
    record format shared with the analytics module (used by demo mode)."""
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
            }
        )
    return records
