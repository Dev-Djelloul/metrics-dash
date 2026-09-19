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


def fetch_canonical_shop_domain(shop: str, access_token: str) -> str:
    """Renvoie le domaine *.myshopify.com canonique via GET /shop.json, plutôt
    que de faire confiance au `shop` saisi/redirigé par l'utilisateur au moment
    du connect. Nécessaire car renommer une boutique dans l'admin Shopify
    change son myshopify_domain, mais l'ancien domaine continue de fonctionner
    pour l'autorisation OAuth (Shopify le redirige en interne) — sans cet
    appel, on stockerait indéfiniment un domaine périmé. Retombe sur `shop`
    si l'appel échoue, pour ne jamais bloquer la connexion sur ce détail."""
    try:
        response = httpx.get(
            f"https://{shop}/admin/api/{_API_VERSION}/shop.json",
            headers={"X-Shopify-Access-Token": access_token},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["shop"]["myshopify_domain"]
    except Exception as exc:
        print(f"[metrics-dash] Échec de résolution du domaine Shopify canonique pour {shop} : {type(exc).__name__}: {exc}")
        return shop


def fetch_orders(shop_domain: str, access_token: str, limit_pages: int = 5) -> list[dict]:
    """Pulls up to `limit_pages` pages (250 each) of paid orders via l'API
    REST Admin de Shopify. La pagination Shopify se fait via l'en-tête `Link`
    (page_info), pas un simple offset."""
    # Pas de filtre financial_status : on veut aussi les commandes remboursées
    # (refunded/partially_refunded), pour pouvoir calculer un taux de
    # remboursement — orders_to_records() les marque via un flag "refunded"
    # plutôt que de les exclure ici, main.py se charge de les retirer du CA
    # par défaut (comme avant) sauf appel explicite include_refunded=True.
    orders = []
    url = f"https://{shop_domain}/admin/api/{_API_VERSION}/orders.json"
    params = {"status": "any", "limit": 250}
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


# Statuts financiers Shopify à retenir : une commande a été honorée (payée,
# éventuellement remboursée ensuite) — on exclut pending/voided/unpaid, qui
# ne représentent ni du CA ni un remboursement, juste une commande jamais
# aboutie.
_COUNTED_FINANCIAL_STATUSES = {"paid", "partially_paid", "refunded", "partially_refunded"}


def orders_to_records(orders: list[dict]) -> list[dict]:
    records = []
    for o in orders:
        if o.get("financial_status") not in _COUNTED_FINANCIAL_STATUSES:
            continue
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
                "refunded": o.get("financial_status") in ("refunded", "partially_refunded"),
            }
        )
    return records
