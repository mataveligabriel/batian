// Preferências do terminal (por navegador) + temas estilo MobaXterm / Termius.
import { useEffect, useState } from "react";

export const TERM_THEMES = {
  mobaxterm: {
    label: "MobaXterm",
    theme: {
      background: "#000000", foreground: "#E6E6E6", cursor: "#FFFFFF", cursorAccent: "#000000",
      selectionBackground: "rgba(90,140,255,0.45)",
      black: "#000000", red: "#E0443E", green: "#4EC940", yellow: "#E5C12E", blue: "#3B8EEA", magenta: "#D670D6", cyan: "#29B8DB", white: "#E5E5E5",
      brightBlack: "#7F7F7F", brightRed: "#FF6E67", brightGreen: "#5FFA68", brightYellow: "#FFFC67", brightBlue: "#6871FF", brightMagenta: "#FF77FF", brightCyan: "#60FDFF", brightWhite: "#FFFFFF",
    },
  },
  termius: {
    label: "Termius",
    theme: {
      background: "#141729", foreground: "#E8E9ED", cursor: "#21D07A", cursorAccent: "#141729",
      selectionBackground: "rgba(33,208,122,0.28)",
      black: "#1E2235", red: "#FF5F5F", green: "#21D07A", yellow: "#FFC857", blue: "#5B8DEF", magenta: "#C678DD", cyan: "#3DD6D0", white: "#D7DAE0",
      brightBlack: "#5C6370", brightRed: "#FF7B7B", brightGreen: "#4BE39A", brightYellow: "#FFD97A", brightBlue: "#7FA7FF", brightMagenta: "#D99BEB", brightCyan: "#6BE5E0", brightWhite: "#FFFFFF",
    },
  },
  bastion: {
    label: "Bastion",
    theme: {
      background: "#05070A", foreground: "#E2E8F0", cursor: "#4DA3FF", cursorAccent: "#05070A",
      selectionBackground: "rgba(0,122,255,0.35)",
      black: "#0A0E17", red: "#F87171", green: "#10B981", yellow: "#F59E0B", blue: "#4DA3FF", magenta: "#8B5CF6", cyan: "#22D3EE", white: "#F8FAFC",
      brightBlack: "#64748B", brightRed: "#FCA5A5", brightGreen: "#34D399", brightYellow: "#FCD34D", brightBlue: "#93C5FD", brightMagenta: "#C4B5FD", brightCyan: "#67E8F9", brightWhite: "#FFFFFF",
    },
  },
  dracula: {
    label: "Dracula",
    theme: {
      background: "#1E1F29", foreground: "#F8F8F2", cursor: "#F8F8F2", cursorAccent: "#1E1F29",
      selectionBackground: "rgba(98,114,164,0.55)",
      black: "#21222C", red: "#FF5555", green: "#50FA7B", yellow: "#F1FA8C", blue: "#BD93F9", magenta: "#FF79C6", cyan: "#8BE9FD", white: "#F8F8F2",
      brightBlack: "#6272A4", brightRed: "#FF6E6E", brightGreen: "#69FF94", brightYellow: "#FFFFA5", brightBlue: "#D6ACFF", brightMagenta: "#FF92DF", brightCyan: "#A4FFFF", brightWhite: "#FFFFFF",
    },
  },
};

const KEY = "bastion_term_prefs";
const EVT = "bastion-term-prefs";
export const DEFAULT_PREFS = { theme: "mobaxterm", fontSize: 14, highlight: true, copyOnSelect: true, rightClickPaste: true };

export function getTermPrefs() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULT_PREFS, ...JSON.parse(raw) } : { ...DEFAULT_PREFS };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export function setTermPrefs(patch) {
  const next = { ...getTermPrefs(), ...patch };
  if (!TERM_THEMES[next.theme]) next.theme = DEFAULT_PREFS.theme;
  next.fontSize = Math.min(24, Math.max(10, Number(next.fontSize) || DEFAULT_PREFS.fontSize));
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* modo privado: vale só nesta aba */ }
  window.dispatchEvent(new CustomEvent(EVT, { detail: next }));
  return next;
}

export function useTermPrefs() {
  const [prefs, setPrefs] = useState(getTermPrefs);
  useEffect(() => {
    const on = (e) => setPrefs(e.detail || getTermPrefs());
    window.addEventListener(EVT, on);
    return () => window.removeEventListener(EVT, on);
  }, []);
  return [prefs, setTermPrefs];
}
