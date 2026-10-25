import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { UserX, CheckCircle2, LogOut, Loader2, Save, AlertTriangle, RefreshCw, Ban } from "lucide-react";

const SEV = { info: ["Info", "text-slate-400 border-slate-500/40"], low: ["Baixa", "text-sky-300 border-sky-500/40"], medium: ["Média", "text-amber-300 border-amber-500/40"],
  high: ["Alta", "text-orange-300 border-orange-500/40"], critical: ["Crítica", "text-red-300 border-red-500/40"] };
const ST = { open: ["a conferir", "text-amber-300"], benign: ["foi o usuário", "text-emerald-300"], compromised: ["comprometida — sessões encerradas", "text-red-300"], blocked: ["bloqueado na entrada", "text-red-300"] };
const lbl = "text-xs text-slate-400 mb-1 block";
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const flag = (cc) => (cc && /^[A-Z]{2}$/.test(cc) ? String.fromCodePoint(...[...cc].map(c => 0x1f1a5 + c.charCodeAt(0))) : "");

function Anomalies() {
  const [rows, setRows] = useState(null);
  const [only, setOnly] = useState("open");
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api.get("/soc/logins/anomalies", { params: { status: only } }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [only]);
  useEffect(() => { load(); }, [load]);
  const act = async (a, action) => {
    if (action === "revoke" && !window.confirm(`Encerrar todas as sessões de ${a.email}? Ele terá que entrar de novo.`)) return;
    setBusy(a.id);
    try { await api.post(`/soc/logins/anomalies/${a.id}`, { action }); toast.success(action === "revoke" ? "Sessões encerradas" : "Marcado como legítimo"); load(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  return (
    <div className="space-y-3" data-testid="la-list">
      <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs w-fit">
        {[["open", "A conferir"], ["blocked", "Bloqueados"], ["", "Todos"]].map(([k, l]) => <button key={k} onClick={() => setOnly(k)} className={`px-3 h-7 rounded ${only === k ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`} data-testid={`la-f-${k || "all"}`}>{l}</button>)}
      </div>
      {rows && rows.length === 0 && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">Nenhum login suspeito {only === "open" ? "a conferir" : ""}.</Card>}
      {(rows || []).map(a => {
        const [sl, sc] = SEV[a.severity] || SEV.medium;
        const [stl, stc] = ST[a.status] || ST.open;
        return (
          <Card key={a.id} className="bg-surface border-line p-4 space-y-2" data-testid={`la-row-${a.email}`}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className={`text-[11px] px-1.5 py-0.5 rounded border ${sc}`}>{sl}</span>
              <span className="text-slate-100 font-medium">{a.email}</span>
              <span className="font-mono text-slate-300">{a.ip}</span>
              {a.cc && <span className="text-slate-300">{flag(a.cc)} {a.cc} · AS{a.asn} {a.asn_name}</span>}
              <span className="text-xs text-slate-500">{a.device} · {fmt(a.at)}</span>
              <span className={`ml-auto text-xs ${stc}`}>{stl}{a.decided_by ? ` (${a.decided_by})` : ""}</span>
            </div>
            <ul className="text-sm text-slate-300 space-y-0.5 ml-1">{a.findings.map((f, i) => <li key={i} className="flex gap-2"><AlertTriangle className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${(SEV[f.severity] || SEV.medium)[1].split(" ")[0]}`} />{f.text}</li>)}</ul>
            {a.status === "open" && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => act(a, "benign")} disabled={busy === a.id} className="h-8 border-line bg-transparent" data-testid={`la-ok-${a.email}`}><CheckCircle2 className="w-4 h-4 mr-1 text-emerald-300" />Foi o usuário</Button>
                <Button size="sm" onClick={() => act(a, "revoke")} disabled={busy === a.id} className="h-8 bg-red-700 hover:bg-red-600" data-testid={`la-revoke-${a.email}`}>{busy === a.id ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <LogOut className="w-4 h-4 mr-1" />}Encerrar sessões dele</Button>
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function Recent() {
  const [rows, setRows] = useState(null);
  const [email, setEmail] = useState("");
  const load = useCallback(() => api.get("/soc/logins/recent", { params: { email } }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [email]);
  useEffect(() => { const t = setTimeout(load, 350); return () => clearTimeout(t); }, [load]);
  return (
    <Card className="bg-surface border-line" data-testid="la-recent">
      <div className="flex gap-2 px-4 py-3 border-b border-line items-center">
        <Input value={email} onChange={e => setEmail(e.target.value)} placeholder="e-mail exato do usuário (vazio = todos)" className="bg-sunken border-line h-9 w-80" data-testid="la-email" />
        <Button size="sm" variant="ghost" onClick={load} className="text-slate-400"><RefreshCw className="w-4 h-4" /></Button>
      </div>
      <div className="overflow-x-auto"><table className="w-full text-sm"><tbody className="divide-y divide-line">
        {(rows || []).map(r => (
          <tr key={r.id}>
            <td className="px-4 py-1.5 text-xs text-slate-500 whitespace-nowrap">{fmt(r.at)}</td>
            <td className="px-2">{r.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-300" /> : <Ban className="w-4 h-4 text-red-300" />}</td>
            <td className="px-2 text-slate-300 whitespace-nowrap">{r.email}</td>
            <td className="px-2 font-mono text-xs text-slate-300">{r.ip}</td>
            <td className="px-2 text-xs text-slate-400 whitespace-nowrap">{r.cc ? `${flag(r.cc)} ${r.cc} · ${r.asn_name || `AS${r.asn}`}` : "rede interna / sem base"}</td>
            <td className="px-2 text-xs text-slate-500 whitespace-nowrap">{r.device}</td>
            <td className="px-2 text-xs text-red-300">{r.ok ? "" : r.reason}</td>
          </tr>
        ))}
      </tbody></table></div>
    </Card>
  );
}

function Settings() {
  const [c, setC] = useState(null);
  const [cc, setCc] = useState("");
  const [nets, setNets] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/soc/logins/settings").then(r => { setC(r.data); setCc(r.data.allowed_countries.join(", ")); setNets(r.data.trusted_nets.join("\n")); }).catch(e => toast.error(formatApiError(e))); }, []);
  if (!c) return null;
  const set = (k, v) => setC({ ...c, [k]: v });
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...c, allowed_countries: cc.split(/[\s,;]+/).filter(Boolean), trusted_nets: nets.split(/[\s,;]+/).filter(Boolean),
        min_history: Number(c.min_history), fail_burst: Number(c.fail_burst), travel_hours: Number(c.travel_hours) };
      const { data } = await api.put("/soc/logins/settings", body);
      setC({ ...c, ...data }); toast.success("Salvo");
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-4 max-w-3xl" data-testid="la-settings">
      {!c.geo_ready && <Card className="bg-amber-500/5 border-amber-500/40 p-3 text-sm text-amber-200"><AlertTriangle className="w-4 h-4 inline mr-1.5" />Sem a base de ASN não dá para saber o país/operadora do IP — importe em <b>Análise de Flow → Configurações → Base IP → ASN</b>. Sem ela, só valem IP novo, horário, navegador e força bruta.</Card>}
      <Card className="bg-surface border-line p-4 space-y-3">
        <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={c.enabled} onCheckedChange={v => set("enabled", v)} data-testid="la-enabled" /> Analisar cada login</label>
        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Abrir incidente a partir de</label>
            <select value={c.notify_min} onChange={e => set("notify_min", e.target.value)} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200">
              {Object.entries(SEV).filter(([k]) => k !== "info").map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <label className="flex items-center gap-2 text-sm text-slate-300 pt-5"><Switch checked={c.ask_user} onCheckedChange={v => set("ask_user", v)} /> Perguntar "Foi você?" no Telegram do usuário</label>
        </div>
        <div className="grid sm:grid-cols-3 gap-3">
          <div><label className={lbl}>Histórico mínimo (logins) p/ avaliar</label><Input type="number" value={c.min_history} onChange={e => set("min_history", e.target.value)} className="bg-sunken border-line h-9" /></div>
          <div><label className={lbl}>Viagem impossível: troca de país em menos de (h)</label><Input type="number" value={c.travel_hours} onChange={e => set("travel_hours", e.target.value)} className="bg-sunken border-line h-9" /></div>
          <div><label className={lbl}>Força bruta: falhas em 30 min antes do acerto</label><Input type="number" value={c.fail_burst} onChange={e => set("fail_burst", e.target.value)} className="bg-sunken border-line h-9" /></div>
        </div>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="text-sm font-semibold text-slate-200">Cerca geográfica</div>
        <div><label className={lbl}>Só aceitar login destes países (códigos de 2 letras; vazio = qualquer país)</label>
          <Input value={cc} onChange={e => setCc(e.target.value.toUpperCase())} placeholder="BR" className="bg-sunken border-line h-9 font-mono" data-testid="la-cc" /></div>
        <div><label className={lbl}>Redes confiáveis (sempre entram e não geram alerta) — uma por linha</label>
          <textarea value={nets} onChange={e => setNets(e.target.value)} rows={3} placeholder="198.51.100.0/24" className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="la-nets" /></div>
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={c.geofence_admins} onCheckedChange={v => set("geofence_admins", v)} /> Vale também para administradores</label>
        <div className="text-[11px] text-slate-500">Rede interna e IPs sem país conhecido sempre passam. O BastiON não deixa salvar uma lista que bloquearia você mesmo agora.</div>
      </Card>
      <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="la-save">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}Salvar</Button>
    </div>
  );
}

export default function LoginAnom() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="la-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><UserX className="w-6 h-6 text-brand-soft" /> Logins suspeitos</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">Cada login é comparado com o histórico do usuário: país ou operadora nova, troca de país em pouco tempo, horário fora do costume, navegador novo e acerto depois de várias falhas.</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
          {[["", "Suspeitos"], ["logins", "Últimos logins"], ["config", "Configurações"]].map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`la-tab-${k || "an"}`}>{l}</button>)}
        </div>
      </div>
      {tab === "logins" ? <Recent /> : tab === "config" ? <Settings /> : <Anomalies />}
    </div>
  );
}
