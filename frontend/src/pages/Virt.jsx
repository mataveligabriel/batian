import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Boxes, Plus, RefreshCw, Loader2, Pencil, Trash2, Play, Power, PowerOff, RotateCw, Pause, Camera,
  Globe, Server, HardDrive, AlertTriangle, CheckCircle2, XCircle, Search, Undo2, Bell, BellOff, Zap,
} from "lucide-react";

const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const KIND = { proxmox: "Proxmox VE", vmware: "VMware ESXi / vCenter" };
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const bytes = (b) => (!b ? "—" : b >= 1e12 ? `${(b / 1e12).toFixed(1)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${b} B`);
const pct = (a, b) => (b ? Math.round((1000 * a) / b) / 10 : 0);
const up = (s) => { if (!s) return "—"; const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600); return d ? `${d}d ${h}h` : h ? `${h}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m`; };
const STATE = {
  running: ["bg-emerald-400", "text-emerald-300", "ligada"], stopped: ["bg-slate-500", "text-slate-400", "desligada"],
  suspended: ["bg-amber-400", "text-amber-300", "suspensa"], paused: ["bg-amber-400", "text-amber-300", "pausada"],
};
const ACTIONS = [
  ["start", "Ligar", Play, false], ["shutdown", "Desligar (sistema)", Power, true], ["reboot", "Reiniciar (sistema)", RotateCw, true],
  ["suspend", "Suspender", Pause, true], ["stop", "Desligar à força", PowerOff, true], ["reset", "Reset à força", Zap, true],
];

function Bar({ v, warn = 80, crit = 90 }) {
  const c = v >= crit ? "bg-red-500" : v >= warn ? "bg-amber-400" : "bg-brand";
  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="flex-1 h-1.5 rounded bg-slate-800 overflow-hidden"><div className={`h-full ${c}`} style={{ width: `${Math.min(100, v || 0)}%` }} /></div>
      <span className="text-[11px] font-mono text-slate-400 w-10 text-right">{(v || 0).toFixed(0)}%</span>
    </div>
  );
}

function Spark({ data, color = "text-brand-soft" }) {
  const v = (data || []).slice(-120);
  if (v.length < 2) return <svg width="140" height="28" />;
  const w = 140, h = 28, step = w / (v.length - 1);
  const d = v.map((x, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(h - (Math.min(100, x || 0) / 100) * (h - 2) - 1).toFixed(1)}`).join(" ");
  return <svg width={w} height={h} className="block"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.3" className={color} /></svg>;
}

function Confirm({ ask, onClose }) {
  const [busy, setBusy] = useState(false);
  if (!ask) return null;
  const go = async () => { setBusy(true); try { await ask.run(); onClose(); } finally { setBusy(false); } };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-md" data-testid="virt-confirm">
        <DialogHeader><DialogTitle>{ask.title}</DialogTitle></DialogHeader>
        <p className="text-sm text-slate-300 whitespace-pre-line">{ask.text}</p>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={go} disabled={busy} data-testid="virt-confirm-ok" className={ask.danger ? "bg-red-600 hover:bg-red-700" : "bg-brand hover:bg-brand-strong"}>
            {busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}{ask.ok || "Confirmar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ cadastro
const EMPTY = { name: "", kind: "proxmox", host: "", port: "", auth: "token", user: "", password: "", token_id: "", secret: "", verify_tls: false,
  agent_id: "", tags: [], thresholds: {}, enabled: true };

function HostDialog({ open, cur, agents, onClose, onSaved }) {
  const [f, setF] = useState(EMPTY);
  const [busy, setBusy] = useState("");
  const [test, setTest] = useState(null);
  useEffect(() => {
    if (!open) return;
    setTest(null);
    setF(cur ? { ...EMPTY, ...cur, port: cur.port || "", agent_id: cur.agent_id || "", password: "", secret: "", thresholds: cur.thresholds || {} } : EMPTY);
  }, [open, cur]);
  const s = (k) => (e) => setF((p) => ({ ...p, [k]: e?.target ? e.target.value : e }));
  const body = () => ({ ...f, port: f.port ? Number(f.port) : null, agent_id: f.agent_id || null,
    tags: typeof f.tags === "string" ? f.tags.split(",").map((x) => x.trim()).filter(Boolean) : f.tags,
    thresholds: Object.fromEntries(Object.entries(f.thresholds || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => [k, Number(v)])) });
  const doTest = async () => {
    setBusy("test"); setTest(null);
    try { const r = await api.post(`/virt/test${cur ? `?hid=${cur.id}` : ""}`, body()); setTest({ ok: true, ...r.data }); }
    catch (e) { setTest({ ok: false, error: formatApiError(e) }); } finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("save");
    try {
      const r = cur ? await api.put(`/virt/hypervisors/${cur.id}`, body()) : await api.post("/virt/hypervisors", body());
      toast.success("Salvo — a primeira coleta leva alguns segundos"); onSaved(r.data); onClose();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const pve = f.kind === "proxmox";
  const token = pve && f.auth === "token";
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl max-h-[90vh] overflow-y-auto" data-testid="virt-host-dialog">
        <DialogHeader><DialogTitle>{cur ? "Editar hypervisor" : "Adicionar hypervisor"}</DialogTitle></DialogHeader>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs font-mono">
          {Object.entries(KIND).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setF((p) => ({ ...p, kind: k, port: "", auth: k === "vmware" ? "password" : "token" }))} data-testid={`virt-kind-${k}`}
              className={`flex-1 py-1.5 rounded ${f.kind === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
          ))}
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Nome</label><Input value={f.name} onChange={s("name")} placeholder={pve ? "Cluster PVE POP-Centro" : "ESXi 01 DC"} className="bg-sunken border-line" data-testid="virt-name" /></div>
          <div className="grid grid-cols-[1fr_90px] gap-2">
            <div><label className={lbl}>Endereço {pve ? "(qualquer nó do cluster)" : "(ESXi ou vCenter)"}</label>
              <Input value={f.host} onChange={s("host")} placeholder={pve ? "192.0.2.10" : "vcenter.exemplo.net"} className="bg-sunken border-line font-mono" data-testid="virt-host" /></div>
            <div><label className={lbl}>Porta</label><Input value={f.port} onChange={s("port")} placeholder={pve ? "8006" : "443"} className="bg-sunken border-line font-mono" /></div>
          </div>
          <div><label className={lbl}>Alcançar por</label>
            <select value={f.agent_id} onChange={s("agent_id")} className={`${sel} w-full`} data-testid="virt-agent">
              <option value="">Direto do servidor do BastiON</option>
              {agents.map((a) => <option key={a.id} value={a.id}>Jump: {a.name}</option>)}
            </select></div>
          {pve && (
            <div><label className={lbl}>Autenticação</label>
              <select value={f.auth} onChange={s("auth")} className={`${sel} w-full`} data-testid="virt-auth">
                <option value="token">Token de API (Proxmox 6.2 ou mais novo)</option>
                <option value="password">Usuário e senha (qualquer versão)</option>
              </select></div>
          )}
          {token ? (
            <>
              <div><label className={lbl}>ID do token</label><Input value={f.token_id} onChange={s("token_id")} placeholder="monitor@pve!bastion" className="bg-sunken border-line font-mono" data-testid="virt-token-id" /></div>
              <div><label className={lbl}>Segredo do token {cur?.has_secret && <span className="text-slate-500">(vazio = mantém)</span>}</label>
                <Input type="password" value={f.secret} onChange={s("secret")} className="bg-sunken border-line font-mono" data-testid="virt-secret" /></div>
            </>
          ) : (
            <>
              <div><label className={lbl}>Usuário</label><Input value={f.user} onChange={s("user")} placeholder={pve ? "root@pam" : "root  ou  monitor@vsphere.local"} className="bg-sunken border-line font-mono" data-testid="virt-user" /></div>
              <div><label className={lbl}>Senha {cur?.has_password && <span className="text-slate-500">(vazio = mantém)</span>}</label>
                <Input type="password" value={f.password} onChange={s("password")} className="bg-sunken border-line font-mono" data-testid="virt-password" /></div>
            </>
          )}
          <div><label className={lbl}>Tags (separadas por vírgula)</label>
            <Input value={Array.isArray(f.tags) ? f.tags.join(", ") : f.tags} onChange={s("tags")} placeholder="datacenter, produção" className="bg-sunken border-line" /></div>
          <div className="grid grid-cols-3 gap-2">
            {[["cpu", "CPU %", 90], ["mem", "Memória %", 95], ["storage", "Storage %", 90]].map(([k, l, d]) => (
              <div key={k}><label className={lbl}>Alarme {l}</label>
                <Input value={f.thresholds?.[k] ?? ""} placeholder={String(d)} onChange={(e) => setF((p) => ({ ...p, thresholds: { ...p.thresholds, [k]: e.target.value } }))} className="bg-sunken border-line font-mono" /></div>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-5 text-sm text-slate-300">
          <label className="flex items-center gap-2"><Switch checked={!!f.verify_tls} onCheckedChange={s("verify_tls")} /> Conferir certificado TLS {f.agent_id && <span className="text-xs text-slate-500">(só vale direto)</span>}</label>
          <label className="flex items-center gap-2"><Switch checked={!!f.enabled} onCheckedChange={s("enabled")} /> Monitorar</label>
        </div>
        <div className="text-xs text-slate-400 bg-sunken border border-line rounded-md p-3 space-y-1">
          {pve ? (<>
            <div className="text-slate-300">Usuário só para o BastiON (no shell de um nó):</div>
            <pre className="font-mono text-[11px] text-slate-300 whitespace-pre-wrap">{`pveum user add monitor@pve
pveum acl modify / --users monitor@pve --roles PVEAuditor        # só ver
pveum acl modify / --users monitor@pve --roles PVEVMAdmin        # ver + ligar/desligar/snapshot
pveum user token add monitor@pve bastion --privsep 0             # mostra o segredo uma vez`}</pre>
            <div>Proxmox 4.x a 6.1 não têm token: use usuário e senha (realm pam ou pve).</div>
          </>) : (<>
            <div>Funciona com ESXi 5.5 a 8.x direto ou pelo vCenter (cadastre o vCenter para ver todos os hosts de uma vez).</div>
            <div>Para só monitorar, um usuário com papel <b>Somente leitura</b> basta. ESXi com licença gratuita deixa a API só em leitura: ligar/desligar e snapshot dão erro — nesse caso use o vCenter ou a interface web.</div>
          </>)}
        </div>
        {test && (
          <div className={`text-sm rounded-md border p-2 ${test.ok ? "border-emerald-700 text-emerald-300" : "border-red-800 text-red-300"}`} data-testid="virt-test-result">
            {test.ok ? `Conectou: ${test.product} · ${test.hosts} host(s), ${test.vms} VM(s), ${test.storages} storage(s)` : test.error}
          </div>
        )}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={doTest} disabled={!!busy} className="border-line text-slate-200" data-testid="virt-test">
            {busy === "test" ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <CheckCircle2 className="w-4 h-4 mr-1" />}Testar</Button>
          <Button onClick={save} disabled={!!busy} className="bg-brand hover:bg-brand-strong" data-testid="virt-save">
            {busy === "save" && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ snapshots
function SnapDialog({ hv, vm, readOnly, onClose, setAsk }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState({ name: "", description: "", memory: false });
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    setErr("");
    api.get(`/virt/hypervisors/${hv.id}/snapshots`, { params: { vm_id: vm.id } }).then((r) => setList(r.data)).catch((e) => { setList([]); setErr(formatApiError(e)); });
  }, [hv.id, vm.id]);
  useEffect(() => { load(); }, [load]);
  const op = async (body) => { await api.post(`/virt/hypervisors/${hv.id}/snapshots`, { vm_id: vm.id, ...body }); load(); };
  const create = async () => {
    setBusy(true);
    try { await op({ op: "create", ...f }); toast.success("Snapshot criado"); setF({ name: "", description: "", memory: false }); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const ask = (s, kind) => setAsk({
    title: kind === "revert" ? "Voltar para o snapshot?" : "Apagar o snapshot?", danger: true, ok: kind === "revert" ? "Voltar" : "Apagar",
    text: kind === "revert" ? `A VM ${vm.name} volta ao estado de "${s.name}". Tudo o que mudou depois dele se perde.` : `Apagar "${s.name}" de ${vm.name}? (os dados são consolidados no disco)`,
    run: async () => { try { await op({ op: kind, snap: s.id }); toast.success("Feito"); } catch (e) { toast.error(formatApiError(e)); } },
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-xl" data-testid="virt-snap-dialog">
        <DialogHeader><DialogTitle>Snapshots · {vm.name}</DialogTitle></DialogHeader>
        {err && <div className="text-sm text-red-300">{err}</div>}
        {!list ? <Loader2 className="w-5 h-5 animate-spin text-slate-500" /> : (
          <div className="border border-line rounded-md divide-y divide-line max-h-64 overflow-y-auto">
            {!list.length && <div className="p-3 text-xs text-slate-500">Nenhum snapshot</div>}
            {list.map((s) => (
              <div key={s.id} className="px-3 py-2 flex items-center gap-2 text-sm" data-testid={`virt-snap-${s.name}`}>
                <Camera className="w-4 h-4 text-slate-500 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="text-slate-200 truncate">{s.name} {s.memory && <span className="text-[10px] text-sky-300 ml-1">com memória</span>}</div>
                  <div className="text-[11px] text-slate-500 truncate">{fmt(s.created)}{s.parent ? ` · depois de ${s.parent}` : ""}{s.description ? ` · ${s.description}` : ""}</div>
                </div>
                {!readOnly && <>
                  <Button size="sm" variant="ghost" onClick={() => ask(s, "revert")} className="h-7 text-slate-300" title="Voltar para este"><Undo2 className="w-4 h-4" /></Button>
                  <Button size="sm" variant="ghost" onClick={() => ask(s, "delete")} className="h-7 text-red-300" title="Apagar"><Trash2 className="w-4 h-4" /></Button>
                </>}
              </div>
            ))}
          </div>
        )}
        {!readOnly && (
          <div className="grid grid-cols-[1fr_1fr_auto] gap-2 items-end">
            <div><label className={lbl}>Nome</label><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="antes_update" className="bg-sunken border-line" data-testid="virt-snap-name" /></div>
            <div><label className={lbl}>Descrição</label><Input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} className="bg-sunken border-line" /></div>
            <Button onClick={create} disabled={busy || !f.name.trim()} className="bg-brand hover:bg-brand-strong" data-testid="virt-snap-create">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}</Button>
            <label className="col-span-3 flex items-center gap-2 text-xs text-slate-400">
              <Switch checked={f.memory} onCheckedChange={(v) => setF({ ...f, memory: v })} /> Incluir a memória (VM ligada volta exatamente como estava; demora mais)</label>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ detalhe
function Detail({ hv, readOnly, onEdit, reloadList, setAsk }) {
  const nav = useNavigate();
  const [d, setD] = useState(null);
  const [hist, setHist] = useState([]);
  const [tab, setTab] = useState("vms");
  const [q, setQ] = useState("");
  const [only, setOnly] = useState("");
  const [snap, setSnap] = useState(null);
  const [busy, setBusy] = useState({});
  const [refreshing, setRefreshing] = useState(false);
  const load = useCallback(() => {
    api.get(`/virt/hypervisors/${hv.id}`).then((r) => setD(r.data)).catch((e) => toast.error(formatApiError(e)));
    api.get(`/virt/hypervisors/${hv.id}/history`, { params: { hours: 24 } }).then((r) => setHist(r.data)).catch(() => {});
  }, [hv.id]);
  useEffect(() => { setD(null); load(); const i = setInterval(load, 30000); return () => clearInterval(i); }, [load]);
  const st = d?.status || {};
  const inv = st.inv || { hosts: [], vms: [], storages: [] };
  const watched = new Set(d?.watched || []);
  const vms = useMemo(() => inv.vms.filter((v) => (!only || (only === "watched" ? watched.has(v.id) : only === "templates" ? v.template : v.state === only && !v.template))
    && (!q || `${v.name} ${v.ip} ${v.host} ${v.vmid} ${v.tags}`.toLowerCase().includes(q.toLowerCase())))
    .sort((a, b) => (a.template - b.template) || a.name.localeCompare(b.name)), [inv.vms, q, only, d?.watched]); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = async () => {
    setRefreshing(true);
    try { await api.post(`/virt/hypervisors/${hv.id}/refresh`); load(); reloadList(); } catch (e) { toast.error(formatApiError(e)); } finally { setRefreshing(false); }
  };
  const act = (vm, a) => {
    const [, label, , confirm] = ACTIONS.find((x) => x[0] === a);
    const run = async () => {
      setBusy((b) => ({ ...b, [vm.id]: a }));
      try { await api.post(`/virt/hypervisors/${hv.id}/action`, { vm_id: vm.id, action: a }); toast.success(`${label}: ${vm.name} — comando enviado`); setTimeout(load, 5000); setTimeout(load, 16000); }
      catch (e) { toast.error(formatApiError(e)); } finally { setBusy((b) => ({ ...b, [vm.id]: null })); }
    };
    if (!confirm) return run();
    setAsk({ title: `${label}?`, text: `${vm.name} em ${vm.host}${["stop", "reset"].includes(a) ? "\nÉ como tirar da tomada: o sistema não fecha os arquivos." : ""}`, danger: ["stop", "reset"].includes(a), ok: label, run });
  };
  const watch = async (vm) => {
    try { await api.post(`/virt/hypervisors/${hv.id}/watch`, { vm_id: vm.id, watch: !watched.has(vm.id) }); load(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const openWeb = async () => {
    try {
      await api.post("/web/sessions", { url: `https://${hv.host}:${hv.port}/`, agent_id: hv.agent_id || null, name: hv.name });
      nav("/web");
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const sm = st.summary;
  return (
    <div className="space-y-3" data-testid="virt-detail">
      <Card className="bg-surface border-line p-3 flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="min-w-0">
          <div className="text-lg text-slate-100 font-semibold flex items-center gap-2">{hv.name}
            {st.ok === false ? <XCircle className="w-4 h-4 text-red-400" /> : st.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}</div>
          <div className="text-xs text-slate-500 font-mono">{st.product || KIND[hv.kind]} · {hv.host}:{hv.port}{hv.agent_name ? ` · via ${hv.agent_name}` : ""} · coletado {fmt(st.at)}</div>
          {st.ok === false && <div className="text-xs text-red-300 mt-1">{st.error}</div>}
        </div>
        {sm && <div className="flex gap-5 text-xs">
          <div><div className="text-slate-500">CPU</div><Spark data={hist.map((x) => x.cpu)} /></div>
          <div><div className="text-slate-500">Memória</div><Spark data={hist.map((x) => x.mem)} color="text-sky-300" /></div>
        </div>}
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="outline" onClick={openWeb} className="border-line text-slate-200" data-testid="virt-open-web"><Globe className="w-4 h-4 mr-1" />Interface web</Button>
          <Button size="sm" variant="outline" onClick={refresh} disabled={refreshing} className="border-line text-slate-200" data-testid="virt-refresh">
            {refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}</Button>
          {!readOnly && <Button size="sm" variant="outline" onClick={onEdit} className="border-line text-slate-200"><Pencil className="w-4 h-4" /></Button>}
        </div>
      </Card>

      <div className="flex gap-1 border-b border-line text-sm">
        {[["vms", `VMs e containers (${inv.vms.length})`], ["hosts", `Hosts (${inv.hosts.length})`], ["storage", `Storage (${inv.storages.length})`], ["events", "Ações"]].map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} data-testid={`virt-tab-${k}`}
            className={`px-3 py-2 -mb-px border-b-2 ${tab === k ? "border-brand text-brand-soft" : "border-transparent text-slate-400 hover:text-slate-200"}`}>{l}</button>
        ))}
      </div>

      {tab === "vms" && (
        <Card className="bg-surface border-line overflow-hidden">
          <div className="p-2 flex flex-wrap gap-2 border-b border-line">
            <div className="relative"><Search className="w-4 h-4 absolute left-2 top-2.5 text-slate-500" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="nome, IP, host, ID…" className="bg-sunken border-line pl-8 h-9 w-56" data-testid="virt-search" /></div>
            <select value={only} onChange={(e) => setOnly(e.target.value)} className={sel} data-testid="virt-filter">
              <option value="">Todas</option><option value="running">Ligadas</option><option value="stopped">Desligadas</option>
              <option value="suspended">Suspensas</option><option value="watched">Monitoradas</option><option value="templates">Templates</option>
            </select>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-slate-500 bg-sunken"><tr>
                <th className="text-left px-3 py-2">Nome</th><th className="text-left px-2">Host</th><th className="text-left px-2">Estado</th>
                <th className="text-left px-2 w-32">CPU</th><th className="text-left px-2">Memória</th><th className="text-left px-2">Disco</th>
                <th className="text-left px-2">IP</th><th className="text-left px-2">Ligada há</th><th className="text-right px-3">Ações</th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {vms.map((v) => {
                  const [dot, tc, sl] = STATE[v.state] || ["bg-slate-600", "text-slate-400", v.state];
                  return (
                    <tr key={v.id} className="hover:bg-slate-800/30" data-testid={`virt-vm-${v.vmid}`}>
                      <td className="px-3 py-1.5"><div className="text-slate-200 flex items-center gap-1.5">{v.name}
                        {v.template && <span className="text-[10px] px-1 rounded bg-slate-700 text-slate-300">template</span>}
                        {v.type === "lxc" && <span className="text-[10px] px-1 rounded bg-indigo-900/60 text-indigo-200">CT</span>}</div>
                        <div className="text-[11px] text-slate-500 font-mono">{hv.kind === "proxmox" ? `ID ${v.vmid}` : v.id} · {v.cpus} vCPU{v.tags ? ` · ${v.tags}` : ""}</div></td>
                      <td className="px-2 text-xs text-slate-400">{v.host}</td>
                      <td className="px-2"><span className={`flex items-center gap-1.5 text-xs ${tc}`}><span className={`w-2 h-2 rounded-full ${dot}`} />{sl}</span></td>
                      <td className="px-2">{v.state === "running" ? <Bar v={v.cpu_pct} /> : <span className="text-slate-600">—</span>}</td>
                      <td className="px-2 text-xs font-mono text-slate-400">{v.state === "running" && v.mem_used ? `${bytes(v.mem_used)} / ` : ""}{bytes(v.mem_total)}</td>
                      <td className="px-2 text-xs font-mono text-slate-400">{bytes(v.disk_total)}</td>
                      <td className="px-2 text-xs font-mono text-slate-300">{v.ip || "—"}</td>
                      <td className="px-2 text-xs text-slate-400">{v.state === "running" ? up(v.uptime) : "—"}</td>
                      <td className="px-3 text-right whitespace-nowrap">
                        {!readOnly && !v.template && (busy[v.id] ? <Loader2 className="w-4 h-4 animate-spin inline text-slate-400" /> :
                          ACTIONS.filter(([a]) => (v.state === "running" ? a !== "start" : v.state === "suspended" || v.state === "paused" ? a === "start" || a === "stop" : a === "start"))
                            .filter(([a]) => !(v.type === "lxc" && (a === "reset")))
                            .map(([a, l, I]) => (
                              <Button key={a} size="sm" variant="ghost" onClick={() => act(v, a)} title={l} data-testid={`virt-act-${v.vmid}-${a}`}
                                className={`h-7 w-7 p-0 ${a === "start" ? "text-emerald-300" : ["stop", "reset"].includes(a) ? "text-red-300" : "text-slate-300"}`}><I className="w-4 h-4" /></Button>
                            )))}
                        {!v.template && <Button size="sm" variant="ghost" onClick={() => setSnap(v)} title="Snapshots" className="h-7 w-7 p-0 text-slate-300" data-testid={`virt-snap-${v.vmid}`}><Camera className="w-4 h-4" /></Button>}
                        {!readOnly && !v.template && <Button size="sm" variant="ghost" onClick={() => watch(v)} data-testid={`virt-watch-${v.vmid}`}
                          title={watched.has(v.id) ? "Monitorada: abre incidente se parar. Clique para deixar de monitorar" : "Abrir incidente se esta VM parar"}
                          className={`h-7 w-7 p-0 ${watched.has(v.id) ? "text-amber-300" : "text-slate-500"}`}>{watched.has(v.id) ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}</Button>}
                      </td>
                    </tr>
                  );
                })}
                {!vms.length && <tr><td colSpan={9} className="p-4 text-center text-xs text-slate-500">{st.ok === false ? "Sem dados: o hypervisor não respondeu" : d ? "Nenhuma VM" : "Carregando…"}</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {tab === "hosts" && (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {inv.hosts.map((h) => (
            <Card key={h.id} className="bg-surface border-line p-3 space-y-2" data-testid={`virt-hostcard-${h.name}`}>
              <div className="flex items-center gap-2"><Server className="w-4 h-4 text-slate-400" /><span className="text-slate-100 font-medium">{h.name}</span>
                <span className={`ml-auto text-xs ${h.state === "online" ? "text-emerald-300" : "text-red-300"}`}>{h.state === "online" ? (h.maintenance ? "manutenção" : "online") : "fora do ar"}</span></div>
              <div className="text-[11px] text-slate-500">{[h.model, h.version && `versão ${h.version}`, h.cpus && `${h.cpus} threads`, h.uptime && `ligado há ${up(h.uptime)}`].filter(Boolean).join(" · ")}</div>
              {h.state === "online" && <>
                <div className="text-xs text-slate-400">CPU<Bar v={h.cpu_pct} /></div>
                <div className="text-xs text-slate-400">Memória {bytes(h.mem_used)} / {bytes(h.mem_total)}<Bar v={pct(h.mem_used, h.mem_total)} warn={85} crit={95} /></div>
                <Spark data={hist.map((x) => x.hosts?.[h.id.replace(/\./g, "_")]?.[0])} />
              </>}
            </Card>
          ))}
        </div>
      )}

      {tab === "storage" && (
        <Card className="bg-surface border-line divide-y divide-line">
          {inv.storages.map((s) => (
            <div key={s.id} className="px-3 py-2 grid grid-cols-[1fr_220px] gap-3 items-center text-sm">
              <div className="min-w-0"><div className="text-slate-200 flex items-center gap-2"><HardDrive className="w-4 h-4 text-slate-500" />{s.name}
                {!s.active && <span className="text-xs text-red-300">indisponível</span>}{s.shared && <span className="text-[10px] text-sky-300">compartilhado</span>}</div>
                <div className="text-[11px] text-slate-500 font-mono">{[s.type, s.host].filter(Boolean).join(" · ")} · {bytes(s.used)} de {bytes(s.total)}</div></div>
              <Bar v={pct(s.used, s.total)} warn={80} crit={90} />
            </div>
          ))}
          {!inv.storages.length && <div className="p-4 text-xs text-slate-500">Nenhum storage</div>}
        </Card>
      )}

      {tab === "events" && (
        <Card className="bg-surface border-line divide-y divide-line">
          {(d?.events || []).map((e) => (
            <div key={e.id} className="px-3 py-1.5 text-sm flex gap-3">
              <span className="text-xs text-slate-500 w-24 shrink-0">{fmt(e.at)}</span>
              {e.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" /> : <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />}
              <span className="text-slate-200">{e.action}</span><span className="text-slate-400 truncate">{e.target}</span>
              <span className="text-xs text-slate-500 ml-auto shrink-0">{e.user}</span>
              {e.error && <span className="text-xs text-red-300 truncate max-w-[40%]">{e.error}</span>}
            </div>
          ))}
          {!(d?.events || []).length && <div className="p-4 text-xs text-slate-500">Nenhuma ação feita pelo BastiON ainda</div>}
        </Card>
      )}
      {snap && <SnapDialog hv={hv} vm={snap} readOnly={readOnly} onClose={() => setSnap(null)} setAsk={setAsk} />}
    </div>
  );
}

// ------------------------------------------------------------------ página
export default function Virt() {
  const { user } = useAuth();
  const readOnly = user?.role === "viewer";
  const [sp, setSp] = useSearchParams();
  const [list, setList] = useState(null);
  const [agents, setAgents] = useState([]);
  const [dlg, setDlg] = useState(null);           // {cur} ao editar, {} ao criar
  const [ask, setAsk] = useState(null);
  const selId = sp.get("hv");
  const load = useCallback(() => api.get("/virt/hypervisors").then((r) => setList(r.data)).catch((e) => { setList([]); toast.error(formatApiError(e)); }), []);
  useEffect(() => {
    load(); api.get("/agents").then((r) => setAgents(r.data)).catch(() => {});
    const i = setInterval(load, 30000); return () => clearInterval(i);
  }, [load]);
  const cur = (list || []).find((h) => h.id === selId) || (list || [])[0];
  const pick = (h) => setSp({ hv: h.id }, { replace: true });
  const del = (h) => setAsk({ title: "Remover hypervisor?", danger: true, ok: "Remover", text: `${h.name} sai do BastiON (nada muda no ${KIND[h.kind]}). Histórico e alarmes dele são apagados.`,
    run: async () => { try { await api.delete(`/virt/hypervisors/${h.id}`); toast.success("Removido"); setSp({}, { replace: true }); load(); } catch (e) { toast.error(formatApiError(e)); } } });

  return (
    <div className="p-4 md:p-6 space-y-4" data-testid="virt-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Boxes className="w-6 h-6 text-brand-soft" />Virtualização</h1>
          <p className="text-sm text-slate-400">Proxmox VE e VMware ESXi / vCenter: hosts, VMs, storage, alarmes e ações — direto ou pelos jumps.</p>
        </div>
        {!readOnly && <Button onClick={() => setDlg({})} className="bg-brand hover:bg-brand-strong" data-testid="virt-add"><Plus className="w-4 h-4 mr-1" />Adicionar</Button>}
      </div>

      {list === null ? <Loader2 className="w-6 h-6 animate-spin text-slate-500" /> : !list.length ? (
        <Card className="bg-surface border-line p-6 text-center text-slate-400 text-sm">Nenhum hypervisor cadastrado. Adicione um Proxmox (nó ou cluster) ou um ESXi / vCenter.</Card>
      ) : (
        <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {list.map((h) => {
            const s = h.status || {}, sm = s.summary;
            const on = cur?.id === h.id;
            return (
              <Card key={h.id} onClick={() => pick(h)} data-testid={`virt-card-${h.id}`}
                className={`p-3 cursor-pointer border ${on ? "border-brand bg-brand/5" : "border-line bg-surface hover:border-slate-600"} ${h.enabled === false ? "opacity-60" : ""}`}>
                <div className="flex items-center gap-2">
                  <span className={`w-2.5 h-2.5 rounded-full ${s.ok ? (s.alarms?.length ? "bg-amber-400" : "bg-emerald-400") : s.ok === false ? "bg-red-500" : "bg-slate-600"}`} />
                  <span className="text-slate-100 font-medium truncate">{h.name}</span>
                  <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">{h.kind === "proxmox" ? "Proxmox" : "VMware"}</span>
                  {!readOnly && <button onClick={(e) => { e.stopPropagation(); del(h); }} className="text-slate-600 hover:text-red-300" title="Remover"><Trash2 className="w-3.5 h-3.5" /></button>}
                </div>
                <div className="text-[11px] text-slate-500 font-mono truncate mt-0.5">{s.product || h.host}{h.agent_name ? ` · via ${h.agent_name}` : ""}</div>
                {sm ? (
                  <div className="mt-2 space-y-1 text-xs text-slate-400">
                    <div className="flex justify-between"><span>Hosts {sm.hosts_up}/{sm.hosts}</span><span>VMs ligadas {sm.vms_running}/{sm.vms}</span></div>
                    <div className="grid grid-cols-[52px_1fr] items-center">CPU<Bar v={sm.cpu_pct} /></div>
                    <div className="grid grid-cols-[52px_1fr] items-center">Mem<Bar v={pct(sm.mem_used, sm.mem_total)} warn={85} crit={95} /></div>
                    <div className="grid grid-cols-[52px_1fr] items-center">Storage<Bar v={pct(sm.storage_used, sm.storage_total)} /></div>
                  </div>
                ) : <div className="mt-2 text-xs text-slate-500">{s.ok === false ? "" : "aguardando a primeira coleta…"}</div>}
                {s.ok === false && <div className="mt-1 text-xs text-red-300 line-clamp-2">{s.error}</div>}
                {s.alarms?.length > 0 && <div className="mt-1 text-xs text-amber-300 flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" />{s.alarms.length} alarme(s) — veja em Incidentes</div>}
              </Card>
            );
          })}
        </div>
      )}

      {cur && <Detail key={cur.id} hv={cur} readOnly={readOnly} onEdit={() => setDlg({ cur })} reloadList={load} setAsk={setAsk} />}
      <HostDialog open={!!dlg} cur={dlg?.cur} agents={agents} onClose={() => setDlg(null)} onSaved={(h) => { load(); pick(h); }} />
      <Confirm ask={ask} onClose={() => setAsk(null)} />
    </div>
  );
}
