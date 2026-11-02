import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Loader2, Folder, ArrowUp, RotateCcw, Server, AlertTriangle, FileText } from "lucide-react";

// Restaurar backup do NAS / copiar ou migrar VM entre servidores (ESXi -> ESXi, Proxmox -> Proxmox, ESXi -> Proxmox).
const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const bytes = (b) => (!b ? "—" : b >= 1e12 ? `${(b / 1e12).toFixed(2)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${Math.round(b / 1e3)} kB`);
const STAMP = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})?$/;
const stampText = (s) => { const m = STAMP.exec(s); return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : s; };

// source: {type:"vm", hv, vm} | {type:"backup", dest_id, path, kind ("vmware"|"proxmox"), label}
export function TransferDialog({ source, hvs, onClose, onStarted }) {
  const srcKind = source.type === "vm" ? source.hv.kind : source.kind;
  const allowed = hvs.filter((h) => srcKind === "vmware" || h.kind === "proxmox");          // Proxmox só vai para Proxmox
  const [f, setF] = useState({
    target_hv_id: (allowed.find((h) => source.type !== "vm" || h.id !== source.hv.id) || allowed[0])?.id || "",
    target_host: "", datastore: "", name: source.type === "vm" ? `${source.vm.name}-copia` : source.name || "", vmid: "",
    bridge: "vmbr0", bus: "sata", nic: "vmxnet3", tmp_dir: "/var/tmp", mode: "copy", power_on: false,
  });
  const [tg, setTg] = useState(null);
  const [busy, setBusy] = useState(false);
  const s = (k) => (e) => setF((p) => ({ ...p, [k]: e?.target ? e.target.value : e }));
  const th = hvs.find((h) => h.id === f.target_hv_id);
  const pve = th?.kind === "proxmox";
  const convert = pve && srcKind === "vmware";
  useEffect(() => {
    if (!f.target_hv_id) return;
    setTg(null);
    api.get(`/virt/targets/${f.target_hv_id}`).then((r) => {
      setTg(r.data);
      setF((p) => ({ ...p, target_host: r.data.hosts[0]?.name || "", datastore: "" }));
    }).catch((e) => setTg({ error: formatApiError(e), hosts: [], storages: [] }));
  }, [f.target_hv_id]);
  useEffect(() => {
    if (source.type === "vm") setF((p) => ({ ...p, name: p.mode === "migrate" ? source.vm.name : `${source.vm.name}-copia` }));
  }, [f.mode]); // eslint-disable-line react-hooks/exhaustive-deps
  const stores = (tg?.storages || []).filter((x) => !pve || !x.host || x.host === f.target_host || x.shared)
    .filter((x, i, a) => a.findIndex((y) => y.name === x.name) === i);
  const go = async () => {
    setBusy(true);
    try {
      const body = { ...f, vmid: f.vmid ? Number(f.vmid) : null, source: source.type,
        ...(source.type === "vm" ? { hv_id: source.hv.id, vm_id: source.vm.id } : { dest_id: source.dest_id, path: source.path }) };
      const r = await api.post("/virt/transfer", body);
      toast.success("Iniciado — acompanhe em Backups → Execuções");
      onStarted && onStarted(r.data.run_id); onClose();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const title = source.type === "vm" ? `Copiar / migrar ${source.vm.name}` : `Restaurar ${source.label || "backup"}`;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl max-h-[90vh] overflow-y-auto" data-testid="vt-dialog">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        {source.type === "vm" ? (
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs">
            {[["copy", "Cópia — a original continua ligada; a cópia ganha MAC/UUID novos"], ["migrate", "Migração — desliga a original, copia e mantém MAC/UUID"]].map(([k, l]) => (
              <button key={k} type="button" onClick={() => setF((p) => ({ ...p, mode: k, power_on: k === "migrate" }))} data-testid={`vt-mode-${k}`}
                className={`flex-1 py-1.5 px-2 rounded text-left ${f.mode === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
            ))}
          </div>
        ) : (
          <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.mode === "migrate"} onCheckedChange={(v) => setF((p) => ({ ...p, mode: v ? "migrate" : "copy" }))} data-testid="vt-keep" />
            Manter MAC e UUID da VM original (use quando a original não existe mais; senão vai dar conflito de MAC na rede)</label>
        )}
        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Servidor de destino</label>
            <select value={f.target_hv_id} onChange={(e) => setF((p) => ({ ...p, target_hv_id: e.target.value }))} className={`${sel} w-full`} data-testid="vt-target">
              {allowed.map((h) => <option key={h.id} value={h.id}>{h.name} ({h.kind === "proxmox" ? "Proxmox" : "VMware"})</option>)}</select></div>
          {(tg?.hosts || []).length > 1 && (
            <div><label className={lbl}>{pve ? "Nó" : "Host ESXi"}</label>
              <select value={f.target_host} onChange={s("target_host")} className={`${sel} w-full`} data-testid="vt-host">
                {tg.hosts.map((h) => <option key={h.name} value={h.name}>{h.name}{h.state && h.state !== "online" ? " (fora)" : ""}</option>)}</select></div>
          )}
          <div><label className={lbl}>{pve ? "Storage dos discos" : "Datastore"}</label>
            <select value={f.datastore} onChange={s("datastore")} className={`${sel} w-full`} data-testid="vt-datastore">
              <option value="">{tg ? "escolha…" : "carregando…"}</option>
              {stores.map((x) => <option key={x.name} value={x.name}>{x.name}{x.type ? ` · ${x.type}` : ""}{x.free ? ` · ${bytes(x.free)} livres` : ""}</option>)}</select></div>
          <div><label className={lbl}>Nome da VM no destino</label><Input value={f.name} onChange={s("name")} className="bg-sunken border-line" data-testid="vt-name" /></div>
          {pve && <div><label className={lbl}>VMID (vazio = próximo livre)</label><Input value={f.vmid} onChange={s("vmid")} placeholder="auto" className="bg-sunken border-line font-mono" data-testid="vt-vmid" /></div>}
          {pve && <div><label className={lbl}>Pasta temporária no nó (precisa de espaço)</label><Input value={f.tmp_dir} onChange={s("tmp_dir")} className="bg-sunken border-line font-mono" /></div>}
        </div>
        {convert && (
          <div className="grid sm:grid-cols-3 gap-3">
            <div><label className={lbl}>Bridge da rede</label><Input value={f.bridge} onChange={s("bridge")} className="bg-sunken border-line font-mono" data-testid="vt-bridge" /></div>
            <div><label className={lbl}>Placa de rede</label>
              <select value={f.nic} onChange={s("nic")} className={`${sel} w-full`}>
                <option value="vmxnet3">vmxnet3 (mesma do VMware)</option><option value="e1000">e1000 (qualquer sistema)</option><option value="virtio">virtio (precisa driver)</option></select></div>
            <div><label className={lbl}>Disco</label>
              <select value={f.bus} onChange={s("bus")} className={`${sel} w-full`}>
                <option value="sata">SATA (liga em qualquer sistema)</option><option value="scsi">SCSI VMware PVSCSI</option></select></div>
          </div>
        )}
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={!!f.power_on} onCheckedChange={s("power_on")} data-testid="vt-poweron" /> Ligar a VM no destino no fim</label>
        {tg && !tg.ssh && <div className="text-sm text-amber-300 flex items-center gap-2"><AlertTriangle className="w-4 h-4" />Cadastre o acesso SSH de {th?.name} (Editar → Backup das VMs).</div>}
        {tg?.error && <div className="text-sm text-red-300">{tg.error}</div>}
        <div className="text-xs text-slate-400 bg-sunken border border-line rounded-md p-2 space-y-1">
          {source.type === "vm" && f.mode === "copy" && <div>A VM original segue ligada: o BastiON tira um snapshot temporário, copia e apaga o snapshot. A cópia é do momento do snapshot.</div>}
          {source.type === "vm" && f.mode === "migrate" && <div>A VM original é desligada pelo sistema (precisa do VMware Tools / QEMU agent ou ACPI) antes da cópia e fica desligada no fim. Se der erro, o BastiON religa a original.</div>}
          {convert && <div>ESXi → Proxmox: cria a VM com a mesma CPU, memória e firmware (BIOS/UEFI) e importa os discos com <span className="font-mono">qm importdisk</span>. Os discos passam pela pasta temporária do nó (só o espaço realmente usado). No primeiro boot, Windows pode pedir drivers; Linux sobe direto. Desinstale o VMware Tools depois.</div>}
          {pve && !convert && <div>Proxmox → Proxmox: vzdump da origem direto para o nó de destino e <span className="font-mono">qmrestore</span>{f.mode === "copy" ? " com MAC novo" : ""}.</div>}
          {!pve && <div>Destino ESXi: a VM vai para uma pasta nova no datastore, é registrada e os discos thin continuam thin.</div>}
          <div>Tudo passa pelo BastiON (direto ou pelos jumps). O tempo depende do tamanho dos discos e do link até os dois servidores.</div>
        </div>
        <DialogFooter><Button onClick={go} disabled={busy || !f.datastore || !f.name.trim() || !tg?.ssh} className="bg-brand hover:bg-brand-strong" data-testid="vt-go">
          {busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}{source.type === "vm" ? (f.mode === "migrate" ? "Migrar" : "Copiar") : "Restaurar"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Navega os backups no NAS (hypervisor -> VM -> data) e restaura
export function RestoreBrowser({ dests, hvs, readOnly, onStarted }) {
  const [did, setDid] = useState("");
  const [path, setPath] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [dlg, setDlg] = useState(null);
  useEffect(() => { if (!did && dests?.length) setDid(dests[0].id); }, [dests, did]);
  const load = useCallback(() => {
    if (!did) return;
    setLoading(true); setErr("");
    api.get(`/virt/backup/dests/${did}/browse`, { params: { path } }).then((r) => setData(r.data)).catch((e) => { setData(null); setErr(formatApiError(e)); }).finally(() => setLoading(false));
  }, [did, path]);
  useEffect(() => { load(); }, [load]);
  const parts = path ? path.split("/") : [];
  const up = () => setPath(parts.slice(0, -1).join("/"));
  const into = (n) => setPath(path ? `${path}/${n}` : n);
  const vmLabel = parts.length >= 2 ? parts[parts.length - (data?.backup ? 2 : 1)].replace(/_[^_]+$/, "") : "";
  return (
    <Card className="bg-surface border-line" data-testid="vt-browser">
      <div className="px-3 py-2 flex flex-wrap items-center gap-2 border-b border-line">
        <span className="text-slate-100 font-medium flex items-center gap-2"><RotateCcw className="w-4 h-4 text-slate-400" />Restaurar do NAS</span>
        <select value={did} onChange={(e) => { setDid(e.target.value); setPath(""); }} className={`${sel} h-8 ml-auto`} data-testid="vt-dest">
          {(dests || []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
      </div>
      <div className="px-3 py-1.5 text-xs font-mono text-slate-400 flex items-center gap-2 border-b border-line">
        {path && <button onClick={up} className="text-slate-300 hover:text-white" title="Voltar"><ArrowUp className="w-4 h-4" /></button>}
        <span>/{path}</span>{loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
      </div>
      {err && <div className="p-3 text-sm text-red-300">{err}</div>}
      {data && !data.backup && (
        <div className="divide-y divide-line max-h-80 overflow-y-auto">
          {!data.items.length && <div className="p-3 text-sm text-slate-500">Vazio</div>}
          {data.items.map((i) => (
            <div key={i.name} className="px-3 py-1.5 flex items-center gap-2 text-sm hover:bg-slate-800/30" data-testid={`vt-item-${i.name}`}>
              <button onClick={() => into(i.name)} className="flex items-center gap-2 text-slate-200 flex-1 text-left">
                {i.stamp ? <FileText className="w-4 h-4 text-emerald-300" /> : parts.length === 0 ? <Server className="w-4 h-4 text-slate-400" /> : <Folder className="w-4 h-4 text-slate-400" />}
                {i.stamp ? stampText(i.name) : i.name}</button>
              {i.stamp && !readOnly && <Button size="sm" variant="ghost" className="h-7 text-brand-soft" data-testid={`vt-restore-${i.name}`}
                onClick={() => setDlg({ path: path ? `${path}/${i.name}` : i.name, label: `${vmLabel} de ${stampText(i.name)}`, name: `${vmLabel}-restaurada`, kind: "" })}>Restaurar</Button>}
            </div>
          ))}
        </div>
      )}
      {data?.backup && (
        <div className="p-3 space-y-2">
          <div className="text-sm text-slate-300">Cópia de <b>{vmLabel}</b> em {stampText(parts[parts.length - 1])} · {data.kind === "proxmox" ? "Proxmox (vzdump)" : data.kind === "vmware" ? "VMware (vmx + discos)" : "formato desconhecido"}</div>
          <div className="text-xs font-mono text-slate-400 space-y-0.5">{data.items.map((i) => <div key={i.name}>{i.name} · {bytes(i.size)}</div>)}</div>
          {!readOnly && data.kind && <Button size="sm" onClick={() => setDlg({ path, label: `${vmLabel} de ${stampText(parts[parts.length - 1])}`, name: `${vmLabel}-restaurada`, kind: data.kind })} className="bg-brand hover:bg-brand-strong" data-testid="vt-restore-here">
            <RotateCcw className="w-4 h-4 mr-1" />Restaurar esta cópia</Button>}
        </div>
      )}
      {dlg && <RestoreKind dlg={dlg} did={did} hvs={hvs} onClose={() => setDlg(null)} onStarted={onStarted} />}
    </Card>
  );
}

// descobre o tipo da cópia (se veio da lista) antes de abrir o diálogo
function RestoreKind({ dlg, did, hvs, onClose, onStarted }) {
  const [kind, setKind] = useState(dlg.kind);
  useEffect(() => {
    if (kind) return;
    api.get(`/virt/backup/dests/${did}/browse`, { params: { path: dlg.path } }).then((r) => {
      if (!r.data.kind) { toast.error("Essa pasta não tem um backup reconhecido"); onClose(); } else setKind(r.data.kind);
    }).catch((e) => { toast.error(formatApiError(e)); onClose(); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!kind) return null;
  return <TransferDialog source={{ type: "backup", dest_id: did, path: dlg.path, kind, label: dlg.label, name: dlg.name }} hvs={hvs} onClose={onClose} onStarted={onStarted} />;
}
