import { Clapperboard, Sparkles, type LucideIcon } from 'lucide-react'

import { VIEW_TABS, type View } from './Header'

/** 下部タブのアイコン（ヘッダーのタブと同じ行き先を、狭幅では絵で見せる）。 */
const ICON: Record<(typeof VIEW_TABS)[number]['value'], LucideIcon> = {
  main: Sparkles,
  studio: Clapperboard,
}

/**
 * 狭幅（sm 未満）用の下部固定タブバー。
 *
 * ヘッダーにタブを並べると接続状態・NSFW・設定と競って横に溢れるので、
 * ネイティブアプリと同じく画面下へ逃がす。設定は歯車ボタン側なのでここには
 * 出さず、`view === 'settings'` のあいだはどのタブも選択状態にしない。
 *
 * ホームインジケータに重ならないよう safe-area ぶんの下余白を持つ（この高さは
 * App 側のルート要素の下パディングと揃えてある）。
 */
export default function BottomNav({
  view,
  onView,
}: {
  view: View
  onView: (view: View) => void
}) {
  return (
    <nav
      aria-label="画面切り替え"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/80 pb-[env(safe-area-inset-bottom)] shadow-elevation-1 backdrop-blur-md supports-[backdrop-filter]:bg-background/60 sm:hidden"
    >
      <ul className="flex h-14 items-stretch">
        {VIEW_TABS.map((tab) => {
          const Icon = ICON[tab.value]
          const active = view === tab.value
          return (
            <li key={tab.value} className="flex-1">
              <button
                type="button"
                onClick={() => onView(tab.value)}
                aria-current={active ? 'page' : undefined}
                className={`flex size-full flex-col items-center justify-center gap-0.5 text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50 ${
                  active ? 'text-primary' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                <Icon className="size-5" aria-hidden />
                <span className="truncate">{tab.label}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
