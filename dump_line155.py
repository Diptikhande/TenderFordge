import re

with open('app.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update import
old_import = '''from tender_response_utils import (record_analysis_outcome, resolve_tender_id,
                                   response_evidence_status, source_reference_is_valid,
                                   existing_company_pdf_payload,
                                   classify_requirement_type,
                                   requirement_needs_information,
                                   requirement_needs_documents,
                                   filter_requirement_records, requirement_group,
                                   requirement_title, simple_status_label)'''
new_import = '''from tender_response_utils import (record_analysis_outcome, resolve_tender_id,
                                   response_evidence_status, source_reference_is_valid,
                                   existing_company_pdf_payload,
                                   classify_requirement_type,
                                   requirement_needs_information,
                                   requirement_needs_documents,
                                   filter_requirement_records, requirement_group,
                                   requirement_title, simple_status_label,
                                   information_required_label, documents_required_label)'''

assert old_import in text, 'old_import not found!'
text = text.replace(old_import, new_import, 1)

# 2. Update _render_tender_specific_response
idx_ts_start = text.index('def _render_tender_specific_response(')
idx_sync = text.index('\ndef _get_profile_field_sync', idx_ts_start)

new_ts_code = '''def _render_tender_specific_response(user_id: int, tender_summary: dict, requirement: dict,
                                     saved_responses: dict[str, dict], payment_value: str = "",
                                     selected_tender_id: int | None = None):
    """Collect tender-only answers and PDFs without adding them to the company profile."""
    import hashlib
    import pandas as pd

    tender_id = resolve_tender_id(tender_summary, selected_tender_id)
    saved_tender = get_tender_by_id(tender_id, user_id) if tender_id else None
    if not saved_tender or int(saved_tender["id"]) != tender_id:
        st.error("This tender's saved record could not be resolved for your account. Tender-specific answers and uploads are disabled; your reusable company profile remains available.")
        return
    requirement_id = str(requirement.get("requirement_id") or "REQ")
    title = requirement_title(requirement)
    quote = str(requirement.get("source_quotation") or requirement.get("tender_evidence") or "")
    normalized_quote = " ".join(quote.split())
    quote_hash = hashlib.sha256(normalized_quote.encode("utf-8")).hexdigest()
    saved = saved_responses.get(requirement_id)
    stale = bool(saved and saved.get("source_quote_sha256") != quote_hash)
    current = saved if saved and not stale else None
    values = (current or {}).get("response") or {}
    documents = (current or {}).get("documents") or []
    reference = _tender_reference(tender_summary.get("filename"), requirement.get("source_page"))
    guidance = PF.guidance_for_requirement(requirement)
    if requirement.get("document_checklist_item"):
        req_doc_title = str(requirement.get("required_document") or requirement.get("requirement") or "Required document")
        guidance = {
            "information": ["Document submission only — upload the required document below."],
            "documents": [req_doc_title],
            "document_label": "Required document listed in the tender checklist",
            "explicit_document": True,
        }
    widget_id = re.sub(r"[^A-Za-z0-9_]", "_", f"{tender_id}_{requirement_id}")[:72]
    payment = bool(re.search(r"earnest money|\bemd\b|bid security|tender (?:document )?fee|processing fee", title + " " + quote, re.I))
    model_item = bool(re.search(r"make\s*/\s*model|make and model|model number|manufacturer.*model", title + " " + quote, re.I))
    project_item = bool(re.search(r"past experience|similar project|similar work|completed project", title + " " + quote, re.I))
    service_item = bool(re.search(r"after.sales|service centre|service center|support network", title + " " + quote, re.I))
    evidence_status = _response_evidence_state(requirement, saved)
    status_class = {"Not provided": "pill-unresolved", "Information provided": "pill-inforeq",
                    "Evidence uploaded": "pill-inforeq", "Verified": "pill-satisfied",
                    "Needs review": "pill-notsatisfied"}.get(evidence_status, "pill-unresolved")

    info_req = information_required_label(requirement, guidance, None)
    doc_text, doc_badge, is_mandatory = documents_required_label(requirement, guidance, None)
    source_page = requirement.get("source_page")
    page_badge = f"Page {source_page}" if source_page else "Tender Clause"
    category = requirement.get("category") or "Eligibility"

    with st.container(border=True):
        col_title, col_status = st.columns([3, 1])
        with col_title:
            st.markdown(f"### {h(title)}")
            st.markdown(
                f"<span class='tf-req-tag'>{h(category)}</span> "
                f"<span class='tf-req-tag'>{h(page_badge)}</span>",
                unsafe_allow_html=True,
            )
        with col_status:
            st.markdown(f"<span class='{status_class}'>{h(evidence_status)}</span>", unsafe_allow_html=True)
            if stale:
                st.caption("Clause updated since saved")
            elif current:
                st.caption("Response saved; unverified")
            else:
                st.caption("Pending submission")

        if not source_reference_is_valid(requirement):
            st.warning("The source citation is not validated. Check the quotation against the official tender document.")
        if payment_value:
            st.caption(f"Tender specified amount: {h(payment_value)}")

        badge_class = "tf-badge-mandatory" if doc_badge == "Mandatory" else "tf-badge-optional"
        badge_html = f"<span class='{badge_class}'>{h(doc_badge)}</span>" if doc_badge in ("Mandatory", "Optional") else ""
        st.markdown(f"**Information Required:** {h(info_req)}")
        st.markdown(f"**Documents Required:** {badge_html} {h(doc_text)}", unsafe_allow_html=True)

        if stale:
            st.warning("The tender clause changed since this response was saved. Earlier answer and uploads are retained below.")
        elif current:
            saved_notes = []
            if values:
                val_summary = "; ".join(f"{k}: {v}" for k, v in values.items() if v and not isinstance(v, list))
                if len(val_summary) > 80:
                    val_summary = val_summary[:77] + "…"
                if val_summary:
                    saved_notes.append(f"**Saved Info:** {h(val_summary)}")
                if values.get("projects"):
                    saved_notes.append(f"**Saved Projects:** {len(values['projects'])} project(s)")
            if documents:
                doc_strs = [f"{h(doc['filename'])} ({h(doc.get('verification_status') or 'unverified')})" for doc in documents]
                saved_notes.append(f"**Saved File:** {', '.join(doc_strs)}")
            if saved_notes:
                st.markdown(f"<div class='tf-saved-chip'>{' · '.join(saved_notes)}</div>", unsafe_allow_html=True)

        with st.form(f"tender_response_form_{widget_id}", border=False):
            if payment:
                statuses = ["Not arranged", "Paid", "Exemption claimed", "Not applicable"]
                old_status = values.get("payment_status", "Not arranged")
                p1, p2 = st.columns(2)
                with p1:
                    resp_amount = st.text_input("Payment Amount", value=values.get("amount", payment_value), placeholder="e.g. ₹50,000", key=f"resp_amount_{widget_id}")
                    resp_method = st.text_input("Payment Method", value=values.get("payment_method", ""), placeholder="e.g. RTGS / NEFT / Bank Guarantee / DD", key=f"resp_method_{widget_id}")
                with p2:
                    resp_status = st.selectbox("Payment Status", statuses, index=statuses.index(old_status) if old_status in statuses else 0, key=f"resp_status_{widget_id}")
                    resp_date = st.text_input("Payment Date / Deadline", value=values.get("payment_date", ""), placeholder="e.g. 15-Nov-2026", key=f"resp_date_{widget_id}")
                resp_exemption = st.text_area("Exemption Basis (if applicable)", value=values.get("exemption_basis", ""), placeholder="State exemption category (e.g. MSME / NSIC / Startup) and registration number", key=f"resp_exemption_{widget_id}")
                response = {
                    "amount": resp_amount, "payment_method": resp_method,
                    "payment_status": resp_status, "payment_date": resp_date,
                    "exemption_basis": resp_exemption,
                }
            elif model_item:
                m1, m2 = st.columns(2)
                with m1:
                    resp_equip = st.text_input("Equipment / Item Name", value=values.get("equipment", ""), placeholder="e.g. Operating Table", key=f"resp_equipment_{widget_id}")
                    resp_mfg = st.text_input("Manufacturer", value=values.get("manufacturer", ""), placeholder="e.g. Siemens / Philips", key=f"resp_manufacturer_{widget_id}")
                with m2:
                    resp_model = st.text_input("Exact Make & Model Number", value=values.get("model", ""), placeholder="e.g. Model X-200", key=f"resp_model_{widget_id}")
                resp_specs = st.text_area("Technical Specifications & Offered Configuration", value=values.get("specifications", ""), placeholder="Describe technical specifications and offered configuration", key=f"resp_specs_{widget_id}")
                response = {
                    "equipment": resp_equip, "manufacturer": resp_mfg,
                    "model": resp_model, "specifications": resp_specs,
                }
            elif project_item:
                old_projects = values.get("projects") or [{"client": "", "completion_date": "", "contract_value": "", "scope": ""}]
                edited_projects = st.data_editor(
                    pd.DataFrame(old_projects, columns=["client", "completion_date", "contract_value", "scope"]),
                    num_rows="dynamic", hide_index=True, use_container_width=True, key=f"resp_projects_{widget_id}",
                    column_config={"client": st.column_config.TextColumn("Client / department", help="e.g. AIIMS New Delhi"),
                                   "completion_date": st.column_config.TextColumn("Completion date", help="e.g. March 2025"),
                                   "contract_value": st.column_config.TextColumn("Contract value", help="e.g. ₹1.2 Crore"),
                                   "scope": st.column_config.TextColumn("Project scope", help="e.g. Supply and maintenance")})
                response = {"projects": [row for row in edited_projects.to_dict("records")
                                          if any(str(v or "").strip() for v in row.values())]}
            elif service_item:
                s1, s2 = st.columns(2)
                with s1:
                    resp_centres = st.text_area("Service Centre Locations", value=values.get("service_centres", ""), placeholder="List service centre locations and coverage", key=f"resp_centres_{widget_id}")
                    resp_staff = st.text_input("Service Engineers / Staff", value=values.get("support_staff", ""), placeholder="e.g. 8 dedicated engineers", key=f"resp_staff_{widget_id}")
                with s2:
                    resp_response = st.text_input("Service Response Time", value=values.get("response_time", ""), placeholder="e.g. Within 4 hours", key=f"resp_response_{widget_id}")
                    resp_escalation = st.text_area("Complaint Escalation Process", value=values.get("escalation", ""), placeholder="Escalation hierarchy and contact details", key=f"resp_escalation_{widget_id}")
                resp_maint = st.text_area("Preventive Maintenance & Warranty Support", value=values.get("maintenance_warranty", ""), placeholder="Details of warranty and preventive maintenance support", key=f"resp_maintenance_{widget_id}")
                response = {
                    "service_centres": resp_centres, "support_staff": resp_staff,
                    "response_time": resp_response, "escalation": resp_escalation,
                    "maintenance_warranty": resp_maint,
                }
            else:
                placeholder_txt = "Enter specific response addressing this requirement..."
                if requirement.get("document_checklist_item"):
                    response = {"details": st.text_input("Submission Reference / Notes (optional)", value=values.get("details", ""),
                                                         placeholder="e.g. Enclosed as Annexure-1", key=f"resp_details_{widget_id}")}
                else:
                    response = {"details": st.text_area("Your Response", value=values.get("details", ""),
                                                         placeholder=placeholder_txt, key=f"resp_details_{widget_id}")}

            company_pdfs = [doc for doc in get_company_documents(user_id)
                            if str(doc.get("filename") or "").lower().endswith(".pdf")]
            existing_ids = [doc["id"] for doc in company_pdfs]
            existing_document_id = None
            if company_pdfs:
                existing_document_id = st.selectbox(
                    "Attach existing company PDF (optional)", [None, *existing_ids],
                    format_func=lambda doc_id: "Choose a saved PDF" if doc_id is None else
                        f"{next(doc['filename'] for doc in company_pdfs if doc['id'] == doc_id)} · existing evidence",
                    key=f"resp_existing_doc_{widget_id}")
            upload_label = "Upload Supporting Document (PDF)" + (" — Mandatory" if is_mandatory else " — Optional")
            upload = st.file_uploader(upload_label, type=["pdf"], key=f"resp_file_{widget_id}")
            st.caption("Accepted: PDF · Maximum 10 MB · Uploaded files remain unverified until reviewed.")
            if st.form_submit_button("Save Response", type="primary"):
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
                    st.error(f"The tender response could not be saved: {exc}")

        with st.popover("View original tender clause", use_container_width=True):
            refs = requirement.get("tender_evidence_refs") or []
            citation = ", ".join(f"page {ref.get('page_number')} · {ref.get('chunk_id')}" for ref in refs)
            st.markdown(f"**Original Tender Clause (Page {h(source_page or 'not located')})**")
            st.markdown(f"> {h(quote or requirement.get('requirement') or 'Source clause not retained.')}")
            st.caption(f"Source: {reference}" + (f" · {h(citation)}" if citation else "") +
                       ". An answer or upload does not itself establish compliance.")
'''

text = text[:idx_ts_start] + new_ts_code + text[idx_sync:]

# 3. Update _render_reusable_requirement_card
idx_reusable_start = text.index('def _render_reusable_requirement_card(')
idx_resp_evidence = text.index('\ndef _response_evidence_state(', idx_reusable_start)

new_reusable_code = '''def _render_reusable_requirement_card(user_id: int, profile: dict, item: dict, definition: dict,
                                      saved_status: str, source_valid: bool = True):
    import pandas as pd

    value = PF.current_value(profile, definition)
    documents = PF.field_documents(profile, definition)
    evidence_status, css_class, state_help = _field_evidence_state(definition, value, documents)
    if not source_valid:
        evidence_status, css_class = "Needs review", "pill-notsatisfied"
    guidance = PF.guidance_for_requirement(item, definition.get("field_key", ""))
    requirement_id = str(item.get("requirement_id") or "requirement")
    widget_id = re.sub(r"[^A-Za-z0-9_]", "_", f"{user_id}_{item.get('source_tender_id')}_{requirement_id}")[:80]

    title = requirement_title(item)
    field_label = str(definition.get("field_label") or "").strip()
    if field_label and "iso" in field_label.lower():
        title = field_label

    info_req = information_required_label(item, guidance, definition)
    doc_text, doc_badge, is_mandatory = documents_required_label(item, guidance, definition)
    source_page = item.get("source_page")
    page_badge = f"Page {source_page}" if source_page else "Tender Clause"
    category = item.get("category") or "Eligibility"

    with st.container(border=True):
        col_title, col_status = st.columns([3, 1])
        with col_title:
            st.markdown(f"### {h(title)}")
            st.markdown(
                f"<span class='tf-req-tag'>{h(category)}</span> "
                f"<span class='tf-req-tag'>{h(page_badge)}</span>",
                unsafe_allow_html=True,
            )
        with col_status:
            st.markdown(f"<span class='{css_class}'>{h(evidence_status)}</span>", unsafe_allow_html=True)
            st.caption(state_help)

        if not source_valid:
            st.warning("The source citation is not validated. Check the quotation against the official tender document.")

        badge_class = "tf-badge-mandatory" if doc_badge == "Mandatory" else "tf-badge-optional"
        badge_html = f"<span class='{badge_class}'>{h(doc_badge)}</span>" if doc_badge in ("Mandatory", "Optional") else ""
        st.markdown(f"**Information Required:** {h(info_req)}")
        st.markdown(f"**Documents Required:** {badge_html} {h(doc_text)}", unsafe_allow_html=True)

        if value or documents:
            saved_notes = []
            if value:
                clean_val = str(value).replace("\n", " ").strip()
                if len(clean_val) > 80:
                    clean_val = clean_val[:77] + "…"
                saved_notes.append(f"**Saved Value:** {h(clean_val)}")
            if documents:
                doc_strs = [f"{h(doc['filename'])} ({h(doc.get('verification_status') or 'unverified')})" for doc in documents]
                saved_notes.append(f"**Saved File:** {', '.join(doc_strs)}")
            st.markdown(f"<div class='tf-saved-chip'>{' · '.join(saved_notes)}</div>", unsafe_allow_html=True)

        with st.form(f"profile_requirement_{widget_id}", border=False):
            errors, new_value, upload = [], value, None
            if definition["input_type"] == "money_period":
                source_clause = " ".join(str(link.get("source_quotation") or link.get("requirement_text") or "")
                                         for link in definition.get("requirements", []))
                period = parse_turnover_requirement(source_clause)["period"]
                year_count = int(period.get("years") or 3)
                rows = PF.turnover_rows_from_text(value) or [
                    {"financial_year": "", "amount": None, "currency": "INR", "unit": "Crore"}
                    for _ in range(min(max(year_count, 1), 10))]
                st.caption("Enter turnover figures for each financial year. Unverified until checked.")
                edited = st.data_editor(
                    pd.DataFrame(rows, columns=["financial_year", "amount", "currency", "unit"]),
                    num_rows="dynamic", hide_index=True, use_container_width=True,
                    key=f"profile_turnover_{widget_id}",
                    column_config={
                        "financial_year": st.column_config.TextColumn("Financial year", help="e.g. 2024-25"),
                        "amount": st.column_config.NumberColumn("Turnover Amount", min_value=0.0),
                        "currency": st.column_config.SelectboxColumn("Currency", options=list(PF.CURRENCIES)),
                        "unit": st.column_config.SelectboxColumn("Unit", options=list(PF.AMOUNT_UNITS)),
                    })
            elif definition["input_type"] == "years":
                current_years = parse_company_years(value)
                years = st.number_input("Years in Business / Operation", min_value=0, max_value=200, step=1,
                                        value=int(current_years) if current_years is not None else 0,
                                        key=f"profile_years_{widget_id}")
            else:
                label, placeholder = _INPUT_HINTS.get(definition["input_type"], ("Details", ""))
                if definition["input_type"] == "certificate":
                    label = "Certificate Details"
                    placeholder = "e.g. Certificate number, issuing body, validity period"
                text_value = (st.text_area if definition["input_type"] in ("long_text", "document") else st.text_input)(
                    label, value=value, placeholder=placeholder or info_req,
                    key=f"profile_value_{widget_id}")

            company_pdfs, existing_document_id = [], None
            if definition.get("document_key") or is_mandatory or doc_badge == "Optional":
                company_pdfs = [doc for doc in get_company_documents(user_id)
                                if str(doc.get("filename") or "").lower().endswith(".pdf")]
                if company_pdfs:
                    existing_document_id = st.selectbox(
                        "Attach existing company PDF (optional)", [None, *[doc["id"] for doc in company_pdfs]],
                        format_func=lambda doc_id: "Choose a saved PDF" if doc_id is None else
                            f"{next(doc['filename'] for doc in company_pdfs if doc['id'] == doc_id)} · existing evidence",
                        key=f"profile_existing_doc_{widget_id}")
                upload_label = "Upload Supporting Document (PDF)" + (" — Mandatory" if is_mandatory else " — Optional")
                upload = st.file_uploader(upload_label, type=["pdf"], key=f"profile_doc_{widget_id}")
                st.caption("Accepted: PDF · Maximum 10 MB · Uploaded files remain unverified until reviewed.")

            if st.form_submit_button("Save Response", type="primary"):
                if definition["input_type"] == "money_period":
                    new_value, errors = PF.format_turnover_rows(edited.to_dict("records"))
                elif definition["input_type"] == "years":
                    new_value = f"{int(years)} years" if years else ""
                else:
                    new_value = text_value
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    try:
                        document = ({"filename": upload.name, "content": upload.getvalue(),
                                     "media_type": upload.type or "application/pdf"} if upload else
                                    existing_company_pdf_payload(company_pdfs, existing_document_id))
                        result = PF.save_field_value(user_id, definition, new_value, document)
                        if result["value_saved"] or result["document_id"]:
                            st.session_state[f"tf_flash_{user_id}"] = "Saved as unverified company information. It will not be treated as verified evidence."
                            st.rerun()
                        else:
                            st.info("No changes to save.")
                    except (ValueError, PermissionError, OSError) as exc:
                        st.error(f"The company information could not be saved: {exc}")

        with st.popover("View original tender clause", use_container_width=True):
            quote = str(item.get("source_quotation") or item.get("tender_evidence") or "").strip()
            refs = item.get("tender_evidence_refs") or []
            citation = ", ".join(f"page {ref.get('page_number')}, citation {ref.get('chunk_id')}" for ref in refs)
            st.markdown(f"**Original Tender Clause (Page {h(source_page or 'not located')})**")
            st.markdown(f"> {h(quote or item.get('requirement') or 'Source clause not retained.')}")
            st.caption(f"Page {h(source_page or 'not located')}" + (f" · {h(citation)}" if citation else ""))
'''

text = text[:idx_reusable_start] + new_reusable_code + text[idx_resp_evidence:]

# 4. Update render_tender_requested_fields
idx_req_start = text.index('def render_tender_requested_fields(')
idx_evidence_store = text.index('\ndef render_profile_evidence_storage(', idx_req_start)

new_requested_code = '''def render_tender_requested_fields(user_id: int, profile: dict):
    """Show only the selected user's tender requirements and their tender-specific responses."""
    st.markdown("## Section 2 – Tender-Specific Requirements")
    st.caption("Review what this tender requires from your company. Provide the requested information and upload supporting documents for each requirement.")

    all_tenders = get_all_tenders(user_id)
    if not all_tenders:
        st.info("No tenders are saved in this account yet. Upload a tender to begin.")
        return

    tender_by_id = {int(tender["id"]): tender for tender in all_tenders}
    selection_key = f"profile_tender_select_{user_id}"
    if selection_key not in st.session_state:
        current = st.session_state.get("selected_tender_id")
        st.session_state[selection_key] = int(current) if current and int(current) in tender_by_id else None
    options = [None, *tender_by_id]
    selected_id = st.selectbox(
        "Select Tender", options, key=selection_key,
        format_func=lambda tender_id: "Choose a tender" if tender_id is None else
            f"{tender_by_id[tender_id]['filename']} · {tender_by_id[tender_id]['page_count']} pages",
        on_change=_profile_tender_selection_changed, args=(user_id,),
    )
    if selected_id is None:
        st.info("Choose one of your saved tenders to see only its requirements.")
        return
    st.session_state["selected_tender_id"] = int(selected_id)
    tender = tender_by_id[int(selected_id)]
    analysis = get_analysis_for_tender(int(selected_id), user_id)
    if not analysis:
        st.info(f"{tender['filename']} has not been analyzed yet. Run its analysis to populate tender requirements here.")
        return

    _get_profile_field_sync(user_id)  # keeps reusable field definitions linked to their source tender
    matrix = analysis.get("compliance_matrix") or []
    structured = analysis.get("structured_tender_data") or {}
    risks = structured.get("risk_assessment") or {}
    checklist = risks.get("document_checklist") or []

    requirements = list(matrix)
    seen = {str(row.get("requirement_id") or "").casefold() for row in requirements}
    import hashlib
    for entry in checklist:
        document = " ".join(str(entry.get("document") or "").split())
        if not document:
            continue
        digest = hashlib.sha256(document.casefold().encode("utf-8")).hexdigest()[:12]
        requirement_id = f"DOC-CHECKLIST-{digest}"
        if requirement_id in seen:
            continue
        seen.add(requirement_id)
        requirements.append({
            "requirement_id": requirement_id, "requirement": document,
            "required_document": document, "category": "Submission Document",
            "requirement_scope": "tender_submission", "is_mandatory": True,
            "status": "INFORMATION REQUIRED", "source_page": entry.get("source_page"),
            "source_quotation": entry.get("source_quotation") or entry.get("tender_evidence") or document,
            "document_checklist_item": True,
            "source_verified": entry.get("source_verified") is True,
            "agent1_validation": entry.get("agent1_validation"),
        })

    saved_responses = get_tender_requirement_responses(user_id, int(selected_id))
    financial = structured.get("financial_analysis") or {}
    for key, requirement_id, label in (("emd", "FIN-EMD", "Earnest money deposit / bid security"),
                                       ("tender_fee", "FIN-FEE", "Tender document fee")):
        payment = financial.get(key) or {}
        if payment.get("status") != "found" or any(str(row.get("requirement_id")) == requirement_id for row in requirements):
            continue
        requirements.append({
            "requirement_id": requirement_id, "requirement": label, "category": "Financial",
            "is_mandatory": True, "status": "INFORMATION REQUIRED",
            "source_page": payment.get("source_page"),
            "source_quotation": payment.get("tender_evidence") or payment.get("evidence") or label,
            "tender_evidence": payment.get("tender_evidence") or payment.get("evidence") or "",
        })

    unique_requirements, seen_requirement_ids, seen_requirement_clauses = [], set(), set()
    for row in requirements:
        if normalize_status(row.get("status")) == "NOT APPLICABLE":
            continue
        item = {**row, "source_tender_id": int(selected_id)}
        identity = str(item.get("requirement_id") or "").strip().casefold()
        clause = " ".join(str(item.get("source_quotation") or item.get("tender_evidence") or "").casefold().split())
        try:
            source_page = int(item.get("source_page") or 0)
        except (TypeError, ValueError):
            source_page = 0
        content_identity = (source_page, clause) if clause else (
            normalize_status(item.get("category")), " ".join(str(item.get("requirement") or "").casefold().split()))
        if (identity and identity in seen_requirement_ids) or content_identity in seen_requirement_clauses:
            continue
        if identity:
            seen_requirement_ids.add(identity)
        seen_requirement_clauses.add(content_identity)
        unique_requirements.append(item)

    records = []
    for item in unique_requirements:
        requirement_id = str(item.get("requirement_id") or "")
        saved = saved_responses.get(requirement_id)
        definition = None if item.get("document_checklist_item") else _reusable_definition_for_requirement(item)
        guidance = PF.guidance_for_requirement(item, definition.get("field_key", "") if definition else "")
        status = _requirement_status(item, saved, definition, profile)
        records.append({"item": item, "saved": saved, "definition": definition, "guidance": guidance,
                        "status": status,
                        "requirement_type": classify_requirement_type(item, guidance),
                        "needs_information": requirement_needs_information(item, guidance),
                        "needs_documents": requirement_needs_documents(item, guidance),
                        "group": requirement_group(item, guidance)})

    status_counts = {label: sum(record["status"] == label for record in records)
                     for label in ("Not provided", "Information provided", "Evidence uploaded", "Verified", "Needs review")}
    completed = status_counts["Verified"]
    information_needed = sum(record["status"] in ("Not provided", "Information provided", "Needs review") or record["needs_information"] for record in records)
    documents_pending = sum(record["status"] == "Evidence uploaded" or record["needs_documents"] for record in records)
    completion = round((completed / len(records) * 100) if records else 0)

    metric_columns = st.columns(4)
    metric_columns[0].metric("Total Requirements", len(records))
    metric_columns[1].metric("Completed / Verified", completed)
    metric_columns[2].metric("Information Needed", information_needed)
    metric_columns[3].metric("Documents Pending", documents_pending)
    st.progress(completion / 100, text=f"{completion}% complete")

    filter_choice = st.radio("Filter requirements", ["All Requirements", "Action Required", "Documents Pending", "Verified"],
                             horizontal=True, label_visibility="collapsed",
                             key=f"profile_requirement_filter_{user_id}")
    visible_records = filter_requirement_records(records, filter_choice)

    CATEGORY_GROUPS = [
        ("Financial Requirements", "💰 Financial", "Annual turnover, net worth, solvency, EMD, and tender fee requirements"),
        ("Company Eligibility", "📋 Eligibility", "Legal structure, registration, incorporation, and minimum qualifications"),
        ("Previous Project Experience", "🏆 Experience", "Similar completed projects, past work orders, and performance certificates"),
        ("Technical Requirements", "⚙️ Technical", "Technical specifications, equipment make/model, service network, and warranty"),
        ("Registrations & Certifications", "📜 Certifications", "Quality certifications (ISO, CE, CDSCO) and statutory tax registrations (GST, PAN)"),
        ("Declarations & Other Conditions", "📝 Declarations & Undertakings", "Non-blacklisting undertakings, integrity declarations, and statutory compliance"),
    ]

    grouped = {group: [] for group, _, _ in CATEGORY_GROUPS}
    for record in visible_records:
        grouped.setdefault(record["group"], []).append(record)

    for group_key, display_title, description in CATEGORY_GROUPS:
        group_records = grouped.get(group_key) or []
        if not group_records:
            continue
        with st.expander(f"{display_title} ({len(group_records)})", expanded=True):
            st.caption(description)
            for record in group_records:
                item, saved = record["item"], record["saved"]
                definition = record["definition"]
                requirement_id = str(item.get("requirement_id") or "")
                if definition:
                    _render_reusable_requirement_card(user_id, profile, item, definition,
                                                       item.get("status", "INFORMATION REQUIRED"),
                                                       source_valid=_requirement_source_is_valid(item))
                else:
                    payment_data = financial.get("emd") if requirement_id == "FIN-EMD" else financial.get("tender_fee") if requirement_id == "FIN-FEE" else {}
                    _render_tender_specific_response(user_id, tender, item, saved_responses,
                                                     str((payment_data or {}).get("value") or ""),
                                                     selected_tender_id=int(selected_id))

    flash = st.session_state.pop(f"tf_flash_{user_id}", None)
    if flash:
        st.success(flash)
'''

text = text[:idx_req_start] + new_requested_code + text[idx_evidence_store:]

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(text)

print('app.py successfully updated!')
