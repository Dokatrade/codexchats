from __future__ import annotations

import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_config
from app.db import connect, init_db
from app.indexer import Indexer
from app.repository import Repository
from app.web.routes import build_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("codexchats")


def create_app() -> FastAPI:
    config = load_config()
    conn = connect(config.db_file)
    init_db(conn)
    repo = Repository(conn)
    indexer = Indexer(config, repo)

    app = FastAPI(title="Codex Chats Local Archive", version="0.1.0")
    app.state.config = config
    app.state.conn = conn
    app.state.repo = repo
    app.state.indexer = indexer

    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(base_dir / "web" / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "web" / "static")), name="static")
    app.include_router(build_router(templates))

    @app.on_event("startup")
    async def _startup() -> None:
        if not config.sources:
            logger.warning("No sources configured. Add sources to config.json/config.example.json.")
            return
        if config.scan_on_startup:
            summary = indexer.run_startup_scan()
            if summary is not None:
                logger.info(
                    "Startup scan finished: mode=%s status=%s seen=%s changed=%s deleted=%s errors=%s",
                    summary.mode,
                    summary.status,
                    summary.files_seen,
                    summary.files_changed,
                    summary.files_deleted,
                    summary.errors_count,
                )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to close DB connection")

    return app


app = create_app()


if __name__ == "__main__":
    cfg = app.state.config
    uvicorn.run(app, host=cfg.host, port=cfg.port, reload=False)
