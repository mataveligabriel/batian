import React, { useEffect, useMemo, useRef, useState } from "react";
import { MapCanvasAvail, C, hhmm } from "@/pages/Availability";

// Link sem login do mapa de disponibilidade: /disp/<token>. Só lê /api/public/avail da própria origem.
const get = async (path) => {
  const r = await fetch(`/api/public/avail/${path}`, { cache: "no-store" });
  let data = null;
  try { data = await r.json(); } catch { /* sem JSON */ }
  if (!r.ok) { const e = new Error((data && typeof data.detail === "string" && data.detail) || "Sem conexão com o BastiON"); e.status = r.status; throw e; }
  return data;
};

export default function AvailPublic({ token }) {
  const [info, setInfo] = useState(null);
  const [fatal, setFatal] = useState("");
  const [trail, setTrail] = useState([]);
  const [live, setLive] = useState(null);
  const [offline, setOffline] = useState(false);
  const [sel, setSel] = useState(null);
  const [fit, setFit] = useState(0);
  const [now, setNow] = useState(new Date());
  const [lastOk, setLastOk] = useState(null);
  const shell = useRef(null);

  useEffect(() => {
    const load = () => get(token).then(d => { setInfo(d); setFatal(""); document.title = `${d.name} · BastiON`; setTrail(t => (t.length && t.every(id => d.maps[id]) ? t : [d.root])); })
      .catch(e => { if (e.status === 404) setFatal(e.message); else setOffline(true); });
    load(); const t = setInterval(load, 300000);
    return () => clearInterval(t);
  }, [token]);
  const mapId = trail[trail.length - 1];
  const map = info?.maps?.[mapId];

  useEffect(() => {
    if (!mapId) return;
    let alive = true;
    const go = () => get(`${token}/live/${mapId}`).then(d => { if (alive) { setLive(d); setOffline(false); setLastOk(new Date()); } }).catch(() => alive && setOffline(true));
    go(); const t = setInterval(go, 10000);
    return () => { alive = false; clearInterval(t); };
  }, [token, mapId]);
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); const r = () => setFit(f => f + 1); window.addEventListener("resize", r); return () => { clearInterval(t); window.removeEventListener("resize", r); }; }, []);
  useEffect(() => {
    let lock = null;
    (async () => { try { lock = await navigator.wakeLock?.request("screen"); } catch { /* sem suporte */ } })();
    return () => { try { lock?.release(); } catch { /* ok */ } };
  }, []);

  const names = useMemo(() => {
    const out = Object.fromEntries(Object.values(info?.maps || {}).map(m => [m.id, m.name]));
    (map?.nodes || []).forEach(n => { if (n.kind === "device" && live?.nodes?.[n.id]) out[n.device_id] = live.nodes[n.id].name; });
    return out;
  }, [info, map, live]);
  const enter = (id) => { if (info?.maps?.[id]) { setTrail(t => [...t, id]); setSel(null); setLive(null); setFit(f => f + 1); } };
  const full = () => { try { document.fullscreenElement ? document.exitFullscreen() : shell.current?.requestFullscreen(); } catch { /* ok */ } };

  if (fatal) return <Center title="Link do mapa indisponível" text={`${fatal}. Peça um link novo em Disponibilidade → Link do mapa.`} />;
  if (!info || !map) return <Center title="BastiON" text={offline ? "Sem conexão com o BastiON… tentando de novo." : "Carregando…"} />;
  const sum = live?.summary;
  const selNode = sel?.id && map.nodes.find(n => n.id === sel.id);
  const sl = selNode && live?.nodes?.[selNode.id];
  const sLink = sel?.link && live?.links?.[sel.link];

  return (
    <div ref={shell} className="fixed inset-0 flex flex-col select-none" style={{ background: C.bg0, color: C.ink }} data-testid="avail-public">
      <div className="flex flex-wrap items-center gap-4 px-4 py-2" style={{ borderBottom: `1px solid ${C.line}` }}>
        <div className="min-w-0">
          <div className="text-xs" style={{ color: C.dim }}>{info.name}</div>
          <nav className="flex items-center gap-1.5 text-lg font-semibold" aria-label="Caminho">
            {trail.map((id, i) => (
              <React.Fragment key={id}>
                {i > 0 && <span style={{ color: C.dim }}>›</span>}
                <button onClick={() => { setTrail(trail.slice(0, i + 1)); setSel(null); setLive(null); setFit(f => f + 1); }} className={i === trail.length - 1 ? "" : "opacity-60 hover:opacity-100"}>{info.maps[id]?.name}</button>
              </React.Fragment>
            ))}
          </nav>
        </div>
        {sum && sum.total + (sum.links || 0) > 0 && (
          <div className="flex items-center gap-3 text-sm">
            <div className="flex h-2 w-40 rounded-full overflow-hidden" style={{ background: C.line }} aria-hidden="true">
              {["down", "warn", "up", "pending"].map(k => (sum[k] ? <div key={k} style={{ width: `${(sum[k] / sum.total) * 100}%`, background: C[k] }} /> : null))}
            </div>
            <span className="font-mono"><span style={{ color: C.up }}>{sum.up}</span> / {sum.total}</span>
            {sum.down > 0 && <span className="font-semibold" style={{ color: C.down }}>{sum.down} sem resposta</span>}
            {sum.links_down > 0 && <span className="font-semibold" style={{ color: C.down }}>{sum.links_down} rota(s) fora</span>}
            {sum.warn > 0 && <span style={{ color: C.warn }}>{sum.warn} instável</span>}
          </div>
        )}
        <div className="ml-auto text-right">
          <div className="text-2xl font-mono tabular-nums leading-none">{now.toLocaleTimeString("pt-BR")}</div>
          <div className="text-[10px] font-mono" style={{ color: offline ? C.down : C.dim }}>{offline ? "sem conexão — mostrando a última leitura" : `atualizado ${lastOk ? lastOk.toLocaleTimeString("pt-BR") : "—"}`}</div>
        </div>
        <button onClick={full} className="w-8 h-8 rounded-md text-sm" style={{ border: `1px solid ${C.line}`, color: C.dim }} title="Tela cheia" aria-label="Tela cheia">⛶</button>
      </div>
      <div className="flex-1 min-h-0 relative">
        <MapCanvasAvail map={map} live={live} names={names} editing={false} tool="move" selected={sel} onSelect={setSel} onMove={() => {}} onConnect={() => {}} onEnter={enter} fitSignal={fit} />
        {(sl || sLink) && (
          <div className="absolute top-3 right-3 w-64 rounded-lg p-3 text-sm space-y-1 shadow-2xl" style={{ background: `${C.panel}f2`, border: `1px solid ${C.line}` }}>
            <button onClick={() => setSel(null)} className="float-right" style={{ color: C.dim }} aria-label="Fechar">×</button>
            {sl && selNode.kind === "device" && <>
              <div className="font-semibold">{sl.name}</div>{sl.host && <div className="font-mono text-xs" style={{ color: C.dim }}>{sl.host}</div>}
              <div style={{ color: C[sl.state] }}>{sl.state === "down" ? `sem resposta há ${hhmmAgo(sl.since)}` : sl.ms != null ? `${sl.ms} ms${sl.loss ? ` · ${Math.round(sl.loss)}% perda` : ""}` : "aguardando"}</div>
            </>}
            {sl && selNode.kind === "submap" && <>
              <div className="font-semibold">{sl.name}</div>
              <div className="text-xs" style={{ color: C.dim }}>{sl.up} respondendo · {sl.down} sem resposta{sl.links_down ? ` · ${sl.links_down} rota(s) fora` : ""}</div>
              {sl.down_names?.map(n => <div key={n} className="text-xs" style={{ color: C.down }}>• {n}</div>)}
              <button onClick={() => enter(selNode.map_id)} className="mt-1 w-full h-8 rounded-md text-xs font-semibold" style={{ background: C.up, color: C.bg0 }}>Entrar</button>
            </>}
            {sLink && <>
              <div className="font-semibold">{sLink.device_name} · <span className="font-mono">{sLink.if_name}</span></div>
              <div style={{ color: sLink.state === "up" ? C.up : sLink.state === "down" ? C.down : C.dim }}>{sLink.state === "up" ? "UP" : sLink.state === "down" ? "DOWN" : "sem leitura"}{sLink.speed_mbps ? ` · ${sLink.speed_mbps >= 1000 ? `${sLink.speed_mbps / 1000}G` : `${sLink.speed_mbps}M`}` : ""}</div>
            </>}
          </div>
        )}
      </div>
      {live?.events?.length > 0 && (
        <div className="px-4 py-1.5 flex items-center gap-4 overflow-hidden whitespace-nowrap text-xs" style={{ borderTop: `1px solid ${C.line}` }}>
          {live.events.slice(0, 10).map(e => (
            <span key={e.id} className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: e.state === "down" ? C.down : C.up }} />
              <span className="font-mono" style={{ color: C.dim }}>{hhmm(e.at)}</span>
              <span>{e.device_name}</span>
              <span style={{ color: e.state === "down" ? C.down : C.up }}>{e.state === "down" ? (e.link ? "caiu" : "parou de responder") : "voltou"}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function hhmmAgo(iso) {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  return s < 3600 ? `${Math.max(1, Math.floor(s / 60))} min` : `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}

function Center({ title, text }) {
  return (
    <div className="fixed inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center" style={{ background: C.bg0, color: C.ink }}>
      <div className="text-2xl font-semibold">{title}</div><div className="text-sm max-w-md" style={{ color: C.dim }}>{text}</div>
    </div>
  );
}
