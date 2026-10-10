import React, { useMemo, useState } from "react";
import { Search, ChevronRight, ChevronDown } from "lucide-react";

/** Seleção de equipamentos agrupada por tag: os grupos vêm fechados; marca o grupo inteiro pela caixa da tag
 *  ou abre o grupo e escolhe os equipamentos um a um. Um equipamento com várias tags aparece em cada grupo.
 *  devices: [{id, name, host, tags}] · selected: array ou Set · onChange(novoArrayDeIds)
 *  extra(d): conteúdo opcional na linha do equipamento (ex.: botão "tornar público"). */
export function TagSelect({ devices, selected, onChange, extra, testid = "tag-select", maxHeight = "max-h-80" }) {
  const sel = useMemo(() => (selected instanceof Set ? selected : new Set(selected || [])), [selected]);
  const [open, setOpen] = useState(() => new Set());
  const [q, setQ] = useState("");
  const t = q.trim().toLowerCase();
  const groups = useMemo(() => {
    const m = new Map();
    for (const d of devices || []) {
      if (t && !`${d.name} ${d.host || ""} ${(d.tags || []).join(" ")} ${d.device_type || ""}`.toLowerCase().includes(t)) continue;
      const tags = (d.tags || []).length ? d.tags : ["__none__"];
      for (const tg of tags) {
        if (!m.has(tg)) m.set(tg, []);
        m.get(tg).push(d);
      }
    }
    for (const list of m.values()) list.sort((a, b) => a.name.localeCompare(b.name));
    return [...m.entries()].sort((a, b) => (a[0] === "__none__") - (b[0] === "__none__") || a[0].localeCompare(b[0]));
  }, [devices, t]);
  const setMany = (ids, on) => {
    const n = new Set(sel);
    ids.forEach(id => (on ? n.add(id) : n.delete(id)));
    onChange([...n]);
  };
  const flipOne = (id) => setMany([id], !sel.has(id));
  const flipOpen = (g) => setOpen(prev => { const n = new Set(prev); n.has(g) ? n.delete(g) : n.add(g); return n; });

  return (
    <div className="space-y-2" data-testid={testid}>
      <div className="relative">
        <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar tag, nome ou IP…" data-testid={`${testid}-search`}
          className="w-full h-8 pl-8 pr-2 rounded-md bg-sunken border border-line text-sm text-slate-100 focus:outline-none focus:border-brand" />
      </div>
      <div className={`${maxHeight} overflow-y-auto border border-line rounded-md divide-y divide-line/70`}>
        {groups.length === 0 && <div className="px-3 py-4 text-xs text-slate-500">Nenhum equipamento{t ? " com essa busca" : ""}.</div>}
        {groups.map(([g, list]) => {
          const ids = list.map(d => d.id);
          const on = ids.filter(id => sel.has(id)).length;
          const isOpen = open.has(g) || !!t;
          return (
            <div key={g} data-testid={`${testid}-g-${g}`}>
              <div className="flex items-center gap-2 px-2 py-1.5 bg-panel text-sm">
                <input type="checkbox" className="accent-brand w-4 h-4 shrink-0" checked={on === ids.length && ids.length > 0}
                  ref={el => { if (el) el.indeterminate = on > 0 && on < ids.length; }}
                  onChange={() => setMany(ids, on !== ids.length)} aria-label={`Marcar o grupo ${g}`} data-testid={`${testid}-gc-${g}`} />
                <button type="button" onClick={() => flipOpen(g)} aria-expanded={isOpen} data-testid={`${testid}-go-${g}`}
                  className="flex-1 flex items-center gap-1.5 text-left text-slate-200 hover:text-white">
                  {isOpen ? <ChevronDown className="w-3.5 h-3.5 text-slate-500" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-500" />}
                  <span className="font-medium">{g === "__none__" ? "Sem tag" : g}</span>
                  <span className={`text-xs ${on ? "text-brand-soft" : "text-slate-500"}`}>{on ? `${on}/` : ""}{ids.length}</span>
                </button>
              </div>
              {isOpen && list.map(d => (
                <label key={d.id} className="flex items-center gap-2 pl-8 pr-2 py-1.5 text-sm cursor-pointer hover:bg-white/[0.04]" data-testid={`${testid}-d-${d.id}`}>
                  <input type="checkbox" className="accent-brand w-4 h-4 shrink-0" checked={sel.has(d.id)} onChange={() => flipOne(d.id)} />
                  <span className="text-slate-200 truncate">{d.name}</span>
                  {extra && extra(d)}
                  <span className="ml-auto text-[11px] font-mono text-slate-500 truncate">{d.host}{d.device_type ? ` · ${d.device_type}` : ""}</span>
                </label>
              ))}
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-3 text-[11px] text-slate-500">
        <span>{sel.size} equipamento(s) selecionado(s)</span>
        {sel.size > 0 && <button type="button" onClick={() => onChange([])} className="hover:text-slate-200" data-testid={`${testid}-clear`}>limpar</button>}
      </div>
    </div>
  );
}

/** <option>s de um <select> agrupadas por tag (<optgroup>). Equipamento com várias tags aparece em cada grupo. */
export function TagOptions({ devices, label }) {
  const groups = new Map();
  for (const d of [...(devices || [])].sort((a, b) => a.name.localeCompare(b.name))) {
    for (const t of (d.tags || []).length ? d.tags : ["Sem tag"]) {
      if (!groups.has(t)) groups.set(t, []);
      groups.get(t).push(d);
    }
  }
  const keys = [...groups.keys()].sort((a, b) => (a === "Sem tag") - (b === "Sem tag") || a.localeCompare(b));
  return keys.map(t => (
    <optgroup key={t} label={t}>
      {groups.get(t).map(d => <option key={`${t}-${d.id}`} value={d.id}>{label ? label(d) : d.name}</option>)}
    </optgroup>
  ));
}
