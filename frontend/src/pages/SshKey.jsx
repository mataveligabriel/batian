import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { KeyRound, Save, Wand2, Copy, Loader2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function SshKey() {
  const [config, setConfig] = useState({ private_key: "", public_key: "", default_username: "root", has_key: false });
  const [busy, setBusy] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const load = async () => setConfig((await api.get("/ssh-key")).data);
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    try {
      await api.put("/ssh-key", {
        private_key: config.private_key, public_key: config.public_key,
        default_username: config.default_username || "root",
      });
      toast.success("Chave SSH salva");
      await load();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const generate = async () => {
    if (!window.confirm("Gerar nova chave RSA 2048? Isto substituirá a chave atual.")) return;
    setBusy(true);
    try {
      const { data } = await api.post("/ssh-key/generate");
      setConfig(c => ({ ...c, private_key: data.private_key, public_key: data.public_key, has_key: true }));
      toast.success("Chave gerada. Copie a chave pública para authorized_keys dos equipamentos.");
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const copy = (t) => { navigator.clipboard.writeText(t); toast.success("Copiado"); };

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="ssh-key-page">
      <div className="mb-6">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Credenciais Globais</div>
        <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Chave SSH Global</h1>
        <p className="text-slate-400 mt-2 text-sm max-w-2xl">
          Chave privada única usada pelo Bastion Central para conectar em todos os equipamentos e agentes.
          Distribua a chave pública para <code className="text-emerald-300 font-mono">~/.ssh/authorized_keys</code>.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="bg-[#111722] border-[#1E293B] p-5 lg:col-span-1" data-testid="ssh-config-card">
          <div className="flex items-center gap-2 mb-3">
            <KeyRound className="w-4 h-4 text-[#4DA3FF]" />
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Configuração</div>
          </div>
          <div>
            <Label>Usuário padrão</Label>
            <Input data-testid="ssh-default-user" value={config.default_username || "root"} onChange={e => setConfig({ ...config, default_username: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
          </div>
          <div className="mt-4 text-xs font-mono text-slate-500">
            Status: {config.has_key
              ? <span className="text-emerald-400">chave carregada</span>
              : <span className="text-amber-400">nenhuma chave</span>}
          </div>
          {isAdmin && (
            <div className="flex gap-2 mt-4">
              <Button onClick={generate} disabled={busy} data-testid="generate-key-btn"
                      className="flex-1 bg-[#111722] border border-[#1E293B] text-slate-100 hover:bg-slate-800">
                {busy ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Wand2 className="w-4 h-4 mr-2" />}
                Gerar chave
              </Button>
              <Button onClick={save} disabled={busy} data-testid="save-key-btn" className="flex-1 bg-[#007AFF] hover:bg-[#0062CC]">
                <Save className="w-4 h-4 mr-2" /> Salvar
              </Button>
            </div>
          )}
        </Card>

        <Card className="bg-[#111722] border-[#1E293B] p-5 lg:col-span-2" data-testid="ssh-keys-card">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Chave privada (PEM)</div>
            {config.private_key && <Button size="sm" variant="ghost" onClick={() => copy(config.private_key)} className="text-slate-400"><Copy className="w-3.5 h-3.5" /></Button>}
          </div>
          <Textarea
            data-testid="private-key-input"
            rows={10}
            value={config.private_key}
            onChange={e => setConfig({ ...config, private_key: e.target.value })}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
            className="bg-[#05070A] border-[#1E293B] font-mono text-xs text-slate-200"
            disabled={!isAdmin}
          />

          <div className="mt-4 mb-3 flex items-center justify-between">
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Chave pública</div>
            {config.public_key && <Button size="sm" variant="ghost" onClick={() => copy(config.public_key)} data-testid="copy-pubkey-btn" className="text-slate-400"><Copy className="w-3.5 h-3.5" /></Button>}
          </div>
          <Textarea
            data-testid="public-key-input"
            rows={4}
            value={config.public_key}
            onChange={e => setConfig({ ...config, public_key: e.target.value })}
            placeholder="ssh-rsa AAAA..."
            className="bg-[#05070A] border-[#1E293B] font-mono text-xs text-emerald-300"
            disabled={!isAdmin}
          />
        </Card>
      </div>
    </div>
  );
}
