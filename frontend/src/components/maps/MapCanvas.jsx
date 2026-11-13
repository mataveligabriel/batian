import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtBps, utilColor, STATUS, NO_DATA, fmtSpeed } from "@/lib/netfmt";
import { GEO_Z, tileUrl } from "@/components/maps/geo";

const NODE = { device: { w: 200, h: 48 }, cloud: { w: 120, h: 44 }, label: { w: 120, h: 28 } };
const SURFACE = "rgb(var(--c-panel))";

const SPREAD = 62; // distância entre enlaces paralelos (no meio da curva)

function insideBox(node, p, s = 1) {
  const z = NODE[node.kind] || NODE.device;
  return Math.abs(p.x - node.x) <= (z.w / 2 + 3) * s && Math.abs(p.y - node.y) <= (z.h / 2 + 3) * s;
}

const qpt = (P0, C, P2, t) => {
  const u = 1 - t;
  return { x: u * u * P0.x + 2 * u * t * C.x + t * t * P2.x, y: u * u * P0.y + 2 * u * t * C.y + t * t * P2.y };
};

// Catmull-Rom: curva suave que passa por todos os pontos
const crPt = (p0, p1, p2, p3, t) => {
  const t2 = t * t, t3 = t2 * t;
  const f = (a, b, c, d) => 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
  return { x: f(p0.x, p1.x, p2.x, p3.x), y: f(p0.y, p1.y, p2.y, p3.y) };
};
function smoothPath(P, n = 14) {
  const out = [P[0]];
  for (let i = 0; i < P.length - 1; i++) {
    const p0 = P[i - 1] || P[i], p3 = P[i + 2] || P[i + 1];
    for (let k = 1; k <= n; k++) out.push(crPt(p0, P[i], P[i + 1], p3, k / n));
  }
  return out;
}
// meio de cada trecho entre pontos (onde ficam as alças "+" para criar ponto novo)
function segMids(P, smooth) {
  return P.slice(0, -1).map((p, i) => (smooth ? crPt(P[i - 1] || p, p, P[i + 1], P[i + 2] || P[i + 1], 0.5)
    : { x: (p.x + P[i + 1].x) / 2, y: (p.y + P[i + 1].y) / 2 }));
}

// corta o começo da polilinha onde ela sai da caixa do equipamento
function clipStart(poly, node, s) {
  let i = 0;
  while (i < poly.length && insideBox(node, poly[i], s)) i++;
  if (i >= poly.length) return null;
  if (i === 0) return poly.slice();
  let lo = poly[i - 1], hi = poly[i];
  for (let k = 0; k < 18; k++) {
    const m = { x: (lo.x + hi.x) / 2, y: (lo.y + hi.y) / 2 };
    if (insideBox(node, m, s)) lo = m; else hi = m;
  }
  return [hi, ...poly.slice(i)];
}

/**
 * Traçado do enlace: sem pontos = curva quadrática (`bend` px na normal de a→b; 0 = reta);
 * com pontos = passa por eles (suave ou em segmentos retos). Devolve as duas metades (cada uma com a seta no meio),
 * onde ficam os rótulos e os pontos de edição.
 */
function linkGeometry(a, b, bend, points, shape, s = 1) {
  let dense, anchors = null;
  if (points?.length) {
    anchors = [{ x: a.x, y: a.y }, ...points, { x: b.x, y: b.y }];
    dense = shape === "straight" ? anchors : smoothPath(anchors);
  } else {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len, mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    const C = { x: mid.x + nx * bend * 2, y: mid.y + ny * bend * 2 };
    dense = Array.from({ length: 49 }, (_, i) => qpt(a, C, b, i / 48));
  }
  let poly = clipStart(dense, a, s);
  poly = poly && clipStart([...poly].reverse(), b, s);
  if (!poly || poly.length < 2) return { ok: false };
  poly.reverse();
  const cum = [0];
  for (let i = 1; i < poly.length; i++) cum.push(cum[i - 1] + Math.hypot(poly[i].x - poly[i - 1].x, poly[i].y - poly[i - 1].y));
  const L = cum[cum.length - 1];
  if (L < 24 * s) return { ok: false };
  const at = (d) => {
    let i = 1;
    while (i < poly.length - 1 && cum[i] < d) i++;
    const seg = cum[i] - cum[i - 1] || 1, r = (d - cum[i - 1]) / seg;
    return { p: { x: poly[i - 1].x + (poly[i].x - poly[i - 1].x) * r, y: poly[i - 1].y + (poly[i].y - poly[i - 1].y) * r }, i };
  };
  const mid = at(L / 2);
  const off = Math.min(L * 0.12, 40 * s);
  return {
    ok: true, full: poly, len: L, m: mid.p,
    halfA: [...poly.slice(0, mid.i), mid.p], halfB: [...poly.slice(mid.i).reverse(), mid.p],
    qA: at(L * 0.25).p, qB: at(L * 0.75).p, cA: at(off).p, cB: at(L - off).p,
    ghosts: anchors ? segMids(anchors, shape !== "straight") : null,
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

function Half({ pts, color, dashed, s = 1 }) {
  // meia-linha até o meio + ponta de seta no meio (sentido do tráfego), na direção do último trecho
  const AL = 13 * s, AW = 9 * s;
  const tip = pts[pts.length - 1];
  let j = pts.length - 2;
  while (j > 0 && Math.hypot(tip.x - pts[j].x, tip.y - pts[j].y) < 0.5 * s) j--;
  const d = Math.hypot(tip.x - pts[j].x, tip.y - pts[j].y) || 1;
  const ux = (tip.x - pts[j].x) / d, uy = (tip.y - pts[j].y) / d;
  const base = { x: tip.x - ux * AL, y: tip.y - uy * AL };
  const px = -uy, py = ux;
  return (
    <g>
      <path d={toPath(trimEnd(pts, AL - s))} fill="none" stroke={color} strokeWidth={7 * s} strokeLinecap="butt" strokeLinejoin="round"
            strokeDasharray={dashed ? `${10 * s} ${7 * s}` : undefined} />
      <polygon points={`${tip.x},${tip.y} ${base.x + px * AW},${base.y + py * AW} ${base.x - px * AW},${base.y - py * AW}`}
               fill={color} stroke={SURFACE} strokeWidth={1.5 * s} />
    </g>
  );
}

function ValueTag({ x, y, text, sub, accent, s = 1 }) {
  const w = Math.max(64, text.length * 6.6 + 14);
  return (
    <g pointerEvents="none" transform={`translate(${x},${y}) scale(${s})`}>
      <rect x={-w / 2} y={-12} width={w} height={sub ? 27 : 18} rx="4" fill={SURFACE} stroke={accent} strokeWidth="1.5" />
      <text x={0} y={1} textAnchor="middle" fontSize="11" fontFamily="JetBrains Mono, monospace" fill="rgb(var(--c-slate-100))">{text}</text>
      {sub && <text x={0} y={12} textAnchor="middle" fontSize="9" fontFamily="JetBrains Mono, monospace" fill="rgb(var(--c-slate-400))">{sub}</text>}
    </g>
  );
}

// fundo de satélite/ruas: tiles visíveis no zoom mais próximo da escala atual
function Tiles({ geo, view, size }) {
  if (!size.w) return null;
  const out = [];
  geo.layers.forEach((ly, li) => {
    const z = Math.max(1, Math.min(ly.maxZoom || 19, Math.round(GEO_Z + Math.log2(view.k))));
    const ts = 256 * 2 ** (GEO_Z - z), n = 2 ** z, ov = ts / 256;
    const x0 = Math.max(0, Math.floor(-view.tx / view.k / ts)), x1 = Math.min(n - 1, Math.floor((size.w - view.tx) / view.k / ts));
    const y0 = Math.max(0, Math.floor(-view.ty / view.k / ts)), y1 = Math.min(n - 1, Math.floor((size.h - view.ty) / view.k / ts));
    if ((x1 - x0 + 1) * (y1 - y0 + 1) > 300) return;
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
      out.push(<image key={`${li}-${z}-${x}-${y}`} href={tileUrl(ly.url, z, x, y)} x={x * ts} y={y * ts} width={ts + ov} height={ts + ov}
                      opacity={ly.opacity} preserveAspectRatio="none" />);
    }
  });
  return <g pointerEvents="none">{out}</g>;
}

export function MapCanvas({
  map, live, devices, editing, tool, selected, onSelect, onMoveNode, onMoveEnd, onConnect, onLinkPoints, fitSignal,
  geo = null,     // {layers, attribution, scale}: desenha sobre o mapa (coordenadas em pixels Web Mercator)
  costs = null,   // análise: {linkId: {ab, ba, changed}} -> custo OSPF perto de cada ponta
  marks = null,   // análise: {linkId: "crit" | "warn"} -> halo no enlace com problema
}) {
  const wrapRef = useRef(null);
  const svgRef = useRef(null);
  const [view, setView] = useState({ tx: 40, ty: 40, k: 1 });
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [drag, setDrag] = useState(null);       // {kind:"pan"|"node"|"vertex"|"pinch", ...}
  const [pending, setPending] = useState(null); // 1º nó escolhido na ferramenta "ligar"
  const [hover, setHover] = useState(null);     // {linkId, x, y}
  const nodesById = useMemo(() => Object.fromEntries((map.nodes || []).map(n => [n.id, n])), [map.nodes]);
  const devById = useMemo(() => Object.fromEntries((devices || []).map(d => [d.id, d])), [devices]);
  const [kMin, kMax] = geo ? [2 ** (3 - GEO_Z), 2 ** (19 - GEO_Z)] : [0.2, 3];
  const clampK = (k) => Math.max(kMin, Math.min(kMax, k));
  const s = geo ? (geo.scale || 0.8) / view.k : 1;   // no mapa as caixas e linhas ficam do mesmo tamanho na tela

  useEffect(() => { if (tool !== "connect") setPending(null); }, [tool]);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el); setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // ajustar à tela
  const fit = useCallback(() => {
    const el = wrapRef.current;
    const ns = map.nodes || [];
    if (!el || !ns.length) return setView(v => ({ ...v, tx: 40, ty: 40 }));
    const xs = ns.map(n => n.x), ys = ns.map(n => n.y);
    const W = el.clientWidth, H = el.clientHeight;
    if (geo) {   // margens em pixels de tela (as caixas não escalam com o zoom)
      const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
      const k = Math.max(kMin, Math.min(4, Math.min((W - 260) / Math.max(1, maxX - minX), (H - 140) / Math.max(1, maxY - minY))));
      return setView({ k, tx: W / 2 - ((minX + maxX) / 2) * k, ty: H / 2 - ((minY + maxY) / 2) * k });
    }
    const minX = Math.min(...xs) - 110, maxX = Math.max(...xs) + 110, minY = Math.min(...ys) - 70, maxY = Math.max(...ys) + 70;
    const k = Math.max(0.25, Math.min(1.6, Math.min(W / (maxX - minX), H / (maxY - minY))));
    setView({ k, tx: (W - (maxX - minX) * k) / 2 - minX * k, ty: (H - (maxY - minY) * k) / 2 - minY * k });
  }, [map.nodes, geo, kMin]);
  useEffect(() => { fit(); }, [fitSignal, map.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const toWorld = (e) => {
    const r = svgRef.current.getBoundingClientRect();
    return { x: (e.clientX - r.left - view.tx) / view.k, y: (e.clientY - r.top - view.ty) / view.k };
  };

  const zoomAt = (mx, my, f) => setView(v => {
    const k = clampK(v.k * f);
    return { k, tx: mx - ((mx - v.tx) / v.k) * k, ty: my - ((my - v.ty) / v.k) * k };
  });
  const onWheel = (e) => {
    e.preventDefault();
    const r = svgRef.current.getBoundingClientRect();
    zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
  };
  useEffect(() => {
    const el = svgRef.current;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  // toque: dois dedos = zoom (pinça) em volta do ponto médio
  const pts = useRef(new Map());
  const pinchStart = () => {
    const [a, b] = [...pts.current.values()];
    const r = svgRef.current.getBoundingClientRect();
    return { kind: "pinch", d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2 - r.left, my: (a.y + b.y) / 2 - r.top, v0: view, moved: true };
  };
  const onDownCapture = (e) => {
    if (e.pointerType === "mouse") return;
    pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.current.size === 2) { setHover(null); setDrag(pinchStart()); }
  };
  const onPtrEnd = (e) => {
    pts.current.delete(e.pointerId);
    if (drag?.kind === "pinch") { if (pts.current.size < 2) setDrag(null); return; }
    onUp();
  };

  const onBgDown = (e) => {
    if (e.button !== 0 || pts.current.size >= 2) return;
    setDrag({ kind: "pan", sx: e.clientX, sy: e.clientY, tx: view.tx, ty: view.ty, moved: false });
  };
  const onNodeDown = (e, n) => {
    e.stopPropagation();
    if (e.button !== 0 || pts.current.size >= 2) return;
    if (editing && tool === "connect") {
      if (!pending) setPending(n.id);
      else if (pending !== n.id) { onConnect(pending, n.id); setPending(null); }
      return;
    }
    const w = toWorld(e);
    setDrag({ kind: "node", id: n.id, ox: w.x - n.x, oy: w.y - n.y, moved: false });
  };
  // ponto do traçado: arrastar move; "insert" cria um ponto novo na posição idx
  const startVertex = (e, l, idx, insert) => {
    e.stopPropagation();
    if (e.button !== 0 || pts.current.size >= 2) return;
    setHover(null);
    setDrag({ kind: "vertex", id: l.id, idx, insert, base: (l.points || []).map(p => ({ ...p })), moved: false });
  };
  const onMove = (e) => {
    if (pts.current.has(e.pointerId)) pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (!drag) return;
    if (drag.kind === "pinch") {
      if (pts.current.size < 2) return;
      const [a, b] = [...pts.current.values()];
      const v = drag.v0, k = clampK(v.k * (Math.hypot(a.x - b.x, a.y - b.y) / drag.d0));
      const r = svgRef.current.getBoundingClientRect();
      const mx = (a.x + b.x) / 2 - r.left, my = (a.y + b.y) / 2 - r.top;   // também arrasta com os dois dedos
      setView({ k, tx: mx - ((drag.mx - v.tx) / v.k) * k, ty: my - ((drag.my - v.ty) / v.k) * k });
      return;
    }
    if (drag.kind === "pan") {
      setView(v => ({ ...v, tx: drag.tx + e.clientX - drag.sx, ty: drag.ty + e.clientY - drag.sy }));
      if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) drag.moved = true;
    } else if (drag.kind === "vertex" && editing) {
      const w = toWorld(e);
      const p = geo ? w : { x: Math.round(w.x), y: Math.round(w.y) };
      drag.moved = true;
      const next = [...drag.base];
      if (drag.insert) next.splice(drag.idx, 0, p); else next[drag.idx] = p;
      onLinkPoints?.(drag.id, next);
    } else if (drag.kind === "node" && editing) {
      const w = toWorld(e);
      drag.moved = true;
      if (geo) onMoveNode(drag.id, w.x - drag.ox, w.y - drag.oy);
      else onMoveNode(drag.id, Math.round((w.x - drag.ox) / 10) * 10, Math.round((w.y - drag.oy) / 10) * 10); // grade de 10px
    }
  };
  const onUp = () => {
    if (drag?.kind === "node") {
      if (drag.moved) onMoveEnd?.(); else onSelect({ type: "node", id: drag.id });
    } else if (drag?.kind === "vertex") {
      if (!drag.moved) onSelect({ type: "link", id: drag.id });
    } else if (drag?.kind === "pan" && !drag.moved) onSelect(null);
    setDrag(null);
  };
  const removeVertex = (l, idx) => onLinkPoints?.(l.id, (l.points || []).filter((_, i) => i !== idx));

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
  const bendOf = (l) => (l.curve ?? null) !== null ? l.curve : (autoBend[l.id]?.bend || 0) * s;

  const hoverLink = hover && (map.links || []).find(l => l.id === hover.linkId);
  const hv = hoverLink && live?.links?.[hoverLink.id];
  const nodeName = (id) => { const n = nodesById[id]; return n?.device_id ? (devById[n.device_id]?.name || n.label || "?") : (n?.label || "?"); };
  const editLinks = editing && tool === "select";

  return (
    <div ref={wrapRef} className="relative w-full h-full overflow-hidden select-none" style={{ background: geo ? "#0b1220" : SURFACE, touchAction: "none" }}
         onPointerDownCapture={onDownCapture} onPointerMove={onMove} onPointerUp={onPtrEnd} onPointerCancel={onPtrEnd}
         onPointerLeave={(e) => { if (e.pointerType === "mouse") { setDrag(null); setHover(null); } }} data-testid="map-canvas">
      <svg ref={svgRef} className={`w-full h-full ${drag?.kind === "pan" ? "cursor-grabbing" : "cursor-grab"}`} style={{ touchAction: "none" }} onPointerDown={onBgDown}>
        {!geo && <>
          <defs>
            <pattern id="grid" width={40 * view.k} height={40 * view.k} patternUnits="userSpaceOnUse" x={view.tx} y={view.ty}>
              <circle cx="1" cy="1" r="1" fill="rgb(var(--c-line))" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#grid)" />
        </>}
        <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
          {geo && <Tiles geo={geo} view={view} size={size} />}
          {(map.links || []).map(l => {
            const a = nodesById[l.from], b = nodesById[l.to];
            if (!a || !b) return null;
            const par = autoBend[l.id] || { i: 0, n: 1 };
            const g = linkGeometry(a, b, bendOf(l), l.points, l.shape, s);
            if (!g.ok) return null; // nós sobrepostos
            const lv = live?.links?.[l.id];
            const down = lv?.down;
            const noData = !lv || lv.collecting || (lv.ab_bps == null && lv.ba_bps == null);
            const cAB = down ? STATUS.critical : noData ? NO_DATA : utilColor(lv.ab_pct);
            const cBA = down ? STATUS.critical : noData ? NO_DATA : utilColor(lv.ba_pct);
            const isSel = selected?.type === "link" && selected.id === l.id;
            const tags = g.len > (par.n > 1 ? 150 : 110) * s;
            const hoverOn = (e) => { if (drag) return; const r = wrapRef.current.getBoundingClientRect(); setHover({ linkId: l.id, x: e.clientX - r.left, y: e.clientY - r.top }); };
            const hasPts = !!l.points?.length;
            return (
              <g key={l.id} data-testid={`link-${l.id}`}>
                {marks?.[l.id] && <path d={toPath(g.full)} fill="none" stroke={marks[l.id] === "crit" ? STATUS.critical : STATUS.warning} strokeOpacity="0.3" strokeWidth={19 * s} strokeLinejoin="round" />}
                {isSel && <path d={toPath(g.full)} fill="none" stroke="rgb(var(--c-slate-50))" strokeOpacity="0.35" strokeWidth={15 * s} strokeLinejoin="round" />}
                <Half pts={g.halfA} color={cAB} dashed={down || noData} s={s} />
                <Half pts={g.halfB} color={cBA} dashed={down || noData} s={s} />
                {/* área de clique/hover maior que a linha */}
                <path d={toPath(g.full)} fill="none" stroke="transparent" strokeWidth={22 * s} style={{ cursor: "pointer" }}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); onSelect({ type: "link", id: l.id }); }}
                      onPointerMove={(e) => e.pointerType === "mouse" && hoverOn(e)} onPointerLeave={() => setHover(null)} />
                {tags && !down && !noData && <>
                  <ValueTag x={g.qA.x} y={g.qA.y} s={s} text={fmtBps(lv.ab_bps)} sub={lv.ab_pct != null ? `${lv.ab_pct}%` : null} accent={cAB} />
                  <ValueTag x={g.qB.x} y={g.qB.y} s={s} text={fmtBps(lv.ba_bps)} sub={lv.ba_pct != null ? `${lv.ba_pct}%` : null} accent={cBA} />
                </>}
                {down && <ValueTag x={g.m.x} y={g.m.y - 16 * s} s={s} text="✕ DOWN" accent={STATUS.critical} />}
                {costs?.[l.id] && [["ab", g.cA], ["ba", g.cB]].map(([k, p]) => {
                  const c = costs[l.id][k];
                  if (c === null || c === undefined) return null;
                  const w = 14 + String(c).length * 7;
                  const hot = costs[l.id].changed?.[k];
                  return (
                    <g key={k} pointerEvents="none" transform={`translate(${p.x},${p.y}) scale(${s})`}>
                      <rect x={-w / 2} y={-9} width={w} height="18" rx="9" fill="rgb(var(--c-panel))" stroke={hot ? "#FAB219" : "rgb(var(--c-slate-400))"} strokeWidth={hot ? 2 : 1.2} />
                      <text x={0} y={4} textAnchor="middle" fontSize="11" fontWeight="700" fontFamily="JetBrains Mono, monospace" fill={hot ? "#FAB219" : "rgb(var(--c-slate-200))"}>{c}</text>
                    </g>
                  );
                })}
                {noData && !down && g.len > 90 * s && <ValueTag x={g.m.x} y={g.m.y - 16 * s} s={s} text={lv?.error ? "⚠ SNMP" : l.from_if || l.to_if ? "coletando…" : "sem interface"} accent={NO_DATA} />}
                {/* edição do traçado: bolinha do meio cria um ponto; quadrados = pontos (arrastar / duplo clique apaga); "+" = ponto novo no trecho */}
                {editLinks && !hasPts && (
                  <g style={{ cursor: "grab" }} data-testid={`link-handle-${l.id}`} transform={`translate(${g.m.x},${g.m.y}) scale(${s})`}
                     onPointerDown={(e) => startVertex(e, l, 0, true)} onDoubleClick={(e) => { e.stopPropagation(); onLinkPoints?.(l.id, null); }}>
                    <circle r="18" fill="transparent" />
                    <circle r={isSel ? 7 : 5.5} fill="rgb(var(--c-slate-50))" stroke="rgb(var(--c-brand))" strokeWidth="2.5" />
                    <title>Arraste para levar o enlace por onde quiser · duplo clique = automático</title>
                  </g>
                )}
                {editLinks && hasPts && l.points.map((p, i) => (
                  <g key={`v${i}`} style={{ cursor: "move" }} transform={`translate(${p.x},${p.y}) scale(${s})`} data-testid={`link-pt-${l.id}-${i}`}
                     onPointerDown={(e) => startVertex(e, l, i, false)} onDoubleClick={(e) => { e.stopPropagation(); removeVertex(l, i); }}>
                    <circle r="14" fill="transparent" />
                    <rect x={-5} y={-5} width="10" height="10" rx="2" fill="rgb(var(--c-slate-50))" stroke="rgb(var(--c-brand))" strokeWidth="2.5" />
                    <title>Arraste para mover · duplo clique apaga este ponto</title>
                  </g>
                ))}
                {editLinks && hasPts && isSel && g.ghosts.map((p, i) => (
                  <g key={`g${i}`} style={{ cursor: "copy" }} transform={`translate(${p.x},${p.y}) scale(${s})`} onPointerDown={(e) => startVertex(e, l, i, true)}>
                    <circle r="12" fill="transparent" />
                    <circle r="6" fill="rgb(var(--c-brand))" fillOpacity="0.55" stroke="rgb(var(--c-slate-50))" strokeWidth="1.5" />
                    <path d="M-3,0 H3 M0,-3 V3" stroke="white" strokeWidth="1.5" />
                    <title>Arraste para criar um ponto aqui</title>
                  </g>
                ))}
              </g>
            );
          })}

          {(map.nodes || []).map(n => {
            const sz = NODE[n.kind] || NODE.device;
            const dev = n.device_id ? devById[n.device_id] : null;
            const st = live?.nodes?.[n.id];
            const status = st?.status || dev?.status || "unknown";
            const stColor = status === "online" ? STATUS.good : status === "offline" ? STATUS.critical : "rgb(var(--c-slate-500))";
            const isSel = selected?.type === "node" && selected.id === n.id;
            const isPending = pending === n.id;
            const title = n.kind === "device" ? (dev?.name || n.label || "equipamento removido") : (n.label || (n.kind === "cloud" ? "Internet" : "texto"));
            const cursor = editing ? (tool === "connect" ? "crosshair" : "move") : "pointer";
            const tr = `translate(${n.x},${n.y})${s !== 1 ? ` scale(${s})` : ""}`;
            if (n.kind === "label") {
              return (
                <g key={n.id} transform={tr} onPointerDown={(e) => onNodeDown(e, n)} style={{ cursor }} data-testid={`node-${n.id}`}>
                  <rect x={-sz.w / 2} y={-sz.h / 2} width={sz.w} height={sz.h} fill={geo ? "rgba(0,0,0,.45)" : "transparent"} stroke={isSel ? "rgb(var(--c-brand-soft))" : "transparent"} strokeDasharray="4 3" rx="4" />
                  <text textAnchor="middle" y="5" fontSize="14" fontWeight="600" fill={geo ? "#fff" : "rgb(var(--c-slate-300))"} fontFamily="Outfit, sans-serif">{title}</text>
                </g>
              );
            }
            return (
              <g key={n.id} transform={tr} onPointerDown={(e) => onNodeDown(e, n)} style={{ cursor }} data-testid={`node-${n.id}`}>
                <rect x={-sz.w / 2} y={-sz.h / 2} width={sz.w} height={sz.h} rx={n.kind === "cloud" ? sz.h / 2 : 8}
                      fill="rgb(var(--c-surface))" fillOpacity={geo ? 0.92 : 1}
                      stroke={isPending ? "#FAB219" : isSel ? "rgb(var(--c-brand-soft))" : n._nogeo ? "#FAB219" : "rgb(var(--c-line2))"} strokeWidth={isSel || isPending ? 2.5 : 1.5}
                      strokeDasharray={n.kind === "cloud" || n._nogeo ? "5 4" : undefined} />
                {n.kind === "device" ? <>
                  <circle cx={-sz.w / 2 + 13} cy={-6} r="4.5" fill={stColor} />
                  <text x={-sz.w / 2 + 24} y={-2} fontSize="12" fontWeight="600" fill="rgb(var(--c-slate-100))" fontFamily="DM Sans, sans-serif">
                    {title.length > 24 ? title.slice(0, 23) + "…" : title}
                  </text>
                  <text x={-sz.w / 2 + 24} y={13} fontSize="10" fill={n._nogeo ? "#FAB219" : "rgb(var(--c-slate-400))"} fontFamily="JetBrains Mono, monospace">
                    {n._nogeo ? "sem posição no mapa" : <>{status}{st?.snmp_error ? " · ⚠ snmp" : ""}{dev ? ` · ${dev.host}` : ""}</>}
                  </text>
                  <title>{`${title}\n${dev?.host || ""}\nstatus: ${status}${st?.snmp_error ? `\nSNMP: ${st.snmp_error}` : ""}`}</title>
                </> : (
                  <text textAnchor="middle" y="5" fontSize="13" fontWeight="600" fill="rgb(var(--c-slate-300))" fontFamily="DM Sans, sans-serif">☁ {title}</text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {editing && tool === "connect" && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-surface border border-amber-500/50 text-amber-200 text-xs font-mono px-3 py-1.5 rounded shadow-lg">
          {pending ? `Agora clique no 2º equipamento para ligar a ${nodeName(pending)}` : "Clique no 1º equipamento do link"}
        </div>
      )}

      {hoverLink && (
        <div className="absolute z-20 pointer-events-none bg-surface border border-line2 rounded-md px-3 py-2 text-[11px] font-mono text-slate-200 shadow-xl w-72"
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

      {geo?.attribution && <div className="absolute bottom-0 left-0 px-1.5 py-0.5 text-[9px] text-slate-300 bg-black/50 pointer-events-none">{geo.attribution}</div>}
      <div className="absolute bottom-3 right-3 flex items-center gap-1 bg-surface/90 border border-line rounded px-2 py-1">
        <button className="px-1.5 text-slate-300 hover:text-white" onClick={() => zoomAt(size.w / 2, size.h / 2, 1.25)} title="Aproximar">+</button>
        <span className="text-[10px] font-mono text-slate-500 w-9 text-center">{geo ? `z${Math.round(GEO_Z + Math.log2(view.k))}` : `${Math.round(view.k * 100)}%`}</span>
        <button className="px-1.5 text-slate-300 hover:text-white" onClick={() => zoomAt(size.w / 2, size.h / 2, 1 / 1.25)} title="Afastar">−</button>
        <button className="px-1.5 text-[11px] text-slate-300 hover:text-white border-l border-line ml-1" onClick={fit} title="Ajustar à tela">ajustar</button>
      </div>
    </div>
  );
}
