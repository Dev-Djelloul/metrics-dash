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


def filter_by_date(records, start: str = None, end: str = None):
    """Filtre les enregistrements sur une période (dates "YYYY-MM-DD",
    bornes incluses). Utilisé par le date range picker du dashboard —
    None de chaque côté laisse la période ouverte dans ce sens."""
    if not start and not end:
        return records

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc) if start else None
    end_dt = (
        datetime.strptime(end, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        if end
        else None
    )

    return [
        r
        for r in records
        if (start_dt is None or r["created"] >= start_dt)
        and (end_dt is None or r["created"] <= end_dt)
    ]


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
def _year_month_add(month_str, offset):
    y, m = map(int, month_str.split("-"))
    total = (y * 12 + (m - 1)) + offset
    return f"{total // 12}-{total % 12 + 1:02d}"


def _fit_linear_regression(xs, ys):
    """Régression linéaire par les moindres carrés, à la main. Retourne
    aussi de quoi construire un intervalle de confiance (écart-type des
    résidus, position par rapport à la moyenne) plutôt qu'une seule
    fonction de calcul de pente."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs) or 1
    slope = num / den
    intercept = mean_y - slope * mean_x

    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    dof = max(1, n - 2)  # degrés de liberté : n points - 2 paramètres estimés
    residual_std = (sum(e**2 for e in residuals) / dof) ** 0.5

    return {"slope": slope, "intercept": intercept, "mean_x": mean_x, "den": den, "residual_std": residual_std, "n": n}


def _forecast_series(months, values, periods_ahead, value_key):
    """Projette une série mensuelle (CA, nb de commandes, nb de nouveaux
    clients...) par régression linéaire, avec une zone d'incertitude
    (~95%) qui s'élargit avec l'horizon de prévision — une prévision loin
    dans le futur est statistiquement moins fiable qu'une prévision proche,
    ce qu'une seule ligne de prédiction ne montre pas."""
    xs = list(range(len(months)))
    fit = _fit_linear_regression(xs, values)
    slope, intercept = fit["slope"], fit["intercept"]

    forecast = []
    for i in range(1, periods_ahead + 1):
        x = len(months) - 1 + i
        predicted = max(0, slope * x + intercept)
        margin = 1.96 * fit["residual_std"] * (1 + 1 / fit["n"] + (x - fit["mean_x"]) ** 2 / fit["den"]) ** 0.5
        forecast.append(
            {
                "month": _year_month_add(months[-1], i),
                value_key: round(predicted, 2),
                "low": round(max(0, predicted - margin), 2),
                "high": round(predicted + margin, 2),
            }
        )

    return forecast, slope


def forecast_revenue(records, periods_ahead: int = 3):
    revenue_by_month = defaultdict(float)
    for r in records:
        revenue_by_month[_month_key(r["created"])] += r["amount"]

    months = sorted(revenue_by_month.keys())
    if len(months) < 2:
        return {"history": [], "forecast": [], "note": "Pas assez de mois de données pour projeter."}

    values = [revenue_by_month[m] for m in months]
    forecast, slope = _forecast_series(months, values, periods_ahead, "predicted_revenue")
    history = [{"month": m, "revenue": round(revenue_by_month[m], 2)} for m in months]

    return {
        "history": history,
        "forecast": forecast,
        "trend": "croissant" if slope > 0 else ("décroissant" if slope < 0 else "stable"),
        "slope_per_month": round(slope, 2),
    }


def forecast_order_count(records, periods_ahead: int = 3):
    orders_by_month = defaultdict(int)
    for r in records:
        orders_by_month[_month_key(r["created"])] += 1

    months = sorted(orders_by_month.keys())
    if len(months) < 2:
        return {"history": [], "forecast": [], "note": "Pas assez de mois de données pour projeter."}

    values = [orders_by_month[m] for m in months]
    forecast, slope = _forecast_series(months, values, periods_ahead, "predicted_orders")
    for point in forecast:
        point["predicted_orders"] = round(point["predicted_orders"])
        point["low"] = round(point["low"])
        point["high"] = round(point["high"])
    history = [{"month": m, "orders": orders_by_month[m]} for m in months]

    return {
        "history": history,
        "forecast": forecast,
        "trend": "croissant" if slope > 0 else ("décroissant" if slope < 0 else "stable"),
    }


def forecast_new_customers(records, periods_ahead: int = 3):
    first_purchase_month = {}
    for r in records:
        month = _month_key(r["created"])
        cust = r["customer"]
        if cust not in first_purchase_month or month < first_purchase_month[cust]:
            first_purchase_month[cust] = month

    new_customers_by_month = defaultdict(int)
    for month in first_purchase_month.values():
        new_customers_by_month[month] += 1

    months = sorted(new_customers_by_month.keys())
    if len(months) < 2:
        return {"history": [], "forecast": [], "note": "Pas assez de mois de données pour projeter."}

    values = [new_customers_by_month[m] for m in months]
    forecast, slope = _forecast_series(months, values, periods_ahead, "predicted_new_customers")
    for point in forecast:
        point["predicted_new_customers"] = round(point["predicted_new_customers"])
        point["low"] = round(point["low"])
        point["high"] = round(point["high"])
    history = [{"month": m, "new_customers": new_customers_by_month[m]} for m in months]

    return {
        "history": history,
        "forecast": forecast,
        "trend": "croissant" if slope > 0 else ("décroissant" if slope < 0 else "stable"),
    }


# ---------------------------------------------------------------------------
# 5. Revenue by weekday: total CA and nombre de commandes par jour de la
#    semaine (toutes semaines confondues) — utile pour repérer un jour fort.
# ---------------------------------------------------------------------------
WEEKDAY_LABELS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def revenue_by_weekday(records):
    revenue = defaultdict(float)
    orders = defaultdict(int)
    for r in records:
        weekday = r["created"].weekday()  # 0 = lundi ... 6 = dimanche
        revenue[weekday] += r["amount"]
        orders[weekday] += 1

    return [
        {
            "weekday": WEEKDAY_LABELS[i],
            "revenue": round(revenue.get(i, 0), 2),
            "orders": orders.get(i, 0),
        }
        for i in range(7)
    ]


# ---------------------------------------------------------------------------
# 6. Revenue concentration: quelle part du CA vient des X% de clients qui
#    dépensent le plus (illustre le principe de Pareto / 80-20).
# ---------------------------------------------------------------------------
def revenue_concentration(records, top_share: float = 0.2):
    totals_by_customer = defaultdict(float)
    for r in records:
        totals_by_customer[r["customer"]] += r["amount"]

    if not totals_by_customer:
        return {"top_share_pct": round(top_share * 100), "segments": [], "total_customers": 0}

    sorted_totals = sorted(totals_by_customer.values(), reverse=True)
    total_revenue = sum(sorted_totals)
    n_customers = len(sorted_totals)
    n_top = max(1, round(n_customers * top_share))

    top_revenue = sum(sorted_totals[:n_top])
    rest_revenue = total_revenue - top_revenue

    return {
        "top_share_pct": round(top_share * 100),
        "top_customers_count": n_top,
        "total_customers": n_customers,
        "segments": [
            {"label": f"Top {round(top_share * 100)}% clients", "amount": round(top_revenue, 2)},
            {"label": "Autres clients", "amount": round(rest_revenue, 2)},
        ],
    }


# ---------------------------------------------------------------------------
# 7. Nouveaux vs clients récurrents : pour chaque mois, quelle part du CA
#    vient de clients qui achètent pour la première fois vs qui reviennent.
# ---------------------------------------------------------------------------
def new_vs_returning_by_month(records):
    first_purchase_month = {}
    for r in records:
        month = _month_key(r["created"])
        cust = r["customer"]
        if cust not in first_purchase_month or month < first_purchase_month[cust]:
            first_purchase_month[cust] = month

    new_revenue = defaultdict(float)
    returning_revenue = defaultdict(float)
    for r in records:
        month = _month_key(r["created"])
        cust = r["customer"]
        if first_purchase_month[cust] == month:
            new_revenue[month] += r["amount"]
        else:
            returning_revenue[month] += r["amount"]

    months = sorted(set(new_revenue) | set(returning_revenue))
    return [
        {
            "month": m,
            "new_revenue": round(new_revenue.get(m, 0), 2),
            "returning_revenue": round(returning_revenue.get(m, 0), 2),
        }
        for m in months
    ]


# ---------------------------------------------------------------------------
# 8. Fidélité & valeur client : LTV moyen (revenu total / nombre de clients)
#    et délai moyen entre deux achats pour les clients qui reviennent.
# ---------------------------------------------------------------------------
def loyalty_metrics(records):
    totals_by_customer = defaultdict(float)
    dates_by_customer = defaultdict(list)
    for r in records:
        totals_by_customer[r["customer"]] += r["amount"]
        dates_by_customer[r["customer"]].append(r["created"])

    customer_count = len(totals_by_customer)
    avg_ltv = round(sum(totals_by_customer.values()) / customer_count, 2) if customer_count else 0

    per_customer_avg_gaps = []
    for dates in dates_by_customer.values():
        dates = sorted(dates)
        if len(dates) < 2:
            continue
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        per_customer_avg_gaps.append(sum(gaps) / len(gaps))

    avg_days_between_purchases = (
        round(sum(per_customer_avg_gaps) / len(per_customer_avg_gaps), 1)
        if per_customer_avg_gaps
        else None
    )

    return {
        "avg_ltv": avg_ltv,
        "customer_count": customer_count,
        "avg_days_between_purchases": avg_days_between_purchases,
        "repeat_customer_count": len(per_customer_avg_gaps),
    }


# ---------------------------------------------------------------------------
# 9. Répartition géographique : CA par pays de facturation.
# ---------------------------------------------------------------------------
# ISO 3166-1 numérique pour les pays qu'on est susceptible de voir : la
# carte du monde (world-atlas, côté frontend) identifie ses pays par ce
# code numérique, pas par le code alpha-2 ("FR") que Stripe nous donne.
# Un pays absent de cette table n'apparaîtra pas sur la carte, mais reste
# compté dans le CA total et dans le tableau texte à côté.
ISO_NUMERIC = {
    "FR": "250", "BE": "056", "CH": "756", "DE": "276", "ES": "724",
    "US": "840", "GB": "826", "CA": "124", "IT": "380", "NL": "528",
    "PT": "620", "LU": "442", "AT": "040", "SE": "752", "NO": "578",
    "DK": "208", "FI": "246", "PL": "616", "IE": "372", "GR": "300",
    "AU": "036", "JP": "392", "BR": "076", "MX": "484", "IN": "356",
    "CN": "156", "KR": "410", "SG": "702", "AE": "784", "ZA": "710",
    "NZ": "554", "CZ": "203", "RO": "642", "HU": "348", "MA": "504",
    "TN": "788", "TR": "792", "IL": "376", "RU": "643", "UA": "804",
}


def revenue_by_country(records):
    revenue = defaultdict(float)
    customers = defaultdict(set)
    unknown_count = 0

    for r in records:
        country = r.get("country")
        if not country:
            unknown_count += 1
            continue
        revenue[country] += r["amount"]
        customers[country].add(r["customer"])

    countries = [
        {
            "country": code,
            "iso_numeric": ISO_NUMERIC.get(code),
            "revenue": round(amount, 2),
            "customer_count": len(customers[code]),
        }
        for code, amount in sorted(revenue.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {"countries": countries, "unknown_count": unknown_count}
