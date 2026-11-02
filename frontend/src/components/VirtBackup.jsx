import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { RestoreBrowser } from "@/components/VirtTransfer";
import { HardDrive, Plus, Pencil, Trash2, Play, Square, Loader2, CheckCircle2, XCircle, AlertTriangle, Clock, ChevronRight, KeyRound } from "lucide-react";

// Backup das VMs (Proxmox vzdump / ESXi por SSH) para o NAS por SFTP, FTP ou FTPS.
const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];        // weekday() do Python: 0 = segunda
const KINDS = { sftp: "SFTP", ftp: "FTP", ftps: "FTPS (FTP com TLS)" };
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const bytes = (b) => (!b ? "0" : b >= 1e12 ? `${(b / 1e12).toFixed(2)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${Math.round(b / 1e3)} kB`);
const dur = (s) => (!s ? "—" : s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : s >= 60 ? `${Math.floor(s / 60)}min` : `${s}s`);
const STATE = {
  ok: ["text-emerald-300", "concluído", CheckCircle2], partial: ["text-amber-300", "parcial", AlertTriangle], error: ["text-red-300", "erro", XCircle],
  running: ["text-sky-300", "rodando", Loader2], cancelled: ["text-slate-400", "cancelado", Square], waiting: ["text-slate-500", "na fila", Clock],
};
// "r620.exemplo.net.br" -> "r620" (IP fica inteiro)
const shortHost = (h) => (!h || /^[\d.:]+$/.test(h) ? h || "" : h.split(".")[0]);
const daysText = (d) => (!d || d.length === 7 ? "todo dia" : d.length === 5 && [0, 1, 2, 3, 4].every((x) => d.includes(x)) ? "seg a sex" : d.map((x) => DAYS[x]).join(", "));

function St({ s, spin = true }) {
  const [c, l, I] = STATE[s] || ["text-slate-400", s, Clock];
  return <span className={`inline-flex items-center gap-1 text-xs ${c}`}><I className={`w-3.5 h-3.5 ${s === "running" && spin ? "animate-spin" : ""}`} />{l}</span>;
}

// ------------------------------------------------------------------ destino (NAS)
const EMPTY_DEST = { name: "", kind: "sftp", host: "", port: "", user: "", password: "", path: "/backup/vms", passive: true, verify_tls: false, agent_id: "" };

function DestDialog({ cur, agents, onClose, onSaved }) {
  const [f, setF] = useState(cur ? { ...EMPTY_DEST, ...cur, port: cur.port || "", agent_id: cur.agent_id || "", password: "" } : EMPTY_DEST);
  const [busy, setBusy] = useState("");
  const [test, setTest] = useState(null);
  const s = (k) => (e) => setF((p) => ({ ...p, [k]: e?.target ? e.target.value : e }));
  const body = () => ({ ...f, port: f.port ? Number(f.port) : null, agent_id: f.kind === "sftp" ? f.agent_id || null : null });
  const doTest = async () => {
    setBusy("test"); setTest(null);
    try { const r = await api.post(`/virt/backup/dests/test${cur ? `?did=${cur.id}` : ""}`, body()); setTest({ ok: true, text: r.data.detail }); }
    catch (e) { setTest({ ok: false, text: formatApiError(e) }); } finally { setBusy(""); }
  };
  const save = async () => {
    setBusy("save");
    try { cur ? await api.put(`/virt/backup/dests/${cur.id}`, body()) : await api.post("/virt/backup/dests", body()); toast.success("Destino salvo"); onSaved(); onClose(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-xl" data-testid="vb-dest-dialog">
        <DialogHeader><DialogTitle>{cur ? "Editar destino" : "Novo destino de backup (NAS)"}</DialogTitle></DialogHeader>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs font-mono">
          {Object.entries(KINDS).map(([k, l]) => (
            <button key={k} type="button" onClick={() => setF((p) => ({ ...p, kind: k, port: "" }))} data-testid={`vb-kind-${k}`}
              className={`flex-1 py-1.5 rounded ${f.kind === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
          ))}
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Nome</label><Input value={f.name} onChange={s("name")} placeholder="NAS Datacenter" className="bg-sunken border-line" data-testid="vb-dest-name" /></div>
          <div className="grid grid-cols-[1fr_80px] gap-2">
            <div><label className={lbl}>Endereço do NAS</label><Input value={f.host} onChange={s("host")} placeholder="192.0.2.30" className="bg-sunken border-line font-mono" data-testid="vb-dest-host" /></div>
            <div><label className={lbl}>Porta</label><Input value={f.port} onChange={s("port")} placeholder={f.kind === "sftp" ? "22" : "21"} className="bg-sunken border-line font-mono" /></div>
          </div>
          <div><label className={lbl}>Usuário</label><Input value={f.user} onChange={s("user")} className="bg-sunken border-line font-mono" data-testid="vb-dest-user" /></div>
          <div><label className={lbl}>Senha {cur?.has_password && <span className="text-slate-500">(vazio = mantém)</span>}</label>
            <Input type="password" value={f.password} onChange={s("password")} className="bg-sunken border-line font-mono" data-testid="vb-dest-password" /></div>
          <div className="sm:col-span-2"><label className={lbl}>Pasta no NAS (o BastiON cria as subpastas: hypervisor / VM / data)</label>
            <Input value={f.path} onChange={s("path")} placeholder="/backup/vms" className="bg-sunken border-line font-mono" data-testid="vb-dest-path" /></div>
          {f.kind === "sftp" ? (
            <div className="sm:col-span-2"><label className={lbl}>Alcançar por</label>
              <select value={f.agent_id} onChange={s("agent_id")} className={`${sel} w-full`} data-testid="vb-dest-agent">
                <option value="">Direto do servidor do BastiON</option>
                {agents.map((a) => <option key={a.id} value={a.id}>Jump: {a.name}</option>)}
              </select></div>
          ) : (
            <div className="sm:col-span-2 flex flex-wrap gap-5 text-sm text-slate-300">
              <label className="flex items-center gap-2"><Switch checked={!!f.passive} onCheckedChange={s("passive")} /> Modo passivo</label>
              {f.kind === "ftps" && <label className="flex items-center gap-2"><Switch checked={!!f.verify_tls} onCheckedChange={s("verify_tls")} /> Conferir certificado</label>}
            </div>
          )}
        </div>
        <div className="text-xs text-slate-400 bg-sunken border border-line rounded-md p-2">
          {f.kind === "ftp" ? "FTP manda usuário, senha e os backups sem criptografia: prefira SFTP ou FTPS se o NAS tiver (Synology, QNAP, TrueNAS e OpenMediaVault têm os dois)."
            : "O backup vai direto do hypervisor para o NAS passando pelo BastiON, sem gravar no disco do servidor. Use um usuário do NAS só para isso, com acesso só a esta pasta."}
        </div>
        {test && <div className={`text-sm rounded-md border p-2 ${test.ok ? "border-emerald-700 text-emerald-300" : "border-red-800 text-red-300"}`} data-testid="vb-dest-test-result">{test.text}</div>}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={doTest} disabled={!!busy} className="border-line text-slate-200" data-testid="vb-dest-test">
            {busy === "test" ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <CheckCircle2 className="w-4 h-4 mr-1" />}Testar gravação</Button>
          <Button onClick={save} disabled={!!busy} className="bg-brand hover:bg-brand-strong" data-testid="vb-dest-save">{busy === "save" && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ rotina
const EMPTY_JOB = { name: "", hv_id: "", dest_id: "", vms: [], all: false, exclude: [], days: [0, 1, 2, 3, 4, 5, 6], time: "01:00", keep: 7, mode: "snapshot",
  compress: true, quiesce: false, enabled: true, parallel: 2 };

function JobDialog({ cur, hvs, dests, onClose, onSaved, onEditHv }) {
  const [f, setF] = useState(cur ? { ...EMPTY_JOB, ...cur } : { ...EMPTY_JOB, hv_id: hvs[0]?.id || "", dest_id: dests[0]?.id || "" });
  const [vms, setVms] = useState(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const s = (k) => (e) => setF((p) => ({ ...p, [k]: e?.target ? e.target.value : e }));
  const hv = hvs.find((h) => h.id === f.hv_id);
  useEffect(() => {
    if (!f.hv_id) return;
    setVms(null);
    api.get(`/virt/hypervisors/${f.hv_id}`).then((r) => setVms(((r.data.status?.inv?.vms) || []).filter((v) => !v.template))).catch(() => setVms([]));
  }, [f.hv_id]);
  const list = useMemo(() => (vms || []).filter((v) => !q || `${v.name} ${v.vmid} ${v.host}`.toLowerCase().includes(q.toLowerCase())).sort((a, b) => a.name.localeCompare(b.name)), [vms, q]);
  const multiHost = new Set((vms || []).map((v) => v.host)).size > 1;      // ESXi sozinho: o host é sempre o mesmo, não mostra
  const key = f.all ? "exclude" : "vms";
  const toggle = (id) => setF((p) => ({ ...p, [key]: p[key].includes(id) ? p[key].filter((x) => x !== id) : [...p[key], id] }));
  const save = async () => {
    setBusy(true);
    try {
      const b = { ...f, keep: Number(f.keep) || 7, parallel: Number(f.parallel) || 2 };
      cur ? await api.put(`/virt/backup/jobs/${cur.id}`, b) : await api.post("/virt/backup/jobs", b);
      toast.success("Rotina salva"); onSaved(); onClose();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const pve = hv?.kind === "proxmox";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl max-h-[90vh] overflow-y-auto" data-testid="vb-job-dialog">
        <DialogHeader><DialogTitle>{cur ? "Editar rotina de backup" : "Nova rotina de backup"}</DialogTitle></DialogHeader>
        <div className="grid sm:grid-cols-3 gap-3">
          <div><label className={lbl}>Nome</label><Input value={f.name} onChange={s("name")} placeholder="VMs de produção" className="bg-sunken border-line" data-testid="vb-job-name" /></div>
          <div><label className={lbl}>Hypervisor</label>
            <select value={f.hv_id} onChange={(e) => setF((p) => ({ ...p, hv_id: e.target.value, vms: [], exclude: [] }))} className={`${sel} w-full`} data-testid="vb-job-hv">
              {hvs.map((h) => <option key={h.id} value={h.id}>{h.name}</option>)}</select></div>
          <div><label className={lbl}>Destino</label>
            <select value={f.dest_id} onChange={s("dest_id")} className={`${sel} w-full`} data-testid="vb-job-dest">
              {dests.map((d) => <option key={d.id} value={d.id}>{d.name} ({KINDS[d.kind].split(" ")[0]})</option>)}</select></div>
        </div>
        {hv && !hv.has_ssh_password && !hv.ssh_key && (
          <div className="text-sm text-amber-300 border border-amber-800/60 rounded-md p-2 flex items-center gap-2" data-testid="vb-job-nossh">
            <KeyRound className="w-4 h-4 shrink-0" />O backup precisa da senha SSH (root) de {hv.name}.
            <button className="underline ml-auto" onClick={() => onEditHv(hv)}>Configurar</button></div>
        )}
        <div className="border border-line rounded-md">
          <div className="p-2 flex flex-wrap gap-3 items-center border-b border-line">
            <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={!!f.all} onCheckedChange={(v) => setF((p) => ({ ...p, all: v }))} data-testid="vb-job-all" />
              Todas as VMs (inclusive as criadas depois)</label>
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filtrar…" className="bg-sunken border-line h-8 w-40 ml-auto" />
          </div>
          <div className="text-[11px] text-slate-500 px-2 pt-1">{f.all ? "Marque as que NÃO entram:" : "Marque as VMs da rotina:"} {f[key].length} marcada(s)</div>
          <div className="max-h-52 overflow-y-auto p-1 grid sm:grid-cols-2 gap-x-2">
            {vms === null ? <Loader2 className="w-4 h-4 animate-spin text-slate-500 m-2" /> : !list.length ? <div className="text-xs text-slate-500 p-2">Nenhuma VM (o hypervisor já foi coletado?)</div>
              : list.map((v) => (
                <label key={v.id} className="flex items-center gap-2 text-sm px-1 py-0.5 rounded hover:bg-slate-800/50 cursor-pointer" data-testid={`vb-vm-${v.vmid}`}>
                  <input type="checkbox" checked={f[key].includes(v.id)} onChange={() => toggle(v.id)} />
                  <span className={`flex-1 min-w-0 truncate ${f.all && f.exclude.includes(v.id) ? "line-through text-slate-500" : "text-slate-200"}`} title={v.name}>{v.name}</span>
                  {(pve || multiHost) && <span className="text-[11px] text-slate-500 font-mono shrink-0 max-w-[40%] truncate" title={v.host}>{pve ? `${v.vmid} · ` : ""}{shortHost(v.host)}</span>}
                </label>
              ))}
          </div>
        </div>
        <div className="grid sm:grid-cols-[1fr_100px_120px] gap-3 items-end">
          <div><label className={lbl}>Dias</label>
            <div className="flex gap-1">{DAYS.map((d, i) => (
              <button key={d} type="button" onClick={() => setF((p) => ({ ...p, days: p.days.includes(i) ? p.days.filter((x) => x !== i) : [...p.days, i].sort() }))} data-testid={`vb-day-${i}`}
                className={`px-2 h-8 rounded text-xs border ${f.days.includes(i) ? "border-brand bg-brand/20 text-brand-soft" : "border-line text-slate-500"}`}>{d}</button>))}</div></div>
          <div><label className={lbl}>Horário</label><Input type="time" value={f.time} onChange={s("time")} className="bg-sunken border-line font-mono" data-testid="vb-job-time" /></div>
          <div><label className={lbl}>Guardar últimas</label><Input type="number" min={1} value={f.keep} onChange={s("keep")} className="bg-sunken border-line font-mono" data-testid="vb-job-keep" /></div>
        </div>
        {pve ? (
          <div><label className={lbl}>Modo do vzdump</label>
            <select value={f.mode} onChange={s("mode")} className={`${sel} w-full`}>
              <option value="snapshot">Snapshot — VM continua ligada (recomendado)</option>
              <option value="suspend">Suspend — pausa a VM por instantes</option>
              <option value="stop">Stop — desliga a VM durante o backup (mais consistente)</option>
            </select></div>
        ) : (
          <div className="flex flex-wrap gap-5 text-sm text-slate-300">
            <label className="flex items-center gap-2"><Switch checked={!!f.compress} onCheckedChange={s("compress")} /> Compactar os discos (gzip — disco thin vazio vira quase nada)</label>
            <label className="flex items-center gap-2"><Switch checked={!!f.quiesce} onCheckedChange={s("quiesce")} /> Snapshot com VMware Tools (quiesce)</label>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-5">
          <label className="flex items-center gap-2 text-sm text-slate-300">VMs ao mesmo tempo
            <select value={f.parallel} onChange={s("parallel")} className={sel} data-testid="vb-job-parallel">{[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={!!f.enabled} onCheckedChange={s("enabled")} /> Rotina ativa</label>
        </div>
        <div className="text-[11px] text-slate-500 -mt-2">Mais de uma VM por vez aproveita os outros núcleos do hypervisor (a compressão e o SSH usam um núcleo por VM) — use 1 se o storage do host for lento.</div>
        <div className="text-xs text-slate-400 bg-sunken border border-line rounded-md p-2">
          {pve ? "Cada VM vira um arquivo vzdump (.vma.zst / .tar.zst). Para restaurar: copie para a pasta dump de um storage do Proxmox e use Restaurar na própria VM/storage."
            : "Cada VM vira uma pasta com o .vmx e os discos. Se a VM estiver ligada, o BastiON tira um snapshot temporário e apaga no fim. VM que já tem snapshots (ex.: réplica do Veeam) vai com a cadeia inteira de discos e os snapshots dela continuam intactos. Para restaurar: envie a pasta ao datastore, descompacte (gunzip) e registre o .vmx."}
          {" "}Backups com erro abrem incidente e não apagam as cópias antigas.
        </div>
        <DialogFooter><Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="vb-job-save">{busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ execução
function RunRow({ r, onCancel, readOnly }) {
  const [open, setOpen] = useState(r.state === "running");
  return (
    <div className="border-b border-line last:border-0" data-testid={`vb-run-${r.id}`}>
      <button onClick={() => setOpen(!open)} className="w-full px-3 py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-left hover:bg-slate-800/30">
        <ChevronRight className={`w-4 h-4 text-slate-500 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className="text-xs text-slate-500 w-24">{fmt(r.started_at)}</span>
        {r.kind && r.kind !== "backup" && <span className="text-[10px] px-1.5 rounded bg-sky-900/50 text-sky-200">{{ restore: "restauração", copy: "cópia", migrate: "migração" }[r.kind] || r.kind}</span>}
        <span className="text-slate-200">{r.job_name}</span>
        <span className="text-xs text-slate-500">{r.hv_name}</span>
        <St s={r.state} />
        <span className="text-xs text-slate-400 font-mono" title="lido dos discos / enviado ao destino (depois da compressão)">
          {r.live ? `lido ${bytes(r.live.raw)} (${bytes(r.live.raw_rate)}/s) · enviado ${bytes(r.live.bytes)}` : bytes(r.bytes)}</span>
        <span className="text-xs text-slate-500 ml-auto">{r.by || "agendado"}</span>
        {r.live?.current && <span className="basis-full pl-8 text-xs text-sky-300/90 font-mono truncate">{r.live.current}</span>}
        {r.state === "running" && !readOnly && <span onClick={(e) => { e.stopPropagation(); onCancel(r); }} className="text-xs text-red-300 hover:underline" data-testid={`vb-cancel-${r.id}`}>cancelar</span>}
      </button>
      {open && (
        <div className="px-10 pb-2 space-y-1">
          {r.error && <div className="text-xs text-red-300">{r.error}</div>}
          {(r.items || []).map((i) => (
            <div key={i.vm_id} className="text-xs grid grid-cols-[160px_90px_80px_60px_1fr] gap-2 items-baseline">
              <span className="text-slate-200 truncate">{i.name}</span><St s={i.state} />
              <span className="font-mono text-slate-400">{i.state === "ok" ? bytes(i.bytes) : ""}</span>
              <span className="text-slate-500">{i.state === "ok" || i.state === "error" ? dur(i.secs) : ""}</span>
              <span className={`truncate ${i.error ? "text-red-300" : "text-slate-500 font-mono"}`} title={i.error || (i.files || []).join(", ")}>
                {i.error || (i.path ? `${i.path}${i.pruned?.length ? ` · apagou ${i.pruned.length} cópia(s) antiga(s)` : ""}${i.note ? ` · ${i.note}` : ""}` : "")}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function VirtBackup({ hvs, agents, readOnly, setAsk, onEditHv }) {
  const [dests, setDests] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [dd, setDd] = useState(null);         // {} novo | {cur}
  const [jd, setJd] = useState(null);
  const load = useCallback(() => {
    api.get("/virt/backup/dests").then((r) => setDests(r.data)).catch((e) => { setDests([]); toast.error(formatApiError(e)); });
    api.get("/virt/backup/jobs").then((r) => setJobs(r.data)).catch(() => {});
    api.get("/virt/backup/runs", { params: { limit: 40 } }).then((r) => setRuns(r.data)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  const running = runs.some((r) => r.state === "running");
  useEffect(() => { const i = setInterval(load, running ? 3000 : 30000); return () => clearInterval(i); }, [load, running]);

  const runNow = async (j) => {
    try { await api.post(`/virt/backup/jobs/${j.id}/run`, {}); toast.success(`Backup "${j.name}" iniciado`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const delJob = (j) => setAsk({ title: "Apagar rotina?", danger: true, ok: "Apagar", text: `A rotina "${j.name}" para de rodar. Os backups já gravados no NAS continuam lá.`,
    run: async () => { try { await api.delete(`/virt/backup/jobs/${j.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } } });
  const delDest = (d) => setAsk({ title: "Apagar destino?", danger: true, ok: "Apagar", text: `${d.name} sai do BastiON (nada é apagado no NAS).`,
    run: async () => { try { await api.delete(`/virt/backup/dests/${d.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } } });
  const cancel = (r) => setAsk({ title: "Cancelar backup?", danger: true, ok: "Cancelar backup", text: `A VM que está sendo copiada agora fica sem esta cópia (o arquivo pela metade é apagado do NAS). Snapshots temporários são removidos.`,
    run: async () => { try { await api.post(`/virt/backup/runs/${r.id}/cancel`); load(); } catch (e) { toast.error(formatApiError(e)); } } });
  const hvName = (id) => hvs.find((h) => h.id === id)?.name || "?";
  const destName = (id) => (dests || []).find((d) => d.id === id)?.name || "?";

  return (
    <div className="space-y-4" data-testid="vb-page">
      <Card className="bg-surface border-line">
        <div className="px-3 py-2 flex items-center border-b border-line">
          <span className="text-slate-100 font-medium flex items-center gap-2"><HardDrive className="w-4 h-4 text-slate-400" />Destinos (NAS)</span>
          {!readOnly && <Button size="sm" onClick={() => setDd({})} className="ml-auto bg-brand hover:bg-brand-strong h-8" data-testid="vb-dest-add"><Plus className="w-4 h-4 mr-1" />Destino</Button>}
        </div>
        {dests === null ? <Loader2 className="w-5 h-5 animate-spin text-slate-500 m-3" /> : !dests.length ? (
          <div className="p-3 text-sm text-slate-400">Cadastre o NAS (pasta FTP, FTPS ou SFTP) para onde vão os backups.</div>
        ) : dests.map((d) => (
          <div key={d.id} className="px-3 py-2 border-b border-line last:border-0 flex items-center gap-3 text-sm" data-testid={`vb-dest-${d.id}`}>
            <span className="text-slate-200">{d.name}</span>
            <span className="text-xs font-mono text-slate-500">{d.kind}://{d.user}@{d.host}:{d.port}{d.path}{d.agent_id ? ` · via ${agents.find((a) => a.id === d.agent_id)?.name || "jump"}` : ""}</span>
            {!readOnly && <span className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setDd({ cur: d })} className="h-7 text-slate-300"><Pencil className="w-4 h-4" /></Button>
              <Button size="sm" variant="ghost" onClick={() => delDest(d)} className="h-7 text-red-300"><Trash2 className="w-4 h-4" /></Button></span>}
          </div>
        ))}
      </Card>

      <Card className="bg-surface border-line">
        <div className="px-3 py-2 flex items-center border-b border-line">
          <span className="text-slate-100 font-medium">Rotinas</span>
          {!readOnly && <Button size="sm" disabled={!dests?.length || !hvs.length} onClick={() => setJd({})} className="ml-auto bg-brand hover:bg-brand-strong h-8" data-testid="vb-job-add"
            title={!dests?.length ? "Cadastre um destino primeiro" : ""}><Plus className="w-4 h-4 mr-1" />Rotina</Button>}
        </div>
        {!jobs.length ? <div className="p-3 text-sm text-slate-400">Nenhuma rotina. Crie uma: hypervisor, VMs, dias, horário e quantas cópias guardar.</div> : jobs.map((j) => (
          <div key={j.id} className={`px-3 py-2 border-b border-line last:border-0 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm ${j.enabled ? "" : "opacity-60"}`} data-testid={`vb-job-${j.id}`}>
            <span className="text-slate-200 font-medium">{j.name}</span>
            <span className="text-xs text-slate-400">{hvName(j.hv_id)} → {destName(j.dest_id)}</span>
            <span className="text-xs text-slate-400">{j.all ? `todas as VMs${j.exclude?.length ? ` menos ${j.exclude.length}` : ""}` : `${j.vms.length} VM(s)`}</span>
            <span className="text-xs text-slate-400 flex items-center gap-1"><Clock className="w-3.5 h-3.5" />{daysText(j.days)} às {j.time} · guarda {j.keep}</span>
            {j.last_run && <span className="text-xs flex items-center gap-1 text-slate-500">último: {fmt(j.last_run.started_at)} <St s={j.last_run.state} /></span>}
            {!readOnly && <span className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" disabled={j.running} onClick={() => runNow(j)} className="h-7 text-emerald-300" title="Rodar agora" data-testid={`vb-run-now-${j.id}`}>
                {j.running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}</Button>
              <Button size="sm" variant="ghost" onClick={() => setJd({ cur: j })} className="h-7 text-slate-300"><Pencil className="w-4 h-4" /></Button>
              <Button size="sm" variant="ghost" onClick={() => delJob(j)} className="h-7 text-red-300"><Trash2 className="w-4 h-4" /></Button></span>}
          </div>
        ))}
      </Card>

      {dests?.length > 0 && <RestoreBrowser dests={dests} hvs={hvs} readOnly={readOnly} onStarted={load} />}

      <Card className="bg-surface border-line">
        <div className="px-3 py-2 border-b border-line text-slate-100 font-medium">Execuções</div>
        {!runs.length ? <div className="p-3 text-sm text-slate-400">Nenhum backup rodou ainda.</div>
          : runs.map((r) => <RunRow key={r.id} r={r} onCancel={cancel} readOnly={readOnly} />)}
      </Card>

      {dd && <DestDialog cur={dd.cur} agents={agents} onClose={() => setDd(null)} onSaved={load} />}
      {jd && dests && <JobDialog cur={jd.cur} hvs={hvs} dests={dests} onClose={() => setJd(null)} onSaved={load} onEditHv={(h) => { setJd(null); onEditHv(h); }} />}
    </div>
  );
}
