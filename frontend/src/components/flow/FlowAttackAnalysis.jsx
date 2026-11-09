import React, { useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { toast } from "sonner";
import { copyText } from "@/lib/clipboard";
import { ShareTable } from "@/components/flow/FlowRawParts";
import { fmtRate, fmtVolume, pct, SERIES } from "@/components/flow/flowlib";
import { Copy, ExternalLink, Loader2, Microscope, ShieldAlert } from "lucide-react";

/**
 * Leitura do ataque no flow detalhado (estilo Wanguard): blocos /24, ASNs, países e IPs de origem, portas, tamanho
 * de pacote e flags TCP, com a assinatura e as regras sugeridas prontas para copiar.
 */
export function AttackAnalysis({ attack, onOpenQuery }) {
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const run = () => {
    setLoading(true);
    setErr("");
    api.get(`/flow/raw/attack/${attack.id}`)
      .then(r => setRes(r.data))
      .catch(e => setErr(formatApiError(e)))
      .finally(() => setLoading(false));
  };
  const T = res?.tables || {};
  const O = res?.others || {};
  const openQ = (group_by, extra = []) => onOpenQuery?.({
    start: new Date(res.start * 1000).toISOString(), end: new Date(res.end * 1000).toISOString(), direction: "in", group_by,
    unit: "bps", filters: [{ dim: "dst_ip", op: "in", values: [res.victim] }, ...extra],
  });
  const link = (gb) => onOpenQuery && res && (
    <button onClick={() => openQ(gb)} className="ml-auto text-[10px] text-slate-500 hover:text-brand-soft inline-flex items-center gap-0.5" title="Abrir na Consulta"><ExternalLink className="w-3 h-3" /> consulta</button>
  );
  const copy = async (text, msg) => { (await copyText(text)) ? toast.success(msg) : toast.error("Não consegui copiar"); };
  const report = () => {
    if (!res) return "";
    const L = [];
    L.push(`Ataque em ${res.victim} — ${res.signature?.kind || res.type || ""}`);
    L.push(`Período analisado: ${new Date(res.start * 1000).toLocaleString("pt-BR")} a ${new Date(res.end * 1000).toLocaleString("pt-BR")}`);
    L.push(`Pico: ${fmtRate(res.total.max_bps)} / ${fmtRate(res.total.max_pps, "pps")} · média ${fmtRate(res.total.avg_bps)} · volume ${fmtVolume(res.total.bytes, "bytes")} · pacote médio ${Math.round(res.total.pkt_size)} B`);
    (res.signature?.lines || []).forEach(l => L.push(`- ${l}`));
    const sec = (t, rows) => { if (rows?.length) { L.push(""); L.push(t); rows.forEach(r => L.push(`  ${r.name}  ${pct(r.share)}  ${fmtRate(r.avg_bps)}`)); } };
    sec("Blocos de origem:", T.src_pfx);
    sec("AS de origem:", T.src_as);
    sec("Países de origem:", T.src_cc);
    sec("Portas de origem:", T.sport);
    if (res.signature?.rules?.length) { L.push(""); L.push("Sugestões:"); res.signature.rules.forEach(r => L.push(`- ${r}`)); }
    return L.join("\n");
  };

  if (!res) {
    return (
      <div className="flex flex-wrap items-center gap-3 border border-dashed border-line rounded-md px-3 py-2" data-testid={`attack-analyze-${attack.id}`}>
        <button onClick={run} disabled={loading} className="h-8 px-3 rounded-md bg-brand text-onbrand text-xs font-medium hover:opacity-90 inline-flex items-center gap-1.5 disabled:opacity-60" data-testid="attack-analyze">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Microscope className="w-3.5 h-3.5" />} Analisar origens no flow detalhado
        </button>
        <span className="text-[11px] text-slate-400">blocos /24, ASNs, países, portas e tamanho de pacote de tudo que chegou em {attack.victim} — com sugestão de filtro</span>
        {err && <span className="text-[11px] text-amber-300 w-full">{err}</span>}
      </div>
    );
  }
  const sig = res.signature;
  return (
    <div className="border border-line rounded-md p-3 space-y-3" data-testid={`attack-analysis-${attack.id}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-xs font-medium text-slate-200 inline-flex items-center gap-1.5"><Microscope className="w-4 h-4" /> Análise detalhada</span>
        <span className="text-xs font-mono text-slate-400">pico {fmtRate(res.total.max_bps)} · {fmtRate(res.total.max_pps, "pps")} · pacote médio {Math.round(res.total.pkt_size)} B · {fmtVolume(res.total.bytes, "bytes")}</span>
        {res.coarse > 0 && <span className="text-[11px] text-amber-300/90 font-mono" title="Origem espalhada demais: parte das origens foi somada por bloco">origem resumida por bloco em parte do ataque</span>}
        <div className="ml-auto flex gap-3">
          <button onClick={() => copy(report(), "Resumo copiado")} className="text-[11px] text-brand-soft hover:underline inline-flex items-center gap-1" data-testid="attack-copy-report"><Copy className="w-3 h-3" /> copiar resumo</button>
          <button onClick={() => copy((T.src_pfx || []).map(r => r.name).join("\n"), "Blocos copiados")} className="text-[11px] text-brand-soft hover:underline inline-flex items-center gap-1"><Copy className="w-3 h-3" /> copiar blocos</button>
          <button onClick={run} className="text-[11px] text-slate-400 hover:text-slate-200">{loading ? "atualizando…" : "atualizar"}</button>
        </div>
      </div>
      {!res.files && <div className="text-xs text-amber-300">Não há flow detalhado gravado no período deste ataque (ele começou antes da atualização ou o detalhado está desligado).</div>}
      {sig && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="rounded-md bg-sunken p-2.5">
            <div className="text-[10px] text-slate-500 mb-1">Assinatura</div>
            <div className="text-sm text-slate-50 font-medium mb-1 flex items-center gap-1.5"><ShieldAlert className="w-4 h-4" /> {sig.kind}</div>
            {sig.lines.map((l, i) => <div key={i} className="text-[11px] text-slate-300 leading-snug">• {l}</div>)}
          </div>
          <div className="rounded-md bg-sunken p-2.5">
            <div className="flex items-center mb-1">
              <div className="text-[10px] text-slate-500">Como filtrar</div>
              <button onClick={() => copy(sig.rules.join("\n"), "Sugestões copiadas")} className="ml-auto text-[10px] text-brand-soft hover:underline inline-flex items-center gap-1"><Copy className="w-3 h-3" /> copiar</button>
            </div>
            {sig.rules.map((r, i) => <div key={i} className="text-[11px] text-slate-200 leading-snug mb-0.5">• {r}</div>)}
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
        <ShareTable title="Blocos de origem" rows={T.src_pfx || []} other={O.src_pfx} testid="aa-pfx" right={link(["src_pfx"])} />
        <ShareTable title="AS de origem" rows={T.src_as || []} other={O.src_as} testid="aa-as" right={link(["src_as"])} />
        <ShareTable title="Países de origem" rows={T.src_cc || []} other={O.src_cc} testid="aa-cc" right={link(["src_cc"])} />
        <ShareTable title="IPs de origem" rows={T.src_ip || []} other={O.src_ip} testid="aa-ip" right={link(["src_ip"])} />
        <ShareTable title="Protocolo" rows={T.proto || []} testid="aa-proto" color={SERIES[2]} />
        <ShareTable title="Porta de origem" rows={T.sport || []} other={O.sport} testid="aa-sport" color={SERIES[2]} right={link(["sport"])} />
        <ShareTable title="Porta de destino" rows={T.dport || []} other={O.dport} testid="aa-dport" color={SERIES[2]} right={link(["dport"])} />
        <ShareTable title="Tamanho do pacote (por pacotes)" rows={T.pktsize || []} unit="pps" testid="aa-size" color={SERIES[3]} />
        {(T.tcpflags || []).some(r => r.parts?.[0]?.value) && <ShareTable title="Flags TCP (por pacotes)" rows={T.tcpflags || []} unit="pps" testid="aa-flags" color={SERIES[3]} />}
        <ShareTable title="Interface de entrada" rows={T.iface || []} testid="aa-iface" color={SERIES[4]} />
      </div>
    </div>
  );
}
