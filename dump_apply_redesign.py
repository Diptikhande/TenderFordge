"""
Carefully update Company Profile section and Section 2 requirement cards in app.py.
Preserves all functional code, database calls, session states, keys, and test contract strings.
"""
import re

with open("app.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Redesign _render_tender_specific_response card layout to two-column
old_tender_spec = """    with st.container(border=True):
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
                    st.error(f"The tender response could not be saved: {exc}")"""

new_tender_spec = """    cat_icon = {
        "Financial": "💰", "Financial Requirements": "💰",
        "Eligibility": "📋", "Company Eligibility": "📋",
        "Experience": "🏆", "Previous Project Experience": "🏆",
        "Technical": "⚙️", "Technical Requirements": "⚙️",
        "Certifications": "📜", "Registrations & Certifications": "📜",
        "Submission Document": "📄", "Declarations & Other Conditions": "📝",
    }.get(category, "📌")

    with st.container(border=True):
        col_title, col_status = st.columns([3, 1])
        with col_title:
            st.markdown(f"### {cat_icon} {h(title)}")
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

        if stale:
            st.warning("The tender clause changed since this response was saved. Earlier answer and uploads are retained below.")

        with st.form(f"tender_response_form_{widget_id}", border=False):
            col_info, col_docs = st.columns([1, 1], gap="large")
            with col_info:
                st.markdown(f"**Information Required:** {h(info_req)}")
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

                if values:
                    saved_val_strs = [f"{k}: {v}" for k, v in values.items() if v and not isinstance(v, list)]
                    if saved_val_strs:
                        val_summary = "; ".join(saved_val_strs)
                        if len(val_summary) > 75: val_summary = val_summary[:72] + "…"
                        st.markdown(f"<div class='tf-saved-chip'><b>Saved Info:</b> {h(val_summary)}</div>", unsafe_allow_html=True)
                    if values.get("projects"):
                        st.markdown(f"<div class='tf-saved-chip'><b>Saved Projects:</b> {len(values['projects'])} project(s)</div>", unsafe_allow_html=True)

            with col_docs:
                st.markdown(f"**Documents Required:** {badge_html} {h(doc_text)}", unsafe_allow_html=True)
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
                if documents:
                    doc_strs = [f"{h(doc['filename'])} ({h(doc.get('verification_status') or 'unverified')})" for doc in documents]
                    st.markdown(f"<div class='tf-saved-chip'><b>Saved File:</b> {', '.join(doc_strs)}</div>", unsafe_allow_html=True)

                if st.form_submit_button("Save Response", type="primary", use_container_width=True):
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

assert old_tender_spec in code, "old_tender_spec not found in app.py"
code = code.replace(old_tender_spec, new_tender_spec, 1)
print("1. Replaced _render_tender_specific_response with two-column layout.")

# 2. Redesign _render_reusable_requirement_card layout to two-column
old_reusable_card = """    with st.container(border=True):
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
                clean_val = " ".join(str(value).split())
                if len(clean_val) > 80:
                    clean_val = clean_val[:77] + "..."
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
                        st.error(f"The company information could not be saved: {exc}")"""

new_reusable_card = """    cat_icon = {
        "Financial": "💰", "Financial Requirements": "💰",
        "Eligibility": "📋", "Company Eligibility": "📋",
        "Experience": "🏆", "Previous Project Experience": "🏆",
        "Technical": "⚙️", "Technical Requirements": "⚙️",
        "Certifications": "📜", "Registrations & Certifications": "📜",
        "Submission Document": "📄", "Declarations & Other Conditions": "📝",
    }.get(category, "📌")

    with st.container(border=True):
        col_title, col_status = st.columns([3, 1])
        with col_title:
            st.markdown(f"### {cat_icon} {h(title)}")
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

        with st.form(f"profile_requirement_{widget_id}", border=False):
            col_info, col_docs = st.columns([1, 1], gap="large")
            with col_info:
                st.markdown(f"**Information Required:** {h(info_req)}")
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

                if value:
                    clean_val = " ".join(str(value).split())
                    if len(clean_val) > 80:
                        clean_val = clean_val[:77] + "..."
                    st.markdown(f"<div class='tf-saved-chip'><b>Saved Value:</b> {h(clean_val)}</div>", unsafe_allow_html=True)

            with col_docs:
                st.markdown(f"**Documents Required:** {badge_html} {h(doc_text)}", unsafe_allow_html=True)
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

                if documents:
                    doc_strs = [f"{h(doc['filename'])} ({h(doc.get('verification_status') or 'unverified')})" for doc in documents]
                    st.markdown(f"<div class='tf-saved-chip'><b>Saved File:</b> {', '.join(doc_strs)}</div>", unsafe_allow_html=True)

                if st.form_submit_button("Save Response", type="primary", use_container_width=True):
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
                            st.error(f"The company information could not be saved: {exc}")"""

assert old_reusable_card in code, "old_reusable_card not found in app.py"
code = code.replace(old_reusable_card, new_reusable_card, 1)
print("2. Replaced _render_reusable_requirement_card with two-column layout.")

# 3. Redesign render_tender_requested_fields with Tender Summary Card & Category Filters
old_fields_start = """    metric_columns = st.columns(4)
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
            st.caption(description)"""

new_fields_start = """    # Top Tender Summary Card
    st.markdown(f\"\"\"
    <div class="tender-summary-card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem; margin-bottom:0.75rem;">
            <div style="display:flex; align-items:center; gap:0.85rem;">
                <div class="tender-summary-icon">📄</div>
                <div>
                    <div style="font-size:1.1rem; font-weight:750; color:#0f172a;">{h(tender['filename'])}</div>
                    <div style="font-size:0.82rem; color:#64748b;">{tender['page_count']} Pages · Official Procurement Tender Notice</div>
                </div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:1.25rem; font-weight:800; color:#2563eb;">{completion}% Complete</div>
                <div style="font-size:0.78rem; color:#64748b;">{completed} of {len(records)} criteria verified</div>
            </div>
        </div>
    </div>
    \"\"\", unsafe_allow_html=True)
    st.progress(completion / 100, text=f"Requirements Completion: {completion}%")

    metric_columns = st.columns(4)
    metric_columns[0].metric("Total Requirements", len(records))
    metric_columns[1].metric("Completed / Verified", completed)
    metric_columns[2].metric("Information Needed", information_needed)
    metric_columns[3].metric("Documents Pending", documents_pending)

    # Dynamic Category Count Filters
    cat_counts = {
        "All": len(records),
        "Financial": sum(r["group"] == "Financial Requirements" for r in records),
        "Eligibility": sum(r["group"] == "Company Eligibility" for r in records),
        "Experience": sum(r["group"] == "Previous Project Experience" for r in records),
        "Technical": sum(r["group"] == "Technical Requirements" for r in records),
        "Certifications": sum(r["group"] == "Registrations & Certifications" for r in records),
        "Declarations": sum(r["group"] == "Declarations & Other Conditions" for r in records),
    }

    cat_filter_map = {
        f"All ({cat_counts['All']})": "All",
        f"💰 Financial ({cat_counts['Financial']})": "Financial Requirements",
        f"📋 Eligibility ({cat_counts['Eligibility']})": "Company Eligibility",
        f"🏆 Experience ({cat_counts['Experience']})": "Previous Project Experience",
        f"⚙️ Technical ({cat_counts['Technical']})": "Technical Requirements",
        f"📜 Certifications ({cat_counts['Certifications']})": "Registrations & Certifications",
        f"📝 Declarations ({cat_counts['Declarations']})": "Declarations & Other Conditions",
    }

    st.markdown("<div style='margin-top:1.25rem;'></div>", unsafe_allow_html=True)
    col_filter_cat, col_filter_status = st.columns([2.5, 1.5])
    with col_filter_cat:
        selected_cat_label = st.radio(
            "Category Filter",
            list(cat_filter_map.keys()),
            horizontal=True,
            label_visibility="collapsed",
            key=f"profile_category_filter_{user_id}",
        )
    with col_filter_status:
        filter_choice = st.radio(
            "Filter requirements",
            ["All Requirements", "Action Required", "Documents Pending", "Verified"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"profile_requirement_filter_{user_id}",
        )

    selected_cat_group = cat_filter_map[selected_cat_label]
    visible_records = filter_requirement_records(records, filter_choice)

    if selected_cat_group != "All":
        visible_records = [r for r in visible_records if r["group"] == selected_cat_group]

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
            st.caption(description)"""

assert old_fields_start in code, "old_fields_start not found in app.py"
code = code.replace(old_fields_start, new_fields_start, 1)
print("3. Replaced render_tender_requested_fields with tender summary card and dynamic category filters.")

# 4. Redesign Company Profile page routing with Top Header Bar, Clean Header, and Horizontal Section Tabs
old_profile_page = """    # ------------------------------------------------------------
    # PAGE 2: 🏢 Company Profile
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "🏢 Company Profile":
        st.markdown(\"\"\"
        <div class="saas-header">
            <div>
                <h1>Company Profile</h1>
                <p>Maintain company details and review the tender evidence your saved analyses request. Claims and uploads stay unverified until checked.</p>
            </div>
        </div>
        \"\"\", unsafe_allow_html=True)

        profile = get_company_profile(user_id) or {}

        st.markdown("## Section 1 — General Company Information")
        st.markdown("#### 1. General Company Information")
        st.markdown("### 2. Financial Information")
        # Structured compatibility helpers keep the profile workflow stable:
        # get_profile_financials, get_profile_projects, get_profile_certifications
        render_profile_completion_summary(user_id, profile)
        render_profile_inconsistency_warnings(profile)

        saved_general_fields = {str(row.get("field_key")): str(row.get("field_value") or "")
                                for row in profile.get("custom_fields") or []}
        with st.expander("General Information · Company Details", expanded=False):
            st.caption("Reusable legal, business, address and contact details.")
            c1, c2 = st.columns(2)
            with c1:
                company_name = st.text_input("Company Name *", value=profile.get("company_name", ""),
                                             placeholder="Legal registered name", key=f"profile_company_name_{user_id}")
                industry = st.text_input("Industry", value=profile.get("industry", ""),
                                         placeholder="e.g. Construction, medical equipment", key=f"profile_industry_{user_id}")
                contact_email = st.text_input("Contact Email", value=profile.get("contact_email", ""),
                                               placeholder="Company email", key=f"profile_contact_email_{user_id}")
            with c2:
                business_type = st.text_input("Business Type", value=saved_general_fields.get("organization.business_type", ""),
                                              placeholder="Company / LLP / Proprietorship", key=f"profile_business_type_{user_id}")
                gstin = st.text_input("GSTIN", value=saved_general_fields.get("registration.gst", ""),
                                      placeholder="GST registration number", key=f"profile_gstin_{user_id}")
                contact_phone = st.text_input("Contact Number", value=profile.get("contact_phone", ""),
                                              placeholder="Phone number", key=f"profile_contact_phone_{user_id}")
            contact_name = st.text_input("Authorized Contact Person", value=profile.get("contact_name", ""),
                                          placeholder="Name and role", key=f"profile_contact_name_{user_id}")
            contact_address = st.text_input("Registered Address", value=profile.get("contact_address", ""),
                                            placeholder="Address, city, state, PIN code", key=f"profile_contact_address_{user_id}")

        with st.expander("Financials and Experience · Financial and Experience Details", expanded=False):
            st.caption("Add a figure for each financial year and state the period covered.")
            years_of_experience = st.text_input("Years in Business / Experience *",
                value=profile.get("years_of_experience", ""), placeholder="e.g. 10 years",
                help="Used for minimum experience checks. Saving does not verify this claim.", key=f"profile_years_{user_id}")
            annual_turnover = st.text_area("Annual Turnover by Financial Year",
                value=profile.get("annual_turnover", ""), height=95,
                placeholder="FY2023-24: INR 2.4 crore; FY2024-25: INR 2.8 crore",
                help="Enter one figure for each year. Saved figures remain unverified until checked against supporting evidence.",
                key=f"profile_turnover_{user_id}")
            financial_details = st.text_area("Financial Details and Banking Facilities",
                value=profile.get("financial_details", ""), height=80,
                placeholder="Net worth, banking facilities, solvency or guarantee details", key=f"profile_financial_details_{user_id}")

        st.markdown("#### 3. Past Projects and Experience")
        with st.expander("Projects · Past Projects", expanded=False):
            past_projects = st.text_area("Projects and Reference Contracts",
                value=profile.get("past_projects", ""), height=125,
                placeholder="Project name, client, contract value, completion date and scope", key=f"profile_projects_{user_id}")

        st.markdown("#### 4. Certifications and Registrations")
        with st.expander("Certifications · Certifications and Registrations", expanded=False):
            certifications = st.text_area("Certifications and Standards",
                value=profile.get("certifications", ""), height=100,
                placeholder="ISO, Udyam/MSME, GST and other registrations with current status",
                help="List active registrations and certifications. Upload supporting PDFs below.", key=f"profile_certifications_{user_id}")

        st.markdown("#### 5. Technical Capabilities")
        with st.expander("Capabilities · Technical Capabilities", expanded=False):
            technical_capabilities = st.text_area("Products, Services, Equipment and Facilities",
                value=profile.get("technical_capabilities", ""), height=125,
                placeholder="Products/services, machinery, facility capacity and technical support",
                key=f"profile_technical_capabilities_{user_id}")

        st.markdown("#### 6. Supporting Documents and Evidence")
        with st.expander("Supporting Documents and Evidence", expanded=False):
            render_profile_evidence_storage(user_id, profile)
        if st.button("Save Company Profile", key=f"save_company_profile_{user_id}",
                     use_container_width=True, type="primary"):
            save_company_profile({
                "company_name": company_name,
                "industry": industry,
                "years_of_experience": years_of_experience,
                "annual_turnover": annual_turnover,
                "certifications": certifications,
                "technical_capabilities": technical_capabilities,
                "past_projects": past_projects,
                "financial_details": financial_details,
                "contact_name": contact_name,
                "contact_email": contact_email,
                "contact_phone": contact_phone,
                "contact_address": contact_address,
            }, user_id=user_id)
            current_general_fields = {str(row.get("field_key")): row for row in profile.get("custom_fields") or []}
            changed_general_fields = []
            for key, value in (("organization.business_type", business_type), ("registration.gst", gstin)):
                old_value = str(current_general_fields.get(key, {}).get("field_value") or "")
                if str(value or "").strip() != old_value.strip():
                    changed_general_fields.append({"field_key": key, "field_value": value,
                                                   "source": "user_entered", "verification_status": "unverified"})
            if changed_general_fields:
                save_company_profile_fields(user_id, changed_general_fields)
            st.success("Company profile saved. These values remain unverified until supported by reviewed evidence.")
            st.rerun()
        render_tender_requested_fields(user_id, profile)"""

new_profile_page = """    # ------------------------------------------------------------
    # PAGE 2: 🏢 Company Profile
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "🏢 Company Profile":
        profile = get_company_profile(user_id) or {}
        active_company_name = profile.get("company_name") or user.get("full_name") or "Enterprise Bidder"
        user_display_name = user.get("full_name") or "User"
        user_initials = "".join([part[0].upper() for part in user_display_name.split()[:2]]) or "TF"

        # 1. Clean Top Header Bar
        st.markdown(f\"\"\"
        <div class="profile-top-header">
            <div class="profile-search-wrap">
                <span class="profile-search-icon">🔍</span>
                <input type="text" placeholder="Search company profile details, requirements..." class="profile-search-input" readonly />
            </div>
            <div class="profile-user-badge">
                <div class="profile-user-avatar">{h(user_initials)}</div>
                <div class="profile-user-text">
                    <div class="profile-user-company">{h(active_company_name)}</div>
                    <div class="profile-user-email">{h(user['email'])}</div>
                </div>
            </div>
        </div>
        \"\"\", unsafe_allow_html=True)

        # 2. Company Profile Page Header
        st.markdown(\"\"\"
        <div class="company-profile-header">
            <div class="company-profile-icon">🏢</div>
            <div>
                <h1 class="company-profile-title">Company Profile</h1>
                <p class="company-profile-subtitle">Maintain company details and tender-specific information.</p>
            </div>
        </div>
        \"\"\", unsafe_allow_html=True)

        # 3. Horizontal Section Navigation Tabs
        profile_tab1, profile_tab2 = st.tabs([
            "Section 1 — General Information",
            "Section 2 — Tender-Specific Requirements"
        ])

        with profile_tab1:
            st.markdown("## Section 1 — General Company Information")
            st.markdown("#### 1. General Company Information")
            st.markdown("### 2. Financial Information")
            # Structured compatibility helpers keep the profile workflow stable:
            # get_profile_financials, get_profile_projects, get_profile_certifications
            render_profile_completion_summary(user_id, profile)
            render_profile_inconsistency_warnings(profile)

            saved_general_fields = {str(row.get("field_key")): str(row.get("field_value") or "")
                                    for row in profile.get("custom_fields") or []}
            with st.expander("General Information · Company Details", expanded=True):
                st.caption("Reusable legal, business, address and contact details.")
                c1, c2 = st.columns(2)
                with c1:
                    company_name = st.text_input("Company Name *", value=profile.get("company_name", ""),
                                                 placeholder="Legal registered name", key=f"profile_company_name_{user_id}")
                    industry = st.text_input("Industry", value=profile.get("industry", ""),
                                             placeholder="e.g. Construction, medical equipment", key=f"profile_industry_{user_id}")
                    contact_email = st.text_input("Contact Email", value=profile.get("contact_email", ""),
                                                   placeholder="Company email", key=f"profile_contact_email_{user_id}")
                with c2:
                    business_type = st.text_input("Business Type", value=saved_general_fields.get("organization.business_type", ""),
                                                  placeholder="Company / LLP / Proprietorship", key=f"profile_business_type_{user_id}")
                    gstin = st.text_input("GSTIN", value=saved_general_fields.get("registration.gst", ""),
                                          placeholder="GST registration number", key=f"profile_gstin_{user_id}")
                    contact_phone = st.text_input("Contact Number", value=profile.get("contact_phone", ""),
                                                  placeholder="Phone number", key=f"profile_contact_phone_{user_id}")
                contact_name = st.text_input("Authorized Contact Person", value=profile.get("contact_name", ""),
                                              placeholder="Name and role", key=f"profile_contact_name_{user_id}")
                contact_address = st.text_input("Registered Address", value=profile.get("contact_address", ""),
                                                placeholder="Address, city, state, PIN code", key=f"profile_contact_address_{user_id}")

            with st.expander("Financials and Experience · Financial and Experience Details", expanded=False):
                st.caption("Add a figure for each financial year and state the period covered.")
                years_of_experience = st.text_input("Years in Business / Experience *",
                    value=profile.get("years_of_experience", ""), placeholder="e.g. 10 years",
                    help="Used for minimum experience checks. Saving does not verify this claim.", key=f"profile_years_{user_id}")
                annual_turnover = st.text_area("Annual Turnover by Financial Year",
                    value=profile.get("annual_turnover", ""), height=95,
                    placeholder="FY2023-24: INR 2.4 crore; FY2024-25: INR 2.8 crore",
                    help="Enter one figure for each year. Saved figures remain unverified until checked against supporting evidence.",
                    key=f"profile_turnover_{user_id}")
                financial_details = st.text_area("Financial Details and Banking Facilities",
                    value=profile.get("financial_details", ""), height=80,
                    placeholder="Net worth, banking facilities, solvency or guarantee details", key=f"profile_financial_details_{user_id}")

            st.markdown("#### 3. Past Projects and Experience")
            with st.expander("Projects · Past Projects", expanded=False):
                past_projects = st.text_area("Projects and Reference Contracts",
                    value=profile.get("past_projects", ""), height=125,
                    placeholder="Project name, client, contract value, completion date and scope", key=f"profile_projects_{user_id}")

            st.markdown("#### 4. Certifications and Registrations")
            with st.expander("Certifications · Certifications and Registrations", expanded=False):
                certifications = st.text_area("Certifications and Standards",
                    value=profile.get("certifications", ""), height=100,
                    placeholder="ISO, Udyam/MSME, GST and other registrations with current status",
                    help="List active registrations and certifications. Upload supporting PDFs below.", key=f"profile_certifications_{user_id}")

            st.markdown("#### 5. Technical Capabilities")
            with st.expander("Capabilities · Technical Capabilities", expanded=False):
                technical_capabilities = st.text_area("Products, Services, Equipment and Facilities",
                    value=profile.get("technical_capabilities", ""), height=125,
                    placeholder="Products/services, machinery, facility capacity and technical support",
                    key=f"profile_technical_capabilities_{user_id}")

            st.markdown("#### 6. Supporting Documents and Evidence")
            with st.expander("Supporting Documents and Evidence", expanded=False):
                render_profile_evidence_storage(user_id, profile)
            if st.button("Save Company Profile", key=f"save_company_profile_{user_id}",
                         use_container_width=True, type="primary"):
                save_company_profile({
                    "company_name": company_name,
                    "industry": industry,
                    "years_of_experience": years_of_experience,
                    "annual_turnover": annual_turnover,
                    "certifications": certifications,
                    "technical_capabilities": technical_capabilities,
                    "past_projects": past_projects,
                    "financial_details": financial_details,
                    "contact_name": contact_name,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                    "contact_address": contact_address,
                }, user_id=user_id)
                current_general_fields = {str(row.get("field_key")): row for row in profile.get("custom_fields") or []}
                changed_general_fields = []
                for key, value in (("organization.business_type", business_type), ("registration.gst", gstin)):
                    old_value = str(current_general_fields.get(key, {}).get("field_value") or "")
                    if str(value or "").strip() != old_value.strip():
                        changed_general_fields.append({"field_key": key, "field_value": value,
                                                       "source": "user_entered", "verification_status": "unverified"})
                if changed_general_fields:
                    save_company_profile_fields(user_id, changed_general_fields)
                st.success("Company profile saved. These values remain unverified until supported by reviewed evidence.")
                st.rerun()

        with profile_tab2:
            render_tender_requested_fields(user_id, profile)"""

assert old_profile_page in code, "old_profile_page not found in app.py"
code = code.replace(old_profile_page, new_profile_page, 1)
print("4. Replaced Company Profile page layout with horizontal tabs, clean header, and top header bar.")

with open("app.py", "w", encoding="utf-8") as f:
    f.write(code)

print("All app.py Company Profile redesign updates applied successfully.")
