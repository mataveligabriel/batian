import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { TagOptions } from "@/components/TagSelect";
import { Cpu, Copy, Download, Loader2, Plus, Trash2, ScanSearch, AlertTriangle, RotateCcw, Router, Search, Unplug, CheckCircle2, XCircle, Send, PowerOff, History } from "lucide-react";

const KEY = "bastion_oltgen_form";                       // formulário lembrado neste navegador, sem senhas
const TABS = [["ports", "Placas e portas"], ["mgmt", "Gerência"], ["services", "Serviços"], ["system", "Sistema"], ["access", "Acesso"]];
const DEFAULT = {
  model: "C620", hostname: "OLT-01", pon: [], uplinks: [],
  mgmt: { out_enabled: false, out_ip: "", out_mask: "24", out_gw: "", in_enabled: false, in_vlan: "", in_ip: "", in_mask: "30", in_gw: "", in_uplinks: [] },
  vlans: { enabled: false, start: "100", count: "32", description: "CLIENTES", uplinks: [] },
  onu_types: [], speeds: [], ntp: ["", ""],
  autosave: { enabled: true, time: "03:00" },
  backup: { enabled: false, server: "", username: "", password: "", path: "" },
  access: { ssh: true, telnet: false, user: "", password: "" },
  snmp: { enabled: false, community: "", trap_host: "" },
  comments: true, write: true,
};
const SECRET = (f) => ({ ...f, access: { ...f.access, password: "" }, backup: { ...f.backup, password: "" }, snmp: { ...f.snmp, community: "" } });
const load = () => { try { const v = JSON.parse(localStorage.getItem(KEY) || "null"); return v ? { ...DEFAULT, ...v } : null; } catch { return null; } };
const board = (b, on) => ({ slot: b.slot, ports: b.ports, prefix: b.prefix, enabled: Array.from({ length: b.ports }, () => on) });
const upName = (u, i, plat) => (plat === "c300" ? `${u.prefix || "xgei"}_1/${u.slot}/${i + 1}` : `${u.prefix || "xgei"}-1/${u.slot}/${i + 1}`);

const sec = "rounded-lg border border-line bg-sunken/40 p-4 space-y-3";
const inp = "bg-sunken border-line font-mono h-9";

function Toggle({ on, onClick, label, testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid} title={label}
      className={`h-7 min-w-[2rem] px-1.5 rounded text-[11px] font-mono border transition-colors ${on ? "bg-brand/25 border-brand/60 text-brand-soft" : "bg-transparent border-line text-slate-500 hover:text-slate-300"}`}>
      {label}
    </button>
  );
}

function Check({ id, checked, onChange, children, testid }) {
  return (
    <div className="flex items-center gap-2">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(!!v)} data-testid={testid} />
      <label htmlFor={id} className="text-sm text-slate-300 cursor-pointer select-none">{children}</label>
    </div>
  );
}

function Field({ label, children, hint }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-slate-400">{label}</Label>
      {children}
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

function BoardsEditor({ title, boards, onChange, kind, testid, plat }) {
  const set = (i, patch) => onChange(boards.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const resize = (i, ports) => {
    const n = Math.max(1, Math.min(kind === "pon" ? 64 : 32, parseInt(ports, 10) || 1));
    const b = boards[i];
    set(i, { ports: n, enabled: Array.from({ length: n }, (_, k) => b.enabled[k] ?? kind === "pon") });
  };
  const add = () => {
    const used = new Set(boards.map(b => b.slot));
    let slot = 1; while (used.has(slot)) slot += 1;
    onChange([...boards, board({ slot, ports: kind === "pon" ? 16 : 4, prefix: "xgei" }, kind === "pon")]);
  };
  return (
    <div className={sec} data-testid={testid}>
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-semibold text-slate-200">{title}</div>
        <Button size="sm" variant="outline" onClick={add} className="border-line bg-transparent h-7 text-xs" data-testid={`${testid}-add`}><Plus className="w-3.5 h-3.5 mr-1" />Placa</Button>
      </div>
      {boards.length === 0 && <div className="text-xs text-slate-500">Nenhuma placa. Use “Ler placas da OLT” ou adicione o slot.</div>}
      {boards.map((b, i) => {
        const all = b.enabled.every(Boolean);
        return (
          <div key={i} className="space-y-1.5 border-t border-line/60 pt-2 first:border-0 first:pt-0">
            <div className="flex flex-wrap items-center gap-2 text-xs font-mono text-slate-400">
              <span>slot</span>
              <Input value={b.slot} onChange={e => set(i, { slot: parseInt(e.target.value, 10) || "" })} className={`${inp} w-14 h-7`} data-testid={`${testid}-slot-${i}`} />
              <span>portas</span>
              <Input value={b.ports} onChange={e => resize(i, e.target.value)} className={`${inp} w-14 h-7`} data-testid={`${testid}-ports-${i}`} />
              {kind === "uplink" && (
                <select value={b.prefix} onChange={e => set(i, { prefix: e.target.value })} data-testid={`${testid}-prefix-${i}`}
                  className="h-7 rounded-md bg-sunken border border-line px-1.5 text-slate-200">
                  {["xgei", "gei", "xxvgei", "cgei"].map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              )}
              <button type="button" className="text-slate-400 hover:text-slate-200 underline-offset-2 hover:underline"
                onClick={() => set(i, { enabled: b.enabled.map(() => !all) })}>{all ? "desmarcar" : "marcar"} todas</button>
              <button type="button" onClick={() => onChange(boards.filter((_, j) => j !== i))} title="Remover placa"
                className="ml-auto text-slate-500 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></button>
            </div>
            <div className="flex flex-wrap gap-1">
              {b.enabled.map((on, k) => (
                <Toggle key={k} on={on} label={kind === "pon" ? k + 1 : upName(b, k, plat)} testid={`${testid}-p-${i}-${k}`}
                  onClick={() => set(i, { enabled: b.enabled.map((x, z) => (z === k ? !x : x)) })} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function UplinkPick({ uplinks, value, onChange, testid, plat }) {
  const names = uplinks.flatMap(u => Array.from({ length: u.ports }, (_, i) => upName(u, i, plat)));
  if (!names.length) return <div className="text-xs text-slate-500">Cadastre as placas de uplink na primeira aba.</div>;
  return (
    <div className="flex flex-wrap gap-1" data-testid={testid}>
      {names.map(n => <Toggle key={n} on={value.includes(n)} label={n} testid={`${testid}-${n}`}
        onClick={() => onChange(value.includes(n) ? value.filter(x => x !== n) : [...value, n])} />)}
    </div>
  );
}

// Enviar direto para a OLT: o servidor gera o script de novo com os mesmos parâmetros, aplica linha a linha e grava
function ApplyBar({ apply, ready }) {
  const [dev, setDev] = useState(apply.deviceId || "");
  const [confirm, setConfirm] = useState(false);
  const [sending, setSending] = useState(false);
  const [res, setRes] = useState(null);
  const [doneSig, setDoneSig] = useState("");           // já aplicado com estes parâmetros: não manda duas vezes
  const sig = JSON.stringify([apply.kind, apply.params]);
  useEffect(() => { if (sig !== doneSig) { setRes(null); setConfirm(false); } }, [sig]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (apply.deviceId) setDev(apply.deviceId); }, [apply.deviceId]);
  const name = apply.devices.find(d => d.id === dev)?.name || "OLT";
  const send = async () => {
    setSending(true); setConfirm(false); setRes(null);
    try {
      const { data } = await api.post("/oltgen/apply", { device_id: dev, kind: apply.kind, params: apply.params });
      setRes(data);
      if (data.ok) { setDoneSig(sig); toast.success(`Aplicado${data.saved ? " e gravado" : ""} em ${data.device}`); apply.onDone && apply.onDone(data); }
      else toast.error(`A OLT recusou: ${data.failed?.command}`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setSending(false); }
  };
  return (
    <div className="px-4 py-2.5 border-b border-line space-y-2" data-testid="oltgen-apply">
      <div className="flex flex-wrap items-center gap-2">
        <select value={dev} onChange={e => { setDev(e.target.value); setConfirm(false); }} data-testid="oltgen-apply-device"
          className="h-8 min-w-0 flex-1 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100">
          <option value="">{apply.devices.length ? "Enviar para qual OLT?" : `Nenhuma OLT ${{ huawei: "Huawei", fiberhome: "FiberHome" }[apply.vendor] || "ZTE"} cadastrada`}</option>
          <TagOptions devices={apply.devices} label={d => `${d.name} · ${d.host}`} />
        </select>
        {!confirm ? (
          <Button size="sm" onClick={() => setConfirm(true)} disabled={!ready || !dev || sending || sig === doneSig} className="h-8 bg-emerald-700 hover:bg-emerald-600" data-testid="oltgen-apply-btn">
            {sending ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Send className="w-3.5 h-3.5 mr-1.5" />}{sending ? "Aplicando…" : "Enviar para a OLT"}
          </Button>
        ) : (
          <>
            <Button size="sm" onClick={send} className="h-8 bg-red-700 hover:bg-red-600" data-testid="oltgen-apply-confirm"><Send className="w-3.5 h-3.5 mr-1.5" />Aplicar e gravar em {name}</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)} className="h-8 text-slate-300">Cancelar</Button>
          </>
        )}
      </div>
      {res && (res.ok ? (
        <div className="text-xs text-emerald-300 flex items-center gap-1.5" data-testid="oltgen-apply-ok">
          <CheckCircle2 className="w-4 h-4" />{res.steps.length} comandos aplicados em {res.device}{res.saved ? ` e configuração gravada (${apply.vendor === "huawei" || apply.vendor === "fiberhome" ? "save" : "write"})` : ""}.
          {apply.kind === "onu-add" && <span className="text-slate-400">Para outra VLAN nessa ONU, clique em Ler ONU na OLT de novo.</span>}
        </div>
      ) : (
        <div className="rounded-md border border-red-800/60 bg-red-950/30 px-3 py-2 text-xs text-red-200 space-y-1" data-testid="oltgen-apply-fail">
          <div><XCircle className="w-3.5 h-3.5 inline mr-1" />A OLT recusou <span className="font-mono">{res.failed?.command}</span> — parei aí e <b>não gravei</b>.
            {res.steps.length > 1 ? ` As ${res.steps.length - 1} linhas anteriores ficaram na configuração em uso (sem gravar).` : ""}</div>
          <pre className="whitespace-pre-wrap font-mono text-[11px] text-red-100/80 max-h-32 overflow-auto m-0">{res.failed?.output}</pre>
        </div>
      ))}
    </div>
  );
}

function ScriptCard({ out, err, busy, apply }) {
  const copy = async () => {
    try { await navigator.clipboard.writeText(out?.script || ""); toast.success("Script copiado"); } catch { toast.error("Não consegui copiar"); }
  };
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([out?.script || ""], { type: "text/plain;charset=utf-8" }));
    a.download = out?.filename || "olt.txt"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  return (
    <Card className="bg-surface border-line flex flex-col min-h-[480px] xl:sticky xl:top-4 xl:max-h-[calc(100vh-6rem)] min-w-0" data-testid="oltgen-result">
          <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-line">
            <Cpu className="w-4 h-4 text-brand-soft" />
            <div className="text-sm text-slate-200 font-mono truncate">{out?.filename || "script"}</div>
            {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-500" />}
            <span className="text-[11px] font-mono text-slate-500">{out ? `${out.lines} linhas` : ""}</span>
            <div className="ml-auto flex gap-1.5">
              <Button size="sm" onClick={copy} disabled={!out || !!err} className="bg-brand hover:bg-brand-strong h-8" data-testid="oltgen-copy"><Copy className="w-3.5 h-3.5 mr-1.5" />Copiar</Button>
              <Button size="sm" variant="outline" onClick={download} disabled={!out || !!err} className="border-line bg-transparent h-8" data-testid="oltgen-download"><Download className="w-3.5 h-3.5 mr-1.5" />.txt</Button>
            </div>
          </div>
          {apply && <ApplyBar apply={apply} ready={!!out && !err && !busy} />}
          {err && <div className="mx-4 mt-3 rounded-md border border-red-800/60 bg-red-950/30 px-3 py-2 text-sm text-red-200" data-testid="oltgen-error">{err}</div>}
          {!err && out?.warnings?.length > 0 && (
            <div className="mx-4 mt-3 space-y-1" data-testid="oltgen-warnings">
              {out.warnings.map((w, i) => <div key={i} className="flex gap-2 text-xs text-amber-200"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}</div>)}
            </div>
          )}
          <pre className={`flex-1 overflow-auto m-0 px-4 py-3 text-[12.5px] leading-relaxed font-mono text-slate-200 whitespace-pre ${err ? "opacity-40" : ""}`} data-testid="oltgen-script">
            {(out?.script || "").split("\n").map((l, i) => <div key={i} className={l.startsWith("!") || l.startsWith("# ") ? "text-slate-500" : ""}>{l || " "}</div>)}
          </pre>
          <div className="px-4 py-2 border-t border-line text-[11px] text-slate-500">Revise os comandos e a versão do firmware antes de aplicar na OLT.</div>
        </Card>
  );
}

function OltActivation() {
  const [meta, setMeta] = useState(null);
  const [f, setF] = useState(() => load() || DEFAULT);
  const [tab, setTab] = useState("ports");
  const [out, setOut] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [devices, setDevices] = useState([]);
  const [devId, setDevId] = useState("");
  const [reading, setReading] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    api.get("/oltgen/models").then(r => {
      setMeta(r.data);
      setF(cur => (cur.pon.length || cur.uplinks.length ? cur : applyModel(cur, cur.model, r.data)));
    }).catch(e => toast.error(formatApiError(e)));
    api.get("/devices").then(r => setDevices((r.data || []).filter(d => d.device_type === "zte"))).catch(() => {});
  }, []);

  // gera a cada mudança (pequena espera para não chamar a cada tecla)
  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(SECRET(f))); } catch { /* sem armazenamento */ }
    const n = ++seq.current;
    setBusy(true);
    const t = setTimeout(() => {
      api.post("/oltgen/generate", f).then(r => { if (n === seq.current) { setOut(r.data); setErr(""); } })
        .catch(e => { if (n === seq.current) setErr(formatApiError(e)); })
        .finally(() => { if (n === seq.current) setBusy(false); });
    }, 350);
    return () => clearTimeout(t);
  }, [f]);

  const up = (k, patch) => setF(cur => ({ ...cur, [k]: { ...cur[k], ...patch } }));
  const model = meta?.models.find(m => m.key === f.model);
  const plat = model?.platform || "titan";

  function applyModel(cur, key, m = meta) {
    const md = m?.models.find(x => x.key === key);
    return { ...cur, model: key, pon: (md?.pon || []).map(b => board(b, true)), uplinks: (md?.uplinks || []).map(b => board(b, false)),
      mgmt: { ...cur.mgmt, in_uplinks: [] }, vlans: { ...cur.vlans, uplinks: [] } };
  }

  const readCards = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setReading(true);
    try {
      const { data } = await api.post("/oltgen/read-cards", { device_id: devId });
      setF(cur => ({ ...cur, pon: data.pon.map(b => board(b, true)), uplinks: data.uplinks.map(b => board(b, false)),
        hostname: cur.hostname === DEFAULT.hostname ? (data.device || cur.hostname).replace(/[^A-Za-z0-9_.-]/g, "-") : cur.hostname,
        mgmt: { ...cur.mgmt, in_uplinks: [] }, vlans: { ...cur.vlans, uplinks: [] } }));
      toast.success(`${data.device}: ${data.pon.length} placa(s) PON e ${data.uplinks.length} com uplink`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setReading(false); }
  };

  const ponCount = f.pon.reduce((a, b) => a + b.enabled.filter(Boolean).length, 0);
  const upCount = f.uplinks.reduce((a, b) => a + b.enabled.filter(Boolean).length, 0);
  const speedsText = useMemo(() => f.speeds.join(", "), [f.speeds]);

  return (
    <div data-testid="oltgen-activation">
      <div className="px-4 md:px-6 pt-2 pb-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-heading text-xl font-semibold tracking-tight text-slate-100 mt-1">Ativação da OLT</h2>
        </div>
        <Button variant="outline" className="border-line bg-transparent text-slate-300" data-testid="oltgen-reset"
          onClick={() => setF(applyModel({ ...DEFAULT }, "C620"))}><RotateCcw className="w-4 h-4 mr-1.5" />Limpar</Button>
      </div>

      <div className="px-4 md:px-6 pb-6 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-4 min-w-0">
          <Card className="bg-surface border-line p-4 space-y-3">
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="Modelo da OLT">
                <select value={f.model} onChange={e => setF(cur => applyModel(cur, e.target.value))} data-testid="oltgen-model"
                  className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
                  {(meta?.models || [{ key: "C620", label: "ZXA10 C620" }]).map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                </select>
              </Field>
              <Field label="Hostname">
                <Input value={f.hostname} onChange={e => setF({ ...f, hostname: e.target.value })} className={inp} data-testid="oltgen-hostname" />
              </Field>
            </div>
            {model?.note && <div className="text-xs text-slate-400">{model.note}</div>}
            <div className="flex flex-wrap items-end gap-2 pt-1">
              <div className="flex-1 min-w-[200px]">
                <Field label="Ler placas de uma OLT cadastrada (show card)">
                  <select value={devId} onChange={e => setDevId(e.target.value)} data-testid="oltgen-device"
                    className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
                    <option value="">{devices.length ? "Escolha a OLT…" : "Nenhum equipamento ZTE cadastrado"}</option>
                    <TagOptions devices={devices} label={d => `${d.name} · ${d.host}`} />
                  </select>
                </Field>
              </div>
              <Button onClick={readCards} disabled={reading || !devId} variant="outline" className="border-line bg-transparent h-9" data-testid="oltgen-read">
                {reading ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ScanSearch className="w-4 h-4 mr-1.5" />}Ler placas da OLT
              </Button>
            </div>
          </Card>

          <div className="flex gap-1 border-b border-line overflow-x-auto whitespace-nowrap">
            {TABS.map(([k, l], i) => (
              <button key={k} onClick={() => setTab(k)} data-testid={`oltgen-tab-${k}`}
                className={`px-3 py-2 text-sm border-b-2 -mb-px ${tab === k ? "border-brand text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
                <span className="font-mono text-[11px] text-slate-500 mr-1">{String(i + 1).padStart(2, "0")}</span>{l}
              </button>
            ))}
          </div>

          {tab === "ports" && (
            <div className="space-y-3">
              <div className="text-xs text-slate-400">Ative só as portas das placas instaladas. Confira com <span className="font-mono">show card</span> e <span className="font-mono">show interface brief</span>.</div>
              <BoardsEditor plat={plat} title={`Placas PON (${plat === "c300" ? "gpon-olt" : "gpon_olt"})`} kind="pon" boards={f.pon} onChange={pon => setF({ ...f, pon })} testid="oltgen-pon" />
              <BoardsEditor plat={plat} title="Placas com uplink" kind="uplink" boards={f.uplinks} onChange={uplinks => setF({ ...f, uplinks })} testid="oltgen-up" />
              <div className="text-xs font-mono text-slate-500" data-testid="oltgen-count">{ponCount} PON e {upCount} uplinks marcadas</div>
            </div>
          )}

          {tab === "mgmt" && (
            <div className="space-y-3">
              <div className={sec}>
                <Check id="og-out" checked={f.mgmt.out_enabled} onChange={v => up("mgmt", { out_enabled: v })} testid="oltgen-out">Outband (porta MGMT, VRF mng)</Check>
                {f.mgmt.out_enabled && (
                  <div className="grid sm:grid-cols-3 gap-3">
                    <Field label="IP"><Input value={f.mgmt.out_ip} onChange={e => up("mgmt", { out_ip: e.target.value })} placeholder="192.0.2.10" className={inp} data-testid="oltgen-out-ip" /></Field>
                    <Field label="Máscara"><Input value={f.mgmt.out_mask} onChange={e => up("mgmt", { out_mask: e.target.value })} placeholder="24" className={inp} /></Field>
                    <Field label="Gateway"><Input value={f.mgmt.out_gw} onChange={e => up("mgmt", { out_gw: e.target.value })} placeholder="192.0.2.1" className={inp} /></Field>
                  </div>
                )}
              </div>
              <div className={sec}>
                <Check id="og-in" checked={f.mgmt.in_enabled} onChange={v => up("mgmt", { in_enabled: v })} testid="oltgen-in">Inband (VLAN de gerência pela uplink)</Check>
                {f.mgmt.in_enabled && (
                  <>
                    <div className="grid sm:grid-cols-4 gap-3">
                      <Field label="VLAN"><Input value={f.mgmt.in_vlan} onChange={e => up("mgmt", { in_vlan: e.target.value })} placeholder="999" className={inp} data-testid="oltgen-in-vlan" /></Field>
                      <Field label="IP"><Input value={f.mgmt.in_ip} onChange={e => up("mgmt", { in_ip: e.target.value })} placeholder="198.51.100.2" className={inp} data-testid="oltgen-in-ip" /></Field>
                      <Field label="Máscara"><Input value={f.mgmt.in_mask} onChange={e => up("mgmt", { in_mask: e.target.value })} placeholder="30" className={inp} /></Field>
                      <Field label="Gateway"><Input value={f.mgmt.in_gw} onChange={e => up("mgmt", { in_gw: e.target.value })} placeholder="198.51.100.1" className={inp} /></Field>
                    </div>
                    <Field label="Uplink(s) por onde a gerência chega">
                      <UplinkPick plat={plat} uplinks={f.uplinks} value={f.mgmt.in_uplinks} onChange={v => up("mgmt", { in_uplinks: v })} testid="oltgen-in-ups" />
                    </Field>
                  </>
                )}
              </div>
            </div>
          )}

          {tab === "services" && (
            <div className="space-y-3">
              <div className={sec}>
                <Check id="og-vl" checked={f.vlans.enabled} onChange={v => up("vlans", { enabled: v })} testid="oltgen-vlans">VLANs de acesso (clientes)</Check>
                {f.vlans.enabled && (
                  <>
                    <div className="grid sm:grid-cols-3 gap-3">
                      <Field label="VLAN inicial"><Input value={f.vlans.start} onChange={e => up("vlans", { start: e.target.value })} className={inp} data-testid="oltgen-vlan-start" /></Field>
                      <Field label="Quantidade" hint="ex.: 32 = uma por porta PON"><Input value={f.vlans.count} onChange={e => up("vlans", { count: e.target.value })} className={inp} data-testid="oltgen-vlan-count" /></Field>
                      <Field label="Descrição"><Input value={f.vlans.description} onChange={e => up("vlans", { description: e.target.value })} className={inp} /></Field>
                    </div>
                    <Field label="Uplinks que levam essas VLANs">
                      <UplinkPick plat={plat} uplinks={f.uplinks} value={f.vlans.uplinks} onChange={v => up("vlans", { uplinks: v })} testid="oltgen-vlan-ups" />
                    </Field>
                  </>
                )}
              </div>
              <div className={sec}>
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold text-slate-200">Perfis de ONU</div>
                  <button type="button" className="text-xs text-slate-400 hover:text-slate-200" data-testid="oltgen-onu-all"
                    onClick={() => setF({ ...f, onu_types: f.onu_types.length === (meta?.onu_types.length || 0) ? [] : (meta?.onu_types || []).map(o => o.name) })}>
                    {f.onu_types.length === (meta?.onu_types.length || -1) ? "desmarcar" : "marcar"} todos</button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {(meta?.onu_types || []).map(o => (
                    <Toggle key={o.name} on={f.onu_types.includes(o.name)} label={o.name} testid={`oltgen-onu-${o.name}`}
                      onClick={() => setF({ ...f, onu_types: f.onu_types.includes(o.name) ? f.onu_types.filter(x => x !== o.name) : [...f.onu_types, o.name] })} />
                  ))}
                </div>
                <div className="text-[11px] text-slate-500">Confira com o firmware e com as ONUs que você usa.</div>
              </div>
              <div className={sec}>
                <Field label="Perfis de banda (T-CONT + tráfego)" hint="separe por vírgula — ex.: 100M, 300M, 500M, 1G">
                  <Input defaultValue={speedsText} key={speedsText} data-testid="oltgen-speeds" className={inp}
                    onBlur={e => setF({ ...f, speeds: e.target.value.split(/[,;\s]+/).map(x => x.trim().toUpperCase()).filter(Boolean) })} />
                </Field>
              </div>
            </div>
          )}

          {tab === "system" && (
            <div className="space-y-3">
              <div className={sec}>
                <div className="grid sm:grid-cols-2 gap-3">
                  <Field label="NTP principal"><Input value={f.ntp[0]} onChange={e => setF({ ...f, ntp: [e.target.value, f.ntp[1]] })} placeholder="200.160.7.186" className={inp} data-testid="oltgen-ntp1" /></Field>
                  <Field label="NTP secundário"><Input value={f.ntp[1]} onChange={e => setF({ ...f, ntp: [f.ntp[0], e.target.value] })} placeholder="opcional" className={inp} /></Field>
                </div>
              </div>
              <div className={sec}>
                <Check id="og-sv" checked={f.autosave.enabled} onChange={v => up("autosave", { enabled: v })} testid="oltgen-autosave">Salvar a configuração todo dia</Check>
                {f.autosave.enabled && <Field label="Horário"><Input value={f.autosave.time} onChange={e => up("autosave", { time: e.target.value })} placeholder="03:00" className={`${inp} w-28`} /></Field>}
              </div>
              <div className={sec}>
                <Check id="og-bk" checked={f.backup.enabled} onChange={v => up("backup", { enabled: v })} testid="oltgen-backup">Backup da configuração por FTP</Check>
                {f.backup.enabled && (
                  <div className="grid sm:grid-cols-2 gap-3">
                    <Field label="Servidor FTP"><Input value={f.backup.server} onChange={e => up("backup", { server: e.target.value })} placeholder="192.0.2.50" className={inp} /></Field>
                    <Field label="Pasta"><Input value={f.backup.path} onChange={e => up("backup", { path: e.target.value })} placeholder={f.hostname} className={inp} /></Field>
                    <Field label="Usuário"><Input value={f.backup.username} onChange={e => up("backup", { username: e.target.value })} className={inp} /></Field>
                    <Field label="Senha"><Input type="password" value={f.backup.password} onChange={e => up("backup", { password: e.target.value })} className={inp} autoComplete="new-password" /></Field>
                  </div>
                )}
              </div>
              <div className={sec}>
                <Check id="og-cm" checked={f.comments} onChange={v => setF({ ...f, comments: v })}>Comentários no script</Check>
                <Check id="og-wr" checked={f.write} onChange={v => setF({ ...f, write: v })} testid="oltgen-write">Gravar a configuração no final (write)</Check>
              </div>
            </div>
          )}

          {tab === "access" && (
            <div className="space-y-3">
              <div className={sec}>
                <div className="text-sm font-semibold text-slate-200">Acesso remoto</div>
                <Check id="og-ssh" checked={f.access.ssh} onChange={v => up("access", { ssh: v })}>SSH</Check>
                <Check id="og-tel" checked={f.access.telnet} onChange={v => up("access", { telnet: v })}>Telnet</Check>
              </div>
              <div className={sec}>
                <div className="text-sm font-semibold text-slate-200">Usuário local administrador</div>
                <div className="grid sm:grid-cols-2 gap-3">
                  <Field label="Usuário"><Input value={f.access.user} onChange={e => up("access", { user: e.target.value })} placeholder="opcional" className={inp} data-testid="oltgen-user" /></Field>
                  <Field label="Senha" hint="mínimo 8 caracteres; não fica salva no navegador">
                    <Input type="password" value={f.access.password} onChange={e => up("access", { password: e.target.value })} className={inp} autoComplete="new-password" data-testid="oltgen-pass" />
                  </Field>
                </div>
              </div>
              <div className={sec}>
                <Check id="og-snmp" checked={f.snmp.enabled} onChange={v => up("snmp", { enabled: v })} testid="oltgen-snmp">SNMP v2c (somente leitura)</Check>
                {f.snmp.enabled && (
                  <div className="grid sm:grid-cols-2 gap-3">
                    <Field label="Comunidade"><Input value={f.snmp.community} onChange={e => up("snmp", { community: e.target.value })} className={inp} autoComplete="off" /></Field>
                    <Field label="Destino dos traps"><Input value={f.snmp.trap_host} onChange={e => up("snmp", { trap_host: e.target.value })} placeholder="opcional" className={inp} /></Field>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <ScriptCard out={out} err={err} busy={busy} />
      </div>
    </div>
  );
}

// ---------- Autorizar ONU ----------
const ONU_KEY = "bastion_oltgen_onu";
const NEW_SVC = (vlan = "", mode = "tag", ports = [1]) => ({ vlan, user_vlan: "", mode, ports });
const ONU_DEFAULT = { platform: "titan", pon: "1/1/1", onu_id: "1", type: "ZTE-F660", sn: "", name: "", description: "",
  tcont_profile: "1G", services: [NEW_SVC("100")], write: true, comments: true, action: "new", start_index: "2", tcont_id: "1" };
const MODE_INFO = [
  ["tag", "Tag", "sai sem tag na porta; a ONU marca a VLAN (roteador do cliente em PPPoE/DHCP)"],
  ["hybrid", "Híbrida", "VLAN padrão sem tag na porta + as outras VLANs da porta com tag"],
  ["transparent", "Transparente", "passa com tag até o equipamento do cliente (L2, corporativo, IPTV…)"],
];
// formulário salvo no formato antigo (uma VLAN só) vira a lista de VLANs
const loadOnu = () => {
  try {
    const v = JSON.parse(localStorage.getItem(ONU_KEY) || "{}");
    if (!Array.isArray(v.services) && v.vlan) v.services = [{ vlan: v.vlan, user_vlan: v.user_vlan || "", mode: v.mode || "tag", ports: v.ports || [1] }];
    ["vlan", "user_vlan", "mode", "ports"].forEach(k => delete v[k]);
    return { ...ONU_DEFAULT, ...v, sn: "", name: "", description: "" };
  } catch { return ONU_DEFAULT; }
};

function OnuAuthorize({ meta, devices }) {
  const [f, setF] = useState(loadOnu);
  const [out, setOut] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [devId, setDevId] = useState("");
  const [found, setFound] = useState(null);
  const [searching, setSearching] = useState(false);
  const [existing, setExisting] = useState(null);
  const [reading, setReading] = useState(false);
  const seq = useRef(0);
  const ethMax = meta?.onu_types.find(o => o.name === f.type)?.eth || 4;

  useEffect(() => {
    const { sn, name, description, ...keep } = f;
    try { localStorage.setItem(ONU_KEY, JSON.stringify(keep)); } catch { /* sem armazenamento */ }
    const adding = f.action === "add";
    if (adding ? !(f.pon && f.onu_id) : !(f.sn && f.name)) { setOut(null); setErr(""); setBusy(false); return undefined; }
    const n = ++seq.current; setBusy(true);
    const t = setTimeout(() => {
      (adding ? api.post("/oltgen/onu-add", { ...f, existing: existing?.existing || null }) : api.post("/oltgen/onu", f)).then(r => { if (n === seq.current) { setOut(r.data); setErr(""); } })
        .catch(e => { if (n === seq.current) setErr(formatApiError(e)); })
        .finally(() => { if (n === seq.current) setBusy(false); });
    }, 300);
    return () => clearTimeout(t);
  }, [f, existing]); // eslint-disable-line react-hooks/exhaustive-deps

  // VLAN a mais em ONU autorizada: lê o que a ONU já tem para continuar a numeração e somar as portas
  const readOnu = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada (no alto)");
    setReading(true);
    try {
      const { data } = await api.post("/oltgen/onu-read", { device_id: devId, platform: f.platform, pon: f.pon, onu_id: Number(f.onu_id) });
      setExisting({ ...data, key: `${f.platform}|${f.pon}|${f.onu_id}` });
    } catch (e) { setExisting(null); toast.error(formatApiError(e)); } finally { setReading(false); }
  };
  useEffect(() => {
    if (existing && existing.key !== `${f.platform}|${f.pon}|${f.onu_id}`) setExisting(null);
  }, [f.platform, f.pon, f.onu_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const search = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setSearching(true); setFound(null);
    try {
      const { data } = await api.post("/oltgen/uncfg", { device_id: devId });
      setFound(data);
      if (data.platform) set({ platform: data.platform });
      if (!data.onus.length) toast.info(`${data.device}: nenhuma ONU esperando autorização`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setSearching(false); }
  };
  const pickUncfg = (o) => setF(cur => ({ ...cur, action: "new", pon: o.pon, sn: o.sn, onu_id: o.free_id ? String(o.free_id) : cur.onu_id }));
  const set = (patch) => setF(cur => ({ ...cur, ...patch }));
  const setSvc = (i, patch) => setF(cur => ({ ...cur, services: cur.services.map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
  const addSvc = () => setF(cur => ({ ...cur, services: [...cur.services, NEW_SVC("", "transparent", cur.services[0]?.ports || [1])] }));
  const delSvc = (i) => setF(cur => ({ ...cur, services: cur.services.filter((_, j) => j !== i) }));
  const portsN = Math.max(ethMax, ...f.services.flatMap(x => x.ports));

  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="onu-page">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]">
              <Field label="Buscar ONUs esperando autorização numa OLT cadastrada">
                <select value={devId} onChange={e => setDevId(e.target.value)} data-testid="onu-device"
                  className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
                  <option value="">{devices.length ? "Escolha a OLT…" : "Nenhum equipamento ZTE cadastrado"}</option>
                  <TagOptions devices={devices} label={d => `${d.name} · ${d.host}`} />
                </select>
              </Field>
            </div>
            <Button onClick={search} disabled={searching || !devId} variant="outline" className="border-line bg-transparent h-9" data-testid="onu-search">
              {searching ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Buscar não autorizadas
            </Button>
          </div>
          {found && found.onus.length > 0 && (
            <div className="border border-line rounded-md divide-y divide-line" data-testid="onu-found">
              {found.onus.map((o, i) => (
                <div key={i} className="flex items-center gap-3 px-3 py-1.5 text-xs font-mono">
                  <span className="text-slate-400 w-20">{o.pon}</span>
                  <span className="text-slate-100 flex-1">{o.sn}</span>
                  <span className="text-slate-500">ID livre {o.free_id ?? "—"}</span>
                  <Button size="sm" variant="ghost" onClick={() => pickUncfg(o)} className="h-6 px-2 text-brand-soft hover:bg-slate-800" data-testid={`onu-use-${i}`}>Usar</Button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div className={sec}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">Série da OLT</span>
            {Object.entries(meta?.platforms || { titan: "TITAN (C600/C650/C620/C610)", c300: "C300 / C320" }).map(([k, l]) => (
              <Toggle key={k} on={f.platform === k} label={l} testid={`onu-plat-${k}`} onClick={() => set({ platform: k })} />
            ))}
          </div>
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs w-fit" data-testid="onu-action">
            {[["new", "Nova ONU"], ["add", "VLAN a mais em ONU autorizada"]].map(([k, l]) => (
              <button key={k} type="button" onClick={() => set({ action: k })} data-testid={`onu-action-${k}`}
                className={`px-3 h-7 rounded ${f.action === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
            ))}
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Porta PON" hint="slot/porta, ex.: 1/1/3"><Input value={f.pon} onChange={e => set({ pon: e.target.value })} className={inp} data-testid="onu-pon" /></Field>
            <Field label="ID da ONU" hint="1 a 128, livre na PON"><Input value={f.onu_id} onChange={e => set({ onu_id: e.target.value })} className={inp} data-testid="onu-id" /></Field>
            {f.action === "add" ? (
              <div className="flex items-start pt-5">
                <Button onClick={readOnu} disabled={reading || !devId} variant="outline" className="border-line bg-transparent h-9 w-full" data-testid="onu-read"
                  title={devId ? "" : "Escolha a OLT no alto da tela"}>
                  {reading ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Ler ONU na OLT
                </Button>
              </div>
            ) : <Field label="Serial (SN)"><Input value={f.sn} onChange={e => set({ sn: e.target.value.toUpperCase().trim() })} placeholder="ZTEGC1A2B3C4" className={inp} data-testid="onu-sn" /></Field>}
          </div>
          {f.action === "add" && (existing ? (
            <div className="rounded-md border border-line bg-sunken/60 px-3 py-2 text-xs font-mono space-y-1" data-testid="onu-existing">
              <div className="text-slate-100"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1 text-emerald-400" />{existing.interface} · {existing.name || "sem nome"} · {existing.sn} · {existing.type}</div>
              <div className="text-slate-400">VLANs atuais: {existing.existing.vlans.length ? existing.existing.vlans.join(", ") : "nenhuma"}
                {Object.entries(existing.existing.ports || {}).map(([e, pc]) => ` · eth ${e}: ${pc.transparent && !pc.untag ? "transparente" : pc.untag ? `${pc.untag_mode === "hybrid" ? "híbrida" : "tag"} ${pc.untag}${pc.tagged.length ? ` + ${pc.tagged.join(",")}` : ""}` : pc.tagged.join(",")}`).join("")}</div>
              <div className="text-slate-500">próximos: gemport {existing.existing.next.gemport} · service-port {existing.existing.next.service_port} · service {existing.existing.next.service}</div>
            </div>
          ) : (
            <div className="grid sm:grid-cols-3 gap-3" data-testid="onu-noread">
              <Field label="Primeiro índice livre" hint="gemport/service-port novos começam aqui"><Input value={f.start_index} onChange={e => set({ start_index: e.target.value })} className={inp} data-testid="onu-start" /></Field>
              <Field label="T-CONT da ONU" hint="normalmente 1"><Input value={f.tcont_id} onChange={e => set({ tcont_id: e.target.value })} className={inp} /></Field>
              <div className="text-[11px] text-amber-300/90 pt-5">Sem ler a ONU, confira os índices e o modo das portas que ela já tem.</div>
            </div>
          ))}
          {f.action !== "add" && <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Tipo (onu-type)">
              <Input value={f.type} onChange={e => set({ type: e.target.value })} list="onu-types" className={inp} data-testid="onu-type" />
              <datalist id="onu-types">{(meta?.onu_types || []).map(o => <option key={o.name} value={o.name} />)}</datalist>
            </Field>
            <Field label="Nome do cliente"><Input value={f.name} onChange={e => set({ name: e.target.value })} placeholder="cliente_0001" className={inp} data-testid="onu-name" /></Field>
            <Field label="Descrição"><Input value={f.description} onChange={e => set({ description: e.target.value })} placeholder="opcional" className={inp} /></Field>
          </div>}
        </div>

        <div className={sec} data-testid="onu-services">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="text-sm font-semibold text-slate-200">{f.action === "add" ? "VLANs novas" : "VLANs da ONU"}</div>
            <div className={`w-40 ${f.action === "add" ? "hidden" : ""}`}>
              <Field label="Perfil de banda (T-CONT)"><Input value={f.tcont_profile} onChange={e => set({ tcont_profile: e.target.value })} className={inp} data-testid="onu-tcont" /></Field>
            </div>
          </div>
          {f.services.map((sv, i) => (
            <div key={i} className="rounded-md border border-line p-3 space-y-2" data-testid={`onu-svc-${i}`}>
              <div className="flex flex-wrap items-end gap-2">
                <div className="w-24"><Field label="VLAN"><Input value={sv.vlan} onChange={e => setSvc(i, { vlan: e.target.value })} className={inp} data-testid={`onu-svc-${i}-vlan`} /></Field></div>
                {sv.mode !== "transparent" && (
                  <div className="w-28"><Field label="VLAN na ONU"><Input value={sv.user_vlan} onChange={e => setSvc(i, { user_vlan: e.target.value })} placeholder="a mesma" className={inp} data-testid={`onu-svc-${i}-uvlan`} /></Field></div>
                )}
                <div className="flex gap-1 pb-0.5">
                  {MODE_INFO.map(([k, l, d]) => (
                    <button key={k} type="button" title={d} onClick={() => setSvc(i, { mode: k, ...(k === "transparent" ? { user_vlan: "" } : {}) })} data-testid={`onu-svc-${i}-mode-${k}`}
                      className={`h-9 px-2.5 rounded-md text-xs border ${sv.mode === k ? "border-brand/70 bg-brand/15 text-brand-soft" : "border-line text-slate-400 hover:text-slate-200"}`}>{l}</button>
                  ))}
                </div>
                {f.services.length > 1 && (
                  <button type="button" onClick={() => delSvc(i)} title="Remover VLAN" data-testid={`onu-svc-${i}-del`}
                    className="ml-auto h-9 px-2 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[11px] text-slate-500 mr-1">portas</span>
                {Array.from({ length: portsN }, (_, k) => k + 1).map(n => (
                  <Toggle key={n} on={sv.ports.includes(n)} label={`eth ${n}`} testid={`onu-svc-${i}-eth-${n}`}
                    onClick={() => setSvc(i, { ports: sv.ports.includes(n) ? sv.ports.filter(x => x !== n) : [...sv.ports, n].sort((a, b) => a - b) })} />
                ))}
              </div>
              <div className="text-[11px] text-slate-500">{MODE_INFO.find(m => m[0] === sv.mode)?.[2]}</div>
            </div>
          ))}
          {f.services.length < 8 && (
            <Button size="sm" variant="outline" onClick={addSvc} className="border-line bg-transparent h-8 text-xs" data-testid="onu-svc-add"><Plus className="w-3.5 h-3.5 mr-1" />Adicionar VLAN</Button>
          )}
          <div className="text-[11px] text-slate-500">Cada porta aceita uma VLAN sem tag (Tag ou Híbrida). Uma VLAN Tag e outras transparentes na mesma porta viram porta híbrida.</div>
          <Check id="onu-wr" checked={f.write} onChange={v => set({ write: v })}>Gravar no final (write)</Check>
        </div>
      </div>
      {(f.action === "add" ? f.pon && f.onu_id : f.sn && f.name) ? <ScriptCard out={out} err={err} busy={busy}
          apply={{ devices, deviceId: devId, kind: f.action === "add" ? "onu-add" : "onu",
            params: f.action === "add" ? { ...f, existing: existing?.existing || null } : f,
            onDone: () => setFound(null) }} />
        : <Card className="bg-surface border-line flex items-center justify-center min-h-[320px] text-sm text-slate-500 p-6 text-center" data-testid="onu-empty">
            {f.action === "add" ? "Informe a porta PON e o ID da ONU." : "Informe o serial e o nome do cliente (ou busque as ONUs não autorizadas) para gerar o script."}</Card>}
    </div>
  );
}

// ---------- Desautorizar ONU ----------
const RM_KEY = "bastion_oltgen_rm";
const NEW_RM = () => ({ slot: "", pon: "", onu: "" });

function OnuRemove({ meta, devices }) {
  const [plat, setPlat] = useState(() => { try { return JSON.parse(localStorage.getItem(RM_KEY) || "{}").platform || "titan"; } catch { return "titan"; } });
  const [rows, setRows] = useState([NEW_RM()]);
  const [write, setWrite] = useState(true);
  const [out, setOut] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [devId, setDevId] = useState("");
  const [info, setInfo] = useState(null);                // conferência na OLT: chave "s/p:o" -> dados
  const [checking, setChecking] = useState(false);
  const [mac, setMac] = useState("");
  const [macDev, setMacDev] = useState("all");
  const [macRes, setMacRes] = useState(null);
  const [macBusy, setMacBusy] = useState(false);
  const seq = useRef(0);
  const filled = rows.filter(r => r.slot !== "" && r.pon !== "" && r.onu !== "");
  const key = (r) => `${Number(r.slot)}/${Number(r.pon)}:${Number(r.onu)}`;

  useEffect(() => {
    try { localStorage.setItem(RM_KEY, JSON.stringify({ platform: plat })); } catch { /* ok */ }
    if (!filled.length) { setOut(null); setErr(""); setBusy(false); return undefined; }
    const n = ++seq.current; setBusy(true);
    const t = setTimeout(() => {
      api.post("/oltgen/onu-remove", { platform: plat, items: filled, write })
        .then(r => { if (n === seq.current) { setOut(r.data); setErr(""); } })
        .catch(e => { if (n === seq.current) setErr(formatApiError(e)); })
        .finally(() => { if (n === seq.current) setBusy(false); });
    }, 300);
    return () => clearTimeout(t);
  }, [rows, plat, write]); // eslint-disable-line react-hooks/exhaustive-deps

  const setRow = (i, patch) => { setRows(rs => rs.map((r, j) => (j === i ? { ...r, ...patch } : r))); };
  const num = (v) => v.replace(/\D/g, "").slice(0, 3);
  const check = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    if (!filled.length) return toast.error("Informe slot, PON e ONU");
    setChecking(true);
    try {
      const { data } = await api.post("/oltgen/onu-info", { device_id: devId, platform: plat, items: filled });
      setInfo(Object.fromEntries(data.items.map(it => [`${it.slot}/${it.pon}:${it.onu}`, it])));
      const miss = data.items.filter(it => !it.found).length;
      if (miss) toast.warning(`${miss} ONU(s) não existem nessa OLT`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setChecking(false); }
  };

  const findMac = async () => {
    if (!mac.trim()) return toast.error("Informe o serial da ONU");
    setMacBusy(true); setMacRes(null);
    try {
      const { data } = await api.post("/oltgen/find-onu", { query: mac, device_id: macDev, vendor: "zte" });
      setMacRes(data);
      if (!data.found) toast.info(`${data.kind === "sn" ? "Serial" : "MAC"} ${data.query} não encontrado${data.searched?.length ? ` (${data.searched.join(", ")})` : ""}`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setMacBusy(false); }
  };
  const takeMacResult = (r) => {
    const row = { slot: String(r.slot), pon: String(r.pon), onu: String(r.onu) };
    // o script vale para uma OLT só: se a ONU achada é de outra OLT/série, a lista recomeça com ela
    const other = r.platform !== plat || (devId && devId !== r.device_id);
    setPlat(r.platform);
    setRows(rs => (other ? [row] : [...rs.filter(x => (x.slot || x.pon || x.onu) && key(x) !== key(row)), row]));
    setDevId(r.device_id || "");
    setInfo(cur => ({ ...(other ? {} : cur || {}), [key(row)]: { ...r, found: true } }));
    if (other && filled.length) toast.info("A lista foi trocada: essa ONU é de outra OLT");
  };

  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="onurm-page">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3" data-testid="onumac">
          <div className="text-sm font-semibold text-slate-200">Procurar ONU pelo serial (SN)</div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-56">
              <Field label="Serial da ONU (ou MAC do cliente)">
                <Input value={mac} onChange={e => setMac(e.target.value)} onKeyDown={e => e.key === "Enter" && findMac()}
                  placeholder="ZTEGD4F3D9EC" className={inp} data-testid="onumac-mac" />
              </Field>
            </div>
            <div className="flex-1 min-w-[180px]">
              <Field label="Em qual OLT">
                <select value={macDev} onChange={e => setMacDev(e.target.value)} data-testid="onumac-device"
                  className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
                  <option value="all">Todas as OLTs ZTE ({devices.length})</option>
                  <TagOptions devices={devices} label={d => `${d.name} · ${d.host}`} />
                </select>
              </Field>
            </div>
            <Button onClick={findMac} disabled={macBusy || !devices.length} variant="outline" className="border-line bg-transparent h-9" data-testid="onumac-go">
              {macBusy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Procurar
            </Button>
          </div>
          {macRes?.found && (
            <div className="rounded-md border border-line bg-sunken/60 px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-mono" data-testid="onumac-result">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span className="text-slate-100">{macRes.device}</span>
              <span className="text-brand-soft">{macRes.platform === "c300" ? `gpon-onu_1/${macRes.slot}/${macRes.pon}:${macRes.onu}` : `gpon_onu-1/${macRes.slot}/${macRes.pon}:${macRes.onu}`}</span>
              {macRes.kind === "mac" && <span className="text-slate-500">MAC {macRes.query}</span>}
              {macRes.vlan && <span className="text-slate-400">VLAN {macRes.vlan}</span>}
              <span className="text-slate-200">{macRes.name || "sem nome"}</span>
              {macRes.sn && <span className="text-slate-400">{macRes.sn} · {macRes.type}</span>}
              <Button size="sm" variant="outline" onClick={() => takeMacResult(macRes)} className="ml-auto h-7 border-red-800/60 bg-transparent text-red-300 hover:bg-red-950/40" data-testid="onumac-use">
                <Unplug className="w-3.5 h-3.5 mr-1" />Desautorizar esta</Button>
            </div>
          )}
          {macRes && !macRes.found && (
            <div className="text-xs text-amber-300" data-testid="onumac-none">
              {macRes.kind === "sn"
                ? `Serial ${macRes.query} não está autorizado ${macRes.searched?.length ? `em ${macRes.searched.join(", ")}` : "nas OLTs"}.`
                : `MAC ${macRes.query} não aparece ${macRes.searched?.length ? `em ${macRes.searched.join(", ")}` : "nas OLTs"}: o equipamento pode estar desligado ou o MAC expirou na tabela.`}
              {macRes.errors?.length ? <span className="block text-red-300 mt-1">Sem acesso: {macRes.errors.join(" · ")}</span> : null}
            </div>
          )}
        </Card>
        <div className={sec}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">Série da OLT</span>
            {Object.entries(meta?.platforms || { titan: "TITAN (C600/C650/C620/C610)", c300: "C300 / C320" }).map(([k, l]) => (
              <Toggle key={k} on={plat === k} label={l} testid={`onurm-plat-${k}`} onClick={() => { setPlat(k); setInfo(null); }} />
            ))}
          </div>
          <div className="space-y-2" data-testid="onurm-rows">
            {rows.map((r, i) => {
              const it = info && filled.includes(r) ? info[key(r)] : null;
              return (
                <div key={i} className="flex flex-wrap items-end gap-2">
                  {[["slot", "Slot"], ["pon", "PON"], ["onu", "ONU"]].map(([k, l]) => (
                    <div key={k} className="w-20">
                      <Field label={i === 0 ? l : ""}>
                        <Input value={r[k]} inputMode="numeric" onChange={e => { setRow(i, { [k]: num(e.target.value) }); setInfo(null); }}
                          onKeyDown={e => { if (e.key === "Enter" && k === "onu" && i === rows.length - 1) setRows(rs => [...rs, { ...NEW_RM(), slot: r.slot, pon: r.pon }]); }}
                          className={inp} data-testid={`onurm-${i}-${k}`} />
                      </Field>
                    </div>
                  ))}
                  <div className="text-xs font-mono pb-2 min-w-0 flex-1 truncate" data-testid={`onurm-${i}-info`}>
                    {it ? (it.found
                      ? <span className="text-slate-200"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1 text-emerald-400" />{it.name || "sem nome"} · {it.sn} · {it.type}</span>
                      : <span className="text-amber-300"><XCircle className="w-3.5 h-3.5 inline mr-1" />não existe nessa PON</span>)
                      : <span className="text-slate-500">{r.slot && r.pon && r.onu ? (plat === "c300" ? `gpon-onu_1/${r.slot}/${r.pon}:${r.onu}` : `gpon_onu-1/${r.slot}/${r.pon}:${r.onu}`) : ""}</span>}
                  </div>
                  {rows.length > 1 && (
                    <button type="button" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))} title="Tirar da lista" data-testid={`onurm-${i}-del`}
                      className="h-9 px-2 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>
                  )}
                </div>
              );
            })}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" variant="outline" onClick={() => setRows(rs => [...rs, { ...NEW_RM(), slot: rs[rs.length - 1]?.slot || "", pon: rs[rs.length - 1]?.pon || "" }])}
              className="border-line bg-transparent h-8 text-xs" data-testid="onurm-add"><Plus className="w-3.5 h-3.5 mr-1" />Outra ONU</Button>
            <span className="text-[11px] text-slate-500">Enter no campo ONU também abre outra linha.</span>
          </div>
          <Check id="onurm-wr" checked={write} onChange={setWrite}>Gravar no final (write)</Check>
        </div>
        <Card className="bg-surface border-line p-4 space-y-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]">
              <Field label="Conferir na OLT quem é cada ONU antes de remover (só leitura)">
                <select value={devId} onChange={e => { setDevId(e.target.value); setInfo(null); }} data-testid="onurm-device"
                  className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
                  <option value="">{devices.length ? "Escolha a OLT…" : "Nenhum equipamento ZTE cadastrado"}</option>
                  <TagOptions devices={devices} label={d => `${d.name} · ${d.host}`} />
                </select>
              </Field>
            </div>
            <Button onClick={check} disabled={checking || !devId || !filled.length} variant="outline" className="border-line bg-transparent h-9" data-testid="onurm-check">
              {checking ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Conferir
            </Button>
          </div>
        </Card>
      </div>
      {filled.length ? <ScriptCard out={out} err={err} busy={busy}
          apply={{ devices, deviceId: devId, kind: "onu-remove", params: { platform: plat, items: filled }, onDone: () => setInfo(null) }} />
        : <Card className="bg-surface border-line flex items-center justify-center min-h-[320px] text-sm text-slate-500 p-6 text-center" data-testid="onurm-empty">
            Informe slot, PON e número da ONU para gerar o script.</Card>}
    </div>
  );
}

// ---------- ONUs off há mais de N dias: procura, confere e remove em massa ----------
const CLEAN_KEY = "bastion_oltgen_clean";

function useCleanJob() {
  const [job, setJob] = useState(null);
  const t = useRef(null);
  const follow = (j) => {
    setJob(j);
    clearTimeout(t.current);
    if (j?.state === "running") t.current = setTimeout(() => api.get(`/oltgen/clean-jobs/${j.id}`).then(r => follow(r.data)).catch(e => { toast.error(formatApiError(e)); setJob(null); }), 1000);
  };
  useEffect(() => () => clearTimeout(t.current), []);
  return [job, follow];
}

function JobBar({ job, what }) {
  if (!job || job.state !== "running") return null;
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  return (
    <div className="rounded-md border border-brand/40 bg-brand/5 p-3" data-testid="clean-progress">
      <div className="flex items-center gap-2 text-sm text-slate-200"><Loader2 className="w-4 h-4 animate-spin text-brand-soft" /> {what} — {job.device}</div>
      <div className="h-1.5 bg-sunken rounded mt-2 overflow-hidden"><div className="h-full bg-brand transition-all" style={{ width: `${pct}%` }} /></div>
      <div className="text-[11px] text-slate-500 font-mono mt-1">{job.step} {job.total ? `· ${job.done}/${job.total}` : ""}</div>
    </div>
  );
}

const fmtDay = (d) => (d === null || d === undefined ? "—" : d >= 1 ? `${Math.floor(d)} dia(s)` : `${Math.round(d * 24)} h`);

const ponLabel = (o) => (o.frame !== undefined && o.frame !== null ? `${o.frame}/${o.slot}/${o.pon} ONT ${o.onu}` : `1/${o.slot}/${o.pon}:${o.onu}`);

function RemovedList({ deviceId, refresh }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api.get("/oltgen/removed", { params: { device_id: deviceId || "" } }).then(r => setRows(r.data)).catch(() => setRows([])); }, [deviceId, refresh]);
  if (!rows || !rows.length) return null;
  const dl = () => {
    const txt = rows.map(r => `! ${r.at} · ${r.device_name} · ${ponLabel(r)} · ${r.sn || ""} · ${r.name || ""} · off ${fmtDay(r.days)} · removida por ${r.by}\n${r.config || ""}\n`).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" }));
    a.download = `onus-removidas-${new Date().toISOString().slice(0, 10)}.txt`; a.click();
  };
  return (
    <div className={sec} data-testid="clean-removed">
      <div className="flex items-center gap-2 text-sm text-slate-200"><History className="w-4 h-4 text-slate-400" /> Removidas pela limpeza ({rows.length})
        <Button size="sm" variant="ghost" onClick={dl} className="ml-auto h-7 text-xs text-brand-soft" data-testid="clean-removed-dl"><Download className="w-3.5 h-3.5 mr-1" /> Configurações (.txt)</Button>
      </div>
      <p className="text-[11px] text-slate-500">A configuração de cada ONU é guardada antes de remover: se o cliente voltar, use o arquivo para reautorizar igual.</p>
      <div className="max-h-48 overflow-y-auto text-xs font-mono divide-y divide-line">
        {rows.slice(0, 200).map(r => (
          <div key={r.id} className="py-1 flex gap-3 text-slate-300"><span className="text-slate-500 w-32 shrink-0">{new Date(r.at).toLocaleString("pt-BR")}</span>
            <span className="w-28 shrink-0">{ponLabel(r)}</span><span className="w-32 shrink-0">{r.sn}</span><span className="truncate">{r.name}</span></div>
        ))}
      </div>
    </div>
  );
}

function OnuClean({ devices, vendor = "zte" }) {
  const hw = vendor === "huawei";
  const ckey = CLEAN_KEY + (hw ? "_hw" : "");
  const saved = (() => { try { return JSON.parse(localStorage.getItem(ckey) || "{}"); } catch { return {}; } })();
  const [devId, setDevId] = useState(saved.devId || "");
  const [days, setDays] = useState(saved.days || 15);
  const [pons, setPons] = useState("");
  const [scan, followScan] = useCleanJob();
  const [rm, followRm] = useCleanJob();
  const [sel, setSel] = useState(new Set());
  const [onlyEl, setOnlyEl] = useState(true);
  const [confirmTxt, setConfirmTxt] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { try { localStorage.setItem(ckey, JSON.stringify({ devId, days })); } catch { /* ok */ } }, [devId, days]); // eslint-disable-line react-hooks/exhaustive-deps
  const k = (o) => `${o.slot}/${o.pon}:${o.onu}`;
  const result = scan?.state === "done" ? scan : null;
  useEffect(() => { if (result) setSel(new Set(result.onus.filter(o => o.eligible).map(k))); }, [result?.id, result?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (rm?.state === "done") { toast[rm.ok ? "success" : "error"](rm.ok ? `${rm.removed.length} ONU(s) removidas e gravado` : `Parou no erro: ${rm.failed?.command || ""}`); setRefresh(x => x + 1); setConfirmTxt(""); }
    if (rm?.state === "error") toast.error(rm.error);
    if (scan?.state === "error") toast.error(scan.error);
  }, [rm?.state, scan?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    if (!devId) return toast.error("Escolha a OLT");
    const list = pons.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean);
    if (hw ? list.some(x => !/^\d{1,2}\/\d{1,2}\/\d{1,3}$/.test(x)) : list.some(x => !/^\d{1,2}\/\d{1,2}$/.test(x)))
      return toast.error(hw ? "PONs no formato F/S/P, ex.: 0/1/0, 0/1/1" : "PONs no formato slot/porta, ex.: 2/1, 2/2");
    try { followRm(null); followScan((await api.post("/oltgen/offline-scan", { device_id: devId, days: Number(days) || 15, pons: list })).data); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const remove = async () => {
    const items = result.onus.filter(o => sel.has(k(o))).map(o => ({ slot: o.slot, pon: o.pon, onu: o.onu, ...(o.frame !== undefined ? { frame: o.frame } : {}) }));
    try { followRm((await api.post("/oltgen/offline-remove", { scan_id: result.id, items })).data); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const csv = () => {
    const head = "pon;onu;serial;nome;tipo;estado;caiu_em;dias_off;causa\n";
    const body = result.onus.map(o => [o.frame !== undefined ? `${o.frame}/${o.slot}/${o.pon}` : `1/${o.slot}/${o.pon}`, o.onu, o.sn, o.name, o.type, o.phase, o.last_down || "", o.days ?? "", o.cause].join(";")).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([head + body], { type: "text/csv" }));
    a.download = `onus-off-${result.device}.csv`; a.click();
  };
  const shown = result ? result.onus.filter(o => !onlyEl || o.eligible) : [];
  const nSel = sel.size;
  const allShown = shown.length > 0 && shown.every(o => sel.has(k(o)));
  const flip = (o) => setSel(s => { const n = new Set(s); n.has(k(o)) ? n.delete(k(o)) : n.add(k(o)); return n; });
  const running = scan?.state === "running" || rm?.state === "running";

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-6xl" data-testid="oltgen-clean">
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="text-sm text-slate-300">Procura as ONUs <b>off</b> (LOS, DyingGasp, OffLine) e há quantos dias cada uma caiu, pela hora da própria OLT. Nada é removido sem você escolher e confirmar.</div>
        <div className="grid grid-cols-1 md:grid-cols-[1fr_8rem_12rem_auto] gap-3 items-end">
          <Field label="OLT">
            <select value={devId} onChange={e => setDevId(e.target.value)} data-testid="clean-device" className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100">
              <option value="">— escolha —</option>
              <TagOptions devices={devices} label={d => `${d.name} (${d.host})`} />
            </select>
          </Field>
          <Field label="Off há mais de (dias)"><Input type="number" min={1} value={days} onChange={e => setDays(e.target.value)} className={inp} data-testid="clean-days" /></Field>
          <Field label="Só estas PONs (opcional)"><Input value={pons} onChange={e => setPons(e.target.value)} placeholder={hw ? "0/1/0, 0/1/1" : "2/1, 2/2"} className={inp} data-testid="clean-pons" /></Field>
          <Button onClick={start} disabled={running} className="bg-brand hover:bg-brand-strong text-onbrand h-9" data-testid="clean-scan">
            {scan?.state === "running" ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ScanSearch className="w-4 h-4 mr-1.5" />} Procurar ONUs off
          </Button>
        </div>
        <JobBar job={scan} what="Lendo a OLT" />
        {(scan?.state === "error" || rm?.state === "error") && (
          <div className="rounded-md border border-red-500/40 bg-red-500/5 p-3 text-sm" data-testid="clean-error">
            <div className="text-red-300 flex items-center gap-1.5"><XCircle className="w-4 h-4" /> {(scan?.state === "error" ? scan : rm).error}</div>
            {(scan?.state === "error" ? scan : rm).diag && (
              <>
                <div className="text-xs text-slate-400 mt-2">Último comando: <span className="font-mono text-slate-200">{(scan?.state === "error" ? scan : rm).diag.command}</span> — resposta da OLT:</div>
                <pre className="text-[11px] text-slate-300 bg-sunken p-2 rounded mt-1 max-h-48 overflow-auto whitespace-pre-wrap">{(scan?.state === "error" ? scan : rm).diag.output || "(vazia)"}</pre>
              </>
            )}
          </div>
        )}
      </Card>

      {result && (
        <Card className="bg-surface border-line p-4 space-y-3" data-testid="clean-result">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="text-slate-100 font-medium">{result.device}</span>
            <span className="text-slate-400">{result.onus_total} ONUs · <span className="text-amber-300">{result.onus.filter(o => o.off).length} off</span> · <span className="text-red-300" data-testid="clean-eligible">{result.eligible} off há {result.days}+ dias</span></span>
            {result.olt_clock && <span className="text-[11px] text-slate-500 font-mono">hora da OLT: {result.olt_clock}</span>}
            <label className="ml-auto flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" className="accent-brand" checked={onlyEl} onChange={e => setOnlyEl(e.target.checked)} data-testid="clean-only" /> só as acima de {result.days} dias</label>
            <Button size="sm" variant="ghost" onClick={csv} className="h-7 text-xs text-slate-300"><Download className="w-3.5 h-3.5 mr-1" /> CSV</Button>
          </div>
          {shown.length === 0 ? <div className="text-sm text-slate-500 py-4">{onlyEl ? `Nenhuma ONU off há ${result.days} dias ou mais.` : "Nenhuma ONU off."}</div> : (
            <div className="overflow-x-auto max-h-[28rem] overflow-y-auto border border-line rounded-md">
              <table className="w-full text-sm">
                <thead className="text-xs text-slate-500 bg-panel sticky top-0"><tr>
                  <th className="px-2 py-2 w-8"><input type="checkbox" className="accent-brand" checked={allShown} data-testid="clean-all"
                    onChange={() => setSel(s => { const n = new Set(s); shown.forEach(o => (allShown ? n.delete(k(o)) : o.off && n.add(k(o)))); return n; })} /></th>
                  <th className="text-left px-2">PON:ONU</th><th className="text-left px-2">Serial</th><th className="text-left px-2">Nome</th>
                  <th className="text-left px-2">Estado</th><th className="text-left px-2">Caiu em</th><th className="text-right px-2">Off há</th></tr></thead>
                <tbody className="divide-y divide-line">
                  {shown.map(o => (
                    <tr key={k(o)} className={sel.has(k(o)) ? "bg-red-500/5" : ""} data-testid={`clean-row-${k(o)}`}>
                      <td className="px-2 py-1.5"><input type="checkbox" className="accent-brand" disabled={!o.off} checked={sel.has(k(o))} onChange={() => flip(o)} /></td>
                      <td className="px-2 font-mono text-slate-200 whitespace-nowrap">{ponLabel(o)}</td>
                      <td className="px-2 font-mono text-xs text-slate-300">{o.sn || "—"}</td>
                      <td className="px-2 text-slate-300 truncate max-w-[14rem]" title={o.description}>{o.name || "—"}</td>
                      <td className="px-2 text-xs"><span className={o.off ? "text-red-300" : "text-amber-300"}>{o.phase}</span>{o.cause && <span className="text-slate-500"> · {o.cause}</span>}</td>
                      <td className="px-2 text-xs font-mono text-slate-400 whitespace-nowrap">{o.last_down || "sem registro"}</td>
                      <td className={`px-2 text-right font-mono whitespace-nowrap ${o.eligible ? "text-red-300" : "text-slate-400"}`}>{fmtDay(o.days)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {result.sample && (
            <details className="text-xs text-amber-300" data-testid="clean-sample"><summary className="cursor-pointer">Nenhuma ONU veio com a data da queda — ver a resposta da OLT ({result.sample.command})</summary>
              <pre className="text-[11px] text-slate-300 bg-sunken p-2 rounded mt-1 max-h-48 overflow-auto whitespace-pre-wrap">{result.sample.output}</pre></details>
          )}
          {result.onus.some(o => o.off && o.days === null) && <div className="text-[11px] text-slate-500">"sem registro": a OLT não mostrou quando a ONU caiu (histórico apagado ou reinício da OLT). Essas não são marcadas sozinhas.</div>}

          <div className="rounded-md border border-red-500/40 bg-red-500/5 p-3 space-y-2">
            <div className="flex items-start gap-2 text-xs text-red-200"><AlertTriangle className="w-4 h-4 shrink-0" />
              <span>Remove <b>{nSel}</b> ONU(s) da OLT ({hw ? <><span className="font-mono">undo service-port</span>, <span className="font-mono">ont delete</span> e <span className="font-mono">save</span></> : <><span className="font-mono">no onu</span> e <span className="font-mono">write</span></>}). Antes, o BastiON confere cada uma de novo: se voltou a ficar online ou o serial mudou, ela é pulada. A configuração de cada ONU removida fica guardada.</span></div>
            <div className="flex flex-wrap gap-2 items-center">
              <Input value={confirmTxt} onChange={e => setConfirmTxt(e.target.value)} placeholder="digite REMOVER" className={`${inp} w-44`} data-testid="clean-confirm-txt" />
              <Button onClick={remove} disabled={!nSel || confirmTxt.trim().toUpperCase() !== "REMOVER" || running}
                      className="bg-red-600 hover:bg-red-700 text-onbrand" data-testid="clean-remove">
                {rm?.state === "running" ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <PowerOff className="w-4 h-4 mr-1.5" />} Remover {nSel} ONU(s) na OLT
              </Button>
            </div>
            <JobBar job={rm} what="Conferindo e removendo" />
            {rm?.state === "done" && (
              <div className="text-sm space-y-1" data-testid="clean-done">
                <div className={rm.ok ? "text-emerald-300" : "text-red-300"}>{rm.ok ? <CheckCircle2 className="w-4 h-4 inline mr-1" /> : <XCircle className="w-4 h-4 inline mr-1" />}
                  {rm.removed.length} removida(s){rm.saved ? ` e configuração gravada (${hw ? "save" : "write"})` : ""}{rm.failed ? ` — parou em "${rm.failed.command}"` : ""}</div>
                {rm.skipped?.length > 0 && <div className="text-xs text-amber-300">Puladas: {rm.skipped.map(x => `${ponLabel(x)} (${x.why})`).join(", ")}</div>}
                {rm.failed?.output && <pre className="text-[11px] text-slate-400 bg-sunken p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap">{rm.failed.output}</pre>}
              </div>
            )}
          </div>
        </Card>
      )}
      <RemovedList deviceId={devId} refresh={refresh} />
    </div>
  );
}

// ================= OLT Huawei (MA5800 / MA5608T / MA5683T) =================
const HW_KEY = "bastion_oltgen_hw_form";
const HW_DEFAULT = {
  model: "MA5800-X7", hostname: "OLT-HW-01", uplinks: "0/8/0",
  vlans: { enabled: true, start: "100", count: "16", description: "CLIENTES" },
  mgmt: { in_enabled: false, in_vlan: "", in_ip: "", in_mask: "30", in_gw: "", out_enabled: false, out_ip: "", out_mask: "24" },
  speeds: ["100M", "300M", "500M", "1G"], srv_profile: true, ntp: ["", ""],
  snmp: { enabled: false, community: "", trap_host: "" }, autosave: { enabled: true, time: "03:00" }, comments: true, write: true,
};
const hwLoad = (key, def) => { try { const v = JSON.parse(localStorage.getItem(key) || "null"); return v ? { ...def, ...v } : def; } catch { return def; } };
const hwUps = (txt) => String(txt || "").split(/[\s,;]+/).map(x => x.trim()).filter(Boolean);

function useLiveScript(url, body, ready, deps) {
  const [out, setOut] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    if (!ready) { setOut(null); setErr(""); setBusy(false); return undefined; }
    const n = ++seq.current; setBusy(true);
    const t = setTimeout(() => {
      api.post(url, body).then(r => { if (n === seq.current) { setOut(r.data); setErr(""); } })
        .catch(e => { if (n === seq.current) setErr(formatApiError(e)); })
        .finally(() => { if (n === seq.current) setBusy(false); });
    }, 320);
    return () => clearTimeout(t);
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return [out, err, busy];
}

function DevSelect({ devices, value, onChange, testid, empty = "Nenhuma OLT Huawei cadastrada" }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} data-testid={testid}
      className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
      <option value="">{devices.length ? "Escolha a OLT…" : empty}</option>
      <TagOptions devices={devices} label={d => `${d.name} · ${d.host}`} />
    </select>
  );
}

function HwActivation({ devices, models }) {
  const [f, setF] = useState(() => hwLoad(HW_KEY, HW_DEFAULT));
  const [devId, setDevId] = useState("");
  const [boards, setBoards] = useState(null);
  const [reading, setReading] = useState(false);
  const body = { ...f, uplinks: hwUps(f.uplinks) };
  const [out, err, busy] = useLiveScript("/oltgen/hw/generate", body, true, [f]);
  useEffect(() => { try { localStorage.setItem(HW_KEY, JSON.stringify({ ...f, snmp: { ...f.snmp, community: "" } })); } catch { /* ok */ } }, [f]);
  const up = (k, patch) => setF(cur => ({ ...cur, [k]: { ...cur[k], ...patch } }));
  const read = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setReading(true);
    try {
      const { data } = await api.post("/oltgen/hw/read-board", { device_id: devId });
      setBoards(data);
      toast.success(`${data.device}: ${data.pon.length} placa(s) PON`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setReading(false); }
  };
  const speedsText = f.speeds.join(", ");
  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="hw-activation">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Modelo">
              <select value={f.model} onChange={e => setF({ ...f, model: e.target.value })} data-testid="hw-model"
                className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
                {(models.length ? models : [{ key: "MA5800-X7" }]).map(m => <option key={m.key} value={m.key}>{m.key}</option>)}
              </select>
            </Field>
            <Field label="Hostname (sysname)"><Input value={f.hostname} onChange={e => setF({ ...f, hostname: e.target.value })} className={inp} data-testid="hw-hostname" /></Field>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]"><Field label="Ver as placas de uma OLT cadastrada (display board 0)">
              <DevSelect devices={devices} value={devId} onChange={setDevId} testid="hw-device" /></Field></div>
            <Button onClick={read} disabled={reading || !devId} variant="outline" className="border-line bg-transparent h-9" data-testid="hw-read">
              {reading ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ScanSearch className="w-4 h-4 mr-1.5" />}Ler placas
            </Button>
          </div>
          {boards && (
            <div className="text-xs font-mono text-slate-300 flex flex-wrap gap-2" data-testid="hw-boards">
              {boards.pon.map(b => <span key={b.slot} className="px-2 py-0.5 rounded border border-line">slot {b.slot} · {b.name} · {b.ports} PON</span>)}
            </div>
          )}
          <div className="text-[11px] text-slate-500">No Huawei as placas e portas PON já vêm ativas; o script cuida de VLANs, uplink, gerência, perfis e sistema.</div>
        </Card>

        <div className={sec}>
          <Field label="Porta(s) de uplink" hint="F/S/P separadas por vírgula, ex.: 0/8/0, 0/9/0 (placa de controle ou de uplink)">
            <Input value={f.uplinks} onChange={e => setF({ ...f, uplinks: e.target.value })} className={inp} data-testid="hw-uplinks" />
          </Field>
          <Check id="hw-vl" checked={f.vlans.enabled} onChange={v => up("vlans", { enabled: v })} testid="hw-vlans">VLANs de clientes no uplink (vlan … smart / port vlan)</Check>
          {f.vlans.enabled && (
            <div className="grid sm:grid-cols-3 gap-3">
              <Field label="VLAN inicial"><Input value={f.vlans.start} onChange={e => up("vlans", { start: e.target.value })} className={inp} data-testid="hw-vlan-start" /></Field>
              <Field label="Quantidade"><Input value={f.vlans.count} onChange={e => up("vlans", { count: e.target.value })} className={inp} data-testid="hw-vlan-count" /></Field>
              <Field label="Descrição"><Input value={f.vlans.description} onChange={e => up("vlans", { description: e.target.value })} className={inp} /></Field>
            </div>
          )}
        </div>

        <div className={sec}>
          <Check id="hw-in" checked={f.mgmt.in_enabled} onChange={v => up("mgmt", { in_enabled: v })} testid="hw-in">Gerência inband (vlanif pela uplink)</Check>
          {f.mgmt.in_enabled && (
            <div className="grid sm:grid-cols-4 gap-3">
              <Field label="VLAN"><Input value={f.mgmt.in_vlan} onChange={e => up("mgmt", { in_vlan: e.target.value })} placeholder="999" className={inp} data-testid="hw-in-vlan" /></Field>
              <Field label="IP"><Input value={f.mgmt.in_ip} onChange={e => up("mgmt", { in_ip: e.target.value })} placeholder="198.51.100.2" className={inp} data-testid="hw-in-ip" /></Field>
              <Field label="Máscara"><Input value={f.mgmt.in_mask} onChange={e => up("mgmt", { in_mask: e.target.value })} className={inp} /></Field>
              <Field label="Gateway"><Input value={f.mgmt.in_gw} onChange={e => up("mgmt", { in_gw: e.target.value })} placeholder="198.51.100.1" className={inp} /></Field>
            </div>
          )}
          <Check id="hw-out" checked={f.mgmt.out_enabled} onChange={v => up("mgmt", { out_enabled: v })} testid="hw-out">Gerência outband (meth0 da controladora)</Check>
          {f.mgmt.out_enabled && (
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="IP"><Input value={f.mgmt.out_ip} onChange={e => up("mgmt", { out_ip: e.target.value })} placeholder="192.0.2.10" className={inp} /></Field>
              <Field label="Máscara"><Input value={f.mgmt.out_mask} onChange={e => up("mgmt", { out_mask: e.target.value })} className={inp} /></Field>
            </div>
          )}
        </div>

        <div className={sec}>
          <Field label="Planos (dba-profile + ont-lineprofile com ID 10, 11…)" hint="separe por vírgula — ex.: 100M, 300M, 1G; a gem 1 mapeia a VLAN inicial">
            <Input defaultValue={speedsText} key={speedsText} data-testid="hw-speeds" className={inp}
              onBlur={e => setF({ ...f, speeds: e.target.value.split(/[,;\s]+/).map(x => x.trim().toUpperCase()).filter(Boolean) })} />
          </Field>
          <Check id="hw-srv" checked={f.srv_profile} onChange={v => setF({ ...f, srv_profile: v })}>Service profile genérico (ID 1, portas adaptive)</Check>
        </div>

        <div className={sec}>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="NTP principal"><Input value={f.ntp[0]} onChange={e => setF({ ...f, ntp: [e.target.value, f.ntp[1]] })} placeholder="192.0.2.123" className={inp} /></Field>
            <Field label="NTP secundário"><Input value={f.ntp[1]} onChange={e => setF({ ...f, ntp: [f.ntp[0], e.target.value] })} placeholder="opcional" className={inp} /></Field>
          </div>
          <Check id="hw-snmp" checked={f.snmp.enabled} onChange={v => up("snmp", { enabled: v })}>SNMP v2c (leitura)</Check>
          {f.snmp.enabled && (
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="Comunidade"><Input value={f.snmp.community} onChange={e => up("snmp", { community: e.target.value })} className={inp} autoComplete="off" /></Field>
              <Field label="Destino dos traps"><Input value={f.snmp.trap_host} onChange={e => up("snmp", { trap_host: e.target.value })} placeholder="opcional" className={inp} /></Field>
            </div>
          )}
          <Check id="hw-as" checked={f.autosave.enabled} onChange={v => up("autosave", { enabled: v })}>Autosave diário</Check>
          {f.autosave.enabled && <Field label="Horário"><Input value={f.autosave.time} onChange={e => up("autosave", { time: e.target.value })} className={`${inp} w-28`} /></Field>}
          <Check id="hw-cm" checked={f.comments} onChange={v => setF({ ...f, comments: v })}>Comentários no script</Check>
          <Check id="hw-wr" checked={f.write} onChange={v => setF({ ...f, write: v })}>Gravar no final (save)</Check>
          <Button size="sm" variant="ghost" onClick={() => setF(HW_DEFAULT)} className="h-7 text-xs text-slate-400"><RotateCcw className="w-3.5 h-3.5 mr-1" />Limpar</Button>
        </div>
      </div>
      <ScriptCard out={out} err={err} busy={busy} />
    </div>
  );
}

const HW_ONU_KEY = "bastion_oltgen_hw_onu";
const HW_SVC = (vlan = "") => ({ vlan, user_vlan: "", gemport: "1", mode: "bridge", ports: [1], pppoe_user: "", pppoe_password: "" });
const HW_ONU_DEFAULT = { pon: "0/1/0", onu_id: "0", sn: "", name: "", line_profile: "10", srv_profile: "1", services: [HW_SVC("100")], write: true, comments: true };
const HW_MODES = [["bridge", "Bridge", "ont port native-vlan: a porta eth entrega a VLAN sem tag (roteador do cliente faz o PPPoE)"],
  ["pppoe", "PPPoE na ONU", "ont ipconfig … pppoe: a própria ONU disca (perfil de ONU roteada)"],
  ["none", "Só service-port", "só cria o service-port; a porta/WAN vem do service profile ou do ACS"]];

function HwAuthorize({ devices }) {
  const [f, setF] = useState(() => {
    const v = hwLoad(HW_ONU_KEY, HW_ONU_DEFAULT);
    return { ...v, sn: "", name: "", services: (v.services || []).map(x => ({ ...x, pppoe_user: "", pppoe_password: "" })) };
  });
  const [devId, setDevId] = useState("");
  const [found, setFound] = useState(null);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    const { sn, name, ...keep } = f;
    try { localStorage.setItem(HW_ONU_KEY, JSON.stringify({ ...keep, services: f.services.map(x => ({ ...x, pppoe_user: "", pppoe_password: "" })) })); } catch { /* ok */ }
  }, [f]);
  const ready = !!(f.sn && f.name);
  const [out, err, busy] = useLiveScript("/oltgen/hw/onu", f, ready, [f]);
  const set = (patch) => setF(cur => ({ ...cur, ...patch }));
  const setSvc = (i, patch) => setF(cur => ({ ...cur, services: cur.services.map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
  const search = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setSearching(true); setFound(null);
    try {
      const { data } = await api.post("/oltgen/hw/autofind", { device_id: devId });
      setFound(data);
      if (!data.onus.length) toast.info(`${data.device}: nenhuma ONT no autofind`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setSearching(false); }
  };
  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="hw-onu-page">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]"><Field label="ONTs pedindo autorização (display ont autofind all)">
              <DevSelect devices={devices} value={devId} onChange={setDevId} testid="hw-onu-device" /></Field></div>
            <Button onClick={search} disabled={searching || !devId} variant="outline" className="border-line bg-transparent h-9" data-testid="hw-onu-search">
              {searching ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Buscar autofind
            </Button>
          </div>
          {found && found.onus.length > 0 && (
            <div className="border border-line rounded-md divide-y divide-line" data-testid="hw-onu-found">
              {found.onus.map((o, i) => (
                <div key={i} className="flex flex-wrap items-center gap-3 px-3 py-1.5 text-xs font-mono">
                  <span className="text-slate-400 w-16">{o.pon}</span>
                  <span className="text-slate-100">{o.sn_vendor}</span>
                  <span className="text-slate-500 flex-1 truncate">{o.model} {o.version}</span>
                  <span className="text-slate-500">ID livre {o.free_id ?? "—"}</span>
                  <Button size="sm" variant="ghost" onClick={() => set({ pon: o.pon, sn: o.sn_vendor, onu_id: o.free_id !== null && o.free_id !== undefined ? String(o.free_id) : f.onu_id })}
                    className="h-6 px-2 text-brand-soft hover:bg-slate-800" data-testid={`hw-onu-use-${i}`}>Usar</Button>
                </div>
              ))}
            </div>
          )}
        </Card>
        <div className={sec}>
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="PON" hint="F/S/P, ex.: 0/1/3"><Input value={f.pon} onChange={e => set({ pon: e.target.value })} className={inp} data-testid="hw-onu-pon" /></Field>
            <Field label="ONT ID" hint="0 a 127, livre na PON"><Input value={f.onu_id} onChange={e => set({ onu_id: e.target.value })} className={inp} data-testid="hw-onu-id" /></Field>
            <Field label="Serial (SN)"><Input value={f.sn} onChange={e => set({ sn: e.target.value.toUpperCase().trim() })} placeholder="HWTC-A1B2C3D4" className={inp} data-testid="hw-onu-sn" /></Field>
          </div>
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="Cliente (desc)"><Input value={f.name} onChange={e => set({ name: e.target.value })} placeholder="cliente_0001" className={inp} data-testid="hw-onu-name" /></Field>
            <Field label="Line profile (ID)" hint="plano / T-CONT / gem mapping"><Input value={f.line_profile} onChange={e => set({ line_profile: e.target.value })} className={inp} data-testid="hw-onu-lp" /></Field>
            <Field label="Service profile (ID)"><Input value={f.srv_profile} onChange={e => set({ srv_profile: e.target.value })} className={inp} data-testid="hw-onu-sp" /></Field>
          </div>
        </div>
        <div className={sec} data-testid="hw-onu-services">
          <div className="text-sm font-semibold text-slate-200">Serviços (service-port por VLAN)</div>
          {f.services.map((sv, i) => (
            <div key={i} className="rounded-md border border-line p-3 space-y-2" data-testid={`hw-svc-${i}`}>
              <div className="flex flex-wrap items-end gap-2">
                <div className="w-24"><Field label="VLAN"><Input value={sv.vlan} onChange={e => setSvc(i, { vlan: e.target.value })} className={inp} data-testid={`hw-svc-${i}-vlan`} /></Field></div>
                <div className="w-28"><Field label="VLAN na ONU"><Input value={sv.user_vlan} onChange={e => setSvc(i, { user_vlan: e.target.value })} placeholder="a mesma" className={inp} /></Field></div>
                <div className="w-20"><Field label="Gem port"><Input value={sv.gemport} onChange={e => setSvc(i, { gemport: e.target.value })} className={inp} data-testid={`hw-svc-${i}-gem`} /></Field></div>
                <div className="flex gap-1 pb-0.5">
                  {HW_MODES.map(([k, l, d]) => (
                    <button key={k} type="button" title={d} onClick={() => setSvc(i, { mode: k })} data-testid={`hw-svc-${i}-mode-${k}`}
                      className={`h-9 px-2.5 rounded-md text-xs border ${sv.mode === k ? "border-brand/70 bg-brand/15 text-brand-soft" : "border-line text-slate-400 hover:text-slate-200"}`}>{l}</button>
                  ))}
                </div>
                {f.services.length > 1 && <button type="button" onClick={() => setF(cur => ({ ...cur, services: cur.services.filter((_, j) => j !== i) }))}
                  className="ml-auto h-9 px-2 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>}
              </div>
              {sv.mode === "bridge" && (
                <div className="flex flex-wrap items-center gap-1">
                  <span className="text-[11px] text-slate-500 mr-1">portas</span>
                  {[1, 2, 3, 4].map(n => <Toggle key={n} on={sv.ports.includes(n)} label={`eth ${n}`} testid={`hw-svc-${i}-eth-${n}`}
                    onClick={() => setSvc(i, { ports: sv.ports.includes(n) ? sv.ports.filter(x => x !== n) : [...sv.ports, n].sort((a, b) => a - b) })} />)}
                </div>
              )}
              {sv.mode === "pppoe" && (
                <div className="grid sm:grid-cols-2 gap-3">
                  <Field label="Usuário PPPoE"><Input value={sv.pppoe_user} onChange={e => setSvc(i, { pppoe_user: e.target.value })} className={inp} autoComplete="off" data-testid={`hw-svc-${i}-user`} /></Field>
                  <Field label="Senha PPPoE" hint="não fica salva no navegador"><Input type="password" value={sv.pppoe_password} onChange={e => setSvc(i, { pppoe_password: e.target.value })} className={inp} autoComplete="new-password" /></Field>
                </div>
              )}
              <div className="text-[11px] text-slate-500">{HW_MODES.find(m => m[0] === sv.mode)?.[2]}</div>
            </div>
          ))}
          {f.services.length < 8 && <Button size="sm" variant="outline" onClick={() => setF(cur => ({ ...cur, services: [...cur.services, { ...HW_SVC(""), gemport: String(cur.services.length + 1), mode: "none" }] }))}
            className="border-line bg-transparent h-8 text-xs" data-testid="hw-svc-add"><Plus className="w-3.5 h-3.5 mr-1" />Adicionar VLAN</Button>}
          <Check id="hw-onu-wr" checked={f.write} onChange={v => set({ write: v })}>Gravar no final (save)</Check>
        </div>
      </div>
      {ready ? <ScriptCard out={out} err={err} busy={busy} apply={{ devices, vendor: "huawei", deviceId: devId, kind: "hw-onu", params: f, onDone: () => setFound(null) }} />
        : <Card className="bg-surface border-line flex items-center justify-center min-h-[320px] text-sm text-slate-500 p-6 text-center" data-testid="hw-onu-empty">
            Informe o serial e o cliente (ou busque o autofind) para gerar o script.</Card>}
    </div>
  );
}

const HW_NEW_RM = (pon = "") => ({ pon, onu: "" });

function HwRemove({ devices }) {
  const [rows, setRows] = useState([HW_NEW_RM("0/1/0")]);
  const [write, setWrite] = useState(true);
  const [devId, setDevId] = useState("");
  const [info, setInfo] = useState(null);
  const [checking, setChecking] = useState(false);
  const [q, setQ] = useState("");
  const [qDev, setQDev] = useState("all");
  const [qRes, setQRes] = useState(null);
  const [qBusy, setQBusy] = useState(false);
  const filled = rows.filter(r => /^\d+\/\d+\/\d+$/.test(r.pon.trim()) && r.onu !== "");
  const key = (r) => `${r.pon.trim()}:${Number(r.onu)}`;
  const [out, err, busy] = useLiveScript("/oltgen/hw/onu-remove", { items: filled, write }, filled.length > 0, [rows, write]);
  const setRow = (i, patch) => { setRows(rs => rs.map((r, j) => (j === i ? { ...r, ...patch } : r))); setInfo(null); };
  const check = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setChecking(true);
    try {
      const { data } = await api.post("/oltgen/hw/onu-info", { device_id: devId, items: filled });
      setInfo(Object.fromEntries(data.items.map(it => [`${it.pon}:${it.onu}`, it])));
      const miss = data.items.filter(it => !it.found).length;
      if (miss) toast.warning(`${miss} ONT(s) não existem nessa OLT`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setChecking(false); }
  };
  const find = async () => {
    if (!q.trim()) return toast.error("Informe o serial ou o MAC");
    setQBusy(true); setQRes(null);
    try {
      const { data } = await api.post("/oltgen/find-onu", { query: q, device_id: qDev, vendor: "huawei" });
      setQRes(data);
      if (!data.found) toast.info(`${data.kind === "sn" ? "Serial" : "MAC"} ${data.query} não encontrado`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setQBusy(false); }
  };
  const take = (r) => {
    const row = { pon: r.pon, onu: String(r.onu) };
    const other = devId && devId !== r.device_id;
    setRows(rs => (other ? [row] : [...rs.filter(x => x.onu !== "" && key(x) !== key(row)), row]));
    setDevId(r.device_id || "");
    setInfo(cur => ({ ...(other ? {} : cur || {}), [key(row)]: { ...r, found: true } }));
  };
  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="hw-rm-page">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3" data-testid="hw-find">
          <div className="text-sm font-semibold text-slate-200">Procurar ONT pelo serial ou MAC</div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-56"><Field label="Serial (ou MAC do cliente)">
              <Input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === "Enter" && find()} placeholder="HWTC-A1B2C3D4" className={inp} data-testid="hw-find-q" /></Field></div>
            <div className="flex-1 min-w-[180px]"><Field label="Em qual OLT">
              <select value={qDev} onChange={e => setQDev(e.target.value)} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-slate-100" data-testid="hw-find-dev">
                <option value="all">Todas as OLTs Huawei ({devices.length})</option>
                <TagOptions devices={devices} label={d => `${d.name} · ${d.host}`} />
              </select></Field></div>
            <Button onClick={find} disabled={qBusy || !devices.length} variant="outline" className="border-line bg-transparent h-9" data-testid="hw-find-go">
              {qBusy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Procurar
            </Button>
          </div>
          {qRes?.found && (
            <div className="rounded-md border border-line bg-sunken/60 px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-mono" data-testid="hw-find-result">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" /><span className="text-slate-100">{qRes.device}</span>
              <span className="text-brand-soft">{qRes.pon} ONT {qRes.onu}</span>
              {qRes.vlan && <span className="text-slate-400">VLAN {qRes.vlan}</span>}
              <span className="text-slate-200">{qRes.name || "sem descrição"}</span>
              <span className="text-slate-400">{qRes.sn_vendor} · {qRes.run}{qRes.cause ? ` · ${qRes.cause}` : ""}</span>
              <Button size="sm" variant="outline" onClick={() => take(qRes)} className="ml-auto h-7 border-red-800/60 bg-transparent text-red-300 hover:bg-red-950/40" data-testid="hw-find-use">
                <Unplug className="w-3.5 h-3.5 mr-1" />Desautorizar esta</Button>
            </div>
          )}
          {qRes && !qRes.found && <div className="text-xs text-amber-300" data-testid="hw-find-none">Não encontrado{qRes.searched?.length ? ` em ${qRes.searched.join(", ")}` : ""}.
            {qRes.errors?.length ? <span className="block text-red-300 mt-1">Sem acesso: {qRes.errors.join(" · ")}</span> : null}</div>}
        </Card>
        <div className={sec}>
          <div className="space-y-2" data-testid="hw-rm-rows">
            {rows.map((r, i) => {
              const it = info ? info[key(r)] : null;
              return (
                <div key={i} className="flex flex-wrap items-end gap-2">
                  <div className="w-28"><Field label={i === 0 ? "PON (F/S/P)" : ""}><Input value={r.pon} onChange={e => setRow(i, { pon: e.target.value.replace(/[^\d/]/g, "") })} className={inp} data-testid={`hw-rm-${i}-pon`} /></Field></div>
                  <div className="w-20"><Field label={i === 0 ? "ONT ID" : ""}><Input value={r.onu} inputMode="numeric" onChange={e => setRow(i, { onu: e.target.value.replace(/\D/g, "").slice(0, 3) })}
                    onKeyDown={e => { if (e.key === "Enter" && i === rows.length - 1) setRows(rs => [...rs, HW_NEW_RM(r.pon)]); }} className={inp} data-testid={`hw-rm-${i}-onu`} /></Field></div>
                  <div className="text-xs font-mono pb-2 min-w-0 flex-1 truncate" data-testid={`hw-rm-${i}-info`}>
                    {it ? (it.found ? <span className="text-slate-200"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1 text-emerald-400" />{it.name || "sem descrição"} · {it.sn_vendor} · {it.run}</span>
                      : <span className="text-amber-300"><XCircle className="w-3.5 h-3.5 inline mr-1" />não existe nessa PON</span>) : null}
                  </div>
                  {rows.length > 1 && <button type="button" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))} className="h-9 px-2 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>}
                </div>
              );
            })}
          </div>
          <Button size="sm" variant="outline" onClick={() => setRows(rs => [...rs, HW_NEW_RM(rs[rs.length - 1]?.pon || "")])} className="border-line bg-transparent h-8 text-xs" data-testid="hw-rm-add"><Plus className="w-3.5 h-3.5 mr-1" />Outra ONT</Button>
          <Check id="hw-rm-wr" checked={write} onChange={setWrite}>Gravar no final (save)</Check>
        </div>
        <Card className="bg-surface border-line p-4">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]"><Field label="Conferir na OLT quem é cada ONT antes de remover (só leitura)">
              <DevSelect devices={devices} value={devId} onChange={v => { setDevId(v); setInfo(null); }} testid="hw-rm-device" /></Field></div>
            <Button onClick={check} disabled={checking || !devId || !filled.length} variant="outline" className="border-line bg-transparent h-9" data-testid="hw-rm-check">
              {checking ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Conferir
            </Button>
          </div>
        </Card>
      </div>
      {filled.length ? <ScriptCard out={out} err={err} busy={busy} apply={{ devices, vendor: "huawei", deviceId: devId, kind: "hw-onu-remove", params: { items: filled }, onDone: () => setInfo(null) }} />
        : <Card className="bg-surface border-line flex items-center justify-center min-h-[320px] text-sm text-slate-500 p-6 text-center" data-testid="hw-rm-empty">Informe a PON (F/S/P) e o ONT ID para gerar o script.</Card>}
    </div>
  );
}

const HW_OLT_RE = /olt|ma5[68]\d\d|ea5[68]\d\d|smartax|gpon/i;
// Huawei também é roteador/switch (VRP): mostra primeiro os que parecem OLT; se nenhum parecer, todos
const huaweiOlts = (devs) => {
  const hw = devs.filter(d => d.device_type === "huawei");
  const olt = hw.filter(d => HW_OLT_RE.test([d.name, d.model, d.description, ...(d.tags || [])].filter(Boolean).join(" ")));
  return olt.length ? olt : hw;
};

// ================= OLT FiberHome (AN5516, CLI clássica) =================
const FH_KEY = "bastion_oltgen_fh_form";
const FH_DEFAULT = { uplinks: "19:1", cards_auth: true, vlans: { enabled: true, start: "100", count: "32", name: "CLIENTES" },
  mgmt: { enabled: false, vlan: "", ip: "", gw: "", port: "", route: true }, snmp: { enabled: false, community: "" },
  autosave: { enabled: true, time: "03:00" }, comments: true, write: true };
const FH_DIRS = [["onu", "cd onu (RPs novos)"], ["gpononu", "cd gpononu (RPs antigos)"]];

function FhDir({ value, onChange }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="fh-dir">
      <span className="text-xs text-slate-400 mr-1">Diretório das ONUs</span>
      {FH_DIRS.map(([k, l]) => <Toggle key={k} on={value === k} label={l} testid={`fh-dir-${k}`} onClick={() => onChange(k)} />)}
    </div>
  );
}

function FhActivation({ devices }) {
  const [f, setF] = useState(() => hwLoad(FH_KEY, FH_DEFAULT));
  const [devId, setDevId] = useState("");
  const [cards, setCards] = useState(null);
  const [reading, setReading] = useState(false);
  const [out, err, busy] = useLiveScript("/oltgen/fh/generate", { ...f, uplinks: hwUps(f.uplinks) }, true, [f]);
  useEffect(() => { try { localStorage.setItem(FH_KEY, JSON.stringify({ ...f, snmp: { ...f.snmp, community: "" } })); } catch { /* ok */ } }, [f]);
  const up = (k, patch) => setF(cur => ({ ...cur, [k]: { ...cur[k], ...patch } }));
  const read = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setReading(true);
    try { const { data } = await api.post("/oltgen/fh/read-cards", { device_id: devId }); setCards(data); toast.success(`${data.device}: ${data.pon.length} placa(s) GPON`); }
    catch (e) { toast.error(formatApiError(e)); } finally { setReading(false); }
  };
  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="fh-activation">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]"><Field label="Ver as placas de uma OLT cadastrada (showcard)">
              <DevSelect devices={devices} value={devId} onChange={setDevId} testid="fh-device" empty="Nenhuma OLT FiberHome cadastrada" /></Field></div>
            <Button onClick={read} disabled={reading || !devId} variant="outline" className="border-line bg-transparent h-9" data-testid="fh-read">
              {reading ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ScanSearch className="w-4 h-4 mr-1.5" />}Ler placas
            </Button>
          </div>
          {cards && <div className="text-xs font-mono text-slate-300 flex flex-wrap gap-2" data-testid="fh-cards">
            {cards.pon.map(c => <span key={c.slot} className="px-2 py-0.5 rounded border border-line">slot {c.slot} · {c.name} · {c.ports} PON</span>)}</div>}
          <div className="text-[11px] text-amber-300/90">AN5516-01/02/04/06 com a CLI clássica. A AN6000 usa outra CLI e ainda não é suportada.</div>
        </Card>
        <div className={sec}>
          <Field label="Porta(s) de uplink" hint="slot:porta separadas por vírgula, ex.: 19:1, 19:2">
            <Input value={f.uplinks} onChange={e => setF({ ...f, uplinks: e.target.value })} className={inp} data-testid="fh-uplinks" /></Field>
          <Check id="fh-cards-auth" checked={f.cards_auth} onChange={v => setF({ ...f, cards_auth: v })}>Autorizar as placas instaladas (set cards_all_auth)</Check>
          <Check id="fh-vl" checked={f.vlans.enabled} onChange={v => up("vlans", { enabled: v })} testid="fh-vlans">VLANs de clientes no uplink (set service)</Check>
          {f.vlans.enabled && <div className="grid sm:grid-cols-3 gap-3">
            <Field label="VLAN inicial"><Input value={f.vlans.start} onChange={e => up("vlans", { start: e.target.value })} className={inp} data-testid="fh-vlan-start" /></Field>
            <Field label="Quantidade"><Input value={f.vlans.count} onChange={e => up("vlans", { count: e.target.value })} className={inp} /></Field>
            <Field label="Nome do serviço"><Input value={f.vlans.name} onChange={e => up("vlans", { name: e.target.value })} className={inp} /></Field>
          </div>}
        </div>
        <div className={sec}>
          <Check id="fh-mg" checked={f.mgmt.enabled} onChange={v => up("mgmt", { enabled: v })} testid="fh-mgmt">VLAN e IP de gerência (set manage vlan)</Check>
          {f.mgmt.enabled && <div className="grid sm:grid-cols-4 gap-3">
            <Field label="VLAN"><Input value={f.mgmt.vlan} onChange={e => up("mgmt", { vlan: e.target.value })} placeholder="4000" className={inp} data-testid="fh-mgmt-vlan" /></Field>
            <Field label="IP/máscara"><Input value={f.mgmt.ip} onChange={e => up("mgmt", { ip: e.target.value })} placeholder="192.0.2.10/30" className={inp} data-testid="fh-mgmt-ip" /></Field>
            <Field label="Gateway"><Input value={f.mgmt.gw} onChange={e => up("mgmt", { gw: e.target.value })} placeholder="192.0.2.9" className={inp} data-testid="fh-mgmt-gw" /></Field>
            <Field label="Porta" hint="vazio = 1º uplink"><Input value={f.mgmt.port} onChange={e => up("mgmt", { port: e.target.value })} placeholder="19:1" className={inp} /></Field>
          </div>}
          <Check id="fh-snmp" checked={f.snmp.enabled} onChange={v => up("snmp", { enabled: v })}>SNMP (community de leitura)</Check>
          {f.snmp.enabled && <Field label="Community"><Input value={f.snmp.community} onChange={e => up("snmp", { community: e.target.value })} className={`${inp} w-56`} autoComplete="off" /></Field>}
          <Check id="fh-as" checked={f.autosave.enabled} onChange={v => up("autosave", { enabled: v })}>Autosave diário</Check>
          {f.autosave.enabled && <Field label="Horário"><Input value={f.autosave.time} onChange={e => up("autosave", { time: e.target.value })} className={`${inp} w-28`} /></Field>}
          <Check id="fh-cm" checked={f.comments} onChange={v => setF({ ...f, comments: v })}>Comentários no script</Check>
          <Check id="fh-wr" checked={f.write} onChange={v => setF({ ...f, write: v })}>Gravar no final (save)</Check>
        </div>
      </div>
      <ScriptCard out={out} err={err} busy={busy} />
    </div>
  );
}

const FH_ONU_KEY = "bastion_oltgen_fh_onu";
const FH_SVC = (vlan = "") => ({ vlan, mode: "bridge", ports: [1], pppoe_user: "", pppoe_password: "" });
const FH_ONU_DEFAULT = { slot: "1", pon: "1", onu_id: "1", sn: "", type: "HG6143D", name: "", dir: "onu", services: [FH_SVC("100")], write: true, comments: true };
const FH_MODES = [["bridge", "Bridge (LAN)", "a porta LAN da ONU entrega a VLAN (ONU bridge / SFU)"],
  ["pppoe", "PPPoE na ONU", "set wancfg: a ONU roteada disca o PPPoE (HGU)"],
  ["veip", "Só VEIP", "só a VLAN no veip; a WAN vem do ACS/TR-069 ou do perfil"]];

function FhAuthorize({ devices }) {
  const [f, setF] = useState(() => {
    const v = hwLoad(FH_ONU_KEY, FH_ONU_DEFAULT);
    return { ...v, sn: "", name: "", services: (v.services || []).map(x => ({ ...x, pppoe_user: "", pppoe_password: "" })) };
  });
  const [devId, setDevId] = useState("");
  const [pons, setPons] = useState("");
  const [found, setFound] = useState(null);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    const { sn, name, ...keep } = f;
    try { localStorage.setItem(FH_ONU_KEY, JSON.stringify({ ...keep, services: f.services.map(x => ({ ...x, pppoe_user: "", pppoe_password: "" })) })); } catch { /* ok */ }
  }, [f]);
  const ready = !!(f.sn && f.type);
  const [out, err, busy] = useLiveScript("/oltgen/fh/onu", f, ready, [f]);
  const set = (patch) => setF(cur => ({ ...cur, ...patch }));
  const setSvc = (i, patch) => setF(cur => ({ ...cur, services: cur.services.map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
  const search = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    const list = pons.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean);
    if (list.some(x => !/^\d{1,2}\/\d{1,2}$/.test(x))) return toast.error("PONs no formato slot/pon, ex.: 1/2");
    setSearching(true); setFound(null);
    try {
      const { data } = await api.post("/oltgen/fh/discovery", { device_id: devId, pons: list, dir: f.dir });
      setFound(data); if (data.dir) set({ dir: data.dir });
      if (!data.onus.length) toast.info(`${data.device}: nenhuma ONU esperando autorização em ${data.pons} PON(s)`);
    } catch (e) { toast.error(formatApiError(e)); } finally { setSearching(false); }
  };
  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="fh-onu-page">
      <div className="space-y-4 min-w-0">
        <Card className="bg-surface border-line p-4 space-y-3">
          <div className="grid gap-2 md:grid-cols-[1fr_9rem_auto] items-end">
            <Field label="ONUs pedindo autorização (show discovery)"><DevSelect devices={devices} value={devId} onChange={setDevId} testid="fh-onu-device" empty="Nenhuma OLT FiberHome cadastrada" /></Field>
            <Field label="Só estas PONs"><Input value={pons} onChange={e => setPons(e.target.value)} placeholder="1/2, 1/3" className={inp} data-testid="fh-onu-pons" /></Field>
            <Button onClick={search} disabled={searching || !devId} variant="outline" className="border-line bg-transparent h-9" data-testid="fh-onu-search">
              {searching ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Buscar
            </Button>
          </div>
          <div className="text-[11px] text-slate-500">Sem PON informada, lê todas as PONs das placas GPON (pode levar alguns minutos por Telnet).</div>
          {found && found.onus.length > 0 && (
            <div className="border border-line rounded-md divide-y divide-line" data-testid="fh-onu-found">
              {found.onus.map((o, i) => (
                <div key={i} className="flex flex-wrap items-center gap-3 px-3 py-1.5 text-xs font-mono">
                  <span className="text-slate-400 w-14">{o.slot}/{o.pon}</span><span className="text-slate-100">{o.sn}</span>
                  <span className="text-slate-500 flex-1 truncate">{o.type}</span><span className="text-slate-500">ID livre {o.free_id ?? "—"}</span>
                  <Button size="sm" variant="ghost" onClick={() => set({ slot: String(o.slot), pon: String(o.pon), sn: o.sn, type: o.type || f.type, onu_id: o.free_id ? String(o.free_id) : f.onu_id })}
                    className="h-6 px-2 text-brand-soft hover:bg-slate-800" data-testid={`fh-onu-use-${i}`}>Usar</Button>
                </div>
              ))}
            </div>
          )}
          {found && !found.onus.length && found.raw && <details className="text-xs text-amber-300"><summary className="cursor-pointer">Ver a resposta da OLT</summary>
            <pre className="text-[11px] text-slate-300 bg-sunken p-2 rounded mt-1 max-h-48 overflow-auto whitespace-pre-wrap">{found.raw}</pre></details>}
        </Card>
        <div className={sec}>
          <FhDir value={f.dir} onChange={v => set({ dir: v })} />
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
            <Field label="Slot"><Input value={f.slot} onChange={e => set({ slot: e.target.value })} className={inp} data-testid="fh-onu-slot" /></Field>
            <Field label="PON"><Input value={f.pon} onChange={e => set({ pon: e.target.value })} className={inp} data-testid="fh-onu-pon" /></Field>
            <Field label="ONU"><Input value={f.onu_id} onChange={e => set({ onu_id: e.target.value })} className={inp} data-testid="fh-onu-id" /></Field>
            <div className="col-span-3"><Field label="Serial"><Input value={f.sn} onChange={e => set({ sn: e.target.value.toUpperCase().trim() })} placeholder="FHTT1A2B3C4D" className={inp} data-testid="fh-onu-sn" /></Field></div>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Tipo da ONU (type)" hint="o nome que a OLT conhece, ex.: HG6143D, AN5506-04-F1"><Input value={f.type} onChange={e => set({ type: e.target.value.trim() })} className={inp} data-testid="fh-onu-type" /></Field>
            <Field label="Cliente" hint="só no comentário do script"><Input value={f.name} onChange={e => set({ name: e.target.value })} placeholder="cliente_0001" className={inp} /></Field>
          </div>
        </div>
        <div className={sec} data-testid="fh-onu-services">
          <div className="text-sm font-semibold text-slate-200">Serviços</div>
          {f.services.map((sv, i) => (
            <div key={i} className="rounded-md border border-line p-3 space-y-2" data-testid={`fh-svc-${i}`}>
              <div className="flex flex-wrap items-end gap-2">
                <div className="w-24"><Field label="VLAN"><Input value={sv.vlan} onChange={e => setSvc(i, { vlan: e.target.value })} className={inp} data-testid={`fh-svc-${i}-vlan`} /></Field></div>
                <div className="flex gap-1 pb-0.5">
                  {FH_MODES.map(([k, l, d]) => <button key={k} type="button" title={d} onClick={() => setSvc(i, { mode: k })} data-testid={`fh-svc-${i}-mode-${k}`}
                    className={`h-9 px-2.5 rounded-md text-xs border ${sv.mode === k ? "border-brand/70 bg-brand/15 text-brand-soft" : "border-line text-slate-400 hover:text-slate-200"}`}>{l}</button>)}
                </div>
                {f.services.length > 1 && <button type="button" onClick={() => setF(cur => ({ ...cur, services: cur.services.filter((_, j) => j !== i) }))} className="ml-auto h-9 px-2 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>}
              </div>
              {sv.mode === "bridge" && <div className="flex flex-wrap items-center gap-1"><span className="text-[11px] text-slate-500 mr-1">portas</span>
                {[1, 2, 3, 4].map(n => <Toggle key={n} on={sv.ports.includes(n)} label={`lan ${n}`} testid={`fh-svc-${i}-lan-${n}`}
                  onClick={() => setSvc(i, { ports: sv.ports.includes(n) ? sv.ports.filter(x => x !== n) : [...sv.ports, n].sort((a, b) => a - b) })} />)}</div>}
              {sv.mode === "pppoe" && <div className="grid sm:grid-cols-2 gap-3">
                <Field label="Usuário PPPoE"><Input value={sv.pppoe_user} onChange={e => setSvc(i, { pppoe_user: e.target.value })} className={inp} autoComplete="off" data-testid={`fh-svc-${i}-user`} /></Field>
                <Field label="Senha PPPoE" hint="não fica salva no navegador"><Input type="password" value={sv.pppoe_password} onChange={e => setSvc(i, { pppoe_password: e.target.value })} className={inp} autoComplete="new-password" /></Field>
              </div>}
              <div className="text-[11px] text-slate-500">{FH_MODES.find(m => m[0] === sv.mode)?.[2]}</div>
            </div>
          ))}
          {f.services.length < 8 && <Button size="sm" variant="outline" onClick={() => setF(cur => ({ ...cur, services: [...cur.services, FH_SVC("")] }))} className="border-line bg-transparent h-8 text-xs" data-testid="fh-svc-add"><Plus className="w-3.5 h-3.5 mr-1" />Adicionar VLAN</Button>}
          <Check id="fh-onu-wr" checked={f.write} onChange={v => set({ write: v })}>Gravar no final (save)</Check>
        </div>
      </div>
      {ready ? <ScriptCard out={out} err={err} busy={busy} apply={{ devices, vendor: "fiberhome", deviceId: devId, kind: "fh-onu", params: f, onDone: () => setFound(null) }} />
        : <Card className="bg-surface border-line flex items-center justify-center min-h-[320px] text-sm text-slate-500 p-6 text-center" data-testid="fh-onu-empty">Informe o serial e o tipo da ONU (ou busque as não autorizadas) para gerar o script.</Card>}
    </div>
  );
}

function FhRemove({ devices }) {
  const [rows, setRows] = useState([{ slot: "1", pon: "1", onu: "", sn: "", type: "" }]);
  const [dir, setDir] = useState("onu");
  const [write, setWrite] = useState(true);
  const [devId, setDevId] = useState("");
  const [checking, setChecking] = useState(false);
  const [raw, setRaw] = useState("");
  const filled = rows.filter(r => r.slot && r.pon && r.onu);
  const withSn = filled.filter(r => r.sn);
  const [out, err, busy] = useLiveScript("/oltgen/fh/onu-remove", { items: withSn, write, dir }, withSn.length > 0, [rows, write, dir]);
  const setRow = (i, patch) => setRows(rs => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const num = (v) => v.replace(/\D/g, "").slice(0, 3);
  const check = async () => {
    if (!devId) return toast.error("Escolha a OLT cadastrada");
    setChecking(true); setRaw("");
    try {
      const { data } = await api.post("/oltgen/fh/onu-info", { device_id: devId, items: filled, dir });
      if (data.dir) setDir(data.dir);
      const by = Object.fromEntries(data.items.map(it => [`${it.slot}/${it.pon}/${it.onu}`, it]));
      setRows(rs => rs.map(r => { const it = by[`${Number(r.slot)}/${Number(r.pon)}/${Number(r.onu)}`]; return it ? { ...r, sn: it.found ? it.sn : "", type: it.found ? it.type : "", state: it.found ? it.state : "missing" } : r; }));
      const miss = data.items.filter(it => !it.found).length;
      if (miss) { toast.warning(`${miss} ONU(s) não aparecem no show authorization`); setRaw(data.raw || ""); }
    } catch (e) { toast.error(formatApiError(e)); } finally { setChecking(false); }
  };
  return (
    <div className="px-4 md:px-6 pb-6 pt-2 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" data-testid="fh-rm-page">
      <div className="space-y-4 min-w-0">
        <div className={sec}>
          <FhDir value={dir} onChange={setDir} />
          <div className="text-[11px] text-slate-400">A FiberHome remove a ONU pelo serial: informe slot, PON e ONU e clique em <b>Conferir</b> para ler o serial e o tipo da OLT.</div>
          <div className="space-y-2" data-testid="fh-rm-rows">
            {rows.map((r, i) => (
              <div key={i} className="flex flex-wrap items-end gap-2">
                {[["slot", "Slot"], ["pon", "PON"], ["onu", "ONU"]].map(([k, l]) => (
                  <div key={k} className="w-16"><Field label={i === 0 ? l : ""}><Input value={r[k]} inputMode="numeric" onChange={e => setRow(i, { [k]: num(e.target.value), sn: "", type: "", state: "" })}
                    onKeyDown={e => { if (e.key === "Enter" && k === "onu" && i === rows.length - 1) setRows(rs => [...rs, { slot: r.slot, pon: r.pon, onu: "", sn: "", type: "" }]); }}
                    className={inp} data-testid={`fh-rm-${i}-${k}`} /></Field></div>
                ))}
                <div className="w-40"><Field label={i === 0 ? "Serial" : ""}><Input value={r.sn} onChange={e => setRow(i, { sn: e.target.value.toUpperCase().trim() })} placeholder="FHTT…" className={inp} data-testid={`fh-rm-${i}-sn`} /></Field></div>
                <div className="text-xs font-mono pb-2 flex-1 truncate" data-testid={`fh-rm-${i}-info`}>
                  {r.state === "missing" ? <span className="text-amber-300"><XCircle className="w-3.5 h-3.5 inline mr-1" />não está autorizada</span>
                    : r.sn && r.type ? <span className="text-slate-300"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1 text-emerald-400" />{r.type}{r.state ? ` · ${r.state}` : ""}</span> : null}
                </div>
                {rows.length > 1 && <button type="button" onClick={() => setRows(rs => rs.filter((_, j) => j !== i))} className="h-9 px-2 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>}
              </div>
            ))}
          </div>
          <Button size="sm" variant="outline" onClick={() => setRows(rs => [...rs, { slot: rs[rs.length - 1]?.slot || "", pon: rs[rs.length - 1]?.pon || "", onu: "", sn: "", type: "" }])} className="border-line bg-transparent h-8 text-xs" data-testid="fh-rm-add"><Plus className="w-3.5 h-3.5 mr-1" />Outra ONU</Button>
          <Check id="fh-rm-wr" checked={write} onChange={setWrite}>Gravar no final (save)</Check>
        </div>
        <Card className="bg-surface border-line p-4 space-y-2">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-[200px]"><Field label="Conferir na OLT (show authorization — só leitura)"><DevSelect devices={devices} value={devId} onChange={setDevId} testid="fh-rm-device" empty="Nenhuma OLT FiberHome cadastrada" /></Field></div>
            <Button onClick={check} disabled={checking || !devId || !filled.length} variant="outline" className="border-line bg-transparent h-9" data-testid="fh-rm-check">
              {checking ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Search className="w-4 h-4 mr-1.5" />}Conferir
            </Button>
          </div>
          {raw && <details className="text-xs text-amber-300"><summary className="cursor-pointer">Ver a resposta da OLT</summary>
            <pre className="text-[11px] text-slate-300 bg-sunken p-2 rounded mt-1 max-h-48 overflow-auto whitespace-pre-wrap">{raw}</pre></details>}
        </Card>
      </div>
      {withSn.length ? <ScriptCard out={out} err={err} busy={busy} apply={{ devices, vendor: "fiberhome", deviceId: devId, kind: "fh-onu-remove", params: { items: withSn, dir } }} />
        : <Card className="bg-surface border-line flex items-center justify-center min-h-[320px] text-sm text-slate-500 p-6 text-center" data-testid="fh-rm-empty">Informe slot, PON e ONU e confira na OLT (ou digite o serial) para gerar o script.</Card>}
    </div>
  );
}

function FhNoClean() {
  return (
    <div className="p-4 md:p-6 max-w-3xl" data-testid="fh-noclean">
      <Card className="bg-surface border-line p-5 text-sm text-slate-300 space-y-2">
        <div className="flex items-center gap-2 text-slate-100 font-medium"><AlertTriangle className="w-4 h-4 text-amber-300" />Limpeza de ONUs off ainda não disponível para FiberHome</div>
        <p>Para remover em massa com segurança o BastiON precisa saber há quantos dias cada ONU caiu, e isso muda entre firmwares da AN5516. Quando tiver uma OLT FiberHome cadastrada, me mande a saída do comando de última queda da sua versão que eu completo.</p>
        <p className="text-slate-400">Enquanto isso: o estado das ONUs (online/offline) e as quedas em massa já aparecem no <b>GPON</b>, e dá para desautorizar na aba ao lado.</p>
      </Card>
    </div>
  );
}

export default function OltScript() {
  const [view, setView] = useState(() => { try { return localStorage.getItem("bastion_oltgen_view") || "olt"; } catch { return "olt"; } });
  const [vendor, setVendor] = useState(() => { try { return localStorage.getItem("bastion_oltgen_vendor") || "zte"; } catch { return "zte"; } });
  const [meta, setMeta] = useState(null);
  const [hwModels, setHwModels] = useState([]);
  const [all, setAll] = useState([]);
  useEffect(() => {
    api.get("/oltgen/models").then(r => setMeta(r.data)).catch(() => {});
    api.get("/oltgen/hw/models").then(r => setHwModels(r.data.models || [])).catch(() => {});
    api.get("/devices").then(r => setAll(r.data || [])).catch(() => {});
  }, []);
  const devices = useMemo(() => (vendor === "huawei" ? huaweiOlts(all) : all.filter(d => d.device_type === (vendor === "fiberhome" ? "fiberhome" : "zte"))), [all, vendor]);
  const go = (v) => { setView(v); try { localStorage.setItem("bastion_oltgen_view", v); } catch { /* ok */ } };
  const pickVendor = (v) => { setVendor(v); try { localStorage.setItem("bastion_oltgen_vendor", v); } catch { /* ok */ } };
  const hw = vendor === "huawei";
  const fh = vendor === "fiberhome";
  return (
    <div className="flex-1 overflow-y-auto" data-testid="oltgen-page">
      <div className="px-4 md:px-6 pt-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="hidden md:block text-xs text-slate-400">{hw ? "Huawei · MA5800 / MA5608T / MA5683T · GPON" : fh ? "FiberHome · AN5516 (CLI clássica) · GPON" : "ZTE · TITAN e C300/C320 · GPON"}</div>
          <div className="flex items-center gap-3 mt-1">
            <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100">Script de OLT</h1>
            <div className="flex gap-1 p-0.5 bg-sunken border border-line rounded-md text-xs" data-testid="oltgen-vendor">
              {[["zte", "ZTE"], ["huawei", "Huawei"], ["fiberhome", "FiberHome"]].map(([k, l]) => (
                <button key={k} onClick={() => pickVendor(k)} data-testid={`oltgen-vendor-${k}`}
                  className={`px-2.5 h-7 rounded ${vendor === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm overflow-x-auto max-w-full">
          {[["olt", "Ativação da OLT", Cpu], ["onu", "Autorizar ONU", Router], ["rm", "Desautorizar ONU", Unplug], ["off", "ONUs off", PowerOff]].map(([k, l, I]) => (
            <button key={k} onClick={() => go(k)} data-testid={`oltgen-view-${k}`}
              className={`shrink-0 flex items-center gap-1.5 px-3 h-8 rounded ${view === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>
              <I className="w-4 h-4" />{l}
            </button>
          ))}
        </div>
      </div>
      {fh ? (view === "olt" ? <FhActivation devices={devices} /> : view === "rm" ? <FhRemove devices={devices} /> : view === "off" ? <FhNoClean /> : <FhAuthorize devices={devices} />)
        : hw ? (view === "olt" ? <HwActivation devices={devices} models={hwModels} /> : view === "rm" ? <HwRemove devices={devices} />
        : view === "off" ? <OnuClean key="hw" devices={devices} vendor="huawei" /> : <HwAuthorize devices={devices} />)
        : (view === "olt" ? <OltActivation /> : view === "rm" ? <OnuRemove meta={meta} devices={devices} /> : view === "off" ? <OnuClean key="zte" devices={devices} /> : <OnuAuthorize meta={meta} devices={devices} />)}
    </div>
  );
}
