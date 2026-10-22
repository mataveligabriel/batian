/** @type {import('tailwindcss').Config} */
module.exports = {
    // `overline` is a Tailwind utility; without this an app's own eyebrow-label class draws a line above the text.
    blocklist: ["overline"],
    darkMode: ["class"],
    content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html"
  ],
  theme: {
    extend: {
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)'
      },
      // Identidade BastiON: grafite (neutros levemente esverdeados), azul do escudo e o verde do "ON"
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        heading: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', '"JetBrains Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      colors: {
        // Cores por variável CSS (src/index.css e src/theme-palette.css): tema escuro, claro e personalizado (lib/theme.js)
        canvas: 'rgb(var(--c-canvas) / <alpha-value>)',     // fundo do app
        panel: 'rgb(var(--c-panel) / <alpha-value>)',       // menu lateral, cabeçalhos de tabela
        surface: 'rgb(var(--c-surface) / <alpha-value>)',   // cartões e diálogos
        sunken: 'rgb(var(--c-sunken) / <alpha-value>)',     // campos, terminal, áreas de código
        line: 'rgb(var(--c-line) / <alpha-value>)',         // bordas
        line2: 'rgb(var(--c-line2) / <alpha-value>)',       // bordas em destaque / hover
        brand: { DEFAULT: 'rgb(var(--c-brand) / <alpha-value>)', strong: 'rgb(var(--c-brand-strong) / <alpha-value>)', soft: 'rgb(var(--c-brand-soft) / <alpha-value>)', pale: 'rgb(var(--c-brand-pale) / <alpha-value>)' },
        on: { DEFAULT: 'rgb(var(--c-on) / <alpha-value>)', soft: 'rgb(var(--c-on-soft) / <alpha-value>)' },
        // "white" segue o tema (realces bg-white/5, hover:text-white); texto sobre botão colorido usa onbrand (sempre branco)
        white: 'rgb(var(--c-white) / <alpha-value>)',
        onbrand: '#FFFFFF',
        // neutros "grafite" (todas as classes text-/bg-/border-slate-* acompanham o tema)
        slate: { 50: 'rgb(var(--c-slate-50) / <alpha-value>)', 100: 'rgb(var(--c-slate-100) / <alpha-value>)', 200: 'rgb(var(--c-slate-200) / <alpha-value>)', 300: 'rgb(var(--c-slate-300) / <alpha-value>)', 400: 'rgb(var(--c-slate-400) / <alpha-value>)', 500: 'rgb(var(--c-slate-500) / <alpha-value>)', 600: 'rgb(var(--c-slate-600) / <alpha-value>)', 700: 'rgb(var(--c-slate-700) / <alpha-value>)', 800: 'rgb(var(--c-slate-800) / <alpha-value>)', 900: 'rgb(var(--c-slate-900) / <alpha-value>)', 950: 'rgb(var(--c-slate-950) / <alpha-value>)' },
        red: { 50: 'rgb(var(--tw-red-50) / <alpha-value>)', 100: 'rgb(var(--tw-red-100) / <alpha-value>)', 200: 'rgb(var(--tw-red-200) / <alpha-value>)', 300: 'rgb(var(--tw-red-300) / <alpha-value>)', 400: 'rgb(var(--tw-red-400) / <alpha-value>)', 500: 'rgb(var(--tw-red-500) / <alpha-value>)', 600: 'rgb(var(--tw-red-600) / <alpha-value>)', 700: 'rgb(var(--tw-red-700) / <alpha-value>)', 800: 'rgb(var(--tw-red-800) / <alpha-value>)', 900: 'rgb(var(--tw-red-900) / <alpha-value>)', 950: 'rgb(var(--tw-red-950) / <alpha-value>)' },
        orange: { 50: 'rgb(var(--tw-orange-50) / <alpha-value>)', 100: 'rgb(var(--tw-orange-100) / <alpha-value>)', 200: 'rgb(var(--tw-orange-200) / <alpha-value>)', 300: 'rgb(var(--tw-orange-300) / <alpha-value>)', 400: 'rgb(var(--tw-orange-400) / <alpha-value>)', 500: 'rgb(var(--tw-orange-500) / <alpha-value>)', 600: 'rgb(var(--tw-orange-600) / <alpha-value>)', 700: 'rgb(var(--tw-orange-700) / <alpha-value>)', 800: 'rgb(var(--tw-orange-800) / <alpha-value>)', 900: 'rgb(var(--tw-orange-900) / <alpha-value>)', 950: 'rgb(var(--tw-orange-950) / <alpha-value>)' },
        amber: { 50: 'rgb(var(--tw-amber-50) / <alpha-value>)', 100: 'rgb(var(--tw-amber-100) / <alpha-value>)', 200: 'rgb(var(--tw-amber-200) / <alpha-value>)', 300: 'rgb(var(--tw-amber-300) / <alpha-value>)', 400: 'rgb(var(--tw-amber-400) / <alpha-value>)', 500: 'rgb(var(--tw-amber-500) / <alpha-value>)', 600: 'rgb(var(--tw-amber-600) / <alpha-value>)', 700: 'rgb(var(--tw-amber-700) / <alpha-value>)', 800: 'rgb(var(--tw-amber-800) / <alpha-value>)', 900: 'rgb(var(--tw-amber-900) / <alpha-value>)', 950: 'rgb(var(--tw-amber-950) / <alpha-value>)' },
        yellow: { 50: 'rgb(var(--tw-yellow-50) / <alpha-value>)', 100: 'rgb(var(--tw-yellow-100) / <alpha-value>)', 200: 'rgb(var(--tw-yellow-200) / <alpha-value>)', 300: 'rgb(var(--tw-yellow-300) / <alpha-value>)', 400: 'rgb(var(--tw-yellow-400) / <alpha-value>)', 500: 'rgb(var(--tw-yellow-500) / <alpha-value>)', 600: 'rgb(var(--tw-yellow-600) / <alpha-value>)', 700: 'rgb(var(--tw-yellow-700) / <alpha-value>)', 800: 'rgb(var(--tw-yellow-800) / <alpha-value>)', 900: 'rgb(var(--tw-yellow-900) / <alpha-value>)', 950: 'rgb(var(--tw-yellow-950) / <alpha-value>)' },
        lime: { 50: 'rgb(var(--tw-lime-50) / <alpha-value>)', 100: 'rgb(var(--tw-lime-100) / <alpha-value>)', 200: 'rgb(var(--tw-lime-200) / <alpha-value>)', 300: 'rgb(var(--tw-lime-300) / <alpha-value>)', 400: 'rgb(var(--tw-lime-400) / <alpha-value>)', 500: 'rgb(var(--tw-lime-500) / <alpha-value>)', 600: 'rgb(var(--tw-lime-600) / <alpha-value>)', 700: 'rgb(var(--tw-lime-700) / <alpha-value>)', 800: 'rgb(var(--tw-lime-800) / <alpha-value>)', 900: 'rgb(var(--tw-lime-900) / <alpha-value>)', 950: 'rgb(var(--tw-lime-950) / <alpha-value>)' },
        green: { 50: 'rgb(var(--tw-green-50) / <alpha-value>)', 100: 'rgb(var(--tw-green-100) / <alpha-value>)', 200: 'rgb(var(--tw-green-200) / <alpha-value>)', 300: 'rgb(var(--tw-green-300) / <alpha-value>)', 400: 'rgb(var(--tw-green-400) / <alpha-value>)', 500: 'rgb(var(--tw-green-500) / <alpha-value>)', 600: 'rgb(var(--tw-green-600) / <alpha-value>)', 700: 'rgb(var(--tw-green-700) / <alpha-value>)', 800: 'rgb(var(--tw-green-800) / <alpha-value>)', 900: 'rgb(var(--tw-green-900) / <alpha-value>)', 950: 'rgb(var(--tw-green-950) / <alpha-value>)' },
        emerald: { 50: 'rgb(var(--tw-emerald-50) / <alpha-value>)', 100: 'rgb(var(--tw-emerald-100) / <alpha-value>)', 200: 'rgb(var(--tw-emerald-200) / <alpha-value>)', 300: 'rgb(var(--tw-emerald-300) / <alpha-value>)', 400: 'rgb(var(--tw-emerald-400) / <alpha-value>)', 500: 'rgb(var(--tw-emerald-500) / <alpha-value>)', 600: 'rgb(var(--tw-emerald-600) / <alpha-value>)', 700: 'rgb(var(--tw-emerald-700) / <alpha-value>)', 800: 'rgb(var(--tw-emerald-800) / <alpha-value>)', 900: 'rgb(var(--tw-emerald-900) / <alpha-value>)', 950: 'rgb(var(--tw-emerald-950) / <alpha-value>)' },
        teal: { 50: 'rgb(var(--tw-teal-50) / <alpha-value>)', 100: 'rgb(var(--tw-teal-100) / <alpha-value>)', 200: 'rgb(var(--tw-teal-200) / <alpha-value>)', 300: 'rgb(var(--tw-teal-300) / <alpha-value>)', 400: 'rgb(var(--tw-teal-400) / <alpha-value>)', 500: 'rgb(var(--tw-teal-500) / <alpha-value>)', 600: 'rgb(var(--tw-teal-600) / <alpha-value>)', 700: 'rgb(var(--tw-teal-700) / <alpha-value>)', 800: 'rgb(var(--tw-teal-800) / <alpha-value>)', 900: 'rgb(var(--tw-teal-900) / <alpha-value>)', 950: 'rgb(var(--tw-teal-950) / <alpha-value>)' },
        cyan: { 50: 'rgb(var(--tw-cyan-50) / <alpha-value>)', 100: 'rgb(var(--tw-cyan-100) / <alpha-value>)', 200: 'rgb(var(--tw-cyan-200) / <alpha-value>)', 300: 'rgb(var(--tw-cyan-300) / <alpha-value>)', 400: 'rgb(var(--tw-cyan-400) / <alpha-value>)', 500: 'rgb(var(--tw-cyan-500) / <alpha-value>)', 600: 'rgb(var(--tw-cyan-600) / <alpha-value>)', 700: 'rgb(var(--tw-cyan-700) / <alpha-value>)', 800: 'rgb(var(--tw-cyan-800) / <alpha-value>)', 900: 'rgb(var(--tw-cyan-900) / <alpha-value>)', 950: 'rgb(var(--tw-cyan-950) / <alpha-value>)' },
        sky: { 50: 'rgb(var(--tw-sky-50) / <alpha-value>)', 100: 'rgb(var(--tw-sky-100) / <alpha-value>)', 200: 'rgb(var(--tw-sky-200) / <alpha-value>)', 300: 'rgb(var(--tw-sky-300) / <alpha-value>)', 400: 'rgb(var(--tw-sky-400) / <alpha-value>)', 500: 'rgb(var(--tw-sky-500) / <alpha-value>)', 600: 'rgb(var(--tw-sky-600) / <alpha-value>)', 700: 'rgb(var(--tw-sky-700) / <alpha-value>)', 800: 'rgb(var(--tw-sky-800) / <alpha-value>)', 900: 'rgb(var(--tw-sky-900) / <alpha-value>)', 950: 'rgb(var(--tw-sky-950) / <alpha-value>)' },
        blue: { 50: 'rgb(var(--tw-blue-50) / <alpha-value>)', 100: 'rgb(var(--tw-blue-100) / <alpha-value>)', 200: 'rgb(var(--tw-blue-200) / <alpha-value>)', 300: 'rgb(var(--tw-blue-300) / <alpha-value>)', 400: 'rgb(var(--tw-blue-400) / <alpha-value>)', 500: 'rgb(var(--tw-blue-500) / <alpha-value>)', 600: 'rgb(var(--tw-blue-600) / <alpha-value>)', 700: 'rgb(var(--tw-blue-700) / <alpha-value>)', 800: 'rgb(var(--tw-blue-800) / <alpha-value>)', 900: 'rgb(var(--tw-blue-900) / <alpha-value>)', 950: 'rgb(var(--tw-blue-950) / <alpha-value>)' },
        indigo: { 50: 'rgb(var(--tw-indigo-50) / <alpha-value>)', 100: 'rgb(var(--tw-indigo-100) / <alpha-value>)', 200: 'rgb(var(--tw-indigo-200) / <alpha-value>)', 300: 'rgb(var(--tw-indigo-300) / <alpha-value>)', 400: 'rgb(var(--tw-indigo-400) / <alpha-value>)', 500: 'rgb(var(--tw-indigo-500) / <alpha-value>)', 600: 'rgb(var(--tw-indigo-600) / <alpha-value>)', 700: 'rgb(var(--tw-indigo-700) / <alpha-value>)', 800: 'rgb(var(--tw-indigo-800) / <alpha-value>)', 900: 'rgb(var(--tw-indigo-900) / <alpha-value>)', 950: 'rgb(var(--tw-indigo-950) / <alpha-value>)' },
        violet: { 50: 'rgb(var(--tw-violet-50) / <alpha-value>)', 100: 'rgb(var(--tw-violet-100) / <alpha-value>)', 200: 'rgb(var(--tw-violet-200) / <alpha-value>)', 300: 'rgb(var(--tw-violet-300) / <alpha-value>)', 400: 'rgb(var(--tw-violet-400) / <alpha-value>)', 500: 'rgb(var(--tw-violet-500) / <alpha-value>)', 600: 'rgb(var(--tw-violet-600) / <alpha-value>)', 700: 'rgb(var(--tw-violet-700) / <alpha-value>)', 800: 'rgb(var(--tw-violet-800) / <alpha-value>)', 900: 'rgb(var(--tw-violet-900) / <alpha-value>)', 950: 'rgb(var(--tw-violet-950) / <alpha-value>)' },
        purple: { 50: 'rgb(var(--tw-purple-50) / <alpha-value>)', 100: 'rgb(var(--tw-purple-100) / <alpha-value>)', 200: 'rgb(var(--tw-purple-200) / <alpha-value>)', 300: 'rgb(var(--tw-purple-300) / <alpha-value>)', 400: 'rgb(var(--tw-purple-400) / <alpha-value>)', 500: 'rgb(var(--tw-purple-500) / <alpha-value>)', 600: 'rgb(var(--tw-purple-600) / <alpha-value>)', 700: 'rgb(var(--tw-purple-700) / <alpha-value>)', 800: 'rgb(var(--tw-purple-800) / <alpha-value>)', 900: 'rgb(var(--tw-purple-900) / <alpha-value>)', 950: 'rgb(var(--tw-purple-950) / <alpha-value>)' },
        fuchsia: { 50: 'rgb(var(--tw-fuchsia-50) / <alpha-value>)', 100: 'rgb(var(--tw-fuchsia-100) / <alpha-value>)', 200: 'rgb(var(--tw-fuchsia-200) / <alpha-value>)', 300: 'rgb(var(--tw-fuchsia-300) / <alpha-value>)', 400: 'rgb(var(--tw-fuchsia-400) / <alpha-value>)', 500: 'rgb(var(--tw-fuchsia-500) / <alpha-value>)', 600: 'rgb(var(--tw-fuchsia-600) / <alpha-value>)', 700: 'rgb(var(--tw-fuchsia-700) / <alpha-value>)', 800: 'rgb(var(--tw-fuchsia-800) / <alpha-value>)', 900: 'rgb(var(--tw-fuchsia-900) / <alpha-value>)', 950: 'rgb(var(--tw-fuchsia-950) / <alpha-value>)' },
        pink: { 50: 'rgb(var(--tw-pink-50) / <alpha-value>)', 100: 'rgb(var(--tw-pink-100) / <alpha-value>)', 200: 'rgb(var(--tw-pink-200) / <alpha-value>)', 300: 'rgb(var(--tw-pink-300) / <alpha-value>)', 400: 'rgb(var(--tw-pink-400) / <alpha-value>)', 500: 'rgb(var(--tw-pink-500) / <alpha-value>)', 600: 'rgb(var(--tw-pink-600) / <alpha-value>)', 700: 'rgb(var(--tw-pink-700) / <alpha-value>)', 800: 'rgb(var(--tw-pink-800) / <alpha-value>)', 900: 'rgb(var(--tw-pink-900) / <alpha-value>)', 950: 'rgb(var(--tw-pink-950) / <alpha-value>)' },
        rose: { 50: 'rgb(var(--tw-rose-50) / <alpha-value>)', 100: 'rgb(var(--tw-rose-100) / <alpha-value>)', 200: 'rgb(var(--tw-rose-200) / <alpha-value>)', 300: 'rgb(var(--tw-rose-300) / <alpha-value>)', 400: 'rgb(var(--tw-rose-400) / <alpha-value>)', 500: 'rgb(var(--tw-rose-500) / <alpha-value>)', 600: 'rgb(var(--tw-rose-600) / <alpha-value>)', 700: 'rgb(var(--tw-rose-700) / <alpha-value>)', 800: 'rgb(var(--tw-rose-800) / <alpha-value>)', 900: 'rgb(var(--tw-rose-900) / <alpha-value>)', 950: 'rgb(var(--tw-rose-950) / <alpha-value>)' },
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))'
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        chart: {
          '1': 'hsl(var(--chart-1))',
          '2': 'hsl(var(--chart-2))',
          '3': 'hsl(var(--chart-3))',
          '4': 'hsl(var(--chart-4))',
          '5': 'hsl(var(--chart-5))'
        }
      },
      keyframes: {
        'accordion-down': {
          from: {
            height: '0'
          },
          to: {
            height: 'var(--radix-accordion-content-height)'
          }
        },
        'accordion-up': {
          from: {
            height: 'var(--radix-accordion-content-height)'
          },
          to: {
            height: '0'
          }
        }
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out'
      }
    }
  },
  plugins: [require("tailwindcss-animate")],
};