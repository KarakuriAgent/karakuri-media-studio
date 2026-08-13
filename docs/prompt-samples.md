# プロンプト実例集（Civitai公開ギャラリーのメタデータから機械抽出）

Grokのシステムプロンプト設計の参考資料。各サンプルは公開ギャラリー画像/動画に埋め込まれた
ComfyUIワークフローから抽出した生データ（原文ママ）。

## 動画プロンプト実例（モデル作者本人の投稿）

これらは Civitai 由来の **1 段落**で、Grok には埋め込まない。現行の動画 few-shot は
`prompts.FEW_SHOT_H3`（公式 MiniMax H3 文書: H3-E1 I2VA / H3-E2 T2VA / H3-E3 Ref2VA）。

構造（旧）: `<シーン種別> scene.` の宣言で始まり、人物の外見 → 動作の推移 → 引用符 `"..."` で囲んだセリフ（そのまま音声合成される）→ 音・声の描写、の順。

### サンプル1（セリフ+効果音あり）
```
voyeur style cum shot and handjob scene.  A woman sitting with her pink panties around her ankles is excited when she sees a big cock poking through the wall, in a british voice she says "Oh Thank god!" as she reaches out and strokes the cock fast.  The huge amounts of white wet cum erupt from the tip of the cock splattering everywhere in endless waves of cum all over the woman, the walls, her panties and the floor as we hear a man moaning with pleasure.  The woman keeps stroking the cock and giggles with sexual enjoyment as even more massive amounts of white cum erupt from the cock tip everywhere in the room
```

### サンプル2（セリフあり）
```
handjob cum in mouth scene.  A nude man laying on his back is getting his a handjob by a woman with long brown hair and a pink headband.  Her mouth is full of the man's white cum, as shes stroking his cock giving him a handjob she opens her mouth and sticks her tongue out.  All the white warm cum falls out of her mouth and lands on the man's cock as she says "See honey?  wasn't that a nice?".  The man lets out a orgasmic sigh
```

### サンプル3（音の描写のみ）
```
amazing mutual masturbation scene with a woman wearing a choker chained to a nude man.  SHe has huge breast and a hairy pussy.  The man has a big belly and erect cock.  The woman is stroking the man's cock in front of the TV while the man is fondling and squeezing her huge tits.  they are both moaning as they pleasure each other and masturbation each other
```

### 作者が実際に使っているネガティブプロンプト

※ dev 系テンプレートの既定値（`pc game, console game, ...`）とは異なる。品質系+音声系の否定語を含む:
```
blurry, oversaturated, pixelated, low resolution, grainy, distorted, noise, compression artifacts, jpeg artifacts, glitches, watermark, text, logo, signature, copyright, subtitles, distorted sound, saturated sound, loud
```

## RedCraft (Krea 2) — 画像プロンプト実例（同バージョンのギャラリーより）

自然文の長い1段落。品質語プレフィックス+被写体・構図・光・質感を文章で記述。

### サンプル1（1095字・自然文段落）
```
masterpiece, very aesthetic

A dynamic low-angle shot of a knight in battered, dark steel armor caught mid-swing, his body torqued with explosive force as he brings his worn longsword down in a devastating arc. His face remains completely obscured, the visor of his scarred helm revealing only an impenetrable black void, while the massive gash across his breastplate glows with a faint, smoldering ember as if the wound itself fuels his fury. The blade carves through the air, trailing a cascade of fierce orange sparks and swirling flame particles that scatter like dying stars. Billowing clouds of ash and ember surge around him, caught in the violent updraft of his strike, while the jagged edges of his ruined armor glint with rim light from the inferno behind. The background is a blurred, crumbling ruin, deep in shadow and illuminated only by the fierce, flickering firelight that paints his entire form in stark, dancing chiaroscuro. The atmosphere is one of desperate, undying rage—a hollowed warrior unleashing his final, blazing onslaught in a world reduced to cinders and silence.
```

### サンプル2（315字・簡潔な段落）
```
A Baroque-style painting of a decadent feast, where elegantly dressed figures engage in flirtatious and intimate interactions amidst an opulent banquet, their eyes locked in suggestive glances. 
(masterpiece, award winning artwork)
many details, extreme detailed, full of details,
Wide range of colors, high Dynamic
```

## 手元ワークフロー内の実例

`workflow/` 配下のテンプレート自体にも実運用サンプルが残っている（こちらが最重要の参考元）:

- 画像プロンプト: `workflow/image/krea2/krea2_turbo.json` のノード `30:19`（トリガーワード先頭 + スタイル宣言 + 被写体/ポーズ + 表情 + 照明/質感 + カメラ/品質語）
- 動画プロンプト: MiniMax H3 の書き方は公式 base / ref ガイド（`VIDEO_PROMPT_WRITING_GUIDE_{base,ref}_en.md`）が契約。このアプリの Grok 向け要約は `prompts.MINIMAX_H3_*`。`workflow/video/minimax-h3/*.json` のプロンプトノードはグラフ上の投入先であり、`Camera:` / `Audio:` フッタや `[0s-1.5s]` スタンプは使わない


## Krea 2 公式プロンプティングガイド（krea-ai/krea-2 docs/prompting.md）

- **自然文プロンプト推奨**。長く詳細なプロンプトが最良の結果を出すが、短くても高品質
- 文字を描画したい場合は対象語を引用符で囲む
- 公式サンプルの傾向: 「ショット種別/媒体 → 被写体+属性 → 細部（衣装・質感） → 照明 → 背景 → 構図/被写界深度」を1段落に。タグ羅列型（短句カンマ区切り）と完全文章型の両方が有効
- **重要**: 公式の LLM 用プロンプト拡張システムプロンプトは、`workflow/image/krea2/krea2_turbo.json` のノード `30:18`（TextGenerate 用システムプロンプト）と同一。Grok 用システムプロンプトはこれをベースに調整する

### 公式サンプル（SFW・原文ママ、抜粋）

```
high-fashion editorial portrait of a young East Asian woman, short choppy platinum blonde bob with heavy bangs, looking over her bare shoulder to the right, lips playfully pursed, wearing a structured black top with an architectural protruding bust detail and thin straps, delicate gold hoop earrings, arm bent with hand resting on hip, warm skin tones, solid striking crimson red background, soft directional studio lighting, cinematic color palette, medium close-up shot
```

```
extreme close-up of a woman's face partially obscured by tousled dark brown hair, soft parted lips, smooth skin on lower cheek and jawline, stray hair strands falling loosely across the nose, deep moody shadows enveloping the left frame, cinematic warm lighting, delicate highlights on the mouth, muted earthy color palette, sepia-toned warmth, intimate portrait photography, macro lens, shallow depth of field, distinct film grain texture, vintage atmospheric aesthetic
```

## MiniMax H3 の動画プロンプト（Grok に渡す契約）

- 公式 rewrite 契約は `prompts.MINIMAX_H3_*`。few-shot は `prompts.FEW_SHOT_H3`（公式フィールド / `[Shot N]` / `<d>`）
- `prompts.VIDEO_SPEC` と talkvid の `[VISUAL]` / `[SPEECH]` 切替は pre-H3 経路。H3 ジョブには埋め込まない
- 技術制約: 幅・高さの丸め単位とフレーム数の格子はワークフローごと（SPEC §3.1）

## 抽出方法メモ

- 動画(mp4): `ffprobe -show_entries format_tags=comment` → comment タグに API 形式ワークフロー JSON
- 画像(PNG): tEXt チャンク `prompt`（API形式JSON）/ `workflow`（UI形式）
- Civitai API: `/api/v1/images?modelVersionId=...` は meta が null（作者非公開）のため本体から抽出した