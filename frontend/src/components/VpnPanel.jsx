import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Lock, LockOpen, Loader2, AlertTriangle, Plus, Pencil, Trash2, Plug, Unplug, ShieldCheck, KeyRound } from "lucide-react";
import { STATUS } from "@/lib/netfmt";

const ST = {
  up: ["Conectada", STATUS.good, Lock], connecting: ["Conectando…", STATUS.warning, Loader2], disconnecting: ["Desconectando…", "#8D9A9D", Loader2],
  need_otp: ["Aguardando token", STATUS.warning, KeyRound],
  error: ["Erro", STATUS.critical, AlertTriangle], down: ["Caiu", STATUS.critical, AlertTriangle],
  disconnected: ["Desconectada", "#6E7B7E", LockOpen],
};
const inputCls = "bg-sunken border-line font-mono";
export const VPN_TYPES = { fortinet: "FortiGate SSL", openvpn: "OpenVPN", pptp: "PPTP", l2tp: "L2TP/IPsec" };
const TYPE_HELP = {
  fortinet: "SSL-VPN do FortiGate (o mesmo do FortiClient). Usuário, senha e token digitado na hora.",
  openvpn: "Cole o arquivo .ovpn do servidor. Usuário e senha só se o servidor pedir.",
  pptp: "Servidor PPTP (MikroTik, Windows…). Usuário e senha; criptografia MPPE.",
  l2tp: "L2TP com IPsec por chave pré-compartilhada (MikroTik, FortiGate, Windows…). Sem chave = L2TP puro.",
};
const kindOf = (v) => v?.type || "fortinet";

export function VpnState({ state, small = false }) {
  const [label, color, Icon] = ST[state] || ST.disconnected;
  return (
    <span className={`inline-flex items-center gap-1 font-mono whitespace-nowrap ${small ? "text-[10px]" : "text-xs"}`} style={{ color }} data-testid="vpn-state">
      <Icon className={`${small ? "w-3 h-3" : "w-3.5 h-3.5"} ${Icon === Loader2 ? "animate-spin" : ""}`} /> {label}
    </span>
  );
}

/** Acompanha as VPNs do usuário (rápido enquanto alguma está conectando). */
export function useVpns(enabled = true) {
  const [data, setData] = useState({ items: [], daemon: true, caps: {}, types: [] });
  const load = useCallback(async () => {
    try { setData((await api.get("/vpns")).data); } catch { /* sem acesso / offline */ }
  }, []);
  const connecting = data.items.some(v => ["connecting", "disconnecting", "need_otp"].includes(v.status.state) || (v.status.diag && !v.status.diag_at));
  useEffect(() => {
    if (!enabled) return undefined;
    load();
    const t = setInterval(load, connecting ? 1500 : 20000);
    return () => clearInterval(t);
  }, [enabled, connecting, load]);
  return [data, load];
}

/** Conectar: token antes (FortiToken/app) ou depois, quando o gateway manda por e-mail/SMS. Erros aparecem aqui mesmo. */
export function VpnConnectDialog({ vpn, onClose, reload, daemon = true }) {
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState("");
  const ref = useRef(null);
  const st = vpn?.status || {};
  useEffect(() => { setTimeout(() => ref.current?.focus(), 60); }, [st.state]);
  useEffect(() => {
    if (sent && st.state === "up") { toast.success(`VPN ${vpn.name} conectada`); onClose(); }
  }, [sent, st.state]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!vpn) return null;
  const call = async (fn) => {
    setBusy(true); setErr("");
    try { await fn(); await reload(); }
    catch (e) { setErr(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const connect = () => call(async () => { await api.post(`/vpns/${vpn.id}/connect`, { otp }); setSent(true); setOtp(""); });
  const sendOtp = () => otp.trim() && call(async () => { await api.post(`/vpns/${vpn.id}/otp`, { otp }); setSent(true); setOtp(""); });
  const trust = () => call(async () => { await api.post(`/vpns/${vpn.id}/trust-cert`); setSent(false); toast.success("Certificado confiado — conecte de novo"); });
  const disconnect = () => call(() => api.post(`/vpns/${vpn.id}/disconnect`));
  const diag = () => call(() => api.post(`/vpns/${vpn.id}/diag`));
  const diagRunning = st.diag && !st.diag_at;
  const up = st.state === "up";
  const forti = kindOf(vpn) === "fortinet";
  const needOtp = st.state === "need_otp";
  const working = st.state === "connecting" || st.state === "disconnecting";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-md" data-testid="vpn-connect-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><KeyRound className="w-4 h-4 text-brand-soft" /> VPN {vpn.name}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="font-mono text-slate-400 truncate">{VPN_TYPES[kindOf(vpn)]} · {vpn.username ? `${vpn.username}@` : ""}{vpn.host}:{vpn.port}</span>
            <VpnState state={st.state} />
          </div>
          {!daemon && (
            <div className="text-xs border rounded px-2.5 py-2 space-y-1" style={{ color: STATUS.critical, borderColor: `${STATUS.critical}66` }} data-testid="vpn-nodaemon">
              <div className="flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />O serviço de VPN não está rodando no servidor — nada é enviado ao servidor da VPN enquanto isso.</div>
              <pre className="font-mono text-[11px] text-slate-200 bg-sunken rounded p-1.5 whitespace-pre-wrap">{"cd /opt/bastion/deploy\necho \"COMPOSE_PROFILES=vpn\" | sudo tee -a .env\nsudo bash update.sh\nsudo docker compose ps vpn"}</pre>
            </div>
          )}
          {up ? (
            <div className="text-sm text-slate-300 space-y-1">
              <div>Túnel <span className="font-mono">{st.iface}</span> com IP <span className="font-mono">{st.ip}</span>.</div>
              <div className="text-xs text-slate-400">Rotas pela VPN: <span className="font-mono">{(st.routes || []).join(", ") || "—"}</span></div>
            </div>
          ) : needOtp ? (
            <div className="space-y-1.5" data-testid="vpn-need-otp">
              <div className="text-sm text-slate-100">O FortiGate pediu o token{st.prompt ? <>: <span className="text-slate-400">“{st.prompt}”</span></> : ""}.</div>
              <div className="text-[11px] text-slate-400">Se ele chega por e-mail ou SMS, confira a caixa de entrada agora (vale por alguns minutos).</div>
              <Input ref={ref} value={otp} onChange={e => setOtp(e.target.value)} onKeyDown={e => e.key === "Enter" && sendOtp()} inputMode="numeric" autoComplete="one-time-code"
                     placeholder="código recebido" className={`${inputCls} text-lg tracking-widest`} data-testid="vpn-otp2" />
            </div>
          ) : !forti ? (
            <div className="text-sm text-slate-300" data-testid="vpn-simple">
              Conexão {VPN_TYPES[kindOf(vpn)]} com as credenciais salvas — não pede token.
              <div className="text-[11px] text-slate-500 mt-1">Só as redes desta VPN e os IPs dos agentes marcados com ela passam pelo túnel; a rota padrão do servidor não muda.</div>
            </div>
          ) : (
            <div>
              <Label>Token (opcional)</Label>
              <Input ref={ref} value={otp} onChange={e => setOtp(e.target.value)} onKeyDown={e => e.key === "Enter" && !working && connect()} inputMode="numeric" autoComplete="one-time-code"
                     placeholder="FortiToken / app autenticador" className={`${inputCls} text-lg tracking-widest`} data-testid="vpn-otp" disabled={working} />
              <div className="text-[11px] text-slate-500 mt-1">Token de app/FortiToken: digite e conecte. <b className="text-slate-400">Token por e-mail ou SMS: deixe em branco e clique em Conectar</b> — o FortiGate envia o código e o campo aparece aqui. A senha já está salva; o token não fica guardado.</div>
            </div>
          )}
          {working && <div className="text-xs text-slate-400 flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> {st.state === "disconnecting" ? "desconectando…" : forti ? "conectando no gateway…" : "conectando no servidor…"}</div>}
          {(err || ((st.state === "error" || st.state === "down") && st.error)) && (
            <div className="text-xs border rounded px-2.5 py-2 flex gap-1.5" style={{ color: STATUS.critical, borderColor: `${STATUS.critical}66` }} data-testid="vpn-error">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /><span>{err || st.error}</span>
            </div>
          )}
          {st.pending_cert && (
            <div className="text-xs border border-line2 rounded p-2.5 space-y-2" data-testid="vpn-cert">
              <div className="text-slate-300">Impressão digital (SHA-256) do certificado do gateway:</div>
              <div className="font-mono text-[11px] text-slate-100 break-all bg-sunken p-1.5 rounded">{st.pending_cert}</div>
              <div className="text-slate-500">Confira com quem administra o FortiGate antes de confiar.</div>
              <Button size="sm" onClick={trust} className="h-8 bg-brand hover:bg-brand-strong" data-testid="vpn-trust"><ShieldCheck className="w-4 h-4 mr-1" /> Confiar neste certificado</Button>
            </div>
          )}
          {(up || st.diag) && (
            <div className="text-[11px]" data-testid="vpn-diag">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-slate-500">Diagnóstico a partir do servidor</span>
                <button onClick={diag} disabled={busy || diagRunning} className="ml-auto text-brand-soft hover:underline disabled:opacity-50" data-testid="vpn-diag-run">
                  {diagRunning ? "rodando…" : "Testar rotas e SSH dos jumps"}</button>
              </div>
              {st.diag && <pre className="max-h-56 overflow-y-auto font-mono text-slate-300 bg-sunken border border-line rounded p-2 whitespace-pre-wrap" data-testid="vpn-diag-out">{st.diag}</pre>}
            </div>
          )}
          {st.log?.length > 0 && (
            <details className="text-[11px]" open={st.state === "error" || st.state === "down"}><summary className="cursor-pointer text-slate-500">Log da conexão</summary>
              <pre className="mt-1 max-h-48 overflow-y-auto font-mono text-slate-400 bg-sunken border border-line rounded p-2 whitespace-pre-wrap" data-testid="vpn-log">{st.log.join("\n")}</pre>
            </details>
          )}
        </div>
        <DialogFooter>
          {up ? (
            <Button variant="outline" onClick={disconnect} disabled={busy} className="border-line bg-panel text-slate-200 hover:bg-slate-800" data-testid="vpn-disconnect"><Unplug className="w-4 h-4 mr-1" /> Desconectar</Button>
          ) : needOtp ? (
            <>
              <Button variant="ghost" onClick={disconnect} disabled={busy} className="text-slate-400">Cancelar</Button>
              <Button onClick={sendOtp} disabled={busy || !otp.trim()} className="bg-brand hover:bg-brand-strong" data-testid="vpn-send-otp">
                {busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <KeyRound className="w-4 h-4 mr-1" />} Enviar token
              </Button>
            </>
          ) : (
            <Button onClick={connect} disabled={busy || working || !daemon} className="bg-brand hover:bg-brand-strong" data-testid="vpn-connect">
              {busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Plug className="w-4 h-4 mr-1" />} Conectar
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const emptyVpn = { type: "fortinet", name: "", host: "", port: "", username: "", password: "", realm: "", routes: "", ovpn_config: "", psk: "", mppe: true };

function VpnForm({ initial, caps = {}, onClose, onSaved }) {
  const [f, setF] = useState(initial ? { ...emptyVpn, ...initial, type: kindOf(initial), password: "", psk: "", ovpn_config: "", routes: (initial.routes || []).join("\n") } : emptyVpn);
  const [busy, setBusy] = useState(false);
  const k = f.type;
  const editing = !!initial?.id;
  const missing = Object.keys(caps).length > 0 && (caps[k] === false || (k === "l2tp" && f.psk && caps.ipsec === false));
  const readFile = (e) => { const file = e.target.files?.[0]; if (file) file.text().then(t => setF(x => ({ ...x, ovpn_config: t, name: x.name || file.name.replace(/\.(ovpn|conf)$/i, "") }))); e.target.value = ""; };
  const save = async () => {
    setBusy(true);
    const body = { type: k, name: f.name, host: f.host, port: f.port ? Number(f.port) : null, username: f.username, password: f.password || null, realm: f.realm,
                   routes: f.routes.split(/[\s,;]+/).filter(Boolean), trusted_certs: initial?.trusted_certs || [],
                   ovpn_config: k === "openvpn" ? (f.ovpn_config || null) : null, mppe: !!f.mppe,
                   psk: k !== "l2tp" ? null : f.psk ? f.psk : (editing && !f.clearPsk ? null : "") };
    try {
      if (editing) await api.put(`/vpns/${initial.id}`, body); else await api.post("/vpns", body);
      toast.success("VPN salva"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg max-h-[92vh] overflow-y-auto" data-testid="vpn-form">
        <DialogHeader><DialogTitle>{editing ? `Editar VPN (${VPN_TYPES[k]})` : "Nova VPN (cliente)"}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          {!editing && (
            <div>
              <Label>Tipo</Label>
              <div className="grid grid-cols-4 gap-1.5 mt-1">
                {Object.entries(VPN_TYPES).map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setF({ ...f, type: key, port: "" })} data-testid={`vpn-type-${key}`}
                          className={`h-9 rounded-md border text-xs ${k === key ? "border-brand bg-brand/15 text-slate-100" : "border-line bg-sunken text-slate-400 hover:text-slate-200"}`}>{label}</button>
                ))}
              </div>
            </div>
          )}
          <div className="text-[11px] text-slate-500 -mt-1">{TYPE_HELP[k]}</div>
          {missing && (
            <div className="text-xs text-amber-300 flex gap-1.5" data-testid="vpn-missing"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              O servidor ainda não tem o programa deste tipo de VPN{k === "l2tp" && caps.l2tp !== false ? " (falta o IPsec/strongSwan — sem chave funciona)" : ""}. Rode o update.sh; se continuar, me avise o que aparece.</div>
          )}
          <div><Label>Nome</Label><Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="ex.: VPN-Cliente" className={inputCls} data-testid="vpn-name" /></div>
          {k === "openvpn" ? (
            <div>
              <div className="flex items-center gap-2"><Label>Arquivo .ovpn</Label>
                <label className="ml-auto text-xs text-brand-soft cursor-pointer hover:underline">escolher arquivo<input type="file" accept=".ovpn,.conf,text/plain" className="hidden" onChange={readFile} data-testid="vpn-ovpn-file" /></label></div>
              <Textarea value={f.ovpn_config} onChange={e => setF({ ...f, ovpn_config: e.target.value })} rows={6} spellCheck={false}
                        placeholder={initial?.has_config ? `•••••••• (mantido — ${initial.host}:${initial.port}). Cole outro para trocar.` : "client\ndev tun\nproto udp\nremote vpn.empresa.com.br 1194\n<ca>\n…\n</ca>"}
                        className={`${inputCls} text-[11px]`} data-testid="vpn-ovpn" />
              <div className="text-[11px] text-slate-500 mt-1">Certificados e chaves precisam estar embutidos no arquivo (blocos &lt;ca&gt;, &lt;cert&gt;, &lt;key&gt;…). Scripts, rota padrão e DNS do arquivo são ignorados.</div>
              {initial?.ovpn_dropped?.length > 0 && !f.ovpn_config && <div className="text-[11px] text-slate-500 mt-1">Ignorado do arquivo atual: <span className="font-mono">{initial.ovpn_dropped.join(", ")}</span></div>}
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-2">
              <div className={k === "fortinet" ? "col-span-2" : "col-span-3"}><Label>{k === "fortinet" ? "Gateway" : "Servidor"}</Label><Input value={f.host} onChange={e => setF({ ...f, host: e.target.value })} placeholder="vpn.empresa.com.br ou IP" className={inputCls} data-testid="vpn-host" /></div>
              {k === "fortinet" && <div><Label>Porta</Label><Input type="number" value={f.port} onChange={e => setF({ ...f, port: e.target.value })} placeholder="443" className={inputCls} data-testid="vpn-port" /></div>}
            </div>
          )}
          {k === "fortinet" && <div className="text-[11px] text-slate-500 -mt-1">É o "Gateway remoto" da conexão no FortiClient (Configurar VPN / editar); a porta costuma ser 443 ou 10443.</div>}
          <div className="grid grid-cols-2 gap-2">
            <div><Label>Usuário{k === "openvpn" ? " (se o servidor pedir)" : ""}</Label><Input value={f.username} onChange={e => setF({ ...f, username: e.target.value })} autoComplete="off" className={inputCls} data-testid="vpn-user" /></div>
            <div><Label>Senha</Label><Input type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} autoComplete="new-password"
                                             placeholder={initial?.has_password ? "•••••••• (mantida)" : ""} className={inputCls} data-testid="vpn-pass" /></div>
          </div>
          {k === "fortinet" && <div><Label>Realm (opcional)</Label><Input value={f.realm} onChange={e => setF({ ...f, realm: e.target.value })} className={inputCls} /></div>}
          {k === "l2tp" && (
            <div>
              <Label>Chave pré-compartilhada (IPsec)</Label>
              <Input type="password" value={f.psk} onChange={e => setF({ ...f, psk: e.target.value, clearPsk: false })} autoComplete="new-password"
                     placeholder={initial?.has_psk && !f.clearPsk ? "•••••••• (mantida)" : "vazio = L2TP sem IPsec"} className={inputCls} data-testid="vpn-psk" />
              {initial?.has_psk && <label className="flex items-center gap-1.5 text-[11px] text-slate-400 mt-1 cursor-pointer"><input type="checkbox" checked={!!f.clearPsk} onChange={e => setF({ ...f, clearPsk: e.target.checked, psk: "" })} /> remover a chave (L2TP sem IPsec)</label>}
            </div>
          )}
          {k === "pptp" && (
            <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer"><input type="checkbox" checked={!!f.mppe} onChange={e => setF({ ...f, mppe: e.target.checked })} data-testid="vpn-mppe" /> Exigir criptografia (MPPE-128, MS-CHAPv2)</label>
          )}
          <div>
            <Label>Redes pela VPN{k === "fortinet" ? " (opcional)" : ""}</Label>
            <Textarea value={f.routes} onChange={e => setF({ ...f, routes: e.target.value })} rows={2} placeholder={"ex.: 10.50.0.0/16"} className={`${inputCls} text-xs`} data-testid="vpn-routes" />
            <div className="text-[11px] text-slate-500 mt-1">Os IPs dos agentes marcados com esta VPN já entram sozinhos. O resto do servidor continua saindo normal (a rota padrão nunca vai para a VPN).</div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="vpn-save">{busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Seção de VPNs na página de Agentes. */
export function VpnSection({ vpns, daemon, caps = {}, reload, openId, onOpened }) {
  const [form, setForm] = useState(null);
  const [conn, setConn] = useState(null);
  useEffect(() => {
    if (openId && vpns.some(v => v.id === openId)) { setConn(openId); onOpened?.(); }
  }, [openId, vpns]); // eslint-disable-line react-hooks/exhaustive-deps
  const del = async (v) => {
    if (!window.confirm(`Apagar a VPN ${v.name}? Os agentes que usam ela deixam de depender dela.`)) return;
    try { await api.delete(`/vpns/${v.id}`); reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const disc = async (v) => { try { await api.post(`/vpns/${v.id}/disconnect`); reload(); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <div className="mb-6" data-testid="vpn-section">
      <div className="flex items-center gap-2 mb-2">
        <div className="text-xs text-slate-400">VPNs no servidor</div>
        <Button size="sm" variant="ghost" onClick={() => setForm({})} className="h-7 text-xs text-brand-soft hover:bg-brand/15" data-testid="vpn-new"><Plus className="w-3.5 h-3.5 mr-1" /> Nova VPN</Button>
      </div>
      {!daemon && vpns.length > 0 && (
        <div className="text-xs text-amber-300 mb-2 flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          O serviço de VPN não está rodando no servidor: coloque <code className="text-slate-200">COMPOSE_PROFILES=vpn</code> no deploy/.env e rode o update.sh.</div>
      )}
      {vpns.length === 0 ? (
        <div className="text-sm text-slate-500 border border-dashed border-line rounded p-3">
          Jumps que só respondem por VPN podem ser alcançados direto do servidor: cadastre a VPN aqui (FortiGate SSL, OpenVPN, PPTP ou L2TP/IPsec — sempre como cliente), marque os agentes com ela e conecte — sem depender do seu PC ligado.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {vpns.map(v => (
            <div key={v.id} className="border border-line rounded-lg bg-surface p-3" data-testid={`vpn-${v.id}`}>
              <div className="flex items-center gap-2">
                <div className="text-slate-100 font-semibold">{v.name}</div>
                <span className="text-[10px] font-mono text-slate-400 border border-line rounded px-1" data-testid={`vpn-kind-${v.id}`}>{VPN_TYPES[kindOf(v)]}</span>
                <VpnState state={v.status.state} />
                <div className="ml-auto flex gap-1">
                  <button onClick={() => setForm(v)} className="text-slate-500 hover:text-slate-200 p-1" title="Editar"><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={() => del(v)} className="text-slate-500 hover:text-red-400 p-1" title="Apagar"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>
              <div className="text-[11px] font-mono text-slate-500 mt-0.5">{v.username ? `${v.username}@` : ""}{v.host}:{v.port}{v.status.state === "up" && v.status.ip ? ` · ${v.status.iface || "ppp?"} ${v.status.ip}` : ""}</div>
              <div className="text-xs text-slate-400 mt-1">Agentes: {v.agents.length ? v.agents.join(", ") : <span className="text-slate-600">nenhum — edite o agente e escolha esta VPN</span>}</div>
              {v.status.error && v.status.state !== "up" && <div className="text-[11px] mt-1" style={{ color: STATUS.critical }}>{v.status.error}</div>}
              <div className="flex gap-2 mt-2">
                {v.status.state === "up"
                  ? <Button size="sm" variant="outline" onClick={() => disc(v)} className="h-8 border-line bg-panel text-slate-200 hover:bg-slate-800"><Unplug className="w-4 h-4 mr-1" /> Desconectar</Button>
                  : <Button size="sm" onClick={() => setConn(v.id)} className="h-8 bg-brand hover:bg-brand-strong" data-testid={`vpn-open-${v.id}`}>{kindOf(v) === "fortinet" ? <KeyRound className="w-4 h-4 mr-1" /> : <Plug className="w-4 h-4 mr-1" />} {v.status.state === "need_otp" ? "Digitar o token" : "Conectar"}</Button>}
                {v.status.state === "up" && <Button size="sm" variant="ghost" onClick={() => setConn(v.id)} className="h-8 text-slate-400 hover:bg-slate-800">Detalhes</Button>}
              </div>
            </div>
          ))}
        </div>
      )}
      {form && <VpnForm initial={form.id ? form : null} caps={caps} onClose={() => setForm(null)} onSaved={() => { setForm(null); reload(); }} />}
      {conn && <VpnConnectDialog vpn={vpns.find(v => v.id === conn)} onClose={() => setConn(null)} reload={reload} daemon={daemon} />}
    </div>
  );
}

/** Indicador no menu lateral: estado de cada VPN; clique abre o token. */
export function VpnIndicator({ mini = false }) {
  const [data, reload] = useVpns(true);
  const [conn, setConn] = useState(null);
  if (!data.items.length) return null;
  return (
    <div className={`px-2 pb-2 ${mini ? "" : "space-y-1"}`} data-testid="vpn-indicator">
      {data.items.map(v => {
        const [label, color, Icon] = ST[v.status.state] || ST.disconnected;
        return (
          <button key={v.id} onClick={() => setConn(v.id)} title={`VPN ${v.name}: ${label}`} data-testid={`vpn-ind-${v.id}`}
                  className={`w-full flex items-center gap-2 rounded-md border border-line hover:bg-slate-800/50 ${mini ? "justify-center py-1.5" : "px-2 py-1.5"}`}>
            <Icon className={`w-3.5 h-3.5 shrink-0 ${Icon === Loader2 ? "animate-spin" : ""}`} style={{ color }} />
            {!mini && <><span className="text-xs text-slate-300 truncate">VPN {v.name}</span><span className="ml-auto text-[10px] font-mono" style={{ color }}>{label}</span></>}
          </button>
        );
      })}
      {conn && <VpnConnectDialog vpn={data.items.find(v => v.id === conn)} onClose={() => setConn(null)} reload={reload} daemon={data.daemon} />}
    </div>
  );
}
