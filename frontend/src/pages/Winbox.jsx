import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { toast } from "sonner";
import { Router, Copy, X, Loader2, Plug, Search, ShieldCheck, Server, Radio } from "lucide-react";

const DIRECT = "__direct__";
const fmtBytes = (n) => (n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(0)} kB` : `${n} B`);
const host = () => window.location.hostname;

async function copyText(t, msg) {
  try { await navigator.clipboard.writeText(t); toast.success(msg || "Copiado"); } catch { toast.error("Não consegui copiar"); }
}

export default function Winbox() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [params, setParams] = useSearchParams();
  const [info, setInfo] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [devices, setDevices] = useState([]);
  const [agents, setAgents] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [adhoc, setAdhoc] = useState({ host: "", port: "8291", agent: "" });

  const refresh = () => api.get("/winbox/sessions").then(r => setSessions(r.data)).catch(() => {});
  useEffect(() => {
    Promise.all([api.get("/winbox/info"), api.get("/devices"), api.get("/agents")]).then(([i, d, a]) => {
      setInfo(i.data); setDevices(d.data || []); setAgents(a.data || []);
      setAdhoc(x => ({ ...x, agent: a.data?.[0]?.id || (isAdmin ? DIRECT : "") }));
      const id = params.get("device");
      if (id) { setParams({}, { replace: true }); const dev = (d.data || []).find(x => x.id === id); if (dev) open({ device_id: dev.id }, dev.id); }
    }).catch(e => toast.error(formatApiError(e)));
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const mks = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = devices.filter(d => d.device_type === "mikrotik" || (d.tags || []).some(x => /mikrotik|mk/i.test(x)));
    return (t ? devices : list).filter(d => !t || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(t)).slice(0, 80);
  }, [devices, q]);
  const agentName = (id) => agents.find(a => a.id === id)?.name;

  async function open(body, key) {
    setBusy(key);
    try {
      const { data } = await api.post("/winbox/sessions", body);
      setSessions(prev => [...prev.filter(x => x.id !== data.id), data]);
      copyText(`${host()}:${data.port}`, `Endereço copiado: ${host()}:${data.port} — cole no Winbox`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  }
  const close = async (s) => {
    try { await api.delete(`/winbox/sessions/${s.id}`); setSessions(prev => prev.filter(x => x.id !== s.id)); } catch (e) { toast.error(formatApiError(e)); }
  };
  const openAdhoc = () => {
    if (!adhoc.host.trim()) return toast.error("Informe o IP do MikroTik");
    open({ host: adhoc.host.trim(), port: Number(adhoc.port) || 8291, agent_id: adhoc.agent === DIRECT ? null : adhoc.agent || null }, "adhoc");
  };

  return (
    <div className="flex-1 overflow-y-auto" data-testid="winbox-page">
      <div className="px-4 md:px-6 pt-4 pb-2">
        <div className="hidden md:block text-xs text-slate-400">MikroTik · porta 8291 pelo BastiON</div>
        <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Winbox</h1>
      </div>

      {info && !info.enabled && (
        <Card className="mx-4 md:mx-6 mb-3 p-3 bg-amber-950/30 border-amber-700/40 text-amber-200 text-sm">O túnel Winbox está desligado: defina WINBOX_PORTS no deploy/.env (README, seção 35).</Card>
      )}

      <div className="px-4 md:px-6 pb-6 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-4 min-w-0">
          <Card className="bg-surface border-line p-4 space-y-3">
            <div className="text-sm text-slate-300 leading-relaxed">
              Clique em <b>Abrir túnel</b>: o BastiON abre uma porta só para o seu IP{info?.your_ip ? <span className="font-mono text-slate-200"> ({info.your_ip})</span> : null} e
              leva a conexão até o MikroTik — direto ou pelos jump servers. No Winbox, conecte em <span className="font-mono text-brand-soft">{host()}:porta</span> com o usuário e a senha do MikroTik.
            </div>
            <div className="relative">
              <Search className="w-4 h-4 absolute left-2.5 top-2.5 text-slate-500" />
              <Input value={q} onChange={e => setQ(e.target.value)} placeholder="procurar equipamento (nome, IP, tag)…" className="bg-sunken border-line pl-8" data-testid="winbox-search" />
            </div>
            <div className="border border-line rounded-md divide-y divide-line max-h-[420px] overflow-y-auto" data-testid="winbox-devices">
              {mks.length === 0 && <div className="p-3 text-xs text-slate-500">Nenhum MikroTik cadastrado{q ? " com esse filtro" : " (tipo mikrotik)"} — use o acesso por IP abaixo.</div>}
              {mks.map(d => (
                <div key={d.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <Router className="w-4 h-4 text-slate-500 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-slate-100 truncate">{d.name}</div>
                    <div className="text-[11px] font-mono text-slate-500 truncate">{d.host}:{d.winbox_port || 8291} · via {d.agent_id ? (agentName(d.agent_id) || "agente") : "BastiON (direto)"}</div>
                  </div>
                  <Button size="sm" onClick={() => open({ device_id: d.id }, d.id)} disabled={!!busy || (info && !info.enabled)} className="h-8 bg-brand hover:bg-brand-strong" data-testid={`winbox-open-${d.id}`}>
                    {busy === d.id ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Plug className="w-3.5 h-3.5 mr-1.5" />}Abrir túnel
                  </Button>
                </div>
              ))}
            </div>
          </Card>

          <Card className="bg-surface border-line p-4 space-y-3" data-testid="winbox-adhoc">
            <div className="text-sm font-semibold text-slate-200">MikroTik sem cadastro (por IP)</div>
            <div className="grid sm:grid-cols-[1fr_6rem_1fr_auto] gap-2 items-end">
              <div><Label className="text-xs text-slate-400">IP do MikroTik</Label>
                <Input value={adhoc.host} onChange={e => setAdhoc({ ...adhoc, host: e.target.value })} placeholder="10.30.1.1" className="bg-sunken border-line font-mono mt-1" data-testid="winbox-host" /></div>
              <div><Label className="text-xs text-slate-400">Porta</Label>
                <Input value={adhoc.port} onChange={e => setAdhoc({ ...adhoc, port: e.target.value.replace(/\D/g, "") })} className="bg-sunken border-line font-mono mt-1" /></div>
              <div><Label className="text-xs text-slate-400">Sair por</Label>
                <select value={adhoc.agent} onChange={e => setAdhoc({ ...adhoc, agent: e.target.value })} data-testid="winbox-agent"
                  className="w-full h-9 mt-1 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100">
                  {isAdmin && <option value={DIRECT}>BastiON (direto do servidor)</option>}
                  {agents.map(a => <option key={a.id} value={a.id}>{a.name}{a.status === "offline" ? " (offline)" : ""}</option>)}
                </select></div>
              <Button onClick={openAdhoc} disabled={!!busy} className="bg-brand hover:bg-brand-strong" data-testid="winbox-adhoc-open">
                {busy === "adhoc" ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Plug className="w-4 h-4 mr-1.5" />}Abrir
              </Button>
            </div>
          </Card>
        </div>

        <Card className="bg-surface border-line p-4 space-y-3 min-w-0 self-start" data-testid="winbox-sessions">
          <div className="text-sm font-semibold text-slate-200">Túneis abertos</div>
          {sessions.length === 0 && <div className="text-xs text-slate-500">Nenhum túnel aberto. Eles fecham sozinhos depois de {info?.idle_minutes || 20} min sem uso.</div>}
          {sessions.map(s => {
            const addr = `${host()}:${s.port}`;
            return (
              <div key={s.id} className="rounded-md border border-line bg-sunken/50 p-3 space-y-2" data-testid={`winbox-s-${s.id}`}>
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-slate-100 truncate">{s.label}</div>
                    <div className="text-[11px] font-mono text-slate-500 truncate">{s.target} · via {s.via}{isAdmin && s.user_email ? ` · ${s.user_email}` : ""}</div>
                  </div>
                  <button onClick={() => close(s)} title="Fechar túnel" data-testid={`winbox-close-${s.id}`} className="p-1 text-slate-500 hover:text-red-300"><X className="w-4 h-4" /></button>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[11px] text-slate-400">No Winbox, conecte em</span>
                  <button onClick={() => copyText(addr, `Copiado: ${addr}`)} data-testid={`winbox-addr-${s.id}`}
                    className="font-mono text-sm px-2 py-1 rounded border border-brand/50 bg-brand/10 text-brand-soft hover:bg-brand/20 flex items-center gap-1.5">
                    {addr}<Copy className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] font-mono text-slate-500">
                  <span className={s.active ? "text-emerald-400" : ""}>{s.active ? `${s.active} conexão(ões) ativa(s)` : "aguardando o Winbox"}</span>
                  <span>{fmtBytes(s.bytes || 0)}</span>
                  <span className="flex items-center gap-1"><ShieldCheck className="w-3 h-3" />só {s.allowed_ip}</span>
                  {s.refused > 0 && <span className="text-amber-300">{s.refused} tentativa(s) de outro IP recusada(s)</span>}
                </div>
              </div>
            );
          })}
          <div className="text-[11px] text-slate-500 leading-relaxed border-t border-line pt-2">
            <Server className="w-3 h-3 inline mr-1" />A porta aceita só o IP que abriu o túnel. Se o Winbox não conectar, confira se a sua rede sai pelo mesmo IP do navegador
            (VPN/proxy no navegador muda o IP) e se as portas {info?.ports?.length ? `${info.ports[0]}–${info.ports[info.ports.length - 1]}` : "do túnel"} estão liberadas no firewall do servidor.
            <Radio className="w-3 h-3 inline mx-1" />MAC-Winbox (camada 2) não passa pelo túnel; use o IP.
          </div>
        </Card>
      </div>
    </div>
  );
}
