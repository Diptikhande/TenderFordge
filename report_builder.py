"""Executive report and compliance-matrix export built from one saved analysis (Section 5).

Counts, statuses and citations come from the saved (deduplicated) compliance matrix, so the report agrees
with the Tender Analysis page. Verification states are labelled honestly: an in-app document check of the
user's own upload is never presented as independent verification.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

import analyzer as A

STATUS_ORDER = (A.SATISFIED, A.CONFIRMED_GAP, A.INFORMATION_REQUIRED, A.UNRESOLVED, A.NOT_APPLICABLE)


def evidence_state(status, review_method=None) -> str:
    status = str(status or "unverified").lower()
    if status == "verified":
        return ("verified (document-checked in-app, not independent verification)"
                if review_method == "document_excerpt_check" else "verified (no in-app review record)")
    return "rejected/withdrawn" if status == "rejected" else "unverified (user-provided)"


def _citation(row: dict) -> str:
    refs = row.get("tender_evidence_refs") or []
    if refs:
        return "; ".join(f"p.{r.get('page_number')} [{r.get('chunk_id')}]" for r in refs)
    return f"p.{row.get('source_page') or '?'} (no validated retrieved chunk)"


def _evidence_used(row: dict) -> str:
    items = []
    for p in row.get("company_evidence_provenance") or []:
        where = f", {p.get('filename')} v{p.get('document_version')} p.{p.get('source_page') or '?'}" if p.get("filename") else ""
        items.append(f"{p.get('field_label') or p.get('field_key')}: {str(p.get('value_text'))[:80]} "
                     f"[{evidence_state(p.get('verification_status'), p.get('review_method'))}{where}]")
    return "; ".join(items) or "none"


def build_report(tender: dict, analysis: dict, *, overview: dict | None = None, state_label: str = "",
                 company_name: str = "", generated_at: datetime | None = None) -> dict:
    """Return {"markdown", "csv", "counts", "decision"} for one saved analysis."""
    data = analysis.get("structured_tender_data") or {}
    matrix = analysis.get("compliance_matrix") or data.get("compliance_matrix") or []
    overview = overview or data.get("tender_overview") or {}
    rec = data.get("bid_recommendation") or {}
    decision = str(analysis.get("recommendation") or rec.get("decision") or "REVIEW REQUIRED").upper()
    counts = {s: 0 for s in STATUS_ORDER}
    for row in matrix:
        counts[A.normalize_status(row.get("status"))] += 1
    merged = [d for row in matrix for d in row.get("merged_duplicates") or []]
    nf = A.NOT_FOUND
    snapshot = data.get("company_snapshot") or {}
    if snapshot:
        bidder = snapshot.get("company_name") or "(no company name in the profile used)"
        if company_name and company_name.strip().lower() != str(snapshot.get("company_name") or "").strip().lower():
            bidder += f" — note: the current profile name is '{company_name}'; re-run the analysis for the current company"
    else:
        bidder = "Not recorded for this analysis (older analysis)" + (f"; current profile: {company_name}" if company_name else "")
    quality = data.get("analysis_quality") or {}

    lines = [
        "# Executive Tender Audit Briefing",
        f"**Tender:** {overview.get('title', tender.get('filename'))}  ",
        f"**Reference:** {overview.get('reference_number', nf)} · **Issuing authority:** {overview.get('issuing_organization', nf)}  ",
        f"**Submission deadline:** {overview.get('deadline', nf)}  ",
        f"**Bidder (company profile used):** {bidder}  ",
        f"**Saved analysis:** #{analysis.get('id')} generated {str(analysis.get('analyzed_at') or '')[:16]} "
        f"using {analysis.get('model_used') or 'unknown engine'}" + (f" · status: {state_label}" if state_label else ""),
        *(["**Coverage warnings:** saved with incomplete extraction coverage, so BID is not possible — "
           + "; ".join(quality.get("coverage_warnings", [])[:4])] if quality.get("status") == "coverage_warnings" else []),
        "",
        f"## 1. Bid decision: {decision}",
        str(analysis.get("executive_summary") or data.get("executive_summary") or ""),
        "",
        "## 2. Requirement status",
        f"- Requirements evaluated: {len(matrix)}" + (f" (after merging {len(merged)} duplicate(s))" if merged else ""),
    ]
    lines += [f"- {s.title()}: {counts[s]}" for s in STATUS_ORDER]
    lines += ["", "## 3. Confirmed disqualifying blockers"]
    lines += [f"- {b}" for b in rec.get("blockers") or []] or ["- None confirmed by verified evidence."]
    open_points = [r for r in rec.get("reasons") or [] if "need" in r or "unresolved" in r]
    if open_points:
        lines += ["", "## 4. Open points (not failures)"] + [f"- {r}" for r in open_points]
    assessment = rec.get("assessment") or {}
    for key, title in (("financial_obligations", "Financial obligations stated in the tender"),
                       ("business_fit_concerns", "Business-fit concerns (not disqualifying)")):
        if assessment.get(key):
            lines += ["", f"### {title}"] + [f"- {e}" for e in assessment[key]]
    lines += ["", "## 5. Requirements", "",
              "| ID | Status | Mandatory | Tender citation | Company evidence used | Confirmation needed |",
              "|---|---|---|---|---|---|"]
    for row in matrix:
        cells = [row.get("requirement_id"), A.normalize_status(row.get("status")), "yes" if row.get("is_mandatory", True) else "no",
                 _citation(row), _evidence_used(row), row.get("evidence_confirmation_needed") or ""]
        lines.append("| " + " | ".join(str(c or "").replace("|", "/").replace("\n", " ") for c in cells) + " |")
    if merged:
        lines += ["", "Merged duplicates: " + "; ".join(f"{d.get('requirement_id')} (p.{d.get('source_page')})" for d in merged)]
    lines += ["", "---",
              "Evidence states: *unverified* = provided by the company and not checked; *verified (document-checked in-app)* = the "
              "quoted text was found on the stated page of the company's uploaded PDF. No independent third-party verification is performed.",
              f"Generated by TenderForge on {(generated_at or datetime.now()).strftime('%Y-%m-%d %H:%M')}"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Requirement ID", "Category", "Requirement", "Status", "Mandatory", "Source Page", "Tender Citation",
                     "Tender Evidence", "Company Evidence Used", "Confirmation Needed", "Merged Duplicates", "Explanation"])
    for row in matrix:
        writer.writerow([row.get("requirement_id", ""), row.get("category", ""), row.get("requirement", ""),
                         A.normalize_status(row.get("status")), "yes" if row.get("is_mandatory", True) else "no",
                         row.get("source_page", ""), _citation(row), row.get("tender_evidence", ""), _evidence_used(row),
                         row.get("evidence_confirmation_needed", ""),
                         ", ".join(str(d.get("requirement_id")) for d in row.get("merged_duplicates") or []),
                         row.get("explanation", "")])
    return {"markdown": "\n".join(lines), "csv": buffer.getvalue(), "counts": counts, "decision": decision,
            "requirement_count": len(matrix)}
