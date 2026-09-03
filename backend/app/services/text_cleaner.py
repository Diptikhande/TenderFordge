import re
from collections import Counter
from typing import Iterable


def clean_page_text(text: str) -> str:
    """Clean extracted page text without changing tender wording."""
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def remove_repeated_headers_footers(page_texts: Iterable[str]) -> list[str]:
    """
    Remove repeated first/last lines that are likely headers or footers.

    The heuristic is intentionally conservative: it only removes short lines that
    appear on at least three pages and more than half of all non-empty pages.
    """
    pages = list(page_texts)
    non_empty_pages = [page for page in pages if page.strip()]
    if len(non_empty_pages) < 3:
        return pages

    candidates: list[str] = []
    for page in non_empty_pages:
        lines = _meaningful_lines(page)
        if not lines:
            continue
        candidates.append(lines[0])
        if len(lines) > 1:
            candidates.append(lines[-1])

    counts = Counter(candidates)
    threshold = max(3, len(non_empty_pages) // 2 + 1)
    repeated = {
        line
        for line, count in counts.items()
        if count >= threshold and 3 <= len(line) <= 120
    }
    if not repeated:
        return pages

    cleaned_pages = []
    for page in pages:
        lines = page.split("\n")
        while lines and lines[0].strip() in repeated:
            lines.pop(0)
        while lines and lines[-1].strip() in repeated:
            lines.pop()
        cleaned_pages.append(clean_page_text("\n".join(lines)))

    return cleaned_pages


def _meaningful_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]
