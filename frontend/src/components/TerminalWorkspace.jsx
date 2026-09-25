import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, wsUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import {
  Plus, X, Wifi, WifiOff, Server, TerminalSquare, RotateCw, Zap, Highlighter, Minus, Palette, Copy,
  ClipboardPaste, Eraser, Maximize, Minimize, PanelLeftClose, PanelLeftOpen, Check, CopyCheck, ZoomIn, ZoomOut,
} from "lucide-react";
import { useTerminal } from "@/context/TerminalContext";
import { createHighlighter, HL_COLORS } from "@/lib/termHighlight";
import { TERM_THEMES, useTermPrefs, setTermPrefs, getTermPrefs } from "@/lib/termPrefs";
import { copyText, readText } from "@/lib/clipboard";

const LEGEND = [
  ["IP público", HL_COLORS.ipPublic], ["IP privado/CGNAT", HL_COLORS.ipPrivate], ["Máscara", HL_COLORS.mask],
  ["IPv6", HL_COLORS.ipv6], ["MAC", HL_COLORS.mac], ["Interface", HL_COLORS.iface],
  ["up / established", HL_COLORS.good], ["down / erro", HL_COLORS.bad], ["idle / dBm", HL_COLORS.warn],
];
const themeOf = (p) => (TERM_THEMES[p.theme] || TERM_THEMES.mobaxterm).theme;
const bumpFont = (d) => setTermPrefs({ fontSize: getTermPrefs().fontSize + d });

// ---------- tela cheia (API do navegador / app desktop) ----------
function useFullscreen() {
  const [fs, setFs] = useState(!!document.fullscreenElement);
  useEffect(() => {
    const on = () => setFs(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  const toggle = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else { setTermPrefs({ focusMode: true }); await document.documentElement.requestFullscreen(); }
    } catch { toast.error("O navegador não permitiu tela cheia — use F11"); }
  }, []);
  return [fs, toggle];
}

function TerminalToolbar() {
  const [prefs, setPrefs] = useTermPrefs();
  const [legend, setLegend] = useState(false);
  const btn = "h-7 px-2 rounded border text-xs font-mono flex items-center gap-1 transition-colors";
  const off = "border-[#1E293B] bg-[#111722] text-slate-400 hover:text-slate-200";
  const on = "border-emerald-500/50 bg-emerald-500/10 text-emerald-300";
  return (
    <div className="flex items-center gap-1.5 relative" data-testid="terminal-toolbar">
      <button className={`${btn} ${prefs.highlight ? on : off}`} onClick={() => setPrefs({ highlight: !prefs.highlight })}
              onMouseEnter={() => setLegend(true)} onMouseLeave={() => setLegend(false)}
              data-testid="toggle-highlight" title="Colorir IPs, MACs, interfaces e estados na saída">
        <Highlighter className="w-3.5 h-3.5" /> Realce
      </button>
      {legend && prefs.highlight && (
        <div className="absolute top-full right-0 mt-1 z-40 bg-[#111722] border border-[#1E293B] rounded-md p-2 shadow-xl w-52" data-testid="highlight-legend">
          {LEGEND.map(([l, c]) => (
            <div key={l} className="flex items-center gap-2 text-[11px] font-mono text-slate-300 py-0.5">
              <span className="w-2.5 h-2.5 rounded-sm" style={{ background: c }} /> {l}
            </div>
          ))}
        </div>
      )}
      <label className={`${btn} ${off} cursor-pointer`} title="Tema do terminal">
        <Palette className="w-3.5 h-3.5" />
        <select value={prefs.theme} onChange={e => setPrefs({ theme: e.target.value })} data-testid="terminal-theme"
                className="bg-transparent outline-none text-slate-200 cursor-pointer">
          {Object.entries(TERM_THEMES).map(([k, t]) => <option key={k} value={k} className="bg-[#111722]">{t.label}</option>)}
        </select>
      </label>
      <div className={`${btn} ${off} px-1`} title="Tamanho da fonte (ou Ctrl + roda do mouse no terminal)">
        <button onClick={() => bumpFont(-1)} className="px-1 hover:text-slate-100" data-testid="font-dec"><Minus className="w-3 h-3" /></button>
        <span className="w-5 text-center text-slate-200" data-testid="font-size">{prefs.fontSize}</span>
        <button onClick={() => bumpFont(1)} className="px-1 hover:text-slate-100" data-testid="font-inc"><Plus className="w-3 h-3" /></button>
      </div>
      <button className={`${btn} ${prefs.copyOnSelect ? on : off}`} onClick={() => setPrefs({ copyOnSelect: !prefs.copyOnSelect })}
              data-testid="toggle-copy-select" title="Estilo MobaXterm: o texto selecionado vai direto para a área de transferência">
        <Copy className="w-3.5 h-3.5" /> Selec. copia
      </button>
    </div>
  );
}

// ---------- menu do botão direito (estilo MobaXterm) ----------
function ContextMenu({ menu, onClose, actions }) {
  const [prefs] = useTermPrefs();
  const ref = useRef(null);
  const [pos, setPos] = useState({ left: menu.x, top: menu.y });
  useEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos({ left: Math.min(menu.x, window.innerWidth - r.width - 8), top: Math.min(menu.y, window.innerHeight - r.height - 8) });
    const close = (e) => { if (!ref.current?.contains(e.target)) onClose(); };
    const esc = (e) => { if (e.key === "Escape") onClose(); };
    setTimeout(() => document.addEventListener("mousedown", close), 0);
    document.addEventListener("keydown", esc);
    window.addEventListener("blur", onClose);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); window.removeEventListener("blur", onClose); };
  }, [menu.x, menu.y, onClose]);

  const Item = ({ icon: Icon, label, hint, onClick, disabled, testid }) => (
    <button disabled={disabled} data-testid={testid}
            onClick={() => { onClose(); onClick(); }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-left text-[13px] text-slate-200 hover:bg-[#007AFF]/20 disabled:opacity-35 disabled:hover:bg-transparent">
      {Icon ? <Icon className="w-3.5 h-3.5 text-slate-400" /> : <span className="w-3.5" />}
      <span className="flex-1">{label}</span>
      {hint && <span className="text-[11px] text-slate-500 font-mono">{hint}</span>}
    </button>
  );
  const Radio = ({ checked, label, onClick }) => (
    <button onClick={onClick} className="w-full flex items-center gap-2.5 px-3 py-1 text-left text-[12px] text-slate-300 hover:bg-[#007AFF]/20">
      <span className="w-3.5 flex justify-center">{checked && <Check className="w-3.5 h-3.5 text-emerald-400" />}</span>{label}
    </button>
  );
  const Sep = () => <div className="my-1 border-t border-[#1E293B]" />;
  const Title = ({ children }) => <div className="px-3 pt-1 pb-0.5 text-[10px] uppercase tracking-widest text-slate-500 font-mono">{children}</div>;

  return (
    <div ref={ref} style={pos} data-testid="terminal-context-menu"
         className="fixed z-[100] w-72 py-1 bg-[#161B26] border border-[#2A3345] rounded-md shadow-2xl select-none"
         onContextMenu={(e) => e.preventDefault()}>
      <Item icon={Copy} label="Copiar" hint="Ctrl+Shift+C" disabled={!menu.hasSelection} onClick={actions.copy} testid="ctx-copy" />
      <Item icon={CopyCheck} label="Copiar tudo" onClick={actions.copyAll} testid="ctx-copy-all" />
      <Item icon={ClipboardPaste} label="Colar" hint="Shift+Insert" onClick={actions.paste} testid="ctx-paste" />
      <Sep />
      <Item icon={Eraser} label="Limpar tela" onClick={actions.clear} testid="ctx-clear" />
      <Item icon={ZoomIn} label="Aumentar fonte" hint="Ctrl+Roda ↑" onClick={() => bumpFont(1)} />
      <Item icon={ZoomOut} label="Diminuir fonte" hint="Ctrl+Roda ↓" onClick={() => bumpFont(-1)} />
      <Sep />
      <Item icon={prefs.focusMode ? PanelLeftOpen : PanelLeftClose} label={prefs.focusMode ? "Sair do modo foco" : "Modo foco (esconder menus)"}
            onClick={() => setTermPrefs({ focusMode: !prefs.focusMode })} testid="ctx-focus" />
      <Item icon={document.fullscreenElement ? Minimize : Maximize} label={document.fullscreenElement ? "Sair da tela cheia" : "Tela cheia"} onClick={actions.fullscreen} />
      <Sep />
      <Title>Selecionar texto</Title>
      <Radio checked={prefs.copyOnSelect} label="Copia automaticamente" onClick={() => setTermPrefs({ copyOnSelect: true })} />
      <Radio checked={!prefs.copyOnSelect} label="Não copia (use Copiar)" onClick={() => setTermPrefs({ copyOnSelect: false })} />
      <Title>Botão direito</Title>
      <Radio checked={prefs.rightClick !== "paste"} label="Abre este menu" onClick={() => setTermPrefs({ rightClick: "menu" })} />
      <Radio checked={prefs.rightClick === "paste"} label="Cola direto (Ctrl+botão direito abre o menu)" onClick={() => setTermPrefs({ rightClick: "paste" })} />
      <Title>Confirmar antes de colar</Title>
      <Radio checked={prefs.pasteConfirm === "always"} label="Sempre" onClick={() => setTermPrefs({ pasteConfirm: "always" })} />
      <Radio checked={prefs.pasteConfirm === "multiline"} label="Só quando tiver várias linhas" onClick={() => setTermPrefs({ pasteConfirm: "multiline" })} />
      <Radio checked={prefs.pasteConfirm === "never"} label="Nunca" onClick={() => setTermPrefs({ pasteConfirm: "never" })} />
    </div>
  );
}

// ---------- caixa de confirmação/edição da colagem ----------
function PasteDialog({ dlg, deviceName, onCancel, onConfirm }) {
  const [text, setText] = useState(dlg.text);
  const [skipSingle, setSkipSingle] = useState(false);
  const taRef = useRef(null);
  useEffect(() => { setTimeout(() => { taRef.current?.focus(); if (dlg.text) taRef.current?.setSelectionRange(0, 0); }, 30); }, [dlg.text]);
  const lines = text ? text.replace(/\r\n/g, "\n").split("\n") : [];
  const endsWithEnter = /\r?\n$/.test(text);
  const count = endsWithEnter ? lines.length - 1 : lines.length;
  const submit = () => {
    if (!text) return;
    if (skipSingle) setTermPrefs({ pasteConfirm: "multiline" });
    onConfirm(text);
  };
  return (
    <div className="fixed inset-0 z-[110] bg-black/55 flex items-center justify-center p-4" data-testid="paste-dialog"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="w-full max-w-2xl bg-[#111722] border border-[#2A3345] rounded-lg shadow-2xl">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#1E293B]">
          <div className="text-sm text-slate-100 font-medium flex items-center gap-2">
            <ClipboardPaste className="w-4 h-4 text-[#4DA3FF]" /> Confirmar colagem
          </div>
          <button onClick={onCancel} className="text-slate-400 hover:text-slate-100"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 space-y-2">
          <div className="text-[13px] text-slate-300">
            {dlg.unreadable && !dlg.text
              ? <>O navegador não liberou a leitura da área de transferência. <b>Cole aqui com Ctrl+V</b>, revise e confirme.</>
              : <>Você vai colar em <b className="text-slate-100">{deviceName}</b>. Cada linha é enviada ao equipamento e pode ser executada. Revise e edite se precisar:</>}
          </div>
          <textarea ref={taRef} value={text} onChange={e => setText(e.target.value)} spellCheck={false} data-testid="paste-text"
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } if (e.key === "Escape") onCancel(); }}
                    placeholder="Ctrl+V para colar aqui…"
                    className="w-full h-64 resize-y bg-[#05070A] border border-[#1E293B] rounded p-2.5 font-mono text-[13px] leading-5 text-slate-100 outline-none focus:border-[#007AFF] whitespace-pre overflow-auto" />
          <div className="flex items-center justify-between text-[11px] font-mono text-slate-500">
            <span data-testid="paste-count">{count} linha(s){endsWithEnter ? " · termina com Enter (a última linha será executada)" : ""}</span>
            <span>Ctrl+Enter confirma · Esc cancela</span>
          </div>
          <label className="flex items-center gap-2 text-[12px] text-slate-400 cursor-pointer">
            <input type="checkbox" checked={skipSingle} onChange={e => setSkipSingle(e.target.checked)} />
            Não perguntar de novo quando for uma linha só (ajustável no botão direito do terminal)
          </label>
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-[#1E293B]">
          <Button variant="ghost" onClick={onCancel} data-testid="paste-cancel">Cancelar</Button>
          <Button onClick={submit} disabled={!text} data-testid="paste-confirm" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <ClipboardPaste className="w-4 h-4 mr-2" /> Colar
          </Button>
        </div>
      </div>
    </div>
  );
}

function TerminalPane({ device, active, visible, registerWs, registerPane, onContextMenu, onPasteRequest }) {
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);
  const [status, setStatus] = useState("connecting");
  const [generation, setGeneration] = useState(0);
  const [copied, setCopied] = useState(false);
  const [prefs] = useTermPrefs();
  const prefsRef = useRef(prefs);
  prefsRef.current = prefs;
  const cbRef = useRef({ onContextMenu, onPasteRequest });
  cbRef.current = { onContextMenu, onPasteRequest };

  useEffect(() => {
    if (!containerRef.current) return;
    const p0 = prefsRef.current;
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", "Cascadia Mono", Consolas, Menlo, monospace',
      fontSize: p0.fontSize,
      lineHeight: 1.15,
      scrollback: 10000,
      drawBoldTextInBrightColors: true,
      rightClickSelectsWord: false,
      theme: themeOf(p0),
    });
    const hl = createHighlighter((rest) => { try { term.write(rest); } catch { /* noop */ } });
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

    ws.onopen = () => ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "data") term.write(prefsRef.current.highlight ? hl.process(msg.data) : hl.flush() + msg.data);
        else if (msg.type === "status") {
          term.writeln(`\x1b[36m>> ${msg.message}\x1b[0m`);
          if (msg.message?.toLowerCase().includes("conectado")) setStatus("connected");
        } else if (msg.type === "error") {
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

    const sendInput = (data) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data })); };
    term.onData(sendInput);

    const flashCopied = () => { setCopied(true); setTimeout(() => setCopied(false), 1200); };
    const copySelection = async () => {
      const sel = term.getSelection();
      if (sel && await copyText(sel)) flashCopied();
      term.focus();
    };
    const copyAll = async () => {
      const b = term.buffer.active, out = [];
      for (let i = 0; i < b.length; i++) out.push(b.getLine(i)?.translateToString(true) ?? "");
      while (out.length && !out[out.length - 1]) out.pop();
      if (await copyText(out.join("\n"))) { flashCopied(); toast.success(`${out.length} linha(s) copiadas`); }
      term.focus();
    };
    registerPane?.(device.id, {
      copySelection, copyAll,
      hasSelection: () => term.hasSelection(),
      paste: (text) => { term.paste(text); term.focus(); },
      clear: () => { term.clear(); term.focus(); },
      focus: () => term.focus(),
    });

    // Ctrl+Shift+C copia a seleção (Ctrl+C continua indo para o equipamento)
    term.attachCustomKeyEventHandler((e) => {
      if (e.type === "keydown" && e.ctrlKey && e.shiftKey && (e.key === "C" || e.key === "c")) { copySelection(); return false; }
      return true;
    });

    const el = containerRef.current;
    // selecionar = copiar (estilo MobaXterm), no soltar do mouse
    const onMouseUp = (e) => { if (e.button === 0 && prefsRef.current.copyOnSelect && term.hasSelection()) copySelection(); };
    // botão direito: menu ou colar direto
    const onContext = (e) => {
      e.preventDefault();
      if (prefsRef.current.rightClick === "paste" && !e.ctrlKey) cbRef.current.onPasteRequest(device.id);
      else cbRef.current.onContextMenu({ x: e.clientX, y: e.clientY, deviceId: device.id, hasSelection: term.hasSelection() });
    };
    // Ctrl+V / Shift+Insert / Ctrl+Shift+V: intercepta para passar pela caixa de confirmação
    const onPaste = (e) => {
      const text = e.clipboardData?.getData("text/plain") ?? "";
      e.preventDefault(); e.stopPropagation();
      if (text) cbRef.current.onPasteRequest(device.id, text);
    };
    // Ctrl + roda do mouse muda a fonte
    const onWheel = (e) => { if (e.ctrlKey) { e.preventDefault(); bumpFont(e.deltaY < 0 ? 1 : -1); } };
    el.addEventListener("mouseup", onMouseUp);
    el.addEventListener("contextmenu", onContext);
    el.addEventListener("paste", onPaste, true);
    el.addEventListener("wheel", onWheel, { passive: false });

    // reajusta quando o espaço muda (janela, menu lateral encolhido, modo foco, tela cheia)
    let raf = 0;
    const refit = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        try {
          if (!el.offsetWidth || !el.offsetHeight) return;
          const before = `${term.cols}x${term.rows}`;
          fit.fit();
          if (`${term.cols}x${term.rows}` !== before && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
        } catch { /* noop */ }
      });
    };
    const ro = new ResizeObserver(refit);
    ro.observe(el);

    return () => {
      ro.disconnect();
      el.removeEventListener("mouseup", onMouseUp);
      el.removeEventListener("contextmenu", onContext);
      el.removeEventListener("paste", onPaste, true);
      el.removeEventListener("wheel", onWheel);
      registerPane?.(device.id, null);
      try { ws.close(); } catch { /* noop */ }
      try { term.dispose(); } catch { /* noop */ }
    };
  }, [device.id, generation]); // eslint-disable-line react-hooks/exhaustive-deps

  // aplica tema / fonte quando o usuário muda
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = themeOf(prefs);
    if (term.options.fontSize !== prefs.fontSize) {
      term.options.fontSize = prefs.fontSize;
      try {
        fitRef.current?.fit();
        const ws = wsRef.current;
        if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      } catch { /* noop */ }
    }
  }, [prefs.theme, prefs.fontSize]); // eslint-disable-line react-hooks/exhaustive-deps

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
      <div className={`flex items-center justify-between border-b border-[#1E293B] bg-[#0B111C] ${prefs.focusMode ? "px-3 py-1" : "px-4 py-2"}`}>
        <div className="flex items-center gap-3 min-w-0">
          <Server className="w-4 h-4 text-[#4DA3FF] shrink-0" />
          <div className={prefs.focusMode ? "flex items-baseline gap-3 min-w-0" : "min-w-0"}>
            <div className="text-sm font-medium text-slate-100 truncate">{device.name}</div>
            <div className="text-[11px] font-mono text-slate-500 truncate">{(device.protocol || "ssh").toUpperCase()} · {device.host}:{device.port} · {device.username || "padrão"}</div>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {copied && <span className="text-[11px] font-mono text-emerald-400 flex items-center gap-1" data-testid={`copied-${device.id}`}><Check className="w-3 h-3" /> copiado</span>}
          {(status === "closed" || status === "error") && (
            <Button size="sm" variant="ghost" onClick={() => setGeneration(g => g + 1)} data-testid={`reconnect-${device.id}`} className="h-7 text-xs text-slate-300 hover:bg-slate-800">
              <RotateCw className="w-3.5 h-3.5 mr-1" /> Reconectar
            </Button>
          )}
          <span data-testid={`terminal-status-${device.id}`} className={`text-[11px] font-mono uppercase tracking-widest ${
            status === "connected" ? "text-emerald-400" : status === "connecting" ? "text-amber-400" : "text-red-400"
          }`}>{label}</span>
        </div>
      </div>
      <div ref={containerRef} className="xterm-container flex-1 min-h-0" data-testid={`xterm-${device.id}`} style={{ background: themeOf(prefs).background }} />
    </div>
  );
}

export function TerminalWorkspace({ visible }) {
  const { tabs, active, setActive, openTab, closeTab } = useTerminal();
  const [termPrefs] = useTermPrefs();
  const [devices, setDevices] = useState([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [quick, setQuick] = useState([]);
  const [menu, setMenu] = useState(null);
  const [pasteDlg, setPasteDlg] = useState(null);
  const [isFs, toggleFs] = useFullscreen();
  const wsMap = useRef({});
  const panes = useRef({});
  const registerWs = useCallback((id, ws) => { wsMap.current[id] = ws; }, []);
  const registerPane = useCallback((id, api_) => { if (api_) panes.current[id] = api_; else delete panes.current[id]; }, []);
  const focus = termPrefs.focusMode;

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

  // colar: passa pela caixa de confirmação conforme a preferência
  const requestPaste = useCallback(async (deviceId, text) => {
    const pane = panes.current[deviceId];
    if (!pane) return;
    let t = text;
    if (t === undefined) t = await readText(); // null = navegador não deixou ler
    const mode = getTermPrefs().pasteConfirm;
    const multi = !!t && /[\r\n]/.test(t.replace(/\r?\n$/, ""));
    if (t && (mode === "never" || (mode === "multiline" && !multi))) return pane.paste(t);
    setPasteDlg({ deviceId, text: t || "", unreadable: t === null });
  }, []);

  const closeMenu = useCallback(() => setMenu(null), []);
  const menuPane = menu && panes.current[menu.deviceId];
  const deviceName = (id) => tabs.find(t => t.id === id)?.name || "equipamento";

  const iconBtn = "h-7 px-2 rounded border border-[#1E293B] bg-[#111722] text-slate-300 hover:text-slate-100 text-xs font-mono flex items-center gap-1.5 shrink-0";

  return (
    <div className={`absolute inset-0 flex flex-col ${visible ? "visible z-20" : "invisible z-0 pointer-events-none"}`} data-testid="terminal-page">
      {!focus && (
        <div className="px-6 py-4 border-b border-[#1E293B] bg-[#0B111C] flex items-center justify-between">
          <div>
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Console SSH</div>
            <h1 className="font-heading text-2xl font-bold text-slate-100">Terminal Workspace</h1>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-xs font-mono text-slate-500 hidden 2xl:block">
              <span className="text-emerald-400" data-testid="active-sessions-count">{tabs.length}</span> sessão(ões) ativa(s) · permanecem abertas ao navegar
            </div>
            <TerminalToolbar />
          </div>
        </div>
      )}

      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[#1E293B] bg-[#0A0F19]">
        <div className="flex items-center gap-1 overflow-x-auto flex-1 min-w-0">
          {tabs.map(t => (
            <div key={t.id} data-testid={`tab-${t.id}`} onClick={() => setActive(t.id)} title={`${t.host}:${t.port}`}
                 className={`group flex items-center gap-2 px-3 py-1.5 rounded-md cursor-pointer text-sm border shrink-0 ${
                   active === t.id ? "bg-[#111722] border-[#334155] text-slate-100" : "border-transparent text-slate-400 hover:bg-slate-800/50"}`}>
              <TerminalSquare className="w-3.5 h-3.5" />
              <span className="font-mono">{t.name}</span>
              <X className="w-3.5 h-3.5 opacity-60 hover:opacity-100 hover:text-red-400" data-testid={`close-tab-${t.id}`}
                 onClick={(e) => { e.stopPropagation(); closeTab(t.id); }} />
            </div>
          ))}
        </div>
        <div className="relative shrink-0">
          <Button size="sm" variant="ghost" onClick={() => setPickerOpen(v => !v)} data-testid="add-tab-btn" className="text-slate-300 hover:bg-slate-800 h-8">
            <Plus className="w-4 h-4 mr-1" /> Nova aba
          </Button>
          {pickerOpen && (
            <div className="absolute top-full mt-1 right-0 z-40 w-72 bg-[#111722] border border-[#1E293B] rounded-md shadow-xl max-h-80 overflow-y-auto">
              {devices.length === 0 && <div className="p-4 text-sm text-slate-500 font-mono">Sem equipamentos</div>}
              {devices.map(d => (
                <div key={d.id} onClick={() => { openTab(d); setPickerOpen(false); }} data-testid={`picker-device-${d.id}`}
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
        {focus && <div className="hidden lg:block ml-1"><TerminalToolbar /></div>}
        <button className={iconBtn} onClick={toggleFs} data-testid="fullscreen-btn" title={isFs ? "Sair da tela cheia" : "Tela cheia"}>
          {isFs ? <Minimize className="w-3.5 h-3.5" /> : <Maximize className="w-3.5 h-3.5" />}
        </button>
        <button className={`${iconBtn} ${focus ? "border-[#007AFF]/60 text-[#4DA3FF]" : ""}`} data-testid="focus-mode-btn"
                onClick={() => setTermPrefs({ focusMode: !focus })}
                title={focus ? "Mostrar menus" : "Modo foco: esconder o menu lateral e o cabeçalho"}>
          {focus ? <><PanelLeftOpen className="w-3.5 h-3.5" /> Menus</> : <><PanelLeftClose className="w-3.5 h-3.5" /> Foco</>}
        </button>
      </div>

      <div className="flex-1 min-h-0 relative overflow-hidden" style={{ background: themeOf(termPrefs).background }}>
        {tabs.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-500">
            <TerminalSquare className="w-10 h-10 mb-3 text-slate-600" />
            <div className="text-sm font-mono">Nenhuma sessão ativa. Clique em &quot;Nova aba&quot; para conectar em um equipamento.</div>
          </div>
        ) : (
          tabs.map(t => (
            <TerminalPane key={t.id} device={t} active={active === t.id} visible={visible}
                          registerWs={registerWs} registerPane={registerPane}
                          onContextMenu={setMenu} onPasteRequest={requestPaste} />
          ))
        )}
      </div>

      {tabs.length > 0 && !focus && (
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

      {menu && menuPane && (
        <ContextMenu menu={menu} onClose={closeMenu} actions={{
          copy: () => menuPane.copySelection(),
          copyAll: () => menuPane.copyAll(),
          paste: () => requestPaste(menu.deviceId),
          clear: () => menuPane.clear(),
          fullscreen: toggleFs,
        }} />
      )}
      {pasteDlg && (
        <PasteDialog dlg={pasteDlg} deviceName={deviceName(pasteDlg.deviceId)}
                     onCancel={() => { const id = pasteDlg.deviceId; setPasteDlg(null); panes.current[id]?.focus(); }}
                     onConfirm={(text) => { const id = pasteDlg.deviceId; setPasteDlg(null); panes.current[id]?.paste(text); }} />
      )}
    </div>
  );
}
