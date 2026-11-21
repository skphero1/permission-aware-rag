"""Pure permission rule — no DB imports, so tests run offline.

This mirrors the SQL in db.py: `allowed_roles && user_roles`.
If this function and the SQL ever disagree, the SQL wins in prod and
the test must be fixed. Keep them in sync.
"""


def is_allowed(doc_allowed_roles: list[str], user_roles: list[str]) -> bool:
    return bool(set(doc_allowed_roles) & set(user_roles))
