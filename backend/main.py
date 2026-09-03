#!/usr/bin/env python3
"""
TenderForge Backend Entry Point

This script starts the FastAPI server for the TenderForge platform.
"""
import sys
import uvicorn
from app.config import API_HOST, API_PORT, DEBUG

if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("TenderForge Backend - Starting FastAPI Server")
    print(f"{'='*60}")
    print(f"Host: {API_HOST}")
    print(f"Port: {API_PORT}")
    print(f"Debug: {DEBUG}")
    print(f"API Docs: http://localhost:{API_PORT}/docs")
    print(f"{'='*60}\n")
    
    uvicorn.run(
        "app.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=DEBUG,
        log_level="info",
    )
