import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import agent_runner, canvas_agent, ws
from .config import load_settings
from .db import init_db
from .jobs import recover_interrupted_jobs, runner
from .paths import (
    ASSETS_DIR,
    FRONTEND_DIST_DIR,
    LIBRARY_DIR,
    OUTPUTS_DIR,
    ensure_dirs,
)
from .routers import (
    agent,
    assets,
    canvas,
    chat,
    external,
    grok,
    health,
    jobs,
    library,
    loras,
    model_download,
    models_config,
    options,
    push,
    settings,
    studio,
)
from .workflows import validate_specs

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    await init_db()
    load_settings()
    # A hand-edited workflow/*.json that no longer matches its manifest would
    # otherwise only surface as a failed job (SPEC §3); surface it at startup
    # and in GET /api/health instead.
    for problem in validate_specs(use_cache=False):
        log.error("workflow manifest mismatch: %s", problem)
    await runner.start()
    # 前回のプロセスが落ちたときに残った queued / running を拾い直す（SPEC §5）。
    await recover_interrupted_jobs()
    try:
        yield
    finally:
        await agent_runner.stop_all()
        await canvas_agent.stop_all()
        await runner.stop()


app = FastAPI(title="Karakuri Media Studio API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(settings.router)
app.include_router(loras.router)
app.include_router(models_config.router)
app.include_router(model_download.router)
app.include_router(library.router)
app.include_router(assets.router)
app.include_router(options.router)
app.include_router(chat.router)
app.include_router(grok.router)
app.include_router(jobs.router)
app.include_router(studio.router)
app.include_router(external.router)
app.include_router(canvas.router)
app.include_router(agent.router)
app.include_router(push.router)
app.include_router(ws.router)

ensure_dirs()
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
app.mount("/library", StaticFiles(directory=LIBRARY_DIR), name="library")


# python:3.12-slim には /etc/mime.types が無く、標準の mimetypes だけだと
# .webmanifest が判別できずに text/plain で返ってしまう（PWA のマニフェストが
# 読まれない）。明示的に登録しておく。
mimetypes.add_type("application/manifest+json", ".webmanifest")


def _dist_file(rel_path: str) -> Path | None:
    """Resolve `rel_path` inside frontend/dist, refusing anything that escapes it."""
    candidate = (FRONTEND_DIST_DIR / rel_path).resolve()
    root = FRONTEND_DIST_DIR.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


# Service Worker とその登録スクリプト、そして SPA シェルは常に取り直させる。
# 古い sw.js がキャッシュに居座ると registerType: "autoUpdate" でも更新が
# 届かなくなるため（PWA、SPEC §7）。
_NO_STORE_FILES = frozenset({"sw.js", "registerSW.js", "index.html", "push-sw.js"})


def _dist_response(file: Path) -> FileResponse:
    headers = {"Cache-Control": "no-cache"} if file.name in _NO_STORE_FILES else None
    return FileResponse(file, headers=headers)


# Production serving: when the frontend has been built, serve it as an SPA.
# Registered last so /api, /outputs, /assets and /library keep priority.
if FRONTEND_DIST_DIR.is_dir():

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> FileResponse:
        # Unmatched API paths must stay a real 404, not the SPA shell.
        if spa_path == "api" or spa_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # 実ファイル（sw.js / manifest.webmanifest / static/… など）が最優先。
        # 見つからないパスだけを index.html にフォールバックする。
        file = _dist_file(spa_path) if spa_path else None
        if file is None:
            index = FRONTEND_DIST_DIR / "index.html"
            if not index.is_file():
                raise HTTPException(status_code=404, detail="frontend is not built")
            return _dist_response(index)
        return _dist_response(file)
