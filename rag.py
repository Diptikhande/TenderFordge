"""Local embedding, durable tender indexing, and user-scoped evidence retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache

from config import (EMBEDDING_DEVICE, EMBEDDING_MODEL, RAG_CHUNK_OVERLAP,
                    RAG_CHUNK_SIZE)
from database import get_connection, get_tender_by_id
from pdf_processor import chunk_pdf_pages, extract_text_from_pdf

CHUNKER_VERSION = "page-window-v1"


class EmbeddingModelUnavailable(RuntimeError):
    """Raised when the configured local embedding model cannot be loaded or used."""


def _create_embedding_model(model_name: str, device: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingModelUnavailable(
            "Local embeddings require sentence-transformers. Install the project requirements and configure a local model."
        ) from exc
    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as exc:
        raise EmbeddingModelUnavailable(
            f"Could not load local embedding model '{model_name}': {str(exc)[:240]}"
        ) from exc


@lru_cache(maxsize=3)
def _get_embedding_model(model_name: str, device: str):
    return _create_embedding_model(model_name, device)


def _failure(error_type: str, error: str, **extra) -> dict:
    return {"success": False, "error_type": error_type, "error": error, **extra}


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_vectors(raw, expected_count: int) -> list[list[float]]:
    if hasattr(raw, "detach"):
        raw = raw.detach().cpu().tolist()
    elif hasattr(raw, "tolist"):
        raw = raw.tolist()
    if expected_count == 1 and raw and isinstance(raw[0], (int, float)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or len(raw) != expected_count:
        raise ValueError("Embedding model returned an unexpected number of vectors.")
    vectors = []
    dimension = None
    for row in raw:
        if hasattr(row, "tolist"):
            row = row.tolist()
        vector = [float(value) for value in row]
        if not vector or any(not math.isfinite(value) for value in vector):
            raise ValueError("Embedding model returned an empty or non-finite vector.")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension:
            raise ValueError("Embedding model returned vectors with inconsistent dimensions.")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError("Embedding model returned a zero vector.")
        vectors.append([value / norm for value in vector])
    return vectors


def _encode(texts: list[str], model_name: str, embedder=None) -> list[list[float]]:
    if not texts:
        return []
    model = embedder if embedder is not None else _get_embedding_model(model_name, EMBEDDING_DEVICE)
    try:
        try:
            raw = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        except TypeError:
            # Keep simple local/fake embedders useful for offline tests and custom installations.
            raw = model.encode(texts)
    except Exception as exc:
        raise EmbeddingModelUnavailable(f"Local embedding failed: {str(exc)[:240]}") from exc
    return _as_vectors(raw, len(texts))


def index_tender(user_id: int, tender_id: int, *, embedder=None, model_name: str | None = None) -> dict:
    """Extract, chunk, embed, and atomically persist one owner's tender index."""
    if not user_id or not tender_id:
        return _failure("invalid_owner", "A user ID and tender ID are required to index a tender.")
    model_name = (model_name or EMBEDDING_MODEL).strip()
    try:
        tender = get_tender_by_id(tender_id, user_id)
        if not tender:
            return _failure("not_found", "Tender was not found for this user.")
        pdf_path = tender.get("filepath") or ""
        if not pdf_path or not os.path.isfile(pdf_path):
            return _failure("pdf_unavailable", "The original tender PDF is unavailable; the document was not indexed.")
        source_sha256 = _file_sha256(pdf_path)
        extracted = extract_text_from_pdf(pdf_path)
        if not extracted.get("success"):
            return _failure("pdf_extraction_failed", extracted.get("error") or "PDF text extraction failed.")
        if not extracted.get("text", "").strip() or not extracted.get("pages"):
            return _failure("no_extractable_text", extracted.get("warning") or
                            "No extractable PDF text was found. OCR is unavailable; the tender was not indexed.")
        chunks = chunk_pdf_pages(extracted["pages"], RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)
        if not chunks:
            return _failure("no_chunks", "PDF extraction produced no non-empty text chunks.")

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT source_sha256, embedding_model, chunker_version, chunk_size, chunk_overlap,
                       chunk_count, embedding_dimension, status
                FROM tender_rag_indexes WHERE user_id = ? AND tender_id = ?
            """, (user_id, tender_id))
            previous = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM tender_chunks WHERE user_id = ? AND tender_id = ?",
                           (user_id, tender_id))
            stored_count = cursor.fetchone()[0]
            if previous and previous["status"] == "ready" and \
                    previous["source_sha256"] == source_sha256 and \
                    previous["embedding_model"] == model_name and \
                    previous["chunker_version"] == CHUNKER_VERSION and \
                    previous["chunk_size"] == RAG_CHUNK_SIZE and \
                    previous["chunk_overlap"] == RAG_CHUNK_OVERLAP and \
                    previous["chunk_count"] == len(chunks) == stored_count:
                return {"success": True, "indexed": False, "idempotent": True,
                        "chunk_count": len(chunks), "embedding_model": model_name,
                        "source_sha256": source_sha256, "embedding_dimension": previous["embedding_dimension"]}

            vectors = _encode([chunk["text"] for chunk in chunks], model_name, embedder)
            dimension = len(vectors[0])
            if any(len(vector) != dimension for vector in vectors):
                raise ValueError("Embedding dimensions changed during indexing.")

            with conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM tender_chunks WHERE user_id = ? AND tender_id = ?", (user_id, tender_id))
                cursor.executemany("""
                    INSERT INTO tender_chunks (
                        user_id, tender_id, chunk_id, page_number, start_char, end_char,
                        text, text_sha256, embedding_json, embedding_model
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [(user_id, tender_id, chunk["chunk_id"], chunk["page_number"],
                       chunk["start_char"], chunk["end_char"], chunk["text"], chunk["text_sha256"],
                       json.dumps(vector, separators=(",", ":")), model_name)
                      for chunk, vector in zip(chunks, vectors)])
                cursor.execute("""
                    INSERT INTO tender_rag_indexes (
                        user_id, tender_id, source_sha256, embedding_model, embedding_dimension,
                        chunker_version, chunk_size, chunk_overlap, chunk_count, status, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, tender_id) DO UPDATE SET
                        source_sha256 = excluded.source_sha256, embedding_model = excluded.embedding_model,
                        embedding_dimension = excluded.embedding_dimension, chunker_version = excluded.chunker_version,
                        chunk_size = excluded.chunk_size, chunk_overlap = excluded.chunk_overlap,
                        chunk_count = excluded.chunk_count, status = 'ready', indexed_at = CURRENT_TIMESTAMP
                """, (user_id, tender_id, source_sha256, model_name, dimension, CHUNKER_VERSION,
                      RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP, len(chunks)))
            return {"success": True, "indexed": True, "idempotent": False,
                    "chunk_count": len(chunks), "embedding_model": model_name,
                    "source_sha256": source_sha256, "embedding_dimension": dimension,
                    "warning": extracted.get("warning", "")}
        finally:
            conn.close()
    except EmbeddingModelUnavailable as exc:
        return _failure("embedding_model_unavailable", str(exc))
    except Exception as exc:
        return _failure("indexing_failed", f"Tender indexing failed: {str(exc)[:300]}")


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Stored and query embedding dimensions do not match.")
    return sum(a * b for a, b in zip(left, right))


def retrieve_tender_chunks(user_id: int, tender_id: int, query: str, *, top_k: int = 5,
                           min_score: float = 0.12, embedder=None,
                           model_name: str | None = None) -> dict:
    """Return cosine-ranked chunks with source citations, scoped to one user and tender."""
    if not user_id or not tender_id:
        return _failure("invalid_owner", "A user ID and tender ID are required for retrieval.")
    if not str(query or "").strip():
        return _failure("invalid_query", "A non-empty evidence query is required.")
    model_name = (model_name or EMBEDDING_MODEL).strip()
    conn = None
    try:
        tender = get_tender_by_id(tender_id, user_id)
        if not tender:
            return _failure("not_found", "Tender was not found for this user.")
        pdf_path = tender.get("filepath") or ""
        if not pdf_path or not os.path.isfile(pdf_path):
            return _failure("pdf_unavailable", "The tender PDF is unavailable; indexed evidence cannot be verified.")
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM tender_rag_indexes WHERE user_id = ? AND tender_id = ?
        """, (user_id, tender_id))
        index = cursor.fetchone()
        if not index or index["status"] != "ready":
            return _failure("index_unavailable", "No ready evidence index exists for this tender.")
        if index["embedding_model"] != model_name:
            return _failure("index_stale", "The evidence index uses a different embedding model and must be rebuilt.")
        if index["source_sha256"] != _file_sha256(pdf_path):
            return _failure("index_stale", "The tender PDF changed after indexing and must be re-indexed.")
        cursor.execute("""
            SELECT chunk_id, page_number, start_char, end_char, text, text_sha256, embedding_json
            FROM tender_chunks
            WHERE user_id = ? AND tender_id = ? AND embedding_model = ?
        """, (user_id, tender_id, model_name))
        rows = cursor.fetchall()
        if not rows or len(rows) != index["chunk_count"]:
            return _failure("index_corrupt", "The stored evidence index is incomplete; re-index the tender.")
        query_vector = _encode([query], model_name, embedder)[0]
        ranked = []
        for row in rows:
            vector = json.loads(row["embedding_json"])
            score = _cosine_similarity(query_vector, vector)
            if score >= min_score:
                ranked.append({
                    "chunk_id": row["chunk_id"], "page_number": row["page_number"],
                    "start_char": row["start_char"], "end_char": row["end_char"],
                    "text": row["text"], "text_sha256": row["text_sha256"],
                    "score": round(score, 6),
                })
        ranked.sort(key=lambda item: (-item["score"], item["page_number"], item["chunk_id"]))
        if not ranked:
            return _failure("no_relevant_chunks", "No tender chunks met the retrieval relevance threshold.")
        return {"success": True, "chunks": ranked[:max(1, min(int(top_k), 20))],
                "embedding_model": model_name, "source_sha256": index["source_sha256"],
                "index_chunk_count": index["chunk_count"]}
    except EmbeddingModelUnavailable as exc:
        return _failure("embedding_model_unavailable", str(exc))
    except Exception as exc:
        return _failure("retrieval_failed", f"Tender evidence retrieval failed: {str(exc)[:300]}")
    finally:
        if conn is not None:
            conn.close()


def build_requirement_retrieval_queries(requirements: list[dict] | None = None) -> list[str]:
    """Build broad procurement queries plus user-curated clauses for requirement evidence retrieval."""
    queries = [
        "mandatory eligibility qualification bidder requirements experience registration certifications",
        "technical requirements specifications compliance standards product equipment performance",
        "financial requirements annual turnover revenue net worth solvency bid security fees",
        "documents to submit supporting certificates declarations audit reports eligibility checklist",
        "conditional obligations deadlines delivery milestones penalties contract terms",
    ]
    for row in requirements or []:
        text = " ".join(str(row.get(key) or "") for key in ("requirement", "source_quotation", "required_document")).strip()
        if text:
            queries.append(text[:500])
        if len(queries) >= 15:
            break
    return list(dict.fromkeys(queries))


def _retrieve_requirement_source_chunks(user_id: int, tender_id: int, requirements: list[dict],
                                       model_name: str, source_sha256: str) -> tuple[list[dict], list[str], list[str]]:
    """Add indexed chunks only when a requirement quote matches both its source page and chunk text."""
    from analyzer import _source_evidence_matches, _validated_retrieved_chunks, split_pages

    tender = get_tender_by_id(tender_id, user_id)
    if not tender or not tender.get("extracted_text"):
        return [], [], [str(r.get("requirement_id") or "") for r in requirements]
    pages = split_pages(tender["extracted_text"])
    conn = get_connection()
    try:
        index = conn.execute("""
            SELECT source_sha256, embedding_model, status FROM tender_rag_indexes
            WHERE user_id = ? AND tender_id = ?
        """, (user_id, tender_id)).fetchone()
        if (not index or index["status"] != "ready" or index["embedding_model"] != model_name or
                index["source_sha256"] != source_sha256):
            return [], [], [str(r.get("requirement_id") or "") for r in requirements]
        rows = conn.execute("""
            SELECT chunk_id, page_number, start_char, end_char, text, text_sha256
            FROM tender_chunks
            WHERE user_id = ? AND tender_id = ? AND embedding_model = ?
            ORDER BY page_number, start_char, chunk_id
        """, (user_id, tender_id, model_name)).fetchall()
    finally:
        conn.close()

    indexed = []
    for row in rows:
        text = str(row["text"] or "")
        if not text or hashlib.sha256(text.encode("utf-8")).hexdigest() != row["text_sha256"]:
            continue
        indexed.append({"chunk_id": row["chunk_id"], "page_number": row["page_number"],
                        "start_char": row["start_char"], "end_char": row["end_char"],
                        "text": text, "text_sha256": row["text_sha256"], "score": 0.0,
                        "retrieval_method": "validated_requirement_source_match"})
    indexed = _validated_retrieved_chunks(tender["extracted_text"], indexed)

    matches, matched_ids, unmatched_ids = {}, [], []
    for requirement in requirements:
        requirement_id = str(requirement.get("requirement_id") or "")
        quote = str(requirement.get("source_quotation") or "").strip()
        try:
            page_number = int(requirement.get("source_page"))
        except (TypeError, ValueError):
            page_number = 0
        page_text = pages.get(page_number, "")
        if not requirement_id or not quote or not page_text or not _source_evidence_matches(quote, page_text):
            if requirement_id:
                unmatched_ids.append(requirement_id)
            continue
        candidates = [chunk for chunk in indexed if chunk["page_number"] == page_number and
                      _source_evidence_matches(quote, chunk["text"])]
        if not candidates:
            unmatched_ids.append(requirement_id)
            continue
        candidate = min(candidates, key=lambda chunk: (len(chunk["text"]), chunk["chunk_id"]))
        candidate = {**candidate, "source_requirement_ids": [requirement_id]}
        existing = matches.get(candidate["chunk_id"])
        if existing:
            existing["source_requirement_ids"].append(requirement_id)
        else:
            matches[candidate["chunk_id"]] = candidate
        matched_ids.append(requirement_id)
    return list(matches.values()), matched_ids, unmatched_ids


def retrieve_requirement_evidence(user_id: int, tender_id: int, requirements: list[dict] | None = None, *,
                                  embedder=None, model_name: str | None = None,
                                  max_chunks: int = 12, top_k_per_query: int = 3) -> dict:
    """Retrieve semantic results plus exact, page-validated chunks for deterministic tender requirements."""
    model_name = (model_name or EMBEDDING_MODEL).strip()
    tender = get_tender_by_id(tender_id, user_id)
    if not tender:
        return _failure("not_found", "Tender was not found for this user.")
    requirement_source = "provided"
    if requirements is None:
        requirement_source = "deterministic_extraction"
        try:
            from analyzer import extract_requirements_with_report
            requirements, _ = extract_requirements_with_report(
                tender.get("extracted_text") or "", pdf_path=tender.get("filepath"))
        except Exception as exc:
            requirements = []
            extraction_error = f"Could not derive requirement-specific retrieval queries: {type(exc).__name__}: {str(exc)[:200]}"
        else:
            extraction_error = ""
    else:
        extraction_error = ""
    requirements = [r for r in (requirements or []) if isinstance(r, dict)]
    collected, query_errors, source_sha256 = {}, [], None
    queries = build_requirement_retrieval_queries(requirements)
    for query in queries:
        result = retrieve_tender_chunks(user_id, tender_id, query, top_k=top_k_per_query,
                                        embedder=embedder, model_name=model_name)
        if result["success"]:
            source_sha256 = result.get("source_sha256") or source_sha256
            for chunk in result["chunks"]:
                current = collected.get(chunk["chunk_id"])
                if current is None or chunk["score"] > current["score"]:
                    collected[chunk["chunk_id"]] = chunk
        elif result.get("error_type") not in ("no_relevant_chunks",):
            return result
        else:
            query_errors.append(result.get("error_type"))
    chunks = sorted(collected.values(), key=lambda item: (-item["score"], item["page_number"], item["chunk_id"]))[:max_chunks]
    source_sha256 = source_sha256 or (_file_sha256(tender["filepath"]) if tender.get("filepath") and
                                      os.path.isfile(tender["filepath"]) else "")
    source_chunks, matched_ids, unmatched_ids = _retrieve_requirement_source_chunks(
        user_id, tender_id, requirements, model_name, source_sha256)
    for source_chunk in source_chunks:
        current = collected.get(source_chunk["chunk_id"])
        if current:
            current["source_requirement_ids"] = source_chunk["source_requirement_ids"]
            current["retrieval_method"] = "semantic_and_validated_requirement_source_match"
        else:
            collected[source_chunk["chunk_id"]] = source_chunk
    protected = set(collected_chunk["chunk_id"] for collected_chunk in source_chunks)
    semantic_sorted = sorted((chunk for chunk_id, chunk in collected.items() if chunk_id not in protected),
                             key=lambda item: (-item["score"], item["page_number"], item["chunk_id"]))
    limit = max(max(1, int(max_chunks)), len(protected))
    chunks = sorted([collected[chunk_id] for chunk_id in protected] + semantic_sorted[:max(0, limit - len(protected))],
                    key=lambda item: (-item["score"], item["page_number"], item["chunk_id"]))
    if not chunks:
        return _failure("no_relevant_chunks", "No relevant tender evidence was retrieved for requirement extraction.",
                        query_count=len(queries))
    return {"success": True, "chunks": chunks, "embedding_model": model_name,
            "query_count": len(queries), "retrieved_count": len(chunks),
            "queries_without_matches": len(query_errors),
            "source_sha256": source_sha256,
            "requirement_source": requirement_source,
            "requirement_source_count": len(requirements),
            "source_matched_requirement_ids": matched_ids,
            "source_unmatched_requirement_ids": unmatched_ids,
            "source_matched_chunk_count": len(source_chunks),
            "requirement_query_error": extraction_error}
