import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LineChart } from "@/components/charts/LineChart";
import { fmtBps, fmtBpsShort, fmtSpeed, isDown, utilColor } from "@/lib/netfmt";
import { ArrowDownLeft, ArrowUpRight, AlertTriangle, XCircle } from "lucide-react";

const IN = { key: "in", label: "Entrada (in)", color: "#3987e5" };
const OUT = { key: "out", label: "Saída (out)", color: "#d95926" };

function Hero({ icon: Icon, label, color, value, pct, p95, max }) {
  return (
    <div className="flex-1 min-w-[180px]">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-widest font-mono text-slate-400">
        <span className="w-2.5 h-2.5 rounded-sm" style={{ background: color }} /><Icon className="w-3.5 h-3.5" /> {label}
      </div>
      <div className="flex items-baseline gap-2 mt-0.5">
        <div className="font-heading text-4xl font-bold text-slate-50 tabular-nums" data-testid="hero-value">{fmtBps(value)}</div>
        {pct != null && <div className="text-sm font-mono text-slate-300">{pct.toFixed(1)}%</div>}
      </div>
      {pct != null && (
        <div className="h-1.5 rounded bg-[#1E293B] mt-1.5 overflow-hidden" title={`${pct.toFixed(1)}% da capacidade`}>
          <div className="h-full rounded" style={{ width: `${Math.min(100, pct)}%`, background: utilColor(pct) }} />
        </div>
      )}
      <div className="text-[11px] font-mono text-slate-400 mt-1.5">p95 <b className="text-slate-200">{fmtBps(p95)}</b> · pico <b className="text-slate-200">{fmtBps(max)}</b></div>
    </div>
  );
}

/** Consumo de uma interface: valores atuais grandes, % do link, p95 e pico do período + gráfico. */
export function TrafficWidget({ widget, minutes, refreshKey, height = 260 }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () => api.get("/monitor/series", { params: { device_id: widget.device_id, if_index: widget.if_index, minutes, points: 500 } })
      .then(r => { if (alive) { setData(r.data); setErr(""); } })
      .catch(e => alive && setErr(e?.response?.data?.detail || e.message));
    load(); const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, [widget.device_id, widget.if_index, minutes, refreshKey]);

  if (err) return <div className="text-xs text-red-400 font-mono p-4">{err}</div>;
  if (!data) return <div className="text-xs text-slate-500 font-mono p-4">Carregando…</div>;
  const cap = widget.capacity_mbps || data.speed_mbps;
  const pct = (v) => (v != null && cap ? (v / (cap * 1e6)) * 100 : null);
  const st = data.stats;
  return (
    <div data-testid={`traffic-widget-${widget.id}`}>
      {(data.error || isDown(data.oper)) && (
        <div className={`text-xs font-mono mb-2 flex items-center gap-1.5 ${isDown(data.oper) ? "text-red-400" : "text-amber-300"}`}>
          {isDown(data.oper) ? <><XCircle className="w-3.5 h-3.5" /> interface DOWN</> : <><AlertTriangle className="w-3.5 h-3.5" /> {data.error}</>}
        </div>
      )}
      <div className="flex flex-wrap gap-6 mb-3">
        <Hero icon={ArrowDownLeft} label="Entrada" color={IN.color} value={st.in.cur} pct={pct(st.in.cur)} p95={st.in.p95} max={st.in.max} />
        <Hero icon={ArrowUpRight} label="Saída" color={OUT.color} value={st.out.cur} pct={pct(st.out.cur)} p95={st.out.p95} max={st.out.max} />
      </div>
      <LineChart points={data.points} series={[IN, OUT]} height={height} area yFormat={fmtBpsShort} tipFormat={fmtBps}
                 refLines={cap && Math.max(st.in.max || 0, st.out.max || 0) > cap * 1e6 * 0.5 ? [{ y: cap * 1e6, label: `capacidade ${fmtSpeed(cap)}`, color: "#64748B" }] : []} />
      <div className="text-[10px] font-mono text-slate-500 mt-1">
        {cap ? `capacidade ${fmtSpeed(cap)}${widget.capacity_mbps ? " (contratada)" : " (porta)"}` : "capacidade desconhecida"}
        {data.step_sec > 30 ? ` · pontos agregados a cada ${data.step_sec >= 3600 ? `${Math.round(data.step_sec / 3600)}h` : data.step_sec >= 60 ? `${Math.round(data.step_sec / 60)} min` : `${data.step_sec}s`} (média)` : ""}
      </div>
    </div>
  );
}
