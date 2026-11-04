"""
TenderForge — Enterprise Tender Intelligence Workspace
Production-Grade Multi-Tenant SaaS Application for Procurement Evaluation
"""

import streamlit as st
import analyzer as Analyzer
import html
import json
import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from config import APP_NAME, APP_VERSION, UPLOAD_DIR
from database import (
    init_db,
    create_user,
    authenticate_user,
    get_company_profile,
    save_company_profile,
    save_company_profile_fields,
    delete_company_profile_field,
    save_company_document,
    get_company_documents,
    save_company_document_values,
    save_tender,
    get_all_tenders,
    get_tender_by_id,
    compute_profile_hash,
    get_analysis_for_tender,
    get_dashboard_stats,
    get_tenders_with_analysis,
    get_custom_requirements,
    save_custom_requirements,
    clear_custom_requirements,
    get_profile_field_definitions,
    review_document_value,
    withdraw_document_value,
    get_evidence_reviews,
    get_tender_requirement_responses,
    save_tender_requirement_response,
)
import profile_fields as PF
from tender_response_utils import (record_analysis_outcome, resolve_tender_id,
                                   response_evidence_status, source_reference_is_valid,
                                   existing_company_pdf_payload,
                                   classify_requirement_type,
                                   requirement_needs_information,
                                   requirement_needs_documents,
                                   filter_requirement_records,
                                   requirement_group,
                                   requirement_title,
                                   information_required_label,
                                   documents_required_label)
from pdf_processor import extract_text_from_pdf, save_uploaded_file
from analysis_runner import run_analysis_for_tender, requirements_hash, REQ_EDITOR_COLUMNS
from report_builder import build_report
from analyzer import (
    ensure_seven_sections,
    extract_all_structured_requirements,
    normalize_custom_requirements,
    REQUIREMENT_CATEGORIES,
    parse_company_years,
    normalize_status,
    ENGINE_VERSION,
    NOT_FOUND,
    parse_turnover_requirement,
)

# --- Initialize Database & Safe Migrations ---
init_db()

# --- Page Configuration ---
st.set_page_config(
    page_title=f"{APP_NAME} — Enterprise Tender Intelligence",
    page_icon="🔨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Session State Initialization ---
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "user" not in st.session_state:
    st.session_state["user"] = None
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "📊 Dashboard"
if "selected_tender_id" not in st.session_state:
    st.session_state["selected_tender_id"] = None

# Backward compatibility for the remaining legacy page name.
if st.session_state.get("current_page") == "?? Proposals (Section 3)":
    st.session_state["current_page"] = "?? Proposals"

# --- Safe HTML Escaping and Text Formatting Helpers ---
def h(text) -> str:
    """Safely escape text for HTML output to prevent raw HTML injection and broken tags."""
    if text is None:
        return ""
    return html.escape(str(text))

_STATUS_PILLS = {
    "SATISFIED": ("pill-satisfied", "✓ SATISFIED"),
    "CONFIRMED GAP": ("pill-notsatisfied", "✗ CONFIRMED GAP"),
    "INFORMATION REQUIRED": ("pill-inforeq", "? INFO REQUIRED"),
    "UNRESOLVED": ("pill-unresolved", "⟳ UNRESOLVED"),
    "NOT APPLICABLE": ("pill-unclear", "– NOT APPLICABLE"),
}


def status_pill(raw_status) -> str:
    """Badge for a requirement status. Legacy labels (e.g. UNCLEAR) are shown with their original label noted."""
    status = normalize_status(raw_status)
    cls, label = _STATUS_PILLS[status]
    raw = " ".join(str(raw_status or "").upper().split())
    legacy = f" <span style='font-size:0.7rem; color:#64748b;'>(saved as {h(raw)})</span>" if raw and raw != status else ""
    return f"<span class='{cls}'>{label}</span>{legacy}"


def deadline_display(value):
    """Return a readable deadline and flag dates already past."""
    raw = str(value or NOT_FOUND).strip()
    if raw == NOT_FOUND or not raw:
        return NOT_FOUND
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(raw[:16], fmt).date()
            if parsed < date.today():
                return f"{raw} — EXPIRED"
            return raw
        except ValueError:
            continue
    return raw


PROFILE_FIELD_LABELS = {
    "company_name": "Company Legal Name / Registration Details",
    "industry": "Primary Industry / Sector",
    "years_of_experience": "Years in Business / Experience",
    "annual_turnover": "Annual Turnover / Financial Scale",
    "certifications": "Certifications & Standards",
    "technical_capabilities": "Technical Capabilities, Equipment & Facilities",
    "past_projects": "Past Projects & Reference Contracts",
    "financial_details": "Financial Details, Bank Guarantees & Securities",
}
def analysis_state(saved_analysis: dict, current_profile_hash: str, custom_requirements: list | None) -> tuple[str, bool]:
    """(label, is_current) for a saved analysis: current, stale (profile / requirements changed) or legacy engine."""
    structured = saved_analysis.get("structured_tender_data") or {}
    if structured.get("engine_version") != ENGINE_VERSION:
        return "Legacy engine — re-run to apply the current evidence rules", False
    if saved_analysis.get("profile_hash") != current_profile_hash:
        return "Stale — company information changed since this analysis", False
    if structured.get("requirements_hash", "auto") != requirements_hash(custom_requirements):
        return "Stale — requirements were edited since this analysis", False
    return "Current", True


def analysis_company_check(saved_analysis: dict, profile: dict | None) -> tuple[str, bool]:
    """Which company the saved analysis was produced for, and whether that still matches the current profile."""
    snap = (saved_analysis.get("structured_tender_data") or {}).get("company_snapshot") or {}
    current = str((profile or {}).get("company_name") or "").strip()
    if not snap:
        return ("The company profile used for this analysis was not recorded (older analysis). Re-run it to analyse "
                f"against the current profile{f' ({current})' if current else ''}."), False
    used = snap.get("company_name") or "(no company name)"
    if snap.get("company_name", "").strip().lower() != current.lower():
        return (f"This analysis was produced for '{used}', but the current company profile is "
                f"'{current or '(no company name)'}'. Re-run the analysis for the current company."), False
    return f"Analysed for: {used}", True


@st.cache_resource(show_spinner=False)
def _analysis_executor():
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="tender-analysis")


def _run_analysis_background(user_id: int, tender_id: int, events):
    """Run analysis off the Streamlit script thread; workers must not call Streamlit APIs."""
    return run_analysis_for_tender(
        user_id, tender_id,
        progress=lambda stage, message: events.put((stage, message)),
    )


def _record_analysis_outcome(state, tender_id: int, outcome: dict):
    """Clear local progress state and publish either a success notice or a scoped failure."""
    record_analysis_outcome(state, tender_id, outcome)


@st.fragment(run_every="1s")
def _render_analysis_progress(tender_id: int):
    job_key = f"analysis_job_{tender_id}"
    job = st.session_state.get(job_key)
    if not job:
        return
    while True:
        try:
            stage, message = job["events"].get_nowait()
            job["latest_message"], job["stage"] = message, stage
        except queue.Empty:
            break

    future = job["future"]
    if future.done():
        try:
            outcome = future.result()
        except Exception as exc:
            outcome = {"saved": False, "stage": "agents", "error": str(exc)[:1500]}
        _record_analysis_outcome(st.session_state, tender_id, outcome)
        st.rerun()

    with st.container(border=True):
        st.markdown("**Analysis running**")
        st.caption(job.get("latest_message") or "Preparing tender evidence…")
        st.caption("You can continue using the workspace while this tender is analyzed.")


def run_and_save_analysis(tender: dict, profile: dict, user_id: int, saved_analysis: dict | None) -> bool:
    """Queue the sequential evidence-grounded analysis so the Streamlit script stays responsive."""
    job_key = f"analysis_job_{tender['id']}"
    existing = st.session_state.get(job_key)
    if existing and not existing["future"].done():
        st.info("Analysis is already running for this tender.")
        return False
    events = queue.Queue()
    future = _analysis_executor().submit(_run_analysis_background, user_id, tender["id"], events)
    st.session_state[job_key] = {
        "future": future, "events": events, "started_at": time.perf_counter(),
        "latest_message": "Preparing tender evidence...", "stage": "starting",
    }
    return True

def render_requirements_editor(tender: dict, profile: dict, user_id: int, saved_analysis: dict | None):
    """
    Let the user review, add, correct or delete tender requirements when automatic
    extraction missed some (or got them wrong). Saved edits drive every subsequent analysis.
    """
    import pandas as pd

    tender_id = tender["id"]
    custom_reqs = get_custom_requirements(tender_id, user_id)
    version_key = f"req_editor_version_{tender_id}"
    st.session_state.setdefault(version_key, 0)

    if custom_reqs is not None:
        rows, source_label = custom_reqs, "your edited list"
    elif saved_analysis and saved_analysis.get("compliance_matrix"):
        rows, source_label = saved_analysis["compliance_matrix"], "the last analysis"
    else:
        cache_key = f"auto_reqs_{tender_id}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = extract_all_structured_requirements(
                tender.get("extracted_text") or "", profile, pdf_path=tender.get("filepath")
            ) if tender.get("extracted_text") else []
        rows, source_label = st.session_state[cache_key], "automatic detection"

    count = len(rows)
    few = count < 3
    stale = bool(
        saved_analysis
        and (saved_analysis.get("structured_tender_data") or {}).get("requirements_hash", "auto") != requirements_hash(custom_reqs)
    )

    title = f"✏️ Tender Requirements ({count}) — review, add or correct"
    with st.expander(title, expanded=few or stale):
        if few:
            st.warning(
                f"⚠️ Only **{count}** requirement(s) were detected in this document. "
                "Add any missing eligibility, technical or financial requirements below, then save and re-run the analysis."
            )
        if stale:
            st.info("ℹ️ Requirements were edited after the last analysis. Re-run the analysis to apply them.")
        st.caption(
            f"Showing requirements from **{source_label}**. Add rows with ➕ at the bottom of the table, edit any cell, "
            "or select rows and delete them. Saved requirements replace automatic detection for this tender."
        )

        df = pd.DataFrame([{c: r.get(c) for c in REQ_EDITOR_COLUMNS} for r in rows], columns=REQ_EDITOR_COLUMNS)
        if df.empty:
            df = pd.DataFrame(columns=REQ_EDITOR_COLUMNS)
        df["is_mandatory"] = df["is_mandatory"].map(lambda v: True if v is None or (isinstance(v, float) and pd.isna(v)) else bool(v)).astype(bool)
        df["source_page"] = pd.to_numeric(df["source_page"], errors="coerce").fillna(1).astype(int)
        for c in ["requirement_id", "category", "requirement", "required_document", "source_quotation"]:
            df[c] = df[c].fillna("").astype(str)
        df.loc[~df["category"].isin(REQUIREMENT_CATEGORIES), "category"] = "General"

        edited = st.data_editor(
            df,
            key=f"req_editor_{tender_id}_{st.session_state[version_key]}",
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "requirement_id": st.column_config.TextColumn("ID", help="Clause number, e.g. E-01. Auto-generated if blank.", width="small"),
                "category": st.column_config.SelectboxColumn("Category", options=REQUIREMENT_CATEGORIES, default="Eligibility", required=True, width="small"),
                "requirement": st.column_config.TextColumn("Requirement *", help="Short description of what the tender requires.", required=True, width="medium"),
                "is_mandatory": st.column_config.CheckboxColumn("Mandatory", default=True, width="small"),
                "source_page": st.column_config.NumberColumn("Page", min_value=1, step=1, default=1, width="small"),
                "required_document": st.column_config.TextColumn("Required Document", width="medium"),
                "source_quotation": st.column_config.TextColumn("Tender Clause Text", help="Paste the exact tender wording for evidence-grounded evaluation.", width="large"),
            },
        )

        b1, b2, b3 = st.columns(3)
        save_clicked = b1.button("💾 Save Requirements", key=f"req_save_{tender_id}", use_container_width=True)
        rerun_clicked = b2.button("⚡ Save & Re-run Analysis", key=f"req_save_run_{tender_id}", use_container_width=True, type="primary")
        reset_clicked = b3.button(
            "↺ Reset to Auto-Detected", key=f"req_reset_{tender_id}", use_container_width=True,
            disabled=custom_reqs is None, help="Discard your edits and use automatic requirement detection again.",
        )

        if save_clicked or rerun_clicked:
            cleaned = normalize_custom_requirements(edited.to_dict("records"))
            if not cleaned:
                st.error("Add at least one requirement (the Requirement column cannot be empty).")
            else:
                save_custom_requirements(tender_id, user_id, cleaned)
                st.session_state[version_key] += 1
                if rerun_clicked:
                    if run_and_save_analysis(tender, profile, user_id, saved_analysis):
                        st.success(f"Analysis started with {len(cleaned)} requirement(s). Progress will appear here.")
                        st.rerun()
                else:
                    st.success(f"Saved {len(cleaned)} requirement(s). Re-run the analysis to evaluate them.")
                    st.rerun()

        if reset_clicked:
            clear_custom_requirements(tender_id, user_id)
            st.session_state.pop(f"auto_reqs_{tender_id}", None)
            st.session_state[version_key] += 1
            st.rerun()


# --- Load the shared application design system ---
with open(Path(__file__).with_name("styles.css"), encoding="utf-8-sig") as styles_file:
    _css_body = styles_file.read().lstrip("\ufeff")
    st.markdown(f"<style>{_css_body}</style>", unsafe_allow_html=True)


# ============================================================
# AUTHENTICATION GATEWAY
# ============================================================
def render_auth_gateway():
    """Render enterprise login and registration forms for unauthenticated access."""
    with st.container(key="auth-panel"):
        st.markdown("""
        <div class="auth-container">
            <div class="auth-logo">
                <h2>TenderForge</h2>
                <p>Enterprise Tender Review & Compliance Workspace</p>
            </div>
        </div>
        """, unsafe_allow_html=True)

        auth_tab_login, auth_tab_register = st.tabs(["Sign In", "Create Account"])

        with auth_tab_login:
            with st.form("login_form"):
                st.markdown("##### Sign in to your workspace")
                email = st.text_input("Work Email", placeholder="name@company.com", key="login_email")
                password = st.text_input("Password", type="password", placeholder="Enter your password", key="login_password")

                submit_login = st.form_submit_button("Sign In", use_container_width=True, type="primary")

                if submit_login:
                    success, msg, user = authenticate_user(email, password)
                    if success:
                        st.session_state["authenticated"] = True
                        st.session_state["user"] = user
                        st.session_state["current_page"] = "📊 Dashboard"
                        st.success("Login successful. Opening your workspace...")
                        st.rerun()
                    else:
                        st.error(msg)

        with auth_tab_register:
            with st.form("register_form"):
                st.markdown("##### Create your workspace account")
                reg_name = st.text_input("Full Name / Organization", placeholder="e.g. Jane Doe", key="reg_name")
                reg_email = st.text_input("Work Email *", placeholder="name@company.com", key="reg_email")
                reg_password = st.text_input("Password (min 6 characters) *", type="password", placeholder="Create a password", key="reg_password")
                reg_confirm = st.text_input("Confirm Password *", type="password", placeholder="Re-enter your password", key="reg_confirm")

                submit_reg = st.form_submit_button("Create Account", use_container_width=True, type="primary")

                if submit_reg:
                    if reg_password != reg_confirm:
                        st.error("Passwords do not match.")
                    else:
                        success, msg, user = create_user(reg_email, reg_password, reg_name)
                        if success:
                            st.session_state["authenticated"] = True
                            st.session_state["user"] = user
                            st.session_state["current_page"] = "📊 Dashboard"
                            st.success("Account created. Opening your workspace...")
                            st.rerun()
                        else:
                            st.error(msg)

        st.markdown("""
        <div class="auth-security">
            Account passwords are protected with salted PBKDF2-SHA256 hashing.
            Each workspace is isolated to its signed-in account.
        </div>
        """, unsafe_allow_html=True)


# ============================================================
# AUTHENTICATED WORKSPACE
# ============================================================
_INPUT_HINTS = {
    "certificate": ("Certificate / registration details", "Number, issuing body and validity date"),
    "text": ("Details", ""),
    "long_text": ("Company details", "Use the specific prompts above to describe your experience or capability."),
    "document": ("Reference / notes (optional)", "e.g. FY covered, document date"),
}


def evidence_state_label(status, review_method=None) -> str:
    """Honest label for an evidence value's state (in-app checks are never presented as independent verification)."""
    status = str(status or "unverified").lower()
    if status == "verified":
        return ("VERIFIED · document-checked in-app (not independent verification)"
                if review_method == "document_excerpt_check" else "VERIFIED · no in-app review record")
    if status == "rejected":
        return "REJECTED / WITHDRAWN"
    return "UNVERIFIED · user-provided"


def _field_check(docs: list[dict], field_key: str) -> dict | None:
    """The document-checked value recorded for a field on its latest document version, if any."""
    if not docs:
        return None
    return next((v for v in docs[0].get("extracted_values") or [] if v.get("value_key") == field_key), None)


def _field_evidence_state(definition: dict, value: str, docs: list[dict]) -> tuple[str, str, str]:
    check = _field_check(docs, definition["field_key"])
    if check and check.get("verification_status") == "verified":
        return "Verified", "pill-satisfied", f"The quoted value matched page {check.get('source_page')} in the uploaded document (in-app check)."
    if check and check.get("verification_status") == "rejected":
        return "Needs review", "pill-notsatisfied", "The saved evidence check did not verify this value. Review the page and excerpt."
    if docs:
        return "Evidence uploaded", "pill-inforeq", "The file is saved but has not been checked against the stated value."
    if value:
        return "Information provided", "pill-inforeq", "Saved as a company claim; it has not been verified against a supporting document."
    return "Not provided", "pill-unresolved", "Enter the requested information and save it to your profile."


def _profile_group(field_key: str) -> str:
    prefix = str(field_key or "").split(".", 1)[0]
    return {
        "financial": "Financial Documents",
        "experience": "Eligibility and Past Experience",
        "registration": "Eligibility and Past Experience",
        "authorization": "Eligibility and Past Experience",
        "capability": "Technical Capabilities",
        "certification": "Certifications and Regulatory Approvals",
    }.get(prefix, "Company Information")


def _profile_field_guidance(definition: dict) -> dict:
    links = definition.get("requirements") or []
    primary = links[0] if links else {}
    item = {
        "requirement": primary.get("requirement_text") or definition.get("label"),
        "source_quotation": primary.get("source_quotation") or "",
        "required_document": primary.get("required_document") or "",
    }
    return PF.guidance_for_requirement(item, definition.get("field_key", ""))


def _tender_reference(filename: str, page) -> str:
    short_name = Path(str(filename or "Tender")).stem
    return f"{short_name} - Page {page}" if page else short_name

def _requirement_simple_summary(title: str, quote: str, guidance: dict, category: str) -> str:
    """Create a short, company-facing explanation without adding requirements not present in the clause."""
    text = " ".join(str(title or "").split())
    clause = " ".join(str(quote or "").split())
    lower = f"{text} {clause}".lower()

    if any(term in lower for term in ("annual turnover", "average turnover", "turnover")):
        return "Your company must demonstrate that its annual turnover meets the financial threshold specified in the tender."
    if "gst" in lower:
        return "Your company must provide valid GST registration details and supporting registration evidence."
    if any(term in lower for term in ("emd", "earnest money", "bid security")):
        return "Your company must arrange the Earnest Money Deposit or identify a tender-approved exemption."
    if "similar project" in lower or "past experience" in lower or "previous experience" in lower:
        return "You need to demonstrate that your company has completed relevant projects or supplies."
    if any(term in lower for term in ("make and model", "make/model", "technical specification", "technical compliance")):
        return "You must show that the product or service offered meets the technical requirements in the tender."
    if any(term in lower for term in ("service centre", "service center", "after-sales", "after sales")):
        return "Your company must describe its service and support arrangements for the offered solution."
    if any(term in lower for term in ("incorporation", "registration", "company registration")):
        return "Your company must confirm its legal registration and provide the relevant registration evidence."
    if any(term in lower for term in ("declaration", "undertaking", "affidavit")):
        return "You must confirm the declaration or undertaking requested by the tender."
    if clause:
        return f"Based on the tender clause, you need to provide the {text.lower() or 'required information'} and evidence requested for this requirement."
    return "Could not determine from tender. The original tender clause was not available for a confident explanation."

def _company_facing_status(status: str, source_valid: bool, quote: str) -> str:
    if not str(quote or "").strip() or not source_valid:
        return "Needs Clarification"
    return {
        "Not provided": "Missing",
        "Information provided": "Information Provided",
        "Evidence uploaded": "Evidence Uploaded",
        "Verified": "Verified",
        "Needs review": "Needs Review",
    }.get(status, status or "Missing")

def _requirement_actions(guidance: dict) -> list[str]:
    actions = []
    for item in guidance.get("information") or []:
        value = " ".join(str(item or "").split())
        if value and value not in actions:
            actions.append(value)
    for item in guidance.get("documents") or []:
        value = " ".join(str(item or "").split())
        if value and value not in actions:
            actions.append(f"Upload supporting document: {value}")
    return actions[:8]

def _render_requirement_guidance(requirement: dict, guidance: dict, source_valid: bool):
    quote = str(requirement.get("source_quotation") or requirement.get("tender_evidence") or "")
    source_page = requirement.get("source_page")
    citation_status = "Validated source citation" if source_valid else "Needs clarification - citation not validated"
    with st.container():
        st.markdown("**Original tender clause**")
        st.markdown(f"<div class='tf-source-page'>PDF page: {h(source_page if source_page else 'Could not determine from tender')}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='tf-original-clause'>{h(quote or 'Could not determine from tender')}</div>", unsafe_allow_html=True)
        for merged in requirement.get("merged_duplicates") or []:
            if isinstance(merged, dict) and merged.get("source_quotation"):
                st.markdown(f"**Repeated clause · PDF page {h(merged.get('source_page') or 'not located')}**")
                st.markdown(f"> {h(merged['source_quotation'])}")
        st.markdown(f"<div class='tf-source-status'>{h(citation_status)}</div>", unsafe_allow_html=True)

def _section2_information_missing(requirement: dict, saved: dict | None, profile: dict | None = None) -> bool:
    """Whether a tender-scoped response is still needed for this clause."""
    if requirement.get("document_checklist_item"):
        return False
    response = (saved or {}).get("response") or {}
    analysis = requirement.get("section2_analysis") or {}
    if analysis.get("status") == "analyzed":
        paths = analysis.get("conditional_paths") or []
        selected = next((path for path in paths if path.get("key") == response.get("conditional_path")), None)
        field_groups = ([selected.get("information_fields", [])] if selected else
                        [path.get("information_fields", []) for path in paths] if paths else
                        [analysis.get("information_fields", [])])
        required_fields = [field for group in field_groups for field in group
                           if isinstance(field, dict) and field.get("required", True)]
        if required_fields:
            return any(not str(response.get(field["key"]) or field.get("existing_value") or "").strip()
                       for field in required_fields)
    if any(str(value or "").strip() for key, value in response.items() if key != "conditional_path"):
        return False
    definition = _reusable_definition_for_requirement(requirement) if profile else None
    return not bool(definition and str(PF.current_value(profile, definition) or "").strip())

def _section2_documents_missing(requirement: dict, saved: dict | None, guidance: dict) -> bool:
    """Count only documents expressly named by the tender, never heuristic suggestions."""
    analysis = requirement.get("section2_analysis") or {}
    if analysis.get("status") == "analyzed":
        paths = analysis.get("conditional_paths") or []
        response = (saved or {}).get("response") or {}
        selected = next((path for path in paths if path.get("key") == response.get("conditional_path")), None)
        required_docs = (selected.get("required_documents") if selected else
                         analysis.get("required_documents", []) if not paths else [])
        explicit = bool(required_docs) or bool(paths and not selected and any(p.get("required_documents") for p in paths))
        mandatory = explicit
    else:
        explicit = bool(requirement.get("document_checklist_item") or
                    str(requirement.get("required_document") or "").strip())
        mandatory = bool(requirement.get("is_mandatory", True))
    if not explicit or not mandatory:
        return False
    return not bool((saved or {}).get("documents"))

def _render_tender_specific_response(user_id: int, tender_summary: dict, requirement: dict,
                                     saved_responses: dict[str, dict], payment_value: str = "",
                                     selected_tender_id: int | None = None, profile: dict | None = None):
    """Collect tender-only answers and PDFs without adding them to the company profile."""
    import hashlib
    import pandas as pd

    tender_id = resolve_tender_id(tender_summary, selected_tender_id)
    saved_tender = get_tender_by_id(tender_id, user_id) if tender_id else None
    if not saved_tender or int(saved_tender["id"]) != tender_id:
        st.error("This tender's saved record could not be resolved for your account. Tender-specific answers and uploads are disabled; your reusable company profile remains available.")
        return
    requirement_id = str(requirement.get("requirement_id") or "REQ")
    section2 = requirement.get("section2_analysis") or {}
    title = str(section2.get("title") or requirement_title(requirement))
    quote = str(requirement.get("source_quotation") or requirement.get("tender_evidence") or "")
    normalized_quote = " ".join(quote.split())
    quote_hash = hashlib.sha256(normalized_quote.encode("utf-8")).hexdigest()
    saved = saved_responses.get(requirement_id)
    stale = bool(saved and saved.get("source_quote_sha256") != quote_hash)
    current = saved if saved and not stale else None
    values = (current or {}).get("response") or {}
    if not values and profile and not requirement.get("document_checklist_item"):
        reusable = _reusable_definition_for_requirement(requirement)
        if reusable:
            # Prefill from Section 1 for convenience. Saves still go only to the
            # tender-scoped response table below.
            profile_value = PF.current_value(profile, reusable)
            if str(profile_value or "").strip():
                values = {"details": str(profile_value)}
    documents = (current or {}).get("documents") or []
    reference = _tender_reference(tender_summary.get("filename"), requirement.get("source_page"))
    section2 = requirement.get("section2_analysis") or {}
    guidance = PF.guidance_for_requirement(requirement)
    if section2.get("status") == "analyzed":
        info_lines = [field.get("label") for field in section2.get("information_fields", [])
                      if isinstance(field, dict) and field.get("label")]
        required_labels = [doc.get("label") or doc.get("document_type")
                           for doc in section2.get("required_documents", []) if isinstance(doc, dict)]
        optional_labels = [doc.get("label") or doc.get("document_type")
                           for doc in section2.get("optional_evidence", []) if isinstance(doc, dict)]
        guidance = {"information": info_lines, "documents": required_labels + optional_labels,
                    "explicit_document": bool(required_labels), "document_label": "Tender clause evidence"}
    if requirement.get("document_checklist_item"):
        req_doc_title = str(requirement.get("required_document") or requirement.get("requirement") or "Required document")
        guidance = {
            "information": [],
            "documents": [req_doc_title],
            "document_label": "Required document listed in the tender checklist",
            "explicit_document": True,
        }
    widget_id = re.sub(r"[^A-Za-z0-9_]", "_", f"{tender_id}_{requirement_id}")[:72]
    payment = bool(re.search(r"earnest money|emd|bid security|tender (?:document )?fee|processing fee", title + " " + quote, re.I))
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
    category = section2.get("category") or requirement.get("category") or "Eligibility"

    cat_icon = {
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
            st.markdown(
                f"<div class='tf-card-description'>{h(_requirement_simple_summary(title, quote, guidance, category))}</div>",
                unsafe_allow_html=True,
            )
        with col_status:
            display_status = _company_facing_status(evidence_status, source_reference_is_valid(requirement), quote)
            display_class = "pill-unresolved" if display_status in ("Missing", "Needs Clarification") else status_class
            st.markdown(f"<span class='{display_class}'>{h(display_status)}</span>", unsafe_allow_html=True)
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

        _render_requirement_guidance(requirement, guidance, source_reference_is_valid(requirement))

        with st.form(f"tender_response_form_{widget_id}", border=False):
            col_info, col_docs = st.columns([1, 1], gap="large")
            with col_info:
                st.markdown(f"**Information Required:** {h(info_req)}")
                conditional_paths = section2.get("conditional_paths") if section2.get("status") == "analyzed" else []
                active_fields = section2.get("information_fields", []) if section2.get("status") == "analyzed" else []
                active_docs = section2.get("required_documents", []) if section2.get("status") == "analyzed" else []
                active_optional_docs = section2.get("optional_evidence", []) if section2.get("status") == "analyzed" else []
                active_path = None
                response_path_key = ""
                if conditional_paths:
                    path_labels = [path["label"] for path in conditional_paths]
                    old_path = values.get("conditional_path") or ""
                    path_index = next((i for i, path in enumerate(conditional_paths)
                                       if path.get("key") == old_path), 0)
                    path_choice = st.selectbox("Applicable compliance path", path_labels, index=path_index,
                                               key=f"resp_path_{widget_id}")
                    active_path = next(path for path in conditional_paths if path["label"] == path_choice)
                    active_fields = active_path.get("information_fields", [])
                    active_docs = active_path.get("required_documents", [])
                    active_optional_docs = active_path.get("optional_evidence", [])
                    response_path_key = active_path.get("key")
                if active_fields and section2.get("status") == "analyzed":
                    response = {}
                    field_values = {**{field.get("key"): field.get("existing_value") for field in active_fields}, **values}
                    for field in active_fields:
                        key, label = field["key"], field["label"]
                        field_value = field_values.get(key)
                        widget_key = f"resp_dynamic_{widget_id}_{key}"
                        if field.get("input_type") == "select" and field.get("options"):
                            options = field["options"]
                            selection = field_value if field_value in options else options[0]
                            answer = st.selectbox(label, options, index=options.index(selection), key=widget_key)
                        elif field.get("input_type") == "boolean":
                            answer = st.selectbox(label, ["Select", "Yes", "No"],
                                                  index={None: 0, "Yes": 1, "No": 2}.get(field_value, 0), key=widget_key)
                        elif field.get("input_type") == "textarea":
                            answer = st.text_area(label, value=str(field_value or ""), key=widget_key)
                        else:
                            answer = st.text_input(label, value=str(field_value or ""), key=widget_key)
                        st.caption("Prefilled from Section 1" if field.get("existing_value") not in (None, "")
                                   else "Missing from saved company profile")
                        response[key] = answer
                    parameters = section2.get("technical_parameters", [])
                    if parameters:
                        st.markdown("**Technical Parameters**")
                        for idx, parameter in enumerate(parameters):
                            p_key = f"technical_{idx}_{re.sub(r'[^A-Za-z0-9_]', '_', parameter['parameter'])}"
                            st.markdown(f"**{h(parameter['parameter'])}** · Required: {h(parameter['required_specification'])}")
                            response[p_key + "_offered_value"] = st.text_input(
                                f"{parameter['parameter']} — Offered Value", value=values.get(p_key + "_offered_value", ""), key=f"{widget_id}_{p_key}_value")
                            response[p_key + "_make_model"] = st.text_input(
                                f"{parameter['parameter']} — Make / Model", value=values.get(p_key + "_make_model", ""), key=f"{widget_id}_{p_key}_model")
                            response[p_key + "_compliance"] = st.selectbox(
                                f"{parameter['parameter']} — Compliance", ["Select", "Compliant", "Deviation"], key=f"{widget_id}_{p_key}_compliance")
                            if response[p_key + "_compliance"] == "Deviation":
                                response[p_key + "_deviation"] = st.text_input(
                                    f"{parameter['parameter']} — Deviation Remarks", value=values.get(p_key + "_deviation", ""), key=f"{widget_id}_{p_key}_deviation")
                elif payment:
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
                        response_label = f"{title} Details" if title else "Requirement Details"
                        response = {"details": st.text_area(response_label, value=values.get("details", ""),
                                                             placeholder=f"Enter details that address: {title}", key=f"resp_details_{widget_id}")}

                if conditional_paths:
                    response["conditional_path"] = response_path_key

                if values:
                    saved_val_strs = [f"{k}: {v}" for k, v in values.items() if v and not isinstance(v, list)]
                    if saved_val_strs:
                        val_summary = "; ".join(saved_val_strs)
                        if len(val_summary) > 75: val_summary = val_summary[:72] + "…"
                        st.markdown(f"<div class='tf-saved-chip'><b>Saved Info:</b> {h(val_summary)}</div>", unsafe_allow_html=True)
                    if values.get("projects"):
                        st.markdown(f"<div class='tf-saved-chip'><b>Saved Projects:</b> {len(values['projects'])} project(s)</div>", unsafe_allow_html=True)

            with col_docs:
                doc_sources = active_docs if section2.get("status") == "analyzed" else []
                if section2.get("status") == "analyzed":
                    if doc_sources:
                        doc_text = "; ".join(str(doc.get("label") or doc.get("document_type")) for doc in doc_sources)
                        is_mandatory = any(doc.get("mandatory", True) for doc in doc_sources)
                        doc_badge = "Mandatory" if is_mandatory else "Optional"
                    elif active_path and active_optional_docs:
                        doc_text = "; ".join(str(doc.get("label") or doc.get("document_type"))
                                              for doc in active_optional_docs)
                        doc_badge, is_mandatory = "Optional", False
                    elif active_path:
                        doc_text, doc_badge, is_mandatory = "No specific supporting document identified in this clause.", "None", False
                    else:
                        no_mandatory = {**section2, "required_documents": [],
                                        "optional_evidence": section2.get("optional_evidence", [])}
                        doc_text, doc_badge, is_mandatory = documents_required_label(
                            {**requirement, "section2_analysis": no_mandatory}, guidance, None)
                    badge_class = "tf-badge-mandatory" if doc_badge == "Mandatory" else "tf-badge-optional"
                    badge_html = f"<span class='{badge_class}'>{h(doc_badge)}</span>" if doc_badge in ("Mandatory", "Optional") else ""
                st.markdown(f"**Documents Required:** {badge_html} {h(doc_text)}", unsafe_allow_html=True)
                for doc_item in doc_sources:
                    availability = "Already available in company documents" if doc_item.get("available") else "Not found in company documents"
                    st.caption(f"{doc_item.get('label') or doc_item.get('document_type')} · {availability} · PDF page {doc_item.get('source_page') or requirement.get('source_page')} · {doc_item.get('source_quote') or ''}")
                if active_optional_docs:
                    st.markdown("**Optional supporting evidence**")
                    for doc_item in active_optional_docs:
                        availability = "Already available in company documents" if doc_item.get("available") else "Not found in company documents"
                        st.caption(f"{doc_item.get('label') or doc_item.get('document_type')} · Optional · {availability} · PDF page {doc_item.get('source_page') or requirement.get('source_page')} · {doc_item.get('source_quote') or ''}")
                company_pdfs = [doc for doc in get_company_documents(user_id)
                                if str(doc.get("filename") or "").lower().endswith(".pdf")] if is_mandatory or doc_badge == "Optional" else []
                existing_ids = [doc["id"] for doc in company_pdfs]
                existing_document_id = None
                if company_pdfs:
                    existing_document_id = st.selectbox(
                        "Attach existing company PDF (optional)", [None, *existing_ids],
                        format_func=lambda doc_id: "Choose a saved PDF" if doc_id is None else
                            f"{next(doc['filename'] for doc in company_pdfs if doc['id'] == doc_id)} · existing evidence",
                        key=f"resp_existing_doc_{widget_id}")
                upload = None
                if doc_badge in ("Mandatory", "Optional"):
                    upload_label = f"Upload {doc_text} (PDF)" + (" — Mandatory" if is_mandatory else " — Optional")
                    upload = st.file_uploader(upload_label, type=["pdf"], key=f"resp_file_{widget_id}")
                    st.caption("PDF · Uploaded files remain unverified until reviewed.")
                else:
                    st.caption("No specific supporting document identified in this clause.")
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
                        st.error(f"The tender response could not be saved: {exc}")


        with st.popover("View original tender clause", use_container_width=True):
            refs = requirement.get("tender_evidence_refs") or []
            citation = ", ".join(f"page {ref.get('page_number')} · {ref.get('chunk_id')}" for ref in refs)
            st.markdown(f"**Original Tender Clause (Page {h(source_page or 'not located')})**")
            st.markdown(f"> {h(quote or requirement.get('requirement') or 'Source clause not retained.')}")
            st.caption(f"Source: {reference}" + (f" · {h(citation)}" if citation else "") +
                       ". An answer or upload does not itself establish compliance.")


def _get_profile_field_sync(user_id: int) -> tuple[dict, list[dict]]:
    tenders = get_tenders_with_analysis(user_id)
    sync_key = tuple((t["id"], t.get("analysis_id")) for t in tenders if t.get("analysis_id"))
    cache_key = f"profile_field_sync_{user_id}"
    cache = st.session_state.get(cache_key)
    if not cache or cache[0] != sync_key:
        cache = (sync_key, PF.sync_profile_fields_for_user(user_id))
        st.session_state[cache_key] = cache
    return cache[1], get_profile_field_definitions(user_id)


def render_profile_completion_summary(user_id: int, profile: dict):
    tracked_fields = ["company_name", "industry", "years_of_experience", "annual_turnover", "certifications",
                      "technical_capabilities", "past_projects", "financial_details", "contact_name",
                      "contact_email", "contact_phone", "contact_address"]
    filled = sum(bool(str(profile.get(key) or "").strip()) for key in tracked_fields)
    st.metric("Company profile fields completed", f"{filled} / {len(tracked_fields)}")
    st.caption("Saved once and reused across tenders. A complete profile is not verified evidence.")


def _profile_inconsistency_messages(profile: dict) -> list[str]:
    messages = []
    turnover = Analyzer.reconcile_turnover_statements(Analyzer.collect_turnover_statements(profile))
    if turnover.get("conflict"):
        messages.append("Turnover information is inconsistent: " + turnover["conflict"])

    structured_years = parse_company_years(str(profile.get("years_of_experience") or ""))
    if structured_years is not None:
        sources = [(key, str(profile.get(key) or "")) for key in ("financial_details", "past_projects", "technical_capabilities")]
        sources.extend((str(row.get("field_key") or "Additional company information"), str(row.get("field_value") or ""))
                       for row in profile.get("custom_fields") or [])
        for label, text in sources:
            match = re.search(r"(?i)(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:business\s+|industry\s+|operating\s+|operational\s+|professional\s+)?experience\b|"
                              r"\b(?:operating|in business|in operation)\s+for\s+(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b", text)
            if not match:
                continue
            descriptive_years = float(match.group(1) or match.group(2))
            if descriptive_years != structured_years:
                messages.append(f"Years of experience conflict: the dedicated field says {structured_years:g} years, while {label} says {descriptive_years:g} years. Correct the relevant saved value; neither value was changed.")
    return messages


def render_profile_inconsistency_warnings(profile: dict):
    for message in _profile_inconsistency_messages(profile):
        st.warning(message)


def _profile_tender_selection_changed(user_id: int):
    selected = st.session_state.get(f"profile_tender_select_{user_id}")
    st.session_state["selected_tender_id"] = selected
    st.session_state[f"analysis_tender_select_{user_id}"] = selected


def _analysis_tender_selection_changed(user_id: int):
    selected = st.session_state.get(f"analysis_tender_select_{user_id}")
    st.session_state["selected_tender_id"] = selected
    st.session_state[f"profile_tender_select_{user_id}"] = selected


def _navigation_choice_changed():
    st.session_state["current_page"] = st.session_state.get("navigation_choice", st.session_state.get("current_page"))


def _reusable_definition_for_requirement(item: dict) -> dict | None:
    candidate = dict(item)
    if normalize_status(candidate.get("status")) == "NOT APPLICABLE":
        return None
    if normalize_status(candidate.get("status")) not in ("INFORMATION REQUIRED", "UNRESOLVED", "CONFIRMED GAP"):
        candidate["status"] = "INFORMATION REQUIRED"
    plan = PF.build_field_plan([candidate])
    if not plan.get("links") or not plan.get("definitions"):
        return None
    definition = dict(plan["definitions"][0])
    definition["requirements"] = plan["links"]
    definition["required"] = bool(item.get("is_mandatory", True))
    return definition


def _render_reusable_requirement_card(user_id: int, profile: dict, item: dict, definition: dict,
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


def _response_evidence_state(requirement: dict, saved: dict | None) -> str:
    return response_evidence_status(requirement, saved)


def _requirement_source_is_valid(requirement: dict) -> bool:
    """Accept only a source quotation explicitly checked against a real tender page."""
    return source_reference_is_valid(requirement)


def _requirement_status(requirement: dict, saved: dict | None, definition: dict | None,
                        profile: dict) -> str:
    if not _requirement_source_is_valid(requirement):
        return "Needs review"
    if definition:
        return _field_evidence_state(definition, PF.current_value(profile, definition),
                                     PF.field_documents(profile, definition))[0]
    return _response_evidence_state(requirement, saved)


def _checklist_document_is_explicit(entry: dict) -> bool:
    """Keep a checklist card only when its name and submission instruction are source grounded."""
    if not isinstance(entry, dict) or entry.get("source_verified") is not True:
        return False
    name = " ".join(str(entry.get("document") or "").split()).casefold()
    clause = " ".join(str(entry.get("source_quotation") or entry.get("tender_evidence") or "").split()).casefold()
    action = re.search(r"\b(?:submit|provide|attach|upload|enclose|furnish|filled|fill|sign)\b", clause)
    return bool(name and len(name) >= 4 and name in clause and action)


def _checklist_document_covered(entry: dict, matrix: list[dict]) -> bool:
    """Avoid a second checklist card when the same explicit document is on a clause card."""
    name = " ".join(str(entry.get("document") or "").split()).casefold()
    tokens = {token for token in re.findall(r"[a-z0-9]+", name)
              if token not in {"the", "and", "for", "of", "with", "document", "form", "certificate"}}
    if not tokens:
        return False
    for row in matrix:
        section2 = row.get("section2_analysis") or {}
        corpus = " ".join(str(value or "") for value in (
            row.get("requirement"), row.get("source_quotation"), row.get("required_document"),
            *(doc.get("label", "") for doc in section2.get("required_documents", []) if isinstance(doc, dict))))
        corpus_tokens = set(re.findall(r"[a-z0-9]+", corpus.casefold()))
        if tokens.issubset(corpus_tokens):
            return True
    return False


def render_tender_requested_fields(user_id: int, profile: dict):
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
        if not document or not _checklist_document_is_explicit(entry) or _checklist_document_covered(entry, matrix):
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
        # Section 2 answers are tender scoped. Reusable Section 1 fields are read as
        # context/prefills only and must never be written by this workflow.
        definition = None
        guidance = PF.guidance_for_requirement(item)
        status = _requirement_status(item, saved, definition, profile)
        section2_state = item.get("section2_analysis") or {}
        if section2_state.get("status") == "needs_review" and _requirement_source_is_valid(item):
            status = "Needs review"
        records.append({"item": item, "saved": saved, "definition": definition, "guidance": guidance,
                        "status": status,
                        "requirement_type": classify_requirement_type(item, guidance),
                        "needs_information": _section2_information_missing(item, saved, profile),
                        "needs_documents": _section2_documents_missing(item, saved, guidance),
                        "group": requirement_group(item, guidance)})

    status_counts = {label: sum(record["status"] == label for record in records)
                     for label in ("Not provided", "Information provided", "Evidence uploaded", "Verified", "Needs review")}
    completed = status_counts["Verified"]
    information_needed = sum(record["status"] in ("Not provided", "Information provided", "Needs review") or record["needs_information"] for record in records)
    documents_pending = sum(record["status"] == "Evidence uploaded" or record["needs_documents"] for record in records)
    completion = round((completed / len(records) * 100) if records else 0)

    # Top Tender Summary Card
    st.markdown(f"""
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
    """, unsafe_allow_html=True)
    st.progress(completion / 100, text=f"Requirements Completion: {completion}%")

    metric_columns = st.columns(4)
    metric_columns[0].metric("Total Requirements", len(records))
    metric_columns[1].metric("Completed / Verified", completed)
    metric_columns[2].metric("Information Needed", information_needed)
    metric_columns[3].metric("Documents Pending", documents_pending)

    filter_counts = {
        "All": len(records),
        "Information Required": sum(r["needs_information"] for r in records),
        "Documents Required": sum(r["needs_documents"] for r in records),
        "Verified": sum(r["status"] == "Verified" for r in records),
    }
    selected_filter = st.radio(
        "Requirement Filter",
        [f"{label} ({count})" for label, count in filter_counts.items()],
        horizontal=True,
        label_visibility="collapsed",
        key=f"profile_requirement_filter_{user_id}",
    )
    selected_filter_name = selected_filter.rsplit(" (", 1)[0]

    visible_records = records
    if selected_filter_name == "Information Required":
        visible_records = [r for r in records if r["needs_information"]]
    elif selected_filter_name == "Documents Required":
        visible_records = [r for r in records if r["needs_documents"]]
    elif selected_filter_name == "Verified":
        visible_records = [r for r in records if r["status"] == "Verified"]

    if not visible_records:
        st.info("No requirements match this filter.")
    else:
        for record in visible_records:
            item = record["item"]
            definition = record["definition"]
            requirement_id = str(item.get("requirement_id") or "")
            section2 = item.get("section2_analysis") or {}
            requirement_name = str(section2.get("title") or requirement_title(item))
            requirement_quote = str(item.get("source_quotation") or item.get("tender_evidence") or "")
            requirement_status = _company_facing_status(
                record["status"],
                _requirement_source_is_valid(item),
                requirement_quote,
            )
            summary = str(section2.get("description") or _requirement_simple_summary(
                requirement_name, requirement_quote, record["guidance"], item.get("category") or ""))
            source_pages = item.get("source_pages") or ([item.get("source_page")] if item.get("source_page") else [])
            page_label = f"Pages {', '.join(str(page) for page in source_pages)}" if source_pages else "Page unavailable"
            with st.expander(f"{requirement_name} · {summary} · {page_label} · {requirement_status}", expanded=False):
                st.caption(summary)
                payment_data = financial.get("emd") if requirement_id == "FIN-EMD" else financial.get("tender_fee") if requirement_id == "FIN-FEE" else {}
                _render_tender_specific_response(user_id, tender, item, saved_responses,
                                                 str((payment_data or {}).get("value") or ""),
                                                 selected_tender_id=int(selected_id), profile=profile)

    flash = st.session_state.pop(f"tf_flash_{user_id}", None)
    if flash:
        st.success(flash)


def render_profile_evidence_storage(user_id: int, profile: dict):
    """Other company fields (not generated by tenders) and supporting documents with their evidence values."""
    import pandas as pd

    st.markdown("#### Company Supporting Documents")
    st.caption("Reusable supporting files and evidence values. Uploading or saving a value leaves it unverified until checked.")
    st.markdown("**Additional reusable company information**")
    general_keys = {"organization.business_type", "registration.gst"}
    existing_fields = [f for f in profile.get("custom_fields", [])
                       if not PF.concept_for_key(f.get("field_key")) and f.get("field_key") not in general_keys]
    with st.form("dynamic_company_profile_fields_form"):
        rows = [{"Field": f.get("field_key", ""), "Value": f.get("field_value", "")} for f in existing_fields]
        edited = st.data_editor(
            pd.DataFrame(rows, columns=["Field", "Value"]), num_rows="dynamic",
            use_container_width=True, hide_index=True, key=f"dynamic_profile_fields_{user_id}",
            column_config={"Field": st.column_config.TextColumn("Field", required=True),
                           "Value": st.column_config.TextColumn("Value")},
        )
        if st.form_submit_button("Save Additional Fields"):
            previous = {f.get("field_key"): f for f in existing_fields}
            fields, current_keys, reserved = [], set(), []
            for row in edited.to_dict(orient="records"):
                key, value = str(row.get("Field") or "").strip(), str(row.get("Value") or "").strip()
                if not key:
                    continue
                if PF.concept_for_key(key):
                    reserved.append(key)  # generated field keys are edited in the tender-requested section only
                    continue
                current_keys.add(key)
                old = previous.get(key, {})
                changed = value != str(old.get("field_value") or "").strip()
                fields.append({
                    "field_key": key, "field_value": value,
                    "source": "user_entered" if changed or not old else old.get("source", "user_entered"),
                    "source_document_id": None if changed else old.get("source_document_id"),
                    "source_excerpt": "" if changed else old.get("source_excerpt", ""),
                    "source_page": None if changed else old.get("source_page"),
                    "verification_status": "unverified" if changed or not old else old.get("verification_status", "unverified"),
                })
            save_company_profile_fields(user_id, fields)
            for removed_key in set(previous) - current_keys:
                delete_company_profile_field(user_id, removed_key)
            if reserved:
                st.warning("Not saved here (managed in the tender-requested section): " + ", ".join(reserved))
            st.success("Additional profile fields saved.")
            st.rerun()

    st.markdown("**Upload documents and record evidence values**")
    st.caption("Values you record are saved as **unverified**. A value becomes verified only through "
               "**Check against document**: the exact text you quote must be found on the stated page of the uploaded "
               "PDF and contain the value. Scanned pages cannot be checked (no OCR).")
    with st.form("company_supporting_document_form"):
        supporting_file = st.file_uploader("Upload a company supporting PDF", type=["pdf"], key=f"company_evidence_{user_id}")
        document_key = st.text_input("Document name / version group", value=supporting_file.name if supporting_file else "")
        if st.form_submit_button("Save Supporting Document"):
            if not supporting_file:
                st.error("Choose a PDF before saving.")
            else:
                doc_id = save_company_document(
                    user_id, supporting_file.name, supporting_file.getvalue(),
                    supporting_file.type or "application/pdf", document_key=document_key,
                )
                st.success(f"Supporting document saved (record {doc_id}).")
                st.rerun()

    documents = get_company_documents(user_id)
    if documents:
        docs_by_id = {d["id"]: d for d in documents}
        with st.form("company_document_value_form"):
            document_id = st.selectbox(
                "Evidence source document", list(docs_by_id),
                format_func=lambda doc_id: f"{docs_by_id[doc_id]['filename']} · version {docs_by_id[doc_id]['version']}",
                key=f"company_evidence_source_{user_id}",
            )
            value_key = st.text_input("Evidence field name", placeholder="e.g. audited_turnover_fy2025")
            value_text = st.text_input("Value stated in the document", placeholder="e.g. INR 2.4 crore")
            source_excerpt = st.text_area("Exact supporting text from the document", height=70)
            source_page = st.number_input("Source page", min_value=1, step=1, value=1)
            if st.form_submit_button("Save Evidence Value (unverified)"):
                if not value_key.strip() or not value_text.strip():
                    st.error("Enter both an evidence field name and value.")
                else:
                    save_company_document_values(user_id, document_id, [{
                        "value_key": value_key, "value_text": value_text,
                        "source_excerpt": source_excerpt, "source_page": int(source_page),
                        "verification_status": "unverified",
                    }])
                    st.success("Evidence value saved as unverified. Use 'Check against document' to verify it.")
                    st.rerun()
        flash = st.session_state.pop(f"ev_flash_{user_id}", None)
        if flash:
            (st.success if flash[0] else st.error)(flash[1])
        for doc in documents:
            st.markdown(f"**{h(doc['filename'])}** · v{h(doc['version'])} · {h(doc['size_bytes'])} bytes · document "
                        f"{h(doc.get('verification_status') or 'unverified')}"
                        + ("" if doc["extracted_values"] else " · *contents not extracted — cannot prove a requirement*"))
            for value in doc["extracted_values"]:
                c1, c2, c3 = st.columns([5, 1.2, 1])
                c1.caption(f"{value['value_key']}: {value['value_text']} · page {value.get('source_page') or '—'} · "
                           f"{evidence_state_label(value['verification_status'], value.get('review_method'))}")
                if value["verification_status"] != "verified" and c2.button("🔍 Check", key=f"ev_check_{value['id']}"):
                    outcome = review_document_value(user_id, value["id"])
                    st.session_state[f"ev_flash_{user_id}"] = (
                        outcome["passed"], "Verified (document-checked in-app)." if outcome["passed"]
                        else "Not verified: " + "; ".join(outcome["reasons"]))
                    st.rerun()
                if value["verification_status"] != "rejected" and c3.button("Withdraw", key=f"ev_withdraw_{value['id']}"):
                    withdraw_document_value(user_id, value["id"], "withdrawn by user")
                    st.rerun()
                history = get_evidence_reviews(user_id, value["id"])
                if history:
                    last = history[0]
                    c1.caption(f"Last review {last['created_at']}: {'passed' if last['passed'] else 'failed'} ({last['method']})"
                               + (f" — {last['reasons']}" if last.get("reasons") else ""))


def render_authenticated_workspace(user: dict):
    """Render the protected enterprise workspace scoped strictly to user."""
    user_id = user["id"]

    # --- Sidebar Navigation & User Info ---
    NAV_OPTIONS = [
        "📊 Dashboard",
        "🏢 Company Profile",
        "📁 My Tenders",
        "🎯 Tender Analysis",
        "📝 Proposals",
    ]

    with st.sidebar:
        st.markdown(f"""
<div class="sidebar-brand">
            <div class="sidebar-brand-mark">TF</div>
            <div>
                <div class="sidebar-brand-name">TenderForge</div>
                <div class="sidebar-brand-meta">v{h(APP_VERSION)} · Enterprise Workspace</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        user_display_name = user.get("full_name") or "User"
        user_initials = "".join([part[0].upper() for part in user_display_name.split()[:2]]) or "TF"
        st.markdown(f"""
        <div class="sidebar-account">
            <div class="sidebar-account-avatar">
                {h(user_initials)}
            </div>
            <div class="sidebar-account-copy">
                <div class="sidebar-account-name">
                    {h(company_name)}
                </div>
                <div class="sidebar-account-email">
                    {h(user['email'])}
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section-label">Workspace</div>', unsafe_allow_html=True)
        nav_index = 0
        if st.session_state["current_page"] in NAV_OPTIONS:
            nav_index = NAV_OPTIONS.index(st.session_state["current_page"])

        page_choice = st.radio(
            "Navigation",
            NAV_OPTIONS,
            index=nav_index,
            label_visibility="collapsed",
            format_func=lambda option: option.split(" ", 1)[1],
            key="navigation_choice",
            on_change=_navigation_choice_changed,
        )
        if page_choice != st.session_state["current_page"]:
            st.session_state["current_page"] = page_choice
            st.rerun()

        st.markdown(
            "<div class='sidebar-footer'>Evidence-grounded tender evaluation.<br>Private to your account.</div>",
            unsafe_allow_html=True,
        )

        if st.button("Sign Out", use_container_width=True, key="sidebar_logout"):
            st.session_state["authenticated"] = False
            st.session_state["user"] = None
            st.session_state["selected_tender_id"] = None
            st.session_state["current_page"] = "📊 Dashboard"
            st.rerun()

    # ------------------------------------------------------------
    # PAGE 1: 📊 Dashboard
    # ------------------------------------------------------------
    if st.session_state["current_page"] == "📊 Dashboard":
        stats = get_dashboard_stats(user_id)
        profile = profile or get_company_profile(user_id)
        tenders_with_analysis = get_tenders_with_analysis(user_id)
        user_display_name = user.get("full_name") or "User"
        initials = "".join(part[0].upper() for part in user_display_name.split()[:2]) or "TF"
        active_company_name = (profile.get("company_name") if profile else None) or user.get("full_name") or "Enterprise Bidder"
        st.markdown(f"""
        <section class="dashboard-hero">
            <div class="dashboard-hero-copy">
                <div class="dashboard-brandline"><span class="dashboard-brandmark">TF</span><b>TENDERFORGE</b></div>
                <div class="dashboard-welcome">Welcome back, {h(user_display_name)}</div>
                <h1>Intelligent Tender Decision Platform</h1>
                <p>Your centralized workspace for smarter tender evaluation and bidding decisions.</p>
            </div>
            <div class="dashboard-account">
                <div class="dashboard-date">{h(date.today().strftime('%A, %d %B %Y'))}</div>
                <div class="dashboard-account-card">
                    <div class="profile-user-avatar">{h(initials)}</div>
                    <div>
                    <strong>{h(active_company_name)}</strong>
                    <span>{h(user.get('email', ''))}</span>
                    </div>
                </div>
            </div>
        </section>
        """, unsafe_allow_html=True)

        pending_evaluations = max(stats["total_tenders"] - stats["analyzed_tenders"], 0)
        deadline_items = []
        high_risk_count = 0
        pending_requirements = 0
        missing_documents = 0
        for tender_item in tenders_with_analysis:
            if not tender_item.get("analysis_id"):
                continue
            analysis = get_analysis_for_tender(tender_item["id"], user_id)
            if not analysis:
                continue
            tender_record = get_tender_by_id(tender_item["id"], user_id)
            try:
                full_data = ensure_seven_sections(
                    analysis, tender_record.get("extracted_text", ""), profile, pdf_path=tender_record.get("filepath")
                )
            except Exception:
                full_data = ensure_seven_sections(analysis, "", profile)
            overview = full_data.get("tender_overview", {}) or {}
            deadline_value = overview.get("deadline") if isinstance(overview, dict) else None
            if deadline_value and str(deadline_value).upper() not in ("", "NOT FOUND", "NONE"):
                deadline_items.append((str(deadline_value), tender_item["filename"], tender_item["id"]))

            risk_data = full_data.get("risk_assessment", {}) or {}
            risks = risk_data.get("risks", []) if isinstance(risk_data, dict) else []
            if isinstance(risks, list):
                for risk in risks:
                    if isinstance(risk, dict) and str(risk.get("level") or risk.get("severity") or "").lower() in ("high", "critical"):
                        high_risk_count += 1

            matrix = full_data.get("compliance_matrix", []) or []
            if isinstance(matrix, list):
                for requirement in matrix:
                    if not isinstance(requirement, dict):
                        continue
                    status = normalize_status(requirement.get("status"))
                    if status in ("INFORMATION REQUIRED", "UNRESOLVED", "CONFIRMED GAP"):
                        pending_requirements += 1
                    if requirement.get("document_checklist_item") and status != "SATISFIED":
                        missing_documents += 1

        parsed_deadlines = []
        for raw_deadline, filename, tender_id in deadline_items:
            match = re.search(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:\d{1,2}\s+[A-Za-z]+\s+\d{4})|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b", raw_deadline)
            if not match:
                continue
            text = match.group(0).strip()
            parsed = None
            for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except Exception:
                    continue
            if parsed and parsed.date() - date.today() >= timedelta(0):
                parsed_deadlines.append((parsed.date(), (parsed.date() - date.today()).days, filename, tender_id, raw_deadline))
        parsed_deadlines.sort(key=lambda item: item[0])

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(f"""
            <div class="dashboard-kpi">
                <div class="dashboard-kpi-top"><span>Total Tenders</span><div style="background:#eff6ff;color:#2563eb;">📁</div></div>
                <div class="dashboard-kpi-value">{stats['total_tenders']}</div>
                <div class="dashboard-kpi-sub">Catalogued in your workspace</div>
            </div>
            """, unsafe_allow_html=True)

        with col2:
            coverage = int((stats['analyzed_tenders']/stats['total_tenders']*100) if stats['total_tenders'] > 0 else 0)
            st.markdown(f"""
            <div class="dashboard-kpi">
                <div class="dashboard-kpi-top"><span>Analysed Tenders</span><div style="background:#f0fdf4;color:#16805a;">✓</div></div>
                <div class="dashboard-kpi-value">{stats['analyzed_tenders']}</div>
                <div class="dashboard-kpi-sub">{coverage}% portfolio coverage</div>
            </div>
            """, unsafe_allow_html=True)

        with col3:
            st.markdown(f"""
            <div class="dashboard-kpi">
                <div class="dashboard-kpi-top"><span>Pending Evaluations</span><div style="background:#fff7ed;color:#c77810;">⏳</div></div>
                <div class="dashboard-kpi-value">{pending_evaluations}</div>
                <div class="dashboard-kpi-sub">Tenders awaiting analysis</div>
            </div>
            """, unsafe_allow_html=True)

        with col4:
            st.markdown(f"""
            <div class="dashboard-kpi">
                <div class="dashboard-kpi-top"><span>Upcoming Deadlines</span><div style="background:#eef4fb;color:#315f94;">◷</div></div>
                <div class="dashboard-kpi-value">{len(parsed_deadlines)}</div>
                <div class="dashboard-kpi-sub">Saved submission deadlines</div>
            </div>
            """, unsafe_allow_html=True)

        total_recommendations = stats["bid_count"] + stats["no_bid_count"] + stats["review_count"]
        overview_col, deadline_col = st.columns([1.45, 1], gap="large")
        with overview_col:
            st.markdown('<div class="dashboard-section-heading"><div><span>DECISION PORTFOLIO</span><h2>Bid Recommendation Overview</h2></div></div>', unsafe_allow_html=True)
            st.markdown('<div class="dashboard-recommendation-card">', unsafe_allow_html=True)
            for label, count, color in (
                ("Bid", stats["bid_count"], "#18734b"),
                ("No-Bid", stats["no_bid_count"], "#a43c42"),
                ("Further Review", stats["review_count"], "#97610b"),
            ):
                percent = int(count / total_recommendations * 100) if total_recommendations else 0
                st.markdown(f"""
                <div class="recommendation-row">
                    <div class="recommendation-row-head"><span><b style="color:{color};">{h(label)}</b><small>{count} tender(s)</small></span><strong>{percent}%</strong></div>
                    <div class="recommendation-track"><span style="width:{percent}%;background:{color};"></span></div>
                </div>
                """, unsafe_allow_html=True)
            if total_recommendations == 0:
                st.caption("No saved bid recommendations are available yet.")
            st.markdown('</div>', unsafe_allow_html=True)

        with deadline_col:
            st.markdown('<div class="dashboard-section-heading"><div><span>NEXT SUBMISSIONS</span><h2>Upcoming Deadlines</h2></div></div>', unsafe_allow_html=True)
            st.markdown('<div class="dashboard-deadline-card">', unsafe_allow_html=True)
            if parsed_deadlines:
                for deadline_date, remaining, filename, tender_id, raw_deadline in parsed_deadlines[:4]:
                    status_class = "deadline-urgent" if remaining <= 7 else ("deadline-soon" if remaining <= 30 else "deadline-normal")
                    status_text = "Due today" if remaining == 0 else f"{remaining} days remaining"
                    st.markdown(f"""
                    <div class="deadline-row">
                        <div class="deadline-date"><strong>{deadline_date.strftime('%d')}</strong><span>{deadline_date.strftime('%b')}</span></div>
                        <div class="deadline-copy"><strong>{h(filename)}</strong><span>{h(raw_deadline)}</span></div>
                        <span class="{status_class}">{status_text}</span>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.markdown('<div class="dashboard-empty">No upcoming deadlines found in saved tender analysis.</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="dashboard-section-heading dashboard-section-spaced"><div><span>PORTFOLIO ACTIVITY</span><h2>Recent Tender Activity</h2></div></div>', unsafe_allow_html=True)
        st.markdown('<div class="dashboard-table-card">', unsafe_allow_html=True)
        if tenders_with_analysis:
            st.markdown('<div class="dashboard-table-head"><span>Tender</span><span>Tender ID</span><span>Extraction</span><span>Recommendation</span><span>Action</span></div>', unsafe_allow_html=True)
            for tender_item in tenders_with_analysis[:6]:
                recommendation = tender_item.get("recommendation")
                if recommendation == "BID":
                    recommendation_html = "<span class='badge-bid'>BID</span>"
                elif recommendation == "NO-BID":
                    recommendation_html = "<span class='badge-nobid'>NO-BID</span>"
                elif recommendation and "REVIEW" in str(recommendation).upper():
                    recommendation_html = "<span class='badge-review'>FURTHER REVIEW</span>"
                else:
                    recommendation_html = "<span class='badge-pending'>NOT ANALYZED</span>"
                if tender_item["upload_status"] == "success":
                    status_html = "<span class='pill-satisfied'>✓ TEXT EXTRACTED</span>"
                elif tender_item["upload_status"] == "no_text":
                    status_html = "<span class='pill-unclear'>SCANNED / NO TEXT</span>"
                else:
                    status_html = "<span class='pill-notsatisfied'>EXTRACTION ERROR</span>"
                uploaded = str(tender_item["uploaded_at"])[:16].replace("T", " ")
                st.markdown(f"""
                <div class="dashboard-table-row">
                    <div class="dashboard-tender-name"><strong>{h(tender_item['filename'])}</strong><span>Uploaded {h(uploaded)}</span></div>
                    <div class="dashboard-tender-id">TF-{int(tender_item['id']):06d}</div>
                    <div>{status_html}</div><div>{recommendation_html}</div>
                    <div></div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("Open Tender", key=f"dash_btn_{tender_item['id']}"):
                    st.session_state["selected_tender_id"] = tender_item["id"]
                    st.session_state["current_page"] = "🎯 Tender Analysis"
                    st.rerun()
        else:
            st.markdown('<div class="dashboard-empty">No tender activity is available yet.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="dashboard-section-heading dashboard-section-spaced"><div><span>ATTENTION CENTER</span><h2>Risk & Evaluation Insights</h2></div></div>', unsafe_allow_html=True)
        insight_cards = [
            ("High-risk tenders", high_risk_count, "Saved high or critical risk findings", "⚠"),
            ("Pending requirements", pending_requirements, "Requirements needing information or review", "◎"),
            ("Missing documentation", missing_documents, "Checklist items not marked satisfied", "▣"),
            ("Further evaluation", stats["review_count"], "Analyses with review recommendation", "↻"),
        ]
        insight_cols = st.columns(4)
        for container, (label, value, sub, icon) in zip(insight_cols, insight_cards):
            with container:
                st.markdown(f"""
                <div class="insight-card">
                    <div class="insight-icon">{icon}</div>
                    <div><span>{h(label)}</span><strong>{value}</strong><small>{h(sub)}</small></div>
                </div>
                """, unsafe_allow_html=True)

    # ------------------------------------------------------------
    # PAGE 2: 🏢 Company Profile
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "🏢 Company Profile":
        profile = get_company_profile(user_id) or {}
        active_company_name = profile.get("company_name") or user.get("full_name") or "Enterprise Bidder"
        user_display_name = user.get("full_name") or "User"
        user_initials = "".join([part[0].upper() for part in user_display_name.split()[:2]]) or "TF"

        # 1. Clean Top Header Bar
        st.markdown(f"""
        <div class="profile-top-header">
            <div class="profile-top-actions">
                <button class="profile-notification-button" aria-label="Notifications">🔔</button>
                <div class="profile-user-badge">
                    <div class="profile-user-avatar">{h(user_initials)}</div>
                    <div class="profile-user-text">
                        <div class="profile-user-company">{h(active_company_name)}</div>
                        <div class="profile-user-email">⌄</div>
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 2. Company Profile Page Header
        company_initials = "".join(part[0].upper() for part in active_company_name.split()[:2]) or "CO"
        profile_documents = get_company_documents(user_id)
        tracked_header_fields = [
            profile.get("company_name"), profile.get("industry"), profile.get("contact_name"),
            profile.get("contact_email"), profile.get("contact_phone"), profile.get("contact_address"),
            profile.get("annual_turnover"), profile.get("financial_details"), profile.get("years_of_experience"),
            profile.get("past_projects"), profile.get("technical_capabilities"), profile.get("certifications"),
            bool(profile_documents),
        ]
        header_completion = int(sum(bool(str(value or "").strip()) for value in tracked_header_fields) / len(tracked_header_fields) * 100)
        business_type = {str(row.get("field_key")): str(row.get("field_value") or "") for row in profile.get("custom_fields") or []}.get("organization.business_type", "")
        st.markdown(f"""
        <section class="profile-business-header">
            <div class="profile-business-brand">
                <div class="profile-company-logo">{h(company_initials)}</div>
                <div>
                    <div class="profile-section-eyebrow">BUSINESS PROFILE</div>
                    <h1>{h(active_company_name)}</h1>
                    <p>{h(business_type or profile.get('industry') or 'Company intelligence and tender qualifications')}</p>
                </div>
            </div>
            <div class="profile-business-status">
                <div><span>Profile status</span><strong>{'In progress' if header_completion < 100 else 'Complete'}</strong></div>
                <div><span>Completion</span><strong>{header_completion}%</strong></div>
            </div>
        </section>
        """, unsafe_allow_html=True)

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
            render_tender_requested_fields(user_id, profile)

    # PAGE 3: 📁 My Tenders
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "📁 My Tenders":
        st.markdown("""
        <div class="saas-header">
            <div>
                <h1>Tender Document Repository</h1>
                <p>Upload official tender PDFs, inspect text extraction, and initiate compliance evaluations.</p>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.container():
            st.markdown("""
            <div class="saas-card">
                <div class="saas-card-header">📄 Upload Tender Document</div>
                <div style="font-size:0.88rem; color:#64748b; margin-bottom:12px;">
                    Select an official procurement notice or RFP in PDF format. Text will be automatically cataloged and extracted for qualification review.
                </div>
            </div>
            """, unsafe_allow_html=True)

            uploaded_file = st.file_uploader(
                "Select Tender PDF file",
                type=["pdf"],
                label_visibility="collapsed",
                help="Text-based PDFs yield the highest quality extraction.",
                key=f"uploader_{user_id}",
            )

            if uploaded_file is not None:
                file_key = f"uploaded_{user_id}_{uploaded_file.name}_{uploaded_file.size}"
                if st.session_state.get("last_uploaded_key") != file_key:
                    with st.spinner("Extracting text and cataloging document..."):
                        file_path = save_uploaded_file(uploaded_file, UPLOAD_DIR)
                        result = extract_text_from_pdf(file_path)

                        if result["error"]:
                            st.error("This PDF could not be processed. Please try a text based PDF or contact support.")
                            tender_id = save_tender(
                                filename=uploaded_file.name,
                                filepath=file_path,
                                extracted_text="",
                                page_count=result["page_count"],
                                upload_status="error",
                                user_id=user_id,
                            )
                        else:
                            if result["warning"]:
                                st.warning(f"⚠️ {result['warning']}")
                            status_str = "success" if result["text"] else "no_text"
                            tender_id = save_tender(
                                filename=uploaded_file.name,
                                filepath=file_path,
                                extracted_text=result["text"],
                                page_count=result["page_count"],
                                upload_status=status_str,
                                user_id=user_id,
                            )
                            st.success(f"✅ Successfully extracted {result['page_count']} page(s) and saved tender!")
                        
                        st.session_state["last_uploaded_key"] = file_key
                        st.session_state["selected_tender_id"] = tender_id
                        st.rerun()

        st.markdown("<hr style='margin:25px 0; border-color:#e2e8f0;'>", unsafe_allow_html=True)

        st.markdown("### Uploaded Tender Documents")
        all_tenders = get_tenders_with_analysis(user_id)

        if all_tenders:
            for t in all_tenders:
                with st.container():
                    col1, col2, col3, col4 = st.columns([3.5, 2, 2, 2])
                    with col1:
                        st.markdown(f"**📄 {h(t['filename'])}**")
                        st.markdown(f"<span style='font-size:0.8rem; color:#64748b;'>Uploaded: {t['uploaded_at'][:16]} · {t['page_count']} Pages</span>", unsafe_allow_html=True)
                    
                    with col2:
                        if t["upload_status"] == "success":
                            st.markdown("<span class='pill-satisfied'>✓ Text Extracted</span>", unsafe_allow_html=True)
                        elif t["upload_status"] == "no_text":
                            st.markdown("<span class='pill-unclear'>Scanned / No Text</span>", unsafe_allow_html=True)
                        else:
                            st.markdown("<span class='pill-notsatisfied'>Extraction Error</span>", unsafe_allow_html=True)
                    
                    with col3:
                        rec = t.get("recommendation")
                        if rec == "BID":
                            st.markdown("<span class='badge-bid'>BID</span>", unsafe_allow_html=True)
                        elif rec == "NO-BID":
                            st.markdown("<span class='badge-nobid'>NO-BID</span>", unsafe_allow_html=True)
                        elif rec == "REVIEW REQUIRED":
                            st.markdown("<span class='badge-review'>REVIEW REQ.</span>", unsafe_allow_html=True)
                        else:
                            st.markdown("<span class='badge-pending'>NOT ANALYZED</span>", unsafe_allow_html=True)

                    with col4:
                        has_analysis = bool(t.get("recommendation"))
                        action_label = "📊 View Analysis" if has_analysis else "⚡ Analyze Tender"
                        btn_type = "primary" if has_analysis else "secondary"
                        if st.button(action_label, key=f"tbl_btn_{t['id']}", use_container_width=True, type=btn_type):
                            st.session_state["selected_tender_id"] = t["id"]
                            st.session_state["current_page"] = "🎯 Tender Analysis"
                            st.rerun()

                    tender_obj = get_tender_by_id(t["id"], user_id)
                    if tender_obj and tender_obj.get("extracted_text"):
                        with st.expander(f"Inspect Extracted Text ({len(tender_obj['extracted_text'])} chars)"):
                            st.text_area(
                                "Extracted Text Preview",
                                value=tender_obj["extracted_text"][:3000] + ("\n... [truncated for preview]" if len(tender_obj["extracted_text"]) > 3000 else ""),
                                height=160,
                                disabled=True,
                                key=f"txt_prev_{t['id']}",
                            )
                    st.markdown("<hr style='margin:10px 0; border-color:#f1f5f9;'>", unsafe_allow_html=True)
        else:
            st.info("No tender documents uploaded in your workspace yet. Upload your first PDF tender notice above.")

    # ------------------------------------------------------------
    # PAGE 4: 🎯 Tender Analysis
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "🎯 Tender Analysis":
        all_tenders = get_all_tenders(user_id)

        if not all_tenders:
            st.markdown("""
            <div class="saas-header">
                <div>
                    <h1>Tender Analysis Results</h1>
                    <p>Evidence-grounded qualification evaluation, compliance matrix, and bid decisions.</p>
                </div>
            </div>
            """, unsafe_allow_html=True)
            st.warning("⚠️ No tender documents have been uploaded in your workspace yet. Please upload a tender in **My Tenders** first.")
            if st.button("Go to My Tenders"):
                st.session_state["current_page"] = "📁 My Tenders"
                st.rerun()
        else:
            tender_options = {t["id"]: f"{t['filename']} ({t['page_count']} pgs, {t['uploaded_at'][:10]})" for t in all_tenders}
            default_index = 0
            if st.session_state.get("selected_tender_id") in tender_options:
                default_index = list(tender_options.keys()).index(st.session_state["selected_tender_id"])

            nav_c1, nav_c2 = st.columns([1.3, 4.7])
            with nav_c1:
                if st.button("← Back to My Tenders", use_container_width=True):
                    st.session_state["current_page"] = "📁 My Tenders"
                    st.rerun()
            with nav_c2:
                selected_id = st.selectbox(
                    "Active Tender Selection",
                    options=list(tender_options.keys()),
                    format_func=lambda tid: f"📄 {tender_options[tid]}",
                    index=default_index,
                    key=f"analysis_tender_select_{user_id}",
                    on_change=_analysis_tender_selection_changed,
                    args=(user_id,),
                    label_visibility="collapsed",
                )
                st.session_state["selected_tender_id"] = selected_id

            tender = get_tender_by_id(selected_id, user_id)
            if not tender:
                st.error("❌ Access Denied: You do not have permission to view this tender.")
                st.stop()

            profile = get_company_profile(user_id) or {}
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

            # Header Bar
            st.markdown(f"""
            <div class="saas-header" style="margin-top:0.4rem; margin-bottom:1rem;">
                <div>
                    <h1 style="font-size:1.55rem; margin-bottom:4px;">Tender Analysis: {h(tender['filename'])}</h1>
                    <p style="font-size:0.88rem;">Uploaded on {tender['uploaded_at'][:16]} · {tender['page_count']} Pages · </p>
                </div>
            </div>
            """, unsafe_allow_html=True)

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
                        st.caption(f"Showing Latest Version {versions[0].get('version', 1)} ({len(versions)} versions archived).")

            # Duplicate AI processing prevention check
            profile_changed = (current_profile_hash != (saved_analysis or {}).get("profile_hash"))
            if saved_analysis and not profile_changed:
                # Duplicate AI processing was skipped when profile and requirements have not changed
                pass
            analysis_res = saved_analysis or {}
            if not analysis_res.get("success") and not saved_analysis:
                # The analysis could not be completed notification
                pass

            # Analysis Action / Freshness Bar
            bar_c1, bar_c2 = st.columns([3.8, 1.2])
            with bar_c1:
                if not saved_analysis:
                    st.info("⚡ This tender has not yet been evaluated. Click 'Run Analysis' to generate the full qualification review.")
                else:
                    state_text, ok = analysis_state(saved_analysis, current_profile_hash,
                                                    get_custom_requirements(selected_id, user_id))
                    line = f"**Saved analysis #{saved_analysis['id']} generated {str(saved_analysis.get('analyzed_at', ''))[:16]}** | **{state_text}**"
                    (st.success if ok else st.warning)(line)
                    company_note, company_ok = analysis_company_check(saved_analysis, profile)
                    if not company_ok:
                        st.caption(company_note)
                    structured_saved = saved_analysis.get("structured_tender_data") or {}
                    company_snapshot = structured_saved.get("company_snapshot") or {}
                    evidence_snapshot = structured_saved.get("company_evidence_snapshot") or {}
                    with st.expander("Company profile used"):
                        st.markdown(f"**Company:** {h(company_snapshot.get('company_name') or '(not recorded)')}")
                        st.caption(f"Saved profile version: {company_snapshot.get('profile_updated_at') or 'date not recorded'} | "
                                   f"{len(evidence_snapshot.get('facts') or [])} evidence item(s) available, "
                                   f"{len(evidence_snapshot.get('verified_fact_ids') or [])} marked verified at analysis time.")
                        if st.button("View Company Profile", key=f"analysis_profile_{selected_id}"):
                            st.session_state["current_page"] = "ðŸ¢ Company Profile"
                            st.rerun()
                last_fail = st.session_state.get(f"last_failed_run_{selected_id}")
                if last_fail:
                    failed_stage = str(last_fail.get("stage") or "analysis").lower()
                    previous_result = (f"Previous saved analysis #{saved_analysis['id']} is preserved and shown."
                                        if saved_analysis else "No earlier saved analysis was changed.")
                    st.error(f"The analysis failed at the **{h(failed_stage)}** step. {previous_result}")
                    with st.expander("Failed analysis details"):
                        st.caption(f"Stage: {last_fail.get('stage')} | Time: {last_fail.get('at')}")
                        st.code(str(last_fail.get("error") or "No error details recorded")[:1500])

            with bar_c2:
                analyze_label = "Re-Run Analysis ⚡" if saved_analysis else "Run Analysis ⚡"
                run_clicked = st.button(analyze_label, use_container_width=True, type="primary")
            if run_clicked:
                if run_and_save_analysis(tender, profile, user_id, saved_analysis):
                    st.success("Analysis started. Progress will appear here while the workspace stays available.")
                    st.rerun()

            analysis_flash = st.session_state.pop(f"analysis_flash_{selected_id}", None)
            if analysis_flash:
                (st.info if "previous complete analysis" in analysis_flash.lower() else st.success)(analysis_flash)
            _render_analysis_progress(selected_id)

            # If Analysis Exists: Render the Dedicated Sections
            if saved_analysis:
                pdf_file_path = tender.get("filepath")
                full_data = ensure_seven_sections(saved_analysis, tender.get("extracted_text", ""), profile, pdf_path=pdf_file_path)

                overview = full_data.get("tender_overview") or {}
                eligibility = full_data.get("eligibility_analysis") or {}
                matrix = saved_analysis.get("compliance_matrix") or full_data.get("compliance_matrix") or []
                # One unique matrix is the source for all status counts, filters, and exports.
                unique_matrix = {}
                for row in matrix:
                    key = str(row.get("requirement_id") or "").strip() or (
                        str(row.get("requirement") or "").strip().casefold(),
                        str(row.get("source_quotation") or "").strip().casefold())
                    unique_matrix.setdefault(key, row)
                matrix = list(unique_matrix.values())
                # Statuses are displayed exactly as saved (no display-time conversion).
                structured_saved = saved_analysis.get("structured_tender_data") or {}
                provenance = structured_saved.get("analysis_provenance") or {}
                technical = full_data.get("technical_evaluation") or {}
                financial = full_data.get("financial_analysis") or {}
                risk_data = full_data.get("risk_assessment") or {}
                bid_rec = full_data.get("bid_recommendation") or {}
                llm_ans = full_data.get("llm_analysis") or {}

                rec = (saved_analysis.get("recommendation") or bid_rec.get("decision") or "REVIEW REQUIRED").upper()
                rec_card_class = "rec-card-review"
                rec_badge_class = "badge-review"
                if rec == "BID":
                    rec_card_class = "rec-card-bid"
                    rec_badge_class = "badge-bid"
                elif rec == "NO-BID":
                    rec_card_class = "rec-card-nobid"
                    rec_badge_class = "badge-nobid"

                # Decision summary from the saved analysis.
                ref_num = overview.get("reference_number")
                authority = overview.get("issuing_organization", NOT_FOUND)
                tender_title = overview.get("title", tender["filename"])
                deadline = deadline_display(overview.get("deadline", NOT_FOUND))
                recommendation_label = {"BID": "Potential Bid", "NO-BID": "Potential No-Bid"}.get(rec, "Review Required")
                reasons = bid_rec.get("reasons") or []
                short_reasons = reasons[:2] or [saved_analysis.get("executive_summary") or "Review the requirement findings and evidence before deciding."]
                reason_text = " ".join(str(reason) for reason in short_reasons if reason)
                metrics_data = full_data.get("metrics") or {}
                quality = structured_saved.get("analysis_quality") or {}
                coverage_incomplete = (quality.get("status") not in (None, "complete") or
                                       bool(quality.get("coverage_warnings")) or
                                       bool(metrics_data.get("is_incomplete")) or
                                       bool(provenance.get("fallback_used")))
                st.markdown(f"""
                <section class="analysis-decision {rec_card_class}">
                    <div class="analysis-decision-heading">
                        <div><div class="eyebrow">Bid assessment</div><h2>{h(tender_title)}</h2></div>
                        <span class="{rec_badge_class}">{h(recommendation_label)}</span>
                    </div>
                    <div class="analysis-decision-meta">
                        <span><b>Reference</b>{h(ref_num or NOT_FOUND)}</span>
                        <span><b>Authority</b>{h(authority)}</span>
                        <span><b>Deadline</b>{h(deadline)}</span>
                        <span><b>Estimated value</b>{h(overview.get('estimated_value', NOT_FOUND))}</span>
                        <span><b>EMD / fee</b>{h((financial.get('emd') or {}).get('value') or NOT_FOUND)} / {h((financial.get('tender_fee') or {}).get('value') or NOT_FOUND)}</span>
                    </div>
                    <p class="analysis-decision-reasons">{h(reason_text[:520])}</p>
                </section>
                """, unsafe_allow_html=True)
                if coverage_incomplete:
                    st.warning("Some tender clauses or tables could not be fully evaluated. Review the cited pages before making a bid decision.")

                # All counts use this same deduplicated compliance matrix.
                total_reqs = len(matrix)
                norm_statuses = [normalize_status(m.get("status")) for m in matrix]
                sat_count = norm_statuses.count("SATISFIED")
                unsat_count = norm_statuses.count("CONFIRMED GAP")
                inforeq_count = norm_statuses.count("INFORMATION REQUIRED")
                unclear_count = norm_statuses.count("UNRESOLVED")
                na_count = norm_statuses.count("NOT APPLICABLE")
                sm1, sm2, sm3, sm4, sm5, sm6 = st.columns(6)
                sm1.metric("Requirements", total_reqs)
                sm2.metric("Satisfied", sat_count)
                sm3.metric("Confirmed gaps", unsat_count)
                sm4.metric("Information needed", inforeq_count)
                sm5.metric("Unresolved", unclear_count)
                sm6.metric("Not applicable", na_count)

                # Dedicated 8-Section Tabs
                tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
                    "Overview", "Eligibility & Qualification", "Compliance Matrix", "Technical",
                    "Financial", "Risks & Deadlines", "Bid Recommendation", "Section 8: Deadlines & Submission Milestones"
                ])

                # ----------------------------------------------------
                # TAB 1: Tender Overview
                # ----------------------------------------------------
                with tab1:
                    st.markdown("##### Section 1: Tender Overview & Scope")
                    st.caption("Verified procurement parameters and statement of work extracted directly from the tender document.")

                    c_ov1, c_ov2, c_ov3 = st.columns(3)
                    with c_ov1:
                        st.markdown(f"""
                        <div class="grid-meta-card">
                            <div class="grid-meta-label">Project / Solicitation Title</div>
                            <div class="grid-meta-val">{h(overview.get('title', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with c_ov2:
                        st.markdown(f"""
                        <div class="grid-meta-card">
                            <div class="grid-meta-label">Tender Reference Number</div>
                            <div class="grid-meta-val">{h(overview.get('reference_number', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with c_ov3:
                        st.markdown(f"""
                        <div class="grid-meta-card">
                            <div class="grid-meta-label">Issuing Authority / Client</div>
                            <div class="grid-meta-val">{h(overview.get('issuing_organization', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)

                    c_ov4, c_ov5 = st.columns(2)
                    with c_ov4:
                        st.markdown(f"""
                        <div class="grid-meta-card">
                            <div class="grid-meta-label">Estimated Value / Budget</div>
                            <div class="grid-meta-val">{h(overview.get('estimated_value', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    with c_ov5:
                        st.markdown(f"""
                        <div class="grid-meta-card">
                            <div class="grid-meta-label">Submission Deadline</div>
                            <div class="grid-meta-val">{h(overview.get('deadline', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                    ov_sources = (saved_analysis.get("structured_tender_data") or {}).get("tender_overview_sources") or {}
                    if ov_sources:
                        st.caption("Sources in the tender: " + " · ".join(
                            f"{label}: {'page ' + str(ov_sources.get(key)) if ov_sources.get(key) else 'page not located'}"
                            for key, label in (("title", "title"), ("reference_number", "reference"),
                                               ("issuing_organization", "authority"), ("estimated_value", "value"),
                                               ("deadline", "deadline"))))
                    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
                    st.markdown("**Scope of Work & Objective:**")
                    st.markdown(f"""
                    <div class="evidence-box" style="font-family:inherit; font-size:0.9rem; line-height:1.6; border-left-color:#0f172a;">
                        {h(overview.get('scope_of_work', NOT_FOUND))}
                    </div>
                    """, unsafe_allow_html=True)

                # ----------------------------------------------------
                # TAB 2: Eligibility Analysis
                # ----------------------------------------------------
                with tab2:
                    st.markdown("##### Section 2: Eligibility & Qualification Analysis")
                    st.caption("Detailed evaluation of the four fundamental qualification pillars against authenticated company disclosures.")

                    pillars = [
                        ("Experience Track Record", eligibility.get("experience", {})),
                        ("Annual Turnover Threshold", eligibility.get("turnover", {})),
                        ("Mandatory Certifications", eligibility.get("certifications", {})),
                        ("Registration & Legal Standing", eligibility.get("registration_requirements", {}))
                    ]

                    st.caption("Each pillar summarises the compliance-matrix requirements it covers; it is not a separate evaluation.")
                    for p_title, p_data in pillars:
                        p_badge = status_pill(p_data.get("status") or "UNRESOLVED")

                        st.markdown(f"""
                        <div class="pillar-card">
                            <div class="pillar-header">
                                <div class="pillar-title">{h(p_title)}: {h(p_data.get('requirement', 'Requirement'))}</div>
                                {p_badge}
                            </div>
                            <div style="font-size:0.88rem; color:#475569; margin-bottom:10px;"><b>Finding:</b> {h(p_data.get('explanation', ''))}</div>
                            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); gap: 12px;">
                                <div>
                                    <span style="font-size:0.72rem; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em;">Tender Document Evidence</span>
                                    <div class="evidence-box">{h(p_data.get('tender_evidence', NOT_FOUND))}</div>
                                </div>
                                <div>
                                    <span style="font-size:0.72rem; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em;">Company Profile Evidence</span>
                                    <div class="evidence-box-company">{h(p_data.get('company_evidence', NOT_FOUND))}</div>
                                </div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                # ----------------------------------------------------
                # TAB 3: Compliance Matrix
                # ----------------------------------------------------
                with tab3:
                    st.markdown("##### Compliance Matrix")
                    st.caption("A unique requirement list links each tender clause to company evidence and a practical next action.")
                    filter_col, category_col, search_col = st.columns([1.4, 1.4, 2.2])
                    statuses = ["All statuses"] + sorted({normalize_status(row.get("status")) for row in matrix})
                    categories = ["All categories"] + sorted({str(row.get("category") or "Unclassified") for row in matrix})
                    selected_status = filter_col.selectbox("Status", statuses, key="matrix_status_filter")
                    selected_category = category_col.selectbox("Category", categories, key="matrix_category_filter")
                    search_term = search_col.text_input("Search requirements", placeholder="Requirement or evidence", key="matrix_search")
                    filtered_matrix = matrix
                    if selected_status != "All statuses":
                        filtered_matrix = [row for row in filtered_matrix if normalize_status(row.get("status")) == selected_status]
                    if selected_category != "All categories":
                        filtered_matrix = [row for row in filtered_matrix if (row.get("category") or "Unclassified") == selected_category]
                    if search_term:
                        term = search_term.casefold()
                        filtered_matrix = [row for row in filtered_matrix if term in " ".join(str(row.get(k) or "") for k in
                            ("requirement", "category", "explanation", "tender_evidence", "company_evidence")).casefold()]
                    table_rows = []
                    for row in filtered_matrix:
                        refs = row.get("tender_evidence_refs") or []
                        ref_text = ", ".join(f"p. {ref.get('page_number')} ({ref.get('chunk_id')})" for ref in refs)
                        if not ref_text and row.get("source_page"):
                            ref_text = f"p. {row['source_page']} (citation not validated)"
                        next_action = (row.get("evidence_confirmation_needed") or
                                       ("Review the confirmed tender gap." if normalize_status(row.get("status")) == "CONFIRMED GAP" else
                                        "No further action recorded." if normalize_status(row.get("status")) in ("SATISFIED", "NOT APPLICABLE") else
                                        "Provide or verify the missing company evidence."))
                        table_rows.append({"Requirement": row.get("requirement") or "Requirement",
                                           "Tender Reference": ref_text or "Source reference unavailable",
                                           "Status": normalize_status(row.get("status")),
                                           "Evidence": str(row.get("company_evidence") or NOT_FOUND)[:180],
                                           "Next Action": str(next_action)[:180]})
                    st.caption(f"Showing {len(filtered_matrix)} of {len(matrix)} unique requirements")
                    if table_rows:
                        st.dataframe(table_rows, hide_index=True, use_container_width=True)
                        for item in filtered_matrix:
                            title = str(item.get("requirement") or "Requirement")
                            with st.expander(f"{title} ? {normalize_status(item.get('status'))}"):
                                st.markdown(f"**Tender clause:** {h(item.get('source_quotation') or item.get('tender_evidence') or NOT_FOUND)}")
                                st.markdown(f"**Explanation:** {h(item.get('explanation') or '')}")
                                refs = item.get("tender_evidence_refs") or []
                                st.caption("Citation: " + (", ".join(f"page {r.get('page_number')} ? {r.get('chunk_id')}" for r in refs)
                                                                 if refs else f"page {item.get('source_page') or 'not located'} ? no validated retrieved chunk"))
                                if item.get("required_document"):
                                    st.markdown(f"**Document named by tender:** {h(item['required_document'])}")
                                st.markdown(f"**Company evidence:** {h(item.get('company_evidence') or NOT_FOUND)}")
                                for provenance_item in item.get("company_evidence_provenance") or []:
                                    st.caption(f"{provenance_item.get('field_label') or provenance_item.get('field_key')} ? "
                                               f"{evidence_state_label(provenance_item.get('verification_status'), provenance_item.get('review_method'))}"
                                               + (f" ? {provenance_item.get('filename')} v{provenance_item.get('document_version')} page {provenance_item.get('source_page') or 'not located'}"
                                                  if provenance_item.get('filename') else ""))
                                if item.get("ai_flagged_gap"):
                                    st.caption("An AI flag is unconfirmed and is not treated as a deterministic gap.")
                    else:
                        st.info("No requirements match these filters.")

                # TAB 4: Technical Evaluation
                # ----------------------------------------------------
                with tab4:
                    st.markdown("##### Section 4: Technical Evaluation")
                    st.caption("Detailed alignment across technical capabilities, infrastructure tooling, domain expertise, and milestone deliverables.")

                    tech_caps = technical.get("capabilities") or []
                    tech_eq = technical.get("equipment") or []
                    tech_exp = technical.get("expertise") or []
                    tech_deliv = technical.get("deliverables") or []

                    st.markdown("###### 1. Technical Capabilities")
                    for tc in tech_caps:
                        t_pill = status_pill(tc.get("status") or "UNRESOLVED")
                        st.markdown(f"""
                        <div class="tech-spec-card">
                            <div style='display:flex; justify-content:space-between; align-items:center;'><b>{h(tc.get('requirement', 'Technical Capability'))}</b> {t_pill}</div>
                            <div style='font-size:0.85rem; color:#475569; margin:4px 0;'><b>Tender:</b> {h(tc.get('tender_evidence', NOT_FOUND))}</div>
                            <div style='font-size:0.85rem; color:#475569;'><b>Company Match:</b> {h(tc.get('company_evidence', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                    st.markdown("<hr style='margin:16px 0; border-color:#e2e8f0;'>", unsafe_allow_html=True)
                    t_c1, t_c2 = st.columns(2)
                    with t_c1:
                        st.markdown("###### 2. Equipment, Tooling & Infrastructure")
                        for eq in tech_eq:
                            st.markdown(f"**{h(eq.get('requirement', 'Equipment'))}**")
                            st.markdown(f"<span style='font-size:0.85rem; color:#475569;'>Tender: {h(eq.get('tender_evidence', NOT_FOUND))}</span>", unsafe_allow_html=True)
                            st.markdown(f"<div class='evidence-box-company' style='margin-top:4px;'>Profile: {h(eq.get('company_evidence', NOT_FOUND))}</div>", unsafe_allow_html=True)

                        st.markdown("<br>", unsafe_allow_html=True)
                        st.markdown("###### 3. Domain Expertise & Staffing")
                        for ex in tech_exp:
                            st.markdown(f"**{h(ex.get('requirement', 'Expertise'))}**")
                            st.markdown(f"<span style='font-size:0.85rem; color:#475569;'>Tender: {h(ex.get('tender_evidence', NOT_FOUND))}</span>", unsafe_allow_html=True)
                            st.markdown(f"<div class='evidence-box-company' style='margin-top:4px;'>Profile: {h(ex.get('company_evidence', NOT_FOUND))}</div>", unsafe_allow_html=True)

                    with t_c2:
                        st.markdown("###### 4. Contract Deliverables & Milestones")
                        for dl in tech_deliv:
                            st.markdown(f"""
                            <div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px 12px; margin-bottom:8px;'>
                                <div style='font-weight:700; color:#0f172a;'>📦 {h(dl.get('deliverable', 'Deliverable'))}</div>
                                <div style='font-size:0.85rem; color:#475569; margin-top:4px;'>{h(dl.get('tender_evidence', NOT_FOUND))}</div>
                                <div style='font-size:0.75rem; color:#64748b; margin-top:2px;'>{h(dl.get('details', ''))}</div>
                            </div>
                            """, unsafe_allow_html=True)

                # ----------------------------------------------------
                # TAB 5: Financial Analysis
                # ----------------------------------------------------
                with tab5:
                    st.markdown("##### Section 5: Financial Analysis & Guarantees")
                    st.caption("Summary of bid bonds, documentation fees, performance security, and commercial payment terms.")

                    emd_data = financial.get("emd") or {}
                    fee_data = financial.get("tender_fee") or {}
                    tov_data = financial.get("turnover_required") or {}
                    pbg_data = financial.get("pbg") or {}
                    pay_data = financial.get("payment_terms") or {}

                    f_col1, f_col2 = st.columns(2)
                    with f_col1:
                        st.markdown(f"""
                        <div class="grid-meta-card" style="margin-bottom:12px;">
                            <div class="grid-meta-label">1. Earnest Money Deposit (EMD) / Bid Security</div>
                            <div class="grid-meta-val">{h(emd_data.get('value', NOT_FOUND))}</div>
                            <div style="font-size:0.82rem; color:#64748b; margin-top:4px;"><b>Tender Clause:</b> {h(emd_data.get('tender_evidence', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                        <div class="grid-meta-card" style="margin-bottom:12px;">
                            <div class="grid-meta-label">2. Tender Document & Processing Fee</div>
                            <div class="grid-meta-val">{h(fee_data.get('value', NOT_FOUND))}</div>
                            <div style="font-size:0.82rem; color:#64748b; margin-top:4px;"><b>Tender Clause:</b> {h(fee_data.get('tender_evidence', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                        <div class="grid-meta-card">
                            <div class="grid-meta-label">3. Mandatory Turnover Threshold</div>
                            <div class="grid-meta-val">{h(tov_data.get('value', NOT_FOUND))}</div>
                            <div style="font-size:0.82rem; color:#64748b; margin-top:4px;"><b>Company Audited Turnover:</b> {h(tov_data.get('company_turnover', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                    with f_col2:
                        st.markdown(f"""
                        <div class="grid-meta-card" style="margin-bottom:12px;">
                            <div class="grid-meta-label">4. Performance Bank Guarantee (PBG)</div>
                            <div class="grid-meta-val">{h(pbg_data.get('value', NOT_FOUND))}</div>
                            <div style="font-size:0.82rem; color:#64748b; margin-top:4px;"><b>Tender Clause:</b> {h(pbg_data.get('tender_evidence', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                        st.markdown(f"""
                        <div class="grid-meta-card" style="margin-bottom:12px;">
                            <div class="grid-meta-label">5. Commercial Payment Terms & Milestones</div>
                            <div class="grid-meta-val">{h(pay_data.get('terms', NOT_FOUND))}</div>
                            <div style="font-size:0.82rem; color:#64748b; margin-top:4px;"><b>Tender Clause:</b> {h(pay_data.get('tender_evidence', NOT_FOUND))}</div>
                        </div>
                        """, unsafe_allow_html=True)

                # ----------------------------------------------------
                # TAB 6: Risk Assessment
                # ----------------------------------------------------
                with tab6:
                    st.markdown("##### Section 6: Risk Assessment & Red Flags")
                    st.caption("Systematic review of contractual liabilities, critical milestone windows, required submission documentation, and ambiguities.")

                    r_c1, r_c2 = st.columns(2)
                    with r_c1:
                        st.markdown("###### 1. Liquidated Damages & Delay Penalties")
                        pen_list = risk_data.get("penalties") or []
                        if pen_list:
                            for p in pen_list:
                                p_sev = (p.get("severity") or "MEDIUM").upper()
                                p_col = "#dc2626" if p_sev == "HIGH" else "#d97706"
                                st.markdown(f"""
                                <div style='background:#fef2f2; border:1px solid #fecaca; border-radius:6px; padding:10px 12px; margin-bottom:10px;'>
                                    <div style='color:{p_col}; font-weight:700;'>[{p_sev} SEVERITY] {h(p.get('clause', 'Penalty'))}</div>
                                    <div style='font-size:0.85rem; color:#334155; margin:4px 0;'><b>Clause:</b> {h(p.get('tender_evidence', NOT_FOUND))}</div>
                                    <div style='font-size:0.82rem; color:#475569;'><b>Mitigation:</b> {h(p.get('mitigation', ''))}</div>
                                </div>
                                """, unsafe_allow_html=True)
                        else:
                            st.info("No explicit penalty clauses identified.")

                        st.markdown("<br>", unsafe_allow_html=True)
                        st.markdown("###### 2. Critical Milestones & Schedule Risks")
                        dl_list = risk_data.get("deadlines") or []
                        if dl_list:
                            for d in dl_list:
                                st.markdown(f"""**⏰ {h(d.get('event', 'Event'))}**: `{deadline_display(d.get('date', NOT_FOUND))}`  \n<span style='font-size:0.82rem; color:#64748b;'>{h(d.get('tender_evidence', ''))}</span>""", unsafe_allow_html=True)
                                st.markdown("<hr style='margin:6px 0; border-color:#f1f5f9;'>", unsafe_allow_html=True)
                        else:
                            st.info("No milestone deadlines extracted.")

                    with r_c2:
                        st.markdown("###### 3. Mandatory Documents Checklist & Readiness")
                        doc_list = risk_data.get("missing_documents") or []
                        if doc_list:
                            for doc in doc_list:
                                st.markdown(f"- 📄 {h(doc)}")
                        else:
                            st.info("No document checklists identified.")

                        st.markdown("<br>", unsafe_allow_html=True)
                        st.markdown("###### 4. Unclear Obligations & Clarification Queries")
                        unc_list = risk_data.get("unclear_obligations") or []
                        if unc_list:
                            for unc in unc_list:
                                st.markdown(f"- ❓ **Pre-bid Query Candidate:** {h(unc)}")
                        else:
                            st.info("No ambiguous obligations identified.")

                # ----------------------------------------------------
                # TAB 7: Bid Recommendation
                # ----------------------------------------------------
                with tab7:
                    st.markdown("##### Section 7: Bid Recommendation & Action Plan")
                    st.caption("Authoritative go/no-go determination grounded in evidence, with identified blockers and concrete next actions.")

                    st.markdown("**Reasons and blockers from the saved recommendation**")

                    # Blockers (if any)
                    blockers = bid_rec.get("blockers") or []
                    if blockers:
                        st.markdown("""
                        <div class="blocker-alert">
                            <div style="font-weight:700; color:#b91c1c; font-size:0.95rem; margin-bottom:6px;">
                                🛑 Confirmed Disqualifying Blockers
                            </div>
                            <div style="font-size:0.85rem; color:#7f1d1d; margin-bottom:8px;">
                                The following critical mandatory requirements are confirmed unmet based on available evidence:
                            </div>
                        """, unsafe_allow_html=True)
                        for b in blockers:
                            st.markdown(f"- ❌ **{h(b)}**")
                        st.markdown("</div><br>", unsafe_allow_html=True)

                    # Decision Reasons
                    st.markdown("###### Key Decision Reasons")
                    reasons = bid_rec.get("reasons") or [saved_analysis.get("executive_summary")]
                    for r in reasons:
                        st.markdown(f"- 📌 {h(r)}")

                    assessment = bid_rec.get("assessment") or {}
                    if assessment:
                        st.markdown("###### Assessment by type")
                        st.caption("Only confirmed mandatory failures are disqualifying. Missing information, unresolved items, "
                                   "technical suitability, financial obligations and business-fit concerns are reported separately.")
                        for key, title in (("mandatory_failures", "Confirmed mandatory failures (disqualifying)"),
                                           ("missing_information", "Missing information (not failures)"),
                                           ("unresolved", "Unresolved / not comparable"),
                                           ("technical_suitability", "Technical suitability"),
                                           ("financial_requirements", "Financial requirements"),
                                           ("financial_obligations", "Financial obligations stated in the tender"),
                                           ("business_fit_concerns", "Business-fit concerns (not disqualifying)")):
                            entries = assessment.get(key) or []
                            with st.expander(f"{title} — {len(entries)}", expanded=key == "mandatory_failures" and bool(entries)):
                                for e in entries:
                                    st.markdown(f"- {h(e)}")
                                if not entries:
                                    st.caption("None.")

                    st.markdown("<hr style='margin:16px 0; border-color:#e2e8f0;'>", unsafe_allow_html=True)

                    # Next Actions
                    st.markdown("###### Recommended Next Actions")
                    actions = bid_rec.get("next_actions") or []
                    if actions:
                        for idx, act in enumerate(actions, 1):
                            st.markdown(f"**{idx}.** {h(act)}")
                    else:
                        st.info("No additional next actions specified.")

                # ----------------------------------------------------
                # Section 8: Deadlines, Milestones & Diagnostics
                with tab8:
                    st.markdown("##### Section 8: Deadlines & Submission Milestones")
                    st.caption("Critical submission dates, technical extraction diagnostics, and requirement coverage.")

                    # Milestones & Deadlines
                    dl_list = risk_data.get("deadlines") or []
                    if dl_list:
                        for d in dl_list:
                            st.markdown(f"**⏰ {h(d.get('event', 'Event'))}**: `{deadline_display(d.get('date', NOT_FOUND))}`  \n<span style='font-size:0.82rem; color:#64748b;'>{h(d.get('tender_evidence', ''))}</span>", unsafe_allow_html=True)
                            st.markdown("<hr style='margin:6px 0; border-color:#f1f5f9;'>", unsafe_allow_html=True)
                    else:
                        st.info("No milestone deadlines extracted.")

                    with st.expander("Analysis Diagnostics & Validation", expanded=False):
                        st.markdown(f"**Analysis status:** {h(quality.get('status') or 'status not recorded')}")
                        if provenance.get("fallback_used"):
                            st.markdown(f"**Fallback used:** {h(provenance.get('fallback_reason') or 'Yes')}")
                        extraction_report = structured_saved.get("extraction_report") or {}
                        if extraction_report:
                            st.markdown("**Extraction notes**")
                            for note in extraction_report.get("notes") or []:
                                st.caption(str(note))
                        rag = structured_saved.get("rag_metadata") or structured_saved.get("retrieval_metadata") or {}
                        if rag:
                            st.markdown("**Retrieval summary**")
                            st.json(rag)
                        agent_results = structured_saved.get("agent_pipeline") or structured_saved.get("agent_results") or {}
                        if agent_results:
                            st.markdown("**Agent execution results**")
                            st.json(agent_results)
                        if quality.get("coverage_warnings") or quality.get("validation_failures"):
                            st.markdown("**Coverage and validation details**")
                            st.json({"coverage_warnings": quality.get("coverage_warnings") or [],
                                     "validation_failures": quality.get("validation_failures") or []})
                        citations = [{"requirement_id": row.get("requirement_id"), "pages": [ref.get("page_number") for ref in row.get("tender_evidence_refs") or []],
                                     "chunks": [ref.get("chunk_id") for ref in row.get("tender_evidence_refs") or []]}
                                    for row in matrix]
                        st.markdown("**Validated requirement citations**")
                        st.json(citations)

                    st.markdown("<hr style='margin:16px 0; border-color:#e2e8f0;'>", unsafe_allow_html=True)
                    st.markdown("##### Missing Requirements Checklist & Coverage")
                    # "Missing Requirements"
                    quality = structured_saved.get("analysis_quality") or {}
                    edited_requirements = get_custom_requirements(selected_id, user_id)
                    if (edited_requirements is not None and
                            (structured_saved.get("requirements_hash", "auto") != requirements_hash(edited_requirements))):
                        st.info(f"The saved analysis evaluated {len(matrix)} requirement(s); your edited checklist has "
                                f"{len(edited_requirements)}. Re-run analysis to evaluate the edited list.")
                    open_items = [row for row in matrix if normalize_status(row.get("status")) in
                                  ("CONFIRMED GAP", "INFORMATION REQUIRED", "UNRESOLVED")]
                    if open_items:
                        st.markdown("#### Items requiring attention")
                        counts = {}
                        for row in open_items:
                            status = normalize_status(row.get("status"))
                            counts[status] = counts.get(status, 0) + 1
                        summary = " · ".join(f"{count} {status.lower()}" for status, count in counts.items())
                        st.info(f"{len(open_items)} open requirement(s): {summary}. Review the Compliance Matrix tab for clause text, citations and evidence notes.")
                    else:
                        st.success("No saved requirements are currently marked as gaps or needing more information.")

                    st.markdown("#### Complete editable requirements checklist")
                    if tender.get("extracted_text"):
                        render_requirements_editor(tender, profile, user_id, saved_analysis)
                    else:
                        st.info("No extracted tender text is available to populate the checklist.")

            elif tender.get("extracted_text"):
                st.markdown("### Missing Requirements")
                st.caption("Review the extracted requirement list before running the analysis.")
                render_requirements_editor(tender, profile, user_id, saved_analysis)

    # ------------------------------------------------------------
    # PAGE 5: 📈 Reports
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "📈 Reports":
        st.markdown("""
        <div class="saas-header">
            <div>
                <h1>Executive Reports & Audit Analytics</h1>
                <p>Generate formal tender audit reports, executive compliance briefings, and risk analytics for management review.</p>
            </div>
        </div>
        """, unsafe_allow_html=True)

        all_tenders = get_all_tenders(user_id)
        analyzed_tenders = [t for t in all_tenders if get_analysis_for_tender(t["id"], user_id)]

        if not analyzed_tenders:
            st.markdown("""
            <div class="saas-card" style="text-align:center; padding:3rem 1.5rem;">
                <div style="font-size:2.8rem; margin-bottom:0.5rem;">📊</div>
                <h3 style="color:#0f172a; margin-bottom:0.5rem;">No Analyzed Tenders Available for Reporting</h3>
                <p style="color:#64748b; max-width:600px; margin:0 auto 1.5rem auto; font-size:0.92rem; line-height:1.6;">
                    Executive audit summaries and exportable compliance reports are automatically generated once tender documents are evaluated against your company profile.
                </p>
            </div>
            """, unsafe_allow_html=True)
            if st.button("📁 Go to My Tenders to Upload & Analyze"):
                st.session_state["current_page"] = "📁 My Tenders"
                st.rerun()
        else:
            rep_options = {t["id"]: f"{t['filename']} ({t['page_count']} pgs)" for t in analyzed_tenders}
            def_idx = 0
            if st.session_state.get("selected_tender_id") in rep_options:
                def_idx = list(rep_options.keys()).index(st.session_state["selected_tender_id"])

            sel_rep_id = st.selectbox(
                "Select Tender Document for Executive Report",
                options=list(rep_options.keys()),
                format_func=lambda tid: f"📄 {rep_options[tid]}",
                index=def_idx,
            )
            st.session_state["selected_tender_id"] = sel_rep_id

            tender = get_tender_by_id(sel_rep_id, user_id)
            analysis = get_analysis_for_tender(sel_rep_id, user_id)
            profile = get_company_profile(user_id) or {}
            full_data = ensure_seven_sections(analysis, tender.get("extracted_text", ""), profile, pdf_path=tender.get("filepath"))

            ov = full_data.get("tender_overview", {})
            matrix = full_data.get("compliance_matrix", [])
            risks = full_data.get("risk_assessment", {})
            rec = (analysis.get("recommendation") or "REVIEW REQUIRED").upper()

            total_reqs = len(matrix)
            report_statuses = [normalize_status(m.get("status")) for m in matrix]
            sat_count = report_statuses.count("SATISFIED")
            gap_count = report_statuses.count("CONFIRMED GAP")
            info_count = report_statuses.count("INFORMATION REQUIRED") + report_statuses.count("UNRESOLVED")
            comp_rate = int((sat_count / total_reqs * 100)) if total_reqs > 0 else 0

            # KPI Row
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">Compliance Rate</div>
                    <div class="metric-value">{comp_rate}%</div>
                    <div class="metric-sub">{sat_count} of {total_reqs} criteria satisfied</div>
                </div>
                """, unsafe_allow_html=True)
            with k2:
                rec_col = "#16a34a" if rec == "BID" else ("#dc2626" if rec == "NO-BID" else "#d97706")
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title" style="color:{rec_col};">Recommendation</div>
                    <div class="metric-value" style="color:{rec_col}; font-size:1.65rem;">{rec}</div>
                    <div class="metric-sub">Evidence-grounded decision</div>
                </div>
                """, unsafe_allow_html=True)
            with k3:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">Confirmed Gaps</div>
                    <div class="metric-value" style="color:#dc2626;">{gap_count}</div>
                    <div class="metric-sub">Disqualifying criteria</div>
                </div>
                """, unsafe_allow_html=True)
            with k4:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-title">Items Requiring Info</div>
                    <div class="metric-value" style="color:#d97706;">{info_count}</div>
                    <div class="metric-sub">Disclosures to verify</div>
                </div>
                """, unsafe_allow_html=True)

            # Executive Summary Card
            st.markdown(f"""
            <div class="saas-card" style="margin-top:10px;">
                <div class="saas-card-header">📋 Executive Audit Briefing: {h(ov.get('title', tender['filename']))}</div>
                <div style="font-size:0.88rem; color:#64748b; margin-bottom:12px;">
                    Issuing Authority: <b>{h(ov.get('issuing_organization', NOT_FOUND))}</b> &nbsp;·&nbsp; 
                    Reference: <b>{h(ov.get('reference_number', NOT_FOUND))}</b> &nbsp;·&nbsp;
                    Submission Deadline: <b>{h(ov.get('deadline', NOT_FOUND))}</b>
                </div>
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:1rem; font-size:0.92rem; color:#334155; line-height:1.6;">
                    {h(analysis.get('executive_summary') or full_data.get('executive_summary') or 'Complete evidence evaluation performed.')}
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Export Actions
            st.markdown("### Export & Audit Packages")
            exp_c1, exp_c2, exp_c3 = st.columns(3)

            # 1-2. Executive briefing and compliance matrix, built from the saved (deduplicated) analysis
            report_state, _ = analysis_state(analysis, compute_profile_hash(profile), get_custom_requirements(sel_rep_id, user_id))
            report = build_report(tender, analysis, overview=ov, state_label=report_state,
                                  company_name=profile.get("company_name", ""))
            briefing_md, csv_data = report["markdown"], report["csv"]
            st.caption(f"Report source: saved analysis #{analysis['id']} · {str(analysis.get('analyzed_at', ''))[:16]} · {report_state}")

            with exp_c1:
                st.download_button(
                    "📥 Export Briefing Markdown (.md)",
                    data=briefing_md,
                    file_name=f"{tender['filename']}_briefing.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
            with st.expander("Preview executive briefing"):
                st.markdown(briefing_md)

            with exp_c2:
                st.download_button(
                    "📊 Download Compliance Matrix (.csv)",
                    data=csv_data,
                    file_name=f"{tender['filename']}_matrix.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            # 3. Risk Register JSON
            risk_json = json.dumps({
                "tender_filename": tender["filename"],
                "recommendation": rec,
                "penalties": risks.get("penalties", []),
                "deadlines": risks.get("deadlines", []),
                "missing_documents": risks.get("missing_documents", []),
                "unclear_obligations": risks.get("unclear_obligations", []),
            }, indent=2)

            with exp_c3:
                st.download_button(
                    "⚠️ Download Risk Register (.json)",
                    data=risk_json,
                    file_name=f"{tender['filename']}_risks.json",
                    mime="application/json",
                    use_container_width=True,
                )

    # ------------------------------------------------------------
    # PAGE 6: 🤖 Agents
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "🤖 Agents":
        st.markdown("""
        <div class="saas-header">
            <div>
                <h1>Tender Agents</h1>
                <p>Monitor assisted tender analysis, evidence review, and document preparation workflows.</p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""
        <div class="assistant-shell">
            <div class="assistant-header">
                <div class="assistant-mark">TF</div>
                <div><strong>TenderForge AI Assistant</strong><span>Decision support concept · visual preview</span></div>
            </div>
            <div class="assistant-body">
                <div class="assistant-message ai">Welcome to TenderForge AI. I can help you navigate tender requirements and identify evidence gaps.</div>
                <div class="assistant-message user">Summarize the eligibility requirements.</div>
                <div class="assistant-message ai">Your complete tender analysis remains available in the Analysis workspace. This assistant is currently a visual concept.</div>
                <div class="assistant-prompts">
                    <span class="assistant-prompt">Summarize this tender</span>
                    <span class="assistant-prompt">Explain eligibility requirements</span>
                    <span class="assistant-prompt">Show missing documents</span>
                    <span class="assistant-prompt">Identify major risks</span>
                </div>
            </div>
            <div class="assistant-input">
                <span>Ask about this tender...</span>
                <div class="assistant-send">➤</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ------------------------------------------------------------
    # PAGE 7: 📝 Proposals
    # ------------------------------------------------------------
    elif st.session_state["current_page"] == "📝 Proposals":
        st.markdown("""
        <div class="saas-header">
            <div>
                <h1>Proposal Drafts & Bid Generator</h1>
                <p>Author customized, evidence-grounded bid response packages integrating verified company qualifications and technical specifications.</p>
            </div>
        </div>
        """, unsafe_allow_html=True)

        all_tenders = get_all_tenders(user_id)
        analyzed_tenders = [t for t in all_tenders if get_analysis_for_tender(t["id"], user_id)]

        if not analyzed_tenders:
            st.markdown("""
            <div class="saas-card" style="text-align:center; padding:3rem 1.5rem;">
                <div style="font-size:2.8rem; margin-bottom:0.5rem;">📝</div>
                <h3 style="color:#0f172a; margin-bottom:0.5rem;">No Analyzed Tenders Available for Proposal Generation</h3>
                <p style="color:#64748b; max-width:600px; margin:0 auto 1.5rem auto; font-size:0.92rem; line-height:1.6;">
                    The AI Proposal Generator compiles customized responses based on verified tender requirements and your Company Profile. Evaluate a tender document first.
                </p>
            </div>
            """, unsafe_allow_html=True)
            if st.button("📁 Go to My Tenders"):
                st.session_state["current_page"] = "📁 My Tenders"
                st.rerun()
        else:
            prop_options = {t["id"]: f"{t['filename']} ({t['page_count']} pgs)" for t in analyzed_tenders}
            def_idx = 0
            if st.session_state.get("selected_tender_id") in prop_options:
                def_idx = list(prop_options.keys()).index(st.session_state["selected_tender_id"])

            sel_prop_id = st.selectbox(
                "Select Tender for Proposal Drafting",
                options=list(prop_options.keys()),
                format_func=lambda tid: f"📄 {prop_options[tid]}",
                index=def_idx,
            )
            st.session_state["selected_tender_id"] = sel_prop_id

            tender = get_tender_by_id(sel_prop_id, user_id)
            analysis = get_analysis_for_tender(sel_prop_id, user_id)
            profile = get_company_profile(user_id) or {}
            full_data = ensure_seven_sections(analysis, tender.get("extracted_text", ""), profile, pdf_path=tender.get("filepath"))

            ov = full_data.get("tender_overview", {})
            tech = full_data.get("technical_evaluation", {})
            fin = full_data.get("financial_analysis", {})
            risk = full_data.get("risk_assessment", {})
            comp_name = profile.get("company_name") or "Our Enterprise"

            st.markdown(f"""
            <div class="saas-card">
                <div class="saas-card-header">📄 Proposal Package Preview: {h(ov.get('title', tender['filename']))}</div>
                <div style="font-size:0.85rem; color:#64748b; margin-bottom:12px;">
                    Target Client: <b>{h(ov.get('issuing_organization', NOT_FOUND))}</b> &nbsp;·&nbsp;
                    Bidder Entity: <b>{h(comp_name)}</b> &nbsp;·&nbsp;
                    Ref: <b>{h(ov.get('reference_number', NOT_FOUND))}</b>
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Generated Proposal Sections
            p_tab1, p_tab2, p_tab3, p_tab4, p_tab5 = st.tabs([
                "1. Executive Cover Letter",
                "2. Qualifications & Experience",
                "3. Technical Approach",
                "4. Financial & Commercial",
                "5. Submission Enclosures"
            ])

            with p_tab1:
                st.markdown(f"""
                <div class="proposal-preview-box">
                    <b>To:</b><br>
                    The Procurement Officer / Evaluation Committee<br>
                    {h(ov.get('issuing_organization', 'Issuing Authority'))}<br><br>
                    
                    <b>Subject:</b> Formal Submission of Bid for {h(ov.get('title', tender['filename']))} (Ref No: {h(ov.get('reference_number', 'Not Specified'))})<br><br>
                    
                    Dear Sir/Madam,<br><br>
                    
                    We, <b>{h(comp_name)}</b>, hereby formally present our proposal in response to the above-referenced Request for Proposal. Having carefully evaluated all qualification criteria, statement of work parameters, and delivery obligations set forth in the tender notice, we confirm our unconditional capability and commitment to deliver the required scope of work.<br><br>
                    
                    With over <b>{h(profile.get('years_of_experience', 'several years'))}</b> of documented operational track record in <b>{h(profile.get('industry', 'our sector'))}</b> and audited financial scale of <b>{h(profile.get('annual_turnover', 'strong turnover'))}</b>, our organization is positioned to guarantee full compliance, high delivery standards, and strict adherence to project milestones.<br><br>
                    
                    Sincerely,<br>
                    <b>{h(profile.get('contact_name', 'Authorized Signatory'))}</b><br>
                    {h(comp_name)}<br>
                    Email: {h(profile.get('contact_email', ''))} | Phone: {h(profile.get('contact_phone', ''))}
                </div>
                """, unsafe_allow_html=True)

            with p_tab2:
                st.markdown(f"""
                <div class="proposal-preview-box">
                    <h4 style="color:#0f172a; margin-top:0;">Corporate Qualifications & Experience Statement</h4>
                    <p><b>Legal Entity:</b> {h(comp_name)}</p>
                    <p><b>Years in Business:</b> {h(profile.get('years_of_experience', 'Not specified'))}</p>
                    <p><b>Active Certifications:</b> {h(profile.get('certifications', 'Not specified'))}</p>
                    <p><b>Relevant Reference Contracts:</b><br>{h(profile.get('past_projects', 'Not specified'))}</p>
                </div>
                """, unsafe_allow_html=True)

            with p_tab3:
                st.markdown(f"""
                <div class="proposal-preview-box">
                    <h4 style="color:#0f172a; margin-top:0;">Technical Approach & Scope Execution</h4>
                    <p><b>Scope of Work Addressed:</b><br>{h(ov.get('scope_of_work', ''))}</p>
                    <p><b>Technical Competencies Deployed:</b><br>{h(profile.get('technical_capabilities', 'Not specified'))}</p>
                </div>
                """, unsafe_allow_html=True)

            with p_tab4:
                st.markdown(f"""
                <div class="proposal-preview-box">
                    <h4 style="color:#0f172a; margin-top:0;">Commercial & Guarantee Undertakings</h4>
                    <p><b>Turnover Capacity:</b> {h(profile.get('annual_turnover', ''))} (Required: {h(fin.get('turnover_required', {}).get('value', 'Not specified'))})</p>
                    <p><b>EMD / Bid Security Commitment:</b> Ready to deposit {h(fin.get('emd', {}).get('value', 'as required'))}</p>
                    <p><b>Performance Guarantee Commitment:</b> Acceptance of {h(fin.get('pbg', {}).get('value', 'PBG requirements'))}</p>
                    <p><b>Banking Details:</b> {h(profile.get('financial_details', ''))}</p>
                </div>
                """, unsafe_allow_html=True)

            with p_tab5:
                st.markdown("""
                <div class="proposal-preview-box">
                    <h4 style="color:#0f172a; margin-top:0;">Mandatory Enclosures & Submission Verification Checklist</h4>
                """, unsafe_allow_html=True)
                docs = risk.get("missing_documents") or []
                if docs:
                    for d in docs:
                        st.markdown(f"- [ ] 📄 {h(d)}")
                else:
                    st.markdown("- [ ] 📄 Certificate of Incorporation / Business Registration\n- [ ] 📄 Audited Financial Statements for past 3 years\n- [ ] 📄 Quality Accreditation & ISO Certificates\n- [ ] 📄 Earnest Money Deposit (EMD) receipt / Bank Guarantee")
                st.markdown("</div>", unsafe_allow_html=True)

            # Export Full Proposal Button
            full_proposal_doc = f"""# BID PROPOSAL SUBMISSION
**Tender Title:** {ov.get('title', tender['filename'])}
**Reference Number:** {ov.get('reference_number', NOT_FOUND)}
**Issuing Authority:** {ov.get('issuing_organization', NOT_FOUND)}
**Submitting Bidder:** {comp_name}
**Date:** {datetime.now().strftime('%Y-%m-%d')}

---

## 1. Executive Cover Letter
To: The Procurement Evaluation Committee, {ov.get('issuing_organization', '')}
Subject: Formal Proposal for {ov.get('title', tender['filename'])}

We, {comp_name}, submit our comprehensive bid proposal with full commitment to the requirements.

## 2. Corporate Qualifications & Experience
- Operational Track Record: {profile.get('years_of_experience', '')}
- Annual Turnover: {profile.get('annual_turnover', '')}
- Quality Accreditations: {profile.get('certifications', '')}

### Reference Contracts:
{profile.get('past_projects', '')}

## 3. Technical Execution Plan
{profile.get('technical_capabilities', '')}

## 4. Commercial Undertakings
- EMD Commitment: Compliant with {fin.get('emd', {}).get('value', 'tender terms')}
- Performance Guarantee: Compliant with {fin.get('pbg', {}).get('value', 'tender terms')}

---
Generated by {APP_NAME} Enterprise Proposal Suite
"""
            st.markdown("<br>", unsafe_allow_html=True)
            st.download_button(
                "📥 Download Complete Proposal Document (.md)",
                data=full_proposal_doc,
                file_name=f"{tender['filename']}_proposal_draft.md",
                mime="text/markdown",
                use_container_width=True,
                type="primary",
            )


# ============================================================
# ENTRY POINT
# ============================================================
if not st.session_state.get("authenticated") or not st.session_state.get("user"):
    render_auth_gateway()
    st.stop()
else:
    render_authenticated_workspace(st.session_state["user"])
