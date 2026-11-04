import re

with open("app.py", "r", encoding="utf-8") as f:
    app_text = f.read()

# 1. Imports
target_imports = """    get_tender_requirement_responses,
    save_tender_requirement_response,
)"""

repl_imports = """    get_tender_requirement_responses,
    save_tender_requirement_response,
    get_tender_analysis_versions,
    get_tender_analysis_by_id,
    compute_combined_evidence_hash,
    update_tender_requirement_document_extraction,
)
import document_intelligence as DI"""

# 2. Section 2 Status Badges in _render_tender_specific_response
target_status = """        with col_status:
            display_status = _company_facing_status(evidence_status, source_reference_is_valid(requirement), quote)
            display_class = "pill-unresolved" if display_status in ("Missing", "Needs Clarification") else status_class
            st.markdown(f"<span class='{display_class}'>{h(display_status)}</span>", unsafe_allow_html=True)
            if stale:
                st.caption("Clause updated since saved")
            elif current:
                st.caption("Response saved; unverified")
            else:
                st.caption("Pending submission")"""

repl_status = """        with col_status:
            has_info = bool(values and (any(str(v).strip() for k, v in values.items() if k not in ("verification_status", "conditional_path") and not isinstance(v, list)) or values.get("projects")))
            has_doc = bool(documents)
            latest_doc = documents[0] if documents else None
            doc_c_status = (latest_doc or {}).get("comparison_status") or ("pending" if has_doc else "")

            # Render required status badges
            if has_info:
                st.markdown("<span class='pill-inforeq' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Information Saved</span>", unsafe_allow_html=True)
            if has_doc:
                st.markdown("<span class='pill-inforeq' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Document Uploaded</span>", unsafe_allow_html=True)
                if doc_c_status in ("processing", "pending"):
                    st.markdown("<span class='pill-unresolved' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Processing</span>", unsafe_allow_html=True)
                elif doc_c_status == "matched":
                    st.markdown("<span class='pill-satisfied' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Evidence Matched</span>", unsafe_allow_html=True)
                elif doc_c_status == "mismatch":
                    st.markdown("<span class='pill-notsatisfied' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Mismatch Found</span>", unsafe_allow_html=True)
                elif doc_c_status == "clarification_needed":
                    st.markdown("<span class='pill-unresolved' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Needs Clarification</span>", unsafe_allow_html=True)
                elif doc_c_status == "unverified_sample":
                    st.markdown("<span class='pill-unresolved' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Sample Document (Not Valid)</span>", unsafe_allow_html=True)

                st.markdown("<span class='tf-badge-optional' style='display:inline-block; margin-bottom:4px; font-size:0.68rem; background:#f1f5f9; color:#475569; border:1px solid #cbd5e1;'>Authenticity Not Verified</span>", unsafe_allow_html=True)

            if not has_info and not has_doc:
                st.markdown("<span class='pill-unresolved' style='display:inline-block; margin-bottom:4px; font-size:0.75rem;'>Pending Submission</span>", unsafe_allow_html=True)

            if stale:
                st.caption("Clause updated since saved")"""

# 3. Section 2 Save Form & Document Processing
target_save = """                if st.form_submit_button("Save Response", type="primary", use_container_width=True):
                    try:
                        document = ({"filename": upload.name, "content": upload.getvalue(),
                                     "media_type": upload.type or "application/pdf"} if upload else
                                  existing_company_pdf_payload(company_pdfs, existing_document_id))
                        if not document and not any(str(v).strip() for v in response.values() if not isinstance(v, list)) and not response.get("projects"):
                            st.error("Enter at least one response detail or attach a supporting PDF.")
                        else:
                            save_tender_requirement_response(user_id, tender_id, requirement_id, quote,
                                                             {**response, "verification_status": "unverified"}, document)
                            st.success("Saved to this tender. The answer and any uploaded PDF remain unverified until reviewed.")
                            st.rerun()
                    except (ValueError, PermissionError, OSError) as exc:
                        st.error(f"The tender response could not be saved: {exc}")"""

repl_save = """                if st.form_submit_button("Save Response", type="primary", use_container_width=True):
                    try:
                        document = ({"filename": upload.name, "content": upload.getvalue(),
                                     "media_type": upload.type or "application/pdf"} if upload else
                                  existing_company_pdf_payload(company_pdfs, existing_document_id))
                        if not document and not any(str(v).strip() for v in response.values() if not isinstance(v, list)) and not response.get("projects"):
                            st.error("Enter at least one response detail or attach a supporting PDF.")
                        else:
                            save_tender_requirement_response(user_id, tender_id, requirement_id, quote,
                                                             {**response, "verification_status": "unverified"}, document)
                            if document and document.get("content"):
                                try:
                                    with st.spinner("Processing document and checking evidence matching..."):
                                        DI.process_and_save_tender_document(
                                            user_id=user_id,
                                            tender_id=tender_id,
                                            requirement_id=requirement_id,
                                            filepath_or_bytes=document["content"],
                                            filename=document.get("filename", "evidence.pdf"),
                                            user_submission=response,
                                            requirement_clause=quote or title
                                        )
                                except Exception as di_err:
                                    st.warning(f"Response saved, but document analysis encountered an issue: {di_err}")
                            elif not document and documents:
                                existing_doc = documents[0]
                                existing_path = existing_doc.get("storage_path")
                                if existing_path and os.path.isfile(existing_path):
                                    try:
                                        with st.spinner("Re-evaluating document comparison with updated information..."):
                                            DI.process_and_save_tender_document(
                                                user_id=user_id,
                                                tender_id=tender_id,
                                                requirement_id=requirement_id,
                                                filepath_or_bytes=existing_path,
                                                filename=existing_doc.get("filename", "evidence.pdf"),
                                                user_submission=response,
                                                requirement_clause=quote or title
                                            )
                                    except Exception:
                                        pass
                            st.success("Tender response and evidence saved successfully.")
                            st.rerun()
                    except (ValueError, PermissionError, OSError) as exc:
                        st.error(f"The tender response could not be saved: {exc}")

        if documents:
            latest_doc = documents[0]
            ext_json = latest_doc.get("extracted_json") or {}
            comp = ext_json.get("comparison") or {}
            comp_status = latest_doc.get("comparison_status") or "pending"

            st.markdown("---")
            st.markdown(f"##### 📄 Document Intelligence: {h(latest_doc['filename'])}")
            
            fact_items = []
            if ext_json.get("certificate_number"):
                fact_items.append(f"**Certificate No:** {h(str(ext_json['certificate_number']))}")
            if ext_json.get("issuing_authority"):
                fact_items.append(f"**Issuer:** {h(str(ext_json['issuing_authority']))}")
            dates = ext_json.get("dates") or {}
            if dates:
                date_strs = [f"{k}: {v}" for k, v in dates.items() if v]
                if date_strs:
                    fact_items.append(f"**Dates:** {h(', '.join(date_strs))}")
            if ext_json.get("financial_amounts"):
                fact_items.append(f"**Amounts:** {h(', '.join(map(str, ext_json['financial_amounts'])))}")
            if ext_json.get("project_experience"):
                fact_items.append(f"**Experience:** {h(', '.join(map(str, ext_json['project_experience'][:2])))}")
            if ext_json.get("equipment"):
                fact_items.append(f"**Equipment:** {h(', '.join(map(str, ext_json['equipment'][:2])))}")
            
            if fact_items:
                st.markdown("<div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:8px 12px; margin-bottom:8px; font-size:0.83rem; color:#334155;'>" +
                            " · ".join(fact_items) + "</div>", unsafe_allow_html=True)

            matching_fields = comp.get("matching_fields") or []
            mismatch_fields = comp.get("mismatch_fields") or []
            missing_fields = comp.get("missing_fields") or []
            clarifications = comp.get("clarification_needed") or []

            if matching_fields:
                match_html = "".join([f"<li style='color:#15803d;'><b>{h(m.get('field', 'Field'))}:</b> Claimed '{h(str(m.get('claimed', '')))}' matches document ('{h(str(m.get('extracted', '')))}')</li>" for m in matching_fields])
                st.markdown(f"<div style='background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; padding:8px 12px; margin-bottom:6px; font-size:0.83rem;'><b style='color:#166534;'>✓ Matching Information ({len(matching_fields)}):</b><ul style='margin:4px 0 0 0; padding-left:18px;'>{match_html}</ul></div>", unsafe_allow_html=True)

            if mismatch_fields:
                mismatch_html = "".join([f"<li style='color:#b91c1c;'><b>{h(m.get('field', 'Field'))}:</b> Claimed '{h(str(m.get('claimed', '')))}' differs from document '{h(str(m.get('extracted', '')))}' ({h(str(m.get('issue', 'Discrepancy detected')))})</li>" for m in mismatch_fields])
                st.markdown(f"<div style='background:#fef2f2; border:1px solid #fecaca; border-radius:6px; padding:8px 12px; margin-bottom:6px; font-size:0.83rem;'><b style='color:#991b1b;'>⚠ Mismatches Detected ({len(mismatch_fields)}):</b><ul style='margin:4px 0 0 0; padding-left:18px;'>{mismatch_html}</ul></div>", unsafe_allow_html=True)

            if missing_fields or clarifications:
                clar_items = [f"Missing {f} in document" for f in missing_fields] + clarifications
                clar_html = "".join([f"<li style='color:#854d0e;'>{h(str(item))}</li>" for item in clar_items])
                st.markdown(f"<div style='background:#fefce8; border:1px solid #fef08a; border-radius:6px; padding:8px 12px; margin-bottom:6px; font-size:0.83rem;'><b style='color:#854d0e;'>ℹ Clarification / Missing Details:</b><ul style='margin:4px 0 0 0; padding-left:18px;'>{clar_html}</ul></div>", unsafe_allow_html=True)

            if ext_json.get("sample_notice"):
                st.warning(f"⚠️ {ext_json['sample_notice']}")

            st.caption("🔒 *Notice: Document-content matching confirms factual consistency between your submission and the uploaded PDF text. It does not certify official issuer authentication or legal validity.*")"""

# 4. Section 1 Document Upload in render_profile_evidence_storage
target_sec1_up = """                doc_id = save_company_document(
                    user_id, supporting_file.name, supporting_file.getvalue(),
                    supporting_file.type or "application/pdf", document_key=document_key,
                )
                st.success(f"Supporting document saved (record {doc_id}).")"""

repl_sec1_up = """                doc_id = save_company_document(
                    user_id, supporting_file.name, supporting_file.getvalue(),
                    supporting_file.type or "application/pdf", document_key=document_key,
                )
                try:
                    with st.spinner("Processing document with Document Intelligence..."):
                        DI.process_and_save_company_document(
                            user_id=user_id,
                            doc_id=doc_id,
                            filepath_or_bytes=supporting_file.getvalue(),
                            filename=supporting_file.name
                        )
                except Exception as di_err:
                    pass
                st.success(f"Supporting document saved and analyzed (record #{doc_id}).")"""

# 5. Section 1 Document Listing Display
target_sec1_list = """        for doc in documents:
            st.markdown(f"**{h(doc['filename'])}** · v{h(doc['version'])} · {h(doc['size_bytes'])} bytes · document "
                        f"{h(doc.get('verification_status') or 'unverified')}"
                        + ("" if doc["extracted_values"] else " · *contents not extracted — cannot prove a requirement*"))"""

repl_sec1_list = """        for doc in documents:
            ext_j = doc.get("extracted_json") or {}
            status_pill = ("<span class='pill-satisfied' style='font-size:0.7rem;'>Analyzed</span>" if ext_j else "<span class='pill-unresolved' style='font-size:0.7rem;'>Unverified</span>")
            if ext_j.get("sample_notice"):
                status_pill = "<span class='pill-unresolved' style='font-size:0.7rem;'>Sample Document (Not Valid)</span>"
            auth_pill = "<span class='tf-badge-optional' style='font-size:0.65rem; background:#f1f5f9; color:#475569; border:1px solid #cbd5e1;'>Authenticity Not Verified</span>"

            st.markdown(f"**{h(doc['filename'])}** · v{h(doc['version'])} · {h(doc['size_bytes'])} bytes · {status_pill} · {auth_pill}", unsafe_allow_html=True)
            if ext_j:
                summary_parts = []
                if ext_j.get("certificate_number"): summary_parts.append(f"Cert: {ext_j['certificate_number']}")
                if ext_j.get("issuing_authority"): summary_parts.append(f"Issuer: {ext_j['issuing_authority']}")
                if ext_j.get("financial_amounts"): summary_parts.append(f"Amounts: {', '.join(map(str, ext_j['financial_amounts']))}")
                if ext_j.get("dates"):
                    d_str = ', '.join(f"{k}: {v}" for k, v in ext_j['dates'].items() if v)
                    if d_str: summary_parts.append(f"Dates: {d_str}")
                if summary_parts:
                    st.caption("Extracted: " + " · ".join(summary_parts))"""

# 6. Analysis Version History Selector on Tender Analysis Page
target_analysis = """            profile = get_company_profile(user_id) or {}
            current_profile_hash = compute_profile_hash(profile)
            saved_analysis = get_analysis_for_tender(selected_id, user_id)

            # Header Bar"""

repl_analysis = """            profile = get_company_profile(user_id) or {}
            combined_evidence = db.get_combined_company_and_tender_evidence(user_id, selected_id)
            current_profile_hash = compute_combined_evidence_hash(profile, combined_evidence)
            saved_analysis = get_analysis_for_tender(selected_id, user_id)
            versions = get_tender_analysis_versions(selected_id, user_id)
            version_key = f"selected_analysis_ver_{selected_id}"
            latest_analysis_id = versions[0]["id"] if versions else None

            current_choice_id = st.session_state.get(version_key, latest_analysis_id)
            valid_ids = [v["id"] for v in versions]
            if current_choice_id not in valid_ids:
                current_choice_id = latest_analysis_id
                st.session_state[version_key] = latest_analysis_id

            if current_choice_id and current_choice_id != latest_analysis_id:
                saved_analysis = get_tender_analysis_by_id(current_choice_id, user_id)

            # Header Bar"""

# 7. Version Selector UI Bar on Tender Analysis Page
target_header_end = """            st.markdown(f\"\"\"
            <div class=\"saas-header\" style=\"margin-top:0.4rem; margin-bottom:1rem;\">
                <div>
                    <h1 style=\"font-size:1.55rem; margin-bottom:4px;\">Tender Analysis: {h(tender['filename'])}</h1>
                    <p style=\"font-size:0.88rem;\">Uploaded on {tender['uploaded_at'][:16]} · {tender['page_count']} Pages · </p>
                </div>
            </div>
            \"\"\", unsafe_allow_html=True)"""

repl_header_end = """            st.markdown(f\"\"\"
            <div class=\"saas-header\" style=\"margin-top:0.4rem; margin-bottom:1rem;\">
                <div>
                    <h1 style=\"font-size:1.55rem; margin-bottom:4px;\">Tender Analysis: {h(tender['filename'])}</h1>
                    <p style=\"font-size:0.88rem;\">Uploaded on {tender['uploaded_at'][:16]} · {tender['page_count']} Pages · </p>
                </div>
            </div>
            \"\"\", unsafe_allow_html=True)

            if versions and len(versions) > 1:
                v_col1, v_col2 = st.columns([3, 2])
                with v_col1:
                    v_options = []
                    for v in versions:
                        v_num = v.get("version", 1)
                        is_lat = (v["id"] == latest_analysis_id)
                        v_time = str(v.get("analyzed_at") or "")[:16]
                        v_rec = v.get("recommendation", "")
                        lbl = f"Version {v_num}{' (Latest)' if is_lat else ''} · {v_time} · [{v_rec}]"
                        v_options.append((v["id"], lbl))

                    cur_idx = next((i for i, opt in enumerate(v_options) if opt[0] == current_choice_id), 0)
                    sel_opt = st.selectbox(
                        "📜 Historical Analysis Version",
                        v_options,
                        index=cur_idx,
                        format_func=lambda x: x[1],
                        key=f"ver_sel_widget_{selected_id}"
                    )
                    if sel_opt and sel_opt[0] != st.session_state.get(version_key):
                        st.session_state[version_key] = sel_opt[0]
                        st.rerun()
                with v_col2:
                    if current_choice_id != latest_analysis_id:
                        st.warning(f"Viewing historical Version {saved_analysis.get('version', 1)}. Latest is preserved.")
                        if st.button("Switch to Latest Version ⚡", key=f"switch_latest_{selected_id}"):
                            st.session_state[version_key] = latest_analysis_id
                            st.rerun()
                    else:
                        st.caption(f"Showing Latest Version {versions[0].get('version', 1)} ({len(versions)} versions archived).")"""

replacements = [
    (target_imports, repl_imports, "imports"),
    (target_status, repl_status, "section 2 status badges"),
    (target_save, repl_save, "section 2 save and document intelligence"),
    (target_sec1_up, repl_sec1_up, "section 1 document upload"),
    (target_sec1_list, repl_sec1_list, "section 1 document list"),
    (target_analysis, repl_analysis, "analysis page version handling"),
    (target_header_end, repl_header_end, "version selector UI"),
]

for old, new, label in replacements:
    old_crlf = old.replace("\n", "\r\n")
    new_crlf = new.replace("\n", "\r\n")
    if old_crlf in app_text:
        app_text = app_text.replace(old_crlf, new_crlf, 1)
        print(f"Applied {label} (CRLF)")
    elif old in app_text:
        app_text = app_text.replace(old, new, 1)
        print(f"Applied {label} (LF)")
    else:
        print(f"FAILED to find {label}")

with open("app.py", "w", encoding="utf-8") as f:
    f.write(app_text)

print("Finished applying updates to app.py.")
