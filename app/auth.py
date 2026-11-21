"""Auth v0.3 — roles come ONLY from a verified token, never the request body.

v0.2 flaw (Ultra P0-1, correct): /search and /ask accepted `user_id` in the
JSON body, so anyone could become HR by typing `"user_id": "layla-hr"`.

v0.3 fix:
- Clients send `Authorization: Bearer <token>`.
- The token is HMAC-SHA256 signed (HS256-style) with DEMO_JWT_SECRET.
  Demo only: `POST /auth/demo-token` mints one for a demo user, and only
  when ALLOW_DEMO_TOKENS=true (default true locally, set false in prod).
- In production replace `verify_token` with PyJWT / OIDC verification
  against your IdP (Keycloak / Auth0) — the interface is the same:
  verify_token() -> {"sub", "roles"}.
- Roles are lowercased and checked against {"sales", "hr", "admin"}.
"""
import base64
import hashlib
import hmac
import json
import os
import time

DEMO_USERS = {
    "sara-sales": {"name": "Sara (Sales)", "roles": ["sales"]},
    "omar-sales": {"name": "Omar (Sales)", "roles": ["sales"]},
    "layla-hr": {"name": "Layla (HR)", "roles": ["hr"]},
    "ahmed-hr": {"name": "Ahmed (HR)", "roles": ["hr"]},
}

ALLOWED_ROLES = {"sales", "hr", "admin"}
_SECRET = os.environ.get("DEMO_JWT_SECRET", "dev-only-change-me-in-prod")
ALLOW_DEMO_TOKENS = os.environ.get("ALLOW_DEMO_TOKENS", "true").lower() == "true"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def create_demo_token(user_id: str, expires_in: int = 3600) -> str:
    """Mint a demo token. Only for local demo — disable in prod."""
    if not ALLOW_DEMO_TOKENS:
        raise ValueError("Demo tokens are disabled (ALLOW_DEMO_TOKENS=false)")
    user = DEMO_USERS.get(user_id)
    if not user:
        raise ValueError(f"Unknown demo user '{user_id}'")
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "roles": [r.lower() for r in user["roles"]],
        "exp": int(time.time()) + expires_in,
    }
    h = _b64(json.dumps(header, separators=(",", ":")).encode())
    p = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def verify_token(token: str) -> dict:
    """Verify signature + expiry, return {"sub", "roles"}. Raises ValueError."""
    try:
        h, p, sig = token.split(".")
    except ValueError:
        raise ValueError("Malformed token")
    expected = _b64(hmac.new(_SECRET.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid token signature")
    payload = json.loads(_unb64(p))
    if payload.get("exp", 0) < time.time():
        raise ValueError("Token expired")
    roles = [str(r).lower() for r in payload.get("roles", [])]
    if not roles or not set(roles) <= ALLOWED_ROLES:
        raise ValueError("Token carries no valid roles")
    return {"sub": payload.get("sub", "unknown"), "roles": roles}


def get_user_from_token(token: str) -> dict:
    claims = verify_token(token)
    return {"user_id": claims["sub"], "roles": claims["roles"]}


def allowed_roles_for(user: dict) -> list[str]:
    return user["roles"]
