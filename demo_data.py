"""
Generates a synthetic but realistic e-commerce dataset spanning several
months, so the time-based analyses (cohorts, growth trends, forecast) have
enough history to be meaningful — something a real Stripe test account
can't easily provide since charge timestamps can't be backdated via the API.

Produces the same record shape as stripe_metrics.charges_to_records():
[{"customer": str, "amount": float, "created": datetime}, ...]
"""
import random
from datetime import datetime, timedelta, timezone

random.seed(7)

MONTHS_OF_HISTORY = 8
CUSTOMER_NAMES = [
    "Alice Martin", "Bruno Bernard", "Chloé Dubois", "David Petit", "Emma Robert",
    "Farid Moreau", "Giulia Laurent", "Hugo Simon", "Inès Michel", "Julien Leroy",
    "Karim Roux", "Léa Fournier", "Marco Girard", "Nina Bonnet", "Oscar Faure",
    "Prisca André", "Quentin Mercier", "Rania Blanc", "Samuel Guerin", "Tara Muller",
]


# Pays de facturation simulés, pondérés pour ressembler à une clientèle
# majoritairement française avec un peu d'international.
COUNTRY_WEIGHTS = [
    ("FR", 0.45), ("BE", 0.10), ("CH", 0.08), ("DE", 0.08), ("ES", 0.06),
    ("US", 0.06), ("GB", 0.05), ("CA", 0.04), ("IT", 0.04), ("NL", 0.04),
]


def _pick_country():
    r = random.random()
    cumulative = 0
    for code, weight in COUNTRY_WEIGHTS:
        cumulative += weight
        if r < cumulative:
            return code
    return COUNTRY_WEIGHTS[-1][0]


def _customer_profile(name):
    """Assigns each customer a behavior archetype so the dataset produces
    realistic segments once RFM/cohorts run on it."""
    roll = random.random()
    if roll < 0.15:
        return {"type": "champion", "monthly_orders": (2, 4), "amount_range": (40, 150)}
    if roll < 0.40:
        return {"type": "loyal", "monthly_orders": (1, 2), "amount_range": (20, 80)}
    if roll < 0.65:
        return {"type": "churned", "monthly_orders": (1, 2), "amount_range": (15, 60)}
    return {"type": "one_time", "monthly_orders": (0, 1), "amount_range": (10, 100)}


def generate_demo_records(months=MONTHS_OF_HISTORY):
    now = datetime.now(timezone.utc)
    start_month = (now.replace(day=1) - timedelta(days=30 * (months - 1))).replace(day=1)

    records = []
    for name in CUSTOMER_NAMES:
        profile = _customer_profile(name)
        country = _pick_country()
        first_active_offset = random.randint(0, months - 2)

        if profile["type"] == "churned":
            active_span = random.randint(1, max(1, months // 3))
        elif profile["type"] == "one_time":
            active_span = 1
        else:
            active_span = months - first_active_offset

        for month_offset in range(first_active_offset, min(months, first_active_offset + active_span)):
            lo, hi = profile["monthly_orders"]
            num_orders = random.randint(lo, hi)
            for _ in range(num_orders):
                day = random.randint(1, 28)
                created = _add_months(start_month, month_offset).replace(
                    day=day, hour=random.randint(8, 21), minute=random.randint(0, 59)
                )
                if created > now:
                    continue
                amount = round(random.uniform(*profile["amount_range"]), 2)
                records.append(
                    {"customer": name, "amount": amount, "created": created, "country": country, "currency": "eur"}
                )

    return sorted(records, key=lambda r: r["created"])


def _add_months(dt, n):
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month)
