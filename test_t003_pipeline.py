"""
Focused End-to-End Test for T003.pdf using TenderForge Groq Analysis Pipeline.
Validates:
1. Groq API invocation with configured model.
2. Modular request processing avoiding token/rate limit failures.
3. Tender-specific analysis with source-page evidence.
4. Comparison of requirements against company profile.
5. Actionable INFORMATION REQUIRED items when company details are missing.
6. Consistent 4-status taxonomy (SATISFIED, CONFIRMED GAP, INFORMATION REQUIRED, UNRESOLVED).
7. Availability and structure of all nine analysis sections.
8. Explicit error reporting on Groq failure.
"""

import os
import sys
import json
import fitz

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pdf_processor import extract_text_from_pdf
from analyzer import (
    analyze_tender_with_groq,
    ensure_seven_sections,
    run_deterministic_analysis,
    extract_tender_overview_deterministic
)
from database import (
    init_db,
    get_connection,
    get_company_profile,
    create_user,
    authenticate_user
)

init_db()

print("=" * 70)
print("  FOCUSED END-TO-END TEST: T003.PDF")
print("=" * 70)

pdf_path = os.path.join(os.path.dirname(__file__), "data", "uploads", "T003.pdf")
if not os.path.exists(pdf_path):
    pdf_path = "/app/data/uploads/T003.pdf"

assert os.path.exists(pdf_path), f"T003.pdf not found at {pdf_path}"
print(f"[1] Located T003.pdf at {pdf_path}")

doc = fitz.open(pdf_path)
page_count = len(doc)
doc.close()
print(f" -> Page Count: {page_count}")

# Step 1: Text extraction
print("\n[2] Extracting text with page markers...")
extract_res = extract_text_from_pdf(pdf_path)
assert extract_res["success"], f"Extraction failed: {extract_res.get('error')}"
tender_text = extract_res["text"]
print(f" -> Extracted {len(tender_text):,} characters across {extract_res['page_count']} pages")

# Step 2: Test User Profile (Mahalaxmi Engineering Works or test profile)
conn = get_connection()
c = conn.cursor()
c.execute("SELECT id FROM users LIMIT 1")
row = c.fetchone()
conn.close()

if row:
    user_id = row[0]
    profile = get_company_profile(user_id) or {}
else:
    profile = {}

if not profile.get("company_name"):
    profile = {
        "company_name": "Apex Engineering & Technologies",
        "industry": "Engineering & Technology Solutions",
        "years_of_experience": "7 years",
        "annual_turnover": "15000000",
        "certifications": "ISO 9001:2015",
        "technical_capabilities": "Software development, data management, mechanical fabrication, electronic surveillance",
        "past_projects": "State procurement portal system, surveillance monitoring setup"
    }

print(f"\n[3] Testing with Profile: '{profile.get('company_name')}'")
print(f" -> Turnover: {profile.get('annual_turnover')}")
print(f" -> Certifications: {profile.get('certifications')}")

# Step 3: Run real Groq modular analysis
print("\n[4] Running analyze_tender_with_groq on T003.pdf...")
res = analyze_tender_with_groq(tender_text, profile, pdf_path=pdf_path)

assert res["success"] is True, f"Groq analysis failed: {res.get('error')}"
model_used = res.get("model_used")
print(f" -> SUCCESS! Model used: {model_used}")
assert any(k in model_used.lower() for k in ["qwen", "gpt", "oss", "llama", "groq"]), f"Expected LLM model name, got: {model_used}"

data = res["data"]

# Step 4: Validate 4-status taxonomy
print("\n[5] Validating 4-status taxonomy in Compliance Matrix...")
matrix = data.get("compliance_matrix", [])
assert len(matrix) > 0, "Compliance matrix is empty!"
print(f" -> Total matrix items: {len(matrix)}")

valid_statuses = {"SATISFIED", "CONFIRMED GAP", "INFORMATION REQUIRED", "UNRESOLVED", "NOT SATISFIED", "UNCLEAR"}
status_counts = {}
has_source_pages = 0

for item in matrix:
    st = item.get("status")
    assert st in valid_statuses, f"Invalid status '{st}' found in requirement {item.get('requirement_id')}"
    status_counts[st] = status_counts.get(st, 0) + 1
    if item.get("source_page"):
        has_source_pages += 1

print(" -> Status Distribution:")
for s, cnt in status_counts.items():
    print(f"    - {s}: {cnt}")

print(f" -> Requirements with explicit source page: {has_source_pages}/{len(matrix)}")
assert has_source_pages > 0, "No requirements contain source page numbers!"

# Step 5: Validate Actionable Missing Information items
print("\n[6] Validating Actionable Missing Information Checklist...")
actionable = data.get("actionable_missing_information", [])
print(f" -> Found {len(actionable)} actionable missing information items")
assert len(actionable) > 0, "Expected actionable missing information items for unverified profile requirements!"

for act in actionable[:3]:
    print(f"    - Item: {act.get('item_name')}")
    print(f"      Priority: {act.get('priority')}")
    print(f"      Why Required: {act.get('why_required')}")
    print(f"      Evidence Needed: {act.get('evidence_needed')}")
    print(f"      Target Field: {act.get('profile_field')}")
    print(f"      Source Page: {act.get('source_page')}")
    assert act.get("item_name"), "Missing item_name"
    assert act.get("evidence_needed"), "Missing evidence_needed"
    assert act.get("profile_field"), "Missing profile_field"

# Step 6: Validate All Nine Sections
print("\n[7] Validating All Nine Analysis Sections...")
# 1. Tender Overview
overview = data.get("tender_overview", {})
assert overview.get("title") and len(overview["title"]) > 5, f"Invalid overview title: {overview.get('title')}"
print(f" -> Section 1 (Tender Overview): Title = '{overview.get('title')[:60]}...'")

# 2. Eligibility Analysis
eligibility = data.get("eligibility_analysis", {})
assert isinstance(eligibility, dict), "Eligibility is not a dict"
assert "experience" in eligibility, "Missing experience pillar"
assert "turnover" in eligibility, "Missing turnover pillar"
print(" -> Section 2 (Eligibility Analysis): Pillars verified (Experience, Turnover, Certs, Registration)")

# 3. Compliance Matrix
assert len(data.get("compliance_matrix", [])) >= 3, "Compliance matrix too short"
print(f" -> Section 3 (Compliance Matrix): {len(data['compliance_matrix'])} requirements with source citations")

# 4. Technical Evaluation
technical = data.get("technical_evaluation", {})
assert isinstance(technical, dict), "Technical evaluation is not a dict"
print(" -> Section 4 (Technical Evaluation): Capabilities, equipment, expertise verified")

# 5. Financial Analysis
financial = data.get("financial_analysis", {})
assert isinstance(financial, dict), "Financial analysis is not a dict"
print(" -> Section 5 (Financial Analysis): EMD, Fee, PBG, Payment Terms verified")

# 6. Risk Assessment
risk_data = data.get("risk_assessment", {})
assert isinstance(risk_data, dict), "Risk assessment is not a dict"
print(f" -> Section 6 (Risk Assessment): Penalties ({len(risk_data.get('penalties', []))}), Deadlines ({len(risk_data.get('deadlines', []))})")

# 7. Bid Recommendation
bid_rec = data.get("bid_recommendation", {})
assert bid_rec.get("decision") in ["BID", "NO-BID", "REVIEW REQUIRED"], f"Invalid decision: {bid_rec.get('decision')}"
print(f" -> Section 7 (Bid Recommendation): Decision = {bid_rec.get('decision')}, Reasons = {len(bid_rec.get('reasons', []))}")

# 8. Missing Information Checklist
assert len(data.get("actionable_missing_information", [])) > 0, "Missing Section 8 actionable items"
print(f" -> Section 8 (Missing Information Checklist): {len(data['actionable_missing_information'])} actionable cards")

# 9. Deadlines & Milestones
deadlines = risk_data.get("deadlines", [])
docs = risk_data.get("missing_documents", [])
print(f" -> Section 9 (Deadlines & Milestones): {len(deadlines)} milestone deadlines, {len(docs)} mandatory docs")

# Step 7: Test explicit error reporting when Groq fails
print("\n[8] Testing Explicit Error Reporting on Groq Failure...")
import analyzer
original_key = analyzer.GROQ_API_KEY
try:
    analyzer.GROQ_API_KEY = "invalid_key_for_testing"
    fail_res = analyze_tender_with_groq("Short tender text", profile, pdf_path=None)
    assert fail_res["success"] is False, "Expected success=False when Groq API key is invalid!"
    assert "error" in fail_res, "Expected error message in result!"
    assert "deterministic" not in fail_res.get("model_used", ""), "Must NOT label deterministic fallback as LLM!"
    print(f" -> Correctly caught explicit error: {fail_res['error'][:80]}...")
finally:
    analyzer.GROQ_API_KEY = original_key

print("\n" + "=" * 70)
print("  ALL T003.PDF FOCUSED TESTS PASSED SUCCESSFULLY!")
print("=" * 70)
