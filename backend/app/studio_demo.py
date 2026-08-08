"""ドラマスタジオのデモプロジェクト（``POST /api/studio/demo``）。

空のプロジェクトから始めると「何をどこに書くのか」が伝わらないので、脚本・
World Bible・話／場までひととおり埋まった作品を 3 本用意しておく。移植元は
TopView 風ドラマスタジオ（`sidecar/seed_projects.py`）の UI デモ 3 本で、日本語の
``caption`` と英語の ``prompt_caption`` の対はそのまま持ってきている。

素材は**メタデータのみ**（ファイル実体なし）で登録する: 絵や声の実体はユーザーが
あとから差し替えるもので、デモに要るのは「``@名前`` で呼べる設定が並んでいる」
状態のほうだから。ファイルを持たない素材は投入時に説明文へ展開される
（:func:`app.studio.resolve_mentions`）。

プロンプトは日本語のまま置いてある。プロジェクトの ``auto_translate`` が既定で
有効なので、投入すると Grok が H3 用の英語プロンプトへ直してから走る。
"""

from __future__ import annotations

from typing import Any

#: デモ 1 本ぶんの形:
#: ``{code, name, synopsis, world_notes, assets: [...], episodes: [{scenes: [{shots}]}]}``
DEMO_PROJECTS: tuple[dict[str, Any], ...] = (
    {
        "code": "YOAKE-01",
        "name": "夜明けの鋼",
        "synopsis": (
            "全8話・縦型ミステリー。帰る場所を失った二人が、封印された記録から"
            "自分たちの過去を見つける。"
        ),
        "world_notes": (
            "夜明け前の旧軌道工場。整備士の凛と帰還兵ユウは、停止していた記録端末に"
            "自分たちの名前が残されていることを知る。色は青い逆光と橙の警告灯の"
            "二色で通す。"
        ),
        "assets": [
            {
                "name": "凛",
                "category": "character",
                "caption": "主人公 / 整備士。黒いショートヘア。右頬の細い傷と、使い込まれた作業服。",
                "prompt_caption": (
                    "young Japanese mechanic, short black hair, thin scar on right"
                    " cheek, worn charcoal workwear"
                ),
                "locked": True,
            },
            {
                "name": "ユウ",
                "category": "character",
                "caption": "帰還兵 / 相棒。白髪まじりの青年。左腕は旧式の金属義手。",
                "prompt_caption": (
                    "lean young veteran, ash-gray hair, old brass mechanical left"
                    " arm, calm gaze"
                ),
                "locked": True,
            },
            {
                "name": "旧軌道工場",
                "category": "environment",
                "caption": "崩れた高架と巨大な整備塔。夜明け前の青い逆光。",
                "prompt_caption": (
                    "abandoned orbital maintenance yard, collapsed gantries, blue"
                    " pre-dawn backlight, drifting dust"
                ),
            },
            {
                "name": "記録端末",
                "category": "prop",
                "caption": "物語の鍵。掌サイズの古い端末。角が欠け、橙色の警告灯が明滅する。",
                "prompt_caption": (
                    "weathered palm-sized data terminal, chipped corner, blinking"
                    " amber warning light"
                ),
            },
        ],
        "episodes": [
            {
                "title": "第1話 停止した塔",
                "synopsis": "止まったままの整備塔に、二人が足を踏み入れる。",
                "scenes": [
                    {
                        "title": "旧軌道工場",
                        "synopsis": "封印されていた記録が見つかるまで。",
                        "time_of_day": "夜明け前",
                        "shots": [
                            {
                                "title": "工場へ進入",
                                "purpose": "舞台と二人の関係を見せる",
                                "prompt": "@凛 と @ユウ が @旧軌道工場 の割れた扉をくぐる。",
                                "camera": "ローアングルからのゆっくりしたプッシュイン",
                                "soundscape": "遠い風、軋む金属",
                                "bgm": "低く沈んだ弦",
                                "duration_seconds": 6,
                                "status": "ready",
                            },
                            {
                                "title": "足音に気づく",
                                "purpose": "工場に自分たち以外がいると匂わせる",
                                "prompt": "@凛 が足を止め、暗がりの奥へ視線を送る。",
                                "dialogue": "……今、何か聞こえた",
                                "camera": "肩越しの寄り、手持ち",
                                "soundscape": "水滴、遠い足音",
                                "duration_seconds": 5,
                                "status": "ready",
                            },
                            {
                                "title": "端末を発見",
                                "purpose": "物語の鍵を画面に置く",
                                "prompt": "@ユウ が瓦礫の下から @記録端末 を拾い上げる。",
                                "camera": "手元へのクロースアップ",
                                "soundscape": "瓦礫のこすれる音",
                                "duration_seconds": 5,
                                "status": "ready",
                            },
                            {
                                "title": "警告灯が点く",
                                "purpose": "端末が生きていると分からせる",
                                "prompt": "@記録端末 の橙色の警告灯が明滅し、@凛 の顔を照らす。",
                                "camera": "固定、浅い被写界深度",
                                "soundscape": "電子音のビープ",
                                "bgm": "細い高音のシンセ",
                                "duration_seconds": 4,
                                "status": "ready",
                            },
                            {
                                "title": "記録を再生",
                                "purpose": "二人の名前が残っていたと明かす",
                                "prompt": "@記録端末 の画面に古い名簿が流れ、@凛 と @ユウ が息を呑む。",
                                "dialogue": "私たちの名前が、どうしてここに",
                                "camera": "二人を収めたミディアム、じわりと寄る",
                                "duration_seconds": 7,
                            },
                            {
                                "title": "塔が起動する",
                                "purpose": "話を次へ押し出す",
                                "prompt": "@旧軌道工場 の整備塔に灯が走り、低い駆動音とともに動き出す。",
                                "camera": "見上げのワイド、ゆっくりティルトアップ",
                                "soundscape": "重い駆動音、警報",
                                "bgm": "打楽器の立ち上がり",
                                "duration_seconds": 8,
                            },
                        ],
                    }
                ],
            }
        ],
    },
    {
        "code": "SAZANAMI-02",
        "name": "さざなみ食堂",
        "synopsis": (
            "全6話・日常ドラマ。閉店を決めた店主と常連客が、最後の夜に言えなかった"
            "本音を料理へ託す。"
        ),
        "world_notes": (
            "海辺の小さな食堂。最後の営業を終えた澪は、帰らずに残った常連の奏へ、"
            "十年前と同じ出汁巻きを差し出す。暖色の灯りと雨の窓で通す。"
        ),
        "assets": [
            {
                "name": "澪",
                "category": "character",
                "caption": "食堂店主。肩までの黒髪。生成りのシャツと深緑の前掛け。穏やかな目元。",
                "prompt_caption": (
                    "Japanese woman in her thirties, shoulder-length black hair,"
                    " ivory shirt, deep green apron"
                ),
                "locked": True,
            },
            {
                "name": "奏",
                "category": "character",
                "caption": "最後の常連客。短い茶髪の青年。紺色のコート。左手に古い腕時計。",
                "prompt_caption": (
                    "young Japanese man, short brown hair, navy coat, old wristwatch"
                    " on left hand"
                ),
                "locked": True,
            },
            {
                "name": "さざなみ食堂",
                "category": "environment",
                "caption": "海辺の小さな食堂。閉店後の暖色照明と、雨に濡れた窓。",
                "prompt_caption": (
                    "small seaside diner after closing, warm pendant lights,"
                    " rain-streaked windows"
                ),
            },
            {
                "name": "欠けた小皿",
                "category": "prop",
                "caption": "二人の記憶。縁が少し欠けた白磁の小皿。青い波模様。",
                "prompt_caption": (
                    "small chipped white porcelain plate with a hand-painted blue"
                    " wave pattern"
                ),
            },
        ],
        "episodes": [
            {
                "title": "第1話 最後の一杯",
                "synopsis": "看板を消したあとの、ひと晩ぶんの会話。",
                "scenes": [
                    {
                        "title": "閉店後の食堂",
                        "synopsis": "言えなかった十年ぶんを、料理で渡すまで。",
                        "time_of_day": "閉店後の夜",
                        "shots": [
                            {
                                "title": "看板を消す",
                                "purpose": "終わりの時間だと示す",
                                "prompt": "@澪 が @さざなみ食堂 の外に出て、濡れた看板の灯を落とす。",
                                "camera": "引きの固定、雨粒が前ボケ",
                                "soundscape": "雨、遠い波",
                                "bgm": "静かなピアノ",
                                "duration_seconds": 6,
                                "status": "ready",
                            },
                            {
                                "title": "奏が残る",
                                "purpose": "帰らない客がいると気づかせる",
                                "prompt": "店内に戻った @澪 が、カウンターに座ったままの @奏 を見つける。",
                                "dialogue": "もう、閉めちゃったよ",
                                "camera": "戸口からのミディアム",
                                "duration_seconds": 5,
                                "status": "ready",
                            },
                            {
                                "title": "出汁を温める",
                                "purpose": "手が語る時間をつくる",
                                "prompt": "@澪 が鍋の出汁を温め、湯気が暖色の灯りに立ちのぼる。",
                                "camera": "手元へのクロースアップ、ゆっくり横移動",
                                "soundscape": "鍋の音、雨",
                                "duration_seconds": 5,
                                "status": "ready",
                            },
                            {
                                "title": "小皿を差し出す",
                                "purpose": "十年前と同じ皿だと見せる",
                                "prompt": "@澪 が湯気の立つ出汁巻きを @欠けた小皿 に載せ、@奏 の前へ静かに差し出す。",
                                "camera": "皿から二人へパン、ゆっくり",
                                "bgm": "低いチェロ",
                                "duration_seconds": 6,
                                "status": "ready",
                            },
                            {
                                "title": "十年前を話す",
                                "purpose": "本音が出る",
                                "prompt": "@奏 が箸を止め、@澪 の顔を見て言葉を探す。",
                                "dialogue": "十年前も、これでしたね",
                                "camera": "切り返しのバストショット",
                                "duration_seconds": 7,
                            },
                            {
                                "title": "雨が止む",
                                "purpose": "話を静かに閉じる",
                                "prompt": "@さざなみ食堂 の窓の雨が止み、外の街灯がにじんで光る。",
                                "camera": "窓越しの固定、ゆっくりフォーカス送り",
                                "soundscape": "雨が上がる、遠い波",
                                "duration_seconds": 6,
                            },
                        ],
                    }
                ],
            }
        ],
    },
    {
        "code": "ORBIT-03",
        "name": "軌道上の手紙",
        "synopsis": (
            "短編・SFロマンス。地球へ帰れない通信士が、届かないはずの手紙に返事を"
            "見つける。"
        ),
        "world_notes": (
            "無人に近い軌道ステーション。通信士レイは古い中継器から、五年前に"
            "失われた乗員の返信を受信する。地球光の青と、ホログラムの琥珀色。"
        ),
        "assets": [
            {
                "name": "レイ",
                "category": "character",
                "caption": "軌道通信士。銀灰色の短髪。青い保守制服。左耳に通信インプラント。",
                "prompt_caption": (
                    "orbital communications officer, short silver-gray hair, blue"
                    " maintenance uniform, left ear implant"
                ),
                "locked": True,
            },
            {
                "name": "ミナの記録",
                "category": "character",
                "caption": "失われた乗員。琥珀色のホログラムとして残る女性乗員の記録。",
                "prompt_caption": (
                    "female astronaut preserved as a translucent amber hologram,"
                    " calm expression"
                ),
                "locked": True,
            },
            {
                "name": "通信管制室",
                "category": "environment",
                "caption": "地球光が差す狭い管制室。古い中継器と浮遊する埃。",
                "prompt_caption": (
                    "narrow orbital communications room, Earthlight through"
                    " viewport, old relay consoles, floating dust"
                ),
            },
            {
                "name": "未送信パケット",
                "category": "prop",
                "caption": "物語の鍵。赤い未送信表示を残す古い光学メモリ。",
                "prompt_caption": (
                    "old optical memory module with a small red unsent indicator"
                ),
            },
        ],
        "episodes": [
            {
                "title": "未送信の声",
                "synopsis": "五年前の返信が届くまでの一夜。",
                "scenes": [
                    {
                        "title": "静止軌道ステーション",
                        "synopsis": "中継器を起こしてから、手紙を送るまで。",
                        "time_of_day": "地球の夜側",
                        "shots": [
                            {
                                "title": "中継器を起動",
                                "purpose": "舞台と孤独を見せる",
                                "prompt": "@レイ が暗い @通信管制室 で古い中継器のスイッチを入れる。",
                                "camera": "背中越しのミディアム、微かな手持ち",
                                "soundscape": "空調のうなり、リレーの音",
                                "bgm": "薄いドローン",
                                "duration_seconds": 6,
                                "status": "ready",
                            },
                            {
                                "title": "ノイズを分離",
                                "purpose": "何かが混ざっていると示す",
                                "prompt": "コンソールの波形が揺れ、@レイ がノイズの底の声を引き出す。",
                                "camera": "画面へのクロースアップ",
                                "soundscape": "ホワイトノイズ、途切れる声",
                                "duration_seconds": 5,
                                "status": "ready",
                            },
                            {
                                "title": "声を認識する",
                                "purpose": "誰の声か分かる",
                                "prompt": "@レイ が息を止め、スピーカーへ耳を寄せる。",
                                "dialogue": "……ミナ？",
                                "camera": "横顔のバストショット",
                                "duration_seconds": 5,
                                "status": "ready",
                            },
                            {
                                "title": "返信が届く",
                                "purpose": "ホログラムを出して転換する",
                                "prompt": "@未送信パケット が赤く点滅し、@ミナの記録 の琥珀色ホログラムが空中に現れる。",
                                "camera": "静かなプッシュイン",
                                "bgm": "高いシンセの持続音",
                                "duration_seconds": 7,
                                "status": "ready",
                            },
                            {
                                "title": "地球を見る",
                                "purpose": "距離を画で語る",
                                "prompt": "@レイ が窓の外の青い地球を見上げ、ホログラムの光が頬に落ちる。",
                                "camera": "ワイド、ゆっくりティルトアップ",
                                "duration_seconds": 6,
                            },
                            {
                                "title": "手紙を送信",
                                "purpose": "短編を閉じる",
                                "prompt": "@レイ が送信キーを押し、@未送信パケット の赤い表示が緑に変わる。",
                                "camera": "指先へのクロースアップから引き",
                                "soundscape": "送信音、静けさ",
                                "duration_seconds": 6,
                            },
                        ],
                    }
                ],
            }
        ],
    },
)

#: 作品コード -> マニフェスト
DEMO_BY_CODE: dict[str, dict[str, Any]] = {
    project["code"]: project for project in DEMO_PROJECTS
}
