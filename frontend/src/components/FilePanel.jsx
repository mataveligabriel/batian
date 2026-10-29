import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Folder, FileText, ArrowUp, RefreshCw, Download, Upload, Trash2, Pencil, FolderPlus, Loader2, Unplug, PanelRightClose } from "lucide-react";

// SFTP / SCP ao lado do terminal: a sessão fica aberta mesmo com o painel escondido; o botão mostra o estado
// (cinza = fechado, amarelo = conectando, verde = conectado, vermelho = caiu/erro — clique reconecta).
const size = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b || 0} B`);
const join = (dir, n) => (dir.endsWith("/") ? dir + n : `${dir}/${n}`);
const parent = (p) => { const t = p.replace(/\/+$/, ""); const i = t.lastIndexOf("/"); return i > 0 ? t.slice(0, i) : "/"; };

export function useFileSessions(device, enabled = true) {
  const [sess, setSess] = useState({});          // { sftp: {sid, state, error, cwd}, scp: {...} }
  const [open, setOpen] = useState(null);
  const ref = useRef(sess);
  ref.current = sess;
  const set = (mode, v) => setSess(s => ({ ...s, [mode]: { ...(s[mode] || {}), ...v } }));
  const connect = useCallback(async (mode) => {
    set(mode, { state: "connecting", error: "" });
    try {
      const r = await api.post("/sftp/open", { device_id: device.id, mode });
      set(mode, { sid: r.data.id, state: "on", cwd: r.data.cwd, error: "" });
      setOpen(mode);
    } catch (e) {
      const msg = formatApiError(e);
      set(mode, { state: "error", error: msg });
      toast.error(`${mode.toUpperCase()}: ${msg}`);
    }
  }, [device.id]);
  const click = (mode) => {
    const s = ref.current[mode];
    if (!s || ["off", "lost", "error"].includes(s.state)) return connect(mode);
    if (s.state === "on") setOpen(o => (o === mode ? null : mode));
  };
  const close = async (mode) => {
    const s = ref.current[mode];
    if (s?.sid) { try { await api.delete(`/sftp/${s.sid}`); } catch { /* já fechada */ } }
    set(mode, { state: "off", sid: null });
    setOpen(o => (o === mode ? null : o));
  };
  const lost = (mode) => set(mode, { state: "lost" });
  // estado a cada 10 s (também mantém a sessão viva no servidor enquanto a aba existe)
  useEffect(() => {
    if (!enabled) return undefined;
    const t = setInterval(() => {
      Object.entries(ref.current).forEach(([mode, s]) => {
        if (!s?.sid || s.state !== "on") return;
        api.get(`/sftp/${s.sid}/status`).then(r => { if (r.data.state !== "on") set(mode, { state: "lost" }); }).catch(() => set(mode, { state: "lost" }));
      });
    }, 10000);
    return () => clearInterval(t);
  }, [enabled]);
  useEffect(() => () => {           // fechou a aba do terminal: encerra as sessões de arquivo
    Object.values(ref.current).forEach(s => { if (s?.sid) api.delete(`/sftp/${s.sid}`).catch(() => {}); });
  }, []);
  return { sess, open, setOpen, click, close, lost, connect, device };
}

const STYLE = {
  on: "border-emerald-500/50 bg-emerald-500/15 text-emerald-300",
  connecting: "border-amber-500/50 bg-amber-500/10 text-amber-300",
  lost: "border-red-500/50 bg-red-500/15 text-red-300",
  error: "border-red-500/50 bg-red-500/15 text-red-300",
};
const TITLE = { on: "conectado — clique para mostrar/esconder", connecting: "conectando…", lost: "desconectou — clique para reconectar", error: "falhou — clique para tentar de novo" };

export function FileButtons({ f }) {
  if ((f.device.protocol || "ssh") === "telnet") return null;
  return (
    <div className="flex items-center gap-1 shrink-0" data-testid={`files-btns-${f.device.id}`}>
      {["sftp", "scp"].map(m => {
        const st = f.sess[m]?.state || "off";
        const shown = f.open === m;
        return (
          <button key={m} onClick={() => f.click(m)} title={TITLE[st] || `abrir ${m.toUpperCase()} com ${f.device.name}`} data-testid={`files-btn-${m}-${f.device.id}`} data-state={st}
            className={`h-6 px-2 rounded border text-[11px] font-mono font-semibold tracking-wide flex items-center gap-1 transition-colors ${STYLE[st] || "border-line text-slate-400 hover:text-slate-100 hover:border-line2"} ${shown ? "ring-1 ring-white/20" : ""}`}>
            {st === "connecting" && <Loader2 className="w-3 h-3 animate-spin" />}
            {st !== "off" && st !== "connecting" && <span className={`w-1.5 h-1.5 rounded-full ${st === "on" ? "bg-emerald-400" : "bg-red-400"}`} />}
            {m.toUpperCase()}
          </button>
        );
      })}
    </div>
  );
}

export function FilePanel({ f }) {
  const mode = f.open;
  const s = f.sess[mode] || {};
  const [path, setPath] = useState(s.cwd || "/");
  const [ls, setLs] = useState(null);
  const [busy, setBusy] = useState("");
  const [scpPath, setScpPath] = useState("");
  const inp = useRef(null);
  const fail = useCallback((e) => {
    if (e?.response?.status === 410 || e?.response?.status === 404) f.lost(mode);
    toast.error(formatApiError(e));
  }, [f, mode]);
  const list = useCallback(async (p) => {
    if (mode !== "sftp" || !s.sid) return;
    setBusy("ls");
    try { const r = await api.get(`/sftp/${s.sid}/ls`, { params: { path: p } }); setLs(r.data); setPath(r.data.path); }
    catch (e) { fail(e); } finally { setBusy(""); }
  }, [mode, s.sid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (mode === "sftp" && s.state === "on") list(s.cwd || path); }, [mode, s.sid]); // eslint-disable-line react-hooks/exhaustive-deps
  const download = async (p) => {
    setBusy(p);
    try {
      const r = await api.get(`/sftp/${s.sid}/download`, { params: { path: p }, responseType: "blob" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(r.data); a.download = p.split("/").pop() || "arquivo"; a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { fail(e); } finally { setBusy(""); }
  };
  const upload = async (files, dest) => {
    for (const file of files) {
      setBusy(file.name);
      const fd = new FormData(); fd.append("path", dest); fd.append("file", file);
      try { const r = await api.post(`/sftp/${s.sid}/upload`, fd); toast.success(`${file.name} → ${r.data.path}`); } catch (e) { fail(e); }
    }
    setBusy("");
    if (mode === "sftp") list(path);
  };
  const act = async (fn, ok) => { try { await fn(); if (ok) toast.success(ok); list(path); } catch (e) { fail(e); } };
  const mkdir = () => { const n = window.prompt("Nome da pasta"); if (n) act(() => api.post(`/sftp/${s.sid}/mkdir`, { path: join(path, n) })); };
  const rename = (it) => { const n = window.prompt("Novo nome", it.name); if (n && n !== it.name) act(() => api.post(`/sftp/${s.sid}/rename`, { path: join(path, it.name), to: join(path, n) })); };
  const del = (it) => { if (window.confirm(`Apagar ${join(path, it.name)} no equipamento?`)) act(() => api.delete(`/sftp/${s.sid}/file`, { params: { path: join(path, it.name) } }), "Apagado"); };
  if (!mode) return null;
  const down = s.state !== "on";
  return (
    <div className="w-80 md:w-96 shrink-0 border-l border-line bg-panel flex flex-col min-h-0" data-testid={`files-panel-${f.device.id}`}
         onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (!down && e.dataTransfer.files.length) upload([...e.dataTransfer.files], mode === "sftp" ? `${path.replace(/\/+$/, "")}/` : scpPath); }}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
        <span className={`text-[11px] font-mono font-semibold px-1.5 rounded border ${down ? "border-red-500/50 text-red-300" : "border-emerald-500/50 text-emerald-300"}`}>{mode.toUpperCase()}</span>
        <span className="text-xs text-slate-400 truncate flex-1">{down ? "desconectado" : f.device.name}</span>
        {down ? <button onClick={() => f.connect(mode)} className="text-xs text-brand-soft hover:underline" data-testid="files-reconnect">reconectar</button>
          : <button onClick={() => f.close(mode)} title="Desconectar" className="text-slate-500 hover:text-red-300"><Unplug className="w-4 h-4" /></button>}
        <button onClick={() => f.setOpen(null)} title="Esconder (a sessão continua)" className="text-slate-500 hover:text-slate-200" data-testid="files-hide"><PanelRightClose className="w-4 h-4" /></button>
      </div>
      {mode === "sftp" ? (<>
        <div className="flex items-center gap-1 px-2 py-1.5 border-b border-line">
          <button onClick={() => list(parent(path))} disabled={down} className="text-slate-400 hover:text-slate-100 p-1" title="Pasta acima"><ArrowUp className="w-4 h-4" /></button>
          <input value={path} onChange={e => setPath(e.target.value)} onKeyDown={e => e.key === "Enter" && list(path)} disabled={down}
                 className="flex-1 min-w-0 h-7 rounded bg-sunken border border-line px-2 font-mono text-xs text-slate-200" data-testid="files-path" />
          <button onClick={() => list(path)} disabled={down} className="text-slate-400 hover:text-slate-100 p-1" title="Atualizar">{busy === "ls" ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}</button>
          <button onClick={mkdir} disabled={down} className="text-slate-400 hover:text-slate-100 p-1" title="Nova pasta"><FolderPlus className="w-4 h-4" /></button>
          <button onClick={() => inp.current?.click()} disabled={down} className="text-slate-400 hover:text-slate-100 p-1" title="Enviar arquivo" data-testid="files-upload"><Upload className="w-4 h-4" /></button>
          <input ref={inp} type="file" multiple className="hidden" onChange={e => { upload([...e.target.files], `${path.replace(/\/+$/, "")}/`); e.target.value = ""; }} data-testid="files-upload-input" />
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto text-sm" data-testid="files-list">
          {ls?.items?.length === 0 && <div className="p-4 text-xs text-slate-500 text-center">Pasta vazia</div>}
          {(ls?.items || []).map(it => (
            <div key={it.name} className="group flex items-center gap-2 px-3 py-1 hover:bg-white/[0.04]" onDoubleClick={() => (it.dir ? list(join(path, it.name)) : download(join(path, it.name)))} data-testid={`files-item-${it.name}`}>
              {it.dir ? <Folder className="w-4 h-4 text-amber-300 shrink-0" /> : <FileText className="w-4 h-4 text-slate-500 shrink-0" />}
              <span className="flex-1 truncate text-slate-200 text-[13px]" title={it.name}>{it.name}</span>
              <span className="text-[11px] text-slate-500">{it.dir ? "" : size(it.size)}</span>
              <span className="hidden group-hover:flex items-center gap-1">
                {!it.dir && <button onClick={() => download(join(path, it.name))} title="Baixar" className="text-slate-400 hover:text-slate-100">{busy === join(path, it.name) ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}</button>}
                <button onClick={() => rename(it)} title="Renomear" className="text-slate-400 hover:text-slate-100"><Pencil className="w-3.5 h-3.5" /></button>
                <button onClick={() => del(it)} title="Apagar" className="text-slate-400 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></button>
              </span>
            </div>))}
        </div>
        <div className="px-3 py-1.5 border-t border-line text-[11px] text-slate-500">{busy && busy !== "ls" ? `transferindo ${busy}…` : "duplo clique: abre pasta / baixa arquivo · arraste arquivos para enviar"}</div>
      </>) : (
        <div className="p-3 space-y-3 text-sm" data-testid="files-scp">
          <p className="text-xs text-slate-500">SCP não lista pastas: informe o caminho no equipamento.</p>
          <div className="space-y-1"><label className="text-xs text-slate-400">Arquivo no equipamento</label>
            <input value={scpPath} onChange={e => setScpPath(e.target.value)} placeholder="cfcard:/vrpcfg.zip ou /config/juniper.conf.gz" disabled={down}
                   className="w-full h-8 rounded bg-sunken border border-line px-2 font-mono text-xs text-slate-200" data-testid="files-scp-path" /></div>
          <div className="flex gap-2">
            <button onClick={() => scpPath.trim() && download(scpPath.trim())} disabled={down || !scpPath.trim()} className="h-8 px-3 rounded border border-line text-slate-200 hover:border-brand/60 text-xs flex items-center gap-1 disabled:opacity-40" data-testid="files-scp-get">
              {busy === scpPath.trim() ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}Baixar</button>
            <button onClick={() => inp.current?.click()} disabled={down || !scpPath.trim()} className="h-8 px-3 rounded border border-line text-slate-200 hover:border-brand/60 text-xs flex items-center gap-1 disabled:opacity-40" data-testid="files-scp-put">
              <Upload className="w-3.5 h-3.5" />Enviar para esse caminho</button>
            <input ref={inp} type="file" className="hidden" onChange={e => { upload([...e.target.files], scpPath.trim()); e.target.value = ""; }} />
          </div>
          <p className="text-[11px] text-slate-500">Para enviar mantendo o nome do arquivo, termine o caminho com "/" (ex.: cfcard:/).</p>
          {busy && <div className="text-xs text-slate-400 flex items-center gap-1"><Loader2 className="w-3.5 h-3.5 animate-spin" />transferindo {busy}…</div>}
        </div>
      )}
    </div>
  );
}

