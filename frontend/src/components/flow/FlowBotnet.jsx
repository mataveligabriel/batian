import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Bug, ShieldCheck, ChevronDown, ChevronRight, Loader2, Settings2, UserSearch, Check, Plus, Trash2, AlertTriangle, Copy } from "lucide-react";
import { toast } from "sonner";
import { TagOptions } from "@/components/TagSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { copyText } from "@/lib/clipboard";
import { STATUS } from "@/lib/netfmt";
import { ago, fmtRate, inputCls, selCls, chip } from "@/components/flow/flowlib";

const SEV = { 3: STATUS.critical, 2: STATUS.warning, 1: "#8D9A9D" };
const KIND_HELP = {
  ddos: "O equipamento do cliente está inundando um destino na internet. Costuma ser roteador/câmera/TV box infectado obedecendo a uma botnet.",
  scan: "O cliente está varrendo a internet atrás de outros aparelhos vulneráveis — é assim que a botnet se espalha.",
  spam: "Conexões SMTP (porta 25) para muitos servidores: máquina infectada enviando spam. Derruba a reputação do seu bloco.",
  amp: "Um serviço aberto no cliente (DNS, NTP, SSDP…) está sendo usado por terceiros para amplificar ataques.",
  c2: "O cliente falou com um servidor de comando-e-controle da sua lista. Indício forte de infecção.",
};
const val = (i, v) => i.unit === "bps" ? fmtRate(v) : i.unit === "pps" ? fmtRate(v, "pps") : `${Math.round(v)} ${i.unit}`;
const lines = (a) => (a || []).join("\n");
const split = (t) => String(t || "").split(/[\s,;]+/).map(x => x.trim()).filter(Boolean);
const NUM = [["ddos_pps", "Flood: pacotes/s para um destino", "pacote médio pequeno (≤ 200 bytes)"], ["syn_pps", "SYN/s para um destino", "TCP SYN sem resposta"],
  ["ddos_bps", "Flood UDP: bits/s para um destino", "fora da porta 443"], ["scan_hosts", "Varredura: destinos por minuto", "na mesma porta de botnet"],
  ["spam_hosts", "Spam: servidores SMTP por minuto", "porta 25"], ["amp_bps", "Refletor: bits/s saindo do cliente", "resposta DNS/NTP/SSDP…"],
  ["min_windows", "Minutos seguidos para abrir", "C2 abre no primeiro"], ["end_windows", "Minutos quieto para encerrar", ""]];

function ConfigDialog({ data, onClose, onSaved }) {
  const s = data.settings;
  const [f, setF] = useState({ ...s, subscriber_prefixes: lines(s.subscriber_prefixes), c2_ips: lines(s.c2_ips), feeds: lines(s.feeds), ignore: lines(s.ignore), ignore_dst: lines(s.ignore_dst),
    bngs: s.exporters.map(e => ({ exporter: e, device_id: s.devices?.[e] || "" })), lookup_commands: { ...(s.lookup_commands || {}) } });
  const [busy, setBusy] = useState(false);
  const [showCmd, setShowCmd] = useState(false);
  const devById = useMemo(() => Object.fromEntries(data.devices.map(d => [d.id, d])), [data.devices]);
  const setBng = (i, patch) => setF(x => ({ ...x, bngs: x.bngs.map((b, j) => j === i ? { ...b, ...patch } : b) }));
  const pickDev = (i, id) => setBng(i, { device_id: id, ...(id && !f.bngs[i].exporter ? { exporter: devById[id]?.host || "" } : {}) });
  const unseen = (ip) => ip && !data.seen_exporters.some(e => e.ip === ip);
  const save = async () => {
    setBusy(true);
    const bngs = f.bngs.filter(b => b.exporter.trim());
    const body = { ...f, exporters: bngs.map(b => b.exporter.trim()), devices: Object.fromEntries(bngs.filter(b => b.device_id).map(b => [b.exporter.trim(), b.device_id])),
      subscriber_prefixes: split(f.subscriber_prefixes), c2_ips: split(f.c2_ips), feeds: split(f.feeds), ignore: split(f.ignore), ignore_dst: split(f.ignore_dst) };
    delete body.bngs;
    try { await api.put("/flow/botnet/settings", body); toast.success("Configuração salva — o coletor aplica em até 30 s"); onSaved(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl max-h-[92vh] overflow-y-auto" data-testid="botnet-config">
        <DialogHeader><DialogTitle>Botnet nos assinantes — configuração</DialogTitle></DialogHeader>
        <div className="space-y-4 min-w-0">
          <label className="flex items-center gap-2 text-sm cursor-pointer"><input type="checkbox" checked={!!f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} data-testid="botnet-enabled" /> Detecção ligada</label>
          <div>
            <div className="flex items-center gap-2"><Label>BNGs que exportam flow</Label>
              <button className="ml-auto text-xs text-brand-soft hover:underline flex items-center gap-1" onClick={() => setF({ ...f, bngs: [...f.bngs, { exporter: "", device_id: "" }] })} data-testid="botnet-add-bng"><Plus className="w-3 h-3" />adicionar BNG</button></div>
            <div className="space-y-1.5 mt-1.5">
              {f.bngs.map((b, i) => (
                <div key={i} className="grid grid-cols-[1fr_170px_28px] gap-2 items-center" data-testid="botnet-bng-row">
                  <select value={b.device_id} onChange={e => pickDev(i, e.target.value)} className={`${selCls} h-9 w-full`} data-testid="botnet-bng-dev">
                    <option value="">— equipamento (para consultar o assinante) —</option>
                    <TagOptions devices={data.devices} label={d => `${d.name} · ${d.host} · ${d.device_type}`} />
                  </select>
                  <Input value={b.exporter} onChange={e => setBng(i, { exporter: e.target.value })} placeholder="IP que envia o flow" list="botnet-exps" className={`${inputCls} font-mono h-9`} data-testid="botnet-bng-ip" />
                  <button className="text-slate-500 hover:text-red-400" onClick={() => setF({ ...f, bngs: f.bngs.filter((_, j) => j !== i) })}><Trash2 className="w-3.5 h-3.5" /></button>
                  {unseen(b.exporter.trim()) && <div className="col-span-3 text-[11px] text-amber-300 -mt-1">O coletor ainda não recebeu flow de {b.exporter.trim()} — confira a exportação no BNG (e o IP de origem do flow).</div>}
                </div>
              ))}
              {!f.bngs.length && <div className="text-xs text-slate-500 border border-dashed border-line rounded p-2.5">Adicione cada BNG: o equipamento cadastrado (para o BastiON perguntar quem é o assinante) e o IP de onde chega o flow dele.</div>}
            </div>
            <datalist id="botnet-exps">{data.seen_exporters.map(e => <option key={e.ip} value={e.ip} />)}</datalist>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            <div><Label>Faixas de IP dos assinantes</Label>
              <Textarea value={f.subscriber_prefixes} onChange={e => setF({ ...f, subscriber_prefixes: e.target.value })} rows={3} spellCheck={false} placeholder={"100.64.0.0/10\n203.0.113.0/24"} className={`${inputCls} font-mono text-xs mt-1`} data-testid="botnet-prefixes" />
              <div className="text-[11px] text-slate-500 mt-1">CGNAT e blocos públicos entregues aos clientes. Só o tráfego com origem nessas faixas é analisado.</div></div>
            <div><Label>Assinantes ignorados</Label>
              <Textarea value={f.ignore} onChange={e => setF({ ...f, ignore: e.target.value })} rows={3} spellCheck={false} placeholder={"203.0.113.25\n198.51.100.0/28"} className={`${inputCls} font-mono text-xs mt-1`} />
              <div className="text-[11px] text-slate-500 mt-1">Clientes com servidor de e-mail, scanner autorizado, etc.</div></div>
          </div>
          <div><Label>Destinos ignorados (nunca contam como alvo de ataque)</Label>
            <Textarea value={f.ignore_dst} onChange={e => setF({ ...f, ignore_dst: e.target.value })} rows={2} spellCheck={false} placeholder={"45.197.34.82\n200.10.20.0/24"} className={`${inputCls} font-mono text-xs mt-1`} data-testid="botnet-ignore-dst" />
            <div className="text-[11px] text-slate-500 mt-1">Servidor de jogo, IPTV, parceiro… O tráfego para os seus prefixos próprios (Flow → Configuração) e entre assinantes já é ignorado.</div></div>
          <div>
            <Label>Limites</Label>
            <div className="grid sm:grid-cols-2 gap-x-3 gap-y-2 mt-1.5">
              {NUM.map(([k, l, h]) => (
                <div key={k} className="flex items-center gap-2"><div className="flex-1 min-w-0"><div className="text-xs text-slate-300">{l}</div>{h && <div className="text-[10px] text-slate-500">{h}</div>}</div>
                  <Input type="number" value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} className={`${inputCls} font-mono h-8 w-28 text-xs`} data-testid={`botnet-${k}`} /></div>
              ))}
            </div>
            <div className="text-[11px] text-slate-500 mt-1.5">Os valores já consideram a amostragem do flow. Se aparecer cliente legítimo demais, suba o limite ou coloque-o em ignorados.</div>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            <div><Label>IPs de comando-e-controle (sua lista)</Label>
              <Textarea value={f.c2_ips} onChange={e => setF({ ...f, c2_ips: e.target.value })} rows={3} spellCheck={false} placeholder={"45.9.148.77\n185.220.100.0/24"} className={`${inputCls} font-mono text-xs mt-1`} data-testid="botnet-c2" /></div>
            <div><Label>Feeds de C2 (https, um IP por linha)</Label>
              <Textarea value={f.feeds} onChange={e => setF({ ...f, feeds: e.target.value })} rows={3} spellCheck={false} placeholder={"https://feodotracker.abuse.ch/downloads/ipblocklist.txt"} className={`${inputCls} font-mono text-xs mt-1`} data-testid="botnet-feeds" />
              <div className="text-[11px] text-slate-500 mt-1">Baixados pelo servidor a cada 6 h.{data.feeds?.count != null ? ` Agora: ${data.feeds.count} endereços.` : ""}</div>
              {Object.entries(data.feeds?.errors || {}).map(([u, e]) => <div key={u} className="text-[11px] text-amber-300 break-all">{u}: {e}</div>)}</div>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <label className="flex items-center gap-2 cursor-pointer"><input type="checkbox" checked={!!f.alert} onChange={e => setF({ ...f, alert: e.target.checked })} /> Avisar no Telegram / push</label>
            <label className="flex items-center gap-2 cursor-pointer"><input type="checkbox" checked={!!f.lookup} onChange={e => setF({ ...f, lookup: e.target.checked })} /> Perguntar ao BNG quem é o assinante</label>
            <button className="text-xs text-brand-soft hover:underline" onClick={() => setShowCmd(v => !v)} data-testid="botnet-cmds-toggle">{showCmd ? "ocultar" : "ajustar"} comandos de consulta</button>
          </div>
          {showCmd && (
            <div className="grid sm:grid-cols-2 gap-2">
              {Object.entries(data.lookup_defaults).map(([v, def]) => (
                <div key={v}><div className="text-[11px] text-slate-400 mb-0.5">{v}</div>
                  <Input value={f.lookup_commands[v] || ""} placeholder={def} onChange={e => setF({ ...f, lookup_commands: { ...f.lookup_commands, [v]: e.target.value } })} className={`${inputCls} font-mono h-8 text-xs`} data-testid={`botnet-cmd-${v}`} /></div>
              ))}
              <div className="sm:col-span-2 text-[11px] text-slate-500">Vazio = padrão (em cinza). Use <code>{"{ip}"}</code> no lugar do endereço. Só comandos de consulta: o BastiON não altera nada no BNG.</div>
            </div>
          )}
        </div>
        <DialogFooter><Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="botnet-save">{busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Row({ i, reload, ports }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const sub = i.sub;
  const lookup = async (e) => { e.stopPropagation(); setBusy(true); try { const { data } = await api.post(`/flow/botnet/${i.id}/lookup`); if (!data.found) toast(data.reason || "Assinante não encontrado"); await reload(); } catch (err) { toast.error(formatApiError(err)); } finally { setBusy(false); } };
  const ack = async (e) => { e.stopPropagation(); try { await api.post(`/flow/botnet/${i.id}/ack`, { ack: !i.ack, note: "" }); reload(); } catch (err) { toast.error(formatApiError(err)); } };
  const active = i.status === "active";
  return (
    <div className={`border border-line rounded-lg bg-surface ${i.ack || !active ? "opacity-70" : ""}`} data-testid="botnet-row">
      <div className="flex items-center gap-3 px-3 py-2.5 cursor-pointer flex-wrap" onClick={() => setOpen(v => !v)}>
        {open ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
        <span className="w-2 h-2 rounded-full shrink-0" style={{ background: active ? SEV[i.severity] : "#4b5557" }} />
        <div className="min-w-[150px]">
          <div className="font-mono text-slate-100 text-sm">{i.ip}</div>
          <div className="text-[11px] truncate max-w-[220px]" data-testid="botnet-sub">{sub?.found ? <span className="text-brand-pale">{sub.username || sub.mac}</span> : <span className="text-slate-500">{sub ? "assinante não identificado" : "identificando…"}</span>}</div>
        </div>
        <div className="flex-1 min-w-[200px]">
          <div className="text-sm text-slate-200">{i.label}</div>
          <div className="text-[11px] text-slate-400">{i.detail}{i.targets?.[0] && (i.kind === "ddos" || i.kind === "c2") ? <> → <span className="font-mono">{i.targets[0][0]}{i.targets[0][2] ? `:${i.targets[0][2]}` : ""}</span></> : ""}</div>
        </div>
        <div className="text-right text-xs font-mono">
          <div className="text-slate-200">{active ? val(i, i.cur || 0) : "encerrado"}</div>
          <div className="text-[10px] text-slate-500">pico {val(i, i.peak || 0)}</div>
        </div>
        <div className="text-right text-[11px] text-slate-500 w-24">{ago(active ? i.start : (i.end || i.updated))}<div>{i.minutes || 1} min visto</div></div>
        <div className="flex gap-1">
          <button onClick={lookup} disabled={busy} title="Perguntar ao BNG quem é o assinante" className="p-1.5 text-slate-400 hover:text-brand-soft" data-testid="botnet-lookup">{busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserSearch className="w-4 h-4" />}</button>
          <button onClick={ack} title={i.ack ? "Reabrir" : "Marcar como tratado"} className={`p-1.5 ${i.ack ? "text-on" : "text-slate-400 hover:text-on"}`} data-testid="botnet-ack"><Check className="w-4 h-4" /></button>
        </div>
      </div>
      {open && (
        <div className="border-t border-line px-4 py-3 text-xs space-y-2.5" data-testid="botnet-detail">
          <div className="text-slate-400">{KIND_HELP[i.kind]}</div>
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <div className="text-slate-500 mb-1">Assinante{sub?.device ? ` (${sub.device})` : ""}</div>
              {sub?.found ? (
                <div className="font-mono text-slate-200 space-y-0.5">
                  {sub.username && <div>login <span className="text-brand-pale">{sub.username}</span> <button className="text-slate-500 hover:text-slate-200 ml-1" onClick={() => copyText(sub.username).then(() => toast.success("Copiado"))}><Copy className="w-3 h-3 inline" /></button></div>}
                  {sub.mac && <div>MAC {sub.mac}</div>}{sub.interface && <div>interface {sub.interface}</div>}{sub.vlan && <div>VLAN {sub.vlan}</div>}
                </div>
              ) : <div className="text-slate-400">{sub?.reason || "Ainda não consultado — clique no ícone de busca."}</div>}
              {sub?.raw && <details className="mt-1"><summary className="cursor-pointer text-slate-500">resposta do BNG</summary><pre className="mt-1 max-h-40 overflow-auto font-mono text-[11px] text-slate-400 bg-sunken border border-line rounded p-2 whitespace-pre-wrap">{sub.command ? `> ${sub.command}\n` : ""}{sub.raw}</pre></details>}
            </div>
            <div>
              <div className="text-slate-500 mb-1">{i.kind === "scan" ? "Portas varridas e amostra de destinos" : "Destinos"}</div>
              {i.ports?.length > 0 && <div className="flex flex-wrap gap-1 mb-1.5">{i.ports.map(([p, n]) => <span key={p} className="font-mono border border-line rounded px-1.5 py-0.5 text-slate-300">{p}{ports[p] ? ` ${ports[p]}` : ""} · {n}</span>)}</div>}
              <div className="font-mono text-slate-300 space-y-0.5">
                {(i.targets || []).map(([ip, rate, port]) => <div key={ip}>{ip}{port ? `:${port}` : ""}{rate ? <span className="text-slate-500"> · {rate} pps</span> : ""}</div>)}
                {!(i.targets || []).length && <span className="text-slate-500">—</span>}
              </div>
            </div>
          </div>
          <div className="text-[11px] text-slate-500">Flow do BNG {i.exporter}{i.bps ? ` · ${fmtRate(i.bps)}` : ""}{i.ack ? ` · tratado por ${i.ack_by || "?"}` : ""}</div>
        </div>
      )}
    </div>
  );
}

/** Aba Botnet: assinantes com comportamento de máquina infectada, vistos no flow dos BNGs. */
export function FlowBotnet({ onCount }) {
  const [data, setData] = useState(null);
  const [cfg, setCfg] = useState(false);
  const [filter, setFilter] = useState("active");
  const [kind, setKind] = useState("");
  const load = async () => { try { const { data: d } = await api.get("/flow/botnet"); setData(d); onCount?.(d.items.filter(i => i.status === "active" && !i.ack).length); } catch (e) { toast.error(formatApiError(e)); } };
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = useMemo(() => (data?.items || []).filter(i => (filter === "all" || (filter === "active" ? i.status === "active" && !i.ack : filter === "ack" ? i.ack : i.status !== "active")) && (!kind || i.kind === kind)), [data, filter, kind]);
  const ackAll = async () => {
    if (!window.confirm(`Marcar ${shown.length} incidente(s) como tratados?`)) return;
    try { const { data: r } = await api.post("/flow/botnet/ack-bulk", { ids: shown.map(i => i.id) }); toast.success(`${r.count} marcado(s) como tratados`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  if (!data) return <div className="p-8 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>;
  const st = data.status || {};
  const on = data.settings.enabled;
  const fresh = st.at && Date.now() - new Date(st.at).getTime() < 180000;
  const counts = data.items.reduce((a, i) => { if (i.status === "active" && !i.ack) a[i.kind] = (a[i.kind] || 0) + 1; return a; }, {});
  return (
    <div className="space-y-4" data-testid="flow-botnet">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="text-sm text-slate-400 max-w-3xl">Clientes com comportamento de máquina infectada, vistos no flow que os <b className="text-slate-200">BNGs</b> exportam: participando de ataque, varrendo a internet, enviando spam, servindo de refletor ou falando com servidor de comando-e-controle. O BastiON pergunta ao BNG qual é o login do assinante.</div>
        {data.can_manage && <Button variant="outline" className="ml-auto border-line text-slate-200" onClick={() => setCfg(true)} data-testid="botnet-open-config"><Settings2 className="w-4 h-4 mr-2" />Configurar</Button>}
      </div>
      {!on ? (
        <div className="border border-dashed border-line rounded-lg p-6 text-sm text-slate-400" data-testid="botnet-off">
          <div className="text-slate-200 mb-1 flex items-center gap-2"><Bug className="w-4 h-4" />Detecção desligada</div>
          {data.can_manage ? "Clique em Configurar, escolha os BNGs que exportam flow e as faixas de IP dos assinantes." : "Peça ao administrador para configurar os BNGs."}
        </div>
      ) : (
        <>
          <div className="flex items-center gap-x-5 gap-y-1 flex-wrap text-xs font-mono text-slate-400 border border-line rounded-lg bg-surface px-3 py-2" data-testid="botnet-status">
            {!fresh ? <span className="text-amber-300 flex items-center gap-1.5 font-sans"><AlertTriangle className="w-3.5 h-3.5" />Sem notícia do coletor de flow nos últimos minutos.</span>
              : st.flows ? <span><span className="text-on">●</span> {st.subs} assinantes com tráfego no último minuto · {st.flows} flows</span>
              : <span className="text-amber-300 font-sans">Nenhum flow dos BNGs configurados com origem nas faixas de assinantes no último minuto — confira a exportação e as faixas.</span>}
            <span>BNGs: {data.settings.exporters.join(", ")}</span>
            {st.c2 > 0 && <span>{st.c2} IPs de C2 carregados</span>}
          </div>
          <div className="flex flex-wrap gap-1.5 items-center">
            {[["active", "Ativos"], ["ack", "Tratados"], ["ended", "Encerrados"], ["all", "Todos"]].map(([v, l]) => <button key={v} className={chip(filter === v)} onClick={() => setFilter(v)} data-testid={`botnet-f-${v}`}>{l}</button>)}
            <span className="w-px h-5 bg-line mx-1" />
            <button className={chip(!kind)} onClick={() => setKind("")}>todos os tipos</button>
            {Object.entries(data.kinds).map(([k, l]) => <button key={k} className={chip(kind === k)} onClick={() => setKind(kind === k ? "" : k)} data-testid={`botnet-k-${k}`}>{l.split(" (")[0]}{counts[k] ? ` · ${counts[k]}` : ""}</button>)}
            {filter === "active" && shown.length > 1 && <button className="ml-auto text-xs text-slate-400 hover:text-on flex items-center gap-1" onClick={ackAll} data-testid="botnet-ack-all"><Check className="w-3.5 h-3.5" />marcar os {shown.length} como tratados</button>}
          </div>
          {!shown.length ? (
            <div className="border border-line rounded-lg bg-surface p-8 text-center text-sm text-slate-400" data-testid="botnet-empty"><ShieldCheck className="w-6 h-6 mx-auto mb-2" style={{ color: STATUS.good }} />{filter === "active" ? "Nenhum assinante suspeito agora." : "Nada nesta lista."}</div>
          ) : <div className="space-y-2">{shown.map(i => <Row key={i.id} i={i} reload={load} ports={data.scan_ports} />)}</div>}
        </>
      )}
      {cfg && <ConfigDialog data={data} onClose={() => setCfg(false)} onSaved={() => { setCfg(false); load(); }} />}
    </div>
  );
}
