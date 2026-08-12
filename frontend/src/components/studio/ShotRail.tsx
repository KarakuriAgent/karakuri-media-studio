import type { StudioTake } from '../../types'
import {
  SHOT_STATUS_CLASS,
  SHOT_STATUS_LABEL,
  episodeLabel,
  sceneLabel,
  selectedTakeOf,
  type ShotNode,
  type ShotTree,
} from './studio'

/** 話・場の見出しに並べる操作ボタン（記号だけなので aria-label で名前を付ける）。 */
function IconButton({
  label,
  title,
  onClick,
  disabled,
  children,
  danger,
}: {
  label: string
  title: string
  onClick: () => void
  disabled?: boolean
  children: string
  danger?: boolean
}) {
  return (
    <button
      className={`${danger ? 'btn-danger' : 'btn-ghost'} !px-1.5 !py-0 text-[10px]`}
      aria-label={label}
      title={title}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  )
}

/** レールの Shot 1 件（サムネイル・状態・上下の並べ替え）。 */
function ShotItem({
  node,
  total,
  takes,
  selectedId,
  onSelect,
  onMove,
  busy,
}: {
  node: ShotNode
  total: number
  takes: StudioTake[]
  selectedId: string | null
  onSelect: (id: string) => void
  onMove: (id: string, delta: number) => void
  busy: boolean
}) {
  const { shot, index } = node
  const active = shot.id === selectedId
  const selectedTake = selectedTakeOf(shot, takes)
  const label = shot.title || `カット ${index + 1}`
  return (
    <li className="flex items-stretch gap-1">
      <button
        aria-label={label}
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-md border px-2 py-2 text-left transition-colors ${
          active
            ? 'border-accent-500 bg-accent-500/10'
            : 'border-ink-600 bg-ink-800 hover:border-ink-500'
        }`}
        onClick={() => onSelect(shot.id)}
        aria-current={active ? 'true' : undefined}
      >
        <span className="h-10 w-14 shrink-0 overflow-hidden rounded bg-ink-900">
          {selectedTake?.last_frame_url ? (
            <img
              src={selectedTake.last_frame_url}
              alt=""
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="flex h-full w-full items-center justify-center text-[10px] text-slate-600">
              {String(index + 1).padStart(2, '0')}
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-slate-200">{label}</span>
          <span className="mt-0.5 flex items-center gap-1">
            <span
              className={`chip !px-1.5 !py-0 text-[10px] ${SHOT_STATUS_CLASS[shot.status]}`}
            >
              {SHOT_STATUS_LABEL[shot.status]}
            </span>
            <span className="text-[10px] text-slate-500">{shot.duration_seconds}s</span>
          </span>
        </span>
      </button>
      <span className="flex shrink-0 flex-col justify-center gap-0.5">
        <IconButton
          label={`${label}を上へ`}
          title="上へ"
          onClick={() => onMove(shot.id, -1)}
          disabled={busy || index === 0}
        >
          ▲
        </IconButton>
        <IconButton
          label={`${label}を下へ`}
          title="下へ"
          onClick={() => onMove(shot.id, 1)}
          disabled={busy || index === total - 1}
        >
          ▼
        </IconButton>
      </span>
    </li>
  )
}

/**
 * 左レール: 話（Episode）-> 場（Scene）-> Shot のツリーと、その並べ替え・追加。
 *
 * 並べ替えは矢印ボタンで 1 つずつ動かす（ドラッグは触れる面積が小さく、
 * キーボードからも触れないので採らない）。改名はブラウザの `prompt` で済ませる
 * ——ここは移動と選択のための面で、書くのは中央のフォームに寄せてある。
 */
export default function ShotRail({
  tree,
  total,
  takes,
  selectedId,
  onSelect,
  onMove,
  onAdd,
  onAddEpisode,
  onRenameEpisode,
  onEditEpisodeSynopsis,
  onMoveEpisode,
  onDeleteEpisode,
  onAddScene,
  onAddShotToScene,
  onRenameScene,
  onEditSceneSynopsis,
  onEditSceneTimeOfDay,
  onMoveScene,
  onDeleteScene,
  busy,
}: {
  tree: ShotTree
  /** Shot の総数（並べ替えの端の判定に使う）。 */
  total: number
  takes: StudioTake[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** delta は -1（上へ）/ +1（下へ）。 */
  onMove: (id: string, delta: number) => void
  onAdd: () => void
  onAddEpisode: () => void
  onRenameEpisode: (id: string, title: string) => void
  /** 話のあらすじ（`StudioEpisode.synopsis`）を書き換える。 */
  onEditEpisodeSynopsis: (id: string, synopsis: string) => void
  onMoveEpisode: (id: string, delta: number) => void
  onDeleteEpisode: (id: string) => void
  onAddScene: (episodeId: string) => void
  /** その場に属するカットを 1 手で足す（作ったカットを選ぶ）。 */
  onAddShotToScene: (sceneId: string) => void
  onRenameScene: (id: string, title: string) => void
  /** 場のあらすじ（`StudioScene.synopsis`）を書き換える。 */
  onEditSceneSynopsis: (id: string, synopsis: string) => void
  /** 場の時間帯（`StudioScene.time_of_day`）を書き換える。 */
  onEditSceneTimeOfDay: (id: string, timeOfDay: string) => void
  onMoveScene: (episodeId: string, id: string, delta: number) => void
  onDeleteScene: (id: string) => void
  busy: boolean
}) {
  /**
   * 1 行の書き換えはブラウザの `prompt` で済ませる（この面は移動と選択のための
   * もので、書くのは中央のフォームに寄せてある）。取消（null）は何もしない。
   */
  const edit = (
    message: string,
    current: string,
    apply: (value: string) => void,
  ) => {
    const next = window.prompt(message, current)
    if (next === null) return
    apply(next.trim())
  }

  const rename = (current: string, apply: (title: string) => void) =>
    edit('新しいタイトル', current, apply)

  const shotList = (nodes: ShotNode[], empty: string) =>
    nodes.length === 0 ? (
      <p className="px-2 py-2 text-[10px] text-slate-600">{empty}</p>
    ) : (
      <ul className="space-y-1">
        {nodes.map((node) => (
          <ShotItem
            key={node.shot.id}
            node={node}
            total={total}
            takes={takes}
            selectedId={selectedId}
            onSelect={onSelect}
            onMove={onMove}
            busy={busy}
          />
        ))}
      </ul>
    )

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-ink-700 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          脚本
        </h2>
        <span className="text-xs text-slate-600">{total} カット</span>
        <span className="ml-auto flex gap-1">
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={onAddEpisode}
            disabled={busy}
          >
            ＋ 話
          </button>
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={onAdd}
            disabled={busy}
          >
            ＋ カット
          </button>
        </span>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-2">
        {total === 0 && tree.episodes.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-slate-600">
            まだカットがありません
          </p>
        )}

        {tree.episodes.map((node, episodeIndex) => {
          const label = episodeLabel(node.episode, episodeIndex)
          return (
            <section key={node.episode.id}>
              <div className="flex items-center gap-1 rounded-md bg-ink-900/60 px-2 py-1">
                <h3 className="min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-200">
                  {label}
                </h3>
                <span className="shrink-0 text-[10px] text-slate-600">
                  {node.shotCount}
                </span>
                <IconButton
                  label={`${label}に場を追加`}
                  title="場を追加"
                  onClick={() => onAddScene(node.episode.id)}
                  disabled={busy}
                >
                  ＋場
                </IconButton>
                <IconButton
                  label={`${label}を改名`}
                  title="改名"
                  onClick={() =>
                    rename(node.episode.title, (title) =>
                      onRenameEpisode(node.episode.id, title),
                    )
                  }
                  disabled={busy}
                >
                  ✎
                </IconButton>
                <IconButton
                  label={`${label}のあらすじを編集`}
                  title="あらすじ"
                  onClick={() =>
                    edit(`${label}のあらすじ`, node.episode.synopsis, (synopsis) =>
                      onEditEpisodeSynopsis(node.episode.id, synopsis),
                    )
                  }
                  disabled={busy}
                >
                  📝
                </IconButton>
                <IconButton
                  label={`${label}を上へ`}
                  title="上へ"
                  onClick={() => onMoveEpisode(node.episode.id, -1)}
                  disabled={busy || episodeIndex === 0}
                >
                  ▲
                </IconButton>
                <IconButton
                  label={`${label}を下へ`}
                  title="下へ"
                  onClick={() => onMoveEpisode(node.episode.id, 1)}
                  disabled={busy || episodeIndex === tree.episodes.length - 1}
                >
                  ▼
                </IconButton>
                <IconButton
                  label={`${label}を削除`}
                  title="削除"
                  onClick={() => onDeleteEpisode(node.episode.id)}
                  disabled={busy}
                  danger
                >
                  ✕
                </IconButton>
              </div>

              {node.scenes.length === 0 && (
                <p className="px-2 py-2 text-[10px] text-slate-600">
                  まだ場がありません
                </p>
              )}
              {node.scenes.map((sceneNode, sceneIndex) => {
                const name = sceneLabel(sceneNode.scene, sceneIndex)
                return (
                  <div key={sceneNode.scene.id} className="mt-1 pl-2">
                    <div className="flex items-center gap-1 border-l-2 border-ink-600 pl-2">
                      <h4 className="min-w-0 flex-1 truncate text-[11px] text-slate-400">
                        {name}
                        {sceneNode.scene.time_of_day && (
                          <span className="ml-1 text-[10px] text-slate-600">
                            {sceneNode.scene.time_of_day}
                          </span>
                        )}
                      </h4>
                      <IconButton
                        label={`${name}にカットを追加`}
                        title="この場にカットを追加"
                        onClick={() => onAddShotToScene(sceneNode.scene.id)}
                        disabled={busy}
                      >
                        ＋
                      </IconButton>
                      <IconButton
                        label={`${name}を改名`}
                        title="改名"
                        onClick={() =>
                          rename(sceneNode.scene.title, (title) =>
                            onRenameScene(sceneNode.scene.id, title),
                          )
                        }
                        disabled={busy}
                      >
                        ✎
                      </IconButton>
                      <IconButton
                        label={`${name}のあらすじを編集`}
                        title="あらすじ"
                        onClick={() =>
                          edit(
                            `${name}のあらすじ`,
                            sceneNode.scene.synopsis,
                            (synopsis) =>
                              onEditSceneSynopsis(sceneNode.scene.id, synopsis),
                          )
                        }
                        disabled={busy}
                      >
                        📝
                      </IconButton>
                      <IconButton
                        label={`${name}の時間帯を編集`}
                        title="時間帯"
                        onClick={() =>
                          edit(
                            `${name}の時間帯（例: 夕方）`,
                            sceneNode.scene.time_of_day,
                            (timeOfDay) =>
                              onEditSceneTimeOfDay(sceneNode.scene.id, timeOfDay),
                          )
                        }
                        disabled={busy}
                      >
                        🕒
                      </IconButton>
                      <IconButton
                        label={`${name}を上へ`}
                        title="上へ"
                        onClick={() =>
                          onMoveScene(node.episode.id, sceneNode.scene.id, -1)
                        }
                        disabled={busy || sceneIndex === 0}
                      >
                        ▲
                      </IconButton>
                      <IconButton
                        label={`${name}を下へ`}
                        title="下へ"
                        onClick={() =>
                          onMoveScene(node.episode.id, sceneNode.scene.id, 1)
                        }
                        disabled={busy || sceneIndex === node.scenes.length - 1}
                      >
                        ▼
                      </IconButton>
                      <IconButton
                        label={`${name}を削除`}
                        title="削除"
                        onClick={() => onDeleteScene(sceneNode.scene.id)}
                        disabled={busy}
                        danger
                      >
                        ✕
                      </IconButton>
                    </div>
                    <div className="mt-1 pl-2">
                      {shotList(sceneNode.shots, 'カットなし')}
                    </div>
                  </div>
                )
              })}
            </section>
          )
        })}

        {(tree.unassigned.length > 0 || tree.episodes.length > 0) && (
          <section>
            <div className="flex items-center gap-1 rounded-md bg-ink-900/60 px-2 py-1">
              <h3 className="min-w-0 flex-1 truncate text-[11px] font-semibold text-slate-400">
                未分類
              </h3>
              <span className="shrink-0 text-[10px] text-slate-600">
                {tree.unassigned.length}
              </span>
            </div>
            <div className="mt-1">
              {shotList(tree.unassigned, 'すべて場に割り当て済みです')}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
