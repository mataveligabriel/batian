import React, { useEffect, useMemo, useRef, useState } from "react";
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
import { Play, Loader2, Plus, Trash2, Pencil, Star, Search, ChevronDown, ChevronRight, Copy, Download, Square } from "lucide-react";
import { useLocation } from "react-router-dom";

export default function Batch() {
  const [devices, setDevices] = useState([]);
  const [scripts, setScripts] = useState([]);
  const [selected, setSelected] = useState([]);
  const [scriptId, setScriptId] = useState("");
  const [inline, setInline] = useState("");
  const [timeout, setTimeoutVal] = useState(60);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState([]);
  const [newScript, setNewScript] = useState({ name: "", content: "", description: "", quick: false });
  const [editingScript, setEditingScript] = useState(null);
  const [showNew, setShowNew] = useState(false);

  // modo "Lista de IPs": roda sem cadastrar — usuário/senha informados na hora
  const [mode, setMode] = useState("saved");
  const [adhoc, setAdhoc] = useState({ targets: "", username: "", password: "", protocol: "auto", port: "", agent_id: "", device_type: "auto" });
  const [agents, setAgents] = useState([]);
  const [job, setJob] = useState(null);
  const pollRef = useRef(null);
  const ipCount = useMemo(() => adhoc.targets.split("\n").filter(l => l.split("#")[0].trim()).length, [adhoc.targets]);
  useEffect(() => { api.get("/agents").then(r => setAgents(r.data)).catch(() => {}); return () => clearTimeout(pollRef.current); }, []);
  const ST = { auth: "login recusado", closed: "porta fechada", down: "sem resposta", error: "erro", skipped: "cancelado" };
  const fromJob = (j) => (j.results || []).map((r, i) => r && ({ device_id: `ip-${i}`, device_name: r.name || r.hostname || r.host, host: `${r.host}${r.protocol ? ` · ${r.protocol}/${r.port}` : ""}${r.label ? ` · ${r.label}` : ""}`,
    ok: r.status === "ok", exit_status: r.status === "ok" ? 0 : -1, stdout: r.output || "", stderr: "", error: r.status === "ok" ? "" : `${ST[r.status] || r.status}${r.error && r.error !== ST[r.status] ? ` — ${r.error}` : ""}`, ip: r.host })).filter(Boolean);
  const poll = (id) => {
    clearTimeout(pollRef.current);
    pollRef.current = setTimeout(async () => {
      try {
        const { data } = await api.get(`/batch/adhoc/${id}`); setJob(data); setResults(fromJob(data));
        if (!data.finished) poll(id);
        else { setRunning(false); const ok = data.results.filter(r => r?.status === "ok").length; toast.success(`Concluído: ${ok} de ${data.total} executaram`); }
      } catch (e) { toast.error(formatApiError(e)); setRunning(false); }
    }, 1500);
  };
  const executeAdhoc = async () => {
    if (!ipCount) return toast.error("Cole ao menos 1 IP");
    if (!scriptId && !inline.trim()) return toast.error("Selecione um script ou digite um comando");
    setRunning(true); setResults([]); setJob(null);
    try {
      const { data } = await api.post("/batch/adhoc", { ...adhoc, port: adhoc.port ? Number(adhoc.port) : null, agent_id: adhoc.agent_id || null,
        command: inline.trim(), script_id: inline.trim() ? null : scriptId || null, timeout: Number(timeout) || 60 });
      setJob(data); poll(data.id);
    } catch (e) { toast.error(formatApiError(e)); setRunning(false); }
  };
  const stopAdhoc = async () => { try { await api.post(`/batch/adhoc/${job.id}/cancel`); toast("Parando — os que já começaram terminam"); } catch (e) { toast.error(formatApiError(e)); } };
  const allText = () => results.map(r => `===== ${r.device_name} (${r.host}) — ${r.ok ? "ok" : "FALHA: " + r.error} =====\n${r.stdout || ""}`).join("\n\n");
  const copyAll = () => navigator.clipboard?.writeText(allText()).then(() => toast.success("Copiado"), () => toast.error("Não consegui copiar — use Baixar"));
  const downloadAll = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([allText()], { type: "text/plain" })); a.download = "lote.txt"; a.click(); URL.revokeObjectURL(a.href); };

  const location = useLocation();
  const load = async () => {
    const [d, s] = await Promise.all([api.get("/devices"), api.get("/scripts")]);
    setDevices(d.data); setScripts(s.data);
    // pré-seleção vinda de Equipamentos → "Executar em lote"
    const pre = location.state?.deviceIds;
    if (Array.isArray(pre) && pre.length) {
      const ids = new Set(d.data.map(x => x.id));
      setSelected(pre.filter(id => ids.has(id)));
      window.history.replaceState({}, "");
    }
  };
  useEffect(() => { load(); }, []);

  const toggle = (id) => setSelected(sel => sel.includes(id) ? sel.filter(x => x !== id) : [...sel, id]);

  // organização por tag (pastas): filtro, busca e grupos que abrem/fecham e marcam todos de uma vez
  const [tag, setTag] = useState("");                    // "" = todas | tag | "__none__" = sem tag
  const [q, setQ] = useState("");
  const [closed, setClosed] = useState(() => new Set());
  const { allTags, countByTag } = useMemo(() => {
    const c = {};
    for (const d of devices) {
      if (!(d.tags || []).length) c.__none__ = (c.__none__ || 0) + 1;
      for (const t of d.tags || []) c[t] = (c[t] || 0) + 1;
    }
    return { allTags: Object.keys(c).filter(t => t !== "__none__").sort((a, b) => a.localeCompare(b, "pt-BR", { sensitivity: "base" })), countByTag: c };
  }, [devices]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return devices.filter(d => {
      const tg = d.tags || [];
      if (tag === "__none__" ? tg.length : tag && !tg.includes(tag)) return false;
      return !t || d.name.toLowerCase().includes(t) || (d.host || "").includes(t) || tg.some(x => x.toLowerCase().includes(t));
    }).sort((a, b) => a.name.localeCompare(b.name, "pt-BR", { numeric: true, sensitivity: "base" }));
  }, [devices, tag, q]);
  const groups = useMemo(() => {
    if (tag) return [[tag, shown]];
    const m = new Map();
    for (const d of shown) {                             // em "Todas", cada equipamento fica na pasta da primeira tag
      const g = (d.tags || [])[0] || "__none__";
      if (!m.has(g)) m.set(g, []);
      m.get(g).push(d);
    }
    return [...m.entries()].sort((a, b) => (a[0] === "__none__") - (b[0] === "__none__") || a[0].localeCompare(b[0], "pt-BR", { sensitivity: "base" }));
  }, [shown, tag]);
  const setMany = (ids, on) => setSelected(sel => on ? [...new Set([...sel, ...ids])] : sel.filter(x => !ids.includes(x)));
  const shownIds = shown.map(d => d.id);
  const allShownOn = shownIds.length > 0 && shownIds.every(id => selected.includes(id));
  const toggleAll = () => setMany(shownIds, !allShownOn);       // marca/desmarca só o que está à vista

  const execute = async () => {
    if (mode === "ips") return executeAdhoc();
    setJob(null);
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
    if (editingScript) await api.put(`/scripts/${editingScript.id}`, newScript);
    else await api.post("/scripts", newScript);
    toast.success(editingScript ? "Script atualizado" : "Script salvo");
    setNewScript({ name: "", content: "", description: "", quick: false }); setEditingScript(null); setShowNew(false); load();
  };
  const editScript = (s) => {
    setEditingScript(s);
    setNewScript({ name: s.name, content: s.content, description: s.description || "", quick: !!s.quick });
    setShowNew(true);
  };
  const cancelEdit = () => { setShowNew(false); setEditingScript(null); setNewScript({ name: "", content: "", description: "", quick: false }); };
  const delScript = async (id) => {
    if (!window.confirm("Excluir script?")) return;
    await api.delete(`/scripts/${id}`); load(); toast.success("Script excluído");
  };

  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="batch-page">
      <div className="mb-6">
        <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Execução em Lote</h1>
        <p className="text-slate-400 mt-2 text-sm">Rode scripts em múltiplos equipamentos simultaneamente e veja o resultado por host.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Devices selector */}
        <Card className="bg-surface border-line p-5 lg:col-span-1" data-testid="devices-selector">
          <div className="flex rounded-md border border-line overflow-hidden mb-3 text-sm">
            {[["saved", "Cadastrados"], ["ips", "Lista de IPs"]].map(([v, l]) => (
              <button key={v} onClick={() => setMode(v)} disabled={running} data-testid={`batch-mode-${v}`}
                      className={`flex-1 h-8 ${mode === v ? "bg-brand text-white" : "bg-sunken text-slate-400 hover:text-slate-200"}`}>{l}</button>
            ))}
          </div>
          {mode === "ips" && (
            <div className="space-y-2.5" data-testid="batch-adhoc">
              <div>
                <Label className="text-xs text-slate-400">IPs, um por linha ({ipCount})</Label>
                <Textarea value={adhoc.targets} onChange={e => setAdhoc({ ...adhoc, targets: e.target.value })} rows={10} spellCheck={false} data-testid="adhoc-targets"
                          placeholder={"172.16.40.118\n172.20.10.3  PAE-ANITA\n172.20.50.51:2222\n172.20.66.0/28"} className="bg-sunken border-line font-mono text-xs" />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div><Label className="text-xs text-slate-400">Usuário</Label><Input value={adhoc.username} onChange={e => setAdhoc({ ...adhoc, username: e.target.value })} autoComplete="off" placeholder="vazio = padrão" className="bg-sunken border-line font-mono" data-testid="adhoc-user" /></div>
                <div><Label className="text-xs text-slate-400">Senha</Label><Input type="password" value={adhoc.password} onChange={e => setAdhoc({ ...adhoc, password: e.target.value })} autoComplete="new-password" className="bg-sunken border-line font-mono" data-testid="adhoc-pass" /></div>
              </div>
              <div className="grid grid-cols-[1fr_80px] gap-2">
                <div><Label className="text-xs text-slate-400">Protocolo</Label>
                  <select value={adhoc.protocol} onChange={e => setAdhoc({ ...adhoc, protocol: e.target.value })} className="w-full h-9 rounded-md bg-sunken border border-line text-sm text-slate-200 px-2">
                    <option value="auto">SSH, depois Telnet</option><option value="ssh">Só SSH</option><option value="telnet">Só Telnet</option></select></div>
                <div><Label className="text-xs text-slate-400">Porta</Label><Input value={adhoc.port} onChange={e => setAdhoc({ ...adhoc, port: e.target.value.replace(/\D/g, "").slice(0, 5) })} placeholder={adhoc.protocol === "telnet" ? "23" : "22"} className="bg-sunken border-line font-mono" /></div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div><Label className="text-xs text-slate-400">Fabricante</Label>
                  <select value={adhoc.device_type} onChange={e => setAdhoc({ ...adhoc, device_type: e.target.value })} data-testid="adhoc-type" className="w-full h-9 rounded-md bg-sunken border border-line text-sm text-slate-200 px-2">
                    <option value="auto">Descobrir sozinho</option>{[["huawei", "Huawei"], ["cisco", "Cisco"], ["datacom", "Datacom"], ["juniper", "Juniper"], ["zte", "ZTE"], ["mikrotik", "MikroTik"], ["linux", "Linux"], ["other", "Outro"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
                <div><Label className="text-xs text-slate-400">Sair por</Label>
                  <select value={adhoc.agent_id} onChange={e => setAdhoc({ ...adhoc, agent_id: e.target.value })} className="w-full h-9 rounded-md bg-sunken border border-line text-sm text-slate-200 px-2">
                    <option value="">Direto do BastiON</option>{agents.map(a => <option key={a.id} value={a.id}>Agente {a.name}</option>)}</select></div>
              </div>
              <p className="text-[11px] text-slate-500 leading-relaxed">Nada é cadastrado e a senha não é gravada. Uma tentativa de login por equipamento. Se a lista mistura fabricantes, use um comando que exista em todos ou rode um fabricante por vez.</p>
            </div>
          )}
          {mode === "saved" && <>
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs text-slate-400">Equipamentos ({selected.length}/{devices.length})</div>
            <div className="flex items-center gap-3">
              {selected.length > 0 && <button onClick={() => setSelected([])} data-testid="clear-selection" className="text-xs text-slate-400 hover:text-slate-100">limpar</button>}
              <button onClick={toggleAll} data-testid="toggle-all-devices" className="text-xs text-brand-soft hover:underline">
                {allShownOn ? "desmarcar" : "marcar"} {tag || q ? `os ${shownIds.length} listados` : "todos"}
              </button>
            </div>
          </div>
          <div className="relative mb-2">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar por nome, IP ou tag…" data-testid="batch-search"
                   className="w-full h-8 pl-8 pr-2 rounded-md bg-sunken border border-line text-sm text-slate-100 focus:outline-none focus:border-brand" />
          </div>
          <div className="flex flex-wrap gap-1.5 mb-2" data-testid="batch-tags">
            {[["", "Todas", devices.length], ...allTags.map(t => [t, t, countByTag[t]]), ...(countByTag.__none__ ? [["__none__", "Sem tag", countByTag.__none__]] : [])].map(([v, l, n]) => (
              <button key={v || "all"} onClick={() => setTag(v)} data-testid={`batch-tag-${v || "all"}`}
                      className={`px-2 py-0.5 rounded-md text-xs border ${tag === v ? "bg-brand border-brand text-white" : "bg-surface border-line text-slate-300 hover:border-line2"}`}>
                {l} <span className={tag === v ? "text-white/70" : "text-slate-500"}>{n}</span>
              </button>
            ))}
          </div>
          <div className="max-h-[520px] overflow-y-auto -mx-2">
            {shown.length === 0 && <div className="px-2 py-4 text-sm text-slate-500" data-testid="batch-empty">Nenhum equipamento com esse filtro.</div>}
            {groups.map(([g, list]) => {
              const ids = list.map(d => d.id);
              const on = ids.filter(id => selected.includes(id)).length;
              const isClosed = closed.has(g) && !q;
              return (
                <div key={g} data-testid={`batch-group-${g}`}>
                  <div className="sticky top-0 z-10 flex items-center gap-2 px-2 py-1.5 bg-panel border-y border-line text-xs">
                    <Checkbox checked={on === ids.length} onCheckedChange={() => setMany(ids, on !== ids.length)} aria-label={`Marcar todos de ${g === "__none__" ? "Sem tag" : g}`} data-testid={`batch-group-check-${g}`} />
                    <button onClick={() => setClosed(prev => { const n = new Set(prev); n.has(g) ? n.delete(g) : n.add(g); return n; })}
                            className="flex-1 flex items-center gap-1.5 text-slate-300 hover:text-slate-100 text-left" aria-expanded={!isClosed} data-testid={`batch-group-toggle-${g}`}>
                      {isClosed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                      <span className="font-medium">{g === "__none__" ? "Sem tag" : g}</span>
                      <span className="text-slate-500">{on ? `${on}/` : ""}{ids.length}</span>
                    </button>
                  </div>
                  {!isClosed && list.map(d => (
                    <label key={d.id} data-testid={`batch-device-${d.id}`}
                           className="flex items-center gap-3 px-2 py-1.5 hover:bg-white/[0.04] cursor-pointer">
                      <Checkbox checked={selected.includes(d.id)} onCheckedChange={() => toggle(d.id)} />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm text-slate-100 truncate">{d.name}</div>
                        <div className="text-xs text-slate-500 truncate"><span className="font-mono">{d.host}:{d.port}</span>
                          {(d.tags || []).filter(t => t !== g).map(t => <span key={t} className="ml-1.5 px-1 rounded border border-line text-slate-400">{t}</span>)}</div>
                      </div>
                      <span className={`w-2 h-2 rounded-full shrink-0 ${d.status === "online" ? "bg-emerald-400" : "bg-slate-600"}`} title={d.status === "online" ? "online" : "offline"} />
                    </label>
                  ))}
                </div>
              );
            })}
          </div>
          </>}
        </Card>

        {/* Script picker + inline + run */}
        <Card className="bg-surface border-line p-5 lg:col-span-2" data-testid="batch-runner">
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs text-slate-400">Comando</div>
            <Button size="sm" variant="ghost" onClick={() => setShowNew(v => !v)} data-testid="toggle-new-script" className="text-brand-soft">
              <Plus className="w-4 h-4 mr-1" /> Novo script
            </Button>
          </div>

          {showNew && (
            <div className="mb-4 p-3 border border-line rounded-md bg-panel space-y-2" data-testid="script-form">
              <div className="text-[11px] text-slate-500">{editingScript ? `Editando: ${editingScript.name}` : "Novo script"}</div>
              <Input placeholder="Nome" data-testid="new-script-name" value={newScript.name} onChange={e => setNewScript({ ...newScript, name: e.target.value })} className="bg-sunken border-line font-mono" />
              <Input placeholder="Descrição" data-testid="new-script-desc" value={newScript.description} onChange={e => setNewScript({ ...newScript, description: e.target.value })} className="bg-sunken border-line font-mono" />
              <Textarea placeholder="Conteúdo do script..." data-testid="new-script-content" rows={4} value={newScript.content} onChange={e => setNewScript({ ...newScript, content: e.target.value })} className="bg-sunken border-line font-mono" />
              <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                <Checkbox data-testid="new-script-quick" checked={newScript.quick} onCheckedChange={(v) => setNewScript({ ...newScript, quick: !!v })} />
                Mostrar como comando favorito na barra do terminal
              </label>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={cancelEdit} data-testid="cancel-script-btn">Cancelar</Button>
                <Button size="sm" onClick={saveScript} data-testid="save-new-script" className="bg-brand hover:bg-brand-strong">{editingScript ? "Atualizar" : "Salvar"}</Button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3 mb-3">
            <div className="col-span-2">
              <Label className="text-xs text-slate-400">Script salvo</Label>
              <Select value={scriptId || "none"} onValueChange={v => setScriptId(v === "none" ? "" : v)}>
                <SelectTrigger data-testid="script-select" className="bg-sunken border-line font-mono">
                  <SelectValue placeholder="Selecione um script" />
                </SelectTrigger>
                <SelectContent className="bg-surface border-line text-slate-100">
                  <SelectItem value="none">— nenhum —</SelectItem>
                  {scripts.map(s => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-xs text-slate-400">Timeout (s)</Label>
              <Input type="number" data-testid="batch-timeout" value={timeout} onChange={e => setTimeoutVal(e.target.value)} className="bg-sunken border-line font-mono" />
            </div>
          </div>

          <div className="mb-3">
            <Label className="text-xs text-slate-400">Ou comando inline</Label>
            <Textarea data-testid="batch-inline-cmd" rows={3} value={inline} onChange={e => setInline(e.target.value)} placeholder="uname -a" className="bg-sunken border-line font-mono text-emerald-300" />
          </div>

          <Button onClick={execute} disabled={running} data-testid="execute-batch-btn" className="bg-brand hover:bg-brand-strong w-full">
            {running ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Play className="w-4 h-4 mr-2" />}
            {mode === "ips" ? `Executar em ${ipCount} IP(s)` : `Executar em ${selected.length} equipamento(s)`}
          </Button>
          {mode === "ips" && job && (
            <div className="mt-3 flex items-center gap-3" data-testid="adhoc-progress">
              <div className="flex-1">
                <div className="h-1.5 rounded bg-sunken overflow-hidden"><div className={`h-full ${job.finished ? "bg-on" : "bg-brand"} transition-all`} style={{ width: `${Math.round(100 * job.done / Math.max(1, job.total))}%` }} /></div>
                <div className="text-[11px] text-slate-500 font-mono mt-1">{job.done}/{job.total} {job.finished ? "— concluído" : "— executando…"}{job.agent_name ? ` · via ${job.agent_name}` : ""} · usuário {job.username}</div>
              </div>
              {!job.finished && <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={stopAdhoc}><Square className="w-3.5 h-3.5 mr-1.5" />Parar</Button>}
            </div>
          )}
          {mode === "ips" && job?.invalid?.length > 0 && <div className="text-xs text-amber-300 mt-2">Linhas ignoradas: {job.invalid.join(" · ")}</div>}

          {scripts.length > 0 && (
            <div className="mt-4">
              <div className="text-xs text-slate-500 mb-2">Scripts salvos</div>
              <div className="flex flex-wrap gap-2">
                {scripts.map(s => (
                  <div key={s.id} className="flex items-center gap-2 bg-panel border border-line rounded-md px-2 py-1" data-testid={`script-chip-${s.id}`}>
                    {s.quick && <Star className="w-3 h-3 text-amber-400" />}
                    <span className="text-xs text-slate-300">{s.name}</span>
                    <button onClick={() => editScript(s)} data-testid={`edit-script-${s.id}`} className="text-slate-500 hover:text-brand-soft" title="Editar">
                      <Pencil className="w-3 h-3" />
                    </button>
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
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span>Resultados — {results.filter(r => r.ok).length} ok, {results.filter(r => !r.ok).length} com falha</span>
            <button onClick={copyAll} className="ml-auto flex items-center gap-1 hover:text-slate-100" data-testid="batch-copy-all"><Copy className="w-3 h-3" />copiar tudo</button>
            <button onClick={downloadAll} className="flex items-center gap-1 hover:text-slate-100"><Download className="w-3 h-3" />baixar .txt</button>
          </div>
          {results.map(r => (
            <Card key={r.device_id} className="bg-surface border-line p-4">
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
              {r.stdout && <pre className="text-xs font-mono text-slate-300 bg-sunken p-3 rounded border border-line overflow-x-auto whitespace-pre-wrap">{r.stdout}</pre>}
              {r.stderr && <pre className="text-xs font-mono text-red-300 bg-sunken p-3 rounded border border-red-900/40 overflow-x-auto whitespace-pre-wrap mt-1">{r.stderr}</pre>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
