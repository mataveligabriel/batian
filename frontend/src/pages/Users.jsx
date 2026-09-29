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
import { UserPlus, Trash2, Pencil, Eye, ArrowLeftRight, Loader2, Network, Gauge, ShieldCheck, ShieldAlert, ShieldOff, XCircle } from "lucide-react";
import { TransferDialog } from "@/components/TransferDialog";
import { useAuth } from "@/context/AuthContext";

const empty = { name: "", email: "", password: "", role: "operator" };
const ROLE = {
  admin: { label: "Administrador", cls: "bg-[#007AFF]/15 text-[#4DA3FF] border-[#007AFF]/30" },
  operator: { label: "Operador", cls: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" },
  viewer: { label: "View", cls: "bg-amber-500/10 text-amber-300 border-amber-500/30" },
};
const RoleItems = () => (<>
  <SelectItem value="operator">Operador — acesso completo aos próprios equipamentos</SelectItem>
  <SelectItem value="admin">Administrador — gerencia usuários e configurações</SelectItem>
  <SelectItem value="viewer">View — só Painel NOC, Dashboards e Mapas (leitura)</SelectItem>
</>);

/** Quais mapas e dashboards um usuário View enxerga (de qualquer dono). */
function AccessDialog({ target, onClose }) {
  const [cat, setCat] = useState(null);
  const [maps, setMaps] = useState(new Set());
  const [dashes, setDashes] = useState(new Set());
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!target) return;
    setCat(null);
    Promise.all([api.get("/access/catalog"), api.get(`/users/${target.id}/access`)]).then(([c, a]) => {
      setCat(c.data); setMaps(new Set(a.data.map_ids)); setDashes(new Set(a.data.dashboard_ids));
    }).catch(e => toast.error(formatApiError(e)));
  }, [target]);
  const tg = (set, setter) => (id) => { const n = new Set(set); n.has(id) ? n.delete(id) : n.add(id); setter(n); };
  const save = async () => {
    setBusy(true);
    try {
      await api.put(`/users/${target.id}/access`, { map_ids: [...maps], dashboard_ids: [...dashes] });
      toast.success(`${target.name}: ${maps.size} mapa(s) e ${dashes.size} dashboard(s) liberados`); onClose();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const List = ({ title, icon: Icon, items, sel, onT, onAll, sub }) => (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-1.5 text-xs uppercase tracking-widest text-slate-400 font-mono mb-1.5">
        <Icon className="w-3.5 h-3.5" /> {title}
        <button className="ml-auto normal-case tracking-normal text-[11px] text-[#4DA3FF]" onClick={onAll}>{items.length && items.every(i => sel.has(i.id)) ? "nenhum" : "todos"}</button>
      </div>
      <div className="border border-[#1E293B] rounded-md max-h-80 overflow-y-auto divide-y divide-[#1E293B]">
        {!items.length && <div className="px-3 py-6 text-center text-xs text-slate-500 font-mono">Nenhum criado ainda</div>}
        {items.map(i => (
          <label key={i.id} className={`flex items-start gap-2 px-3 py-2 cursor-pointer text-sm ${sel.has(i.id) ? "bg-[#007AFF]/10" : "hover:bg-slate-800/40"}`}>
            <input type="checkbox" className="accent-[#007AFF] mt-1" checked={sel.has(i.id)} onChange={() => onT(i.id)} />
            <span className="min-w-0"><span className="block truncate text-slate-100">{i.name}</span><span className="block text-[10px] font-mono text-slate-500 truncate">{sub(i)}</span></span>
          </label>
        ))}
      </div>
    </div>
  );
  return (
    <Dialog open={!!target} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-3xl" data-testid="access-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Eye className="w-4 h-4 text-amber-300" /> O que {target?.name} pode ver</DialogTitle></DialogHeader>
        <p className="text-xs text-slate-400">Usuário <b>View</b> só acessa Painel NOC, Dashboards e Mapas, em modo leitura e ao vivo. Marque o que ele enxerga — de qualquer usuário.</p>
        {!cat ? <div className="py-8 text-center text-xs text-slate-500"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Carregando…</div> : (
          <div className="flex gap-4 flex-col md:flex-row">
            <List title="Mapas" icon={Network} items={cat.maps} sel={maps} onT={tg(maps, setMaps)} sub={i => `de ${i.owner_name}`}
                  onAll={() => setMaps(cat.maps.every(i => maps.has(i.id)) ? new Set() : new Set(cat.maps.map(i => i.id)))} />
            <List title="Dashboards" icon={Gauge} items={cat.dashboards} sel={dashes} onT={tg(dashes, setDashes)} sub={i => `${i.group} · de ${i.owner_name}`}
                  onAll={() => setDashes(cat.dashboards.every(i => dashes.has(i.id)) ? new Set() : new Set(cat.dashboards.map(i => i.id)))} />
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy || !cat} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="access-save">Salvar acesso</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

const REASON = { senha: "senha errada", "2fa": "código errado", bloqueado: "bloqueado" };

function SecurityCard({ onChanged }) {
  const [s, setS] = useState(null);
  const [fails, setFails] = useState([]);
  const load = async () => {
    const [a, b] = await Promise.all([api.get("/security/settings"), api.get("/security/logins", { params: { failed: true, limit: 8 } })]);
    setS(a.data); setFails(b.data);
  };
  useEffect(() => { load().catch(() => {}); }, []);
  const toggle = async () => {
    try { setS((await api.put("/security/settings", { require_2fa: !s.require_2fa })).data); onChanged?.(); toast.success(!s.require_2fa ? "2FA agora é obrigatório" : "2FA deixou de ser obrigatório"); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  if (!s) return null;
  return (
    <Card className="bg-[#111722] border-[#1E293B] p-4 mb-4 grid gap-4 md:grid-cols-2" data-testid="security-card">
      <div>
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-[#4DA3FF]" />
          <div className="text-sm font-medium text-slate-100">Exigir 2FA de todos os usuários</div>
          <label className="ml-auto flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={s.require_2fa} onChange={toggle} data-testid="require-2fa" /> {s.require_2fa ? "exigido" : "opcional"}
          </label>
        </div>
        <p className="text-[11px] text-slate-500 mt-1">Com 2FA exigido, quem ainda não ativou é levado à tela de ativação no próximo acesso. Login também bloqueia por 15 min após 5 senhas erradas.</p>
        {s.users_without_2fa.length > 0
          ? <div className="text-[11px] text-amber-300 mt-2" data-testid="users-without-2fa">Sem 2FA: {s.users_without_2fa.join(", ")}</div>
          : <div className="text-[11px] text-emerald-400 mt-2">Todos os usuários com 2FA.</div>}
      </div>
      <div>
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono mb-2">Últimas tentativas recusadas</div>
        {fails.length === 0 && <div className="text-[11px] text-slate-500">Nenhuma.</div>}
        <div className="space-y-0.5" data-testid="failed-logins">
          {fails.map(f => (
            <div key={f.id} className="flex gap-2 text-[11px] font-mono">
              <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />
              <span className="text-slate-400 w-24 shrink-0">{new Date(f.at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
              <span className="text-slate-300 truncate">{f.email}</span>
              <span className="text-slate-500 truncate ml-auto">{f.ip} · {REASON[f.reason] || f.reason}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

export default function Users() {
  const [users, setUsers] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const { user: current } = useAuth();
  const [access, setAccess] = useState(null);     // usuário View cujo acesso está sendo editado
  const [transfer, setTransfer] = useState(null); // usuário para enviar/trazer itens

  const load = async () => setUsers((await api.get("/users")).data);
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ name: "", role: "operator", password: "" });
  const openEdit = (u) => { setEditing(u); setEditForm({ name: u.name, role: u.role, password: "" }); };
  const saveEdit = async () => {
    try {
      const { data } = await api.put(`/users/${editing.id}`, { name: editForm.name, role: editForm.role, password: editForm.password || null });
      toast.success(editForm.password ? "Usuário atualizado e senha redefinida" : "Usuário atualizado");
      const becameViewer = editing.role !== "viewer" && data.role === "viewer";
      setEditing(null); load();
      if (becameViewer) setAccess(data);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.email || !form.password || !form.name) return toast.error("Preencha todos os campos");
    try {
      const { data } = await api.post("/users", form);
      toast.success("Usuário criado"); setForm(empty); setOpen(false); load();
      if (data.role === "viewer") setAccess(data);   // já escolhe o que ele vai ver
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const reset2fa = async (u) => {
    if (!window.confirm(`Zerar o 2FA de ${u.email}? Ele entra só com a senha e precisa ativar de novo (se for exigido, é levado à ativação). As sessões dele são encerradas.`)) return;
    try { await api.post(`/users/${u.id}/2fa/reset`); toast.success("2FA zerado"); load(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (u) => {
    if (!window.confirm(`Excluir ${u.email}?`)) return;
    try { await api.delete(`/users/${u.id}`); load(); toast.success("Excluído"); }
    catch (e) { toast.error(formatApiError(e)); }
  };

  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="users-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-6">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Controle de Acesso</div>
          <h1 className="font-heading text-2xl sm:text-4xl font-bold text-slate-100 mt-1">Usuários</h1>
        </div>
        <Button onClick={() => setOpen(true)} data-testid="add-user-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
          <UserPlus className="w-4 h-4 mr-2" /> Novo Usuário
        </Button>
      </div>
      <SecurityCard onChanged={load} />
      <Card className="bg-[#111722] border-[#1E293B] overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-widest text-slate-500 font-mono bg-[#0B111C]">
            <tr>
              <th className="text-left px-4 py-3">Nome</th>
              <th className="text-left px-4 py-3">Email</th>
              <th className="text-left px-4 py-3">Papel</th>
              <th className="text-left px-4 py-3">2FA</th>
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
                  <Badge className={(ROLE[u.role] || ROLE.operator).cls}>{(ROLE[u.role] || ROLE.operator).label}</Badge>
                  {u.role === "viewer" && <span className="ml-2 text-[10px] font-mono text-slate-500">{(u.view_maps || []).length} mapa(s) · {(u.view_dashboards || []).length} dash</span>}
                </td>
                <td className="px-4 py-3" data-testid={`user-2fa-${u.id}`}>
                  {u.totp_enabled
                    ? <span className="inline-flex items-center gap-1 text-[11px] font-mono text-emerald-400"><ShieldCheck className="w-3.5 h-3.5" /> ativo</span>
                    : <span className="inline-flex items-center gap-1 text-[11px] font-mono text-amber-400"><ShieldAlert className="w-3.5 h-3.5" /> não</span>}
                </td>
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{new Date(u.created_at).toLocaleString("pt-BR")}</td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  {u.role === "viewer" ? (
                    <Button size="sm" variant="ghost" onClick={() => setAccess(u)} data-testid={`access-user-${u.id}`} className="text-amber-300 hover:bg-amber-950/30" title="O que este usuário View pode ver">
                      <Eye className="w-4 h-4 md:mr-1.5" /><span className="hidden md:inline">Acesso</span>
                    </Button>
                  ) : u.id !== current?.id && (
                    <Button size="sm" variant="ghost" onClick={() => setTransfer(u)} data-testid={`transfer-user-${u.id}`} className="text-[#4DA3FF] hover:bg-[#007AFF]/15" title="Enviar ou trazer equipamentos, mapas e dashboards">
                      <ArrowLeftRight className="w-4 h-4 md:mr-1.5" /><span className="hidden md:inline">Transferir</span>
                    </Button>
                  )}
                  {u.totp_enabled && (
                    <Button size="sm" variant="ghost" onClick={() => reset2fa(u)} data-testid={`reset-2fa-${u.id}`} className="text-amber-300 hover:bg-amber-950/30" title="Zerar 2FA (celular perdido/trocado)">
                      <ShieldOff className="w-4 h-4" />
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => openEdit(u)} data-testid={`edit-user-${u.id}`} className="text-slate-300 hover:bg-slate-800">
                    <Pencil className="w-4 h-4" />
                  </Button>
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

      <Dialog open={!!editing} onOpenChange={(v) => !v && setEditing(null)}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100" data-testid="edit-user-dialog">
          <DialogHeader><DialogTitle>Editar usuário — {editing?.email}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div><Label>Nome</Label><Input data-testid="edit-user-name" value={editForm.name} onChange={e => setEditForm({ ...editForm, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div>
              <Label>Papel</Label>
              <Select value={editForm.role} onValueChange={(v) => setEditForm({ ...editForm, role: v })} disabled={editing?.id === current?.id}>
                <SelectTrigger data-testid="edit-user-role" className="bg-[#05070A] border-[#1E293B] font-mono"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100"><RoleItems /></SelectContent>
              </Select>
            </div>
            <div>
              <Label>Nova senha (opcional — redefine sem excluir o usuário)</Label>
              <Input data-testid="edit-user-password" type="password" value={editForm.password} onChange={e => setEditForm({ ...editForm, password: e.target.value })} placeholder="deixe vazio para manter" className="bg-[#05070A] border-[#1E293B] font-mono" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancelar</Button>
            <Button onClick={saveEdit} data-testid="save-edit-user-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AccessDialog target={access} onClose={() => { setAccess(null); load(); }} />
      <TransferDialog open={!!transfer} onOpenChange={(v) => !v && setTransfer(null)} peer={transfer} />

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
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100"><RoleItems /></SelectContent>
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
