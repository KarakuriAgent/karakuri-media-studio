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
3. video prompt spec — LTX 2.3, plus the two prompt templates and the selected
   video workflow's own characteristics (generated from ``app.workflows``, so
   the prompts, the UI and the job validator share one source of truth),
4. few-shot examples taken from ``docs/prompt-samples.md`` (kept here as
   constants so the running app never reads the docs tree),
5. the form context (mode, LoRA trigger words, duration, drafts),
6. output rules — a single ```json fence with
   ``{image_prompt, video_prompt, notes}``.
"""

from __future__ import annotations

from .models import (
    AgentMessage,
    AgentSessionCreate,
    ChatMessage,
    ChatSessionCreate,
    ModelSlot,
    ModelSource,
    Options,
    missing_job_fields,
    video_workflow_problem,
)
from .workflows import (
    AUDIO_CATEGORIES,
    BPM_RANGE,
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
    image_catalog,
    image_families,
    video_catalog,
)

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
   - facial expression / emotion
   - action and how it develops over the clip (tempo, intensity)
   - spoken lines and sounds (does the character say anything?)
   **Ask in Japanese**, concisely, and offer plausible default options so the
   user can just pick one.
2. **Write the prompts.** As soon as you have enough, output the final JSON
   (see OUTPUT RULES).

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

GROK_IMAGINE_SPEC = """\
# IMAGE PROMPT SPEC — Grok Imagine (via the Grok Build CLI, subscription quota)

Grok Imagine is driven through the official CLI, so `image_prompt` is passed as
plain natural language inside an instruction — there is no negative prompt, no
weight syntax and no exact resolution control.

1. **Write natural sentences, not a tag list.** Order: subject → style / medium
   → environment → lighting → mood → technical (lens, framing, finish).
2. **The first 20-30 words carry the most weight.** Put the subject and the look
   there; details added at the end influence the picture much less.
3. **Name the light**: where it comes from and what quality it has ("soft window
   light from the left, shallow depth of field"). Material words land well
   ("matte-black ceramic", "brushed steel", "raw linen").
4. **Never write what you do not want** — negations are ignored. Say the
   positive form instead: `sharp focus` rather than `no blur`, `plain seamless
   background` rather than `no clutter`.
5. **Aspect ratio and resolution are wishes, not settings**: the app writes the
   ratio into the instruction, but the model may not follow it exactly. Do not
   demand pixel dimensions in the prompt text.
6. **Moderation is strict**: real people, celebrities, trademarks and logos are
   refused (false positives are common). Describe an invented person by their
   features instead of naming anyone.
7. **Adults only**: every depicted person is an adult with an unambiguously
   adult body and face.

Example:
```
A weathered fisherman mending a net on a wooden pier at dawn, documentary photograph, muted colour, salt-worn timber and coiled rope around him, low sun raking from the left through sea haze, quiet and patient mood, 35mm lens at chest height, sharp focus on his hands, soft falloff into the harbour behind.
```
"""

#: family -> the prompt spec section to embed for it
IMAGE_SPECS: dict[str, str] = {
    "krea2": IMAGE_SPEC,
    "anima": ANIMA_SPEC,
    "z-image": Z_IMAGE_SPEC,
    "qwen-image": QWEN_IMAGE_SPEC,
    "grok-imagine": GROK_IMAGINE_SPEC,
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
    "grok-imagine": (
        "Natural sentences, subject and style in the first 20-30 words, then"
        " environment, lighting, mood, camera. No negatives (write the positive"
        " form), no exact resolution — the ratio is only a wish."
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

VIDEO_SPEC = """\
# VIDEO PROMPT SPEC — LTX 2.3 22B (dev / distilled fp8), TE: Gemma-3 12B

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
5. **Audio** — LTX 2.3 generates sound together with the picture, so audio is
   mandatory: ambience, room tone, breathing, moans, impact sounds. **Spoken
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

    自由記述ではなく決まった選択肢で挙動が決まるワークフロー（wan_dancer）向け。
    ジョブの ``selects`` に書ける名前と、その値をそのまま列挙する。
    """
    if not entry.selects:
        return []
    lines = [
        "  - 選択項目（ジョブの `selects` に"
        ' `{"<名前>": "<選んだ値>"}` の形で書く。以下の文字列のみ使用可）:'
    ]
    for name, label, choices, default, auto, hint in entry.selects:
        values = ", ".join(f"`{choice}`" for choice in choices)
        note = f" {hint}" if hint else ""
        fallback = (
            "省略すると入力から自動で決まる"
            if auto
            else f"省略すると `{default}`"
        )
        lines.append(f"    - `{name}`（{label}）: {values} — {fallback}。{note}")
    return lines


def workflow_catalog_section() -> str:
    """The `video_workflow` catalog embedded in the agent system prompt."""
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
    for entry in video_catalog():
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
    hint = IMAGE_PROMPT_HINTS.get(entry.family)
    if hint:
        lines.append(f"  - Writing `image_prompt`: {hint}")
    if entry.notes:
        lines.append(f"  - Notes: {entry.notes}")
    return lines


def image_workflow_catalog_section() -> str:
    """The `image_workflow` catalog embedded in the agent system prompt."""
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
    for entry in image_catalog():
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
# VIDEO SPEC は LTX 2.3 のもの。外部 API のモデルは書き方も守備範囲も違うので、
# 画像 / 音声と同じ流儀で「モデルごとのガイド」を持ち、チャットでは**選択中の
# ワークフローの分だけ**注入する（両方渡すと混ざる）。
#
# Veo 3.1 — https://docs.kie.ai/veo3-api/generate-veo-3-video.md と
# https://ai.google.dev/gemini-api/docs/veo（プロンプト構成・音声・否定表現）

VEO_VIDEO_GUIDE = """\
# VIDEO PROMPT SPEC — Google Veo 3.1 (kie.ai, `veo3_1_fast` / `veo3_1_quality`)

Veo generates **picture and sound together** in one 4-8 second take. It is not
LTX: where this section and the VIDEO PROMPT SPEC above disagree, this one wins.

Write `video_prompt` as 3-6 English sentences (100-150 words), in this order:

1. **Composition / shot** — shot size and angle (medium close-up, low-angle
   wide shot), and the format if it matters.
2. **Subject** — who / what, described concretely (age range, build, hair,
   wardrobe, distinguishing details).
3. **Action** — one continuous action, in the order it happens.
4. **Scene** — where, when, weather, set dressing.
5. **Camera motion** — exactly **one** (slow push-in, handheld follow, static
   lock-off). Two moves in one clip is the most common way to break a shot.
6. **Lens / focus** — 35mm, shallow depth of field, rack focus to the hands.
7. **Style and light** — film stock / grade, key light direction, mood.

Sound is part of the prompt, not an afterthought:

- **Ambience and SFX**: name them (`distant traffic hum, rain on the window,
  the shutter clacks once`).
- **Dialogue**: put the line in quotes, say who speaks and how —
  `The man says in a low voice: "We are not done here."` One or two short lines
  is all that fits in 8 seconds.
- Add `(no subtitles)` when the shot must not carry burnt-in captions.

Hard rules:

- **Never write a negative inside the description** ("no cars", "without
  text"): Veo tends to render exactly what you name. Put unwanted elements at
  the very end as `Negative: cartoon, blurry, distorted hands, text, watermark`.
- **One clip = one scene = one camera move.** Cuts, "then", and "meanwhile"
  belong in separate jobs.
- With a start frame, do **not** re-describe what the picture already shows —
  write how it moves, what happens next, and how it sounds.
- Duration, aspect ratio and resolution are job fields (`selects`), never
  sentences in the prompt.
"""

# Kling 3.0 — https://docs.kie.ai/market/kling/kling-3-0.md と
# https://blog.fal.ai/kling-3-0-prompting-guide/（構成順・アイデンティティ固定・音声）

KLING_VIDEO_GUIDE = """\
# VIDEO PROMPT SPEC — Kling 3.0 (kie.ai, `kling3_video`)

Kling is a motion model: it is strongest on people, physical action and
photoreal footage, and it takes 3-15 second takes. Where this section and the
generic VIDEO PROMPT SPEC above disagree, this one wins.

**Hard limit: `video_prompt` must be at most 500 characters.** The job is
rejected before it is queued if it is longer, so write one dense English
paragraph — no lists, no repetition, no restating the job fields.

Write it in this order:

1. **Camera first** — open with the move (`Slow dolly push forward,`
   `Handheld follow behind her,` `Locked-off wide shot,`). Exactly **one**
   move per clip.
2. **Scene / subject** — pin the identity in the first clause (age, build,
   hair, wardrobe: `a woman in her 30s, short black bob, grey wool coat`).
   Refer back to her with the **same words** every time; pronouns and synonyms
   are the main cause of the character drifting mid-shot.
3. **Action** — one continuous action, in the order it happens.
4. **Mood / lighting** — time of day, key light, weather.
5. **Style** — film stock, grade, lens character.

With a start frame (`image`), the picture is the anchor: write **only what
changes** — how the subject starts moving, where the camera goes, what happens
next. Never re-describe the composition that is already in the picture.

With `sound: true` (the `sound` select):

- Label the speaker before the line and describe the voice:
  `Woman (raspy, low voice): "We are out of time."` Japanese dialogue is
  supported and lip-synced.
- Name the ambience and the effects you want (`rain on glass, distant sirens`).
- Keep it to one or two short lines — a 5 second take holds very little speech.

There is **no `negative_prompt`, no `cfg`, no seed and no camera-control
parameter** on this model. Everything is the prompt text: write what you *do*
want, and put the few things to suppress inline in the same sentence style
(`no text overlays, no camera shake`). Avoid words that summon what you are
trying to avoid.

Duration, mode (std / pro / 4K), aspect ratio and sound are job fields
(`selects`), never sentences in the prompt. Multi-shot prompts and Elements
(`@character` references) are not wired up yet — one shot per job.
"""

# Seedance 2 — https://docs.kie.ai/market/bytedance/seedance-2.md と
# 公式プロンプトガイドの解説（6 要素フォーミュラ・照明・カメラ 8 種・マルチショット）
# https://help.apiyi.com/en/seedance-2-0-prompt-guide-video-generation-camera-style-tips-en.html

SEEDANCE_VIDEO_GUIDE = """\
# VIDEO PROMPT SPEC — ByteDance Seedance 2 (kie.ai, `seedance2` / `seedance2_mini`)

Seedance generates picture and **native audio together** in one 4-15 second
take. Where this section and the generic VIDEO PROMPT SPEC above disagree, this
one wins.

**Write like a director, not like a tag list.** One dense English paragraph of
**60-100 words** built from six elements, in this order:

`[subject — concrete looks] + [action — verb + intensity] + [setting — light,
atmosphere] + camera [exactly one move] + [style] + avoid [what to exclude]`

1. **Subject** — age, build, hair, wardrobe, distinguishing details. Name them
   once and reuse the *same* words; synonyms and pronouns make the character
   drift.
2. **Action** — one continuous action with an intensity (`walks briskly`,
   `slumps slowly`), in the order it happens.
3. **Setting and light** — **the lighting sentence is the single biggest lever
   on quality**: name it concretely (`golden hour backlight`, `hard rim light
   from a neon sign`, `soft overcast window light`, `backlit silhouette`).
   Piles of vague adjectives (`amazing`, `epic`, a bare `cinematic`) make the
   result *worse* — cut them.
4. **Camera** — exactly **one** move out of push-in / pull-out / pan /
   tracking / orbit / aerial / handheld / fixed, with a rhythm word (`slow`,
   `smooth`, `gentle`). Two moves in one clip is the usual way to break a shot.
   **Keep the subject's motion and the camera's motion in separate sentences**
   — mixing them in one sentence is what produces sliding, warped movement.
5. **Style** — film stock, grade, lens character.
6. **Avoid** — a short closing clause. For anything with people, always include
   **`avoid jitter and bent limbs`**.

Multi-shot is what Seedance 2 is good at, but keep it deliberate: label the
shots in time order (`Shot 1: … Shot 2: …`) and write each one as **camera →
action → spatial relation → sound**, repeating the subject's description
verbatim in every shot so the character stays the same person.

With a start frame (`image`), the picture is the anchor: write only how it
starts moving and what happens next, never re-describe the composition. With
`end_image` as well, describe the *transition* that lands exactly on that last
frame.

There is **no seed, no `camera_fixed` and no negative-prompt parameter** on
Seedance 2: a locked-off camera is `fixed camera, no camera movement` in the
text, and everything unwanted goes in the closing `avoid …` clause.

Resolution, duration, aspect ratio and audio on/off are job fields (`selects`),
never sentences in the prompt. `generate_audio` is **on by default**, so name
the ambience and the effects you want; multimodal references (reference images
/ videos / audio) are not wired up yet.
"""

# Grok Imagine video-1.5 — https://docs.x.ai/developers/model-capabilities/video/generation
# と二次のプロンプトガイド（逐次レンダリング・1 クリップ 1 アクション・音声指定・
# カメラ語彙）https://github.com/thoxakihiko/grok-imagine-prompt-1.5-guide

GROK_IMAGINE_VIDEO_GUIDE = """\
# VIDEO PROMPT SPEC — Grok Imagine video-1.5 (Grok Build CLI, `grok_imagine_video`)

Grok Imagine makes one short take (1-10 seconds) with **native audio** —
ambience, effects and spoken lines come out of the same model. Where this
section and the generic VIDEO PROMPT SPEC above disagree, this one wins.

Write `video_prompt` as one short English paragraph (2-4 sentences) built from
five elements in this order: **subject → motion → camera → audio → duration
feel**.

1. **With a start frame (`image`), write only what CHANGES.** The picture
   already fixes composition, framing, wardrobe, lighting and style; restating
   them fights the image and drifts the look. Open with the motion instead
   (`She turns her head toward the window and smiles.`).
2. **The model renders sequentially**: whatever you write first is what the
   clip opens with. Put the action that must be seen at the very front, and
   keep it to **one clip, one action** — "then", "after that" and cuts belong
   in separate jobs.
3. **Camera**: use the concrete vocabulary — `locked static shot`,
   `slow push-in`, `camera drifts gently to the left`,
   `tracking shot alongside her`, `handheld tracking shot following him`,
   `slow pan out`. Exactly one move. Abstract words (`cinematic`, `epic`,
   `dynamic`) do nothing. **Saying nothing about the camera gives a static
   camera, which is the safest default** for a short take.
4. **Audio is part of the prompt.** Name the sounds (`footsteps on gravel`,
   `rain drumming on a tin roof`); a closing `Sound: ...` block works too.
   Vague labels (`city sounds`) come out muddy — give the material and the
   space instead (`traffic muffled through glass`). Spoken lines go in quotes,
   short, with the voice quality: `in a low, raspy voice: "We are done here."`
   Lip-sync needs a face toward the camera, the mouth inside the frame and a
   line short enough to fit the clip.
5. **Intensity comes from strong verbs plus adverbs**:
   `the wave crashes down with tremendous force` lands, `the wave crests` does
   not. Do not pile adjectives on the subject instead.

Duration, resolution and aspect ratio are job fields (`selects`), never
sentences in the prompt — and they are only *wishes* passed inside the CLI
instruction, so do not demand exact numbers in the text. There is no negative
prompt and no seed: write what you do want. Real people, celebrities and
trademarks are refused by moderation, and every take draws on the same daily
Grok subscription quota as the chat (roughly 10 videos a day), so plan large
batches over several days.
"""

#: workflow id -> そのモデル専用の VIDEO PROMPT SPEC（無いワークフローは
#: 汎用の :data:`VIDEO_SPEC` のまま）
VIDEO_SPECS: dict[str, str] = {
    "veo3_1_fast": VEO_VIDEO_GUIDE,
    "veo3_1_quality": VEO_VIDEO_GUIDE,
    "kling3_video": KLING_VIDEO_GUIDE,
    "seedance2": SEEDANCE_VIDEO_GUIDE,
    "seedance2_mini": SEEDANCE_VIDEO_GUIDE,
    "grok_imagine_video": GROK_IMAGINE_VIDEO_GUIDE,
}


def video_guide_for(workflow_id: str) -> str:
    """選んだ動画ワークフロー専用のガイド（無ければ空文字）。"""
    return VIDEO_SPECS.get(workflow_id, "")


def video_prompt_guides_section() -> str:
    """モデル固有のガイドをまとめた節（エージェントのプロンプト用）。

    :func:`app.workflows.video_catalog` から引くので、使えないバックエンドの
    ワークフロー（キー未設定の kie.ai など）のガイドは出ない。1 つも無ければ
    節ごと出さない。
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
        "\n`video_workflow` of the job you are writing; it overrides the generic"
        "\nVIDEO PROMPT SPEC)"
    )
    return "\n\n".join([header, *guides])


# --------------------------------------------------------------------------
# 3.7 audio prompt guides (agent mode only — the chat never writes audio jobs)
# --------------------------------------------------------------------------
# Summarized from each model's primary sources:
#
# * ACE-Step 1.5 — https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md
#   (caption dimensions, lyric structure tags, syllable / dynamics rules),
#   https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/GRADIO_GUIDE.md
#   (instrumental switch, auto language) and the README's "10 seconds to 10
#   minutes (600s)" length spec.  The COMBO value sets (keyscale, language,
#   timesignature) and the numeric bounds come from ComfyUI's own
#   comfy_extras/nodes_ace.py, mirrored in app.workflows.
#   NB: the node's `tags` input is inserted into the LLM prompt's `# Caption`
#   section verbatim (comfy/text_encoders/ace15.py), so it is a *caption*, not
#   a tag list — the official tutorial says either style works.
# * Stable Audio 3 —
#   https://github.com/Stability-AI/stable-audio-3/blob/main/docs/guides/prompting.md
#   (Music = genre / instruments / mood / BPM; SFX = source / action /
#   production; medium tops out at 6m20s = 380s) plus the per-category prompt
#   templates embedded in ComfyUI's own stable_audio_3_medium template
#   (docs.comfy.org/tutorials/audio/stable-audio/stable-audio-3), which is the
#   very text this workflow's Enable_Reprompt branch feeds to its local LLM.
#   Stability's guides document no negative prompt, and this template exposes
#   none, so never write one.

ACE_STEP_AUDIO_SPEC = """\
# AUDIO PROMPT SPEC — ACE-Step 1.5 XL (`ace_step1_5_xl_sft`)

This model writes **songs**: a full arrangement, with a singer when `lyrics`
are given and an instrumental when they are not.

`audio_prompt` is the track's **caption**. Comma-separated keywords and plain
prose both work (the model is explicitly agnostic there) — what matters is that
it is specific and that it agrees with the lyrics. Cover, roughly in this
order: style / genre, mood and atmosphere, the instruments and how each one
sounds, timbre and production style, tempo feel, and — when someone sings — the
voice (gender, register, delivery).

`lyrics` carries the words, with **capitalized structure tags** on their own
lines: `[Intro]` `[Verse]` / `[Verse 1]` `[Pre-Chorus]` `[Chorus]` `[Bridge]`
`[Outro]`, dynamics `[Build]` `[Drop]` `[Breakdown]`, instrumental sections
`[Instrumental]` `[Guitar Solo]` `[Piano Interlude]`, and `[Fade Out]`.
- One modifier per tag at most, joined with a single hyphen: `[Chorus - anthemic]`.
- 6-10 syllables per line, kept even inside a section; blank line between sections.
- CAPITALS raise the vocal intensity; backing vocals go in parentheses:
  `We rise together (together)`.
- **Instrumental**: leave the words out and write structure tags only, e.g.
  `[Instrumental]` / `[Main Theme - piano]` / `[Outro - fade out]`, and set
  `language` to `unknown`.

Other fields: `bpm` ({bpm_min}-{bpm_max}), `keyscale` as `"<root> <major|minor>"`
(`"C major"`, `"F# minor"` — never `"Am"`), `language` as an ISO code
(`en`, `ja`, `zh`, …) or `unknown`. `duration` is the track length in seconds;
the model is specified for 10-600s and is most reliable at 30-60s or 2-4min.

Example:
```
"audio_prompt": "dreamy city-pop ballad, female vocal with a breathy low register, warm Rhodes electric piano, fretless bass, brushed drums with a laid-back pocket, analog tape saturation, nostalgic and bittersweet, slow build into a wide chorus"
"lyrics": "[Verse 1]\\nThe last train hums below the rain\\nYour name still lights the window pane\\n\\n[Chorus - soaring]\\nWE BURN LIKE NEON IN THE DARK\\nStill holding on (holding on)"
```
"""

STABLE_AUDIO_SPEC = """\
# AUDIO PROMPT SPEC — Stable Audio 3 Medium (`stable_audio_3_medium_base`)

General-purpose audio: sound effects, one-shots, single-instrument stems and
**instrumental** music. It does not sing — a song with words needs ACE-Step.

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

# Suno V5 — https://docs.kie.ai/suno-api/generate-music（customMode / style /
# prompt = 歌詞 / negativeTags / vocalGender、1 リクエスト 2 曲）と
# https://openmusicprompt.com/blog/suno-ai-metatags-guide（メタタグの作法）。

SUNO_AUDIO_SPEC = """\
# AUDIO PROMPT SPEC — Suno V5 (`suno_v5`, external API)

The strongest option for **songs with real vocals**. Two fields do all the
work, and they must not be mixed up:

`audio_prompt` is the **style** — what the track *sounds* like, never what it
is about. English, comma separated, and only audible things, in this order:
genre, tempo feel / BPM, the main instruments, the vocal (gender, register,
delivery) and the production / mood. **120-300 characters** is the sweet spot;
past that the model starts averaging your ideas together. Do not name real
artists (the request is rejected as an imitation) and do not put the story,
the lyrics or anything you want *excluded* in here.

`lyrics` carries the words, with **structure tags on their own line right
before each section**: `[Intro]` `[Verse 1]` `[Pre-Chorus]` `[Chorus]`
`[Bridge]` `[Outro]` `[End]`. Performance tags (`[Build Up]`, `[Whispered]`,
`[Guitar Solo]`) work best at **1-3 words**; a final `[End]` is what reliably
makes the song stop instead of fading into filler.
- **Japanese lyrics just work**: write them in Japanese and they are sung in
  Japanese, while the tags and `audio_prompt` stay **English**
  (`J-pop, upbeat, female vocals, …`). Rewrite kanji the model is likely to
  misread in hiragana.
- **No lyrics == instrumental** — leave the field empty and the request is sent
  with `instrumental: true`.
- The **title is derived automatically** from the first sung line (or, for an
  instrumental, the head of the style), so never write one.

`negative_tags` is where everything you want kept *out* goes — comma separated
sounds, same vocabulary as the style (`heavy metal, screaming, distorted
guitar`). Writing exclusions into the style or the lyrics makes the model
produce them instead.

There is **no bpm, keyscale, language or length field** on this model: tempo
and key belong in the style text (`92 BPM, F# minor feel`), the language is
whatever the lyrics are written in, and the model decides the length. The
`selects` knobs are `model` (`V5` default / `V5_5` / `V4_5PLUS`) and
`vocal_gender` (`auto` / `m` / `f`, a probabilistic hint).
Every request comes back as **two takes** of the same song, and both are saved.
Contradictions break the take, so keep the mood words and the tempo consistent
("slow jazz" with "140 BPM" produces neither).

Example:
```
"audio_prompt": "dreamy Japanese city-pop, 92 BPM, breathy female vocal in a low register, warm Rhodes electric piano, fretless bass, brushed drums with a laid-back pocket, analog tape saturation, nostalgic and bittersweet"
"lyrics": "[Verse 1]\\nさいごの電車が 雨をぬけて\\n\\n[Chorus - soaring]\\nネオンのように もえている\\n\\n[End]"
"negative_tags": "distorted guitar, screaming, heavy drums"
```
"""

#: audio workflow id -> the AUDIO PROMPT SPEC section to embed for it
AUDIO_SPECS: dict[str, str] = {
    "ace_step1_5_xl_sft": ACE_STEP_AUDIO_SPEC,
    "stable_audio_3_medium_base": STABLE_AUDIO_SPEC,
    "suno_v5": SUNO_AUDIO_SPEC,
}

#: audio workflow id -> the one-line reminder in the AUDIO WORKFLOWS catalog
AUDIO_PROMPT_HINTS: dict[str, str] = {
    "ace_step1_5_xl_sft": (
        "A caption of the track (style, mood, instruments, production, voice);"
        " the words to sing go in `lyrics` with [Verse] / [Chorus] structure"
        " tags. No lyrics == instrumental."
    ),
    "stable_audio_3_medium_base": (
        "One dense sentence describing the sound itself, shaped by"
        " `audio_category`, ending in `BPM: X. Length: Y seconds` (music) or"
        " `Length: Y seconds` (SFX / one-shot). Never lyrics."
    ),
    "suno_v5": (
        "The *style* only: English, comma separated, audible things (genre,"
        " tempo, instruments, vocal, production), 120-300 characters. The words"
        " go in `lyrics` with [Verse] / [Chorus] tags (Japanese lyrics stay"
        " Japanese, tags stay English), and anything to keep out goes in"
        " `negative_tags`. No lyrics == instrumental."
    ),
}


def audio_spec_for(workflow_id: str) -> str:
    """The AUDIO PROMPT SPEC section of one audio workflow (formatted)."""
    template = AUDIO_SPECS.get(workflow_id, ACE_STEP_AUDIO_SPEC)
    return (
        template.replace("{bpm_min}", str(BPM_RANGE[0]))
        .replace("{bpm_max}", str(BPM_RANGE[1]))
        .replace("{categories}", " / ".join(AUDIO_CATEGORIES))
    )


def audio_prompt_guides_section() -> str:
    """Every audio workflow's prompt guide, for the agent system prompt.

    Driven by :func:`app.workflows.audio_catalog`, so a workflow added to
    ``workflow/audio/`` without a guide here falls back to the ACE-Step one
    rather than silently going missing.
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
        for name in ("lyrics", "bpm", "keyscale", "language", "negative_tags",
                     "audio_category", "reprompt")
        if name in entry.supports
    ]
    # 尺を宣言しないモデル（Suno は API に長さのパラメータが無い）は、
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


def audio_workflow_catalog_section() -> str:
    """The `audio_workflow` catalog embedded in the agent system prompt."""
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
    for entry in audio_catalog():
        lines += _audio_catalog_entry_lines(entry)
    lines += [
        "",
        f"`audio_workflow` を省略すると `{DEFAULT_AUDIO_WORKFLOW}` になります。",
        "歌詞のある曲は ACE-Step、効果音・環境音・単発の楽器音・インストの短尺は",
        "Stable Audio 3 が向いています。書き方は AUDIO PROMPT GUIDES を見ること。",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 4. few-shot examples (docs/prompt-samples.md)
# --------------------------------------------------------------------------

FEW_SHOT_VIDEO = """\
# FEW-SHOT EXAMPLES — video (real prompts from the model authors' own posts)

## Video prompts — LTX 2.3 (model authors' own posts)

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
   - tempo (BPM) and key, if the user has a preference
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
  `lyrics` are written in the language the song is sung in (the `language`
  field when the model has one, otherwise simply the language you write them
  in), so Japanese lyrics stay Japanese.
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
  "bpm": 92,
  "keyscale": "F# minor",
  "language": "ja",
  "negative_tags": "...",
  "notes": "..."
}
```

- `audio_prompt` is the only required field. Use JSON `null` for anything the
  selected workflow does not have (CONTEXT lists exactly which fields it reads)
  — sending a field it ignores is harmless but pointless.
- `lyrics` keeps its newlines and structure tags; every other string is one
  single line. For an instrumental, either omit the words entirely or send only
  structure tags such as `[Instrumental]`.
- `bpm` is a plain number (no quotes, no "BPM:" prefix), `keyscale` is
  `"<root> major"` / `"<root> minor"`, `language` an ISO code or `unknown`.
- `negative_tags` (Suno only) is a comma separated list of **sounds to keep
  out**, written like the style — never a sentence, and never the same words as
  `audio_prompt`. There is no title field: it is derived from the lyrics.
- `notes` is a short Japanese note for the user (what you assumed, what could
  be tweaked). It is never used as a model prompt.
- Hard limits: no lyrics about real, identifiable people in sexual contexts, no
  minors in sexual contexts, and no reproduction of copyrighted lyrics —
  write original words.
"""


def _audio_context_section(ctx: ChatSessionCreate) -> str:
    """CONTEXT of an audio session: the selected model and what it reads."""
    try:
        spec = get_audio_spec(ctx.audio_workflow)
    except WorkflowSpecError:
        spec = get_audio_spec(None)
    entry = catalog_entry(spec)

    knobs = {
        "lyrics": "`lyrics`（歌詞。構造タグ付き）",
        "bpm": f"`bpm`（{BPM_RANGE[0]}-{BPM_RANGE[1]}）",
        "keyscale": "`keyscale`（例 `C major` / `F# minor`）",
        "language": "`language`（ISO コード、または `unknown`）",
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
            # 長さのパラメータを持たないモデル（Suno）。フォームの秒数は
            # 使われないので、尺の話に引きずられないよう明示する。
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
    if not spec.supports("lyrics"):
        lines.append(
            "- このモデルは歌いません。`lyrics` は `null` にし、歌詞の相談もしない"
            "こと（歌モノが欲しいと言われたら、音声ワークフローを ACE-Step に"
            "変えるようフォームで案内する）。"
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

    Only the *selected* workflow's guide is embedded: ACE-Step wants a track
    caption plus structured lyrics and Stable Audio a single dense sentence, so
    shipping both would just invite the model to mix them.
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
- The prompt strings are one single line each — no newlines, no markdown.
- Hard limits: only adults; no real, identifiable people; no non-consent themes
  and no animals in sexual contexts.
"""


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def _mode_rules(mode: str, spec: WorkflowSpec | None = None) -> str:
    if mode == "i2v":
        # a video-only run: whether an image is even involved depends on the
        # selected workflow (t2v and the reference-sheet IC-LoRA take none).
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
        " These belong in `video_prompt` (the video LoRA is applied to the LTX"
        " graph, not to the image one).",
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


def _context_section(
    ctx: ChatSessionCreate, start_image_filename: str | None = None
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
        lines.append(f"Clip duration: {ctx.duration:g} seconds, one continuous shot.")
        lines += _workflow_context_lines(ctx.video_workflow)
        lines += _video_trigger_lines(ctx)
        lines.append("")
    if ctx.mode != "i2v":
        lines += _image_workflow_context_lines(ctx.image_workflow)
        lines.append("")
        lines += _trigger_lines(ctx)

    if ctx.mode == "i2v" and start_image_filename:
        lines.append(
            f"A start frame image named `{start_image_filename}` is in your "
            "current working directory. Open and look at it if you can, and "
            "make the video prompt match what it actually shows. If you cannot "
            "read it, just continue from the user's description without "
            "mentioning the file."
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
    return "\n".join(lines) + "\n"


def build_system_prompt(
    ctx: ChatSessionCreate, start_image_filename: str | None = None
) -> str:
    """Full system prompt for one chat session (SPEC §4.2 / §4.3).

    Only the *selected* image workflow's family guide is embedded — the models
    want incompatible prompt styles, so shipping all four would be worse than
    useless.  ``mode: "audio"`` is a different conversation altogether (no
    scene, no camera, no LoRA) and gets its own prompt.
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
        parts.append(
            VIDEO_SPEC.replace("DURATION_SECONDS", f"{ctx.duration:g}")
        )
        parts.append(
            TEMPLATE_TAGGED if ctx.prompt_template == "tagged" else TEMPLATE_NATURAL
        )
        parts.append(FEW_SHOT_VIDEO)
        # 選択中の動画ワークフローがモデル固有のガイドを持つなら、そのぶんだけ
        # 足す（両方のモデルの流儀を渡すと混ざるので選択中の 1 本きり）。
        try:
            guide = video_guide_for(get_video_spec(ctx.video_workflow).id)
        except WorkflowSpecError:
            guide = ""
        if guide:
            parts.append(guide)
    # The image examples are Krea 2 prose; the other families demand a different
    # style and carry their own examples in their spec section.
    if family == DEFAULT_FAMILY:
        parts.append(FEW_SHOT_IMAGE_KREA2)
    parts.append(_context_section(ctx, start_image_filename))
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
   each one as an EVENT message. React to it: inspect the video frames, rerun a
   miss with a new seed, continue a hit from its last frame, or declare done.
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
        "video_workflow": "ltx2_3_id_lora",
        "image_prompt": "...", "video_prompt": "...",
        "negative_prompt": "...",
        "aspect_ratio": "9:16", "megapixels": 1.0,
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
        "seed": null
      }
    },
    {
      "label": "② 主題歌デモ（音声のみ）",
      "job": {
        "mode": "audio",
        "audio_workflow": "ace_step1_5_xl_sft",
        "audio_prompt": "...", "lyrics": "[Verse 1]\\n...",
        "bpm": 92, "keyscale": "F# minor", "language": "ja",
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
| `inspect` | `job_id`, `interval` (seconds, default 1) | the app extracts frames with ffmpeg into your work dir; look at them next turn |
| `note` | `title`, `content` or `filename`, `kind` | register a memo as an artifact; `kind: "research"` for a web-search / research summary, `"note"` (default) for anything else |
| `rename` | `title`, plus `name` (artifact file name) **or** `job_id` (+ optional `kind`: `image` / `video` / `frame`) | rename an existing artifact so the panel shows a human title. No approval needed |
| `library` | `job_id`, `source` (`image` / `last_frame` / `video` / `audio`), optional `title`, optional `tags[]`, optional `category` (`character` / `background` / `prop` / `none`) | keep that output in the user's library so later jobs (and later sessions) can use it as an input. No approval needed |
| `library_search` | any of `q` (name / tag substring), `tag` (exact), `kind` (`image` / `video` / `audio`), `category` (`character` / `background` / `prop` / `none` = uncategorized), `offset` | search the **whole** library, not just what CHOICES lists; the result arrives next turn as an EVENT with the paths, categories and how many are left. No approval needed |
| `library_sheet` | `item_ids[]` (library ids, 1–8, **in the order they should be laid out**), optional `name`, optional `width` / `height` | compose those library images into one black-background reference sheet and keep it in the library; its id, path and URL arrive next turn as an EVENT. No approval needed |
| `checkin` | `question`, `options[]` | ask the user and wait for the answer |
| `done` | `summary` | the plan is finished; deliver the summary |

Rules:

- `job` uses the app's own job schema, exactly the fields shown above and
  nothing else. `mode` picks the stages (`full` = image then video, `i2v` =
  video only from assets you supply, `image_only` = a still and nothing else,
  `audio` = one audio track and nothing else), `image_workflow` picks the image
  graph, `video_workflow` the video one and `audio_workflow` the audio one.
  **Which fields are required follows from those** — the IMAGE WORKFLOWS,
  VIDEO WORKFLOWS and AUDIO WORKFLOWS sections list the exact set per workflow
  and per mode, so read them before you write a plan. Omit the inputs a
  workflow does not use.
- `mode: "audio"` is a **stand-alone** job — a separate task, never a stage of
  a `full` one, and it cannot supply or replace a video's soundtrack (LTX
  generates its own, and `audio_path` takes a *file* you already have). Such a
  job carries only `mode`, `audio_workflow`, `audio_prompt`, `duration`, `seed`,
  its workflow's own extra fields (`lyrics`, `bpm`, `keyscale`,
  `language`, `audio_category`, `reprompt`) and — if you switch a model —
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
  `end_image` for flf2v), otherwise the continuation is rejected.
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
- **Character sheets and IC-LoRA video** (the way to keep one character looking
  the same across shots) — work in this order:
  1. `library_search` with `category: "character"` (then `prop` / `background`)
     for material that already exists. Do not regenerate what the user curated.
  2. for what is missing, plan `mode: "image_only"` jobs — other angles or
     expressions of the character, a prop, the set — and keep each result with
     `library` + the matching `category`.
  3. `library_sheet` with those ids in layout order. `character` entries get the
     big panels and **the bigger a panel is, the more faithfully the model
     reproduces it**, so put the hero first and label it `character`; give
     `width` / `height` the aspect ratio of the video you are about to make.
  4. plan a `mode: "i2v"` job with `video_workflow: "ltx2_3_ic_lora_image"` and
     the sheet's path as `source_image`. The sheet is a look reference, **not** a
     first frame, so write `video_prompt` in the two parts that workflow expects:
     `Reference sheet:` + one short clause per panel in layout order, then
     `Generated video:` + the shot itself (subject, set, framing, motion, audio).
- `selects` carries the fixed-choice knobs a workflow declares (VIDEO WORKFLOWS
  lists them per workflow with their exact strings — `wan_dancer` picks its dance
  style, motion amplitude and length that way). Only write names that workflow
  declares, only the listed strings, and leave out what you do not need: an
  omitted knob falls back to its default, and one marked "省略すると入力から自動で
  決まる" is worked out from the job's own inputs (the clip length follows the
  audio file). Workflows without selectable knobs take no `selects` at all.
- `model_overrides` switches which model file a workflow loads for **this job
  only** (CHOICES の「使用モデルの切り替え」に、キーと候補が出ています). Leave it
  out to use the configured default; when you do set it, write only keys of the
  workflows this job runs and only values from that slot's candidate list —
  anything else is rejected. `continue` may carry it too.
- `aspect_ratio` is a preset for the image stage; when the job has a
  `source_image` the video follows that image's real aspect ratio instead (so it
  is never centre-cropped), and only `megapixels` still applies.
- LoRAs come in two kinds and are **not** interchangeable: 画像用 goes into
  `loras` (+ `trigger_text`, used by the image stage) and 動画用 into
  `video_loras` (+ `video_trigger_text`, used by the LTX video stage). Leave
  either list out when you do not need it, and never put video LoRAs in a
  `mode: "image_only"` job.
- Exactly one action per reply — `rename`, `library`, `library_search` and
  `library_sheet` count like `plan` / `checkin` here, so rename, keep, search or
  compose one thing per turn (the app renames every frame of a job at once when
  you target it by `job_id`).
- While you are only asking a question or reporting, send **no JSON at all**.
- EVENT messages in the transcript are written by the app, not by the user.
  `inspect_result` tells you which frame files are in your working directory —
  open them and judge the quality (broken hands, blur, framing, seed luck).
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
        f"- Generation budget: **{ctx.auto_limit}** generated jobs per stretch."
        " When the budget is used up the app itself asks the user whether to"
        f" keep going; approval buys another {ctx.auto_limit} jobs and the"
        " question comes back at the next milestone. Plan inside the budget"
        " and never try to work around it — you cannot approve the extension"
        " yourself.",
    ]
    # 1 プラン提案あたりの新規ジョブ数の上限は自走モードだけ。他のモードは
    # プラン承認とチェックインで必ず人間が挟まるので、プランの長さは自由。
    if ctx.checkin_mode == "auto":
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
                " spliced into the LTX 2.3 graph (`mode` must be `full` or"
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
                " compare the output (generated stills, `inspect` frames)"
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


def build_agent_system_prompt(
    ctx: AgentSessionCreate,
    options: Options,
    *,
    workdir: str = "",
    max_tasks: int = 5,
    tools_enabled: bool = False,
    lora_samples: dict[str, list[str]] | None = None,
    model_sources: list[ModelSource] | None = None,
) -> str:
    """System prompt of one agent session (AGENT-MODE §5.1)."""
    video_spec = VIDEO_SPEC.replace(
        "DURATION_SECONDS seconds", "as many seconds as the job's `duration` field says"
    )
    parts = [AGENT_ROLE, AGENT_PROTOCOL,
             image_workflow_catalog_section(), image_prompt_guides_section(),
             workflow_catalog_section(), video_prompt_guides_section(),
             audio_workflow_catalog_section(), audio_prompt_guides_section(),
             video_spec, TEMPLATE_NATURAL, FEW_SHOT_VIDEO, FEW_SHOT_IMAGE_KREA2,
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
    """Flatten the agent transcript into the single ``grok -p`` argument."""
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
