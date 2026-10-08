import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Loader2, Share2 } from "lucide-react";

/** Compartilhar ao vivo as interfaces e os conteúdos do Flow deste usuário com outros (eles veem, não editam). */
export function FlowShareDialog({ onClose }) {
  const [data, setData] = useState(null);
  const [sel, setSel] = useState(new Set());
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get("/flow/shares").then(({ data }) => { setData(data); setSel(new Set(data.user_ids)); }).catch(e => { toast.error(formatApiError(e)); onClose(); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (id) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const save = async () => {
    setBusy(true);
    try { await api.put("/flow/shares", { user_ids: [...sel] }); toast.success(sel.size ? `Flow compartilhado com ${sel.size} usuário(s)` : "Compartilhamento removido"); onClose(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-md" data-testid="flow-share-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Share2 className="w-4 h-4 text-brand-soft" />Compartilhar o Flow</DialogTitle></DialogHeader>
        {!data ? <div className="py-8 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : (
          <div className="space-y-3">
            <p className="text-xs text-slate-400">Os usuários marcados passam a ver, ao vivo, as suas <b className="text-slate-200">interfaces</b> e os seus <b className="text-slate-200">conteúdos</b> do Flow — e os ataques que entram por elas. Eles não conseguem editar nem apagar; o que você mudar aparece para eles na hora.</p>
            <div className="border border-line rounded-md max-h-72 overflow-y-auto divide-y divide-line/60 bg-sunken">
              {data.users.map(u => (
                <div key={u.id} className="flex items-center gap-3 px-3 py-2 text-sm" data-testid={`share-${u.id}`}>
                  <input type="checkbox" id={`fsh-${u.id}`} checked={sel.has(u.id)} onChange={() => toggle(u.id)} />
                  <label htmlFor={`fsh-${u.id}`} className="cursor-pointer flex-1 min-w-0"><span className="text-slate-200">{u.name || u.email}</span> <span className="text-[11px] text-slate-500">{u.email}</span></label>
                  <span className="text-[10px] font-mono text-slate-500">{u.role}</span>
                </div>
              ))}
              {!data.users.length && <div className="px-3 py-6 text-center text-xs text-slate-500">Nenhum outro usuário (perfil View não usa o Flow).</div>}
            </div>
            {data.from.length > 0 && <div className="text-[11px] text-slate-500">Você recebe o Flow de: <span className="text-slate-300">{data.from.map(f => f.name).join(", ")}</span></div>}
          </div>
        )}
        <DialogFooter><Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy || !data} className="bg-brand hover:bg-brand-strong" data-testid="flow-share-save">{busy && <Loader2 className="w-4 h-4 mr-1 animate-spin" />}Salvar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
