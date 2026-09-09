import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Activity, Archive, BellRing, Send, Zap, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "nunca";

export default function Automation() {
  const [s, setS] = useState(null);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [alerts, setAlerts] = useState([]);
  const [busy, setBusy] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const load = async () => {
    const [a, b] = await Promise.all([api.get("/automation/settings"), api.get("/alerts", { params: { limit: 20 } })]);
    setS(a.data); setAlerts(b.data);
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    try {
      const { data } = await api.put("/automation/settings", {
        ...s, ping_interval_min: Number(s.ping_interval_min) || 5, backup_hour: Number(s.backup_hour) || 0,
        telegram_bot_token: token || null, clear_telegram_token: clearToken,
      });
      setS(prev => ({ ...prev, ...data })); setToken(""); setClearToken(false);
      toast.success("Automação salva");
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const testAlert = async () => {
    try {
      const { data } = await api.post("/automation/test-alert");
      if (data.error) return toast.error(data.error);
      const parts = [data.telegram && `Telegram: ${data.telegram}`, data.webhook && `Webhook: ${data.webhook}`].filter(Boolean);
      (parts.every(p => p.endsWith("ok")) ? toast.success : toast.warning)(parts.join(" · "));
      load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const pingNow = async () => {
    const { data } = await api.post("/automation/ping-now");
    data.started ? toast.info("Ping de todos os agentes e equipamentos iniciado") : toast.warning(data.reason);
  };

  if (!s) return <div className="p-8 text-slate-500 font-mono text-sm">Carregando…</div>;
  const inputCls = "bg-[#05070A] border-[#1E293B] font-mono";

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="automation-page">
      <div className="mb-6">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Rotinas em segundo plano</div>
        <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Automação & Alertas</h1>
        <p className="text-slate-400 mt-2 text-sm max-w-2xl">Monitoramento automático pela cadeia de saltos, backup diário de configurações e notificações quando um agente ou equipamento cai.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="bg-[#111722] border-[#1E293B] p-5" data-testid="ping-card">
          <div className="flex items-center gap-2 mb-4"><Activity className="w-4 h-4 text-[#4DA3FF]" /><div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Ping automático</div></div>
          <div className="flex items-center justify-between mb-3">
            <Label>Ativo</Label>
            <Switch data-testid="ping-enabled" checked={s.ping_enabled} onCheckedChange={v => setS({ ...s, ping_enabled: v })} disabled={!isAdmin} />
          </div>
          <Label>Intervalo (minutos)</Label>
          <Input data-testid="ping-interval" type="number" min={1} value={s.ping_interval_min} onChange={e => setS({ ...s, ping_interval_min: e.target.value })} className={inputCls} disabled={!isAdmin} />
          <div className="text-[11px] font-mono text-slate-500 mt-3">Último ciclo: {fmt(s.last_ping)}{s.ping_running && <span className="text-amber-400 ml-2">em execução…</span>}</div>
          <Button size="sm" variant="outline" onClick={pingNow} data-testid="ping-now-btn" className="mt-3 border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 w-full">
            <Zap className="w-3.5 h-3.5 mr-1.5" /> Executar ping agora
          </Button>
        </Card>

        <Card className="bg-[#111722] border-[#1E293B] p-5" data-testid="backup-card">
          <div className="flex items-center gap-2 mb-4"><Archive className="w-4 h-4 text-[#4DA3FF]" /><div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Backup diário</div></div>
          <div className="flex items-center justify-between mb-3">
            <Label>Ativo</Label>
            <Switch data-testid="backup-enabled" checked={s.backup_enabled} onCheckedChange={v => setS({ ...s, backup_enabled: v })} disabled={!isAdmin} />
          </div>
          <Label>Hora do dia (0–23, horário do servidor)</Label>
          <Input data-testid="backup-hour" type="number" min={0} max={23} value={s.backup_hour} onChange={e => setS({ ...s, backup_hour: e.target.value })} className={inputCls} disabled={!isAdmin} />
          <div className="text-[11px] font-mono text-slate-500 mt-3">Comandos padrão: Cisco/Datacom/ZTE <code>show running-config</code> · Huawei <code>display current-configuration</code> · Mikrotik <code>/export</code>. Personalize por equipamento.</div>
        </Card>

        <Card className="bg-[#111722] border-[#1E293B] p-5" data-testid="alerts-card">
          <div className="flex items-center gap-2 mb-4"><BellRing className="w-4 h-4 text-[#4DA3FF]" /><div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Notificações</div></div>
          <div className="flex items-center justify-between mb-2">
            <Label>Agente caiu / voltou</Label>
            <Switch data-testid="notify-agents" checked={s.notify_agents} onCheckedChange={v => setS({ ...s, notify_agents: v })} disabled={!isAdmin} />
          </div>
          <div className="flex items-center justify-between mb-3">
            <Label>Equipamento caiu / voltou e falhas de backup</Label>
            <Switch data-testid="notify-devices" checked={s.notify_devices} onCheckedChange={v => setS({ ...s, notify_devices: v })} disabled={!isAdmin} />
          </div>
          <Label>Telegram — token do bot</Label>
          <Input data-testid="telegram-token" type="password" value={token} onChange={e => setToken(e.target.value)} disabled={!isAdmin}
                 placeholder={s.has_telegram_token ? "•••••••• (mantido — digite para trocar)" : "123456:ABC-DEF… (via @BotFather)"} className={inputCls} />
          {s.has_telegram_token && (
            <label className="flex items-center gap-2 mt-1 text-[11px] text-slate-400 cursor-pointer">
              <input type="checkbox" data-testid="clear-telegram-token" checked={clearToken} onChange={e => setClearToken(e.target.checked)} /> remover token
            </label>
          )}
          <Label className="mt-3 block">Telegram — chat ID</Label>
          <Input data-testid="telegram-chat" value={s.telegram_chat_id || ""} onChange={e => setS({ ...s, telegram_chat_id: e.target.value })} placeholder="-1001234567890 ou 123456789" className={inputCls} disabled={!isAdmin} />
          <Label className="mt-3 block">Webhook (Slack / Discord / genérico)</Label>
          <Input data-testid="webhook-url" value={s.webhook_url || ""} onChange={e => setS({ ...s, webhook_url: e.target.value })} placeholder="https://hooks.slack.com/services/…" className={inputCls} disabled={!isAdmin} />
          {isAdmin && (
            <Button size="sm" variant="outline" onClick={testAlert} data-testid="test-alert-btn" className="mt-3 border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 w-full">
              <Send className="w-3.5 h-3.5 mr-1.5" /> Enviar alerta de teste
            </Button>
          )}
        </Card>
      </div>

      {isAdmin && (
        <div className="flex justify-end mt-4">
          <Button onClick={save} disabled={busy} data-testid="save-automation-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            {busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar automação
          </Button>
        </div>
      )}

      <Card className="bg-[#111722] border-[#1E293B] mt-6 overflow-hidden" data-testid="alerts-history">
        <div className="px-4 py-3 border-b border-[#1E293B] text-xs uppercase tracking-widest text-slate-400 font-mono">Últimos alertas enviados</div>
        {alerts.length === 0 && <div className="p-4 text-sm text-slate-500 font-mono">Nenhum alerta ainda.</div>}
        <div className="divide-y divide-[#1E293B]">
          {alerts.map(a => (
            <div key={a.id} className="px-4 py-2.5 text-sm">
              <div className="text-slate-100">{a.title}</div>
              <div className="text-[11px] font-mono text-slate-500">{fmt(a.created_at)} · telegram: {a.results?.telegram || "—"} · webhook: {a.results?.webhook || "—"}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
