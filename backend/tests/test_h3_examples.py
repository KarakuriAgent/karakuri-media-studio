"""MiniMax H3 の実例集（``app.h3_examples``）。

見るのは 3 つ: 選択関数（内蔵と外部で共有する唯一の選び方）、ワークフローごとの
既定の対応表、そして canonical の例が公式 rewrite 形式を機械的に満たしていること
（見出し・アライン行・ショットの時刻・末尾文）。
"""

import re

import pytest

from app import h3_examples
from app.h3_examples import (
    CATEGORIES,
    DEFAULT_IDS,
    EXAMPLES,
    MODES,
    default_examples_for_workflow,
    render_examples,
    select_examples,
)

#: base モードの 3 フィールド
BASE_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
#: Ref2VA の 6 セクション（この順）
REF_SECTIONS = (
    "subject_definitions:",
    "summary:",
    "retention_analysis:",
    "detailed_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)

CANONICAL = [x for x in EXAMPLES if x.tier == "canonical"]
CLOSING = "No text, subtitles, logos or watermarks."


# --------------------------------------------------------------------------
# データそのもの
# --------------------------------------------------------------------------

def test_the_ids_are_unique_and_the_vocabulary_is_closed():
    ids = [x.id for x in EXAMPLES]
    assert len(ids) == len(set(ids))
    for example in EXAMPLES:
        assert example.mode in MODES, example.id
        assert example.categories, example.id
        assert set(example.categories) <= set(CATEGORIES), example.id
        assert example.tier in ("canonical", "inspiration"), example.id
        assert example.summary.strip(), example.id
        assert example.source.strip(), example.id
        assert example.body.strip(), example.id


def test_there_are_ten_canonical_examples_and_the_first_three_are_the_old_ones():
    assert [x.id for x in CANONICAL] == [f"H3-E{n}" for n in range(1, 11)]
    assert DEFAULT_IDS == ("H3-E1", "H3-E2", "H3-E3")
    # モードの穴が無いこと（既存の 3 本には無かった fl2v / l2v / edit を含む）
    assert {x.mode for x in CANONICAL} == set(MODES)


def test_the_inspiration_examples_are_kept_apart_from_the_finished_ones():
    inspiration = [x for x in EXAMPLES if x.tier == "inspiration"]
    assert len(inspiration) >= 10
    block = render_examples(inspiration)
    assert "RAW PROMPT INPUTS" in block
    assert "FEW-SHOT EXAMPLES" not in block
    # 生入力なので公式形式の見出しは持たない（真似する形ではない）
    for example in inspiration:
        assert "integrated_multimodal_description:" not in example.body


# --------------------------------------------------------------------------
# canonical の書式（公式 rewrite 契約に照らして機械的に見られる分だけ）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("example", CANONICAL, ids=[x.id for x in CANONICAL])
def test_a_canonical_example_follows_the_official_format(example):
    body = example.body
    lines = body.splitlines()

    if example.mode in ("r2v", "edit"):
        # 6 セクションが順番どおり行頭に並ぶ
        heads = [
            line for line in lines
            if any(line.startswith(name) for name in REF_SECTIONS)
        ]
        assert [head.split(":")[0] + ":" for head in heads] == list(
            REF_SECTIONS
        ), example.id
    else:
        for field in BASE_FIELDS:
            assert any(line.startswith(field) for line in lines), field
        assert "subject_definitions:" not in body

    # アライン行はモードごとに決まっている
    if example.mode == "i2v":
        assert body.startswith(
            "For the target video, at 0.00 seconds into the target video,"
        )
    elif example.mode in ("fl2v", "l2v"):
        assert body.startswith("How the reference pictures align with the target video")
    else:
        assert "align with the" not in lines[0]

    # `[Shot 1]` にタイムスタンプは無く、以降は厳密に増えるカット時刻
    assert "[Shot 1]" in body
    assert "[Shot 1] At" not in body
    stamps = re.findall(r"\[Shot (\d+)\] At (\d\d):(\d\d)\.(\d{3})", body)
    seconds = [
        int(m) * 60 + int(s) + int(ms) / 1000 for _, m, s, ms in stamps
    ]
    assert seconds == sorted(seconds) and len(seconds) == len(set(seconds))
    assert [int(index) for index, *_ in stamps] == list(
        range(2, len(stamps) + 2)
    )

    # 禁じている書式を持ち込んでいない
    assert "Camera:" not in body
    assert "Audio:" not in body
    assert not re.search(r"\[\d+(\.\d+)?s", body)
    assert "```" not in body

    # 末尾の除外文。画面内の文字が主題の例だけ付けない
    if "ui-text" in example.categories:
        assert CLOSING not in body
    else:
        assert body.rstrip().endswith(CLOSING), example.id


def test_the_dialogue_tags_carry_a_language():
    for example in CANONICAL:
        for tag in re.findall(r"<d>([^<]*)", example.body):
            assert re.match(r"\[[A-Z][a-z]+\] ", tag), (example.id, tag)


# --------------------------------------------------------------------------
# 選択ロジック
# --------------------------------------------------------------------------

def test_select_examples_filters_by_mode_category_and_tier():
    assert all(x.mode == "t2v" for x in select_examples(mode="t2v"))
    assert all(
        "ui-text" in x.categories for x in select_examples(category="ui-text")
    )
    # 既定は canonical だけ。tier=None で生入力も混ざる
    assert all(x.tier == "canonical" for x in select_examples())
    assert len(select_examples(tier=None)) == len(EXAMPLES)
    assert {x.tier for x in select_examples(mode="edit", tier=None)} == {
        "canonical",
        "inspiration",
    }
    # 該当が無ければ空（呼び出し側が「無かった」と言えるように例外にしない）
    assert select_examples(mode="l2v", category="product") == []


def test_select_examples_honours_the_limit_and_explicit_ids():
    assert len(select_examples(limit=2)) == 2
    assert len(select_examples(mode="t2v", limit=1)) == 1
    assert select_examples(limit=0) == []
    # id 指定は並び順もそのまま。知らない id は黙って落ちる
    picked = select_examples(ids=("H3-E7", "H3-E2", "H3-E999"))
    assert [x.id for x in picked] == ["H3-E7", "H3-E2"]


@pytest.mark.parametrize(
    "workflow, expected",
    [
        ("minimax_h3_t2v", ["H3-E2", "H3-E8"]),
        ("minimax_h3_t2v_turbo", ["H3-E2", "H3-E8"]),
        ("minimax_h3_i2v", ["H3-E1", "H3-E4"]),
        ("minimax_h3_i2v_save_opt", ["H3-E1", "H3-E4"]),
        ("minimax_h3_r2v", ["H3-E3", "H3-E7"]),
        ("minimax_h3_r2v_context_turbo", ["H3-E3", "H3-E7"]),
        ("minimax_h3_edit", ["H3-E6", "H3-E3"]),
        ("wan_i2v", list(DEFAULT_IDS)),
        ("", list(DEFAULT_IDS)),
    ],
)
def test_default_examples_for_workflow(workflow, expected):
    assert [x.id for x in default_examples_for_workflow(workflow)] == expected


def test_rendering_keeps_the_ids_and_the_tags_in_the_headings():
    block = render_examples(default_examples_for_workflow("minimax_h3_r2v"))
    assert block.startswith("# FEW-SHOT EXAMPLES")
    assert "## H3-E3 " in block and "## H3-E7 " in block
    assert "[tags: multi-reference, dialogue]" in block
    # 本文はフェンスの中に入る
    assert block.count("```") == 4


def test_the_index_carries_what_the_agent_needs_to_choose():
    index = h3_examples.example_index()
    assert len(index) == len(EXAMPLES)
    assert set(index[0]) == {"id", "mode", "categories", "summary", "tier"}
