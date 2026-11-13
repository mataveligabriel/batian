import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { BookMarked, Copy, Download, FileCode2, Import, Layers, Loader2, Pencil, Plus, Save, Tags, Trash2, Users, X } from "lucide-react";

const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100 min-w-0";
const inp = "bg-sunken border-line h-9";
const sm = "bg-sunken border-line h-8 text-xs";
const tabBtn = (on) => `flex items-center gap-2 px-4 py-2 text-sm -mb-px border-b-2 whitespace-nowrap ${on ? "border-brand text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`;
const KIND_CLS = { upstream: "border-sky-600/60 text-sky-300", downstream: "border-emerald-600/60 text-emerald-300", ix: "border-violet-600/60 text-violet-300",
  cdn: "border-amber-600/60 text-amber-300", ibgp: "border-slate-500 text-slate-300" };
const STATE_CLS = (s) => (s === "established" ? "text-emerald-300" : s ? "text-red-300" : "text-slate-500");
const download = (text, name, type = "text/plain") => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
const copy = async (t, msg = "Copiado") => { try { await navigator.clipboard.writeText(t); toast.success(msg); } catch { toast.error("Não consegui copiar"); } };
const STORE = "bastion_bgpdoc_group";

function Field({ label, children, hint, className = "" }) {
  return <label className={`block text-xs text-slate-400 space-y-1 ${className}`}>{label}{children}{hint && <span className="block text-[10px] text-slate-500">{hint}</span>}</label>;
}

function GroupPicker({ groups, value, onChange, onNew, canEdit }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={value || ""} onChange={e => onChange(e.target.value)} className={`${sel} w-64`} aria-label="Grupo" data-testid="bd-group">
        {!groups.length && <option value="">nenhum grupo ainda</option>}
        {groups.map(g => <option key={g.id} value={g.id}>{g.name}{g.asn ? ` · AS${g.asn}` : ""}</option>)}
      </select>
      {canEdit && <Button size="sm" variant="outline" onClick={onNew} className="h-9 border-line bg-transparent" data-testid="bd-new-group"><Plus className="w-4 h-4 mr-1" />Novo grupo</Button>}
    </div>
  );
}

// ---------------------------------------------------------------- visão geral
function Overview({ groups, meta, open }) {
  const md = async (g) => {
    try { const { data } = await api.get(`/bgpdoc/groups/${g.id}/markdown`); download(data.markdown, data.filename, "text/markdown"); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  if (!groups.length) {
    return <div className="text-sm text-slate-400 p-6 border border-dashed border-line rounded max-w-3xl">
      Comece criando um grupo (o grupo/empresa ou o backbone, com o ASN dele). Depois cadastre as communities do grupo, documente os prefixos
      (prefixo + ASN + community) e os pares BGP — upstreams, downstreams, IX e CDN. A aba Config padrão gera a configuração a partir disso.
    </div>;
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3" data-testid="bd-overview">
      {groups.map(g => (
        <div key={g.id} className="rounded-lg border border-line bg-surface p-4 space-y-2">
          <div className="flex items-baseline gap-2">
            <button onClick={() => open(g.id, "comm")} className="text-base font-semibold text-slate-100 hover:text-brand-soft text-left">{g.name}</button>
            {g.asn && <span className="font-mono text-xs text-brand-soft">AS{g.asn}</span>}
            {g.tag && <span className="text-[10px] px-1.5 rounded border border-line text-slate-400">tag {g.tag}</span>}
          </div>
          {g.description && <p className="text-xs text-slate-400 line-clamp-2">{g.description}</p>}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <button onClick={() => open(g.id, "comm")} className="rounded-md bg-sunken px-2 py-1.5 text-left hover:ring-1 hover:ring-brand/50"><div className="text-[10px] text-slate-500">Communities</div><div className="font-mono text-slate-100 text-base">{g.n_communities}</div></button>
            <button onClick={() => open(g.id, "comm")} className="rounded-md bg-sunken px-2 py-1.5 text-left hover:ring-1 hover:ring-brand/50"><div className="text-[10px] text-slate-500">Prefixos documentados</div><div className="font-mono text-slate-100 text-base">{g.n_prefixes}</div></button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(meta?.kinds || {}).map(([k, l]) => (g.peers?.[k] ? (
              <button key={k} onClick={() => open(g.id, "peers", k)} className={`text-[11px] px-1.5 py-0.5 rounded border ${KIND_CLS[k]}`}>{l.split(" (")[0]}: {g.peers[k]}</button>
            ) : null))}
            {!Object.keys(g.peers || {}).length && <span className="text-[11px] text-slate-500">nenhum par BGP documentado</span>}
          </div>
          <div className="flex gap-3 pt-1 text-[11px]">
            <button onClick={() => open(g.id, "gen")} className="text-brand-soft hover:underline inline-flex items-center gap-1"><FileCode2 className="w-3 h-3" />config padrão</button>
            <button onClick={() => md(g)} className="text-brand-soft hover:underline inline-flex items-center gap-1" data-testid={`bd-md-${g.id}`}><Download className="w-3 h-3" />documentação (.md)</button>
            <span className="ml-auto text-slate-500">atualizado {g.updated_at ? new Date(g.updated_at).toLocaleDateString("pt-BR") : "—"}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- grupo: dados + communities + prefixos
function GroupEditor({ meta, group, onSaved, onDeleted, canEdit, isNew, onCancel }) {
  const [f, setF] = useState(() => ({ name: group?.name || "", asn: group?.asn ? String(group.asn) : "", tag: group?.tag || "", description: group?.description || "",
    communities: (group?.communities || []).map(c => ({ ...c })) }));
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setF({ name: group?.name || "", asn: group?.asn ? String(group.asn) : "", tag: group?.tag || "", description: group?.description || "", communities: (group?.communities || []).map(c => ({ ...c })) });
  }, [group?.id, group?.updated_at]); // eslint-disable-line react-hooks/exhaustive-deps
  const setC = (i, p) => setF(s => ({ ...s, communities: s.communities.map((c, j) => (j === i ? { ...c, ...p } : c)) }));
  const addC = () => setF(s => ({ ...s, communities: [...s.communities, { value: s.asn ? `${s.asn}:` : "", name: "", role: "custom", target: "", description: "" }] }));
  const usage = useMemo(() => { const u = {}; (group?.prefixes || []).forEach(e => (e.communities || []).forEach(c => { u[c] = (u[c] || 0) + 1; })); return u; }, [group]);
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...f, communities: f.communities.filter(c => String(c.value).trim()) };
      const { data } = isNew ? await api.post("/bgpdoc/groups", body) : await api.put(`/bgpdoc/groups/${group.id}`, body);
      toast.success("Grupo salvo"); onSaved(data);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const del = async () => {
    if (!window.confirm(`Apagar o grupo ${group.name} com as communities e os prefixos documentados?`)) return;
    try { await api.delete(`/bgpdoc/groups/${group.id}`); onDeleted(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const ro = !canEdit;
  return (
    <div className="rounded-lg border border-line bg-surface p-4 space-y-4" data-testid="bd-group-editor">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <Field label="Nome do grupo"><Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} disabled={ro} className={inp} placeholder="NOME DO GRUPO" /></Field>
        <Field label="ASN do grupo"><Input value={f.asn} onChange={e => setF({ ...f, asn: e.target.value.replace(/[^\d]/g, "") })} disabled={ro} className={`${inp} font-mono`} placeholder="ex.: 64500" /></Field>
        <Field label="Tag dos equipamentos" hint="opcional: liga o grupo às tags de Equipamentos"><Input value={f.tag} onChange={e => setF({ ...f, tag: e.target.value })} disabled={ro} className={inp} /></Field>
        <Field label="Descrição"><Input value={f.description} onChange={e => setF({ ...f, description: e.target.value })} disabled={ro} className={inp} /></Field>
      </div>
      <div>
        <div className="flex items-center gap-2 mb-1.5"><Tags className="w-4 h-4 text-brand-soft" /><span className="text-sm font-medium text-slate-100">Communities do grupo</span>
          <span className="text-[11px] text-slate-500">o papel diz o que a config padrão faz com ela (marcar rotas de cliente, de upstream, blackhole…)</span></div>
        <div className="overflow-x-auto border border-line rounded-md">
          <table className="w-full text-xs" data-testid="bd-communities">
            <thead className="text-[10px] text-slate-500"><tr><th className="text-left px-2 py-1.5 font-normal">Community</th><th className="text-left px-2 font-normal">Nome</th>
              <th className="text-left px-2 font-normal">Papel</th><th className="text-left px-2 font-normal">Alvo (operadora/PTT)</th><th className="text-left px-2 font-normal">Descrição</th>
              <th className="text-right px-2 font-normal">Em uso</th><th className="w-8" /></tr></thead>
            <tbody>
              {f.communities.map((c, i) => (
                <tr key={i} className="border-t border-line/70">
                  <td className="px-2 py-1"><Input value={c.value} onChange={e => setC(i, { value: e.target.value })} disabled={ro} className={`${sm} w-36 font-mono`} placeholder="ASN:valor" aria-label="Community" /></td>
                  <td className="px-2"><Input value={c.name} onChange={e => setC(i, { name: e.target.value })} disabled={ro} className={`${sm} w-40`} aria-label="Nome" /></td>
                  <td className="px-2"><select value={c.role} onChange={e => setC(i, { role: e.target.value })} disabled={ro} className={`${sel} h-8 text-xs w-56`} aria-label="Papel">
                    {Object.entries(meta?.roles || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                  <td className="px-2"><Input value={c.target || ""} onChange={e => setC(i, { target: e.target.value })} disabled={ro} className={`${sm} w-36`} aria-label="Alvo" /></td>
                  <td className="px-2"><Input value={c.description || ""} onChange={e => setC(i, { description: e.target.value })} disabled={ro} className={`${sm} min-w-[200px]`} aria-label="Descrição" /></td>
                  <td className="px-2 text-right font-mono text-slate-400">{usage[c.value] || 0}</td>
                  <td>{!ro && <button onClick={() => setF(s => ({ ...s, communities: s.communities.filter((_, j) => j !== i) }))} className="p-1 text-slate-500 hover:text-red-300" aria-label="Remover community"><X className="w-3.5 h-3.5" /></button>}</td>
                </tr>
              ))}
              {!f.communities.length && <tr><td colSpan={7} className="px-3 py-3 text-slate-500">Nenhuma community ainda.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-2">
          {!ro && <Button size="sm" variant="outline" onClick={addC} className="h-8 border-line bg-transparent" data-testid="bd-add-comm"><Plus className="w-3.5 h-3.5 mr-1" />Community</Button>}
          {!ro && <Button size="sm" onClick={save} disabled={busy} className="h-8 bg-brand hover:bg-brand-strong" data-testid="bd-save-group">{busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}{isNew ? "Criar grupo" : "Salvar grupo"}</Button>}
          {isNew && <Button size="sm" variant="ghost" onClick={onCancel} className="h-8 text-slate-400">cancelar</Button>}
          {!isNew && !ro && <Button size="sm" variant="ghost" onClick={del} className="h-8 text-red-300 ml-auto"><Trash2 className="w-3.5 h-3.5 mr-1" />Apagar grupo</Button>}
        </div>
      </div>
    </div>
  );
}

function Prefixes({ group, peers, canEdit, reload }) {
  const blank = { prefix: "", asn: group?.asn ? String(group.asn) : "", communities: [], extra: "", origin: "", note: "", peer_id: "" };
  const [f, setF] = useState(blank);
  const [edit, setEdit] = useState(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setF(blank); setEdit(null); }, [group?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const cname = Object.fromEntries((group?.communities || []).map(c => [c.value, c.name]));
  const toggleC = (v) => setF(s => ({ ...s, communities: s.communities.includes(v) ? s.communities.filter(x => x !== v) : [...s.communities, v] }));
  const pickPeer = (pid) => { const p = peers.find(x => x.id === pid); setF(s => ({ ...s, peer_id: pid, asn: p ? String(p.remote_asn) : s.asn, origin: p ? p.name : s.origin })); };
  const submit = async () => {
    setBusy(true);
    const body = { prefix: f.prefix, asn: f.asn, communities: [...f.communities, ...f.extra.split(/[\s,]+/).filter(Boolean)], origin: f.origin, note: f.note, peer_id: f.peer_id };
    try {
      if (edit) await api.put(`/bgpdoc/groups/${group.id}/prefixes/${edit}`, body);
      else await api.post(`/bgpdoc/groups/${group.id}/prefixes`, body);
      toast.success(edit ? "Prefixo atualizado" : `${f.prefix} documentado em ${group.name}`);
      setF({ ...blank, asn: f.asn, peer_id: f.peer_id, origin: f.origin }); setEdit(null); reload();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const startEdit = (e) => { setEdit(e.id); setF({ prefix: e.prefix, asn: String(e.asn), communities: (e.communities || []).filter(c => cname[c] !== undefined), extra: (e.communities || []).filter(c => cname[c] === undefined).join(" "), origin: e.origin || "", note: e.note || "", peer_id: e.peer_id || "" }); };
  const del = async (e) => { if (!window.confirm(`Tirar ${e.prefix} (AS${e.asn}) da documentação?`)) return; try { await api.delete(`/bgpdoc/groups/${group.id}/prefixes/${e.id}`); reload(); } catch (x) { toast.error(formatApiError(x)); } };
  const rows = (group?.prefixes || []).filter(e => !q || `${e.prefix} AS${e.asn} ${e.origin} ${e.note} ${(e.communities || []).join(" ")}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => a.asn - b.asn || a.prefix.localeCompare(b.prefix, undefined, { numeric: true }));
  const downs = peers.filter(p => p.kind === "downstream");
  return (
    <div className="rounded-lg border border-line bg-surface p-4 space-y-3" data-testid="bd-prefixes">
      <div className="flex items-center gap-2"><Layers className="w-4 h-4 text-brand-soft" /><span className="text-sm font-medium text-slate-100">Prefixos documentados em {group.name}</span>
        <span className="text-[11px] text-slate-500">prefixo + ASN de origem + community — fica registrado no grupo</span></div>
      {canEdit && (
        <div className="rounded-md bg-sunken/60 border border-line p-3 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
            <Field label="Prefixo"><Input value={f.prefix} onChange={e => setF({ ...f, prefix: e.target.value.trim() })} className={`${inp} font-mono`} placeholder="x.x.x.x/x ou xxxx:xxxx::/xx" data-testid="bd-pfx" /></Field>
            <Field label="ASN de origem"><Input value={f.asn} onChange={e => setF({ ...f, asn: e.target.value.replace(/[^\d]/g, "") })} className={`${inp} font-mono`} data-testid="bd-pfx-asn" /></Field>
            <Field label="Cliente (downstream)" hint="opcional: preenche ASN e origem"><select value={f.peer_id} onChange={e => pickPeer(e.target.value)} className={`${sel} w-full`}>
              <option value="">—</option>{downs.map(p => <option key={p.id} value={p.id}>{p.name} · AS{p.remote_asn}</option>)}</select></Field>
            <Field label="Origem / POP"><Input value={f.origin} onChange={e => setF({ ...f, origin: e.target.value })} className={inp} /></Field>
            <Field label="Observação"><Input value={f.note} onChange={e => setF({ ...f, note: e.target.value })} className={inp} /></Field>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-slate-500 mr-1">Communities</span>
            {(group.communities || []).map(c => (
              <button key={c.value} onClick={() => toggleC(c.value)} title={c.description || ""} data-testid={`bd-pc-${c.value}`}
                      className={`px-2 py-0.5 rounded-md border text-xs font-mono ${f.communities.includes(c.value) ? "bg-brand border-brand text-onbrand" : "border-line text-slate-300 hover:border-line2"}`}>
                {c.value}{c.name ? <span className="font-sans opacity-75"> {c.name}</span> : ""}
              </button>
            ))}
            <Input value={f.extra} onChange={e => setF({ ...f, extra: e.target.value })} placeholder="outra(s): ASN:valor" className={`${sm} w-40 font-mono`} aria-label="Outras communities" />
            <Button size="sm" onClick={submit} disabled={busy || !f.prefix || !f.asn} className="h-8 ml-auto bg-brand hover:bg-brand-strong" data-testid="bd-pfx-add">
              {busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Plus className="w-3.5 h-3.5 mr-1" />}{edit ? "Salvar" : "Documentar"}
            </Button>
            {edit && <Button size="sm" variant="ghost" onClick={() => { setEdit(null); setF(blank); }} className="h-8 text-slate-400">cancelar</Button>}
          </div>
        </div>
      )}
      <div className="flex items-center gap-2"><Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar prefixo, ASN, community…" className={`${sm} w-64`} aria-label="Buscar prefixo" />
        <span className="text-[11px] text-slate-500">{rows.length} de {(group.prefixes || []).length}</span></div>
      <div className="overflow-x-auto border border-line rounded-md">
        <table className="w-full text-xs" data-testid="bd-prefix-table">
          <thead className="text-[10px] text-slate-500"><tr><th className="text-left px-2 py-1.5 font-normal">Prefixo</th><th className="text-left px-2 font-normal">ASN</th>
            <th className="text-left px-2 font-normal">Communities</th><th className="text-left px-2 font-normal">Origem</th><th className="text-left px-2 font-normal">Obs.</th>
            <th className="text-left px-2 font-normal">Por</th><th className="w-14" /></tr></thead>
          <tbody>
            {rows.map(e => (
              <tr key={e.id} className="border-t border-line/70 align-top">
                <td className="px-2 py-1.5 font-mono text-slate-100">{e.prefix}</td>
                <td className="px-2 py-1.5 font-mono text-slate-300">AS{e.asn}{group.asn === e.asn ? <span className="ml-1 text-[10px] text-brand-soft">próprio</span> : ""}</td>
                <td className="px-2 py-1.5"><div className="flex flex-wrap gap-1">{(e.communities || []).map(c => (
                  <span key={c} className={`px-1.5 rounded border font-mono ${cname[c] !== undefined ? "border-line text-slate-200" : "border-amber-600/60 text-amber-200"}`} title={cname[c] !== undefined ? cname[c] : "fora do catálogo do grupo"}>{c}{cname[c] ? <span className="font-sans text-slate-400"> {cname[c]}</span> : ""}</span>))}</div></td>
                <td className="px-2 py-1.5 text-slate-300">{e.origin}</td>
                <td className="px-2 py-1.5 text-slate-400">{e.note}</td>
                <td className="px-2 py-1.5 text-[10px] text-slate-500 whitespace-nowrap">{e.by}<br />{e.at ? new Date(e.at).toLocaleDateString("pt-BR") : ""}</td>
                <td className="px-1 whitespace-nowrap">{canEdit && <>
                  <button onClick={() => startEdit(e)} className="p-1 text-slate-500 hover:text-slate-200" aria-label={`Editar ${e.prefix}`}><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={() => del(e)} className="p-1 text-slate-500 hover:text-red-300" aria-label={`Apagar ${e.prefix}`}><Trash2 className="w-3.5 h-3.5" /></button></>}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={7} className="px-3 py-3 text-slate-500">Nenhum prefixo documentado.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- pares
const BLANK_PEER = { kind: "upstream", name: "", remote_asn: "", peer_ipv4: "", peer_ipv6: "", local_ipv4: "", local_ipv6: "", device_id: "", interface: "", vlan: "",
  bandwidth_mbps: "", max_prefix_v4: "", max_prefix_v6: "", communities_in: [], local_pref: "", export: "", md5: false, bfd: false, multihop: 0, prefixes: [],
  circuit: "", noc_email: "", noc_phone: "", status: "ativo", notes: "" };

function PeerForm({ meta, group, devices, peer, onClose, onSaved }) {
  const [f, setF] = useState(() => ({ ...BLANK_PEER, ...(peer || {}), remote_asn: peer?.remote_asn ? String(peer.remote_asn) : "",
    communities_in: (peer?.communities_in || []).join(" "), prefixes: (peer?.prefixes || []).join("\n") }));
  const [busy, setBusy] = useState(false);
  const set = (p) => setF(s => ({ ...s, ...p }));
  const save = async () => {
    setBusy(true);
    const body = { ...f, group_id: group.id, communities_in: String(f.communities_in).split(/[\s,]+/).filter(Boolean), prefixes: String(f.prefixes).split(/[\s,]+/).filter(Boolean) };
    try { peer?.id ? await api.put(`/bgpdoc/peers/${peer.id}`, body) : await api.post("/bgpdoc/peers", body); toast.success("Par salvo"); onSaved(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const lpDefault = meta?.default_lp?.[f.kind];
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-2 md:p-6 overflow-y-auto" role="dialog" aria-modal="true" aria-label="Par BGP" data-testid="bd-peer-form">
      <div className="w-full max-w-4xl bg-surface border border-line rounded-lg shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-line">
          <Users className="w-4 h-4 text-brand-soft" /><h2 className="text-sm font-semibold text-slate-100">{peer?.id ? `Editar ${peer.name}` : "Novo par BGP"} · {group.name}</h2>
          <button onClick={onClose} className="ml-auto p-1 text-slate-400 hover:text-slate-100" aria-label="Fechar"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <Field label="Tipo"><select value={f.kind} onChange={e => set({ kind: e.target.value })} className={`${sel} w-full`} data-testid="bd-peer-kind">{Object.entries(meta?.kinds || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Nome (operadora, cliente, PTT)"><Input value={f.name} onChange={e => set({ name: e.target.value })} className={inp} placeholder="Operadora X" data-testid="bd-peer-name" /></Field>
            <Field label="ASN remoto"><Input value={f.remote_asn} onChange={e => set({ remote_asn: e.target.value.replace(/[^\d]/g, "") })} className={`${inp} font-mono`} data-testid="bd-peer-asn" /></Field>
            <Field label="Situação"><select value={f.status} onChange={e => set({ status: e.target.value })} className={`${sel} w-full`}><option value="ativo">ativo</option><option value="implantando">implantando</option><option value="desativado">desativado</option></select></Field>
            <Field label="IP do vizinho (IPv4)"><Input value={f.peer_ipv4} onChange={e => set({ peer_ipv4: e.target.value.trim() })} className={`${inp} font-mono`} placeholder="x.x.x.x" data-testid="bd-peer-ip4" /></Field>
            <Field label="IP do vizinho (IPv6)"><Input value={f.peer_ipv6} onChange={e => set({ peer_ipv6: e.target.value.trim() })} className={`${inp} font-mono`} placeholder="xxxx:xxxx::x" /></Field>
            <Field label="IP local (IPv4)"><Input value={f.local_ipv4} onChange={e => set({ local_ipv4: e.target.value.trim() })} className={`${inp} font-mono`} /></Field>
            <Field label="IP local (IPv6)"><Input value={f.local_ipv6} onChange={e => set({ local_ipv6: e.target.value.trim() })} className={`${inp} font-mono`} /></Field>
            <Field label="Roteador"><select value={f.device_id} onChange={e => set({ device_id: e.target.value })} className={`${sel} w-full`}><option value="">—</option>
              {devices.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
            <Field label="Interface / VLAN"><div className="flex gap-1"><Input value={f.interface} onChange={e => set({ interface: e.target.value })} className={inp} /><Input value={f.vlan} onChange={e => set({ vlan: e.target.value })} className={`${inp} w-20`} placeholder="vlan" aria-label="VLAN" /></div></Field>
            <Field label="Banda contratada (Mbps)"><Input value={f.bandwidth_mbps ?? ""} onChange={e => set({ bandwidth_mbps: e.target.value.replace(/\D/g, "") })} className={`${inp} font-mono`} /></Field>
            <Field label="Circuito / designação"><Input value={f.circuit} onChange={e => set({ circuit: e.target.value })} className={inp} /></Field>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <Field label="Máx. prefixos IPv4"><Input value={f.max_prefix_v4 ?? ""} onChange={e => set({ max_prefix_v4: e.target.value.replace(/\D/g, "") })} className={`${inp} font-mono`} placeholder={f.kind === "upstream" ? "ex.: 1200000" : ""} /></Field>
            <Field label="Máx. prefixos IPv6"><Input value={f.max_prefix_v6 ?? ""} onChange={e => set({ max_prefix_v6: e.target.value.replace(/\D/g, "") })} className={`${inp} font-mono`} /></Field>
            <Field label="Local-preference" hint={lpDefault ? `vazio = ${lpDefault}` : ""}><Input value={f.local_pref ?? ""} onChange={e => set({ local_pref: e.target.value.replace(/\D/g, "") })} className={`${inp} font-mono`} /></Field>
            <Field label="Exportar para ele"><select value={f.export || (f.kind === "downstream" ? "full" : "own_customers")} onChange={e => set({ export: e.target.value })} className={`${sel} w-full`}>
              {Object.entries(meta?.exports || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Communities aplicadas na entrada" hint="vazio = a do papel no grupo"><Input value={f.communities_in} onChange={e => set({ communities_in: e.target.value })} className={`${inp} font-mono`} placeholder="ASN:valor" /></Field>
            <Field label="Multihop (TTL)"><Input value={f.multihop || ""} onChange={e => set({ multihop: Number(e.target.value.replace(/\D/g, "")) || 0 })} className={`${inp} font-mono`} /></Field>
            <div className="flex items-end gap-4 text-xs text-slate-300 pb-2 col-span-2">
              <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!f.md5} onChange={e => set({ md5: e.target.checked })} /> usa senha MD5 (não é guardada)</label>
              <label className="flex items-center gap-1.5"><input type="checkbox" checked={!!f.bfd} onChange={e => set({ bfd: e.target.checked })} /> BFD</label>
            </div>
          </div>
          {f.kind === "downstream" && (
            <Field label="Prefixos do cliente (além dos documentados no grupo com o ASN dele)">
              <textarea value={f.prefixes} onChange={e => set({ prefixes: e.target.value })} rows={3} className="w-full rounded-md bg-sunken border border-line px-3 py-2 text-xs font-mono text-slate-100" placeholder="x.x.x.x/x (um por linha)" />
            </Field>
          )}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Field label="E-mail do NOC"><Input value={f.noc_email} onChange={e => set({ noc_email: e.target.value })} className={inp} /></Field>
            <Field label="Telefone do NOC"><Input value={f.noc_phone} onChange={e => set({ noc_phone: e.target.value })} className={inp} /></Field>
            <Field label="Observações"><Input value={f.notes} onChange={e => set({ notes: e.target.value })} className={inp} /></Field>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} className="h-9 text-slate-300">Cancelar</Button>
            <Button onClick={save} disabled={busy} className="h-9 bg-brand hover:bg-brand-strong" data-testid="bd-peer-save">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}Salvar par</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ImportSessions({ group, onClose, onDone }) {
  const [rows, setRows] = useState(null);
  const [pick, setPick] = useState({});
  useEffect(() => { api.get("/bgpdoc/sessions").then(r => setRows(r.data)).catch(e => { toast.error(formatApiError(e)); setRows([]); }); }, []);
  const go = async () => {
    const sessions = (rows || []).filter(r => pick[r.peer]).map(r => ({ ...r, kind: pick[r.peer] }));
    try { const { data } = await api.post("/bgpdoc/import", { group_id: group.id, sessions }); toast.success(`${data.imported} par(es) importado(s)`); data.errors.forEach(e => toast.error(e)); onDone(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-2 md:p-6 overflow-y-auto" role="dialog" aria-modal="true" aria-label="Importar sessões">
      <div className="w-full max-w-3xl bg-surface border border-line rounded-lg shadow-2xl p-4 space-y-3">
        <div className="flex items-center gap-2"><Import className="w-4 h-4 text-brand-soft" /><h2 className="text-sm font-semibold text-slate-100">Importar do Monitor BGP para {group.name}</h2>
          <button onClick={onClose} className="ml-auto p-1 text-slate-400" aria-label="Fechar"><X className="w-4 h-4" /></button></div>
        <p className="text-xs text-slate-400">Sessões que os roteadores do Monitor BGP já mostram e ainda não estão documentadas. Escolha o tipo de cada uma.</p>
        {!rows ? <Loader2 className="w-4 h-4 animate-spin" /> : !rows.length ? <div className="text-xs text-slate-500">Nada para importar (configure os roteadores no Monitor BGP).</div> : (
          <div className="max-h-96 overflow-y-auto border border-line rounded-md">
            <table className="w-full text-xs"><tbody>
              {rows.map(r => (
                <tr key={r.peer + r.device_id} className="border-t border-line/70">
                  <td className="px-2 py-1.5 font-mono text-slate-100">{r.peer}</td><td className="px-2 font-mono text-slate-300">AS{r.asn}</td>
                  <td className="px-2 text-slate-300">{r.name}</td><td className="px-2 text-slate-400">{r.device_name}</td>
                  <td className={`px-2 ${STATE_CLS(r.state)}`}>{r.state}</td>
                  <td className="px-2"><select value={pick[r.peer] || ""} onChange={e => setPick({ ...pick, [r.peer]: e.target.value })} className={`${sel} h-8 text-xs`} aria-label={`Tipo de ${r.peer}`}>
                    <option value="">não importar</option><option value="upstream">Upstream</option><option value="downstream">Downstream</option><option value="ix">IX / PTT</option><option value="cdn">CDN</option><option value="ibgp">iBGP</option></select></td>
                </tr>
              ))}
            </tbody></table>
          </div>
        )}
        <div className="flex justify-end"><Button onClick={go} disabled={!Object.values(pick).some(Boolean)} className="h-9 bg-brand hover:bg-brand-strong">Importar</Button></div>
      </div>
    </div>
  );
}

function Peers({ meta, group, peers, devices, canEdit, reload, kindFilter, setKindFilter }) {
  const [form, setForm] = useState(null);
  const [imp, setImp] = useState(false);
  const [q, setQ] = useState("");
  const list = peers.filter(p => (!kindFilter || p.kind === kindFilter) && (!q || `${p.name} AS${p.remote_asn} ${p.peer_ipv4} ${p.peer_ipv6} ${p.device_name}`.toLowerCase().includes(q.toLowerCase())));
  const del = async (p) => { if (!window.confirm(`Apagar a documentação do par ${p.name}?`)) return; try { await api.delete(`/bgpdoc/peers/${p.id}`); reload(); } catch (e) { toast.error(formatApiError(e)); } };
  const counts = peers.reduce((a, p) => ({ ...a, [p.kind]: (a[p.kind] || 0) + 1 }), {});
  return (
    <div className="space-y-3" data-testid="bd-peers">
      <div className="flex flex-wrap items-center gap-1.5">
        <button onClick={() => setKindFilter("")} className={`px-2.5 h-8 rounded-md border text-xs ${!kindFilter ? "bg-brand border-brand text-onbrand" : "border-line text-slate-300"}`}>Todos {peers.length}</button>
        {Object.entries(meta?.kinds || {}).map(([k, l]) => (
          <button key={k} onClick={() => setKindFilter(k)} data-testid={`bd-kind-${k}`} className={`px-2.5 h-8 rounded-md border text-xs ${kindFilter === k ? "bg-brand border-brand text-onbrand" : "border-line text-slate-300"}`}>{l.split(" (")[0]}s {counts[k] || 0}</button>
        ))}
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar…" className={`${sm} w-48 ml-2`} aria-label="Buscar par" />
        {canEdit && <div className="ml-auto flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setImp(true)} className="h-8 border-line bg-transparent"><Import className="w-3.5 h-3.5 mr-1" />Importar do Monitor BGP</Button>
          <Button size="sm" onClick={() => setForm({ kind: kindFilter || "upstream" })} className="h-8 bg-brand hover:bg-brand-strong" data-testid="bd-add-peer"><Plus className="w-3.5 h-3.5 mr-1" />Par BGP</Button>
        </div>}
      </div>
      <div className="overflow-x-auto border border-line rounded-md">
        <table className="w-full text-xs" data-testid="bd-peer-table">
          <thead className="text-[10px] text-slate-500"><tr>
            <th className="text-left px-2 py-1.5 font-normal">Tipo</th><th className="text-left px-2 font-normal">Nome</th><th className="text-left px-2 font-normal">ASN</th>
            <th className="text-left px-2 font-normal">Vizinho</th><th className="text-left px-2 font-normal">Roteador / interface</th><th className="text-left px-2 font-normal">Sessão agora</th>
            <th className="text-left px-2 font-normal">Política</th><th className="text-left px-2 font-normal">Contato</th><th className="w-14" />
          </tr></thead>
          <tbody>
            {list.map(p => (
              <tr key={p.id} className={`border-t border-line/70 align-top ${p.status === "desativado" ? "opacity-50" : ""}`}>
                <td className="px-2 py-1.5"><span className={`text-[10px] px-1.5 py-0.5 rounded border ${KIND_CLS[p.kind]}`}>{(meta?.kinds?.[p.kind] || p.kind).split(" (")[0]}</span></td>
                <td className="px-2 py-1.5 text-slate-100">{p.name}{p.status !== "ativo" && <span className="ml-1 text-[10px] text-slate-500">({p.status})</span>}</td>
                <td className="px-2 py-1.5 font-mono text-slate-300">AS{p.remote_asn}</td>
                <td className="px-2 py-1.5 font-mono text-slate-200">{p.peer_ipv4}{p.peer_ipv6 && <div>{p.peer_ipv6}</div>}</td>
                <td className="px-2 py-1.5 text-slate-300">{p.device_name || "—"}{p.interface && <div className="font-mono text-[11px] text-slate-500">{p.interface}{p.vlan ? ` · vlan ${p.vlan}` : ""}</div>}</td>
                <td className="px-2 py-1.5">{p.live?.length ? p.live.map((s, i) => <div key={i} className={STATE_CLS(s.state)}>{s.state}{s.prefixes != null ? <span className="text-slate-500"> · {Number(s.prefixes).toLocaleString("pt-BR")} pfx</span> : ""}</div>) : <span className="text-slate-500">—</span>}</td>
                <td className="px-2 py-1.5 text-slate-400">LP {p.local_pref ?? meta?.default_lp?.[p.kind] ?? "—"} · {(meta?.exports?.[p.export] || "").split(" (")[0]}{p.max_prefix_v4 ? ` · máx ${Number(p.max_prefix_v4).toLocaleString("pt-BR")}` : ""}</td>
                <td className="px-2 py-1.5 text-slate-400">{[p.noc_email, p.noc_phone, p.circuit].filter(Boolean).join(" · ")}</td>
                <td className="px-1 whitespace-nowrap">{canEdit && <>
                  <button onClick={() => setForm(p)} className="p-1 text-slate-500 hover:text-slate-200" aria-label={`Editar ${p.name}`}><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={() => del(p)} className="p-1 text-slate-500 hover:text-red-300" aria-label={`Apagar ${p.name}`}><Trash2 className="w-3.5 h-3.5" /></button></>}</td>
              </tr>
            ))}
            {!list.length && <tr><td colSpan={9} className="px-3 py-4 text-center text-slate-500">Nenhum par {kindFilter ? (meta?.kinds?.[kindFilter] || "").toLowerCase() : ""} documentado neste grupo.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="text-[11px] text-slate-500">"Sessão agora" vem do Monitor BGP (pelo IP do vizinho), quando o roteador está sendo monitorado lá.</div>
      {form && <PeerForm meta={meta} group={group} devices={devices} peer={form.id ? form : { ...BLANK_PEER, ...form }} onClose={() => setForm(null)} onSaved={() => { setForm(null); reload(); }} />}
      {imp && <ImportSessions group={group} onClose={() => setImp(false)} onDone={() => { setImp(false); reload(); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- config padrão
function Generator({ meta, group, peers }) {
  const [f, setF] = useState({ vendor: "huawei", local_asn: "", router_id: "", base: true });
  const [pick, setPick] = useState(() => new Set());
  const [out, setOut] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setPick(new Set()); setOut(null); }, [group?.id]);
  const gen = async () => {
    setBusy(true);
    try { setOut((await api.post("/bgpdoc/generate", { group_id: group.id, ...f, peer_ids: [...pick] })).data); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const byKind = Object.keys(meta?.kinds || {}).map(k => [k, peers.filter(p => p.kind === k && p.status !== "desativado")]).filter(([, l]) => l.length);
  return (
    <div className="space-y-3" data-testid="bd-gen">
      <div className="rounded-lg border border-line bg-surface p-4 space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <Field label="Fabricante"><select value={f.vendor} onChange={e => setF({ ...f, vendor: e.target.value })} className={`${sel} w-full`} data-testid="bd-vendor">{Object.entries(meta?.vendors || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="ASN local" hint={group.asn ? `vazio = AS${group.asn} (do grupo)` : "o grupo não tem ASN"}><Input value={f.local_asn} onChange={e => setF({ ...f, local_asn: e.target.value.replace(/\D/g, "") })} className={`${inp} font-mono`} /></Field>
          <Field label="Router-ID (opcional)"><Input value={f.router_id} onChange={e => setF({ ...f, router_id: e.target.value.trim() })} className={`${inp} font-mono`} placeholder="x.x.x.x" /></Field>
          <label className="flex items-end gap-2 text-xs text-slate-300 pb-2"><input type="checkbox" checked={f.base} onChange={e => setF({ ...f, base: e.target.checked })} /> incluir base do roteador (process BGP, network e rota nula dos agregados)</label>
        </div>
        <div>
          <div className="text-xs text-slate-300 mb-1">Pares para configurar</div>
          {!byKind.length && <div className="text-xs text-slate-500">Nenhum par documentado neste grupo — a config sai só com a base e as listas.</div>}
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {byKind.map(([k, l]) => (
              <div key={k} className="rounded-md border border-line p-2">
                <div className="flex items-center gap-2 mb-1"><span className={`text-[10px] px-1.5 rounded border ${KIND_CLS[k]}`}>{meta.kinds[k]}</span>
                  <button onClick={() => setPick(s => { const n = new Set(s); const all = l.every(p => n.has(p.id)); l.forEach(p => (all ? n.delete(p.id) : n.add(p.id))); return n; })} className="ml-auto text-[10px] text-slate-500 hover:text-slate-300">todos</button></div>
                {l.map(p => (
                  <label key={p.id} className="flex items-center gap-2 text-xs py-0.5 cursor-pointer">
                    <input type="checkbox" checked={pick.has(p.id)} onChange={() => setPick(s => { const n = new Set(s); n.has(p.id) ? n.delete(p.id) : n.add(p.id); return n; })} />
                    <span className="text-slate-100 truncate">{p.name}</span><span className="font-mono text-slate-500 ml-auto">AS{p.remote_asn}</span>
                  </label>
                ))}
              </div>
            ))}
          </div>
        </div>
        <Button onClick={gen} disabled={busy} className="h-9 bg-brand hover:bg-brand-strong" data-testid="bd-generate">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <FileCode2 className="w-4 h-4 mr-1.5" />}Gerar configuração</Button>
        <div className="text-[11px] text-slate-500">
          O que sai: prefix-list dos prefixos próprios do grupo (até /24 e /48), community-list de clientes, política de entrada por par (upstream/IX/CDN: nega os seus
          prefixos, tira a community de cliente, marca a community do papel e o local-pref; cliente: só os prefixos documentados dele) e de saída (próprios + clientes, tabela
          completa ou só a padrão), limite de prefixos e o peer. Senha MD5 sai como &lt;SENHA&gt;. Revise antes de aplicar.
        </div>
      </div>
      {out && (
        <div className="rounded-md border border-line bg-canvas" data-testid="bd-gen-out">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-line">
            <FileCode2 className="w-4 h-4 text-brand-soft" /><span className="text-xs font-mono text-slate-200">bgp-{group.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-{f.vendor}.txt</span>
            <div className="ml-auto flex gap-1.5">
              <Button size="sm" onClick={() => copy(out.script, "Configuração copiada")} className="h-7 bg-brand hover:bg-brand-strong"><Copy className="w-3.5 h-3.5 mr-1" />Copiar</Button>
              <Button size="sm" variant="outline" onClick={() => download(out.script, `bgp-${group.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${f.vendor}.txt`)} className="h-7 border-line bg-transparent"><Download className="w-3.5 h-3.5 mr-1" />.txt</Button>
            </div>
          </div>
          {out.warnings?.length > 0 && <div className="px-3 pt-2 space-y-1">{out.warnings.map((w, i) => <div key={i} className="text-[11px] text-amber-200">! {w}</div>)}</div>}
          <pre className="m-0 px-3 py-2 max-h-[32rem] overflow-auto text-[12px] leading-relaxed font-mono text-slate-200 whitespace-pre">
            {out.script.split("\n").map((l, i) => <div key={i} className={/^[#!]/.test(l) ? "text-slate-500" : ""}>{l || " "}</div>)}
          </pre>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- página
const TABS = [["overview", "Visão geral", BookMarked], ["comm", "Communities e prefixos", Tags], ["peers", "Pares BGP", Users], ["gen", "Config padrão", FileCode2]];

export default function BgpDoc() {
  const [tab, setTab] = useState("overview");
  const [meta, setMeta] = useState(null);
  const [groups, setGroups] = useState([]);
  const [gid, setGid] = useState(() => { try { return localStorage.getItem(STORE) || ""; } catch { return ""; } });
  const [group, setGroup] = useState(null);
  const [peers, setPeers] = useState([]);
  const [devices, setDevices] = useState([]);
  const [creating, setCreating] = useState(false);
  const [kindFilter, setKindFilter] = useState("");
  const canEdit = !!meta?.can_edit;

  const loadGroups = useCallback(async () => {
    const { data } = await api.get("/bgpdoc/groups");
    setGroups(data);
    setGid(cur => (data.some(g => g.id === cur) ? cur : data[0]?.id || ""));
  }, []);
  const loadGroup = useCallback(async () => {
    if (!gid) { setGroup(null); setPeers([]); return; }
    try {
      const [g, p] = await Promise.all([api.get(`/bgpdoc/groups/${gid}`), api.get("/bgpdoc/peers", { params: { group_id: gid } })]);
      setGroup(g.data); setPeers(p.data);
    } catch (e) { toast.error(formatApiError(e)); }
  }, [gid]);
  useEffect(() => {
    api.get("/bgpdoc/meta").then(r => setMeta(r.data)).catch(() => {});
    api.get("/devices").then(r => setDevices((r.data || []).sort((a, b) => a.name.localeCompare(b.name, "pt-BR", { numeric: true })))).catch(() => {});
    loadGroups().catch(e => toast.error(formatApiError(e)));
  }, [loadGroups]);
  useEffect(() => { loadGroup(); try { if (gid) localStorage.setItem(STORE, gid); } catch { /* ok */ } }, [gid, loadGroup]);
  const reloadAll = () => { loadGroups(); loadGroup(); };
  const open = (id, t, kind = "") => { setGid(id); setTab(t); setKindFilter(kind); setCreating(false); };

  return (
    <div className="flex-1 flex flex-col min-h-0" data-testid="bgpdoc-page">
      <div className="px-4 md:px-6 pt-4">
        <div className="hidden md:block text-xs text-slate-400">Grupos · communities · prefixos · upstreams e downstreams</div>
        <div className="flex flex-wrap items-center gap-3 mt-1">
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100">Documentação BGP</h1>
          {tab !== "overview" && <div className="ml-auto"><GroupPicker groups={groups} value={gid} onChange={(v) => { setGid(v); setCreating(false); }} onNew={() => { setCreating(true); setTab("comm"); }} canEdit={canEdit} /></div>}
          {tab === "overview" && canEdit && <Button size="sm" onClick={() => { setCreating(true); setTab("comm"); }} className="ml-auto h-9 bg-brand hover:bg-brand-strong" data-testid="bd-new-group-top"><Plus className="w-4 h-4 mr-1" />Novo grupo</Button>}
        </div>
        <div className="flex gap-1 mt-3 border-b border-line overflow-x-auto">
          {TABS.map(([k, l, I]) => <button key={k} className={tabBtn(tab === k)} onClick={() => setTab(k)} data-testid={`bd-tab-${k}`}><I className="w-4 h-4" />{l}</button>)}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 md:px-6 md:pb-6 space-y-4">
        {tab === "overview" && <Overview groups={groups} meta={meta} open={open} />}
        {tab === "comm" && (creating ? (
          <GroupEditor meta={meta} isNew canEdit={canEdit} onCancel={() => setCreating(false)} onSaved={(g) => { setCreating(false); loadGroups().then(() => setGid(g.id)); }} />
        ) : group ? (<>
          <GroupEditor meta={meta} group={group} canEdit={canEdit} onSaved={() => reloadAll()} onDeleted={() => { setGid(""); loadGroups(); }} />
          <Prefixes group={group} peers={peers} canEdit={canEdit} reload={loadGroup} />
        </>) : <div className="text-sm text-slate-500">Crie um grupo para começar.</div>)}
        {tab === "peers" && (group ? <Peers meta={meta} group={group} peers={peers} devices={devices} canEdit={canEdit} reload={reloadAll} kindFilter={kindFilter} setKindFilter={setKindFilter} />
          : <div className="text-sm text-slate-500">Crie um grupo para começar.</div>)}
        {tab === "gen" && (group ? <Generator meta={meta} group={group} peers={peers} /> : <div className="text-sm text-slate-500">Crie um grupo para começar.</div>)}
      </div>
    </div>
  );
}
