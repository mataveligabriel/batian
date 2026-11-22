import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { FileUp } from "lucide-react";

/** Baixa o arquivo do mapa (topologia: /maps/<id>/export; disponibilidade: /avail/maps/<id>/export). */
export async function downloadMap(path, fallbackName) {
  try {
    const r = await api.get(path, { responseType: "blob" });
    const cd = r.headers?.["content-disposition"] || "";
    const name = (cd.match(/filename="([^"]+)"/) || [])[1] || `${fallbackName || "mapa"}.json`;
    const url = URL.createObjectURL(r.data);
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast.success("Mapa exportado — leve o arquivo para o outro BastiON e use Importar mapa");
  } catch (e) { toast.error(formatApiError(e)); }
}

/** Importar mapa exportado por outro BastiON: lê o arquivo, mostra o que casa e o que falta, e cria.
 *  kind: "topology" (POST /maps/import) | "availability" (POST /avail/import, com parents = mapas onde pode entrar). */
export function ImportMapDialog({ kind, parents = [], onClose, onDone }) {
  const [data, setData] = useState(null);
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState(null);
  const [create, setCreate] = useState(false);
  const [tag, setTag] = useState("");
  const [agent, setAgent] = useState("");
  const [parent, setParent] = useState("");
  const [agents, setAgents] = useState([]);
  const [tags, setTags] = useState([]);
  const [busy, setBusy] = useState(false);
  const url = kind === "availability" ? "/avail/import" : "/maps/import";

  useEffect(() => {
    api.get("/agents").then(r => setAgents(r.data || [])).catch(() => {});
    api.get("/devices").then(r => setTags([...new Set((r.data || []).flatMap(d => d.tags || []))].sort((a, b) => a.localeCompare(b, "pt-BR")))).catch(() => {});
  }, []);

  const pick = async (f) => {
    setPreview(null); setData(null);
    if (!f) return;
    setFileName(f.name);
    if (f.size > 50 * 1024 * 1024) return toast.error("Arquivo grande demais (máx. 50 MB)");
    let d;
    try { d = JSON.parse(await f.text()); } catch { return toast.error("O arquivo não é um JSON de mapa exportado pelo BastiON"); }
    setBusy(true);
    try {
      const r = await api.post(url, { data: d, apply: false });
      setData(d); setPreview(r.data); setCreate(false);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };

  const go = async () => {
    setBusy(true);
    try {
      const r = await api.post(url, { data, apply: true, create_missing: create, tag: create ? tag : "", agent_id: create ? agent : "", parent_id: parent || undefined }, { timeout: 300000 });
      const p = r.data;
      toast.success(`Mapa importado${p.maps_created > 1 ? ` (${p.maps_created} mapas)` : ""} · ${p.matched} equipamento(s) casados${p.created_devices ? ` · ${p.created_devices} cadastrado(s)` : ""}`);
      onDone?.(p.map_id);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };

  const miss = preview?.missing_count || 0;
  return (
    <Dialog open onOpenChange={o => !o && !busy && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg max-h-[90vh] overflow-y-auto" data-testid="map-import-dlg">
        <DialogHeader><DialogTitle>Importar mapa de outro BastiON</DialogTitle></DialogHeader>
        <div className="space-y-3 text-sm">
          <label className="flex flex-col items-center justify-center gap-1.5 h-24 rounded-md border border-dashed border-line text-slate-400 hover:border-brand hover:text-slate-200 cursor-pointer">
            <FileUp className="w-5 h-5" />
            <span>{fileName || `Escolher o arquivo .json exportado em ${kind === "availability" ? "Disponibilidade" : "Mapas de rede"}`}</span>
            <input type="file" accept=".json,application/json" className="hidden" onChange={e => pick(e.target.files?.[0])} data-testid="map-import-file" />
          </label>
          <p className="text-[11px] text-slate-500">O arquivo não tem usuário, senha, community nem jump. Os equipamentos são casados pelo IP (e porta) com os já cadastrados aqui.</p>

          {preview && (
            <div className="rounded-md border border-line p-3 space-y-2" data-testid="map-import-preview">
              <div className="font-medium text-slate-100">{preview.name || "(sem nome)"}{preview.maps > 1 ? ` · ${preview.maps} mapas (com submapas)` : ""}</div>
              <div className="text-xs text-slate-300">
                <b className="text-emerald-300">{preview.matched}</b> de {preview.devices} equipamento(s) já existem aqui.
                {miss > 0 && <> <b className="text-amber-300">{miss}</b> não existem.</>}
              </div>
              {miss > 0 && (
                <>
                  <ul className="max-h-28 overflow-y-auto text-[11px] font-mono text-slate-400 border border-line/60 rounded p-1.5">
                    {preview.missing.map((m, i) => <li key={i} className="truncate">{m.name} · {m.host}</li>)}
                    {miss > preview.missing.length && <li>… e mais {miss - preview.missing.length}</li>}
                  </ul>
                  <div className="space-y-1.5">
                    <label className="flex items-start gap-2 text-xs"><input type="radio" checked={!create} onChange={() => setCreate(false)} className="mt-0.5" />
                      <span>Não cadastrar — no mapa eles viram {kind === "availability" ? "um texto" : "uma nuvem"} com o nome, no mesmo lugar</span></label>
                    <label className="flex items-start gap-2 text-xs"><input type="radio" checked={create} onChange={() => setCreate(true)} className="mt-0.5" data-testid="map-import-create" />
                      <span>Cadastrar os que faltam (sem senha e com backup desligado; ajuste depois em Editar em massa)</span></label>
                  </div>
                  {create && (
                    <div className="grid grid-cols-2 gap-2">
                      <label className="text-[11px] text-slate-400 space-y-1">Tag (empresa)
                        <select value={tag} onChange={e => setTag(e.target.value)} className="w-full h-8 rounded-md bg-sunken border border-line text-xs px-2">
                          <option value="">as do arquivo</option>{tags.map(t => <option key={t} value={t}>{t}</option>)}
                        </select></label>
                      <label className="text-[11px] text-slate-400 space-y-1">Jump
                        <select value={agent} onChange={e => setAgent(e.target.value)} className="w-full h-8 rounded-md bg-sunken border border-line text-xs px-2">
                          <option value="">nenhum (direto)</option>{agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                        </select></label>
                      <Input value={tag} onChange={e => setTag(e.target.value)} placeholder="ou digite uma tag nova" className="col-span-2 h-8 bg-sunken border-line text-xs" />
                    </div>
                  )}
                </>
              )}
              {kind === "availability" && parents.length > 0 && (
                <label className="block text-[11px] text-slate-400 space-y-1">Onde colocar
                  <select value={parent} onChange={e => setParent(e.target.value)} className="w-full h-8 rounded-md bg-sunken border border-line text-xs px-2">
                    <option value="">como mapa principal</option>{parents.map(m => <option key={m.id} value={m.id}>dentro de {m.name}</option>)}
                  </select></label>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancelar</Button>
          <Button onClick={go} disabled={busy || !preview} className="bg-brand hover:bg-brand-strong" data-testid="map-import-go">{busy ? "Aguarde…" : "Importar"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
