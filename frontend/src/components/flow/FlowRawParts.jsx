import React, { useState } from "react";
import { Input } from "@/components/ui/input";
import { Plus, X, Filter, Ban } from "lucide-react";
import { chip, fmtRate, inputCls, pct, RAW_FILTERS, RAW_FILTER_LABEL, RAW_PLACEHOLDER, RAW_RANGES, selCls, SERIES } from "@/components/flow/flowlib";

/** Período: atalhos (últimos N) ou personalizado (de/até no horário local). */
export function PeriodPicker({ value, onChange, ranges = RAW_RANGES, testid = "raw-range" }) {
  const custom = !!(value.start && value.end);
  const toLocal = (iso) => { if (!iso) return ""; const d = new Date(iso); const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
  const setLocal = (k, v) => { const d = new Date(v); if (!isNaN(d)) onChange({ ...value, [k]: d.toISOString() }); };
  const startCustom = () => {
    const end = new Date(); end.setSeconds(0, 0);
    onChange({ ...value, start: new Date(end - (value.minutes || 60) * 60000).toISOString(), end: end.toISOString() });
  };
  return (
    <div className="flex flex-wrap items-center gap-1" data-testid={testid}>
      {ranges.map(([m, l]) => (
        <button key={m} onClick={() => onChange({ minutes: m, start: null, end: null })} className={chip(!custom && value.minutes === m)} data-testid={`${testid}-${m}`}>{l}</button>
      ))}
      <button onClick={() => (custom ? onChange({ ...value, start: null, end: null }) : startCustom())} className={chip(custom)} data-testid={`${testid}-custom`}>personalizado</button>
      {custom && (
        <span className="flex items-center gap-1 text-[11px] text-slate-400">
          de <input type="datetime-local" value={toLocal(value.start)} onChange={e => setLocal("start", e.target.value)} className={`${selCls} w-[11.5rem]`} aria-label="Início" />
          até <input type="datetime-local" value={toLocal(value.end)} onChange={e => setLocal("end", e.target.value)} className={`${selCls} w-[11.5rem]`} aria-label="Fim" />
        </span>
      )}
    </div>
  );
}

/** Filtros como etiquetas ("IP de destino é x.x.x.x"), com remover e inverter (é / não é). */
export function FilterChips({ filters, onChange, labels = {} }) {
  if (!filters.length) return null;
  const upd = (i, p) => onChange(filters.map((f, j) => (j === i ? { ...f, ...p } : f)));
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="raw-filter-chips">
      {filters.map((f, i) => (
        <span key={i} className={`inline-flex items-center gap-1 h-7 pl-2 pr-1 rounded-md border text-xs ${f.op === "not" ? "border-amber-500/60 bg-amber-500/10" : "border-brand/60 bg-brand/10"}`} data-testid="raw-filter-chip">
          <span className="text-slate-400">{RAW_FILTER_LABEL[f.dim] || f.dim}</span>
          <button className={`font-semibold ${f.op === "not" ? "text-amber-300" : "text-brand-soft"} hover:underline`} title="Trocar entre é / não é" onClick={() => upd(i, { op: f.op === "not" ? "in" : "not" })}>
            {f.op === "not" ? "não é" : "é"}
          </button>
          <span className="font-mono text-slate-100 max-w-[22rem] truncate" title={f.values.join(", ")}>{f.values.map(v => labels[`${f.dim}:${v}`] || v).join(", ")}</span>
          <button onClick={() => onChange(filters.filter((_, j) => j !== i))} className="p-0.5 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-700" aria-label="Remover filtro"><X className="w-3.5 h-3.5" /></button>
        </span>
      ))}
      <button onClick={() => onChange([])} className="text-[11px] text-slate-500 hover:text-slate-300 px-1">limpar filtros</button>
    </div>
  );
}

/** "+ Filtro": dimensão, é / não é, valores separados por vírgula. */
export function FilterEditor({ onAdd }) {
  const [open, setOpen] = useState(false);
  const [dim, setDim] = useState("dst_ip");
  const [op, setOp] = useState("in");
  const [vals, setVals] = useState("");
  const add = () => {
    const values = vals.split(/[\s,;]+/).map(v => v.trim()).filter(Boolean);
    if (!values.length) return;
    onAdd({ dim, op, values });
    setVals("");
    setOpen(false);
  };
  if (!open) {
    return <button onClick={() => setOpen(true)} className={`${chip(false)} inline-flex items-center gap-1`} data-testid="raw-add-filter"><Plus className="w-3.5 h-3.5" /> Filtro</button>;
  }
  return (
    <form className="flex flex-wrap items-center gap-1.5" onSubmit={e => { e.preventDefault(); add(); }} data-testid="raw-filter-editor">
      <select value={dim} onChange={e => setDim(e.target.value)} className={selCls} aria-label="Campo do filtro">
        {RAW_FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <select value={op} onChange={e => setOp(e.target.value)} className={selCls} aria-label="Condição">
        <option value="in">é</option><option value="not">não é</option>
      </select>
      <Input autoFocus value={vals} onChange={e => setVals(e.target.value)} placeholder={RAW_PLACEHOLDER[dim]} className={`${inputCls} h-8 w-64 text-xs font-mono`} data-testid="raw-filter-values" />
      <button type="submit" className="h-8 px-3 rounded-md bg-brand text-onbrand text-xs font-medium hover:opacity-90">Aplicar</button>
      <button type="button" onClick={() => setOpen(false)} className="h-8 px-2 text-xs text-slate-400 hover:text-slate-200">cancelar</button>
    </form>
  );
}

/**
 * Tabela compacta "quem é quem" com barra de participação. rows: saída da API ({name, parts, avg_bps, avg_pps, share, pkt_size…}).
 * onFilter(row, op) recebe o clique em "filtrar" / "excluir"; onIp(ip) abre o Consultar IP.
 */
export function ShareTable({ title, rows, other, unit = "bps", onFilter, onIp, limit = 10, testid, empty = "sem dados", color = SERIES[0], right }) {
  const [all, setAll] = useState(false);
  const list = all ? rows : rows.slice(0, limit);
  return (
    <div className="border border-line rounded-md p-2.5 min-w-0" data-testid={testid}>
      <div className="flex items-baseline gap-2 mb-1.5">
        <div className="text-[11px] font-medium text-slate-300">{title}</div>
        {right}
      </div>
      {!rows.length && <div className="text-[11px] text-slate-500 py-2">{empty}</div>}
      <div className="space-y-0.5">
        {list.map(r => {
          const ip = r.parts?.find(p => p.dim === "src_ip" || p.dim === "dst_ip")?.value;
          return (
            <div key={r.id} className="group grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 items-center text-xs font-mono py-0.5" data-testid="share-row">
              <div className="min-w-0">
                <div className="flex items-center gap-1 min-w-0">
                  <span className="truncate text-slate-200" title={r.name}>{r.name}</span>
                  <span className="inline-flex items-center gap-0.5 shrink-0 opacity-40 group-hover:opacity-100 focus-within:opacity-100">
                    {onFilter && <button onClick={() => onFilter(r, "in")} className="p-0.5 rounded text-slate-400 hover:text-brand-soft" title="Filtrar por este valor" aria-label="Filtrar por este valor"><Filter className="w-3 h-3" /></button>}
                    {onFilter && <button onClick={() => onFilter(r, "not")} className="p-0.5 rounded text-slate-400 hover:text-amber-300" title="Excluir este valor" aria-label="Excluir este valor"><Ban className="w-3 h-3" /></button>}
                    {onIp && ip && <button onClick={() => onIp(ip)} className="px-1 rounded text-[10px] text-slate-400 hover:text-brand-soft border border-line" title="Consultar este IP">IP</button>}
                  </span>
                </div>
                <div className="h-1 rounded bg-line overflow-hidden mt-0.5"><div className="h-full rounded" style={{ width: `${Math.min(100, (r.share || 0) * 100)}%`, background: color }} /></div>
              </div>
              <div className="text-right whitespace-nowrap">
                <span className="text-slate-100">{fmtRate(unit === "pps" ? r.avg_pps : r.avg_bps, unit)}</span>
                <span className="text-slate-500 ml-1.5 inline-block w-11">{pct(r.share || 0)}</span>
              </div>
            </div>
          );
        })}
      </div>
      {(rows.length > limit || other) && (
        <div className="flex items-center gap-3 mt-1 text-[10px] text-slate-500">
          {rows.length > limit && <button onClick={() => setAll(!all)} className="hover:text-slate-300">{all ? "mostrar menos" : `ver os ${rows.length}`}</button>}
          {other && <span>+ {other.groups.toLocaleString("pt-BR")} outros ({pct(other.share || 0)})</span>}
        </div>
      )}
    </div>
  );
}
