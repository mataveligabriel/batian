import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { Activity, Server, Radio, Clock, RefreshCw, Zap, ArrowUpRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { NocPanels } from "@/components/noc/NocPanels";
import { canUse } from "@/lib/modules";

const Stat = ({ label, value, sub, color, icon: Icon, testid }) => (
  <Card
    data-testid={testid}
    className="bg-surface border-line p-5 hover:border-line2 transition-colors"
  >
    <div className="flex items-start justify-between">
      <div>
        <div className="text-xs text-slate-400">{label}</div>
        <div className="mt-1.5 font-heading text-[2rem] leading-tight font-semibold text-slate-50 tabular-nums">{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
      </div>
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center border ${color}`}>
        <Icon className="w-[18px] h-[18px]" />
      </div>
    </div>
  </Card>
);

export default function Dashboard() {
  const { user } = useAuth();
  if (user?.role === "viewer") return <ViewerNoc user={user} />;
  return <FullNoc />;
}

/** Perfil View: o Painel NOC é só os mapas e dashboards liberados. */
function ViewerNoc({ user }) {
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="dashboard-page">
      <div className="mb-4">
        <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Painel NOC</h1>
        <p className="text-slate-500 mt-1 text-xs">Olá, {user.name || user.email} — visualização dos mapas e dashboards liberados para você.</p>
      </div>
      <NocPanels big />
    </div>
  );
}

function FullNoc() {
  const { user } = useAuth();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const nav = useNavigate();

  const load = async () => {
    try {
      const { data } = await api.get("/stats");
      setStats(data);
    } catch (e) {
      toast.error("Falha ao carregar métricas");
    } finally {
      setLoading(false);
    }
  };

  const pingAll = async () => {
    toast.info("Executando ping em todos os equipamentos…");
    try {
      await api.post("/devices/ping-all");
      await load();
      toast.success("Ping concluído");
    } catch (e) {
      toast.error("Falha no ping global");
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="dashboard-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-6">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Painel NOC</h1>
          <p className="text-slate-400 mt-2 text-sm max-w-2xl">
            Equipamentos, agentes e sessões da sua rede, atualizados a cada 20 segundos.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={load} data-testid="refresh-stats-btn" className="border-line bg-surface text-slate-200 hover:bg-slate-800">
            <RefreshCw className="w-4 h-4 mr-2" /> Atualizar
          </Button>
          <Button onClick={pingAll} data-testid="ping-all-btn" className="bg-brand hover:bg-brand-strong">
            <Zap className="w-4 h-4 mr-2" /> Ping em todos
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat testid="stat-total-devices" label="Equipamentos" value={stats?.total_devices ?? "—"}
              sub={`${stats?.online_devices ?? 0} online`}
              color="border-line bg-white/[0.03] text-slate-400" icon={Server} />
        <Stat testid="stat-online" label="Online" value={stats?.online_devices ?? "—"}
              sub={stats?.offline_devices ? `${stats.offline_devices} offline` : "todos ativos"}
              color="border-on/30 bg-on/10 text-on" icon={Activity} />
        <Stat testid="stat-agents" label="Agentes remotos" value={`${stats?.online_agents ?? 0}/${stats?.total_agents ?? 0}`}
              sub="túneis ativos"
              color="border-line bg-white/[0.03] text-slate-400" icon={Radio} />
        <Stat testid="stat-sessions-today" label="Sessões hoje" value={stats?.sessions_today ?? "—"}
              sub="terminal + lote"
              color="border-line bg-white/[0.03] text-slate-400" icon={Clock} />
      </div>

      {(canUse(user, "maps") || canUse(user, "dashboards")) && <div className="mt-6"><NocPanels /></div>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-6">
        <Card className="bg-surface border-line p-5 lg:col-span-2" data-testid="recent-sessions-card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="font-heading text-base font-semibold text-slate-100">Sessões recentes</h2>
            </div>
            {canUse(user, "sessions") && <Button variant="ghost" size="sm" onClick={() => nav("/sessions")} className="text-slate-400 hover:text-slate-100" data-testid="view-all-sessions">
              Ver tudo <ArrowUpRight className="w-3.5 h-3.5 ml-1" />
            </Button>}
          </div>
          <div className="divide-y divide-line">
            {(stats?.recent_sessions || []).length === 0 && (
              <div className="py-6 text-sm text-slate-500 font-mono">Nenhuma sessão registrada ainda.</div>
            )}
            {(stats?.recent_sessions || []).map((s) => (
              <div key={s.id} className="py-3 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="text-sm text-slate-100 font-medium truncate">{s.device_name}</div>
                  <div className="text-xs text-slate-500 font-mono truncate">{s.user_email} · {s.kind}</div>
                </div>
                <div className="text-xs text-slate-500 font-mono shrink-0">
                  {new Date(s.started_at).toLocaleString("pt-BR")}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="bg-surface border-line p-5" data-testid="quick-actions-card">
          <h2 className="font-heading text-base font-semibold text-slate-100">Atalhos</h2>
          <div className="mt-4 space-y-2">
            {canUse(user, "terminal") && (
              <Button onClick={() => nav("/terminal")} data-testid="quick-open-terminal"
                className="w-full justify-start font-normal bg-white/[0.03] border border-line text-slate-200 hover:bg-white/[0.07] hover:text-slate-50">
                Abrir terminal SSH
              </Button>
            )}
            {canUse(user, "batch") && (
              <Button onClick={() => nav("/batch")} data-testid="quick-batch"
                className="w-full justify-start font-normal bg-white/[0.03] border border-line text-slate-200 hover:bg-white/[0.07] hover:text-slate-50">
                Executar script em lote
              </Button>
            )}
            {canUse(user, "devices") && (
              <Button onClick={() => nav("/devices")} data-testid="quick-devices"
                className="w-full justify-start font-normal bg-white/[0.03] border border-line text-slate-200 hover:bg-white/[0.07] hover:text-slate-50">
                Gerenciar equipamentos
              </Button>
            )}
            {canUse(user, "agents") && (
              <Button onClick={() => nav("/agents")} data-testid="quick-agents"
                className="w-full justify-start font-normal bg-white/[0.03] border border-line text-slate-200 hover:bg-white/[0.07] hover:text-slate-50">
                Ver agentes remotos
              </Button>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
