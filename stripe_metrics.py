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
    orders_per_customer = defaultdict(int)
    last_purchase_per_customer = {}
    for r in records:
        revenue_per_day[r["created"].strftime("%Y-%m-%d")] += r["amount"]
        revenue_per_customer[r["customer"]] += r["amount"]
        orders_per_customer[r["customer"]] += 1
        prev = last_purchase_per_customer.get(r["customer"])
        if prev is None or r["created"] > prev:
            last_purchase_per_customer[r["customer"]] = r["created"]

    revenue_by_day = [
        {"date": day, "amount": round(amount, 2)}
        for day, amount in sorted(revenue_per_day.items())
    ]
    # order_count/last_purchase/share_pct viennent des mêmes records déjà
    # normalisés (Stripe + Shopify + démo) que le montant — pas d'appel API
    # supplémentaire, ça alimente la carte de détail au survol du tableau.
    top_customers = sorted(
        (
            {
                "name": name,
                "amount": round(amount, 2),
                "order_count": orders_per_customer[name],
                "last_purchase": last_purchase_per_customer[name].strftime("%Y-%m-%d"),
                "share_pct": round((amount / total) * 100, 1) if total else 0,
            }
            for name, amount in revenue_per_customer.items()
        ),
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


def fetch_active_subscriptions(api_key: str, limit_pages: int = 5):
    """Pulls active + trialing subscriptions, with their price expanded (on a
    besoin de price.unit_amount et price.recurring pour calculer le MRR sans
    un second aller-retour API par abonnement)."""
    subscriptions = []
    starting_after = None
    for _ in range(limit_pages):
        page = stripe.Subscription.list(
            status="all",
            limit=100,
            starting_after=starting_after,
            expand=["data.items.data.price"],
            api_key=api_key,
        )
        subscriptions.extend(page.data)
        if not page.has_more:
            break
        starting_after = page.data[-1].id
    return [s for s in subscriptions if s.status in ("active", "trialing")]


# Convertit n'importe quel intervalle de facturation Stripe (jour, semaine,
# mois x N, année) en équivalent mensuel — c'est la définition même du MRR
# (Monthly Recurring Revenue) : "si ce prix était facturé tous les mois,
# combien ça representerait ?".
_MONTHS_PER_INTERVAL = {"day": 1 / 30, "week": 1 / (52 / 12), "month": 1, "year": 12}


def compute_mrr(subscriptions):
    """Calcule le MRR à partir d'abonnements Stripe actifs/en essai.

    Regroupé par devise plutôt que sommé globalement : un abonnement à 10 USD
    et un à 10 EUR ne valent pas "20", ce sont deux montants dans deux
    unités différentes (même logique que pour les paiements ponctuels)."""
    by_currency = defaultdict(lambda: {"mrr": 0.0, "subscriptions": 0})

    for sub in subscriptions:
        for item in sub["items"]["data"]:
            price = item["price"]
            recurring = price.get("recurring") or {}
            interval = recurring.get("interval", "month")
            interval_count = recurring.get("interval_count", 1) or 1
            months = _MONTHS_PER_INTERVAL.get(interval, 1) * interval_count

            amount = (price.get("unit_amount") or 0) / 100 * item.get("quantity", 1)
            monthly_amount = amount / months if months else 0

            bucket = by_currency[price.get("currency", "eur")]
            bucket["mrr"] += monthly_amount
        by_currency[sub["items"]["data"][0]["price"].get("currency", "eur")]["subscriptions"] += 1

    return [
        {"currency": currency, "mrr": round(data["mrr"], 2), "active_subscriptions": data["subscriptions"]}
        for currency, data in sorted(by_currency.items())
    ]


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
