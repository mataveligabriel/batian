import React from "react";
import { MODULES, PRESETS, ALL_KEYS } from "@/lib/modules";
import { Label } from "@/components/ui/label";

/** Quais partes do sistema o operador usa. value: null = todos | lista de chaves. */
export function ModulePicker({ value, onChange }) {
  const all = !Array.isArray(value);
  const has = (k) => all || value.includes(k);
  const toggle = (k) => {
    const cur = all ? ALL_KEYS : value;
    const next = cur.includes(k) ? cur.filter(x => x !== k) : ALL_KEYS.filter(x => x === k || cur.includes(x));
    onChange(next.length === ALL_KEYS.length ? null : next);
  };
  const same = (keys) => (keys === null ? all : !all && keys.length === value.length && keys.every(k => value.includes(k)));
  return (
    <div data-testid="module-picker">
      <Label>Módulos que este usuário acessa</Label>
      <div className="flex flex-wrap gap-1.5 mt-1.5">
        {PRESETS.map(p => (
          <button key={p.label} type="button" onClick={() => onChange(p.keys ? [...p.keys] : null)} data-testid={`module-preset-${p.label}`}
                  className={`px-2 py-0.5 rounded-md text-xs border ${same(p.keys) ? "bg-brand border-brand text-white" : "border-line text-slate-300 hover:border-line2"}`}>{p.label}</button>
        ))}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 mt-2.5">
        {MODULES.map(m => (
          <label key={m.key} className="flex items-start gap-2 py-1 cursor-pointer text-sm">
            <input type="checkbox" checked={has(m.key)} onChange={() => toggle(m.key)} data-testid={`module-${m.key}`} className="mt-0.5 accent-[#2F6FCB] w-4 h-4" />
            <span><span className="text-slate-100">{m.label}</span><span className="block text-[11px] text-slate-500 leading-tight">{m.desc}</span></span>
          </label>
        ))}
      </div>
      <div className="text-[11px] text-slate-500 mt-2">O Painel NOC e a própria conta ficam sempre liberados. Ele só vê os equipamentos, mapas e dashboards que são dele — use <b>Enviar</b> para passar os seus.</div>
    </div>
  );
}
