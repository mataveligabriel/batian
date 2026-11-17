import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { fmtSpeed } from "@/lib/netfmt";
import { InterfacePicker } from "@/components/maps/InterfacePicker";
import { tvHtml } from "@/components/maps/TvShares";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { DeviceSelect } from "@/components/TagSelect";
import {
  ChevronDown, ChevronRight, Hexagon, Link2, Loader2, Maximize2, MousePointer2, Pencil, Plus, RotateCcw, Save, Search, Settings2,
  Palette, Shapes, StickyNote, Trash2, Type, Upload, Volume2, VolumeX, X, Zap,
} from "lucide-react";

// Paleta própria do mapa (independe do tema do app: o mapa é sempre "sala escura")
export const C = { bg0: "#06111d", bg1: "#0b2034", grid: "rgba(96,165,250,0.07)", ink: "#e3eef8", dim: "#7f9bb3", panel: "#0d1d2e",
  up: "#2dd4bf", warn: "#f5b544", down: "#ff4d5e", pending: "#5b6f86", empty: "#3a4b5e", line: "#1d3a55", ignored: "#5b6f86" };
// temas prontos da aparência do mapa (cada cor pode ser trocada depois)
export const PRESETS = {
  neon: { label: "Neon (padrão)", bg0: "#06111d", bg1: "#0b2034", ink: "#e3eef8", dim: "#7f9bb3", panel: "#0d1d2e", line: "#1d3a55", up: "#2dd4bf", warn: "#f5b544", down: "#ff4d5e", pending: "#5b6f86" },
  classic: { label: "Claro", bg0: "#dfe6ee", bg1: "#f7f9fb", ink: "#16212c", dim: "#55687a", panel: "#ffffff", line: "#b9c6d3", up: "#16a34a", warn: "#d97706", down: "#dc2626", pending: "#94a3b8" },
  graphite: { label: "Grafite", bg0: "#101215", bg1: "#1b1f24", ink: "#e5e7eb", dim: "#9ca3af", panel: "#1f242a", line: "#2f363e", up: "#22c55e", warn: "#eab308", down: "#ef4444", pending: "#6b7280" },
  contrast: { label: "Alto contraste", bg0: "#000000", bg1: "#000000", ink: "#ffffff", dim: "#d1d5db", panel: "#000000", line: "#3f3f46", up: "#00ff9c", warn: "#ffd400", down: "#ff2d55", pending: "#8e8e93" },
  ocean: { label: "Oceano", bg0: "#041b2d", bg1: "#0a3354", ink: "#e6f4ff", dim: "#8fb3cc", panel: "#072640", line: "#13466d", up: "#38bdf8", warn: "#fbbf24", down: "#fb7185", pending: "#64748b" },
};
const hexA = (h, a) => (/^#[0-9a-f]{6}$/i.test(h || "") ? `${h}${Math.round(a * 255).toString(16).padStart(2, "0")}` : h);
// paleta efetiva do mapa: tema padrão + o que o mapa personalizou
export function paletteOf(style) {
  const p = { ...C, ...Object.fromEntries(Object.entries(style || {}).filter(([k, v]) => k in C && /^#[0-9a-f]{6}$/i.test(v || ""))) };
  p.grid = hexA(p.line, 0.45) || C.grid;
  p.empty = hexA(p.pending, 0.6) || C.empty;
  p.ignored = p.pending;
  return p;
}

/** Aparência que vale para o mapa: a própria (mapa principal ou "aparência própria") ou a do mapa de cima mais próximo
 *  que tenha a sua — primeiro pelo caminho aberto (trail), depois subindo pelos pais. maps: {id: {style, parents?, nodes?}} */
// aparência própria: marcada como tal, ou (gravada antes de existir a opção) com alguma coisa mudada
export const styleOwn = (st) => (!st ? false : st.own !== undefined ? !!st.own
  : ["up", "warn", "down", "pending", "bg0", "bg1", "ink", "dim", "panel", "line"].some(k => st[k]) || (st.preset || "neon") !== "neon"
    || (st.bg || "grid") !== "grid" || (st.sub_shape || "hex") !== "hex" || (st.dev_shape || "orb") !== "orb");
export function effectiveStyle(id, maps, trail = []) {
  const parentsOf = {};
  const hasParents = Object.values(maps).some(m => m.parents);
  if (!hasParents) Object.values(maps).forEach(m => (m.nodes || []).forEach(n => { if (n.kind === "submap") (parentsOf[n.map_id] ||= []).push(m.id); }));
  const par = (x) => (hasParents ? maps[x]?.parents || [] : parentsOf[x] || []);
  const final = (x) => styleOwn(maps[x]?.style) || !par(x).length;
  if (!maps[id] || final(id)) return maps[id]?.style || {};
  const seen = new Set([id]);
  const queue = [...trail.slice(0, Math.max(0, trail.indexOf(id))).reverse(), ...par(id)];
  while (queue.length) {
    const x = queue.shift();
    if (seen.has(x) || !maps[x]) continue;
    seen.add(x);
    if (final(x)) return maps[x].style || {};
    queue.push(...par(x));
  }
  return maps[id].style || {};
}
export const STATE_TXT = { up: "respondendo", warn: "instável", down: "sem resposta", pending: "aguardando 1º ping", empty: "vazio", ignored: "ignorado" };
// "ignorado até 14:30" / "ignorado até 18/11 09:00" / "ignorado"
export const ignoredTxt = (until) => {
  if (!until) return "ignorado";
  const d = new Date(until), now = new Date();
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? `ignorado até ${hm}` : `ignorado até ${d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })} ${hm}`;
};
const R_DEV = 22, R_SUB = 36;
// espessura pela capacidade: 1G = base, 10G = +50%, 100G = o dobro (abaixo de 1G fica na base)
export const capWidth = (base, mbps) => base * (1 + 0.5 * Math.min(2, Math.max(0, Math.log10((mbps || 0) / 1000) || 0)));
export const fmtBps = (b) => (b == null ? "—" : b >= 1e9 ? `${(b / 1e9).toFixed(b >= 1e10 ? 0 : 1)} Gbps` : b >= 1e6 ? `${(b / 1e6).toFixed(b >= 1e8 ? 0 : 1)} Mbps` : b >= 1e3 ? `${Math.round(b / 1e3)} kbps` : `${Math.round(b)} bps`);
const uid = () => Math.random().toString(36).slice(2, 10);
export const ago = (iso) => {
  if (!iso) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
};
export const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "");
const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const ls = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ok */ } } };

const CSS = `
@keyframes av-shock { 0% { r: 24; opacity: .55 } 100% { r: 54; opacity: 0 } }
@keyframes av-sweep { to { transform: rotate(360deg) } }
.av-shock { animation: av-shock 1.6s ease-out infinite; transform-box: fill-box }
.av-sweep { animation: av-sweep 9s linear infinite; transform-origin: 0 0 }
@media (prefers-reduced-motion: reduce) { .av-shock, .av-sweep { animation: none } }
`;

// ícones desenhados (16x16, centro 0,0) por tipo de equipamento
function Glyph({ type, color }) {
  const p = { stroke: color, strokeWidth: 1.7, fill: "none", strokeLinecap: "round", strokeLinejoin: "round" };
  const t = String(type || "").toLowerCase();
  if (/olt|zte|fiberhome/.test(t)) return <g {...p}><circle r="2" /><path d="M-7,-6 L-2,-1.5 M7,-6 L2,-1.5 M-7,6 L-2,1.5 M7,6 L2,1.5" /></g>;
  if (/mikrotik|routeros|juniper|huawei|cisco|router/.test(t)) return <g {...p}><path d="M-7,-3 H5 M2,-6 L5,-3 L2,0 M7,3 H-5 M-2,0 L-5,3 L-2,6" /></g>;
  if (/datacom|switch|dmos/.test(t)) return <g {...p}><rect x="-7.5" y="-4.5" width="15" height="9" rx="1.5" /><path d="M-4.5,0 h1 M-1,0 h1 M2.5,0 h1" /></g>;
  if (/linux|server|windows|proxmox|esxi/.test(t)) return <g {...p}><rect x="-6" y="-7" width="12" height="6" rx="1" /><rect x="-6" y="1" width="12" height="6" rx="1" /></g>;
  return <g {...p}><circle r="5.5" /><circle r="1.4" fill={color} /></g>;
}

// curva suave (Catmull-Rom) que passa por todos os pontos
function crPt(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  const f = (a, b, c, d) => 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
  return { x: f(p0.x, p1.x, p2.x, p3.x), y: f(p0.y, p1.y, p2.y, p3.y) };
}
function catmull(P, n = 14) {
  const out = [P[0]];
  for (let i = 0; i < P.length - 1; i++) {
    const p0 = P[i - 1] || P[i], p3 = P[i + 2] || P[i + 1];
    for (let k = 1; k <= n; k++) out.push(crPt(p0, P[i], P[i + 1], p3, k / n));
  }
  return out;
}
// ponto no meio do comprimento da linha (onde ficam o selo e o texto)
function halfway(pts) {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
  const L = cum[cum.length - 1], h = L / 2;
  let i = 1;
  while (i < pts.length - 1 && cum[i] < h) i++;
  const seg = cum[i] - cum[i - 1] || 1, r = (h - cum[i - 1]) / seg;
  return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * r, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * r, len: L };
}

function hexPath(r) {
  return Array.from({ length: 6 }, (_, i) => { const a = (Math.PI / 3) * i - Math.PI / 2; return `${i ? "L" : "M"}${(r * Math.cos(a)).toFixed(1)},${(r * Math.sin(a)).toFixed(1)}`; }).join(" ") + "Z";
}

// anel segmentado: fatia de cada estado dentro do submapa
function Ring({ s, r, C }) {
  const circ = 2 * Math.PI * r, total = s?.total || 0;
  if (!total) return <circle r={r} fill="none" stroke={C.empty} strokeWidth="3" strokeDasharray="2 5" />;
  let off = 0;
  return <g transform="rotate(-90)">{["down", "warn", "up", "pending"].map(k => {
    const len = (s[k] / total) * circ;
    if (!len) return null;
    const el = <circle key={k} r={r} fill="none" stroke={C[k]} strokeWidth="4" strokeDasharray={`${Math.max(0, len - 2)} ${circ}`} strokeDashoffset={-off} />;
    off += len;
    return el;
  })}</g>;
}

export function MapCanvasAvail({ map, live, names = {}, editing, tool, selected, onSelect, onMove, onConnect, onEnter, onLinkPoints, onPatchNode, fitSignal }) {
  const wrap = useRef(null), svg = useRef(null);
  const sty = map.style || {};
  const C = useMemo(() => paletteOf(map.style), [map.style]);   // paleta deste mapa (sobrepõe a padrão)
  const K = Math.max(0.5, Math.min(2, Number(sty.scale) || 1));  // tamanho dos ícones
  const subShape = sty.sub_shape || "hex", devShape = sty.dev_shape || "orb";
  const flow = sty.flow !== false;
  const lineW = Number(sty.line_w) || 2, lineAuto = sty.line_auto !== false, traffic = sty.traffic !== false;
  const [view, setView] = useState({ tx: 0, ty: 0, k: 1 });
  const [drag, setDrag] = useState(null);
  const [pending, setPending] = useState(null);
  const byId = useMemo(() => Object.fromEntries(map.nodes.map(n => [n.id, n])), [map.nodes]);
  useEffect(() => { if (tool !== "link") setPending(null); }, [tool]);

  const fit = useCallback(() => {
    const el = wrap.current;
    if (!el) return;
    const W = el.clientWidth, H = el.clientHeight;
    if (!map.nodes.length) return setView({ tx: W / 2, ty: H / 2, k: 1 });
    const xs = map.nodes.flatMap(n => (n.w ? [n.x, n.x + n.w] : [n.x])), ys = map.nodes.flatMap(n => (n.h ? [n.y, n.y + n.h] : [n.y]));
    const minX = Math.min(...xs) - 120, maxX = Math.max(...xs) + 120, minY = Math.min(...ys) - 90, maxY = Math.max(...ys) + 110;
    const k = Math.max(0.25, Math.min(1.6, Math.min(W / (maxX - minX), H / (maxY - minY))));
    setView({ k, tx: W / 2 - ((minX + maxX) / 2) * k, ty: H / 2 - ((minY + maxY) / 2) * k });
  }, [map.nodes]);
  useEffect(() => { fit(); }, [fitSignal, map.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const world = (e) => { const r = svg.current.getBoundingClientRect(); return { x: (e.clientX - r.left - view.tx) / view.k, y: (e.clientY - r.top - view.ty) / view.k }; };
  const zoomAt = (mx, my, f) => setView(v => { const k = Math.max(0.2, Math.min(3, v.k * f)); return { k, tx: mx - ((mx - v.tx) / v.k) * k, ty: my - ((my - v.ty) / v.k) * k }; });
  useEffect(() => {
    const el = svg.current;
    const wheel = (e) => { e.preventDefault(); const r = el.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.12 : 1 / 1.12); };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  });

  const down = (e, n) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    if (editing && tool === "link" && n) {
      if (n.kind === "note" || n.kind === "shape" || n.kind === "label") return;
      if (!pending) setPending(n.id); else if (pending !== n.id) { onConnect(pending, n.id); setPending(null); }
      return;
    }
    if (n) { const w = world(e); setDrag({ kind: "node", id: n.id, ox: w.x - n.x, oy: w.y - n.y, moved: false }); }
    else setDrag({ kind: "pan", sx: e.clientX, sy: e.clientY, tx: view.tx, ty: view.ty, moved: false });
  };
  const move = (e) => {
    if (!drag) return;
    if (drag.kind === "pinch") return;
    if (drag.kind === "pan") {
      setView(v => ({ ...v, tx: drag.tx + e.clientX - drag.sx, ty: drag.ty + e.clientY - drag.sy }));
      if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) drag.moved = true;
    } else if (drag.kind === "resize" && editing) {
      const w = world(e); drag.moved = true;
      onPatchNode?.(drag.id, { w: Math.max(40, Math.round((w.x - drag.x0) / 10) * 10), h: Math.max(24, Math.round((w.y - drag.y0) / 10) * 10) });
    } else if (drag.kind === "vertex" && editing) {
      const w = world(e); drag.moved = true;
      const pt = { x: Math.round(w.x / 5) * 5, y: Math.round(w.y / 5) * 5 };
      const next = [...drag.base];
      if (drag.insert) next.splice(drag.idx, 0, pt); else next[drag.idx] = pt;
      onLinkPoints?.(drag.id, next);
    } else if (editing) {
      const w = world(e); drag.moved = true;
      onMove(drag.id, Math.round((w.x - drag.ox) / 10) * 10, Math.round((w.y - drag.oy) / 10) * 10);
    } else if (Math.abs(e.movementX) + Math.abs(e.movementY) > 2) drag.moved = true;
  };
  const lastClick = useRef({ id: null, t: 0 });
  // dois dedos (celular/tablet): pinça para zoom, e arrastar com os dois move o mapa
  const touches = useRef(new Map());
  const pinchOf = () => {
    const [a, b] = [...touches.current.values()];
    return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
  };
  const tDown = (e) => {
    if (e.pointerType !== "touch") return;
    const r = wrap.current.getBoundingClientRect();
    touches.current.set(e.pointerId, { x: e.clientX - r.left, y: e.clientY - r.top });
    if (touches.current.size === 2) {
      e.stopPropagation();
      const p = pinchOf();
      setDrag({ kind: "pinch", d0: p.d, mx0: p.mx, my0: p.my, v0: view, moved: true });
    }
  };
  const tMove = (e) => {
    if (e.pointerType !== "touch" || !touches.current.has(e.pointerId)) return;
    const r = wrap.current.getBoundingClientRect();
    touches.current.set(e.pointerId, { x: e.clientX - r.left, y: e.clientY - r.top });
    if (drag?.kind === "pinch" && touches.current.size >= 2) {
      e.stopPropagation();
      const p = pinchOf(), v0 = drag.v0;
      const k = Math.max(0.2, Math.min(3, v0.k * (p.d / drag.d0)));
      const wx = (drag.mx0 - v0.tx) / v0.k, wy = (drag.my0 - v0.ty) / v0.k;   // ponto do mapa que estava entre os dedos
      setView({ k, tx: p.mx - wx * k, ty: p.my - wy * k });
    }
  };
  const tUp = (e) => {
    if (e.pointerType !== "touch") return;
    touches.current.delete(e.pointerId);
    if (drag?.kind === "pinch") { e.stopPropagation(); if (touches.current.size < 2) setDrag(null); }
  };
  // ponto do traçado da ligação: arrastar move; insert = cria um ponto novo na posição idx
  const startVertex = (e, l, idx, insert) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    setDrag({ kind: "vertex", id: l.id, idx, insert, base: (l.points || []).map(p => ({ ...p })), moved: false });
  };
  const up = () => {
    if (drag?.kind === "resize") { setDrag(null); return; }
    if (drag?.kind === "vertex") {
      if (!drag.moved) onSelect({ link: drag.id });
      setDrag(null);
      return;
    }
    if (drag?.kind === "node" && !drag.moved) {
      const n = byId[drag.id], now = Date.now();
      if (n?.kind === "submap" && lastClick.current.id === n.id && now - lastClick.current.t < 450 && !(editing && tool === "link")) {
        lastClick.current = { id: null, t: 0 }; setDrag(null); return onEnter(n.map_id);
      }
      lastClick.current = { id: drag.id, t: now };
      onSelect({ id: drag.id });
    }
    if (drag?.kind === "pan" && !drag.moved) onSelect(null);
    setDrag(null);
  };

  const st = (n) => live?.nodes?.[n.id];
  const stateOf = (n) => (n.kind === "device" || n.kind === "submap" ? st(n)?.state || "pending" : null);
  const radius = (n) => (n.kind === "submap" ? (R_SUB + 6) * K : n.kind === "device" ? (devShape === "dot" ? 12 : devShape === "card" ? 26 : R_DEV + 4) * K : 0);
  // várias ligações entre os mesmos dois itens (rotas): abrem em leque
  const fan = useMemo(() => {
    const g = {}, out = {};
    map.links.forEach(l => { const k = [l.from, l.to].sort().join("|"); (g[k] = g[k] || []).push(l); });
    Object.entries(g).forEach(([k, ls]) => {
      const first = k.split("|")[0];
      ls.forEach((l, i) => { const b = (i - (ls.length - 1) / 2) * 30; out[l.id] = { bend: l.from === first ? b : -b }; });
    });
    return out;
  }, [map.links]);
  const bg = sty.bg || map.bg || "grid";

  return (
    <div ref={wrap} className="relative w-full h-full overflow-hidden select-none" style={{ background: `radial-gradient(ellipse at 50% 40%, ${C.bg1} 0%, ${C.bg0} 70%)`, touchAction: "none" }}
         onPointerMove={move} onPointerUp={up} onPointerLeave={(e) => { if (e.pointerType !== "touch") setDrag(null); }}
         onPointerDownCapture={tDown} onPointerMoveCapture={tMove} onPointerUpCapture={tUp} onPointerCancelCapture={(e) => { tUp(e); setDrag(null); }}
         data-testid="avail-canvas">
      <style>{CSS}</style>
      <svg ref={svg} className={`w-full h-full ${drag?.kind === "pan" ? "cursor-grabbing" : "cursor-grab"}`} onPointerDown={(e) => down(e, null)}>
        <defs>
          <pattern id="av-grid" width={48 * view.k} height={48 * view.k} patternUnits="userSpaceOnUse" x={view.tx} y={view.ty}>
            <path d={`M ${48 * view.k} 0 L 0 0 0 ${48 * view.k}`} fill="none" stroke={C.grid} strokeWidth="1" />
          </pattern>
          <radialGradient id="av-sweep-g" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="scale(900)">
            <stop offset="0" stopColor={C.up} stopOpacity=".16" /><stop offset="1" stopColor={C.up} stopOpacity="0" />
          </radialGradient>
          <filter id="av-glow" x="-1" y="-1" width="3" height="3"><feGaussianBlur stdDeviation="5" /></filter>
        </defs>
        {bg === "grid" && <rect width="100%" height="100%" fill="url(#av-grid)" />}
        {bg === "dots" && <>
          <defs><pattern id="av-dots" width={28 * view.k} height={28 * view.k} patternUnits="userSpaceOnUse" x={view.tx} y={view.ty}>
            <circle cx="1.5" cy="1.5" r={Math.max(0.8, 1.4 * Math.min(view.k, 1.5))} fill={C.line} /></pattern></defs>
          <rect width="100%" height="100%" fill="url(#av-dots)" />
        </>}
        {bg === "radar" && (
          <g transform={`translate(${wrap.current?.clientWidth / 2 || 0},${wrap.current?.clientHeight / 2 || 0})`} pointerEvents="none">
            {[120, 240, 360, 480, 600].map(r => <circle key={r} r={r} fill="none" stroke={C.grid} strokeWidth="1.2" />)}
            <path d="M -900 0 H 900 M 0 -900 V 900" stroke={C.grid} />
            {!reduced && <path className="av-sweep" d="M0,0 L900,0 A900,900 0 0,0 778,-452 Z" fill="url(#av-sweep-g)" />}
          </g>
        )}
        <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
          {/* formas e caixas de texto ficam atrás das linhas e dos equipamentos */}
          {map.nodes.filter(n => n.kind === "shape" || n.kind === "note").sort((a, b) => (a.kind === "note") - (b.kind === "note")).map(n => {
            const isSel = selected?.id === n.id;
            const w = n.w || 200, h = n.h || 80, fill = n.fill || (n.kind === "note" ? C.panel : "transparent");
            const shapeEl = n.shape === "ellipse" ? <ellipse cx={w / 2} cy={h / 2} rx={w / 2} ry={h / 2} />
              : <rect width={w} height={h} rx={n.shape === "rect" ? 0 : Math.min(16, h / 4)} />;
            return (
              <g key={n.id} transform={`translate(${n.x},${n.y})`} onPointerDown={(e) => down(e, n)} style={{ cursor: editing ? "move" : "default" }} data-testid={`av-node-${n.id}`}>
                {React.cloneElement(shapeEl, { fill: fill === "transparent" ? "transparent" : fill, fillOpacity: fill === "transparent" ? 0 : (n.opacity ?? 0.9),
                  stroke: n.stroke || C.line, strokeWidth: 1.6, strokeDasharray: n.dashed ? "8 6" : undefined })}
                {n.text && (
                  <foreignObject x="0" y="0" width={w} height={h} pointerEvents="none">
                    <div style={{ width: "100%", height: "100%", padding: n.kind === "note" ? "10px 12px" : "8px 12px", boxSizing: "border-box", color: n.color || C.ink,
                                  fontSize: n.size || 13, lineHeight: 1.35, whiteSpace: "pre-wrap", overflow: "hidden", fontFamily: "DM Sans, sans-serif",
                                  fontWeight: n.kind === "shape" ? 600 : 400 }}>{n.text}</div>
                  </foreignObject>
                )}
                {isSel && <rect x="-4" y="-4" width={w + 8} height={h + 8} fill="none" stroke="#fff" strokeOpacity=".7" strokeDasharray="4 3" />}
                {isSel && editing && (
                  <g transform={`translate(${w},${h})`} style={{ cursor: "nwse-resize" }} data-testid={`av-resize-${n.id}`}
                     onPointerDown={(e) => { e.stopPropagation(); setDrag({ kind: "resize", id: n.id, x0: n.x, y0: n.y, moved: false }); }}>
                    <circle r="12" fill="transparent" /><rect x="-6" y="-6" width="12" height="12" rx="2" fill="#fff" stroke={C.up} strokeWidth="2" />
                    <title>Arraste para mudar o tamanho</title>
                  </g>
                )}
              </g>
            );
          })}
          {map.links.map(l => {
            const a = byId[l.from], b = byId[l.to];
            if (!a || !b) return null;
            const par = fan[l.id] || { bend: 0 };
            const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
            const pts = l.points || [];
            if (!pts.length && d < radius(a) + radius(b) + 4) return null;
            const toward = (p, q, r) => { const L = Math.hypot(q.x - p.x, q.y - p.y) || 1; return { x: p.x + ((q.x - p.x) / L) * r, y: p.y + ((q.y - p.y) / L) * r }; };
            let dense, ghosts = [];
            if (pts.length) {                         // traçado livre: passa pelos pontos (curvo ou em segmentos retos)
              const anchors = [{ x: a.x, y: a.y }, ...pts, { x: b.x, y: b.y }];
              anchors[0] = toward(a, pts[0], radius(a));
              anchors[anchors.length - 1] = toward(b, pts[pts.length - 1], radius(b));
              const smooth = l.shape !== "straight";
              dense = smooth ? catmull(anchors) : anchors;
              ghosts = anchors.slice(0, -1).map((p, i) => (smooth ? crPt(anchors[i - 1] || p, p, anchors[i + 1], anchors[i + 2] || anchors[i + 1], 0.5)
                : { x: (p.x + anchors[i + 1].x) / 2, y: (p.y + anchors[i + 1].y) / 2 }));
            } else {
              const nx = -dy / d, ny = dx / d;
              const cp = { x: (a.x + b.x) / 2 + nx * par.bend * 2, y: (a.y + b.y) / 2 + ny * par.bend * 2 };
              const p1 = toward(a, cp, radius(a)), p2 = toward(b, cp, radius(b));
              dense = Array.from({ length: 25 }, (_, i) => { const t = i / 24, u = 1 - t; return { x: u * u * p1.x + 2 * u * t * cp.x + t * t * p2.x, y: u * u * p1.y + 2 * u * t * cp.y + t * t * p2.y }; });
            }
            const path = "M" + dense.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" L");
            const mid = halfway(dense);
            const ls = l.snmp ? live?.links?.[l.id] : null;
            let col, ok, bad, dashed;
            if (l.snmp) {                             // rota com interface: a cor é o estado da interface
              const st = ls?.state || "unknown";
              ok = st === "up"; bad = st === "down"; dashed = !ok;
              col = ok ? C.up : bad ? C.down : C.pending;
            } else {
              const sa = stateOf(a), sb = stateOf(b);
              bad = sa === "down" || sb === "down";
              const warn = sa === "warn" || sb === "warn";
              ok = !bad && !warn && sa !== "pending" && sb !== "pending";
              col = bad ? C.down : warn ? C.warn : ok ? C.up : C.pending; dashed = bad;
            }
            if (!bad && l.color) col = l.color;          // cor escolhida na linha; caída continua vermelha
            if (!dashed && l.dash && l.dash !== "solid") dashed = l.dash;
            const w = l.width > 0 ? l.width : lineAuto && ls?.speed_mbps ? capWidth(lineW, ls.speed_mbps) : lineW;
            const dashArr = dashed === "dotted" ? `0.1 ${w * 2.6}` : dashed ? `${w * 3} ${w * 2.4}` : undefined;
            // tráfego (SNMP): bolinhas nos dois sentidos, mais rápidas e em maior número quanto maior o uso
            const flows = [];
            if (l.snmp && traffic && ok && !reduced && ls && map.links.length < 400) {
              const fromIsDev = a.kind === "device" && a.device_id === l.snmp.device_id, toIsDev = b.kind === "device" && b.device_id === l.snmp.device_id;
              const fwd = toIsDev && !fromIsDev ? ls.in_bps : ls.out_bps, back = toIsDev && !fromIsDev ? ls.out_bps : ls.in_bps;
              const cap = (ls.speed_mbps || 1000) * 1e6;
              [[fwd, false], [back, true]].forEach(([bps, rev]) => {
                if (!(bps > 1000)) return;
                const u = Math.min(1, bps / cap);
                const pxs = 50 + 330 * Math.sqrt(u);
                flows.push({ rev, dur: Math.max(0.6, mid.len / pxs), n: u > 0.6 ? 4 : u > 0.2 ? 3 : 2 });
              });
            }
            const isSel = selected?.link === l.id;
            const chip = l.snmp ? (ls?.state === "down" ? "DOWN" : ls?.speed_mbps ? fmtSpeed(ls.speed_mbps) : ls?.state === "up" ? "UP" : "?") : null;
            const dur = Math.max(1.6, mid.len / 140);
            return (
              <g key={l.id} data-testid={`av-link-${l.id}`}>
                {isSel && <path d={path} fill="none" stroke="#fff" strokeOpacity=".3" strokeWidth={w + 8} />}
                {bad && <path d={path} fill="none" stroke={C.down} strokeOpacity=".35" strokeWidth={w + 6} filter="url(#av-glow)" />}
                <path d={path} fill="none" stroke={col} strokeOpacity={l.snmp || l.color ? 0.95 : ok ? 0.45 : 0.85} strokeWidth={bad ? w + 0.4 : w} strokeDasharray={dashArr} strokeLinecap="round" />
                {flows.map(f => Array.from({ length: f.n }, (_, i) => (
                  <circle key={`${f.rev}-${i}`} r={Math.max(2.2, w * 0.85)} fill={f.rev ? C.ink : col} stroke={C.bg0} strokeWidth="0.8">
                    <animateMotion dur={`${f.dur}s`} begin={`${(i / f.n) * f.dur}s`} repeatCount="indefinite" path={path}
                                   {...(f.rev ? { keyPoints: "1;0", keyTimes: "0;1", calcMode: "linear" } : {})} />
                  </circle>
                )))}
                {!l.snmp && ok && flow && !reduced && map.links.length < 400 && [0, 0.5].map(o => (
                  <circle key={o} r={Math.max(2.2, w * 0.9)} fill={col}>
                    <animateMotion dur={`${dur}s`} begin={`${o * dur}s`} repeatCount="indefinite" path={path} />
                  </circle>
                ))}
                <path d={path} fill="none" stroke="transparent" strokeWidth="14" style={{ cursor: "pointer" }} onPointerDown={(e) => { e.stopPropagation(); onSelect({ link: l.id }); }} />
                {chip && (
                  <g transform={`translate(${mid.x},${mid.y})`} pointerEvents="none">
                    <rect x={-(chip.length * 3.6 + 8)} y="-9" width={chip.length * 7.2 + 16} height="18" rx="9" fill={C.bg0} stroke={col} strokeWidth="1.4" />
                    <text y="4" textAnchor="middle" fontSize="10.5" fontWeight="700" fill={bad ? C.down : C.ink} fontFamily="JetBrains Mono, monospace">{chip}</text>
                  </g>
                )}
                {l.label && <text x={mid.x} y={mid.y - (chip ? 15 : 6)} textAnchor="middle" fontSize="10" fill={C.dim} stroke={C.bg0} strokeWidth="3.5" paintOrder="stroke">{l.label}</text>}
                {editing && tool === "move" && !pts.length && isSel && (
                  <g transform={`translate(${mid.x},${mid.y + (chip ? 18 : 0)})`} style={{ cursor: "grab" }} onPointerDown={(e) => startVertex(e, l, 0, true)} data-testid={`av-link-handle-${l.id}`}>
                    <circle r="14" fill="transparent" />
                    <circle r="6" fill="#fff" stroke={C.up} strokeWidth="2.5" />
                    <title>Arraste para curvar ou desviar a linha</title>
                  </g>
                )}
                {editing && tool === "move" && pts.map((p, i) => (
                  <g key={`v${i}`} transform={`translate(${p.x},${p.y})`} style={{ cursor: "move" }} data-testid={`av-link-pt-${l.id}-${i}`}
                     onPointerDown={(e) => startVertex(e, l, i, false)} onDoubleClick={(e) => { e.stopPropagation(); onLinkPoints?.(l.id, pts.filter((_, j) => j !== i)); }}>
                    <circle r="12" fill="transparent" />
                    <rect x="-5" y="-5" width="10" height="10" rx="2" fill="#fff" stroke={C.up} strokeWidth="2.2" />
                    <title>Arraste para mover · dois cliques apagam o ponto</title>
                  </g>
                ))}
                {editing && tool === "move" && isSel && ghosts.map((p, i) => (
                  <g key={`g${i}`} transform={`translate(${p.x},${p.y})`} style={{ cursor: "copy" }} onPointerDown={(e) => startVertex(e, l, i, true)}>
                    <circle r="11" fill="transparent" />
                    <circle r="5.5" fill={C.up} fillOpacity=".55" stroke="#fff" strokeWidth="1.3" />
                    <path d="M-2.6,0 H2.6 M0,-2.6 V2.6" stroke="#fff" strokeWidth="1.4" />
                    <title>Arraste para criar mais um ponto aqui</title>
                  </g>
                ))}
              </g>
            );
          })}

          {map.nodes.filter(n => n.kind !== "shape" && n.kind !== "note").map(n => {
            const s = st(n) || {};
            const state = stateOf(n);
            const col = C[state] || C.pending;
            const isSel = selected?.id === n.id, isPend = pending === n.id;
            const cursor = editing ? (tool === "link" ? "crosshair" : "move") : "pointer";
            if (n.kind === "label") {
              return (
                <g key={n.id} transform={`translate(${n.x},${n.y})`} onPointerDown={(e) => down(e, n)} style={{ cursor }} data-testid={`av-node-${n.id}`}>
                  {isSel && <rect x={-90} y={-16} width="180" height="26" rx="4" fill="none" stroke={C.up} strokeDasharray="4 3" />}
                  <text textAnchor="middle" y="2" fontSize="15" fontWeight="600" letterSpacing=".06em" fill={C.dim} stroke={C.bg0} strokeWidth="4" strokeLinejoin="round" paintOrder="stroke">{n.label}</text>
                </g>
              );
            }
            if (n.kind === "submap") {
              const R = R_SUB;
              const outline = (r) => (subShape === "circle" ? <circle r={r} /> : subShape === "square" ? <rect x={-r} y={-r} width={r * 2} height={r * 2} rx={r * 0.22} />
                : subShape === "diamond" ? <path d={`M0,${-r * 1.2} L${r * 1.2},0 L0,${r * 1.2} L${-r * 1.2},0 Z`} /> : <path d={hexPath(r)} />);
              return (
                <g key={n.id} transform={`translate(${n.x},${n.y}) scale(${K})`} onPointerDown={(e) => down(e, n)}
                   style={{ cursor }} data-testid={`av-node-${n.id}`}>
                  {state === "down" && <circle r={R + 14} fill={C.down} opacity=".18" filter="url(#av-glow)" />}
                  {state === "down" && !reduced && <circle className="av-shock" r={R} fill="none" stroke={C.down} strokeWidth="2" />}
                  <Ring s={s} r={(subShape === "diamond" ? R * 1.25 : R) + 7} C={C} />
                  {React.cloneElement(outline(R), { fill: C.panel, stroke: isPend ? C.warn : isSel ? "#fff" : col, strokeOpacity: isSel || isPend ? 1 : 0.7, strokeWidth: isSel || isPend ? 2.4 : 1.6 })}
                  {React.cloneElement(outline(R - 7), { fill: "none", stroke: col, strokeOpacity: 0.25 })}
                  <text y="2" textAnchor="middle" fontSize="17" fontWeight="700" fill={C.ink} fontFamily="JetBrains Mono, monospace">{s.total ?? "–"}</text>
                  <text y="15" textAnchor="middle" fontSize="8.5" fill={C.dim}>{s.down ? <tspan fill={C.down}>{s.down} fora</tspan> : s.links_down ? <tspan fill={C.down}>{s.links_down} rota fora</tspan> : "equip."}</text>
                  <text y={(subShape === "diamond" ? R * 1.25 : R) + 26} textAnchor="middle" fontSize="12.5" fontWeight="600" fill={C.ink} stroke={C.bg0} strokeWidth="4" strokeLinejoin="round" paintOrder="stroke">{n.label || s.name || names[n.map_id] || "submapa"}</text>
                  <title>{`${s.name || ""}\n${s.up || 0} respondendo · ${s.warn || 0} instável · ${s.down || 0} sem resposta\nDois cliques para abrir`}</title>
                </g>
              );
            }
            const nm = (n.label || s.name || names[n.device_id] || "").slice(0, 26);
            const sub = state === "ignored" ? ignoredTxt(s.ignored_until) : state === "down" ? `sem resposta · ${ago(s.since)}` : state === "pending" ? "aguardando" : s.ms != null ? `${s.ms < 10 ? s.ms.toFixed(1) : Math.round(s.ms)} ms${s.loss ? ` · ${Math.round(s.loss)}% perda` : ""}` : "";
            if (devShape === "card") {                 // cartão com nome dentro (estilo Dude, mais moderno)
              const W = Math.max(120, nm.length * 7.8 + 50), H = 40;
              return (
                <g key={n.id} transform={`translate(${n.x},${n.y}) scale(${K})`} onPointerDown={(e) => down(e, n)} style={{ cursor }} opacity={state === "ignored" ? 0.5 : 1} data-testid={`av-node-${n.id}`}>
                  {state === "down" && <rect x={-W / 2 - 6} y={-H / 2 - 6} width={W + 12} height={H + 12} rx="12" fill={C.down} opacity=".25" filter="url(#av-glow)" />}
                  <rect x={-W / 2} y={-H / 2} width={W} height={H} rx="9" fill={C.panel} stroke={isPend ? C.warn : isSel ? "#fff" : col} strokeWidth={isSel || isPend ? 2.6 : 1.8} />
                  <rect x={-W / 2} y={-H / 2} width="5" height={H} rx="2" fill={col} />
                  <g transform={`translate(${-W / 2 + 19},0)`}><Glyph type={s.type} color={col} /></g>
                  <text x={-W / 2 + 34} y="-3" fontSize="12" fontWeight="600" fill={C.ink}>{nm}</text>
                  <text x={-W / 2 + 34} y="12" fontSize="9.5" fill={state === "down" ? C.down : C.dim} fontFamily="JetBrains Mono, monospace">{sub}</text>
                  <title>{`${s.name || ""}\n${s.host || ""}\n${STATE_TXT[state]}`}</title>
                </g>
              );
            }
            const r = devShape === "dot" ? 9 : R_DEV;
            const body = devShape === "square" ? <rect x={-r} y={-r} width={r * 2} height={r * 2} rx="7" />
              : devShape === "diamond" ? <path d={`M0,${-r * 1.25} L${r * 1.25},0 L0,${r * 1.25} L${-r * 1.25},0 Z`} /> : <circle r={r} />;
            return (
              <g key={n.id} transform={`translate(${n.x},${n.y}) scale(${K})`} onPointerDown={(e) => down(e, n)} style={{ cursor }} opacity={state === "ignored" ? 0.5 : 1} data-testid={`av-node-${n.id}`}>
                {state === "down" && <circle r={r + 10} fill={C.down} opacity=".22" filter="url(#av-glow)" />}
                {state === "down" && !reduced && <circle className="av-shock" r={r} fill="none" stroke={C.down} strokeWidth="2" />}
                {state !== "down" && devShape !== "dot" && <circle r={r + 6} fill={col} opacity={state === "pending" ? 0.05 : 0.1} />}
                {React.cloneElement(body, devShape === "dot" ? { fill: col, stroke: isPend ? C.warn : isSel ? "#fff" : C.panel, strokeWidth: 2.5 }
                  : { fill: C.panel, stroke: isPend ? C.warn : col, strokeWidth: isSel || isPend ? 3 : 2.2, strokeDasharray: state === "ignored" ? "4 3" : undefined })}
                {isSel && <circle r={r + 6} fill="none" stroke="#fff" strokeOpacity=".7" strokeWidth="1.2" strokeDasharray="3 3" />}
                {devShape !== "dot" && <Glyph type={s.type} color={col} />}
                <text y={r * (devShape === "diamond" ? 1.25 : 1) + 17} textAnchor="middle" fontSize="12" fontWeight="600" fill={C.ink} stroke={C.bg0} strokeWidth="4" strokeLinejoin="round" paintOrder="stroke">{nm}</text>
                <text y={r * (devShape === "diamond" ? 1.25 : 1) + 31} textAnchor="middle" fontSize="10" fill={state === "down" ? C.down : C.dim} fontFamily="JetBrains Mono, monospace" stroke={C.bg0} strokeWidth="4" strokeLinejoin="round" paintOrder="stroke">
                  {sub}
                </text>
                <title>{`${s.name || ""}\n${s.host || ""}\n${STATE_TXT[state]}`}</title>
              </g>
            );
          })}
        </g>
      </svg>
      {editing && tool === "link" && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 text-xs px-3 py-1.5 rounded-md shadow-lg" style={{ background: C.panel, color: C.warn, border: `1px solid ${C.warn}55` }}>
          {pending ? "Agora clique no segundo item" : "Clique no primeiro item da ligação"}
        </div>
      )}
      {!map.nodes.length && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="text-center max-w-sm" style={{ color: C.dim }}>
            <Hexagon className="w-10 h-10 mx-auto mb-3 opacity-60" />
            <div className="text-sm">{editing ? "Use Equipamentos e Submapa na barra acima para montar este mapa." : "Mapa vazio. Clique em Editar para colocar equipamentos e submapas."}</div>
          </div>
        </div>
      )}
      <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded px-2 py-1 text-xs" style={{ background: `${C.panel}e6`, border: `1px solid ${C.line}`, color: C.dim }}>
        <button className="px-1.5 hover:text-white" onClick={() => zoomAt((wrap.current?.clientWidth || 0) / 2, (wrap.current?.clientHeight || 0) / 2, 1.25)} aria-label="Aproximar">+</button>
        <span className="w-9 text-center font-mono text-[10px]">{Math.round(view.k * 100)}%</span>
        <button className="px-1.5 hover:text-white" onClick={() => zoomAt((wrap.current?.clientWidth || 0) / 2, (wrap.current?.clientHeight || 0) / 2, 0.8)} aria-label="Afastar">−</button>
        <button className="px-1.5 border-l ml-1 hover:text-white" style={{ borderColor: C.line }} onClick={fit}>ajustar</button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ painel lateral de incidentes (sempre visível)
export function AlarmPanel({ live, mapId, onPick, onClose }) {
  const alarms = live?.alarms || [];
  const ignored = live?.ignored || [];
  const [showIgn, setShowIgn] = useState(false);
  const down = alarms.filter(a => a.state === "down").length;
  const spd = (m) => (!m ? "" : m >= 1000 ? `${+(m / 1000).toFixed(1)}G` : `${m}M`);
  return (
    <aside className="w-80 shrink-0 flex flex-col min-h-0 border-l" style={{ background: C.panel, borderColor: C.line }} data-testid="av-alarms">
      <div className="flex items-center gap-2 px-4 py-3 border-b" style={{ borderColor: C.line }}>
        <span className="text-sm font-semibold" style={{ color: C.ink }}>Incidentes</span>
        <span className="min-w-[1.5rem] h-6 px-1.5 rounded-full flex items-center justify-center text-xs font-bold font-mono"
              style={{ background: down ? C.down : alarms.length ? C.warn : `${C.up}33`, color: alarms.length ? "#fff" : C.up }}>{alarms.length}</span>
        {onClose && <button onClick={onClose} className="ml-auto p-1" style={{ color: C.dim }} aria-label="Esconder incidentes"><X className="w-4 h-4" /></button>}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {!live ? <div className="p-4 text-xs" style={{ color: C.dim }}>Carregando…</div> : !alarms.length ? (
          <div className="px-4 py-6 text-center">
            <div className="text-2xl" style={{ color: C.up }}>✓</div>
            <div className="text-sm mt-1" style={{ color: C.ink }}>Tudo respondendo</div>
            <div className="text-xs" style={{ color: C.dim }}>nenhum equipamento sem resposta, com perda de pacote ou porta DOWN</div>
          </div>
        ) : alarms.map((a, i) => {
          const col = C[a.state] || C.down;
          const sub = [a.kind === "link" ? a.device_name : a.host, a.map_id !== mapId ? `em ${a.map_name}` : ""].filter(Boolean).join(" · ");
          return (
            <button key={i} onClick={() => onPick?.(a)} className="w-full text-left flex gap-3 px-4 py-2.5 border-b hover:bg-white/[0.04]" style={{ borderColor: `${C.line}99` }}
                    data-testid={`av-alarm-${i}`}>
              <span className="w-1 self-stretch rounded-full shrink-0" style={{ background: col, boxShadow: a.state === "down" ? `0 0 8px ${col}` : "none" }} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="text-[13px] font-semibold truncate" style={{ color: C.ink }}>{a.name}</span>
                  <span className="ml-auto text-[11px] font-mono shrink-0" style={{ color: col }}>{ago(a.since)}</span>
                </span>
                <span className="block text-xs" style={{ color: col }}>
                  {a.kind === "link" ? `porta DOWN · ${a.if_name}${a.speed_mbps ? ` · ${spd(a.speed_mbps)}` : ""}${a.admin === "down" ? " · shutdown" : ""}`
                    : a.state === "down" ? "sem resposta ao ping" : `${a.loss ? `perda de pacote · ${Math.round(a.loss)}%` : "instável"}${a.ms != null ? ` · ${Math.round(a.ms)} ms` : ""}`}
                </span>
                {sub && <span className="block text-[11px] truncate" style={{ color: C.dim }}>{sub}</span>}
              </span>
            </button>
          );
        })}
        {ignored.length > 0 && (
          <div className="border-t" style={{ borderColor: C.line }} data-testid="av-ignored">
            <button onClick={() => setShowIgn(v => !v)} className="w-full flex items-center gap-2 px-4 py-2 text-xs" style={{ color: C.dim }} aria-expanded={showIgn}>
              {showIgn ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              Ignorados ({ignored.length}) <span className="ml-auto text-[10px]">sem ping, sem alarme</span>
            </button>
            {showIgn && ignored.map(g => (
              <button key={g.device_id} onClick={() => onPick?.({ kind: "device", ...g })} className="w-full text-left px-4 py-1.5 hover:bg-white/[0.04]">
                <span className="block text-[13px] truncate" style={{ color: C.ink, opacity: 0.75 }}>{g.name}</span>
                <span className="block text-[11px] truncate" style={{ color: C.dim }}>{[ignoredTxt(g.until), g.reason, g.map_id !== mapId ? `em ${g.map_name}` : ""].filter(Boolean).join(" · ")}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

// ------------------------------------------------------------------ ignorar equipamento (eventos, desligado de propósito)
const IGN_OPTS = [[60, "1 hora"], [240, "4 horas"], [720, "12 horas"], [1440, "24 horas"], [10080, "7 dias"], [0, "até eu reativar"]];
function IgnoreBox({ deviceId, live, canEdit, onDone }) {
  const [mins, setMins] = useState(240);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const ignored = live?.state === "ignored";
  const run = async (fn, msg) => {
    setBusy(true);
    try { await fn(); toast.success(msg); onDone?.(); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  if (ignored) {
    return (
      <div className="rounded-md border border-line p-2.5 space-y-2 text-xs" data-testid="av-ignore">
        <div className="text-slate-200">{ignoredTxt(live.ignored_until)}{live.ignored_reason ? ` · ${live.ignored_reason}` : ""}</div>
        <div className="text-[11px] text-slate-500">Sem ping, sem alarme, fora dos incidentes e da contagem do mapa.{live.ignored_by ? ` Por ${live.ignored_by}.` : ""}</div>
        {canEdit && <Button size="sm" disabled={busy} onClick={() => run(() => api.delete(`/avail/ignore/${deviceId}`), "Voltou a ser monitorado")}
                            className="w-full h-8 bg-brand hover:bg-brand-strong text-xs" data-testid="av-unignore">Voltar a monitorar agora</Button>}
      </div>
    );
  }
  if (!canEdit) return null;
  return (
    <details className="rounded-md border border-line text-xs group" data-testid="av-ignore">
      <summary className="px-2.5 py-2 cursor-pointer text-slate-300 select-none">Ignorar este equipamento…</summary>
      <div className="px-2.5 pb-2.5 space-y-2">
        <div className="text-[11px] text-slate-500">Para equipamento de evento ou desligado de propósito: para de pingar, não alarma e não acende o mapa.</div>
        <div className="grid grid-cols-3 gap-1">
          {IGN_OPTS.map(([m, l]) => (
            <button key={m} onClick={() => setMins(m)} className={`px-1.5 py-1 rounded border text-[11px] ${mins === m ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400"}`}>{l}</button>))}
        </div>
        <Input value={reason} onChange={e => setReason(e.target.value)} maxLength={120} placeholder="motivo (opcional), ex.: evento" className="h-8 bg-sunken border-line text-xs" />
        <Button size="sm" disabled={busy} onClick={() => run(() => api.post("/avail/ignore", { device_id: deviceId, minutes: mins, reason }), mins ? "Ignorado por um tempo" : "Ignorado até reativar")}
                variant="outline" className="w-full h-8 border-line bg-transparent text-xs" data-testid="av-ignore-go">Ignorar {IGN_OPTS.find(o => o[0] === mins)?.[1]}</Button>
      </div>
    </details>
  );
}

// ------------------------------------------------------------------ painel da ligação (rota)
function LinkPanel({ link, map, live, devices, names, editing, onChange }) {
  const nodeName = (id) => { const n = map.nodes.find(x => x.id === id); return n ? n.label || names[n.device_id] || names[n.map_id] || "?" : "?"; };
  const ends = map.nodes.filter(n => (n.id === link.from || n.id === link.to) && n.kind === "device").map(n => n.device_id);
  const [devId, setDevId] = useState(link.snmp?.device_id || ends[0] || "");
  useEffect(() => { setDevId(link.snmp?.device_id || ends[0] || ""); }, [link.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const st = live?.state || (link.snmp ? "unknown" : null);
  const stCol = st === "up" ? C.up : st === "down" ? C.down : C.pending;
  return (
    <div className="space-y-3" data-testid="av-link-panel">
      <div>
        <div className="text-[11px]" style={{ color: C.dim }}>Ligação</div>
        <div className="text-sm font-semibold text-slate-100 break-words">{nodeName(link.from)} ↔ {nodeName(link.to)}</div>
        {link.label && !editing && <div className="text-xs text-slate-300">{link.label}</div>}
      </div>
      {link.snmp ? (
        <div className="rounded-md p-2.5 space-y-1" style={{ border: `1px solid ${stCol}66`, background: `${stCol}12` }}>
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold font-mono" style={{ color: stCol }}>{st === "up" ? "UP" : st === "down" ? "DOWN" : "sem leitura"}</span>
            {live?.speed_mbps ? <span className="ml-auto font-mono text-sm text-slate-100">{fmtSpeed(live.speed_mbps)}</span> : null}
          </div>
          <div className="text-xs text-slate-300 break-words">{live?.device_name || names[link.snmp.device_id]} · <span className="font-mono">{link.snmp.if_name || `if${link.snmp.if_index}`}</span></div>
          {st === "up" && (live?.in_bps != null || live?.out_bps != null) && (
            <div className="space-y-0.5 pt-1 text-xs" data-testid="av-link-traffic">
              <div className="flex text-slate-300">↓ entrada<b className="ml-auto font-mono text-slate-100">{fmtBps(live.in_bps)}</b></div>
              <div className="flex text-slate-300">↑ saída<b className="ml-auto font-mono text-slate-100">{fmtBps(live.out_bps)}</b></div>
              {live.speed_mbps > 0 && <div className="text-[11px]" style={{ color: C.dim }}>uso {Math.round(100 * Math.max(live.in_bps || 0, live.out_bps || 0) / (live.speed_mbps * 1e6))}% da capacidade</div>}
            </div>
          )}
          {live?.since && <div className="text-[11px]" style={{ color: C.dim }}>Neste estado há {ago(live.since)}{live.admin === "down" ? " · desligada (shutdown)" : ""}</div>}
          {live?.error && <div className="text-[11px]" style={{ color: C.warn }}>{live.error}</div>}
          {!live && <div className="text-[11px]" style={{ color: C.dim }}>Salve o mapa: a interface é lida no próximo ciclo.</div>}
        </div>
      ) : <div className="text-xs" style={{ color: C.dim }}>Sem interface escolhida: a cor segue o ping dos dois lados. Escolha uma interface para a linha mostrar UP/DOWN e a capacidade.</div>}
      {editing && <>
        <div className="space-y-1">
          <div className="text-xs text-slate-300">Traçado</div>
          {(link.points || []).length ? <>
            <div className="flex gap-1">
              {[["smooth", "curvo"], ["straight", "retas"]].map(([k, l]) => (
                <button key={k} onClick={() => onChange({ shape: k })} className={`px-2 py-1 rounded border text-[11px] ${(link.shape || "smooth") === k ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400"}`}>{l}</button>))}
              <button onClick={() => onChange({ points: [] })} className="ml-auto text-[11px] text-slate-400 hover:text-slate-200">voltar a reta</button>
            </div>
            <div className="text-[11px]" style={{ color: C.dim }}>Arraste os quadrados; os + criam mais pontos; dois cliques num quadrado apagam.</div>
          </> : <div className="text-[11px]" style={{ color: C.dim }}>Arraste a bolinha branca no meio da linha para curvar ou desviar de outro item.</div>}
        </div>
        <div className="space-y-1.5" data-testid="av-link-look">
          <div className="text-xs text-slate-300">Aparência da linha</div>
          <div className="flex items-center gap-2 text-[11px] text-slate-400">
            <span className="w-14 shrink-0">Espessura</span>
            <input type="range" min="0" max="10" step="0.5" value={link.width || 0} onChange={e => onChange({ width: Number(e.target.value) })} className="flex-1" aria-label="Espessura da linha" />
            <span className="w-10 text-right font-mono">{link.width > 0 ? `${link.width}px` : "auto"}</span>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-slate-400">
            <span className="w-14 shrink-0">Cor</span>
            <input type="color" value={link.color || C.up} onChange={e => onChange({ color: e.target.value })} className="w-8 h-6 rounded border border-line bg-transparent cursor-pointer" aria-label="Cor da linha" />
            {link.color ? <button onClick={() => onChange({ color: "" })} className="hover:text-slate-200">usar a cor do estado</button> : <span>cor do estado (UP/instável)</span>}
          </div>
          <div className="flex gap-1">
            {[["solid", "contínua"], ["dashed", "tracejada"], ["dotted", "pontilhada"]].map(([k, l]) => (
              <button key={k} onClick={() => onChange({ dash: k })} className={`px-2 py-1 rounded border text-[11px] ${(link.dash || "solid") === k ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400"}`}>{l}</button>))}
          </div>
          <div className="text-[11px]" style={{ color: C.dim }}>Caída fica sempre vermelha tracejada. Automática: mais grossa quanto maior a capacidade (1G fina, 100G o dobro).</div>
        </div>
        <label className="block text-xs text-slate-400 space-y-1">Texto na linha (ex.: rota 1, fibra norte)
          <Input value={link.label || ""} onChange={e => onChange({ label: e.target.value })} className="h-8 bg-sunken border-line text-sm" /></label>
        <div className="space-y-1.5 pt-2 border-t" style={{ borderColor: C.line }}>
          <div className="text-xs text-slate-300">Interface monitorada por SNMP</div>
          <DeviceSelect value={devId} onChange={e => setDevId(e.target.value)} className="w-full h-8 rounded-md bg-sunken border border-line px-2 text-xs text-slate-100" aria-label="Equipamento da interface" devices={devices}>
            <option value="">escolha o equipamento…</option>
            {ends.length > 0 && <optgroup label="Pontas da ligação">{ends.map(id => <option key={id} value={id}>{names[id] || id}</option>)}</optgroup>}
          </DeviceSelect>
          {devId && <InterfacePicker deviceId={devId} mode="single" value={link.snmp?.device_id === devId ? link.snmp.if_index : undefined} height="h-56"
                                     onPick={(i) => onChange({ snmp: { device_id: devId, if_index: i.index, if_name: i.name } })} />}
          {link.snmp && <button onClick={() => onChange({ snmp: null })} className="text-[11px] text-red-300 hover:underline">tirar a interface desta linha</button>}
        </div>
      </>}
    </div>
  );
}

function ShareDlg({ map, onClose }) {
  const [rows, setRows] = useState(null);
  const [showHosts, setShowHosts] = useState(false);
  const load = () => api.get("/avail/shares", { params: { map_id: map.id } }).then(r => setRows(r.data)).catch(e => { toast.error(formatApiError(e)); setRows([]); });
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const link = (t) => `${window.location.origin}/disp/${t}`;
  const create = async () => { try { await api.post("/avail/shares", { map_id: map.id, name: map.name, show_hosts: showHosts }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const upd = async (r, body, msg) => { try { await api.put(`/avail/shares/${r.id}`, body); if (msg) toast.success(msg); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const del = async (r) => { if (!window.confirm("Apagar este link? Quem estiver com ele aberto para de ver o mapa.")) return; try { await api.delete(`/avail/shares/${r.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const copy = async (t) => { try { await navigator.clipboard.writeText(t); toast.success("Link copiado"); } catch { toast.error("Não consegui copiar"); } };
  const dl = (r) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([tvHtml(r.name, link(r.token))], { type: "text/html;charset=utf-8" }));
    a.download = `disponibilidade-${r.name.toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "mapa"}.html`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  return (
    <Modal title={`Link do mapa · ${map.name}`} onClose={onClose} wide>
      <p className="text-xs text-slate-400 mb-3">Abre este mapa e os submapas dele em qualquer navegador (PC, TV) <b className="text-slate-200">sem logar no BastiON</b>, só para ver, atualizando sozinho.</p>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={showHosts} onChange={e => setShowHosts(e.target.checked)} /> mostrar os IPs</label>
        <Button size="sm" onClick={create} className="ml-auto h-8 bg-brand hover:bg-brand-strong" data-testid="av-share-new"><Plus className="w-3.5 h-3.5 mr-1" />Gerar link</Button>
      </div>
      {!rows ? <Loader2 className="w-4 h-4 animate-spin text-slate-400" /> : !rows.length ? <div className="text-sm text-slate-500">Nenhum link ainda.</div> : rows.map(r => (
        <div key={r.id} className={`border border-line rounded-md p-3 mb-2 space-y-2 ${r.enabled ? "" : "opacity-60"}`}>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span>{r.last_seen ? `aberto ${new Date(r.last_seen).toLocaleString("pt-BR")}${r.last_ip ? ` de ${r.last_ip}` : ""}` : "nunca aberto"}</span>
            <label className="flex items-center gap-1 ml-auto"><input type="checkbox" checked={r.show_hosts} onChange={e => upd(r, { show_hosts: e.target.checked })} /> IPs</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={r.enabled} onChange={e => upd(r, { enabled: e.target.checked })} /> ativo</label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded bg-sunken border border-line px-2 py-1.5 text-xs text-slate-300" data-testid="av-share-link">{link(r.token)}</code>
            <Button size="sm" onClick={() => copy(link(r.token))} className="h-8 bg-brand hover:bg-brand-strong">Copiar</Button>
            <Button size="sm" variant="outline" onClick={() => window.open(link(r.token), "_blank", "noopener")} className="h-8 border-line bg-transparent">Abrir</Button>
            <Button size="sm" variant="outline" onClick={() => dl(r)} className="h-8 border-line bg-transparent">.html</Button>
            <Button size="sm" variant="ghost" onClick={() => window.confirm("Trocar o endereço? O atual para de funcionar na hora.") && upd(r, { new_link: true }, "Endereço trocado")} className="h-8 text-slate-300">Trocar endereço</Button>
            <Button size="sm" variant="ghost" onClick={() => del(r)} className="h-8 text-red-300" aria-label="Apagar link"><Trash2 className="w-3.5 h-3.5" /></Button>
          </div>
        </div>
      ))}
    </Modal>
  );
}

// ------------------------------------------------------------------ caixa de texto / forma: edição
function ColorField({ label, value, onChange, allowNone }) {
  const none = value === "transparent";
  return (
    <label className="flex items-center gap-2 text-xs text-slate-300">
      <input type="color" value={none ? "#000000" : value || "#000000"} onChange={e => onChange(e.target.value)} disabled={none}
             className="w-8 h-7 rounded border border-line bg-transparent p-0.5 cursor-pointer disabled:opacity-30" aria-label={label} />
      <span className="flex-1">{label}</span>
      {allowNone && <button type="button" onClick={() => onChange(none ? "#2dd4bf" : "transparent")} className="text-[10px] text-slate-500 hover:text-slate-200">{none ? "colorir" : "sem cor"}</button>}
    </label>
  );
}

function BoxPanel({ node, onChange }) {
  const pill = (on) => `px-2 py-1 rounded border text-[11px] ${on ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400"}`;
  return (
    <div className="space-y-2.5" data-testid="av-box-panel">
      <div className="text-sm font-semibold text-slate-100">{node.kind === "note" ? "Caixa de texto" : "Forma"}</div>
      <textarea value={node.text || ""} onChange={e => onChange({ text: e.target.value })} rows={4} placeholder={node.kind === "note" ? "Escreva a observação…" : "Texto dentro da forma (opcional)"}
                className="w-full rounded-md bg-sunken border border-line px-2 py-1.5 text-sm text-slate-100" data-testid="av-box-text" />
      <div className="flex gap-1">{[["round", "arredondada"], ["rect", "retângulo"], ["ellipse", "elipse"]].map(([k, l]) => (
        <button key={k} onClick={() => onChange({ shape: k })} className={pill((node.shape || "round") === k)}>{l}</button>))}</div>
      <ColorField label="Preenchimento" value={node.fill} onChange={v => onChange({ fill: v })} allowNone />
      <ColorField label="Borda" value={node.stroke} onChange={v => onChange({ stroke: v })} />
      <ColorField label="Texto" value={node.color} onChange={v => onChange({ color: v })} />
      <label className="block text-xs text-slate-400">Opacidade do preenchimento · {Math.round((node.opacity ?? 0.9) * 100)}%
        <input type="range" min="0" max="1" step="0.02" value={node.opacity ?? 0.9} onChange={e => onChange({ opacity: Number(e.target.value) })} className="w-full" /></label>
      <label className="block text-xs text-slate-400">Tamanho do texto · {node.size || 13}
        <input type="range" min="9" max="40" step="1" value={node.size || 13} onChange={e => onChange({ size: Number(e.target.value) })} className="w-full" /></label>
      <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={!!node.dashed} onChange={e => onChange({ dashed: e.target.checked })} /> borda tracejada</label>
      <div className="text-[11px] text-slate-500">Arraste para mover; o quadradinho no canto muda o tamanho. Formas ficam atrás dos equipamentos — bom para marcar uma região ou POP.</div>
    </div>
  );
}

// ------------------------------------------------------------------ aparência do mapa
function StyleDlg({ style, onChange, onClose, toSubs, onToSubs, isSub, own, onOwn }) {
  const st = { ...style };
  const P = paletteOf(st);
  const set = (p) => onChange({ ...st, ...p });
  const pill = (on) => `px-2.5 py-1.5 rounded border text-xs ${on ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400 hover:border-line2"}`;
  const COLORS = [["bg0", "Fundo (bordas)"], ["bg1", "Fundo (centro)"], ["panel", "Preenchimento dos ícones"], ["line", "Grade e bordas"], ["ink", "Texto"], ["dim", "Texto secundário"],
    ["up", "Respondendo / UP"], ["warn", "Instável"], ["down", "Sem resposta / DOWN"], ["pending", "Aguardando"]];
  return (
    <Modal title="Aparência do mapa" onClose={onClose} wide>
      <div className="space-y-4" data-testid="av-style-dlg">
        {isSub && (
          <label className="flex items-start gap-2 rounded-md border border-line p-2.5 text-sm text-slate-200" data-testid="av-style-follow">
            <input type="checkbox" className="mt-1" checked={!own} onChange={e => onOwn(!e.target.checked)} />
            <span>Seguir a aparência do mapa de cima<span className="block text-[11px] text-slate-500">Marcado, este submapa fica igual ao mapa principal (muda junto). Mexer em qualquer opção abaixo dá a ele uma aparência própria.</span></span>
          </label>
        )}
        <div>
          <div className="text-xs text-slate-400 mb-1.5">Tema</div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(PRESETS).map(([k, p]) => (
              <button key={k} onClick={() => { const { label, ...cols } = p; set({ ...cols, preset: k }); }} className={`${pill(st.preset === k || (!st.preset && k === "neon"))} flex items-center gap-2`} data-testid={`av-preset-${k}`}>
                <span className="flex">{["bg1", "up", "warn", "down"].map(c => <span key={c} className="w-3 h-3 rounded-full -ml-1 first:ml-0 border border-black/30" style={{ background: p[c] }} />)}</span>{p.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
          {COLORS.map(([k, l]) => <ColorField key={k} label={l} value={P[k]} onChange={v => set({ [k]: v, preset: "" })} />)}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="text-xs text-slate-400 mb-1.5">Fundo</div>
            <div className="flex flex-wrap gap-1">{[["grid", "grade"], ["dots", "pontos"], ["radar", "radar"], ["plain", "liso"]].map(([k, l]) => (
              <button key={k} onClick={() => set({ bg: k })} className={pill((st.bg || "grid") === k)}>{l}</button>))}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1.5">Tamanho dos ícones · {Math.round((st.scale || 1) * 100)}%</div>
            <input type="range" min="0.5" max="1.8" step="0.05" value={st.scale || 1} onChange={e => set({ scale: Number(e.target.value) })} className="w-full" />
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1.5">Ícone dos POPs (submapas)</div>
            <div className="flex flex-wrap gap-1">{[["hex", "hexágono"], ["circle", "círculo"], ["square", "quadrado"], ["diamond", "losango"]].map(([k, l]) => (
              <button key={k} onClick={() => set({ sub_shape: k })} className={pill((st.sub_shape || "hex") === k)} data-testid={`av-sub-${k}`}>{l}</button>))}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400 mb-1.5">Ícone dos equipamentos</div>
            <div className="flex flex-wrap gap-1">{[["orb", "círculo"], ["square", "quadrado"], ["diamond", "losango"], ["dot", "ponto"], ["card", "cartão com nome"]].map(([k, l]) => (
              <button key={k} onClick={() => set({ dev_shape: k })} className={pill((st.dev_shape || "orb") === k)} data-testid={`av-dev-${k}`}>{l}</button>))}</div>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <div className="text-xs text-slate-400 mb-1.5">Espessura das linhas · {(Number(st.line_w) || 2).toFixed(1)}px</div>
            <input type="range" min="0.5" max="6" step="0.5" value={Number(st.line_w) || 2} onChange={e => set({ line_w: Number(e.target.value) })} className="w-full" data-testid="av-line-w" />
          </div>
          <div className="space-y-1.5">
            <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={st.line_auto !== false} onChange={e => set({ line_auto: e.target.checked })} /> mais grossa quanto maior a capacidade (1G a 100G)</label>
            <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={st.traffic !== false} onChange={e => set({ traffic: e.target.checked })} /> animação de tráfego nas linhas com SNMP</label>
            <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={st.flow !== false} onChange={e => set({ flow: e.target.checked })} /> animação nas linhas sem SNMP que estão UP</label>
          </div>
        </div>
        <label className="flex items-start gap-2 text-sm text-slate-300"><input type="checkbox" className="mt-1" checked={toSubs} onChange={e => onToSubs(e.target.checked)} data-testid="av-style-subs" />
          <span>Aplicar também a todos os submapas dentro deste<span className="block text-[11px] text-slate-500">e aos submapas deles, ao salvar. Desmarque para mudar só este mapa.</span></span></label>
        <div className="flex items-center justify-between pt-1">
          <span className="text-[11px] text-slate-500">Aparece na hora; clique em Salvar no mapa para gravar.</span>
          <Button onClick={onClose} className="bg-brand hover:bg-brand-strong">Pronto</Button>
        </div>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ importar do The Dude
function DudeImport({ onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [pct, setPct] = useState(0);
  const [pv, setPv] = useState(null);
  const [createMissing, setCreateMissing] = useState(true);
  const [tag, setTag] = useState("");
  const [showNew, setShowNew] = useState(false);
  const upload = async (file) => {
    if (!file) return;
    setBusy(true); setPct(0); setPv(null);
    const fd = new FormData(); fd.append("file", file);
    try {
      const { data } = await api.post("/avail/import/dude", fd, { onUploadProgress: (e) => e.total && setPct(Math.round((e.loaded / e.total) * 100)) });
      setPv(data);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const apply = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/avail/import/dude/${pv.import_id}/apply`, { create_missing: createMissing, tag });
      toast.success(`${data.maps} mapa(s) importado(s) · ${data.devices_created} equipamento(s) cadastrado(s) · ${data.devices_reused} já existiam`);
      onDone(data.open);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const Tree = ({ n, d = 0 }) => (
    <>
      <div className="flex items-center gap-2 py-0.5 text-xs" style={{ paddingLeft: d * 16 }}>
        <Hexagon className="w-3 h-3 shrink-0" style={{ color: C.up }} /><span className="text-slate-200">{n.name}</span>
        <span className="text-slate-500">{n.devices} equip. · {n.links} ligações</span>
      </div>
      {n.children.map((c, i) => <Tree key={i} n={c} d={d + 1} />)}
    </>
  );
  return (
    <Modal title="Importar mapas do The Dude" onClose={onClose} wide>
      {!pv ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-300">Mande o backup do Dude: no RouterOS, <code className="text-xs bg-sunken px-1 rounded">/dude export-db backup-file=dude.tgz</code> e baixe o arquivo pelo Winbox (Files). No Dude para Windows, o <code className="text-xs bg-sunken px-1 rounded">dude.db</code> da pasta data (com o serviço parado).</p>
          <p className="text-xs text-slate-500">Lemos só mapas, posições, ligações, nomes e IPs. Senhas e communities do Dude são ignoradas. Nada é gravado antes da sua confirmação.</p>
          <label className={`flex flex-col items-center justify-center gap-2 h-32 rounded-lg border-2 border-dashed cursor-pointer ${busy ? "opacity-60" : "hover:border-brand"}`} style={{ borderColor: C.line }}>
            {busy ? <><Loader2 className="w-5 h-5 animate-spin text-slate-400" /><span className="text-xs text-slate-400">{pct < 100 ? `Enviando ${pct}%` : "Lendo os mapas…"}</span></>
              : <><Upload className="w-5 h-5 text-slate-400" /><span className="text-sm text-slate-300">Escolher o .tgz ou dude.db</span></>}
            <input type="file" accept=".tgz,.gz,.tar,.db" className="hidden" disabled={busy} onChange={e => upload(e.target.files?.[0])} data-testid="dude-file" />
          </label>
        </div>
      ) : (
        <div className="space-y-3" data-testid="dude-preview">
          <div className="grid grid-cols-3 gap-2 text-center">
            {[["mapas", pv.maps], ["equipamentos nos mapas", pv.devices_in_maps], ["ligações", pv.links]].map(([l, v]) => (
              <div key={l} className="rounded-md border border-line py-2"><div className="text-xl font-mono text-slate-100">{v}</div><div className="text-[11px] text-slate-500">{l}</div></div>
            ))}
          </div>
          <div className="max-h-56 overflow-y-auto rounded-md border border-line p-2">{pv.tree.map((n, i) => <Tree key={i} n={n} />)}</div>
          <div className="text-sm text-slate-300">
            <b style={{ color: C.up }}>{pv.match.length}</b> já existem no BastiON com o mesmo IP (são reaproveitados, não cadastra de novo) ·{" "}
            <b className="text-slate-100">{pv.new.length}</b> ainda não existem
            {pv.new.length > 0 && <button onClick={() => setShowNew(v => !v)} className="ml-2 text-xs text-brand-soft hover:underline">{showNew ? "esconder" : "ver lista"}</button>}
          </div>
          {showNew && <div className="max-h-40 overflow-y-auto rounded-md border border-line text-xs divide-y divide-line">
            {pv.new.map((d, i) => <div key={i} className="flex px-2 py-1"><span className="text-slate-200 truncate">{d.name}</span><span className="ml-auto font-mono text-slate-500">{d.ip}</span></div>)}</div>}
          {pv.new.length > 0 && (
            <label className="flex items-start gap-2 text-sm text-slate-300">
              <input type="checkbox" className="mt-1" checked={createMissing} onChange={e => setCreateMissing(e.target.checked)} data-testid="dude-create" />
              <span>Cadastrar os {pv.new.length} que faltam (nome e IP, para o ping) com a tag
                <Input value={tag} onChange={e => setTag(e.target.value.toUpperCase())} placeholder="ex.: HELP" className="inline-block w-28 h-7 mx-1.5 bg-sunken border-line text-xs" aria-label="Tag" data-testid="dude-tag" />
                — só essa tag, sem os nomes dos POPs. Sem marcar, eles ficam fora dos mapas.</span>
            </label>
          )}
          <div className="flex justify-between items-center">
            <button onClick={() => setPv(null)} className="text-xs text-slate-400 hover:text-slate-200">escolher outro arquivo</button>
            <Button onClick={apply} disabled={busy || (createMissing && pv.new.length > 0 && !tag.trim())} className="bg-brand hover:bg-brand-strong" data-testid="dude-apply">{busy && <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />}Importar {pv.maps} mapas</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ diálogos
function Modal({ title, onClose, children, wide }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-3 md:p-8 overflow-y-auto" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`w-full ${wide ? "max-w-3xl" : "max-w-lg"} bg-surface border border-line rounded-lg shadow-2xl`}>
        <div className="flex items-center px-4 py-3 border-b border-line"><h2 className="text-sm font-semibold text-slate-100">{title}</h2>
          <button onClick={onClose} className="ml-auto text-slate-400 hover:text-slate-100" aria-label="Fechar"><X className="w-4 h-4" /></button></div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

function AddDevices({ devices, current, maps, names, initialTarget, onAddHere, onAddedElsewhere, onClose }) {
  const [q, setQ] = useState("");
  const [pick, setPick] = useState(() => new Set());
  const [target, setTarget] = useState(initialTarget || current.id);
  const [other, setOther] = useState(null);          // nós do mapa de destino quando não é o mapa aberto
  const [busy, setBusy] = useState(false);
  const subIds = current.nodes.filter(n => n.kind === "submap").map(n => n.map_id);
  useEffect(() => {
    setPick(new Set());
    if (target === current.id) { setOther(null); return; }
    setOther(undefined);
    api.get(`/avail/maps/${target}`).then(r => setOther(r.data)).catch(e => { toast.error(formatApiError(e)); setOther(null); });
  }, [target]); // eslint-disable-line react-hooks/exhaustive-deps
  const inMap = useMemo(() => new Set(((target === current.id ? current : other) || { nodes: [] }).nodes.filter(n => n.kind === "device").map(n => n.device_id)), [target, current, other]);
  const groups = useMemo(() => {
    const ql = q.toLowerCase(), g = {};
    devices.filter(d => !ql || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(ql))
      .forEach(d => ((d.tags || []).length ? d.tags : ["Sem grupo"]).forEach(t => (g[t] = g[t] || []).push(d)));
    return Object.entries(g).sort((a, b) => (a[0] === "Sem grupo") - (b[0] === "Sem grupo") || a[0].localeCompare(b[0], "pt-BR"));
  }, [devices, q]);
  const set = (ids, on) => setPick(p => { const n = new Set(p); ids.forEach(i => (on ? n.add(i) : n.delete(i))); return n; });
  const go = async () => {
    if (target === current.id) return onAddHere([...pick]);
    setBusy(true);
    try {
      const { data } = await api.post(`/avail/maps/${target}/add-devices`, { device_ids: [...pick] });
      toast.success(`${data.added} equipamento(s) colocado(s) em ${data.map}`);
      onAddedElsewhere();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const tName = target === current.id ? current.name : names[target] || "mapa";
  return (
    <Modal title="Colocar equipamentos" onClose={onClose} wide>
      <label className="block text-xs text-slate-400 mb-3 space-y-1">Colocar em
        <select value={target} onChange={e => setTarget(e.target.value)} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100" data-testid="av-add-target">
          <option value={current.id}>Este mapa · {current.name}</option>
          {subIds.length > 0 && <optgroup label="Submapas deste mapa">{subIds.map(id => <option key={id} value={id}>{names[id] || id}</option>)}</optgroup>}
          {maps.filter(m => m.id !== current.id && !subIds.includes(m.id)).length > 0 && (
            <optgroup label="Outros mapas">{maps.filter(m => m.id !== current.id && !subIds.includes(m.id)).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>)}
        </select>
      </label>
      <div className="relative mb-2"><Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar por nome, IP ou grupo" className="pl-8 h-9 bg-sunken border-line" autoFocus /></div>
      {other === undefined ? <div className="p-6 flex justify-center"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div> : (
      <div className="max-h-[50vh] overflow-y-auto border border-line rounded" data-testid="av-add-list">
        {groups.map(([g, ds]) => {
          const free = ds.filter(d => !inMap.has(d.id)).map(d => d.id), n = free.filter(i => pick.has(i)).length;
          return (
            <div key={g} className="border-b border-line last:border-0">
              <label className="flex items-center gap-2 px-3 py-1.5 bg-panel sticky top-0 text-xs font-semibold text-slate-200 cursor-pointer">
                <input type="checkbox" disabled={!free.length} checked={!!free.length && n === free.length} onChange={e => set(free, e.target.checked)} />{g}
                <span className="font-mono font-normal text-slate-500">{ds.length}</span></label>
              {ds.map(d => (
                <label key={d.id} className={`flex items-center gap-2 pl-7 pr-3 py-1 text-sm cursor-pointer hover:bg-slate-800/40 ${inMap.has(d.id) ? "opacity-40" : ""}`}>
                  <input type="checkbox" disabled={inMap.has(d.id)} checked={pick.has(d.id) || inMap.has(d.id)} onChange={e => set([d.id], e.target.checked)} />
                  <span className="text-slate-100 truncate">{d.name}</span><span className="ml-auto font-mono text-xs text-slate-500">{inMap.has(d.id) ? "já está lá" : d.host}</span>
                </label>
              ))}
            </div>
          );
        })}
      </div>)}
      <div className="flex items-center justify-between mt-3">
        <span className="text-xs text-slate-400">{pick.size} selecionado(s)</span>
        <Button onClick={go} disabled={!pick.size || busy} className="bg-brand hover:bg-brand-strong" data-testid="av-add-confirm">
          {busy && <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />}Colocar {pick.size || ""} em {tName}</Button>
      </div>
    </Modal>
  );
}

function AddSubmap({ maps, current, onPick, onClose }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const used = new Set(current.nodes.filter(n => n.kind === "submap").map(n => n.map_id));
  const create = async () => {
    setBusy(true);
    try { const { data } = await api.post("/avail/maps", { name }); onPick(data.id, true); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Modal title="Colocar submapa" onClose={onClose}>
      <div className="text-xs text-slate-400 mb-2">Mapa novo dentro deste</div>
      <div className="flex gap-2 mb-4">
        <Input value={name} onChange={e => setName(e.target.value)} placeholder="ex.: POP Centro" className="h-9 bg-sunken border-line" onKeyDown={e => e.key === "Enter" && name.trim() && create()} autoFocus />
        <Button onClick={create} disabled={!name.trim() || busy} className="bg-brand hover:bg-brand-strong">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : "Criar"}</Button>
      </div>
      <div className="text-xs text-slate-400 mb-1">Ou um mapa que já existe</div>
      <div className="max-h-72 overflow-y-auto border border-line rounded divide-y divide-line">
        {maps.filter(m => m.id !== current.id).map(m => (
          <button key={m.id} disabled={used.has(m.id)} onClick={() => onPick(m.id)} className="w-full flex items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-800/40 disabled:opacity-40">
            <span className="w-2 h-2 rounded-full" style={{ background: C[m.summary.state] || C.empty }} />
            <span className="text-slate-100">{m.name}</span><span className="ml-auto text-xs text-slate-500">{m.summary.total} equip.{used.has(m.id) ? " · já está aqui" : ""}</span>
          </button>
        ))}
        {maps.length < 2 && <div className="px-3 py-2 text-xs text-slate-500">Nenhum outro mapa ainda.</div>}
      </div>
    </Modal>
  );
}

function SettingsDlg({ init, onClose, onSaved }) {
  const [f, setF] = useState(init);
  const save = async () => { try { onSaved((await api.put("/avail/settings", f)).data); toast.success("Ajustes salvos"); } catch (e) { toast.error(formatApiError(e)); } };
  const row = (k, label, hint) => (
    <label className="flex items-center gap-3 text-sm text-slate-300 py-1">{label}<Input type="number" value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} className="ml-auto w-24 h-8 bg-sunken border-line font-mono" />
      <span className="w-14 text-xs text-slate-500">{hint}</span></label>
  );
  return (
    <Modal title="Ajustes do ping" onClose={onClose}>
      {row("interval_s", "Pingar a cada", "seg")}{row("count", "Pacotes por ciclo", "")}{row("down_after", "Marcar sem resposta após", "ciclos")}
      {row("warn_loss", "Instável com perda a partir de", "%")}{row("warn_ms", "Instável com latência a partir de", "ms")}
      <label className="flex items-center gap-2 text-sm text-slate-300 py-2"><input type="checkbox" checked={!!f.alert} onChange={e => setF({ ...f, alert: e.target.checked })} /> Avisar no Telegram quando parar e voltar a responder</label>
      <div className="text-[11px] text-slate-500 mb-3">O aviso segue os grupos de alerta do Telegram (tipo Equipamento). Equipamento atrás de jump é pingado a partir do jump.</div>
      <div className="flex justify-end"><Button onClick={save} className="bg-brand hover:bg-brand-strong">Salvar ajustes</Button></div>
    </Modal>
  );
}

// ------------------------------------------------------------------ página
export default function Availability() {
  const [params, setParams] = useSearchParams();
  const [list, setList] = useState(null);
  const [mapId, setMapId] = useState(() => params.get("m") || ls.get("bastion_avail_map", ""));
  const [trail, setTrail] = useState([]);
  const [map, setMap] = useState(null);
  const [draft, setDraft] = useState(null);
  const [live, setLive] = useState(null);
  const [selected, setSelected] = useState(null);
  const [tool, setTool] = useState("move");
  const [fitSig, setFitSig] = useState(0);
  const [devices, setDevices] = useState([]);
  const [dlg, setDlg] = useState(null);
  const [newName, setNewName] = useState(null);
  const [sound, setSound] = useState(() => ls.get("bastion_avail_sound", false));
  const [saving, setSaving] = useState(false);
  const [alarmsOpen, setAlarmsOpen] = useState(() => ls.get("bastion_avail_alarms", true));
  const pendingSel = useRef(null);         // item para selecionar depois de abrir o submapa (clique no incidente)
  const shell = useRef(null);
  const lastDown = useRef(null);
  const editing = !!draft;
  const cur = draft || map;

  const loadList = useCallback(() => api.get("/avail/maps").then(r => { setList(r.data); return r.data; }), []);
  useEffect(() => {
    loadList().then(d => { if (!d.maps.some(m => m.id === mapId)) setMapId(d.maps.find(m => !m.parents.length)?.id || d.maps[0]?.id || ""); }).catch(e => toast.error(formatApiError(e)));
    api.get("/devices").then(r => setDevices((r.data || []).sort((a, b) => a.name.localeCompare(b.name, "pt-BR", { numeric: true })))).catch(() => {});
    const t = setInterval(() => loadList().catch(() => {}), 15000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // caminho até o mapa (breadcrumb): sobe pelos mapas "pai" até um mapa principal
  const pathTo = useCallback((id, maps) => {
    const by = Object.fromEntries((maps || []).map(m => [m.id, m]));
    const out = [id]; let c = by[id];
    while (c && c.parents.length && out.length < 8) { const p = c.parents.find(x => !out.includes(x)); if (!p) break; out.unshift(p); c = by[p]; }
    return out;
  }, []);

  useEffect(() => {
    if (!mapId) { setMap(null); return; }
    ls.set("bastion_avail_map", mapId);
    if (params.get("m") !== mapId) setParams({ m: mapId }, { replace: true });
    setDraft(null); setSelected(null); setLive(null); lastDown.current = null;
    api.get(`/avail/maps/${mapId}`).then(r => { setMap(r.data); setFitSig(s => s + 1); if (pendingSel.current) { setSelected(pendingSel.current); pendingSel.current = null; } })
      .catch(e => { toast.error(formatApiError(e)); setMap(null); });
  }, [mapId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (list && mapId && !trail.includes(mapId)) setTrail(pathTo(mapId, list.maps)); }, [list, mapId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!mapId) return;
    let alive = true;
    const go = () => api.get(`/avail/maps/${mapId}/live`).then(r => {
      if (!alive) return;
      setLive(r.data);
      const d = r.data.summary.down;
      if (sound && lastDown.current != null && d > lastDown.current) beep();
      lastDown.current = d;
    }).catch(() => {});
    go(); const t = setInterval(go, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [mapId, sound]);

  const refreshLive = () => { if (mapId) api.get(`/avail/maps/${mapId}/live`).then(r => setLive(r.data)).catch(() => {}); loadList().catch(() => {}); };
  const pickAlarm = (a) => {
    const target = a.kind === "link" ? (a.link_id ? { link: a.link_id } : null) : (a.node_id ? { id: a.node_id } : null);
    if (a.map_id === mapId) return setSelected(target);
    pendingSel.current = target;
    open(a.map_id, true);
  };
  const toggleAlarms = () => { const v = !alarmsOpen; setAlarmsOpen(v); ls.set("bastion_avail_alarms", v); };
  // duplo clique no submapa abre tudo que tem dentro (editando: salva antes)
  const enterSub = async (id) => {
    if (draft) {
      try { const { data } = await api.put(`/avail/maps/${draft.id}`, draft); setMap(data); setDraft(null); loadList(); }
      catch (e) { return toast.error(formatApiError(e)); }
    }
    setTrail(t => [...t.slice(0, t.indexOf(mapId) + 1), id]); setMapId(id);
  };
  const open = (id, push) => { if (editing && !window.confirm("Sair sem salvar as alterações?")) return; setTrail(t => (push ? [...t.slice(0, t.indexOf(mapId) + 1), id] : pathTo(id, list?.maps))); setMapId(id); };
  const byId = useMemo(() => Object.fromEntries((list?.maps || []).map(m => [m.id, m])), [list]);
  const names = useMemo(() => ({ ...Object.fromEntries(devices.map(d => [d.id, d.name])), ...Object.fromEntries((list?.maps || []).map(m => [m.id, m.name])) }), [devices, list]);
  const canEdit = !!list?.can_edit;

  // ---- edição
  const patch = (fn) => setDraft(d => fn(JSON.parse(JSON.stringify(d))));
  const startEdit = () => { setDraft(JSON.parse(JSON.stringify(map))); setTool("move"); };
  const save = async () => {
    setSaving(true);
    try { const { data } = await api.put(`/avail/maps/${map.id}`, draft); setMap(data); setDraft(null); toast.success("Mapa salvo"); loadList(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setSaving(false); }
  };
  const centerSpot = (i = 0, cols = 6) => {
    const xs = cur.nodes.map(n => n.x), ys = cur.nodes.map(n => n.y);
    const x0 = xs.length ? Math.min(...xs) : 0, y0 = ys.length ? Math.max(...ys) + 150 : 0;
    return { x: Math.round((x0 + (i % cols) * 150) / 10) * 10, y: Math.round((y0 + Math.floor(i / cols) * 130) / 10) * 10 };
  };
  const addDevices = (ids) => { patch(d => { ids.forEach((id, i) => d.nodes.push({ id: uid(), kind: "device", device_id: id, label: "", ...centerSpot(i) })); return d; }); setDlg(null); setFitSig(s => s + 1); };
  const addSubmap = (mid, created) => { patch(d => { d.nodes.push({ id: uid(), kind: "submap", map_id: mid, label: "", ...centerSpot() }); return d; }); setDlg(null); if (created) loadList(); setFitSig(s => s + 1); };
  const patchNode = (id, p) => setDraft(d => ({ ...d, nodes: d.nodes.map(n => (n.id === id ? { ...n, ...p } : n)) }));
  const addBox = (kind) => {
    const P = paletteOf(draft?.style);
    const sp = centerSpot();
    const id = uid();
    patch(d => {
      d.nodes.push(kind === "note"
        ? { id, kind: "note", x: sp.x, y: sp.y, w: 240, h: 90, text: "Observação", fill: P.panel, stroke: P.line, color: P.ink, opacity: 0.92, size: 13, shape: "round", label: "" }
        : { id, kind: "shape", x: sp.x - 100, y: sp.y - 60, w: 320, h: 200, text: "", fill: P.up, stroke: P.up, color: P.ink, opacity: 0.08, size: 14, shape: "round", dashed: true, label: "" });
      return d;
    });
    setSelected({ id }); setTool("move"); setFitSig(s => s + 1);
  };
  const addLabel = () => { const t = window.prompt("Texto"); if (t?.trim()) patch(d => { d.nodes.push({ id: uid(), kind: "label", label: t.trim().slice(0, 80), ...centerSpot() }); return d; }); };
  const delSel = () => {
    if (!selected) return;
    patch(d => {
      if (selected.link) d.links = d.links.filter(l => l.id !== selected.link);
      else { d.nodes = d.nodes.filter(n => n.id !== selected.id); d.links = d.links.filter(l => l.from !== selected.id && l.to !== selected.id); }
      return d;
    });
    setSelected(null);
  };
  useEffect(() => {
    const k = (e) => { if (editing && (e.key === "Delete" || e.key === "Backspace") && selected && !/input|textarea/i.test(e.target.tagName)) delSel(); };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  });
  const delMap = async () => {
    if (!window.confirm(`Excluir o mapa "${map.name}"? Ele vai para a lixeira e sai dos mapas onde era submapa.`)) return;
    try { await api.delete(`/avail/maps/${map.id}`); toast.success("Mapa na lixeira"); setDraft(null); const d = await loadList(); setMapId(d.maps.find(m => !m.parents.length)?.id || d.maps[0]?.id || ""); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const restore = async (id) => { try { await api.post(`/avail/trash/${id}/restore`); toast.success("Mapa restaurado"); await loadList(); setMapId(id); } catch (e) { toast.error(formatApiError(e)); } };
  const createMap = async () => {
    try { const { data } = await api.post("/avail/maps", { name: newName }); setNewName(null); await loadList(); setTrail([data.id]); setMapId(data.id); setDraft(JSON.parse(JSON.stringify(data))); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const full = () => { try { document.fullscreenElement ? document.exitFullscreen() : shell.current?.requestFullscreen(); } catch { /* ok */ } };
  const toggleSound = () => { const v = !sound; setSound(v); ls.set("bastion_avail_sound", v); if (v) beep(); };

  const sel = selected?.id && cur?.nodes.find(n => n.id === selected.id);
  const selLive = sel && live?.nodes?.[sel.id];
  const sum = live?.summary;
  const roots = (list?.maps || []).filter(m => !m.parents.length);
  const isSub = !!(cur && (byId[cur.id]?.parents || []).length);
  const viewStyle = !cur ? {} : (!isSub || styleOwn(cur.style)) ? (cur.style || { bg: cur.bg }) : effectiveStyle(cur.id, byId, trail);
  const viewMap = cur && cur.style !== viewStyle ? { ...cur, style: viewStyle } : cur;

  // grupos (o que vai no seletor do menu): os mapas de cima; um "Local" (contêiner do Dude) abre nos mapas de dentro
  const groups = useMemo(() => {
    const out = [];
    roots.forEach(r => {
      if (r.name.trim().toLowerCase() === "local" && r.children.length) r.children.forEach(c => byId[c] && !out.includes(c) && out.push(c));
      else if (!out.includes(r.id)) out.push(r.id);
    });
    return out;
  }, [list]); // eslint-disable-line react-hooks/exhaustive-deps
  const under = (gid) => {                          // o grupo e tudo que está abaixo dele
    const seen = new Set([gid]), q = [gid];
    while (q.length) (byId[q.shift()]?.children || []).forEach(c => { if (!seen.has(c)) { seen.add(c); q.push(c); } });
    return seen;
  };
  const [group, setGroupRaw] = useState(() => ls.get("bastion_avail_group", ""));
  const setGroup = (g) => { setGroupRaw(g); ls.set("bastion_avail_group", g); };
  const groupOk = group && groups.includes(group);
  const groupSet = useMemo(() => (groupOk ? under(group) : null), [group, groupOk, list]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (groupSet && mapId && !groupSet.has(mapId) && byId[mapId]) setGroupRaw(""); }, [mapId]); // eslint-disable-line react-hooks/exhaustive-deps
  // nós abertos na árvore (o caminho do mapa aberto abre sozinho)
  const [openSet, setOpenSet] = useState(() => new Set(ls.get("bastion_avail_open", [])));
  const flip = (k) => setOpenSet(o => { const n = new Set(o); n.has(k) ? n.delete(k) : n.add(k); ls.set("bastion_avail_open", [...n].slice(-300)); return n; });
  const trailKeys = useMemo(() => new Set(trail.slice(0, -1).map((t, i) => trail.slice(0, i + 1).join(">"))), [trail]);

  // árvore: cada mapa com seus submapas; abre e fecha pela setinha
  const Tree = ({ id, depth, seen, label, path }) => {
    const m = byId[id];
    if (!m || seen.has(id)) return null;
    const s2 = new Set(seen).add(id);
    const key = path ? `${path}>${id}` : id;
    const kids = m.children.filter(c => byId[c] && !s2.has(c));
    const isOpen = depth === 0 ? !openSet.has(`-${key}`) : openSet.has(key) || trailKeys.has(key);
    return (
      <>
        <div className={`flex items-center rounded ${id === mapId ? "bg-white/5 text-white" : "text-slate-300 hover:bg-white/5"}`} style={{ paddingLeft: 2 + depth * 14 }}>
          {kids.length ? (
            <button onClick={() => flip(depth === 0 ? `-${key}` : key)} className="w-5 h-6 shrink-0 inline-flex items-center justify-center text-slate-500 hover:text-slate-200" aria-expanded={isOpen} aria-label={isOpen ? "Fechar" : "Abrir"}>
              {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </button>
          ) : <span className="w-5 shrink-0" />}
          <button onClick={() => open(id)} className="flex-1 min-w-0 flex items-center gap-2 py-1.5 pr-2 text-left text-[13px]" data-testid={`av-tree-${id}`}>
            <span className="w-2 h-2 rounded-full shrink-0" style={{ background: C[m.summary.state] || C.empty, boxShadow: m.summary.state === "down" ? `0 0 8px ${C.down}` : "none" }} />
            <span className="truncate">{label || m.name}</span>
            {!isOpen && kids.length > 0 && <span className="text-[10px] text-slate-600">{kids.length}</span>}
            {m.summary.down > 0 && <span className="ml-auto font-mono text-[10px]" style={{ color: C.down }}>{m.summary.down}</span>}
          </button>
        </div>
        {isOpen && depth < 6 && kids.map(c => <Tree key={c} id={c} depth={depth + 1} seen={s2} label={m.child_labels?.[c]} path={key} />)}
      </>
    );
  };
  const GroupChip = ({ id, name, st, down }) => (
    <button onClick={() => setGroup(id)} data-testid={`av-group-${id || "all"}`}
      className={`max-w-full inline-flex items-center gap-1.5 h-7 px-2 rounded-full border text-[12px] ${(id ? groupOk && group === id : !groupOk) ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400 hover:text-slate-100"}`}>
      {st && <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: C[st] || C.empty }} />}
      <span className="truncate">{name}</span>
      {down > 0 && <span className="font-mono text-[10px]" style={{ color: C.down }}>{down}</span>}
    </button>
  );

  const tb = "h-8 px-2.5 text-xs border-line bg-panel text-slate-200 hover:bg-slate-800";
  return (
    <div ref={shell} className="flex-1 flex min-h-0 bg-canvas" data-testid="avail-page">
      <aside className="hidden lg:flex w-60 shrink-0 flex-col border-r border-line">
        <div className="px-3 pt-4 pb-2 flex items-center">
          <h1 className="font-heading text-lg font-semibold text-slate-100">Disponibilidade</h1>
          {canEdit && <button onClick={() => setDlg("dude")} className="ml-auto p-1 text-slate-400 hover:text-slate-100" aria-label="Importar do The Dude" title="Importar do The Dude" data-testid="av-dude"><Upload className="w-4 h-4" /></button>}
          {canEdit && <button onClick={() => setNewName("")} className="p-1 text-slate-400 hover:text-slate-100" aria-label="Novo mapa" data-testid="av-new"><Plus className="w-4 h-4" /></button>}
        </div>
        {newName !== null && (
          <div className="px-3 pb-2 flex gap-1">
            <Input value={newName} onChange={e => setNewName(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && newName.trim()) createMap(); if (e.key === "Escape") setNewName(null); }}
                   placeholder="Nome do mapa" className="h-8 bg-sunken border-line text-sm" autoFocus data-testid="av-new-name" />
            <Button size="sm" onClick={createMap} disabled={!newName.trim()} className="h-8 bg-brand hover:bg-brand-strong">Criar</Button>
          </div>
        )}
        {groups.length > 1 && (
          <div className="px-3 pb-2 flex flex-wrap gap-1" role="group" aria-label="Mostrar só os mapas de" data-testid="av-groups">
            <GroupChip id="" name="Todos" />
            {groups.map(g => <GroupChip key={g} id={g} name={byId[g].name} st={byId[g].summary.state} down={byId[g].summary.down} />)}
          </div>
        )}
        <div className="flex-1 overflow-y-auto px-1.5 pb-3">
          {groupOk ? <Tree key={group} id={group} depth={0} seen={new Set()} />
            : roots.map(m => <Tree key={m.id} id={m.id} depth={0} seen={new Set()} />)}
          {list && !list.maps.length && <div className="px-3 py-4 text-xs text-slate-500">Crie o mapa principal no + acima. Dentro dele você coloca equipamentos e os submapas de cada POP ou cidade.
            {canEdit && <button onClick={() => setDlg("dude")} className="block mt-2 text-brand-soft hover:underline">Ou importe os mapas do The Dude</button>}</div>}
        </div>
        {list?.trash?.length > 0 && (
          <details className="px-3 py-2 border-t border-line text-xs text-slate-400">
            <summary className="cursor-pointer">Lixeira ({list.trash.length})</summary>
            {list.trash.map(t => (
              <div key={t.id} className="flex items-center gap-2 py-1"><span className="truncate">{t.name}</span>
                {canEdit && <button onClick={() => restore(t.id)} className="ml-auto text-brand-soft hover:underline flex items-center gap-1"><RotateCcw className="w-3 h-3" />restaurar</button>}</div>
            ))}
          </details>
        )}
        {list?.info?.last_cycle && <div className="px-3 py-2 border-t border-line text-[11px] text-slate-500">Último ping {hhmm(list.info.last_cycle)} · {list.info.devices} equip. em {list.info.duration_s}s</div>}
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-3 md:px-4 py-2.5 border-b border-line flex flex-wrap items-center gap-2">
          <select className="lg:hidden h-8 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100" value={mapId} onChange={e => open(e.target.value)} aria-label="Mapa">
            {(list?.maps || []).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          <nav className="hidden lg:flex items-center gap-1 text-sm min-w-0" aria-label="Caminho">
            {trail.filter(id => byId[id]).map((id, i, arr) => (
              <React.Fragment key={id}>
                {i > 0 && <ChevronRight className="w-3.5 h-3.5 text-slate-600 shrink-0" />}
                <button onClick={() => { setTrail(arr.slice(0, i + 1)); open(id); }} className={`truncate ${id === mapId ? "text-slate-100 font-semibold" : "text-slate-400 hover:text-slate-200"}`}>{byId[id].name}</button>
              </React.Fragment>
            ))}
          </nav>
          {sum && sum.total > 0 && (
            <div className="flex items-center gap-3 ml-2" data-testid="av-summary">
              <div className="flex h-2 w-40 rounded-full overflow-hidden bg-slate-800" aria-hidden="true">
                {["down", "warn", "up", "pending"].map(k => sum[k] ? <div key={k} style={{ width: `${(sum[k] / sum.total) * 100}%`, background: C[k] }} /> : null)}
              </div>
              <span className="text-xs text-slate-300 font-mono"><span style={{ color: C.up }}>{sum.up}</span> / {sum.total}</span>
              {sum.down > 0 && <span className="text-xs font-semibold" style={{ color: C.down }}>{sum.down} sem resposta</span>}
              {sum.links_down > 0 && <span className="text-xs font-semibold" style={{ color: C.down }}>{sum.links_down} rota(s) fora</span>}
              {sum.warn > 0 && <span className="text-xs" style={{ color: C.warn }}>{sum.warn} instável</span>}
            </div>
          )}
          <div className="ml-auto flex items-center gap-1.5">
            {!editing && <>
              {!alarmsOpen && <Button size="sm" variant="outline" onClick={toggleAlarms} className={`${tb} hidden md:inline-flex`} data-testid="av-alarms-open">
                Incidentes{live?.alarms?.length ? <span className="ml-1.5 px-1.5 rounded-full text-[10px] font-bold text-white" style={{ background: live.summary?.down || live.summary?.links_down ? C.down : C.warn }}>{live.alarms.length}</span> : null}</Button>}
              <Button size="sm" variant="outline" onClick={toggleSound} className={tb} title={sound ? "Som ligado: apita quando algo para de responder" : "Ligar som de alarme"} aria-label="Som">
                {sound ? <Volume2 className="w-3.5 h-3.5" /> : <VolumeX className="w-3.5 h-3.5" />}</Button>
              <Button size="sm" variant="outline" onClick={() => api.post("/avail/ping-now").then(() => toast.info("Pingando agora"))} className={tb}><Zap className="w-3.5 h-3.5 mr-1" />Pingar agora</Button>
              <Button size="sm" variant="outline" onClick={full} className={tb} aria-label="Tela cheia"><Maximize2 className="w-3.5 h-3.5" /></Button>
              {canEdit && map && <Button size="sm" variant="outline" onClick={() => setDlg("share")} className={tb} data-testid="av-share"><Link2 className="w-3.5 h-3.5 mr-1" />Link do mapa</Button>}
              {list?.can_edit && list?.settings && <Button size="sm" variant="outline" onClick={() => setDlg("settings")} className={tb} aria-label="Ajustes do ping"><Settings2 className="w-3.5 h-3.5" /></Button>}
              {canEdit && map && <Button size="sm" onClick={startEdit} className="h-8 bg-brand hover:bg-brand-strong text-xs" data-testid="av-edit"><Pencil className="w-3.5 h-3.5 mr-1" />Editar</Button>}
            </>}
            {editing && <>
              <div className="flex border border-line rounded overflow-hidden">
                <button onClick={() => setTool("move")} className={`h-8 px-2.5 text-xs flex items-center gap-1 ${tool === "move" ? "bg-brand/25 text-white" : "text-slate-400"}`}><MousePointer2 className="w-3.5 h-3.5" />Mover</button>
                <button onClick={() => setTool("link")} className={`h-8 px-2.5 text-xs flex items-center gap-1 ${tool === "link" ? "bg-brand/25 text-white" : "text-slate-400"}`} data-testid="av-tool-link"><Link2 className="w-3.5 h-3.5" />Ligar</button>
              </div>
              <Button size="sm" variant="outline" onClick={() => setDlg("devices")} className={tb} data-testid="av-add-devices"><Plus className="w-3.5 h-3.5 mr-1" />Equipamentos</Button>
              <Button size="sm" variant="outline" onClick={() => setDlg("submap")} className={tb} data-testid="av-add-submap"><Hexagon className="w-3.5 h-3.5 mr-1" />Submapa</Button>
              <Button size="sm" variant="outline" onClick={addLabel} className={tb}><Type className="w-3.5 h-3.5 mr-1" />Texto</Button>
              <Button size="sm" variant="outline" onClick={() => addBox("note")} className={tb} data-testid="av-add-note"><StickyNote className="w-3.5 h-3.5 mr-1" />Caixa de texto</Button>
              <Button size="sm" variant="outline" onClick={() => addBox("shape")} className={tb} data-testid="av-add-shape"><Shapes className="w-3.5 h-3.5 mr-1" />Forma</Button>
              <Button size="sm" variant="outline" onClick={() => setDlg("style")} className={tb} data-testid="av-style"><Palette className="w-3.5 h-3.5 mr-1" />Aparência</Button>
              {selected && <Button size="sm" variant="outline" onClick={delSel} className={`${tb} text-red-300`}><Trash2 className="w-3.5 h-3.5 mr-1" />Tirar</Button>}
              <Button size="sm" variant="ghost" onClick={delMap} className="h-8 text-xs text-red-300">Excluir mapa</Button>
              <Button size="sm" variant="ghost" onClick={() => { setDraft(null); setSelected(null); }} className="h-8 text-xs text-slate-300">Cancelar</Button>
              <Button size="sm" onClick={save} disabled={saving} className="h-8 bg-brand hover:bg-brand-strong text-xs" data-testid="av-save">{saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}Salvar</Button>
            </>}
          </div>
        </div>

        <div className="flex-1 min-h-0 flex">
          <div className="flex-1 min-w-0 relative">
            {cur ? (
              <MapCanvasAvail map={viewMap} live={live} names={names} editing={editing} tool={tool} selected={selected} onSelect={setSelected} fitSignal={fitSig}
                              onEnter={enterSub}
                              onPatchNode={patchNode}
                              onLinkPoints={(id, pts) => setDraft(d => ({ ...d, links: d.links.map(l => (l.id === id ? { ...l, points: pts || [] } : l)) }))}
                              onMove={(id, x, y) => setDraft(d => ({ ...d, nodes: d.nodes.map(n => (n.id === id ? { ...n, x, y } : n)) }))}
                              onConnect={(a, b) => { const id = uid(); patch(d => { d.links.push({ id, from: a, to: b, label: "", snmp: null }); return d; }); setSelected({ link: id }); }} />
            ) : (
              <div className="h-full flex items-center justify-center text-sm text-slate-500" style={{ background: C.bg0 }}>
                {list ? (canEdit ? "Crie o primeiro mapa no + da lista." : "Nenhum mapa ainda.") : <Loader2 className="w-5 h-5 animate-spin" />}
              </div>
            )}
          {((sel && (editing || (sel.kind !== "note" && sel.kind !== "shape"))) || selected?.link) && (
            <aside className="absolute top-3 right-3 w-72 max-h-[calc(100%-4.5rem)] rounded-lg border p-4 space-y-3 overflow-y-auto hidden md:block shadow-2xl" style={{ background: `${C.panel}f2`, borderColor: C.line }} data-testid="av-inspector">
              <button onClick={() => setSelected(null)} className="float-right text-slate-500 hover:text-slate-200" aria-label="Fechar"><X className="w-4 h-4" /></button>
              {selected?.link && cur.links.find(l => l.id === selected.link) && (
                <LinkPanel link={cur.links.find(l => l.id === selected.link)} map={cur} live={live?.links?.[selected.link]} devices={devices} names={names} editing={editing}
                           onChange={(p) => setDraft(d => ({ ...d, links: d.links.map(l => (l.id === selected.link ? { ...l, ...p } : l)) }))} />
              )}
              {sel?.kind === "device" && selLive && <>
                <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full" style={{ background: C[selLive.state] }} />
                  <span className="text-sm font-semibold" style={{ color: C[selLive.state] }}>{STATE_TXT[selLive.state]}</span></div>
                <div><div className="text-base font-semibold text-slate-100 break-words">{selLive.name}</div><div className="font-mono text-sm text-slate-300">{selLive.host}</div></div>
                <dl className="grid grid-cols-2 gap-y-1.5 text-xs">
                  <dt className="text-slate-500">Latência</dt><dd className="font-mono text-slate-200">{selLive.ms != null ? `${selLive.ms} ms` : "—"}</dd>
                  <dt className="text-slate-500">Perda</dt><dd className="font-mono text-slate-200">{selLive.loss != null ? `${Math.round(selLive.loss)}%` : "—"}</dd>
                  <dt className="text-slate-500">Neste estado há</dt><dd className="font-mono text-slate-200">{ago(selLive.since) || "—"}</dd>
                  <dt className="text-slate-500">Último ping</dt><dd className="font-mono text-slate-200">{hhmm(selLive.checked_at) || "—"}</dd>
                  <dt className="text-slate-500">Pingado de</dt><dd className="text-slate-200">{selLive.via_jump ? "jump do equipamento" : "servidor do BastiON"}</dd>
                </dl>
                {selLive.error && selLive.state !== "up" && <div className="text-xs" style={{ color: C.warn }}>{selLive.error}</div>}
                {!!selLive.tags?.length && <div className="flex flex-wrap gap-1">{selLive.tags.map(t => <span key={t} className="text-[10px] px-1.5 py-0.5 rounded border border-line text-slate-400">{t}</span>)}</div>}
                <IgnoreBox key={sel.device_id} deviceId={sel.device_id} live={selLive} canEdit={canEdit} onDone={refreshLive} />
              </>}
              {sel?.kind === "submap" && selLive && <>
                <div className="text-base font-semibold text-slate-100">{selLive.name}</div>
                <dl className="grid grid-cols-2 gap-y-1.5 text-xs">
                  {["up", "warn", "down", "pending"].map(k => <React.Fragment key={k}><dt className="text-slate-500">{STATE_TXT[k]}</dt><dd className="font-mono" style={{ color: selLive[k] ? C[k] : undefined }}>{selLive[k]}</dd></React.Fragment>)}
                </dl>
                {selLive.down_names?.length > 0 && <div className="text-xs space-y-0.5" style={{ color: C.down }}>{selLive.down_names.map(n => <div key={n}>• {n}</div>)}</div>}
                {editing && <Button size="sm" variant="outline" onClick={() => setDlg({ devicesTo: sel.map_id })} className="w-full h-8 border-line bg-transparent text-xs" data-testid="av-sub-add">
                  <Plus className="w-3.5 h-3.5 mr-1" />Colocar equipamentos dentro</Button>}
                <Button size="sm" onClick={() => enterSub(sel.map_id)} className="w-full h-8 bg-brand hover:bg-brand-strong text-xs" data-testid="av-sub-enter">{editing ? "Salvar e abrir o submapa" : "Abrir o submapa"}</Button>
              </>}
              {editing && (sel?.kind === "note" || sel?.kind === "shape") && <BoxPanel node={sel} onChange={(p) => patchNode(sel.id, p)} />}
              {editing && sel && (sel.kind === "device" || sel.kind === "submap") && (
                <label className="block text-xs text-slate-400 space-y-1 pt-2 border-t border-line">{sel.kind === "submap" ? "Nome do submapa (muda também no menu ao salvar)" : "Nome mostrado no mapa (opcional)"}
                  <Input value={sel.label || (sel.kind === "submap" ? byId[sel.map_id]?.name : "") || ""} onChange={e => setDraft(d => ({ ...d, nodes: d.nodes.map(n => (n.id === sel.id ? { ...n, label: e.target.value } : n)) }))} className="h-8 bg-sunken border-line text-sm" data-testid="av-node-name" /></label>
              )}
              {editing && sel?.kind === "label" && (
                <Input value={sel.label} onChange={e => setDraft(d => ({ ...d, nodes: d.nodes.map(n => (n.id === sel.id ? { ...n, label: e.target.value } : n)) }))} className="h-8 bg-sunken border-line text-sm" />
              )}
            </aside>
          )}
          </div>
          {alarmsOpen && !editing && cur && <div className="hidden md:flex"><AlarmPanel live={live} mapId={mapId} onPick={pickAlarm} onClose={toggleAlarms} /></div>}
        </div>
      </div>

      {(dlg === "devices" || dlg?.devicesTo) && cur && <AddDevices devices={devices} current={cur} maps={list?.maps || []} names={names} initialTarget={dlg?.devicesTo}
                                                         onAddHere={addDevices} onAddedElsewhere={() => { setDlg(null); loadList(); }} onClose={() => setDlg(null)} />}
      {dlg === "submap" && cur && <AddSubmap maps={list?.maps || []} current={cur} onPick={addSubmap} onClose={() => setDlg(null)} />}
      {dlg === "share" && map && <ShareDlg map={map} onClose={() => setDlg(null)} />}
      {dlg === "style" && draft && <StyleDlg style={viewStyle} isSub={isSub} own={styleOwn(draft.style)}
        onOwn={(v) => setDraft(d => ({ ...d, style: { ...(v ? viewStyle : d.style), own: v } }))}
        onChange={(st) => setDraft(d => ({ ...d, style: { ...st, own: isSub ? true : st.own }, bg: st.bg, style_to_subs: d.style_to_subs ?? true }))}
        toSubs={draft.style_to_subs ?? true} onToSubs={(v) => setDraft(d => ({ ...d, style_to_subs: v }))} onClose={() => setDlg(null)} />}
      {dlg === "dude" && <DudeImport onClose={() => setDlg(null)} onDone={async (openId) => { setDlg(null); await loadList(); api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {}); if (openId) { setTrail([]); setMapId(openId); } }} />}
      {dlg === "settings" && <SettingsDlg init={list.settings} onClose={() => setDlg(null)} onSaved={(s) => { setList(l => ({ ...l, settings: s })); setDlg(null); }} />}
    </div>
  );
}

// dois bipes curtos (WebAudio) quando aumenta o número de equipamentos sem resposta
function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.22].forEach((t, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.value = i ? 660 : 880;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + t); g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.18);
      o.connect(g).connect(ctx.destination); o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.2);
    });
    setTimeout(() => ctx.close(), 800);
  } catch { /* sem áudio */ }
}
