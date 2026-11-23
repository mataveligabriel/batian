import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Loader2, Pencil, Plus, Save, Search, Send, Trash2, Users, X } from "lucide-react";

const BLANK = { name: "", bot: "default", token: "", chat_id: "", thread_id: "", scope: "tags", tags: [], device_ids: [], map_ids: [], submaps: true, kinds: [], exclusive: false, enabled: true };
const inp = "bg-sunken border-line font-mono h-9";
const pill = (on) => `px-2 py-1 rounded border text-[11px] ${on ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400 hover:border-line2"}`;

function Form({ init, meta, devices, maps, onClose, onSaved }) {
  const [f, setF] = useState({ ...BLANK, ...init, token: "", thread_id: init?.thread_id || "" });
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [chats, setChats] = useState(null);
  const set = (p) => setF(s => ({ ...s, ...p }));
  const flip = (key, v) => set({ [key]: f[key].includes(v) ? f[key].filter(x => x !== v) : [...f[key], v] });
  const save = async () => {
    setBusy(true);
    try { init?.id ? await api.put(`/alert-routes/${init.id}`, f) : await api.post("/alert-routes", f); toast.success("Grupo de alerta salvo"); onSaved(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const find = async () => {
    try {
      const { data } = await api.post("/alert-routes/find-chats", { bot: f.bot, token: f.token, route_id: init?.id });
      setChats(data.chats);
      if (!data.chats.length) toast.info("Nenhum grupo encontrado. Adicione o bot ao grupo, mande uma mensagem lá e tente de novo.");
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const [mq, setMq] = useState("");
  const byId = useMemo(() => Object.fromEntries(maps.map(m => [m.id, m])), [maps]);
  const pathOf = (m) => { const p = (m.parents || []).map(x => byId[x]?.name).filter(Boolean); return p.length ? `em ${p.slice(0, 2).join(", ")}` : "mapa principal"; };
  const mapList = useMemo(() => {
    const ql = mq.toLowerCase();
    return maps.filter(m => !ql || m.name.toLowerCase().includes(ql))
      .sort((a, b) => (a.parents?.length ? 1 : 0) - (b.parents?.length ? 1 : 0) || a.name.localeCompare(b.name, "pt-BR", { numeric: true })).slice(0, 400);
  }, [maps, mq]);
  const devList = useMemo(() => {
    const ql = q.toLowerCase();
    return devices.filter(d => !ql || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(ql)).slice(0, 300);
  }, [devices, q]);
  return (
    <div className="rounded-md border border-brand/40 bg-brand/5 p-4 space-y-3" data-testid="route-form">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="text-xs text-slate-400 space-y-1">Nome do grupo<Input value={f.name} onChange={e => set({ name: e.target.value })} placeholder="NOC - Grupo X" className={inp} data-testid="route-name" /></label>
        <div className="text-xs text-slate-400 space-y-1">Bot
          <div className="flex gap-1">
            <button onClick={() => set({ bot: "default" })} className={pill(f.bot === "default")}>o bot do BastiON</button>
            <button onClick={() => set({ bot: "custom" })} className={pill(f.bot === "custom")}>outro bot</button>
          </div>
        </div>
        {f.bot === "custom" && <label className="text-xs text-slate-400 space-y-1">Token do outro bot
          <Input type="password" value={f.token} onChange={e => set({ token: e.target.value.trim() })} placeholder={init?.has_token ? "•••••••• (mantido — digite para trocar)" : "123456:ABC… (via @BotFather)"} className={inp} autoComplete="off" /></label>}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
        <label className="text-xs text-slate-400 space-y-1 md:col-span-1">Chat ID do grupo
          <Input value={f.chat_id} onChange={e => set({ chat_id: e.target.value.trim() })} placeholder="-1001234567890" className={inp} data-testid="route-chat" /></label>
        <label className="text-xs text-slate-400 space-y-1">Tópico (opcional)
          <Input value={f.thread_id} onChange={e => set({ thread_id: e.target.value.replace(/\D/g, "") })} placeholder="só em grupos com tópicos" className={inp} /></label>
        <Button size="sm" variant="outline" onClick={find} className="h-9 border-line bg-transparent"><Search className="w-3.5 h-3.5 mr-1.5" />Descobrir chat ID</Button>
      </div>
      <div className="text-[11px] text-slate-500">
        {f.bot === "default" ? "Adicione o bot do BastiON ao grupo e mande /id lá: ele responde com o chat ID. (Se o Assistente IA estiver desligado, use Descobrir chat ID depois de mandar uma mensagem no grupo.)"
          : "Adicione o outro bot ao grupo, mande uma mensagem lá e clique em Descobrir chat ID."}
      </div>
      {chats?.length > 0 && <div className="flex flex-wrap gap-1.5">{chats.map(c => (
        <button key={c.chat_id} onClick={() => set({ chat_id: c.chat_id, name: f.name || c.title })} className={pill(f.chat_id === c.chat_id)}>{c.title || c.type} <span className="font-mono opacity-70">{c.chat_id}</span></button>))}</div>}

      <div>
        <div className="text-xs text-slate-400 mb-1">Recebe alertas de</div>
        <div className="flex flex-wrap gap-1">
          {[["maps", "mapas de disponibilidade"], ["tags", "equipamentos dos grupos (tags)"], ["devices", "equipamentos específicos"], ["all", "todos os equipamentos"]].map(([k, l]) => (
            <button key={k} onClick={() => set({ scope: k })} className={pill(f.scope === k)} data-testid={`route-scope-${k}`}>{l}</button>))}
        </div>
        {f.scope === "tags" && <div className="flex flex-wrap gap-1 mt-2" data-testid="route-tags">
          {(meta.tags || []).map(t => <button key={t} onClick={() => flip("tags", t)} className={pill(f.tags.includes(t))}>{t}</button>)}
          {!(meta.tags || []).length && <span className="text-[11px] text-slate-500">Nenhuma tag nos equipamentos ainda.</span>}
        </div>}
        {f.scope === "maps" && <div className="mt-2" data-testid="route-maps">
          <Input value={mq} onChange={e => setMq(e.target.value)} placeholder="buscar mapa…" className="h-8 bg-sunken border-line text-xs mb-1" aria-label="Buscar mapa" />
          <div className="max-h-44 overflow-y-auto border border-line rounded divide-y divide-line/60">
            {mapList.map(m => (
              <label key={m.id} className="flex items-center gap-2 px-2 py-1 text-xs cursor-pointer hover:bg-slate-800/40">
                <input type="checkbox" checked={f.map_ids.includes(m.id)} onChange={() => flip("map_ids", m.id)} />
                <span className="text-slate-100 truncate">{m.name}</span>
                <span className="text-slate-500 ml-auto truncate">{pathOf(m)} · {m.summary?.total ?? 0} equip.</span>
              </label>
            ))}
            {!maps.length && <div className="px-2 py-2 text-[11px] text-slate-500">Nenhum mapa de disponibilidade ainda.</div>}
          </div>
          <label className="flex items-center gap-2 mt-1.5 text-xs text-slate-300"><input type="checkbox" checked={f.submaps} onChange={e => set({ submaps: e.target.checked })} data-testid="route-submaps" />
            incluir os submapas (ex.: marcando o mapa principal, alarma todos os POPs dentro dele)</label>
          <div className="text-[10px] text-slate-500 mt-1">{f.map_ids.length} mapa(s) · alarma só os equipamentos e as linhas com SNMP que estão nesses mapas. Equipamento que sai do mapa para de alarmar aqui.</div>
        </div>}
        {f.scope === "devices" && <div className="mt-2">
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar equipamento…" className="h-8 bg-sunken border-line text-xs mb-1" aria-label="Buscar equipamento" />
          <div className="max-h-44 overflow-y-auto border border-line rounded divide-y divide-line/60">
            {devList.map(d => (
              <label key={d.id} className="flex items-center gap-2 px-2 py-1 text-xs cursor-pointer hover:bg-slate-800/40">
                <input type="checkbox" checked={f.device_ids.includes(d.id)} onChange={() => flip("device_ids", d.id)} />
                <span className="text-slate-100 truncate">{d.name}</span><span className="font-mono text-slate-500 ml-auto">{d.host}</span>
              </label>
            ))}
          </div>
          <div className="text-[10px] text-slate-500 mt-1">{f.device_ids.length} selecionado(s)</div>
        </div>}
      </div>
      <div>
        <div className="text-xs text-slate-400 mb-1">Tipos de alerta <span className="text-slate-500">(nenhum marcado = todos)</span></div>
        <div className="flex flex-wrap gap-1">{Object.entries(meta.kinds || {}).map(([k, l]) => <button key={k} onClick={() => flip("kinds", k)} className={pill(f.kinds.includes(k))}>{l}</button>)}</div>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-300">
        {f.scope !== "all" && <label className="flex items-center gap-2"><input type="checkbox" checked={f.exclusive} onChange={e => set({ exclusive: e.target.checked })} data-testid="route-exclusive" /> exclusivo: esses equipamentos não avisam mais no chat principal</label>}
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.enabled} onChange={e => set({ enabled: e.target.checked })} /> ativo</label>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose} className="h-9 text-slate-300"><X className="w-4 h-4 mr-1" />Cancelar</Button>
        <Button onClick={save} disabled={busy} className="h-9 bg-brand hover:bg-brand-strong" data-testid="route-save">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}Salvar grupo</Button>
      </div>
    </div>
  );
}

/** Outros grupos do Telegram (mesmo bot ou outro) que recebem só os alertas de alguns equipamentos. */
export function AlertRoutesCard() {
  const [meta, setMeta] = useState({ routes: [], kinds: {}, tags: [] });
  const [devices, setDevices] = useState([]);
  const [form, setForm] = useState(null);
  const [maps, setMaps] = useState([]);
  const [sp, setSp] = useSearchParams();
  useEffect(() => {
    api.get("/avail/maps").then(r => {
      const rows = r.data?.maps || [];
      setMaps(rows);
      const pre = sp.get("grupo_mapa");             // atalho "Telegram deste mapa" da Disponibilidade
      if (pre) {
        const m = rows.find(x => x.id === pre);
        if (m) setForm({ name: `Mapa ${m.name}`, scope: "maps", map_ids: [m.id], submaps: true });
        const n = new URLSearchParams(sp); n.delete("grupo_mapa"); setSp(n, { replace: true });
        setTimeout(() => document.querySelector("[data-testid=route-form]")?.scrollIntoView({ behavior: "smooth", block: "center" }), 300);
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const load = () => api.get("/alert-routes").then(r => setMeta(r.data)).catch(e => toast.error(formatApiError(e)));
  useEffect(() => { load(); api.get("/devices").then(r => setDevices((r.data || []).sort((a, b) => a.name.localeCompare(b.name, "pt-BR", { numeric: true })))).catch(() => {}); }, []);
  const test = async (r) => {
    try { const { data } = await api.post(`/alert-routes/${r.id}/test`); (data.ok ? toast.success : toast.error)(data.ok ? `Teste enviado para ${r.name}` : `Telegram: ${data.result}`); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (r) => { if (!window.confirm(`Apagar o grupo de alerta "${r.name}"?`)) return; try { await api.delete(`/alert-routes/${r.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const scopeText = (r) => r.scope === "all" ? "todos os equipamentos" : r.scope === "tags" ? `grupos: ${r.tags.join(", ")}`
    : r.scope === "maps" ? `mapa(s): ${(r.map_ids || []).slice(0, 3).map(i => maps.find(m => m.id === i)?.name || "?").join(", ")}${(r.map_ids || []).length > 3 ? "…" : ""}${r.submaps === false ? " (sem submapas)" : " e submapas"}`
    : `${r.device_ids.length} equipamento(s): ${r.device_ids.slice(0, 4).map(i => devices.find(d => d.id === i)?.name || "?").join(", ")}${r.device_ids.length > 4 ? "…" : ""}`;
  return (
    <Card className="bg-surface border-line mt-6 p-5" data-testid="alert-routes">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <Users className="w-4 h-4 text-brand-soft" /><div className="text-xs text-slate-400">Grupos de alerta do Telegram</div>
        {!form && <Button size="sm" onClick={() => setForm({})} className="ml-auto h-8 bg-brand hover:bg-brand-strong" data-testid="route-new"><Plus className="w-3.5 h-3.5 mr-1" />Novo grupo</Button>}
      </div>
      <p className="text-[11px] text-slate-500 mb-3 max-w-3xl">
        Além do chat principal acima, mande os alertas de alguns equipamentos para outros grupos — com o mesmo bot ou com outro bot. Útil para um grupo só de um cliente,
        de uma equipe, de um POP ou de um mapa de disponibilidade. Valem para interface, equipamento, configuração alterada e incidentes; avisos do sistema continuam só no chat principal.
      </p>
      {form && <Form key={form.id || "new"} init={form} meta={meta} devices={devices} maps={maps} onClose={() => setForm(null)} onSaved={() => { setForm(null); load(); }} />}
      <div className="divide-y divide-line">
        {meta.routes.map(r => (
          <div key={r.id} className={`py-2.5 flex flex-wrap items-center gap-2 ${r.enabled ? "" : "opacity-50"}`} data-testid={`route-${r.id}`}>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-slate-100">{r.name}
                <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded border border-line text-slate-400">{r.bot === "custom" ? "outro bot" : "bot do BastiON"}</span>
                {r.exclusive && <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded border border-amber-500/50 text-amber-300">exclusivo</span>}
                {!r.enabled && <span className="ml-1 text-[10px] text-slate-500">desativado</span>}
              </div>
              <div className="text-[11px] text-slate-400 truncate"><span className="font-mono">{r.chat_id}</span> · {scopeText(r)} · {r.kinds.length ? r.kinds.map(k => meta.kinds[k]).join(", ") : "todos os tipos"}</div>
            </div>
            <Button size="sm" variant="ghost" onClick={() => test(r)} className="h-8 text-slate-300"><Send className="w-3.5 h-3.5 mr-1" />Testar</Button>
            <Button size="sm" variant="ghost" onClick={() => setForm(r)} className="h-8 text-slate-300"><Pencil className="w-3.5 h-3.5" /></Button>
            <Button size="sm" variant="ghost" onClick={() => del(r)} className="h-8 text-red-300"><Trash2 className="w-3.5 h-3.5" /></Button>
          </div>
        ))}
        {!meta.routes.length && !form && <div className="py-3 text-sm text-slate-500">Nenhum grupo extra. Hoje todos os alertas vão para o chat principal.</div>}
      </div>
    </Card>
  );
}
