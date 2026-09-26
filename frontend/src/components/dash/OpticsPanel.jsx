import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { LineChart } from "@/components/charts/LineChart";
import { STATUS } from "@/lib/netfmt";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { CheckCircle2, AlertTriangle, XCircle, Loader2, FlaskConical } from "lucide-react";

// 4 lanes = slots 1-4 da paleta categórica (modo escuro), validados no fundo #0B111C.
// Par amarelo/verde fica na faixa de atenção p/ daltonismo -> sempre com legenda + tabela de valores (codificação secundária).
export const LANE_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500"];
const NO_LIGHT = -40;
const fmtDbm = (v) => (v === null || v === undefined ? "—" : v <= NO_LIGHT ? "sem luz" : `${v.toFixed(2)} dBm`);

function rxStatus(v, warn, crit) {
  if (v === null || v === undefined) return null;
  if (v <= NO_LIGHT) return "crit";
  if (crit != null && v < crit) return "crit";
  if (warn != null && v < warn) return "warn";
  return warn != null || crit != null ? "ok" : null;
}
function StatusTag({ s }) {
  if (s === "crit") return <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: STATUS.critical }}><XCircle className="w-3 h-3" /> crítico</span>;
  if (s === "warn") return <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: STATUS.warning }}><AlertTriangle className="w-3 h-3" /> atenção</span>;
  if (s === "ok") return <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: STATUS.good }}><CheckCircle2 className="w-3 h-3" /> ok</span>;
  return null;
}

export function OpticsTestDialog({ open, onClose, deviceId, ifIndex, ifName, onDone }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setRes(null); setBusy(true);
    api.post(`/devices/${deviceId}/optics-test`, { if_index: ifIndex, if_name: ifName })
      .then(r => { setRes(r.data); onDone?.(); }).catch(e => setRes({ ok: false, error: formatApiError(e) })).finally(() => setBusy(false));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-3xl" data-testid="optics-test-dialog">
        <DialogHeader><DialogTitle>Teste de leitura óptica · {ifName}</DialogTitle></DialogHeader>
        {busy && <div className="text-sm text-slate-400 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Conectando e lendo…</div>}
        {res && <>
          <div className={`text-sm ${res.ok ? "text-emerald-400" : "text-red-400"}`}>
            {res.ok ? `✔ ${res.lanes.length} lane(s) lida(s) com "${res.command}"` : `✘ ${res.error || `não consegui interpretar a saída de "${res.command || "?"}"`}`}
          </div>
          {res.ok && <div className="text-xs font-mono text-slate-300">{res.lanes.map(l => `lane ${l.lane}: RX ${fmtDbm(l.rx)} · TX ${fmtDbm(l.tx)}`).join("   |   ")}</div>}
          {!res.ok && res.commands_tried && <div className="text-xs text-slate-400">Comandos tentados: <code className="text-slate-200">{res.commands_tried.join("  →  ") || "nenhum"}</code>. Ajuste em <b>Dashboards → Configurações da óptica</b> para o seu firmware.</div>}
          {res.raw && <pre className="bg-[#05070A] border border-[#1E293B] rounded p-2 text-[11px] font-mono text-slate-400 max-h-80 overflow-auto whitespace-pre-wrap">{res.raw}</pre>}
        </>}
      </DialogContent>
    </Dialog>
  );
}

/** Potência óptica por lane: valores atuais (tabela) + histórico de RX/TX. */
export function OpticsPanel({ deviceId, ifIndex, ifName, minutes = 1440, warn, crit, compact = false, refreshKey, chartHeight }) {
  const [data, setData] = useState(null);
  const [which, setWhich] = useState("rx");
  const [testOpen, setTestOpen] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => api.get("/optics/series", { params: { device_id: deviceId, if_index: ifIndex, minutes } })
      .then(r => alive && setData(r.data)).catch(() => {});
    load(); const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [deviceId, ifIndex, minutes, refreshKey, tick]);

  if (!data) return <div className="text-xs text-slate-500 font-mono py-2">Carregando óptica…</div>;
  const lanes = data.live?.lanes || [];
  const n = Math.max(data.lanes || 0, lanes.length);
  const series = Array.from({ length: n }, (_, i) => ({ key: `l${i}`, label: n > 1 ? `Lane ${i}` : which.toUpperCase(), color: LANE_COLORS[i % 4] }));
  const pts = data.points.map(p => Object.fromEntries([["t", p.t], ...Array.from({ length: n }, (_, i) => {
    const v = (p[which] || [])[i]; return [`l${i}`, v === null || v === undefined || v <= NO_LIGHT ? null : v];
  })]));
  const refs = which === "rx" ? [
    ...(warn != null ? [{ y: warn, label: `atenção ${warn} dBm`, color: STATUS.warning }] : []),
    ...(crit != null ? [{ y: crit, label: `crítico ${crit} dBm`, color: STATUS.critical }] : []),
  ] : [];

  return (
    <div data-testid={`optics-panel-${deviceId}-${ifIndex}`}>
      {data.live?.error && (
        <div className="text-[11px] font-mono text-amber-300 mb-2 flex items-center gap-2 flex-wrap">
          <AlertTriangle className="w-3.5 h-3.5" /> {data.live.error}
          <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px] text-slate-200 hover:bg-slate-800" onClick={() => setTestOpen(true)}><FlaskConical className="w-3 h-3 mr-1" /> Testar leitura</Button>
        </div>
      )}
      {!lanes.length && !data.live?.error && (
        <div className="text-[11px] font-mono text-slate-500 mb-2">Aguardando a 1ª leitura (a cada 5 min).
          <button className="ml-2 underline text-slate-300" onClick={() => setTestOpen(true)}>testar agora</button></div>
      )}
      {lanes.length > 0 && (
        <table className="w-full text-xs font-mono mb-2" data-testid="optics-lanes">
          <thead className="text-[10px] uppercase tracking-widest text-slate-500">
            <tr><th className="text-left py-1">{n > 1 ? "Lane" : ""}</th><th className="text-right">RX</th><th className="text-right">TX</th><th className="text-right pl-2"></th></tr>
          </thead>
          <tbody>
            {lanes.map((l, i) => (
              <tr key={i} className="border-t border-[#1E293B]">
                <td className="py-1"><span className="inline-flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: LANE_COLORS[i % 4] }} />{n > 1 ? `Lane ${l.lane}` : "Porta"}</span></td>
                <td className="text-right text-slate-100 font-semibold">{fmtDbm(l.rx)}</td>
                <td className="text-right text-slate-300">{fmtDbm(l.tx)}</td>
                <td className="text-right pl-2"><StatusTag s={rxStatus(l.rx, warn, crit)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!compact || data.points.length > 1 ? (
        <>
          <div className="flex items-center gap-1 mb-1">
            {["rx", "tx"].map(k => (
              <button key={k} onClick={() => setWhich(k)} className={`text-[11px] font-mono px-2 py-0.5 rounded border ${which === k ? "border-[#007AFF] text-slate-100 bg-[#007AFF]/15" : "border-[#1E293B] text-slate-400"}`}>
                {k === "rx" ? "RX (recebido)" : "TX (enviado)"}
              </button>
            ))}
            {data.stats?.rx_min != null && <span className="ml-auto text-[10px] font-mono text-slate-500">pior RX no período {data.stats.rx_min.toFixed(2)} dBm</span>}
          </div>
          <LineChart points={pts} series={series} height={compact ? 150 : chartHeight ? Math.max(170, chartHeight - 110) : 210} zeroBased={false} yFormat={(v) => v.toFixed(1)} tipFormat={(v) => `${v.toFixed(2)} dBm`}
                     refLines={refs} emptyText="Histórico óptico aparece após algumas leituras (a cada 5 min)." />
        </>
      ) : null}
      <OpticsTestDialog open={testOpen} onClose={() => setTestOpen(false)} deviceId={deviceId} ifIndex={ifIndex} ifName={ifName} onDone={() => setTick(t => t + 1)} />
    </div>
  );
}
