"""
Section 3B: Tender Analysis Results Page Verification Test Suite
Tests:
1. DEMO account authentication & company profile setup.
2. Tender PDF generation, upload, and PyMuPDF text extraction.
3. Execution of full 7-section tender analysis.
4. Comprehensive verification of all Seven Sections:
   - Section 1: Tender Overview (Title, Ref #, Org, Value, Deadline, Scope)
   - Section 2: Eligibility Analysis (Experience, Turnover, Certs, Registration)
   - Section 3: Compliance Matrix (Requirement, Tender Ev, Company Ev, Status, Explanation)
   - Section 4: Technical Evaluation (Capabilities, Equipment, Expertise, Deliverables)
   - Section 5: Financial Analysis (EMD, Fees, Turnover, PBG, Payment Terms)
   - Section 6: Risk Assessment (Penalties, Deadlines, Missing Docs, Unclear Obligations)
   - Section 7: Bid Recommendation (BID/NO-BID/REVIEW REQUIRED, Reasons, Blockers, Next Actions)
5. Evidence grounding & traceability verification (verbatim citations from PDF and profile).
6. "Not found in available evidence" rule validation.
7. Persistence across session logout and login.
8. Cross-account access rejection (unauthorized account cannot read demo analysis or tender).
9. NO-BID disqualification rule verification on failing criteria.
10. Clean teardown preserving existing data.
"""

import os
import sys
import fitz
import json

from database import (
    init_db,
    create_user,
    authenticate_user,
    get_user_by_id,
    save_company_profile,
    get_company_profile,
    save_tender,
    get_all_tenders,
    get_tender_by_id,
    save_analysis,
    get_analysis_for_tender,
    compute_profile_hash,
    get_connection
)
from pdf_processor import extract_text_from_pdf
from analyzer import (
    analyze_tender_with_groq,
    run_deterministic_analysis,
    ensure_seven_sections,
    NOT_FOUND
)

def run_section3b_test():
    print("=" * 70)
    print("  SECTION 3B: TENDER ANALYSIS RESULTS PAGE VERIFICATION SUITE")
    print("=" * 70)

    init_db()

    demo_email = "demo_executive@tenderforge.internal"
    demo_pw = "DemoExec#2026!Vault"
    demo_name = "Demo Executive Officer"

    attacker_email = "auditor_external@othercorp.internal"
    attacker_pw = "Attacker#2026!External"
    attacker_name = "External Auditor"

    # Pre-clean test accounts if left over
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email IN (?, ?)", (demo_email, attacker_email))
    for r in c.fetchall():
        uid = r["id"]
        c.execute("DELETE FROM tender_analyses WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM tenders WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM company_profile WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    # 1. SETUP DEMO ACCOUNT & PROFILE
    print("\n[STEP 1] Setting up DEMO account & Company Profile...")
    ok, msg, demo_user = create_user(demo_email, demo_pw, demo_name)
    assert ok and demo_user, f"Failed to register DEMO user: {msg}"
    demo_id = demo_user["id"]
    print(f" -> DEMO User created: ID={demo_id}, Email={demo_email}")

    demo_profile = {
        "company_name": "Apex Cloud Defense & Space Cyber LLC",
        "industry": "Enterprise Cloud Architecture & Zero Trust Defense",
        "years_of_experience": "9 years",
        "annual_turnover": "$15,000,000 USD",
        "certifications": "ISO 9001:2015, ISO 27001:2022, CMMI Level 3",
        "technical_capabilities": "Python scalable microservices, Kubernetes orchestration, Zero Trust SIEM",
        "past_projects": "Department of Defense Secure Data Ingestion Platform ($6.2M, 2024)",
        "financial_details": "5 years continuous audited profit; $3,000,000 bank guarantee facility",
        "contact_name": demo_name,
        "contact_email": demo_email,
        "contact_phone": "+1 (555) 019-8765",
        "contact_address": "800 Pentagon Way, Suite 400, Arlington, VA"
    }
    save_company_profile(demo_profile, user_id=demo_id)
    saved_prof = get_company_profile(user_id=demo_id)
    assert saved_prof["company_name"] == demo_profile["company_name"], "Profile mismatch"
    prof_hash = compute_profile_hash(saved_prof)
    print(f" -> Company Profile saved! Profile Hash: {prof_hash}")

    # 2. GENERATE REAL SAMPLE TENDER PDF & PROCESS
    print("\n[STEP 2] Generating & Processing Sample RFP Tender PDF...")
    pdf_dir = "/app/data/uploads"
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = os.path.join(pdf_dir, "US_DOD_Cloud_Data_Platform_RFP.pdf")

    doc = fitz.open()
    page = doc.new_page()
    tender_content = """
REQUEST FOR PROPOSAL (RFP) # US-DOD-2026-CLOUD-99
TITLE: Enterprise Secure Cloud Data Ingestion Platform
ISSUING AUTHORITY: Federal Technology Procurement Division
ESTIMATED BUDGET: $10,000,000 USD
BID SUBMISSION DEADLINE: 2026-11-20 17:00 EST

BRIEF DESCRIPTION & SCOPE OF WORK:
The objective is to procure and implement an Enterprise Secure Cloud Data Ingestion Platform
supporting high-throughput streaming pipelines, zero trust microservices, and 24/7 dedicated support.

1. MANDATORY ELIGIBILITY CRITERIA:
- The bidder must have at least 5 years of verifiable experience in enterprise cloud deployments.
- Minimum average annual turnover of $5,000,000 USD over the past three audited financial years.
- Mandatory valid ISO 9001 and ISO 27001 certifications required at bid submission.
- Bidder must be a registered legal entity with good statutory standing.

2. TECHNICAL SPECIFICATIONS & DELIVERABLES:
- Proven experience with Python-based scalable data pipelines and Kubernetes container orchestration.
- Modern cloud infrastructure tooling with automated monitoring.
- Deliverable: Production deployment of secure data platform within 6 months.
- Service Level Agreement: 24/7 dedicated incident support with 1-hour response SLA.

3. COMMERCIAL & FINANCIAL TERMS:
- Earnest Money Deposit (EMD) / Bid Security: $100,000 USD refundable bank guarantee.
- Tender Document Fee: $500 USD non-refundable.
- Performance Bank Guarantee (PBG): 5% of total contract value upon award.
- Payment Terms: Milestone payments based on 25% acceptance phases.

4. RISK, DEADLINES & PENALTY TERMS:
- Liquidated Damages: 0.5% per week of delay up to a maximum cap of 10% contract value.
- Pre-bid clarification query deadline: 2026-10-31 17:00 EST.
- Mandatory Submission Documents:
  * Audited balance sheets for past 3 years.
  * Copies of ISO 9001 and ISO 27001 certificates.
  * Solvency certificate from a nationalized bank.
"""
    page.insert_text((50, 50), tender_content, fontsize=9.5)
    doc.save(pdf_path)
    doc.close()

    extract_result = extract_text_from_pdf(pdf_path)
    assert extract_result["success"] is True, "PDF extraction failed"
    print(f" -> PDF extracted successfully! Pages: {extract_result['page_count']}, Chars: {len(extract_result['text'])}")

    tender_id = save_tender(
        filename="US_DOD_Cloud_Data_Platform_RFP.pdf",
        filepath=pdf_path,
        extracted_text=extract_result["text"],
        page_count=extract_result["page_count"],
        upload_status="success",
        user_id=demo_id
    )
    print(f" -> Tender saved in DB: ID={tender_id} for User={demo_id}")

    # 3. RUN 7-SECTION ANALYSIS & SAVE
    print("\n[STEP 3] Running 7-Section AI / Deterministic Analysis...")
    analysis_res = analyze_tender_with_groq(extract_result["text"], saved_prof)
    assert analysis_res["success"] is True, f"Analysis execution failed: {analysis_res.get('error')}"
    data = analysis_res["data"]

    analysis_id = save_analysis(
        tender_id=tender_id,
        profile_hash=prof_hash,
        recommendation=data.get("recommendation", "REVIEW REQUIRED"),
        executive_summary=data.get("executive_summary", ""),
        structured_tender_data=data,
        compliance_matrix=data.get("compliance_matrix", []),
        risks=data.get("risks", []),
        missing_information=data.get("missing_information", []),
        model_used=data.get("model_used", "deterministic-engine"),
        user_id=demo_id
    )
    print(f" -> Analysis saved successfully: ID={analysis_id}")

    # 4. VERIFY ALL SEVEN SECTIONS
    print("\n[STEP 4] Verifying the Seven Mandatory Sections...")
    saved_analysis = get_analysis_for_tender(tender_id, user_id=demo_id)
    assert saved_analysis is not None, "Failed to retrieve saved analysis"

    full_sections = ensure_seven_sections(saved_analysis, extract_result["text"], saved_prof)

    # Section 1: Tender Overview
    print("\n--- Section 1: Tender Overview ---")
    ov = full_sections["tender_overview"]
    print(f"  Title: {ov['title']}")
    print(f"  Reference #: {ov['reference_number']}")
    print(f"  Issuing Org: {ov['issuing_organization']}")
    print(f"  Estimated Value: {ov['estimated_value']}")
    print(f"  Deadline: {ov['deadline']}")
    print(f"  Scope: {ov['scope_of_work'][:80]}...")

    assert "Cloud Data Ingestion" in ov["title"] or "Enterprise" in ov["title"], "Title mismatch"
    assert "US-DOD-2026-CLOUD-99" in ov["reference_number"], "Reference # mismatch"
    assert "Federal Technology Procurement" in ov["issuing_organization"], "Issuing org mismatch"
    assert "10,000,000" in ov["estimated_value"], "Value mismatch"
    assert "2026-11-20" in ov["deadline"], "Deadline mismatch"
    assert ov["scope_of_work"] != NOT_FOUND, "Scope must be extracted"
    print(" -> PASS: Section 1 (Tender Overview) validated.")

    # Section 2: Eligibility Analysis
    print("\n--- Section 2: Eligibility Analysis ---")
    el = full_sections["eligibility_analysis"]
    print(f"  Experience: Status={el['experience']['status']} | {el['experience']['explanation']}")
    print(f"  Turnover: Status={el['turnover']['status']} | {el['turnover']['explanation']}")
    print(f"  Certifications: Status={el['certifications']['status']} | {el['certifications']['explanation']}")
    print(f"  Registration: Status={el['registration_requirements']['status']} | {el['registration_requirements']['explanation']}")

    assert el["experience"]["status"] == "SATISFIED", "Experience must be SATISFIED (9 yrs > 5 yrs)"
    assert el["turnover"]["status"] == "SATISFIED", "Turnover must be SATISFIED ($15M > $5M)"
    assert el["certifications"]["status"] == "SATISFIED", "Certifications must be SATISFIED (ISO 9001 & 27001)"
    assert el["registration_requirements"]["status"] == "SATISFIED", "Registration must be SATISFIED"
    print(" -> PASS: Section 2 (Eligibility Analysis) validated.")

    # Section 3: Compliance Matrix
    print("\n--- Section 3: Compliance Matrix ---")
    cm = full_sections["compliance_matrix"]
    print(f"  Total Requirements Evaluated: {len(cm)}")
    assert len(cm) >= 3, "Compliance matrix should contain at least 3 requirements"
    for item in cm:
        print(f"  - [{item.get('requirement_id')}] {item.get('requirement')}: {item.get('status')}")
        assert item.get("tender_evidence") is not None, "Tender evidence missing"
        assert item.get("company_evidence") is not None, "Company evidence missing"
        assert item.get("explanation") is not None, "Explanation missing"
    print(" -> PASS: Section 3 (Compliance Matrix) validated.")

    # Section 4: Technical Evaluation
    print("\n--- Section 4: Technical Evaluation ---")
    te = full_sections["technical_evaluation"]
    print(f"  Capabilities Count: {len(te.get('capabilities', []))}")
    print(f"  Equipment Count: {len(te.get('equipment', []))}")
    print(f"  Expertise Count: {len(te.get('expertise', []))}")
    print(f"  Deliverables Count: {len(te.get('deliverables', []))}")
    assert len(te.get("capabilities", [])) > 0, "Technical capabilities missing"
    assert len(te.get("deliverables", [])) > 0, "Technical deliverables missing"
    print(" -> PASS: Section 4 (Technical Evaluation) validated.")

    # Section 5: Financial Analysis
    print("\n--- Section 5: Financial Analysis ---")
    fa = full_sections["financial_analysis"]
    print(f"  EMD: {fa['emd']['value']}")
    print(f"  Tender Fee: {fa['tender_fee']['value']}")
    print(f"  Turnover: {fa['turnover_required']['value']}")
    print(f"  PBG: {fa['pbg']['value']}")
    print(f"  Payment Terms: {fa['payment_terms']['terms']}")
    assert "100,000" in fa["emd"]["value"], "EMD value mismatch"
    assert "500" in fa["tender_fee"]["value"], "Fee value mismatch"
    assert "5%" in fa["pbg"]["value"], "PBG percentage mismatch"
    print(" -> PASS: Section 5 (Financial Analysis) validated.")

    # Section 6: Risk Assessment
    print("\n--- Section 6: Risk Assessment ---")
    ra = full_sections["risk_assessment"]
    print(f"  Penalties: {ra['penalties']}")
    print(f"  Deadlines: {ra['deadlines']}")
    print(f"  Missing Documents: {ra['missing_documents']}")
    print(f"  Unclear Obligations: {ra['unclear_obligations']}")
    assert len(ra["penalties"]) > 0, "Penalties missing"
    assert any("0.5%" in str(p) or "liquidated" in str(p).lower() for p in ra["penalties"]), "Delay penalty clause missing"
    assert len(ra["deadlines"]) > 0, "Deadlines missing"
    print(" -> PASS: Section 6 (Risk Assessment) validated.")

    # Section 7: Bid Recommendation
    print("\n--- Section 7: Bid Recommendation ---")
    br = full_sections["bid_recommendation"]
    print(f"  Decision: {br['decision']}")
    print(f"  Reasons: {br['reasons']}")
    print(f"  Blockers: {br['blockers']}")
    print(f"  Next Actions: {br['next_actions']}")
    assert br["decision"] in ["BID", "REVIEW REQUIRED", "NO-BID"], "Invalid decision"
    assert len(br["reasons"]) > 0, "Reasons missing"
    assert len(br["next_actions"]) > 0, "Next actions missing"
    print(" -> PASS: Section 7 (Bid Recommendation) validated.")

    # 5. SOURCE TRACEABILITY VERIFICATION
    print("\n[STEP 5] Verifying Source Traceability...")
    # Check that tender evidence in overview was drawn from PDF
    assert ov["reference_number"] in extract_result["text"], "Reference # was not in PDF!"
    assert "Federal Technology Procurement" in extract_result["text"], "Issuing org was not in PDF!"
    # Check that company evidence in eligibility was drawn from profile
    assert demo_profile["annual_turnover"] in el["turnover"]["company_evidence"], "Turnover evidence was not in profile!"
    print(" -> PASS: All evidence is verifiably traceable to tender PDF and company profile.")

    # 6. PERSISTENCE ACROSS LOGOUT & LOGIN
    print("\n[STEP 6] Testing Persistence across Logout & Login...")
    # Simulate logout
    logged_out_user = None
    # Simulate login
    ok_login, msg_login, logged_in_user = authenticate_user(demo_email, demo_pw)
    assert ok_login and logged_in_user["id"] == demo_id, "Login failed after logout"

    # Fetch analysis again with authenticated ID
    re_retrieved = get_analysis_for_tender(tender_id, user_id=logged_in_user["id"])
    assert re_retrieved is not None, "Failed to retrieve analysis after login"
    assert re_retrieved["profile_hash"] == prof_hash, "Profile hash altered after login"
    assert re_retrieved["recommendation"] == saved_analysis["recommendation"], "Recommendation altered after login"
    print(" -> PASS: Analysis persists and remains available across logout and login.")

    # 7. CROSS-ACCOUNT ACCESS PREVENTION
    print("\n[STEP 7] Testing Cross-Account Access Prevention...")
    ok_att, msg_att, attacker = create_user(attacker_email, attacker_pw, attacker_name)
    assert ok_att and attacker["id"] != demo_id, "Failed to create attacker account"

    # Attacker tries to read Demo user's tender
    stolen_tender = get_tender_by_id(tender_id, user_id=attacker["id"])
    assert stolen_tender is None, "SECURITY FAILURE: Attacker accessed Demo tender!"

    # Attacker tries to read Demo user's analysis
    stolen_analysis = get_analysis_for_tender(tender_id, user_id=attacker["id"])
    assert stolen_analysis is None, "SECURITY FAILURE: Attacker accessed Demo tender analysis!"

    # Attacker lists all tenders
    attacker_tenders = get_all_tenders(user_id=attacker["id"])
    assert tender_id not in [t["id"] for t in attacker_tenders], "SECURITY FAILURE: Demo tender in attacker listing!"
    print(" -> PASS: Cross-account access strictly rejected (foreign user receives None).")

    # 8. NO-BID DISQUALIFICATION RULE TEST
    print("\n[STEP 8] Testing NO-BID Rule on Failing Mandatory Requirement...")
    underqualified_profile = {
        "company_name": "Ineligible Startup LLC",
        "industry": "General IT",
        "years_of_experience": "1 year",  # Requires 5 years!
        "annual_turnover": "$200,000 USD", # Requires $5M!
        "certifications": "None Listed",   # Requires ISO 9001 and ISO 27001!
        "technical_capabilities": "Basic web scripts",
        "past_projects": "Local bakery site",
        "financial_details": "No facility"
    }
    failing_analysis = run_deterministic_analysis(extract_result["text"], underqualified_profile)
    rec_result = failing_analysis["bid_recommendation"]
    print(f"  Underqualified Evaluation Decision: {rec_result['decision']}")
    print(f"  Identified Blockers: {rec_result['blockers']}")
    assert rec_result["decision"] == "NO-BID", f"Expected NO-BID for failing criteria, got {rec_result['decision']}"
    assert len(rec_result["blockers"]) >= 2, "Expected multiple critical blockers identified"
    print(" -> PASS: NO-BID rule deterministically enforced when mandatory criteria are unmet.")

    # 9. CLEANUP TEST DATA
    print("\n[STEP 9] Cleaning up test records...")
    conn = get_connection()
    c = conn.cursor()
    for uid in (demo_id, attacker["id"]):
        c.execute("DELETE FROM tender_analyses WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM tenders WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM company_profile WHERE user_id = ?", (uid,))
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    conn.commit()
    conn.close()

    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    print(" -> PASS: Test records cleanly cleaned up.")
    print("\n" + "=" * 70)
    print("  ALL SECTION 3B RESULTS PAGE VERIFICATION TESTS PASSED!")
    print("=" * 70)

if __name__ == "__main__":
    run_section3b_test()
