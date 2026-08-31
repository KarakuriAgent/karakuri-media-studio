"""画面の状態の内部 API（``/api/ui/…``）。

生成フォームの下書きをブラウザと共有するための受け口で、外部 API
（``/api/v1/ui/…``、:mod:`app.routers.external`）と同じ :mod:`app.ui_state` を
触る。UI 用なので認証は無い（ほかの内部 API と同じ）。

保存に成功したら WS（``type: "form"``）で全ブラウザへ流す。書いた本人にも同じ
フレームが届くが、送り主は自分が受け取った ``revision`` を覚えていて読み飛ばす。
"""

from fastapi import APIRouter, HTTPException

from .. import ui_state, ws
from ..models import UiFormState, UiFormUpdate

router = APIRouter(prefix="/api/ui", tags=["ui"])


def _conflict(exc: ui_state.UiStateConflict) -> HTTPException:
    """409 の body には現在値を入れる（送り主が取り直さずに済むように）。"""
    return HTTPException(
        status_code=409,
        detail={"message": str(exc), "current": exc.current.model_dump()},
    )


@router.get("/generate-form", response_model=UiFormState)
async def get_generate_form() -> UiFormState:
    """いまの下書き（まだ一度も保存されていなければ空 + ``revision`` 0）。"""
    return await ui_state.get()


@router.put("/generate-form", response_model=UiFormState)
async def put_generate_form(payload: UiFormUpdate) -> UiFormState:
    """フォーム全体を保存する（``base_revision`` を省略すると強制上書き）。"""
    try:
        state = await ui_state.put(
            payload.values,
            updated_by="ui",
            base_revision=payload.base_revision,
        )
    except ui_state.UiStateConflict as exc:
        raise _conflict(exc) from exc
    except ui_state.UiStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ws.publish_form(state.revision, state.updated_by, state.values)
    return state
