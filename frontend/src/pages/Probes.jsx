import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Activity, Plus, Pencil, Trash2, Loader2, FlaskConical, Sparkles, Settings2, X } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { SEV as INC_SEV } from "@/pages/Incidents";

const TYPES = [["ping", "Ping"], ["dns", "DNS"], ["http", "HTTP(S)"], ["tcp", "Porta TCP"]];
const TYPE_LABEL = Object.fromEntries(TYPES);
const PERIODS = [[3, "3 h"], [24, "24 h"], [168, "7 dias"], [720, "30 dias"]];
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const ST = {
  ok: ["bg-emerald-400", "text-emerald-300", "OK"], warn: ["bg-amber-400", "text-amber-300", "piorando"],
  down: ["bg-red-500", "text-red-300", "alarme"], skipped: ["bg-slate-500", "text-slate-400", "sem medir"], unknown: ["bg-slate-600", "text-slate-500", "—"],
};
const fmtMs = (v) => (v == null ? "—" : v >= 100 ? `${Math.round(v)} ms` : `${Number(v).toFixed(1)} ms`);
const lossColor = (l) => (l == null ? "#64748b" : l <= 0 ? "#34d399" : l <= 5 ? "#38bdf8" : l <= 20 ? "#fbbf24" : l < 100 ? "#fb923c" : "#ef4444");

function Spark({ data }) {
  const v = (data || []).slice(-40);
  const nums = v.filter(x => x != null);
  if (!nums.length) return <svg width="80" height="20" />;
  const max = Math.max(...nums) * 1.1 || 1;
  const w = 80, h = 20, step = w / Math.max(1, v.length - 1);
  let d = "", pen = false;
  v.forEach((x, i) => {
    if (x == null) { pen = false; return; }
    const y = h - (x / max) * (h - 2) - 1;
    d += `${pen ? "L" : "M"}${(i * step).toFixed(1)},${y.toFixed(1)} `; pen = true;
  });
  return (
    <svg width={w} height={h} className="block">
      {v.map((x, i) => x == null && <rect key={i} x={i * step - 1} y={0} width={2} height={h} fill="#ef4444" opacity="0.7" />)}
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.3" className="text-brand-soft" />
    </svg>
  );
}

// ------------------------------------------------------------------ gráfico estilo SmokePing
export function SmokeChart({ points, kind, isPing }) {
  const box = useRef(null);
  const [w, setW] = useState(800);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    const ro = new ResizeObserver(() => setW(box.current?.clientWidth || 800));
    if (box.current) ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  const H = 240, PL = 52, PR = 10, PT = 10, PB = 26;
  const pts = useMemo(() => (points || []).map(p => ({ ...p, t: new Date(p.at).getTime() })), [points]);
  if (!pts.length) return <div ref={box} className="h-[240px] flex items-center justify-center text-sm text-slate-500">Sem medições no período.</div>;
  const t0 = pts[0].t, t1 = Math.max(pts[pts.length - 1].t, t0 + 60000);
  const highs = pts.map(p => (kind === "raw" && p.samples?.length ? [...p.samples].sort((a, b) => a - b)[Math.floor(p.samples.length * 0.9)] : p.max ?? p.ms)).filter(x => x != null).sort((a, b) => a - b);
  const ymax = Math.max(1, (highs[Math.floor(highs.length * 0.97)] ?? highs[highs.length - 1] ?? 1) * 1.25);
  const X = (t) => PL + ((t - t0) / (t1 - t0)) * (w - PL - PR);
  const Y = (v) => PT + (1 - Math.min(v, ymax) / ymax) * (H - PT - PB);
  const bw = Math.max(1.5, (w - PL - PR) / Math.max(1, pts.length) * 0.9);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => ymax * f);
  const span = t1 - t0;
  const nT = w < 500 ? 3 : 5;
  const xt = Array.from({ length: nT + 1 }, (_, i) => t0 + (span * i) / nT);
  const fmtT = (t) => new Date(t).toLocaleString("pt-BR", span > 2 * 86400000 ? { day: "2-digit", month: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
  const onMove = (e) => {
    const r = box.current.getBoundingClientRect();
    const x = e.clientX - r.left;
    let best = null, bd = 1e9;
    for (const p of pts) { const d = Math.abs(X(p.t) - x); if (d < bd) { bd = d; best = p; } }
    setHover(best && bd < 30 ? best : null);
  };
  return (
    <div ref={box} className="relative select-none" onMouseMove={onMove} onMouseLeave={() => setHover(null)} data-testid="smoke-chart">
      <svg width={w} height={H} className="block">
        {ticks.map((v, i) => <g key={i}><line x1={PL} x2={w - PR} y1={Y(v)} y2={Y(v)} stroke="currentColor" className="text-line" strokeWidth="1" />
          <text x={PL - 6} y={Y(v) + 3} textAnchor="end" fontSize="10" className="fill-slate-500">{v >= 100 ? Math.round(v) : v.toFixed(v < 10 ? 1 : 0)} ms</text></g>)}
        {xt.map((t, i) => <text key={i} x={X(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === nT ? "end" : "middle"} fontSize="10" className="fill-slate-500">{fmtT(t)}</text>)}
        {pts.map((p, i) => {
          const x = X(p.t) - bw / 2;
          if (!p.ok && p.ms == null) return <rect key={i} x={x} y={PT} width={bw} height={H - PT - PB} fill="#ef4444" opacity="0.18" />;
          const els = [];
          if (kind === "raw" && p.samples?.length > 1) {
            const s = [...p.samples].sort((a, b) => a - b), n = s.length;
            for (let k = 0; k < Math.floor(n / 2); k++) {
              const lo = s[k], hi = s[n - 1 - k];
              els.push(<rect key={k} x={x} y={Y(hi)} width={bw} height={Math.max(1, Y(lo) - Y(hi))} fill="currentColor" className="text-slate-400" opacity={0.08 + 0.06 * k} />);
            }
          } else if (p.min != null && p.max != null) {
            els.push(<rect key="b" x={x} y={Y(p.max)} width={bw} height={Math.max(1, Y(p.min) - Y(p.max))} fill="currentColor" className="text-slate-400" opacity="0.22" />);
          }
          if (p.ms != null) els.push(<rect key="m" x={x} y={Y(p.ms) - 1} width={bw} height={2.5} fill={isPing ? lossColor(p.loss) : p.ok === false ? "#ef4444" : "#34d399"} />);
          if (p.bad && p.ms != null) els.push(<rect key="e" x={x} y={PT} width={bw} height={3} fill="#fb923c" />);
          return <g key={i}>{els}</g>;
        })}
        {hover && <line x1={X(hover.t)} x2={X(hover.t)} y1={PT} y2={H - PB} stroke="currentColor" className="text-slate-500" strokeDasharray="3 3" />}
      </svg>
      {hover && (
        <div className="absolute top-2 pointer-events-none rounded-md border border-line bg-panel/95 px-2.5 py-1.5 text-[11px] text-slate-200 shadow-lg"
             style={{ left: Math.min(X(hover.t) + 10, w - 190) }}>
          <div className="text-slate-400">{new Date(hover.t).toLocaleString("pt-BR")}</div>
          {hover.ms != null ? <div>mediana <b>{fmtMs(hover.ms)}</b>{hover.min != null && <span className="text-slate-400"> · {fmtMs(hover.min)} – {fmtMs(hover.max)}</span>}</div> : <div className="text-red-300">{hover.error || "falhou"}</div>}
          {isPing && hover.loss != null && <div style={{ color: lossColor(hover.loss) }}>perda {hover.loss}%</div>}
          {hover.avail != null && <div className="text-slate-400">dentro do limite {hover.avail}% dos ciclos</div>}
          {hover.code && <div className="text-slate-400">{hover.code}</div>}
          {hover.jitter != null && isPing && <div className="text-slate-400">jitter {fmtMs(hover.jitter)}</div>}
        </div>
      )}
      {isPing && <div className="flex flex-wrap gap-3 text-[10px] text-slate-500 mt-1 pl-12">
        {[[0, "0%"], [5, "≤5%"], [20, "≤20%"], [50, ">20%"], [100, "100%"]].map(([l, t]) => <span key={t} className="flex items-center gap-1"><span className="w-3 h-1.5 rounded-sm" style={{ background: lossColor(l) }} />perda {t}</span>)}
        <span className="flex items-center gap-1"><span className="w-3 h-2 rounded-sm bg-slate-400/40" />fumaça = espalhamento dos pings</span>
      </div>}
    </div>
  );
}

function Detail({ probe, source, sources, onClose }) {
  const [hours, setHours] = useState(24);
  const [src, setSrc] = useState(source);
  const [data, setData] = useState(null);
  const top = useRef(null);
  useEffect(() => { top.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" }); }, []);
  useEffect(() => {
    setData(null);
    api.get(`/probes/${probe.id}/series`, { params: { source: src, hours } }).then(r => setData(r.data)).catch(e => toast.error(formatApiError(e)));
  }, [probe.id, src, hours]);
  const pts = data?.points || [];
  const ok = pts.filter(p => p.ms != null);
  const stats = useMemo(() => {
    if (!ok.length) return null;
    const meds = ok.map(p => p.ms).sort((a, b) => a - b);
    const loss = probe.type === "ping" ? pts.filter(p => p.loss != null).reduce((a, p) => a + p.loss, 0) / Math.max(1, pts.filter(p => p.loss != null).length) : null;
    return { med: meds[Math.floor(meds.length / 2)], p95: meds[Math.floor(meds.length * 0.95)], loss, fails: pts.filter(p => p.ok === false || p.bad).length };
  }, [pts, ok, probe.type]);
  return (
    <Card ref={top} className="bg-surface border-line p-4 mb-4 scroll-mt-4" data-testid="probe-detail">
      <div className="flex flex-wrap items-start gap-2 mb-3">
        <div className="flex-1 min-w-[12rem]">
          <div className="text-slate-100 font-medium">{probe.name}</div>
          <div className="text-xs text-slate-400 font-mono">{TYPE_LABEL[probe.type]} · {probe.target}{probe.type === "dns" ? ` · ${probe.query} ${probe.qtype}` : ""}{probe.type === "tcp" ? `:${probe.port}` : ""}</div>
        </div>
        <select value={src} onChange={e => setSrc(e.target.value)} className={`${sel} h-8 text-xs`} data-testid="detail-source">
          {(probe.sources || []).map(s => <option key={s} value={s}>de {sources[s] || s}</option>)}
        </select>
        <div className="flex gap-1 p-0.5 bg-sunken border border-line rounded-md text-xs">
          {PERIODS.map(([h, l]) => <button key={h} onClick={() => setHours(h)} className={`px-2 h-7 rounded ${hours === h ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`} data-testid={`period-${h}`}>{l}</button>)}
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} className="h-8 text-slate-400"><X className="w-4 h-4" /></Button>
      </div>
      {stats && <div className="flex flex-wrap gap-4 text-xs text-slate-400 mb-2">
        <span>mediana <b className="text-slate-200">{fmtMs(stats.med)}</b></span><span>p95 <b className="text-slate-200">{fmtMs(stats.p95)}</b></span>
        {stats.loss != null && <span>perda média <b style={{ color: lossColor(stats.loss) }}>{stats.loss.toFixed(1)}%</b></span>}
        <span>{data.kind === "raw" ? "ciclos" : "horas"} com problema <b className={stats.fails ? "text-orange-300" : "text-slate-200"}>{stats.fails}</b> de {pts.length}</span>
      </div>}
      {!data ? <div className="h-[240px] flex items-center justify-center"><Loader2 className="w-5 h-5 animate-spin text-slate-500" /></div>
        : <SmokeChart points={pts} kind={data.kind} isPing={probe.type === "ping"} />}
    </Card>
  );
}

// ------------------------------------------------------------------ cadastro
const EMPTY = { name: "", type: "ping", target: "", group: "", enabled: true, sources: ["server"], interval_s: 60, count: 10, timeout_s: 5,
  max_loss: 20, max_ms: 0, fail_count: 3, recover_count: 2, severity: "high", alert: true, query: "google.com", qtype: "A", port: 0,
  expect_status: 0, contains: "", insecure: false };

function ProbeDialog({ probe, srcList, onClose, onSaved }) {
  const [f, setF] = useState({ ...EMPTY, ...(probe || {}) });
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState(null);
  const [testSrc, setTestSrc] = useState("server");
  const set = (k, v) => setF(p => ({ ...p, [k]: v }));
  const num = (k) => ({ type: "number", value: f[k], onChange: e => set(k, e.target.value) });
  const body = () => {
    const b = { ...f };
    ["interval_s", "count", "timeout_s", "max_loss", "max_ms", "fail_count", "recover_count", "port", "expect_status"].forEach(k => { b[k] = Number(b[k]) || 0; });
    return b;
  };
  const save = async () => {
    setBusy(true);
    try {
      if (probe?.id) await api.put(`/probes/${probe.id}`, body()); else await api.post("/probes", body());
      toast.success("Sonda salva"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const runTest = async () => {
    setBusy(true); setTest(null);
    try { setTest((await api.post("/probes/test", { ...body(), source: testSrc })).data); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const toggleSrc = (id) => set("sources", f.sources.includes(id) ? f.sources.filter(x => x !== id) : [...f.sources, id]);
  const lbl = "text-xs text-slate-400 mb-1 block";
  const ph = { ping: "203.0.113.1 ou host.exemplo.net", dns: "IP do servidor DNS (ex.: 192.0.2.53)", http: "https://www.exemplo.net/", tcp: "IP ou nome" }[f.type];
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl w-[96vw] max-h-[92vh] overflow-y-auto" data-testid="probe-dialog">
        <DialogHeader><DialogTitle>{probe?.id ? "Editar sonda" : "Nova sonda"}</DialogTitle></DialogHeader>
        <div className="grid sm:grid-cols-3 gap-3">
          <div><label className={lbl}>Tipo</label>
            <select value={f.type} onChange={e => set("type", e.target.value)} className={`${sel} w-full`} data-testid="probe-type">{TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <div className="sm:col-span-2"><label className={lbl}>{f.type === "dns" ? "Servidor DNS" : f.type === "http" ? "URL" : "Alvo"}</label>
            <Input value={f.target} onChange={e => set("target", e.target.value)} placeholder={ph} className="bg-sunken border-line font-mono" data-testid="probe-target" /></div>
          {f.type === "dns" && <>
            <div className="sm:col-span-2"><label className={lbl}>Nome consultado</label><Input value={f.query} onChange={e => set("query", e.target.value)} className="bg-sunken border-line font-mono" /></div>
            <div><label className={lbl}>Registro</label><select value={f.qtype} onChange={e => set("qtype", e.target.value)} className={`${sel} w-full`}>{["A", "AAAA", "MX", "NS", "TXT", "SOA"].map(x => <option key={x}>{x}</option>)}</select></div>
          </>}
          {f.type === "tcp" && <div><label className={lbl}>Porta</label><Input {...num("port")} className="bg-sunken border-line" data-testid="probe-port" /></div>}
          {f.type === "http" && <>
            <div><label className={lbl}>Status esperado (0 = 2xx/3xx)</label><Input {...num("expect_status")} className="bg-sunken border-line" /></div>
            <div><label className={lbl}>Resposta deve conter (opcional)</label><Input value={f.contains} onChange={e => set("contains", e.target.value)} className="bg-sunken border-line" /></div>
            <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={f.insecure} onCheckedChange={v => set("insecure", v)} /> Ignorar certificado</label>
          </>}
          <div><label className={lbl}>Nome</label><Input value={f.name} onChange={e => set("name", e.target.value)} placeholder="automático" className="bg-sunken border-line" data-testid="probe-name" /></div>
          <div><label className={lbl}>Grupo</label><Input value={f.group} onChange={e => set("group", e.target.value)} placeholder="DNS recursivos, Trânsito…" className="bg-sunken border-line" /></div>
          <div><label className={lbl}>A cada (segundos)</label><Input {...num("interval_s")} className="bg-sunken border-line" /></div>
          {f.type === "ping" && <div><label className={lbl}>Pings por ciclo</label><Input {...num("count")} className="bg-sunken border-line" /></div>}
          <div><label className={lbl}>Timeout (s)</label><Input {...num("timeout_s")} className="bg-sunken border-line" /></div>
        </div>
        <div className="mt-1">
          <label className={lbl}>Medir a partir de</label>
          <div className="flex flex-wrap gap-2" data-testid="probe-sources">
            {srcList.map(s => (
              <label key={s.id} className={`flex items-center gap-1.5 text-sm px-2 h-8 rounded border cursor-pointer ${f.sources.includes(s.id) ? "border-brand bg-brand/10 text-slate-100" : "border-line text-slate-400"}`}>
                <input type="checkbox" className="accent-brand" checked={f.sources.includes(s.id)} onChange={() => toggleSrc(s.id)} />
                {s.name}{s.status === "offline" && <span className="text-[10px] text-red-300">offline</span>}
              </label>
            ))}
          </div>
          <p className="text-[11px] text-slate-500 mt-1">Do jump, a sonda roda pela conexão SSH que o BastiON já mantém com ele (não gera um login por medição). Precisa de bash, ping e — para DNS/HTTP — dig/curl ou python3.</p>
        </div>
        <div className="border-t border-line pt-3 mt-1 grid sm:grid-cols-4 gap-3">
          {f.type === "ping" && <div><label className={lbl}>Perda máxima (%)</label><Input {...num("max_loss")} className="bg-sunken border-line" /></div>}
          <div><label className={lbl}>{f.type === "ping" ? "Latência máxima (ms, 0 = sem)" : "Tempo máximo (ms, 0 = sem)"}</label><Input {...num("max_ms")} className="bg-sunken border-line" data-testid="probe-maxms" /></div>
          <div><label className={lbl}>Alarma após N ciclos ruins</label><Input {...num("fail_count")} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Normaliza após N bons</label><Input {...num("recover_count")} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Severidade</label>
            <select value={f.severity} onChange={e => set("severity", e.target.value)} className={`${sel} w-full`}>{["critical", "high", "medium", "low", "info"].map(k => <option key={k} value={k}>{INC_SEV[k].label}</option>)}</select></div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={f.alert} onCheckedChange={v => set("alert", v)} /> Abrir incidente</label>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={f.enabled} onCheckedChange={v => set("enabled", v)} /> Ativa</label>
        </div>
        <div className="border-t border-line pt-3 flex flex-wrap items-center gap-2">
          <select value={testSrc} onChange={e => setTestSrc(e.target.value)} className={`${sel} h-8 text-xs`}>{srcList.map(s => <option key={s.id} value={s.id}>testar de {s.name}</option>)}</select>
          <Button size="sm" variant="ghost" onClick={runTest} disabled={busy || !f.target} className="text-slate-200" data-testid="probe-test">{busy ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <FlaskConical className="w-4 h-4 mr-1" />} Testar agora</Button>
          {test && <span className={`text-xs ${test.skipped ? "text-slate-400" : test.bad ? "text-orange-300" : "text-emerald-300"}`} data-testid="probe-test-result">
            {test.ok ? `${fmtMs(test.ms)}${test.loss != null ? ` · perda ${test.loss}%` : ""}${test.code ? ` · ${test.code}` : ""}` : (test.error || "falhou")}{test.bad && test.reason ? ` — ${test.reason}` : test.ok ? " — dentro do limite" : ""}
          </span>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={save} disabled={busy || !f.target || !f.sources.length} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="probe-save">Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PresetsDialog({ srcList, onClose, onSaved }) {
  const [dns, setDns] = useState("");
  const [pub, setPub] = useState(true);
  const [srcs, setSrcs] = useState(["server"]);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.post("/probes/presets", { dns_servers: dns.split(/[\s,;]+/).filter(Boolean), public: pub, sources: srcs });
      toast.success(`${r.data.added} sonda(s) criada(s)`); onSaved();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg" data-testid="presets-dialog">
        <DialogHeader><DialogTitle>Sondas prontas</DialogTitle></DialogHeader>
        <label className="text-xs text-slate-400">IPs dos DNS recursivos da sua rede (DNS + ping para cada um, severidade crítica)</label>
        <Input value={dns} onChange={e => setDns(e.target.value)} placeholder="192.0.2.53, 192.0.2.54" className="bg-sunken border-line font-mono" data-testid="presets-dns" />
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={pub} onCheckedChange={setPub} /> Também DNS públicos (8.8.8.8, 1.1.1.1), trânsito por ping e HTTP</label>
        <div className="text-xs text-slate-400">Medir a partir de</div>
        <div className="flex flex-wrap gap-2">
          {srcList.map(s => <label key={s.id} className="flex items-center gap-1.5 text-sm text-slate-300"><input type="checkbox" className="accent-brand" checked={srcs.includes(s.id)}
            onChange={() => setSrcs(x => x.includes(s.id) ? x.filter(y => y !== s.id) : [...x, s.id])} /> {s.name}</label>)}
        </div>
        <DialogFooter><Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={go} disabled={busy || !srcs.length} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="presets-go">Criar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SettingsDialog({ onClose }) {
  const [c, setC] = useState(null);
  useEffect(() => { api.get("/probes-settings").then(r => setC(r.data)).catch(e => toast.error(formatApiError(e))); }, []);
  const save = async () => {
    try { await api.put("/probes-settings", { ...c, retention_days: Number(c.retention_days) }); toast.success("Salvo"); onClose(); } catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-md">
        <DialogHeader><DialogTitle>Configuração das sondas</DialogTitle></DialogHeader>
        {!c ? <Loader2 className="w-4 h-4 animate-spin" /> : <>
          <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={!!c.enabled} onCheckedChange={v => setC({ ...c, enabled: v })} /> Medindo</label>
          <label className="text-xs text-slate-400">Guardar cada medição por (dias) — o resumo por hora fica 400 dias (base do relatório de SLA)</label>
          <Input type="number" value={c.retention_days} onChange={e => setC({ ...c, retention_days: e.target.value })} className="bg-sunken border-line" />
        </>}
        <DialogFooter><Button onClick={save} disabled={!c} className="bg-brand hover:bg-brand-strong text-onbrand">Salvar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ------------------------------------------------------------------ página
export default function Probes() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const [data, setData] = useState(null);
  const [srcList, setSrcList] = useState([]);
  const [edit, setEdit] = useState(null);
  const [presets, setPresets] = useState(false);
  const [cfg, setCfg] = useState(false);
  const load = useCallback(() => api.get("/probes").then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => {
    load(); api.get("/probes-sources").then(r => setSrcList(r.data || [])).catch(() => {});
    const t = setInterval(() => { if (!document.hidden) load(); }, 20000);
    return () => clearInterval(t);
  }, [load]);
  const probes = data?.probes || [];
  const names = data?.sources || {};
  const groups = useMemo(() => {
    const m = new Map();
    for (const p of probes) { const g = p.group || "Outras"; if (!m.has(g)) m.set(g, []); m.get(g).push(p); }
    return [...m.entries()];
  }, [probes]);
  const used = useMemo(() => {
    const ids = new Set(probes.flatMap(p => p.sources || []));
    return ["server", ...[...ids].filter(x => x !== "server").sort((a, b) => (names[a] || "").localeCompare(names[b] || ""))].filter(x => ids.has(x));
  }, [probes, names]);
  const selP = probes.find(p => p.id === sp.get("p"));
  const del = async (p) => {
    if (!window.confirm(`Apagar a sonda "${p.name}" e o histórico dela?`)) return;
    try { await api.delete(`/probes/${p.id}`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const alarms = probes.reduce((a, p) => a + Object.values(p.status || {}).filter(s => s.state === "down").length, 0);
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="probes-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Activity className="w-6 h-6 text-brand-soft" /> Sondas ativas</h1>
          <p className="text-sm text-slate-400 mt-1">Latência, perda e disponibilidade medidas do servidor e de cada jump server — DNS recursivos, trânsito, sites e portas.
            {alarms > 0 && <span className="text-red-300"> {alarms} em alarme.</span>}</p>
        </div>
        {isAdmin && <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setCfg(true)} className="text-slate-300" title="Configuração"><Settings2 className="w-4 h-4" /></Button>
          <Button size="sm" variant="ghost" onClick={() => setPresets(true)} className="text-slate-300" data-testid="probes-presets"><Sparkles className="w-4 h-4 mr-1" /> Sondas prontas</Button>
          <Button size="sm" onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="probes-new"><Plus className="w-4 h-4 mr-1" /> Nova sonda</Button>
        </div>}
      </div>
      {selP && <Detail key={`${selP.id}-${sp.get("s")}`} probe={selP} source={sp.get("s") || selP.sources[0]} sources={names} onClose={() => setSp({})} />}
      {!data ? <div className="py-10 text-center text-slate-500"><Loader2 className="w-5 h-5 animate-spin inline" /></div> : probes.length === 0 ? (
        <Card className="bg-surface border-line p-8 text-center text-sm text-slate-400">Nenhuma sonda ainda.{isAdmin && <> Comece pelas <button className="text-brand-soft underline" onClick={() => setPresets(true)}>sondas prontas</button> (seus DNS recursivos, DNS públicos, trânsito e HTTP).</>}</Card>
      ) : groups.map(([g, list]) => (
        <Card key={g} className="bg-surface border-line mb-3 overflow-x-auto" data-testid={`probe-group-${g}`}>
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-500 bg-panel">
              <tr><th className="text-left px-3 py-2 min-w-[14rem]">{g}</th>
                {used.map(s => <th key={s} className="text-left px-3 py-2 whitespace-nowrap">{s === "server" ? "BastiON" : names[s] || "?"}</th>)}
                {isAdmin && <th />}</tr>
            </thead>
            <tbody className="divide-y divide-line">
              {list.map(p => (
                <tr key={p.id} className={p.enabled ? "" : "opacity-50"} data-testid={`probe-${p.id}`}>
                  <td className="px-3 py-2">
                    <div className="text-slate-100">{p.name}</div>
                    <div className="text-[11px] text-slate-500 font-mono">{TYPE_LABEL[p.type]} {p.target}{p.type === "tcp" ? `:${p.port}` : ""} · {p.interval_s}s</div>
                  </td>
                  {used.map(s => {
                    if (!(p.sources || []).includes(s)) return <td key={s} className="px-3 py-2 text-slate-700">·</td>;
                    const st = p.status?.[s] || {};
                    const [dot, txt, lab] = ST[st.state] || ST.unknown;
                    const on = selP?.id === p.id && (sp.get("s") || selP.sources[0]) === s;
                    return (
                      <td key={s} className="px-2 py-1.5">
                        <button onClick={() => setSp({ p: p.id, s })} title={st.reason || st.error || lab}
                                className={`w-full text-left rounded-md px-2 py-1 border ${on ? "border-brand bg-brand/10" : "border-transparent hover:border-line"}`} data-testid={`cell-${p.id}-${s}`}>
                          <div className="flex items-center gap-1.5 whitespace-nowrap">
                            <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} />
                            <span className={`text-xs font-mono ${st.state === "down" ? "text-red-300" : "text-slate-200"}`}>{st.ms != null ? fmtMs(st.ms) : st.state === "skipped" ? "sem medir" : st.state ? "falhou" : "—"}</span>
                            {p.type === "ping" && st.loss > 0 && <span className="text-[10px]" style={{ color: lossColor(st.loss) }}>{st.loss}%</span>}
                          </div>
                          <Spark data={st.spark} />
                          {(st.state === "down" || st.state === "skipped" || st.state === "warn") && <div className={`text-[10px] truncate max-w-[11rem] ${txt}`}>{st.reason || st.error}</div>}
                        </button>
                      </td>
                    );
                  })}
                  {isAdmin && <td className="px-2 py-2 text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => setEdit(p)} className="h-7 text-slate-300" title="Editar"><Pencil className="w-4 h-4" /></Button>
                    <Button size="sm" variant="ghost" onClick={() => del(p)} className="h-7 text-red-300" title="Apagar"><Trash2 className="w-4 h-4" /></Button>
                  </td>}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}
      {edit && <ProbeDialog probe={edit.id ? edit : null} srcList={srcList} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
      {presets && <PresetsDialog srcList={srcList} onClose={() => setPresets(false)} onSaved={() => { setPresets(false); load(); }} />}
      {cfg && <SettingsDialog onClose={() => setCfg(false)} />}
    </div>
  );
}
