from __future__ import annotations

from fastapi import FastAPI

from companion.api.routes import router as api_router
from companion.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ReaperGPT Companion",
        version="0.1.0",
        debug=settings.debug,
    )
    app.include_router(api_router)
    return app


app = create_app()
