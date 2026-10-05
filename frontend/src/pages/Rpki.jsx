import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { FileBadge, Loader2, Plus, Trash2, Copy, Download, RefreshCw, Check, AlertTriangle, Link2 } from "lucide-react";

const DOT = { ok: "bg-on", warn: "bg-amber-400", bad: "bg-red-500", info: "bg-slate-500" };
const TXT = { ok: "text-on", warn: "text-amber-300", bad: "text-red-400", info: "text-slate-400" };
const caState = (c) => c.error ? ["bad", "erro"] : !c.repo ? ["warn", "falta o servidor de publicação"] : !c.parents?.length ? ["warn", "falta vincular ao Registro.br"]
  : c.parents.some(p => p.ok === false) || c.repo_ok === false ? ["bad", "sem sincronizar"] : !(c.ipv4?.length || c.ipv6?.length) ? ["warn", "aguardando os recursos do Registro.br"] : ["ok", "ativa"];
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
const copy = (t) => navigator.clipboard?.writeText(t).then(() => toast.success("Copiado"), () => toast.error("Não consegui copiar — use Baixar"));
const download = (name, text) => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "application/xml" })); a.download = name; a.click(); URL.revokeObjectURL(a.href); };

function XmlStep({ n, done, title, help, reqName, reqXml, placeholder, onSend, testid, extra }) {
  const [xml, setXml] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async () => { setBusy(true); try { await onSend(xml); setXml(""); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); } };
  const file = (e) => { const f = e.target.files?.[0]; if (f) f.text().then(setXml); e.target.value = ""; };
  return (
    <div className="border border-line rounded-md p-4 bg-sunken" data-testid={testid}>
      <div className="flex items-center gap-2.5 mb-1">
        <span className={`w-6 h-6 rounded-full text-xs flex items-center justify-center font-mono ${done ? "bg-on/15 text-on" : "bg-panel border border-line text-slate-300"}`}>{done ? <Check className="w-3.5 h-3.5" /> : n}</span>
        <span className="text-slate-100 font-medium">{title}</span>{done && <span className="text-xs text-on">feito</span>}
      </div>
      <p className="text-xs text-slate-400 mb-3 ml-8">{help}</p>
      <div className="ml-8 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-slate-400">a) Leve este pedido:</span>
          <Button size="sm" variant="outline" className="h-7 border-line text-slate-200" onClick={() => copy(reqXml)} data-testid={`${testid}-copy`}><Copy className="w-3.5 h-3.5 mr-1.5" />Copiar XML</Button>
          <Button size="sm" variant="outline" className="h-7 border-line text-slate-200" onClick={() => download(reqName, reqXml)}><Download className="w-3.5 h-3.5 mr-1.5" />Baixar {reqName}</Button>
        </div>
        <div>
          <div className="flex items-center gap-2 mb-1.5 flex-wrap"><span className="text-xs text-slate-400">b) Cole aqui a resposta{done ? " (só se precisar refazer)" : ""}:</span>
            <label className="text-xs text-brand-soft cursor-pointer hover:underline">ou escolha o arquivo<input type="file" accept=".xml,text/xml,application/xml" className="hidden" onChange={file} /></label>{extra}</div>
          <Textarea value={xml} onChange={e => setXml(e.target.value)} placeholder={placeholder} rows={3} spellCheck={false} className="bg-panel border-line font-mono text-[11px]" data-testid={`${testid}-xml`} />
          <Button size="sm" className="mt-2 bg-brand hover:bg-brand-strong" disabled={busy || !xml.trim()} onClick={send} data-testid={`${testid}-send`}>{busy && <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />}Enviar resposta</Button>
        </div>
      </div>
    </div>
  );
}

function ConfirmRoas({ change, handle, onClose, onDone }) {
  const [checked, setChecked] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setChecked(null);
    if (!change) return;
    if (!change.added.length) return setChecked([]);
    api.post(`/rpki/cas/${handle}/roas/check`, change).then(({ data }) => setChecked(data.added)).catch(e => { toast.error(formatApiError(e)); onClose(); });
  }, [change]);
  const save = async () => {
    setBusy(true);
    try { await api.post(`/rpki/cas/${handle}/roas`, change); toast.success("ROAs atualizados — a publicação leva alguns minutos"); onDone(); onClose(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open={!!change} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-xl" data-testid="rpki-confirm">
        <DialogHeader><DialogTitle>Confirmar alteração de ROAs — {handle}</DialogTitle></DialogHeader>
        {!checked || !change ? <div className="py-8 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : (
          <div className="space-y-2 text-sm">
            {checked.map((r, i) => (
              <div key={i} className="border border-line rounded-md px-3 py-2 bg-sunken">
                <div className="font-mono text-on">+ AS{r.asn} · {r.prefix} · máx /{r.max_length}</div>
                {r.warnings.map((w, j) => <div key={j} className="text-xs text-amber-300 flex gap-1.5 mt-1" data-testid="rpki-warning"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}</div>)}
              </div>
            ))}
            {change.removed.map((r, i) => (
              <div key={i} className="border border-line rounded-md px-3 py-2 bg-sunken">
                <div className="font-mono text-red-400">− AS{r.asn} · {r.prefix} · máx /{r.max_length}</div>
                <div className="text-xs text-slate-400 mt-1">Sem outro ROA cobrindo, os anúncios deste prefixo voltam a ficar "sem ROA" (não inválidos).</div>
              </div>
            ))}
            <p className="text-xs text-slate-400 pt-1">Um ROA com ASN ou tamanho errado faz as operadoras <b className="text-slate-200">descartarem</b> o anúncio. Confira com o que o roteador anuncia hoje.</p>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy || !checked} className="bg-brand hover:bg-brand-strong" data-testid="rpki-confirm-save">{busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Publicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CaDetail({ handle, canManage, onChanged }) {
  const [d, setD] = useState(null);
  const [form, setForm] = useState({ asn: "", prefix: "", max_length: "", comment: "" });
  const [change, setChange] = useState(null);
  const [parent, setParent] = useState("nicbr");
  const [del, setDel] = useState(null);
  const [setup, setSetup] = useState(false);
  const load = async () => { try { const { data } = await api.get(`/rpki/cas/${handle}`); setD(data); setForm(f => ({ ...f, asn: f.asn || (data.asns[0] || "").replace(/^AS/, "") })); } catch (e) { toast.error(formatApiError(e)); } };
  useEffect(() => { setD(null); setSetup(false); setForm({ asn: "", prefix: "", max_length: "", comment: "" }); load(); }, [handle]);
  const reload = () => { load(); onChanged(); };
  const noRoa = useMemo(() => (d?.announcements || []).filter(a => a.level !== "ok"), [d]);
  if (!d) return <Card className="bg-surface border-line p-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></Card>;
  const linked = d.repo && d.parents.length > 0;
  const add = (e) => { e.preventDefault(); if (!form.asn || !form.prefix.trim()) return toast.error("Informe o ASN e o prefixo"); setChange({ added: [{ ...form, prefix: form.prefix.trim() }], removed: [] }); };
  const removeCa = async () => { try { await api.delete(`/rpki/cas/${handle}?confirm=${encodeURIComponent(del)}`); toast.success("CA removida"); setDel(null); onChanged(true); } catch (e) { toast.error(formatApiError(e)); } };

  return (
    <div className="space-y-4" data-testid="rpki-detail">
      <Card className="bg-surface border-line p-4 md:p-5">
        <div className="flex items-start gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="text-lg text-slate-100 font-medium">{d.label || d.handle} <span className="text-xs font-mono text-slate-500 ml-1">{d.handle}</span></div>
            <div className="text-xs text-slate-400 mt-1 font-mono break-words" data-testid="rpki-resources">
              {d.asns.length || d.ipv4.length || d.ipv6.length ? [...d.asns, ...d.ipv4, ...d.ipv6].join("  ·  ") : "sem recursos certificados ainda"}
            </div>
          </div>
          <div className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={load} title="Atualizar"><RefreshCw className="w-3.5 h-3.5" /></Button>
            {canManage && linked && <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={() => setSetup(s => !s)} data-testid="rpki-toggle-setup"><Link2 className="w-3.5 h-3.5 mr-1.5" />Vínculos</Button>}
            {canManage && <Button size="sm" variant="outline" className="border-line text-red-400" onClick={() => setDel("")} data-testid="rpki-delete-ca"><Trash2 className="w-3.5 h-3.5" /></Button>}
          </div>
        </div>
        {linked && <div className="flex flex-wrap gap-x-6 gap-y-1 mt-3 text-xs">
          {d.parents.map(p => <span key={p.name} className={p.ok === false ? "text-red-400" : "text-slate-400"}><span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${p.ok === false ? "bg-red-500" : p.ok ? "bg-on" : "bg-slate-500"}`} />pai {p.name}: {p.ok === false ? p.error || "erro" : `sincronizado ${when(p.last_success)}`}</span>)}
          <span className={d.repo_ok === false ? "text-red-400" : "text-slate-400"}><span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${d.repo_ok === false ? "bg-red-500" : d.repo_ok ? "bg-on" : "bg-slate-500"}`} />publicação: {d.repo_ok === false ? d.repo_error || "erro" : when(d.repo_last)}</span>
        </div>}
      </Card>

      {(!linked || setup) && (canManage ? (
        <Card className="bg-surface border-line p-4 md:p-5 space-y-3" data-testid="rpki-setup">
          <div className="text-sm text-slate-200">Vincular esta CA <span className="text-slate-500">— feito uma vez por ASN, com o login do titular no Registro.br</span></div>
          <XmlStep n={1} done={d.repo} testid="rpki-step-repo" title="Servidor de publicação" reqName={`${d.handle}-publisher_request.xml`} reqXml={d.publisher_request}
            help="Onde os ROAs ficam publicados. No portal do Registro.br (RPKI), informe o pedido de publicação e traga a resposta (repository response)."
            placeholder="<repository_response …>" onSend={async (xml) => { await api.post(`/rpki/cas/${handle}/repo`, { xml }); toast.success("Servidor de publicação configurado"); reload(); }} />
          <XmlStep n={2} done={d.parents.length > 0} testid="rpki-step-parent" title="Pai (Registro.br)" reqName={`${d.handle}-child_request.xml`} reqXml={d.child_request}
            help="Quem certifica os recursos do ASN. No portal do Registro.br, informe o pedido de filho (child request) e traga a resposta (parent response)."
            placeholder="<parent_response …>" onSend={async (xml) => { await api.post(`/rpki/cas/${handle}/parents`, { xml, name: parent }); toast.success("Vinculado — os recursos chegam em alguns minutos"); reload(); }}
            extra={<span className="ml-auto flex items-center gap-1.5 text-xs text-slate-500">nome do pai<Input value={parent} onChange={e => setParent(e.target.value)} className="h-6 w-24 bg-panel border-line font-mono text-[11px] px-2" /></span>} />
        </Card>
      ) : <Card className="bg-surface border-line p-6 text-sm text-slate-400">Esta CA ainda não foi vinculada ao Registro.br. Peça ao administrador.</Card>)}

      {linked && (
        <Card className="bg-surface border-line overflow-hidden">
          <div className="px-4 md:px-5 py-3 border-b border-line text-sm text-slate-200">ROAs <span className="text-slate-500 font-mono text-xs">({d.roas.length})</span></div>
          <form onSubmit={add} className="px-4 md:px-5 py-3 border-b border-line flex flex-wrap items-end gap-2 bg-sunken">
            <div><Label className="text-[11px] text-slate-400">ASN de origem</Label><Input value={form.asn} onChange={e => setForm({ ...form, asn: e.target.value.replace(/[^0-9]/g, "") })} placeholder="65010" className="h-8 w-28 bg-panel border-line font-mono mt-1" data-testid="roa-asn" /></div>
            <div className="flex-1 min-w-[180px]"><Label className="text-[11px] text-slate-400">Prefixo</Label><Input value={form.prefix} onChange={e => setForm({ ...form, prefix: e.target.value })} placeholder="200.160.0.0/20" spellCheck={false} className="h-8 bg-panel border-line font-mono mt-1" data-testid="roa-prefix" /></div>
            <div><Label className="text-[11px] text-slate-400">Tam. máx.</Label><Input value={form.max_length} onChange={e => setForm({ ...form, max_length: e.target.value.replace(/[^0-9]/g, "") })} placeholder="= prefixo" className="h-8 w-24 bg-panel border-line font-mono mt-1" data-testid="roa-max" /></div>
            <div className="flex-1 min-w-[140px]"><Label className="text-[11px] text-slate-400">Comentário</Label><Input value={form.comment} onChange={e => setForm({ ...form, comment: e.target.value })} maxLength={120} className="h-8 bg-panel border-line mt-1" /></div>
            <Button type="submit" size="sm" className="h-8 bg-brand hover:bg-brand-strong" data-testid="roa-add"><Plus className="w-4 h-4 mr-1" />Adicionar</Button>
          </form>
          {!d.roas.length ? <div className="px-5 py-8 text-center text-sm text-slate-500">Nenhum ROA ainda. Crie um para cada prefixo que este ASN anuncia.</div> : (
            <div className="overflow-x-auto"><table className="w-full text-sm">
              <thead><tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-line"><th className="px-4 md:px-5 py-2">ASN</th><th className="px-3 py-2">Prefixo</th><th className="px-3 py-2">Máx.</th><th className="px-3 py-2">Situação</th><th className="px-3 py-2">Comentário</th><th /></tr></thead>
              <tbody className="divide-y divide-line/60">
                {d.roas.map(r => (
                  <tr key={`${r.asn}|${r.prefix}|${r.max_length}`} data-testid="roa-row">
                    <td className="px-4 md:px-5 py-2 font-mono text-slate-200">AS{r.asn}</td><td className="px-3 py-2 font-mono text-slate-100">{r.prefix}</td><td className="px-3 py-2 font-mono text-slate-300">/{r.max_length}</td>
                    <td className="px-3 py-2 text-xs">{r.status ? <span className={TXT[r.status.level]}><span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${DOT[r.status.level]}`} />{r.status.text}</span> : <span className="text-slate-500">—</span>}
                      {r.warnings?.length > 0 && <AlertTriangle className="w-3.5 h-3.5 inline ml-2 text-amber-300" title={r.warnings.join("\n")} />}</td>
                    <td className="px-3 py-2 text-xs text-slate-400">{r.comment}</td>
                    <td className="px-3 py-2 text-right"><button className="text-slate-500 hover:text-red-400" title="Remover" onClick={() => setChange({ added: [], removed: [r] })} data-testid="roa-remove"><Trash2 className="w-3.5 h-3.5" /></button></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </Card>
      )}

      {linked && (
        <Card className="bg-surface border-line overflow-hidden" data-testid="rpki-announcements">
          <div className="px-4 md:px-5 py-3 border-b border-line text-sm text-slate-200">Anúncios vistos na internet que pedem atenção <span className="text-slate-500 font-mono text-xs">({noRoa.length})</span></div>
          {!d.analysis_available ? <div className="px-5 py-5 text-xs text-slate-500">O Krill ainda não tem a tabela global carregada (ele baixa do RIPE RIS de tempos em tempos). Tente de novo em alguns minutos.</div>
            : !noRoa.length ? <div className="px-5 py-5 text-xs text-slate-500">Nada pendente: todo anúncio visto dos prefixos desta CA está coberto por ROA.</div> : (
              <div className="divide-y divide-line/60">
                {noRoa.map(a => (
                  <div key={`${a.asn}|${a.prefix}`} className="px-4 md:px-5 py-2 flex items-center gap-3 text-sm flex-wrap" data-testid="rpki-ann">
                    <span className={`w-1.5 h-1.5 rounded-full ${DOT[a.level]}`} /><span className="font-mono text-slate-100">{a.prefix}</span><span className="font-mono text-slate-400">AS{a.asn}</span>
                    <span className={`text-xs ${TXT[a.level]}`}>{a.text}</span>
                    <Button size="sm" variant="outline" className="ml-auto h-7 border-line text-slate-200" data-testid="rpki-ann-roa"
                      onClick={() => setChange({ added: [{ asn: a.asn, prefix: a.prefix, max_length: "", comment: "" }], removed: [] })}><Plus className="w-3.5 h-3.5 mr-1" />Criar ROA</Button>
                  </div>
                ))}
              </div>
            )}
          <div className="px-4 md:px-5 py-2 border-t border-line text-[11px] text-slate-500">Fonte: coletores públicos do RIPE RIS, vistos pelo Krill. Antes de criar um ROA por aqui, confirme que o anúncio é mesmo seu — criar ROA para um anúncio indevido legitima o sequestro.</div>
        </Card>
      )}

      {d.log.length > 0 && (
        <Card className="bg-surface border-line p-4 md:p-5">
          <div className="text-sm text-slate-200 mb-2">Histórico</div>
          <div className="space-y-1 text-xs font-mono text-slate-400 max-h-48 overflow-y-auto">
            {d.log.map(l => <div key={l.id}><span className="text-slate-500">{new Date(l.at).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })}</span> {l.user_email} · {l.action.replace(/_/g, " ")} {l.detail}</div>)}
          </div>
        </Card>
      )}

      <ConfirmRoas change={change} handle={handle} onClose={() => setChange(null)} onDone={() => { setForm(f => ({ ...f, prefix: "", max_length: "", comment: "" })); load(); }} />
      <Dialog open={del !== null} onOpenChange={(v) => !v && setDel(null)}>
        <DialogContent className="bg-surface border-line text-slate-100 max-w-md">
          <DialogHeader><DialogTitle>Remover a CA {handle}?</DialogTitle></DialogHeader>
          <p className="text-sm text-slate-400">As chaves e todos os ROAs desta CA são apagados e deixam de ser publicados. Para voltar, é preciso refazer o vínculo no Registro.br. Digite <b className="text-slate-200 font-mono">{handle}</b> para confirmar.</p>
          <Input value={del || ""} onChange={e => setDel(e.target.value)} className="bg-sunken border-line font-mono" data-testid="rpki-delete-confirm" />
          <DialogFooter><Button variant="ghost" onClick={() => setDel(null)}>Cancelar</Button><Button onClick={removeCa} disabled={del !== handle} className="bg-red-600 hover:bg-red-700" data-testid="rpki-delete-ok">Remover</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function Rpki() {
  const [st, setSt] = useState(null);
  const [sel, setSel] = useState(null);
  const [creating, setCreating] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async (reset) => {
    try { const { data } = await api.get("/rpki/status"); setSt(data); setSel(s => (!reset && s && data.cas.some(c => c.handle === s)) ? s : (data.cas[0]?.handle || null)); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  useEffect(() => { load(); }, []);
  const create = async (e) => {
    e.preventDefault(); setBusy(true);
    try { const { data } = await api.post("/rpki/cas", creating); toast.success("CA criada — agora vincule ao Registro.br"); setCreating(null); await load(); setSel(data.handle); }
    catch (err) { toast.error(formatApiError(err)); } finally { setBusy(false); }
  };
  if (!st) return <div className="p-10 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>;
  return (
    <div className="p-4 md:p-8 max-w-6xl" data-testid="rpki-page">
      <div className="flex items-start gap-3 mb-6">
        <div>
          <h1 className="text-2xl md:text-3xl font-semibold text-slate-100 flex items-center gap-2.5"><FileBadge className="w-6 h-6 text-brand-soft" />RPKI</h1>
          <p className="text-sm text-slate-400 mt-1">Certificação e ROAs dos ASNs gerenciados{st.online && st.version ? <span className="text-slate-500"> · Krill {st.version}</span> : ""}.</p>
        </div>
        {st.online && st.can_manage && <Button className="ml-auto bg-brand hover:bg-brand-strong" onClick={() => setCreating({ handle: "", label: "" })} data-testid="rpki-new-ca"><Plus className="w-4 h-4 mr-2" />Nova CA</Button>}
      </div>

      {!st.configured ? (
        <Card className="bg-surface border-line p-8" data-testid="rpki-off">
          <div className="text-slate-100 mb-2">O RPKI ainda não está ativado neste servidor</div>
          <p className="text-sm text-slate-400 mb-3">O BastiON usa o Krill (NLnet Labs) num container ao lado para guardar as chaves e assinar os ROAs. Para ativar, rode uma vez no servidor:</p>
          <pre className="bg-sunken border border-line rounded-md px-4 py-3 text-sm font-mono text-slate-200 overflow-x-auto">sudo bash /opt/bastion/deploy/rpki-setup.sh</pre>
        </Card>
      ) : !st.online ? (
        <Card className="bg-surface border-line p-8" data-testid="rpki-down">
          <div className="text-red-400 mb-2 flex items-center gap-2"><AlertTriangle className="w-4 h-4" />Krill fora do ar</div>
          <p className="text-sm text-slate-400 mb-3">{st.error}</p>
          <Button variant="outline" className="border-line text-slate-200" onClick={() => load()}><RefreshCw className="w-4 h-4 mr-2" />Tentar de novo</Button>
        </Card>
      ) : !st.cas.length ? (
        <Card className="bg-surface border-line p-10 text-center" data-testid="rpki-empty">
          <FileBadge className="w-8 h-8 mx-auto text-slate-600 mb-3" />
          <div className="text-slate-200">Nenhuma CA ainda</div>
          <div className="text-sm text-slate-500 mt-1">{st.can_manage ? "Crie uma CA para cada rede/ASN que você gerencia (ex.: LINK10)." : "Peça ao administrador para criar a CA do ASN."}</div>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-5">
            {st.cas.map(c => { const [lv, tx] = caState(c); return (
              <button key={c.handle} onClick={() => setSel(c.handle)} data-testid={`rpki-ca-${c.handle}`}
                className={`text-left px-3.5 py-2.5 rounded-md border min-w-[170px] ${sel === c.handle ? "border-brand bg-brand/10" : "border-line bg-surface hover:border-line2"}`}>
                <div className="text-sm text-slate-100 flex items-center gap-2"><span className={`w-1.5 h-1.5 rounded-full ${DOT[lv]}`} />{c.label || c.handle}</div>
                <div className="text-[11px] font-mono text-slate-500 mt-0.5">{c.asns?.join(", ") || c.handle} · {tx}</div>
              </button>); })}
          </div>
          {sel && <CaDetail key={sel} handle={sel} canManage={st.can_manage} onChanged={(reset) => load(reset)} />}
        </>
      )}

      <Dialog open={!!creating} onOpenChange={(v) => !v && setCreating(null)}>
        <DialogContent className="bg-surface border-line text-slate-100 max-w-md" data-testid="rpki-new-dialog">
          <DialogHeader><DialogTitle>Nova CA</DialogTitle></DialogHeader>
          <form onSubmit={create} className="space-y-3">
            <div><Label className="text-slate-300">Identificador</Label>
              <Input value={creating?.handle || ""} onChange={e => setCreating({ ...creating, handle: e.target.value.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 32) })} placeholder="LINK10" autoFocus className="bg-sunken border-line font-mono mt-1" data-testid="rpki-new-handle" />
              <p className="text-[11px] text-slate-500 mt-1">Letras, números, - e _. Não dá para trocar depois.</p></div>
            <div><Label className="text-slate-300">Nome (opcional)</Label><Input value={creating?.label || ""} onChange={e => setCreating({ ...creating, label: e.target.value })} placeholder="Link10 Telecom — AS65010" className="bg-sunken border-line mt-1" /></div>
            <DialogFooter><Button type="button" variant="ghost" onClick={() => setCreating(null)}>Cancelar</Button>
              <Button type="submit" disabled={busy || !creating?.handle} className="bg-brand hover:bg-brand-strong" data-testid="rpki-new-save">{busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Criar</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
