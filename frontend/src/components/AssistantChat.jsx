import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { Bot, X, Send, Loader2, RotateCcw, Wrench, AlertTriangle, CheckCircle2, XCircle, Clock, ShieldAlert, Sparkles } from "lucide-react";
import { STATUS } from "@/lib/netfmt";

const SUGGESTIONS = [
  "Quais interfaces estão down na BORDA?",
  "Mostra as sessões BGP da BORDA",
  "Qual o sinal da porta 100GE0/0/1 da BORDA?",
  "O que está consumindo o trânsito agora?",
];
const RISK = { baixo: ["Risco baixo", STATUS.good], medio: ["Risco médio", STATUS.warning], alto: ["Risco alto", STATUS.critical] };
const PSTATUS = {
  pending: ["Aguardando sua confirmação", Clock, "#94A3B8"], running: ["Executando…", Loader2, "#94A3B8"],
  done: ["Executada", CheckCircle2, STATUS.good], failed: ["Executada com falhas", AlertTriangle, STATUS.serious],
  cancelled: ["Cancelada — nada foi executado", XCircle, "#94A3B8"], expired: ["Expirou — peça de novo", Clock, "#94A3B8"],
};

/** Texto do assistente: blocos ``` viram <pre>, **negrito**, `código` e quebras de linha. Sem HTML cru. */
function RichText({ text }) {
  const parts = String(text || "").split(/```[a-zA-Z0-9-]*\n?/);
  return (
    <div className="space-y-1.5">
      {parts.map((p, i) => i % 2 === 1
        ? <pre key={i} className="text-[11px] font-mono bg-[#05070A] border border-[#1E293B] rounded p-2 overflow-x-auto whitespace-pre">{p.replace(/\n$/, "")}</pre>
        : p.trim() && (
          <div key={i} className="whitespace-pre-wrap break-words">
            {p.replace(/^\n+|\n+$/g, "").split(/(\*\*[^*]+\*\*|\*[^*\s](?:[^*\n]*[^*\s])?\*|`[^`\n]+`)/).map((seg, j) => (
              seg.startsWith("**") && seg.endsWith("**") ? <b key={j} className="text-slate-50">{seg.slice(2, -2)}</b>
                : seg.length > 2 && seg.startsWith("*") && seg.endsWith("*") ? <b key={j} className="text-slate-50">{seg.slice(1, -1)}</b>
                : seg.startsWith("`") && seg.endsWith("`") ? <code key={j} className="font-mono text-[12px] bg-[#05070A] px-1 rounded">{seg.slice(1, -1)}</code>
                  : <React.Fragment key={j}>{seg}</React.Fragment>
            ))}
          </div>
        ))}
    </div>
  );
}

function Proposal({ it, onDecide, busy }) {
  const [risk, rc] = RISK[it.risk] || RISK.medio;
  const [label, Icon, sc] = PSTATUS[it.status] || PSTATUS.pending;
  const [sending, setSending] = useState(false);
  const act = async (a) => { setSending(true); try { await onDecide(it.pid, a); } finally { setSending(false); } };
  return (
    <div className="border rounded-md bg-[#0B111C] p-3 text-sm" style={{ borderColor: it.status === "pending" ? rc : "#1E293B" }} data-testid={`proposal-${it.pid}`}>
      <div className="flex items-center gap-2 mb-1.5">
        <ShieldAlert className="w-4 h-4 shrink-0" style={{ color: rc }} />
        <span className="text-[11px] font-mono font-bold uppercase tracking-widest" style={{ color: rc }}>{risk}</span>
        <span className="text-[10px] font-mono text-slate-500 ml-auto">#{it.pid}</span>
      </div>
      <div className="text-slate-100 mb-2">{it.summary}</div>
      {(it.changes || []).map((c, i) => (
        <div key={i} className="mb-2">
          <div className="text-[11px] font-mono text-slate-400 mb-0.5">▶ {c.device_name} <span className="text-slate-600">({c.host})</span></div>
          <pre className="text-[11px] font-mono text-slate-100 bg-[#05070A] border border-[#1E293B] rounded p-2 overflow-x-auto whitespace-pre">{c.commands.join("\n")}</pre>
        </div>
      ))}
      {it.rollback && <details className="text-[11px] text-slate-400 mb-2"><summary className="cursor-pointer">Como desfazer</summary><pre className="font-mono whitespace-pre-wrap mt-1">{it.rollback}</pre></details>}
      {it.status === "pending" ? (
        <div className="flex gap-2">
          <button onClick={() => act("confirm")} disabled={sending || busy} className="flex-1 h-9 rounded-md bg-[#007AFF] hover:bg-[#0062CC] text-white text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-1.5" data-testid={`confirm-${it.pid}`}>
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />} Confirmar e executar
          </button>
          <button onClick={() => act("cancel")} disabled={sending} className="h-9 px-3 rounded-md border border-[#2A3345] text-slate-300 hover:bg-slate-800 text-sm" data-testid={`cancel-${it.pid}`}>Cancelar</button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 text-xs font-mono" style={{ color: sc }} data-testid={`pstatus-${it.pid}`}>
          <Icon className={`w-3.5 h-3.5 ${it.status === "running" ? "animate-spin" : ""}`} /> {label}
        </div>
      )}
      {it.status === "pending" && <div className="text-[10px] text-slate-500 mt-1.5">Nada foi executado ainda. Vale por 15 min.</div>}
    </div>
  );
}

/** Chat do assistente dentro do Bastion: botão flutuante + painel lateral (tela cheia no celular). */
export function AssistantChat({ hideButton = false, isAdmin = false }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(null);
  const [conv, setConv] = useState({ items: [], busy: false });
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const lastRev = useRef(-1);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/assistant/conversation");
      if (data.rev !== lastRev.current || data.busy !== conv.busy) { lastRev.current = data.rev; setConv(data); }
    } catch { /* offline: tenta de novo no próximo ciclo */ }
  }, [conv.busy]);
  useEffect(() => { api.get("/assistant/status").then(r => setStatus(r.data)).catch(() => setStatus({ enabled: false })); }, [open]);
  useEffect(() => { if (open) { load(); setTimeout(() => inputRef.current?.focus(), 50); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {                         // enquanto trabalha, acompanha de perto; parado, não fica consultando
    if (!conv.busy) return undefined;
    const t = setInterval(load, 1200);
    return () => clearInterval(t);
  }, [conv.busy, load]);
  useEffect(() => { const el = listRef.current; if (el) el.scrollTop = el.scrollHeight; }, [conv.items.length, open, conv.busy]);
  useEffect(() => {
    if (!open) return undefined;
    const esc = (e) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [open]);

  const send = async (t) => {
    const msg = (t ?? text).trim();
    if (!msg || conv.busy) return;
    setSending(true);
    try { const { data } = await api.post("/assistant/messages", { text: msg }); lastRev.current = data.rev; setConv(data); setText(""); }
    catch (e) { toast.error(formatApiError(e)); }
    finally { setSending(false); }
  };
  const decide = async (pid, action) => {
    try { const { data } = await api.post(`/assistant/proposals/${pid}/${action}`); lastRev.current = data.rev; setConv(data); }
    catch (e) { toast.error(formatApiError(e)); load(); }
  };
  const reset = async () => {
    if (!window.confirm("Começar uma conversa nova? O histórico desta some.")) return;
    try { const { data } = await api.post("/assistant/reset"); lastRev.current = data.rev; setConv(data); }
    catch (e) { toast.error(formatApiError(e)); }
  };

  const enabled = status?.enabled;
  if (!status || (!enabled && !isAdmin && !open)) return null;   // operador só vê o botão quando o chat está ligado

  return (
    <>
      {!open && !hideButton && (
        <button onClick={() => setOpen(true)} className="fixed z-40 right-4 bottom-[calc(1rem+env(safe-area-inset-bottom))] w-12 h-12 rounded-full bg-[#007AFF] hover:bg-[#0062CC] text-white shadow-lg shadow-black/40 flex items-center justify-center"
                title="Assistente" aria-label="Abrir assistente" data-testid="assistant-fab">
          <Bot className="w-6 h-6" />
          {conv.busy && <span className="absolute top-0.5 right-0.5 w-3 h-3 rounded-full bg-amber-400 animate-pulse" />}
        </button>
      )}
      {open && (
        <div className="fixed z-50 inset-0 md:inset-auto md:right-4 md:bottom-4 md:top-4 md:w-[440px] flex flex-col bg-[#0F1520] md:border border-[#2A3345] md:rounded-lg shadow-2xl shadow-black/60 pt-[env(safe-area-inset-top)]" data-testid="assistant-panel">
          <div className="flex items-center gap-2 px-3 h-12 border-b border-[#1E293B] shrink-0">
            <Bot className="w-5 h-5 text-[#4DA3FF]" />
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-100">Assistente</div>
              <div className="text-[10px] font-mono text-slate-500 truncate">{enabled ? `${status.provider} · ${status.model}` : "não configurado"}</div>
            </div>
            <button onClick={reset} disabled={conv.busy || !conv.items.length} className="ml-auto w-8 h-8 flex items-center justify-center text-slate-400 hover:text-slate-100 disabled:opacity-30" title="Nova conversa" data-testid="assistant-reset"><RotateCcw className="w-4 h-4" /></button>
            <button onClick={() => setOpen(false)} className="w-8 h-8 flex items-center justify-center text-slate-400 hover:text-slate-100" title="Fechar (Esc)" data-testid="assistant-close"><X className="w-5 h-5" /></button>
          </div>

          {!enabled ? (
            <div className="p-4 text-sm text-slate-300 space-y-2" data-testid="assistant-setup">
              <div className="flex items-center gap-2 text-amber-300"><AlertTriangle className="w-4 h-4" /> Assistente ainda não configurado</div>
              <div className="text-slate-400">{status.reason ? `Motivo: ${status.reason}.` : ""}</div>
              {isAdmin ? <div>Vá em <a href="/automation" className="text-[#4DA3FF] underline">Automação → Assistente IA</a>, escolha um provedor grátis (Groq ou Gemini) ou o Ollama no servidor, cole a chave e salve.</div>
                       : <div>Peça ao administrador para ligar o assistente.</div>}
            </div>
          ) : (
            <>
              <div ref={listRef} className="flex-1 overflow-y-auto px-3 py-3 space-y-2.5" data-testid="assistant-messages">
                {conv.items.length === 0 && (
                  <div className="text-sm text-slate-400 space-y-3">
                    <div>Pergunte sobre os seus equipamentos: interfaces, BGP, sinal óptico, flow — ou peça uma alteração, que eu mostro os comandos e só executo depois do seu clique.</div>
                    <div className="flex flex-col gap-1.5">
                      {SUGGESTIONS.map(s => (
                        <button key={s} onClick={() => send(s)} className="text-left text-xs px-2.5 py-1.5 rounded-md border border-[#1E293B] text-slate-300 hover:bg-slate-800/60 flex items-center gap-1.5">
                          <Sparkles className="w-3.5 h-3.5 text-slate-500 shrink-0" />{s}</button>
                      ))}
                    </div>
                    {!status.allow_changes && <div className="text-[11px] text-slate-500">Alterações estão desligadas pelo administrador: só consultas.</div>}
                  </div>
                )}
                {conv.items.map(it => {
                  if (it.kind === "user") return <div key={it.id} className="flex justify-end"><div className="max-w-[85%] bg-[#007AFF]/20 border border-[#007AFF]/30 text-slate-100 text-sm rounded-lg rounded-br-sm px-3 py-2 whitespace-pre-wrap break-words" data-testid="msg-user">{it.text}</div></div>;
                  if (it.kind === "tool") return <div key={it.id} className="flex items-center gap-1.5 text-[11px] font-mono text-slate-500 pl-1" data-testid="msg-tool"><Wrench className="w-3 h-3 shrink-0" /><span className="truncate">{it.text}</span></div>;
                  if (it.kind === "wait") return <div key={it.id} className="flex items-center gap-1.5 text-[11px] font-mono text-amber-300/80 pl-1" data-testid="msg-wait"><Clock className="w-3 h-3 shrink-0" /><span className="truncate" title={it.text}>{it.text}</span></div>;
                  if (it.kind === "proposal") return <Proposal key={it.id} it={it} onDecide={decide} busy={conv.busy} />;
                  if (it.kind === "error") return <div key={it.id} className="text-xs text-amber-300 border border-amber-400/30 bg-amber-400/5 rounded px-2.5 py-2 flex gap-1.5" data-testid="msg-error"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /><span className="whitespace-pre-wrap">{it.text.replace(/^⚠️\s*/, "")}</span></div>;
                  return <div key={it.id} className="max-w-[95%] text-sm text-slate-200 bg-[#111722] border border-[#1E293B] rounded-lg rounded-bl-sm px-3 py-2" data-testid="msg-assistant"><RichText text={it.text} /></div>;
                })}
                {conv.busy && <div className="flex items-center gap-2 text-xs text-slate-400 pl-1" data-testid="assistant-busy"><Loader2 className="w-3.5 h-3.5 animate-spin" /> trabalhando…</div>}
              </div>
              <div className="border-t border-[#1E293B] p-2 shrink-0 pb-[calc(0.5rem+env(safe-area-inset-bottom))]">
                <div className="flex gap-2 items-end">
                  <textarea ref={inputRef} value={text} onChange={e => setText(e.target.value)} rows={1} maxLength={4000}
                            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                            placeholder={conv.busy ? "Aguarde a resposta…" : "Pergunte ou peça algo… (Enter envia)"}
                            className="flex-1 resize-none max-h-32 min-h-[38px] bg-[#05070A] border border-[#1E293B] rounded-md px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-[#007AFF]" data-testid="assistant-input" />
                  <button onClick={() => send()} disabled={!text.trim() || conv.busy || sending} className="h-[38px] w-10 rounded-md bg-[#007AFF] hover:bg-[#0062CC] text-white flex items-center justify-center disabled:opacity-40" aria-label="Enviar" data-testid="assistant-send">
                    {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  </button>
                </div>
                <div className="text-[10px] text-slate-600 mt-1 px-1">Consultas rodam direto. Alterações só depois do seu clique em Confirmar. Tudo fica no Histórico.</div>
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}
