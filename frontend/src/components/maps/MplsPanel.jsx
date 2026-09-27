import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { X, Loader2, FlaskConical, Save, FileText, ChevronDown, ChevronRight } from "lucide-react";
import { STATUS } from "@/lib/netfmt";

const SEC_ORDER = ["ldp_session", "ldp_iface", "vpws", "vpls", "vrf", "bgp_vpn"];
const SEC_LABEL = { ldp_session: "Sessões LDP", ldp_iface: "Interfaces LDP", vpws: "VPWS (l2vc)", vpls: "VPLS (vsi)", vrf: "L3VPN (VRF)", bgp_vpn: "MP-BGP VPN" };
const VENDORS = [["huawei", "Huawei"], ["juniper", "Juniper"], ["cisco", "Cisco"], ["datacom", "Datacom DMOS"], ["zte", "ZTE"]];
const stColor = (s) => (s === "up" ? STATUS.good : s === "down" ? STATUS.critical : s === "degraded" ? STATUS.warning : "#94A3B8");
const Yes = ({ v }) => (v === true ? <span style={{ color: STATUS.good }}>✓</span> : v === false ? <span style={{ color: STATUS.critical }}>✕</span> : <span className="text-slate-600">—</span>);

function RawModal({ title, text, onClose }) {
  return (
    <div className="fixed inset-0 z-[130] bg-black/70 flex items-center justify-center p-3" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-4xl max-h-[88vh] flex flex-col bg-[#111722] border border-[#2A3345] rounded-lg">
        <div className="flex items-center px-4 py-2 border-b border-[#1E293B]">
          <div className="text-sm font-semibold text-slate-100">{title}</div>
          <button onClick={onClose} className="ml-auto text-slate-400 hover:text-slate-100"><X className="w-4 h-4" /></button>
        </div>
        <pre className="flex-1 overflow-auto p-3 text-[11px] font-mono text-slate-300 whitespace-pre-wrap" data-testid="mpls-raw">{text}</pre>
      </div>
    </div>
  );
}

function SectionItems({ sec, items, nameOf }) {
  if (!items.length) return <div className="text-[11px] text-slate-500 px-2 pb-1">nenhum</div>;
  const row = "flex items-center gap-2 px-2 py-0.5 text-[11px] font-mono";
  if (sec === "ldp_session") return items.map((x, i) => <div key={i} className={row}><span className="text-slate-300">{nameOf(x.peer)}</span><span className="text-slate-500">{x.peer}</span><span className="ml-auto" style={{ color: stColor(x.state) }}>{x.raw_state || x.state}</span></div>);
  if (sec === "ldp_iface") return <div className="px-2 text-[11px] font-mono text-slate-400">{items.map(x => x.iface + (x.active ? "" : " (inativa)")).join(" · ")}</div>;
  if (sec === "vpws") return items.map((x, i) => <div key={i} className={row}><span className="text-slate-200">VC {x.vcid}</span><span className="text-slate-500 truncate">{x.name || x.iface}</span><span className="text-slate-400">→ {nameOf(x.peer)}</span>
    <span className="ml-auto" style={{ color: stColor(x.state) }}>{x.state}{x.ac === "down" ? " (AC down)" : ""}</span></div>);
  if (sec === "vpls") return items.map((x, i) => <div key={i} className={row}><span className="text-slate-200 truncate">{x.name}</span>{x.pws ? <span className="text-slate-500">PWs {x.pws_up}/{x.pws}</span> : null}<span className="ml-auto" style={{ color: stColor(x.state) }}>{x.state}</span></div>);
  if (sec === "vrf") return items.map((x, i) => <div key={i} className={row}><span className="text-slate-200 truncate">{x.name}</span><span className="text-slate-500">{x.rd || ""}</span><span className="ml-auto text-slate-400" style={{ color: x.routes === 0 ? STATUS.warning : undefined }}>{x.routes == null ? "" : `${x.routes} rotas`}</span></div>);
  if (sec === "bgp_vpn") return items.map((x, i) => <div key={i} className={row}><span className="text-slate-300">{nameOf(x.peer)}</span><span className="text-slate-500">{x.peer}</span><span className="text-slate-500">{x.prefixes != null ? `${x.prefixes} pfx` : ""}</span><span className="ml-auto" style={{ color: stColor(x.state) }}>{x.state === "up" ? "established" : "down"}</span></div>);
  return null;
}

/** Aba MPLS do diagnóstico. */
export function MplsTab({ rep, rid, onSelectLink, selId, onOpenSettings }) {
  const mp = rep.mpls;
  const [open, setOpen] = useState({});
  const [raw, setRaw] = useState(null);
  if (!mp) return <div className="text-center text-slate-500 py-8 text-xs">MPLS não foi lido nesta análise (opção desligada ou nenhum equipamento Huawei/Juniper/Cisco/Datacom/ZTE).</div>;
  const s = mp.summary;
  const ipName = {};
  (rep.devices || []).forEach(d => { if (d.router_id) ipName[d.router_id] = d.name; });
  const nameOf = (ip) => ipName[ip] || ip || "?";
  const showRaw = async (d) => {
    try { const { data } = await api.get(`/analysis/${rid}/raw/${d.id}`);
      setRaw({ title: `${d.name} · saída da CLI`, text: SEC_ORDER.map(k => data.sections?.[k] ? `======== ${SEC_LABEL[k]} ${data.sections[k].ok ? "(entendido)" : "(NÃO entendido)"}\n${data.sections[k].raw || ""}` : "").filter(Boolean).join("\n\n") || data.error || "vazio" });
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const Stat = ({ l, v, bad }) => <div className="rounded border border-[#1E293B] bg-[#0B111C] px-2 py-1"><div className="text-[9px] uppercase tracking-widest text-slate-500 font-mono">{l}</div><div className="text-sm font-semibold" style={{ color: bad ? STATUS.critical : "#E2E8F0" }}>{v}</div></div>;
  return (
    <div className="space-y-3" data-testid="mpls-tab">
      <div className="grid grid-cols-3 gap-1.5">
        <Stat l="LDP up" v={`${s.ldp_up}/${s.ldp_total}`} bad={s.ldp_up < s.ldp_total} />
        <Stat l="enlaces sem LDP" v={s.links_no_ldp} bad={s.links_no_ldp > 0} />
        <Stat l="VPWS up" v={`${s.vc_up}/${s.vc_total}`} bad={s.vc_up < s.vc_total} />
        <Stat l="VPLS up" v={`${s.vsi_up}/${s.vsi_total}`} bad={s.vsi_up < s.vsi_total} />
        <Stat l="VRFs" v={s.vrfs} />
        <Stat l="MP-BGP VPN" v={`${s.bgp_vpn_up}/${s.bgp_vpn_total}`} bad={s.bgp_vpn_up < s.bgp_vpn_total} />
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-1">LDP por enlace</div>
        <table className="w-full font-mono text-xs">
          <thead className="text-[10px] uppercase text-slate-500"><tr><th className="text-left px-2">Enlace</th><th className="px-1">A</th><th className="px-1">B</th><th className="text-right px-2">sessão</th></tr></thead>
          <tbody>
            {rep.links.map(l => (
              <tr key={l.id} onClick={() => onSelectLink(l.id)} className={`border-t border-[#1E293B] cursor-pointer hover:bg-slate-800/40 ${selId === l.id ? "bg-[#007AFF]/10" : ""}`}>
                <td className="px-2 py-1 text-slate-200 truncate max-w-[220px]">{l.a_name} ↔ {l.b_name}{!l.in_spf && <span className="text-slate-500"> · sem OSPF</span>}</td>
                <td className="text-center"><Yes v={l.ldp_a} /></td><td className="text-center"><Yes v={l.ldp_b} /></td>
                <td className="px-2 text-right" style={{ color: stColor(l.ldp_session) }}>{l.ldp_session || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="flex items-center mb-1">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">Por equipamento</div>
          <button onClick={onOpenSettings} className="ml-auto text-[11px] text-[#4DA3FF] underline" data-testid="mpls-open-settings">comandos por fabricante</button>
        </div>
        {mp.devices.map(d => {
          const secs = d.sections || {};
          const isOpen = open[d.id];
          const bad = SEC_ORDER.filter(k => secs[k] && !secs[k].ok).length;
          return (
            <div key={d.id} className="rounded border border-[#1E293B] bg-[#0B111C] mb-1.5">
              <button className="w-full flex items-center gap-2 px-2 py-1.5 text-left" onClick={() => setOpen(o => ({ ...o, [d.id]: !o[d.id] }))}>
                {isOpen ? <ChevronDown className="w-3.5 h-3.5 text-slate-500" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-500" />}
                <span className="text-slate-100 text-xs font-semibold">{d.name}</span>
                <span className="text-[10px] font-mono text-slate-500">{d.device_type}</span>
                {d.error ? <span className="ml-auto text-[10px] text-red-300 truncate max-w-[200px]">{d.error}</span>
                  : bad ? <span className="ml-auto text-[10px] text-amber-300">{bad} seção(ões) não lida(s)</span> : <span className="ml-auto text-[10px] text-slate-500">ok</span>}
              </button>
              {isOpen && (
                <div className="pb-2">
                  {SEC_ORDER.filter(k => secs[k]).map(k => (
                    <div key={k} className="mt-1">
                      <div className="flex items-center gap-2 px-2 text-[10px] uppercase tracking-widest font-mono text-slate-500">
                        {SEC_LABEL[k]} {secs[k].ok ? <span className="normal-case tracking-normal text-slate-600">· {secs[k].command}</span> : <span className="normal-case tracking-normal text-amber-300">· não entendido</span>}
                      </div>
                      {secs[k].ok && <SectionItems sec={k} items={secs[k].items} nameOf={nameOf} />}
                    </div>
                  ))}
                  {!d.error && <button onClick={() => showRaw(d)} className="ml-2 mt-1 text-[11px] text-[#4DA3FF] underline flex items-center gap-1" data-testid={`mpls-raw-${d.id}`}><FileText className="w-3 h-3" /> ver saída da CLI</button>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {raw && <RawModal title={raw.title} text={raw.text} onClose={() => setRaw(null)} />}
    </div>
  );
}

/** Comandos MPLS por fabricante + teste num equipamento (para ajustar DMOS/ZTE/firmwares diferentes). */
export function MplsSettingsDialog({ devices, onClose }) {
  const [st, setSt] = useState(null);
  const [vendor, setVendor] = useState("huawei");
  const [edit, setEdit] = useState({});
  const [busy, setBusy] = useState(false);
  const [testDev, setTestDev] = useState("");
  const [testRes, setTestRes] = useState(null);
  const [testing, setTesting] = useState(false);
  const [raw, setRaw] = useState(null);
  useEffect(() => {
    api.get("/mpls/settings").then(r => { setSt(r.data); setEdit(JSON.parse(JSON.stringify(r.data.commands))); }).catch(e => toast.error(formatApiError(e)));
  }, []);
  const save = async () => {
    setBusy(true);
    try { await api.put("/mpls/settings", { mpls_commands: edit }); toast.success("Comandos MPLS salvos"); }
    catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const test = async () => {
    setTesting(true); setTestRes(null);
    try { const { data } = await api.post(`/devices/${testDev}/mpls-test`, {}, { timeout: 300000 }); setTestRes(data); }
    catch (e) { toast.error(formatApiError(e)); }
    finally { setTesting(false); }
  };
  const cand = devices.filter(d => VENDORS.some(([v]) => v === d.device_type));
  return (
    <div className="fixed inset-0 z-[120] bg-black/70 flex items-center justify-center p-3" data-testid="mpls-settings" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-3xl max-h-[92vh] overflow-y-auto bg-[#111722] border border-[#2A3345] rounded-lg p-4 space-y-3">
        <div className="flex items-center"><div className="text-sm font-semibold text-slate-100">Comandos MPLS por fabricante</div>
          <button onClick={onClose} className="ml-auto text-slate-400 hover:text-slate-100"><X className="w-4 h-4" /></button></div>
        <p className="text-xs text-slate-400">Um comando por linha: o Bastion tenta em ordem até um responder. Use o teste abaixo para ver a saída real do seu equipamento.</p>
        {!st ? <Loader2 className="w-4 h-4 animate-spin text-slate-400" /> : <>
          <div className="flex gap-1 border-b border-[#1E293B]">
            {VENDORS.map(([v, l]) => <button key={v} onClick={() => setVendor(v)} className={`px-3 py-1.5 text-xs -mb-px border-b-2 ${vendor === v ? "border-[#007AFF] text-slate-100" : "border-transparent text-slate-400"}`}>{l}</button>)}
          </div>
          <div className="grid sm:grid-cols-2 gap-2">
            {SEC_ORDER.map(k => (
              <label key={k} className="text-[11px] text-slate-400">{SEC_LABEL[k]}
                <textarea rows={2} disabled={!st.is_admin} value={(edit[vendor]?.[k] || []).join("\n")}
                          onChange={e => setEdit(x => ({ ...x, [vendor]: { ...(x[vendor] || {}), [k]: e.target.value.split("\n") } }))}
                          className="mt-0.5 w-full bg-[#05070A] border border-[#1E293B] rounded px-2 py-1 font-mono text-[11px] text-slate-200" data-testid={`mpls-cmd-${k}`} />
              </label>
            ))}
          </div>
          {st.is_admin && <div className="flex justify-end"><Button size="sm" onClick={save} disabled={busy} className="bg-[#007AFF] hover:bg-[#0062CC]"><Save className="w-3.5 h-3.5 mr-1.5" /> Salvar comandos</Button></div>}
        </>}
        <div className="border-t border-[#1E293B] pt-3">
          <div className="text-xs text-slate-300 mb-1.5">Testar num equipamento</div>
          <div className="flex gap-2">
            <select value={testDev} onChange={e => setTestDev(e.target.value)} className="flex-1 h-8 bg-[#05070A] border border-[#1E293B] rounded px-2 text-xs" data-testid="mpls-test-dev">
              <option value="">Escolha…</option>
              {cand.map(d => <option key={d.id} value={d.id}>{d.name} ({d.device_type})</option>)}
            </select>
            <Button size="sm" onClick={test} disabled={!testDev || testing} className="h-8 bg-[#0B111C] border border-[#1E293B] text-slate-200 hover:bg-slate-800">
              {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FlaskConical className="w-3.5 h-3.5 mr-1.5" />} Testar
            </Button>
          </div>
          {testRes && (
            <div className="mt-2 space-y-1" data-testid="mpls-test-result">
              {testRes.error && <div className="text-xs text-red-300">{testRes.error}</div>}
              {SEC_ORDER.filter(k => testRes.sections?.[k]).map(k => {
                const x = testRes.sections[k];
                return (
                  <div key={k} className="flex items-center gap-2 text-[11px] font-mono">
                    <span className="w-36 text-slate-400">{SEC_LABEL[k]}</span>
                    {x.ok ? <span style={{ color: STATUS.good }}>✓ {x.items.length} item(ns) · {x.command}</span> : <span className="text-amber-300">✕ não entendido</span>}
                    <button className="ml-auto text-[#4DA3FF] underline" onClick={() => setRaw({ title: `${SEC_LABEL[k]} · saída`, text: x.raw || "(vazio)" })}>saída</button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
      {raw && <RawModal title={raw.title} text={raw.text} onClose={() => setRaw(null)} />}
    </div>
  );
}
