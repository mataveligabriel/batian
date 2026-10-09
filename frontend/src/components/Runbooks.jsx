import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Play, Plus, Pencil, Trash2, Loader2, ArrowUp, ArrowDown, CheckCircle2, XCircle, SkipForward, Send, X, History } from "lucide-react";

const EMPTY = () => ({ name: "", description: "", telegram: true, shared: false, save: false, steps: [{ device_id: "", commands: "" }] });

function Check({ id, checked, onChange, children }) {
  return (
    <div className="flex items-center gap-2">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(!!v)} data-testid={id} />
      <label htmlFor={id} className="text-sm text-slate-300 cursor-pointer select-none">{children}</label>
    </div>
  );
}

function Editor({ value, devices, onCancel, onSaved }) {
  const [f, setF] = useState(value);
  const [saving, setSaving] = useState(false);
  const sorted = useMemo(() => [...devices].sort((a, b) => a.name.localeCompare(b.name)), [devices]);
  const setStep = (i, patch) => setF(cur => ({ ...cur, steps: cur.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) }));
  const move = (i, d) => setF(cur => {
    const st = [...cur.steps]; const j = i + d;
    if (j < 0 || j >= st.length) return cur;
    [st[i], st[j]] = [st[j], st[i]]; return { ...cur, steps: st };
  });
  const save = async () => {
    setSaving(true);
    try {
      const body = { name: f.name, description: f.description, telegram: f.telegram, shared: f.shared, save: f.save,
        steps: f.steps.map(s => ({ device_id: s.device_id, commands: s.commands })) };
      const r = f.id ? await api.put(`/runbooks/${f.id}`, body) : await api.post("/runbooks", body);
      toast.success(f.id ? "Roteiro salvo" : "Roteiro criado"); onSaved(r.data);
    } catch (e) { toast.error(formatApiError(e)); } finally { setSaving(false); }
  };
  return (
    <Card className="bg-surface border-line p-5 space-y-4" data-testid="rb-editor">
      <div className="flex items-center justify-between">
        <div className="text-base font-semibold text-slate-100">{f.id ? "Editar roteiro" : "Novo roteiro"}</div>
        <button onClick={onCancel} className="text-slate-500 hover:text-slate-200"><X className="w-4 h-4" /></button>
      </div>
      <div className="grid sm:grid-cols-2 gap-3">
        <div><Label className="text-xs text-slate-400">Nome (é o que você manda no Telegram)</Label>
          <Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="ATIVAR ROTA LIMOEIRO" className="bg-sunken border-line mt-1" data-testid="rb-name" /></div>
        <div><Label className="text-xs text-slate-400">Descrição</Label>
          <Input value={f.description} onChange={e => setF({ ...f, description: e.target.value })} placeholder="opcional" className="bg-sunken border-line mt-1" /></div>
      </div>
      <div className="space-y-3">
        {f.steps.map((s, i) => (
          <div key={i} className="rounded-md border border-line bg-sunken/40 p-3 space-y-2" data-testid={`rb-step-${i}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-mono text-slate-400 w-14">passo {i + 1}</span>
              <select value={s.device_id} onChange={e => setStep(i, { device_id: e.target.value })} data-testid={`rb-step-${i}-dev`}
                className="h-9 flex-1 min-w-[200px] rounded-md bg-sunken border border-line px-2 text-sm text-slate-100">
                <option value="">Equipamento…</option>
                {sorted.map(d => <option key={d.id} value={d.id}>{d.name} · {d.host}</option>)}
              </select>
              <button onClick={() => move(i, -1)} disabled={i === 0} className="p-1 text-slate-500 hover:text-slate-200 disabled:opacity-30" title="Subir"><ArrowUp className="w-4 h-4" /></button>
              <button onClick={() => move(i, 1)} disabled={i === f.steps.length - 1} className="p-1 text-slate-500 hover:text-slate-200 disabled:opacity-30" title="Descer"><ArrowDown className="w-4 h-4" /></button>
              {f.steps.length > 1 && <button onClick={() => setF({ ...f, steps: f.steps.filter((_, j) => j !== i) })} className="p-1 text-slate-500 hover:text-red-300" title="Remover passo" data-testid={`rb-step-${i}-del`}><Trash2 className="w-4 h-4" /></button>}
            </div>
            <Textarea rows={5} value={s.commands} onChange={e => setStep(i, { commands: e.target.value })} data-testid={`rb-step-${i}-cmd`}
              placeholder={"sys\n#\ninterface XGigabitEthernet0/0/8.1110\nundo shutdown\n#"} className="bg-sunken border-line font-mono text-emerald-300 text-[13px]" />
          </div>
        ))}
        <Button size="sm" variant="outline" onClick={() => setF({ ...f, steps: [...f.steps, { device_id: "", commands: "" }] })} className="border-line bg-transparent" data-testid="rb-step-add">
          <Plus className="w-4 h-4 mr-1" />Adicionar passo</Button>
        <div className="text-[11px] text-slate-500">Os passos rodam na ordem e <b>param no primeiro erro</b> — se o passo 1 falhar, o passo 2 não é executado. Linhas só com “#” são ignoradas.</div>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <Check id="rb-telegram" checked={f.telegram} onChange={v => setF({ ...f, telegram: v })}>Pode ser executado pelo bot do Telegram</Check>
        <Check id="rb-save" checked={f.save} onChange={v => setF({ ...f, save: v })}>Gravar a configuração no fim de cada equipamento (save/write)</Check>
        <Check id="rb-shared" checked={f.shared} onChange={v => setF({ ...f, shared: v })}>Outros usuários podem executar</Check>
      </div>
      <div className="flex gap-2 justify-end">
        <Button variant="outline" onClick={onCancel} className="border-line bg-transparent">Cancelar</Button>
        <Button onClick={save} disabled={saving} className="bg-brand hover:bg-brand-strong" data-testid="rb-save-btn">
          {saving && <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />}Salvar roteiro</Button>
      </div>
    </Card>
  );
}

function Result({ res }) {
  return (
    <div className={`rounded-md border p-3 space-y-2 ${res.ok ? "border-emerald-700/50 bg-emerald-950/20" : "border-red-800/60 bg-red-950/20"}`} data-testid="rb-result">
      <div className={`text-sm font-semibold ${res.ok ? "text-emerald-300" : "text-red-300"}`}>
        {res.ok ? "Executado com sucesso" : "Parou com erro"} · {new Date(res.ended_at || res.started_at).toLocaleString("pt-BR")}{res.via ? ` · ${res.via === "telegram" ? "Telegram" : "web"}` : ""}{res.user_email ? ` · ${res.user_email}` : ""}
      </div>
      {res.steps.map(s => (
        <details key={s.index} className="text-xs" open={!s.ok && !s.skipped}>
          <summary className="cursor-pointer flex items-center gap-1.5 text-slate-200">
            {s.skipped ? <SkipForward className="w-3.5 h-3.5 text-slate-500" /> : s.ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <XCircle className="w-3.5 h-3.5 text-red-400" />}
            {s.index}. {s.device} <span className="text-slate-500 font-mono">{s.host}</span>
            {s.skipped && <span className="text-slate-500">— não executado (passo anterior falhou)</span>}
            {s.failed_command && <span className="text-red-300">— recusou <span className="font-mono">{s.failed_command}</span></span>}
            {s.error && <span className="text-red-300">— {s.error}</span>}
          </summary>
          {s.output && <pre className="mt-1 font-mono text-[11px] text-slate-300 bg-sunken p-2 rounded border border-line overflow-x-auto whitespace-pre-wrap max-h-64">{s.output}</pre>}
        </details>
      ))}
    </div>
  );
}

export default function Runbooks() {
  const { user } = useAuth();
  const [list, setList] = useState([]);
  const [devices, setDevices] = useState([]);
  const [editing, setEditing] = useState(null);
  const [confirm, setConfirm] = useState("");
  const [running, setRunning] = useState("");
  const [results, setResults] = useState({});
  const [history, setHistory] = useState({});

  const load = () => api.get("/runbooks").then(r => setList(r.data)).catch(e => toast.error(formatApiError(e)));
  useEffect(() => { load(); api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {}); }, []);

  const run = async (rb) => {
    setConfirm(""); setRunning(rb.id);
    try {
      const { data } = await api.post(`/runbooks/${rb.id}/run`);
      setResults(r => ({ ...r, [rb.id]: data }));
      data.ok ? toast.success(`${rb.name}: executado`) : toast.error(`${rb.name}: parou com erro`);
      load();
    } catch (e) { toast.error(formatApiError(e)); } finally { setRunning(""); }
  };
  const del = async (rb) => {
    if (!window.confirm(`Apagar o roteiro ${rb.name}?`)) return;
    try { await api.delete(`/runbooks/${rb.id}`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const showHistory = async (rb) => {
    if (history[rb.id]) return setHistory(h => ({ ...h, [rb.id]: null }));
    try { const { data } = await api.get(`/runbooks/${rb.id}/runs`); setHistory(h => ({ ...h, [rb.id]: data })); } catch (e) { toast.error(formatApiError(e)); }
  };
  const canEdit = (rb) => rb.mine || user?.role === "admin";

  return (
    <div className="space-y-4" data-testid="runbooks">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-slate-400 max-w-2xl">Roteiros rodam uma sequência de comandos em vários equipamentos, um depois do outro, com um clique —
          ou pelo bot do Telegram: mande <span className="font-mono text-slate-200">/roteiros</span> ou o nome do roteiro e confirme no botão.</div>
        {!editing && <Button onClick={() => setEditing(EMPTY())} className="bg-brand hover:bg-brand-strong" data-testid="rb-new"><Plus className="w-4 h-4 mr-1.5" />Novo roteiro</Button>}
      </div>

      {editing && <Editor value={editing} devices={devices} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}

      {list.length === 0 && !editing && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">Nenhum roteiro ainda.</Card>}
      {list.map(rb => (
        <Card key={rb.id} className="bg-surface border-line p-4 space-y-3" data-testid={`rb-${rb.id}`}>
          <div className="flex flex-wrap items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-semibold text-slate-100">{rb.name}</span>
                {rb.telegram && <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-sky-700/50 text-sky-300 flex items-center gap-1"><Send className="w-3 h-3" />Telegram</span>}
                {rb.save && <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-line text-slate-400">grava no fim</span>}
                {!rb.mine && <span className="text-[10px] font-mono text-slate-500">de {rb.owner_email}</span>}
              </div>
              {rb.description && <div className="text-xs text-slate-400 mt-0.5">{rb.description}</div>}
              <ol className="mt-2 space-y-1">
                {rb.steps.map((s, i) => (
                  <li key={i} className="text-xs font-mono text-slate-400 flex gap-2">
                    <span className="text-slate-500">{i + 1}.</span><span className="text-slate-200">{s.device_name}</span>
                    <span className="truncate">{s.commands.split("\n").map(l => l.trim()).filter(l => l && l !== "#").join(" ⏵ ")}</span>
                  </li>
                ))}
              </ol>
              {rb.last_run && <div className={`text-[11px] mt-2 ${rb.last_run.ok ? "text-emerald-400/80" : "text-red-300/80"}`}>
                última execução: {rb.last_run.ok ? "ok" : "com erro"} · {new Date(rb.last_run.at).toLocaleString("pt-BR")} · {rb.last_run.by} ({rb.last_run.via})</div>}
            </div>
            <div className="flex items-center gap-1.5">
              {confirm === rb.id ? (
                <>
                  <Button size="sm" onClick={() => run(rb)} className="bg-red-700 hover:bg-red-600" data-testid={`rb-confirm-${rb.id}`}><Play className="w-3.5 h-3.5 mr-1.5" />Confirmar execução</Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirm("")} className="text-slate-300">Cancelar</Button>
                </>
              ) : (
                <Button size="sm" onClick={() => setConfirm(rb.id)} disabled={!!running} className="bg-emerald-700 hover:bg-emerald-600" data-testid={`rb-run-${rb.id}`}>
                  {running === rb.id ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1.5" />}{running === rb.id ? "Executando…" : "Executar"}
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => showHistory(rb)} title="Últimas execuções" className="text-slate-300"><History className="w-4 h-4" /></Button>
              {canEdit(rb) && <>
                <Button size="sm" variant="ghost" onClick={() => setEditing({ ...rb, steps: rb.steps.map(s => ({ device_id: s.device_id, commands: s.commands })) })} className="text-slate-300" data-testid={`rb-edit-${rb.id}`}><Pencil className="w-4 h-4" /></Button>
                <Button size="sm" variant="ghost" onClick={() => del(rb)} className="text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></Button>
              </>}
            </div>
          </div>
          {results[rb.id] && <Result res={results[rb.id]} />}
          {history[rb.id] && (
            <div className="space-y-2">
              {history[rb.id].length === 0 && <div className="text-xs text-slate-500">Ainda não foi executado.</div>}
              {history[rb.id].map(h => <Result key={h.id} res={h} />)}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
