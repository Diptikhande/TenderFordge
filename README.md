# TenderForge - Section 1 Foundation + Section 2 Processing

TenderForge is a private, company-specific tender intelligence platform. A company obtains tender documents independently, uploads or selects a tender PDF it wants to evaluate, and will eventually use TenderForge to compare that opportunity with its own profile, evidence, capabilities, experience, certifications, and financial qualifications.

It is not a tender marketplace, tender discovery portal, government tender portal, or a system that automatically receives tender opportunities.

## Section 1 scope

Section 1 establishes the frontend, FastAPI API foundation, MongoDB configuration, and dynamic local-PDF development dataset discovery. It does not implement upload storage, OCR, RAG, embeddings, AI agents, analysis, recommendations, or reports.

The local `dataset/tenders/` directory is development/test data. PDFs in that directory are dynamically discovered at runtime and remain ignored by Git. Adding `T006.pdf` or any further PDF requires no code change.

## Section 2 document processing

Section 2 adds a backend-only processing pipeline for PDFs already present in `dataset/tenders/`.

The pipeline:

- opens each PDF with PyMuPDF
- extracts page-by-page text while preserving page numbers
- applies conservative whitespace and repeated header/footer cleanup
- identifies simple document structure from likely headings and numbered clauses
- creates paragraph-aware chunks with small overlap
- stores processing metadata in MongoDB collection `tender_processing`
- stores chunks in MongoDB collection `tender_chunks`

Large extracted page text is currently stored with processing metadata because the sample PDFs are small enough for this development stage. Chunk storage is separate so later RAG work can read chunk records directly without reshaping the processing document.

## Application areas

- Dashboard - company profile status and counts of local tender documents awaiting analysis.
- Company Profile - the future permanent source of company capabilities and supporting evidence; UI foundation only.
- Tenders - tender opportunities the company has selected for evaluation.
- Upload Tender - PDF selection and validation UI only; documents are not yet stored through the backend.
- Tender Details - real document metadata, with `Not Processed` and `Not Available` analysis states.
- Analysis, Reports, and Settings - foundation empty states only.

## Technology

- Frontend: React, TypeScript, Tailwind CSS, Vite
- Backend: Python, FastAPI, Pydantic, PyMuPDF
- Database configuration: MongoDB
- Local development tooling: Docker Compose

## Running locally

Start MongoDB, then the backend and frontend in separate terminals:

```bash
docker-compose up -d

cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py

cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173`; the API runs on `http://localhost:8000`.

## Available API endpoints

- `GET /api/health`
- `GET /api/tenders/`
- `GET /api/tenders/{tender_id}`
- `POST /api/tenders/{tender_id}/process`
- `GET /api/tenders/{tender_id}/processing`

Tender responses contain dynamically discovered file metadata. Processing endpoints extract text and chunks, but no AI, embedding, RAG, OCR, or recommendation endpoints exist yet.

## Processing verification

Run extraction-only verification without MongoDB:

```bash
cd backend
python scripts/verify_extraction.py
```

Run full MongoDB-backed processing verification:

```bash
docker-compose up -d

cd backend
python scripts/verify_processing.py
```

Expected successful output includes one line per tender with detected page count, generated chunk count, and `extracted_text=True`.

## Next section

Implement company-profile persistence and the tender upload/storage workflow before introducing embeddings, vector search, multi-agent analysis, recommendations, or reports.
