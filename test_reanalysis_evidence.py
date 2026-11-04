"""Section 4: re-analysis uses the latest saved company evidence under the verification rules.

End-to-end through analysis_runner.run_analysis_for_tender: real PDF extraction, local RAG indexing and
retrieval (deterministic test embedder), the three agent contracts (Groq mocked) and SQLite persistence,
all in tf_test_env's isolated data directory.
"""

import tf_test_env  # noqa: F401  (must precede project imports)

import json
import os
import re
import uuid

import fitz
import pytest

import agents
import analysis_runner as R
import analyzer as A
import database as db
import profile_fields as PF
from config import UPLOAD_DIR
from pdf_processor import extract_text_from_pdf

TENDER_LINES = [
    "REQUEST FOR PROPOSAL - Supply of Machined Components",
    "Tender No: TF-S4-001",
    "E-01 - Turnover",
    "Average annual turnover must be at least INR 50 lakh over the last two completed financial years. Submit audited accounts.",
    "E-02 - Quality certification",
    "Bidder must hold a valid ISO 9001 certificate on the bid date. Submit the certificate.",
    "T-01 - Delivery period",
    "Supplier must deliver all components within ten weeks of the purchase order.",
]
TURNOVER_KEY, ISO_KEY = "financial.annual_turnover_by_year", "certification.iso_9001"


class KeywordEmbedder:
    """Deterministic offline embedder (same idea as the RAG pipeline tests)."""

    def encode(self, texts, **kwargs):
        groups = [("turnover", "financial", "inr", "lakh"), ("certificate", "iso", "quality"),
                  ("deliver", "delivery", "weeks"), ("submit", "document", "audited"),
                  ("bidder", "tender", "supply", "components", "technical", "product", "compliance")]
        out = []
        for text in texts:
            value = str(text).lower()
            out.append([float(any(t in value for t in g)) for g in groups] + [0.1])  # never a zero vector
        return out


class FakeGroq:
    """Agent 1 proposes nothing; Agent 2 evaluates as configured; Agent 3 cites a real retrieved chunk."""

    def __init__(self, agent2_mode="information_required", fail_agent=None):
        self.agent2_mode, self.fail_agent, self.calls = agent2_mode, fail_agent, []

    def __call__(self, messages, **kwargs):
        system, user = messages[0]["content"], messages[1]["content"]
        agent = "agent1" if "Agent 1 —" in system else "agent2" if "Agent 2 —" in system else "agent3"
        self.calls.append(agent)
        if agent == self.fail_agent:
            raise RuntimeError(f"simulated Groq failure in {agent}")
        if agent == "agent1":
            return {"requirements": []}, "fake-agent1"
        if agent == "agent2":
            payload = json.loads(user.split("VALIDATED AGENT 1 OUTPUT:\n", 1)[1].split("\n\nSAVED COMPANY EVIDENCE", 1)[0])
            evidence = json.loads(user.split("SAVED COMPANY EVIDENCE (with source and verification status):\n", 1)[1].split("\n\nBatch", 1)[0])
            by_id = {e["evidence_id"]: e for e in evidence}
            evals = []
            for r in payload:
                ids = r["relevant_company_evidence_ids"]
                if self.agent2_mode == "claims_satisfied" and ids:
                    # Adversarial AI: claims compliance from whatever saved value is relevant (verified or not).
                    evals.append({"requirement_id": r["requirement_id"], "status": "SATISFIED", "explanation": "AI says met.",
                                  "company_evidence_ids": ids, "company_evidence_quote": by_id[ids[0]]["value_text"][:40],
                                  "risk": "", "risk_evidence_ids": []})
                else:
                    evals.append({"requirement_id": r["requirement_id"], "status": "INFORMATION REQUIRED",
                                  "explanation": "Evidence needs confirmation.", "company_evidence_ids": [],
                                  "company_evidence_quote": "", "risk": "", "risk_evidence_ids": []})
            return {"evaluations": evals}, "fake-agent2"
        chunk_ids = re.findall(r'"chunk_id":\s*"(p\d+-[0-9a-f]+)"', user)
        return {"proposed_decision": "BID", "report_summary": "Report.", "reasons": [], "blockers": [], "next_actions": [],
                "evidence_references": chunk_ids[:1]}, "fake-agent3"


def _user():
    db.init_db()
    ok, msg, user = db.create_user(f"s4_{uuid.uuid4().hex}@example.invalid", "Section4Pass123!", "S4")
    assert ok, msg
    return user["id"]


def _tender(user_id):
    path = os.path.join(UPLOAD_DIR, f"s4_{uuid.uuid4().hex}.pdf")
    doc = fitz.open()
    page = doc.new_page()
    y = 60
    for line in TENDER_LINES:
        page.insert_text((40, y), line, fontsize=9)
        y += 18
    doc.save(path)
    doc.close()
    ext = extract_text_from_pdf(path)
    return db.save_tender(os.path.basename(path), path, ext["text"], ext["page_count"], "success", user_id)


def _run(monkeypatch, user_id, tender_id, groq=None):
    monkeypatch.setattr(agents.A, "call_groq_modular", groq or FakeGroq())
    return R.run_analysis_for_tender(user_id, tender_id, embedder=KeywordEmbedder(), model_name="s4-test-embedder")


def _rows(outcome):
    assert outcome["saved"], outcome
    return {r["requirement_id"]: r for r in outcome["data"]["compliance_matrix"]}


def _definition(key):
    return {"field_key": key, "storage": "fixed:annual_turnover" if key == TURNOVER_KEY else "dynamic",
            "document_key": f"field:{key}"}


def _tender_refs(rows):
    return {rid: r["tender_evidence_refs"] for rid, r in rows.items()}


# ------------------------------------------------------------------ the next analysis sees newly saved evidence

def test_newly_saved_fixed_and_dynamic_values_reach_the_next_analysis(monkeypatch):
    user = _user()
    tender = _tender(user)
    first = _run(monkeypatch, user, tender)
    rows1 = _rows(first)
    assert {"E-01", "E-02", "T-01"} <= set(rows1)
    assert rows1["E-01"]["candidate_company_evidence"] == []
    assert rows1["E-01"]["evidence_confirmation_needed"] == "No saved company information matches this requirement."

    PF.save_field_value(user, _definition(TURNOVER_KEY), "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")  # fixed column
    PF.save_field_value(user, _definition(ISO_KEY), "QMS-123, TUV, valid to 2027")                          # dynamic field
    second = _run(monkeypatch, user, tender)
    rows2 = _rows(second)
    assert second["analysis_id"] != first["analysis_id"]
    assert second["index"]["idempotent"] is True and second["index"]["indexed"] is False  # company change: no reindex
    e1 = {c["evidence_id"]: c for c in rows2["E-01"]["candidate_company_evidence"]}
    e2 = {c["evidence_id"]: c for c in rows2["E-02"]["candidate_company_evidence"]}
    assert e1["profile:annual_turnover"]["value_text"].startswith("FY2023-24") and e1["profile:annual_turnover"]["verification_status"] == "unverified"
    assert e2[f"profile_field:{ISO_KEY}"]["value_text"] == "QMS-123, TUV, valid to 2027"
    # Unverified saved values are candidates only: nothing became compliant, and confirmation is requested explicitly.
    assert rows2["E-01"]["status"] == A.INFORMATION_REQUIRED and rows2["E-02"]["status"] == A.INFORMATION_REQUIRED
    assert "must be verified" in rows2["E-02"]["evidence_confirmation_needed"]
    assert "Saved company information:" in rows2["E-02"]["explanation"]
    assert second["data"]["recommendation"] != "BID"
    assert second["data"]["company_evidence_snapshot"]["profile_hash"] == db.compute_profile_hash(db.get_company_profile(user))
    assert db.get_analysis_for_tender(tender, user)["profile_hash"] == second["profile_hash"] != first["profile_hash"]
    assert _tender_refs(rows2) == _tender_refs(rows1)  # tender citations unchanged by company changes


def test_evidence_is_matched_only_to_relevant_requirements(monkeypatch):
    user = _user()
    tender = _tender(user)
    PF.save_field_value(user, _definition(TURNOVER_KEY), "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    PF.save_field_value(user, _definition(ISO_KEY), "QMS-123")
    db.save_company_profile_fields(user, [{"field_key": "capability.service_support", "field_value": "3 service centres"}])
    rows = _rows(_run(monkeypatch, user, tender))
    ids = {rid: {c["evidence_id"] for c in r["candidate_company_evidence"]} for rid, r in rows.items()}
    assert ids["E-01"] == {"profile:annual_turnover"}
    assert ids["E-02"] == {f"profile_field:{ISO_KEY}"}
    assert ids["T-01"] == set()  # a delivery term for this tender: no saved company field is relevant


def test_unverified_values_cannot_satisfy_even_if_the_ai_claims_so(monkeypatch):
    user = _user()
    tender = _tender(user)
    PF.save_field_value(user, _definition(TURNOVER_KEY), "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    PF.save_field_value(user, _definition(ISO_KEY), "ISO 9001:2015 QMS-123 valid to 2027")
    outcome = _run(monkeypatch, user, tender, FakeGroq("claims_satisfied"))
    rows = _rows(outcome)
    for rid in ("E-01", "E-02"):
        assert rows[rid]["status"] != A.SATISFIED and rows[rid]["company_evidence_refs"] == []
    assert outcome["data"]["recommendation"] != "BID"


# ------------------------------------------------------------------ verified evidence (existing rules only)

def _verified_document_value(user, key, value, filename="evidence.pdf", content=b"%PDF-1.4 synthetic evidence"):
    doc_id = db.save_company_document(user, filename, content, document_key=f"field:{key}")
    db.save_company_document_values(user, doc_id, [{"value_key": key, "value_text": value, "source_excerpt": value,
                                                    "source_page": 2, "verification_status": "verified"}])
    return doc_id


def test_verified_evidence_is_accepted_through_existing_rules(monkeypatch):
    user = _user()
    tender = _tender(user)
    doc_id = _verified_document_value(user, TURNOVER_KEY, "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    db.save_company_profile_fields(user, [{"field_key": ISO_KEY, "field_value": "QMS-123 valid to 2027",
                                           "verification_status": "verified"}])
    rows = _rows(_run(monkeypatch, user, tender))
    e1, e2 = rows["E-01"], rows["E-02"]
    assert e1["status"] == A.SATISFIED and e1["rule_decisive"]          # deterministic average >= INR 50 lakh
    prov = e1["company_evidence_provenance"][0]
    assert (prov["document_id"], prov["document_version"], prov["verification_status"]) == (doc_id, 1, "verified")
    assert e2["status"] == A.SATISFIED and e2["company_evidence_refs"] == [f"profile_field:{ISO_KEY}"]
    assert e1["evidence_confirmation_needed"] == "" and e2["evidence_confirmation_needed"] == ""


def test_verified_figure_below_threshold_is_a_confirmed_gap(monkeypatch):
    user = _user()
    tender = _tender(user)
    _verified_document_value(user, TURNOVER_KEY, "FY2023-24: INR 20 Lakh; FY2024-25: INR 25 Lakh")
    outcome = _run(monkeypatch, user, tender)
    rows = _rows(outcome)
    assert rows["E-01"]["status"] == A.CONFIRMED_GAP
    assert outcome["data"]["recommendation"] == "NO-BID"


def test_uploaded_but_unprocessed_document_is_not_proof(monkeypatch):
    user = _user()
    tender = _tender(user)
    doc_id = PF.save_field_value(user, _definition(ISO_KEY), "",
                                 {"filename": "iso.pdf", "content": b"%PDF-1.4 iso certificate"})["document_id"]
    db.update_company_document_status(user, doc_id, "verified")  # even a reviewed file proves nothing without extracted values
    rows = _rows(_run(monkeypatch, user, tender))
    e2 = rows["E-02"]
    assert e2["status"] == A.INFORMATION_REQUIRED
    assert e2["unprocessed_company_documents"][0]["document_id"] == doc_id
    assert e2["unprocessed_company_documents"][0]["content_extracted"] is False
    assert "contents have not been extracted" in e2["evidence_confirmation_needed"]


def test_document_versions_and_status_changes_are_reflected(monkeypatch):
    user = _user()
    tender = _tender(user)
    _verified_document_value(user, TURNOVER_KEY, "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh", content=b"%PDF v1")
    run1 = _rows(_run(monkeypatch, user, tender))
    assert run1["E-01"]["status"] == A.SATISFIED

    # A newer version without extracted values replaces v1: v1's values are no longer current evidence.
    v2 = db.save_company_document(user, "evidence.pdf", b"%PDF v2", document_key=f"field:{TURNOVER_KEY}")
    run2 = _rows(_run(monkeypatch, user, tender))
    assert run2["E-01"]["status"] != A.SATISFIED
    assert run2["E-01"]["unprocessed_company_documents"][0]["document_version"] == 2

    # Verified values on v2, then the v2 document is rejected: its values must not count.
    db.save_company_document_values(user, v2, [{"value_key": TURNOVER_KEY, "value_text": "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh",
                                                "verification_status": "verified"}])
    assert _rows(_run(monkeypatch, user, tender))["E-01"]["status"] == A.SATISFIED
    db.update_company_document_status(user, v2, "rejected")
    run4 = _rows(_run(monkeypatch, user, tender))
    assert run4["E-01"]["status"] != A.SATISFIED and run4["E-01"]["company_evidence_refs"] == []
    assert _tender_refs(run4) == _tender_refs(run1)


def test_fresh_evidence_replaces_stale_evidence(monkeypatch):
    user = _user()
    tender = _tender(user)
    PF.save_field_value(user, _definition(TURNOVER_KEY), "FY2023-24: INR 10 Lakh; FY2024-25: INR 12 Lakh")
    first = _run(monkeypatch, user, tender)
    PF.save_field_value(user, _definition(TURNOVER_KEY), "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    second = _run(monkeypatch, user, tender)
    old, new = _rows(first)["E-01"], _rows(second)["E-01"]
    assert [c["value_text"] for c in new["candidate_company_evidence"]] == ["FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh"]
    assert old["candidate_company_evidence"][0]["value_text"].startswith("FY2023-24: INR 10 Lakh")
    assert new["tender_evidence_refs"] == old["tender_evidence_refs"]
    assert first["profile_hash"] != second["profile_hash"]


# ------------------------------------------------------------------ isolation and failure handling

def test_user_isolation(monkeypatch):
    a, b = _user(), _user()
    tender_a = _tender(a)
    _verified_document_value(b, TURNOVER_KEY, "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    db.save_company_profile_fields(b, [{"field_key": ISO_KEY, "field_value": "B's certificate", "verification_status": "verified"}])
    outcome = _run(monkeypatch, a, tender_a)
    rows = _rows(outcome)
    assert all(r["candidate_company_evidence"] == [] and r["company_evidence_refs"] == [] for r in rows.values())
    assert outcome["data"]["company_evidence_snapshot"]["facts"] == []
    denied = R.run_analysis_for_tender(b, tender_a, embedder=KeywordEmbedder(), model_name="s4-test-embedder")
    assert not denied["saved"] and denied["error_type"] == "tender_not_found"


@pytest.mark.parametrize("failure", ["agent3", "agent2", "retrieval", "save"])
def test_failed_reanalysis_preserves_previous_analysis(monkeypatch, failure):
    user = _user()
    tender = _tender(user)
    good = _run(monkeypatch, user, tender)
    assert good["saved"]
    PF.save_field_value(user, _definition(ISO_KEY), "QMS-123")  # profile changed before the failing run
    if failure == "retrieval":
        monkeypatch.setattr(R, "retrieve_requirement_evidence", lambda *a, **k: {"success": False, "error_type": "simulated", "error": "down"})
        outcome = _run(monkeypatch, user, tender)
    elif failure == "save":
        monkeypatch.setattr(R.db, "save_analysis", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
        outcome = _run(monkeypatch, user, tender)
    else:
        outcome = _run(monkeypatch, user, tender, FakeGroq(fail_agent=failure))
    assert outcome["saved"] is False
    assert outcome["stage"] == {"agent3": "agents", "agent2": "agents", "retrieval": "retrieval", "save": "save"}[failure]
    latest = db.get_analysis_for_tender(tender, user)
    assert latest["id"] == good["analysis_id"] and latest["profile_hash"] == good["profile_hash"]
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM tender_analyses WHERE tender_id = ?", (tender,)).fetchone()[0] == 1
    conn.close()


# ------------------------------------------------------------------ controlled live-Groq integration (opt-in)

@pytest.mark.skipif(not tf_test_env.live_groq_enabled(), reason="live Groq test: set TENDERFORGE_LIVE_GROQ=1")
def test_live_groq_reanalysis_respects_verification():
    """Real Groq agents; synthetic tender and profile; deterministic test embedder; isolated database."""
    user = _user()
    tender = _tender(user)
    _verified_document_value(user, TURNOVER_KEY, "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh")
    PF.save_field_value(user, _definition(ISO_KEY), "ISO 9001:2015 QMS-123 valid to 2027")  # unverified
    outcome = R.run_analysis_for_tender(user, tender, embedder=KeywordEmbedder(), model_name="s4-test-embedder")
    print("live outcome:", outcome["stage"], outcome.get("error_type"), (outcome.get("error") or "")[:300],
          "| model:", (outcome.get("result") or {}).get("model_used"))
    if not outcome["saved"]:
        assert db.get_analysis_for_tender(tender, user) is None  # a failed/partial live run saved nothing
        pytest.skip(f"live pipeline did not complete ({outcome['stage']}): {str(outcome.get('error'))[:200]}")
    rows = _rows(outcome)
    print({rid: (r["status"], r.get("llm_status")) for rid, r in rows.items()})
    assert rows["E-01"]["status"] == A.SATISFIED
    assert rows["E-02"]["status"] != A.SATISFIED
    assert outcome["data"]["recommendation"] != "BID"
