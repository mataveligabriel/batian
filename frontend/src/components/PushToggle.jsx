import React, { useState } from "react";
import { toast } from "sonner";
import { Bell, BellOff, BellRing, Loader2, Share, X } from "lucide-react";
import { api, formatApiError } from "@/lib/api";
import { BLOCKER_TEXT, disablePush, enablePush, isIOS, isStandalone, pushBlocker, usePushState } from "@/lib/pwa";

/** Liga/desliga os alarmes por notificação neste aparelho (celular ou PC). */
export function PushToggle({ mini = false }) {
  const [state, refresh] = usePushState();
  const [busy, setBusy] = useState(false);
  const on = state === "on";

  const toggle = async () => {
    if (state === "blocked") return toast.info(BLOCKER_TEXT[pushBlocker()], { duration: 8000 });
    if (state === "denied") return toast.warning("As notificações do Bastion estão bloqueadas neste aparelho. Libere nos Ajustes (Notificações → Bastion).", { duration: 8000 });
    setBusy(true);
    try {
      if (on) { await disablePush(); toast.success("Alertas desativados neste aparelho"); }
      else {
        await enablePush();
        await api.post("/push/test").catch(() => {});
        toast.success("Alertas ativados — enviei uma notificação de teste");
      }
    } catch (e) { toast.error(e?.response ? formatApiError(e) : e.message); }
    finally { setBusy(false); refresh(); }
  };

  const Icon = busy || state === "loading" ? Loader2 : on ? BellRing : state === "off" ? Bell : BellOff;
  const label = on ? "Alertas: ligados" : "Alertas neste aparelho";
  return (
    <button onClick={toggle} disabled={busy} data-testid="push-toggle" title={on ? "Desativar alertas neste aparelho" : "Receber os alarmes como notificação neste aparelho"}
            className={`w-full flex items-center ${mini ? "justify-center" : "gap-2 px-2.5"} py-1.5 rounded-md text-[12px] border transition-colors ${
              on ? "border-emerald-600/40 bg-emerald-500/10 text-emerald-300" : "border-[#1E293B] text-slate-400 hover:text-slate-100 hover:bg-slate-800/60"}`}>
      <Icon className={`w-3.5 h-3.5 shrink-0 ${busy || state === "loading" ? "animate-spin" : ""}`} />
      {!mini && <span className="truncate">{label}</span>}
    </button>
  );
}

/** Dica de instalação no iPhone/iPad (Safari não mostra botão de instalar). */
export function InstallHint() {
  const key = "bastion.installHint.dismissed";
  const [hidden, setHidden] = useState(() => { try { return localStorage.getItem(key) === "1"; } catch { return false; } });
  if (hidden || !isIOS() || isStandalone()) return null;
  const close = () => { setHidden(true); try { localStorage.setItem(key, "1"); } catch { /* ignore */ } };
  return (
    <div className="md:hidden mx-3 mt-2 rounded-md border border-[#007AFF]/40 bg-[#007AFF]/10 px-3 py-2 text-[12px] text-slate-200 flex items-start gap-2" data-testid="install-hint">
      <Share className="w-4 h-4 text-[#4DA3FF] mt-0.5 shrink-0" />
      <div className="flex-1">Instale o Bastion: toque em <b>Compartilhar</b> e depois em <b>Adicionar à Tela de Início</b>. Assim ele abre em tela cheia e recebe os alarmes.
        {pushBlocker() === "https" && <div className="text-amber-300 mt-1">Para os alarmes chegarem, o servidor precisa estar em HTTPS.</div>}</div>
      <button onClick={close} className="text-slate-400 hover:text-slate-100"><X className="w-4 h-4" /></button>
    </div>
  );
}
