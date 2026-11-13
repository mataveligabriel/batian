import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Copy, Download, ExternalLink, KeyRound, Loader2, Pencil, Plus, Save, Trash2, Tv, X } from "lucide-react";

const ROT = [[0, "não trocar"], [15, "15 s"], [30, "30 s"], [60, "1 min"], [120, "2 min"], [300, "5 min"]];
const linkOf = (t) => `${window.location.origin}/tv/${t}`;
const ago = (iso) => {
  if (!iso) return "nunca aberto";
  const s = Math.round((Date.now() - new Date(iso)) / 1000);
  return s < 120 ? "aberto agora" : s < 3600 ? `visto há ${Math.round(s / 60)} min` : s < 86400 ? `visto há ${Math.round(s / 3600)} h` : `visto em ${new Date(iso).toLocaleDateString("pt-BR")}`;
};
const esc = (t) => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// arquivo .html para a TV (pendrive, navegador da smart TV, Chromecast…): abre o telão em tela cheia e recarrega se cair
function tvHtml(name, url) {
  return `<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(name)} · BastiON</title>
<style>html,body{margin:0;height:100%;background:#0b1220;color:#cbd5e1;font-family:system-ui,sans-serif}
iframe{border:0;width:100%;height:100%;display:block}#off{position:fixed;inset:0;display:none;align-items:center;justify-content:center;text-align:center}</style></head>
<body><iframe id="f" src="${esc(url)}" allow="fullscreen; screen-wake-lock"></iframe>
<div id="off"><div><h2>Sem conexão com o BastiON</h2><p>Tentando de novo…</p></div></div>
<script>
// confere a cada minuto se o BastiON responde; se a rede cair, mostra o aviso e recarrega quando voltar
var URL_TV=${JSON.stringify(url)},down=false,f=document.getElementById("f"),off=document.getElementById("off");
function check(){fetch(URL_TV.replace("/tv/","/api/public/tv/"),{mode:"no-cors",cache:"no-store"}).then(function(){if(down){down=false;off.style.display="none";f.style.display="block";f.src=URL_TV;}})
.catch(function(){down=true;off.style.display="flex";f.style.display="none";});}
setInterval(check,60000);document.addEventListener("dblclick",function(){document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen();});
</script></body></html>
`;
}

function Form({ maps, init, onCancel, onSaved }) {
  const [f, setF] = useState(() => ({ name: init?.name || "", map_ids: init?.map_ids || [], rotate_sec: init?.rotate_sec ?? 30, show_hosts: !!init?.show_hosts, enabled: init?.enabled ?? true }));
  const [busy, setBusy] = useState(false);
  const toggle = (id) => setF(s => ({ ...s, map_ids: s.map_ids.includes(id) ? s.map_ids.filter(x => x !== id) : [...s.map_ids, id] }));
  const save = async () => {
    setBusy(true);
    try { const { data } = init?.id ? await api.put(`/maps-tv/${init.id}`, f) : await api.post("/maps-tv", f); toast.success("Link do mapa salvo"); onSaved(data); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <div className="rounded-lg border border-brand/40 bg-surface p-4 space-y-3" data-testid="tv-form">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="text-xs text-slate-400 space-y-1">Nome do link<Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="NOC - Backbone" className="bg-sunken border-line h-9" data-testid="tv-name" /></label>
        <label className="text-xs text-slate-400 space-y-1">Trocar de mapa a cada
          <select value={f.rotate_sec} onChange={e => setF({ ...f, rotate_sec: Number(e.target.value) })} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100">
            {ROT.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <div className="flex flex-col justify-end gap-1.5 text-xs text-slate-300 pb-1">
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.show_hosts} onChange={e => setF({ ...f, show_hosts: e.target.checked })} /> mostrar o IP dos equipamentos</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} /> link ativo</label>
        </div>
      </div>
      <div>
        <div className="text-xs text-slate-400 mb-1">Mapas que aparecem (na ordem em que você marcar)</div>
        <div className="flex flex-wrap gap-1.5">
          {maps.map(m => {
            const pos = f.map_ids.indexOf(m.id);
            return (
              <button key={m.id} onClick={() => toggle(m.id)} data-testid={`tv-map-${m.id}`}
                      className={`px-2.5 py-1 rounded-md border text-xs ${pos >= 0 ? "bg-brand border-brand text-onbrand" : "border-line text-slate-300 hover:border-line2"}`}>
                {pos >= 0 && <b className="mr-1">{pos + 1}.</b>}{m.name}
              </button>
            );
          })}
          {!maps.length && <span className="text-xs text-slate-500">Crie um mapa primeiro.</span>}
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} className="h-9 text-slate-300"><X className="w-4 h-4 mr-1" />Cancelar</Button>
        <Button onClick={save} disabled={busy || !f.map_ids.length} className="h-9 bg-brand hover:bg-brand-strong" data-testid="tv-save">
          {busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}{init?.id ? "Salvar" : "Criar link"}
        </Button>
      </div>
    </div>
  );
}

export function TvShares({ maps, preselect }) {
  const [rows, setRows] = useState(null);
  const [form, setForm] = useState(preselect ? { map_ids: [preselect.id], name: preselect.name } : null);
  const load = () => api.get("/maps-tv").then(r => setRows(r.data)).catch(e => { toast.error(formatApiError(e)); setRows([]); });
  useEffect(() => { load(); }, []);
  const copy = async (t) => { try { await navigator.clipboard.writeText(t); toast.success("Link copiado"); } catch { toast.error("Não consegui copiar"); } };
  const dl = (r) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([tvHtml(r.name, linkOf(r.token))], { type: "text/html;charset=utf-8" }));
    a.download = `telao-${r.name.toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "bastion"}.html`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const newLink = async (r) => {
    if (!window.confirm("Gerar outro endereço? O link atual para de funcionar na hora (quem usa vai precisar do novo).")) return;
    try { await api.post(`/maps-tv/${r.id}/new-link`); toast.success("Novo link gerado"); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (r) => {
    if (!window.confirm(`Apagar o link "${r.name}"? Quem estiver com ele aberto para de ver o mapa.`)) return;
    try { await api.delete(`/maps-tv/${r.id}`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const mapName = (id) => maps.find(m => m.id === id)?.name || "(mapa excluído)";
  return (
    <div className="space-y-3 max-w-5xl" data-testid="tv-shares">
      <div className="flex flex-wrap items-start gap-3">
        <div className="text-sm text-slate-400 max-w-3xl">
          Link para abrir o mapa em qualquer PC, notebook ou TV <b className="text-slate-200">sem logar no BastiON</b>: tela cheia, tráfego ao vivo dos enlaces a cada 10 s,
          contador de enlaces down e acima de 80% e troca automática entre os mapas escolhidos. Copie o link e abra no navegador, ou baixe o <b className="text-slate-200">.html</b> (pendrive, Chromecast, mini PC).
        </div>
        {!form && <Button onClick={() => setForm({})} className="ml-auto h-9 bg-brand hover:bg-brand-strong" data-testid="tv-new"><Plus className="w-4 h-4 mr-1.5" />Novo link</Button>}
      </div>
      {form && <Form maps={maps} init={form} onCancel={() => setForm(null)} onSaved={() => { setForm(null); load(); }} />}
      {!rows ? <Loader2 className="w-4 h-4 animate-spin text-slate-400" /> : !rows.length && !form ? (
        <div className="text-sm text-slate-500 border border-dashed border-line rounded-lg p-6 text-center">Nenhum link ainda. Clique em <b>Novo link</b> e escolha os mapas.</div>
      ) : rows.map(r => (
        <div key={r.id} className={`rounded-lg border border-line bg-surface p-4 space-y-2 ${r.enabled ? "" : "opacity-60"}`} data-testid={`tv-row-${r.id}`}>
          <div className="flex flex-wrap items-center gap-2">
            <Tv className="w-4 h-4 text-brand-soft" /><span className="text-base font-semibold text-slate-100">{r.name}</span>
            {!r.enabled && <span className="text-[10px] px-1.5 rounded border border-line text-slate-400">desativado</span>}
            <span className="text-[11px] text-slate-500">{ago(r.last_seen)}{r.last_ip ? ` · ${r.last_ip}` : ""}</span>
            <div className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setForm(r)} className="h-8 text-slate-300"><Pencil className="w-3.5 h-3.5 mr-1" />Editar</Button>
              <Button size="sm" variant="ghost" onClick={() => newLink(r)} className="h-8 text-slate-300" title="Gerar outro endereço e invalidar o atual"><KeyRound className="w-3.5 h-3.5 mr-1" />Trocar endereço</Button>
              <Button size="sm" variant="ghost" onClick={() => del(r)} className="h-8 text-red-300"><Trash2 className="w-3.5 h-3.5" /></Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {r.map_ids.map((id, i) => <span key={id} className="px-1.5 py-0.5 rounded border border-line text-slate-300">{i + 1}. {mapName(id)}</span>)}
            <span className="text-slate-500 self-center">· {r.rotate_sec ? `troca a cada ${r.rotate_sec >= 60 ? `${r.rotate_sec / 60} min` : `${r.rotate_sec} s`}` : "sem troca automática"}{r.show_hosts ? " · mostra IPs" : ""}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded-md bg-sunken border border-line px-2 py-1.5 text-xs text-slate-300" data-testid="tv-link">{r.token ? linkOf(r.token) : "link indisponível — gere um novo"}</code>
            <Button size="sm" onClick={() => copy(linkOf(r.token))} disabled={!r.token} className="h-8 bg-brand hover:bg-brand-strong"><Copy className="w-3.5 h-3.5 mr-1" />Copiar</Button>
            <Button size="sm" variant="outline" onClick={() => window.open(linkOf(r.token), "_blank", "noopener")} disabled={!r.token} className="h-8 border-line bg-transparent"><ExternalLink className="w-3.5 h-3.5 mr-1" />Abrir</Button>
            <Button size="sm" variant="outline" onClick={() => dl(r)} disabled={!r.token} className="h-8 border-line bg-transparent" data-testid="tv-html"><Download className="w-3.5 h-3.5 mr-1" />.html</Button>
          </div>
        </div>
      ))}
      <div className="text-[11px] text-slate-500 max-w-3xl">
        Quem tiver o link vê os mapas escolhidos (nomes, status e tráfego) — não dá acesso a mais nada do BastiON. Se o link vazar, use <b>Trocar endereço</b>.
        Na tela do link: setas trocam de mapa, espaço pausa a troca e F (ou duplo clique no .html) põe em tela cheia.
      </div>
    </div>
  );
}
