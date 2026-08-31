import type {
  Asset,
  AudioJobCreate,
  ChatReply,
  ChatSession,
  ChatState,
  ChatSessionCreate,
  ComfyTarget,
  Health,
  HealthStatus,
  Job,
  JobContinue,
  JobCreate,
  LibraryCategoryValue,
  LibraryItem,
  LibraryKind,
  LibraryPage,
  LibraryQuery,
  LibrarySheetRequest,
  LibrarySource,
  Lora,
  LoraPayload,
  ModelDownload,
  ModelDownloadAllResult,
  ModelFieldState,
  ModelsDirStatus,
  Options,
  PushSubscriptionPayload,
  PushVapidPublicKey,
  Settings,
  StudioAsset,
  StudioAssetCreate,
  StudioAssetFile,
  StudioAssetFileRole,
  StudioAssetUpdate,
  StudioCapabilities,
  StudioEpisode,
  StudioEpisodeCreate,
  StudioEpisodeUpdate,
  StudioProject,
  StudioProjectCreate,
  StudioProjectDetail,
  StudioProjectSummary,
  StudioProjectUpdate,
  StudioRenderRequest,
  StudioRevision,
  StudioRevisionDetail,
  StudioRevisionDiff,
  StudioRevisionRestore,
  StudioScene,
  StudioSceneCreate,
  StudioSceneUpdate,
  StudioShot,
  StudioShotCreate,
  StudioShotPreview,
  StudioShotUpdate,
  StudioTake,
  StudioTimeline,
  StudioTimelineCreate,
  StudioTimelineDetail,
  StudioTimelineUpdate,
  TimelineClipInput,
  TimelineExport,
  TimelineExportRequest,
  TimelineMediaKind,
  TimelineMediaPage,
  TimelineMissingFix,
  TimelineMissingReport,
  TimelineSubtitleRequest,
  TimelineSyncPreview,
  TimelineSyncRequest,
  TimelineTrackCreate,
  TimelineTrackUpdate,
  UiFormState,
} from './types'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(formatDetail(detail) || `HTTP ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/**
 * FastAPI returns `{detail: "..."}`, `{detail: [{loc, msg}, ...]}` or, for the
 * errors that carry data（ライブラリの二重登録など）, `{detail: {message, …}}`.
 */
export function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    const trimmed = detail.trim()
    if (/^<!DOCTYPE/i.test(trimmed) || /^<html\b/i.test(trimmed)) {
      const lower = trimmed.toLowerCase()
      if (lower.includes('524') || lower.includes('timeout')) {
        return '接続がタイムアウトしました。生成はサーバー側で続いていることがあります。'
      }
      return 'サーバーから予期しない応答が返りました。'
    }
    return detail
  }
  if (
    detail &&
    typeof detail === 'object' &&
    !Array.isArray(detail) &&
    typeof (detail as { message?: unknown }).message === 'string'
  ) {
    return (detail as { message: string }).message
  }
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === 'object') {
          const item = d as { loc?: unknown[]; msg?: string }
          const loc = Array.isArray(item.loc)
            ? item.loc.filter((p) => p !== 'body').join('.')
            : ''
          return loc ? `${loc}: ${item.msg ?? ''}` : (item.msg ?? '')
        }
        return String(d)
      })
      .filter(Boolean)
      .join(' / ')
  }
  if (detail == null) return ''
  return typeof detail === 'object' ? JSON.stringify(detail) : String(detail)
}

const FIELD_LABELS: Record<string, string> = {
  image_prompt: '画像プロンプト',
  video_prompt: '動画プロンプト',
  audio_prompt: '音声プロンプト',
  audio_path: 'リファレンス音声',
  source_image: '開始フレーム',
  end_image: '最後のフレーム',
  reference_video: '参照動画',
}

/** Pull `mode 'x' requires: a, b` out of a 422 body into per-field messages. */
export function fieldErrorsFromError(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const text = formatDetail(error.detail)
  const match = /requires:\s*(.+)$/.exec(text)
  if (!match) return {}
  const fields: Record<string, string> = {}
  for (const raw of match[1].split(',')) {
    const key = raw.trim()
    if (key in FIELD_LABELS) fields[key] = `${FIELD_LABELS[key]}は必須です`
  }
  return fields
}

async function request<T>(
  path: string,
  init?: RequestInit & { raw?: BodyInit },
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch (cause) {
    throw new ApiError(0, `バックエンドに接続できません (${String(cause)})`)
  }
  if (!response.ok) {
    let detail: unknown = response.statusText
    const text = await response.text().catch(() => '')
    if (text) {
      try {
        const parsed = JSON.parse(text) as { detail?: unknown }
        detail = parsed?.detail ?? text
      } catch {
        detail = text
      }
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

function upload<T>(
  path: string,
  file: File,
  fields: Record<string, string> = {},
): Promise<T> {
  const data = new FormData()
  data.append('file', file)
  for (const [key, value] of Object.entries(fields)) data.append(key, value)
  return request<T>(path, { method: 'POST', body: data })
}

/** ファイルを伴わない multipart（受け口が Form のままのものに使う）。 */
function form<T>(path: string, fields: Record<string, string> = {}): Promise<T> {
  const data = new FormData()
  for (const [key, value] of Object.entries(fields)) data.append(key, value)
  return request<T>(path, { method: 'POST', body: data })
}

/** multipart のフィールド（未指定は送らず、サーバー側の既定値に任せる）。 */
function formFields(fields: object): Record<string, string> {
  return Object.fromEntries(
    Object.entries(fields)
      .filter(([, value]) => value !== undefined)
      .map(([key, value]) => [key, String(value)]),
  )
}

/** multipart で送れる素材のメタデータ（アップロードと共通）。 */
interface StudioAssetFields {
  name?: string
  kind?: string
  category?: string
  caption?: string
  prompt_caption?: string
  locked?: boolean
}

export const api = {
  health: () => request<Health>('/api/health'),

  options: () => request<Options>('/api/options'),

  getSettings: () => request<Settings>('/api/settings'),
  putSettings: (patch: Partial<Settings>) =>
    json<Settings>('PUT', '/api/settings', patch),

  /**
   * Grok Build CLI（Grok Imagine の生成バックエンド、SPEC §5.2）の疎通確認。
   * `status` は枠を使わない軽い確認（コマンドと認証ファイル）、`check` は実際に
   * 1 ターン回す（設定ページの「接続確認」）。
   */
  grokStatus: () => request<HealthStatus>('/api/grok/status'),
  checkGrok: () => json<HealthStatus>('POST', '/api/grok/check', {}),

  vapidPublicKey: () => request<PushVapidPublicKey>('/api/push/vapid-public-key'),
  savePushSubscription: (payload: PushSubscriptionPayload) =>
    json<void>('POST', '/api/push/subscriptions', payload),
  deletePushSubscription: (endpoint: string) =>
    json<void>(
      'DELETE',
      `/api/push/subscriptions?endpoint=${encodeURIComponent(endpoint)}`,
    ),

  // モデル指定と LoRA 登録は接続先ごと（SPEC §5）。`target` を省略すると
  // サーバーが現在の接続先を使う。設定ページは編集中の環境を明示的に渡す。
  listModels: (target?: ComfyTarget) =>
    request<ModelFieldState[]>(`/api/models${target ? `?target=${target}` : ''}`),
  /** `choices` を省略すると保存済みの候補リストはそのまま残る。 */
  putModels: (
    overrides: Record<string, string>,
    choices?: Record<string, string[]>,
    target?: ComfyTarget,
  ) =>
    json<ModelFieldState[]>('PUT', '/api/models', { overrides, choices, target }),

  // 不足モデルのダウンロード（SPEC §3.3）。進捗は WS の `model_download` で届く。
  // 落とし先は `target`: ローカルはこのアプリが、RunPod は Pod の API が落とす。
  modelsDirStatus: () => request<ModelsDirStatus>('/api/models/dir-status'),
  listModelDownloads: (target?: ComfyTarget) =>
    request<ModelDownload[]>(
      `/api/models/downloads${target ? `?target=${target}` : ''}`,
    ),
  downloadModel: (
    filename: string,
    url: string,
    subfolder: string,
    target?: ComfyTarget,
  ) =>
    json<ModelDownload>('POST', '/api/models/download', {
      filename,
      url,
      subfolder,
      target,
    }),
  /** 未検出かつ取得元 URL 登録済みのモデルをまとめて落とす。 */
  downloadAllModels: (target?: ComfyTarget) =>
    json<ModelDownloadAllResult>('POST', '/api/models/download-all', { target }),

  listLoras: (target?: ComfyTarget) =>
    request<Lora[]>(`/api/loras${target ? `?target=${target}` : ''}`),
  createLora: (payload: LoraPayload) => json<Lora>('POST', '/api/loras', payload),
  updateLora: (id: number, payload: Partial<LoraPayload>) =>
    json<Lora>('PUT', `/api/loras/${id}`, payload),
  deleteLora: (id: number) => json<void>('DELETE', `/api/loras/${id}`),
  uploadLoraSample: (id: number, file: File) =>
    upload<Lora>(`/api/loras/${id}/samples`, file),
  deleteLoraSample: (id: number, name: string) =>
    json<Lora>('DELETE', `/api/loras/${id}/samples/${encodeURIComponent(name)}`),

  // ライブラリ（SPEC §7.2）。一覧は /api/options にも入っているので、フォームは
  // そちらを使い、ここは登録・更新・削除のために使う。
  listLibrary: (query: LibraryQuery = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    const search = params.toString()
    return request<LibraryPage>(`/api/library${search ? `?${search}` : ''}`)
  },
  /** `category` を省くと未分類で登録される。 */
  uploadToLibrary: (
    kind: LibraryKind,
    file: File,
    tags: string[] = [],
    category: LibraryCategoryValue = 'none',
  ) =>
    upload<LibraryItem>(`/api/library/${kind}`, file, {
      tags: tags.join(','),
      category,
    }),
  /** ジョブの出力をライブラリに取っておく（NSFW は元ジョブを引き継ぐ）。 */
  addJobToLibrary: (
    jobId: string,
    source: LibrarySource,
    name = '',
    tags: string[] = [],
    category: LibraryCategoryValue = 'none',
  ) =>
    json<LibraryItem>('POST', '/api/library/from-job', {
      job_id: jobId,
      source,
      name,
      tags,
      category,
    }),
  /**
   * 選んだ画像素材を 1 枚のリファレンスシートに合成して登録する（SPEC §7.2）。
   *
   * `itemIds` の**並び順に意味がある**（左上から置く）。返るのは出来上がった
   * シートの LibraryItem で、そのまま画像欄（`source_image`）に指定できる。
   */
  createLibrarySheet: (
    itemIds: string[],
    options: Omit<LibrarySheetRequest, 'item_ids'> = {},
  ) =>
    json<LibraryItem>('POST', '/api/library/sheet', {
      item_ids: itemIds,
      ...options,
    }),
  /** 送った項目だけ変える。`category: 'none'` は未分類に戻す指定（SPEC §7.2）。 */
  updateLibraryItem: (
    id: string,
    patch: {
      name?: string
      nsfw?: boolean
      tags?: string[]
      category?: LibraryCategoryValue
    },
  ) => json<LibraryItem>('PATCH', `/api/library/${id}`, patch),
  deleteLibraryItem: (id: string) => json<void>('DELETE', `/api/library/${id}`),

  listAudio: () => request<Asset[]>('/api/assets/audio'),
  listImages: () => request<Asset[]>('/api/assets/image'),
  listVideos: () => request<Asset[]>('/api/assets/video'),
  uploadAudio: (file: File) => upload<Asset>('/api/assets/audio', file),
  uploadImage: (file: File) => upload<Asset>('/api/assets/image', file),
  uploadVideo: (file: File) => upload<Asset>('/api/assets/video', file),

  listJobs: (limit = 60) => request<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  createJob: (payload: JobCreate | AudioJobCreate) =>
    json<Job>('POST', '/api/jobs', payload),
  /**
   * 同じ設定でもう一度流す。既定はシードの再抽選で、`randomizeSeed: false` を
   * 渡すと元ジョブと同じシードのまま投げ直す（SPEC §2）。
   */
  rerunJob: (id: string, randomizeSeed = true) =>
    json<Job>('POST', `/api/jobs/${id}/rerun`, {
      randomize_seed: randomizeSeed,
    }),
  /** ラストフレームから続きを生成する。`body` に入れた項目だけ元ジョブを上書きする。 */
  continueJob: (id: string, body: JobContinue = {}) =>
    json<Job>('POST', `/api/jobs/${id}/continue`, body),
  deleteJob: (id: string) => json<void>('DELETE', `/api/jobs/${id}`),
  /** 実行中・待ちのジョブを止める。完了済みは冪等にそのまま返る。 */
  cancelJob: (id: string) => json<Job>('POST', `/api/jobs/${id}/cancel`),
  /** NSFW フラグの手動トグル（manual として保存される）。 */
  setJobNsfw: (id: string, nsfw: boolean) =>
    json<Job>('POST', `/api/jobs/${id}/nsfw`, { nsfw }),

  createChatSession: (payload: ChatSessionCreate) =>
    json<ChatSession>('POST', '/api/chat/sessions', payload),
  getChatSession: (id: string) => request<ChatSession>(`/api/chat/sessions/${id}`),
  sendChatMessage: (id: string, content: string) =>
    json<ChatReply>('POST', `/api/chat/sessions/${id}/messages`, { content }),
  /** ⏹: 走っている Grok のターンを止める（次の発言は新しい会話で続く）。 */
  stopChatTurn: (id: string) =>
    json<ChatState>('POST', `/api/chat/sessions/${id}/stop`, {}),

  // 生成フォームの下書き（外部エージェントと共有する画面の状態）。保存すると
  // WS の `form` フレームが全ブラウザへ飛ぶ（自分が出した revision は読み飛ばす）。
  getGenerateForm: () => request<UiFormState>('/api/ui/generate-form'),
  putGenerateForm: (
    values: Record<string, unknown>,
    baseRevision: number | null = null,
  ) =>
    json<UiFormState>('PUT', '/api/ui/generate-form', {
      values,
      base_revision: baseRevision,
    }),

  // ドラマスタジオ（プロジェクト -> 脚本 -> Shot ごとの生成 -> Take の採用）。
  // 画面 1 枚は getStudioProject（素材・Shot・Take 込み）で組み立てる。
  /** いまの接続先でスタジオの追加機能（ラテント連続性）が使えるか。 */
  getStudioCapabilities: () =>
    request<StudioCapabilities>('/api/studio/capabilities'),
  listStudioProjects: () => request<StudioProjectSummary[]>('/api/studio/projects'),
  createStudioProject: (payload: StudioProjectCreate) =>
    json<StudioProject>('POST', '/api/studio/projects', payload),
  /** デモ作品を 1 本まるごと作る（同じ作品コードが既にあれば 409）。 */
  createStudioDemoProject: (code: string) =>
    json<StudioProjectDetail>('POST', '/api/studio/demo', { code }),
  /**
   * 画面 1 枚ぶんの詳細。
   *
   * `episodeId` を渡すと **場・カット・テイクだけ**がその話のぶんに絞られる
   * （話と素材はいつも全件返る）。null = 作品まるごと。
   */
  getStudioProject: (id: string, episodeId: string | null = null) =>
    request<StudioProjectDetail>(
      `/api/studio/projects/${id}${
        episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
      }`,
    ),
  updateStudioProject: (id: string, patch: StudioProjectUpdate) =>
    json<StudioProject>('PATCH', `/api/studio/projects/${id}`, patch),
  deleteStudioProject: (id: string) =>
    json<void>('DELETE', `/api/studio/projects/${id}`),

  /** World Bible に素材を足す（multipart）。`name` が `@名前` の識別名になる。 */
  uploadStudioAsset: (
    projectId: string,
    file: File,
    fields: StudioAssetFields = {},
  ) =>
    upload<StudioAsset>(
      `/api/studio/projects/${projectId}/assets`,
      file,
      formFields(fields),
    ),
  /**
   * ファイルを持たない素材を足す（名前とキャプションだけ）。
   *
   * 参照には添付されず、`@名前` は投入時にプロンプトの説明文へ展開される。
   * 受け口は upload と同じ multipart なので、file だけ付けずに投げる。
   */
  createStudioAsset: (projectId: string, payload: StudioAssetCreate) =>
    form<StudioAsset>(
      `/api/studio/projects/${projectId}/assets`,
      formFields(payload),
    ),
  updateStudioAsset: (id: string, patch: StudioAssetUpdate) =>
    json<StudioAsset>('PATCH', `/api/studio/assets/${id}`, patch),
  deleteStudioAsset: (id: string) => json<void>('DELETE', `/api/studio/assets/${id}`),

  /**
   * 素材のメインのファイルを付ける / 差し替える。
   *
   * 種別（image / video / audio）は拡張子から決まり、実体は今までどおり
   * `assets/<kind>/` に置かれる。
   */
  uploadStudioAssetFile: (assetId: string, file: File) =>
    upload<StudioAsset>(`/api/studio/assets/${assetId}/file`, file),

  /** 素材にぶら下がるリファレンス（声サンプル・動画・追加画像）。 */
  listStudioAssetFiles: (assetId: string) =>
    request<StudioAssetFile[]>(`/api/studio/assets/${assetId}/files`),
  addStudioAssetFile: (
    assetId: string,
    file: File,
    fields: { role?: StudioAssetFileRole; caption?: string } = {},
  ) =>
    upload<StudioAssetFile>(
      `/api/studio/assets/${assetId}/files`,
      file,
      formFields(fields),
    ),
  deleteStudioAssetFile: (fileId: string) =>
    json<void>('DELETE', `/api/studio/asset-files/${fileId}`),

  // 話（エピソード）と場（シーン）。Shot は場に属する（属さないものは未分類）。
  createStudioEpisode: (projectId: string, payload: StudioEpisodeCreate = {}) =>
    json<StudioEpisode>('POST', `/api/studio/projects/${projectId}/episodes`, payload),
  /** `ids` の並び順がそのまま sort_order になる（全件を過不足なく送る）。 */
  reorderStudioEpisodes: (projectId: string, ids: string[]) =>
    json<StudioEpisode[]>(
      'POST',
      `/api/studio/projects/${projectId}/episodes/reorder`,
      { ids },
    ),
  updateStudioEpisode: (id: string, patch: StudioEpisodeUpdate) =>
    json<StudioEpisode>('PATCH', `/api/studio/episodes/${id}`, patch),
  /** 配下の場ごと消す（そこにいた Shot は未分類に戻る）。 */
  deleteStudioEpisode: (id: string) =>
    json<void>('DELETE', `/api/studio/episodes/${id}`),

  createStudioScene: (episodeId: string, payload: StudioSceneCreate = {}) =>
    json<StudioScene>('POST', `/api/studio/episodes/${episodeId}/scenes`, payload),
  /** `ids` の並び順がそのまま sort_order になる（この話の場を全件送る）。 */
  reorderStudioScenes: (episodeId: string, ids: string[]) =>
    json<StudioScene[]>('POST', `/api/studio/episodes/${episodeId}/scenes/reorder`, {
      ids,
    }),
  updateStudioScene: (id: string, patch: StudioSceneUpdate) =>
    json<StudioScene>('PATCH', `/api/studio/scenes/${id}`, patch),
  /** 場だけ消す（そこにいた Shot は未分類に戻る）。 */
  deleteStudioScene: (id: string) => json<void>('DELETE', `/api/studio/scenes/${id}`),

  // リビジョン履歴（新しい順の見出し -> 中身 -> その時点への書き戻し）。
  /**
   * 新しい順の見出し一覧。`entity` を渡すとその 1 件を触った履歴だけに絞る
   * （「このカットの履歴」。絞り込みは名前ではなく id で行う）。
   */
  listStudioRevisions: (
    projectId: string,
    entity?: { kind: string; id: string },
  ) => {
    const query = entity
      ? `?${new URLSearchParams({ entity_kind: entity.kind, entity_id: entity.id })}`
      : ''
    return request<StudioRevision[]>(
      `/api/studio/projects/${projectId}/revisions${query}`,
    )
  },
  getStudioRevision: (projectId: string, seq: number) =>
    request<StudioRevisionDetail>(
      `/api/studio/projects/${projectId}/revisions/${seq}`,
    ),
  /** そのリビジョンで何が変わったか（直前のリビジョンとの差分）。 */
  getStudioRevisionDiff: (projectId: string, seq: number) =>
    request<StudioRevisionDiff>(
      `/api/studio/projects/${projectId}/revisions/${seq}/diff`,
    ),
  /**
   * その時点へ書き戻す。`target` を渡すとその 1 件（`fields` まで渡すとその
   * 項目だけ）の部分復元になる。
   */
  restoreStudioRevision: (
    projectId: string,
    seq: number,
    target?: StudioRevisionRestore,
  ) =>
    json<StudioProjectDetail>(
      'POST',
      `/api/studio/projects/${projectId}/revisions/${seq}/restore`,
      target ?? {},
    ),

  createStudioShot: (projectId: string, payload: StudioShotCreate = {}) =>
    json<StudioShot>('POST', `/api/studio/projects/${projectId}/shots`, payload),
  /** `shotIds` の並び順がそのまま sort_order になる（全件を過不足なく送る）。 */
  reorderStudioShots: (projectId: string, shotIds: string[]) =>
    json<StudioShot[]>('POST', `/api/studio/projects/${projectId}/shots/reorder`, {
      shot_ids: shotIds,
    }),
  updateStudioShot: (id: string, patch: StudioShotUpdate) =>
    json<StudioShot>('PATCH', `/api/studio/shots/${id}`, patch),
  deleteStudioShot: (id: string) => json<void>('DELETE', `/api/studio/shots/${id}`),
  /**
   * このカットを今生成したら**実際に投入されるもの**（読み取りだけ）。
   *
   * 生成と同じ組み立てを通るが、英訳は走らない（入るかどうかは
   * `will_translate`。使える英語キャッシュがあれば false）。組み立てられない
   * カットも 200 で `error` に理由が入る。
   */
  previewStudioShotPrompt: (id: string) =>
    request<StudioShotPreview>(`/api/studio/shots/${id}/prompt-preview`),
  /** 組み立て済み本文を英語の公式 H3 文書にして Shot に保存する。 */
  translateStudioShotPrompt: (id: string) =>
    json<StudioShot>('POST', `/api/studio/shots/${id}/translate`),

  listStudioTakes: (shotId: string) =>
    request<StudioTake[]>(`/api/studio/shots/${shotId}/takes`),
  /**
   * Shot を 1 回生成する（ワークフローはサーバー側で t2v / i2v / r2v に決まる）。
   *
   * `body` はそのテイク 1 回だけに効く上書き（解像度・尺・ステップ数・シード）。
   * 省略すればカット / プロジェクトの設定で焼く従来どおりの投入。
   */
  renderStudioShot: (shotId: string, body?: StudioRenderRequest) =>
    json<StudioTake>('POST', `/api/studio/shots/${shotId}/render`, body),
  selectStudioTake: (id: string) =>
    json<StudioTake>('POST', `/api/studio/takes/${id}/select`),
  rejectStudioTake: (id: string) =>
    json<StudioTake>('POST', `/api/studio/takes/${id}/reject`),
  cancelStudioTake: (id: string) =>
    json<StudioTake>('POST', `/api/studio/takes/${id}/cancel`),
  deleteStudioTake: (id: string) => json<void>('DELETE', `/api/studio/takes/${id}`),

  // 編集タブ（タイムライン -> クリップ -> 書き出し）。焼き上がった Take を
  // 並べ直して 1 本の動画にする面で、生成（上の Shot / Take）とは別に持つ。
  /**
   * タイムラインを 1 本作る。
   *
   * `episode_id` を送ると**自動配置つき**の初期化になる（その話のカットを
   * 場 -> カット順に走査し、採用 Take の動画があるものを V1 へ隙間なく並べる）。
   */
  createStudioTimeline: (projectId: string, payload: StudioTimelineCreate = {}) =>
    json<StudioTimelineDetail>(
      'POST',
      `/api/studio/projects/${projectId}/timelines`,
      payload,
    ),
  listStudioTimelines: (projectId: string) =>
    request<StudioTimeline[]>(`/api/studio/projects/${projectId}/timelines`),
  /** トラックとクリップ込みのフル EDL（クリップはソース解決済み）。 */
  getStudioTimeline: (id: string) =>
    request<StudioTimelineDetail>(`/api/studio/timelines/${id}`),
  updateStudioTimeline: (id: string, patch: StudioTimelineUpdate) =>
    json<StudioTimeline>('PATCH', `/api/studio/timelines/${id}`, patch),
  deleteStudioTimeline: (id: string) =>
    json<void>('DELETE', `/api/studio/timelines/${id}`),
  /**
   * クリップを丸ごと置き換える（画面の自動保存の受け口）。
   *
   * 同じトラックで重なっているもの、`in_ms >= out_ms`、尺と切り出しの長さが
   * 食い違うもの（フェーズ 1 は等速のみ）は 400 で断られる。
   */
  replaceStudioTimelineClips: (id: string, clips: TimelineClipInput[]) =>
    json<StudioTimelineDetail>('PUT', `/api/studio/timelines/${id}/clips`, {
      clips,
    }),
  /**
   * 書き出しを 1 本受け付ける（**202 即受付**。ffmpeg は裏で走る）。
   *
   * 進捗は WS の `timeline_export` フレームと `listStudioTimelineExports` で追う。
   * 同じタイムラインで走っているものがあれば 409。
   */
  exportStudioTimeline: (id: string, body: TimelineExportRequest = {}) =>
    json<TimelineExport>('POST', `/api/studio/timelines/${id}/export`, body),
  listStudioTimelineExports: (id: string) =>
    request<TimelineExport[]>(`/api/studio/timelines/${id}/exports`),
  /** トラックを 1 本足す（音声 A1… / 字幕 T1。映像トラックは 400）。 */
  addStudioTimelineTrack: (id: string, payload: TimelineTrackCreate = {}) =>
    json<StudioTimelineDetail>(
      'POST',
      `/api/studio/timelines/${id}/tracks`,
      payload,
    ),
  /** 名前・ミュート・ロックを変える（送らなかった項目はそのまま）。 */
  updateStudioTimelineTrack: (
    id: string,
    trackId: string,
    patch: TimelineTrackUpdate,
  ) =>
    json<StudioTimelineDetail>(
      'PATCH',
      `/api/studio/timelines/${id}/tracks/${trackId}`,
      patch,
    ),
  /** トラックを 1 本消す（載っていたクリップも一緒に消える）。 */
  deleteStudioTimelineTrack: (id: string, trackId: string) =>
    json<StudioTimelineDetail>(
      'DELETE',
      `/api/studio/timelines/${id}/tracks/${trackId}`,
    ),
  /** 素材ビンの 1 ページ（テイク・ライブラリ・単発ジョブ・作品の素材）。 */
  listStudioTimelineMedia: (
    projectId: string,
    kind: TimelineMediaKind,
    limit = 50,
    offset = 0,
  ) =>
    request<TimelineMediaPage>(
      `/api/studio/projects/${projectId}/media` +
        `?kind=${kind}&limit=${limit}&offset=${offset}`,
    ),
  /**
   * V1 の各クリップの元カットの台詞から、テロップを一括で置き直す。
   *
   * 字幕トラックの中身は**置き換わる**（呼ぶ前に確認を取ること）。
   */
  generateStudioTimelineSubtitles: (
    id: string,
    body: TimelineSubtitleRequest = {},
  ) =>
    json<StudioTimelineDetail>(
      'POST',
      `/api/studio/timelines/${id}/generate-subtitles`,
      body,
    ),
  /** 作ったあとに脚本で起きた差分（増えた / 採用が変わった / 消えたカット）。 */
  getStudioTimelineSyncPreview: (id: string) =>
    request<TimelineSyncPreview>(`/api/studio/timelines/${id}/sync-preview`),
  /** 差分のうち、選んだものだけ反映する。 */
  applyStudioTimelineSync: (id: string, body: TimelineSyncRequest) =>
    json<StudioTimelineDetail>('POST', `/api/studio/timelines/${id}/sync`, body),
  /** 実ファイルが見つからないクリップと、同じカットの差し替え候補。 */
  getStudioTimelineMissing: (id: string) =>
    request<TimelineMissingReport>(`/api/studio/timelines/${id}/missing`),
  /** 欠落クリップを別テイクへ差し替える / まとめて消す。 */
  resolveStudioTimelineMissing: (id: string, body: TimelineMissingFix) =>
    json<StudioTimelineDetail>(
      'POST',
      `/api/studio/timelines/${id}/missing/resolve`,
      body,
    ),
  /** 完成した mp4 をライブラリ（`library/video/`）へコピーして登録する。 */
  saveStudioExportToLibrary: (exportId: string, name = '') =>
    json<LibraryItem>('POST', `/api/studio/exports/${exportId}/save-to-library`, {
      name,
    }),
}

export function wsUrl(path = '/api/ws'): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${location.host}${path}`
}
