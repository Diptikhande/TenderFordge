"""
TenderForge PDF Processor
Extracts text from PDF files using PyMuPDF (fitz).
Handles invalid, empty, and scanned PDFs gracefully.
"""

import os
import hashlib
import fitz  # PyMuPDF


def extract_text_from_pdf(file_path: str) -> dict:
    """
    Extract text from a PDF file.

    Returns:
        dict with keys:
            - success (bool)
            - text (str): extracted text
            - page_count (int)
            - error (str): error message if any
            - warning (str): warning if PDF appears scanned
    """
    result = {
        "success": False,
        "text": "",
        "page_count": 0,
        "error": "",
        "warning": "",
    }

    if not os.path.exists(file_path):
        result["error"] = "File not found."
        return result
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        result["error"] = f"Could not open PDF: {str(e)}"
        return result

    try:
        result["page_count"] = len(doc)

        if len(doc) == 0:
            result["error"] = "PDF has no pages."
            doc.close()
            return result

        full_text = []
        pages_data = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_text = page.get_text("text")
            if page_text.strip():
                full_text.append(f"--- [Page {page_num + 1}] ---\n{page_text}")
                pages_data.append({"page": page_num + 1, "text": page_text})

        doc.close()

        combined_text = "\n".join(full_text).strip()

        if not combined_text:
            result["warning"] = (
                "No extractable text found. This PDF may be scanned/image-based. "
                "OCR support is not available in this version."
            )
            result["success"] = True
            return result

        # Heuristic: if extracted text is very short relative to pages, warn
        avg_chars = len(combined_text) / result["page_count"]
        if avg_chars < 50:
            result["warning"] = (
                "Very little text extracted. Some pages may be scanned images."
            )

        result["text"] = combined_text
        result["pages"] = pages_data
        result["success"] = True
        return result

    except Exception as e:
        doc.close()
        result["error"] = f"Error processing PDF: {str(e)}"
        return result


def chunk_pdf_pages(pages: list[dict], max_chars: int = 1200, overlap: int = 160) -> list[dict]:
    """Create deterministic, page-local text chunks with source offsets and stable IDs."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100 characters.")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be non-negative and smaller than max_chars.")

    chunks = []
    for item in pages or []:
        try:
            page_number = int(item.get("page", item.get("page_number")))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text") or "")
        if page_number < 1 or not text.strip():
            continue
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                boundary = max(text.rfind("\n", start + max_chars // 2, end),
                               text.rfind(" ", start + max_chars // 2, end))
                if boundary > start:
                    end = boundary
            raw = text[start:end]
            left_trim = len(raw) - len(raw.lstrip())
            chunk_text = raw.strip()
            actual_start = start + left_trim
            actual_end = actual_start + len(chunk_text)
            if chunk_text:
                digest = hashlib.sha256(
                    f"{page_number}:{actual_start}:{actual_end}:{chunk_text}".encode("utf-8")
                ).hexdigest()[:24]
                chunks.append({
                    "chunk_id": f"p{page_number}-{digest}",
                    "page_number": page_number,
                    "start_char": actual_start,
                    "end_char": actual_end,
                    "text": chunk_text,
                    "text_sha256": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                })
            if end >= len(text):
                break
            next_start = max(start + 1, end - overlap)
            while next_start < end and text[next_start].isspace():
                next_start += 1
            start = next_start
    return chunks


def save_uploaded_file(uploaded_file, upload_dir: str) -> str:
    """
    Save an uploaded file to disk.

    Args:
        uploaded_file: Streamlit UploadedFile object
        upload_dir: Directory to save to

    Returns:
        Full path to saved file
    """
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, uploaded_file.name)

    # If file already exists, add a numeric suffix
    base, ext = os.path.splitext(uploaded_file.name)
    counter = 1
    while os.path.exists(file_path):
        file_path = os.path.join(upload_dir, f"{base}_{counter}{ext}")
        counter += 1

    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    return file_path
