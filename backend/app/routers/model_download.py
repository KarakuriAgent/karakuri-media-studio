"""不足モデルのダウンロード API（SPEC §3.3 / §9）。

設定ページの「モデル」タブで「未検出」になったファイルを、**選んだ接続先環境の**
models ディレクトリへ落とす:

- ``local``  … このアプリが自分でダウンロードし、``COMFY_MODELS_DIR`` に置く
  （:mod:`app.model_download`）
- ``runpod`` … Pod の中で動くダウンロード API に依頼し、進捗をポーリングして
  ローカルと同じ WS フレームで流す（``deploy/runpod/model_api.py``）
- ``comfy_cloud`` … ファイルシステムに触れないので不可（400）

進捗はどちらも WS ``/api/ws`` に ``type: "model_download"`` として流れる。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import comfy, model_download
from ..config import load_settings
from ..models import (
    ComfyTarget,
    ModelDownload,
    ModelDownloadAllRequest,
    ModelDownloadAllResult,
    ModelDownloadRequest,
    ModelsDirStatus,
)
from ..workflow import model_fields
from .loras import loras_for

router = APIRouter(prefix="/api/models", tags=["models"])

#: LoRA ファイルの置き場所（ComfyUI の models/loras）
LORA_SUBFOLDER = "loras"
#: LoRA 一覧を持つノード（未検出判定に使う）
LORA_COMBO = ("LoraLoaderModelOnly", "lora_name")


def _resolve(target: ComfyTarget | None) -> ComfyTarget:
    return target or load_settings().comfy_target


def _reject_cloud(target: ComfyTarget) -> None:
    if target == "comfy_cloud":
        raise HTTPException(
            status_code=400,
            detail="ComfyCloud のモデルは Comfy Cloud 側の管理なので、"
            "ここからはダウンロードできません",
        )


@router.get("/dir-status", response_model=ModelsDirStatus)
async def get_dir_status() -> ModelsDirStatus:
    """ローカル接続で使う models ディレクトリの状態（案内文の材料）。"""
    return model_download.dir_status()


@router.get("/downloads", response_model=list[ModelDownload])
async def get_downloads(target: ComfyTarget | None = None) -> list[ModelDownload]:
    """進行中のダウンロードと、直近の完了 / 失敗。

    ``target=runpod`` では Pod 側の一覧も取り込む（アプリを再起動しても Pod の
    ダウンロードは走り続けるため）。Pod に繋がらないときは手元の分だけ返す。
    """
    if _resolve(target) == "runpod":
        try:
            await model_download.remote_downloads()
        except model_download.DownloadError:
            # 一覧の取得は失敗してもエラーにしない（手元の記録は返せる）
            pass
    return model_download.downloads()


@router.post("/download", response_model=ModelDownload)
async def post_download(payload: ModelDownloadRequest) -> ModelDownload:
    """1 ファイルのダウンロードを開始する（すぐ返り、進捗は WS で流れる）。"""
    target = _resolve(payload.target)
    _reject_cloud(target)
    try:
        if target == "runpod":
            return await model_download.start_remote(
                payload.filename, payload.url, payload.subfolder
            )
        return model_download.start(payload.filename, payload.url, payload.subfolder)
    except model_download.DownloadBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except model_download.DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# 一括ダウンロード（「全DL」）
# --------------------------------------------------------------------------

class _Wanted(BaseModel):
    """その環境で要るファイル 1 件（未検出かどうかの判定材料つき）。"""

    filename: str
    subfolder: str = ""
    #: このファイル名が載るはずの ``/object_info`` の入力（class_type, field）
    combo: tuple[str, str]


def _missing(info: dict, item: _Wanted) -> bool:
    """ComfyUI のファイル一覧に無いか（一覧が取れないときは「無い」と言わない）。

    設定ページの「未検出」バッジと同じ判断: その class_type が ComfyUI に入って
    いない等で一覧そのものが取れないときは判定できないので、勝手に落とさない。
    """
    try:
        installed = comfy.combo_options(info, *item.combo)
    except comfy.ComfyError:
        return False
    return bool(installed) and item.filename not in installed


async def _wanted(target: ComfyTarget) -> list[_Wanted]:
    """その環境で要るモデルファイル（同じ名前は 1 度だけ）。

    ワークフローの各スロットの実効値と候補リスト（実行時に選べるので在って
    ほしい）、それに LoRA 登録のファイル名。
    """
    settings = load_settings()
    overrides = settings.overrides_for(target)
    choices = settings.choices_for(target)
    wanted: dict[str, _Wanted] = {}
    for field in model_fields():
        names = [overrides.get(field.key) or field.default]
        names.extend(choices.get(field.key) or ())
        for name in names:
            if name and name not in wanted:
                wanted[name] = _Wanted(
                    filename=name,
                    subfolder=field.subfolder,
                    combo=(field.class_type, field.field),
                )
    for lora in await loras_for(target):
        if lora.lora_name and lora.lora_name not in wanted:
            wanted[lora.lora_name] = _Wanted(
                filename=lora.lora_name,
                subfolder=LORA_SUBFOLDER,
                combo=LORA_COMBO,
            )
    return list(wanted.values())


@router.post("/download-all", response_model=ModelDownloadAllResult)
async def post_download_all(
    payload: ModelDownloadAllRequest,
) -> ModelDownloadAllResult:
    """未検出かつ取得元 URL が登録済みのファイルをまとめて落とす（SPEC §3.3）。

    「未検出」は選んだ接続先の ComfyUI の ``/object_info`` と比べて決めるので、
    その ComfyUI に繋がっていることが前提（RunPod なら Pod が起動していること）。
    """
    target = _resolve(payload.target)
    _reject_cloud(target)
    try:
        info = await comfy.get_object_info(target=target)
    except comfy.ComfyError as exc:
        raise HTTPException(
            status_code=400,
            detail="ComfyUI に接続できないため、不足しているモデルを判定できません: "
            + comfy.display_error(exc),
        ) from exc

    urls = load_settings().model_download_urls
    result = ModelDownloadAllResult()
    for item in await _wanted(target):
        if not _missing(info, item):
            continue
        url = (urls.get(item.filename) or "").strip()
        if not url:
            result.missing_urls.append(item.filename)
            continue
        try:
            if target == "runpod":
                result.started.append(
                    await model_download.start_remote(
                        item.filename, url, item.subfolder
                    )
                )
            else:
                result.started.append(
                    model_download.start(item.filename, url, item.subfolder)
                )
        except model_download.DownloadBusy:
            continue  # すでに走っている分は数えない
        except model_download.DownloadError as exc:
            result.errors[item.filename] = str(exc)
    return result
