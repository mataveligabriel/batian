import React, { useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";

export function ChangePasswordDialog({ open, onOpenChange }) {
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (form.next.length < 6) return toast.error("A nova senha deve ter pelo menos 6 caracteres");
    if (form.next !== form.confirm) return toast.error("A confirmação não confere");
    setBusy(true);
    try {
      await api.post("/auth/change-password", { current_password: form.current, new_password: form.next });
      toast.success("Senha alterada com sucesso");
      setForm({ current: "", next: "", confirm: "" });
      onOpenChange(false);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const cls = "bg-[#05070A] border-[#1E293B] font-mono";
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-sm" data-testid="change-password-dialog">
        <DialogHeader><DialogTitle>Trocar minha senha</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label>Senha atual</Label><Input data-testid="cp-current" type="password" value={form.current} onChange={e => setForm({ ...form, current: e.target.value })} className={cls} /></div>
          <div><Label>Nova senha</Label><Input data-testid="cp-new" type="password" value={form.next} onChange={e => setForm({ ...form, next: e.target.value })} className={cls} /></div>
          <div><Label>Confirmar nova senha</Label><Input data-testid="cp-confirm" type="password" value={form.confirm} onChange={e => setForm({ ...form, confirm: e.target.value })} className={cls} /></div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={submit} disabled={busy} data-testid="cp-submit" className="bg-[#007AFF] hover:bg-[#0062CC]">Alterar senha</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
