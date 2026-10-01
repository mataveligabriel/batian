import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { ShieldCheck, ShieldAlert, Loader2, Copy, Download, LogOut, KeyRound, CheckCircle2, XCircle } from "lucide-react";

const inputCls = "bg-sunken border-line font-mono";
const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const browser = (ua = "") => /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox"
  : /Safari\//.test(ua) ? "Safari" : ua ? ua.split(" ")[0] : "—";
const REASON = { senha: "senha errada", "2fa": "código errado", bloqueado: "bloqueado (tentativas)" };

function RecoveryCodes({ codes, onDone }) {
  const text = codes.join("\n");
  const copy = () => navigator.clipboard?.writeText(text).then(() => toast.success("Códigos copiados"));
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([`BastiON — códigos de recuperação (cada um vale uma vez)\n\n${text}\n`], { type: "text/plain" }));
    a.download = "bastion-codigos-recuperacao.txt"; a.click(); URL.revokeObjectURL(a.href);
  };
  return (
    <div className="space-y-3" data-testid="recovery-codes">
      <div className="text-sm text-amber-300">Guarde estes códigos agora — eles não aparecem de novo. Cada um entra uma vez, se você perder o celular.</div>
      <div className="grid grid-cols-2 gap-1.5 font-mono text-sm bg-sunken border border-line rounded p-3">
        {codes.map(c => <div key={c} className="text-slate-200 text-center">{c}</div>)}
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={copy} className="border-line bg-panel text-slate-200 hover:bg-slate-800"><Copy className="w-3.5 h-3.5 mr-1" /> Copiar</Button>
        <Button size="sm" variant="outline" onClick={download} className="border-line bg-panel text-slate-200 hover:bg-slate-800"><Download className="w-3.5 h-3.5 mr-1" /> Baixar .txt</Button>
        <Button size="sm" onClick={onDone} className="ml-auto bg-brand hover:bg-brand-strong" data-testid="recovery-done">Guardei os códigos</Button>
      </div>
    </div>
  );
}

function PasswordAndCode({ label, danger, onSubmit, busy }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  return (
    <form className="space-y-2 mt-2" onSubmit={e => { e.preventDefault(); onSubmit(password, code); }}>
      <div className="grid grid-cols-2 gap-2">
        <Input type="password" placeholder="sua senha" value={password} onChange={e => setPassword(e.target.value)} className={inputCls} data-testid="sec-password" />
        <Input placeholder="código do app" value={code} onChange={e => setCode(e.target.value)} className={inputCls} data-testid="sec-code" inputMode="numeric" />
      </div>
      <Button type="submit" size="sm" disabled={busy || !password || !code} data-testid="sec-confirm"
              className={danger ? "bg-red-600 hover:bg-red-700" : "bg-brand hover:bg-brand-strong"}>
        {busy && <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />}{label}
      </Button>
    </form>
  );
}

export function AccountSecurity({ open, onOpenChange, forced = false }) {
  const { user, applySession, refreshMe } = useAuth();
  const [st, setSt] = useState(null);
  const [setup, setSetup] = useState(null);       // {secret, qr_svg}
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState(null);
  const [mode, setMode] = useState(null);         // disable | regen
  const [busy, setBusy] = useState(false);
  const [logins, setLogins] = useState([]);

  const load = async () => {
    const [a, b] = await Promise.all([api.get("/auth/2fa"), api.get("/auth/logins", { params: { limit: 10 } })]);
    setSt(a.data); setLogins(b.data);
  };
  useEffect(() => { if (open) { load().catch(e => toast.error(formatApiError(e))); } else { setSetup(null); setCodes(null); setMode(null); setCode(""); } }, [open]); // eslint-disable-line

  const run = async (fn) => { setBusy(true); try { await fn(); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); } };
  const start = () => run(async () => { setSetup((await api.post("/auth/2fa/setup")).data); setCode(""); });
  const enable = () => run(async () => {
    const { data } = await api.post("/auth/2fa/enable", { code });
    setCodes(data.recovery_codes); setSetup(null); await load(); await refreshMe();
    toast.success("2FA ativado");
  });
  const disable = (password, c) => run(async () => {
    const { data } = await api.post("/auth/2fa/disable", { password, code: c });
    await applySession(data); setMode(null); await load(); toast.success("2FA desativado");
  });
  const regen = (password, c) => run(async () => {
    const { data } = await api.post("/auth/2fa/recovery-codes", { password, code: c });
    setCodes(data.recovery_codes); setMode(null); await load();
  });
  const logoutOthers = () => run(async () => {
    const { data } = await api.post("/auth/logout-all");
    await applySession(data); toast.success("Sessões nos outros aparelhos encerradas");
  });
  const close = (v) => { if (forced && !(st?.enabled)) return; onOpenChange(v); };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg max-h-[90vh] overflow-y-auto" data-testid="account-security">
        <DialogHeader><DialogTitle>Segurança da conta</DialogTitle></DialogHeader>
        {forced && !st?.enabled && (
          <div className="text-sm text-amber-300 border border-amber-400/30 bg-amber-400/5 rounded p-2.5 flex gap-2" data-testid="forced-2fa">
            <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0" /> O administrador exige verificação em duas etapas. Ative o 2FA para continuar usando o BastiON.
          </div>
        )}
        {!st ? <Loader2 className="w-5 h-5 animate-spin text-slate-500" /> : (
          <div className="space-y-5">
            <section>
              <div className="flex items-center gap-2 mb-2">
                {st.enabled ? <ShieldCheck className="w-4 h-4 text-emerald-400" /> : <ShieldAlert className="w-4 h-4 text-amber-400" />}
                <div className="text-sm font-medium">Verificação em duas etapas (2FA)</div>
                <span className={`ml-auto text-[11px] font-mono ${st.enabled ? "text-emerald-400" : "text-amber-400"}`} data-testid="twofa-state">
                  {st.enabled ? `ativa · ${st.recovery_left} código(s) de recuperação` : "desativada"}
                </span>
              </div>

              {codes && <RecoveryCodes codes={codes} onDone={() => { setCodes(null); if (forced) onOpenChange(false); }} />}

              {!codes && !st.enabled && !setup && (
                <>
                  <p className="text-xs text-slate-400 mb-2">Além da senha, o login pede um código de 6 dígitos do app no celular (Google Authenticator, Authy, Microsoft Authenticator…).</p>
                  <Button size="sm" onClick={start} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="twofa-start">
                    {busy && <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />} Ativar 2FA
                  </Button>
                </>
              )}

              {!codes && setup && (
                <div className="space-y-3" data-testid="twofa-setup">
                  <div className="flex gap-4 items-start flex-wrap">
                    <img alt="QR code do 2FA" data-testid="twofa-qr" className="rounded bg-white p-1 w-44 h-44"
                         src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(setup.qr_svg)}`} />
                    <div className="flex-1 min-w-[180px] text-xs text-slate-400 space-y-2">
                      <div>1. No app autenticador, toque em <b className="text-slate-300">+</b> e leia o QR code.</div>
                      <div>Sem câmera? Digite a chave:</div>
                      <div className="font-mono text-[11px] text-slate-200 bg-sunken border border-line rounded p-1.5 break-all" data-testid="twofa-secret">{setup.secret}</div>
                      <div>2. Digite o código de 6 dígitos que aparece:</div>
                    </div>
                  </div>
                  <form className="flex gap-2" onSubmit={e => { e.preventDefault(); enable(); }}>
                    <Input value={code} onChange={e => setCode(e.target.value)} placeholder="123456" inputMode="numeric" autoFocus
                           className={`${inputCls} w-40 text-center tracking-[0.3em]`} data-testid="twofa-code" />
                    <Button type="submit" disabled={busy || code.replace(/\D/g, "").length !== 6} className="bg-brand hover:bg-brand-strong" data-testid="twofa-enable">
                      {busy && <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />} Confirmar
                    </Button>
                  </form>
                </div>
              )}

              {!codes && st.enabled && (
                <div className="flex gap-2 flex-wrap">
                  <Button size="sm" variant="outline" onClick={() => setMode(mode === "regen" ? null : "regen")} className="border-line bg-panel text-slate-200 hover:bg-slate-800" data-testid="twofa-regen">
                    <KeyRound className="w-3.5 h-3.5 mr-1" /> Novos códigos de recuperação
                  </Button>
                  {!st.require_2fa && (
                    <Button size="sm" variant="outline" onClick={() => setMode(mode === "disable" ? null : "disable")} className="border-red-900/60 bg-panel text-red-300 hover:bg-red-950/40" data-testid="twofa-disable">
                      Desativar 2FA
                    </Button>
                  )}
                </div>
              )}
              {mode === "regen" && <PasswordAndCode label="Gerar códigos novos" onSubmit={regen} busy={busy} />}
              {mode === "disable" && <PasswordAndCode label="Desativar 2FA" danger onSubmit={disable} busy={busy} />}
              {st.require_2fa && st.enabled && <p className="text-[11px] text-slate-500 mt-2">O 2FA é obrigatório. Trocou de celular? Peça ao administrador para zerar o seu 2FA.</p>}
            </section>

            {!(forced && !st.enabled) && (
              <>
                <section className="border-t border-line pt-4">
                  <div className="text-sm font-medium mb-1">Sessões</div>
                  <p className="text-xs text-slate-400 mb-2">Esqueceu o BastiON aberto em outro computador ou celular? Encerre todas as outras sessões (esta continua).</p>
                  <Button size="sm" variant="outline" onClick={logoutOthers} disabled={busy} className="border-line bg-panel text-slate-200 hover:bg-slate-800" data-testid="logout-others">
                    <LogOut className="w-3.5 h-3.5 mr-1" /> Encerrar outras sessões
                  </Button>
                </section>
                <section className="border-t border-line pt-4">
                  <div className="text-sm font-medium mb-2">Últimos acessos à sua conta</div>
                  <div className="space-y-1" data-testid="my-logins">
                    {logins.length === 0 && <div className="text-xs text-slate-500">Nenhum registro ainda.</div>}
                    {logins.map(l => (
                      <div key={l.id} className="flex items-center gap-2 text-[11px] font-mono">
                        {l.ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" /> : <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />}
                        <span className="text-slate-300 w-24 shrink-0">{fmt(l.at)}</span>
                        <span className="text-slate-400 w-28 shrink-0 truncate">{l.ip}</span>
                        <span className="text-slate-500 truncate">{l.ok ? browser(l.ua) : (REASON[l.reason] || l.reason)}</span>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            )}
            {user && <div className="text-[10px] text-slate-600 font-mono">{user.email}</div>}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
