import React, { useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Search, Loader2, Eye, ChevronDown, ChevronRight, AlertTriangle, CornerDownRight } from "lucide-react";

const TYPES = ["", "huawei", "juniper", "datacom", "zte", "cisco", "mikrotik", "ubiquiti", "linux", "other"];
const MODES = [["word", "Palavra inteira"], ["text", "Trecho"], ["regex", "Regex"]];
const EXAMPLES = ["1302", "187.16.216.95", "vpn-instance", "mpls l2vc"];
const sel = "h-9 bg-sunken border border-line rounded-md px-2 text-sm text-slate-200";
const fmtAt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const ageDays = (iso) => iso ? (Date.now() - new Date(iso).getTime()) / 86400000 : 0;

function Hl({ text, spans }) {
  if (!spans?.length) return <>{text}</>;
  const out = []; let pos = 0;
  spans.forEach(([a, b], i) => {
    if (a > pos) out.push(<React.Fragment key={`t${i}`}>{text.slice(pos, a)}</React.Fragment>);
    out.push(<mark key={`m${i}`} className="bg-amber-400/25 text-amber-200 rounded-sm px-0.5">{text.slice(a, b)}</mark>);
    pos = b;
  });
  if (pos < text.length) out.push(<React.Fragment key="end">{text.slice(pos)}</React.Fragment>);
  return <>{out}</>;
}

function Line({ no, children, dim }) {
  return (
    <div className={`flex gap-3 ${dim ? "text-slate-500" : "text-slate-200"}`}>
      <span className="w-12 shrink-0 text-right text-slate-600 select-none">{no}</span>
      <span className="whitespace-pre overflow-hidden text-ellipsis">{children}</span>
    </div>
  );
}

function DeviceResult({ r, onView, open, onToggle }) {
  const old = ageDays(r.backup_at) > 2;
  return (
    <Card className="bg-surface border-line overflow-hidden" data-testid={`cs-dev-${r.device_id}`}>
      <div className="px-3 py-2 flex items-center gap-2 border-b border-line cursor-pointer hover:bg-slate-900/40" onClick={onToggle}>
        {open ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
        <div className="flex-1 min-w-0">
          <span className="text-sm text-slate-100 font-medium">{r.device_name}</span>
          <span className="ml-2 text-[11px] font-mono text-slate-500">{r.device_type} · {r.host}</span>
        </div>
        <span className={`text-[11px] font-mono ${old ? "text-amber-400" : "text-slate-500"}`} title="data do backup pesquisado">
          backup {fmtAt(r.backup_at)}{old ? " (antigo)" : ""}
        </span>
        <span className="text-[11px] font-mono text-brand-soft bg-brand-soft/10 rounded px-1.5">{r.count} linha{r.count > 1 ? "s" : ""}</span>
        <Button size="sm" variant="ghost" className="h-7 text-slate-300 hover:bg-slate-800" title="Abrir a config inteira"
                onClick={(e) => { e.stopPropagation(); onView(r.backup_id, r.matches[0]?.line_no); }} data-testid={`cs-view-${r.device_id}`}>
          <Eye className="w-3.5 h-3.5" />
        </Button>
      </div>
      {open && (
        <div className="divide-y divide-line/60">
          {r.matches.map((m) => {
            // o bloco que já aparece no contexto acima não precisa ser repetido no caminho
            const shown = new Set(m.before.map(([, x]) => x.trim()));
            const path = (m.path || []).filter((x, i, a) => !(i === a.length - 1 && shown.has(x)));
            return (
            <div key={m.line_no} className="px-3 py-2 text-[12px] font-mono cursor-pointer hover:bg-slate-900/30"
                 onClick={() => onView(r.backup_id, m.line_no)} data-testid="cs-match">
              {path.length > 0 && (
                <div className="flex items-center gap-1 text-[11px] text-slate-500 mb-0.5 pl-[3.75rem] truncate" data-testid="cs-path">
                  <CornerDownRight className="w-3 h-3 shrink-0" /> {path.join("  ›  ")}
                </div>
              )}
              {m.before.map(([n, t]) => <Line key={n} no={n} dim>{t}</Line>)}
              <Line no={m.line_no}><Hl text={m.text} spans={m.spans} /></Line>
              {m.after.map(([n, t]) => <Line key={n} no={n} dim>{t}</Line>)}
            </div>
            );
          })}
          {r.count > r.matches.length && (
            <div className="px-3 py-2 text-[11px] font-mono text-slate-500">… mais {r.count - r.matches.length} linha(s) neste equipamento — refine a busca</div>
          )}
        </div>
      )}
    </Card>
  );
}

export function ConfigSearch({ onView, initialQuery = "" }) {
  const [q, setQ] = useState(initialQuery);
  const [mode, setMode] = useState("word");
  const [cs, setCs] = useState(false);
  const [ctx, setCtx] = useState(1);
  const [dtype, setDtype] = useState("");
  const [dev, setDev] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [closed, setClosed] = useState({});
  const inputRef = useRef(null);

  const run = async (query = q) => {
    if (!query.trim()) return;
    setBusy(true); setErr("");
    try {
      const { data } = await api.get("/configs/search", { params: { q: query, mode, case: cs, context: ctx, device_type: dtype || undefined, device: dev || undefined } });
      setRes(data); setClosed(data.results.length > 8 ? Object.fromEntries(data.results.map(r => [r.device_id, true])) : {});
      const url = new URL(window.location.href); url.searchParams.set("tab", "search"); url.searchParams.set("q", query);
      window.history.replaceState(null, "", url.toString());
    } catch (e) { setErr(formatApiError(e)); setRes(null); }
    finally { setBusy(false); }
  };
  useEffect(() => { inputRef.current?.focus(); if (initialQuery) run(initialQuery); }, []); // eslint-disable-line

  return (
    <div className="space-y-3" data-testid="config-search">
      <Card className="bg-surface border-line p-3">
        <form className="flex flex-wrap gap-2 items-center" onSubmit={(e) => { e.preventDefault(); run(); }}>
          <div className="relative flex-1 min-w-[220px]">
            <Search className="w-4 h-4 text-slate-500 absolute left-2.5 top-2.5" />
            <Input ref={inputRef} value={q} onChange={e => setQ(e.target.value)} data-testid="cs-query"
                   placeholder="VLAN, IP, peer, VSI, nome de cliente… em todas as configs"
                   className="pl-8 bg-sunken border-line font-mono" />
          </div>
          <div className="flex rounded-md border border-line overflow-hidden" data-testid="cs-mode">
            {MODES.map(([v, l]) => (
              <button type="button" key={v} onClick={() => setMode(v)} data-testid={`cs-mode-${v}`}
                      className={`px-2.5 h-9 text-xs ${mode === v ? "bg-brand/20 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
            ))}
          </div>
          <select className={sel} value={dtype} onChange={e => setDtype(e.target.value)} data-testid="cs-type">
            {TYPES.map(t => <option key={t} value={t}>{t || "todos os fabricantes"}</option>)}
          </select>
          <Input value={dev} onChange={e => setDev(e.target.value)} placeholder="equipamento (opcional)" className="w-44 bg-sunken border-line h-9 text-sm" data-testid="cs-device" />
          <select className={sel} value={ctx} onChange={e => setCtx(Number(e.target.value))} title="linhas de contexto">
            {[0, 1, 2, 3, 5].map(n => <option key={n} value={n}>±{n} linhas</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
            <input type="checkbox" checked={cs} onChange={e => setCs(e.target.checked)} /> Aa
          </label>
          <Button type="submit" disabled={busy || !q.trim()} className="bg-brand hover:bg-brand-strong h-9" data-testid="cs-go">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : "Buscar"}
          </Button>
        </form>
        <div className="text-[11px] text-slate-500 mt-2">
          Procura no último backup de cada equipamento. <b className="text-slate-400">Palavra inteira</b>: <code>1302</code> não casa <code>13020</code> nem <code>Vlanif1302</code> (use <b className="text-slate-400">Trecho</b> para isso).
        </div>
      </Card>

      {err && <div className="text-sm text-red-400 font-mono" data-testid="cs-error">{err}</div>}

      {!res && !err && (
        <div className="flex flex-wrap gap-2 items-center text-xs text-slate-500">
          Exemplos:
          {EXAMPLES.map(x => (
            <button key={x} onClick={() => { setQ(x); run(x); }} className="font-mono px-2 py-1 rounded border border-line text-slate-300 hover:bg-slate-800/60">{x}</button>
          ))}
        </div>
      )}

      {res && (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-mono text-slate-400" data-testid="cs-summary">
            <span><b className="text-slate-100">{res.devices_matched}</b> de {res.devices_searched} equipamento(s) · <b className="text-slate-100">{res.lines_matched}</b> linha(s) · {res.elapsed_ms} ms</span>
            {res.truncated && <span className="text-amber-400 flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> resultado cortado — refine a busca</span>}
            {res.results.length > 1 && (
              <button className="text-brand-soft hover:underline" onClick={() => setClosed(Object.keys(closed).length ? {} : Object.fromEntries(res.results.map(r => [r.device_id, true])))}>
                {Object.keys(closed).length ? "expandir todos" : "recolher todos"}
              </button>
            )}
            {res.without_backup?.length > 0 && (
              <span className="text-slate-500" title={res.without_backup.join(", ")}>{res.without_backup.length} equipamento(s) sem backup não foram pesquisados</span>
            )}
          </div>
          {res.results.length === 0 && <div className="text-sm text-slate-500 font-mono p-4">Nada encontrado.</div>}
          <div className="space-y-2">
            {res.results.map(r => (
              <DeviceResult key={r.device_id} r={r} onView={onView} open={!closed[r.device_id]}
                            onToggle={() => setClosed(c => { const n = { ...c }; if (n[r.device_id]) delete n[r.device_id]; else n[r.device_id] = true; return n; })} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
