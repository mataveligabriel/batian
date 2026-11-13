import React, { useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { fmtBps, fmtSpeed } from "@/lib/netfmt";
import { AlertTriangle, ArrowLeftRight, Loader2, Network, Radar, Search, Wand2, X } from "lucide-react";

const VENDOR = { huawei: "Huawei", juniper: "Juniper", datacom: "Datacom", cisco: "Cisco", zte: "ZTE", mikrotik: "MikroTik", fiberhome: "FiberHome", nokia: "Nokia" };
const vname = (t) => VENDOR[t] || (t ? t[0].toUpperCase() + t.slice(1) : "Outro");
const chip = (on) => `px-2 py-0.5 rounded-md text-xs border ${on ? "bg-brand border-brand text-onbrand" : "bg-surface border-line text-slate-300 hover:border-line2"}`;

function Util({ pct, bps }) {
  const p = Math.max(0, Math.min(100, pct || 0));
  const col = pct == null ? "rgb(var(--c-slate-600))" : pct >= 90 ? "#e66767" : pct >= 70 ? "#c98500" : "#199e70";
  return (
    <div className="min-w-[110px]">
      <div className="flex justify-between text-[11px] font-mono"><span className="text-slate-100">{bps == null ? "—" : fmtBps(bps)}</span><span className="text-slate-500">{pct == null ? "" : `${pct}%`}</span></div>
      <div className="h-1 rounded bg-line overflow-hidden mt-0.5"><div className="h-full" style={{ width: `${p}%`, background: col }} /></div>
    </div>
  );
}

/**
 * Mapa automático: escolhe os equipamentos (por tag/grupo, fabricante ou busca), o BastiON lê por SNMP as interfaces,
 * os IPs e os vizinhos LLDP, calcula os enlaces e mostra o tráfego medido agora; aí grava como mapa novo ou acrescenta
 * num mapa existente.
 */
export function AutoMapDialog({ devices, maps, current, onClose, onDone }) {
  const [tag, setTag] = useState("");
  const [vendor, setVendor] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(() => new Set((current?.nodes || []).map(n => n.device_id).filter(Boolean)));
  const [opts, setOpts] = useState({ lldp: true, physical: false, sample_sec: 10 });
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState("");
  const [skip, setSkip] = useState(() => new Set());
  const [target, setTarget] = useState(current?.id || "");
  const [name, setName] = useState("");

  const tags = useMemo(() => [...new Set(devices.flatMap(d => d.tags || []))].sort(), [devices]);
  const inTag = devices.filter(d => !tag || (d.tags || []).includes(tag));
  const vendors = useMemo(() => { const c = {}; inTag.forEach(d => { const v = d.device_type || "other"; c[v] = (c[v] || 0) + 1; }); return Object.entries(c).sort((a, b) => b[1] - a[1]); }, [inTag]);
  const listed = inTag.filter(d => (!vendor || (d.device_type || "other") === vendor) && (!q || `${d.name} ${d.host}`.toLowerCase().includes(q.toLowerCase())))
    .sort((a, b) => a.name.localeCompare(b.name, "pt-BR", { numeric: true }));
  const allOn = listed.length > 0 && listed.every(d => sel.has(d.id));
  const toggle = (id) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleListed = () => setSel(s => { const n = new Set(s); listed.forEach(d => (allOn ? n.delete(d.id) : n.add(d.id))); return n; });

  const discover = async () => {
    setBusy("disc"); setRes(null); setSkip(new Set());
    try {
      const { data } = await api.post("/maps/auto/discover", { device_ids: [...sel], ...opts });
      setRes(data);
      if (!name) setName(tag ? `Backbone ${tag}` : "Mapa automático");
      toast.success(`${data.links.length} enlace(s) entre ${data.nodes.filter(n => n.ok).length} equipamento(s)`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const create = async () => {
    setBusy("save");
    try {
      const links = res.links.filter(l => !skip.has(l.id));
      const { data } = await api.post("/maps/auto/create", { name, map_id: target, device_ids: res.nodes.filter(n => n.ok).map(n => n.id), links });
      toast.success(target ? `Mapa atualizado: +${data.added_nodes} equipamento(s), +${data.added_links} enlace(s)` : `Mapa criado com ${data.nodes} equipamentos e ${data.links} enlaces`);
      onDone(data.id);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const addOutside = (o) => {
    const d = devices.find(x => [x.name, x.snmp_sys_name].some(n => n && n.toLowerCase().split(".")[0] === o.sys_name.toLowerCase().split(".")[0]));
    if (!d) { toast.error(`${o.sys_name} não está cadastrado no BastiON`); return; }
    setSel(s => new Set([...s, d.id])); toast.success(`${d.name} entrou na seleção — descubra de novo`);
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-2 md:p-6 overflow-y-auto" role="dialog" aria-modal="true" aria-label="Mapa automático" data-testid="automap">
      <div className="w-full max-w-6xl bg-surface border border-line rounded-lg shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-line">
          <Wand2 className="w-4 h-4 text-brand-soft" />
          <h2 className="text-sm font-semibold text-slate-100">Mapa automático</h2>
          <span className="text-xs text-slate-400 hidden md:inline">escolha os equipamentos do grupo/backbone — o BastiON calcula os enlaces e mostra o tráfego real</span>
          <button onClick={onClose} className="ml-auto p-1 text-slate-400 hover:text-slate-100" aria-label="Fechar"><X className="w-4 h-4" /></button>
        </div>

        <div className="p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-slate-500 mr-1">Grupo (tag)</span>
            <button className={chip(!tag)} onClick={() => setTag("")}>Todas</button>
            {tags.map(t => <button key={t} className={chip(tag === t)} onClick={() => { setTag(t); setVendor(""); }} data-testid={`am-tag-${t}`}>{t}</button>)}
          </div>
          {vendors.length > 1 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-slate-500 mr-1">Fabricante</span>
              <button className={chip(!vendor)} onClick={() => setVendor("")}>Todos</button>
              {vendors.map(([v, n]) => <button key={v} className={chip(vendor === v)} onClick={() => setVendor(v)}>{vname(v)} <span className="opacity-60">{n}</span></button>)}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative"><Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-500" />
              <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar…" className="pl-8 h-9 w-56 bg-sunken border-line text-sm" aria-label="Buscar equipamento" /></div>
            <Button size="sm" variant="outline" onClick={toggleListed} className="h-9 border-line bg-transparent">{allOn ? "desmarcar" : "marcar"} os {listed.length} listados</Button>
            <span className="text-xs text-slate-300 ml-auto"><b className="text-slate-100">{sel.size}</b> selecionado(s)</span>
            {sel.size > 0 && <button onClick={() => setSel(new Set())} className="text-[11px] text-slate-500 hover:text-slate-300">limpar</button>}
          </div>
          <div className="max-h-56 overflow-y-auto border border-line rounded-md grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3" data-testid="am-devices">
            {listed.map(d => (
              <label key={d.id} className="flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-slate-800/40 min-w-0">
                <input type="checkbox" checked={sel.has(d.id)} onChange={() => toggle(d.id)} className="w-4 h-4 shrink-0" />
                <span className="truncate text-slate-100">{d.name}</span>
                <span className="ml-auto text-[10px] text-slate-500 shrink-0">{vname(d.device_type)}</span>
              </label>
            ))}
            {!listed.length && <div className="px-3 py-3 text-xs text-slate-500">nenhum equipamento com esse filtro</div>}
          </div>
          <div className="flex flex-wrap items-center gap-4 text-xs text-slate-300">
            <label className="flex items-center gap-1.5"><input type="checkbox" checked={opts.lldp} onChange={e => setOpts({ ...opts, lldp: e.target.checked })} /> vizinhos LLDP (enlaces L2 sem IP)</label>
            <label className="flex items-center gap-1.5" title="Quando dois equipamentos já estão ligados por uma interface com IP (ex.: Eth-Trunk), também desenha as portas físicas"><input type="checkbox" checked={opts.physical} onChange={e => setOpts({ ...opts, physical: e.target.checked })} /> portas físicas dos bundles</label>
            <label className="flex items-center gap-1.5">amostra de tráfego
              <select value={opts.sample_sec} onChange={e => setOpts({ ...opts, sample_sec: Number(e.target.value) })} className="h-8 rounded-md bg-sunken border border-line px-1.5">{[5, 10, 20, 30].map(s => <option key={s} value={s}>{s} s</option>)}</select>
            </label>
            <Button onClick={discover} disabled={sel.size < 2 || !!busy} className="ml-auto h-9 bg-brand hover:bg-brand-strong" data-testid="am-discover">
              {busy === "disc" ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Radar className="w-4 h-4 mr-1.5" />}{busy === "disc" ? "Lendo os equipamentos…" : "Descobrir enlaces"}
            </Button>
          </div>
          <div className="text-[11px] text-slate-500">Lê por SNMP: interfaces, IPs (enlaces /29–/31 entre dois equipamentos), vizinhos LLDP e o tráfego em duas leituras. Os equipamentos precisam ter SNMP funcionando.</div>
        </div>

        {res && (
          <div className="border-t border-line p-4 space-y-3" data-testid="am-result">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
              <span className="text-slate-100 font-medium">{res.links.length} enlace(s)</span>
              <span className="text-slate-400">{res.nodes.filter(n => n.ok).length}/{res.nodes.length} equipamentos lidos · {res.duration_sec}s · tráfego medido em {res.sample_sec}s</span>
              {res.nodes.some(n => !n.ok) && <span className="text-amber-300">{res.nodes.filter(n => !n.ok).length} sem SNMP</span>}
            </div>
            {res.nodes.some(n => !n.ok || !n.links) && (
              <div className="flex flex-wrap gap-1.5 text-[11px]">
                {res.nodes.filter(n => !n.ok).map(n => <span key={n.id} className="px-1.5 py-0.5 rounded border border-amber-600/50 text-amber-200" title={n.error}>{n.name}: {n.error || "sem leitura"}</span>)}
                {res.nodes.filter(n => n.ok && !n.links).map(n => <span key={n.id} className="px-1.5 py-0.5 rounded border border-line text-slate-400">{n.name}: nenhum enlace com os outros escolhidos</span>)}
              </div>
            )}
            <div className="overflow-x-auto border border-line rounded-md">
              <table className="w-full text-xs" data-testid="am-links">
                <thead className="text-[10px] text-slate-500"><tr>
                  <th className="w-8" /><th className="text-left px-2 py-1.5 font-normal">Ponta A</th><th className="w-6" /><th className="text-left px-2 font-normal">Ponta B</th>
                  <th className="text-left px-2 font-normal">Como achou</th><th className="text-right px-2 font-normal">Capacidade</th>
                  <th className="text-left px-2 font-normal">A → B</th><th className="text-left px-2 font-normal">B → A</th>
                </tr></thead>
                <tbody>
                  {res.links.map(l => (
                    <tr key={l.id} className={`border-t border-line/70 ${skip.has(l.id) ? "opacity-40" : ""}`}>
                      <td className="text-center"><input type="checkbox" checked={!skip.has(l.id)} aria-label="Incluir no mapa"
                        onChange={() => setSkip(s => { const n = new Set(s); n.has(l.id) ? n.delete(l.id) : n.add(l.id); return n; })} /></td>
                      <td className="px-2 py-1.5"><div className="text-slate-100">{l.a_name}</div><div className="font-mono text-[11px] text-slate-400">{l.a_if}{l.oper_a && l.oper_a !== "up" ? <span className="text-red-300"> ({l.oper_a})</span> : ""}</div></td>
                      <td className="text-slate-500"><ArrowLeftRight className="w-3.5 h-3.5" /></td>
                      <td className="px-2"><div className="text-slate-100">{l.b_name}</div><div className="font-mono text-[11px] text-slate-400">{l.b_if}</div></td>
                      <td className="px-2 text-slate-400 whitespace-nowrap">{l.method === "ip" ? <>sub-rede <span className="font-mono">{l.net}</span></> : "LLDP"}</td>
                      <td className="px-2 text-right font-mono text-slate-300 whitespace-nowrap">{l.capacity_mbps ? fmtSpeed(l.capacity_mbps) : "—"}</td>
                      <td className="px-2 py-1"><Util pct={l.ab_pct} bps={l.ab_bps} /></td>
                      <td className="px-2 py-1"><Util pct={l.ba_pct} bps={l.ba_bps} /></td>
                    </tr>
                  ))}
                  {!res.links.length && <tr><td colSpan={8} className="px-3 py-4 text-center text-slate-500">Nenhum enlace entre os escolhidos. Confira o SNMP e se as interfaces têm IP /30–/31 ou LLDP ligado.</td></tr>}
                </tbody>
              </table>
            </div>
            {res.outside?.length > 0 && (
              <div className="text-[11px] text-slate-400">
                <div className="mb-1 flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5 text-amber-300" /> Vizinhos LLDP fora da seleção (clique para incluir e descobrir de novo):</div>
                <div className="flex flex-wrap gap-1.5">{res.outside.map(o => (
                  <button key={o.sys_name} onClick={() => addOutside(o)} className="px-1.5 py-0.5 rounded border border-line hover:border-brand text-slate-300" title={`visto por ${o.seen_by.join(", ")} · portas ${o.ports.join(", ")}`}>
                    {o.sys_name} <span className="text-slate-500">← {o.seen_by.join(", ")}</span>
                  </button>
                ))}</div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <select value={target} onChange={e => setTarget(e.target.value)} className="h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100" aria-label="Onde gravar">
                <option value="">Criar mapa novo</option>
                {maps.map(m => <option key={m.id} value={m.id}>Acrescentar em: {m.name}</option>)}
              </select>
              {!target && <Input value={name} onChange={e => setName(e.target.value)} placeholder="Nome do mapa" className="h-9 w-64 bg-sunken border-line" aria-label="Nome do mapa" />}
              <Button onClick={create} disabled={!!busy || !res.nodes.some(n => n.ok)} className="h-9 bg-brand hover:bg-brand-strong" data-testid="am-create">
                {busy === "save" ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Network className="w-4 h-4 mr-1.5" />}{target ? "Atualizar mapa" : "Criar mapa"}
              </Button>
              <span className="text-[11px] text-slate-500">{target ? "Mantém o que já está desenhado e acrescenta o que faltar." : "Posições automáticas — ajuste arrastando no modo Editar."} O tráfego dos enlaces passa a ser coletado sozinho.</span>
            </div>
            {sel.size !== res.nodes.length && <div className="text-[11px] text-amber-300">A seleção mudou depois da leitura — descubra de novo para incluir.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
