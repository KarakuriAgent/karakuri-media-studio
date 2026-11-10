# 同梱の素材パック

効果音・BGM・漫画記号などの**素材パックの目録**を同梱しています。ここにあるのは
**目録だけ**（入手元の情報・規約の要点と URL・規約を確かめた日・素材ごとの名前・分類・
タグ・説明・取得元の URL）で、**素材のファイルは入れていません**（どの入手元も素材そのものの
再配布を禁止しています）。

スタジオで選んで入れると、各素材を入手元のサイトから 1 件ずつ取りに行き（robots.txt を守り、
間隔をあける）、出どころの記録（`license`）付きでライブラリ（棚）に組み込みます。取り込みの
仕組みと目録の書き方は [`docs/EXTERNAL-API.md`](../docs/EXTERNAL-API.md) §3.8。

## 入れ方

- **画面**: ファイルタブ（ライブラリ）の [素材パック] → 入手元ごとの規約の要点・確かめた日を
  読み、[規約のページ] を開いて確かめてから同意のチェックを付けて [入れる]。
  進み具合と結果（取り込んだ・飛ばした・取れなかった）がその場に出ます
- **API**: `GET /api/v1/library/packs/bundled` で一覧（入れ済みの件数つき）、
  `POST /api/v1/library/packs/bundled/{id}/install {"accept_sources": [...], "dry_run": false}`
  で入れる（202。進み具合は `GET /api/v1/library/packs/imports/{id}`）
- 入れた素材は棚で目録の分類で絞れます（`GET /api/v1/library?category=効果音`）
- 何度入れても同じ結果です（入れ済みの素材は取りに行きません）。入手元に無くなった素材は
  `missing` に出ます

**使い方のコツ**（効果音・BGM の当て方、漫画記号の置き方）はスキル
`workspace/.agents/skills/karakuri-studio/SKILL.md`「効果音を当てる」「BGM を選ぶ・当てる」と
`workspace/.agents/skills/karakuri-remotion/SKILL.md`「漫画記号を置く」にあります。

**規約は変わることがあります。** 下の「確かめた日」より後に規約が変わっていることがあるので、
入れる前に規約のページを確かめてください。クレジットはどの入手元も不要か任意ですが、
各入手元の禁止事項（素材の再配布・販売、Content ID への登録など）は守ってください。

## パックと入手元

| パック | 件数 | 前の id（`aliases`） |
|---|---|---|
| `shorts-sfx-komori` 効果音（無料効果音で遊ぼう！） | 80 | `kanon-shorts-sfx` |
| `shorts-bgm-amacha` 場面別 BGM（甘茶の音楽工房） | 37 | `amacha-shorts-bgm` |
| `manga-symbols-ja` 漫画記号（集中線・漫符・吹き出し） | 137 | `kanon-shorts-manga-symbols` |

入手元（規約を確かめた日はどれも 2026-11-10。取り方はどれも `auto` ＝ 取得元の URL から取る）:

| 入手元 | パック（件数） | 規約 | 要点 |
|---|---|---|---|
| 無料効果音で遊ぼう！（小森平） | shorts-sfx-komori（80） | <https://taira-komori.net/welcome.html> | あらゆる目的で使用可・加工可・商用可・連絡不要。リンク表示は任意。禁止: 自作と主張する・素材そのものの再配布や販売・犯罪行為への使用・Scratch での利用 |
| 甘茶の音楽工房 | shorts-bgm-amacha（37） | <https://amachamusic.chagasi.com/terms.html> | 商用・個人とも可。クレジット・報告は不要。加工可。禁止: 音楽だけの販売・再配布、作曲者を偽る、直リンクでの使用、JASRAC などへの登録、音楽配信サイトでの配信、YouTube の Content ID への登録 |
| 小樽総合デザイン事務局（漫画風フリー素材） | manga-symbols-ja（53） | <https://otarunet.com/it/webdesign/comic-material-fukidashi/> | 商用・非商用とも無料。著作権表記は不要。禁止: 素材が主役になる用途、素材データの再配布（改変含む）・販売 |
| アイキャッチャー | manga-symbols-ja（22） | <https://ai-catcher.com/%e5%95%86%e7%94%a8%e5%88%a9%e7%94%a8%e3%81%ab%e3%81%a4%e3%81%84%e3%81%a6/> | 無料で商用可。出典表記は任意。禁止: イメージを損なう媒体での利用、素材の再配布、キャラクターの商品化 |
| みりんのイラスト素材 | manga-symbols-ja（28） | <https://miirriin.com/illustration/terms-conditions/> | 個人・商用とも無料（YouTube の収益化も可）。加工可・クレジットは任意。禁止: 素材の再配布・販売、素材が主役の制作物の配布・販売、自作と偽る、AI 学習目的、公序良俗に反する利用 |
| みりんの動画素材 | manga-symbols-ja（24） | <https://miirriin.com/tos/> | 同上（動画素材版）。ZIP で配っている（目録の `member` で中のファイルを取り出す） |
| フリー素材ドットコム | manga-symbols-ja（6） | <https://www.free-materials.com/guidelines/> | 個人・商用とも無料・クレジット不要・加工自由。禁止: 素材そのものの再配布・販売など |
| Openclipart | manga-symbols-ja（3） | <https://openclipart.org/share> | すべて CC0（パブリックドメイン） |
| フレームイラスト | manga-symbols-ja（1） | <https://frame-illust.com/?page_id=57> | 個人・法人とも無料・クレジット不要。禁止: 素材の転売・再配布、配信サイトのスタンプ等としての提供 |

正確な要点・Content ID の扱い・メモは各目録の `sources` にあります（画面と API の一覧にも出ます）。

## 目録を足す・直す

同梱の目録は「自動で取りに行ってよく、クレジット不要か任意で、数の制限が無い」ことを
確かめた入手元から、**選別して採用した物だけ**を載せます。

1. **入手元を確かめる**: 規約のページで、映像での利用・商用・加工・クレジットの要否・数の制限・
   禁止事項（再配布・直リンク・自動取得・ボット・Content ID）を読む。`robots.txt` と、
   素材のファイルの URL が `KarakuriMediaStudio` の User-Agent の普通の GET で取れること
   （HTML や 403 にならないこと）を確かめる。自動取得を禁じている・直リンク対策で取れない
   入手元は載せない
2. **試しの目録で取り込む**: 同梱しない手元の目録（`docs/examples/asset-pack.example.yaml` の形）に
   候補を書き、`POST /api/v1/library/packs/import`（`manifest_text`・`accept_sources`）で
   `dry_run: true` → 本番の順に取り込む。ZIP の中身は `POST /api/v1/library/packs/archive`
3. **選別する**: 棚で聞く・見る（長さ・ラウドネスは `media_info`）。不採用の物は棚から消し
   （`DELETE /api/v1/library/{id}`）、手元の目録に不採用の理由を残す（同梱の目録には入れない）
4. **同梱の目録に足す**: 採用した物だけを `asset-packs/<id>.yaml` の `items` に足す。入手元が
   新しければ `sources` に `terms_url`・`terms_summary`・`checked_at`（確かめた日）・
   `credit_required`・`acquire: auto`・`interval_seconds`（4 秒前後）を書く。パックの id・名前・
   説明・タグには特定のキャラクターや利用者に寄った言葉を入れない。素材のファイルは入れない
5. **確かめる**: `cd backend && ../.venv/bin/python -m pytest tests/test_bundled_packs.py`
   （目録が読めること・素材のファイルが無いことなど）。件数が変わったらテストの `EXPECTED` と
   この README の表を直してコミットする
6. **規約を確かめ直したとき**は `checked_at` と `terms_summary` を直す。規約が変わって使えなく
   なった入手元は、その `sources` と素材を目録から外す（入れ済みの素材は棚に残る）

- **パックの id を変える**ときは、前の id を `aliases` に足す。前の id で入っている素材も
  入れ済みと見なし、起動時と入れるときに出どころの記録（`license.pack`）とタグを今の id に揃える
- 素材を目録から外しても、入れ済みの棚の項目は消えません（要らなければ棚から消す）
