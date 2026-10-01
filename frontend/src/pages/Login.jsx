import React, { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { Navigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Toaster } from "@/components/ui/sonner";
import { formatApiError } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { Wordmark, LogoMark } from "@/components/Brand";

export default function Login() {
  const { user, login, loading } = useAuth();
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
        </form>
        <p className="mt-6 text-center text-xs text-slate-500" data-testid="login-credit">Powered by <span className="text-slate-300 font-medium">Gabriel Mataveli</span></p>
      </div>
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}
