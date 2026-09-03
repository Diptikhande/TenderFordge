import re
from typing import Any


NUMBERED_HEADING_RE = re.compile(r"^(\d+(\.\d+)*|[A-Z]\.|\([a-z]\)|\([ivx]+\))\s+.+", re.IGNORECASE)


def identify_document_structure(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Identify likely tender section headings from cleaned page text."""
    sections: list[dict[str, Any]] = []

    for page in pages:
        page_number = int(page["page_number"])
        for line_number, line in enumerate(_candidate_lines(page.get("text", "")), start=1):
            heading_type = _classify_heading(line)
            if not heading_type:
                continue
            sections.append(
                {
                    "section_id": f"page-{page_number}-section-{len(sections) + 1:04d}",
                    "page_number": page_number,
                    "line_number": line_number,
                    "title": line,
                    "heading_type": heading_type,
                }
            )

    return sections


def _candidate_lines(text: str) -> list[str]:
    lines = []
    for line in text.split("\n"):
        candidate = line.strip()
        if 4 <= len(candidate) <= 140:
            lines.append(candidate)
    return lines


def _classify_heading(line: str) -> str | None:
    alpha_chars = [char for char in line if char.isalpha()]
    if not alpha_chars:
        return None

    if NUMBERED_HEADING_RE.match(line):
        return "numbered_heading"

    uppercase_ratio = sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars)
    word_count = len(line.split())
    if uppercase_ratio >= 0.75 and word_count <= 14:
        return "uppercase_heading"

    if line.endswith(":") and word_count <= 12:
        return "label_heading"

    return None
