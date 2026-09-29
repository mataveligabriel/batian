import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus, Trash2, Search, Loader2, RefreshCw, Radio, Check, Wand2 } from "lucide-react";
import { ago, fmtRate, ROLE_LABEL, selCls, inputCls } from "@/components/flow/flowlib";

function AddDialog({ devices, onClose, onAdded }) {
  const [q, setQ] = useState("");
  const [dev, setDev] = useState(null);
  const [cand, setCand] = useState(null);
  const [exp, setExp] = useState("");
  const [busy, setBusy] = useState(false);
  const [fq, setFq] = useState("");
  const [onlyActive, setOnlyActive] = useState(true);
  const [rowRole, setRowRole] = useState({});
  const [rowLabel, setRowLabel] = useState({});
  const list = devices.filter(d => !q || `${d.name} ${d.host}`.toLowerCase().includes(q.toLowerCase()));
  const loadCand = async (d, e = "") => {
    setBusy(true);
    try { const { data } = await api.get(`/flow/devices/${d.id}/candidates`, { params: { exporter: e } }); setCand(data); setExp(data.exporter); }
    catch (err) { toast.error(formatApiError(err)); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (dev) loadCand(dev); }, [dev]); // eslint-disable-line react-hooks/exhaustive-deps
  const snmp = async () => {
    setBusy(true);
    try { await api.get(`/devices/${dev.id}/interfaces`, { params: { refresh: true } }); await loadCand(dev, exp); }
    catch (e) { toast.error(formatApiError(e)); setBusy(false); }
  };
  const add = async (i) => {
    try {
      await api.post("/flow/interfaces", { device_id: dev.id, if_index: i.index, if_name: i.name || `ifIndex ${i.index}`, exporter: exp,
                                          role: rowRole[i.index] || "transito", label: rowLabel[i.index] ?? (i.alias || "") });
      toast.success(`${i.name || i.index} monitorada`);
      await loadCand(dev, exp); onAdded();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const rows = (cand?.interfaces || []).filter(i => (!onlyActive || i.active || i.monitored) &&
    (!fq || `${i.name} ${i.alias} ${i.index}`.toLowerCase().includes(fq.toLowerCase())));

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-5xl max-h-[94vh] overflow-y-auto" data-testid="flow-add-dialog">
        <DialogHeader><DialogTitle>Monitorar interfaces com flow</DialogTitle></DialogHeader>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <div className="relative"><Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Equipamento…" className={`${inputCls} pl-8 h-8 text-sm`} /></div>
            <div className="h-80 overflow-y-auto border border-[#1E293B] rounded mt-1 divide-y divide-[#111722] bg-[#05070A]">
              {list.map(d => (
                <button key={d.id} onClick={() => setDev(d)} data-testid={`fdev-${d.id}`}
                        className={`w-full text-left px-2.5 py-1.5 text-sm ${dev?.id === d.id ? "bg-[#007AFF]/20 text-slate-100" : "text-slate-300 hover:bg-slate-800/60"}`}>
                  <div className="truncate">{d.name}</div><div className="text-[10px] font-mono text-slate-500">{d.host}</div>
                </button>
              ))}
            </div>
          </div>
          <div className="md:col-span-3">
            {!dev ? <div className="h-80 flex items-center justify-center text-xs text-slate-500 font-mono border border-[#1E293B] rounded">Escolha o equipamento</div> : (
              <>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-slate-400">Exportador (IP de origem do flow):</span>
                  <Input value={exp} onChange={e => setExp(e.target.value)} onBlur={() => cand && exp !== cand.exporter && loadCand(dev, exp)} className={`${inputCls} h-8 w-40 font-mono text-xs`} data-testid="fexp" />
                  {cand?.exporter_seen
                    ? <span className="text-[11px] font-mono flex items-center gap-1 text-slate-300"><Radio className="w-3.5 h-3.5 text-[#199e70]" /> recebendo {cand.exporter_kind} ({ago(cand.exporter_last)})</span>
                    : cand && <span className="text-[11px] text-amber-300">Nada chegou deste IP ainda — confira a configuração do roteador, o firewall (ufw) ou use o IP da loopback de origem.</span>}
                  <Button size="sm" variant="ghost" onClick={snmp} disabled={busy} className="h-8 ml-auto text-slate-300 hover:bg-slate-800" title="Ler nomes das interfaces por SNMP"><RefreshCw className={`w-3.5 h-3.5 mr-1 ${busy ? "animate-spin" : ""}`} />Nomes (SNMP)</Button>
                </div>
                {cand && !cand.snmp_cached && <div className="text-[11px] text-slate-500 mt-1">Sem a lista SNMP deste equipamento: as interfaces aparecem pelo ifIndex. Clique em <b>Nomes (SNMP)</b> para ver os nomes.</div>}
                <div className="flex items-center gap-2 mt-2">
                  <Input value={fq} onChange={e => setFq(e.target.value)} placeholder="Filtrar interface…" className={`${inputCls} h-8 text-xs font-mono flex-1`} />
                  <label className="text-[11px] text-slate-400 flex items-center gap-1 cursor-pointer"><input type="checkbox" checked={onlyActive} onChange={e => setOnlyActive(e.target.checked)} data-testid="fonly-active" /> só com flow ativo</label>
                </div>
                <div className="border border-[#1E293B] rounded mt-2 max-h-[52vh] overflow-y-auto">
                  {busy && !cand && <div className="p-4 text-xs text-slate-500 flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" />Carregando…</div>}
                  {cand && rows.length === 0 && <div className="p-4 text-xs text-slate-500">{onlyActive ? "Nenhuma interface com flow chegando deste exportador. Desmarque \"só com flow ativo\" para ver todas." : "Nenhuma interface."}</div>}
                  <table className="w-full text-xs font-mono">
                    <tbody>
                      {rows.map(i => (
                        <tr key={i.index} className="border-b border-[#1E293B] last:border-0" data-testid={`fcand-${i.index}`}>
                          <td className="px-2 py-1.5">
                            <div className="text-slate-100 flex items-center gap-1.5">{i.name || `ifIndex ${i.index}`}
                              {i.active && <span className="text-[9px] px-1 rounded border border-[#199e70]/60 text-[#5fd3a7]">flow ativo</span>}</div>
                            <div className="text-[10px] text-slate-500 truncate max-w-[220px]">#{i.index} {i.alias}</div>
                          </td>
                          <td className="px-2 text-right whitespace-nowrap text-slate-300">{i.active ? <>↓ {fmtRate(i.in_bps)}<br />↑ {fmtRate(i.out_bps)}</> : <span className="text-slate-600">—</span>}</td>
                          <td className="px-2">{!i.monitored && (
                            <select value={rowRole[i.index] || "transito"} onChange={e => setRowRole({ ...rowRole, [i.index]: e.target.value })} className={selCls} data-testid={`frole-${i.index}`}>
                              {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                            </select>)}</td>
                          <td className="px-2">{!i.monitored && <Input value={rowLabel[i.index] ?? (i.alias || "")} onChange={e => setRowLabel({ ...rowLabel, [i.index]: e.target.value })} placeholder="nome (ex.: Trânsito X)" className={`${inputCls} h-8 text-xs w-44`} />}</td>
                          <td className="px-2 text-right">
                            {i.monitored ? <span className="text-[11px] text-[#5fd3a7] flex items-center gap-1 justify-end"><Check className="w-3.5 h-3.5" />monitorada</span>
                              : <Button size="sm" onClick={() => add(i)} className="h-7 text-xs bg-[#007AFF] hover:bg-[#0062CC]" data-testid={`fadd-${i.index}`}>Monitorar</Button>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="text-[10px] text-slate-500 mt-1.5">"Flow ativo" = o coletor está recebendo flows com essa interface de entrada ou saída (atualiza a cada 30 s).
                  Juniper: escolha a unidade lógica (ex.: xe-0/0/1.0).</div>
              </>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Descoberta: interfaces que já mandam flow e não estão monitoradas, com papel sugerido pela descrição. */
function DiscoverDialog({ onClose, onAdded }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState({});
  const [role, setRole] = useState({});
  const [label, setLabel] = useState({});
  const k = (r) => `${r.exporter}|${r.if_index}`;
  const load = async (refresh = false) => {
    setBusy(true);
    try {
      const { data } = await api.get("/flow/discover", { params: { refresh } });
      setRes(data);
      const s = {}, ro = {}, la = {};
      data.devices.forEach(d => d.items.forEach(r => { s[k(r)] = !!r.role; ro[k(r)] = r.role || "outro"; la[k(r)] = r.alias || ""; }));
      setSel(s); setRole(ro); setLabel(la);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const apply = async () => {
    const items = [];
    (res?.devices || []).forEach(d => d.items.forEach(r => {
      if (sel[k(r)]) items.push({ device_id: d.device_id, exporter: r.exporter, if_index: r.if_index, if_name: r.if_name, role: role[k(r)], label: label[k(r)] });
    }));
    if (!items.length) return toast.error("Marque pelo menos uma interface");
    setBusy(true);
    try {
      const { data } = await api.post("/flow/discover/apply", { items });
      toast.success(`${data.added} interface(s) monitorada(s)` + (data.skipped.length ? ` · ${data.skipped.length} já estavam` : ""));
      onAdded(); onClose();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const count = Object.values(sel).filter(Boolean).length;
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-5xl max-h-[90vh] overflow-y-auto" data-testid="discover-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Wand2 className="w-5 h-5 text-[#4DA3FF]" /> Descobrir interfaces com flow</DialogTitle></DialogHeader>
        {!res ? <Loader2 className="w-5 h-5 animate-spin text-slate-500" /> : (
          <div className="space-y-3">
            <div className="text-xs text-slate-400">Interfaces que os roteadores já estão exportando (acima de {res.min_mbps} Mb/s) e ainda não estão monitoradas.
              O papel vem da descrição da interface (SNMP) — confira antes de adicionar.
              <button onClick={() => load(true)} disabled={busy} className="ml-2 text-[#4DA3FF] hover:underline inline-flex items-center gap-1"><RefreshCw className="w-3 h-3" /> reler nomes via SNMP</button></div>
            {res.total === 0 && <div className="text-sm text-slate-400 p-4 border border-dashed border-[#1E293B] rounded" data-testid="discover-empty">Nada novo: tudo que manda flow já está monitorado.</div>}
            {res.devices.map(d => (
              <div key={d.device_id + d.exporter} className="border border-[#1E293B] rounded" data-testid="discover-device">
                <div className="px-3 py-1.5 text-sm text-slate-100 border-b border-[#1E293B] flex gap-2">{d.device_name}<span className="text-xs font-mono text-slate-500">{d.exporter}</span>
                  {!d.snmp_cached && <span className="text-[11px] text-amber-300 ml-auto">sem nomes via SNMP</span>}</div>
                <table className="w-full text-xs font-mono">
                  <tbody>
                    {d.items.map(r => (
                      <tr key={k(r)} className="border-t border-[#1E293B]/60" data-testid="discover-row">
                        <td className="pl-3 py-1 w-6"><input type="checkbox" checked={!!sel[k(r)]} onChange={e => setSel({ ...sel, [k(r)]: e.target.checked })} /></td>
                        <td className="pr-2"><div className="text-slate-100">{r.if_name}</div><div className="text-[10px] text-slate-500">{r.alias || "sem descrição"} · ifIndex {r.if_index}</div></td>
                        <td className="text-right text-slate-200 whitespace-nowrap pr-3">↓ {fmtRate(r.in_bps)} <span className="text-slate-500">↑ {fmtRate(r.out_bps)}</span></td>
                        <td className="pr-2"><select value={role[k(r)]} onChange={e => setRole({ ...role, [k(r)]: e.target.value })} className={selCls} data-testid="discover-role">
                          {Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
                          <div className="text-[10px] text-slate-500 mt-0.5">{r.role ? `sugerido: ${r.why}` : "sem sugestão"}</div></td>
                        <td className="pr-3"><Input value={label[k(r)]} onChange={e => setLabel({ ...label, [k(r)]: e.target.value })} className={`${inputCls} h-8 text-xs w-44`} placeholder="nome" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
            {res.orphans.length > 0 && <div className="text-[11px] text-amber-300" data-testid="discover-orphans">Exportadores sem equipamento cadastrado: {res.orphans.map(o => o.exporter).join(", ")} — cadastre o equipamento com esse IP (ou o IP de gerência) para descobrir as interfaces dele.</div>}
            {Object.keys(res.snmp_errors || {}).length > 0 && <div className="text-[11px] text-slate-500">SNMP: {Object.entries(res.snmp_errors).map(([n, e]) => `${n}: ${e}`).join(" · ")}</div>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>Fechar</Button>
              <Button onClick={apply} disabled={busy || !count} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="discover-apply">
                {busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />} Adicionar {count} interface(s)</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Aba Interfaces: o que está monitorado, papel (trânsito, PNI, IX…), nome e tráfego ao vivo. */
export function FlowInterfaces({ ifaces, liveAt, reload }) {
  const [devices, setDevices] = useState([]);
  const [dlg, setDlg] = useState(false);
  const [disc, setDisc] = useState(false);
  useEffect(() => { api.get("/devices").then(r => setDevices(r.data)).catch(() => {}); }, []);
  const upd = async (i, p) => {
    try { await api.put(`/flow/interfaces/${i.id}`, p); await reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (i) => {
    if (!window.confirm(`Parar de monitorar ${i.device_name} · ${i.if_name}? O histórico dela deixa de aparecer.`)) return;
    try { await api.delete(`/flow/interfaces/${i.id}`); await reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <div data-testid="flow-ifaces">
      <div className="flex items-center gap-2 mb-3">
        <Button size="sm" onClick={() => setDisc(true)} className="h-8 bg-[#007AFF] hover:bg-[#0062CC]" data-testid="flow-discover"><Wand2 className="w-4 h-4 mr-1" /> Descobrir interfaces</Button>
        <Button size="sm" variant="outline" onClick={() => setDlg(true)} className="h-8 border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800" data-testid="flow-add-iface"><Plus className="w-4 h-4 mr-1" /> Adicionar manualmente</Button>
        <span className="text-[11px] text-slate-500 font-mono ml-auto">ao vivo: média de 60 s · {liveAt ? `atualizado ${ago(liveAt)}` : "coletor sem dados"}</span>
      </div>
      {ifaces.length === 0 ? (
        <div className="text-sm text-slate-400 p-6 border border-dashed border-[#1E293B] rounded">Nenhuma interface ainda. Configure o roteador para mandar NetFlow/IPFIX (UDP 2055) ou sFlow (UDP 6343) para este servidor
          (veja Configuração) e clique em <b>Adicionar interfaces</b>: as que já estão mandando flow aparecem marcadas.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono" data-testid="flow-iface-table">
            <thead className="text-[10px] uppercase tracking-widest text-slate-500"><tr>
              <th className="text-left py-1">Equipamento · interface</th><th className="text-left">Exportador</th><th className="text-left">Papel</th><th className="text-left">Nome</th>
              <th className="text-right">Entrada ↓</th><th className="text-right">Saída ↑</th><th /></tr></thead>
            <tbody>
              {ifaces.map(i => (
                <tr key={i.id} className="border-t border-[#1E293B]" data-testid={`fif-${i.id}`}>
                  <td className="py-1.5 pr-2"><div className="text-slate-100">{i.device_name} · {i.if_name}</div><div className="text-[10px] text-slate-500">ifIndex {i.if_index}</div></td>
                  <td className="text-slate-400 pr-2">{i.exporter}</td>
                  <td className="pr-2"><select value={i.role} onChange={e => upd(i, { role: e.target.value })} className={selCls}>{Object.entries(ROLE_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
                  <td className="pr-2"><Input defaultValue={i.label} onBlur={e => e.target.value !== i.label && upd(i, { label: e.target.value })} className={`${inputCls} h-8 text-xs w-44`} /></td>
                  <td className="text-right text-slate-100 whitespace-nowrap">{fmtRate(i.live?.in?.bps)}</td>
                  <td className="text-right text-slate-300 whitespace-nowrap">{fmtRate(i.live?.out?.bps)}</td>
                  <td className="text-right pl-2"><button onClick={() => del(i)} className="text-slate-500 hover:text-red-400" title="Parar de monitorar"><Trash2 className="w-3.5 h-3.5" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dlg && <AddDialog devices={devices} onClose={() => setDlg(false)} onAdded={reload} />}
      {disc && <DiscoverDialog onClose={() => setDisc(false)} onAdded={reload} />}
    </div>
  );
}
