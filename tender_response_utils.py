"""Small validation helpers for tender-scoped Company Profile responses."""

from collections.abc import Mapping
import hashlib
import re
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Requirement type classification
# ---------------------------------------------------------------------------

# Requirement type is SEPARATE from response/verification status.
# Type describes what the tender *asks for*; status describes what the user *provided*.
REQUIREMENT_TYPE_INFO = "information_required"
REQUIREMENT_TYPE_DOC = "document_required"
REQUIREMENT_TYPE_BOTH = "information_and_document_required"
REQUIREMENT_TYPE_NONE = "no_additional_input"
REQUIREMENT_TYPE_UNKNOWN = "needs_classification"


def classify_requirement_type(requirement: Mapping, guidance: Mapping | None = None) -> str:
    """Classify what a requirement asks for: information, document, both, neither, or unknown.

    This is independent of the response/verification status. A requirement can need
    information even if its citation is unvalidated.

    Parameters
    ----------
    requirement : Mapping
        A compliance-matrix row or requirement record.
    guidance : Mapping | None
        The output of ``profile_fields.guidance_for_requirement()`` for this
        requirement, if already computed. When *None*, a lightweight heuristic
        is used instead.
    """
    has_explicit_document = bool(
        requirement.get("document_checklist_item")
        or requirement.get("required_document")
        or (guidance and guidance.get("explicit_document"))
    )
    needs_info = False

    if requirement.get("document_checklist_item"):
        # A pure document checklist item requires a document submission.
        needs_info = False
    elif guidance is not None:
        info_lines = guidance.get("information") or []
        needs_info = bool(info_lines and any(
            line.strip() and "no supporting document" not in line.lower()
            for line in info_lines
        ))
    else:
        needs_info = True

    if needs_info and has_explicit_document:
        return REQUIREMENT_TYPE_BOTH
    if has_explicit_document:
        return REQUIREMENT_TYPE_DOC
    if needs_info:
        return REQUIREMENT_TYPE_INFO
    return REQUIREMENT_TYPE_UNKNOWN


def requirement_needs_information(requirement: Mapping, guidance: Mapping | None = None) -> bool:
    """Return True if requirement asks for information (either info only, or both info and doc)."""
    t = classify_requirement_type(requirement, guidance)
    return t in (REQUIREMENT_TYPE_INFO, REQUIREMENT_TYPE_BOTH)


def requirement_needs_documents(requirement: Mapping, guidance: Mapping | None = None) -> bool:
    """Return True if requirement asks for documents (either doc only, or both info and doc)."""
    t = classify_requirement_type(requirement, guidance)
    return t in (REQUIREMENT_TYPE_DOC, REQUIREMENT_TYPE_BOTH)


def requirement_group(requirement: Mapping, guidance: Mapping | None = None) -> str:
    """Map a requirement to one of the business-friendly Section 2 groups."""
    text = " ".join(str(requirement.get(key) or "") for key in (
        "requirement", "category", "requirement_scope", "source_quotation", "tender_evidence"))
    lowered = text.casefold()
    if any(word in lowered for word in ("experience", "project", "work completed", "similar work")):
        return "Previous Project Experience"
    if any(word in lowered for word in ("turnover", "net worth", "financial", "annual return", "emd", "bid security", "tender fee")):
        return "Financial Requirements"
    if any(word in lowered for word in ("gst", "iso", "certif", "registration", "pan card", "company registration")):
        return "Registrations & Certifications"
    if any(word in lowered for word in ("blacklist", "debar", "undertaking", "declaration", "affidavit")):
        return "Declarations & Other Conditions"
    if any(word in lowered for word in ("technical", "specification", "make/model", "service centre", "service support", "equipment", "capacity")):
        return "Technical Requirements"
    return "Company Eligibility"


def requirement_title(requirement: Mapping) -> str:
    """Return a short, business-readable title without changing source text."""
    text = str(requirement.get("requirement") or "Tender requirement").strip()
    lowered = text.casefold()
    mappings = (
        ("ISO 9001 certification", ("iso 9001", "iso-9001")),
        ("ISO 13485 certification", ("iso 13485", "iso-13485")),
        ("ISO 14001 certification", ("iso 14001", "iso-14001")),
        ("CE Certification", ("ce mark", "ce certification")),
        ("CDSCO Registration", ("cdsco",)),
        ("Solvency Certificate", ("solvency",)),
        ("Annual Turnover", ("turnover", "annual return")),
        ("Net Worth", ("net worth",)),
        ("GST Registration", ("gst",)),
        ("Company Registration", ("company registration", "incorporation", "certificate of registration")),
        ("Past Experience", ("past experience", "similar project", "similar work", "similar completed", "completed project")),
        ("Technical Capability", ("technical capability", "technical specification", "service support")),
        ("OEM Authorization", ("oem", "authorization", "authorisation")),
        ("Blacklisting Declaration", ("blacklist", "black-list", "debar")),
        ("Certifications", ("certificate", "certification", "iso")),
        ("Earnest Money / Bid Security", ("earnest money", "emd", "bid security")),
        ("Tender Document Fee", ("tender fee", "document fee", "processing fee")),
    )
    for title, markers in mappings:
        if any(marker in lowered for marker in markers):
            return title
    text = re.sub(r"^(?:ELG|FIN|EXP|TECH|REG|DOC|REQ)[- ]*\d*\s*[|:\-]*\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -:|")
    return (text[:100] + "…") if len(text) > 100 else text or "Tender Requirement"


def information_required_label(requirement: Mapping, guidance: Mapping | None = None,
                               definition: Mapping | None = None) -> str:
    """Return a concise, one-line instruction explaining exactly what the company needs to provide."""
    if requirement.get("document_checklist_item"):
        return "Upload the required document specified below (no text response required)."

    field_key = str((definition or {}).get("field_key") or "")
    text = " ".join(str(requirement.get(k) or "") for k in (
        "requirement", "source_quotation", "tender_evidence", "required_document"
    )).casefold()

    if field_key == "financial.annual_turnover_by_year" or "turnover" in text:
        return "Enter turnover figures separately for each required financial year (currency and unit specified)."
    if field_key == "experience.years_in_business" or "years in business" in text or "years of experience" in text:
        return "Provide total years in operation and business commencement or incorporation date."
    if field_key == "experience.similar_projects" or any(w in text for w in ("similar project", "similar work", "past experience")):
        return "Provide client name, contract value, completion date, and scope for similar completed projects."
    if field_key == "capability.service_support" or any(w in text for w in ("service centre", "service center", "support network", "after sales")):
        return "Provide service centre locations, support engineer count, response times, and warranty support details."
    if field_key == "registration.gst" or "gst" in text:
        return "Provide registered GSTIN, legal entity name, and return filing status for the applicable period."
    if field_key == "financial.net_worth" or "net worth" in text:
        return "Provide net worth figure for the required financial period with currency and calculation basis."
    if field_key == "financial.solvency_certificate" or "solvency" in text:
        return "Provide solvency amount, issuing bank, and date of issue as required by the clause."
    if field_key == "financial.audited_accounts" or "audited" in text:
        return "Provide audited balance sheets and financial statements for the financial years specified in the clause."
    if field_key == "authorization.oem" or any(w in text for w in ("oem authorization", "manufacturer authorization")):
        return "Provide OEM manufacturer name, authorized products / models, and authorization validity period."
    if field_key.startswith("certification.") or "iso" in text or "certif" in text:
        return "Provide certificate number, issuing body, and validity / expiry date."
    if any(w in text for w in ("earnest money", "emd", "bid security")):
        return "Provide payment amount, transaction / reference number, payment date, or valid exemption details."
    if any(w in text for w in ("tender fee", "document fee", "processing fee")):
        return "Provide fee payment amount, payment method / reference number, and date of payment."
    if any(w in text for w in ("make/model", "make and model", "model number", "manufacturer.*model")):
        return "Provide equipment name, manufacturer, exact make and model number, and offered configuration."
    if any(w in text for w in ("blacklist", "debar", "affidavit", "undertaking")):
        return "Confirm whether the company has ever been blacklisted, debarred, or declared bankrupt by any authority."

    if guidance and guidance.get("information"):
        lines = [line.strip() for line in guidance["information"] if line.strip()]
        if lines:
            line = lines[0]
            line = re.sub(r"^(?:Address the cited tender clause directly:\s*|Review the cited tender page and\s*)", "", line, flags=re.I)
            if line:
                if not line.endswith("."):
                    line += "."
                return line[:160]

    return "Provide the specific response and factual confirmation requested by this tender clause."


def documents_required_label(requirement: Mapping, guidance: Mapping | None = None,
                             definition: Mapping | None = None) -> tuple[str, str, bool]:
    """Return (document_name, badge_label, is_mandatory).

    badge_label is 'Mandatory', 'Optional', or 'None'.
    """
    analysis = requirement.get("section2_analysis")
    if isinstance(analysis, Mapping) and analysis.get("status") == "analyzed":
        required_docs = analysis.get("required_documents") or []
        optional_docs = analysis.get("optional_evidence") or []
        if required_docs:
            return "; ".join(str(doc.get("label") or doc.get("document_type")) for doc in required_docs), "Mandatory", True
        if optional_docs:
            return "; ".join(str(doc.get("label") or doc.get("document_type")) for doc in optional_docs), "Optional", False
        return "No specific supporting document identified in this clause.", "None", False

    is_checklist = bool(requirement.get("document_checklist_item"))
    has_explicit = bool(requirement.get("required_document") or is_checklist)
    is_mandatory = bool(requirement.get("is_mandatory", True)) and (has_explicit or is_checklist)

    text = " ".join(str(requirement.get(k) or "") for k in (
        "requirement", "source_quotation", "tender_evidence", "required_document"
    )).casefold()
    doc_raw = str(requirement.get("required_document") or "").strip()

    if is_checklist:
        doc_name = str(requirement.get("required_document") or requirement.get("requirement") or "Required checklist document").strip()
        return doc_name, "Mandatory", True

    # Guidance can suggest useful evidence; only tender-named documents belong in
    # the mandatory upload list.
    if doc_raw and "no supporting document" not in doc_raw.lower():
        badge = "Mandatory" if is_mandatory else "Optional"
        return doc_raw, badge, is_mandatory

    if guidance and guidance.get("documents"):
        badge = "Mandatory" if is_mandatory else "Optional"
        return "; ".join(str(d) for d in guidance["documents"]), badge, is_mandatory

    if "turnover" in text:
        return "Audited financial statements / CA turnover certificate", ("Mandatory" if is_mandatory else "Optional"), is_mandatory

    if "iso" in text or "certif" in text or (definition and "cert" in str(definition.get("field_key", "")).lower()):
        return "Valid certificate copy", ("Mandatory" if is_mandatory else "Optional"), is_mandatory

    return "No specific supporting document identified in this clause.", "None", False


def simple_status_label(evidence_status: str, needs_information: bool = False, needs_documents: bool = False) -> tuple[str, str]:
    status = str(evidence_status or "")
    if status == "Verified":
        return "Verified", "The evidence has been reviewed and accepted."
    if status == "Evidence uploaded":
        return "Under Review", "Document uploaded. It is pending verification and is not yet accepted."
    if status == "Needs review":
        return "Under Review", "The response or source needs review."
    if needs_documents:
        return "Document Required", "Add the supporting document requested by this requirement."
    if needs_information:
        return "Information Needed", "Enter the requested information before this requirement can be completed."
    if status == "Information provided":
        return "Information Needed", "Information is saved, but supporting evidence has not been verified."
    return "Not Started", "No response or supporting document has been saved yet."


def filter_requirement_records(records: list[Mapping], choice: str) -> list[Mapping]:
    """Filter by requirement type or evidence review status, keeping those dimensions separate."""
    if choice == "Action Required":
        return [record for record in records if record.get("status") != "Verified"]
    if choice == "Documents Pending":
        return [record for record in records if record.get("status") in ("Evidence uploaded", "Needs review") or record.get("needs_documents")]
    if choice == "Information Required":
        return [record for record in records if record.get("needs_information")]
    if choice == "Documents Required":
        return [record for record in records if record.get("needs_documents")]
    if choice == "Needs Review":
        return [record for record in records if record.get("status") == "Needs review"]
    if choice == "Verified":
        return [record for record in records if record.get("status") == "Verified"]
    return list(records)


def _positive_id(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 and str(value).strip() == str(result) else None


def resolve_tender_id(tender_record: Mapping | None, fallback_id=None) -> int | None:
    """Resolve only an explicit saved-record ID or a caller-proven selected-tender ID.

    An invalid or conflicting explicit ID fails closed. The fallback is intended for
    summaries that omit IDs when the caller has already established the selected
    tender from the user's saved tender list.
    """
    if not isinstance(tender_record, Mapping):
        return None

    explicit_keys = [key for key in ("id", "tender_id")
                     if key in tender_record and tender_record.get(key) is not None]
    explicit_ids = [_positive_id(tender_record[key]) for key in explicit_keys]
    if explicit_ids and (any(value is None for value in explicit_ids) or len(set(explicit_ids)) != 1):
        return None

    fallback = _positive_id(fallback_id)
    if fallback_id is not None and fallback is None:
        return None
    explicit = explicit_ids[0] if explicit_ids else None
    if explicit is not None and fallback is not None and explicit != fallback:
        return None
    return explicit if explicit is not None else fallback


def source_reference_is_valid(requirement: Mapping) -> bool:
    """A source is usable only with a verified quotation and a real page reference."""
    if requirement.get("source_verified") is not True:
        return False
    try:
        page = int(requirement.get("source_page"))
    except (TypeError, ValueError):
        return False
    if page < 1 or requirement.get("retrieval_grounded") is False:
        return False
    if requirement.get("agent1_validation") in ("unverified_source", "source_verified_not_retrieved"):
        return False
    refs = requirement.get("tender_evidence_refs") or []
    if refs:
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            try:
                ref_page = int(ref.get("page_number"))
            except (TypeError, ValueError):
                continue
            if ref_page == page and str(ref.get("chunk_id") or "").strip():
                return True
        return False
    return bool(str(requirement.get("source_quotation") or requirement.get("tender_evidence") or "").strip())


def response_evidence_status(requirement: Mapping, saved: Mapping | None) -> str:
    """Return a conservative presentation status without treating an answer as proof."""
    if not source_reference_is_valid(requirement):
        return "Needs review"
    if saved:
        quote = " ".join(str(requirement.get("source_quotation") or requirement.get("tender_evidence") or "").split())
        current_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        if saved.get("source_quote_sha256") != current_hash:
            return "Needs review"
        documents = saved.get("documents") or []
        response = saved.get("response") or {}
        has_response = any(str(value).strip() for value in response.values() if not isinstance(value, list)) or bool(response.get("projects"))
        doc_only = bool(requirement.get("document_checklist_item"))
        mandatory_document = bool(requirement.get("required_document") or doc_only)
        has_verified_doc = any(str(doc.get("verification_status") or "").lower() == "verified" for doc in documents)
        has_verified_response = str(response.get("verification_status") or "").lower() == "verified"
        if has_verified_doc or has_verified_response:
            return "Verified"
        if documents:
            return "Evidence uploaded"
        if has_response:
            return "Information provided" 
    status = " ".join(str(requirement.get("status") or "").upper().replace("_", " ").split())
    if status in ("CONFIRMED GAP", "UNRESOLVED"):
        return "Needs review"
    if status == "SATISFIED":
        return "Information provided"
    return "Not provided"


def record_analysis_outcome(state: dict, tender_id: int, outcome: Mapping,
                            completed_at: datetime | None = None) -> None:
    """Clear a tender's local progress state and retain only its success or failure result."""
    state.pop(f"analysis_job_{tender_id}", None)
    if outcome.get("saved"):
        state.pop(f"last_failed_run_{tender_id}", None)
        state[f"analysis_flash_{tender_id}"] = f"Analysis #{outcome['analysis_id']} completed and saved."
    elif outcome.get("stage") == "kept_previous":
        state[f"analysis_flash_{tender_id}"] = outcome.get("error") or "The previous complete analysis was kept."
    else:
        when = completed_at or datetime.now()
        state[f"last_failed_run_{tender_id}"] = {
            "at": when.strftime("%Y-%m-%d %H:%M"),
            "stage": outcome.get("stage") or "analysis",
            "error": str(outcome.get("error") or "The analysis failed.")[:1500],
        }


def existing_company_pdf_payload(company_documents: list[Mapping], document_id) -> dict | None:
    """Read a PDF only from the caller's already user-scoped company document records."""
    if document_id in (None, ""):
        return None
    selected_id = _positive_id(document_id)
    if selected_id is None:
        raise ValueError("Select a valid saved company document.")
    document = next((item for item in company_documents if _positive_id(item.get("id")) == selected_id), None)
    if not document:
        raise PermissionError("The selected company document is not available to this user.")
    filename = str(document.get("filename") or "")
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Only saved PDF evidence can be attached to a tender requirement.")
    path = Path(str(document.get("storage_path") or ""))
    if not path.is_file():
        raise FileNotFoundError("The saved company PDF is no longer available on disk.")
    content = path.read_bytes()
    if not content:
        raise ValueError("The saved company PDF is empty.")
    return {"filename": filename, "content": content,
            "media_type": str(document.get("media_type") or "application/pdf")}
