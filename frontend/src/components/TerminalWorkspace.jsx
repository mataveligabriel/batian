import React, { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { Plus, X, Wifi, WifiOff, Server, TerminalSquare, RotateCw, Zap } from "lucide-react";
import { useTerminal } from "@/context/TerminalContext";

function TerminalPane({ device, active, visible, registerWs }) {
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);
  const [status, setStatus] = useState("connecting");
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!containerRef.current) return;
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", Menlo, Consolas, monospace',
      fontSize: 13,
      theme: {
        background: "#05070A",
        foreground: "#E2E8F0",
        cursor: "#4DA3FF",
        selectionBackground: "rgba(0,122,255,0.35)",
        black: "#0A0E17", red: "#F87171", green: "#10B981", yellow: "#F59E0B",
        blue: "#4DA3FF", magenta: "#8B5CF6", cyan: "#22D3EE", white: "#F8FAFC",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    requestAnimationFrame(() => { try { fit.fit(); } catch { /* noop */ } });
    termRef.current = term;
    fitRef.current = fit;

    const token = localStorage.getItem("bastion_token");
    const url = `${wsUrl(`/ws/terminal/${device.id}`)}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    registerWs?.(device.id, ws);
    setStatus("connecting");

    ws.onopen = () => {
      const { cols, rows } = term;
      ws.send(JSON.stringify({ type: "resize", cols, rows }));
    };
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "data") term.write(msg.data);
        else if (msg.type === "status") {
          term.writeln(`\x1b[36m>> ${msg.message}\x1b[0m`);
          if (msg.message?.toLowerCase().includes("conectado")) setStatus("connected");
        }
        else if (msg.type === "error") {
          term.writeln(`\x1b[31m>> ${msg.message}\x1b[0m`);
          setStatus("error");
        }
      } catch { /* ignore malformed */ }
    };
    ws.onclose = () => {
      setStatus(s => (s === "error" ? s : "closed"));
      try { term.writeln("\r\n\x1b[33m>> Sessão encerrada. Clique em reconectar.\x1b[0m"); } catch { /* noop */ }
    };
    ws.onerror = () => setStatus("error");

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data }));
    });

    const onResize = () => {
      try {
        fit.fit();
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      } catch { /* noop */ }
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      try { ws.close(); } catch { /* noop */ }
      try { term.dispose(); } catch { /* noop */ }
    };
  }, [device.id, generation]);

  useEffect(() => {
    if (active && visible && fitRef.current) {
      const t = setTimeout(() => {
        try {
          fitRef.current.fit();
          const ws = wsRef.current, term = termRef.current;
          if (ws?.readyState === WebSocket.OPEN && term) ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
          term?.focus();
        } catch { /* noop */ }
      }, 60);
      return () => clearTimeout(t);
    }
  }, [active, visible]);

  const label = status === "connected" ? "conectado" : status === "connecting" ? "conectando" : status === "closed" ? "encerrada" : "erro";
  return (
    <div className={`absolute inset-0 flex flex-col ${active && visible ? "visible z-10" : "invisible z-0"}`} data-testid={`terminal-pane-${device.id}`}>
      <div className="flex items-center justify-between border-b border-[#1E293B] px-4 py-2 bg-[#0B111C]">
        <div className="flex items-center gap-3">
          <Server className="w-4 h-4 text-[#4DA3FF]" />
          <div>
            <div className="text-sm font-medium text-slate-100">{device.name}</div>
            <div className="text-[11px] font-mono text-slate-500">{device.host}:{device.port} · {device.username || "padrão"}</div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {(status === "closed" || status === "error") && (
            <Button size="sm" variant="ghost" onClick={() => setGeneration(g => g + 1)} data-testid={`reconnect-${device.id}`} className="h-7 text-xs text-slate-300 hover:bg-slate-800">
              <RotateCw className="w-3.5 h-3.5 mr-1" /> Reconectar
            </Button>
          )}
          <span data-testid={`terminal-status-${device.id}`} className={`text-[11px] font-mono uppercase tracking-widest ${
            status === "connected" ? "text-emerald-400" :
            status === "connecting" ? "text-amber-400" :
            "text-red-400"
          }`}>{label}</span>
        </div>
      </div>
      <div ref={containerRef} className="xterm-container flex-1" data-testid={`xterm-${device.id}`} />
    </div>
  );
}

export function TerminalWorkspace({ visible }) {
  const { tabs, active, setActive, openTab, closeTab } = useTerminal();
  const [devices, setDevices] = useState([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [quick, setQuick] = useState([]);
  const wsMap = useRef({});
  const registerWs = (id, ws) => { wsMap.current[id] = ws; };

  useEffect(() => {
    if (pickerOpen) api.get("/devices").then(r => setDevices(r.data)).catch(() => toast.error("Falha ao carregar equipamentos"));
  }, [pickerOpen]);

  useEffect(() => {
    if (visible) api.get("/scripts").then(r => setQuick(r.data.filter(s => s.quick))).catch(() => {});
  }, [visible]);

  const sendQuick = (s) => {
    const ws = active && wsMap.current[active];
    if (!ws || ws.readyState !== WebSocket.OPEN) return toast.error("Nenhuma sessão ativa conectada");
    const data = s.content.replace(/\r?\n/g, "\r").replace(/\r?$/, "\r");
    ws.send(JSON.stringify({ type: "input", data }));
  };

  return (
    <div className={`absolute inset-0 flex flex-col ${visible ? "visible z-20" : "invisible z-0 pointer-events-none"}`} data-testid="terminal-page">
      <div className="px-6 py-4 border-b border-[#1E293B] bg-[#0B111C] flex items-center justify-between">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Console SSH</div>
          <h1 className="font-heading text-2xl font-bold text-slate-100">Terminal Workspace</h1>
        </div>
        <div className="text-xs font-mono text-slate-500">
          <span className="text-emerald-400" data-testid="active-sessions-count">{tabs.length}</span> sessão(ões) ativa(s) · permanecem abertas ao navegar
        </div>
      </div>

      <div className="flex items-center gap-1 px-3 py-2 border-b border-[#1E293B] bg-[#0A0F19] overflow-x-auto">
        {tabs.map(t => (
          <div key={t.id}
               data-testid={`tab-${t.id}`}
               onClick={() => setActive(t.id)}
               className={`group flex items-center gap-2 px-3 py-1.5 rounded-md cursor-pointer text-sm border ${
                 active === t.id
                   ? "bg-[#111722] border-[#334155] text-slate-100"
                   : "border-transparent text-slate-400 hover:bg-slate-800/50"
               }`}>
            <TerminalSquare className="w-3.5 h-3.5" />
            <span className="font-mono">{t.name}</span>
            <X className="w-3.5 h-3.5 opacity-60 hover:opacity-100 hover:text-red-400" data-testid={`close-tab-${t.id}`}
               onClick={(e) => { e.stopPropagation(); closeTab(t.id); }} />
          </div>
        ))}
        <div className="relative">
          <Button size="sm" variant="ghost" onClick={() => setPickerOpen(v => !v)} data-testid="add-tab-btn" className="text-slate-300 hover:bg-slate-800">
            <Plus className="w-4 h-4 mr-1" /> Nova aba
          </Button>
          {pickerOpen && (
            <div className="absolute top-full mt-1 left-0 z-30 w-72 bg-[#111722] border border-[#1E293B] rounded-md shadow-xl max-h-80 overflow-y-auto">
              {devices.length === 0 && <div className="p-4 text-sm text-slate-500 font-mono">Sem equipamentos</div>}
              {devices.map(d => (
                <div key={d.id} onClick={() => { openTab(d); setPickerOpen(false); }}
                     data-testid={`picker-device-${d.id}`}
                     className="px-3 py-2 hover:bg-slate-800 cursor-pointer border-b border-[#1E293B] last:border-b-0">
                  <div className="text-sm text-slate-100 flex items-center gap-2">
                    {d.status === "online" ? <Wifi className="w-3 h-3 text-emerald-400" /> : <WifiOff className="w-3 h-3 text-slate-600" />}
                    {d.name}
                  </div>
                  <div className="text-xs font-mono text-slate-500">{d.host}:{d.port}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 bg-[#05070A] relative overflow-hidden">
        {tabs.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <TerminalSquare className="w-10 h-10 mb-3 text-slate-600" />
            <div className="text-sm font-mono">Nenhuma sessão ativa. Clique em &quot;Nova aba&quot; para conectar em um equipamento.</div>
          </div>
        ) : (
          tabs.map(t => <TerminalPane key={t.id} device={t} active={active === t.id} visible={visible} registerWs={registerWs} />)
        )}
      </div>

      {tabs.length > 0 && (
        <div className="relative z-30 flex items-center gap-2 px-3 py-2 border-t border-[#1E293B] bg-[#0A0F19] overflow-x-auto" data-testid="quick-commands-bar">
          <Zap className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          <span className="text-[10px] uppercase tracking-widest text-slate-500 font-mono shrink-0">Favoritos</span>
          {quick.length === 0 && <span className="text-xs text-slate-600 font-mono">Marque scripts como favoritos em "Execução em Lote" para aparecerem aqui.</span>}
          {quick.map(s => (
            <button key={s.id} onClick={() => sendQuick(s)} title={s.content} data-testid={`quick-cmd-${s.id}`}
                    className="shrink-0 text-xs font-mono px-2.5 py-1 rounded border border-[#1E293B] bg-[#111722] text-slate-200 hover:border-amber-500/50 hover:text-amber-300 transition-colors">
              {s.name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
