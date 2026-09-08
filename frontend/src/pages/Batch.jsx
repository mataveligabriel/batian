import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { Play, Loader2, Plus, Trash2 } from "lucide-react";

export default function Batch() {
  const [devices, setDevices] = useState([]);
  const [scripts, setScripts] = useState([]);
  const [selected, setSelected] = useState([]);
  const [scriptId, setScriptId] = useState("");
  const [inline, setInline] = useState("");
  const [timeout, setTimeoutVal] = useState(60);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState([]);
  const [newScript, setNewScript] = useState({ name: "", content: "", description: "" });
  const [showNew, setShowNew] = useState(false);

  const load = async () => {
    const [d, s] = await Promise.all([api.get("/devices"), api.get("/scripts")]);
    setDevices(d.data); setScripts(s.data);
  };
  useEffect(() => { load(); }, []);

  const toggle = (id) => setSelected(sel => sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id]);
  const toggleAll = () => setSelected(selected.length === devices.length ? [] : devices.map(d => d.id));

  const execute = async () => {
    if (selected.length === 0) return toast.error("Selecione ao menos 1 equipamento");
    if (!scriptId && !inline.trim()) return toast.error("Selecione um script ou digite um comando");
    setRunning(true); setResults([]);
    try {
      const { data } = await api.post("/batch/execute", {
        device_ids: selected,
        script_id: scriptId || null,
        inline_command: inline.trim() || null,
        timeout: Number(timeout) || 60,
      });
      setResults(data.results);
      toast.success(`Execução concluída em ${data.results.length} equipamento(s)`);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setRunning(false); }
  };

  const saveScript = async () => {
    if (!newScript.name || !newScript.content) return toast.error("Nome e conteúdo obrigatórios");
    await api.post("/scripts", newScript);
    toast.success("Script salvo"); setNewScript({ name: "", content: "", description: "" }); setShowNew(false); load();
  };
  const delScript = async (id) => {
    if (!window.confirm("Excluir script?")) return;
    await api.delete(`/scripts/${id}`); load(); toast.success("Script excluído");
  };

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="batch-page">
      <div className="mb-6">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Automação</div>
        <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Execução em Lote</h1>
        <p className="text-slate-400 mt-2 text-sm">Rode scripts em múltiplos equipamentos simultaneamente e veja o resultado por host.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Devices selector */}
        <Card className="bg-[#111722] border-[#1E293B] p-5 lg:col-span-1" data-testid="devices-selector">
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Equipamentos ({selected.length}/{devices.length})</div>
            <button onClick={toggleAll} data-testid="toggle-all-devices" className="text-xs text-[#4DA3FF] hover:underline font-mono">
              {selected.length === devices.length ? "limpar" : "todos"}
            </button>
          </div>
          <div className="space-y-1 max-h-[520px] overflow-y-auto">
            {devices.map(d => (
              <label key={d.id} data-testid={`batch-device-${d.id}`}
                     className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-slate-800/60 cursor-pointer">
                <Checkbox checked={selected.includes(d.id)} onCheckedChange={() => toggle(d.id)} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm text-slate-100 truncate">{d.name}</div>
                  <div className="text-xs font-mono text-slate-500 truncate">{d.host}:{d.port}</div>
                </div>
                <span className={`w-2 h-2 rounded-full ${d.status === "online" ? "bg-emerald-400" : "bg-slate-600"}`} />
              </label>
            ))}
          </div>
        </Card>

        {/* Script picker + inline + run */}
        <Card className="bg-[#111722] border-[#1E293B] p-5 lg:col-span-2" data-testid="batch-runner">
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Comando</div>
            <Button size="sm" variant="ghost" onClick={() => setShowNew(v => !v)} data-testid="toggle-new-script" className="text-[#4DA3FF]">
              <Plus className="w-4 h-4 mr-1" /> Novo script
            </Button>
          </div>

          {showNew && (
            <div className="mb-4 p-3 border border-[#1E293B] rounded-md bg-[#0B111C] space-y-2">
              <Input placeholder="Nome" data-testid="new-script-name" value={newScript.name} onChange={e => setNewScript({ ...newScript, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              <Input placeholder="Descrição" data-testid="new-script-desc" value={newScript.description} onChange={e => setNewScript({ ...newScript, description: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              <Textarea placeholder="Conteúdo do script..." data-testid="new-script-content" rows={4} value={newScript.content} onChange={e => setNewScript({ ...newScript, content: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setShowNew(false)}>Cancelar</Button>
                <Button size="sm" onClick={saveScript} data-testid="save-new-script" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar</Button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 mb-3">
            <div className="col-span-2">
              <Label className="text-xs uppercase tracking-widest text-slate-400 font-mono">Script salvo</Label>
              <Select value={scriptId || "none"} onValueChange={v => setScriptId(v === "none" ? "" : v)}>
                <SelectTrigger data-testid="script-select" className="bg-[#05070A] border-[#1E293B] font-mono">
                  <SelectValue placeholder="Selecione um script" />
                </SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                  <SelectItem value="none">— nenhum —</SelectItem>
                  {scripts.map(s => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs uppercase tracking-widest text-slate-400 font-mono">Timeout (s)</Label>
              <Input type="number" data-testid="batch-timeout" value={timeout} onChange={e => setTimeoutVal(e.target.value)} className="bg-[#05070A] border-[#1E293B] font-mono" />
            </div>
          </div>

          <div className="mb-3">
            <Label className="text-xs uppercase tracking-widest text-slate-400 font-mono">Ou comando inline</Label>
            <Textarea data-testid="batch-inline-cmd" rows={3} value={inline} onChange={e => setInline(e.target.value)} placeholder="uname -a" className="bg-[#05070A] border-[#1E293B] font-mono text-emerald-300" />
          </div>

          <Button onClick={execute} disabled={running} data-testid="execute-batch-btn" className="bg-[#007AFF] hover:bg-[#0062CC] w-full">
            {running ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Play className="w-4 h-4 mr-2" />}
            Executar em {selected.length} equipamento(s)
          </Button>

          {scripts.length > 0 && (
            <div className="mt-4">
              <div className="text-xs uppercase tracking-widest text-slate-500 font-mono mb-2">Scripts salvos</div>
              <div className="flex flex-wrap gap-2">
                {scripts.map(s => (
                  <div key={s.id} className="flex items-center gap-2 bg-[#0B111C] border border-[#1E293B] rounded-md px-2 py-1">
                    <span className="text-xs text-slate-300">{s.name}</span>
                    <button onClick={() => delScript(s.id)} data-testid={`del-script-${s.id}`} className="text-slate-500 hover:text-red-400">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>

      {results.length > 0 && (
        <div className="mt-6 space-y-3" data-testid="batch-results">
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Resultados</div>
          {results.map(r => (
            <Card key={r.device_id} className="bg-[#111722] border-[#1E293B] p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <Badge className={r.ok ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" : "bg-red-500/15 text-red-400 border-red-500/30"}>
                    {r.ok ? "sucesso" : "falha"}
                  </Badge>
                  <span className="font-medium text-slate-100">{r.device_name}</span>
                  <span className="text-xs font-mono text-slate-500">{r.host}</span>
                </div>
                <span className="text-xs font-mono text-slate-500">exit {r.exit_status}</span>
              </div>
              {r.error && <div className="text-xs font-mono text-red-400 mb-1">{r.error}</div>}
              {r.stdout && <pre className="text-xs font-mono text-slate-300 bg-[#05070A] p-3 rounded border border-[#1E293B] overflow-x-auto whitespace-pre-wrap">{r.stdout}</pre>}
              {r.stderr && <pre className="text-xs font-mono text-red-300 bg-[#050505] p-3 rounded border border-red-900/40 overflow-x-auto whitespace-pre-wrap mt-1">{r.stderr}</pre>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
