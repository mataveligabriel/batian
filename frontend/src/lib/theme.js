// Tema do BastiON: escuro, claro ou do sistema, com cor de destaque, tom do fundo e tamanho do texto escolhidos pelo
// usuário. Fica salvo no usuário (vale em qualquer computador) e no navegador (abre já no tema certo, sem piscar).
// As cores são variáveis CSS em "r g b" (src/index.css); aqui só trocamos data-theme e, no personalizado, os valores.

const KEY = "bastion.theme";

export const ACCENTS = [
  { id: "#2F6FCB", label: "Azul BastiON" },
  { id: "#0E8F8F", label: "Petróleo" },
  { id: "#1E9E6B", label: "Verde" },
  { id: "#7C5CD6", label: "Violeta" },
  { id: "#C2410C", label: "Laranja" },
  { id: "#BE3A5A", label: "Vinho" },
  { id: "#475569", label: "Grafite" },
];

// tom dos neutros: matiz e multiplicador de saturação aplicados à escala grafite original
export const TONES = [
  { id: "grafite", label: "Grafite", hue: null, sat: 1 },
  { id: "azul", label: "Azul-noite", hue: 218, sat: 2.2 },
  { id: "neutro", label: "Cinza neutro", hue: 0, sat: 0 },
  { id: "quente", label: "Quente", hue: 30, sat: 1.3 },
  { id: "roxo", label: "Roxo", hue: 262, sat: 1.8 },
];

export const SCALES = [
  { id: 0.9, label: "Menor" },
  { id: 1, label: "Normal" },
  { id: 1.1, label: "Maior" },
  { id: 1.2, label: "Bem maior" },
];

export const DEFAULT_THEME = { mode: "dark", accent: "#2F6FCB", tone: "grafite", scale: 1 };

// escala grafite de cada modo (a mesma do index.css), para recalcular o tom
const BASE = {
  dark: {
    canvas: "#111517", panel: "#14191B", surface: "#181E20", sunken: "#0E1214", line: "#262F32", line2: "#354145",
    "slate-50": "#F4F6F6", "slate-100": "#E6EBEB", "slate-200": "#CED6D7", "slate-300": "#ADB9BB", "slate-400": "#8D9A9D",
    "slate-500": "#6E7B7E", "slate-600": "#535F62", "slate-700": "#3C4649", "slate-800": "#283134", "slate-900": "#1A2124",
    "slate-950": "#111517",
  },
  light: {
    canvas: "#EEF2F2", panel: "#F7F9F9", surface: "#FFFFFF", sunken: "#F4F6F6", line: "#DCE2E3", line2: "#C3CCCE",
    "slate-50": "#111517", "slate-100": "#1A2124", "slate-200": "#283134", "slate-300": "#3C4649", "slate-400": "#535F62",
    "slate-500": "#687477", "slate-600": "#8D9A9D", "slate-700": "#C3CCCE", "slate-800": "#DCE2E3", "slate-900": "#E9EEEE",
    "slate-950": "#F4F6F6",
  },
};

export function hexToRgb(hex) {
  const h = String(hex || "").replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(h)) return null;
  return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
}

function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min, s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s, l];
}

function hslToRgb([h, s, l]) {
  h = ((h % 360) + 360) % 360 / 360;
  if (s === 0) return [l, l, l].map(v => Math.round(v * 255));
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  const f = (t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [f(h + 1 / 3), f(h), f(h - 1 / 3)].map(v => Math.round(v * 255));
}

const triple = (rgb) => rgb.join(" ");
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

/** Variáveis da cor de destaque para o modo: no escuro o "soft" é claro (texto sobre fundo escuro), no claro é escuro. */
export function accentVars(hex, mode) {
  const rgb = hexToRgb(hex);
  if (!rgb) return {};
  const [h, s, l] = rgbToHsl(rgb);
  const at = (L, S = s) => triple(hslToRgb([h, clamp(S, 0, 1), clamp(L, 0, 1)]));
  const base = triple(rgb), strong = at(l - 0.07);
  return mode === "light"
    ? { brand: base, "brand-strong": strong, "brand-soft": at(Math.min(l, 0.42)), "brand-pale": at(Math.min(l, 0.42) - 0.1) }
    : { brand: base, "brand-strong": strong, "brand-soft": at(Math.max(l, 0.7)), "brand-pale": at(Math.max(l, 0.7) + 0.14) };
}

/** Neutros (fundo, bordas, textos) com o tom escolhido. */
export function toneVars(toneId, mode) {
  const tone = TONES.find(t => t.id === toneId) || TONES[0];
  const out = {};
  for (const [k, hex] of Object.entries(BASE[mode])) {
    const [h, s, l] = rgbToHsl(hexToRgb(hex));
    out[k] = tone.hue === null && tone.sat === 1 ? triple(hexToRgb(hex)) : triple(hslToRgb([tone.hue ?? h, s * tone.sat, l]));
  }
  return out;
}

export function normalize(t) {
  const x = { ...DEFAULT_THEME, ...(t || {}) };
  if (!["dark", "light", "system"].includes(x.mode)) x.mode = "dark";
  if (!hexToRgb(x.accent)) x.accent = DEFAULT_THEME.accent;
  if (!TONES.some(o => o.id === x.tone)) x.tone = "grafite";
  x.scale = clamp(Number(x.scale) || 1, 0.8, 1.3);
  return x;
}

export function resolvedMode(t) {
  if (t.mode !== "system") return t.mode;
  try { return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; } catch { return "dark"; }
}

let current = DEFAULT_THEME;

export function applyTheme(t) {
  current = normalize(t);
  const mode = resolvedMode(current);
  const root = document.documentElement;
  root.setAttribute("data-theme", mode);
  root.style.colorScheme = mode;
  const vars = { ...toneVars(current.tone, mode), ...accentVars(current.accent, mode) };
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(`--c-${k}`, v);
  // componentes shadcn (botão primário, anel de foco) seguem a cor de destaque
  const [h, s, l] = rgbToHsl(hexToRgb(current.accent));
  const hsl = `${Math.round(h)} ${Math.round(s * 100)}% ${Math.round(l * 100)}%`;
  root.style.setProperty("--primary", hsl);
  root.style.setProperty("--ring", hsl);
  root.style.setProperty("--ui-scale", String(current.scale));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", `rgb(${(vars.panel || "20 25 27").split(" ").join(",")})`);
  try { window.dispatchEvent(new CustomEvent("bastion-theme", { detail: { ...current, resolved: mode } })); } catch { /* */ }
  return current;
}

export function getTheme() { return current; }

export function loadLocalTheme() {
  try { return normalize(JSON.parse(localStorage.getItem(KEY) || "null")); } catch { return DEFAULT_THEME; }
}

export function saveLocalTheme(t) {
  try { localStorage.setItem(KEY, JSON.stringify(normalize(t))); } catch { /* navegador sem storage */ }
}

/** Chamado uma vez, antes de desenhar o app. Acompanha o sistema quando o modo é "system". */
export function initTheme() {
  applyTheme(loadLocalTheme());
  try {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
      if (current.mode === "system") applyTheme(current);
    });
  } catch { /* navegador antigo */ }
}

/** Cor de uma variável do tema para uso em canvas/JS ("rgb(r g b)"). */
export function themeColor(name, alpha) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(`--c-${name}`).trim() || "128 128 128";
  return alpha === undefined ? `rgb(${v})` : `rgb(${v} / ${alpha})`;
}
