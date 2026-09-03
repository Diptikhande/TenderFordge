# TenderForge

## Project Title

Multi-Agent Cognitive Platform for Automated Tender Intelligence

## Purpose

TenderForge is a private, company-specific AI decision-support platform designed to
evaluate tender documents that an organization has independently obtained and chosen to upload.

It is not a tender marketplace, public tender browsing platform, tender receiving system, or automatic tender collection system.

The system allows one organization to maintain a reusable company profile
and upload tender documents for evaluation against that profile.

The system extracts and understands tender requirements, retrieves
relevant information using Hybrid Retrieval-Augmented Generation (RAG),
compares tender requirements with company capabilities, identifies
compliance gaps and risks, and produces an explainable recommendation.

## Core Workflow

Company Profile
        ↓
Tender Upload
        ↓
Document Processing
        ↓
Knowledge Base
        ↓
Hybrid RAG
        ↓
Multi-Agent Analysis
        ↓
Company-Tender Matching
        ↓
Risk & Compliance Analysis
        ↓
Recommendation
        ↓
Tender Intelligence Report

## Recommendation Types

1. BID
2. DO_NOT_BID
3. BID_AFTER_IMPROVEMENTS

## Main AI Agents

1. Eligibility Agent
2. Compliance Agent
3. Risk Agent
4. Proposal Agent
5. Recommendation Agent

## Important Principle

TenderForge is a decision-support system.

It must provide evidence and reasoning for its recommendations.

It must not invent company information or tender requirements.

When reliable evidence cannot be found, the system should return
UNKNOWN rather than guessing.

## Technology

Frontend:
- React
- TypeScript
- Tailwind CSS

Backend:
- Python
- FastAPI

Database:
- MongoDB

Vector Database:
- Qdrant

AI Orchestration:
- LangGraph

RAG:
- Hybrid Retrieval-Augmented Generation
- Semantic/vector search
- Keyword retrieval

Document Processing:
- PyMuPDF
- Tesseract OCR

Supporting Infrastructure:
- Docker
- Redis

## Target Users

- Procurement teams
- Bid/tender teams
- Business development teams
- Organizations responding to tenders

## Core Deliverables

- Company profile management
- Tender upload and management
- Tender document extraction
- Searchable tender knowledge base
- Hybrid RAG
- Multi-agent analysis
- Eligibility analysis
- Compliance analysis
- Risk analysis
- Proposal assistance
- Company-tender capability matching
- Explainable recommendation
- Tender intelligence report
