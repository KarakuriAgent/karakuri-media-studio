import type { NodeChange } from '@xyflow/react'
import { describe, expect, it } from 'vitest'
import type {
  CanvasNode,
  CanvasNodeKind,
  CanvasProgress,
  CanvasProjectDetail,
  CanvasStoryboardData,
} from '../../types'
import {
  CANVAS_KINDS,
  KIND_ICON,
  KIND_LABEL,
  MENTION_LIMIT,
  applyMention,
  cardSummary,
  defaultDataFor,
  fromNodeChange,
  mentionCandidates,
  mentionQueryAt,
  mergeFlowNodes,
  messageText,
  nextDraggingIds,
  nextGenerating,
  shouldReplaceProject,
  toFlowNode,
  type CardFlowNode,
} from './logic'

function node(
  kind: CanvasNodeKind,
  data: Record<string, unknown> = {},
  overrides: Partial<CanvasNode> = {},
): CanvasNode {
  return {
    id: 'node-1',
    project_id: 'project-1',
    created_at: '2026-08-04T00:00:00+00:00',
    updated_at: '2026-08-04T00:00:00+00:00',
    kind,
    title: 'カード',
    data: { ...(defaultDataFor(kind) as unknown as Record<string, unknown>), ...data },
    x: 10,
    y: 20,
    w: 320,
    h: 220,
    z: 3,
    ...overrides,
  }
}

function project(overrides: Partial<CanvasProjectDetail> = {}): CanvasProjectDetail {
  return {
    id: 'project-1',
    created_at: '2026-08-04T00:00:00+00:00',
    updated_at: '2026-08-04T00:00:00+00:00',
    title: 'PV 企画',
    llm: 'grok',
    viewport: { x: 0, y: 0, zoom: 1 },
    nodes: [],
    messages: [],
    thinking: false,
    ...overrides,
  }
}

describe('defaultDataFor', () => {
  it('全 kind に空の data を返す', () => {
    for (const kind of CANVAS_KINDS) {
      const data = defaultDataFor(kind)
      expect(data).toBeTypeOf('object')
      expect(Object.keys(data).length).toBeGreaterThan(0)
    }
  })

  it('model カードは params を丸ごと持つ', () => {
    const data = defaultDataFor('model')
    expect(data).toMatchObject({ target: 'image', workflow: '' })
    expect('params' in data && data.params.fps).toBe(25)
  })

  it('毎回新しいオブジェクトを返す（カード間で共有しない）', () => {
    const first = defaultDataFor('character')
    const second = defaultDataFor('character')
    expect(first).not.toBe(second)
  })

  it('全 kind にラベルとアイコンがある', () => {
    for (const kind of CANVAS_KINDS) {
      expect(KIND_LABEL[kind]).toBeTruthy()
      expect(KIND_ICON[kind]).toBeTruthy()
    }
  })
})

describe('toFlowNode', () => {
  it('位置・大きさ・重なりを React Flow のノードへ写す', () => {
    const flow = toFlowNode(node('text', { body: 'メモ' }))
    expect(flow).toMatchObject({
      id: 'node-1',
      type: 'card',
      position: { x: 10, y: 20 },
      width: 320,
      height: 220,
      zIndex: 3,
    })
    expect(flow.data.node.kind).toBe('text')
  })
})

describe('fromNodeChange', () => {
  const position = (
    overrides: Partial<Extract<NodeChange<CardFlowNode>, { type: 'position' }>> = {},
  ): NodeChange<CardFlowNode> => ({
    type: 'position',
    id: 'node-1',
    position: { x: 100, y: 200 },
    dragging: false,
    ...overrides,
  })

  it('確定した位置だけを返す', () => {
    expect(fromNodeChange(position())).toEqual({ id: 'node-1', x: 100, y: 200 })
  })

  it('ドラッグ中は捨てる', () => {
    expect(fromNodeChange(position({ dragging: true }))).toBeNull()
  })

  it('位置を持たない変更は捨てる', () => {
    expect(fromNodeChange(position({ position: undefined }))).toBeNull()
    expect(
      fromNodeChange({ type: 'select', id: 'node-1', selected: true }),
    ).toBeNull()
  })
})

describe('mergeFlowNodes', () => {
  const card = (id: string, x: number, y: number, title = 'カード') =>
    node('text', {}, { id, x, y, title })

  it('ドラッグ中のカードはサーバーの座標で巻き戻さない', () => {
    const current: CardFlowNode[] = [
      { ...toFlowNode(card('a', 10, 20)), position: { x: 500, y: 600 } },
    ]
    const merged = mergeFlowNodes(current, [card('a', 10, 20)], new Set(['a']))
    expect(merged[0].position).toEqual({ x: 500, y: 600 })
  })

  it('ドラッグ中でも中身と大きさはサーバー値で更新する', () => {
    const current: CardFlowNode[] = [
      { ...toFlowNode(card('a', 10, 20)), position: { x: 500, y: 600 } },
    ]
    const incoming = node(
      'text',
      { body: '更新後' },
      { id: 'a', x: 10, y: 20, title: '新しい題', w: 400, h: 300, z: 9 },
    )
    const merged = mergeFlowNodes(current, [incoming], new Set(['a']))
    expect(merged[0].data.node.title).toBe('新しい題')
    expect(merged[0].data.node.data).toMatchObject({ body: '更新後' })
    expect(merged[0]).toMatchObject({ width: 400, height: 300, zIndex: 9 })
  })

  it('ドラッグしていないカードはサーバーの座標で置き換える', () => {
    const current: CardFlowNode[] = [
      { ...toFlowNode(card('a', 10, 20)), position: { x: 500, y: 600 } },
    ]
    const merged = mergeFlowNodes(current, [card('a', 10, 20)], new Set())
    expect(merged[0].position).toEqual({ x: 10, y: 20 })
  })

  it('選択状態は引き継ぐ', () => {
    const current: CardFlowNode[] = [
      { ...toFlowNode(card('a', 10, 20)), selected: true },
      { ...toFlowNode(card('b', 30, 40)), selected: false },
    ]
    const merged = mergeFlowNodes(
      current,
      [card('a', 10, 20), card('b', 30, 40)],
      new Set(),
    )
    expect(merged.map((item) => item.selected)).toEqual([true, false])
  })

  it('サーバーにしかないカードは足し、消えたカードは落とす', () => {
    const current: CardFlowNode[] = [toFlowNode(card('a', 10, 20))]
    const merged = mergeFlowNodes(current, [card('b', 30, 40)], new Set(['a']))
    expect(merged.map((item) => item.id)).toEqual(['b'])
    expect(merged[0].position).toEqual({ x: 30, y: 40 })
  })
})

describe('nextDraggingIds', () => {
  const position = (
    id: string,
    dragging: boolean,
  ): NodeChange<CardFlowNode> => ({
    type: 'position',
    id,
    position: { x: 1, y: 2 },
    dragging,
  })

  it('ドラッグ開始で足し、終了で外す', () => {
    const started = nextDraggingIds(new Set(), [position('a', true)])
    expect([...started]).toEqual(['a'])
    expect([...nextDraggingIds(started, [position('a', false)])]).toEqual([])
  })

  it('消えたカードは落とす', () => {
    const current = new Set(['a'])
    const next = nextDraggingIds(current, [{ type: 'remove', id: 'a' }])
    expect(next.size).toBe(0)
  })

  it('関係のない変更では変えない（元の集合も壊さない）', () => {
    const current = new Set(['a'])
    const next = nextDraggingIds(current, [
      { type: 'select', id: 'b', selected: true },
    ])
    expect([...next]).toEqual(['a'])
    expect([...current]).toEqual(['a'])
  })
})

describe('cardSummary', () => {
  it('style は説明、無ければパレット', () => {
    expect(cardSummary(node('style', { description: '水彩\n2行目' }))).toBe('水彩')
    expect(cardSummary(node('style', { palette: '青緑' }))).toBe('青緑')
  })

  it('character は紹介、無ければ外見', () => {
    expect(cardSummary(node('character', { appearance: 'silver hair' }))).toBe(
      'silver hair',
    )
    expect(cardSummary(node('character', { description: 'ヒロイン' }))).toBe('ヒロイン')
  })

  it('location / object は説明を出す', () => {
    expect(cardSummary(node('location', { mood: '夕暮れ' }))).toBe('夕暮れ')
    expect(cardSummary(node('object', { description: '赤い傘' }))).toBe('赤い傘')
  })

  it('script はシーン数を添える', () => {
    expect(
      cardSummary(
        node('script', {
          synopsis: '青春もの',
          scenes: [{ no: 1, heading: '屋上', body: '' }],
        }),
      ),
    ).toBe('青春もの（1 シーン）')
    expect(cardSummary(node('script'))).toBe('0 シーン')
  })

  it('storyboard はカット数', () => {
    const data: CanvasStoryboardData = {
      notes: '',
      cuts: [
        {
          no: 1,
          scene: '',
          description: '',
          camera: '',
          audio: '',
          duration: null,
          prompt: '',
          image: '',
        },
      ],
    }
    expect(cardSummary(node('storyboard', data as unknown as Record<string, unknown>))).toBe(
      '1 カット',
    )
  })

  it('media はキャプション → プロンプト → URL の順', () => {
    expect(cardSummary(node('media', { url: '/outputs/a.png' }))).toBe('/outputs/a.png')
    expect(
      cardSummary(node('media', { url: '/outputs/a.png', caption: '完成カット' })),
    ).toBe('完成カット')
  })

  it('text は本文の 1 行目', () => {
    expect(cardSummary(node('text', { body: '1 行目\n2 行目' }))).toBe('1 行目')
  })

  it('model は対象とワークフロー', () => {
    expect(cardSummary(node('model', { target: 'video', workflow: 'ltx2_3_t2v' }))).toBe(
      'video / ltx2_3_t2v',
    )
    expect(cardSummary(node('model'))).toBe('image / 未選択')
  })

  it('空のカードでも例外にならない', () => {
    for (const kind of CANVAS_KINDS) {
      expect(() => cardSummary(node(kind))).not.toThrow()
    }
  })
})

describe('shouldReplaceProject', () => {
  const message = (id: string) => ({
    id,
    project_id: 'project-1',
    ts: '2026-08-04T00:00:00+00:00',
    role: 'user' as const,
    content: 'hi',
    kind: null,
    data: {},
  })

  it('未取得・別キャンバスなら差し替える', () => {
    expect(shouldReplaceProject(null, project())).toBe(true)
    expect(
      shouldReplaceProject(project({ id: 'other' }), project()),
    ).toBe(true)
  })

  it('記録が減る差し替えは捨てる', () => {
    const current = project({
      messages: [message('m1'), message('m2')],
      nodes: [node('text')],
    })
    expect(
      shouldReplaceProject(current, project({ messages: [message('m1')], nodes: [node('text')] })),
    ).toBe(false)
    expect(
      shouldReplaceProject(
        current,
        project({ messages: [message('m1'), message('m2')], nodes: [] }),
      ),
    ).toBe(false)
  })

  it('増えていれば差し替える', () => {
    const current = project({ messages: [message('m1')] })
    expect(
      shouldReplaceProject(
        current,
        project({ messages: [message('m1'), message('m2')] }),
      ),
    ).toBe(true)
  })
})

// ------------------------------------------------------------------ @ 参照

describe('mentionQueryAt', () => {
  it('行頭の @ から補完する', () => {
    expect(mentionQueryAt('@ヒロ', 3)).toEqual({ start: 0, query: 'ヒロ' })
  })

  it('文中でも直前が空白なら補完する', () => {
    const text = 'この案は @ヒロ'
    expect(mentionQueryAt(text, text.length)).toEqual({ start: 5, query: 'ヒロ' })
  })

  it('@ の直後（語がまだ無い）でも開く', () => {
    expect(mentionQueryAt('@', 1)).toEqual({ start: 0, query: '' })
  })

  it('@ とカーソルのあいだに空白があれば補完しない', () => {
    expect(mentionQueryAt('@ヒロイン のカット', 10)).toBeNull()
  })

  it('直前が空白でない @（メールアドレス等）は補完しない', () => {
    expect(mentionQueryAt('foo@bar', 7)).toBeNull()
  })

  it('@ が無ければ補完しない', () => {
    expect(mentionQueryAt('ふつうの文', 5)).toBeNull()
  })

  it('カーソルより後ろの @ は見ない', () => {
    expect(mentionQueryAt('あとで @ヒロイン', 3)).toBeNull()
  })

  it('日本語タイトルの続きでも補完し続ける', () => {
    const text = '@夜の屋上'
    expect(mentionQueryAt(text, text.length)).toEqual({ start: 0, query: '夜の屋上' })
  })
})

describe('applyMention', () => {
  it('入力中の語をタイトルに置き換えて空白を足す', () => {
    const text = 'この案は @ヒロ'
    const hit = mentionQueryAt(text, text.length)!
    const next = applyMention(text, hit.start, text.length, 'ヒロイン')
    expect(next.text).toBe('この案は @ヒロイン ')
    expect(next.caret).toBe(next.text.length)
  })

  it('カーソルより後ろの文字は残す', () => {
    const text = '@ヒロ の登場カット'
    const next = applyMention(text, 0, 3, '夜の屋上')
    expect(next.text).toBe('@夜の屋上  の登場カット')
    expect(next.caret).toBe('@夜の屋上 '.length)
  })
})

describe('mentionCandidates', () => {
  const nodes = [
    node('character', {}, { id: 'n1', title: 'ヒロイン' }),
    node('location', {}, { id: 'n2', title: '夜の屋上' }),
    node('text', {}, { id: 'n3', title: '' }),
    node('style', {}, { id: 'n4', title: 'ヒロインの衣装' }),
  ]

  it('タイトルの部分一致で絞る（日本語）', () => {
    expect(mentionCandidates(nodes, 'ヒロ').map((n) => n.id)).toEqual(['n1', 'n4'])
  })

  it('タイトルの無いカードは候補に出さない', () => {
    expect(mentionCandidates(nodes, '').map((n) => n.id)).toEqual(['n1', 'n2', 'n4'])
  })

  it('英字は大文字小文字を区別しない', () => {
    const list = [node('text', {}, { id: 'e1', title: 'Krea2 Model' })]
    expect(mentionCandidates(list, 'krea').map((n) => n.id)).toEqual(['e1'])
  })

  it('候補は 8 件まで', () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      node('text', {}, { id: `m${i}`, title: `カード${i}` }),
    )
    expect(mentionCandidates(many, 'カード')).toHaveLength(MENTION_LIMIT)
  })
})

describe('messageText', () => {
  const base = {
    id: 'm1',
    project_id: 'project-1',
    ts: '2026-08-04T00:00:00+00:00',
    kind: null,
  }

  it('ユーザー発言は展開前の元発言を出す', () => {
    expect(
      messageText({
        ...base,
        role: 'user',
        content: '@ヒロイン の案\n\n[Referenced cards — full contents]\n…',
        data: { text: '@ヒロイン の案', mentions: ['n1'] },
      }),
    ).toBe('@ヒロイン の案')
  })

  it('控えが無ければ content をそのまま出す', () => {
    expect(
      messageText({ ...base, role: 'assistant', content: '了解しました', data: {} }),
    ).toBe('了解しました')
  })
})

describe('nextGenerating', () => {
  const frame = (event: string): CanvasProgress => ({
    type: 'canvas',
    project_id: 'p1',
    event,
    node_id: null,
    job_id: null,
    thinking: null,
    message: null,
  })

  it('job_started で走り出す', () => {
    expect(nextGenerating(false, frame('job_started'))).toBe(true)
  })

  it('job_done / job_failed / generate_failed で止まる', () => {
    expect(nextGenerating(true, frame('job_done'))).toBe(false)
    expect(nextGenerating(true, frame('job_failed'))).toBe(false)
    expect(nextGenerating(true, frame('generate_failed'))).toBe(false)
  })

  it('関係のないイベントでは変わらない', () => {
    expect(nextGenerating(true, frame('node_created'))).toBe(true)
    expect(nextGenerating(false, frame('message'))).toBe(false)
  })
})
