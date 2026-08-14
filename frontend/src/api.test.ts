import { describe, expect, it } from 'vitest'
import { formatDetail } from './api'

describe('formatDetail', () => {
  it('通常の文字列はそのまま返す', () => {
    expect(formatDetail('投入できません')).toBe('投入できません')
  })

  it('JSON 配列の detail は loc と msg を並べる', () => {
    expect(
      formatDetail([{ loc: ['body', 'video_prompt'], msg: 'Field required' }]),
    ).toBe('video_prompt: Field required')
  })

  it('message を持つオブジェクトは message を返す', () => {
    expect(formatDetail({ message: '二重登録です', path: '/x' })).toBe('二重登録です')
  })

  it('Cloudflare 524 の HTML はタイムアウト案内にする', () => {
    expect(
      formatDetail(
        '<!DOCTYPE html><html><body>Error 524: A timeout occurred</body></html>',
      ),
    ).toBe('接続がタイムアウトしました。生成はサーバー側で続いていることがあります。')
  })

  it('その他の HTML は生で出さない', () => {
    expect(formatDetail('<html><body>Bad gateway</body></html>')).toBe(
      'サーバーから予期しない応答が返りました。',
    )
  })
})
