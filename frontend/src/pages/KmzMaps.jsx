import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Globe, Upload, Layers, Search, Trash2, RefreshCw, X, Plus, Minus, Maximize2, Palette, MapPin, Copy } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

// ------------------------------------------------------------------ mapa base e projeção (Web Mercator, mundo de 256 px no zoom 0)
const BASES = {
  dark: { label: "Escuro", url: "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", max: 20, attr: "© OpenStreetMap · © CARTO" },
  light: { label: "Claro", url: "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png", max: 20, attr: "© OpenStreetMap · © CARTO" },
  satellite: { label: "Satélite", url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", max: 19, attr: "Imagens © Esri, Maxar" },
  streets: { label: "Ruas", url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", max: 19, attr: "© colaboradores do OpenStreetMap" },
};
const SAT_LABELS = "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}";
const wx = (lon) => ((lon + 180) / 360) * 256;
const wy = (lat) => { const s = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256; };
const unproj = (x, y) => { const n = Math.PI - (2 * Math.PI * y) / 256; return { lat: (180 / Math.PI) * Math.atan(Math.sinh(n)), lon: (x / 256) * 360 - 180 }; };
const tile = (t, z, x, y) => t.replace("{z}", z).replace("{x}", x).replace("{y}", y);

const KIND_OPTS = [["auto", "Automático"], ["pop", "POP"], ["device", "Equipamento"], ["box", "Caixa / CTO"], ["point", "Ponto"], ["hidden", "Ocultar"]];
const KIND_TXT = { pop: "POP", device: "Equipamento", box: "Caixa / CTO", point: "Ponto", route: "Rota", area: "Área" };
const ROUTE_PALETTE = ["#22d3ee", "#a78bfa", "#f59e0b", "#34d399", "#f472b6", "#60a5fa", "#facc15", "#fb7185", "#4ade80", "#c084fc"];
const STATE = { up: "#2dd4bf", warn: "#f5b544", down: "#ff4d5e", pending: "#7b8ca1", ignored: "#5b6f86" };
const STATE_TXT = { up: "respondendo", warn: "instável", down: "sem resposta", pending: "sem leitura", ignored: "ignorado" };
const STYLE_DEF = { base: "dark", labels: "auto", boxes_zoom: 15, glow: true, route_w: 2.5, colors: "kml" };

// cor clara o bastante para aparecer no fundo escuro/satélite
function visible(hex, dark) {
  if (!hex || !/^#[0-9a-f]{6}$/i.test(hex)) return null;
  const n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const l = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  if (dark && l < 0.35) { const k = 0.45 / Math.max(l, 0.05); return `rgb(${Math.min(255, r * k + 40)},${Math.min(255, g * k + 40)},${Math.min(255, b * k + 40)})`; }
  if (!dark && l > 0.85) return `rgb(${r * 0.6},${g * 0.6},${b * 0.6})`;
  return hex;
}

// ------------------------------------------------------------------ página
export default function KmzMaps() {
  const { user } = useAuth();
  const canEdit = user?.role !== "viewer";
  const [list, setList] = useState(null);
  const [curId, setCurId] = useState(() => { try { return localStorage.getItem("kmz_cur") || ""; } catch { return ""; } });
  const [meta, setMeta] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [live, setLive] = useState({});
  const [sel, setSel] = useState(null);
  const [panel, setPanel] = useState(null);        // "layers" | "style" | null
  const [imp, setImp] = useState(null);            // diálogo de importação {mode:"new"|"re"}
  const [tags, setTags] = useState([]);
  const [q, setQ] = useState("");
  const [fly, setFly] = useState(null);

  const loadList = useCallback(() => api.get("/kmz").then(r => { setList(r.data); return r.data; }), []);
  useEffect(() => {
    loadList().then(rows => { if (rows.length && !rows.some(m => m.id === curId)) setCurId(rows[0].id); }).catch(e => toast.error(formatApiError(e)));
    api.get("/devices").then(r => setTags([...new Set(r.data.flatMap(d => d.tags || []))].sort((a, b) => a.localeCompare(b, "pt-BR")))).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!curId) { setMeta(null); setData(null); return undefined; }
    try { localStorage.setItem("kmz_cur", curId); } catch { /* ok */ }
    let alive = true;
    setLoading(true); setSel(null); setData(null);
    Promise.all([api.get(`/kmz/${curId}`), api.get(`/kmz/${curId}/data`)])
      .then(([m, d]) => { if (alive) { setMeta(m.data); setData(d.data); setFly({ fit: true, t: Date.now() }); } })
      .catch(e => alive && toast.error(formatApiError(e))).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [curId]);

  useEffect(() => {
    if (!curId || !meta?.match?.matched) { setLive({}); return undefined; }
    let alive = true;
    const go = () => api.get(`/kmz/${curId}/live`).then(r => alive && setLive(r.data.devices || {})).catch(() => {});
    go(); const t = setInterval(go, 20000);
    return () => { alive = false; clearInterval(t); };
  }, [curId, meta?.match?.matched]);

  const style = { ...STYLE_DEF, ...(meta?.style || {}) };
  const save = async (patch) => {
    setMeta(m => ({ ...m, ...patch }));
    try {
      const r = await api.put(`/kmz/${curId}`, patch);
      setMeta(r.data); loadList();
      if (patch.tag !== undefined) { const d = await api.get(`/kmz/${curId}/data`); setData(d.data); toast.success(`${r.data.match?.matched || 0} equipamento(s) da tag encontrados no mapa`); }
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async () => {
    if (!window.confirm(`Apagar o mapa "${meta.name}"? O arquivo original não é afetado.`)) return;
    try { await api.delete(`/kmz/${curId}`); const rows = await loadList(); setCurId(rows[0]?.id || ""); } catch (e) { toast.error(formatApiError(e)); }
  };

  const folders = meta?.folders || [];
  const fById = useMemo(() => Object.fromEntries(folders.map((f, i) => [f.id, { ...f, i }])), [folders]);
  // tipo valendo de cada ponto: o da pasta (se escolhido) ou o automático; equipamento casado continua equipamento
  const feats = useMemo(() => {
    if (!data) return null;
    const vis = (f) => { const fo = fById[f]; return fo && fo.visible !== false && fo.kind !== "hidden"; };
    const kindOf = (p) => { const fk = fById[p.f]?.kind || "auto"; return p.kind === "device" ? "device" : fk !== "auto" ? fk : p.kind; };
    return {
      points: data.points.filter(p => vis(p.f)).map(p => ({ ...p, k: kindOf(p), X: wx(p.lon), Y: wy(p.lat) })),
      lines: data.lines.filter(l => vis(l.f)).map(l => projLine(l, fById[l.f]?.i || 0)),
      polys: data.polys.filter(a => vis(a.f)).map(a => projLine(a, fById[a.f]?.i || 0)),
    };
  }, [data, fById]);
  const counts = useMemo(() => {
    const c = { pop: 0, device: 0, box: 0, point: 0, down: 0 };
    (feats?.points || []).forEach(p => { c[p.k] = (c[p.k] || 0) + 1; if (p.k === "device" && live[p.device_id]?.state === "down") c.down += 1; });
    return c;
  }, [feats, live]);
  const results = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t || !feats) return [];
    const out = [];
    for (const p of feats.points) { if (`${p.name} ${live[p.device_id]?.host || ""}`.toLowerCase().includes(t)) out.push({ type: "point", f: p }); if (out.length >= 30) break; }
    for (const l of feats.lines) { if (out.length >= 40) break; if ((l.name || "").toLowerCase().includes(t)) out.push({ type: "line", f: l }); }
    return out;
  }, [q, feats, live]);
  const goTo = (r) => { setSel(r); setQ(""); setFly({ target: r, t: Date.now() }); };

  return (
    <div className="flex-1 flex min-h-0 bg-canvas" data-testid="kmz-page">
      <aside className="hidden lg:flex w-60 shrink-0 flex-col border-r border-line">
        <div className="px-3 pt-4 pb-2 flex items-center gap-2">
          <h1 className="font-heading text-lg font-semibold text-slate-100 flex-1">Mapas KMZ</h1>
          {canEdit && <Button size="sm" onClick={() => setImp({ mode: "new" })} className="h-8 bg-brand hover:bg-brand-strong" data-testid="kmz-import"><Upload className="w-3.5 h-3.5 mr-1" />Importar</Button>}
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          {list?.length === 0 && <div className="px-2 py-6 text-xs text-slate-500">Nenhum mapa ainda. Importe um KML ou KMZ do Google Earth.</div>}
          {(list || []).map(m => (
            <button key={m.id} onClick={() => setCurId(m.id)} data-testid={`kmz-item-${m.id}`}
              className={`w-full text-left rounded-md px-2.5 py-2 ${m.id === curId ? "bg-white/[0.07] text-slate-100" : "text-slate-300 hover:bg-white/[0.04]"}`}>
              <div className="text-sm truncate">{m.name}</div>
              <div className="text-[11px] text-slate-500 truncate">{m.tag || "todas as tags"} · {m.summary?.points || 0} pontos · {m.summary?.km || 0} km</div>
            </button>
          ))}
        </div>
      </aside>

      <div className="flex-1 min-w-0 flex flex-col">
        <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-line">
          <select value={curId} onChange={e => setCurId(e.target.value)} className="lg:hidden h-8 max-w-[45vw] rounded-md bg-sunken border border-line text-sm text-slate-100 px-2" aria-label="Mapa">
            {(list || []).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          {meta && <>
            <div className="hidden lg:block min-w-0">
              <div className="text-sm font-semibold text-slate-100 truncate">{meta.name}</div>
              <div className="text-[11px] text-slate-500 truncate">{meta.source}</div>
            </div>
            <label className="flex items-center gap-1.5 text-xs text-slate-400">Empresa (tag)
              <select value={meta.tag || ""} disabled={!canEdit} onChange={e => save({ tag: e.target.value })} data-testid="kmz-tag"
                className="h-8 rounded-md bg-sunken border border-line text-sm text-slate-100 px-2">
                <option value="">todas</option>
                {[...new Set([...tags, meta.tag].filter(Boolean))].map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
            <div className="flex items-center gap-3 text-xs text-slate-400 ml-1">
              <Stat c="#e2e8f0" n={counts.pop} l="POPs" /><Stat c={STATE.up} n={counts.device} l="equip." />
              {counts.down > 0 && <Stat c={STATE.down} n={counts.down} l="sem resposta" />}
              <Stat c="#94a3b8" n={counts.box} l="caixas" /><Stat c="#22d3ee" n={meta.summary?.km || 0} l="km de rota" />
            </div>
          </>}
          <div className="ml-auto flex items-center gap-1.5">
            {meta && <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar POP, caixa, rota, IP…" data-testid="kmz-search"
                className="h-8 w-52 pl-8 pr-2 rounded-md bg-sunken border border-line text-sm text-slate-100 focus:outline-none focus:border-brand" />
              {results.length > 0 && (
                <div className="absolute right-0 top-9 z-30 w-72 max-h-80 overflow-y-auto rounded-md border border-line bg-panel shadow-2xl">
                  {results.map((r, i) => (
                    <button key={i} onClick={() => goTo(r)} className="w-full text-left px-3 py-1.5 hover:bg-white/[0.05]">
                      <div className="text-sm text-slate-100 truncate">{r.f.name || "(sem nome)"}</div>
                      <div className="text-[11px] text-slate-500 truncate">{r.type === "line" ? `rota · ${r.f.km} km` : KIND_TXT[r.f.k]} · {fById[r.f.f]?.path}</div>
                    </button>))}
                </div>)}
            </div>}
            {meta && <IconBtn on={panel === "layers"} onClick={() => setPanel(p => (p === "layers" ? null : "layers"))} title="Camadas (pastas do KML)" testid="kmz-layers"><Layers className="w-4 h-4" /></IconBtn>}
            {meta && canEdit && <IconBtn on={panel === "style"} onClick={() => setPanel(p => (p === "style" ? null : "style"))} title="Aparência" testid="kmz-style"><Palette className="w-4 h-4" /></IconBtn>}
            {meta && canEdit && <IconBtn onClick={() => setImp({ mode: "re" })} title="Atualizar com um arquivo novo" testid="kmz-reimport"><RefreshCw className="w-4 h-4" /></IconBtn>}
            {meta && canEdit && <IconBtn onClick={del} title="Apagar mapa" testid="kmz-delete"><Trash2 className="w-4 h-4 text-red-400" /></IconBtn>}
            {canEdit && <Button size="sm" onClick={() => setImp({ mode: "new" })} className="lg:hidden h-8 bg-brand hover:bg-brand-strong"><Upload className="w-3.5 h-3.5" /></Button>}
          </div>
        </div>

        <div className="flex-1 min-h-0 relative">
          {!curId && list && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-center p-6">
              <Globe className="w-10 h-10 text-slate-600" />
              <div className="text-slate-200 font-medium">Importe o KML ou KMZ da sua rede</div>
              <div className="text-sm text-slate-500 max-w-md">POPs, equipamentos, caixas e rotas de fibra do Google Earth viram um mapa com o estado dos equipamentos da empresa (tag) que você escolher.</div>
              {canEdit && <Button onClick={() => setImp({ mode: "new" })} className="bg-brand hover:bg-brand-strong"><Upload className="w-4 h-4 mr-1.5" />Importar KML/KMZ</Button>}
            </div>
          )}
          {curId && loading && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-500">Carregando o mapa…</div>}
          {feats && meta && <GeoView feats={feats} bbox={meta.bbox} style={style} live={live} sel={sel} onSel={setSel} fly={fly} />}
          {panel === "layers" && meta && <LayersPanel folders={folders} canEdit={canEdit} onClose={() => setPanel(null)} onChange={(fs) => save({ folders: fs })} />}
          {panel === "style" && meta && <StylePanel style={style} onClose={() => setPanel(null)} onChange={(st) => save({ style: st })} />}
          {sel && <InfoPanel sel={sel} live={live} folder={fById[sel.f.f]} onClose={() => setSel(null)} />}
          {meta?.match && meta.tag && meta.match.matched === 0 && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200">
              Nenhum ponto do KML casou com os {meta.match.devices} equipamento(s) da tag {meta.tag}. O casamento é pelo nome do marcador ou por um IP no nome/descrição.
            </div>)}
        </div>
      </div>
      {imp && <ImportDlg mode={imp.mode} cur={meta} tags={tags} onClose={() => setImp(null)}
        onDone={async (m) => { setImp(null); await loadList(); if (m.id === curId) { setCurId(""); setTimeout(() => setCurId(m.id), 0); } else setCurId(m.id); }} />}
    </div>
  );
}

function projLine(l, fi) {
  const P = new Float64Array(l.pts.length * 2);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  l.pts.forEach(([a, b], i) => { const x = wx(b), y = wy(a); P[i * 2] = x; P[i * 2 + 1] = y; x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); });
  return { ...l, P, bb: [x0, y0, x1, y1], fi };
}

const Stat = ({ c, n, l }) => <span className="whitespace-nowrap"><b className="font-mono" style={{ color: c }}>{n}</b> {l}</span>;
const IconBtn = ({ on, children, testid, ...r }) => (
  <button {...r} data-testid={testid} className={`w-8 h-8 inline-flex items-center justify-center rounded-md border ${on ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-300 hover:bg-white/[0.05]"}`}>{children}</button>
);

// ------------------------------------------------------------------ desenho do mapa (tiles em <img> + camadas num <canvas>)
function GeoView({ feats, bbox, style, live, sel, onSel, fly }) {
  const wrap = useRef(null), cvs = useRef(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [measured, setMeasured] = useState(false);
  const [v, setV] = useState({ z: 4, cx: 128, cy: 128 });
  const vRef = useRef(v); vRef.current = v;
  const base = BASES[style.base] || BASES.dark;
  const dark = style.base !== "light" && style.base !== "streets";

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => { setSize({ w: e.contentRect.width, h: e.contentRect.height }); setMeasured(true); });
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);
  const fitBox = useCallback((x0, y0, x1, y1, maxZ = 17) => {
    const { w, h } = size;
    const z = Math.min(maxZ, Math.log2(Math.min((w - 80) / Math.max(1e-9, x1 - x0), (h - 80) / Math.max(1e-9, y1 - y0))));
    setV({ z: Math.max(2, Math.min(20, z)), cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 });
  }, [size]);
  const fitAll = useCallback(() => { if (bbox) fitBox(wx(bbox.w), wy(bbox.n), wx(bbox.e), wy(bbox.s)); }, [bbox, fitBox]);
  useEffect(() => {
    if (!fly || !measured) return;
    if (fly.fit) fitAll();
    else if (fly.target?.type === "point") setV(o => ({ z: Math.max(o.z, 16), cx: fly.target.f.X, cy: fly.target.f.Y }));
    else if (fly.target?.type === "line") { const [a, b, c, d] = fly.target.f.bb; fitBox(a, b, c, d, 17); }
  }, [fly, measured]); // eslint-disable-line react-hooks/exhaustive-deps

  const S = 2 ** v.z;
  const toS = (x, y) => [(x - v.cx) * S + size.w / 2, (y - v.cy) * S + size.h / 2];

  // ---- desenho
  const anyDown = useMemo(() => feats.points.some(p => p.k === "device" && live[p.device_id]?.state === "down"), [feats, live]);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!anyDown || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    const t = setInterval(() => setTick(x => x + 1), 80);
    return () => clearInterval(t);
  }, [anyDown]);

  useEffect(() => {
    const c = cvs.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(size.w * dpr)) { c.width = Math.round(size.w * dpr); c.height = Math.round(size.h * dpr); }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    const vx0 = v.cx - size.w / 2 / S, vx1 = v.cx + size.w / 2 / S, vy0 = v.cy - size.h / 2 / S, vy1 = v.cy + size.h / 2 / S;
    const inView = (bb) => bb[2] >= vx0 && bb[0] <= vx1 && bb[3] >= vy0 && bb[1] <= vy1;
    const path = (P) => { g.beginPath(); for (let i = 0; i < P.length; i += 2) { const x = (P[i] - v.cx) * S + size.w / 2, y = (P[i + 1] - v.cy) * S + size.h / 2; i ? g.lineTo(x, y) : g.moveTo(x, y); } };
    const colorOf = (l) => (style.colors === "kml" && visible(l.color, dark)) || ROUTE_PALETTE[l.fi % ROUTE_PALETTE.length];
    // áreas
    for (const a of feats.polys) {
      if (!inView(a.bb)) continue;
      path(a.P); g.closePath();
      const col = (style.colors === "kml" && visible(a.fill || a.color, dark)) || ROUTE_PALETTE[a.fi % ROUTE_PALETTE.length];
      g.globalAlpha = 0.12; g.fillStyle = col; g.fill(); g.globalAlpha = 0.7; g.strokeStyle = col; g.lineWidth = 1.2; g.setLineDash([5, 4]); g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
    }
    // rotas
    g.lineJoin = "round"; g.lineCap = "round";
    const rw = style.route_w * (v.z < 10 ? 0.7 : v.z > 15 ? 1.25 : 1);
    for (const l of feats.lines) {
      if (!inView(l.bb)) continue;
      const col = colorOf(l), w = l.width ? Math.max(1, Math.min(8, l.width)) * (rw / 2.5) : rw;
      if (style.glow) { path(l.P); g.strokeStyle = col; g.globalAlpha = 0.18; g.lineWidth = w + 6; g.stroke(); g.globalAlpha = 1; }
      path(l.P); g.strokeStyle = col; g.lineWidth = w; g.stroke();
    }
    if (sel?.type === "line") { path(sel.f.P); g.strokeStyle = "#fff"; g.lineWidth = rw + 3; g.globalAlpha = 0.9; g.stroke(); g.globalAlpha = 1; }
    // pontos (caixas primeiro, POPs/equipamentos por cima)
    const showBoxes = v.z >= style.boxes_zoom;
    const order = { box: 0, point: 1, pop: 2, device: 3 };
    const pts = feats.points.filter(p => (p.k !== "box" || showBoxes) && p.X >= vx0 - 20 / S && p.X <= vx1 + 20 / S && p.Y >= vy0 - 20 / S && p.Y <= vy1 + 20 / S)
      .sort((a, b) => order[a.k] - order[b.k]);
    const pulse = (tick % 25) / 25;
    for (const p of pts) {
      const [x, y] = toS(p.X, p.Y);
      if (p.k === "box") {
        const s = v.z >= 17 ? 4.5 : 3.2;
        g.fillStyle = visible(p.color, dark) || (dark ? "#cbd5e1" : "#475569"); g.strokeStyle = dark ? "#0b1220" : "#fff"; g.lineWidth = 1;
        g.fillRect(x - s, y - s, s * 2, s * 2); g.strokeRect(x - s, y - s, s * 2, s * 2);
      } else if (p.k === "point") {
        g.beginPath(); g.arc(x, y, 3.5, 0, 7); g.fillStyle = visible(p.color, dark) || "#94a3b8"; g.fill(); g.lineWidth = 1; g.strokeStyle = dark ? "#0b1220" : "#fff"; g.stroke();
      } else if (p.k === "pop") {
        const r = v.z >= 12 ? 11 : 8;
        g.beginPath();
        for (let i = 0; i < 6; i++) { const an = Math.PI / 6 + (i * Math.PI) / 3; i ? g.lineTo(x + r * Math.cos(an), y + r * Math.sin(an)) : g.moveTo(x + r * Math.cos(an), y + r * Math.sin(an)); }
        g.closePath(); g.fillStyle = dark ? "#0d1d2e" : "#ffffff"; g.fill(); g.lineWidth = 2.4; g.strokeStyle = dark ? "#e2e8f0" : "#0f172a"; g.stroke();
        g.beginPath(); g.arc(x, y, r * 0.32, 0, 7); g.fillStyle = dark ? "#e2e8f0" : "#0f172a"; g.fill();
      } else {
        const st = live[p.device_id]?.state || "pending", col = STATE[st] || STATE.pending, r = v.z >= 12 ? 8 : 6;
        if (st === "down") { g.beginPath(); g.arc(x, y, r + 4 + pulse * 14, 0, 7); g.strokeStyle = col; g.globalAlpha = 1 - pulse; g.lineWidth = 2; g.stroke(); g.globalAlpha = 1; }
        g.beginPath(); g.arc(x, y, r + 3, 0, 7); g.fillStyle = col; g.globalAlpha = 0.22; g.fill(); g.globalAlpha = 1;
        g.beginPath(); g.arc(x, y, r, 0, 7); g.fillStyle = dark ? "#0b1622" : "#fff"; g.fill(); g.lineWidth = 2.6; g.strokeStyle = col; g.stroke();
        g.beginPath(); g.arc(x, y, r * 0.42, 0, 7); g.fillStyle = col; g.fill();
      }
      if (sel?.type === "point" && sel.f.id === p.id) { g.beginPath(); g.arc(x, y, 15, 0, 7); g.strokeStyle = "#fff"; g.lineWidth = 2; g.setLineDash([4, 3]); g.stroke(); g.setLineDash([]); }
    }
    // nomes (sem sobrepor: grade de ocupação)
    if (style.labels !== "never") {
      const minZ = style.labels === "always" ? { pop: 0, device: 0, box: style.boxes_zoom, point: 0 } : { pop: 8, device: 11, box: 17, point: 14 };
      const used = new Set();
      g.font = "600 11px Inter, system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "top";
      for (const p of [...pts].reverse()) {
        if (!p.name || v.z < minZ[p.k]) continue;
        const [x, y] = toS(p.X, p.Y);
        const name = p.k === "device" ? live[p.device_id]?.name || p.name : p.name;
        const tw = g.measureText(name).width, ty = y + (p.k === "box" ? 7 : 14);
        const cells = [];
        for (let cx = Math.floor((x - tw / 2) / 24); cx <= Math.floor((x + tw / 2) / 24); cx++) cells.push(`${cx}:${Math.floor(ty / 14)}`);
        if (cells.some(k => used.has(k))) continue;
        cells.forEach(k => used.add(k));
        g.lineWidth = 3.5; g.strokeStyle = dark ? "rgba(5,10,20,.9)" : "rgba(255,255,255,.95)"; g.strokeText(name, x, ty);
        g.fillStyle = p.k === "device" ? (STATE[live[p.device_id]?.state] || (dark ? "#e2e8f0" : "#0f172a")) : dark ? "#e2e8f0" : "#0f172a";
        g.fillText(name, x, ty);
      }
    }
  });

  // ---- interação: arrastar, roda do mouse, pinça, clique
  const ptrs = useRef(new Map());
  const drag = useRef(null);
  const zoomAt = (nz, sx, sy) => setV(o => {
    const z = Math.max(2, Math.min(base.max + 1, nz)), k0 = 2 ** o.z, k1 = 2 ** z;
    const mx = o.cx + (sx - size.w / 2) / k0, my = o.cy + (sy - size.h / 2) / k0;
    return { z, cx: mx - (sx - size.w / 2) / k1, cy: my - (sy - size.h / 2) / k1 };
  });
  useEffect(() => {
    const el = wrap.current;
    const wheel = (e) => { e.preventDefault(); const r = el.getBoundingClientRect(); zoomAt(vRef.current.z - e.deltaY * 0.0022, e.clientX - r.left, e.clientY - r.top); };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }); // eslint-disable-line react-hooks/exhaustive-deps
  const local = (e) => { const r = wrap.current.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const down = (e) => {
    wrap.current.setPointerCapture?.(e.pointerId);
    ptrs.current.set(e.pointerId, local(e));
    if (ptrs.current.size === 2) {
      const [a, b] = [...ptrs.current.values()];
      drag.current = { pinch: true, d0: Math.hypot(a.x - b.x, a.y - b.y) || 1, v0: vRef.current, m0: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, moved: true };
    } else drag.current = { start: local(e), v0: vRef.current, moved: false };
  };
  const move = (e) => {
    if (!ptrs.current.has(e.pointerId) || !drag.current) return;
    ptrs.current.set(e.pointerId, local(e));
    const d = drag.current;
    if (d.pinch && ptrs.current.size >= 2) {
      const [a, b] = [...ptrs.current.values()];
      const z = Math.max(2, Math.min(base.max + 1, d.v0.z + Math.log2((Math.hypot(a.x - b.x, a.y - b.y) || 1) / d.d0)));
      const k0 = 2 ** d.v0.z, k1 = 2 ** z, m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const wx0 = d.v0.cx + (d.m0.x - size.w / 2) / k0, wy0 = d.v0.cy + (d.m0.y - size.h / 2) / k0;
      setV({ z, cx: wx0 - (m.x - size.w / 2) / k1, cy: wy0 - (m.y - size.h / 2) / k1 });
    } else if (!d.pinch) {
      const p = local(e), k = 2 ** d.v0.z;
      if (Math.hypot(p.x - d.start.x, p.y - d.start.y) > 4) d.moved = true;
      if (d.moved) setV({ ...d.v0, cx: d.v0.cx - (p.x - d.start.x) / k, cy: d.v0.cy - (p.y - d.start.y) / k });
    }
  };
  const up = (e) => {
    ptrs.current.delete(e.pointerId);
    const d = drag.current;
    if (d && !d.pinch && !d.moved && ptrs.current.size === 0) pick(local(e));
    if (ptrs.current.size === 0 || d?.pinch) drag.current = ptrs.current.size === 1 ? { start: [...ptrs.current.values()][0], v0: vRef.current, moved: true } : null;
  };
  const pick = ({ x, y }) => {
    let best = null, bd = 13;
    for (const p of feats.points) {
      if (p.k === "box" && v.z < style.boxes_zoom) continue;
      const [sx, sy] = toS(p.X, p.Y), dd = Math.hypot(sx - x, sy - y);
      if (dd < bd) { bd = dd; best = { type: "point", f: p }; }
    }
    if (!best) {
      bd = 7;
      for (const l of feats.lines) {
        const P = l.P;
        for (let i = 0; i < P.length - 2; i += 2) {
          const [ax, ay] = toS(P[i], P[i + 1]), [bx, by] = toS(P[i + 2], P[i + 3]);
          const dd = segDist(x, y, ax, ay, bx, by);
          if (dd < bd) { bd = dd; best = { type: "line", f: l }; }
        }
      }
    }
    onSel(best);
  };

  // ---- tiles
  const tz = Math.max(0, Math.min(base.max, Math.round(v.z)));
  const ts = 256 * 2 ** (v.z - tz), n = 2 ** tz;
  const ox = size.w / 2 - v.cx * S, oy = size.h / 2 - v.cy * S;
  const tiles = [];
  const x0 = Math.floor(-ox / ts), x1 = Math.floor((size.w - ox) / ts), y0 = Math.max(0, Math.floor(-oy / ts)), y1 = Math.min(n - 1, Math.floor((size.h - oy) / ts));
  if ((x1 - x0 + 1) * (y1 - y0 + 1) < 400) {
    for (let ty = y0; ty <= y1; ty++) for (let tx = x0; tx <= x1; tx++) {
      const wxT = ((tx % n) + n) % n;
      const st = { left: ox + tx * ts, top: oy + ty * ts, width: ts + 0.6, height: ts + 0.6 };
      tiles.push(<img key={`b${tz}-${tx}-${ty}`} src={tile(base.url, tz, wxT, ty)} alt="" draggable={false} className="absolute max-w-none select-none" style={st} onError={hideImg} />);
      if (style.base === "satellite") tiles.push(<img key={`l${tz}-${tx}-${ty}`} src={tile(SAT_LABELS, tz, wxT, ty)} alt="" draggable={false} className="absolute max-w-none select-none" style={st} onError={hideImg} />);
    }
  }
  const center = unproj(v.cx, v.cy);

  return (
    <div ref={wrap} className="absolute inset-0 overflow-hidden touch-none select-none" style={{ background: dark ? "#0a1018" : "#e5e7eb", cursor: "grab" }}
         onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={(e) => { ptrs.current.delete(e.pointerId); drag.current = null; }}
         onDoubleClick={(e) => { const p = local(e); zoomAt(v.z + 1, p.x, p.y); }} data-testid="kmz-canvas">
      <div className="absolute inset-0" style={{ filter: style.base === "satellite" ? "brightness(.82) saturate(.9)" : undefined }}>{tiles}</div>
      <canvas ref={cvs} className="absolute inset-0" style={{ width: size.w, height: size.h }} />
      <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded-md border border-line bg-panel/90 px-1 py-1 text-xs text-slate-300" onPointerDown={e => e.stopPropagation()}>
        <button onClick={() => zoomAt(v.z + 1, size.w / 2, size.h / 2)} className="w-7 h-7 inline-flex items-center justify-center hover:bg-white/10 rounded" aria-label="Aproximar"><Plus className="w-3.5 h-3.5" /></button>
        <span className="w-8 text-center font-mono">{v.z.toFixed(0)}</span>
        <button onClick={() => zoomAt(v.z - 1, size.w / 2, size.h / 2)} className="w-7 h-7 inline-flex items-center justify-center hover:bg-white/10 rounded" aria-label="Afastar"><Minus className="w-3.5 h-3.5" /></button>
        <button onClick={fitAll} className="w-7 h-7 inline-flex items-center justify-center hover:bg-white/10 rounded" title="Ver o mapa todo" aria-label="Ver o mapa todo"><Maximize2 className="w-3.5 h-3.5" /></button>
      </div>
      <Legend dark={dark} boxes={v.z >= style.boxes_zoom} boxesZoom={style.boxes_zoom} />
      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 px-2 py-0.5 text-[10px] text-slate-400 bg-black/40 rounded-t">
        {base.attr} · {center.lat.toFixed(5)}, {center.lon.toFixed(5)}
      </div>
    </div>
  );
}

const hideImg = (e) => { e.currentTarget.style.visibility = "hidden"; };

function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function Legend({ dark, boxes, boxesZoom }) {
  const ink = dark ? "#e2e8f0" : "#0f172a";
  return (
    <div className="absolute bottom-3 left-3 rounded-md border border-line bg-panel/90 px-3 py-2 text-[11px] text-slate-300 space-y-1" onPointerDown={e => e.stopPropagation()}>
      <div className="flex items-center gap-2"><svg width="14" height="14" viewBox="-7 -7 14 14"><polygon points="0,-6 5.2,-3 5.2,3 0,6 -5.2,3 -5.2,-3" fill="none" stroke={ink} strokeWidth="1.6" /></svg>POP</div>
      <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full border-2" style={{ borderColor: STATE.up }} />equipamento respondendo</div>
      <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full border-2" style={{ borderColor: STATE.down }} />sem resposta</div>
      <div className="flex items-center gap-2"><span className="w-2.5 h-2.5" style={{ background: dark ? "#cbd5e1" : "#475569" }} />caixa / CTO{!boxes && <span className="text-slate-500"> (aparece no zoom {boxesZoom})</span>}</div>
      <div className="flex items-center gap-2"><span className="w-4 h-0.5 rounded" style={{ background: "#22d3ee" }} />rota / cabo</div>
    </div>
  );
}

// ------------------------------------------------------------------ painéis
function InfoPanel({ sel, live, folder, onClose }) {
  const f = sel.f;
  const dv = f.device_id ? live[f.device_id] : null;
  const coord = sel.type === "point" ? `${f.lat}, ${f.lon}` : null;
  const copy = (t) => { try { navigator.clipboard.writeText(t); toast.success("Copiado"); } catch { /* ok */ } };
  return (
    <aside className="absolute top-3 right-3 w-72 max-h-[calc(100%-5rem)] overflow-y-auto rounded-lg border border-line bg-panel/95 p-4 space-y-2.5 shadow-2xl text-sm" data-testid="kmz-info" onPointerDown={e => e.stopPropagation()}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-slate-100 break-words">{dv?.name || f.name || "(sem nome)"}</div>
          <div className="text-[11px] text-slate-500">{sel.type === "line" ? "Rota" : KIND_TXT[f.k]} · {folder?.path}</div>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Fechar"><X className="w-4 h-4" /></button>
      </div>
      {dv && (
        <div className="rounded-md border border-line p-2 text-xs space-y-0.5">
          <div style={{ color: STATE[dv.state] }} className="font-medium">{STATE_TXT[dv.state] || dv.state}{dv.ms != null && dv.state !== "down" ? ` · ${Math.round(dv.ms)} ms` : ""}</div>
          <div className="font-mono text-slate-400">{dv.host}{dv.type ? ` · ${dv.type}` : ""}</div>
          {f.name && f.name !== dv.name && <div className="text-slate-500">no KML: {f.name}</div>}
        </div>
      )}
      {sel.type === "line" && <div className="text-xs text-slate-300">Comprimento: <b className="font-mono text-slate-100">{f.km} km</b></div>}
      {f.desc && <div className="text-xs text-slate-300 whitespace-pre-wrap break-words">{f.desc}</div>}
      {f.ext && Object.keys(f.ext).length > 0 && (
        <dl className="text-xs grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
          {Object.entries(f.ext).map(([k, val]) => <React.Fragment key={k}><dt className="text-slate-500">{k}</dt><dd className="text-slate-200 break-words">{val}</dd></React.Fragment>)}
        </dl>
      )}
      {coord && (
        <div className="flex items-center gap-2 text-xs">
          <MapPin className="w-3.5 h-3.5 text-slate-500" /><span className="font-mono text-slate-300">{coord}</span>
          <button onClick={() => copy(coord)} className="text-slate-500 hover:text-slate-200" aria-label="Copiar coordenada"><Copy className="w-3.5 h-3.5" /></button>
          <a href={`https://www.google.com/maps?q=${f.lat},${f.lon}`} target="_blank" rel="noreferrer" className="ml-auto text-brand-soft hover:underline">Google Maps</a>
        </div>
      )}
    </aside>
  );
}

function LayersPanel({ folders, canEdit, onChange, onClose }) {
  const set = (id, patch) => onChange(folders.map(f => (f.id === id ? { ...f, ...patch } : f)));
  return (
    <aside className="absolute top-3 left-3 w-80 max-h-[calc(100%-6rem)] overflow-y-auto rounded-lg border border-line bg-panel/95 p-3 shadow-2xl" data-testid="kmz-layers-panel" onPointerDown={e => e.stopPropagation()}>
      <div className="flex items-center mb-2"><div className="text-sm font-semibold text-slate-100 flex-1">Camadas (pastas do KML)</div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Fechar"><X className="w-4 h-4" /></button></div>
      <div className="text-[11px] text-slate-500 mb-2">Desmarque para esconder. "Tipo" diz o que tem na pasta quando o automático errar (ex.: pasta de caixas com nomes só numéricos).</div>
      <div className="flex gap-2 mb-2 text-[11px]">
        <button onClick={() => onChange(folders.map(f => ({ ...f, visible: true })))} className="text-slate-400 hover:text-slate-100">mostrar todas</button>
        <button onClick={() => onChange(folders.map(f => ({ ...f, visible: false })))} className="text-slate-400 hover:text-slate-100">esconder todas</button>
      </div>
      <div className="space-y-1">
        {folders.map((f, i) => (
          <div key={f.id} className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-white/[0.03]">
            <input type="checkbox" checked={f.visible !== false} onChange={e => set(f.id, { visible: e.target.checked })} className="accent-brand" aria-label={`Mostrar ${f.path}`} />
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: ROUTE_PALETTE[i % ROUTE_PALETTE.length] }} />
            <div className="min-w-0 flex-1">
              <div className="text-xs text-slate-200 truncate" title={f.path}>{f.path}</div>
              <div className="text-[10px] text-slate-500">{[f.points && `${f.points} pontos`, f.lines && `${f.lines} rotas`, f.polys && `${f.polys} áreas`].filter(Boolean).join(" · ")}</div>
            </div>
            {f.points > 0 && (
              <select value={f.kind || "auto"} disabled={!canEdit} onChange={e => set(f.id, { kind: e.target.value })} className="h-7 rounded bg-sunken border border-line text-[11px] text-slate-200 px-1" aria-label="Tipo">
                {KIND_OPTS.map(([k, l]) => <option key={k} value={k}>{k === "auto" ? `Auto (${KIND_TXT[f.auto] || "—"})` : l}</option>)}
              </select>
            )}
          </div>
        ))}
      </div>
    </aside>
  );
}

function StylePanel({ style, onChange, onClose }) {
  const set = (p) => onChange({ ...style, ...p });
  const Opt = ({ k, v, children }) => (
    <button onClick={() => set({ [k]: v })} className={`px-2 py-1 rounded border text-[11px] ${style[k] === v ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400"}`}>{children}</button>
  );
  return (
    <aside className="absolute top-3 left-3 w-80 rounded-lg border border-line bg-panel/95 p-3 shadow-2xl space-y-3" data-testid="kmz-style-panel" onPointerDown={e => e.stopPropagation()}>
      <div className="flex items-center"><div className="text-sm font-semibold text-slate-100 flex-1">Aparência</div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Fechar"><X className="w-4 h-4" /></button></div>
      <div className="space-y-1"><div className="text-xs text-slate-400">Fundo</div>
        <div className="flex flex-wrap gap-1">{Object.entries(BASES).map(([k, b]) => <Opt key={k} k="base" v={k}>{b.label}</Opt>)}</div></div>
      <div className="space-y-1"><div className="text-xs text-slate-400">Cor das rotas</div>
        <div className="flex gap-1"><Opt k="colors" v="kml">a do KML</Opt><Opt k="colors" v="folder">uma por pasta</Opt></div></div>
      <div className="space-y-1"><div className="text-xs text-slate-400">Nomes</div>
        <div className="flex gap-1"><Opt k="labels" v="auto">pelo zoom</Opt><Opt k="labels" v="always">sempre</Opt><Opt k="labels" v="never">nunca</Opt></div></div>
      <label className="block text-xs text-slate-400 space-y-1">Espessura das rotas · {style.route_w}px
        <input type="range" min="1" max="8" step="0.5" value={style.route_w} onChange={e => set({ route_w: Number(e.target.value) })} className="w-full" /></label>
      <label className="block text-xs text-slate-400 space-y-1">Caixas/CTOs aparecem a partir do zoom {style.boxes_zoom}
        <input type="range" min="10" max="19" step="1" value={style.boxes_zoom} onChange={e => set({ boxes_zoom: Number(e.target.value) })} className="w-full" /></label>
      <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={style.glow} onChange={e => set({ glow: e.target.checked })} /> brilho em volta das rotas</label>
    </aside>
  );
}

function ImportDlg({ mode, cur, tags, onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState(mode === "re" ? cur?.name || "" : "");
  const [tag, setTag] = useState(mode === "re" ? cur?.tag || "" : "");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!file) return toast.error("Escolha o arquivo .kml ou .kmz");
    const fd = new FormData();
    fd.append("file", file); fd.append("name", name); fd.append("tag", tag);
    if (mode === "re" && cur) fd.append("map_id", cur.id);
    setBusy(true);
    try {
      const r = await api.post("/kmz/import", fd, { headers: { "Content-Type": "multipart/form-data" }, timeout: 300000 });
      const s = r.data.summary || {};
      toast.success(`${s.points || 0} pontos, ${s.lines || 0} rotas (${s.km || 0} km)${r.data.tag ? ` · ${r.data.match?.matched || 0} equipamento(s) de ${r.data.tag}` : ""}`);
      onDone(r.data);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={o => !o && !busy && onClose()}>
      <DialogContent className="bg-panel border-line text-slate-100 max-w-lg" data-testid="kmz-import-dlg">
        <DialogHeader><DialogTitle>{mode === "re" ? "Atualizar o mapa com um arquivo novo" : "Importar KML / KMZ"}</DialogTitle></DialogHeader>
        <div className="space-y-3 text-sm">
          <label className="block space-y-1"><span className="text-xs text-slate-400">Arquivo do Google Earth (.kml ou .kmz, até 40 MB)</span>
            <input type="file" accept=".kml,.kmz,application/vnd.google-earth.kml+xml,application/vnd.google-earth.kmz" onChange={e => setFile(e.target.files?.[0] || null)}
              className="block w-full text-xs text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-brand file:px-3 file:py-1.5 file:text-white" data-testid="kmz-file" /></label>
          <label className="block space-y-1"><span className="text-xs text-slate-400">Nome do mapa {mode === "new" && "(vazio = o nome que está no KML)"}</span>
            <Input value={name} onChange={e => setName(e.target.value)} maxLength={80} className="h-9 bg-sunken border-line" /></label>
          <label className="block space-y-1"><span className="text-xs text-slate-400">Empresa (tag dos equipamentos)</span>
            <select value={tag} onChange={e => setTag(e.target.value)} className="w-full h-9 rounded-md bg-sunken border border-line text-sm px-2" data-testid="kmz-import-tag">
              <option value="">todas as tags</option>
              {tags.map(t => <option key={t} value={t}>{t}</option>)}
            </select></label>
          <ul className="text-[11px] text-slate-500 list-disc pl-4 space-y-0.5">
            <li>As pastas do KML viram camadas que você liga e desliga.</li>
            <li>Marcador com o nome ou o IP de um equipamento da tag vira o equipamento, com o estado ao vivo (respondendo / sem resposta).</li>
            <li>CTO, CEO, caixa, poste… viram caixas (aparecem ao aproximar); POP, OLT, torre, site… viram POPs; linhas viram rotas com o comprimento em km.</li>
            {mode === "re" && <li>Atualizar mantém o nome, a aparência e o tipo das pastas com o mesmo caminho.</li>}
          </ul>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancelar</Button>
          <Button onClick={go} disabled={busy || !file} className="bg-brand hover:bg-brand-strong" data-testid="kmz-import-go">{busy ? "Convertendo…" : mode === "re" ? "Atualizar" : "Importar"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
