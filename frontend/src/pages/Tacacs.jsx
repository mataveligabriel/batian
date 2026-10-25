import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Fingerprint, Save, Loader2, Plus, Trash2, Copy, Download, Play, RefreshCw, KeyRound, CheckCircle2, XCircle, AlertTriangle, PlugZap, RotateCcw, Eye } from "lucide-react";

const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const ROLE = { admin: "Administrador", operator: "Operador", viewer: "Visualização" };
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const lines = (t) => String(t || "").split(/[\s,;]+/).map(x => x.trim()).filter(Boolean);
const copy = (t, what = "Copiado") => { try { navigator.clipboard.writeText(t).then(() => toast.success(what), () => toast.error("Não consegui copiar")); } catch (e) { toast.error("Não consegui copiar"); } };
const RES = { pass: "text-emerald-300", start: "text-sky-300", stop: "text-sky-300", watchdog: "text-slate-400", fail: "text-red-300", deny: "text-amber-300", error: "text-red-300" };
const TYPE = { authen: "Login", author: "Autorização", acct: "Contabilidade" };

function ShowOnce({ pw, who, onClose }) {
  if (!pw) return null;
  return (
    <Card className="bg-emerald-500/5 border-emerald-500/40 p-3 flex flex-wrap items-center gap-3" data-testid="tac-showonce">
      <KeyRound className="w-4 h-4 text-emerald-300" />
      <span className="text-sm text-slate-200">Senha TACACS de <b>{who}</b>:</span>
      <code className="font-mono text-emerald-200 bg-sunken border border-line rounded px-2 py-0.5" data-testid="tac-newpw">{pw}</code>
      <Button size="sm" variant="ghost" onClick={() => copy(pw)} className="text-slate-300"><Copy className="w-4 h-4 mr-1" />Copiar</Button>
      <span className="text-xs text-slate-500 flex-1">Aparece só agora — passe para a pessoa por um canal seguro.</span>
      <Button size="sm" variant="ghost" onClick={onClose} className="text-slate-500">Fechar</Button>
    </Card>
  );
}

function Chips({ options, value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1">{options.map(o => {
      const on = value.includes(o.id);
      return <button key={o.id} type="button" onClick={() => onChange(on ? value.filter(x => x !== o.id) : [...value, o.id])}
        className={`text-[11px] px-1.5 py-0.5 rounded border ${on ? "border-brand/60 text-brand-soft bg-brand/10" : "border-line text-slate-500"}`}>{o.name}</button>;
    })}</div>
  );
}

function Overview({ cfg, reload }) {
  const [st, setSt] = useState(null);
  const [t, setT] = useState({ username: "", password: "", command: "" });
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get("/tacacs/status").then(r => setSt(r.data)).catch(() => {}), []);
  useEffect(() => { load(); const i = setInterval(load, 10000); return () => clearInterval(i); }, [load]);
  const test = async () => {
    setBusy(true); setRes(null);
    try { setRes((await api.post("/tacacs/test", t)).data); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const c24 = st?.last24h || {};
  const steps = [
    [!!cfg.server_ip, "IP do BastiON que os equipamentos alcançam", "Ajustes"],
    [cfg.clients.length > 0, "Equipamentos (IPs/redes) que podem usar o TACACS", "Equipamentos"],
    [null, "Ligar o TACACS para as pessoas e contas de serviço", "Usuários"],
    [null, "Gerar o script do fabricante e aplicar (mantendo o login local)", "Script"],
    [null, "Liberar TCP 49 no firewall do servidor só para a rede de gerência", ""],
  ];
  return (
    <div className="space-y-4 max-w-5xl" data-testid="tac-overview">
      <div className="grid gap-3 md:grid-cols-[1fr_1fr]">
        <Card className="bg-surface border-line p-4 space-y-2">
          <div className="flex items-center gap-2 text-sm text-slate-200 font-semibold">Serviço
            <Button size="sm" variant="ghost" onClick={load} className="ml-auto text-slate-500 h-7"><RefreshCw className="w-3.5 h-3.5" /></Button></div>
          {!st ? <div className="text-sm text-slate-500">…</div> : st.alive && st.listening ? (
            <div className="text-sm text-emerald-300 flex items-center gap-1.5" data-testid="tac-alive"><CheckCircle2 className="w-4 h-4" />No ar na porta TCP {st.port} · {st.clients} cliente(s) com chave</div>
          ) : (
            <div className="text-sm text-red-300 flex items-start gap-1.5" data-testid="tac-down"><XCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{st.at ? (st.error ? `Não abriu a porta: ${st.error}` : `Sem sinal desde ${fmt(st.at)}`) : "O serviço tacacs ainda não subiu."} Rode o update.sh (ele sobe o container "tacacs") e veja <span className="font-mono">docker compose logs tacacs</span>.</span></div>
          )}
          {!cfg.enabled && <div className="text-xs text-amber-300">Desligado em Ajustes: o serviço recusa todas as conexões.</div>}
          <div className="grid grid-cols-3 gap-2 pt-1 text-center">
            {[["Logins ok", c24["authen:pass"] || 0, "text-emerald-300"], ["Logins negados", c24["authen:fail"] || 0, "text-red-300"], ["Comandos negados", c24["author:deny"] || 0, "text-amber-300"]].map(([l, n, c]) => (
              <div key={l} className="rounded border border-line bg-sunken py-2"><div className={`text-lg font-semibold ${c}`}>{n}</div><div className="text-[11px] text-slate-500">{l} (24 h)</div></div>))}
          </div>
          {(st?.recent || []).length > 0 && <div className="pt-1"><div className="text-[11px] text-slate-500 mb-1">Diagnóstico recente</div>
            {(st.recent || []).slice(0, 5).map((r, i) => <div key={i} className="text-[11px] font-mono text-slate-400 truncate">{fmt(r.at)} {r.kind === "unknown" ? "IP não cadastrado" : r.kind} {r.ip} {r.text}</div>)}</div>}
        </Card>
        <Card className="bg-surface border-line p-4 space-y-2">
          <div className="text-sm text-slate-200 font-semibold">Passo a passo</div>
          {steps.map(([ok, t2, tab], i) => (
            <div key={i} className="flex items-start gap-2 text-sm">
              <span className={`w-5 h-5 rounded-full text-[11px] flex items-center justify-center shrink-0 ${ok ? "bg-emerald-500/20 text-emerald-300" : "bg-sunken text-slate-400 border border-line"}`}>{ok ? "✓" : i + 1}</span>
              <span className="text-slate-300">{t2}{tab && <span className="text-slate-500"> — aba {tab}</span>}</span>
            </div>))}
        </Card>
      </div>
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="text-sm text-slate-200 font-semibold flex items-center gap-2"><PlugZap className="w-4 h-4 text-brand-soft" />Testar um login</div>
        <p className="text-xs text-slate-400">Faz um login TACACS+ de verdade no serviço (como se fosse um equipamento): confere se está no ar, a senha e o perfil que o equipamento recebe. Com comando, mostra se a política de comandos deixaria.</p>
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_1.4fr_auto] items-end">
          <div><label className={lbl}>Usuário TACACS</label><Input value={t.username} onChange={e => setT({ ...t, username: e.target.value })} className="bg-sunken border-line h-9" data-testid="tac-test-user" /></div>
          <div><label className={lbl}>Senha</label><Input type="password" value={t.password} onChange={e => setT({ ...t, password: e.target.value })} className="bg-sunken border-line h-9" autoComplete="new-password" data-testid="tac-test-pw" /></div>
          <div><label className={lbl}>Comando (opcional)</label><Input value={t.command} onChange={e => setT({ ...t, command: e.target.value })} placeholder="reset saved-configuration" className="bg-sunken border-line h-9 font-mono" data-testid="tac-test-cmd" /></div>
          <Button onClick={test} disabled={busy || !t.username} className="bg-brand hover:bg-brand-strong text-onbrand h-9" data-testid="tac-test-go">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Play className="w-4 h-4 mr-1" />}Testar</Button>
        </div>
        {res && (
          <div className="text-sm space-y-1" data-testid="tac-test-res">
            {!res.reachable ? <div className="text-red-300">{res.message}</div> : res.login
              ? <div className="text-emerald-300 flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4" />Login aceito — {res.message}</div>
              : <div className="text-red-300 flex items-center gap-1.5"><XCircle className="w-4 h-4" />Login negado (veja o motivo na aba Eventos)</div>}
            {res.profile && <div className="text-slate-300">Perfil enviado ao equipamento: <span className="font-mono text-slate-100">{res.profile.join(" · ")}</span></div>}
            {res.command && <div className={res.command.allowed ? "text-emerald-300" : "text-amber-300"}>Comando: {res.command.allowed ? "permitido" : `negado — ${res.command.message}`}</div>}
          </div>
        )}
      </Card>
    </div>
  );
}

function Users({ cfg }) {
  const [d, setD] = useState(null);
  const [edit, setEdit] = useState({});
  const [once, setOnce] = useState(null);
  const [acc, setAcc] = useState(null);
  const load = useCallback(() => api.get("/tacacs/users").then(r => { setD(r.data); setEdit({}); }).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  if (!d) return null;
  const row = (u) => edit[u.id] || { enabled: u.tacacs.enabled, username: u.tacacs.username, password: "", clients: u.tacacs.clients };
  const set = (u, k, v) => setEdit({ ...edit, [u.id]: { ...row(u), [k]: v } });
  const save = async (u, generate = false) => {
    const r0 = row(u);
    try {
      const r = (await api.put(`/tacacs/users/${u.id}`, { ...r0, generate })).data;
      if (r.password) setOnce({ pw: r.password, who: r.tacacs.username });
      toast.success(r0.enabled ? `TACACS ligado para ${r.tacacs.username}` : "Salvo");
      load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const saveAcc = async (a, generate = false) => {
    try {
      const body = { username: a.username, role: a.role, description: a.description || "", enabled: a.enabled !== false, password: a.password || "", generate, clients: a.clients || [] };
      const r = (a.id ? await api.put(`/tacacs/accounts/${a.id}`, body) : await api.post("/tacacs/accounts", body)).data;
      if (r.password) setOnce({ pw: r.password, who: r.username });
      setAcc(null); load(); toast.success("Conta salva");
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const delAcc = async (a) => { try { await api.delete(`/tacacs/accounts/${a.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const src = cfg.password_source;
  return (
    <div className="space-y-4 max-w-6xl" data-testid="tac-users">
      <ShowOnce pw={once?.pw} who={once?.who} onClose={() => setOnce(null)} />
      <Card className="bg-surface border-line p-3 text-xs text-slate-400">
        Senha usada no equipamento: <b className="text-slate-200">{src === "bastion" ? "a mesma do BastiON" : src === "both" ? "a do TACACS ou a do BastiON" : "uma senha TACACS própria"}</b>
        {cfg.otp === "if_enabled" && " · quem tem 2FA digita a senha seguida dos 6 números do app"} (muda em Ajustes). A pessoa também pode definir a própria senha TACACS em "Meu acesso".
        Grupos de equipamentos vazios = todos.
      </Card>
      <Card className="bg-surface border-line">
        <div className="overflow-x-auto"><table className="w-full text-sm">
          <thead className="text-xs text-slate-500"><tr><th className="text-left px-4 py-2">TACACS</th><th className="text-left px-2">Pessoa</th><th className="text-left px-2">Usuário no equipamento</th><th className="text-left px-2">Senha</th><th className="text-left px-2">Grupos</th><th className="text-left px-2">Último login</th><th /></tr></thead>
          <tbody className="divide-y divide-line">{d.users.map(u => {
            const r = row(u); const dirty = !!edit[u.id];
            return (
              <tr key={u.id} data-testid={`tac-user-${u.id}`}>
                <td className="px-4 py-2"><Switch checked={r.enabled} onCheckedChange={v => set(u, "enabled", v)} data-testid={`tac-user-on-${u.id}`} /></td>
                <td className="px-2"><div className="text-slate-100">{u.name || u.email}</div><div className="text-[11px] text-slate-500">{u.email} · {ROLE[u.role]}{u.totp_enabled ? " · 2FA" : ""}</div></td>
                <td className="px-2"><Input value={r.username} onChange={e => set(u, "username", e.target.value)} className="bg-sunken border-line h-8 w-40 font-mono" data-testid={`tac-user-name-${u.id}`} /></td>
                <td className="px-2">
                  <div className="flex items-center gap-1">
                    <Input type="password" value={r.password} onChange={e => set(u, "password", e.target.value)} placeholder={u.tacacs.has_password ? "•••• (definida)" : src === "bastion" ? "usa a do BastiON" : "definir"} className="bg-sunken border-line h-8 w-36" autoComplete="new-password" data-testid={`tac-user-pw-${u.id}`} />
                    <Button size="sm" variant="ghost" title="Gerar uma senha forte e mostrar uma vez" onClick={() => save(u, true)} className="h-8 px-2 text-slate-400" data-testid={`tac-user-gen-${u.id}`}><KeyRound className="w-4 h-4" /></Button>
                  </div>
                  {r.enabled && !u.tacacs.has_password && src === "tacacs" && <div className="text-[10px] text-amber-300 mt-0.5">sem senha TACACS: ainda não entra</div>}
                </td>
                <td className="px-2"><Chips options={cfg.clients} value={r.clients} onChange={v => set(u, "clients", v)} /></td>
                <td className="px-2 text-xs text-slate-500 whitespace-nowrap">{fmt(u.tacacs.last_login)}{u.tacacs.last_device ? <div className="font-mono">{u.tacacs.last_device}</div> : null}</td>
                <td className="px-2 pr-4 text-right"><Button size="sm" onClick={() => save(u)} disabled={!dirty} className="h-8 bg-brand hover:bg-brand-strong text-onbrand" data-testid={`tac-user-save-${u.id}`}><Save className="w-3.5 h-3.5 mr-1" />Salvar</Button></td>
              </tr>);
          })}</tbody>
        </table></div>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-3" data-testid="tac-accounts">
        <div className="flex items-center justify-between">
          <div><div className="text-sm font-semibold text-slate-200">Contas de serviço</div>
            <div className="text-xs text-slate-500">Para automações sem pessoa por trás: o usuário que o próprio BastiON usa nos backups e no lote, scripts, coletores.</div></div>
          <Button size="sm" variant="outline" onClick={() => setAcc({ username: "", role: "operator", description: "", enabled: true, clients: [] })} className="border-line bg-transparent" data-testid="tac-acc-add"><Plus className="w-4 h-4 mr-1" />Conta</Button>
        </div>
        {acc && (
          <div className="grid gap-2 sm:grid-cols-[10rem_9rem_1fr_10rem_auto] items-end rounded border border-line p-3">
            <div><label className={lbl}>Usuário</label><Input value={acc.username} onChange={e => setAcc({ ...acc, username: e.target.value })} className="bg-sunken border-line h-9 font-mono" data-testid="tac-acc-name" /></div>
            <div><label className={lbl}>Perfil</label><select value={acc.role} onChange={e => setAcc({ ...acc, role: e.target.value })} className={`${sel} w-full`}>{Object.entries(ROLE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
            <div><label className={lbl}>Descrição</label><Input value={acc.description} onChange={e => setAcc({ ...acc, description: e.target.value })} className="bg-sunken border-line h-9" /></div>
            <div><label className={lbl}>Senha (vazio = gerar)</label><Input type="password" value={acc.password || ""} onChange={e => setAcc({ ...acc, password: e.target.value })} className="bg-sunken border-line h-9" autoComplete="new-password" /></div>
            <div className="flex gap-1"><Button onClick={() => saveAcc(acc)} className="h-9 bg-brand hover:bg-brand-strong text-onbrand" data-testid="tac-acc-save"><Save className="w-4 h-4" /></Button><Button variant="ghost" onClick={() => setAcc(null)} className="h-9 text-slate-500">✕</Button></div>
            <div className="sm:col-span-5"><span className="text-[11px] text-slate-500 mr-2">Grupos de equipamentos (vazio = todos):</span><Chips options={cfg.clients} value={acc.clients || []} onChange={v => setAcc({ ...acc, clients: v })} /></div>
          </div>
        )}
        {d.accounts.length === 0 && !acc && <div className="text-sm text-slate-500">Nenhuma conta de serviço.</div>}
        <div className="divide-y divide-line">{d.accounts.map(a => (
          <div key={a.id} className="flex flex-wrap items-center gap-3 py-2 text-sm" data-testid={`tac-acc-${a.username}`}>
            <span className={`w-2 h-2 rounded-full ${a.enabled !== false ? "bg-emerald-400" : "bg-slate-600"}`} />
            <span className="font-mono text-slate-100 w-40">{a.username}</span><span className="text-xs text-slate-400 w-28">{ROLE[a.role]}</span>
            <span className="text-xs text-slate-500 flex-1">{a.description}</span>
            <span className="text-xs text-slate-500">{fmt(a.last_login)}</span>
            <Button size="sm" variant="ghost" onClick={() => setAcc({ ...a, password: "" })} className="h-7 text-slate-400">Editar</Button>
            <Button size="sm" variant="ghost" title="Gerar senha nova" onClick={() => saveAcc(a, true)} className="h-7 text-slate-400"><KeyRound className="w-3.5 h-3.5" /></Button>
            <Button size="sm" variant="ghost" onClick={() => delAcc(a)} className="h-7 text-slate-500 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></Button>
          </div>))}</div>
      </Card>
    </div>
  );
}

function Clients({ cfg, setCfg, saveCfg, busy }) {
  const [shown, setShown] = useState({});
  const upd = (i, k, v) => setCfg({ ...cfg, clients: cfg.clients.map((c, j) => (j === i ? { ...c, [k]: v } : c)) });
  const reveal = async (c) => {
    try { const r = await api.get(`/tacacs/clients/${c.id}/secret`); setShown({ ...shown, [c.id]: r.data.secret }); } catch (e) { toast.error(formatApiError(e)); }
  };
  const regen = async (c) => {
    try { const r = await api.post(`/tacacs/clients/${c.id}/secret`, { secret: "" }); setShown({ ...shown, [c.id]: r.data.secret }); toast.success("Chave nova — atualize nos equipamentos desse grupo"); } catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <div className="space-y-3 max-w-5xl" data-testid="tac-clients">
      <Card className="bg-surface border-line p-3 text-xs text-slate-400">Cada grupo tem a sua chave (shared secret) e o fabricante, que define o perfil enviado (priv-lvl, usuário-modelo do Junos, roles do NX-OS…). Conexão de IP fora destes grupos é fechada na hora. Use o IP de origem que o equipamento usa para falar com o BastiON (loopback ou gerência).</Card>
      {cfg.clients.map((c, i) => (
        <Card key={c.id || i} className="bg-surface border-line p-4 space-y-3" data-testid={`tac-client-${i}`}>
          <div className="flex flex-wrap items-center gap-2">
            <Switch checked={c.enabled !== false} onCheckedChange={v => upd(i, "enabled", v)} />
            <Input value={c.name} onChange={e => upd(i, "name", e.target.value)} placeholder="Nome do grupo (ex.: Bordas Huawei)" className="bg-sunken border-line h-9 w-64" data-testid={`tac-client-name-${i}`} />
            <select value={c.vendor} onChange={e => upd(i, "vendor", e.target.value)} className={sel} data-testid={`tac-client-vendor-${i}`}>{Object.entries(cfg.vendors).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            <Button size="sm" variant="ghost" onClick={() => setCfg({ ...cfg, clients: cfg.clients.filter((_, j) => j !== i) })} className="ml-auto text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></Button>
          </div>
          <div className="grid gap-3 md:grid-cols-[1fr_1fr]">
            <div><label className={lbl}>IPs ou redes dos equipamentos (um por linha)</label>
              <textarea value={(c.networks || []).join("\n")} onChange={e => upd(i, "networks", e.target.value.split("\n"))} rows={3} placeholder={"10.255.0.0/24\n192.0.2.1"} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid={`tac-client-nets-${i}`} /></div>
            <div><label className={lbl}>Chave (shared secret)</label>
              {c.id && c.has_secret ? (
                <div className="flex items-center gap-1">
                  <code className="flex-1 font-mono text-xs bg-sunken border border-line rounded px-2 py-2 text-slate-300 truncate">{shown[c.id] || "•••••••••••••••••••••"}</code>
                  <Button size="sm" variant="ghost" title="Mostrar" onClick={() => reveal(c)} className="text-slate-400"><Eye className="w-4 h-4" /></Button>
                  {shown[c.id] && <Button size="sm" variant="ghost" onClick={() => copy(shown[c.id])} className="text-slate-400"><Copy className="w-4 h-4" /></Button>}
                  <Button size="sm" variant="ghost" title="Gerar chave nova" onClick={() => regen(c)} className="text-slate-400"><RotateCcw className="w-4 h-4" /></Button>
                </div>
              ) : <div className="text-xs text-slate-500 py-2">Gerada ao salvar.</div>}
              <Input value={c.description || ""} onChange={e => upd(i, "description", e.target.value)} placeholder="observação" className="bg-sunken border-line h-8 mt-2 text-xs" />
            </div>
          </div>
        </Card>
      ))}
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => setCfg({ ...cfg, clients: [...cfg.clients, { name: "", networks: [], vendor: "huawei", enabled: true }] })} className="border-line bg-transparent" data-testid="tac-client-add"><Plus className="w-4 h-4 mr-1" />Grupo de equipamentos</Button>
        <Button onClick={() => saveCfg({ clients: cfg.clients.map(c => ({ ...c, networks: lines((c.networks || []).join("\n")) })) })} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="tac-clients-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
      </div>
    </div>
  );
}

function Script({ cfg, reloadCfg }) {
  const nav = useNavigate();
  const [f, setF] = useState({ client_id: cfg.clients[0]?.id || "", vendor: cfg.clients[0]?.vendor || "huawei", source: "", cmd: true, acct: true });
  const [out, setOut] = useState(null);
  const [tpl, setTpl] = useState(null);
  const load = useCallback(() => {
    api.get("/tacacs/config", { params: f }).then(r => setOut(r.data)).catch(e => toast.error(formatApiError(e)));
  }, [f]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);
  const pickClient = (id) => { const c = cfg.clients.find(x => x.id === id); setF({ ...f, client_id: id, vendor: c?.vendor || f.vendor }); };
  const download = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([out.text], { type: "text/plain" })); a.download = `tacacs-${out.vendor}.txt`; a.click(); URL.revokeObjectURL(a.href); };
  const toBatch = () => nav("/batch", { state: { commands: out.text } });
  const saveTpl = async (reset = false) => {
    try { await api.put(`/tacacs/templates/${f.vendor}`, { text: reset ? "" : tpl }); toast.success(reset ? "Modelo original de volta" : "Modelo salvo"); setTpl(null); await reloadCfg(); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const editTpl = () => setTpl((cfg.templates || {})[f.vendor] || cfg.default_templates[f.vendor] || "");
  return (
    <div className="space-y-3 max-w-5xl" data-testid="tac-script">
      {!cfg.server_ip && <Card className="bg-amber-500/5 border-amber-500/40 p-3 text-sm text-amber-200"><AlertTriangle className="w-4 h-4 inline mr-1.5" />Informe em Ajustes o IP do BastiON que os equipamentos alcançam{cfg.server_ip_hint ? ` (o endereço público é ${cfg.server_ip_hint})` : ""}.</Card>}
      <Card className="bg-surface border-line p-4 grid gap-3 sm:grid-cols-[1fr_1fr_12rem] items-end">
        <div><label className={lbl}>Grupo de equipamentos (a chave vem dele)</label>
          <select value={f.client_id} onChange={e => pickClient(e.target.value)} className={`${sel} w-full`} data-testid="tac-script-client">
            <option value="">— sem grupo (chave de exemplo) —</option>{cfg.clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
        <div><label className={lbl}>Fabricante</label>
          <select value={f.vendor} onChange={e => setF({ ...f, vendor: e.target.value })} className={`${sel} w-full`} data-testid="tac-script-vendor">{Object.entries(cfg.vendors).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        <div><label className={lbl}>Interface/IP de origem (opcional)</label><Input value={f.source} onChange={e => setF({ ...f, source: e.target.value })} placeholder="LoopBack0" className="bg-sunken border-line h-9 font-mono" data-testid="tac-script-source" /></div>
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.cmd} onCheckedChange={v => setF({ ...f, cmd: v })} disabled={!cfg.cmd_author} />Autorizar comando a comando (Política de comandos)</label>
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.acct} onCheckedChange={v => setF({ ...f, acct: v })} />Registrar sessões e comandos (contabilidade)</label>
      </Card>
      {out && (
        <Card className="bg-surface border-line p-4 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-slate-200">{cfg.vendors[out.vendor]}</span>
            {out.custom_template && <span className="text-[11px] px-1.5 rounded border border-brand/50 text-brand-soft">modelo editado</span>}
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => copy(out.text)} className="text-slate-300" data-testid="tac-script-copy"><Copy className="w-4 h-4 mr-1" />Copiar</Button>
              <Button size="sm" variant="ghost" onClick={download} className="text-slate-300"><Download className="w-4 h-4 mr-1" />Baixar</Button>
              <Button size="sm" variant="ghost" onClick={toBatch} className="text-slate-300" data-testid="tac-script-batch"><Play className="w-4 h-4 mr-1" />Levar para o Lote</Button>
              <Button size="sm" variant="ghost" onClick={editTpl} className="text-slate-400" data-testid="tac-script-edit">Editar modelo</Button>
            </div>
          </div>
          {out.note && <div className="text-xs text-amber-300"><AlertTriangle className="w-3.5 h-3.5 inline mr-1" />{out.note}</div>}
          <pre className="text-[12px] font-mono text-slate-200 bg-sunken border border-line rounded p-3 overflow-x-auto whitespace-pre max-h-[32rem]" data-testid="tac-script-text">{out.text}</pre>
          <p className="text-[11px] text-slate-500">A chave aparece no script: trate como senha. Aplique primeiro em um equipamento, deixe a sessão atual aberta e teste o login numa segunda janela — o login local continua como segundo método.</p>
        </Card>
      )}
      {tpl !== null && (
        <Card className="bg-surface border-line p-4 space-y-2" data-testid="tac-tpl">
          <div className="text-sm font-semibold text-slate-200">Modelo — {cfg.vendors[f.vendor]}</div>
          <p className="text-xs text-slate-500">Variáveis: {"{{server}} {{secret}} {{source}} {{junos_admin}} {{junos_oper}} {{junos_ro}}"}. Linha que começa com <span className="font-mono">?source</span>, <span className="font-mono">?cmd</span> ou <span className="font-mono">?acct</span> só entra com a opção ligada.</p>
          <textarea value={tpl} onChange={e => setTpl(e.target.value)} rows={16} spellCheck={false} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" data-testid="tac-tpl-text" />
          <div className="flex gap-2"><Button size="sm" onClick={() => saveTpl(false)} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="tac-tpl-save"><Save className="w-4 h-4 mr-1" />Salvar modelo</Button>
            <Button size="sm" variant="outline" onClick={() => saveTpl(true)} className="border-line bg-transparent"><RotateCcw className="w-4 h-4 mr-1" />Voltar ao original</Button>
            <Button size="sm" variant="ghost" onClick={() => setTpl(null)} className="text-slate-500">Fechar</Button></div>
        </Card>
      )}
    </div>
  );
}

function Events() {
  const [rows, setRows] = useState(null);
  const [f, setF] = useState({ type: "", result: "", q: "" });
  const load = useCallback(() => api.get("/tacacs/events", { params: f }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [f]);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [load]);
  return (
    <Card className="bg-surface border-line" data-testid="tac-events">
      <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-line items-center">
        <select value={f.type} onChange={e => setF({ ...f, type: e.target.value })} className={sel}><option value="">tudo</option>{Object.entries(TYPE).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <select value={f.result} onChange={e => setF({ ...f, result: e.target.value })} className={sel}><option value="">qualquer resultado</option><option value="pass">aceito</option><option value="fail">negado</option><option value="deny">comando negado</option></select>
        <Input value={f.q} onChange={e => setF({ ...f, q: e.target.value })} placeholder="usuário, IP, equipamento, comando" className="bg-sunken border-line h-9 w-72" data-testid="tac-ev-q" />
        <Button size="sm" variant="ghost" onClick={load} className="text-slate-400"><RefreshCw className="w-4 h-4" /></Button>
      </div>
      {rows && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nenhum evento ainda.</div>}
      <div className="overflow-x-auto"><table className="w-full text-sm">
        <tbody className="divide-y divide-line">{(rows || []).map(e => (
          <tr key={e.id}>
            <td className="px-4 py-1.5 text-xs text-slate-500 whitespace-nowrap">{fmt(e.at)}</td>
            <td className="px-2 text-xs text-slate-400">{TYPE[e.type] || e.type}</td>
            <td className={`px-2 text-xs ${RES[e.result] || "text-slate-400"}`}>{e.result}</td>
            <td className="px-2 font-mono text-slate-200">{e.user}</td>
            <td className="px-2 text-xs text-slate-300">{e.device_name || e.nas_ip}<div className="text-[10px] text-slate-500 font-mono">{e.device_name ? e.nas_ip : ""}{e.rem_addr ? ` ← ${e.rem_addr}` : ""}</div></td>
            <td className="px-2 text-xs"><span className="font-mono text-slate-200">{e.cmd}</span> <span className="text-slate-500">{e.reason}</span></td>
          </tr>))}</tbody>
      </table></div>
    </Card>
  );
}

function Settings({ cfg, setCfg, saveCfg, busy }) {
  const prof = (r, k, v) => setCfg({ ...cfg, profiles: { ...cfg.profiles, [r]: { ...cfg.profiles[r], [k]: v } } });
  return (
    <div className="space-y-4 max-w-4xl" data-testid="tac-settings">
      <Card className="bg-surface border-line p-4 grid gap-3 sm:grid-cols-3 items-end">
        <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={cfg.enabled} onCheckedChange={v => setCfg({ ...cfg, enabled: v })} data-testid="tac-enabled" />Servidor TACACS+ ligado</label>
        <div><label className={lbl}>IP do BastiON visto pelos equipamentos</label><Input value={cfg.server_ip} onChange={e => setCfg({ ...cfg, server_ip: e.target.value })} placeholder={cfg.server_ip_hint || "192.0.2.10"} className="bg-sunken border-line h-9 font-mono" data-testid="tac-server-ip" /></div>
        <div><label className={lbl}>Porta TCP</label><Input type="number" value={cfg.port} onChange={e => setCfg({ ...cfg, port: e.target.value })} className="bg-sunken border-line h-9" /></div>
        <div><label className={lbl}>Senha no equipamento</label>
          <select value={cfg.password_source} onChange={e => setCfg({ ...cfg, password_source: e.target.value })} className={`${sel} w-full`} data-testid="tac-pwsrc">
            <option value="tacacs">Senha TACACS própria (recomendado)</option><option value="bastion">A mesma do BastiON</option><option value="both">Qualquer uma das duas</option></select></div>
        <div><label className={lbl}>2FA</label>
          <select value={cfg.otp} onChange={e => setCfg({ ...cfg, otp: e.target.value })} className={`${sel} w-full`} data-testid="tac-otp">
            <option value="never">Não pedir no equipamento</option><option value="if_enabled">Quem tem 2FA: senha + 6 números do app</option></select></div>
        <div><label className={lbl}>Guardar eventos por (dias)</label><Input type="number" value={cfg.retention_days} onChange={e => setCfg({ ...cfg, retention_days: e.target.value })} className="bg-sunken border-line h-9" /></div>
        <label className="flex items-center gap-2 text-sm text-slate-300 sm:col-span-3"><Switch checked={cfg.cmd_author} onCheckedChange={v => setCfg({ ...cfg, cmd_author: v })} />Autorização comando a comando passa pela Política de comandos (bloquear / pedir aprovação / alertar)</label>
        <label className="flex items-center gap-2 text-sm text-slate-300 sm:col-span-3"><Switch checked={cfg.readonly_viewer} onCheckedChange={v => setCfg({ ...cfg, readonly_viewer: v })} />Perfil Visualização: só comandos de consulta (show, display, ping, traceroute…)</label>
        <label className="flex items-center gap-2 text-sm text-slate-300 sm:col-span-3"><Switch checked={cfg.fail_open_cmd} onCheckedChange={v => setCfg({ ...cfg, fail_open_cmd: v })} />Se a política de comandos falhar, deixar o comando passar (desligado: o equipamento cai no método seguinte)</label>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="text-sm font-semibold text-slate-200">Perfil enviado ao equipamento</div>
        <p className="text-xs text-slate-500">Conforme o perfil da pessoa no BastiON. O Junos usa o usuário-modelo (crie com o script); NX-OS usa roles; IOS XR, task groups.</p>
        <table className="w-full text-sm"><thead className="text-xs text-slate-500"><tr><th className="text-left py-1">Perfil</th><th className="text-left px-2">priv-lvl</th><th className="text-left px-2">Junos (usuário-modelo)</th><th className="text-left px-2">NX-OS (role)</th><th className="text-left px-2">IOS XR (task)</th></tr></thead>
          <tbody>{Object.keys(ROLE).map(r => (
            <tr key={r}><td className="py-1 text-slate-300">{ROLE[r]}</td>
              <td className="px-2"><Input type="number" min={0} max={15} value={cfg.profiles[r].priv} onChange={e => prof(r, "priv", e.target.value)} className="bg-sunken border-line h-8 w-20" data-testid={`tac-priv-${r}`} /></td>
              {["junos", "nxos", "iosxr"].map(k => <td key={k} className="px-2"><Input value={cfg.profiles[r][k]} onChange={e => prof(r, k, e.target.value)} className="bg-sunken border-line h-8 font-mono text-xs" /></td>)}
            </tr>))}</tbody></table>
      </Card>
      <Button onClick={() => saveCfg({})} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="tac-settings-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
    </div>
  );
}

function Me() {
  const [m, setM] = useState(null);
  const [f, setF] = useState({ current: "", password: "", again: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/tacacs/me").then(r => setM(r.data)).catch(() => {}); }, []);
  if (!m) return null;
  const save = async () => {
    if (f.password !== f.again) return toast.error("As duas senhas não conferem");
    setBusy(true);
    try { await api.put("/tacacs/me/password", { password: f.password, current: f.current }); toast.success("Senha TACACS trocada"); setF({ current: "", password: "", again: "" }); setM({ ...m, has_password: true }); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Card className="bg-surface border-line p-4 space-y-3 max-w-xl" data-testid="tac-me">
      <div className="text-sm font-semibold text-slate-200">Meu acesso aos equipamentos (TACACS+)</div>
      {!m.enabled ? <div className="text-sm text-slate-400">Seu acesso TACACS não está ligado. Peça a um administrador.</div> : <>
        <div className="text-sm text-slate-300">Usuário nos equipamentos: <span className="font-mono text-slate-100">{m.username}</span></div>
        <div className="text-xs text-slate-500">{m.password_source === "bastion" ? "A senha é a mesma do BastiON." : m.has_password ? `Senha TACACS definida em ${fmt(m.password_set_at)}.` : "Você ainda não tem senha TACACS."}
          {m.otp === "if_enabled" && " Se você usa 2FA, digite a senha seguida dos 6 números do app."}</div>
        {m.password_source !== "bastion" && <div className="grid gap-2 sm:grid-cols-3">
          <div><label className={lbl}>Senha atual do BastiON</label><Input type="password" value={f.current} onChange={e => setF({ ...f, current: e.target.value })} className="bg-sunken border-line h-9" autoComplete="current-password" data-testid="tac-me-cur" /></div>
          <div><label className={lbl}>Nova senha TACACS</label><Input type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} className="bg-sunken border-line h-9" autoComplete="new-password" data-testid="tac-me-pw" /></div>
          <div><label className={lbl}>Repita</label><Input type="password" value={f.again} onChange={e => setF({ ...f, again: e.target.value })} className="bg-sunken border-line h-9" autoComplete="new-password" data-testid="tac-me-pw2" /></div>
          <Button onClick={save} disabled={busy || f.password.length < 10} className="bg-brand hover:bg-brand-strong text-onbrand w-fit" data-testid="tac-me-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Trocar senha</Button>
        </div>}
      </>}
    </Card>
  );
}

const TABS = [["", "Visão geral"], ["usuarios", "Usuários"], ["equipamentos", "Equipamentos"], ["script", "Script"], ["eventos", "Eventos"], ["ajustes", "Ajustes"], ["meu", "Meu acesso"]];

export default function Tacacs() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = admin ? sp.get("tab") || "" : "meu";
  const [cfg, setCfg] = useState(null);
  const [busy, setBusy] = useState(false);
  const loadCfg = useCallback(() => (admin ? api.get("/tacacs/settings").then(r => setCfg(r.data)).catch(e => toast.error(formatApiError(e))) : Promise.resolve()), [admin]);
  useEffect(() => { loadCfg(); }, [loadCfg]);
  const saveCfg = async (extra) => {
    setBusy(true);
    try {
      const body = { ...cfg, ...extra, port: Number((extra.port ?? cfg.port)) || 49, retention_days: Number(cfg.retention_days) || 90 };
      ["vendors", "notes", "default_templates", "server_ip_hint"].forEach(k => delete body[k]);
      const r = await api.put("/tacacs/settings", body);
      setCfg({ ...cfg, ...r.data }); toast.success("Salvo");
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const tabs = useMemo(() => (admin ? TABS : TABS.filter(t => t[0] === "meu")), [admin]);
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="tac-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Fingerprint className="w-6 h-6 text-brand-soft" /> TACACS+</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">O BastiON como servidor de login dos equipamentos: cada pessoa entra com o próprio usuário, o perfil sai do BastiON, comando perigoso passa pela política e tudo fica registrado.</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm flex-wrap">
          {tabs.map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`tac-tab-${k || "home"}`}>{l}</button>)}
        </div>
      </div>
      {tab === "meu" ? <Me /> : !cfg ? <div className="text-sm text-slate-500">Carregando…</div>
        : tab === "usuarios" ? <Users cfg={cfg} />
          : tab === "equipamentos" ? <Clients cfg={cfg} setCfg={setCfg} saveCfg={saveCfg} busy={busy} />
            : tab === "script" ? <Script cfg={cfg} reloadCfg={loadCfg} />
              : tab === "eventos" ? <Events />
                : tab === "ajustes" ? <Settings cfg={cfg} setCfg={setCfg} saveCfg={saveCfg} busy={busy} />
                  : <Overview cfg={cfg} reload={loadCfg} />}
    </div>
  );
}
