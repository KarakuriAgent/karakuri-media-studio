import { describe, expect, it } from 'vitest'
import type {
  CanvasCard,
  CanvasCardKind,
  CanvasMessage,
  StudioAsset,
  StudioProjectDetail,
  StudioScene,
  StudioShot,
  StudioTake,
} from '../../types'
import {
  CARD_KINDS,
  KIND_CATEGORY,
  KIND_LABEL,
  MAX_ZOOM,
  MIN_ZOOM,
  PROFILE_FIELDS,
  appendMessage,
  applyMention,
  arrangeCards,
  assetMedia,
  boardToScreen,
  canvasTabs,
  cardEpisode,
  cardSummary,
  cardTitle,
  cardsInTab,
  clampZoom,
  defaultDataFor,
  dropCard,
  entityEpisodes,
  entityOf,
  fitViewport,
  freeSpot,
  isDangling,
  isLooseShot,
  isStandalone,
  jobSignature,
  mentionCandidates,
  messageAttachments,
  messageText,
  runningLabel,
  mentionQueryAt,
  modelDataOf,
  panBy,
  profileForm,
  profilePayload,
  screenToBoard,
  shotsInScene,
  takeMedia,
  topZ,
  upsertCard,
  viewCenter,
  zoomAt,
} from './logic'

function card(
  id: string,
  kind: CanvasCardKind,
  overrides: Partial<CanvasCard> = {},
): CanvasCard {
  return {
    id,
    project_id: 'p1',
    kind,
    entity_id: null,
    episode_id: null,
    data: {},
    x: 0,
    y: 0,
    w: 320,
    h: 220,
    z: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function asset(id: string, overrides: Partial<StudioAsset> = {}): StudioAsset {
  return {
    id,
    project_id: 'p1',
    name: id,
    category: 'character',
    caption: '',
    prompt_caption: '',
    kind: 'image',
    path: '',
    url: '',
    locked: false,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function scene(id: string, overrides: Partial<StudioScene> = {}): StudioScene {
  return {
    id,
    episode_id: 'e1',
    project_id: 'p1',
    sort_order: 0,
    title: '',
    synopsis: '',
    time_of_day: '',
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function shot(id: string, overrides: Partial<StudioShot> = {}): StudioShot {
  return {
    id,
    project_id: 'p1',
    scene_id: null,
    sort_order: 0,
    title: '',
    purpose: '',
    action: '',
    dialogue: '',
    soundscape: '',
    bgm: '',
    camera: '',
    duration_seconds: 5,
    prompt: '',
    status: 'draft',
    selected_take_id: null,
    carry_over_end_frame: false,
    nsfw: false,
    aspect_ratio: null,
    megapixels: null,
    seed: null,
    workflow_override: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function take(id: string, overrides: Partial<StudioTake> = {}): StudioTake {
  return {
    id,
    shot_id: 's1',
    project_id: 'p1',
    job_id: 'j1',
    status: 'candidate',
    job_status: 'done',
    video_workflow: null,
    video_path: null,
    video_url: null,
    last_frame_path: null,
    last_frame_url: null,
    error: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function detailOf(overrides: Partial<StudioProjectDetail> = {}): StudioProjectDetail {
  return {
    id: 'p1',
    name: '作品',
    code: '',
    synopsis: '',
    world_notes: '',
    auto_translate: true,
  latent_continuity: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    assets: [],
    episodes: [],
    scenes: [],
    shots: [],
    takes: [],
    ...overrides,
  }
}

describe('座標変換', () => {
  const viewport = { x: 100, y: 50, zoom: 2 }

  it('画面 -> ボード -> 画面 で元に戻る', () => {
    const point = { x: 321, y: 654 }
    const board = screenToBoard(point, viewport)
    expect(boardToScreen(board, viewport)).toEqual(point)
  })

  it('ボード座標は原点と倍率を打ち消した位置になる', () => {
    expect(screenToBoard({ x: 100, y: 50 }, viewport)).toEqual({ x: 0, y: 0 })
    expect(boardToScreen({ x: 10, y: 10 }, viewport)).toEqual({ x: 120, y: 70 })
  })

  it('zoom が 0 のときも壊れない（1 として扱う）', () => {
    expect(screenToBoard({ x: 4, y: 4 }, { x: 0, y: 0, zoom: 0 })).toEqual({
      x: 4,
      y: 4,
    })
  })

  it('流し見は原点だけを動かす', () => {
    expect(panBy(viewport, 10, -20)).toEqual({ x: 110, y: 30, zoom: 2 })
  })

  it('ズームはピボットのボード座標を動かさない', () => {
    const start = { x: 100, y: 50, zoom: 1 }
    const pivot = { x: 400, y: 300 }
    const before = screenToBoard(pivot, start)
    const next = zoomAt(start, 1.5, pivot)
    const after = screenToBoard(pivot, next)
    expect(next.zoom).toBeCloseTo(1.5)
    expect(after.x).toBeCloseTo(before.x)
    expect(after.y).toBeCloseTo(before.y)
  })

  it('倍率は上下の限界で止まる', () => {
    expect(clampZoom(99)).toBe(MAX_ZOOM)
    expect(clampZoom(0.01)).toBe(MIN_ZOOM)
    expect(clampZoom(Number.NaN)).toBe(1)
    expect(zoomAt(viewport, 100, { x: 0, y: 0 }).zoom).toBe(MAX_ZOOM)
  })

  it('見えている範囲の中心をボード座標で返す', () => {
    expect(viewCenter({ width: 800, height: 600 }, { x: 0, y: 0, zoom: 2 })).toEqual({
      x: 200,
      y: 150,
    })
  })

  it('全体を収める表示位置は、はみ出したぶんだけ縮小する', () => {
    const cards = [
      card('c1', 'character', { x: 0, y: 0, w: 320, h: 220 }),
      card('c2', 'shot', { x: 1080, y: 780, w: 320, h: 220 }),
    ]
    const size = { width: 800, height: 600 }
    const view = fitViewport(cards, size)
    expect(view.zoom).toBeLessThan(1)
    expect(view.zoom).toBeGreaterThanOrEqual(MIN_ZOOM)
    // 端のカードが両方とも画面の中に入る
    for (const item of cards) {
      const topLeft = boardToScreen({ x: item.x, y: item.y }, view)
      const bottomRight = boardToScreen(
        { x: item.x + item.w, y: item.y + item.h },
        view,
      )
      expect(topLeft.x).toBeGreaterThanOrEqual(0)
      expect(topLeft.y).toBeGreaterThanOrEqual(0)
      expect(bottomRight.x).toBeLessThanOrEqual(size.width)
      expect(bottomRight.y).toBeLessThanOrEqual(size.height)
    }
  })

  it('全体を収めても等倍より拡大はしない（カードが無ければ原点）', () => {
    const cards = [card('c1', 'text', { x: 0, y: 0, w: 320, h: 220 })]
    expect(fitViewport(cards, { width: 1600, height: 1200 }).zoom).toBe(1)
    expect(fitViewport([], { width: 800, height: 600 })).toEqual({
      x: 0,
      y: 0,
      zoom: 1,
    })
  })
})

describe('カードの並べ方', () => {
  it('置きたい場所が埋まっていたらずらす', () => {
    const cards = [card('c1', 'text', { x: 0, y: 0 })]
    expect(freeSpot(cards, { x: 0, y: 0 }, 32)).toEqual({ x: 32, y: 32 })
    expect(freeSpot(cards, { x: 500, y: 500 }, 32)).toEqual({ x: 500, y: 500 })
  })

  it('整列は種別 -> 置いた順で格子に並べる', () => {
    const cards = [
      card('note', 'text', { created_at: '2026-01-01T00:00:00Z' }),
      card('hero', 'character', { created_at: '2026-01-03T00:00:00Z' }),
      card('sub', 'character', { created_at: '2026-01-02T00:00:00Z' }),
    ]
    const layout = arrangeCards(cards, {
      columns: 2,
      gapX: 100,
      gapY: 200,
      origin: { x: 10, y: 20 },
    })
    expect(layout).toEqual([
      { id: 'sub', x: 10, y: 20 },
      { id: 'hero', x: 110, y: 20 },
      { id: 'note', x: 10, y: 220 },
    ])
  })

  it('整列は元の配列を書き換えない', () => {
    const cards = [card('c2', 'text'), card('c1', 'character')]
    arrangeCards(cards)
    expect(cards.map((item) => item.id)).toEqual(['c2', 'c1'])
  })

  it('重なり順・差し替え・取り除きの小道具', () => {
    const cards = [card('c1', 'text', { z: 3 }), card('c2', 'text', { z: 7 })]
    expect(topZ(cards)).toBe(7)
    expect(topZ([])).toBe(0)
    const updated = upsertCard(cards, card('c2', 'text', { z: 9 }))
    expect(updated).toHaveLength(2)
    expect(updated[1].z).toBe(9)
    expect(upsertCard(cards, card('c3', 'text'))).toHaveLength(3)
    expect(dropCard(cards, 'c1').map((item) => item.id)).toEqual(['c2'])
  })
})

describe('カードの中身', () => {
  const detail = detailOf({
    assets: [asset('a1', { name: 'アキ', caption: '主人公', category: 'character' })],
    scenes: [scene('sc1', { title: '路地', synopsis: '雨上がり' })],
    shots: [
      shot('sh1', { title: 'カット1', scene_id: 'sc1', prompt: '歩く' }),
      shot('sh2', { title: 'カット2' }),
    ],
    takes: [take('t1', { shot_id: 'sh1', video_url: '/outputs/a.mp4' })],
  })

  it('参照先を種別ごとに引く', () => {
    expect(entityOf(card('c1', 'character', { entity_id: 'a1' }), detail)).toEqual(
      detail.assets[0],
    )
    expect(entityOf(card('c2', 'scene', { entity_id: 'sc1' }), detail)).toEqual(
      detail.scenes[0],
    )
    expect(entityOf(card('c3', 'shot', { entity_id: 'sh1' }), detail)).toEqual(
      detail.shots[0],
    )
    expect(entityOf(card('c4', 'media', { entity_id: 't1' }), detail)).toEqual(
      detail.takes[0],
    )
    expect(entityOf(card('c5', 'text'), detail)).toBeNull()
  })

  it('見出しはスタジオ側の名前を使う', () => {
    expect(cardTitle(card('c1', 'character', { entity_id: 'a1' }), detail)).toBe('@アキ')
    expect(cardTitle(card('c2', 'scene', { entity_id: 'sc1' }), detail)).toBe('路地')
    expect(cardTitle(card('c3', 'shot', { entity_id: 'sh2' }), detail)).toBe('カット2')
    expect(cardTitle(card('c4', 'media', { entity_id: 't1' }), detail)).toBe('カット1')
    expect(
      cardTitle(card('c5', 'text', { data: { body: '覚え書き\n続き' } }), detail),
    ).toBe('覚え書き')
    expect(cardTitle(card('c6', 'shot', { entity_id: 'missing' }), detail)).toBe(
      '（参照先がありません）',
    )
  })

  it('要約は種別ごとに拾う場所が変わる', () => {
    expect(cardSummary(card('c1', 'character', { entity_id: 'a1' }), detail)).toBe(
      '主人公',
    )
    expect(cardSummary(card('c2', 'scene', { entity_id: 'sc1' }), detail)).toBe(
      '雨上がり',
    )
    expect(cardSummary(card('c3', 'shot', { entity_id: 'sh1' }), detail)).toBe('歩く')
    expect(
      cardSummary(card('c4', 'model', { data: { target: 'video' } }), detail),
    ).toBe('動画 / ワークフロー未選択')
  })

  it('参照先が消えたカードが分かる', () => {
    expect(isDangling(card('c1', 'shot', { entity_id: 'nope' }), detail)).toBe(true)
    expect(isDangling(card('c2', 'shot', { entity_id: 'sh1' }), detail)).toBe(false)
    expect(isDangling(card('c3', 'text'), detail)).toBe(false)
  })

  it('場に属するカットだけ並べる', () => {
    expect(shotsInScene(detail, 'sc1').map((item) => item.id)).toEqual(['sh1'])
  })

  it('プレビューは動画 -> ラストフレームの順に選ぶ', () => {
    expect(takeMedia(detail.takes[0])).toEqual({
      kind: 'video',
      url: '/outputs/a.mp4',
    })
    expect(takeMedia(take('t2', { last_frame_url: '/outputs/a.png' }))).toEqual({
      kind: 'image',
      url: '/outputs/a.png',
    })
    expect(takeMedia(take('t3'))).toBeNull()
    expect(assetMedia(asset('a2', { url: '/assets/image/a.png' }))).toEqual({
      kind: 'image',
      url: '/assets/image/a.png',
    })
    expect(assetMedia(asset('a3'))).toBeNull()
  })

  it('model カードの data は既定値の上に重ねて読む', () => {
    const data = modelDataOf(
      card('c1', 'model', { data: { target: 'video', params: { fps: 30 } } }),
    )
    expect(data.target).toBe('video')
    expect(data.params.fps).toBe(30)
    // 送られていない項目は既定値のまま（古いカードでも壊れない）
    expect(data.params.megapixels).toBe(1.0)
    expect(data.params.loras).toEqual([])
  })

  it('新規カードの空 data はキャンバス専用の 2 種だけ', () => {
    expect(defaultDataFor('text')).toEqual({ body: '' })
    expect(defaultDataFor('character')).toEqual({})
    expect(isStandalone('model')).toBe(true)
    expect(isStandalone('media')).toBe(false)
  })
})

describe('カードの種別', () => {
  it('素材カードの種別は素材の分類と対応する', () => {
    expect(KIND_CATEGORY.location).toBe('environment')
    expect(KIND_CATEGORY.object).toBe('prop')
    expect(KIND_CATEGORY.text).toBeUndefined()
    // 「＋」メニューの並びは全種別を過不足なく持つ
    expect(new Set(CARD_KINDS).size).toBe(Object.keys(KIND_LABEL).length)
  })
})

describe('タブ（作品共通 + 話ごと）', () => {
  const world = detailOf({
    episodes: [
      { id: 'e1', project_id: 'p1', sort_order: 0, title: '第1話', synopsis: '', created_at: '2026-01-01T00:00:00Z' },
      { id: 'e2', project_id: 'p1', sort_order: 1, title: '', synopsis: '', created_at: '2026-01-01T00:00:00Z' },
    ],
    scenes: [scene('sc1', { episode_id: 'e1' })],
    shots: [shot('s1', { scene_id: 'sc1' }), shot('s2')],
    takes: [take('t1', { shot_id: 's1' }), take('t2', { shot_id: 's2' })],
  })
  const index = entityEpisodes(world)

  it('作品共通のあとに話が並ぶ（無題の話は通し番号で呼ぶ）', () => {
    expect(canvasTabs(world)).toEqual([
      { episodeId: null, label: '作品共通' },
      { episodeId: 'e1', label: '第1話' },
      { episodeId: 'e2', label: '第 2 話' },
    ])
  })

  it('所属はスタジオから導く（場 -> 話、カット -> 場、生成物 -> カット）', () => {
    expect(index).toEqual({ sc1: 'e1', s1: 'e1', s2: null, t1: 'e1', t2: null })
  })

  it('素材と未分類のカットは作品共通、その話のものは話タブに出る', () => {
    const cards = [
      card('c1', 'character', { entity_id: 'a1' }),
      card('c2', 'scene', { entity_id: 'sc1' }),
      card('c3', 'shot', { entity_id: 's1' }),
      card('c4', 'shot', { entity_id: 's2' }),
      card('c5', 'media', { entity_id: 't1' }),
      card('c6', 'text'),
      card('c7', 'text', { episode_id: 'e1' }),
    ]
    expect(cardsInTab(cards, index, null).map((item) => item.id)).toEqual([
      'c1', 'c4', 'c6',
    ])
    expect(cardsInTab(cards, index, 'e1').map((item) => item.id)).toEqual([
      'c2', 'c3', 'c5', 'c7',
    ])
    expect(cardsInTab(cards, index, 'e2')).toEqual([])
  })

  it('メモの置き場所はカードが覚えている（参照カードは見ない）', () => {
    expect(cardEpisode(card('c1', 'text', { episode_id: 'e2' }), index)).toBe('e2')
    // 参照カードの episode_id は使わない（所属はスタジオが決める）
    const shotCard = card('c2', 'shot', { entity_id: 's1', episode_id: 'e2' })
    expect(cardEpisode(shotCard, index)).toBe('e1')
  })

  it('場に入れていないカットだけ「未分類」と分かる', () => {
    expect(isLooseShot(card('c1', 'shot', { entity_id: 's2' }), world)).toBe(true)
    expect(isLooseShot(card('c2', 'shot', { entity_id: 's1' }), world)).toBe(false)
    expect(isLooseShot(card('c3', 'text'), world)).toBe(false)
  })
})

describe('素材の拡張項目', () => {
  it('分類のスキーマにある項目だけをフォームに出す', () => {
    const form = profileForm(
      asset('a1', {
        category: 'character',
        profile: { appearance: '赤い上着', unknown: '捨てる' },
      }),
    )
    expect(form).toEqual({
      appearance: '赤い上着',
      personality: '',
      voice: '',
      notes: '',
    })
  })

  it('参照画像は 1 行 1 件で読み書きする', () => {
    const form = profileForm(
      asset('a2', {
        category: 'style',
        profile: { palette: '寒色', references: ['/library/a.png', '/library/b.png'] },
      }),
    )
    expect(form.references).toBe('/library/a.png\n/library/b.png')
    expect(profilePayload('style', form)).toEqual({
      palette: '寒色',
      references: ['/library/a.png', '/library/b.png'],
      notes: '',
    })
  })

  it('送る profile は分類の全項目を埋める（丸ごと置き換わるため）', () => {
    expect(profilePayload('environment', { mood: '夕暮れ' })).toEqual({
      mood: '夕暮れ',
      notes: '',
    })
    expect(Object.keys(PROFILE_FIELDS.prop)).toHaveLength(1)
  })
})

describe('@ 素材メンション', () => {
  it('カーソル直前の @ 語だけを拾う', () => {
    expect(mentionQueryAt('こんにちは @ア', 8)).toEqual({ start: 6, query: 'ア' })
    expect(mentionQueryAt('@アキ', 3)).toEqual({ start: 0, query: 'アキ' })
    // 直前が空白でない（メールアドレスなど）は補完しない
    expect(mentionQueryAt('a@b', 3)).toBeNull()
    // 語の途中に空白が入ったら閉じる
    expect(mentionQueryAt('@アキ と', 5)).toBeNull()
    expect(mentionQueryAt('ふつうの文', 5)).toBeNull()
  })

  it('確定は @ から カーソルまでを置き換える', () => {
    expect(applyMention('こんにちは @ア', 6, 8, 'アキ')).toEqual({
      text: 'こんにちは @アキ ',
      caret: 10,
    })
  })

  it('候補は素材名の部分一致（上限つき）', () => {
    const assets = [asset('a1', { name: 'aki' }), asset('a2', { name: 'yume' })]
    expect(mentionCandidates(assets, 'ak').map((item) => item.name)).toEqual(['aki'])
    expect(mentionCandidates(assets, '')).toHaveLength(2)
    expect(mentionCandidates(assets, '', 1)).toHaveLength(1)
  })
})

describe('チャットの履歴', () => {
  const message = (id: string, content: string): CanvasMessage => ({
    id,
    project_id: 'p1',
    ts: '2026-01-01T00:00:00Z',
    role: 'assistant',
    content,
    kind: null,
    data: {},
  })

  it('同じ id は積まずに上書きする（WS と応答の二重取り）', () => {
    const first = appendMessage([], message('m1', 'はい'))
    expect(first.map((item) => item.content)).toEqual(['はい'])
    const again = appendMessage(first, message('m1', 'はい（訂正）'))
    expect(again.map((item) => item.content)).toEqual(['はい（訂正）'])
    expect(appendMessage(again, message('m2', '次'))).toHaveLength(2)
  })

  it('実行中の表示は活動が分かればそれを出す', () => {
    expect(runningLabel('ツール実行中: ls')).toContain('ツール実行中: ls')
    expect(runningLabel(null)).toContain('エージェント')
  })
})

describe('盤面の取り直しの合図', () => {
  const frame = (jobId: string, status: string) => ({
    type: 'job' as const,
    job_id: jobId,
    status: status as 'queued',
    node: null,
    progress: null,
    message: null,
    nsfw: null,
  })

  it('状態が動いたときだけ変わる（進捗の % では変わらない）', () => {
    const before = jobSignature({ j1: frame('j1', 'running') })
    expect(
      jobSignature({ j1: { ...frame('j1', 'running'), progress: 0.5 } }),
    ).toBe(before)
    expect(jobSignature({ j1: frame('j1', 'succeeded') })).not.toBe(before)
    // 新しいジョブ（＝ 新しい Take）が現れたときも変わる
    expect(
      jobSignature({ j1: frame('j1', 'running'), j2: frame('j2', 'queued') }),
    ).not.toBe(before)
    // 並び順には依らない
    expect(
      jobSignature({ j2: frame('j2', 'queued'), j1: frame('j1', 'running') }),
    ).toBe(jobSignature({ j1: frame('j1', 'running'), j2: frame('j2', 'queued') }))
  })
})

describe('添付つきの発言', () => {
  const said = (data: Record<string, unknown>): CanvasMessage => ({
    id: 'm1',
    project_id: 'p1',
    ts: '2026-01-01T00:00:00Z',
    role: 'user',
    content: 'この声で\n\n[Attached files]\n- /tmp/koe.wav（audio / koe.wav）',
    kind: null,
    data,
  })

  it('画面には本文だけ、添付は別に取り出す', () => {
    const message = said({
      text: 'この声で',
      attachments: [
        { path: 'attachments/koe.wav', name: 'koe.wav', kind: 'audio' },
      ],
    })
    expect(messageText(message)).toBe('この声で')
    expect(messageAttachments(message)).toEqual([
      {
        path: 'attachments/koe.wav',
        name: 'koe.wav',
        abs_path: '',
        kind: 'audio',
      },
    ])
  })

  it('添付の無い発言はそのままの本文（壊れた data は無視する）', () => {
    expect(messageText(said({}))).toContain('[Attached files]')
    expect(messageAttachments(said({ attachments: ['x', null] }))).toEqual([])
  })
})
