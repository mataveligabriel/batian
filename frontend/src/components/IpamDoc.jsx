import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Plus, Pencil, Trash2, Loader2, ArrowLeft, Download, Upload, Save, X, Search, PlugZap, CheckCircle2, AlertTriangle, BookOpen } from "lucide-react";

const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const STATUS = { used: ["Em uso", "text-emerald-300"], reserved: ["Reservado", "text-amber-300"], dhcp: ["DHCP", "text-sky-300"], offline: ["Desligado", "text-slate-500"], free: ["Livre", "text-slate-400"] };
const COLORS = ["#2F6FCB", "#1E9E6B", "#C2410C", "#7C3AED", "#BE185D", "#0E7490", "#A16207", "#4B5563"];
const emptyIp = { ip: "", hostname: "", description: "", status: "used", mac: "", owner: "", vrf: "", notes: "" };

function GroupForm({ g, onSave, onCancel }) {
  const [f, setF] = useState({ name: g?.name || "", description: g?.description || "", color: g?.color || COLORS[0] });
  return (
    <div className="space-y-2 p-3 rounded border border-line bg-sunken/50" data-testid="doc-group-form">
      <Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Nome (ex.: Rede Guaçuí)" className="bg-sunken border-line h-8" data-testid="doc-group-name" />
      <Input value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="descrição" className="bg-sunken border-line h-8 text-xs" />
      <div className="flex gap-1">{COLORS.map(c => <button key={c} onClick={() => setF({ ...f, color: c })} className={`w-5 h-5 rounded-full border-2 ${f.color === c ? "border-white" : "border-transparent"}`} style={{ background: c }} />)}</div>
      <div className="flex gap-1"><Button size="sm" onClick={() => onSave(f)} disabled={!f.name.trim()} className="h-7 bg-brand hover:bg-brand-strong text-onbrand" data-testid="doc-group-save"><Save className="w-3.5 h-3.5 mr-1" />Salvar</Button>
        <Button size="sm" variant="ghost" onClick={onCancel} className="h-7 text-slate-500">Cancelar</Button></div>
    </div>
  );
}

function IpForm({ value, onSave, onCancel, busy }) {
  const [f, setF] = useState({ ...emptyIp, ...value });
  useEffect(() => { setF({ ...emptyIp, ...value }); }, [value]);
  const set = (k, v) => setF({ ...f, [k]: v });
  return (
    <div className="grid gap-2 sm:grid-cols-[9rem_1fr_1fr_8rem] p-3 rounded border border-line bg-sunken/40" data-testid="doc-ip-form">
      <Input value={f.ip} onChange={e => set("ip", e.target.value)} placeholder="IP" className="bg-sunken border-line h-8 font-mono" data-testid="doc-ip-ip" />
      <Input value={f.hostname} onChange={e => set("hostname", e.target.value)} placeholder="hostname" className="bg-sunken border-line h-8" data-testid="doc-ip-host" />
      <Input value={f.description} onChange={e => set("description", e.target.value)} placeholder="descrição" className="bg-sunken border-line h-8" data-testid="doc-ip-desc" />
      <select value={f.status} onChange={e => set("status", e.target.value)} className={`${sel} h-8`} data-testid="doc-ip-status">{Object.entries(STATUS).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}</select>
      <Input value={f.mac} onChange={e => set("mac", e.target.value)} placeholder="MAC" className="bg-sunken border-line h-8 font-mono" />
      <Input value={f.owner} onChange={e => set("owner", e.target.value)} placeholder="dono / cliente" className="bg-sunken border-line h-8" />
      <Input value={f.notes} onChange={e => set("notes", e.target.value)} placeholder="observação" className="bg-sunken border-line h-8" />
      <Input value={f.vrf} onChange={e => set("vrf", e.target.value)} placeholder="VRF" className="bg-sunken border-line h-8 font-mono" />
      <div className="sm:col-span-4 flex gap-1">
        <Button size="sm" onClick={() => onSave(f)} disabled={busy || !f.ip.trim()} className="h-7 bg-brand hover:bg-brand-strong text-onbrand" data-testid="doc-ip-save">{busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}Salvar</Button>
        <Button size="sm" variant="ghost" onClick={onCancel} className="h-7 text-slate-500">Cancelar</Button>
      </div>
    </div>
  );
}

function SubnetView({ g, sub, loose, onBack }) {
  const [d, setD] = useState(null);
  const [q, setQ] = useState("");
  const [form, setForm] = useState(null);          // {mode:"new"|id, value}
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get(`/ipam/doc/groups/${g.id}/ips`, { params: { subnet_id: sub?.id || "", loose: !!loose, q } })
    .then(r => setD(r.data)).catch(e => toast.error(formatApiError(e))), [g.id, sub, loose, q]);
  useEffect(() => { const t = setTimeout(load, q ? 300 : 0); return () => clearTimeout(t); }, [load, q]);
  const save = async (f) => {
    setBusy(true);
    try {
      if (form.mode === "new") await api.post(`/ipam/doc/groups/${g.id}/ips`, { ...f, vrf: f.vrf || sub?.vrf || "" });
      else await api.put(`/ipam/doc/ips/${form.mode}`, f);
      toast.success(`${f.ip} salvo`); setForm(null); load();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const del = async (r) => { if (!window.confirm(`Tirar ${r.ip} da documentação de ${g.name}?`)) return; try { await api.delete(`/ipam/doc/ips/${r.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const s = d?.subnet;
  return (
    <div className="space-y-3" data-testid="doc-subnet">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="ghost" onClick={onBack} className="text-slate-300 h-8"><ArrowLeft className="w-4 h-4 mr-1" />{g.name}</Button>
        <span className="font-mono text-lg text-slate-100">{loose ? "IPs sem prefixo" : sub.prefix}</span>
        {s && <span className="text-xs text-slate-400">{s.vlan ? `VLAN ${s.vlan} · ` : ""}{s.vrf ? `VRF ${s.vrf} · ` : ""}{s.description} · {s.used}/{s.usable} documentados ({s.pct}%)</span>}
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="filtrar" className="bg-sunken border-line h-8 w-48 ml-auto" />
        <Button size="sm" onClick={() => setForm({ mode: "new", value: { ...emptyIp } })} className="h-8 bg-brand hover:bg-brand-strong text-onbrand" data-testid="doc-ip-add"><Plus className="w-4 h-4 mr-1" />IP</Button>
      </div>
      {form?.mode === "new" && <IpForm value={form.value} onSave={save} onCancel={() => setForm(null)} busy={busy} />}
      {d?.free?.length > 0 && (
        <div className="text-xs text-slate-500 flex flex-wrap gap-1 items-center" data-testid="doc-free"><span className="mr-1">Livres:</span>
          {d.free.slice(0, 24).map(ip => <button key={ip} onClick={() => setForm({ mode: "new", value: { ...emptyIp, ip } })} className="font-mono px-1.5 py-0.5 rounded border border-line text-slate-400 hover:text-slate-100 hover:border-brand/50">{ip}</button>)}
          {d.free.length > 24 && <span>…</span>}
        </div>
      )}
      <Card className="bg-surface border-line overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500"><tr><th className="text-left px-3 py-2">IP</th><th className="text-left px-2">Hostname</th><th className="text-left px-2">Descrição</th><th className="text-left px-2">Situação</th><th className="text-left px-2">MAC / dono</th><th className="text-left px-2">Nas configurações</th><th /></tr></thead>
          <tbody className="divide-y divide-line">
            {d && d.ips.length === 0 && <tr><td colSpan={7} className="py-6 text-center text-slate-500">Nenhum IP documentado aqui ainda.</td></tr>}
            {(d?.ips || []).map(r => (form?.mode === r.id ? (
              <tr key={r.id}><td colSpan={7} className="p-2"><IpForm value={r} onSave={save} onCancel={() => setForm(null)} busy={busy} /></td></tr>
            ) : (
              <tr key={r.id} data-testid={`doc-ip-${r.ip}`}>
                <td className="px-3 py-1.5 font-mono text-slate-100 whitespace-nowrap">{r.ip}{r.vrf && <span className="text-[10px] text-amber-300"> @{r.vrf}</span>}</td>
                <td className="px-2 text-slate-200">{r.hostname}</td>
                <td className="px-2 text-slate-400 text-xs">{r.description}{r.notes ? <div className="text-slate-500">{r.notes}</div> : null}
                  {r.also_in?.length > 0 && <div className="text-[10px] text-brand-soft">também em: {r.also_in.join(", ")}</div>}</td>
                <td className={`px-2 text-xs ${STATUS[r.status]?.[1]}`}>{STATUS[r.status]?.[0] || r.status}</td>
                <td className="px-2 text-xs text-slate-400"><span className="font-mono">{r.mac}</span>{r.owner ? <div>{r.owner}</div> : null}</td>
                <td className="px-2 text-[11px] text-slate-500">{(r.seen || []).join(", ") || "—"}</td>
                <td className="px-2 pr-3 text-right whitespace-nowrap">
                  <button onClick={() => setForm({ mode: r.id, value: r })} className="text-slate-500 hover:text-slate-200 mr-2"><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={() => del(r)} className="text-slate-500 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></button></td>
              </tr>)))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function SubnetForm({ value, onSave, onCancel }) {
  const [f, setF] = useState({ prefix: "", vlan: "", vrf: "", description: "", location: "", gateway: "", notes: "", ...value });
  const set = (k, v) => setF({ ...f, [k]: v });
  return (
    <div className="grid gap-2 sm:grid-cols-[10rem_6rem_8rem_1fr] p-3 rounded border border-line bg-sunken/40" data-testid="doc-sub-form">
      <Input value={f.prefix} onChange={e => set("prefix", e.target.value)} placeholder="10.20.0.0/24" className="bg-sunken border-line h-8 font-mono" data-testid="doc-sub-prefix" />
      <Input value={f.vlan ?? ""} onChange={e => set("vlan", e.target.value)} placeholder="VLAN" className="bg-sunken border-line h-8" />
      <Input value={f.vrf} onChange={e => set("vrf", e.target.value)} placeholder="VRF" className="bg-sunken border-line h-8 font-mono" />
      <Input value={f.description} onChange={e => set("description", e.target.value)} placeholder="descrição" className="bg-sunken border-line h-8" data-testid="doc-sub-desc" />
      <Input value={f.gateway} onChange={e => set("gateway", e.target.value)} placeholder="gateway" className="bg-sunken border-line h-8 font-mono" />
      <Input value={f.location} onChange={e => set("location", e.target.value)} placeholder="local" className="bg-sunken border-line h-8 sm:col-span-2" />
      <Input value={f.notes} onChange={e => set("notes", e.target.value)} placeholder="observação" className="bg-sunken border-line h-8" />
      <div className="sm:col-span-4 flex gap-1"><Button size="sm" onClick={() => onSave(f)} disabled={!f.prefix.trim()} className="h-7 bg-brand hover:bg-brand-strong text-onbrand" data-testid="doc-sub-save"><Save className="w-3.5 h-3.5 mr-1" />Salvar</Button>
        <Button size="sm" variant="ghost" onClick={onCancel} className="h-7 text-slate-500">Cancelar</Button></div>
    </div>
  );
}

export function DocTab() {
  const [groups, setGroups] = useState(null);
  const [gid, setGid] = useState(() => { try { return localStorage.getItem("ipam_doc_group") || ""; } catch { return ""; } });
  const [gform, setGform] = useState(null);       // "new" | group
  const [subs, setSubs] = useState(null);
  const [sform, setSform] = useState(null);
  const [view, setView] = useState(null);         // {sub} | {loose:true}
  const [csv, setCsv] = useState(null);
  const loadGroups = useCallback(() => api.get("/ipam/doc/groups").then(r => setGroups(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { loadGroups(); }, [loadGroups]);
  const g = (groups || []).find(x => x.id === gid) || (groups || [])[0];
  useEffect(() => { try { if (g) localStorage.setItem("ipam_doc_group", g.id); } catch { /* sem storage */ } }, [g]);
  const loadSubs = useCallback(() => { if (g) api.get(`/ipam/doc/groups/${g.id}/subnets`).then(r => setSubs(r.data)).catch(e => toast.error(formatApiError(e))); }, [g?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setSubs(null); setView(null); loadSubs(); }, [loadSubs]);
  const saveGroup = async (f) => {
    try {
      const r = gform === "new" ? await api.post("/ipam/doc/groups", f) : await api.put(`/ipam/doc/groups/${gform.id}`, f);
      setGform(null); await loadGroups(); setGid(r.data.id);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const delGroup = async () => {
    if (!window.confirm(`Apagar o grupo ${g.name} com todos os prefixos e IPs documentados nele?`)) return;
    try { await api.delete(`/ipam/doc/groups/${g.id}`); setGid(""); loadGroups(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const saveSub = async (f) => {
    try {
      const body = { ...f, vlan: f.vlan === "" ? null : f.vlan };
      if (sform.id) await api.put(`/ipam/doc/subnets/${sform.id}`, body); else await api.post(`/ipam/doc/groups/${g.id}/subnets`, body);
      setSform(null); loadSubs(); loadGroups();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const delSub = async (s) => { if (!window.confirm(`Apagar o prefixo ${s.prefix}? (os IPs ficam, sem prefixo)`)) return; try { await api.delete(`/ipam/doc/subnets/${s.id}`); loadSubs(); } catch (e) { toast.error(formatApiError(e)); } };
  const exportCsv = async () => {
    try {
      const r = await api.get(`/ipam/doc/groups/${g.id}/export`, { responseType: "blob" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(r.data); a.download = `ipam-${g.name}.csv`; a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const importCsv = async () => {
    try {
      const r = await api.post(`/ipam/doc/groups/${g.id}/import-csv`, { text: csv.text, overwrite: csv.overwrite });
      toast.success(`${r.data.created} criado(s), ${r.data.updated} atualizado(s), ${r.data.kept} mantido(s)${r.data.errors.length ? ` · ${r.data.errors.length} erro(s)` : ""}`);
      setCsv(null); loadSubs(); loadGroups();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  if (!groups) return <Loader2 className="w-5 h-5 animate-spin text-slate-500" />;
  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr]" data-testid="doc-tab">
      <div className="space-y-2">
        <div className="flex items-center justify-between"><span className="text-xs uppercase tracking-wide text-slate-500">Grupos</span>
          <Button size="sm" variant="ghost" onClick={() => setGform("new")} className="h-7 text-slate-300" data-testid="doc-group-add"><Plus className="w-4 h-4 mr-1" />Grupo</Button></div>
        {gform === "new" && <GroupForm onSave={saveGroup} onCancel={() => setGform(null)} />}
        {groups.length === 0 && !gform && <div className="text-xs text-slate-500">Crie um grupo para cada rede separada (cidade, cliente, VRF). O mesmo IP pode ser documentado em vários grupos.</div>}
        {groups.map(x => (gform?.id === x.id ? <GroupForm key={x.id} g={x} onSave={saveGroup} onCancel={() => setGform(null)} /> : (
          <button key={x.id} onClick={() => setGid(x.id)} data-testid={`doc-group-${x.name}`}
            className={`w-full text-left rounded-md border px-3 py-2 ${g?.id === x.id ? "border-brand/60 bg-brand/10" : "border-line bg-surface hover:border-line2"}`}>
            <div className="flex items-center gap-2"><span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: x.color }} /><span className="text-sm text-slate-100 truncate">{x.name}</span></div>
            <div className="text-[11px] text-slate-500 pl-4">{x.subnets} prefixo(s) · {x.ips} IP(s)</div>
          </button>)))}
      </div>
      {g && (view ? <SubnetView g={g} sub={view.sub} loose={view.loose} onBack={() => { setView(null); loadSubs(); loadGroups(); }} /> : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-3 h-3 rounded-full" style={{ background: g.color }} />
            <span className="text-lg text-slate-100 font-medium">{g.name}</span><span className="text-xs text-slate-500">{g.description}</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" onClick={() => setSform({})} className="h-8 bg-brand hover:bg-brand-strong text-onbrand" data-testid="doc-sub-add"><Plus className="w-4 h-4 mr-1" />Prefixo</Button>
              <Button size="sm" variant="outline" onClick={() => setView({ loose: true })} className="h-8 border-line bg-transparent" data-testid="doc-search"><Search className="w-4 h-4 mr-1" />IPs sem prefixo{subs?.loose ? ` (${subs.loose})` : ""}</Button>
              <Button size="sm" variant="ghost" onClick={exportCsv} className="h-8 text-slate-300"><Download className="w-4 h-4 mr-1" />CSV</Button>
              <Button size="sm" variant="ghost" onClick={() => setCsv({ text: "", overwrite: false })} className="h-8 text-slate-300" data-testid="doc-csv-open"><Upload className="w-4 h-4 mr-1" />Importar CSV</Button>
              <Button size="sm" variant="ghost" onClick={() => setGform(g)} className="h-8 text-slate-400"><Pencil className="w-4 h-4" /></Button>
              <Button size="sm" variant="ghost" onClick={delGroup} className="h-8 text-slate-500 hover:text-red-300" data-testid="doc-group-del"><Trash2 className="w-4 h-4" /></Button>
            </div>
          </div>
          {csv && (
            <Card className="bg-surface border-line p-3 space-y-2" data-testid="doc-csv">
              <div className="text-xs text-slate-400">Cole o CSV com cabeçalho (ip; hostname; descrição; mac; dono; situação; vrf; observação — vírgula ou ponto e vírgula). Os IPs entram no prefixo do grupo que os contém.</div>
              <textarea value={csv.text} onChange={e => setCsv({ ...csv, text: e.target.value })} rows={6} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" data-testid="doc-csv-text" />
              <div className="flex items-center gap-3"><label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={csv.overwrite} onCheckedChange={v => setCsv({ ...csv, overwrite: v })} />Sobrescrever IPs que já existem no grupo</label>
                <Button size="sm" onClick={importCsv} disabled={!csv.text.trim()} className="h-7 bg-brand hover:bg-brand-strong text-onbrand ml-auto" data-testid="doc-csv-go">Importar</Button>
                <Button size="sm" variant="ghost" onClick={() => setCsv(null)} className="h-7 text-slate-500"><X className="w-4 h-4" /></Button></div>
            </Card>
          )}
          {sform && <SubnetForm value={sform} onSave={saveSub} onCancel={() => setSform(null)} />}
          <Card className="bg-surface border-line overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-slate-500"><tr><th className="text-left px-3 py-2">Prefixo</th><th className="text-left px-2">VLAN</th><th className="text-left px-2">Descrição</th><th className="text-left px-2 w-48">Documentados</th><th /></tr></thead>
              <tbody className="divide-y divide-line">
                {subs && subs.subnets.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-500">Nenhum prefixo neste grupo. Crie um ou importe do phpIPAM.</td></tr>}
                {(subs?.subnets || []).map(s => (
                  <tr key={s.id} className="hover:bg-white/[0.02]" data-testid={`doc-sub-${s.prefix}`}>
                    <td className="px-3 py-1.5"><button onClick={() => setView({ sub: s })} className="font-mono text-slate-100 hover:underline">{s.prefix}</button>{s.vrf && <span className="text-[10px] text-amber-300"> @{s.vrf}</span>}</td>
                    <td className="px-2 text-slate-400">{s.vlan || ""}</td>
                    <td className="px-2 text-xs text-slate-400">{s.description}{s.location ? ` · ${s.location}` : ""}</td>
                    <td className="px-2"><div className="flex items-center gap-2"><div className="h-1.5 flex-1 rounded bg-sunken overflow-hidden"><div className="h-full bg-brand" style={{ width: `${Math.min(100, s.pct)}%` }} /></div>
                      <span className="text-[11px] text-slate-500 w-20 text-right">{s.used}/{s.usable}</span></div></td>
                    <td className="px-2 pr-3 text-right whitespace-nowrap">
                      <button onClick={() => setSform(s)} className="text-slate-500 hover:text-slate-200 mr-2"><Pencil className="w-3.5 h-3.5" /></button>
                      <button onClick={() => delSub(s)} className="text-slate-500 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></button></td>
                  </tr>))}
              </tbody>
            </table>
          </Card>
        </div>))}
    </div>
  );
}

export function PhpIpamTab({ isAdmin, onImported }) {
  const [c, setC] = useState(null);
  const [secret, setSecret] = useState("");
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState("");
  const [groups, setGroups] = useState([]);
  const [map, setMap] = useState({});
  const [opts, setOpts] = useState({ addresses: true, overwrite: false });
  const [job, setJob] = useState(null);
  useEffect(() => {
    api.get("/ipam/phpipam").then(r => setC(r.data)).catch(e => toast.error(formatApiError(e)));
    api.get("/ipam/doc/groups").then(r => setGroups(r.data)).catch(() => {});
  }, []);
  useEffect(() => {
    if (!job || job.state !== "importando") return undefined;
    const t = setInterval(() => api.get(`/ipam/phpipam/import/${job.id}`).then(r => { setJob(r.data); if (r.data.state !== "importando") { onImported && onImported(); api.get("/ipam/doc/groups").then(x => setGroups(x.data)).catch(() => {}); } }).catch(() => {}), 1200);
    return () => clearInterval(t);
  }, [job, onImported]);
  if (!c) return null;
  const save = async () => {
    setBusy("save");
    try { const r = await api.put("/ipam/phpipam", { ...c, secret: secret || null }); setC(r.data); setSecret(""); toast.success("Salvo"); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const doTest = async () => {
    setBusy("test"); setTest(null);
    try {
      const r = (await api.post("/ipam/phpipam/test")).data; setTest(r);
      if (r.ok) setMap(Object.fromEntries(r.sections.map(s => [s.id, { target: groups.find(g => g.name === s.name)?.id || "new", name: s.name }])));
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const run = async () => {
    try {
      const mapping = Object.entries(map).map(([section_id, m]) => ({ section_id, ...m }));
      setJob((await api.post("/ipam/phpipam/import", { mapping, ...opts })).data);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <div className="space-y-4 max-w-5xl" data-testid="php-tab">
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="text-sm font-semibold text-slate-200 flex items-center gap-2"><BookOpen className="w-4 h-4 text-brand-soft" />Conexão com o phpIPAM</div>
        <p className="text-xs text-slate-500">No phpIPAM: Administração → API → criar app (permissão de leitura). Com "App code token", use o código do app como token; senão, usuário e senha.</p>
        <div className="grid gap-2 sm:grid-cols-[1fr_10rem_12rem] items-end">
          <div><label className={lbl}>URL</label><Input value={c.url} onChange={e => setC({ ...c, url: e.target.value })} placeholder="https://ipam.exemplo.net" disabled={!isAdmin} className="bg-sunken border-line h-9 font-mono" data-testid="php-url" /></div>
          <div><label className={lbl}>App ID</label><Input value={c.app_id} onChange={e => setC({ ...c, app_id: e.target.value })} placeholder="bastion" disabled={!isAdmin} className="bg-sunken border-line h-9 font-mono" data-testid="php-app" /></div>
          <div><label className={lbl}>Autenticação</label><select value={c.auth} onChange={e => setC({ ...c, auth: e.target.value })} disabled={!isAdmin} className={`${sel} w-full`} data-testid="php-auth">
            <option value="token">Token do app</option><option value="user">Usuário e senha</option></select></div>
          {c.auth === "user" && <div><label className={lbl}>Usuário</label><Input value={c.user} onChange={e => setC({ ...c, user: e.target.value })} disabled={!isAdmin} className="bg-sunken border-line h-9" /></div>}
          <div><label className={lbl}>{c.auth === "user" ? "Senha" : "Token"}{c.has_secret ? " (guardado)" : ""}</label><Input type="password" value={secret} onChange={e => setSecret(e.target.value)} placeholder={c.has_secret ? "vazio = manter" : ""} disabled={!isAdmin} autoComplete="new-password" className="bg-sunken border-line h-9" data-testid="php-secret" /></div>
          <label className="flex items-center gap-2 text-xs text-slate-300 pb-2"><Switch checked={c.verify_tls} onCheckedChange={v => setC({ ...c, verify_tls: v })} disabled={!isAdmin} />Conferir certificado</label>
        </div>
        {isAdmin && <div className="flex gap-2">
          <Button size="sm" onClick={save} disabled={!!busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="php-save">{busy === "save" ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
          <Button size="sm" variant="outline" onClick={doTest} disabled={!!busy || !c.url} className="border-line bg-transparent" data-testid="php-test">{busy === "test" ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <PlugZap className="w-4 h-4 mr-1" />}Conectar e ver seções</Button>
        </div>}
        {c.last_import && <div className="text-[11px] text-slate-500">Última importação: {new Date(c.last_import.at).toLocaleString("pt-BR")} por {c.last_import.by} — {c.last_import.subnets_created} prefixo(s), {c.last_import.ips_created} IP(s) novos.</div>}
        {test && !test.ok && <div className="text-sm text-red-300" data-testid="php-err"><AlertTriangle className="w-4 h-4 inline mr-1" />{test.error}</div>}
      </Card>
      {test?.ok && (
        <Card className="bg-surface border-line p-4 space-y-3" data-testid="php-sections">
          <div className="text-sm font-semibold text-slate-200">Seções do phpIPAM → grupos de documentação</div>
          <div className="divide-y divide-line">{test.sections.map(s => (
            <div key={s.id} className="flex flex-wrap items-center gap-3 py-2" data-testid={`php-sec-${s.id}`}>
              <div className="min-w-0 flex-1"><div className="text-sm text-slate-100">{s.name}</div><div className="text-[11px] text-slate-500 font-mono truncate">{s.subnets} sub-rede(s) · {s.sample.join(", ")}</div></div>
              <select value={map[s.id]?.target || "skip"} onChange={e => setMap({ ...map, [s.id]: { ...map[s.id], target: e.target.value } })} className={sel} data-testid={`php-sec-target-${s.id}`}>
                <option value="new">Grupo novo</option><option value="skip">Não importar</option>
                {groups.map(g => <option key={g.id} value={g.id}>Para: {g.name}</option>)}
              </select>
              {map[s.id]?.target === "new" && <Input value={map[s.id]?.name || ""} onChange={e => setMap({ ...map, [s.id]: { ...map[s.id], name: e.target.value } })} className="bg-sunken border-line h-9 w-48" />}
            </div>))}</div>
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={opts.addresses} onCheckedChange={v => setOpts({ ...opts, addresses: v })} />Trazer também os IPs (hostname, descrição, MAC, dono, situação)</label>
            <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={opts.overwrite} onCheckedChange={v => setOpts({ ...opts, overwrite: v })} data-testid="php-overwrite" />Sobrescrever o que já está documentado no BastiON</label>
            <Button size="sm" onClick={run} disabled={job?.state === "importando"} className="ml-auto bg-brand hover:bg-brand-strong text-onbrand" data-testid="php-run">Importar</Button>
          </div>
        </Card>
      )}
      {job && (
        <Card className="bg-surface border-line p-4 text-sm space-y-1" data-testid="php-job">
          <div className="flex items-center gap-2">{job.state === "importando" ? <Loader2 className="w-4 h-4 animate-spin text-slate-400" /> : job.state === "pronto" ? <CheckCircle2 className="w-4 h-4 text-emerald-300" /> : <AlertTriangle className="w-4 h-4 text-red-300" />}
            <span className="text-slate-200">{job.state === "importando" ? `Importando ${job.section}… ${job.subnets_done}/${job.subnets_total} sub-redes` : job.state === "pronto" ? "Importação concluída" : "Falhou"}</span></div>
          <div className="text-xs text-slate-400">{job.subnets_created} prefixo(s) novos ({job.subnets_kept} já existiam) · {job.ips_created} IP(s) novos · {job.ips_updated} atualizados · {job.ips_kept} mantidos · grupos: {job.groups.join(", ")}</div>
          {job.error && <div className="text-xs text-red-300">{job.error}</div>}
          {job.errors?.length > 0 && <div className="text-[11px] text-amber-300">{job.errors.slice(0, 5).join(" · ")}</div>}
        </Card>
      )}
    </div>
  );
}
