import React, { useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { Navigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Toaster } from "@/components/ui/sonner";
import { formatApiError } from "@/lib/api";
import { ShieldCheck, Terminal, Loader2 } from "lucide-react";

export default function Login() {
  const { user, login, loading } = useAuth();
  const [email, setEmail] = useState("admin@sshcentral.io");
  const [password, setPassword] = useState("admin123");
  const [submitting, setSubmitting] = useState(false);

  if (loading) return null;
  if (user) return <Navigate to="/dashboard" replace />;

  const onSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await login(email, password);
      toast.success("Autenticado com sucesso");
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex bg-[#05070A]" data-testid="login-page">
      {/* Left panel */}
      <div className="hidden lg:flex flex-col justify-between w-1/2 p-12 border-r border-[#1E293B] relative overflow-hidden">
        <div
          className="absolute inset-0 opacity-40"
          style={{
            backgroundImage:
              "radial-gradient(ellipse at 30% 20%, rgba(0,122,255,0.25), transparent 55%),radial-gradient(ellipse at 70% 80%, rgba(16,185,129,0.15), transparent 60%)",
          }}
        />
        <div className="relative">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-lg bg-[#007AFF]/20 border border-[#007AFF]/40 flex items-center justify-center">
              <ShieldCheck className="w-6 h-6 text-[#4DA3FF]" />
            </div>
            <div>
              <div className="font-heading font-bold text-2xl text-slate-100">SSH Bastion Central</div>
              <div className="text-xs uppercase tracking-widest text-slate-500 font-mono">NOC · Multi-rede · Sem VPN</div>
            </div>
          </div>
          <h1 className="mt-16 text-4xl sm:text-5xl font-heading font-bold text-slate-100 leading-tight">
            Acesse centenas de <span className="text-[#4DA3FF]">equipamentos</span><br />
            em redes distintas.<br />
            <span className="text-emerald-400">Sem VPN.</span>
          </h1>
          <p className="mt-6 text-slate-400 max-w-md leading-relaxed">
            Console tático para engenheiros de rede. Túneis reversos via agentes,
            terminal SSH interativo no navegador, execução em lote e histórico auditável.
          </p>
        </div>
        <div className="relative font-mono text-xs text-slate-500 border border-[#1E293B] bg-[#0B111C]/80 rounded-md p-4 max-w-md">
          <div className="text-emerald-400">$ ssh -J bastion-sp core-sp-01</div>
          <div className="text-slate-500">→ conectado via Agente-SP-DC01 (12.4ms)</div>
          <div className="text-slate-500">→ sessão registrada · operator@sshcentral.io</div>
        </div>
      </div>

      {/* Right panel form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <form
          onSubmit={onSubmit}
          className="w-full max-w-md bg-[#0B111C] border border-[#1E293B] rounded-xl p-8 shadow-2xl grain"
          data-testid="login-form"
        >
          <div className="flex items-center gap-2 mb-6">
            <Terminal className="w-4 h-4 text-[#4DA3FF]" />
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Autenticação NOC</div>
          </div>
          <h2 className="text-2xl font-heading font-bold text-slate-100">Bem-vindo de volta</h2>
          <p className="text-sm text-slate-400 mt-1">Entre com suas credenciais de operador.</p>

          <div className="mt-6 space-y-4">
            <div>
              <Label htmlFor="email" className="text-xs uppercase tracking-widest text-slate-400 font-mono">Email</Label>
              <Input
                id="email"
                type="email"
                data-testid="login-email-input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                className="mt-1 bg-[#05070A] border-[#1E293B] font-mono text-slate-100"
                placeholder="operador@empresa.com"
              />
            </div>
            <div>
              <Label htmlFor="password" className="text-xs uppercase tracking-widest text-slate-400 font-mono">Senha</Label>
              <Input
                id="password"
                type="password"
                data-testid="login-password-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="mt-1 bg-[#05070A] border-[#1E293B] font-mono text-slate-100"
                placeholder="••••••••"
              />
            </div>
          </div>

          <Button
            type="submit"
            disabled={submitting}
            data-testid="login-submit-btn"
            className="mt-6 w-full bg-[#007AFF] hover:bg-[#0062CC] text-white font-medium tracking-wide glow-primary"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
            Entrar no Console
          </Button>

          <div className="mt-6 text-[11px] text-slate-500 font-mono">
            Padrão inicial: <span className="text-slate-300">admin@sshcentral.io</span> / <span className="text-slate-300">admin123</span>
          </div>
        </form>
      </div>
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}
