import React, { useEffect, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { StackedChart } from "@/components/flow/StackedChart";
import { FlowIfaceSelect } from "@/components/flow/FlowIfaceSelect";
import { PeriodPicker, ShareTable } from "@/components/flow/FlowRawParts";
import { chip, DIM_TO_FILTER, fmtMinutes, fmtRate, fmtVolume, inputCls, SERIES } from "@/components/flow/flowlib";
import { AlertTriangle, ArrowDownToLine, ArrowUpFromLine, ExternalLink, Info, Loader2, Search } from "lucide-react";

const STORE = "bastion_flow_ip";
let consumedTarget = 0;   // alvo vindo de outra aba: consulta uma vez só
const load = () => { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { return {}; } };
const save = (v) => { try { localStorage.setItem(STORE, JSON.stringify(v)); } catch { /* tudo bem */ } };

function Card({ icon: I, title, s, color }) {
  return (
    <div className="border border-line rounded-md p-3 min-w-0" data-testid={`ip-card-${title}`}>
      <div className="flex items-center gap-1.5 text-xs text-slate-300 mb-1.5"><I className="w-4 h-4" style={{ color }} /> {title}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs font-mono">
        <div><div className="text-[10px] text-slate-500">média</div><div className="text-slate-50 text-base">{fmtRate(s.avg_bps)}</div></div>
        <div><div className="text-[10px] text-slate-500">pico</div><div className="text-slate-50 text-base">{fmtRate(s.max_bps)}</div></div>
        <div><div className="text-[10px] text-slate-500">pacotes/s (média · pico)</div><div className="text-slate-300">{fmtRate(s.avg_pps, "pps")} · {fmtRate(s.max_pps, "pps")}</div></div>
        <div><div className="text-[10px] text-slate-500">volume · pacote médio</div><div className="text-slate-300">{fmtVolume(s.bytes, "bytes")} · {s.pkt_size ? `${Math.round(s.pkt_size)} B` : "—"}</div></div>
      </div>
    </div>
  );
}

/** Aba Consultar IP: consumo de um IP ou bloco (download/upload) e com quem ele troca tráfego. */
export function FlowIp({ ifaces, target: incoming, onOpenQuery }) {
  const [saved] = useState(load);
  const [q, setQ] = useState(incoming?.target || saved.target || "");
  const [period, setPeriod] = useState({ minutes: saved.minutes || 360, start: null, end: null });
  const [ifs, setIfs] = useState(saved.ifs || []);
  const [unit, setUnit] = useState("bps");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  const run = (target = q, p = period, sel = ifs) => {
    const t = (target || "").trim();
    if (!t) return;
    save({ target: t, minutes: p.minutes, ifs: sel });
    const my = ++seq.current;
    setLoading(true);
    setErr("");
    api.post("/flow/raw/ip", { target: t, minutes: p.minutes, start: p.start || null, end: p.end || null, interfaces: sel })
      .then(r => { if (my === seq.current) setRes(r.data); })
      .catch(e => { if (my === seq.current) { setErr(formatApiError(e)); setRes(null); } })
      .finally(() => { if (my === seq.current) setLoading(false); });
  };
  useEffect(() => {          // veio de outra aba (clique em "IP")
    if (incoming?.target && incoming._t > consumedTarget) { consumedTarget = incoming._t; setQ(incoming.target); run(incoming.target); }
    else if (q && !res) run();             // voltou para a aba: mostra de novo o último consultado
  }, [incoming]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (res) run(res.target, period, ifs); }, [period, ifs]); // eslint-disable-line react-hooks/exhaustive-deps

  const T = res?.tables || {};
  const series = res ? [
    { id: "down", name: "Download (entrada)", color: SERIES[0], values: unit === "pps" ? res.down.pps : res.down.bps },
    { id: "up", name: "Upload (saída)", color: SERIES[1], values: unit === "pps" ? res.up.pps : res.up.bps },
  ] : [];
  // abrir na Consulta: o alvo + o valor clicado
  const open = (dir, extra = [], group_by) => onOpenQuery?.({
    minutes: period.minutes, start: period.start, end: period.end, direction: dir === "down" ? "in" : "out",
    filters: [{ dim: dir === "down" ? "dst_ip" : "src_ip", op: "in", values: [res.target] }, ...extra], ...(group_by ? { group_by } : {}),
  });
  const pick = (dir) => (row, op) => open(dir, row.parts.filter(p => DIM_TO_FILTER[p.dim] && p.value !== "").map(p => ({ dim: DIM_TO_FILTER[p.dim], op, values: [p.value] })), [row.parts[0].dim]);
  const ipClick = (ip) => { setQ(ip); run(ip); };
  const link = (dir, gb) => onOpenQuery && (
    <button onClick={() => open(dir, [], gb)} className="ml-auto text-[10px] text-slate-500 hover:text-brand-soft inline-flex items-center gap-0.5" title="Abrir na Consulta"><ExternalLink className="w-3 h-3" /> consulta</button>
  );

  return (
    <div data-testid="flow-ip">
      <form className="flex flex-wrap items-center gap-2 mb-3" onSubmit={e => { e.preventDefault(); run(); }}>
        <div className="relative">
          <Search className="w-4 h-4 absolute left-2.5 top-2 text-slate-500" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="IP ou bloco: x.x.x.x ou x.x.x.x/x" className={`${inputCls} h-8 w-72 pl-8 font-mono text-sm`} data-testid="ip-target" aria-label="IP ou bloco" />
        </div>
        <button type="submit" className="h-8 px-3 rounded-md bg-brand text-onbrand text-xs font-medium hover:opacity-90 inline-flex items-center gap-1.5" data-testid="ip-run">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />} Consultar
        </button>
        <PeriodPicker value={period} onChange={setPeriod} testid="ip-range" />
        <FlowIfaceSelect ifaces={ifaces} value={ifs} onChange={setIfs} testid="ip-ifsel" />
        <button type="button" onClick={() => setUnit(unit === "bps" ? "pps" : "bps")} className={`${chip(false)} ml-auto`}>{unit === "bps" ? "bits/s" : "pacotes/s"}</button>
      </form>

      {err && <div className="mb-2 text-xs text-amber-300 font-mono flex items-start gap-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}
      {!res && !loading && !err && (
        <div className="text-sm text-slate-400 p-6 border border-dashed border-line rounded max-w-3xl">
          Digite um IP (cliente, servidor, CGNAT) ou um bloco para ver quanto ele baixa e envia, de quais AS e blocos vem o tráfego,
          as portas e por qual interface entra. Sem escolher interfaces, a consulta usa as de trânsito, PNI, IX e CDN.
        </div>
      )}
      {res && (
        <>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
            <span className="font-mono text-lg text-slate-50" data-testid="ip-title">{res.target}</span>
            <span className="text-xs text-slate-400">{fmtMinutes(res.minutes)} · {res.interfaces.length} interface(s): {res.interfaces.slice(0, 3).join(", ")}{res.interfaces.length > 3 ? "…" : ""}</span>
            {res.files < res.minutes * 0.98 && <span className="text-[11px] text-amber-300/90 font-mono">dados em {res.files} de {res.minutes} minutos</span>}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mb-2">
            <Card icon={ArrowDownToLine} title="Download (chega no IP)" s={res.down} color={SERIES[0]} />
            <Card icon={ArrowUpFromLine} title="Upload (sai do IP)" s={res.up} color={SERIES[1]} />
          </div>
          <StackedChart ts={res.ts} series={series} unit={unit} stacked={false} height={240}
                        emptyText={res.files ? "Nenhum tráfego deste IP nas interfaces escolhidas." : "Ainda não há flow detalhado neste período."} />
          <div className="text-[10px] font-mono text-slate-500 mt-1">resolução {res.step >= 3600 ? `${res.step / 3600} h` : `${res.step / 60} min`}</div>

          {res.is_block && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-3">
              <ShareTable title="IPs do bloco que mais baixam" rows={T.local_down || []} unit={unit} onFilter={pick("down")} onIp={ipClick} testid="ip-local-down" right={link("down", ["dst_ip"])} />
              <ShareTable title="IPs do bloco que mais enviam" rows={T.local_up || []} unit={unit} onFilter={pick("up")} onIp={ipClick} color={SERIES[1]} testid="ip-local-up" right={link("up", ["src_ip"])} />
            </div>
          )}

          <div className="text-xs text-slate-300 font-medium mt-4 mb-1.5 flex items-center gap-1.5"><ArrowDownToLine className="w-4 h-4" style={{ color: SERIES[0] }} /> Download — de onde vem</div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
            <ShareTable title="AS de origem" rows={T.down_as || []} unit={unit} onFilter={pick("down")} testid="ip-down-as" right={link("down", ["src_as"])} />
            <ShareTable title="Blocos de origem" rows={T.down_pfx || []} unit={unit} onFilter={pick("down")} testid="ip-down-pfx" right={link("down", ["src_pfx"])} />
            <ShareTable title="IPs de origem" rows={T.down_ip || []} unit={unit} onFilter={pick("down")} testid="ip-down-ip" right={link("down", ["src_ip"])} />
            <ShareTable title="Protocolo e porta de origem" rows={T.down_port || []} unit={unit} onFilter={pick("down")} testid="ip-down-port" right={link("down", ["proto", "sport"])} />
          </div>
          <div className="text-xs text-slate-300 font-medium mt-4 mb-1.5 flex items-center gap-1.5"><ArrowUpFromLine className="w-4 h-4" style={{ color: SERIES[1] }} /> Upload — para onde vai</div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
            <ShareTable title="AS de destino" rows={T.up_as || []} unit={unit} onFilter={pick("up")} color={SERIES[1]} testid="ip-up-as" right={link("up", ["dst_as"])} />
            <ShareTable title="Blocos de destino" rows={T.up_pfx || []} unit={unit} onFilter={pick("up")} color={SERIES[1]} testid="ip-up-pfx" right={link("up", ["dst_pfx"])} />
            <ShareTable title="IPs de destino" rows={T.up_ip || []} unit={unit} onFilter={pick("up")} color={SERIES[1]} testid="ip-up-ip" right={link("up", ["dst_ip"])} />
            <ShareTable title="Protocolo e porta de destino" rows={T.up_port || []} unit={unit} onFilter={pick("up")} color={SERIES[1]} testid="ip-up-port" right={link("up", ["proto", "dport"])} />
          </div>
          <div className="mt-2 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
            <ShareTable title="Interface por onde o download entra" rows={T.down_iface || []} unit={unit} testid="ip-down-iface" />
          </div>
        </>
      )}
      <div className="mt-3 text-[11px] text-slate-500 flex items-start gap-1.5 max-w-4xl">
        <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>Download = tráfego com destino ao IP que entra pelas interfaces escolhidas; upload = tráfego com origem no IP que sai por elas.
          Para IP atrás de CGNAT, consulte o IP público do NAT. Nas tabelas, <b>filtrar</b> abre a Consulta já com o IP e o valor escolhido.</span>
      </div>
    </div>
  );
}
