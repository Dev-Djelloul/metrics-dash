"""
Generates a synthetic but realistic e-commerce dataset spanning several
months, so the time-based analyses (cohorts, growth trends, forecast) have
enough history to be meaningful — something a real Stripe test account
can't easily provide since charge timestamps can't be backdated via the API.

Produces the same record shape as stripe_metrics.charges_to_records():
[{"customer": str, "amount": float, "created": datetime}, ...]

Plusieurs PROFILES : des scénarios d'entreprise distincts (croissance saine,
saisonnier, en déclin, stable) plutôt qu'un seul jeu de données figé, pour
que le mode démo soit plus immersif — chacun a sa propre répartition
d'archétypes clients, son propre seed (donc des clients/montants visible-
ment différents d'un profil à l'autre) et, pour certains, une tendance
mensuelle (saisonnalité, déclin) appliquée aux montants.
"""
import random
from datetime import datetime, timedelta, timezone

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

# Chaque profil définit : un seed distinct (pour que les jeux de données se
# distinguent visuellement, pas juste par leur nom), la répartition des
# archétypes clients (type, poids cumulatif, commandes/mois, montant), et
# une fonction de tendance mensuelle optionnelle (multiplicateur appliqué
# au montant selon le mois écoulé, pour simuler saisonnalité ou déclin).
DEMO_PROFILES = {
    "growth": {
        "seed": 7,
        "label_fr": "Croissance saine",
        "label_en": "Healthy growth",
        "archetypes": [
            ("champion", 0.15, (2, 4), (40, 150)),
            ("loyal", 0.25, (1, 2), (20, 80)),
            ("churned", 0.25, (1, 2), (15, 60)),
            ("one_time", 0.35, (0, 1), (10, 100)),
        ],
        "trend": lambda month_offset, months: 1 + 0.06 * month_offset,
        "refund_rate": 0.03,
    },
    "seasonal": {
        "seed": 11,
        "label_fr": "E-commerce saisonnier",
        "label_en": "Seasonal e-commerce",
        "archetypes": [
            ("champion", 0.12, (2, 4), (35, 130)),
            ("loyal", 0.28, (1, 2), (20, 75)),
            ("churned", 0.25, (1, 2), (15, 55)),
            ("one_time", 0.35, (0, 1), (10, 90)),
        ],
        # Pic sur les 2 derniers mois de la fenêtre (simule une saison haute,
        # type fêtes de fin d'année), calme le reste du temps.
        "trend": lambda month_offset, months: 2.2 if month_offset >= months - 2 else 0.85,
        # Plus de retours en e-commerce qu'ailleurs, et encore plus pendant
        # le pic saisonnier (achats impulsifs, cadeaux qui ne conviennent pas).
        "refund_rate": 0.06,
    },
    "decline": {
        "seed": 13,
        "label_fr": "Activité en déclin",
        "label_en": "Declining business",
        "archetypes": [
            ("champion", 0.05, (1, 2), (30, 120)),
            ("loyal", 0.15, (1, 2), (15, 60)),
            ("churned", 0.45, (1, 2), (10, 50)),
            ("one_time", 0.35, (0, 1), (10, 80)),
        ],
        # Les mois récents pèsent de moins en moins lourd — l'inverse de
        # "growth" — pour simuler un chiffre d'affaires qui s'effrite.
        "trend": lambda month_offset, months: max(0.35, 1 - 0.10 * month_offset),
        # Un taux de remboursement élevé est cohérent avec un déclin : souvent
        # un symptôme (produit/service qui déçoit) plutôt qu'une coïncidence.
        "refund_rate": 0.09,
    },
    "steady": {
        "seed": 17,
        "label_fr": "Activité stable",
        "label_en": "Stable business",
        "archetypes": [
            ("champion", 0.10, (1, 3), (30, 100)),
            ("loyal", 0.55, (1, 2), (20, 70)),
            ("churned", 0.15, (1, 2), (15, 50)),
            ("one_time", 0.20, (0, 1), (10, 80)),
        ],
        "trend": lambda month_offset, months: 1,
        "refund_rate": 0.02,
    },
}

DEFAULT_PROFILE = "growth"


def list_profiles():
    return [
        {"key": key, "label_fr": p["label_fr"], "label_en": p["label_en"]}
        for key, p in DEMO_PROFILES.items()
    ]


def _pick_country(rng):
    r = rng.random()
    cumulative = 0
    for code, weight in COUNTRY_WEIGHTS:
        cumulative += weight
        if r < cumulative:
            return code
    return COUNTRY_WEIGHTS[-1][0]


def _customer_archetype(rng, archetypes):
    """Assigns each customer a behavior archetype (selon la répartition du
    profil actif) so the dataset produces realistic segments once RFM/
    cohorts run on it."""
    roll = rng.random()
    cumulative = 0
    for archetype_type, weight, monthly_orders, amount_range in archetypes:
        cumulative += weight
        if roll < cumulative:
            return {"type": archetype_type, "monthly_orders": monthly_orders, "amount_range": amount_range}
    archetype_type, _, monthly_orders, amount_range = archetypes[-1]
    return {"type": archetype_type, "monthly_orders": monthly_orders, "amount_range": amount_range}


def generate_demo_records(months=MONTHS_OF_HISTORY, profile=DEFAULT_PROFILE):
    config = DEMO_PROFILES.get(profile, DEMO_PROFILES[DEFAULT_PROFILE])

    # Instance locale plutôt que le module `random` global : celui-ci garde
    # un état partagé par tout le process, donc avec un seed posé une seule
    # fois à l'import, seul le tout premier appel après démarrage du serveur
    # était reproductible — chaque appel suivant (chaque requête /api/*, il
    # n'y a pas de cache ici) repartait d'un état différent et générait des
    # clients/montants différents. Une Random() locale, recréée à chaque
    # appel, rend le jeu de données identique à chaque fois, y compris avec
    # des requêtes en parallèle (loadAll() en tire une quinzaine à la fois).
    rng = random.Random(config["seed"])
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_month = (midnight.replace(day=1) - timedelta(days=30 * (months - 1))).replace(day=1)

    records = []
    for name in CUSTOMER_NAMES:
        archetype = _customer_archetype(rng, config["archetypes"])
        country = _pick_country(rng)
        first_active_offset = rng.randint(0, months - 2)

        if archetype["type"] == "churned":
            active_span = rng.randint(1, max(1, months // 3))
        elif archetype["type"] == "one_time":
            active_span = 1
        else:
            active_span = months - first_active_offset

        for month_offset in range(first_active_offset, min(months, first_active_offset + active_span)):
            lo, hi = archetype["monthly_orders"]
            num_orders = rng.randint(lo, hi)
            month_multiplier = config["trend"](month_offset, months)
            for _ in range(num_orders):
                day = rng.randint(1, 28)
                created = _add_months(start_month, month_offset).replace(
                    day=day, hour=rng.randint(8, 21), minute=rng.randint(0, 59)
                )
                if created > now:
                    continue
                amount = round(rng.uniform(*archetype["amount_range"]) * month_multiplier, 2)
                records.append(
                    {
                        "customer": name,
                        "amount": amount,
                        "created": created,
                        "country": country,
                        "currency": "eur",
                        "refunded": rng.random() < config["refund_rate"],
                    }
                )

    return sorted(records, key=lambda r: r["created"])


def _add_months(dt, n):
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month)
