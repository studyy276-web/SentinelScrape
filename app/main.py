"""SentinelScrape FastAPI Application Entrypoint."""

from fastapi import FastAPI
from app.api.analyze import router as analyze_router
from app.api.health import router as health_router


def create_app() -> FastAPI:
    """Factory function to create and configure the FastAPI application."""
    application = FastAPI(
        title="SentinelScrape",
        description="Trust layer between Bright Data Scraper Studio and Gemini.",
        version="0.1.0",
    )

    # Register API routers
    application.include_router(health_router)
    application.include_router(analyze_router)

    return application


app = create_app()
