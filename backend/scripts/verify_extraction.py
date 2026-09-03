import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.pdf_processor import PdfProcessor
from app.services.tender_service import tender_service


def main() -> int:
    processor = PdfProcessor()
    tenders = tender_service.get_all_tenders()
    if not tenders:
        print("No tender PDFs found in dataset/tenders.")
        return 1

    failures = []
    print(f"Found {len(tenders)} tender PDF(s).")
    for tender in tenders:
        tender_id = tender["tender_id"]
        try:
            result = processor.process_pdf(tender_id, tender["path"])
        except Exception as exc:
            failures.append((tender_id, str(exc)))
            print(f"{tender_id}: FAILED, error={exc}")
            continue

        text_pages = sum(1 for page in result["pages"] if page["has_text"])
        chunk_count = len(result["chunks"])
        structure_count = len(result["document_structure"])
        print(
            f"{tender_id}: pages={result['page_count']}, text_pages={text_pages}, "
            f"sections={structure_count}, chunks={chunk_count}, "
            f"extracted_text={result['extracted_text_available']}"
        )
        if result["page_count"] == 0 or (result["extracted_text_available"] and chunk_count == 0):
            failures.append((tender_id, "Missing pages or chunks"))

    if failures:
        print("\nFailures:")
        for tender_id, error in failures:
            print(f"- {tender_id}: {error}")
        return 1

    print("\nExtraction verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
