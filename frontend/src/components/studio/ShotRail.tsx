import { useState, type ReactNode } from 'react'
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock,
  FileText,
  Pencil,
  Plus,
  X,
} from 'lucide-react'

import type { StudioTake } from '../../types'
import { Button } from '../ui/button'
import {
  SHOT_STATUS_CLASS,
  SHOT_STATUS_LABEL,
  episodeLabel,
  sceneLabel,
  selectedTakeOf,
  type ShotNode,
  type ShotTree,
} from './studio'

/**
 * 話・場の見出しに並べる操作ボタン（アイコンだけなので aria-label で名前を付ける）。
 *
 * 20px 角では狙いが要るので、最小でも 32px（icon-sm）を確保する。
 */
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
  children: ReactNode
  danger?: boolean
}) {
  return (
    <Button
      variant={danger ? 'destructive' : 'outline'}
      size="icon-sm"
      aria-label={label}
      title={title}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </Button>
  )
}

/** レールの Shot 1 件（サムネイル・状態・上下の並べ替え）。 */
function ShotItem({
  node,
  siblings,
  takes,
  selectedId,
  onSelect,
  onMove,
  busy,
}: {
  node: ShotNode
  /** 同じ場に並んでいるカットの数（上下の端の判定に使う）。 */
  siblings: number
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
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-md border px-2 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${
          active
            ? 'border-primary bg-primary/10'
            : 'border-border bg-surface-sunken hover:border-primary/50'
        }`}
        onClick={() => onSelect(shot.id)}
        aria-current={active ? 'true' : undefined}
      >
        <span className="h-10 w-14 shrink-0 overflow-hidden rounded bg-background">
          {selectedTake?.last_frame_url ? (
            <img
              src={selectedTake.last_frame_url}
              alt=""
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="tnum flex h-full w-full items-center justify-center text-[11px] text-muted-foreground">
              #{index + 1}
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-foreground/90">{label}</span>
          <span className="mt-0.5 flex items-center gap-1">
            <span
              className={`chip !px-1.5 !py-0 text-[11px] ${SHOT_STATUS_CLASS[shot.status]}`}
            >
              {SHOT_STATUS_LABEL[shot.status]}
            </span>
            <span className="tnum text-[11px] text-muted-foreground">
              {shot.duration_seconds}s
            </span>
          </span>
        </span>
      </button>
      <span className="flex shrink-0 flex-col justify-center gap-1">
        <IconButton
          label={`${label}を上へ`}
          title="上へ"
          onClick={() => onMove(shot.id, -1)}
          disabled={busy || index === 0}
        >
          <ChevronUp />
        </IconButton>
        <IconButton
          label={`${label}を下へ`}
          title="下へ"
          onClick={() => onMove(shot.id, 1)}
          disabled={busy || index === siblings - 1}
        >
          <ChevronDown />
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
  episodeFiltered = false,
  busy,
}: {
  tree: ShotTree
  /** 見出しに出す Shot の総数（話で絞っているならその話のぶん）。 */
  total: number
  takes: StudioTake[]
  selectedId: string | null
  onSelect: (id: string) => void
  /** delta は -1（上へ）/ +1（下へ）。動けるのは同じ場の中だけ。 */
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
  /**
   * 話で絞り込んでいるか。絞っているあいだは未分類のカットがそもそも降りて
   * こないので、「未分類」の見出しごと隠す（空の見出しを出すと「未分類が
   * 無くなった」と読めてしまうため）。
   */
  episodeFiltered?: boolean
  busy: boolean
}) {
  /** lg 未満でのレールの開閉（lg 以上は CSS で常に開いている）。 */
  const [open, setOpen] = useState(false)

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
      <p className="px-2 py-2 text-xs text-muted-foreground">{empty}</p>
    ) : (
      <ul className="space-y-1">
        {nodes.map((node) => (
          <ShotItem
            key={node.shot.id}
            node={node}
            siblings={nodes.length}
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
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        {/* 画面が狭いと（lg 未満）レールが縦に積まれて中身を押し下げるので、
            そこでは畳めるようにする。既定は閉で、カット数だけ見出しに残す。 */}
        <Button
          variant="ghost"
          size="icon-sm"
          className="lg:hidden"
          aria-expanded={open}
          aria-controls="shot-rail-tree"
          aria-label={open ? '脚本を折りたたむ' : '脚本を開く'}
          title={open ? '折りたたむ' : '開く'}
          onClick={() => setOpen((previous) => !previous)}
        >
          <ChevronRight className={open ? 'rotate-90' : ''} />
        </Button>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          脚本
        </h2>
        <span className="tnum text-xs text-muted-foreground">{total} カット</span>
        <span className="ml-auto flex gap-1">
          <Button variant="outline" size="sm" onClick={onAddEpisode} disabled={busy}>
            <Plus />
            話を追加
          </Button>
          {/* 話で絞っているあいだは押させない。ここで作るカットは未分類に
              入るので、絞り込みの向こう側に消えてしまうため（その話に足すなら
              場の「＋」から）。 */}
          <Button
            variant="outline"
            size="sm"
            onClick={onAdd}
            disabled={busy || episodeFiltered}
            title={
              episodeFiltered
                ? '話を選んでいるあいだは、場の「＋」からカットを足してください'
                : undefined
            }
          >
            <Plus />
            カットを追加
          </Button>
        </span>
      </div>

      <div
        id="shot-rail-tree"
        className={`flex-1 space-y-2 overflow-y-auto p-2 ${
          open ? '' : 'hidden lg:block'
        }`}
      >
        {total === 0 && tree.episodes.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">
            まだカットがありません
          </p>
        )}

        {tree.episodes.map((node, episodeIndex) => {
          const label = episodeLabel(node.episode, episodeIndex)
          return (
            <section key={node.episode.id}>
              {/* 操作は見出しと同じ行に詰めず下段へ回す（32px のボタンが
                  7 つ並ぶと、狭いレールではタイトルが潰れるため）。 */}
              <div className="rounded-md bg-surface-sunken px-2 py-1.5">
                <div className="flex items-center gap-2">
                  <h3 className="min-w-0 flex-1 truncate text-xs font-semibold text-foreground/90">
                    {label}
                  </h3>
                  <span className="tnum shrink-0 text-[11px] text-muted-foreground">
                    {node.shotCount}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1">
                  <IconButton
                    label={`${label}に場を追加`}
                    title="場を追加"
                    onClick={() => onAddScene(node.episode.id)}
                    disabled={busy}
                  >
                    <Plus />
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
                    <Pencil />
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
                    <FileText />
                  </IconButton>
                  <IconButton
                    label={`${label}を上へ`}
                    title="上へ"
                    onClick={() => onMoveEpisode(node.episode.id, -1)}
                    disabled={busy || episodeIndex === 0}
                  >
                    <ChevronUp />
                  </IconButton>
                  <IconButton
                    label={`${label}を下へ`}
                    title="下へ"
                    onClick={() => onMoveEpisode(node.episode.id, 1)}
                    disabled={busy || episodeIndex === tree.episodes.length - 1}
                  >
                    <ChevronDown />
                  </IconButton>
                  <IconButton
                    label={`${label}を削除`}
                    title="削除"
                    onClick={() => onDeleteEpisode(node.episode.id)}
                    disabled={busy}
                    danger
                  >
                    <X />
                  </IconButton>
                </div>
              </div>

              {node.scenes.length === 0 && (
                <p className="px-2 py-2 text-xs text-muted-foreground">
                  まだ場がありません
                </p>
              )}
              {node.scenes.map((sceneNode, sceneIndex) => {
                const name = sceneLabel(sceneNode.scene, sceneIndex)
                return (
                  <div key={sceneNode.scene.id} className="mt-1 pl-2">
                    <div className="border-l-2 border-border pl-2">
                      <h4 className="min-w-0 truncate text-xs text-muted-foreground">
                        {name}
                        {sceneNode.scene.time_of_day && (
                          <span className="ml-1 text-[11px] text-muted-foreground">
                            {sceneNode.scene.time_of_day}
                          </span>
                        )}
                      </h4>
                      <div className="mt-1 flex flex-wrap items-center gap-1">
                        <IconButton
                          label={`${name}にカットを追加`}
                          title="この場にカットを追加"
                          onClick={() => onAddShotToScene(sceneNode.scene.id)}
                          disabled={busy}
                        >
                          <Plus />
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
                          <Pencil />
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
                          <FileText />
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
                          <Clock />
                        </IconButton>
                        <IconButton
                          label={`${name}を上へ`}
                          title="上へ"
                          onClick={() =>
                            onMoveScene(node.episode.id, sceneNode.scene.id, -1)
                          }
                          disabled={busy || sceneIndex === 0}
                        >
                          <ChevronUp />
                        </IconButton>
                        <IconButton
                          label={`${name}を下へ`}
                          title="下へ"
                          onClick={() =>
                            onMoveScene(node.episode.id, sceneNode.scene.id, 1)
                          }
                          disabled={busy || sceneIndex === node.scenes.length - 1}
                        >
                          <ChevronDown />
                        </IconButton>
                        <IconButton
                          label={`${name}を削除`}
                          title="削除"
                          onClick={() => onDeleteScene(sceneNode.scene.id)}
                          disabled={busy}
                          danger
                        >
                          <X />
                        </IconButton>
                      </div>
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

        {!episodeFiltered && (tree.unassigned.length > 0 || tree.episodes.length > 0) && (
          <section>
            <div className="flex items-center gap-2 rounded-md bg-surface-sunken px-2 py-1.5">
              <h3 className="min-w-0 flex-1 truncate text-xs font-semibold text-muted-foreground">
                未分類
              </h3>
              <span className="tnum shrink-0 text-[11px] text-muted-foreground">
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
