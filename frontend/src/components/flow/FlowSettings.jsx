import React, { useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { Save, Loader2, Download, Upload, Copy, Database, Radio, ShieldAlert, Router } from "lucide-react";
import { ago, chip, inputCls } from "@/components/flow/flowlib";
import { STATUS } from "@/lib/netfmt";

const host = () => window.location.hostname || "IP_DO_BASTION";

const SNIPPETS = {
  huawei: (ip, nf) => `ip netstream sampler fix-packets 1000 inbound
ip netstream export version 9 origin-as
ip netstream export source 10.255.0.1
ip netstream export host ${ip} ${nf}
ip netstream timeout active 1
ip netstream timeout inactive 15
#
interface 100GE0/0/1
 ip netstream inbound
#
# NE40E/ME60: em cada placa ->  slot N  /  ip netstream sampler to slot self
# IPv6: os mesmos comandos com "ipv6 netstream"`,
  juniper: (ip, nf) => `set chassis fpc 0 sampling-instance BASTION
set services flow-monitoring version-ipfix template V4 ipv4-template
set services flow-monitoring version-ipfix template V4 flow-active-timeout 60
set services flow-monitoring version-ipfix template V4 flow-inactive-timeout 15
set services flow-monitoring version-ipfix template V6 ipv6-template
set services flow-monitoring version-ipfix template V6 flow-active-timeout 60
set services flow-monitoring version-ipfix template V6 flow-inactive-timeout 15
set forwarding-options sampling instance BASTION input rate 1000
set forwarding-options sampling instance BASTION family inet output flow-server ${ip} port ${nf} version-ipfix template V4
set forwarding-options sampling instance BASTION family inet output inline-jflow source-address 10.255.0.1
set forwarding-options sampling instance BASTION family inet6 output flow-server ${ip} port ${nf} version-ipfix template V6
set forwarding-options sampling instance BASTION family inet6 output inline-jflow source-address 10.255.0.1
set interfaces xe-0/0/1 unit 0 family inet sampling input
set interfaces xe-0/0/1 unit 0 family inet6 sampling input`,
  ciscoxe: (ip, nf) => `flow record BASTION
 match ipv4 source address
 match ipv4 destination address
 match ipv4 protocol
 match transport source-port
 match transport destination-port
 match interface input
 match flow direction
 collect interface output
 collect transport tcp flags
 collect routing source as
 collect routing destination as
 collect counter bytes long
 collect counter packets long
flow exporter BASTION
 destination ${ip}
 source Loopback0
 transport udp ${nf}
 option sampler-table
flow monitor BASTION
 exporter BASTION
 record BASTION
 cache timeout active 60
sampler S1000
 mode random 1 out-of 1000
interface TenGigabitEthernet0/0/1
 ip flow monitor BASTION sampler S1000 input`,
  ciscoxr: (ip, nf) => `flow exporter-map BASTION
 version v9
  options sampler-table
 !
 transport udp ${nf}
 source Loopback0
 destination ${ip}
!
flow monitor-map BASTION-V4
 record ipv4
 exporter BASTION
 cache timeout active 60
 cache timeout inactive 15
!
sampler-map S1000
 random 1 out-of 1000
!
interface HundredGigE0/0/0/1
 flow ipv4 monitor BASTION-V4 sampler S1000 ingress`,
  sflow: (ip, nf, sf) => `Datacom DMOS / switches com sFlow:
  coletor ........ ${ip}  porta ${sf}
  agente ......... IP da loopback (é por ele que o Bastion identifica o equipamento)
  amostragem ..... 1:1000 a 1:4096 nas interfaces externas (trânsito, PNI, IX)
  counter-poll ... opcional (o Bastion usa só as amostras de pacote)
ZTE ZXR10: NetFlow v9 ou IPFIX para ${ip} porta ${nf}, amostragem na entrada, timeout ativo 60 s.
A sintaxe muda entre versões de firmware — confira com "?" no equipamento; a tabela de exportadores
acima mostra na hora se chegou, o tipo e a taxa de amostragem lida.`,
};
const VENDORS = [["huawei", "Huawei"], ["juniper", "Juniper MX"], ["ciscoxe", "Cisco IOS-XE"], ["ciscoxr", "Cisco IOS-XR"], ["sflow", "Datacom / ZTE / sFlow"]];

function Exporters({ s, onSampling }) {
  const [list, setList] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.get("/flow/exporters").then(r => alive && setList(r.data)).catch(() => {});
    load(); const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return (
    <div className="border border-[#1E293B] rounded" data-testid="flow-exporters">
      <div className="px-3 py-2 border-b border-[#1E293B] flex items-center gap-2 text-sm text-slate-100"><Radio className="w-4 h-4 text-slate-400" /> Exportadores recebidos
        <span className="ml-auto text-[11px] font-mono text-slate-500">UDP {s.netflow_port} (NetFlow/IPFIX) · {s.sflow_port} (sFlow)</span></div>
      {list && list.length === 0 && <div className="p-3 text-xs text-slate-500">Nada chegou ainda. Confira o roteador, o firewall do servidor (<code className="text-slate-300">ufw allow from IP_DO_ROTEADOR to any port {s.netflow_port} proto udp</code>) e se o container <b>flow</b> está rodando.</div>}
      {list && list.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead className="text-[10px] uppercase tracking-widest text-slate-500"><tr>
              <th className="text-left px-3 py-1">IP</th><th className="text-left">Tipo</th><th className="text-right">Amostragem</th><th className="text-right">Pacotes/s</th>
              <th className="text-right">Flows/s</th><th className="text-right">Interfaces</th><th className="text-left pl-3">Último</th><th className="text-left">Avisos</th>{s.is_admin && <th className="text-left">Taxa fixa</th>}</tr></thead>
            <tbody>
              {list.map(e => {
                const stale = !e.last || Date.now() - new Date(e.last).getTime() > 120000;
                return (
                  <tr key={e.ip} className="border-t border-[#1E293B]" data-testid={`exp-${e.ip}`}>
                    <td className="px-3 py-1.5 text-slate-100">{e.ip}{e.src && e.src !== e.ip && <div className="text-[10px] text-slate-500">via {e.src}</div>}</td>
                    <td className="text-slate-300">{({ v5: "NetFlow v5", v9: "NetFlow v9", ipfix: "IPFIX", sflow: "sFlow v5" })[e.kind] || e.kind}{e.bidir && <span className="text-[10px] text-slate-500"> · entrada+saída</span>}</td>
                    <td className="text-right text-slate-200">{e.rate ? `1:${e.rate}` : <span className="text-amber-300">?</span>}</td>
                    <td className="text-right text-slate-300">{e.pps}</td>
                    <td className="text-right text-slate-300">{e.fps}</td>
                    <td className="text-right text-slate-300">{e.n_ifs}</td>
                    <td className="pl-3 whitespace-nowrap" style={{ color: stale ? STATUS.warning : undefined }}>{stale && "⚠ "}<span className={stale ? "" : "text-slate-400"}>{ago(e.last)}</span></td>
                    <td className="text-[11px] text-amber-300 max-w-[260px] truncate" title={`${e.no_template ? `${e.no_template} registros sem template. ` : ""}${e.errors ? `${e.errors} pacotes inválidos: ${e.last_error || ""}. ` : ""}${!e.rate ? "Amostragem não informada pelo equipamento." : ""}`}>{e.no_template ? `${e.no_template} registros sem template (aguardando) ` : ""}{e.errors ? `${e.errors} pacotes inválidos: ${e.last_error || ""}` : ""}{!e.rate ? "amostragem não informada: defina a taxa fixa" : ""}</td>
                    {s.is_admin && <td><Input defaultValue={s.sampling?.[e.ip] || ""} placeholder="auto" onBlur={ev => onSampling(e.ip, ev.target.value)} className={`${inputCls} h-7 w-20 text-xs font-mono`} data-testid={`samp-${e.ip}`} /></td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AsnBase({ s, reload }) {
  const [busy, setBusy] = useState(false);
  const file = useRef(null);
  const run = async (fn) => { setBusy(true); try { const { data } = await fn(); toast.success(`Base de ASN atualizada: ${data.ranges_v4.toLocaleString("pt-BR")} faixas IPv4, ${data.ranges_v6.toLocaleString("pt-BR")} IPv6`); await reload(); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); } };
  const up = (f) => { const fd = new FormData(); fd.append("file", f); run(() => api.post("/flow/asn/upload", fd)); };
  const a = s.asn;
  return (
    <div className="border border-[#1E293B] rounded p-3" data-testid="flow-asn">
      <div className="flex items-center gap-2 text-sm text-slate-100 mb-1"><Database className="w-4 h-4 text-slate-400" /> Base IP → ASN <span className="text-[11px] text-slate-500 font-mono">(iptoasn.com, domínio público)</span></div>
      <div className="text-xs font-mono text-slate-300 mb-2">
        {a ? <>{a.ranges_v4?.toLocaleString("pt-BR")} faixas IPv4 · {a.ranges_v6?.toLocaleString("pt-BR")} IPv6 · {a.asns?.toLocaleString("pt-BR")} ASNs · atualizada {ago(a.at)} ({a.source})</>
           : <span className="text-amber-300">Ainda não baixada: sem ela, o AS só aparece se o roteador mandar no flow (origin-as).</span>}
        {s.asn_status?.state === "erro" && <div className="text-amber-300 mt-1">Última tentativa falhou: {s.asn_status.error}</div>}
      </div>
      {s.is_admin && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => run(() => api.post("/flow/asn/update"))} disabled={busy} className="h-8 bg-[#007AFF] hover:bg-[#0062CC]" data-testid="asn-update">
            {busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Download className="w-4 h-4 mr-1" />}Atualizar base de ASN</Button>
          <Button size="sm" variant="ghost" onClick={() => file.current?.click()} disabled={busy} className="h-8 text-slate-300 hover:bg-slate-800"><Upload className="w-4 h-4 mr-1" />Enviar arquivo</Button>
          <input ref={file} type="file" accept=".gz,.tsv,.txt" className="hidden" onChange={e => e.target.files?.[0] && up(e.target.files[0])} />
          <span className="text-[11px] text-slate-500">sem internet no servidor? baixe <code>ip2asn-combined.tsv.gz</code> em outro PC e envie aqui</span>
        </div>
      )}
    </div>
  );
}

/** Aba Configuração: exportadores, detector de ataques, prefixos, retenção, base de ASN e configuração dos roteadores. */
export function FlowSettings({ settings, reload }) {
  const [f, setF] = useState(null);
  const [live, setLive] = useState(null);     // estado do coletor, consultado sempre (a idade vem do relógio do servidor)
  useEffect(() => {
    let alive = true;
    const load = () => api.get("/flow/live").then(r => alive && setLive(r.data)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const [busy, setBusy] = useState(false);
  const [vendor, setVendor] = useState("huawei");
  useEffect(() => {
    if (!settings) return;
    setF({ ...settings, own_txt: (settings.own_prefixes || []).join("\n"), ign_txt: (settings.ignore_prefixes || []).join("\n"),
           att: { ...settings.attack, bps_g: settings.attack.bps / 1e9, amp_m: settings.attack.amp_bps / 1e6 } });
  }, [settings]);
  if (!f) return null;
  const s = settings;
  const ro = !s.is_admin;
  const body = (over = {}) => ({
    netflow_port: Number(f.netflow_port), sflow_port: Number(f.sflow_port), retention_days: Number(f.retention_days), hourly_days: Number(f.hourly_days),
    own_prefixes: f.own_txt.split(/[\s,;]+/).filter(Boolean), ignore_prefixes: f.ign_txt.split(/[\s,;]+/).filter(Boolean),
    sampling: f.sampling || {}, asn_auto: !!f.asn_auto,
    attack: { enabled: !!f.att.enabled, pps: Number(f.att.pps), bps: Math.round(Number(f.att.bps_g) * 1e9), amp_bps: Math.round(Number(f.att.amp_m) * 1e6),
              syn_pps: Number(f.att.syn_pps), min_windows: Number(f.att.min_windows), end_windows: Number(f.att.end_windows), avg_windows: Number(f.att.avg_windows) },
    ...over,
  });
  const save = async (over) => {
    setBusy(true);
    try { await api.put("/flow/settings", body(over)); toast.success("Configuração do Flow salva (o coletor aplica em até 30 s)"); await reload(); }
    catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const onSampling = (ip, v) => {
    const cur = { ...(f.sampling || {}) };
    const n = parseInt(v, 10);
    if (n > 0) cur[ip] = n; else delete cur[ip];
    if (JSON.stringify(cur) === JSON.stringify(f.sampling || {})) return;
    setF({ ...f, sampling: cur });
    save({ sampling: cur });
  };
  const setAtt = (p) => setF({ ...f, att: { ...f.att, ...p } });
  const num = (label, val, on, hint, tid) => (
    <div><Label className="text-xs">{label}</Label><Input type="number" value={val} onChange={e => on(e.target.value)} disabled={ro} className={`${inputCls} h-8 font-mono text-sm`} data-testid={tid} />{hint && <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>}</div>
  );
  const collectorOk = live ? live.age_sec !== null && live.age_sec < 60 : !!s.collector_at;
  const snippet = SNIPPETS[vendor](host(), s.netflow_port, s.sflow_port);

  return (
    <div className="space-y-4 max-w-6xl" data-testid="flow-settings">
      <div className="flex items-center gap-2 text-sm" data-testid="collector-status">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: collectorOk ? STATUS.good : STATUS.critical }} />
        <b style={{ color: collectorOk ? STATUS.good : STATUS.critical }}>{collectorOk ? "Coletor ativo" : "Coletor parado"}</b>
        <span className="text-xs text-slate-400">{collectorOk ? `última gravação há ${Math.max(0, Math.round(live?.age_sec ?? 0))} s` : "o container flow não está gravando — no servidor: cd /opt/bastion/deploy && docker compose ps (e docker compose logs flow)"}</span>
      </div>
      <Exporters s={s} onSampling={onSampling} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="border border-[#1E293B] rounded p-3 space-y-3">
          <div className="flex items-center gap-2 text-sm text-slate-100"><ShieldAlert className="w-4 h-4 text-slate-400" /> Detector de ataques
            <label className="ml-auto flex items-center gap-2 text-xs text-slate-300">ativo <Switch checked={!!f.att.enabled} onCheckedChange={v => setAtt({ enabled: v })} disabled={ro} /></label></div>
          <div className="grid grid-cols-2 gap-3">
            {num("Volume (Gb/s) por IP", f.att.bps_g, v => setAtt({ bps_g: v }), null, "att-bps")}
            {num("Pacotes/s por IP", f.att.pps, v => setAtt({ pps: v }), null, "att-pps")}
            {num("Amplificação (Mb/s)", f.att.amp_m, v => setAtt({ amp_m: v }), "NTP, DNS, SSDP, Memcached, CLDAP… vindo para 1 IP", "att-amp")}
            {num("SYN/s por IP", f.att.syn_pps, v => setAtt({ syn_pps: v }), null, "att-syn")}
            {num("Janelas para começar", f.att.min_windows, v => setAtt({ min_windows: v }), "de 10 s", "att-min")}
            {num("Janelas calmas para terminar", f.att.end_windows, v => setAtt({ end_windows: v }), "de 10 s", "att-end")}
          </div>
          <div>
            <Label className="text-xs">Prefixos próprios (o detector só olha tráfego que entra para eles)</Label>
            <Textarea value={f.own_txt} onChange={e => setF({ ...f, own_txt: e.target.value })} disabled={ro} rows={4} placeholder={"177.10.0.0/20\n2804:1::/32"} className={`${inputCls} font-mono text-xs`} data-testid="own-prefixes" />
            <div className="text-[10px] text-slate-500 mt-0.5">Vazio: considera tudo que entra pelas interfaces de trânsito, PNI, IX e CDN.</div>
          </div>
          <div>
            <Label className="text-xs">IPs/blocos ignorados pelo detector</Label>
            <Textarea value={f.ign_txt} onChange={e => setF({ ...f, ign_txt: e.target.value })} disabled={ro} rows={2} placeholder="ex.: pool CGNAT de alto volume, caches" className={`${inputCls} font-mono text-xs`} />
          </div>
        </div>
        <div className="space-y-4">
          <div className="border border-[#1E293B] rounded p-3 space-y-3">
            <div className="text-sm text-slate-100">Coletor e retenção</div>
            <div className="grid grid-cols-2 gap-3">
              {num("Porta NetFlow/IPFIX (UDP)", f.netflow_port, v => setF({ ...f, netflow_port: v }))}
              {num("Porta sFlow (UDP)", f.sflow_port, v => setF({ ...f, sflow_port: v }))}
              {num("Dias de detalhe (1 e 5 min)", f.retention_days, v => setF({ ...f, retention_days: v }), "1 a 60")}
              {num("Dias da junção por hora", f.hourly_days, v => setF({ ...f, hourly_days: v }), "7 a 730")}
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={!!f.asn_auto} onCheckedChange={v => setF({ ...f, asn_auto: v })} disabled={ro} /> atualizar a base de ASN sozinho toda semana</label>
          </div>
          <AsnBase s={s} reload={reload} />
        </div>
      </div>
      {s.is_admin ? (
        <Button onClick={() => save()} disabled={busy} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="flow-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar configuração</Button>
      ) : <div className="text-xs text-slate-500">Só o administrador altera a configuração do coletor.</div>}

      <div className="border border-[#1E293B] rounded" data-testid="router-config">
        <div className="px-3 py-2 border-b border-[#1E293B] flex flex-wrap items-center gap-2 text-sm text-slate-100"><Router className="w-4 h-4 text-slate-400" /> Configuração dos roteadores
          <div className="flex flex-wrap gap-1 ml-auto">{VENDORS.map(([v, l]) => <button key={v} onClick={() => setVendor(v)} className={chip(vendor === v)} data-testid={`vendor-${v}`}>{l}</button>)}</div></div>
        <div className="relative">
          <pre className="text-[11px] font-mono text-slate-200 bg-[#05070A] p-3 overflow-x-auto whitespace-pre" data-testid="router-snippet">{snippet}</pre>
          <button onClick={() => { navigator.clipboard?.writeText(snippet); toast.success("Copiado"); }} className="absolute top-2 right-2 text-slate-400 hover:text-slate-100" title="Copiar"><Copy className="w-4 h-4" /></button>
        </div>
        <div className="px-3 py-2 text-[11px] text-slate-500 border-t border-[#1E293B]">
          Troque 10.255.0.1 pela loopback do roteador e a interface pelos seus trânsitos/PNIs. Regras: amostragem na <b>entrada</b> das interfaces externas,
          timeout ativo de 60 s, e libere o UDP no servidor só para os roteadores: <code className="text-slate-300">ufw allow from IP_DO_ROTEADOR to any port {s.netflow_port} proto udp</code>.
        </div>
      </div>
    </div>
  );
}
