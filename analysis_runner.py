"""
Run and persist one tender analysis (Section 4).

The company profile is re-read from the database at the start of every run, so a re-analysis uses the
latest saved fixed fields, dynamic fields, documents and verification statuses — never a page-level
snapshot. The profile hash stored with the analysis is computed from that same snapshot.

Only a complete three-agent result is saved. Any failure (indexing, retrieval, an agent, or the save
itself) is reported and the previously saved analysis is left untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

import database as db
from agents import run_three_agent_pipeline
from pdf_processor import extract_text_from_pdf
from rag import index_tender, retrieve_requirement_evidence

REQ_EDITOR_COLUMNS = ["requirement_id", "category", "requirement", "is_mandatory",
                      "source_page", "required_document", "source_quotation"]


def requirements_hash(reqs: list | None) -> str:
    """Stable short hash of a requirement list (detects edits made after the last analysis)."""
    if not reqs:
        return "auto"
    core = [{k: r.get(k) for k in REQ_EDITOR_COLUMNS} for r in reqs]
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def run_analysis_for_tender(user_id: int, tender_id: int, *, embedder=None, model_name: str | None = None,
                            model: str | None = None, progress=None) -> dict:
    """
    `progress(stage, message)` is called when each stage starts (and once after the result is stored);
    it never reports a stage as finished before it is.
    Returns {"saved": bool, "stage": ..., "error": ..., "analysis_id": ..., "data": ..., "result": ...}.
    "saved" is True only when a complete pipeline result was stored.
    """
    tender = db.get_tender_by_id(tender_id, user_id)
    if not tender:
        return {"saved": False, "stage": "tender", "error_type": "tender_not_found", "error": "Tender not found for this user."}
    if not tender.get("extracted_text"):
        return {"saved": False, "stage": "tender", "error_type": "no_text",
                "error": "This tender document has no extracted text. Image-only or corrupted PDFs cannot be analyzed."}

    # Old uploads may predate page-marked extraction and otherwise be treated as page 1
    # during citation validation. Rebuild this derived text from the owned original PDF.
    pdf_path = tender.get("filepath") or ""
    if pdf_path and os.path.isfile(pdf_path):
        refreshed = extract_text_from_pdf(pdf_path)
        if refreshed.get("success") and refreshed.get("text", "").strip() and refreshed.get("pages"):
            expected_pages = int(refreshed.get("page_count") or 0)
            stored_markers = len(re.findall(r"---\s*\[Page\s*\d+\]\s*---", tender["extracted_text"], re.I))
            if stored_markers != expected_pages:
                if not db.update_tender_extracted_text(tender_id, user_id, refreshed["text"], expected_pages):
                    return {"saved": False, "stage": "tender", "error_type": "text_refresh_failed",
                            "error": "The saved tender text could not be refreshed for the owning user."}
            tender["extracted_text"] = refreshed["text"]
            tender["page_count"] = expected_pages or tender.get("page_count")

    profile = db.get_company_profile(user_id) or {}          # fresh read: the latest saved evidence
    profile_hash = db.compute_profile_hash(profile)
    custom_reqs = db.get_custom_requirements(tender_id, user_id)

    def report(stage, message):
        if progress:
            try:
                progress(stage, message)
            except Exception:  # a display problem must not break the analysis
                pass

    rag_kwargs = {k: v for k, v in (("embedder", embedder), ("model_name", model_name)) if v is not None}
    report("indexing", "Indexing tender text (reused when this PDF was indexed before)")
    index_result = index_tender(user_id, tender_id, **rag_kwargs)  # idempotent: reindexes only if the PDF/model changed
    if not index_result.get("success"):
        return {"saved": False, "stage": "indexing", "error_type": index_result.get("error_type", "indexing_failed"),
                "error": index_result.get("error", "Unknown indexing error"), "index": index_result}
    report("retrieval", "Retrieving tender evidence for each requirement")
    retrieval = retrieve_requirement_evidence(user_id, tender_id, custom_reqs or None, **rag_kwargs)
    if not retrieval.get("success"):
        return {"saved": False, "stage": "retrieval", "error_type": retrieval.get("error_type", "retrieval_failed"),
                "error": retrieval.get("error", "Unknown retrieval error"), "index": index_result}

    try:
        result = run_three_agent_pipeline(tender["extracted_text"], profile, retrieval["chunks"], retrieval,
                                          pdf_path=tender.get("filepath"), custom_requirements=custom_reqs, model=model,
                                          progress=report)
    except Exception as exc:  # an agent crashed: nothing is saved
        return {"saved": False, "stage": "agents", "error_type": "agent_exception", "error": str(exc)[:1500], "index": index_result}
    # Saved: a complete pipeline, or one whose agents all produced validated output and whose only issues are
    # coverage warnings (the decision is then limited by the incomplete-extraction rule). Never saved: any agent
    # output that failed validation (unusable/truncated responses, failed batches, invalid citations).
    if not (result.get("pipeline_complete") or result.get("saveable")):
        failures = (result.get("analysis_quality") or {}).get("validation_failures") or []
        return {"saved": False, "stage": "agents", "error_type": result.get("error_type", "validation_failed"),
                "error": "; ".join(failures) or result.get("error") or "The three-agent analysis is incomplete.",
                "result": result, "index": index_result}

    data = result["data"]
    data["requirements_source"] = "user" if custom_reqs else "auto"
    data["requirements_hash"] = requirements_hash(custom_reqs)
    # A run with coverage warnings never replaces a complete analysis of the same company evidence and requirements:
    # it would only lose information (e.g. a transient AI outage). Changed evidence is always saved.
    previous = db.get_analysis_for_tender(tender_id, user_id)
    prev_data = (previous or {}).get("structured_tender_data") or {}
    if (not result.get("pipeline_complete") and previous
            and (prev_data.get("analysis_quality") or {}).get("status") == "complete"
            and previous.get("profile_hash") == profile_hash
            and prev_data.get("requirements_hash", "auto") == data["requirements_hash"]):
        return {"saved": False, "stage": "kept_previous", "error_type": "previous_analysis_more_complete",
                "error": (f"This run had coverage warnings ({'; '.join(result['analysis_quality']['coverage_warnings'][:2])}). "
                          f"The previous complete analysis #{previous['id']} used the same company information and requirements, "
                          "so it was kept."), "result": result, "index": index_result}
    data.setdefault("company_evidence_snapshot", {})["profile_hash"] = profile_hash
    # The company this analysis was produced for (shown with the analysis and in reports).
    data["company_snapshot"] = {"user_id": user_id, "company_name": str(profile.get("company_name") or "").strip(),
                                "profile_hash": profile_hash, "profile_updated_at": profile.get("updated_at")}
    report("saving", "Saving the analysis")
    try:
        analysis_id = db.save_analysis(
            tender_id=tender_id, profile_hash=profile_hash,
            recommendation=data.get("recommendation", "REVIEW REQUIRED"),
            executive_summary=data.get("executive_summary", ""), structured_tender_data=data,
            compliance_matrix=data.get("compliance_matrix", []), risks=data.get("risks", []),
            missing_information=data.get("missing_information", []),
            model_used=result.get("model_used") or data.get("model_used", "groq-ai"), user_id=user_id)
    except Exception as exc:  # the insert did not happen; the previous analysis is still the latest one
        return {"saved": False, "stage": "save", "error_type": "save_failed", "error": str(exc)[:1500],
                "result": result, "index": index_result}
    report("saved", f"Analysis #{analysis_id} saved")
    return {"saved": True, "stage": "saved", "analysis_id": analysis_id, "profile_hash": profile_hash,
            "data": data, "result": result, "index": index_result}
