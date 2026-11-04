"""Regression tests for safe tender selection, response status and evidence ownership."""

import uuid
from pathlib import Path

import pytest

import tf_test_env  # noqa: F401; select isolated test database before importing database
import database as db
from tender_response_utils import (record_analysis_outcome, resolve_tender_id,
                                   response_evidence_status, source_reference_is_valid,
                                   existing_company_pdf_payload,
                                   classify_requirement_type,
                                   requirement_needs_information,
                                   requirement_needs_documents,
                                   filter_requirement_records, requirement_group,
                                   requirement_title, simple_status_label,
                                   information_required_label, documents_required_label)


@pytest.fixture
def two_saved_tenders():
    db.init_db()
    ok, message, user = db.create_user(f"response_{uuid.uuid4().hex}@example.invalid", "TenderTest123!", "Response Test")
    assert ok, message
    t003 = db.save_tender("T003.pdf", "isolated/T003.pdf", "T003 source", 36, "success", user["id"])
    t005 = db.save_tender("T005.pdf", "isolated/T005.pdf", "T005 source", 20, "success", user["id"])
    return user["id"], t003, t005


def test_tender_id_resolution_uses_saved_id_and_safe_fallback(two_saved_tenders):
    _, t003, t005 = two_saved_tenders
    assert resolve_tender_id({"id": t003, "filename": "T003.pdf"}, t003) == t003
    assert resolve_tender_id({"tender_id": t005}, t005) == t005
    assert resolve_tender_id({"filename": "T003.pdf"}, t003) == t003
    assert resolve_tender_id({"filename": "T003.pdf"}) is None
    assert resolve_tender_id({"id": "invalid", "tender_id": t003}, t003) is None
    assert resolve_tender_id({"id": t003}, t005) is None
    assert resolve_tender_id({"id": t003, "tender_id": t005}) is None
    assert resolve_tender_id({"id": -1}, t003) is None


def test_section2_business_titles_groups_and_status_mapping():
    turnover = {"requirement": "ELG-04 Minimum Average Annual Turnover", "category": "Financial"}
    experience = {"requirement": "Similar completed project experience", "category": "Experience"}
    blacklist = {"requirement": "Confirm the bidder is not blacklisted", "category": "Declaration"}
    gst = {"requirement": "GST registration certificate", "category": "Registration"}
    assert requirement_title(turnover) == "Annual Turnover"
    assert requirement_title(experience) == "Past Experience"
    assert requirement_group(turnover) == "Financial Requirements"
    assert requirement_group(experience) == "Previous Project Experience"
    assert requirement_group(blacklist) == "Declarations & Other Conditions"
    assert requirement_group(gst) == "Registrations & Certifications"
    assert simple_status_label("Information provided", True, False)[0] == "Information Needed"
    assert simple_status_label("Evidence uploaded", False, True)[0] == "Under Review"
    assert simple_status_label("Verified")[0] == "Verified"


def test_section2_action_and_document_filters():
    records = [
        {"status": "Not provided", "needs_documents": False},
        {"status": "Evidence uploaded", "needs_documents": True},
        {"status": "Verified", "needs_documents": False},
    ]
    assert len(filter_requirement_records(records, "Action Required")) == 2
    assert len(filter_requirement_records(records, "Documents Pending")) == 1
    assert len(filter_requirement_records(records, "Verified")) == 1


def test_t003_and_t005_answers_and_pdfs_stay_tender_scoped(two_saved_tenders):
    user_id, t003, t005 = two_saved_tenders
    clause = "Submit the applicable audited statement."
    payload = b"synthetic PDF test bytes"
    for tender_id, answer in ((t003, "T003 answer"), (t005, "T005 answer")):
        db.save_tender_requirement_response(
            user_id, tender_id, "FIN-01", clause, {"details": answer},
            {"filename": f"{answer}.pdf", "content": payload + answer.encode(), "media_type": "application/pdf"},
        )
    assert db.get_tender_requirement_responses(user_id, t003)["FIN-01"]["response"]["details"] == "T003 answer"
    assert db.get_tender_requirement_responses(user_id, t005)["FIN-01"]["response"]["details"] == "T005 answer"
    assert db.get_tender_requirement_responses(user_id, t003)["FIN-01"]["documents"][0]["verification_status"] == "unverified"
    assert db.get_tender_requirement_responses(user_id, t005)["FIN-01"]["documents"][0]["verification_status"] == "unverified"


def test_invalid_and_unvalidated_citations_need_review():
    requirement = {"source_verified": True, "source_page": 33,
                  "source_quotation": "Submit the audited table", "status": "INFORMATION REQUIRED"}
    assert source_reference_is_valid(requirement)
    assert not source_reference_is_valid({**requirement, "source_verified": False})
    assert not source_reference_is_valid({**requirement, "source_page": None})
    assert not source_reference_is_valid({**requirement, "retrieval_grounded": False})
    assert not source_reference_is_valid({**requirement,
        "tender_evidence_refs": [{"page_number": 34, "chunk_id": "p34"}]})
    assert response_evidence_status({**requirement, "source_verified": False}, {"response": {"details": "filled"}}) == "Needs review"


def test_requirement_type_filters_include_information_and_document_requirements():
    info = {"requirement": "State the delivery period"}
    doc = {"requirement": "Submit audited accounts", "document_checklist_item": True}
    both = {"requirement": "Provide turnover by year with audited support", "required_document": "Audited accounts"}
    rows = []
    for key, requirement in (("info", info), ("doc", doc), ("both", both)):
        guidance = {"information": ["Enter the requested details."] if key != "doc" else [],
                    "explicit_document": key in ("doc", "both")}
        rows.append({"key": key, "status": "Needs review", "needs_information": requirement_needs_information(requirement, guidance),
                     "needs_documents": requirement_needs_documents(requirement, guidance),
                     "requirement_type": classify_requirement_type(requirement, guidance)})

    assert [row["key"] for row in filter_requirement_records(rows, "Information Required")] == ["info", "both"]
    assert [row["key"] for row in filter_requirement_records(rows, "Documents Required")] == ["doc", "both"]
    assert [row["key"] for row in filter_requirement_records(rows, "Needs Review")] == ["info", "doc", "both"]


def test_requirement_type_is_independent_of_review_status_and_citation():
    req = {"requirement": "State the delivery period", "status": "SATISFIED", "source_verified": False}
    guidance = {"information": ["Enter the delivery period."], "explicit_document": False}
    kind = classify_requirement_type(req, guidance)
    assert kind == "information_required"
    records = [{"key": "delivery", "status": "Needs review",
                "needs_information": requirement_needs_information(req, guidance),
                "needs_documents": requirement_needs_documents(req, guidance)}]
    assert [row["key"] for row in filter_requirement_records(records, "Information Required")] == ["delivery"]
    assert filter_requirement_records(records, "Verified") == []
    assert [row["key"] for row in filter_requirement_records(records, "Needs Review")] == ["delivery"]


def test_entered_values_and_uploaded_documents_are_not_verified_without_review():
    requirement = {"source_verified": True, "source_page": 3, "source_quotation": "Provide ISO certificate",
                   "status": "INFORMATION REQUIRED"}
    from hashlib import sha256
    quote_hash = sha256(b"Provide ISO certificate").hexdigest()
    assert response_evidence_status(requirement, {"source_quote_sha256": quote_hash,
        "response": {"details": "ISO 9001"}, "documents": []}) == "Information provided"
    assert response_evidence_status(requirement, {"source_quote_sha256": quote_hash,
        "response": {}, "documents": [{"verification_status": "unverified"}]}) == "Evidence uploaded"
    assert response_evidence_status(requirement, {"source_quote_sha256": quote_hash,
        "response": {}, "documents": [{"verification_status": "verified"}]}) == "Verified"


def test_background_analysis_success_and_failure_clear_progress_state():
    from datetime import datetime
    success = {"analysis_job_11": object(), "last_failed_run_11": {"stage": "old"}}
    record_analysis_outcome(success, 11, {"saved": True, "analysis_id": 4})
    assert "analysis_job_11" not in success and "last_failed_run_11" not in success
    assert success["analysis_flash_11"] == "Analysis #4 completed and saved."

    failure = {"analysis_job_12": object()}
    record_analysis_outcome(failure, 12, {"saved": False, "stage": "groq", "error": "timeout"},
                            datetime(2026, 11, 2, 12, 30))
    assert "analysis_job_12" not in failure
    assert failure["last_failed_run_12"] == {"at": "2026-11-02 12:30", "stage": "groq", "error": "timeout"}

    kept = {"analysis_job_13": object()}
    record_analysis_outcome(kept, 13, {"saved": False, "stage": "kept_previous", "error": "Prior result retained"})
    assert "analysis_job_13" not in kept and kept["analysis_flash_13"] == "Prior result retained"


def test_existing_company_pdf_can_be_reused_without_verification_transfer(tmp_path, two_saved_tenders):
    user_id, t003, _ = two_saved_tenders
    path = tmp_path / "certificate.pdf"
    path.write_bytes(b"company PDF evidence")
    documents = [{"id": 7, "filename": "certificate.pdf", "storage_path": str(path),
                  "media_type": "application/pdf", "verification_status": "verified"}]
    payload = existing_company_pdf_payload(documents, 7)
    assert payload == {"filename": "certificate.pdf", "content": b"company PDF evidence",
                       "media_type": "application/pdf"}
    saved = db.save_tender_requirement_response(user_id, t003, "DOC-1", "Submit a certificate.", {}, payload)
    response = db.get_tender_requirement_responses(user_id, t003)["DOC-1"]
    assert saved["verification_status"] == "unverified"
    assert response["documents"][0]["verification_status"] == "unverified"
    with pytest.raises(PermissionError):
        existing_company_pdf_payload([], 7)


def test_section2_requirement_cards_do_not_nest_expanders():
    source = (Path(__file__).with_name("app.py")).read_text(encoding="utf-8")
    assert "with st.container(border=True):" in source
    assert "with st.popover(\"View original tender clause\"" in source
    reusable_start = source.index("def _render_reusable_requirement_card")
    reusable_end = source.index("\n\ndef _response_evidence_state", reusable_start)
    reusable = source[reusable_start:reusable_end]
    tender_start = source.index("def _render_tender_specific_response")
    tender_end = source.index("\n\ndef _get_profile_field_sync", tender_start)
    tender = source[tender_start:tender_end]
    assert "with st.expander(" not in reusable
    assert "with st.expander(" not in tender


def test_section2_information_and_document_labels():
    turnover_req = {"requirement": "ELG-04 Minimum Average Annual Turnover of INR 50 Lakh", "category": "Financial"}
    info_turnover = information_required_label(turnover_req)
    doc_turnover, badge_turnover, is_mand_turnover = documents_required_label(turnover_req)
    assert "turnover" in info_turnover.lower()
    assert "financial year" in info_turnover.lower()
    assert "audited" in doc_turnover.lower()
    assert badge_turnover in ("Mandatory", "Optional")

    iso_req = {"requirement": "Valid ISO 9001 certification", "category": "Certifications"}
    info_iso = information_required_label(iso_req, definition={"field_key": "certification.iso_9001"})
    doc_iso, badge_iso, is_mand_iso = documents_required_label(iso_req, definition={"field_key": "certification.iso_9001"})
    assert "certificate number" in info_iso.lower()
    assert "validity" in info_iso.lower()
    assert "certificate" in doc_iso.lower()

    checklist_req = {"requirement": "Copy of GST Registration", "document_checklist_item": True, "required_document": "GST Registration Certificate"}
    info_check = information_required_label(checklist_req)
    doc_check, badge_check, is_mand_check = documents_required_label(checklist_req)
    assert "upload the required document" in info_check.lower()
    assert doc_check == "GST Registration Certificate"
    assert badge_check == "Mandatory"
    assert is_mand_check is True

    decl_req = {"requirement": "Delivery within 30 days of PO", "category": "Technical", "is_mandatory": False}
    doc_decl, badge_decl, is_mand_decl = documents_required_label(decl_req)
    assert badge_decl == "None"
    assert is_mand_decl is False


def test_section2_redesigned_ui_contract():
    source = (Path(__file__).with_name("app.py")).read_text(encoding="utf-8")
    assert "## Section 2 – Tender-Specific Requirements" in source
    assert "**Information Required:**" in source
    assert "**Documents Required:**" in source
    assert "tf-badge-mandatory" in source
    assert "tf-badge-optional" in source
    assert "View original tender clause" in source
