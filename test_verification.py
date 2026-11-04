"""
End-to-End Test for TenderForge Section 2 Features:
- Company Profile Persistence
- Tender PDF Extraction & DB Storage
- Evidence-Based Compliance Matrix Validation
- Deterministic Rule Enforcement (Pass/Fail/Review)
- SQLite Analysis Caching & Profile Hash Validation
- Real Dashboard Stats Computation
"""

import fitz
import os
import sys

from database import (
    init_db, save_company_profile, get_company_profile,
    save_tender, get_all_tenders, get_tender_by_id,
    save_analysis, get_analysis_for_tender, compute_profile_hash,
    get_dashboard_stats, get_tenders_with_analysis,
    create_user, authenticate_user, get_connection
)
from analyzer import run_deterministic_checks, enforce_deterministic_rules

init_db()
test_email = "apex_tester@apexdefense.internal"
test_pw = "ApexPassword123!"

ok, msg, user = authenticate_user(test_email, test_pw)
if not user:
    ok, msg, user = create_user(test_email, test_pw, "Eleanor Vance")
user_id = user["id"]

print("=== 1. TEST COMPANY PROFILE ===")
profile_data = {
    "company_name": "Apex Defense & Software Solutions",
    "industry": "Defense IT & Cloud Security",
    "years_of_experience": "8 years",
    "annual_turnover": "$12,000,000 USD",
    "certifications": "ISO 9001:2015, ISO 27001:2022, CMMI Level 3",
    "technical_capabilities": "Cloud Architecture, Zero Trust Security, Python/Kubernetes microservices",
    "past_projects": "Department of Defense logistics portal ($4.5M, 2024)",
    "financial_details": "Audited profitable for 5 consecutive years; $3M bank guarantee facility",
    "contact_name": "Eleanor Vance",
    "contact_email": "evance@apexdefense.com",
    "contact_phone": "+1 (555) 234-5678",
    "contact_address": "400 Federal Plaza, Suite 900, Arlington, VA"
}
save_company_profile(profile_data, user_id=user_id)
saved_profile = get_company_profile(user_id=user_id)
assert saved_profile["company_name"] == profile_data["company_name"], "Profile save mismatch"
hash_val = compute_profile_hash(saved_profile)
print("Profile saved successfully! Hash:", hash_val)

print("\n=== 2. TEST REPRESENTATIVE TENDER PDF & EXTRACTION ===")
pdf_path = "/app/data/uploads/sample_rfp_tender.pdf" if os.path.exists("/app") else os.path.join(os.path.dirname(__file__), "data", "uploads", "sample_rfp_tender.pdf")
os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
doc = fitz.open()
page = doc.new_page()
tender_text = """
REQUEST FOR PROPOSAL (RFP) # US-DOD-2026-09
TITLE: Enterprise Secure Cloud Data Ingestion Platform
ISSUING AUTHORITY: Federal Technology Procurement Division

1. ELIGIBILITY CRITERIA:
- The bidder must have at least 5 years of experience in enterprise cloud deployments.
- Bidder must hold valid ISO 9001 and ISO 27001 certifications at the time of bid submission.
- Minimum average annual turnover of $5,000,000 USD over the last 3 financial years.

2. TECHNICAL REQUIREMENTS:
- Proven experience with Python-based scalable data pipelines and Kubernetes orchestration.
- 24/7 dedicated incident support with 1-hour SLA.

3. MANDATORY DOCUMENTS:
- Audited balance sheets for past 3 years.
- ISO certificates copy.
- Solvency Certificate from a nationalized bank.

4. DEADLINES & MILESTONES:
- Bid Submission Deadline: 2026-11-15 at 17:00 EST.
- Pre-bid clarification queries deadline: 2026-10-30.

5. RISK & PENALTY TERMS:
- Liquidated damages of 0.5% per week of delay up to a maximum of 10% contract value.
- Performance Bank Guarantee (PBG) of 5% contract value required upon award.
"""
page.insert_text((50, 50), tender_text, fontsize=10)
doc.save(pdf_path)
doc.close()

from pdf_processor import extract_text_from_pdf
extract_res = extract_text_from_pdf(pdf_path)
assert extract_res["success"] is True, "PDF extraction failed"
print("PDF text extracted! Page count:", extract_res["page_count"], "Length:", len(extract_res["text"]))

tender_id = save_tender(
    filename="sample_rfp_tender.pdf",
    filepath=pdf_path,
    extracted_text=extract_res["text"],
    page_count=extract_res["page_count"],
    upload_status="success",
    user_id=user_id
)
print("Tender saved to DB with ID:", tender_id)

print("\n=== 3. TEST DETERMINISTIC CHECKS & EVIDENCE INTEGRITY ===")
det_checks = run_deterministic_checks(extract_res["text"], saved_profile)
print(f"Found {len(det_checks)} deterministic qualification checks:")
for c in det_checks:
    print(f" - [{c['requirement_id']}] {c['status']}: {c['explanation']}")

print("\n=== 4. TEST EVIDENCE-BASED ANALYSIS PERSISTENCE & RETRIEVAL ===")
structured_data = {
    "deadlines": [
        {"event": "Bid Submission Deadline", "date": "2026-11-15 at 17:00 EST", "notes": "Late bids rejected"},
        {"event": "Pre-bid Clarification Queries", "date": "2026-10-30", "notes": "Electronic submission"}
    ],
    "required_documents": [
        "Audited balance sheets for past 3 years",
        "ISO 9001 and ISO 27001 certificate copies",
        "Bank Solvency Certificate"
    ]
}

compliance_matrix = [
    {
        "requirement_id": "REQ-ELIG-01",
        "category": "Eligibility",
        "requirement": "Minimum 5 years experience in enterprise cloud deployments",
        "tender_evidence": "The bidder must have at least 5 years of experience in enterprise cloud deployments.",
        "company_evidence": "Years in Business: 8 years in Defense IT & Cloud Security",
        "status": "SATISFIED",
        "is_critical": True,
        "explanation": "Company possesses 8 years of relevant enterprise experience, exceeding the 5-year requirement."
    },
    {
        "requirement_id": "REQ-CERT-01",
        "category": "Certifications",
        "requirement": "Valid ISO 9001 and ISO 27001 certifications",
        "tender_evidence": "Bidder must hold valid ISO 9001 and ISO 27001 certifications at the time of bid submission.",
        "company_evidence": "Certifications: ISO 9001:2015, ISO 27001:2022",
        "status": "SATISFIED",
        "is_critical": True,
        "explanation": "Company actively holds both ISO 9001:2015 and ISO 27001:2022 accreditations."
    },
    {
        "requirement_id": "REQ-FIN-01",
        "category": "Financial",
        "requirement": "Minimum average annual turnover of $5,000,000 USD",
        "tender_evidence": "Minimum average annual turnover of $5,000,000 USD over the last 3 financial years.",
        "company_evidence": "Annual Turnover: $12,000,000 USD",
        "status": "SATISFIED",
        "is_critical": True,
        "explanation": "Annual turnover of $12M USD substantially exceeds the $5M USD threshold."
    },
    {
        "requirement_id": "REQ-TECH-01",
        "category": "Technical",
        "requirement": "24/7 dedicated incident support with 1-hour SLA",
        "tender_evidence": "24/7 dedicated incident support with 1-hour SLA.",
        "company_evidence": "No evidence found in company profile",
        "status": "UNCLEAR",
        "is_critical": False,
        "explanation": "Company profile lists technical capabilities but does not explicitly document SLA-backed 24/7 support tier."
    }
]

risks = [
    {
        "risk": "Liquidated damages of 0.5% per week of delay up to 10% total contract value.",
        "severity": "MEDIUM",
        "mitigation": "Ensure strict delivery milestone buffers in project plan and schedule."
    },
    {
        "risk": "5% Performance Bank Guarantee (PBG) commitment upon contract award.",
        "severity": "LOW",
        "mitigation": "Company holds $3M bank guarantee facility which readily accommodates this requirement."
    }
]

missing_info = [
    "Confirm whether 24/7 support tier requires on-site clearance or remote operations center.",
    "Obtain updated bank solvency certificate formatted specifically for this RFP authority."
]

mock_llm_payload = {
    "executive_summary": "Strong strategic and technical fit for US-DOD-2026-09. All core eligibility and financial criteria are satisfied with documented evidence. Minor clarification required on 24/7 support tier SLA commitments.",
    "compliance_matrix": compliance_matrix,
    "deadlines": structured_data["deadlines"],
    "required_documents": structured_data["required_documents"],
    "risks": risks,
    "missing_information": missing_info,
    "recommendation": "BID",
    "recommendation_rationale": "All critical eligibility, experience, and certification criteria are SATISFIED."
}

validated_data = enforce_deterministic_rules(mock_llm_payload, saved_profile, det_checks)
print("Validated Recommendation:", validated_data["recommendation"])
assert validated_data["recommendation"] == "REVIEW REQUIRED", "Expected REVIEW REQUIRED due to unclear SLA requirement"

analysis_id = save_analysis(
    tender_id=tender_id,
    profile_hash=hash_val,
    recommendation=validated_data["recommendation"],
    executive_summary=validated_data["executive_summary"],
    structured_tender_data=structured_data,
    compliance_matrix=validated_data["compliance_matrix"],
    risks=validated_data["risks"],
    missing_information=validated_data["missing_information"],
    model_used="llama-3.3-70b-versatile",
    user_id=user_id
)
print("Analysis saved with ID:", analysis_id)

print("\n=== 5. TEST CACHED RETRIEVAL & DASHBOARD STATS ===")
retrieved = get_analysis_for_tender(tender_id, user_id=user_id)
assert retrieved is not None, "Failed to retrieve analysis"
assert retrieved["profile_hash"] == hash_val, "Profile hash mismatch"
assert retrieved["recommendation"] == "REVIEW REQUIRED", "Recommendation mismatch"
assert len(retrieved["compliance_matrix"]) > 0, "Empty compliance matrix"
print("Retrieved successfully from SQLite! Profile hash match:", retrieved["profile_hash"] == hash_val)

dash_stats = get_dashboard_stats(user_id=user_id)
print("Real Dashboard Stats:", dash_stats)
assert dash_stats["total_tenders"] >= 1
assert dash_stats["analyzed_tenders"] >= 1
assert dash_stats["review_count"] >= 1

tenders_list = get_tenders_with_analysis(user_id=user_id)
print(f"Tenders with analysis list count: {len(tenders_list)}")
for t in tenders_list:
    print(f" - Tender: {t['filename']} -> Recommendation: {t['recommendation']}")

print("\n=== 6. TEST DETERMINISTIC NO-BID ENFORCEMENT ===")
# If profile experience is only 2 years and tender requires 5 years -> Must be NO-BID
low_exp_profile = dict(saved_profile)
low_exp_profile["years_of_experience"] = "2 years"
det_fail_checks = run_deterministic_checks(extract_res["text"], low_exp_profile)
fail_eval = enforce_deterministic_rules(
    {"compliance_matrix": compliance_matrix, "recommendation": "BID"},
    low_exp_profile,
    det_fail_checks
)
print("Low Experience Enforced Recommendation:", fail_eval["recommendation"])
assert fail_eval["recommendation"] == "NO-BID", "Must be NO-BID when experience falls short of mandatory minimum"

# Clean up test records
conn = get_connection()
c = conn.cursor()
c.execute("DELETE FROM tender_analyses WHERE user_id = ?", (user_id,))
c.execute("DELETE FROM tenders WHERE user_id = ?", (user_id,))
c.execute("DELETE FROM company_profile WHERE user_id = ?", (user_id,))
c.execute("DELETE FROM users WHERE id = ?", (user_id,))
conn.commit()
conn.close()

if os.path.exists(pdf_path):
    os.remove(pdf_path)

print("Test records and sample PDF cleaned up.")
print("\nALL VERIFICATION TESTS COMPLETED SUCCESSFULLY!")
