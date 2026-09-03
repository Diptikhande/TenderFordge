from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import DEBUG, CORS_ORIGINS, API_HOST, API_PORT
from app.database import get_database, close_database
from app.routers import health, tenders

# Initialize FastAPI application
app = FastAPI(
    title="TenderForge API",
    description="Multi-Agent Cognitive Platform for Automated Tender Intelligence",
    version="0.1.0",
    debug=DEBUG,
)

# Add CORS middleware to allow frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Startup event: Initialize database and services
@app.on_event("startup")
async def startup_event():
    """Initialize connections on startup."""
    print("\n🚀 TenderForge Backend Starting Up...")
    try:
        db = get_database()
        print(f"✓ Database initialized: {db.name}")
        
        # Import file scanner to discover tenders at startup
        from app.utils.file_scanner import scan_available_tenders
        tenders = scan_available_tenders()
        print(f"✓ Tender discovery complete: {len(tenders)} tender(s) found\n")
    except Exception as e:
        print(f"⚠ Warning during startup: {e}")
        print("  (Application will continue, but some features may not work)\n")


# Shutdown event: Close database connections
@app.on_event("shutdown")
async def shutdown_event():
    """Clean up connections on shutdown."""
    print("\n🛑 TenderForge Backend Shutting Down...")
    close_database()
    print("✓ Database connection closed\n")


# Include routers
app.include_router(health.router)
app.include_router(tenders.router)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "TenderForge",
        "version": "0.1.0",
        "message": "Welcome to TenderForge - Multi-Agent Tender Intelligence Platform",
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=DEBUG,
    )
