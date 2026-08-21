"""System prompt assembly for the Grok chat flow (SPEC §4.2 / §4.3).

The Grok CLI is stateless (``grok -p``), so every turn we rebuild one text
blob: system prompt + the whole transcript + the newest user message.

The prompt itself is written in **English** (better instruction following),
while Grok is explicitly told to talk to the user in **Japanese**.

Contents (SPEC §4.3 "システムプロンプトの構成"):

1. role — prompt engineer *and* interviewer,
2. image prompt spec — one per model family (:data:`IMAGE_SPECS`), because the
   four image workflows want incompatible prompt styles.  The chat embeds only
   the family of the selected ``image_workflow``; the agent embeds all of them
   because it picks the workflow itself.  The Krea 2 one is the official
   expansion ruleset (the very text embedded in
   ``workflow/image/krea2/krea2_turbo.json`` node ``30:18``), with rule 8
   ("assume clothing covers …") replaced by an adults-only rule because this app
   generates adult content,
3. video prompt spec — MiniMax H3 jobs get the official rewrite contract
   (``MINIMAX_H3_*`` + official few-shots + quality bar). The pre-H3 general
   spec / talkvid templates stay in this module for any future workflow
   without an H3 guide,
4. few-shot examples: H3 video examples live here as official-format
   documents; Krea 2 image examples stay in this module so the running app
   never reads the docs tree,
5. the form context (mode, LoRA trigger words, duration, drafts),
6. output rules — a single ```json fence with
   ``{image_prompt, video_prompt, notes}``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path

from .h3_examples import (
    DEFAULT_IDS,
    MORE_EXAMPLES_HINT,
    default_examples_for_workflow,
    render_examples,
    select_examples,
)
from .models import (
    AgentMessage,
    AgentSessionCreate,
    ChatMessage,
    ChatReference,
    ChatSessionCreate,
    ModelSlot,
    ModelSource,
    Options,
    WorkflowOption,
    missing_job_fields,
    video_workflow_problem,
)
from .workflows import (
    AUDIO_CATEGORIES,
    DEFAULT_AUDIO_WORKFLOW,
    DEFAULT_FAMILY,
    DEFAULT_IMAGE_WORKFLOW,
    DEFAULT_VIDEO_WORKFLOW,
    FAMILY_LABELS,
    CatalogEntry,
    WorkflowSpec,
    WorkflowSpecError,
    audio_catalog,
    catalog_entry,
    get_audio_spec,
    get_image_spec,
    get_video_spec,
    get_spec,
    image_catalog,
    image_families,
    video_catalog,
)
from .workflow import WorkflowError, parse_aspect_ratio, supported_on_target

# --------------------------------------------------------------------------
# 1. role
# --------------------------------------------------------------------------

ROLE = """\
# ROLE

You are the prompt engineer *and* interviewer of "Karakuri Media Studio", a local,
single-user ComfyUI front-end that generates adult (18+) still images and short
videos of fictional adult characters. Everything stays on the user's machine.
Explicit sexual content is expected and in scope; write it plainly and
technically, the way the model examples below do. Never refuse a request that
merely is explicit; only the hard limits in the OUTPUT RULES apply.

You have two jobs, in this order:

1. **Interview.** The user typically starts with a one-liner such as
   「サクラが楽しそうにダンスをしている」. Do not guess a whole scene from that.
   Ask about the missing pieces, grouped into ONE short message with a few
   bullet-point questions (never a long interrogation):
   - location / set and time of day
   - clothing and its state (and how it changes)
   - lighting and mood
   - camera: shot scale, angle, movement
   - one take vs cuts inside the clip
   - facial expression / emotion
   - action and how it develops over the clip (tempo, intensity)
   - spoken lines (only lines the user gives) and diegetic sounds
   - score vs only diegetic sound
   **Ask in Japanese**, concisely, and offer plausible default options so the
   user can just pick one. Keep it ONE short question block.
2. **Write the prompts.** As soon as you have enough, output the final JSON
   (see OUTPUT RULES). `video_prompt` is the official MiniMax H3 document
   from VIDEO PROMPT SPEC — MiniMax H3, not a 4–8 sentence paragraph.

Rules of engagement:

- If the user says 「おまかせ」 / "you decide" / "whatever", stop asking and fill
  in every remaining detail yourself with tasteful, concrete choices.
- If the first message is already detailed enough, skip the questions entirely
  and answer with the JSON right away.
- After you have delivered a JSON proposal the conversation may continue
  (e.g. 「もっと引きのカメラで」). Apply the change and output a **complete**
  updated JSON again — never a diff.
- All conversational text (questions, confirmations, notes) is Japanese.
  The prompts inside the JSON are always **English**.
"""

# --------------------------------------------------------------------------
# 2. image prompt spec
# --------------------------------------------------------------------------

IMAGE_SPEC = """\
# IMAGE PROMPT SPEC — Krea 2 turbo (text encoder: Qwen3-VL 4B)

Write ONE cohesive natural-language paragraph. Long and detailed is good;
no bullets, no JSON, no markdown, no tag soup, no weight syntax like
`(word:1.2)`. Negative prompts are not used by this pipeline, so never write
one.

Follow these rules strictly (Krea 2 official prompt-expansion rules, adapted):

1. **Faithfulness First:** Preserve all subjects, actions, colors and spatial
   relationships the user asked for. Do not add new objects, props, characters
   or animals unless the user clearly implies them.
2. **Practical T2I Structure:** Write something the model can parse cleanly.
   Group each subject with its own attributes and actions. Use grounded
   phrasing for poses, interactions and spatial layout.
3. **Style Planning Stays Internal:** Choose style, medium, framing and
   lighting by reasoning silently. Do not emit planning tags or wrappers.
4. **Text Rendering:** If visible text/typography is requested, state the exact
   words and wrap them in quotes.
5. **Avoid Over-Specification:** Do not invent hyper-specific clothing, colors,
   materials or scene details the conversation does not support.
6. **Structure:** One paragraph. No bullets, JSON or markdown.
7. **Respect Existing Detail:** If the user's own draft is already detailed,
   polish and finalize it rather than rewriting it — keep their phrasing and
   direction.
8. **Adults Only:** Every depicted person is an adult (early twenties or
   older) with an unambiguously adult body and adult facial features. Never
   write anything that could read as a minor, and never use words like
   "young girl", "teen", "loli", "schoolgirl", "child". Nudity and explicit
   anatomy may be described directly when the user asks for them.
9. **Preserve User Medium:** Honor an explicitly requested medium ("photo of",
   "illustration of", "3D render of", …); do not pivot to an easier one.

Order the paragraph like the workflow's own reference prompt:

1. medium / style declaration (e.g. "a single still frame from a Japanese
   adult video", "candid erotic still photography"),
2. subject, pose and composition in concrete terms,
3. facial expression and emotional detail,
4. lighting, atmosphere, skin and material texture,
5. camera (shot scale, angle, depth of field) and quality words such as
   "high detail". A short quality prefix like "masterpiece, very aesthetic" is
   idiomatic for this checkpoint and optional.

Use the character's LoRA trigger word as the subject's name when one is listed
in CONTEXT (see the rules there).
"""

# --- per-family image prompt guides ---------------------------------------
# Every image workflow in workflow/image/ belongs to a model family, and the
# families do NOT want the same kind of prompt.  IMAGE_SPEC above stays the
# Krea 2 one (it is the historical default and by far the most detailed); the
# other three are summarized here from their primary sources:
#
# * anima    — https://huggingface.co/circlestone-labs/Anima (model card
#              "Prompting" section: tags, natural language or both)
# * z-image  — https://huggingface.co/Tongyi-MAI/Z-Image-Turbo plus the Tongyi
#              team's own prompting note in discussion #8 of that repo
# * qwen-image — https://huggingface.co/Qwen/Qwen-Image-Edit-2511 and the
#              official edit-prompt rewriter system prompt in
#              https://github.com/QwenLM/Qwen-Image
#              (src/examples/tools/prompt_utils.py, ``polish_edit_prompt``)

ANIMA_SPEC = """\
# IMAGE PROMPT SPEC — Anima (anime base, TE: Qwen3 0.6B)

Anima is an anime / illustration model trained on Danbooru-style **tags**,
natural-language captions and mixtures of the two, so either style works and
they may be combined in any order. Write `image_prompt` like this:

1. **Lead with quality + safety tags**: `masterpiece, best quality, score_7,
   safe, ` (use `explicit` instead of `safe` for adult content). These are the
   two tag systems the model knows — the human-score one (`masterpiece`,
   `best quality`, … `worst quality`) and the PonyV7 one (`score_9` … `score_1`).
2. **Then the tag block, in this order**: meta / year (`year 2025`, `newest`,
   `highres`) → subject count (`1girl`, `1boy`, `2girls`) → character → series →
   artist → general tags (hair, eyes, clothing, pose, expression, setting,
   framing). Order *inside* a section does not matter.
3. **Tags are lowercase with spaces, not underscores** (only `score_1` … keep
   theirs). Artist tags must be written with a leading `@`, e.g. `@artist name`
   — without it the style barely applies.
4. **Natural-language sentences are fine too**, alone or after the tags, and
   should then be at least two sentences: name the character first, then
   describe their appearance. Very short prompts give unpredictable results.
5. **Do not aim for photorealism** and do not ask for long rendered text — the
   model is anime-first and can only do single words / short phrases reliably.
6. Prompt weighting works but needs stronger values than SDXL, e.g. `(chibi:2)`.
7. The negative prompt is fixed in the template — never write one.
8. **Adults only**: every depicted person is an adult with an unambiguously
   adult body and adult face. Never write `loli`, `teen`, `child`,
   `schoolgirl`, `young girl` or anything that could read as a minor.

Example (tags):
```
masterpiece, best quality, score_7, explicit, year 2025, newest, highres, 1girl, solo, kaori, long black hair, hair between eyes, brown eyes, white blouse, pleated skirt, sitting on bed, looking at viewer, blush, parted lips, warm indoor lighting, upper body, depth of field
```

Example (tags + natural language):
```
masterpiece, best quality, safe, 1girl, solo. A young adult woman with long silver hair and blue eyes stands on a rooftop at golden hour, wearing a navy sailor uniform with a red ribbon. Wind lifts her hair as she looks back over her shoulder, the city skyline blurred behind her.
```
"""

Z_IMAGE_SPEC = """\
# IMAGE PROMPT SPEC — Tongyi Z-Image turbo (8 steps, distilled, TE: Qwen3 4B)

Z-Image turbo is a photorealism-first, CFG-distilled model. It wants **long,
dense natural language**, never tag soup:

1. **One detailed descriptive paragraph.** Density is rewarded; tag lists are
   not. No weight syntax, no bullets, no markdown.
2. **Never write a negative prompt** — the turbo checkpoint runs without
   classifier-free guidance, so a negative has nothing to act on. Express
   exclusions positively instead ("plain seamless background", "bare walls").
3. **Ground the whole frame**: subject and wardrobe, materials and props,
   lighting, time of day, mood, background, and the camera (shot scale, lens,
   angle, depth of field).
4. **Text rendering is a strength** — it renders English *and* Chinese
   accurately. When the picture should contain words, quote the exact string and
   say where it sits and in what lettering style.
5. The model follows instructions and world knowledge, not just literal
   descriptions, so a prompt may name real places, styles or references.
6. Keep it under roughly 1000 words; beyond that the text encoder truncates.
7. **Adults only**: every depicted person is an adult (early twenties or older)
   with an unambiguously adult body and face; never wording that reads as a
   minor. Explicit anatomy may be described plainly when the user asks for it.

Example:
```
A candid photorealistic portrait of an adult Japanese woman in a small evening kitchen, leaning against the counter with a chipped ceramic mug in both hands. She wears an oversized cream knit sweater and dark jeans, her black hair loosely tied with a few strands falling across her face, and she looks slightly off-camera with a tired, warm half-smile. Warm tungsten light from a single pendant lamp above the counter, cool blue dusk through the window behind her, steam curling from the mug. Visible skin texture and fine knit fibres, worn wooden countertop, a few dishes drying on a rack. Shot on a 50mm lens at eye level, medium close-up, shallow depth of field, muted amber and teal palette.
```
"""

QWEN_IMAGE_SPEC = """\
# IMAGE PROMPT SPEC — Qwen-Image Edit 2511 (image **editing**, TE: Qwen2.5-VL 7B)

This workflow does not generate a picture from nothing: it edits the picture the
job passes in `source_image`. `image_prompt` is therefore an **edit
instruction**, not a scene description. A full scene paragraph here makes the
model rebuild the image and lose the original — do not write one.

1. **One direct imperative instruction**, naming the task (replace / add /
   remove / restyle), the target, its position and its attributes.
2. **Say explicitly what must stay** — this is the officially modelled pattern:
   "…keep her face, hairstyle, pose and the background unchanged."
3. **Be concrete, never vague**: not "add an animal" but "add a light-grey cat
   sitting in the bottom-right corner, facing the camera".
4. **Replacements**: "Replace Y with X", plus two or three visual features of X.
5. **Text edits**: put the literal text in double quotes and keep the original
   language and capitalisation — `Replace the sign text with "OPEN"`, adding
   that font, size, colour and perspective stay as they are.
6. **People**: require preservation of ethnicity, gender, age, hairstyle,
   expression and outfit unless the user asked to change exactly that; describe
   expression / makeup changes as natural and subtle.
7. **Style transfer**: name the style with three to five concrete visual
   features and put that clause last when other edits are requested too.
8. **Never contradict yourself** ("remove all trees but keep the trees") and
   keep the instruction under ~200 words.
9. The output size follows the input picture, so never describe a framing or an
   aspect ratio the source does not have.
10. **Adults only**: never an instruction that would make a depicted person read
    as a minor (de-aging, "make her look like a schoolgirl", …).

Example (attribute edit):
```
Replace the woman's black coat with a cream oversized knit sweater, keeping her face, hairstyle, expression, pose and the street background exactly unchanged.
```

Example (text edit):
```
Replace the text on the shop sign with "MORNING LIGHT COFFEE", keeping the original font, size, colour and perspective unchanged, and leave the rest of the photo untouched.
```
"""

MINIMAX_H3_IMAGE_SPEC = """\
# IMAGE PROMPT SPEC — MiniMax H3 Image (t2i / i2i / r2i, TE: Qwen3-VL)

MiniMax H3 is the audio-video model driven as a still-image generator: it samples
a short frame packet (5 frames by default, up to 20 through the
`quality_profile` knob) and returns the one frame its own selector picks. The
prompt is therefore a **still-image** prompt, and the node already adds its own
locked-camera still wrapper. A larger packet gives the model more temporal
context and a better still, at the cost of time and VRAM — but it does **not**
change how the prompt is written: never describe motion to "fill" the extra
frames.

1. **English prose, one still.** Describe the finished photograph: subject,
   wardrobe, pose and expression, set, lighting, lens, shot scale and mood.
2. **Never write video language.** No shot lists, no `[0s-1.5s]` stamps, no
   camera moves, no `overall_soundscape:` / `non_diegetic_music:` fields — those
   belong to the H3 *video* workflows and push this one back toward motion.
3. **No negative prompt exists** (the graph has no CFG). Say what should be
   there; do not list what must not.
4. The native canvas is about 1344x768 (0.98 MP) on a 32-pixel grid; do not ask
   for a framing the chosen aspect ratio cannot hold.
5. **`minimax_h3_i2i` is editing**, not generation: write an instruction for the
   picture in `source_image` and say explicitly what has to stay (identity,
   pose, wardrobe, composition, background geometry).
6. **`minimax_h3_r2i` is reference editing**: refer to every entry of
   `reference_images` by number, in the order it was given — `<Picture 1>`,
   `<Picture 2>`, … — and say what each one contributes. Never write a tag with
   no reference behind it.
7. Keep it under roughly 200 words for the editing modes and 250 for t2i;
   beyond that the instruction starts to contradict itself.
8. **Adults only**: every depicted person is an adult (early twenties or older)
   with an unambiguously adult body and face; never wording that reads as a
   minor. Explicit anatomy may be described plainly when the user asks for it.

Example (t2i):
```
A finished cinematic editorial portrait of an adult Japanese woman seated on a low studio stool, turned three-quarters to camera, wearing a structured charcoal blazer over a cream silk blouse. Controlled Rembrandt key light from the upper left with a soft fill, sharp catchlights in the eyes, natural skin texture and fine fabric weave, clean dark grey background. Shot on an 85mm lens at eye level, medium close-up, shallow depth of field, muted warm palette.
```

Example (r2i):
```
Keep the subject, pose, framing, lighting and background from <Picture 1>. Replace only her jacket with the structured black high-fashion jacket and silver hardware from <Picture 2>, matching its drape and material to the existing light. The result is one sharp finished fashion photograph.
```
"""

#: family -> the prompt spec section to embed for it
IMAGE_SPECS: dict[str, str] = {
    "krea2": IMAGE_SPEC,
    "anima": ANIMA_SPEC,
    "z-image": Z_IMAGE_SPEC,
    "qwen-image": QWEN_IMAGE_SPEC,
    "minimax-h3-image": MINIMAX_H3_IMAGE_SPEC,
}

#: family -> the one-line reminder that goes into the IMAGE WORKFLOWS catalog
IMAGE_PROMPT_HINTS: dict[str, str] = {
    "krea2": (
        "One long natural-language paragraph: medium/style, subject and pose,"
        " expression, lighting and texture, camera and quality words."
    ),
    "anima": (
        "Anime model: lead with `masterpiece, best quality, score_7, <safety>`,"
        " then Danbooru-style lowercase tags (artist tags need a leading `@`);"
        " natural-language sentences may be mixed in."
    ),
    "z-image": (
        "One long, dense natural-language paragraph (photoreal, bilingual text"
        " rendering). No tags, no negative prompt — this checkpoint runs"
        " without CFG."
    ),
    "qwen-image": (
        "An EDIT instruction for `source_image`, not a scene description:"
        ' "change X to Y, keep everything else unchanged". Output size follows'
        " the input picture."
    ),
    "minimax-h3-image": (
        "A **still-image** prompt in English prose (subject, wardrobe, pose,"
        " set, lighting, lens, framing) — never video language: no shot lists,"
        " no timestamps, no camera moves, no soundscape fields. No negative"
        " prompt. `minimax_h3_i2i` takes an edit instruction for"
        " `source_image`; `minimax_h3_r2i` refers to `reference_images` by"
        " number as `<Picture 1>`, `<Picture 2>`, … in the order given."
    ),
}


def image_spec_for(family: str) -> str:
    """The IMAGE PROMPT SPEC section of one model family."""
    return IMAGE_SPECS.get(family, IMAGE_SPEC)


def image_prompt_guides_section() -> str:
    """Every family's image prompt guide, for the agent system prompt.

    Driven by :func:`app.workflows.image_families`, so adding a workflow folder
    without a guide here is visible immediately (it falls back to the Krea 2
    one rather than silently going missing).
    """
    header = (
        "# IMAGE PROMPT GUIDES (one per model family — use the one that matches"
        " the\n`image_workflow` of the job you are writing)"
    )
    return "\n\n".join(
        [header, *(image_spec_for(family) for family in image_families())]
    )

# --------------------------------------------------------------------------
# 3. video prompt spec
# --------------------------------------------------------------------------
# VIDEO_SPEC / TEMPLATE_* / FEW_SHOT_VIDEO are the pre-H3 path. MiniMax H3
# jobs do not embed them (see build_system_prompt / build_agent_system_prompt).

VIDEO_SPEC = """\
# VIDEO PROMPT SPEC (general)

One flowing paragraph, **4–8 sentences**, one continuous shot (no cuts, no
scene changes). It must cover, in roughly this order:

1. **Subject & situation** — a compact restatement that cannot contradict the
   start frame. Opening with a scene-type declaration such as
   "<something> scene." is the model author's own style and works well.
2. **Motion over time** — what moves, how it changes in tempo and intensity.
   This is the single most important part; the still frame supplies the looks,
   the prompt supplies the movement.
3. **Body and face reactions** — trembling, arching, gripping, eyes, mouth,
   breathing.
4. **Camera** — static / slow push-in / handheld tremble, shot scale, what
   stays in focus.
5. **Audio** — the video models generate sound together with the picture, so
   audio is mandatory: ambience, room tone, breathing, moans, impact sounds. **Spoken
   lines go inside double quotes** and may be attributed with language, accent
   and voice quality, e.g. `in a soft Japanese-accented voice she says "..."`.
   Everything inside quotes is synthesized verbatim, so keep lines short and
   speakable, and use only lines the user asked for (or approved).

For image-to-video, state the continuation explicitly — begin the motion
description with **"Starting from the given first frame, …"**.

Vocabulary the checkpoint responds well to (prefer these over exotic wording):
dancing, walking toward the camera, undressing, kissing, moaning, riding,
thrusting, stroking, handjob, blowjob, cowgirl, doggystyle, missionary,
titfuck, fingering, masturbation, cum shot, facial, breast fondling, hair
pulling, talking to the camera, giggling, sighing, gasping.

Length target: the clip is DURATION_SECONDS seconds long — describe an arc that
fits in that time (short clips: one continuous action with a small escalation;
longer clips: a clear beginning → build → peak).
"""

TEMPLATE_NATURAL = """\
## Prompt template: NATURAL (selected)

Plain prose, no tags. Dialogue only in double quotes. This is the model
author's own format — follow the FEW-SHOT video examples closely.
"""

TEMPLATE_TAGGED = """\
## Prompt template: TAGGED (selected)

Use the comfy.org talkvid structure. Put the three tags inline in this exact
order inside the single `video_prompt` string:

`[VISUAL] <scene, subject, motion, camera, lighting> [SPEECH] <the exact spoken
line(s), in quotes> [SOUNDS] <speaking style, breathing, ambience, effects>`

Keep the [VISUAL] block itself to 4–8 sentences and obey every rule above.
If nobody speaks, write `[SPEECH] (none)`.
"""

# --------------------------------------------------------------------------
# 3.5 workflow catalog (generated from app/workflows.py — 単一情報源)
# --------------------------------------------------------------------------

def _required_fields(mode: str, workflow_id: str) -> list[str]:
    """Required job fields of one mode + workflow, straight from the validator.

    :func:`app.models.missing_job_fields` is what ``POST /api/jobs`` and
    ``agent_protocol.validate_job`` enforce, so asking it with an *empty* job
    yields exactly the fields that are mandatory — the prompt rules can never
    drift from the code that rejects a job.
    """
    return missing_job_fields(
        mode,
        image_prompt=None,
        video_prompt=None,
        audio_path=None,
        source_image=None,
        end_image=None,
        reference_video=None,
        video_workflow=workflow_id,
    )


def _fields_text(fields: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in fields) if fields else "(なし)"


def _inputs_text(
    pairs: tuple[tuple[str, str], ...], empty: str = "なし"
) -> str:
    if not pairs:
        return empty
    return ", ".join(f"`{field}`（{label}）" for field, label in pairs)


def _catalog_entry_lines(entry: CatalogEntry) -> list[str]:
    """One catalog bullet: what the workflow is, needs and expects."""
    default = " **（既定）**" if entry.id == DEFAULT_VIDEO_WORKFLOW else ""
    lines = [
        f"- `{entry.id}` — {entry.label}{default}",
        f"  - 用途: {entry.description}",
        "  - 必要入力: "
        + _inputs_text(entry.required_inputs, "なし（プロンプトだけで生成できる）"),
    ]
    if entry.optional_inputs:
        lines.append("  - 任意入力: " + _inputs_text(entry.optional_inputs))
    if entry.reference_inputs:
        minimum = (
            f"。参照素材は種類を問わず**合計 {entry.min_references} 件以上必須**"
            "（1 件も無いジョブは拒否されます）"
            if entry.min_references
            else ""
        )
        lines.append(
            "  - 参照素材（複数指定・このワークフローの主入力）: "
            + ", ".join(
                f"`{field}`（{label}・最大 {limit} 件）"
                for field, label, limit in entry.reference_inputs
            )
            + minimum
            + "。**開始フレームは受け取らない**ので `mode: \"i2v\"` 専用で、"
            "`source_image` / `end_image` を書いたジョブは拒否されます"
        )
    if entry.multi_shot is not None:
        lines.append(
            "  - マルチショット**必須**: `multi_shots`（最大"
            f" {entry.multi_shot.max_shots} ショット、各"
            f" `{{prompt, duration}}` で duration は"
            f" {entry.multi_shot.min_duration}〜{entry.multi_shot.max_duration}"
            " 秒の整数）。本文はショット側にあるので `video_prompt` は空のまま"
            "（書いたジョブは拒否されます）"
        )
    if entry.elements is not None:
        lines.append(
            "  - Elements: `kling_elements`（最大"
            f" {entry.elements.max_elements} 要素、各 `{{name, description,"
            f" images}}` で画像は {entry.elements.min_images}〜"
            f"{entry.elements.max_images} 枚）。本文からは `@要素名` で呼び、"
            f"**1 参照が {entry.elements.reference_chars} 文字**を消費する"
            "（宣言していない `@名前` は拒否されます）"
        )
    lines.append(f"  - 音声: {entry.audio}")
    i2v = _fields_text(_required_fields("i2v", entry.id))
    # the same helper POST /api/jobs uses, so "full is impossible" can never be
    # claimed here while the API accepts it
    if not video_workflow_problem("full", entry.id):
        full = _fields_text(_required_fields("full", entry.id))
        lines.append(
            f'  - 必須フィールド: `mode: "i2v"` -> {i2v} /'
            f' `mode: "full"` -> {full}'
        )
    else:
        lines.append(
            f'  - 必須フィールド: `mode: "i2v"` -> {i2v} /'
            ' `mode: "full"` は使えない（生成した開始フレームを受け取れない'
            "ワークフローなので、`continue` の行き先にもできない）"
        )
    lines += _select_lines(entry)
    if entry.prompt_hint:
        lines.append(f"  - Writing `video_prompt`: {entry.prompt_hint}")
    if entry.notes:
        lines.append(f"  - Notes: {entry.notes}")
    return lines


def _select_lines(entry: CatalogEntry) -> list[str]:
    """選択式フィールドの案内（宣言していないワークフローでは何も出ない、§3.1）。

    自由記述ではなく決まった選択肢で挙動が決まるワークフロー向け。
    ジョブの ``selects`` に書ける名前と、その値をそのまま列挙する。
    """
    if not entry.selects:
        return []
    lines = [
        "  - 選択項目（ジョブの `selects` に"
        ' `{"<名前>": "<選んだ値>"}` の形で書く。以下の文字列のみ使用可）:'
    ]
    for name, label, choices, default, auto, hint, choice_labels in entry.selects:
        # **書くのは常にバッククォートの中の生の値**。日本語ラベルは意味の手がかり
        # としてだけ添える（宣言の無い値はそのまま）。
        values = ", ".join(
            f"`{choice}`"
            + (f"（{choice_labels[choice]}）" if choice_labels.get(choice) else "")
            for choice in choices
        )
        note = f" {hint}" if hint else ""
        fallback = (
            "省略すると入力から自動で決まる"
            if auto
            else f"省略すると `{default}`"
        )
        lines.append(f"    - `{name}`（{label}）: {values} — {fallback}。{note}")
    return lines


# 接続先ごとの「MiniMax H3 のどの版を選ぶか」。turbo / opt は専用の量子化
# ウェイトと MiniMax H3 系カスタムノードが要るので、それが入っている前提の
# local だけで勧める（runpod / comfy_cloud は素の版が既定）。
def _catalog_entries(
    entries: Iterable[CatalogEntry],
    only: Collection[str] | None = None,
    comfy_target: str = "",
) -> list[CatalogEntry]:
    """カタログに載せるワークフローだけを残す。

    ``only`` は ``GET /api/options`` が返したワークフロー id の集合（フォームに
    出ていないものはエージェントにも出さない）。``comfy_target`` を渡すと、その
    接続先で動かないワークフロー（Comfy Cloud の MiniMax H3 turbo / opt）も落ちる
    ので、``only`` を持たない直接の呼び出しでも同じ一覧になる。
    """
    kept: list[CatalogEntry] = []
    for entry in entries:
        if only is not None and entry.id not in only:
            continue
        if comfy_target and not supported_on_target(get_spec(entry.id), comfy_target):
            continue
        kept.append(entry)
    return kept


def _target_preference_lines(comfy_target: str) -> list[str]:
    """接続先（`Settings.comfy_target`）に合わせた MiniMax H3 の版の選び方。"""
    if not comfy_target:
        return []
    lines = [
        "",
        f"## この環境の接続先: `{comfy_target}`",
        "",
    ]
    if comfy_target == "comfy_cloud":
        # Comfy Cloud には任意のカスタムノードを入れられないので、turbo / opt は
        # 上のカタログからも消えている（:func:`_catalog_entries`）。
        lines += [
            "Comfy Cloud には MiniMax H3 系のカスタムノードと量子化ウェイトを"
            "入れられないので、`_turbo` / `_opt` は選ばないこと"
            "（上のカタログにも載せていない）。MiniMax H3 は **素の版"
            "（`minimax_h3_i2v` / `minimax_h3_r2v` / `minimax_h3_t2v`）だけ**を"
            "使う。",
            "ユーザーが turbo / opt を名指ししてきたときも、**この接続先では"
            "動かない**ことを伝えて素の版を提案すること。",
        ]
        return lines
    if comfy_target == "local":
        lines += [
            "ローカルの ComfyUI には MiniMax H3 系のカスタムノードと量子化"
            "ウェイトが入っているので、MiniMax H3 を使うときは"
            " **`_opt` 版（`minimax_h3_t2v_opt` / `minimax_h3_i2v_opt` /"
            " `minimax_h3_r2v_opt`）を"
            "優先**して選ぶこと。opt は素の版と入力もプロンプトの書き方も同じで、"
            "20 ステップのまま実行だけが速い（品質は素の版相当）。",
        ]
    else:
        lines += [
            "この接続先には MiniMax H3 系のカスタムノードと量子化ウェイトが"
            "無い前提なので、MiniMax H3 を使うときは **素の版"
            "（`minimax_h3_i2v` / `minimax_h3_r2v` / `minimax_h3_t2v`）を優先**し、"
            "`_turbo` / `_opt` は選ばないこと。",
        ]
    lines += [
        "ただし**ユーザーがワークフローを名指ししたとき、あるいは turbo /"
        " opt を明示的に指定したときは、その指定が最優先**でこの既定より優先"
        "される。",
    ]
    return lines


def workflow_catalog_section(
    comfy_target: str = "", only: Collection[str] | None = None
) -> str:
    """The `video_workflow` catalog embedded in the agent system prompt.

    ``comfy_target`` は :class:`app.models.Settings` の接続先で、渡すと末尾に
    「どの版を優先するか」の節が付き（空なら何も足さない）、その接続先で動かない
    ワークフローは一覧から落ちる。``only`` は ``GET /api/options`` が返した
    ワークフロー id の集合で、フォームに出ていないものはカタログにも出さない。
    システムプロンプトはセッション作成時に一度だけ組み立てて焼き込むので、
    途中で接続先を変えても既存セッションの文面は変わらない（新しいセッションから
    効く）。
    """
    lines = [
        "# VIDEO WORKFLOWS (the `video_workflow` field of a job)",
        "",
        "Every video job runs exactly one of these ComfyUI graphs. Pick the one"
        " whose",
        "required inputs you actually have, and write `video_prompt` the way that",
        "workflow wants it — where a workflow's own note and the generic VIDEO",
        "PROMPT SPEC disagree, the workflow's note wins.",
        "",
    ]
    for entry in _catalog_entries(video_catalog(comfy_target), only, comfy_target):
        lines += _catalog_entry_lines(entry)
    lines += [
        "",
        f"Omitting `video_workflow` selects `{DEFAULT_VIDEO_WORKFLOW}`.",
        '`mode: "image_only"` runs no video stage at all, so `video_workflow` is'
        " ignored there",
        f"and only {_fields_text(_required_fields('image_only', DEFAULT_VIDEO_WORKFLOW))}"
        " is required.",
        "",
        "アセットのパスは CHOICES に挙がっているものだけを使い、そのワークフローが"
        "使わない入力は送らないこと（未知のフィールドはエラーになります）。",
    ]
    lines += _target_preference_lines(comfy_target)
    return "\n".join(lines)


def _image_catalog_entry_lines(entry: CatalogEntry) -> list[str]:
    """One IMAGE WORKFLOWS bullet: what it is, what it needs, how to prompt it."""
    default = " **（既定）**" if entry.id == DEFAULT_IMAGE_WORKFLOW else ""
    family = FAMILY_LABELS.get(entry.family, entry.family)
    lines = [
        f"- `{entry.id}` — {entry.label}{default}",
        f"  - モデルファミリー: `{entry.family}`（{family}）",
        f"  - 用途: {entry.description}",
        "  - 必要入力: "
        + _inputs_text(entry.required_inputs, "なし（image_prompt だけで生成できる）"),
    ]
    # 参照素材を主入力にする画像ワークフロー（MiniMax H3 Image r2i）だけに出る。
    # VIDEO WORKFLOWS 側と同じ書きぶりで、こちらは開始フレームの代わりに
    # `reference_images` を並べる（`source_image` は受け取らない）。
    if entry.reference_inputs:
        minimum = (
            f"。参照素材は**合計 {entry.min_references} 件以上必須**"
            "（1 件も無いジョブは拒否されます）"
            if entry.min_references
            else ""
        )
        lines.append(
            "  - 参照素材（複数指定・このワークフローの主入力）: "
            + ", ".join(
                f"`{field}`（{label}・最大 {limit} 件）"
                for field, label, limit in entry.reference_inputs
            )
            + minimum
            + "。渡した順が `<Picture 1>`, `<Picture 2>`, … の番号になり、"
            "**開始フレーム（`source_image`）は受け取らない**"
        )
    lines += _select_lines(entry)
    hint = IMAGE_PROMPT_HINTS.get(entry.family)
    if hint:
        lines.append(f"  - Writing `image_prompt`: {hint}")
    if entry.notes:
        lines.append(f"  - Notes: {entry.notes}")
    return lines


def image_workflow_catalog_section(
    comfy_target: str = "", only: Collection[str] | None = None
) -> str:
    """The `image_workflow` catalog embedded in the agent system prompt.

    ``comfy_target`` / ``only`` は :func:`workflow_catalog_section` と同じ意味
    （フォームに出ていないワークフローはカタログにも出さない）。
    """
    lines = [
        "# IMAGE WORKFLOWS (the `image_workflow` field of a job)",
        "",
        "Every job that runs an image stage (`mode` `full` or `image_only`) uses"
        " one of",
        "these ComfyUI graphs. They are **different models and want different"
        " prompts** —",
        "once you pick one, write `image_prompt` in that family's style"
        " (the IMAGE PROMPT",
        "GUIDES section right below has the full rules per family).",
        "",
    ]
    for entry in _catalog_entries(image_catalog(), only, comfy_target):
        lines += _image_catalog_entry_lines(entry)
    lines += [
        "",
        f"Omitting `image_workflow` selects `{DEFAULT_IMAGE_WORKFLOW}`."
        ' `mode: "i2v"` runs no image',
        "stage, so `image_workflow`, `image_prompt` and `loras` are ignored"
        " there.",
        "",
        "画像用 LoRA は**モデルファミリー単位**で登録されています。選んだ"
        " `image_workflow` と同じ",
        "ファミリーの LoRA しか `loras` に入れられません（不一致のジョブは"
        "拒否されます）。",
    ]
    return "\n".join(lines)


def _image_workflow_context_lines(workflow_id: str) -> list[str]:
    """The selected image workflow, for the chat CONTEXT section (SPEC §4.3)."""
    try:
        spec = get_image_spec(workflow_id)
    except WorkflowSpecError:
        return []
    entry = catalog_entry(spec)
    lines = [
        "",
        f"Selected image workflow: **`{entry.id}`**（{entry.label}）"
        f" — model family `{entry.family}`",
        f"- 用途: {entry.description}",
    ]
    if entry.required_inputs:
        lines.append(f"- 必要入力: {_inputs_text(entry.required_inputs)}")
    hint = IMAGE_PROMPT_HINTS.get(entry.family)
    if hint:
        lines += [
            f"- How to write `image_prompt` for it: {hint}",
            "  The IMAGE PROMPT SPEC above is the one for this model — follow"
            " it, and",
            "  interview the user accordingly.",
        ]
    return lines


def _workflow_context_lines(workflow_id: str) -> list[str]:
    """The selected video workflow, for the chat CONTEXT section (SPEC §4.3)."""
    try:
        spec = get_video_spec(workflow_id)
    except WorkflowSpecError:
        return []
    entry = catalog_entry(spec)
    lines = [
        "",
        f"Selected video workflow: **`{entry.id}`**（{entry.label}）",
        f"- 用途: {entry.description}",
        f"- 入力: {_inputs_text(entry.required_inputs, 'なし（プロンプトのみ）')}",
        f"- 音声: {entry.audio}",
    ]
    if entry.prompt_hint:
        lines += [
            f"- How to write `video_prompt` for it: {entry.prompt_hint}",
            "  This is authoritative: where it disagrees with the VIDEO PROMPT",
            "  SPEC above, follow this. Interview the user accordingly (do not",
            "  ask about things this workflow does not take from the prompt).",
        ]
    return lines


# --------------------------------------------------------------------------
# 3.6a per-workflow video prompt guides（選んだワークフローの分だけ埋め込む）
# --------------------------------------------------------------------------
# VIDEO SPEC は汎用のもの。モデルごとに書き方も守備範囲も違うので、
# 画像 / 音声と同じ流儀で「モデルごとのガイド」を持ち、チャットでは**選択中の
# ワークフローの分だけ**注入する（両方渡すと混ざる）。
#
# MiniMax H3（ローカル ComfyUI）— 出典:
# * 公式 rewrite 契約（一次）:
#   https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md
#   https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md
#   https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing
# * 二次（このアプリのグラフ / タグ番号）:
#   https://docs.comfy.org/tutorials/video/minimax/minimax-h3
#   https://www.minimax.io/blog/minimax-h3

#: t2v / i2v / r2v で共通のショット・カメラ・台詞・音・禁止事項
MINIMAX_H3_GUIDE_BODY = """\
Shared shot / camera / speech / sound rules (base `integrated_multimodal_description`
and Ref2VA `detailed_description`):

- `[Shot 1]` has **no timestamp**. Later shots: `[Shot 2] At 00:03.500, the
  camera cuts to …` with strictly increasing cut times inside the job
  `duration`. Prefer `the camera cuts to` for ordinary cuts; `the shot cuts to`,
  `the shot transitions to`, `the shot changes to` and `the shot switches to`
  are equally valid. Cross-dissolve, fade and wipe only when the user asked
  for them.
- **Cut only for new information** about subject, space, state, viewpoint or
  time. If just the distance or the angle changes a little, keep one shot and
  write camera motion instead.
- Start Shot 1 with style + initial composition
  (`Live-action, cinematic, a medium-wide shot frames…`). Ref2VA puts those 1–2
  style sentences **before** `[Shot 1]`. Common styles: `Cinematic`,
  `live-action`, `2D-animated`, `3D CG`, `claymation`, `watercolor`,
  `vintage film`. Derive the style from the attached picture on keyframe /
  reference tasks; pick it from the user's text on T2VA. One style only,
  unless the user explicitly wants a mix (then say how the two combine).
- Camera motion is a **natural English clause inside the shot**, never a
  `Camera:` footer. Vocabulary: Zoom In/Out, Push In / Pull Out, Pan Left/Right,
  Truck Left/Right, Tilt Up/Down, Pedestal Up/Down, Arc Shot, Tracking Shot,
  Static Shot, Shake Slightly/Strongly, POV, Roll Clockwise/Counterclockwise;
  optional `with small/large amplitude`, `at slow/fast speed`. Add amplitude
  and speed **only when they mean something** — medium amplitude and normal
  speed are simply omitted.
- Speakers: stable `(S1)`, `(S2)`; compound `(S1,S2)` when they speak together.
  Identifying phrase + ID + delivery **outside** `<d>`; inside `<d>` only
  `[Language]` + the user's verbatim words. Do not translate dialogue.
  Example: `The young woman with a quiet, breathy voice (S1) says: <d>[Japanese] いらっしゃい</d>`
  On a speaker's **first appearance** establish a stable identity from what the
  material actually shows: character type, age, gender, on-screen or off,
  pitch, timbre, speaking rate, accent.
  Voiceover: exact phrase `says in an off-screen voiceover`, then state lips
  remain closed. Cross-cut speech: `<scenetrans>` at both connecting points,
  plus one of `continues seamlessly across the cut`, `continues uninterrupted
  into the next shot`, `carries over from the previous shot`, `remains audible
  across the transition`. Truncated by clip end: `<cutoff>`.
- Transcribing dialogue from a reference audio: keep the source words and the
  original language verbatim inside `<d>`, write `[unclear]` for spans you
  cannot make out (never guess), normalise punctuation to `,` `.` `?` `!`, and
  drop repeated tildes, emoji and decorative marks. If only the timbre,
  rhythm or delivery is being referenced, do **not** carry the source lines
  over.
- Visible on-screen text stays in English double quotes, original language, no
  translation (`A neon sign reading "営業中"`).
- `overall_soundscape:` 1–4 English sentences. Ambience + physical + non-verbal
  human sounds. Do **not** repeat dialogue/singing. `N/A` only if the user
  wants complete silence.
- `non_diegetic_music:` 1–3 sentences of instrumentation / tempo / dynamics
  (not mood words). Diegetic music belongs in the description. `N/A` when none.
- Never write a `Camera:` or `Audio:` footer and never use `[0s-1.5s] Shot 1:`
  stamps.

Hard rules (this app):

- Duration 4–15s; the app snaps the length up to the model's 17k+5 frame grid
  at 24fps. Do not write frame counts or fps in the text.
- The native canvas is a **768px short edge (max 768x1344)**: keep `megapixels`
  around 0.4. Larger costs time and drifts. (That is this local install's
  working size, not the model's ceiling — never write resolution claims into
  the prompt text.)
- `duration`, `megapixels` and `aspect_ratio` are job fields, never sentences.
- There is **no negative prompt** (no CFG). If the body does not already
  mention subtitles/watermarks, finish with
  `No text, subtitles, logos or watermarks.` Official `<d>` is how speech is
  spoken; that sentence is a safety net against burnt-in captions. Leave it
  out when on-screen typography or a UI is the subject of the shot.
- Number tags **per type, in attach order**: `<Picture 1>`, `<Video 1>`,
  `<Audio 1>`. Never write a tag with nothing behind it.
"""

MINIMAX_H3_VIDEO_GUIDE = (
    """\
# VIDEO PROMPT SPEC — MiniMax H3 (local ComfyUI, `minimax_h3_t2v*` / `minimax_h3_i2v*`)

MiniMax H3 generates the picture **and native stereo audio** in one pass and
can hold several cuts inside a single clip. This is the only video contract
for this job. Write **one English document** in the official rewrite fields —
never a `Camera:` / `Audio:` footer.

Base modes (`minimax_h3_t2v*`, `minimax_h3_i2v*`): this order.

1. Optional **alignment first line** (then a blank line). T2VA has none.
   - I2VA (start frame only):
     `For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`
   - FL2VA (`end_image` present):
     `How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.`
     (`N` = last shot index, `S.SS` = duration with exactly two decimals)
   - L2VA (last-frame only; chat may write it, studio i2v does not):
     `How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.`
2. Three fields:
   ```
   integrated_multimodal_description: [Shot 1] ...
   overall_soundscape: ...
   non_diegetic_music: ...
   ```

- `minimax_h3_t2v` has **no start frame**: Shot 1 must also fix the subject,
  wardrobe and set.
- `minimax_h3_i2v`: Picture 1 **is** frame 0 of Shot 1. Path: first-frame
  anchor → action onset → development → result. Never contradict the start
  frame.
- `minimax_h3_i2v` + `end_image` (FL2VA): prefer a **single shot**; describe the
  **path** between Picture 1 and Picture 2, do not re-describe both stills.
  Without `end_image`, leave it out entirely.

Short T2VA example:

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a baker opening the shutters of a small street bakery. The camera pushes in with small amplitude at slow speed as the baker with a calm, slightly raspy voice (S1) says: <d>[English] First batch of the morning.</d> [Shot 2] At 00:05.000, the camera cuts to a close-up of steam rising from the sliced bread.

overall_soundscape: Wooden shutters scrape open over a quiet street. Trays clink; bread is sliced.

non_diegetic_music: A soft acoustic-guitar pattern at a moderate tempo, fading out.
```

"""
    + MINIMAX_H3_GUIDE_BODY
)

MINIMAX_H3_REFERENCE_VIDEO_GUIDE = (
    """\
# VIDEO PROMPT SPEC — MiniMax H3 reference mode (local ComfyUI, `minimax_h3_r2v*`)

This workflow runs **Ref2VA** (`minimax_h3_r2v`, including `_context` / `_save`
/ `_turbo` / `_opt`). It takes **no start frame**: up to 9 `reference_images`,
3 `reference_videos` and 3 `reference_audios` are references for identity,
look, motion and sound. At least one is required. This is the only video
contract for this job. Motion Context (`minimax_h3_r2v_context`) uses the
same writing rules.

Six English sections, this order:

```
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

- `subject_definitions`: one line per tracked item. `<Subject N>` = reusable
  visible content (person, set, wardrobe, style, motion). Standalone
  `<Picture N>` only if the image is a concrete frame / storyboard /
  composition anchor — if it only defines a character, cite it *inside* the
  Subject line. `<Video N>` = edit source / continuation / whole-video
  structure (not a person extracted from the video). `<Audio N>` = copied or
  referenced audio.
- `summary`: one short paragraph starting with `[task type]` or
  `[type + type]`. Types: `keyframe completion`, `reference generation`,
  `video editing`, `video continuation`, `audio reuse`, `audio reference`.
  Combined with ` + `. **Attachment alone creates no task type**: a reference
  video that only supplies camera movement, cuts or rhythm is
  `reference generation`, not `video editing`. Editing a source video whose
  original audio stays audible adds `audio reuse`; continuing one without
  copying the signal adds `audio reference`. For an editing task the summary
  starts, right after the prefix, with
  `The target video is an edited version of <Video 1>.`
- `retention_analysis`: one line per label.
  Visible markers: `fully_preserved` / `partially_preserved` /
  `attribute_transfer` / `weak_reference`
  Audio markers: `fully_copy` / `partially_copy` / `reference` /
  `weak_reference`
  The parenthesis differs per label — Subject = where it appears, Picture /
  Video = what role it plays:
  `<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - …`
  `<Picture 2> ([Shot 1] first frame): fully_preserved - …`
  `<Video 1> (cut and pacing structure): weak_reference - …`
  `<Audio 1>: reference - …`
  Never write `(Sx)` in `retention_analysis`.
- `(Sx)` belongs to the target video's speakers, numbered in the order they
  actually speak, and is reused everywhere that voice appears — including an
  `<Audio N>` bound to that speaker in `subject_definitions`, which never
  assigns a new one. A voice that exists only inside a reused BGM or complete
  soundtrack gets **no** `(Sx)`: name `<Audio N>` as the source instead.
- State a copied / referenced audio relationship in the section that carries
  that layer: ambience and effects in `overall_soundscape`, audience-only
  score in `non_diegetic_music`, and in both when one audio supplies both.
- `detailed_description`: style in 1–2 sentences **before** `[Shot 1]`. Then
  the same shot / camera / dialogue rules as base. Insert labels where they
  apply. Generation tasks target ~350–500 English words when the source has
  enough facts; do not invent plot to pad. With dense dialogue, a complete
  speech timeline outranks the word count; editing tasks follow the source's
  complexity instead of the range; a single shot is never a reason to be brief.
- Do not re-describe a reference as if it were the shot. Name **which
  reference drives which part**.

This app's tag numbering (overrides official pairing):

- Number tags **per type, in attach order**: `<Picture 1>`, `<Video 1>`,
  `<Audio 1>`.
- **Every reference video is passed with its soundtrack.** Those soundtracks
  **share `<Audio j>` numbering with standalone `reference_audios` and take
  the low numbers.** Example: 2 videos + 1 audio → `<Audio 1>` / `<Audio 2>`
  are the videos' soundtracks, `<Audio 3>` is the standalone track. (Official
  Ref2VA says a reference video creates no `<Audio N>` by itself; the ComfyUI
  graph here always hands the soundtrack over, so this app's rule wins.)
- Never write a tag with nothing behind it.

Short Ref2VA skeleton (fill from the attached material; do not invent plot):

```
subject_definitions:
<Subject 1> is the woman in <Picture 1>, with …
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).

summary:
[reference generation + audio reference] The target video shows <Subject 1> …

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - …
<Audio 1>: reference - timbre only, signal not copied.

detailed_description:
The target video is live-action and cinematic, warm practical lighting.
[Shot 1] A medium shot frames <Subject 1>. The camera pushes in with small amplitude at slow speed. <Subject 1> (S1) says: <d>[Japanese] いらっしゃい</d>

overall_soundscape:
Quiet indoor room tone and light rain on the awning.

non_diegetic_music:
Sparse piano at a slow tempo.
```

"""
    + MINIMAX_H3_GUIDE_BODY
)

#: 1 本のクリップの中でカットを割れるワークフロー（CONTEXT の一文を切り替える）。
#: 汎用の VIDEO PROMPT SPEC は「1 クリップ 1 ショット」を前提にしているが、
#: MiniMax H3 は公式の `[Shot N]` タイムラインを書けば複数ショットを 1 本に収められる。
#: turbo / opt / ラテント保存版は素の版と入力の形もプロンプトの書き方も同じなので、
#: ガイドの登録は素の版と同じものを共有する（ここに載っていないと汎用の
#: :data:`VIDEO_SPEC` に落ちてしまう）。
#: ``t2v`` / ``i2v`` / ``r2v`` の論理モードごとの、素・ラテント保存（``_save``）・
#: 連続カット（``_context``）× 品質（``_turbo`` / ``_opt``）の全 id。どれも入力の形も
#: プロンプトの書き方も素の版と同じなので、ガイドの登録は素の版と共有する。
def _minimax_h3_ids(mode: str) -> tuple[str, ...]:
    bases = [f"minimax_h3_{mode}", f"minimax_h3_{mode}_save"]
    if mode == "r2v":
        bases.append("minimax_h3_r2v_context")
    return tuple(
        f"{base}{suffix}" for base in bases for suffix in ("", "_turbo", "_opt")
    )


MULTI_CUT_WORKFLOWS: frozenset[str] = frozenset(
    workflow_id
    for mode in ("t2v", "i2v", "r2v")
    for workflow_id in _minimax_h3_ids(mode)
)

#: 前のカットの映像と AV ラテントを引き継ぐワークフロー（Motion Context）。
#: プロンプトの書き方は素の r2v と同じなので、CONTEXT に一言添えるだけでよい。
MOTION_CONTEXT_WORKFLOWS: frozenset[str] = frozenset(
    {
        "minimax_h3_r2v_context",
        "minimax_h3_r2v_context_turbo",
        "minimax_h3_r2v_context_opt",
    }
)

#: workflow id -> そのモデル専用の VIDEO PROMPT SPEC（無いワークフローは
#: 汎用の :data:`VIDEO_SPEC` のまま）。参照モード（r2v）だけ別のガイドで、連続カット版も
#: 参照モードの上に Motion Context を足しただけなので同じものを読む。
VIDEO_SPECS: dict[str, str] = {
    **{
        workflow_id: MINIMAX_H3_VIDEO_GUIDE
        for mode in ("t2v", "i2v")
        for workflow_id in _minimax_h3_ids(mode)
    },
    **{
        workflow_id: MINIMAX_H3_REFERENCE_VIDEO_GUIDE
        for workflow_id in _minimax_h3_ids("r2v")
    },
}


def video_guide_for(workflow_id: str) -> str:
    """選んだ動画ワークフロー専用のガイド（無ければ空文字）。"""
    return VIDEO_SPECS.get(workflow_id, "")


def video_prompt_guides_section() -> str:
    """モデル固有のガイドをまとめた節（エージェントのプロンプト用）。

    :func:`app.workflows.video_catalog` から引くので、カタログに無いワークフロー
    のガイドは出ない。1 つも無ければ節ごと出さない。
    """
    guides: list[str] = []
    for entry in video_catalog():
        guide = video_guide_for(entry.id)
        if guide and guide not in guides:
            guides.append(guide)
    if not guides:
        return ""
    header = (
        "# VIDEO PROMPT GUIDES (model-specific — use the one that matches the"
        "\n`video_workflow` of the job you are writing; that guide is the video"
        "\ncontract for the job)"
    )
    return "\n\n".join([header, *guides])


# --------------------------------------------------------------------------
# 3.7 audio prompt guides (agent mode only — the chat never writes audio jobs)
# --------------------------------------------------------------------------
# Summarized from each model's primary sources:
#
# * MiniMax Music 3 — https://github.com/MiniMax-AI/MiniMax-Music3 (README and
#   skills/music-caption-rewriter/SKILL.md: the three Structured Caption
#   sections, the 250-450 word target, the section tag list, the "do not invent
#   unstated BPM / key / vocal gender" rule) and
#   https://docs.comfy.org/tutorials/audio/minimax/minimax-music-3 (the ComfyUI
#   workflow this template comes from: songs of up to ~5 minutes, 32 kHz
#   16-bit stereo output, 5,000 token text budget).  The node's own widget
#   bounds are in comfy_extras/nodes_minimax_music.py, mirrored in
#   app.workflows.
# * Stable Audio 3 —
#   https://github.com/Stability-AI/stable-audio-3/blob/main/docs/guides/prompting.md
#   (Music = genre / instruments / mood / BPM; SFX = source / action /
#   production; medium tops out at 6m20s = 380s) plus the per-category prompt
#   templates embedded in ComfyUI's own stable_audio_3_medium template
#   (docs.comfy.org/tutorials/audio/stable-audio/stable-audio-3), which is the
#   very text this workflow's Enable_Reprompt branch feeds to its local LLM.
#   Stability's guides document no negative prompt, and this template exposes
#   none, so never write one.

MINIMAX_MUSIC_3_SPEC = """\
# AUDIO PROMPT SPEC — MiniMax Music 3 (`minimax_music_3`)

This model writes **songs**: a full arrangement, with a singer when `lyrics`
are given and an instrumental when they are not. Output is 32 kHz 16-bit
stereo, up to about 5 minutes (300s) per track.

`audio_prompt` is the track's **Structured Caption**: prose, written as three
labelled sections in this order, 250-450 words in total.

1. `Global Metadata:` — genre and sub-genre, tempo, key / scale (only when it
   matters), the emotional arc across the track, the listening setting, and the
   production character (recording style, texture, mix, era).
2. `Vocal Details:` — the lead vocal setup, gender / timbre / register,
   delivery and phrasing, harmonies and backing vocals, and vocal effects
   (sparingly). Never retell or paraphrase what the lyrics say. For an
   instrumental, name the instrument that carries the melody instead.
3. `Arrangement:` — a section-by-section timeline (Intro -> Verse -> Chorus ->
   …): which instruments enter and leave and when, how the groove develops, the
   transitions, the texture and the spatial effects. Prefer concrete musical
   changes over decorative prose.

Do **not** invent specifics the user did not give: write an exact BPM, key or
vocal gender only when they asked for one — otherwise give a range
(`around 70-80 BPM`) or a qualitative description (`slow and unhurried`). Any
constraint or exclusion the user stated must survive into the caption.

`lyrics` carries the words, with **section tags on their own lines**:
`[Intro]` `[Verse]` `[Pre-Chorus]` `[Chorus]` `[Post-Chorus]` `[Bridge]`
`[Instrumental]` `[Solo]` `[Outro]`.
- The words to sing live **only** in `lyrics` — never in `audio_prompt`.
- Blank line between sections; keep the line breaks.
- Write them in the language they should be sung in (Japanese lyrics stay
  Japanese).
- **Instrumental**: leave `lyrics` empty (or write structure tags only, e.g.
  `[Intro]` / `[Solo]` / `[Outro]`) and describe the lead instrument in
  `Vocal Details:`.

`duration` is the track length in seconds — it is an *upper bound*, so the
model may end the song earlier.

Example:
```
"audio_prompt": "Global Metadata: Dreamy city-pop ballad with a modern neo-soul tint, around 80 BPM, minor key with jazzy extensions. Nostalgic and bittersweet, opening intimate and widening into a spacious chorus before settling back down. Late-night headphone listening after rain. Warm analog production: tape saturation, soft-clipped drums, wide but uncrowded stereo image.\\n\\nVocal Details: Single female lead, breathy low register with an airy top, laid-back behind-the-beat phrasing, close-mic'd and intimate in the verses, opening into a fuller belted tone in the chorus. Doubled octave harmonies and soft wordless backing pads in the chorus only, light plate reverb and short slap delay.\\n\\nArrangement: Intro: solo warm Rhodes chords with tape wobble, faint vinyl noise. Verse: fretless bass and brushed drums enter with a relaxed pocket, Rhodes thins out to leave space for the vocal. Pre-Chorus: sustained string pad rises, hi-hats open up, a single rising bass fill leads in. Chorus: full band, wide layered harmonies, ride cymbal wash, bass moving in melodic countermelody. Bridge: everything drops to Rhodes and voice, one distant muted trumpet line. Outro: the band fades out one layer at a time, ending on an unresolved Rhodes chord.",
"lyrics": "[Verse]\\nThe last train hums below the rain\\nYour name still lights the window pane\\n\\n[Chorus]\\nWe burn like neon in the dark\\nStill holding on (holding on)"
```
"""

STABLE_AUDIO_SPEC = """\
# AUDIO PROMPT SPEC — Stable Audio 3 Medium (`stable_audio_3_medium_base`)

General-purpose audio: sound effects, one-shots, single-instrument stems and
**instrumental** music. It does not sing — a song with words needs
MiniMax Music 3.

`audio_prompt` is one dense natural-language description of the sound, and
`audio_category` picks which of the graph's built-in templates shapes it
({categories}). Write it the way that category wants:

- **Music** — `<genre/style> with <lead instruments>, <supporting layers>, and
  <rhythm/percussion> creating <mood/energy>. BPM: X. Length: Y seconds` — one
  flowing sentence, no semicolons, BPM and length spelled out at the end.
- **Instrument** — the instrument, the playing technique, its timbre and
  texture, the musical style, the room / production, then `BPM: X. Length: Y
  seconds`. Loops 6-20s, stems 20-180s.
- **SFX** — the sound source, its physical character (material: metal, wood,
  glass, concrete), the space (indoor / outdoor, dry / reverberant, close /
  distant), how it evolves over time and any movement, then
  `Length: Y seconds`. Impacts 1-3s, mechanical actions 3-6s, ambiences 6-15s.
- **One-shot** — the source, the kind of hit (pluck, slam, tap, stab), the
  material and timbre, the production (dry / wet, close-mic'd), then
  `Length: Y seconds` — 1-11s.

Always keep `duration` and the `Length:` you wrote in the text the same number,
and set it to what the sound actually needs — a 3-second door slam stretched to
60 seconds comes back padded with silence or noise. The medium checkpoint
supports up to 380 seconds.

`reprompt: true` hands your description to the graph's own local LLM, which
expands it with the official per-category template before it is encoded. Leave
it **false** when you already wrote the full structured prompt yourself (the
normal case here); set it true only for a deliberately terse one-liner.
There is no negative prompt in this workflow — never write one.

Examples:
```
"audio_category": "SFX", "audio_prompt": "Heavy rain hitting a corrugated metal roof during a thunderstorm, distant thunder rumbles, wide stereo field, realistic close ambience. Length: 45 seconds", "duration": 45
"audio_category": "Music", "audio_prompt": "Jazz ballad with smooth saxophone lead, warm piano chords, upright bass, brushed drums, and soft strings that swing gently for a cozy late-evening mood. BPM: 85. Length: 120 seconds", "duration": 120
```
"""

#: audio workflow id -> the AUDIO PROMPT SPEC section to embed for it
AUDIO_SPECS: dict[str, str] = {
    "minimax_music_3": MINIMAX_MUSIC_3_SPEC,
    "stable_audio_3_medium_base": STABLE_AUDIO_SPEC,
}

#: audio workflow id -> the one-line reminder in the AUDIO WORKFLOWS catalog
AUDIO_PROMPT_HINTS: dict[str, str] = {
    "minimax_music_3": (
        "A Structured Caption of the track in three labelled sections"
        " (`Global Metadata:` / `Vocal Details:` / `Arrangement:`), 250-450"
        " words; the words to sing go in `lyrics` with [Verse] / [Chorus]"
        " section tags, never in the caption. No lyrics == instrumental."
    ),
    "stable_audio_3_medium_base": (
        "One dense sentence describing the sound itself, shaped by"
        " `audio_category`, ending in `BPM: X. Length: Y seconds` (music) or"
        " `Length: Y seconds` (SFX / one-shot). Never lyrics."
    ),
}


def audio_spec_for(workflow_id: str) -> str:
    """The AUDIO PROMPT SPEC section of one audio workflow (formatted)."""
    template = AUDIO_SPECS.get(workflow_id, MINIMAX_MUSIC_3_SPEC)
    return template.replace("{categories}", " / ".join(AUDIO_CATEGORIES))


def audio_prompt_guides_section() -> str:
    """Every audio workflow's prompt guide, for the agent system prompt.

    Driven by :func:`app.workflows.audio_catalog`, so a workflow added to
    ``workflow/audio/`` without a guide here falls back to the MiniMax Music 3
    one rather than silently going missing.
    """
    header = (
        "# AUDIO PROMPT GUIDES (one per audio model — use the one that matches"
        " the\n`audio_workflow` of the job you are writing)"
    )
    return "\n\n".join(
        [header, *(audio_spec_for(entry.id) for entry in audio_catalog())]
    )


def _audio_catalog_entry_lines(entry: CatalogEntry) -> list[str]:
    """One AUDIO WORKFLOWS bullet: what it is, what it takes, how to prompt it."""
    default = " **（既定）**" if entry.id == DEFAULT_AUDIO_WORKFLOW else ""
    extra = [
        name
        for name in ("lyrics", "negative_tags", "audio_category", "reprompt")
        if name in entry.supports
    ]
    # 尺を宣言しないモデル（API に長さのパラメータが無いもの）は、
    # 「0〜0 秒」と書くより指定できないと言うほうが誤解が無い。
    length = (
        f"  - `duration`: {entry.min_duration:g}〜{entry.max_duration:g} 秒"
        f"（省略時 {entry.default_duration:g} 秒。範囲外のジョブは拒否されます）"
        if entry.max_duration > 0
        else "  - `duration`: このモデルには長さの指定がありません"
        "（書いても無視されるので省略してください）"
    )
    lines = [
        f"- `{entry.id}` — {entry.label}{default}",
        f"  - 用途: {entry.description}",
        f"  - 必須フィールド: `mode: \"audio\"`, `audio_prompt`",
        "  - 追加フィールド: "
        + (", ".join(f"`{name}`" for name in extra) if extra else "なし"),
        length,
    ]
    lines += _select_lines(entry)
    hint = AUDIO_PROMPT_HINTS.get(entry.id)
    if hint:
        lines.append(f"  - Writing `audio_prompt`: {hint}")
    if entry.notes:
        lines.append(f"  - Notes: {entry.notes}")
    return lines


def audio_workflow_catalog_section(
    comfy_target: str = "", only: Collection[str] | None = None
) -> str:
    """The `audio_workflow` catalog embedded in the agent system prompt.

    ``comfy_target`` / ``only`` は :func:`workflow_catalog_section` と同じ意味
    （フォームに出ていないワークフローはカタログにも出さない）。
    """
    lines = [
        "# AUDIO WORKFLOWS (the `audio_workflow` field of a `mode: \"audio\"` job)",
        "",
        "音声は**独立したジョブ**です。`mode: \"audio\"` は音声ワークフローを 1 本",
        "だけ走らせ、画像・動画のステージとは一切連結しません（`mode: \"full\"` の",
        "音声トラックを差し替えることもできません）。動画と音声の両方が要るときは、",
        "音声ジョブと動画ジョブを別のタスクとして並べてください。",
        "",
        "音声ジョブが読むのは `mode` / `audio_workflow` / `audio_prompt` /"
        " `duration` /",
        "`seed` と下の追加フィールドだけです。`image_prompt`・`video_prompt`・",
        "`source_image`・`loras`・`video_loras` などを入れたジョブは拒否されます。",
        "音声ワークフローに LoRA はありません。",
        "",
    ]
    for entry in _catalog_entries(audio_catalog(), only, comfy_target):
        lines += _audio_catalog_entry_lines(entry)
    lines += [
        "",
        f"`audio_workflow` を省略すると `{DEFAULT_AUDIO_WORKFLOW}` になります。",
        "歌詞のある曲は MiniMax Music 3、効果音・環境音・単発の楽器音・インストの",
        "短尺は Stable Audio 3 が向いています。書き方は AUDIO PROMPT GUIDES を",
        "見ること。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 4. few-shot examples (docs/prompt-samples.md)
# --------------------------------------------------------------------------

#: pre-H3 path — not embedded when the selected video workflow has an H3 guide
FEW_SHOT_VIDEO = """\
# FEW-SHOT EXAMPLES — video (real prompts from the model authors' own posts)

MiniMax H3 jobs must follow the MiniMax H3 VIDEO PROMPT SPEC (official fields /
`[Shot N] At MM:SS.mmm` / `<d>`), **not** these legacy one-paragraph examples
and **not** a `Camera:` / `Audio:` footer.

## Video prompts (model authors' own posts)

Example V1 (dialogue + sound effects):
```
voyeur style cum shot and handjob scene.  A woman sitting with her pink panties around her ankles is excited when she sees a big cock poking through the wall, in a british voice she says "Oh Thank god!" as she reaches out and strokes the cock fast.  The huge amounts of white wet cum erupt from the tip of the cock splattering everywhere in endless waves of cum all over the woman, the walls, her panties and the floor as we hear a man moaning with pleasure.  The woman keeps stroking the cock and giggles with sexual enjoyment as even more massive amounts of white cum erupt from the cock tip everywhere in the room
```

Example V2 (dialogue):
```
handjob cum in mouth scene.  A nude man laying on his back is getting his a handjob by a woman with long brown hair and a pink headband.  Her mouth is full of the man's white cum, as shes stroking his cock giving him a handjob she opens her mouth and sticks her tongue out.  All the white warm cum falls out of her mouth and lands on the man's cock as she says "See honey?  wasn't that a nice?".  The man lets out a orgasmic sigh
```

Example V3 (sound description only, no dialogue):
```
amazing mutual masturbation scene with a woman wearing a choker chained to a nude man.  SHe has huge breast and a hairy pussy.  The man has a big belly and erect cock.  The woman is stroking the man's cock in front of the TV while the man is fondling and squeezing her huge tits.  they are both moaning as they pleasure each other and masturbation each other
```

Example V4 (this app's own i2v reference prompt — the target style for
image-to-video, note the "Starting from the given first frame" opener and the
closing sound sentence):
```
adult Japanese woman in sex on a rumpled hotel bed. Starting from the given first frame, the thrusting becomes rapid and intense, short hard strokes in quick succession. Her whole body shakes with the pace, legs tremble, fingers dig into the sheets, and her back arches off the mattress. Her brows lock tight, watery eyes roll upward, mouth open wide as shaky high moans break between gasps. Heavy sweat on her flushed skin, messy dark hair stuck to her face and pillow. Static camera with stronger handheld tremble, tight focus on her climaxing face and torso under harsh practical lighting. Fast bed creaks, sharp body sounds, gasping breaths, and urgent moans continue through the continuous shot.
```

"""

H3_QUALITY_BAR = """\
# QUALITY BAR — MiniMax H3

The MiniMax H3 VIDEO PROMPT SPEC is the only video contract. Do not copy
legacy one-paragraph examples, talkvid visual/speech/sounds blocks,
`Camera:` / `Audio:` footers, or `[0s-1.5s] Shot 1:` stamps. Do not replace
the official I2VA alignment line with a first-frame opener sentence.

- Every added detail is visible or audible. Prefer geometry, contact, and
  cause-and-effect over mood adjectives.
- Do not invent characters, locations, wardrobe, or spoken lines the user
  did not give. Develop the stated action fully: body, contact, eyeline,
  resulting state, camera, synced diegetic sound.
- Shot 1 opens with style + composition, then subject position, environment,
  action/reaction, state change, camera, synced diegetic sound, in playback
  order.
- Time the last shot to the job `duration`. `[Shot 1]` has no timestamp;
  later shots: `[Shot N] At MM:SS.mmm, the camera cuts to …`.
- `video_prompt` is a **complete** official H3 document, not a one-liner wrap
  of the user's sentence.
- Base (t2v / i2v): alignment line if needed, then
  `integrated_multimodal_description` / `overall_soundscape` /
  `non_diegetic_music`.
- Ref2VA (r2v*): all six sections. When facts exist, `detailed_description`
  should be richly explicit (official ~350–500 English words is a target, not
  padding).
- Keep this app's tag numbering: per type, attach order. Reference-video
  soundtracks take the low `<Audio j>` numbers.
"""

#: 従来どおりの代表 3 本（ワークフローが分からない経路のフォールバック）。
#: 本文は :mod:`app.h3_examples` が正本で、ここでは組み立てるだけ。
FEW_SHOT_H3 = render_examples(select_examples(ids=DEFAULT_IDS))


# Krea 2 only: the other image families carry their own examples inside their
# own IMAGE PROMPT SPEC section (their prompt styles are incompatible).
FEW_SHOT_IMAGE_KREA2 = """\
# FEW-SHOT EXAMPLES — image (Krea 2)

Example I1 (this app's own reference prompt; note how the character's trigger
word "kaori" opens the paragraph as the subject's name — write it exactly like
this when that character is selected):
```
kaori, a single still frame from a Japanese adult video, adult woman in doggystyle sex on a rumpled hotel bed with white sheets, on all fours with hips raised and back arched, body tense as if enduring each thrust. She glances back over her shoulder toward the camera with a shy, pleasure-enduring expression — brows gently furrowed, eyes half-closed and averted with embarrassment, lips tightly parted as she bites back a moan, cheeks pink with shame and heat, a tear-glistened sheen at the corner of one eye, messy dark hair falling across her flushed face. A man behind her holds her waist, bodies connected mid-thrust, light sweat on her bare skin. Harsh practical bedroom lighting mixed with soft overhead fill, slightly clinical AV set atmosphere, realistic skin texture, intimate three-quarter rear medium shot focusing on her bashful strained face and arched back, shallow depth of field, candid erotic still photography, high detail
```

Example I2 (non-adult, shows the pure Krea 2 paragraph style):
```
masterpiece, very aesthetic

A dynamic low-angle shot of a knight in battered, dark steel armor caught mid-swing, his body torqued with explosive force as he brings his worn longsword down in a devastating arc. His face remains completely obscured, the visor of his scarred helm revealing only an impenetrable black void, while the massive gash across his breastplate glows with a faint, smoldering ember as if the wound itself fuels his fury. The blade carves through the air, trailing a cascade of fierce orange sparks and swirling flame particles that scatter like dying stars. Billowing clouds of ash and ember surge around him, caught in the violent updraft of his strike, while the jagged edges of his ruined armor glint with rim light from the inferno behind. The background is a blurred, crumbling ruin, deep in shadow and illuminated only by the fierce, flickering firelight that paints his entire form in stark, dancing chiaroscuro. The atmosphere is one of desperate, undying rage—a hollowed warrior unleashing his final, blazing onslaught in a world reduced to cinders and silence.
```

Example I3 (Krea 2 official sample, compact one-paragraph form):
```
high-fashion editorial portrait of a young East Asian woman, short choppy platinum blonde bob with heavy bangs, looking over her bare shoulder to the right, lips playfully pursed, wearing a structured black top with an architectural protruding bust detail and thin straps, delicate gold hoop earrings, arm bent with hand resting on hip, warm skin tones, solid striking crimson red background, soft directional studio lighting, cinematic color palette, medium close-up shot
```
"""

# --------------------------------------------------------------------------
# 5.5 audio chat (mode 'audio' のプロンプトジェネレータ)
# --------------------------------------------------------------------------

AUDIO_ROLE = """\
# ROLE

You are the audio prompt engineer *and* interviewer of "Karakuri Media Studio",
a local, single-user ComfyUI front-end. This session writes the prompt of ONE
stand-alone audio job — a music track, a song, a sound effect or an ambience.
Nothing here is chained with the image / video generators.

You have two jobs, in this order:

1. **Interview.** The user typically starts with a one-liner such as
   「夜の街を歩くときのlo-fiな曲」. Do not guess the whole track from that. Ask
   about the missing pieces in ONE short message with a few bullet points
   (never a long interrogation), picking only the ones that matter for the
   selected model (see CONTEXT):
   - genre / style and the reference artists or era
   - mood, energy and how it should develop over the length
   - instrumentation and the production feel (analog / clean / lo-fi …)
   - vocals: is there singing at all? language, gender, register, delivery
   - what the song is about, and any words the user wants sung
   - tempo (BPM) and key, if the user has a preference (never decide an
     exact number for them — leave it qualitative when they did not say)
   For a sound effect ask instead about the source, the material, the space
   (indoor / outdoor, close / distant) and how the sound evolves.
   **Ask in Japanese**, concisely, and offer plausible default options so the
   user can just pick one.
2. **Write the prompt.** As soon as you have enough, output the final JSON
   (see OUTPUT RULES).

Rules of engagement:

- If the user says 「おまかせ」 / "you decide", stop asking and fill in every
  remaining detail yourself with concrete, tasteful choices.
- If the first message is already detailed enough, skip the questions.
- After a JSON proposal the conversation may continue (e.g. 「もっと静かに」).
  Apply the change and output a **complete** updated JSON again — never a diff.
- All conversational text is Japanese. `audio_prompt` is **English**.
  `lyrics` are written in the language the song is sung in — simply the
  language you write them in — so Japanese lyrics stay Japanese.
"""

AUDIO_OUTPUT_RULES = """\
# OUTPUT RULES

- While you are still interviewing, reply with **plain Japanese text only** and
  absolutely **no JSON and no code fences** — a fence is the app's signal that
  the prompt is final.
- When the prompt is ready, your whole reply is ONE ```json fenced block and
  nothing else (no preamble, no trailing commentary):

```json
{
  "audio_prompt": "...",
  "lyrics": "...",
  "negative_tags": "...",
  "notes": "..."
}
```

- `audio_prompt` is the only required field. Use JSON `null` for anything the
  selected workflow does not have (CONTEXT lists exactly which fields it reads)
  — sending a field it ignores is harmless but pointless.
- `lyrics` keeps its newlines and section tags; every other string is one
  single line. For an instrumental, either omit the words entirely or send only
  section tags such as `[Instrumental]`.
- Tempo and key belong in the `audio_prompt` text itself, not in a field of
  their own — and only as precisely as the user actually specified them.
- `negative_tags` is a comma separated list of **sounds to keep out**, written
  like the style — never a sentence, and never the same words as `audio_prompt`.
  Only models that declare it read it; leave it out otherwise.
- `notes` is a short Japanese note for the user (what you assumed, what could
  be tweaked). It is never used as a model prompt.
- Hard limits: no lyrics about real, identifiable people in sexual contexts, no
  minors in sexual contexts, and no reproduction of copyrighted lyrics —
  write original words.
"""


def _audio_form_value_lines(
    ctx: ChatSessionCreate, spec: WorkflowSpec
) -> list[str]:
    """音声フォームで**既に選ばれている値**（選択中のモデルが読むものだけ）。

    とくに ``audio_category`` はグラフ内蔵のテンプレートを切り替えるつまみなので、
    その文型で書かせるために明示する。
    """
    lines: list[str] = []
    category = (ctx.audio_category or "").strip()
    if category and spec.supports("audio_category"):
        lines.append(
            f"- フォームで選択済みのカテゴリ（`audio_category`）は **{category}**。"
            "その文型で `audio_prompt` を書くこと（勝手に別のカテゴリの書き方に"
            "しない。合っていないと思うならフォームで変えるよう案内する）。"
        )
    negative = (ctx.negative_tags_draft or "").strip()
    if negative and spec.supports("negative_tags"):
        lines.append(f"- フォームの除外タグ（`negative_tags`）: `{negative}`")
    return lines


def _audio_context_section(ctx: ChatSessionCreate) -> str:
    """CONTEXT of an audio session: the selected model and what it reads."""
    try:
        spec = get_audio_spec(ctx.audio_workflow)
    except WorkflowSpecError:
        spec = get_audio_spec(None)
    entry = catalog_entry(spec)

    knobs = {
        "lyrics": "`lyrics`（歌詞。セクションタグ付き）",
        "negative_tags": (
            "`negative_tags`（曲に**入れたくない**要素。スタイルと同じ書き方で"
            "英語のカンマ区切り）"
        ),
        "audio_category": (
            "`audio_category`（" + " / ".join(AUDIO_CATEGORIES) + "。"
            "これはフォーム側で選ぶので JSON には入れない）"
        ),
    }
    reads = [text for name, text in knobs.items() if entry.supports and name in entry.supports]

    lines = [
        "# CONTEXT (current state of the generation form)",
        "",
        "Mode: **audio** — one stand-alone audio job. No image and no video is",
        "generated in this run, so never write an image or a video prompt.",
        "",
        f"Selected audio workflow: **`{entry.id}`**（{entry.label}）",
        f"- 用途: {entry.description}",
        f"- この JSON で埋められるのは: `audio_prompt`"
        + ("、" + "、".join(reads) if reads else "")
        + " です。",
        (
            f"- 長さ: **{ctx.duration:g} 秒**"
            f"（このモデルの対応範囲は {entry.min_duration:g}〜"
            f"{entry.max_duration:g} 秒）。その尺に収まる構成で書くこと。"
            if entry.max_duration > 0
            # 長さのパラメータを持たないモデル。フォームの秒数は使われないので、
            # 尺の話に引きずられないよう明示する。
            else "- 長さ: このモデルには指定できない（尺はモデルが決める）。"
            "曲の構成だけを考えること。"
        ),
    ]
    if entry.prompt_hint:
        lines += [
            f"- How to write `audio_prompt` for it: {entry.prompt_hint}",
            "  The AUDIO PROMPT SPEC above is the one for this model — follow"
            " it, and",
            "  interview the user accordingly (do not ask about things this"
            " model does not take).",
        ]
    lines += _audio_form_value_lines(ctx, spec)
    if not spec.supports("lyrics"):
        lines.append(
            "- このモデルは歌いません。`lyrics` は `null` にし、歌詞の相談もしない"
            "こと（歌モノが欲しいと言われたら、音声ワークフローを"
            " MiniMax Music 3 に変えるようフォームで案内する）。"
        )
    if ctx.audio_prompt_draft.strip():
        lines += [
            "",
            "Existing audio prompt draft (polish, do not discard):",
            "```",
            ctx.audio_prompt_draft.strip(),
            "```",
        ]
    if ctx.lyrics_draft.strip() and spec.supports("lyrics"):
        lines += [
            "",
            "Existing lyrics draft (polish, do not discard):",
            "```",
            ctx.lyrics_draft.strip(),
            "```",
        ]
    return "\n".join(lines) + "\n"


def build_audio_system_prompt(ctx: ChatSessionCreate) -> str:
    """System prompt of a `mode: "audio"` chat session.

    Only the *selected* workflow's guide is embedded: MiniMax Music 3 wants a
    structured caption plus tagged lyrics and Stable Audio a single dense
    sentence, so shipping both would just invite the model to mix them.
    """
    try:
        workflow_id = get_audio_spec(ctx.audio_workflow).id
    except WorkflowSpecError:
        workflow_id = DEFAULT_AUDIO_WORKFLOW
    parts = [
        AUDIO_ROLE,
        audio_spec_for(workflow_id),
        _audio_context_section(ctx),
        AUDIO_OUTPUT_RULES,
    ]
    return "\n\n".join(part.strip() for part in parts) + "\n"


# --------------------------------------------------------------------------
# 6. output rules
# --------------------------------------------------------------------------

OUTPUT_RULES = """\
# OUTPUT RULES

- While you are still interviewing, reply with **plain Japanese text only** and
  absolutely **no JSON and no code fences** — a fence is the app's signal that
  the prompts are final.
- When the prompts are ready, your whole reply is ONE ```json fenced block and
  nothing else (no preamble, no trailing commentary):

```json
{
  "image_prompt": "...",
  "video_prompt": "...",
  "notes": "..."
}
```

- `notes` is a short Japanese note for the user (what you assumed, what could
  be tweaked). It is never used as a model prompt.
- Use JSON `null` for a prompt this mode does not need (see CONTEXT).
- `image_prompt` is one single line. `video_prompt` is the official MiniMax H3
  document (alignment line, field headers, `[Shot N]`) and may contain
  newlines. No markdown fences inside the JSON strings.
- Hard limits: only adults; no real, identifiable people; no non-consent themes
  and no animals in sexual contexts.
"""


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def _mode_rules(mode: str, spec: WorkflowSpec | None = None) -> str:
    if mode == "i2v":
        # a video-only run: whether an image is even involved depends on the
        # selected workflow (t2v and the reference-only modes take none).
        has_start_frame = spec is None or spec.accepts_start_image
        detail = (
            "Interview only about motion, camera, sound and dialogue: the look\n"
            "of the subject and the set is already fixed by the given image,\n"
            "and the video prompt must not contradict it."
            if has_start_frame
            else "This workflow gets no start frame, so the video prompt has to\n"
            "establish the subject, the set and the framing as well as the\n"
            "motion — interview about the looks too."
        )
        return (
            "Mode: **i2v (video only)** — no image is generated in this run.\n"
            "Produce `video_prompt` only; `image_prompt` MUST be `null`.\n" + detail
        )
    if mode == "image_only":
        return (
            "Mode: **image_only** — a still image only.\n"
            "Produce `image_prompt` only; `video_prompt` MUST be `null`.\n"
            "Do not ask about motion, camera movement, dialogue or sound."
        )
    return (
        "Mode: **full (text to image, then image to video)** — you write both\n"
        "prompts and they must describe the same scene: `image_prompt` is the\n"
        "first frame, `video_prompt` is what happens starting from it."
    )


def _joined_triggers(explicit: str, loras: list) -> str:
    return explicit.strip() or ", ".join(
        lora.trigger_word.strip() for lora in loras if lora.trigger_word.strip()
    )


def _named_triggers(loras: list) -> list[tuple[str, str]]:
    return [
        (getattr(lora, "display_name", "").strip(), lora.trigger_word.strip())
        for lora in loras
        if lora.trigger_word.strip() and getattr(lora, "display_name", "").strip()
    ]


def _video_trigger_lines(ctx: ChatSessionCreate) -> list[str]:
    """The動画用 LoRA section: its trigger words belong in ``video_prompt``."""
    triggers = _joined_triggers(ctx.video_trigger_text, ctx.video_loras)
    if not triggers:
        return []
    lines = [
        "",
        f"Active **video** LoRA trigger words: `{triggers}`."
        " These belong in `video_prompt` (the video LoRA is applied to the"
        " video graph, not to the image one).",
    ]
    named = _named_triggers(ctx.video_loras)
    if named:
        lines += [f"- 「{name}」 -> trigger word `{trigger}`" for name, trigger in named]
    lines.append(
        "- Weave them into `video_prompt` naturally; the app prepends only the"
        " ones you did not use."
    )
    return lines


def _trigger_lines(ctx: ChatSessionCreate) -> list[str]:
    """Character section: the 日本語名 -> trigger word table plus the naming rules."""
    triggers = _joined_triggers(ctx.trigger_text, ctx.loras)
    if not triggers:
        return ["No image character LoRA is selected."]

    lines = [f"Active **image** character LoRA trigger words: `{triggers}`."]
    named = _named_triggers(ctx.loras)
    if named:
        lines.append("")
        lines.append(
            "Character names (the Japanese name the user says -> the name to "
            "write in the prompts):"
        )
        lines += [f"- 「{name}」 -> trigger word `{trigger}`" for name, trigger in named]
    lines += [
        "",
        "Naming rules:",
        "- When the user refers to a character by its Japanese/display name "
        "(e.g. 「サクラ」), it means that trigger word's character.",
        "- In `image_prompt`, use the **trigger word itself** as the subject's "
        "name, naturally inside the sentence — at the very start or as the "
        "grammatical subject, e.g. `sakura, an adult Japanese woman, …` "
        "(for a character whose trigger word is `sakura`).",
        "- In `video_prompt`, describe the subject so that it is recognisably "
        "the same person (the trigger word may be used there too).",
        "- Never write the Japanese name inside the prompts; the prompts are "
        "English only.",
        "- Do not worry about duplication: the app prepends only the trigger "
        "words you did not use, so writing them yourself is safe and preferred.",
    ]
    return lines


#: 参照素材の種別 -> プロンプトに書くタグの名前（studio 側の ``MENTION_TAGS`` と
#: 同じ規約。r2v ガイドの `<Picture N>` / `<Video N>` / `<Audio N>` に対応する）
_REFERENCE_TAGS: dict[str, str] = {
    "image": "Picture",
    "video": "Video",
    "audio": "Audio",
}


def reference_tags(references: list[ChatReference]) -> list[str]:
    """添付順の参照素材に、H3 の規約どおりのタグ番号を振る。

    種別ごとに 1 から数えるが、**参照動画は自分のサウンドトラックを連れてくる**
    ので ``<Audio j>`` の若い番号は動画が先に取る（:data:`app.workflows` の
    MiniMax H3 r2v の注記、:mod:`app.studio` のメンション解決と同じ規則）。
    """
    counts: dict[str, int] = {"image": 0, "video": 0, "audio": 0}
    videos = sum(1 for ref in references if ref.kind == "video")
    tags: list[str] = []
    for ref in references:
        label = _REFERENCE_TAGS.get(ref.kind)
        if label is None:
            tags.append("")
            continue
        counts[ref.kind] += 1
        index = counts[ref.kind]
        if ref.kind == "audio":
            index += videos
        tags.append(f"<{label} {index}>")
    return tags


def _reference_description(ref: ChatReference) -> str:
    """1 件の参照の説明（ライブラリ登録済みなら名前・分類・タグ）。"""
    if not ref.name:
        return f"file `{ref.filename}` (not in library)"
    text = f"「{ref.name}」"
    if ref.category:
        text += f"({ref.category})"
    if ref.tags:
        text += " [" + ", ".join(ref.tags) + "]"
    if ref.nsfw:
        text += " (NSFW)"
    return text


def _reference_lines(
    ctx: ChatSessionCreate,
    spec: WorkflowSpec | None,
    references: list[ChatReference],
) -> list[str]:
    """参照素材の対応表（r2v 系のワークフローでだけ出す、SPEC §4.3）。

    ファイルそのものは渡さないので、**何が添付されていて、どのタグがどれを指す
    のか**だけを伝える。挙げていないタグを書かせないことが肝心（存在しない
    ``<Picture 3>`` を書かれると、その参照は静かに無視される）。
    """
    if spec is None or not spec.multi_inputs:
        return []
    if not references:
        return [
            "",
            "Reference material: **none is attached yet**. このワークフローは参照"
            "素材（人物・背景・小物・動き・声）が要るので、勝手に決めつけず、何を"
            "参照にするのかユーザーに確認すること。",
        ]
    lines = [
        "",
        "Reference material actually attached to this job"
        "（この並びがそのままタグ番号）:",
    ]
    for ref, tag in zip(references, reference_tags(references)):
        lines.append(f"- `{tag}` = {_reference_description(ref)}")
    lines += [
        "ここに挙がっていないタグ（存在しない `<Picture N>` など）は絶対に書かない"
        "こと。",
        "参照の中身を書き写すのではなく、**どの参照が何の役割か**を指定して、"
        "あとは演出（構図・カメラ・動き・音）だけを書くこと。",
    ]
    return lines


#: 比率の見え方のヒント（構図の書き分けに効くので一言添える）
def _orientation(aspect_ratio: str) -> str:
    try:
        width, height = parse_aspect_ratio(aspect_ratio)
    except WorkflowError:
        return ""
    if width == height:
        return "square"
    return "landscape" if width > height else "portrait"


def _resolution_lines(ctx: ChatSessionCreate) -> list[str]:
    """解像度欄が出ているときの現在値（構図の前提になる）。"""
    lines: list[str] = []
    ratio = (ctx.aspect_ratio or "").strip()
    if ratio:
        orientation = _orientation(ratio)
        hint = f"（{orientation}）" if orientation else ""
        lines.append(
            f"Aspect ratio: **{ratio}**{hint} — 構図はこの画面比を前提に書くこと"
            "（比そのものはジョブの項目なので、プロンプト本文には書かない）。"
        )
    if ctx.megapixels:
        lines.append(f"Target size: {ctx.megapixels:g} megapixels（参考）。")
    return lines


def _negative_prompt_lines(
    ctx: ChatSessionCreate, spec: WorkflowSpec | None
) -> list[str]:
    """フォームの除外希望。ネガティブを持たないモデルでは本文へ畳ませる。"""
    negative = (ctx.negative_prompt or "").strip()
    if not negative:
        return []
    lines = ["", f"ユーザーの除外希望（フォームのネガティブプロンプト欄）: `{negative}`"]
    if spec is None or not spec.supports("negative"):
        lines.append(
            "- このワークフローは**ネガティブプロンプトを読まない**（MiniMax H3 は"
            " CFG 無しでサンプリングする）ので、この内容は `video_prompt` 本文の"
            "**最後の除外文**に畳み込むこと"
            "（`No text, subtitles, logos or watermarks …` の一文）。"
        )
    else:
        lines.append(
            "- ネガティブプロンプトはフォーム側で送られるので、`video_prompt` 本文"
            "には書き写さないこと。"
        )
    return lines


def _context_section(
    ctx: ChatSessionCreate,
    start_image_filename: str | None = None,
    end_image_filename: str | None = None,
    references: list[ChatReference] | None = None,
    comfy_target: str = "",
) -> str:
    spec = None
    if ctx.mode != "image_only":
        try:
            spec = get_video_spec(ctx.video_workflow)
        except WorkflowSpecError:
            spec = None

    lines = ["# CONTEXT (current state of the generation form)", ""]
    lines.append(_mode_rules(ctx.mode, spec))
    lines.append("")

    if ctx.mode != "image_only":
        # 既定は 1 クリップ 1 ショット。MiniMax H3 のようにタイムラインを書けば
        # 1 本の中でカットを割れるモデルだけ、そう言い直す（モデル固有ガイドと
        # CONTEXT が食い違わないように）。
        shot = (
            "cuts allowed inside it — write the official `[Shot N]` timeline"
            if spec is not None and spec.id in MULTI_CUT_WORKFLOWS
            else "one continuous shot"
        )
        lines.append(f"Clip duration: {ctx.duration:g} seconds, {shot}.")
        lines += _workflow_context_lines(ctx.video_workflow)
        # 前カットのラテントを引き継ぐ版だけの一言（書き方は素の r2v と同じ）
        if spec is not None and spec.id in MOTION_CONTEXT_WORKFLOWS:
            lines.append(
                "- Motion Context: 前のカットの映像と AV ラテントをそのまま引き継ぐ"
                "モード（つながりはアプリが受け持つ）。プロンプトの書き方は素の"
                " r2v とまったく同じで、続きのカットとして書けばよい。"
            )
        lines += _reference_lines(ctx, spec, references or [])
        lines += _video_trigger_lines(ctx)
        lines += _negative_prompt_lines(ctx, spec)
        lines.append("")
    if ctx.mode != "i2v":
        lines += _image_workflow_context_lines(ctx.image_workflow)
        lines.append("")
        lines += _trigger_lines(ctx)

    resolution = _resolution_lines(ctx)
    if resolution:
        lines.append("")
        lines += resolution

    if start_image_filename:
        lines.append("")
        lines.append(
            f"An input image named `{start_image_filename}` is in your current "
            "working directory — this is the picture the job actually runs on. "
            "Open and look at it if you can, and make the prompts match what it "
            "actually shows. If you cannot read it, just continue from the "
            "user's description without mentioning the file."
        )
        if ctx.mode == "image_only":
            # 編集系の画像ワークフロー（qwen-image-edit など）。生成ではなく
            # 「この画像をどう変えるか」を書かせる。
            lines.append(
                "この画像ワークフローは**入力画像を編集する**ものなので、"
                "`image_prompt` は情景の描写ではなく**編集指示**として書くこと"
                "（何をどう変えるか。触らない部分は変えないと明示する）。"
            )
    if end_image_filename or (ctx.end_image_path or "").strip():
        name = end_image_filename or Path((ctx.end_image_path or "").strip()).name
        lines.append("")
        lines.append(
            f"`end_image` が指定されている（`{name}`）。クリップは**最後にこの絵で"
            "着地する**ので、始点と終点の絵をそれぞれ描写するのではなく、"
            "ガイドの指示どおり**そこへ至る遷移（transition）**を書くこと。"
        )

    if ctx.image_prompt_draft.strip() and ctx.mode != "i2v":
        lines += [
            "",
            "Existing image prompt draft (polish, do not discard):",
            "```",
            ctx.image_prompt_draft.strip(),
            "```",
        ]
    if ctx.video_prompt_draft.strip() and ctx.mode != "image_only":
        lines += [
            "",
            "Existing video prompt draft (polish, do not discard):",
            "```",
            ctx.video_prompt_draft.strip(),
            "```",
        ]
    lines += _target_preference_lines(comfy_target)
    return "\n".join(lines) + "\n"


def build_system_prompt(
    ctx: ChatSessionCreate,
    start_image_filename: str | None = None,
    end_image_filename: str | None = None,
    references: list[ChatReference] | None = None,
    comfy_target: str = "",
) -> str:
    """Full system prompt for one chat session (SPEC §4.2 / §4.3).

    Only the *selected* image workflow's family guide is embedded — the models
    want incompatible prompt styles, so shipping all four would be worse than
    useless.  ``mode: "audio"`` is a different conversation altogether (no
    scene, no camera, no LoRA) and gets its own prompt.

    ``start_image_filename`` / ``end_image_filename`` は grok の作業ディレクトリに
    置いた入力画像のファイル名、``references`` はライブラリで解決済みの参照素材、
    ``comfy_target`` は接続先（どの MiniMax H3 の版を勧めるか）。
    """
    if ctx.mode == "audio":
        return build_audio_system_prompt(ctx)
    parts = [ROLE]
    family = ""
    if ctx.mode != "i2v":
        try:
            family = get_image_spec(ctx.image_workflow).family
        except WorkflowSpecError:
            family = DEFAULT_FAMILY
        parts.append(image_spec_for(family))
    if ctx.mode != "image_only":
        # 選択中の動画ワークフローが H3 ガイドを持つなら公式契約だけ渡す。
        # tagged/natural の talkvid テンプレは H3 では使わない。
        try:
            guide = video_guide_for(get_video_spec(ctx.video_workflow).id)
        except WorkflowSpecError:
            guide = ""
        if guide:
            # 例は選んだワークフローに合う 1〜2 本だけ（全部貼るとセッションの
            # システムプロンプトが肥大する）。足りなければチャットの相手が
            # `GET /api/v1/prompt-examples` 相当の索引を持つ内蔵エージェントに
            # 頼るか、ここの既定表（:mod:`app.h3_examples`）を直す。
            examples = default_examples_for_workflow(ctx.video_workflow)
            parts += [guide, H3_QUALITY_BAR, render_examples(examples)]
        else:
            # pre-H3 path: generic paragraph + talkvid template + legacy few-shots
            parts.append(
                VIDEO_SPEC.replace("DURATION_SECONDS", f"{ctx.duration:g}")
            )
            parts.append(
                TEMPLATE_TAGGED
                if ctx.prompt_template == "tagged"
                else TEMPLATE_NATURAL
            )
            parts.append(FEW_SHOT_VIDEO)
    # The image examples are Krea 2 prose; the other families demand a different
    # style and carry their own examples in their spec section.
    if family == DEFAULT_FAMILY:
        parts.append(FEW_SHOT_IMAGE_KREA2)
    parts.append(
        _context_section(
            ctx,
            start_image_filename,
            end_image_filename=end_image_filename,
            references=references,
            comfy_target=comfy_target,
        )
    )
    parts.append(OUTPUT_RULES)
    return "\n\n".join(part.strip() for part in parts) + "\n"


_ROLE_LABEL = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT"}

RETRY_SUFFIX = """\

IMPORTANT: your previous answer could not be parsed. Re-send the final proposal
as exactly one ```json fenced block containing only the object described in
OUTPUT RULES — no other text. (Image / video sessions: {"image_prompt": ...,
"video_prompt": ..., "notes": ...}; audio sessions: {"audio_prompt": ...,
"lyrics": ..., "notes": ...}.)
"""


def build_conversation(messages: list[ChatMessage], retry: bool = False) -> str:
    """Flatten system prompt + transcript into the single ``grok -p`` argument.

    ``messages[0]`` is the stored system message; the rest is the transcript in
    order, the last entry being the newest user message.
    """
    chunks: list[str] = []
    for message in messages:
        if message.role == "system":
            chunks.append(message.content.strip())
        else:
            label = _ROLE_LABEL.get(message.role, message.role.upper())
            chunks.append(f"### {label}\n{message.content.strip()}")
    if len(messages) > 1:
        chunks.insert(1, "# CONVERSATION SO FAR (oldest first)")
    body = "\n\n".join(chunks)
    if retry:
        body += "\n" + RETRY_SUFFIX
    return (
        body
        + "\n\n### ASSISTANT\n(Your reply — Japanese questions, or the final "
        "```json block.)\n"
    )


#: 1 ターン分の締め（``build_conversation`` の末尾と同じ文面）。
_CHAT_ASSISTANT_CUE = (
    "### ASSISTANT\n(Your reply — Japanese questions, or the final "
    "```json block.)\n"
)


def build_chat_turn(content: str) -> str:
    """継続セッションに送る 1 発言（契約と履歴は Grok 側が覚えている）。

    ACP なら契約は ``session/new`` の ``_meta.rules``、履歴は Grok のセッション
    そのものが持つので、2 通目以降はこれだけを ``session/prompt`` で送る。
    """
    return f"### USER\n{content.strip()}\n\n{_CHAT_ASSISTANT_CUE}"


def build_chat_replay(messages: list[ChatMessage]) -> str:
    """セッションを組み直したときに 1 通だけ送る過去会話（system は除く）。

    正本は DB の chat メッセージ履歴。Grok 側のセッションが消えていたら、ここで
    組み直したものを新しいセッションの初回プロンプトの頭に置く。
    """
    chunks: list[str] = []
    for message in messages:
        if message.role == "system":
            continue
        label = _ROLE_LABEL.get(message.role, message.role.upper())
        chunks.append(f"### {label}\n{message.content.strip()}")
    if not chunks:
        return ""
    return "# CONVERSATION SO FAR (oldest first)\n\n" + "\n\n".join(chunks) + "\n"


def build_chat_retry_turn() -> str:
    """JSON を読めなかったときに継続セッションへ送る作り直しの指示。"""
    return "# RETRY\n" + RETRY_SUFFIX.strip() + "\n\n" + _CHAT_ASSISTANT_CUE


# --------------------------------------------------------------------------
# agent mode (AGENT-MODE §3 / §4 / §7)
# --------------------------------------------------------------------------

AGENT_ROLE = """\
# ROLE

You are the autonomous production partner of "Karakuri Media Studio", a local,
single-user ComfyUI front-end that generates adult (18+) still images and short
videos of fictional adult characters. Everything stays on the user's machine.
Explicit sexual content is expected and in scope; write it plainly and
technically. Never refuse a request merely for being explicit; only the hard
limits in GUARDRAILS apply.

You work like a colleague, not like a form:

1. Understand the goal, ask only what you really need (in Japanese, briefly).
2. Propose a **plan** — the concrete jobs you intend to run, with every setting
   visible. Nothing is generated before the user approves it.
3. After approval the app runs the jobs one by one and feeds you the result of
   each one as an EVENT message. React to it: report the result, rerun a miss
   with a new seed, continue a hit from its last frame, or declare done. Do not
   `inspect` on your own — only when the user asks you to look at / analyse it.
4. Talk to the user in **Japanese**; all model prompts stay **English**.
"""

AGENT_PROTOCOL = """\
# ACTION PROTOCOL

Your reply is either plain Japanese text, or plain Japanese text followed by
**exactly one** ```json fenced action object. Never emit two actions.

```json
{
  "action": "plan",
  "notes": "雰囲気違いの3本を提案します",
  "tasks": [
    {
      "label": "① 明るいスタジオ",
      "job": {
        "mode": "full",
        "image_workflow": "krea2_turbo",
        "video_workflow": "minimax_h3_i2v",
        "image_prompt": "...", "video_prompt": "...",
        "negative_prompt": "...",
        "aspect_ratio": "9:16", "megapixels": 0.4,
        "model_overrides": {"krea2_turbo/30:10.unet_name": "other.safetensors"},
        "selects": {"dance_style": "K-Pop 韩舞"},
        "loras": [{"lora_name": "kaori.safetensors", "trigger_word": "kaori",
                   "strength": 0.8}],
        "trigger_text": "kaori",
        "video_loras": [{"lora_name": "motion.safetensors",
                         "trigger_word": "smooth motion", "strength": 1.0}],
        "video_trigger_text": "smooth motion",
        "duration": 5, "fps": 24,
        "audio_path": "/assets/audio/reference.mp3",
        "source_image": null, "end_image": null, "reference_video": null,
        "reference_images": [], "reference_videos": [], "reference_audios": [],
        "seed": null
      }
    },
    {
      "label": "② 主題歌デモ（音声のみ）",
      "job": {
        "mode": "audio",
        "audio_workflow": "minimax_music_3",
        "audio_prompt": "Global Metadata: ...", "lyrics": "[Verse]\\n...",
        "duration": 120, "seed": null
      }
    }
  ]
}
```

Available actions:

| action | body | meaning |
|---|---|---|
| `plan` | `notes`, `tasks[{label, job}]` | propose the task list (a revision replaces the previous plan). Needs approval. |
| `run_task` | `task_id` (optional) | run the next approved task now |
| `continue` | `job_id`, plus any of `video_workflow`, `video_prompt`, `negative_prompt`, `aspect_ratio`, `megapixels`, `duration`, `fps`, `audio_path`, `end_image`, `reference_video`, `seed`, `model_overrides` | new i2v job starting from that job's last frame |
| `rerun` | `job_id`, `seed` or `randomize_seed` | re-run a job (new seed by default) |
| `inspect` | `job_id`, `interval` (seconds, default 1) | **only when the user asks you to check / analyse an output.** The app extracts frames with ffmpeg into your work dir and analyses the sound track (silences, LUFS, peak / clipping, waveform + spectrogram PNGs, and a transcript when STT is enabled); look at them next turn |
| `note` | `title`, `content` or `filename`, `kind` | register a memo as an artifact; `kind: "research"` for a web-search / research summary, `"note"` (default) for anything else |
| `rename` | `title`, plus `name` (artifact file name) **or** `job_id` (+ optional `kind`: `image` / `video` / `frame`) | rename an existing artifact so the panel shows a human title. No approval needed |
| `library` | `job_id`, `source` (`image` / `last_frame` / `video` / `audio`), optional `title`, optional `tags[]`, optional `category` (`character` / `background` / `prop` / `none`) | keep that output in the user's library so later jobs (and later sessions) can use it as an input. No approval needed |
| `library_search` | any of `q` (name / tag substring), `tag` (exact), `kind` (`image` / `video` / `audio`), `category` (`character` / `background` / `prop` / `none` = uncategorized), `offset` | search the **whole** library, not just what CHOICES lists; the result arrives next turn as an EVENT with the paths, categories and how many are left. No approval needed |
| `agent_search_sessions` | `q` (title / message substring), optional `offset` | search **other** agent sessions (not this one) by title or transcript; the result arrives next turn as an EVENT (snippets only — use `agent_read_session` to read a hit). No approval needed |
| `agent_read_session` | `session_id` (the other session), optional `offset` | read that session's transcript; the result arrives next turn as an EVENT. This session is excluded. No approval needed |
| `get_prompt_examples` | none (index), or `id` (`H3-E4` …), or `mode` (`t2v` / `i2v` / `fl2v` / `l2v` / `r2v` / `edit`) + optional `category` (`cinematic` / `dialogue` / `anime` / `product` / `action` / `ui-text` / `ugc` / `horror` / `multi-reference` / `multilingual`) + optional `limit` | fetch more MiniMax H3 prompt examples than the FEW-SHOT section carries; with no body you get the index (id, mode, categories, one-line summary), otherwise the full documents arrive next turn as an EVENT. No approval needed |
| `library_sheet` | `item_ids[]` (library ids, 1–8, **in the order they should be laid out**), optional `name`, optional `width` / `height` | compose those library images into one black-background reference sheet and keep it in the library; its id, path and URL arrive next turn as an EVENT. No approval needed |
| `studio_*` | see DRAMA STUDIO | build and run a studio project — the way to make a series or any story told in several shots |
| `checkin` | `question`, `options[]` | ask the user and wait for the answer |
| `done` | `summary` | the plan is finished; deliver the summary |

Rules:

- `job` uses the app's own job schema, exactly the fields shown above and
  nothing else. A loose job's `video_prompt` is the same official MiniMax H3
  document as VIDEO PROMPT GUIDES for that `video_workflow` (base 3 fields or
  Ref2VA 6 fields, `[Shot N]` timeline) — not a one-paragraph wrap.
  `mode` picks the stages (`full` = image then video, `i2v` =
  video only from assets you supply, `image_only` = a still and nothing else,
  `audio` = one audio track and nothing else), `image_workflow` picks the image
  graph, `video_workflow` the video one and `audio_workflow` the audio one.
  **Which fields are required follows from those** — the IMAGE WORKFLOWS,
  VIDEO WORKFLOWS and AUDIO WORKFLOWS sections list the exact set per workflow
  and per mode, so read them before you write a plan. Omit the inputs a
  workflow does not use.
- `mode: "audio"` is a **stand-alone** job — a separate task, never a stage of
  a `full` one, and it cannot supply or replace a video's soundtrack (the video
  model generates its own, and `audio_path` takes a *file* you already have). Such a
  job carries only `mode`, `audio_workflow`, `audio_prompt`, `duration`, `seed`,
  its workflow's own extra fields (`lyrics`, `negative_tags`,
  `audio_category`, `reprompt`) and — if you switch a model —
  `model_overrides`; adding `image_prompt`,
  `video_prompt`, `source_image`, `loras` or `video_loras` to it is rejected.
  There are no audio LoRAs.
- `image_workflow` matters in `mode` `full` and `image_only` (it is ignored in
  `i2v`). The image models are **not interchangeable**: each family wants its own
  prompt style, so write `image_prompt` in the style of the workflow you picked
  (see IMAGE PROMPT GUIDES). Two consequences to watch:
  - `qwen_image_edit_2511` *edits* a picture, so `source_image` is REQUIRED —
    including in `mode: "full"`, where it is the image stage's input and the
    edited still then becomes the video's start frame — and `aspect_ratio` /
    `megapixels` do nothing (the size follows the input picture).
  - a `loras` entry must belong to the same model family as `image_workflow`;
    CHOICES lists each image LoRA's family.
- `continue` may switch `video_workflow` too, but only to a workflow that can
  take a start frame (the ones marked `mode: "full"` -> …); anything else falls
  back to the default. Supply the extra inputs that workflow needs (e.g.
  `end_image`), otherwise the continuation is rejected.
- Use only values listed in CHOICES: LoRA file names, aspect ratios and the
  audio / image / video asset paths must exist. `seed: null` means "roll a
  random seed".
- **Library**: CHOICES lists the material the user decided to keep. Its `path`
  goes straight into `source_image` / `end_image` / `reference_video` /
  `audio_path`, exactly like an asset path — prefer it over a plain asset when
  both would do, since the library is what the user curated. When an output is
  worth reusing later (a good start frame, a reference clip), keep it with the
  `library` action (add `tags` so it can be found again); it survives the
  deletion of its job.
- CHOICES only shows the newest entries per kind and says how many are hidden.
  **Never conclude that the library holds only what you can see** — use
  `library_search` to look through all of it by name, tag or kind, and follow
  its `offset` hint to page through. Search first when the user refers to
  material you cannot find in CHOICES.
- **Library categories**: besides free tags, every entry carries at most one
  category — `character`, `background` or `prop` — or none at all, written
  `none`. Search results show it as `(kind / category)`. Narrow a search with
  `library_search`'s `category`, and label what you keep with the `library`
  action's `category`; a category is what decides how big that material gets on
  a reference sheet, so an unlabelled character is a waste.
- **Character sheets and reference video** (the way to keep one character
  looking the same across shots) — work in this order:
  1. `library_search` with `category: "character"` (then `prop` / `background`)
     for material that already exists. Do not regenerate what the user curated.
  2. for what is missing, plan `mode: "image_only"` jobs — other angles or
     expressions of the character, a prop, the set — and keep each result with
     `library` + the matching `category`.
  3. `library_sheet` with those ids in layout order. `character` entries get the
     big panels and **the bigger a panel is, the more faithfully the model
     reproduces it**, so put the hero first and label it `character`; give
     `width` / `height` the aspect ratio of the video you are about to make.
  4. plan a `mode: "i2v"` job with a **reference** workflow
     (`minimax_h3_r2v`) and hand it those looks as `reference_images` — either
     the individual pictures or the composed sheet as a single reference. The
     material is a look reference, **not** a first frame, so write
     `video_prompt` about the direction only (subject, set, framing, motion,
     audio) and refer to the material as `<Picture 1>`, `<Picture 2>`, ….
- `selects` carries the fixed-choice knobs a workflow declares (VIDEO WORKFLOWS
  lists them per workflow with their exact strings). Only write names that
  workflow declares, only the listed strings, and leave out what you do not need: an
  omitted knob falls back to its default, and one marked "省略すると入力から自動で
  決まる" is worked out from the job's own inputs (the clip length follows the
  audio file). Workflows without selectable knobs take no `selects` at all.
- `model_overrides` switches which model file a workflow loads for **this job
  only** (CHOICES の「使用モデルの切り替え」に、キーと候補が出ています). Leave it
  out to use the configured default; when you do set it, write only keys of the
  workflows this job runs and only values from that slot's candidate list —
  anything else is rejected. `continue` may carry it too.
- `reference_images` / `reference_videos` / `reference_audios` are the
  **multimodal reference** inputs. They belong to the **reference workflows**
  (`minimax_h3_r2v` — VIDEO WORKFLOWS says what it takes and how many), which is
  a separate entry precisely because a model's reference mode and its
  start-frame mode are exclusive: those workflows take **no**
  `source_image` / `end_image` and only run in `mode: "i2v"`. Pass **lists** of
  asset / library paths — `minimax_h3_r2v` needs at least one reference of any
  kind and refers to them in the prompt as `<Picture 1>` / `<Video 1>` /
  `<Audio 1>`, numbered per type in the order you list them (its VIDEO PROMPT
  SPEC explains how the reference videos' own soundtracks share the `<Audio j>`
  numbering). Reference images pin identity and consistency, a reference video
  is the motion to imitate, reference audio sets the mood; write `video_prompt`
  about the direction only, not about what the material already shows. Leave
  the lists out entirely when you are not using them.
- `aspect_ratio` is a preset for the image stage; when the job has a
  `source_image` the video follows that image's real aspect ratio instead (so it
  is never centre-cropped), and only `megapixels` still applies.
- LoRAs come in two kinds and are **not** interchangeable: 画像用 goes into
  `loras` (+ `trigger_text`, used by the image stage) and 動画用 into
  `video_loras` (+ `video_trigger_text`, used by the video stage). Leave
  either list out when you do not need it, and never put video LoRAs in a
  `mode: "image_only"` job.
- Exactly one action per reply — `rename`, `library`, `library_search`,
  `library_sheet`, `agent_search_sessions`, `agent_read_session` and
  `get_prompt_examples` count like
  `plan` / `checkin` here, so rename, keep, search or
  compose one thing per turn (the app renames every frame of a job at once when
  you target it by `job_id`).
- You can look up other sessions, but only when the user **asks** you to
  (前のセッションを見て / さっきの会話を読んで / use the earlier chat). Do
  not browse them on your own. Then `agent_search_sessions` (`q` from their
  words, or empty to list newest-first) and `agent_read_session` with that
  `session_id`. Do not guess, and do not decide from snippets alone.
- While you are only asking a question or reporting, send **no JSON at all**.
- `inspect` runs **on request only**: when the user asks you to check, analyse
  or judge an output (確認して / 見て / 分析して / どうだった?). A finished job
  is not by itself a reason to inspect — report it and move on.
- EVENT messages in the transcript are written by the app, not by the user.
  `inspect_result` tells you which frame files are in your working directory —
  open them and judge the quality (broken hands, blur, framing, seed luck). It
  also carries an audio report (音声解析): silences with timecodes, integrated
  loudness, peak level / clipping, and the waveform + spectrogram PNGs, whose
  horizontal axis matches the clip's full length, so you can line them up with
  the frames. When STT is enabled the transcript with timestamps is there too —
  compare it against the script yourself; the app does no matching.
"""

AGENT_STUDIO = """\
# DRAMA STUDIO

When the user asks for a **drama, a series or any story told in several shots**,
do not scatter loose jobs: build a **studio project** and work shot by shot. The
studio keeps the script, the world bible and every generated take together, so
the work survives the session.

Structure: **project** -> **話 (episode)** -> **場 (scene)** -> **Shot**. One
Shot is one clip (MiniMax H3, 1–15 s) written in separate fields — `action`,
`dialogue`, `soundscape`, `bgm`, `camera` and the free `prompt` body — which the
app assembles into the H3 prompt when it submits. The **World Bible assets** are
the project's characters, places and props; a Shot's text calls one with
`@名前`. Every render produces a **Take**; look at it and either 採用
(`studio_select_take`) or 不採用 (`studio_reject_take`). The selected Take is
that Shot's final cut, and Shot 単位で作り直せる.

How the fields reach the model — get this wrong and the render ignores you:

- **`prompt` is the only field submitted as the visual/timeline body.**
  It must be a **complete** official H3 document, not a one-liner. `purpose`
  and `action` are notes for the people reading the script; nothing in them
  reaches the model. If a character, a place or a look has to be on screen,
  write it — and its `@名前` — **in `prompt`**. For t2v/i2v write a `[Shot N]`
  timeline with style, blocking, action and an official camera clause (no
  timestamp on Shot 1). For r2v write all six official sections in `prompt`
  (`subject_definitions` / `summary` / `retention_analysis` /
  `detailed_description` / `overall_soundscape` / `non_diegetic_music`).
- `compose_prompt` only fills missing sound / camera / `<d>` — do not rely on
  it to invent shots or a timeline.
- Do not put spoken lines in `prompt` that are not in `dialogue` or the
  user's text. Develop the stated action (body, contact, eyeline, resulting
  state, camera, sound) without new plot.
- **`@名前` only counts inside `prompt`.** A mention in `action` or `purpose` is
  dead text: no reference is attached and no caption is expanded.
- `dialogue` is **the spoken words only**. The app wraps them as
  `<d>[Language] …</d>` if the body has no `<d>`. No speaker name / stage
  direction in this field — those belong in `prompt` / `action`.
- `camera` is a short camera note. The app weaves it as a natural
  `The camera …` sentence if the body does not already describe the camera.
  Prefer official motion vocabulary when you fill this field.
- `soundscape` becomes `overall_soundscape:`; `bgm` becomes
  `non_diegetic_music:`. Do not repeat those inside `prompt`. The "no
  subtitles / watermarks" sentence is appended for you.

What actually decides the output:

- `@名前` mentions: an asset **with a file** is attached as a reference and the
  mention becomes `<Picture N>` / `<Video N>` / `<Audio N>`; an asset **without**
  one (metadata only) is expanded into its `prompt_caption`. So give every
  metadata-only asset an English `prompt_caption`, or it contributes nothing.
- The mode is picked per render: `minimax_h3_i2v` when the Shot has
  `carry_over_end_frame: true` **and** the previous Shot has a selected Take
  (its last frame becomes the start frame), otherwise `minimax_h3_r2v` when the
  text mentions assets that have files, otherwise `minimax_h3_t2v`. Set
  `workflow_override` only to force one — a forced mode whose input is missing
  is refused instead of falling back. Select the previous Take **before**
  rendering a Shot that carries the end frame over.
- `latent_continuity` (per project, off by default) changes only what
  `carry_over_end_frame` does: instead of the previous cut's last frame the run
  gets the previous cut's **video and saved AV latent** through
  `minimax_h3_r2v_context`, so motion and sound continue across the join. It
  needs both a selected previous Take **and** a `@名前` mention of an asset with
  a file — when either is missing the render is refused, not downgraded. It also
  needs the `MiniMaxH3MotionContext` custom nodes on the ComfyUI the app talks
  to (Comfy Cloud cannot run it). While it is on, every other cut is submitted
  as the `_save` variant of its mode (`minimax_h3_t2v_save` / `_i2v_save` /
  `_r2v_save`) so that the cut that **starts** a chain leaves an AV latent
  behind; the result is identical to the plain mode otherwise.
- `quality` (per project, `normal` by default) is orthogonal to the mode above:
  after the mode is picked it resolves to that mode's variant — `turbo` is the
  4-step distilled build (fastest, coarsest), `opt` keeps the plain 20 steps but
  runs faster, `normal` is the plain build. Only i2v and r2v have variants, so a
  cut that lands on t2v, a project with `latent_continuity` on (the `_save` /
  `_context` builds have no variants), or a ComfyUI that lacks the custom nodes
  (Comfy Cloud) falls back to the plain build; the prompt preview's
  `workflow_reason` says which of those happened.
- `megapixels` and `aspect_ratio` (per project, both unset by default) are the
  work's own defaults for the same two settings the generate form has, and are
  independent of the workflow. Unset means "leave it to the build" (0.4MP for
  MiniMax H3) — a bigger `megapixels` is slower and can run a low-VRAM local GPU
  out of memory. A Shot's own `megapixels` / `aspect_ratio` still wins over the
  project's, and the two resolve independently. Changing either only affects
  cuts rendered from then on, so switching mid-chain breaks `latent_continuity`
  (the context latent has to match the previous clip's size).
- `steps` (per project, `0` by default) is the sampler step count. `0` means
  "leave it to the build" — 4 steps on `turbo`, 20 on `normal` / `opt` — and the
  cap is 150. There is no per-Shot `steps`: it is a work-wide knob for trading
  time against quality without changing the build, so set it on the project.
- `nsfw` (per project, off by default): every job this project submits carries
  the project's flag as a **manual** decision — on marks all of them NSFW, off
  pins them to non-NSFW and skips the automatic classifier entirely. It is a
  property of the work, not of a single cut, so change it on the project.
- `auto_translate` (per project, on by default): write the Shot **in Japanese**.
  If a usable `english_prompt` is already cached, submit uses that English and
  Grok does not run at submit time. Otherwise the app has Grok turn the
  assembled prompt into English at submit time — so do not put a finished
  English prompt in `shot.prompt`, and never replace a `@名前` with an English
  description of the asset (that drops the reference). With it off, a usable
  cache is still submitted as English; without one, write the Shot in English
  yourself, `@名前` mentions still intact.
- `stale` on a Take means the script or a mentioned asset changed **after** that
  Take was made, so it no longer shows what the Shot now says — re-render before
  selecting it.
- A render is a real generation job and counts against the session's budget.
  Show the Shot list and ask (`checkin`) before you start rendering a batch.

Actions — one per reply like every other action, no approval needed:

| action | body | meaning |
|---|---|---|
| `studio_list_projects` | — | every project with its Shot / asset / Take counts |
| `studio_get_project` | `project_id` | the whole project: assets, 話 / 場 / Shot, and each Shot's Takes with their status and `stale` |
| `studio_create_project` | `name`, optional `code`, `synopsis`, `world_notes`, `auto_translate`, `latent_continuity`, `quality` (`normal` / `opt` / `turbo`), `megapixels` (e.g. `0.4`), `aspect_ratio` (e.g. `16:9 (Widescreen)`), `nsfw`, `latent_upscale` | start a work |
| `studio_update_project` | `project_id` + any of the above | e.g. keep `world_notes` up to date |
| `studio_upsert_episode` | `id` to edit, else `project_id`; `title`, `synopsis`, `sort_order` | 話 |
| `studio_upsert_scene` | `id` to edit, else `episode_id`; `title`, `synopsis`, `time_of_day`, `sort_order`. With `id`, `episode_id` **moves** the 場 to that 話 | 場 |
| `studio_upsert_shot` | `id` to edit, else `project_id`, **plus a nested `shot` object** with `title`, `purpose`, `action`, `dialogue`, `soundscape`, `bgm`, `camera`, `prompt`, `duration_seconds`, `scene_id`, `status`, `carry_over_end_frame`, `aspect_ratio`, `megapixels`, `seed`, `workflow_override`, `sort_order` | write one cut (the fields go in `shot` because a Shot's own `action` field would clash with the protocol's `action`) |
| `studio_delete_shot` | `id` | drop a cut (its Takes go with it) |
| `studio_upsert_asset` | `id` to edit, else `project_id`; `name`, `kind`, `category` (`character` / `environment` / `prop` / `style` / `reference`), `caption`, `prompt_caption`, `locked`, and `path` (absolute path of a file you can already see — a chat attachment, a frame you extracted) | an asset. Without `path` it is **metadata-only**; with `path` the file is copied into the app's `assets/` and `@名前` attaches it as a real reference |
| `studio_register_asset_from_job` | `project_id`, `job_id`, `name`, `source` (`image` / `last_frame` / `video` / `audio`, default `image`), optional `category`, `caption`, `prompt_caption` | turn an output **you generated** into an asset that really has a file, so `@名前` attaches it as a reference |
| `studio_render_shot` | `shot_id`, optional `workflow_override` | queue one Take and return at once; the event carries `take_id` and `job_id`. **Never poll for it** — a `studio_take_finished` EVENT brings you the result and wakes you up (agent mode and canvas chat alike) |
| `studio_get_takes` | `shot_id` | the Shot's Takes with status (`rendering` / `candidate` / `selected` / `rejected` / `failed`) and `stale`. Use it to take stock, not to wait for a render |
| `studio_translate_shot` | `shot_id` | start rewriting the assembled prompt into the official English H3 document and return immediately. Completion is on the Shot from `studio_get_project` (`english_status` / `english_prompt`). Submit then uses that cache |
| `studio_select_take` / `studio_reject_take` | `take_id` | 採用 / 不採用 |

Recommended flow:

1. `studio_list_projects` (then `studio_get_project`) — never rebuild what is
   already there.
2. Create the project, write `synopsis` / `world_notes`, then register the cast
   and the sets as assets. For a character whose look must stay stable, make a
   still with a normal `mode: "image_only"` job first and register it with
   `studio_register_asset_from_job` — a real file pins the look, a
   `prompt_caption` only describes it.
3. Lay out 話 / 場, then write the Shots in order.
4. `studio_render_shot`, then **end your turn**. A render takes minutes and you
   must never wait or poll for it. The app appends a `studio_take_finished`
   EVENT carrying the result (`candidate` or `failed`, with the error message
   when it failed) and gives you a fresh turn at that moment — in agent mode and
   in the canvas chat alike. So: say in Japanese what you queued, and stop — no
   `studio_get_takes` loop, no filler turns.
5. When the result reaches you: `studio_select_take` when it is good, or
   re-render with a different seed / text when it is not. `inspect` the Take's
   `job_id` only if the user asks you to check it.
6. Move on to the next Shot, and report progress in Japanese as you go.
"""

AGENT_TOOLS = """\
# TOOLS

You run as an agentic CLI with real tools — use them proactively:

- **Read files**: you can open any file in your working directory, including
  images (generated stills, last frames, the frames `inspect` extracts).
  Actually look at them before judging quality.
- **Write files**: keep 企画メモ / research notes / prompt drafts as Markdown
  files in the working directory, then register them with a `note` action
  (`filename`) so they appear in the user's artifact panel.
- **Web search**: research trends, locations, choreography or terminology when
  it makes the plan better. Summarize findings into a `note` action with
  `kind: "research"` so it shows up as a research artifact.

Stay inside your working directory; never touch other paths. If a tool turns
out to be unavailable, continue without it — the ACTION PROTOCOL alone is
enough to do the job.
"""

AGENT_OUTPUT_RULES = """\
# OUTPUT RULES

- Japanese prose for the user, English for every model prompt inside `job`.
  A loose job's `video_prompt` is the same official MiniMax H3 document.
- At most one ```json action per reply, as the last thing in the message.
- Never invent job ids: use the ones the EVENT messages give you.
- `done` only after every approved task reached a final state.
- **Naming**: a task `label` and every `note` / `rename` title must be a
  Japanese work title the user grasps at a glance — e.g.
  「夕暮れ屋上ダンス・引きカメラ」. Never use file-name-like strings, job ids,
  seeds, model names or English slugs as a title.
- The artifact panel only shows those titles (never a thumbnail), so after a
  job finishes, use `rename` whenever the automatic title
  （「<label> 生成画像」/「<label> 動画」）does not describe the result well.
"""


def _agent_guardrails(ctx: AgentSessionCreate, max_tasks: int) -> str:
    modes = {
        "every_job": "毎ジョブ確認（1 本終わるごとにユーザーへ確認する）",
        "milestone": "節目のみ確認（区切りでだけ確認する）",
        "auto": "完了まで自走（確認は最小限）",
    }
    lines = [
        "# GUARDRAILS",
        "",
        f"- Check-in mode: **{ctx.checkin_mode}** — {modes[ctx.checkin_mode]}.",
    ]
    # 生成本数の上限は 0 で無制限（セッション作成時に外したとき）。
    if ctx.auto_limit > 0:
        lines.append(
            f"- Generation budget: **{ctx.auto_limit}** generated jobs per stretch."
            " When the budget is used up the app itself asks the user whether to"
            f" keep going; approval buys another {ctx.auto_limit} jobs and the"
            " question comes back at the next milestone. Plan inside the budget"
            " and never try to work around it — you cannot approve the extension"
            " yourself."
        )
    else:
        lines.append(
            "- Generation budget: **unlimited** — the app will not stop you at a"
            " job count, so keep the plan proportionate to the goal yourself."
        )
    # 1 プラン提案あたりの新規ジョブ数の上限は自走モードだけ。他のモードは
    # プラン承認とチェックインで必ず人間が挟まるので、プランの長さは自由。
    # 設定 ``agent_max_plan_tasks`` が 0 なら自走でも上限なし。
    if ctx.checkin_mode == "auto" and max_tasks > 0:
        lines.append(
            f"- One plan proposal may add at most {max_tasks} **new** jobs;"
            " tasks that already finished and are only re-listed in a revised"
            " plan do not count. Propose more in the next revision."
        )
    return "\n".join(
        [
            *lines,
            "- Generation only starts after the user approves the plan."
            + (
                " `continue` / `rerun` run right away in this self-driving"
                " session."
                if ctx.checkin_mode == "auto"
                else " `continue` / `rerun` outside an approved plan are held"
                " until the user approves them, so expect a check-in before"
                " they run."
            ),
            "- Stay inside your session work directory when you read or write"
            " files.",
            "- Only adults; no real, identifiable people; no non-consent themes"
            " and no animals in sexual contexts.",
        ]
    )


def _agent_choices(
    options: Options, lora_samples: dict[str, list[str]] | None = None
) -> str:
    lines = ["# CHOICES (the only values that exist in this installation)", ""]
    samples = lora_samples or {}

    image_loras = [lora for lora in options.loras if lora.target != "video"]
    video_loras = [lora for lora in options.loras if lora.target == "video"]

    def lora_lines(loras: list, *, with_family: bool = False) -> None:
        for lora in loras:
            label = f"「{lora.display_name}」" if lora.display_name else ""
            family = (
                f", family `{lora.family}`" if with_family and lora.family else ""
            )
            lines.append(
                f"- `{lora.lora_name}` -> trigger `{lora.trigger_word}`"
                f" {label} (default strength {lora.default_strength:g}"
                + family
                + (f", audio {lora.default_audio}" if lora.default_audio else "")
                + ")"
            )
            for sample in samples.get(lora.lora_name, ()):
                lines.append(f"  - reference image: `{sample}`")

    if options.loras:
        if image_loras:
            lines.append(
                "画像用 LoRA — the `loras` field of a job"
                " (lora_name -> trigger word):"
            )
            lora_lines(image_loras, with_family=True)
            lines.append(
                "Put the trigger words of the LoRAs you use into `trigger_text`"
                " and use the trigger word as the subject's name inside"
                " `image_prompt`."
            )
            lines.append(
                "**`family` は必ず `image_workflow` のファミリーと一致させること**"
                " — 別ファミリーの LoRA を入れたジョブは拒否されます"
                "（IMAGE WORKFLOWS に各ワークフローのファミリーがあります）。"
            )
            lines.append("")
        else:
            lines += ["画像用 LoRA はありません: `loras` は空のままにしてください。", ""]

        if video_loras:
            lines.append(
                "動画用 LoRA — the `video_loras` field of a job. These are"
                " spliced into the video graph (`mode` must be `full` or"
                " `i2v`) (lora_name -> trigger word):"
            )
            lora_lines(video_loras)
            lines.append(
                "Put their trigger words into `video_trigger_text` and use them"
                " inside `video_prompt`."
            )
        else:
            lines.append(
                "動画用 LoRA はありません: `video_loras` は空のままにしてください。"
            )
        lines.append(
            "画像用と動画用は入れ替えられません — 画像用を `video_loras` に、"
            "動画用を `loras` に入れたジョブは拒否されます。"
        )
        if any(samples.values()):
            lines += [
                "",
                "Reference images: the `lora_samples/…` paths above are sample"
                " images of each character, copied into your working directory."
                " They show what the character is supposed to look like. Open"
                " and study them before planning, and after every generation"
                " compare the output (generated stills, and the `inspect`"
                " frames when the user asks you to check it)"
                " against them — face, hairstyle, body proportions and overall"
                " style must match the reference. If the output drifts from the"
                " reference, adjust the prompt or LoRA strength and rerun"
                " instead of accepting it.",
            ]
    else:
        lines.append(
            "No character LoRA is registered: leave `loras` and `video_loras`"
            " empty."
        )
    lines.append("")

    if options.aspect_ratios:
        lines.append("Aspect ratios: " + ", ".join(f"`{a}`" for a in options.aspect_ratios))
        lines.append(
            "When a job supplies `source_image`, the video keeps that image's own"
            " aspect ratio (only `megapixels` still applies) so the start frame is"
            " not cropped — `aspect_ratio` then only matters for the image stage."
        )
    else:
        lines.append(
            "Aspect ratios: ComfyUI の一覧を取得できませんでした。"
            " `aspect_ratio` は省略して既定値を使ってください。"
        )
    lines.append("")

    for title, assets in (
        ("Audio assets (audio_path)", options.audio_assets),
        ("Image assets (source_image / end_image)", options.image_assets),
        ("Video assets (reference_video)", options.video_assets),
    ):
        lines.append(f"{title}:")
        if assets:
            lines += [f"- `{a.path}`" for a in assets[:30]]
        else:
            lines.append("- (none)")
        lines.append("")

    lines.append(_library_lines(options))
    lines.append("")

    lines.append("Negative prompt presets:")
    for name, value in options.negative_presets.items():
        lines.append(f"- {name}: `{value}`")
    lines.append("")
    lines.append(_model_choices_lines(options))
    return "\n".join(lines)


#: CHOICES に載せるライブラリの件数（種別ごと。古いものは省く）
LIBRARY_PROMPT_LIMIT = 50

_LIBRARY_FIELDS = {
    "image": "source_image / end_image",
    "video": "reference_video",
    "audio": "audio_path",
}


def _library_lines(options: Options) -> str:
    """ライブラリの目録（SPEC §7.2）。ジョブの入力にそのまま書けるパスを出す。

    履歴の生成物と手元のアップロードのうち、利用者が「残す」と決めたものだけが
    入っている棚なので、素材を選ぶときはアセット一覧よりこちらを先に見せる。

    ここに載るのは種別ごとの新しい :data:`LIBRARY_PROMPT_LIMIT` 件だけ。省略した
    ぶんが**あること**と、``library_search`` で取りに行けることを必ず明示する
    （「50 件しか無い」と誤解させないため）。
    """
    lines = [
        "Library（取っておいた素材、`path` をそのままジョブの入力に書けます）:",
    ]
    if not options.library:
        lines += [
            "- (none)",
            "",
            "まだ何も登録されていません。`library` アクションで生成物を取っておけます。",
        ]
        return "\n".join(lines)
    hidden = 0
    for kind, field in _LIBRARY_FIELDS.items():
        items = [item for item in options.library if item.kind == kind]
        lines.append(f"- {kind}（{field}、全 {len(items)} 件）:")
        if not items:
            lines.append("  - (none)")
            continue
        for item in items[:LIBRARY_PROMPT_LIMIT]:
            tags = f" [{', '.join(item.tags)}]" if item.tags else ""
            nsfw = " 🫣NSFW" if item.nsfw else ""
            category = f"（{item.category}）" if item.category else ""
            lines.append(f"  - `{item.path}` — 「{item.name}」{category}{tags}{nsfw}")
        if len(items) > LIBRARY_PROMPT_LIMIT:
            rest = len(items) - LIBRARY_PROMPT_LIMIT
            hidden += rest
            lines.append(
                f"  - …ほか {rest} 件（ここに載るのは新しい"
                f" {LIBRARY_PROMPT_LIMIT} 件だけ。`library_search` で辿れます）"
            )
    lines.append("")
    lines.append(
        "`（…）` は分類（`character` / `background` / `prop`。無いものは未分類 ="
        " `none`）、`[…]` は自由なタグです。分類は `library_search` の `category`"
        "で絞り込めて、リファレンスシート（`library_sheet`）のパネルの大小を決めます。"
    )
    lines.append(
        (
            f"上の一覧は種別ごとに新しい {LIBRARY_PROMPT_LIMIT} 件までで、"
            f"ここに出していない素材が {hidden} 件あります。"
            if hidden
            else "上の一覧が現時点の全件です。"
        )
        + " 名前やタグで探すとき、古いものを見たいときは `library_search`"
        " アクション（`q` / `tag` / `kind` / `category` / `offset`）を使ってください"
        "——**ライブラリ全体が対象**で、結果は次のターンに EVENT として届きます。"
    )
    return "\n".join(lines)


def _model_choices_lines(options: Options) -> str:
    """The `model_overrides` catalogue: which model a job may switch (SPEC §3.3).

    Only the slots the user registered two or more candidates for are listed
    (``GET /api/options`` の ``model_slots``), so a session where nothing was
    registered simply tells the agent to leave the field out.
    """
    if not options.model_slots:
        return (
            "使用モデルの切り替え候補は登録されていません:"
            " `model_overrides` は指定しないでください（各ワークフローの既定モデルが"
            "使われます）。"
        )
    lines = [
        "使用モデルの切り替え（ジョブの `model_overrides`）— ワークフローごとに、"
        "この環境で差し替えられるモデルスロットと候補です:",
        "",
    ]
    grouped: dict[str, list[ModelSlot]] = {}
    for slot in options.model_slots:
        grouped.setdefault(slot.workflow_id, []).append(slot)
    for workflow_id, slots in grouped.items():
        lines.append(f"- `{workflow_id}`（{slots[0].workflow_label}）:")
        for slot in slots:
            label = slot.label or slot.class_type
            names = ", ".join(f"`{name}`" for name in slot.choices)
            lines.append(
                f"  - `{slot.key}`（{label}、既定 `{slot.default}`）: {names}"
            )
    lines += [
        "",
        '`model_overrides` は `{"<キー>": "<ファイル名>"}` の形で、**そのジョブが'
        "走らせるワークフローのキーだけ**を書くこと（`mode: \"full\"` なら画像と"
        "動画の両方、`i2v` なら動画だけ、`audio` なら音声だけ）。値は上の候補から"
        "選ぶ。省略すれば既定モデルが使われる（それが通常の選択で、切り替えるのは"
        "ユーザーが指示したときか、同じ画で絵柄違いを比べたいときだけ）。",
    ]
    return "\n".join(lines)


def _model_sources_lines(sources: list[ModelSource]) -> str:
    """MODEL SOURCES: 登録済みモデル・LoRA の配布ページ（AGENT-MODE §3.1）。

    ``page`` は Web 閲覧で開ける配布ページ（Hugging Face のリポジトリ /
    Civitai のモデルページ）。エージェントはここでトリガーワード・推奨設定・
    作例プロンプトを自分で調べられる。解決できなかったものは ``download`` だけを
    出す（Civitai の API が引けなかった場合など）。
    """
    lines = [
        "# MODEL SOURCES (どこから来たモデルか / 使い方の調べ先)",
        "",
        "この環境に登録されている LoRA・モデルファイルの取得元です。`page` は"
        "配布ページなので、**Web 閲覧で開いて**トリガーワード・推奨設定"
        "（strength / steps / CFG / sampler / 解像度）・作例プロンプトを調べ、"
        "プランと `image_prompt` / `video_prompt` に反映してください"
        "（特に見慣れない LoRA を初めて使うときは、思い込みで書かずに必ず調べる）。"
        "`page` が無いものはダウンロード URL しか登録されていないので、"
        "調べられなければ既定の設定のまま使ってかまいません。",
        "",
    ]
    for title, kind in (("LoRA:", "lora"), ("モデルファイル:", "model")):
        items = [source for source in sources if source.kind == kind]
        if not items:
            continue
        lines.append(title)
        for source in items:
            label = f"「{source.label}」" if source.label else ""
            usage = f" — {', '.join(source.usage)}" if source.usage else ""
            lines.append(f"- `{source.filename}`{label}{usage}")
            if source.page_url:
                lines.append(f"  - page: {source.page_url}")
            lines.append(f"  - download: {source.download_url}")
        lines.append("")
    lines.append(
        "ここに出ていないファイルは取得元が登録されていないだけで、使えないという"
        "意味ではありません。ページの記述と CHOICES が食い違うときは"
        "**CHOICES が正**です（この環境に実在する値だけが書いてあります）。"
    )
    return "\n".join(lines)


def _option_ids(workflows: Iterable[WorkflowOption]) -> set[str] | None:
    """フォームに出ているワークフロー id（1 件も無ければ ``None`` = 絞らない）。

    ``Options`` を最小限だけ組み立てて呼ぶ古い経路でカタログが空にならないよう、
    空リストは「指定なし」として扱う。
    """
    ids = {workflow.id for workflow in workflows}
    return ids or None


def build_agent_system_prompt(
    ctx: AgentSessionCreate,
    options: Options,
    *,
    workdir: str = "",
    max_tasks: int = 5,
    tools_enabled: bool = False,
    lora_samples: dict[str, list[str]] | None = None,
    model_sources: list[ModelSource] | None = None,
    comfy_target: str = "",
) -> str:
    """System prompt of one agent session (AGENT-MODE §5.1).

    ``comfy_target`` は :class:`app.models.Settings` の接続先で、VIDEO WORKFLOWS
    の末尾に「MiniMax H3 のどの版を優先するか」を書き足すためだけに使う。
    システムプロンプトは**セッション作成時に一度だけ焼き込む**ので、あとから
    接続先を切り替えても既存セッションの文面は変わらない（新しいセッションから
    効く）。空文字なら節ごと出さない。
    """
    # カタログはフォームの選択肢（``GET /api/options``）と同じ一覧にする:
    # 接続先で使えないワークフローは options から落ちているので、その id 集合で
    # カタログも絞る（空のときは絞らない = 従来どおり全件）。
    parts = [AGENT_ROLE, AGENT_PROTOCOL,
             image_workflow_catalog_section(
                 comfy_target, _option_ids(options.image_workflows)
             ),
             image_prompt_guides_section(),
             workflow_catalog_section(
                 comfy_target, _option_ids(options.video_workflows)
             ),
             video_prompt_guides_section(),
             audio_workflow_catalog_section(
                 comfy_target, _option_ids(options.audio_workflows)
             ),
             audio_prompt_guides_section(),
             H3_QUALITY_BAR,
             FEW_SHOT_H3 + "\n" + MORE_EXAMPLES_HINT,
             FEW_SHOT_IMAGE_KREA2,
             AGENT_STUDIO,
             _agent_choices(options, lora_samples),
             _agent_guardrails(ctx, max_tasks), AGENT_OUTPUT_RULES]
    # 中身の無い節（使えるモデルにガイドが 1 つも無いとき）は落とす
    parts = [part for part in parts if part.strip()]
    # 取得元が 1 件も登録されていない環境ではセクションごと出さない
    if model_sources:
        parts.insert(-2, _model_sources_lines(model_sources))
    if tools_enabled:
        parts.insert(2, AGENT_TOOLS)
    context = ["# SESSION CONTEXT", ""]
    if workdir:
        context.append(
            f"Your working directory is `{workdir}`. Memos, research notes and"
            " the inspection frames the app extracts all live there."
        )
    context += [
        "",
        "When the user attaches files, their message carries an"
        " `[Attached files — …]` block listing workdir-relative paths under"
        " `attachments/`. Open every listed file and read / look at it before"
        " you answer — the attachment is part of the instruction, not"
        " decoration.",
    ]
    if ctx.goal.strip():
        context += ["", "User's goal for this session:", "```", ctx.goal.strip(), "```"]
    parts.insert(-1, "\n".join(context))
    return "\n\n".join(part.strip() for part in parts) + "\n"


_AGENT_ROLE_LABEL = {
    "system": "SYSTEM",
    "user": "USER",
    "assistant": "ASSISTANT",
    "event": "EVENT",
    "checkin": "CHECKIN",
}

AGENT_RETRY_SUFFIX = """\

IMPORTANT: your previous action could not be used ({reason}).
Re-send the reply with exactly one ```json fenced action object that follows
the ACTION PROTOCOL, or with no JSON at all if you only meant to talk.
"""


def build_agent_conversation(
    messages: list[AgentMessage], *, retry_reason: str | None = None
) -> str:
    """Flatten the agent transcript into the single ``grok -p`` argument.

    ホストへ渡す経路からは外した。REPLAY 組み立てと古い呼び出し用に残す。
    """
    chunks: list[str] = []
    for message in messages:
        if message.role == "system":
            chunks.append(message.content.strip())
            continue
        label = _AGENT_ROLE_LABEL.get(message.role, message.role.upper())
        if message.kind:
            label = f"{label} ({message.kind})"
        chunks.append(f"### {label}\n{message.content.strip()}")
    if len(messages) > 1:
        chunks.insert(1, "# CONVERSATION SO FAR (oldest first)")
    body = "\n\n".join(chunks)
    if retry_reason:
        body += "\n" + AGENT_RETRY_SUFFIX.format(reason=retry_reason)
    return (
        body
        + "\n\n### ASSISTANT\n(Your reply — Japanese text, optionally followed by"
        " one ```json action.)\n"
    )


def build_replay(messages: list[AgentMessage]) -> str:
    """load / resume 失敗時に 1 通だけ送る過去会話（ツールは再実行しない）。"""
    chunks: list[str] = []
    for message in messages:
        if message.role == "system":
            continue
        label = _AGENT_ROLE_LABEL.get(message.role, message.role.upper())
        if message.kind:
            label = f"{label} ({message.kind})"
        chunks.append(f"### {label}\n{message.content.strip()}")
    if not chunks:
        return ""
    return (
        "# REPLAY\n"
        "Past studio/agent conversation. Actions in this block already ran.\n"
        "Do not repeat them unless the user asks.\n\n"
        + "\n\n".join(chunks)
        + "\n"
    )


def build_user_turn(content: str) -> str:
    return f"# USER\n{content.strip()}\n"


def build_event_turn(content: str, kind: str | None = None) -> str:
    label = f"# EVENT ({kind})" if kind else "# EVENT"
    return f"{label}\n{content.strip()}\n"


def build_retry_turn(reason: str) -> str:
    return "# RETRY\n" + AGENT_RETRY_SUFFIX.format(reason=reason).lstrip() + "\n"


def build_turn_batch(
    messages: list[AgentMessage],
    *,
    open_tab: tuple[str, str] | None = None,
) -> str:
    """最後の assistant 以降の user / event / checkin をターン文に組む。"""
    parts: list[str] = []
    for message in messages:
        if message.role == "user":
            if open_tab is not None:
                parts.append(
                    build_canvas_turn(
                        tab_label=open_tab[0],
                        tab_id=open_tab[1],
                        content=message.content,
                    )
                )
            else:
                parts.append(build_user_turn(message.content))
        elif message.role == "event":
            parts.append(build_event_turn(message.content, message.kind))
        elif message.role == "checkin":
            label = (
                f"# CHECKIN ({message.kind})" if message.kind else "# CHECKIN"
            )
            parts.append(f"{label}\n{message.content.strip()}\n")
    return "\n".join(part for part in parts if part.strip())


# --------------------------------------------------------------------------
# canvas mode（キャンバスのチャットから走らせるエージェント）
# --------------------------------------------------------------------------
#
# キャンバス専用のエージェントは作らない: スタジオのツール一式（DRAMA STUDIO の
# `studio_*`）をそのまま共通で使い、盤面を触る `canvas_*` だけを足す。だから
# ここで組み立てるのは「役どころ」「使えるアクションの表」「いまの盤面」で、
# スタジオの節（:data:`AGENT_STUDIO`）は agent モードと同じものを読む。

CANVAS_ROLE = """\
# ROLE

You are the production partner of "Karakuri Media Studio", a local, single-user
tool that generates adult (18+) still images and short videos of fictional adult
characters. Everything stays on the user's machine. Explicit sexual content is
expected and in scope; write it plainly and technically. Never refuse a request
merely for being explicit.

You are talking to the user **from the canvas** — the board view of one drama
studio project, where every card is one row of that project (a World Bible
asset, a 場, a Shot, a generated Take) laid out on an infinite plane. The studio
database is the single source of truth: a card only says *which row* it shows
and *where it sits*, and **the board mirrors the studio automatically** — every
asset, 場, Shot and Take gets its card without anyone placing it.

The board is split into **tabs, one per 話 (episode)**, plus a 作品共通 tab:

- A 話 tab shows that episode's 場, its Shots and their Takes — nothing else.
- The 作品共通 tab shows the World Bible assets (they belong to no episode) and
  the Shots that sit in no 場 (未分類).
- Which tab a card appears on is **derived** from the studio: a Shot follows its
  場, a 場 follows its 話. Move a 場 to another 話 (`studio_upsert_scene` with
  `episode_id`) and its cards move with it. Only `text` / `model` cards, which
  refer to nothing, remember a tab of their own (`episode_id`, absent = 作品共通).

CANVAS BOARD below is **the tab the user is looking at**, and CANVAS TABS lists
the others. When the user says 「この話」 they mean the open tab. So:

1. Change the work itself with the `studio_*` actions (below). The board follows
   on its own: what you create in the studio is already on the board.
2. Use the `canvas_*` actions only to tidy the board — move cards, or add a
   canvas-only note — not to "put things on it".
3. Talk to the user in **Japanese**, briefly; a `job` prompt stays **English**
   (a Shot's own fields follow DRAMA STUDIO — they may be Japanese).
4. Ask (plain text, no JSON) when the goal is unclear. Do not invent a project
   structure the user did not ask for.
"""

CANVAS_PROTOCOL = """\
# ACTION PROTOCOL

Your reply is either plain Japanese text, or plain Japanese text followed by
**exactly one** ```json fenced action object. Never emit two actions. The app
runs it, writes the result back into this conversation as an EVENT message, and
asks you again — so work one step per reply and read the EVENT before deciding
the next one.

```json
{"action": "canvas_place_card", "project_id": "…", "kind": "text",
 "x": 640, "y": 0, "data": {"body": "…"}}
```

# CANVAS

| action | body | meaning |
|---|---|---|
| `canvas_list_cards` | `project_id`, optional `episode_id` (a 話 id, or `"common"`) | what is on the board right now, with each card's id, kind, position and what it refers to — one tab if `episode_id` is given, otherwise the whole board with each card's tab |
| `canvas_search_sessions` | `project_id`, `q`, optional `session_id` (exclude this chat), optional `offset` | search other chats of **this project** by title or message text; the result arrives next turn as an EVENT (snippets only — use `canvas_read_session` to read a hit). No approval needed |
| `canvas_read_session` | `session_id` (the other chat), optional `project_id`, optional `offset` | read that chat's messages; the result arrives next turn as an EVENT. This chat is excluded. No approval needed |
| `canvas_place_card` | `project_id`, `kind`, the fields to create one (`title`, `scene_id` / `episode_id`, `asset_kind`), `x`, `y`, optional `w` / `h`, optional `data` | create **something new** and its card |
| `canvas_move_card` | `card_id`, `x`, `y`, optional `w` / `h` / `z` | move / resize a card (touches nothing in the studio) |
| `canvas_update_card` | `card_id`, `data` (text / model cards only), optional `x` / `y` / `w` / `h` / `z` | edit a canvas-only card's contents |

Card kinds and what they refer to:

- `character` / `location` / `object` / `style` / `reference` — a World Bible
  asset (its studio `category` is `character` / `environment` / `prop` /
  `style` / `reference` respectively).
- `scene` — a 場, `shot` — a Shot, `media` — a generated Take.
- `text` — a canvas-only sticky note, `data: {"body": "…"}`.
- `model` — a canvas-only generation-settings note, `data: {"target": "image" |
  "video" | "audio", "workflow": "…", "note": "…"}`.

Rules:

- You can look up other chats of this project, but only when the user **asks**
  you to (前のセッションを見て / さっきの会話を読んで / use the earlier chat).
  Do not browse them on your own. Then `canvas_search_sessions` (`q` from their
  words, or empty to list newest-first) and `canvas_read_session` with that
  `session_id`. Do not guess, and do not decide from snippets alone.
- **Never place what already exists.** Anything in the studio is on the board
  already (CANVAS BOARD below lists it). `canvas_place_card` *creates* a new
  asset / 場 / Shot together with its card (`title`, plus `scene_id` /
  `episode_id` for where it belongs), so use it only when the user asked for
  something new — `studio_upsert_asset` / `_scene` / `_shot` do the same and are
  usually the clearer choice. `media` cards cannot be created at all: a Take is
  born from a render and appears by itself.
- One entity has at most one card.
- A reference card has **no** contents of its own: edit an asset, a 場 or a Shot
  with `studio_upsert_asset` / `studio_upsert_scene` / `studio_upsert_shot`, not
  with `canvas_update_card`. Only `text` and `model` cards carry `data`.
- New cards land on **the open tab**: a `text` / `model` card is placed there
  unless you pass another `episode_id`, and a new 場 belongs to the open 話.
  To work on another 話, say so — do not silently place things elsewhere.
- The board is a plane in pixels; a card is 320×220 by default. Place related
  cards in a readable row or column (leave ~360 px horizontally, ~260 px
  vertically) near what they belong to, and never on top of an existing card.
  Do not rearrange the whole board — the user lays it out by hand.
- Deleting is the user's business; there is no action for it. A reference card
  only goes away when its studio row does.
"""

CANVAS_OUTPUT_RULES = """\
# OUTPUT RULES

- Japanese prose for the user, English for every `job` prompt (a Shot's fields
  follow DRAMA STUDIO).
- At most one ```json action per reply, as the last thing in the message.
- Never invent ids: use the ones CANVAS BOARD, THIS PROJECT and the EVENT
  messages give you. `project_id` is the project below unless the user really
  means another one.
- A render (`studio_render_shot`) is a real generation job and takes minutes.
  Ask the user first (plain text) before rendering more than one Shot. Once it
  is queued, say so and **end the turn** — never poll `studio_get_takes` for it.
  The app sends you a `studio_take_finished` EVENT when the render is over
  (success or failure) and wakes this chat up, so there is nothing to wait for.
  Pick the next step from that event: 採用 / 不採用, the next Shot, or a fix
  when it failed.
- When the request is done, say so in plain text with **no JSON** — that ends
  the run. Send `{"action": "done", "summary": "…"}` instead only when a summary
  of several steps is worth writing out.
- EVENT messages are written by the app, not by the user.
"""


def build_canvas_contract(
    *,
    project_id: str = "",
    workdir: str = "",
    tools_enabled: bool = False,
) -> str:
    """キャンバス Grok セッション誕生時だけ渡す契約（現況は入れない）。"""
    parts = [CANVAS_ROLE, CANVAS_PROTOCOL, AGENT_STUDIO]
    if tools_enabled:
        parts.append(AGENT_TOOLS)
    context = [
        "The LIVE SNAPSHOT / THIS PROJECT / CANVAS BOARD blocks (when present)"
        " can be stale. When you need a new id or the current board, call"
        " `studio_get_project` / `canvas_list_cards`.",
        "# EVENT blocks are results the app wrote after running your action.",
        "# REPLAY blocks already ran. Do not repeat those actions unless the"
        " user asks.",
        "Other chats of this project can be searched with"
        " `canvas_search_sessions` and read with `canvas_read_session`,"
        " but only when the user asks you to. Do not open them unprompted.",
    ]
    if project_id:
        context.append(f"This project's `project_id` is `{project_id}`.")
    if workdir:
        context.append(
            f"Your working directory is `{workdir}`. Stay inside it when you"
            " read or write files."
        )
    context += [
        "When the user attaches files, their message ends with an"
        " `[Attached files — …]` block listing an absolute path, a kind"
        " (image / video / audio / document) and the original file name for"
        " each one. They sit in `attachments/` inside your working directory:"
        " **open every one and look at it / read it** before you answer — the"
        " attachment is part of the instruction, not decoration. To keep one"
        " for the project, register it with `studio_upsert_asset` and its"
        " `path` (the file is copied into the app's `assets/`, so `@名前` can"
        " attach it as a reference later).",
    ]
    parts += ["\n".join(context), CANVAS_OUTPUT_RULES]
    return "\n\n".join(part.strip() for part in parts) + "\n"


def build_canvas_snapshot(
    *,
    project: str,
    board: str,
    tabs: str = "",
    tab_id: str = "common",
    tab_label: str = "作品共通",
) -> str:
    """必要なときだけ付ける現況（開いているタブ + 作品 + 盤面）。"""
    lines = [
        "# OPEN TAB",
        f"「{tab_label}」 (`episode_id`: `{tab_id}`)",
        "",
        "# LIVE SNAPSHOT",
        "# THIS PROJECT",
        "",
        project.strip(),
        "",
        "# CANVAS BOARD",
        "",
        f"The user has the **「{tab_label}」** tab open (`episode_id`:"
        f" `{tab_id}`). 「この話」「このタブ」 means this one.",
        "",
        board.strip(),
    ]
    if tabs:
        lines += ["", "# CANVAS TABS", "", tabs.strip()]
    return "\n".join(lines) + "\n"


def build_canvas_turn(*, tab_label: str, tab_id: str, content: str) -> str:
    """毎回のユーザー発言（タブは短く、本文は # USER）。"""
    return (
        "# OPEN TAB\n"
        f"「{tab_label}」 (`episode_id`: `{tab_id}`)\n"
        "\n"
        f"# USER\n{content.strip()}\n"
    )


def build_canvas_system_prompt(
    *,
    project: str,
    board: str,
    tabs: str = "",
    tab_id: str = "common",
    tab_label: str = "作品共通",
    workdir: str = "",
    tools_enabled: bool = False,
    project_id: str = "",
) -> str:
    """契約 + 現況を 1 本にしたもの（ワンショット冷スタート / 旧呼び出し用）。"""
    return (
        build_canvas_contract(
            project_id=project_id, workdir=workdir, tools_enabled=tools_enabled
        ).rstrip()
        + "\n\n"
        + build_canvas_snapshot(
            project=project,
            board=board,
            tabs=tabs,
            tab_id=tab_id,
            tab_label=tab_label,
        )
    )
