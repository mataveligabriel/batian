import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Cpu, Copy, Download, Loader2, Plus, Trash2, ScanSearch, AlertTriangle, RotateCcw } from "lucide-react";

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
const upName = (u, i) => `${u.prefix || "xgei"}-1/${u.slot}/${i + 1}`;

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

function BoardsEditor({ title, boards, onChange, kind, testid }) {
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
                <Toggle key={k} on={on} label={kind === "pon" ? k + 1 : upName(b, k)} testid={`${testid}-p-${i}-${k}`}
                  onClick={() => set(i, { enabled: b.enabled.map((x, z) => (z === k ? !x : x)) })} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function UplinkPick({ uplinks, value, onChange, testid }) {
  const names = uplinks.flatMap(u => Array.from({ length: u.ports }, (_, i) => upName(u, i)));
  if (!names.length) return <div className="text-xs text-slate-500">Cadastre as placas de uplink na primeira aba.</div>;
  return (
    <div className="flex flex-wrap gap-1" data-testid={testid}>
      {names.map(n => <Toggle key={n} on={value.includes(n)} label={n} testid={`${testid}-${n}`}
        onClick={() => onChange(value.includes(n) ? value.filter(x => x !== n) : [...value, n])} />)}
    </div>
  );
}

export default function OltScript() {
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

  const copy = async () => {
    try { await navigator.clipboard.writeText(out?.script || ""); toast.success("Script copiado"); } catch { toast.error("Não consegui copiar"); }
  };
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([out?.script || ""], { type: "text/plain;charset=utf-8" }));
    a.download = out?.filename || "olt.txt"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  return (
    <div className="flex-1 overflow-y-auto" data-testid="oltgen-page">
      <div className="px-4 md:px-6 pt-4 pb-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="hidden md:block text-xs text-slate-400">ZTE · linha TITAN · GPON</div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Script de OLT</h1>
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
                    {devices.map(d => <option key={d.id} value={d.id}>{d.name} · {d.host}</option>)}
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
              <BoardsEditor title="Placas PON (gpon_olt)" kind="pon" boards={f.pon} onChange={pon => setF({ ...f, pon })} testid="oltgen-pon" />
              <BoardsEditor title="Placas com uplink" kind="uplink" boards={f.uplinks} onChange={uplinks => setF({ ...f, uplinks })} testid="oltgen-up" />
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
                      <UplinkPick uplinks={f.uplinks} value={f.mgmt.in_uplinks} onChange={v => up("mgmt", { in_uplinks: v })} testid="oltgen-in-ups" />
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
                      <UplinkPick uplinks={f.uplinks} value={f.vlans.uplinks} onChange={v => up("vlans", { uplinks: v })} testid="oltgen-vlan-ups" />
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
          {err && <div className="mx-4 mt-3 rounded-md border border-red-800/60 bg-red-950/30 px-3 py-2 text-sm text-red-200" data-testid="oltgen-error">{err}</div>}
          {!err && out?.warnings?.length > 0 && (
            <div className="mx-4 mt-3 space-y-1" data-testid="oltgen-warnings">
              {out.warnings.map((w, i) => <div key={i} className="flex gap-2 text-xs text-amber-200"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}</div>)}
            </div>
          )}
          <pre className={`flex-1 overflow-auto m-0 px-4 py-3 text-[12.5px] leading-relaxed font-mono text-slate-200 whitespace-pre ${err ? "opacity-40" : ""}`} data-testid="oltgen-script">
            {(out?.script || "").split("\n").map((l, i) => <div key={i} className={l.startsWith("!") ? "text-slate-500" : ""}>{l || " "}</div>)}
          </pre>
          <div className="px-4 py-2 border-t border-line text-[11px] text-slate-500">Revise os comandos e a versão do firmware antes de aplicar na OLT.</div>
        </Card>
      </div>
    </div>
  );
}
