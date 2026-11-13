import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapCanvas } from "@/components/maps/MapCanvas";
import { geoLayers, toGeoView } from "@/components/maps/geo";
import { fmtBps, UTIL_BANDS, STATUS, NO_DATA } from "@/lib/netfmt";

// Telão (TV): página pública, sem login, aberta por um link /tv/<token>. Só lê /api/public/tv da própria origem.
const LIVE_SEC = 10, INFO_SEC = 300;
const get = async (path) => {
  const r = await fetch(`/api/public/tv/${path}`, { cache: "no-store" });
  let data = null;
  try { data = await r.json(); } catch { /* sem JSON */ }
  if (!r.ok) { const e = new Error((data && typeof data.detail === "string" && data.detail) || "Sem conexão com o BastiON"); e.status = r.status; throw e; }
  return data;
};
const hhmm = (d) => d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export default function TvMaps({ token }) {
  const [info, setInfo] = useState(null);
  const [fatal, setFatal] = useState("");
  const [idx, setIdx] = useState(0);
  const [live, setLive] = useState({});           // {mapId: live}
  const [offline, setOffline] = useState(false);
  const [paused, setPaused] = useState(false);
  const [now, setNow] = useState(new Date());
  const [fit, setFit] = useState(0);
  const [sel, setSel] = useState(null);
  const [lastOk, setLastOk] = useState(null);
  const shownAt = useRef(Date.now());

  const loadInfo = useCallback(() => get(token).then(d => { setInfo(d); setFatal(""); document.title = `${d.name} · BastiON`; })
    .catch(e => { if (e.status === 404) setFatal(e.message); else setOffline(true); }), [token]);
  useEffect(() => { loadInfo(); const t = setInterval(loadInfo, INFO_SEC * 1000); return () => clearInterval(t); }, [loadInfo]);

  const maps = info?.maps || [];
  const map = maps[Math.min(idx, Math.max(0, maps.length - 1))];

  // tráfego: o mapa da tela a cada 10 s; os outros também (contadores de alarme do rodapé)
  useEffect(() => {
    if (!maps.length) return;
    let alive = true;
    const load = async () => {
      let ok = false;
      await Promise.all(maps.map(m => get(`${token}/live/${m.id}`).then(d => { ok = true; if (alive) setLive(s => ({ ...s, [m.id]: d })); }).catch(() => {})));
      if (!alive) return;
      setOffline(!ok); if (ok) setLastOk(new Date());
    };
    load(); const t = setInterval(load, LIVE_SEC * 1000);
    return () => { alive = false; clearInterval(t); };
  }, [token, maps.map(m => m.id).join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // relógio + troca automática de mapa
  useEffect(() => {
    const t = setInterval(() => {
      setNow(new Date());
      const r = info?.rotate_sec || 0;
      if (!paused && r && maps.length > 1 && Date.now() - shownAt.current >= r * 1000) go(1);
    }, 1000);
    return () => clearInterval(t);
  }); // eslint-disable-line react-hooks/exhaustive-deps
  const go = (d) => { shownAt.current = Date.now(); setSel(null); setIdx(i => (maps.length ? (i + d + maps.length) % maps.length : 0)); setFit(f => f + 1); };

  useEffect(() => {
    const onKey = (e) => { if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1); else if (e.key === " ") setPaused(p => !p); else if (e.key === "f") full(); };
    const onResize = () => setFit(f => f + 1);
    window.addEventListener("keydown", onKey); window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("resize", onResize); };
  });
  // mantém a tela da TV acesa (quando o navegador deixa)
  useEffect(() => {
    let lock = null;
    const req = async () => { try { lock = await navigator.wakeLock?.request("screen"); } catch { /* sem suporte */ } };
    req(); const onVis = () => document.visibilityState === "visible" && req();
    document.addEventListener("visibilitychange", onVis);
    return () => { document.removeEventListener("visibilitychange", onVis); try { lock?.release(); } catch { /* ok */ } };
  }, []);
  const full = () => { try { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen(); } catch { /* ok */ } };

  const devices = info?.devices || [];
  const isGeo = map?.geo?.view === "geo";
  const viewMap = useMemo(() => (map ? (isGeo ? toGeoView(map) : map) : null), [map, isGeo]);
  const geo = useMemo(() => (isGeo ? geoLayers(map.geo) : null), [isGeo, map?.geo]); // eslint-disable-line react-hooks/exhaustive-deps

  // resumo de alarmes por mapa
  const summary = useCallback((m) => {
    const lv = live[m.id]?.links || {};
    const nodeName = (id) => { const n = m.nodes.find(x => x.id === id); return devices.find(d => d.id === n?.device_id)?.name || n?.label || "?"; };
    const down = [], hot = [];
    (m.links || []).forEach(l => {
      const v = lv[l.id]; if (!v) return;
      const name = l.label || `${nodeName(l.from)} ↔ ${nodeName(l.to)}`;
      if (v.down) down.push({ name });
      else { const p = Math.max(v.ab_pct || 0, v.ba_pct || 0); if (p >= 80) hot.push({ name, p, bps: Math.max(v.ab_bps || 0, v.ba_bps || 0) }); }
    });
    const nodesDown = Object.values(live[m.id]?.nodes || {}).filter(n => n.status === "offline").length;
    return { down, hot: hot.sort((a, b) => b.p - a.p), nodesDown };
  }, [live, devices]);
  const cur = map ? summary(map) : { down: [], hot: [], nodesDown: 0 };
  const selLink = sel?.type === "link" && map?.links.find(l => l.id === sel.id);
  const selLv = selLink && live[map.id]?.links?.[selLink.id];

  if (fatal) return <Center title="Link do mapa indisponível" text={`${fatal}. Gere um novo em Mapas → Link do mapa.`} />;
  if (!info) return <Center title="BastiON" text={offline ? "Sem conexão com o BastiON… tentando de novo." : "Carregando…"} />;
  if (!maps.length) return <Center title={info.name} text="Nenhum mapa neste link (o mapa pode ter sido excluído)." />;

  const remain = info.rotate_sec && maps.length > 1 && !paused ? Math.max(0, info.rotate_sec - Math.floor((now - shownAt.current) / 1000)) : null;
  return (
    <div className="fixed inset-0 flex flex-col bg-canvas text-slate-100 select-none" data-testid="tv-page">
      <div className="flex items-center gap-4 px-4 py-2 border-b border-line bg-surface">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wider text-slate-400 truncate">{info.name}</div>
          <div className="text-xl font-semibold truncate" data-testid="tv-map-name">{map.name}</div>
        </div>
        {maps.length > 1 && (
          <div className="flex items-center gap-1.5">
            {maps.map((m, i) => {
              const s = summary(m);
              return <button key={m.id} onClick={() => { shownAt.current = Date.now(); setIdx(i); setSel(null); setFit(f => f + 1); }} title={m.name}
                             className={`h-2.5 rounded-full transition-all ${i === idx ? "w-8 bg-brand" : "w-2.5"} ${i !== idx && (s.down.length || s.nodesDown) ? "bg-red-500" : i !== idx ? "bg-slate-600" : ""}`} />;
            })}
          </div>
        )}
        <div className="flex items-center gap-2 ml-auto text-sm">
          <Chip color={STATUS.good} label="enlaces" value={(map.links || []).length} />
          <Chip color={STATUS.critical} label="down" value={cur.down.length} alert={cur.down.length > 0} />
          <Chip color="#F59E0B" label="≥ 80%" value={cur.hot.length} alert={cur.hot.length > 0} />
          {cur.nodesDown > 0 && <Chip color={STATUS.critical} label="equip. offline" value={cur.nodesDown} alert />}
        </div>
        <div className="text-right">
          <div className="text-2xl font-mono tabular-nums leading-none">{hhmm(now)}</div>
          <div className={`text-[10px] font-mono ${offline ? "text-red-400" : "text-slate-500"}`}>
            {offline ? "⚠ sem conexão — mostrando a última leitura" : `atualizado ${lastOk ? hhmm(lastOk) : "—"}`}
            {remain != null && ` · próximo mapa em ${remain}s`}{paused && " · pausado"}
          </div>
        </div>
        <div className="flex gap-1 text-slate-400">
          {maps.length > 1 && <>
            <TvBtn onClick={() => go(-1)} title="Mapa anterior (←)">‹</TvBtn>
            <TvBtn onClick={() => setPaused(p => !p)} title="Pausar/continuar (espaço)">{paused ? "▶" : "❚❚"}</TvBtn>
            <TvBtn onClick={() => go(1)} title="Próximo mapa (→)">›</TvBtn>
          </>}
          <TvBtn onClick={full} title="Tela cheia (F)">⛶</TvBtn>
        </div>
      </div>

      <div className="flex-1 min-h-0 relative">
        <MapCanvas key={map.id} map={viewMap} geo={geo} live={live[map.id]} devices={devices} editing={false} tool="select"
                   selected={sel} onSelect={setSel} onMoveNode={() => {}} onConnect={() => {}} fitSignal={fit} />
        {selLink && (
          <div className="absolute top-3 left-3 bg-surface/95 border border-line2 rounded-md px-3 py-2 text-xs font-mono shadow-xl">
            <div className="font-sans font-semibold text-slate-100 mb-1">{selLink.label || "Enlace"}</div>
            <div>→ {fmtBps(selLv?.ab_bps)}{selLv?.ab_pct != null ? ` (${selLv.ab_pct}%)` : ""} · ← {fmtBps(selLv?.ba_bps)}{selLv?.ba_pct != null ? ` (${selLv.ba_pct}%)` : ""}</div>
            {selLv?.down && <div className="text-red-400">✕ DOWN</div>}
          </div>
        )}
      </div>

      <div className="flex items-center gap-4 px-4 py-1.5 border-t border-line bg-surface text-[11px] font-mono overflow-hidden whitespace-nowrap" data-testid="tv-footer">
        <div className="flex items-center gap-2 text-slate-400 shrink-0">
          {UTIL_BANDS.map(b => <span key={b.label} className="flex items-center gap-1"><span className="w-3 h-1.5 rounded-sm" style={{ background: b.color }} />{b.label}</span>)}
          <span className="flex items-center gap-1"><span className="w-3 border-t-2 border-dashed" style={{ borderColor: STATUS.critical }} />down</span>
          <span className="flex items-center gap-1"><span className="w-3 border-t-2 border-dashed" style={{ borderColor: NO_DATA }} />sem dados</span>
        </div>
        <div className="flex items-center gap-3 min-w-0 overflow-hidden">
          {cur.down.map((d, i) => <span key={`d${i}`} className="text-red-300">✕ {d.name}</span>)}
          {cur.hot.slice(0, 6).map((d, i) => <span key={`h${i}`} className="text-amber-300">▲ {d.name} {d.p}% · {fmtBps(d.bps)}</span>)}
          {!cur.down.length && !cur.hot.length && <span className="text-emerald-400">✓ todos os enlaces normais</span>}
          {maps.filter(m => m.id !== map.id).map(m => { const s = summary(m); return s.down.length || s.nodesDown ? <span key={m.id} className="text-red-300">⚠ {m.name}: {s.down.length} down{s.nodesDown ? `, ${s.nodesDown} offline` : ""}</span> : null; })}
        </div>
      </div>
    </div>
  );
}

function Chip({ color, label, value, alert }) {
  return (
    <span className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md border ${alert ? "border-red-500/60 bg-red-950/40 animate-pulse" : "border-line"}`}>
      <span className="w-2 h-2 rounded-full" style={{ background: color }} />
      <b className="font-mono text-base tabular-nums">{value}</b><span className="text-slate-400 text-xs">{label}</span>
    </span>
  );
}
const TvBtn = ({ children, ...p }) => <button {...p} className="w-8 h-8 rounded-md border border-line hover:text-slate-100 hover:border-line2 text-sm">{children}</button>;
function Center({ title, text }) {
  return (
    <div className="fixed inset-0 bg-canvas text-slate-200 flex flex-col items-center justify-center gap-2 p-6 text-center">
      <div className="text-2xl font-semibold">{title}</div><div className="text-sm text-slate-400 max-w-md">{text}</div>
    </div>
  );
}
