import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, ChevronRight, Copy, Download, FileText, FolderOpen, Loader2, RefreshCw, RotateCcw, ScanSearch, Send, Trash2, Undo2, XCircle } from "lucide-react";

const VENDOR = { zte: "ZTE", huawei: "Huawei", fiberhome: "FiberHome" };
const KIND_OF = { zte: "zte-titan", huawei: "huawei", fiberhome: "fiberhome" };
const sec = "rounded-lg border border-line bg-sunken/40 p-4 space-y-3";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100 min-w-0";
const inp = "bg-sunken border-line font-mono h-9";
const small = "bg-sunken border-line font-mono h-8 text-xs";
const ponKey = (p) => p.split("/").map(Number);
const cmpPon = (a, b) => { const x = ponKey(a), y = ponKey(b); for (let i = 0; i < Math.max(x.length, y.length); i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0); } return 0; };
const fmtAt = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const download = (text, name) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

function Step({ n, title, children, right }) {
  return (
    <section className={sec}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-6 h-6 rounded-full bg-brand/20 text-brand-soft text-xs font-semibold flex items-center justify-center">{n}</span>
        <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
        <div className="ml-auto flex flex-wrap gap-2">{right}</div>
      </div>
      {children}
    </section>
  );
}

/** Próxima PON: ZTE/FiberHome "S/P" -> "S/P+1"; Huawei "F/S/P" -> "F/S/P+1". */
const nextPon = (p, step = 1) => { const x = p.split("/"); x[x.length - 1] = String(Number(x[x.length - 1]) + step); return x.join("/"); };

/** Script de uma PON do plano: ver, copiar, baixar. */
function ScriptView({ data, onClose }) {
  const copy = async () => { try { await navigator.clipboard.writeText(data.script); toast.success("Script copiado"); } catch { toast.error("Não consegui copiar"); } };
  return (
    <div className="mt-2 rounded-md border border-line bg-canvas" data-testid="mig-script">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-line">
        <FileText className="w-4 h-4 text-brand-soft" /><span className="text-xs font-mono text-slate-200">{data.filename}</span>
        <span className="text-[11px] text-slate-500 font-mono">{data.script.split("\n").length - 1} linhas</span>
        <div className="ml-auto flex gap-1.5">
          <Button size="sm" onClick={copy} className="h-7 bg-brand hover:bg-brand-strong"><Copy className="w-3.5 h-3.5 mr-1" />Copiar</Button>
          <Button size="sm" variant="outline" onClick={() => download(data.script, data.filename)} className="h-7 border-line bg-transparent"><Download className="w-3.5 h-3.5 mr-1" />.txt</Button>
          <Button size="sm" variant="ghost" onClick={onClose} className="h-7 text-slate-400">fechar</Button>
        </div>
      </div>
      {data.warnings?.length > 0 && <div className="px-3 pt-2 space-y-1">{data.warnings.map((w, i) => <div key={i} className="flex gap-1.5 text-[11px] text-amber-200"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}</div>)}</div>}
      <pre className="m-0 px-3 py-2 max-h-80 overflow-auto text-[12px] leading-relaxed font-mono text-slate-200 whitespace-pre">
        {data.script.split("\n").map((l, i) => <div key={i} className={/^[!#] /.test(l) ? "text-slate-500" : ""}>{l || " "}</div>)}
      </pre>
    </div>
  );
}

/** Uma PON de destino do plano: lista de ONUs, scripts e ações (aplicar, conferir, remover na origem, desfazer). */
function GroupCard({ mig, g, onChanged, sameOlt }) {
  const [open, setOpen] = useState(false);
  const [script, setScript] = useState(null);
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState("");
  const [verify, setVerify] = useState(null);
  const [fail, setFail] = useState(null);
  const st = (mig.status || {})[g.dst.replace(/\//g, "_")] || {};
  const ok = g.onus.filter(o => o.ok).length;
  const canRemove = !!mig.src_device_id;
  const show = async (which) => {
    setBusy(`s-${which}`);
    try { setScript((await api.get(`/oltmig/${mig.id}/script`, { params: { dst: g.dst, which } })).data); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const act = async (which) => {
    setConfirm(""); setBusy(which); setFail(null);
    try {
      const { data } = await api.post(`/oltmig/${mig.id}/apply`, { dst: g.dst, which });
      if (data.ok) toast.success(`${{ dest: "Autorizadas", remove: "Removidas", rollback: "Desfeito" }[which]} em ${data.device}`);
      else { toast.error(`A OLT recusou: ${data.failed?.command}`); setFail(data.failed); }
      onChanged();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const check = async () => {
    setBusy("verify");
    try { setVerify((await api.post(`/oltmig/${mig.id}/verify`, { dst: g.dst })).data); setOpen(true); onChanged(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const vmap = Object.fromEntries((verify?.rows || []).map(r => [r.dst_onu, r.state]));
  const badge = (s, label) => s && (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${s.ok ? "border-emerald-700 text-emerald-300" : "border-red-700 text-red-300"}`} title={`${s.by || ""} · ${fmtAt(s.at)}`}>
      {s.ok ? "✓" : "✗"} {label} {fmtAt(s.at)}
    </span>
  );
  const ConfirmBtn = ({ which, label, danger, icon: I, disabled }) => (confirm === which ? (
    <span className="inline-flex gap-1">
      <Button size="sm" onClick={() => act(which)} className={`h-8 ${danger ? "bg-red-700 hover:bg-red-600" : "bg-emerald-700 hover:bg-emerald-600"}`} data-testid={`mig-confirm-${which}`}>
        Confirmar: {label.toLowerCase()} {ok} ONU(s) em {which === "remove" ? mig.src_device : mig.dst_device}
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setConfirm("")} className="h-8 text-slate-300">Cancelar</Button>
    </span>
  ) : (
    <Button size="sm" variant="outline" disabled={disabled || !!busy} onClick={() => setConfirm(which)} className={`h-8 border-line bg-transparent ${danger ? "text-red-300" : "text-emerald-300"}`} data-testid={`mig-${which}`}>
      {busy === which ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <I className="w-3.5 h-3.5 mr-1" />}{label}
    </Button>
  ));
  return (
    <div className="rounded-lg border border-line bg-surface" data-testid={`mig-group-${g.dst}`}>
      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
        <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-sm text-slate-100 font-mono" aria-expanded={open}>
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          {g.src.join(" + ")} <ArrowRight className="w-3.5 h-3.5 text-slate-500" /> <span className="text-brand-soft">{g.dst}</span>
        </button>
        <span className="text-xs text-slate-400">{g.onus.length} ONU(s){g.errors ? <span className="text-red-300"> · {g.errors} com pendência</span> : ""}{g.warnings ? <span className="text-amber-300"> · {g.warnings} com aviso</span> : ""}</span>
        <div className="flex flex-wrap gap-1">
          {badge(st.dest, "destino")}
          {st.verify && <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-line text-slate-300" title={fmtAt(st.verify.at)}>online {st.verify.online}/{st.verify.online + st.verify.offline + st.verify.missing}</span>}
          {badge(st.remove, "origem removida")}
          {badge(st.rollback, "desfeito")}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 px-3 pb-2.5">
        <Button size="sm" variant="outline" onClick={() => show("dest")} disabled={!ok || !!busy} className="h-8 border-line bg-transparent"><FileText className="w-3.5 h-3.5 mr-1" />Script do destino</Button>
        <ConfirmBtn which="dest" label="Aplicar no destino" icon={Send} disabled={!ok} />
        <Button size="sm" variant="outline" onClick={check} disabled={!ok || !!busy} className="h-8 border-line bg-transparent" data-testid="mig-verify">
          {busy === "verify" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}Conferir no destino
        </Button>
        <span className="w-px h-6 bg-line mx-1" />
        <Button size="sm" variant="outline" onClick={() => show("remove")} disabled={!ok || !canRemove || !!busy} className="h-8 border-line bg-transparent" title={canRemove ? "" : "Origem por planilha: sem OLT de origem"}><FileText className="w-3.5 h-3.5 mr-1" />Script de remoção na origem</Button>
        <ConfirmBtn which="remove" label="Remover na origem" icon={Trash2} danger disabled={!ok || !canRemove || (!sameOlt && !st.dest?.ok)} />
        <Button size="sm" variant="ghost" onClick={() => show("rollback")} disabled={!ok || !!busy} className="h-8 text-slate-400"><Undo2 className="w-3.5 h-3.5 mr-1" />Desfazer (script)</Button>
        {st.dest?.ok && <ConfirmBtn which="rollback" label="Desfazer no destino" icon={RotateCcw} danger />}
      </div>
      {fail && (
        <div className="mx-3 mb-2 rounded-md border border-red-800/60 bg-red-950/30 px-3 py-2 text-xs text-red-200">
          <div><XCircle className="w-3.5 h-3.5 inline mr-1" />A OLT recusou <span className="font-mono">{fail.command}</span> — parei aí e <b>não gravei</b>. As linhas anteriores ficaram na configuração em uso.</div>
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-red-100/80 max-h-32 overflow-auto m-0">{fail.output}</pre>
        </div>
      )}
      {verify && (
        <div className="mx-3 mb-2 text-xs flex flex-wrap gap-3" data-testid="mig-verify-result">
          <span className="text-emerald-300"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1" />{verify.online} online</span>
          <span className="text-amber-300">{verify.offline} offline</span>
          <span className="text-slate-400">{verify.missing} não aparecem no destino</span>
          <span className="text-slate-500">lido {fmtAt(verify.at)}</span>
        </div>
      )}
      {script && <div className="px-3 pb-3"><ScriptView data={script} onClose={() => setScript(null)} /></div>}
      {open && (
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full text-xs font-mono" data-testid="mig-onus">
            <thead className="text-[10px] text-slate-500"><tr>
              <th className="text-left px-3 py-1.5 font-normal">Origem</th><th className="text-left px-2 font-normal">Destino</th><th className="text-left px-2 font-normal">Serial</th>
              <th className="text-left px-2 font-normal">Nome</th><th className="text-left px-2 font-normal">Tipo</th><th className="text-left px-2 font-normal">VLANs</th>
              {verify && <th className="text-left px-2 font-normal">No destino</th>}<th className="text-left px-2 font-normal">Situação</th>
            </tr></thead>
            <tbody>
              {g.onus.map(o => (
                <tr key={`${o.src_pon}:${o.src_onu}`} className="border-t border-line/70 align-top">
                  <td className="px-3 py-1 text-slate-300 whitespace-nowrap">{o.src_pon}:{o.src_onu}</td>
                  <td className="px-2 py-1 text-slate-100 whitespace-nowrap">{o.dst_onu != null ? `${o.dst_pon}:${o.dst_onu}` : "—"}</td>
                  <td className="px-2 py-1 text-slate-200">{o.sn || <span className="text-red-300">{o.sn_raw || "—"}</span>}</td>
                  <td className="px-2 py-1 text-slate-200 max-w-[220px] truncate" title={o.name}>{o.name}</td>
                  <td className="px-2 py-1 text-slate-400 whitespace-nowrap">{o.src_type && o.src_type !== o.type ? `${o.src_type} → ` : ""}<span className="text-slate-200">{o.type || (o.line != null ? `line ${o.line}/srv ${o.srv}` : "—")}</span></td>
                  <td className="px-2 py-1 text-slate-300 whitespace-nowrap">{o.services.map(s => `${s.src_vlan !== s.vlan ? `${s.src_vlan}→` : ""}${s.vlan}${s.mode === "router" ? "(r)" : s.mode === "transparent" ? "(t)" : ""}`).join(" ")}</td>
                  {verify && <td className={`px-2 py-1 whitespace-nowrap ${vmap[o.dst_onu] === "online" ? "text-emerald-300" : vmap[o.dst_onu] === "offline" ? "text-amber-300" : "text-slate-500"}`}>{o.ok ? vmap[o.dst_onu] || "—" : ""}</td>}
                  <td className="px-2 py-1 font-sans">
                    {o.errors.map((e, i) => <div key={i} className="text-red-300">✗ {e}</div>)}
                    {o.warnings.map((w, i) => <div key={i} className="text-amber-200/90">! {w}</div>)}
                    {o.ok && !o.warnings.length && <span className="text-emerald-400">ok</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-3 py-1.5 text-[10px] text-slate-500">(r) roteada · (t) transparente/com tag · ONUs com pendência ficam fora dos scripts</div>
        </div>
      )}
    </div>
  );
}

/** Aba Migração: ONUs de uma OLT para outra (qualquer fabricante), escolhendo as PONs — dá para migrar por partes. */
export function OltMigration({ devices }) {
  const olts = useMemo(() => devices.filter(d => VENDOR[d.device_type]), [devices]);
  const [meta, setMeta] = useState(null);
  const [list, setList] = useState([]);
  const [src, setSrc] = useState({ device_id: "", mode: "backup", csv: "", vendor: "zte" });
  const [srcRes, setSrcRes] = useState(null);
  const [reading, setReading] = useState(false);
  const [pons, setPons] = useState({});                    // pon de origem -> {on, dst}
  const [seqStart, setSeqStart] = useState("");
  const [dst, setDst] = useState({ device_id: "", kind: "", id_mode: "keep", name_from: "name", fh_dir: "onu" });
  const [maps, setMaps] = useState({ vlan_map: {}, type_map: {}, tcont_map: {}, default_type: "", default_tcont: "", hw_profiles: {}, default_line: "", default_srv: "", hw_gem: {}, default_service: { vlan: "", mode: "bridge", ports: "1" } });
  const [name, setName] = useState("");
  const [mig, setMig] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [showList, setShowList] = useState(false);
  const setMap = (k, key, v) => setMaps(m => ({ ...m, [k]: { ...m[k], [key]: v } }));
  const dstDev = olts.find(d => d.id === dst.device_id);
  const kind = dst.kind || (dstDev ? KIND_OF[dstDev.device_type] : "");
  const zteDst = kind.startsWith("zte");

  const loadList = () => api.get("/oltmig").then(r => setList(r.data || [])).catch(() => {});
  useEffect(() => { api.get("/oltmig/meta").then(r => setMeta(r.data)).catch(() => {}); loadList(); }, []);

  const read = async () => {
    setReading(true); setSrcRes(null); setMig(null);
    try {
      const { data } = await api.post("/oltmig/source", src);
      setSrcRes(data);
      setPons(Object.fromEntries(data.summary.pons.map(p => [p.pon, { on: false, dst: "" }])));
      toast.success(`${data.summary.onus} ONU(s) em ${data.summary.pons.length} PON(s)`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setReading(false); }
  };
  const chosen = Object.entries(pons).filter(([, v]) => v.on).map(([k]) => k).sort(cmpPon);
  const chosenSet = new Set(chosen);
  const selOnus = useMemo(() => (srcRes?.onus || []).filter(o => chosenSet.has(o.pon)), [srcRes, chosen.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const uniq = (f) => { const m = {}; selOnus.forEach(o => f(o).forEach(k => { if (k !== "" && k != null) m[k] = (m[k] || 0) + 1; })); return Object.entries(m).sort((a, b) => b[1] - a[1]); };
  const vlans = uniq(o => o.services.map(s => s.vlan)).sort((a, b) => Number(a[0]) - Number(b[0]));
  const types = uniq(o => [o.type || "?"]);
  const tconts = uniq(o => [o.tcont]);
  const noSvc = selOnus.filter(o => !o.services.length).length;

  const setPon = (p, patch) => setPons(s => ({ ...s, [p]: { ...s[p], ...patch } }));
  const allOn = (on) => setPons(s => Object.fromEntries(Object.entries(s).map(([k, v]) => [k, { ...v, on }])));
  const sameNumbers = () => setPons(s => Object.fromEntries(Object.entries(s).map(([k, v]) => {
    if (!v.on) return [k, v];
    let d = k;
    if (kind === "huawei" && k.split("/").length === 2) d = `0/${k}`;
    if (kind !== "huawei" && k.split("/").length === 3) d = k.split("/").slice(1).join("/");
    return [k, { ...v, dst: d }];
  })));
  const sequential = () => {
    if (!seqStart.trim()) { toast.error("Informe a primeira PON de destino"); return; }
    let cur = seqStart.trim();
    const next = { ...pons };
    chosen.forEach(k => { next[k] = { ...next[k], dst: cur }; cur = nextPon(cur); });
    setPons(next);
  };

  const plan = async (id) => {
    if (!srcRes) return;
    setPlanning(true);
    const opts = {
      kind, id_mode: dst.id_mode, name_from: dst.name_from, fh_dir: dst.fh_dir,
      pons: Object.fromEntries(chosen.map(k => [k, pons[k].dst])),
      vlan_map: maps.vlan_map, type_map: maps.type_map, tcont_map: maps.tcont_map, default_type: maps.default_type, default_tcont: maps.default_tcont,
      hw_profiles: maps.hw_profiles, default_line: maps.default_line === "" ? null : Number(maps.default_line), default_srv: maps.default_srv === "" ? null : Number(maps.default_srv),
      hw_gem: maps.hw_gem,
      default_service: maps.default_service.vlan ? { vlan: Number(maps.default_service.vlan), mode: maps.default_service.mode, ports: String(maps.default_service.ports).split(/[\s,]+/).filter(Boolean).map(Number) } : {},
    };
    try {
      const { data } = await api.post("/oltmig/plan", {
        id: id || "", name, src_device_id: srcRes.device_id || "", src_vendor: srcRes.vendor, src_platform: srcRes.platform,
        source_kind: srcRes.mode, source_at: srcRes.source_at, onus: srcRes.onus, dst_device_id: dst.device_id, opts,
      });
      setMig(data); loadList();
      toast.success(`Plano salvo: ${data.plan.ok} de ${data.plan.total} ONU(s) prontas`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setPlanning(false); }
  };
  const refreshMig = async () => { if (mig) { try { const { data } = await api.get(`/oltmig/${mig.id}`); setMig(data); loadList(); } catch { /* fica o que tem */ } } };
  const openSaved = async (m) => {
    try {
      const { data } = await api.get(`/oltmig/${m.id}`);
      setMig(data); setShowList(false); setName(data.name);
      setSrcRes({ vendor: data.src_vendor, platform: data.src_platform, device: data.src_device, device_id: data.src_device_id, mode: data.source_kind,
                  source_at: data.source_at, onus: data.onus, summary: { onus: data.onus.length, pons: [] } });
      const o = data.opts || {};
      setDst({ device_id: data.dst_device_id, kind: o.kind || "", id_mode: o.id_mode || "keep", name_from: o.name_from || "name", fh_dir: o.fh_dir || "onu" });
      const ponsAll = [...new Set(data.onus.map(x => x.pon))].sort(cmpPon);
      setPons(Object.fromEntries(ponsAll.map(p => [p, { on: !!(o.pons || {})[p], dst: (o.pons || {})[p] || "" }])));
      setMaps(mm => ({ ...mm, ...o, default_line: o.default_line ?? "", default_srv: o.default_srv ?? "",
                       default_service: o.default_service?.vlan ? { vlan: o.default_service.vlan, mode: o.default_service.mode || "bridge", ports: (o.default_service.ports || [1]).join(",") } : mm.default_service }));
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const remove = async (m) => {
    if (!window.confirm(`Apagar o plano "${m.name}"? (não mexe nas OLTs)`)) return;
    try { await api.delete(`/oltmig/${m.id}`); if (mig?.id === m.id) setMig(null); loadList(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const downloadAll = async (which) => {
    const parts = [];
    for (const g of mig.plan.groups) {
      try { parts.push((await api.get(`/oltmig/${mig.id}/script`, { params: { dst: g.dst, which } })).data.script); } catch { /* PON sem ONU pronta */ }
    }
    if (!parts.length) { toast.error("Nenhum script para baixar"); return; }
    download(parts.join("\n"), `migracao-${which === "dest" ? "destino" : "remover-origem"}-todas.txt`);
  };

  const summaryPons = srcRes ? (srcRes.summary.pons.length ? srcRes.summary.pons : Object.keys(pons).sort(cmpPon).map(p => ({ pon: p, onus: srcRes.onus.filter(o => o.pon === p).length, vlans: {}, types: {}, no_sn: 0 }))) : [];
  const sameOlt = !!(mig && mig.src_device_id && mig.src_device_id === mig.dst_device_id);
  const ready = srcRes && chosen.length && dst.device_id && kind && chosen.every(k => pons[k].dst.trim());

  return (
    <div className="px-4 md:px-6 py-4 space-y-4 max-w-[1400px]" data-testid="oltmig">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs text-slate-400 max-w-3xl">Leve as ONUs de uma OLT para outra — ZTE C300 → C600, Huawei → ZTE, FiberHome → ZTE, ZTE → Huawei… Escolha só as PONs que vai mudar agora
          (dá para migrar por partes), revise o plano e aplique PON por PON: <b>autorizar no destino → mudar a fibra → conferir → remover na origem</b>.</p>
        <Button size="sm" variant="outline" onClick={() => setShowList(!showList)} className="ml-auto h-8 border-line bg-transparent" data-testid="mig-saved">
          <FolderOpen className="w-3.5 h-3.5 mr-1.5" />Migrações salvas ({list.length})
        </Button>
      </div>
      {showList && (
        <div className="rounded-lg border border-line bg-surface divide-y divide-line" data-testid="mig-list">
          {!list.length && <div className="px-3 py-3 text-xs text-slate-500">Nenhum plano salvo ainda.</div>}
          {list.map(m => {
            const done = Object.values(m.status || {}).filter(s => s.dest?.ok).length;
            return (
              <div key={m.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
                <button onClick={() => openSaved(m)} className="text-slate-100 hover:text-brand-soft font-medium">{m.name}</button>
                <span className="text-slate-500">{m.src_device} → {m.dst_device} · {m.plan?.total} ONU(s) · {m.plan?.groups?.length} PON(s) · {done} aplicada(s) · {fmtAt(m.updated_at)}</span>
                <button onClick={() => remove(m)} className="ml-auto p-1 text-slate-500 hover:text-red-300" aria-label={`Apagar ${m.name}`}><Trash2 className="w-3.5 h-3.5" /></button>
              </div>
            );
          })}
        </div>
      )}

      <Step n={1} title="Origem: de onde saem as ONUs">
        <div className="flex flex-wrap items-center gap-2">
          <select value={src.device_id} onChange={e => setSrc({ ...src, device_id: e.target.value })} className={`${sel} w-72`} data-testid="mig-src-device" aria-label="OLT de origem">
            <option value="">{src.mode === "csv" ? "Sem OLT (só a planilha)" : "OLT de origem…"}</option>
            {olts.map(d => <option key={d.id} value={d.id}>{d.name} · {VENDOR[d.device_type]}</option>)}
          </select>
          <div className="flex gap-1 p-0.5 bg-sunken border border-line rounded-md text-xs">
            {[["backup", "Último backup"], ["live", "Ler agora da OLT"], ["csv", "Planilha"]].map(([k, l]) => (
              <button key={k} onClick={() => setSrc({ ...src, mode: k })} className={`px-2.5 h-8 rounded ${src.mode === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`mig-mode-${k}`}>{l}</button>
            ))}
          </div>
          {src.mode === "csv" && !src.device_id && (
            <select value={src.vendor} onChange={e => setSrc({ ...src, vendor: e.target.value })} className={sel} aria-label="Fabricante da planilha">
              {Object.entries(VENDOR).map(([k, l]) => <option key={k} value={k}>formato {l}</option>)}
            </select>
          )}
          <Button onClick={read} disabled={reading || (src.mode !== "csv" && !src.device_id) || (src.mode === "csv" && !src.csv.trim())} className="h-9 bg-brand hover:bg-brand-strong" data-testid="mig-read">
            {reading ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ScanSearch className="w-4 h-4 mr-1.5" />}{reading ? "Lendo…" : "Ler ONUs"}
          </Button>
        </div>
        {src.mode === "csv" && (
          <div>
            <textarea value={src.csv} onChange={e => setSrc({ ...src, csv: e.target.value })} rows={5} spellCheck={false} data-testid="mig-csv"
              placeholder={"pon;onu;serial;tipo;nome;vlan;modo;portas\n1/1/3;1;ZTEGXXXXXXXX;ZTE-F601;NOME DO CLIENTE;100;bridge;1"}
              className="w-full rounded-md bg-sunken border border-line px-3 py-2 text-xs font-mono text-slate-100" />
            <div className="text-[11px] text-slate-500 mt-1">Colunas: {meta?.csv_help}</div>
          </div>
        )}
        {src.mode === "live" && <div className="text-[11px] text-slate-500">ZTE: show running-config · Huawei: display current-configuration · FiberHome: show authorization de cada PON (traz serial, tipo e posição; a VLAN vem do serviço padrão). Só leitura; pode levar alguns minutos.</div>}
        {srcRes && (
          <div className="text-xs text-slate-300 flex flex-wrap gap-x-4 gap-y-1" data-testid="mig-src-summary">
            <span><b className="text-slate-100">{srcRes.onus.length}</b> ONU(s) · {srcRes.summary.pons.length || Object.keys(pons).length} PON(s)</span>
            <span>{VENDOR[srcRes.vendor] || "Planilha"}{srcRes.platform ? ` ${srcRes.platform === "c300" ? "C300/C320" : "TITAN"}` : ""} · {srcRes.mode === "backup" ? `backup de ${fmtAt(srcRes.source_at)}` : `lido ${fmtAt(srcRes.source_at)}`}</span>
            {srcRes.summary.no_sn > 0 && <span className="text-amber-300">{srcRes.summary.no_sn} sem serial (LOID/senha)</span>}
            {srcRes.summary.routed > 0 && <span className="text-slate-400">{srcRes.summary.routed} roteada(s)</span>}
            {srcRes.note && <span className="text-amber-200">{srcRes.note}</span>}
          </div>
        )}
      </Step>

      {srcRes && (
        <Step n={2} title="PONs a migrar (marque só as que vai mudar agora)"
              right={<>
                <Button size="sm" variant="ghost" onClick={() => allOn(true)} className="h-8 text-slate-300">marcar todas</Button>
                <Button size="sm" variant="ghost" onClick={() => allOn(false)} className="h-8 text-slate-300">nenhuma</Button>
              </>}>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span>Destino das marcadas:</span>
            <Button size="sm" variant="outline" onClick={sameNumbers} disabled={!chosen.length} className="h-8 border-line bg-transparent" data-testid="mig-same">mesma numeração</Button>
            <span>ou em sequência a partir de</span>
            <Input value={seqStart} onChange={e => setSeqStart(e.target.value)} placeholder={kind === "huawei" ? "0/1/0" : "1/1"} className={`${small} w-24`} aria-label="Primeira PON de destino" />
            <Button size="sm" variant="outline" onClick={sequential} disabled={!chosen.length} className="h-8 border-line bg-transparent" data-testid="mig-seq">preencher</Button>
            <span className="ml-auto text-slate-300" data-testid="mig-chosen"><b className="text-slate-100">{chosen.length}</b> PON(s) · <b className="text-slate-100">{selOnus.length}</b> ONU(s) marcadas</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs" data-testid="mig-pons">
              <thead className="text-[10px] text-slate-500"><tr>
                <th className="w-8 py-1.5" /><th className="text-left font-normal px-2">PON origem</th><th className="text-right font-normal px-2">ONUs</th>
                <th className="text-left font-normal px-2">VLANs</th><th className="text-left font-normal px-2 hidden md:table-cell">Tipos</th><th className="text-left font-normal px-2">PON destino</th>
              </tr></thead>
              <tbody>
                {summaryPons.map(p => {
                  const v = pons[p.pon] || { on: false, dst: "" };
                  return (
                    <tr key={p.pon} className={`border-t border-line/70 ${v.on ? "" : "opacity-60"}`} data-testid={`mig-pon-${p.pon}`}>
                      <td className="py-1 text-center"><input type="checkbox" checked={v.on} onChange={e => setPon(p.pon, { on: e.target.checked })} aria-label={`Migrar a PON ${p.pon}`} className="w-4 h-4 accent-[rgb(var(--c-brand))]" /></td>
                      <td className="px-2 font-mono text-slate-100">{p.pon}</td>
                      <td className="px-2 text-right font-mono text-slate-200">{p.onus}{p.no_sn ? <span className="text-amber-300" title="sem serial"> ({p.no_sn}!)</span> : ""}</td>
                      <td className="px-2 font-mono text-slate-400 max-w-[260px] truncate">{Object.keys(p.vlans).slice(0, 6).join(", ")}{Object.keys(p.vlans).length > 6 ? "…" : ""}</td>
                      <td className="px-2 text-slate-400 max-w-[260px] truncate hidden md:table-cell">{Object.keys(p.types).slice(0, 3).join(", ")}</td>
                      <td className="px-2 py-1"><Input value={v.dst} disabled={!v.on} onChange={e => setPon(p.pon, { dst: e.target.value })} placeholder={kind === "huawei" ? "F/S/P" : "slot/porta"} className={`${small} w-28`} aria-label={`PON de destino da ${p.pon}`} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="text-[11px] text-slate-500">Duas PONs de origem podem ir para a mesma PON de destino (os IDs são remanejados). As PONs não marcadas ficam como estão — volte aqui depois para migrar as próximas.</div>
        </Step>
      )}

      {srcRes && (
        <Step n={3} title="Destino">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            <label className="text-xs text-slate-400 space-y-1">OLT de destino
              <select value={dst.device_id} onChange={e => setDst({ ...dst, device_id: e.target.value, kind: "" })} className={`${sel} w-full`} data-testid="mig-dst-device">
                <option value="">escolha…</option>
                {olts.map(d => <option key={d.id} value={d.id}>{d.name} · {VENDOR[d.device_type]}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-400 space-y-1">Modelo / CLI
              <select value={kind} onChange={e => setDst({ ...dst, kind: e.target.value })} disabled={!dstDev} className={`${sel} w-full`} data-testid="mig-kind">
                {Object.entries(meta?.dest_kinds || {}).filter(([k]) => !dstDev || k.startsWith(dstDev.device_type === "zte" ? "zte" : dstDev.device_type)).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-400 space-y-1">ID das ONUs
              <select value={dst.id_mode} onChange={e => setDst({ ...dst, id_mode: e.target.value })} className={`${sel} w-full`}>
                <option value="keep">manter o mesmo ID (ajusta se ocupado)</option><option value="renumber">renumerar a partir do primeiro livre</option>
              </select>
            </label>
            <label className="text-xs text-slate-400 space-y-1">Nome da ONU no destino
              <select value={dst.name_from} onChange={e => setDst({ ...dst, name_from: e.target.value })} className={`${sel} w-full`}>
                <option value="name">nome da origem</option><option value="description">descrição da origem</option><option value="pppoe">usuário PPPoE</option><option value="sn">serial</option>
              </select>
            </label>
            {kind === "fiberhome" && (
              <label className="text-xs text-slate-400 space-y-1">Diretório de ONU
                <select value={dst.fh_dir} onChange={e => setDst({ ...dst, fh_dir: e.target.value })} className={`${sel} w-full`}><option value="onu">cd onu (RP novos)</option><option value="gpononu">cd gpononu (antigos)</option></select>
              </label>
            )}
          </div>
          {dstDev && srcRes.device_id === dstDev.id && <div className="text-[11px] text-amber-200"><AlertTriangle className="w-3.5 h-3.5 inline mr-1" />Mesma OLT (troca de placa/PON): a OLT não aceita o mesmo serial duas vezes — aqui a ordem é <b>remover na origem primeiro</b> e depois aplicar no destino.</div>}
        </Step>
      )}

      {srcRes && chosen.length > 0 && kind && (
        <Step n={4} title="Ajustes: VLANs, tipos e perfis do destino">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <div className="text-xs text-slate-300 mb-1.5">VLANs (deixe vazio para manter)</div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 max-h-56 overflow-auto pr-1">
                {vlans.map(([v, n]) => (
                  <label key={v} className="flex items-center gap-1.5 text-xs font-mono text-slate-400">
                    <span className="w-12 text-right text-slate-200">{v}</span><ArrowRight className="w-3 h-3" />
                    <Input value={maps.vlan_map[v] || ""} onChange={e => setMap("vlan_map", v, e.target.value.replace(/\D/g, ""))} placeholder={v} className={`${small} w-16`} aria-label={`VLAN ${v} no destino`} />
                    <span className="text-[10px] text-slate-500">{n}</span>
                  </label>
                ))}
                {!vlans.length && <span className="text-xs text-slate-500">nenhuma VLAN lida — use o serviço padrão</span>}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-300 mb-1.5">{kind === "huawei" ? "Line / srv profile no destino (por tipo/perfil de origem)" : "Tipo da ONU no destino (onu-type cadastrado na OLT)"}</div>
              <datalist id="mig-zte-types">{(meta?.zte_types || []).map(t => <option key={t} value={t} />)}</datalist>
              <div className="space-y-1.5 max-h-56 overflow-auto pr-1">
                {types.map(([t, n]) => (
                  <div key={t} className="flex items-center gap-1.5 text-xs">
                    <span className="w-44 truncate font-mono text-slate-200" title={t}>{t}</span><span className="text-[10px] text-slate-500 w-8">{n}</span><ArrowRight className="w-3 h-3 text-slate-500" />
                    {kind === "huawei" ? (<>
                      <Input value={maps.hw_profiles[t]?.line ?? ""} onChange={e => setMap("hw_profiles", t, { ...(maps.hw_profiles[t] || {}), line: e.target.value.replace(/\D/g, "") })} placeholder="line" className={`${small} w-16`} aria-label={`Line profile para ${t}`} />
                      <Input value={maps.hw_profiles[t]?.srv ?? ""} onChange={e => setMap("hw_profiles", t, { ...(maps.hw_profiles[t] || {}), srv: e.target.value.replace(/\D/g, "") })} placeholder="srv" className={`${small} w-16`} aria-label={`Srv profile para ${t}`} />
                    </>) : (
                      <Input list={zteDst ? "mig-zte-types" : undefined} value={maps.type_map[t] || ""} onChange={e => setMap("type_map", t, e.target.value.trim())}
                             placeholder={(srcRes.vendor === "zte" && zteDst) || srcRes.mode === "csv" || (srcRes.vendor === "fiberhome" && kind === "fiberhome") ? `manter ${t}` : "tipo no destino"} className={`${small} w-44`} aria-label={`Tipo no destino para ${t}`} />
                    )}
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-1.5 mt-2 text-xs text-slate-400">
                {kind === "huawei" ? (<>padrão: line <Input value={maps.default_line} onChange={e => setMaps({ ...maps, default_line: e.target.value.replace(/\D/g, "") })} className={`${small} w-16`} aria-label="Line profile padrão" />
                  srv <Input value={maps.default_srv} onChange={e => setMaps({ ...maps, default_srv: e.target.value.replace(/\D/g, "") })} className={`${small} w-16`} aria-label="Srv profile padrão" /></>)
                  : (<>tipo padrão (o que ficar vazio) <Input list={zteDst ? "mig-zte-types" : undefined} value={maps.default_type} onChange={e => setMaps({ ...maps, default_type: e.target.value.trim() })} className={`${small} w-44`} aria-label="Tipo padrão" /></>)}
              </div>
            </div>
            {zteDst && (
              <div>
                <div className="text-xs text-slate-300 mb-1.5">Perfil de banda (T-CONT) no destino</div>
                <div className="space-y-1.5">
                  {tconts.map(([t, n]) => (
                    <div key={t} className="flex items-center gap-1.5 text-xs">
                      <span className="w-32 truncate font-mono text-slate-200">{t}</span><span className="text-[10px] text-slate-500 w-8">{n}</span><ArrowRight className="w-3 h-3 text-slate-500" />
                      <Input value={maps.tcont_map[t] || ""} onChange={e => setMap("tcont_map", t, e.target.value.trim())} placeholder={`manter ${t}`} className={`${small} w-36`} aria-label={`T-CONT no destino para ${t}`} />
                    </div>
                  ))}
                  <div className="flex items-center gap-1.5 text-xs text-slate-400">{tconts.length ? "padrão (sem perfil na origem)" : "perfil para todas"}
                    <Input value={maps.default_tcont} onChange={e => setMaps({ ...maps, default_tcont: e.target.value.trim() })} placeholder="ex.: 100M" className={`${small} w-36`} aria-label="T-CONT padrão" />
                  </div>
                </div>
              </div>
            )}
            {kind === "huawei" && (
              <div>
                <div className="text-xs text-slate-300 mb-1.5">Gem port de cada VLAN (do line profile de destino)</div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                  {vlans.map(([v]) => {
                    const dv = maps.vlan_map[v] || v;
                    return (
                      <label key={v} className="flex items-center gap-1.5 text-xs font-mono text-slate-400">VLAN <span className="text-slate-200">{dv}</span>
                        <Input value={maps.hw_gem[dv] || ""} onChange={e => setMap("hw_gem", dv, e.target.value.replace(/\D/g, ""))} placeholder="gem" className={`${small} w-14`} aria-label={`Gem port da VLAN ${dv}`} />
                      </label>
                    );
                  })}
                </div>
                <div className="text-[10px] text-slate-500 mt-1">Vazio: a gem port da origem (Huawei) ou a ordem da VLAN na ONU (1, 2…).</div>
              </div>
            )}
            <div>
              <div className="text-xs text-slate-300 mb-1.5">Serviço padrão (ONUs sem VLAN lida{noSvc ? `: ${noSvc} nas PONs marcadas` : ""})</div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
                VLAN <Input value={maps.default_service.vlan} onChange={e => setMaps({ ...maps, default_service: { ...maps.default_service, vlan: e.target.value.replace(/\D/g, "") } })} className={`${small} w-16`} aria-label="VLAN padrão" />
                <select value={maps.default_service.mode} onChange={e => setMaps({ ...maps, default_service: { ...maps.default_service, mode: e.target.value } })} className={`${sel} h-8 text-xs`} aria-label="Modo padrão">
                  {Object.entries(meta?.modes || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                portas <Input value={maps.default_service.ports} onChange={e => setMaps({ ...maps, default_service: { ...maps.default_service, ports: e.target.value } })} className={`${small} w-20`} aria-label="Portas padrão" />
              </div>
            </div>
          </div>
        </Step>
      )}

      {srcRes && (
        <div className="flex flex-wrap items-center gap-2">
          <Input value={name} onChange={e => setName(e.target.value)} placeholder="Nome do plano (ex.: NOME DO EQUIPAMENTO → OLT NOVA)" className={`${inp} w-80`} aria-label="Nome do plano" />
          <Button onClick={() => plan(mig?.id)} disabled={!ready || planning} className="h-9 bg-brand hover:bg-brand-strong" data-testid="mig-plan">
            {planning ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ScanSearch className="w-4 h-4 mr-1.5" />}{mig ? "Atualizar plano" : "Gerar plano"}
          </Button>
          {!ready && <span className="text-[11px] text-slate-500">marque as PONs, informe o destino de cada uma e escolha a OLT de destino</span>}
          {mig && <Button variant="ghost" onClick={() => plan("")} disabled={!ready || planning} className="h-9 text-slate-400">salvar como novo</Button>}
        </div>
      )}

      {mig && (
        <section className="space-y-2" data-testid="mig-plan-result">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <h2 className="text-sm font-semibold text-slate-100">Plano: {mig.name}</h2>
            <span className="text-slate-300"><b className="text-emerald-300">{mig.plan.ok}</b> pronta(s) de {mig.plan.total}</span>
            {mig.plan.errors > 0 && <span className="text-red-300">{mig.plan.errors} com pendência (ficam fora dos scripts)</span>}
            {mig.plan.shift !== 0 && <span className="text-slate-400">IDs {mig.plan.shift > 0 ? "+1" : "−1"} (Huawei numera de 0)</span>}
            <span className="text-slate-500">{mig.dest_backup_at ? `destino conferido no backup de ${fmtAt(mig.dest_backup_at)}` : "destino sem backup: IDs e seriais já existentes não foram conferidos"}</span>
            <div className="ml-auto flex gap-1.5">
              <Button size="sm" variant="outline" onClick={() => downloadAll("dest")} className="h-8 border-line bg-transparent"><Download className="w-3.5 h-3.5 mr-1" />Todos os scripts do destino</Button>
              {mig.src_device_id && <Button size="sm" variant="outline" onClick={() => downloadAll("remove")} className="h-8 border-line bg-transparent"><Download className="w-3.5 h-3.5 mr-1" />Remoções na origem</Button>}
            </div>
          </div>
          {mig.plan.missing?.length > 0 && <div className="text-xs text-amber-200">Sem ONUs na origem: {mig.plan.missing.join(", ")}</div>}
          <div className="text-[11px] text-slate-400">{sameOlt
            ? "Mesma OLT: 1) Remover na origem  2) Mudar a fibra  3) Aplicar no destino  4) Conferir."
            : "Ordem por PON: 1) Aplicar no destino  2) Mudar a fibra da PON  3) Conferir no destino  4) Remover na origem (só libera depois de aplicar no destino)."}</div>
          <div className="space-y-2">
            {mig.plan.groups.map(g => <GroupCard key={g.dst} mig={mig} g={g} onChanged={refreshMig} sameOlt={sameOlt} />)}
          </div>
        </section>
      )}
    </div>
  );
}
