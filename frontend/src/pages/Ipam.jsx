import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Network, Plus, Pencil, Trash2, Loader2, Search, ArrowLeft, RefreshCw, AlertTriangle, Download, Database, Lock } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const STATUS = { active: "ativo", reserved: "reservado", container: "agregado", deprecated: "desativado" };
const usageCls = (p) => (p >= 90 ? "bg-red-500" : p >= 70 ? "bg-amber-400" : "bg-brand");
const csvDl = (name, rows) => {
  const text = "﻿" + rows.map(r => r.map(x => `"${String(x ?? "").replace(/"/g, '""')}"`).join(";")).join("\r\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" })); a.download = name; a.click();
};

function PrefixDialog({ p, onClose, onSaved }) {
  const [f, setF] = useState({ prefix: "", description: "", vrf: "", vlan: "", status: "active", tags: [], ...(p || {}) });
  const save = async () => {
    try {
      const body = { ...f, vlan: f.vlan ? Number(f.vlan) : null, tags: typeof f.tags === "string" ? f.tags.split(",") : f.tags };
      if (p?.id) await api.put(`/ipam/prefixes/${p.id}`, body); else await api.post("/ipam/prefixes", body);
      toast.success("Prefixo salvo"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const lbl = "text-xs text-slate-400 mb-1 block";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg" data-testid="ipam-prefix-dialog">
        <DialogHeader><DialogTitle>{p?.id ? "Editar prefixo" : "Novo prefixo"}</DialogTitle></DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2"><label className={lbl}>Prefixo</label><Input value={f.prefix} onChange={e => setF({ ...f, prefix: e.target.value })} placeholder="203.0.113.0/24 ou 2001:db8::/32" className="bg-sunken border-line font-mono" data-testid="ipam-prefix" /></div>
          <div className="col-span-2"><label className={lbl}>Descrição</label><Input value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="Clientes FTTH POP Centro, PTPs do backbone…" className="bg-sunken border-line" /></div>
          <div><label className={lbl}>VRF (opcional)</label><Input value={f.vrf} onChange={e => setF({ ...f, vrf: e.target.value })} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>VLAN (opcional)</label><Input type="number" value={f.vlan || ""} onChange={e => setF({ ...f, vlan: e.target.value })} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Situação</label><select value={f.status} onChange={e => setF({ ...f, status: e.target.value })} className={`${sel} w-full`}>{Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        </div>
        <DialogFooter><Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button><Button onClick={save} disabled={!f.prefix} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="ipam-prefix-save">Salvar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PrefixDetail({ prefix, vrf, onBack }) {
  const [d, setD] = useState(null);
  const [want, setWant] = useState(0);
  const [res, setRes] = useState({ ip: "", description: "" });
  const load = useCallback(() => api.get("/ipam/prefix", { params: { prefix, vrf, want } }).then(r => setD(r.data)).catch(e => toast.error(formatApiError(e))), [prefix, vrf, want]);
  useEffect(() => { load(); }, [load]);
  const reserve = async () => { try { await api.post("/ipam/manual", { ...res, vrf }); setRes({ ip: "", description: "" }); toast.success("IP reservado"); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const unreserve = async (a) => { try { await api.delete(`/ipam/manual/${a.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const v6 = prefix.includes(":");
  const lens = v6 ? [64, 56, 48, 127] : [30, 29, 28, 27, 24, 31];
  return (
    <div data-testid="ipam-detail">
      <button onClick={onBack} className="text-sm text-slate-400 hover:text-slate-200 flex items-center gap-1 mb-3"><ArrowLeft className="w-4 h-4" /> Prefixos</button>
      {!d ? <Loader2 className="w-5 h-5 animate-spin text-slate-500" /> : <>
        <Card className="bg-surface border-line p-4 mb-3">
          <div className="flex flex-wrap items-center gap-4">
            <span className="font-mono text-lg text-slate-100">{d.prefix}</span>{vrf && <span className="text-xs text-slate-400">VRF {vrf}</span>}
            <div className="flex items-center gap-2"><div className="w-40 h-2 rounded bg-white/5 overflow-hidden"><div className={`h-full ${usageCls(d.pct)}`} style={{ width: `${Math.min(100, d.pct)}%` }} /></div><span className="text-sm text-slate-300">{d.used} de {d.usable} ({d.pct}%)</span></div>
            {d.children.length > 0 && <span className="text-xs text-slate-400">{d.children.length} sub-prefixo(s)</span>}
          </div>
        </Card>
        <div className="grid lg:grid-cols-[1fr_20rem] gap-3">
          <Card className="bg-surface border-line overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">IP</th><th className="text-left px-2">Equipamento</th><th className="text-left px-2">Interface</th><th className="text-left px-2">Descrição</th><th /></tr></thead>
              <tbody className="divide-y divide-line">
                {d.addresses.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-500">Nenhum IP em uso nas configurações.</td></tr>}
                {d.addresses.map((a, i) => (
                  <tr key={i}>
                    <td className="px-3 py-1.5 font-mono text-slate-200">{a.ip}<span className="text-slate-500">/{a.plen}</span></td>
                    <td className="px-2 text-slate-300">{a.source === "manual" ? <span className="text-amber-300 flex items-center gap-1"><Lock className="w-3 h-3" /> reservado</span> : a.device_name}</td>
                    <td className="px-2 font-mono text-xs text-slate-400">{a.interface}{a.vrf ? ` · ${a.vrf}` : ""}</td>
                    <td className="px-2 text-xs text-slate-400 truncate max-w-[16rem]">{a.description}</td>
                    <td className="px-2 text-right">{a.source === "manual" && <button onClick={() => unreserve(a)} className="text-slate-500 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <div className="space-y-3">
            <Card className="bg-surface border-line p-3">
              <div className="text-sm text-slate-200 mb-2">Blocos livres</div>
              <div className="flex flex-wrap gap-1 mb-2">{[0, ...lens].map(l => <button key={l} onClick={() => setWant(l)} className={`px-2 h-6 rounded text-xs border ${want === l ? "border-brand bg-brand/15 text-slate-100" : "border-line text-slate-400"}`} data-testid={`ipam-want-${l}`}>{l ? `/${l}` : "todos"}</button>)}</div>
              <div className="space-y-0.5 max-h-64 overflow-y-auto" data-testid="ipam-free">{d.free.length === 0 ? <div className="text-xs text-slate-500">Nada livre desse tamanho.</div> : d.free.map(f => <div key={f} className="font-mono text-xs text-emerald-300">{f}</div>)}</div>
            </Card>
            <Card className="bg-surface border-line p-3">
              <div className="text-sm text-slate-200 mb-2">Reservar IP</div>
              <Input value={res.ip} onChange={e => setRes({ ...res, ip: e.target.value })} placeholder="IP" className="bg-sunken border-line font-mono h-8 mb-1.5" data-testid="ipam-res-ip" />
              <Input value={res.description} onChange={e => setRes({ ...res, description: e.target.value })} placeholder="para quê / cliente" className="bg-sunken border-line h-8 mb-1.5" />
              <Button size="sm" onClick={reserve} disabled={!res.ip} className="bg-brand hover:bg-brand-strong text-onbrand h-8" data-testid="ipam-res-save">Reservar</Button>
            </Card>
          </div>
        </div>
      </>}
    </div>
  );
}

function Prefixes({ onOpen }) {
  const [data, setData] = useState(null);
  const [edit, setEdit] = useState(null);
  const [q, setQ] = useState("");
  const load = useCallback(() => api.get("/ipam/prefixes").then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const del = async (p) => { if (!window.confirm(`Apagar o prefixo ${p.prefix}?`)) return; try { await api.delete(`/ipam/prefixes/${p.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const list = (data?.prefixes || []).filter(p => !q || `${p.prefix} ${p.description} ${p.vrf}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="filtrar prefixos…" className="bg-sunken border-line h-9 max-w-xs" />
        {data && <span className="text-xs text-slate-400">{data.addresses} IPs em uso nas configurações{data.uncovered ? <span className="text-amber-300"> · {data.uncovered} rede(s) sem prefixo</span> : ""}{data.invalid ? <span className="text-slate-500" title="Prefixos com valor ilegível no banco; ficaram fora da lista"> · {data.invalid} prefixo(s) inválido(s) ignorado(s)</span> : ""}</span>}
        <Button size="sm" onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand ml-auto" data-testid="ipam-new"><Plus className="w-4 h-4 mr-1" /> Prefixo</Button>
      </div>
      <Card className="bg-surface border-line overflow-x-auto">
        {!data ? <div className="p-6 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : (
          <table className="w-full text-sm" data-testid="ipam-prefixes">
            <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">Prefixo</th><th className="text-left px-2">Descrição</th><th className="text-left px-2">Uso</th><th className="text-left px-2">Situação</th><th /></tr></thead>
            <tbody className="divide-y divide-line">
              {list.length === 0 && <tr><td colSpan={5} className="py-8 text-center text-slate-500">Nenhum prefixo. Cadastre seus blocos ou importe as redes vistas em Problemas.</td></tr>}
              {list.map(p => (
                <tr key={p.id} className="hover:bg-white/[0.03] cursor-pointer" onClick={() => onOpen(p)} data-testid={`ipam-row-${p.prefix}`}>
                  <td className="px-3 py-1.5 font-mono text-slate-100" style={{ paddingLeft: `${0.75 + Math.min(p.depth, 6) * 1.1}rem` }}>{p.prefix}{p.vrf && <span className="text-[10px] text-slate-500 ml-1">{p.vrf}</span>}</td>
                  <td className="px-2 text-slate-300 truncate max-w-[20rem]">{p.description}{p.vlan ? <span className="text-xs text-slate-500"> · VLAN {p.vlan}</span> : ""}</td>
                  <td className="px-2"><div className="flex items-center gap-2"><div className="w-24 h-1.5 rounded bg-white/5 overflow-hidden"><div className={`h-full ${usageCls(p.pct)}`} style={{ width: `${Math.min(100, p.pct)}%` }} /></div><span className="text-xs text-slate-400">{p.used}/{p.usable > 100000 ? "∞" : p.usable}</span></div></td>
                  <td className="px-2 text-xs text-slate-400">{STATUS[p.status] || p.status}</td>
                  <td className="px-2 text-right whitespace-nowrap" onClick={e => e.stopPropagation()}>
                    <Button size="sm" variant="ghost" onClick={() => setEdit(p)} className="h-7 text-slate-300"><Pencil className="w-4 h-4" /></Button>
                    <Button size="sm" variant="ghost" onClick={() => del(p)} className="h-7 text-red-300"><Trash2 className="w-4 h-4" /></Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {edit && <PrefixDialog p={edit.id ? edit : null} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </>
  );
}

function SearchTab() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return undefined; }
    const t = setTimeout(() => api.get("/ipam/search", { params: { q } }).then(r => setRes(r.data)).catch(e => toast.error(formatApiError(e))), 350);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <>
      <div className="relative mb-3"><Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="IP, rede, equipamento, interface ou descrição" className="pl-9 bg-sunken border-line h-10" data-testid="ipam-search" /></div>
      {res && <Card className="bg-surface border-line overflow-x-auto"><table className="w-full text-sm" data-testid="ipam-search-res">
        <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">IP</th><th className="text-left px-2">Equipamento</th><th className="text-left px-2">Interface</th><th className="text-left px-2">Descrição</th><th className="text-left px-2">Prefixo</th></tr></thead>
        <tbody className="divide-y divide-line">
          {res.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-500">Nada encontrado.</td></tr>}
          {res.map((a, i) => <tr key={i}><td className="px-3 py-1.5 font-mono text-slate-200">{a.ip}/{a.plen}</td><td className="px-2 text-slate-300">{a.device_name}</td><td className="px-2 font-mono text-xs text-slate-400">{a.interface}{a.vrf ? ` · ${a.vrf}` : ""}</td><td className="px-2 text-xs text-slate-400">{a.description}</td><td className="px-2 font-mono text-xs text-slate-400">{a.prefix || <span className="text-amber-300">sem prefixo</span>}</td></tr>)}
        </tbody></table></Card>}
    </>
  );
}

function Issues() {
  const [d, setD] = useState(null);
  const [pick, setPick] = useState({});
  const load = useCallback(() => api.get("/ipam/issues").then(r => setD(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const imp = async () => {
    const nets = Object.keys(pick).filter(k => pick[k]);
    try { const r = await api.post("/ipam/import", { networks: nets }); toast.success(`${r.data.added} prefixo(s) criado(s)`); setPick({}); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  if (!d) return <Loader2 className="w-5 h-5 animate-spin text-slate-500" />;
  return (
    <div className="grid gap-3">
      <Card className="bg-surface border-line p-3 text-sm text-slate-400" data-testid="ipam-dups-moved">
        IP duplicado agora tem aba própria, separada por grupo de equipamentos: <b className="text-slate-200">IP duplicado</b>. Aqui ficam as redes das configurações que ainda não têm prefixo cadastrado.
      </Card>
      <Card className="bg-surface border-line p-4" data-testid="ipam-uncovered">
        <div className="flex items-center gap-2 mb-2"><span className="text-slate-100 font-medium">Redes nas configurações sem prefixo cadastrado ({d.uncovered.length})</span>
          <Button size="sm" onClick={imp} disabled={!Object.values(pick).some(Boolean)} className="bg-brand hover:bg-brand-strong text-onbrand ml-auto h-8" data-testid="ipam-import">Criar prefixos marcados</Button></div>
        <div className="max-h-96 overflow-y-auto divide-y divide-line">
          {d.uncovered.map(u => (
            <label key={u.network} className="flex items-center gap-2 py-1 text-sm">
              <input type="checkbox" className="accent-brand" checked={!!pick[u.network]} onChange={e => setPick(p => ({ ...p, [u.network]: e.target.checked }))} />
              <span className="font-mono text-slate-200 w-44">{u.network}</span><span className="text-xs text-slate-400 truncate">{u.devices.join(", ")}{u.vrf ? ` · VRF ${u.vrf}` : ""}</span>
            </label>
          ))}
        </div>
      </Card>
    </div>
  );
}

const NONE = "__none__";
const gname = (t) => (t === NONE ? "sem grupo" : t);
const LS = "ipam_dup_prefs";

function Duplicates() {
  const saved = (() => { try { return JSON.parse(localStorage.getItem(LS) || "{}"); } catch { return {}; } })();
  const [tags, setTags] = useState(saved.tags || []);
  const [mode, setMode] = useState(saved.mode || "per_group");
  const [mgmt, setMgmt] = useState(saved.mgmt ?? true);
  const [same, setSame] = useState(saved.same ?? true);
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [closed, setClosed] = useState({});
  const load = useCallback(() => {
    setBusy(true);
    api.get("/ipam/duplicates", { params: { tags: tags.join(","), mode, ignore_mgmt: mgmt, same_name: same } })
      .then(r => setD(r.data)).catch(e => toast.error(formatApiError(e))).finally(() => setBusy(false));
  }, [tags, mode, mgmt, same]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { try { localStorage.setItem(LS, JSON.stringify({ tags, mode, mgmt, same })); } catch { /* sem storage */ } }, [tags, mode, mgmt, same]);
  const toggle = (t) => setTags(tags.includes(t) ? tags.filter(x => x !== t) : [...tags, t]);
  const list = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (d?.duplicates || []).filter(x => !ql || x.ip.includes(ql) || x.where.some(w => `${w.device_name} ${w.interface}`.toLowerCase().includes(ql)));
  }, [d, q]);
  const grouped = useMemo(() => {
    const m = new Map();
    for (const x of list) { const k = x.group || "__all__"; if (!m.has(k)) m.set(k, []); m.get(k).push(x); }
    return [...m.entries()];
  }, [list]);
  const csv = () => csvDl("ip-duplicado.csv", [["grupo", "ip", "vrf", "equipamentos", "onde"],
    ...list.map(x => [x.group ? gname(x.group) : "(grupos juntos)", x.ip, x.vrf, x.devices, x.where.map(w => `${w.device_name} (${w.interface})`).join("; ")])]);
  return (
    <div className="grid gap-3" data-testid="ipam-dup">
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-slate-200 font-medium">Grupos de equipamentos</span>
          <span className="text-xs text-slate-500">{tags.length ? `${tags.length} marcado(s)` : "nenhum marcado = todos"}</span>
          {tags.length > 0 && <button onClick={() => setTags([])} className="text-xs text-brand-soft hover:underline">limpar</button>}
        </div>
        <div className="flex flex-wrap gap-1.5" data-testid="ipam-dup-groups">
          {(d?.groups || []).map(g => {
            const on = tags.includes(g.tag), n = d?.by_group?.[g.tag];
            return (
              <button key={g.tag} onClick={() => toggle(g.tag)} data-testid={`ipam-dup-g-${g.tag}`}
                className={`text-xs px-2 py-1 rounded border ${on ? "border-brand/60 bg-brand/10 text-brand-soft" : "border-line text-slate-400 hover:text-slate-200"}`}>
                {gname(g.tag)} <span className="text-slate-500">· {g.devices}</span>{mode === "per_group" && n ? <span className="text-red-300"> · {n} dup</span> : null}
              </button>);
          })}
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs">
            {[["per_group", "Comparar dentro de cada grupo"], ["together", "Juntar os grupos marcados"]].map(([k, l]) => (
              <button key={k} onClick={() => setMode(k)} className={`px-2.5 h-7 rounded ${mode === k ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`} data-testid={`ipam-dup-mode-${k}`}>{l}</button>))}
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={mgmt} onCheckedChange={setMgmt} data-testid="ipam-dup-mgmt" />Ignorar portas de gerência (MEth, mgmt, fxp0…)</label>
          <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={same} onCheckedChange={setSame} data-testid="ipam-dup-same" />Mesmo nome = mesmo equipamento</label>
        </div>
        <p className="text-[11px] text-slate-500">"Dentro de cada grupo": um IP só conta como duplicado se aparecer em dois equipamentos do mesmo grupo — redes separadas (outra VRF/cliente/cidade) não se misturam. A VRF também separa.</p>
      </Card>
      <Card className="bg-surface border-line">
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
          <AlertTriangle className="w-4 h-4 text-red-300" /><span className="text-slate-100 font-medium" data-testid="ipam-dup-total">IP duplicado ({d ? d.total : "…"})</span>
          {busy && <Loader2 className="w-4 h-4 animate-spin text-slate-500" />}
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="filtrar IP ou equipamento" className="bg-sunken border-line h-8 w-60 ml-auto" />
          <Button size="sm" variant="ghost" onClick={csv} disabled={!list.length} className="text-slate-300 h-8"><Download className="w-4 h-4 mr-1" />CSV</Button>
        </div>
        {d && list.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nenhum IP duplicado com esse filtro.</div>}
        {grouped.map(([g, xs]) => (
          <div key={g} data-testid={`ipam-dup-sec-${g}`}>
            <button onClick={() => setClosed({ ...closed, [g]: !closed[g] })} className="w-full flex items-center gap-2 px-4 py-1.5 bg-white/[0.02] border-b border-line text-xs text-slate-300">
              <span>{closed[g] ? "▸" : "▾"}</span><span className="font-medium">{g === "__all__" ? "Grupos juntos" : gname(g)}</span><span className="text-slate-500">{xs.length}</span>
            </button>
            {!closed[g] && xs.map(x => (
              <div key={`${g}-${x.ip}-${x.vrf}`} className="text-sm px-4 py-1.5 border-b border-line last:border-0">
                <span className="font-mono text-red-300">{x.ip}</span>{x.vrf && <span className="text-xs text-amber-300"> @{x.vrf}</span>} <span className="text-slate-400">em</span>{" "}
                {x.where.map((w, i) => <span key={i} className="text-slate-200">{i ? ", " : ""}{w.device_name} <span className="text-slate-500">({w.interface})</span></span>)}
              </div>))}
          </div>))}
      </Card>
    </div>
  );
}

function Inventory() {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => { api.get("/ipam/inventory").then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))); }, []);
  const list = (rows || []).filter(d => !q || `${d.name} ${d.host} ${d.device_type} ${d.model} ${d.firmware} ${(d.tags || []).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  const csv = () => csvDl("inventario.csv", [["nome", "ip", "fabricante", "modelo", "firmware", "grupos", "agente", "status", "ips_configurados", "ultimo_backup"],
    ...list.map(d => [d.name, d.host, d.device_type, d.model, d.firmware, (d.tags || []).join(", "), d.agent, d.status, d.addresses, d.last_backup])]);
  return (
    <>
      <div className="flex gap-2 mb-3"><Input value={q} onChange={e => setQ(e.target.value)} placeholder="filtrar…" className="bg-sunken border-line h-9 max-w-xs" />
        <Button size="sm" variant="ghost" onClick={csv} disabled={!rows} className="text-slate-300 ml-auto" data-testid="ipam-inv-csv"><Download className="w-4 h-4 mr-1" /> CSV</Button></div>
      <Card className="bg-surface border-line overflow-x-auto">
        {!rows ? <div className="p-6 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : (
          <table className="w-full text-sm" data-testid="ipam-inventory">
            <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">Equipamento</th><th className="text-left px-2">IP</th><th className="text-left px-2">Fabricante / modelo</th><th className="text-left px-2">Firmware</th><th className="text-left px-2">Grupos</th><th className="text-left px-2">Via</th><th className="text-right px-2">IPs</th><th className="text-left px-2">Backup</th></tr></thead>
            <tbody className="divide-y divide-line">
              {list.map(d => <tr key={d.id}><td className="px-3 py-1.5 text-slate-100">{d.name}</td><td className="px-2 font-mono text-xs text-slate-400">{d.host}</td><td className="px-2 text-xs text-slate-300">{d.device_type}{d.model ? ` · ${d.model}` : ""}</td>
                <td className="px-2 font-mono text-xs text-slate-300">{d.firmware || "—"}</td><td className="px-2 text-xs text-slate-400">{(d.tags || []).join(", ")}</td><td className="px-2 text-xs text-slate-400">{d.agent || "direto"}</td>
                <td className="px-2 text-right text-slate-300">{d.addresses}</td><td className="px-2 text-xs text-slate-400">{d.last_backup ? new Date(d.last_backup).toLocaleDateString("pt-BR") : <span className="text-amber-300">sem backup</span>}</td></tr>)}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function NetboxTab() {
  const [c, setC] = useState(null);
  const [tok, setTok] = useState("");
  const [res, setRes] = useState(null);
  const [opts, setOpts] = useState({ push_prefixes: true, push_ips: true, pull_prefixes: false });
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/ipam/netbox").then(r => setC(r.data)).catch(e => toast.error(formatApiError(e))); }, []);
  const save = async () => { try { setC((await api.put("/ipam/netbox", { url: c.url, verify_tls: c.verify_tls, ...(tok ? { token: tok } : {}) })).data); setTok(""); toast.success("Salvo"); } catch (e) { toast.error(formatApiError(e)); } };
  const test = async () => { try { const r = await api.post("/ipam/netbox/test"); r.data.ok ? toast.success(`NetBox ${r.data.version} respondeu`) : toast.error(r.data.error); } catch (e) { toast.error(formatApiError(e)); } };
  const sync = async (dry) => {
    if (!dry && !window.confirm("Criar no NetBox os prefixos/IPs que faltam? Nada é apagado.")) return;
    setBusy(true);
    try { setRes((await api.post("/ipam/netbox/sync", { ...opts, dry_run: dry })).data); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  if (!c) return <Loader2 className="w-5 h-5 animate-spin text-slate-500" />;
  const lbl = "text-xs text-slate-400 mb-1 block";
  return (
    <div className="grid gap-3 max-w-3xl" data-testid="ipam-netbox">
      <Card className="bg-surface border-line p-4">
        <div className="text-slate-100 font-medium mb-1 flex items-center gap-1.5"><Database className="w-4 h-4" /> NetBox</div>
        <p className="text-xs text-slate-500 mb-3">Leva para o NetBox os prefixos do BastiON e os IPs das interfaces (com equipamento e interface na descrição) e, se quiser, traz os prefixos que só existem lá. Só cria; nunca apaga. Token de API com permissão em IPAM.</p>
        <div className="grid sm:grid-cols-2 gap-3">
          <div className="sm:col-span-2"><label className={lbl}>URL</label><Input value={c.url} onChange={e => setC({ ...c, url: e.target.value })} placeholder="https://netbox.exemplo.net" className="bg-sunken border-line" data-testid="nb-url" /></div>
          <div><label className={lbl}>Token {c.has_token && "(já salvo — preencha só para trocar)"}</label><Input type="password" value={tok} onChange={e => setTok(e.target.value)} className="bg-sunken border-line" data-testid="nb-token" /></div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={c.verify_tls} onCheckedChange={v => setC({ ...c, verify_tls: v })} /> Verificar certificado</label>
        </div>
        <div className="flex gap-2 mt-3"><Button onClick={save} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="nb-save">Salvar</Button><Button variant="ghost" onClick={test} className="text-slate-300" data-testid="nb-test">Testar</Button></div>
      </Card>
      <Card className="bg-surface border-line p-4">
        <div className="flex flex-wrap gap-4 mb-3">
          {[["push_prefixes", "Enviar prefixos"], ["push_ips", "Enviar IPs das interfaces"], ["pull_prefixes", "Trazer prefixos do NetBox"]].map(([k, l]) => (
            <label key={k} className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" className="accent-brand" checked={opts[k]} onChange={e => setOpts({ ...opts, [k]: e.target.checked })} /> {l}</label>))}
        </div>
        <div className="flex gap-2"><Button variant="ghost" onClick={() => sync(true)} disabled={busy} className="text-slate-200" data-testid="nb-preview">{busy ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}Comparar</Button>
          <Button onClick={() => sync(false)} disabled={busy || !res} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="nb-apply">Sincronizar</Button></div>
        {res && <div className="mt-3 text-sm text-slate-300 space-y-1" data-testid="nb-result">
          <div>Faltam no NetBox: <b>{res.to_create_prefixes}</b> prefixo(s) e <b>{res.to_create_ips}</b> IP(s) · só no NetBox: <b>{res.only_netbox}</b> prefixo(s)</div>
          {!res.dry_run && <div className="text-emerald-300">Criados {res.created_prefixes} prefixo(s) e {res.created_ips} IP(s); trazidos {res.imported}.</div>}
          {res.errors?.map((e, i) => <div key={i} className="text-red-300 text-xs">{e}</div>)}
          {res.dry_run && res.sample_prefixes?.length > 0 && <div className="text-xs text-slate-500 font-mono">ex.: {res.sample_prefixes.join(", ")}</div>}
        </div>}
        {c.last_sync && <div className="text-[11px] text-slate-500 mt-2">Última sincronização {new Date(c.last_sync.at).toLocaleString("pt-BR")}</div>}
      </Card>
    </div>
  );
}

export default function Ipam() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  const [open, setOpen] = useState(null);
  const tabs = [["", "Prefixos"], ["buscar", "Buscar IP"], ["duplicados", "IP duplicado"], ["problemas", "Sem prefixo"], ["inventario", "Inventário"], ...(isAdmin ? [["netbox", "NetBox"]] : [])];
  const refresh = async () => { try { await api.post("/ipam/refresh"); toast.success("Relendo os backups"); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="ipam-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Network className="w-6 h-6 text-brand-soft" /> Inventário e IPAM</h1>
          <p className="text-sm text-slate-400 mt-1">IPs em uso lidos das configurações (backups), prefixos com uso e blocos livres, IP duplicado e integração com o NetBox.</p>
        </div>
        <div className="flex gap-2 items-center">
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm overflow-x-auto max-w-full">
            {tabs.map(([k, l]) => <button key={k} onClick={() => { setOpen(null); setSp(k ? { tab: k } : {}); }} className={`px-3 h-7 rounded whitespace-nowrap ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`ipam-tab-${k || "prefixos"}`}>{l}</button>)}
          </div>
          <Button size="sm" variant="ghost" onClick={refresh} className="text-slate-300" title="Reler os backups"><RefreshCw className="w-4 h-4" /></Button>
        </div>
      </div>
      {tab === "" && (open ? <PrefixDetail prefix={open.prefix} vrf={open.vrf || ""} onBack={() => setOpen(null)} /> : <Prefixes onOpen={setOpen} />)}
      {tab === "buscar" && <SearchTab />}
      {tab === "duplicados" && <Duplicates />}
      {tab === "problemas" && <Issues />}
      {tab === "inventario" && <Inventory />}
      {tab === "netbox" && isAdmin && <NetboxTab />}
    </div>
  );
}
