import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * Gráfico de linhas genérico (SVG): um eixo só, grade discreta, cruzamento + tooltip no hover,
 * legenda sempre que houver 2+ séries, área opcional sob a 1ª série, linhas de referência.
 * points: [{ t: ISO, [series.key]: number|null }]
 */
export function LineChart({
  points, series, height = 220, yFormat = (v) => String(v), tipFormat, area = false,
  yMin, yMax, zeroBased = true, refLines = [], emptyText = "Coletando… o gráfico aparece depois de algumas leituras.",
}) {
  const ref = useRef(null);
  const boxRef = useRef(null);
  const [hover, setHover] = useState(null);
  const [W, setW] = useState(800);   // largura real do contêiner: texto nunca fica esticado
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(260, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [points.length >= 2]); // eslint-disable-line react-hooks/exhaustive-deps
  const H = height, PL = 52, PR = 12, PT = 10, PB = 24;
  const data = useMemo(() => points.map(p => ({ ...p, ts: new Date(p.t).getTime() })), [points]);
  const fmtTip = tipFormat || yFormat;
  if (data.length < 2) return <div className="text-xs font-mono text-slate-500 py-8 text-center" style={{ minHeight: height / 2 }}>{emptyText}</div>;

  const vals = data.flatMap(d => series.map(s => d[s.key])).filter(v => v !== null && v !== undefined && Number.isFinite(v));
  let lo = yMin ?? (zeroBased ? 0 : Math.min(...vals, ...refLines.map(r => r.y)));
  let hi = yMax ?? Math.max(...vals, ...refLines.map(r => r.y));
  if (!zeroBased && yMin === undefined) { const pad = (hi - lo) * 0.12 || 1; lo -= pad; hi += pad; }
  if (hi <= lo) hi = lo + 1;
  const niceStep = (range) => { const r = range / 4; const p = Math.pow(10, Math.floor(Math.log10(r))); const n = r / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p; };
  const step = niceStep(hi - lo);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const ticks = []; for (let v = lo; v <= hi + step / 2; v += step) ticks.push(v);

  const t0 = data[0].ts, t1 = data[data.length - 1].ts;
  const x = (t) => PL + ((t - t0) / Math.max(1, t1 - t0)) * (W - PL - PR);
  const y = (v) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
  const span = t1 - t0;
  const tfmt = (t) => span > 2 * 86400e3
    ? new Date(t).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) + " " + new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
    : new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

  // quebra a linha onde faltam dados (null) em vez de ligar pontos distantes
  const path = (k) => {
    let d = "", pen = false;
    data.forEach(p => {
      const v = p[k];
      if (v === null || v === undefined || !Number.isFinite(v)) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(p.ts).toFixed(1)},${y(v).toFixed(1)}`; pen = true;
    });
    return d;
  };
  const areaPath = (k) => {
    const pts = data.filter(p => Number.isFinite(p[k]));
    if (pts.length < 2) return "";
    return `M${x(pts[0].ts)},${y(Math.max(lo, 0))}` + pts.map(p => `L${x(p.ts).toFixed(1)},${y(p[k]).toFixed(1)}`).join("") + `L${x(pts[pts.length - 1].ts)},${y(Math.max(lo, 0))}Z`;
  };
  const xticks = [0, 0.25, 0.5, 0.75, 1].map(f => t0 + f * (t1 - t0));

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < PL || px > W - PR) return setHover(null);
    const t = t0 + ((px - PL) / (W - PL - PR)) * (t1 - t0);
    let best = data[0];
    for (const d of data) if (Math.abs(d.ts - t) < Math.abs(best.ts - t)) best = d;
    setHover(best);
  };
  const tipLeft = hover && x(hover.ts) / W > 0.6;

  return (
    <div className="relative">
      {series.length > 1 && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mb-1 text-[11px] font-mono text-slate-300">
          {series.map(s => <span key={s.key} className="flex items-center gap-1.5"><span className="w-3.5 h-0.5 rounded" style={{ background: s.color }} />{s.label}</span>)}
        </div>
      )}
      <div className="relative" ref={boxRef}>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" style={{ height: H }}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="#1E293B" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <text x={PL - 6} y={y(v) + 3.5} textAnchor="end" fontSize="11" fill="#64748B" fontFamily="JetBrains Mono, monospace">{yFormat(v)}</text>
          </g>
        ))}
        {xticks.map((t, i) => (
          <text key={i} x={x(t)} y={H - 6} fontSize="10.5" fill="#64748B" fontFamily="JetBrains Mono, monospace"
                textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"}>{tfmt(t)}</text>
        ))}
        {refLines.map((r, i) => (
          <g key={`r${i}`}>
            <line x1={PL} x2={W - PR} y1={y(r.y)} y2={y(r.y)} stroke={r.color} strokeWidth="1.5" strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />
            <text x={W - PR - 4} y={y(r.y) - 4} textAnchor="end" fontSize="10.5" fill="#CBD5E1" fontFamily="JetBrains Mono, monospace">{r.label}</text>
          </g>
        ))}
        {area && series[0] && <path d={areaPath(series[0].key)} fill={series[0].color} fillOpacity="0.14" />}
        {series.map(s => <path key={s.key} d={path(s.key)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />)}
        {hover && <line x1={x(hover.ts)} x2={x(hover.ts)} y1={PT} y2={H - PB} stroke="#94A3B8" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />}
      </svg>
      {hover && (
        <>
          {series.map(s => Number.isFinite(hover[s.key]) && (
            <span key={s.key} className="absolute w-2.5 h-2.5 rounded-full pointer-events-none"
                  style={{ left: `calc(${(x(hover.ts) / W) * 100}% - 5px)`, top: `${y(hover[s.key]) - 5}px`,
                           background: s.color, boxShadow: "0 0 0 2px #0B111C" }} />
          ))}
          <div className="absolute z-10 pointer-events-none bg-[#111722] border border-[#2A3345] rounded px-2.5 py-1.5 text-[11px] font-mono text-slate-200 shadow-lg"
               style={{ top: 4, ...(tipLeft ? { right: `calc(${100 - (x(hover.ts) / W) * 100}% + 12px)` } : { left: `calc(${(x(hover.ts) / W) * 100}% + 12px)` }) }}>
            <div className="text-slate-400">{new Date(hover.ts).toLocaleString("pt-BR")}</div>
            {series.map(s => (
              <div key={s.key} className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full" style={{ background: s.color }} />{s.label}: <b>{Number.isFinite(hover[s.key]) ? fmtTip(hover[s.key]) : "—"}</b>
              </div>
            ))}
          </div>
        </>
      )}
      </div>
    </div>
  );
}
