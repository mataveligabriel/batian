import React, { useMemo, useRef, useState } from "react";
import { fmtBps, fmtBpsShort } from "@/lib/netfmt";

// Duas séries validadas no fundo escuro (slots 1 e 2 da paleta categórica, modo dark).
const SERIES = [
  { key: "ab", label: "A → B", color: "#3987e5" },
  { key: "ba", label: "B → A", color: "#d95926" },
];

function niceMax(v) {
  if (!v || v <= 0) return 1e6;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/** points: [{t: ISO, ab: bps, ba: bps}] */
export function TrafficChart({ points, labels = {}, height = 170 }) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  const W = 520, H = height, PL = 46, PR = 10, PT = 8, PB = 22;
  const data = useMemo(() => points.map(p => ({ ...p, ts: new Date(p.t).getTime() })), [points]);
  if (data.length < 2) {
    return <div className="text-xs font-mono text-slate-500 py-6 text-center" data-testid="chart-empty">
      Coletando… o gráfico aparece depois de algumas leituras.</div>;
  }
  const t0 = data[0].ts, t1 = data[data.length - 1].ts;
  const yMax = niceMax(Math.max(...data.flatMap(d => [d.ab || 0, d.ba || 0])));
  const x = (t) => PL + ((t - t0) / Math.max(1, t1 - t0)) * (W - PL - PR);
  const y = (v) => PT + (1 - (v || 0) / yMax) * (H - PT - PB);
  const path = (k) => data.map((d, i) => `${i ? "L" : "M"}${x(d.ts).toFixed(1)},${y(d[k]).toFixed(1)}`).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => f * yMax);
  const timeFmt = (t) => new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < PL || px > W - PR) return setHover(null);
    const t = t0 + ((px - PL) / (W - PL - PR)) * (t1 - t0);
    let best = data[0];
    for (const d of data) if (Math.abs(d.ts - t) < Math.abs(best.ts - t)) best = d;
    setHover(best);
  };

  return (
    <div className="relative" data-testid="traffic-chart">
      <div className="flex gap-4 mb-1 text-[11px] font-mono text-slate-300">
        {SERIES.map(s => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 rounded" style={{ background: s.color }} />{labels[s.key] || s.label}
          </span>
        ))}
      </div>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="#1E293B" strokeWidth="1" />
            <text x={PL - 6} y={y(v) + 3} textAnchor="end" fontSize="10" fill="#64748B" fontFamily="monospace">{fmtBpsShort(v)}</text>
          </g>
        ))}
        <text x={PL} y={H - 6} fontSize="10" fill="#64748B" fontFamily="monospace">{timeFmt(t0)}</text>
        <text x={W - PR} y={H - 6} fontSize="10" fill="#64748B" fontFamily="monospace" textAnchor="end">{timeFmt(t1)}</text>
        {SERIES.map(s => <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />)}
        {hover && (
          <g>
            <line x1={x(hover.ts)} x2={x(hover.ts)} y1={PT} y2={H - PB} stroke="#94A3B8" strokeWidth="1" strokeDasharray="3 3" />
            {SERIES.map(s => <circle key={s.key} cx={x(hover.ts)} cy={y(hover[s.key])} r="4" fill={s.color} stroke="#0B111C" strokeWidth="2" />)}
          </g>
        )}
      </svg>
      {hover && (
        <div className="absolute top-6 right-2 bg-[#111722] border border-[#2A3345] rounded px-2.5 py-1.5 text-[11px] font-mono text-slate-200 pointer-events-none shadow-lg">
          <div className="text-slate-400">{new Date(hover.ts).toLocaleTimeString("pt-BR")}</div>
          {SERIES.map(s => (
            <div key={s.key} className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ background: s.color }} />{labels[s.key] || s.label}: <b>{fmtBps(hover[s.key])}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
