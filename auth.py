"""
Sessions "sans état" : plutôt qu'une table `sessions` en base (à créer, à
nettoyer, à interroger à chaque requête), on signe un petit jeton contenant
l'id utilisateur + une date d'expiration, avec HMAC-SHA256. Le serveur n'a
rien à stocker : il recalcule la signature à chaque requête et compare.
C'est le même principe qu'un JWT, en plus simple (pas de librairie externe).
"""
import base64
import hashlib
import hmac
import json
import time

SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 jours


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def create_session_token(user_id: int, secret: str) -> str:
    payload = json.dumps({"uid": user_id, "exp": int(time.time()) + SESSION_TTL_SECONDS}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = _sign(payload_b64.encode(), secret)
    return f"{payload_b64}.{signature}"


def verify_session_token(token: str, secret: str) -> int | None:
    """Returns the user_id if the token is valid and not expired, else None."""
    if not token or "." not in token:
        return None

    payload_b64, _, signature = token.partition(".")
    expected_signature = _sign(payload_b64.encode(), secret)
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None

    if payload.get("exp", 0) < time.time():
        return None

    return payload.get("uid")
