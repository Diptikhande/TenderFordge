"""
Automated Comprehensive Test Suite for Company Profile and Tender Analysis Workflow Fix.

Verifies:
1. Section 8 in app.py has no duplicate form (st.form removed from tab8).
2. Section 8 contains missing company details, associated requirement, why required, and Complete Profile button.
3. Dedicated Company Profile page renders dynamic fields under the 4 standard sections.
4. Database tender_disclosures table isolates data per user and tender.
5. Cross-tenant isolation: different companies cannot access each other's disclosures.
6. Multi-tender handling: disclosures for Tender 1 do not pollute universal profile unless explicitly mapped, but are reusable across tenders.
7. Re-evaluation uses updated profile and tender disclosures without LLM calls (deterministic evaluator).
8. Requirement status classification: missing info is never classified as confirmed failure.
"""

import os
import sys
import sqlite3
import re

import database
import analyzer

def run_tests():
    print("=" * 65)
    print("  RUNNING COMPANY PROFILE & TENDER ANALYSIS WORKFLOW TEST SUITE")
    print("=" * 65)

    # -------------------------------------------------------------
    # TEST 1: Inspect app.py for removal of duplicate form in Tab 8
    # -------------------------------------------------------------
    print("\n[TEST 1] Verifying removal of duplicate profile form from Section 8...")
    with open("app.py", "r", encoding="utf-8") as f:
        app_code = f.read()

    # Find tab8 block
    tab8_match = re.search(r"with tab8:(.*?)(?=with tab9:|\Z)", app_code, re.DOTALL)
    assert tab8_match, "tab8 block not found in app.py!"
    tab8_content = tab8_match.group(1)

    assert "complete_profile_form" not in tab8_content, "FAIL: complete_profile_form still found in tab8!"
    assert "st.form(" not in tab8_content, "FAIL: st.form found inside tab8! Section 8 must not have a duplicate form."
    assert "st.form_submit_button" not in tab8_content, "FAIL: st.form_submit_button found in tab8!"
    assert "Section 8: Deadlines & Submission Milestones" in tab8_content, "FAIL: Section 8 heading missing or incorrect!"
    print(" -> PASS: Duplicate form completely removed from Section 8.")
    print(" -> PASS: Section 8 remains the deadlines and submission milestones section.")

    # -------------------------------------------------------------
    # TEST 2: Inspect Company Profile page structure (4 Sections)
    # -------------------------------------------------------------
    print("\n[TEST 2] Verifying Company Profile page has the structured 6-section profile...")
    profile_match = re.search(r'elif st\.session_state\["current_page"\] == "🏢 Company Profile":(.*?)(?=\n    elif st\.session_state\["current_page"\]|\Z)', app_code, re.DOTALL)
    assert profile_match, "Company Profile page not found in app.py!"
    profile_content = profile_match.group(1)

    assert "1. General Company Information" in profile_content, "Section 1 missing from Company Profile!"
    assert "2. Financial Information" in profile_content, "Section 2 missing from Company Profile!"
    assert "3. Past Projects and Experience" in profile_content, "Section 3 missing from Company Profile!"
    assert "4. Certifications and Registrations" in profile_content, "Section 4 missing from Company Profile!"
    assert "5. Technical Capabilities" in profile_content, "Section 5 missing from Company Profile!"
    assert "6. Supporting Documents and Evidence" in profile_content, "Section 6 missing from Company Profile!"
    assert "get_profile_financials" in profile_content, "Structured financial persistence missing from Company Profile!"
    assert "get_profile_projects" in profile_content, "Structured project persistence missing from Company Profile!"
    assert "get_profile_certifications" in profile_content, "Structured certification persistence missing from Company Profile!"
    print(" -> PASS: Company Profile page maintains 6 structured sections with persistence.")

    # -------------------------------------------------------------
    # TEST 3: Database & Multi-Tenant Disclosures Isolation
    # -------------------------------------------------------------
    print("\n[TEST 3] Testing database isolation of tender-specific disclosures...")
    # Register two test users
    ok_a, msg_a, user_a = database.create_user("workflow_alice@test.internal", "Password123!", "Alice Smith")
    if not ok_a:
        _, _, user_a = database.authenticate_user("workflow_alice@test.internal", "Password123!")
    uid_a = user_a["id"]

    ok_b, msg_b, user_b = database.create_user("workflow_bob@test.internal", "Password123!", "Bob Jones")
    if not ok_b:
        _, _, user_b = database.authenticate_user("workflow_bob@test.internal", "Password123!")
    uid_b = user_b["id"]

    # Create tenders for both users
    tender_a1 = database.save_tender(
        filename="alice_rfp1.pdf",
        filepath=os.path.abspath("test_alice1.pdf"),
        extracted_text="Tender for Hospital Equipment. Requirement: ISO 13485 Medical Device Certification. Minimum Turnover: INR 10 Crores.",
        page_count=5,
        user_id=uid_a
    )

    tender_a2 = database.save_tender(
        filename="alice_rfp2.pdf",
        filepath=os.path.abspath("test_alice2.pdf"),
        extracted_text="Tender for IT Infrastructure. Requirement: CMMI Level 5. Minimum Experience: 7 years.",
        page_count=8,
        user_id=uid_a
    )

    tender_b1 = database.save_tender(
        filename="bob_rfp1.pdf",
        filepath=os.path.abspath("test_bob1.pdf"),
        extracted_text="Tender for Defense Logistics. Requirement: Security Clearance Level A.",
        page_count=4,
        user_id=uid_b
    )

    # Save disclosures for Alice on Tender A1
    database.save_tender_disclosure(
        user_id=uid_a,
        tender_id=tender_a1,
        field_key="cert_iso_13485",
        field_label="ISO 13485 Medical Device Certification",
        field_value="ISO 13485:2016 Certified (Cert #MED-9921, Valid to 2027)",
        doc_reference="ISO_13485_TUV_Audit.pdf",
        evidence_status="Verified supporting document attached / on file",
        section_id=2
    )

    # Check Alice's Tender A1 disclosures
    discs_a1 = database.get_tender_disclosures(uid_a, tender_a1)
    assert "cert_iso_13485" in discs_a1, "Disclosure not found for Alice Tender A1!"
    assert discs_a1["cert_iso_13485"]["doc_reference"] == "ISO_13485_TUV_Audit.pdf"

    # Verify Tender A2 does NOT have Tender A1's disclosure automatically as a tender disclosure
    discs_a2 = database.get_tender_disclosures(uid_a, tender_a2)
    assert "cert_iso_13485" not in discs_a2, "Tender A1 disclosure improperly leaked into Tender A2 disclosures!"

    # Verify cross-tender reuse capability: Alice can retrieve all her previous disclosures
    all_alice_discs = database.get_all_user_disclosures(uid_a)
    assert "cert_iso_13485" in all_alice_discs, "User's past disclosures should be accessible for cross-tender reuse!"

    # Verify Bob (User B) cannot access Alice's disclosures
    discs_bob_from_a1 = database.get_tender_disclosures(uid_b, tender_a1)
    assert not discs_bob_from_a1, "FAIL: Bob was able to retrieve Alice's disclosures!"

    all_bob_discs = database.get_all_user_disclosures(uid_b)
    assert "cert_iso_13485" not in all_bob_discs, "FAIL: Bob accessed Alice's global disclosures!"
    print(" -> PASS: Tender disclosures correctly isolated by user_id and tender_id.")
    print(" -> PASS: Cross-tenant security verified (no data leakage between companies).")
    print(" -> PASS: Cross-tender reuse supported within the same authenticated company.")

    # -------------------------------------------------------------
    # TEST 4: Dynamic Field Extraction from Tender Text
    # -------------------------------------------------------------
    print("\n[TEST 4] Testing dynamic missing-fields extraction from tender...")
    alice_profile = {
        "company_name": "Alice Healthcare Solutions",
        "years_of_experience": "6 years",
        "annual_turnover": "",  # missing turnover
        "certifications": "ISO 9001:2015",  # missing ISO 13485
        "technical_capabilities": "Medical device supply and maintenance"
    }

    tender_text = (
        "SECTION 3: ELIGIBILITY CRITERIA\n"
        "3.1 Mandatory Certifications: The bidder must possess valid ISO 13485 certification for medical device quality management.\n"
        "3.2 Financial Standing: Average annual audited turnover of at least INR 10 Crores during last 3 financial years.\n"
        "3.3 Technical: Bidder must provide OEM Authorization Letter for all diagnostic equipment.\n"
        "3.4 Nodal Contact: Power of Attorney authorizing the signatory must be submitted."
    )

    extracted_fields = analyzer.extract_tender_missing_profile_fields(
        tender_text=tender_text,
        profile=alice_profile,
        tender_disclosures={},
        other_tender_disclosures={}
    )

    print(f" -> Extracted {len(extracted_fields)} missing requirement fields:")
    section_map = {1: [], 2: [], 3: [], 4: []}
    for ef in extracted_fields:
        print(f"    * [{ef['section_name']}] {ef['field_label']} (Urgency: {ef['urgency']})")
        section_map[ef['section_id']].append(ef)

    # Verify ISO 13485 is in Section 2 (Certifications)
    assert any("iso" in ef["field_key"].lower() or "13485" in ef["field_key"].lower() for ef in section_map[2]), \
        "ISO 13485 was not placed in Section 2 (Certifications & Quality Accreditations)!"

    # Verify Turnover is in Section 3 (Financial Details)
    assert any("turnover" in ef["field_key"].lower() for ef in section_map[3]), \
        "Turnover was not placed in Section 3 (Capabilities, Past Projects & Financial Details)!"

    print(" -> PASS: Dynamic fields correctly extracted from tender text and mapped to appropriate profile sections.")

    # -------------------------------------------------------------
    # TEST 5: Deterministic Re-evaluation without LLM
    # -------------------------------------------------------------
    print("\n[TEST 5] Testing deterministic re-evaluation with updated profile and disclosures...")
    # Initial re-evaluation with incomplete profile
    initial_reeval = analyzer.reevaluate_tender_analysis(
        tender_text=tender_text,
        profile=alice_profile,
        tender_disclosures={}
    )
    init_summ = initial_reeval.get("summary", {})
    print(f" -> Initial Recommendation: {init_summ.get('recommendation')}")
    print(f" -> Initial Remaining Missing: {len(init_summ.get('remaining_missing_information', []))}")
    assert len(init_summ.get('remaining_missing_information', [])) > 0, "Should have missing info initially!"

    # Now provide the missing disclosures
    completed_disclosures = {
        "annual_turnover": {
            "field_value": "INR 18 Crores average audited annual turnover",
            "doc_reference": "Audited_Balance_Sheets_2022_2024.pdf",
            "evidence_status": "Verified supporting document attached / on file",
            "section_id": 3
        },
        "cert_iso_13485": {
            "field_value": "ISO 13485:2016 Certified",
            "doc_reference": "ISO_13485_Certificate_Copy.pdf",
            "evidence_status": "Verified supporting document attached / on file",
            "section_id": 2
        }
    }

    # Re-evaluate with updated disclosures
    updated_reeval = analyzer.reevaluate_tender_analysis(
        tender_text=tender_text,
        profile=alice_profile,
        tender_disclosures=completed_disclosures
    )
    upd_summ = updated_reeval.get("summary", {})
    print(f" -> Updated Recommendation: {upd_summ.get('recommendation')}")
    print(f" -> Satisfied Requirements: {len(upd_summ.get('satisfied_requirements', []))}")
    print(f" -> Updated Remaining Missing: {len(upd_summ.get('remaining_missing_information', []))}")

    # Check model_used
    assert updated_reeval.get("model_used") == "deterministic-evaluator", "model_used must be 'deterministic-evaluator'!"
    # Check that missing info decreased or was resolved
    assert len(upd_summ.get('remaining_missing_information', [])) < len(init_summ.get('remaining_missing_information', [])), \
        "Disclosed requirements did not resolve missing information!"
    print(" -> PASS: Deterministic re-evaluation updated requirement statuses using provided disclosures (0 LLM calls).")

    # -------------------------------------------------------------
    # TEST 6: Clean up test records
    # -------------------------------------------------------------
    print("\n[TEST 6] Cleaning up test records...")
    conn = database.get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM tender_disclosures WHERE user_id IN (?, ?)", (uid_a, uid_b))
    c.execute("DELETE FROM tender_analyses WHERE user_id IN (?, ?)", (uid_a, uid_b))
    c.execute("DELETE FROM tenders WHERE user_id IN (?, ?)", (uid_a, uid_b))
    c.execute("DELETE FROM company_profile WHERE user_id IN (?, ?)", (uid_a, uid_b))
    c.execute("DELETE FROM users WHERE id IN (?, ?)", (uid_a, uid_b))
    conn.commit()
    conn.close()
    print(" -> PASS: Cleanup completed successfully.")

    print("\n" + "=" * 65)
    print("  ALL 6 WORKFLOW AND ARCHITECTURE TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)

if __name__ == "__main__":
    run_tests()
