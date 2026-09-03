from app.utils.file_scanner import scan_available_tenders
from typing import List, Dict


class TenderService:
    """Service for tender-related operations."""
    
    @staticmethod
    def get_all_tenders() -> List[Dict]:
        """
        Get all tenders discovered in dataset/tenders/.
        
        In Section 1, this simply returns discovered PDFs with demo status.
        Future sections will add AI analysis results from MongoDB.
        """
        tenders = scan_available_tenders()
        return tenders
    
    @staticmethod
    def get_tender_by_id(tender_id: str) -> Dict | None:
        """Get a single tender by ID."""
        tenders = scan_available_tenders()
        for tender in tenders:
            if tender["tender_id"] == tender_id:
                return tender
        return None
    
    @staticmethod
    def validate_pdf_file(filename: str) -> bool:
        """Validate that a file is a PDF."""
        return filename.lower().endswith(".pdf")


# Create singleton instance
tender_service = TenderService()
