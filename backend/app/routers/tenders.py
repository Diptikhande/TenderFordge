from fastapi import APIRouter, HTTPException
from pymongo.errors import PyMongoError

from app.services.document_processing_service import document_processing_service
from app.services.tender_service import tender_service
from app.models.tender import TenderProcessingResponse, TenderResponse

router = APIRouter(prefix="/api/tenders", tags=["tenders"])


@router.get("/", response_model=list[TenderResponse])
async def get_all_tenders():
    """
    Get all tenders discovered in dataset/tenders/.
    
    Returns a list of discovered PDF files with metadata.
    In Section 1, status is always "Not Processed".
    Future sections will add AI analysis results.
    """
    tenders = tender_service.get_all_tenders()
    return [
        TenderResponse(
            id=tender["tender_id"],
            tender_id=tender["tender_id"],
            filename=tender["filename"],
            status=tender["status"],
            discovered_at=None,  # Will be enhanced in future sections
            document_size_bytes=tender["document_size_bytes"],
        )
        for tender in tenders
    ]


@router.get("/{tender_id}", response_model=TenderResponse)
async def get_tender(tender_id: str):
    """Get a specific tender by ID."""
    tender = tender_service.get_tender_by_id(tender_id)
    if tender is None:
        raise HTTPException(status_code=404, detail="Tender not found")
    
    return TenderResponse(
        id=tender["tender_id"],
        tender_id=tender["tender_id"],
        filename=tender["filename"],
        status=tender["status"],
        discovered_at=None,
        document_size_bytes=tender["document_size_bytes"],
    )


@router.post("/{tender_id}/process", response_model=TenderProcessingResponse)
async def process_tender(tender_id: str):
    """Process a selected tender PDF into page text and chunks."""
    try:
        result = document_processing_service.process_tender(tender_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Tender not found")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"Processing storage unavailable: {exc}")

    if result["processing_status"] == "FAILED":
        return result
    result["chunks"] = document_processing_service.get_processing_result(tender_id)["chunks"]
    return result


@router.get("/{tender_id}/processing", response_model=TenderProcessingResponse)
async def get_tender_processing(tender_id: str):
    """Get processing status, extracted pages, and generated chunks."""
    try:
        return document_processing_service.get_processing_result(tender_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Tender not found")
    except PyMongoError as exc:
        raise HTTPException(status_code=503, detail=f"Processing storage unavailable: {exc}")
