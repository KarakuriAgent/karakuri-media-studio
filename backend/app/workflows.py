"""Workflow template registry and per-template injection manifests (SPEC §3).

The app ships a folder of independent ComfyUI API-format graphs under
``workflow/``: four image workflows (Krea 2 turbo, Anima, Z-Image turbo and
Qwen-Image Edit 2511), five MiniMax H3 video workflows and two audio workflows
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
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal

from .paths import WORKFLOW_DIR

Workflow = dict[str, dict[str, Any]]

WorkflowKind = Literal["image", "video", "audio"]

#: どのエンジンがこのワークフローを実行するか（SPEC §5 / §5.2）。
#: ``comfyui`` は ``workflow/*.json`` のテンプレートを自前の ComfyUI に投げる経路、
#: ``grok_cli`` は Grok Build CLI（サブスク枠）に Grok Imagine で描かせる経路
#: （:mod:`app.grok_media`。テンプレートを持たない）。
WorkflowBackend = Literal["comfyui", "grok_cli"]

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
#: 決める（尺など）。空文字は自動なし（既定値をそのまま使う）。
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
    （踊りの種類など）のための汎用の仕組み。宣言すると

    * 生成フォームがこのリストからの ``select`` を自動で描画し、
    * ジョブは ``selects`` にその値を持ち（リスト外は 422）、
    * エージェントのカタログにも選択肢が載る。

    ComfyUI の ``CustomCombo`` は選んだ文字列（``choice``）と 0 始まりの番号
    （``index``）の両方を持ち、**グラフが読むのは番号側**（``choice`` は表示用）。
    そのため :attr:`index_field` にも同じ選択の番号を書き込む。

    ``numeric_target`` は「選んだ値を数値としても別のノードに入れる」場合に使う
    （尺はコンボと ``TrimAudioDuration`` の両方に入れないと、音声だけ
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

        尺を音声の実長から決めるのに使う: 曲が途中で切れないよう
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
    templates have them; a template that carries fixed LoRA nodes it cannot work
    without splices the user chain in **after** them and leaves
    ``placeholders`` empty.
    """

    head: str
    placeholders: tuple[str, ...] = ()
    consumers: tuple[Target, ...] = ()


#: :data:`MULTI_INPUT_FIELDS` の名前のうち、ComfyUI のグラフに展開できるもの
#: （:class:`RefMediaFan`）。MiniMax H3 r2v は 3 種類とも受け取る。
REF_IMAGES_NAME = "reference_images"
REF_VIDEOS_NAME = "reference_videos"
REF_AUDIOS_NAME = "reference_audios"


@dataclass(frozen=True)
class RefMediaFan:
    """参照素材を**渡された件数ぶんだけ**グラフに生やす宣言（SPEC §3.1）。

    :class:`LoraChain` と並ぶ「グラフを動的に組み替える」宣言のもう 1 つ。
    MiniMax H3 の ``MiniMaxH3ReferenceToVideo`` は ``ref_images.ref_image_0``,
    ``…_1``, … という**可変個の入力**を種類ごとに持ち、繋いだ順にプロンプトから
    ``<Picture i>`` / ``<Video k>`` / ``<Audio j>`` として参照される。
    テンプレートは各種 1 件だけ繋いだ状態で持ち、ビルダー
    （:func:`app.workflow._build_ref_media`）が

    * テンプレートの雛形ローダー（:attr:`image_loader` ほか）と ``ref_*`` の
      入力をいったん全部落とし、
    * ジョブが渡した件数ぶんローダーを作って 0 から順に繋ぎ直す

    という手順で組み直す。0 件なら入力ごと消えるので、**雛形のファイル名が
    ComfyUI 側に無くて失敗する**ことがない。

    参照動画は ``LoadVideo`` 1 つでは終わらない: ``GetVideoComponents``
    （:attr:`video_decoder`）でフレーム列（出力 0）と音声（出力 1）に分け、
    **同じ番号**の ``ref_video_N`` / ``ref_video_audio_N`` に繋ぐ。ノード側が
    番号でペアを見るので、動画の音声だけを別リストにはできない（アプリ側でも
    「参照動画のサウンドトラックは常に一緒に渡す」ことにしてある）。

    受け取れる件数の上限は :attr:`WorkflowSpec.multi_inputs` の各名前（外部 API
    の参照モードと同じ仕組み・同じ UI）、下限は :attr:`min_refs`（種類を問わない
    合計）。どちらも投入前に :func:`app.models.reference_problem` が見る。
    """

    #: 参照素材を受け取るノード（可変入力を持つ側。``field`` は使わないので空）
    node: Target
    #: テンプレートが持つ雛形の ``LoadImage``（複製元。組み直しのときに消える）
    image_loader: Target
    #: 参照画像の可変入力の接頭辞。後ろに 0 始まりの番号が付く
    image_prefix: str = "ref_images.ref_image_"
    #: 雛形の ``LoadVideo``（``None`` = 参照動画は受け取らない）
    video_loader: Target | None = None
    #: 雛形の ``GetVideoComponents``（``field`` は VIDEO を受ける入力名）
    video_decoder: Target | None = None
    #: 参照動画（フレーム列）とそのサウンドトラックの可変入力の接頭辞。番号は
    #: 共通で、``ref_video_N`` と ``ref_video_audio_N`` が 1 本の動画を指す
    video_prefix: str = "ref_videos.ref_video_"
    video_audio_prefix: str = "ref_video_audios.ref_video_audio_"
    #: 雛形の ``LoadAudio``（``None`` = 単独の参照音声は受け取らない）
    audio_loader: Target | None = None
    audio_prefix: str = "ref_audios.ref_audio_"
    #: 参照素材の最低件数（種類を問わない合計。0 = 参照なしでも走らせてよい）
    min_refs: int = 1

    def names(self) -> tuple[str, ...]:
        """この宣言が受け取る論理名（:data:`MULTI_INPUT_FIELDS` のキー）。"""
        names = [REF_IMAGES_NAME]
        if self.video_loader is not None and self.video_decoder is not None:
            names.append(REF_VIDEOS_NAME)
        if self.audio_loader is not None:
            names.append(REF_AUDIOS_NAME)
        return tuple(names)

    def prefixes(self) -> tuple[str, ...]:
        """組み直しのときに落とす可変入力の接頭辞（宣言している種類のぶんだけ）。"""
        found = [self.image_prefix]
        if REF_VIDEOS_NAME in self.names():
            found += [self.video_prefix, self.video_audio_prefix]
        if REF_AUDIOS_NAME in self.names():
            found.append(self.audio_prefix)
        return tuple(found)

    def loaders(self) -> tuple[Target, ...]:
        """テンプレートが持つ雛形ノード（組み直しのときに消える）。"""
        found = [self.image_loader]
        if REF_VIDEOS_NAME in self.names():
            found += [self.video_loader, self.video_decoder]  # type: ignore[list-item]
        if REF_AUDIOS_NAME in self.names():
            found.append(self.audio_loader)  # type: ignore[arg-type]
        return tuple(found)


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
    "minimax-h3": "MiniMax H3",
    "ace-step": "ACE-Step 1.5",
    "stable-audio": "Stable Audio 3",
    "grok-imagine": "Grok Imagine",
}

#: 供給元の注記（生成フォームの 1 段目「モデル」に付く）。今はどのファミリーも
#: ローカルの ComfyUI で走るので空だが、外部サービス経由のモデルを足したときに
#: ここへ注記を書く。モードごとに変わるものではないので**モデル側**に出す
#: （各ワークフローの :attr:`WorkflowSpec.mode_label` には書かない）。
FAMILY_NOTES: dict[str, str] = {"grok-imagine": "サブスク CLI"}

#: LoRA registrations default to this family (the only image workflow that
#: existed before the selector), so the DB migration can backfill with it.
DEFAULT_FAMILY = "krea2"

#: 生成フォーム／ジョブのグローバル既定の解像度（メガピクセル、SPEC §3.1）。
#: 既定の動画ワークフローが MiniMax H3 になり、8GB 級のローカル GPU では
#: 1.0MP だと CUDA OOM になるので 0.4MP を全体の既定に置く。宣言のある
#: ワークフロー（:attr:`WorkflowSpec.default_megapixels`）はそちらが優先。
DEFAULT_MEGAPIXELS = 0.4


def family_label(family: str) -> str:
    """生成フォームの「モデル」プルダウンに出す 1 行（供給元の注記つき）。

    :data:`FAMILY_LABELS` は LoRA の一覧・登録フォームでも使う素の名前なので、
    注記はここで足す（そちらの表示は変えない）。
    """
    label = FAMILY_LABELS.get(family, family)
    note = FAMILY_NOTES.get(family)
    return f"{label}（{note}）" if note else label


@dataclass(frozen=True)
class MultiShotSpec:
    """**ショット割り**で 1 本の動画を作れるモデルの宣言（SPEC §3.1）。

    宣言は**ショット割り専用のワークフロー**だけが
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
class FrameGrid:
    """``frames_expr`` に固定するフレーム数の決め方（SPEC §3.1）。

    動画モデルの latent は時間方向にも圧縮されるので、受け取れるフレーム数は
    飛び飛びになる。テンプレートの ``ComfyMathExpression`` はその丸めを式で
    書いているが、アプリは値を Python 側で決めて式に焼き込む
    （:func:`app.workflow._inject_frame_count`）ので、格子をここで宣言する。

    * 既定: ``8n + 1``。要求より**長くならない**よう切り下げる。
    * MiniMax H3: ``17k + 5`` を 24fps で。ブロック単位でしか作れないので
      切り上げ、最短は 5 フレーム（``nodes_minimax_h3.py`` の
      ``align_frame_count(max(5, length))`` と同じ）。
    """

    #: 格子の間隔（``8`` なら 8 フレームごと）
    multiple: int = 8
    #: 格子の位相（``multiple * n + offset``）
    offset: int = 1
    #: True なら格子に切り上げ（MiniMax H3）、False なら切り下げ（既定）
    round_up: bool = False
    #: グラフの fps が固定のときその値（0 = ジョブの ``fps`` を使う）
    fps: int = 0
    #: フレーム数の下限（0 = 下限なし）
    minimum: int = 0

    def frames(self, duration: float, fps: int) -> int:
        """``duration`` 秒をこの格子に載せたフレーム数。"""
        rate = self.fps or max(1, int(fps))
        raw = max(0.0, float(duration)) * rate
        if self.round_up:
            count = max(self.minimum, int(round(raw)))
            return count + (self.offset - count) % self.multiple
        count = math.floor(raw / self.multiple) * self.multiple + self.offset
        return max(count, self.minimum)


#: 既定の格子（``8n + 1``）。宣言しないワークフローはこれ。
DEFAULT_FRAME_GRID = FrameGrid()

#: MiniMax H3 の格子（24fps・``17k + 5``、最短 5 フレーム）
MINIMAX_H3_FRAME_GRID = FrameGrid(multiple=17, offset=5, round_up=True, fps=24, minimum=5)


@dataclass(frozen=True)
class ElementsSpec:
    """**Elements**（参照画像を名前つきの要素にまとめる）の宣言（§3.1）。

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


#: Grok Imagine が受ける縦横比（CLI の ``image_gen`` / ``image_edit`` の
#: ``aspect_ratio`` パラメータそのまま）。``auto`` はツールに任せる。
GROK_ASPECT_RATIOS: tuple[str, ...] = ("1:1", "16:9", "9:16", "3:2", "2:3", "auto")

#: フォームの縦横比プリセット（``"16:9 (Widescreen)"``）は Grok の語彙より細かい
#: ので、**数値の比を計算して一番近いもの**に寄せる（:func:`grok_aspect_ratio`）。
_GROK_ASPECT_VALUES: tuple[tuple[str, float], ...] = tuple(
    (name, float(w) / float(h))
    for name, (w, h) in (
        ("1:1", (1, 1)),
        ("16:9", (16, 9)),
        ("9:16", (9, 16)),
        ("3:2", (3, 2)),
        ("2:3", (2, 3)),
    )
)


def grok_aspect_ratio(aspect_ratio: str) -> str:
    """フォームの縦横比 -> Grok Imagine が受ける縦横比（SPEC §5.2）。

    ``"4:3 (Standard)"`` のような表示名から ``w:h`` を読み、比が一番近いものを
    返す。読めなければ ``auto``（ツールに任せる）。
    """
    match = re.search(r"(\d+)\s*:\s*(\d+)", aspect_ratio or "")
    if not match:
        return "auto"
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return "auto"
    wanted = width / height
    return min(_GROK_ASPECT_VALUES, key=lambda item: abs(item[1] - wanted))[0]


#: :attr:`GrokImagineTask.values` に書ける論理名（指示文に織り込める値）
GROK_VALUES: frozenset[str] = frozenset({"prompt", "aspect_ratio", "image"})


@dataclass(frozen=True)
class GrokImagineTask:
    """Grok Build CLI の内蔵ツール 1 つ分の宣言（SPEC §5.2）。

    CLI にはグラフも ``input`` も無く、渡せるのは**自然文の指示だけ**なので、
    :class:`Target` のような注入先の対応表は持たない。宣言するのは「どの内蔵
    ツールを使わせるか」と「どの論理値を指示文に織り込むか」の 2 つだけで、
    指示文の組み立ては :func:`app.grok_media.build_request` が行う。

    入力ファイル（``image``）を宣言すると、そのファイルは**作業ディレクトリへ
    コピー**され、指示文がファイル名で参照する（CLI のサンドボックスは作業
    ディレクトリの外を読めるとは限らないため）。
    """

    #: 使わせる内蔵ツール（``image_gen`` = text-to-image / ``image_edit`` = 編集）
    tool: Literal["image_gen", "image_edit"] = "image_gen"
    #: 指示文に織り込む論理値。``prompt`` は必須で、``aspect_ratio`` と
    #: :data:`InputName` の入力（``image``）を足せる。
    values: tuple[str, ...] = ("prompt", "aspect_ratio")
    #: ``image_edit`` が受け取れる参照画像の枚数（``image_gen`` では 0）
    max_references: int = 0


@dataclass(frozen=True)
class WorkflowSpec:
    id: str
    label: str
    kind: WorkflowKind
    #: 生成フォームの「モデル → モード」2 段プルダウンの **2 段目**（モード）に
    #: 出す表示名。1 段目にモデル名（:data:`FAMILY_LABELS`）と供給元の注記
    #: （:data:`FAMILY_NOTES`）が出るので、こちらには**モデル名を書かない**
    #: （「テキスト→動画・音声つき (t2v)」のように、そのモデルの中での違いだけ）。
    #: 空なら :attr:`label` をそのまま使う。:attr:`label` のほうは履歴・
    #: プロンプトのカタログでも単独で読めなければならないので、モデル名を含んだ
    #: ままにしてある（両者は用途違いの別物）。
    mode_label: str = ""
    #: ``workflow/`` からの相対パス（``backend`` が ``comfyui`` のときだけ意味を持つ）
    relpath: str = ""
    #: logical name -> injection target
    inject: dict[str, Target] = field(default_factory=dict)
    #: node id that produces the artefact the job runner downloads
    output_node: str = ""
    #: 成果物とは別に、``/history`` から**文字列を 1 本**受け取るノードの id
    #: （``""`` = 受け取らない）。ラテント連続性（``minimax_h3_r2v_context``）が
    #: 保存した AV ラテントのパスを持ち帰るための口で、``PreviewAny`` の
    #: ``ui.text`` を読む（:func:`app.jobs._pick_text`）。
    latent_output_node: str = ""
    #: このワークフローを実行するエンジン（SPEC §5.2）。今は ComfyUI のみ。
    backend: WorkflowBackend = "comfyui"
    #: **ドラマスタジオが内部で解決するだけ**のバリアントか（SPEC §2.2）。
    #: True のものは :func:`selectable_specs` が落とすので、生成フォームの
    #: 動画モードにもエージェントのカタログにも出ない。ラテント保存版
    #: （``*_save``）と連続カット版（``*_context``）がこれで、どちらも
    #: プロジェクトの「ラテント連続性」＋「動画生成品質」から
    #: :func:`app.studio._plan_render` が id を組み立てて使う
    #: （素の版と入力の形も仕上がりも同じで、人が手で選ぶ意味が無い）。
    #: **id を直に指定する経路（:func:`get_spec`）はそのまま通る**ので、
    #: スタジオの解決・ジョブの実行・マニフェスト検証（:func:`validate_specs`）・
    #: 外部 API の id 直指定はどれも従来どおり。
    studio_only: bool = False
    #: model family (= the ``workflow/<kind>/<folder>`` name).  Image LoRAs are
    #: only offered for the family of the selected image workflow; the video
    #: templates ignore it (no video template has a user LoRA chain today).
    family: str = DEFAULT_FAMILY
    requires: tuple[InputName, ...] = ()
    #: **複数ファイル**を配列で受け取る論理入力（論理名 -> 受け取れる件数の上限、
    #: SPEC §3.1）。マルチモーダル参照（参照画像 9 枚 / 参照動画 3 本 /
    #: 参照音声 3 本）用で、宣言のないワークフローに参照素材を渡すと 422 になる
    #: （:func:`app.models.reference_problem`）。名前は
    #: :data:`MULTI_INPUT_FIELDS` のキー。**参照素材を使うモードは開始フレームと
    #: 排他**（外部 API 側の制約）なので、宣言を持つのは参照専用のワークフロー
    #: （``minimax_h3_r2v``）だけで、そちらは
    #: :attr:`accepts_start_image` が False になっている。
    multi_inputs: dict[str, int] = field(default_factory=dict)
    #: **選択式どうしの相関**（名前 -> ``(相手の名前, 相手に必要な値)``、§3.1）。
    #: 「その項目は相手がこの値のときしか効かない」ことの宣言で、既定以外を
    #: 選んでいるのに相手が違う値なら投入前に 422 にする
    #: （:func:`app.models.select_problem`）。たとえば ``duration`` が
    #: ``model`` の特定の値のときしか効かず、**それ以外では黙って無視される**
    #: ようなときに、気づかずに指定してしまうのを防ぐ。
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
    #: プロンプトを 422 で弾く（たとえば 500 文字）ので、ジョブを投入する
    #: 前に :func:`app.models.prompt_length_problem` で落とす。
    max_prompt_chars: int = 0
    #: audio workflows only: the clip length the model supports, in seconds.
    #: ``duration`` outside ``[min_duration, max_duration]`` is rejected before
    #: the job is queued (0.0 == no limit declared).
    min_duration: float = 0.0
    max_duration: float = 0.0
    #: audio workflows only: the length the UI / the agent start from
    default_duration: float = 0.0
    #: そのモデルが想定している解像度（メガピクセル、0.0 = 宣言なし）。
    #: テンプレートの ``ResolutionSelector`` が前提にしている画角と、フォームの
    #: グローバル既定（``DEFAULT_MEGAPIXELS``）がずれるモデル用（SPEC §3.1）。宣言があると、
    #: そのワークフローを選んだ時点でフォームの ``megapixels`` がこの値になる。
    #: MiniMax H3 は 0.4（1.0MP のまま回すと 8GB 級の GPU で CUDA OOM になる）。
    default_megapixels: float = 0.0
    #: can this workflow be the second stage of a full (image -> video) job?
    accepts_start_image: bool = False
    #: UI label of the primary image input
    image_label: str = "開始フレーム"
    #: 動画の幅・高さを丸める単位。動画モデルの latent は空間方向にも粗い格子を
    #: 持つので、その倍数でないと端が数 px 欠けたり latent の形が合わずに実行時に
    #: 落ちたりする（MiniMax H3 は 32 の倍数）。
    resolution_multiple: int = 8
    #: ``frames_expr`` に焼き込むフレーム数の格子（:class:`FrameGrid`）。既定は
    #: 既定は ``8n + 1``。MiniMax H3 は 24fps の ``17k + 5`` なので宣言し直す。
    frames: FrameGrid = DEFAULT_FRAME_GRID
    lora_chain: LoraChain | None = None
    #: 参照素材をグラフに動的に生やす宣言（:class:`RefMediaFan`、``None`` = 非対応）。
    #: 宣言すると ``multi_inputs`` の ``reference_images`` / ``reference_videos`` /
    #: ``reference_audios`` を ComfyUI のグラフでも受け取れるようになる（外部 API の
    #: 参照モードと同じ入力・同じ UI）。
    ref_media: RefMediaFan | None = None
    #: Grok Build CLI で走らせるときの宣言（:class:`GrokImagineTask`、
    #: ``None`` = ComfyUI のワークフロー）。``backend='grok_cli'`` と対で使う。
    grok: GrokImagineTask | None = None
    #: **渡されなかったらグラフから取り外す**入力の論理名（:data:`InputName`）。
    #: ``inject`` に載っているだけの入力はファイル名を空文字にするだけなので、
    #: ComfyUI が「そのファイルが無い」で落ちる。任意の最終フレーム
    #: （MiniMax H3 i2v の ``end_image``）のように**渡されないほうが普通**の入力は
    #: ここに宣言し、ビルダー（:func:`app.workflow._prune_optional_loaders`）が
    #: 雛形のローダーごと、それを読んでいるリンクごと落とす。
    #: :class:`RefMediaFan` が参照素材にしているのと同じ手口の単数版。
    optional_loaders: tuple[InputName, ...] = ()
    notes: str = ""
    seeds: tuple[Target, ...] = ()
    #: extra targets keyed by logical name that are always forced to a constant
    constants: dict[str, Any] = field(default_factory=dict)
    #: 選択式フィールド（論理名 -> :class:`SelectSpec`）。宣言のないワークフロー
    #: では空なので、フォームにもジョブにも何も増えない。
    selects: dict[str, SelectSpec] = field(default_factory=dict)
    #: ``video_prompt`` が必須か。プロンプトをコンボから組み立てるワークフロー
    #: は False で、書かれた場合だけ注入する。
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
        """このワークフローが論理名 ``name`` の値を受け取るか。"""
        if name in self.inject:
            return True
        # 参照素材は 1 つの :class:`Target` では表せない（件数ぶんノードを作る）
        # ので、宣言そのものを受け取り口として見る
        if self.ref_media is not None and name in self.ref_media.names():
            return True
        # 外部バックエンド（Grok CLI）は注入先を持たないので、宣言そのものを
        # 受け取り口として見る（SPEC §5.2）
        return self.grok is not None and name in self.grok.values

    def supported_names(self) -> tuple[str, ...]:
        """このワークフローが受け取る論理名（フォームとカタログが読む）。"""
        names = set(self.inject)
        if self.ref_media is not None:
            names |= set(self.ref_media.names())
        if self.grok is not None:
            names |= set(self.grok.values)
        return tuple(sorted(names))

    def target(self, name: str) -> Target | None:
        return self.inject.get(name)


# --------------------------------------------------------------------------
# image: workflow/image/*/*.json
# --------------------------------------------------------------------------

KREA2_TURBO = WorkflowSpec(
    id="krea2_turbo",
    label="Krea 2 turbo",
    mode_label="turbo",
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
        "steps": T("30:3", "steps", "KSampler"),
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
    mode_label="標準",
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
        "steps": T("90:76", "steps", "KSampler"),
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
    mode_label="turbo",
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
        "steps": T("57:3", "steps", "KSampler"),
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
    mode_label="画像編集 (2511)",
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
# image: Grok Imagine（Grok Build CLI・サブスク枠、SPEC §5.2）
# --------------------------------------------------------------------------
#
# xAI の従量課金 API（``XAI_API_KEY``）ではなく、**SuperGrok / X Premium+ の
# サブスクリプション枠**で動く公式 CLI（``grok``）をヘッドレスで叩き、内蔵ツールの
# ``image_gen`` / ``image_edit``（Grok Imagine Image 2.0）に描かせる
# （:mod:`app.grok_media`）。ComfyUI のテンプレートは持たないので ``relpath`` /
# ``inject`` / ``output_node`` はすべて空で、渡せるのは**自然文の指示だけ**。
#
# そのため:
#
# * **LoRA は挿せない**（グラフが無い）。``lora_chain`` は宣言しない
# * **解像度は選べない**。縦横比だけを ``image_gen`` の ``aspect_ratio``
#   （1:1 / 16:9 / 9:16 / 3:2 / 2:3 / auto）に寄せて渡し、``megapixels`` は使わない
# * **モデルのバージョンは指定できない**（CLI に ``model`` パラメータが無い）
# * 枠は Grok チャットと共有で、実在人物・著名人・商標はモデレーションで弾かれる

_GROK_IMAGINE_NOTES = (
    "Grok Build CLI（サブスク枠・要サインイン）/ ComfyUI 非依存 /"
    " 縦横比は 1:1・16:9・9:16・3:2・2:3 のいずれかに寄せて渡す"
    "（megapixels は使わない）/ モデルのバージョンは指定できない /"
    " LoRA 不可 / 実在人物・著名人・商標はモデレーションで弾かれる /"
    " 枠は Grok チャットと共有"
)

GROK_IMAGINE_T2I = WorkflowSpec(
    id="grok_imagine_t2i",
    label="Grok Imagine 画像生成（サブスク CLI）",
    mode_label="テキスト→画像",
    kind="image",
    family="grok-imagine",
    backend="grok_cli",
    description=(
        "Text-to-image through the official Grok Build CLI, on the SuperGrok /"
        " X Premium+ **subscription** quota (no metered API, no local ComfyUI)."
        " Same shape as the local text-to-image workflows: `image_prompt` only,"
        ' usable for `mode: "image_only"` and as the first stage of'
        ' `mode: "full"`. `aspect_ratio` is snapped to the closest ratio the'
        " tool accepts (1:1 / 16:9 / 9:16 / 3:2 / 2:3); `megapixels` and LoRAs"
        " are ignored because there is no graph. The model refuses real people,"
        " celebrities and trademarks, and the quota is shared with Grok chat."
    ),
    grok=GrokImagineTask(tool="image_gen", values=("prompt", "aspect_ratio")),
    notes=_GROK_IMAGINE_NOTES,
)

GROK_IMAGINE_EDIT = WorkflowSpec(
    id="grok_imagine_edit",
    label="Grok Imagine 画像編集（サブスク CLI）",
    mode_label="画像編集",
    kind="image",
    family="grok-imagine",
    backend="grok_cli",
    requires=("image",),
    image_label="編集元画像",
    description=(
        "Image **editing** through the Grok Build CLI's built-in `image_edit`"
        " tool: it rewrites the picture given in `source_image` following the"
        " instruction in `image_prompt`, so `source_image` is REQUIRED in every"
        ' mode that runs the image stage (including `mode: "full"`, where the'
        " edited still then becomes the video's start frame). The output"
        " resolution follows the input picture, so `aspect_ratio` /"
        " `megapixels` are ignored. Write `image_prompt` as an edit instruction"
        ' ("change X to Y, keep everything else unchanged"), never as a full'
        " scene description."
    ),
    grok=GrokImagineTask(
        tool="image_edit", values=("prompt", "image"), max_references=5
    ),
    notes=_GROK_IMAGINE_NOTES + " / 編集元画像が必須・解像度は入力画像から決まる",
)


# --------------------------------------------------------------------------
# video: workflow/video/minimax-h3/*.json
# --------------------------------------------------------------------------
#
# MiniMax H3（https://www.minimax.io/blog/minimax-h3）はテキスト・画像・動画・
# 音声をまとめて扱う omni-modal モデルで、**映像とステレオ音声を 1 回の
# forward pass で同時に生成する**（後乗せではない）。ComfyUI 公式テンプレート
# （https://docs.comfy.org/tutorials/video/minimax/minimax-h3）をそのまま
# API 形式にしたものが 3 つ:
#
# * t2v / i2v は同じ ``MiniMaxH3ImageToVideo`` ノード（``first_frame`` /
#   ``last_frame`` を繋ぐかどうかだけの違い）で、モデルは fl2va。プロンプトは
#   ノードの widget なので注入先は ``105:104.prompt`` そのもの。
# * r2v は ``MiniMaxH3ReferenceToVideo``（ref2va の別ウェイト）で、プロンプトは
#   ``PrimitiveStringMultiline``。
#
# これに加えて **turbo**（i2v / r2v）が 2 つある。入力の形は素の i2v / r2v と
# まったく同じで、違うのは中身だけ:
#
# * UNET は w4a8 mixed の量子化ウェイト、CLIP は heretic nvfp4、動画 VAE は
#   int8_convrot（音声 VAE だけ据え置き）。
# * ``BasicScheduler`` の steps が 20 → **4**。
# * UNETLoader と BasicGuider の間に高速化のノードが**テンプレートに直接**
#   直列で入っている: ``MiniMaxH3TurboLoRA``（4step 蒸留 LoRA）→
#   ``PathchSageAttentionKJ`` → ``MiniMaxH3MemoryEfficientSageAttentionPatch`` →
#   ``SolAttnPatch`` → ``MiniMaxH3SigmaShift`` →
#   ``SpectrumApplyMiniMaxH3``。``BasicScheduler`` は sigma を作るだけなので
#   ``SolAttnPatch`` の出力（SigmaShift の**手前**）から model を取る。
#
# さらに **opt**（i2v / r2v）が 2 つ。turbo から蒸留 LoRA だけを抜いたもので、
# ``MiniMaxH3TurboLoRA`` が無く（``PathchSageAttentionKJ`` が UNETLoader 直結）、
# ``BasicScheduler`` の steps は素の版と同じ **20**。量子化ウェイトと
# アテンション系パッチはそのままなので、品質は素の版相当のまま実行だけ速い。
#
# グラフの都合で入れ替えた点:
#
# * 幅・高さはテンプレートでは ``ResolutionSelector``（115）から来ているが、
#   アプリは縦横比とメガピクセルから自分で計算した整数を持っている（§3.1）ので、
#   MiniMaxH3 ノードの ``width`` / ``height`` に直接入れる（115 は宙に浮くが、
#   ComfyUI は出力に繋がっていないノードを実行しないので害はない）。
# * 任意の入力（i2v の ``last_frame``、r2v の参照素材）はテンプレートに**雛形を
#   1 つだけ**繋いだ状態で持たせ、ビルダーが渡された件数ぶんに組み直す
#   （:attr:`WorkflowSpec.optional_loaders` / :class:`RefMediaFan`）。雛形を
#   そのまま残すと、テンプレートに書いてあるファイル名を ComfyUI 側で探して
#   失敗するため。
#
# CFG を使わない（``BasicGuider``）ので **negative prompt は無い**。禁止事項は
# プロンプト本文に書かせる。

#: 3 つのワークフローで共通の注意書き（モデルの素性と要件）
_MINIMAX_H3_NOTES = (
    "24fps 固定・尺は 17k+5 フレームの格子に切り上げ（5 秒 = 124 フレーム、"
    "学習範囲は約 1〜15 秒）/ 短辺 768px・最大 768x1344 が既定の画角"
    "（幅高さは 32 の倍数）/ negative prompt は無い（CFG 無しの BasicGuider）/"
    " ユーザー LoRA を挿すチェーンは持たない"
)

#: turbo 版だけの注意書き（素のものとの差分）
_MINIMAX_H3_TURBO_NOTES = (
    " / **turbo**: 4step 蒸留 LoRA（`minimax_h3_turbo_v4_step600_ema`）と"
    " Sage Attention / Mem Eff Sage Attention / Sol-Attn / SigmaShift /"
    " Spectrum をテンプレートに"
    "直列で焼き込んだ高速版で、サンプリングは **4 ステップ**固定"
    "（入力の形は素の版とまったく同じ）。`PathchSageAttentionKJ`"
    "（ComfyUI-KJNodes + SageAttention）・"
    "`MiniMaxH3MemoryEfficientSageAttentionPatch`・"
    "`SolAttnPatch`・`MiniMaxH3TurboLoRA`・"
    "`MiniMaxH3SigmaShift`・`SpectrumApplyMiniMaxH3` の**カスタムノードと"
    "量子化ウェイト一式が入った環境でのみ**動く"
)

#: opt 版だけの注意書き（turbo からの差分）
_MINIMAX_H3_OPT_NOTES = (
    " / **opt**: turbo から 4step 蒸留 LoRA だけを抜いた最適化版で、"
    "サンプリングは素の版と同じ **20 ステップ**（品質は素の版相当のまま）。"
    "量子化ウェイトと Sage Attention / Mem Eff Sage Attention / Sol-Attn /"
    " SigmaShift / Spectrum は turbo と同じくテンプレートに直列で焼き込み済みで、"
    "実行だけが速い（入力の形は素の版とまったく同じ）。"
    "`PathchSageAttentionKJ`（ComfyUI-KJNodes + SageAttention）・"
    "`MiniMaxH3MemoryEfficientSageAttentionPatch`・`SolAttnPatch`・"
    "`MiniMaxH3SigmaShift`・`SpectrumApplyMiniMaxH3` の**カスタムノードと"
    "量子化ウェイト一式が入った環境でのみ**動く"
)

#: MiniMax H3 が想定している解像度（短辺 768px・最大 768x1344 なので約 0.4MP）。
#: テンプレートの ``ResolutionSelector`` もこの値で、1.0MP のまま回すと 8GB 級の
#: GPU では CUDA OOM になる（SPEC §3.1）。``DEFAULT_MEGAPIXELS`` と同値。
MINIMAX_H3_MEGAPIXELS = 0.4

#: r2v が受け取れる参照素材の件数（``MiniMaxH3ReferenceToVideo`` の Autogrow の
#: 上限そのまま。動画のサウンドトラックは動画と同数なので別枠を持たない）
MINIMAX_H3_REFERENCE_IMAGES = 9
MINIMAX_H3_REFERENCE_VIDEOS = 3
MINIMAX_H3_REFERENCE_AUDIOS = 3

#: モデルファイル（t2v / i2v は fl2va、r2v は ref2va。他は共通）
_MINIMAX_H3_MODELS = (
    " モデル: minimax_h3_{unet}_pruned_int8_convrot（diffusion_models）+"
    " minimax_h3_video_vae_fp16 + minimax_h3_audio_vae_fp32 +"
    " qwen3vl_32b_minimax_h3_nvfp4_awq（text_encoders）"
)

#: turbo / 連続カットのテンプレートだけが使う**任意のカスタムノード**の
#: ``class_type``。入れていない環境で接続インジケーターが赤くならないよう、
#: ヘルスチェックの「必ず在るべきノード」からは外す
#: （:func:`app.workflow.all_required_class_types`）。そのワークフローを選んで
#: 実行したときだけ ComfyUI 側でエラーになる。
OPTIONAL_CLASS_TYPES: frozenset[str] = frozenset(
    {
        "MiniMaxH3TurboLoRA",
        "PathchSageAttentionKJ",
        "MiniMaxH3MemoryEfficientSageAttentionPatch",
        "SolAttnPatch",
        "MiniMaxH3SigmaShift",
        "SpectrumApplyMiniMaxH3",
        # ラテント連続性（ComfyUI-H3-Motion-Context /
        # ComfyUI-MiniMaxH3-Contex-Loop）。:data:`LATENT_CONTEXT_CLASS_TYPES`
        # にも同じものを並べてあり、そちらは接続先ごとの対応判定に使う。
        "MiniMaxH3MotionContext",
        "MiniMaxH3MotionContextLoadLatent",
        "MiniMaxH3MotionContextSaveLatent",
        "MiniMaxH3MotionContextTrim",
    }
)

#: ラテント連続性が要求するカスタムノード（:func:`app.comfy.latent_context_support`
#: が ``/object_info`` に居るかどうかを見る）。1 つでも欠けたら「使えない」。
LATENT_CONTEXT_CLASS_TYPES: tuple[str, ...] = (
    "MiniMaxH3MotionContext",
    "MiniMaxH3MotionContextLoadLatent",
    "MiniMaxH3MotionContextSaveLatent",
    "MiniMaxH3MotionContextTrim",
)

#: turbo 版のモデルファイル（量子化ウェイト + 4step 蒸留 LoRA）
_MINIMAX_H3_TURBO_MODELS = (
    " モデル: minimax_h3_{unet}_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_int8_convrot + minimax_h3_audio_vae_fp32 +"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）+"
    " minimax_h3_turbo_v4_step600_ema（loras）"
)

#: opt 版のモデルファイル（量子化ウェイトのみ・蒸留 LoRA は使わない）
_MINIMAX_H3_OPT_MODELS = (
    " モデル: minimax_h3_{unet}_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_int8_convrot + minimax_h3_audio_vae_fp32 +"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）"
)

#: turbo 版の ``MiniMaxH3TurboLoRA.low_vram``（論理名 = ジョブの ``selects`` のキー）
MINIMAX_H3_LOW_VRAM_NAME = "low_vram"

#: 4step 蒸留 LoRA を低 VRAM モードで読ませるかどうか。ノードの入力は真偽値
#: なので、選んだ文字列は :func:`app.workflow._coerce` が ``on`` -> ``True`` /
#: ``off`` -> ``False`` に直してから書き込む（:data:`app.workflow._BOOL_INPUTS`）。
#: 既定は OFF（テンプレートの現状値と同じ）で、VRAM が足りないときだけ ON。
_MINIMAX_H3_LOW_VRAM_SELECT = SelectSpec(
    label="Low VRAM（turbo LoRA）",
    choices=("off", "on"),
    default="off",
    target=T("150", "low_vram", "MiniMaxH3TurboLoRA"),
    # ``CustomCombo`` ではないので番号を書く先は無い
    index_field="",
    hint=(
        "on にすると 4step 蒸留 LoRA を低 VRAM モードで読み込む"
        "（VRAM が足りずに落ちるときだけ。遅くなるので既定は off）"
    ),
)

#: i2v だけの注意書き（任意の最終フレーム）
_MINIMAX_H3_I2V_NOTES = (
    " / `end_image` は任意（渡すと `last_frame` に繋いで最終フレーム指定に"
    "なる。渡さなければ雛形の LoadImage ごとグラフから外す）"
)

#: r2v だけの注意書き（参照素材の数え方と扱い）
_MINIMAX_H3_R2V_NOTES = (
    f" / 参照素材は画像 {MINIMAX_H3_REFERENCE_IMAGES} 枚"
    f"（`reference_images`）+ 動画 {MINIMAX_H3_REFERENCE_VIDEOS} 本"
    f"（`reference_videos`）+ 音声 {MINIMAX_H3_REFERENCE_AUDIOS} 本"
    "（`reference_audios`）まで・合計 1 件以上必須（件数ぶんローダーを作って"
    "繋ぐ）/ プロンプトからは種類ごとに渡した順で `<Picture i>` /"
    " `<Video k>` / `<Audio j>`。提示順は 画像 → 動画（各動画の音声の"
    " `<Audio j>` はその `<Video k>` の直前）→ 単独音声で、**動画の音声と"
    "単独音声は `<Audio j>` の連番を共有し、動画の音声が先に番号を消費する** /"
    " 参照動画は **24fps 前提**（LoadVideo → GetVideoComponents でフレーム列と"
    "音声を取り出すだけで fps 変換はしない。他の fps の素材は時間感覚がずれる）・"
    "1 本 2〜15 秒が目安で、生成フレーム数より長いぶんは切り詰められる・"
    "音声トラックは常に一緒に参照へ渡す（音声の無い動画は `<Audio j>` を"
    "消費しないので番号がずれる）/ 参照音声は 32kHz に自動リサンプル /"
    " 開始フレームは受け取らない / ref_image_size=match（生成解像度に合わせて"
    "縮小。max は 2048px 短辺で同一性は上がるが数倍遅い）"
)


#: 3 つで共通のプロンプトの書き方（公式 rewrite 契約・禁止事項を明記）。
#: 一次: MiniMax H3 VIDEO_PROMPT_WRITING_GUIDE_{base,ref}_en.md と
#: skills/h3-prompt-writing。二次: ComfyUI テンプレート / docs.comfy.org。
_MINIMAX_H3_PROMPT_CORE = (
    " Write **one English document** in official MiniMax H3 fields — never a"
    " `Camera:` / `Audio:` footer and never `[0s-1.5s] Shot 1:` stamps."
    " Base (t2v / i2v): optional alignment first line (I2VA / FL2VA / L2VA"
    " only), then `integrated_multimodal_description:` /"
    " `overall_soundscape:` / `non_diegetic_music:`. Ref2VA:"
    " `subject_definitions:` / `summary:` / `retention_analysis:` /"
    " `detailed_description:` / `overall_soundscape:` /"
    " `non_diegetic_music:`. `[Shot 1]` has no timestamp; later shots are"
    " `[Shot N] At MM:SS.mmm, the camera cuts to …` with strictly increasing"
    " times inside `duration`. Camera motion is a natural English clause"
    " inside the shot (Zoom In/Out, Push In / Pull Out, Pan Left/Right, Truck"
    " Left/Right, Tilt Up/Down, Pedestal Up/Down, Arc Shot, Tracking Shot,"
    " Static Shot, Shake Slightly/Strongly, POV, Roll Clockwise/"
    " Counterclockwise; optional `with small/large amplitude`,"
    " `at slow/fast speed`). Speakers use stable `(S1)` / `(S2)`; spoken words"
    " go in `<d>[Language] …</d>` (identifying phrase + ID + delivery outside"
    " `<d>`; do not translate the words). `overall_soundscape` is 1–4 sentences"
    " of ambience and physical / non-verbal sound (not dialogue)."
    " `non_diegetic_music` is 1–3 sentences of instrumentation / tempo /"
    " dynamics, or `N/A`. There is **no negative prompt**: finish with"
    " `No text, subtitles, logos or watermarks.` — burnt-in captions appear"
    " otherwise whenever dialogue is spoken."
)

MINIMAX_H3_T2V = WorkflowSpec(
    id="minimax_h3_t2v",
    label="テキスト→動画・音声つき (MiniMax H3 t2v)",
    mode_label="テキスト→動画・音声つき (t2v)",
    kind="video",
    family="minimax-h3",
    relpath="video/minimax-h3/minimax_h3_t2v.json",
    output_node="92",
    requires=(),
    description=(
        "テキストだけから、ステレオ音声つきの動画を生成する（MiniMax H3 fl2va）。"
        "開始フレームは不要で、画面に写るものも音（セリフ・効果音・音楽）も"
        "すべてプロンプトで決まる。1 本のプロンプトにショット割りのタイムラインを"
        "書けば、カット割りのある短いシーンをそのまま作れる。24fps・尺は約 1〜15 秒、"
        "短辺 768px 前後（最大 768x1344）が想定解像度。"
    ),
    prompt_hint=(
        "No start frame exists (T2VA): the prompt has to establish the"
        " subject, the set, the wardrobe and the framing as well as the motion"
        " and the sound. Start directly with the three official fields — no"
        " alignment line." + _MINIMAX_H3_PROMPT_CORE
    ),
    accepts_start_image=False,
    resolution_multiple=32,
    default_megapixels=MINIMAX_H3_MEGAPIXELS,
    frames=MINIMAX_H3_FRAME_GRID,
    inject={
        # prompt / width / height はサブグラフを展開した MiniMaxH3ImageToVideo の
        # widget そのもの（テンプレートでは width / height は 115 から来ている）
        "prompt": T("105:104", "prompt", "MiniMaxH3ImageToVideo"),
        "width": T("105:104", "width", "MiniMaxH3ImageToVideo"),
        "height": T("105:104", "height", "MiniMaxH3ImageToVideo"),
        "duration": T("105:111", "value", "PrimitiveFloat"),
        "frames_expr": T("105:107", "", "ComfyMathExpression"),
        "steps": T("105:9", "steps", "BasicScheduler"),
        "save_prefix": T("92", "filename_prefix", "SaveVideo"),
    },
    # 高速化トグル（任意・既定 OFF）: UNETLoader と BasicGuider の間に挟む
    seeds=(T("105:15", "noise_seed", "RandomNoise"),),
    notes=_MINIMAX_H3_NOTES + " /" + _MINIMAX_H3_MODELS.format(unet="fl2va"),
)

MINIMAX_H3_I2V = WorkflowSpec(
    id="minimax_h3_i2v",
    label="画像→動画・音声つき (MiniMax H3 i2v)",
    mode_label="画像→動画・音声つき (i2v)",
    kind="video",
    family="minimax-h3",
    relpath="video/minimax-h3/minimax_h3_i2v.json",
    output_node="92",
    requires=("image",),
    description=(
        "開始フレーム画像から、ステレオ音声つきの動画を生成する"
        "（MiniMax H3 fl2va）。被写体とセットは画像が決め、プロンプトは動き・"
        "カット割り・音（セリフ・効果音・音楽）を担当する。`end_image` を一緒に"
        "渡すと**最終フレームの指定**になり（fl2va = first/last frame to video・"
        "audio）、2 枚の間をどうつなぐかをプロンプトで書く。24fps・尺は約 1〜15 秒。"
    ),
    prompt_hint=(
        "I2VA: the start frame is Picture 1 and **is** frame 0 of Shot 1 —"
        " never contradict it. Open with"
        " `For the target video, at 0.00 seconds into the target video,"
        " <Picture 1> (from [Shot 1]) is fully referenced.` then a blank line,"
        " then the three official fields. Path: first-frame anchor → action"
        " onset → development → result. With an `end_image` as well this is"
        " FL2VA: prefer a single shot and describe the *path* between Picture 1"
        " and Picture 2 (alignment:"
        " `How the reference pictures align with the target video — Picture 1"
        " (from Shot 1) aligns with the 0.00-second mark of the target video;"
        " Picture 2 (from Shot N) aligns with the S.SS-second mark of the"
        " target video.`)."
        + _MINIMAX_H3_PROMPT_CORE
    ),
    accepts_start_image=True,
    image_label="開始フレーム",
    resolution_multiple=32,
    default_megapixels=MINIMAX_H3_MEGAPIXELS,
    frames=MINIMAX_H3_FRAME_GRID,
    inject={
        "prompt": T("105:104", "prompt", "MiniMaxH3ImageToVideo"),
        "width": T("105:104", "width", "MiniMaxH3ImageToVideo"),
        "height": T("105:104", "height", "MiniMaxH3ImageToVideo"),
        "duration": T("105:111", "value", "PrimitiveFloat"),
        "frames_expr": T("105:107", "", "ComfyMathExpression"),
        "steps": T("105:9", "steps", "BasicScheduler"),
        "image": T("114", "image", "LoadImage"),
        # 任意の最終フレーム。渡されなければ雛形の LoadImage ごと落ちる
        "end_image": T("116", "image", "LoadImage"),
        "save_prefix": T("92", "filename_prefix", "SaveVideo"),
    },
    optional_loaders=("end_image",),
    seeds=(T("105:15", "noise_seed", "RandomNoise"),),
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + " /"
        + _MINIMAX_H3_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_R2V = WorkflowSpec(
    id="minimax_h3_r2v",
    label="参照素材→動画・音声つき (MiniMax H3 r2v)",
    mode_label="参照素材→動画・音声つき (r2v)",
    kind="video",
    family="minimax-h3",
    relpath="video/minimax-h3/minimax_h3_r2v.json",
    output_node="92",
    requires=(),
    description=(
        f"参照画像 1〜{MINIMAX_H3_REFERENCE_IMAGES} 枚・参照動画"
        f" {MINIMAX_H3_REFERENCE_VIDEOS} 本・参照音声"
        f" {MINIMAX_H3_REFERENCE_AUDIOS} 本までの**見た目・動き・音を保ったまま**、"
        "別のシーンの動画をステレオ音声つきで生成する（MiniMax H3 ref2va）。"
        "開始フレームとは違い、参照素材は 1 枚目の絵ではなく「誰／何を出すか・"
        "どう動くか・どう鳴るか」の指定で、構図とカット割りはプロンプトが決める。"
        "プロンプト中では種類ごとに渡した順で `<Picture 1>`・`<Video 1>`・"
        "`<Audio 1>` … と呼ぶ（参照動画の音声も参照として渡すので、`<Audio j>` は"
        "**参照動画のぶんが先に番号を取り**、そのあと参照音声が続く）。"
        "24fps・尺は約 1〜15 秒。"
    ),
    prompt_hint=(
        "Ref2VA: write the six official sections in order"
        " (`subject_definitions` / `summary` / `retention_analysis` /"
        " `detailed_description` / `overall_soundscape` /"
        " `non_diegetic_music`). Refer to the material **by tag, per type, in"
        " the order it was given** — `<Picture 1>`, … for `reference_images`,"
        " `<Video 1>`, … for `reference_videos`, `<Audio 1>`, … for audio —"
        " and say what each one must keep. `<Subject N>` is reusable visible"
        " content; a standalone `<Picture N>` is only a frame / storyboard"
        " anchor. **Every reference video is passed together with its own"
        " soundtrack, and soundtracks share the `<Audio j>` numbering with"
        " `reference_audios`, taking the low numbers first**: with 2 reference"
        " videos and 1 reference audio, `<Audio 1>` / `<Audio 2>` are those"
        " videos' soundtracks and `<Audio 3>` is the standalone track. Do not"
        " re-describe a reference as if it were the shot; name which reference"
        " drives which part, and never use a tag with nothing behind it."
        + _MINIMAX_H3_PROMPT_CORE
    ),
    # 参照モードは「1 枚目の絵」ではないので、image -> video 連鎖の受け口には
    # しない（生成した静止画を開始フレームとして渡す意味にならない）。開始
    # フレームの受け取り口そのものを持たないのは、外部 API の参照専用ワーク
    # フローと同じ形。
    accepts_start_image=False,
    resolution_multiple=32,
    default_megapixels=MINIMAX_H3_MEGAPIXELS,
    frames=MINIMAX_H3_FRAME_GRID,
    multi_inputs={
        REF_IMAGES_NAME: MINIMAX_H3_REFERENCE_IMAGES,
        REF_VIDEOS_NAME: MINIMAX_H3_REFERENCE_VIDEOS,
        REF_AUDIOS_NAME: MINIMAX_H3_REFERENCE_AUDIOS,
    },
    inject={
        "prompt": T("138", "value", "PrimitiveStringMultiline"),
        "width": T("136", "width", "MiniMaxH3ReferenceToVideo"),
        "height": T("136", "height", "MiniMaxH3ReferenceToVideo"),
        "duration": T("132", "value", "PrimitiveFloat"),
        "frames_expr": T("131", "", "ComfyMathExpression"),
        "steps": T("124", "steps", "BasicScheduler"),
        "save_prefix": T("92", "filename_prefix", "SaveVideo"),
    },
    # 参照素材は件数ぶんローダーを作って ref_*_N に繋ぐ（テンプレートの 137 /
    # 140+141 / 142 はその雛形で、組み立てのときに置き換わる）。参照動画は
    # LoadVideo -> GetVideoComponents で映像（出力 0）と音声（出力 1）に分け、
    # 同じ番号の ref_video_N / ref_video_audio_N の両方に繋ぐ。
    ref_media=RefMediaFan(
        node=T("136", "", "MiniMaxH3ReferenceToVideo"),
        image_loader=T("137", "image", "LoadImage"),
        video_loader=T("140", "file", "LoadVideo"),
        video_decoder=T("141", "video", "GetVideoComponents"),
        audio_loader=T("142", "audio", "LoadAudio"),
    ),
    seeds=(T("129", "noise_seed", "RandomNoise"),),
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + " /"
        + _MINIMAX_H3_MODELS.format(unet="ref2va")
    ),
)

#: ラテント連続性（Motion Context）の固定パラメータ。ComfyUI に入っている本家
#: ComfyUI-H3-Motion-Context v0.2.0 の `MiniMaxH3MotionContext` は
#: context_length（"22" / "5" / "39" / "56" の文字列コンボ）と
#: audio_context_length（INT・0 で映像窓に追従）だけを受け取る。既定どおり
#: context_length = "22"（ツールチップ曰く「22 is nearly seamless」で、頭で
#: 捨てるフレームが 39 より少なく尺への影響が小さい）・audio_context_length = 0。
#: encode_mode / anchor_mode / crop / audio_mode は v0.2.0 には入力として存在せず
#: ノード内部で固定されている。テンプレートに直接書いてあり、ジョブからは動かせない
#: （つまみを増やすほど組み合わせの検証が効かなくなる）。
_MINIMAX_H3_CONTEXT_NOTES = (
    " / ラテント連続性: 直前カットの mp4（`reference_video`）とサンプラー出力の"
    "AV ラテント（`context_latent`）を `MiniMaxH3MotionContext` に渡し、"
    "ReferenceToVideo の CONDITIONING に直前カットの末尾フレームと音を追記する"
    "（context_length \"22\" / audio_context_length 0 = 映像窓に追従、の固定値。"
    "encode_mode / anchor_mode / crop / audio_mode は本家 v0.2.0 には入力として"
    "存在せずノード内部で固定）/ ピン留めした 22 フレームが出力の先頭に返ってくる。"
    "`MiniMaxH3MotionContextTrim` で映像と音声を揃えて落とすため、"
    "**仕上がりの尺は指定した尺より 22 フレーム（24fps で約 0.9 秒）短くなる** /"
    " `context_latent` は**生成するクリップと同じ解像度**でなければならない /"
    " サンプラー出力は `MiniMaxH3MotionContextSaveLatent` でも保存し、"
    "そのパスを `PreviewAny` 経由で受け取って次のカットに渡す /"
    " `MiniMaxH3MotionContext` 系のカスタムノード"
    "（ComfyUI-H3-Motion-Context + ComfyUI-MiniMaxH3-Contex-Loop）が"
    "入った ComfyUI でしか動かない（Comfy Cloud では選べない）"
)

MINIMAX_H3_R2V_CONTEXT = replace(
    MINIMAX_H3_R2V,
    id="minimax_h3_r2v_context",
    studio_only=True,
    label="参照素材→動画・音声つき・連続カット (MiniMax H3 r2v + Motion Context)",
    mode_label="参照素材→動画・音声つき・連続カット (r2v context)",
    relpath="video/minimax-h3/minimax_h3_r2v_context.json",
    description=(
        MINIMAX_H3_R2V.description
        + "こちらは**直前カットの続きとして**生成する版で、直前カットの動画"
        "（`reference_video`）と保存しておいた AV ラテント（`context_latent`）を"
        "Motion Context に渡し、動き・音・見た目をつないだまま次のカットを作る。"
        "スタジオの「ラテント連続性」がこれを使う。"
    ),
    # 直前カットの動画は必須（無ければ連続カットにならない）。参照素材の
    # 1 件以上必須（:func:`app.models.reference_problem`）は r2v と同じ。
    requires=("video",),
    inject={
        **MINIMAX_H3_R2V.inject,
        # 直前カットの mp4（LoadVideo -> GetVideoComponents で映像を取り出す）
        "video": T("150", "file", "LoadVideo"),
        # 直前カットの AV ラテント。ComfyUI 側のパス文字列で、上げ直しはしない
        "context_latent": T(
            "152", "latent_path", "MiniMaxH3MotionContextLoadLatent"
        ),
        # このカットのラテントの保存先（次のカットが `context_latent` で読む）
        "save_latent_prefix": T(
            "155", "filename_prefix", "MiniMaxH3MotionContextSaveLatent"
        ),
    },
    # SaveLatent は STRING を返すだけで /history に何も残さないので、
    # `PreviewAny` に通した先を読む（:func:`app.jobs._pick_text`）。
    latent_output_node="156",
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_CONTEXT_NOTES
        + " /"
        + _MINIMAX_H3_MODELS.format(unet="ref2va")
    ),
)

#: 保存付きバリアント（``*_save``）の説明と注記。素の t2v / i2v / r2v に
#: `MiniMaxH3MotionContextSaveLatent` + `PreviewAny` の 2 ノードだけを足したもので、
#: Motion Context（読み込み・Trim）は入っていない: **連鎖の起点になるカット**も
#: 次のカットに渡す AV ラテントを残すためだけの版。素の JSON は触っていないので、
#: ラテント連続性 OFF のプロジェクトと Comfy Cloud は今までどおり素の版を使う。
_MINIMAX_H3_SAVE_DESCRIPTION = (
    "こちらはスタジオの「ラテント連続性」が ON のときに使う版で、"
    "サンプラー出力の AV ラテントを保存する以外は素の版とまったく同じ"
    "（入力の指定も仕上がりも変わらない）。保存したラテントは、次のカットを"
    "`minimax_h3_r2v_context` で作るときの引き継ぎ元になる。"
)

_MINIMAX_H3_SAVE_NOTES = (
    " / latent_continuity ON のスタジオ生成用: サンプラー出力を"
    "`MiniMaxH3MotionContextSaveLatent` で保存し、そのパスを `PreviewAny` 経由で"
    "受け取って次のカットに渡す。**AV ラテントを保存する以外は素の版と同じ**で、"
    "Motion Context の読み込み・Trim は入っていない（尺も素の版のまま）/"
    " `MiniMaxH3MotionContextSaveLatent` はカスタムノード"
    "（ComfyUI-H3-Motion-Context + ComfyUI-MiniMaxH3-Contex-Loop）なので、"
    "入っていない ComfyUI では動かない（Comfy Cloud では選べない）"
)

#: このカットのラテントの保存先（次のカットが `context_latent` で読む）と、
#: そのパスを持ち帰る `PreviewAny`。ノード ID は連続カット版と同じ 155 / 156。
_MINIMAX_H3_SAVE_LATENT_INJECT = {
    "save_latent_prefix": T(
        "155", "filename_prefix", "MiniMaxH3MotionContextSaveLatent"
    ),
}

MINIMAX_H3_T2V_SAVE = replace(
    MINIMAX_H3_T2V,
    id="minimax_h3_t2v_save",
    studio_only=True,
    label="テキスト→動画・音声つき・ラテント保存 (MiniMax H3 t2v + Save Latent)",
    mode_label="テキスト→動画・音声つき・ラテント保存 (t2v save)",
    relpath="video/minimax-h3/minimax_h3_t2v_save.json",
    description=MINIMAX_H3_T2V.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={**MINIMAX_H3_T2V.inject, **_MINIMAX_H3_SAVE_LATENT_INJECT},
    # SaveLatent は STRING を返すだけで /history に何も残さないので、
    # `PreviewAny` に通した先を読む（:func:`app.jobs._pick_text`）。
    latent_output_node="156",
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + " /"
        + _MINIMAX_H3_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2V_SAVE = replace(
    MINIMAX_H3_I2V,
    id="minimax_h3_i2v_save",
    studio_only=True,
    label="画像→動画・音声つき・ラテント保存 (MiniMax H3 i2v + Save Latent)",
    mode_label="画像→動画・音声つき・ラテント保存 (i2v save)",
    relpath="video/minimax-h3/minimax_h3_i2v_save.json",
    description=MINIMAX_H3_I2V.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={**MINIMAX_H3_I2V.inject, **_MINIMAX_H3_SAVE_LATENT_INJECT},
    latent_output_node="156",
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + " /"
        + _MINIMAX_H3_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_R2V_SAVE = replace(
    MINIMAX_H3_R2V,
    id="minimax_h3_r2v_save",
    studio_only=True,
    label="参照素材→動画・音声つき・ラテント保存 (MiniMax H3 r2v + Save Latent)",
    mode_label="参照素材→動画・音声つき・ラテント保存 (r2v save)",
    relpath="video/minimax-h3/minimax_h3_r2v_save.json",
    description=MINIMAX_H3_R2V.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={**MINIMAX_H3_R2V.inject, **_MINIMAX_H3_SAVE_LATENT_INJECT},
    latent_output_node="156",
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + " /"
        + _MINIMAX_H3_MODELS.format(unet="ref2va")
    ),
)

#: turbo 版は素の i2v / r2v と**入力の形が完全に同じ**（受け取る論理入力も
#: プロンプトの書き方も変わらない）ので、宣言は :func:`dataclasses.replace` で
#: 差分だけを書く。テンプレート側でノード ID を素の連番に振り直してあるので、
#: i2v turbo だけは ``inject`` / ``seeds`` も宣言し直す（r2v は元から連番なので
#: そのまま使い回せる）。
_MINIMAX_H3_TURBO_DESCRIPTION = (
    "サンプリングは 4 ステップ固定で、素の版よりずっと速く上がる（4step 蒸留 "
    "LoRA と Sage Attention / Sol-Attn / Spectrum を焼き込んだ高速版）。"
    "入力の指定は素の版とまったく同じだが、専用の量子化ウェイトと "
    "MiniMax H3 系のカスタムノード一式が入った環境でのみ動く。"
)

MINIMAX_H3_T2V_TURBO = replace(
    MINIMAX_H3_T2V,
    id="minimax_h3_t2v_turbo",
    label="テキスト→動画・音声つき (MiniMax H3 t2v Turbo)",
    mode_label="テキスト→動画・音声つき (t2v Turbo)",
    relpath="video/minimax-h3/minimax_h3_t2v_turbo.json",
    description=MINIMAX_H3_T2V.description + _MINIMAX_H3_TURBO_DESCRIPTION,
    inject={
        "prompt": T("136", "prompt", "MiniMaxH3ImageToVideo"),
        "width": T("136", "width", "MiniMaxH3ImageToVideo"),
        "height": T("136", "height", "MiniMaxH3ImageToVideo"),
        "duration": T("132", "value", "PrimitiveFloat"),
        "frames_expr": T("131", "", "ComfyMathExpression"),
        "steps": T("124", "steps", "BasicScheduler"),
        "save_prefix": T("92", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129", "noise_seed", "RandomNoise"),),
    selects={MINIMAX_H3_LOW_VRAM_NAME: _MINIMAX_H3_LOW_VRAM_SELECT},
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2V_TURBO = replace(
    MINIMAX_H3_I2V,
    id="minimax_h3_i2v_turbo",
    label="画像→動画・音声つき (MiniMax H3 i2v Turbo)",
    mode_label="画像→動画・音声つき (i2v Turbo)",
    relpath="video/minimax-h3/minimax_h3_i2v_turbo.json",
    description=MINIMAX_H3_I2V.description + _MINIMAX_H3_TURBO_DESCRIPTION,
    inject={
        "prompt": T("136", "prompt", "MiniMaxH3ImageToVideo"),
        "width": T("136", "width", "MiniMaxH3ImageToVideo"),
        "height": T("136", "height", "MiniMaxH3ImageToVideo"),
        "duration": T("132", "value", "PrimitiveFloat"),
        "frames_expr": T("131", "", "ComfyMathExpression"),
        "steps": T("124", "steps", "BasicScheduler"),
        "image": T("114", "image", "LoadImage"),
        "end_image": T("116", "image", "LoadImage"),
        "save_prefix": T("92", "filename_prefix", "SaveVideo"),
    },
    seeds=(T("129", "noise_seed", "RandomNoise"),),
    selects={MINIMAX_H3_LOW_VRAM_NAME: _MINIMAX_H3_LOW_VRAM_SELECT},
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_R2V_TURBO = replace(
    MINIMAX_H3_R2V,
    id="minimax_h3_r2v_turbo",
    label="参照素材→動画・音声つき (MiniMax H3 r2v Turbo)",
    mode_label="参照素材→動画・音声つき (r2v Turbo)",
    relpath="video/minimax-h3/minimax_h3_r2v_turbo.json",
    description=MINIMAX_H3_R2V.description + _MINIMAX_H3_TURBO_DESCRIPTION,
    selects={MINIMAX_H3_LOW_VRAM_NAME: _MINIMAX_H3_LOW_VRAM_SELECT},
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="ref2va")
    ),
)

#: opt 版は turbo から 4step 蒸留 LoRA（``MiniMaxH3TurboLoRA``）を抜いただけの
#: テンプレートなので、宣言も turbo と同じ形。ただし **`low_vram` は持たない**:
#: あの選択式が書き込む先はノード 150（TurboLoRA）で、opt にはそのノードが無い。
_MINIMAX_H3_OPT_DESCRIPTION = (
    "サンプリングは素の版と同じ 20 ステップで、品質は素の版相当のまま実行だけ"
    "速い最適化版（4step 蒸留 LoRA は使わず、量子化ウェイトと Sage Attention /"
    " Mem Eff Patch / Sol-Attn / SigmaShift / Spectrum だけを焼き込んである）。"
    "入力の指定は素の版とまったく同じだが、専用の量子化ウェイトと "
    "MiniMax H3 系のカスタムノード一式が入った環境でのみ動く。"
)

MINIMAX_H3_T2V_OPT = replace(
    MINIMAX_H3_T2V,
    id="minimax_h3_t2v_opt",
    label="テキスト→動画・音声つき (MiniMax H3 t2v Optimized)",
    mode_label="テキスト→動画・音声つき (t2v Optimized)",
    relpath="video/minimax-h3/minimax_h3_t2v_opt.json",
    description=MINIMAX_H3_T2V.description + _MINIMAX_H3_OPT_DESCRIPTION,
    # テンプレートのノード ID は turbo と同じ連番なので、turbo と同じ宣言を使う
    inject=dict(MINIMAX_H3_T2V_TURBO.inject),
    seeds=MINIMAX_H3_T2V_TURBO.seeds,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2V_OPT = replace(
    MINIMAX_H3_I2V,
    id="minimax_h3_i2v_opt",
    label="画像→動画・音声つき (MiniMax H3 i2v Optimized)",
    mode_label="画像→動画・音声つき (i2v Optimized)",
    relpath="video/minimax-h3/minimax_h3_i2v_opt.json",
    description=MINIMAX_H3_I2V.description + _MINIMAX_H3_OPT_DESCRIPTION,
    # テンプレートのノード ID は turbo と同じ連番なので、turbo と同じ宣言を使う
    inject=dict(MINIMAX_H3_I2V_TURBO.inject),
    seeds=MINIMAX_H3_I2V_TURBO.seeds,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_R2V_OPT = replace(
    MINIMAX_H3_R2V,
    id="minimax_h3_r2v_opt",
    label="参照素材→動画・音声つき (MiniMax H3 r2v Optimized)",
    mode_label="参照素材→動画・音声つき (r2v Optimized)",
    relpath="video/minimax-h3/minimax_h3_r2v_opt.json",
    description=MINIMAX_H3_R2V.description + _MINIMAX_H3_OPT_DESCRIPTION,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="ref2va")
    ),
)

#: ラテント連続性（``*_save`` / ``*_context``）と品質（turbo / opt）を掛け合わせた
#: バリアント。テンプレートは「turbo / opt の JSON + 保存の 2 ノード」（連続カット版は
#: さらに Motion Context の 5 ノード）で、素の版と同じ作りをそのまま踏襲している。
#: ただし**ノード ID だけが素の版と違う**: 素の ``*_save`` / ``*_context`` は保存の
#: 2 ノードを 155 / 156 に置いているが、turbo / opt のテンプレートは高速化パッチの
#: チェーンで 150〜155 を使い切っているので、こちらは 10 ずらして 160〜166 に置く。
_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT = {
    "save_latent_prefix": T(
        "165", "filename_prefix", "MiniMaxH3MotionContextSaveLatent"
    ),
}

#: 保存の 2 ノードのうち、パスを持ち帰る ``PreviewAny``（:attr:`latent_output_node`）
_MINIMAX_H3_QUALITY_LATENT_OUTPUT = "166"

MINIMAX_H3_T2V_SAVE_TURBO = replace(
    MINIMAX_H3_T2V_TURBO,
    id="minimax_h3_t2v_save_turbo",
    studio_only=True,
    label=(
        "テキスト→動画・音声つき・ラテント保存"
        " (MiniMax H3 t2v Turbo + Save Latent)"
    ),
    mode_label="テキスト→動画・音声つき・ラテント保存 (t2v Turbo save)",
    relpath="video/minimax-h3/minimax_h3_t2v_save_turbo.json",
    description=MINIMAX_H3_T2V_TURBO.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={
        **MINIMAX_H3_T2V_TURBO.inject,
        **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_T2V_SAVE_OPT = replace(
    MINIMAX_H3_T2V_OPT,
    id="minimax_h3_t2v_save_opt",
    studio_only=True,
    label=(
        "テキスト→動画・音声つき・ラテント保存"
        " (MiniMax H3 t2v Optimized + Save Latent)"
    ),
    mode_label="テキスト→動画・音声つき・ラテント保存 (t2v Optimized save)",
    relpath="video/minimax-h3/minimax_h3_t2v_save_opt.json",
    description=MINIMAX_H3_T2V_OPT.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={
        **MINIMAX_H3_T2V_OPT.inject,
        **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2V_SAVE_TURBO = replace(
    MINIMAX_H3_I2V_TURBO,
    id="minimax_h3_i2v_save_turbo",
    studio_only=True,
    label=(
        "画像→動画・音声つき・ラテント保存"
        " (MiniMax H3 i2v Turbo + Save Latent)"
    ),
    mode_label="画像→動画・音声つき・ラテント保存 (i2v Turbo save)",
    relpath="video/minimax-h3/minimax_h3_i2v_save_turbo.json",
    description=MINIMAX_H3_I2V_TURBO.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={
        **MINIMAX_H3_I2V_TURBO.inject,
        **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2V_SAVE_OPT = replace(
    MINIMAX_H3_I2V_OPT,
    id="minimax_h3_i2v_save_opt",
    studio_only=True,
    label=(
        "画像→動画・音声つき・ラテント保存"
        " (MiniMax H3 i2v Optimized + Save Latent)"
    ),
    mode_label="画像→動画・音声つき・ラテント保存 (i2v Optimized save)",
    relpath="video/minimax-h3/minimax_h3_i2v_save_opt.json",
    description=MINIMAX_H3_I2V_OPT.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={
        **MINIMAX_H3_I2V_OPT.inject,
        **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_R2V_SAVE_TURBO = replace(
    MINIMAX_H3_R2V_TURBO,
    id="minimax_h3_r2v_save_turbo",
    studio_only=True,
    label=(
        "参照素材→動画・音声つき・ラテント保存"
        " (MiniMax H3 r2v Turbo + Save Latent)"
    ),
    mode_label="参照素材→動画・音声つき・ラテント保存 (r2v Turbo save)",
    relpath="video/minimax-h3/minimax_h3_r2v_save_turbo.json",
    description=MINIMAX_H3_R2V_TURBO.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={
        **MINIMAX_H3_R2V_TURBO.inject,
        **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="ref2va")
    ),
)

MINIMAX_H3_R2V_SAVE_OPT = replace(
    MINIMAX_H3_R2V_OPT,
    id="minimax_h3_r2v_save_opt",
    studio_only=True,
    label=(
        "参照素材→動画・音声つき・ラテント保存"
        " (MiniMax H3 r2v Optimized + Save Latent)"
    ),
    mode_label="参照素材→動画・音声つき・ラテント保存 (r2v Optimized save)",
    relpath="video/minimax-h3/minimax_h3_r2v_save_opt.json",
    description=MINIMAX_H3_R2V_OPT.description + _MINIMAX_H3_SAVE_DESCRIPTION,
    inject={
        **MINIMAX_H3_R2V_OPT.inject,
        **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_SAVE_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="ref2va")
    ),
)

#: 連続カット版の Motion Context 一式（読み込み・引き継ぎ・Trim）の注入先。
#: 素の ``minimax_h3_r2v_context`` の 150 / 152 / 155 を 10 ずらしたもの。
_MINIMAX_H3_QUALITY_CONTEXT_INJECT = {
    "video": T("160", "file", "LoadVideo"),
    "context_latent": T(
        "162", "latent_path", "MiniMaxH3MotionContextLoadLatent"
    ),
    **_MINIMAX_H3_QUALITY_SAVE_LATENT_INJECT,
}

MINIMAX_H3_R2V_CONTEXT_TURBO = replace(
    MINIMAX_H3_R2V_TURBO,
    id="minimax_h3_r2v_context_turbo",
    studio_only=True,
    label=(
        "参照素材→動画・音声つき・連続カット"
        " (MiniMax H3 r2v Turbo + Motion Context)"
    ),
    mode_label="参照素材→動画・音声つき・連続カット (r2v Turbo context)",
    relpath="video/minimax-h3/minimax_h3_r2v_context_turbo.json",
    description=(
        MINIMAX_H3_R2V_CONTEXT.description + _MINIMAX_H3_TURBO_DESCRIPTION
    ),
    requires=("video",),
    inject={
        **MINIMAX_H3_R2V_TURBO.inject,
        **_MINIMAX_H3_QUALITY_CONTEXT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_CONTEXT_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="ref2va")
    ),
)

MINIMAX_H3_R2V_CONTEXT_OPT = replace(
    MINIMAX_H3_R2V_OPT,
    id="minimax_h3_r2v_context_opt",
    studio_only=True,
    label=(
        "参照素材→動画・音声つき・連続カット"
        " (MiniMax H3 r2v Optimized + Motion Context)"
    ),
    mode_label="参照素材→動画・音声つき・連続カット (r2v Optimized context)",
    relpath="video/minimax-h3/minimax_h3_r2v_context_opt.json",
    description=(
        MINIMAX_H3_R2V_CONTEXT.description + _MINIMAX_H3_OPT_DESCRIPTION
    ),
    requires=("video",),
    inject={
        **MINIMAX_H3_R2V_OPT.inject,
        **_MINIMAX_H3_QUALITY_CONTEXT_INJECT,
    },
    latent_output_node=_MINIMAX_H3_QUALITY_LATENT_OUTPUT,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_CONTEXT_NOTES
        + _MINIMAX_H3_OPT_NOTES
        + " /"
        + _MINIMAX_H3_OPT_MODELS.format(unet="ref2va")
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
    mode_label="XL SFT（音楽・歌もの）",
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
        "steps": T("3", "steps", "KSampler"),
        "save_prefix": T("107", "filename_prefix", "SaveAudioMP3"),
    },
    notes="acestep_v1.5_xl_sft / 出力 MP3・歌詞ありでボーカル、なしでインスト",
)

STABLE_AUDIO_3 = WorkflowSpec(
    id="stable_audio_3_medium_base",
    label="Stable Audio 3 Medium（効果音・環境音・音楽）",
    mode_label="Medium base（効果音・環境音・音楽）",
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
        "steps": T("52:3", "steps", "KSampler"),
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



SPECS: tuple[WorkflowSpec, ...] = (
    KREA2_TURBO,
    ANIMA,
    Z_IMAGE_TURBO,
    QWEN_IMAGE_EDIT,
    GROK_IMAGINE_T2I,
    GROK_IMAGINE_EDIT,
    MINIMAX_H3_T2V,
    MINIMAX_H3_T2V_SAVE,
    MINIMAX_H3_T2V_TURBO,
    MINIMAX_H3_T2V_SAVE_TURBO,
    MINIMAX_H3_T2V_OPT,
    MINIMAX_H3_T2V_SAVE_OPT,
    MINIMAX_H3_I2V,
    MINIMAX_H3_I2V_SAVE,
    MINIMAX_H3_I2V_TURBO,
    MINIMAX_H3_I2V_SAVE_TURBO,
    MINIMAX_H3_I2V_OPT,
    MINIMAX_H3_I2V_SAVE_OPT,
    MINIMAX_H3_R2V,
    MINIMAX_H3_R2V_SAVE,
    MINIMAX_H3_R2V_CONTEXT,
    MINIMAX_H3_R2V_TURBO,
    MINIMAX_H3_R2V_SAVE_TURBO,
    MINIMAX_H3_R2V_CONTEXT_TURBO,
    MINIMAX_H3_R2V_OPT,
    MINIMAX_H3_R2V_SAVE_OPT,
    MINIMAX_H3_R2V_CONTEXT_OPT,
    ACE_STEP_1_5,
    STABLE_AUDIO_3,
)

BY_ID: dict[str, WorkflowSpec] = {spec.id: spec for spec in SPECS}

DEFAULT_IMAGE_WORKFLOW = KREA2_TURBO.id
#: 開始フレームを受け取れて（``mode: "full"`` の 2 段目になれる）映像と音声を
#: 同時に作る、いちばん素直な動画ワークフロー
DEFAULT_VIDEO_WORKFLOW = MINIMAX_H3_I2V.id
#: 開始フレームを**取らない**ぶんの既定（廃止されたワークフローの id を持つ古い
#: ジョブを、開始フレーム無しで再実行するときの寄せ先）
DEFAULT_T2V_WORKFLOW = MINIMAX_H3_T2V.id
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


def comfy_specs() -> tuple[WorkflowSpec, ...]:
    """``workflow/*.json`` のテンプレートを持つワークフローだけ。

    テンプレートを読むもの（モデルスロットの列挙、custom node の存在確認、
    マニフェスト検証）は外部バックエンドのワークフローを見てはいけない。
    """
    return tuple(spec for spec in SPECS if spec.backend == "comfyui")


def specs_of_kind(kind: WorkflowKind) -> list[WorkflowSpec]:
    """``kind`` の**全**ワークフロー（:attr:`WorkflowSpec.studio_only` も含む）。

    「そのモデルで焼けるか」を見るもの（マニフェスト検証・テンプレートの
    組み立て）は、選択肢に出さないバリアントも見なければならない。
    """
    return [spec for spec in SPECS if spec.kind == kind]


def selectable_specs(kind: WorkflowKind) -> list[WorkflowSpec]:
    """UI・エージェントに出す ``kind`` のワークフロー（SPEC §2.2）。

    :attr:`WorkflowSpec.studio_only` のバリアント（``*_save`` / ``*_context``）は
    落とす。生成フォームに出さないのはもちろん、**エージェントのカタログからも
    外す**: ラテント連続性はプロジェクトの設定から
    :func:`app.studio._plan_render` が自動で解決する建て付けで、エージェントが
    書くのは論理モード（t2v / i2v / r2v）までというのが既存の取り決めだから
    （素の版と入力の形も仕上がりも同じなので、選ばせても得るものが無く、
    「ラテント連続性 OFF なのに保存版」のような矛盾だけが増える）。
    id 直指定（:func:`get_spec`）は落とさないので、スタジオの解決・ジョブの
    実行・外部 API の直指定は従来どおり通る。
    """
    return [spec for spec in specs_of_kind(kind) if not spec.studio_only]


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
    #: model family (``krea2`` / ``anima`` / ``z-image`` / ``qwen-image`` / ``minimax-h3``)
    family: str
    description: str
    #: ``(JobCreate field, 日本語ラベル)`` of every input the workflow needs
    required_inputs: tuple[tuple[str, str], ...]
    #: ``(JobCreate field, 日本語ラベル)`` of inputs it accepts but does not need
    optional_inputs: tuple[tuple[str, str], ...]
    #: ``(JobCreate field, 日本語ラベル, 件数の上限)`` of the multi-file reference
    #: inputs it accepts (empty for every workflow without a reference mode)
    reference_inputs: tuple[tuple[str, str, int], ...]
    #: 参照素材の**下限**（種類を問わない合計。0 = 無くても走る）。ComfyUI 側で
    #: グラフに展開するワークフロー（MiniMax H3 r2v）だけが 1 以上を持つ。
    min_references: int
    #: 選択式どうしの相関（``(名前, 相手の名前, 相手に必要な値)``、§3.1）。
    #: 相手の選択式の値でしか効かない項目のための宣言。
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
        min_references=(
            spec.ref_media.min_refs if spec.ref_media is not None else 0
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
    """The families an image LoRA can be registered for, in UI order.

    外部バックエンド（Grok Build CLI）のワークフローはグラフを持たないので LoRA を
    差せず、モデル固有のプロンプトガイドも持たない。LoRA 登録の選択肢と
    エージェントのプロンプトガイド（:func:`app.prompts.image_prompt_guides_section`）
    の両方がここを読むので、**ComfyUI のワークフローのファミリーだけ**を並べる
    （SPEC §3.4 / §5.2）。
    """
    seen: list[str] = []
    for spec in image_specs():
        if spec.backend != "comfyui":
            continue
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
    # 長さを一切宣言しないのは「このモデルには尺の指定が無い」の意味で、
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


#: :class:`RefMediaFan` の 1 種類ぶんの検証材料
#: ``(論理名, 可変入力の接頭辞, 繋ぎ元の雛形, 期待する class_type)``。
#: 論理名が空の組（動画のサウンドトラック）は件数の上限を持たない。
_RefMediaKind = tuple[str, str, Target, str]


def _ref_media_kinds(fan: RefMediaFan) -> tuple[_RefMediaKind, ...]:
    """宣言している種類ぶんの ``(論理名, 接頭辞, 雛形, class_type)``。

    ``ref_video_audio_*`` は動画と同じ番号・同じ雛形（``GetVideoComponents``）を
    使うので、論理名を持たない 4 つ目の組として並ぶ。
    """
    kinds: list[_RefMediaKind] = [
        (REF_IMAGES_NAME, fan.image_prefix, fan.image_loader, "LoadImage")
    ]
    names = fan.names()
    if REF_VIDEOS_NAME in names:
        assert fan.video_loader is not None and fan.video_decoder is not None
        kinds += [
            (REF_VIDEOS_NAME, fan.video_prefix, fan.video_decoder, "GetVideoComponents"),
            ("", fan.video_audio_prefix, fan.video_decoder, "GetVideoComponents"),
        ]
    if REF_AUDIOS_NAME in names:
        assert fan.audio_loader is not None
        kinds.append((REF_AUDIOS_NAME, fan.audio_prefix, fan.audio_loader, "LoadAudio"))
    return tuple(kinds)


def _ref_media_problems(
    spec: WorkflowSpec, tpl: Workflow, check: Any
) -> list[str]:
    """:class:`RefMediaFan` の宣言がテンプレートと合っているか（SPEC §3.1）。

    ``check`` は :func:`validate_spec` の内側の関数で、ノードの実在と
    ``class_type`` のずれは**呼び出し側の一覧に**積まれる。ここが返すのは
    「上限が宣言されているか」「雛形が本当にその可変入力に繋がっているか」など、
    この宣言でしか意味を持たない検査。
    """
    fan = spec.ref_media
    assert fan is not None
    problems: list[str] = []
    check(fan.node, "ref_media.node")
    for target, origin in (
        (fan.image_loader, "image_loader"),
        (fan.video_loader, "video_loader"),
        (fan.video_decoder, "video_decoder"),
        (fan.audio_loader, "audio_loader"),
    ):
        if target is not None:
            check(target, f"ref_media.{origin}")
    if (fan.video_loader is None) != (fan.video_decoder is None):
        problems.append(
            f"{spec.id}.ref_media: video_loader and video_decoder come as a pair"
        )
    # 参照動画は LoadVideo -> GetVideoComponents の 2 段なので、雛形の時点で
    # デコーダが本当にローダーを読んでいることを見る
    if fan.video_loader is not None and fan.video_decoder is not None:
        node = tpl.get(fan.video_decoder.node_id)
        link = (node.get("inputs") or {}).get(fan.video_decoder.field) if isinstance(node, dict) else None
        if not isinstance(link, list) or len(link) != 2 or link[0] != fan.video_loader.node_id:
            problems.append(
                f"{spec.id}.ref_media: {fan.video_decoder.key} does not read the"
                f" loader {fan.video_loader.node_id!r}"
            )

    total = 0
    node = tpl.get(fan.node.node_id)
    inputs = (node.get("inputs") or {}) if isinstance(node, dict) else {}
    for name, prefix, loader, class_type in _ref_media_kinds(fan):
        if name:
            limit = spec.multi_inputs.get(name)
            if limit is None:
                problems.append(
                    f"{spec.id}.ref_media: multi_inputs[{name!r}] is not declared"
                    " (the app would have no upper bound)"
                )
            else:
                total += limit
        if loader.class_type != class_type:
            problems.append(
                f"{spec.id}.ref_media: {prefix}* is fed by"
                f" {loader.class_type!r}, expected {class_type!r}"
            )
        if not prefix:
            problems.append(f"{spec.id}.ref_media: a prefix is empty")
            continue
        wired = {key: value for key, value in inputs.items() if key.startswith(prefix)}
        if not wired:
            problems.append(
                f"{spec.id}.ref_media: {fan.node.node_id} has no {prefix}* input"
            )
        for key, link in wired.items():
            if not isinstance(link, list) or len(link) != 2 or link[0] != loader.node_id:
                problems.append(
                    f"{spec.id}.ref_media: {fan.node.node_id}.{key} does not read"
                    f" the loader {loader.node_id!r}"
                )
    if not 0 <= fan.min_refs <= total:
        problems.append(
            f"{spec.id}.ref_media: min_refs {fan.min_refs} is not in 0..{total}"
        )
    for name in spec.multi_inputs:
        if name not in fan.names():
            problems.append(
                f"{spec.id}.ref_media: multi_inputs[{name!r}] has nothing to"
                " connect it to"
            )
    return problems


def validate_external_spec(spec: WorkflowSpec) -> list[str]:
    """テンプレートを持たないワークフロー（Grok CLI）のマニフェスト検証（SPEC §5.2）。

    ComfyUI 側は「宣言したノードが本当にテンプレートに在るか」を見るが、外部の
    バックエンドにはグラフが無いので、代わりに**宣言そのものの筋が通っているか**
    を見る: テンプレート由来の項目を持っていないか、タスク宣言があるか、指示文に
    織り込む論理値が既知の語彙か。
    """
    problems = _validate_common(spec)
    if spec.relpath or spec.inject or spec.output_node:
        problems.append(
            f"{spec.id}: backend {spec.backend!r} does not use a ComfyUI template"
        )
    if spec.lora_chain is not None:
        problems.append(f"{spec.id}: backend {spec.backend!r} has no LoRA chain")
    if spec.ref_media is not None:
        problems.append(f"{spec.id}: backend {spec.backend!r} has no RefMediaFan")
    if spec.seeds:
        problems.append(f"{spec.id}: backend {spec.backend!r} has no seed input")
    if spec.backend != "grok_cli":
        problems.append(f"{spec.id}: backend {spec.backend!r} is not implemented yet")
        return problems

    task = spec.grok
    if task is None:
        return [*problems, f"{spec.id}: backend 'grok_cli' but no GrokImagineTask"]
    if spec.kind != "image":
        problems.append(f"{spec.id}: grok_cli only produces images (kind={spec.kind!r})")
    if "prompt" not in task.values:
        problems.append(f"{spec.id}.grok.values: 'prompt' is required")
    for name in task.values:
        if name not in GROK_VALUES:
            problems.append(
                f"{spec.id}.grok.values: unknown value {name!r}"
                f" (known: {', '.join(sorted(GROK_VALUES))})"
            )
    # 編集ツールは参照画像を読むので、`image` を受け取り必須にしていること
    if task.tool == "image_edit":
        if "image" not in task.values:
            problems.append(f"{spec.id}.grok: 'image_edit' needs the 'image' value")
        if "image" not in spec.requires:
            problems.append(f"{spec.id}.grok: 'image_edit' must require 'image'")
        if task.max_references < 1:
            problems.append(f"{spec.id}.grok.max_references: must be >= 1 for editing")
    elif task.max_references:
        problems.append(f"{spec.id}.grok.max_references: only 'image_edit' takes refs")
    return problems


def validate_spec(spec: WorkflowSpec, template: Workflow | None = None) -> list[str]:
    """Problems found in ``spec`` against its template (empty list == fine)."""
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

    # 参照素材の動的展開（:class:`RefMediaFan`）: 受け側と雛形が実在し、雛形が
    # 本当に ``ref_*`` に繋がっているか。ここがずれると、組み直しのときに雛形が
    # 消えずにダミーのファイル名が ComfyUI に残る。
    if spec.ref_media is not None:
        problems += _ref_media_problems(spec, tpl, check)

    # 任意の入力（:attr:`WorkflowSpec.optional_loaders`）: 落とす先が
    # ``inject`` にあり、雛形のノードを名指ししているか。
    for name in spec.optional_loaders:
        target = spec.inject.get(name)
        if target is None:
            problems.append(
                f"{spec.id}.optional_loaders: {name!r} has no inject target"
            )
        elif target.node_id not in tpl:
            problems.append(
                f"{spec.id}.optional_loaders: node {target.node_id!r} is missing"
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
