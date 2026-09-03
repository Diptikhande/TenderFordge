from datetime import datetime, timezone
from typing import Any

from pymongo.errors import PyMongoError

from app.database import get_database
from app.services.pdf_processor import PdfProcessingError, PdfProcessor
from app.services.tender_service import tender_service


STATUS_NOT_PROCESSED = "NOT_PROCESSED"
STATUS_PROCESSING = "PROCESSING"
STATUS_PROCESSED = "PROCESSED"
STATUS_FAILED = "FAILED"


class DocumentProcessingService:
    """Coordinates PDF processing and MongoDB persistence."""

    def __init__(self, processor: PdfProcessor | None = None):
        self.processor = processor or PdfProcessor()

    def process_tender(self, tender_id: str) -> dict[str, Any]:
        tender = tender_service.get_tender_by_id(tender_id)
        if tender is None:
            raise FileNotFoundError(f"Tender not found: {tender_id}")

        started_at = _utcnow()
        self._upsert_processing_status(
            tender_id=tender_id,
            filename=tender["filename"],
            status=STATUS_PROCESSING,
            started_at=started_at,
            error=None,
        )

        try:
            result = self.processor.process_pdf(tender_id, tender["path"])
            processed_at = _utcnow()
            processing_document = {
                "tender_id": tender_id,
                "filename": result["filename"],
                "page_count": result["page_count"],
                "processing_status": STATUS_PROCESSED,
                "started_at": started_at,
                "processed_at": processed_at,
                "extracted_text_available": result["extracted_text_available"],
                "empty_or_scanned": result["empty_or_scanned"],
                "pages": result["pages"],
                "document_structure": result["document_structure"],
                "chunk_count": len(result["chunks"]),
                "error": None,
            }
            self._store_result(processing_document, result["chunks"])
            return processing_document
        except (FileNotFoundError, PdfProcessingError, PyMongoError, Exception) as exc:
            failed_document = {
                "tender_id": tender_id,
                "filename": tender["filename"],
                "page_count": None,
                "processing_status": STATUS_FAILED,
                "started_at": started_at,
                "processed_at": _utcnow(),
                "extracted_text_available": False,
                "empty_or_scanned": False,
                "pages": [],
                "document_structure": [],
                "chunk_count": 0,
                "error": str(exc),
            }
            self._store_failed_result(failed_document)
            return failed_document

    def get_processing_result(self, tender_id: str) -> dict[str, Any]:
        db = get_database()
        result = db.tender_processing.find_one({"tender_id": tender_id}, {"_id": False})
        if result is None:
            tender = tender_service.get_tender_by_id(tender_id)
            if tender is None:
                raise FileNotFoundError(f"Tender not found: {tender_id}")
            return {
                "tender_id": tender_id,
                "filename": tender["filename"],
                "page_count": None,
                "processing_status": STATUS_NOT_PROCESSED,
                "processed_at": None,
                "extracted_text_available": False,
                "empty_or_scanned": False,
                "pages": [],
                "document_structure": [],
                "chunk_count": 0,
                "chunks": [],
                "error": None,
            }

        chunks = list(db.tender_chunks.find({"tender_id": tender_id}, {"_id": False}).sort("metadata.sequence", 1))
        result["chunks"] = chunks
        return result

    def _store_result(self, processing_document: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
        db = get_database()
        db.tender_processing.update_one(
            {"tender_id": processing_document["tender_id"]},
            {"$set": processing_document},
            upsert=True,
        )
        db.tender_chunks.delete_many({"tender_id": processing_document["tender_id"]})
        if chunks:
            db.tender_chunks.insert_many(chunks)

    def _store_failed_result(self, failed_document: dict[str, Any]) -> None:
        db = get_database()
        db.tender_processing.update_one(
            {"tender_id": failed_document["tender_id"]},
            {"$set": failed_document},
            upsert=True,
        )
        db.tender_chunks.delete_many({"tender_id": failed_document["tender_id"]})

    def _upsert_processing_status(
        self,
        tender_id: str,
        filename: str,
        status: str,
        started_at: datetime,
        error: str | None,
    ) -> None:
        db = get_database()
        db.tender_processing.update_one(
            {"tender_id": tender_id},
            {
                "$set": {
                    "tender_id": tender_id,
                    "filename": filename,
                    "processing_status": status,
                    "started_at": started_at,
                    "processed_at": None,
                    "error": error,
                }
            },
            upsert=True,
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


document_processing_service = DocumentProcessingService()
