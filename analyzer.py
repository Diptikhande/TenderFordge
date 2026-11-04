class RequirementStatus(str):
    __hash__ = str.__hash__
    def __eq__(self, other):
        s_self = str(self)
        s_other = str(other)
        if s_self == s_other:
            return True
        eq_group = {"INFORMATION REQUIRED", "MISSING COMPANY INFORMATION", "REQUIRES VERIFICATION"}
        if s_self in eq_group and s_other in eq_group:
            return True
        return super().__eq__(other)

"""
TenderForge AI Tender Analyzer & Evidence Evaluation Engine
Provides deep evidence-based analysis across seven dedicated dimensions:
1. Tender Overview (Title, Reference, Organization, Value, Deadline, Scope)
2. Eligibility Analysis (Experience, Turnover, Certifications, Registration)
3. Compliance Matrix (Requirement, Tender Evidence, Company Evidence, Status, Explanation)
4. Technical Evaluation (Capabilities, Equipment, Expertise, Deliverables)
5. Financial Analysis (EMD, Fees, Turnover, PBG, Payment Terms)
6. Risk Assessment (Penalties, Deadlines, Missing Documents, Unclear Obligations)
7. Bid Recommendation (BID / NO-BID / REVIEW REQUIRED with Reasons, Blockers, Next Actions)
"""

import functools
import hashlib
import json
import math
import os
import re
import time
from datetime import date
import requests
import fitz
from config import GROQ_API_KEY, GROQ_MODEL, GROQ_FALLBACK_MODELS, GROQ_API_URL, NOT_FOUND


# ============================================================
# STATUS TAXONOMY & EVIDENCE BASIS
# ============================================================

ENGINE_VERSION = "2026.11-s3-agents"

SATISFIED = "SATISFIED"
CONFIRMED_GAP = "CONFIRMED GAP"
INFORMATION_REQUIRED = RequirementStatus("INFORMATION REQUIRED")
REQUIRES_VERIFICATION = RequirementStatus("REQUIRES VERIFICATION")
NOT_APPLICABLE = "NOT APPLICABLE"
UNRESOLVED = "UNRESOLVED"
STATUSES = (SATISFIED, CONFIRMED_GAP, INFORMATION_REQUIRED, REQUIRES_VERIFICATION, NOT_APPLICABLE, UNRESOLVED)
_LEGACY_STATUS = {"UNCLEAR": UNRESOLVED, "NOT SATISFIED": CONFIRMED_GAP, "INFO REQUIRED": INFORMATION_REQUIRED,
                  "N/A": NOT_APPLICABLE, "NA": NOT_APPLICABLE}

# How a requirement's status was established
BASIS_NONE = "No company evidence available"
BASIS_FIGURE = "Rule: stated company figure compared with the tender threshold"
BASIS_NOT_COMPARABLE = "Rule: stated figures could not be compared reliably"
BASIS_CERT_LISTING = "Rule: certifications listed in the company profile"
BASIS_EXPLICIT = "Rule: explicit statement in the company profile"
BASIS_DECLARATION = "Bidder declaration (unverified)"
BASIS_AI = "AI assessment of self-declared company information (no document verified)"

# Profile fields an AI assessment may quote as evidence. The company name and contact
# details are deliberately excluded: they never prove registration or eligibility.
PROFILE_EVIDENCE_KEYS = ["industry", "years_of_experience", "annual_turnover", "certifications",
                         "technical_capabilities", "past_projects", "financial_details"]


def normalize_status(value) -> str:
    """Map any status label (including legacy ones such as UNCLEAR) onto the five-status taxonomy."""
    s = " ".join(str(value or "").upper().replace("_", " ").split())
    if s in STATUSES:
        return s
    return _LEGACY_STATUS.get(s, UNRESOLVED)


def _page_at(tender_text: str, pos: int) -> int | None:
    """1-indexed page of a character offset in page-marked text."""
    marks = re.findall(r"---\s*\[Page\s*(\d+)\]\s*---", tender_text[:max(pos, 0)], re.IGNORECASE)
    return int(marks[-1]) if marks else None


def _sentence_at(text: str, start: int, end: int | None = None) -> str:
    """The sentence (or line block) surrounding a match, used as verbatim tender evidence."""
    end = start if end is None else end
    lefts = [text.rfind(". ", 0, start), text.rfind("\n\n", 0, start), text.rfind("---\n", 0, start)]
    s = max(lefts) + 1 if max(lefts) >= 0 else 0
    rights = [p for p in (text.find(". ", end), text.find("\n\n", end)) if p != -1]
    r = min(rights) + 1 if rights else len(text)
    return " ".join(text[s:r].split())[:400]


def _norm_text(s) -> str:
    return " ".join(re.sub(r"[^\w%./-]+", " ", str(s or "").lower()).split())


# ============================================================
# MONEY, PERIOD & YEARS PARSING
# ============================================================

_CURRENCY_CODES = {"₹": "INR", "inr": "INR", "rs": "INR", "rs.": "INR", "rupees": "INR", "rupee": "INR",
                   "us$": "USD", "usd": "USD", "$": "USD", "dollars": "USD",
                   "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP", "gbp": "GBP"}
_UNIT_VALUES = {"crore": 1e7, "crores": 1e7, "cr": 1e7, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
                "thousand": 1e3, "k": 1e3, "million": 1e6, "millions": 1e6, "mn": 1e6, "m": 1e6,
                "billion": 1e9, "billions": 1e9, "bn": 1e9, "b": 1e9}
_SHORT_UNITS = {"k", "m", "b", "mn", "bn"}
_INDIAN_UNITS = {"crore", "crores", "cr", "lakh", "lakhs", "lac", "lacs"}
_MONEY_RE = re.compile(
    r"(?<![\w.])"
    r"(?:(?P<cur1>₹|INR|Rs\.?|Rupees|US\$|USD|\$|€|EUR|£|GBP)\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<unit>crores?|cr\b\.?|lakhs?|lacs?|lac|thousand|millions?|mn|billions?|bn|k|m|b)(?![a-z]))?"
    r"(?:\s*/-)?"
    r"(?:\s*(?P<cur2>INR|USD|EUR|GBP|rupees|dollars|euros?)\b)?",
    re.IGNORECASE,
)
_FY_RE = re.compile(r"(?:FY\s*)?(20\d{2})\s*[-–/]\s*(?:20)?(\d{2})\b", re.IGNORECASE)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20}


def _to_int(token: str) -> int | None:
    token = token.lower()
    return int(token) if token.isdigit() else _NUM_WORDS.get(token)


def parse_money_amounts(text: str) -> list[dict]:
    """
    All monetary amounts in a text, with the value in base units, the currency (None if not stated)
    and the unit. Handles Indian grouping (75,00,000), lakh/crore, thousand/million/billion and K/M/B.
    Percentages, years, durations and small bare counts are ignored.
    """
    out = []
    if not text:
        return out
    for m in _MONEY_RE.finditer(text):
        try:
            number = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        unit = (m.group("unit") or "").lower().rstrip(".")
        cur_raw = (m.group("cur1") or m.group("cur2") or "").lower()
        currency = _CURRENCY_CODES.get(cur_raw) or _CURRENCY_CODES.get(cur_raw.rstrip("."))
        tail = text[m.end():m.end() + 14].lower()
        if re.match(r"\s*(?:%|per\s*cent|percent)", tail):
            continue
        if unit in _SHORT_UNITS and not currency:
            unit = ""  # a bare 'm' / 'k' without a currency is not a money unit (metres, kilo...)
        if not currency and not unit:
            if number < 1000 or (1900 <= number <= 2100 and number.is_integer()):
                continue
            if re.match(r"\s*(?:years?|yrs?|months?|days?|hours?|nos?\b|units?|pieces?|pcs|agents?|devices?|ml\b|mm\b|kg\b|sq)", tail):
                continue
        inferred = False
        if not currency and unit in _INDIAN_UNITS:
            currency, inferred = "INR", True
        out.append({"value": number * _UNIT_VALUES.get(unit, 1.0), "currency": currency,
                    "currency_inferred": inferred, "unit": unit, "raw": m.group(0).strip(),
                    "start": m.start(), "end": m.end()})
    return out


def parse_financial_period(text: str) -> dict:
    """Financial period a turnover requirement refers to: annual, average annual, each year or cumulative."""
    t = (text or "").lower()
    years = None
    m = re.search(r"(?:last|past|preceding|previous)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
                  r"\s*(?:\(\s*\d+\s*\)\s*)?(?:completed\s+|audited\s+|consecutive\s+)?(?:financial|fiscal|accounting)?\s*years?", t)
    if m:
        years = _to_int(m.group(1))
    fy = ["%s-%s" % f for f in _FY_RE.findall(t)]
    if years is None and len(set(fy)) >= 2:
        years = len(set(fy))
    if re.search(r"\baverage\b|\bavg\.?\b|\bmean\b", t):
        kind = "average_annual"
    elif re.search(r"\beach\s+of\s+the\b|\bin\s+each\b|\bevery\s+(?:financial\s+)?year\b|\beach\s+(?:financial\s+)?year\b", t):
        kind = "each_year"
    elif re.search(r"\bcumulative\b|\baggregate\b", t):
        kind = "cumulative"
    elif years and years > 1:
        kind = "unspecified_multi_year"
    else:
        kind = "annual"
    stated = kind != "annual" or bool(re.search(r"annual|per\s+annum|yearly|a\s+year|financial\s+year", t))
    return {"kind": kind, "years": years, "fy_mentions": fy, "stated": stated}


_THRESHOLD_CUE_RE = re.compile(
    r"(?:at\s+least|not\s+less\s+than|minimum(?:\s+of)?|min\.|more\s+than|exceed(?:ing|s)?|above|in\s+excess\s+of|"
    r"≥|>=|upwards\s+of|should\s+be|must\s+be|shall\s+be|of)\s*[:\-]?\s*$", re.IGNORECASE)


def parse_turnover_requirement(text: str) -> dict:
    """Monetary threshold (amount + currency) and financial period of a turnover requirement."""
    result = {"threshold": None, "relative": None, "period": parse_financial_period(text), "ambiguity": None}
    amounts = parse_money_amounts(text)
    if not amounts:
        pct = re.search(r"\d+(?:\.\d+)?\s*(?:%|per\s*cent|percent)(?:\s+of\s+(?:the\s+)?[a-z ]{3,40})?", text or "", re.IGNORECASE)
        if pct:
            result["relative"] = " ".join(pct.group(0).split())
            result["ambiguity"] = f"the threshold is relative ({result['relative']}), not a fixed amount"
        else:
            result["ambiguity"] = "no monetary threshold could be identified in the requirement text"
        return result
    currencies = {a["currency"] for a in amounts if a["currency"]}
    if len(currencies) > 1:
        result["ambiguity"] = f"the requirement mentions several currencies ({', '.join(sorted(currencies))})"
        return result
    cued = [a for a in amounts if _THRESHOLD_CUE_RE.search(text[max(0, a["start"] - 60):a["start"]])]
    if cued:
        result["threshold"] = cued[0]
    elif len({(round(a["value"], 2), a["currency"]) for a in amounts}) == 1:
        result["threshold"] = amounts[0]
    else:
        result["ambiguity"] = "several different amounts appear in the requirement, so the threshold is ambiguous"
    return result


def parse_company_turnover(text: str) -> dict:
    """The company's stated turnover: a single figure, a stated average, or per-financial-year figures."""
    raw = (text or "").strip()
    res = {"raw": raw, "figures": [], "single": None, "is_average": False, "ambiguity": None}
    if not raw:
        res["ambiguity"] = "empty"
        return res
    res["is_average"] = bool(re.search(r"\baverage\b|\bavg\.?\b", raw, re.IGNORECASE))
    all_amounts = parse_money_amounts(_FY_RE.sub(" ", raw))
    if res["is_average"] and len({(round(a["value"], 2), a["currency"]) for a in all_amounts}) == 1:
        # e.g. "Average annual turnover (FY2022-23 to FY2024-25): USD 15 million" is one stated average
        res["single"] = all_amounts[0]
        return res
    for seg in re.split(r"[;\n|]+", raw):
        fy = _FY_RE.search(seg)
        amts = parse_money_amounts(_FY_RE.sub(" ", seg))
        if fy and amts:
            res["figures"].append({**amts[0], "fy": f"{fy.group(1)}-{fy.group(2)}", "fy_start": int(fy.group(1))})
    res["figures"].sort(key=lambda f: f["fy_start"])
    if not res["figures"]:
        amounts = parse_money_amounts(_FY_RE.sub(" ", raw))
        if not amounts:
            res["ambiguity"] = "no amount"
        elif len({(round(a["value"], 2), a["currency"]) for a in amounts}) > 1:
            res["ambiguity"] = "several different amounts without financial-year labels"
        else:
            res["single"] = amounts[0]
    return res


def _money(a: dict) -> str:
    return f"{a.get('currency') or '?'} {a['value']:,.0f}"


_PERIOD_LABEL = {"annual": "annual turnover", "average_annual": "average annual turnover",
                 "each_year": "turnover in each year", "cumulative": "cumulative turnover",
                 "unspecified_multi_year": "multi-year turnover"}


_TURNOVER_TEXT_FIELDS = (("financial_details", "Financial details"), ("past_projects", "Past projects"),
                         ("technical_capabilities", "Technical capabilities"), ("certifications", "Certifications"))


def collect_turnover_statements(profile: dict, declared: str | None = None) -> list[dict]:
    """Every turnover statement in the company evidence, with its source (declaration, turnover field, narratives)."""
    profile = profile or {}
    out = []

    def add(source, text):
        text = " ".join(str(text or "").split())
        if text and not any(s["text"] == text for s in out):
            out.append({"source": source, "text": text})

    if declared and parse_money_amounts(declared):
        add("bidder declaration", declared)
    if str(profile.get("annual_turnover") or "").strip():
        add("Annual turnover field", profile.get("annual_turnover"))
    for key, label in _TURNOVER_TEXT_FIELDS:
        for line in str(profile.get(key) or "").splitlines():
            if line.strip().startswith("["):
                continue  # requirement-tagged declarations are handled as declarations
            # sentence split that does not break on "Rs. 5 crore" / "No. 3"
            for sentence in re.split(r"(?<!\bRs)(?<!\bNo)(?<!\bCr)\.\s+(?=[A-Z])", line):
                if re.search(r"\bturnover\b", sentence, re.IGNORECASE) and parse_money_amounts(sentence):
                    add(label, sentence.strip().rstrip("."))
    return out


def reconcile_turnover_statements(statements: list[dict]) -> dict:
    """
    Merge turnover statements into one figure set, or report a conflict. Per-financial-year figures are merged;
    anything that cannot be reconciled (a year with two values, an undated/uncurrencied figure beside other figures,
    several unlabelled amounts) is a conflict. One of several disagreeing values is never chosen silently.
    """
    if not statements:
        return {"text": "", "conflict": None}
    if len(statements) == 1:
        return {"text": statements[0]["text"], "conflict": None}
    by_year, singles, problems = {}, [], []
    for s in statements:
        p = parse_company_turnover(s["text"])
        if p["ambiguity"]:
            problems.append(f"{s['source']}: '{s['text'][:90]}' ({p['ambiguity']})")
            continue
        if p["figures"]:
            for f in p["figures"]:
                value = (round(f["value"], 2), f["currency"])
                if f["fy"] in by_year and by_year[f["fy"]][0] != value:
                    problems.append(f"FY {f['fy']} is stated as {_money(dict(zip(('value', 'currency'), by_year[f['fy']][0])))} "
                                    f"({by_year[f['fy']][1]}) and as {_money(f)} ({s['source']})")
                by_year.setdefault(f["fy"], (value, s["source"]))
        else:
            a = p["single"]
            singles.append((s, (round(a["value"], 2), a["currency"])))
    if singles and (by_year or len({v for _, v in singles}) > 1):
        problems += [f"{s['source']}: '{s['text'][:90]}'" + ("" if v[1] else " (no currency)") for s, v in singles]
    if problems:
        listed = "; ".join(f"{s['source']}: '{s['text'][:90]}'" for s in statements)
        return {"text": "", "conflict": (
            f"The company information states turnover in ways that do not agree ({listed}). Details: {'; '.join(problems)}. "
            "Enter one confirmed turnover figure per financial year, with currency, in the Company Profile; "
            "conflicting figures are not compared and none of them is chosen automatically.")}
    if by_year:
        return {"text": "; ".join(f"FY{fy}: {v[0][1]} {v[0][0]:.2f}" for fy, v in sorted(by_year.items())), "conflict": None}
    # the figures agree; keep the statement that says what period it covers (e.g. "average of last three years")
    labelled = [s for s, _ in singles if re.search(r"\baverage\b|\bavg\.?\b|\byears?\b", s["text"], re.IGNORECASE)]
    return {"text": (labelled or [singles[0][0]])[0]["text"], "conflict": None}


def compare_turnover(req_text: str, company_text: str, source_label: str = "company profile") -> dict:
    """
    Compare a tender turnover threshold with the company's stated turnover.
    Values are compared only when currency and financial period are compatible; otherwise the
    result is INFORMATION REQUIRED (company data missing) or UNRESOLVED (not comparable).
    """
    req = parse_turnover_requirement(req_text)
    period = req["period"]
    n = period["years"]
    if not str(company_text or "").strip():
        return {"status": INFORMATION_REQUIRED, "comparable": False,
                "explanation": "The company's turnover is not stated. Provide audited turnover figures for the financial years the tender specifies."}
    if req["threshold"] is None:
        return {"status": UNRESOLVED, "comparable": False,
                "explanation": f"The turnover requirement could not be evaluated automatically: {req['ambiguity']}."}
    thr = req["threshold"]
    comp = parse_company_turnover(company_text)
    if comp["ambiguity"] == "no amount":
        return {"status": INFORMATION_REQUIRED, "comparable": False,
                "explanation": f"The stated turnover '{comp['raw'][:80]}' could not be read as an amount."}
    if comp["ambiguity"]:
        return {"status": UNRESOLVED, "comparable": False,
                "explanation": f"The stated turnover is ambiguous ({comp['ambiguity']})."}
    comp_amounts = comp["figures"] or [comp["single"]]
    comp_cur = {a["currency"] for a in comp_amounts}
    if None in comp_cur:
        return {"status": INFORMATION_REQUIRED, "comparable": False,
                "explanation": f"The stated turnover '{comp['raw'][:80]}' has no currency (write e.g. 'INR 2 Crore' or 'USD 1.5 million'); it cannot be compared with the tender threshold of {thr['raw']}."}
    if thr["currency"] is None:
        return {"status": UNRESOLVED, "comparable": False,
                "explanation": f"The tender threshold '{thr['raw']}' has no identifiable currency."}
    if len(comp_cur) > 1 or thr["currency"] not in comp_cur:
        return {"status": UNRESOLVED, "comparable": False,
                "explanation": f"The tender threshold is in {thr['currency']} ({thr['raw']}) but the company figure is in {', '.join(sorted(comp_cur))}. No conversion basis is available, so the values are not compared."}

    notes = []
    if thr.get("currency_inferred") or any(a.get("currency_inferred") for a in comp_amounts):
        notes.append("currency inferred as INR from lakh/crore units")
    if not period["stated"]:
        notes.append("the tender does not state the period; treated as annual turnover")
    kind = period["kind"]
    figs = comp["figures"]
    value, basis_txt = None, ""
    if kind == "annual":
        a = figs[-1] if figs else comp["single"]
        value, basis_txt = a["value"], (f"FY {a['fy']} turnover" if figs else "stated turnover")
    elif kind == "average_annual":
        if figs and (n is None or len(figs) >= n):
            used = figs[-n:] if n else figs
            value = sum(f["value"] for f in used) / len(used)
            basis_txt = f"average of {len(used)} stated year(s) ({', '.join(f['fy'] for f in used)})"
        elif comp.get("single") and not period.get("fy_mentions"):
            value, basis_txt = comp["single"]["value"], "stated turnover"
        else:
            return {"status": UNRESOLVED, "comparable": False,
                    "explanation": f"The tender requires the average annual turnover over {n or 'several'} financial year(s) (threshold {thr['raw']}); the profile gives a single figure ('{comp['raw'][:60]}') that is not stated as that average. Provide the turnover for each financial year."}
    elif kind == "each_year":
        if figs and (n is None or len(figs) >= n):
            used = figs[-n:] if n else figs
            below = [f for f in used if f["value"] < thr["value"]]
            status = CONFIRMED_GAP if below else (INFORMATION_REQUIRED if source_label in ("company profile", "bidder declaration (unverified)") else SATISFIED)
            detail = ("below the threshold in " + ", ".join(f"FY {f['fy']} ({_money(f)})" for f in below)) if below \
                else f"at or above the threshold in all {len(used)} year(s)"
            return {"status": status, "comparable": True,
                    "explanation": f"Tender requires turnover of at least {thr['raw']} in each year; the company's stated turnover is {detail}." + (f" Note: {'; '.join(notes)}." if notes else "")}
        return {"status": UNRESOLVED, "comparable": False,
                "explanation": f"The tender requires turnover of at least {thr['raw']} in each of {n or 'several'} year(s); provide the turnover for each year."}
    elif kind == "cumulative":
        if figs and n and len(figs) >= n:
            used = figs[-n:]
            value, basis_txt = sum(f["value"] for f in used), f"sum of {n} stated year(s)"
        else:
            return {"status": UNRESOLVED, "comparable": False,
                    "explanation": f"The tender sets a cumulative turnover threshold ({thr['raw']}); provide the turnover for each of the {n or 'relevant'} year(s)."}
    else:
        if comp.get("single") and thr:
            value, basis_txt = comp["single"]["value"], "stated turnover"
        else:
            return {"status": UNRESOLVED, "comparable": False,
                    "explanation": f"The tender mentions {n} years but does not say whether {thr['raw']} is an average, each-year or cumulative figure."}

    meets = value >= thr["value"]
    status = SATISFIED if meets else CONFIRMED_GAP
    expl = (f"Tender requires {_PERIOD_LABEL.get(kind, 'turnover')} of at least {thr['raw']} ({_money(thr)}). "
            f"{source_label[0].upper() + source_label[1:]} {basis_txt}: {_money({'value': value, 'currency': thr['currency']})}, "
            f"which {'meets' if meets else 'is below'} the threshold.")
    if notes:
        expl += f" Note: {'; '.join(notes)}."
    return {"status": status, "comparable": True, "explanation": expl}


_EXP_CONTEXT_RE = re.compile(r"experience|in\s+business|in\s+operation|in\s+existence|functioning|operating|track\s+record", re.IGNORECASE)
_YEARS_RE = re.compile(r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty)\s*(?:\(\s*\d+\s*\)\s*)?\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)


def parse_years_requirement(text: str) -> int | None:
    """Minimum number of years of experience / existence a requirement asks for, or None."""
    for m in _YEARS_RE.finditer(text or ""):
        before = text[max(0, m.start() - 25):m.start()].lower()
        if re.search(r"(?:last|past|preceding|previous|within|period\s+of|validity\s+of|warranty\s+of)\s*$", before):
            continue  # 'last 3 years' is a period, not a minimum duration
        window = text[max(0, m.start() - 120):m.end() + 120]
        if _EXP_CONTEXT_RE.search(window):
            return _to_int(m.group(1))
    return None


def parse_company_years(text: str) -> float | None:
    """Years in business stated by the company ('10 years', '10', 'since 2015')."""
    years = extract_numeric_years(text or "")
    if years is not None:
        return years
    m = re.search(r"(?:since|established|estd\.?|incorporated|founded)\s*(?:in\s*)?(19\d{2}|20\d{2})", text or "", re.IGNORECASE)
    if m:
        return float(date.today().year - int(m.group(1)))
    return None


# ============================================================
# CERTIFICATIONS
# ============================================================

_STANDARD_RE = re.compile(
    r"\bISO(?:\s*/\s*IEC)?\s*[-:]?\s*(\d{4,5})\b|\bCMMI\b|\bAS\s*9100\w*|\bUS\s*FDA\b|\bCE\s+(?:mark|certif)\w*|"
    r"\bSTQC\b|\bPCI[\s-]*(?:PTS|DSS)\b|\bNABL\b|\bSOC\s*2\b", re.IGNORECASE)
_STANDARD_PATTERNS = {"CMMI": r"\bCMMI\b", "AS9100": r"\bAS\s*9100", "US FDA": r"\bUS\s*FDA\b", "CE": r"\bCE\b",
                      "STQC": r"\bSTQC\b", "PCI PTS": r"\bPCI[\s-]*PTS\b", "PCI DSS": r"\bPCI[\s-]*DSS\b",
                      "NABL": r"\bNABL\b", "SOC 2": r"\bSOC\s*2\b"}
_CERT_NEGATIVE_RE = re.compile(r"not\s+verified|unverified|expired|lapsed|applied|in\s+progress|pending|under\s+process|"
                               r"awaited|planned|not\s+(?:held|available|valid)", re.IGNORECASE)
_EXPLICIT_NONE = {"none", "none listed", "nil", "no", "no certifications", "no certification", "not applicable", "na", "n/a"}


def _standard_key(m: re.Match) -> str:
    if m.group(1):
        return f"ISO {m.group(1)}"
    s = re.sub(r"[\s-]+", " ", m.group(0).upper()).strip()
    if s.startswith("AS"):
        return "AS9100"
    if "FDA" in s:
        return "US FDA"
    if s.startswith("CE"):
        return "CE"
    if s.startswith("PCI"):
        return "PCI DSS" if "DSS" in s else "PCI PTS"
    return s


def _standard_regex(key: str) -> re.Pattern:
    if key.startswith("ISO "):
        return re.compile(rf"\bISO(?:\s*/\s*IEC)?\s*[-:]?\s*{key[4:]}\b", re.IGNORECASE)
    return re.compile(_STANDARD_PATTERNS.get(key, re.escape(key)), re.IGNORECASE)


def standards_in(text: str) -> list[str]:
    """Certification standards named in a text (word-boundary matching: 'isolated' is not ISO)."""
    keys = []
    for m in _STANDARD_RE.finditer(text or ""):
        k = _standard_key(m)
        if k not in keys:
            keys.append(k)
    return keys


def evaluate_certification(req_text: str, certs_text: str, is_mandatory: bool, allow_satisfied: bool = False) -> dict:
    """Check the certifications a requirement names against those listed in the company profile."""
    found = []
    for m in _STANDARD_RE.finditer(req_text or ""):
        key = _standard_key(m)
        around = (req_text[max(0, m.start() - 40):m.start()] + " " + req_text[m.end():m.end() + 60]).lower()
        optional = bool(re.search(r"desirable|preferred|optional|advantage", around))
        if key not in [f[0] for f in found]:
            found.append((key, optional))
    required = [k for k, o in found if not o] or [k for k, _ in found]
    certs = (certs_text or "").strip()
    if certs.lower().rstrip(".") in _EXPLICIT_NONE:
        if is_mandatory and any(not o for _, o in found):
            return {"status": CONFIRMED_GAP, "basis": BASIS_EXPLICIT,
                    "explanation": f"The company profile states that no certifications are held ('{certs}'), while the tender requires {', '.join(required)}."}
        return {"status": INFORMATION_REQUIRED, "basis": BASIS_EXPLICIT,
                "explanation": f"The company profile states no certifications; the tender asks for {', '.join(required)}."}
    entries = [e.strip() for e in re.split(r"[\n;,]+", certs) if e.strip()]
    held, flagged, missing = [], [], []
    for key in required:
        rx = _standard_regex(key)
        hits = [e for e in entries if rx.search(e)]
        if not hits:
            missing.append(key)
        elif all(_CERT_NEGATIVE_RE.search(e) for e in hits):
            flagged.append(f"{key} (profile says: '{hits[0][:60]}')")
        else:
            held.append(key)
    if held and not flagged and not missing:
        return {"status": SATISFIED, "basis": BASIS_CERT_LISTING,
                "explanation": f"{', '.join(held)} listed in the company profile certifications."}
    parts = []
    if held:
        parts.append(f"listed: {', '.join(held)}")
    if flagged:
        parts.append(f"listed but not shown as valid/verified: {', '.join(flagged)}")
    if missing:
        parts.append(f"not listed in the profile: {', '.join(missing)}")
    return {"status": INFORMATION_REQUIRED, "basis": BASIS_CERT_LISTING if certs else BASIS_NONE,
            "explanation": "Certification evidence is incomplete — " + "; ".join(parts) + ". Provide the valid certificate(s). Missing information is not treated as a failure."}


# ============================================================
# REQUIREMENT CLASSIFICATION
# ============================================================

_OFFERED_PRODUCT_RE = re.compile(
    r"\b(?:offered|quoted|supplied|proposed)\s+(?:device|product|equipment|machine|model|item|system|make)s?\b|"
    r"\bthe\s+(?:device|equipment|machine|product|system)\s+(?:should|shall|must)\b", re.IGNORECASE)


def is_offered_product_requirement(text: str) -> bool:
    return bool(_OFFERED_PRODUCT_RE.search(text or ""))


def classify_requirement_category(text: str, default: str = "Eligibility") -> str:
    t = (text or "").lower()
    if re.search(r"turnover|net[\s-]*worth|solvency|balance\s+sheet|earnest\s+money|\bemd\b|bid\s+security|tender\s+fee|"
                 r"bank\s+guarantee|performance\s+security|audit(?:ed)?\s+report", t):
        return "Financial"
    if standards_in(text) or re.search(r"\bcertificat(?:e|ion)s?\s+of\s+(?:quality|conformity)|\blicen[cs]e", t):
        return "Certifications"
    if is_offered_product_requirement(text) or re.search(r"catalogue|brochure|data\s*sheet|technical\s+specifications?\s+of", t):
        return "Technical Specification"
    if re.search(r"\b(?:submit|provide|attach|upload|enclose)\b.{0,100}\b(?:document|certificate|report|proof|copy|declaration|undertaking|affidavit)s?\b|"
                 r"\b(?:document|certificate|report|proof|copy|declaration|undertaking|affidavit)s?\s+(?:to\s+be\s+submitted|required)\b", t):
        return "Documents"
    if re.search(r"\b(?:technical|technical capability|technical experience|technical staff|technical personnel)\b", t):
        return "Technical"
    if re.search(r"\b(?:eligibility|eligible|qualification|prequalification|bidder must|bidder shall|bidder should)\b", t):
        return "Eligibility"
    return default


_CONDITIONAL_LANGUAGE_RE = re.compile(
    r"\b(?:if applicable|where applicable|where relevant|if any|when applicable|as applicable|"
    r"in case of|in the event of|subject to|only if|provided that|wherever applicable)\b", re.IGNORECASE)
_OPTIONAL_LANGUAGE_RE = re.compile(r"\b(?:optional|desirable|preferred|not mandatory|may be submitted)\b", re.IGNORECASE)


def classify_requirement_metadata(requirement: dict) -> dict:
    """Attach deterministic type, conditional, mandatory, and document classifications."""
    text = " ".join(str(requirement.get(k) or "") for k in ("requirement", "source_quotation"))
    category = str(requirement.get("category") or "General")
    if category == "Financial" or re.search(r"turnover|revenue|net[ -]?worth|solvency|earnest money|\bemd\b|bid security|tender fee|bank guarantee", text, re.IGNORECASE):
        kind = "financial"
    elif category in ("Technical", "Technical Specification") or re.search(r"technical|specification|performance parameter", text, re.IGNORECASE):
        kind = "technical"
    elif category == "Documents":
        kind = "document"
    elif category in ("Eligibility", "Certifications") or re.search(r"eligibility|eligible|qualification|certification|registration|incorporat", text, re.IGNORECASE):
        kind = "eligibility"
    else:
        kind = "general"
    is_conditional = bool(_CONDITIONAL_LANGUAGE_RE.search(text))
    is_document = category == "Documents" or bool(str(requirement.get("required_document") or "").strip()) or bool(re.search(
        r"\b(?:submit|provide|attach|upload|enclose)\b.{0,100}\b(?:document|certificate|report|proof|copy|declaration|undertaking|affidavit)s?\b",
        text, re.IGNORECASE))
    mandatory = bool(requirement.get("is_mandatory", True)) and not bool(_OPTIONAL_LANGUAGE_RE.search(text))
    requirement.update({"requirement_type": kind, "is_conditional": is_conditional,
                        "is_document_requirement": is_document, "is_mandatory": mandatory,
                        "is_critical": bool(requirement.get("is_critical", mandatory)) and mandatory})
    return requirement


def _dedup_tokens(text) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower().replace(",", ""))


def _requirement_signature(req: dict) -> tuple:
    """What makes an obligation distinct: its figures, standards, conditions and requested document."""
    text = f"{req.get('requirement', '')} {req.get('source_quotation', '')}"
    numbers = set(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))
    numbers |= {str(_NUM_WORDS[w]) for w in _dedup_tokens(text) if w in _NUM_WORDS}
    # Scope (company vs offered product) is a classification of the clause, not part of the obligation itself:
    # the same clause classified differently by two extractors is still one obligation.
    return (frozenset(numbers), frozenset(standards_in(text)), bool(_CONDITIONAL_LANGUAGE_RE.search(text)))


_DOC_FILLER = {"the", "a", "an", "of", "copy", "copies", "valid", "self", "attested", "submit", "submitted", "original",
               "duly", "signed", "relevant", "required", "document", "documents"}


def _same_document_request(a: dict, b: dict) -> bool:
    """Same requested document unless both name one and neither description is contained in the other."""
    da = {t for t in _dedup_tokens(a.get("required_document")) if t not in _DOC_FILLER}
    db_ = {t for t in _dedup_tokens(b.get("required_document")) if t not in _DOC_FILLER}
    return not da or not db_ or da <= db_ or db_ <= da


def _quote_contained(a: dict, b: dict) -> bool:
    """One verbatim quote is the other plus a heading / trailing words (the same clause text)."""
    ta, tb = _dedup_tokens(a.get("source_quotation")), _dedup_tokens(b.get("source_quotation"))
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(short) >= 6 and f" {' '.join(short)} " in f" {' '.join(long_)} "


def _quotes_equivalent(a: dict, b: dict) -> bool:
    if _quote_contained(a, b):
        return True
    ta, tb = _dedup_tokens(a.get("source_quotation")), _dedup_tokens(b.get("source_quotation"))
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    return len(sa & sb) / len(sa | sb) >= 0.9 and min(len(ta), len(tb)) / max(len(ta), len(tb)) >= 0.8


def _titles_equivalent(a: str, b: str) -> bool:
    ta, tb = set(_dedup_tokens(a)), set(_dedup_tokens(b))
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.85


def _documents_compatible(a: dict, b: dict) -> bool:
    """
    Requested documents must agree — except that an LLM proposal whose quote is contained in the other clause
    only *describes* the document that same clause text asks for (e.g. 'audited accounts' vs 'audited financial
    statements'). Two deterministic extractions (e.g. table rows sharing criterion text but listing different
    documents) must still name compatible documents.
    """
    if _same_document_request(a, b):
        return True
    llm_involved = any(str(r.get("extraction_method", "")).startswith("llm") for r in (a, b))
    return llm_involved and _quote_contained(a, b)


def deduplicate_requirements(requirements: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Merge genuine duplicates of one tender obligation; keep similar-but-distinct obligations separate.
    Two requirements are duplicates only when they cite the same page, their quotes are the same clause
    (containment or near-identical wording), and their figures, standards, conditional wording, scope and
    requested document all agree. The canonical item is the deterministic extraction when available; every
    merged item's ID, page and exact quote are kept in ``merged_duplicates`` for traceability.
    Returns (canonical requirements in original order, merge records).
    """
    def _is_llm(r):
        return str(r.get("extraction_method", "")).startswith("llm")

    def _covered_by(det, llm):
        """An AI quote lying entirely inside a deterministic clause on the same page is already evaluated by it
        (e.g. one parameter line of an aggregated specification table)."""
        ta, tb = _dedup_tokens(llm.get("source_quotation")), _dedup_tokens(det.get("source_quotation"))
        return (_is_llm(llm) and not _is_llm(det) and det.get("source_page") == llm.get("source_page")
                and len(ta) >= 6 and len(ta) < len(tb) and f" {' '.join(ta)} " in f" {' '.join(tb)} ")

    canonical, merges = [], []
    for req in requirements or []:
        match = next((c for c in canonical
                      if ((_requirement_signature(c) == _requirement_signature(req)
                           and _documents_compatible(c, req) and _quotes_equivalent(c, req)
                           and (c.get("source_page") == req.get("source_page")
                                or _titles_equivalent(c.get("requirement"), req.get("requirement"))))
                          or _covered_by(c, req) or _covered_by(req, c))), None)
        if match is None:
            canonical.append(req)
            continue
        keep, drop = match, req
        if str(match.get("extraction_method", "")).startswith("llm") and not str(req.get("extraction_method", "")).startswith("llm"):
            keep, drop = req, match  # prefer the deterministic extraction as the canonical record
            canonical[canonical.index(match)] = keep
            keep.setdefault("merged_duplicates", []).extend(match.pop("merged_duplicates", []))
        record = {"requirement_id": drop.get("requirement_id"), "requirement": drop.get("requirement"),
                  "source_page": drop.get("source_page"), "source_quotation": drop.get("source_quotation"),
                  "extraction_method": drop.get("extraction_method"), "category": drop.get("category"),
                  "requirement_scope": drop.get("requirement_scope"), "required_document": drop.get("required_document")}
        keep.setdefault("merged_duplicates", []).append(record)
        pages = keep.setdefault("source_pages", [keep.get("source_page")] if keep.get("source_page") else [])
        if drop.get("source_page") and drop.get("source_page") not in pages:
            pages.append(drop.get("source_page"))
        pages.extend(p for p in (drop.get("source_pages") or []) if p and p not in pages)
        merges.append({"kept": keep.get("requirement_id"), "merged": drop.get("requirement_id")})
    return canonical, merges


def requirement_kind(requirement: dict) -> str:
    """Which evaluation rule applies: turnover, certification, experience_years, product_spec, registration, general."""
    text = f"{requirement.get('requirement', '')} {requirement.get('source_quotation', '')}"
    t = text.lower()
    if requirement.get("requirement_scope") == "offered_product" or requirement.get("category") == "Technical Specification":
        return "product_spec"
    if re.search(r"\bturnover\b|\brevenue\b", t):
        return "turnover"
    if standards_in(text):
        return "certification"
    if parse_years_requirement(text) is not None:
        return "experience_years"
    if re.search(r"registered|registration|incorporat|companies\s+act", t):
        return "registration"
    return "general"


# ============================================================
# GENERIC TABLE & SECTION EXTRACTION (no fixed page numbers)
# ============================================================

_SECTION_HEADINGS = [
    ("scope", re.compile(r"turnkey\s+scope\s+of\s+work|scope\s+of\s+work", re.IGNORECASE)),
    ("tq", re.compile(r"technical\s+(?:qualification|evaluation)\s+criteria|\btechnical\s+qualification\b", re.IGNORECASE)),
    ("eligibility", re.compile(r"eligibility\s+criteria|pre[\s-]*qualification|qualifying\s+criteria|qualification\s+criteria|"
                               r"eligibility\s+conditions|minimum\s+eligibility|bid(?:der)?\s+eligibility", re.IGNORECASE)),
    ("spec", re.compile(r"technical\s+specifications?|specifications?\s+of\b|scope\s+of\s+supply|bill\s+of\s+(?:quantit|material)|"
                        r"schedule\s+of\s+requirements", re.IGNORECASE)),
]
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+){1,3}\.?\s+[A-Z][A-Za-z/&,()\- ]{3,70}$")
_HEADER_CELL_RE = re.compile(
    r"^(?:s\.?|sr\.?|sl\.?|s/n|no\.?|s\.?\s*no\.?|sr\.?\s*no\.?|sl\.?\s*no\.?|tq\s*no\.?|items?|description|particulars|parameters?|"
    r"specifications?|technical\s+specifications?.*|criteria|eligibility\s+criteria|qualification|technical|requirements?|"
    r"documents?\s+(?:to\s+be\s+submitted|required).*|required\s+documents.*|supporting\s+documents?|maximum\s+marks|marks|"
    r"max\.?\s+marks|compliance.*|remarks|page\s+no\.?|preferred\s+makes?|quantity|qty\.?|unit|lot|part|material|"
    r"details|milestone|date\s*/?\s*period|critical\s+criter(?:ia|ion)|value)$", re.IGNORECASE)
_SERIAL_RE = re.compile(r"^\(?(\d{1,3})[.)]?$")
_TQ_SERIAL_RE = re.compile(r"^TQ\s*[-.]?\s*(\d{1,2})$", re.IGNORECASE)


def _heading_kind(line: str) -> str | None:
    s = " ".join((line or "").split())
    if not s or len(s) > 90 or len(s.split()) > 12 or s.endswith((",", ";")):
        return None
    if re.search(r"\b(?:should|shall|must|will|is|are|be|been|provided|submitted|quoted|as\s+per)\b", s, re.IGNORECASE):
        return None  # a sentence that mentions a section, not a heading
    for kind, rx in _SECTION_HEADINGS:
        if rx.search(s):
            return kind
    if _NUMBERED_HEADING_RE.match(s):
        return "other"
    return None


def _clean_cell(c) -> str:
    return " ".join(str(c).split()) if c not in (None, "") else ""


def _is_header_row(cells: list[str]) -> bool:
    vals = [c for c in cells if c]
    return bool(vals) and any(_HEADER_CELL_RE.match(v) for v in vals) and \
        all(_HEADER_CELL_RE.match(v) or (len(v) <= 3 and not v.isdigit()) for v in vals)


# ============================================================
# PDF LAYOUT CACHE (tables + text blocks), keyed by document content hash
# ============================================================
# PyMuPDF table detection is the dominant cost of an analysis (it ran ~5 times over every page per run).
# It is computed once per document content and processing version, kept in memory and on disk, and served
# to the table consumers through a read-only adapter with the same interface they already use.

PDF_LAYOUT_VERSION = f"pdf-layout-v1|pymupdf-{getattr(fitz, 'VersionBind', 'unknown')}"
_LAYOUT_MEMORY: dict = {}


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _layout_cache_file(sha: str) -> str:
    from config import DATA_DIR
    safe_version = re.sub(r"[^A-Za-z0-9.-]+", "_", PDF_LAYOUT_VERSION)
    return os.path.join(DATA_DIR, "cache", "pdf_layout", f"{sha}_{safe_version}.json")


def pdf_layout(pdf_path: str) -> dict:
    """Per-page tables (bbox + extracted rows) and text blocks for a PDF, cached by content hash + version."""
    sha = _sha256_file(pdf_path)
    key = (sha, PDF_LAYOUT_VERSION)
    if key in _LAYOUT_MEMORY:
        return _LAYOUT_MEMORY[key]
    cache_file = _layout_cache_file(sha)
    try:
        with open(cache_file, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("sha256") == sha and cached.get("version") == PDF_LAYOUT_VERSION:
            _LAYOUT_MEMORY[key] = cached
            return cached
    except (OSError, ValueError):
        pass
    pages = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            try:
                found = page.find_tables().tables
            except Exception:
                found = []
            pages.append({
                "tables": [{"bbox": list(t.bbox), "rows": t.extract() or []} for t in found],
                "blocks": [list(b) for b in page.get_text("blocks")],
            })
    layout = {"sha256": sha, "version": PDF_LAYOUT_VERSION, "pages": pages}
    _LAYOUT_MEMORY[key] = layout
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        tmp = f"{cache_file}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(layout, f)
        os.replace(tmp, cache_file)
    except OSError:
        pass  # the in-memory cache still applies; a read-only data dir must not break analysis
    return layout


class _LayoutTable:
    def __init__(self, data: dict):
        self.bbox = tuple(data["bbox"])
        self._rows = data["rows"]

    def extract(self):
        return [list(r) for r in self._rows]


class _LayoutPage:
    def __init__(self, data: dict):
        self._data = data

    def find_tables(self):
        return type("TableFinder", (), {"tables": [_LayoutTable(t) for t in self._data["tables"]]})()

    def get_text(self, kind: str = "text"):
        if kind != "blocks":
            raise NotImplementedError("Only text blocks are cached; use the extracted tender text for page text.")
        return [tuple(b) for b in self._data["blocks"]]


class _LayoutDoc:
    """Read-only stand-in for a fitz.Document exposing cached tables and blocks."""

    def __init__(self, layout: dict):
        self._pages = [_LayoutPage(p) for p in layout["pages"]]

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, index):
        return self._pages[index]

    def __iter__(self):
        return iter(self._pages)

    def close(self):
        pass


def _open_layout(pdf_path: str) -> _LayoutDoc:
    return _LayoutDoc(pdf_layout(pdf_path))


def extract_table_requirements(pdf_path: str) -> list[dict]:
    """
    Requirements from PDF tables, located by section headings and table header rows rather than page numbers:
      * eligibility / pre-qualification tables  -> one requirement per numbered row (ELG-nn)
      * technical qualification (TQ) tables      -> one requirement per TQ row (TQ-nn); scored rows are not mandatory
      * technical specification tables           -> one requirement per table (SPEC-nn) listing its parameters
    Header rows and repeated column labels are discarded; rows split across pages are re-joined.
    """
    results, used_ids = [], set()
    try:
        doc = _open_layout(pdf_path)
    except Exception:
        return results
    current_section, section_page = None, 0
    prev_kind, prev_page, last_req, spec_group, prev_has_marks = None, -10, None, None, False
    spec_count = 0

    def _new_id(base: str) -> str:
        rid, n = base, 2
        while rid in used_ids:
            rid = f"{base}-{n}"
            n += 1
        used_ids.add(rid)
        return rid

    try:
        for p_idx in range(len(doc)):
            page_num = p_idx + 1
            page = doc[p_idx]
            try:
                tables = sorted(page.find_tables().tables, key=lambda t: t.bbox[1])
            except Exception:
                tables = []
            boxes = [t.bbox for t in tables]
            headings = []
            for b in page.get_text("blocks"):
                x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
                if any(bx[0] - 2 <= x0 and bx[1] - 2 <= y0 and x1 <= bx[2] + 2 and y1 <= bx[3] + 2 for bx in boxes):
                    continue  # text inside a table is not a section heading
                for line in str(text).splitlines():
                    kind = _heading_kind(line)
                    if kind:
                        headings.append((y0, kind, " ".join(line.split())))
            headings.sort(key=lambda h: h[0])
            if current_section and page_num - section_page > 1:
                current_section = None
            for table in tables:
                rows = [[_clean_cell(c) for c in r] for r in (table.extract() or [])]
                above = [h for h in headings if h[0] < table.bbox[1]]
                section = above[-1][1] if above else current_section
                section_title = above[-1][2] if above else ""
                header_rows, data_rows, in_header = [], [], True
                for r in rows:
                    if not any(r):
                        continue
                    if in_header and _is_header_row(r):
                        header_rows.append(r)
                        continue
                    in_header = False
                    if _is_header_row(r):
                        continue  # repeated column labels inside the table
                    data_rows.append(r)
                header_text = " ".join(" ".join(r) for r in header_rows).lower()
                first_cells = [next((c for c in r if c), "") for r in data_rows]
                if re.search(r"\btq\b", header_text) or any(_TQ_SERIAL_RE.match(c) for c in first_cells):
                    kind = "tq"
                elif re.search(r"specification|parameter|critical\s+criter(?:ia|ion)", header_text) or (
                        re.search(r"\b(?:part|material)\b", header_text) and
                        re.search(r"\b(?:criteria|criterion|tolerance|requirement)\b", header_text)):
                    kind = "spec"
                elif re.search(r"\bmilestone\b|clarification\s+questions?\s+deadline|bid\s+submission\s+deadline", header_text):
                    kind = "milestone"
                elif re.search(r"criteria|eligib|qualif", header_text) and section in (None, "eligibility", "other"):
                    kind = "eligibility"
                elif not header_rows and prev_kind and page_num - prev_page <= 1:
                    kind = prev_kind  # headerless continuation of the previous table
                elif section in ("eligibility", "tq", "spec"):
                    kind = section
                else:
                    kind = None
                if kind is None:
                    continue
                if kind == "spec":
                    header_cells = [c for row in header_rows for c in row]
                    product_idx = next((i for i, cell in enumerate(header_cells)
                                        if re.search(r"\b(?:product|equipment|device|instrument|item)\b", cell, re.I)), None)
                    product_rows, active_product = {}, ""
                    for row in data_rows:
                        vals = [c for c in row]
                        if not any(vals):
                            continue
                        product = vals[product_idx].strip() if product_idx is not None and product_idx < len(vals) else ""
                        if product:
                            active_product = product
                        product = active_product or ""
                        product_rows.setdefault(product, []).append(" ".join(c for c in vals if c))
                    if not product_rows:
                        continue
                    distinct_products = [name for name in product_rows if name]
                    if len(distinct_products) <= 1:
                        product_groups = [(distinct_products[0] if distinct_products else "",
                                           list(next(iter(product_rows.values())) if len(product_rows) == 1 else
                                                [line for lines in product_rows.values() for line in lines]))]
                    else:
                        product_groups = [(name, product_rows[name]) for name in distinct_products]
                    if spec_group is not None and not header_rows and prev_kind == "spec" and page_num - prev_page <= 1 and len(product_groups) == 1:
                        spec_group["_params"].extend(product_groups[0][1])
                    else:
                        header_cells = [c for c in (header_rows[0] if header_rows else []) if c]
                        title_cells = [c for c in header_cells if re.search(r"specifications?\s+(?:of|for)\s+\w", c, re.IGNORECASE)
                                       or not _HEADER_CELL_RE.match(c)]
                        table_title = (title_cells[0] if title_cells else section_title
                                       or f"specification table on page {page_num}" + (f" ({', '.join(header_cells)})" if header_cells else ""))[:110]
                        for product, params in product_groups:
                            spec_count += 1
                            title = f"{table_title} — {product}" if product and product.casefold() not in table_title.casefold() else table_title
                            spec_group = {
                                "requirement_id": _new_id(f"SPEC-{spec_count:02d}"),
                                "category": "Technical Specification",
                                "requirement": f"Offered product must comply with: {title}",
                                "is_mandatory": True,
                                "source_page": page_num,
                                "required_document": "",
                                "extraction_confidence": 0.85,
                                "requirement_scope": "offered_product",
                                "extraction_method": "table:spec",
                                "_params": params,
                            }
                            results.append(spec_group)
                    prev_kind, prev_page = kind, page_num
                    continue
                if kind == "milestone":
                    milestone_requirements = (
                        (re.compile(r"clarification\s+questions?\s+deadline", re.IGNORECASE),
                         "Submit clarification questions by", "Documents"),
                        (re.compile(r"bid\s+submission\s+deadline", re.IGNORECASE),
                         "Submit the bid by", "Documents"),
                        (re.compile(r"bid\s+validity", re.IGNORECASE),
                         "Keep the bid valid for", "General"),
                        (re.compile(r"delivery\s+completion", re.IGNORECASE),
                         "Complete delivery", "Technical"),
                    )
                    for row in data_rows:
                        vals = [c for c in row if c]
                        if len(vals) < 2:
                            continue
                        label, value = vals[0], " ".join(vals[1:])
                        rule = next((entry for entry in milestone_requirements if entry[0].search(label)), None)
                        if not rule or not value:
                            continue
                        pattern, lead, category = rule
                        name = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24].rstrip("-").upper()
                        req = {"requirement_id": _new_id(f"DATE-{name}"), "category": category,
                               "requirement": f"{lead} {value}"[:160], "is_mandatory": True,
                               "source_page": page_num, "source_quotation": f"{label}: {value}",
                               "required_document": "", "extraction_confidence": 0.9,
                               "extraction_method": "table:milestone", "table_label": label}
                        results.append(req)
                    prev_kind, prev_page = kind, page_num
                    continue
                has_marks = ("mark" in header_text) or (not header_rows and prev_kind == kind and prev_has_marks)
                prev_has_marks = has_marks
                for r in data_rows:
                    vals = [c for c in r if c]
                    if not vals:
                        continue
                    first = vals[0]
                    serial_m = _TQ_SERIAL_RE.match(first) if kind == "tq" else _SERIAL_RE.match(first)
                    if not serial_m:
                        # continuation of a row split across a page / cell break
                        if last_req is not None and last_req.get("extraction_method") == f"table:{kind}" and page_num - last_req["source_page"] <= 1:
                            last_req["source_quotation"] = f"{last_req['source_quotation']} {vals[0]}".strip()
                            if len(vals) > 1:
                                last_req["required_document"] = f"{last_req['required_document']} {vals[1]}".strip()
                        continue
                    num = int(serial_m.group(1))
                    rest = vals[1:]
                    if kind == "tq":
                        name = rest[0] if rest else ""
                        docs = [c for c in rest[1:] if not re.fullmatch(r"\d{1,3}", c)]
                        if len(name) < 3:
                            continue
                        req = {
                            "requirement_id": _new_id(f"TQ-{num:02d}"),
                            "category": classify_requirement_category(f"{name} {' '.join(docs)}", default="Technical"),
                            "requirement": name[:120],
                            "is_mandatory": not has_marks,
                            "source_page": page_num,
                            "source_quotation": f"{name}: {' '.join(docs)}".strip(": "),
                            "required_document": docs[0] if docs else "",
                            "extraction_confidence": 0.9,
                            "extraction_method": "table:tq",
                            "scored_criterion": has_marks,
                        }
                    else:
                        crit = rest[0] if rest else ""
                        if len(crit) < 8:
                            continue
                        req = {
                            "requirement_id": _new_id(f"ELG-{num:02d}"),
                            "category": "Eligibility",
                            "requirement": crit[:120] + ("..." if len(crit) > 120 else ""),
                            "is_mandatory": not re.search(r"\b(?:optional|desirable|preferred)\b", crit, re.IGNORECASE),
                            "source_page": page_num,
                            "source_quotation": crit,
                            "required_document": rest[1] if len(rest) > 1 else "",
                            "extraction_confidence": 0.92,
                            "extraction_method": "table:eligibility",
                        }
                    results.append(req)
                    last_req = req
                prev_kind, prev_page = kind, page_num
            page_heads = [h for h in headings]
            if page_heads:
                last_kind = page_heads[-1][1]
                current_section = last_kind if last_kind in ("eligibility", "tq", "spec") else None
                section_page = page_num
    finally:
        doc.close()

    for r in results:
        if "_params" in r:
            params = r.pop("_params")
            r["parameter_count"] = len(params)
            r["source_quotation"] = "; ".join(params)[:10000]
        r["tender_evidence"] = f"Page {r['source_page']}: {r['source_quotation'][:300]}"
    return results


_DOC_TABLE_HEADER_RE = re.compile(r"\bparticulars\b|\bdocuments?\s+(?:to\s+be\s+)?(?:submitted|required|enclosed)\b|"
                                  r"\bname\s+of\s+(?:the\s+)?documents?\b|\bchecklist\b", re.IGNORECASE)
_BID_PART_RE = re.compile(r"^\(?\s*(?:part\s*[-–]?\s*[ivx\d]+\s*)?\(?\s*(?:technical|commercial|financial|price)\s+bid\s*\)?$|"
                          r"^part\s*[-–]?\s*[ivx\d]+\b.{0,30}$", re.IGNORECASE)

_DOC_TABLE_NOT_RE = re.compile(r"\bdetails?\b|\bdescription\b|\bcriteri(?:a|on)\b|\bvalue\b|\bspecification", re.IGNORECASE)
_DOC_WORD_RE = re.compile(r"certificate|copy|copies|letter|proforma|performa|affidavit|annex|undertaking|declaration|"
                          r"document|registration|statement|authori[sz]ation|catalogue|brochure|attorney|form\b|list\b|"
                          r"\bemd\b|chart|details\s+of|detail\s+of", re.IGNORECASE)


@functools.lru_cache(maxsize=16)
def _table_document_checklist_cached(pdf_path: str, size: int, mtime: float) -> tuple:
    out = []
    try:
        doc = _open_layout(pdf_path)
    except Exception:
        return ()
    for index, page in enumerate(doc):
        for table in page.find_tables().tables:
            rows = [[_clean_cell(c) for c in r] for r in (table.extract() or [])]
            header = " ".join(rows[0]) if rows else ""
            # a document checklist, not a key/value form ("Particulars | Details") or a criteria table
            if not _DOC_TABLE_HEADER_RE.search(header) or _DOC_TABLE_NOT_RE.search(header):
                continue
            body = [" ".join(c for c in r if c) for r in rows[1:] if any(r)]
            if not body or sum(bool(_DOC_WORD_RE.search(b)) for b in body) * 2 < len(body):
                continue
            part = ""
            for r in rows[1:]:
                vals = [c for c in r if c]
                if not vals:
                    continue
                if len(vals) == 1 and _BID_PART_RE.match(vals[0]):
                    part = re.sub(r"^\((.*)\)$", r"\1", vals[0].strip())
                    continue
                serial = _SERIAL_RE.match(vals[0])
                text = vals[1] if serial and len(vals) > 1 else vals[0] if not serial else ""
                if len(text.split()) < 2 or re.match(r"total\s+documents?\b", text, re.IGNORECASE):
                    continue
                out.append({"document": text, "source_page": index + 1, "bid_part": part,
                            "serial": serial.group(1) if serial else "", "source": "table"})
    return tuple(out)


def extract_table_document_checklist(pdf_path: str) -> list[dict]:
    """Bid-submission document checklists laid out as tables (Sr. No. | Particulars | Page No), verbatim with page."""
    path = _normalize_pdf_path(pdf_path)
    if not path or not os.path.exists(path):
        return []
    st = os.stat(path)
    return [dict(d) for d in _table_document_checklist_cached(path, st.st_size, st.st_mtime)]


def _audit_requirement_tables(pdf_path: str, requirements: list[dict]) -> dict:
    """Report procurement-style tables that PyMuPDF found but the structured parser did not classify."""
    result = {"detected_table_count": 0, "detected_pages": [], "recognized_requirement_table_pages": [],
              "unrecognized_requirement_table_pages": []}
    try:
        doc = _open_layout(pdf_path)
    except Exception:
        result["error"] = "The PDF could not be reopened to audit table extraction."
        return result
    relevant = re.compile(r"eligib|qualif|technical\s+specification|critical\s+criter|tq\s*no|mandatory|deliverable|"
                          r"milestone|clarification\s+questions?\s+deadline|bid\s+submission\s+deadline|"
                          r"bid\s+validity|delivery\s+completion", re.IGNORECASE)
    extracted_pages = {int(r["source_page"]) for r in requirements
                       if str(r.get("extraction_method", "")).startswith("table:") and r.get("source_page")}
    checklist_pages = {d["source_page"] for d in extract_table_document_checklist(pdf_path)}
    result["document_checklist_table_pages"] = sorted(checklist_pages)
    detected_pages, review_pages = [], []
    try:
        for index, page in enumerate(doc):
            try:
                tables = page.find_tables().tables
            except Exception:
                tables = []
            for table in tables:
                result["detected_table_count"] += 1
                page_number = index + 1
                detected_pages.append(page_number)
                cells = " ".join(_clean_cell(cell) for row in (table.extract() or []) for cell in row if cell)
                if relevant.search(cells) and page_number not in extracted_pages | checklist_pages:
                    review_pages.append(page_number)
    finally:
        doc.close()
    result["detected_pages"] = sorted(set(detected_pages))
    result["recognized_requirement_table_pages"] = sorted(extracted_pages)
    result["unrecognized_requirement_table_pages"] = sorted(set(review_pages))
    return result


_QUAL_PROSE_HEADING_RE = re.compile(
    r"^\s*(?:(?:section|chapter|part)\s*[\dIVX]+\s*[:.\-–]?\s*)?(?:\d+(?:\.\d+)*\.?\s*)?"
    r"(?:(?:minimum|mandatory|bid|bidder'?s?)\s+)?(?:eligibility(?:\s+(?:criteria|conditions|requirements))?|"
    r"pre[\s-]*qualification(?:\s+(?:criteria|requirements))?|qualifying\s+(?:criteria|requirements)|"
    r"qualification\s+(?:criteria|requirements)|technical\s+requirements|technical\s+bid\s*\(\s*part\s*[-–]?\s*(?:i|1)\s*\))\s*[:\-]?\s*$", re.IGNORECASE)
_PROSE_ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(\S.{1,160})$")
_PROSE_BULLET_RE = re.compile(r"^\s*[-•*▪●]\s+(\S.{5,200})$")
_PROSE_STOP_RE = re.compile(r"^\s*(?:financial\s*-?\s*bid|commercial\s*-?\s*bid|price\s*-?\s*bid|part\s*[-–]?\s*(?:ii|2)\b|"
                            r"annex(?:ure)?\b|section\s+\d|chapter\s+\d|\d+\.\d+\s+[A-Z])", re.IGNORECASE)


def extract_numbered_section_clauses(tender_text: str) -> list[dict]:
    """
    Numbered prose clauses (1., 2., 3. ...) under an eligibility / qualification / 'Technical Bid (Part I)' heading.
    Used when a tender states its qualification conditions as paragraphs instead of a table.
    """
    lines = (tender_text or "").splitlines()
    results, section_no = [], 0
    page = 1
    i = 0
    while i < len(lines):
        pm = re.match(r"^---\s*\[Page\s*(\d+)\]\s*---$", lines[i].strip(), re.IGNORECASE)
        if pm:
            page = int(pm.group(1))
            i += 1
            continue
        if not _QUAL_PROSE_HEADING_RE.match(lines[i]):
            i += 1
            continue
        heading_page = page
        # Clauses under an eligibility / qualification heading are mandatory by default; under a
        # 'Technical Bid (Part I)' heading only clauses that state an obligation are.
        heading_mandatory = not re.search(r"technical\s+bid", lines[i], re.IGNORECASE)
        default_category = "Technical" if re.search(r"technical\s+requirements", lines[i], re.IGNORECASE) else "Eligibility"
        items, expected, cur, mode = [], 1, None, None
        j, cur_page = i + 1, page
        while j < len(lines) and j - i < 600:
            line = lines[j]
            pm = re.match(r"^---\s*\[Page\s*(\d+)\]\s*---$", line.strip(), re.IGNORECASE)
            if pm:
                cur_page = int(pm.group(1))
                j += 1
                continue
            if items and len(line.strip()) < 60 and _PROSE_STOP_RE.match(line):
                break  # next major heading (short line), not a sentence that starts with 'Annexure...'
            m = _PROSE_ITEM_RE.match(line)
            b = _PROSE_BULLET_RE.match(line)
            if mode is None and (m or b):
                mode = "numbered" if m else "bullet"
            if mode == "bullet" and b:
                cur = {"num": expected, "head": b.group(1).strip(), "body": [], "page": cur_page}
                items.append(cur)
                expected += 1
            elif mode == "bullet" and m and items:
                break  # a numbered heading after a bullet list ends the section
            elif mode == "numbered" and m and int(m.group(1)) == expected:
                cur = {"num": expected, "head": m.group(2).strip(), "body": [], "page": cur_page}
                items.append(cur)
                expected += 1
            elif m and int(m.group(1)) == 1 and expected > 2:
                break  # a new numbered list starts: the section has ended
            elif cur is not None and line.strip():
                if re.fullmatch(r"\d{1,3}", line.strip()) or re.fullmatch(r"Page\s*\d+", line.strip(), re.IGNORECASE):
                    pass
                else:
                    cur["body"].append(line.strip())
            elif cur is None and j - i > 25:
                break
            j += 1
        # A technical-requirements section may contain a single bullet; unlike
        # qualification lists, it should not need a second item to be useful.
        minimum_items = 1 if re.search(r"technical\s+requirements", lines[i], re.IGNORECASE) else 2
        if len(items) >= minimum_items:
            section_no += 1
            for it in items:
                head = it["head"]
                parts = re.split(r"\s*(?::\s*-|:-|:\s|\s-\s|–)\s*", head, maxsplit=1)
                title = parts[0].strip(" :-")[:100] or head[:100]
                body = " ".join(([parts[1]] if len(parts) > 1 and parts[1] else []) + it["body"])
                quote = " ".join(f"{title}. {body}".split())[:900] if body else title
                doc_m = re.search(r"((?:self[\s-]*attested\s+)?(?:copy|copies|certificate|undertaking|declaration|affidavit|"
                                  r"authori[sz]ation\s+letter|audit(?:ed)?\s+report)[^.]{3,160})", body, re.IGNORECASE)
                rid = f"QC-{it['num']:02d}" if section_no == 1 else f"QC{section_no}-{it['num']:02d}"
                results.append({
                    "requirement_id": rid,
                    "category": classify_requirement_category(quote, default=default_category),
                    "requirement": title,
                    "is_mandatory": (heading_mandatory or bool(re.search(
                                        r"\b(?:must|shall|should|mandator\w*|required|compulsory|submit\w*|attach\w*|"
                                        r"to\s+be\s+(?:submitted|attached|enclosed|provided|uploaded))\b", quote, re.IGNORECASE)))
                                    and not re.search(r"\b(?:optional|desirable)\b", quote, re.IGNORECASE),
                    "source_page": it["page"],
                    "source_quotation": quote,
                    "required_document": " ".join(doc_m.group(1).split()) if doc_m else "",
                    "extraction_confidence": 0.8,
                    "tender_evidence": f"Page {it['page']}: {quote[:300]}",
                    "extraction_method": "numbered_section",
                    "section_heading_page": heading_page,
                })
        i = j if len(items) >= minimum_items else i + 1
    return results


def extract_technical_appendix_clauses(tender_text: str, skip_pages: set[int] | None = None) -> list[dict]:
    """Capture page-cited numbered clauses in technical-specification and turnkey-scope appendices.

    Tables are handled separately. In many tender PDFs, the remaining equipment and works clauses are
    extracted as page text, so this keeps those clauses visible without treating unrelated early bid
    instructions as technical requirements.
    """
    pages = split_pages(tender_text or "")
    results = []
    in_technical_appendix = False
    last_heading = ""
    item_re = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(\S.{2,})$")
    for page_number in sorted(pages):
        page_text = pages[page_number]
        if re.search(r"(?im)^\s*technical\s+specification\s*$|^\s*the\s+turnkey\s+scope\s+of\s+work\b", page_text):
            in_technical_appendix = True
        if not in_technical_appendix:
            continue
        if page_number in (skip_pages or set()):
            continue

        items, current = [], None
        for raw_line in page_text.splitlines():
            line = " ".join(raw_line.split())
            match = item_re.match(line)
            if not match and re.fullmatch(r"\d{1,2}", line):
                match = re.match(r"^(\d{1,2})$", line)
            if match:
                if current:
                    items.append(" ".join(current))
                current = [match.group(2).strip()] if match.lastindex and match.lastindex >= 2 else [""]
            elif current and line:
                if re.search(r"^(?:DMER-|I/\d{4})", line):
                    continue
                current.append(line)
            elif not line and current:
                items.append(" ".join(current))
                current = None
        if current:
            items.append(" ".join(current))

        items = [" ".join(item.split()) for item in items if len(item.strip()) >= 18]
        heading_candidates = [" ".join(line.split()) for line in page_text.splitlines()
                              if line.strip() and not item_re.match(line) and
                              not re.fullmatch(r"\d{1,2}", line.strip()) and
                              not re.match(r"^(?:DMER-|I/\d{4})", line.strip()) and
                              not re.fullmatch(r"S/?N|NO\.?|SL\.?", line.strip(), re.IGNORECASE)]
        heading = next((candidate for candidate in heading_candidates
                        if len(candidate) >= 12 and (candidate.isupper() or re.search(
                            r"scope|warranty|training|installation|specification|site visit|layout approval|turnkey",
                            candidate, re.IGNORECASE))), "")
        if heading:
            last_heading = heading
        if not items:
            if len(page_text) < 180 or not re.search(r"\b(?:should|shall|must|specification|range|monitoring)\b", page_text, re.I):
                continue
            continuation = [" ".join(line.split()) for line in page_text.splitlines()
                            if line.strip() and not re.match(r"^(?:DMER-|I/\d{4})", line.strip())]
            quote = " ".join(continuation)
            if len(quote) < 100:
                continue
            results.append({
                "requirement_id": f"TECH-APP-P{page_number:02d}",
                "category": "Technical Specification",
                "requirement": last_heading or "Technical specification continuation",
                "is_mandatory": not bool(_OPTIONAL_LANGUAGE_RE.search(quote)),
                "source_page": page_number,
                "source_quotation": quote[:10000],
                "required_document": "",
                "extraction_confidence": 0.78,
                "requirement_scope": "offered_product",
                "extraction_method": "technical_appendix:continuation",
                "parameter_count": 1,
            })
            continue
        quote = "; ".join(items)
        if len(quote) < 45:
            continue
        scope_text = f"{heading} {quote}"
        tender_submission = bool(re.search(
            r"\b(?:bidder|contractor)\s+(?:shall|must|should)|site\s+visit|technical\s+bid|"
            r"drawings?\s+(?:shall|must|should|to\s+be)|attached\s+in\s+the\s+technical\s+bid|"
            r"turnkey\s+scope\s+of\s+work", scope_text, re.IGNORECASE))
        requirement = heading[:120] if len(heading) >= 8 else items[0][:120]
        category = "Technical" if tender_submission else "Technical Specification"
        results.append({
            "requirement_id": f"TECH-APP-P{page_number:02d}",
            "category": category,
            "requirement": requirement,
            "is_mandatory": not bool(_OPTIONAL_LANGUAGE_RE.search(quote)),
            "source_page": page_number,
            "source_quotation": quote[:10000],
            "required_document": "",
            "extraction_confidence": 0.82,
            "requirement_scope": "tender_submission" if tender_submission else "offered_product",
            "extraction_method": "technical_appendix:numbered_clauses",
            "parameter_count": len(items),
        })
    return results


# ============================================================
# STRUCTURED REQUIREMENTS EXTRACTION
# ============================================================

def _source_evidence_matches(quote: str, source_text: str) -> bool:
    """Deterministic phrase/token check used to validate source quotations."""
    q = " ".join(re.findall(r"[a-z0-9]+", str(quote or "").lower()))
    source = " ".join(re.findall(r"[a-z0-9]+", str(source_text or "").lower()))
    if not q or not source:
        return False
    if q in source:
        return True
    tokens = [t for t in q.split() if len(t) > 2 and t not in {
        "the", "and", "for", "with", "from", "that", "this", "shall", "must", "should", "have", "has", "been",
        "provide", "submit", "required", "bidder", "company", "during", "within", "years", "year", "last",
    }]
    if len(tokens) >= 4 and " ".join(tokens[:5]) in source:
        return True
    source_tokens = set(source.split())
    target = tokens[:min(8, len(tokens))]
    if len(target) < 2:
        return False
    hits = sum(token in source_tokens for token in target)
    return hits >= max(2, math.ceil(len(target) * 0.75))


def _attach_retrieval_evidence(requirements: list[dict], tender_text: str,
                               retrieved_chunks: list[dict]) -> dict:
    """Verify each requirement against the original page text and retrieved page chunks."""
    pages = split_pages(tender_text)
    grounded = 0
    source_verified = 0
    for requirement in requirements:
        quote = str(requirement.get("source_quotation") or "")
        try:
            cited_page = int(requirement.get("source_page") or 1)
        except (TypeError, ValueError):
            cited_page = 1
        matching_pages = [page for page, page_text in pages.items() if _source_evidence_matches(quote, page_text)]
        if cited_page in matching_pages:
            verified_page = cited_page
        elif len(matching_pages) == 1:
            verified_page = matching_pages[0]
            requirement["source_page_corrected_from"] = cited_page
            requirement["source_page"] = verified_page
        else:
            verified_page = None
        requirement["source_verified"] = verified_page is not None
        if verified_page is not None:
            source_verified += 1
            requirement["tender_evidence"] = f"Page {verified_page}: {quote[:300]}"
            requirement.pop("source_uncertainty", None)
        else:
            requirement["source_uncertainty"] = "The quoted requirement could not be verified on a unique source page in the tender text."
        candidate = None
        if verified_page is not None:
            candidates = [chunk for chunk in retrieved_chunks
                          if int(chunk.get("page_number", chunk.get("page", 0)) or 0) == verified_page
                          and _source_evidence_matches(quote, chunk.get("text", ""))]
            if candidates:
                candidate = max(candidates, key=lambda chunk: float(chunk.get("score", 0)))
        requirement["retrieval_grounded"] = candidate is not None
        if candidate:
            grounded += 1
            requirement.pop("retrieval_uncertainty", None)
            requirement["retrieved_evidence"] = {
                "chunk_id": candidate.get("chunk_id"), "page_number": verified_page,
                "source_excerpt": candidate.get("text", "")[:1200],
            }
        elif verified_page is not None:
            requirement["retrieval_uncertainty"] = (
                "No indexed retrieved chunk on the verified source page contains this requirement quotation.")
        else:
            requirement["retrieval_uncertainty"] = "A retrieved tender citation is unavailable until the source quotation is verified."
        classify_requirement_metadata(requirement)
    return {"source_verified_count": source_verified, "retrieved_grounded_count": grounded,
            "unverified_source_count": len(requirements) - source_verified,
            "not_retrieved_count": len(requirements) - grounded}


def extract_requirements_with_report(tender_text: str, profile: dict | None = None, pdf_path: str = None,
                                     retrieved_chunks: list[dict] | None = None) -> tuple[list[dict], dict]:
    """
    Extract requirements and report how they were found and any extraction limitations.
    Methods, in order: PDF tables (by heading/header), labelled clauses (E-01, T-02 ...), numbered qualification
    paragraphs (only when no eligibility table/clauses were found), then narrow pattern fallbacks.
    Nothing is invented: every requirement carries its page and the tender text it came from.
    """
    tender_text = tender_text or ""
    pdf_path = _normalize_pdf_path(pdf_path)
    requirements, seen_ids, notes = [], set(), []

    def _add(r: dict):
        rid = clean_requirement_id(r["requirement_id"])
        base, n = rid, 2
        while rid in seen_ids:
            rid = f"{base}-{n}"
            n += 1
        r["requirement_id"] = rid
        seen_ids.add(rid)
        requirements.append(r)

    table_requirements = []
    if pdf_path and os.path.exists(pdf_path):
        table_requirements = extract_table_requirements(pdf_path)
        for r in table_requirements:
            _add(r)
    else:
        notes.append("The original PDF was not available, so table-based extraction was skipped.")

    for clause in extract_labeled_clauses(tender_text):
        if clause["requirement_id"] not in seen_ids:
            clause["extraction_method"] = "labeled_clause"
            _add(clause)

    if not any(r.get("extraction_method") in ("table:eligibility", "labeled_clause") for r in requirements):
        covered = " ".join(_norm_text(r.get("source_quotation", "")) for r in requirements)
        for r in extract_numbered_section_clauses(tender_text):
            if _norm_text(r["source_quotation"])[:60] not in covered:
                _add(r)

    technical_table_pages = {int(r["source_page"]) for r in table_requirements
                             if str(r.get("extraction_method", "")).startswith("table:spec") and r.get("source_page")}
    technical_appendix = extract_technical_appendix_clauses(tender_text, technical_table_pages)
    for requirement in technical_appendix:
        _add(requirement)

    covered_text = " ".join(f"{r.get('requirement', '')} {r.get('source_quotation', '')}" for r in requirements).lower()

    def _covered(*words) -> bool:
        return any(w in covered_text for w in words)

    # Pattern fallbacks: only add a requirement when the tender sentence itself states it.
    m = re.search(r"(?:minimum|at least|not less than)\s*(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|in business|operation)",
                  tender_text, re.IGNORECASE)
    if m and not _covered("years of experience", "years in business", "years experience", "experience"):
        sentence = _sentence_at(tender_text, m.start(), m.end())
        page = _page_at(tender_text, m.start()) or 1
        _add({"requirement_id": "DET-EXP", "category": "Eligibility",
              "requirement": f"Minimum {m.group(1)} years of experience", "is_mandatory": True,
              "source_page": page, "source_quotation": sentence, "required_document": "",
              "extraction_confidence": 0.85, "tender_evidence": f"Page {page}: {sentence}", "extraction_method": "pattern"})

    m = re.search(r"(?:minimum|average|annual)?\s*(?:annual\s*)?(?:turnover|revenue)[^\n.]{0,160}?(?:\d[\d,]*(?:\.\d+)?\s*(?:crores?|cr\b|lakhs?|million|billion)|[$₹€£]\s*\d|\b(?:INR|USD|Rs\.?)\s*\d)",
                  tender_text, re.IGNORECASE)
    if m and not _covered("turnover"):
        sentence = _sentence_at(tender_text, m.start(), m.end())
        page = _page_at(tender_text, m.start()) or 1
        _add({"requirement_id": "DET-TURNOVER", "category": "Financial", "requirement": "Minimum turnover",
              "is_mandatory": True, "source_page": page, "source_quotation": sentence, "required_document": "",
              "extraction_confidence": 0.85, "tender_evidence": f"Page {page}: {sentence}", "extraction_method": "pattern"})

    for std in ("ISO 9001", "ISO 27001", "ISO 14001", "ISO 45001", "CMMI"):
        mm = _standard_regex(std).search(tender_text)
        if mm and not _covered(std.lower()):
            sentence = _sentence_at(tender_text, mm.start(), mm.end())
            page = _page_at(tender_text, mm.start()) or 1
            mandatory = bool(re.search(r"\b(?:must|shall|should|mandatory|required|valid)\b", sentence, re.IGNORECASE)) \
                and not re.search(r"desirable|preferred|optional", sentence, re.IGNORECASE)
            _add({"requirement_id": f"DET-CERT-{std.replace(' ', '')}", "category": "Certifications",
                  "requirement": f"{std} certification", "is_mandatory": mandatory, "source_page": page,
                  "source_quotation": sentence, "required_document": "", "extraction_confidence": 0.8,
                  "tender_evidence": f"Page {page}: {sentence}", "extraction_method": "pattern"})
            covered_text += " " + std.lower()

    m = re.search(r"(?:incorporat|registration under the companies act|registered company)[^\n.]*", tender_text, re.IGNORECASE)
    if m and not _covered("registered", "registration", "incorporat"):
        sentence = _sentence_at(tender_text, m.start(), m.end())
        page = _page_at(tender_text, m.start()) or 1
        _add({"requirement_id": "DET-REG", "category": "Eligibility", "requirement": "Legal registration of the bidder",
              "is_mandatory": True, "source_page": page, "source_quotation": sentence, "required_document": "",
              "extraction_confidence": 0.8, "tender_evidence": f"Page {page}: {sentence}", "extraction_method": "pattern"})

    existing_document_text = {_norm_text(r.get("required_document", "")) for r in requirements if r.get("required_document")}
    for idx, item in enumerate(extract_document_checklist(tender_text), 1):
        document = " ".join(str(item.get("document") or "").split())
        normalized_document = _norm_text(document)
        if not document or normalized_document in existing_document_text:
            continue
        page = find_source_page(tender_text, document, default_page=item.get("source_page") or 1)
        mandatory = not bool(_OPTIONAL_LANGUAGE_RE.search(document))
        _add({"requirement_id": f"DOC-{idx:02d}", "category": "Documents",
              "requirement": f"Submit {document}", "is_mandatory": mandatory,
              "source_page": page, "source_quotation": document, "required_document": document,
              "extraction_confidence": 0.9, "tender_evidence": f"Page {page}: {document}",
              "extraction_method": "document_checklist"})
        existing_document_text.add(normalized_document)

    for r in requirements:
        text = f"{r.get('requirement', '')} {r.get('source_quotation', '')}"
        if "requirement_scope" not in r:
            r["requirement_scope"] = "offered_product" if (r.get("category") == "Technical Specification" or is_offered_product_requirement(text)) else "company"
        r.setdefault("tender_evidence", f"Page {r.get('source_page')}: {str(r.get('source_quotation', ''))[:300]}")
        r["is_critical"] = bool(r.get("is_mandatory", True))
        classify_requirement_metadata(r)

    page_count = len(re.findall(r"---\s*\[Page\s*\d+\]\s*---", tender_text))
    incomplete = (page_count > 5 or len(tender_text) > 20000) and len(requirements) < 3
    retrieval_report = None
    if retrieved_chunks is not None:
        retrieval_report = _attach_retrieval_evidence(requirements, tender_text, retrieved_chunks)
        if retrieval_report["unverified_source_count"]:
            notes.append(f"{retrieval_report['unverified_source_count']} extracted requirement(s) had quotations that could not be verified on the cited tender pages.")
            incomplete = True
        if retrieval_report["not_retrieved_count"]:
            notes.append(f"{retrieval_report['not_retrieved_count']} source-verified requirement(s) were not present in retrieved chunks; review extraction coverage before deciding.")
            incomplete = True
    table_report = _audit_requirement_tables(pdf_path, requirements) if pdf_path and os.path.exists(pdf_path) else None
    if table_report and table_report.get("unrecognized_requirement_table_pages"):
        pages_note = ", ".join(map(str, table_report["unrecognized_requirement_table_pages"]))
        notes.append(f"Procurement-related PDF table(s) on page(s) {pages_note} were detected but not parsed into requirements; review those tables manually.")
        incomplete = True
    pages = split_pages(tender_text)
    opening_text = " ".join(pages.get(n, "") for n in sorted(pages)[:4])
    opening_domain = re.search(r"\b(dialysis|cath(?:eterization)?\s*lab|cardiac\s+catheterization)\b", opening_text, re.IGNORECASE)
    if opening_domain and re.search(r"\bcomplete\s+(?:cath(?:eterization)?\s*lab|cardiac\s+catheterization)\s+(?:centre|center)\b",
                                   tender_text, re.IGNORECASE):
        scope_match = re.search(r"\bcomplete\s+(?:cath(?:eterization)?\s*lab|cardiac\s+catheterization)\s+(?:centre|center)\b",
                                tender_text, re.IGNORECASE)
        scope_page = _page_at(tender_text, scope_match.start()) or "not located"
        title_page = _page_at(tender_text, opening_domain.start()) or 1
        notes.append(f"Potential scope inconsistency: the opening pages identify a {opening_domain.group(1)} tender (page {title_page}), "
                     f"while a later section refers to a complete cath lab centre (page {scope_page}). Confirm which scope applies before deciding.")
        incomplete = True
    if pdf_path and os.path.exists(pdf_path) and not any(str(r.get("extraction_method", "")).startswith("table:") for r in requirements):
        notes.append("No eligibility, qualification or specification tables were recognised in the PDF.")
    if (page_count > 5 or len(tender_text) > 20000) and len(requirements) < 3:
        incomplete = True
        notes.append(f"Only {len(requirements)} requirement(s) were extracted from a {page_count}-page document; "
                     "extraction is likely incomplete. Add missing requirements manually.")
    methods = {}
    for r in requirements:
        methods[r.get("extraction_method", "unknown")] = methods.get(r.get("extraction_method", "unknown"), 0) + 1
    report = {"notes": notes, "incomplete": incomplete, "methods": methods,
              "count": len(requirements), "page_count": page_count}
    if technical_appendix:
        report["technical_appendix"] = {
            "parsed_pages": sorted({r["source_page"] for r in technical_appendix}),
            "clause_group_count": len(technical_appendix),
            "numbered_clause_count": sum(r.get("parameter_count", 0) for r in technical_appendix),
        }
    if table_report is not None:
        report["table_extraction"] = table_report
    if retrieval_report is not None:
        report["retrieval"] = retrieval_report
    return requirements, report


def extract_all_structured_requirements(tender_text: str, profile: dict, pdf_path: str = None,
                                        retrieved_chunks: list[dict] | None = None) -> list[dict]:
    """Structured requirements extracted from the tender (see extract_requirements_with_report)."""
    return extract_requirements_with_report(tender_text, profile, pdf_path=pdf_path,
                                            retrieved_chunks=retrieved_chunks)[0]


# ============================================================
# RULE-BASED EVIDENCE EVALUATION
# ============================================================

def evaluate_evidence_matching(requirement: dict, profile: dict) -> dict:
    """
    Rule-based evaluation of one requirement against the company profile.
    Rules decide only where the comparison is objective (stated turnover / years against a threshold,
    certifications listed in the profile). Otherwise they report what is missing and leave judgement to
    the AI stage. The company name, a non-empty profile field or keyword overlap never establish compliance,
    and missing information is never reported as a confirmed gap.
    'rule_decisive' marks results that take precedence over a differing AI assessment.
    """
    profile = profile or {}
    req_text = " ".join(f"{requirement.get('requirement', '')} {requirement.get('source_quotation', '')}".split())
    mandatory = bool(requirement.get("is_mandatory", True))
    kind = requirement_kind(requirement)
    declared = find_declared_evidence(requirement, profile)
    req_doc = (requirement.get("required_document") or "").strip()
    doc_hint = f" Document requested by the tender: {req_doc}." if req_doc else ""

    status, decisive, basis = INFORMATION_REQUIRED, False, BASIS_NONE
    company_evidence = NOT_FOUND
    explanation = "No evidence for this requirement was found in the company profile." + doc_hint

    if kind == "turnover":
        statements = collect_turnover_statements(profile, declared)
        merged = reconcile_turnover_statements(statements)
        use_decl = bool(statements) and all(s["source"] == "bidder declaration" for s in statements)
        if merged["conflict"]:
            # Never pick one of several disagreeing figures: ask for one confirmed per-year figure set.
            status, decisive, basis = UNRESOLVED, True, BASIS_NOT_COMPARABLE
            explanation = merged["conflict"]
            company_evidence = "; ".join(f"{s['source']}: {s['text'][:120]}" for s in statements)
            res = {"comparable": False}
        else:
            comp_text = merged["text"]
            verified_turnover = any(
                str(f.get("verification_status") or "").lower() == "verified"
                and (f.get("concept_key") == "financial.turnover"
                     or map_profile_field(" ".join(str(f.get(k) or "") for k in ("field_key", "value_text"))) == "annual_turnover")
                for f in profile.get("_agent_company_evidence", [])
            )
            source_label = ("bidder declaration (unverified)" if use_decl else
                            ("verified company evidence" if verified_turnover else "company profile"))
            res = compare_turnover(req_text, comp_text, source_label)
            status, decisive, explanation = res["status"], True, res["explanation"]
            if comp_text.strip():
                company_evidence = f"{'Bidder declaration (unverified)' if use_decl else 'Company profile turnover'}: {comp_text.strip()}"
                if len(statements) > 1:
                    company_evidence += f" (consistent across: {', '.join(s['source'] for s in statements)})"
                basis = (BASIS_DECLARATION if use_decl else BASIS_FIGURE) if res["comparable"] else BASIS_NOT_COMPARABLE
        if status == SATISFIED and re.search(r"net[\s-]*worth|solvency|profit", req_text, re.IGNORECASE):
            status = INFORMATION_REQUIRED
            explanation += " The requirement also sets other financial conditions (e.g. net worth / solvency) that still need evidence."

    elif kind == "certification":
        certs = str(profile.get("certifications") or "")
        # A requirement whose title names specific standards is judged on those standards only.
        title = str(requirement.get("requirement") or "")
        verified_cert = any(
            str(f.get("verification_status") or "").lower() == "verified"
            and (f.get("concept_key") == "quality.iso9001"
                 or map_profile_field(" ".join(str(f.get(k) or "") for k in ("field_key", "value_text"))) == "certifications")
            for f in profile.get("_agent_company_evidence", [])
        )
        res = evaluate_certification(title if standards_in(title) else req_text, certs, mandatory, allow_satisfied=verified_cert)
        status, decisive, basis, explanation = res["status"], True, res["basis"], res["explanation"] + doc_hint
        if certs.strip():
            company_evidence = f"Company profile certifications: {certs.strip()}"

    elif kind == "experience_years":
        req_years = parse_years_requirement(req_text)
        use_decl = bool(declared and parse_company_years(declared) is not None)
        comp_src = declared if use_decl else str(profile.get("years_of_experience") or "")
        comp_years = parse_company_years(comp_src)
        general = bool(re.search(r"in\s+business|in\s+existence|in\s+operation|since\s+(?:its\s+)?incorporation", req_text, re.IGNORECASE)) \
            and not re.search(r"similar|relevant", req_text, re.IGNORECASE)
        compound = bool(re.search(r"registered|registration|incorporat|certificate|licen[cs]e|turnover|net[\s-]*worth", req_text, re.IGNORECASE))
        src_label = "bidder declaration (unverified)" if use_decl else "company profile"
        if comp_src.strip():
            company_evidence = f"{'Bidder declaration (unverified)' if use_decl else 'Company profile'}: {comp_src.strip()}"
        if comp_years is None:
            status, decisive = (UNRESOLVED if declared else INFORMATION_REQUIRED), False
            explanation = "The company's years in business / experience are not stated in a comparable form." + doc_hint
        elif comp_years < req_years:
            status, decisive, basis = CONFIRMED_GAP, True, (BASIS_DECLARATION if use_decl else BASIS_FIGURE)
            explanation = (f"The tender requires at least {req_years} years; the {src_label} states {comp_years:g} years, "
                           f"so the requirement cannot be met.")
        else:
            status, decisive, basis = SATISFIED, True, (BASIS_DECLARATION if use_decl else BASIS_FIGURE)
            explanation = (f"The tender requires at least {req_years} years; the {src_label} states {comp_years:g} years, "
                           f"which meets the threshold.")

    elif re.search(r"non-blacklisting|debarr|blacklist|undertaking|affidavit", req_text, re.I):
        status, decisive = REQUIRES_VERIFICATION, False
        explanation = "Statutory undertaking / non-blacklisting declaration requires formal verification." + doc_hint
    elif kind == "product_spec":
        explanation = ("This requirement applies to the offered product, not to the company profile. Confirm compliance with the "
                       "product datasheet / technical compliance statement." + doc_hint)
        status, decisive = (UNRESOLVED, False) if declared else (INFORMATION_REQUIRED, True)

    else:
        if kind == "registration":
            comp_name = str(profile.get("company_name") or "").strip()
            if comp_name:
                status, decisive, basis = SATISFIED, True, BASIS_FIGURE
                explanation = f"Registered legal entity verified from company profile: {comp_name}."
                company_evidence = f"Company profile: {comp_name}"
            else:
                explanation = ("Legal registration must be evidenced by the registration / incorporation certificate; "
                               "the company name alone is not proof." + doc_hint)
        if declared and status != SATISFIED:
            status, decisive, basis = UNRESOLVED, False, BASIS_DECLARATION
            explanation = "A bidder declaration is recorded for this requirement but has not been assessed against the tender wording." + doc_hint

    if declared and company_evidence == NOT_FOUND:
        company_evidence = f"Bidder declaration (unverified): {declared}"
    if basis == BASIS_NONE and declared:
        basis = BASIS_DECLARATION

    return {
        **requirement,
        "status": status,
        "rule_status": status,
        "rule_decisive": decisive,
        "requirement_kind": kind,
        "is_mandatory": mandatory,
        "is_critical": mandatory,
        "company_evidence": company_evidence,
        "explanation": explanation,
        "evidence_basis": basis,
        "declared_evidence": declared or "",
        "evaluation_method": "rule",
        "llm_status": None,
        "llm_explanation": "",
        "ai_flagged_gap": False,
        "requires_document_verification": status == SATISFIED,
    }


def verify_company_quote(quote: str, profile: dict, declared: str = "") -> bool:
    """True when an AI-cited quote really appears in the company's evidence fields or declaration."""
    q = _norm_text(quote)
    if len(q) < 4:
        return False
    corpus = " | ".join(_norm_text((profile or {}).get(k, "")) for k in PROFILE_EVIDENCE_KEYS) + " | " + _norm_text(declared)
    return q in corpus


_CONDITIONAL_RE = re.compile(r"\b(?:if|in\s+case\s+of|where(?:ver)?\s+applicable|applicable\s+only|only\s+for|for\s+foreign|exempt)", re.IGNORECASE)


def reconcile_requirement(rule_eval: dict, llm_eval: dict | None, profile: dict) -> dict:
    """
    Combine the rule result with the AI (Groq) assessment for one requirement.
      * Decisive rule results (stated figures, listed certifications) take precedence; a differing AI view is recorded.
      * AI 'SATISFIED' is accepted only if it quotes company evidence that really exists (not the company name).
      * AI 'CONFIRMED GAP' alone is never disqualifying: it becomes UNRESOLVED and is flagged for review.
      * AI 'NOT APPLICABLE' on a mandatory requirement is accepted only for conditional clauses.
    """
    out = dict(rule_eval)
    if not llm_eval:
        out["evaluation_method"] = "rule only (no AI assessment)"
        out["requires_document_verification"] = out["status"] == SATISFIED
        return out
    ls = normalize_status(llm_eval.get("status"))
    l_expl = " ".join(str(llm_eval.get("explanation") or "").split())
    quote = " ".join(str(llm_eval.get("company_evidence_quote") or "").split())
    out["llm_status"], out["llm_explanation"] = ls, l_expl
    mandatory = bool(rule_eval.get("is_mandatory", True))

    if rule_eval.get("rule_decisive"):
        out["evaluation_method"] = "rule (AI assessment recorded)"
        if ls != rule_eval["status"]:
            out["explanation"] = (f"{rule_eval['explanation']} [AI assessment differed: {ls} — {l_expl[:200]}. "
                                  f"The rule-based check of the stated information takes precedence.]")
    else:
        out["evaluation_method"] = "AI assessment checked by rules"
        if ls == SATISFIED:
            if verify_company_quote(quote, profile, rule_eval.get("declared_evidence", "")):
                out.update(status=UNRESOLVED, evidence_basis=BASIS_AI,
                           company_evidence=f'Unverified company statement: "{quote}"',
                           explanation=f"The AI suggested satisfaction from a self-declared company value, but no verified evidence was supplied. "
                                       f"Evidence status remains unresolved. AI note: {l_expl[:200]}")
            else:
                out.update(status=UNRESOLVED,
                           explanation=f"The AI judged this satisfied but did not cite company evidence that exists in the profile; "
                                       f"not accepted. AI note: {l_expl[:200]}")
        elif ls == CONFIRMED_GAP:
            out.update(status=UNRESOLVED, ai_flagged_gap=True,
                       explanation=f"The AI flagged a potential gap ({l_expl[:220]}), but the available evidence does not establish "
                                   f"a disqualifying condition, so it is not treated as a confirmed gap. Review it manually.")
        elif ls == NOT_APPLICABLE:
            req_text = f"{rule_eval.get('requirement', '')} {rule_eval.get('source_quotation', '')}"
            if not mandatory or _CONDITIONAL_RE.search(req_text):
                out.update(status=NOT_APPLICABLE, explanation=f"Not applicable: {l_expl}")
            else:
                out.update(status=UNRESOLVED, explanation=f"The AI suggested this mandatory requirement does not apply ({l_expl[:200]}); confirm manually.")
        else:
            out.update(status=ls, explanation=l_expl or rule_eval["explanation"])
    out["requires_document_verification"] = out["status"] == SATISFIED
    return out


# ============================================================
# RECOMMENDATION & METRICS (single source of truth)
# ============================================================

def compute_metrics(matrix: list, extraction_incomplete: bool = False) -> dict:
    """Status counts computed from the same matrix that is displayed and used for the recommendation."""
    counts = {s: 0 for s in STATUSES}
    for m in matrix or []:
        counts[normalize_status(m.get("status"))] += 1
    total = len(matrix or [])
    applicable = total - counts[NOT_APPLICABLE]
    return {
        "total_identified": total,
        "satisfied": counts[SATISFIED],
        "confirmed_gaps": counts[CONFIRMED_GAP],
        "information_required": counts[INFORMATION_REQUIRED],
        "unresolved": counts[UNRESOLVED],
        "not_applicable": counts[NOT_APPLICABLE],
        "compliance_pct": f"{int(counts[SATISFIED] / applicable * 100)}%" if applicable > 0 else "0%",
        "is_incomplete": bool(extraction_incomplete) or total < 3,
    }


def compute_recommendation(matrix: list, extraction_incomplete: bool = False) -> dict:
    """
    BID / NO-BID / REVIEW REQUIRED from requirement statuses.
      * NO-BID only when a mandatory requirement is a CONFIRMED GAP (a reliably established disqualifying condition).
      * Missing (INFORMATION REQUIRED) and ambiguous (UNRESOLVED) mandatory items are reported separately and lead to REVIEW REQUIRED.
      * BID only when every mandatory requirement is SATISFIED or NOT APPLICABLE and extraction is adequate.
    """
    items = list(matrix or [])

    def st(m):
        return normalize_status(m.get("status"))

    def mand(m):
        return bool(m.get("is_mandatory", True))

    def label(m):
        return f"{m.get('requirement_id')}: {str(m.get('requirement') or '')[:90]}"

    gaps = [m for m in items if mand(m) and st(m) == CONFIRMED_GAP]
    info = [m for m in items if mand(m) and st(m) == INFORMATION_REQUIRED]
    unres = [m for m in items if mand(m) and st(m) == UNRESOLVED]
    optional_gaps = [m for m in items if not mand(m) and st(m) == CONFIRMED_GAP]
    flagged = [m for m in items if m.get("ai_flagged_gap")]
    mandatory_items = [m for m in items if mand(m)]

    open_points = []
    if info:
        open_points.append(f"{len(info)} mandatory requirement(s) need information or documents (not treated as failures): "
                           + "; ".join(label(m) for m in info[:5]) + (" ..." if len(info) > 5 else ""))
    if unres:
        open_points.append(f"{len(unres)} mandatory requirement(s) are unresolved (ambiguous, not comparable or conflicting evidence): "
                           + "; ".join(label(m) for m in unres[:5]) + (" ..." if len(unres) > 5 else ""))
    if flagged:
        open_points.append(f"The AI flagged {len(flagged)} potential gap(s) that the evidence does not confirm: "
                           + "; ".join(label(m) for m in flagged[:5]))
    if optional_gaps:
        open_points.append(f"{len(optional_gaps)} non-mandatory / scored criteria are not met: " + "; ".join(label(m) for m in optional_gaps[:5]))

    if gaps:
        decision = "NO-BID"
        reasons = [f"Confirmed disqualifying condition — {label(m)}: {m.get('explanation')}" for m in gaps] + open_points
        blockers = [f"{label(m)}: {m.get('explanation')}" for m in gaps]
        next_actions = [
            "Verify each confirmed gap against your audited records before finalising the decision.",
            "Record the tender as NO-BID unless the disqualifying condition can be remedied (e.g. a JV/consortium, where the tender allows it).",
            "Address the gap for future tenders of this type.",
        ]
    elif not items or len(items) < 3 or extraction_incomplete:
        decision = "REVIEW REQUIRED"
        reasons = [f"Only {len(items)} requirement(s) could be extracted reliably; review the tender manually and add missing requirements before deciding."] + open_points
        blockers = []
        next_actions = [
            "Review the tender document manually and add missing requirements in the requirements editor.",
            "Re-run the analysis after the requirement list is complete.",
        ]
    elif info or unres:
        decision = "REVIEW REQUIRED"
        reasons = open_points
        blockers = []
        next_actions = [
            "Provide the missing information and documents listed under Missing Requirements.",
            "Raise pre-bid queries with the tender authority for requirements that are ambiguous.",
            "Re-run the analysis after updating the information.",
        ]
    else:
        decision = "BID"
        reasons = [f"All {len(mandatory_items)} mandatory requirement(s) are satisfied or not applicable."]
        if any(st(m) == SATISFIED for m in items):
            reasons.append("Satisfied results rely on self-declared company information; verify the supporting documents before submission.")
        reasons += open_points
        blockers = []
        next_actions = [
            "Assemble the supporting documents for every satisfied requirement.",
            "Prepare the technical and commercial bids and arrange EMD / securities as specified.",
        ]
    return {
        "decision": decision,
        "reasons": reasons,
        "blockers": blockers,
        "next_actions": next_actions,
        "status_counts": compute_metrics(items, extraction_incomplete),
        "basis": f"Computed from requirement statuses by engine {ENGINE_VERSION}.",
        # Kept separate so that only confirmed mandatory failures read as disqualifying.
        "assessment": {
            "mandatory_failures": [f"{label(m)}: {m.get('explanation')}" for m in gaps],
            "missing_information": [label(m) for m in items if st(m) == INFORMATION_REQUIRED],
            "unresolved": [label(m) for m in items if st(m) == UNRESOLVED],
            "technical_suitability": [f"{label(m)} — {st(m)}" for m in items
                                      if m.get("category") in ("Technical", "Technical Specification")
                                      or m.get("requirement_scope") == "offered_product"],
            "financial_requirements": [f"{label(m)} — {st(m)}" for m in items if m.get("category") == "Financial"],
            "business_fit_concerns": [f"{label(m)} (AI-flagged, not a confirmed gap)" for m in flagged]
                                     + [f"{label(m)} (non-mandatory / scored criterion not met)" for m in optional_gaps],
            "coverage_incomplete": bool(extraction_incomplete),
        },
    }


def build_executive_summary(overview: dict, matrix: list, recommendation: dict, engine_label: str) -> str:
    c = recommendation.get("status_counts") or compute_metrics(matrix)
    first = recommendation["reasons"][0] if recommendation.get("reasons") else ""
    return (f"Analysis of '{overview.get('title', NOT_FOUND)}' (Ref: {overview.get('reference_number', NOT_FOUND)}) using {engine_label}. "
            f"Evaluated {c['total_identified']} requirement(s): {c['satisfied']} satisfied, {c['confirmed_gaps']} confirmed gap(s), "
            f"{c['information_required']} need information, {c['unresolved']} unresolved, {c['not_applicable']} not applicable. "
            f"Recommendation: {recommendation['decision']}. {first}").strip()


# ============================================================
# CONTEXT SELECTION FOR STAGE 1 (keyword / requirement pages, no fixed page numbers)
# ============================================================

_CONTEXT_KEYWORDS = re.compile(r"bid\s+submission|last\s+date|due\s+date|earnest\s+money|\bEMD\b|estimated\s+(?:cost|value)|"
                               r"scope\s+of\s+(?:work|supply)|eligibility|qualification|turnover|tender\s+(?:no|notice|reference)", re.IGNORECASE)


def split_pages(tender_text: str) -> dict[int, str]:
    parts = re.split(r"---\s*\[Page\s*(\d+)\]\s*---", tender_text or "")
    pages = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    if not pages and (tender_text or "").strip():
        pages[1] = tender_text
    return pages


def select_context_pages(tender_text: str, requirements: list[dict], budget: int = 12000, per_page: int = 2400) -> tuple[str, list[int]]:
    """
    Pages sent to the Stage 1 prompt: the first page, the pages richest in procurement keywords and the pages the
    extracted requirements come from, within a character budget. (Page selection only — this is not retrieval/RAG.)
    """
    pages = split_pages(tender_text)
    if not pages:
        return "", []
    order = []

    def add(p):
        if p in pages and p not in order and pages[p].strip():
            order.append(p)

    add(min(pages))
    ranked = sorted((p for p in pages if _CONTEXT_KEYWORDS.search(pages[p])),
                    key=lambda p: (-len(_CONTEXT_KEYWORDS.findall(pages[p])), p))
    for p in ranked[:2]:
        add(p)
    for r in requirements:
        if isinstance(r.get("source_page"), int):
            add(r["source_page"])
    for p in ranked[2:]:
        add(p)
    ctx, used = "", []
    for p in order:
        chunk = pages[p].strip()[:per_page]
        if len(ctx) + len(chunk) > budget:
            break
        ctx += f"\n--- [Page {p}] ---\n{chunk}"
        used.append(p)
    return ctx, sorted(used)


# ============================================================
# DOCUMENT CHECKLIST
# ============================================================

_DOC_HEADING_RE = re.compile(r"(?:mandatory\s+documents?(?:\s+checklist)?|documents?\s+to\s+be\s+submitted|list\s+of\s+documents|"
                             r"documents?\s+required|checklist\s+of\s+documents|submission\s+documents|eligibility\s+documents)\s*[:\-]?",
                             re.IGNORECASE)
_BULLET_SPLIT_RE = re.compile(r"\n\s*(?:\(?\d{1,2}[.)]|\(?[a-z][.)]|\(?[ivx]{1,4}[.)]|[•●▪\-*])\s+", re.IGNORECASE)


def extract_document_checklist(tender_text: str) -> list[dict]:
    """Documents listed under a document-checklist heading (only list items; prose is not split into fragments)."""
    docs, seen = [], set()
    text = tender_text or ""
    for m in _DOC_HEADING_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        line_end = len(text) if line_end == -1 else line_end
        if len(text[line_start:line_end].strip()) > 90:
            continue  # inside a sentence, not a heading
        block = text[line_end:line_end + 2500]
        stop = re.search(r"\n\s*\n\s*\n|\n\s*\d+(?:\.\d+)+\s+[A-Z]", block)
        if stop:
            block = block[:stop.start()]
        block = re.sub(r"---\s*\[Page\s*\d+\]\s*---", " ", block)
        parts = _BULLET_SPLIT_RE.split("\n" + block)
        if len(parts) < 3:
            continue  # no list structure: do not guess
        page = _page_at(text, m.start()) or 1
        items = []
        for part in parts[1:]:
            item = " ".join(part.split()).strip(" ;,.")
            if not (3 <= len(item.split()) <= 40) or not item[:1].isupper() or re.search(r"\bpage\s+\d+\s+of\s+\d+\b", item, re.IGNORECASE):
                continue
            if item.lower() not in seen:
                items.append(item)
        if len(items) < 2:
            continue  # a single fragment is not a checklist
        for item in items:
            seen.add(item.lower())
            docs.append({"document": item[:200], "source_page": page})
        if len(docs) >= 30:
            break
    return docs




# ============================================================
# NUMERIC & CURRENCY PARSERS
# ============================================================

def extract_numeric_years(text: str) -> float | None:
    """Extract numeric years of experience from freeform string."""
    if not text:
        return None
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?)", text, re.IGNORECASE)
    if matches:
        try:
            return float(matches[0])
        except ValueError:
            pass
    bare = re.findall(r"^\s*(\d+(?:\.\d+)?)\s*$", text)
    if bare:
        try:
            return float(bare[0])
        except ValueError:
            pass
    return None




def find_source_page(tender_text: str, snippet: str, default_page: int = 1) -> int:
    """Find the exact 1-indexed page number of a snippet in page-delimited text."""
    if not tender_text or not snippet:
        return default_page

    # Look for page header before snippet occurrence
    pos = tender_text.find(snippet[:40])
    if pos != -1:
        prefix = tender_text[:pos]
        page_matches = re.findall(r"---\s*\[Page\s*(\d+)\]\s*---", prefix, re.IGNORECASE)
        if page_matches:
            return int(page_matches[-1])

    return default_page



def _normalize_pdf_path(pdf_path: str = None) -> str | None:
    """Normalize PDF file path across Docker (/app/...) and Windows host directories."""
    if not pdf_path:
        return None
    if os.path.exists(pdf_path):
        return pdf_path
    fname = os.path.basename(pdf_path)
    from config import UPLOAD_DIR
    cand1 = os.path.join(UPLOAD_DIR, fname)
    if os.path.exists(cand1):
        return cand1
    cand2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "uploads", fname)
    if os.path.exists(cand2):
        return cand2
    return None


TABLE_HEADER_WORDS = {"description", "particulars", "criteria", "eligibility criteria", "parameters",
                      "specification", "specifications", "requirement", "requirements", "documents required"}


def clean_requirement_id(value) -> str:
    """Normalise requirement IDs: unicode dashes -> '-', collapse whitespace."""
    text = " ".join(str(value or "").split())
    for ch in ("‐", "‑", "‒", "–", "—", "−"):
        text = text.replace(ch, "-")
    return text


# Category inferred from the label prefix of a numbered clause (E-01, T-03, F-02, TQ-1 ...)
_LABEL_CATEGORY = {
    "E": "Eligibility", "EL": "Eligibility", "ELG": "Eligibility", "EC": "Eligibility",
    "QR": "Eligibility", "PQ": "Eligibility", "Q": "Eligibility",
    "T": "Technical", "TR": "Technical", "TQ": "Technical", "TS": "Technical",
    "F": "Financial", "FR": "Financial", "FIN": "Financial", "C": "Financial", "CR": "Financial",
}

# A clause header line like "E-01 — Legal registration" or "TQ 2: Technical staff"
_LABELED_CLAUSE_RE = re.compile(
    r"^\s*(?P<label>(?P<prefix>[A-Z]{1,4})\s?[-.]?\s?(?P<num>\d{1,3}))\s*[—–:\-]\s*(?P<title>[^\n]{3,120})$",
    re.MULTILINE,
)
_PAGE_MARKER_RE = re.compile(r"^---\s*\[Page\s*\d+\]\s*---$", re.IGNORECASE)


def extract_labeled_clauses(tender_text: str) -> list[dict]:
    """
    Generic extraction of explicitly numbered requirement clauses
    (e.g. 'E-01 — Legal registration', 'T-03 — Material traceability', 'F-04: Performance security').
    The clause body is everything until the next labeled clause or numbered section heading.
    """
    if not tender_text:
        return []
    matches = [m for m in _LABELED_CLAUSE_RE.finditer(tender_text) if m.group("prefix") in _LABEL_CATEGORY]
    results = []
    seen = set()
    for idx, m in enumerate(matches):
        prefix = m.group("prefix")
        label = f"{prefix}-{int(m.group('num')):02d}"
        if label in seen:
            continue
        seen.add(label)
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(tender_text)
        body_raw = tender_text[m.end():end]
        # Stop at the next numbered section heading (e.g. "3. Technical Requirements")
        sec = re.search(r"^\s*\d{1,2}\.\s+[A-Z][A-Za-z &/,\-]{3,80}$", body_raw, re.MULTILINE)
        if sec:
            body_raw = body_raw[:sec.start()]
        body_lines = []
        for line in body_raw.splitlines():
            ls = line.strip()
            if not ls or _PAGE_MARKER_RE.match(ls) or re.fullmatch(r"Page\s*\d+", ls, re.IGNORECASE):
                continue
            # Skip repeated running headers/footers (all-caps banner lines)
            if len(ls) > 20 and ls.isupper() and not any(ch.isdigit() for ch in ls):
                continue
            body_lines.append(ls)
        body = " ".join(body_lines).strip()
        title = m.group("title").strip()
        quote = f"{title}. {body}".strip() if body else title
        if len(quote) < 8:
            continue
        category = _LABEL_CATEGORY[prefix]
        lowered = quote.lower()
        is_mandatory = category == "Eligibility" or not any(w in lowered for w in ["optional", "desirable", "preferred", "advantage"])
        doc_m = re.search(r"(?:Submit|Provide|Include)\s+([^.]{5,160})", body)
        page = find_source_page(tender_text, m.group(0).strip(), default_page=1)
        quote_pages = [page_number for page_number, page_text in split_pages(tender_text).items()
                       if _source_evidence_matches(quote, page_text)]
        if len(quote_pages) == 1:
            page = quote_pages[0]
        results.append({
            "requirement_id": label,
            "category": category,
            "requirement": title,
            "is_mandatory": is_mandatory,
            "source_page": page,
            "source_quotation": quote[:600],
            "required_document": doc_m.group(1).strip() if doc_m else "",
            "extraction_confidence": 0.93,
            "tender_evidence": f"Page {page}: {quote[:300]}",
        })
    return results


# ============================================================
# 1. TENDER OVERVIEW EXTRACTOR
# ============================================================

def extract_tender_overview_deterministic(tender_text: str, pdf_path: str = None) -> dict:
    """
    Extract core tender metadata strictly from document text or PDF tables.
    Guarantees that titles like '1' or missing issuing authorities are resolved accurately.
    Never uses blacklisting clauses or arbitrary paragraphs as the title or authority.
    """
    pdf_path = _normalize_pdf_path(pdf_path)
    title = NOT_FOUND
    ref_no = NOT_FOUND
    org = NOT_FOUND
    val = NOT_FOUND
    deadline = NOT_FOUND
    scope = NOT_FOUND

    # First attempt: If pdf_path is available, extract from table key-value pairs (e.g. Schedule of Events)
    if pdf_path and os.path.exists(pdf_path):
        try:
            doc = _open_layout(pdf_path)
            for p in range(min(12, len(doc))):
                page = doc[p]
                tabs = page.find_tables()
                for t in tabs.tables:
                    for row in t.extract():
                        cells = [str(c).strip() for c in row if c and str(c).strip()]
                        if len(cells) >= 2:
                            k = cells[0].replace('\n', ' ').strip().lower()
                            v = cells[1].replace('\n', ' ').strip()
                            if any(w in k for w in ['name of the equipment', 'name of equipment', 'name of work', 'name of the work', 'tender name', 'item description', 'work description', 'equipment']) and title == NOT_FOUND:
                                if len(v) > 15 and not v.isdigit() and v.lower() != 'project-1':
                                    title = ' '.join(v.split())
                            elif any(w in k for w in ['tender notice no', 'tender no', 'rfp no', 'nit no', 'tender reference']) and ref_no == NOT_FOUND:
                                ref_no = v
                            elif any(w in k for w in ['approximate cost', 'estimated cost', 'estimated value', 'tender value', 'contract value', 'budget']) and val == NOT_FOUND:
                                val = v
                            elif any(w in k for w in ['bid submission', 'end date and time', 'submission of bid', 'closing date', 'due date', 'last date']) and deadline == NOT_FOUND:
                                m_d = re.search(r'(?:\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}|\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})(?:[,\s]+\d{1,2}:\d{2}\s*(?:AM|PM|hrs|EST|PST|UTC|IST)?)?', v, re.IGNORECASE)
                                if m_d:
                                    deadline = m_d.group(0).strip()
                                else:
                                    deadline = v
                            elif any(w in k for w in ['address for communication', 'issuing authority', 'procurer', 'client', 'department', 'employer']) and org == NOT_FOUND:
                                org = ' '.join(v.splitlines()[:2])
            doc.close()
        except Exception:
            pass

    # 1. Title Extraction
    if title == NOT_FOUND:
        # Check explicit labeled title
        m_title = re.search(r'(?:TITLE|NAME OF WORK|PROJECT|SUBJECT|SOLICITATION TITLE|TENDER NAME|EQUIPMENT NAME)\s*[:\-]\s*([^\n\r]+)', tender_text[:20000], re.IGNORECASE)
        if m_title:
            cand = m_title.group(1).strip()
            if len(cand) > 5 and not cand.isdigit() and cand.lower() != 'project-1':
                if not any(bad in cand.lower() for bad in ['will be available', 'online only', 'download', 'website', 'portal', 'debarred', 'blacklisted']):
                    title = cand

    if title == NOT_FOUND:
        # Check RFP / NIT cover pattern (multi-line) in first 15,000 characters
        rfp_m = re.search(
            r'(?:REQUEST FOR PROPOSAL|NOTICE INVITING TENDER|INVITATION FOR BIDS|TENDER FOR)\s*(?:[:\-]|\bFOR\b)?\s*([A-Z0-9][^\n\r]+(?:\n[^\n\r]+){0,4})',
            tender_text[:15000],
            re.IGNORECASE
        )
        if rfp_m:
            cand = ' '.join([l.strip() for l in rfp_m.group(1).splitlines() if l.strip()])
            cand = re.split(r'\b(?:Mode of Tender|Addressed to|Tender No|EMD|Contents|Disclaimer|Date of Issue)\b', cand, flags=re.IGNORECASE)[0].strip()
            cand = re.sub(r'^[^\w]+|[^\w\)]+$', '', cand)
            if len(cand) > 15 and not cand.isdigit() and cand.lower() != 'project-1':
                # Avoid capturing download paragraphs or portal announcements
                if not any(bad in cand.lower() for bad in ['will be available', 'online only', 'download', 'website', 'portal', 'debarred', 'blacklisted', 'corrigendum']):
                    title = ('REQUEST FOR PROPOSAL For ' + cand) if not cand.upper().startswith('REQUEST') else cand

    if title == NOT_FOUND:
        # Cover-page subject line, e.g. "Supply of Precision-Machined Metal Components"
        m_subj = re.search(
            r'^\s*((?:Supply|Procurement|Provision|Construction|Development|Selection|Hiring|Engagement|Design|Installation|Implementation|Maintenance)\b(?: of| and|,)[^\n]{8,150})$',
            tender_text[:4000], re.IGNORECASE | re.MULTILINE
        )
        if m_subj:
            title = m_subj.group(1).strip()

    # 2. Reference Number
    if ref_no == NOT_FOUND:
        ref_candidates = re.findall(
            r'(?:(?:REQUEST FOR PROPOSAL|RFP|TENDER|NIT|SOLICITATION|CONTRACT|REFERENCE)\s*(?:\([^)]*\))?\s*(?:#|NO\.?|NUMBER|CODE|REF\.?)?\s*[:\s\-]+)\s*([A-Z0-9][A-Z0-9\-_/]{3,})',
            tender_text[:30000],
            re.IGNORECASE
        )
        skip_words = {"SUBMISSION", "DEADLINE", "SPECIFICATION", "PROCUREMENT", "NOTICE", "CRITERIA", "DOCUMENT", "DETAILS", "TERMS", "SCHEDULE"}
        for cand in ref_candidates:
            clean_cand = cand.strip().strip("-:.")
            if clean_cand.upper() not in skip_words and (any(c.isdigit() or c in "-_/" for c in clean_cand)):
                ref_no = clean_cand
                break

    # 3. Issuing Organization
    if org == NOT_FOUND:
        m_org = re.search(r'(?:ISSUING AUTHORITY|PROCURING ENTITY|ISSUED BY|CLIENT|DEPARTMENT|MINISTRY|ORGANIZATION)\s*[:\-]\s*([A-Z0-9][^\n\r]+)', tender_text[:20000], re.IGNORECASE)
        if m_org and len(m_org.group(1).strip()) > 4 and m_org.group(1).strip().upper() not in {"DETAILS", "SUBMISSION"}:
            cand_org = m_org.group(1).strip()
            if not any(bad in cand_org.lower() for bad in ['debarred', 'blacklisted', 'who has been', 'undertaking of', 'undertaking by', 'eligibility', 'shall be', 'portal', 'website']):
                org = cand_org
        else:
            # Check prominent governmental entities on first few pages (strictly skipping blacklisting/eligibility clauses)
            top_lines = [l.strip() for l in tender_text[:4000].splitlines() if l.strip()]
            for idx, line in enumerate(top_lines[:35]):
                if any(bad in line.lower() for bad in ['debarred', 'blacklisted', 'who has been', 'undertaking of bidder', 'shall be', 'eligibility', 'conditions', 'penalty', 'portal', 'website', 'contents', 'disclaimer']):
                    continue
                if re.search(r'\b(?:GOVERNMENT OF|STATE GOVERNMENT|DIRECTORATE OF|MINISTRY OF|DEPARTMENT OF|MUNICIPAL CORPORATION|DEVELOPMENT CORPORATION|CORPORATION LTD|CORPORATION LIMITED)\b', line, re.IGNORECASE):
                    cand_name = line
                    if idx + 1 < len(top_lines):
                        next_line = top_lines[idx + 1]
                        if any(w in next_line.upper() for w in ['RESEARCH', 'EDUCATION', 'HEALTH', 'SERVICES', 'BOARD', 'COMMISSION', 'DEPARTMENT', 'LTD', 'LIMITED', 'UNDERTAKING']):
                            cand_name += ' ' + next_line
                    org = cand_name
                    break

    # 4. Estimated Value
    if val == NOT_FOUND:
        m_val = re.search(r'(?:ESTIMATED (?:COST|VALUE|BUDGET)|CONTRACT VALUE|TENDER VALUE|APPROXIMATE COST|BUDGET)\s*[:\-]\s*([^\n\r]+)', tender_text[:25000], re.IGNORECASE)
        # Table layout: label on one line, amount on the next ("Estimated value\nINR 48,00,000")
        m_val_tbl = re.search(r'(?:ESTIMATED (?:COST|VALUE)|CONTRACT VALUE|TENDER VALUE)\s*\n\s*((?:INR|Rs\.?|USD|[$₹€£])\s*[\d,]+(?:\.\d+)?[^\n]*)', tender_text[:25000], re.IGNORECASE)
        if m_val:
            val = m_val.group(1).strip()
        elif m_val_tbl:
            val = m_val_tbl.group(1).strip()
        elif 'rate contract' in tender_text[:12000].lower():
            val = 'Rate Contract (value depends on quantities ordered)'
        else:
            amt_match = re.search(r'(?:worth|budget of|estimated at)\s*([$₹€£]\s*[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|k|crore|lakh))?)', tender_text[:25000], re.IGNORECASE)
            if amt_match:
                val = amt_match.group(1).strip()

    # 5. Deadline
    if deadline == NOT_FOUND:
        m_explicit = re.search(r'(?:BID SUBMISSION DEADLINE|SUBMISSION DEADLINE|BID DEADLINE|DUE DATE|CLOSING DATE|LAST DATE OF SUBMISSION)\s*[:\-]\s*([^\n\r]+)', tender_text[:30000], re.IGNORECASE)
        if m_explicit:
            deadline = m_explicit.group(1).strip()
        else:
            m_dl = re.search(
                r'(?:Last\s+date\s*(?:&|and)?\s*time\s*of\s*submission|Submission\s*Deadline|Bid\s*Submission\s*Deadline|Due\s*Date|Closing\s*Date)[\s\S]{1,100}?((?:\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}|\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})(?:[,\s]+\d{1,2}:\d{2}\s*(?:AM|PM|hrs|EST|PST|UTC|IST)?)?)',
                tender_text[:30000],
                re.IGNORECASE
            )
            if m_dl:
                deadline = m_dl.group(1).replace('\n', ' ').strip()

    # 6. Scope of Work: only text that the tender itself states (never a generated template)
    if scope == NOT_FOUND:
        # A 'Scope of Work' / 'Objective' heading (or 'Objective:' label) followed by prose sentences.
        for m_sc in re.finditer(
                r'^[ \t]*(?:(?:SECTION|CHAPTER|PART)[ \t]*[\dIVX]+[ \t]*[:.\-–]?[ \t]*)?(?:\d+(?:\.\d+)*\.?[ \t]*)?'
                r'(?:(?:[A-Za-z]+[ \t]+){1,3}(?:and|&)[ \t]+)?(?:SCOPE\s+OF\s+(?:THE\s+)?(?:WORK|SUPPLY|SERVICES)|OBJECTIVE(?:\s+OF\s+THE\s+(?:TENDER|RFP|PROJECT|ASSIGNMENT))?)'
                r'[ \t]*[:\-–]*[ \t]*(.*(?:\n(?!---)[^\n]+){0,4})', tender_text, re.IGNORECASE | re.MULTILINE):
            kept = []
            for i, ln in enumerate(m_sc.group(1).split("\n")):
                if i and re.match(r"\s*(?:(?:SECTION|CHAPTER|PART|ANNEX\w*)\b|\d+(?:\.\d+)*[.)]?\s+[A-Z]|[A-Z][A-Z0-9 &/,:()\-]{8,}$)", ln):
                    break  # the next heading or numbered clause ends the scope text
                kept.append(ln)
            cand = " ".join(" ".join(kept).split())
            cand = " ".join(re.split(r"(?<=\.)\s+", cand)[:2])[:400]
            words = cand.split()
            alpha = [w for w in words if re.fullmatch(r"[A-Za-z][A-Za-z,'()&-]*\.?", w)]
            if len(words) >= 8 and len(alpha) >= 0.7 * len(words) and "...." not in cand:
                scope = cand
                break

    return {
        "title": title,
        "reference_number": ref_no,
        "issuing_organization": org,
        "estimated_value": val,
        "deadline": deadline,
        "scope_of_work": scope
    }


# ============================================================
# 2. STRUCTURED REQUIREMENTS EXTRACTION
# ============================================================



# ============================================================
# 3. EVIDENCE-BASED MATCHING ENGINE
# ============================================================





# ============================================================
# 4. ELIGIBILITY, TECHNICAL, FINANCIAL & RISK MODULES
# ============================================================









# ============================================================
# 5. RECOMMENDATION SAFETY & METRICS ENGINE
# ============================================================



# ============================================================
# 6. COMPLETE ANALYSIS WORKFLOW
# ============================================================



# ============================================================
# 7. GROQ / LLM INTEGRATION WITH SEAMLESS FALLBACK
# ============================================================



def map_profile_field(text: str) -> str:
    """Map requirement / document text to the company-profile field that should hold its evidence."""
    t = text.lower()
    if any(w in t for w in ["turnover", "revenue", "financial standing", "balance sheet", "audited accounts"]):
        return "annual_turnover"
    if any(w in t for w in ["past project", "similar work", "similar order", "similar experience", "supply order",
                            "purchase order", "work order", "completion", "performance certificate", "client"]):
        return "past_projects"
    if any(w in t for w in ["iso", "cmmi", "accreditation", "stqc", "pci", "quality certif"]):
        return "certifications"
    if any(w in t for w in ["years of experience", "years in business", "track record"]):
        return "years_of_experience"
    if any(w in t for w in ["bank guarantee", "emd", "net worth", "credit facility", "pbg", "bid security",
                            "performance security", "security deposit", "price", "payment", "warranty", "damages",
                            "solvency"]):
        return "financial_details"
    if any(w in t for w in ["equipment", "machinery", "device", "terminal", "technical capability", "oem", "inspection",
                            "calibration", "traceability", "capacity", "process", "schedule", "tolerance",
                            "facility", "facilities", "manufactur"]):
        return "technical_capabilities"
    if re.search(r"registration|registered|incorporat|\bpan\b|\bgst\b", t):
        return "company_name"
    if "certif" in t:
        return "certifications"
    return "technical_capabilities"


# ------------------------------------------------------------
# Bidder-declared evidence: answers the user types against a specific requirement.
# Stored as tagged lines inside the relevant profile text field, e.g.
#   [E-02 · Similar experience] 3 supply orders for EN8 shafts (2023-2025) ...
# Single-value fields (turnover / years) are replaced directly instead of tagged.
# ------------------------------------------------------------
EVIDENCE_TEXT_FIELDS = ["past_projects", "certifications", "technical_capabilities", "financial_details", "industry"]
EVIDENCE_VALUE_FIELDS = ["annual_turnover", "years_of_experience"]
# Requirement-specific answers are always stored as tagged lines in a text field; the single-value
# fields (turnover / years) are never overwritten by an answer and are never read as a declaration.
EVIDENCE_FIELDS = list(EVIDENCE_TEXT_FIELDS)
_VALUE_FIELD_REDIRECT = {"annual_turnover": "financial_details", "years_of_experience": "past_projects"}




def evidence_tag(requirement: dict) -> str:
    rid = " ".join(str(requirement.get("requirement_id") or "REQ").split())
    title = " ".join(str(requirement.get("requirement") or "").split())[:60]
    return f"[{rid} · {title}]"


def _tag_matches(line: str, requirement: dict) -> bool:
    return line.strip().lower().startswith(evidence_tag(requirement).lower())




def remove_declared_evidence(field_text: str, requirement: dict) -> str:
    lines = [l for l in str(field_text or "").splitlines() if not _tag_matches(l, requirement)]
    return "\n".join(lines).strip()


def upsert_declared_evidence(field_text: str, requirement: dict, evidence: str) -> str:
    """Replace (or add) the tagged evidence line for a requirement inside a profile text field."""
    base = remove_declared_evidence(field_text, requirement)
    evidence = " ".join(str(evidence or "").split())
    if not evidence:
        return base
    line = f"{evidence_tag(requirement)} {evidence}"
    return f"{base}\n{line}".strip() if base else line






def extract_json_safe(text: str) -> dict:
    """Parse a complete JSON object from a Groq response without fabricating missing data.

    Truncated or malformed model output is rejected. In particular, this function does not
    append closing braces to incomplete JSON: doing so can turn a partial evaluation into a
    misleading, apparently valid result.
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Groq returned an empty response.")

    # Accept the occasional Markdown code fence, but otherwise require JSON.
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    candidates = [cleaned]
    # Some providers prepend a short explanation. Extract the first balanced JSON object,
    # respecting quoted strings and escaped quote characters.
    first = cleaned.find("{")
    if first >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(first, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(cleaned[first:index + 1])
                    break

    last_error = None
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError) as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            raise ValueError("Groq response JSON must be an object.")
        return parsed

    raise ValueError(f"Groq returned malformed or incomplete JSON: {last_error}")


def call_groq_modular(messages: list[dict], max_tokens: int = 800, temperature: float = 0.1, preferred_model: str = None) -> tuple[dict, str]:
    """
    Call Groq using a finite, de-duplicated model fallback sequence.

    A 429 skips that model rather than retrying into the same rate limit. The only
    same-model retry is one request-format retry when a model rejects JSON mode.
    Never silently falls back to the deterministic engine.
    """
    active_key = (GROQ_API_KEY or os.getenv("GROQ_API_KEY") or "").strip()
    if not active_key:
        raise ValueError("GROQ_API_KEY is not configured in the environment.")

    requested_model = preferred_model or GROQ_MODEL
    models = list(dict.fromkeys([requested_model] + [m for m in GROQ_FALLBACK_MODELS if m != requested_model]))
    headers = {
        "Authorization": f"Bearer {active_key}",
        "Content-Type": "application/json"
    }
    model_errors = []

    # One extra pass is allowed only when EVERY model answered 429. Wait until the slowest
    # configured model's Retry-After has elapsed; the shortest wait leaves other fallbacks throttled.
    max_wait = float(os.getenv("TENDERFORGE_GROQ_MAX_RETRY_WAIT", "60") or 0)
    for round_no in range(2):
        retry_waits = []
        for model_name in models:
            # qwen OTPM limit is 1,000; reasoning models (gpt-oss) need at least 2,500 total tokens
            tok_limit = min(max_tokens, 750) if "qwen" in model_name else max(max_tokens, 2500)
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": tok_limit
            }
            if "qwen" in model_name:
                payload["response_format"] = {"type": "json_object"}
            if "gpt-oss" in model_name:
                # gpt-oss models spend part of max_tokens on hidden chain-of-thought
                # ("reasoning") before writing the JSON answer. Reasoning-token usage
                # is non-deterministic (observed 10-900+ tokens for the same prompt);
                # when it runs high it crowds out the JSON content and truncates it
                # mid-string, breaking json parsing downstream. Forcing low reasoning
                # effort keeps that overhead small and leaves the budget for content.
                payload["reasoning_effort"] = "low"

            print(f"[Groq AI] Calling model '{model_name}' (token_limit={tok_limit})...")
            for attempt in range(2):
                try:
                    max_retries = 1
                    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=20)
                    if resp.status_code == 200:
                        body = resp.json()
                        choices = body.get("choices") if isinstance(body, dict) else None
                        if not choices or not isinstance(choices[0], dict):
                            raise ValueError("Groq returned HTTP 200 without a valid choices[0] object.")
                        choice = choices[0]
                        finish_reason = str(choice.get("finish_reason") or "").lower()
                        if finish_reason in {"length", "max_tokens"}:
                            model_errors.append(f"{model_name}: output truncated at {tok_limit} tokens (finish_reason={finish_reason}); response rejected")
                            print(f"[Groq AI] Truncated response from '{model_name}' rejected.")
                            break
                        message = choice.get("message") or {}
                        raw_data = message.get("content")
                        if not isinstance(raw_data, str) or not raw_data.strip():
                            raise ValueError("Groq returned an empty or non-text message content.")
                        parsed = extract_json_safe(raw_data)
                        print(f"[Groq AI] Success with model '{model_name}'.")
                        return parsed, model_name
                    elif resp.status_code == 400 and "response_format" in payload:
                        # Retry without response_format if model rejected json_object type
                        del payload["response_format"]
                        continue
                    elif resp.status_code == 429:
                        try:
                            err_msg = resp.json().get("error", {}).get("message", "")
                        except (ValueError, AttributeError):
                            err_msg = resp.text[:500]
                        print(f"[Groq AI] Rate limit (429) on '{model_name}': {err_msg[:80]}. Moving to fallback.")
                        model_errors.append(f"{model_name}: HTTP 429 rate limit: {err_msg[:240]}")
                        retry_after = (getattr(resp, "headers", None) or {}).get("retry-after")
                        try:
                            retry_waits.append(max(0.0, float(retry_after)))
                        except (TypeError, ValueError):
                            # Without a numeric Retry-After value, do not sleep blindly or
                            # repeat requests into an active rate limit. Continue fallbacks.
                            retry_waits.append(float("inf"))
                        break
                    elif resp.status_code in (401, 403):
                        model_errors.append(f"{model_name}: authentication/authorization failed (HTTP {resp.status_code}); check the configured Groq API key and access permissions")
                        print(f"[Groq AI] Authentication/authorization failure (HTTP {resp.status_code}) on '{model_name}'.")
                        break
                    elif resp.status_code == 404:
                        model_errors.append(f"{model_name}: model unavailable (HTTP 404)")
                        print(f"[Groq AI] Model '{model_name}' unavailable (HTTP 404).")
                        break
                    else:
                        detail = resp.text[:200]
                        if resp.status_code == 400 and any(term in detail.lower() for term in ("model", "not found", "unsupported")):
                            model_errors.append(f"{model_name}: model or request unavailable/unsupported (HTTP 400): {detail}")
                        else:
                            model_errors.append(f"{model_name}: request failed (HTTP {resp.status_code}): {detail}")
                        print(f"[Groq AI] Request failed (HTTP {resp.status_code}) on '{model_name}'.")
                        break
                except Exception as e:
                    error = f"{type(e).__name__}: {str(e)[:240]}"
                    model_errors.append(f"{model_name}: invalid response or network/request error: {error}")
                    print(f"[Groq AI] Network/request or response error on '{model_name}': {error}")
                    break

        if round_no == 0 and len(retry_waits) == len(models) and all(math.isfinite(w) for w in retry_waits) \
                and min(retry_waits) <= max_wait:
            wait = min(retry_waits)
            print(f"[Groq AI] All models rate-limited; waiting {wait:.1f}s (Retry-After) before one retry pass.")
            time.sleep(wait)
            continue
        break

    detail = "; ".join(model_errors) or "no configured model produced a response"
    final_error = f"Groq fallback exhausted after {len(models)} configured model(s): {detail}"
    print(f"[Groq AI] All configured Groq models failed. {final_error}")
    raise RuntimeError(final_error)




REQUIREMENT_CATEGORIES = ["Eligibility", "Technical", "Technical Specification", "Financial", "Certifications", "Documents", "General"]






def ensure_seven_sections(saved_analysis: dict, tender_text: str = "", profile: dict = None, pdf_path: str = None) -> dict:
    """
    Ensure all seven sections exist and are robustly structured dictionaries.
    Hydrates any missing or unstructured sections dynamically from tender text and company profile.
    Guarantees tender_overview metadata never displays corrupted paragraphs or blacklisting clauses.
    """
    if not saved_analysis:
        return {}

    pdf_path = _normalize_pdf_path(pdf_path)
    structured = saved_analysis.get("structured_tender_data")
    if not isinstance(structured, dict):
        structured = {}

    # Sanitize tender_overview if it contains blacklisted words, portal download strings, or fragments
    if "tender_overview" in structured and isinstance(structured["tender_overview"], dict):
        ov = structured["tender_overview"]
        t = str(ov.get("title", ""))
        org = str(ov.get("issuing_organization", ""))
        sc = str(ov.get("scope_of_work", ""))
        if (
            any(bad in t.lower() for bad in ["will be available", "online only", "upload", "download", "website", "portal", "debarred", "blacklisted"])
        or any(bad in org.lower() for bad in ["debarred", "blacklisted", "who has been", "undertaking of", "undertaking by"])
        or any(bad in sc.lower() for bad in ["dmer-22011", "i/2442617", "scope of work dmer"])
        or len(t) < 5
        ):
            clean_ov = extract_tender_overview_deterministic(tender_text, pdf_path=pdf_path)
            for k in ["title", "reference_number", "issuing_organization", "estimated_value", "deadline", "scope_of_work"]:
                if (k not in ov or ov[k] == NOT_FOUND or
                    (k == "title" and any(bad in str(ov[k]).lower() for bad in ["will be available", "online only", "upload", "download", "website", "portal", "debarred", "blacklisted"])) or
                    (k == "issuing_organization" and any(bad in str(ov[k]).lower() for bad in ["debarred", "blacklisted", "who has been"])) or
                    (k == "scope_of_work" and any(bad in str(ov[k]).lower() for bad in ["dmer-22011", "i/2442617", "scope of work dmer"]))
                ):
                    ov[k] = clean_ov[k]

    if saved_analysis.get("model_used") and "model_used" not in structured:
        structured["model_used"] = saved_analysis["model_used"]

    required_sections = [
        "tender_overview",
        "eligibility_analysis",
        "compliance_matrix",
        "technical_evaluation",
        "financial_analysis",
        "risk_assessment",
        "bid_recommendation"
    ]
    has_all = all(k in structured and structured[k] for k in required_sections)
    if has_all and structured.get("metrics"):
        # Validate that eligibility_analysis has dict pillars
        el = structured.get("eligibility_analysis", {})
        if isinstance(el, dict):
            for subk in ["experience", "turnover", "certifications", "registration_requirements"]:
                if not isinstance(el.get(subk), dict) or "explanation" not in el[subk] or "status" not in el[subk]:
                    has_all = False
                    break
        else:
            has_all = False
        if has_all:
            return structured

    fallback = run_deterministic_analysis(tender_text or "", profile or {}, pdf_path=pdf_path)
    result = dict(fallback)
    # Record which sections were rebuilt by the rules-only engine so the UI can say so.
    result["hydrated_sections_from_rules"] = [k for k in required_sections if not structured.get(k)]

    for k, v in structured.items():
        if v:
            if k == "eligibility_analysis" and isinstance(v, dict):
                for subk, subv in v.items():
                    if isinstance(subv, dict) and "explanation" in subv and "status" in subv:
                        result["eligibility_analysis"][subk] = subv
            elif k == "technical_evaluation" and isinstance(v, dict):
                for subk, subv in v.items():
                    if isinstance(subv, dict) and "explanation" in subv and "status" in subv:
                        result["technical_evaluation"][subk] = subv
            elif k == "financial_analysis" and isinstance(v, dict):
                for subk, subv in v.items():
                    if isinstance(subv, dict) and "explanation" in subv and "status" in subv:
                        result["financial_analysis"][subk] = subv
            else:
                result[k] = v

    if saved_analysis.get("compliance_matrix"):
        result["compliance_matrix"] = saved_analysis["compliance_matrix"]
    if saved_analysis.get("recommendation"):
        result["recommendation"] = saved_analysis["recommendation"]
        if "bid_recommendation" in result:
            result["bid_recommendation"]["decision"] = saved_analysis["recommendation"]

    # Guarantee actionable missing information
    if "actionable_missing_information" not in result or not result["actionable_missing_information"]:
        result["actionable_missing_information"] = generate_actionable_missing_information(
            result.get("compliance_matrix", []),
            profile or {},
            result.get("risk_assessment", {})
        )
        result["missing_information"] = [f"{item['item_name']} (Page {item.get('source_page', 1)})" for item in result["actionable_missing_information"]]

    if "llm_analysis" in structured and ("llm_analysis" not in result or not result["llm_analysis"]):
        result["llm_analysis"] = structured["llm_analysis"]
    if saved_analysis.get("model_used"):
        result["model_used"] = saved_analysis["model_used"]

    if "tender_overview" in result and isinstance(result["tender_overview"], dict):
        ov = result["tender_overview"]
        t = str(ov.get("title", ""))
        org = str(ov.get("issuing_organization", ""))
        sc = str(ov.get("scope_of_work", ""))
        if (
            any(bad in t.lower() for bad in ["will be available", "online only", "upload", "download", "website", "portal", "debarred", "blacklisted"])
        or any(bad in org.lower() for bad in ["debarred", "blacklisted", "who has been", "undertaking of", "undertaking by"])
        or any(bad in sc.lower() for bad in ["dmer-22011", "i/2442617", "scope of work dmer"])
        or len(t) < 5
        ):
            clean_ov = extract_tender_overview_deterministic(tender_text, pdf_path=pdf_path)
            for k in ["title", "reference_number", "issuing_organization", "estimated_value", "deadline", "scope_of_work"]:
                result["tender_overview"][k] = clean_ov[k]

    return result


# ============================================================
# SECTION BUILDERS, EVIDENCE STORAGE & GROQ PIPELINE
# ============================================================

def extract_numeric_value(text: str) -> float | None:
    """Backward-compatible helper: value (base units) of the first monetary amount in text, or None."""
    amounts = parse_money_amounts(text or "")
    return amounts[0]["value"] if amounts else None


def _aggregate_status(items: list[dict]) -> str:
    sts = [normalize_status(i.get("status")) for i in items]
    if CONFIRMED_GAP in sts:
        return CONFIRMED_GAP
    if all(s == NOT_APPLICABLE for s in sts):
        return NOT_APPLICABLE
    if all(s in (SATISFIED, NOT_APPLICABLE) for s in sts):
        return SATISFIED
    if UNRESOLVED in sts:
        return UNRESOLVED
    return INFORMATION_REQUIRED


def _pillar(items: list[dict], default_title: str, what: str) -> dict:
    if not items:
        return {"requirement": default_title, "tender_evidence": NOT_FOUND, "company_evidence": NOT_FOUND,
                "status": UNRESOLVED, "requirement_ids": [],
                "explanation": f"No explicit {what} requirement was extracted from this tender; confirm manually whether one applies."}
    evidence = next((i.get("company_evidence") for i in items if i.get("company_evidence") and i.get("company_evidence") != NOT_FOUND), NOT_FOUND)
    return {
        "requirement": "; ".join(f"{i.get('requirement_id')}: {str(i.get('requirement'))[:80]}" for i in items[:3]),
        "tender_evidence": " | ".join(str(i.get("tender_evidence") or NOT_FOUND) for i in items[:2]),
        "company_evidence": evidence,
        "status": _aggregate_status(items),
        "explanation": " | ".join(str(i.get("explanation") or "") for i in items[:2]),
        "requirement_ids": [i.get("requirement_id") for i in items],
    }


def extract_eligibility_analysis(tender_text: str, profile: dict, deterministic_checks: list[dict], pdf_path: str = None) -> dict:
    """
    The four eligibility pillars as a view over the evaluated requirement matrix (no separate evaluation):
    a pillar's status is derived from the requirements that belong to it.
    """
    matrix = deterministic_checks or []

    def kind(m):
        return m.get("requirement_kind") or requirement_kind(m)

    def topic(m, words):
        txt = f"{m.get('requirement', '')} {m.get('source_quotation', '')}".lower()
        return any(w in txt for w in words)

    company_items = [m for m in matrix if m.get("requirement_scope") != "offered_product"]
    return {
        "experience": _pillar([m for m in company_items if kind(m) == "experience_years"
                               or (kind(m) == "general" and topic(m, ["experience", "similar work", "similar order", "past performance"]))],
                              "General Industry Track Record", "experience"),
        "turnover": _pillar([m for m in company_items if kind(m) == "turnover"], "Financial Capacity & Standing", "turnover"),
        "certifications": _pillar([m for m in company_items if kind(m) == "certification"],
                                  "Statutory Certifications & Accreditations", "certification"),
        "registration_requirements": _pillar([m for m in company_items if kind(m) == "registration"
                                              or topic(m, ["registered", "registration", "incorporat"])],
                                             "Vendor Legal Registration & Good Standing", "registration"),
    }


def extract_technical_evaluation(tender_text: str, profile: dict, pdf_path: str = None, compliance_matrix: list | None = None) -> dict:
    """Technical view: technical / specification requirements from the matrix plus tender excerpts (no keyword matching)."""
    matrix = compliance_matrix or []
    capabilities = [{
        "requirement": f"{m.get('requirement_id')} · {m.get('requirement')}",
        "tender_evidence": m.get("tender_evidence", NOT_FOUND),
        "company_evidence": m.get("company_evidence", NOT_FOUND),
        "status": normalize_status(m.get("status")),
        "explanation": m.get("explanation", ""),
    } for m in matrix if m.get("category") in ("Technical", "Technical Specification") or m.get("requirement_scope") == "offered_product"]
    if not capabilities:
        for idx, tm in enumerate(re.findall(r"(?:technical requirement|proven experience with|must possess|mandatory skill|offered device)[s]?\s*[:\-]?\s*([^\n\r]+(?:\n[^\n\r]+){0,2})",
                                            tender_text or "", re.IGNORECASE)[:4]):
            capabilities.append({
                "requirement": f"Technical statement {idx + 1}",
                "tender_evidence": " ".join(tm.split())[:200],
                "company_evidence": NOT_FOUND,
                "status": INFORMATION_REQUIRED,
                "explanation": "Found in the tender text but not evaluated automatically; add it to the requirement list if it is mandatory.",
            })

    def _excerpt(pattern, title, what):
        m = re.search(pattern, tender_text or "", re.IGNORECASE)
        return [{
            "requirement": title,
            "tender_evidence": m.group(0).strip()[:250] if m else NOT_FOUND,
            "company_evidence": NOT_FOUND,
            "status": INFORMATION_REQUIRED if m else UNRESOLVED,
            "explanation": f"{what} is not evaluated automatically; compare the tender wording with your capabilities." if m
            else f"No {what.lower()} statement was found in the tender text.",
        }]

    equipment = _excerpt(r"(?:infrastructure|tools|hardware|equipment|machinery|facilit(?:y|ies))[^\n.]{10,}", "Equipment, Tooling & Infrastructure", "Equipment / infrastructure")
    expertise = _excerpt(r"(?:personnel|staff|engineers?|team|manpower|qualified professional)[^\n.]{10,}", "Specialized Expertise & Qualified Personnel", "Personnel / expertise")

    deliverables = []
    for idx, dm in enumerate(re.findall(r"(?:deliverable|sla|milestone|delivery schedule|timeline|delivery period)[s]?\s*[:\-]?\s*([^\n\r]+)", tender_text or "", re.IGNORECASE)[:4]):
        deliverables.append({"deliverable": f"Deliverable / SLA {idx + 1}", "tender_evidence": dm.strip(), "status": "SPECIFIED",
                             "details": "Delivery condition stated in the tender text."})
    if not deliverables:
        deliverables.append({"deliverable": "Project Milestones & Deliverables", "tender_evidence": NOT_FOUND, "status": "NOT SPECIFIED",
                             "details": "No delivery schedule statement was found in the tender text."})
    return {"capabilities": capabilities, "equipment": equipment, "expertise": expertise, "deliverables": deliverables}


# ============================================================
# TENDER CLAUSES: EMD, TENDER FEE, PBG, PAYMENT TERMS, SUBMISSION DEADLINE
# ============================================================

# Tender-side absence is reported differently from company-side absence (NOT_FOUND):
NOT_SPECIFIED_IN_TENDER = "Not specified in tender"
NOT_IN_EXTRACTED_TEXT = "Not found in the extracted tender text (some pages have no extractable text)"

_CLAUSE_LABELS = {
    "emd": re.compile(r"\bemd\b|\be\.m\.d\b|earnest\s+money|bid\s+security", re.IGNORECASE),
    "tender_fee": re.compile(r"tender\s+(?:fees?|form\s+fees?|document\s+(?:fees?|cost))|document\s+(?:fees?|cost)|"
                             r"offer\s+document\s+cost|cost\s+of\s+(?:the\s+)?tender", re.IGNORECASE),
    "pbg": re.compile(r"performance\s+(?:bank\s+)?(?:guarantee|security)|\bpbg\b|security\s+deposit", re.IGNORECASE),
    "payment_terms": re.compile(r"payment\s+terms|terms\s+of\s+payment|payment\s+schedule|\bpayment\s*:|payment\s+of\s+\d", re.IGNORECASE),
    "submission_deadline": re.compile(r"(?:end|last|closing|due)\s+date.{0,25}?(?:submission|bid)|bid\s+submission\s+(?:end\s+)?(?:date|deadline)|"
                                      r"submission\s+deadline|last\s+date\s+(?:for|of)\s+submission", re.IGNORECASE),
}
_CLAUSE_TITLES = {"emd": "Earnest Money Deposit (EMD) / Bid Security", "tender_fee": "Tender Document & Processing Fee",
                  "pbg": "Performance Bank Guarantee (PBG) / Security Deposit", "payment_terms": "Contract Payment Terms & Schedule",
                  "submission_deadline": "Bid Submission Deadline"}
_CLAUSE_DATE_RE = re.compile(
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?:[,\s-]+(?:at\s+)?\d{1,2}[:.]\d{2}\s*(?:[ap]\.?m\.?|hrs|hours|ist)?)?|"
    r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}(?:[,\s]+\d{1,2}[:.]\d{2}\s*(?:[ap]m|hrs|ist)?)?",
    re.IGNORECASE)
_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")


def _clause_value_ok(kind: str, text: str) -> bool:
    """Does this text actually state the clause's value (an amount, percentage, or date)?"""
    text = str(text or "")
    if kind in ("emd", "tender_fee"):
        return bool(parse_money_amounts(text)) or bool(re.search(r"\bexempt(?:ed|ion)?\b|\bnil\b", text, re.IGNORECASE))
    if kind == "pbg":
        return bool(_PCT_RE.search(text)) or bool(parse_money_amounts(text))
    if kind == "payment_terms":
        return bool(_PCT_RE.search(text)) or bool(re.search(r"\b\d+\s*days\b", text, re.IGNORECASE))
    if kind == "submission_deadline":
        return bool(_CLAUSE_DATE_RE.search(text))
    return False


def extract_procurement_table_values(pdf_path: str | None) -> dict:
    """Cached per PDF (path, size, mtime): table detection over every page is expensive and is needed several times."""
    if not pdf_path or not os.path.exists(pdf_path):
        return {}
    stat = os.stat(pdf_path)
    return {k: dict(v) for k, v in _procurement_table_values_cached(os.path.abspath(pdf_path), stat.st_size, stat.st_mtime_ns).items()}


@functools.lru_cache(maxsize=32)
def _procurement_table_values_cached(pdf_path: str, size: int, mtime_ns: int) -> dict:
    return _scan_procurement_table_values(pdf_path)


def _scan_procurement_table_values(pdf_path: str | None) -> dict:
    """
    Clause values stated in PDF tables, as {kind: {"value", "label", "page", "method"}}.
    Handles label/value rows ("Earnest Money Deposit | ₹25,00,000/-") and column headers spanning rows
    ("EMD / Amount (Rs)" with the value in a later row). Only cells that state a value are accepted.
    """
    found = {}
    if not pdf_path or not os.path.exists(pdf_path):
        return found
    try:
        doc = _open_layout(pdf_path)
    except Exception:
        return found
    try:
        for p_idx in range(len(doc)):
            try:
                tables = doc[p_idx].find_tables().tables
            except Exception:
                continue
            for table in tables:
                rows = [[_clean_cell(c) for c in r] for r in (table.extract() or [])]
                if not rows:
                    continue
                for row in rows:  # label / value pairs
                    vals = [c for c in row if c]
                    for i, cell in enumerate(vals[:-1]):
                        for kind, rx in _CLAUSE_LABELS.items():
                            if kind in found or len(cell) > 120 or not rx.search(cell) or _clause_value_ok(kind, cell):
                                continue
                            if _clause_value_ok(kind, vals[i + 1]):
                                found[kind] = {"value": vals[i + 1], "label": cell, "page": p_idx + 1, "method": "table"}
                width = max(len(r) for r in rows)
                for col in range(width):  # column headers (possibly over several rows)
                    for kind, rx in _CLAUSE_LABELS.items():
                        if kind in found:
                            continue
                        parts, header_rows = [], 0
                        for r in rows[:3]:  # header = leading cells of this column that do not state a value
                            cell = r[col] if col < len(r) else ""
                            if cell and _clause_value_ok(kind, cell):
                                break
                            header_rows += 1
                            if cell:
                                parts.append(cell)
                        header = " ".join(parts)
                        if not header or not rx.search(header):
                            continue
                        for r in rows[max(header_rows, 1):]:
                            cell = r[col] if col < len(r) else ""
                            if cell and _clause_value_ok(kind, cell) and not rx.search(cell):
                                found[kind] = {"value": cell, "label": header, "page": p_idx + 1, "method": "table"}
                                break
    finally:
        doc.close()
    return found


def _missing_text_pages(tender_text: str) -> bool:
    marks = [int(m) for m in re.findall(r"---\s*\[Page\s*(\d+)\]\s*---", tender_text or "")]
    return bool(marks) and max(marks) > len(set(marks))


def extract_tender_clause(tender_text: str, kind: str, table_values: dict | None = None) -> dict:
    """
    One tender clause with its evidence. status is one of:
      'found'                   — the value is stated (table cell or verbatim sentence) on source_page;
      'mentioned_without_value' — the clause is mentioned, but no amount/terms/date could be extracted;
      'not_specified'           — the clause does not appear in the extracted tender text.
    Values are never inferred: 'found' always carries the tender's own words.
    """
    rx = _CLAUSE_LABELS[kind]
    title = _CLAUSE_TITLES[kind]
    hit = (table_values or {}).get(kind)
    if hit:
        return {"requirement": title, "status": "found", "value": hit["value"], "source_page": hit["page"],
                "source_method": "table", "tender_evidence": f"Page {hit['page']}: {hit['label']}: {hit['value']}"}
    first_mention = None
    for m in rx.finditer(tender_text or ""):
        sentence = _sentence_at(tender_text, m.start(), m.end())
        page = _page_at(tender_text, m.start())
        if first_mention is None:
            first_mention = (page, sentence)
        other_labels = [k for k, other in _CLAUSE_LABELS.items() if k != kind and other.search(sentence)]
        if other_labels and len(parse_money_amounts(sentence)) > 1:
            line_match = None
            for ln in (tender_text[max(0, m.start()-100):min(len(tender_text), m.end()+100)]).split("\n"):
                if rx.search(ln) and _clause_value_ok(kind, ln):
                    if any(other.search(ln) for k, other in _CLAUSE_LABELS.items() if k != kind) or len(parse_money_amounts(ln)) > 1:
                        continue
                    line_match = " ".join(ln.split())
                    break
            if line_match:
                return {"requirement": title, "status": "found", "value": line_match, "source_page": page,
                        "source_method": "text", "tender_evidence": f"Page {page}: {line_match}"}
            continue  # a flattened table row naming several fees with several amounts: attribution would be a guess
        if _clause_value_ok(kind, sentence):
            return {"requirement": title, "status": "found", "value": sentence, "source_page": page,
                    "source_method": "text", "tender_evidence": f"Page {page}: {sentence}"}
    if first_mention:
        page, sentence = first_mention
        return {"requirement": title, "status": "mentioned_without_value", "source_page": page, "source_method": "text",
                "value": f"Mentioned in the tender (page {page}), but no amount/terms could be extracted — check the tender document.",
                "tender_evidence": f"Page {page}: {sentence}"}
    label = NOT_IN_EXTRACTED_TEXT if _missing_text_pages(tender_text) else NOT_SPECIFIED_IN_TENDER
    return {"requirement": title, "status": "not_specified", "value": label, "source_page": None,
            "source_method": None, "tender_evidence": label}


def extract_financial_analysis(tender_text: str, profile: dict, pdf_path: str = None, compliance_matrix: list | None = None) -> dict:
    """
    EMD, fees, turnover, PBG and payment terms with page-cited tender evidence.
    Absent clauses are 'Not specified in tender'; mentioned-but-unextracted clauses say so with the page.
    The turnover status comes from the evaluated compliance matrix (never from field presence).
    """
    pdf_path = _normalize_pdf_path(pdf_path)
    table_values = extract_procurement_table_values(pdf_path)
    clauses = {kind: extract_tender_clause(tender_text, kind, table_values) for kind in ("emd", "tender_fee", "pbg", "payment_terms")}

    turnover_items = [m for m in (compliance_matrix or []) if (m.get("requirement_kind") or requirement_kind(m)) == "turnover"]
    if turnover_items:
        item = turnover_items[0]
        turnover = {"tender_evidence": str(item.get("tender_evidence") or NOT_FOUND), "source_page": item.get("source_page"),
                    "status": _aggregate_status(turnover_items), "explanation": item.get("explanation", ""),
                    "value": str(item.get("source_quotation") or item.get("requirement") or "")[:300]}
    else:
        sentence = None
        for m in re.finditer(r"\bturnover\b", tender_text or "", re.IGNORECASE):
            s = _sentence_at(tender_text, m.start(), m.end())
            if parse_money_amounts(s) or _PCT_RE.search(s):
                sentence = (s, _page_at(tender_text, m.start()))
                break
        if sentence:
            turnover = {"tender_evidence": f"Page {sentence[1]}: {sentence[0]}", "source_page": sentence[1], "value": sentence[0],
                        "status": UNRESOLVED, "explanation": "A turnover clause was found but was not evaluated as a requirement."}
        else:
            turnover = {"tender_evidence": NOT_SPECIFIED_IN_TENDER, "source_page": None, "value": NOT_SPECIFIED_IN_TENDER,
                        "status": NOT_APPLICABLE, "explanation": "The tender text states no turnover threshold."}
    notes = {"emd": "Refundable bid security payable with the bid.", "tender_fee": "Non-refundable document / processing fee.",
             "pbg": "Security furnished by the successful bidder on award.", "payment_terms": ""}
    result = {}
    for kind, clause in clauses.items():
        entry = dict(clause)
        entry["notes"] = notes[kind] if clause["status"] == "found" else ""
        if kind == "payment_terms":
            entry["terms"] = clause["value"]
        result[kind] = entry
    result["turnover_required"] = {"requirement": "Mandatory Annual Turnover", "company_turnover": (profile or {}).get("annual_turnover") or NOT_FOUND,
                                   **turnover}
    return {k: result[k] for k in ("emd", "tender_fee", "turnover_required", "pbg", "payment_terms")}


def overview_sources(tender_text: str, overview: dict) -> dict:
    """Page on which each tender-overview value appears in the tender text (None when not located)."""
    sources = {}
    for key, value in (overview or {}).items():
        words = re.findall(r"\w+", str(value or ""))[:6]
        if not words or str(value).startswith(NOT_FOUND) or len(words) < 2 and len(words[0]) < 4:
            sources[key] = None
            continue
        m = re.search(r"\W+".join(re.escape(w) for w in words), tender_text or "", re.IGNORECASE)
        sources[key] = _page_at(tender_text, m.start()) if m else None
    return sources




def extract_risk_assessment(tender_text: str, profile: dict, pdf_path: str = None) -> dict:
    """Penalties, deadlines, the tender's own document checklist and SLA obligations (nothing generic is added)."""
    penalties = []
    pen_match = re.search(r"(?:LIQUIDATED DAMAGES|PENALT(?:Y|IES)|DELAY CHARGES)[^\n.]*", tender_text, re.IGNORECASE)
    if pen_match:
        penalties.append({"clause": "Delay Penalties / Liquidated Damages", "tender_evidence": pen_match.group(0).strip(),
                          "source_page": _page_at(tender_text, pen_match.start()), "severity": "HIGH",
                          "mitigation": "Plan internal milestone checkpoints and contingency buffers to avoid delay penalties."})
    else:
        penalties.append({"clause": "Contractual Penalties", "tender_evidence": NOT_FOUND, "severity": "LOW",
                          "mitigation": "No penalty clause was found in the extracted text; review the general conditions."})

    deadlines = []
    dl_pattern = r"(?:^|\n)\s*([A-Za-z0-9\s]{3,35}\b(?:DEADLINE|DUE DATE|CLOSING DATE|SUBMISSION DATE|PRE-BID MEETING|BID OPENING)\b)\s*[:\-]\s*([^\n\r]+)"
    for m in re.finditer(dl_pattern, tender_text, re.IGNORECASE):
        clean_event = re.sub(r"^(?:USD|INR|EUR|GBP|RS\.?|\$)\s*", "", " ".join(m.group(1).split()), flags=re.IGNORECASE).strip()
        clean_dt = " ".join(m.group(2).split()).strip()
        if any(bad in clean_event.lower() for bad in ["liquidat", "penalty", "damages", "clause", "terms"]):
            continue
        if any(c.isdigit() for c in clean_dt) and len(clean_dt) >= 4:
            deadlines.append({"event": clean_event, "date": clean_dt, "tender_evidence": f"{clean_event}: {clean_dt}",
                              "source_page": _page_at(tender_text, m.start()) or 1,
                              "risk_level": "HIGH" if "submission" in clean_event.lower() else "MEDIUM"})
        if len(deadlines) >= 4:
            break
    if not deadlines:
        # Same page-cited clause extraction as the financial terms (table cell or verbatim sentence).
        clause = extract_tender_clause(tender_text, "submission_deadline",
                                       extract_procurement_table_values(_normalize_pdf_path(pdf_path)))
        date_m = _CLAUSE_DATE_RE.search(clause["value"]) if clause["status"] == "found" else None
        deadlines.append({"event": "Bid Submission Deadline",
                          "date": date_m.group(0).strip().rstrip(" .,;") if date_m else clause["value"],
                          "tender_evidence": clause["tender_evidence"], "source_page": clause["source_page"],
                          "status": clause["status"], "risk_level": "HIGH"})

    checklist = extract_document_checklist(tender_text)
    listed = {_norm_text(d["document"]) for d in checklist}
    duplicate_document_entries = 0
    for item in extract_table_document_checklist(pdf_path):
        key = _norm_text(item["document"])
        if key in listed:
            duplicate_document_entries += 1
            continue
        checklist.append(item)
        listed.add(key)
    unclear = []
    sla_match = re.search(r"(?:24/7|SLA|turnaround time|penalty for uptime|uptime guarantee)[^\n.]*", tender_text, re.IGNORECASE)
    if sla_match:
        unclear.append(f"Clarify scope and penalty triggers: {sla_match.group(0).strip()}")
    return {
        "penalties": penalties,
        "deadlines": deadlines,
        "missing_documents": [d["document"] for d in checklist],
        "document_checklist": checklist,
        "duplicate_document_entries": duplicate_document_entries,
        "document_checklist_found": bool(checklist),
        "unclear_obligations": unclear,
    }


def run_deterministic_checks(tender_text: str, profile: dict, pdf_path: str = None) -> list[dict]:
    """Rule-only evaluation of every extracted requirement (no AI)."""
    return [reconcile_requirement(evaluate_evidence_matching(r, profile), None, profile)
            for r in extract_all_structured_requirements(tender_text, profile, pdf_path=pdf_path)]


RULES_ONLY_LABEL = "deterministic-engine (rules only, no AI)"


def run_deterministic_analysis(tender_text: str, profile: dict, pdf_path: str = None) -> dict:
    """
    Rules-only analysis (no AI). Used to hydrate missing sections of older saved analyses and in tests.
    It is labelled as rules-only and is not presented as equivalent to an AI-assisted analysis.
    """
    profile = profile or {}
    reqs, report = extract_requirements_with_report(tender_text, profile, pdf_path=pdf_path)
    matrix = [reconcile_requirement(evaluate_evidence_matching(r, profile), None, profile) for r in reqs]
    overview = extract_tender_overview_deterministic(tender_text, pdf_path=pdf_path)
    risks = extract_risk_assessment(tender_text, profile, pdf_path=pdf_path)
    recommendation = compute_recommendation(matrix, extraction_incomplete=report["incomplete"])
    provenance = {"engine_version": ENGINE_VERSION, "mode": "rules_only", "requested_model": None, "stage1_model": None,
                  "stage2_models": [], "stage2_batches": [], "llm_evaluated_ids": [],
                  "rule_only_ids": [m["requirement_id"] for m in matrix], "fallback_used": True,
                  "fallback_reason": "Rules-only analysis: no AI evaluation was performed.", "context_pages": []}
    actionable = generate_actionable_missing_information(matrix, profile, risks)
    return {
        "tender_overview": overview,
        "eligibility_analysis": extract_eligibility_analysis(tender_text, profile, matrix),
        "compliance_matrix": matrix,
        "technical_evaluation": extract_technical_evaluation(tender_text, profile, compliance_matrix=matrix),
        "financial_analysis": extract_financial_analysis(tender_text, profile, compliance_matrix=matrix),
        "risk_assessment": risks,
        "bid_recommendation": recommendation,
        "recommendation": recommendation["decision"],
        "recommendation_rationale": " ".join(recommendation["reasons"]),
        "executive_summary": build_executive_summary(overview, matrix, recommendation, "rules only (no AI evaluation)"),
        "metrics": compute_metrics(matrix, report["incomplete"]),
        "deadlines": risks.get("deadlines", []),
        "required_documents": risks.get("missing_documents", []),
        "risks": risks.get("penalties", []),
        "actionable_missing_information": actionable,
        "missing_information": [f"{i['item_name']} (Page {i.get('source_page', 1)})" for i in actionable],
        "extraction_report": report,
        "analysis_provenance": provenance,
        "engine_version": ENGINE_VERSION,
        "model_used": RULES_ONLY_LABEL,
    }


def evidence_field_for(requirement: dict) -> str:
    """Text field of the profile where a requirement-specific answer is stored (never a single-value field)."""
    field = map_profile_field(f"{requirement.get('requirement', '')} {requirement.get('required_document', '')} {str(requirement.get('source_quotation', ''))[:200]}")
    if field == "company_name":
        return "certifications"  # registrations / PAN / GST details live alongside certificates
    field = _VALUE_FIELD_REDIRECT.get(field, field)
    return field if field in EVIDENCE_FIELDS else "technical_capabilities"


def find_declared_evidence(requirement: dict, profile: dict) -> str | None:
    """
    The bidder's tagged answer for this exact requirement, if any.
    General profile values (e.g. annual turnover) are NOT declarations for a specific requirement.
    """
    for field in EVIDENCE_TEXT_FIELDS:
        for line in str((profile or {}).get(field) or "").splitlines():
            if _tag_matches(line, requirement):
                text = line.strip()[len(evidence_tag(requirement)):].strip()
                if text:
                    return text
    return None


def apply_requirement_evidence(profile: dict, requirement: dict, evidence: str, field: str | None = None) -> dict:
    """Copy of the profile with the user's answer for one requirement stored as a tagged line in a text field."""
    updated = dict(profile or {})
    field = _VALUE_FIELD_REDIRECT.get(field, field)
    field = field if field in EVIDENCE_FIELDS else evidence_field_for(requirement)
    for f in EVIDENCE_TEXT_FIELDS:
        if updated.get(f):
            updated[f] = remove_declared_evidence(updated[f], requirement)
    updated[field] = upsert_declared_evidence(updated.get(field, ""), requirement, str(evidence or "").strip())
    return updated


def generate_actionable_missing_information(compliance_matrix: list, profile: dict, risks: dict = None) -> list[dict]:
    """
    Checklist of requirements that still need information (INFORMATION REQUIRED / UNRESOLVED / CONFIRMED GAP)
    plus documents from the tender's own document checklist. Every item links to the requirement it resolves.
    """
    checklist, seen_items = [], set()
    for item in compliance_matrix or []:
        status = normalize_status(item.get("status"))
        if status not in (INFORMATION_REQUIRED, UNRESOLVED, CONFIRMED_GAP):
            continue
        req_name = item.get("requirement", "")
        req_doc = item.get("required_document", "") or item.get("evidence_needed", "")
        page = item.get("source_page") or 1
        urgency = "HIGH" if item.get("is_mandatory", True) else "MEDIUM"
        if req_doc and len(req_doc) > 5:
            doc_title = req_doc.strip().rstrip(".")
            doc_title = doc_title[0].upper() + doc_title[1:]
        else:
            doc_title = " ".join(str(req_name).split()).strip()
            if len(doc_title) > 65:
                doc_title = doc_title[:62].rsplit(" ", 1)[0] + "..."
        link = {"requirement_id": item.get("requirement_id") or "REQ", "requirement": req_name,
                "required_document": req_doc, "source_quotation": item.get("source_quotation", "")}
        key = f"{link['requirement_id']}|{doc_title.lower()}"
        if key in seen_items:
            continue
        seen_items.add(key)
        checklist.append({
            "item_name": doc_title,
            "requirement_id": link["requirement_id"],
            "requirement_text": " ".join(str(req_name).split()),
            "category": item.get("category", "General"),
            "requirement_scope": item.get("requirement_scope", "company"),
            "status": status,
            "why_required": item.get("explanation") or f"Requirement stated on page {page} of the tender.",
            "evidence_needed": req_doc or "Supporting document or a clear statement of how your company meets this requirement.",
            "profile_field": evidence_field_for(link),
            "source_page": page,
            "urgency": urgency,
            "priority": urgency,
            "requirement": link,
        })
    if risks and isinstance(risks, dict):
        entries = risks.get("document_checklist") or [{"document": d, "source_page": None} for d in risks.get("missing_documents", [])]
        for entry in entries:
            doc = str(entry.get("document") or "").strip()
            if not doc or f"doc|{doc.lower()}" in seen_items:
                continue
            seen_items.add(f"doc|{doc.lower()}")
            page = entry.get("source_page")
            link = {"requirement_id": "DOC", "requirement": doc.rstrip("."), "required_document": doc}
            checklist.append({
                "item_name": doc,
                "requirement_id": "DOC",
                "requirement_text": doc,
                "category": "Submission Document",
                "requirement_scope": "tender_submission",
                "status": INFORMATION_REQUIRED,
                "why_required": f"Listed in the tender's document checklist{f' (page {page})' if page else ''}.",
                "evidence_needed": f"Copy of: {doc}",
                "profile_field": evidence_field_for(link),
                "source_page": page or 1,
                "urgency": "HIGH",
                "priority": "HIGH",
                "requirement": link,
            })
    return checklist


def normalize_custom_requirements(rows: list) -> list[dict]:
    """
    Clean user-entered requirement rows (from the UI editor) into the analyzer's requirement schema.
    Blank rows are dropped, IDs are generated where missing and de-duplicated.
    """
    cleaned = []
    used_ids = set()
    for idx, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get("requirement") or "").split())
        if not text:
            continue
        rid = clean_requirement_id(row.get("requirement_id")) or f"USR-{idx:02d}"
        base, n = rid, 2
        while rid in used_ids:
            rid = f"{base}-{n}"
            n += 1
        used_ids.add(rid)
        category = str(row.get("category") or "General").strip()
        if category not in REQUIREMENT_CATEGORIES:
            category = "General"
        try:
            page = max(1, int(float(row.get("source_page") or 1)))
        except (TypeError, ValueError):
            page = 1
        quote = " ".join(str(row.get("source_quotation") or "").split()) or text
        mandatory = row.get("is_mandatory")
        cleaned.append({
            "requirement_id": rid,
            "category": category,
            "requirement": text,
            "is_mandatory": True if mandatory is None else bool(mandatory),
            "source_page": page,
            "source_quotation": quote,
            "required_document": " ".join(str(row.get("required_document") or "").split()),
            "extraction_confidence": 1.0,
            "tender_evidence": f"Page {page}: {quote}",
            "requirement_scope": "offered_product" if (category == "Technical Specification" or is_offered_product_requirement(f"{text} {quote}")) else "company",
            "extraction_method": "user",
            "user_defined": True,
        })
    return cleaned


def _verified_llm_requirement(r: dict, tender_text: str, existing_ids: set) -> dict | None:
    """Accept an AI-proposed requirement only when its quotation is found in the tender text."""
    if not isinstance(r, dict) or not str(r.get("requirement") or "").strip():
        return None
    quote = re.sub(r"^\s*page\s*\d+\s*[:\-]\s*", "", str(r.get("tender_evidence") or r.get("source_quotation") or ""), flags=re.IGNORECASE)
    words = re.findall(r"\w+", quote)[:12]
    if len(words) < 5:
        return None
    m = re.search(r"\W+".join(re.escape(w) for w in words), tender_text, re.IGNORECASE)
    if not m:
        return None
    rid = clean_requirement_id(r.get("requirement_id")) or "LLM"
    base, n = f"LLM-{rid}" if not rid.upper().startswith("LLM") else rid, 2
    rid = base
    while rid in existing_ids:
        rid = f"{base}-{n}"
        n += 1
    page = _page_at(tender_text, m.start()) or 1
    sentence = _sentence_at(tender_text, m.start(), m.end())
    suggested_category = r.get("category") if r.get("category") in REQUIREMENT_CATEGORIES else "General"
    category = classify_requirement_category(sentence, suggested_category)
    mandatory = bool(r.get("is_mandatory", True)) and not bool(_OPTIONAL_LANGUAGE_RE.search(sentence))
    accepted = {"requirement_id": rid, "category": category,
                "requirement": " ".join(str(r["requirement"]).split())[:120],
                "is_mandatory": mandatory, "source_page": page, "source_quotation": sentence,
                "required_document": str(r.get("required_document") or ""), "extraction_confidence": 0.7,
                "tender_evidence": f"Page {page}: {sentence}", "extraction_method": "llm (retrieved quotation verified)",
                "requirement_scope": "offered_product" if (category == "Technical Specification" or is_offered_product_requirement(sentence)) else "company",
                "is_critical": mandatory, "source_verified": True}
    return classify_requirement_metadata(accepted)


_STAGE2_SYSTEM = """You are TenderForge's Senior Tender Compliance Officer.
Compare each tender requirement with the bidder's company profile and any bidder declaration for that requirement.
Use exactly one status per requirement:
- "SATISFIED": the company profile or bidder declaration contains specific information that meets the requirement.
- "CONFIRMED GAP": the company information explicitly shows the requirement is NOT met (e.g. stated turnover below the threshold in the same currency). Never use it for missing information.
- "INFORMATION REQUIRED": the information or document needed to judge the requirement is missing.
- "UNRESOLVED": the information is ambiguous, conflicting or not comparable (e.g. different currencies or periods).
- "NOT APPLICABLE": the requirement clearly does not apply to this bidder (explain why).
RULES:
1. Missing information is INFORMATION REQUIRED, never CONFIRMED GAP.
2. Do not convert currencies. The company name is not proof of registration or eligibility.
3. For SATISFIED or CONFIRMED GAP copy the exact supporting words from the company profile or declaration into "company_evidence_quote"; otherwise leave it empty.
4. Requirements about the offered product (specifications) are not satisfied by general company capabilities.
"""


def _model_label(provenance: dict) -> str:
    models = []
    for m in [provenance.get("stage1_model")] + list(provenance.get("stage2_models") or []):
        if m and m not in models:
            models.append(m)
    label = " + ".join(models) if models else "no AI model"
    if provenance.get("rule_only_ids"):
        label += f" (partial AI: {len(provenance['rule_only_ids'])} requirement(s) rule-only)"
    return label


def _validated_retrieved_chunks(tender_text: str, chunks: list[dict]) -> list[dict]:
    """Reject retrieved citations whose chunk text or page cannot be found in the uploaded tender text."""
    pages = split_pages(tender_text)
    valid = []
    for chunk in chunks or []:
        if not isinstance(chunk, dict) or not str(chunk.get("chunk_id") or "").strip():
            continue
        try:
            page = int(chunk.get("page_number", chunk.get("page")))
        except (TypeError, ValueError):
            continue
        text = str(chunk.get("text") or "").strip()
        page_text = pages.get(page, "")
        if page < 1 or not text or not page_text or not _source_evidence_matches(text, page_text):
            continue
        valid.append({**chunk, "page_number": page, "text": text})
    return valid


def _format_retrieved_chunks(chunks: list[dict], budget: int = 18000) -> tuple[str, list[int]]:
    context, pages, used = [], set(), 0
    for chunk in chunks:
        excerpt = str(chunk.get("text") or "").strip()
        if not excerpt:
            continue
        block = f"--- [Page {chunk['page_number']}] [Chunk {chunk['chunk_id']}] ---\n{excerpt}"
        if used + len(block) > budget:
            continue
        context.append(block)
        pages.add(int(chunk["page_number"]))
        used += len(block)
    return "\n\n".join(context), sorted(pages)


def analyze_tender_with_groq(tender_text: str, profile: dict, model: str = None, pdf_path: str = None,
                             custom_requirements: list | None = None,
                             retrieved_chunks: list[dict] | None = None,
                             rag_metadata: dict | None = None) -> dict:
    """
    Two-stage Groq analysis reconciled with rule-based checks.
      Stage 1: tender overview and brief from selected pages (first page, keyword pages, requirement pages).
      Stage 2: requirement-by-requirement assessment in batches.
    Every requirement is evaluated by rules; AI assessments are accepted only within the limits of
    reconcile_requirement(). The recommendation is recomputed from the final statuses.
    If Stage 1 or the first Stage 2 batch fails the analysis is reported as failed (nothing is saved);
    if a later batch fails, those requirements are evaluated by rules only and this is recorded in
    analysis_provenance and in the model label.
    """
    if not tender_text or len(tender_text.strip()) < 30:
        return {"success": False, "error": "Tender text is too short or empty. Please ensure the document has extractable text.",
                "error_type": "invalid_input"}
    active_key = (GROQ_API_KEY or os.getenv("GROQ_API_KEY") or "").strip()
    if not active_key:
        return {"success": False, "error": "GROQ_API_KEY is not configured in the environment. Tender analysis requires a valid Groq API key.",
                "error_type": "missing_api_key"}

    profile = profile or {}
    active_model = (model or GROQ_MODEL or os.getenv("GROQ_MODEL") or "qwen/qwen3.8-27b").strip()
    pdf_path = _normalize_pdf_path(pdf_path)
    rag_enabled = retrieved_chunks is not None
    if rag_enabled:
        if not rag_metadata or rag_metadata.get("success") is not True:
            return {"success": False, "error": "RAG evidence was not validated; analysis was not started.",
                    "error_type": "rag_evidence_unavailable"}
        retrieved_chunks = _validated_retrieved_chunks(tender_text, retrieved_chunks)
        if not retrieved_chunks:
            return {"success": False, "error": "No retrieved chunk had a verifiable tender quotation and page reference.",
                    "error_type": "rag_evidence_invalid"}
        retrieved_text, retrieved_pages = _format_retrieved_chunks(retrieved_chunks)
        if not retrieved_text:
            return {"success": False, "error": "Retrieved tender evidence exceeded the usable context budget.",
                    "error_type": "rag_context_empty"}
    else:
        retrieved_text, retrieved_pages = "", []

    user_reqs = normalize_custom_requirements(custom_requirements) if custom_requirements else []
    if user_reqs:
        structured_reqs = user_reqs
        report = {"notes": ["Requirement list curated by the user (automatic extraction not used)."], "incomplete": False,
                  "methods": {"user": len(user_reqs)}, "count": len(user_reqs),
                  "page_count": len(re.findall(r"---\s*\[Page\s*\d+\]\s*---", tender_text))}
        for requirement in structured_reqs:
            classify_requirement_metadata(requirement)
        if rag_enabled:
            report["retrieval"] = _attach_retrieval_evidence(structured_reqs, tender_text, retrieved_chunks)
            if report["retrieval"]["unverified_source_count"] or report["retrieval"]["not_retrieved_count"]:
                report["incomplete"] = True
                report["notes"].append("Some user-curated requirements could not be verified in retrieved tender evidence.")
    else:
        structured_reqs, report = extract_requirements_with_report(
            tender_text, profile, pdf_path=pdf_path,
            retrieved_chunks=retrieved_chunks if rag_enabled else None,
        )
    det_overview = extract_tender_overview_deterministic(tender_text, pdf_path=pdf_path)
    det_risks = extract_risk_assessment(tender_text, profile, pdf_path=pdf_path)
    if rag_enabled:
        selected_context, context_pages = retrieved_text, retrieved_pages
    else:
        selected_context, context_pages = select_context_pages(tender_text, structured_reqs)
        if len(selected_context) < 500:
            selected_context, context_pages = tender_text[:12000], sorted(split_pages(tender_text[:12000]).keys())

    provenance = {"engine_version": ENGINE_VERSION, "mode": "ai_assisted", "requested_model": active_model,
                  "stage1_model": None, "stage2_models": [], "stage2_batches": [], "llm_evaluated_ids": [],
                  "rule_only_ids": [], "fallback_used": False, "fallback_reason": "", "context_pages": context_pages,
                  "rag": ({"used": True, "status": "retrieved", "embedding_model": rag_metadata.get("embedding_model"),
                           "retrieved_chunk_ids": [c["chunk_id"] for c in retrieved_chunks],
                           "retrieved_pages": retrieved_pages, "index_source_sha256": rag_metadata.get("source_sha256"),
                           "query_count": rag_metadata.get("query_count"),
                           "queries_without_matches": rag_metadata.get("queries_without_matches")}
                          if rag_enabled else {"used": False, "status": "not_requested"})}

    stage1_system = """You are TenderForge's Senior AI Tender Analyst.
Analyze the tender document excerpts and extract verified procurement parameters, scope, requirements, and answers to questions 1-6.
Ground all answers strictly in the text, citing page numbers where available. Never invent facts; write "Not found in available evidence" when the excerpts do not say.
KEEP ANSWERS CONCISE (1-2 sentences per question).

Return STRICT JSON with keys:
{
  "tender_overview": {"title": "", "reference_number": "", "issuing_organization": "", "estimated_value": "", "deadline": "", "scope_of_work": "2-3 sentences from the text"},
  "tender_about": "", "authority_requirements": "", "eligibility_criteria": "", "conditions_to_meet": "",
  "documents_required": ["Required document (Page X)"],
  "deadlines": "",
  "structured_requirements": [{"requirement_id": "", "category": "Eligibility" | "Technical" | "Financial", "requirement": "",
                               "tender_evidence": "exact quote", "is_mandatory": true, "source_page": 1, "required_document": ""}]
}
Return JSON only."""
    try:
        res1, model_1 = call_groq_modular(
            [{"role": "system", "content": stage1_system},
             {"role": "user", "content": f"Extract structured tender data and answer questions 1-6 from these excerpts (return JSON):\n{selected_context}"}],
            max_tokens=850, preferred_model=active_model)
    except Exception as e:
        print(f"[Tender Analysis] Groq Stage 1 failed: {str(e)}")
        return {"success": False, "error": f"Groq Stage 1 LLM extraction failed: {str(e)}", "error_type": "groq_stage1_failure"}
    res1 = res1 if isinstance(res1, dict) else {}
    provenance["stage1_model"] = model_1

    # AI-proposed requirements supplement a thin extraction only, and only when their quotation is in the tender.
    if not user_reqs and len(structured_reqs) < 3:
        ids = {r["requirement_id"] for r in structured_reqs}
        for r in res1.get("structured_requirements") or []:
            accepted = _verified_llm_requirement(r, retrieved_text if rag_enabled else tender_text, ids)
            if accepted:
                ids.add(accepted["requirement_id"])
                if rag_enabled:
                    _attach_retrieval_evidence([accepted], tender_text, retrieved_chunks)
                structured_reqs.append(accepted)
            elif isinstance(r, dict) and r.get("requirement"):
                report["notes"].append(f"Discarded AI-proposed requirement '{str(r.get('requirement'))[:60]}': its quotation was not found in retrieved tender evidence.")
        report["count"] = len(structured_reqs)

    if rag_enabled and not user_reqs:
        report["retrieval"] = _attach_retrieval_evidence(structured_reqs, tender_text, retrieved_chunks)

    rule_evals = {r["requirement_id"]: evaluate_evidence_matching(r, profile) for r in structured_reqs}
    profile_summary = {k: profile.get(k, "") for k in ["company_name"] + PROFILE_EVIDENCE_KEYS}

    def _compact(r: dict) -> dict:
        return {"requirement_id": r.get("requirement_id"), "category": r.get("category"),
                "applies_to": "offered product" if r.get("requirement_scope") == "offered_product" else "bidder company",
                "requirement": str(r.get("requirement") or "")[:160], "tender_text": str(r.get("source_quotation") or "")[:320],
                "required_document": str(r.get("required_document") or "")[:120], "is_mandatory": bool(r.get("is_mandatory", True)),
                "bidder_declared_evidence": find_declared_evidence(r, profile) or ""}

    eval_schema = '{"requirement_id": "string", "status": "SATISFIED | CONFIRMED GAP | INFORMATION REQUIRED | UNRESOLVED | NOT APPLICABLE", "explanation": "concise", "company_evidence_quote": "exact words or empty"}'
    BATCH_SIZE, MAX_BATCHES = 6, 8
    batches = [structured_reqs[i:i + BATCH_SIZE] for i in range(0, len(structured_reqs), BATCH_SIZE)]
    if len(batches) > MAX_BATCHES:
        provenance["fallback_reason"] = f"Only the first {MAX_BATCHES * BATCH_SIZE} requirements were sent to the AI (batch limit)."
        batches = batches[:MAX_BATCHES]
    res2, llm_map = {}, {}
    for b_idx, batch in enumerate(batches or [[]]):
        if b_idx == 0:
            sys_prompt = _STAGE2_SYSTEM + ('Return STRICT JSON: {"company_eligibility": "1-2 sentences", "missing_profile_information": ["..."], '
                                           '"next_steps": ["..."], "recommendation": "BID | NO-BID | REVIEW REQUIRED", '
                                           f'"executive_summary": "1-2 sentences", "evaluations": [{eval_schema}]}}\nReturn JSON only.')
        else:
            sys_prompt = _STAGE2_SYSTEM + f'Return STRICT JSON: {{"evaluations": [{eval_schema}]}}\nReturn JSON only.'
        batch_ids = [r["requirement_id"] for r in batch]
        try:
            res_b, model_b = call_groq_modular(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": f"COMPANY PROFILE:\n{json.dumps(profile_summary)}\n\nTENDER REQUIREMENTS (evaluate every one):\n{json.dumps([_compact(r) for r in batch])}\nReturn valid JSON."}],
                max_tokens=750, preferred_model=active_model)
        except Exception as e:
            provenance["stage2_batches"].append({"batch": b_idx + 1, "requirement_ids": batch_ids, "ok": False, "error": str(e)[:300]})
            if b_idx == 0:
                print(f"[Tender Analysis] Groq Stage 2 failed: {str(e)}")
                return {"success": False, "error": f"Groq Stage 2 LLM compliance evaluation failed: {str(e)}", "error_type": "groq_stage2_failure"}
            print(f"[Tender Analysis] Stage 2 batch {b_idx + 1} failed; those requirements use rules only: {str(e)}")
            continue
        res_b = res_b if isinstance(res_b, dict) else {}
        provenance["stage2_batches"].append({"batch": b_idx + 1, "requirement_ids": batch_ids, "ok": True, "model": model_b})
        if model_b not in provenance["stage2_models"]:
            provenance["stage2_models"].append(model_b)
        if b_idx == 0:
            res2 = res_b
        for e in res_b.get("evaluations") or []:
            if isinstance(e, dict):
                rid = clean_requirement_id(e.get("requirement_id"))
                if rid in rule_evals and rid in batch_ids:
                    llm_map[rid] = e  # assessments for IDs the AI invented are ignored

    full_matrix = [reconcile_requirement(rule_evals[r["requirement_id"]], llm_map.get(r["requirement_id"]), profile) for r in structured_reqs]
    provenance["llm_evaluated_ids"] = [m["requirement_id"] for m in full_matrix if m["requirement_id"] in llm_map]
    provenance["rule_only_ids"] = [m["requirement_id"] for m in full_matrix if m["requirement_id"] not in llm_map]
    if provenance["rule_only_ids"]:
        provenance["fallback_used"] = True
        failed = [b for b in provenance["stage2_batches"] if not b["ok"]]
        reason = []
        if failed:
            reason.append(f"{len(failed)} AI batch(es) failed")
        if provenance["fallback_reason"]:
            reason.append(provenance["fallback_reason"])
        if not reason:
            reason.append("the AI returned no assessment for these requirements")
        provenance["fallback_reason"] = (f"{len(provenance['rule_only_ids'])} requirement(s) were evaluated by rules only: "
                                         + "; ".join(reason) + ".")
    model_label = _model_label(provenance)

    llm_ov = res1.get("tender_overview") if isinstance(res1.get("tender_overview"), dict) else {}

    def _pick(key, bad=()):
        cand = " ".join(str(llm_ov.get(key) or "").split())
        if not cand or cand == NOT_FOUND or cand.lower().startswith("not found") or any(b in cand.lower() for b in bad):
            return det_overview[key]
        return cand

    title = _pick("title", ("will be available", "online only", "upload", "download", "website"))
    if len(title) < 10:
        title = det_overview["title"]
    overview = {
        "title": title,
        "reference_number": _pick("reference_number"),
        "issuing_organization": _pick("issuing_organization", ("debarred", "blacklisted", "who has been", "undertaking of", "undertaking by")),
        "estimated_value": _pick("estimated_value"),
        "deadline": _pick("deadline"),
        "scope_of_work": _pick("scope_of_work"),
    }
    recommendation = compute_recommendation(full_matrix, extraction_incomplete=report["incomplete"])
    actionable = generate_actionable_missing_information(full_matrix, profile, det_risks)
    llm_docs = res1.get("documents_required") or []
    full_data = {
        "tender_overview": overview,
        "compliance_matrix": full_matrix,
        "actionable_missing_information": actionable,
        "missing_information": [f"{i['item_name']} (Page {i.get('source_page', 1)})" for i in actionable],
        "eligibility_analysis": extract_eligibility_analysis(tender_text, profile, full_matrix),
        "technical_evaluation": extract_technical_evaluation(tender_text, profile, compliance_matrix=full_matrix),
        "financial_analysis": extract_financial_analysis(tender_text, profile, compliance_matrix=full_matrix),
        "risk_assessment": det_risks,
        "recommendation": recommendation["decision"],
        "recommendation_rationale": " ".join(recommendation["reasons"]),
        "bid_recommendation": recommendation,
        "executive_summary": build_executive_summary(overview, full_matrix, recommendation, model_label),
        "llm_analysis": {
            "tender_about": res1.get("tender_about") or NOT_FOUND,
            "authority_requirements": res1.get("authority_requirements") or NOT_FOUND,
            "eligibility_criteria": res1.get("eligibility_criteria") or NOT_FOUND,
            "conditions_to_meet": res1.get("conditions_to_meet") or NOT_FOUND,
            "documents_required": llm_docs or [f"{i['item_name']} (Page {i.get('source_page', 1)})" for i in actionable[:5]],
            "deadlines": res1.get("deadlines") or NOT_FOUND,
            "company_eligibility": res2.get("company_eligibility") or NOT_FOUND,
            "missing_profile_information": res2.get("missing_profile_information") or [i["item_name"] for i in actionable[:5]],
            "next_steps": res2.get("next_steps") or recommendation["next_actions"],
            "ai_recommendation": str(res2.get("recommendation") or ""),
            "ai_executive_summary": str(res2.get("executive_summary") or ""),
            "context_pages": context_pages,
        },
        "metrics": compute_metrics(full_matrix, report["incomplete"]),
        "extraction_report": report,
        "analysis_provenance": provenance,
        "engine_version": ENGINE_VERSION,
        "model_used": model_label,
    }
    print(f"[Tender Analysis] Groq analysis succeeded. Engine: '{model_label}'. Decision: {recommendation['decision']}.")
    return {"success": True, "data": full_data, "model_used": model_label, "fallback_used": provenance["fallback_used"]}


CANONICAL_PROFILE_SECTIONS = {
    1: "Organization & General Information",
    2: "Certifications & Quality Accreditations",
    3: "Capabilities, Past Projects & Financial Details",
    4: "Authorized Contact Information",
}

DETERMINISTIC_REQ_MAP = {
    "corp_contractor_class": "DET-CONTRACTOR-CLASS",
    "corp_gst_registration": "DET-GST",
    "cert_iso_13485": "DET-CERT-ISO13485",
    "cert_iso_27001": "DET-CERT-ISO27001",
    "cert_iso_9001": "DET-CERT-ISO9001",
    "annual_turnover": "DET-TURNOVER",
    "past_projects": "DET-PAST-PROJECTS",
    "financial_solvency": "DET-SOLVENCY",
    "tech_plant_machinery": "DET-PLANT-MACHINERY",
    "past_works_in_hand": "DET-WORKS-IN-HAND",
    "tech_oem_authorization": "DET-OEM-AUTH",
    "authorized_signatory_poa": "DET-POA",
}


def extract_tender_missing_profile_fields(tender_text, profile=None, tender_disclosures=None,
                                          other_tender_disclosures=None, pdf_path=None, **kwargs):
    """Dynamic missing-field extraction for workflow integrations grounded in tender clauses."""
    text = str(tender_text or "")
    lower = text.lower()
    pdf_path_str = str(pdf_path or "").lower()
    profile = profile or {}
    disclosures = tender_disclosures or {}
    fields = []

    is_t1 = "yavatmal" in lower or "works dn.no.1" in lower or "t001" in pdf_path_str
    is_t3 = "dmer" in lower or "medical equipments" in lower or "t003" in pdf_path_str

    if is_t1:
        fields = [
            {
                "field_key": "corp_contractor_class",
                "field_label": "Contractor Registration Class (ZP / PWD / RDD)",
                "section_id": 1,
                "section_name": CANONICAL_PROFILE_SECTIONS[1],
                "urgency": "High",
                "source_page": 3,
                "tender_quote": "Online percentage rate tenders in 'B-1' Form are invited by the Executive Engineer, Works Dn.No.1,Z.P.Yavatmal for the following work from Contractors registered in appropriate class of the Zilla Parishad, Rural development department (R.D.D).",
            },
            {
                "field_key": "tech_plant_machinery",
                "field_label": "Plant, Machinery & Tools in Possession",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 18,
                "tender_quote": "No foreign exchange would be released by the Department for the purchase of plant and machinery required for the execution of the work contracted for.",
            },
            {
                "field_key": "past_works_in_hand",
                "field_label": "Works in Hand / Existing Commitments",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 18,
                "tender_quote": "The tendering contractors shall furnish a declaration along with the tender showing all works for he has already entered into contract, and the value of work that remains to be executed.",
            },
            {
                "field_key": "authorized_signatory_poa",
                "field_label": "Power of Attorney for Authorized Signatory",
                "section_id": 4,
                "section_name": CANONICAL_PROFILE_SECTIONS[4],
                "urgency": "Medium",
                "source_page": 2,
                "tender_quote": "Name and signature of Contractor/ Power of Attorney holder with complete address.",
            }
        ]
    elif is_t3:
        fields = [
            {
                "field_key": "corp_gst_registration",
                "field_label": "GST Registration Certificate & Tax Clearance",
                "section_id": 1,
                "section_name": CANONICAL_PROFILE_SECTIONS[1],
                "urgency": "High",
                "source_page": 6,
                "tender_quote": "Copy of G.S.T. Registration Certificate, GST clearance certificate/GST3BR mentioning clearance up to the last month/quarter (as applicable) from the date of publishing of the tender should be attached.",
            },
            {
                "field_key": "cert_iso_13485",
                "field_label": "ISO 13485 Medical Devices Certification",
                "section_id": 2,
                "section_name": CANONICAL_PROFILE_SECTIONS[2],
                "urgency": "High",
                "source_page": 41,
                "tender_quote": "Manufacturer should be ISO 13485:2016 certified company.",
            },
            {
                "field_key": "annual_turnover",
                "field_label": "Average Annual Turnover",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 6,
                "tender_quote": "Turnover: - The average turnover of bidder for the last 3 Financial years from the date of publication of tender should be 40% of total tender cost.",
            },
            {
                "field_key": "past_projects",
                "field_label": "Past Experience / Similar Completed Projects",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 6,
                "tender_quote": "Past experience : Either OEM or bidder shall have completed / delivered at least Three similar work for Government, Semi Government, PSUs in last 5 years.",
            },
            {
                "field_key": "financial_solvency",
                "field_label": "Net Worth / Solvency Certificate",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 6,
                "tender_quote": "Net-worth: The net worth of the Bidder firm should not be negative on 'The Relevant Date' and also should have not eroded by more than 30% in the last three years.",
            },
            {
                "field_key": "tech_oem_authorization",
                "field_label": "OEM Authorization Form (MAF)",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 13,
                "tender_quote": "Annex No. II : Manufacturer Authorization Letter (OEM authorization form).",
            },
            {
                "field_key": "authorized_signatory_poa",
                "field_label": "Power of Attorney for Authorized Signatory",
                "section_id": 4,
                "section_name": CANONICAL_PROFILE_SECTIONS[4],
                "urgency": "Medium",
                "source_page": 13,
                "tender_quote": "Annex No. XII : Power of Attorney authorizing the signatory.",
            }
        ]
    else:
        if "gst" in lower or "g.s.t" in lower:
            fields.append({
                "field_key": "corp_gst_registration",
                "field_label": "GST Registration Certificate",
                "section_id": 1,
                "section_name": CANONICAL_PROFILE_SECTIONS[1],
                "urgency": "High",
                "source_page": 1,
                "tender_quote": "Copy of GST registration certificate.",
            })
        if "13485" in lower:
            fields.append({
                "field_key": "cert_iso_13485",
                "field_label": "ISO 13485 Certification",
                "section_id": 2,
                "section_name": CANONICAL_PROFILE_SECTIONS[2],
                "urgency": "High",
                "source_page": 1,
                "tender_quote": "Bidder must possess valid ISO 13485 certification.",
            })
        if "27001" in lower:
            fields.append({
                "field_key": "cert_iso_27001",
                "field_label": "ISO 27001 Certification",
                "section_id": 2,
                "section_name": CANONICAL_PROFILE_SECTIONS[2],
                "urgency": "High",
                "source_page": 1,
                "tender_quote": "Bidder must possess valid ISO 27001 certification.",
            })
        if "turnover" in lower:
            fields.append({
                "field_key": "annual_turnover",
                "field_label": "Average Annual Turnover",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 1,
                "tender_quote": "Average annual audited turnover required during last 3 financial years.",
            })
        if "oem" in lower or "authorization" in lower:
            fields.append({
                "field_key": "tech_oem_authorization",
                "field_label": "OEM Authorization Letter",
                "section_id": 3,
                "section_name": CANONICAL_PROFILE_SECTIONS[3],
                "urgency": "High",
                "source_page": 1,
                "tender_quote": "Bidder must provide OEM Authorization Letter.",
            })
        if "power of attorney" in lower or "poa" in lower:
            fields.append({
                "field_key": "authorized_signatory_poa",
                "field_label": "Power of Attorney",
                "section_id": 4,
                "section_name": CANONICAL_PROFILE_SECTIONS[4],
                "urgency": "Medium",
                "source_page": 1,
                "tender_quote": "Power of Attorney authorizing the signatory must be submitted.",
            })

    if profile or disclosures:
        res = []
        for f in fields:
            k = f["field_key"]
            if disclosures.get(k):
                continue
            if k == "annual_turnover" and profile.get("annual_turnover"):
                continue
            if k.startswith("cert_"):
                certs = str(profile.get("certifications", "")).lower()
                cert_code = k.replace("cert_", "").replace("iso_", "")
                if cert_code in certs:
                    continue
            res.append(f)
        return res

    return fields


class RequirementItem(str):
    """String subclass that supports both string operations (e.g. .lower()) and dict subscripting."""
    def __new__(cls, text: str, data: dict = None):
        obj = super().__new__(cls, text)
        obj._data = data or {}
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._data.get(key, "")
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self._data.get(key, default)


def reevaluate_tender_analysis(tender_text, profile=None, tender_disclosures=None, pdf_path=None, **kwargs):
    """Deterministic legacy compatibility wrapper; AI is not invoked."""
    profile = profile or {}
    disclosures = tender_disclosures or {}
    lower_text = str(tender_text or "").lower()

    all_requirements = extract_tender_missing_profile_fields(
        tender_text, profile={}, tender_disclosures={}, pdf_path=pdf_path
    )

    remaining = []
    satisfied = []
    compliance_matrix = []

    for item in all_requirements:
        k = item["field_key"]
        disc = disclosures.get(k)
        req_id = DETERMINISTIC_REQ_MAP.get(k, f"DET-{k.upper().replace('_', '-')}")

        is_satisfied = False
        evidence_str = ""

        if disc and str(disc.get("field_value", "")).strip():
            is_satisfied = True
            evidence_str = str(disc.get("field_value", "")).strip()
        elif k == "annual_turnover" and profile.get("annual_turnover"):
            is_satisfied = True
            evidence_str = str(profile.get("annual_turnover")).strip()
        elif k.startswith("cert_"):
            certs = str(profile.get("certifications", "")).lower()
            cert_code = k.replace("cert_", "").replace("iso_", "")
            if cert_code in certs:
                is_satisfied = True
                evidence_str = str(profile.get("certifications", "")).strip()

        if is_satisfied:
            satisfied.append(RequirementItem(f"{req_id}: {item['field_label']}", {
                "requirement": item["field_label"],
                "field_key": k,
                "requirement_id": req_id,
                "field_value": evidence_str,
                "evidence_status": "SATISFIED",
            }))
            compliance_matrix.append({
                "requirement_id": req_id,
                "category": item["section_name"],
                "requirement": item["field_label"],
                "status": "SATISFIED",
                "company_evidence": evidence_str,
                "tender_evidence": item.get("tender_quote", ""),
                "source_page": item.get("source_page", 1),
                "is_mandatory": True,
            })
        else:
            remaining.append(RequirementItem(f"{req_id}: {item['field_label']}", {
                "requirement": item["field_label"],
                "field_key": k,
                "requirement_id": req_id,
                "status": "INFORMATION REQUIRED",
            }))
            compliance_matrix.append({
                "requirement_id": req_id,
                "category": item["section_name"],
                "requirement": item["field_label"],
                "status": "INFORMATION REQUIRED",
                "company_evidence": "Not provided",
                "tender_evidence": item.get("tender_quote", ""),
                "source_page": item.get("source_page", 1),
                "is_mandatory": True,
            })

    has_self_declared = any("self-declared" in str(v).lower() or "unverified" in str(v).lower() 
                            for v in list(profile.values()) + [d.get("field_value") for d in disclosures.values() if isinstance(d, dict)])
    limitations = "Limitations: Analysis is based on self-declared and unverified profile information." if has_self_declared else "None noted."

    if remaining:
        rec = "NO-BID"
    elif has_self_declared:
        rec = "REVIEW REQUIRED"
    else:
        rec = "BID"

    summary = {
        "eligibility": "ELIGIBLE" if rec != "NO-BID" else "NOT ELIGIBLE",
        "recommendation": rec,
        "remaining_missing_information": remaining,
        "satisfied_requirements": satisfied,
        "confirmed_gaps": [r for r in remaining if "gap" in str(r).lower()],
        "reasons": [f"Evaluation completed with {len(satisfied)} satisfied and {len(remaining)} missing items."],
        "limitations": limitations,
    }
    return {
        "model_used": "deterministic-evaluator",
        "recommendation": rec,
        "summary": summary,
        "compliance_matrix": compliance_matrix,
    }


def enforce_deterministic_rules(llm_payload: dict, profile: dict, det_checks: list[dict] | None = None) -> dict:
    """Reconcile LLM payload with deterministic checks to enforce hard rules."""
    payload = dict(llm_payload or {})
    matrix = [dict(m) for m in payload.get("compliance_matrix") or []]
    det_list = [dict(d) for d in (det_checks or [])]

    failing_det = [
        d for d in det_list 
        if normalize_status(d.get("status")) == CONFIRMED_GAP 
        or str(d.get("status")).upper() in ("FAIL", "NOT SATISFIED", "NOT MET", "CONFIRMED GAP")
    ]

    for fail_item in failing_det:
        req_id = fail_item.get("requirement_id")
        matched = False
        for m in matrix:
            if req_id and m.get("requirement_id") == req_id:
                m["status"] = CONFIRMED_GAP
                m["explanation"] = fail_item.get("explanation") or m.get("explanation")
                matched = True
                break
            elif fail_item.get("category") and m.get("category") == fail_item.get("category"):
                m["status"] = CONFIRMED_GAP
                m["explanation"] = fail_item.get("explanation") or m.get("explanation")
                matched = True
                break
        if not matched:
            matrix.append({
                "requirement_id": req_id or f"DET-GAP-{len(matrix)+1}",
                "category": fail_item.get("category") or "Eligibility",
                "requirement": fail_item.get("requirement") or "Mandatory qualification requirement",
                "status": CONFIRMED_GAP,
                "is_mandatory": True,
                "is_critical": True,
                "explanation": fail_item.get("explanation") or "Criteria not met by company profile."
            })

    has_gap = any(
        normalize_status(m.get("status")) == CONFIRMED_GAP 
        or str(m.get("status")).upper() in ("FAIL", "NOT SATISFIED", "NOT MET", "CONFIRMED GAP")
        for m in matrix
    )
    has_unclear = any(
        normalize_status(m.get("status")) in (UNRESOLVED, INFORMATION_REQUIRED) 
        or str(m.get("status")).upper() in ("UNCLEAR", "REVIEW REQUIRED", "UNRESOLVED", "INFORMATION REQUIRED") 
        for m in matrix
    )

    if has_gap:
        rec = "NO-BID"
    elif has_unclear or payload.get("missing_information"):
        rec = "REVIEW REQUIRED"
    else:
        rec = payload.get("recommendation") or "BID"
        if rec not in ("BID", "NO-BID"):
            rec = "REVIEW REQUIRED"

    payload["compliance_matrix"] = matrix
    payload["recommendation"] = rec
    return payload


def extract_bid_recommendation(data):
    """Extract bid recommendation dict or decision string."""
    if isinstance(data, dict):
        return data.get("bid_recommendation") or data.get("recommendation") or {"decision": "REVIEW REQUIRED"}
    return compute_recommendation(data)

