import React, { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { Navigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Toaster } from "@/components/ui/sonner";
import { formatApiError } from "@/lib/api";
import { ShieldCheck, Loader2 } from "lucide-react";

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
    <div className="min-h-screen flex items-center justify-center bg-[#05070A] px-4" data-testid="login-page">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8" data-testid="login-logo">
          <div className="w-16 h-16 rounded-2xl bg-[#007AFF]/15 border border-[#007AFF]/40 flex items-center justify-center shadow-[0_0_40px_rgba(0,122,255,0.25)]">
            <ShieldCheck className="w-9 h-9 text-[#4DA3FF]" />
          </div>
          <h1 className="font-heading text-3xl font-bold text-slate-100 mt-4 tracking-wide">BASTION</h1>
          <div className="text-[11px] uppercase tracking-[0.3em] text-slate-500 font-mono mt-1">SSH Central</div>
        </div>

        <form onSubmit={onSubmit} data-testid="login-form" className="bg-[#0B111C] border border-[#1E293B] rounded-xl p-6 space-y-4">
          {step === "totp" ? (
            <div data-testid="login-totp-step">
              <Label htmlFor="totp" className="text-xs uppercase tracking-widest text-slate-400 font-mono">Código do app autenticador</Label>
              <Input id="totp" required autoFocus inputMode="numeric" autoComplete="one-time-code" data-testid="login-totp-input"
                     value={code} onChange={e => setCode(e.target.value)} placeholder="123456"
                     className="mt-1 bg-[#05070A] border-[#1E293B] font-mono text-slate-100 text-center text-xl tracking-[0.4em]" />
              <p className="text-[11px] text-slate-500 mt-2">Abra o Google Authenticator / Authy / Microsoft Authenticator. Sem o celular? Use um código de recuperação (xxxx-xxxx).</p>
              <button type="button" onClick={() => { setStep("password"); setCode(""); }} className="text-[11px] text-[#4DA3FF] mt-1 hover:underline">voltar</button>
            </div>
          ) : (<>
          <div>
            <Label htmlFor="email" className="text-xs uppercase tracking-widest text-slate-400 font-mono">Email</Label>
            <Input id="email" type="email" required autoFocus autoComplete="username" data-testid="login-email-input"
                   value={email} onChange={e => setEmail(e.target.value)}
                   className="mt-1 bg-[#05070A] border-[#1E293B] font-mono text-slate-100" />
          </div>
          <div>
            <Label htmlFor="password" className="text-xs uppercase tracking-widest text-slate-400 font-mono">Senha</Label>
            <Input id="password" type="password" required autoComplete="current-password" data-testid="login-password-input"
                   value={password} onChange={e => setPassword(e.target.value)}
                   className="mt-1 bg-[#05070A] border-[#1E293B] font-mono text-slate-100" />
          </div>
          </>)}
          <Button type="submit" disabled={submitting} data-testid="login-submit-btn" className="w-full bg-[#007AFF] hover:bg-[#0062CC] h-10">
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : "Entrar"}
          </Button>
        </form>
      </div>
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}
