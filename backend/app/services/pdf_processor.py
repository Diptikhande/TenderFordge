from pathlib import Path
from typing import Any

import fitz

from app.services.chunker import TenderChunker
from app.services.document_structure import identify_document_structure
from app.services.text_cleaner import clean_page_text, remove_repeated_headers_footers


class PdfProcessingError(Exception):
    """Raised when a PDF cannot be processed."""


class PdfProcessor:
    """Extract page text and chunks from a tender PDF."""

    def __init__(self, chunker: TenderChunker | None = None):
        self.chunker = chunker or TenderChunker()

    def process_pdf(self, tender_id: str, pdf_path: Path | str) -> dict[str, Any]:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise PdfProcessingError(f"Unsupported file type: {pdf_path.suffix}")

        try:
            document = fitz.open(pdf_path)
        except Exception as exc:
            raise PdfProcessingError(f"Invalid or unreadable PDF: {pdf_path.name}") from exc

        try:
            raw_pages = []
            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                raw_text = page.get_text("text") or ""
                raw_pages.append(
                    {
                        "page_number": page_index + 1,
                        "raw_text": raw_text,
                        "text": clean_page_text(raw_text),
                        "char_count": len(raw_text),
                    }
                )

            cleaned_texts = remove_repeated_headers_footers(page["text"] for page in raw_pages)
            pages = [
                {
                    "page_number": page["page_number"],
                    "text": cleaned_texts[index],
                    "raw_char_count": page["char_count"],
                    "cleaned_char_count": len(cleaned_texts[index]),
                    "has_text": bool(cleaned_texts[index].strip()),
                }
                for index, page in enumerate(raw_pages)
            ]
            structure = identify_document_structure(pages)
            chunks = self.chunker.chunk_pages(tender_id, pages)
            extracted_text_available = any(page["has_text"] for page in pages)

            return {
                "tender_id": tender_id,
                "filename": pdf_path.name,
                "page_count": document.page_count,
                "pages": pages,
                "document_structure": structure,
                "chunks": [chunk.__dict__ for chunk in chunks],
                "extracted_text_available": extracted_text_available,
                "empty_or_scanned": not extracted_text_available,
            }
        except Exception as exc:
            raise PdfProcessingError(f"Failed to extract PDF text: {pdf_path.name}") from exc
        finally:
            document.close()
