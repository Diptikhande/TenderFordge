from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    tender_id: str
    start_page: int
    end_page: int
    text: str
    metadata: dict[str, Any]


class TenderChunker:
    """Simple paragraph-aware chunker for extracted tender text."""

    def __init__(self, max_chars: int = 1800, overlap_chars: int = 200):
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk_pages(self, tender_id: str, pages: list[dict[str, Any]]) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        current_parts: list[str] = []
        current_pages: list[int] = []

        for page in pages:
            page_number = int(page["page_number"])
            paragraphs = _split_paragraphs(page.get("text", ""))
            for paragraph in paragraphs:
                if not current_parts:
                    current_pages = [page_number]

                current_length = sum(len(part) for part in current_parts) + max(len(current_parts) - 1, 0) * 2
                would_exceed = current_parts and current_length + len(paragraph) + 2 > self.max_chars
                if would_exceed:
                    chunks.append(self._build_chunk(tender_id, len(chunks) + 1, current_parts, current_pages))
                    current_parts = self._overlap_parts(current_parts)
                    current_pages = [current_pages[-1]] if current_pages else [page_number]

                current_parts.append(paragraph)
                if page_number not in current_pages:
                    current_pages.append(page_number)

        if current_parts:
            chunks.append(self._build_chunk(tender_id, len(chunks) + 1, current_parts, current_pages))

        return chunks

    def _build_chunk(
        self,
        tender_id: str,
        sequence: int,
        parts: list[str],
        page_numbers: list[int],
    ) -> TextChunk:
        text = "\n\n".join(parts).strip()
        start_page = min(page_numbers)
        end_page = max(page_numbers)
        return TextChunk(
            chunk_id=f"{tender_id}-chunk-{sequence:04d}",
            tender_id=tender_id,
            start_page=start_page,
            end_page=end_page,
            text=text,
            metadata={
                "sequence": sequence,
                "char_count": len(text),
                "page_range": [start_page, end_page],
                "strategy": "paragraph_max_chars_with_overlap",
            },
        )

    def _overlap_parts(self, parts: list[str]) -> list[str]:
        if self.overlap_chars <= 0:
            return []

        overlap: list[str] = []
        total_chars = 0
        for part in reversed(parts):
            if total_chars >= self.overlap_chars:
                break
            overlap.insert(0, part)
            total_chars += len(part)
        return overlap


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    normalized: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= 2200:
            normalized.append(paragraph)
            continue
        normalized.extend(_split_long_paragraph(paragraph))
    return normalized


def _split_long_paragraph(paragraph: str, max_chars: int = 1800) -> list[str]:
    words = paragraph.split()
    parts: list[str] = []
    current: list[str] = []
    current_length = 0

    for word in words:
        extra = len(word) + (1 if current else 0)
        if current and current_length + extra > max_chars:
            parts.append(" ".join(current))
            current = [word]
            current_length = len(word)
        else:
            current.append(word)
            current_length += extra

    if current:
        parts.append(" ".join(current))
    return parts
