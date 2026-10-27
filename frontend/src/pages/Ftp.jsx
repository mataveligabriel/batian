import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { FolderUp, Plus, Trash2, Copy, Download, Upload, Folder, FileText, RefreshCw, Loader2, Save, CheckCircle2, XCircle, AlertTriangle, Eye, EyeOff, Clock, ChevronRight, Pencil, ArrowUp } from "lucide-react";

const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const size = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b || 0} B`);
const copy = (t) => { try { navigator.clipboard.writeText(t).then(() => toast.success("Copiado"), () => toast.error("Não consegui copiar")); } catch (e) { toast.error("Não consegui copiar"); } };
const VALID = [[2, "2 horas"], [24, "24 horas"], [168, "7 dias"], [720, "30 dias"], [0, "Sem prazo (backup automático)"]];

// comandos por fabricante: baixar (equipamento ← BastiON) e enviar (equipamento → BastiON)
const VENDORS = {
  zte_titan: { name: "ZTE TITAN (C600/C650)", local: "root:/datadisk0/DATA0/startrun.dat",
    down: (c) => `copy ftp //${c.hp}/${c.file}@${c.u}:${c.p} ${c.local}`, up: (c) => `copy ${c.local} ftp //${c.hp}/${c.file}@${c.u}:${c.p}` },
  huawei: { name: "Huawei VRP (ftp interativo)", local: "",
    down: (c) => `ftp ${c.h}${c.port !== 21 ? " " + c.port : ""}\n  ${c.u}\n  ${c.p}\nbinary\nget ${c.file}\nbye`,
    up: (c) => `ftp ${c.h}${c.port !== 21 ? " " + c.port : ""}\n  ${c.u}\n  ${c.p}\nbinary\nput ${c.file}\nbye` },
  cisco: { name: "Cisco IOS / IOS-XE", local: "flash:",
    down: (c) => `copy ftp://${c.u}:${c.p}@${c.hp}/${c.file} ${c.local}${c.file}`, up: (c) => `copy running-config ftp://${c.u}:${c.p}@${c.hp}/${c.file}` },
  juniper: { name: "Juniper Junos", local: "/var/tmp/",
    down: (c) => `file copy ftp://${c.u}:${c.p}@${c.hp}/${c.file} ${c.local}${c.file}`, up: (c) => `file copy /config/juniper.conf.gz ftp://${c.u}:${c.p}@${c.hp}/${c.file}` },
  mikrotik: { name: "MikroTik RouterOS", local: "",
    down: (c) => `/tool fetch mode=ftp address=${c.h} port=${c.port} user=${c.u} password=${c.p} src-path=${c.file} dst-path=${c.file}`,
    up: (c) => `/tool fetch mode=ftp upload=yes address=${c.h} port=${c.port} user=${c.u} password=${c.p} src-path=${c.file} dst-path=${c.file}` },
  linux: { name: "Linux (curl)", local: "",
    down: (c) => `curl -u ${c.u}:${c.p} ftp://${c.hp}/${c.file} -o ${c.file}`, up: (c) => `curl -u ${c.u}:${c.p} -T ${c.file} ftp://${c.hp}/` },
  generic: { name: "Outro (dados para digitar)", local: "",
    down: (c) => `Servidor: ${c.h}\nPorta: ${c.port}\nUsuário: ${c.u}\nSenha: ${c.p}\nArquivo: ${c.file}\nModo: passivo ou ativo (os dois funcionam)`, up: (c) => "(mesmos dados)" },
};

function where(st, a) {
  if (a.via === "local") return { h: st?.local?.host || "<IP_DO_BASTION>", port: st?.local?.port || 2121, label: "BastiON (direto)", state: st?.local?.state };
  const j = st?.jumps?.[a.via];
  return { h: j?.ip || "<IP_DO_JUMP>", port: j?.port || 2121, label: j?.name || "jump", state: j?.state, j };
}

function StatusBar({ st, agents }) {
  if (!st) return null;
  const jumps = Object.entries(st.jumps || {});
  return (
    <Card className="bg-surface border-line p-3 space-y-2" data-testid="ftp-status">
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        <span className="flex items-center gap-1.5">{st.local.state === "no ar" ? <CheckCircle2 className="w-4 h-4 text-emerald-300" /> : <span className="w-2 h-2 rounded-full bg-slate-600" />}
          <span className="text-slate-300">BastiON</span><span className="font-mono text-xs text-slate-500">{st.local.state === "no ar" ? `${st.local.host || "?"}:${st.local.port}` : st.local.state}</span>
          {st.local.error && <span className="text-xs text-red-300">{st.local.error}</span>}</span>
        {jumps.map(([id, j]) => (
          <span key={id} className="flex items-center gap-1.5" data-testid={`ftp-jump-${id}`}>
            {j.state === "no ar" ? <CheckCircle2 className="w-4 h-4 text-emerald-300" /> : j.state === "erro" ? <XCircle className="w-4 h-4 text-red-300" /> : <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
            <span className="text-slate-300">{j.name}</span><span className="font-mono text-xs text-slate-500">{j.state === "no ar" ? `${j.ip || "?"}:${j.port}` : j.state}</span>
          </span>))}
        {!jumps.length && st.local.state !== "no ar" && <span className="text-xs text-slate-500">Nenhuma porta aberta: crie um acesso para abrir.</span>}
      </div>
      {jumps.filter(([, j]) => j.error).map(([id, j]) => <div key={id} className="text-xs text-red-300">{j.name}: {j.error}</div>)}
      {jumps.filter(([, j]) => j.state === "no ar" && j.bound && j.bound !== "0.0.0.0").map(([id, j]) => (
        <div key={id} className="text-xs text-amber-300 space-y-1" data-testid={`ftp-gw-${id}`}>
          <div><AlertTriangle className="w-3.5 h-3.5 inline mr-1" />{j.name}: o SSH do jump abriu a porta só em {j.bound} — os equipamentos não alcançam. No jump, como root:</div>
          <pre className="font-mono text-[11px] text-slate-300 bg-sunken border border-line rounded p-2 whitespace-pre-wrap">{"echo 'GatewayPorts clientspecified' > /etc/ssh/sshd_config.d/bastion-ftp.conf && systemctl reload ssh || systemctl reload sshd"}</pre>
        </div>))}
      {jumps.filter(([, j]) => j.state === "no ar" && !j.ip).map(([id, j]) => <div key={id} className="text-xs text-amber-300"><AlertTriangle className="w-3.5 h-3.5 inline mr-1" />{j.name}: não descobri o IP do jump — informe em Ajustes (o modo passivo precisa dele; o ativo funciona sem).</div>)}
    </Card>
  );
}

function Commands({ a, st }) {
  const [v, setV] = useState("zte_titan");
  const [file, setFile] = useState("startrun.dat");
  const w = where(st, a);
  const vd = VENDORS[v];
  const [local, setLocal] = useState(vd.local);
  useEffect(() => { setLocal(VENDORS[v].local); }, [v]);
  const c = { h: w.h, port: w.port, hp: w.port === 21 ? w.h : `${w.h}:${w.port}`, u: a.username, p: a.password, file: file || "arquivo", local };
  return (
    <div className="space-y-2 pt-2" data-testid={`ftp-cmds-${a.id}`}>
      <div className="grid gap-2 sm:grid-cols-[14rem_1fr_1fr]">
        <select value={v} onChange={e => setV(e.target.value)} className={sel} data-testid="ftp-cmd-vendor">{Object.entries(VENDORS).map(([k, x]) => <option key={k} value={k}>{x.name}</option>)}</select>
        <Input value={file} onChange={e => setFile(e.target.value.replace(/\s/g, "_"))} placeholder="nome do arquivo no BastiON" className="bg-sunken border-line h-9 font-mono" data-testid="ftp-cmd-file" />
        {vd.local !== "" && <Input value={local} onChange={e => setLocal(e.target.value)} placeholder="caminho no equipamento" className="bg-sunken border-line h-9 font-mono" />}
      </div>
      {[["Equipamento baixa do BastiON", vd.down(c), "down"], ...(a.write ? [["Equipamento envia para o BastiON", vd.up(c), "up"]] : [])].map(([t, txt, k]) => (
        <div key={k}>
          <div className="flex items-center justify-between"><span className="text-[11px] text-slate-500">{t}</span>
            <button onClick={() => copy(txt)} className="text-slate-500 hover:text-slate-200"><Copy className="w-3.5 h-3.5" /></button></div>
          <pre className="font-mono text-[12px] text-slate-200 bg-sunken border border-line rounded p-2 whitespace-pre-wrap break-all" data-testid={`ftp-cmd-${k}`}>{txt}</pre>
        </div>))}
      {w.port !== 21 && <div className="text-[11px] text-slate-500">Porta {w.port}: se o equipamento não deixar escolher a porta, redirecione a 21 no {a.via === "local" ? "servidor" : "jump"} (root): <span className="font-mono text-slate-400">iptables -t nat -A PREROUTING -p tcp --dport 21 -j REDIRECT --to-ports {w.port}</span></div>}
    </div>
  );
}

function Accesses({ st, agents, reloadSt }) {
  const [rows, setRows] = useState(null);
  const [f, setF] = useState({ name: "", via: "local", folder: "", write: true, hours: 24, allow_ips: "" });
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState({});
  const [show, setShow] = useState({});
  const load = useCallback(() => api.get("/ftp/accesses").then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const add = async () => {
    setBusy(true);
    try {
      const r = await api.post("/ftp/accesses", { ...f, hours: Number(f.hours), allow_ips: f.allow_ips.split(/[\s,;]+/).filter(Boolean) });
      toast.success(`Acesso criado — usuário ${r.data.username}`);
      setF({ ...f, name: "", folder: "" }); setOpen({ [r.data.id]: true }); await load(); setTimeout(reloadSt, 1500); setTimeout(reloadSt, 5000);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const renew = async (a, hours) => { try { await api.post(`/ftp/accesses/${a.id}/renew`, { hours, enabled: true }); load(); setTimeout(reloadSt, 1500); } catch (e) { toast.error(formatApiError(e)); } };
  const del = async (a) => { try { await api.delete(`/ftp/accesses/${a.id}`); load(); setTimeout(reloadSt, 1500); } catch (e) { toast.error(formatApiError(e)); } };
  const agentName = (id) => (id === "local" ? "BastiON (direto)" : agents.find(x => x.id === id)?.name || id);
  return (
    <div className="space-y-3" data-testid="ftp-accesses">
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="text-sm font-semibold text-slate-200">Novo acesso</div>
        <div className="grid gap-2 sm:grid-cols-[1fr_14rem_1fr_12rem] items-end">
          <div><label className={lbl}>Nome</label><Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Backup OLT Guaçuí" className="bg-sunken border-line h-9" data-testid="ftp-new-name" /></div>
          <div><label className={lbl}>Por onde o equipamento chega</label>
            <select value={f.via} onChange={e => setF({ ...f, via: e.target.value })} className={`${sel} w-full`} data-testid="ftp-new-via">
              <option value="local">BastiON (direto)</option>{agents.map(a => <option key={a.id} value={a.id}>Jump {a.name}</option>)}</select></div>
          <div><label className={lbl}>Pasta (vazio = o nome)</label><Input value={f.folder} onChange={e => setF({ ...f, folder: e.target.value })} placeholder="backups/olt-guacui" className="bg-sunken border-line h-9 font-mono" data-testid="ftp-new-folder" /></div>
          <div><label className={lbl}>Validade</label>
            <select value={f.hours} onChange={e => setF({ ...f, hours: e.target.value })} className={`${sel} w-full`} data-testid="ftp-new-hours">{VALID.map(([h, l]) => <option key={h} value={h}>{l}</option>)}</select></div>
          <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.write} onCheckedChange={v => setF({ ...f, write: v })} data-testid="ftp-new-write" />Pode enviar arquivos (senão, só baixar)</label>
          <div className="sm:col-span-2"><label className={lbl}>Só destes IPs (vazio = qualquer equipamento)</label><Input value={f.allow_ips} onChange={e => setF({ ...f, allow_ips: e.target.value })} placeholder="10.20.0.0/24" className="bg-sunken border-line h-9 font-mono" /></div>
          <Button onClick={add} disabled={busy || !f.name.trim()} className="bg-brand hover:bg-brand-strong text-onbrand h-9" data-testid="ftp-new-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Plus className="w-4 h-4 mr-1" />}Criar acesso</Button>
        </div>
        <p className="text-[11px] text-slate-500">Usuário e senha são gerados e valem só por aquele caminho (no jump escolhido ou no BastiON). Pelo jump, a porta abre no próprio jump: o equipamento usa o IP do jump. A porta fecha sozinha quando não houver acesso válido.</p>
      </Card>
      {rows && rows.length === 0 && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">Nenhum acesso ainda.</Card>}
      {(rows || []).map(a => {
        const w = where(st, a);
        return (
          <Card key={a.id} className={`bg-surface border-line p-4 ${a.active ? "" : "opacity-60"}`} data-testid={`ftp-acc-${a.id}`}>
            <div className="flex flex-wrap items-center gap-3">
              <span className={`w-2 h-2 rounded-full ${a.active ? (w.state === "no ar" ? "bg-emerald-400" : "bg-amber-400") : "bg-slate-600"}`} />
              <div className="min-w-0"><div className="text-slate-100">{a.name}</div>
                <div className="text-[11px] text-slate-500">{agentName(a.via)} · pasta <span className="font-mono">{a.folder || "/"}</span> · {a.write ? "baixar e enviar" : "só baixar"}{a.allow_ips?.length ? ` · só ${a.allow_ips.join(", ")}` : ""}</div></div>
              <div className="font-mono text-xs text-slate-300 bg-sunken border border-line rounded px-2 py-1 flex items-center gap-2">
                <span>{w.h}:{w.port}</span><span className="text-slate-500">|</span><span>{a.username}</span><span className="text-slate-500">|</span>
                <span data-testid={`ftp-pw-${a.id}`}>{show[a.id] ? a.password : "••••••••"}</span>
                <button onClick={() => setShow({ ...show, [a.id]: !show[a.id] })} className="text-slate-500 hover:text-slate-200">{show[a.id] ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}</button>
                <button onClick={() => copy(a.password)} className="text-slate-500 hover:text-slate-200"><Copy className="w-3.5 h-3.5" /></button>
              </div>
              <span className="text-[11px] text-slate-500 flex items-center gap-1"><Clock className="w-3 h-3" />{a.expires_at ? (a.active ? `até ${fmt(a.expires_at)}` : `venceu ${fmt(a.expires_at)}`) : "sem prazo"}{a.last_used ? ` · usado ${fmt(a.last_used)}` : ""}</span>
              <div className="ml-auto flex gap-1">
                <Button size="sm" variant="outline" onClick={() => setOpen({ ...open, [a.id]: !open[a.id] })} className="h-8 border-line bg-transparent" data-testid={`ftp-acc-cmds-${a.id}`}>Comandos</Button>
                <select value="" onChange={e => e.target.value !== "" && renew(a, Number(e.target.value))} className={`${sel} h-8 text-xs`} title="Renovar"><option value="">Renovar…</option>{VALID.map(([h, l]) => <option key={h} value={h}>{l}</option>)}</select>
                <Button size="sm" variant="ghost" onClick={() => del(a)} className="h-8 text-slate-500 hover:text-red-300" data-testid={`ftp-acc-del-${a.id}`}><Trash2 className="w-4 h-4" /></Button>
              </div>
            </div>
            {open[a.id] && <Commands a={a} st={st} />}
          </Card>);
      })}
    </div>
  );
}

function Files() {
  const [path, setPath] = useState("");
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState("");
  const inp = useRef(null);
  const load = useCallback(() => api.get("/ftp/files", { params: { path } }).then(r => setD(r.data)).catch(e => toast.error(formatApiError(e))), [path]);
  useEffect(() => { load(); }, [load]);
  const join = (n) => (path ? `${path}/${n}` : n);
  const crumbs = path ? path.split("/") : [];
  const upload = async (files) => {
    for (const file of files) {
      setBusy(file.name);
      const fd = new FormData(); fd.append("path", path); fd.append("file", file);
      try { await api.post("/ftp/files/upload", fd); toast.success(`${file.name} enviado`); } catch (e) { toast.error(formatApiError(e)); }
    }
    setBusy(""); load();
  };
  const download = async (n) => {
    try {
      const r = await api.get("/ftp/files/download", { params: { path: join(n) }, responseType: "blob" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(r.data); a.download = n; a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const mkdir = async () => { const n = window.prompt("Nome da pasta"); if (!n) return; try { await api.post("/ftp/files/mkdir", { path: join(n) }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const rename = async (n) => { const t = window.prompt("Novo nome", n); if (!t || t === n) return; try { await api.post("/ftp/files/rename", { path: join(n), to: join(t) }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const del = async (it) => { if (!window.confirm(`Apagar ${it.name}?`)) return; try { await api.delete("/ftp/files", { params: { path: join(it.name) } }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <Card className="bg-surface border-line" data-testid="ftp-files"
          onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (e.dataTransfer.files.length) upload([...e.dataTransfer.files]); }}>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
        <button onClick={() => setPath("")} className="text-sm text-slate-300 hover:underline" data-testid="ftp-crumb-root">Arquivos</button>
        {crumbs.map((c, i) => <span key={i} className="flex items-center gap-1 text-sm"><ChevronRight className="w-3.5 h-3.5 text-slate-600" /><button onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))} className="text-slate-300 hover:underline">{c}</button></span>)}
        <span className="text-xs text-slate-500 ml-2">{d ? `${size(d.total_bytes)} no total` : ""}</span>
        <div className="ml-auto flex gap-1">
          <input ref={inp} type="file" multiple className="hidden" onChange={e => { upload([...e.target.files]); e.target.value = ""; }} data-testid="ftp-upload-input" />
          <Button size="sm" variant="outline" onClick={() => inp.current?.click()} disabled={!!busy} className="border-line bg-transparent h-8" data-testid="ftp-upload">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Upload className="w-4 h-4 mr-1" />}{busy ? busy.slice(0, 20) : "Enviar arquivo"}</Button>
          <Button size="sm" variant="ghost" onClick={mkdir} className="h-8 text-slate-300"><FolderUp className="w-4 h-4 mr-1" />Pasta</Button>
          <Button size="sm" variant="ghost" onClick={load} className="h-8 text-slate-400"><RefreshCw className="w-4 h-4" /></Button>
        </div>
      </div>
      {d && d.items.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Pasta vazia. Arraste arquivos para cá (ex.: firmware para as OLTs baixarem).</div>}
      <div className="divide-y divide-line">{(d?.items || []).map(it => (
        <div key={it.name} className="flex items-center gap-3 px-4 py-2 text-sm hover:bg-white/[0.02]" data-testid={`ftp-item-${it.name}`}>
          {it.dir ? <Folder className="w-4 h-4 text-brand-soft" /> : <FileText className="w-4 h-4 text-slate-500" />}
          {it.dir ? <button onClick={() => setPath(join(it.name))} className="text-slate-100 hover:underline flex-1 text-left truncate">{it.name}</button> : <span className="text-slate-100 flex-1 truncate font-mono text-[13px]">{it.name}</span>}
          <span className="text-xs text-slate-500 w-20 text-right">{it.dir ? "" : size(it.size)}</span>
          <span className="text-xs text-slate-500 w-28">{fmt(it.mtime)}</span>
          <div className="flex gap-1 w-24 justify-end">
            {!it.dir && <button onClick={() => download(it.name)} className="text-slate-500 hover:text-slate-200" title="Baixar"><Download className="w-4 h-4" /></button>}
            <button onClick={() => rename(it.name)} className="text-slate-500 hover:text-slate-200" title="Renomear"><Pencil className="w-4 h-4" /></button>
            <button onClick={() => del(it)} className="text-slate-500 hover:text-red-300" title="Apagar"><Trash2 className="w-4 h-4" /></button>
          </div>
        </div>))}</div>
    </Card>
  );
}

const PATH_HINT = { zte: "/datadisk0/DATA0/startrun.dat", huawei: "vrpcfg.zip", cisco: "flash:/", juniper: "/config/juniper.conf.gz", mikrotik: "/", datacom: "/" };

function DeviceXfer() {
  const [devs, setDevs] = useState([]);
  const [dev, setDev] = useState("");
  const [path, setPath] = useState("");
  const [ls, setLs] = useState(null);
  const [lsErr, setLsErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [dest, setDest] = useState("");
  const [files, setFiles] = useState([]);
  const [local, setLocal] = useState("");
  const [rpush, setRpush] = useState("");
  const [jobs, setJobs] = useState([]);
  const d = devs.find(x => x.id === dev);
  useEffect(() => {
    api.get("/devices").then(r => setDevs((r.data || []).filter(x => x.protocol !== "telnet"))).catch(() => {});
    api.get("/ftp/files/all").then(r => setFiles(r.data)).catch(() => {});
  }, []);
  const loadJobs = useCallback(() => api.get("/ftp/device/jobs").then(r => setJobs(r.data)).catch(() => {}), []);
  useEffect(() => { loadJobs(); }, [loadJobs]);
  const running = jobs.some(j => ["na fila", "conectando", "copiando"].includes(j.state));
  useEffect(() => {
    if (!running) return undefined;
    const i = setInterval(() => { loadJobs(); }, 1500);
    return () => clearInterval(i);
  }, [running, loadJobs]);
  useEffect(() => { if (!running) api.get("/ftp/files/all").then(r => setFiles(r.data)).catch(() => {}); }, [running]);
  const pick = (id) => { setDev(id); setLs(null); setLsErr(""); const t = devs.find(x => x.id === id)?.device_type; setPath(PATH_HINT[t] || ""); };
  const list = async (p) => {
    setBusy(true); setLsErr("");
    try { const r = await api.get("/ftp/device/ls", { params: { device_id: dev, path: p || "." } }); setLs(r.data); setPath(r.data.path); }
    catch (e) { setLs(null); setLsErr(formatApiError(e)); } finally { setBusy(false); }
  };
  const start = async (body) => {
    try { await api.post("/ftp/device/transfer", { device_id: dev, ...body }); toast.success("Transferência iniciada"); loadJobs(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const up = () => { const p = (ls?.path || path).replace(/\/+$/, ""); const i = p.lastIndexOf("/"); list(i > 0 ? p.slice(0, i) : "/"); };
  return (
    <div className="space-y-3" data-testid="ftp-dev">
      <Card className="bg-surface border-line p-3 text-xs text-slate-400">
        Para equipamento que não alcança o BastiON nem o jump (NAT no meio): o BastiON entra nele pelo mesmo caminho do SSH e copia o arquivo por <b className="text-slate-200">SFTP</b> (ou SCP, se não houver SFTP). O arquivo trazido cai na aba Arquivos.
      </Card>
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="grid gap-2 sm:grid-cols-[16rem_1fr_auto] items-end">
          <div><label className={lbl}>Equipamento</label>
            <select value={dev} onChange={e => pick(e.target.value)} className={`${sel} w-full`} data-testid="ftp-dev-sel">
              <option value="">— escolha —</option>{devs.map(x => <option key={x.id} value={x.id}>{x.name} ({x.host})</option>)}</select></div>
          <div><label className={lbl}>Caminho no equipamento (arquivo ou pasta)</label>
            <Input value={path} onChange={e => setPath(e.target.value)} onKeyDown={e => e.key === "Enter" && dev && list(path)} placeholder="/datadisk0/DATA0/startrun.dat" className="bg-sunken border-line h-9 font-mono" data-testid="ftp-dev-path" /></div>
          <div className="flex gap-1">
            <Button variant="outline" onClick={() => list(path)} disabled={!dev || busy} className="h-9 border-line bg-transparent" data-testid="ftp-dev-ls">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Folder className="w-4 h-4 mr-1" />}Listar</Button>
            <Button onClick={() => start({ direction: "pull", remote_path: path, local_path: dest })} disabled={!dev || !path.trim()} className="h-9 bg-brand hover:bg-brand-strong text-onbrand" data-testid="ftp-dev-pull"><Download className="w-4 h-4 mr-1" />Trazer</Button>
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-[16rem_1fr]"><span className="text-xs text-slate-500 self-center">Salvar na pasta (vazio = equipamentos/{d?.name || "<nome>"})</span>
          <Input value={dest} onChange={e => setDest(e.target.value)} className="bg-sunken border-line h-8 font-mono text-xs" data-testid="ftp-dev-dest" /></div>
        {lsErr && <div className="text-xs text-amber-300" data-testid="ftp-dev-lserr"><AlertTriangle className="w-3.5 h-3.5 inline mr-1" />{lsErr}</div>}
        {ls && (
          <div className="rounded border border-line divide-y divide-line" data-testid="ftp-dev-list">
            <button onClick={up} className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-slate-400 hover:bg-white/[0.03]"><ArrowUp className="w-4 h-4" /><span className="font-mono">{ls.path}</span></button>
            {ls.items.length === 0 && <div className="px-3 py-2 text-xs text-slate-500">Pasta vazia</div>}
            {ls.items.map(it => {
              const full = `${ls.path.replace(/\/+$/, "")}/${it.name}`;
              return (
                <div key={it.name} className="flex items-center gap-3 px-3 py-1.5 text-sm">
                  {it.dir ? <Folder className="w-4 h-4 text-brand-soft" /> : <FileText className="w-4 h-4 text-slate-500" />}
                  {it.dir ? <button onClick={() => list(full)} className="flex-1 text-left text-slate-100 hover:underline truncate">{it.name}</button> : <span className="flex-1 font-mono text-[13px] text-slate-100 truncate">{it.name}</span>}
                  <span className="text-xs text-slate-500 w-20 text-right">{it.dir ? "" : size(it.size)}</span>
                  {!it.dir && <Button size="sm" variant="ghost" onClick={() => start({ direction: "pull", remote_path: full, local_path: dest })} className="h-7 text-slate-300" data-testid={`ftp-dev-get-${it.name}`}><Download className="w-3.5 h-3.5 mr-1" />Trazer</Button>}
                </div>);
            })}
          </div>
        )}
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="text-sm font-semibold text-slate-200">Enviar arquivo do BastiON para o equipamento</div>
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] items-end">
          <div><label className={lbl}>Arquivo (aba Arquivos)</label>
            <select value={local} onChange={e => setLocal(e.target.value)} className={`${sel} w-full`} data-testid="ftp-dev-local">
              <option value="">— escolha —</option>{files.map(f => <option key={f.path} value={f.path}>{f.path} ({size(f.size)})</option>)}</select></div>
          <div><label className={lbl}>Destino no equipamento (termine com / para manter o nome)</label>
            <Input value={rpush} onChange={e => setRpush(e.target.value)} placeholder={ls?.path ? `${ls.path.replace(/\/+$/, "")}/` : "/datadisk0/"} className="bg-sunken border-line h-9 font-mono" data-testid="ftp-dev-rpush" /></div>
          <Button onClick={() => start({ direction: "push", local_path: local, remote_path: rpush || `${(ls?.path || "").replace(/\/+$/, "")}/` })} disabled={!dev || !local || !(rpush || ls?.path)} className="h-9 bg-brand hover:bg-brand-strong text-onbrand" data-testid="ftp-dev-push"><Upload className="w-4 h-4 mr-1" />Enviar</Button>
        </div>
      </Card>
      {jobs.length > 0 && (
        <Card className="bg-surface border-line divide-y divide-line" data-testid="ftp-dev-jobs">
          {jobs.map(j => {
            const pct = j.total ? Math.min(100, Math.round(100 * j.bytes / j.total)) : 0;
            return (
              <div key={j.id} className="px-4 py-2 text-sm space-y-1" data-testid={`ftp-job-${j.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  {j.state === "pronto" ? <CheckCircle2 className="w-4 h-4 text-emerald-300" /> : j.state === "erro" ? <XCircle className="w-4 h-4 text-red-300" /> : <Loader2 className="w-4 h-4 animate-spin text-slate-400" />}
                  <span className="text-slate-200">{j.direction === "pull" ? `${j.device_name} → BastiON` : `BastiON → ${j.device_name}`}</span>
                  <span className="font-mono text-xs text-slate-400 truncate">{j.direction === "pull" ? `${j.remote} → ${j.local}` : `${j.local} → ${j.remote}`}</span>
                  <span className="ml-auto text-xs text-slate-500">{j.state}{j.method ? ` · ${j.method.toUpperCase()}` : ""}{j.bytes ? ` · ${size(j.bytes)}` : ""}{j.secs ? ` · ${j.secs}s` : ""}</span>
                </div>
                {["copiando"].includes(j.state) && j.total > 0 && <div className="h-1.5 rounded bg-sunken overflow-hidden"><div className="h-full bg-brand" style={{ width: `${pct}%` }} /></div>}
                {j.error && <div className="text-xs text-red-300">{j.error}</div>}
              </div>);
          })}
        </Card>
      )}
    </div>
  );
}

function Events() {
  const [rows, setRows] = useState(null);
  const load = useCallback(() => api.get("/ftp/events").then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const L = { login: "login", upload: "enviou", download: "baixou", delete: "apagou" };
  return (
    <Card className="bg-surface border-line" data-testid="ftp-events">
      <div className="flex px-4 py-2 border-b border-line"><Button size="sm" variant="ghost" onClick={load} className="ml-auto text-slate-400"><RefreshCw className="w-4 h-4" /></Button></div>
      {rows && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nada ainda.</div>}
      <div className="overflow-x-auto"><table className="w-full text-sm"><tbody className="divide-y divide-line">{(rows || []).map(e => (
        <tr key={e.id}>
          <td className="px-4 py-1.5 text-xs text-slate-500 whitespace-nowrap">{fmt(e.at)}</td>
          <td className={`px-2 text-xs ${e.ok ? "text-slate-300" : "text-red-300"}`}>{L[e.action] || e.action}{e.ok ? "" : " (falhou)"}</td>
          <td className="px-2 font-mono text-xs text-slate-200">{e.path}</td>
          <td className="px-2 text-xs text-slate-400 whitespace-nowrap">{e.bytes ? size(e.bytes) : ""}{e.secs ? ` · ${e.secs}s` : ""}</td>
          <td className="px-2 text-xs text-slate-400">{e.access_name || e.user}</td>
          <td className="px-2 text-xs text-slate-500 whitespace-nowrap">{e.via}{e.peer ? ` ← ${e.peer}` : ""}</td>
          <td className="px-2 text-xs text-red-300">{e.error}</td>
        </tr>))}</tbody></table></div>
    </Card>
  );
}

function Settings({ agents, reloadSt }) {
  const [c, setC] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/ftp/settings").then(r => setC(r.data)).catch(e => toast.error(formatApiError(e))); }, []);
  if (!c) return null;
  const save = async () => {
    setBusy(true);
    try {
      const { root, public_host, ...body } = c;
      const r = await api.put("/ftp/settings", { ...body, local_port: Number(c.local_port), jump_port: Number(c.jump_port), jump_pasv_start: Number(c.jump_pasv_start), jump_pasv_count: Number(c.jump_pasv_count), max_mb: Number(c.max_mb) });
      setC({ ...c, ...r.data }); toast.success("Salvo — as portas reabrem em alguns segundos"); setTimeout(reloadSt, 3000);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const setAg = (id, ip) => setC({ ...c, agents: { ...c.agents, [id]: { ip } } });
  const pend = Number(c.jump_pasv_start) + Number(c.jump_pasv_count) - 1;
  return (
    <div className="space-y-4 max-w-4xl" data-testid="ftp-settings">
      <Card className="bg-surface border-line p-4 grid gap-3 sm:grid-cols-3 items-end">
        <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={c.enabled} onCheckedChange={v => setC({ ...c, enabled: v })} />FTP ligado</label>
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={c.local_enabled} onCheckedChange={v => setC({ ...c, local_enabled: v })} />Atender direto no BastiON</label>
        <div><label className={lbl}>Tamanho máximo de arquivo (MB)</label><Input type="number" value={c.max_mb} onChange={e => setC({ ...c, max_mb: e.target.value })} className="bg-sunken border-line h-9" /></div>
        <div><label className={lbl}>BastiON: porta</label><Input type="number" value={c.local_port} onChange={e => setC({ ...c, local_port: e.target.value })} className="bg-sunken border-line h-9" data-testid="ftp-local-port" /></div>
        <div><label className={lbl}>BastiON: portas passivas</label><Input value={c.local_pasv} onChange={e => setC({ ...c, local_pasv: e.target.value })} className="bg-sunken border-line h-9 font-mono" /></div>
        <div><label className={lbl}>BastiON: IP anunciado no passivo</label><Input value={c.local_ip} onChange={e => setC({ ...c, local_ip: e.target.value })} placeholder={c.public_host || "vazio = o IP em que o equipamento conectou"} className="bg-sunken border-line h-9 font-mono" /></div>
        <div><label className={lbl}>Jumps: porta</label><Input type="number" value={c.jump_port} onChange={e => setC({ ...c, jump_port: e.target.value })} className="bg-sunken border-line h-9" data-testid="ftp-jump-port" /></div>
        <div><label className={lbl}>Jumps: 1ª porta passiva</label><Input type="number" value={c.jump_pasv_start} onChange={e => setC({ ...c, jump_pasv_start: e.target.value })} className="bg-sunken border-line h-9" /></div>
        <div><label className={lbl}>Jumps: quantas passivas</label><Input type="number" value={c.jump_pasv_count} onChange={e => setC({ ...c, jump_pasv_count: e.target.value })} className="bg-sunken border-line h-9" /></div>
        <p className="sm:col-span-3 text-[11px] text-slate-500">Firewall: no servidor, <span className="font-mono">ufw allow from REDE to any port {c.local_port} proto tcp</span> e a faixa {c.local_pasv}. Nos jumps, as portas {c.jump_port} e {c.jump_pasv_start}-{pend} precisam aceitar conexão da rede dos equipamentos. Arquivos ficam em <span className="font-mono">{c.root}</span>.</p>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="text-sm font-semibold text-slate-200">IP de cada jump visto pelos equipamentos</div>
        <p className="text-xs text-slate-500">Vazio = o primeiro IP do jump (hostname -I). Ajuste quando o jump tiver várias redes.</p>
        {agents.map(a => (
          <div key={a.id} className="flex items-center gap-3"><span className="text-sm text-slate-300 w-48 truncate">{a.name}</span>
            <Input value={c.agents?.[a.id]?.ip || ""} onChange={e => setAg(a.id, e.target.value)} placeholder="automático" className="bg-sunken border-line h-8 w-48 font-mono" data-testid={`ftp-agip-${a.id}`} /></div>))}
      </Card>
      <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="ftp-settings-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
    </div>
  );
}

export default function Ftp() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  const [st, setSt] = useState(null);
  const [agents, setAgents] = useState([]);
  const reloadSt = useCallback(() => api.get("/ftp/status").then(r => setSt(r.data)).catch(() => {}), []);
  useEffect(() => { reloadSt(); api.get("/agents").then(r => setAgents(r.data)).catch(() => {}); const i = setInterval(reloadSt, 15000); return () => clearInterval(i); }, [reloadSt]);
  const tabs = useMemo(() => [["", "Acessos (FTP)"], ["equipamento", "Direto no equipamento"], ["arquivos", "Arquivos"], ["eventos", "Transferências"], ...(admin ? [["ajustes", "Ajustes"]] : [])], [admin]);
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="ftp-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><FolderUp className="w-6 h-6 text-brand-soft" /> Arquivos (FTP)</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">Firmware e backups de configuração entre os equipamentos e o BastiON: o equipamento busca por FTP (direto ou pela porta que o BastiON abre no jump) ou o BastiON entra no equipamento e copia por SFTP/SCP.</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
          {tabs.map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`ftp-tab-${k || "acc"}`}>{l}</button>)}
        </div>
      </div>
      <div className="space-y-3">
        {(tab === "" || tab === "arquivos") && <StatusBar st={st} agents={agents} />}
        {tab === "equipamento" ? <DeviceXfer /> : tab === "arquivos" ? <Files /> : tab === "eventos" ? <Events /> : tab === "ajustes" && admin ? <Settings agents={agents} reloadSt={reloadSt} /> : <Accesses st={st} agents={agents} reloadSt={reloadSt} />}
      </div>
    </div>
  );
}
