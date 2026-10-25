import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { KeyRound, Plus, Trash2, Save, Loader2, Copy, CheckCircle2, PlugZap } from "lucide-react";

const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const list = (v) => String(v || "").split(/[\n,;]+/).map(x => x.trim()).filter(Boolean);

function Provider({ p, onChange, onDel, onTest, testing, publicUrl }) {
  const set = (k, v) => onChange({ ...p, [k]: v });
  const redirect = p.redirect_uri || `${publicUrl || window.location.origin}/api/auth/sso/${p.id || "<id>"}/callback`;
  return (
    <Card className="bg-surface border-line p-4 space-y-3" data-testid={`sso-prov-${p.id || "novo"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Switch checked={p.enabled} onCheckedChange={v => set("enabled", v)} />
        <Input value={p.name} onChange={e => set("name", e.target.value)} placeholder="Nome no botão (ex.: Google)" className="bg-sunken border-line h-9 w-56" data-testid="sso-name" />
        <Input value={p.id} onChange={e => set("id", e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder="id (ex.: google)" className="bg-sunken border-line h-9 w-36 font-mono" data-testid="sso-id" />
        <div className="ml-auto flex gap-1">
          {p.redirect_uri && <Button size="sm" variant="outline" onClick={onTest} disabled={testing} className="h-8 border-line bg-transparent" data-testid="sso-test">{testing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <PlugZap className="w-4 h-4 mr-1" />}Testar</Button>}
          <Button size="sm" variant="ghost" onClick={onDel} className="h-8 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></Button>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-[1fr_16rem_14rem]">
        <div><label className={lbl}>Emissor (issuer)</label><Input value={p.issuer} onChange={e => set("issuer", e.target.value)} className="bg-sunken border-line h-9 font-mono text-xs" data-testid="sso-issuer" /></div>
        <div><label className={lbl}>Client ID</label><Input value={p.client_id} onChange={e => set("client_id", e.target.value)} className="bg-sunken border-line h-9 font-mono text-xs" data-testid="sso-client" /></div>
        <div><label className={lbl}>Client secret{p.has_secret ? " (guardado)" : ""}</label><Input type="password" value={p.client_secret || ""} onChange={e => set("client_secret", e.target.value)} placeholder={p.has_secret ? "vazio = manter" : ""} className="bg-sunken border-line h-9" autoComplete="new-password" data-testid="sso-secret" /></div>
      </div>
      <div>
        <label className={lbl}>URL de retorno — cadastre exatamente esta no provedor</label>
        <div className="flex gap-2"><Input readOnly value={redirect} className="bg-sunken border-line h-9 font-mono text-xs" data-testid="sso-redirect" />
          <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(redirect).then(() => toast.success("Copiado")).catch(() => {})} className="border-line bg-transparent h-9"><Copy className="w-4 h-4" /></Button></div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <div><label className={lbl}>Domínios de e-mail aceitos (vazio = qualquer)</label><Input value={(p.allowed_domains || []).join(", ")} onChange={e => set("allowed_domains", list(e.target.value))} placeholder="exemplo.net" className="bg-sunken border-line h-9" data-testid="sso-domains" /></div>
        <div className="space-y-1.5 pt-4">
          <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={p.auto_create} onCheckedChange={v => set("auto_create", v)} data-testid="sso-auto" /> Criar o usuário no 1º login</label>
          <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={p.require_group} onCheckedChange={v => set("require_group", v)} /> Só quem está num grupo abaixo</label>
        </div>
        <div><label className={lbl}>Perfil de quem é criado sem grupo</label>
          <select value={p.default_role} onChange={e => set("default_role", e.target.value)} className={sel}><option value="operator">Operador</option><option value="viewer">View (só painéis)</option><option value="admin">Administrador</option></select></div>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-slate-300">Perfil pelos grupos do provedor (opcional)</summary>
        <div className="grid gap-3 md:grid-cols-4 mt-2">
          <div><label className={lbl}>Claim dos grupos</label><Input value={p.groups_claim} onChange={e => set("groups_claim", e.target.value)} className="bg-sunken border-line h-9 font-mono" /></div>
          {[["admin_groups", "Grupos → Administrador"], ["operator_groups", "Grupos → Operador"], ["viewer_groups", "Grupos → View"]].map(([k, l]) => (
            <div key={k}><label className={lbl}>{l}</label><Input value={(p[k] || []).join(", ")} onChange={e => set(k, list(e.target.value))} className="bg-sunken border-line h-9" data-testid={`sso-${k}`} /></div>
          ))}
        </div>
        <div className="text-[11px] text-slate-500 mt-1">Com grupos configurados, o perfil é atualizado a cada login (o único administrador nunca perde o perfil). No Entra ID os grupos vêm como IDs; no Keycloak, inclua o mapper "groups" no client.</div>
      </details>
    </Card>
  );
}

const MODES = [["default", "Padrão"], ["both", "Senha e SSO"], ["sso", "Só SSO"], ["password", "Só senha"]];

// o que cada usuário consegue usar, com a regra geral aplicada a quem está no "Padrão"
function UsersCard({ users, scope, pw, onChanged }) {
  const [busy, setBusy] = useState("");
  const eff = (u) => {
    const m = u.login_mode || "default";
    const sso = m === "password" ? false : m === "both" || m === "sso" ? true : scope === "all";
    const senha = m === "sso" ? false : m === "both" || m === "password" ? true : u.role === "admin" || pw !== "admins";
    return [senha, sso];
  };
  const change = async (u, v) => {
    setBusy(u.id);
    try { await api.put(`/users/${u.id}`, { login_mode: v }); toast.success(`${u.email}: ${MODES.find(m => m[0] === v)[1]}`); onChanged(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  return (
    <Card className="bg-surface border-line" data-testid="sso-users">
      <div className="px-4 py-3 border-b border-line text-sm text-slate-200">Por usuário <span className="text-xs text-slate-500">— "Padrão" segue as duas regras acima; os outros valem só para a pessoa</span></div>
      <div className="divide-y divide-line">
        {users.map(u => {
          const [senha, sso] = eff(u);
          return (
            <div key={u.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm" data-testid={`sso-user-${u.email}`}>
              <span className="text-slate-100 flex-1 min-w-[12rem] truncate">{u.email}<span className="text-xs text-slate-500 ml-2">{u.role}</span></span>
              <span className={`text-[11px] ${senha ? "text-emerald-300" : "text-slate-600 line-through"}`}>senha</span>
              <span className={`text-[11px] ${sso ? "text-emerald-300" : "text-slate-600 line-through"}`}>SSO</span>
              {!senha && !sso && <span className="text-[11px] text-red-300" data-testid={`sso-user-locked-${u.email}`}>não consegue entrar</span>}
              <select value={u.login_mode || "default"} disabled={busy === u.id} onChange={e => change(u, e.target.value)} data-testid={`sso-user-mode-${u.email}`}
                className="h-8 rounded-md bg-sunken border border-line px-2 text-xs text-slate-200">{MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export default function Sso() {
  const [c, setC] = useState(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState("");
  const load = useCallback(() => api.get("/soc/sso/settings").then(r => setC(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  if (!c) return null;
  const add = (k) => {
    const pr = c.presets[k];
    setC({ ...c, providers: [...c.providers, { id: k === "generic" ? "" : k, name: pr.name, issuer: pr.issuer, scopes: pr.scopes, client_id: "", client_secret: "", enabled: true,
      allowed_domains: [], auto_create: false, require_group: false, default_role: "operator", groups_claim: "groups", admin_groups: [], operator_groups: [], viewer_groups: [] }] });
  };
  const setP = (i, v) => setC({ ...c, providers: c.providers.map((x, j) => (j === i ? v : x)) });
  const save = async () => {
    setBusy(true);
    try { setC((await api.put("/soc/sso/settings", { providers: c.providers.map(({ redirect_uri, has_secret, ...p }) => p), public_url: c.public_url, password_login: c.password_login, sso_users: c.sso_users })).data); toast.success("Salvo"); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const test = async (p) => {
    setTesting(p.id);
    try { const { data } = await api.post(`/soc/sso/test/${p.id}`); toast.success(`${p.name}: conectou (${data.keys} chave(s) de assinatura${data.pkce ? ", PKCE" : ""})`); }
    catch (e) { toast.error(formatApiError(e)); } finally { setTesting(""); }
  };
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="sso-page">
      <div className="mb-4">
        <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><KeyRound className="w-6 h-6 text-brand-soft" /> Login único (SSO)</h1>
        <p className="text-sm text-slate-400 mt-1 max-w-3xl">Entrar com a conta da empresa por OpenID Connect — Google Workspace, Microsoft Entra ID, Keycloak, Authentik, Authelia… O 2FA fica com o provedor; o login anômalo e a cerca geográfica continuam valendo.</p>
      </div>
      <div className="space-y-4 max-w-5xl">
        <Card className="bg-surface border-line p-4 grid gap-3 md:grid-cols-[1fr_16rem_16rem_auto] items-end">
          <div><label className={lbl}>Endereço público do BastiON (de onde o provedor volta)</label>
            <Input value={c.public_url} onChange={e => setC({ ...c, public_url: e.target.value })} placeholder="https://bastion.exemplo.net" className="bg-sunken border-line h-9 font-mono" data-testid="sso-public" /></div>
          <div><label className={lbl}>Quem pode usar o SSO</label>
            <select value={c.sso_users} onChange={e => setC({ ...c, sso_users: e.target.value })} className={sel} data-testid="sso-scope">
              <option value="all">Todos (com usuário ou domínio liberado)</option><option value="selected">Só os usuários marcados abaixo</option></select></div>
          <div><label className={lbl}>Login com senha (para quem está no padrão)</label>
            <select value={c.password_login} onChange={e => setC({ ...c, password_login: e.target.value })} className={sel} data-testid="sso-pwmode">
              <option value="all">Todos podem usar senha</option><option value="admins">Só administradores (os demais pelo SSO)</option></select></div>
          <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand h-9" data-testid="sso-save">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}Salvar</Button>
        </Card>
        {c.providers.length === 0 && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">Nenhum provedor. Adicione um abaixo, cadastre a URL de retorno no provedor e cole o client ID/secret.</Card>}
        {c.sso_users === "selected" && <div className="text-[11px] text-amber-300">Com "só os usuários marcados", ninguém é criado no 1º login: libere cada pessoa em "Por usuário" (Senha e SSO ou Só SSO).</div>}
        {c.providers.map((p, i) => <Provider key={i} p={p} publicUrl={c.public_url} testing={testing === p.id} onChange={v => setP(i, v)}
          onDel={() => setC({ ...c, providers: c.providers.filter((_, j) => j !== i) })} onTest={() => test(p)} />)}
        <div className="flex flex-wrap gap-2" data-testid="sso-add">
          {Object.entries(c.presets).map(([k, v]) => <Button key={k} size="sm" variant="outline" onClick={() => add(k)} className="border-line bg-transparent" data-testid={`sso-add-${k}`}><Plus className="w-4 h-4 mr-1" />{v.name}</Button>)}
        </div>
        <UsersCard users={c.users || []} scope={c.sso_users} pw={c.password_login} onChanged={load} />
        <Card className="bg-surface border-line p-4 text-xs text-slate-400 space-y-1">
          <div className="text-slate-200 text-sm flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4 text-emerald-300" />Segurança do fluxo</div>
          <div>Authorization code com PKCE (S256), state e nonce de uso único; o id_token é conferido pela chave pública do provedor (emissor, audiência, validade). O navegador recebe só um código de 90 s, trocado pela sessão — o token nunca passa pela URL.</div>
          <div>E-mail não verificado no provedor é recusado. Usuário criado pelo SSO não tem senha utilizável; o 2FA obrigatório do BastiON não se aplica a ele (fica com o provedor).</div>
        </Card>
      </div>
    </div>
  );
}
