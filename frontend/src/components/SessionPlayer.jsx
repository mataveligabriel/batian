import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { api, formatApiError } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Play, Pause, Download, Loader2, Rewind, FastForward } from "lucide-react";

const SPEEDS = [1, 2, 4, 8, 16];
const fmtT = (s) => { s = Math.max(0, Math.floor(s)); const m = Math.floor(s / 60); return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };

/** asciicast v2 → {header, events:[{t, real, type, data}]}; "pular ociosidade" encurta pausas longas para maxIdle s. */
export function parseCast(text, maxIdle) {
  const lines = (text || "").split("\n").filter(Boolean);
  if (!lines.length) return { header: {}, events: [], duration: 0 };
  const header = JSON.parse(lines[0]);
  const events = [];
  let prevReal = 0, virt = 0;
  for (const l of lines.slice(1)) {
    let e;
    try { e = JSON.parse(l); } catch { continue; }
    if (!Array.isArray(e) || e.length < 3) continue;
    const real = Number(e[0]) || 0;
    const gap = real - prevReal;
    virt += maxIdle ? Math.min(gap, maxIdle) : gap;
    prevReal = real;
    events.push({ t: virt, real, type: e[1], data: String(e[2]) });
  }
  return { header, events, duration: events.length ? events[events.length - 1].t : 0 };
}

export function SessionPlayer({ session, onClose }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState("");
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [skipIdle, setSkipIdle] = useState(true);
  const [pos, setPos] = useState(0);
  const box = useRef(null);
  const term = useRef(null);
  const st = useRef({ idx: 0, clock: 0, last: 0, raf: 0 });
  const cast = useMemo(() => (text ? parseCast(text, skipIdle ? 2 : 0) : null), [text, skipIdle]);

  useEffect(() => {
    setText(null); setErr("");
    api.get(`/sessions/${session.id}/recording`, { responseType: "text", transformResponse: (d) => d })
      .then(r => setText(r.data)).catch(e => setErr(formatApiError(e)));
  }, [session.id]);

  // terminal do player (mesmo tamanho da gravação; a fonte se ajusta à largura)
  useEffect(() => {
    if (!cast || !box.current) return undefined;
    const h = cast.header;
    const w = box.current.clientWidth || 900;
    const fs = Math.max(8, Math.min(15, Math.floor((w - 24) / ((h.width || 120) * 0.6))));
    const t = new XTerm({ cols: h.width || 120, rows: h.height || 32, fontSize: fs, convertEol: false, disableStdin: true,
      cursorBlink: false, scrollback: 5000, fontFamily: '"JetBrains Mono", Consolas, Menlo, monospace',
      theme: { background: "#0b0f10", foreground: "#d8dee0" } });
    t.open(box.current);
    term.current = t;
    st.current = { idx: 0, clock: 0, last: 0, raf: 0 };
    setPos(0);
    return () => { cancelAnimationFrame(st.current.raf); t.dispose(); term.current = null; };
  }, [cast]);

  const apply = (e) => {
    const t = term.current;
    if (!t) return;
    if (e.type === "o") t.write(e.data);
    else if (e.type === "r") { const [c, r] = e.data.split("x").map(Number); if (c && r) t.resize(c, r); }
  };

  const seek = useCallback((target) => {
    const t = term.current;
    if (!t || !cast) return;
    t.reset();
    let i = 0, buf = "";
    while (i < cast.events.length && cast.events[i].t <= target) {
      const e = cast.events[i];
      if (e.type === "o") buf += e.data;
      else if (e.type === "r") { if (buf) { t.write(buf); buf = ""; } apply(e); }
      i++;
    }
    if (buf) t.write(buf);
    st.current.idx = i; st.current.clock = target;
    setPos(target);
  }, [cast]);

  useEffect(() => {
    if (!playing || !cast) return undefined;
    st.current.last = performance.now();
    const tick = (now) => {
      const s = st.current;
      s.clock += ((now - s.last) / 1000) * speed;
      s.last = now;
      while (s.idx < cast.events.length && cast.events[s.idx].t <= s.clock) { apply(cast.events[s.idx]); s.idx++; }
      setPos(Math.min(s.clock, cast.duration));
      if (s.idx >= cast.events.length) { setPlaying(false); return; }
      s.raf = requestAnimationFrame(tick);
    };
    st.current.raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(st.current.raf);
  }, [playing, speed, cast]);

  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/x-asciicast" }));
    a.download = `sessao-${session.device_name || "terminal"}-${(session.started_at || "").slice(0, 16).replace(/[:T]/g, "-")}.cast`;
    a.click();
  };
  const rec = session.recording || {};

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-6xl w-[96vw]" data-testid="session-player">
        <DialogHeader><DialogTitle className="text-base">Replay — {session.device_name} · {session.user_email}</DialogTitle></DialogHeader>
        <div className="text-xs text-slate-400 -mt-2">{new Date(session.started_at).toLocaleString("pt-BR")} · {fmtT(session.duration_seconds || rec.duration || 0)} de sessão
          {rec.input ? " · com o que foi digitado" : ""}{rec.truncated ? " · gravação cortada no limite de tamanho" : ""}{rec.live ? " · sessão ainda aberta (até agora)" : ""}</div>
        {err ? <div className="text-sm text-red-300 py-8 text-center">{err}</div> : !cast ? <div className="py-10 text-center text-slate-500"><Loader2 className="w-5 h-5 animate-spin inline mr-2" />Carregando a gravação…</div> : (
          <>
            <div ref={box} className="rounded-md overflow-auto bg-[#0b0f10] p-3 max-h-[65vh]" data-testid="session-player-term" />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => { if (pos >= cast.duration) seek(0); setPlaying(p => !p); }} className="bg-brand hover:bg-brand-strong text-onbrand w-24" data-testid="player-play">
                {playing ? <><Pause className="w-4 h-4 mr-1" /> Pausar</> : <><Play className="w-4 h-4 mr-1" /> {pos > 0 && pos < cast.duration ? "Seguir" : "Assistir"}</>}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => seek(Math.max(0, pos - 10))} className="text-slate-300" title="-10 s"><Rewind className="w-4 h-4" /></Button>
              <Button size="sm" variant="ghost" onClick={() => seek(Math.min(cast.duration, pos + 10))} className="text-slate-300" title="+10 s"><FastForward className="w-4 h-4" /></Button>
              <input type="range" min={0} max={Math.max(0.1, cast.duration)} step={0.1} value={pos} data-testid="player-seek"
                     onChange={e => seek(Number(e.target.value))} className="flex-1 min-w-[10rem] accent-brand" />
              <span className="text-xs font-mono text-slate-400 w-36 text-right">{fmtT(pos)} / {fmtT(cast.duration)}</span>
              <select value={speed} onChange={e => setSpeed(Number(e.target.value))} className="h-8 rounded-md bg-sunken border border-line px-1 text-xs text-slate-200" data-testid="player-speed">
                {SPEEDS.map(s => <option key={s} value={s}>{s}×</option>)}
              </select>
              <label className="text-xs text-slate-300 flex items-center gap-1.5"><input type="checkbox" className="accent-brand" checked={skipIdle} onChange={e => { setPlaying(false); setSkipIdle(e.target.checked); }} /> pular pausas</label>
              <Button size="sm" variant="ghost" onClick={download} className="text-slate-300" title="Baixar (.cast — abre no asciinema)"><Download className="w-4 h-4" /></Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
