import json
from typing import get_args

from .models import ComfyTarget, Settings
from .paths import CONFIG_PATH, GROK_WORKDIR

#: 接続先の全種類（移行で 3 環境へ複製するときに使う）
TARGETS: tuple[str, ...] = get_args(ComfyTarget)

_settings: Settings | None = None


def _defaults() -> Settings:
    return Settings(grok_workdir=str(GROK_WORKDIR))


def _migrated(data: dict) -> dict:
    """旧レイアウトの接続情報を接続先プロファイルへ移す（SPEC §5）。

    以前は接続先が ``comfy_url`` / ``comfy_api_key`` の 1 組しか無かった。旧い
    設定ファイルを読むと（= ``comfy_target`` がまだ書かれていない）、その 1 組を
    今の 3 プロファイルのどれかに振り分ける:

    - ``runpod_enabled`` が true なら RunPod の Pod を指していたので
      ``runpod_comfy_url`` へ（接続先も ``runpod``）
    - そうでなく ComfyCloud のホスト、または API キーが入っているなら
      接続先を ``comfy_cloud`` にする（URL は固定なので捨てる）
    - それ以外はローカルとして ``local_comfy_url`` へ

    ``comfy_api_key`` は URL を引き取ったプロファイルのキー欄へ移す（Pod の
    ComfyUI を認証付きで公開している構成があるため、ComfyCloud 決め打ちにはでき
    ない）。ローカルはキーを持たないので、その場合だけ ComfyCloud 側に残す。
    旧キー自体（``comfy_url`` / ``comfy_api_key`` / 一時期あった
    ``comfy_cloud_url``）は呼び出し側のフィルタで捨てられる（モデルに無い名前
    なので残っていてもエラーにならない）。
    """
    if "comfy_target" in data:
        return data
    url = str(data.get("comfy_url") or "").strip()
    api_key = str(data.get("comfy_api_key") or "").strip()
    if not url and not api_key:
        return data

    moved = dict(data)
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    if url and data.get("runpod_enabled"):
        target, url_field, key_field = "runpod", "runpod_comfy_url", "runpod_comfy_api_key"
    elif url and not (host.endswith("comfy.org") or api_key):
        target, url_field, key_field = "local", "local_comfy_url", "comfy_cloud_api_key"
    else:
        # ComfyCloud のエンドポイントは固定なので、URL は移さず接続先だけ立てる。
        target, url_field, key_field = "comfy_cloud", None, "comfy_cloud_api_key"
    if url:
        moved.setdefault("comfy_target", target)
        if url_field:
            moved.setdefault(url_field, url)
    if api_key:
        moved.setdefault(key_field, api_key)
    return moved


#: 接続先ごとに持つようになった設定（旧レイアウトは全環境へ複製する）
_PER_TARGET_FIELDS = ("model_overrides", "model_choices")


def _per_target(data: dict) -> dict:
    """モデル指定・候補リストを接続先ごとの形に揃える（SPEC §3.3 / §5）。

    以前はどの ComfyUI に繋いでいても 1 組しか持っていなかったので、旧レイアウト
    （値が接続先キーではなくスロットキーの辞書）を見つけたら **3 環境すべてに同じ
    ものを複製する**: 環境を分けた瞬間に手元の指定が消えると、それまで動いていた
    ジョブが既定モデルで走ってしまうため。
    """
    moved = dict(data)
    for field in _PER_TARGET_FIELDS:
        stored = data.get(field)
        if not isinstance(stored, dict) or not stored:
            continue
        # 接続先キーだけで出来ていれば新レイアウト（空の設定もここに入る）
        if set(stored) <= set(TARGETS):
            continue
        moved[field] = {target: stored for target in TARGETS}
    return moved


def load_settings() -> Settings:
    """Read settings from runtime/config.json (cached)."""
    global _settings
    if _settings is None:
        if CONFIG_PATH.exists():
            data = _per_target(
                _migrated(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            )
            # 廃止したキー（旧 comfy_url / comfy_models_dir など）が残っていても
            # 捨てる: model_copy はモデルに無い名前もそのまま持ってしまうため。
            _settings = _defaults().model_copy(update={
                k: v for k, v in data.items() if k in Settings.model_fields
            })
        else:
            _settings = _defaults()
    return _settings


def save_settings(settings: Settings) -> Settings:
    global _settings
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _settings = settings
    return _settings


def update_settings(patch: dict) -> Settings:
    """Partial update. Unknown keys are ignored by the model."""
    merged = load_settings().model_copy(update={
        k: v for k, v in patch.items() if k in Settings.model_fields
    })
    return save_settings(merged)
