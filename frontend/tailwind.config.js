/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          900: '#0a0c11',
          800: '#11141b',
          700: '#171b24',
          600: '#20252f',
          500: '#2b313d',
        },
        accent: {
          400: '#7c9cff',
          500: '#5b7cfa',
          600: '#4463e0',
        },
      },
    },
  },
  plugins: [],
}
