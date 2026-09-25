import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { Bot, Plus, Trash2, Loader2, PlugZap, ShieldCheck } from "lucide-react";

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";
const num = (n) => (n || 0).toLocaleString("pt-BR");

/** Configuração do assistente Claude no Telegram (somente admin). */
export function AIAssistantCard() {
  const [s, setS] = useState(null);
  const [users, setUsers] = useState([]);
  const [audit, setAudit] = useState([]);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);

  const load = async () => {
    const [a, u, l] = await Promise.all([api.get("/ai/settings"), api.get("/users"), api.get("/ai/audit", { params: { limit: 30 } })]);
    setS(a.data); setUsers(u.data); setAudit(l.data);
  };
  useEffect(() => { load().catch(e => toast.error(formatApiError(e))); }, []);

  if (!s) return null;
  const inputCls = "bg-[#05070A] border-[#1E293B] font-mono";
  const content = "bg-[#111722] border-[#1E293B] text-slate-100";
  const setUser = (i, patch) => setS({ ...s, ai_users: s.ai_users.map((u, j) => j === i ? { ...u, ...patch } : u) });

  const save = async () => {
    setBusy(true);
    try {
      const { data } = await api.put("/ai/settings", {
        ai_enabled: s.ai_enabled, ai_model: s.ai_model, ai_allow_changes: s.ai_allow_changes,
        ai_users: s.ai_users.filter(u => String(u.telegram_id).trim()),
        anthropic_api_key: apiKey || null, clear_api_key: clearKey,
      });
      setS(data); setApiKey(""); setClearKey(false);
      toast.success("Assistente salvo");
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const test = async () => {
    setTesting(true);
    try {
      const { data } = await api.post("/ai/test");
      data.ok ? toast.success(`Claude respondeu (${data.model}): ${data.reply}`) : toast.error(data.error);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setTesting(false); }
  };

  const u = s.usage || {};
  const statusColor = s.bot_status === "ativo" ? "text-emerald-400" : s.bot_status?.startsWith("erro") ? "text-red-400" : "text-slate-400";

  return (
    <Card className="bg-[#111722] border-[#1E293B] p-5 mt-6" data-testid="ai-card">
      <div className="flex items-center justify-between gap-2 mb-1 flex-wrap">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-[#4DA3FF]" />
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Assistente IA no Telegram (Claude)</div>
        </div>
        <div className="text-[11px] font-mono">bot: <span className={statusColor} data-testid="ai-bot-status">{s.bot_status}</span></div>
      </div>
      <p className="text-xs text-slate-400 mb-4 max-w-3xl">
        Converse com o bot do Telegram configurado acima para consultar e operar os equipamentos em linguagem natural.
        Comandos de leitura rodam direto; <b>qualquer alteração exige clique em ✅ Confirmar</b> no próprio Telegram.
        Cada usuário do Telegram só enxerga os equipamentos do usuário do Bastion vinculado a ele.
        {!s.has_telegram_token && <span className="text-amber-400"> Configure o token do bot em Notificações primeiro.</span>}
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label>Assistente ativo</Label>
            <Switch data-testid="ai-enabled" checked={s.ai_enabled} onCheckedChange={v => setS({ ...s, ai_enabled: v })} />
          </div>
          <div>
            <Label>Chave da API da Anthropic</Label>
            <Input data-testid="ai-api-key" type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
                   placeholder={s.has_api_key ? "•••••••• (mantida — digite para trocar)" : "sk-ant-… (console.anthropic.com)"} className={inputCls} />
            {s.has_api_key && (
              <label className="flex items-center gap-2 mt-1 text-[11px] text-slate-400 cursor-pointer">
                <input type="checkbox" checked={clearKey} onChange={e => setClearKey(e.target.checked)} /> remover chave
              </label>
            )}
          </div>
          <div>
            <Label>Modelo</Label>
            <Select value={s.ai_model} onValueChange={v => setS({ ...s, ai_model: v })}>
              <SelectTrigger data-testid="ai-model" className={inputCls}><SelectValue /></SelectTrigger>
              <SelectContent className={content}>
                {(s.models || []).map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-start justify-between gap-3">
            <div>
              <Label>Permitir alterações</Label>
              <div className="text-[11px] text-slate-500">Desligado = só consultas. Ligado = propõe comandos e executa após confirmação.</div>
            </div>
            <Switch data-testid="ai-allow-changes" checked={s.ai_allow_changes} onCheckedChange={v => setS({ ...s, ai_allow_changes: v })} />
          </div>
          <div className="text-[11px] font-mono text-slate-500 border border-[#1E293B] rounded p-2" data-testid="ai-usage">
            Uso em {u.month}: {num(u.requests)} chamadas · entrada {num(u.input_tokens)} · saída {num(u.output_tokens)} · cache {num(u.cache_read_tokens)} tokens
          </div>
        </div>

        <div className="lg:col-span-2">
          <div className="flex items-center justify-between mb-2">
            <Label className="flex items-center gap-1.5"><ShieldCheck className="w-3.5 h-3.5" /> Usuários autorizados no Telegram</Label>
            <Button size="sm" variant="ghost" onClick={() => setS({ ...s, ai_users: [...s.ai_users, { telegram_id: "", user_id: users[0]?.id || "", label: "" }] })}
                    data-testid="ai-add-user" className="text-[#4DA3FF] hover:bg-[#007AFF]/15">
              <Plus className="w-4 h-4 mr-1" /> Adicionar
            </Button>
          </div>
          <div className="text-[11px] text-slate-500 mb-2">Para descobrir o ID, a pessoa manda <code>/id</code> para o bot (ou qualquer mensagem — ele responde com o ID quando não está autorizado).</div>
          {s.ai_users.length === 0 && <div className="text-sm text-slate-500 font-mono py-3">Ninguém autorizado ainda — o bot recusa todas as mensagens.</div>}
          <div className="space-y-2">
            {s.ai_users.map((row, i) => (
              <div key={i} className="grid grid-cols-12 gap-2 items-center" data-testid={`ai-user-row-${i}`}>
                <Input className={`${inputCls} col-span-3`} placeholder="ID Telegram" value={row.telegram_id}
                       onChange={e => setUser(i, { telegram_id: e.target.value.replace(/\D/g, "") })} />
                <Input className={`${inputCls} col-span-3`} placeholder="Apelido (opcional)" value={row.label || ""}
                       onChange={e => setUser(i, { label: e.target.value })} />
                <div className="col-span-5">
                  <Select value={row.user_id} onValueChange={v => setUser(i, { user_id: v })}>
                    <SelectTrigger className={inputCls}><SelectValue placeholder="Usuário do Bastion" /></SelectTrigger>
                    <SelectContent className={content}>
                      {users.map(x => <SelectItem key={x.id} value={x.id}>{x.name || x.email} · {x.email}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <Button size="sm" variant="ghost" className="col-span-1 text-red-400 hover:bg-red-950/40"
                        onClick={() => setS({ ...s, ai_users: s.ai_users.filter((_, j) => j !== i) })}>
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="flex justify-end gap-2 mt-4">
        <Button variant="outline" onClick={test} disabled={testing} data-testid="ai-test-btn" className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">
          {testing ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <PlugZap className="w-4 h-4 mr-2" />} Testar Claude
        </Button>
        <Button onClick={save} disabled={busy} data-testid="ai-save-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
          {busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar assistente
        </Button>
      </div>

      <div className="mt-5 border-t border-[#1E293B] pt-3" data-testid="ai-audit">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono mb-2">Últimas ações do assistente nos equipamentos</div>
        {audit.length === 0 && <div className="text-sm text-slate-500 font-mono">Nenhuma ainda.</div>}
        <div className="divide-y divide-[#1E293B] max-h-80 overflow-y-auto">
          {audit.map(a => (
            <details key={a.id} className="py-2 text-sm">
              <summary className="cursor-pointer flex flex-wrap gap-x-3 items-center">
                <span className={`text-[10px] uppercase font-mono px-1.5 py-0.5 rounded border ${a.kind === "change" ? "border-amber-500/50 text-amber-400" : "border-[#1E293B] text-slate-400"}`}>
                  {a.kind === "change" ? "alteração" : "leitura"}
                </span>
                <span className="text-slate-100">{a.device_name}</span>
                <span className={a.ok ? "text-emerald-400 text-xs" : "text-red-400 text-xs"}>{a.ok ? "ok" : "falha"}</span>
                <span className="text-[11px] font-mono text-slate-500">{fmt(a.at)} · {a.user_email}{a.proposal_id ? ` · #${a.proposal_id}` : ""}</span>
              </summary>
              <pre className="mt-2 text-[11px] font-mono text-emerald-300 bg-[#05070A] border border-[#1E293B] rounded p-2 whitespace-pre-wrap">{(a.commands || []).join("\n")}</pre>
              <pre className="mt-1 text-[11px] font-mono text-slate-400 bg-[#05070A] border border-[#1E293B] rounded p-2 whitespace-pre-wrap max-h-60 overflow-y-auto">{a.output}</pre>
            </details>
          ))}
        </div>
      </div>
    </Card>
  );
}
