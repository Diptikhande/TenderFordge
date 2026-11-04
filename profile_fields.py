"""
Dynamic company-profile fields generated from validated tender requirements (Section 3).

A requirement that still needs company information is mapped to a stable, reusable *concept*
(e.g. ``financial.annual_turnover_by_year`` or ``certification.iso_9001``). The concept key — not the
display label — identifies the field, so the same need in different tenders (or repeated analyses of
one tender) reuses one field and its saved value.

Not everything becomes a profile field:
  * requirements about the offered product (specifications) and tender-specific submissions
    (EMD, price schedule, tender-format declarations, ...) stay with the tender;
  * requirements whose tender evidence was not validated are reported as uncertain, not turned into fields;
  * company requirements that match no known reusable concept are listed for review instead of
    generating a field from their wording.

Values and documents are stored with the existing profile-evidence APIs and are always saved as
``unverified``. This module does not change how an analysis evaluates evidence (Section 4).
"""

from __future__ import annotations

import re

import analyzer as A
import database as db

OPEN_STATUSES = (A.INFORMATION_REQUIRED, A.UNRESOLVED, A.CONFIRMED_GAP)
FIXED_STORAGE_PREFIX = "fixed:"
CURRENCIES = ("INR", "USD", "EUR", "GBP")
AMOUNT_UNITS = ("", "Lakh", "Crore", "thousand", "million", "billion")


# ------------------------------------------------------------------ concept catalogue

def _concept(key, label, input_type, description, document_label="", storage="dynamic", pattern=None):
    return {"field_key": key, "label": label, "input_type": input_type, "description": description,
            "document_label": document_label, "document_key": f"field:{key}" if document_label else None,
            "storage": storage, "reusable": True, "origin": "generated",
            "pattern": re.compile(pattern, re.IGNORECASE) if pattern else None}


TURNOVER = _concept(
    "financial.annual_turnover_by_year", "Annual turnover by financial year", "money_period",
    "Turnover for each financial year, with currency. Tenders usually ask for an average or a minimum over a "
    "number of recent financial years, so give one figure per year.",
    "Audited financial statements / CA-certified turnover certificate", storage="fixed:annual_turnover")
YEARS = _concept(
    "experience.years_in_business", "Years in business", "years",
    "How many years the company has been operating (from incorporation / commencement of business).",
    "Certificate of incorporation or commencement of business", storage="fixed:years_of_experience")

# Matched in order; the first concept whose pattern fits the requirement wording is used.
CONCEPTS = [
    _concept("financial.net_worth", "Net worth", "money_period",
             "Net worth for the financial year(s) the tender asks about, with currency.",
             "CA certificate of net worth", pattern=r"net[\s-]*worth"),
    _concept("financial.solvency_certificate", "Bank solvency certificate", "certificate",
             "A solvency certificate from your bank: issuing bank, amount and date of issue.",
             "Bank solvency certificate", pattern=r"solvency"),
    _concept("financial.audited_accounts", "Audited balance sheets / profit & loss statements", "document",
             "Audited annual accounts for the financial years the tender asks about.",
             "Audited balance sheets and profit & loss statements",
             pattern=r"balance\s*sheets?|audit(?:ed)?\s+(?:report|accounts|financial|statement)|profit\s*(?:&|and)\s*loss"),
    _concept("registration.gst", "GST registration", "certificate",
             "Your GST registration number (GSTIN).", "GST registration certificate", pattern=r"\bgst\b|g\.s\.t"),
    _concept("registration.pan", "PAN", "certificate",
             "The company's Permanent Account Number.", "PAN card copy", pattern=r"\bpan\b"),
    _concept("registration.msme", "MSME / Udyam registration", "certificate",
             "MSME / Udyam registration number and category, if the company is registered.",
             "MSME / Udyam registration certificate", pattern=r"\bmsme\b|udyam|micro,?\s+small"),
    _concept("registration.incorporation", "Legal registration / incorporation", "certificate",
             "The company's legal registration (e.g. certificate of incorporation): registration number, "
             "registering authority and date.", "Certificate of incorporation / registration",
             pattern=r"incorporat|companies\s+act|registered\s+(?:in|under|company|legal|business|entity|as)|legal(?:ly)?\s+(?:entity|registration|registered)"),
    _concept("authorization.oem", "OEM / manufacturer authorization", "certificate",
             "Authorization from the manufacturer (OEM) to offer and support its products: manufacturer, "
             "scope and validity.", "Manufacturer authorization letter",
             pattern=r"authori[sz]ation\s+letter|manufacturer'?s?\s+authori|oem\s+authori|authori[sz]ed\s+(?:dealer|distributor|agent)"),
    _concept("experience.similar_projects", "Similar projects / work orders", "long_text",
             "Past contracts similar to what tenders ask for: client, scope, value, year and completion status.",
             "Work orders, purchase orders or completion certificates",
             # Order wording counts only in an experience context: "within ten weeks of purchase order" is a delivery term.
             pattern=r"similar\s+(?:work|order|project|experience|supply|nature)|past\s+(?:experience|performance)|relevant\s+experience|"
                     r"(?:copies?\s+of|past|previous|executed)\s+(?:the\s+)?(?:work|purchase|supply)\s+orders?|"
                     r"(?:work|purchase|supply)\s+orders?\s+(?:for|of)\s+similar|completion\s+certificates?|users?\s+list|"
                     r"supplied\s+not\s+less|successfully\s+executed"),
    _concept("capability.manufacturing_facilities", "Manufacturing facilities", "long_text",
             "Your manufacturing set-up: locations, main machines and in-house processes.",
             "Factory registration / facility details",
             pattern=r"manufacturing\s+(?:capabilit|facilit|unit|set-?up)|in-house\s+manufactur|factory\s+(?:licen|registration|premises)"),
    _concept("capability.inspection_testing", "Inspection & testing facilities", "long_text",
             "Inspection and testing equipment and processes, including calibration status.",
             "Calibration certificates / inspection equipment list",
             pattern=r"inspection|testing\s+facilit|calibrat|quality\s+control\s+lab"),
    _concept("capability.production_capacity", "Production / delivery capacity", "long_text",
             "How much the company can produce or deliver per month, and typical lead times.",
             pattern=r"delivery\s+capacity|production\s+capacity|monthly\s+capacity|capacity\s+to\s+(?:supply|deliver|manufacture)"),
_concept("capability.service_support", "After-Sales Service and Support", "long_text",
             "Service-centre locations, coverage, support-team capacity, response times and warranty arrangements.",
             pattern=r"after[\s-]*sales|service\s+(?:centre|center|network|support)|maintenance\s+support"),
]

# Requirement wording that belongs to one tender's submission rather than to the company profile.
_TENDER_SPECIFIC_RE = re.compile(
    r"earnest\s+money|\bemd\b|bid\s+security|tender\s+(?:fee|document\s+fee)|price\s+(?:schedule|bid|validity)|"
    r"\bpayment\b|performance\s+(?:security|bank\s+guarantee)|\bpbg\b|liquidated|delay\s+damages|warranty|"
    r"undertaking|affidavit|declaration|proforma|letter\s?head|presentation|compliance\s+(?:chart|statement)|"
    r"envelope|commercial\s+bid|quoted\s+(?:price|rate)|fall\s+clause|blacklist|debar|make\s*/\s*model|catalogue|"
    r"brochure|data\s*sheet|acceptance|quantity|submit\s+(?:the\s+)?bid|bid\s+(?:submission|validity|opening)|"
    r"clarification|deadline|pre-?bid|validity\s+of\s+(?:the\s+)?bid", re.IGNORECASE)


def _standard_slug(std: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", std.lower()).strip("_")


def certification_concept(std: str) -> dict:
    return _concept(f"certification.{_standard_slug(std)}", f"{std} certification", "certificate",
                    f"Your {std} certificate: certificate number, issuing body and validity (expiry) date.",
                    f"{std} certificate")


_KNOWN_CONCEPTS = {c["field_key"]: c for c in [TURNOVER, YEARS] + CONCEPTS}
# Fixed profile column that the deterministic rules read for each concept group (Section 4 evidence mapping).
_GROUP_TARGET = {"certification": "certifications", "registration": "certifications", "authorization": "certifications",
                 "capability": "technical_capabilities"}
_KEY_TARGET = {"financial.annual_turnover_by_year": "annual_turnover", "experience.years_in_business": "years_of_experience",
               "experience.similar_projects": "past_projects", "financial.net_worth": "financial_details",
               "financial.solvency_certificate": "financial_details", "financial.audited_accounts": "financial_details"}


def concept_for_key(field_key: str) -> dict | None:
    """The concept behind a stable field key (certification keys are reconstructed from the standard)."""
    key = str(field_key or "")
    if key in _KNOWN_CONCEPTS:
        return _KNOWN_CONCEPTS[key]
    if key.startswith("certification.") and len(key) > len("certification."):
        std = key.split(".", 1)[1].replace("_", " ").upper()  # iso_9001 -> ISO 9001, us_fda -> US FDA
        return certification_concept(std)
    return None


def concept_profile_target(field_key: str) -> str | None:
    """Fixed profile column the rules use for a concept's evidence, or None for unknown keys."""
    key = str(field_key or "")
    if key in _KEY_TARGET:
        return _KEY_TARGET[key]
    group = key.split(".", 1)[0]
    return _GROUP_TARGET.get(group) if concept_for_key(key) else None


def concept_keys_for_requirement(item: dict) -> set[str]:
    """Concept keys whose evidence is relevant to a requirement (empty for tender-specific or unmatched items)."""
    concepts, disposition = concepts_for_requirement(item)
    return {c["field_key"] for c in concepts} if disposition == "profile" else set()


def fixed_column_concept(column: str) -> str | None:
    """Concept stored in a fixed profile column (turnover / years), if any."""
    return next((c["field_key"] for c in (TURNOVER, YEARS) if c["storage"] == f"{FIXED_STORAGE_PREFIX}{column}"), None)


def public_definition(concept: dict) -> dict:
    """The persisted part of a concept (without its matching pattern)."""
    return {k: v for k, v in concept.items() if k != "pattern"}


# ------------------------------------------------------------------ requirement -> field plan

def is_validated_requirement(item: dict) -> bool:
    """Generated fields only come from requirements whose tender evidence was validated, or that the user curated."""
    if item.get("user_defined") or item.get("extraction_method") == "user":
        return True
    return item.get("agent1_validation") == "validated" and item.get("source_verified") is True


def _requirement_text(item: dict) -> str:
    return " ".join(f"{item.get('requirement', '')} {item.get('source_quotation', '')} {item.get('required_document', '')}".split())


def guidance_for_requirement(item: dict, field_key: str = "") -> dict:
    """Return practical, clause-aware instructions without inventing tender obligations."""
    title = " ".join(str(item.get("requirement") or item.get("requirement_text") or "").split())
    quote = " ".join(str(item.get("source_quotation") or "").split())
    doc = " ".join(str(item.get("required_document") or "").split()).strip(" .")
    if (re.search(r"\b(?:copies|copy|photocopies)\s+(?:will|shall)\s+be\s+rejected\b", doc, re.I)
            or re.fullmatch(r"copy of [a-z]", doc, re.I)):
        doc = ""
    text = f"{title} {quote}".lower()
    key = str(field_key or "")
    info, examples = [], []
    explicit_document_override = None
    document_label_override = None
    if re.search(r"gst\s*/?\s*clearance|gst clearance certificate", text) and re.search(r"gst3br|last month|quarter", text):
        info = ["Provide the GST registration details and confirm GST clearance through the last month or quarter applicable on the tender publication date."]
        examples = ["GST Registration Certificate and GST clearance certificate or GST3B return covering the applicable period."]
        doc = "GST Registration Certificate and GST clearance certificate/GST3B for the applicable period"
    elif re.search(r"authorization letter", text) and re.search(r"annex\s*[- ]?ii|manufacturer", text):
        info = ["Identify the manufacturer, full address, email, phone and country of manufacture; name the authorized product/model and sign the manufacturer authorization in the tender's Annex II format.",
                "Submit the original ink-signed authorization to DMER as stated in the clause."]
        if re.search(r"foreign manufacturer", text):
            info.append("If the manufacturer is foreign, include the specified import documents for similar equipment from the last three financial years.")
        examples = ["Completed Annex II manufacturer authorization letter.",
                    "For a foreign manufacturer only: the similar-equipment import documents for the last three financial years."]
        doc = "Annex II manufacturer authorization; foreign-manufacturer import documents for similar equipment for the last three financial years, if applicable"
    elif re.search(r"cdsco|md[- ]?(?:15|5|9|42)|iso\s*13485", text):
        info = ["State whether the offered product is imported or indigenous and provide the applicable CDSCO license numbers.",
                "Provide MD-42 in all cases and the manufacturing site's ISO 13485:2016 certificate.",
                "List US FDA or CE approval only as desirable evidence where available; the clause does not make these mandatory."]
        examples = ["Applicable CDSCO license: MD-15 for imported devices, or MD-5/MD-9 for indigenous devices; MD-42 in all cases; ISO 13485:2016 certificate for the manufacturing site.",
                    "US FDA/CE certificate (desirable, if available)."]
        doc = "MANDATORY: applicable CDSCO license (MD-15 imported or MD-5/MD-9 indigenous), MD-42 in all cases, and manufacturing-site ISO 13485:2016 certificate. DESIRABLE only: US FDA/CE approval, if available."
        explicit_document_override = True
        document_label_override = "Mandatory certificate evidence; desirable approvals identified separately"
    elif re.search(r"user list|users list", text) and re.search(r"annex\s*[- ]?iv|purchase order", text):
        info = ["Complete the user list in Annex IV for the exact quoted model.",
                "List purchase orders for that model issued during the last three financial years."]
        examples = ["Annex IV user list and copies of the required purchase orders for the quoted model."]
        doc = "Annex IV user list and purchase orders for the quoted model from the last three financial years"
    elif re.search(r"service centre|service center", text) and re.search(r"maharashtra|annex\s*[- ]?v", text):
        info = ["List at least two service centres located in Maharashtra.",
                "For each centre, provide the full address, phone, fax and email, using Annex V as required by the clause."]
        examples = ["Completed Annex V service-centre list with the required contact details."]
        doc = "Annex V service-centre list showing at least two Maharashtra centres and each full address, phone, fax and email"
        explicit_document_override = True
        document_label_override = "Tender-required information in Annex V"
    elif re.search(r"acceptance.*terms|terms.*condition.*acceptance|annex\s*[- ]?vi", text):
        info = ["Confirm acceptance of the tender terms and conditions, or identify each exception clearly.",
                "Complete and sign the acceptance certificate in Annex VI through the authorized signatory."]
        examples = ["Signed Annex VI acceptance certificate, as required by the clause."]
        doc = "Signed Annex VI acceptance certificate"
    elif re.search(r"fall clause", text):
        info = ["Confirm that during the contract you will not charge another customer a lower price for the same goods under comparable conditions.",
                "Disclose any lower price charged during the contract and explain how the tender's rate-adjustment obligation will be met."]
        examples = ["The cited clause names no separate supporting document; provide a signed statement only if the tender's form or submission instructions require one."]
        document_label_override = "No supporting document is named by this clause"
    elif re.search(r"commercial bid", text) and re.search(r"envelope", text):
        info = ["Keep all prices and the commercial bid in the designated commercial-bid envelope; do not include them in Envelope No. 01 (technical bid), which the clause says may be rejected."]
        examples = ["No additional supporting document is named by this clause."]
        document_label_override = "No supporting document is required by this clause"
    elif re.search(r"one agent|one principal|principal.*agent|agent.*principal", text):
        info = ["Identify the principal/OEM and the bidder's relationship to it, and confirm that the same principal is not represented by another agent and the bidder does not represent another principal for this tender.",
                "If relying on the PAC exception, identify that exception and provide its PAC certificate."]
        examples = ["PAC certificate only when relying on the stated PAC exception; otherwise no separate document is named by this clause."]
        document_label_override = "PAC certificate only if relying on the exception"
    elif re.search(r"approximate quantity|additional quantity|extra\s+50%", text):
        info = ["Confirm that you can supply an additional quantity of up to 50% at the same price if ordered within six months, as stated in the clause.",
                "Ensure every copy of each certificate/document submitted with the bid is self-attested and submitted by the stated deadline."]
        examples = ["Self-attested copies of the certificates and documents submitted with the bid."]
        doc = "Self-attested copies of all certificates/documents submitted with the bid"
    elif item.get("requirement_scope") == "offered_product" or item.get("category") == "Technical Specification":
        info = ["For each listed specification, state the offered make/model and the matching value or configuration.",
                "Identify any deviation explicitly; do not leave optional or conditional items unexplained."]
        examples = ["Product catalogue or datasheet for the exact offered make and model, if available or required by the clause."]
    elif item.get("requirement_scope") == "tender_submission" or item.get("category") == "Technical":
        clause = quote or title
        info = [f"Respond to each obligation in this tender clause: {clause[:650]}" if clause else
                "State the proposed approach and how each part of this tender obligation will be met."]
        if re.search(r"layout|blueprint|drawings?", text) and re.search(r"attach|submit|enclose|provide", text):
            examples = ["The approved layout/blueprint drawings expressly requested in the clause."]
        elif re.search(r"site visit|inspect", text):
            examples = ["Site-visit record or inspection notes, if requested by the procuring authority."]
        else:
            examples = ["Only the response or supporting evidence expressly named in this clause; other files are optional."]
    elif re.search(r"earnest money|\bemd\b|bid security|tender (?:document )?fee|processing fee", text):
        info = ["Amount stated in the tender and the amount payable or claimed as exempt.",
                "Payment method, transaction/reference number, payment date and current status.",
                "Payment or exemption deadline, if stated in the clause."]
        examples = ["Payment receipt or bank guarantee, if the tender requires it.",
                    "Only the exemption evidence expressly accepted by the tender, if applicable."]
    elif re.search(r"make\s*/\s*model|make and model|model number|manufacturer.*model", text):
        info = ["Equipment/item name, manufacturer and exact make and model number.",
                "The specifications and offered configuration that correspond to the tender clause."]
        examples = ["Product datasheet or catalogue for the exact make and model named in your response."]
    elif key == "financial.annual_turnover_by_year" or "turnover" in text:
        period = A.parse_turnover_requirement(f"{title} {quote}")["period"]
        years = period.get("years")
        info = ([f"Enter turnover separately for each of the {years} financial years required by the tender."]
                if years else ["Enter turnover separately for each financial year named in the tender."])
        info.append("Include the currency and unit for every year's figure; do not combine years into one total.")
        examples = ["Audited financial statements or a CA-certified turnover statement, in the form accepted by the tender."]
    elif key == "experience.similar_projects" or re.search(r"past experience|similar project|similar work|completed project", text):
        info = ["For each similar completed project, give the client/department and project scope.",
                "Include completion date, contract value and completion status.",
                "State how each project matches the work described in the tender."]
        examples = ["Purchase/work orders and completion or performance certificates, where required by the tender."]
    elif key == "capability.service_support" or re.search(r"after.sales|service centre|service center|support network", text):
        info = ["List service-centre locations and the areas each centre covers.",
                "Give the number of service engineers/support staff available.",
                "State response times, complaint escalation steps, maintenance and warranty-support arrangements."]
        examples = ["Service-centre list with addresses and contacts; support-team details or a service policy, if available or required.",
                    "OEM service authorization or service agreement, where applicable."]
    elif key == "registration.gst" or re.search(r"gst registration|gst clearance|gst return|gst filing", text):
        info = ["Enter the legal entity's GSTIN and registered name.",
                "Describe the GST clearance/return status for the period the tender specifies."]
        examples = ["GST registration certificate and only the return/clearance evidence specified in the tender."]
    elif key == "financial.audited_accounts" or re.search(r"audited (?:financial )?statements?|audited accounts|balance sheet", text):
        years = sorted(set(re.findall(r"(?:FY\s*)?20\d{2}\s*[-–/]\s*(?:20)?\d{2}", f"{title} {quote}", re.I)))
        info = [f"Provide audited statements for these financial years: {', '.join(years)}." if years else
                "Provide the audited financial years explicitly named in the tender clause.",
                "Include each required statement (such as balance sheet or profit and loss statement) named by the tender."]
        examples = ["Upload the audit/certification documents in the format the tender accepts; do not assume an alternative is allowed."]
    elif key == "financial.net_worth" or "net worth" in text:
        info = ["Enter the net-worth figure for the financial year or period required by the tender.",
                "Include currency, unit and the calculation basis if the tender specifies one."]
        examples = ["Audited or CA-certified financial evidence, where accepted or required by the tender."]
    elif key == "authorization.oem" or re.search(r"manufacturer authorization|oem authorization|authorization letter", text):
        info = ["Name the manufacturer and the products/models covered by its authorization.",
                "Include the authorized activities and the letter's validity dates, as specified in the tender."]
        examples = ["Manufacturer/OEM authorization letter covering the offered products, if required by the tender."]
    elif key.startswith("certification.") or key.startswith("certification"):
        certificate = title or key.split(".", 1)[-1].replace("_", " ")
        info = [f"For {certificate}, provide the certificate number, issuing body and validity/expiry date."]
        examples = [f"The {certificate} certificate named in the tender clause."]
    elif "experience.years_in_business" == key or re.search(r"years? in business|years? of experience", text):
        info = ["Enter the number of years in operation and the incorporation/commencement date used to calculate it."]
        examples = ["Certificate of incorporation or another commencement-of-business record, if required by the tender."]
    else:
        clause = quote or title
        info = [f"Address the cited tender clause directly: {clause[:650]}" if clause else
                "Review the cited tender page and state the specific fact, commitment or response requested by that clause."]
        examples = ["No supporting document is identified in the extracted clause. Attach a document only if the clause or tender form explicitly requests it."]

    if not doc:
        if re.search(r"(?:original\s+)?(?:catalogues?|brochures?|data\s*heets?)", quote, re.I) and \
                re.search(r"\b(?:enclose|submit|attach|provide)\w*\b", quote, re.I):
            doc = "Original product catalogue/brochure and technical data sheets"
        elif re.search(r"(?:approved\s+)?(?:layout|blueprint)\s+drawings?", quote, re.I) and \
                re.search(r"\b(?:attach|submit|enclose|provide)\w*\b", quote, re.I):
            doc = "Approved layout/blueprint drawings"
        elif re.search(r"\b(?:test|calibration|safety|biocompatibility)\s+reports?\b", quote, re.I) and \
                re.search(r"\b(?:submit|attach|enclose|provide)\w*\b", quote, re.I):
            doc = "The test/safety reports named in the tender clause"
    explicit_document = (bool(doc and re.search(r"\b(?:submit|attach|enclose|furnish|provide|upload)\w*\b", quote, re.I))
                         if explicit_document_override is None else explicit_document_override)
    return {
        "information": info,
        "documents": ([f"Tender clause names: {doc}."] if explicit_document and doc else examples),
        "document_label": document_label_override or ("Document expressly named by the tender" if explicit_document else "Supporting documents (optional unless the clause requires them)"),
        "explicit_document": explicit_document,
    }


def _turnover_explanation(item: dict) -> str:
    text = " ".join(f"{item.get('requirement', '')} {item.get('source_quotation', '')}".split())
    parsed = A.parse_turnover_requirement(text)
    period = parsed["period"]
    what = A._PERIOD_LABEL.get(period["kind"], "turnover")
    years = f" over the last {period['years']} financial years" if period.get("years") else ""
    if parsed["threshold"]:
        return f"The tender requires {what} of at least {parsed['threshold']['raw']}{years}. Enter the turnover for each of those years."
    if parsed["relative"]:
        return f"The tender sets a relative threshold ({parsed['relative']}){years}. Enter the turnover for each of those years."
    return f"The tender asks about turnover{years}: \"{text[:200]}\""


def concepts_for_requirement(item: dict) -> tuple[list[dict], str]:
    """
    Map one requirement to profile concepts. Returns (concepts, disposition) where disposition is
    'profile' (concepts found), 'tender_specific' (stays with the tender) or 'unmatched'.
    """
    text = _requirement_text(item)
    title = " ".join(str(item.get("requirement") or "").split())
    if item.get("requirement_scope") == "offered_product" or item.get("category") == "Technical Specification":
        return [], "tender_specific"
    # The clause title states what is asked; a tender-specific title (EMD, payment, bid date, make/model ...)
    # keeps the item with the tender even if its body mentions a company document in passing.
    if _TENDER_SPECIFIC_RE.search(title) or str(item.get("requirement_id") or "").upper().startswith("DATE-"):
        return [], "tender_specific"
    kind = item.get("requirement_kind") or A.requirement_kind(item)
    found = []
    if kind == "turnover":
        found.append(TURNOVER)
    if kind == "experience_years":
        general = re.search(r"in\s+business|in\s+existence|in\s+operation|since\s+(?:its\s+)?incorporation|functioning|operating", text, re.IGNORECASE)
        found.append(YEARS)
        if not general or re.search(r"similar|relevant", text, re.IGNORECASE):
            found.append(next(c for c in CONCEPTS if c["field_key"] == "experience.similar_projects"))
    for std in A.standards_in(text):
        found.append(certification_concept(std))
    if not found:
        # Title first; the clause body only for non-technical requirements (a technical clause's body describes
        # the work, e.g. "measured by CMM inspection", not a company capability being asked for).
        matches = [c for c in CONCEPTS if c["pattern"].search(title)]
        if not matches and item.get("category") != "Technical":
            matches = [c for c in CONCEPTS if c["pattern"].search(text)]
        if matches:
            group = matches[0]["field_key"].split(".")[0]
            found = [c for c in matches if c["field_key"].split(".")[0] == group]
    if found:
        unique = list({c["field_key"]: c for c in found}.values())
        return unique, "profile"
    if _TENDER_SPECIFIC_RE.search(text) or item.get("category") == "Technical":
        return [], "tender_specific"
    return [], "unmatched"


def build_field_plan(matrix: list[dict]) -> dict:
    """Field definitions and requirement links for one tender's evaluated requirements (no database access)."""
    definitions, links = {}, []
    plan = {"tender_specific": [], "uncertain": [], "unmatched": []}
    for item in matrix or []:
        status = A.normalize_status(item.get("status"))
        if status not in OPEN_STATUSES:
            continue
        summary = {"requirement_id": item.get("requirement_id"), "requirement": item.get("requirement", ""),
                   "status": status, "source_page": item.get("source_page"),
                   "is_mandatory": bool(item.get("is_mandatory", True))}
        if not is_validated_requirement(item):
            plan["uncertain"].append({**summary, "reason": "The requirement's tender evidence was not validated, so no profile field was generated."})
            continue
        concepts, disposition = concepts_for_requirement(item)
        if disposition != "profile":
            plan[disposition].append(summary)
            continue
        for concept in concepts:
            definitions.setdefault(concept["field_key"], public_definition(concept))
            if concept is TURNOVER:
                explanation = _turnover_explanation(item)
            else:
                explanation = f"The tender requires: {' '.join(str(item.get('requirement') or '').split())[:240]}"
                if item.get("required_document"):
                    explanation += f" Document requested: {item['required_document'][:160]}."
            links.append({"field_key": concept["field_key"], "requirement_id": item.get("requirement_id"),
                          "requirement_text": str(item.get("requirement") or "")[:400], "requirement_status": status,
                          "is_mandatory": summary["is_mandatory"], "source_page": item.get("source_page"),
                          "source_quotation": str(item.get("source_quotation") or ""), "explanation": explanation})
    plan["definitions"] = list(definitions.values())
    plan["links"] = links
    return plan


def sync_profile_fields_for_user(user_id: int) -> dict:
    """
    Record the profile fields needed by the latest saved analysis of each of the user's tenders.
    Idempotent: re-running (or re-analysing a tender) reuses existing fields and links.
    Returns per-tender plans so the UI can also show tender-specific, uncertain and unmatched items.
    """
    summary = {"tenders": [], "created": [], "reused": []}
    for tender in db.get_tenders_with_analysis(user_id):
        if not tender.get("analysis_id"):
            continue
        analysis = db.get_analysis_for_tender(tender["id"], user_id)
        if not analysis:
            continue
        plan = build_field_plan(analysis.get("compliance_matrix") or [])
        result = db.sync_tender_profile_fields(user_id, tender["id"], analysis["id"], plan["definitions"], plan["links"])
        summary["created"] += result["created"]
        summary["reused"] += result["reused"]
        summary["tenders"].append({"tender_id": tender["id"], "filename": tender["filename"], "analysis_id": analysis["id"],
                                   "tender_specific": plan["tender_specific"], "uncertain": plan["uncertain"],
                                   "unmatched": plan["unmatched"]})
    summary["created"] = sorted(set(summary["created"]))
    summary["reused"] = sorted(set(summary["reused"]) - set(summary["created"]))
    return summary


# ------------------------------------------------------------------ values

def format_turnover_rows(rows: list[dict]) -> tuple[str, list[str]]:
    """Serialise per-year rows to text the turnover parser understands, e.g. 'FY2023-24: INR 2 Crore'."""
    parts, errors = [], []
    for i, row in enumerate(rows or [], 1):
        fy = " ".join(str(row.get("financial_year") or "").split())
        amount = row.get("amount")
        currency = str(row.get("currency") or "").upper()
        unit = str(row.get("unit") or "")
        if not fy and amount in (None, "", 0):
            continue
        m = re.fullmatch(r"(?:FY\s*)?(20\d{2})\s*[-–/]\s*(?:20)?(\d{2})", fy, re.IGNORECASE)
        if not m:
            errors.append(f"Row {i}: financial year must look like 2024-25.")
            continue
        try:
            value = float(amount)
        except (TypeError, ValueError):
            errors.append(f"Row {i}: amount must be a number.")
            continue
        if value <= 0:
            errors.append(f"Row {i}: amount must be greater than zero.")
            continue
        if currency not in CURRENCIES:
            errors.append(f"Row {i}: choose a currency.")
            continue
        if unit not in AMOUNT_UNITS:
            errors.append(f"Row {i}: unsupported unit '{unit}'.")
            continue
        number = f"{value:,.2f}".rstrip("0").rstrip(".") if not unit else f"{value:g}"
        parts.append(f"FY{m.group(1)}-{m.group(2)}: {currency} {number}{(' ' + unit) if unit else ''}")
    if len({p.split(':')[0] for p in parts}) != len(parts):
        errors.append("Each financial year can only be entered once.")
    return "; ".join(parts), errors


def turnover_rows_from_text(text: str) -> list[dict]:
    """Rows for the turnover editor from a stored per-year turnover text (empty if not per-year)."""
    rows = []
    for fig in A.parse_company_turnover(text or "").get("figures") or []:
        value, currency = fig["value"], fig["currency"] or "INR"
        unit, shown = "", value
        if currency == "INR" and value >= 1e7:
            unit, shown = "Crore", value / 1e7
        elif currency == "INR" and value >= 1e5:
            unit, shown = "Lakh", value / 1e5
        elif value >= 1e6:
            unit, shown = "million", value / 1e6
        rows.append({"financial_year": fig["fy"], "amount": round(shown, 4), "currency": currency, "unit": unit})
    return rows


def current_value(profile: dict, definition: dict) -> str:
    """The value currently saved for a field (fixed profile column or dynamic field)."""
    storage = str(definition.get("storage") or "dynamic")
    if storage.startswith(FIXED_STORAGE_PREFIX):
        return str((profile or {}).get(storage[len(FIXED_STORAGE_PREFIX):]) or "")
    row = next((f for f in (profile or {}).get("custom_fields") or [] if f.get("field_key") == definition["field_key"]), None)
    return str(row.get("field_value") or "") if row else ""


def field_documents(profile: dict, definition: dict) -> list[dict]:
    """Versions of the supporting document linked to this field, newest first."""
    key = definition.get("document_key")
    if not key:
        return []
    docs = [d for d in (profile or {}).get("supporting_documents") or [] if d.get("document_key") == key]
    return sorted(docs, key=lambda d: int(d.get("version") or 0), reverse=True)


def check_field_against_document(user_id: int, definition: dict, value: str, page: int | None, excerpt: str) -> dict:
    """
    Record the field's value with the page and exact supporting text of its latest uploaded document version,
    then run the in-app document check (database.review_document_value). The value is stored unverified and
    becomes 'verified' only if the check passes. Returns the review result.
    """
    value = str(value or "").strip()
    profile = db.get_company_profile(user_id) or {}
    docs = field_documents(profile, definition)
    if not docs:
        return {"passed": False, "status": "unverified", "reasons": ["upload the supporting document for this field first"]}
    if not value:
        return {"passed": False, "status": "unverified", "reasons": ["enter the value to check"]}
    latest = docs[0]
    db.save_company_document_values(user_id, latest["id"], [{
        "value_key": definition["field_key"], "value_text": value, "source_excerpt": str(excerpt or "").strip(),
        "source_page": int(page) if page else None, "verification_status": "unverified"}])
    row = next(v for v in db.get_company_documents(user_id) if v["id"] == latest["id"])
    value_id = next(v["id"] for v in row["extracted_values"] if v["value_key"] == definition["field_key"])
    return {**db.review_document_value(user_id, value_id), "document_value_id": value_id}


_FIXED_COLUMNS = ("company_name", "industry", "years_of_experience", "annual_turnover", "certifications",
                  "technical_capabilities", "past_projects", "financial_details", "contact_name",
                  "contact_email", "contact_phone", "contact_address")


def save_field_value(user_id: int, definition: dict, value: str, document: dict | None = None) -> dict:
    """
    Persist a value (and optionally a supporting document) for one generated field.
    Documents are versioned via save_company_document under the field's document key. Values and
    documents are recorded as user-entered and unverified; nothing is marked verified or compliant.
    """
    if not user_id:
        raise ValueError("user_id is required to save a profile field value.")
    value = str(value or "").strip()
    result = {"value_saved": False, "document_id": None}
    if document and document.get("content"):
        if not definition.get("document_key"):
            raise ValueError("This field does not accept a supporting document.")
        result["document_id"] = db.save_company_document(
            user_id, document["filename"], document["content"], document.get("media_type") or "application/pdf",
            document_key=definition["document_key"])
    profile = db.get_company_profile(user_id) or {}
    storage = str(definition.get("storage") or "dynamic")
    if storage.startswith(FIXED_STORAGE_PREFIX):
        column = storage[len(FIXED_STORAGE_PREFIX):]
        if column not in _FIXED_COLUMNS:
            raise ValueError(f"Unsupported fixed storage column: {column}")
        if value and value != str(profile.get(column) or "").strip():
            data = {c: profile.get(c, "") or "" for c in _FIXED_COLUMNS}
            data[column] = value
            db.save_company_profile(data, user_id=user_id)
            result["value_saved"] = True
        return result
    existing = next((f for f in profile.get("custom_fields") or [] if f.get("field_key") == definition["field_key"]), None)
    doc_id = result["document_id"] or (existing or {}).get("source_document_id")
    unchanged = existing is not None and str(existing.get("field_value") or "").strip() == value \
        and existing.get("source_document_id") == doc_id
    if (value or result["document_id"]) and not unchanged:
        db.save_company_profile_fields(user_id, [{
            "field_key": definition["field_key"], "field_value": value,
            "source": "user_entered_with_document" if doc_id else "user_entered",
            "source_document_id": doc_id, "source_excerpt": "", "source_page": None,
            "verification_status": "unverified",
        }])
        result["value_saved"] = True
    return result
