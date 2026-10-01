import React, { useEffect, useMemo, useRef, useState } from "react";
import { fmtAxis, fmtRate } from "@/components/flow/flowlib";

/**
 * Gráfico de área empilhada (ou linhas) no estilo Kentik/Akvorado, em SVG puro.
 * ts: [ms] · series: [{id, name, color, values:[…]}] (ordem = de baixo para cima) · unit: "bps" | "pps"
 * Legenda clicável (esconde/mostra; duplo clique = só esta), cruz + dica com todas as séries no ponto,
 * rótulo direto na ponta das faixas quando há até 4 séries e a faixa comporta o texto.
 */
export function StackedChart({ ts, series, unit = "bps", stacked = true, height = 320, bands = [], loading = false, emptyText = "Sem dados de flow no período." }) {
  const boxRef = useRef(null);
  const svgRef = useRef(null);
  const [W, setW] = useState(900);
  const [hover, setHover] = useState(null);
  const [hidden, setHidden] = useState(() => new Set());
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // séries que sumiram não ficam escondidas para sempre
  useEffect(() => {
    setHidden(h => { const ids = new Set(series.map(s => s.id)); const n = new Set([...h].filter(x => ids.has(x))); return n.size === h.size ? h : n; });
  }, [series]);

  const vis = useMemo(() => series.filter(s => !hidden.has(s.id)), [series, hidden]);
  const H = height, PL = 50, PR = 10, PT = 8, PB = 22;
  const n = ts?.length || 0;
  const cum = useMemo(() => {
    if (!stacked) return null;
    const acc = new Array(n).fill(0);
    return vis.map(s => { const lo = acc.slice(); s.values.forEach((v, i) => { acc[i] += v || 0; }); return { lo, hi: acc.slice() }; });
  }, [vis, stacked, n]);
  const peak = useMemo(() => {
    let m = 0;
    if (stacked && cum?.length) cum[cum.length - 1].hi.forEach(v => { if (v > m) m = v; });
    else vis.forEach(s => s.values.forEach(v => { if (v > m) m = v; }));
    return m;
  }, [cum, vis, stacked]);

  const empty = !n || !series.length || series.every(s => s.values.every(v => !v));
  if (empty) {
    return <div ref={boxRef} className="flex items-center justify-center text-xs font-mono text-slate-500 border border-dashed border-line rounded" style={{ height: H }} data-testid="flow-chart-empty">{loading ? "Carregando…" : emptyText}</div>;
  }

  const niceStep = (r) => { const s = r / 4; const p = Math.pow(10, Math.floor(Math.log10(s || 1))); const q = s / p; return (q <= 1 ? 1 : q <= 2 ? 2 : q <= 2.5 ? 2.5 : q <= 5 ? 5 : 10) * p; };
  const step = niceStep(peak || 1);
  const hi = Math.max(step, Math.ceil((peak || 1) / step) * step);
  const ticks = []; for (let v = 0; v <= hi + step / 2; v += step) ticks.push(v);
  const t0 = ts[0], t1 = ts[n - 1];
  const x = (i) => PL + (n <= 1 ? 0 : (i / (n - 1)) * (W - PL - PR));
  const xt = (t) => PL + ((t - t0) / Math.max(1, t1 - t0)) * (W - PL - PR);
  const y = (v) => PT + (1 - v / hi) * (H - PT - PB);
  const span = t1 - t0;
  const tfmt = (t) => span > 2 * 86400e3
    ? new Date(t).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) + " " + new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })
    : new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const xticks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(f * (n - 1)));

  const bandPath = (k) => {
    const { lo, hi: up } = cum[k];
    let d = `M${x(0).toFixed(1)},${y(up[0]).toFixed(1)}`;
    for (let i = 1; i < n; i++) d += `L${x(i).toFixed(1)},${y(up[i]).toFixed(1)}`;
    for (let i = n - 1; i >= 0; i--) d += `L${x(i).toFixed(1)},${y(lo[i]).toFixed(1)}`;
    return d + "Z";
  };
  const linePath = (vals) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v || 0).toFixed(1)}`).join("");

  const onMove = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < PL - 4 || px > W - PR + 4) return setHover(null);
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - PL) / (W - PL - PR)) * (n - 1))));
    setHover(i);
  };
  const toggle = (id) => setHidden(h => { const s = new Set(h); if (s.has(id)) s.delete(id); else s.add(id); return s; });
  const solo = (id) => setHidden(h => (h.size === series.length - 1 && !h.has(id) ? new Set() : new Set(series.filter(s => s.id !== id).map(s => s.id))));

  // rótulos diretos: até 4 séries visíveis, só onde a faixa tem altura para o texto
  // (no ponto "típico" do trecho final — o de total mediano —, para um pico na ponta não arrastar os rótulos)
  const labels = [];
  if (stacked && vis.length <= 4 && W > 520 && n > 4) {
    const top = cum[cum.length - 1].hi;
    const tail = []; for (let i = Math.floor(n * 0.8); i < n; i++) tail.push(i);
    tail.sort((a, b) => top[a] - top[b]);
    const li = tail[Math.floor(tail.length / 2)];
    vis.forEach((s, k) => {
      const { lo, hi: up } = cum[k];
      const th = y(lo[li]) - y(up[li]);
      if (th >= 16) labels.push({ id: s.id, name: s.name, xx: x(li), yy: (y(lo[li]) + y(up[li])) / 2 + 4 });
    });
  }
  const tipLeft = hover !== null && x(hover) / W > 0.6;
  const rows = hover === null ? [] : vis.map(s => ({ s, v: s.values[hover] || 0 })).sort((a, b) => b.v - a.v);
  const total = rows.reduce((a, r) => a + r.v, 0);

  return (
    <div className={`relative ${loading ? "opacity-60" : ""} transition-opacity`} data-testid="flow-chart">
      {series.length > 1 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 mb-1.5 text-[11px] font-mono" data-testid="flow-legend">
          {series.map(s => {
            const off = hidden.has(s.id);
            return (
              <button key={s.id} onClick={() => toggle(s.id)} onDoubleClick={() => solo(s.id)} aria-pressed={!off}
                      title="Clique: esconder/mostrar · duplo clique: só esta" className={`flex items-center gap-1.5 max-w-[260px] ${off ? "text-slate-600 line-through" : "text-slate-300 hover:text-slate-100"}`}>
                {stacked ? <span className="w-3 h-2.5 rounded-[2px] shrink-0" style={{ background: off ? "#354145" : s.color }} />
                         : <span className="w-3.5 h-0.5 rounded shrink-0" style={{ background: off ? "#354145" : s.color }} />}
                <span className="truncate">{s.name}</span>
              </button>
            );
          })}
        </div>
      )}
      <div ref={boxRef} className="relative">
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full select-none" style={{ height: H }}
             onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label="Gráfico de tráfego por série">
          {bands.map((b, i) => b.to >= t0 && b.from <= t1 && (
            <g key={`b${i}`}>
              <rect x={xt(Math.max(t0, b.from))} y={PT} width={Math.max(2, xt(Math.min(t1, b.to)) - xt(Math.max(t0, b.from)))} height={H - PT - PB} fill={b.color || "#d03b3b"} fillOpacity="0.10" />
              {b.label && <text x={xt(Math.max(t0, b.from)) + 4} y={PT + 11} fontSize="10" fill="#ADB9BB" fontFamily="JetBrains Mono, monospace">{b.label}</text>}
            </g>
          ))}
          {ticks.map((v, i) => (
            <g key={i}>
              <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="#262F32" strokeWidth="1" />
              <text x={PL - 6} y={y(v) + 3.5} textAnchor="end" fontSize="11" fill="#6E7B7E" fontFamily="JetBrains Mono, monospace">{fmtAxis(v)}</text>
            </g>
          ))}
          {xticks.map((i, k) => (
            <text key={k} x={x(i)} y={H - 6} fontSize="10.5" fill="#6E7B7E" fontFamily="JetBrains Mono, monospace"
                  textAnchor={k === 0 ? "start" : k === 4 ? "end" : "middle"}>{tfmt(ts[i])}</text>
          ))}
          {stacked ? vis.map((s, k) => (
            <g key={s.id}>
              <path d={bandPath(k)} fill={s.color} fillOpacity={s.other ? 0.16 : 0.24} />
              <path d={linePath(cum[k].hi)} fill="none" stroke="#181E20" strokeWidth="3" strokeLinejoin="round" />
              <path d={linePath(cum[k].hi)} fill="none" stroke={s.color} strokeWidth="1.5" strokeLinejoin="round" />
            </g>
          )) : vis.map(s => (
            <path key={s.id} d={linePath(s.values)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {labels.map(l => (
            <text key={l.id} x={l.xx - 6} y={l.yy} textAnchor="end" fontSize="11" fill="#CED6D7" fontFamily="JetBrains Mono, monospace"
                  stroke="#181E20" strokeWidth="3" paintOrder="stroke">{l.name.length > 38 ? l.name.slice(0, 37) + "…" : l.name}</text>
          ))}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PT} y2={H - PB} stroke="#8D9A9D" strokeWidth="1" />}
        </svg>
        {hover !== null && (
          <div className="absolute z-10 pointer-events-none bg-surface border border-line2 rounded px-2.5 py-1.5 text-[11px] font-mono text-slate-200 shadow-lg max-w-[340px]"
               style={{ top: 4, ...(tipLeft ? { right: `calc(${100 - (x(hover) / W) * 100}% + 12px)` } : { left: `calc(${(x(hover) / W) * 100}% + 12px)` }) }}
               data-testid="flow-tip">
            <div className="text-slate-400 mb-0.5">{new Date(ts[hover]).toLocaleString("pt-BR")}</div>
            {stacked && vis.length > 1 && <div className="flex justify-between gap-3 border-b border-line pb-0.5 mb-0.5"><b className="text-slate-50">{fmtRate(total, unit)}</b><span className="text-slate-400">total</span></div>}
            {rows.slice(0, 12).map(({ s, v }) => (
              <div key={s.id} className="flex items-center gap-1.5 min-w-0">
                <span className="w-3 h-0.5 rounded shrink-0" style={{ background: s.color }} />
                <b className="text-slate-50 tabular-nums whitespace-nowrap">{fmtRate(v, unit)}</b>
                <span className="text-slate-400 truncate">{s.name}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
