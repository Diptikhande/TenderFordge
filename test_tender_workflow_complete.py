"""
Complete End-to-End Verification Test for Tender-to-Company-Profile Workflow
Tests T001.pdf and T003.pdf extraction, 4-section profile mapping, persistence,
re-evaluation, and tab structure.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import sqlite3

from database import (
    init_db,
    get_connection,
    save_company_profile,
    get_company_profile,
    save_tender_disclosure,
    get_tender_disclosures,
    get_all_user_disclosures,
    save_analysis,
    get_analysis_for_tender,
    create_user,
    save_tender,
    get_tender_by_id,
    compute_profile_hash
)
from analyzer import (
    extract_tender_missing_profile_fields,
    reevaluate_tender_analysis,
    find_source_page,
    run_deterministic_checks,
    extract_eligibility_analysis
)

def test_tender_workflow_t001_and_t003():
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # Verify T001 and T003 exist in the database
    cur.execute("SELECT id, filename, extracted_text, filepath, page_count FROM tenders WHERE filename LIKE '%T001%' LIMIT 1")
    t1_row = cur.fetchone()
    assert t1_row is not None, "T001 tender not found in DB"
    t1_id, t1_fn, t1_text, t1_path, t1_pages = t1_row

    cur.execute("SELECT id, filename, extracted_text, filepath, page_count FROM tenders WHERE filename LIKE '%T003%' LIMIT 1")
    t3_row = cur.fetchone()
    assert t3_row is not None, "T003 tender not found in DB"
    t3_id, t3_fn, t3_text, t3_path, t3_pages = t3_row

    print(f"\n[PASS] Loaded T001 ({t1_fn}, {t1_pages} pages) and T003 ({t3_fn}, {t3_pages} pages)")

    # -------------------------------------------------------------
    # 1. VERIFY T001 REQUIREMENT EXTRACTION & SOURCE PAGES
    # -------------------------------------------------------------
    print("\n--- 1. Testing T001 Requirement Extraction & Source Pages ---")
    t1_empty_missing = extract_tender_missing_profile_fields(t1_text, {}, pdf_path=t1_path)
    t1_keys = {m["field_key"]: m for m in t1_empty_missing}

    # Verify contractor class
    assert "corp_contractor_class" in t1_keys, "T001 missing contractor class registration requirement"
    c_class = t1_keys["corp_contractor_class"]
    assert c_class["section_id"] == 1, "Contractor class must be in Section 1 (Organization & General Info)"
    assert c_class["source_page"] in [3, 5, 9], f"Contractor class source page unexpected: {c_class['source_page']}"
    assert "appropriate class" in c_class["tender_quote"].lower(), "Quote should reference appropriate class"
    print(f"  ✓ T001 Contractor Class: Page {c_class['source_page']} | Sec {c_class['section_id']} | Quote: {c_class['tender_quote'][:50]}")

    # Verify plant & machinery
    assert "tech_plant_machinery" in t1_keys, "T001 missing plant and machinery requirement"
    mach = t1_keys["tech_plant_machinery"]
    assert mach["section_id"] == 3, "Machinery must be in Section 3 (Capabilities, Past Projects & Financial Details)"
    assert mach["source_page"] == 18, f"Machinery source page should be 18, got {mach['source_page']}"
    assert "plant and machinery" in mach["tender_quote"].lower(), "Quote should mention plant and machinery"
    print(f"  ✓ T001 Machinery: Page {mach['source_page']} | Sec {mach['section_id']} | Quote: {mach['tender_quote'][:50]}")

    # Verify works in hand
    assert "past_works_in_hand" in t1_keys, "T001 missing works in hand requirement"
    wih = t1_keys["past_works_in_hand"]
    assert wih["section_id"] == 3, "Works in hand must be in Section 3"
    assert wih["source_page"] == 18, f"Works in hand source page should be 18, got {wih['source_page']}"
    assert "works" in wih["tender_quote"].lower(), "Quote should mention works in hand"
    print(f"  ✓ T001 Works in Hand: Page {wih['source_page']} | Sec {wih['section_id']} | Quote: {wih['tender_quote'][:50]}")

    # Verify power of attorney
    assert "authorized_signatory_poa" in t1_keys, "T001 missing Power of Attorney requirement"
    poa1 = t1_keys["authorized_signatory_poa"]
    assert poa1["section_id"] == 4, "Power of Attorney must be in Section 4 (Authorized Contact Information)"
    assert poa1["source_page"] in [2, 6, 13, 14], f"POA source page unexpected: {poa1['source_page']}"
    assert "power of attorney" in poa1["tender_quote"].lower(), "Quote should mention Power of Attorney"
    print(f"  ✓ T001 Power of Attorney: Page {poa1['source_page']} | Sec {poa1['section_id']} | Quote: {poa1['tender_quote'][:50]}")

    # -------------------------------------------------------------
    # 2. VERIFY T003 REQUIREMENT EXTRACTION & SOURCE PAGES
    # -------------------------------------------------------------
    print("\n--- 2. Testing T003 Requirement Extraction & Source Pages ---")
    t3_empty_missing = extract_tender_missing_profile_fields(t3_text, {}, pdf_path=t3_path)
    t3_keys = {m["field_key"]: m for m in t3_empty_missing}

    # Verify turnover percentage requirement
    assert "annual_turnover" in t3_keys, "T003 missing annual turnover requirement"
    tov3 = t3_keys["annual_turnover"]
    assert tov3["section_id"] == 3, "Turnover must be in Section 3"
    print(f"  ✓ T003 Turnover: Page {tov3['source_page']} | Sec {tov3['section_id']} | Quote: {tov3['tender_quote'][:50]}")

    # Verify GST registration
    assert "corp_gst_registration" in t3_keys, "T003 missing GST registration requirement"
    gst3 = t3_keys["corp_gst_registration"]
    assert gst3["section_id"] == 1, "GST must be in Section 1"
    assert gst3["source_page"] == 6, f"GST source page should be 6, got {gst3['source_page']}"
    assert "g.s.t" in gst3["tender_quote"].lower() or "gst" in gst3["tender_quote"].lower()
    print(f"  ✓ T003 GST Registration: Page {gst3['source_page']} | Sec {gst3['section_id']} | Quote: {gst3['tender_quote'][:50]}")

    # Verify similar past experience
    assert "past_projects" in t3_keys, "T003 missing past experience requirement"
    past3 = t3_keys["past_projects"]
    assert past3["section_id"] == 3, "Past projects must be in Section 3"
    assert past3["source_page"] == 6, f"Past experience source page should be 6, got {past3['source_page']}"
    assert "similar" in past3["tender_quote"].lower()
    print(f"  ✓ T003 Past Projects: Page {past3['source_page']} | Sec {past3['section_id']} | Quote: {past3['tender_quote'][:50]}")

    # Verify net worth / solvency
    assert "financial_solvency" in t3_keys, "T003 missing net worth/solvency requirement"
    fin3 = t3_keys["financial_solvency"]
    assert fin3["section_id"] == 3, "Net worth must be in Section 3"
    assert fin3["source_page"] == 6, f"Net worth source page should be 6, got {fin3['source_page']}"
    print(f"  ✓ T003 Net Worth / Solvency: Page {fin3['source_page']} | Sec {fin3['section_id']} | Quote: {fin3['tender_quote'][:50]}")

    # Verify ISO 13485 certification
    iso_found = [k for k in t3_keys if "13485" in k]
    assert len(iso_found) > 0, "T003 missing ISO 13485 medical device accreditation requirement"
    iso3 = t3_keys[iso_found[0]]
    assert iso3["section_id"] == 2, "ISO 13485 must be in Section 2 (Certifications & Quality Accreditations)"
    assert iso3["source_page"] in [41, 46], f"ISO 13485 source page unexpected: {iso3['source_page']}"
    print(f"  ✓ T003 ISO 13485: Page {iso3['source_page']} | Sec {iso3['section_id']} | Quote: {iso3['tender_quote'][:50]}")

    # Verify OEM Authorization Form (MAF)
    assert "tech_oem_authorization" in t3_keys, "T003 missing MAF requirement"
    maf3 = t3_keys["tech_oem_authorization"]
    assert maf3["section_id"] == 3, "MAF must be in Section 3"
    assert maf3["source_page"] in [13, 16], f"MAF source page unexpected: {maf3['source_page']}"
    print(f"  ✓ T003 OEM Authorization (MAF): Page {maf3['source_page']} | Sec {maf3['section_id']} | Quote: {maf3['tender_quote'][:50]}")

    # Verify Power of Attorney
    assert "authorized_signatory_poa" in t3_keys, "T003 missing POA requirement"
    poa3 = t3_keys["authorized_signatory_poa"]
    assert poa3["section_id"] == 4, "POA must be in Section 4"
    assert poa3["source_page"] in [13, 27], f"POA source page unexpected: {poa3['source_page']}"
    print(f"  ✓ T003 Power of Attorney: Page {poa3['source_page']} | Sec {poa3['section_id']} | Quote: {poa3['tender_quote'][:50]}")

    # -------------------------------------------------------------
    # 3. VERIFY ONLY 4 SECTIONS ARE USED
    # -------------------------------------------------------------
    print("\n--- 3. Verifying Section IDs across all extracted fields ---")
    for f in t1_empty_missing + t3_empty_missing:
        assert f["section_id"] in [1, 2, 3, 4], f"Invalid section id: {f['section_id']} for field {f['field_label']}"
        assert f["section_name"] in [
            "Organization & General Information",
            "Certifications & Quality Accreditations",
            "Capabilities, Past Projects & Financial Details",
            "Authorized Contact Information"
        ], f"Invalid section name: {f['section_name']}"
    print("  ✓ All fields are correctly mapped ONLY to the 4 canonical profile sections")

    # -------------------------------------------------------------
    # 4. VERIFY PROFILE SAVE & PERSISTENCE ACROSS SESSIONS
    # -------------------------------------------------------------
    print("\n--- 4. Testing Profile and Disclosure Persistence ---")
    test_uid = 99912
    cur.execute("INSERT OR IGNORE INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, datetime('now'))",
                (test_uid, "workflow_test@tenderforge.internal", "dummy_hash"))
    conn.commit()

    # Initial profile
    save_company_profile({
        "company_name": "Apex Engineering & Medical Solutions Ltd",
        "industry": "Civil Works & Medical Supplies",
        "years_of_experience": "12 years",
        "annual_turnover": "$35,000,000 USD",
        "certifications": "ISO 9001:2015",
        "technical_capabilities": "State-of-the-art dialysis service & heavy civil works",
        "past_projects": "Supplied medical equipment to 5 govt hospitals with purchase orders",
        "financial_details": "Audited positive net worth for last 3 years",
        "contact_name": "Dr. Rajesh Sharma, Managing Director",
        "contact_email": "bids@apexmed.com",
        "contact_phone": "+91-9876543210",
        "contact_address": "45 Industrial Zone, Pune, Maharashtra"
    }, user_id=test_uid)

    # Save tender disclosures for T001
    save_tender_disclosure(
        user_id=test_uid,
        tender_id=t1_id,
        field_key="corp_contractor_class",
        field_label="Contractor Registration Class (ZP / PWD / RDD)",
        field_value="Registered Class 1 Contractor with ZP Yavatmal (Reg No: ZPY/CIVIL/2022/491)",
        doc_reference="ZP_Yavatmal_Reg_Cert_Class1.pdf",
        evidence_status="Verified supporting document attached / on file",
        section_id=1
    )
    save_tender_disclosure(
        user_id=test_uid,
        tender_id=t1_id,
        field_key="tech_plant_machinery",
        field_label="Plant, Machinery & Tools in Possession",
        field_value="Fully owned fleet: 2 Excavators, 3 Concrete Mixers, 4 Tipper Trucks, Vibrators",
        doc_reference="Machinery_Ownership_RTO_Invoices.pdf",
        evidence_status="Verified supporting document attached / on file",
        section_id=3
    )

    # Save tender disclosures for T003
    save_tender_disclosure(
        user_id=test_uid,
        tender_id=t3_id,
        field_key="corp_gst_registration",
        field_label="GST Registration Certificate & Tax Clearance",
        field_value="GSTIN: 27AABCA1234F1Z5; latest GSTR-3B tax clearance filed for Q4",
        doc_reference="GST_Reg_Certificate_and_GSTR3B.pdf",
        evidence_status="Verified supporting document attached / on file",
        section_id=1
    )
    save_tender_disclosure(
        user_id=test_uid,
        tender_id=t3_id,
        field_key="cert_iso_13485",
        field_label="ISO 13485 Accreditation & Validity",
        field_value="ISO 13485:2016 Certified Medical Devices Quality Management System (Cert #MD-98421, valid till 2028)",
        doc_reference="ISO_13485_Certificate_NABCB.pdf",
        evidence_status="Verified supporting document attached / on file",
        section_id=2
    )

    # Reload from DB (simulate fresh login or page refresh)
    reloaded_prof = get_company_profile(test_uid)
    assert reloaded_prof["company_name"] == "Apex Engineering & Medical Solutions Ltd"
    t1_saved_discs = get_tender_disclosures(test_uid, t1_id)
    assert "corp_contractor_class" in t1_saved_discs
    assert "tech_plant_machinery" in t1_saved_discs
    assert t1_saved_discs["corp_contractor_class"]["evidence_status"] == "Verified supporting document attached / on file"

    t3_saved_discs = get_tender_disclosures(test_uid, t3_id)
    assert "corp_gst_registration" in t3_saved_discs
    assert "cert_iso_13485" in t3_saved_discs
    print("  ✓ Profile and per-tender disclosures persist accurately across database reload")

    # -------------------------------------------------------------
    # 5. VERIFY RE-EVALUATION WITH UPDATED PROFILE (ZERO LLM CALLS)
    # -------------------------------------------------------------
    print("\n--- 5. Testing Re-Evaluation with Updated Profile ---")
    # Re-evaluate T001
    t1_reeval = reevaluate_tender_analysis(t1_text, reloaded_prof, pdf_path=t1_path, tender_disclosures=t1_saved_discs)
    t1_mat = t1_reeval["compliance_matrix"]
    t1_summ = t1_reeval["summary"]

    # Verify contractor class requirement is now SATISFIED or VERIFIED
    c_req = next((c for c in t1_mat if c["requirement_id"] == "DET-CONTRACTOR-CLASS"), None)
    assert c_req is not None, "DET-CONTRACTOR-CLASS missing from re-evaluated matrix"
    assert c_req["status"] == "SATISFIED", f"Contractor class should be SATISFIED, got {c_req['status']}"
    assert "Registered Class 1" in c_req["company_evidence"]
    print(f"  ✓ T001 Re-evaluated: Contractor Class is '{c_req['status']}' with evidence: {c_req['company_evidence'][:60]}")

    # Re-evaluate T003
    t3_reeval = reevaluate_tender_analysis(t3_text, reloaded_prof, pdf_path=t3_path, tender_disclosures=t3_saved_discs)
    t3_mat = t3_reeval["compliance_matrix"]
    gst_req = next((c for c in t3_mat if c["requirement_id"] == "DET-GST"), None)
    assert gst_req is not None, "DET-GST missing from re-evaluated matrix"
    assert gst_req["status"] == "SATISFIED", f"GST requirement should be SATISFIED, got {gst_req['status']}"
    assert "27AABCA1234F1Z5" in gst_req["company_evidence"]
    print(f"  ✓ T003 Re-evaluated: GST Requirement is '{gst_req['status']}' with evidence: {gst_req['company_evidence'][:60]}")

    # Verify ISO 13485 requirement is SATISFIED
    iso_req = next((c for c in t3_mat if "13485" in c["requirement_id"]), None)
    assert iso_req is not None, "ISO 13485 requirement missing from re-evaluated matrix"
    assert iso_req["status"] == "SATISFIED", f"ISO 13485 should be SATISFIED, got {iso_req['status']}"
    print(f"  ✓ T003 Re-evaluated: ISO 13485 is '{iso_req['status']}' with evidence: {iso_req['company_evidence'][:60]}")

    # Verify model_used is deterministic-evaluator (zero LLM tokens)
    assert t1_reeval["model_used"] == "deterministic-evaluator"
    assert t3_reeval["model_used"] == "deterministic-evaluator"
    print("  ✓ Re-evaluation executed 100% deterministically with 0 LLM calls")

    # -------------------------------------------------------------
    # 6. VERIFY APP.PY TAB STRUCTURE
    # -------------------------------------------------------------
    print("\n--- 6. Verifying app.py Tab Structure ---")
    with open("app.py", "r", encoding="utf-8") as f:
        app_code = f.read()

    # Verify st.tabs has exactly 8 tabs
    assert "tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([" in app_code, "st.tabs should have exactly 8 tabs"
    assert "tab9" not in app_code, "tab9 should not exist in app.py"
    assert "Missing Company Information" not in app_code.split("st.tabs([")[1].split("])")[0], "Missing Company Information must NOT be a tab"
    assert "Section 8: Deadlines & Submission Milestones" in app_code, "Section 8 must be Deadlines & Submission Milestones"
    print("  ✓ Tender Analysis has exactly 8 tabs with Deadlines as Section 8 (No Missing Company Information tab)")

    # Clean up test user
    cur.execute("DELETE FROM tender_disclosures WHERE user_id = ?", (test_uid,))
    cur.execute("DELETE FROM company_profile WHERE user_id = ?", (test_uid,))
    cur.execute("DELETE FROM users WHERE id = ?", (test_uid,))
    conn.commit()
    conn.close()

    print("\n=======================================================")
    print(" ALL WORKFLOW REQUIREMENTS SUCCESSFULLY VERIFIED! ")
    print("=======================================================")

if __name__ == "__main__":
    test_tender_workflow_t001_and_t003()
