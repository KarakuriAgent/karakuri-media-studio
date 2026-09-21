"""脚本ドラフト作成ガイド（外部 API の ``GET /api/v1/prompt-guide``）。

外部サービスの LLM エージェントが ``POST /api/v1/stories`` に脚本を納品する
とき、そのプロンプトへそのまま貼れる日本語 Markdown を組み立てる。

**静的なコピーは置かない。** ガイドを手書きで複製すると、本体の定数が変わった
ときに黙って古くなる。そこで

- 尺の範囲は :data:`app.studio.SHOT_DURATION_MIN` / :data:`~app.studio.SHOT_DURATION_MAX`
- 参照素材の上限は :data:`app.workflows.MINIMAX_H3_REFERENCE_IMAGES` ほか
- H3 の書き方そのものは :data:`app.prompts.MINIMAX_H3_GUIDE_BODY`
- 実例は :mod:`app.h3_examples`（``GET /api/v1/prompt-examples`` と同じデータ）

を**そのまま引いて**組み立て、ここには「stories 投入に要る分だけ」の編成と、
このアプリ固有の契約（フィールドの届き方・``@名前``）だけを書く。

.. warning::

   フィールドの届き方は :func:`app.studio.compose_prompt` が正本。あちらの
   挙動（何を包むか、何を足すか、除外文を付けるか）を変えたら、この
   モジュールの「1. フィールド契約」も直し、:data:`GUIDE_VERSION` を上げる
   こと。逆に :func:`~app.studio.compose_prompt` 側にも、ここへの参照コメントを
   置いてある。

規模はリクエストごとに組み立てて問題ない（キャッシュしない）。
"""

from .h3_examples import (
    available_categories,
    available_modes,
    render_examples,
    select_examples,
)
from .models import DraftingGuide, DraftingGuideLimits
from .prompts import MINIMAX_H3_GUIDE_BODY
from .studio import EXCLUSION_SENTENCE, SHOT_DURATION_MAX, SHOT_DURATION_MIN
from .workflows import (
    MINIMAX_H3_REFERENCE_AUDIOS,
    MINIMAX_H3_REFERENCE_IMAGES,
    MINIMAX_H3_REFERENCE_VIDEOS,
)

#: ガイド本文の版。中身を変えたら上げる（受け取り側がキャッシュの判定に使う）。
GUIDE_VERSION = "2026-09-07"

#: 実用上の下限（秒）。:data:`~app.studio.SHOT_DURATION_MIN` は API が受け付ける
#: 範囲で、H3 は 4 秒を切ると芝居が入りきらない（``MINIMAX_H3_GUIDE_BODY`` の
#: "Duration 4–15s" と同じ値）。
SHOT_DURATION_RECOMMENDED_MIN = 4.0


def _seconds(value: float) -> str:
    """``6.0`` → ``"6"``（整数なら小数点を落として本文に埋める）。"""
    return str(int(value)) if float(value).is_integer() else str(value)


def _recommended_range() -> str:
    return f"{_seconds(SHOT_DURATION_RECOMMENDED_MIN)}-{_seconds(SHOT_DURATION_MAX)}"


def _few_shot(*ids: str) -> str:
    """:mod:`app.h3_examples` の例を見出し id で切り出す。

    全部貼るとガイドが長すぎるので代表だけを選ぶが、本文は写さずあちらから
    組み立てる（例が直れば追随する）。このガイドでは ``### 4.2`` の下に置くので、
    見出しを 1 段下げ、few-shot 用の前書き（``header``）は付けない。
    """
    block = render_examples(select_examples(ids=ids), header=False)
    return "\n".join(
        f"##{line}" if line.startswith("## ") else line
        for line in block.rstrip().splitlines()
    )


def _example_catalog() -> str:
    """「実例の追加取得」節を実例データから組み立てる。

    モード・カテゴリの一覧は :mod:`app.h3_examples` に**実際に存在する値**だけを
    出す（手書きの列挙は例が増減したときに黙って古くなる）。
    """
    modes = " / ".join(f"`{mode}`" for mode in available_modes())
    categories = " / ".join(f"`{name}`" for name in available_categories())
    canonical = select_examples(tier="canonical")
    inspiration = select_examples(tier="inspiration")
    index = "\n".join(
        f"- `{example.id}` — {example.mode} [{', '.join(example.categories)}]"
        f" {example.summary}"
        for example in canonical
    )
    return f"""\
`GET /api/v1/prompt-examples` で、上より多くの実例を取れる（`guide_version` は
このガイドと同じ版）。

- クエリ無し … 索引だけ（`id` / `mode` / `categories` / `summary` / `tier`。本文なし）
- `?id=H3-E4` … その 1 件を本文つきで
- `?mode=<モード>&category=<カテゴリ>&limit=<件数>` … 条件に合うものを本文つきで

指定できる `mode`: {modes}
指定できる `category`: {categories}

`tier` は 2 種類ある。`canonical`（{len(canonical)} 件）は公式 rewrite 形式の
**完成例**で、`prompt` の書きぶりはこれを真似する。`inspiration`（{len(inspiration)} 件）は
公式ブログやコミュニティの**生入力**で、発想の素材にはなるが形は真似しない。

使いどころ: 書こうとしているカットが下の代表例とジャンルの違うもの
（アニメ調・商品CM・画面内の文字やUI・複数話者の会話・参照素材の多いカット・
既存動画の編集）なら、書き始める前に近いカテゴリの `canonical` を 1 件取って
形を合わせること。

現在の `canonical` の索引:

{index}"""


#: 素材メンション入りの Shot の実例（``app.studio_demo`` のデモ脚本と同じ書き味を、
#: stories のボディの形で見せる）
_MENTION_EXAMPLE = """\
```json
{
  "title": "工場へ進入",
  "purpose": "舞台と二人の関係を見せる",
  "prompt": "実写・シネマティック。[Shot 1] ミディアムワイドで、@凛 と @ユウ が @旧軌道工場 の割れた扉をくぐる。夜明け前の青い薄明かりが割れたガラスから差し、埃が光の筋の中を漂う。カメラはローアングルからゆっくりプッシュインし、二人が瓦礫をまたいで暗がりへ進む。",
  "dialogue": "……ここで合ってる",
  "camera": "ローアングルからのゆっくりしたプッシュイン",
  "soundscape": "遠い風、軋む金属、砂利を踏む足音",
  "bgm": "低く沈んだ弦",
  "duration_seconds": 6,
  "status": "ready"
}
```

`@凛` / `@ユウ` / `@旧軌道工場` はこの作品に登録済みの素材名。`purpose` と
`title` はモデルに届かないメモなので、「夜明け前」「割れた扉」といった画に要る
情報は上のように **`prompt` の中に書き直してある**。"""


def build_drafting_guide() -> DraftingGuide:
    """``GET /api/v1/prompt-guide`` のレスポンスを組み立てる。"""
    duration_range = (
        f"{_seconds(SHOT_DURATION_MIN)}〜{_seconds(SHOT_DURATION_MAX)} 秒"
    )
    markdown = f"""\
# 脚本ドラフト作成ガイド（`POST /api/v1/stories` 投入用）

このスタジオは 1 カット = MiniMax H3 の 1 クリップ（{duration_range}、映像と音声を
同時に生成）で作る。**実際に映像化できる脚本**を書くための契約を以下にまとめる。
構造は **作品 (project) → 話 (episode) → 場 (scene) → カット (shot)**。

## 1. フィールド契約（どれがモデルに届くか）

- **`prompt` が映像・タイムラインの本体で、モデルに届く唯一の記述フィールド。**
  投入時に `integrated_multimodal_description:`（参照素材つきの r2v では
  `detailed_description:`）として包まれる。1 行の短文ではなく、**スタイル・構図・
  被写体・動き・時間軸**を含む完結した記述を書くこと。
- **`title` / `action` / `purpose` はモデルに一切届かないメモ欄。** 場の
  `time_of_day`（「夜」など）も同じで、**`prompt` の中に書き直さないと映像には
  出ない**。
- `dialogue` は**発話内容だけ**（話者名やト書きは書かない）。本文に `<d>` が
  無ければ自動で `(S1) says: <d>[Japanese] …</d>` に包まれるので、**単一話者
  専用**。複数の話者がしゃべるカットは `prompt` の中に `(S1)` / `(S2)` を自分で
  書き、`dialogue` は空にする。
- `camera` は**短いカメラ指示 1 文**（下の公式語彙を推奨）。本文がカメラに
  触れていなければ `The camera …` の一文として織り込まれる。
- `soundscape` → `overall_soundscape:`、`bgm` → `non_diegetic_music:` になる。
  無音なら `bgm` は空でよい（必要なら自動で `N/A` が入る）。`prompt` の中で
  同じ内容を繰り返さない。
- 字幕・ロゴの禁止文（`{EXCLUSION_SENTENCE}`）は
  自動で付くので書かなくてよい。
- **ネガティブプロンプトは存在しない。** 「〜を写さない」は指定できないので、
  写したいものを本文で描写して表現する。
- 日本語で書いてよい。ただし**投入されるのは英語だけ**で、アプリは英訳を
  しない。日本語のまま生成しようとすると 400 で断られる。英語版を書くのは
  エージェント自身の仕事（手順は §3.2）。

## 2. 素材メンション（`@名前`）

- 登場キャラクター・場所・小道具は、**`prompt` の中の `@名前`** でのみ作品の
  素材（World Bible）に解決される。名前に空白や記号が入るときは `@{{名前}}`。
- **`action` や `purpose` に書いた `@名前` は無効**（ただの文字列として捨てられる）。
- 使えるのは**登録済みの素材名だけ**。`GET /api/v1/projects/{{id}}` の `assets` に
  ある名前を確認してから書くこと。未登録の `@名前` は stories の投入自体は通るが、
  **レンダリング時に 400 で失敗する**。
- ファイルを持つ素材（画像・動画・音声）をメンションすると、そのカットは参照
  素材つきの **r2v モードに自動で切り替わり**、`@名前` は `<Picture 1>` /
  `<Video 1>` / `<Audio 1>` のタグになる。1 カットに添付できるのは
  画像 {MINIMAX_H3_REFERENCE_IMAGES} 件・動画 {MINIMAX_H3_REFERENCE_VIDEOS} 件・音声 {MINIMAX_H3_REFERENCE_AUDIOS} 件まで。
  ファイルを持たない（メタデータのみの）素材は、その説明文が本文に展開される。

## 3. H3 プロンプト規約

`prompt` の中身が満たすべき規約（公式 rewrite 契約より）:

{MINIMAX_H3_GUIDE_BODY}

尺は API としては {duration_range}（`duration_seconds`）を受けるが、実質は
**{_recommended_range()} 秒**で書くこと。

### 3.1 構図が難しいカットはブロッキング動画を作る

「誰が画面のどこに立ち、カメラがどう動くか」を言葉だけで詰め切れないカットは、
**構図リファレンス動画（ブロッキング）**を先に作る。四角・丸・簡易人型だけの
3D シーン定義を `POST /api/v1/library/blocking` に投げると、24fps の mp4 が
ライブラリの動画素材として登録される（詳細は `docs/EXTERNAL-API.md` §3.5）。

- 応答の `location_map`（`hero at x 50%, y 56%` の形の英文）を**カット本文へ
  写す**。数字は実際に描かれた絵から出しているので、書きぶりと画がずれない。
- 動画そのものは作品の素材に登録して（`POST /api/v1/projects/{{id}}/assets` に
  `library_id` を書けばライブラリの項目をそのまま取り込める）`@名前` で参照する。
  参照素材が付くカットは r2v になり、`reference_note`
  （`<Video k> (camera path and blocking only): weak_reference - …`）は
  **`retention_analysis` の 1 行として自動で足される**ので手で書かなくてよい
  （番号 `k` も実際の添付順に合わせて振られる）。言い回しを変えたいときだけ
  自分で書く（同じ番号の注記があれば二重にはならない）。
- ブロッキングは**グレーのマネキンのプレビズ**なので、見た目・色・素材は
  絶対に真似させない（それを言い切るのが `reference_note` の役目）。人物の顔や
  衣装は今までどおり本文と `<Picture n>` で決める。
- 構図を直したいときは同じ項目に `POST /api/v1/library/{{id}}/blocking` を投げる
  （mp4 のパスも id も変わらないので、カット側は直さなくてよい）。ただし**作品の
  素材はコピー**なので追従しない: `library_update_available` が立つので
  `POST /api/v1/assets/{{id}}/refresh-from-library` で取り直す（取り直した素材を
  使った Take は stale になる = 焼き直しが要る）。

### 3.2 日本語で書いた場合の英訳手順

MiniMax H3 は英語プロンプト前提のモデルで、**アプリは英訳をしない**（アプリ内で
LLM を呼ぶのはプロンプト作成チャットとヘルスチェックだけ）。脚本は日本語で
書いてよいが、生成の前に**あなたが英語版を書いて保存する**。

1. `GET /api/v1/shots/{{id}}/prompt-preview` を見る。`prompt` が実際に組み立て
   られた本文（公式フィールド・`@名前` の展開・除外文まで込み）で、
   `needs_translation` が `true` なら**このままでは投入できない**（本文に日本語が
   あり、使える英語キャッシュが無い）。
2. その `prompt` を**英語の公式 H3 文書として書き直す**（下の鉄則）。
3. `PATCH /api/v1/shots/{{id}}` の `english_prompt` に保存する（`base_revision` を
   忘れずに）。元になった本文は `english_source` としてサーバー側が控える。
4. もう一度 `prompt-preview` を見て `needs_translation` が `false`、
   `english_stale` が `false` になっていることを確かめてから
   `POST /api/v1/shots/{{id}}/render`。
5. 脚本を直すと本文が変わるので `english_stale` が `true` に戻る。そのときは
   1 からやり直す（`english_prompt` に空文字を PATCH すればキャッシュを消せる）。

書き直しの鉄則（**直訳ではなく、公式 H3 文書として書き直す**）:

- 事実は 1 つも足さず減らさない。書かれていない人物・場所・衣装・台詞・筋を
  **発明しない**。
- 参照タグ（`<Picture 1>` / `<Video 2>` / `<Audio 1>` / `<Subject 1>` …）は
  **一字一句そのまま**。振り直さない、訳さない、消さない、増やさない。
- **二重引用符の中の台詞と `<d>…</d>` の中身は原語のまま**（日本語は日本語で
  残す。H3 はここをそのまま喋る）。訳すのはその周りの地の文だけ。
- 末尾の除外文（字幕・ロゴの禁止文）があればそのまま残す。
- 公式フィールドを**揃った形**で出す（基本 3 フィールド、または参照素材つきの
  Ref2VA なら 6 フィールド。どちらかは `prompt-preview` の `workflow` で分かる）。
  足りないフィールド見出し・`[Shot 1]`・公式のカメラ節・書かれた動きから導ける
  `overall_soundscape` / `non_diegetic_music` は**補う**。
- **書かれている**動作の観測可能な段取り（身体・接触・視線・結果の状態・すでに
  含意されている光）は膨らませてよい。
- i2v（開始フレームつき）では公式の alignment 行を先頭に置いたままにする。
- Ref2VA でタグはあるのに分析セクションが無いときは、**そのタグと書かれた事実
  だけから** `subject_definitions` / `summary` / `retention_analysis` /
  `detailed_description` を**最小限**書く（被写体を勝手に増やさない）。
- `[Shot N]` と `At MM:SS.mmm` はそのまま。`Camera:` / `Audio:` 行に変換しない。

## 4. 実例

### 4.1 素材メンション入りのカット（このアプリの脚本の形）

{_MENTION_EXAMPLE}

### 4.2 公式 H3 文書の例（`prompt` が最終的にこう解釈される）

{_few_shot("H3-E2", "H3-E3")}

### 4.3 実例の追加取得（`GET /api/v1/prompt-examples`）

{_example_catalog()}

## 5. `POST /api/v1/stories` の注意

- 話 1 本（話 → 場 → カット）を 1 リクエストで納品する。作成は **1 トランザクションで
  all-or-nothing**: 途中の検証に落ちたら全部ロールバックして 400 になり、
  中途半端な脚本は残らない。
- `render` は立てない。生成は脚本を入れてから**1 カットずつ**回す:
  `GET /api/v1/shots/{{id}}/prompt-preview` で実際に投入される本文と
  `needs_translation` / `render_blocker` を確かめる（日本語が残っていれば §3.2 の
  手順で `english_prompt` を保存してから）→ `POST /api/v1/shots/{{id}}/render` →
  `GET /api/v1/jobs/{{job_id}}` をポーリングして完了を待つ → 出来を見て
  `POST /api/v1/takes/{{id}}/select`（採用）か `/reject`（不採用）。
- 直しは PATCH（`base_revision` を必ず付ける）。削除はカット・場・話・素材・
  Take まで自分でできるが、**作品（プロジェクト）の削除だけは API に無い**
  ので人に頼むこと（履歴ごと消えて復元できないため）。
- 各カットには `title` / `purpose` / `action` / `prompt` / `dialogue` / `camera` /
  `soundscape` / `bgm` / `duration_seconds` / `status` を指定できる。`soundscape` と
  `bgm` はカットごとの音の指定で、書いておくと上の公式フィールドになる。
"""
    return DraftingGuide(
        guide_version=GUIDE_VERSION,
        markdown=markdown,
        limits=DraftingGuideLimits(
            shot_duration_min_seconds=SHOT_DURATION_MIN,
            shot_duration_max_seconds=SHOT_DURATION_MAX,
            shot_duration_recommended=_recommended_range(),
            reference_images_max=MINIMAX_H3_REFERENCE_IMAGES,
            reference_videos_max=MINIMAX_H3_REFERENCE_VIDEOS,
            reference_audios_max=MINIMAX_H3_REFERENCE_AUDIOS,
        ),
    )
