"""不足モデルのダウンロード API（SPEC §3.3 / §9）。

設定ページの「モデル」タブで「未検出」になったファイルを、ここから
``COMFY_MODELS_DIR`` の models ディレクトリへ直接落とす。実際のダウンロードは :mod:`app.model_download`
がバックグラウンドタスクで行い、進捗は WS ``/api/ws`` に ``type: "model_download"``
として流れる。
"""

from fastapi import APIRouter, HTTPException

from .. import model_download
from ..models import (
    ModelDownload,
    ModelDownloadRequest,
    ModelsDirStatus,
)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("/dir-status", response_model=ModelsDirStatus)
async def get_dir_status() -> ModelsDirStatus:
    """models ディレクトリが設定され、存在し、書き込めるか。"""
    return model_download.dir_status()


@router.get("/downloads", response_model=list[ModelDownload])
async def get_downloads() -> list[ModelDownload]:
    """進行中のダウンロードと、直近の完了 / 失敗。"""
    return model_download.downloads()


@router.post("/download", response_model=ModelDownload)
async def post_download(payload: ModelDownloadRequest) -> ModelDownload:
    """1 ファイルのダウンロードを開始する（すぐ返り、進捗は WS で流れる）。"""
    try:
        return model_download.start(
            payload.filename, payload.url, payload.subfolder
        )
    except model_download.DownloadBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except model_download.DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
