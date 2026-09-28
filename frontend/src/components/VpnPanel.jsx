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
  up: ["Conectada", STATUS.good, Lock], connecting: ["Conectando…", STATUS.warning, Loader2], disconnecting: ["Desconectando…", "#94A3B8", Loader2],
  need_otp: ["Aguardando token", STATUS.warning, KeyRound],
  error: ["Erro", STATUS.critical, AlertTriangle], down: ["Caiu", STATUS.critical, AlertTriangle],
  disconnected: ["Desconectada", "#64748B", LockOpen],
};
const inputCls = "bg-[#05070A] border-[#1E293B] font-mono";

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
  const [data, setData] = useState({ items: [], daemon: true });
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
  const needOtp = st.state === "need_otp";
  const working = st.state === "connecting" || st.state === "disconnecting";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-md" data-testid="vpn-connect-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><KeyRound className="w-4 h-4 text-[#4DA3FF]" /> VPN {vpn.name}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="font-mono text-slate-400 truncate">{vpn.username}@{vpn.host}:{vpn.port}</span>
            <VpnState state={st.state} />
          </div>
          {!daemon && (
            <div className="text-xs border rounded px-2.5 py-2 space-y-1" style={{ color: STATUS.critical, borderColor: `${STATUS.critical}66` }} data-testid="vpn-nodaemon">
              <div className="flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />O serviço de VPN não está rodando no servidor — nada é enviado ao FortiGate enquanto isso.</div>
              <pre className="font-mono text-[11px] text-slate-200 bg-[#05070A] rounded p-1.5 whitespace-pre-wrap">{"cd /opt/bastion/deploy\necho \"COMPOSE_PROFILES=vpn\" | sudo tee -a .env\nsudo bash update.sh\nsudo docker compose ps vpn"}</pre>
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
          ) : (
            <div>
              <Label>Token (opcional)</Label>
              <Input ref={ref} value={otp} onChange={e => setOtp(e.target.value)} onKeyDown={e => e.key === "Enter" && !working && connect()} inputMode="numeric" autoComplete="one-time-code"
                     placeholder="FortiToken / app autenticador" className={`${inputCls} text-lg tracking-widest`} data-testid="vpn-otp" disabled={working} />
              <div className="text-[11px] text-slate-500 mt-1">Token de app/FortiToken: digite e conecte. <b className="text-slate-400">Token por e-mail ou SMS: deixe em branco e clique em Conectar</b> — o FortiGate envia o código e o campo aparece aqui. A senha já está salva; o token não fica guardado.</div>
            </div>
          )}
          {working && <div className="text-xs text-slate-400 flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> {st.state === "disconnecting" ? "desconectando…" : "conectando no gateway…"}</div>}
          {(err || ((st.state === "error" || st.state === "down") && st.error)) && (
            <div className="text-xs border rounded px-2.5 py-2 flex gap-1.5" style={{ color: STATUS.critical, borderColor: `${STATUS.critical}66` }} data-testid="vpn-error">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /><span>{err || st.error}</span>
            </div>
          )}
          {st.pending_cert && (
            <div className="text-xs border border-[#2A3345] rounded p-2.5 space-y-2" data-testid="vpn-cert">
              <div className="text-slate-300">Impressão digital (SHA-256) do certificado do gateway:</div>
              <div className="font-mono text-[11px] text-slate-100 break-all bg-[#05070A] p-1.5 rounded">{st.pending_cert}</div>
              <div className="text-slate-500">Confira com quem administra o FortiGate antes de confiar.</div>
              <Button size="sm" onClick={trust} className="h-8 bg-[#007AFF] hover:bg-[#0062CC]" data-testid="vpn-trust"><ShieldCheck className="w-4 h-4 mr-1" /> Confiar neste certificado</Button>
            </div>
          )}
          {(up || st.diag) && (
            <div className="text-[11px]" data-testid="vpn-diag">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-slate-500">Diagnóstico a partir do servidor</span>
                <button onClick={diag} disabled={busy || diagRunning} className="ml-auto text-[#4DA3FF] hover:underline disabled:opacity-50" data-testid="vpn-diag-run">
                  {diagRunning ? "rodando…" : "Testar rotas e SSH dos jumps"}</button>
              </div>
              {st.diag && <pre className="max-h-56 overflow-y-auto font-mono text-slate-300 bg-[#05070A] border border-[#1E293B] rounded p-2 whitespace-pre-wrap" data-testid="vpn-diag-out">{st.diag}</pre>}
            </div>
          )}
          {st.log?.length > 0 && (
            <details className="text-[11px]" open={st.state === "error"}><summary className="cursor-pointer text-slate-500">Log da conexão</summary>
              <pre className="mt-1 max-h-48 overflow-y-auto font-mono text-slate-400 bg-[#05070A] border border-[#1E293B] rounded p-2 whitespace-pre-wrap" data-testid="vpn-log">{st.log.join("\n")}</pre>
            </details>
          )}
        </div>
        <DialogFooter>
          {up ? (
            <Button variant="outline" onClick={disconnect} disabled={busy} className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800" data-testid="vpn-disconnect"><Unplug className="w-4 h-4 mr-1" /> Desconectar</Button>
          ) : needOtp ? (
            <>
              <Button variant="ghost" onClick={disconnect} disabled={busy} className="text-slate-400">Cancelar</Button>
              <Button onClick={sendOtp} disabled={busy || !otp.trim()} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="vpn-send-otp">
                {busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <KeyRound className="w-4 h-4 mr-1" />} Enviar token
              </Button>
            </>
          ) : (
            <Button onClick={connect} disabled={busy || working || !daemon} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="vpn-connect">
              {busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Plug className="w-4 h-4 mr-1" />} Conectar
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const emptyVpn = { name: "", host: "", port: 443, username: "", password: "", realm: "", routes: "" };

function VpnForm({ initial, onClose, onSaved }) {
  const [f, setF] = useState(initial ? { ...emptyVpn, ...initial, password: "", routes: (initial.routes || []).join("\n") } : emptyVpn);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const body = { name: f.name, host: f.host, port: Number(f.port) || 443, username: f.username, password: f.password || null, realm: f.realm,
                   routes: f.routes.split(/[\s,;]+/).filter(Boolean), trusted_certs: initial?.trusted_certs || [] };
    try {
      if (initial?.id) await api.put(`/vpns/${initial.id}`, body); else await api.post("/vpns", body);
      toast.success("VPN salva"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-lg" data-testid="vpn-form">
        <DialogHeader><DialogTitle>{initial?.id ? "Editar VPN" : "Nova VPN (FortiGate SSL-VPN)"}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label>Nome</Label><Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="ex.: VPNJVE" className={inputCls} data-testid="vpn-name" /></div>
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2"><Label>Gateway</Label><Input value={f.host} onChange={e => setF({ ...f, host: e.target.value })} placeholder="vpn.empresa.com.br ou IP" className={inputCls} data-testid="vpn-host" /></div>
            <div><Label>Porta</Label><Input type="number" value={f.port} onChange={e => setF({ ...f, port: e.target.value })} className={inputCls} data-testid="vpn-port" /></div>
          </div>
          <div className="text-[11px] text-slate-500 -mt-1">É o "Gateway remoto" da conexão no FortiClient (Configurar VPN / editar); a porta costuma ser 443 ou 10443.</div>
          <div className="grid grid-cols-2 gap-2">
            <div><Label>Usuário</Label><Input value={f.username} onChange={e => setF({ ...f, username: e.target.value })} className={inputCls} data-testid="vpn-user" /></div>
            <div><Label>Senha</Label><Input type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })}
                                             placeholder={initial?.has_password ? "•••••••• (mantida)" : ""} className={inputCls} data-testid="vpn-pass" /></div>
          </div>
          <div><Label>Realm (opcional)</Label><Input value={f.realm} onChange={e => setF({ ...f, realm: e.target.value })} className={inputCls} /></div>
          <div>
            <Label>Redes extras pela VPN (opcional)</Label>
            <Textarea value={f.routes} onChange={e => setF({ ...f, routes: e.target.value })} rows={2} placeholder={"ex.: 10.50.0.0/16"} className={`${inputCls} text-xs`} data-testid="vpn-routes" />
            <div className="text-[11px] text-slate-500 mt-1">Os IPs dos agentes marcados com esta VPN já entram sozinhos. O resto do servidor continua saindo normal (a rota padrão nunca vai para a VPN).</div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="vpn-save">{busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Seção de VPNs na página de Agentes. */
export function VpnSection({ vpns, daemon, reload, openId, onOpened }) {
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
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">VPNs no servidor</div>
        <Button size="sm" variant="ghost" onClick={() => setForm({})} className="h-7 text-xs text-[#4DA3FF] hover:bg-[#007AFF]/15" data-testid="vpn-new"><Plus className="w-3.5 h-3.5 mr-1" /> Nova VPN</Button>
      </div>
      {!daemon && vpns.length > 0 && (
        <div className="text-xs text-amber-300 mb-2 flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          O serviço de VPN não está rodando no servidor: coloque <code className="text-slate-200">COMPOSE_PROFILES=vpn</code> no deploy/.env e rode o update.sh.</div>
      )}
      {vpns.length === 0 ? (
        <div className="text-sm text-slate-500 border border-dashed border-[#1E293B] rounded p-3">
          Jumps que só respondem por uma VPN FortiGate (SSL-VPN) podem ser alcançados direto do servidor: cadastre a VPN aqui, marque os agentes com ela e conecte digitando só o token — sem depender do seu PC ligado.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {vpns.map(v => (
            <div key={v.id} className="border border-[#1E293B] rounded-lg bg-[#111722] p-3" data-testid={`vpn-${v.id}`}>
              <div className="flex items-center gap-2">
                <div className="text-slate-100 font-semibold">{v.name}</div>
                <VpnState state={v.status.state} />
                <div className="ml-auto flex gap-1">
                  <button onClick={() => setForm(v)} className="text-slate-500 hover:text-slate-200 p-1" title="Editar"><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={() => del(v)} className="text-slate-500 hover:text-red-400 p-1" title="Apagar"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>
              <div className="text-[11px] font-mono text-slate-500 mt-0.5">{v.username}@{v.host}:{v.port}{v.status.state === "up" && v.status.ip ? ` · ${v.status.iface || "ppp?"} ${v.status.ip}` : ""}</div>
              <div className="text-xs text-slate-400 mt-1">Agentes: {v.agents.length ? v.agents.join(", ") : <span className="text-slate-600">nenhum — edite o agente e escolha esta VPN</span>}</div>
              {v.status.error && v.status.state !== "up" && <div className="text-[11px] mt-1" style={{ color: STATUS.critical }}>{v.status.error}</div>}
              <div className="flex gap-2 mt-2">
                {v.status.state === "up"
                  ? <Button size="sm" variant="outline" onClick={() => disc(v)} className="h-8 border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800"><Unplug className="w-4 h-4 mr-1" /> Desconectar</Button>
                  : <Button size="sm" onClick={() => setConn(v.id)} className="h-8 bg-[#007AFF] hover:bg-[#0062CC]" data-testid={`vpn-open-${v.id}`}><KeyRound className="w-4 h-4 mr-1" /> {v.status.state === "need_otp" ? "Digitar o token" : "Conectar"}</Button>}
                {v.status.state === "up" && <Button size="sm" variant="ghost" onClick={() => setConn(v.id)} className="h-8 text-slate-400 hover:bg-slate-800">Detalhes</Button>}
              </div>
            </div>
          ))}
        </div>
      )}
      {form && <VpnForm initial={form.id ? form : null} onClose={() => setForm(null)} onSaved={() => { setForm(null); reload(); }} />}
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
                  className={`w-full flex items-center gap-2 rounded-md border border-[#1E293B] hover:bg-slate-800/50 ${mini ? "justify-center py-1.5" : "px-2 py-1.5"}`}>
            <Icon className={`w-3.5 h-3.5 shrink-0 ${Icon === Loader2 ? "animate-spin" : ""}`} style={{ color }} />
            {!mini && <><span className="text-xs text-slate-300 truncate">VPN {v.name}</span><span className="ml-auto text-[10px] font-mono" style={{ color }}>{label}</span></>}
          </button>
        );
      })}
      {conn && <VpnConnectDialog vpn={data.items.find(v => v.id === conn)} onClose={() => setConn(null)} reload={reload} daemon={data.daemon} />}
    </div>
  );
}
