import React, { useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus, Pencil, Trash2, Search, Loader2, Sparkles, Layers, MapPin } from "lucide-react";
import { FlowView } from "@/components/flow/FlowView";
import { FlowIfaceSelect } from "@/components/flow/FlowIfaceSelect";
import { chip, EXTERNAL, fmtRate, RANGES, ROLE_LABEL, selCls, inputCls } from "@/components/flow/flowlib";

function GroupDialog({ initial, onClose, onSaved }) {
  const [g, setG] = useState({ name: initial?.name || "", asns: (initial?.asns || []).join(", "), prefixes: (initial?.prefixes || []).join("\n") });
  const [busy, setBusy] = useState(false);
  const [asnInfo, setAsnInfo] = useState(null);
  const save = async () => {
    setBusy(true);
    try {
      const body = { name: g.name, asns: g.asns.split(/[\s,;]+/).filter(Boolean), prefixes: g.prefixes.split(/[\s,;]+/).filter(Boolean) };
      const { data } = initial?.id ? await api.put(`/flow/groups/${initial.id}`, body) : await api.post("/flow/groups", body);
      toast.success(`Conteúdo "${data.name}" salvo`);
      onSaved(data);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const showPrefixes = async () => {
    const first = g.asns.split(/[\s,;]+/).filter(Boolean)[0];
    if (!first) return;
    try { setAsnInfo((await api.get("/flow/asn/lookup", { params: { q: first } })).data); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl" data-testid="group-dialog">
        <DialogHeader><DialogTitle>{initial?.id ? "Editar conteúdo" : "Novo conteúdo"}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div><Label>Nome</Label><Input value={g.name} onChange={e => setG({ ...g, name: e.target.value })} placeholder="ex.: Google / YouTube" className={inputCls} data-testid="group-name" /></div>
          <div>
            <Label>ASNs</Label>
            <div className="flex gap-2">
              <Input value={g.asns} onChange={e => setG({ ...g, asns: e.target.value })} placeholder="ex.: 15169, 36040" className={`${inputCls} font-mono`} data-testid="group-asns" />
              <Button variant="ghost" size="sm" onClick={showPrefixes} className="h-9 text-xs text-slate-300 hover:bg-slate-800" title="Ver os prefixos do 1º ASN na base IP→ASN">Prefixos do AS</Button>
            </div>
            <div className="text-[11px] text-slate-500 mt-1">Casa o tráfego cujo AS de origem ou destino é um destes (AS do próprio flow, ou da base IP→ASN).</div>
          </div>
          <div>
            <Label>Blocos (um por linha)</Label>
            <Textarea value={g.prefixes} onChange={e => setG({ ...g, prefixes: e.target.value })} rows={5} placeholder={"ex.: bloco do cache dentro da sua rede (GGC, OCA, FNA)\n177.10.200.0/26\n2804:1:ca::/48"} className={`${inputCls} font-mono text-xs`} data-testid="group-prefixes" />
            <div className="text-[11px] text-slate-500 mt-1">Use para caches instalados no seu provedor (usam IPs seus, então o AS não pega) ou para acompanhar blocos específicos.</div>
          </div>
          {asnInfo && (
            <div className="text-[11px] font-mono border border-line rounded p-2 max-h-40 overflow-y-auto" data-testid="group-asninfo">
              <div className="text-slate-300 mb-1">AS{asnInfo.asn} {asnInfo.name} — {asnInfo.v4_total} blocos IPv4, {asnInfo.v6_total} IPv6 (o ASN já cobre todos; só copie se quiser acompanhar bloco a bloco)</div>
              <div className="text-slate-500 break-all">{[...(asnInfo.v4 || []).slice(0, 40), ...(asnInfo.v6 || []).slice(0, 20)].join("  ")}</div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy || !g.name.trim()} className="bg-brand hover:bg-brand-strong" data-testid="group-save">{busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** "IP → AS e bloco" / "AS → nome" / busca por nome, para montar conteúdos. */
function Lookup({ onCreate }) {
  const [q, setQ] = useState("");
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try { setR((await api.get("/flow/asn/lookup", { params: { q: q.trim() } })).data); }
    catch (e) { setR({ error: formatApiError(e) }); }
    finally { setBusy(false); }
  };
  return (
    <div className="border border-line rounded p-2" data-testid="flow-lookup">
      <div className="text-[10px] text-slate-500 mb-1">Descobrir conteúdo</div>
      <div className="flex gap-1">
        <Input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === "Enter" && go()} placeholder="IP, AS ou nome (ex.: 142.250.79.46)" className={`${inputCls} h-8 text-xs font-mono`} data-testid="lookup-q" />
        <Button size="sm" variant="ghost" onClick={go} className="h-8 px-2 hover:bg-slate-800" data-testid="lookup-go">{busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}</Button>
      </div>
      {r?.error && <div className="text-[11px] text-amber-300 mt-1">{r.error}</div>}
      {r?.kind === "ip" && (
        <div className="text-[11px] font-mono mt-1.5" data-testid="lookup-res">
          {r.asn ? <>
            <div className="text-slate-200">{r.ip} → <b>AS{r.asn}</b> {r.name}</div>
            <div className="text-slate-500">bloco: {r.prefixes.join(", ")}</div>
            <button className="text-brand-pale hover:underline mt-0.5" onClick={() => onCreate({ name: r.name, asns: [r.asn], prefixes: [] })}>+ criar conteúdo com AS{r.asn}</button>
          </> : <div className="text-slate-400">{r.ip}: fora da base (IP privado, não anunciado ou seu).</div>}
        </div>
      )}
      {r?.kind === "asn" && (
        <div className="text-[11px] font-mono mt-1.5 text-slate-200" data-testid="lookup-res">AS{r.asn} {r.name || "(sem nome)"} · {r.v4_total} blocos v4 / {r.v6_total} v6
          <button className="block text-brand-pale hover:underline mt-0.5" onClick={() => onCreate({ name: r.name || `AS${r.asn}`, asns: [r.asn], prefixes: [] })}>+ criar conteúdo</button></div>
      )}
      {r?.kind === "search" && (
        <div className="mt-1.5 max-h-40 overflow-y-auto" data-testid="lookup-res">
          {r.results.length === 0 && <div className="text-[11px] text-slate-500">Nada encontrado.</div>}
          {r.results.map(x => (
            <button key={x.asn} onClick={() => onCreate({ name: x.name, asns: [x.asn], prefixes: [] })} className="w-full text-left text-[11px] font-mono px-1 py-0.5 rounded hover:bg-slate-800/60 text-slate-300 truncate">
              AS{x.asn} {x.name} <span className="text-slate-500">{x.cc}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Quanto do conteúdo entra por cada tipo de interface (PNI × trânsito × IX × CDN): números, sem cor (a cor é das interfaces no gráfico). */
function RoleSplit({ res }) {
  if (!res?.table?.length || res.group_by !== "interface") return null;
  const byRole = {};
  let tot = 0;
  res.table.forEach(r => {
    const role = r.role || "outro";
    byRole[role] = byRole[role] || { share: 0, avg: 0 };
    byRole[role].share += r.share; byRole[role].avg += r.avg; tot += r.avg;
  });
  const roles = Object.keys(ROLE_LABEL).filter(r => byRole[r]);
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3" data-testid="role-split">
      {roles.map(r => (
        <div key={r} className="border border-line rounded px-3 py-2">
          <div className="text-[10px] text-slate-500">{ROLE_LABEL[r]}</div>
          <div className="flex items-baseline gap-2">
            <div className="text-2xl font-heading font-bold text-slate-50 tabular-nums">{(byRole[r].share * 100).toFixed(0)}%</div>
            <div className="text-xs font-mono text-slate-400">{fmtRate(byRole[r].avg, res.unit)} média</div>
          </div>
        </div>
      ))}
      {tot === 0 && <div className="text-xs text-slate-500">Nada deste conteúdo nas interfaces escolhidas.</div>}
    </div>
  );
}

/** Aba Conteúdos: por onde entra cada conteúdo (Google pelo PNI ou pelo trânsito?). */
export function FlowContents({ ifaces, groups, presets, reload, ifColors, groupColors }) {
  const [sel, setSel] = useState(groups[0]?.id || "");
  const [dlg, setDlg] = useState(null);
  const [showPresets, setShowPresets] = useState(false);
  const extIds = useMemo(() => ifaces.filter(i => EXTERNAL.includes(i.role)).map(i => i.id), [ifaces]);
  const [ifs, setIfs] = useState(null);
  const [minutes, setMinutes] = useState(1440);
  const [dir, setDir] = useState("in");
  const [mode, setMode] = useState("where");
  const [src, setSrc] = useState("exact");     // exact = conta da coleta · asn = histórico pelo detalhe de 5 min
  const [res, setRes] = useState(null);
  const cur = groups.find(g => g.id === sel) || groups[0];
  const chosen = ifs ?? (extIds.length ? extIds : []);

  const del = async (g) => {
    if (!window.confirm(`Apagar o conteúdo "${g.name}"?`)) return;
    try { await api.delete(`/flow/groups/${g.id}`); await reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const byAsn = src === "asn" && cur?.asns?.length;
  const query = mode === "where"
    ? (cur && ifaces.length ? { interfaces: chosen, minutes, direction: dir, group_by: "interface", top: 30, unit: "bps",
                                filter: byAsn ? { dim: dir === "in" ? "sas" : "das", values: cur.asns.map(String) } : { dim: "group", values: [cur.id] } } : null)
    : (groups.length && ifaces.length ? { interfaces: chosen, minutes, direction: dir, group_by: "group", filter: { dim: null, values: [] }, top: 30, unit: "bps" } : null);

  return (
    <div className="flex flex-col lg:flex-row gap-4" data-testid="flow-contents">
      <div className="lg:w-72 shrink-0 space-y-2">
        <div className="flex gap-1.5">
          <Button size="sm" onClick={() => setDlg({})} className="flex-1 h-8 bg-brand hover:bg-brand-strong" data-testid="group-new"><Plus className="w-4 h-4 mr-1" /> Novo conteúdo</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowPresets(!showPresets)} className="h-8 text-xs text-slate-300 hover:bg-slate-800" data-testid="group-presets"><Sparkles className="w-3.5 h-3.5 mr-1" />Sugestões</Button>
        </div>
        {showPresets && (
          <div className="border border-line rounded p-1.5 max-h-60 overflow-y-auto" data-testid="presets-list">
            {presets.map(p => (
              <button key={p.name} onClick={() => { setShowPresets(false); setDlg({ name: p.name, asns: p.asns, prefixes: [] }); }}
                      className="w-full text-left px-2 py-1 rounded hover:bg-slate-800/60 text-xs">
                <div className="text-slate-200">{p.name}</div><div className="text-[10px] font-mono text-slate-500 truncate">AS {p.asns.join(", ")}</div>
              </button>
            ))}
            <div className="text-[10px] text-slate-500 px-2 pt-1">Confira os ASNs e acrescente os blocos dos caches que você tem na rede.</div>
          </div>
        )}
        <div className="border border-line rounded divide-y divide-line" data-testid="group-list">
          {groups.length === 0 && <div className="p-3 text-xs text-slate-500">Nenhum conteúdo ainda. Use <b>Sugestões</b> ou a busca abaixo.</div>}
          {groups.map(g => (
            <div key={g.id} className={`px-2.5 py-2 cursor-pointer ${cur?.id === g.id && mode === "where" ? "bg-brand/15" : "hover:bg-slate-800/40"}`} onClick={() => { setSel(g.id); setMode("where"); }} data-testid={`group-${g.id}`}>
              <div className="flex items-center gap-1">
                <span className="text-sm text-slate-100 truncate">{g.name}</span>
                <button onClick={(e) => { e.stopPropagation(); setDlg(g); }} className="ml-auto text-slate-500 hover:text-slate-200" title="Editar"><Pencil className="w-3.5 h-3.5" /></button>
                <button onClick={(e) => { e.stopPropagation(); del(g); }} className="text-slate-500 hover:text-red-400" title="Apagar"><Trash2 className="w-3.5 h-3.5" /></button>
              </div>
              <div className="text-[10px] font-mono text-slate-500 truncate">{g.asns.length ? `AS ${g.asns.join(", ")}` : ""}{g.asns.length && g.prefixes.length ? " · " : ""}{g.prefixes.length ? `${g.prefixes.length} bloco(s)` : ""}</div>
            </div>
          ))}
        </div>
        <Lookup onCreate={(p) => setDlg(p)} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <div className="flex gap-1">
            <button onClick={() => setMode("where")} className={chip(mode === "where")} data-testid="cont-mode-where"><MapPin className="w-3.5 h-3.5 inline mr-1" />Por onde entra</button>
            <button onClick={() => setMode("all")} className={chip(mode === "all")} data-testid="cont-mode-all"><Layers className="w-3.5 h-3.5 inline mr-1" />Todos os conteúdos</button>
          </div>
          <div className="flex gap-1">{RANGES.map(([m, l]) => <button key={m} onClick={() => setMinutes(m)} className={chip(minutes === m)}>{l}</button>)}</div>
          <FlowIfaceSelect ifaces={ifaces} value={chosen} onChange={setIfs} testid="cont-ifsel" />
          <select value={dir} onChange={e => setDir(e.target.value)} className={selCls}><option value="in">Entrada ↓</option><option value="out">Saída ↑</option></select>
        </div>
        {mode === "where" && cur && (
          <>
            <div className="mb-2 flex flex-wrap items-end gap-3">
              <div>
                <div className="text-lg font-heading font-bold text-slate-100">{cur.name}</div>
                <div className="text-[11px] text-slate-500">{byAsn ? `Pelo AS ${dir === "in" ? "de origem" : "de destino"} nos detalhes de 5 min (inclui o período antes de o conteúdo existir; blocos não entram).`
                  : "Quanto deste conteúdo passa por cada interface escolhida (conta exata, minuto a minuto)."}</div>
              </div>
              {cur.asns?.length > 0 && (
                <div className="flex gap-1 ml-auto">
                  <button onClick={() => setSrc("exact")} className={chip(src === "exact")} data-testid="cont-src-exact">Exato (ASN + blocos)</button>
                  <button onClick={() => setSrc("asn")} className={chip(src === "asn")} data-testid="cont-src-asn">Por AS (histórico)</button>
                </div>
              )}
            </div>
            {!byAsn && res?.counting_since && (
              <div className="text-[11px] text-amber-300 mb-2" data-testid="counting-since">Contando desde {new Date(res.counting_since).toLocaleString("pt-BR")} (criação ou alteração do conteúdo): a conta exata acontece na coleta.
                {cur.asns?.length > 0 && <> Para ver antes disso use <button className="underline" onClick={() => setSrc("asn")}>Por AS (histórico)</button>.</>}</div>
            )}
            <RoleSplit res={res} />
          </>
        )}
        {query ? <FlowView query={query} onData={setRes} fixed={mode === "where" ? ifColors : groupColors} shareLabel={mode === "where" ? "Participação entre os locais" : "Participação no tráfego total"} testid="cont-view"
                           emptyText={mode === "where" ? "Nada deste conteúdo nas interfaces escolhidas no período." : "Sem dados no período."} />
               : <div className="text-sm text-slate-400 p-6 border border-dashed border-line rounded">{!ifaces.length ? "Cadastre as interfaces monitoradas primeiro (aba Interfaces)." : "Crie um conteúdo à esquerda (ex.: Google pelas Sugestões)."}</div>}
      </div>
      {dlg && <GroupDialog initial={dlg} onClose={() => setDlg(null)} onSaved={async (g) => { setDlg(null); await reload(); setSel(g.id); setMode("where"); }} />}
    </div>
  );
}
