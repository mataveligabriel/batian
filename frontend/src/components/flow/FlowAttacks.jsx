import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { ShieldAlert, ShieldCheck, ChevronDown, ChevronRight, Siren, Info, ShieldBan, Copy } from "lucide-react";
import { toast } from "sonner";
import { copyText } from "@/lib/clipboard";
import { MitigateDialog } from "@/components/flow/FlowMitigation";
import { StackedChart } from "@/components/flow/StackedChart";
import { STATUS } from "@/lib/netfmt";
import { ago, fmtDur, fmtRate, SERIES } from "@/components/flow/flowlib";

const PROTO = { 1: "ICMP", 6: "TCP", 17: "UDP", 47: "GRE", 58: "ICMPv6" };

function hint(a) {
  const t = a.type || "";
  const sp = a.sport?.[0]?.[0];
  const out = [`Botão Mitigar: blackhole (RTBH) do ${a.victim}${a.victim.includes(":") ? "/128" : "/32"} nas suas bordas (BGP do BastiON) — derruba o ataque e o IP junto.`];
  if (t.startsWith("Amplificação") && sp !== undefined) out.push(`Filtro/Flowspec no upstream: UDP porta de origem ${sp} → ${a.victim} (tráfego legítimo com essa porta de origem é raro).`);
  if (t === "UDP fragmentado") out.push("Filtro de fragmentos UDP para o IP atacado no upstream (Flowspec fragment).");
  if (t.includes("SYN")) out.push("SYN cookies / limite de SYN no servidor; no upstream, Flowspec TCP flags SYN para o destino.");
  return out;
}

/** Quem está mandando: os IPs de origem com mais tráfego para a vítima. */
function SourceIps({ a }) {
  const rows = a.src_ip || [];
  const [all, setAll] = useState(false);
  if (!rows.length) return null;
  const total = (a.src_as || []).reduce((n, x) => n + (x[1] || 0), 0) || rows.reduce((n, x) => n + x[1], 0);
  const shown = all ? rows : rows.slice(0, 12);
  const t = a.type || "";
  const note = t.startsWith("Amplificação")
    ? "São refletores (servidores abertos usados na amplificação): os IPs são reais e dá para filtrar ou avisar os donos."
    : (t.includes("SYN") || t.includes("TCP"))
      ? "Em flood TCP/SYN os IPs de origem costumam ser falsificados: bloquear por IP ajuda pouco — o que resolve é o blackhole ou o filtro no upstream."
      : "";
  const copy = async () => {
    const ok = await copyText(rows.map(r => r[0]).join("\n"));
    ok ? toast.success(`${rows.length} IPs copiados`) : toast.error("Não consegui copiar");
  };
  return (
    <div data-testid={`attack-src-ips-${a.id}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-1">
        <div className="text-[11px] text-slate-400">
          IPs de origem — <span className="text-slate-200">{(a.n_src || rows.length).toLocaleString("pt-BR")}</span> distintos
          {a.n_src > rows.length ? `, os ${rows.length} com mais tráfego` : ""}
        </div>
        <button onClick={copy} className="text-[11px] text-brand-soft hover:underline inline-flex items-center gap-1" data-testid="attack-copy-ips">
          <Copy className="w-3 h-3" /> copiar lista
        </button>
        {rows.length > 12 && (
          <button onClick={() => setAll(!all)} className="text-[11px] text-slate-400 hover:text-slate-200">{all ? "mostrar menos" : `ver os ${rows.length}`}</button>
        )}
      </div>
      {note && <div className="text-[11px] text-slate-500 mb-1.5">{note}</div>}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-x-6 text-xs font-mono">
        {shown.map(([ip, b, p, asn, name]) => (
          <div key={ip} className="flex items-center gap-2 py-0.5 border-b border-line/60 min-w-0">
            <span className="text-slate-200 w-[15ch] shrink-0 truncate" title={ip}>{ip}</span>
            <span className="text-slate-500 truncate flex-1" title={name}>{asn ? `AS${asn}` : "AS ?"} {name}</span>
            <span className="text-slate-400 shrink-0 tabular-nums">{total ? `${((b / total) * 100).toFixed(b / total < 0.01 ? 2 : 1)}%` : ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
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
          <div className="text-[10px] text-slate-500">AS de origem</div>
          {(a.src_as || []).map(([asn, b, name]) => <div key={asn} className="text-slate-300 truncate">{asn ? `AS${asn}` : "AS ?"} <span className="text-slate-500">{name}</span></div>)}
        </div>
        <div>
          <div className="text-[10px] text-slate-500">Portas (origem → destino)</div>
          <div className="text-slate-300">origem: {(a.sport || []).map(p => p[0]).join(", ") || "—"}</div>
          <div className="text-slate-300">destino: {(a.dport || []).map(p => p[0]).join(", ") || "—"}</div>
          <div className="text-slate-400">protocolo: {(a.proto || []).map(p => PROTO[p[0]] || p[0]).join(", ") || "—"}</div>
          <div className="text-slate-400 mt-1">entrada: {Object.values(a.if_labels || {}).join(", ") || "—"}</div>
        </div>
        <div>
          <div className="text-[10px] text-slate-500">Como mitigar</div>
          {hint(a).map((h, i) => <div key={i} className="text-slate-300 text-[11px] leading-snug mb-1">• {h}</div>)}
        </div>
      </div>
      <SourceIps a={a} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div>
          <div className="text-[10px] text-slate-500 mb-1">Bits/s para {a.victim} (média de 30 s)</div>
          <StackedChart ts={ts} series={bps} unit="bps" height={170} emptyText="Série ainda curta — aparece nas próximas janelas." />
        </div>
        <div>
          <div className="text-[10px] text-slate-500 mb-1">Pacotes/s para {a.victim}</div>
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
  const [mit, setMit] = useState(null);          // ataque sendo mitigado (diálogo)
  const [blackholed, setBlackholed] = useState({});  // ip -> mitigação ativa
  const loadMit = () => api.get("/flow/mitigation/status").then(r => setBlackholed(Object.fromEntries((r.data.active || []).map(m => [m.prefix.split("/")[0], m])))).catch(() => {});
  useEffect(() => {
    let alive = true;
    loadMit();
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
          <div className="flex items-center gap-2 text-sm text-slate-300 border border-line rounded px-3 py-3" data-testid="no-active">
            <ShieldCheck className="w-5 h-5" style={{ color: STATUS.good }} /> <b style={{ color: STATUS.good }}>Normal</b> — nenhum ataque em andamento.
          </div>
        ) : act.map(a => (
          <div key={a.id} className="border rounded mb-2" style={{ borderColor: STATUS.critical }} data-testid="attack-active">
            <div className="flex flex-wrap items-center">
            <button className="flex-1 text-left px-3 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1" onClick={() => setOpen(open === a.id ? null : a.id)}>
              <span className="flex items-center gap-1.5 text-xs font-bold" style={{ color: STATUS.critical }}><Siren className="w-4 h-4 animate-pulse" /> Em andamento</span>
              <span className="font-mono text-lg text-slate-50">{a.victim}</span>
              <span className="text-sm text-slate-200">{a.type}</span>
              <span className="text-sm font-mono text-slate-50"><b>{fmtRate(a.cur_bps)}</b> · {fmtRate(a.cur_pps, "pps")}</span>
              <span className="text-xs font-mono text-slate-400">pico {fmtRate(a.peak_bps)} · começou {ago(a.start)}</span>
              <span className="text-xs text-slate-400 truncate">entrando por {where(a)}</span>
            </button>
            {blackholed[a.victim]
              ? <span className="mx-3 text-xs font-mono flex items-center gap-1" style={{ color: STATUS.warning }} data-testid="attack-blackholed"><ShieldBan className="w-4 h-4" /> em blackhole até {new Date(blackholed[a.victim].expires_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}</span>
              : <button onClick={() => setMit(a)} data-testid="attack-mitigate" className="mx-3 my-2 px-3 py-1.5 rounded text-xs font-medium text-white flex items-center gap-1.5 hover:opacity-90" style={{ background: STATUS.critical }}>
                  <ShieldBan className="w-4 h-4" /> Mitigar</button>}
            </div>
            {open === a.id && <AttackDetail a={a} />}
          </div>
        ))}
      </div>

      {mit && <MitigateDialog target={mit} onClose={() => setMit(null)} onDone={loadMit} />}
      <div className="text-[10px] text-slate-500 mb-1">Histórico</div>
      {items && past.length === 0 && <div className="text-xs text-slate-500 mb-3">Nenhum ataque registrado.</div>}
      {past.length > 0 && (
        <div className="border border-line rounded divide-y divide-line mb-4" data-testid="attack-history">
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
