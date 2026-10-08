import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { ShieldBan, Plus, Trash2, Save, Loader2, Copy, Clock, Router, CircleDot, AlertTriangle } from "lucide-react";
import { STATUS } from "@/lib/netfmt";
import { fmtRate, selCls, inputCls } from "@/components/flow/flowlib";

const fmtMin = (m) => (m >= 60 && m % 60 === 0 ? `${m / 60} h` : `${m} min`);
const hhmm = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const left = (iso) => {
  const s = Math.max(0, (new Date(iso) - Date.now()) / 1000);
  return s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.ceil(s / 60)} min`;
};
const STATE_COLOR = (st) => st === "Established" ? STATUS.good : st === "Idle" ? STATUS.critical : STATUS.warning;
const STATUS_TXT = { active: "ativa", withdrawn: "removida", expired: "expirou" };

/** Diálogo "Mitigar": usado na aba Ataques e aqui (IP manual). */
export function MitigateDialog({ target, onClose, onDone }) {
  const [cfg, setCfg] = useState(null);
  const [minutes, setMinutes] = useState(30);
  const [ip, setIp] = useState(target?.victim || "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get("/flow/mitigation/settings").then(r => { setCfg(r.data); setMinutes(r.data.default_minutes || 30); }).catch(e => toast.error(formatApiError(e)));
  }, []);
  const go = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/flow/mitigations", { attack_id: target?.id || "", prefix: target?.id ? "" : ip, minutes, reason });
      toast.success(data.extended ? `Prazo de ${data.prefix} estendido` : `${data.prefix} em blackhole por ${fmtMin(minutes)}`);
      onDone?.(data); onClose();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const off = cfg && (!cfg.enabled || !(cfg.peers || []).some(p => p.enabled !== false));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-md" data-testid="mitigate-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><ShieldBan className="w-5 h-5" style={{ color: STATUS.critical }} /> Mitigar com blackhole</DialogTitle></DialogHeader>
        {!cfg ? <Loader2 className="w-5 h-5 animate-spin text-slate-500" /> : off ? (
          <div className="text-sm text-amber-300" data-testid="mitigation-off">A mitigação ainda não está configurada. Configure o BGP e as bordas na aba <b>Mitigação</b>.</div>
        ) : (
          <div className="space-y-3">
            {target?.id ? (
              <div className="text-sm"><span className="font-mono text-lg text-slate-50">{target.victim}</span>
                <div className="text-xs text-slate-400">{target.type} · pico {fmtRate(target.peak_bps)}</div></div>
            ) : (
              <div><Label className="text-xs">IP atacado (seu)</Label>
                <Input value={ip} onChange={e => setIp(e.target.value)} placeholder="203.0.113.10" className={`${inputCls} font-mono`} data-testid="mit-ip" autoFocus /></div>
            )}
            <div>
              <Label className="text-xs">Duração</Label>
              <div className="flex flex-wrap gap-1 mt-1">
                {(cfg.durations || []).filter(m => m <= cfg.max_minutes).map(m => (
                  <button key={m} onClick={() => setMinutes(m)} data-testid={`mit-dur-${m}`}
                          className={`px-2.5 py-1 rounded border text-xs font-mono ${minutes === m ? "border-brand bg-brand/15 text-slate-100" : "border-line text-slate-400 hover:text-slate-200"}`}>{fmtMin(m)}</button>
                ))}
              </div>
            </div>
            <Input value={reason} onChange={e => setReason(e.target.value)} placeholder="motivo (opcional)" className={`${inputCls} text-sm`} />
            <div className="text-xs rounded border p-2.5 flex gap-2" style={{ borderColor: `${STATUS.critical}66`, color: "#fca5a5" }}>
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>O IP fica <b>sem tráfego nenhum</b> (inclusive o legítimo) nas bordas até o prazo acabar ou você remover. O ataque continua ocupando os links de trânsito — o descarte é na sua borda.</span>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={onClose}>Cancelar</Button>
              <Button onClick={go} disabled={busy || (!target?.id && !ip.trim())} data-testid="mit-confirm" style={{ background: STATUS.critical }} className="text-white hover:opacity-90">
                {busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />} Confirmar blackhole
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PeersStatus({ st }) {
  if (!st) return null;
  const peers = st.peers || [];
  return (
    <div className="border border-line rounded p-3" data-testid="bgp-status">
      <div className="flex items-center gap-2 text-sm text-slate-100 mb-2"><Router className="w-4 h-4 text-slate-400" /> Sessões BGP com as bordas
        <span className="ml-auto text-[11px] font-mono" style={{ color: st.daemon_ok ? STATUS.good : STATUS.critical }} data-testid="bgpd-state">
          {st.daemon_ok ? "serviço bgp ativo" : "serviço bgp parado — no servidor: docker compose ps (container bgp)"}</span>
      </div>
      {!st.enabled && <div className="text-xs text-slate-500">Mitigação desligada.</div>}
      {st.enabled && peers.length === 0 && <div className="text-xs text-slate-500">Nenhuma borda cadastrada.</div>}
      <div className="space-y-1">
        {peers.map(p => (
          <div key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs font-mono" data-testid="bgp-peer">
            <CircleDot className="w-3.5 h-3.5" style={{ color: STATE_COLOR(p.state) }} />
            <span className="text-slate-100 w-40 truncate">{p.name || p.ip}</span>
            <span className="text-slate-400 w-28">{p.ip}</span>
            <b style={{ color: STATE_COLOR(p.state) }}>{p.state}</b>
            {p.state === "Established" && <span className="text-slate-400">{(p.advertised || []).length} /32 anunciado(s) · desde {hhmm(p.established_at)}</span>}
            {p.state !== "Established" && p.last_error && <span className="text-amber-300 basis-full pl-6 font-sans">{p.last_error}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

function Settings({ cfg, reload }) {
  const [f, setF] = useState(null);
  const [busy, setBusy] = useState(false);
  const [vendor, setVendor] = useState("huawei");
  const [bip, setBip] = useState("");
  const [snip, setSnip] = useState("");
  useEffect(() => { setF({ ...cfg, comm_txt: (cfg.communities || []).join(" "), prot_txt: (cfg.protect || []).join("\n") }); setBip(cfg.local_address || cfg.router_id || ""); }, [cfg]);
  const loadSnip = useCallback(() => api.get("/flow/mitigation/router-config", { params: { vendor, bastion_ip: bip } }).then(r => setSnip(r.data.config)).catch(() => {}), [vendor, bip]);
  useEffect(() => { loadSnip(); }, [loadSnip, cfg]);
  if (!f) return null;
  const ro = !cfg.is_admin;
  const save = async () => {
    setBusy(true);
    try {
      await api.put("/flow/mitigation/settings", {
        enabled: !!f.enabled, local_as: Number(f.local_as) || 0, router_id: f.router_id, local_address: f.local_address, hold_time: Number(f.hold_time) || 90,
        next_hop: f.next_hop, communities: f.comm_txt.split(/[\s,;]+/).filter(Boolean), no_export: true, local_pref: Number(f.local_pref) || 200,
        peers: f.peers, default_minutes: Number(f.default_minutes) || 30, max_minutes: Number(f.max_minutes) || 1440,
        max_active: Number(f.max_active) || 20, protect: f.prot_txt.split(/[\s,;]+/).filter(Boolean),
      });
      toast.success("Mitigação salva (o serviço bgp aplica em segundos)"); await reload();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const fld = (label, key, hint, ph, tid) => (
    <div><Label className="text-xs">{label}</Label>
      <Input value={f[key] ?? ""} onChange={e => setF({ ...f, [key]: e.target.value })} disabled={ro} placeholder={ph} className={`${inputCls} h-8 font-mono text-sm`} data-testid={tid} />
      {hint && <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>}</div>
  );
  const setPeer = (i, p) => setF({ ...f, peers: f.peers.map((x, j) => j === i ? { ...x, ...p } : x) });
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div className="border border-line rounded p-3 space-y-3" data-testid="mit-settings">
        <div className="flex items-center gap-2 text-sm text-slate-100">BGP do BastiON (blackhole só na borda)
          <label className="ml-auto flex items-center gap-2 text-xs text-slate-300">ligado <Switch checked={!!f.enabled} onCheckedChange={v => setF({ ...f, enabled: v })} disabled={ro} data-testid="mit-enabled" /></label></div>
        <div className="grid grid-cols-2 gap-3">
          {fld("AS (o mesmo das bordas)", "local_as", "iBGP", "263009", "mit-as")}
          {fld("Router-ID", "router_id", "normalmente o IP do servidor", "203.0.113.1", "mit-rid")}
          {fld("IP de origem (opcional)", "local_address", "o IP que a borda vê como vizinho", "")}
          {fld("Next-hop de descarte", "next_hop", "na borda: rota estática → NULL0", "192.0.2.1")}
          {fld("Community de blackhole", "comm_txt", "65535:666 ou, com AS de 4 bytes, 263009:666:0", "65535:666", "mit-comm")}
          {fld("Local-pref", "local_pref", null, "200")}
          {fld("Duração padrão (min)", "default_minutes", null, "30")}
          {fld("Máximo ativas ao mesmo tempo", "max_active", null, "20")}
        </div>
        <div>
          <div className="flex items-center text-xs text-slate-300 mb-1">Bordas (vizinhos iBGP)
            {!ro && <button onClick={() => setF({ ...f, peers: [...f.peers, { ip: "", name: "", remote_as: "", enabled: true }] })} className="ml-auto text-brand-soft flex items-center gap-1" data-testid="mit-add-peer"><Plus className="w-3.5 h-3.5" /> borda</button>}</div>
          {f.peers.length === 0 && <div className="text-[11px] text-slate-500">Nenhuma ainda.</div>}
          {f.peers.map((p, i) => (
            <div key={i} className="flex gap-2 mb-1.5 items-center" data-testid="mit-peer-row">
              <Input value={p.ip} onChange={e => setPeer(i, { ip: e.target.value })} placeholder="IP da borda" disabled={ro} className={`${inputCls} h-8 font-mono text-xs w-36`} />
              <Input value={p.name} onChange={e => setPeer(i, { name: e.target.value })} placeholder="nome" disabled={ro} className={`${inputCls} h-8 text-xs flex-1`} />
              <Input value={p.remote_as || ""} onChange={e => setPeer(i, { remote_as: e.target.value })} placeholder="AS (=local)" disabled={ro} className={`${inputCls} h-8 font-mono text-xs w-24`} />
              <Switch checked={p.enabled !== false} onCheckedChange={v => setPeer(i, { enabled: v })} disabled={ro} />
              {!ro && <button onClick={() => setF({ ...f, peers: f.peers.filter((_, j) => j !== i) })} className="text-slate-500 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>}
            </div>
          ))}
        </div>
        <div>
          <Label className="text-xs">Nunca fazer blackhole de (DNS, servidores, gateways…)</Label>
          <Textarea value={f.prot_txt} onChange={e => setF({ ...f, prot_txt: e.target.value })} disabled={ro} rows={2} className={`${inputCls} font-mono text-xs`} placeholder={"203.0.113.53/32\n198.51.100.0/28"} />
          <div className="text-[10px] text-slate-500 mt-0.5">Só IPs (/32) dentro dos seus blocos próprios ({(cfg.own_prefixes || []).join(", ") || "cadastre em Configuração"}) podem ser mitigados. A rota vai sempre com NO_EXPORT.</div>
        </div>
        {!ro ? <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="mit-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
          : <div className="text-xs text-slate-500">Só o administrador altera.</div>}
      </div>
      <div className="border border-line rounded" data-testid="mit-router-config">
        <div className="px-3 py-2 border-b border-line flex flex-wrap items-center gap-2 text-sm text-slate-100"><Router className="w-4 h-4 text-slate-400" /> Configuração da borda
          <select value={vendor} onChange={e => setVendor(e.target.value)} className={`${selCls} ml-auto`} data-testid="mit-vendor"><option value="huawei">Huawei</option><option value="juniper">Juniper</option></select>
          <Input value={bip} onChange={e => setBip(e.target.value)} placeholder="IP do BastiON" className={`${inputCls} h-8 w-36 font-mono text-xs`} title="IP de onde a borda vê o BastiON" /></div>
        <div className="relative">
          <pre className="text-[11px] font-mono text-slate-200 bg-sunken p-3 overflow-x-auto whitespace-pre max-h-[420px]" data-testid="mit-snippet">{snip}</pre>
          <button onClick={() => { navigator.clipboard?.writeText(snip); toast.success("Copiado"); }} className="absolute top-2 right-2 text-slate-400 hover:text-slate-100" title="Copiar"><Copy className="w-4 h-4" /></button>
        </div>
        <div className="px-3 py-2 text-[11px] text-slate-500 border-t border-line">
          A borda só aceita /32 dos seus blocos com essa community e não manda nada para o BastiON (export deny). Se o serviço bgp parar,
          a sessão cai e a borda retira o blackhole sozinha. Teste antes com um IP seu que não esteja em uso.
        </div>
      </div>
    </div>
  );
}

/** Aba Mitigação: sessões, blackholes ativos, histórico e configuração. */
export function FlowMitigation() {
  const [cfg, setCfg] = useState(null);
  const [st, setSt] = useState(null);
  const [hist, setHist] = useState([]);
  const [dlg, setDlg] = useState(false);
  const [, tick] = useState(0);
  const load = useCallback(async () => {
    const [a, b, c] = await Promise.all([api.get("/flow/mitigation/settings"), api.get("/flow/mitigation/status"), api.get("/flow/mitigations", { params: { limit: 50 } })]);
    setCfg(a.data); setSt(b.data); setHist(c.data);
  }, []);
  useEffect(() => {
    load().catch(e => toast.error(formatApiError(e)));
    const t = setInterval(() => { api.get("/flow/mitigation/status").then(r => setSt(r.data)).catch(() => {}); tick(x => x + 1); }, 10000);
    return () => clearInterval(t);
  }, [load]);
  const act = async (m, what, minutes) => {
    try {
      if (what === "withdraw") {
        if (!window.confirm(`Remover o blackhole de ${m.prefix}? O tráfego (e o ataque, se ainda houver) volta a chegar.`)) return;
        await api.post(`/flow/mitigations/${m.id}/withdraw`);
      } else await api.post(`/flow/mitigations/${m.id}/extend`, { minutes });
      await load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  if (!cfg) return <Loader2 className="w-5 h-5 animate-spin text-slate-500" />;
  const active = st?.active || [];
  const peersUp = (st?.peers || []).filter(p => p.state === "Established").length;
  return (
    <div className="space-y-4 max-w-6xl" data-testid="flow-mitigation">
      <div className="border rounded p-3" style={{ borderColor: active.length ? STATUS.critical : "#262F32" }} data-testid="mit-active">
        <div className="flex items-center gap-2 text-sm text-slate-100 mb-2">
          <ShieldBan className="w-4 h-4" style={{ color: active.length ? STATUS.critical : "#8D9A9D" }} /> Blackholes ativos
          <span className="text-xs font-mono text-slate-400">{active.length} · {peersUp} borda(s) recebendo</span>
          <Button size="sm" onClick={() => setDlg(true)} disabled={!cfg.enabled} className="ml-auto h-8 bg-brand hover:bg-brand-strong" data-testid="mit-manual"><Plus className="w-4 h-4 mr-1" /> Mitigar IP</Button>
        </div>
        {active.length === 0 && <div className="text-xs text-slate-500">Nenhum IP em blackhole.</div>}
        {active.map(m => (
          <div key={m.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-1.5 border-t border-line text-xs font-mono" data-testid="mit-row">
            <span className="text-slate-50 text-sm">{m.prefix}</span>
            <span className="text-slate-300">{m.attack_type || m.reason || "manual"}</span>
            <span className="text-slate-400">por {m.created_by} · {m.channel}</span>
            <span className="flex items-center gap-1" style={{ color: STATUS.warning }}><Clock className="w-3.5 h-3.5" /> sai em {left(m.expires_at)} ({hhmm(m.expires_at)})</span>
            <span className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => act(m, "extend", 30)} className="h-7 text-xs text-slate-300 hover:bg-slate-800" data-testid="mit-extend">+30 min</Button>
              <Button size="sm" variant="ghost" onClick={() => act(m, "withdraw")} className="h-7 text-xs text-red-300 hover:bg-red-950/40" data-testid="mit-withdraw">Remover</Button>
            </span>
          </div>
        ))}
      </div>
      <PeersStatus st={st} />
      <Settings cfg={cfg} reload={load} />
      <div>
        <div className="text-[10px] text-slate-500 mb-1">Histórico</div>
        <div className="border border-line rounded divide-y divide-line" data-testid="mit-history">
          {hist.filter(h => h.status !== "active").length === 0 && <div className="text-xs text-slate-500 p-2">Nada ainda.</div>}
          {hist.filter(h => h.status !== "active").map(h => (
            <div key={h.id} className="px-3 py-1.5 grid grid-cols-2 md:grid-cols-[150px_150px_1fr_120px_1fr] gap-x-3 text-xs font-mono">
              <span className="text-slate-400">{hhmm(h.created_at)}</span><span className="text-slate-100">{h.prefix}</span>
              <span className="text-slate-300 truncate">{h.attack_type || h.reason || "manual"}</span>
              <span className="text-slate-400">{STATUS_TXT[h.status] || h.status}</span>
              <span className="text-slate-500 truncate">{h.created_by}{h.ended_by ? ` → ${h.ended_by}` : ""}</span>
            </div>
          ))}
        </div>
      </div>
      {dlg && <MitigateDialog target={null} onClose={() => setDlg(false)} onDone={load} />}
    </div>
  );
}
