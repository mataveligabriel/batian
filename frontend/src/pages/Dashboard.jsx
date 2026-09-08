import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { Activity, Server, Radio, Clock, RefreshCw, Zap, ArrowUpRight } from "lucide-react";
import { useNavigate } from "react-router-dom";

const Stat = ({ label, value, sub, color, icon: Icon, testid }) => (
  <Card
    data-testid={testid}
    className="bg-[#111722] border-[#1E293B] p-5 hover:border-[#334155] transition-colors"
  >
    <div className="flex items-start justify-between">
      <div>
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">{label}</div>
        <div className="mt-2 font-heading text-3xl font-bold text-slate-100">{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-1 font-mono">{sub}</div>}
      </div>
      <div className={`w-10 h-10 rounded-md flex items-center justify-center border ${color}`}>
        <Icon className="w-5 h-5" />
      </div>
    </div>
  </Card>
);

export default function Dashboard() {
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
    <div className="p-8 flex-1 overflow-y-auto" data-testid="dashboard-page">
      <div className="flex items-start justify-between mb-8">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Painel de Operações</div>
          <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">NOC Console</h1>
          <p className="text-slate-400 mt-2 text-sm max-w-2xl">
            Visão em tempo real dos equipamentos, agentes remotos e sessões SSH ativas na sua infraestrutura multi-rede.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={load} data-testid="refresh-stats-btn" className="border-[#1E293B] bg-[#111722] text-slate-200 hover:bg-slate-800">
            <RefreshCw className="w-4 h-4 mr-2" /> Atualizar
          </Button>
          <Button onClick={pingAll} data-testid="ping-all-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <Zap className="w-4 h-4 mr-2" /> Ping Global
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat testid="stat-total-devices" label="Equipamentos" value={stats?.total_devices ?? "—"}
              sub={`${stats?.online_devices ?? 0} online`}
              color="border-[#007AFF]/30 bg-[#007AFF]/10 text-[#4DA3FF]" icon={Server} />
        <Stat testid="stat-online" label="Online" value={stats?.online_devices ?? "—"}
              sub={stats?.offline_devices ? `${stats.offline_devices} offline` : "todos ativos"}
              color="border-emerald-500/30 bg-emerald-500/10 text-emerald-400" icon={Activity} />
        <Stat testid="stat-agents" label="Agentes Remotos" value={`${stats?.online_agents ?? 0}/${stats?.total_agents ?? 0}`}
              sub="túneis ativos"
              color="border-violet-500/30 bg-violet-500/10 text-violet-400" icon={Radio} />
        <Stat testid="stat-sessions-today" label="Sessões Hoje" value={stats?.sessions_today ?? "—"}
              sub="terminal + lote"
              color="border-amber-500/30 bg-amber-500/10 text-amber-400" icon={Clock} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-6">
        <Card className="bg-[#111722] border-[#1E293B] p-5 lg:col-span-2" data-testid="recent-sessions-card">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Sessões Recentes</div>
              <h2 className="font-heading text-xl font-semibold text-slate-100">Auditoria em tempo real</h2>
            </div>
            <Button variant="ghost" size="sm" onClick={() => nav("/sessions")} className="text-slate-400 hover:text-slate-100" data-testid="view-all-sessions">
              Ver tudo <ArrowUpRight className="w-3.5 h-3.5 ml-1" />
            </Button>
          </div>
          <div className="divide-y divide-[#1E293B]">
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

        <Card className="bg-[#111722] border-[#1E293B] p-5" data-testid="quick-actions-card">
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Atalhos</div>
          <h2 className="font-heading text-xl font-semibold text-slate-100 mt-1">Ações rápidas</h2>
          <div className="mt-4 space-y-2">
            <Button onClick={() => nav("/terminal")} data-testid="quick-open-terminal"
              className="w-full justify-start bg-[#05070A] border border-[#1E293B] text-slate-100 hover:bg-slate-800 hover:border-[#334155]">
              Abrir terminal SSH
            </Button>
            <Button onClick={() => nav("/batch")} data-testid="quick-batch"
              className="w-full justify-start bg-[#05070A] border border-[#1E293B] text-slate-100 hover:bg-slate-800 hover:border-[#334155]">
              Executar script em lote
            </Button>
            <Button onClick={() => nav("/devices")} data-testid="quick-devices"
              className="w-full justify-start bg-[#05070A] border border-[#1E293B] text-slate-100 hover:bg-slate-800 hover:border-[#334155]">
              Gerenciar equipamentos
            </Button>
            <Button onClick={() => nav("/agents")} data-testid="quick-agents"
              className="w-full justify-start bg-[#05070A] border border-[#1E293B] text-slate-100 hover:bg-slate-800 hover:border-[#334155]">
              Ver agentes remotos
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
