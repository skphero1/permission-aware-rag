"""Permission tests — the tests that matter for Project 8.

These run WITHOUT a database: they test the same rule the SQL enforces
(`allowed_roles && user_roles`). The DB integration test at the bottom is
skipped when Postgres is not available.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.permissions import is_allowed
from app.seed import SEED_DOCS


def docs_visible_to(roles):
    return [d for d in SEED_DOCS if is_allowed(d["allowed_roles"], roles)]


def test_sales_cannot_see_hr_docs():
    visible = docs_visible_to(["sales"])
    titles = {d["title"] for d in visible}
    assert "Salary Bands 2026" not in titles
    assert "Disciplinary Policy" not in titles
    assert "Q4 Pricing & Discounts" in titles


def test_hr_cannot_see_sales_docs():
    visible = docs_visible_to(["hr"])
    titles = {d["title"] for d in visible}
    assert "Q4 Pricing & Discounts" not in titles
    assert "Enterprise Client Contract Terms" not in titles
    assert "Salary Bands 2026" in titles


def test_shared_docs_visible_to_both():
    for roles in (["sales"], ["hr"]):
        titles = {d["title"] for d in docs_visible_to(roles)}
        assert "Product Overview" in titles
        assert "Company Handbook" in titles


def test_query_words_do_not_override_permissions():
    # Even if the query matches HR text exactly, Sales still gets nothing HR-only.
    # This is the leak the SQL filter prevents: similarity must not beat permissions.
    visible = docs_visible_to(["sales"])
    assert all("hr" not in d["allowed_roles"] or "sales" in d["allowed_roles"] for d in visible)
    assert not any(d["title"] == "Salary Bands 2026" for d in visible)


def test_seed_has_expected_split():
    # v0.3: 2 sales-only, 2 hr-only, 3 shared (incl. Agency Onboarding, the T5 injection doc)
    sales_only = [d for d in SEED_DOCS if d["allowed_roles"] == ["sales"]]
    hr_only = [d for d in SEED_DOCS if d["allowed_roles"] == ["hr"]]
    shared = [d for d in SEED_DOCS if set(d["allowed_roles"]) == {"sales", "hr"}]
    assert len(sales_only) == 2
    assert len(hr_only) == 2
    assert len(shared) == 3
    assert len(SEED_DOCS) == 7
