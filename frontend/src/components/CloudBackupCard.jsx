import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Cloud, Loader2, CheckCircle2, AlertTriangle, UploadCloud, RefreshCw } from "lucide-react";

const fmt = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR") : "—");

/** Automação → Backups na nuvem: quais tags vão para qual drive (rclone no servidor). */
export function CloudBackupCard() {
  const [st, setSt] = useState(null);
  const [f, setF] = useState(null);
  const [busy, setBusy] = useState("");                 // "" | save | test | run
  const [testRes, setTestRes] = useState(null);

  const apply = (data) => { setSt(data); setF(data.settings); };
  const load = () => api.get("/cloud/status").then(r => apply(r.data)).catch(() => setSt({ error: true }));
  useEffect(() => { load(); }, []);
  if (!st || st.error || !f) return null;

  const dirty = JSON.stringify(f) !== JSON.stringify(st.settings);
  const toggleTag = (t) => setF({ ...f, tags: f.tags.includes(t) ? f.tags.filter(x => x !== t) : [...f.tags, t].sort() });
  const save = async () => {
    setBusy("save");
    try { apply((await api.put("/cloud/settings", f)).data); toast.success("Backups na nuvem salvos"); return true; }
    catch (e) { toast.error(formatApiError(e)); return false; }
    finally { setBusy(""); }
  };
  const test = async () => {
    setBusy("test"); setTestRes(null);
    try { setTestRes((await api.post("/cloud/test", f)).data); }
    catch (e) { setTestRes({ ok: false, error: formatApiError(e) }); }
    finally { setBusy(""); }
  };
  const run = async () => {
    if (dirty && !(await save())) return;
    setBusy("run");
    try {
      const { data } = await api.post("/cloud/run");
      if (data.ok) toast.success(data.sent ? `${data.sent} arquivo(s) enviados para a nuvem` : "Nada novo para enviar — a nuvem já está em dia");
      else toast.error(data.error || "O envio falhou");
      await load();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(""); }
  };

  const last = st.last || {};
  const noRemote = st.remotes.length === 0;
  const inputCls = "bg-sunken border-line";

  return (
    <Card className="bg-surface border-line p-5 mt-6" data-testid="cloud-card">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Cloud className="w-4 h-4 text-brand-soft" />
          <div>
            <div className="text-sm font-semibold text-slate-100">Backups na nuvem</div>
            <div className="text-xs text-slate-400">Depois de cada backup, as configurações das tags escolhidas são copiadas para o seu drive, em pastas por tag e equipamento.</div>
          </div>
        </div>
        <Switch checked={!!f.enabled} onCheckedChange={v => setF({ ...f, enabled: v })} data-testid="cloud-enabled" aria-label="Ligar backups na nuvem" />
      </div>

      {!st.rclone && (
        <div className="text-sm text-amber-300 border border-amber-400/30 bg-amber-400/5 rounded-md p-3 mb-3" data-testid="cloud-no-rclone">
          O rclone ainda não está no servidor. Rode <span className="font-mono">sudo bash /opt/bastion/deploy/update.sh</span> e volte aqui.
        </div>
      )}
      {st.rclone && noRemote && (
        <div className="text-sm text-slate-300 border border-line rounded-md p-3 mb-3 space-y-1" data-testid="cloud-no-remote">
          <div className="text-amber-300 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" /> Nenhum drive autorizado ainda.</div>
          <div>No servidor, rode uma vez: <span className="font-mono text-slate-100">sudo bash /opt/bastion/deploy/cloud-auth.sh</span></div>
          <div className="text-slate-400">Ele mostra um link para você entrar na conta do Google e permitir. Depois clique em atualizar aqui.</div>
          <button onClick={load} className="text-brand-soft hover:underline inline-flex items-center gap-1 text-xs"><RefreshCw className="w-3 h-3" /> atualizar</button>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <Label className="text-xs text-slate-400">Drive</Label>
          <select value={f.remote} onChange={e => setF({ ...f, remote: e.target.value })} data-testid="cloud-remote"
                  className="mt-1 w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100">
            <option value="">— escolha —</option>
            {st.remotes.map(r => <option key={r} value={r}>{r}</option>)}
            {f.remote && !st.remotes.includes(f.remote) && <option value={f.remote}>{f.remote} (não autorizado)</option>}
          </select>
        </div>
        <div>
          <Label className="text-xs text-slate-400">Pasta no drive</Label>
          <Input value={f.folder} onChange={e => setF({ ...f, folder: e.target.value })} placeholder="BastiON" className={`${inputCls} mt-1`} data-testid="cloud-folder" />
        </div>
      </div>

      <div className="mt-3">
        <Label className="text-xs text-slate-400">Tags que vão para a nuvem</Label>
        <div className="flex flex-wrap gap-1.5 mt-1.5" data-testid="cloud-tags">
          {st.all_tags.length === 0 && <span className="text-xs text-slate-500">Nenhum equipamento tem tag ainda.</span>}
          {st.all_tags.map(t => (
            <button key={t} onClick={() => toggleTag(t)} aria-pressed={f.tags.includes(t)} data-testid={`cloud-tag-${t}`}
                    className={`px-2 py-0.5 rounded-md text-xs border ${f.tags.includes(t) ? "bg-brand border-brand text-white" : "bg-surface border-line text-slate-300 hover:border-line2"}`}>{t}</button>
          ))}
        </div>
        {f.remote && f.tags.length > 0 && (
          <div className="text-[11px] font-mono text-slate-500 mt-2" data-testid="cloud-path">
            {f.remote}:{f.folder ? f.folder + "/" : ""}{f.tags[0]}/NOME-DO-EQUIPAMENTO/2026-10-02_03-00-00.cfg
          </div>
        )}
      </div>

      <label className="flex items-center justify-between gap-3 mt-3 text-sm text-slate-300">
        <span>Enviar só quando a configuração mudou <span className="text-slate-500">(desligado: todas as versões, uma por backup)</span></span>
        <Switch checked={f.only_changed !== false} onCheckedChange={v => setF({ ...f, only_changed: v })} data-testid="cloud-only-changed" />
      </label>

      <div className="flex flex-wrap items-center gap-2 mt-4">
        <Button onClick={save} disabled={!dirty || !!busy} className="bg-brand hover:bg-brand-strong" data-testid="cloud-save">
          {busy === "save" && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar
        </Button>
        <Button variant="outline" onClick={test} disabled={!f.remote || !!busy} className="border-line bg-transparent text-slate-200" data-testid="cloud-test">
          {busy === "test" && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Testar acesso
        </Button>
        <Button variant="outline" onClick={run} disabled={!f.remote || !f.tags.length || !!busy || st.running} className="border-line bg-transparent text-slate-200" data-testid="cloud-run">
          {busy === "run" || st.running ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <UploadCloud className="w-4 h-4 mr-2" />} Enviar agora
        </Button>
        {testRes && (testRes.ok
          ? <span className="text-xs text-emerald-400 flex items-center gap-1" data-testid="cloud-test-ok"><CheckCircle2 className="w-3.5 h-3.5" /> acesso OK{testRes.items?.length ? ` — ${testRes.items.length} item(ns) na pasta` : " — pasta criada"}</span>
          : <span className="text-xs text-amber-300 flex items-center gap-1" data-testid="cloud-test-err"><AlertTriangle className="w-3.5 h-3.5" /> {testRes.error}</span>)}
      </div>

      {last.at && (
        <div className={`mt-3 text-xs ${last.ok ? "text-slate-400" : "text-amber-300"}`} data-testid="cloud-last">
          {last.ok
            ? <>Última verificação: {fmt(last.at)} — {last.sent ? `${last.sent} arquivo(s) enviados` : "nada novo"}{last.last_sent_at && !last.sent ? ` (último envio: ${fmt(last.last_sent_at)})` : ""}</>
            : <>Último envio falhou ({fmt(last.at)}): {last.error}</>}
        </div>
      )}
      <div className="text-[11px] text-slate-500 mt-2">O BastiON só copia: nunca apaga nada no drive. As configurações têm senhas e chaves dos equipamentos — use uma pasta que só você acessa.</div>
    </Card>
  );
}
