import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Download, AlertTriangle } from "lucide-react";

/**
 * Exporta equipamentos em CSV no mesmo formato do "Importar CSV" (ida e volta).
 * scopes: { all: number, filtered: {ids, count}, selected: {ids, count} }
 */
export function ExportDevicesDialog({ open, onOpenChange, total, filteredIds, selectedIds }) {
  const [scope, setScope] = useState("all");
  const [withPasswords, setWithPasswords] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setScope(selectedIds.length ? "selected" : filteredIds.length !== total ? "filtered" : "all");
      setWithPasswords(false);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const options = [
    ["all", "Todos os equipamentos", total],
    ["filtered", "Resultado da busca/filtro atual", filteredIds.length],
    ["selected", "Somente os selecionados", selectedIds.length],
  ];
  const count = options.find(o => o[0] === scope)?.[2] ?? 0;

  const doExport = async () => {
    setBusy(true);
    try {
      const device_ids = scope === "all" ? null : scope === "filtered" ? filteredIds : selectedIds;
      const res = await api.post("/devices/export", { device_ids, include_passwords: withPasswords }, { responseType: "blob" });
      const cd = res.headers?.["content-disposition"] || "";
      const name = /filename="([^"]+)"/.exec(cd)?.[1] || "equipamentos.csv";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(res.data); a.download = name; a.click();
      URL.revokeObjectURL(a.href);
      toast.success(`${count} equipamento(s) exportado(s)`);
      onOpenChange(false);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-md" data-testid="export-dialog">
        <DialogHeader><DialogTitle>Exportar equipamentos (CSV)</DialogTitle></DialogHeader>
        <p className="text-xs text-slate-400 font-mono">
          Mesmo formato do <b>Importar CSV</b>: dá para editar no Excel e importar de volta, ou migrar para outro Bastion.
        </p>
        <div className="space-y-2">
          {options.map(([v, label, n]) => (
            <label key={v} className={`flex items-center justify-between gap-3 px-3 py-2 rounded border cursor-pointer text-sm
                ${scope === v ? "border-[#007AFF] bg-[#007AFF]/10" : "border-[#1E293B] bg-[#0B111C]"} ${n === 0 ? "opacity-40 pointer-events-none" : ""}`}>
              <span className="flex items-center gap-2">
                <input type="radio" name="export-scope" value={v} checked={scope === v} onChange={() => setScope(v)} data-testid={`export-scope-${v}`} />
                {label}
              </span>
              <span className="font-mono text-xs text-slate-400">{n}</span>
            </label>
          ))}
        </div>
        <label className="flex items-start gap-2 text-sm cursor-pointer mt-1">
          <input type="checkbox" className="mt-1" checked={withPasswords} onChange={e => setWithPasswords(e.target.checked)} data-testid="export-with-passwords" />
          <span>
            Incluir senhas próprias dos equipamentos
            {withPasswords && (
              <span className="flex items-start gap-1.5 text-[11px] text-amber-400 mt-1">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                As senhas saem em texto puro no arquivo. Guarde em local seguro e apague depois de usar.
              </span>
            )}
          </span>
        </label>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={doExport} disabled={busy || count === 0} data-testid="export-confirm-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <Download className="w-4 h-4 mr-2" /> Exportar ({count})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
