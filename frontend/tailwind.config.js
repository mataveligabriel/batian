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
        canvas: '#111517',     // fundo do app
        panel: '#14191B',      // menu lateral, cabeçalhos de tabela
        surface: '#181E20',    // cartões e diálogos
        sunken: '#0E1214',     // campos, terminal, áreas de código
        line: '#262F32',       // bordas
        line2: '#354145',      // bordas em destaque / hover
        brand: { DEFAULT: '#2F6FCB', strong: '#285FAE', soft: '#7FADEB', pale: '#BBD3F5' },
        on: { DEFAULT: '#3CC48D', soft: '#7ADBB3' },
        // neutros "grafite" no lugar do slate azulado (todas as classes text-/bg-/border-slate-* acompanham)
        slate: {
          50: '#F4F6F6', 100: '#E6EBEB', 200: '#CED6D7', 300: '#ADB9BB', 400: '#8D9A9D', 500: '#6E7B7E',
          600: '#535F62', 700: '#3C4649', 800: '#283134', 900: '#1A2124', 950: '#111517',
        },
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