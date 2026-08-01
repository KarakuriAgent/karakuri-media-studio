"""生成バックエンドの可用性（SPEC §5.2）。

ワークフローは ``comfyui`` / ``kie``（将来 ``grok_cli`` / ``codex_cli``）のどれかで
実行される。外部バックエンドは**認証情報が入っていて、かつそれが実際に通ること**を
確かめられて初めて「使える」ので、選択肢（``GET /api/options`` のワークフロー一覧、
エージェントのカタログ）に出すかどうかをここで一元的に決める。

- 判定は**バックエンドごとの軽いヘルスチェック**（kie.ai なら残クレジット照会）。
  結果はプロセス内にキャッシュし、**起動時**と**設定の保存時**に取り直す
- キャッシュが無いあいだ外部バックエンドは「使えない」扱い。確認できていない
  ものを選択肢に出して、投入してから 401 で落ちるより出さないほうがよい
- ``comfyui`` は例外で常に使える: 接続先が落ちていることは普通にあり、その報告は
  ``GET /api/health`` の仕事なので、選択肢そのものは消さない

新しいバックエンドを足すときは :func:`_checks` に「確認する関数」を 1 つ足すだけで、
選択肢の出し分け・設定保存時の再確認・UI への表示はこの層がまとめて面倒を見る。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, get_args

from .workflows import WorkflowBackend

log = logging.getLogger(__name__)

#: すべてのバックエンド名
KNOWN_BACKENDS: tuple[str, ...] = get_args(WorkflowBackend)

#: 認証情報が無い / 通らない / 通る
BackendState = Literal["ok", "not_configured", "error"]


@dataclass(frozen=True)
class BackendStatus:
    """1 つのバックエンドの状態（UI にはこのまま出す）。"""

    backend: str
    state: BackendState
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.state == "ok"


#: 「確認する関数」の型
BackendCheck = Callable[[], Awaitable[BackendStatus]]

#: 確認結果のキャッシュ（バックエンド名 -> 状態）
_status: dict[str, BackendStatus] = {}
#: 同じバックエンドを同時に 2 回確認しない
_lock = asyncio.Lock()


def _checks() -> dict[str, BackendCheck]:
    """バックエンド名 -> 確認する関数。

    :mod:`app.kie` は :mod:`app.workflows` を import するので、循環を避けて
    呼ばれた時点で読み込む（モジュールの import 自体はキャッシュされる）。
    """
    from . import kie

    return {"kie": kie.check_backend}


def _unimplemented(name: str) -> BackendStatus:
    return BackendStatus(name, "not_configured", "このバックエンドは未実装です")


def status(name: str) -> BackendStatus:
    """キャッシュされている状態（未確認なら「使えない」側に倒す）。"""
    if name == "comfyui":
        return BackendStatus(name, "ok", "ComfyUI の疎通は /api/health を参照")
    cached = _status.get(name)
    if cached is not None:
        return cached
    if name not in _checks():
        return _unimplemented(name)
    return BackendStatus(name, "not_configured", "まだ確認していません")


def available(name: str) -> bool:
    """このバックエンドのワークフローを選択肢に出してよいか。"""
    return status(name).available


async def refresh(name: str) -> BackendStatus:
    """確認をやり直してキャッシュを更新する。"""
    if name == "comfyui":
        return status(name)
    check = _checks().get(name)
    if check is None:
        return _unimplemented(name)
    async with _lock:
        try:
            result = await check()
        except Exception as exc:  # noqa: BLE001 - 確認の失敗で起動を止めない
            log.warning("backend %s の確認に失敗しました: %s", name, exc)
            result = BackendStatus(name, "error", str(exc))
        _status[name] = result
    log.info("backend %s: %s (%s)", name, result.state, result.detail)
    return result


async def ensure(name: str) -> BackendStatus:
    """未確認なら 1 度だけ確認する（確認済みならそのまま返す）。"""
    if name == "comfyui" or name in _status:
        return status(name)
    return await refresh(name)


async def refresh_all() -> list[BackendStatus]:
    """全バックエンドを確認し直す（起動時・設定保存時）。"""
    return [await refresh(name) for name in _checks()]


async def ensure_all() -> list[BackendStatus]:
    """全バックエンドの状態（未確認のものだけ確認する）。"""
    return [await ensure(name) for name in KNOWN_BACKENDS]


def invalidate(name: str | None = None) -> None:
    """キャッシュを捨てる（設定が変わったとき）。次の照会で確認し直す。"""
    if name is None:
        _status.clear()
    else:
        _status.pop(name, None)
