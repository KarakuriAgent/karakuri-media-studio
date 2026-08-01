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
  :class:`TaskApi` で差し替えられる**ようにしてある（Veo は
  :class:`VeoTaskApi`、Suno は :class:`SunoTaskApi`）
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

#: Suno の ``callBackUrl`` に入れるダミー（:class:`SunoTaskApi`）。スキーマ上は
#: 必須だが、ローカル運用では webhook を受けられないので届かない URL を入れて
#: ポーリングで結果を拾う。kie.ai はコールバックの配送失敗でタスクを失敗には
#: しないが、もしこの運用が通らなくなったら SPEC §5.2 の注記を見直すこと。
CALLBACK_URL = "https://localhost/unused-callback"

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


class VeoTaskApi(TaskApi):
    """Google Veo の旧専用系（``/api/v1/veo/*``、SPEC §5.2 / issue #17）。

    Market 系との違いは 3 つだけで、あとは :func:`wait_for_task` の共通ループに
    そのまま乗る:

    - **ボディが平ら**: ``{"model": …, "input": {…}}`` ではなく、パラメータを
      そのまま並べる（``prompt`` / ``imageUrls`` / ``aspect_ratio`` …）
    - **``generationType`` は渡した画像の枚数で決まる**: 2 枚なら
      「最初と最後のフレーム」、0〜1 枚なら通常生成（1 枚は開始フレーム扱い）。
      マニフェスト側で固定したいときは ``constants`` に書けばそれが勝つ
    - **状態が ``successFlag``**: 0 = 生成中 / 1 = 成功 / 2, 3 = 失敗。成果物は
      ``response.resultUrls`` に入る
    """

    name = "veo"
    create_url = f"{API_BASE}/api/v1/veo/generate"
    record_url = f"{API_BASE}/api/v1/veo/record-info"

    #: 画像 2 枚（最初 + 最後のフレーム）のときの生成種別
    FLF_TYPE = "FIRST_AND_LAST_FRAMES_2_VIDEO"
    #: それ以外（画像なし = t2v、1 枚 = 開始フレーム）の生成種別
    DEFAULT_TYPE = "TEXT_2_VIDEO"
    #: 数値で送るキー（選択式フィールドの値は文字列で届く）
    NUMERIC_KEYS = ("duration", "seeds")
    #: ``successFlag`` -> :data:`TaskPhase`
    FLAGS: dict[int, TaskPhase] = {0: "running", 1: "success", 2: "fail", 3: "fail"}

    def create_body(self, model: str, task_input: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, **task_input}
        for key in self.NUMERIC_KEYS:
            value = body.get(key)
            if isinstance(value, str) and value.strip().isdigit():
                body[key] = int(value)
        urls = body.get("imageUrls")
        count = len(urls) if isinstance(urls, (list, tuple)) else 0
        body.setdefault(
            "generationType", self.FLF_TYPE if count >= 2 else self.DEFAULT_TYPE
        )
        return body

    def read_state(self, data: Any) -> TaskState:
        if not isinstance(data, dict):
            raise KieError(f"kie.ai の record-info が想定と違います: {str(data)[:200]}")
        try:
            flag = int(data.get("successFlag"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            flag = -1
        phase = self.FLAGS.get(flag, "waiting")
        credits = _credits(data)
        if phase == "success":
            response = parse_result_json(data.get("response"))
            urls = _urls(response.get("resultUrls") or data.get("resultUrls"))
            if not urls:
                raise KieError("kie.ai がタスクの成果物 URL を返しませんでした")
            return TaskState("success", "success", urls, credits)
        if phase == "fail":
            reason = str(data.get("errorMessage") or data.get("failMsg") or "").strip()
            code = data.get("errorCode")
            hint = ERROR_HINTS.get(int(code)) if str(code).isdigit() else None
            return TaskState(
                "fail",
                "fail",
                error=" ".join(
                    part for part in (reason, f"（{hint}）" if hint else "") if part
                )
                or "kie.ai がタスクの失敗を報告しました",
            )
        # 生成中は 0 しか返ってこない（キュー待ちと生成中の区別は無い）
        return TaskState(phase, "generating" if flag == 0 else "waiting", credits=credits)


class SunoTaskApi(TaskApi):
    """Suno の旧専用系（``/api/v1/generate*``、SPEC §5.2 / issue #20）。

    音声の外部ワークフローで唯一の系統。Market 系との違いは 4 つ:

    - **ボディが平ら**（Veo と同じ）: ``model`` / ``customMode`` /
      ``instrumental`` / ``prompt`` / ``style`` / ``title`` … をそのまま並べる。
      ``model`` はモデル名ではなく**バージョン**（``V5`` / ``V5_5`` /
      ``V4_5PLUS``）なので、マニフェストの選択式フィールドで上書きできる
    - **``callBackUrl`` がスキーマ上必須**。ローカル運用では webhook を受けられ
      ないので :data:`CALLBACK_URL` のダミーを必ず入れ、結果はポーリングで拾う
      （kie.ai 側はコールバックの失敗をタスクの失敗にはしない）
    - **``instrumental`` と ``title`` は他の入力から決まる**: 歌詞
      （``prompt``）が空ならインスト、``title`` は customMode で必須なので
      歌詞かスタイルの頭から作る（フォームに項目を増やさない、:meth:`_title`）
    - **状態語が独自**: ``PENDING → TEXT_SUCCESS → FIRST_SUCCESS → SUCCESS``。
      成果物は ``response.sunoData[]`` に**2 曲**入る（1 リクエストで 2
      バリエーションが標準なので、両方とも回収して保存する）
    """

    name = "suno"
    create_url = f"{API_BASE}/api/v1/generate"
    record_url = f"{API_BASE}/api/v1/generate/record-info"

    #: 状態語 -> :data:`TaskPhase`。中間状態（歌詞ができた / 1 曲目ができた）は
    #: 「まだ待つ」だが、進捗として UI に出したいので running で区別する。
    STATES: dict[str, TaskPhase] = {
        "PENDING": "waiting",
        "TEXT_SUCCESS": "running",
        "FIRST_SUCCESS": "running",
        "SUCCESS": "success",
        "CREATE_TASK_FAILED": "fail",
        "GENERATE_AUDIO_FAILED": "fail",
        "CALLBACK_EXCEPTION": "fail",
        "SENSITIVE_WORD_ERROR": "fail",
    }
    #: 上の表に無い状態語でも、これで終わるものは失敗として扱う（kie.ai は
    #: モデルの追加とともに ``*_FAILED`` / ``*_ERROR`` を増やしてくる）
    FAIL_SUFFIXES = ("_FAILED", "_ERROR")
    #: ``sunoData[]`` の 1 曲から音声 URL を拾うキー（前から順に見る）
    AUDIO_KEYS = ("audioUrl", "audio_url", "sourceAudioUrl", "source_audio_url")
    #: ``vocalGender`` に送ってよい値（それ以外＝「おまかせ」はキーごと落とす）
    VOCAL_GENDERS = ("m", "f")
    #: ``title`` の上限（customMode で必須。超えると 422）
    MAX_TITLE = 80

    def create_body(self, model: str, task_input: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, **task_input}
        # 歌詞が無ければインスト（``prompt`` は歌詞本文なので空なら送らない）
        body.setdefault("instrumental", not str(body.get("prompt") or "").strip())
        # customMode=true では title が必須。専用の入力欄は作らず、歌詞 →
        # スタイルの順に頭を借りる。
        if not str(body.get("title") or "").strip():
            body["title"] = self._title(body)
        if body.get("vocalGender") not in self.VOCAL_GENDERS:
            body.pop("vocalGender", None)
        body.setdefault("callBackUrl", CALLBACK_URL)
        return body

    def _title(self, body: dict[str, Any]) -> str:
        """曲名（歌詞の最初の 1 行 → スタイルの最初の要素 → ``Untitled``）。

        歌詞側は ``[Verse 1]`` のような構造タグの行を飛ばして最初の歌い出しを
        使う。どちらも空（インストでスタイルも空）のときだけ既定値になる。
        """
        for line in str(body.get("prompt") or "").splitlines():
            text = line.strip()
            if text and not (text.startswith("[") and text.endswith("]")):
                return text[: self.MAX_TITLE]
        style = str(body.get("style") or "")
        head = style.replace("\n", ",").split(",")[0].strip()
        return head[: self.MAX_TITLE] or "Untitled"

    def _phase(self, status: str) -> TaskPhase:
        phase = self.STATES.get(status)
        if phase is not None:
            return phase
        return "fail" if status.endswith(self.FAIL_SUFFIXES) else "waiting"

    def _audio_urls(self, response: dict[str, Any]) -> tuple[str, ...]:
        """``response.sunoData[]`` の音声 URL（**返ってきた全曲**）。"""
        tracks = response.get("sunoData") or response.get("suno_data")
        if not isinstance(tracks, (list, tuple)):
            return ()
        urls: list[str] = []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            for key in self.AUDIO_KEYS:
                url = track.get(key)
                if isinstance(url, str) and url.startswith("http"):
                    urls.append(url)
                    break
        return tuple(urls)

    def read_state(self, data: Any) -> TaskState:
        if not isinstance(data, dict):
            raise KieError(f"kie.ai の record-info が想定と違います: {str(data)[:200]}")
        status = str(data.get("status") or "").strip().upper()
        phase = self._phase(status)
        credits = _credits(data)
        if phase == "success":
            urls = self._audio_urls(parse_result_json(data.get("response")))
            if not urls:
                raise KieError("kie.ai がタスクの成果物 URL を返しませんでした")
            return TaskState("success", status, urls, credits)
        if phase == "fail":
            reason = str(data.get("errorMessage") or data.get("msg") or "").strip()
            code = data.get("errorCode")
            hint = ERROR_HINTS.get(int(code)) if str(code).isdigit() else None
            return TaskState(
                "fail",
                status,
                error=" ".join(
                    part
                    for part in (reason or status, f"（{hint}）" if hint else "")
                    if part
                )
                or "kie.ai がタスクの失敗を報告しました",
            )
        return TaskState(phase, status or "PENDING", credits=credits)


#: 既定の系統（Kling / Seedance などの Market 系モデル）
MARKET = TaskApi()
#: Veo 3.1（旧専用系）
VEO = VeoTaskApi()
#: Suno V5 系（旧専用系）
SUNO = SunoTaskApi()

#: 系統名 -> 実装
TASK_APIS: dict[str, TaskApi] = {
    MARKET.name: MARKET,
    VEO.name: VEO,
    SUNO.name: SUNO,
}


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
        "negative_tags": params.negative_tags,
    }
    for name, select in spec.selects.items():
        chosen = params.selects.get(name) or select.fallback
        values[f"{KIE_SELECT_PREFIX}{name}"] = chosen
    values.update(uploads)
    return values


#: 「真」と読む文字列（選択式フィールドの値は文字列で届く）
_TRUTHY = frozenset({"true", "1", "yes", "on"})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


def _as_int(value: Any) -> Any:
    """整数で送るキーの値（数として読めなければそのまま = API に判断させる）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return value


def _as_float(value: Any) -> float | None:
    """小数で送るキーの値（数として読めなければ ``None`` = キーごと落とす）。

    選択式フィールドの「指定しない」（``"auto"``）をここで吸収する: 0 を送ると
    「0 を指定した」になってしまうつまみ（Suno の重みづけ）があるので、
    未指定はキーそのものを送らないのが正しい。
    """
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def task_input(
    spec: WorkflowSpec, params: GenerationParams, uploads: dict[str, str]
) -> dict[str, Any]:
    """``createTask`` の ``input``（マニフェストが宣言したキーだけ）。

    値が空のものは**送らない**: 外部 API は空文字や 0 を「そう指定された」と解釈
    することがあるので、指定していないものはキーごと落とすほうが安全。

    :attr:`app.workflows.KieTask.list_keys` に挙げたキーは**配列**になり、同じ
    キーに複数の論理名を宣言できる（Veo の ``imageUrls`` は 1 枚目が開始フレーム、
    2 枚目が最終フレーム）。並びは宣言順で、空の値はそこでも落ちる。

    :attr:`app.workflows.KieTask.bool_keys` に挙げたキーは **``bool``** に、
    :attr:`app.workflows.KieTask.int_keys` に挙げたキーは **``int``** に、
    :attr:`app.workflows.KieTask.float_keys` に挙げたキーは **``float``** になる
    （選択式フィールドの値は文字列で届くので、Kling の ``sound`` や Seedance の
    ``duration`` のように JSON の型が決まっているものはここで直す）。
    ``float_keys`` だけは**数として読めない値をキーごと落とす**ので、Suno の
    ``styleWeight`` などの「auto = 指定しない」がそのまま表現できる。
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
        if key in task.list_keys:
            payload.setdefault(key, []).append(value)
        elif key in task.bool_keys:
            payload[key] = _as_bool(value)
        elif key in task.int_keys:
            payload[key] = _as_int(value)
        elif key in task.float_keys:
            number = _as_float(value)
            # "auto"（＝指定しない）はキーごと落とす
            if number is not None:
                payload[key] = number
        else:
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
