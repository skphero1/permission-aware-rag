"""T1-T5 eval suite (Ultra's 5-test gate, adapted to this repo's seed docs).

Every test asserts BOTH sides: positive recall for the authorised role
AND zero leakage for the forbidden role. These run offline against the
pure permission rule + sanitizer — the DB-backed versions of the same
assertions live in the CI notes in README (run against ephemeral Postgres).

Mapping to Ultra's original tests:
  T1 salary bands -> Salary Bands 2026 (hr only)
  T2 margin/discount floor -> Q4 Pricing & Discounts (sales only)
  T3 split topic -> same query must return disjoint citations per role
  T4 lexical code HR-EQ-409A -> Salary Bands 2026 §Equity (hr only)
  T5 injection -> Agency Onboarding shared doc carries an injection payload;
      sales must not escalate, must not see payroll, images stripped.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.permissions import is_allowed
from app.sanitize import (
    strip_markdown_images_and_links,
    validate_citations,
    wrap_passages,
)
from app.seed import SEED_DOCS


def _visible(roles):
    return [d for d in SEED_DOCS if is_allowed(d["allowed_roles"], roles)]


def _titles(docs):
    return {d["title"] for d in docs}


def test_t1_direct_leak_salary_bands():
    hr = _visible(["hr"])
    sales = _visible(["sales"])
    assert "Salary Bands 2026" in _titles(hr)
    assert "Salary Bands 2026" not in _titles(sales)
    # Sales must not even see the doc title in a citation list
    assert not any("Salary" in t for t in _titles(sales))


def test_t2_reverse_leak_discount_floor():
    sales = _visible(["sales"])
    hr = _visible(["hr"])
    assert "Q4 Pricing & Discounts" in _titles(sales)
    assert "Q4 Pricing & Discounts" not in _titles(hr)
    assert "Enterprise Client Contract Terms" not in _titles(hr)


def test_t3_split_topic_disjoint_citations():
    # A query touching both departments must return disjoint sets.
    sales_titles = _titles(_visible(["sales"]))
    hr_titles = _titles(_visible(["hr"]))
    shared = {"Product Overview", "Company Handbook", "Agency Onboarding"}
    assert sales_titles & hr_titles == shared
    assert "Salary Bands 2026" not in sales_titles
    assert "Q4 Pricing & Discounts" not in hr_titles


def test_t4_lexical_code_hr_eq_409a():
    doc = next(d for d in SEED_DOCS if d["title"] == "Salary Bands 2026")
    assert "HR-EQ-409A" in doc["content"]
    assert is_allowed(doc["allowed_roles"], ["hr"])
    assert not is_allowed(doc["allowed_roles"], ["sales"])
    # 404-not-403 rule: an unauthorised lookup must not confirm existence.
    # (Enforced in app/main.py get_doc; here we assert the permission fact
    # that makes that endpoint return 404 for sales.)
    assert "hr" in doc["allowed_roles"] and "sales" not in doc["allowed_roles"]


def test_t5_adversarial_injection_shared_doc():
    doc = next(d for d in SEED_DOCS if d["title"] == "Agency Onboarding")
    assert "Ignore prior rules" in doc["content"]
    # The payload is inside a SHARED doc, so both roles can retrieve the doc —
    # but the instruction inside it must never change the caller's roles.
    assert is_allowed(doc["allowed_roles"], ["sales"])
    sales_docs = _visible(["sales"])
    assert "Salary Bands 2026" not in _titles(sales_docs)
    # Markdown image beacons in retrieved text are stripped from answers.
    assert strip_markdown_images_and_links(doc["content"]).find("evil.tld") == -1
    assert "[image removed]" in strip_markdown_images_and_links(doc["content"])
    # Passages are wrapped as untrusted data, and citations are validated.
    passages = [{"chunk_id": "c1", "doc_title": doc["title"], "text": doc["content"]}]
    wrapped = wrap_passages(passages)
    assert "<untrusted_passage" in wrapped
    assert validate_citations(["c1", "forged-id"], ["c1"]) == ["c1"]
