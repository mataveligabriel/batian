import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Search, PlayCircle, ListTree, Video } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { SessionPlayer } from "@/components/SessionPlayer";

const KINDS = [["", "todos os tipos"], ["terminal", "Terminal"], ["batch", "Lote / scripts"], ["web", "Acesso Web"], ["rdp", "RDP"],
  ["winbox", "Winbox"], ["ia", "Assistente IA"], ["replay", "Replays assistidos"]];
const KIND_LABEL = Object.fromEntries(KINDS.filter(k => k[0]));
const dur = (s) => (s == null ? "—" : s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}min ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}min`);

export default function Sessions() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState([]);
  const [all, setAll] = useState(isAdmin);
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [onlyRec, setOnlyRec] = useState(false);
  const [play, setPlay] = useState(null);
  const [cmds, setCmds] = useState(null);
  const load = useCallback(() => {
    api.get("/sessions", { params: { limit: 300, all: all && isAdmin ? 1 : 0, kind, q, recorded: onlyRec ? 1 : 0 } })
      .then(r => setItems(r.data || [])).catch(e => toast.error(formatApiError(e)));
  }, [all, isAdmin, kind, q, onlyRec]);
  useEffect(() => { const t = setTimeout(load, q ? 300 : 0); return () => clearTimeout(t); }, [load, q]);
  const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";

  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="sessions-page">
      <div className="mb-4">
        <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Histórico de Sessões</h1>
        <p className="text-sm text-slate-400 mt-1">Quem acessou o quê e quando. Sessões de terminal gravadas podem ser assistidas de novo (replay).</p>
      </div>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        {isAdmin && (
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
            {[[true, "Todos os usuários"], [false, "Só as minhas"]].map(([v, l]) => (
              <button key={l} onClick={() => setAll(v)} data-testid={`sessions-all-${v}`}
                      className={`px-3 h-7 rounded ${all === v ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
            ))}
          </div>
        )}
        <select value={kind} onChange={e => setKind(e.target.value)} className="h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200" data-testid="sessions-kind">
          {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-slate-300"><input type="checkbox" className="accent-brand" checked={onlyRec} onChange={e => setOnlyRec(e.target.checked)} data-testid="sessions-rec" /> só gravadas</label>
        <div className="relative flex-1 min-w-[12rem]">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar equipamento ou usuário…" className="pl-8 bg-sunken border-line h-9" data-testid="sessions-q" />
        </div>
      </div>
      <Card className="bg-surface border-line overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 bg-panel">
            <tr>
              <th className="text-left px-4 py-3">Tipo</th>
              <th className="text-left px-4 py-3">Usuário</th>
              <th className="text-left px-4 py-3">Equipamento</th>
              <th className="text-left px-4 py-3">Início</th>
              <th className="text-left px-4 py-3">Duração</th>
              <th className="text-right px-4 py-3">Auditoria</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {items.length === 0 && <tr><td colSpan={6} className="py-10 text-center text-slate-500 font-mono">Nenhuma sessão</td></tr>}
            {items.map(s => (
              <tr key={s.id} data-testid={`session-row-${s.id}`}>
                <td className="px-4 py-2.5"><Badge className="bg-panel border border-line text-slate-300 text-[10px]">{KIND_LABEL[s.kind] || s.kind}</Badge>
                  {s.kind === "replay" && <span className="block text-[11px] text-slate-500 mt-0.5">de {s.replay_user}</span>}</td>
                <td className="px-4 py-2.5 text-slate-100 font-mono text-xs">{s.user_email}</td>
                <td className="px-4 py-2.5 text-slate-100">{s.device_name}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-slate-400 whitespace-nowrap">{fmt(s.started_at)}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-slate-400 whitespace-nowrap">{s.ended_at || s.kind !== "terminal" ? dur(s.duration_seconds) : <span className="text-emerald-400">em andamento</span>}</td>
                <td className="px-4 py-2.5 text-right whitespace-nowrap">
                  {s.recording?.file && (
                    <Button size="sm" variant="ghost" onClick={() => setPlay(s)} className="text-brand-soft hover:bg-brand/15 h-7" data-testid={`session-play-${s.id}`}>
                      <PlayCircle className="w-4 h-4 mr-1" /> Assistir
                    </Button>
                  )}
                  {s.commands_count > 0 && (
                    <Button size="sm" variant="ghost" onClick={() => setCmds(s)} className="text-slate-300 h-7" data-testid={`session-cmds-${s.id}`}>
                      <ListTree className="w-4 h-4 mr-1" /> {s.commands_count} comando(s)
                    </Button>
                  )}
                  {!s.recording?.file && !s.commands_count && s.kind === "terminal" && <span className="text-[11px] text-slate-600 inline-flex items-center gap-1"><Video className="w-3.5 h-3.5" /> sem gravação</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {play && <SessionPlayer session={play} onClose={() => { setPlay(null); load(); }} />}
      {cmds && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => setCmds(null)}>
          <Card className="bg-surface border-line p-4 max-w-2xl w-full max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()} data-testid="session-cmds">
            <div className="text-sm text-slate-100 mb-2">{cmds.device_name} · {cmds.user_email} · {fmt(cmds.started_at)}</div>
            <pre className="text-xs font-mono text-slate-200 bg-sunken border border-line rounded p-3 whitespace-pre-wrap">{(cmds.commands || []).join("\n")}</pre>
          </Card>
        </div>
      )}
    </div>
  );
}
