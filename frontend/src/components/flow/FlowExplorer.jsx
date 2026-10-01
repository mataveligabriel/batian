import React, { useEffect, useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import { FlowView } from "@/components/flow/FlowView";
import { FlowIfaceSelect } from "@/components/flow/FlowIfaceSelect";
import { chip, DIM_LABEL, GROUP_BY, PFX_DIMS, RANGES, selCls, inputCls } from "@/components/flow/flowlib";
import { Layers, Activity, Info } from "lucide-react";

const STORE = "bastion_flow_explorer";
const load = () => { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { return {}; } };
const save = (v) => { try { localStorage.setItem(STORE, JSON.stringify(v)); } catch { /* sem armazenamento: tudo bem */ } };

const PLACEHOLDER = {
  sas: "ex.: 15169, 32934", das: "ex.: 263000", spfx: "ex.: 142.250.0.0/15", dpfx: "ex.: 177.10.0.0/22, 2804:1::/32",
  sip: "ex.: 8.8.8.8 ou 8.8.8.0/24", dip: "ex.: 177.10.0.50", proto: "ex.: UDP, TCP, 47", sport: "ex.: 123, 53", dport: "ex.: 443", peer: "ifIndex, ex.: 20",
};

/** Aba Explorar: filtros numa linha, gráfico empilhado e tabela. */
export function FlowExplorer({ ifaces, groups, preset, ifColors, groupColors }) {
  const saved = useMemo(load, []);
  const [st, setSt] = useState({ minutes: 360, ifs: [], direction: "in", group_by: "interface", fdim: "", fvals: "", fgroup: "", by_block: false, unit: "bps", stacked: true, ...saved });
  const set = (p) => setSt(s => ({ ...s, ...p }));
  useEffect(() => { save(st); }, [st]);
  useEffect(() => { if (preset) setSt(s => ({ ...s, ...preset })); }, [preset]);
  useEffect(() => {   // interfaces removidas saem da seleção
    const ids = new Set(ifaces.map(i => i.id));
    if (st.ifs.some(i => !ids.has(i))) set({ ifs: st.ifs.filter(i => ids.has(i)) });
  }, [ifaces]); // eslint-disable-line react-hooks/exhaustive-deps

  const gb = st.group_by;
  const dimOptions = gb === "interface" ? ["group", ...GROUP_BY.map(g => g[0]).filter(d => !["interface", "group"].includes(d))]
    : gb === "group" ? ["group"] : [gb];
  const fdim = dimOptions.includes(st.fdim) ? st.fdim : "";
  const values = fdim === "group" ? (st.fgroup ? [st.fgroup] : []) : st.fvals.split(/[\s,;]+/).map(v => v.trim()).filter(Boolean);
  const dir = st.direction === "both" && gb !== "interface" ? "in" : st.direction;
  const query = ifaces.length ? {
    interfaces: st.ifs, minutes: st.minutes, direction: dir, group_by: gb, top: gb === "interface" || gb === "group" ? 30 : 15,
    unit: st.unit, by_block: !!(st.by_block && PFX_DIMS.includes(fdim)), filter: fdim && values.length ? { dim: fdim, values } : { dim: null, values: [] },
  } : null;

  if (!ifaces.length) {
    return <div className="text-sm text-slate-400 p-6 border border-dashed border-line rounded" data-testid="flow-noifaces">
      Nenhuma interface monitorada. Vá em <b>Interfaces</b>, escolha o equipamento e marque os trânsitos, PNIs e IXs que já mandam flow para o BastiON.
    </div>;
  }
  return (
    <div data-testid="flow-explorer">
      <div className="flex flex-wrap items-center gap-2 mb-3" data-testid="flow-filters">
        <div className="flex gap-1">{RANGES.map(([m, l]) => <button key={m} onClick={() => set({ minutes: m })} className={chip(st.minutes === m)} data-testid={`fx-range-${m}`}>{l}</button>)}</div>
        <FlowIfaceSelect ifaces={ifaces} value={st.ifs} onChange={(v) => set({ ifs: v })} />
        <select value={dir} onChange={e => set({ direction: e.target.value })} className={selCls} data-testid="fx-dir" title="Entrada = o que chega pela interface (download de quem está atrás dela)">
          <option value="in">Entrada ↓</option><option value="out">Saída ↑</option>{gb === "interface" && <option value="both">Entrada e saída</option>}
        </select>
        <label className="text-[11px] text-slate-500 font-mono">agrupar</label>
        <select value={gb} onChange={e => set({ group_by: e.target.value })} className={selCls} data-testid="fx-groupby">
          {GROUP_BY.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <label className="text-[11px] text-slate-500 font-mono">filtro</label>
        <select value={fdim} onChange={e => set({ fdim: e.target.value })} className={selCls} data-testid="fx-fdim">
          <option value="">nenhum</option>
          {dimOptions.map(d => <option key={d} value={d}>{DIM_LABEL[d]}</option>)}
        </select>
        {fdim === "group" && (
          <select value={st.fgroup} onChange={e => set({ fgroup: e.target.value })} className={selCls} data-testid="fx-fgroup">
            <option value="">escolha o conteúdo…</option>{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        )}
        {fdim && fdim !== "group" && (
          <Input value={st.fvals} onChange={e => set({ fvals: e.target.value })} placeholder={PLACEHOLDER[fdim]} className={`${inputCls} h-8 w-60 text-xs font-mono`} data-testid="fx-fvals" />
        )}
        {PFX_DIMS.includes(fdim) && gb === fdim && (
          <label className="text-[11px] text-slate-400 flex items-center gap-1 cursor-pointer" title="Soma os /24 (/48) e IPs dentro de cada bloco informado">
            <input type="checkbox" checked={!!st.by_block} onChange={e => set({ by_block: e.target.checked })} data-testid="fx-byblock" /> somar por bloco
          </label>
        )}
        <div className="flex gap-1 ml-auto">
          <button onClick={() => set({ unit: st.unit === "bps" ? "pps" : "bps" })} className={chip(false)} data-testid="fx-unit">{st.unit === "bps" ? "bits/s" : "pacotes/s"}</button>
          <button onClick={() => set({ stacked: true })} className={chip(st.stacked)} title="Área empilhada"><Layers className="w-3.5 h-3.5" /></button>
          <button onClick={() => set({ stacked: false })} className={chip(!st.stacked)} title="Linhas"><Activity className="w-3.5 h-3.5" /></button>
        </div>
      </div>
      <FlowView query={query} stacked={st.stacked} fixed={gb === "interface" && dir !== "both" ? ifColors : gb === "group" ? groupColors : null} shareLabel={gb === "interface" ? "Participação entre as interfaces" : "Participação no total"} />
      <div className="mt-3 text-[11px] text-slate-500 flex items-start gap-1.5 max-w-4xl">
        <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>Interface e Conteúdo usam os totais exatos por minuto. AS, prefixo, IP, porta e protocolo vêm dos detalhes de 5 min (os maiores de cada
          dimensão), por isso o filtro precisa ser da mesma dimensão do agrupamento — ou agrupe por interface e filtre por qualquer uma.
          Para cruzar AS + bloco com precisão, cadastre um <b>Conteúdo</b>.</span>
      </div>
    </div>
  );
}
