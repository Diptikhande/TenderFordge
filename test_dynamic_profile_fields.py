"""Section 3: dynamic company-profile fields generated from validated tender requirements.

All data uses tf_test_env's isolated temporary database; Groq is mocked.
"""

import tf_test_env  # noqa: F401  (must precede project imports)

import os
import uuid

import pytest

import agents
import analyzer as A
import database as db
import profile_fields as PF

PAGE = """FIN-01 Average annual turnover must be at least INR 50 lakh over the last three completed financial years.
CERT-01 Bidder must hold a valid ISO 9001:2015 certificate on the bid date.
ELIG-01 Bidder must have at least five years in business.
Supplier must deliver all listed components within ten weeks of purchase order.
If applicable, bidder must submit GST registration certificate.
"""
TENDER_TEXT = f"--- [Page 2] ---\n{PAGE}"
CHUNKS = [{"chunk_id": "p2-chunk-001", "page_number": 2, "start_char": 0, "end_char": len(PAGE), "text": PAGE, "score": 0.9}]
RAG = {"success": True, "status": "retrieved", "embedding_model": "local-test-model", "source_sha256": "s", "query_count": 3}


def _user():
    db.init_db()
    ok, msg, user = db.create_user(f"s3_{uuid.uuid4().hex}@example.invalid", "Section3Pass123!", "S3")
    assert ok, msg
    return user["id"]


def _custom_requirements():
    def r(rid, cat, text, doc):
        return {"requirement_id": rid, "category": cat, "requirement": text, "is_mandatory": True, "source_page": 2,
                "source_quotation": text, "required_document": doc}
    return [r("FIN-01", "Financial", "Average annual turnover must be at least INR 50 lakh over the last three completed financial years.", "audited accounts"),
            r("CERT-01", "Certifications", "Bidder must hold a valid ISO 9001:2015 certificate on the bid date.", "the certificate"),
            r("ELIG-01", "Eligibility", "Bidder must have at least five years in business.", "")]


def _fake_groq(messages, **kwargs):
    system = messages[0]["content"]
    if "Agent 1 — Requirement Extraction" in system:
        return {"requirements": [
            {"requirement_id": "T-01", "category": "Technical", "requirement": "Deliver components within ten weeks.",
             "is_mandatory": True, "source_page": 2, "source_chunk_id": "p2-chunk-001",
             "source_quotation": "Supplier must deliver all listed components within ten weeks of purchase order."},
            {"requirement_id": "DOC-01", "category": "Documents", "requirement": "Submit GST registration certificate if applicable.",
             "is_mandatory": True, "source_page": 2, "source_chunk_id": "p2-chunk-001",
             "source_quotation": "If applicable, bidder must submit GST registration certificate.",
             "required_document": "GST registration certificate"},
        ]}, "agent1-test"
    if "Agent 2 — Compliance & Risk Analysis" in system:
        return {"evaluations": []}, "agent2-test"
    return {"proposed_decision": "REVIEW REQUIRED", "report_summary": "Information is missing.", "reasons": [],
            "blockers": [], "next_actions": [], "evidence_references": ["p2-chunk-001"]}, "agent3-test"


@pytest.fixture
def pipeline_matrix(monkeypatch):
    monkeypatch.setattr(agents.A, "call_groq_modular", _fake_groq)
    result = agents.run_three_agent_pipeline(TENDER_TEXT, {"company_name": "Empty Co"}, CHUNKS, RAG,
                                             custom_requirements=_custom_requirements())
    assert result.get("result_available")
    return result["data"]["compliance_matrix"]


def _item(rid, text, status="INFORMATION REQUIRED", validated=True, **extra):
    item = {"requirement_id": rid, "requirement": text, "source_quotation": text, "status": status,
            "is_mandatory": True, "source_page": 3, "category": extra.pop("category", "Eligibility"),
            "requirement_scope": extra.pop("scope", "company"), **extra}
    if validated:
        item.update(agent1_validation="validated", source_verified=True, retrieval_grounded=True)
    return item


def _save_analysis(user_id, tender_id, matrix):
    return db.save_analysis(tender_id=tender_id, profile_hash="h", recommendation="REVIEW REQUIRED", executive_summary="",
                            structured_tender_data={"compliance_matrix": matrix}, compliance_matrix=matrix, risks=[],
                            missing_information=[], model_used="test", user_id=user_id)


def _tender(user_id, name="T.pdf"):
    return db.save_tender(name, f"/nonexistent/{name}", TENDER_TEXT, 1, "success", user_id)


# ------------------------------------------------------------------ field generation

def test_fields_generated_from_real_agent_pipeline_output(pipeline_matrix):
    plan = PF.build_field_plan(pipeline_matrix)
    defs = {d["field_key"]: d for d in plan["definitions"]}
    assert set(defs) == {"financial.annual_turnover_by_year", "certification.iso_9001",
                         "experience.years_in_business", "registration.gst"}
    turnover = defs["financial.annual_turnover_by_year"]
    assert turnover["input_type"] == "money_period" and turnover["storage"] == "fixed:annual_turnover"
    assert turnover["document_key"] and "turnover" in turnover["document_label"].lower()
    assert defs["certification.iso_9001"]["input_type"] == "certificate"
    assert defs["certification.iso_9001"]["document_key"] == "field:certification.iso_9001"
    assert defs["experience.years_in_business"]["input_type"] == "years"
    links = {l["requirement_id"]: l for l in plan["links"]}
    assert links["FIN-01"]["field_key"] == "financial.annual_turnover_by_year"
    assert "INR 50 lakh" in links["FIN-01"]["explanation"] and "3 financial years" in links["FIN-01"]["explanation"]
    assert all(l["is_mandatory"] and l["source_page"] == 2 and l["source_quotation"] for l in plan["links"])
    # The delivery-time requirement is specific to this tender: it is not turned into a permanent field.
    assert [r["requirement_id"] for r in plan["tender_specific"]] == ["LLM-T-01"]


def test_unvalidated_requirements_are_reported_as_uncertain():
    plan = PF.build_field_plan([_item("E-1", "Annual turnover of at least INR 50 lakh.", validated=False)])
    assert plan["definitions"] == [] and plan["links"] == []
    assert plan["uncertain"][0]["requirement_id"] == "E-1"


def test_product_specs_and_tender_submissions_do_not_become_profile_fields():
    plan = PF.build_field_plan([
        _item("SPEC-01", "Offered product must comply with: Hemodialysis machine specification",
              category="Technical Specification", scope="offered_product"),
        _item("E-06", "Bid security of INR 50,000 must be submitted.", category="Financial"),
        _item("Q-09", "Non-blacklisting declaration on company letterhead.", category="Eligibility"),
        _item("T-03", "Material traceability for every lot.", category="Technical"),
    ])
    assert plan["definitions"] == []
    assert {r["requirement_id"] for r in plan["tender_specific"]} == {"SPEC-01", "E-06", "Q-09", "T-03"}


def test_order_wording_maps_to_experience_only_in_experience_context():
    delivery, _ = PF.concepts_for_requirement(_item("D-1", "Deliver within ten weeks of purchase order.", category="General"))
    assert all(c["field_key"] != "experience.similar_projects" for c in delivery)
    past, _ = PF.concepts_for_requirement(_item("E-2", "Submit copies of purchase orders for similar supplies in the last 3 years."))
    assert [c["field_key"] for c in past] == ["experience.similar_projects"]


def test_title_decides_tender_specific_and_technical_items():
    # Body mentions MSME / inspection, but the clause asks for EMD / payment terms / a part tolerance.
    for rid, title, body, cat in [
        ("QC-02", "Earnest Money Deposit (EMD) & Tender Fee", "MSME registered firms are exempt from EMD.", "Financial"),
        ("F-03", "Payment", "Payment within 30 days after inspection and acceptance.", "Financial"),
        ("T-01", "Dimensional accuracy", "Parts must meet tolerances verified by CMM inspection.", "Technical"),
        ("DATE-BID-VALIDITY", "Keep the bid valid for 90 days", "Keep the bid valid for 90 days from bid opening.", "General"),
    ]:
        item = {**_item(rid, title, category=cat), "source_quotation": body}
        assert PF.concepts_for_requirement(item) == ([], "tender_specific"), rid
    concepts, _ = PF.concepts_for_requirement(_item("T-02", "Inspection equipment", category="Technical"))
    assert [c["field_key"] for c in concepts] == ["capability.inspection_testing"]


def test_all_matching_concepts_in_one_group_are_kept():
    concepts, _ = PF.concepts_for_requirement(_item("ELG-11", "The bidder should hold valid PAN, and GST registration."))
    assert {c["field_key"] for c in concepts} == {"registration.gst", "registration.pan"}


def test_unmatched_company_requirement_is_listed_not_generated():
    plan = PF.build_field_plan([_item("E-09", "Bidder must operate at least 5,000 business correspondent agents.")])
    assert plan["definitions"] == [] and plan["unmatched"][0]["requirement_id"] == "E-09"


def test_only_open_requirements_generate_fields():
    plan = PF.build_field_plan([_item("C-1", "Valid ISO 9001 certification", status="SATISFIED"),
                                _item("C-2", "Valid ISO 9001 certification", status="NOT APPLICABLE"),
                                _item("C-3", "Valid ISO 14001 certification", status="UNRESOLVED")])
    assert [d["field_key"] for d in plan["definitions"]] == ["certification.iso_14001"]


def test_required_status_and_specific_experience_mapping():
    optional = dict(_item("C-1", "ISO 14001 certification is desirable"), is_mandatory=False)
    plan = PF.build_field_plan([optional, _item("E-2", "At least 5 years of experience in similar supply orders")])
    keys = {l["field_key"]: l for l in plan["links"]}
    assert keys["certification.iso_14001"]["is_mandatory"] is False
    assert {"experience.years_in_business", "experience.similar_projects"} <= set(keys)


# ------------------------------------------------------------------ persistence, stable IDs and reuse

def test_sync_is_idempotent_and_reuses_fields_across_tenders():
    user = _user()
    t1, t2 = _tender(user, "a.pdf"), _tender(user, "b.pdf")
    _save_analysis(user, t1, [_item("E-03", "Average annual turnover of at least INR 75,00,000 over the last three financial years.")])
    _save_analysis(user, t2, [_item("ELG-04", "The bidder should have an average annual turnover of not less than Rs. 50 crore.")])
    first = PF.sync_profile_fields_for_user(user)
    assert first["created"] == ["financial.annual_turnover_by_year"]
    for _ in range(2):  # re-running sync (or re-analysing) creates nothing new
        again = PF.sync_profile_fields_for_user(user)
        assert again["created"] == [] and again["reused"] == ["financial.annual_turnover_by_year"]
    defs = db.get_profile_field_definitions(user)
    assert len(defs) == 1 and defs[0]["required"]
    assert sorted((l["tender_id"], l["requirement_id"]) for l in defs[0]["requirements"]) == [(t1, "E-03"), (t2, "ELG-04")]
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM company_profile_field_requirements WHERE user_id = ?", (user,)).fetchone()[0] == 2
    conn.close()


def test_reanalysis_deactivates_stale_links_but_keeps_field_and_value():
    user = _user()
    tid = _tender(user)
    _save_analysis(user, tid, [_item("C-1", "Valid ISO 9001 certification")])
    PF.sync_profile_fields_for_user(user)
    definition = db.get_profile_field_definitions(user)[0]
    PF.save_field_value(user, definition, "QMS-123, TUV, valid to 2027")
    _save_analysis(user, tid, [_item("C-1", "Valid ISO 9001 certification", status="SATISFIED")])
    PF.sync_profile_fields_for_user(user)
    after = db.get_profile_field_definitions(user)
    assert len(after) == 1 and after[0]["requirements"] == []
    assert db.get_profile_field_definitions(user, include_inactive_links=True)[0]["requirements"][0]["active"] == 0
    assert PF.current_value(db.get_company_profile(user), after[0]) == "QMS-123, TUV, valid to 2027"


def test_existing_saved_values_are_reused():
    user = _user()
    db.save_company_profile({"company_name": "Reuse Co", "annual_turnover": "FY2023-24: INR 2 Crore"}, user)
    db.save_company_profile_fields(user, [{"field_key": "certification.iso_9001", "field_value": "Cert 77"}])
    tid = _tender(user)
    _save_analysis(user, tid, [_item("F-1", "Annual turnover of at least INR 50 lakh."),
                               _item("C-1", "Valid ISO 9001 certification")])
    PF.sync_profile_fields_for_user(user)
    profile = db.get_company_profile(user)
    values = {d["field_key"]: PF.current_value(profile, d) for d in db.get_profile_field_definitions(user)}
    assert values == {"financial.annual_turnover_by_year": "FY2023-24: INR 2 Crore", "certification.iso_9001": "Cert 77"}


def test_saving_values_and_documents_is_unverified_and_versioned():
    user = _user()
    db.save_company_profile({"company_name": "Keep Co", "industry": "Machining", "certifications": "ISO 9001"}, user)
    tid = _tender(user)
    _save_analysis(user, tid, [_item("F-1", "Average annual turnover of at least INR 50 lakh over the last 2 financial years."),
                               _item("C-1", "Valid ISO 9001 certification")])
    PF.sync_profile_fields_for_user(user)
    defs = {d["field_key"]: d for d in db.get_profile_field_definitions(user)}

    text, errors = PF.format_turnover_rows([{"financial_year": "2023-24", "amount": 60, "currency": "INR", "unit": "Lakh"},
                                            {"financial_year": "2024-25", "amount": 70, "currency": "INR", "unit": "Lakh"}])
    assert not errors and text == "FY2023-24: INR 60 Lakh; FY2024-25: INR 70 Lakh"
    PF.save_field_value(user, defs["financial.annual_turnover_by_year"], text)
    profile = db.get_company_profile(user)
    assert profile["annual_turnover"] == text
    assert (profile["company_name"], profile["industry"], profile["certifications"]) == ("Keep Co", "Machining", "ISO 9001")
    # The stored text is the format the deterministic turnover rule reads (comparison itself belongs to analysis).
    assert A.compare_turnover("Average annual turnover of at least INR 50 lakh over the last 2 financial years.", text)["status"] == A.SATISFIED
    assert PF.turnover_rows_from_text(text)[1] == {"financial_year": "2024-25", "amount": 70.0, "currency": "INR", "unit": "Lakh"}

    cert = defs["certification.iso_9001"]
    pdf = {"filename": "iso.pdf", "content": b"%PDF-1.4 synthetic certificate", "media_type": "application/pdf"}
    result = PF.save_field_value(user, cert, "QMS-123 valid to 2027", pdf)
    row = next(f for f in db.get_company_profile(user)["custom_fields"] if f["field_key"] == "certification.iso_9001")
    assert row["verification_status"] == "unverified" and row["source_document_id"] == result["document_id"]
    assert row["source"] == "user_entered_with_document"
    docs = PF.field_documents(db.get_company_profile(user), cert)
    assert len(docs) == 1 and docs[0]["version"] == 1 and docs[0]["verification_status"] == "unverified"
    assert os.path.isfile(docs[0]["storage_path"])
    PF.save_field_value(user, cert, "QMS-123 valid to 2027", pdf)  # identical upload: no new version
    PF.save_field_value(user, cert, "QMS-123 valid to 2027", {**pdf, "content": b"%PDF-1.4 renewed certificate"})
    assert [d["version"] for d in PF.field_documents(db.get_company_profile(user), cert)] == [2, 1]
    assert PF.save_field_value(user, cert, "QMS-123 valid to 2027") == {"value_saved": False, "document_id": None}


def test_turnover_row_validation():
    _, errors = PF.format_turnover_rows([{"financial_year": "24-25", "amount": 1, "currency": "INR", "unit": ""},
                                         {"financial_year": "2023-24", "amount": -5, "currency": "INR", "unit": ""},
                                         {"financial_year": "2022-23", "amount": 5, "currency": "", "unit": ""}])
    assert len(errors) == 3
    _, dup = PF.format_turnover_rows([{"financial_year": "2023-24", "amount": 1, "currency": "INR", "unit": "Crore"}] * 2)
    assert dup


def test_user_isolation():
    a, b = _user(), _user()
    ta = _tender(a)
    _save_analysis(a, ta, [_item("C-1", "Valid ISO 9001 certification")])
    PF.sync_profile_fields_for_user(a)
    assert db.get_profile_field_definitions(b) == []
    with pytest.raises(PermissionError):
        db.sync_tender_profile_fields(b, ta, None, [], [])
    definition = db.get_profile_field_definitions(a)[0]
    PF.save_field_value(b, definition, "B's own value")
    assert PF.current_value(db.get_company_profile(a), definition) == ""
    doc_id = db.save_company_document(a, "a.pdf", b"%PDF-1.4 A only", document_key=definition["document_key"])
    with pytest.raises(ValueError):
        db.update_company_document_status(b, doc_id, "verified")


def test_profile_hash_tracks_evidence_not_field_definitions():
    user = _user()
    db.save_company_profile({"company_name": "Hash Co"}, user)
    base = db.compute_profile_hash(db.get_company_profile(user))
    tid = _tender(user)
    _save_analysis(user, tid, [_item("C-1", "Valid ISO 9001 certification")])
    PF.sync_profile_fields_for_user(user)
    assert db.compute_profile_hash(db.get_company_profile(user)) == base  # generating fields is not new evidence
    definition = db.get_profile_field_definitions(user)[0]
    PF.save_field_value(user, definition, "QMS-123")
    with_value = db.compute_profile_hash(db.get_company_profile(user))
    assert with_value != base
    doc_id = PF.save_field_value(user, definition, "QMS-123", {"filename": "c.pdf", "content": b"%PDF cert"})["document_id"]
    with_doc = db.compute_profile_hash(db.get_company_profile(user))
    assert with_doc != with_value
    profile = db.get_company_profile(user)
    legacy = {**profile, "supporting_documents": [{k: v for k, v in d.items() if k != "verification_status"}
                                                   for d in profile["supporting_documents"]]}
    assert db.compute_profile_hash(legacy) == with_doc  # adding the unverified column did not change existing hashes
    db.update_company_document_status(user, doc_id, "verified")
    assert db.compute_profile_hash(db.get_company_profile(user)) != with_doc


def test_invalid_definitions_are_rejected():
    user = _user()
    tid = _tender(user)
    with pytest.raises(ValueError):
        db.sync_tender_profile_fields(user, tid, None, [{"field_key": "x", "label": "X", "input_type": "dropdown"}], [])
    with pytest.raises(ValueError):
        db.update_company_document_status(user, 1, "approved")


# ------------------------------------------------------------------ UI (real Streamlit page via AppTest)

def test_profile_page_renders_generated_field_and_persists_value():
    """Drives app.py itself. AppTest cannot operate file_uploader/data_editor, so uploads and the turnover
    editor are covered by the persistence tests above, not here."""
    from streamlit.testing.v1 import AppTest
    user = _user()
    db.save_company_profile({"company_name": "UI Co"}, user)
    tid = _tender(user, "ui.pdf")
    _save_analysis(user, tid, [_item("C-1", "Valid ISO 9001 certification", category="Certifications")])
    profile_user = db.get_user_by_id(user)
    at = AppTest.from_file(os.path.join(tf_test_env.PROJECT_DIR, "app.py"), default_timeout=120)
    at.session_state["authenticated"] = True
    at.session_state["user"] = profile_user
    at.session_state["current_page"] = "🏢 Company Profile"
    at.run()
    assert not at.exception
    assert any("Section 2" in m.value and "Tender-Specific Requirements" in m.value for m in at.markdown)
    at.selectbox(key=f"profile_tender_select_{user}").select(tid).run()
    assert not at.exception
    assert any("Valid ISO 9001 certification" in m.value for m in at.markdown)
    assert any("ISO 9001 certification" in m.value for m in at.markdown)
    assert any("certificate number" in m.value.lower() for m in at.markdown)
    at.text_input(key=f"profile_value_{user}_{tid}_C_1").input("QMS-123, TUV, valid to 2027")
    next(b for b in at.button if f"profile_requirement_{user}_{tid}_C_1" in str(b.form_id)).click()
    at.run()
    assert not at.exception
    row = next(f for f in db.get_company_profile(user)["custom_fields"] if f["field_key"] == "certification.iso_9001")
    assert (row["field_value"], row["verification_status"]) == ("QMS-123, TUV, valid to 2027", "unverified")
    assert any("unverified" in s.value for s in at.success)
    at.session_state["current_page"] = "🎯 Tender Analysis"
    at.session_state["selected_tender_id"] = tid
    at.run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert labels[-1] in ("Missing Requirements", "Section 8: Deadlines & Submission Milestones") and len(labels) in (8, 9)


# ------------------------------------------------------------------ UI wiring guards

def test_missing_requirements_tab_contains_editor_and_profile_section_present():
    src = open(os.path.join(tf_test_env.PROJECT_DIR, "app.py"), encoding="utf-8").read()
    assert '"Missing Requirements"' in src
    assert "render_requirements_editor(tender, profile, user_id, saved_analysis)" in src
    assert "render_tender_requested_fields(user_id, profile)" in src
    assert "render_profile_evidence_storage(user_id, profile)" in src  # existing evidence storage kept
    assert 'st.button("Save Company Profile", key=f"save_company_profile_{user_id}"' in src
