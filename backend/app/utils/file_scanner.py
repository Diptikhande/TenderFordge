from pathlib import Path
from typing import List

from app.config import DATASET_PATH


def scan_available_tenders() -> List[dict]:
    """Dynamically return metadata for every PDF in the local test dataset."""
    tenders_dir = Path(DATASET_PATH)
    if not tenders_dir.exists():
        print(f"Dataset directory not found: {tenders_dir}")
        return []

    pdf_files = sorted(tenders_dir.glob("*.pdf"))
    tenders = [
        {
            "tender_id": pdf.stem,
            "filename": pdf.name,
            "status": "Not Processed",
            "document_size_bytes": pdf.stat().st_size,
            "path": str(pdf),
        }
        for pdf in pdf_files
    ]

    print(f"Discovered {len(tenders)} tender(s) in {tenders_dir}")
    return tenders
