import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import close_database
from app.services.document_processing_service import STATUS_PROCESSED, document_processing_service
from app.services.tender_service import tender_service


def main() -> int:
    tenders = tender_service.get_all_tenders()
    if not tenders:
        print("No tender PDFs found in dataset/tenders.")
        return 1

    failures = []
    print(f"Processing {len(tenders)} tender PDF(s)...")
    for tender in tenders:
        tender_id = tender["tender_id"]
        try:
            result = document_processing_service.process_tender(tender_id)
        except Exception as exc:
            print(f"{tender_id}: status=FAILED, error={exc}")
            failures.append((tender_id, str(exc)))
            continue

        page_count = result.get("page_count") or 0
        chunk_count = result.get("chunk_count") or 0
        structure_count = len(result.get("document_structure") or [])
        has_text = result.get("extracted_text_available", False)
        status = result.get("processing_status")
        print(
            f"{tender_id}: status={status}, pages={page_count}, "
            f"sections={structure_count}, chunks={chunk_count}, extracted_text={has_text}"
        )
        if status != STATUS_PROCESSED or page_count == 0 or (has_text and chunk_count == 0):
            failures.append((tender_id, result.get("error") or "No chunks generated"))

    close_database()

    if failures:
        print("\nFailures:")
        for tender_id, error in failures:
            print(f"- {tender_id}: {error}")
        return 1

    print("\nProcessing verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
