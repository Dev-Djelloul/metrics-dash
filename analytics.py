"""
"BI-style" analytics on top of the normalized charge records produced
by stripe_metrics.charges_to_records(). Each function here mirrors a
technique real data analysts use (growth rates, RFM segmentation,
cohort retention, simple forecasting) — implemented from scratch with
plain Python so the logic stays visible instead of hidden in a library.
"""
from collections import defaultdict
from datetime import datetime, timezone


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# 1. Growth & trends: monthly revenue, MoM growth, YoY growth, 7-day moving avg
# ---------------------------------------------------------------------------
def growth_metrics(records):
    revenue_by_month = defaultdict(float)
    revenue_by_day = defaultdict(float)

    for r in records:
        revenue_by_month[_month_key(r["created"])] += r["amount"]
        revenue_by_day[r["created"].strftime("%Y-%m-%d")] += r["amount"]

    months = sorted(revenue_by_month.keys())
    monthly = []
    for i, month in enumerate(months):
        revenue = revenue_by_month[month]
        prev = revenue_by_month[months[i - 1]] if i > 0 else None
        mom_growth = ((revenue - prev) / prev * 100) if prev else None

        # Year-over-year: same month, previous year
        year, mm = month.split("-")
        yoy_key = f"{int(year) - 1}-{mm}"
        yoy_prev = revenue_by_month.get(yoy_key)
        yoy_growth = ((revenue - yoy_prev) / yoy_prev * 100) if yoy_prev else None

        monthly.append(
            {
                "month": month,
                "revenue": round(revenue, 2),
                "mom_growth_pct": round(mom_growth, 1) if mom_growth is not None else None,
                "yoy_growth_pct": round(yoy_growth, 1) if yoy_growth is not None else None,
            }
        )

    # 7-day moving average of daily revenue, for smoothing out day-to-day noise
    days = sorted(revenue_by_day.keys())
    daily_values = [revenue_by_day[d] for d in days]
    moving_avg = []
    window = 7
    for i in range(len(daily_values)):
        start = max(0, i - window + 1)
        chunk = daily_values[start : i + 1]
        moving_avg.append(round(sum(chunk) / len(chunk), 2))

    daily_with_avg = [
        {"date": d, "revenue": round(v, 2), "moving_avg_7d": ma}
        for d, v, ma in zip(days, daily_values, moving_avg)
    ]

    return {"monthly": monthly, "daily": daily_with_avg}


# ---------------------------------------------------------------------------
# 2. RFM segmentation: Recency / Frequency / Monetary
# ---------------------------------------------------------------------------
def _score_quartile(value, sorted_values, reverse=False):
    """Scores 1-4 based on which quartile `value` falls into."""
    n = len(sorted_values)
    if n == 0:
        return 1
    rank = sorted_values.index(value)
    quartile = min(4, (rank * 4) // n + 1)
    return (5 - quartile) if reverse else quartile


SEGMENT_LABELS = {
    (4, 4, 4): "Champions",
    (4, 3, 4): "Champions",
    (3, 4, 4): "Clients fidèles",
    (3, 3, 3): "Clients fidèles",
    (4, 1, 1): "Nouveaux clients",
    (4, 2, 1): "Nouveaux clients",
    (1, 4, 4): "À risque (gros clients)",
    (1, 3, 3): "À risque",
    (1, 1, 1): "Perdus",
    (2, 1, 1): "Perdus",
}


def _label_segment(r, f, m):
    if (r, f, m) in SEGMENT_LABELS:
        return SEGMENT_LABELS[(r, f, m)]
    avg = (r + f + m) / 3
    if avg >= 3.3:
        return "Clients fidèles"
    if avg >= 2:
        return "Clients occasionnels"
    return "À risque / Perdus"


def rfm_segments(records, as_of: datetime = None):
    as_of = as_of or datetime.now(timezone.utc)

    per_customer = defaultdict(lambda: {"last_purchase": None, "count": 0, "total": 0.0})
    for r in records:
        c = per_customer[r["customer"]]
        c["count"] += 1
        c["total"] += r["amount"]
        if c["last_purchase"] is None or r["created"] > c["last_purchase"]:
            c["last_purchase"] = r["created"]

    if not per_customer:
        return []

    recencies = sorted((as_of - c["last_purchase"]).days for c in per_customer.values())
    frequencies = sorted(c["count"] for c in per_customer.values())
    monetaries = sorted(round(c["total"], 2) for c in per_customer.values())

    results = []
    for name, c in per_customer.items():
        recency_days = (as_of - c["last_purchase"]).days
        r_score = _score_quartile(recency_days, recencies, reverse=True)
        f_score = _score_quartile(c["count"], frequencies)
        m_score = _score_quartile(round(c["total"], 2), monetaries)

        results.append(
            {
                "customer": name,
                "recency_days": recency_days,
                "frequency": c["count"],
                "monetary": round(c["total"], 2),
                "r_score": r_score,
                "f_score": f_score,
                "m_score": m_score,
                "segment": _label_segment(r_score, f_score, m_score),
            }
        )

    return sorted(results, key=lambda x: x["monetary"], reverse=True)


# ---------------------------------------------------------------------------
# 3. Cohort retention: group customers by their first-purchase month, then
#    track what % of each cohort is still buying N months later.
# ---------------------------------------------------------------------------
def cohort_retention(records):
    first_purchase = {}
    purchases_by_customer_month = defaultdict(set)

    for r in records:
        month = _month_key(r["created"])
        cust = r["customer"]
        purchases_by_customer_month[cust].add(month)
        if cust not in first_purchase or month < first_purchase[cust]:
            first_purchase[cust] = month

    cohorts = defaultdict(set)  # cohort_month -> set of customers
    for cust, cohort_month in first_purchase.items():
        cohorts[cohort_month].add(cust)

    all_months = sorted({_month_key(r["created"]) for r in records})

    def month_offset(m1, m2):
        y1, mo1 = map(int, m1.split("-"))
        y2, mo2 = map(int, m2.split("-"))
        return (y2 - y1) * 12 + (mo2 - mo1)

    table = []
    for cohort_month in sorted(cohorts.keys()):
        cohort_customers = cohorts[cohort_month]
        cohort_size = len(cohort_customers)
        row = {"cohort_month": cohort_month, "cohort_size": cohort_size, "retention": []}

        for m in all_months:
            offset = month_offset(cohort_month, m)
            if offset < 0:
                continue
            active = sum(
                1 for cust in cohort_customers if m in purchases_by_customer_month[cust]
            )
            pct = round(active / cohort_size * 100, 1) if cohort_size else 0
            row["retention"].append({"month_offset": offset, "active": active, "pct": pct})

        table.append(row)

    return table


# ---------------------------------------------------------------------------
# 4. Forecasting: simple linear regression on monthly revenue to project
#    the next few periods. No numpy — least squares by hand.
# ---------------------------------------------------------------------------
def forecast_revenue(records, periods_ahead: int = 3):
    revenue_by_month = defaultdict(float)
    for r in records:
        revenue_by_month[_month_key(r["created"])] += r["amount"]

    months = sorted(revenue_by_month.keys())
    if len(months) < 2:
        return {"history": [], "forecast": [], "note": "Pas assez de mois de données pour projeter."}

    xs = list(range(len(months)))
    ys = [revenue_by_month[m] for m in months]

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs) or 1
    slope = num / den
    intercept = mean_y - slope * mean_x

    def year_month_add(month_str, offset):
        y, m = map(int, month_str.split("-"))
        total = (y * 12 + (m - 1)) + offset
        return f"{total // 12}-{total % 12 + 1:02d}"

    forecast = []
    for i in range(1, periods_ahead + 1):
        x = len(months) - 1 + i
        predicted = max(0, slope * x + intercept)
        forecast.append(
            {"month": year_month_add(months[-1], i), "predicted_revenue": round(predicted, 2)}
        )

    history = [{"month": m, "revenue": round(revenue_by_month[m], 2)} for m in months]

    return {
        "history": history,
        "forecast": forecast,
        "trend": "croissant" if slope > 0 else ("décroissant" if slope < 0 else "stable"),
        "slope_per_month": round(slope, 2),
    }
