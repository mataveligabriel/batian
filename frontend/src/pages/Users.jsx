import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { UserPlus, Trash2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const empty = { name: "", email: "", password: "", role: "operator" };

export default function Users() {
  const [users, setUsers] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const { user: current } = useAuth();

  const load = async () => setUsers((await api.get("/users")).data);
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.email || !form.password || !form.name) return toast.error("Preencha todos os campos");
    try {
      await api.post("/users", form);
      toast.success("Usuário criado"); setForm(empty); setOpen(false); load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (u) => {
    if (!window.confirm(`Excluir ${u.email}?`)) return;
    try { await api.delete(`/users/${u.id}`); load(); toast.success("Excluído"); }
    catch (e) { toast.error(formatApiError(e)); }
  };

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="users-page">
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Controle de Acesso</div>
          <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Usuários</h1>
        </div>
        <Button onClick={() => setOpen(true)} data-testid="add-user-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
          <UserPlus className="w-4 h-4 mr-2" /> Novo Usuário
        </Button>
      </div>
      <Card className="bg-[#111722] border-[#1E293B] overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-widest text-slate-500 font-mono bg-[#0B111C]">
            <tr>
              <th className="text-left px-4 py-3">Nome</th>
              <th className="text-left px-4 py-3">Email</th>
              <th className="text-left px-4 py-3">Papel</th>
              <th className="text-left px-4 py-3">Criado em</th>
              <th className="text-right px-4 py-3">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E293B]">
            {users.map(u => (
              <tr key={u.id} data-testid={`user-row-${u.id}`}>
                <td className="px-4 py-3 text-slate-100">{u.name}</td>
                <td className="px-4 py-3 font-mono text-slate-300">{u.email}</td>
                <td className="px-4 py-3">
                  <Badge className={u.role === "admin" ? "bg-[#007AFF]/15 text-[#4DA3FF] border-[#007AFF]/30" : "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"}>
                    {u.role}
                  </Badge>
                </td>
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{new Date(u.created_at).toLocaleString("pt-BR")}</td>
                <td className="px-4 py-3 text-right">
                  {u.id !== current?.id && (
                    <Button size="sm" variant="ghost" onClick={() => del(u)} data-testid={`del-user-${u.id}`} className="text-red-400 hover:bg-red-950/40">
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100">
          <DialogHeader><DialogTitle>Novo usuário</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div><Label>Nome</Label><Input data-testid="user-form-name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div><Label>Email</Label><Input data-testid="user-form-email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div><Label>Senha</Label><Input data-testid="user-form-password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div>
              <Label>Papel</Label>
              <Select value={form.role} onValueChange={v => setForm({ ...form, role: v })}>
                <SelectTrigger data-testid="user-form-role" className="bg-[#05070A] border-[#1E293B] font-mono"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                  <SelectItem value="operator">Operador</SelectItem>
                  <SelectItem value="admin">Administrador</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button onClick={save} data-testid="save-user-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">Criar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
