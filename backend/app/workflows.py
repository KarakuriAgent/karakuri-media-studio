"""Workflow template registry and per-template injection manifests (SPEC §3).

The app ships a folder of independent ComfyUI API-format graphs under
``workflow/``: four plain image workflows (Krea 2 turbo, Anima, Z-Image turbo and
Qwen-Image Edit 2511) plus MiniMax H3 Image (t2i / i2i / r2i × base / opt /
turbo), the MiniMax H3 video workflows and two audio workflows
(MiniMax Music 3 and Stable Audio 3 Medium).  Each one is described here by a
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
    #: **表示だけ**の日本語ラベル（``選ぶ値 -> 画面に出す文字列``、SPEC §3.1）。
    #: ComfyUI に送るのも API が受け取るのも :attr:`choices` の生の値のままで、
    #: ここは生成フォームの ``<option>`` に出す文字だけを差し替える
    #: （``decode_recommended`` のようなノード由来の enum は日本語のほうが読める）。
    #: 宣言の無い値は生の値をそのまま出すので、**宣言は任意**（既存の選択式は
    #: 空のままで従来どおり）。エージェントのカタログには生の値と併記する
    #: （:func:`app.prompts._select_lines`）。
    choice_labels: dict[str, str] = field(default_factory=dict)
    #: **注入先を持たず、ビルダーがグラフを組み替える**選択式か（SPEC §3.1）。
    #: ``latent_upscale`` のように「選んだ値でノードを足したり配線を変えたり
    #: する」つまみは 1 つの :class:`Target` では表せないので、
    #: :attr:`target` を ``None`` のままにしてこちらを立てる
    #: （:func:`validate_spec` の「注入先が無い」検査を通す印にもなる）。
    rewrites_graph: bool = False
    #: **既定以外を選んだときだけ**必要になるカスタムノードの ``class_type``。
    #: テンプレートには出てこない（組み替えで足す）ので、テンプレート由来の
    #: 接続先判定（:func:`app.workflow.uses_optional_class_types`）では拾えない。
    #: 宣言があると Comfy Cloud では :attr:`restricted_choice` に固定する。
    requires_class_types: tuple[str, ...] = ()
    #: :attr:`requires_class_types` を入れられない接続先で唯一許す値
    restricted_choice: str = ""

    def choices_for_target(self, comfy_target: str) -> tuple[str, ...]:
        """接続先 ``comfy_target`` で実際に選べる値（SPEC §3.1 / §5.1）。

        :func:`app.workflow.supported_on_target` の選択式版。Comfy Cloud には
        任意のカスタムノードを入れられないので、それを要求する選択式は
        :attr:`restricted_choice` の 1 つだけに絞る。
        """
        if comfy_target != "comfy_cloud" or not self.requires_class_types:
            return self.choices
        if self.restricted_choice not in self.choices:  # pragma: no cover - 宣言ミス
            return self.choices
        return (self.restricted_choice,)

    def fallback_for_target(self, comfy_target: str) -> str:
        """接続先 ``comfy_target`` で未指定のときに使う値。"""
        allowed = self.choices_for_target(comfy_target)
        return self.fallback if self.fallback in allowed else allowed[0]

    @property
    def fallback(self) -> str:
        """未指定・不正な値のときに使う値。"""
        if self.default and self.default in self.choices:
            return self.default
        return self.choices[0] if self.choices else ""

    def label_of(self, choice: str) -> str:
        """``choice`` を画面に出すときの文字（宣言が無ければ生の値）。"""
        return self.choice_labels.get(choice) or choice

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
    #: **1 枚目の参照画像だけ**を受ける固定の入力名（空 = 番号つきの入力に入れる）。
    #: MiniMax H3 Image の ``H3ReferenceEditPrepare`` は 1 枚目を必須の
    #: ``source_image``（``<Picture 1>``）として受け、2 枚目以降だけが任意の
    #: ``reference_image_2`` … になっているので、その形を宣言できるようにしてある。
    primary_image_field: str = ""
    #: 番号つきの入力に足すオフセット（``reference_image_2`` から始まる並びなら 1）。
    #: :attr:`primary_image_field` がある宣言では 1 枚目がそちらへ行くので、
    #: 2 枚目（``index`` 1）が ``reference_image_2`` になる。
    image_offset: int = 0
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
ImageFamily = Literal[
    "krea2", "anima", "z-image", "qwen-image", "minimax-h3-image"
]

#: 日本語ラベル（設定画面の LoRA フォームと一覧バッジ）
FAMILY_LABELS: dict[str, str] = {
    "krea2": "Krea 2",
    "anima": "Anima",
    "z-image": "Z-Image",
    "qwen-image": "Qwen-Image Edit",
    "minimax-h3-image": "MiniMax H3 Image",
    "minimax-h3": "MiniMax H3",
    "minimax-music": "MiniMax Music 3",
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
class UpscaleSpec:
    """**ラテントアップスケール**（2 パス）にグラフを組み替える宣言（SPEC §3.1）。

    テンプレートは常に 1 パスのままで、選択式 ``latent_upscale`` が ``on`` の
    ときだけ :func:`app.workflow.splice_latent_upscale` がノードを足す:

    1. 1 パス目は :attr:`first_pass_megapixels` の解像度で回す（``width`` /
       ``height`` の注入先に低い値を書く）。
    2. 1 パス目の denoised_output を ``LTXVSeparateAVLatent`` で映像と音声に
       分け、映像だけ ``MinimaxH3LatentUpscaler3D`` で**最終解像度**に拡大して
       ``LTXVConcatAVLatent`` で戻す。
    3. ``ManualSigmas``（:attr:`sigmas`）で 2 パス目の
       ``SamplerCustomAdvanced`` を回す。noise / guider / sampler は 1 パス目と
       同じものを共有する。
    4. 1 パス目を読んでいた ``VAEDecode`` / ``VAEDecodeAudio`` の ``samples``
       だけを 2 パス目に付け替える。1 パス目のラテントの保存
       （``MiniMaxH3MotionContextSaveLatent``）と 1 個目の Motion Context は
       **1 パス目のまま**（入力名が ``latent`` なので付け替えの対象にならない）。
    5. **2 段引き継ぎ**: ラテント連続性のバリアントでは、2 パス目のラテントも
       2 個目の ``…SaveLatent``（:attr:`hires_save_node`）で保存し、そのパスを
       2 個目の ``PreviewAny`` で持ち帰る。連続カット版はさらに、直前カットの
       高解像度ラテントを 2 個目の ``…LoadLatent`` で読んで 2 個目の
       ``MiniMaxH3MotionContext`` + ``BasicGuider`` を組み、2 パス目の
       サンプラーの ``guider`` をそちらへ付け替える。直前カットに高解像度
       ラテントが無ければ（``off`` で作った過去テイクなど）この 3 ノードは
       足さず、2 パス目は 1 パス目と同じ guider を共有する = 1 段引き継ぎ。

    ``off`` のときは何もしないので、グラフはテンプレートそのままになる。

    **チェーンの途中で解像度や ``latent_upscale`` を変えられない**:
    ``MiniMaxH3MotionContext`` の ``context_latent`` は生成するクリップと同じ
    解像度でなければならないので、前のカットと違う解像度・違う
    ``latent_upscale`` で続きを作ると ComfyUI 側で落ちる。
    """

    #: 1 パス目の ``SamplerCustomAdvanced`` のノード ID。素の t2v / i2v は
    #: サブグラフを展開した ``105:14``、それ以外のテンプレートは ``125``。
    sampler: str
    #: 1 パス目を回す解像度（メガピクセル）。縦横比はジョブの指定のまま。
    first_pass_megapixels: float = 0.2
    #: ``MinimaxH3LatentUpscaler3D`` のモデルファイル（設定ページで差し替え可）
    model_name: str = "minimax_h3_latent_upscaler_3d_bf16.safetensors"
    #: 2 パス目の ``ManualSigmas``（全バリアント共通の固定値）
    sigmas: str = "0.9035, 0.6316, 0.3158, 0.0000"
    #: アップスケーラの画素グリッド（32 未満だと下端に光の帯が出る）
    align: int = 32
    device: str = "cuda"
    precision: str = "bf16"
    #: 足すノードの ID。既存のテンプレート（最大 166）とも、サブグラフ由来の
    #: ``105:xx`` とも衝突しない数字文字列にしてある。
    separate_node: str = "900"
    upscaler_node: str = "901"
    concat_node: str = "902"
    sigmas_node: str = "903"
    second_sampler_node: str = "904"

    # --- 2 段引き継ぎ（ラテント連続性 × ``latent_upscale`` on）---------------
    #
    # ラテント連続性のバリアント（``*_save*`` / ``*_context*``）で ``on`` の
    # ときだけ足す。継ぎ目を**最終解像度でも**合わせるために、1 パス目
    # （0.2MP）と 2 パス目（最終解像度）の**両方**のラテントを保存し、次の
    # カットは 2 本とも読む（:func:`app.workflow.splice_latent_upscale`）。
    #: 2 パス目のラテントを保存する 2 個目の ``…SaveLatent`` と、そのパスを
    #: 持ち帰る 2 個目の ``PreviewAny``
    hires_save_node: str = "905"
    hires_preview_node: str = "906"
    #: 直前カットの**高解像度**ラテントを読む 2 個目の ``…LoadLatent``
    hires_load_node: str = "907"
    #: 2 パス目用の 2 個目の ``MiniMaxH3MotionContext`` と、その
    #: CONDITIONING を受ける 2 個目の ``BasicGuider``（2 パス目の
    #: ``SamplerCustomAdvanced`` はこちらの guider を使う）
    hires_context_node: str = "908"
    hires_guider_node: str = "909"
    #: 2 パス目のラテントの保存先に付ける接尾辞（1 本目と別ファイルにする）
    hires_suffix: str = "_hires"

    def node_ids(self) -> tuple[str, ...]:
        """組み替えで足すノードの ID（衝突検査用）。"""
        return (
            self.separate_node,
            self.upscaler_node,
            self.concat_node,
            self.sigmas_node,
            self.second_sampler_node,
            self.hires_save_node,
            self.hires_preview_node,
            self.hires_load_node,
            self.hires_context_node,
            self.hires_guider_node,
        )


#: ラテントアップスケールの選択式の論理名（ジョブの ``selects`` のキー）
LATENT_UPSCALE_NAME = "latent_upscale"

#: ラテントアップスケーラのカスタムノード（Comfyui_Minimax_h3_latent_Upscaler）
LATENT_UPSCALER_CLASS = "MinimaxH3LatentUpscaler3D"


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
    #: ラテントアップスケール（2 パス）への組み替え方（:class:`UpscaleSpec`、
    #: ``None`` = 非対応）。宣言があるワークフローだけが選択式 ``latent_upscale``
    #: を持てる（:func:`validate_spec` が対で宣言されているか見る）。
    upscale: "UpscaleSpec | None" = None
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
# image: workflow/image/minimax-h3-image/*.json
# --------------------------------------------------------------------------
#
# MiniMax H3 を**静止画 1 枚のために**回すワークフロー（カスタムノード
# ComfyUI-MiniMax-H3-Image-Studio）。H3 は音声つき動画のモデルなので、内部では
# 短いフレームパケット（``quality_profile`` の選択式で 5 / 9 / 13 / 20 フレーム。
# 既定は 5）を作ってデコードし、その中から 1 枚を選んで出す。枚数を上げるほど
# 時間方向の文脈が増えて仕上がりが良くなり、そのぶん遅く VRAM も要る。動画側と
# 同じウェイトを使うが、音声 VAE は要らない。
#
# 3 つのモードがあり、ノードとウェイトが対応する:
#
# * t2i … ``H3TextToImagePrepare``（fl2va）。プロンプトだけ
# * i2i … ``H3ImageToImagePrepare``（fl2va）。編集元画像をフレーム 0 に置く
# * r2i … ``H3ReferenceEditPrepare``（ref2va）。参照画像 1〜9 枚を順番に受け、
#   プロンプトからは ``<Picture 1>`` … で呼ぶ
#
# **fl2va と ref2va のアダプタは混ぜられない**（Turbo LoRA も別物）。
#
# examples/api の JSON からの主な差分:
#
# * ``H3ImageResolutionPreset`` は使わない。アプリは縦横比とメガピクセルから
#   幅・高さを自分で計算して持っている（§3.1）ので、``H3*Prepare`` の
#   ``width`` / ``height`` に整数を直接入れる（z-image と同じ形）。
# * ``H3ImageSamplingPreset``（プリセットのコンボ）ではなく
#   ``H3SamplingSettings``（Advanced Sampling）を使う。sampler / scheduler /
#   steps が素の widget になるので、フォームのサンプリング回数がそのまま効く。
# * r2i の参照画像は雛形の ``LoadImage`` 1 つだけを繋いだ状態で持ち、ビルダー
#   （:func:`app.workflow._build_ref_media`）が渡された枚数ぶんに組み直す
#   （:class:`RefMediaFan`）。1 枚目は必須の ``source_image``、2 枚目以降が
#   ``reference_image_2`` … なので、:attr:`RefMediaFan.primary_image_field` と
#   :attr:`RefMediaFan.image_offset` でその形を宣言している。
#
# CFG を使わない（``BasicGuider``）ので **negative prompt は無い**。

#: H3 の native canvas（約 1344x768 = 0.98MP。README の "native detail" と同値）。
#: 動画側（0.4MP）より広いのは、静止画は既定で 5 フレームしか作らないため
#: （``quality_profile`` を 20 フレームに上げるなら解像度は控えめに）。
MINIMAX_H3_IMAGE_MEGAPIXELS = 0.98

#: r2i が受け取れる参照画像の枚数（``H3ReferenceEditPrepare`` の
#: ``source_image`` + ``reference_image_2..9`` = ``MAX_REFERENCE_IMAGES``）
MINIMAX_H3_IMAGE_REFERENCES = 9

#: 3 つのモードで共通の注記（素性と要件）
_MINIMAX_H3_IMAGE_NOTES = (
    "MiniMax H3（音声つき動画モデル）で静止画 1 枚を作るカスタムノード"
    "（ComfyUI-MiniMax-H3-Image-Studio）/ 内部でフレームのパケットを作って"
    "デコードし、`Single Image Output` が 1 枚を選ぶ。**枚数（`quality_profile`）を"
    "上げるほど H3 が使える時間方向の文脈が増えて品質が上がるが、その分だけ"
    "遅く・VRAM を食う**（既定 5 / 9 / 13 / 20 フレーム）/"
    " 解像度は 32px グリッドに丸め、native canvas は約 1344x768（0.98MP）/"
    " negative prompt は無い（CFG 無しの BasicGuider）/"
    " ユーザー LoRA を挿すチェーンは持たない /"
    " 音声 VAE は使わない"
)

# --- 選択式のつまみ（SPEC §3.1）--------------------------------------------
#
# ノード側の widget をそのままフォームに出す。どれも ``CustomCombo`` ではないので
# 番号を書く先は無く（``index_field=""``）、値は文字列のまま注入する。真偽値の
# ``optimize_for_still`` と実数の ``source_fidelity`` だけは
# :func:`app.workflow._coerce` が型を合わせてから書き込む
# （:data:`app.workflow._BOOL_INPUTS` / :data:`app.workflow._FLOAT_SELECT_INPUTS`）。

#: 論理名（= ジョブの ``selects`` のキー）
MINIMAX_H3_IMAGE_QUALITY_NAME = "quality_profile"
MINIMAX_H3_IMAGE_STRATEGY_NAME = "frame_strategy"
MINIMAX_H3_IMAGE_STILL_NAME = "optimize_for_still"
MINIMAX_H3_IMAGE_FIDELITY_NAME = "source_fidelity"
MINIMAX_H3_IMAGE_FIT_NAME = "source_fit"
MINIMAX_H3_IMAGE_REF_DETAIL_NAME = "reference_detail"

#: ``H3*Prepare.quality_profile`` の選択肢（ノードの ``FRAME_PRESETS`` そのまま）
MINIMAX_H3_IMAGE_QUALITY_CHOICES: tuple[str, ...] = (
    "recommended | 5 frames",
    "extended quality | 9 frames",
    "high quality | 13 frames",
    "maximum quality | 20 frames (slow)",
)

#: 画面に出すときの日本語（送る値はノードの enum のまま。:attr:`SelectSpec.choice_labels`）
MINIMAX_H3_IMAGE_QUALITY_LABELS: dict[str, str] = {
    "recommended | 5 frames": "標準（5 フレーム）",
    "extended quality | 9 frames": "高品質（9 フレーム）",
    "high quality | 13 frames": "最高品質（13 フレーム）",
    "maximum quality | 20 frames (slow)": "最大品質（20 フレーム・低速）",
}

#: ``H3ImageFrameSelector.strategy`` のうち、このアプリで意味のあるもの。
#: ``manual_index`` は番号のつまみ（``manual_index``）を出していないので
#: ``first`` と同じ動きになるだけなので載せない。``most_similar_to_source`` /
#: ``balanced_edit`` は選択ノードに ``source_image`` が繋がっている
#: モード（i2i / r2i）だけ。
_MINIMAX_H3_IMAGE_STRATEGIES: tuple[str, ...] = (
    "decode_recommended",
    "stable_quality",
    "best_quality",
    "sharpest",
    "first",
    "middle",
    "last",
)
_MINIMAX_H3_IMAGE_EDIT_STRATEGIES: tuple[str, ...] = (
    *_MINIMAX_H3_IMAGE_STRATEGIES,
    "balanced_edit",
    "most_similar_to_source",
)

#: 画面に出すときの日本語（送る値はノードの enum のまま）
MINIMAX_H3_IMAGE_STRATEGY_LABELS: dict[str, str] = {
    "decode_recommended": "おまかせ（推奨フレーム）",
    "stable_quality": "安定重視",
    "best_quality": "品質重視",
    "sharpest": "最も鮮明",
    "first": "最初のフレーム",
    "middle": "中間のフレーム",
    "last": "最後のフレーム",
    "balanced_edit": "編集と再現のバランス",
    "most_similar_to_source": "元画像に最も近い",
}


def _minimax_h3_image_quality_select(prepare_class: str) -> SelectSpec:
    """フレームパケットの枚数（品質と速度のつまみ）。"""
    return SelectSpec(
        label="フレーム枚数（品質）",
        choices=MINIMAX_H3_IMAGE_QUALITY_CHOICES,
        default=MINIMAX_H3_IMAGE_QUALITY_CHOICES[0],
        target=T("5", "quality_profile", prepare_class),
        index_field="",
        choice_labels=MINIMAX_H3_IMAGE_QUALITY_LABELS,
        hint=(
            "H3 は 1 枚出すときも複数フレームを作る。枚数を増やすと時間方向の"
            "文脈が増えて仕上がりが良くなるが、そのぶん遅く・VRAM も要る"
            "（既定は 5 フレーム。20 フレームは目に見えて遅い）"
        ),
    )


def _minimax_h3_image_strategy_select(choices: tuple[str, ...]) -> SelectSpec:
    """デコードしたパケットから 1 枚を選ぶやり方。"""
    return SelectSpec(
        label="出力フレームの選び方",
        choices=choices,
        default="decode_recommended",
        target=T("12", "strategy", "H3ImageFrameSelector"),
        index_field="",
        # 宣言に無い選び方は生の値が出るだけなので、全部の enum を並べておく
        choice_labels={
            value: label
            for value, label in MINIMAX_H3_IMAGE_STRATEGY_LABELS.items()
            if value in choices
        },
        hint=(
            "既定の decode_recommended はノードの推奨フレームをそのまま使う。"
            "枚数を増やしたときは stable_quality / best_quality / sharpest で"
            "選び直せる（`balanced_edit` と `most_similar_to_source` は元画像・"
            "参照画像との近さを見るので、編集系のモードだけ）"
        ),
    )


def _minimax_h3_image_still_select(prepare_class: str) -> SelectSpec:
    """静止画向けのプロンプト包み（ノードの ``optimize_for_still``）。"""
    return SelectSpec(
        label="静止画プロンプト補正",
        choices=("on", "off"),
        default="on",
        target=T("5", "optimize_for_still", prepare_class),
        index_field="",
        choice_labels={"on": "する", "off": "しない"},
        hint=(
            "on はノードが「カメラ固定の 1 枚絵」を要求する文をプロンプトに"
            "足す（既定。フレーム数・解像度・サンプリングは変わらない）。"
            "off にすると書いた文がそのまま入るので、動画寄りの出力になりやすい"
        ),
    )


def _minimax_h3_image_fidelity_select(prepare_class: str) -> SelectSpec:
    """元画像・参照画像をどれだけ保てとプロンプトに書き足すか。"""
    return SelectSpec(
        label="元画像の保持強度",
        # ノードは 0.00〜1.00・刻み 0.05 の FLOAT。全刻みを並べても選べないので、
        # 実用的な段階だけを出す（値はそのまま float として注入される）。
        choices=("0.00", "0.25", "0.50", "0.75", "0.90", "1.00"),
        default="0.75",
        target=T("5", "source_fidelity", prepare_class),
        index_field="",
        choice_labels={
            "0.00": "0.00（保持を求めない）",
            "0.25": "0.25（弱め）",
            "0.50": "0.50",
            "0.75": "0.75（推奨）",
            "0.90": "0.90（強め）",
            "1.00": "1.00（できる限り保持）",
        },
        hint=(
            "**denoise 強度ではない**: 同一性・ポーズ・構図をどれだけ保てと"
            "プロンプトに書き足すかの強さ（既定 0.75）。大きく作り替えたいときは"
            "下げ、人物や構図を動かしたくないときは上げる"
        ),
    )


def _minimax_h3_image_fit_select(prepare_class: str) -> SelectSpec:
    """元画像を生成キャンバスに合わせるやり方。"""
    return SelectSpec(
        label="元画像の合わせ方",
        choices=("crop_center", "contain_pad", "stretch"),
        default="crop_center",
        target=T("5", "source_fit", prepare_class),
        index_field="",
        choice_labels={
            "crop_center": "中央でトリミング",
            "contain_pad": "全体を収める・余白あり",
            "stretch": "引き伸ばし",
        },
        hint=(
            "生成キャンバスに元画像を合わせるやり方（VAE に通す前）。"
            "crop_center は中央を切り抜き、contain_pad は余白を足して全体を残し、"
            "stretch は縦横比を無視して引き伸ばす"
        ),
    )

#: r2i の参照画像をどの解像度で VAE に通すか（``reference_detail``）
_MINIMAX_H3_IMAGE_REF_DETAIL_SELECT = SelectSpec(
    label="参照画像の解像度",
    choices=("match_generation_area", "max_identity_2048"),
    default="match_generation_area",
    target=T("5", "reference_detail", "H3ReferenceEditPrepare"),
    index_field="",
    choice_labels={
        "match_generation_area": "生成サイズに合わせる",
        "max_identity_2048": "顔・細部優先／短辺 2048px・低速",
    },
    hint=(
        "match_generation_area は生成解像度に合わせて縮小（既定・速い）。"
        "max_identity_2048 は短辺 2048px まで残すので顔などの同一性は上がるが、"
        "メモリと時間を数倍使う"
    ),
)

#: t2i の選択式（元画像を取らないので保持強度・合わせ方は無い）
_MINIMAX_H3_T2I_SELECTS: dict[str, SelectSpec] = {
    MINIMAX_H3_IMAGE_QUALITY_NAME: _minimax_h3_image_quality_select(
        "H3TextToImagePrepare"
    ),
    MINIMAX_H3_IMAGE_STRATEGY_NAME: _minimax_h3_image_strategy_select(
        _MINIMAX_H3_IMAGE_STRATEGIES
    ),
    MINIMAX_H3_IMAGE_STILL_NAME: _minimax_h3_image_still_select(
        "H3TextToImagePrepare"
    ),
}

#: i2i の選択式（元画像を取るので保持強度・合わせ方と編集系の選び方が増える）
_MINIMAX_H3_I2I_SELECTS: dict[str, SelectSpec] = {
    MINIMAX_H3_IMAGE_QUALITY_NAME: _minimax_h3_image_quality_select(
        "H3ImageToImagePrepare"
    ),
    MINIMAX_H3_IMAGE_STRATEGY_NAME: _minimax_h3_image_strategy_select(
        _MINIMAX_H3_IMAGE_EDIT_STRATEGIES
    ),
    MINIMAX_H3_IMAGE_STILL_NAME: _minimax_h3_image_still_select(
        "H3ImageToImagePrepare"
    ),
    MINIMAX_H3_IMAGE_FIDELITY_NAME: _minimax_h3_image_fidelity_select(
        "H3ImageToImagePrepare"
    ),
    MINIMAX_H3_IMAGE_FIT_NAME: _minimax_h3_image_fit_select(
        "H3ImageToImagePrepare"
    ),
}

#: r2i の選択式（i2i のぶん + 参照画像の解像度）
_MINIMAX_H3_R2I_SELECTS: dict[str, SelectSpec] = {
    MINIMAX_H3_IMAGE_QUALITY_NAME: _minimax_h3_image_quality_select(
        "H3ReferenceEditPrepare"
    ),
    MINIMAX_H3_IMAGE_STRATEGY_NAME: _minimax_h3_image_strategy_select(
        _MINIMAX_H3_IMAGE_EDIT_STRATEGIES
    ),
    MINIMAX_H3_IMAGE_STILL_NAME: _minimax_h3_image_still_select(
        "H3ReferenceEditPrepare"
    ),
    MINIMAX_H3_IMAGE_FIDELITY_NAME: _minimax_h3_image_fidelity_select(
        "H3ReferenceEditPrepare"
    ),
    MINIMAX_H3_IMAGE_FIT_NAME: _minimax_h3_image_fit_select(
        "H3ReferenceEditPrepare"
    ),
    MINIMAX_H3_IMAGE_REF_DETAIL_NAME: _MINIMAX_H3_IMAGE_REF_DETAIL_SELECT,
}

#: 全モード共通の選択式の注記
_MINIMAX_H3_IMAGE_SELECT_NOTES = (
    " / つまみ（`selects`）: `quality_profile`（フレーム枚数 5 / 9 / 13 / 20。"
    "上げるほど品質が上がり、そのぶん遅く VRAM も要る）・`frame_strategy`"
    "（デコードしたパケットから 1 枚を選ぶやり方）・`optimize_for_still`"
    "（静止画向けのプロンプト包み。既定 on）"
)

#: i2i / r2i だけの選択式の注記
_MINIMAX_H3_IMAGE_EDIT_SELECT_NOTES = (
    "・`source_fidelity`（元画像の保持強度 0.00〜1.00・既定 0.75）・`source_fit`"
    "（元画像の合わせ方）"
)

#: r2i だけの選択式の注記
_MINIMAX_H3_IMAGE_REF_SELECT_NOTES = (
    "・`reference_detail`（参照画像を生成解像度に合わせるか短辺 2048px まで残すか）"
)

#: t2i の注記のうち**モデルファイル以外**（素の版・opt・turbo で共通）
_MINIMAX_H3_T2I_COMMON_NOTES = (
    _MINIMAX_H3_IMAGE_NOTES + _MINIMAX_H3_IMAGE_SELECT_NOTES
)

#: 素の版のモデルファイル（t2i / i2i は fl2va、r2i は ref2va）。動画側の素の版と
#: 同じく量子化ウェイト（w4a8_mixed）と heretic の text encoder を使い、VAE だけ
#: fp16（int8_convrot は opt / turbo だけ）。
_MINIMAX_H3_IMAGE_MODELS = (
    " モデル: minimax_h3_{unet}_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_fp16（vae）+"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）/"
    " サンプリングは res_multistep・simple・20 ステップ"
)

#: opt / turbo 版が使う量子化ウェイト（動画側の turbo / opt と同じファイル）
_MINIMAX_H3_IMAGE_FAST_MODELS = (
    " モデル: minimax_h3_{unet}_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_int8_convrot（vae）+"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）"
)

#: opt 版だけの注記（素の版との差分）
_MINIMAX_H3_IMAGE_OPT_NOTES = (
    " / **opt**: サンプリングは素の版と同じ 20 ステップのまま、量子化ウェイトと"
    " Sage Attention / Mem Eff Sage Attention / Sol-Attn / Spectrum を"
    "テンプレートに直列で焼き込んだ最適化版（品質は素の版相当で実行だけ速い）。"
    "`PathchSageAttentionKJ`（ComfyUI-KJNodes + SageAttention）・"
    "`MiniMaxH3MemoryEfficientSageAttentionPatch`・`SolAttnPatch`・"
    "`SpectrumApplyMiniMaxH3` の**カスタムノードと量子化ウェイト一式が入った"
    "環境でのみ**動く（`MiniMaxH3SigmaShift` は使わない: sigma shift は"
    " Advanced Sampling 側が持っている）"
)

#: r2i の opt / turbo だけの注記。動画の r2v opt / turbo と同じで、ref2va の
#: 量子化ウェイトではなく **fl2va + 参照 LoRA** で参照モードにする。
_MINIMAX_H3_IMAGE_REF_LORA_NOTES = (
    " / r2i の opt / turbo だけは素の版と土台が違い、"
    "`minimax_h3_fl2va_pruned_w4a8_mixed` に参照 LoRA"
    "（`minimax_h3_ref_lora_rank_256_bf16`）を `LoraLoaderModelOnly`（ノード 144）で"
    "重ねてから `PathchSageAttentionKJ` 以降の連鎖に流す"
    "（ref2va の量子化ウェイトは使わない）"
)

#: turbo 版だけの注記（opt との差分）
_MINIMAX_H3_IMAGE_TURBO_NOTES = (
    " / **turbo**: opt に**公式の 4step 蒸留 LoRA**"
    "（`minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16`。"
    "`LoraLoaderModelOnly` で読むので設定画面から差し替え可）を足した高速版で、"
    "サンプリングは **4 ステップ**（t2i・i2i は res_multistep・simple、"
    "r2i は euler・simple）。r2i は蒸留 LoRA（ノード 143）→ 参照 LoRA"
    "（ノード 144）の順で 2 段重ねる"
)

#: i2i / r2i の共通の注記（source_fidelity はプロンプトの言い回しを変えるだけ）
_MINIMAX_H3_IMAGE_EDIT_NOTES = (
    " / `source_fidelity`（既定 0.75・`selects` で変更可）は**denoise 強度では"
    "なく**、元の同一性・ポーズ・構図をどれだけ保てとプロンプトに書き足すかの強さ /"
    " 元画像は生成キャンバスに合わせてから VAE に通す（既定 crop_center・"
    "`source_fit` で変更可）"
)

#: i2i の注記のうち**モデルファイル以外**（素の版・opt・turbo で共通）
_MINIMAX_H3_I2I_COMMON_NOTES = (
    _MINIMAX_H3_IMAGE_NOTES
    + _MINIMAX_H3_IMAGE_EDIT_NOTES
    + _MINIMAX_H3_IMAGE_SELECT_NOTES
    + _MINIMAX_H3_IMAGE_EDIT_SELECT_NOTES
)

#: r2i の注記のうち**モデルファイル以外**（素の版・opt・turbo で共通）
_MINIMAX_H3_R2I_COMMON_NOTES = (
    _MINIMAX_H3_IMAGE_NOTES
    + _MINIMAX_H3_IMAGE_EDIT_NOTES
    + f" / 参照画像は 1〜{MINIMAX_H3_IMAGE_REFERENCES} 枚必須"
    "（1 枚目が `source_image` = `<Picture 1>`、2 枚目以降が"
    " `reference_image_2` …）/ 参照画像は既定では生成キャンバスに合わせて縮小"
    "（`reference_detail` で短辺 2048px まで残せる）/ 開始フレームは受け取らない"
    + _MINIMAX_H3_IMAGE_SELECT_NOTES
    + _MINIMAX_H3_IMAGE_EDIT_SELECT_NOTES
    + _MINIMAX_H3_IMAGE_REF_SELECT_NOTES
)

MINIMAX_H3_T2I = WorkflowSpec(
    id="minimax_h3_t2i",
    label="テキスト→画像 (MiniMax H3 Image t2i)",
    mode_label="テキスト→画像 (t2i)",
    kind="image",
    family="minimax-h3-image",
    relpath="image/minimax-h3-image/minimax_h3_t2i.json",
    output_node="13",
    description=(
        "Text-to-image with the MiniMax H3 omni-modal model (fl2va weights):"
        " it samples a short 5-frame packet and returns the one still frame its"
        " own selector recommends. `image_prompt` only; the resolution comes"
        " from `aspect_ratio` + `megapixels`, rounded to a 32px grid, and the"
        " native canvas is about 1344x768 (0.98 MP). Usable for"
        ' `mode: "image_only"` and as the first stage of `mode: "full"`.'
    ),
    prompt_hint=(
        "One English paragraph describing the **finished still**: subject,"
        " wardrobe, pose, set, lighting, lens and framing. Never write video"
        " language (no shot lists, no camera moves, no timestamps, no audio"
        " fields) — the node adds its own locked-camera still wrapper. There is"
        " no negative prompt."
    ),
    resolution_multiple=32,
    default_megapixels=MINIMAX_H3_IMAGE_MEGAPIXELS,
    inject={
        # ``H3ImageResolutionPreset`` は使わず、整数を直接入れる（z-image と同じ）
        "width": T("5", "width", "H3TextToImagePrepare"),
        "height": T("5", "height", "H3TextToImagePrepare"),
        "prompt": T("5", "prompt", "H3TextToImagePrepare"),
        "seed": T("6", "noise_seed", "RandomNoise"),
        "steps": T("8", "steps", "H3SamplingSettings"),
        "save_prefix": T("13", "filename_prefix", "SaveImage"),
    },
    selects=_MINIMAX_H3_T2I_SELECTS,
    notes=(
        _MINIMAX_H3_T2I_COMMON_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2I = WorkflowSpec(
    id="minimax_h3_i2i",
    label="画像→画像 (MiniMax H3 Image i2i)",
    mode_label="画像→画像・編集 (i2i)",
    kind="image",
    family="minimax-h3-image",
    relpath="image/minimax-h3-image/minimax_h3_i2i.json",
    output_node="13",
    requires=("image",),
    image_label="編集元画像",
    description=(
        "Image **editing** with MiniMax H3 (fl2va): the picture given in"
        " `source_image` is encoded as frame 0 and `image_prompt` says what to"
        " change, so `source_image` is REQUIRED in every mode that runs the"
        ' image stage (including `mode: "full"`, where the edited still then'
        " becomes the video's start frame). The output canvas comes from"
        " `aspect_ratio` + `megapixels` (32px grid) and the source is fitted to"
        " it by centre crop. Write `image_prompt` as an edit instruction"
        ' ("change X to Y, keep everything else unchanged"), never as a full'
        " scene description."
    ),
    prompt_hint=(
        "An English EDIT instruction for `source_image`, not a scene"
        " description: name the change, then say explicitly what must stay"
        " (identity, pose, wardrobe, composition, background). No video"
        " language. There is no negative prompt."
    ),
    resolution_multiple=32,
    default_megapixels=MINIMAX_H3_IMAGE_MEGAPIXELS,
    inject={
        "width": T("5", "width", "H3ImageToImagePrepare"),
        "height": T("5", "height", "H3ImageToImagePrepare"),
        "prompt": T("5", "edit_instruction", "H3ImageToImagePrepare"),
        "image": T("0", "image", "LoadImage"),
        "seed": T("6", "noise_seed", "RandomNoise"),
        "steps": T("8", "steps", "H3SamplingSettings"),
        "save_prefix": T("13", "filename_prefix", "SaveImage"),
    },
    selects=_MINIMAX_H3_I2I_SELECTS,
    notes=(
        _MINIMAX_H3_I2I_COMMON_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_R2I = WorkflowSpec(
    id="minimax_h3_r2i",
    label="参照画像→画像 (MiniMax H3 Image r2i)",
    mode_label="参照画像→画像・参照編集 (r2i)",
    kind="image",
    family="minimax-h3-image",
    relpath="image/minimax-h3-image/minimax_h3_r2i.json",
    output_node="13",
    description=(
        f"Reference editing with MiniMax H3 ref2va: 1〜"
        f"{MINIMAX_H3_IMAGE_REFERENCES} 枚の参照画像（`reference_images`）の"
        "見た目を保ったまま、1 枚の静止画を作り直す。参照画像は「編集元」1 枚では"
        "なく**順番のあるリスト**で、プロンプトからは渡した順に `<Picture 1>`・"
        "`<Picture 2>` … と呼ぶ（1 枚目が主体・2 枚目以降が差し替える要素、という"
        "使い方が公式の例）。開始フレームは受け取らないので、`source_image` の"
        "代わりに `reference_images` に並べる。解像度は `aspect_ratio` +"
        " `megapixels`（32px グリッド）。"
    ),
    prompt_hint=(
        "REF2VA reference editing. Refer to every reference **by number, in the"
        " order it was given** — `<Picture 1>`, `<Picture 2>`, … — and say what"
        " each one contributes: e.g. `Keep the subject, pose, framing and"
        " background from <Picture 1>. Replace only the jacket with the one"
        " from <Picture 2>.` Never use a tag with nothing behind it. No video"
        " language, no negative prompt."
    ),
    resolution_multiple=32,
    default_megapixels=MINIMAX_H3_IMAGE_MEGAPIXELS,
    multi_inputs={REF_IMAGES_NAME: MINIMAX_H3_IMAGE_REFERENCES},
    inject={
        "width": T("5", "width", "H3ReferenceEditPrepare"),
        "height": T("5", "height", "H3ReferenceEditPrepare"),
        "prompt": T("5", "edit_instruction", "H3ReferenceEditPrepare"),
        "seed": T("6", "noise_seed", "RandomNoise"),
        "steps": T("8", "steps", "H3SamplingSettings"),
        "save_prefix": T("13", "filename_prefix", "SaveImage"),
    },
    # 1 枚目は必須の ``source_image``（``<Picture 1>``）、2 枚目以降が任意の
    # ``reference_image_2`` … なので、番号つきの入力は 1 つずらす。テンプレートの
    # 雛形（LoadImage 0）は両方に繋いであり、組み立てのときに置き換わる。
    ref_media=RefMediaFan(
        node=T("5", "", "H3ReferenceEditPrepare"),
        image_loader=T("0", "image", "LoadImage"),
        image_prefix="reference_image_",
        primary_image_field="source_image",
        image_offset=1,
    ),
    selects=_MINIMAX_H3_R2I_SELECTS,
    notes=(
        _MINIMAX_H3_R2I_COMMON_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_MODELS.format(unet="ref2va")
    ),
)

#: opt / turbo は素の版と**入力の形が完全に同じ**（ノード ID も揃えてあるので
#: ``inject`` / ``seeds`` / ``ref_media`` はそのまま使い回せる）ので、宣言は
#: :func:`dataclasses.replace` で差分だけを書く。
_MINIMAX_H3_IMAGE_OPT_DESCRIPTION = (
    " Optimised build: same 20 sampling steps and same inputs as the plain"
    " workflow, but with quantised weights and the Sage Attention / Sol-Attn /"
    " Spectrum patches baked into the template. It only runs on a ComfyUI that"
    " has those custom nodes and weights."
)

_MINIMAX_H3_IMAGE_TURBO_DESCRIPTION = (
    " Turbo build: the optimised graph plus the official 4-step distilled H3"
    " Turbo LoRA (4 sampling steps), so it finishes much faster. Same inputs as"
    " the plain workflow, but it only runs on a ComfyUI that has the H3 custom"
    " nodes, the quantised weights and the Turbo LoRA."
)

MINIMAX_H3_T2I_OPT = replace(
    MINIMAX_H3_T2I,
    id="minimax_h3_t2i_opt",
    label="テキスト→画像 (MiniMax H3 Image t2i Optimized)",
    mode_label="テキスト→画像 (t2i Optimized)",
    relpath="image/minimax-h3-image/minimax_h3_t2i_opt.json",
    description=MINIMAX_H3_T2I.description + _MINIMAX_H3_IMAGE_OPT_DESCRIPTION,
    notes=(
        _MINIMAX_H3_T2I_COMMON_NOTES
        + _MINIMAX_H3_IMAGE_OPT_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_FAST_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_T2I_TURBO = replace(
    MINIMAX_H3_T2I,
    id="minimax_h3_t2i_turbo",
    label="テキスト→画像 (MiniMax H3 Image t2i Turbo)",
    mode_label="テキスト→画像 (t2i Turbo)",
    relpath="image/minimax-h3-image/minimax_h3_t2i_turbo.json",
    description=MINIMAX_H3_T2I.description + _MINIMAX_H3_IMAGE_TURBO_DESCRIPTION,
    notes=(
        _MINIMAX_H3_T2I_COMMON_NOTES
        + _MINIMAX_H3_IMAGE_OPT_NOTES
        + _MINIMAX_H3_IMAGE_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_FAST_MODELS.format(unet="fl2va")
        + " + minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16（loras）/"
        " サンプリングは res_multistep・simple・4 ステップ"
    ),
)

MINIMAX_H3_I2I_OPT = replace(
    MINIMAX_H3_I2I,
    id="minimax_h3_i2i_opt",
    label="画像→画像 (MiniMax H3 Image i2i Optimized)",
    mode_label="画像→画像・編集 (i2i Optimized)",
    relpath="image/minimax-h3-image/minimax_h3_i2i_opt.json",
    description=MINIMAX_H3_I2I.description + _MINIMAX_H3_IMAGE_OPT_DESCRIPTION,
    notes=(
        _MINIMAX_H3_I2I_COMMON_NOTES
        + _MINIMAX_H3_IMAGE_OPT_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_FAST_MODELS.format(unet="fl2va")
    ),
)

MINIMAX_H3_I2I_TURBO = replace(
    MINIMAX_H3_I2I,
    id="minimax_h3_i2i_turbo",
    label="画像→画像 (MiniMax H3 Image i2i Turbo)",
    mode_label="画像→画像・編集 (i2i Turbo)",
    relpath="image/minimax-h3-image/minimax_h3_i2i_turbo.json",
    description=MINIMAX_H3_I2I.description + _MINIMAX_H3_IMAGE_TURBO_DESCRIPTION,
    notes=(
        _MINIMAX_H3_I2I_COMMON_NOTES
        + _MINIMAX_H3_IMAGE_OPT_NOTES
        + _MINIMAX_H3_IMAGE_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_FAST_MODELS.format(unet="fl2va")
        + " + minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16（loras）/"
        " サンプリングは res_multistep・simple・4 ステップ"
    ),
)

MINIMAX_H3_R2I_OPT = replace(
    MINIMAX_H3_R2I,
    id="minimax_h3_r2i_opt",
    label="参照画像→画像 (MiniMax H3 Image r2i Optimized)",
    mode_label="参照画像→画像・参照編集 (r2i Optimized)",
    relpath="image/minimax-h3-image/minimax_h3_r2i_opt.json",
    description=MINIMAX_H3_R2I.description + _MINIMAX_H3_IMAGE_OPT_DESCRIPTION,
    notes=(
        _MINIMAX_H3_R2I_COMMON_NOTES
        + _MINIMAX_H3_IMAGE_OPT_NOTES
        + _MINIMAX_H3_IMAGE_REF_LORA_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_FAST_MODELS.format(unet="fl2va")
        + " + minimax_h3_ref_lora_rank_256_bf16（loras）"
    ),
)

MINIMAX_H3_R2I_TURBO = replace(
    MINIMAX_H3_R2I,
    id="minimax_h3_r2i_turbo",
    label="参照画像→画像 (MiniMax H3 Image r2i Turbo)",
    mode_label="参照画像→画像・参照編集 (r2i Turbo)",
    relpath="image/minimax-h3-image/minimax_h3_r2i_turbo.json",
    description=MINIMAX_H3_R2I.description + _MINIMAX_H3_IMAGE_TURBO_DESCRIPTION,
    notes=(
        _MINIMAX_H3_R2I_COMMON_NOTES
        + _MINIMAX_H3_IMAGE_OPT_NOTES
        + _MINIMAX_H3_IMAGE_REF_LORA_NOTES
        + _MINIMAX_H3_IMAGE_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_IMAGE_FAST_MODELS.format(unet="fl2va")
        + " + minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16 +"
        " minimax_h3_ref_lora_rank_256_bf16（loras）/"
        " サンプリングは euler・simple・4 ステップ"
    ),
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
_MINIMAX_H3_BASE_NOTES = (
    "24fps 固定・尺は 17k+5 フレームの格子に切り上げ（5 秒 = 124 フレーム、"
    "学習範囲は約 1〜15 秒）/ 短辺 768px・最大 768x1344 が既定の画角"
    "（幅高さは 32 の倍数）/ negative prompt は無い（CFG 無しの BasicGuider）/"
    " ユーザー LoRA を挿すチェーンは持たない"
)

#: latent_upscale の注意書き（全 MiniMax H3 動画スペック共通）
_MINIMAX_H3_UPSCALE_NOTES = (
    " / `latent_upscale`（既定 **on**）: on だと 1 パス目を **0.2MP**"
    "（縦横比は指定のまま・32 の倍数）で回し、`LTXVSeparateAVLatent` で映像と"
    "音声に分けてから `MinimaxH3LatentUpscaler3D`（mode=target dimensions・"
    "align 32・bf16）で**指定解像度**に拡大 → `LTXVConcatAVLatent` で戻して"
    "`ManualSigmas`（0.9035 / 0.6316 / 0.3158 / 0.0000）の 3 ステップで仕上げる"
    "2 パス構成になる（noise / guider / sampler は 1 パス目と共有。組み替えは"
    "ジョブの組み立て時なのでテンプレート自体は 1 パスのまま）/ off は"
    "テンプレートそのままで指定解像度を 1 パス / on には"
    "`MinimaxH3LatentUpscaler3D`（Comfyui_Minimax_h3_latent_Upscaler）と"
    "`minimax_h3_latent_upscaler_3d_bf16`（latent_upscale_models）が要る。"
    "入れられない接続先（Comfy Cloud）では off しか選べない /"
    " ラテント連続性のバリアントで on にすると**2 段引き継ぎ**になる:"
    "1 パス目（0.2MP）と 2 パス目（最終解像度）のラテントを両方保存し"
    "（2 本目は保存先の末尾に `_hires`）、連続カット版は 2 本目を読む"
    "2 個目の `MiniMaxH3MotionContext` + `BasicGuider` を組んで 2 パス目の"
    "guider に据える。直前カットに 2 本目が無ければ 1 段引き継ぎに戻る /"
    " **チェーンの途中で解像度や `latent_upscale` を変えると"
    "`MiniMaxH3MotionContext` が解像度不一致で止まる**"
    "（`context_latent` は生成するクリップと同じ解像度でなければならない）"
)

#: 全 MiniMax H3 動画スペックが共通で持つ注意書き
_MINIMAX_H3_NOTES = _MINIMAX_H3_BASE_NOTES + _MINIMAX_H3_UPSCALE_NOTES

#: turbo 版だけの注意書き（素のものとの差分）
_MINIMAX_H3_TURBO_NOTES = (
    " / **turbo**: 4step 蒸留 LoRA（`minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16`）と"
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
#: r2v の opt / turbo は素の r2v と土台が違う: ref2va の量子化ウェイトではなく
#: **fl2va + 参照 LoRA** の組み合わせで、``LoraLoaderModelOnly`` で重ねる。
_MINIMAX_H3_R2V_REF_LORA_NOTES = (
    " / r2v の opt / turbo だけは素の版と土台が違い、"
    "`minimax_h3_fl2va_pruned_w4a8_mixed` に参照 LoRA"
    "（`minimax_h3_ref_lora_rank_256_bf16`）を `LoraLoaderModelOnly` で重ねて"
    "参照モードにする（ref2va の量子化ウェイトは使わない）"
)

#: r2v turbo だけの注意書き。``MiniMaxH3TurboLoRA`` は使わず、4step 蒸留 LoRA も
#: 素の ``LoraLoaderModelOnly`` で重ねるので `low_vram` の選択式は持たない。
#: **opt との差は「蒸留 LoRA を 1 段足して steps 4 / euler にする」だけ**で、
#: テンプレートは他の版と同じ 1 パス（アップスケールは `latent_upscale` 側）。
_MINIMAX_H3_R2V_TURBO_NOTES = (
    " / **turbo**: opt の構成（fl2va + 参照 LoRA）にさらに 4step 蒸留 LoRA"
    "（`minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16`）を"
    "`LoraLoaderModelOnly` で重ね、サンプリングを **4 ステップ / euler** に"
    "したもの（`MiniMaxH3TurboLoRA` を使わないので `low_vram` は無い）。"
    "Sage Attention / Mem Eff Sage Attention / Sol-Attn / SigmaShift /"
    " Spectrum は opt と同じく焼き込み済みで、入力の形は素の版とまったく同じ。"
    "`PathchSageAttentionKJ`（ComfyUI-KJNodes + SageAttention）・"
    "`MiniMaxH3MemoryEfficientSageAttentionPatch`・`SolAttnPatch`・"
    "`MiniMaxH3SigmaShift`・`SpectrumApplyMiniMaxH3` の"
    "**カスタムノードと量子化ウェイト一式が入った環境でのみ**動く"
)
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

#: モデルファイル（t2v / i2v は fl2va、r2v は ref2va。他は共通）。
#: 素の版も量子化ウェイト（w4a8_mixed）と heretic の text encoder を使い、
#: opt / turbo との差は動画 VAE（fp16 か int8_convrot か）と焼き込みノードだけ。
_MINIMAX_H3_MODELS = (
    " モデル: minimax_h3_{unet}_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_fp16 + minimax_h3_audio_vae_fp32 +"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）"
)

#: turbo / opt / 連続カットのテンプレートだけが使う**任意のカスタムノード**の
#: ``class_type``。入れていない環境で接続インジケーターが赤くならないよう、
#: ヘルスチェックの「必ず在るべきノード」からは外す
#: （:func:`app.workflow.all_required_class_types`）。そのワークフローを選んで
#: 実行したときだけ ComfyUI 側でエラーになる。
#:
#: 画像側の MiniMax H3 Image（``minimax_h3_*_opt`` / ``*_turbo``）も**同じノード**を
#: 使うので、こちらの一覧をそのまま共有している（``MiniMaxH3SigmaShift`` だけは
#: 画像テンプレートでは使わない: sigma shift は ``H3SamplingSettings`` が持つ）。
#: そのため :func:`app.workflow.supported_on_target` が Comfy Cloud では画像の
#: opt / turbo も選択肢から落とす。素の版が使う ``H3*`` のノード
#: （ComfyUI-MiniMax-H3-Image-Studio、``deploy/runpod/custom_nodes.txt`` 参照）も
#: 並べてあり、カスタムノード未導入の接続先では base も含めて画像の
#: MiniMax H3 Image が丸ごと選択肢から消える。
OPTIONAL_CLASS_TYPES: frozenset[str] = frozenset(
    {
        "MiniMaxH3TurboLoRA",
        # ComfyUI-MiniMax-H3-Image-Studio（画像 t2i / i2i / r2i の全バリアント）
        "H3TextToImagePrepare",
        "H3ImageToImagePrepare",
        "H3ReferenceEditPrepare",
        "H3SamplingSettings",
        "H3ImageDecode",
        "H3ImageFrameSelector",
        "PathchSageAttentionKJ",
        "MiniMaxH3MemoryEfficientSageAttentionPatch",
        "SolAttnPatch",
        "MiniMaxH3SigmaShift",
        "SpectrumApplyMiniMaxH3",
        # ラテントアップスケーラ（Comfyui_Minimax_h3_latent_Upscaler）。
        # テンプレートには出てこない: 選択式 ``latent_upscale`` が on のときだけ
        # ジョブの組み立てがグラフに足す（:class:`UpscaleSpec`）ので、接続先ごとの
        # 対応判定は :meth:`SelectSpec.choices_for_target` のほうで行う。
        # ここに並べてあるのはヘルスチェックの「必ず在るべきノード」から外すため。
        LATENT_UPSCALER_CLASS,
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
    " minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16（loras）"
)

#: r2v opt 版のモデルファイル（fl2va ウェイト + 参照 LoRA）
_MINIMAX_H3_R2V_OPT_MODELS = (
    " モデル: minimax_h3_fl2va_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_int8_convrot + minimax_h3_audio_vae_fp32 +"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）+"
    " minimax_h3_ref_lora_rank_256_bf16（loras）"
)

#: r2v turbo 版のモデルファイル（opt に 4step 蒸留 LoRA を足しただけ）
_MINIMAX_H3_R2V_TURBO_MODELS = (
    " モデル: minimax_h3_fl2va_pruned_w4a8_mixed（diffusion_models）+"
    " minimax_h3_video_vae_int8_convrot + minimax_h3_audio_vae_fp32 +"
    " qwen3vl_32b_heretic_minimax_h3_nvfp4（text_encoders）+"
    " minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16 +"
    " minimax_h3_ref_lora_rank_256_bf16（loras）"
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
    choice_labels={"off": "通常", "on": "VRAM 節約・遅くなる"},
    hint=(
        "on にすると 4step 蒸留 LoRA を低 VRAM モードで読み込む"
        "（VRAM が足りずに落ちるときだけ。遅くなるので既定は off）"
    ),
)

#: ラテントアップスケール（``latent_upscale``）の選択式。**全 MiniMax H3 動画
#: ワークフロー共通**で、既定は ``on``。テンプレートは常に 1 パスのままで、
#: ``on`` のときだけジョブの組み立てがグラフを 2 パスに組み替える
#: （:class:`UpscaleSpec` / :func:`app.workflow.splice_latent_upscale`）。
#: ``MinimaxH3LatentUpscaler3D`` は任意のカスタムノードなので、Comfy Cloud では
#: 選択肢が ``off`` だけになる（:meth:`SelectSpec.choices_for_target`）。
_MINIMAX_H3_LATENT_UPSCALE_SELECT = SelectSpec(
    label="ラテントアップスケール（2 パス）",
    choices=("on", "off"),
    default="on",
    # 1 つの注入先では表せない（ノードを足して配線を変える）ので Target は持たない
    rewrites_graph=True,
    index_field="",
    requires_class_types=(LATENT_UPSCALER_CLASS,),
    restricted_choice="off",
    choice_labels={
        "on": "する（0.2MP で下描き → 指定解像度に拡大）",
        "off": "しない（指定解像度で 1 パス）",
    },
    hint=(
        "on にすると 1 パス目を 0.2MP（縦横比はそのまま）で回してから"
        "`MinimaxH3LatentUpscaler3D` でラテントのまま指定解像度に拡大し、"
        "3 ステップ（ManualSigmas 固定）で仕上げる。同じ解像度を 1 パスで"
        "回すより速くて破綻しにくい。off はテンプレートそのままの 1 パス"
    ),
)

#: MiniMax H3 動画ワークフローが共通で持つ選択式（品質バリアント固有のものは
#: それぞれの宣言で足す）
_MINIMAX_H3_VIDEO_SELECTS: dict[str, SelectSpec] = {
    LATENT_UPSCALE_NAME: _MINIMAX_H3_LATENT_UPSCALE_SELECT,
}

#: 1 パス目のサンプラーは、素の t2v / i2v だけサブグラフ由来の ``105:14``。
#: r2v と turbo / opt の全テンプレートは連番に振り直してあるので ``125``。
_MINIMAX_H3_UPSCALE = UpscaleSpec(sampler="125")
_MINIMAX_H3_UPSCALE_SUBGRAPH = UpscaleSpec(sampler="105:14")

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
    selects=dict(_MINIMAX_H3_VIDEO_SELECTS),
    upscale=_MINIMAX_H3_UPSCALE_SUBGRAPH,
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
    selects=dict(_MINIMAX_H3_VIDEO_SELECTS),
    upscale=_MINIMAX_H3_UPSCALE_SUBGRAPH,
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
    selects=dict(_MINIMAX_H3_VIDEO_SELECTS),
    upscale=_MINIMAX_H3_UPSCALE,
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
    selects={
        MINIMAX_H3_LOW_VRAM_NAME: _MINIMAX_H3_LOW_VRAM_SELECT,
        **_MINIMAX_H3_VIDEO_SELECTS,
    },
    upscale=_MINIMAX_H3_UPSCALE,
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
    selects={
        MINIMAX_H3_LOW_VRAM_NAME: _MINIMAX_H3_LOW_VRAM_SELECT,
        **_MINIMAX_H3_VIDEO_SELECTS,
    },
    upscale=_MINIMAX_H3_UPSCALE,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_I2V_NOTES
        + _MINIMAX_H3_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_TURBO_MODELS.format(unet="fl2va")
    ),
)

#: r2v turbo は他の turbo と作りが違う（``MiniMaxH3TurboLoRA`` を使わないので
#: `low_vram` の選択式も持たない）。ウェイトは fl2va で、参照 LoRA と 4step 蒸留
#: LoRA を ``LoraLoaderModelOnly`` で 2 段重ねてから高速化パッチの連鎖に流す。
_MINIMAX_H3_R2V_TURBO_DESCRIPTION = (
    "サンプリングは 4 ステップ固定で、素の版よりずっと速く上がる"
    "（fl2va ウェイトに参照 LoRA と 4step 蒸留 LoRA を重ね、Sage Attention /"
    " Sol-Attn / Spectrum を焼き込んだ高速版）。入力の指定は素の版と"
    "まったく同じだが、専用の量子化ウェイトと MiniMax H3 系のカスタムノード"
    "一式が入った環境でのみ動く。"
)

MINIMAX_H3_R2V_TURBO = replace(
    MINIMAX_H3_R2V,
    id="minimax_h3_r2v_turbo",
    label="参照素材→動画・音声つき (MiniMax H3 r2v Turbo)",
    mode_label="参照素材→動画・音声つき (r2v Turbo)",
    relpath="video/minimax-h3/minimax_h3_r2v_turbo.json",
    description=MINIMAX_H3_R2V.description + _MINIMAX_H3_R2V_TURBO_DESCRIPTION,
    notes=(
        _MINIMAX_H3_NOTES
        + _MINIMAX_H3_R2V_NOTES
        + _MINIMAX_H3_R2V_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_R2V_TURBO_MODELS
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
    # 蒸留 LoRA は無いが、テンプレートのサンプラー ID は turbo と同じ 125
    upscale=_MINIMAX_H3_UPSCALE,
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
    upscale=_MINIMAX_H3_UPSCALE,
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
        + _MINIMAX_H3_R2V_REF_LORA_NOTES
        + " /"
        + _MINIMAX_H3_R2V_OPT_MODELS
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
        + _MINIMAX_H3_R2V_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_R2V_TURBO_MODELS
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
        + _MINIMAX_H3_R2V_REF_LORA_NOTES
        + " /"
        + _MINIMAX_H3_R2V_OPT_MODELS
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
        MINIMAX_H3_R2V_CONTEXT.description + _MINIMAX_H3_R2V_TURBO_DESCRIPTION
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
        + _MINIMAX_H3_R2V_TURBO_NOTES
        + " /"
        + _MINIMAX_H3_R2V_TURBO_MODELS
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
        + _MINIMAX_H3_R2V_REF_LORA_NOTES
        + " /"
        + _MINIMAX_H3_R2V_OPT_MODELS
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

MINIMAX_MUSIC_3 = WorkflowSpec(
    id="minimax_music_3",
    label="MiniMax Music 3（音楽・歌もの）",
    mode_label="Music 3（音楽・歌もの）",
    kind="audio",
    family="minimax-music",
    relpath="audio/minimax_music_3.json",
    output_node="35",
    description=(
        "Song generation: writes a full music track, with **vocals** when"
        " `lyrics` are given and an instrumental when they are not."
        " `audio_prompt` is the *caption* of the track — tempo, key, style,"
        " instruments, production and the voice all go in there as prose."
        " Use it whenever the user wants music or a song."
    ),
    prompt_hint=(
        "A **Structured Caption** of the track, not a scene: three headed"
        " sections — `Global Metadata:` (genre, tempo, key, emotional arc,"
        " listening setting, production), `Vocal Details:` (lead, timbre,"
        " register, delivery, harmonies) and `Arrangement:` (a section by"
        " section timeline of what enters and leaves). 250-450 words of prose."
        " The words to sing go in `lyrics`, never in `audio_prompt`, and the"
        " caption never paraphrases them."
    ),
    # 尺の根拠: モデルが書ける曲は約 5 分（300 秒）まで
    # （github.com/MiniMax-AI/MiniMax-Music3 README /
    # docs.comfy.org/tutorials/audio/minimax/minimax-music-3）。ComfyUI ノード側の
    # 受付幅はもっと広い（MiniMaxMusic3TextEncode.max_duration は FLOAT で
    # 0.04〜MAX_AUDIO_FRAMES/AUDIO_FRAMES_PER_SECOND = 9000/25 = 360 秒、
    # comfy_extras/nodes_minimax_music.py）ので、狭いほうのモデル仕様を採る。
    # 既定はテンプレートの widget 値そのまま（60 秒）。なお `max_duration` は
    # 「上限」であって、モデルはそれより早く曲を終えることがある。
    min_duration=1.0,
    max_duration=300.0,
    default_duration=60.0,
    inject={
        "prompt": T("37:13", "caption", "MiniMaxMusic3TextEncode"),
        "lyrics": T("37:13", "lyrics", "MiniMaxMusic3TextEncode"),
        # 空ラテント（37:15.seconds）は 37:13 の 2 番目の出力を読むので、
        # 秒数の注入先はここ 1 か所だけでよい
        "duration": T("37:13", "max_duration", "MiniMaxMusic3TextEncode"),
        # one SeedNode feeds both KSampler.seed and 37:13.seed
        "seed": T("37:38", "seed", "SeedNode"),
        "steps": T("37:9", "steps", "KSampler"),
        "save_prefix": T("35", "filename_prefix", "SaveAudioAdvanced"),
    },
    notes="minimax_music3_dit / 出力 MP3・歌詞ありでボーカル、なしでインスト",
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
        " use MiniMax Music 3 for songs with lyrics."
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


SPECS: tuple[WorkflowSpec, ...] = (
    KREA2_TURBO,
    ANIMA,
    Z_IMAGE_TURBO,
    QWEN_IMAGE_EDIT,
    MINIMAX_H3_T2I,
    MINIMAX_H3_T2I_OPT,
    MINIMAX_H3_T2I_TURBO,
    MINIMAX_H3_I2I,
    MINIMAX_H3_I2I_OPT,
    MINIMAX_H3_I2I_TURBO,
    MINIMAX_H3_R2I,
    MINIMAX_H3_R2I_OPT,
    MINIMAX_H3_R2I_TURBO,
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
    MINIMAX_MUSIC_3,
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
DEFAULT_AUDIO_WORKFLOW = MINIMAX_MUSIC_3.id

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
    """One workflow as the system prompts describe it (SPEC §4.2 / §4.3).

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
    #: logical knobs the manifest exposes (``prompt``, ``lyrics``, ``duration``, …).
    #: The audio catalog lists them so the agent knows which extra fields the
    #: workflow reads and which it ignores.
    supports: tuple[str, ...] = ()
    #: audio workflows: the accepted / suggested clip length in seconds
    min_duration: float = 0.0
    max_duration: float = 0.0
    default_duration: float = 0.0
    #: 選択式フィールド
    #: ``(論理名, 見出し, 選択肢, 既定値, 自動か, 一言, 表示ラベル)``。
    #: 宣言のないワークフローでは空なので、カタログにも何も出ない（SPEC §3.1）。
    #: **最後の表示ラベルは画面用の飾り**（``選ぶ値 -> 日本語``）で、エージェントが
    #: 書くのは 3 つ目の選択肢のほうの生の値。
    selects: tuple[
        tuple[str, str, tuple[str, ...], str, bool, str, dict[str, str]], ...
    ] = ()

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field for field, _ in self.required_inputs)


def catalog_entry(spec: WorkflowSpec, comfy_target: str = "") -> CatalogEntry:
    """Describe one workflow for the system prompts.

    ``comfy_target`` を渡すと、その接続先で選べない選択肢を落とす
    （Comfy Cloud には ``MinimaxH3LatentUpscaler3D`` を入れられないので
    ``latent_upscale`` が ``off`` だけになる。:meth:`SelectSpec.choices_for_target`）。
    """
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
                select.choices_for_target(comfy_target),
                select.fallback_for_target(comfy_target),
                bool(select.auto),
                select.hint,
                dict(select.choice_labels),
            )
            for name, select in spec.selects.items()
        ),
    )


def video_catalog(comfy_target: str = "") -> list[CatalogEntry]:
    """Every selectable video workflow, in UI / prompt order."""
    return [catalog_entry(spec, comfy_target) for spec in video_specs()]


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
    # the catalog embedded in the chat system prompts is generated from these,
    # so a new workflow must document itself (SPEC §4.2 / §4.3)
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
    # 1 枚目だけを受ける固定の入力（``H3ReferenceEditPrepare.source_image``）も
    # 雛形のローダーから引かれていなければならない: 組み立てのときにここへ
    # 1 枚目を繋ぎ直すので、テンプレート側で別のノードを読んでいると、
    # そのノードが宙に浮いたまま残ってしまう。
    if fan.primary_image_field:
        link = inputs.get(fan.primary_image_field)
        if not isinstance(link, list) or len(link) != 2 or link[0] != fan.image_loader.node_id:
            problems.append(
                f"{spec.id}.ref_media: {fan.node.node_id}.{fan.primary_image_field}"
                f" does not read the loader {fan.image_loader.node_id!r}"
            )
    if fan.image_offset < 0:
        problems.append(f"{spec.id}.ref_media: image_offset must be >= 0")
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


def _validate_upscale(spec: WorkflowSpec, tpl: Workflow) -> list[str]:
    """:attr:`WorkflowSpec.upscale` の宣言がテンプレートと噛み合うか（SPEC §3.1）。

    テンプレート自体は 1 パスのままなので、見るのは「組み替えの足場がある
    か」だけ: 1 パス目のサンプラーが宣言どおりに居るか、その出力をデコーダが
    ``samples`` で読んでいるか、足すノードの ID が空いているか、そして
    1 パス目の解像度を書き換えるための ``width`` / ``height`` があるか。
    """
    upscale = spec.upscale
    select = spec.selects.get(LATENT_UPSCALE_NAME)
    if upscale is None:
        if select is not None:
            return [
                f"{spec.id}.selects[{LATENT_UPSCALE_NAME}]: declared without an"
                " `upscale` spec"
            ]
        return []

    problems: list[str] = []
    if select is None:
        problems.append(
            f"{spec.id}.upscale: declared without a"
            f" `selects[{LATENT_UPSCALE_NAME}]` to switch it on"
        )
    for name in ("width", "height"):
        if name not in spec.inject:
            problems.append(f"{spec.id}.upscale: no {name!r} injection target")

    node = tpl.get(upscale.sampler)
    if not isinstance(node, dict) or node.get("class_type") != "SamplerCustomAdvanced":
        problems.append(
            f"{spec.id}.upscale.sampler: node {upscale.sampler!r} is not a"
            " SamplerCustomAdvanced"
        )
    else:
        missing = [
            field_name
            for field_name in ("noise", "guider", "sampler")
            if field_name not in (node.get("inputs") or {})
        ]
        if missing:
            problems.append(
                f"{spec.id}.upscale.sampler: {upscale.sampler} has no"
                f" {', '.join(missing)}"
            )
    link = [upscale.sampler, 0]
    if not any(
        isinstance(other, dict) and (other.get("inputs") or {}).get("samples") == link
        for other in tpl.values()
    ):
        problems.append(
            f"{spec.id}.upscale.sampler: nothing decodes {upscale.sampler!r}"
        )
    for node_id in upscale.node_ids():
        if node_id in tpl:
            problems.append(
                f"{spec.id}.upscale: node id {node_id!r} is already taken"
            )
    if len(set(upscale.node_ids())) != len(upscale.node_ids()):
        problems.append(f"{spec.id}.upscale: duplicate node ids")
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

    problems += _validate_upscale(spec, tpl)

    for name, select in spec.selects.items():
        if select.rewrites_graph:
            # 注入先を持たず、ビルダーがグラフを組み替える選択式（latent_upscale）。
            # 書き込む先が無いので Target の検査は掛からない。
            if not select.choices:
                problems.append(f"{spec.id}.selects[{name}]: no choices declared")
            if not select.label.strip():
                problems.append(f"{spec.id}.selects[{name}]: label is empty")
            if select.default and select.default not in select.choices:
                problems.append(
                    f"{spec.id}.selects[{name}]: default {select.default!r} is not"
                    " one of the choices"
                )
            if select.target is not None:
                problems.append(
                    f"{spec.id}.selects[{name}]: rewrites_graph selects must not"
                    " declare an injection target"
                )
            continue
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
        # 表示ラベルは画面用の飾りなので宣言は任意だが、値を打ち間違えると
        # 黙って生の値が出るだけで気づけない（フォールバックがあるため）
        for value in select.choice_labels:
            if value not in select.choices:
                problems.append(
                    f"{spec.id}.selects[{name}]: choice_labels has {value!r},"
                    " which is not one of the choices"
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
