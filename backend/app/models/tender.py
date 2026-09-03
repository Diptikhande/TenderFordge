from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime


class TenderBase(BaseModel):
    """Base tender schema."""
    tender_id: str
    filename: str
    status: str = "Not Processed"


class TenderCreate(TenderBase):
    """Schema for creating a tender record."""
    discovered_at: datetime = datetime.utcnow()


class TenderResponse(TenderBase):
    """Schema for tender response."""
    id: Optional[str] = None
    discovered_at: Optional[datetime] = None
    document_size_bytes: Optional[int] = None
    
    class Config:
        from_attributes = True


class UploadResponse(BaseModel):
    """Schema for file upload response."""
    success: bool
    message: str
    tender_id: Optional[str] = None


class ExtractedPage(BaseModel):
    """Schema for extracted page text."""
    page_number: int
    text: str
    raw_char_count: int = 0
    cleaned_char_count: int = 0
    has_text: bool = False


class TenderChunk(BaseModel):
    """Schema for processed tender chunks."""
    chunk_id: str
    tender_id: str
    start_page: int
    end_page: int
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSection(BaseModel):
    """Schema for simple detected document structure."""
    section_id: str
    page_number: int
    line_number: int
    title: str
    heading_type: str


class TenderProcessingResponse(BaseModel):
    """Schema for tender processing metadata and extracted content."""
    tender_id: str
    filename: str
    page_count: Optional[int] = None
    processing_status: str
    started_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    extracted_text_available: bool = False
    empty_or_scanned: bool = False
    pages: list[ExtractedPage] = Field(default_factory=list)
    document_structure: list[DocumentSection] = Field(default_factory=list)
    chunk_count: int = 0
    chunks: list[TenderChunk] = Field(default_factory=list)
    error: Optional[str] = None
