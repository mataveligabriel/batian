import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Telescope, Loader2, Play, Settings2, Copy, Search, RotateCcw, Trash2 } from "lucide-react";

const VENDOR = { huawei: "Huawei", juniper: "Juniper", cisco: "Cisco", mikrotik: "MikroTik", datacom: "Datacom", zte: "ZTE", linux: "Linux" };
const TPL_LABEL = { ping: "Ping", ping6: "Ping IPv6", trace: "Traceroute", trace6: "Traceroute IPv6", route: "Rota BGP", route6: "Rota BGP IPv6",
  summary: "Vizinhos BGP", summary6: "Vizinhos BGP IPv6" };
const HINT = { ping: "IP ou nome — ex.: 8.8.8.8 ou registro.br", trace: "IP ou nome — ex.: 1.1.1.1", route: "IP ou prefixo — ex.: 200.160.0.0/20" };

function ManageDialog({ open, onClose, onSaved }) {
  const [data, setData] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [q, setQ] = useState("");
  const [vendor, setVendor] = useState("huawei");
  const [cmds, setCmds] = useState({});
  const [saving, setSaving] = useState(false);
  const [pub, setPub] = useState(null);                 // acesso público (sem login, porta própria)
  const [pubSel, setPubSel] = useState(new Set());
  useEffect(() => {
    if (!open) return;
    setData(null); setQ("");
    api.get("/lg/admin").then(({ data }) => {
      setData(data); setSel(new Set(data.devices.filter(d => d.looking_glass).map(d => d.id)));
      setCmds(JSON.parse(JSON.stringify(data.overrides || {})));
      setPub({ ...data.public, password: "" }); setPubSel(new Set(data.public?.routers || []));
    }).catch(e => { toast.error(formatApiError(e)); onClose(); });
  }, [open]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data?.devices || []).filter(d => !t || `${d.name} ${d.host} ${(d.tags || []).join(" ")} ${d.device_type}`.toLowerCase().includes(t));
  }, [data, q]);
  const toggle = (id) => { setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; }); setPubSel(s => { const n = new Set(s); n.delete(id); return n; }); };
  const togglePub = (id) => setPubSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setQ1 = (k, on) => setPub(p => ({ ...p, queries: on ? [...new Set([...p.queries, k])] : p.queries.filter(x => x !== k) }));
  const pubUrl = data?.public_port ? `http://${window.location.hostname}:${data.public_port}` : `${window.location.origin}/looking-glass`;
  const setTpl = (k, v) => setCmds(c => ({ ...c, [vendor]: { ...(c[vendor] || {}), [k]: v } }));
  const save = async () => {
    setSaving(true);
    try {
      await api.put("/lg/admin/commands", { commands: cmds });
      await api.put("/lg/admin/routers", { device_ids: [...sel] });
      await api.put("/lg/admin/public", { ...pub, per_min: Number(pub.per_min) || 6, per_day: Number(pub.per_day) || 200, routers: [...pubSel].filter(id => sel.has(id)),
        password: pub.password ? pub.password : (pub.username && data.public?.has_password && pub.username === data.public.username ? null : "") });
      toast.success("Looking Glass atualizado"); onSaved(); onClose();
    } catch (e) { toast.error(formatApiError(e)); } finally { setSaving(false); }
  };
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl max-h-[92vh] overflow-y-auto" data-testid="lg-manage-dialog">
        <DialogHeader><DialogTitle>Configurar o Looking Glass</DialogTitle></DialogHeader>
        {!data ? <div className="py-10 text-center text-slate-500"><Loader2 className="w-5 h-5 animate-spin inline" /></div> : (
          <div className="space-y-5">
            <div>
              <div className="flex items-center gap-3 mb-2">
                <Label className="text-slate-300">Roteadores liberados para consulta <span className="text-slate-500 font-mono text-xs">({sel.size})</span></Label>
                <div className="relative ml-auto w-56">
                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-500" />
                  <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar nome, IP ou tag" className="pl-8 h-8 bg-sunken border-line text-xs" data-testid="lg-manage-search" />
                </div>
              </div>
              <div className="border border-line rounded-md max-h-64 overflow-y-auto divide-y divide-line/60 bg-sunken">
                {shown.map(d => (
                  <label key={d.id} className="flex items-center gap-3 px-3 py-2 text-sm cursor-pointer hover:bg-panel" data-testid={`lg-dev-${d.id}`}>
                    <Checkbox checked={sel.has(d.id)} onCheckedChange={() => toggle(d.id)} />
                    <span className="text-slate-200 truncate">{d.name}</span>
                    {sel.has(d.id) && <button type="button" onClick={(e) => { e.preventDefault(); togglePub(d.id); }} data-testid={`lg-pub-${d.id}`}
                      className={`px-1.5 py-0.5 rounded border text-[10px] ${pubSel.has(d.id) ? "border-on text-on bg-on/10" : "border-line text-slate-500 hover:text-slate-300"}`}>{pubSel.has(d.id) ? "público ✓" : "tornar público"}</button>}
                    <span className="font-mono text-[11px] text-slate-500">{d.host}</span>
                    <span className="ml-auto text-[10px] font-mono text-slate-500">{VENDOR[d.device_type] || d.device_type}{(d.tags || []).length ? ` · ${d.tags.join(", ")}` : ""}</span>
                  </label>
                ))}
                {!shown.length && <div className="px-3 py-6 text-center text-xs text-slate-500 font-mono">Nenhum equipamento</div>}
              </div>
              <p className="text-[11px] text-slate-500 mt-1.5">Quem tem o módulo Looking Glass consulta estes roteadores mesmo sem ter acesso ao equipamento. Marque só roteadores de borda/núcleo.</p>
            </div>
            {pub && (
              <div className="border border-line rounded-md p-3 bg-sunken space-y-3" data-testid="lg-public-box">
                <div className="flex items-center gap-2 text-sm text-slate-200"><Checkbox id="lgp-enabled" checked={!!pub.enabled} onCheckedChange={v => setPub(p => ({ ...p, enabled: !!v }))} data-testid="lg-pub-enabled" />
                  <label htmlFor="lgp-enabled" className="cursor-pointer">Acesso público (sem login)</label> <span className="text-slate-500 font-mono text-xs">{pubSel.size} roteador(es)</span></div>
                <div className="text-[11px] text-slate-400">Endereço para passar a quem quiser consultar: <a href={pubUrl} target="_blank" rel="noreferrer" className="font-mono text-brand-soft hover:underline" data-testid="lg-pub-url">{pubUrl}</a>
                  {data.public_port ? <> — libere a porta TCP {data.public_port} no firewall.</> : null} Só aparecem os roteadores marcados como "público" na lista acima.</div>
                <div className="grid sm:grid-cols-2 gap-3">
                  <div><Label className="text-[11px] text-slate-400">Título da página</Label><Input value={pub.title} onChange={e => setPub({ ...pub, title: e.target.value })} placeholder="Looking Glass — AS263112" className="mt-1 h-8 bg-panel border-line text-xs" data-testid="lg-pub-title" /></div>
                  <div><Label className="text-[11px] text-slate-400">Contato exibido (opcional)</Label><Input value={pub.contact} onChange={e => setPub({ ...pub, contact: e.target.value })} placeholder="noc@empresa.com.br" className="mt-1 h-8 bg-panel border-line text-xs" /></div>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-300">
                  <span className="text-slate-400">Consultas:</span>
                  {[["ping", "Ping"], ["trace", "Traceroute"], ["route", "Rota BGP"], ["summary", "Vizinhos BGP"]].map(([k, l]) => (
                    <span key={k} className="flex items-center gap-1.5"><Checkbox id={`lgpq-${k}`} checked={pub.queries.includes(k)} onCheckedChange={(v) => setQ1(k, !!v)} data-testid={`lg-pub-q-${k}`} /><label htmlFor={`lgpq-${k}`} className="cursor-pointer">{l}</label></span>))}
                  <span className="ml-auto flex items-center gap-1.5 text-slate-400">por visitante: <Input value={pub.per_min} onChange={e => setPub({ ...pub, per_min: e.target.value.replace(/\D/g, "") })} className="h-7 w-12 bg-panel border-line font-mono text-xs px-1.5" />/min
                    <Input value={pub.per_day} onChange={e => setPub({ ...pub, per_day: e.target.value.replace(/\D/g, "") })} className="h-7 w-16 bg-panel border-line font-mono text-xs px-1.5" />/dia</span>
                </div>
                <div className="grid sm:grid-cols-2 gap-3">
                  <div><Label className="text-[11px] text-slate-400">Usuário só de leitura nos roteadores (recomendado)</Label><Input value={pub.username} onChange={e => setPub({ ...pub, username: e.target.value })} autoComplete="off" placeholder="vazio = usa o login do equipamento" className="mt-1 h-8 bg-panel border-line font-mono text-xs" data-testid="lg-pub-user" /></div>
                  <div><Label className="text-[11px] text-slate-400">Senha desse usuário</Label><Input type="password" value={pub.password} onChange={e => setPub({ ...pub, password: e.target.value })} autoComplete="new-password" placeholder={data.public?.has_password ? "•••••• (mantida)" : ""} className="mt-1 h-8 bg-panel border-line font-mono text-xs" /></div>
                </div>
                <div className="text-[11px] text-slate-500">O visitante só informa IP ou prefixo público (nada de rede interna nem nomes), não vê o comando nem o IP do roteador, e uma consulta roda por vez em cada roteador. "Vizinhos BGP" mostra todos os seus peers: deixe desmarcado se não quiser expor.</div>
                {data.public_log?.length > 0 && (
                  <details className="text-[11px]"><summary className="cursor-pointer text-slate-400">Últimas consultas públicas ({data.public_24h} nas últimas 24 h)</summary>
                    <div className="mt-1 max-h-32 overflow-y-auto font-mono text-slate-400 space-y-0.5">{data.public_log.map((l, i) => <div key={i}>{new Date(l.ts).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })} · {l.ip} · {l.router} · {l.query} {l.target}{l.ok ? "" : " · falhou"}</div>)}</div></details>
                )}
              </div>
            )}
            <div>
              <div className="flex items-center gap-2 mb-2 flex-wrap">
                <Label className="text-slate-300 mr-1">Comandos por fabricante</Label>
                {data.vendors.map(v => (
                  <button key={v} onClick={() => setVendor(v)} data-testid={`lg-vendor-${v}`}
                    className={`px-2 py-0.5 rounded text-[11px] border ${vendor === v ? "border-brand text-brand-soft bg-brand/10" : "border-line text-slate-400 hover:text-slate-200"}`}>
                    {VENDOR[v] || v}{Object.keys(cmds[v] || {}).some(k => cmds[v][k] && cmds[v][k] !== data.defaults[v][k]) ? " •" : ""}
                  </button>
                ))}
              </div>
              <div className="grid sm:grid-cols-2 gap-x-3 gap-y-2">
                {data.template_keys.map(k => {
                  const def = data.defaults[vendor][k]; const val = cmds[vendor]?.[k] ?? "";
                  return (
                    <div key={k}>
                      <div className="flex items-center text-[11px] text-slate-400 mb-0.5">{TPL_LABEL[k]}
                        {val && val !== def && <button className="ml-auto text-slate-500 hover:text-slate-200 flex items-center gap-1" onClick={() => setTpl(k, "")}><RotateCcw className="w-3 h-3" />padrão</button>}
                      </div>
                      <Input value={val} placeholder={def} onChange={e => setTpl(k, e.target.value)} className="h-8 bg-sunken border-line font-mono text-xs" data-testid={`lg-tpl-${k}`} />
                    </div>
                  );
                })}
              </div>
              <p className="text-[11px] text-slate-500 mt-1.5">Vazio = usa o padrão (em cinza). Marcadores: <code>{"{target}"}</code> destino como digitado, <code>{"{addr}"}</code> só o endereço, <code>{"{len}"}</code> tamanho do prefixo, <code>{"{addr_len}"}</code> "endereço tamanho".</p>
            </div>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={saving || !data} className="bg-brand hover:bg-brand-strong" data-testid="lg-manage-save">{saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function LookingGlass() {
  const [info, setInfo] = useState(null);
  const [picked, setPicked] = useState([]);
  const [query, setQuery] = useState("ping");
  const [target, setTarget] = useState("");
  const [v6, setV6] = useState(false);
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(0);
  const [manage, setManage] = useState(false);
  const seq = useRef(0);

  const load = async () => {
    try {
      const { data } = await api.get("/lg/routers"); setInfo(data);
      setPicked(p => { const ids = new Set(data.routers.map(r => r.id)); const keep = p.filter(id => ids.has(id)); return keep.length ? keep : data.routers.slice(0, 1).map(r => r.id); });
    } catch (e) { toast.error(formatApiError(e)); }
  };
  useEffect(() => { load(); }, []);

  const qdef = info?.queries.find(x => x.key === query);
  const toggle = (id) => setPicked(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  const run = async (e) => {
    e?.preventDefault();
    if (!picked.length) return toast.error("Escolha pelo menos um roteador");
    if (qdef?.needs_target && !target.trim()) return toast.error("Informe o destino");
    const routers = info.routers.filter(r => picked.includes(r.id));
    const batch = routers.map(r => ({ key: ++seq.current, router: r.name, label: qdef.label, target: qdef.needs_target ? target.trim() : (v6 ? "IPv6" : "IPv4"), loading: true }));
    setResults(rs => [...batch.slice().reverse(), ...rs].slice(0, 30));
    setBusy(b => b + routers.length);
    routers.forEach(async (r, i) => {
      let res;
      try { res = (await api.post("/lg/query", { device_id: r.id, query, target: target.trim(), v6 })).data; }
      catch (err) { res = { ok: false, error: formatApiError(err), output: "", command: "" }; }
      setResults(rs => rs.map(x => x.key === batch[i].key ? { ...x, ...res, loading: false } : x));
      setBusy(b => b - 1);
    });
  };
  const copy = (t) => navigator.clipboard?.writeText(t).then(() => toast.success("Copiado"), () => {});

  if (!info) return <div className="p-10 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>;
  return (
    <div className="p-4 md:p-8 max-w-6xl" data-testid="lg-page">
      <div className="flex items-start gap-3 mb-6">
        <div>
          <h1 className="text-2xl md:text-3xl font-semibold text-slate-100 flex items-center gap-2.5"><Telescope className="w-6 h-6 text-brand-soft" />Looking Glass</h1>
          <p className="text-sm text-slate-400 mt-1">Ping, traceroute e rota BGP vistos a partir dos roteadores da rede.</p>
        </div>
        {info.can_manage && <Button variant="outline" className="ml-auto border-line text-slate-200" onClick={() => setManage(true)} data-testid="lg-manage-btn"><Settings2 className="w-4 h-4 mr-2" />Configurar</Button>}
      </div>

      {!info.routers.length ? (
        <Card className="bg-surface border-line p-10 text-center" data-testid="lg-empty">
          <Telescope className="w-8 h-8 mx-auto text-slate-600 mb-3" />
          <div className="text-slate-200">Nenhum roteador liberado ainda</div>
          <div className="text-sm text-slate-500 mt-1">{info.can_manage ? "Clique em Configurar e marque os roteadores que podem ser consultados." : "Peça ao administrador para liberar os roteadores."}</div>
        </Card>
      ) : (
        <>
          <Card className="bg-surface border-line p-4 md:p-5 mb-5">
            <form onSubmit={run} className="space-y-4">
              <div>
                <Label className="text-slate-400 text-xs uppercase tracking-wider">Roteador <span className="normal-case tracking-normal text-slate-500">(pode marcar mais de um para comparar)</span></Label>
                <div className="flex flex-wrap gap-2 mt-2">
                  {info.routers.map(r => {
                    const on = picked.includes(r.id);
                    return (
                      <button type="button" key={r.id} onClick={() => toggle(r.id)} data-testid={`lg-router-${r.id}`} aria-pressed={on}
                        className={`px-3 py-1.5 rounded-md border text-sm flex items-center gap-2 ${on ? "border-brand bg-brand/10 text-slate-100" : "border-line text-slate-400 hover:text-slate-200"}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${r.status === "online" ? "bg-on" : r.status === "offline" ? "bg-red-500" : "bg-slate-600"}`} />
                        {r.name}<span className="text-[10px] font-mono text-slate-500">{VENDOR[r.device_type] || r.device_type}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <Label className="text-slate-400 text-xs uppercase tracking-wider">Consulta</Label>
                  <div className="flex mt-2 rounded-md border border-line overflow-hidden">
                    {info.queries.map(x => (
                      <button type="button" key={x.key} onClick={() => setQuery(x.key)} data-testid={`lg-query-${x.key}`}
                        className={`px-3 h-9 text-sm border-r border-line last:border-r-0 ${query === x.key ? "bg-brand text-white" : "bg-sunken text-slate-400 hover:text-slate-200"}`}>{x.label}</button>
                    ))}
                  </div>
                </div>
                {qdef?.needs_target ? (
                  <div className="flex-1 min-w-[220px]">
                    <Label className="text-slate-400 text-xs uppercase tracking-wider">Destino</Label>
                    <Input value={target} onChange={e => setTarget(e.target.value)} placeholder={HINT[query]} autoFocus spellCheck={false}
                      className="mt-2 h-9 bg-sunken border-line font-mono" data-testid="lg-target" />
                  </div>
                ) : (
                  <label className="flex items-center gap-2 h-9 text-sm text-slate-300 cursor-pointer"><Checkbox checked={v6} onCheckedChange={v => setV6(!!v)} data-testid="lg-v6" />IPv6</label>
                )}
                <Button type="submit" disabled={busy > 0} className="h-9 bg-brand hover:bg-brand-strong" data-testid="lg-run">
                  {busy > 0 ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}Consultar
                </Button>
              </div>
            </form>
          </Card>

          {results.length > 0 && <div className="flex items-center mb-2 text-xs text-slate-500"><span>Resultados</span>
            <button className="ml-auto flex items-center gap-1 hover:text-slate-200" onClick={() => setResults(rs => rs.filter(r => r.loading))} data-testid="lg-clear"><Trash2 className="w-3 h-3" />limpar</button></div>}
          <div className="space-y-3">
            {results.map(r => (
              <Card key={r.key} className="bg-surface border-line overflow-hidden" data-testid="lg-result">
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-line text-sm flex-wrap">
                  <span className={`w-2 h-2 rounded-full ${r.loading ? "bg-amber-400 animate-pulse" : r.ok ? "bg-on" : "bg-red-500"}`} />
                  <span className="text-slate-100 font-medium">{r.router}</span>
                  <span className="text-slate-500">· {r.label} {r.target}</span>
                  {r.command && <code className="text-[11px] text-slate-500 font-mono truncate max-w-[40ch]">{r.command}</code>}
                  <span className="ml-auto text-[11px] font-mono text-slate-500">{r.loading ? "consultando…" : r.seconds != null ? `${r.seconds} s` : ""}</span>
                  {!r.loading && r.output && <button className="text-slate-500 hover:text-slate-200" title="Copiar" onClick={() => copy(r.output)}><Copy className="w-3.5 h-3.5" /></button>}
                </div>
                {r.loading ? <div className="px-4 py-5 text-xs text-slate-500 font-mono">aguardando o roteador responder…</div>
                  : <>
                    {r.error && <div className="px-4 py-2 text-sm text-red-400" data-testid="lg-error">{r.error}</div>}
                    {r.output && <pre className="px-4 py-3 text-[12px] leading-relaxed font-mono text-slate-200 bg-sunken overflow-x-auto max-h-[60vh] whitespace-pre" data-testid="lg-output">{r.output}</pre>}
                    {!r.error && !r.output && <div className="px-4 py-3 text-xs text-slate-500 font-mono">sem saída</div>}
                  </>}
              </Card>
            ))}
          </div>
        </>
      )}
      <ManageDialog open={manage} onClose={() => setManage(false)} onSaved={load} />
    </div>
  );
}
