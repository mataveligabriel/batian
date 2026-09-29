import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Loader2, Handshake, Info, AlertTriangle } from "lucide-react";
import { STATUS } from "@/lib/netfmt";
import { fmtRate, SERIES } from "@/components/flow/flowlib";

const LEVEL = { alta: { c: STATUS.good, t: "alta" }, "média": { c: STATUS.warning, t: "média" }, baixa: { c: "#64748B", t: "baixa" } };
const PERIODS = [[1, "24 h"], [7, "7 dias"], [30, "30 dias"]];

function Stat({ label, value, sub, color }) {
  return (
    <div className="border border-[#1E293B] rounded p-3 min-w-[170px] flex-1">
      <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{label}</div>
      <div className="text-xl font-mono mt-0.5" style={{ color: color || "#f1f5f9" }}>{value}</div>
      {sub && <div className="text-[11px] text-slate-400 mt-0.5">{sub}</div>}
    </div>
  );
}

/** Aba Peering: ASNs que chegam pelo trânsito e poderiam vir por PTT/PNI/cache (flow + PeeringDB). */
export function FlowPeering({ goConfig, goIfaces }) {
  const [days, setDays] = useState(7);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setBusy(true); setErr("");
    try { setRes((await api.get("/flow/peering", { params: { days } })).data); }
    catch (e) { setErr(formatApiError(e)); setRes(null); }
    finally { setBusy(false); }
  }, [days]);
  useEffect(() => { load(); }, [load]);
  const maxAvg = Math.max(1, ...(res?.rows || []).map(r => r.transit_avg));

  return (
    <div className="space-y-4 max-w-6xl" data-testid="flow-peering">
      <div className="flex flex-wrap items-center gap-2">
        <Handshake className="w-4 h-4 text-slate-400" />
        <span className="text-sm text-slate-100">Quem chega pelo trânsito e poderia vir por PTT, PNI ou cache</span>
        <div className="ml-auto flex gap-1">
          {PERIODS.map(([d, l]) => (
            <button key={d} onClick={() => setDays(d)} data-testid={`peer-days-${d}`}
                    className={`px-2.5 h-8 rounded border text-xs ${days === d ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] text-slate-400 hover:text-slate-200"}`}>{l}</button>
          ))}
        </div>
      </div>
      {busy && <Loader2 className="w-5 h-5 animate-spin text-slate-500" />}
      {err && (
        <div className="text-sm text-amber-300" data-testid="peer-error">{err}
          {err.includes("trânsito") && <button onClick={goIfaces} className="ml-2 text-[#4DA3FF] hover:underline">abrir Interfaces</button>}</div>
      )}
      {res && (
        <>
          {!res.own_asn && (
            <div className="text-xs text-amber-300 flex items-center gap-1.5" data-testid="peer-no-asn"><AlertTriangle className="w-3.5 h-3.5" />
              Informe o seu AS em <button onClick={goConfig} className="text-[#4DA3FF] hover:underline">Configuração</button> para achar os IXs em comum.</div>
          )}
          {res.pdb_error && <div className="text-xs text-amber-300">{res.pdb_error}</div>}
          <div className="flex flex-wrap gap-3" data-testid="peer-summary">
            <Stat label="Trânsito (média)" value={fmtRate(res.transit_avg)} sub={`${res.transit_ifaces} interface(s) de trânsito · entrada`} />
            <Stat label="Poderia sair do trânsito" value={fmtRate(res.movable_avg)} color={STATUS.good}
                  sub={`${Math.round(res.movable_share * 100)}% — AS com IX em comum ou cache com volume`} />
            <Stat label={`AS${res.own_asn || "?"} no PeeringDB`} value={res.own_ixs.length ? `${res.own_ixs.length} IX` : "—"}
                  sub={res.own_ixs.slice(0, 3).join(", ") || (res.own_asn ? "não encontrado" : "informe o seu AS")} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="peer-table">
              <thead className="text-[10px] uppercase tracking-widest text-slate-500 font-mono"><tr>
                <th className="text-left py-1">Prioridade</th><th className="text-left">AS</th><th className="text-left">Pelo trânsito (média · p95)</th>
                <th className="text-right">% do trânsito</th><th className="text-right">Já pelo PTT/PNI</th><th className="text-left pl-3">Sugestão</th></tr></thead>
              <tbody>
                {res.rows.map(r => (
                  <tr key={r.asn} className="border-t border-[#1E293B] align-top" data-testid="peer-row">
                    <td className="py-1.5 pr-2 whitespace-nowrap"><span className="font-mono text-[11px] px-1.5 py-0.5 rounded" style={{ color: LEVEL[r.level].c, background: `${LEVEL[r.level].c}22` }}>{LEVEL[r.level].t}</span></td>
                    <td className="pr-2 min-w-[160px]"><div className="text-slate-100 font-mono">AS{r.asn}</div>
                      <div className="text-slate-400 truncate max-w-[220px]" title={r.name}>{r.name || "—"}</div>
                      {(r.type || r.policy) && <div className="text-[10px] text-slate-500">{[r.type, r.policy && `política ${r.policy}`].filter(Boolean).join(" · ")}</div>}</td>
                    <td className="pr-2 min-w-[170px]">
                      <div className="font-mono text-slate-100">{fmtRate(r.transit_avg)} <span className="text-slate-500">· {fmtRate(r.transit_p95)}</span></div>
                      <div className="h-1.5 rounded bg-[#1E293B] mt-1"><div className="h-1.5 rounded" style={{ width: `${(r.transit_avg / maxAvg) * 100}%`, background: SERIES[0] }} /></div>
                    </td>
                    <td className="text-right font-mono text-slate-300 pr-2">{(r.share * 100).toFixed(1)}%</td>
                    <td className="text-right font-mono text-slate-300 pr-2">{r.ix_avg ? fmtRate(r.ix_avg) : "—"}</td>
                    <td className="pl-3 text-slate-300 leading-snug max-w-[420px]">{r.advice}
                      {r.ixs?.length > 0 && !r.common_ixs?.length && <div className="text-[10px] text-slate-500 mt-0.5">em {r.ixs.length} IX(s) no PeeringDB</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-[11px] text-slate-500 flex items-start gap-1.5"><Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>Tráfego de entrada por AS de origem nas interfaces com papel <b>Trânsito</b> (e, na coluna PTT/PNI, nas de papel IX, PNI e CDN), pelo detalhe
              de 5 min. A presença em IX vem do PeeringDB (atualiza 1× por dia). Os 30 maiores ASNs entram na análise.</span>
          </div>
        </>
      )}
    </div>
  );
}
