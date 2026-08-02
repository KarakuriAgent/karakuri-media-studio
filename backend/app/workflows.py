"""Workflow template registry and per-template injection manifests (SPEC §3).

The app ships a folder of independent ComfyUI API-format graphs under
``workflow/``: four image workflows (Krea 2 turbo, Anima, Z-Image turbo and
Qwen-Image Edit 2511), seven LTX 2.3 video workflows and two audio workflows
(ACE-Step 1.5 XL and Stable Audio 3 Medium).  Each one is described here by a
:class:`WorkflowSpec` whose ``inject`` map names every node/field the app writes
to.

Audio is a **stand-alone** kind: an audio job runs exactly one graph and is
never chained with the image / video stages (SPEC §2 knows nothing about it).

Why node ids and not ``class_type`` + title?  Because the templates contain
several nodes that are indistinguishable that way — both CLIPTextEncode nodes
share the title 「CLIPテキストエンコード（プロンプト）」, there are two
``RandomNoise`` nodes per video graph and half a dozen ``ComfyMathExpression``
nodes titled 「数式」.  The manifests therefore pin node ids, and
:func:`validate_specs` (run by the health check and by the tests) asserts that
every pinned node still exists with the expected ``class_type`` so that editing
a template can never silently break the injector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from .paths import WORKFLOW_DIR

Workflow = dict[str, dict[str, Any]]

WorkflowKind = Literal["image", "video", "audio"]

#: どのエンジンがこのワークフローを実行するか（SPEC §5 / §5.2）。``comfyui`` は
#: ``workflow/*.json`` のテンプレートを自前の ComfyUI に投げる従来の経路、``kie``
#: は外部 API アグリゲータ kie.ai にタスクを投げる経路、``grok_cli`` は Grok Build
#: CLI をサブスク枠でヘッドレス実行する経路（:mod:`app.grok_media`）、``codex_cli``
#: は Codex CLI を ChatGPT サブスク枠でヘッドレス実行する経路
#: （:mod:`app.codex_media`、SPEC §5.4）。
WorkflowBackend = Literal["comfyui", "kie", "grok_cli", "codex_cli"]

#: Logical names of the assets a video workflow can require.  ``image`` is the
#: primary image input (start frame, first frame or reference sheet depending on
#: the workflow), ``audio`` a reference audio track, ``end_image`` the closing
#: frame of flf2v and ``video`` a reference clip.
InputName = Literal["image", "audio", "end_image", "video"]


class WorkflowSpecError(Exception):
    """A manifest does not match the template it describes."""


@dataclass(frozen=True)
class Target:
    """One injection point: ``node_id.field`` of a node of ``class_type``.

    ``field`` is empty for manifest entries that name a *node* rather than one
    of its inputs (the frame-count expression, the trigger concatenation node,
    the head of the LoRA chain).
    """

    node_id: str
    field: str
    class_type: str

    @property
    def key(self) -> str:
        return f"{self.node_id}.{self.field}" if self.field else self.node_id


def T(node_id: str, field: str, class_type: str) -> Target:
    return Target(node_id, field, class_type)


#: 選択式フィールドの「自動決定」の種類。``audio_duration`` は入力音声の実長から
#: 決める（wan_dancer の尺）。空文字は自動なし（既定値をそのまま使う）。
AutoSource = Literal["", "audio_duration"]


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class SelectSpec:
    """One *selectable* injection point: the app offers a fixed list of strings.

    テンプレートが自由記述ではなくコンボボックスで挙動を決めるワークフロー
    （wan_dancer の踊りの種類など）のための汎用の仕組み。宣言すると

    * 生成フォームがこのリストからの ``select`` を自動で描画し、
    * ジョブは ``selects`` にその値を持ち（リスト外は 422）、
    * エージェントのカタログにも選択肢が載る。

    ComfyUI の ``CustomCombo`` は選んだ文字列（``choice``）と 0 始まりの番号
    （``index``）の両方を持ち、**グラフが読むのは番号側**（``choice`` は表示用）。
    そのため :attr:`index_field` にも同じ選択の番号を書き込む。

    ``numeric_target`` は「選んだ値を数値としても別のノードに入れる」場合に使う
    （wan_dancer の尺はコンボと ``TrimAudioDuration`` の両方に入れないと、音声だけ
    25 秒で切られる）。
    """

    #: UI の見出し（日本語）
    label: str
    #: 選べる値。**テンプレートの option と同じ文字列**であること
    choices: tuple[str, ...]
    #: 選んだ文字列を書き込む先（``CustomCombo.choice`` など）。ComfyUI 以外の
    #: バックエンドではグラフが無いので ``None``（値は API の入力に直接入る）。
    target: Target | None = None
    #: 未指定のときに使う値（空なら ``choices[0]``）
    default: str = ""
    #: 選択の番号を書き込むフィールド（``CustomCombo`` は必須。空なら書かない）
    index_field: str = "index"
    #: 選んだ値を数値としても入れる先（``float`` に変換して書き込む）
    numeric_target: Target | None = None
    #: 未指定のときの自動決定（:data:`AutoSource`）
    auto: AutoSource = ""
    #: UI とエージェント向けの一言（省略時の挙動など）
    hint: str = ""

    @property
    def fallback(self) -> str:
        """未指定・不正な値のときに使う値。"""
        if self.default and self.default in self.choices:
            return self.default
        return self.choices[0] if self.choices else ""

    def round_up(self, value: float) -> str:
        """``value`` 以上で最小の（数値として読める）選択肢。無ければ最大のもの。

        wan_dancer の尺を音声の実長から決めるのに使う: 曲が途中で切れないよう
        切り上げ、選択肢の上限で止める。
        """
        numeric = sorted(
            (float(choice), choice)
            for choice in self.choices
            if _is_number(choice)
        )
        if not numeric:
            return self.fallback
        for threshold, choice in numeric:
            if value <= threshold + 1e-6:
                return choice
        return numeric[-1][1]

    def index_of(self, choice: str) -> int:
        """``choice`` の 0 始まりの番号（不明なら既定値の番号）。"""
        try:
            return self.choices.index(choice)
        except ValueError:
            return self.choices.index(self.fallback) if self.fallback in self.choices else 0


@dataclass(frozen=True)
class LoraChain:
    """Where the dynamic user-LoRA chain is spliced into a template (SPEC §3.4).

    The chain is one edge of the graph, cut open: ``head`` is the node whose
    ``MODEL`` output the chain starts from and ``consumers`` are the inputs that
    used to read it directly and are re-pointed at the chain's tail.  With no
    LoRA selected the consumers are wired straight back to ``head``, so the graph
    is identical to the template.

    ``placeholders`` are a template's own (strength 0) ``LoraLoaderModelOnly``
    stubs, which the app deletes before rebuilding the chain.  Only the image
    template has them; the LTX 2.3 templates splice in **after** their fixed
    LoRA nodes (distill / ID-LoRA / IC-LoRA), which must stay, so their
    ``placeholders`` is empty.
    """

    head: str
    placeholders: tuple[str, ...] = ()
    consumers: tuple[Target, ...] = ()


#: Model families, one per ``workflow/<kind>/<folder>``.  A registered LoRA is
#: trained for exactly one family, so the family decides which image workflow a
#: LoRA may be used with (SPEC §3.4).
ImageFamily = Literal["krea2", "anima", "z-image", "qwen-image"]

#: 日本語ラベル（設定画面の LoRA フォームと一覧バッジ）
FAMILY_LABELS: dict[str, str] = {
    "krea2": "Krea 2",
    "anima": "Anima",
    "z-image": "Z-Image",
    "qwen-image": "Qwen-Image Edit",
    "grok-imagine": "Grok Imagine",
    "gpt-image": "GPT Image 2",
    "ltx2.3": "LTX 2.3",
    "wan": "Wan 2.2",
    "ace-step": "ACE-Step 1.5",
    "stable-audio": "Stable Audio 3",
    "veo": "Veo 3.1",
    "kling": "Kling 3.0",
    "seedance": "Seedance 2",
    "suno": "Suno V5",
}

#: LoRA registrations default to this family (the only image workflow that
#: existed before the selector), so the DB migration can backfill with it.
DEFAULT_FAMILY = "krea2"


#: kie.ai のタスク入力（``input``）に流し込める論理名。ComfyUI マニフェストの
#: ``inject`` と同じ語彙にしてあるので、同じワークフローを両バックエンドで書いても
#: 意味がずれない。``select:<名前>`` の形で :class:`SelectSpec` の値も渡せる。
KIE_VALUES: frozenset[str] = frozenset({
    "prompt",
    "negative_prompt",
    "aspect_ratio",
    "duration",
    "fps",
    "seed",
    "lyrics",
    "bpm",
    "language",
    # 音声の「除外したい要素」（Suno の negativeTags）。画像・動画の
    # `negative_prompt` とは別物なので混ぜない（§2.4）。
    "negative_tags",
    # 入力ファイル: File Upload API で公開 URL にしてから入れる（§5.2）
    "image",
    "end_image",
    "audio",
    "video",
    # **複数**の入力ファイル（:data:`MULTI_INPUT_FIELDS`）。1 つの論理名が
    # ファイルのリストを持ち、URL の**配列**として ``input`` に入る（§5.2）。
    "reference_images",
    "reference_videos",
    "reference_audios",
    # マルチショット（:class:`MultiShotSpec`、Kling）。``multi_shots`` は
    # 「ショット割りで作る」の真偽値、``multi_prompt`` は
    # ``[{"prompt": ..., "duration": ...}]`` の配列（§3.1）。
    "multi_shots",
    "multi_prompt",
    # Elements（:class:`ElementsSpec`、Kling）。参照画像を要素にまとめ、
    # プロンプト中の ``@要素名`` で呼び出す（§3.1）。
    "kling_elements",
})

#: ``select:<名前>`` の接頭辞
KIE_SELECT_PREFIX = "select:"

#: kie.ai の API 系統。``market`` が統一 API（``/api/v1/jobs/*``）、``veo`` /
#: ``suno`` はモデル別の旧専用系（:class:`app.kie.VeoTaskApi` /
#: :class:`app.kie.SunoTaskApi`）。
KieApi = Literal["market", "veo", "suno"]


@dataclass(frozen=True)
class KieTask:
    """kie.ai のタスクとして 1 ワークフローを実行するための宣言（SPEC §5.2）。

    ComfyUI の :class:`Target` 群にあたるもの。「どのモデルに」「どの論理値を
    ``input`` のどのキーで」渡すかだけを持ち、実際の組み立ては
    :func:`app.kie.task_input` が行う。モデル名も価格もここ（＝マニフェスト）に
    書くので、kie.ai 側でモデルが増減してもコードは触らない。
    """

    #: ``createTask`` の ``model``（例 ``"google/veo3.1"``）
    model: str
    #: 論理名（:data:`KIE_VALUES` か ``select:<名前>``）-> ``input`` のキー
    fields: dict[str, str] = field(default_factory=dict)
    #: 常に同じ値で入れる ``input`` のキー（モデル固有の固定オプション）。
    #: モードの切り替え（Veo の ``generationType``）も、モードごとに
    #: ワークフローを分けてあるのでここに書く固定値で足りる。
    constants: dict[str, Any] = field(default_factory=dict)
    #: **配列で渡す** ``input`` のキー。同じキーに複数の論理名を割り当てられる
    #: ようになり、値は :attr:`fields` の宣言順に並ぶ（Veo の ``imageUrls`` は
    #: 「1 枚目 = 開始フレーム / 2 枚目 = 最終フレーム」の順序が意味を持つ）。
    list_keys: tuple[str, ...] = ()
    #: **真偽値で渡す** ``input`` のキー。選択式フィールドの値は文字列で届くので
    #: （``"true"`` / ``"false"``）、ここに挙げたキーだけ ``bool`` に直してから
    #: 送る（Kling の ``sound``）。JSON の型が違うと API に弾かれる。
    bool_keys: tuple[str, ...] = ()
    #: **整数で渡す** ``input`` のキー。同じ「尺」でもモデルごとに型が違う
    #: （Kling の ``duration`` は文字列、Seedance の ``duration`` は int）ので、
    #: 選択式フィールドの文字列を ``int`` に直すキーをここで宣言する。
    int_keys: tuple[str, ...] = ()
    #: **小数で渡す** ``input`` のキー（Suno の ``styleWeight`` など 0〜1 の
    #: つまみ）。選択式フィールドの文字列を ``float`` に直してから送り、
    #: **数として読めない値（``"auto"`` = 指定しない）はキーごと落とす**。
    #: 「未指定」を送らないことに意味がある（0 は「0 を指定した」になる）。
    float_keys: tuple[str, ...] = ()
    #: API 系統（既定は Market 系の統一 API）
    api: KieApi = "market"
    #: 1 タスクの概算クレジット（0 = 不明。実消費は ``creditsConsumed`` を記録する）
    credits: float = 0.0


@dataclass(frozen=True)
class GrokCliTask:
    """Grok Build CLI（サブスク枠）で 1 ワークフローを実行するための宣言（§5.2）。

    CLI にはグラフも ``input`` も無く、渡せるのは**自然文の指示だけ**なので、
    :class:`KieTask` のような「キーの対応表」は持たない。宣言するのは「どの論理値を
    指示文に織り込むか」と「何が出てくるか」の 2 つだけで、指示文の組み立ては
    :func:`app.grok_media.build_request` が行う。

    入力ファイル（``image``）を宣言すると、そのファイルは**作業ディレクトリへ
    コピー**され、指示文がファイル名で参照する（開始フレーム、issue #22）。
    """

    #: 指示文に織り込む論理値（:data:`KIE_VALUES` と同じ語彙）。``select:<名前>``
    #: の形で :class:`SelectSpec` の値も織り込める（動画の尺・解像度・縦横比）。
    #: 解像度・縦横比はプロンプト経由の**希望**であって、厳密な制御は保証されない
    #: （issue #21）。
    values: tuple[str, ...] = ("prompt", "aspect_ratio")
    #: 生成物の種類（``image`` / ``video``）
    media: Literal["image", "video"] = "image"


@dataclass(frozen=True)
class CodexCliTask:
    """Codex CLI（ChatGPT サブスク枠）で 1 ワークフローを実行する宣言（§5.4）。

    :class:`GrokCliTask` と同じ発想（渡せるのは自然文の指示だけなので「キーの
    対応表」は持たない）だが、コマンド体系がまったく違う（``codex exec`` +
    ``--output-last-message``）ので別の宣言にしてある。指示文の組み立ては
    :func:`app.codex_media.build_request`。

    gpt-image-2 は今のところ text-to-image だけなので :attr:`media` は持たない
    （動画を作れる Codex の経路が出てきたら、そのときに足す）。
    """

    #: 指示文に織り込む論理値（:data:`KIE_VALUES` と同じ語彙）。``select:<名前>``
    #: の形で :class:`SelectSpec` の値も織り込める（大きさ・品質）。サイズ・品質は
    #: 自然文で伝える**希望**であって、API 同等の厳密保証は無い（issue #23）。
    values: tuple[str, ...] = ("prompt",)


@dataclass(frozen=True)
class MultiShotSpec:
    """**ショット割り**で 1 本の動画を作れるモデルの宣言（SPEC §3.1、Kling 3.0）。

    宣言は**ショット割り専用のワークフロー**（:data:`KLING3_MULTISHOT`）だけが
    持つ。ジョブは 1 本の ``video_prompt`` の代わりに ``multi_shots``
    （``[{"prompt": ..., "duration": ...}]``）を**必ず**持ち、``input`` には
    ``multi_shots: true`` と ``multi_prompt`` の配列が入って**トップレベルの
    ``prompt`` は送らない**（API 仕様どおり、ショットの文だけで決まる）。
    ショットの有無・件数・1 ショットの長さ・1 ショットのプロンプト長は投入前に
    :func:`app.models.multi_shot_problem` が見る。
    """

    #: 1 ジョブで並べられるショット数
    max_shots: int = 5
    #: 1 ショットの尺（秒、整数）
    min_duration: int = 1
    max_duration: int = 12


@dataclass(frozen=True)
class ElementsSpec:
    """**Elements**（参照画像を名前つきの要素にまとめる）の宣言（§3.1、Kling 3.0）。

    1 要素 = 名前 + 説明 + 参照画像 2〜4 枚で、プロンプト本文からは ``@要素名``
    で呼び出す。**``@要素名`` 1 回はプロンプトの文字数を
    :attr:`reference_chars` 文字消費する**（実際の文字数ではない）ので、500 文字
    の上限を数えるときはこの補正を掛ける（:func:`app.models.prompt_chars`）。
    """

    #: 1 ジョブで宣言できる要素の数
    max_elements: int = 3
    #: 1 要素に付ける参照画像の枚数
    min_images: int = 2
    max_images: int = 4
    #: ``@要素名`` 1 参照がプロンプトの上限から消費する文字数
    reference_chars: int = 37


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    kind: WorkflowKind
    #: ``workflow/`` からの相対パス（``backend`` が ``comfyui`` のときだけ意味を持つ）
    relpath: str = ""
    #: logical name -> injection target
    inject: dict[str, Target] = field(default_factory=dict)
    #: node id that produces the artefact the job runner downloads
    output_node: str = ""
    #: このワークフローを実行するエンジン（SPEC §5.2）。既定は従来どおり ComfyUI。
    backend: WorkflowBackend = "comfyui"
    #: ``backend == "kie"`` のときのタスク宣言（それ以外では ``None``）
    kie: KieTask | None = None
    #: ``backend == "grok_cli"`` のときのタスク宣言（それ以外では ``None``）
    grok: GrokCliTask | None = None
    #: ``backend == "codex_cli"`` のときのタスク宣言（それ以外では ``None``）
    codex: CodexCliTask | None = None
    #: model family (= the ``workflow/<kind>/<folder>`` name).  Image LoRAs are
    #: only offered for the family of the selected image workflow; the video
    #: templates all share the ``ltx2.3`` family and ignore it.
    family: str = DEFAULT_FAMILY
    requires: tuple[InputName, ...] = ()
    #: **複数ファイル**を配列で受け取る論理入力（論理名 -> 受け取れる件数の上限、
    #: SPEC §3.1）。Seedance のマルチモーダル参照（参照画像 9 枚 / 参照動画 3 本 /
    #: 参照音声 3 本）用で、宣言のないワークフローに参照素材を渡すと 422 になる
    #: （:func:`app.models.reference_problem`）。名前は
    #: :data:`MULTI_INPUT_FIELDS` のキー。**参照素材を使うモードは開始フレームと
    #: 排他**（外部 API 側の制約）なので、宣言を持つのは参照専用のワークフロー
    #: （``*_ref`` / ``veo3_1_fast_ref``）だけで、そちらは
    #: :attr:`accepts_start_image` が False になっている。
    multi_inputs: dict[str, int] = field(default_factory=dict)
    #: **選択式どうしの相関**（名前 -> ``(相手の名前, 相手に必要な値)``、§3.1）。
    #: 「その項目は相手がこの値のときしか効かない」ことの宣言で、既定以外を
    #: 選んでいるのに相手が違う値なら投入前に 422 にする
    #: （:func:`app.models.select_problem`）。Suno の ``duration`` は
    #: ``model`` が ``V5_5`` のときしか効かず、**他のモデルでは黙って無視される**
    #: ので、気づかずに指定してしまうのを防ぐ。
    select_requires: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: **ショット割り**の宣言（``None`` = 1 ジョブ 1 ショットのみ、SPEC §3.1）
    multi_shot: MultiShotSpec | None = None
    #: **Elements**（``@要素名`` で呼ぶ参照画像の束）の宣言（``None`` = 非対応）
    elements: ElementsSpec | None = None
    #: what the workflow is for, in one or two Japanese sentences.  This is the
    #: single source of the catalog embedded in the Grok system prompts
    #: (:func:`video_catalog`), so keep it factual and short.
    description: str = ""
    #: how ``audio_path`` is used.  Empty means "there is no audio input": the
    #: model then generates the soundtrack together with the picture.
    audio_role: str = ""
    #: how to write ``video_prompt`` for this workflow (English, it goes
    #: straight into the LLM prompts).  Required for video workflows.
    prompt_hint: str = ""
    #: プロンプトの長さの上限（文字数、0 = 上限なし）。外部 API は長すぎる
    #: プロンプトを 422 で弾く（Kling 3.0 は 500 文字）ので、ジョブを投入する
    #: 前に :func:`app.models.prompt_length_problem` で落とす。
    max_prompt_chars: int = 0
    #: audio workflows only: the clip length the model supports, in seconds.
    #: ``duration`` outside ``[min_duration, max_duration]`` is rejected before
    #: the job is queued (0.0 == no limit declared).
    min_duration: float = 0.0
    max_duration: float = 0.0
    #: audio workflows only: the length the UI / the agent start from
    default_duration: float = 0.0
    #: can this workflow be the second stage of a full (image -> video) job?
    accepts_start_image: bool = False
    #: UI label of the primary image input
    image_label: str = "開始フレーム"
    #: 動画の幅・高さを丸める単位。LTX の latent は 32px 単位なので、32 の倍数で
    #: ないと端が数 px 欠ける。さらに union-control IC-LoRA は参照動画を 0.5 倍
    #: 解像度で latent エンコードするため、その半分（= 元の 64 の倍数）でないと
    #: latent の形が合わずに実行時に落ちる。
    resolution_multiple: int = 8
    lora_chain: LoraChain | None = None
    notes: str = ""
    seeds: tuple[Target, ...] = ()
    #: extra targets keyed by logical name that are always forced to a constant
    constants: dict[str, Any] = field(default_factory=dict)
    #: 選択式フィールド（論理名 -> :class:`SelectSpec`）。宣言のないワークフロー
    #: では空なので、フォームにもジョブにも何も増えない。
    selects: dict[str, SelectSpec] = field(default_factory=dict)
    #: ``video_prompt`` が必須か。プロンプトをコンボから組み立てるワークフロー
    #: （wan_dancer）は False で、書かれた場合だけ注入する。
    prompt_required: bool = True

    @property
    def path(self):
        return WORKFLOW_DIR / self.relpath

    def targets(self) -> Iterable[Target]:
        yield from self.inject.values()
        yield from self.seeds
        for select in self.selects.values():
            if select.target is not None:
                yield select.target
            if select.numeric_target is not None:
                yield select.numeric_target
        if self.lora_chain is not None:
            yield from self.lora_chain.consumers

    def select(self, name: str) -> SelectSpec | None:
        return self.selects.get(name)

    def supports(self, name: str) -> bool:
        """このワークフローが論理名 ``name`` の値を受け取るか。

        バックエンドごとに「受け取り口」の持ち方が違う（ComfyUI は
        :attr:`inject`、kie.ai は :attr:`KieTask.fields`、Grok Build CLI は
        :attr:`GrokCliTask.values`）ので、呼び出し側はどちらかを知らずに済むよう
        ここで吸収する。
        """
        if name in self.inject:
            return True
        if self.kie is not None and name in self.kie.fields:
            return True
        if self.grok is not None and name in self.grok.values:
            return True
        return self.codex is not None and name in self.codex.values

    def supported_names(self) -> tuple[str, ...]:
        """このワークフローが受け取る論理名（フォームとカタログが読む）。

        :meth:`supports` の一覧版。ComfyUI は :attr:`inject`、kie.ai は
        :attr:`KieTask.fields`、Grok Build CLI は :attr:`GrokCliTask.values` が
        受け取り口なので、すべて同じ語彙で見せる（``select:`` 付きは選択式として
        別に案内するので外す）。
        """
        names = set(self.inject)
        if self.kie is not None:
            names |= set(self.kie.fields)
        if self.grok is not None:
            names |= set(self.grok.values)
        if self.codex is not None:
            names |= set(self.codex.values)
        return tuple(
            sorted(name for name in names if not name.startswith(KIE_SELECT_PREFIX))
        )

    def target(self, name: str) -> Target | None:
        return self.inject.get(name)


# --------------------------------------------------------------------------
# image: workflow/image/*/*.json
# --------------------------------------------------------------------------

KREA2_TURBO = WorkflowSpec(
    id="krea2_turbo",
    label="Krea 2 turbo",
    kind="image",
    family="krea2",
    relpath="image/krea2/krea2_turbo.json",
    output_node="29",
    description=(
        "Text-to-image. Writes one still from `image_prompt` alone; the"
        " resolution comes from `aspect_ratio` + `megapixels`. Use it for"
        " `mode: \"image_only\"` and as the first stage of `mode: \"full\"`."
    ),
    inject={
        "aspect_ratio": T("49", "aspect_ratio", "ResolutionSelector"),
        "megapixels": T("49", "megapixels", "ResolutionSelector"),
        "prompt": T("30:19", "value", "PrimitiveStringMultiline"),
        "seed": T("30:3", "seed", "KSampler"),
        # local TextGenerate refine is off: Grok already writes the final prompt
        "refine_enable": T("30:24", "value", "PrimitiveBoolean"),
        # StringConcatenate that prepends the LoRA trigger words
        "trigger_concat": T("30:27", "", "StringConcatenate"),
        "trigger_switch": T("30:28", "switch", "ComfySwitchNode"),
        # PreviewAny that carries the (possibly refined) prompt string
        "prompt_source": T("30:20", "", "PreviewAny"),
        "save_prefix": T("29", "filename_prefix", "SaveImage"),
    },
    lora_chain=LoraChain(
        head="30:10",
        placeholders=("30:61:60", "30:61:58", "30:61:57", "30:61:55", "30:61:62"),
        consumers=(T("30:3", "model", "KSampler"),),
    ),
    constants={"refine_enable": False},
)

ANIMA = WorkflowSpec(
    id="anima",
    label="Anima",
    kind="image",
    family="anima",
    relpath="image/anima/anima.json",
    output_node="46",
    description=(
        "Text-to-image, anime / illustration oriented (Anima base). Same shape"
        " as krea2: `image_prompt` only, resolution from `aspect_ratio` +"
        " `megapixels`. Usable for `mode: \"image_only\"` and as the first stage"
        " of `mode: \"full\"`."
    ),
    inject={
        "aspect_ratio": T("91", "aspect_ratio", "ResolutionSelector"),
        "megapixels": T("91", "megapixels", "ResolutionSelector"),
        "prompt": T("90:77", "text", "CLIPTextEncode"),
        "seed": T("90:76", "seed", "KSampler"),
        "save_prefix": T("46", "filename_prefix", "SaveImage"),
    },
    lora_chain=LoraChain(
        head="90:78",
        placeholders=("90:83",),
        consumers=(T("90:76", "model", "KSampler"),),
    ),
    notes="anima-base-v1.0 / negative は既定値のまま",
)

Z_IMAGE_TURBO = WorkflowSpec(
    id="z_image_turbo",
    label="Z-Image turbo",
    kind="image",
    family="z-image",
    relpath="image/z-image/z_image_turbo.json",
    output_node="9",
    description=(
        "Text-to-image, 8-step distilled Tongyi Z-Image turbo. `image_prompt`"
        " only; the template has no ResolutionSelector, so the app computes the"
        " width / height from `aspect_ratio` + `megapixels` itself. Usable for"
        " `mode: \"image_only\"` and as the first stage of `mode: \"full\"`."
    ),
    inject={
        # no ResolutionSelector in this graph: the app injects plain integers
        "width": T("57:13", "width", "EmptySD3LatentImage"),
        "height": T("57:13", "height", "EmptySD3LatentImage"),
        "prompt": T("57:27", "text", "CLIPTextEncode"),
        "seed": T("57:3", "seed", "KSampler"),
        "save_prefix": T("9", "filename_prefix", "SaveImage"),
    },
    lora_chain=LoraChain(
        head="57:28",
        placeholders=("57:63",),
        consumers=(T("57:11", "model", "ModelSamplingAuraFlow"),),
    ),
    notes="z_image_turbo_bf16 / 8 steps・CFG 1（ネガティブは ConditioningZeroOut）",
)

QWEN_IMAGE_EDIT = WorkflowSpec(
    id="qwen_image_edit_2511",
    label="Qwen-Image Edit 2511",
    kind="image",
    family="qwen-image",
    relpath="image/qwen-image/qwen_image_edit_2511.json",
    output_node="195",
    requires=("image",),
    description=(
        "Image **editing**, not text-to-image: it rewrites the picture given in"
        " `source_image` following the instruction in `image_prompt`, so"
        " `source_image` is REQUIRED in every mode that runs the image stage"
        " (including `mode: \"full\"`, where the edited still then becomes the"
        " video's start frame). The output resolution is derived from the input"
        " image (FluxKontextImageScale), so `aspect_ratio` / `megapixels` are"
        " ignored by this workflow. Write `image_prompt` as an edit instruction"
        ' ("change X to Y, keep everything else unchanged"), never as a full'
        " scene description."
    ),
    image_label="編集元画像",
    inject={
        "image": T("41", "image", "LoadImage"),
        "prompt": T("170:151", "prompt", "TextEncodeQwenImageEditPlus"),
        "seed": T("170:169", "seed", "KSampler"),
        "save_prefix": T("195", "filename_prefix", "SaveImageAdvanced"),
    },
    # The template's own `170:153` LoraLoaderModelOnly is the Lightning 4-steps
    # speed LoRA and must stay, so it is *not* a placeholder.  The user chain is
    # spliced in front of it — at the CFGNorm output both branches of the
    # `170:163` Switch (Model) read — so the user LoRA applies whether the
    # 4-steps LoRA is switched on or off.
    lora_chain=LoraChain(
        head="170:152",
        consumers=(
            T("170:153", "model", "LoraLoaderModelOnly"),
            T("170:163", "on_false", "ComfySwitchNode"),
        ),
    ),
    notes="qwen_image_edit_2511 + Lightning 4steps LoRA / 解像度は入力画像から自動",
)


# --------------------------------------------------------------------------
# image: Grok Build CLI（サブスク枠、SPEC §5.2 / issue #21）
# --------------------------------------------------------------------------
#
# xAI の従量課金 API ではなく、SuperGrok / X Premium+ のサブスクリプションで動く
# 公式 CLI をヘッドレス実行して Grok Imagine に描かせる（:mod:`app.grok_media`）。
# ComfyUI / kie.ai と違って渡せるのは**自然文の指示だけ**なので、解像度・縦横比は
# 指示文に織り込む「希望」であって厳密な制御は保証されない。
#
# 枠は Chat / Imagine / Build 横断の共有プールで、目安は画像 ~40 枚/日
# （SuperGrok、時期・地域で変動）。使い切ったときは :class:`app.grok_media.
# GrokQuotaError` として「時間をおいて」の案内に変換する。

GROK_IMAGINE_PROMPT_HINT = (
    "One natural-language description, not a tag list. Order: subject → style /"
    " medium → environment → lighting → mood → technical (lens, framing)."
    " **The first 20-30 words carry the most weight**, so put the subject and"
    " the look there. Name the light source and its quality, and use concrete"
    " material words. Never write what you do not want (`no blur` is ignored) —"
    " say the positive form instead (`sharp focus`)."
)

GROK_IMAGINE = WorkflowSpec(
    id="grok_imagine",
    label="Grok Imagine（サブスク CLI）",
    kind="image",
    family="grok-imagine",
    backend="grok_cli",
    description=(
        "Text-to-image through the official Grok Build CLI, on the SuperGrok /"
        " X Premium+ **subscription** quota (no metered API). Same shape as the"
        " local text-to-image workflows: `image_prompt` only, usable for"
        " `mode: \"image_only\"` and as the first stage of `mode: \"full\"`."
        " `aspect_ratio` is passed as a wish inside the instruction, so the"
        " exact resolution is not guaranteed. The model refuses real people,"
        " celebrities and trademarks, and the daily quota is shared with Grok"
        " chat. LoRAs cannot be used."
    ),
    prompt_hint=GROK_IMAGINE_PROMPT_HINT,
    grok=GrokCliTask(values=("prompt", "aspect_ratio"), media="image"),
    notes=(
        "Grok Build CLI（サブスク枠）/ 縦横比・解像度はプロンプト経由の希望 /"
        " LoRA 不可 / 実在人物・著名人・商標はモデレーションで弾かれる /"
        " 枠は Chat と共有（目安 40 枚/日）"
    ),
)


# --------------------------------------------------------------------------
# image: Codex CLI（ChatGPT サブスク枠、SPEC §5.4 / issue #23）
# --------------------------------------------------------------------------
#
# OpenAI の従量課金 API ではなく、ChatGPT Plus / Pro のサブスクリプションで動く
# 公式 CLI（`codex exec`）の組み込みスキル `$imagegen` に gpt-image-2 で描かせる
# （:mod:`app.codex_media`）。Grok CLI と同じく渡せるのは**自然文の指示だけ**で、
# 大きさ・品質も指示文に織り込む「希望」（API 同等の厳密保証は無い）。
#
# 位置づけは「高品質枠として少量」: 画像生成ターンは通常のターンより 3〜5 倍速く
# 5 時間 / 週次の枠を消費する（公式明記）。月に数百枚を回すようになったら API 直
# （gpt-image-1.5）への切り替えを検討する。

#: gpt-image-2 が受ける大きさ（正方形・横長・縦長）
GPT_IMAGE_SIZES: tuple[str, ...] = ("1024x1024", "1536x1024", "1024x1536")
#: 品質（枠の消費量に直結するので既定は medium）
GPT_IMAGE_QUALITIES: tuple[str, ...] = ("low", "medium", "high")

GPT_IMAGE2_PROMPT_HINT = (
    "One natural-language description in this order: background / scene →"
    " subject → key details → constraints, and say what the picture is for"
    " (ad, UI mock, …). **Text rendering is this model's strength**: quote every"
    " string verbatim and name its font, size, colour and placement, then say"
    " no other text should appear. Name the medium (photo / watercolour / 3D"
    " render) and write `photorealistic` outright when that is what you want."
)

GPT_IMAGE2 = WorkflowSpec(
    id="gpt_image2",
    label="gpt-image-2（Codex CLI）",
    kind="image",
    family="gpt-image",
    backend="codex_cli",
    description=(
        "Text-to-image through the official Codex CLI, on the **ChatGPT"
        " subscription** quota (no metered API key). Same shape as the local"
        " text-to-image workflows: `image_prompt` only, usable for"
        " `mode: \"image_only\"` and as the first stage of `mode: \"full\"`."
        " Its strengths are rendered text, instruction following and"
        " photorealism, so use it as the high-quality option rather than the"
        " default one — an image turn eats the shared ChatGPT quota 3-5x faster"
        " than a normal turn. `size` and `quality` are job fields (`selects`)"
        " passed as *wishes* inside the instruction, so they are not guaranteed"
        " exactly, and `aspect_ratio` / `megapixels` are ignored (pick the"
        " `size` instead). Transparent backgrounds are not supported. LoRAs"
        " cannot be used."
    ),
    prompt_hint=GPT_IMAGE2_PROMPT_HINT,
    codex=CodexCliTask(
        values=(
            "prompt",
            f"{KIE_SELECT_PREFIX}size",
            f"{KIE_SELECT_PREFIX}quality",
        ),
    ),
    selects={
        "size": SelectSpec(
            label="大きさ",
            choices=GPT_IMAGE_SIZES,
            default="1024x1024",
            hint=(
                "指示文に書く希望なので、実際の出力はぶれることがある"
                "（縦横比プリセットとメガピクセルはこのワークフローでは使わない）。"
            ),
        ),
        "quality": SelectSpec(
            label="品質",
            choices=GPT_IMAGE_QUALITIES,
            default="medium",
            hint="高いほどサブスク枠の消費が増える。",
        ),
    },
    notes=(
        "Codex CLI（ChatGPT サブスク枠）/ 大きさ・品質はプロンプト経由の希望 /"
        " 縦横比・メガピクセルは使わない / LoRA 不可 / 透過背景は非対応"
        "（クロマキー方式での透過は未対応。必要なら別途 gpt-image-1.5 の API へ）/"
        " 画像生成ターンは通常の 3〜5 倍速く枠を消費するので少量利用向け"
    ),
)


# --------------------------------------------------------------------------
# video: workflow/video/ltx2.3/*.json
# --------------------------------------------------------------------------
#
# Video LoRA chain (SPEC §3.4): every LTX template already carries fixed LoRA
# nodes it cannot work without — the distilled-1.1 speed LoRA on the "dev"
# graphs, the talkvid ID-LoRA, the ingredients / union-control IC-LoRAs.  The
# user chain is therefore spliced in *after* the last of those on the path to
# the sampler, and only the sampler-side consumers are re-pointed:
#
# * t2v / i2v / ia2v: head = the distill ``LoraLoaderModelOnly``, consumers =
#   both ``CFGGuider.model`` inputs (the base and the upscale pass).  The Gemma
#   ``LoraLoader`` hanging off the same output is a text-encoder LoRA whose
#   MODEL output nothing reads, so it keeps the raw model.
# * id_lora: head = the distill LoRA as well, consumers = the first
#   ``CFGGuider`` *and* the talkvid ID-LoRA node, so the user LoRA applies to
#   both passes while staying in front of the ID-LoRA / LTXVReferenceAudio.
# * flf2v: head = the distill ``LoraLoaderModelOnly`` and the single
#   ``CFGGuider`` is the consumer.
# * ic_lora_*: head = the IC-LoRA node, consumer = ``KSampler.model``.  The
#   ``GetICLoRAParameters`` branch keeps reading the IC-LoRA model directly.

_DEV_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"

LTX_T2V = WorkflowSpec(
    id="ltx2_3_t2v",
    label="テキスト→動画 (t2v)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/ltx2_3_t2v.json",
    output_node="75",
    requires=(),
    description=(
        "テキストだけから動画を生成する。開始フレームは不要で、画面に写るものは"
        "すべてプロンプトで決まる。"
    ),
    prompt_hint=(
        "No start frame exists: the prompt has to establish the subject, the"
        " set, the wardrobe and the framing as well as the motion. Never open"
        ' with "Starting from the given first frame".'
    ),
    accepts_start_image=False,
    resolution_multiple=32,
    inject={
        "prompt": T("267:266", "value", "PrimitiveStringMultiline"),
        "negative": T("267:247", "text", "CLIPTextEncode"),
        "width": T("267:257", "value", "PrimitiveInt"),
        "height": T("267:258", "value", "PrimitiveInt"),
        "duration": T("267:225", "value", "PrimitiveInt"),
        "fps": T("267:260", "value", "PrimitiveInt"),
        "frames_expr": T("267:277", "", "ComfyMathExpression"),
        "prompt_enhance": T("267:330", "value", "PrimitiveBoolean"),
        "save_prefix": T("75", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("267:216", "noise_seed", "RandomNoise"),
        T("267:237", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="267:232",
        consumers=(
            T("267:213", "model", "CFGGuider"),
            T("267:231", "model", "CFGGuider"),
        ),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 / 開始画像なし",
)

LTX_I2V = WorkflowSpec(
    id="tx2_3_i2v",
    label="画像→動画 (i2v)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/tx2_3_i2v.json",
    output_node="75",
    requires=("image",),
    description=(
        "開始フレーム画像から動画を生成する。被写体とセットは画像が決め、"
        "プロンプトは動きを担当する。"
    ),
    prompt_hint=(
        "The start frame supplies the looks of the subject and the set — never"
        ' contradict it. Open the motion description with "Starting from the'
        ' given first frame, …" and spend the paragraph on movement, body and'
        " face reactions, camera and sound."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    resolution_multiple=32,
    inject={
        "prompt": T("320:319", "value", "PrimitiveStringMultiline"),
        "negative": T("320:313", "text", "CLIPTextEncode"),
        "width": T("320:312", "value", "PrimitiveInt"),
        "height": T("320:299", "value", "PrimitiveInt"),
        "duration": T("320:301", "value", "PrimitiveInt"),
        "fps": T("320:300", "value", "PrimitiveInt"),
        "frames_expr": T("320:323", "", "ComfyMathExpression"),
        "prompt_enhance": T("320:328", "value", "PrimitiveBoolean"),
        "image": T("269", "image", "LoadImage"),
        "save_prefix": T("75", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("320:276", "noise_seed", "RandomNoise"),
        T("320:277", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="320:285",
        consumers=(
            T("320:282", "model", "CFGGuider"),
            T("320:314", "model", "CFGGuider"),
        ),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 / 音声は生成側で合成",
)

LTX_IA2V = WorkflowSpec(
    id="tx2_3_ia2v",
    label="画像+音声→動画 (ia2v)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/tx2_3_ia2v.json",
    output_node="341",
    requires=("image", "audio"),
    description=(
        "開始フレーム画像と音声ファイルから動画を生成する。渡した音声がそのまま"
        "クリップの音声トラックになり、映像はその音に合わせて動く。"
    ),
    audio_role=(
        "指定した音声ファイルがクリップの音声トラックそのものになる"
        "（`audio_path` 必須）。セリフは音声側で決まるのでプロンプトには書かない。"
    ),
    prompt_hint=(
        "The user's audio file *is* the clip's soundtrack, so the picture has to"
        ' follow it. Open with "Starting from the given first frame, …" and'
        " describe motion that matches that audio (speech rhythm, music,"
        " moans). Do NOT write spoken lines in double quotes — the words come"
        " from the file, not from the prompt; keep the audio sentence about"
        " ambience and body sounds only."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    resolution_multiple=32,
    inject={
        "prompt": T("340:319", "value", "PrimitiveStringMultiline"),
        "negative": T("340:314", "text", "CLIPTextEncode"),
        "width": T("340:330", "value", "PrimitiveInt"),
        "height": T("340:324", "value", "PrimitiveInt"),
        "duration": T("340:331", "value", "PrimitiveFloat"),
        "fps": T("340:323", "value", "PrimitiveInt"),
        "frames_expr": T("340:329", "", "ComfyMathExpression"),
        "prompt_enhance": T("340:349", "value", "PrimitiveBoolean"),
        "image": T("269", "image", "LoadImage"),
        "audio": T("276", "audio", "LoadAudio"),
        "save_prefix": T("341", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("340:285", "noise_seed", "RandomNoise"),
        T("340:286", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="340:293",
        consumers=(
            T("340:290", "model", "CFGGuider"),
            T("340:315", "model", "CFGGuider"),
        ),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 / 音声をラテントに焼き込む",
)

LTX_ID_LORA = WorkflowSpec(
    id="ltx2_3_id_lora",
    label="画像+参照音声→動画・リップシンク (ID-LoRA)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/ltx2_3_id_lora.json",
    output_node="341",
    requires=("image", "audio"),
    description=(
        "開始フレーム画像とリファレンス音声から、talkvid ID-LoRA で口の動きが"
        "揃った喋りの動画を生成する。音声そのものはモデルが生成し、リファレンス"
        "音声は声質とリップシンクの参照に使う。"
    ),
    audio_role=(
        "リファレンス音声（`audio_path` 必須）。声質と口の動きの参照に使うだけで、"
        "出力される音声はモデルが生成するので、セリフはプロンプトの二重引用符で"
        "指定する。"
    ),
    prompt_hint=(
        "The clip is a lip-synced talking performance: the reference audio drives"
        ' the voice and the mouth. Open with "Starting from the given first'
        ' frame, …", describe the delivery (how she speaks, expression, head and'
        " body movement while talking) and keep the spoken lines short inside"
        " double quotes — the model synthesizes them verbatim."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    resolution_multiple=32,
    inject={
        "prompt": T("340:319", "value", "PrimitiveStringMultiline"),
        "negative": T("340:314", "text", "CLIPTextEncode"),
        "width": T("340:330", "value", "PrimitiveInt"),
        "height": T("340:324", "value", "PrimitiveInt"),
        "duration": T("340:331", "value", "PrimitiveFloat"),
        "fps": T("340:323", "value", "PrimitiveInt"),
        "frames_expr": T("340:329", "", "ComfyMathExpression"),
        "image": T("269", "image", "LoadImage"),
        "audio": T("276", "audio", "LoadAudio"),
        "save_prefix": T("341", "filename_prefix", "SaveVideo"),
    },
    seeds=(
        T("340:285", "noise_seed", "RandomNoise"),
        T("340:286", "noise_seed", "RandomNoise"),
    ),
    lora_chain=LoraChain(
        head="340:293",
        consumers=(
            T("340:290", "model", "CFGGuider"),
            # the ID-LoRA -> LTXVReferenceAudio -> 2nd CFGGuider branch
            T("340:346", "model", "LoraLoaderModelOnly"),
        ),
    ),
    notes="ltx-2.3-22b-dev-fp8 + talkvid ID-LoRA / LTXVReferenceAudio",
)

LTX_FLF2V = WorkflowSpec(
    id="ltx2_3_flf2v",
    label="最初と最後のフレーム指定 (flf2v)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/ltx2_3_flf2v.json",
    output_node="68",
    requires=("image", "end_image"),
    description=(
        "最初のフレームと最後のフレームの画像を指定し、その間の動きを補間する。"
    ),
    prompt_hint=(
        "Both the first and the last frame are given: describe the *transition*"
        " between them — the path the body, the wardrobe and the camera take"
        " from the opening pose to the closing one — and make sure the arc ends"
        " exactly where the last frame is. Do not describe motion that would"
        " leave the character somewhere else."
    ),
    accepts_start_image=True,
    image_label="最初のフレーム",
    resolution_multiple=32,
    inject={
        "prompt": T("129:128", "text", "CLIPTextEncode"),
        "negative": T("129:112", "text", "CLIPTextEncode"),
        "width": T("129:113", "value", "PrimitiveInt"),
        "height": T("129:98", "value", "PrimitiveInt"),
        "duration": T("129:102", "value", "PrimitiveInt"),
        "fps": T("129:114", "value", "PrimitiveInt"),
        "frames_expr": T("129:130", "", "ComfyMathExpression"),
        "image": T("31", "image", "LoadImage"),
        "end_image": T("39", "image", "LoadImage"),
        "save_prefix": T("68", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129:100", "noise_seed", "RandomNoise"),),
    lora_chain=LoraChain(
        head="129:300",
        consumers=(T("129:116", "model", "CFGGuider"),),
    ),
    notes="ltx-2.3-22b-dev-fp8 + distilled-1.1 LoRA",
)

LTX_IC_LORA_IMAGE = WorkflowSpec(
    id="ltx2_3_ic_lora_image",
    label="リファレンスシート (IC-LoRA)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/ltx2_3_ic_lora_image.json",
    output_node="68",
    requires=("image",),
    description=(
        "複数カットを並べたリファレンスシート画像から動画を生成する"
        "（Ingredients IC-LoRA）。画像は開始フレームではなく見た目の参照なので、"
        "full モードの 2 段目には使えない。"
    ),
    prompt_hint=(
        "The image input is a multi-panel reference sheet, not a first frame: it"
        " only fixes how the character and the props look. Write the prompt in"
        ' the two parts this IC-LoRA expects. Start with "Reference sheet:"'
        " followed by one short clause per panel, in the order the panels are"
        " laid out (left to right, top to bottom), naming what each panel shows"
        ' — then "Generated video:" followed by the shot itself (subject, set,'
        " framing, motion and audio, written from scratch as in t2v). Never"
        ' write "Starting from the given first frame".'
    ),
    # the image is a multi-panel reference sheet, not a first frame
    accepts_start_image=False,
    image_label="リファレンスシート画像",
    resolution_multiple=32,
    inject={
        # prompt enhance is disabled, so the literal on_false branch is used
        "prompt": T("129:211", "on_false", "ComfySwitchNode"),
        "negative": T("129:112", "text", "CLIPTextEncode"),
        # resolution is taken from the padded reference sheet
        "width": T("722", "target_width", "ResizeAndPadImage"),
        "height": T("722", "target_height", "ResizeAndPadImage"),
        "duration": T("715", "value", "PrimitiveInt"),
        "fps": T("716", "value", "PrimitiveInt"),
        "frames_expr": T("717", "", "ComfyMathExpression"),
        "prompt_enhance": T("129:212", "value", "PrimitiveBoolean"),
        "image": T("724", "image", "LoadImage"),
        "save_prefix": T("68", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129:704", "seed", "KSampler"),),
    lora_chain=LoraChain(
        head="129:195",
        consumers=(T("129:704", "model", "KSampler"),),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 + distilled-1.1 LoRA + ingredients IC-LoRA",
)

LTX_IC_LORA_MOTION = WorkflowSpec(
    id="ltx2_3_ic_lora_motion",
    label="参照動画からモーション転写 (IC-LoRA + MoGe)",
    kind="video",
    family="ltx2.3",
    relpath="video/ltx2.3/ltx2_3_ic_lora_motion.json",
    output_node="68",
    requires=("image", "video"),
    description=(
        "参照動画のカメラワークとモーションを MoGe 深度経由で転写し、開始フレーム"
        "画像の被写体で動画を生成する（Union Control IC-LoRA）。クリップの長さは"
        "参照動画から切り出す区間の長さになる。"
    ),
    prompt_hint=(
        "Camera work and the choreography's timing come from the reference"
        " video, not from the prompt: do not describe camera movement or the"
        " tempo of the action. Spend the paragraph on who the subject is, the"
        " set, the wardrobe, expressions, materials and the audio, and keep the"
        " motion wording compatible with what the reference clip does."
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    resolution_multiple=64,
    inject={
        "prompt": T("129:211", "on_false", "ComfySwitchNode"),
        "negative": T("129:112", "text", "CLIPTextEncode"),
        "width": T("129:113", "value", "PrimitiveInt"),
        "height": T("129:98", "value", "PrimitiveInt"),
        "fps": T("129:114", "value", "PrimitiveInt"),
        # frame count follows the reference clip: the slice length is the knob
        "duration": T("692", "duration", "Video Slice"),
        "prompt_enhance": T("129:212", "value", "PrimitiveBoolean"),
        "image": T("200", "image", "LoadImage"),
        "video": T("199", "file", "LoadVideo"),
        "save_prefix": T("68", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129:704", "seed", "KSampler"),),
    lora_chain=LoraChain(
        head="129:195",
        consumers=(T("129:704", "model", "KSampler"),),
    ),
    constants={"prompt_enhance": False},
    notes="ltx-2.3-22b-dev-fp8 + distilled-1.1 LoRA + union-control IC-LoRA / MoGe 深度",
)



# --------------------------------------------------------------------------
# video: workflow/video/wan/*.json
# --------------------------------------------------------------------------

#: 尺の選択肢（テンプレートの Duration コンボと同じ並び）。番号がそのまま
#: WanDancerPadKeyframesList の num_segments（= 5 秒単位のセグメント数）になる。
WAN_DURATIONS: tuple[str, ...] = ("5", "10", "15", "20", "25", "30")

WAN_DANCER = WorkflowSpec(
    id="wan_dancer",
    label="画像+音声→ダンス動画 (Wan Dancer)",
    kind="video",
    family="wan",
    relpath="video/wan/wan_dancer.json",
    output_node="699",
    requires=("image", "audio"),
    description=(
        "開始フレーム画像と音楽ファイルから、その曲に合わせて踊る動画を生成する"
        "（Wan 2.2 WanDancerVideo）。渡した音声がそのままクリップの音声トラックに"
        "なり、映像はビートに合わせて踊る。プロンプトは自由記述ではなく"
        "「踊りの種類」「動きの大きさ」の選択で決まり、尺は音声の長さに自動で"
        "合わせる（5〜30 秒）。"
    ),
    audio_role=(
        "指定した音声ファイルがクリップの音声トラックそのものになる"
        "（`audio_path` 必須）。踊りはこの曲に合わせて付くので、"
        "リズムのはっきりした曲を渡す。尺も既定ではこの音声の長さに合わせる。"
    ),
    prompt_hint=(
        "This workflow builds its own Chinese prompt from the `dance_style` and"
        " `motion_amplitude` selections, so **`video_prompt` is optional** —"
        " leave it out unless the user asked for something the two selections"
        " cannot express. When you do set it, write the Wan-style Chinese"
        " template string; keep the `<dance style>` placeholder in it if you"
        " want the selected dance to be substituted"
        ' (e.g. "一个人正在跳舞，舞蹈种类是<dance style>，在霓虹灯的舞台上").'
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    prompt_required=False,
    inject={
        # Global 側のテンプレ文（<dance style> が選択値に置換される）
        "prompt": T("696:685", "string", "StringReplace"),
        "negative": T("696:629", "text", "CLIPTextEncode"),
        "width": T("696:398", "value", "PrimitiveInt"),
        "height": T("696:400", "value", "PrimitiveInt"),
        "image": T("547", "image", "LoadImage"),
        "audio": T("548", "audio", "LoadAudio"),
        "save_prefix": T("699", "filename_prefix", "SaveVideo"),
    },
    selects={
        "dance_style": SelectSpec(
            label="踊りの種類",
            choices=(
                "Chinese Classic Dance 古典舞",
                "K-Pop 韩舞",
                "Street Dance 街舞",
                "Latin Dance 拉丁舞",
                "Tap Dance 踢踏舞",
            ),
            target=T("696:695", "choice", "CustomCombo"),
            default="K-Pop 韩舞",
            hint="プロンプトの <dance style> に入る踊りの種類。",
        ),
        "motion_amplitude": SelectSpec(
            label="動きの大きさ",
            choices=("low 低", "medium 中等", "high 高", "max 最大"),
            target=T("696:694", "choice", "CustomCombo"),
            default="medium 中等",
            hint="大きいほど激しく動くが、破綻もしやすい。",
        ),
        "duration": SelectSpec(
            label="尺（秒）",
            choices=WAN_DURATIONS,
            target=T("696:700", "choice", "CustomCombo"),
            # 音声の長さが測れなかったときの落としどころ（真ん中）
            default="15",
            # 音声もこの秒数で切る（既定の 25 秒固定だと曲が途中で切れる）
            numeric_target=T("696:494", "duration", "TrimAudioDuration"),
            auto="audio_duration",
            hint="省略すると音声の長さに合わせて 5〜30 秒から自動で決める。",
        ),
    },
    seeds=(
        T("696:654", "noise_seed", "RandomNoise"),
        T("696:667", "noise_seed", "SamplerCustom"),
    ),
    notes=(
        "wan2.2 global/local の 2 段 UNet + lightx2v LoRA / 既定 720x1280 /"
        " ユーザー LoRA を挿すチェーンは持たない"
    ),
)


# --------------------------------------------------------------------------
# video: kie.ai（Google Veo 3.1、SPEC §5.2 / issue #17）
# --------------------------------------------------------------------------
#
# ここから下はテンプレートを持たない**外部 API のワークフロー**。ComfyUI の
# グラフの代わりに :class:`KieTask` が「どのモデルに何を渡すか」を宣言し、実際の
# 組み立ては :mod:`app.kie` が行う。Veo は kie.ai の**旧専用系 API**
# （``/api/v1/veo/generate``）で、モデル名は 3.1 になっても旧名のまま
# （``veo3`` = Quality / ``veo3_fast`` = Fast）。
#
# 入力画像は 1 枚なら開始フレーム、2 枚なら「最初と最後のフレーム」になる
# （``generationType`` は :class:`app.kie.VeoTaskApi` が枚数から決める）。
# 音声はモデルが映像と一緒に生成するので、音声入力は取らない。
#
# **素材参照生成**（``REFERENCE_2_VIDEO``、issue #26）は同じ ``imageUrls`` に
# 参照画像を 1〜3 枚載せる別モードで、開始 / 最終フレームとは API 側で排他。
# 排他のモードを 1 つのマニフェストに同居させると「宣言はしているが組み合わせに
# よっては使えない」入力ができてしまうので、**ワークフローそのものを分けて**
# 宣言する（:data:`VEO3_1_FAST_REF`）。分けたことで ``generationType`` は
# 「参照素材があるときだけ切り替える」特別扱いではなく :attr:`KieTask.constants`
# の固定値になり、8 秒固定も ``duration`` の選択肢を 1 つだけ持つ
# :class:`SelectSpec` でそのまま表現できる。
# API 側の制約で素材参照生成が使えるのは **Fast / Lite のみ**なので、参照専用の
# バリアントも Fast にだけ用意する。

#: Veo の縦横比（``Auto`` は 1080p/4K が使えないので出さない）
VEO_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16")
#: Veo の尺（秒）。1080p は 8 秒生成のときだけ用意される。
VEO_DURATIONS: tuple[str, ...] = ("4", "6", "8")
#: 素材参照生成の ``generationType``（参照画像が入っているときだけ送る）
VEO_REFERENCE_TYPE = "REFERENCE_2_VIDEO"
#: 素材参照生成で受け取れる参照画像の枚数（API の上限そのまま）
VEO_REFERENCE_IMAGES = 3
#: 素材参照生成で固定される尺（API 側が 8 秒しか作れない）
VEO_REFERENCE_DURATION = "8"
#: 生成解像度。``4k`` は generate API 自体が受け取る（生成後の追加取得
#: （``POST /veo/get-4k-video``）とは別の経路）。ただし 8 秒生成のときだけで、
#: しかも高価なので既定にはしない。
VEO_RESOLUTIONS: tuple[str, ...] = ("720p", "1080p", "4k")

#: プロンプトの書き方（Fast / Quality で同じ。モデルの違いは品質と値段だけ）
VEO_PROMPT_HINT = (
    "One shot, one scene, one camera move. Write 3-6 English sentences"
    " (100-150 words) in this order: composition / shot size, subject, action,"
    " scene, **one** camera motion, lens & focus, style & light."
    " Veo generates the **sound with the picture**: name the ambience and the"
    " sound effects, and put spoken lines in quotes with the speaker and the"
    " delivery (`The woman says softly: \"...\"`) — 1-2 short lines fit in"
    " 8 seconds. Add `(no subtitles)` when no burnt-in captions are wanted."
    " Never write what you do *not* want inside the description; list it after"
    " the description as `Negative: cartoon, blurry, distorted hands, text,"
    " watermark`. With a start frame, do not re-describe what the picture"
    " already shows — write how it moves, what happens next and how it sounds."
)

#: プロンプトの書き方（素材参照生成のバリアント）。参照画像が見た目を決めるので、
#: 「素材が写しているもの」ではなく演出だけを書かせる。
VEO_REFERENCE_PROMPT_HINT = (
    "The 1-3 `reference_images` carry identity and look (face, wardrobe, prop),"
    " so **do not describe what they already show** — spend the text on"
    " direction. Write 3-6 English sentences (100-150 words): what happens, in"
    " which scene and light, with **one** camera motion, lens & focus, style."
    " Refer to the material in words the model can attach (`the woman from the"
    " reference images`), never by file name."
    " Veo generates the **sound with the picture**: name the ambience and the"
    " sound effects, and put spoken lines in quotes with the speaker and the"
    " delivery (`The woman says softly: \"...\"`) — 1-2 short lines fit in"
    " 8 seconds. Add `(no subtitles)` when no burnt-in captions are wanted."
    " Never write what you do *not* want inside the description; list it after"
    " the description as `Negative: cartoon, blurry, distorted hands, text,"
    " watermark`."
)

#: 画像入力の説明（カタログ・フォームの案内に使う共通文）
_VEO_INPUTS = (
    "画像は任意で、1 枚渡すと開始フレーム、`end_image` も一緒に渡すと"
    "「最初と最後のフレーム」の補間（flf2v）になる。"
)

#: 縦横比・解像度は素材参照生成でも同じ（尺だけが 8 秒に固定される）
VEO_ASPECT_SELECT = SelectSpec(
    label="縦横比",
    choices=VEO_ASPECT_RATIOS,
    default="16:9",
    hint="16:9 は横長、9:16 は縦長。",
)
VEO_RESOLUTION_SELECT = SelectSpec(
    label="解像度",
    choices=VEO_RESOLUTIONS,
    default="720p",
    hint="1080p・4k は尺 8 秒のときのみ。4k は高価なので仕上げのカットだけに使う。",
)


def _veo_spec(
    spec_id: str,
    label: str,
    model: str,
    credits: float,
    description: str,
) -> WorkflowSpec:
    """Veo の 1 モデル分のマニフェスト（Fast / Quality は宣言がほぼ同じ）。

    開始 / 最終フレームで作る通常の生成だけを宣言する。素材参照生成は API 側で
    このモードと排他なので、同じ宣言に混ぜず :data:`VEO3_1_FAST_REF` として
    別のワークフローにしてある。
    """
    return WorkflowSpec(
        id=spec_id,
        label=label,
        kind="video",
        family="veo",
        backend="kie",
        description=description,
        prompt_hint=VEO_PROMPT_HINT,
        accepts_start_image=True,
        image_label="開始フレーム（任意）",
        kie=KieTask(
            model=model,
            api="veo",
            fields={
                "prompt": "prompt",
                # 宣言順がそのまま imageUrls の並び（1 枚目 = 開始フレーム）
                "image": "imageUrls",
                "end_image": "imageUrls",
                f"{KIE_SELECT_PREFIX}aspect_ratio": "aspect_ratio",
                f"{KIE_SELECT_PREFIX}duration": "duration",
                f"{KIE_SELECT_PREFIX}resolution": "resolution",
            },
            # 既定値がドキュメント内で食い違うので明示する（英語プロンプトを
            # そのまま使わせたいので翻訳は有効のままでよい）
            constants={"enableTranslation": True},
            list_keys=("imageUrls",),
            credits=credits,
        ),
        selects={
            "aspect_ratio": VEO_ASPECT_SELECT,
            "duration": SelectSpec(
                label="尺（秒）",
                choices=VEO_DURATIONS,
                default="8",
                hint="1080p は 8 秒のときだけ生成できる。",
            ),
            "resolution": VEO_RESOLUTION_SELECT,
        },
        notes=(
            "kie.ai 経由 / 音声つき / SynthID 透かしが必ず入る /"
            " 4k は生成時に選べる（8 秒のときのみ・高価） /"
            " 生成後に履歴から +7 秒の延長と 1080P 版の取得ができる /"
            " 4K の追加取得（get-4k-video）は未対応 /"
            " 素材参照生成は `veo3_1_fast_ref`（別ワークフロー）"
        ),
    )


VEO3_1_FAST = _veo_spec(
    "veo3_1_fast",
    "Veo 3.1 Fast（音声つき・外部 API）",
    "veo3_fast",
    60.0,
    "kie.ai 経由の Google Veo 3.1 Fast。音声（環境音・効果音・セリフ）まで"
    "モデルが同時に生成する 4〜8 秒のクリップで、ふだんの試し撮り・量産用。"
    f"{_VEO_INPUTS}素材で見た目を指定したいときは `veo3_1_fast_ref` を使う。"
    "外部 API なので LoRA は使えない。",
)

VEO3_1_QUALITY = _veo_spec(
    "veo3_1_quality",
    "Veo 3.1 Quality（音声つき・外部 API）",
    "veo3",
    250.0,
    "kie.ai 経由の Google Veo 3.1（Quality）。Fast と同じ使い方で品質が高く、"
    "そのぶん高価（Fast の約 4 倍）。本番に載せるカットだけに使う。"
    f"{_VEO_INPUTS}外部 API なので LoRA は使えない。",
)

#: Veo 3.1 Fast の**素材参照生成**（``REFERENCE_2_VIDEO``）。通常の生成とは API
#: 側で排他なので、開始 / 最終フレームを**宣言そのものから外した**別ワークフロー
#: にしてある。おかげで ``generationType`` は常に載る固定値、8 秒固定は選択肢が
#: 1 つだけの ``duration`` として素直に書ける。
VEO3_1_FAST_REF = WorkflowSpec(
    id="veo3_1_fast_ref",
    label="Veo 3.1 Fast 素材参照（音声つき・外部 API）",
    kind="video",
    family="veo",
    backend="kie",
    description=(
        "kie.ai 経由の Google Veo 3.1 Fast の**素材参照生成**。"
        f"**参照画像**（`reference_images` 最大 {VEO_REFERENCE_IMAGES} 枚）で"
        "人物・衣装・小道具の見た目を指定し、プロンプトには演出だけを書く。"
        "**開始フレームは受け取らない**（API 側で通常の生成と排他のモード）ので "
        f"`mode: \"i2v\"` 専用で、尺は {VEO_REFERENCE_DURATION} 秒固定。"
        "外部 API なので LoRA は使えない。"
    ),
    prompt_hint=VEO_REFERENCE_PROMPT_HINT,
    # 素材参照生成に開始フレームは渡せない（＝ full の 2 段目にもなれない）
    accepts_start_image=False,
    multi_inputs={"reference_images": VEO_REFERENCE_IMAGES},
    kie=KieTask(
        model="veo3_fast",
        api="veo",
        fields={
            "prompt": "prompt",
            # 参照画像も通常の生成と同じ配列（載せる中身の意味が違うだけ）
            "reference_images": "imageUrls",
            f"{KIE_SELECT_PREFIX}aspect_ratio": "aspect_ratio",
            f"{KIE_SELECT_PREFIX}duration": "duration",
            f"{KIE_SELECT_PREFIX}resolution": "resolution",
        },
        # 素材参照生成は枚数から判別できない（imageUrls は開始フレームと共通）
        # ので、このワークフローでは常に generationType を明示して送る。
        constants={
            "enableTranslation": True,
            "generationType": VEO_REFERENCE_TYPE,
        },
        list_keys=("imageUrls",),
        credits=60.0,
    ),
    selects={
        "aspect_ratio": VEO_ASPECT_SELECT,
        "duration": SelectSpec(
            label="尺（秒）",
            choices=(VEO_REFERENCE_DURATION,),
            default=VEO_REFERENCE_DURATION,
            hint=f"素材参照生成は {VEO_REFERENCE_DURATION} 秒固定（API 側の制約）。",
        ),
        "resolution": VEO_RESOLUTION_SELECT,
    },
    notes=(
        "kie.ai 経由 / 音声つき / SynthID 透かしが必ず入る /"
        f" 参照画像は {VEO_REFERENCE_IMAGES} 枚まで /"
        f" 尺は {VEO_REFERENCE_DURATION} 秒固定・開始フレームは受け取らない /"
        " 生成後に履歴から +7 秒の延長と 1080P 版の取得ができる /"
        " 開始フレームから作るなら `veo3_1_fast`"
    ),
)


# --------------------------------------------------------------------------
# video: kie.ai（Kling 3.0、SPEC §5.2 / issue #18）
# --------------------------------------------------------------------------
#
# Kling は Veo と違って **Market 系（統一 API）** なので、系統は既定の
# ``market`` のまま（``POST /api/v1/jobs/createTask`` に ``{"model", "input"}``）。
# t2v / i2v はモデルが分かれておらず、``image_urls`` を入れるかどうかだけで
# 決まる（1 枚 = 開始フレーム、2 枚 = 開始 + 最終フレーム）。
#
# 注意すべき癖が 2 つある:
#
# - **``duration`` は文字列**（``"3"``〜``"15"``）。Veo の ``duration`` は整数
#   なので、同じ「尺」でも型が逆になる。選択式フィールドの値は文字列で届くので
#   ここでは何も変換しない（Market 系の ``create_body`` も素通し）
# - **``sound`` は真偽値**。選択式の文字列を :attr:`KieTask.bool_keys` で
#   ``bool`` に直してから送る
#
# ``negative_prompt`` / ``cfg`` / ``camera_control`` / ``seed`` は kie.ai 経由の
# Kling には無いので宣言しない（すべてプロンプト本文で制御する）。
#
# 第 2 段で足した 2 つの構造化パラメータ:
#
# - **マルチショット**（:class:`MultiShotSpec`）。``multi_shots: true`` と
#   ``multi_prompt: [{"prompt", "duration"}]`` の組で、1 タスクに最大 5 ショット。
#   このとき**トップレベルの ``prompt`` は送らない**（:func:`app.kie.task_values`）。
#   1 本の ``video_prompt`` で作る通常の生成とは書き方も送るキーも別物なので、
#   **ワークフローを分けて**宣言する（:data:`KLING3_MULTISHOT`）。分けたことで
#   「ショット割りのときだけ ``sound`` の既定が変わる」といった特別扱いが要らず、
#   マルチショット側の ``sound`` を既定 true にしておけば済む
# - **Elements**（:class:`ElementsSpec`）。``kling_elements`` は
#   ``[{"name", "description", "element_input_urls"}]`` で、参照画像は
#   :func:`app.jobs._kie_uploads` が 1 枚ずつ URL 化する。プロンプトからは
#   ``@要素名`` で呼び、**1 参照が 37 文字**を消費する。こちらは開始フレームとも
#   ショット割りとも併用できるので、両方のワークフローが宣言する
#
# Turbo 系（``kling/v3-turbo-text-to-video`` / ``-image-to-video``）は未対応。

#: Kling の生成モード（解像度と値段が変わる）
KLING_MODES: tuple[str, ...] = ("std", "pro", "4K")
#: Kling の尺（秒）。**API には文字列で渡す**（``"3"``〜``"15"``）。
KLING_DURATIONS: tuple[str, ...] = tuple(str(second) for second in range(3, 16))
#: Kling の縦横比（画像を渡したときは画像に従うので無視される）
KLING_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16", "1:1")
#: ネイティブ音声の ON / OFF（``sound`` は真偽値なので :attr:`KieTask.bool_keys`）
KLING_SOUND: tuple[str, ...] = ("false", "true")

#: プロンプトの長さの上限（kie.ai の Kling 3.0）。マルチショットの 1 ショットも
#: 同じ上限（``multi_prompt[].prompt`` も 500 文字まで）。
KLING_MAX_PROMPT_CHARS = 500

#: マルチショット（``multi_shots`` / ``multi_prompt``）の宣言
KLING_MULTI_SHOT = MultiShotSpec(max_shots=5, min_duration=1, max_duration=12)

#: Elements（``kling_elements``）の宣言
KLING_ELEMENTS = ElementsSpec(
    max_elements=3, min_images=2, max_images=4, reference_chars=37
)

#: 1 ショットの書き方（通常の生成とマルチショットの 1 ショット目で共通）
_KLING_SHOT_HINT = (
    "Order: **camera move first**, then scene / subject, action, mood &"
    " lighting, style. Start with the camera (`Slow dolly push forward, ...`)"
    " and use exactly one move. Fix the subject's identity (age, hair,"
    " wardrobe) in the first clause and refer back to it with the *same* words"
    " — pronouns and synonyms make the character drift. One scene, one action."
)

#: Elements の書き方（両方のワークフローで共通）
_KLING_ELEMENTS_HINT = (
    " **Elements** (`kling_elements`, up to 3, each with 2-4 reference images)"
    " are named casts you call with `@name` in the text — one `@name` costs"
    " **37 characters** of the 500, and a name you did not declare is rejected."
)

KLING_PROMPT_HINT = (
    "**Hard limit: 500 characters** — the API rejects anything longer, so write"
    " one dense paragraph, not an essay. " + _KLING_SHOT_HINT +
    " With a start frame, treat the picture as the anchor: write only how it"
    " starts moving and what changes, never re-describe the composition."
    " With `sound` on, label the speaker before the line and describe the voice"
    " (`Woman (raspy, low voice): \"...\"`); Japanese dialogue is lip-synced."
    " There is no negative prompt parameter: write what you do want, and put"
    " unwanted elements as `no text overlays, no camera shake` inside the text."
    + _KLING_ELEMENTS_HINT
)

#: マルチショット版のプロンプトの書き方。本文は 1 ショットずつ ``multi_shots`` に
#: 書き、``video_prompt`` は空のまま（API にも送られない）。
KLING_MULTISHOT_PROMPT_HINT = (
    "This workflow takes **shots, not one take**: write every paragraph into"
    " `multi_shots` (up to 5 shots of 1-12 seconds) and leave `video_prompt`"
    " empty — a job without shots is rejected."
    " **Each shot is limited to 500 characters on its own.** " + _KLING_SHOT_HINT
    + " Repeat the identity wording **verbatim in every shot** (same age, hair,"
    " wardrobe) and keep the location and the light consistent: that wording is"
    " the only thing holding the character together across the cuts."
    " Shots are cuts, not one long move — never carry a camera move across two"
    " shots, give each one its own."
    " `sound` is on by default here, so name the ambience per shot; label the"
    " speaker before a line and describe the voice (`Woman (raspy, low voice):"
    " \"...\"`). There is no negative prompt parameter: write what you do want,"
    " and put unwanted elements as `no text overlays` inside the text."
    + _KLING_ELEMENTS_HINT
)

#: 開始フレーム・尺・モード・縦横比は 2 本で同じ（違うのは ``sound`` の既定だけ）
KLING_MODE_SELECT = SelectSpec(
    label="モード",
    choices=KLING_MODES,
    default="pro",
    hint="std は 720p、pro は 1080p、4K は 4K（pro の約 4 倍の値段）。",
)
KLING_DURATION_SELECT = SelectSpec(
    label="尺（秒）",
    choices=KLING_DURATIONS,
    default="5",
    hint="3〜15 秒。長いほど比例して高い。",
)
KLING_ASPECT_SELECT = SelectSpec(
    label="縦横比",
    choices=KLING_ASPECT_RATIOS,
    default="16:9",
    hint="開始フレーム画像を渡したときは画像の縦横比が優先される。",
)

#: 2 本で共通の入力（``prompt`` / 開始 + 最終フレーム / Elements / 選択式）
_KLING_FIELDS: dict[str, str] = {
    "prompt": "prompt",
    # 宣言順がそのまま image_urls の並び（1 枚目 = 開始フレーム）
    "image": "image_urls",
    "end_image": "image_urls",
    # Elements（参照画像は _kie_uploads が element_input_urls に直す）
    "kling_elements": "kling_elements",
    f"{KIE_SELECT_PREFIX}mode": "mode",
    f"{KIE_SELECT_PREFIX}duration": "duration",
    f"{KIE_SELECT_PREFIX}aspect_ratio": "aspect_ratio",
    f"{KIE_SELECT_PREFIX}sound": "sound",
}

KLING3_VIDEO = WorkflowSpec(
    id="kling3_video",
    label="Kling 3.0（音声つき・外部 API）",
    kind="video",
    family="kling",
    backend="kie",
    description=(
        "kie.ai 経由の Kling 3.0（t2v / i2v 統合）。人物の動きと実写寄りの絵に強く、"
        "3〜15 秒と尺が長い。`sound` を on にすると環境音・効果音・セリフ"
        "（日本語のリップシンクつき）まで同時に生成する。画像は任意で、1 枚渡すと"
        "開始フレーム、`end_image` も渡すと開始 + 最終フレームの補間になる。"
        "**プロンプトは 500 文字まで**。`kling_elements` で `@要素名` 参照の"
        "キャラクター固定ができる。ショット割りで作るなら `kling3_multishot`。"
        "外部 API なので LoRA は使えない。"
    ),
    prompt_hint=KLING_PROMPT_HINT,
    max_prompt_chars=KLING_MAX_PROMPT_CHARS,
    accepts_start_image=True,
    image_label="開始フレーム（任意）",
    elements=KLING_ELEMENTS,
    kie=KieTask(
        model="kling-3.0/video",
        # Market 系（統一 API）なので系統は既定のまま
        fields=dict(_KLING_FIELDS),
        list_keys=("image_urls",),
        bool_keys=("sound",),
        # pro / 5 秒 / 音声なしの概算（$0.09/秒 = 90 credits）
        credits=90.0,
    ),
    selects={
        "mode": KLING_MODE_SELECT,
        "duration": KLING_DURATION_SELECT,
        "aspect_ratio": KLING_ASPECT_SELECT,
        "sound": SelectSpec(
            label="音声を生成",
            choices=KLING_SOUND,
            default="false",
            hint="true で環境音・効果音・セリフを同時生成（そのぶん高い）。",
        ),
    },
    notes=(
        "kie.ai 経由 / プロンプトは 500 文字まで / ネガティブプロンプト・seed・"
        "カメラ制御パラメータは無い（本文で指定） / Elements は最大 3 要素"
        "（各 2〜4 枚、`@要素名` 1 参照 = 37 文字） / ショット割りは"
        " `kling3_multishot`（別ワークフロー） / Turbo 系は未対応"
    ),
)

#: Kling 3.0 の**ショット割り**専用ワークフロー。``multi_shots`` があるときは
#: トップレベルの ``prompt`` を送らない（API 仕様）ので、1 本の ``video_prompt``
#: で作る :data:`KLING3_VIDEO` とは入力の形そのものが違う。同居させると
#: 「どちらかにしか意味の無い欄」が両方出てしまうため、宣言ごと分けてある。
KLING3_MULTISHOT = WorkflowSpec(
    id="kling3_multishot",
    label="Kling 3.0 マルチショット（音声つき・外部 API）",
    kind="video",
    family="kling",
    backend="kie",
    description=(
        "kie.ai 経由の Kling 3.0 を**ショット割り**で使うワークフロー。"
        "`multi_shots` に最大 5 ショット（各 1〜12 秒）を並べると 1 本の動画に"
        "つながる。本文はショット側に書くので **`video_prompt` は空のまま**"
        "（指定すると 422）、**1 ショットが 500 文字まで**。音声は既定 ON で、"
        "画像は任意（1 枚で開始フレーム、`end_image` も渡すと最終フレーム）。"
        "`kling_elements` の `@要素名` はショットをまたいで同じ人物を保つのに効く。"
        "1 カットで作るなら `kling3_video`。外部 API なので LoRA は使えない。"
    ),
    prompt_hint=KLING_MULTISHOT_PROMPT_HINT,
    max_prompt_chars=KLING_MAX_PROMPT_CHARS,
    accepts_start_image=True,
    image_label="開始フレーム（任意）",
    # 本文はショット側にあるので、トップレベルの `video_prompt` は必須ではない
    # （書かれていたら `models.multi_shot_problem` が 422 で断る）
    prompt_required=False,
    multi_shot=KLING_MULTI_SHOT,
    elements=KLING_ELEMENTS,
    kie=KieTask(
        model="kling-3.0/video",
        fields={
            **_KLING_FIELDS,
            # ショット割り（prompt は task_values が空にするので落ちる）
            "multi_shots": "multi_shots",
            "multi_prompt": "multi_prompt",
        },
        list_keys=("image_urls",),
        bool_keys=("sound", "multi_shots"),
        # pro / 5 秒 / 音声ありの概算（ショット割りは音つきが前提）
        credits=90.0,
    ),
    selects={
        "mode": KLING_MODE_SELECT,
        "duration": KLING_DURATION_SELECT,
        "aspect_ratio": KLING_ASPECT_SELECT,
        "sound": SelectSpec(
            label="音声を生成",
            choices=KLING_SOUND,
            # ショット割りは音つき前提の機能なので、こちらは既定 ON
            default="true",
            hint="ショット割りは音つきが前提なので既定 ON。"
            "false にすると無音の映像だけを作る。",
        ),
    },
    notes=(
        "kie.ai 経由 / ショット割り専用（`multi_shots` が必須・`video_prompt` は"
        "書かない） / 最大 5 ショット・各 1〜12 秒・1 ショット 500 文字まで /"
        " 音声は既定 ON / Elements は最大 3 要素（各 2〜4 枚、`@要素名` 1 参照 ="
        " 37 文字） / 1 カットで作るなら `kling3_video`"
    ),
)


# --------------------------------------------------------------------------
# video: kie.ai（ByteDance Seedance 2、SPEC §5.2 / issue #19）
# --------------------------------------------------------------------------
#
# Seedance も Kling と同じ **Market 系（統一 API）** なので系統は既定の ``market``
# のまま。バリアント（2.0 / 2.0 Fast / 2.0 Mini）の違いは**モデル名と使える解像度
# だけ**で、宣言の形はまったく同じなので :func:`_seedance_spec` で 1 つにまとめて
# ある。2.5 が kie.ai に来たら（今は Coming Soon）エントリを 1 つ足すだけでよい。
#
# Kling との違いで気をつける点:
#
# - **開始 / 最終フレームはキーが別**（``first_frame_url`` / ``last_frame_url``）。
#   Kling の ``image_urls`` のような 1 つの配列ではないので :attr:`KieTask.list_keys`
#   は使わず、論理入力ごとに別のキーを宣言する
# - **``duration`` は整数**（4〜15）。Kling は文字列なので型が逆で、選択式の値は
#   文字列で届くため :attr:`KieTask.int_keys` で ``int`` に直してから送る
# - **``generate_audio`` は真偽値**で、しかも**既定が true**（Kling の ``sound`` は
#   既定 false）
#
# 2 系に seed / camera_fixed は無い（カメラ固定も再現性もプロンプト側の仕事）。
#
# **マルチモーダル参照**（``reference_image_urls`` / ``reference_video_urls`` /
# ``reference_audio_urls``）は :attr:`WorkflowSpec.multi_inputs` で宣言する
# 「複数ファイル -> URL の配列」の入力。API 側では
# **「先頭フレーム i2v」と「参照モード」が相互排他**なので、1 つのマニフェストに
# 両方を宣言せず、**バリアントごとに 2 本**（フレーム版 / 参照版 ``*_ref``）に
# 分けてある（:func:`_seedance_spec` の ``references``）。参照版は
# ``accepts_start_image=False`` で開始 / 最終フレームの受け取り口そのものを
# 持たないので、``full``（画像ステージが開始フレームを作る）でも選べない。

#: Seedance 2.0 の解像度（Mini は 720p まで）
SEEDANCE_RESOLUTIONS: tuple[str, ...] = ("480p", "720p", "1080p", "4k")
#: Seedance 2.0 Fast の解像度（Mini と同じく 720p まで）
SEEDANCE_FAST_RESOLUTIONS: tuple[str, ...] = ("480p", "720p")
#: Seedance 2.0 Mini の解像度
SEEDANCE_MINI_RESOLUTIONS: tuple[str, ...] = ("480p", "720p")
#: Seedance の尺（秒）。**API には整数で渡す**（:attr:`KieTask.int_keys`）。
SEEDANCE_DURATIONS: tuple[str, ...] = tuple(str(second) for second in range(4, 16))
#: Seedance の縦横比（``adaptive`` は入力画像に追従する）
SEEDANCE_ASPECT_RATIOS: tuple[str, ...] = (
    "16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive",
)
#: ネイティブ音声の ON / OFF（**既定は ON**）
SEEDANCE_AUDIO: tuple[str, ...] = ("false", "true")
#: kie.ai 側の NSFW フィルタの ON / OFF（**既定は OFF**）
SEEDANCE_NSFW_CHECKER: tuple[str, ...] = ("false", "true")

#: マルチモーダル参照で受け取れる件数（論理名 -> 上限）。API 側の上限そのまま。
SEEDANCE_MULTI_INPUTS: dict[str, int] = {
    "reference_images": 9,
    "reference_videos": 3,
    "reference_audios": 3,
}

#: プロンプトの長さの上限（kie.ai の Seedance 2 系）
SEEDANCE_MAX_PROMPT_CHARS = 20000

#: 演出の書き方（フレーム版・参照版で共通の骨格）
_SEEDANCE_DIRECTION_HINT = (
    "Write like a director: one dense English paragraph of 60-100 words in the"
    " order **subject (concrete looks) → action (verb + intensity) → setting"
    " (light, atmosphere) → camera (exactly one move) → style → what to avoid**."
    " The **lighting sentence matters most** (golden hour, rim light, neon"
    " spill, backlit silhouette) — piles of vague adjectives (amazing, epic,"
    ' bare "cinematic") make the result worse.'
    " Pick **one** camera move out of push-in / pull-out / pan / tracking /"
    " orbit / aerial / handheld / fixed and give it a rhythm word (slow,"
    " smooth, gentle); keep **the subject's motion and the camera's motion in"
    " separate sentences**. For characters, end with a short negative clause"
    ' such as "avoid jitter and bent limbs".'
)

#: プロンプトの書き方（フレーム版。バリアント間で同じ。違うのは品質と値段と
#: 最大解像度だけ）
SEEDANCE_PROMPT_HINT = (
    _SEEDANCE_DIRECTION_HINT
    + " With a start frame, do not re-describe what the picture already shows —"
    " write how it moves. With `end_image` as well, describe the *transition*"
    " that lands exactly on that last frame."
)

#: プロンプトの書き方（参照版）。素材が見た目を決めるので、演出だけを書かせる。
SEEDANCE_REFERENCE_PROMPT_HINT = (
    "The material carries the look: `reference_images` pin **identity and"
    " consistency** (the same face, wardrobe, prop across shots), a"
    " `reference_videos` clip is the **motion to imitate** (rhythm, camera"
    " behaviour), `reference_audios` set the **mood / musical feel**."
    " **Do not describe what the references already show** — spend the text on"
    " direction, and refer to the material in words the model can attach"
    " (`the woman from the reference images`), never by file name. "
    + _SEEDANCE_DIRECTION_HINT
)

#: 画像入力の説明（フレーム版のカタログ・フォームの案内に使う共通文）
_SEEDANCE_INPUTS = (
    "画像は任意で、1 枚渡すと開始フレーム（`first_frame_url`）、`end_image` も"
    "渡すと最終フレーム（`last_frame_url`）になる。"
    "素材で見た目・動き・ムードを指定したいときは参照版（`*_ref`）を使う。"
)

#: 参照素材の説明（参照版のカタログ・フォームの案内に使う共通文）
_SEEDANCE_REFERENCE_INPUTS = (
    "**マルチモーダル参照**（`reference_images` 最大 9 枚 / `reference_videos`"
    " 最大 3 本 / `reference_audios` 最大 3 本）で一貫性・動きのお手本・ムードを"
    "素材から指定する。**開始フレームは受け取らない**（API 側で先頭フレーム "
    "i2v と排他のモード）ので `mode: \"i2v\"` 専用。開始フレームから作るなら"
    "フレーム版を使う。"
)


def _seedance_spec(
    spec_id: str,
    label: str,
    model: str,
    resolutions: tuple[str, ...],
    credits: float,
    description: str,
    *,
    references: bool = False,
) -> WorkflowSpec:
    """Seedance 2 系の 1 バリアント分のマニフェスト。

    バリアント間で違うのは **モデル名と解像度の選択肢と値段だけ**なので、
    2.5 や Fast を足すときもここを呼ぶエントリを 1 つ書けば済む。

    ``references`` を立てると**参照版**（``*_ref``）になる: 開始 / 最終フレームの
    受け取り口を持たず、代わりにマルチモーダル参照を宣言する。API 側で 2 つの
    モードが排他なので、宣言のほうを分けて「使える入力だけが並ぶ」ようにしてある。
    """
    return WorkflowSpec(
        id=spec_id,
        label=label,
        kind="video",
        family="seedance",
        backend="kie",
        description=description,
        prompt_hint=(
            SEEDANCE_REFERENCE_PROMPT_HINT if references else SEEDANCE_PROMPT_HINT
        ),
        max_prompt_chars=SEEDANCE_MAX_PROMPT_CHARS,
        # 参照版は開始フレームを受け取らない（＝ full の 2 段目にもなれない）
        accepts_start_image=not references,
        image_label="開始フレーム（任意）",
        multi_inputs=dict(SEEDANCE_MULTI_INPUTS) if references else {},
        kie=KieTask(
            model=model,
            # Market 系（統一 API）なので系統は既定のまま
            fields={
                "prompt": "prompt",
                **(
                    {
                        # マルチモーダル参照: 複数ファイル -> URL の配列
                        "reference_images": "reference_image_urls",
                        "reference_videos": "reference_video_urls",
                        "reference_audios": "reference_audio_urls",
                    }
                    if references
                    else {
                        # Kling と違い開始 / 最終フレームはキーが別（配列ではない）
                        "image": "first_frame_url",
                        "end_image": "last_frame_url",
                    }
                ),
                f"{KIE_SELECT_PREFIX}resolution": "resolution",
                f"{KIE_SELECT_PREFIX}duration": "duration",
                f"{KIE_SELECT_PREFIX}aspect_ratio": "aspect_ratio",
                f"{KIE_SELECT_PREFIX}generate_audio": "generate_audio",
                f"{KIE_SELECT_PREFIX}nsfw_checker": "nsfw_checker",
            },
            bool_keys=("generate_audio", "nsfw_checker"),
            int_keys=("duration",),
            credits=credits,
        ),
        selects={
            "resolution": SelectSpec(
                label="解像度",
                choices=resolutions,
                default="720p",
                hint="高いほど比例して高価（秒単価で課金される）。",
            ),
            "duration": SelectSpec(
                label="尺（秒）",
                choices=SEEDANCE_DURATIONS,
                default="5",
                hint="4〜15 秒。長いほど比例して高い。",
            ),
            "aspect_ratio": SelectSpec(
                label="縦横比",
                choices=SEEDANCE_ASPECT_RATIOS,
                default="16:9",
                hint=(
                    "adaptive は参照画像の縦横比に合わせる。"
                    if references
                    else "adaptive は開始フレーム画像の縦横比に合わせる。"
                ),
            ),
            "generate_audio": SelectSpec(
                label="音声を生成",
                choices=SEEDANCE_AUDIO,
                default="true",
                hint="既定で ON。false にすると無音の映像だけを作る。",
            ),
            "nsfw_checker": SelectSpec(
                label="NSFW チェック",
                choices=SEEDANCE_NSFW_CHECKER,
                default="false",
                hint="false で kie.ai 側のフィルタ無効（既定）。true にすると"
                "フィルタが有効になり、際どい生成が弾かれる。",
            ),
        },
        notes=(
            "kie.ai 経由 / ネイティブ音声つき（既定 ON） / seed・カメラ固定の"
            "パラメータは無い（本文で指定） / 成果物 URL は約 24 時間で失効"
            + (
                " / マルチモーダル参照（参照画像 9 枚・参照動画 3 本・"
                "参照音声 3 本）専用で開始フレームは受け取らない"
                if references
                else " / 素材参照で作るなら `*_ref`（別ワークフロー）"
            )
        ),
    )


SEEDANCE2 = _seedance_spec(
    "seedance2",
    "Seedance 2.0（音声つき・外部 API）",
    "bytedance/seedance-2",
    SEEDANCE_RESOLUTIONS,
    # 720p / 5 秒の概算（$0.06/秒 = 60 credits）
    60.0,
    "kie.ai 経由の ByteDance Seedance 2.0。映像と音声を同時に生成する 4〜15 秒の"
    "クリップで、**4K まで**出せる 2 系の本命。720p で試作して 1080p / 4K で"
    f"仕上げる使い方を想定している。{_SEEDANCE_INPUTS}"
    "外部 API なので LoRA は使えない。",
)

SEEDANCE2_FAST = _seedance_spec(
    "seedance2_fast",
    "Seedance 2.0 Fast（音声つき・外部 API）",
    "bytedance/seedance-2-fast",
    SEEDANCE_FAST_RESOLUTIONS,
    # 720p / 5 秒の概算（$0.05/秒 = 50 credits）
    50.0,
    "kie.ai 経由の ByteDance Seedance 2.0 Fast。2.0 と同じ使い方で待ち時間が短く、"
    "480p / 720p まで。数を出して当たりを探す段階向けで、決まったカットは"
    f"2.0 で作り直す。{_SEEDANCE_INPUTS}外部 API なので LoRA は使えない。",
)

SEEDANCE2_MINI = _seedance_spec(
    "seedance2_mini",
    "Seedance 2.0 Mini（音声つき・外部 API）",
    "bytedance/seedance-2-mini",
    SEEDANCE_MINI_RESOLUTIONS,
    # 720p / 5 秒の概算（$0.04/秒 = 40 credits）
    40.0,
    "kie.ai 経由の ByteDance Seedance 2.0 Mini。2.0 と同じ使い方で最安・"
    "720p まで。試作と大量出し用で、決まったカットを 2.0 で作り直す。"
    f"{_SEEDANCE_INPUTS}外部 API なので LoRA は使えない。",
)

SEEDANCE2_REF = _seedance_spec(
    "seedance2_ref",
    "Seedance 2.0（素材参照・音声つき・外部 API）",
    "bytedance/seedance-2",
    SEEDANCE_RESOLUTIONS,
    60.0,
    "kie.ai 経由の ByteDance Seedance 2.0 の**素材参照モード**。"
    f"{_SEEDANCE_REFERENCE_INPUTS}**4K まで**出せる 2 系の本命で、"
    "外部 API なので LoRA は使えない。",
    references=True,
)

SEEDANCE2_FAST_REF = _seedance_spec(
    "seedance2_fast_ref",
    "Seedance 2.0 Fast（素材参照・音声つき・外部 API）",
    "bytedance/seedance-2-fast",
    SEEDANCE_FAST_RESOLUTIONS,
    50.0,
    "kie.ai 経由の ByteDance Seedance 2.0 Fast の**素材参照モード**。"
    f"{_SEEDANCE_REFERENCE_INPUTS}待ち時間が短く 480p / 720p まで。"
    "外部 API なので LoRA は使えない。",
    references=True,
)

SEEDANCE2_MINI_REF = _seedance_spec(
    "seedance2_mini_ref",
    "Seedance 2.0 Mini（素材参照・音声つき・外部 API）",
    "bytedance/seedance-2-mini",
    SEEDANCE_MINI_RESOLUTIONS,
    40.0,
    "kie.ai 経由の ByteDance Seedance 2.0 Mini の**素材参照モード**。"
    f"{_SEEDANCE_REFERENCE_INPUTS}最安・720p までで、大量出し用。"
    "外部 API なので LoRA は使えない。",
    references=True,
)


# --------------------------------------------------------------------------
# video: Grok Build CLI（サブスク枠、SPEC §5.3 / issue #22）
# --------------------------------------------------------------------------
#
# 画像版（:data:`GROK_IMAGINE`）と同じ CLI ラッパーを ``media="video"`` で使う
# 動画ワークフロー。渡せるのは自然文の指示だけなので、尺・解像度・縦横比はすべて
# **指示文に織り込む希望**であって、外部 API の ``input`` のような保証は無い。
#
# 画像版との違いは 3 つ:
#
# - **開始フレームを取れる**（i2v）。渡された画像は grok のメディア作業ディレクトリ
#   へコピーされ、指示文がファイル名で参照する（:func:`app.grok_media.stage_input`）
# - **音声も同時に生成する**（環境音・効果音・セリフ）。ia2v のような音声入力は
#   無いので、鳴らしたい音はプロンプト本文に書く
# - **尺・解像度・縦横比が選択式フィールド**（§3.1）。CLI が受け取るのは文字列
#   なので型変換（kie の ``int_keys`` / ``bool_keys``）にあたるものは要らない
#
# 上限は **10 秒 / 720p** から始める: モデル（video-1.5）の API は 15 秒 / 1080p
# まで受けるが、CLI 経由は「up to 10 seconds at 720p」という報道しか無く、一次情報
# が無い。実機で 15 秒が通ることを確かめたら選択肢を広げる。
# 枠は Chat / Imagine / Build 横断の共有プールで、動画の目安は ~10 本/日
# （SuperGrok、時期・地域で変動）。

#: Grok Imagine 動画の尺（秒）。CLI 経由の上限は未確定なので 10 秒から始める。
GROK_VIDEO_DURATIONS: tuple[str, ...] = tuple(str(second) for second in range(1, 11))
#: Grok Imagine 動画の解像度（CLI 経由は 720p まで、という報道に合わせる）
GROK_VIDEO_RESOLUTIONS: tuple[str, ...] = ("480p", "720p")
#: Grok Imagine 動画の縦横比（開始フレームを渡したときは画像の比が優先される）
GROK_VIDEO_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16", "1:1")

GROK_IMAGINE_VIDEO_PROMPT_HINT = (
    "One short English paragraph (2-4 sentences) covering subject → motion →"
    " camera → audio. **With a start frame, describe only what CHANGES** — the"
    " picture already carries composition, lighting and style, so re-describing"
    " it fights the image. The model renders sequentially, so the action you"
    " write first is the action the clip opens with; **one clip, one action**."
    " Name the sounds you want (`footsteps on gravel`, `muffled through"
    " glass`), and put spoken lines in quotes with the voice quality"
    " (`in a low, raspy voice: \"...\"`). Use concrete camera words"
    " (`locked static shot`, `slow push-in`, `tracking shot alongside`) —"
    " abstract ones (`cinematic`, `epic`) do nothing, and saying nothing about"
    " the camera gives a still one, which is the safest default. Intensity"
    " comes from strong verbs with adverbs (`crashing down with tremendous"
    " force`), never from adjectives piled on the subject."
)

GROK_IMAGINE_VIDEO = WorkflowSpec(
    id="grok_imagine_video",
    label="Grok Imagine 動画（サブスク CLI）",
    kind="video",
    family="grok-imagine",
    backend="grok_cli",
    description=(
        "Video through the official Grok Build CLI, on the SuperGrok / X"
        " Premium+ **subscription** quota (no metered API). One take of 1-10"
        " seconds with **native audio** (ambience, effects and spoken lines)"
        " generated together with the picture — there is no audio input, so"
        " write the sound into `video_prompt`. The start frame (`source_image`)"
        " is optional: with one it works as image-to-video (the picture is"
        " copied next to the CLI and referenced by name), without one as"
        " text-to-video, so it can be used for `mode: \"i2v\"` and as the second"
        " stage of `mode: \"full\"`. Duration, resolution and aspect ratio are"
        " job fields (`selects`) that are passed as *wishes* inside the"
        " instruction, so they are not guaranteed exactly. The model refuses"
        " real people, celebrities and trademarks, and the daily quota is"
        " shared with Grok chat. LoRAs cannot be used."
    ),
    prompt_hint=GROK_IMAGINE_VIDEO_PROMPT_HINT,
    accepts_start_image=True,
    image_label="開始フレーム（任意）",
    grok=GrokCliTask(
        values=(
            "prompt",
            "image",
            f"{KIE_SELECT_PREFIX}duration",
            f"{KIE_SELECT_PREFIX}resolution",
            f"{KIE_SELECT_PREFIX}aspect_ratio",
        ),
        media="video",
    ),
    selects={
        "duration": SelectSpec(
            label="尺（秒）",
            choices=GROK_VIDEO_DURATIONS,
            default="6",
            hint="1〜10 秒。指示文に書く希望なので、実際の尺はぶれることがある。",
        ),
        "resolution": SelectSpec(
            label="解像度",
            choices=GROK_VIDEO_RESOLUTIONS,
            default="720p",
            hint="CLI 経由は 720p までという報道に合わせている（希望として渡す）。",
        ),
        "aspect_ratio": SelectSpec(
            label="縦横比",
            choices=GROK_VIDEO_ASPECT_RATIOS,
            default="16:9",
            hint="開始フレーム画像を渡したときは画像の縦横比が優先される。",
        ),
    },
    notes=(
        "Grok Build CLI（サブスク枠）/ 音声（環境音・効果音・セリフ）はモデルが"
        "映像と同時に生成する（音声入力は無い） / 尺・解像度・縦横比は"
        "プロンプト経由の希望 / LoRA 不可 / 実在人物・著名人・商標は"
        "モデレーションで弾かれる / 枠は Chat と共有（動画の目安 10 本/日）/"
        " 尺の上限 10 秒・720p は CLI 経由の報道値なので、実機検証で 15 秒・"
        "1080p まで広げる可能性がある"
    ),
)


# --------------------------------------------------------------------------
# audio: workflow/audio/*.json
# --------------------------------------------------------------------------
#
# Audio workflows are stand-alone: they never take a start frame, never produce
# one, and are not part of the image -> video chain.  They have no LoRA chain
# either (neither template carries a LoRA loader), so ``lora_chain`` stays None
# and the LoRA pickers simply never offer them.

ACE_STEP_1_5 = WorkflowSpec(
    id="ace_step1_5_xl_sft",
    label="ACE-Step 1.5 XL（音楽・歌もの）",
    kind="audio",
    family="ace-step",
    relpath="audio/ace_step1_5_xl_sft.json",
    output_node="107",
    description=(
        "Song generation: writes a full music track, with **vocals** when"
        " `lyrics` are given and an instrumental when they are not."
        " `audio_prompt` is the *caption* of the track (style, instruments,"
        " production, voice); `bpm` / `keyscale` / `language` steer the"
        " arrangement. Use it whenever the user wants music or a song."
    ),
    prompt_hint=(
        "A caption of the track, not a scene: style / genre, mood and"
        " atmosphere, the instruments and how each one sounds, production"
        " style, tempo feel and — when there are lyrics — the voice. Comma"
        " separated keywords and plain prose both work; be specific rather"
        " than vague and keep it consistent with `lyrics`. The words to sing"
        " go in `lyrics`, never in `audio_prompt`."
    ),
    # 公式スペックは 10 秒〜600 秒（github.com/ace-step/ACE-Step-1.5 README）。
    # ComfyUI ノード側の受付幅（duration 0-2000 / seconds 1-1000）はもっと広いが、
    # モデルが品質を保証するのは 600 秒まで。
    min_duration=10.0,
    max_duration=600.0,
    default_duration=120.0,
    inject={
        # tags = the prompt describing the track
        "prompt": T("94", "tags", "TextEncodeAceStepAudio1.5"),
        "lyrics": T("94", "lyrics", "TextEncodeAceStepAudio1.5"),
        # the length lives in two places and must stay in sync: the conditioning
        # (94.duration) and the empty latent the sampler fills (98.seconds)
        "duration": T("94", "duration", "TextEncodeAceStepAudio1.5"),
        "latent_seconds": T("98", "seconds", "EmptyAceStep1.5LatentAudio"),
        "bpm": T("94", "bpm", "TextEncodeAceStepAudio1.5"),
        "keyscale": T("94", "keyscale", "TextEncodeAceStepAudio1.5"),
        "language": T("94", "language", "TextEncodeAceStepAudio1.5"),
        # one PrimitiveInt feeds both KSampler.seed and 94.seed
        "seed": T("109", "value", "PrimitiveInt"),
        "save_prefix": T("107", "filename_prefix", "SaveAudioMP3"),
    },
    notes="acestep_v1.5_xl_sft / 出力 MP3・歌詞ありでボーカル、なしでインスト",
)

STABLE_AUDIO_3 = WorkflowSpec(
    id="stable_audio_3_medium_base",
    label="Stable Audio 3 Medium（効果音・環境音・音楽）",
    kind="audio",
    family="stable-audio",
    relpath="audio/stable_audio_3_medium_base.json",
    output_node="19",
    description=(
        "General-purpose audio: sound effects, one-shots, single instrument"
        " takes and instrumental music. `audio_prompt` is a short natural"
        " description, `audio_category` picks the built-in prompt template"
        " (Music / Instrument / SFX / One-shot) and `reprompt` lets a local LLM"
        " expand the description before it is encoded. It does **not** sing:"
        " use ACE-Step for songs with lyrics."
    ),
    prompt_hint=(
        "One short natural-language description of the sound itself — what is"
        " heard, then genre / instruments / mood / tempo (music) or the source,"
        " the material and the space (SFX). No lyrics, no tag soup."
    ),
    # Stable Audio 3 medium は 6 分 20 秒（380 秒）まで
    # （github.com/Stability-AI/stable-audio-3 docs/guides/prompting.md）。
    min_duration=1.0,
    max_duration=380.0,
    default_duration=60.0,
    inject={
        "prompt": T("52:31", "value", "PrimitiveStringMultiline"),
        # EmptyLatentAudio.seconds reads this float, so one injection is enough
        "duration": T("52:36", "value", "PrimitiveFloat"),
        "audio_category": T("52:43", "choice", "CustomCombo"),
        "category_index": T("52:43", "index", "CustomCombo"),
        "reprompt": T("52:35", "value", "PrimitiveBoolean"),
        "seed": T("52:3", "seed", "KSampler"),
        "save_prefix": T("19", "filename_prefix", "SaveAudioMP3"),
    },
    # `index: 0` means "use the `choice` widget"; pinning it keeps the category
    # injection authoritative whatever the node's index handling does.
    constants={"category_index": 0},
    notes="stable_audio_3_medium_base / 出力 MP3・カテゴリ別の内蔵プロンプト展開あり",
)

#: the categories the Stable Audio template's CustomCombo offers
AUDIO_CATEGORIES: tuple[str, ...] = ("Music", "Instrument", "SFX", "One-shot")

# --- ACE-Step 1.5 enums (comfy_extras/nodes_ace.py, TextEncodeAceStepAudio1.5)
# The node declares these as COMBO widgets, so a value outside the list fails
# the whole prompt on ComfyUI's side.  Mirrored here so the form, the agent
# catalog and the job validator all offer / accept exactly the same set.

#: 17 roots x {major, minor}
KEYSCALES: tuple[str, ...] = tuple(
    f"{root} {quality}"
    for root in (
        "C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#",
        "Ab", "A", "A#", "Bb", "B",
    )
    for quality in ("major", "minor")
)

#: 50 languages plus ``unknown`` (auto / instrumental)
LANGUAGES: tuple[str, ...] = (
    "ar", "az", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es", "fa",
    "fi", "fr", "he", "hi", "hr", "ht", "hu", "id", "is", "it", "ja", "ko",
    "la", "lt", "ms", "ne", "nl", "no", "pa", "pl", "pt", "ro", "ru", "sa",
    "sk", "sr", "sv", "sw", "ta", "te", "th", "tl", "tr", "uk", "ur", "vi",
    "yue", "zh", "unknown",
)

#: the node's own INT bounds for ``bpm``
BPM_RANGE: tuple[int, int] = (10, 300)


# --------------------------------------------------------------------------
# audio: kie.ai（Suno V5 系、SPEC §5.2 / issue #20）
# --------------------------------------------------------------------------
#
# ACE-Step / Stable Audio と同じ**独立した音声ジョブ**（LoRA なし・画像や動画と
# 連結しない）で、走らせる先が自前の ComfyUI ではなく kie.ai というだけ。ただし
# Suno は Market 系ではなく**旧専用系**（:class:`app.kie.SunoTaskApi`）なので、
# マニフェスト側で気をつける点が 3 つある:
#
# - **``model`` はモデル名ではなくバージョン**（`V5` / `V5_5` / `V4_5PLUS`）。
#   選択式フィールドにしてあり、選んだ値が :attr:`KieTask.model` の既定を上書き
#   する（平置きボディなので ``input`` の ``model`` がそのまま勝つ）
# - **`customMode` は常に true**（スタイルと歌詞を自分で書くのがこのアプリの
#   使い方）。true では `style` / `title` が必須なので、`title` は
#   :meth:`app.kie.SunoTaskApi._title` が歌詞かスタイルの頭から作る
# - **`instrumental` は歌詞の有無から決まる**ので宣言しない（ACE-Step と同じ
#   「歌詞を空にすればインスト」の操作感になる）
#
# ACE-Step にあって Suno に無いつまみ（`bpm` / `keyscale` / `language`）は
# **宣言しない**: フォームはそのぶんの入力を出さず、エージェントが指定してきたら
# プラン検証で弾かれる（:func:`app.agent_protocol._audio_workflow_detail`）。
# テンポやキーは style の文中に、歌詞の言語は歌詞そのもので決まる。
#
# 尺（`duration`）は **選択式**（`auto` + 代表値）で持つ: kie.ai の `duration` は
# **V5_5 + customMode でしか効かず、他のモデルでは黙って無視される**ので、
# `WorkflowSpec.select_requires` で `model` が `V5_5` であることを要求し、違う
# モデルで明示指定したジョブは投入前に 422 にする（黙って無視されるより、その場で
# 断るほうが親切）。数値入力（`min_duration` / `max_duration`）にはしない: 上下限を
# 0 のままにしてフォームの長さ入力は出さず、選択式のプルダウンだけを出す（§2.4）。
#
# 1 リクエストで**2 曲**返るのが Suno の標準。両方 `outputs/{job_id}/` に落とす
# （`audio.mp3` / `audio_2.mp3`、:func:`app.kie.download_results`）。

#: Suno のモデルバージョン（kie.ai の `model`）
SUNO_MODELS: tuple[str, ...] = ("V5", "V5_5", "V4_5PLUS")

#: ボーカルの性別ヒント。``auto`` は「指定しない」で、キーごと落とされる
#: （:attr:`app.kie.SunoTaskApi.VOCAL_GENDERS`）。
SUNO_VOCAL_GENDERS: tuple[str, ...] = ("auto", "m", "f")

#: 尺（秒）。API は 10〜360 の任意の整数を取るが、細かく刻んでも意味が薄いので
#: 代表値だけ出す。``auto``（= 指定しない）が既定で、キーごと落とされる
#: （:attr:`KieTask.int_keys`）。**V5_5 でしか効かない**（:attr:`SUNO_DURATION_MODEL`）。
SUNO_DURATIONS: tuple[str, ...] = (
    "auto", "30", "60", "90", "120", "180", "240", "300", "360",
)
#: `duration` が効く唯一のモデルバージョン
SUNO_DURATION_MODEL = "V5_5"

#: 0〜1 の重みづけ（`styleWeight` / `weirdnessConstraint` / `audioWeight`）。
#: API は小数を取るので選択式では 0.25 刻みだけ出し、``auto``（= 指定しない）を
#: 既定にしてキーごと落とす（:attr:`KieTask.float_keys`）。
SUNO_WEIGHTS: tuple[str, ...] = ("auto", "0", "0.25", "0.5", "0.75", "1")

#: `style` の上限（kie.ai の customMode）。歌詞（`prompt`）は 5,000 字まで。
SUNO_MAX_PROMPT_CHARS = 1000

SUNO_V5 = WorkflowSpec(
    id="suno_v5",
    label="Suno V5（歌もの・外部 API）",
    kind="audio",
    family="suno",
    backend="kie",
    description=(
        "Song generation with **Suno V5** (external API): the strongest option"
        " for songs with real vocals. `audio_prompt` is the *style* — English,"
        " comma separated, sound only (genre, tempo, instruments, vocal,"
        " production) — and `lyrics` are the words, with `[Verse]` /"
        " `[Chorus]` structure tags. No lyrics == instrumental. Every request"
        " returns **two takes**. There is no bpm / key / language knob: write"
        " those into the style, or pick another model. The track **length**"
        f" (`duration`) only works on model `{SUNO_DURATION_MODEL}` — on any"
        " other version it is silently ignored, so a job that sets it with a"
        " different model is refused."
    ),
    prompt_hint=(
        "The **style**, not a story: English, comma separated, and only things"
        " you can hear — genre, tempo feel, the main instruments, the vocal"
        " (gender, register, delivery) and the production / mood. 120-300"
        " characters is the sweet spot. What the song is *about* belongs in"
        " `lyrics`, and anything to keep out belongs in `negative_tags`."
    ),
    max_prompt_chars=SUNO_MAX_PROMPT_CHARS,
    # 尺は選択式で持つので、数値入力の上下限は宣言しない（フォームは長さの
    # 入力欄を出さず、`duration` のプルダウンだけを出す）。
    # `duration` は V5_5 でしか効かず、他のモデルでは黙って無視されるので、
    # 明示指定と model の組み合わせを投入前に見る（models.select_problem）。
    select_requires={"duration": ("model", SUNO_DURATION_MODEL)},
    kie=KieTask(
        model="V5",
        api="suno",
        fields={
            # audio_prompt -> style（曲の「音」の記述）、lyrics -> prompt（歌詞）
            "prompt": "style",
            "lyrics": "prompt",
            "negative_tags": "negativeTags",
            f"{KIE_SELECT_PREFIX}model": "model",
            f"{KIE_SELECT_PREFIX}duration": "duration",
            f"{KIE_SELECT_PREFIX}vocal_gender": "vocalGender",
            f"{KIE_SELECT_PREFIX}style_weight": "styleWeight",
            f"{KIE_SELECT_PREFIX}weirdness": "weirdnessConstraint",
            f"{KIE_SELECT_PREFIX}audio_weight": "audioWeight",
        },
        # 0〜1 の重みづけは小数で送る。``auto`` は数として読めないので
        # キーごと落ちる（= kie.ai 側の既定に任せる）
        float_keys=("styleWeight", "weirdnessConstraint", "audioWeight"),
        # 尺は整数の秒。``auto`` は数として読めないのでキーごと落ちる
        int_keys=("duration",),
        # customMode=true = style と歌詞を自分で書くモード（false は説明文
        # 500 字だけのおまかせ生成なので、このアプリの使い方には合わない）
        constants={"customMode": True},
        # 12 credits ≒ $0.06（2 曲ぶん）。バージョンによる価格差は無い。
        credits=12.0,
    ),
    selects={
        "model": SelectSpec(
            label="モデル",
            choices=SUNO_MODELS,
            default="V5",
            hint="V5 が既定。V5_5 は最新、V4_5PLUS は旧世代。価格は同じ。",
        ),
        "duration": SelectSpec(
            label="尺（秒）",
            choices=SUNO_DURATIONS,
            default="auto",
            hint=f"{SUNO_DURATION_MODEL} を選んだときだけ効く（他のモデルでは"
            "指定できない）。auto は Suno におまかせ（だいたい 3 分前後）。",
        ),
        "vocal_gender": SelectSpec(
            label="ボーカルの性別",
            choices=SUNO_VOCAL_GENDERS,
            default="auto",
            hint="確率的なヒント（m = 男性 / f = 女性）。auto は指定しない。",
        ),
        "style_weight": SelectSpec(
            label="スタイルの効き",
            choices=SUNO_WEIGHTS,
            default="auto",
            hint="スタイル文にどれだけ忠実にするか（0〜1）。高いほど指定どおり、"
            "低いほど自由。auto は指定しない（kie.ai の既定）。",
        ),
        "weirdness": SelectSpec(
            label="奇抜さ",
            choices=SUNO_WEIGHTS,
            default="auto",
            hint="実験的な展開をどれだけ許すか（0〜1）。高いほど奇抜。"
            "auto は指定しない（kie.ai の既定）。",
        ),
        "audio_weight": SelectSpec(
            label="サウンドの効き",
            choices=SUNO_WEIGHTS,
            default="auto",
            hint="音づくり（編曲・音色）にどれだけ寄せるか（0〜1）。"
            "auto は指定しない（kie.ai の既定）。",
        ),
    },
    notes=(
        "kie.ai 経由 / 1 回で 2 曲返る（両方保存される） / 歌詞なしでインスト"
        f" / 尺は {SUNO_DURATION_MODEL} を選んだときだけ指定できる"
        "（他のモデルでは auto のまま） / bpm・キー・言語の指定は無い"
        "（スタイル文に書く） / 除外したい"
        "要素は「除外タグ」へ / スタイル・奇抜さ・サウンドの効きは 0〜1 の"
        "重みづけ（auto で kie.ai の既定） / タイトルは歌詞かスタイルから自動 /"
        " 成果物 URL は 14 日で失効"
    ),
)


SPECS: tuple[WorkflowSpec, ...] = (
    KREA2_TURBO,
    ANIMA,
    Z_IMAGE_TURBO,
    QWEN_IMAGE_EDIT,
    GROK_IMAGINE,
    GPT_IMAGE2,
    LTX_T2V,
    LTX_I2V,
    LTX_IA2V,
    LTX_ID_LORA,
    LTX_FLF2V,
    LTX_IC_LORA_IMAGE,
    LTX_IC_LORA_MOTION,
    WAN_DANCER,
    VEO3_1_FAST,
    VEO3_1_FAST_REF,
    VEO3_1_QUALITY,
    KLING3_VIDEO,
    KLING3_MULTISHOT,
    SEEDANCE2,
    SEEDANCE2_FAST,
    SEEDANCE2_MINI,
    SEEDANCE2_REF,
    SEEDANCE2_FAST_REF,
    SEEDANCE2_MINI_REF,
    GROK_IMAGINE_VIDEO,
    ACE_STEP_1_5,
    STABLE_AUDIO_3,
    SUNO_V5,
)

BY_ID: dict[str, WorkflowSpec] = {spec.id: spec for spec in SPECS}

DEFAULT_IMAGE_WORKFLOW = KREA2_TURBO.id
#: the closest successor of the old combined graph (image + reference audio +
#: talkvid ID-LoRA), so existing jobs and agent plans keep their semantics
DEFAULT_VIDEO_WORKFLOW = LTX_ID_LORA.id
DEFAULT_AUDIO_WORKFLOW = ACE_STEP_1_5.id

#: JobCreate field that carries each logical input
INPUT_FIELDS: dict[str, str] = {
    "image": "source_image",
    "audio": "audio_path",
    "end_image": "end_image",
    "video": "reference_video",
}

#: Japanese label of every logical input except ``image``, whose meaning differs
#: per workflow (see :attr:`WorkflowSpec.image_label`).
INPUT_LABELS: dict[str, str] = {
    "audio": "音声ファイル",
    "end_image": "最後のフレーム画像",
    "video": "参照動画",
}

#: **複数ファイル**の論理入力 -> それを運ぶ JobCreate / params のフィールド名。
#: :data:`INPUT_FIELDS` の複数版で、値は 1 本のパスではなく**パスのリスト**。
#: 受け取れる件数の上限はワークフローごと（:attr:`WorkflowSpec.multi_inputs`）。
MULTI_INPUT_FIELDS: dict[str, str] = {
    "reference_images": "reference_images",
    "reference_videos": "reference_videos",
    "reference_audios": "reference_audios",
}

#: 日本語ラベル（フォームの見出し・422 のメッセージ）
MULTI_INPUT_LABELS: dict[str, str] = {
    "reference_images": "参照画像",
    "reference_videos": "参照動画（参照モード）",
    "reference_audios": "参照音声",
}

#: 参照素材として受け付ける拡張子（投入前の軽い検証。大きさ・解像度・尺の細かい
#: 制約は外部 API 側の判断に任せ、失敗メッセージをそのまま見せる）。
MULTI_INPUT_EXTS: dict[str, frozenset[str]] = {
    "reference_images": frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"}),
    "reference_videos": frozenset({".mp4", ".webm", ".mkv", ".mov"}),
    "reference_audios": frozenset({".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus"}),
}

#: audio handling of a workflow that has no audio input at all
GENERATED_AUDIO = (
    "モデルが映像と同時に音声（環境音・声・効果音）も生成する。`audio_path` は"
    "使わないので指定しない。"
)


def input_label(spec: WorkflowSpec, name: str) -> str:
    """Japanese label of one logical input of ``spec``."""
    return spec.image_label if name == "image" else INPUT_LABELS.get(name, name)


def backend_available(backend: str) -> bool:
    """そのバックエンドが今この環境で使えるか（SPEC §5.2）。

    判定そのものは :mod:`app.backends`（認証確認とそのキャッシュ）が持つ。
    そちらはこのモジュールを import するので、循環を避けて関数の中で読み込む。
    """
    from . import backends

    return backends.available(backend)


def comfy_specs() -> tuple[WorkflowSpec, ...]:
    """``workflow/*.json`` のテンプレートを持つワークフローだけ。

    テンプレートを読むもの（モデルスロットの列挙、custom node の存在確認、
    マニフェスト検証）は外部バックエンドのワークフローを見てはいけない。
    """
    return tuple(spec for spec in SPECS if spec.backend == "comfyui")


def selectable_specs(kind: WorkflowKind) -> list[WorkflowSpec]:
    """UI・エージェントに出す ``kind`` のワークフロー（使えるものだけ）。

    :func:`get_spec` は使えないバックエンドのものも返す（過去ジョブの再実行や
    履歴の表示で id を引けなくなると困るため）。ここで絞るのは「これから選べる
    もの」の一覧だけ。
    """
    return [
        spec
        for spec in SPECS
        if spec.kind == kind and backend_available(spec.backend)
    ]


def image_specs() -> list[WorkflowSpec]:
    return selectable_specs("image")


def video_specs() -> list[WorkflowSpec]:
    return selectable_specs("video")


def audio_specs() -> list[WorkflowSpec]:
    return selectable_specs("audio")


def get_spec(workflow_id: str, kind: WorkflowKind | None = None) -> WorkflowSpec:
    spec = BY_ID.get(workflow_id)
    if spec is None:
        raise WorkflowSpecError(f"unknown workflow: {workflow_id!r}")
    if kind is not None and spec.kind != kind:
        raise WorkflowSpecError(f"workflow {workflow_id!r} is not a {kind} workflow")
    return spec


def get_video_spec(workflow_id: str | None) -> WorkflowSpec:
    return get_spec(workflow_id or DEFAULT_VIDEO_WORKFLOW, "video")


def get_image_spec(workflow_id: str | None) -> WorkflowSpec:
    return get_spec(workflow_id or DEFAULT_IMAGE_WORKFLOW, "image")


def get_audio_spec(workflow_id: str | None) -> WorkflowSpec:
    return get_spec(workflow_id or DEFAULT_AUDIO_WORKFLOW, "audio")


# --------------------------------------------------------------------------
# catalog (the manifests as the Grok system prompts see them)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogEntry:
    """One workflow as the system prompts describe it (SPEC §4.3 / AGENT-MODE §3.1).

    Everything here is derived from the :class:`WorkflowSpec`, so the prompts,
    the UI and the validators cannot drift apart.
    """

    id: str
    label: str
    kind: WorkflowKind
    #: model family (``krea2`` / ``anima`` / ``z-image`` / ``qwen-image`` / ``ltx2.3``)
    family: str
    description: str
    #: ``(JobCreate field, 日本語ラベル)`` of every input the workflow needs
    required_inputs: tuple[tuple[str, str], ...]
    #: ``(JobCreate field, 日本語ラベル)`` of inputs it accepts but does not need
    optional_inputs: tuple[tuple[str, str], ...]
    #: ``(JobCreate field, 日本語ラベル, 件数の上限)`` of the multi-file reference
    #: inputs it accepts (empty for every workflow without a reference mode)
    reference_inputs: tuple[tuple[str, str, int], ...]
    #: 選択式どうしの相関（``(名前, 相手の名前, 相手に必要な値)``、§3.1）。
    #: Suno の ``duration`` は ``model`` が ``V5_5`` のときしか効かない。
    select_requires: tuple[tuple[str, str, str], ...]
    #: ショット割りの宣言（``None`` = 1 ジョブ 1 ショット、§3.1）
    multi_shot: "MultiShotSpec | None"
    #: Elements（``@要素名`` の参照画像）の宣言（``None`` = 非対応、§3.1）
    elements: "ElementsSpec | None"
    #: can it be the second stage of a full (image -> video) job, and therefore
    #: also the target of a ``continue``?
    accepts_start_image: bool
    #: how ``audio_path`` is used (never empty)
    audio: str
    prompt_hint: str
    notes: str
    #: logical knobs the manifest exposes (``prompt``, ``lyrics``, ``bpm``, …).
    #: The audio catalog lists them so the agent knows which extra fields the
    #: workflow reads and which it ignores.
    supports: tuple[str, ...] = ()
    #: audio workflows: the accepted / suggested clip length in seconds
    min_duration: float = 0.0
    max_duration: float = 0.0
    default_duration: float = 0.0
    #: 選択式フィールド ``(論理名, 見出し, 選択肢, 既定値, 自動か, 一言)``。
    #: 宣言のないワークフローでは空なので、カタログにも何も出ない（SPEC §3.1）。
    selects: tuple[tuple[str, str, tuple[str, ...], str, bool, str], ...] = ()

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field for field, _ in self.required_inputs)


def catalog_entry(spec: WorkflowSpec) -> CatalogEntry:
    """Describe one workflow for the system prompts."""
    optional = tuple(
        name for name in INPUT_FIELDS if spec.supports(name) and name not in spec.requires
    )
    return CatalogEntry(
        id=spec.id,
        label=spec.label,
        kind=spec.kind,
        family=spec.family,
        description=spec.description,
        required_inputs=tuple(
            (INPUT_FIELDS[name], input_label(spec, name)) for name in spec.requires
        ),
        optional_inputs=tuple(
            (INPUT_FIELDS[name], input_label(spec, name)) for name in optional
        ),
        reference_inputs=tuple(
            (MULTI_INPUT_FIELDS[name], MULTI_INPUT_LABELS[name], limit)
            for name, limit in spec.multi_inputs.items()
            if name in MULTI_INPUT_FIELDS
        ),
        select_requires=tuple(
            (name, other, needed)
            for name, (other, needed) in spec.select_requires.items()
        ),
        multi_shot=spec.multi_shot,
        elements=spec.elements,
        accepts_start_image=spec.accepts_start_image,
        audio=spec.audio_role or GENERATED_AUDIO,
        prompt_hint=spec.prompt_hint,
        notes=spec.notes,
        supports=spec.supported_names(),
        min_duration=spec.min_duration,
        max_duration=spec.max_duration,
        default_duration=spec.default_duration,
        selects=tuple(
            (
                name,
                select.label,
                select.choices,
                select.fallback,
                bool(select.auto),
                select.hint,
            )
            for name, select in spec.selects.items()
        ),
    )


def video_catalog() -> list[CatalogEntry]:
    """Every selectable video workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in video_specs()]


def audio_catalog() -> list[CatalogEntry]:
    """Every selectable audio workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in audio_specs()]


def image_catalog() -> list[CatalogEntry]:
    """Every selectable image workflow, in UI / prompt order."""
    return [catalog_entry(spec) for spec in image_specs()]


def image_families() -> list[str]:
    """The families an image LoRA can be registered for, in UI order."""
    seen: list[str] = []
    for spec in image_specs():
        if spec.family not in seen:
            seen.append(spec.family)
    return seen


# --------------------------------------------------------------------------
# template loading
# --------------------------------------------------------------------------

_cache: dict[str, Workflow] = {}


def load_template(spec: WorkflowSpec | str, *, use_cache: bool = True) -> Workflow:
    """Read one API-format template from ``workflow/``.

    Templates are read-only, and every consumer deep-copies before mutating, so
    they are cached per process.  ``use_cache=False`` forces a re-read (the
    tests and the health check use it to pick up edits).
    """
    resolved = spec if isinstance(spec, WorkflowSpec) else get_spec(spec)
    if use_cache and resolved.id in _cache:
        return _cache[resolved.id]
    with open(resolved.path, encoding="utf-8") as fh:
        template = json.load(fh)
    if not isinstance(template, dict):
        raise WorkflowSpecError(f"{resolved.path} is not an API-format workflow")
    if use_cache:
        _cache[resolved.id] = template
    return template


def clear_cache() -> None:
    _cache.clear()


# --------------------------------------------------------------------------
# manifest validation
# --------------------------------------------------------------------------

def _validate_common(spec: WorkflowSpec) -> list[str]:
    """バックエンドに依らない決まりごと（カタログに出せる説明があるか等）。"""
    problems: list[str] = []
    # the catalog embedded in the Grok system prompts is generated from these,
    # so a new workflow must document itself (SPEC §4.3 / AGENT-MODE §3.1)
    if not spec.description.strip():
        problems.append(f"{spec.id}: description is empty")
    if spec.kind in ("video", "audio") and not spec.prompt_hint.strip():
        problems.append(f"{spec.id}: prompt_hint is empty")
    if spec.audio_role and not spec.supports("audio"):
        problems.append(f"{spec.id}: has an audio_role but no audio input")
    if spec.supports("audio") and not spec.audio_role.strip():
        problems.append(f"{spec.id}: has an audio input but no audio_role")
    for name in spec.requires:
        if not spec.supports(name):
            problems.append(f"{spec.id}: requires {name!r} but has no injection point")
    if spec.accepts_start_image and not spec.supports("image"):
        problems.append(f"{spec.id}: accepts_start_image but has no image input")
    # 複数ファイルの参照入力: 名前が語彙にあり、受け取り口があり、上限が正か
    for name, limit in spec.multi_inputs.items():
        if name not in MULTI_INPUT_FIELDS:
            problems.append(
                f"{spec.id}.multi_inputs[{name}]: unknown input"
                f" (known: {', '.join(sorted(MULTI_INPUT_FIELDS))})"
            )
        elif not spec.supports(name):
            problems.append(
                f"{spec.id}.multi_inputs[{name}]: no injection point"
            )
        if limit < 1:
            problems.append(f"{spec.id}.multi_inputs[{name}]: limit must be >= 1")
    # 参照素材のモードは開始フレームと排他なので、同じ宣言に同居させない
    if spec.multi_inputs and (spec.accepts_start_image or spec.supports("image")):
        problems.append(
            f"{spec.id}: reference inputs and a start frame are exclusive"
            " (declare the reference mode as its own workflow)"
        )
    # 選択式どうしの相関: 両方の名前が実在し、要求する値が相手の選択肢にあるか
    for name, requirement in spec.select_requires.items():
        other, needed = requirement
        for key in (name, other):
            if spec.select(key) is None:
                problems.append(f"{spec.id}.select_requires[{name}]: unknown select {key}")
        partner = spec.select(other)
        if partner is not None and needed not in partner.choices:
            problems.append(
                f"{spec.id}.select_requires[{name}]: {needed!r} is not a choice of {other}"
            )
    # ショット割り / Elements: 受け取り口があり、上限が正で筋が通っているか
    if spec.multi_shot is not None:
        for name in ("multi_shots", "multi_prompt"):
            if not spec.supports(name):
                problems.append(f"{spec.id}.multi_shot: no {name} injection point")
        if spec.multi_shot.max_shots < 1:
            problems.append(f"{spec.id}.multi_shot: max_shots must be >= 1")
        if spec.multi_shot.min_duration > spec.multi_shot.max_duration:
            problems.append(f"{spec.id}.multi_shot: duration range is inverted")
        # ショット割りのワークフローでは本文がショット側にあるので、トップレベルの
        # `video_prompt` を必須にしてはいけない（`models.multi_shot_problem` が
        # 逆に「書くな」と断る）
        if spec.prompt_required:
            problems.append(
                f"{spec.id}.multi_shot: prompt_required must be False"
                " (the text lives in the shots)"
            )
    if spec.elements is not None:
        if not spec.supports("kling_elements"):
            problems.append(f"{spec.id}.elements: no kling_elements injection point")
        if spec.elements.max_elements < 1:
            problems.append(f"{spec.id}.elements: max_elements must be >= 1")
        if not 1 <= spec.elements.min_images <= spec.elements.max_images:
            problems.append(f"{spec.id}.elements: image count range is inverted")
    if spec.kind == "audio" and (spec.accepts_start_image or spec.supports("image")):
        problems.append(f"{spec.id}: an audio workflow takes no image input")
    # 長さを一切宣言しないのは「このモデルには尺の指定が無い」（Suno）の意味で、
    # そのときだけ 0 / 0 / 0 を許す。中途半端に片方だけ 0 なのは宣言漏れ。
    if spec.kind == "audio" and any(
        (spec.min_duration, spec.default_duration, spec.max_duration)
    ) and not (
        0 < spec.min_duration <= spec.default_duration <= spec.max_duration
    ):
        problems.append(
            f"{spec.id}: min/default/max duration must be ordered and positive"
            f" (got {spec.min_duration} / {spec.default_duration}"
            f" / {spec.max_duration})"
        )
    return problems


def _validate_grok_spec(spec: WorkflowSpec) -> list[str]:
    """Grok Build CLI で走るワークフローの宣言そのものの検証（SPEC §5.2）。

    CLI は自然文しか受け取らないので、見るのは「タスク宣言があるか」「織り込む
    論理値が既知の語彙か」「プロンプトを受け取るか」だけ。``select:<名前>`` は
    kie.ai と同じ流儀で、宣言した選択式フィールドが本当に在るかを見る。
    """
    problems: list[str] = []
    if spec.kie is not None:
        problems.append(f"{spec.id}: backend 'grok_cli' but a KieTask is declared")
    if spec.grok is None:
        return [*problems, f"{spec.id}: backend 'grok_cli' but no GrokCliTask declared"]
    if "prompt" not in spec.grok.values:
        problems.append(f"{spec.id}.grok.values: 'prompt' is required")
    for name in spec.grok.values:
        if name.startswith(KIE_SELECT_PREFIX):
            if name[len(KIE_SELECT_PREFIX):] not in spec.selects:
                problems.append(f"{spec.id}.grok.values[{name}]: no such select")
        elif name not in KIE_VALUES:
            problems.append(
                f"{spec.id}.grok.values: unknown value {name!r}"
                f" (known: {', '.join(sorted(KIE_VALUES))})"
            )
    if spec.grok.media not in ("image", "video"):
        problems.append(f"{spec.id}.grok.media: {spec.grok.media!r} is not a media kind")
    elif spec.grok.media != spec.kind:
        problems.append(
            f"{spec.id}.grok.media: {spec.grok.media!r} does not match kind"
            f" {spec.kind!r}"
        )
    return problems


def _validate_codex_spec(spec: WorkflowSpec) -> list[str]:
    """Codex CLI で走るワークフローの宣言そのものの検証（SPEC §5.4）。

    :func:`_validate_grok_spec` と同じ見方（タスク宣言があるか / 織り込む論理値が
    既知の語彙か / プロンプトを受け取るか）に、「画像しか作れない」を足したもの。
    """
    problems: list[str] = []
    if spec.kie is not None:
        problems.append(f"{spec.id}: backend 'codex_cli' but a KieTask is declared")
    if spec.grok is not None:
        problems.append(f"{spec.id}: backend 'codex_cli' but a GrokCliTask is declared")
    if spec.codex is None:
        return [
            *problems,
            f"{spec.id}: backend 'codex_cli' but no CodexCliTask declared",
        ]
    if "prompt" not in spec.codex.values:
        problems.append(f"{spec.id}.codex.values: 'prompt' is required")
    for name in spec.codex.values:
        if name.startswith(KIE_SELECT_PREFIX):
            if name[len(KIE_SELECT_PREFIX):] not in spec.selects:
                problems.append(f"{spec.id}.codex.values[{name}]: no such select")
        elif name not in KIE_VALUES:
            problems.append(
                f"{spec.id}.codex.values: unknown value {name!r}"
                f" (known: {', '.join(sorted(KIE_VALUES))})"
            )
    if spec.kind != "image":
        problems.append(
            f"{spec.id}: backend 'codex_cli' only generates images (kind is"
            f" {spec.kind!r})"
        )
    return problems


def validate_external_spec(spec: WorkflowSpec) -> list[str]:
    """テンプレートを持たないワークフロー（kie.ai など）のマニフェスト検証。

    ComfyUI 側は「宣言したノードが本当にテンプレートに在るか」を見るが、外部 API
    にはグラフが無いので、代わりに**宣言そのものの筋が通っているか**を見る:
    タスク宣言があるか、渡す論理名が :data:`KIE_VALUES` の語彙か、``requires`` に
    書いた入力を本当に受け取れるか。
    """
    problems = _validate_common(spec)
    if spec.relpath or spec.inject or spec.output_node:
        problems.append(
            f"{spec.id}: backend {spec.backend!r} does not use a ComfyUI template"
        )
    if spec.lora_chain is not None:
        problems.append(f"{spec.id}: backend {spec.backend!r} has no LoRA chain")
    for name, select in spec.selects.items():
        if not select.choices:
            problems.append(f"{spec.id}.selects[{name}]: no choices declared")
        if select.default and select.default not in select.choices:
            problems.append(
                f"{spec.id}.selects[{name}]: default {select.default!r} is not"
                " one of the choices"
            )

    if spec.backend == "grok_cli":
        return problems + _validate_grok_spec(spec)
    if spec.backend == "codex_cli":
        return problems + _validate_codex_spec(spec)
    if spec.grok is not None:
        problems.append(
            f"{spec.id}: declares a GrokCliTask but its backend is {spec.backend!r}"
        )
    if spec.codex is not None:
        problems.append(
            f"{spec.id}: declares a CodexCliTask but its backend is {spec.backend!r}"
        )
    if spec.backend != "kie":
        problems.append(f"{spec.id}: backend {spec.backend!r} is not implemented yet")
        return problems
    if spec.kie is None:
        problems.append(f"{spec.id}: backend 'kie' but no KieTask declared")
        return problems
    if not spec.kie.model.strip():
        problems.append(f"{spec.id}.kie: model is empty")
    if not spec.kie.fields:
        problems.append(f"{spec.id}.kie: no input fields declared")
    for name, key in spec.kie.fields.items():
        if name.startswith(KIE_SELECT_PREFIX):
            if name[len(KIE_SELECT_PREFIX):] not in spec.selects:
                problems.append(
                    f"{spec.id}.kie.fields[{name}]: no such select"
                )
        elif name not in KIE_VALUES:
            problems.append(
                f"{spec.id}.kie.fields[{name}]: unknown value"
                f" (known: {', '.join(sorted(KIE_VALUES))})"
            )
        if not str(key).strip():
            problems.append(f"{spec.id}.kie.fields[{name}]: empty input key")
    declared = set(spec.kie.fields.values())
    for group, keys in (
        ("list_keys", spec.kie.list_keys),
        ("bool_keys", spec.kie.bool_keys),
        ("int_keys", spec.kie.int_keys),
    ):
        for key in keys:
            if key not in declared:
                problems.append(
                    f"{spec.id}.kie.{group}: {key!r} is not one of the declared"
                    " input keys"
                )
    return problems


def validate_spec(spec: WorkflowSpec, template: Workflow | None = None) -> list[str]:
    """Problems found in ``spec`` against its template (empty list == fine)."""
    if spec.backend != "comfyui":
        return validate_external_spec(spec)
    if spec.kie is not None:
        return [f"{spec.id}: declares a KieTask but its backend is 'comfyui'"]
    if spec.grok is not None:
        return [f"{spec.id}: declares a GrokCliTask but its backend is 'comfyui'"]
    if spec.codex is not None:
        return [f"{spec.id}: declares a CodexCliTask but its backend is 'comfyui'"]
    problems: list[str] = []
    try:
        tpl = template if template is not None else load_template(spec)
    except (OSError, ValueError) as exc:
        return [f"{spec.id}: template unreadable: {exc}"]

    def check(target: Target, origin: str) -> None:
        node = tpl.get(target.node_id)
        if not isinstance(node, dict):
            problems.append(f"{spec.id}.{origin}: node {target.node_id!r} is missing")
            return
        actual = node.get("class_type")
        if actual != target.class_type:
            problems.append(
                f"{spec.id}.{origin}: node {target.node_id!r} is {actual!r},"
                f" expected {target.class_type!r}"
            )
            return
        if target.field and target.field not in (node.get("inputs") or {}):
            problems.append(
                f"{spec.id}.{origin}: {target.node_id}.{target.field} does not exist"
            )

    for name, target in spec.inject.items():
        check(target, name)
    for index, target in enumerate(spec.seeds):
        check(target, f"seeds[{index}]")

    for name, select in spec.selects.items():
        if select.target is None:
            problems.append(f"{spec.id}.selects[{name}]: no injection target")
            continue
        check(select.target, f"selects[{name}]")
        if select.numeric_target is not None:
            check(select.numeric_target, f"selects[{name}].numeric_target")
        if not select.choices:
            problems.append(f"{spec.id}.selects[{name}]: no choices declared")
        if not select.label.strip():
            problems.append(f"{spec.id}.selects[{name}]: label is empty")
        if select.default and select.default not in select.choices:
            problems.append(
                f"{spec.id}.selects[{name}]: default {select.default!r} is not"
                " one of the choices"
            )
        node = tpl.get(select.target.node_id)
        inputs = (node.get("inputs") or {}) if isinstance(node, dict) else {}
        if select.index_field and select.index_field not in inputs:
            problems.append(
                f"{spec.id}.selects[{name}]: {select.target.node_id}"
                f".{select.index_field} does not exist"
            )
        # テンプレートの option と選択肢がずれていると、番号を書いても別の値が
        # 選ばれる（グラフは番号で n 行目を引く）。option を持つノードだけ確認する。
        options = [
            str(value)
            for key, value in inputs.items()
            if key.startswith("option") and str(value).strip()
        ]
        if options and list(select.choices) != options:
            problems.append(
                f"{spec.id}.selects[{name}]: choices {list(select.choices)!r} do"
                f" not match the template options {options!r}"
            )

    if spec.lora_chain is not None:
        chain = spec.lora_chain
        if not chain.consumers:
            problems.append(f"{spec.id}.lora_chain: no consumers declared")
        for index, consumer in enumerate(chain.consumers):
            check(consumer, f"lora_chain.consumers[{index}]")
            # the chain is spliced into an existing edge, so every consumer must
            # currently read the head (directly or through the placeholders it
            # replaces) — otherwise the manifest would silently rewire the graph
            node = tpl.get(consumer.node_id)
            link = (node.get("inputs") or {}).get(consumer.field) if isinstance(node, dict) else None
            if not isinstance(link, list) or len(link) != 2:
                problems.append(
                    f"{spec.id}.lora_chain.consumers[{index}]:"
                    f" {consumer.key} is not connected to a node"
                )
            elif link[0] != chain.head and link[0] not in chain.placeholders:
                problems.append(
                    f"{spec.id}.lora_chain.consumers[{index}]: {consumer.key} reads"
                    f" {link[0]!r}, expected the chain head {chain.head!r}"
                )
        if chain.head not in tpl:
            problems.append(
                f"{spec.id}.lora_chain.head: node {chain.head!r} is missing"
            )
        for node_id in chain.placeholders:
            node = tpl.get(node_id)
            if not isinstance(node, dict):
                problems.append(
                    f"{spec.id}.lora_chain: placeholder {node_id!r} is missing"
                )
            elif node.get("class_type") != "LoraLoaderModelOnly":
                problems.append(
                    f"{spec.id}.lora_chain: placeholder {node_id!r} is"
                    f" {node.get('class_type')!r}, expected 'LoraLoaderModelOnly'"
                )

    if spec.output_node not in tpl:
        problems.append(f"{spec.id}.output_node: node {spec.output_node!r} is missing")

    problems += _validate_common(spec)

    # audio workflows are stand-alone one-stage graphs: they take no picture,
    # produce no start frame and declare the clip length the model supports
    if spec.kind == "audio":
        if spec.lora_chain is not None:
            problems.append(f"{spec.id}: audio workflows have no LoRA chain")
        for name in ("prompt", "duration", "seed", "save_prefix"):
            if name not in spec.inject:
                problems.append(f"{spec.id}: audio workflow has no {name!r} target")

    return problems


def validate_specs(*, use_cache: bool = True) -> list[str]:
    """Validate every manifest. Used by the health check and the test suite."""
    problems: list[str] = []
    for spec in SPECS:
        if spec.backend != "comfyui":
            # テンプレートを持たないので、宣言そのものだけを見る（SPEC §5.2）
            problems += validate_external_spec(spec)
            continue
        try:
            template = load_template(spec, use_cache=use_cache)
        except (OSError, ValueError, WorkflowSpecError) as exc:
            problems.append(f"{spec.id}: template unreadable: {exc}")
            continue
        problems += validate_spec(spec, template)
    return problems
