import React, { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { Navigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Toaster } from "@/components/ui/sonner";
import { api, API_BASE, formatApiError } from "@/lib/api";
import { Loader2, KeyRound } from "lucide-react";
import { Wordmark, LogoMark } from "@/components/Brand";

export default function Login() {
  const { user, login, loading, applySession } = useAuth();
  const [sso, setSso] = useState({ providers: [], password_login: "all" });
  const [ssoBusy, setSsoBusy] = useState(false);

  // volta do login único: ?sso=<código de uso único> ou ?sso_error=<motivo>
  useEffect(() => {
    api.get("/auth/sso/providers").then(r => setSso(r.data)).catch(() => {});
    const q = new URLSearchParams(window.location.search);
    const err = q.get("sso_error");
    const code = q.get("sso");
    if (err || code) { try { window.history.replaceState(null, "", "/login"); } catch { /* ok */ } }
    if (err) toast.error(err);
    if (code) {
      setSsoBusy(true);
      api.post("/auth/sso/exchange", { code }).then(r => applySession(r.data)).catch(e => toast.error(formatApiError(e))).finally(() => setSsoBusy(false));
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [step, setStep] = useState("password");     // password | totp
  const [code, setCode] = useState("");

  if (loading) return null;
  if (user) return <Navigate to="/dashboard" replace />;

  const onSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const r = await login(email.trim(), password, step === "totp" ? code.trim() : undefined);
      if (r?.need_totp) { setStep("totp"); setCode(""); }
    } catch (err) {
      toast.error(formatApiError(err));
      if (step === "totp") setCode("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-canvas px-4" data-testid="login-page">
      <div className="w-full max-w-[22rem]">
        <div className="flex flex-col items-center mb-7" data-testid="login-logo">
          <LogoMark size="lg" />
          <h1 className="mt-5 text-[2.6rem] leading-none"><Wordmark /></h1>
          <p className="text-sm text-slate-400 mt-3 text-center">Acesso e monitoramento do NOC</p>
        </div>

        <form onSubmit={onSubmit} data-testid="login-form" className="bg-surface border border-line rounded-xl p-6 space-y-4 shadow-[0_24px_60px_-30px_rgba(0,0,0,0.7)]">
          {step === "totp" ? (
            <div data-testid="login-totp-step">
              <Label htmlFor="totp" className="text-[13px] text-slate-300 font-normal">Código do app autenticador</Label>
              <Input id="totp" required autoFocus inputMode="numeric" autoComplete="one-time-code" data-testid="login-totp-input"
                     value={code} onChange={e => setCode(e.target.value)} placeholder="123456"
                     className="mt-1 bg-sunken border-line font-mono text-slate-100 text-center text-xl tracking-[0.4em]" />
              <p className="text-[11px] text-slate-500 mt-2">Abra o Google Authenticator / Authy / Microsoft Authenticator. Sem o celular? Use um código de recuperação (xxxx-xxxx).</p>
              <button type="button" onClick={() => { setStep("password"); setCode(""); }} className="text-[11px] text-brand-soft mt-1 hover:underline">voltar</button>
            </div>
          ) : (<>
          <div>
            <Label htmlFor="email" className="text-[13px] text-slate-300 font-normal">E-mail</Label>
            <Input id="email" type="email" required autoFocus autoComplete="username" data-testid="login-email-input"
                   value={email} onChange={e => setEmail(e.target.value)}
                   className="mt-1.5 h-10 bg-sunken border-line text-slate-100" />
          </div>
          <div>
            <Label htmlFor="password" className="text-[13px] text-slate-300 font-normal">Senha</Label>
            <Input id="password" type="password" required autoComplete="current-password" data-testid="login-password-input"
                   value={password} onChange={e => setPassword(e.target.value)}
                   className="mt-1.5 h-10 bg-sunken border-line text-slate-100" />
          </div>
          </>)}
          <Button type="submit" disabled={submitting} data-testid="login-submit-btn" className="w-full bg-brand hover:bg-brand-strong h-10 text-[14px]">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : "Entrar"}
          </Button>
          {sso.password_login === "admins" && step !== "totp" && <p className="text-[11px] text-slate-500 text-center -mt-1">Senha só para administradores — os demais entram pelo login único.</p>}
          {sso.providers.length > 0 && step !== "totp" && (
            <div className="space-y-2 pt-1" data-testid="login-sso">
              <div className="flex items-center gap-2 text-[11px] text-slate-500"><span className="h-px flex-1 bg-line" />ou<span className="h-px flex-1 bg-line" /></div>
              {sso.providers.map(p => (
                <Button key={p.id} type="button" variant="outline" disabled={ssoBusy} onClick={() => { window.location.href = `${API_BASE}/auth/sso/${p.id}/start`; }}
                  className="w-full h-10 border-line bg-transparent text-slate-200 hover:bg-white/[0.04]" data-testid={`login-sso-${p.id}`}>
                  {ssoBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <><KeyRound className="w-4 h-4 mr-2 text-brand-soft" />Entrar com {p.name}</>}
                </Button>
              ))}
            </div>
          )}
        </form>
        <p className="mt-6 text-center text-xs text-slate-500" data-testid="login-credit">Powered by <span className="text-slate-300 font-medium">Gabriel Mataveli</span></p>
      </div>
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}
