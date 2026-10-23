import React, { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import {
  Palette, Database, BellRing, KeyRound, Download, Upload, Trash2, Loader2, RotateCcw, ShieldAlert, CheckCircle2,
  XCircle, HardDrive, Clock, CloudUpload, ArchiveRestore, Info, Globe, Plus, RefreshCw, Copy, CircleDashed,
} from "lucide-react";
import { AppearanceFields, useAppearanceEditor } from "@/components/AppearanceDialog";
import { DEFAULT_THEME } from "@/lib/theme";
import Automation from "@/pages/Automation";
import SshKey from "@/pages/SshKey";

const TABS = [
  { id: "aparencia", label: "Aparência", icon: Palette },
  { id: "dominio", label: "Domínio", icon: Globe },
  { id: "banco", label: "Banco de dados", icon: Database },
  { id: "automacao", label: "Automação e alertas", icon: BellRing },
  { id: "chave", label: "Chave SSH global", icon: KeyRound },
];

export const fmtBytes = (n) => {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0).replace(".", ",")} ${u[i]}`;
};
const fmtDate = (s) => (s ? new Date(s).toLocaleString("pt-BR") : "—");
const KIND = { manual: ["manual", "text-slate-300 border-line"], auto: ["automático", "text-brand-soft border-brand/40"],
  "pre-import": ["antes da importação", "text-amber-300 border-amber-500/40"] };
const GROUP_LABEL = {
  core: "Cadastros e configurações", config_backups: "Backups de configuração dos equipamentos",
  history: "Histórico (sessões, alertas, logs)", metrics: "Métricas e flow (gráficos de tráfego, sinal óptico)",
};

function Section({ icon: Icon, title, children, desc, testid, className = "" }) {
  return (
    <Card className={`bg-surface border-line p-4 md:p-5 ${className}`} data-testid={testid}>
      <div className="flex items-center gap-2 text-sm font-medium text-slate-100"><Icon className="w-4 h-4 text-brand-soft" /> {title}</div>
      {desc && <p className="text-xs text-slate-400 mt-1">{desc}</p>}
      <div className="mt-4">{children}</div>
    </Card>
  );
}

function AppearanceTab() {
  const ed = useAppearanceEditor();
  const ref = useRef(ed);
  ref.current = ed;
  useEffect(() => { ref.current.begin(); return () => { if (ref.current.dirty) ref.current.revert(); }; }, []);
  return (
    <Section icon={Palette} title="Aparência" desc="Vale para o seu usuário. Cada usuário escolhe a dele no botão Aparência do menu lateral." testid="settings-appearance">
      <div className="max-w-2xl"><AppearanceFields t={ed.t} set={ed.set} /></div>
      <div className="flex items-center gap-2 mt-5">
        <Button onClick={ed.save} disabled={ed.busy || !ed.dirty} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="theme-save">Salvar</Button>
        <Button variant="ghost" onClick={ed.revert} disabled={!ed.dirty} className="text-slate-300">Desfazer</Button>
        <Button variant="ghost" onClick={() => ed.set(DEFAULT_THEME)} className="text-slate-400 ml-auto" data-testid="theme-reset"><RotateCcw className="w-3.5 h-3.5 mr-1.5" /> Padrão</Button>
      </div>
    </Section>
  );
}

/** Acompanha a operação do banco em andamento (backup/importação) até terminar. */
function useDbJob(onDone) {
  const [job, setJob] = useState({ state: "idle" });
  const timer = useRef(null);
  const cb = useRef(onDone);
  cb.current = onDone;
  const poll = useCallback(() => {
    clearTimeout(timer.current);
    api.get("/system/db-job").then(({ data }) => {
      setJob(data);
      if (data.state === "running") timer.current = setTimeout(poll, 1000);
      else cb.current?.(data);
    }).catch(() => { timer.current = setTimeout(poll, 2000); });   // reiniciando: tenta de novo
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return [job, setJob, poll];
}

function Pw({ value, onChange, placeholder, testid }) {
  return <Input type="password" autoComplete="new-password" value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
                data-testid={testid} className="bg-sunken border-line font-mono h-9" />;
}

function Check({ checked, onChange, label, hint, testid }) {
  return (
    <label className="flex items-start gap-2.5 py-1 cursor-pointer text-sm" data-testid={testid}>
      <input type="checkbox" className="accent-brand mt-0.5 w-4 h-4" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span><span className="text-slate-200">{label}</span>{hint && <span className="block text-[11px] text-slate-500">{hint}</span>}</span>
    </label>
  );
}

function RestorePreview({ pv, password, onCancel, onStarted }) {
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      await api.post("/system/db-restore", { password, upload_id: pv.upload_id || null, name: pv.name || null });
      onStarted();
    } catch (e) { toast.error(formatApiError(e)); setBusy(false); }
  };
  return (
    <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm" data-testid="restore-preview">
      <div className="text-slate-100 font-medium">{pv.file || pv.name}</div>
      <div className="text-xs text-slate-400 mt-0.5">Gerado em {fmtDate(pv.created_at)} no servidor <span className="font-mono">{pv.from_host || "?"}</span>
        {pv.same_server ? " (este mesmo servidor)" : " (outro servidor: as senhas guardadas são convertidas para este)"}</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
        {[["Equipamentos", pv.devices], ["Agentes", pv.agents], ["Usuários", pv.users], ["Mapas", pv.maps]].map(([l, n]) => (
          <div key={l} className="rounded border border-line bg-surface px-2 py-1.5"><div className="text-[11px] text-slate-500">{l}</div><div className="text-slate-100 font-mono">{n}</div></div>
        ))}
      </div>
      <div className="flex gap-2 mt-3 text-xs text-amber-200"><ShieldAlert className="w-4 h-4 shrink-0" />
        <span>O que está no backup substitui o que existe aqui (equipamentos, agentes, usuários, configurações…). Os usuários e senhas passam a ser os do backup: depois, entre com o login do sistema antigo. Antes de importar, o BastiON guarda uma cópia do banco atual com a mesma senha, e reinicia sozinho no fim.</span></div>
      <Check checked={ok} onChange={setOk} label="Entendi, pode substituir" testid="restore-ack" />
      <div className="flex gap-2 mt-2">
        <Button onClick={go} disabled={!ok || busy} className="bg-amber-600 hover:bg-amber-700 text-onbrand" data-testid="restore-confirm">
          {busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ArchiveRestore className="w-4 h-4 mr-1.5" />} Importar agora
        </Button>
        <Button variant="ghost" onClick={onCancel} className="text-slate-300">Cancelar</Button>
      </div>
    </div>
  );
}

function DatabaseTab() {
  const [info, setInfo] = useState(null);
  const load = useCallback(() => api.get("/system/db").then(r => setInfo(r.data)).catch(e => toast.error(formatApiError(e))), []);
  const [job, setJob, poll] = useDbJob((j) => {
    if (j.state === "done" && j.result?.restarting) return;      // tratado em restarting
    if (j.state === "done") toast.success(j.what === "uma importação" ? "Banco importado" : `Backup pronto: ${j.result?.name}`);
    if (j.state === "error") toast.error(j.error);
    load();
  });
  useEffect(() => { load(); poll(); }, [load, poll]);

  // backup manual
  const [pw, setPw] = useState(""), [pw2, setPw2] = useState("");
  const [opt, setOpt] = useState({ config_backups: true, history: false, metrics: false });
  const startBackup = async () => {
    if (pw.length < 8) return toast.error("A senha precisa ter pelo menos 8 caracteres");
    if (pw !== pw2) return toast.error("As senhas não conferem");
    try { setJob((await api.post("/system/db-backups", { password: pw, ...opt })).data); setPw(""); setPw2(""); poll(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const download = async (name) => {
    try { const { data } = await api.post(`/system/db-backups/${encodeURIComponent(name)}/link`); window.location.href = data.url; }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (name) => {
    if (!window.confirm(`Apagar ${name} do servidor?`)) return;
    try { await api.delete(`/system/db-backups/${encodeURIComponent(name)}`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };

  // importação
  const fileRef = useRef(null);
  const [file, setFile] = useState(null);
  const [rpw, setRpw] = useState("");
  const [pv, setPv] = useState(null);
  const [upPct, setUpPct] = useState(null);
  const sendFile = async () => {
    if (!file) return toast.error("Escolha o arquivo .bastionbk");
    if (!rpw) return toast.error("Informe a senha do backup");
    const fd = new FormData(); fd.append("file", file); fd.append("password", rpw);
    setUpPct(0);
    try {
      const { data } = await api.post("/system/db-restore/upload", fd, { onUploadProgress: (e) => e.total && setUpPct(Math.round(e.loaded * 100 / e.total)) });
      setPv(data);
    } catch (e) { toast.error(formatApiError(e)); }
    setUpPct(null);
  };
  const restoreFromServer = async (name) => {
    const p = window.prompt(`Senha do backup ${name}:`);
    if (!p) return;
    try { setRpw(p); setPv((await api.post("/system/db-restore/preview", { name, password: p })).data); window.scrollTo?.(0, 0); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const [restarting, setRestarting] = useState(false);
  useEffect(() => {
    if (!(job.state === "done" && job.result?.restarting) && !restarting) return;
    setRestarting(true);
    const t = setInterval(() => {
      api.get("/auth/me").then(() => { clearInterval(t); toast.success("Banco importado — BastiON reiniciado"); setTimeout(() => window.location.reload(), 600); }).catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [job, restarting]);

  // automático
  const [auto, setAuto] = useState(null), [apw, setApw] = useState("");
  useEffect(() => { if (info && !auto) setAuto(info.settings); }, [info, auto]);
  const saveAuto = async () => {
    try { const { data } = await api.put("/system/db-settings", { ...auto, password: apw || null }); setAuto(data); setApw(""); toast.success("Backup automático salvo"); load(); }
    catch (e) { toast.error(formatApiError(e)); }
  };

  if (!info) return <div className="text-sm text-slate-500 font-mono" data-testid="settings-db-loading">Carregando…</div>;
  const byGroup = {};
  for (const c of info.collections) { if (!byGroup[c.group]) byGroup[c.group] = { docs: 0, size: 0 }; byGroup[c.group].docs += c.docs; byGroup[c.group].size += c.size; }
  const running = job.state === "running";
  const cloudReady = info.cloud?.rclone && info.cloud?.settings?.remote;

  return (
    <div className="space-y-4" data-testid="settings-db">
      {(running || restarting) && (
        <Card className="bg-surface border-brand/50 p-4 flex items-center gap-3" data-testid="db-job">
          <Loader2 className="w-5 h-5 text-brand-soft animate-spin" />
          <div className="text-sm">
            <div className="text-slate-100">{restarting ? "Banco importado. Reiniciando o BastiON…" : job.what === "uma importação" ? "Importando o banco…" : "Gerando o backup…"}</div>
            {!restarting && <div className="text-xs text-slate-400 font-mono">{job.step || "preparando"} · {job.docs || 0} documentos</div>}
            {restarting && <div className="text-xs text-slate-400">A página recarrega sozinha. Se pedir login, entre com o usuário e a senha do sistema de onde veio o backup.</div>}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Section icon={HardDrive} title="Fazer backup agora" testid="db-backup-card"
                 desc="Gera um arquivo .bastionbk criptografado com a senha abaixo. Guarde a senha: sem ela o arquivo não abre, nem para o suporte.">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Pw value={pw} onChange={setPw} placeholder="senha do backup (mín. 8)" testid="db-pw" />
            <Pw value={pw2} onChange={setPw2} placeholder="repita a senha" testid="db-pw2" />
          </div>
          <div className="mt-3 space-y-0.5">
            <Check checked disabled onChange={() => {}} label={`${GROUP_LABEL.core} — sempre`} hint={`${fmtBytes(byGroup.core?.size)} · equipamentos, agentes, usuários, mapas, dashboards, roteiros, VPNs, chave SSH e todas as configurações`} />
            <Check checked={opt.config_backups} onChange={v => setOpt({ ...opt, config_backups: v })} label={GROUP_LABEL.config_backups} hint={fmtBytes(byGroup.config_backups?.size)} testid="db-opt-config" />
            <Check checked={opt.history} onChange={v => setOpt({ ...opt, history: v })} label={GROUP_LABEL.history} hint={fmtBytes(byGroup.history?.size)} testid="db-opt-history" />
            <Check checked={opt.metrics} onChange={v => setOpt({ ...opt, metrics: v })} label={GROUP_LABEL.metrics} hint={`${fmtBytes(byGroup.metrics?.size)} — pode ficar grande`} testid="db-opt-metrics" />
          </div>
          <Button onClick={startBackup} disabled={running} className="mt-3 bg-brand hover:bg-brand-strong text-onbrand" data-testid="db-backup-go">
            {running ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Database className="w-4 h-4 mr-1.5" />} Gerar backup
          </Button>
          <p className="text-[11px] text-slate-500 mt-2">Tamanhos sem compressão; o arquivo sai bem menor. O banco ocupa {fmtBytes(info.storage_size)} em disco.</p>
        </Section>

        <Section icon={Upload} title="Importar backup" testid="db-import-card"
                 desc="Para levar o BastiON para outro servidor: instale o sistema novo, entre com o administrador do .env e importe aqui o arquivo gerado no servidor antigo.">
          <input ref={fileRef} type="file" accept=".bastionbk" className="hidden" data-testid="db-import-file"
                 onChange={e => { setFile(e.target.files?.[0] || null); setPv(null); }} />
          <div className="flex flex-wrap gap-2 items-center">
            <Button variant="outline" onClick={() => fileRef.current?.click()} className="border-line bg-surface text-slate-200 h-9">
              <Upload className="w-4 h-4 mr-1.5" /> Escolher arquivo
            </Button>
            <span className="text-xs text-slate-400 font-mono truncate max-w-[16rem]" data-testid="db-import-name">{file ? `${file.name} · ${fmtBytes(file.size)}` : "nenhum arquivo"}</span>
          </div>
          <div className="flex gap-2 mt-2">
            <Pw value={rpw} onChange={setRpw} placeholder="senha do backup" testid="db-import-pw" />
            <Button onClick={sendFile} disabled={running || upPct !== null || !file} className="bg-brand hover:bg-brand-strong text-onbrand shrink-0" data-testid="db-import-send">
              {upPct !== null ? <><Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> {upPct}%</> : "Conferir"}
            </Button>
          </div>
          <p className="text-[11px] text-slate-500 mt-2">Conferir só lê o arquivo e mostra o que tem nele; nada muda até você confirmar.</p>
          {pv && <RestorePreview pv={pv} password={rpw} onCancel={() => setPv(null)} onStarted={() => { setPv(null); setFile(null); poll(); }} />}
        </Section>
      </div>

      <Section icon={Clock} title="Backup automático" testid="db-auto-card"
               desc="Todo dia, na hora escolhida, o BastiON gera um backup e guarda os últimos no servidor (e, se quiser, manda para a nuvem).">
        {auto && (
          <div className="space-y-3">
            <label className="flex items-center gap-3 text-sm text-slate-200"><Switch checked={auto.auto} onCheckedChange={v => setAuto({ ...auto, auto: v })} data-testid="db-auto-on" /> Ativo</label>
            <div className="flex flex-wrap gap-3 items-end">
              <label className="text-xs text-slate-400">Hora
                <select value={auto.hour} onChange={e => setAuto({ ...auto, hour: Number(e.target.value) })} data-testid="db-auto-hour"
                        className="block mt-1 h-9 px-2 rounded-md bg-sunken border border-line text-slate-100 text-sm">
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </select>
              </label>
              <label className="text-xs text-slate-400">Guardar os últimos
                <Input type="number" min={1} max={90} value={auto.keep} onChange={e => setAuto({ ...auto, keep: Number(e.target.value) || 7 })}
                       className="mt-1 w-24 bg-sunken border-line h-9" data-testid="db-auto-keep" />
              </label>
              <label className="text-xs text-slate-400 flex-1 min-w-[14rem]">Senha dos backups automáticos {auto.has_password && <span className="text-emerald-400">(definida)</span>}
                <div className="mt-1"><Pw value={apw} onChange={setApw} placeholder={auto.has_password ? "deixe vazio para manter" : "mín. 8 caracteres"} testid="db-auto-pw" /></div>
              </label>
            </div>
            <div className="flex flex-wrap gap-x-6">
              <Check checked={auto.config_backups} onChange={v => setAuto({ ...auto, config_backups: v })} label={GROUP_LABEL.config_backups} />
              <Check checked={auto.history} onChange={v => setAuto({ ...auto, history: v })} label={GROUP_LABEL.history} />
              <Check checked={auto.cloud} onChange={v => setAuto({ ...auto, cloud: v })} testid="db-auto-cloud"
                     label="Enviar também para a nuvem" hint={cloudReady ? `${info.cloud.settings.remote}:${info.cloud.settings.folder || ""}/banco` : "configure o drive em Automação e alertas → Backup na nuvem"} />
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <Button onClick={saveAuto} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="db-auto-save">Salvar</Button>
              {auto.last_auto_at && (
                <span className={`text-xs flex items-center gap-1 ${auto.last_auto_ok ? "text-emerald-400" : "text-red-400"}`} data-testid="db-auto-last">
                  {auto.last_auto_ok ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                  último: {fmtDate(auto.last_auto_at)}{auto.last_auto_error ? ` — ${auto.last_auto_error}` : ""}
                  {auto.cloud && auto.last_cloud_ok === false && <span className="text-amber-300"> · nuvem: {auto.last_cloud_error}</span>}
                  {auto.cloud && auto.last_cloud_ok && <span className="text-slate-400 flex items-center gap-1"> · <CloudUpload className="w-3.5 h-3.5" /> na nuvem</span>}
                </span>
              )}
            </div>
          </div>
        )}
      </Section>

      <Section icon={Database} title="Backups guardados no servidor" testid="db-files"
               desc={`Ficam em ${info.dir} (volume do Docker, sobrevive às atualizações). Para levar a outro servidor, baixe o arquivo.`}>
        {info.files.length === 0 ? <div className="text-sm text-slate-500">Nenhum backup ainda.</div> : (
          <div className="overflow-x-auto -mx-1">
            <table className="w-full text-sm">
              <thead className="text-xs text-slate-500"><tr>
                <th className="text-left px-2 py-1.5">Arquivo</th><th className="text-left px-2">Quando</th><th className="text-left px-2">Tipo</th>
                <th className="text-right px-2">Tamanho</th><th className="text-right px-2">Ações</th></tr></thead>
              <tbody className="divide-y divide-line">
                {info.files.map(f => {
                  const [kl, kc] = KIND[f.kind] || ["—", "text-slate-400 border-line"];
                  return (
                    <tr key={f.name} data-testid={`db-file-${f.name}`}>
                      <td className="px-2 py-2 font-mono text-xs text-slate-200 break-all">{f.name}{f.docs != null && <span className="block text-[11px] text-slate-500">{f.docs} documentos{f.by ? ` · ${f.by}` : ""}</span>}</td>
                      <td className="px-2 text-xs text-slate-400 whitespace-nowrap">{fmtDate(f.at)}</td>
                      <td className="px-2"><span className={`text-[11px] px-1.5 py-0.5 rounded border whitespace-nowrap ${kc}`}>{kl}</span></td>
                      <td className="px-2 text-right font-mono text-xs text-slate-300 whitespace-nowrap">{fmtBytes(f.size)}</td>
                      <td className="px-2 text-right whitespace-nowrap">
                        <Button size="sm" variant="ghost" onClick={() => download(f.name)} className="text-brand-soft hover:bg-brand/15" title="Baixar" data-testid={`db-dl-${f.name}`}><Download className="w-4 h-4" /></Button>
                        <Button size="sm" variant="ghost" onClick={() => restoreFromServer(f.name)} disabled={running} className="text-amber-300 hover:bg-amber-500/10" title="Restaurar este backup"><ArchiveRestore className="w-4 h-4" /></Button>
                        <Button size="sm" variant="ghost" onClick={() => del(f.name)} className="text-red-400 hover:bg-red-500/10" title="Apagar"><Trash2 className="w-4 h-4" /></Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex gap-2 mt-3 text-[11px] text-slate-500"><Info className="w-3.5 h-3.5 shrink-0" />
          O arquivo leva a chave que abre as senhas dos equipamentos e agentes, protegida pela senha do backup. Use uma senha forte e guarde o arquivo em local seguro.</div>
      </Section>
    </div>
  );
}

function Step({ ok, label, detail }) {
  const Icon = ok === true ? CheckCircle2 : ok === false ? XCircle : CircleDashed;
  const cls = ok === true ? "text-emerald-400" : ok === false ? "text-red-400" : "text-slate-500";
  return (
    <div className="flex items-start gap-2 text-sm">
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${cls}`} />
      <div><div className="text-slate-200">{label}</div>{detail && <div className="text-xs text-slate-500">{detail}</div>}</div>
    </div>
  );
}

function DomainCheck({ domain, onRemove }) {
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = useCallback(async () => {
    setBusy(true);
    try { setR((await api.post("/system/domain/check", { domain })).data); } catch (e) { toast.error(formatApiError(e)); }
    setBusy(false);
  }, [domain]);
  useEffect(() => { run(); }, [run]);
  const all = r?.dns_ok && r?.https_ok;
  return (
    <div className="rounded-md border border-line p-3" data-testid={`dom-check-${domain}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <a href={`https://${domain}`} target="_blank" rel="noreferrer" className="font-mono text-slate-100 hover:text-brand-soft">{domain}</a>
        {r && <span className={`text-[11px] px-1.5 py-0.5 rounded border ${all ? "text-emerald-300 border-emerald-500/40" : "text-amber-300 border-amber-500/40"}`}>{all ? "funcionando" : "pendente"}</span>}
        <Button size="sm" variant="ghost" onClick={run} disabled={busy} className="ml-auto h-7 text-xs text-slate-300" data-testid={`dom-recheck-${domain}`}>
          <RefreshCw className={`w-3.5 h-3.5 mr-1 ${busy ? "animate-spin" : ""}`} /> Verificar
        </Button>
        <Button size="sm" variant="ghost" onClick={onRemove} className="h-7 text-red-400 hover:bg-red-500/10" title="Remover" data-testid={`dom-del-${domain}`}><Trash2 className="w-4 h-4" /></Button>
      </div>
      {r && (
        <div className="grid gap-1.5 mt-2">
          <Step ok={r.dns_ok} label="DNS aponta para este servidor"
                detail={r.a.length ? `registro A: ${r.a.join(", ")}${r.server_ip ? ` · este servidor: ${r.server_ip}` : ""}` : "sem registro A ainda"} />
          <Step ok={r.dns_ok ? r.http_ok : null} label="Porta 80 aberta" detail="usada pelo Let's Encrypt para emitir o certificado" />
          <Step ok={r.dns_ok ? r.cert_ok : null} label="Certificado HTTPS válido" />
          <Step ok={r.dns_ok ? r.https_ok : null} label={`https://${domain} abre este BastiON`} />
          {r.error && <div className="text-xs text-amber-300 mt-1">{r.error}</div>}
        </div>
      )}
    </div>
  );
}

function DomainTab() {
  const [st, setSt] = useState(null);
  const [list, setList] = useState([]);
  const [add, setAdd] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get("/system/domain").then(r => { setSt(r.data); setList(r.data.domains); }).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const save = async (next) => {
    setBusy(true);
    try { const { data } = await api.put("/system/domain", { domains: next }); setSt(data); setList(data.domains); toast.success("Domínios salvos — o HTTPS é configurado em instantes"); }
    catch (e) { toast.error(formatApiError(e)); }
    setBusy(false);
  };
  const addOne = () => {
    const d = add.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (!d) return;
    if (list.includes(d)) return toast.error("Esse domínio já está na lista");
    setAdd(""); save([...list, d]);
  };
  const copy = (t) => { navigator.clipboard?.writeText(t); toast.success("Copiado"); };
  if (!st) return <div className="text-sm text-slate-500 font-mono">Carregando…</div>;
  const ip = st.server_ip || "IP_DO_SERVIDOR";
  const root = list[0] || "seudominio.com.br";
  return (
    <div className="space-y-4" data-testid="settings-domain">
      <Section icon={Globe} title="Domínios do BastiON" testid="dom-card"
               desc="O BastiON passa a abrir também nestes endereços, com HTTPS automático (Let's Encrypt). O endereço atual continua funcionando.">
        <div className="flex gap-2 max-w-xl">
          <Input value={add} onChange={e => setAdd(e.target.value)} onKeyDown={e => e.key === "Enter" && addOne()} placeholder="ex.: bastion.net.br"
                 className="bg-sunken border-line font-mono h-9" data-testid="dom-add-input" />
          <Button onClick={addOne} disabled={busy || !add.trim()} className="bg-brand hover:bg-brand-strong text-onbrand shrink-0" data-testid="dom-add">
            <Plus className="w-4 h-4 mr-1" /> Adicionar
          </Button>
        </div>
        <div className="text-[11px] text-slate-500 mt-1.5">Endereço configurado na instalação: <span className="font-mono text-slate-400">{st.env_domain || "—"}</span>
          {st.caddy?.state === "erro" && <span className="text-red-400"> · Caddy não aplicou: {st.caddy.message}</span>}
          {st.caddy?.state === "ok" && st.caddy?.at && <span> · servidor web atualizado em {fmtDate(st.caddy.at)}</span>}
        </div>
        <div className="space-y-2 mt-4">
          {list.length === 0 && <div className="text-sm text-slate-500">Nenhum domínio próprio ainda.</div>}
          {list.map(d => (
            <DomainCheck key={d} domain={d} onRemove={() => window.confirm(`Tirar ${d} do BastiON?`) && save(list.filter(x => x !== d))} />
          ))}
        </div>
      </Section>

      <Section icon={Info} title="Como apontar o domínio (HostGator e outros)" testid="dom-help">
        <ol className="list-decimal pl-5 space-y-2 text-sm text-slate-300">
          <li>No painel do registro do domínio (HostGator: <span className="text-slate-100">Portal do Cliente → Domínios → Gerenciar → Zona DNS</span>), mantenha os nameservers padrão dele.</li>
          <li>Crie (ou edite) estes registros:
            <div className="overflow-x-auto mt-2">
              <table className="text-xs font-mono border border-line rounded" data-testid="dom-records">
                <thead className="text-slate-500"><tr><th className="text-left px-3 py-1.5">Tipo</th><th className="text-left px-3">Nome</th><th className="text-left px-3">Valor</th><th className="text-left px-3">TTL</th><th /></tr></thead>
                <tbody className="divide-y divide-line text-slate-200">
                  <tr><td className="px-3 py-1.5">A</td><td className="px-3">@ <span className="text-slate-500">({root})</span></td><td className="px-3">{ip}</td><td className="px-3">3600</td>
                    <td className="px-2"><button onClick={() => copy(ip)} className="text-slate-500 hover:text-slate-200" title="Copiar IP"><Copy className="w-3.5 h-3.5" /></button></td></tr>
                  <tr><td className="px-3 py-1.5">CNAME</td><td className="px-3">www</td><td className="px-3">{root}</td><td className="px-3">3600</td><td /></tr>
                </tbody>
              </table>
            </div>
            <span className="block text-xs text-slate-500 mt-1">Se já existir registro A em @ ou www apontando para a hospedagem da HostGator, troque o valor (não crie outro). Para usar um subdomínio, ex. noc.{root}, crie só um registro A com nome <span className="font-mono">noc</span>.</span>
          </li>
          <li>Libere as portas <span className="font-mono">80</span> e <span className="font-mono">443</span> (TCP) no firewall do servidor.</li>
          <li>Adicione o domínio acima (e o <span className="font-mono">www.</span>, se criou o CNAME). Quando o DNS propagar (de minutos a algumas horas), o certificado sai sozinho e os itens ficam verdes.</li>
        </ol>
      </Section>
    </div>
  );
}

export default function Settings() {
  const [sp, setSp] = useSearchParams();
  const tab = TABS.some(t => t.id === sp.get("tab")) ? sp.get("tab") : "aparencia";
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="settings-page">
      <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Configurações</h1>
      <p className="text-slate-400 mt-1 mb-5 text-sm">Aparência, banco de dados, automação e o que vale para o sistema inteiro. Só administradores.</p>
      <div className="flex flex-col lg:flex-row gap-4 lg:gap-6">
        <nav className="flex lg:flex-col gap-1 overflow-x-auto lg:w-52 shrink-0 -mx-1 px-1 pb-1 lg:pb-0" aria-label="Seções">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" onClick={() => setSp({ tab: id })} data-testid={`settings-tab-${id}`} aria-current={tab === id ? "page" : undefined}
                    className={`shrink-0 flex items-center gap-2 px-3 py-2 rounded-md text-sm text-left transition-colors ${tab === id ? "bg-brand/15 text-slate-100" : "text-slate-400 hover:text-slate-100 hover:bg-white/5"}`}>
              <Icon className={`w-4 h-4 ${tab === id ? "text-brand-soft" : ""}`} /> {label}
            </button>
          ))}
        </nav>
        <div className="flex-1 min-w-0">
          {tab === "aparencia" && <AppearanceTab />}
          {tab === "dominio" && <DomainTab />}
          {tab === "banco" && <DatabaseTab />}
          {tab === "automacao" && <Automation embedded />}
          {tab === "chave" && <SshKey embedded />}
        </div>
      </div>
    </div>
  );
}
