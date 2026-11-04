"""Analysis accuracy & data integrity: tender clauses, company identity, bid-decision separation and safe saving.

Isolated database (tf_test_env); Groq mocked at the HTTP or call level; deterministic test embedder.
"""

import tf_test_env  # noqa: F401  (must precede project imports)

import json
import re

import pytest

import agents
import analysis_runner as R
import analyzer as A
import database as db
import report_builder
from pdf_processor import extract_text_from_pdf
import test_reanalysis_evidence as S4

T003, T005 = tf_test_env.sample_pdf("T003.pdf"), tf_test_env.sample_pdf("T005.pdf")


def _text(path):
    return extract_text_from_pdf(path)["text"]


def _page_text(path, page):
    import fitz
    with fitz.open(path) as d:
        return " ".join(d[page - 1].get_text("text").split())


# ------------------------------------------------------------------ tender clauses (EMD, fee, PBG, payment, deadline)

@pytest.mark.parametrize("path,kind,value,page", [
    (T003, "emd", "13,00,000", 4), (T003, "tender_fee", "30,000", 4),
    (T005, "emd", "₹25,00,000/-", 7), (T005, "tender_fee", "₹ 11,800 (inclusive of GST)", 7),
])
def test_fee_clauses_come_from_the_tender_tables(path, kind, value, page):
    fin = A.extract_financial_analysis(_text(path), {}, pdf_path=path)
    clause = fin[kind]
    assert (clause["status"], clause["value"], clause["source_page"], clause["source_method"]) == ("found", value, page, "table")
    assert value.replace(" ", "") in _page_text(path, page).replace(" ", "")  # the value is on the cited page


def test_pbg_payment_and_deadlines_are_cited_verbatim():
    t3, t5 = _text(T003), _text(T005)
    f3, f5 = A.extract_financial_analysis(t3, {}, pdf_path=T003), A.extract_financial_analysis(t5, {}, pdf_path=T005)
    assert f3["pbg"]["source_page"] == 10 and "3%" in f3["pbg"]["value"]
    assert f5["pbg"]["source_page"] == 12 and "@3% of contract/payable value" in f5["pbg"]["value"]
    assert f3["payment_terms"]["source_page"] == 11 and "50%" in f3["payment_terms"]["value"]
    assert f5["payment_terms"]["status"] == "mentioned_without_value" and f5["payment_terms"]["source_page"] == 28
    for clause, path in ((f3["pbg"], T003), (f5["pbg"], T005), (f3["payment_terms"], T003)):
        assert " ".join(clause["value"].split())[:40] in _page_text(path, clause["source_page"])
    d3 = A.extract_risk_assessment(t3, {}, pdf_path=T003)["deadlines"][0]
    d5 = A.extract_risk_assessment(t5, {}, pdf_path=T005)["deadlines"][0]
    assert (d3["date"], d3["source_page"]) == ("22.09.2026 - 05.00 pm", 2)
    assert (d5["date"], d5["source_page"]) == ("17/09/2026, 3:00 PM", 7)


def test_absent_and_unextractable_clauses_are_not_invented():
    text = "--- [Page 1] ---\nSupply of chairs. The bidder shall pay EMD online through the portal.\n--- [Page 2] ---\nDelivery in 30 days."
    emd = A.extract_tender_clause(text, "emd")
    assert emd["status"] == "mentioned_without_value" and emd["source_page"] == 1 and "check the tender" in emd["value"]
    pbg = A.extract_tender_clause(text, "pbg")
    assert (pbg["status"], pbg["value"], pbg["source_page"]) == ("not_specified", A.NOT_SPECIFIED_IN_TENDER, None)
    # A list number next to the keyword is not an amount (previously extracted as 'EMD. 2').
    trap = "--- [Page 1] ---\nPay the document fee and EMD. 2. The bidders who wish to participate must register."
    assert A.extract_tender_clause(trap, "emd")["status"] == "mentioned_without_value"
    # Pages without extractable text: absence cannot be claimed.
    gap = "--- [Page 1] ---\nSupply of chairs.\n--- [Page 3] ---\nDelivery in 30 days."
    assert A.extract_tender_clause(gap, "pbg")["value"] == A.NOT_IN_EXTRACTED_TEXT


def test_turnover_section_does_not_invent_a_threshold():
    fin = A.extract_financial_analysis("--- [Page 1] ---\nSupply of chairs. Delivery in 30 days.", {})
    assert fin["turnover_required"]["status"] == A.NOT_APPLICABLE
    assert fin["turnover_required"]["value"] == A.NOT_SPECIFIED_IN_TENDER


def test_overview_values_carry_their_source_pages():
    text = _text(T005)
    ov = A.extract_tender_overview_deterministic(text, pdf_path=T005)
    pages = A.overview_sources(text, ov)
    assert pages["reference_number"] and ov["reference_number"].split("/")[0] in _page_text(T005, pages["reference_number"])


# ------------------------------------------------------------------ Groq: truncation and rate limits

class _Resp:
    def __init__(self, status, content=None, finish="stop", retry_after=None):
        self.status_code, self.headers = status, ({"retry-after": str(retry_after)} if retry_after is not None else {})
        self._body = {"choices": [{"message": {"content": content or "{}"}, "finish_reason": finish}]}
        self.text = json.dumps(self._body) if status == 200 else '{"error":{"message":"rate limit"}}'

    def json(self):
        return self._body if self.status_code == 200 else {"error": {"message": "rate limit"}}


@pytest.fixture
def groq_http(monkeypatch):
    monkeypatch.setattr(A, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(A, "GROQ_MODEL", "m1")
    monkeypatch.setattr(A, "GROQ_FALLBACK_MODELS", ["m1", "m2"])
    sleeps = []
    monkeypatch.setattr(A.time, "sleep", lambda s: sleeps.append(s))

    def install(responses):
        queue = list(responses)
        calls = []

        def post(url, headers=None, json=None, timeout=None):  # noqa: A002
            calls.append(json["model"])
            return queue.pop(0)
        monkeypatch.setattr(A.requests, "post", post)
        return calls
    return install, sleeps


def test_truncated_response_is_rejected_and_next_model_used(groq_http):
    install, _ = groq_http
    calls = install([_Resp(200, '{"requirements": [{"requirement_id": "E-01"', finish="length"),
                     _Resp(200, '{"requirements": []}')])
    parsed, model = A.call_groq_modular([{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])
    assert (parsed, model, calls) == ({"requirements": []}, "m2", ["m1", "m2"])


def test_truncated_on_every_model_fails_instead_of_repairing(groq_http):
    install, _ = groq_http
    install([_Resp(200, '{"a": [1, 2', finish="length"), _Resp(200, '{"a": [1', finish="length")])
    with pytest.raises(RuntimeError, match="truncated"):
        A.call_groq_modular([{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])


def test_all_models_rate_limited_waits_for_retry_after_once(groq_http):
    install, sleeps = groq_http
    calls = install([_Resp(429, retry_after=7), _Resp(429, retry_after=3), _Resp(200, '{"ok": true}')])
    parsed, model = A.call_groq_modular([{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])
    assert parsed == {"ok": True} and sleeps == [3.0] and calls == ["m1", "m2", "m1"]


def test_rate_limit_wait_is_bounded_and_not_used_for_other_failures(groq_http, monkeypatch):
    install, sleeps = groq_http
    install([_Resp(429, retry_after=120), _Resp(429, retry_after=90)])
    with pytest.raises(RuntimeError, match="429"):
        A.call_groq_modular([{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])
    install([_Resp(429, retry_after=2), _Resp(500)])
    with pytest.raises(RuntimeError):
        A.call_groq_modular([{"role": "system", "content": "x"}, {"role": "user", "content": "y"}])
    assert sleeps == []


# ------------------------------------------------------------------ Agent 3 contract

def test_agent3_asks_for_citations_first_and_rejects_missing_citations(monkeypatch):
    seen = {}

    def fake(messages, **kwargs):
        system = messages[0]["content"]
        if "Agent 3 —" in system:
            seen["schema"] = system[system.index("Return JSON"):]
            return {"proposed_decision": "BID", "report_summary": "Truncated report without citations"}, "fake3"
        return S4.FakeGroq()(messages, **kwargs)
    user = S4._user()
    tender = S4._tender(user)
    monkeypatch.setattr(agents.A, "call_groq_modular", fake)
    outcome = R.run_analysis_for_tender(user, tender, embedder=S4.KeywordEmbedder(), model_name="acc-embedder")
    assert seen["schema"].index("evidence_references") < seen["schema"].index("report_summary")
    assert "reasons" not in seen["schema"]
    assert not outcome["saved"] and "Agent 3" in outcome["error"] and "citations" in outcome["error"]
    assert db.get_analysis_for_tender(tender, user) is None


# ------------------------------------------------------------------ safe saving: coverage warnings vs validation failures

def _with_coverage_gap(monkeypatch):
    real = R.retrieve_requirement_evidence

    def retrieve(*a, **k):
        res = real(*a, **k)
        res["queries_without_matches"] = 1  # a retrieval query found nothing: extraction coverage is uncertain
        return res
    monkeypatch.setattr(R, "retrieve_requirement_evidence", retrieve)


def test_coverage_warnings_are_saved_but_cannot_produce_bid(monkeypatch):
    user = S4._user()
    tender = S4._tender(user)
    S4._verified_document_value(user, S4.TURNOVER_KEY, "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    db.save_company_profile_fields(user, [{"field_key": S4.ISO_KEY, "field_value": "QMS-123", "verification_status": "verified"}])
    _with_coverage_gap(monkeypatch)
    outcome = S4._run(monkeypatch, user, tender)
    assert outcome["saved"]
    data = outcome["data"]
    assert data["analysis_quality"]["status"] == "coverage_warnings"
    assert any("retrieval query" in w for w in data["analysis_quality"]["coverage_warnings"])
    assert data["recommendation"] != "BID"
    assert data["bid_recommendation"]["assessment"]["coverage_incomplete"] is True


@pytest.mark.parametrize("fail_agent", ["agent2", "agent3"])
def test_validation_failures_are_never_saved(monkeypatch, fail_agent):
    user = S4._user()
    tender = S4._tender(user)
    good = S4._run(monkeypatch, user, tender)
    outcome = S4._run(monkeypatch, user, tender, S4.FakeGroq(fail_agent=fail_agent))
    assert not outcome["saved"] and outcome["error"]  # the reason is reported, not an empty message
    assert db.get_analysis_for_tender(tender, user)["id"] == good["analysis_id"]


# ------------------------------------------------------------------ company identity

def _company_check(saved, profile):
    # Same logic as app.analysis_company_check, imported without starting the Streamlit page.
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location("_app_src", os.path.join(tf_test_env.PROJECT_DIR, "app.py"))
    src = open(spec.origin, encoding="utf-8").read()
    fn_src = src[src.index("def analysis_company_check"):src.index("def run_and_save_analysis")]
    scope = {}
    exec(fn_src, scope)
    return scope["analysis_company_check"](saved, profile)


def test_company_snapshot_mismatch_and_isolation(monkeypatch):
    a, b = S4._user(), S4._user()
    db.save_company_profile({"company_name": "Alpha Machining Pvt Ltd"}, a)
    db.save_company_profile({"company_name": "Beta Devices Ltd"}, b)
    ta, tb = S4._tender(a), S4._tender(b)
    ra, rb = S4._run(monkeypatch, a, ta), S4._run(monkeypatch, b, tb)
    assert ra["data"]["company_snapshot"]["company_name"] == "Alpha Machining Pvt Ltd"
    assert rb["data"]["company_snapshot"]["company_name"] == "Beta Devices Ltd"
    saved_a = db.get_analysis_for_tender(ta, a)
    note, ok = _company_check(saved_a, db.get_company_profile(a))
    assert ok and note == "Analysed for: Alpha Machining Pvt Ltd"
    # Profile renamed after the analysis: the mismatch is reported, and the report keeps the analysed company.
    db.save_company_profile({"company_name": "Alpha Renamed Ltd"}, a)
    note, ok = _company_check(saved_a, db.get_company_profile(a))
    assert not ok and "Alpha Machining Pvt Ltd" in note and "Alpha Renamed Ltd" in note
    md = report_builder.build_report(db.get_tender_by_id(ta, a), saved_a, company_name="Alpha Renamed Ltd")["markdown"]
    assert "Bidder (company profile used):** Alpha Machining Pvt Ltd" in md and "Beta Devices" not in md
    # Older analyses without a snapshot are labelled as such, never attributed to the current company.
    legacy = dict(saved_a, structured_tender_data={k: v for k, v in saved_a["structured_tender_data"].items() if k != "company_snapshot"})
    note, ok = _company_check(legacy, db.get_company_profile(a))
    assert not ok and "not recorded" in note
    assert "Not recorded for this analysis" in report_builder.build_report(db.get_tender_by_id(ta, a), legacy)["markdown"]


# ------------------------------------------------------------------ eligibility: specialization is not ineligibility

def test_unrelated_specialization_is_not_a_disqualification():
    machining = {"company_name": "Machining Co", "industry": "CNC machining", "technical_capabilities": "CNC turning, VMC milling",
                 "years_of_experience": "10 years", "annual_turnover": "", "certifications": "", "past_projects": "", "financial_details": ""}
    analysis = A.run_deterministic_analysis(_text(T005), machining, pdf_path=T005)
    assert not [m for m in analysis["compliance_matrix"] if m["status"] == A.CONFIRMED_GAP]
    assert analysis["recommendation"] == "REVIEW REQUIRED"
    assert not any("inelig" in str(m.get("explanation", "")).lower() for m in analysis["compliance_matrix"])


def test_ai_specialization_gap_becomes_business_fit_concern(monkeypatch):
    class SpecializationGroq(S4.FakeGroq):
        def __call__(self, messages, **kwargs):
            system, user = messages[0]["content"], messages[1]["content"]
            if "Agent 2 —" in system:
                payload = json.loads(user.split("VALIDATED AGENT 1 OUTPUT:\n", 1)[1].split("\n\nSAVED COMPANY EVIDENCE", 1)[0])
                return {"evaluations": [{"requirement_id": r["requirement_id"], "status": "CONFIRMED GAP",
                                         "explanation": "The company specialises in machining, unrelated to this tender.",
                                         "company_evidence_ids": [], "company_evidence_quote": "", "risk": "", "risk_evidence_ids": []}
                                        for r in payload]}, "fake-agent2"
            return super().__call__(messages, **kwargs)
    user = S4._user()
    tender = S4._tender(user)
    outcome = S4._run(monkeypatch, user, tender, SpecializationGroq())
    rec = outcome["data"]["bid_recommendation"]
    rows = {r["requirement_id"]: r for r in outcome["data"]["compliance_matrix"]}
    assert outcome["data"]["recommendation"] != "NO-BID" and rec["blockers"] == []
    assert rec["assessment"]["mandatory_failures"] == []
    assert all(r["status"] != A.CONFIRMED_GAP for r in rows.values())
    # Rule-decided requirements keep the rule result (the AI view is recorded); the others become fit concerns.
    assert rows["E-01"]["llm_status"] == A.CONFIRMED_GAP and rows["E-01"]["rule_decisive"]
    flagged = [rid for rid, r in rows.items() if r.get("ai_flagged_gap")]
    assert flagged == ["T-01"]
    assert rec["assessment"]["business_fit_concerns"] == ["T-01: Delivery period (AI-flagged, not a confirmed gap)"]


def test_financial_obligations_are_listed_separately_with_pages(monkeypatch):
    user = S4._user()
    tender = S4._tender(user)
    outcome = S4._run(monkeypatch, user, tender)
    assessment = outcome["data"]["bid_recommendation"]["assessment"]
    assert set(assessment) >= {"mandatory_failures", "missing_information", "unresolved", "technical_suitability",
                               "financial_requirements", "financial_obligations", "business_fit_concerns", "coverage_incomplete"}
    assert all(re.search(r"\(page \d+\)$", e) for e in assessment["financial_obligations"])


def test_ambiguous_flattened_fee_row_is_not_attributed():
    row = ("--- [Page 4] ---\nS.No Description Non-refundable Tender Fees Amount to be paid Online Fee in Rs EMD Amount "
           "to be paid online in Rs 1 Supply of equipment 30,000 13,00,000 INSTRUCTIONS TO TENDERER")
    for kind in ("emd", "tender_fee"):
        clause = A.extract_tender_clause(row, kind)
        assert clause["status"] == "mentioned_without_value" and clause["source_page"] == 4


def test_pipeline_reads_fee_tables_from_the_pdf(monkeypatch):
    """Regression: the pipeline must pass the PDF path, otherwise table values are lost."""
    captured = {}
    real = A.extract_financial_analysis

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)
    monkeypatch.setattr(agents.A, "extract_financial_analysis", spy)
    user = S4._user()
    tender = S4._tender(user)
    assert S4._run(monkeypatch, user, tender)["saved"]
    assert captured.get("pdf_path") == db.get_tender_by_id(tender, user)["filepath"]


def test_rejected_agent1_output_is_saved_only_as_a_coverage_warning(monkeypatch):
    """Agent 1's AI output is rejected (e.g. truncated); its deterministic, cited requirements are still used."""
    user = S4._user()
    tender = S4._tender(user)
    outcome = S4._run(monkeypatch, user, tender, S4.FakeGroq(fail_agent="agent1"))
    assert outcome["saved"]
    quality = outcome["data"]["analysis_quality"]
    assert quality["status"] == "coverage_warnings" and quality["validation_failures"] == []
    assert any("AI requirement extraction (Agent 1) was unavailable" in w for w in quality["coverage_warnings"])
    assert outcome["data"]["recommendation"] != "BID"
    assert all(r["tender_evidence_refs"] for r in outcome["data"]["compliance_matrix"])  # only cited requirements


def test_coverage_warning_run_does_not_replace_a_complete_analysis_of_the_same_evidence(monkeypatch):
    user = S4._user()
    tender = S4._tender(user)
    complete = S4._run(monkeypatch, user, tender)
    assert complete["saved"] and complete["data"]["analysis_quality"]["status"] == "complete"
    kept = S4._run(monkeypatch, user, tender, S4.FakeGroq(fail_agent="agent1"))
    assert not kept["saved"] and kept["stage"] == "kept_previous" and f"#{complete['analysis_id']}" in kept["error"]
    assert db.get_analysis_for_tender(tender, user)["id"] == complete["analysis_id"]
    # With changed company evidence the new (coverage-warning) analysis is saved: it reflects the current profile.
    db.save_company_profile({"company_name": "Changed Co"}, user)
    changed = S4._run(monkeypatch, user, tender, S4.FakeGroq(fail_agent="agent1"))
    assert changed["saved"] and changed["data"]["company_snapshot"]["company_name"] == "Changed Co"
