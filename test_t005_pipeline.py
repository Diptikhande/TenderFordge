"""
T005.pdf (Business Correspondent devices RFP) pipeline tests.

Offline checks (always run): overview extraction, table-based requirement extraction, rule evaluation,
recommendation safety and metrics. The Groq step runs only with TENDERFORGE_LIVE_GROQ=1.
All database writes go to an isolated temporary database; no real tender IDs or profiles are used.
"""

import tf_test_env  # noqa: F401  (must precede project imports)

import pytest

from analyzer import (
    analyze_tender_with_groq, extract_all_structured_requirements, extract_tender_overview_deterministic,
    run_deterministic_analysis, run_deterministic_checks, NOT_FOUND,
)
from database import init_db, create_user, save_tender, save_analysis, get_analysis_for_tender, compute_profile_hash, get_connection
from pdf_processor import extract_text_from_pdf

PDF = tf_test_env.sample_pdf("T005.pdf")

# Synthetic machining-company fixture (no banking / BC experience). Turnover is given per financial year
# in INR so that it is comparable with the tender's "average annual turnover over the last three FY".
PROFILE = {
    "company_name": "Mahalaxmi Engineering Works",
    "industry": "Manufacturing and Engineering Services",
    "years_of_experience": "10 years",
    "annual_turnover": "FY2023-24: INR 18 Lakh; FY2024-25: INR 20 Lakh; FY2025-26: INR 22 Lakh",
    "certifications": "ISO 9001: Not verified\nISO 14001: Not verified",
    "technical_capabilities": "CNC turning and precision machining, Vertical Machining Centre (VMC) operations, Conventional lathe machining",
    "past_projects": "",
    "financial_details": "",
}


@pytest.fixture(scope="module")
def tender_text():
    ext = extract_text_from_pdf(PDF)
    assert ext["success"] and "--- [Page 1] ---" in ext["text"]
    return ext["text"]


def test_t005_overview(tender_text):
    ov = extract_tender_overview_deterministic(tender_text, pdf_path=PDF)
    assert ov["title"] != "1"
    assert "BUSINESS CORRESPONDENT" in ov["title"].upper() or "RATE CONTRACT" in ov["title"].upper()
    assert ov["reference_number"] == "MPBCDC/MD/P-1/POS/Aug/2026"
    assert "MAHATMA PHULE" in ov["issuing_organization"].upper()
    assert "17/09/2026" in ov["deadline"]
    assert "Turnkey supply, installation" not in ov["scope_of_work"]  # no generated scope text


def test_t005_requirement_extraction(tender_text):
    reqs = extract_all_structured_requirements(tender_text, PROFILE, pdf_path=PDF)
    assert len(reqs) >= 13
    first = reqs[0]
    assert first["requirement_id"] == "ELG-01" and first["source_page"] == 34
    assert len(first["source_quotation"]) > 10
    assert len({r["requirement_id"] for r in reqs}) == len(reqs)


def test_t005_rule_evaluation(tender_text):
    checks = run_deterministic_checks(tender_text, PROFILE, pdf_path=PDF)
    by_id = {c["requirement_id"]: c for c in checks}
    turnover = by_id["ELG-04"]
    assert turnover["requirement_kind"] == "turnover"
    assert turnover["status"] == "CONFIRMED GAP" and turnover["rule_decisive"]  # INR 20 Lakh average < INR 50 Crore
    bc = by_id["ELG-02"]
    # No BC experience in the profile is missing information, not a proven failure; CNC must not be used as evidence.
    assert bc["status"] in ("INFORMATION REQUIRED", "UNRESOLVED")
    assert "cnc" not in bc["company_evidence"].lower()
    iso = [c for c in checks if c["requirement_kind"] == "certification"]
    assert all(c["status"] != "SATISFIED" for c in iso)


def test_t005_turnover_without_currency_is_information_required(tender_text):
    checks = run_deterministic_checks(tender_text, dict(PROFILE, annual_turnover="2000000"), pdf_path=PDF)
    assert next(c for c in checks if c["requirement_id"] == "ELG-04")["status"] == "INFORMATION REQUIRED"


@pytest.mark.parametrize("turnover", ["INR 60 Crore", "FY2023-24: INR 60 Crore"])
def test_profile_turnover_above_threshold_is_never_satisfied_by_profile_value(tender_text, turnover):
    checks = run_deterministic_checks(tender_text, dict(PROFILE, annual_turnover=turnover), pdf_path=PDF)
    turnover_check = next(c for c in checks if c["requirement_id"] == "ELG-04")
    assert turnover_check["status"] != "SATISFIED"
    assert turnover_check["status"] in ("INFORMATION REQUIRED", "UNRESOLVED")


def test_t005_recommendation_and_metrics(tender_text):
    analysis = run_deterministic_analysis(tender_text, PROFILE, pdf_path=PDF)
    rec = analysis["bid_recommendation"]
    assert rec["decision"] == "NO-BID"
    assert rec["blockers"] and all("ELG-04" in b for b in rec["blockers"])
    assert any("need information" in r for r in rec["reasons"])  # open items reported separately from the blocker
    m = analysis["metrics"]
    assert m["total_identified"] == len(analysis["compliance_matrix"]) >= 13
    assert int(m["compliance_pct"].rstrip("%")) < 35
    assert analysis["model_used"].startswith("deterministic-engine")
    assert analysis["analysis_provenance"]["fallback_used"] is True  # rules-only is labelled as such


def test_t005_persistence_in_isolated_db(tender_text):
    init_db()
    ok, msg, user = create_user("t005_tester@tenderforge-audit.internal", "T005#Tester2026", "T005 Tester")
    assert ok, msg
    try:
        tender_id = save_tender("T005.pdf", PDF, tender_text, 67, "success", user["id"])
        analysis = run_deterministic_analysis(tender_text, PROFILE, pdf_path=PDF)
        save_analysis(tender_id=tender_id, profile_hash=compute_profile_hash(PROFILE), recommendation=analysis["recommendation"],
                      executive_summary=analysis["executive_summary"], structured_tender_data=analysis,
                      compliance_matrix=analysis["compliance_matrix"], risks=analysis["risks"],
                      missing_information=analysis["missing_information"], model_used=analysis["model_used"], user_id=user["id"])
        saved = get_analysis_for_tender(tender_id, user_id=user["id"])
        assert saved["recommendation"] == "NO-BID"
        assert saved["structured_tender_data"]["engine_version"] == analysis["engine_version"]
    finally:
        conn = get_connection()
        for table in ("tender_analyses", "tenders", "company_profile"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        conn.commit()
        conn.close()


@pytest.mark.skipif(not tf_test_env.live_groq_enabled(), reason="live Groq test: set TENDERFORGE_LIVE_GROQ=1")
def test_t005_live_groq(tender_text):
    res = analyze_tender_with_groq(tender_text, PROFILE, pdf_path=PDF)
    assert res["success"], res.get("error")
    data = res["data"]
    prov = data["analysis_provenance"]
    print("model:", res["model_used"], "| fallback:", prov["fallback_used"], prov["fallback_reason"])
    assert prov["stage1_model"] and prov["stage2_models"]
    elg4 = next(m for m in data["compliance_matrix"] if m["requirement_id"] == "ELG-04")
    assert elg4["status"] == "CONFIRMED GAP"  # decisive rule result regardless of the AI's view
    assert data["recommendation"] == "NO-BID"
    gaps = [m for m in data["compliance_matrix"] if m["status"] == "CONFIRMED GAP" and m["is_mandatory"]]
    assert all(m["rule_decisive"] for m in gaps), "every blocker must rest on a decisive rule check, not on the AI alone"
    assert data["tender_overview"]["title"] != NOT_FOUND
