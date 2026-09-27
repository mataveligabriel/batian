import React, { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { EXTERNAL, ROLE_LABEL } from "@/components/flow/flowlib";

/** Escolha de interfaces monitoradas (vazio = todas), agrupadas por papel, com atalhos "todos os trânsitos" etc. */
export function FlowIfaceSelect({ ifaces, value, onChange, testid = "flow-ifsel" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const on = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", on);
    return () => document.removeEventListener("mousedown", on);
  }, [open]);
  const sel = new Set(value || []);
  const all = !sel.size;
  const label = all ? `Todas as interfaces (${ifaces.length})` : sel.size === 1 ? (ifaces.find(i => i.id === [...sel][0])?.label || ifaces.find(i => i.id === [...sel][0])?.if_name || "1 interface") : `${sel.size} interfaces`;
  const roles = [...new Set(ifaces.map(i => i.role))].sort((a, b) => Object.keys(ROLE_LABEL).indexOf(a) - Object.keys(ROLE_LABEL).indexOf(b));
  const toggle = (id) => { const s = new Set(sel); if (s.has(id)) s.delete(id); else s.add(id); onChange([...s]); };
  const pickRole = (r) => onChange(ifaces.filter(i => (r === "_ext" ? EXTERNAL.includes(i.role) : i.role === r)).map(i => i.id));
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)} className="h-8 px-2.5 rounded-md border border-[#1E293B] bg-[#05070A] text-xs text-slate-200 flex items-center gap-1.5 max-w-[260px]" data-testid={testid}>
        <span className="truncate">{label}</span><ChevronDown className="w-3.5 h-3.5 text-slate-500 shrink-0" />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-[340px] max-w-[92vw] bg-[#111722] border border-[#2A3345] rounded-md shadow-xl p-2" data-testid={`${testid}-menu`}>
          <div className="flex flex-wrap gap-1 mb-2">
            <button onClick={() => onChange([])} className={`text-[11px] px-2 py-0.5 rounded border ${all ? "border-[#007AFF] text-slate-100" : "border-[#1E293B] text-slate-400"}`}>Todas</button>
            <button onClick={() => pickRole("_ext")} className="text-[11px] px-2 py-0.5 rounded border border-[#1E293B] text-slate-400 hover:text-slate-200">Externas</button>
            {roles.map(r => <button key={r} onClick={() => pickRole(r)} className="text-[11px] px-2 py-0.5 rounded border border-[#1E293B] text-slate-400 hover:text-slate-200">Só {ROLE_LABEL[r] || r}</button>)}
          </div>
          <div className="max-h-72 overflow-y-auto">
            {roles.map(r => (
              <div key={r} className="mb-1.5">
                <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono px-1">{ROLE_LABEL[r] || r}</div>
                {ifaces.filter(i => i.role === r).map(i => (
                  <label key={i.id} className="flex items-center gap-2 px-1 py-0.5 rounded hover:bg-slate-800/60 cursor-pointer text-xs">
                    <input type="checkbox" checked={all || sel.has(i.id)} onChange={() => (all ? onChange(ifaces.filter(x => x.id !== i.id).map(x => x.id)) : toggle(i.id))} />
                    <span className="text-slate-200 truncate">{i.label || i.if_name}</span>
                    <span className="ml-auto text-[10px] font-mono text-slate-500 truncate">{i.device_name} · {i.if_name}</span>
                  </label>
                ))}
              </div>
            ))}
            {!ifaces.length && <div className="text-xs text-slate-500 p-2">Nenhuma interface monitorada ainda (aba Interfaces).</div>}
          </div>
        </div>
      )}
    </div>
  );
}
