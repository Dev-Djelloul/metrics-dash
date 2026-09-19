"""
Fetches Shopify orders and normalizes them into the same record shape
used everywhere else (analytics.py, stripe_metrics.py) : {customer, amount,
created, country, currency}. Ça permet aux commandes Shopify de rejoindre
les charges Stripe dans get_all_records() sans que les modules d'analyse
(RFM, cohortes, prévision...) aient besoin de savoir d'où vient chaque vente.
"""
from datetime import datetime

import httpx

_API_VERSION = "2024-01"


def fetch_orders(shop_domain: str, access_token: str, limit_pages: int = 5) -> list[dict]:
    """Pulls up to `limit_pages` pages (250 each) of paid orders via l'API
    REST Admin de Shopify. La pagination Shopify se fait via l'en-tête `Link`
    (page_info), pas un simple offset."""
    orders = []
    url = f"https://{shop_domain}/admin/api/{_API_VERSION}/orders.json"
    params = {"status": "any", "financial_status": "paid", "limit": 250}
    headers = {"X-Shopify-Access-Token": access_token}

    for _ in range(limit_pages):
        response = httpx.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        orders.extend(response.json().get("orders", []))

        next_link = _next_page_url(response.headers.get("Link", ""))
        if not next_link:
            break
        url, params = next_link, None

    return orders


def _next_page_url(link_header: str) -> str | None:
    """Parse l'en-tête `Link: <url>; rel="next", <url>; rel="previous"`
    renvoyé par l'API Shopify pour la pagination par curseur."""
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def orders_to_records(orders: list[dict]) -> list[dict]:
    records = []
    for o in orders:
        customer = o.get("customer") or {}
        customer_key = (
            o.get("email")
            or customer.get("email")
            or f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
            or "unknown"
        )
        shipping = o.get("shipping_address") or {}
        records.append(
            {
                "customer": customer_key,
                "amount": float(o.get("total_price") or 0),
                "created": datetime.fromisoformat(o["created_at"]),
                "country": shipping.get("country_code"),
                "currency": (o.get("currency") or "eur").lower(),
            }
        )
    return records
