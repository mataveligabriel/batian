import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { ShieldAlert, ShieldCheck, ChevronDown, ChevronRight, Siren, Info } from "lucide-react";
import { StackedChart } from "@/components/flow/StackedChart";
import { STATUS } from "@/lib/netfmt";
import { ago, fmtDur, fmtRate, SERIES } from "@/components/flow/flowlib";

const PROTO = { 1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 58: "ICMPv6" };

function hint(a) {
  const t = a.type || "";
  const sp = a.sport?.[0]?.[0];
  const out = [`RTBH do ${a.victim}${a.victim.includes(":") ? "/128" : "/32"} nos trânsitos (community 65535:666, se o trânsito aceitar) — derruba o ataque e o IP junto.`];
  if (t.startsWith("Amplificação") && sp !== undefined) out.push(`Filtro/Flowspec no upstream: UDP porta de origem ${sp} → ${a.victim} (tráfego legítimo com essa porta de origem é raro).`);
  if (t === "UDP fragmentado") out.push("Filtro de fragmentos UDP para o IP atacado no upstream (Flowspec fragment).");
  if (t.includes("SYN")) out.push("SYN cookies / limite de SYN no servidor; no upstream, Flowspec TCP flags SYN para o destino.");
  return out;
}

function AttackDetail({ a: base }) {
  const [a, setA] = useState(base);
  useEffect(() => {
    let alive = true;
    const load = () => api.get(`/flow/attacks/${base.id}`).then(r => alive && setA(r.data)).catch(() => {});
    load();
    const t = base.status === "active" ? setInterval(load, 15000) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [base.id, base.status]);
  const ser = a.series || [];
  const ts = ser.map(p => p[0]);
  const bps = [{ id: "bps", name: "bits/s", color: SERIES[0], values: ser.map(p => p[1]) }];
  const pps = [{ id: "pps", name: "pacotes/s", color: SERIES[0], values: ser.map(p => p[2]) }];
  return (
    <div className="px-3 pb-3 pt-1 space-y-3" data-testid={`attack-detail-${a.id}`}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500">AS de origem</div>
          {(a.src_as || []).map(([asn, b, name]) => <div key={asn} className="text-slate-300 truncate">{asn ? `AS${asn}` : "AS ?"} <span className="text-slate-500">{name}</span></div>)}
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500">Portas (origem → destino)</div>
          <div className="text-slate-300">origem: {(a.sport || []).map(p => p[0]).join(", ") || "—"}</div>
          <div className="text-slate-300">destino: {(a.dport || []).map(p => p[0]).join(", ") || "—"}</div>
          <div className="text-slate-400">protocolo: {(a.proto || []).map(p => PROTO[p[0]] || p[0]).join(", ") || "—"}</div>
          <div className="text-slate-400 mt-1">entrada: {Object.values(a.if_labels || {}).join(", ") || "—"}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500">Como mitigar</div>
          {hint(a).map((h, i) => <div key={i} className="text-slate-300 text-[11px] leading-snug mb-1">• {h}</div>)}
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-1">Bits/s para {a.victim} (média de 30 s)</div>
          <StackedChart ts={ts} series={bps} unit="bps" height={170} emptyText="Série ainda curta — aparece nas próximas janelas." />
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-1">Pacotes/s para {a.victim}</div>
          <StackedChart ts={ts} series={pps} unit="pps" height={170} emptyText="Série ainda curta — aparece nas próximas janelas." />
        </div>
      </div>
    </div>
  );
}

/** Aba Ataques: em andamento (vermelho, com ícone e rótulo) e histórico. */
export function FlowAttacks({ settings, onCount }) {
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.get("/flow/attacks", { params: { limit: 200 } })
      .then(r => { if (alive) { setItems(r.data); setErr(""); onCount?.(r.data.filter(a => a.status === "active").length); } })
      .catch(e => alive && setErr(formatApiError(e)));
    load();
    const t = setInterval(load, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const act = (items || []).filter(a => a.status === "active");
  const past = (items || []).filter(a => a.status !== "active");
  const at = settings?.attack || {};
  const where = (a) => Object.values(a.if_labels || {}).join(", ") || a.ifaces.join(", ");

  return (
    <div data-testid="flow-attacks">
      {err && <div className="text-xs text-amber-300 mb-2">{err}</div>}
      <div className="mb-4">
        {act.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-slate-300 border border-[#1E293B] rounded px-3 py-3" data-testid="no-active">
            <ShieldCheck className="w-5 h-5" style={{ color: STATUS.good }} /> <b style={{ color: STATUS.good }}>Normal</b> — nenhum ataque em andamento.
          </div>
        ) : act.map(a => (
          <div key={a.id} className="border rounded mb-2" style={{ borderColor: STATUS.critical }} data-testid="attack-active">
            <button className="w-full text-left px-3 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1" onClick={() => setOpen(open === a.id ? null : a.id)}>
              <span className="flex items-center gap-1.5 text-xs font-mono font-bold uppercase tracking-widest" style={{ color: STATUS.critical }}><Siren className="w-4 h-4 animate-pulse" /> Em andamento</span>
              <span className="font-mono text-lg text-slate-50">{a.victim}</span>
              <span className="text-sm text-slate-200">{a.type}</span>
              <span className="text-sm font-mono text-slate-50"><b>{fmtRate(a.cur_bps)}</b> · {fmtRate(a.cur_pps, "pps")}</span>
              <span className="text-xs font-mono text-slate-400">pico {fmtRate(a.peak_bps)} · começou {ago(a.start)}</span>
              <span className="text-xs text-slate-400 truncate">entrando por {where(a)}</span>
            </button>
            {open === a.id && <AttackDetail a={a} />}
          </div>
        ))}
      </div>

      <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-1">Histórico</div>
      {items && past.length === 0 && <div className="text-xs text-slate-500 mb-3">Nenhum ataque registrado.</div>}
      {past.length > 0 && (
        <div className="border border-[#1E293B] rounded divide-y divide-[#1E293B] mb-4" data-testid="attack-history">
          {past.map(a => {
            const dur = ((a.end ? new Date(a.end) : new Date()) - new Date(a.start)) / 1000;
            return (
              <div key={a.id}>
                <button className="w-full text-left px-3 py-2 grid grid-cols-[16px_1fr] md:grid-cols-[16px_150px_140px_1fr_150px_90px] gap-x-3 gap-y-0.5 items-center text-xs font-mono hover:bg-slate-800/30"
                        onClick={() => setOpen(open === a.id ? null : a.id)} data-testid="attack-row">
                  {open === a.id ? <ChevronDown className="w-3.5 h-3.5 text-slate-500" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-500" />}
                  <span className="text-slate-400">{new Date(a.start).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
                  <span className="text-slate-100">{a.victim}</span>
                  <span className="text-slate-300 truncate flex items-center gap-1.5"><ShieldAlert className="w-3.5 h-3.5 shrink-0" style={{ color: STATUS.serious }} />{a.type}</span>
                  <span className="text-slate-200">{fmtRate(a.peak_bps)} · {fmtRate(a.peak_pps, "pps")}</span>
                  <span className="text-slate-400">{fmtDur(dur)}</span>
                </button>
                {open === a.id && <AttackDetail a={a} />}
              </div>
            );
          })}
        </div>
      )}
      <div className="text-[11px] text-slate-500 flex items-start gap-1.5 max-w-4xl">
        <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>Detecção por IP de destino dentro dos seus prefixos, com média de 30 s: volume ≥ {fmtRate(at.bps)} ou {fmtRate(at.pps, "pps")},
          amplificação (NTP, DNS, SSDP, Memcached, CLDAP…) ≥ {fmtRate(at.amp_bps)}, SYN ≥ {fmtRate(at.syn_pps, "pps")}. Começa depois de {at.min_windows} janelas de 10 s
          e termina após {at.end_windows} janelas calmas. O alerta vai para o Telegram/webhook e para o app (push). Ajuste em Configuração.</span>
      </div>
    </div>
  );
}
