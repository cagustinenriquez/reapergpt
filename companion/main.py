from __future__ import annotations

from fastapi import FastAPI

from companion.api.routes import router as api_router
from companion.config import get_settings
from companion.logging_conf import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.debug)
    app = FastAPI(
        title="ReaperDAW MVP Agent",
        version="0.1.0",
        debug=settings.debug,
    )
    app.include_router(api_router)
    return app


app = create_app()
