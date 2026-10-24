import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { PhoneCall, ArrowUp, ArrowDown, Trash2, Plus, AlertTriangle, CalendarClock, Repeat2 } from "lucide-react";

const WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"];
const SEVS = [["critical", "Crítica"], ["high", "Alta ou mais"], ["medium", "Média ou mais"], ["low", "Baixa ou mais"], ["info", "Todas"]];
const fmt = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100";

/** Plantão: quem está agora, próximos turnos e (admin) a escala e o escalonamento. */
export function OncallPanel({ isAdmin }) {
  const [v, setV] = useState(null);
  const [f, setF] = useState(null);
  const [users, setUsers] = useState([]);
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/oncall").then(r => { setV(r.data); setF(r.data); }).catch(e => toast.error(formatApiError(e)));
  useEffect(() => { load(); api.get("/incidents-assignees").then(r => setUsers(r.data)).catch(() => {}); }, []);
  if (!v || !f) return <div className="p-6 text-sm text-slate-500">Carregando…</div>;
  const name = (id) => users.find(u => u.id === id)?.name || v.names?.[id] || "—";
  const set = (patch) => setF({ ...f, ...patch });
  const move = (i, d) => { const m = [...f.members]; [m[i], m[i + d]] = [m[i + d], m[i]]; set({ members: m }); };
  const save = async () => {
    setBusy(true);
    try { const { data } = await api.put("/oncall", f); setV(data); setF(data); toast.success("Plantão salvo"); }
    catch (e) { toast.error(formatApiError(e)); }
    setBusy(false);
  };
  const noTg = (f.members || []).filter(id => !(v.telegram_ok || []).includes(id));
  const target = (t) => ({ oncall: "quem está de plantão", next: "o próximo da escala", all: "toda a escala" }[t] || `${name(t.slice(5))}`);

  return (
    <div className="space-y-4" data-testid="oncall-panel">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="bg-surface border-line p-4 lg:col-span-1" data-testid="oncall-now">
          <div className="flex items-center gap-2 text-xs text-slate-400"><PhoneCall className="w-4 h-4 text-emerald-400" /> De plantão agora</div>
          <div className="text-2xl font-semibold text-slate-100 mt-1" data-testid="oncall-now-name">{v.now?.name || (v.members.length ? "—" : "sem escala")}</div>
          {v.now?.until && <div className="text-xs text-slate-400 mt-0.5">até {fmt(v.now.until)}</div>}
          {v.now?.override && <div className="text-xs text-amber-300 mt-1 flex items-center gap-1"><Repeat2 className="w-3.5 h-3.5" /> troca pontual{v.now.override.note ? `: ${v.now.override.note}` : ""}</div>}
          {v.now?.next && <div className="text-xs text-slate-500 mt-2">se não responder: {name(v.now.next)}</div>}
          <div className={`text-[11px] mt-3 ${v.enabled ? "text-emerald-400" : "text-slate-500"}`}>{v.enabled ? "Escalonamento ligado" : "Escalonamento desligado"} · /plantao no Telegram</div>
        </Card>
        <Card className="bg-surface border-line p-4 lg:col-span-2" data-testid="oncall-shifts">
          <div className="flex items-center gap-2 text-xs text-slate-400 mb-2"><CalendarClock className="w-4 h-4" /> Próximos turnos</div>
          {v.members.length === 0 ? <div className="text-sm text-slate-500">Monte a escala abaixo.</div> : (
            <div className="divide-y divide-line">
              {v.shifts.map((s, i) => (
                <div key={s.start} className={`flex items-center gap-3 py-1.5 text-sm ${i === 0 ? "text-slate-100" : "text-slate-300"}`}>
                  <span className="w-44 shrink-0 text-xs font-mono text-slate-400">{fmt(s.start)}</span>
                  <span className="flex-1">{name(s.user_id)}</span>
                  {s.overrides.length > 0 && <span className="text-[11px] text-amber-300 truncate">troca: {s.overrides.map(o => `${name(o.user_id)} ${fmt(o.start).slice(5)}→${fmt(o.end).slice(5)}`).join("; ")}</span>}
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {isAdmin && (
        <Card className="bg-surface border-line p-4 space-y-5" data-testid="oncall-edit">
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-100"><Switch checked={f.enabled} onCheckedChange={x => set({ enabled: x })} data-testid="oncall-enabled" /> Escalonamento ligado</label>
            <label className="text-xs text-slate-400 ml-auto">Vale para incidentes
              <select value={f.min_severity} onChange={e => set({ min_severity: e.target.value })} className={`${sel} ml-2`} data-testid="oncall-minsev">
                {SEVS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
          </div>

          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Escala (rodízio, na ordem)</div>
            <div className="space-y-1">
              {f.members.map((id, i) => (
                <div key={id} className="flex items-center gap-2 border border-line rounded-md px-2 py-1.5" data-testid={`oncall-member-${id}`}>
                  <span className="text-xs text-slate-500 w-5">{i + 1}.</span>
                  <span className="flex-1 text-sm text-slate-200">{name(id)}</span>
                  {!(v.telegram_ok || []).includes(id) && <span className="text-[11px] text-amber-300">sem Telegram</span>}
                  <button disabled={i === 0} onClick={() => move(i, -1)} className="text-slate-500 hover:text-slate-200 disabled:opacity-30"><ArrowUp className="w-4 h-4" /></button>
                  <button disabled={i === f.members.length - 1} onClick={() => move(i, 1)} className="text-slate-500 hover:text-slate-200 disabled:opacity-30"><ArrowDown className="w-4 h-4" /></button>
                  <button onClick={() => set({ members: f.members.filter(x => x !== id) })} className="text-slate-500 hover:text-red-400"><Trash2 className="w-4 h-4" /></button>
                </div>
              ))}
              <select value="" onChange={e => e.target.value && set({ members: [...f.members, e.target.value] })} className={sel} data-testid="oncall-add-member">
                <option value="">+ adicionar pessoa</option>
                {users.filter(u => !f.members.includes(u.id)).map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </div>
            <div className="flex flex-wrap gap-3 items-end mt-3 text-xs text-slate-400">
              <label>Cada um fica
                <select value={f.period} onChange={e => set({ period: e.target.value })} className={`${sel} block mt-1`} data-testid="oncall-period">
                  <option value="week">uma semana</option><option value="day">um dia</option>
                </select>
              </label>
              {f.period === "week" && (
                <label>Troca na
                  <select value={f.handoff_weekday} onChange={e => set({ handoff_weekday: Number(e.target.value) })} className={`${sel} block mt-1`}>
                    {WEEKDAYS.map((w, i) => <option key={w} value={i}>{w}</option>)}
                  </select>
                </label>
              )}
              <label>às
                <select value={f.handoff_hour} onChange={e => set({ handoff_hour: Number(e.target.value) })} className={`${sel} block mt-1`}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </select>
              </label>
              <label>1º da lista começou em
                <Input type="date" value={f.anchor} onChange={e => set({ anchor: e.target.value })} className="bg-sunken border-line h-9 mt-1" data-testid="oncall-anchor" />
              </label>
            </div>
            {noTg.length > 0 && <div className="text-[11px] text-amber-300 mt-2 flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" />
              {noTg.map(name).join(", ")} sem Telegram vinculado: recebe só o push no celular/PC. Vincule em Configurações → Automação e alertas → Assistente IA.</div>}
          </section>

          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Escalonamento (incidente aberto e ainda não reconhecido)</div>
            <div className="space-y-1">
              {f.levels.map((lv, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2 text-sm text-slate-300" data-testid={`oncall-level-${i}`}>
                  <span className="text-xs text-slate-500 w-14">Nível {i + 1}</span>
                  <Input type="number" min={0} value={lv.after_min} onChange={e => set({ levels: f.levels.map((x, j) => j === i ? { ...x, after_min: Number(e.target.value) } : x) })}
                         className="w-20 bg-sunken border-line h-8" />
                  <span className="text-xs text-slate-400">min depois de aberto, avisar</span>
                  <select value={lv.target} onChange={e => set({ levels: f.levels.map((x, j) => j === i ? { ...x, target: e.target.value } : x) })} className={`${sel} h-8`}>
                    <option value="oncall">quem está de plantão</option>
                    <option value="next">o próximo da escala</option>
                    <option value="all">toda a escala</option>
                    {users.map(u => <option key={u.id} value={`user:${u.id}`}>{u.name}</option>)}
                  </select>
                  <button onClick={() => set({ levels: f.levels.filter((_, j) => j !== i) })} className="text-slate-500 hover:text-red-400"><Trash2 className="w-4 h-4" /></button>
                </div>
              ))}
              {f.levels.length < 6 && <Button size="sm" variant="ghost" onClick={() => set({ levels: [...f.levels, { after_min: ((f.levels[f.levels.length - 1] || {}).after_min || 0) + 15, target: "all" }] })}
                className="text-brand-soft h-8"><Plus className="w-4 h-4 mr-1" /> nível</Button>}
            </div>
            <p className="text-[11px] text-slate-500 mt-1">{f.levels.map((l, i) => `${l.after_min ? `aos ${l.after_min} min` : "na hora"}: ${target(l.target)}`).join(" → ") || "nenhum nível"}. Reconhecer o incidente para a escalada; quem é acionado passa a ver o incidente.</p>
          </section>

          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Trocas pontuais (cobre o plantão de alguém)</div>
            <div className="space-y-1">
              {f.overrides.map((o, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2" data-testid={`oncall-ov-${i}`}>
                  <select value={o.user_id} onChange={e => set({ overrides: f.overrides.map((x, j) => j === i ? { ...x, user_id: e.target.value } : x) })} className={`${sel} h-8`}>
                    <option value="">— quem cobre —</option>
                    {users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                  <Input type="datetime-local" value={o.start} onChange={e => set({ overrides: f.overrides.map((x, j) => j === i ? { ...x, start: e.target.value } : x) })} className="w-52 bg-sunken border-line h-8" />
                  <span className="text-xs text-slate-500">até</span>
                  <Input type="datetime-local" value={o.end} onChange={e => set({ overrides: f.overrides.map((x, j) => j === i ? { ...x, end: e.target.value } : x) })} className="w-52 bg-sunken border-line h-8" />
                  <Input value={o.note} onChange={e => set({ overrides: f.overrides.map((x, j) => j === i ? { ...x, note: e.target.value } : x) })} placeholder="motivo" className="w-40 bg-sunken border-line h-8" />
                  <button onClick={() => set({ overrides: f.overrides.filter((_, j) => j !== i) })} className="text-slate-500 hover:text-red-400"><Trash2 className="w-4 h-4" /></button>
                </div>
              ))}
              <Button size="sm" variant="ghost" onClick={() => set({ overrides: [...f.overrides, { user_id: "", start: "", end: "", note: "" }] })} className="text-brand-soft h-8" data-testid="oncall-add-ov">
                <Plus className="w-4 h-4 mr-1" /> troca</Button>
            </div>
          </section>

          <div className="flex gap-2">
            <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="oncall-save">Salvar plantão</Button>
            <Button variant="ghost" onClick={() => setF(v)} className="text-slate-300">Desfazer</Button>
          </div>
        </Card>
      )}
    </div>
  );
}
