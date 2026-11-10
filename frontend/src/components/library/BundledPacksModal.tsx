import { useCallback, useEffect, useRef, useState } from 'react'
import { ExternalLink, Loader2, PackageOpen } from 'lucide-react'

import { api } from '../../api'
import type { AssetPackImport, AssetPackImportEntry, BundledAssetPack } from '../../types'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Banner, Modal } from '../ui'

/** 取り込みの進み具合を読み直す間隔（ms）。 */
export const POLL_MS = 1000

/** 取り込みの結果の区分（画面の言い方）。 */
const OUTCOMES: { key: keyof AssetPackImport; label: string }[] = [
  { key: 'imported', label: '取り込んだ' },
  { key: 'skipped', label: '飛ばした' },
  { key: 'changed', label: '中身が違う' },
  { key: 'missing', label: '見つからない' },
  { key: 'blocked', label: 'robots.txt で取らなかった' },
  { key: 'failed', label: '取れなかった' },
]

function entriesOf(result: AssetPackImport, key: keyof AssetPackImport) {
  const value = result[key]
  return Array.isArray(value) ? (value as AssetPackImportEntry[]) : []
}

/**
 * 同梱の素材パック（SPEC §7.2 / EXTERNAL-API §3.8）。
 *
 * リポジトリの `asset-packs/` に目録だけを同梱している。入れると入手元から
 * 1 件ずつ取りに行って棚に組み込む。入れる前に入手元ごとの規約の要点・確かめた日・
 * 規約のページを見せ、**同意のチェックを付けた入手元だけ**を取り込む。
 */
export default function BundledPacksModal({
  onClose,
  onInstalled,
}: {
  onClose: () => void
  /** 取り込みが終わったとき（棚を読み直してもらう） */
  onInstalled?: () => void
}) {
  const [packs, setPacks] = useState<BundledAssetPack[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // パックごとの同意した入手元
  const [consent, setConsent] = useState<Record<string, string[]>>({})
  // パックごとの最後の取り込み
  const [imports, setImports] = useState<Record<string, AssetPackImport>>({})
  const [starting, setStarting] = useState<string | null>(null)
  const timers = useRef<number[]>([])
  const alive = useRef(true)

  const load = useCallback(async () => {
    try {
      const list = await api.listBundledPacks()
      if (alive.current) setPacks(list)
    } catch (caught) {
      if (alive.current) setError(caught instanceof Error ? caught.message : String(caught))
    }
  }, [])

  useEffect(() => {
    alive.current = true
    void load()
    return () => {
      alive.current = false
      for (const timer of timers.current) window.clearTimeout(timer)
    }
  }, [load])

  const poll = useCallback(
    (packId: string, importId: string) => {
      const timer = window.setTimeout(async () => {
        if (!alive.current) return
        try {
          const result = await api.getPackImport(importId)
          if (!alive.current) return
          setImports((prev) => ({ ...prev, [packId]: result }))
          if (result.status === 'queued' || result.status === 'running') {
            poll(packId, importId)
          } else {
            void load()
            if (!result.dry_run) onInstalled?.()
          }
        } catch (caught) {
          if (alive.current) setError(caught instanceof Error ? caught.message : String(caught))
        }
      }, POLL_MS)
      timers.current.push(timer)
    },
    [load, onInstalled],
  )

  const toggle = (packId: string, sourceId: string, on: boolean) =>
    setConsent((prev) => {
      const current = new Set(prev[packId] ?? [])
      if (on) current.add(sourceId)
      else current.delete(sourceId)
      return { ...prev, [packId]: [...current] }
    })

  const install = async (pack: BundledAssetPack, dryRun: boolean) => {
    setError(null)
    setStarting(pack.id)
    try {
      const result = await api.installBundledPack(pack.id, {
        accept_sources: consent[pack.id] ?? [],
        dry_run: dryRun,
      })
      setImports((prev) => ({ ...prev, [pack.id]: result }))
      if (result.status === 'queued' || result.status === 'running') poll(pack.id, result.id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setStarting(null)
    }
  }

  return (
    <Modal title="素材パック" onClose={onClose} wide>
      <div className="flex max-h-[75vh] flex-col gap-3 overflow-y-auto pr-1 text-sm">
        {error && <Banner onClose={() => setError(null)}>{error}</Banner>}
        {packs === null && !error && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> 読み込み中…
          </div>
        )}
        {packs && packs.length === 0 && (
          <p className="text-muted-foreground">同梱の素材パックはありません。</p>
        )}
        {packs && packs.length > 0 && <Banner tone="info">{packs[0].notice}</Banner>}
        {packs?.map((pack) => {
          const accepted = consent[pack.id] ?? []
          const job = imports[pack.id]
          const running = job && (job.status === 'queued' || job.status === 'running')
          const pending = accepted.length
            ? pack.sources
                .filter((source) => accepted.includes(source.id))
                .reduce((sum, source) => sum + source.total - source.installed, 0)
            : 0
          return (
            <section
              key={pack.id}
              aria-label={pack.name || pack.id}
              className="flex flex-col gap-2 rounded-md border border-border p-3"
            >
              <div className="flex flex-wrap items-baseline gap-2">
                <PackageOpen className="size-4 self-center" />
                <h3 className="font-semibold">{pack.name || pack.id}</h3>
                <code className="text-xs text-muted-foreground">{pack.id}</code>
                <span className="ml-auto text-xs" data-testid={`installed-${pack.id}`}>
                  入れ済み {pack.installed} / {pack.total} 件
                </span>
              </div>
              {pack.description && (
                <p className="text-xs text-muted-foreground">{pack.description}</p>
              )}
              <div className="flex flex-wrap gap-1 text-xs">
                {pack.categories.map((category) => (
                  <span key={category} className="chip !py-0.5">
                    {category}
                  </span>
                ))}
                {Object.entries(pack.kinds).map(([kind, count]) => (
                  <span key={kind} className="chip !py-0.5">
                    {kind} {count}
                  </span>
                ))}
              </div>

              <ul className="flex flex-col gap-2">
                {pack.sources.map((source) => {
                  const id = `consent-${pack.id}-${source.id}`
                  return (
                    <li key={source.id} className="rounded-sm bg-surface-sunken p-2 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{source.name}</span>
                        {source.url && (
                          <a
                            href={source.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-muted-foreground underline"
                          >
                            サイト
                          </a>
                        )}
                        {source.terms_url && (
                          <a
                            href={source.terms_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-0.5 underline"
                          >
                            規約のページ <ExternalLink className="size-3" />
                          </a>
                        )}
                        <span className="ml-auto text-muted-foreground">
                          {source.installed} / {source.total} 件
                        </span>
                      </div>
                      <p className="mt-1 whitespace-pre-wrap">{source.terms_summary}</p>
                      <p className="mt-1 text-muted-foreground">
                        規約を確かめた日: {source.checked_at || '不明'}
                        {source.credit_required === false && '・クレジット不要'}
                        {source.credit_required === true && '・クレジットが必要'}
                      </p>
                      <label htmlFor={id} className="mt-1 flex items-center gap-2">
                        <Checkbox
                          id={id}
                          checked={accepted.includes(source.id)}
                          disabled={Boolean(running)}
                          onCheckedChange={(value) => toggle(pack.id, source.id, value === true)}
                        />
                        規約のページを確かめ、同意する
                      </label>
                    </li>
                  )
                })}
              </ul>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={!accepted.length || Boolean(running) || starting === pack.id}
                  onClick={() => void install(pack, false)}
                >
                  {starting === pack.id || running ? (
                    <Loader2 className="animate-spin" />
                  ) : null}
                  入れる{accepted.length ? `（未入手 ${pending} 件）` : ''}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!accepted.length || Boolean(running) || starting === pack.id}
                  onClick={() => void install(pack, true)}
                >
                  予定だけ見る
                </Button>
                {!accepted.length && (
                  <span className="text-xs text-muted-foreground">
                    入れるには、同意する入手元にチェックを付けてください
                  </span>
                )}
              </div>

              {job && <ImportResult result={job} />}
            </section>
          )
        })}
      </div>
    </Modal>
  )
}

function ImportResult({ result }: { result: AssetPackImport }) {
  const running = result.status === 'queued' || result.status === 'running'
  return (
    <div className="rounded-sm border border-border p-2 text-xs" role="status">
      <div className="flex flex-wrap items-center gap-2">
        {running && <Loader2 className="size-3 animate-spin" />}
        <span className="font-medium">
          {result.dry_run ? '予定' : running ? '取り込み中' : result.status === 'failed' ? '失敗' : '完了'}
        </span>
        <span>
          {result.processed} / {result.total} 件
        </span>
        {result.error && <span className="text-red-300">{result.error}</span>}
      </div>
      <ul className="mt-1 flex flex-wrap gap-x-3">
        {OUTCOMES.map(({ key, label }) => {
          const count = entriesOf(result, key).length
          if (!count) return null
          return (
            <li key={key}>
              {result.dry_run && key === 'imported' ? '取り込む予定' : label} {count}
            </li>
          )
        })}
      </ul>
      {OUTCOMES.filter(({ key }) => key !== 'imported' && key !== 'skipped').map(
        ({ key, label }) => {
          const entries = entriesOf(result, key)
          if (!entries.length) return null
          return (
            <details key={key} className="mt-1">
              <summary>
                {label}（{entries.length}）
              </summary>
              <ul className="ml-4 list-disc">
                {entries.map((entry) => (
                  <li key={`${entry.source}/${entry.file}`}>
                    {entry.name || entry.file}: {entry.reason}
                  </li>
                ))}
              </ul>
            </details>
          )
        },
      )}
    </div>
  )
}
