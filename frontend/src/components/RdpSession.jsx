import React, { useEffect, useRef, useState } from "react";
import Guacamole from "guacamole-common-js";
import { wsUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Loader2, X, Maximize2, ClipboardPaste, Keyboard, RefreshCw } from "lucide-react";

const KEY = { ctrl: 0xffe3, alt: 0xffe9, del: 0xffff, win: 0xffeb };

/** Sessão RDP em tela cheia sobre o app. `ticket` vem de POST /rdp/connect (uso único). */
export function RdpSession({ ticket, name, target, onClose, onRetry }) {
  const boxRef = useRef(null);
  const clientRef = useRef(null);
  const [phase, setPhase] = useState("connecting");      // connecting | on | ended
  const [error, setError] = useState("");

  useEffect(() => {
    const box = boxRef.current;
    const size = () => ({ w: Math.max(640, Math.floor(box.clientWidth)), h: Math.max(480, Math.floor(box.clientHeight)) });
    const tunnel = new Guacamole.WebSocketTunnel(wsUrl(`/ws/rdp/${ticket}`));
    const client = new Guacamole.Client(tunnel);
    clientRef.current = client;
    const display = client.getDisplay();
    const el = display.getElement();
    el.style.margin = "0 auto";
    box.appendChild(el);

    const fit = () => {
      const dw = display.getWidth(), dh = display.getHeight();
      if (!dw || !dh) return;
      display.scale(Math.min(box.clientWidth / dw, box.clientHeight / dh, 1));
    };
    display.onresize = fit;
    let ended = false;
    const end = (msg) => { if (ended) return; ended = true; if (msg) setError(msg); setPhase("ended"); };
    client.onerror = (st) => end(st?.message || "A conexão falhou");
    tunnel.onerror = (st) => end(st?.message || "Perdi a ligação com o BastiON");
    client.onstatechange = (s) => { if (s === 3) setPhase("on"); else if (s === 5) end(""); };

    // área de transferência: remoto → local
    client.onclipboard = (stream, mimetype) => {
      if (!/^text\//.test(mimetype)) return;
      const reader = new Guacamole.StringReader(stream);
      let data = "";
      reader.ontext = (t) => { data += t; };
      reader.onend = () => { try { navigator.clipboard?.writeText(data).catch(() => {}); } catch { /* sem permissão */ } };
    };

    const s = size();
    client.connect(`token=${encodeURIComponent(localStorage.getItem("bastion_token") || "")}&w=${s.w}&h=${s.h}`);

    const mouse = new Guacamole.Mouse(el);
    const sendMouse = (st) => {
      const sc = display.getScale() || 1;
      client.sendMouseState(new Guacamole.Mouse.State(st.x / sc, st.y / sc, st.left, st.middle, st.right, st.up, st.down));
    };
    mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = sendMouse;
    el.oncontextmenu = (e) => e.preventDefault();

    const kbd = new Guacamole.Keyboard(document);
    kbd.onkeydown = (k) => { client.sendKeyEvent(1, k); };
    kbd.onkeyup = (k) => { client.sendKeyEvent(0, k); };

    let timer = null;
    const onResize = () => { fit(); clearTimeout(timer); timer = setTimeout(() => { const z = size(); try { client.sendSize(z.w, z.h); } catch { /* fechado */ } }, 400); };
    const ro = new ResizeObserver(onResize);
    ro.observe(box);
    const onUnload = () => client.disconnect();
    window.addEventListener("beforeunload", onUnload);

    return () => {
      ended = true;
      window.removeEventListener("beforeunload", onUnload);
      ro.disconnect(); clearTimeout(timer);
      kbd.onkeydown = kbd.onkeyup = null; try { kbd.reset(); } catch { /* noop */ }
      try { client.disconnect(); } catch { /* noop */ }
      try { box.removeChild(el); } catch { /* noop */ }
    };
  }, [ticket]);

  const keys = (...ks) => { const c = clientRef.current; if (!c) return; ks.forEach(k => c.sendKeyEvent(1, k)); [...ks].reverse().forEach(k => c.sendKeyEvent(0, k)); };
  const paste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (!text) return toast("Área de transferência vazia");
      const stream = clientRef.current.createClipboardStream("text/plain");
      const w = new Guacamole.StringWriter(stream); w.sendText(text); w.sendEnd();
      toast.success("Enviado — cole no computador remoto com Ctrl+V");
    } catch { toast.error("O navegador não liberou a área de transferência (precisa de HTTPS e permissão)"); }
  };
  const full = () => { const b = boxRef.current?.parentElement; if (document.fullscreenElement) document.exitFullscreen(); else b?.requestFullscreen?.().catch(() => {}); };

  return (
    <div className="fixed inset-0 z-50 bg-black flex flex-col" data-testid="rdp-session">
      <div className="h-10 shrink-0 flex items-center gap-2 px-3 bg-panel border-b border-line text-sm">
        <span className={`w-2 h-2 rounded-full ${phase === "on" ? "bg-on" : phase === "connecting" ? "bg-amber-400 animate-pulse" : "bg-red-500"}`} />
        <span className="text-slate-100 truncate">{name}</span><span className="text-xs font-mono text-slate-500 truncate hidden sm:inline">{target}</span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" className="h-7 text-slate-300" disabled={phase !== "on"} onClick={() => keys(KEY.ctrl, KEY.alt, KEY.del)} title="Enviar Ctrl+Alt+Del" data-testid="rdp-cad"><Keyboard className="w-3.5 h-3.5 sm:mr-1.5" /><span className="hidden sm:inline">Ctrl+Alt+Del</span></Button>
          <Button size="sm" variant="ghost" className="h-7 text-slate-300" disabled={phase !== "on"} onClick={paste} title="Enviar o texto copiado aqui para o computador remoto"><ClipboardPaste className="w-3.5 h-3.5 sm:mr-1.5" /><span className="hidden sm:inline">Enviar área de transferência</span></Button>
          <Button size="sm" variant="ghost" className="h-7 text-slate-300" onClick={full} title="Tela cheia"><Maximize2 className="w-3.5 h-3.5" /></Button>
          <Button size="sm" variant="ghost" className="h-7 text-red-400" onClick={onClose} title="Desconectar" data-testid="rdp-close"><X className="w-4 h-4 sm:mr-1" /><span className="hidden sm:inline">Desconectar</span></Button>
        </div>
      </div>
      <div className="relative flex-1 min-h-0 overflow-hidden bg-black">
        <div ref={boxRef} className="absolute inset-0 overflow-hidden" style={{ cursor: phase === "on" ? "none" : "default" }} />
        {phase !== "on" && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/80">
            <div className="text-center max-w-md px-6" data-testid="rdp-overlay">
              {phase === "connecting" ? <><Loader2 className="w-6 h-6 animate-spin text-brand-soft mx-auto mb-3" /><div className="text-slate-200">Conectando em {name}…</div><div className="text-xs text-slate-500 mt-1 font-mono">{target}</div></>
                : <>
                  <div className={error ? "text-red-400" : "text-slate-200"}>{error ? "Não foi possível manter a sessão" : "Sessão encerrada"}</div>
                  {error && <div className="text-sm text-slate-300 mt-2" data-testid="rdp-error">{error}</div>}
                  <div className="flex gap-2 justify-center mt-4">
                    {onRetry && <Button size="sm" className="bg-brand hover:bg-brand-strong" onClick={onRetry}><RefreshCw className="w-3.5 h-3.5 mr-1.5" />Conectar de novo</Button>}
                    <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={onClose}>Fechar</Button>
                  </div>
                </>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
