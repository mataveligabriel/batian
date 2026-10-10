import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { TagSelect } from "@/components/TagSelect";
import { Send, Server, Network, Gauge, Loader2, Search, Eye, ArrowRight } from "lucide-react";

const ROLE_LABEL = { admin: "administrador", operator: "operador", viewer: "View" };
const TABS = [
  { key: "devices", label: "Equipamentos", icon: Server },
  { key: "maps", label: "Mapas", icon: Network },
  { key: "dashboards", label: "Dashboards", icon: Gauge },
];

function CheckList({ items, selected, onToggle, onAll, render, q, empty }) {
  const shown = items.filter(i => !q || JSON.stringify(i).toLowerCase().includes(q.toLowerCase()));
  const allOn = shown.length > 0 && shown.every(i => selected.has(i.id));
  return (
    <div className="border border-line rounded-md overflow-hidden">
      <label className="flex items-center gap-2 px-3 py-1.5 bg-panel text-[11px] font-mono text-slate-400 cursor-pointer border-b border-line">
        <input type="checkbox" className="accent-brand" checked={allOn} onChange={() => onAll(shown, !allOn)} disabled={!shown.length} />
        {allOn ? "desmarcar" : "marcar"} todos os {shown.length} visíveis
        <span className="ml-auto">{selected.size} selecionado(s)</span>
      </label>
      <div className="max-h-72 overflow-y-auto divide-y divide-line">
        {!shown.length && <div className="px-3 py-6 text-center text-xs text-slate-500 font-mono">{empty}</div>}
        {shown.map(i => (
          <label key={i.id} className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer text-sm ${selected.has(i.id) ? "bg-brand/10" : "hover:bg-slate-800/40"}`}>
            <input type="checkbox" className="accent-brand shrink-0" checked={selected.has(i.id)} onChange={() => onToggle(i.id)} />
            {render(i)}
          </label>
        ))}
      </div>
    </div>
  );
}

/**
 * Envia (copia) equipamentos, mapas e dashboards para outro usuário.
 * - peer: usuário da linha na tela Usuários (admin escolhe se envia para ele ou traz dele)
 * - preset: itens já marcados ({device_ids, map_ids, dashboard_ids, tab})
 */
export function TransferDialog({ open, onOpenChange, peer = null, preset = {}, onDone }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [targets, setTargets] = useState([]);
  const [targetId, setTargetId] = useState(peer?.id || "");
  const [direction, setDirection] = useState("send");            // send: eu -> peer | fetch: peer -> eu
  const [assets, setAssets] = useState(null);
  const [tab, setTab] = useState(preset.tab || "devices");
  const [sel, setSel] = useState({ devices: new Set(), maps: new Set(), dashboards: new Set() });
  const [q, setQ] = useState("");
  const [creds, setCreds] = useState(true);
  const [busy, setBusy] = useState(false);

  const sourceId = peer && direction === "fetch" ? peer.id : user?.id;
  const target = peer ? (direction === "fetch" ? { id: user?.id, name: "você", role: user?.role } : peer)
                      : targets.find(t => t.id === targetId);
  const toViewer = target?.role === "viewer";

  useEffect(() => {
    if (!open) return;
    setSel({ devices: new Set(preset.device_ids || []), maps: new Set(preset.map_ids || []), dashboards: new Set(preset.dashboard_ids || []) });
    setTab(preset.tab || (preset.map_ids?.length ? "maps" : preset.dashboard_ids?.length ? "dashboards" : "devices"));
    setQ(""); setDirection("send"); setTargetId(peer?.id || "");
    if (!peer) api.get("/transfer/targets").then(r => { setTargets(r.data); if (r.data.length === 1) setTargetId(r.data[0].id); }).catch(() => {});
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open || !sourceId) return;
    setAssets(null);
    api.get("/transfer/assets", { params: sourceId === user?.id ? {} : { user_id: sourceId } })
      .then(r => setAssets(r.data)).catch(e => toast.error(formatApiError(e)));
    if (direction === "fetch") setSel({ devices: new Set(), maps: new Set(), dashboards: new Set() });
  }, [open, sourceId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (toViewer && tab === "devices") setTab("maps"); }, [toViewer]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (k) => (id) => setSel(s => { const n = new Set(s[k]); n.has(id) ? n.delete(id) : n.add(id); return { ...s, [k]: n }; });
  const all = (k) => (items, on) => setSel(s => { const n = new Set(s[k]); items.forEach(i => (on ? n.add(i.id) : n.delete(i.id))); return { ...s, [k]: n }; });
  const count = sel.maps.size + sel.dashboards.size + (toViewer ? 0 : sel.devices.size);
  const tabs = useMemo(() => TABS.filter(t => !(toViewer && t.key === "devices")), [toViewer]);

  const submit = async () => {
    if (!target) return toast.error("Escolha o usuário de destino");
    setBusy(true);
    try {
      const { data } = await api.post("/transfer", {
        target_user_id: target.id, source_user_id: sourceId,
        device_ids: toViewer ? [] : [...sel.devices], map_ids: [...sel.maps], dashboard_ids: [...sel.dashboards],
        include_credentials: creds,
      });
      if (data.mode === "shared") toast.success(`${target.name} agora vê ${data.maps} mapa(s) e ${data.dashboards} dashboard(s)`);
      else {
        const parts = [
          data.devices_created && `${data.devices_created} equipamento(s) novo(s)`,
          data.devices_reused && `${data.devices_reused} já existia(m)`,
          data.maps && `${data.maps} mapa(s)`, data.dashboards && `${data.dashboards} dashboard(s)`,
          data.agents_created && `${data.agents_created} agente(s)`,
        ].filter(Boolean);
        toast.success(`Enviado para ${direction === "fetch" ? "você" : target.name}: ${parts.join(", ") || "nada novo"}`);
      }
      onOpenChange(false); onDone?.(data);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const L = assets || { devices: [], maps: [], dashboards: [] };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl max-h-[92vh] overflow-y-auto" data-testid="transfer-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Send className="w-4 h-4 text-brand-soft" />
          {peer ? `Transferir · ${peer.name}` : "Enviar para outro usuário"}</DialogTitle></DialogHeader>

        {peer && isAdmin && peer.role !== "viewer" && (
          <div className="flex border border-line rounded overflow-hidden text-xs" data-testid="transfer-direction">
            <button onClick={() => setDirection("send")} className={`flex-1 px-3 py-2 ${direction === "send" ? "bg-brand/25 text-slate-100" : "bg-panel text-slate-400"}`}>
              Enviar meus itens para {peer.name}
            </button>
            <button onClick={() => setDirection("fetch")} className={`flex-1 px-3 py-2 ${direction === "fetch" ? "bg-brand/25 text-slate-100" : "bg-panel text-slate-400"}`}>
              Trazer itens de {peer.name} para mim
            </button>
          </div>
        )}

        {!peer && (
          <div>
            <div className="text-xs text-slate-400 mb-1">Para</div>
            <select value={targetId} onChange={e => setTargetId(e.target.value)} data-testid="transfer-target"
                    className="w-full h-9 bg-sunken border border-line rounded px-2 text-sm">
              <option value="">Escolha o usuário…</option>
              {targets.map(t => <option key={t.id} value={t.id}>{t.name}{t.email ? ` · ${t.email}` : ""} ({ROLE_LABEL[t.role] || t.role})</option>)}
            </select>
            {!isAdmin && <div className="text-[11px] text-slate-500 mt-1">Você pode enviar para um administrador.</div>}
          </div>
        )}

        {target && (
          <div className="text-[11px] font-mono text-slate-400 flex items-center gap-1.5 flex-wrap">
            {peer && direction === "fetch" ? peer.name : "você"} <ArrowRight className="w-3 h-3" /> {target.name}
            {toViewer && <span className="ml-2 inline-flex items-center gap-1 text-amber-300"><Eye className="w-3 h-3" /> usuário View: passa a ver os itens ao vivo, sem cópia</span>}
          </div>
        )}

        <div className="flex gap-1 border-b border-line">
          {tabs.map(t => (
            <button key={t.key} onClick={() => { setTab(t.key); setQ(""); }}
                    className={`flex items-center gap-1.5 px-3 py-2 text-sm -mb-px border-b-2 ${tab === t.key ? "border-brand text-slate-100" : "border-transparent text-slate-400"}`}>
              <t.icon className="w-3.5 h-3.5" /> {t.label}
              {sel[t.key].size > 0 && <span className="text-[10px] font-mono px-1.5 rounded bg-brand/25 text-slate-100">{sel[t.key].size}</span>}
            </button>
          ))}
        </div>
        {tab !== "devices" && <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Filtrar…" className="pl-8 h-8 bg-sunken border-line text-sm" />
        </div>}
        {!assets ? <div className="py-8 text-center text-xs text-slate-500 font-mono"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Carregando…</div> : (
          <>
            {tab === "devices" && <TagSelect devices={assets.devices || []} selected={sel.devices} testid="transfer-tags" maxHeight="max-h-64"
              onChange={(ids) => setSel(s => ({ ...s, devices: new Set(ids) }))} />}
            {tab === "maps" && <CheckList items={L.maps} selected={sel.maps} onToggle={toggle("maps")} onAll={all("maps")} q={q}
              empty="Nenhum mapa" render={m => <><span className="truncate">{m.name}</span><span className="ml-auto text-[11px] font-mono text-slate-500 shrink-0">{m.devices} equip.</span></>} />}
            {tab === "dashboards" && <CheckList items={L.dashboards} selected={sel.dashboards} onToggle={toggle("dashboards")} onAll={all("dashboards")} q={q}
              empty="Nenhum dashboard" render={d => <><span className="truncate"><span className="text-slate-500">{d.group} · </span>{d.name}</span><span className="ml-auto text-[11px] font-mono text-slate-500 shrink-0">{d.devices} equip.</span></>} />}
          </>
        )}

        {!toViewer && (
          <div className="space-y-1.5 text-[12px] text-slate-400">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="accent-brand" checked={creds} onChange={e => setCreds(e.target.checked)} data-testid="transfer-creds" />
              Levar as senhas dos equipamentos e agentes (continuam criptografadas)
            </label>
            <div className="text-[11px] text-slate-500">
              É uma <b>cópia</b>: cada um edita a sua. Mapas e dashboards levam junto os equipamentos que usam; equipamento que o
              destino já tem (mesmo IP:porta) é reaproveitado. Equipamentos atrás de agente levam a cadeia de saltos.
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={submit} disabled={busy || !target || !count} className="bg-brand hover:bg-brand-strong" data-testid="transfer-submit">
            {busy ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Send className="w-4 h-4 mr-2" />}
            {toViewer ? `Liberar ${count} item(ns)` : `Enviar ${count} item(ns)`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
