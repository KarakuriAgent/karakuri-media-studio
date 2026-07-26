from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import ws
from .config import load_settings
from .db import init_db
from .jobs import runner
from .paths import ASSETS_DIR, OUTPUTS_DIR, ensure_dirs
from .routers import assets, chat, health, jobs, loras, options, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    await init_db()
    load_settings()
    await runner.start()
    try:
        yield
    finally:
        await runner.stop()


app = FastAPI(title="Video Studio API", version="0.1.0", lifespan=lifespan)

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
app.include_router(assets.router)
app.include_router(options.router)
app.include_router(chat.router)
app.include_router(jobs.router)
app.include_router(ws.router)

ensure_dirs()
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
