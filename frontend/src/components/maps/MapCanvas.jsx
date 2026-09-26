import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtBps, utilColor, STATUS, NO_DATA, fmtSpeed } from "@/lib/netfmt";

const NODE = { device: { w: 200, h: 48 }, cloud: { w: 120, h: 44 }, label: { w: 120, h: 28 } };
const SURFACE = "#0B111C";

// distância do centro até a borda da caixa do nó, na direção (ux, uy)
function edgeDist(node, ux, uy) {
  const s = NODE[node.kind] || NODE.device;
  const tx = Math.abs(ux) > 1e-6 ? (s.w / 2) / Math.abs(ux) : Infinity;
  const ty = Math.abs(uy) > 1e-6 ? (s.h / 2) / Math.abs(uy) : Infinity;
  return Math.min(tx, ty) + 3;
}

function linkGeometry(a, b, offset) {
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len, nx = -uy * offset, ny = ux * offset;
  const da = edgeDist(a, ux, uy), db = edgeDist(b, ux, uy);
  // a linha começa/termina na borda das caixas (os valores não ficam por baixo do equipamento)
  const pa = { x: a.x + nx + ux * da, y: a.y + ny + uy * da };
  const pb = { x: b.x + nx - ux * db, y: b.y + ny - uy * db };
  const vis = len - da - db;
  return { pa, pb, m: { x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2 }, ux, uy, len: vis };
}

function Half({ from, to, ux, uy, color, dashed, width = 7 }) {
  // meia-linha de `from` até perto do meio + ponta de seta no meio (sentido do tráfego)
  const AL = 13, AW = 9;
  const tip = to;
  const base = { x: tip.x - ux * AL, y: tip.y - uy * AL };
  const px = -uy, py = ux;
  return (
    <g>
      <line x1={from.x} y1={from.y} x2={base.x} y2={base.y} stroke={color} strokeWidth={width} strokeLinecap="butt"
            strokeDasharray={dashed ? "10 7" : undefined} />
      <polygon points={`${tip.x},${tip.y} ${base.x + px * AW},${base.y + py * AW} ${base.x - px * AW},${base.y - py * AW}`}
               fill={color} stroke={SURFACE} strokeWidth="1.5" />
    </g>
  );
}

function ValueTag({ x, y, text, sub, accent }) {
  const w = Math.max(64, text.length * 6.6 + 14);
  return (
    <g pointerEvents="none">
      <rect x={x - w / 2} y={y - 12} width={w} height={sub ? 27 : 18} rx="4" fill={SURFACE} stroke={accent} strokeWidth="1.5" />
      <text x={x} y={y + 1} textAnchor="middle" fontSize="11" fontFamily="JetBrains Mono, monospace" fill="#F1F5F9">{text}</text>
      {sub && <text x={x} y={y + 12} textAnchor="middle" fontSize="9" fontFamily="JetBrains Mono, monospace" fill="#94A3B8">{sub}</text>}
    </g>
  );
}

export function MapCanvas({
  map, live, devices, editing, tool, selected, onSelect, onMoveNode, onMoveEnd, onConnect, fitSignal,
}) {
  const wrapRef = useRef(null);
  const svgRef = useRef(null);
  const [view, setView] = useState({ tx: 40, ty: 40, k: 1 });
  const [drag, setDrag] = useState(null);       // {kind:"pan"|"node", ...}
  const [pending, setPending] = useState(null); // 1º nó escolhido na ferramenta "ligar"
  const [hover, setHover] = useState(null);     // {linkId, x, y}
  const nodesById = useMemo(() => Object.fromEntries((map.nodes || []).map(n => [n.id, n])), [map.nodes]);
  const devById = useMemo(() => Object.fromEntries((devices || []).map(d => [d.id, d])), [devices]);

  useEffect(() => { if (tool !== "connect") setPending(null); }, [tool]);

  // ajustar à tela
  const fit = useCallback(() => {
    const el = wrapRef.current;
    const ns = map.nodes || [];
    if (!el || !ns.length) return setView({ tx: 40, ty: 40, k: 1 });
    const xs = ns.map(n => n.x), ys = ns.map(n => n.y);
    const minX = Math.min(...xs) - 110, maxX = Math.max(...xs) + 110, minY = Math.min(...ys) - 70, maxY = Math.max(...ys) + 70;
    const W = el.clientWidth, H = el.clientHeight;
    const k = Math.max(0.25, Math.min(1.6, Math.min(W / (maxX - minX), H / (maxY - minY))));
    setView({ k, tx: (W - (maxX - minX) * k) / 2 - minX * k, ty: (H - (maxY - minY) * k) / 2 - minY * k });
  }, [map.nodes]);
  useEffect(() => { fit(); }, [fitSignal, map.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const toWorld = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    return { x: (e.clientX - r.left - view.tx) / view.k, y: (e.clientY - r.top - view.ty) / view.k };
  };

  const onWheel = (e) => {
    e.preventDefault();
    const r = svgRef.current.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    setView(v => {
      const k = Math.max(0.2, Math.min(3, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      return { k, tx: mx - ((mx - v.tx) / v.k) * k, ty: my - ((my - v.ty) / v.k) * k };
    });
  };
  useEffect(() => {
    const el = svgRef.current;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const onBgDown = (e) => {
    if (e.button !== 0) return;
    setDrag({ kind: "pan", sx: e.clientX, sy: e.clientY, tx: view.tx, ty: view.ty, moved: false });
  };
  const onNodeDown = (e, n) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    if (editing && tool === "connect") {
      if (!pending) setPending(n.id);
      else if (pending !== n.id) { onConnect(pending, n.id); setPending(null); }
      return;
    }
    const w = toWorld(e);
    setDrag({ kind: "node", id: n.id, ox: w.x - n.x, oy: w.y - n.y, moved: false });
  };
  const onMove = (e) => {
    if (!drag) return;
    if (drag.kind === "pan") {
      setView(v => ({ ...v, tx: drag.tx + e.clientX - drag.sx, ty: drag.ty + e.clientY - drag.sy }));
      if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) drag.moved = true;
    } else if (drag.kind === "node" && editing) {
      const w = toWorld(e);
      drag.moved = true;
      onMoveNode(drag.id, Math.round((w.x - drag.ox) / 10) * 10, Math.round((w.y - drag.oy) / 10) * 10); // grade de 10px
    }
  };
  const onUp = () => {
    if (drag?.kind === "node") {
      if (drag.moved) onMoveEnd?.(); else onSelect({ type: "node", id: drag.id });
    } else if (drag?.kind === "pan" && !drag.moved) onSelect(null);
    setDrag(null);
  };

  // links paralelos entre o mesmo par de nós ganham deslocamento lateral
  const offsets = useMemo(() => {
    const groups = {};
    (map.links || []).forEach(l => { const k = [l.from, l.to].sort().join("|"); (groups[k] = groups[k] || []).push(l.id); });
    const out = {};
    Object.values(groups).forEach(ids => ids.forEach((id, i) => { out[id] = { off: (i - (ids.length - 1) / 2) * 16, i, n: ids.length }; }));
    return out;
  }, [map.links]);

  const hoverLink = hover && (map.links || []).find(l => l.id === hover.linkId);
  const hv = hoverLink && live?.links?.[hoverLink.id];
  const nodeName = (id) => { const n = nodesById[id]; return n?.device_id ? (devById[n.device_id]?.name || n.label || "?") : (n?.label || "?"); };

  return (
    <div ref={wrapRef} className="relative w-full h-full overflow-hidden select-none" style={{ background: SURFACE }}
         onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={() => { setDrag(null); setHover(null); }} data-testid="map-canvas">
      <svg ref={svgRef} className={`w-full h-full ${drag?.kind === "pan" ? "cursor-grabbing" : "cursor-grab"}`} onMouseDown={onBgDown}>
        <defs>
          <pattern id="grid" width={40 * view.k} height={40 * view.k} patternUnits="userSpaceOnUse" x={view.tx} y={view.ty}>
            <circle cx="1" cy="1" r="1" fill="#1E293B" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
        <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
          {(map.links || []).map(l => {
            const a = nodesById[l.from], b = nodesById[l.to];
            if (!a || !b) return null;
            const par = offsets[l.id] || { off: 0, i: 0, n: 1 };
            const g = linkGeometry(a, b, par.off);
            if (g.len < 30) return null; // nós sobrepostos
            const lv = live?.links?.[l.id];
            const down = lv?.down;
            const noData = !lv || lv.collecting || (lv.ab_bps == null && lv.ba_bps == null);
            const cAB = down ? STATUS.critical : noData ? NO_DATA : utilColor(lv.ab_pct);
            const cBA = down ? STATUS.critical : noData ? NO_DATA : utilColor(lv.ba_pct);
            const isSel = selected?.type === "link" && selected.id === l.id;
            // links paralelos: rótulos empurrados para fora, cada um para o seu lado
            const push = par.n > 1 ? Math.sign(par.off || (par.i ? 1 : -1)) * 16 : 0;
            const nx = -g.uy * push, ny = g.ux * push;
            const q1 = { x: g.pa.x + (g.m.x - g.pa.x) * 0.45 + nx, y: g.pa.y + (g.m.y - g.pa.y) * 0.45 + ny };
            const q3 = { x: g.pb.x + (g.m.x - g.pb.x) * 0.45 + nx, y: g.pb.y + (g.m.y - g.pb.y) * 0.45 + ny };
            return (
              <g key={l.id} data-testid={`link-${l.id}`}>
                {isSel && <line x1={g.pa.x} y1={g.pa.y} x2={g.pb.x} y2={g.pb.y} stroke="#F8FAFC" strokeOpacity="0.35" strokeWidth="15" />}
                <Half from={g.pa} to={g.m} ux={g.ux} uy={g.uy} color={cAB} dashed={down || noData} />
                <Half from={g.pb} to={g.m} ux={-g.ux} uy={-g.uy} color={cBA} dashed={down || noData} />
                {g.len > 110 && !down && !noData && <>
                  <ValueTag x={q1.x} y={q1.y} text={fmtBps(lv.ab_bps)} sub={lv.ab_pct != null ? `${lv.ab_pct}%` : null} accent={cAB} />
                  <ValueTag x={q3.x} y={q3.y} text={fmtBps(lv.ba_bps)} sub={lv.ba_pct != null ? `${lv.ba_pct}%` : null} accent={cBA} />
                </>}
                {down && <ValueTag x={g.m.x} y={g.m.y - 16} text="✕ DOWN" accent={STATUS.critical} />}
                {noData && !down && g.len > 90 && <ValueTag x={g.m.x} y={g.m.y - 16} text={lv?.error ? "⚠ SNMP" : l.from_if || l.to_if ? "coletando…" : "sem interface"} accent={NO_DATA} />}
                {/* área de clique/hover maior que a linha */}
                <line x1={g.pa.x} y1={g.pa.y} x2={g.pb.x} y2={g.pb.y} stroke="transparent" strokeWidth="22" style={{ cursor: "pointer" }}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); onSelect({ type: "link", id: l.id }); }}
                      onMouseMove={(e) => { const r = wrapRef.current.getBoundingClientRect(); setHover({ linkId: l.id, x: e.clientX - r.left, y: e.clientY - r.top }); }}
                      onMouseLeave={() => setHover(null)} />
              </g>
            );
          })}

          {(map.nodes || []).map(n => {
            const sz = NODE[n.kind] || NODE.device;
            const dev = n.device_id ? devById[n.device_id] : null;
            const st = live?.nodes?.[n.id];
            const status = st?.status || dev?.status || "unknown";
            const stColor = status === "online" ? STATUS.good : status === "offline" ? STATUS.critical : "#64748B";
            const isSel = selected?.type === "node" && selected.id === n.id;
            const isPending = pending === n.id;
            const title = n.kind === "device" ? (dev?.name || n.label || "equipamento removido") : (n.label || (n.kind === "cloud" ? "Internet" : "texto"));
            const cursor = editing ? (tool === "connect" ? "crosshair" : "move") : "pointer";
            if (n.kind === "label") {
              return (
                <g key={n.id} transform={`translate(${n.x},${n.y})`} onMouseDown={(e) => onNodeDown(e, n)} style={{ cursor }} data-testid={`node-${n.id}`}>
                  <rect x={-sz.w / 2} y={-sz.h / 2} width={sz.w} height={sz.h} fill="transparent" stroke={isSel ? "#4DA3FF" : "transparent"} strokeDasharray="4 3" rx="4" />
                  <text textAnchor="middle" y="5" fontSize="14" fontWeight="600" fill="#CBD5E1" fontFamily="Outfit, sans-serif">{title}</text>
                </g>
              );
            }
            return (
              <g key={n.id} transform={`translate(${n.x},${n.y})`} onMouseDown={(e) => onNodeDown(e, n)} style={{ cursor }} data-testid={`node-${n.id}`}>
                <rect x={-sz.w / 2} y={-sz.h / 2} width={sz.w} height={sz.h} rx={n.kind === "cloud" ? sz.h / 2 : 8}
                      fill="#111722" stroke={isPending ? "#FAB219" : isSel ? "#4DA3FF" : "#334155"} strokeWidth={isSel || isPending ? 2.5 : 1.5}
                      strokeDasharray={n.kind === "cloud" ? "5 4" : undefined} />
                {n.kind === "device" ? <>
                  <circle cx={-sz.w / 2 + 13} cy={-6} r="4.5" fill={stColor} />
                  <text x={-sz.w / 2 + 24} y={-2} fontSize="12" fontWeight="600" fill="#F1F5F9" fontFamily="DM Sans, sans-serif">
                    {title.length > 24 ? title.slice(0, 23) + "…" : title}
                  </text>
                  <text x={-sz.w / 2 + 24} y={13} fontSize="10" fill="#94A3B8" fontFamily="JetBrains Mono, monospace">
                    {status}{st?.snmp_error ? " · ⚠ snmp" : ""}{dev ? ` · ${dev.host}` : ""}
                  </text>
                  <title>{`${title}\n${dev?.host || ""}\nstatus: ${status}${st?.snmp_error ? `\nSNMP: ${st.snmp_error}` : ""}`}</title>
                </> : (
                  <text textAnchor="middle" y="5" fontSize="13" fontWeight="600" fill="#CBD5E1" fontFamily="DM Sans, sans-serif">☁ {title}</text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {editing && tool === "connect" && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-[#161B26] border border-amber-500/50 text-amber-200 text-xs font-mono px-3 py-1.5 rounded shadow-lg">
          {pending ? `Agora clique no 2º equipamento para ligar a ${nodeName(pending)}` : "Clique no 1º equipamento do link"}
        </div>
      )}

      {hoverLink && (
        <div className="absolute z-20 pointer-events-none bg-[#161B26] border border-[#2A3345] rounded-md px-3 py-2 text-[11px] font-mono text-slate-200 shadow-xl w-72"
             style={{ left: Math.min(hover.x + 14, (wrapRef.current?.clientWidth || 800) - 300), top: hover.y + 14 }} data-testid="link-tooltip">
          <div className="text-slate-100 font-sans text-xs font-semibold mb-1">{hoverLink.label || `${nodeName(hoverLink.from)} ↔ ${nodeName(hoverLink.to)}`}</div>
          <div className="text-slate-400">{nodeName(hoverLink.from)} {hoverLink.from_if ? `· ${hoverLink.from_if.name}` : ""}</div>
          <div className="text-slate-400 mb-1">{nodeName(hoverLink.to)} {hoverLink.to_if ? `· ${hoverLink.to_if.name}` : ""}</div>
          {hv?.down && <div className="text-red-400">✕ interface DOWN</div>}
          {hv?.error && <div className="text-amber-300">⚠ {hv.error}</div>}
          <div>→ {nodeName(hoverLink.to)}: <b>{fmtBps(hv?.ab_bps)}</b>{hv?.ab_pct != null ? ` (${hv.ab_pct}%)` : ""}</div>
          <div>← {nodeName(hoverLink.from)}: <b>{fmtBps(hv?.ba_bps)}</b>{hv?.ba_pct != null ? ` (${hv.ba_pct}%)` : ""}</div>
          <div className="text-slate-500 mt-1">capacidade {fmtSpeed(hv?.capacity_mbps)} · clique para ver o gráfico</div>
        </div>
      )}

      <div className="absolute bottom-3 right-3 flex items-center gap-1 bg-[#111722]/90 border border-[#1E293B] rounded px-2 py-1">
        <button className="px-1.5 text-slate-300 hover:text-white" onClick={() => setView(v => ({ ...v, k: Math.min(3, v.k * 1.2) }))} title="Aproximar">+</button>
        <span className="text-[10px] font-mono text-slate-500 w-9 text-center">{Math.round(view.k * 100)}%</span>
        <button className="px-1.5 text-slate-300 hover:text-white" onClick={() => setView(v => ({ ...v, k: Math.max(0.2, v.k / 1.2) }))} title="Afastar">−</button>
        <button className="px-1.5 text-[11px] text-slate-300 hover:text-white border-l border-[#1E293B] ml-1" onClick={fit} title="Ajustar à tela">ajustar</button>
      </div>
    </div>
  );
}
