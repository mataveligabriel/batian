import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { ModulePicker } from "@/components/ModulePicker";
import { modulesSummary } from "@/lib/modules";
import { toast } from "sonner";
import { UserPlus, Trash2, Pencil, Eye, ArrowLeftRight, Loader2, Network, Gauge, ShieldCheck, ShieldAlert, ShieldOff, XCircle } from "lucide-react";
import { TransferDialog } from "@/components/TransferDialog";
import { useAuth } from "@/context/AuthContext";

const empty = { name: "", email: "", password: "", role: "operator", modules: null };
const ROLE = {
  admin: { label: "Administrador", cls: "bg-brand/15 text-brand-soft border-brand/30" },
  operator: { label: "Operador", cls: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" },
  viewer: { label: "View", cls: "bg-amber-500/10 text-amber-300 border-amber-500/30" },
};
const RoleItems = () => (<>
  <SelectItem value="operator">Operador — usa os módulos que você liberar, nos equipamentos dele</SelectItem>
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
      <div className="flex items-center gap-1.5 text-xs text-slate-400 mb-1.5">
        <Icon className="w-3.5 h-3.5" /> {title}
        <button className="ml-auto normal-case tracking-normal text-[11px] text-brand-soft" onClick={onAll}>{items.length && items.every(i => sel.has(i.id)) ? "nenhum" : "todos"}</button>
      </div>
      <div className="border border-line rounded-md max-h-80 overflow-y-auto divide-y divide-line">
        {!items.length && <div className="px-3 py-6 text-center text-xs text-slate-500 font-mono">Nenhum criado ainda</div>}
        {items.map(i => (
          <label key={i.id} className={`flex items-start gap-2 px-3 py-2 cursor-pointer text-sm ${sel.has(i.id) ? "bg-brand/10" : "hover:bg-slate-800/40"}`}>
            <input type="checkbox" className="accent-brand mt-1" checked={sel.has(i.id)} onChange={() => onT(i.id)} />
            <span className="min-w-0"><span className="block truncate text-slate-100">{i.name}</span><span className="block text-[10px] font-mono text-slate-500 truncate">{sub(i)}</span></span>
          </label>
        ))}
      </div>
    </div>
  );
  return (
    <Dialog open={!!target} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl" data-testid="access-dialog">
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
          <Button onClick={save} disabled={busy || !cat} className="bg-brand hover:bg-brand-strong" data-testid="access-save">Salvar acesso</Button>
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
    <Card className="bg-surface border-line p-4 mb-4 grid gap-4 md:grid-cols-2" data-testid="security-card">
      <div>
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-brand-soft" />
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
        <div className="text-xs text-slate-400 mb-2">Últimas tentativas recusadas</div>
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
  const [editForm, setEditForm] = useState({ name: "", role: "operator", password: "", modules: null, incidents_scope: "own", nets: "" });
  const openEdit = (u) => { setEditing(u); setEditForm({ name: u.name, role: u.role, password: "", modules: Array.isArray(u.modules) ? u.modules : null,
    incidents_scope: u.incidents_scope || "own", nets: (u.incident_nets || []).join("\n") }); };
  const saveEdit = async () => {
    try {
      const { data } = await api.put(`/users/${editing.id}`, { name: editForm.name, role: editForm.role, password: editForm.password || null,
        incidents_scope: editForm.incidents_scope, incident_nets: editForm.nets.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean),
        ...(editForm.role === "operator" ? (editForm.modules ? { modules: editForm.modules } : { modules_all: true }) : {}) });
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
      const { data } = await api.post("/users", { ...form, modules: form.role === "operator" ? form.modules : null });
      toast.success("Usuário criado"); setForm(empty); setOpen(false); load();
      if (data.role === "viewer") setAccess(data);   // já escolhe o que ele vai ver
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const reset2fa = async (u) => {
    if (!window.confirm(`Zerar o 2FA de ${u.email}? Ele entra só com a senha e precisa ativar de novo (se for exigido, é levado à ativação). As sessões dele são encerradas.`)) return;
    try { await api.post(`/users/${u.id}/2fa/reset`); toast.success("2FA zerado"); load(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const [delTarget, setDelTarget] = useState(null);   // {user, items}
  const [orphKey, setOrphKey] = useState(0);
  const del = async (u) => {
    try { const { data } = await api.get(`/users/${u.id}/items`); setDelTarget({ user: u, items: data }); }
    catch (e) { toast.error(formatApiError(e)); }
  };

  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="users-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-6">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Usuários</h1>
        </div>
        <Button onClick={() => setOpen(true)} data-testid="add-user-btn" className="bg-brand hover:bg-brand-strong">
          <UserPlus className="w-4 h-4 mr-2" /> Novo Usuário
        </Button>
      </div>
      <SecurityCard onChanged={load} />
      <OrphansCard key={orphKey} />
      <Card className="bg-surface border-line overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500 bg-panel">
            <tr>
              <th className="text-left px-4 py-3">Nome</th>
              <th className="text-left px-4 py-3">Email</th>
              <th className="text-left px-4 py-3">Papel</th>
              <th className="text-left px-4 py-3">2FA</th>
              <th className="text-left px-4 py-3">Criado em</th>
              <th className="text-right px-4 py-3">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {users.map(u => (
              <tr key={u.id} data-testid={`user-row-${u.id}`}>
                <td className="px-4 py-3 text-slate-100">{u.name}</td>
                <td className="px-4 py-3 font-mono text-slate-300">{u.email}</td>
                <td className="px-4 py-3">
                  <Badge className={(ROLE[u.role] || ROLE.operator).cls}>{(ROLE[u.role] || ROLE.operator).label}</Badge>
                  {u.role === "viewer" && <span className="ml-2 text-[10px] font-mono text-slate-500">{(u.view_maps || []).length} mapa(s) · {(u.view_dashboards || []).length} dash</span>}
                  {modulesSummary(u) && <span className="ml-2 text-[11px] text-slate-400" data-testid={`user-modules-${u.id}`}>{modulesSummary(u)}</span>}
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
                    <Button size="sm" variant="ghost" onClick={() => setTransfer(u)} data-testid={`transfer-user-${u.id}`} className="text-brand-soft hover:bg-brand/15" title="Enviar ou trazer equipamentos, mapas e dashboards">
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
        <DialogContent className="bg-surface border-line text-slate-100 max-w-xl max-h-[92vh] overflow-y-auto" data-testid="edit-user-dialog">
          <DialogHeader><DialogTitle>Editar usuário — {editing?.email}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div><Label>Nome</Label><Input data-testid="edit-user-name" value={editForm.name} onChange={e => setEditForm({ ...editForm, name: e.target.value })} className="bg-sunken border-line font-mono" /></div>
            <div>
              <Label>Papel</Label>
              <Select value={editForm.role} onValueChange={(v) => setEditForm({ ...editForm, role: v })} disabled={editing?.id === current?.id}>
                <SelectTrigger data-testid="edit-user-role" className="bg-sunken border-line font-mono"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-surface border-line text-slate-100"><RoleItems /></SelectContent>
              </Select>
            </div>
            {editForm.role === "operator" && <ModulePicker value={editForm.modules} onChange={(m) => setEditForm({ ...editForm, modules: m })} />}
            {editForm.role === "operator" && (
              <div className="rounded-md border border-line p-3 space-y-2" data-testid="edit-user-incidents">
                <Label>Incidentes que ele vê</Label>
                {[["own", "Só os da rede dele", "alarmes dos equipamentos e agentes que são dele, e das redes abaixo"],
                  ["all", "Todos", "todos os incidentes do BastiON"]].map(([k, l, d]) => (
                  <label key={k} className="flex items-start gap-2 text-sm cursor-pointer" data-testid={`edit-user-inc-${k}`}>
                    <input type="radio" name="incscope" className="accent-brand mt-1" checked={editForm.incidents_scope === k} onChange={() => setEditForm({ ...editForm, incidents_scope: k })} />
                    <span><span className="text-slate-100">{l}</span><span className="block text-xs text-slate-500">{d}</span></span>
                  </label>
                ))}
                {editForm.incidents_scope === "own" && (
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Redes dele (opcional): alarme do Zabbix/LibreNMS com IP dentro destas faixas também aparece para ele, mesmo sem o equipamento cadastrado.</div>
                    <textarea value={editForm.nets} onChange={e => setEditForm({ ...editForm, nets: e.target.value })} rows={3} data-testid="edit-user-nets"
                              placeholder={"203.0.113.0/24\n198.51.100.0/26"} className="w-full rounded-md bg-sunken border border-line p-2 text-sm font-mono text-slate-100" />
                  </div>
                )}
              </div>
            )}
            <div>
              <Label>Nova senha (opcional — redefine sem excluir o usuário)</Label>
              <Input data-testid="edit-user-password" type="password" value={editForm.password} onChange={e => setEditForm({ ...editForm, password: e.target.value })} placeholder="deixe vazio para manter" className="bg-sunken border-line font-mono" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancelar</Button>
            <Button onClick={saveEdit} data-testid="save-edit-user-btn" className="bg-brand hover:bg-brand-strong">Salvar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DeleteUserDialog target={delTarget} onClose={(done) => { setDelTarget(null); if (done) { load(); setOrphKey(k => k + 1); } }} />
      <AccessDialog target={access} onClose={() => { setAccess(null); load(); }} />
      <TransferDialog open={!!transfer} onOpenChange={(v) => !v && setTransfer(null)} peer={transfer} />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="bg-surface border-line text-slate-100 max-w-xl max-h-[92vh] overflow-y-auto" data-testid="new-user-dialog">
          <DialogHeader><DialogTitle>Novo usuário</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div><Label>Nome</Label><Input data-testid="user-form-name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="bg-sunken border-line font-mono" /></div>
            <div><Label>Email</Label><Input data-testid="user-form-email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} className="bg-sunken border-line font-mono" /></div>
            <div><Label>Senha</Label><Input data-testid="user-form-password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} className="bg-sunken border-line font-mono" /></div>
            <div>
              <Label>Papel</Label>
              <Select value={form.role} onValueChange={v => setForm({ ...form, role: v })}>
                <SelectTrigger data-testid="user-form-role" className="bg-sunken border-line font-mono"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-surface border-line text-slate-100"><RoleItems /></SelectContent>
              </Select>
            </div>
            {form.role === "operator" && <ModulePicker value={form.modules} onChange={(m) => setForm({ ...form, modules: m })} />}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button onClick={save} data-testid="save-user-btn" className="bg-brand hover:bg-brand-strong">Criar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Exclusão de usuário: o que era dele (agentes/jump servers, equipamentos, mapas…) é transferido para você ou apagado.
 *  Agente de usuário excluído deixa de ter o túnel reverso aceito. */
function DeleteUserDialog({ target, onClose }) {
  const [mode, setMode] = useState("transfer");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setMode("transfer"); }, [target]);
  const u = target?.user, it = target?.items, has = it && Object.values(it.counts || {}).some(n => n > 0);
  const go = async () => {
    setBusy(true);
    try {
      const { data } = await api.delete(`/users/${u.id}`, { params: { items: mode } });
      toast.success(has ? `${u.email} excluído — ${mode === "transfer" ? "transferido para você" : "apagado"}: ${data.text}` : `${u.email} excluído`);
      onClose(true);
    } catch (e) { toast.error(formatApiError(e)); }
    setBusy(false);
  };
  return (
    <Dialog open={!!target} onOpenChange={(v) => !v && onClose(false)}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg" data-testid="delete-user-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Trash2 className="w-4 h-4 text-red-400" /> Excluir {u?.name || u?.email}</DialogTitle></DialogHeader>
        <div className="space-y-3 text-sm">
          <p className="text-slate-300">O usuário <span className="font-mono">{u?.email}</span> deixa de entrar na hora.</p>
          {has ? (
            <>
              <p className="text-slate-300">Ele tem: <span className="text-slate-100" data-testid="delete-user-items">{it.text}</span>.</p>
              {[["transfer", "Transferir para mim", "Tudo passa a ser seu; os jump servers continuam funcionando."],
                ["delete", "Apagar tudo", "Agentes, equipamentos, mapas e o resto dele são apagados; o túnel reverso dos jump servers dele é derrubado em até 1 minuto."]]
                .map(([k, l, d]) => (
                  <label key={k} className={`flex gap-2.5 p-2.5 rounded-md border cursor-pointer ${mode === k ? "border-brand bg-brand/10" : "border-line hover:border-line2"}`} data-testid={`delete-user-${k}`}>
                    <input type="radio" name="delmode" className="accent-brand mt-0.5" checked={mode === k} onChange={() => setMode(k)} />
                    <span><span className="block text-slate-100 font-medium">{l}</span><span className="block text-xs text-slate-400 mt-0.5">{d}</span></span>
                  </label>
                ))}
            </>
          ) : <p className="text-slate-400">Ele não tem equipamentos, agentes nem outros itens.</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onClose(false)} className="text-slate-300">Cancelar</Button>
          <Button onClick={go} disabled={busy} data-testid="delete-user-confirm" className="bg-red-600 hover:bg-red-700 text-onbrand">
            {busy && <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />} Excluir
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Itens de usuários excluídos antes de a exclusão resolver o que era deles (ex.: jump server que seguia online). */
function OrphansCard() {
  const [list, setList] = useState(null);
  const load = () => api.get("/users-orphans").then(r => setList(r.data)).catch(() => setList([]));
  useEffect(() => { load(); }, []);
  if (!list || !list.length) return null;
  const act = async (o, action) => {
    const who = o.email || "usuário excluído";
    if (action === "delete" && !window.confirm(`Apagar ${o.text} de ${who}? Não dá para desfazer.`)) return;
    try {
      const { data } = await api.post("/users-orphans/resolve", { owner_id: o.owner_id, action });
      toast.success(`${action === "transfer" ? "Transferido para você" : "Apagado"}: ${data.text}`); load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <Card className="bg-surface border-amber-500/40 p-4 mb-4" data-testid="orphans-card">
      <div className="flex items-center gap-2 text-amber-300 font-medium text-sm"><ShieldAlert className="w-4 h-4" /> Itens de usuários excluídos</div>
      <p className="text-xs text-slate-400 mt-1 mb-3">Esses usuários foram excluídos, mas o que era deles ficou no BastiON. Os túneis reversos dos jump servers deles já não são aceitos; resolva abaixo.</p>
      <div className="space-y-2">
        {list.map(o => (
          <div key={o.owner_id} className="flex items-center gap-3 flex-wrap border border-line rounded-md px-3 py-2" data-testid={`orphan-${o.owner_id}`}>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-slate-100 font-mono truncate">{o.email || `usuário ${o.owner_id.slice(0, 8)}`}</div>
              <div className="text-xs text-slate-400">{o.text}{o.agents.length ? ` · jump: ${o.agents.map(a => a.name).join(", ")}` : ""}</div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => act(o, "transfer")} className="text-brand-soft hover:bg-brand/15" data-testid={`orphan-transfer-${o.owner_id}`}>
              <ArrowLeftRight className="w-4 h-4 mr-1.5" /> Trazer para mim
            </Button>
            <Button size="sm" variant="ghost" onClick={() => act(o, "delete")} className="text-red-400 hover:bg-red-950/40" data-testid={`orphan-delete-${o.owner_id}`}>
              <Trash2 className="w-4 h-4 mr-1.5" /> Apagar
            </Button>
          </div>
        ))}
      </div>
    </Card>
  );
}
