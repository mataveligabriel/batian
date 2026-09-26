import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { RefreshCw, Search, Loader2, AlertTriangle } from "lucide-react";
import { fmtSpeed, isDown, OPER_LABEL } from "@/lib/netfmt";

/**
 * Lista as interfaces de um equipamento (SNMP, com cache no servidor).
 * mode="single": onPick(iface) · mode="multi": selected (Set de índices) + onToggle(iface)
 */
export function InterfacePicker({ deviceId, mode = "single", value, selected, onPick, onToggle, onLoaded, height = "h-72" }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [hideDown, setHideDown] = useState(false);

  const load = async (refresh = false) => {
    if (!deviceId) return;
    setLoading(true); setErr("");
    try {
      const { data } = await api.get(`/devices/${deviceId}/interfaces`, { params: { refresh } });
      setData(data); onLoaded?.(data);
    } catch (e) { setErr(formatApiError(e)); }
    finally { setLoading(false); }
  };
  useEffect(() => { setData(null); setQ(""); load(false); }, [deviceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const list = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return (data?.interfaces || []).filter(i => {
      if (hideDown && i.oper !== "up") return false;
      const hay = `${i.name} ${i.descr} ${i.alias} ${i.index}`.toLowerCase();
      return words.every(w => hay.includes(w));
    });
  }, [data, q, hideDown]);

  if (!deviceId) return <div className="text-xs text-slate-500 font-mono p-3">Nó sem equipamento (nuvem/texto): não tem interfaces.</div>;

  return (
    <div className="flex flex-col gap-2" data-testid={`iface-picker-${deviceId}`}>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar interface ou descrição…"
                 className="pl-8 h-8 bg-[#05070A] border-[#1E293B] font-mono text-xs" data-testid="iface-search" />
        </div>
        <label className="text-[11px] text-slate-400 flex items-center gap-1 whitespace-nowrap cursor-pointer">
          <input type="checkbox" checked={hideDown} onChange={e => setHideDown(e.target.checked)} /> só up
        </label>
        <Button size="sm" variant="ghost" onClick={() => load(true)} disabled={loading} title="Ler de novo do equipamento (SNMP)"
                className="h-8 px-2 text-slate-300 hover:bg-slate-800" data-testid="iface-refresh">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
        </Button>
      </div>
      {data && (
        <div className="text-[10px] font-mono text-slate-500">
          {data.sys_name || "?"} · {data.interfaces.length} interfaces · lido em {new Date(data.at).toLocaleString("pt-BR")}
        </div>
      )}
      <div className={`${height} overflow-y-auto border border-[#1E293B] rounded bg-[#05070A]`}>
        {loading && !data && <div className="p-3 text-xs text-slate-400 font-mono flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Lendo interfaces por SNMP…</div>}
        {err && (
          <div className="p-3 text-xs text-red-400 font-mono flex gap-2" data-testid="iface-error">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>{err}<br /><span className="text-slate-500">Confira a community SNMP do equipamento (Equipamentos → editar) e se o SNMP está liberado para o IP do Bastion (ou do agente).</span></span>
          </div>
        )}
        {data && list.length === 0 && <div className="p-3 text-xs text-slate-500 font-mono">Nenhuma interface encontrada.</div>}
        {list.map(i => {
          const isSel = mode === "single" ? value === i.index : selected?.has(i.index);
          return (
            <button key={i.index} type="button" data-testid={`iface-${i.index}`}
                    onClick={() => (mode === "single" ? onPick?.(i) : onToggle?.(i))}
                    className={`w-full text-left px-2.5 py-1.5 flex items-center gap-2 border-b border-[#111722] last:border-b-0 ${isSel ? "bg-[#007AFF]/20" : "hover:bg-slate-800/60"}`}>
              {mode === "multi" && <input type="checkbox" readOnly checked={!!isSel} className="pointer-events-none" />}
              <span className={`text-[10px] font-mono w-12 shrink-0 ${isDown(i.oper) ? "text-red-400" : i.oper === "up" ? "text-emerald-400" : "text-slate-500"}`}>
                {i.oper === "up" ? "● up" : isDown(i.oper) ? "○ down" : `· ${OPER_LABEL[i.oper] || i.oper}`}
              </span>
              <span className="font-mono text-xs text-slate-100 truncate">{i.name}</span>
              {i.alias && <span className="text-[11px] text-slate-400 truncate">{i.alias}</span>}
              <span className="ml-auto text-[10px] font-mono text-slate-500 shrink-0">{fmtSpeed(i.speed_mbps)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
