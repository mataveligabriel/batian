import React, { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { Moon, Sun, Monitor, Check, Palette, RotateCcw } from "lucide-react";
import { ACCENTS, TONES, SCALES, DEFAULT_THEME, applyTheme, getTheme, normalize, saveLocalTheme, toneVars, accentVars } from "@/lib/theme";

const MODES = [
  { id: "dark", label: "Escuro", icon: Moon },
  { id: "light", label: "Claro", icon: Sun },
  { id: "system", label: "Do sistema", icon: Monitor },
];

/** Miniatura da tela com as cores do modo/tom/destaque (não depende do tema aplicado agora). */
function Preview({ mode, tone, accent }) {
  const m = mode === "system" ? "dark" : mode;
  const n = toneVars(tone, m), a = accentVars(accent, m);
  const c = (k) => `rgb(${n[k]})`;
  return (
    <div className="rounded-md overflow-hidden border w-full h-16 flex" style={{ background: c("canvas"), borderColor: c("line") }} aria-hidden>
      <div className="w-1/4 h-full" style={{ background: c("panel"), borderRight: `1px solid ${c("line")}` }}>
        <div className="m-1.5 h-1.5 rounded-sm" style={{ background: `rgb(${a.brand})` }} />
        <div className="mx-1.5 h-1 rounded-sm" style={{ background: c("slate-700") }} />
        <div className="m-1.5 h-1 rounded-sm" style={{ background: c("slate-700") }} />
      </div>
      <div className="flex-1 p-1.5">
        <div className="h-full rounded-sm p-1.5 space-y-1" style={{ background: c("surface"), border: `1px solid ${c("line")}` }}>
          <div className="h-1.5 w-2/3 rounded-sm" style={{ background: c("slate-200") }} />
          <div className="h-1 w-1/2 rounded-sm" style={{ background: c("slate-500") }} />
          <div className="h-2 w-8 rounded-sm" style={{ background: `rgb(${a.brand})` }} />
        </div>
      </div>
    </div>
  );
}

export function AppearanceDialog({ open, onOpenChange }) {
  const { user, setUser } = useAuth();
  const [t, setT] = useState(getTheme());
  const [busy, setBusy] = useState(false);
  const before = useRef(getTheme());
  useEffect(() => { if (open) { before.current = getTheme(); setT(getTheme()); } }, [open]);
  // pré-visualização ao vivo: aplica enquanto escolhe; cancelar volta ao anterior
  const set = (patch) => setT(prev => { const n = normalize({ ...prev, ...patch }); applyTheme(n); return n; });
  const close = (saved) => { if (!saved) applyTheme(before.current); onOpenChange(false); };
  const save = async () => {
    setBusy(true);
    saveLocalTheme(t);
    try {
      if (user) {
        const { data } = await api.put("/auth/me/theme", t);
        setUser({ ...user, theme: data });
      }
      toast.success("Aparência salva");
      close(true);
    } catch (e) { toast.error(formatApiError(e)); }
    setBusy(false);
  };
  const custom = !ACCENTS.some(a => a.id.toLowerCase() === t.accent.toLowerCase());

  return (
    <Dialog open={open} onOpenChange={(v) => !v && close(false)}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-xl max-h-[92vh] overflow-y-auto" data-testid="appearance-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Palette className="w-4 h-4 text-brand-soft" /> Aparência</DialogTitle></DialogHeader>
        <div className="space-y-5 text-sm">
          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Tema</div>
            <div className="grid grid-cols-3 gap-2">
              {MODES.map(({ id, label, icon: Icon }) => (
                <button key={id} type="button" onClick={() => set({ mode: id })} data-testid={`theme-mode-${id}`} aria-pressed={t.mode === id}
                  className={`rounded-md border p-2 text-left transition-colors ${t.mode === id ? "border-brand ring-1 ring-brand" : "border-line hover:border-line2"}`}>
                  {id === "system"
                    ? <div className="flex gap-1"><div className="flex-1"><Preview mode="dark" tone={t.tone} accent={t.accent} /></div><div className="flex-1"><Preview mode="light" tone={t.tone} accent={t.accent} /></div></div>
                    : <Preview mode={id} tone={t.tone} accent={t.accent} />}
                  <div className="flex items-center gap-1.5 mt-2 text-slate-200"><Icon className="w-3.5 h-3.5" /> {label}</div>
                </button>
              ))}
            </div>
          </section>

          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Cor de destaque</div>
            <div className="flex flex-wrap items-center gap-2">
              {ACCENTS.map(a => (
                <button key={a.id} type="button" title={a.label} aria-label={a.label} onClick={() => set({ accent: a.id })} data-testid={`theme-accent-${a.id.slice(1)}`}
                  className={`w-8 h-8 rounded-full flex items-center justify-center ring-offset-2 ring-offset-surface ${t.accent.toLowerCase() === a.id.toLowerCase() ? "ring-2 ring-slate-300" : ""}`}
                  style={{ background: a.id }}>
                  {t.accent.toLowerCase() === a.id.toLowerCase() && <Check className="w-4 h-4 text-onbrand" />}
                </button>
              ))}
              <label className={`h-8 pl-1 pr-2.5 rounded-full border flex items-center gap-1.5 cursor-pointer text-xs ${custom ? "border-brand text-slate-100" : "border-line text-slate-400 hover:border-line2"}`} title="Escolher outra cor">
                <input type="color" value={t.accent} onChange={e => set({ accent: e.target.value })} data-testid="theme-accent-custom"
                  className="w-6 h-6 rounded-full border-0 bg-transparent p-0 cursor-pointer" />
                {custom ? t.accent.toUpperCase() : "Outra cor"}
              </label>
            </div>
          </section>

          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Tom do fundo</div>
            <div className="flex flex-wrap gap-2">
              {TONES.map(o => {
                const m = t.mode === "light" ? "light" : "dark", v = toneVars(o.id, m);
                return (
                  <button key={o.id} type="button" onClick={() => set({ tone: o.id })} data-testid={`theme-tone-${o.id}`} aria-pressed={t.tone === o.id}
                    className={`h-8 pl-1.5 pr-3 rounded-md border flex items-center gap-2 text-xs ${t.tone === o.id ? "border-brand text-slate-100" : "border-line text-slate-300 hover:border-line2"}`}>
                    <span className="flex rounded overflow-hidden border border-line">
                      {["canvas", "surface", "slate-500"].map(k => <span key={k} className="w-3 h-5" style={{ background: `rgb(${v[k]})` }} />)}
                    </span>
                    {o.label}
                  </button>
                );
              })}
            </div>
          </section>

          <section>
            <div className="text-xs font-medium text-slate-400 mb-2">Tamanho do texto</div>
            <div className="inline-flex rounded-md border border-line overflow-hidden">
              {SCALES.map(o => (
                <button key={o.id} type="button" onClick={() => set({ scale: o.id })} data-testid={`theme-scale-${o.id}`} aria-pressed={t.scale === o.id}
                  className={`px-3 h-8 text-xs border-r border-line last:border-r-0 ${t.scale === o.id ? "bg-brand/15 text-slate-100" : "text-slate-400 hover:text-slate-100"}`}>
                  {o.label}
                </button>
              ))}
            </div>
          </section>
          <p className="text-[11px] text-slate-500">Fica salvo no seu usuário: vale em qualquer computador ou celular em que você entrar. O terminal SSH mantém o tema próprio dele (ícone de paleta no terminal).</p>
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="ghost" onClick={() => set(DEFAULT_THEME)} className="text-slate-400" data-testid="theme-reset"><RotateCcw className="w-3.5 h-3.5 mr-1.5" /> Padrão</Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => close(false)} className="text-slate-300">Cancelar</Button>
            <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="theme-save">Salvar</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
