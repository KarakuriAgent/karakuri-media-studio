"""キャンバス API: スタジオの中身を並べる別ビュー。

見るのは「カードとスタジオのエンティティがずれないか」に尽きる——キャンバスを
開けばスタジオにあるものが必ずカードになっていること（鏡）、カードを作れば
エンティティもできること、エンティティが消えればカードも消えること、
リビジョンを戻したときに片方だけ残らないこと。中身の編集はスタジオの API の
仕事なので、ここでは扱わない。

環境（DB・assets・ジョブの差し替え）は :mod:`test_studio` のものを借りる。
"""

from test_studio import (  # noqa: F401 - フィクスチャの再エクスポート
    detail,
    env,
    make_asset,
    make_episode,
    make_metadata_asset,
    make_project,
    make_scene,
    make_shot,
    render,
)


# --------------------------------------------------------------------------
# ヘルパー
# --------------------------------------------------------------------------

def make_card(env, project_id: str, kind: str, **body) -> dict:
    response = env.client.post(
        f"/api/canvas/projects/{project_id}/cards", json={"kind": kind, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


def cards(env, project_id: str, episode_id: str | None = None) -> list[dict]:
    """1 タブぶんのカード（``episode_id`` を省くと作品共通タブ）。"""
    params = {"episode_id": episode_id} if episode_id else None
    response = env.client.get(
        f"/api/canvas/projects/{project_id}/cards", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def board_of(env, project_id: str, episode_id: str | None = None) -> dict:
    params = {"episode_id": episode_id} if episode_id else None
    response = env.client.get(f"/api/canvas/projects/{project_id}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def revisions(env, project_id: str) -> list[dict]:
    response = env.client.get(f"/api/studio/projects/{project_id}/revisions")
    assert response.status_code == 200, response.text
    return response.json()


def restore(env, project_id: str, seq: int):
    return env.client.post(
        f"/api/studio/projects/{project_id}/revisions/{seq}/restore"
    )


# --------------------------------------------------------------------------
# カードを置く（エンティティも一緒に作る）
# --------------------------------------------------------------------------

def test_a_character_card_creates_the_asset_behind_it(env):
    project = make_project(env)
    card = make_card(
        env,
        project["id"],
        "character",
        title="Neko",
        data={"appearance": "三毛猫", "voice": "低い"},
        x=120,
        y=40,
    )
    assert card["entity_id"]
    # カードは中身を持たない（拡張項目は素材側に入る）
    assert card["data"] == {}
    assert (card["x"], card["y"]) == (120, 40)

    assets = detail(env, project["id"])["assets"]
    assert [asset["id"] for asset in assets] == [card["entity_id"]]
    assert assets[0]["name"] == "Neko"
    assert assets[0]["category"] == "character"
    assert assets[0]["profile"]["appearance"] == "三毛猫"
    assert assets[0]["profile"]["personality"] == ""


def test_card_kinds_map_onto_the_asset_categories(env):
    project = make_project(env)
    for kind, category in (
        ("location", "environment"),
        ("object", "prop"),
        ("style", "style"),
        ("reference", "reference"),
    ):
        card = make_card(env, project["id"], kind, title=f"{kind}-1")
        assets = {asset["id"]: asset for asset in detail(env, project["id"])["assets"]}
        assert assets[card["entity_id"]]["category"] == category


def test_an_unnamed_asset_card_gets_a_free_name(env):
    project = make_project(env)
    first = make_card(env, project["id"], "character")
    second = make_card(env, project["id"], "character")
    assets = {asset["id"]: asset["name"] for asset in detail(env, project["id"])["assets"]}
    assert assets[first["entity_id"]] == "character_1"
    assert assets[second["entity_id"]] == "character_2"


def test_a_scene_card_lands_in_an_episode_even_without_one(env):
    project = make_project(env)
    card = make_card(env, project["id"], "scene", title="開店前")
    board = detail(env, project["id"])
    assert len(board["episodes"]) == 1  # 話が無ければ 1 つ作られる
    assert [scene["id"] for scene in board["scenes"]] == [card["entity_id"]]
    assert board["scenes"][0]["episode_id"] == board["episodes"][0]["id"]


def test_a_shot_card_can_be_dropped_into_a_scene(env):
    project = make_project(env)
    scene = make_card(env, project["id"], "scene", title="開店前")
    shot = make_card(
        env, project["id"], "shot", title="鍋を火にかける", scene_id=scene["entity_id"]
    )
    shots = detail(env, project["id"])["shots"]
    assert [row["id"] for row in shots] == [shot["entity_id"]]
    assert shots[0]["scene_id"] == scene["entity_id"]
    assert shots[0]["title"] == "鍋を火にかける"


def test_text_and_model_cards_keep_their_own_contents(env):
    project = make_project(env)
    text = make_card(env, project["id"], "text", data={"body": "メモ"})
    assert text["entity_id"] is None
    assert text["data"] == {"body": "メモ"}

    model = make_card(
        env, project["id"], "model", data={"target": "video", "note": "本編用"}
    )
    assert model["data"]["target"] == "video"
    assert model["data"]["params"]["fps"] == 25  # 既定値で埋まる
    # キャンバス専用のカードなので、スタジオ側には何も増えない
    board = detail(env, project["id"])
    assert board["assets"] == [] and board["shots"] == []


def test_a_bad_card_body_is_refused(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/cards",
        json={"kind": "text", "data": {"unknown": 1}},
    )
    assert response.status_code == 400
    assert "text カードの data" in response.json()["detail"]


def test_a_character_card_refuses_a_profile_it_cannot_hold(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/cards",
        json={"kind": "character", "title": "Neko", "data": {"palette": "青"}},
    )
    assert response.status_code == 400
    assert detail(env, project["id"])["assets"] == []


# --------------------------------------------------------------------------
# 鏡（スタジオの中身は開けば必ずカードになっている）
# --------------------------------------------------------------------------

def populated(env) -> dict:
    """素材・場・カット・生成結果がひととおり入った作品（カードは 0 枚）。"""
    project = make_project(env)
    episode = make_episode(env, project["id"], title="第1話")
    scene = make_scene(env, episode["id"], title="屋台")
    return {
        "project": project,
        "episode": episode,
        "scene": scene,
        "asset": make_asset(env, project["id"], "Neko", category="character"),
        "prop": make_metadata_asset(env, project["id"], "Ramen", category="prop"),
        "shots": [
            make_shot(env, project["id"], title="鍋", scene_id=scene["id"]),
            make_shot(env, project["id"], title="暖簾", scene_id=scene["id"]),
        ],
        "loose": make_shot(env, project["id"], title="未分類"),
    }


def test_opening_the_canvas_mirrors_the_whole_studio(env):
    """開いたタブに、その話の中身が必ずカードとして出そろう。"""
    world = populated(env)
    project_id = world["project"]["id"]
    episode_id = world["episode"]["id"]
    take = render(env, world["shots"][0]["id"]).json()

    board = board_of(env, project_id, episode_id)
    assert board["episode_id"] == episode_id
    by_entity = {row["entity_id"]: row for row in board["cards"]}
    assert by_entity[world["scene"]["id"]]["kind"] == "scene"
    assert by_entity[world["shots"][0]["id"]]["kind"] == "shot"
    assert by_entity[take["id"]]["kind"] == "media"
    assert len(board["cards"]) == 4  # 場 1 + カット 2 + 生成結果 1


def test_the_common_tab_holds_the_assets_and_the_loose_shots(env):
    """素材は話に属さず、どの場にも入れていないカットも作品共通に出る。"""
    world = populated(env)
    project_id = world["project"]["id"]

    common = board_of(env, project_id)
    assert common["episode_id"] is None
    by_entity = {row["entity_id"]: row for row in common["cards"]}
    assert by_entity[world["asset"]["id"]]["kind"] == "character"
    assert by_entity[world["prop"]["id"]]["kind"] == "object"
    assert by_entity[world["loose"]["id"]]["kind"] == "shot"
    assert len(common["cards"]) == 3  # 素材 2 + 未分類のカット 1
    # 'common' と省略は同じ意味
    assert cards(env, project_id, "common") == common["cards"]


def test_a_card_moves_to_the_tab_its_scene_belongs_to(env):
    """所属はスタジオから導くので、場を引っ越せばカードもタブを移る。"""
    world = populated(env)
    project_id = world["project"]["id"]
    other = make_episode(env, project_id, title="第2話")

    response = env.client.patch(
        f"/api/studio/scenes/{world['scene']['id']}",
        json={"episode_id": other["id"]},
    )
    assert response.status_code == 200, response.text
    assert cards(env, project_id, world["episode"]["id"]) == []
    moved = {row["entity_id"] for row in cards(env, project_id, other["id"])}
    assert world["scene"]["id"] in moved
    assert {shot["id"] for shot in world["shots"]} <= moved


def test_a_scene_cannot_move_into_another_project(env):
    world = populated(env)
    other = make_project(env, name="別の作品")
    stranger = make_episode(env, other["id"], title="よその第1話")
    response = env.client.patch(
        f"/api/studio/scenes/{world['scene']['id']}",
        json={"episode_id": stranger["id"]},
    )
    assert response.status_code == 400
    assert "話が見つかりません" in response.json()["detail"]


def test_an_unknown_tab_is_404(env):
    project = make_project(env)
    assert env.client.get(
        f"/api/canvas/projects/{project['id']}", params={"episode_id": "NOPE"}
    ).status_code == 404
    assert env.client.get(
        f"/api/canvas/projects/{project['id']}/cards", params={"episode_id": "NOPE"}
    ).status_code == 404


def test_the_mirror_lays_things_out_without_overlapping(env):
    world = populated(env)
    project_id = world["project"]["id"]
    render(env, world["shots"][0]["id"])

    for tab in (None, world["episode"]["id"]):
        spots = [(row["x"], row["y"]) for row in cards(env, project_id, tab)]
        assert len(set(spots)) == len(spots)
        for index, (x, y) in enumerate(spots):
            for other_x, other_y in spots[index + 1:]:
                assert abs(x - other_x) >= 320 or abs(y - other_y) >= 220

    # 作品共通は素材が左の縦列、未分類のカットはその右
    common = {row["entity_id"]: row for row in cards(env, project_id)}
    assert common[world["asset"]["id"]]["x"] == 0
    assert common[world["prop"]["id"]]["x"] == 0
    assert common[world["loose"]["id"]]["x"] > 0

    # 話タブは場が左、その所属カットは sort_order 順に横へ（同じ行に載る）
    placed = {
        row["entity_id"]: row
        for row in cards(env, project_id, world["episode"]["id"])
    }
    assert placed[world["scene"]["id"]]["x"] == 0
    first, second = (placed[shot["id"]] for shot in world["shots"])
    assert first["x"] > 0
    assert second["x"] > first["x"] and second["y"] == first["y"]


def test_mirroring_only_fills_in_what_is_missing(env):
    world = populated(env)
    project_id = world["project"]["id"]
    before = cards(env, project_id)
    assert cards(env, project_id) == before  # 何度開いても増えない

    # 手で動かしたカードは動かさない
    moved = before[0]
    env.client.put(
        f"/api/canvas/cards/{moved['id']}/position", json={"x": 999, "y": -999}
    )
    # スタジオ側に足したものは、次に開けば出る
    added = make_metadata_asset(env, project_id, "Yatai", category="environment")
    after = {row["entity_id"]: row for row in cards(env, project_id)}
    assert len(after) == len(before) + 1
    assert after[added["id"]]["kind"] == "location"
    assert (after[moved["entity_id"]]["x"], after[moved["entity_id"]]["y"]) == (
        999, -999
    )


def test_mirroring_is_not_an_edit(env):
    """自動配置は派生状態の生成なので、履歴には残さない。"""
    world = populated(env)
    project_id = world["project"]["id"]
    before = len(revisions(env, project_id))
    assert cards(env, project_id)  # ここで鏡が走る
    assert len(revisions(env, project_id)) == before


def test_only_the_current_take_of_a_shot_gets_a_card(env):
    """Take は試した回数ぶん増えるので、出すのは「いまの結果」1 件だけ。"""
    project = make_project(env)
    shot = make_shot(env, project["id"])
    first = render(env, shot["id"]).json()
    second = render(env, shot["id"]).json()

    media = [row for row in cards(env, project["id"]) if row["kind"] == "media"]
    assert [row["entity_id"] for row in media] == [second["id"]]

    # 採用した Take があればそちらを映す（前に映したカードもそのまま残る）
    env.client.post(f"/api/studio/takes/{first['id']}/select")
    media = [row for row in cards(env, project["id"]) if row["kind"] == "media"]
    assert sorted(row["entity_id"] for row in media) == sorted(
        [first["id"], second["id"]]
    )


def test_a_media_card_cannot_be_created_by_hand(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/cards", json={"kind": "media"}
    )
    assert response.status_code == 400
    assert "media カードは作れません" in response.json()["detail"]


def test_the_mirror_stays_inside_one_project(env):
    project = make_project(env)
    other = make_project(env, name="別の作品")
    make_asset(env, other["id"], "Neko", category="character")
    assert cards(env, project["id"]) == []


def test_cards_need_a_project(env):
    response = env.client.post(
        "/api/canvas/projects/NOPE/cards", json={"kind": "text"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------
# 動かす・書き換える
# --------------------------------------------------------------------------

def test_moving_a_card_touches_nothing_else(env):
    project = make_project(env)
    card = make_card(env, project["id"], "character", title="Neko")
    before = len(revisions(env, project["id"]))

    response = env.client.put(
        f"/api/canvas/cards/{card['id']}/position",
        json={"x": 400, "y": -120, "z": 7},
    )
    assert response.status_code == 200, response.text
    moved = response.json()
    assert (moved["x"], moved["y"], moved["z"]) == (400, -120, 7)
    assert moved["w"] == card["w"]  # 送らなかったものは動かない
    # 置き場所は作品の状態ではないので履歴には残さない
    assert len(revisions(env, project["id"])) == before
    assert cards(env, project["id"])[0]["x"] == 400


def test_only_canvas_only_cards_can_be_edited_here(env):
    project = make_project(env)
    text = make_card(env, project["id"], "text", data={"body": "メモ"})
    patched = env.client.patch(
        f"/api/canvas/cards/{text['id']}", json={"data": {"body": "書き直し"}, "h": 90}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"] == {"body": "書き直し"}
    assert patched.json()["h"] == 90

    card = make_card(env, project["id"], "character", title="Neko")
    refused = env.client.patch(
        f"/api/canvas/cards/{card['id']}", json={"data": {"appearance": "白猫"}}
    )
    assert refused.status_code == 400
    assert "スタジオの API" in refused.json()["detail"]


def test_missing_cards_are_404(env):
    assert env.client.patch("/api/canvas/cards/NOPE", json={"z": 1}).status_code == 404
    assert env.client.put(
        "/api/canvas/cards/NOPE/position", json={"x": 0, "y": 0}
    ).status_code == 404
    assert env.client.delete("/api/canvas/cards/NOPE").status_code == 404


# --------------------------------------------------------------------------
# 消す（カードだけ / エンティティごと）
# --------------------------------------------------------------------------

def test_a_reference_card_cannot_be_dropped_on_its_own(env):
    """カードはスタジオの写しなので、カードだけ消しても鏡がすぐ戻す。"""
    project = make_project(env)
    card = make_card(env, project["id"], "character", title="Neko")
    response = env.client.delete(f"/api/canvas/cards/{card['id']}")
    assert response.status_code == 400
    assert "ごと削除" in response.json()["detail"]
    assert [row["id"] for row in cards(env, project["id"])] == [card["id"]]


def test_a_canvas_only_card_is_just_removed(env):
    project = make_project(env)
    card = make_card(env, project["id"], "text", data={"body": "メモ"})
    assert env.client.delete(f"/api/canvas/cards/{card['id']}").status_code == 204
    assert cards(env, project["id"]) == []


def test_deleting_a_card_with_its_entity_takes_both(env):
    project = make_project(env)
    card = make_card(env, project["id"], "shot", title="鍋を火にかける")
    response = env.client.delete(
        f"/api/canvas/cards/{card['id']}?delete_entity=true"
    )
    assert response.status_code == 204
    assert cards(env, project["id"]) == []
    assert detail(env, project["id"])["shots"] == []


def test_deleting_the_entity_in_the_studio_removes_the_card(env):
    project = make_project(env)
    character = make_card(env, project["id"], "character", title="Neko")
    shot = make_card(env, project["id"], "shot")
    text = make_card(env, project["id"], "text", data={"body": "メモ"})

    assert env.client.delete(
        f"/api/studio/assets/{character['entity_id']}"
    ).status_code == 204
    assert env.client.delete(
        f"/api/studio/shots/{shot['entity_id']}"
    ).status_code == 204
    # 参照カードだけが消え、キャンバス専用のカードは残る
    assert [row["id"] for row in cards(env, project["id"])] == [text["id"]]


def test_a_scene_card_goes_when_its_episode_does(env):
    project = make_project(env)
    scene = make_card(env, project["id"], "scene", title="開店前")
    episode_id = detail(env, project["id"])["episodes"][0]["id"]
    assert env.client.delete(
        f"/api/studio/episodes/{episode_id}"
    ).status_code == 204
    # 場は話ごと（ON DELETE CASCADE）消えるので、カードも残らない
    assert cards(env, project["id"]) == []
    assert scene["entity_id"] not in [
        row["id"] for row in detail(env, project["id"])["scenes"]
    ]


def test_a_media_card_goes_when_its_take_does(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    assert [row["kind"] for row in cards(env, project["id"])] == ["shot", "media"]
    assert env.client.delete(f"/api/studio/takes/{take['id']}").status_code == 204
    # Take のカードだけが消える（カットは作品に残っているので映り続ける）
    assert [row["kind"] for row in cards(env, project["id"])] == ["shot"]


def test_deleting_the_project_takes_the_canvas(env):
    project = make_project(env)
    make_card(env, project["id"], "text", data={"body": "メモ"})
    env.client.post(
        f"/api/canvas/projects/{project['id']}/messages", json={"content": "やあ"}
    )
    assert env.client.delete(f"/api/studio/projects/{project['id']}").status_code == 204
    assert env.client.get(f"/api/canvas/projects/{project['id']}").status_code == 404


# --------------------------------------------------------------------------
# 表示位置とキャンバス 1 枚
# --------------------------------------------------------------------------

def test_the_viewport_is_remembered_on_the_project(env):
    project = make_project(env)
    board = board_of(env, project["id"])
    assert board["viewport"] == {"x": 0.0, "y": 0.0, "zoom": 1.0}

    response = env.client.put(
        f"/api/canvas/projects/{project['id']}/viewport",
        json={"x": -80, "y": 20, "zoom": 1.5},
    )
    assert response.status_code == 200, response.text
    board = board_of(env, project["id"])
    assert board["viewport"] == {"x": -80.0, "y": 20.0, "zoom": 1.5}
    assert env.client.put(
        "/api/canvas/projects/NOPE/viewport", json={"x": 0, "y": 0, "zoom": 1}
    ).status_code == 404


def test_each_tab_keeps_its_own_viewport(env):
    project = make_project(env)
    episode = make_episode(env, project["id"], title="第1話")
    response = env.client.put(
        f"/api/canvas/projects/{project['id']}/viewport",
        params={"episode_id": episode["id"]},
        json={"x": 300, "y": -40, "zoom": 0.5},
    )
    assert response.status_code == 200, response.text
    assert board_of(env, project["id"], episode["id"])["viewport"] == {
        "x": 300.0, "y": -40.0, "zoom": 0.5
    }
    # 作品共通のタブは動いていない
    assert board_of(env, project["id"])["viewport"] == {
        "x": 0.0, "y": 0.0, "zoom": 1.0
    }
    assert env.client.put(
        f"/api/canvas/projects/{project['id']}/viewport",
        params={"episode_id": "NOPE"},
        json={"x": 0, "y": 0, "zoom": 1},
    ).status_code == 404


def test_a_note_stays_on_the_tab_it_was_placed_on(env):
    """text / model カードは所属を導けないので、置いたタブを覚える。"""
    project = make_project(env)
    episode = make_episode(env, project["id"], title="第1話")
    note = make_card(
        env, project["id"], "text", data={"body": "この話の山場"},
        episode_id=episode["id"],
    )
    common_note = make_card(env, project["id"], "text", data={"body": "作品メモ"})

    assert note["episode_id"] == episode["id"]
    assert common_note["episode_id"] is None
    assert [row["id"] for row in cards(env, project["id"], episode["id"])] == [
        note["id"]
    ]
    assert [row["id"] for row in cards(env, project["id"])] == [common_note["id"]]


def test_a_note_cannot_be_placed_on_another_projects_tab(env):
    project = make_project(env)
    other = make_project(env, name="別の作品")
    stranger = make_episode(env, other["id"], title="よその第1話")
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/cards",
        json={"kind": "text", "episode_id": stranger["id"]},
    )
    assert response.status_code == 400
    assert "話が見つかりません" in response.json()["detail"]


def test_a_note_falls_back_to_the_common_tab_when_its_episode_goes(env):
    project = make_project(env)
    episode = make_episode(env, project["id"], title="第1話")
    note = make_card(
        env, project["id"], "text", data={"body": "メモ"}, episode_id=episode["id"]
    )
    assert env.client.delete(
        f"/api/studio/episodes/{episode['id']}"
    ).status_code == 204
    assert [row["id"] for row in cards(env, project["id"])] == [note["id"]]


def test_the_board_carries_cards_and_messages(env):
    project = make_project(env)
    card = make_card(env, project["id"], "text", data={"body": "メモ"})
    env.client.post(
        f"/api/canvas/projects/{project['id']}/messages", json={"content": "やあ"}
    )
    board = env.client.get(f"/api/canvas/projects/{project['id']}").json()
    assert [row["id"] for row in board["cards"]] == [card["id"]]
    assert [row["content"] for row in board["messages"]] == ["やあ"]


# --------------------------------------------------------------------------
# 会話
# --------------------------------------------------------------------------

def test_messages_are_kept_in_order(env):
    project = make_project(env)
    for role, content in (("user", "冒頭を書いて"), ("assistant", "書きました")):
        response = env.client.post(
            f"/api/canvas/projects/{project['id']}/messages",
            json={"role": role, "content": content},
        )
        assert response.status_code == 201, response.text
    listed = env.client.get(
        f"/api/canvas/projects/{project['id']}/messages"
    ).json()
    assert [(row["role"], row["content"]) for row in listed] == [
        ("user", "冒頭を書いて"),
        ("assistant", "書きました"),
    ]


def test_canvas_sessions_can_be_created_and_listed(env):
    project = make_project(env)
    env.client.post(
        f"/api/canvas/projects/{project['id']}/messages", json={"content": "やあ"}
    )
    listed = env.client.get(f"/api/canvas/projects/{project['id']}/sessions").json()
    assert len(listed) == 1
    created = env.client.post(
        f"/api/canvas/projects/{project['id']}/sessions", json={"title": "別案"}
    )
    assert created.status_code == 201
    listed = env.client.get(f"/api/canvas/projects/{project['id']}/sessions").json()
    assert {row["title"] for row in listed} >= {"別案"}
    empty = env.client.get(
        f"/api/canvas/projects/{project['id']}",
        params={"session_id": created.json()["id"]},
    ).json()
    assert empty["messages"] == []


def test_an_empty_message_is_refused(env):
    project = make_project(env)
    response = env.client.post(
        f"/api/canvas/projects/{project['id']}/messages", json={"content": "   "}
    )
    assert response.status_code == 422
    assert env.client.post(
        "/api/canvas/projects/NOPE/messages", json={"content": "やあ"}
    ).status_code == 404


# --------------------------------------------------------------------------
# 素材の拡張項目（profile）
# --------------------------------------------------------------------------

def test_the_profile_is_edited_through_the_studio_api(env):
    project = make_project(env)
    card = make_card(env, project["id"], "character", title="Neko")
    response = env.client.patch(
        f"/api/studio/assets/{card['entity_id']}",
        json={"profile": {"appearance": "三毛猫", "personality": "気まぐれ"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["personality"] == "気まぐれ"

    refused = env.client.patch(
        f"/api/studio/assets/{card['entity_id']}", json={"profile": {"palette": "青"}}
    )
    assert refused.status_code == 400
    assert "profile" in refused.json()["detail"]


def test_changing_the_category_drops_what_no_longer_fits(env):
    project = make_project(env)
    card = make_card(env, project["id"], "character", title="Neko")
    env.client.patch(
        f"/api/studio/assets/{card['entity_id']}",
        json={"profile": {"appearance": "三毛猫", "notes": "看板猫"}},
    )
    response = env.client.patch(
        f"/api/studio/assets/{card['entity_id']}", json={"category": "style"}
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile == {"palette": "", "references": [], "notes": "看板猫"}


def test_a_new_asset_gets_the_empty_shape_of_its_category(env):
    """拡張項目を送らなくても、分類のスキーマの形（空の値）で入る。"""
    project = make_project(env)
    asset = make_metadata_asset(env, project["id"], "Neko", category="character")
    assert asset["profile"] == {
        "appearance": "", "personality": "", "voice": "", "notes": "",
    }
    prop = make_metadata_asset(env, project["id"], "Ramen", category="prop")
    assert prop["profile"] == {"notes": ""}


# --------------------------------------------------------------------------
# リビジョンとの整合
# --------------------------------------------------------------------------

def test_a_revision_carries_the_cards_and_the_viewport(env):
    project = make_project(env)
    make_card(env, project["id"], "text", data={"body": "メモ"})
    env.client.put(
        f"/api/canvas/projects/{project['id']}/viewport",
        json={"x": 10, "y": 20, "zoom": 2},
    )
    seq = revisions(env, project["id"])[0]["seq"]
    snapshot = env.client.get(
        f"/api/studio/projects/{project['id']}/revisions/{seq}"
    ).json()["snapshot"]
    assert [row["kind"] for row in snapshot["cards"]] == ["text"]


def test_restoring_puts_the_canvas_back_with_its_entities(env):
    project = make_project(env)
    kept = make_card(env, project["id"], "text", data={"body": "メモ"})
    seq = revisions(env, project["id"])[0]["seq"]

    later = make_card(env, project["id"], "character", title="Neko")
    assert len(cards(env, project["id"])) == 2

    assert restore(env, project["id"], seq).status_code == 200
    # あとから足したカードも、その素材も消える（片方だけ残らない）
    assert [row["id"] for row in cards(env, project["id"])] == [kept["id"]]
    assert [
        asset["id"] for asset in detail(env, project["id"])["assets"]
    ] == []
    assert later["entity_id"] not in [
        asset["id"] for asset in detail(env, project["id"])["assets"]
    ]


def test_restoring_brings_a_deleted_card_and_entity_back_together(env):
    project = make_project(env)
    card = make_card(env, project["id"], "shot", title="鍋を火にかける")
    seq = revisions(env, project["id"])[0]["seq"]
    assert env.client.delete(
        f"/api/canvas/cards/{card['id']}?delete_entity=true"
    ).status_code == 204

    assert restore(env, project["id"], seq).status_code == 200
    restored = cards(env, project["id"])
    shots = detail(env, project["id"])["shots"]
    assert [row["entity_id"] for row in restored] == [card["entity_id"]]
    assert [row["id"] for row in shots] == [card["entity_id"]]


def test_a_card_is_not_restored_when_its_entity_is_gone_for_good(env):
    project = make_project(env)
    shot = make_shot(env, project["id"])
    take = render(env, shot["id"]).json()
    assert len(cards(env, project["id"])) == 2  # 鏡がカットと生成結果を映す
    seq = revisions(env, project["id"])[0]["seq"]

    # Take は履歴の対象外（復元しても戻らない）ので、それを指すカードも戻さない
    assert env.client.delete(f"/api/studio/takes/{take['id']}").status_code == 204
    assert restore(env, project["id"], seq).status_code == 200
    assert [row["kind"] for row in cards(env, project["id"])] == ["shot"]
