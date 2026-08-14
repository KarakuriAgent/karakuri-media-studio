import { describe, expect, it } from 'vitest'
import type {
  StudioAsset,
  StudioEpisode,
  StudioProjectDetail,
  StudioScene,
  StudioShot,
  StudioTake,
} from '../../types'
import {
  assetHasFile,
  assetKindFromFile,
  assetNameFromFile,
  buildShotTree,
  formatProjectSettingsSummary,
  isStale,
  moveId,
  moveShot,
  projectSummary,
  renderingJobIds,
  sceneOptions,
  selectedTakeOf,
  renderFormFromShot,
  renderRequestFromForm,
  shotFormFromShot,
  shotUpdateFromForm,
  splitMentions,
  staleTooltip,
  takeActivityLabel,
  takesByShot,
  unresolvedMentions,
  validateProjectForm,
  validateRenderForm,
  validateShotForm,
} from './studio'

function shot(id: string, overrides: Partial<StudioShot> = {}): StudioShot {
  return {
    id,
    project_id: 'p1',
    scene_id: null,
    sort_order: 0,
    title: id,
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
    aspect_ratio: null,
    megapixels: null,
    seed: null,
    workflow_override: null,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function episode(id: string, overrides: Partial<StudioEpisode> = {}): StudioEpisode {
  return {
    id,
    project_id: 'p1',
    sort_order: 0,
    title: '',
    synopsis: '',
    created_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function scene(
  id: string,
  episodeId: string,
  overrides: Partial<StudioScene> = {},
): StudioScene {
  return {
    id,
    episode_id: episodeId,
    project_id: 'p1',
    sort_order: 0,
    title: '',
    synopsis: '',
    time_of_day: '',
    created_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function take(id: string, overrides: Partial<StudioTake> = {}): StudioTake {
  return {
    id,
    shot_id: 's1',
    project_id: 'p1',
    job_id: `job-${id}`,
    status: 'candidate',
    created_at: '2026-01-01T00:00:00+00:00',
    job_status: 'done',
    video_workflow: 'minimax_h3_t2v',
    video_path: `/outputs/${id}.mp4`,
    video_url: `/outputs/${id}.mp4`,
    last_frame_path: null,
    last_frame_url: null,
    error: null,
    ...overrides,
  }
}

function asset(name: string, overrides: Partial<StudioAsset> = {}): StudioAsset {
  return {
    id: `a-${name}`,
    project_id: 'p1',
    name,
    category: 'character',
    caption: '',
    prompt_caption: '',
    kind: 'image',
    path: `/assets/image/${name}.png`,
    url: `/assets/image/${name}.png`,
    locked: false,
    sort_order: 0,
    created_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

describe('moveShot', () => {
  const shots = [shot('a'), shot('b'), shot('c')]

  it('入れ替えた並びを返す', () => {
    expect(moveShot(shots, 'b', -1)).toEqual(['b', 'a', 'c'])
    expect(moveShot(shots, 'b', 1)).toEqual(['a', 'c', 'b'])
  })

  it('端では null（動かさない）', () => {
    expect(moveShot(shots, 'a', -1)).toBeNull()
    expect(moveShot(shots, 'c', 1)).toBeNull()
  })

  it('居ない id は null', () => {
    expect(moveShot(shots, 'zzz', 1)).toBeNull()
  })

  it('元の配列は書き換えない', () => {
    moveShot(shots, 'a', 1)
    expect(shots.map((item) => item.id)).toEqual(['a', 'b', 'c'])
  })
})

describe('moveId', () => {
  it('話や場の並べ替えにもそのまま使える', () => {
    expect(moveId(['e1', 'e2', 'e3'], 'e3', -1)).toEqual(['e1', 'e3', 'e2'])
    expect(moveId(['e1', 'e2'], 'e1', -1)).toBeNull()
    expect(moveId(['e1'], 'zzz', 1)).toBeNull()
  })
})

describe('buildShotTree', () => {
  const tree = () =>
    buildShotTree({
      episodes: [episode('e1', { title: '第一夜' }), episode('e2')],
      scenes: [
        scene('sc1', 'e1', { title: '路地' }),
        scene('sc2', 'e1'),
        scene('sc3', 'e2'),
      ],
      shots: [
        shot('s1', { scene_id: 'sc1' }),
        shot('s2', { scene_id: null }),
        shot('s3', { scene_id: 'sc3' }),
        shot('s4', { scene_id: 'sc1' }),
        // 既に消えた場を指している Shot は未分類に落とす
        shot('s5', { scene_id: 'gone' }),
      ],
    })

  it('話 -> 場 -> Shot に束ねる', () => {
    const built = tree()
    expect(built.episodes.map((node) => node.episode.id)).toEqual(['e1', 'e2'])
    expect(built.episodes[0].scenes.map((node) => node.scene.id)).toEqual([
      'sc1',
      'sc2',
    ])
    expect(built.episodes[0].scenes[0].shots.map((node) => node.shot.id)).toEqual([
      's1',
      's4',
    ])
    expect(built.episodes[0].scenes[1].shots).toEqual([])
    expect(built.episodes[1].scenes[0].shots.map((node) => node.shot.id)).toEqual([
      's3',
    ])
  })

  it('場に属さない Shot と、消えた場を指す Shot は未分類', () => {
    expect(tree().unassigned.map((node) => node.shot.id)).toEqual(['s2', 's5'])
  })

  it('通し番号はプロジェクト全体の並び順のまま', () => {
    const built = tree()
    expect(built.episodes[0].scenes[0].shots.map((node) => node.index)).toEqual([0, 3])
    expect(built.unassigned.map((node) => node.index)).toEqual([1, 4])
  })

  it('配下の Shot 数を話ごとに数える', () => {
    expect(tree().episodes.map((node) => node.shotCount)).toEqual([2, 1])
  })

  it('話も場もなければ全部が未分類', () => {
    const built = buildShotTree({
      episodes: [],
      scenes: [],
      shots: [shot('s1'), shot('s2')],
    })
    expect(built.episodes).toEqual([])
    expect(built.unassigned.map((node) => node.shot.id)).toEqual(['s1', 's2'])
  })
})

describe('sceneOptions', () => {
  it('「話 / 場」のラベルで並べる（無題は通し番号）', () => {
    expect(
      sceneOptions({
        episodes: [episode('e1', { title: '第一夜' }), episode('e2')],
        scenes: [scene('sc1', 'e1', { title: '路地' }), scene('sc2', 'e2')],
      }),
    ).toEqual([
      { id: 'sc1', label: '第一夜 / 路地' },
      { id: 'sc2', label: '第 2 話 / 場 1' },
    ])
  })
})

describe('isStale / staleTooltip', () => {
  it('理由つきの Take は古びている', () => {
    const target = take('t1', {
      stale: true,
      stale_reasons: ['脚本が更新されました', '素材『アキ』が更新されました'],
    })
    expect(isStale(target)).toBe(true)
    expect(staleTooltip(target)).toBe(
      '作り直しをおすすめします: 脚本が更新されました / 素材『アキ』が更新されました',
    )
  })

  it('理由だけ来ていても古びている扱いにする', () => {
    expect(isStale(take('t1', { stale_reasons: ['脚本が更新されました'] }))).toBe(true)
  })

  it('印が無ければ何も出さない', () => {
    expect(isStale(take('t1'))).toBe(false)
    expect(staleTooltip(take('t1'))).toBe('')
  })
})

describe('assetHasFile', () => {
  it('path も url も空ならメタデータのみ', () => {
    expect(assetHasFile(asset('アキ'))).toBe(true)
    expect(assetHasFile(asset('設定', { path: '', url: '' }))).toBe(false)
  })
})

describe('takesByShot / selectedTakeOf', () => {
  it('Shot ごとに束ねる（サーバーの並びのまま）', () => {
    const grouped = takesByShot([
      take('t1', { shot_id: 's1' }),
      take('t2', { shot_id: 's2' }),
      take('t3', { shot_id: 's1' }),
    ])
    expect(Object.keys(grouped).sort()).toEqual(['s1', 's2'])
    expect(grouped.s1.map((item) => item.id)).toEqual(['t1', 't3'])
  })

  it('selected_take_id が指すものを返す', () => {
    const target = shot('s1', { selected_take_id: 't2' })
    const takes = [take('t1'), take('t2', { status: 'selected' })]
    expect(selectedTakeOf(target, takes)?.id).toBe('t2')
  })

  it('指し先が消えていれば状態 selected の Take に落ちる', () => {
    const target = shot('s1', { selected_take_id: 'gone' })
    const takes = [take('t1'), take('t2', { status: 'selected' })]
    expect(selectedTakeOf(target, takes)?.id).toBe('t2')
  })

  it('採用が無ければ null', () => {
    expect(selectedTakeOf(shot('s1'), [take('t1')])).toBeNull()
  })

  it('他の Shot の Take は拾わない', () => {
    const takes = [take('t1', { shot_id: 's2', status: 'selected' })]
    expect(selectedTakeOf(shot('s1'), takes)).toBeNull()
  })
})

describe('projectSummary / renderingJobIds', () => {
  const detail: StudioProjectDetail = {
    id: 'p1',
    name: '作品',
    code: '',
    synopsis: '',
    world_notes: '',
    auto_translate: true,
    latent_continuity: false,
    quality: 'normal',
    megapixels: null,
    aspect_ratio: null,
    steps: 0,
    nsfw: false,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    assets: [asset('アキ'), asset('路地')],
    episodes: [],
    scenes: [],
    shots: [shot('s1', { duration_seconds: 5 }), shot('s2', { duration_seconds: 7.5 })],
    takes: [
      take('t1', { status: 'selected' }),
      take('t2', { status: 'rendering', job_status: 'running' }),
      take('t3'),
    ],
  }

  it('件数と合計の尺を数える', () => {
    expect(projectSummary(detail)).toEqual({
      shots: 2,
      assets: 2,
      takes: 3,
      selectedTakes: 1,
      totalSeconds: 12.5,
    })
  })

  it('生成中の Take の job_id だけ返す', () => {
    expect(renderingJobIds(detail.takes)).toEqual(['job-t2'])
  })
})

describe('takeActivityLabel', () => {
  it('rendering 中は進捗メッセージを優先する', () => {
    expect(
      takeActivityLabel(take('t1', { status: 'rendering' }), { message: '英訳作成中' }),
    ).toBe('英訳作成中')
  })

  it('メッセージが無ければ状態ラベル', () => {
    expect(takeActivityLabel(take('t1', { status: 'rendering' }))).toBe('生成中')
    expect(
      takeActivityLabel(take('t1', { status: 'failed' }), { message: '英訳作成中' }),
    ).toBe('失敗')
  })
})

describe('validateProjectForm', () => {
  it('作品名は必須', () => {
    expect(validateProjectForm({ name: '  ' })).toEqual({ name: '作品名は必須です' })
    expect(validateProjectForm({ name: '夜明け' })).toEqual({})
  })
})

describe('validateShotForm', () => {
  const base = shotFormFromShot(shot('s1', { prompt: 'a street at night' }))

  it('埋まっていれば通る', () => {
    expect(validateShotForm(base)).toEqual({})
  })

  it('尺は 1〜15 秒', () => {
    expect(validateShotForm({ ...base, duration_seconds: '0.5' })).toHaveProperty(
      'duration_seconds',
    )
    expect(validateShotForm({ ...base, duration_seconds: '16' })).toHaveProperty(
      'duration_seconds',
    )
    expect(validateShotForm({ ...base, duration_seconds: '15' })).toEqual({})
  })

  it('数値でない尺を弾く', () => {
    expect(validateShotForm({ ...base, duration_seconds: '' })).toHaveProperty(
      'duration_seconds',
    )
    expect(validateShotForm({ ...base, duration_seconds: 'ごびょう' })).toHaveProperty(
      'duration_seconds',
    )
  })

  it('プロンプトもアクションも空なら弾く', () => {
    expect(validateShotForm({ ...base, prompt: '' })).toHaveProperty('prompt')
    // アクションだけでも本文にはなるので通す
    expect(validateShotForm({ ...base, prompt: '', action: '歩き出す' })).toEqual({})
  })

  it('解像度は空欄か正の数', () => {
    expect(validateShotForm({ ...base, megapixels: '' })).toEqual({})
    expect(validateShotForm({ ...base, megapixels: '1.5' })).toEqual({})
    expect(validateShotForm({ ...base, megapixels: '0' })).toHaveProperty('megapixels')
    expect(validateShotForm({ ...base, megapixels: 'おおきめ' })).toHaveProperty(
      'megapixels',
    )
  })

  it('シードは空欄か整数', () => {
    expect(validateShotForm({ ...base, seed: '' })).toEqual({})
    expect(validateShotForm({ ...base, seed: '42' })).toEqual({})
    expect(validateShotForm({ ...base, seed: '1.5' })).toHaveProperty('seed')
  })
})

describe('renderFormFromShot / renderRequestFromForm', () => {
  const unset = { megapixels: null, aspect_ratio: null, steps: 0 }

  it('何も設定が無ければ尺だけが埋まる', () => {
    const form = renderFormFromShot(shot('s1', { duration_seconds: 5 }), unset)
    expect(form).toEqual({
      megapixels: '',
      aspect_ratio: '',
      duration: '5',
      steps: '',
      fixed_seed: false,
      seed: '',
    })
  })

  it('解像度とステップ数はプロジェクトの設定をプレフィルする', () => {
    const form = renderFormFromShot(shot('s1'), {
      megapixels: 1,
      aspect_ratio: '16:9 (Widescreen)',
      steps: 12,
    })
    expect(form).toMatchObject({
      megapixels: '1',
      aspect_ratio: '16:9 (Widescreen)',
      steps: '12',
    })
  })

  it('カット個別の設定はプロジェクトより優先してプレフィルする', () => {
    const form = renderFormFromShot(
      shot('s1', { megapixels: 0.5, aspect_ratio: '1:1 (Square)', seed: 7 }),
      { megapixels: 1, aspect_ratio: '16:9 (Widescreen)', steps: 12 },
    )
    expect(form).toMatchObject({
      megapixels: '0.5',
      aspect_ratio: '1:1 (Square)',
      fixed_seed: true,
      seed: '7',
    })
  })

  it('触らなければ従来どおりの投入になる body を作る', () => {
    const form = renderFormFromShot(shot('s1', { duration_seconds: 5 }), unset)
    // 空欄の解像度とシードは送らない（サーバー側の解決に落とす）
    expect(renderRequestFromForm(form)).toEqual({ duration: 5, steps: 0 })
  })

  it('入っている項目だけを型を戻して送る', () => {
    const form = renderFormFromShot(shot('s1'), unset)
    expect(
      renderRequestFromForm({
        ...form,
        megapixels: '0.8',
        aspect_ratio: '16:9 (Widescreen)',
        duration: '9',
        steps: '30',
        fixed_seed: true,
        seed: '4242',
      }),
    ).toEqual({
      megapixels: 0.8,
      aspect_ratio: '16:9 (Widescreen)',
      duration: 9,
      steps: 30,
      seed: 4242,
    })
  })

  it('シードの固定を外せば seed を送らない（= 毎回ランダム）', () => {
    const form = renderFormFromShot(shot('s1', { seed: 7 }), unset)
    const body = renderRequestFromForm({ ...form, fixed_seed: false })
    expect(body.seed).toBeUndefined()
  })
})

describe('validateRenderForm', () => {
  const base = renderFormFromShot(shot('s1'), {
    megapixels: null,
    aspect_ratio: null,
    steps: 0,
  })

  it('既定のままなら通る', () => {
    expect(validateRenderForm(base)).toEqual({})
  })

  it('尺は 1〜15 秒', () => {
    expect(validateRenderForm({ ...base, duration: '0.5' })).toHaveProperty(
      'duration',
    )
    expect(validateRenderForm({ ...base, duration: '16' })).toHaveProperty(
      'duration',
    )
    expect(validateRenderForm({ ...base, duration: '' })).toHaveProperty('duration')
    expect(validateRenderForm({ ...base, duration: '15' })).toEqual({})
  })

  it('ステップ数は空欄か 0〜150 の整数', () => {
    expect(validateRenderForm({ ...base, steps: '' })).toEqual({})
    expect(validateRenderForm({ ...base, steps: '0' })).toEqual({})
    expect(validateRenderForm({ ...base, steps: '150' })).toEqual({})
    expect(validateRenderForm({ ...base, steps: '151' })).toHaveProperty('steps')
    expect(validateRenderForm({ ...base, steps: '-1' })).toHaveProperty('steps')
    expect(validateRenderForm({ ...base, steps: '1.5' })).toHaveProperty('steps')
  })

  it('解像度は空欄か正の数', () => {
    expect(validateRenderForm({ ...base, megapixels: '' })).toEqual({})
    expect(validateRenderForm({ ...base, megapixels: '0' })).toHaveProperty(
      'megapixels',
    )
  })

  it('固定シードは整数が要る（ランダムなら空欄でよい）', () => {
    expect(validateRenderForm({ ...base, fixed_seed: false, seed: '' })).toEqual({})
    expect(
      validateRenderForm({ ...base, fixed_seed: true, seed: '' }),
    ).toHaveProperty('seed')
    expect(
      validateRenderForm({ ...base, fixed_seed: true, seed: '1.5' }),
    ).toHaveProperty('seed')
    expect(validateRenderForm({ ...base, fixed_seed: true, seed: '42' })).toEqual({})
  })
})

describe('shotFormFromShot / shotUpdateFromForm', () => {
  it('尺を数値に戻して PATCH の body にする', () => {
    const form = shotFormFromShot(shot('s1', { prompt: 'x', duration_seconds: 5 }))
    const patch = shotUpdateFromForm({ ...form, duration_seconds: '7.5' })
    expect(patch.duration_seconds).toBe(7.5)
    expect(patch.prompt).toBe('x')
    expect(patch.carry_over_end_frame).toBe(false)
  })

  it('未設定の生成設定は空文字で持つ', () => {
    const form = shotFormFromShot(shot('s1'))
    expect(form).toMatchObject({
      scene_id: '',
      aspect_ratio: '',
      megapixels: '',
      seed: '',
      workflow_override: '',
    })
  })

  it('設定済みの値は文字列にして持つ', () => {
    const form = shotFormFromShot(
      shot('s1', {
        scene_id: 'sc1',
        aspect_ratio: '16:9 (Widescreen)',
        megapixels: 1.5,
        seed: 0,
        workflow_override: 'minimax_h3_i2v',
      }),
    )
    expect(form).toMatchObject({
      scene_id: 'sc1',
      aspect_ratio: '16:9 (Widescreen)',
      megapixels: '1.5',
      // 0 は「未設定」ではないので空文字にしない
      seed: '0',
      workflow_override: 'minimax_h3_i2v',
    })
  })

  it('空欄の生成設定は null を明示して解除する', () => {
    const patch = shotUpdateFromForm(shotFormFromShot(shot('s1', { prompt: 'x' })))
    expect(patch.scene_id).toBeNull()
    expect(patch.aspect_ratio).toBeNull()
    expect(patch.megapixels).toBeNull()
    expect(patch.seed).toBeNull()
    expect(patch.workflow_override).toBeNull()
  })

  it('入っていれば型を戻して送る', () => {
    const form = shotFormFromShot(shot('s1', { prompt: 'x' }))
    const patch = shotUpdateFromForm({
      ...form,
      scene_id: 'sc1',
      aspect_ratio: ' 16:9 (Widescreen) ',
      megapixels: '1.5',
      seed: '42',
      workflow_override: 'minimax_h3_r2v',
    })
    expect(patch).toMatchObject({
      scene_id: 'sc1',
      aspect_ratio: '16:9 (Widescreen)',
      megapixels: 1.5,
      seed: 42,
      workflow_override: 'minimax_h3_r2v',
    })
  })
})

describe('splitMentions', () => {
  const assets = [asset('アキ'), asset('Aki'), asset('Akira'), asset('路地 裏')]

  it('メンションを素材に結びつける', () => {
    const segments = splitMentions('@アキ が歩く', assets)
    expect(segments[0]).toMatchObject({ text: '@アキ', unresolved: false })
    expect(segments[0].asset?.name).toBe('アキ')
    expect(segments[1]).toMatchObject({ text: ' が歩く', asset: null })
  })

  it('長い名前を先に見て前方一致の取り違えを避ける', () => {
    expect(splitMentions('@Akira', assets)[0].asset?.name).toBe('Akira')
    expect(splitMentions('@Aki ', assets)[0].asset?.name).toBe('Aki')
  })

  it('@{…} で空白入りの名前も呼べる', () => {
    const segments = splitMentions('@{路地 裏} の奥', assets)
    expect(segments[0].asset?.name).toBe('路地 裏')
    expect(segments[0].text).toBe('@{路地 裏}')
  })

  it('登録の無い名前は unresolved になる', () => {
    const segments = splitMentions('@ユキ が来る', assets)
    expect(segments[0]).toMatchObject({ text: '@ユキ', asset: null, unresolved: true })
  })

  it('メンションの無い文はひとかたまり', () => {
    expect(splitMentions('a quiet street', assets)).toEqual([
      { text: 'a quiet street', asset: null, unresolved: false },
    ])
  })

  it('空文字は何も返さない', () => {
    expect(splitMentions('', assets)).toEqual([])
  })

  it('末尾の裸の @ でも止まらない', () => {
    const segments = splitMentions('street @', assets)
    expect(segments[segments.length - 1]).toMatchObject({ text: '@', unresolved: true })
  })

  it('未解決だけを取り出せる', () => {
    expect(unresolvedMentions('@アキ と @ユキ', assets)).toEqual(['@ユキ'])
    expect(unresolvedMentions('@アキ', assets)).toEqual([])
  })
})

describe('assetKindFromFile / assetNameFromFile', () => {
  it('拡張子から種別を当てる', () => {
    expect(assetKindFromFile('aki.PNG')).toBe('image')
    expect(assetKindFromFile('alley.mp4')).toBe('video')
    expect(assetKindFromFile('voice.wav')).toBe('audio')
    expect(assetKindFromFile('noext')).toBe('image')
  })

  it('ファイル名の主部を素材名にする', () => {
    expect(assetNameFromFile('アキ.png')).toBe('アキ')
    expect(assetNameFromFile('a.b.mp4')).toBe('a.b')
    expect(assetNameFromFile('noext')).toBe('noext')
  })
})

describe('formatProjectSettingsSummary', () => {
  it('接続先・短縮アスペクト・MP・おまかせを中黒でつなぐ', () => {
    expect(
      formatProjectSettingsSummary({
        target: 'local',
        quality: 'turbo',
        aspectRatio: '16:9 (Widescreen)',
        megapixels: 1,
        steps: 0,
      }),
    ).toBe('ローカル · Turbo · 16:9 · 1MP · おまかせ')
  })

  it('接続先なし・既定の画質・ステップ数を出す', () => {
    expect(
      formatProjectSettingsSummary({
        quality: 'normal',
        aspectRatio: null,
        megapixels: null,
        steps: 20,
      }),
    ).toBe('通常 · 既定 · 既定 · 20step')
  })

  it('空のアスペクトも既定、小数の MP は桁を足さない', () => {
    expect(
      formatProjectSettingsSummary({
        target: undefined,
        quality: 'opt',
        aspectRatio: '',
        megapixels: 0.7,
        steps: 4,
      }),
    ).toBe('Opt · 既定 · 0.7MP · 4step')
  })
})
