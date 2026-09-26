import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtBps, utilColor, STATUS, NO_DATA, fmtSpeed } from "@/lib/netfmt";

const NODE = { device: { w: 200, h: 48 }, cloud: { w: 120, h: 44 }, label: { w: 120, h: 28 } };
const SURFACE = "#0B111C";

const SPREAD = 62; // distância entre enlaces paralelos (no meio da curva)

function insideBox(node, p) {
  const s = NODE[node.kind] || NODE.device;
  return Math.abs(p.x - node.x) <= s.w / 2 + 3 && Math.abs(p.y - node.y) <= s.h / 2 + 3;
}

const qpt = (P0, C, P2, t) => {
  const u = 1 - t;
  return { x: u * u * P0.x + 2 * u * t * C.x + t * t * P2.x, y: u * u * P0.y + 2 * u * t * C.y + t * t * P2.y };
};

/**
 * Link como curva quadrática: `bend` = quanto o meio da curva se afasta da reta (px, na normal de a→b).
 * bend 0 = reta. Retorna pontos para desenhar as duas metades, onde ficam os rótulos e a alça de arrasto.
 */
function linkGeometry(a, b, bend) {
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len, nx = -uy, ny = ux;
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const C = { x: mid.x + nx * bend * 2, y: mid.y + ny * bend * 2 };
  const P = (t) => qpt(a, C, b, t);
  // onde a curva sai de cada caixa (busca binária) — a linha começa/termina na borda do equipamento
  const exitT = (node, inside, outside) => {
    if (!insideBox(node, P(inside))) return inside;
    for (let k = 0; k < 18; k++) { const m = (inside + outside) / 2; if (insideBox(node, P(m))) inside = m; else outside = m; }
    return outside;
  };
  const ta = exitT(a, 0, 0.5), tb = exitT(b, 1, 0.5);
  const sample = (t0, t1, n = 28) => Array.from({ length: n + 1 }, (_, i) => P(t0 + ((t1 - t0) * i) / n));
  return {
    ok: ta < 0.42 && tb > 0.58,
    halfA: sample(ta, 0.5), halfB: sample(tb, 0.5), full: sample(ta, tb, 40),
    m: P(0.5), ux, uy, nx, ny, mid,
    qA: P(ta + (0.5 - ta) * 0.5), qB: P(tb + (0.5 - tb) * 0.5),
    len: Math.hypot(P(tb).x - P(ta).x, P(tb).y - P(ta).y),
  };
}

const toPath = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");

// corta o final da polilinha em `cut` px (espaço para a ponta da seta)
function trimEnd(pts, cut) {
  const out = [...pts];
  while (out.length > 1) {
    const p = out[out.length - 1], q = out[out.length - 2], d = Math.hypot(p.x - q.x, p.y - q.y);
    if (d > cut) { const r = (d - cut) / d; out[out.length - 1] = { x: q.x + (p.x - q.x) * r, y: q.y + (p.y - q.y) * r }; break; }
    cut -= d; out.pop();
  }
  return out;
}

function Half({ pts, ux, uy, color, dashed, width = 7 }) {
  // meia-linha até o meio + ponta de seta no meio (sentido do tráfego); no meio da curva a tangente = direção a→b
  const AL = 13, AW = 9;
  const tip = pts[pts.length - 1];
  const base = { x: tip.x - ux * AL, y: tip.y - uy * AL };
  const px = -uy, py = ux;
  return (
    <g>
      <path d={toPath(trimEnd(pts, AL - 1))} fill="none" stroke={color} strokeWidth={width} strokeLinecap="butt" strokeLinejoin="round"
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
  map, live, devices, editing, tool, selected, onSelect, onMoveNode, onMoveEnd, onConnect, onBendLink, fitSignal,
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
    } else if (drag.kind === "bend" && editing) {
      const w = toWorld(e);
      drag.moved = true;
      let bend = (w.x - drag.mid.x) * drag.nx + (w.y - drag.mid.y) * drag.ny;
      bend = Math.abs(bend) < 8 ? 0 : Math.round(bend / 2) * 2; // gruda na reta perto do zero
      onBendLink?.(drag.id, bend);
    } else if (drag.kind === "node" && editing) {
      const w = toWorld(e);
      drag.moved = true;
      onMoveNode(drag.id, Math.round((w.x - drag.ox) / 10) * 10, Math.round((w.y - drag.oy) / 10) * 10); // grade de 10px
    }
  };
  const onUp = () => {
    if (drag?.kind === "node") {
      if (drag.moved) onMoveEnd?.(); else onSelect({ type: "node", id: drag.id });
    } else if (drag?.kind === "bend") {
      if (!drag.moved) onSelect({ type: "link", id: drag.id });
    } else if (drag?.kind === "pan" && !drag.moved) onSelect(null);
    setDrag(null);
  };

  // enlaces paralelos entre o mesmo par de equipamentos: abrem em leque (curvas), estilo weathermap
  const autoBend = useMemo(() => {
    const groups = {};
    (map.links || []).forEach(l => { const k = [l.from, l.to].sort().join("|"); (groups[k] = groups[k] || []).push(l); });
    const out = {};
    Object.entries(groups).forEach(([k, ls]) => {
      const first = k.split("|")[0];
      ls.forEach((l, i) => {
        const b = (i - (ls.length - 1) / 2) * SPREAD;          // na orientação canônica do par
        out[l.id] = { bend: l.from === first ? b : -b, i, n: ls.length };
      });
    });
    return out;
  }, [map.links]);
  const bendOf = (l) => (l.curve ?? null) !== null ? l.curve : (autoBend[l.id]?.bend || 0);

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
            const par = autoBend[l.id] || { i: 0, n: 1 };
            const g = linkGeometry(a, b, bendOf(l));
            if (!g.ok) return null; // nós sobrepostos
            const lv = live?.links?.[l.id];
            const down = lv?.down;
            const noData = !lv || lv.collecting || (lv.ab_bps == null && lv.ba_bps == null);
            const cAB = down ? STATUS.critical : noData ? NO_DATA : utilColor(lv.ab_pct);
            const cBA = down ? STATUS.critical : noData ? NO_DATA : utilColor(lv.ba_pct);
            const isSel = selected?.type === "link" && selected.id === l.id;
            const tags = g.len > (par.n > 1 ? 150 : 110);
            const hoverOn = (e) => { if (drag) return; const r = wrapRef.current.getBoundingClientRect(); setHover({ linkId: l.id, x: e.clientX - r.left, y: e.clientY - r.top }); };
            return (
              <g key={l.id} data-testid={`link-${l.id}`}>
                {isSel && <path d={toPath(g.full)} fill="none" stroke="#F8FAFC" strokeOpacity="0.35" strokeWidth="15" />}
                <Half pts={g.halfA} ux={g.ux} uy={g.uy} color={cAB} dashed={down || noData} />
                <Half pts={g.halfB} ux={-g.ux} uy={-g.uy} color={cBA} dashed={down || noData} />
                {/* área de clique/hover maior que a linha */}
                <path d={toPath(g.full)} fill="none" stroke="transparent" strokeWidth="22" style={{ cursor: "pointer" }}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); onSelect({ type: "link", id: l.id }); }}
                      onMouseMove={hoverOn} onMouseLeave={() => setHover(null)} />
                {tags && !down && !noData && <>
                  <ValueTag x={g.qA.x} y={g.qA.y} text={fmtBps(lv.ab_bps)} sub={lv.ab_pct != null ? `${lv.ab_pct}%` : null} accent={cAB} />
                  <ValueTag x={g.qB.x} y={g.qB.y} text={fmtBps(lv.ba_bps)} sub={lv.ba_pct != null ? `${lv.ba_pct}%` : null} accent={cBA} />
                </>}
                {down && <ValueTag x={g.m.x} y={g.m.y - 16} text="✕ DOWN" accent={STATUS.critical} />}
                {noData && !down && g.len > 90 && <ValueTag x={g.m.x} y={g.m.y - 16} text={lv?.error ? "⚠ SNMP" : l.from_if || l.to_if ? "coletando…" : "sem interface"} accent={NO_DATA} />}
                {/* alça para curvar/afastar o enlace (modo edição) — duplo clique volta ao automático */}
                {editing && tool === "select" && (
                  <g style={{ cursor: "grab" }} data-testid={`link-handle-${l.id}`}
                     onMouseDown={(e) => { e.stopPropagation(); if (e.button !== 0) return; setHover(null);
                       setDrag({ kind: "bend", id: l.id, mid: g.mid, nx: g.nx, ny: g.ny, moved: false }); }}
                     onDoubleClick={(e) => { e.stopPropagation(); onBendLink?.(l.id, null); }}>
                    <circle cx={g.m.x} cy={g.m.y} r="14" fill="transparent" />
                    <circle cx={g.m.x} cy={g.m.y} r={isSel ? 7 : 5.5} fill="#F8FAFC" stroke="#007AFF" strokeWidth="2.5" />
                    <title>Arraste para curvar/afastar este enlace · duplo clique = automático</title>
                  </g>
                )}
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
          {[["A", hv?.optics_a, hoverLink.from], ["B", hv?.optics_b, hoverLink.to]].filter(([, o]) => o && (o.lanes?.length || o.error)).map(([k, o, nid]) => (
            <div key={k} className="text-slate-300">RX {nodeName(nid)}: {o.lanes?.length
              ? o.lanes.map(l => (l.rx == null ? "—" : l.rx <= -40 ? "sem luz" : l.rx.toFixed(1))).join(" / ") + " dBm"
              : <span className="text-amber-300">⚠ óptica</span>}</div>
          ))}
          <div className="text-slate-500 mt-1">capacidade {fmtSpeed(hv?.capacity_mbps)} · clique para ver gráfico e sinal</div>
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
