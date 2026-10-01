import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { Globe, Plus, X, RotateCw, ExternalLink, Loader2, Server, Radio, ShieldAlert } from "lucide-react";

const DIRECT = "__direct__";

// endereço da porta da sessão no mesmo host em que o BastiON foi aberto
export function webSessionUrl(s, entry = true) {
  const base = `${window.location.protocol}//${window.location.hostname}:${s.port}`;
  return entry && s.entry ? base + s.entry : base + "/";
}

function OpenDialog({ open, onOpenChange, devices, agents, isAdmin, preset, onOpened }) {
  const [mode, setMode] = useState("device");
  const [q, setQ] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setBusy(false);
    if (preset?.device) {
      setMode("device"); setDeviceId(preset.device.id); setQ("");
      setUrl(preset.device.web_url || `http://${preset.device.host}/`);
    } else {
      setMode("device"); setDeviceId(""); setUrl(""); setQ("");
      setAgentId(agents[0]?.id || (isAdmin ? DIRECT : ""));
    }
  }, [open, preset]); // eslint-disable-line react-hooks/exhaustive-deps

  const dev = devices.find(d => d.id === deviceId);
  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    return devices.filter(d => !t || d.name.toLowerCase().includes(t) || (d.host || "").includes(t)
      || (d.tags || []).some(x => x.toLowerCase().includes(t))).slice(0, 60);
  }, [devices, q]);
  const agentName = (id) => agents.find(a => a.id === id)?.name;

  const pick = (d) => { setDeviceId(d.id); setUrl(d.web_url || `http://${d.host}/`); };
  const quick = (scheme, port) => {
    if (!dev) return;
    const def = (scheme === "http" && port === 80) || (scheme === "https" && port === 443);
    setUrl(`${scheme}://${dev.host}${def ? "" : ":" + port}/`);
  };

  const submit = async () => {
    const body = mode === "device"
      ? { url, device_id: deviceId }
      : { url, agent_id: agentId === DIRECT ? null : agentId };
    if (mode === "device" && !deviceId) return toast.error("Escolha o equipamento");
    if (!url.trim()) return toast.error("Informe o endereço");
    setBusy(true);
    try {
      const r = await api.post("/web/sessions", body);
      onOpened(r.data);
      onOpenChange(false);
    } catch (e) {
      toast.error(formatApiError(e));
    } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg" data-testid="web-open-dialog">
        <DialogHeader><DialogTitle>Abrir página web</DialogTitle></DialogHeader>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs font-mono">
          {[["device", "Equipamento", Server], ["url", "Endereço por agente", Radio]].map(([k, l, I]) => (
            <button key={k} type="button" onClick={() => setMode(k)} data-testid={`web-mode-${k}`}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded ${mode === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>
              <I className="w-3.5 h-3.5" />{l}
            </button>
          ))}
        </div>

        {mode === "device" ? (
          <div className="space-y-3">
            {!preset?.device && (
              <div>
                <Label>Equipamento</Label>
                <Input value={q} onChange={e => setQ(e.target.value)} placeholder="nome, IP ou tag…" data-testid="web-device-search"
                  className="bg-sunken border-line font-mono mt-1" />
                <div className="mt-1 max-h-44 overflow-y-auto border border-line rounded-md divide-y divide-line">
                  {filtered.length === 0 && <div className="p-3 text-xs text-slate-500 font-mono">Nenhum equipamento</div>}
                  {filtered.map(d => (
                    <button key={d.id} type="button" onClick={() => pick(d)} data-testid={`web-pick-${d.id}`}
                      className={`w-full text-left px-3 py-1.5 text-xs flex justify-between gap-2 ${d.id === deviceId ? "bg-brand/15 text-brand-soft" : "text-slate-300 hover:bg-slate-800/60"}`}>
                      <span className="truncate">{d.name}</span>
                      <span className="font-mono text-slate-500 shrink-0">{d.host}{d.agent_id ? ` · ${agentName(d.agent_id) || "agente"}` : ""}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            {dev && (
              <div className="text-xs font-mono text-slate-400" data-testid="web-device-route">
                <span className="text-slate-200">{dev.name}</span> · via {dev.agent_id ? (agentName(dev.agent_id) || "agente") : "BastiON (direto)"}
              </div>
            )}
          </div>
        ) : (
          <div>
            <Label>Sair por</Label>
            <Select value={agentId} onValueChange={setAgentId}>
              <SelectTrigger className="bg-sunken border-line mt-1" data-testid="web-agent-select"><SelectValue placeholder="Escolha o agente" /></SelectTrigger>
              <SelectContent className="bg-surface border-line text-slate-100">
                {isAdmin && <SelectItem value={DIRECT}>BastiON (direto do servidor)</SelectItem>}
                {agents.map(a => <SelectItem key={a.id} value={a.id}>{a.name}{a.status === "offline" ? " (offline)" : ""}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        )}

        <div>
          <Label>Endereço</Label>
          <Input value={url} onChange={e => setUrl(e.target.value)} placeholder="http://10.0.0.1/  ou  https://10.0.0.1:8443/"
            onKeyDown={e => e.key === "Enter" && submit()} data-testid="web-url"
            className="bg-sunken border-line font-mono mt-1" />
          {mode === "device" && dev && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {[["http", 80], ["https", 443], ["http", 8080], ["https", 8443]].map(([s, p]) => (
                <button key={s + p} type="button" onClick={() => quick(s, p)} data-testid={`web-quick-${s}-${p}`}
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-line text-slate-400 hover:text-slate-200">{s}:{p}</button>
              ))}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} className="border-line bg-transparent">Cancelar</Button>
          <Button onClick={submit} disabled={busy} data-testid="web-open-submit" className="bg-brand hover:bg-brand-strong">
            {busy ? <Loader2 className="w-4 h-4 animate-spin mr-1.5" /> : <Globe className="w-4 h-4 mr-1.5" />}Abrir
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function WebAccess() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [params, setParams] = useSearchParams();
  const [info, setInfo] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [devices, setDevices] = useState([]);
  const [agents, setAgents] = useState([]);
  const [active, setActive] = useState(null);
  const [dlg, setDlg] = useState(false);
  const [preset, setPreset] = useState(null);
  const [reloads, setReloads] = useState({});
  const https = window.location.protocol === "https:";

  const load = async () => {
    const [i, s, d, a] = await Promise.all([api.get("/web/info"), api.get("/web/sessions"), api.get("/devices"), api.get("/agents")]);
    setInfo(i.data); setSessions(s.data); setDevices(d.data); setAgents(a.data);
    setActive(cur => (s.data.some(x => x.id === cur) ? cur : s.data[s.data.length - 1]?.id || null));
    return d.data;
  };

  useEffect(() => {
    load().then(devs => {
      const id = params.get("device");
      if (id) {
        const dev = devs.find(x => x.id === id);
        if (dev) { setPreset({ device: dev }); setDlg(true); }
        setParams({}, { replace: true });
      }
    }).catch(e => toast.error(formatApiError(e)));
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // após cada carga da página (ex.: equipamento mandou de http para https) o endereço mostrado é atualizado
  const refresh = () => api.get("/web/sessions").then(r => {
    setSessions(prev => r.data.map(n => ({ ...n, entry: n.entry || prev.find(p => p.id === n.id)?.entry })));
    setActive(cur => (r.data.some(x => x.id === cur) ? cur : r.data[r.data.length - 1]?.id || null));
  }).catch(() => {});

  const opened = (s) => {
    setSessions(prev => [...prev.filter(x => x.id !== s.id), s]);
    setActive(s.id);
    setReloads(r => ({ ...r, [s.id]: (r[s.id] || 0) + 1 }));
    if (https) window.open(webSessionUrl(s), "_blank", "noopener");
  };

  const close = async (s) => {
    try { await api.delete(`/web/sessions/${s.id}`); } catch (e) { toast.error(formatApiError(e)); }
    setSessions(prev => prev.filter(x => x.id !== s.id));
    if (active === s.id) setActive(sessions.filter(x => x.id !== s.id).slice(-1)[0]?.id || null);
  };

  const cur = sessions.find(s => s.id === active);

  return (
    <div className="flex-1 flex flex-col min-h-0" data-testid="web-page">
      <div className="px-4 md:px-6 pt-4 pb-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Acesso Web</h1>
        </div>
        <Button onClick={() => { setPreset(null); setDlg(true); }} disabled={info && !info.enabled}
          className="bg-brand hover:bg-brand-strong" data-testid="web-new-btn">
          <Plus className="w-4 h-4 mr-1.5" />Abrir página
        </Button>
      </div>

      {info && !info.enabled && (
        <Card className="mx-4 md:mx-6 mb-3 p-3 bg-amber-950/30 border-amber-700/40 text-amber-200 text-sm" data-testid="web-disabled">
          O Acesso Web está desligado: nenhuma porta de WEB_PROXY_PORTS ficou livre no servidor. Veja o README (seção 20).
        </Card>
      )}

      {sessions.length > 0 && (
        <div className="px-4 md:px-6 flex gap-1 overflow-x-auto border-b border-line" data-testid="web-tabs">
          {sessions.map(s => (
            <div key={s.id} onClick={() => setActive(s.id)} data-testid={`web-tab-${s.id}`}
              className={`group flex items-center gap-2 pl-3 pr-1.5 py-1.5 rounded-t-md border border-b-0 cursor-pointer text-xs shrink-0 ${s.id === active ? "bg-surface border-line text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
              <Globe className="w-3.5 h-3.5 text-brand-soft" />
              <span className="max-w-[180px] truncate">{s.label}</span>
              <button onClick={(e) => { e.stopPropagation(); close(s); }} title="Fechar sessão" data-testid={`web-close-${s.id}`}
                className="p-0.5 rounded hover:bg-slate-700/60 text-slate-500 hover:text-slate-200"><X className="w-3 h-3" /></button>
            </div>
          ))}
        </div>
      )}

      {cur ? (
        <div className="flex-1 flex flex-col min-h-0 px-4 md:px-6 pb-4">
          <div className="flex flex-wrap items-center gap-2 py-2 text-xs font-mono">
            <span className="text-slate-200 truncate max-w-[40ch]" data-testid="web-cur-url">{cur.target}</span>
            <span className="text-slate-500">via {cur.via}</span>
            <span className="text-slate-600">· porta {cur.port}</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" title="Recarregar" data-testid="web-reload"
                onClick={() => setReloads(r => ({ ...r, [cur.id]: (r[cur.id] || 0) + 1 }))} className="text-slate-300 hover:bg-slate-800 h-7">
                <RotateCw className="w-3.5 h-3.5" /><span className="hidden sm:inline ml-1">Recarregar</span>
              </Button>
              <Button size="sm" variant="ghost" title="Abrir em nova aba" data-testid="web-newtab"
                onClick={() => window.open(webSessionUrl(cur), "_blank", "noopener")} className="text-slate-300 hover:bg-slate-800 h-7">
                <ExternalLink className="w-3.5 h-3.5" /><span className="hidden sm:inline ml-1">Nova aba</span>
              </Button>
            </div>
          </div>
          {https ? (
            <Card className="flex-1 flex flex-col items-center justify-center gap-3 bg-surface border-line text-center p-6" data-testid="web-https-note">
              <ShieldAlert className="w-8 h-8 text-amber-400" />
              <div className="text-slate-200 text-sm max-w-md">Você abriu o BastiON por HTTPS e a página do equipamento vem pela porta {cur.port} em HTTP. O navegador não mostra uma dentro da outra, então ela abre numa aba separada.</div>
              <Button onClick={() => window.open(webSessionUrl(cur), "_blank", "noopener")} className="bg-brand hover:bg-brand-strong">
                <ExternalLink className="w-4 h-4 mr-1.5" />Abrir {cur.label}
              </Button>
            </Card>
          ) : (
            <div className="relative flex-1 min-h-[320px] rounded-md border border-line overflow-hidden bg-white">
              {sessions.map(s => (
                <iframe key={`${s.id}-${reloads[s.id] || 0}`} title={s.label} src={webSessionUrl(s)} data-testid={`web-frame-${s.id}`}
                  allow="clipboard-read; clipboard-write; fullscreen" onLoad={refresh}
                  className={`absolute inset-0 w-full h-full border-0 ${s.id === active ? "block" : "hidden"}`} />
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="text-center max-w-md space-y-2" data-testid="web-empty">
            <Globe className="w-10 h-10 text-slate-600 mx-auto" />
            <div className="text-slate-200">Nenhuma página aberta</div>
            <div className="text-sm text-slate-500">Abra a interface web de um equipamento (http/https) pelo mesmo caminho do terminal: pelos agentes ou direto do servidor. Também dá para abrir pelo ícone <Globe className="w-3.5 h-3.5 inline" /> na lista de Equipamentos.</div>
            {info?.idle_minutes ? <div className="text-xs text-slate-600 font-mono">sessões sem uso fecham sozinhas em {info.idle_minutes} min</div> : null}
          </div>
        </div>
      )}

      <OpenDialog open={dlg} onOpenChange={setDlg} devices={devices} agents={agents} isAdmin={isAdmin} preset={preset} onOpened={opened} />
    </div>
  );
}
