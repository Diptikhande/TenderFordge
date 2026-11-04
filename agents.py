"""Sequential, evidence-validated Groq agents for tender decisions."""

from __future__ import annotations

import copy
import json
import re

import analyzer as A
import profile_fields as PF
from config import GROQ_MODEL


AGENT_ORDER = (
    "Requirement Extraction & Eligibility",
    "Compliance & Risk Analysis",
    "Bid Recommendation & Report",
)
AGENT_CONTRACT_VERSION = "tender-agents-v1"
_PROFILE_EVIDENCE_FIELDS = tuple(A.PROFILE_EVIDENCE_KEYS)


_SECTION2_CATEGORIES = {"Eligibility", "Financial", "Technical", "Experience",
                        "Registrations & Certifications", "Required Documents",
                        "EMD & Payments", "Other Bidder Obligations"}

_SECTION2_GENERIC_LABELS = {"details", "enter details", "provide information", "enter response",
                            "add information here", "information", "response"}

def _is_heading_only_requirement(item: dict) -> bool:
    """Drop only clear standalone labels with no bidder action or substantive clause."""
    title = " ".join(str(item.get("requirement") or "").split()).strip(" .:-")
    quote = " ".join(str(item.get("source_quotation") or "").split()).strip(" .:-")
    is_annex_title = bool(re.match(r"^(?:annex(?:ure)?|appendix|schedule)\b", title, re.I))
    has_action = bool(re.search(r"\b(?:must|shall|required|submit|provide|complete|fill|sign|attach|furnish|upload)\b", quote, re.I))
    if is_annex_title and len(re.findall(r"\w+", quote)) <= 14 and not has_action:
        return True
    if len(re.findall(r"\w+", quote)) <= 7 and re.fullmatch(
            r"(?:instructions? to bidders?|important instructions?|general conditions?|tender conditions?|eligibility criteria|technical specifications?)",
            title, re.I):
        return True
    return False

def _validate_section2_analysis(raw: dict, requirement: dict, profile: dict) -> dict:
    """Validate model-generated Section 2 fields/docs against source and saved profile."""
    if not isinstance(raw, dict):
        raise ValueError("Section 2 analysis must be a JSON object")
    title = raw.get("title")
    description = raw.get("description")
    category = raw.get("category")
    if not all(isinstance(x, str) and x.strip() for x in (title, description, category)):
        raise ValueError("Section 2 analysis omitted title, description, or category")
    if category not in _SECTION2_CATEGORIES:
        raise ValueError("Section 2 analysis returned an unknown category")
    nature = raw.get("nature")
    if nature not in ("mandatory", "conditional", "optional", "review"):
        raise ValueError("Section 2 analysis returned an invalid requirement nature")
    source = " ".join(str(requirement.get("source_quotation") or "").split())
    source_page = int(requirement.get("source_page") or 0)
    if source_page < 1 or not source:
        raise ValueError("Requirement has no traceable source clause/page")

    profile_values = {str(k): str(v).strip() for k, v in (profile or {}).items()
                      if isinstance(v, (str, int, float)) and str(v).strip()}
    profile_values.update({str(row.get("field_key")): str(row.get("field_value") or "").strip()
                           for row in (profile or {}).get("custom_fields", [])
                           if isinstance(row, dict) and row.get("field_key") and row.get("field_value")})
    allowed_values = {" ".join(v.split()).casefold(): v for v in profile_values.values()}

    def matched_profile_value(candidate, field_key, field_label):
        if candidate is None:
            return None
        value = allowed_values.get(" ".join(str(candidate).split()).casefold())
        if value is None:
            return None
        profile_key = next((key for key, val in profile_values.items() if val == value), "")
        field_tokens = set(re.findall(r"[a-z0-9]+", f"{field_key} {field_label}".casefold()))
        profile_tokens = set(re.findall(r"[a-z0-9]+", profile_key.casefold()))
        field_tokens -= {"value", "details", "information", "company", "data", "number"}
        profile_tokens -= {"value", "details", "information", "company", "data", "number"}
        return value if field_tokens & profile_tokens else None

    fields = raw.get("information_fields", [])
    if not isinstance(fields, list):
        raise ValueError("information_fields must be a list")
    clean_fields = []
    for field in fields[:16]:
        if not isinstance(field, dict):
            raise ValueError("An information field was not an object")
        key, label = field.get("key"), field.get("label")
        input_type = field.get("input_type", "text")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", key):
            raise ValueError("An information field has an invalid key")
        if not isinstance(label, str) or not label.strip() or label.strip().casefold() in _SECTION2_GENERIC_LABELS:
            raise ValueError("An information field has a generic or missing label")
        if input_type not in ("text", "number", "date", "select", "textarea", "currency", "boolean"):
            raise ValueError("An information field has an unsupported input type")
        existing = matched_profile_value(field.get("existing_value"), key, label)
        options = field.get("options", [])
        options = [str(option)[:120] for option in options[:20]
                   if isinstance(option, (str, int, float))] if isinstance(options, list) else []
        clean_fields.append({"key": key, "label": label.strip()[:100], "input_type": input_type,
                             "required": bool(field.get("required", True)), "existing_value": existing,
                             "missing": existing is None, "options": options})

    def clean_docs(key: str, mandatory: bool) -> list[dict]:
        rows = raw.get(key, [])
        if not isinstance(rows, list):
            raise ValueError(f"{key} must be a list")
        clean = []
        for doc in rows[:12]:
            if not isinstance(doc, dict):
                raise ValueError("A document analysis entry was not an object")
            name, quote = doc.get("document_type"), doc.get("source_quote")
            if not isinstance(name, str) or not name.strip() or not isinstance(quote, str) or not quote.strip():
                raise ValueError("A document entry omitted its name or source quote")
            normalized_quote = " ".join(quote.split()).casefold()
            normalized_source = source.casefold()
            # Document names and their cited instruction must be grounded in the actual clause.
            if " ".join(name.split()).casefold() not in normalized_source or normalized_quote not in normalized_source:
                raise ValueError("A document entry is not explicitly grounded in the source clause")
            clean.append({"document_type": name.strip()[:160], "label": name.strip()[:160],
                          "mandatory": mandatory, "source_quote": quote.strip()[:500],
                          "source_page": source_page, "available": any(
                              name.casefold() in filename.casefold() for filename in
                              [str(d.get("filename") or "") for d in (profile or {}).get("supporting_documents", [])
                               if isinstance(d, dict)])})
        return clean

    mandatory_docs = clean_docs("required_documents", True)
    optional_docs = clean_docs("optional_evidence", False)
    paths = raw.get("conditional_paths", [])
    if not isinstance(paths, list):
        raise ValueError("conditional_paths must be a list")
    clean_paths = []
    for path in paths[:8]:
        if not isinstance(path, dict) or not isinstance(path.get("label"), str) or not path["label"].strip():
            raise ValueError("A conditional path is malformed")
        path_fields = path.get("information_fields", [])
        path_docs = path.get("required_documents", [])
        path_optional_docs = path.get("optional_evidence", [])
        if not isinstance(path_fields, list) or not isinstance(path_docs, list) or not isinstance(path_optional_docs, list):
            raise ValueError("Conditional path fields/documents must be lists")
        validated_path_fields = []
        for field in path_fields[:16]:
            if (not isinstance(field, dict) or not isinstance(field.get("key"), str) or
                    not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", field["key"]) or
                    not isinstance(field.get("label"), str) or
                    field["label"].strip().casefold() in _SECTION2_GENERIC_LABELS):
                raise ValueError("A conditional-path field is malformed or generic")
            existing = matched_profile_value(field.get("existing_value"), field["key"], field["label"])
            validated_path_fields.append({"key": field["key"], "label": field["label"].strip()[:100],
                                          "input_type": field.get("input_type") if field.get("input_type") in
                                          ("text", "number", "date", "select", "textarea", "currency", "boolean") else "text",
                                          "required": bool(field.get("required", True)),
                                          "existing_value": existing, "missing": existing is None})
        validated_path_docs = []
        for doc in path_docs[:12]:
            if not isinstance(doc, dict) or not isinstance(doc.get("document_type"), str) or not isinstance(doc.get("source_quote"), str):
                raise ValueError("A conditional-path document is malformed")
            name, quote = doc["document_type"].strip(), " ".join(doc["source_quote"].split())
            if not name or name.casefold() not in source.casefold() or quote.casefold() not in source.casefold():
                raise ValueError("A conditional-path document is not source grounded")
            validated_path_docs.append({"document_type": name[:160], "label": name[:160], "mandatory": True,
                                        "source_quote": quote[:500], "source_page": source_page,
                                        "available": any(name.casefold() in str(d.get("filename") or "").casefold()
                                                         for d in (profile or {}).get("supporting_documents", [])
                                                         if isinstance(d, dict))})
        validated_path_optional = []
        for doc in path_optional_docs[:12]:
            if not isinstance(doc, dict) or not isinstance(doc.get("document_type"), str) or not isinstance(doc.get("source_quote"), str):
                raise ValueError("A conditional-path optional evidence entry is malformed")
            name, quote = doc["document_type"].strip(), " ".join(doc["source_quote"].split())
            if not name or name.casefold() not in source.casefold() or quote.casefold() not in source.casefold():
                raise ValueError("A conditional-path optional document is not source grounded")
            validated_path_optional.append({"document_type": name[:160], "label": name[:160], "mandatory": False,
                                            "source_quote": quote[:500], "source_page": source_page,
                                            "available": any(name.casefold() in str(d.get("filename") or "").casefold()
                                                             for d in (profile or {}).get("supporting_documents", [])
                                                             if isinstance(d, dict))})
        clean_paths.append({"key": str(path.get("key") or path["label"]).strip()[:80],
                            "label": path["label"].strip()[:120],
                            "information_fields": validated_path_fields,
                            "required_documents": validated_path_docs,
                            "optional_evidence": validated_path_optional})
    if nature == "conditional" and len(clean_paths) < 2:
        raise ValueError("A conditional requirement must include at least two alternative paths.")
    parameters = raw.get("technical_parameters", [])
    if not isinstance(parameters, list):
        raise ValueError("technical_parameters must be a list")
    clean_parameters = []
    for param in parameters[:40]:
        if not isinstance(param, dict) or not isinstance(param.get("parameter"), str) or not param["parameter"].strip():
            raise ValueError("A technical parameter is malformed")
        required = str(param.get("required_specification") or "").strip()
        if not required or required.casefold() not in source.casefold():
            raise ValueError("Technical specification is not grounded in the source clause")
        clean_parameters.append({"parameter": param["parameter"].strip()[:120],
                                 "required_specification": required[:400]})
    return {"status": "analyzed", "title": title.strip()[:120], "description": description.strip()[:300],
            "category": category, "nature": nature, "mandatory": nature == "mandatory",
            "source_quote": source, "source_page": source_page,
            "information_fields": clean_fields, "required_documents": mandatory_docs,
            "optional_evidence": optional_docs, "conditional_paths": clean_paths,
            "technical_parameters": clean_parameters}

def analyze_section2_requirements(requirements: list[dict], chunks: list[dict], profile: dict | None,
                                  model: str | None = None, *, provider_rate_limited: bool = False) -> tuple[list[dict], list[dict]]:
    """Ask Groq for requirement-level UI guidance in small batches; validate each item independently."""
    profile = profile or {}
    errors = []
    usable = []
    for requirement in requirements:
        evidence = requirement.get("retrieved_evidence") or {}
        page = int(requirement.get("source_page") or 0)
        source_chunk = next((chunk for chunk in chunks
                             if chunk.get("chunk_id") == evidence.get("chunk_id")
                             and int(chunk.get("page_number") or 0) == page), None)
        if not source_chunk or requirement.get("source_verified") is not True:
            requirement["section2_analysis"] = {"status": "needs_review", "error": "Source clause/page was not validated."}
            errors.append({"requirement_id": requirement.get("requirement_id"), "error": "Source clause/page was not validated."})
            continue
        usable.append((requirement, source_chunk, page))

    if not (A.GROQ_API_KEY or os.getenv("GROQ_API_KEY")):
        provider_rate_limited = True
        error = "GROQ_API_KEY is not configured in the environment."
    elif provider_rate_limited:
        error = "All configured Groq models were rate-limited during Agent 1; Section 2 requests were skipped to avoid repeating them."
    else:
        error = ""
    if error:
        for requirement, _, page in usable:
            requirement["section2_analysis"] = {"status": "needs_review", "error": error,
                "title": requirement.get("requirement"), "source_quote": requirement.get("source_quotation"),
                "source_page": page, "information_fields": [], "required_documents": [],
                "optional_evidence": [], "conditional_paths": [], "technical_parameters": []}
            errors.append({"requirement_id": requirement.get("requirement_id"), "error": error})
        return requirements, errors

    profile_context = {key: value for key, value in profile.items()
                       if key not in ("supporting_documents", "custom_fields")}
    profile_context["custom_fields"] = profile.get("custom_fields") or []
    profile_context["supporting_documents"] = [
        {"filename": str(doc.get("filename") or ""),
         "verification_status": str(doc.get("verification_status") or "unverified")}
        for doc in profile.get("supporting_documents") or [] if isinstance(doc, dict)]
    system = (
        "Analyze each listed bidder requirement independently for TenderForge Section 2. Use its exact source clause, PDF page, "
        "nearby tender context and saved company profile. Do not turn headings, annexure references, generic instructions or "
        "mere evidence suggestions into obligations. Never combine distinct products or clauses. Technical parameters for one "
        "product are separate rows. Return only fields needed by each clause. Copy a company value only when present verbatim in "
        "the supplied profile; otherwise use null. required_documents must be explicitly required by that clause; optional_evidence "
        "must be expressly optional/desirable. Do not suggest documents. Document names and source_quote, and each technical "
        "required_specification, must be exact substrings of that item's source_clause. Conditional paths are mutually exclusive "
        "and include only their own fields/docs. Return one result for every requirement_id, no others. JSON only: "
        '{"analyses":[{"requirement_id":"","title":"","description":"","category":"Eligibility|Financial|Technical|Experience|Registrations & Certifications|Required Documents|EMD & Payments|Other Bidder Obligations",'
        '"nature":"mandatory|conditional|optional|review","information_fields":[{"key":"","label":"","input_type":"text|number|date|select|textarea|currency|boolean","required":true,"existing_value":null,"missing":true,"options":[]}],'
        '"required_documents":[{"document_type":"exact name","source_quote":"exact quote"}],"optional_evidence":[{"document_type":"exact name","source_quote":"exact quote"}],'
        '"conditional_paths":[{"key":"","label":"","information_fields":[],"required_documents":[],"optional_evidence":[]}],'
        '"technical_parameters":[{"parameter":"","required_specification":"exact quote"}]}]}.'
    )
    batches, pending = [], []
    for item in usable:
        source_length = len(str(item[0].get("source_quotation") or ""))
        if source_length > 1500:
            if pending:
                batches.append(pending)
                pending = []
            batches.append([item])
        else:
            pending.append(item)
            if len(pending) == 3:
                batches.append(pending)
                pending = []
    if pending:
        batches.append(pending)
    provider_rate_limited = False
    for batch in batches:
        if provider_rate_limited:
            error = "Skipped after all configured Groq models were rate-limited; no repeat request was sent."
            for requirement, _, page in batch:
                requirement["section2_analysis"] = {"status": "needs_review", "error": error,
                    "title": requirement.get("requirement"), "source_quote": requirement.get("source_quotation"),
                    "source_page": page, "information_fields": [], "required_documents": [],
                    "optional_evidence": [], "conditional_paths": [], "technical_parameters": []}
                errors.append({"requirement_id": requirement.get("requirement_id"), "error": error})
            continue
        prompts = []
        for requirement, source_chunk, page in batch:
            neighbors = [chunk for chunk in chunks if chunk.get("chunk_id") != source_chunk.get("chunk_id")
                         and abs(int(chunk.get("page_number") or 0) - page) <= 1]
            neighbors.sort(key=lambda chunk: abs(int(chunk.get("page_number") or 0) - page))
            context_chunks = [source_chunk, *neighbors[:2]]
            context = "\n\n".join(
                f"[PDF page {int(chunk.get('page_number') or 0)}] {str(chunk.get('text') or '')[:1100]}"
                for chunk in context_chunks)[:3300]
            prompts.append({"requirement_id": requirement.get("requirement_id"),
                "title": requirement.get("requirement"), "category": requirement.get("category"),
                "source_clause": requirement.get("source_quotation"), "source_page": page,
                "source_pdf_context": context})
        try:
            raw, used_model = A.call_groq_modular(
                [{"role": "system", "content": system},
                 {"role": "user", "content": json.dumps({"requirements": prompts,
                     "saved_company_profile": profile_context}, ensure_ascii=False)}],
                max_tokens=2200, preferred_model=model or GROQ_MODEL)
            rows = raw.get("analyses") if isinstance(raw, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Groq Section 2 batch omitted the analyses array.")
            by_id, duplicate_ids = {}, set()
            for row in rows:
                if not isinstance(row, dict) or not row.get("requirement_id"):
                    raise ValueError("Groq Section 2 batch contained a malformed result.")
                rid = str(row["requirement_id"])
                if rid in by_id:
                    duplicate_ids.add(rid)
                else:
                    by_id[rid] = row
            for requirement, _, page in batch:
                rid = str(requirement.get("requirement_id") or "")
                row = by_id.get(rid)
                try:
                    if not row or rid in duplicate_ids:
                        raise ValueError(f"Groq Section 2 batch omitted or duplicated {rid}.")
                    analysis = _validate_section2_analysis(row, requirement, profile)
                    analysis["model"] = used_model
                    requirement["section2_analysis"] = analysis
                except Exception as exc:
                    error = str(exc)[:300]
                    requirement["section2_analysis"] = {"status": "needs_review", "error": error,
                        "title": requirement.get("requirement"), "source_quote": requirement.get("source_quotation"),
                        "source_page": page, "information_fields": [], "required_documents": [],
                        "optional_evidence": [], "conditional_paths": [], "technical_parameters": []}
                    errors.append({"requirement_id": rid, "error": error})
        except Exception as exc:
            error = str(exc)[:300]
            provider_rate_limited = "all configured groq models were rate-limited" in str(exc).casefold()
            for requirement, _, page in batch:
                requirement["section2_analysis"] = {"status": "needs_review", "error": error,
                    "title": requirement.get("requirement"), "source_quote": requirement.get("source_quotation"),
                    "source_page": page, "information_fields": [], "required_documents": [],
                    "optional_evidence": [], "conditional_paths": [], "technical_parameters": []}
                errors.append({"requirement_id": requirement.get("requirement_id"), "error": error})
    return requirements, errors


def _normal(value) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _concept_label(concept_key: str | None, fallback: str) -> str:
    concept = PF.concept_for_key(concept_key) if concept_key else None
    return concept["label"] if concept else str(fallback or "")


def _latest_documents(profile: dict) -> list[dict]:
    latest = {}
    for doc in (profile.get("supporting_documents") or []):
        if not isinstance(doc, dict):
            continue
        key = str(doc.get("document_key") or doc.get("id"))
        if key not in latest or int(doc.get("version") or 0) > int(latest[key].get("version") or 0):
            latest[key] = doc
    return list(latest.values())


def _document_concept(document_key) -> str | None:
    key = str(document_key or "")
    return key[len("field:"):] if key.startswith("field:") else None


def collect_company_evidence(profile: dict | None) -> list[dict]:
    """
    Build user-scoped evidence facts with their persisted provenance and verification status.
    Each fact keeps its own verification_status; nothing here promotes a fact to verified. Facts from
    Section 3 fields carry their stable concept key and label so they can be matched to requirements.
    Values extracted from a document version that was rejected are excluded; uploaded documents without
    extracted values are not facts (see collect_unprocessed_documents).
    """
    profile = profile or {}
    facts = []
    docs_by_id = {d.get("id"): d for d in (profile.get("supporting_documents") or []) if isinstance(d, dict)}
    for key in _PROFILE_EVIDENCE_FIELDS:
        value = str(profile.get(key) or "").strip()
        if value:
            concept_key = PF.fixed_column_concept(key)
            facts.append({"evidence_id": f"profile:{key}", "field_key": key, "value_text": value,
                          "source": "fixed_company_profile", "verification_status": "unverified",
                          "source_excerpt": "", "source_page": None,
                          "concept_key": concept_key, "field_label": _concept_label(concept_key, key)})

    for row in profile.get("custom_fields") or []:
        if not isinstance(row, dict) or not str(row.get("field_value") or "").strip():
            continue
        concept_key = row.get("field_key") if PF.concept_for_key(row.get("field_key")) else None
        linked = docs_by_id.get(row.get("source_document_id")) or {}
        facts.append({"evidence_id": f"profile_field:{row.get('field_key')}",
                      "field_key": row.get("field_key"), "value_text": str(row.get("field_value")),
                      "source": row.get("source") or "user_entered",
                      "source_document_id": row.get("source_document_id"),
                      "source_document_version": linked.get("version"),
                      "source_document_filename": linked.get("filename"),
                      "source_excerpt": str(row.get("source_excerpt") or ""),
                      "source_page": row.get("source_page"),
                      "verification_status": row.get("verification_status") or "unverified",
                      "concept_key": concept_key, "field_label": _concept_label(concept_key, row.get("field_key"))})

    for doc in _latest_documents(profile):
        if str(doc.get("verification_status") or "unverified") == "rejected":
            continue  # values taken from a rejected document version are not company evidence
        concept_key = _document_concept(doc.get("document_key"))
        metadata = {"document_id": doc.get("id"), "document_key": doc.get("document_key"),
                    "document_version": doc.get("version"), "filename": doc.get("filename"),
                    "document_sha256": doc.get("sha256"),
                    "document_verification_status": doc.get("verification_status") or "unverified"}
        for value in doc.get("extracted_values") or []:
            if not isinstance(value, dict) or not str(value.get("value_text") or "").strip():
                continue
            facts.append({"evidence_id": f"company_document_value:{value.get('id')}",
                          "field_key": value.get("value_key"), "value_text": str(value.get("value_text")),
                          "source": "company_supporting_document", **metadata,
                          "source_excerpt": str(value.get("source_excerpt") or ""),
                          "source_page": value.get("source_page"),
                          "verification_status": value.get("verification_status") or "unverified",
                          # How a 'verified' value was established: 'document_excerpt_check' (in-app check of the
                          # user's upload) or None (set through the API without an in-app review record).
                          "review_method": value.get("review_method"),
                          "concept_key": concept_key, "field_label": _concept_label(concept_key, value.get("value_key"))})
    return facts


def collect_unprocessed_documents(profile: dict | None) -> list[dict]:
    """Latest document versions with no extracted values: they are listed, never treated as proof."""
    return [{"document_id": d.get("id"), "document_key": d.get("document_key"), "document_version": d.get("version"),
             "filename": d.get("filename"), "verification_status": d.get("verification_status") or "unverified",
             "concept_key": _document_concept(d.get("document_key")), "content_extracted": False}
            for d in _latest_documents(profile or {})
            if not any(str((v or {}).get("value_text") or "").strip() for v in d.get("extracted_values") or [])]


def relevant_company_evidence(requirement: dict, facts: list[dict], documents: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """
    Evidence relevant to one requirement (not every profile field is relevant to every tender):
      * Section 3 facts / documents match by stable concept key (financial.annual_turnover_by_year, certification.iso_9001 ...);
      * other facts match by the profile area the requirement maps to (company name never counts).
    """
    keys = PF.concept_keys_for_requirement(requirement)
    text = " ".join(str(requirement.get(k) or "") for k in ("requirement", "source_quotation", "required_document"))
    target = A.map_profile_field(text)
    matched = []
    for fact in facts:
        if fact.get("concept_key"):
            if fact["concept_key"] in keys:
                matched.append(fact)
        elif target != "company_name" and A.map_profile_field(
                " ".join(str(fact.get(k) or "") for k in ("field_key", "value_text"))) == target:
            matched.append(fact)
    docs = [d for d in (documents or []) if d.get("concept_key") and d["concept_key"] in keys]
    return matched, docs


def _verified_profile(profile: dict | None, facts: list[dict]) -> dict:
    """Create the deterministic evaluator's view from verified facts only."""
    result = copy.deepcopy(profile or {})
    for key in _PROFILE_EVIDENCE_FIELDS:
        result[key] = ""
    result["_agent_company_evidence"] = [f for f in facts if f.get("verification_status") == "verified"]
    for fact in result["_agent_company_evidence"]:
        if fact.get("concept_key"):
            target = PF.concept_profile_target(fact["concept_key"])
        else:
            target = A.map_profile_field(" ".join(str(fact.get(k) or "") for k in ("field_key", "value_text")))
        if target not in _PROFILE_EVIDENCE_FIELDS:
            continue
        value = str(fact.get("value_text") or "").strip()
        label = str(fact.get("field_label") or "")
        if fact.get("concept_key") and target == "certifications" and label and label.lower() not in value.lower():
            value = f"{label}: {value}"  # keep the standard / registration name with its certificate details
        if value and value not in str(result.get(target) or "").splitlines():
            result[target] = "\n".join(filter(None, (str(result.get(target) or "").strip(), value)))
    return result


def _chunk_refs(requirement: dict) -> list[dict]:
    evidence = requirement.get("retrieved_evidence") or {}
    if not evidence.get("chunk_id"):
        return []
    return [{"chunk_id": evidence["chunk_id"], "page_number": evidence.get("page_number")}]


def _validate_agent1_proposal(proposal: dict, chunks: list[dict], existing_ids: set[str]) -> dict | None:
    if not isinstance(proposal, dict) or not str(proposal.get("requirement") or "").strip():
        return None
    page = proposal.get("source_page", proposal.get("page_number"))
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    quote = str(proposal.get("source_quotation") or proposal.get("tender_evidence") or "").strip()
    if not quote:
        return None
    requested_id = str(proposal.get("source_chunk_id") or proposal.get("chunk_id") or "").strip()
    if not requested_id:
        return None
    candidates = [c for c in chunks if c.get("page_number") == page and c.get("chunk_id") == requested_id]
    source_chunk = next((c for c in candidates if A._source_evidence_matches(quote, c["text"])), None)
    if not source_chunk:
        return None
    quote_tokens = re.findall(r"\w+", quote)
    exact_match = re.search(r"\W+".join(re.escape(token) for token in quote_tokens), source_chunk["text"], re.IGNORECASE) if quote_tokens else None
    if not exact_match:
        return None
    exact_quote = source_chunk["text"][exact_match.start():exact_match.end()]
    page_context = f"--- [Page {page}] ---\n{source_chunk['text']}"
    verified = A._verified_llm_requirement({**proposal, "source_quotation": exact_quote}, page_context, existing_ids)
    if not verified:
        return None
    verified["source_quotation"] = exact_quote
    verified["tender_evidence"] = f"Page {page}: {exact_quote[:300]}"
    suggested_category = proposal.get("category") if proposal.get("category") in A.REQUIREMENT_CATEGORIES else "General"
    verified["category"] = A.classify_requirement_category(
        f"{proposal.get('requirement', '')} {exact_quote}", suggested_category)
    verified["requirement_scope"] = "offered_product" if (
        verified["category"] == "Technical Specification" or
        A.is_offered_product_requirement(f"{proposal.get('requirement', '')} {exact_quote}")) else "company"
    A.classify_requirement_metadata(verified)
    verified["retrieved_evidence"] = {"chunk_id": source_chunk["chunk_id"], "page_number": page,
                                      "source_excerpt": source_chunk["text"][:1200]}
    verified["retrieval_grounded"] = True
    verified["source_verified"] = True
    verified["agent1_validation"] = "validated"
    return verified


def run_agent1_requirement_extraction(tender_text: str, retrieved_chunks: list[dict],
                                      company_evidence: list[dict], *, profile: dict | None = None,
                                      pdf_path: str | None = None,
                                      custom_requirements: list | None = None,
                                      retrieval_metadata: dict | None = None,
                                      model: str | None = None) -> dict:
    """Agent 1 contract: retrieved tender chunks + saved company evidence -> cited requirements."""
    valid_chunks = A._validated_retrieved_chunks(tender_text, retrieved_chunks)
    valid_ids = {c["chunk_id"] for c in valid_chunks}
    if not valid_chunks:
        return {"agent": AGENT_ORDER[0], "status": "failed", "error": "No retrieved tender chunk passed page/text validation.",
                "requirements": [], "evidence_refs": [], "fallback_used": False}
    if custom_requirements:
        requirements = A.normalize_custom_requirements(custom_requirements)
        report = {"notes": ["User-curated requirement list."], "incomplete": False,
                  "count": len(requirements), "page_count": len(A.split_pages(tender_text))}
        report["retrieval"] = A._attach_retrieval_evidence(requirements, tender_text, valid_chunks)
        if report["retrieval"]["unverified_source_count"] or report["retrieval"]["not_retrieved_count"]:
            report["incomplete"] = True
    else:
        requirements, report = A.extract_requirements_with_report(
            tender_text, profile, pdf_path=pdf_path, retrieved_chunks=valid_chunks)

    context, _ = A._format_retrieved_chunks(valid_chunks)
    prompt_evidence = [{k: f.get(k) for k in ("evidence_id", "field_key", "value_text", "source",
                                                   "source_excerpt", "source_page", "verification_status",
                                                   "document_id", "document_version", "filename")}
                       for f in company_evidence[:60]]
    error = ""
    used_model = None
    requested_model = model or GROQ_MODEL
    proposals = []
    try:
        response, used_model = A.call_groq_modular(
            [{"role": "system", "content": (
                "You are Agent 1 — Requirement Extraction & Eligibility. Extract only actual tender requirements from the supplied retrieved chunks. "
                "Classify mandatory/conditional and financial, technical, eligibility, and document requirements. Company evidence is context only; "
                "do not convert company facts into tender requirements. For every item return requirement_id, category, requirement, is_mandatory, "
                "source_quotation (verbatim), source_page, source_chunk_id, required_document, and requirement_type. Do not infer facts absent from the chunks. "
                "Return the requirements key even when there are no additional requirements; in that case use an empty array. Keep each item concise. "
                # Bounded output: re-listing every requirement truncates the response on real tenders.
                "Return ONLY requirements that are not already covered by the existing deterministic requirement IDs listed below, "
                "at most 8 items, each source_quotation at most 40 words copied verbatim. "
                "Return JSON only; do not add prose or Markdown. "
                'Return JSON: {"requirements": [{"requirement_id":"...","category":"Eligibility|Technical|Technical Specification|Financial|Certifications|Documents|General",'
                '"requirement":"...","is_mandatory":true,"is_conditional":false,"source_quotation":"exact quote",'
                '"source_page":1,"source_chunk_id":"exact retrieved chunk ID","required_document":"...","requirement_type":"..."}]}.' )},
             {"role": "user", "content": "RETRIEVED TENDER EVIDENCE:\n" + context +
              "\n\nSAVED COMPANY EVIDENCE (context only):\n" + json.dumps(prompt_evidence, ensure_ascii=False) +
              "\n\nExisting deterministic requirement IDs (avoid duplicates):\n" +
              json.dumps([r.get("requirement_id") for r in requirements])}],
            max_tokens=1800, preferred_model=requested_model)
        if isinstance(response, dict):
            if "requirements" in response:
                proposals = response["requirements"]
            elif "structured_requirements" in response:
                proposals = response["structured_requirements"]
            else:
                raise ValueError("Agent 1 response omitted the requirements list.")
            if not isinstance(proposals, list):
                raise ValueError("Agent 1 response did not contain a requirements list.")
        else:
            raise ValueError("Agent 1 response was not a JSON object.")
    except Exception as exc:
        error = str(exc)[:500]

    existing_ids = {str(r.get("requirement_id")) for r in requirements}
    accepted, rejected = [], 0
    if not error:
        for proposal in proposals[:80]:
            item = _validate_agent1_proposal(proposal, valid_chunks, existing_ids)
            if not item:
                rejected += 1
                continue
            existing_ids.add(item["requirement_id"])
            requirements.append(item)
            accepted.append(item["requirement_id"])
    # One canonical record per tender obligation; merged IDs and quotes stay traceable on the canonical item.
    requirements, merges = A.deduplicate_requirements(requirements)
    merged_ids = {m["merged"] for m in merges}
    accepted = [rid for rid in accepted if rid not in merged_ids]
    if merges:
        report.setdefault("notes", []).append(
            f"Merged {len(merges)} duplicate requirement(s) into their canonical clause: "
            + ", ".join(f"{m['merged']} → {m['kept']}" for m in merges))

    if requirements:
        A._attach_retrieval_evidence(requirements, tender_text, valid_chunks)
    missing_refs = []
    for row in requirements:
        evidence = row.get("retrieved_evidence") or {}
        if evidence.get("chunk_id") not in valid_ids or evidence.get("page_number") != row.get("source_page"):
            row["agent1_validation"] = "source_verified_not_retrieved" if row.get("source_verified") else "unverified_source"
            missing_refs.append(row.get("requirement_id"))
        else:
            row["agent1_validation"] = "validated"
    heading_only = [row for row in requirements if not custom_requirements and _is_heading_only_requirement(row)]
    if heading_only:
        requirements = [row for row in requirements if row not in heading_only]
        report.setdefault("notes", []).append(
            f"Ignored {len(heading_only)} standalone heading-only extraction(s); no bidder action was stated.")
    requirements, section2_errors = analyze_section2_requirements(
        requirements, valid_chunks, profile, model=requested_model,
        provider_rate_limited="all configured groq models were rate-limited" in error.casefold())
    if section2_errors:
        report.setdefault("notes", []).append(
            f"Section 2 Groq analysis needs review for {len(section2_errors)} requirement(s); source requirements were retained.")
    retrieval = report.get("retrieval") or {}
    report["count"] = len(requirements)
    report["incomplete"] = bool(report.get("incomplete") or missing_refs)
    if missing_refs:
        report.setdefault("notes", []).append(
            f"{len(missing_refs)} requirement(s) lack a validated retrieved chunk citation and remain uncertain.")
    if not requirements:
        report["incomplete"] = True
        report.setdefault("notes", []).append("No requirements were extracted; a bid decision cannot be supported.")
    if rejected:
        report.setdefault("notes", []).append(f"Discarded {rejected} Agent 1 proposal(s) with invalid quote/page/chunk citations.")
    if retrieval_metadata and retrieval_metadata.get("queries_without_matches", 0):
        report["incomplete"] = True
        report.setdefault("notes", []).append(
            f"{retrieval_metadata['queries_without_matches']} retrieval query/queries returned no relevant chunks; extraction coverage is uncertain.")
    status = "complete" if not error and not report["incomplete"] else "partial"
    if error and not requirements:
        status = "failed"
    partial_reasons = []
    if error:
        partial_reasons.append(f"Groq requirement extraction response was unusable: {error}")
    if report.get("incomplete"):
        partial_reasons.extend(str(note) for note in report.get("notes", []) if str(note).strip())
    return {"agent": AGENT_ORDER[0], "contract_version": AGENT_CONTRACT_VERSION, "status": status,
            "error": error, "fallback_used": bool(error), "model": used_model,
            "requested_model": requested_model,
            "model_fallback_used": bool(used_model and used_model != requested_model),
            "partial_reasons": list(dict.fromkeys(partial_reasons)),
            "requirements": requirements, "extraction_report": report,
            "section2_analysis_errors": section2_errors,
            "accepted_llm_requirement_ids": accepted, "rejected_proposal_count": rejected,
            "merged_duplicates": merges,
            "evidence_refs": [{"chunk_id": c["chunk_id"], "page_number": c["page_number"]} for c in valid_chunks],
            "unresolved_requirement_ids": missing_refs,
            "profile_evidence_ids_consumed": [f["evidence_id"] for f in prompt_evidence],
            "retrieval_coverage": {"query_count": (retrieval_metadata or {}).get("query_count"),
                                   "queries_without_matches": (retrieval_metadata or {}).get("queries_without_matches", 0)}}


def _verified_evidence_for_field(requirement: dict, facts: list[dict]) -> list[dict]:
    text = " ".join(str(requirement.get(k) or "") for k in ("requirement", "source_quotation", "required_document"))
    target = A.map_profile_field(text)
    if target == "company_name":
        return []
    matched = []
    for fact in facts:
        if fact.get("verification_status") != "verified":
            continue
        fact_target = A.map_profile_field(" ".join(str(fact.get(k) or "") for k in ("field_key", "value_text")))
        if fact_target == target:
            matched.append(fact)
    return matched


def _valid_agent1_requirement(requirement: dict, chunk_refs: dict[str, int]) -> bool:
    evidence = requirement.get("retrieved_evidence") or {}
    chunk_id = str(evidence.get("chunk_id") or "")
    try:
        page = int(evidence.get("page_number"))
        source_page = int(requirement.get("source_page"))
    except (TypeError, ValueError):
        return False
    excerpt = str(evidence.get("source_excerpt") or "")
    quote = str(requirement.get("source_quotation") or "")
    return bool(requirement.get("requirement_id") and requirement.get("source_verified") is True and
                requirement.get("retrieval_grounded") is True and requirement.get("agent1_validation") == "validated" and
                chunk_id in chunk_refs and chunk_refs[chunk_id] == page == source_page and excerpt and
                A._source_evidence_matches(quote, excerpt))


def _validate_agent3_citations(citations, tender_chunk_pages: dict[str, int],
                               company_evidence_ids: set[str]) -> tuple[list[str] | None, str | None]:
    """Normalize Agent 3 citations to IDs, accepting only exact upstream evidence references.

    The LLM may echo the structured references it was shown, or use the explicit ID-only
    schema. Structured references are accepted only when both the ID and page match the
    validated Agent 1/2 outputs.
    """
    if not isinstance(citations, list):
        return None, "Agent 3 evidence_references must be a list."
    if not citations:
        return None, "Agent 3 report omitted evidence references."

    allowed_ids = set(tender_chunk_pages) | set(company_evidence_ids)
    normalized = []
    for citation in citations:
        if isinstance(citation, str):
            citation_id = citation
            valid = citation_id in allowed_ids
        elif isinstance(citation, dict) and set(citation) == {"chunk_id", "page_number"}:
            citation_id = citation.get("chunk_id")
            page = citation.get("page_number")
            valid = (isinstance(citation_id, str) and type(page) is int and
                     tender_chunk_pages.get(citation_id) == page)
        elif isinstance(citation, dict) and set(citation) == {"evidence_id"}:
            citation_id = citation.get("evidence_id")
            valid = isinstance(citation_id, str) and citation_id in company_evidence_ids
        else:
            return None, "Agent 3 returned a malformed evidence citation."
        if not valid:
            return None, "Agent 3 cited an evidence reference that does not match validated Agent 1/2 output."
        normalized.append(citation_id)
    return list(dict.fromkeys(normalized)), None


def _quote_in_evidence(quote: str, facts: list[dict]) -> list[dict]:
    needle = _normal(quote)
    if len(needle) < 4:
        return []
    return [f for f in facts if needle in _normal(f.get("value_text")) or needle in _normal(f.get("source_excerpt"))]


_CANDIDATE_KEYS = ("evidence_id", "field_key", "field_label", "concept_key", "value_text", "source", "verification_status",
                   "review_method",
                   "source_excerpt", "source_page", "document_id", "document_version", "filename",
                   "source_document_id", "source_document_version", "source_document_filename")


def _confirmation_needed(status: str, candidates: list[dict], documents: list[dict]) -> str:
    """Exactly which confirmation is missing before saved company information can count for a requirement."""
    if status in (A.SATISFIED, A.NOT_APPLICABLE):
        return ""
    parts = []
    for fact in candidates:
        if fact.get("verification_status") == "verified":
            continue
        where = fact.get("filename") or fact.get("source_document_filename")
        version = fact.get("document_version") or fact.get("source_document_version")
        origin = f"from {where} v{version}" if where else str(fact.get("source") or "profile").replace("_", " ")
        state = "was rejected and cannot be used" if fact.get("verification_status") == "rejected" else \
            "is saved but unverified; it must be verified before it can count as evidence"
        parts.append(f"{fact.get('field_label') or fact.get('field_key')} = '{str(fact.get('value_text'))[:80]}' ({origin}) {state}")
    for doc in documents:
        parts.append(f"document '{doc.get('filename')}' v{doc.get('document_version')} ({doc.get('verification_status')}) was "
                     "uploaded, but its contents have not been extracted or validated, so it does not prove this requirement")
    if not parts and not any(f.get("verification_status") == "verified" for f in candidates):
        return "No saved company information matches this requirement."
    return "; ".join(parts) + ("." if parts else "")


def _attach_candidate_evidence(result: dict, candidates: list[dict], documents: list[dict]) -> None:
    """Record relevant saved company information and what confirmation it still needs (status is not changed here)."""
    result["candidate_company_evidence"] = [{k: f.get(k) for k in _CANDIDATE_KEYS} for f in candidates]
    result["unprocessed_company_documents"] = [dict(d) for d in documents]
    needed = _confirmation_needed(A.normalize_status(result.get("status")), candidates, documents)
    result["evidence_confirmation_needed"] = needed
    if needed and (candidates or documents) and A.normalize_status(result.get("status")) in (A.INFORMATION_REQUIRED, A.UNRESOLVED):
        result["explanation"] = f"{result.get('explanation', '').rstrip()} Saved company information: {needed}".strip()


def run_agent2_compliance_risk(agent1_output: dict, company_evidence: list[dict], *,
                               profile: dict | None = None, model: str | None = None,
                               batch_size: int = 3, documents: list[dict] | None = None, progress=None) -> dict:
    """Agent 2 contract: Agent 1's cited requirements + scoped company evidence -> validated compliance matrix."""
    if not isinstance(agent1_output, dict) or agent1_output.get("contract_version") != AGENT_CONTRACT_VERSION \
            or not isinstance(agent1_output.get("requirements"), list):
        return {"agent": AGENT_ORDER[1], "status": "failed", "error": "Agent 1 output contract is invalid.",
                "compliance_matrix": [], "fallback_used": False, "evidence_refs": []}
    facts_by_id = {str(f.get("evidence_id")): f for f in company_evidence if f.get("evidence_id")}
    verified_profile = _verified_profile(profile, company_evidence)
    requirements = agent1_output["requirements"]
    chunk_refs = {str(ref.get("chunk_id")): int(ref.get("page_number"))
                  for ref in agent1_output.get("evidence_refs", [])
                  if isinstance(ref, dict) and ref.get("chunk_id") and ref.get("page_number")}
    seen_requirement_ids = set()
    valid_requirements = []
    for req in requirements:
        rid = str(req.get("requirement_id") or "") if isinstance(req, dict) else ""
        if rid and rid not in seen_requirement_ids and _valid_agent1_requirement(req, chunk_refs):
            valid_requirements.append(req)
            seen_requirement_ids.add(rid)
    grounded = valid_requirements
    uncertain = [r for r in requirements if r not in grounded]
    rules = {r["requirement_id"]: A.evaluate_evidence_matching(r, verified_profile) for r in grounded}
    relevance = {str(r.get("requirement_id")): relevant_company_evidence(r, company_evidence, documents)
                 for r in requirements if isinstance(r, dict)}
    llm_evaluations, models, errors, batch_results, seen = {}, [], [], [], 0
    provider_rate_limited = False
    requested_model = model or GROQ_MODEL
    batches = [grounded[i:i + max(1, batch_size)] for i in range(0, len(grounded), max(1, batch_size))]
    for batch_number, batch in enumerate(batches, 1):
        if progress:
            progress("agent2", f"Agent 2: evaluating compliance, batch {batch_number} of {len(batches)} "
                               f"({len(batch)} requirements)")
        if provider_rate_limited:
            error = "Skipped after all configured Groq models were rate-limited; no repeat request was sent."
            errors.append({"batch": batch_number, "requirement_ids": [r["requirement_id"] for r in batch], "error": error})
            batch_results.append({"batch": batch_number, "ok": False,
                                  "requirement_ids": [r["requirement_id"] for r in batch], "error": error})
            continue
        evidence_payload = [{k: f.get(k) for k in ("evidence_id", "field_key", "value_text", "source",
                                                    "source_excerpt", "source_page", "verification_status",
                                                    "document_id", "document_version", "filename")}
                            for f in company_evidence[:80]]
        requirement_payload = [{"requirement_id": r["requirement_id"], "category": r.get("category"),
                                "requirement_type": r.get("requirement_type"), "requirement": r.get("requirement"),
                                "is_mandatory": r.get("is_mandatory"), "is_conditional": r.get("is_conditional"),
                                "tender_evidence": r.get("tender_evidence"), "source_page": r.get("source_page"),
                                "retrieved_evidence": {k: (r.get("retrieved_evidence") or {}).get(k)
                                                       for k in ("chunk_id", "page_number")},
                                "requirement_scope": r.get("requirement_scope"),
                                "deterministic_rule_status": rules[r["requirement_id"]].get("status"),
                                "relevant_company_evidence_ids": [f["evidence_id"] for f in relevance[r["requirement_id"]][0]]}
                               for r in batch]
        try:
            response, used_model = A.call_groq_modular(
                [{"role": "system", "content": (
                    "You are Agent 2 — Compliance & Risk Analysis. Evaluate only the supplied Agent 1 requirements. "
                    "Use the saved company evidence and cite evidence IDs and exact company_evidence_quote. "
                    "An unverified declaration is not proof of compliance. Missing or unverified support is INFORMATION REQUIRED, "
                    "never a confirmed gap. A confirmed gap requires verified company evidence. Product specifications cannot be proven by company claims. "
                "Return one evaluation for every listed requirement ID exactly once. Keep explanations to one concise sentence and risks to one short sentence. "
                "Use empty evidence ID arrays and empty strings when no evidence or risk applies. Return JSON only; do not add prose or Markdown. "
                    'Return JSON: {"evaluations":[{"requirement_id":"...","status":"SATISFIED|CONFIRMED GAP|INFORMATION REQUIRED|UNRESOLVED|NOT APPLICABLE",'
                    '"explanation":"...","company_evidence_ids":[],"company_evidence_quote":"","risk":"","risk_evidence_ids":[]}]}.' )},
                 {"role": "user", "content": "VALIDATED AGENT 1 OUTPUT:\n" + json.dumps(requirement_payload, ensure_ascii=False) +
                  "\n\nSAVED COMPANY EVIDENCE (with source and verification status):\n" +
                  json.dumps(evidence_payload, ensure_ascii=False) +
                  f"\n\nBatch {batch_number}: evaluate each listed requirement ID exactly once."}],
                max_tokens=1600, preferred_model=requested_model)
            if not isinstance(response, dict) or not isinstance(response.get("evaluations"), list):
                raise ValueError("Agent 2 response did not contain an evaluations list.")
            if used_model and used_model not in models:
                models.append(used_model)
            allowed_ids = {r["requirement_id"] for r in batch}
            allowed_tender_ids = {str(ref.get("chunk_id")) for r in batch for ref in _chunk_refs(r)}
            allowed_company_ids = set(facts_by_id)
            accepted_batch_ids = set()
            batch_issues = []
            for evaluation in response["evaluations"]:
                if not isinstance(evaluation, dict):
                    batch_issues.append("response contained a non-object evaluation")
                    continue
                rid = str(evaluation.get("requirement_id") or "")
                if rid not in allowed_ids:
                    batch_issues.append(f"response contained an unknown requirement ID {rid or '<missing>'}")
                    continue
                if rid in accepted_batch_ids or rid in llm_evaluations:
                    batch_issues.append(f"response duplicated requirement ID {rid}")
                    continue
                status = str(evaluation.get("status") or "").strip().upper()
                if status not in A.STATUSES:
                    batch_issues.append(f"response contained an invalid status for {rid}")
                    continue
                company_ids = evaluation.get("company_evidence_ids", [])
                risk_ids_raw = evaluation.get("risk_evidence_ids", [])
                quote_raw = evaluation.get("company_evidence_quote", "")
                risk_raw = evaluation.get("risk", "")
                if (not isinstance(company_ids, list) or not all(isinstance(i, str) for i in company_ids) or
                        not isinstance(risk_ids_raw, list) or not all(isinstance(i, str) for i in risk_ids_raw) or
                        not isinstance(quote_raw, str) or not isinstance(risk_raw, str)):
                    batch_issues.append(f"response contained malformed evidence fields for {rid}")
                    continue
                quote = quote_raw.strip()
                cited = [i for i in company_ids if i in facts_by_id]
                matched = _quote_in_evidence(quote, [facts_by_id[i] for i in cited])
                evaluation = dict(evaluation)
                evaluation["status"] = status
                evaluation["_validated_company_evidence_ids"] = [f["evidence_id"] for f in matched]
                if quote and not matched:
                    evaluation["company_evidence_quote"] = ""
                    evaluation["_invalid_company_quote"] = True
                risk_ids = [i for i in risk_ids_raw if i in allowed_tender_ids or i in allowed_company_ids]
                evaluation["_validated_risk_evidence_ids"] = risk_ids
                if evaluation.get("risk") and not risk_ids:
                    evaluation["risk"] = ""
                llm_evaluations[rid] = evaluation
                accepted_batch_ids.add(rid)
            missing_batch_ids = sorted(allowed_ids - accepted_batch_ids)
            if missing_batch_ids:
                batch_issues.append("response omitted evaluations for " + ", ".join(missing_batch_ids))
            seen += len(accepted_batch_ids)
            if batch_issues:
                batch_error = "; ".join(batch_issues)[:500]
                errors.append({"batch": batch_number, "requirement_ids": sorted(allowed_ids), "error": batch_error})
                batch_results.append({"batch": batch_number, "ok": False, "requirement_ids": sorted(allowed_ids),
                                      "model": used_model, "evaluation_count": len(accepted_batch_ids),
                                      "error": batch_error})
            else:
                batch_results.append({"batch": batch_number, "ok": True, "requirement_ids": sorted(allowed_ids),
                                      "model": used_model, "evaluation_count": len(accepted_batch_ids)})
        except Exception as exc:
            error = str(exc)[:400]
            provider_rate_limited = "all configured groq models were rate-limited" in str(exc).casefold()
            errors.append({"batch": batch_number, "requirement_ids": [r["requirement_id"] for r in batch], "error": error})
            batch_results.append({"batch": batch_number, "ok": False,
                                  "requirement_ids": [r["requirement_id"] for r in batch], "error": error})

    matrix = []
    for requirement in requirements:
        rid = requirement.get("requirement_id")
        if rid in rules:
            baseline = rules[rid]
            evaluation = llm_evaluations.get(rid)
            result = A.reconcile_requirement(baseline, evaluation, verified_profile)
            candidates, docs = relevance.get(str(rid), ([], []))
            verified_ids = list(dict.fromkeys(
                [f["evidence_id"] for f in candidates if f.get("verification_status") == "verified"] +
                [f["evidence_id"] for f in _verified_evidence_for_field(requirement, company_evidence)
                 if not f.get("concept_key")]))
            cited_ids = (evaluation or {}).get("_validated_company_evidence_ids", [])
            # Only verified facts can support compliance; an LLM-cited unverified fact is a candidate, not proof.
            verified_cited = [i for i in cited_ids if facts_by_id.get(i, {}).get("verification_status") == "verified"]
            company_refs = list(dict.fromkeys(verified_ids + verified_cited))
            if result["status"] == A.SATISFIED and not company_refs:
                result.update(status=A.INFORMATION_REQUIRED, explanation=(
                    "The analysis found a possible match, but no verified company evidence supports compliance. "
                    "Provide a verified supporting document or confirm the evidence."))
            if result["status"] == A.CONFIRMED_GAP and not verified_ids:
                result.update(status=A.UNRESOLVED, explanation=(
                    "A potential gap was identified from unverified or missing company data; it is not a confirmed failure."))
            if evaluation and evaluation.get("_invalid_company_quote"):
                result["company_evidence_validation"] = "rejected_unverifiable_quote"
            result["company_evidence_refs"] = company_refs
            result["company_evidence_provenance"] = [facts_by_id[i] for i in company_refs if i in facts_by_id]
            result["tender_evidence_refs"] = _chunk_refs(requirement)
            result["section2_analysis"] = requirement.get("section2_analysis")
            _attach_candidate_evidence(result, candidates, docs)
            if evaluation and evaluation.get("risk") and evaluation.get("_validated_risk_evidence_ids"):
                result["cited_risk"] = str(evaluation["risk"])[:500]
                result["risk_evidence_refs"] = [
                    ({"chunk_id": ref_id, "page_number": next(
                        (ref.get("page_number") for ref in _chunk_refs(requirement) if ref.get("chunk_id") == ref_id), None)}
                     if ref_id not in facts_by_id else {"evidence_id": ref_id})
                    for ref_id in evaluation["_validated_risk_evidence_ids"]]
            if not evaluation:
                result["evaluation_method"] = "deterministic rule fallback (Agent 2 unavailable or omitted this requirement)"
        else:
            result = {**requirement, "status": A.INFORMATION_REQUIRED, "rule_status": A.INFORMATION_REQUIRED,
                      "rule_decisive": False, "requirement_kind": A.requirement_kind(requirement),
                      "company_evidence": A.NOT_FOUND,
                      "explanation": "This tender requirement has no validated retrieved chunk citation; compliance is uncertain and needs review.",
                      "evaluation_method": "not evaluated (Agent 1 evidence incomplete)",
                      "company_evidence_refs": [], "company_evidence_provenance": [],
                      "tender_evidence_refs": _chunk_refs(requirement)}
            _attach_candidate_evidence(result, *relevance.get(str(rid), ([], [])))
        matrix.append(result)

    missing_llm = [r["requirement_id"] for r in grounded if r["requirement_id"] not in llm_evaluations]
    status = "partial" if errors or missing_llm or uncertain or agent1_output.get("status") != "complete" else "complete"
    partial_reasons = [item["error"] for item in errors]
    if missing_llm:
        partial_reasons.append("Agent 2 used deterministic rule evaluation for requirements without a valid LLM evaluation: " +
                               ", ".join(missing_llm))
    if uncertain:
        partial_reasons.append(f"{len(uncertain)} Agent 1 requirement(s) did not pass deterministic citation validation.")
    if agent1_output.get("status") != "complete":
        upstream_reasons = agent1_output.get("partial_reasons") or []
        partial_reasons.append("Agent 1 extraction is incomplete; Agent 2 results inherit that uncertainty." +
                               (" " + " ".join(str(reason) for reason in upstream_reasons) if upstream_reasons else ""))
    all_refs = [ref for row in matrix for ref in row.get("tender_evidence_refs", [])]
    all_refs.extend({"evidence_id": evidence_id} for row in matrix
                    for evidence_id in row.get("company_evidence_refs", []))
    return {"agent": AGENT_ORDER[1], "contract_version": AGENT_CONTRACT_VERSION, "status": status,
            "error": "; ".join(item["error"] for item in errors), "errors": errors,
            "fallback_used": bool(errors or missing_llm), "evaluated_count": seen,
            "batch_results": batch_results, "models": models, "requested_model": requested_model,
            "model_fallback_used": any(used_model != requested_model for used_model in models),
            "partial_reasons": list(dict.fromkeys(partial_reasons)),
            "llm_evaluated_requirement_ids": list(llm_evaluations), "rule_only_requirement_ids": missing_llm,
            "unresolved_requirement_ids": [r.get("requirement_id") for r in uncertain],
            "unresolved_gaps": [{"requirement_id": r.get("requirement_id"),
                                 "status": next((m.get("status") for m in matrix if m.get("requirement_id") == r.get("requirement_id")), A.INFORMATION_REQUIRED),
                                 "explanation": next((m.get("explanation") for m in matrix if m.get("requirement_id") == r.get("requirement_id")), "Requirement evidence is unresolved."),
                                 "evidence_refs": next((m.get("tender_evidence_refs", []) + m.get("company_evidence_refs", [])
                                                        for m in matrix if m.get("requirement_id") == r.get("requirement_id")), [])}
                                for r in matrix if A.normalize_status(r.get("status")) in
                                (A.INFORMATION_REQUIRED, A.UNRESOLVED, A.CONFIRMED_GAP)],
            "compliance_matrix": matrix, "evidence_refs": all_refs,
            "company_evidence_ids_consumed": list(facts_by_id)[:80]}


def run_agent3_bid_recommendation(agent1_output: dict, agent2_output: dict, *, tender_overview: dict,
                                  extraction_incomplete: bool = False, model: str | None = None,
                                  skip_groq_reason: str | None = None) -> dict:
    """Agent 3 contract: validated Agent 1/2 outputs -> cited report + deterministic final decision."""
    if not isinstance(agent1_output, dict) or not isinstance(agent2_output, dict) \
            or agent1_output.get("contract_version") != AGENT_CONTRACT_VERSION \
            or agent2_output.get("contract_version") != AGENT_CONTRACT_VERSION:
        return {"agent": AGENT_ORDER[2], "status": "failed", "error": "Agent 1 or Agent 2 output contract is invalid.",
                "fallback_used": True, "recommendation": A.compute_recommendation([], True),
                "report": {"label": "Deterministic fallback report", "summary": "Validated agent outputs are unavailable."}}
    matrix = agent2_output.get("compliance_matrix") or []
    agent1_requirements = {str(r.get("requirement_id")): r for r in agent1_output.get("requirements", [])
                           if isinstance(r, dict) and r.get("requirement_id")}
    tender_chunk_pages = {str(ref.get("chunk_id")): int(ref.get("page_number"))
                          for ref in agent1_output.get("evidence_refs", [])
                          if isinstance(ref, dict) and ref.get("chunk_id") and ref.get("page_number")}
    company_evidence_ids = {str(ref.get("evidence_id")) for ref in agent2_output.get("evidence_refs", [])
                            if isinstance(ref, dict) and ref.get("evidence_id")}
    matrix_ids = set()
    invalid_matrix = False
    for row in matrix:
        if not isinstance(row, dict):
            invalid_matrix = True
            break
        rid = str(row.get("requirement_id") or "")
        status_value = str(row.get("status") or "").upper()
        if not rid or rid not in agent1_requirements or rid in matrix_ids or status_value not in A.STATUSES:
            invalid_matrix = True
            break
        matrix_ids.add(rid)
        for ref in row.get("tender_evidence_refs", []):
            if not isinstance(ref, dict) or tender_chunk_pages.get(str(ref.get("chunk_id"))) != ref.get("page_number"):
                invalid_matrix = True
                break
        if invalid_matrix or any(str(ref) not in company_evidence_ids for ref in row.get("company_evidence_refs", [])):
            invalid_matrix = True
            break
    if invalid_matrix or matrix_ids != set(agent1_requirements):
        return {"agent": AGENT_ORDER[2], "status": "failed", "error": "Agent 2 compliance matrix failed citation/status validation.",
                "fallback_used": True, "recommendation": A.compute_recommendation([], True),
                "report": {"label": "Deterministic fallback report", "summary": "Agent 2 output was not sufficiently validated."}}
    incomplete = bool(extraction_incomplete or agent1_output.get("status") != "complete" or
                      agent2_output.get("status") != "complete")
    recommendation = A.compute_recommendation(matrix, extraction_incomplete=incomplete)
    valid_refs = set(tender_chunk_pages) | company_evidence_ids
    # Compact prompt: large tenders otherwise exceed the per-request token limits (HTTP 413 / ITPM 429).
    rows_for_prompt = [{"requirement_id": r.get("requirement_id"), "requirement": str(r.get("requirement") or "")[:120],
                        "status": r.get("status"), "is_mandatory": r.get("is_mandatory"),
                        "explanation": str(r.get("explanation") or "")[:160], "tender_evidence_refs": r.get("tender_evidence_refs"),
                        "company_evidence_refs": r.get("company_evidence_refs"), "cited_risk": r.get("cited_risk")}
                       for r in matrix]
    decision_for_prompt = {"decision": recommendation["decision"], "blockers": recommendation["blockers"][:5],
                           "status_counts": recommendation.get("status_counts")}
    overview_for_prompt = {k: tender_overview.get(k) for k in ("title", "reference_number", "issuing_organization", "deadline")}
    summary, cited_refs, model_used, error, proposed = "", [], None, "", ""
    if skip_groq_reason:
        error = str(skip_groq_reason)[:500]
    else:
        try:
            response, model_used = A.call_groq_modular(
            [{"role": "system", "content": (
                "You are Agent 3 — Bid Recommendation & Report. Consume the validated Agent 1 requirements and Agent 2 compliance results. "
                "Draft a concise evidence-grounded report summary (at most 120 words). Reasons, blockers and next actions are "
                "computed deterministically elsewhere; do not return them. "
                "For evidence_references return a non-empty JSON array of strings copied exactly from ALLOWED EVIDENCE IDS below. "
                "Do not return citation objects, page labels, requirement IDs, or any other identifier. "
                "Your proposed decision is advisory: deterministic eligibility/status rules are authoritative. Never turn missing evidence into BID. "
                "Do not call the company ineligible because its business specialization seems unrelated; only confirmed mandatory gaps disqualify. "
                # evidence_references comes first so a long summary can never push it out of the response.
                'Return JSON: {"evidence_references":[],"proposed_decision":"BID|NO-BID|REVIEW REQUIRED","report_summary":"..."}.' )},
             {"role": "user", "content": "AGENT 1 VALIDATED REQUIREMENTS (IDs; details are in the Agent 2 results):\n" + json.dumps(
                  [r.get("requirement_id") for r in agent1_output.get("requirements", [])], ensure_ascii=False) +
              "\n\nAGENT 2 VALIDATED COMPLIANCE AND RISK RESULTS:\n" + json.dumps(rows_for_prompt, ensure_ascii=False) +
              "\n\nALLOWED EVIDENCE IDS (copy exact IDs into evidence_references):\n" +
              json.dumps(sorted(valid_refs), ensure_ascii=False) +
              "\n\nDETERMINISTIC DECISION (must not be overridden):\n" + json.dumps(decision_for_prompt, ensure_ascii=False) +
              "\n\nTENDER OVERVIEW:\n" + json.dumps(overview_for_prompt, ensure_ascii=False)}],
            max_tokens=1200, preferred_model=model or GROQ_MODEL)
            if not isinstance(response, dict):
                raise ValueError("Agent 3 response was not a JSON object.")
            proposed = str(response.get("proposed_decision") or "")[:80]
            raw_summary = response.get("report_summary")
            if not isinstance(raw_summary, str) or not raw_summary.strip():
                raise ValueError("Agent 3 response did not contain a non-empty report_summary string.")
            cited_refs, citation_error = _validate_agent3_citations(
                response.get("evidence_references"), tender_chunk_pages, company_evidence_ids)
            if citation_error:
                raise ValueError(citation_error)
            summary = " ".join(raw_summary.split())[:2000]
        except Exception as exc:
            error = str(exc)[:500]

    if not error and not summary:
        error = "Agent 3 report summary was empty after validation."
    fallback = bool(error or not summary)
    if fallback:
        cited_refs = list(dict.fromkeys(
            ref.get("chunk_id") or ref.get("evidence_id")
            for row in matrix
            for ref in row.get("tender_evidence_refs", []) +
            [{"evidence_id": i} for i in row.get("company_evidence_refs", [])]
            if ref.get("chunk_id") or ref.get("evidence_id")))
    summary = summary or A.build_executive_summary(tender_overview, matrix, recommendation,
                                                    "deterministic three-agent fallback")
    if proposed and proposed.strip().upper() not in {"BID", "NO-BID", "REVIEW REQUIRED"}:
        proposed = "invalid proposal"
    report = {"label": "Deterministic fallback report (Agent 3 unavailable or citations invalid)" if fallback
              else "Evidence-grounded Agent 3 report",
              "summary": summary, "recommendation": recommendation["decision"],
              "reasons": recommendation["reasons"], "blockers": recommendation["blockers"],
              "next_actions": recommendation["next_actions"], "evidence_references": cited_refs,
              "llm_proposed_decision": proposed,
              "deterministic_decision_enforced": True}
    status = "partial" if fallback or agent1_output.get("status") != "complete" or agent2_output.get("status") != "complete" else "complete"
    return {"agent": AGENT_ORDER[2], "contract_version": AGENT_CONTRACT_VERSION, "status": status,
            "error": error, "model": model_used, "fallback_used": fallback,
            "recommendation": recommendation, "report": report,
            "evidence_refs": cited_refs,
            "consumed_agent1_requirement_ids": [r.get("requirement_id") for r in agent1_output.get("requirements", [])],
            "consumed_agent2_statuses": {r.get("requirement_id"): r.get("status") for r in matrix}}


def run_three_agent_pipeline(tender_text: str, profile: dict | None, retrieved_chunks: list[dict],
                             rag_metadata: dict, *, pdf_path: str | None = None,
                             custom_requirements: list | None = None, model: str | None = None,
                             progress=None) -> dict:
    """Execute the explicit sequential Agent 1 -> Agent 2 -> Agent 3 contracts."""
    def report(stage, message):
        if progress:
            progress(stage, message)

    if not tender_text or len(tender_text.strip()) < 30:
        return {"success": False, "pipeline_complete": False, "error_type": "invalid_input",
                "error": "Tender text is too short or empty."}
    if not isinstance(rag_metadata, dict) or rag_metadata.get("success") is not True:
        return {"success": False, "pipeline_complete": False, "error_type": "rag_evidence_unavailable",
                "error": "Verified retrieved tender evidence is required before running agents."}
    profile = profile or {}
    evidence = collect_company_evidence(profile)
    unprocessed_documents = collect_unprocessed_documents(profile)
    report("extraction", "Reading tender overview, tables and document checklist")
    overview = A.extract_tender_overview_deterministic(tender_text, pdf_path=pdf_path)
    risk_assessment = A.extract_risk_assessment(tender_text, profile, pdf_path=pdf_path)

    report("agent1", "Agent 1: extracting and validating requirements")
    agent1 = run_agent1_requirement_extraction(tender_text, retrieved_chunks, evidence, profile=profile,
                                               pdf_path=pdf_path, custom_requirements=custom_requirements,
                                               retrieval_metadata=rag_metadata, model=model)
    if agent1.get("status") == "failed":
        return {"success": False, "pipeline_complete": False, "error_type": "agent1_failed",
                "error": agent1.get("error"), "agent_pipeline": {"execution_order": list(AGENT_ORDER),
                "agents": [agent1], "fallback_used": agent1.get("fallback_used", False)}}
    agent2 = run_agent2_compliance_risk(agent1, evidence, profile=profile, model=model, documents=unprocessed_documents,
                                        progress=report)
    if agent2.get("status") == "failed":
        return {"success": False, "pipeline_complete": False, "error_type": "agent2_failed",
                "error": agent2.get("error"), "agent_pipeline": {"execution_order": list(AGENT_ORDER),
                "agents": [agent1, agent2], "fallback_used": True}}
    incomplete = bool(agent1.get("extraction_report", {}).get("incomplete"))
    report("agent3", "Agent 3: writing the cited bid recommendation")
    agent2_provider_limited = any(
        "all configured groq models were rate-limited" in str((item or {}).get("error") or "").casefold()
        for item in agent2.get("errors") or [])
    agent3 = run_agent3_bid_recommendation(
        agent1, agent2, tender_overview=overview, extraction_incomplete=incomplete, model=model,
        skip_groq_reason=("Skipped Agent 3 request because all configured Groq models were rate-limited in Agent 2."
                          if agent2_provider_limited else None))
    matrix = agent2["compliance_matrix"]
    recommendation = A.compute_recommendation(matrix, extraction_incomplete=incomplete)
    # Agent 3's deterministic recomputation is repeated here at the orchestration boundary.
    recommendation["decision"] = agent3["recommendation"]["decision"]
    actionable = A.generate_actionable_missing_information(matrix, profile, risk_assessment)
    pipeline_complete = all(a.get("status") == "complete" for a in (agent1, agent2, agent3))
    # Validation failures (never saved) vs coverage warnings (saved, decision limited to REVIEW REQUIRED / NO-BID).
    validation_failures, extra_warnings = [], []
    if agent1.get("error"):
        # The rejected AI output is not used at all; the requirements are Agent 1's deterministic, citation-validated
        # extraction. That limits coverage (the run cannot be BID) but nothing unvalidated enters the analysis.
        extra_warnings.append("AI requirement extraction (Agent 1) was unavailable or its output was rejected "
                              f"({str(agent1['error'])[:200]}); only deterministic, citation-validated requirements were used.")
    for err in agent2.get("errors") or []:
        validation_failures.append(f"Agent 2 batch {err.get('batch')} failed validation: {err.get('error')}")
    if agent2.get("rule_only_requirement_ids"):
        validation_failures.append("Agent 2 returned no valid evaluation for: " + ", ".join(map(str, agent2["rule_only_requirement_ids"])))
    if not agent2.get("llm_evaluated_requirement_ids"):
        validation_failures.append("Agent 2 evaluated no requirement with a validated tender citation.")
    if agent3.get("fallback_used") or agent3.get("error"):
        validation_failures.append(f"Agent 3 report or citations failed validation: {agent3.get('error') or 'invalid report'}")
    coverage_warnings = [] if pipeline_complete else list(dict.fromkeys(
        str(r) for r in extra_warnings + [p for p in (agent1.get("partial_reasons") or [])
                                          if not str(p).startswith("Groq requirement extraction response was unusable")] + [
            f"{len(agent2.get('unresolved_requirement_ids') or [])} requirement(s) have no validated retrieved citation and are shown as uncertain."
        ] if r and not str(r).startswith("0 requirement")))
    saveable = not validation_failures
    quality = {"status": "complete" if pipeline_complete else ("coverage_warnings" if saveable else "failed_validation"),
               "validation_failures": validation_failures, "coverage_warnings": coverage_warnings}
    models = [a.get("model") for a in (agent1, agent2, agent3) if a.get("model")]
    model_label = " + ".join(dict.fromkeys(models)) if models else "Groq unavailable (deterministic fallback)"
    pipeline = {"contract_version": AGENT_CONTRACT_VERSION, "execution_order": list(AGENT_ORDER),
                "status": "complete" if pipeline_complete else "partial",
                "pipeline_complete": pipeline_complete,
                "agents": [agent1, agent2, agent3],
                "fallback_used": any(a.get("fallback_used") for a in (agent1, agent2, agent3)),
                "model_fallback_used": any(a.get("model_fallback_used") for a in (agent1, agent2, agent3)),
                "errors": [{"agent": a["agent"], "error": a.get("error")} for a in (agent1, agent2, agent3) if a.get("error")],
                "evidence_refs": {"tender_chunks": agent1.get("evidence_refs", []),
                                  "company_evidence_ids": list({i for a in (agent1, agent2, agent3)
                                                                 for i in a.get("company_evidence_ids_consumed", [])})},
                "agent_contributions": {
                    AGENT_ORDER[0]: {"output": "validated requirements and tender citations",
                                     "requirement_count": len(agent1.get("requirements", [])),
                                     "accepted_llm_requirements": agent1.get("accepted_llm_requirement_ids", []),
                                     "consumed_by": [AGENT_ORDER[1], AGENT_ORDER[2]]},
                    AGENT_ORDER[1]: {"output": "evidence-reconciled compliance matrix and risks",
                                     "evaluated_count": len(matrix), "consumed_by": [AGENT_ORDER[2]]},
                    AGENT_ORDER[2]: {"output": "evidence-backed report and rule-enforced recommendation",
                                     "report_label": agent3.get("report", {}).get("label"),
                                     "deterministic_decision": recommendation["decision"]}},
                "rag": {"used": True, "status": rag_metadata.get("status", "retrieved"),
                        "embedding_model": rag_metadata.get("embedding_model"),
                        "source_sha256": rag_metadata.get("source_sha256"),
                        "retrieved_chunk_ids": [c.get("chunk_id") for c in retrieved_chunks],
                        "retrieved_pages": sorted({c.get("page_number") for c in retrieved_chunks if c.get("page_number")})}}
    provenance = {"engine_version": A.ENGINE_VERSION, "mode": "three_agent_groq", "requested_model": model or GROQ_MODEL,
                  "stage1_model": agent1.get("model"), "stage2_models": agent2.get("models", []),
                  "stage2_batches": agent2.get("batch_results", []),
                  "rule_only_ids": agent2.get("rule_only_requirement_ids", []),
                  "fallback_used": pipeline["fallback_used"], "fallback_reason": "; ".join(e["error"] for e in pipeline["errors"]),
                  "agents": pipeline["agents"], "agent_pipeline": pipeline,
                  "rag": pipeline["rag"]}
    data = {
        "tender_overview": overview,
        "eligibility_analysis": A.extract_eligibility_analysis(tender_text, profile, matrix, pdf_path=pdf_path),
        "compliance_matrix": matrix,
        "technical_evaluation": A.extract_technical_evaluation(tender_text, profile, compliance_matrix=matrix),
        "financial_analysis": A.extract_financial_analysis(tender_text, profile, pdf_path=pdf_path, compliance_matrix=matrix),
        "risk_assessment": risk_assessment,
        "risks": risk_assessment.get("penalties", []),
        "actionable_missing_information": actionable,
        "missing_information": [f"{i['item_name']} (Page {i.get('source_page', 1)})" for i in actionable],
        "recommendation": recommendation["decision"], "bid_recommendation": recommendation,
        "recommendation_rationale": " ".join(recommendation["reasons"]),
        "executive_summary": agent3["report"]["summary"],
        "agent_report": agent3["report"], "agent_pipeline": pipeline,
        "llm_analysis": {"company_eligibility": A.NOT_FOUND, "missing_profile_information": [i["item_name"] for i in actionable[:8]],
                         "next_steps": recommendation["next_actions"],
                         "ai_recommendation": agent3["report"].get("llm_proposed_decision", ""),
                         "ai_executive_summary": agent3["report"]["summary"]},
        "metrics": A.compute_metrics(matrix, incomplete),
        "extraction_report": agent1.get("extraction_report", {}),
        "analysis_provenance": provenance, "engine_version": A.ENGINE_VERSION,
        "model_used": model_label,
        # Which saved company evidence this analysis used (Section 4 freshness / audit trail).
        "company_evidence_snapshot": {
            "facts": [{k: f.get(k) for k in ("evidence_id", "concept_key", "verification_status", "document_id", "document_version")}
                      for f in evidence],
            "verified_fact_ids": [f["evidence_id"] for f in evidence if f.get("verification_status") == "verified"],
            "unprocessed_documents": unprocessed_documents,
        },
    }
    data["analysis_quality"] = quality
    data["tender_overview_sources"] = A.overview_sources(tender_text, overview)
    # Financial obligations / risks stated by the tender (EMD, PBG, ...), with their pages — never eligibility failures.
    data["bid_recommendation"].setdefault("assessment", {})["financial_obligations"] = [
        f"{c['requirement']}: {c['value']} (page {c['source_page']})"
        for c in (data["financial_analysis"].get(k) or {} for k in ("emd", "tender_fee", "pbg", "payment_terms"))
        if c.get("status") == "found"]
    error = "; ".join(e["error"] for e in pipeline["errors"]) or "; ".join(validation_failures)
    return {"success": pipeline_complete, "result_available": True,
            "pipeline_complete": pipeline_complete, "saveable": saveable, "analysis_quality": quality,
            "data": data, "model_used": model_label, "fallback_used": pipeline["fallback_used"],
            "error": error or ("; ".join(coverage_warnings) if not pipeline_complete else "")}
