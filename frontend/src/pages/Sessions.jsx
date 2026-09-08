import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { History } from "lucide-react";

export default function Sessions() {
  const [items, setItems] = useState([]);
  useEffect(() => { api.get("/sessions").then(r => setItems(r.data)); }, []);
  const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";
  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="sessions-page">
      <div className="mb-6">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Auditoria</div>
        <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Histórico de Sessões</h1>
      </div>
      <Card className="bg-[#111722] border-[#1E293B] overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-widest text-slate-500 font-mono bg-[#0B111C]">
            <tr>
              <th className="text-left px-4 py-3">Tipo</th>
              <th className="text-left px-4 py-3">Usuário</th>
              <th className="text-left px-4 py-3">Equipamento</th>
              <th className="text-left px-4 py-3">Início</th>
              <th className="text-left px-4 py-3">Fim</th>
              <th className="text-left px-4 py-3">Duração</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E293B]">
            {items.length === 0 && <tr><td colSpan={6} className="py-10 text-center text-slate-500 font-mono">Nenhuma sessão ainda</td></tr>}
            {items.map(s => (
              <tr key={s.id} data-testid={`session-row-${s.id}`}>
                <td className="px-4 py-3"><Badge className="bg-[#0B111C] border border-[#1E293B] text-slate-300 text-[10px]">{s.kind}</Badge></td>
                <td className="px-4 py-3 text-slate-100 font-mono text-xs">{s.user_email}</td>
                <td className="px-4 py-3 text-slate-100">{s.device_name}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{fmt(s.started_at)}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{fmt(s.ended_at)}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{s.duration_seconds != null ? `${s.duration_seconds}s` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
