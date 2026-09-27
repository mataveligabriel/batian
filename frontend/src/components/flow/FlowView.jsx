import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { StackedChart } from "@/components/flow/StackedChart";
import { colorize, fmtRate, fmtVolume, ROLE_LABEL, useSeriesColors } from "@/components/flow/flowlib";
import { AlertTriangle, Loader2 } from "lucide-react";

/** Consulta de flow (POST /flow/query) + gráfico + tabela. Recarrega sozinha a cada minuto nos períodos curtos. */
export function FlowView({ query, stacked = true, height = 320, bands = [], shareLabel = "Participação", onData, testid = "flow-view", emptyText, fixed }) {
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const assign = useSeriesColors();
  const key = JSON.stringify(query);
  useEffect(() => {
    if (!query) return undefined;
    let alive = true;
    const load = () => {
      setLoading(true);
      api.post("/flow/query", query)
        .then(r => { if (alive) { setRes(r.data); setErr(""); onData?.(r.data); } })
        .catch(e => { if (alive) { setErr(formatApiError(e)); } })
        .finally(() => alive && setLoading(false));
    };
    load();
    const t = query.minutes <= 1440 ? setInterval(load, 60000) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const { series, table } = useMemo(() => colorize(res, assign, fixed), [res, fixed]); // eslint-disable-line react-hooks/exhaustive-deps
  const unit = res?.unit || query?.unit || "bps";
  const step = res?.step;

  return (
    <div data-testid={testid}>
      {err && <div className="mb-2 text-xs text-amber-300 font-mono flex items-start gap-1.5" data-testid="flow-err"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}
      <StackedChart ts={res?.ts || []} series={series} unit={unit} stacked={stacked} height={height} bands={bands} loading={loading && !res} emptyText={emptyText} />
      <div className="flex items-center gap-2 mt-1 text-[10px] font-mono text-slate-500">
        {loading && <Loader2 className="w-3 h-3 animate-spin" />}
        {res && <span>resolução {step >= 3600 ? `${step / 3600} h` : `${step / 60} min`} · fonte {res.source === "flow_1m" ? "totais por minuto (exato)" : res.source === "flow_5m" ? "detalhes de 5 min (top-K)" : "junção por hora"}</span>}
      </div>
      {table.length > 0 && (
        <div className="overflow-x-auto mt-2">
          <table className="w-full text-xs font-mono" data-testid="flow-table">
            <thead className="text-[10px] uppercase tracking-widest text-slate-500">
              <tr>
                <th className="text-left py-1 pr-2">Série</th><th className="text-right px-2">Média</th><th className="text-right px-2">p95</th>
                <th className="text-right px-2">Máximo</th><th className="text-right px-2">Atual</th><th className="text-right px-2">Volume</th>
                <th className="text-left pl-3 w-[22%] min-w-[120px]">{shareLabel}</th>
              </tr>
            </thead>
            <tbody>
              {table.map(r => (
                <tr key={r.id} className="border-t border-[#1E293B]" data-testid="flow-row">
                  <td className="py-1 pr-2 max-w-[360px]">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="w-2.5 h-2.5 rounded-[2px] shrink-0" style={{ background: r.color }} />
                      <span className="text-slate-100 truncate" title={r.name}>{r.name}</span>
                      {r.role && <span className="text-[9px] px-1 rounded border border-[#2A3345] text-slate-400 shrink-0">{ROLE_LABEL[r.role] || r.role}</span>}
                    </div>
                  </td>
                  <td className="text-right px-2 text-slate-200 whitespace-nowrap">{fmtRate(r.avg, unit)}</td>
                  <td className="text-right px-2 text-slate-50 whitespace-nowrap font-semibold">{fmtRate(r.p95, unit)}</td>
                  <td className="text-right px-2 text-slate-300 whitespace-nowrap">{fmtRate(r.max, unit)}</td>
                  <td className="text-right px-2 text-slate-400 whitespace-nowrap">{fmtRate(r.last, unit)}</td>
                  <td className="text-right px-2 text-slate-400 whitespace-nowrap">{fmtVolume(r.total, unit === "pps" ? "pps" : "bytes")}</td>
                  <td className="pl-3">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 rounded bg-[#1E293B] overflow-hidden"><div className="h-full rounded" style={{ width: `${Math.min(100, r.share * 100)}%`, background: r.color }} /></div>
                      <span className="text-[10px] text-slate-300 w-11 text-right">{(r.share * 100).toFixed(r.share < 0.1 ? 1 : 0)}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
