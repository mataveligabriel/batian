import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { copyText } from "@/lib/clipboard";
import { StackedChart } from "@/components/flow/StackedChart";
import { FlowIfaceSelect } from "@/components/flow/FlowIfaceSelect";
import { FilterChips, FilterEditor, PeriodPicker } from "@/components/flow/FlowRawParts";
import { chip, colorize, DIM_TO_FILTER, fmtMinutes, fmtRate, fmtVolume, IP_DIMS, pct, RAW_DIMS, RAW_DIM_LABEL, selCls, useSeriesColors } from "@/components/flow/flowlib";
import { Activity, AlertTriangle, Ban, Copy, Filter, Info, Layers, Loader2, Plus, RefreshCw, X } from "lucide-react";

const STORE = "bastion_flow_query";
const load = () => { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { return {}; } };
const save = (v) => { try { localStorage.setItem(STORE, JSON.stringify(v)); } catch { /* sem armazenamento: tudo bem */ } };

// atalhos no estilo Wanguard/Kentik: um clique monta a consulta
export const QUERY_PRESETS = [
  ["Quem mais baixa", { group_by: ["dst_ip"], direction: "in", unit: "bps" }],
  ["Quem mais envia", { group_by: ["src_ip"], direction: "out", unit: "bps" }],
  ["AS de origem", { group_by: ["src_as"], direction: "in", unit: "bps" }],
  ["Blocos de origem", { group_by: ["src_pfx"], direction: "in", unit: "bps" }],
  ["Países de origem", { group_by: ["src_cc"], direction: "in", unit: "bps" }],
  ["Protocolo e porta", { group_by: ["proto", "sport"], direction: "in", unit: "bps" }],
  ["AS × IP de destino", { group_by: ["src_as", "dst_ip"], direction: "in", unit: "bps" }],
  ["Pacotes por IP (ataque?)", { group_by: ["dst_ip"], direction: "in", unit: "pps" }],
  ["Tamanho de pacote", { group_by: ["pktsize"], direction: "in", unit: "pps" }],
];

let consumedPreset = 0;
const INITIAL = { minutes: 60, start: null, end: null, ifs: [], direction: "in", group_by: ["dst_ip"], filters: [], unit: "bps", top: 10, pfx4: 24, pfx6: 48, stacked: true };

/** Aba Consulta: flow detalhado com qualquer filtro e até 3 agrupamentos (clique numa linha para filtrar). */
export function FlowQuery({ ifaces, preset, onOpenIp }) {
  const [st, setSt] = useState(() => ({ ...INITIAL, ...load() }));
  const set = (p) => setSt(s => ({ ...s, ...p }));
  useEffect(() => { save(st); }, [st]);
  useEffect(() => {      // consulta montada em outra aba: aplica uma vez só (voltar para a aba não desfaz o que o usuário mudou)
    if (!preset || preset._t <= consumedPreset) return;
    consumedPreset = preset._t;
    const { _t, ...p } = preset; // eslint-disable-line no-unused-vars
    setSt(s => ({ ...s, start: null, end: null, filters: [], ...p }));
  }, [preset]);
  useEffect(() => {
    const ids = new Set(ifaces.map(i => i.id));
    if (st.ifs.some(i => !ids.has(i))) set({ ifs: st.ifs.filter(i => ids.has(i)) });
  }, [ifaces]); // eslint-disable-line react-hooks/exhaustive-deps

  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const query = useMemo(() => ({
    minutes: st.minutes, start: st.start || null, end: st.end || null, interfaces: st.ifs, direction: st.direction,
    filters: st.filters, group_by: st.group_by.filter(Boolean), unit: st.unit, top: st.top, pfx4: st.pfx4, pfx6: st.pfx6,
  }), [st]);
  const key = JSON.stringify(query);
  useEffect(() => {
    if (!ifaces.length) return undefined;
    const my = ++seq.current;
    setLoading(true);
    const t = setTimeout(() => {
      api.post("/flow/raw/query", query)
        .then(r => { if (my === seq.current) { setRes(r.data); setErr(""); } })
        .catch(e => { if (my === seq.current) setErr(formatApiError(e)); })
        .finally(() => { if (my === seq.current) setLoading(false); });
    }, 350);
    return () => clearTimeout(t);
  }, [key, tick, ifaces.length]); // eslint-disable-line react-hooks/exhaustive-deps
  // período curto e "até agora": atualiza sozinho a cada minuto
  useEffect(() => {
    if (st.start || st.minutes > 360) return undefined;
    const t = setInterval(() => setTick(x => x + 1), 60000);
    return () => clearInterval(t);
  }, [st.start, st.minutes]);

  const assign = useSeriesColors();
  const { series, table } = useMemo(() => colorize(res, assign, null), [res]); // eslint-disable-line react-hooks/exhaustive-deps
  const unit = res?.unit || st.unit;
  const gb = res?.group_by || [];

  const addFilter = (f) => set({ filters: [...st.filters.filter(x => !(x.dim === f.dim && x.op === f.op)), f] });
  const partFilter = (p, op) => addFilter({ dim: DIM_TO_FILTER[p.dim], op, values: [p.value] });
  const setDim = (i, v) => { const g = [...st.group_by]; g[i] = v; set({ group_by: g }); };
  const copyTable = async () => {
    const head = [...gb.map(d => RAW_DIM_LABEL[d]), "média bps", "pico bps", "média pps", "pacote médio (B)", "volume (bytes)", "participação"].join("\t");
    const lines = (res?.table || []).map(r => [...r.parts.map(p => p.label), Math.round(r.avg_bps), Math.round(r.max_bps), Math.round(r.avg_pps), Math.round(r.pkt_size), Math.round(r.bytes), (r.share * 100).toFixed(2) + "%"].join("\t"));
    const ok = await copyText([head, ...lines].join("\n"));
    ok ? toast.success("Tabela copiada (cole no Excel / planilha)") : toast.error("Não consegui copiar");
  };

  if (!ifaces.length) {
    return <div className="text-sm text-slate-400 p-6 border border-dashed border-line rounded">Nenhuma interface monitorada. Vá em <b>Interfaces</b> e marque as que mandam flow para o BastiON.</div>;
  }
  const hasPfx = st.group_by.some(d => d === "src_pfx" || d === "dst_pfx");
  const coverage = res ? res.files / Math.max(1, res.minutes) : 1;

  return (
    <div data-testid="flow-query">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <PeriodPicker value={st} onChange={(p) => set(p)} />
        <FlowIfaceSelect ifaces={ifaces} value={st.ifs} onChange={(v) => set({ ifs: v })} testid="raw-ifsel" />
        <select value={st.direction} onChange={e => set({ direction: e.target.value })} className={selCls} data-testid="raw-dir"
                title="Entrada = o que chega pelas interfaces escolhidas · Saída = o que sai por elas">
          <option value="in">Entrada ↓</option><option value="out">Saída ↑</option>
        </select>
        <div className="flex gap-1 ml-auto">
          <button onClick={() => set({ unit: st.unit === "bps" ? "pps" : "bps" })} className={chip(false)} data-testid="raw-unit">{st.unit === "bps" ? "bits/s" : "pacotes/s"}</button>
          <button onClick={() => set({ stacked: true })} className={chip(st.stacked)} title="Área empilhada" aria-label="Área empilhada"><Layers className="w-3.5 h-3.5" /></button>
          <button onClick={() => set({ stacked: false })} className={chip(!st.stacked)} title="Linhas" aria-label="Linhas"><Activity className="w-3.5 h-3.5" /></button>
          <button onClick={() => setTick(x => x + 1)} className={chip(false)} title="Consultar de novo" aria-label="Consultar de novo"><RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /></button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 mb-2" data-testid="raw-groupby">
        <span className="text-[11px] text-slate-500 font-mono mr-0.5">agrupar por</span>
        {st.group_by.map((d, i) => (
          <span key={i} className="inline-flex items-center">
            <select value={d} onChange={e => setDim(i, e.target.value)} className={selCls} aria-label={`Agrupamento ${i + 1}`}>
              {RAW_DIMS.filter(([v]) => v === d || !st.group_by.includes(v)).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <button onClick={() => set({ group_by: st.group_by.filter((_, j) => j !== i) })} className="ml-0.5 p-1 rounded text-slate-500 hover:text-slate-200" aria-label="Tirar agrupamento"><X className="w-3.5 h-3.5" /></button>
            {i < st.group_by.length - 1 && <span className="text-slate-600 mx-0.5">×</span>}
          </span>
        ))}
        {st.group_by.length < 3 && (
          <button onClick={() => set({ group_by: [...st.group_by, RAW_DIMS.find(([v]) => !st.group_by.includes(v))[0]] })} className={`${chip(false)} inline-flex items-center gap-1`} data-testid="raw-add-dim">
            <Plus className="w-3.5 h-3.5" /> {st.group_by.length ? "cruzar com" : "dimensão"}
          </button>
        )}
        {!st.group_by.length && <span className="text-[11px] text-slate-500">sem agrupamento: mostra o total</span>}
        {hasPfx && (
          <span className="inline-flex items-center gap-1 text-[11px] text-slate-400 ml-1">
            bloco IPv4 /<input type="number" min={8} max={32} value={st.pfx4} onChange={e => set({ pfx4: Number(e.target.value) || 24 })} className={`${selCls} w-14`} aria-label="Tamanho do bloco IPv4" />
            IPv6 /<input type="number" min={16} max={128} value={st.pfx6} onChange={e => set({ pfx6: Number(e.target.value) || 48 })} className={`${selCls} w-16`} aria-label="Tamanho do bloco IPv6" />
          </span>
        )}
        <span className="inline-flex items-center gap-1 text-[11px] text-slate-500 ml-1">mostrar
          <select value={st.top} onChange={e => set({ top: Number(e.target.value) })} className={selCls} aria-label="Quantidade de linhas">
            {[5, 10, 20, 30, 50, 100].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        <FilterEditor onAdd={addFilter} />
        <FilterChips filters={st.filters} onChange={(filters) => set({ filters })} />
      </div>

      <div className="flex flex-wrap items-center gap-1 mb-3" data-testid="raw-presets">
        <span className="text-[11px] text-slate-500 font-mono mr-1">atalhos</span>
        {QUERY_PRESETS.map(([l, p]) => <button key={l} onClick={() => set(p)} className="h-7 px-2 rounded-md border border-line text-[11px] text-slate-300 hover:border-brand hover:text-slate-100">{l}</button>)}
      </div>

      {err && <div className="mb-2 text-xs text-amber-300 font-mono flex items-start gap-1.5" data-testid="raw-err"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}

      {res && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-2" data-testid="raw-summary">
          {[["Média", fmtRate(res.total.avg_bps)], ["Pico", fmtRate(res.total.max_bps)], ["Pacotes (média / pico)", `${fmtRate(res.total.avg_pps, "pps")} / ${fmtRate(res.total.max_pps, "pps")}`],
            ["Volume", fmtVolume(res.total.bytes, "bytes")], ["Pacote médio", res.total.pkts ? `${Math.round(res.total.bytes / res.total.pkts)} B` : "—"]].map(([l, v]) => (
            <div key={l} className="border border-line rounded-md px-2.5 py-1.5"><div className="text-[10px] text-slate-500">{l}</div><div className="text-sm font-mono text-slate-100">{v}</div></div>
          ))}
        </div>
      )}

      <StackedChart ts={res?.ts || []} series={series} unit={unit} stacked={st.stacked} height={300} loading={loading && !res}
                    emptyText={res && !res.files ? "Ainda não há flow detalhado neste período (ele começa a ser gravado depois da atualização)." : "Nada casa com estes filtros no período."} />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-[10px] font-mono text-slate-500">
        {loading && <span className="flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> consultando…</span>}
        {res && <span>resolução {res.step >= 3600 ? `${res.step / 3600} h` : `${res.step / 60} min`} · {fmtMinutes(res.minutes)} · fonte: flow detalhado</span>}
        {res && coverage < 0.98 && <span className="text-amber-300/90">dados em {res.files} de {res.minutes} minutos</span>}
        {res?.coarse > 0 && <span className="text-amber-300/90" title="Minuto com chaves demais (ataque com origem muito espalhada): parte das origens foi somada por bloco e sem porta de origem.">origem resumida em {res.coarse.toLocaleString("pt-BR")} flows</span>}
      </div>

      {table.length > 0 && (
        <div className="mt-3">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] text-slate-400">Clique num valor para filtrar por ele; <Ban className="w-3 h-3 inline" /> exclui.</span>
            <button onClick={copyTable} className="ml-auto text-[11px] text-brand-soft hover:underline inline-flex items-center gap-1" data-testid="raw-copy"><Copy className="w-3 h-3" /> copiar tabela</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono" data-testid="raw-table">
              <thead className="text-[10px] text-slate-500">
                <tr>
                  {gb.map(d => <th key={d} className="text-left py-1 pr-3 font-normal">{RAW_DIM_LABEL[d]}</th>)}
                  <th className="text-right px-2 font-normal">Média</th><th className="text-right px-2 font-normal">p95</th>
                  <th className="text-right px-2 font-normal">Pico</th><th className="text-right px-2 font-normal">{unit === "pps" ? "bits/s médio" : "pacotes/s médio"}</th>
                  <th className="text-right px-2 font-normal">Pacote</th><th className="text-right px-2 font-normal">Volume</th>
                  <th className="text-left pl-3 w-[18%] min-w-[110px] font-normal">Participação</th>
                </tr>
              </thead>
              <tbody>
                {table.map(r => (
                  <tr key={r.id} className="border-t border-line group" data-testid="raw-row">
                    {(r.parts || [{ label: r.name, dim: "", value: "" }]).map((p, i) => (
                      <td key={i} className="py-1 pr-3 max-w-[320px]">
                        <div className="flex items-center gap-1.5 min-w-0">
                          {i === 0 && <span className="w-2.5 h-2.5 rounded-[2px] shrink-0" style={{ background: r.color }} />}
                          {p.dim && p.value !== "" ? (
                            <button onClick={() => partFilter(p, "in")} className="text-slate-100 truncate hover:text-brand-soft hover:underline text-left" title={`Filtrar: ${p.label}`}>{p.label}</button>
                          ) : <span className="text-slate-100 truncate" title={p.label}>{p.label}</span>}
                          {p.dim && p.value !== "" && (
                            <span className="inline-flex items-center gap-0.5 shrink-0 opacity-40 group-hover:opacity-100 focus-within:opacity-100">
                              <button onClick={() => partFilter(p, "not")} className="p-0.5 text-slate-400 hover:text-amber-300" aria-label={`Excluir ${p.label}`} title="Excluir este valor"><Ban className="w-3 h-3" /></button>
                              {IP_DIMS.includes(p.dim) && onOpenIp && <button onClick={() => onOpenIp(p.value)} className="px-1 rounded text-[10px] text-slate-400 hover:text-brand-soft border border-line" title="Consultar este IP / bloco">IP</button>}
                            </span>
                          )}
                        </div>
                      </td>
                    ))}
                    <td className="text-right px-2 text-slate-100 whitespace-nowrap">{fmtRate(r.avg, unit)}</td>
                    <td className="text-right px-2 text-slate-300 whitespace-nowrap">{fmtRate(r.p95, unit)}</td>
                    <td className="text-right px-2 text-slate-50 font-semibold whitespace-nowrap">{fmtRate(r.max, unit)}</td>
                    <td className="text-right px-2 text-slate-400 whitespace-nowrap">{unit === "pps" ? fmtRate(r.avg_bps) : fmtRate(r.avg_pps, "pps")}</td>
                    <td className="text-right px-2 text-slate-400 whitespace-nowrap">{r.pkt_size ? `${Math.round(r.pkt_size)} B` : "—"}</td>
                    <td className="text-right px-2 text-slate-400 whitespace-nowrap">{fmtVolume(r.bytes, "bytes")}</td>
                    <td className="pl-3">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 rounded bg-line overflow-hidden"><div className="h-full rounded" style={{ width: `${Math.min(100, (r.share || 0) * 100)}%`, background: r.color }} /></div>
                        <span className="text-[10px] text-slate-300 w-11 text-right">{pct(r.share || 0)}</span>
                      </div>
                    </td>
                  </tr>
                ))}
                {res?.other && (
                  <tr className="border-t border-line text-slate-500">
                    <td colSpan={gb.length} className="py-1 pr-3">+ {res.other.groups.toLocaleString("pt-BR")} outros</td>
                    <td className="text-right px-2">{fmtRate(res.other.avg, unit)}</td><td /><td className="text-right px-2">{fmtRate(res.other.max, unit)}</td>
                    <td /><td /><td className="text-right px-2">{fmtVolume(res.other.bytes, "bytes")}</td>
                    <td className="pl-3 text-[10px]">{pct(res.other.share || 0)}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="mt-3 text-[11px] text-slate-500 flex items-start gap-1.5 max-w-4xl">
        <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>O flow detalhado guarda cada minuto com IP, porta, AS e interface — dá para achar qualquer IP, não só os maiores. Os valores já vêm
          multiplicados pela amostragem do roteador. Portas altas (efêmeras) aparecem como 0. Consultas longas (24 h ou mais) sem filtro demoram mais;
          filtre por IP, bloco ou AS para respostas rápidas. <Filter className="w-3 h-3 inline" /> Retenção e espaço em disco em Configuração.</span>
      </div>
    </div>
  );
}
