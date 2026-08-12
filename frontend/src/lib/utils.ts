import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Tailwind のクラスを条件付きで合成し、競合するユーティリティは後勝ちで解決する。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
