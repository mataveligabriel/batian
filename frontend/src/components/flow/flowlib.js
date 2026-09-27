// Utilitários da Análise de Flow: paleta, formatação, papéis e cores estáveis por série.
import { useRef } from "react";

// Paleta categórica (passos do modo escuro, validada contra o fundo #0f1520: CVD ΔE ≥ 8,4 entre vizinhas, contraste ≥ 3:1).
// A ordem é o mecanismo de segurança para daltonismo: nunca reordenar nem gerar uma 9ª cor.
export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
export const OTHER = "#64748B";
export const MAX_SERIES = SERIES.length;

export const ROLE_LABEL = { transito: "Trânsito", pni: "PNI", ix: "IX / PTT", cdn: "CDN / cache", cliente: "Cliente", outro: "Outro" };
export const EXTERNAL = ["transito", "pni", "ix", "cdn"];

export const RANGES = [[60, "1h"], [360, "6h"], [1440, "24h"], [2880, "48h"], [10080, "7 dias"], [43200, "30 dias"]];

export const GROUP_BY = [
  ["interface", "Interface"], ["group", "Conteúdo"], ["sas", "AS de origem"], ["das", "AS de destino"],
  ["spfx", "Prefixo de origem"], ["dpfx", "Prefixo de destino"], ["sip", "IP de origem"], ["dip", "IP de destino"],
  ["proto", "Protocolo"], ["sport", "Porta de origem"], ["dport", "Porta de destino"], ["peer", "Interface par"],
];
export const DIM_LABEL = Object.fromEntries(GROUP_BY);
export const PFX_DIMS = ["spfx", "dpfx", "sip", "dip"];

export const fmtRate = (v, unit = "bps") => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (unit === "pps") {
    if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 1 : 2)} Mpps`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(v >= 1e4 ? 0 : 1)} kpps`;
    return `${Math.round(v)} pps`;
  }
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)} Tbps`;
  if (v >= 1e9) return `${(v / 1e9).toFixed(v >= 1e10 ? 1 : 2)} Gbps`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e8 ? 0 : 1)} Mbps`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)} Kbps`;
  return `${Math.round(v)} bps`;
};
export const fmtAxis = (v) => {
  if (!v) return "0";
  if (v >= 1e12) return `${+(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `${+(v / 1e9).toFixed(1)}G`;
  if (v >= 1e6) return `${+(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${+(v / 1e3).toFixed(0)}K`;
  return `${Math.round(v)}`;
};
export const fmtVolume = (units, unit = "bps") => {
  if (unit === "pps") {
    if (units >= 1e9) return `${(units / 1e9).toFixed(1)} bi pkts`;
    if (units >= 1e6) return `${(units / 1e6).toFixed(1)} mi pkts`;
    return `${Math.round(units).toLocaleString("pt-BR")} pkts`;
  }
  for (const [u, d] of [["PB", 1e15], ["TB", 1e12], ["GB", 1e9], ["MB", 1e6], ["KB", 1e3]]) if (units >= d) return `${(units / d).toFixed(1)} ${u}`;
  return `${Math.round(units)} B`;
};
export const ago = (iso) => {
  if (!iso) return "nunca";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `há ${Math.round(s)} s`;
  if (s < 3600) return `há ${Math.round(s / 60)} min`;
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  return `há ${Math.round(s / 86400)} d`;
};
export const fmtDur = (sec) => {
  if (sec < 60) return `${Math.round(sec)} s`;
  if (sec < 3600) return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
  return `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min`;
};

/** Cor segue a entidade, nunca a posição: um filtro que muda a quantidade de séries não repinta as que ficaram. */
export function useSeriesColors() {
  const ref = useRef(new Map());
  return (ids) => {
    const map = ref.current;
    const live = new Set(ids);
    for (const [id] of map) if (!live.has(id)) map.delete(id);
    const used = new Set([...map.values()]);
    const out = {};
    for (const id of ids) {
      if (map.has(id)) { out[id] = map.get(id); continue; }
      let slot = -1;
      for (let i = 0; i < SERIES.length; i++) if (!used.has(i)) { slot = i; break; }
      if (slot < 0) { out[id] = null; continue; }
      map.set(id, slot); used.add(slot); out[id] = slot;
    }
    return out;
  };
}

/**
 * Junta o resultado da API com as cores: até 8 séries coloridas (as maiores), o resto dobra em "Outros".
 * Devolve { series:[{id,name,color,values,other}], table } na mesma ordem para gráfico, legenda e tabela.
 */
export function colorize(res, assign, fixed) {
  if (!res) return { series: [], table: [] };
  const src = res.series || [];
  const named = src.filter(s => !s.other);
  const keep = named.slice(0, MAX_SERIES);
  const fold = named.slice(MAX_SERIES);
  const useFixed = fixed && keep.length && keep.every(s => fixed[s.id]);
  const slots = useFixed ? null : assign(keep.map(s => s.id));
  const series = keep.map(s => ({ ...s, color: useFixed ? fixed[s.id] : SERIES[slots[s.id]] }));
  const otherSrc = [...fold, ...src.filter(s => s.other)];
  if (otherSrc.length) {
    const n = res.ts.length;
    const values = Array.from({ length: n }, (_, i) => otherSrc.reduce((a, s) => a + (s.values[i] || 0), 0));
    series.push({ id: "__other", name: fold.length ? `Outros (${fold.length}${src.some(s => s.other) ? "+" : ""})` : "Outros", color: OTHER, values, other: true });
  }
  const colorOf = Object.fromEntries(series.map(s => [s.id, s.color]));
  const table = (res.table || []).map(r => ({ ...r, color: colorOf[r.id] || OTHER, folded: !colorOf[r.id] }));
  return { series, table };
}

export const selCls = "h-8 rounded-md bg-[#05070A] border border-[#1E293B] text-slate-200 text-xs px-2 focus:outline-none focus:border-[#007AFF]";
export const inputCls = "bg-[#05070A] border-[#1E293B]";
export const chip = (on) => `h-8 px-2.5 rounded-md border text-xs font-mono whitespace-nowrap ${on ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] text-slate-400 hover:text-slate-200"}`;

/** Cor fixa por entidade quando cabem na paleta (até 8): a mesma interface tem a mesma cor em todas as telas. */
export function fixedColors(ids) {
  if (!ids.length || ids.length > MAX_SERIES) return null;
  return Object.fromEntries(ids.map((id, i) => [id, SERIES[i]]));
}
