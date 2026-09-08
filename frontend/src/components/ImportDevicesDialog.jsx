import React, { useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Upload, Download, FileSpreadsheet } from "lucide-react";

const TEMPLATE = `name,host,port,username,password,device_type,tags,agent,description
core-sp-01,10.10.1.1,22,admin,,cisco,Core;SP,jump-vpn,Core Router SP
sw-rj-02,192.168.20.5,2222,admin,,huawei,Switch;RJ,,Switch Distribuição
rb-mg-01,172.16.0.1,22,admin+ct,,mikrotik,Roteador;MG,,RouterBoard
`;

function parseCSV(text) {
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === "," || c === ";") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const clean = rows.filter(r => r.some(v => v.trim() !== ""));
  if (clean.length < 2) return [];
  const headers = clean[0].map(h => h.trim().toLowerCase().replace(/^\uFEFF/, ""));
  return clean.slice(1).map(r => Object.fromEntries(headers.map((h, i) => [h, (r[i] || "").trim()])));
}

export function ImportDevicesDialog({ open, onOpenChange, onDone }) {
  const [rows, setRows] = useState([]);
  const [fileName, setFileName] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  const onFile = async (f) => {
    if (!f) return;
    const text = await f.text();
    const parsed = parseCSV(text);
    setFileName(f.name); setRows(parsed); setResult(null);
    if (!parsed.length) toast.error("CSV vazio ou sem cabeçalho (name,host,...)");
  };

  const doImport = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/devices/import", { rows });
      setResult(data);
      toast.success(`${data.created} importado(s), ${data.skipped} duplicado(s)`);
      onDone?.();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const downloadTemplate = () => {
    const blob = new Blob([TEMPLATE], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "modelo-equipamentos.csv"; a.click();
    URL.revokeObjectURL(a.href);
  };

  const reset = () => { setRows([]); setFileName(""); setResult(null); };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) reset(); onOpenChange(v); }}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-3xl" data-testid="import-dialog">
        <DialogHeader><DialogTitle>Importar equipamentos (CSV)</DialogTitle></DialogHeader>
        <p className="text-xs text-slate-400 font-mono">
          Colunas: <span className="text-emerald-300">name, host, port, username, password, device_type, tags, agent, description</span>.
          Apenas <b>name</b> e <b>host</b> são obrigatórios. Tags separadas por <code>;</code>. <b>agent</b> = nome do agente cadastrado.
          Tipos: linux, mikrotik, cisco, huawei, ubiquiti, datacom, zte, other.
        </p>
        <div className="flex gap-2 flex-wrap">
          <input ref={inputRef} type="file" accept=".csv,text/csv" className="hidden" data-testid="import-file-input"
                 onChange={(e) => onFile(e.target.files?.[0])} />
          <Button variant="outline" onClick={() => inputRef.current?.click()} data-testid="import-choose-file-btn"
                  className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">
            <FileSpreadsheet className="w-4 h-4 mr-2" /> Escolher CSV
          </Button>
          <Button variant="ghost" onClick={downloadTemplate} data-testid="import-template-btn" className="text-slate-300 hover:bg-slate-800">
            <Download className="w-4 h-4 mr-2" /> Baixar modelo
          </Button>
          {fileName && <span className="text-xs font-mono text-slate-400 self-center" data-testid="import-file-name">{fileName} · {rows.length} linha(s)</span>}
        </div>

        {rows.length > 0 && (
          <div className="border border-[#1E293B] rounded max-h-64 overflow-auto" data-testid="import-preview">
            <table className="w-full text-xs font-mono">
              <thead className="bg-[#0B111C] text-slate-500 uppercase tracking-widest sticky top-0">
                <tr><th className="text-left px-3 py-2">name</th><th className="text-left px-3 py-2">host</th><th className="text-left px-3 py-2">port</th><th className="text-left px-3 py-2">user</th><th className="text-left px-3 py-2">tipo</th><th className="text-left px-3 py-2">agent</th><th className="text-left px-3 py-2">tags</th></tr>
              </thead>
              <tbody className="divide-y divide-[#1E293B]">
                {rows.slice(0, 200).map((r, i) => (
                  <tr key={i} className={!r.name || !r.host ? "text-red-400" : "text-slate-300"}>
                    <td className="px-3 py-1.5">{r.name || "—"}</td><td className="px-3 py-1.5">{r.host || r.ip || "—"}</td>
                    <td className="px-3 py-1.5">{r.port || 22}</td><td className="px-3 py-1.5">{r.username || r.user || ""}</td>
                    <td className="px-3 py-1.5">{r.device_type || r.type || "linux"}</td><td className="px-3 py-1.5">{r.agent || ""}</td>
                    <td className="px-3 py-1.5">{r.tags || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {result && (
          <div className="text-xs font-mono space-y-1" data-testid="import-result">
            <div><span className="text-emerald-400">{result.created}</span> criado(s) · <span className="text-amber-400">{result.skipped}</span> duplicado(s) ignorado(s)</div>
            {result.errors?.map((e, i) => <div key={i} className="text-red-400">{e}</div>)}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Fechar</Button>
          <Button onClick={doImport} disabled={!rows.length || busy || !!result} data-testid="import-confirm-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <Upload className="w-4 h-4 mr-2" /> Importar {rows.length ? `(${rows.length})` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
