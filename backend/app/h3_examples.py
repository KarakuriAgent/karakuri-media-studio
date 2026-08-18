"""MiniMax H3 の実例集（few-shot）を構造化データとして持つ。

`prompts.py` に文字列として貼ってあった `FEW_SHOT_H3` を、モード・カテゴリで
選べるデータに開いたもの。**内蔵エージェント（`app.prompts`）と外部 API
（`app.drafting_guide` / `GET /api/v1/prompt-examples`）は、どちらもここの
:func:`select_examples` / :func:`default_examples_for_workflow` だけを通す**
（選び方が 2 か所に分かれると、片方だけ古い例を配るようになる）。

例は 2 段構え:

- ``tier="canonical"``: 公式 rewrite 形式の**完成例**。そのまま真似してよい。
  H3-E1〜E3 は元の `FEW_SHOT_H3` の 3 本（本文はそのまま）、H3-E4〜E10 は
  `docs/h3-prompt-guide-draft.md` 第4章の素材を公式形式へ書き起こしたもの。
- ``tier="inspiration"``: **rewrite 前の生入力**（公式ブログやコミュニティの
  原文）。何を書くかの発想源であって、出力の形は canonical に従う。

新しい例を足すときは:

1. :data:`EXAMPLES` に 1 件足す（``id`` は ``H3-E{n}`` の連番、``categories``
   は :data:`CATEGORIES` の語彙から選ぶ）。
2. canonical なら :data:`app.prompts.MINIMAX_H3_GUIDE_BODY` の規約に照らして
   自己点検する（``tests/test_h3_examples.py`` が機械的に見られる分だけ見る）。
3. 外部 API のガイド本文はカテゴリ一覧を**このデータから生成する**ので、
   語彙が増えたら :data:`app.drafting_guide.GUIDE_VERSION` を上げる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 例の書き方の段。``canonical`` = 公式形式の完成例、``inspiration`` = rewrite
#: 前の生入力（形は真似しない）。
Tier = str

#: 例が属する論理モード。``t2v`` / ``i2v`` / ``fl2v`` / ``l2v`` は base モード、
#: ``r2v`` は参照生成、``edit`` は参照モードの中の生成編集タスク。
MODES = ("t2v", "i2v", "fl2v", "l2v", "r2v", "edit")

#: カテゴリ語彙（題材の別。モードとは直交する）。
CATEGORIES = (
    "cinematic",
    "dialogue",
    "anime",
    "product",
    "action",
    "ui-text",
    "ugc",
    "horror",
    "multi-reference",
    "multilingual",
)


@dataclass(frozen=True)
class H3Example:
    """実例 1 件。"""

    #: ``H3-E1`` のような安定した id（見出しにもそのまま出る）
    id: str
    #: :data:`MODES` のいずれか
    mode: str
    #: :data:`CATEGORIES` から選んだ題材のタグ
    categories: tuple[str, ...]
    #: 見出しに出る一行説明（英語）
    summary: str
    #: ``canonical`` / ``inspiration``
    tier: Tier
    #: 例の本文（そのままコードフェンスに入る）
    body: str
    #: 出典。canonical の書き起こしは素材の出どころを書く
    source: str
    #: 補足（inspiration で「どこを変換するか」を一言添えるとき）
    note: str = ""
    #: 予備（将来の属性用。dataclass の順序都合でここに置く）
    extra: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# canonical — 公式 rewrite 形式の完成例
# --------------------------------------------------------------------------

_E1 = """\
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a tight medium shot frames the adult Japanese woman shown in <Picture 1> on a rumpled hotel bed with white sheets. She lies on her back under a man, knees bent, hips raised against his, fingers already gripping the sheet beside her hip. The camera holds a static shot and shakes slightly as the thrusting becomes rapid and intense — short hard strokes in quick succession. Each stroke drives her hips into the mattress; her legs tremble, fingers dig into the sheets, and her back arches off the bed. Her brows lock tight, watery eyes roll upward, mouth open wide. Heavy sweat beads on her flushed skin; messy dark hair sticks to her face and the pillow. Harsh practical bedroom lighting stays on her climaxing face and torso. Bed springs compress and rebound with each stroke; skin slaps at the contact; her breath breaks into shaky high moans between gasps.

overall_soundscape: Fast bed creaks keep time with the thrusts. Skin-on-skin impacts stay sharp and close. Gasping breaths and urgent moans continue through the continuous shot.

non_diegetic_music: N/A

No text, subtitles, logos or watermarks."""

_E2 = """\
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a late-night ramen stall under a canvas awning, steam rising from a steel pot toward a handwritten sign reading "営業中". A young woman in a dark apron stands behind the counter, wiping a ceramic bowl. The camera pushes in with small amplitude at slow speed as the woman with a quiet, even voice (S1) looks up at the arriving customer and says: <d>[Japanese] いらっしゃい</d> [Shot 2] At 00:05.000, the camera cuts to a close-up of chopsticks lifting noodles from the bowl while broth steam crosses the frame.

overall_soundscape: Rain ticks on the awning. Broth simmers; ceramic bowls clink; traffic passes on the wet street.

non_diegetic_music: Sparse electric-piano notes at a slow tempo, fading under the simmer.

No text, subtitles, logos or watermarks."""

_E3 = """\
subject_definitions:
<Subject 1> is the adult Japanese woman in <Picture 1>, with long dark hair, flushed skin, and the same face, body, and wardrobe shown there.

summary:
[reference generation] The target video shows <Subject 1> sitting up on a rumpled hotel bed and looking toward the camera.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity, face, hair, body, and wardrobe from <Picture 1> are retained.

detailed_description:
The target video is live-action and cinematic, lit by a single warm practical lamp that leaves the far wall in shadow.
[Shot 1] A medium shot frames <Subject 1> sitting on the edge of the rumpled hotel bed, knees together, hands on the mattress. The camera pushes in with small amplitude at slow speed as she lifts her chin, meets the lens, and holds the look. Sheets crease under her palms; her shoulders rise with one breath and settle.

overall_soundscape: Hotel-room tone and the rustle of sheets under her hands. A distant corridor door clicks once.

non_diegetic_music: N/A

No text, subtitles, logos or watermarks."""

_E4 = """\
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, two stage magicians stand facing the audience in the positions and framing established by Picture 1, the magician on the left in a black suit and the one on the right in a white suit, both wearing dark grey gloves. The camera pulls out with small amplitude at slow speed as they raise their wands together and sweep them down; a low bank of stage smoke rises and swallows both figures to the shoulders. The smoke thins from the top down and their suit colours have exchanged: the magician on the left now wears white, the one on the right now wears black. Their glove colours, hairstyles, wands, footing, and spacing do not change. They bow in unison and settle into the pose, spacing, and composition established by Picture 2 while the red stage curtain closes behind them and shifts gradually from deep red to dark blue.

overall_soundscape: A quiet theatre with a faint audience murmur. Smoke hisses from the stage vents, fabric rustles as the two men bow, and the heavy curtain drags along its track.

non_diegetic_music: A single sustained low string note at a slow tempo, joined by a soft cymbal swell that fades as the curtain closes.

No text, subtitles, logos or watermarks."""

_E5 = """\
How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the 6.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a close shot begins with an intact drinking glass standing near the edge of a dark wooden table, half full, while the same hand and dark sleeve visible in <Picture 1> reach in from the right. The camera pushes in with small amplitude at slow speed as the fingertips strike the rim. The glass tips, rolls off the edge, and hits the floor; the base shatters first, cracks race up the wall of the glass, and fragments slide outward across the boards while the spilled water spreads behind them. Toward the end the moving pieces lose momentum and settle into the exact broken arrangement, hand position, camera angle, lighting, and final composition established by <Picture 1>.

overall_soundscape: Quiet room tone with a low refrigerator hum. A fingertip taps the glass, the glass scrapes over the table edge, then breaks with one sharp crash followed by small fragments skittering and coming to rest.

non_diegetic_music: A low electronic pulse at a slow tempo that stops immediately on the impact.

No text, subtitles, logos or watermarks."""

_E6 = """\
subject_definitions:
<Video 1> is the source video for the target video edit: a handheld night walk out of a convenience store, one young woman carrying a plastic bag toward the camera.
<Subject 1> is the young woman in <Video 1>, with a beige coat, shoulder-length dark hair, and the plastic bag in her right hand.
<Subject 2> is the illuminated convenience-store sign behind her in <Video 1>, a horizontal light box above the entrance.
<Audio 1> is the soundtrack of <Video 1>: street ambience, footsteps, the plastic bag, and the woman's single spoken line.

summary:
[video editing + audio reuse] The target video is an edited version of <Video 1>. <Subject 1> walks out of the store as before, but the canned drink in her hand becomes a Coca-Cola can, the sign of <Subject 2> reads "HUHUI" instead of "FamilyMart", and the snacks in her bag are replaced with Coca-Cola cans as she delivers a new closing line.

retention_analysis:
<Video 1> (source video being edited): partially_preserved - framing, handheld camera path, cut timing, and the length of the take are retained; the drink, the sign lettering, the contents of the bag, and the spoken line are changed.
<Subject 1> (appears in [Shot 1]): fully_preserved - face, hair, beige coat, gait, and hand positions are retained.
<Subject 2> (appears in [Shot 1]): partially_preserved - the light box, its mounting, brightness, and glow on the pavement are retained; only the lettering is replaced.
<Audio 1>: partially_copy - street ambience, footsteps, and bag rustle are reused; the woman's line is replaced with the new dialogue.

detailed_description:
The target video keeps the live-action, handheld night-street look of <Video 1>, lit by the store front and a wet pavement reflection.
[Shot 1] A medium shot follows <Subject 1> stepping out of the automatic door with a chilled can in her left hand; the can is a red Coca-Cola can, condensation beading on the aluminium, the logo turning into the light as she lowers it. Behind her the light box of <Subject 2> reads "HUHUI" in the same typeface weight and brightness as the original sign, its glow unchanged on the pavement. The camera trucks backward at normal speed, holding her at the same size in frame. She lifts the plastic bag toward the lens and the packets inside are Coca-Cola cans, their tops catching the sign light through the translucent bag. <Subject 1> (S1), the young woman with a bright, slightly hoarse voice, says: <d>[Chinese] 我买了一大堆可乐。</d> She closes her lips, swings the bag down, and walks past the camera.

overall_soundscape:
The copied ambience layer from <Audio 1> continues throughout the target video: a quiet night street, the store's automatic door, her footsteps on wet pavement, and the plastic bag creaking, with the cans now clinking inside it.

non_diegetic_music:
N/A"""

_E7 = """\
subject_definitions:
<Video 1> is the camera-movement reference: a single Hitchcock dolly-zoom move around a standing singer on a small stage.
<Subject 1> is the young man in <Picture 1>, with a shaved head, a black leather jacket, and a silver ring on his left hand.
<Audio 1> is the soundtrack of <Video 1> and is not used in the target video.
<Audio 2> is the vocal-timbre reference for <Subject 1> (S1), containing a sung English vocal layer.

summary:
[reference generation + audio reference] The target video shows <Subject 1> singing alone on a small stage while the camera repeats the dolly-zoom move of <Video 1>, with his voice following the timbre of <Audio 2>.

retention_analysis:
<Video 1> (camera movement and pacing structure): weak_reference - only the dolly-zoom trajectory, its timing, and its framing rhythm are followed; the location, subject, and lighting are not reused.
<Subject 1> (appears in [Shot 1]): fully_preserved - face, shaved head, black leather jacket, and silver ring from <Picture 1> are retained.
<Audio 1>: weak_reference - not audible in the target video.
<Audio 2>: reference - only the vocal timbre and phrasing guide the singing; the signal is not copied.

detailed_description:
The target video is live-action and cinematic, lit by one warm key light from stage left with deep shadow behind.
[Shot 1] A medium shot frames <Subject 1> standing at a chrome microphone stand, both hands on the stand, shoulders squared to the lens. The camera performs a dolly-zoom: it pulls out with large amplitude at slow speed while the lens pushes the background walls inward, so <Subject 1> stays the same size in frame as the room stretches behind him. Using the warm, slightly hoarse mid-range timbre referenced from <Audio 2>, <Subject 1> (S1) sings: <d>[English] Hold the line for me tonight.</d> His jaw stays open on the last word and he leans into the microphone as the move settles.

overall_soundscape:
A small room with a live, slightly reverberant stage tone. The microphone stand creaks once and his jacket rustles as he leans in.

non_diegetic_music:
A sparse electric-guitar figure at a slow tempo with a single sustained bass note underneath, holding an even level throughout.

No text, subtitles, logos or watermarks."""

_E8 = """\
integrated_multimodal_description: [Shot 1] Live-action, cinematic, 1990s Korean noir with practical neon, rain haze, and heavy film grain. A medium-wide shot frames a Korean woman in her late twenties, soaked trench coat, hair flattened by the rain, pushing open the steel door of an abandoned underground nightclub. The camera trucks left with small amplitude at slow speed as she steps down into the room, and the woman with a low, unsteady voice (S1) calls into the dark: <d>[Korean] 언니... 여기 있어?</d> [Shot 2] At 00:03.000, the shot cuts to a close-up of a lighter striking in the shadows; the flame lifts across a scarred man in his forties and stops under his eye. The man with a gravelled, unhurried voice (S2) says: <d>[Korean] 오랜만이네.</d> [Shot 3] At 00:06.000, the shot cuts to a close-up of the woman going still, water running off her jaw, as she (S1) answers: <d>[Korean] 당신... 죽은 줄 알았는데.</d> [Shot 4] At 00:09.000, the shot cuts to a medium shot of the man, and the camera pushes in with small amplitude at slow speed as he draws on the cigarette and (S2) says: <d>[Korean] 네 언니가 날 죽였다고 생각했겠지.</d> [Shot 5] At 00:12.500, the shot cuts to a tight two-shot of their locked eyes in profile; thunder cracks outside, the neon tube behind them stutters, and the room falls to black.

overall_soundscape: Heavy rain on the stairwell and a steady drip inside the club. A lighter wheel scrapes and catches; a cigarette crackles; distant thunder rolls twice and the room tone drops to near silence under each line.

non_diegetic_music: A low upright-bass figure at a slow tempo under a bed of vinyl crackle, thinning to a single sustained note before the final cut.

No text, subtitles, logos or watermarks."""

_E9 = """\
integrated_multimodal_description: [Shot 1] Claymation, thumbprints and tool marks visible in the fur, shot with cinematic depth. A wide low-angle shot frames a clay fox sprinting along a cracked ridge toward the lip of a cliff over an immense lava canyon; heat shimmer bends the far wall. The camera trucks right with large amplitude at fast speed alongside the fox, then the fox launches without hesitation and the action drops into slow motion, its clay body fully extended, tail streaming, forepaws reaching. Mid-air the camera dives beneath its belly with large amplitude at fast speed and tilts up, revealing the depth of the chasm and the molten seams glowing far below. Orange light rakes across the underside of the fox as it crosses the frame and the far ledge enters the shot.

overall_soundscape: Wind roars past the cliff edge and gravel skitters loose under the fox's paws. Lava churns far below with a deep, slow rumble, and the takeoff lands as one soft clay-on-clay scuff.

non_diegetic_music: Low taiko-like drums at a slow tempo with a rising brass swell that holds through the leap and drops to a single low note.

No text, subtitles, logos or watermarks."""

_E10 = """\
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 6.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 2D-animated otome visual-novel interface, soft painterly backstage art with a warm rim light, exactly as established by Picture 1: the heroine's point of view on a dressing-room door, a dialogue box across the lower third reading "Choose to watch his performance", and two choice buttons stacked at the right. The camera holds a static shot as the cursor slides to the upper choice, the button brightens, and the panel slides out to the right while a soft light bloom crosses the frame. The dialogue box empties and refills line by line with "Han Xu turns toward you, intrigued.", the name plate above it reading "Han Xu". The male lead turns his head into the light, one eyebrow lifting, and the layout settles into the framing, panel positions, and composition established by Picture 2.

overall_soundscape: A quiet backstage room tone with muffled applause behind the wall. Interface sounds stay small: a soft click on the choice, a light sweep as the panel slides, and a per-character tick as the new line fills in.

non_diegetic_music: A solo celesta figure at a moderate tempo over sustained strings, dropping to a single held note when the new line finishes."""


# --------------------------------------------------------------------------
# inspiration — rewrite 前の生入力（形は真似しない）
# --------------------------------------------------------------------------

_BLOG = "https://www.minimax.io/blog/minimax-h3"
_ECOM = "https://github.com/ecomimagelab/awesome-minimax-h3-prompts"
_FAL = "https://fal.ai/learn/devs/minimax-h3-prompting-guide"
_DOCS = "https://platform.minimax.io/docs/guides/video-generation"

_I01 = "A tiktok dancer is dancing on a drone, doing flips and tricks."

_I02 = """\
15 seconds, 16:9 landscape. Combine a live-action late-night laundromat with hand-drawn luminous animation. The small self-service laundromat has gently flickering fluorescent lights, running washers, plastic baskets, a worn bench, and one sock on the floor. Keep the space quiet and faintly nostalgic.

Use a one-handed phone-camera feel with visible shake, exposure fluctuation under white fluorescent light, environmental reflections in glass, and delayed autofocus at close range. Avoid polished commercial composition; it should feel like an authentic late-night encounter, filmed while following a strange apparition."""

_I03 = """\
16:9, 15s, hyper-realistic Korean noir crime teaser.

A rain-soaked Korean woman (late 20s, trench coat) enters an abandoned underground nightclub searching for her missing sister. A scarred crime boss (40s), presumed dead for years, emerges from the shadows with a lit cigarette. Atmosphere: 90s Korean noir, practical neon, rain, smoke, film grain, tactile realism, no CGI gloss.

Shots:

1. She enters: "언니... 여기 있어?"
2. Lighter ignites, revealing his scar. "오랜만이네."
3. She freezes: "당신... 죽은 줄 알았는데."
4. Slow push-in. He smiles: "네 언니가 날 죽였다고 생각했겠지."
5. They lock eyes. Thunder. Cut to black.

Audio: Rain, thunder, jazz crackle, lighter click, intimate silence during dialogue."""

_I04 = "A little girl grows up."

_I05 = """\
Epic theatrical space-opera teaser

Keep the pace fast and the scale enormous without letting the edit drag. Use sharp hard cuts, a shaking command deck, white-hot flashes, split-second black frames, and a violent jump-to-warp impact. Title cards should use wide-tracked cinematic typography—not pure white—with restrained material texture, subtle illumination, and a faint edge glow. Animate the titles by emerging from deep-space shadow, catching a sweep of starlight, opening their letter spacing, leaving a slight afterimage, and flashing briefly against black."""

_I06 = """\
Claymation. A fox sprints to the edge of a cliff and launches without hesitation, making a dramatic heroic leap in slow motion over an immense lava canyon. Midair, the camera races beneath the fox's belly in a bold dynamic move, revealing the terrifying depth of the chasm and the fully extended motion of its clay body."""

_I07 = """\
Two magicians stand onstage facing the audience and perform a "swap" illusion. They wave their wands simultaneously and smoke rises. When it clears, their suit colors have exchanged: the magician on the left now wears white, and the one on the right now wears black. Their glove colors do not change. They bow; the red curtain closes behind them and gradually shifts from deep red to dark blue."""

_I08 = """\
Interactive Otome Game

Use the first image as the exact opening frame and the second as the exact ending frame. Create a transition within a premium Chinese otome visual-novel interface, capturing an intimate backstage moment before and after a performance. Move naturally from "Choose to watch his performance" to "Han Xu reacts with intrigued interest after hearing the heroine." Reveal UI copy, choices, and dialogue boxes with refined otome-game motion design. Keep transitions fluid and the romantic tension suggestive but restrained."""

_I09 = """\
Animate the website UI: the top headline slides down into place, the copy panel below slides up, and the car's lights shift from dark to red."""

_I10 = """\
Reference the Hitchcock camera movement from Video 1, have the character in Image 2 sing, with the vocals matching Audio 3."""

_I11 = """\
Use Video 1 as the motion reference for a street-dance performance. Use Images 1 and 2 as the character references."""

_I12 = """\
Use Image 2 as the locked character reference. Preserve the half-up long black hair, openwork silver crown, indigo ribbon, layered pale hanfu, translucent blue outer robe, deep-blue sash, silver floral fastener, and long tassels. Use Image 1 for storyboard order and pacing.

Render in high-quality 4K, 16:9 Chinese-inspired 3D with cinematic xianxia production value: intense, solemn, and shaped by destiny. Follow the storyboard beat by beat, with natural camera movement and seamless transitions—never a slideshow. Show the face only in close-up or extreme close-up. In wide shots, use back view, rear three-quarter view, or empty environment shots; never show a distant frontal face."""

_I13 = """\
In the reference video: replace the newspaper with a green hardcover book; replace the chair with a red sofa; remove the subject's sunglasses and reveal a clear face; remove the burning-car effect and restore the vehicle to normal; replace the photograph taken from the coat with a small black notebook; and add a tree on the left side of frame."""

_I14 = """\
Precise Subject and Wardrobe Replacement

Replace the child at the back of Video 1 with the golden retriever from Image 1. Replace the khaki jacket worn by the child on the far left with the denim jacket from Image 2."""

_I15 = """\
Motion reference for a DIY reaction clip

Match the action in Video 1 from a locked-off wide camera. Replace the three suited men with three highly photoreal capybaras. Preserve the original movement path exactly: all three drop quickly to the floor; the left capybara jumps to center; the center capybara rolls to the far left; the new center capybara rolls to the far right; the right capybara jumps to center; finally, the center capybara jumps onto the other two, forming a pyramid. Keep the camera fixed and integrate fur, lighting, and shadows realistically into the scene."""


#: 全実例。並び順が id の連番であることを前提にしている箇所は無いが、
#: 出力の見た目は宣言順（canonical → inspiration）になる。
EXAMPLES: tuple[H3Example, ...] = (
    H3Example(
        id="H3-E1",
        mode="i2v",
        categories=("cinematic",),
        summary="I2VA — hotel-bed i2v (this app's scene; no invented dialogue)",
        tier="canonical",
        body=_E1,
        source="this app's own reference prompt",
    ),
    H3Example(
        id="H3-E2",
        mode="t2v",
        categories=("cinematic", "dialogue"),
        summary="T2VA — two shots, 8s (ramen stall; one user-given line)",
        tier="canonical",
        body=_E2,
        source="this app's own reference prompt",
    ),
    H3Example(
        id="H3-E3",
        mode="r2v",
        categories=("cinematic",),
        summary=(
            "Ref2VA — six sections (one picture as identity; no standalone"
            " Picture line)"
        ),
        tier="canonical",
        body=_E3,
        source="this app's own reference prompt",
    ),
    H3Example(
        id="H3-E4",
        mode="fl2v",
        categories=("cinematic", "action"),
        summary=(
            "FL2VA — one shot, 8s (stage suit swap; what changes and what must"
            " not)"
        ),
        tier="canonical",
        body=_E4,
        source=(
            "written for this app from the official blog example"
            f" `h3-0054` ({_BLOG}), in the official rewrite format"
        ),
    ),
    H3Example(
        id="H3-E5",
        mode="l2v",
        categories=("cinematic", "action"),
        summary="L2VA — one shot, 6s (the last frame is the broken glass)",
        tier="canonical",
        body=_E5,
        source=(
            "written for this app from Case 4 of the official base guide"
            " (VIDEO_PROMPT_WRITING_GUIDE_base_en.md)"
        ),
    ),
    H3Example(
        id="H3-E6",
        mode="edit",
        categories=("product", "dialogue", "ui-text", "multilingual"),
        summary=(
            "Ref2VA / video editing — product, sign text and the spoken line"
            " replaced in one source video"
        ),
        tier="canonical",
        body=_E6,
        source=(
            "written for this app from the official blog example"
            f" `h3-0053` ({_BLOG}), in the official rewrite format"
        ),
    ),
    H3Example(
        id="H3-E7",
        mode="r2v",
        categories=("multi-reference", "dialogue"),
        summary=(
            "Ref2VA — picture + video + audio, one job each (and this app's tag"
            " numbering)"
        ),
        tier="canonical",
        body=_E7,
        source=(
            "written for this app from the official blog example"
            f" `h3-0005` ({_BLOG}), renumbered for this app's tag rules"
        ),
    ),
    H3Example(
        id="H3-E8",
        mode="t2v",
        categories=("dialogue", "multilingual", "cinematic", "horror"),
        summary="T2VA — five shots, 15s, two speakers, Korean dialogue",
        tier="canonical",
        body=_E8,
        source=(
            "written for this app from the community example `iv-03`"
            " (https://github.com/imagineVid/Awesome-minimax-h3-prompts-and-skills)"
        ),
    ),
    H3Example(
        id="H3-E9",
        mode="t2v",
        categories=("anime", "action"),
        summary="T2VA — one shot, non-live-action style (claymation)",
        tier="canonical",
        body=_E9,
        source=(
            "written for this app from the official blog example"
            f" `h3-0035` ({_BLOG}), in the official rewrite format"
        ),
    ),
    H3Example(
        id="H3-E10",
        mode="fl2v",
        categories=("ui-text", "anime"),
        summary=(
            "FL2VA — on-screen UI text (quoted verbatim; no closing"
            " no-text sentence)"
        ),
        tier="canonical",
        body=_E10,
        source=(
            "written for this app from the official blog example"
            f" `h3-0039` ({_BLOG}), in the official rewrite format"
        ),
    ),
    # ----------------------------------------------------------------------
    H3Example(
        id="H3-I1",
        mode="t2v",
        categories=("action",),
        summary="raw T2VA input — one line is enough for a simple action",
        tier="inspiration",
        body=_I01,
        source=f"MiniMax official API docs ({_DOCS}) / {_ECOM} `h3-0001`",
        note="Counter-example against over-inflating a one-line request.",
    ),
    H3Example(
        id="H3-I2",
        mode="t2v",
        categories=("cinematic", "anime", "ugc"),
        summary="raw T2VA input — deliberate style mix plus a phone-camera look",
        tier="inspiration",
        body=_I02,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0025`",
        note="Duration and aspect ratio move to job fields on rewrite.",
    ),
    H3Example(
        id="H3-I3",
        mode="t2v",
        categories=("dialogue", "multilingual", "cinematic", "horror"),
        summary="raw T2VA input — numbered shot list with quoted Korean lines",
        tier="inspiration",
        body=_I03,
        source=(
            "community, WasifAI via imagineVid `iv-03`"
            " (https://github.com/imagineVid/Awesome-minimax-h3-prompts-and-skills)"
        ),
        note="Rewritten as H3-E8; compare the two to see the conversion.",
    ),
    H3Example(
        id="H3-I4",
        mode="fl2v",
        categories=("cinematic",),
        summary="raw FL2VA input — the path between two frames, in four words",
        tier="inspiration",
        body=_I04,
        source=f"MiniMax official API docs ({_DOCS}) / {_ECOM} `h3-0003`",
    ),
    H3Example(
        id="H3-I5",
        mode="fl2v",
        categories=("ui-text", "cinematic"),
        summary="raw FL2VA input — typography-led teaser (material, glow, tracking)",
        tier="inspiration",
        body=_I05,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0022`",
        note="On-screen typography is the subject, so no no-text closing line.",
    ),
    H3Example(
        id="H3-I6",
        mode="fl2v",
        categories=("anime", "action"),
        summary="raw FL2VA input — claymation leap with one bold camera move",
        tier="inspiration",
        body=_I06,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0035`",
        note="Rewritten as H3-E9.",
    ),
    H3Example(
        id="H3-I7",
        mode="fl2v",
        categories=("cinematic", "action"),
        summary="raw FL2VA input — what changes stated next to what must not",
        tier="inspiration",
        body=_I07,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0054`",
        note="Rewritten as H3-E4.",
    ),
    H3Example(
        id="H3-I8",
        mode="fl2v",
        categories=("ui-text", "anime"),
        summary="raw FL2VA input — visual-novel UI transition between two frames",
        tier="inspiration",
        body=_I08,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0039`",
        note=(
            "The 'use the first image as the opening frame' sentence becomes"
            " the official alignment line. Rewritten as H3-E10."
        ),
    ),
    H3Example(
        id="H3-I9",
        mode="fl2v",
        categories=("ui-text", "product"),
        summary="raw FL2VA input — minimal web-UI animation",
        tier="inspiration",
        body=_I09,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0033`",
    ),
    H3Example(
        id="H3-I10",
        mode="r2v",
        categories=("multi-reference",),
        summary="raw Ref2VA input — one job per reference (video / image / audio)",
        tier="inspiration",
        body=_I10,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0005`",
        note=(
            "Rewritten as H3-E7; the tag numbers change under this app's"
            " numbering rules."
        ),
    ),
    H3Example(
        id="H3-I11",
        mode="r2v",
        categories=("multi-reference", "action"),
        summary="raw Ref2VA input — motion from the video, identity from the images",
        tier="inspiration",
        body=_I11,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0043`",
        note=(
            "A video that supplies only motion stays `reference generation`,"
            " not `video editing`."
        ),
    ),
    H3Example(
        id="H3-I12",
        mode="r2v",
        categories=("multi-reference", "anime"),
        summary="raw Ref2VA input — storyboard image plus a locked character",
        tier="inspiration",
        body=_I12,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0036`",
        note=(
            "A storyboard image earns a standalone `<Picture N>` line; the"
            " `4K` wording does not survive the rewrite."
        ),
    ),
    H3Example(
        id="H3-I13",
        mode="edit",
        categories=("cinematic",),
        summary="raw editing input — six edits in one semicolon list",
        tier="inspiration",
        body=_I13,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0052`",
        note="Each edited element needs its own `retention_analysis` line.",
    ),
    H3Example(
        id="H3-I14",
        mode="edit",
        categories=("multi-reference",),
        summary="raw editing input — targets picked out by their position in frame",
        tier="inspiration",
        body=_I14,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0047`",
        note="Swapping a subject for an image's subject is `attribute_transfer`.",
    ),
    H3Example(
        id="H3-I15",
        mode="edit",
        categories=("action", "multi-reference"),
        summary="raw editing input — the whole movement path written out in words",
        tier="inspiration",
        body=_I15,
        source=f"MiniMax official blog ({_BLOG}) / {_ECOM} `h3-0044`",
    ),
    H3Example(
        id="H3-I16",
        mode="r2v",
        categories=("ui-text", "action"),
        summary="raw Ref2VA input — timecoded UI / HUD sequence (community notation)",
        tier="inspiration",
        body=(
            "Use Image 1 for the character and Image 2 for the UI style.\n\n"
            "[0–2 seconds] High-angle overhead shot. The character sits on a"
            " vivid, highly saturated purple floor, looks up at camera, and"
            " matches Image 1. A game menu appears on the right: START NEW"
            " GAME, CONTINUE (highlighted), SETTINGS, EXIT GAME. Player profile"
            " MINIMAX appears top left. The cursor selects CONTINUE.\n\n"
            "[2–4 seconds] Smoothly push in to her right arm. A RIGHT ARM"
            " EQUIPMENT panel slides in from the right. PHANTOM GRIP is"
            " selected, then the selection moves to CHRONOS CLAW. Her"
            " mechanical hand reconfigures: fingers separate, new claw-like"
            " joints lock into place, and cyan LEDs flare brighter.\n\n"
            "[4–7 seconds] Arc smoothly to her left. An ARMAMENT CUSTOMIZATION"
            " grid slides in. The selector cycles rapidly and her left arm"
            " disassembles section by section, with exposed wiring and pistons"
            " visible during the change."
        ),
        source=f"community, Bennett Heyn / fal ({_FAL}) / {_ECOM} `h3-0019`",
        note=(
            "`[0–2 seconds]` stamps are not official notation: continuous"
            " camera movement stays inside one `[Shot 1]`, and on-screen"
            " strings go in double quotes."
        ),
    ),
)

#: id 引き
BY_ID: dict[str, H3Example] = {example.id: example for example in EXAMPLES}


def _matches(
    example: H3Example, mode: str | None, category: str | None, tier: str | None
) -> bool:
    if mode and example.mode != mode:
        return False
    if category and category not in example.categories:
        return False
    if tier and example.tier != tier:
        return False
    return True


def select_examples(
    mode: str | None = None,
    category: str | None = None,
    tier: str | None = "canonical",
    limit: int | None = None,
    ids: tuple[str, ...] | list[str] | None = None,
) -> list[H3Example]:
    """条件に合う例を宣言順で返す（内蔵・外部で共有する唯一の選択ロジック）。

    ``tier`` の既定は ``"canonical"``（完成例だけ）。``None`` を渡すと生入力の
    例も混ざる。``ids`` を渡したときは**その並び順**で返し、他の条件は無視する
    （id 指定は「この例をくれ」という直接の要求）。
    """
    if ids is not None:
        return [BY_ID[key] for key in ids if key in BY_ID]
    picked = [
        example
        for example in EXAMPLES
        if _matches(example, mode, category, tier)
    ]
    if limit is not None and limit >= 0:
        picked = picked[:limit]
    return picked


#: ワークフロー名の接頭辞 → 既定で埋め込む例の id（前から順に 1〜2 本）。
#: 編集系は参照モードの中の生成編集タスクなので、`edit` を先に見る。
_WORKFLOW_DEFAULTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("edit", ("H3-E6", "H3-E3")),
    ("minimax_h3_t2v", ("H3-E2", "H3-E8")),
    ("minimax_h3_i2v", ("H3-E1", "H3-E4")),
    ("minimax_h3_r2v", ("H3-E3", "H3-E7")),
)

#: ワークフローが分からない経路で使う代表例（従来の `FEW_SHOT_H3` と同じ 3 本）
DEFAULT_IDS = ("H3-E1", "H3-E2", "H3-E3")


def default_examples_for_workflow(workflow_name: str) -> list[H3Example]:
    """ワークフロー名から既定で埋め込む例を選ぶ。

    ``minimax_h3_i2v_turbo`` のような版違い（``_save`` / ``_context`` /
    ``_turbo`` / ``_opt``）は接頭辞で同じ扱いになる。対応が無い名前（H3 以外の
    動画ワークフローや空文字）は :data:`DEFAULT_IDS` の代表 3 本に落ちる。
    """
    name = (workflow_name or "").strip().lower()
    for marker, ids in _WORKFLOW_DEFAULTS:
        if marker == "edit":
            if "edit" in name:
                return select_examples(ids=ids)
        elif name.startswith(marker):
            return select_examples(ids=ids)
    return select_examples(ids=DEFAULT_IDS)


_CANONICAL_HEADER = """\
# FEW-SHOT EXAMPLES — MiniMax H3 (official rewrite format)

Imitate these complete official documents. Do not fall back to one-paragraph
legacy examples.\
"""

#: 例を取りに行けるところ（内蔵エージェント）でだけ足す 1 文。
MORE_EXAMPLES_HINT = """\
When the piece you are writing does not match the genre of the examples above
(2D/3D animation, product spots, on-screen UI text, generative editing, UGC …),
run a `get_prompt_examples` action and read the matching example instead of
guessing the format.\
"""

_INSPIRATION_HEADER = """\
# RAW PROMPT INPUTS — MiniMax H3 (before the rewrite)

These are **inputs**, not outputs: they show what people ask for and how they
phrase it. Whatever you take from them, deliver it in the official rewrite
format shown by the canonical examples.\
"""


def _section(example: H3Example) -> str:
    tags = ", ".join(example.categories)
    head = f"## {example.id} {example.summary} [tags: {tags}]"
    note = f"\n{example.note}\n" if example.note else ""
    return f"{head}\n{note}\n```\n{example.body}\n```"


def render_examples(examples: list[H3Example], header: bool = True) -> str:
    """例を few-shot ブロック（Markdown）に組み立てる。

    canonical と inspiration が混ざっていたら段ごとに見出しを分ける（生入力を
    完成例と同じ棚に置かない）。``header=False`` なら節だけを返す（別の見出しの
    下にぶら下げる外部ガイド用）。
    """
    canonical = [item for item in examples if item.tier == "canonical"]
    inspiration = [item for item in examples if item.tier != "canonical"]
    blocks: list[str] = []
    for head, group in (
        (_CANONICAL_HEADER, canonical),
        (_INSPIRATION_HEADER, inspiration),
    ):
        if not group:
            continue
        parts = [head] if header else []
        parts += [_section(example) for example in group]
        blocks.append("\n\n".join(parts))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def example_index(examples: list[H3Example] | None = None) -> list[dict[str, object]]:
    """本文抜きの索引（``id`` / ``mode`` / ``categories`` / ``summary``）。"""
    return [
        {
            "id": example.id,
            "mode": example.mode,
            "categories": list(example.categories),
            "summary": example.summary,
            "tier": example.tier,
        }
        for example in (EXAMPLES if examples is None else examples)
    ]


def available_modes(tier: str | None = None) -> list[str]:
    """実際に例が存在するモードを :data:`MODES` の順で返す。"""
    present = {x.mode for x in EXAMPLES if not tier or x.tier == tier}
    return [mode for mode in MODES if mode in present]


def available_categories(tier: str | None = None) -> list[str]:
    """実際に例が付けているカテゴリを :data:`CATEGORIES` の順で返す。"""
    present = {
        category
        for x in EXAMPLES
        if not tier or x.tier == tier
        for category in x.categories
    }
    return [category for category in CATEGORIES if category in present]
