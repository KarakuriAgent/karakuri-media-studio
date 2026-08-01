"""kie.ai クライアント（外部生成バックエンド、SPEC §5.2）。

ComfyUI と並ぶ **2 つめの生成バックエンド**。自前の GPU ではなく外部 API
アグリゲータ [kie.ai](https://docs.kie.ai/) にタスクを投げ、出来上がった成果物を
ダウンロードして今までと同じ ``outputs/{job_id}/`` に置く。ジョブから見た違いは
「どのマニフェストを選んだか」だけで、履歴・WS 進捗・ライブラリ登録の流儀は
ComfyUI のジョブとまったく同じ。

kie.ai の性質と、それがこのモジュールの形を決めている理由:

- **完全非同期**: ``createTask`` で ``taskId`` を受け取り、``recordInfo`` を
  ポーリングして仕上がりを待つ（webhook はローカル運用では受け取れないので使わ
  ない）。間隔は 10 秒から、``429`` を食らったら指数バックオフで最大 30 秒
- **API が 2 系統ある**: 新しい Market 系（``/api/v1/jobs/*``、``model`` を body で
  指定する統一 API）と、Veo / Suno の旧専用系（モデル別のパス・別のステータス語彙）。
  ポーリングループ自体は同じなので、**エンドポイントとステータスの読み方だけを
  :class:`TaskApi` で差し替えられる**ようにしてある（子 issue #17 / #20 が
  ``VeoTaskApi`` / ``SunoTaskApi`` を足す想定）
- **``resultJson`` は JSON 文字列**なので二重パースが要る（:func:`parse_result_json`）
- **成果物は 14 日で消える**（モデルによっては 24 時間）。完了を検知したら
  その場でダウンロードして自前ストレージに落とす
- 入力画像は公開 URL でしか渡せないので、ローカルのファイルは File Upload API で
  ``fileUrl`` にしてから ``input`` に入れる（:func:`upload_file`）
- 課金はクレジット制で、**失敗したタスクは返金される**。成功したタスクの
  ``creditsConsumed`` だけをジョブ履歴に残す

API キーは**設定の ``kie_api_key``**（設定ページで入力・保存する）が一次で、空の
ときだけ環境変数 ``KIE_API_KEY`` に落ちる。キーが入っていて、かつ残クレジット照会が
通ることを確かめられたときだけ kie 系ワークフローが選択肢に出る（判定と結果の
キャッシュは :mod:`app.backends`）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from .backends import BackendStatus
from .config import load_settings
from .models import GenerationParams, HealthStatus
from .workflows import KIE_SELECT_PREFIX, WorkflowSpec

log = logging.getLogger(__name__)

#: 統一 API のベース URL
API_BASE = "https://api.kie.ai"
#: File Upload API（base64 で送ると公開 URL が返る。保持は 24 時間〜3 日）
FILE_UPLOAD_URL = "https://kieai.redpandaai.co/api/file-base64-upload"
#: アップロード先のディレクトリ（kie 側の論理パス）
UPLOAD_PATH = "images/karakuri-media-studio"

#: API キーの環境変数名（設定が空のときのフォールバック）
API_KEY_ENV = "KIE_API_KEY"

REQUEST_TIMEOUT = 60.0
#: 認証確認（残クレジット照会）のタイムアウト。設定保存のたびに待たされないよう短め。
CHECK_TIMEOUT = 15.0
UPLOAD_TIMEOUT = 300.0
DOWNLOAD_TIMEOUT = 600.0

#: ポーリングの既定間隔（秒）。kie.ai のレート制限は 20 req / 10 秒なので、
#: 1 ジョブ 1 タスクなら 10 秒でも十分に余裕がある。
POLL_INTERVAL = 10.0
#: バックオフの上限（秒）
MAX_POLL_INTERVAL = 30.0
#: 429 を受けるたびに間隔を掛ける倍率
BACKOFF_FACTOR = 2.0
#: 1 タスクの全体タイムアウト（秒）
TASK_TIMEOUT = 60 * 60.0

#: 生成物の種類ごとの既定拡張子（URL から読み取れないとき）
DEFAULT_SUFFIX = {"image": ".png", "video": ".mp4", "audio": ".mp3"}

#: 共通エラーコードの日本語（docs.kie.ai の Common Errors）
ERROR_HINTS: dict[int, str] = {
    401: "API キーが正しくありません",
    402: "クレジットが不足しています",
    404: "タスクまたはエンドポイントが見つかりません",
    422: "リクエストの内容が API に受け付けられませんでした",
    429: "レート制限（20 リクエスト / 10 秒）を超えました",
    455: "kie.ai がメンテナンス中です",
    501: "生成に失敗しました",
}


class KieError(Exception):
    """kie.ai とのやり取りの失敗（そのままジョブの失敗理由として出す）。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class KieNotConfigured(KieError):
    """API キーが無いので 1 リクエストも送れない。"""


class KieRateLimited(KieError):
    """429。ポーリングループはこれを見てバックオフする（失敗にはしない）。"""


# --------------------------------------------------------------------------
# 認証
# --------------------------------------------------------------------------

def api_key() -> str:
    """使う API キー。**設定ページで入れた ``kie_api_key`` が一次**。

    設定が空のときだけ環境変数 ``KIE_API_KEY`` に落ちる（docker の env や
    ``.envrc`` でキーを渡す運用も通るように残してある）。設定を一次にするのは、
    画面から入れ替えたキーが即座に効かないと混乱するため。
    """
    from_settings = (load_settings().kie_api_key or "").strip()
    if from_settings:
        return from_settings
    return (os.environ.get(API_KEY_ENV) or "").strip()


def configured() -> bool:
    """kie 系ワークフローを出してよいか（= API キーがあるか）。"""
    return bool(api_key())


def _require_key() -> str:
    key = api_key()
    if not key:
        raise KieNotConfigured(
            f"kie.ai の API キーが設定されていません"
            f"（環境変数 {API_KEY_ENV} か設定の kie_api_key）"
        )
    return key


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

async def _request(
    method: str,
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    **kwargs: Any,
) -> Any:
    """kie.ai を 1 回叩き、``data`` の中身を返す。

    kie.ai は HTTP 200 で ``{"code": 4xx, "msg": …}`` を返すこともあるので、
    HTTP ステータスと封筒の ``code`` の**両方**を見る。
    """
    headers = {"Authorization": f"Bearer {_require_key()}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise KieError(f"kie.ai に接続できません: {exc}") from exc
    return _envelope(response, method, url)


def _error(method: str, url: str, status: int, body: str) -> KieError:
    hint = ERROR_HINTS.get(status)
    detail = f"HTTP {status}" + (f"（{hint}）" if hint else "")
    message = f"kie.ai {method} {url} が失敗しました: {detail} {body}".rstrip()
    if status == 429:
        return KieRateLimited(message, status_code=status)
    return KieError(message, status_code=status)


def _envelope(response: httpx.Response, method: str, url: str) -> Any:
    status = response.status_code
    if status >= 400:
        raise _error(method, url, status, (response.text or "")[:500])
    try:
        body = response.json()
    except ValueError as exc:
        raise KieError(f"kie.ai が JSON を返しませんでした: {exc}") from exc
    if not isinstance(body, dict):
        raise KieError(f"kie.ai の応答が想定と違います: {str(body)[:200]}")
    try:
        code = int(body.get("code", 200))
    except (TypeError, ValueError):
        code = 200
    if code != 200:
        raise _error(method, url, code, str(body.get("msg") or "")[:500])
    return body.get("data")


# --------------------------------------------------------------------------
# タスク API（系統ごとに差し替えられる部分）
# --------------------------------------------------------------------------

#: ポーリング中の状態。``waiting`` / ``running`` は「まだ待つ」。
TaskPhase = Literal["waiting", "running", "success", "fail"]


@dataclass(frozen=True)
class TaskState:
    """1 回のポーリングで読み取ったタスクの状態。"""

    phase: TaskPhase
    #: kie.ai が返した生のステータス語（``queuing`` / ``generating`` など）。
    #: UI にはこれをそのまま添えるので、系統が違っても表示が壊れない。
    label: str = ""
    result_urls: tuple[str, ...] = ()
    #: 成功したタスクが消費したクレジット（分からなければ None）
    credits: float | None = None
    error: str = ""

    @property
    def done(self) -> bool:
        return self.phase in ("success", "fail")


def parse_result_json(value: Any) -> dict[str, Any]:
    """``resultJson`` を辞書にする。**JSON 文字列なので二重パースが要る**。

    Market 系の ``recordInfo`` は ``resultJson`` を「JSON を文字列にしたもの」で
    返す（さらに二重にエンコードされている応答も観測されている）。読めなければ
    空の辞書を返し、呼び出し側は「まだ結果が無い」として扱う。
    """
    parsed: Any = value
    for _ in range(2):
        if isinstance(parsed, dict):
            return parsed
        if not isinstance(parsed, str) or not parsed.strip():
            return {}
        try:
            parsed = json.loads(parsed)
        except ValueError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _urls(value: Any) -> tuple[str, ...]:
    """``resultUrls``（リスト、または JSON 文字列のリスト）を URL の並びにする。"""
    if isinstance(value, str):
        value = parse_result_json(value).get("resultUrls", value)
    if isinstance(value, str):
        return (value,) if value.startswith("http") else ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(url) for url in value if isinstance(url, str) and url.strip())


#: クレジット消費が入りうるキー（系統ごとに名前が違う）
_CREDIT_KEYS = ("creditsConsumed", "consumeCredits", "credits", "costCredits")


def _credits(data: dict[str, Any]) -> float | None:
    for key in _CREDIT_KEYS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


class TaskApi:
    """createTask / recordInfo のエンドポイントとステータスの読み方。

    既定は **Market 系（統一 API）**。旧専用系（Veo の ``successFlag``、Suno の
    ``PENDING → … → SUCCESS``）はこれを継承してパスと :meth:`read_state` を
    差し替えるだけでよく、:func:`wait_for_task` のループは共通のまま使える。
    """

    name = "market"
    create_url = f"{API_BASE}/api/v1/jobs/createTask"
    record_url = f"{API_BASE}/api/v1/jobs/recordInfo"

    #: 「まだ終わっていない」状態語 -> :data:`TaskPhase`
    pending_states = {"waiting": "waiting", "queuing": "waiting", "generating": "running"}

    def create_body(self, model: str, task_input: dict[str, Any]) -> dict[str, Any]:
        return {"model": model, "input": dict(task_input)}

    def read_task_id(self, data: Any) -> str:
        task_id = data.get("taskId") if isinstance(data, dict) else None
        if not task_id:
            raise KieError(f"kie.ai が taskId を返しませんでした: {str(data)[:200]}")
        return str(task_id)

    def record_params(self, task_id: str) -> dict[str, str]:
        return {"taskId": task_id}

    def read_state(self, data: Any) -> TaskState:
        if not isinstance(data, dict):
            raise KieError(f"kie.ai の recordInfo が想定と違います: {str(data)[:200]}")
        state = str(data.get("state") or "").strip()
        result = parse_result_json(data.get("resultJson"))
        credits = _credits(data)
        if state == "success":
            urls = _urls(result.get("resultUrls") or data.get("resultUrls"))
            if not urls:
                raise KieError("kie.ai がタスクの成果物 URL を返しませんでした")
            return TaskState("success", state, urls, credits)
        if state == "fail":
            reason = str(data.get("failMsg") or data.get("msg") or "").strip()
            code = data.get("failCode")
            hint = ERROR_HINTS.get(int(code)) if str(code).isdigit() else None
            return TaskState(
                "fail",
                state,
                error=" ".join(
                    part for part in (reason, f"（{hint}）" if hint else "") if part
                )
                or "kie.ai がタスクの失敗を報告しました",
            )
        phase = self.pending_states.get(state, "waiting")
        return TaskState(phase, state or "waiting", credits=credits)  # type: ignore[arg-type]


#: 既定の系統（Kling / Seedance などの Market 系モデル）
MARKET = TaskApi()

#: 系統名 -> 実装。子 issue が ``veo`` / ``suno`` をここに登録する。
TASK_APIS: dict[str, TaskApi] = {MARKET.name: MARKET}


def task_api(name: str) -> TaskApi:
    api = TASK_APIS.get(name)
    if api is None:
        raise KieError(f"kie.ai の API 系統 '{name}' はまだ実装されていません")
    return api


# --------------------------------------------------------------------------
# タスクの実行
# --------------------------------------------------------------------------

#: 進捗コールバック（既存の WS 配信へ中継するために渡す）
ProgressCallback = Callable[[TaskState], Awaitable[None]]


async def create_task(
    model: str, task_input: dict[str, Any], *, api: TaskApi = MARKET
) -> str:
    """タスクを 1 つ作り、``taskId`` を返す。"""
    data = await _request(
        "POST", api.create_url, json=api.create_body(model, task_input)
    )
    task_id = api.read_task_id(data)
    log.info("kie: created task %s (%s, %s)", task_id, model, api.name)
    return task_id


async def get_task(task_id: str, *, api: TaskApi = MARKET) -> TaskState:
    """``recordInfo`` を 1 回だけ読む。"""
    data = await _request(
        "GET", api.record_url, params=api.record_params(task_id)
    )
    return api.read_state(data)


async def wait_for_task(
    task_id: str,
    *,
    api: TaskApi = MARKET,
    on_progress: ProgressCallback | None = None,
    timeout: float | None = None,
) -> TaskState:
    """タスクが終わるまでポーリングする（成功した :class:`TaskState` を返す）。

    - 間隔は :data:`POLL_INTERVAL`。作成直後は必ず ``waiting`` なので**先に待つ**
    - ``429`` は失敗ではないので、間隔を :data:`BACKOFF_FACTOR` 倍
      （:data:`MAX_POLL_INTERVAL` 上限）にして数え直す
    - 状態語が変わったときだけ ``on_progress`` を呼ぶ（WS を無駄に叩かない）
    """
    loop = asyncio.get_running_loop()
    limit = timeout if timeout is not None else TASK_TIMEOUT
    deadline = loop.time() + limit
    interval = POLL_INTERVAL
    last_label = ""
    while True:
        await asyncio.sleep(interval)
        try:
            state = await get_task(task_id, api=api)
        except KieRateLimited:
            interval = min(MAX_POLL_INTERVAL, interval * BACKOFF_FACTOR)
            log.info(
                "kie: rate limited while polling %s; next poll in %.0fs",
                task_id,
                interval,
            )
            if loop.time() > deadline:
                raise
            continue
        interval = POLL_INTERVAL

        if state.label and state.label != last_label and not state.done:
            last_label = state.label
            if on_progress is not None:
                await on_progress(state)
        if state.phase == "success":
            return state
        if state.phase == "fail":
            raise KieError(state.error or "kie.ai のタスクが失敗しました")
        if loop.time() > deadline:
            raise KieError(
                f"kie.ai のタスク {task_id} が {limit:.0f} 秒以内に終わりませんでした"
            )


# --------------------------------------------------------------------------
# ファイル（入力のアップロードと成果物のダウンロード）
# --------------------------------------------------------------------------

async def upload_file(path: str | Path) -> str:
    """ローカルのファイルを kie に置いて、公開 URL（``fileUrl``）を返す。

    i2v 系のモデルは入力画像を**公開 URL でしか**受け取らない（base64 直指定は
    不可）ので、``assets/`` や ``library/`` のファイルはここを通してから
    ``input`` に入れる。置いたファイルは 24 時間〜3 日で消えるが、ジョブ 1 本の
    あいだ生きていれば足りる。
    """
    src = Path(path)
    if not src.is_file():
        raise KieError(f"ファイルが見つかりません: {src}")
    mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(src.read_bytes()).decode("ascii")
    data = await _request(
        "POST",
        FILE_UPLOAD_URL,
        json={
            "base64Data": f"data:{mime};base64,{encoded}",
            "uploadPath": UPLOAD_PATH,
            "fileName": src.name,
        },
        timeout=UPLOAD_TIMEOUT,
    )
    url = ""
    if isinstance(data, dict):
        url = str(data.get("fileUrl") or data.get("downloadUrl") or data.get("url") or "")
    if not url:
        raise KieError(f"kie.ai がアップロード先の URL を返しませんでした: {str(data)[:200]}")
    return url


def _suffix(url: str, kind: str) -> str:
    """URL の末尾から拡張子を拾う（取れなければ種類ごとの既定）。"""
    name = url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if suffix and len(suffix) <= 5 and suffix[1:].isalnum():
        return suffix
    return DEFAULT_SUFFIX.get(kind, "")


async def download(url: str, dest: str | Path) -> Path:
    """成果物を 1 つダウンロードする（署名 URL なので認証ヘッダは付けない）。"""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with path.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        fh.write(chunk)
    except httpx.HTTPStatusError as exc:
        raise KieError(
            f"kie.ai の成果物をダウンロードできませんでした:"
            f" HTTP {exc.response.status_code} {url}"
        ) from exc
    except httpx.HTTPError as exc:
        raise KieError(f"kie.ai の成果物をダウンロードできませんでした: {exc}") from exc
    except OSError as exc:
        raise KieError(f"{path} に書き込めませんでした: {exc}") from exc
    return path


async def download_results(
    state: TaskState, dest_dir: str | Path, stem: str, kind: str
) -> list[Path]:
    """成功したタスクの ``resultUrls` を全部落とす（先頭が主成果物）。

    kie.ai の成果物は 14 日（モデルによっては 24 時間）で消えるので、完了を
    検知したそばから自前の ``outputs/{job_id}/`` に落とす。
    """
    if not state.result_urls:
        raise KieError("kie.ai のタスクに成果物がありません")
    directory = Path(dest_dir)
    saved: list[Path] = []
    for index, url in enumerate(state.result_urls):
        name = stem if index == 0 else f"{stem}_{index + 1}"
        saved.append(await download(url, directory / f"{name}{_suffix(url, kind)}"))
    return saved


# --------------------------------------------------------------------------
# クレジット
# --------------------------------------------------------------------------

async def get_credits(*, timeout: float = REQUEST_TIMEOUT) -> float:
    """残クレジット（1 credit = $0.005）。"""
    data = await _request("GET", f"{API_BASE}/api/v1/chat/credit", timeout=timeout)
    if isinstance(data, dict):
        data = data.get("credits", data.get("credit"))
    try:
        return float(data)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise KieError(
            f"kie.ai が残クレジットを返しませんでした: {str(data)[:200]}"
        ) from exc


async def check_backend() -> BackendStatus:
    """キーが入っていて、実際に通るか（SPEC §5.2）。

    確認は**残クレジット照会**（``GET /api/v1/chat/credit``）。一番軽く、かつ
    「キーが有効で・アカウントが生きている」ことが 1 回で分かる。結果は
    :mod:`app.backends` がキャッシュし、起動時と設定保存時に取り直される。
    """
    if not configured():
        return BackendStatus(
            "kie",
            "not_configured",
            f"API キーが未設定です（設定ページの kie.ai、または環境変数 {API_KEY_ENV}）",
        )
    try:
        credits = await get_credits(timeout=CHECK_TIMEOUT)
    except KieError as exc:
        return BackendStatus("kie", "error", str(exc))
    return BackendStatus("kie", "ok", f"残クレジット {credits:g}")


async def check_kie() -> HealthStatus:
    """``GET /api/health`` の kie 欄（キー未設定は異常ではない）。"""
    state = await check_backend()
    return HealthStatus(status=state.state, detail=state.detail)


# --------------------------------------------------------------------------
# マニフェスト -> タスク入力
# --------------------------------------------------------------------------

def _prompt(spec: WorkflowSpec, params: GenerationParams) -> str:
    """そのワークフローにとっての「プロンプト」（種類で持ち場が違う）。"""
    if spec.kind == "image":
        return params.image_prompt
    if spec.kind == "audio":
        return params.audio_prompt
    return params.video_prompt


def _seed(spec: WorkflowSpec, params: GenerationParams) -> int:
    if spec.kind == "image":
        return params.image_seed
    if spec.kind == "audio":
        return params.audio_seed
    return params.video_seeds[0] if params.video_seeds else 0


def task_values(
    spec: WorkflowSpec, params: GenerationParams, uploads: dict[str, str]
) -> dict[str, Any]:
    """論理名 -> 値。``uploads`` は論理入力名（``image`` など）-> 公開 URL。

    ComfyUI 側の :mod:`app.workflow` にあたる「注入する値を決める」層。実際の
    キー名は :class:`app.workflows.KieTask` が決めるので、ここはモデルに依らない。
    """
    values: dict[str, Any] = {
        "prompt": _prompt(spec, params),
        "negative_prompt": params.negative_prompt,
        "aspect_ratio": params.aspect_ratio,
        "duration": params.duration,
        "fps": params.fps,
        "seed": _seed(spec, params),
        "lyrics": params.lyrics,
        "bpm": params.bpm,
        "language": params.language,
    }
    for name, select in spec.selects.items():
        chosen = params.selects.get(name) or select.fallback
        values[f"{KIE_SELECT_PREFIX}{name}"] = chosen
    values.update(uploads)
    return values


def task_input(
    spec: WorkflowSpec, params: GenerationParams, uploads: dict[str, str]
) -> dict[str, Any]:
    """``createTask`` の ``input``（マニフェストが宣言したキーだけ）。

    値が空のものは**送らない**: 外部 API は空文字や 0 を「そう指定された」と解釈
    することがあるので、指定していないものはキーごと落とすほうが安全。
    """
    task = spec.kie
    if task is None:
        raise KieError(f"workflow '{spec.id}' に kie のタスク宣言がありません")
    values = task_values(spec, params, uploads)
    payload: dict[str, Any] = dict(task.constants)
    for name, key in task.fields.items():
        value = values.get(name)
        if value is None or value == "":
            continue
        payload[key] = value
    return payload


@dataclass(frozen=True)
class TaskRequest:
    """kie.ai に投げた内容そのもの（履歴に残して再現できるようにする）。"""

    model: str
    api: str
    input: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "api": self.api, "input": dict(self.input)}


def build_request(
    spec: WorkflowSpec, params: GenerationParams, uploads: dict[str, str]
) -> TaskRequest:
    """マニフェスト + ジョブのパラメータ -> 投入するリクエスト。"""
    task = spec.kie
    if task is None:
        raise KieError(f"workflow '{spec.id}' に kie のタスク宣言がありません")
    return TaskRequest(task.model, task.api, task_input(spec, params, uploads))
