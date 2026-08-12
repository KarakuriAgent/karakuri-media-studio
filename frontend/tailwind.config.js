import tailwindcssAnimate from 'tailwindcss-animate'

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        // shadcn/ui 標準トークン（実体は src/index.css の CSS 変数）。
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        'surface-sunken': 'hsl(var(--surface-sunken))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        // 補助情報用の控えめな文字色。`text-muted-foreground/70` のような不透明度
        // 指定は AA 不合格になるため、代わりにこの実値トークンを使う。
        // （`muted.subtle` にすると `text-muted-subtle` という紛らわしいクラス名に
        // なるので、トップレベルキーで `text-muted-foreground-subtle` を生やす）
        'muted-foreground-subtle': 'hsl(var(--muted-foreground-subtle))',
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        // 旧パレットの生き残り。ink-* は全画面の移行で不要になったので落とした。
        // accent-400/500 は「primary より一段明るいリンク色」として使う場所が残って
        // いるため据え置く（shadcn の `accent` トークンと名前が衝突しないよう、
        // accent は数値キーだけを足している）。
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
          400: '#7c9cff',
          500: '#5b7cfa',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      boxShadow: {
        // ダーク背景で沈み込みを表現する控えめな黒影。
        'elevation-1': '0 1px 2px 0 rgb(0 0 0 / 0.35), 0 1px 3px 0 rgb(0 0 0 / 0.25)',
        'elevation-2': '0 2px 6px -1px rgb(0 0 0 / 0.45), 0 4px 12px -2px rgb(0 0 0 / 0.3)',
        'elevation-3': '0 8px 24px -4px rgb(0 0 0 / 0.55), 0 16px 40px -8px rgb(0 0 0 / 0.4)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [tailwindcssAnimate],
}
