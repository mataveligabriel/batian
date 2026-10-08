import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Compass, Layers, ShieldAlert, Cable, Settings2, ShieldBan, Handshake, Bug, Share2 } from "lucide-react";
import { FlowShareDialog } from "@/components/flow/FlowShare";
import { useAuth } from "@/context/AuthContext";
import { FlowExplorer } from "@/components/flow/FlowExplorer";
import { FlowContents } from "@/components/flow/FlowContents";
import { FlowAttacks } from "@/components/flow/FlowAttacks";
import { FlowInterfaces } from "@/components/flow/FlowInterfaces";
import { FlowSettings } from "@/components/flow/FlowSettings";
import { FlowMitigation } from "@/components/flow/FlowMitigation";
import { FlowPeering } from "@/components/flow/FlowPeering";
import { FlowBotnet } from "@/components/flow/FlowBotnet";
import { STATUS } from "@/lib/netfmt";
import { fixedColors } from "@/components/flow/flowlib";

const tabBtn = (on) => `flex items-center gap-2 px-4 py-2 text-sm -mb-px border-b-2 ${on ? "border-brand text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`;
const TABS = [["explorar", "Explorar", Compass], ["conteudos", "Conteúdos", Layers], ["ataques", "Ataques", ShieldAlert],
              ["mitigacao", "Mitigação", ShieldBan], ["botnet", "Botnet (BNG)", Bug], ["peering", "Peering", Handshake],
              ["interfaces", "Interfaces", Cable], ["config", "Configuração", Settings2]];

export default function Flow() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState(TABS.some(t => t[0] === params.get("tab")) ? params.get("tab") : "explorar");
  const [settings, setSettings] = useState(null);
  const [ifaces, setIfaces] = useState([]);
  const [liveAt, setLiveAt] = useState(null);
  const [groups, setGroups] = useState([]);
  const [active, setActive] = useState(0);
  const [bots, setBots] = useState(0);
  const [share, setShare] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [err, setErr] = useState("");

  const loadIfaces = useCallback(async () => { const { data } = await api.get("/flow/interfaces"); setIfaces(data.items); setLiveAt(data.live_at); }, []);
  const loadGroups = useCallback(async () => setGroups((await api.get("/flow/groups")).data), []);
  const loadSettings = useCallback(async () => setSettings((await api.get("/flow/settings")).data), []);
  useEffect(() => {
    Promise.all([loadSettings(), loadIfaces(), loadGroups()]).catch(e => setErr(formatApiError(e)));
    const t = setInterval(() => loadIfaces().catch(() => {}), 30000);
    // contador de ataques ativos no título da aba, mesmo fora da aba Ataques
    const pollAtt = () => api.get("/flow/attacks", { params: { status: "active", limit: 50 } }).then(r => setActive(r.data.length)).catch(() => {});
    pollAtt();
    const t2 = setInterval(pollAtt, 20000);
    return () => { clearInterval(t); clearInterval(t2); };
  }, [loadSettings, loadIfaces, loadGroups]);
  useEffect(() => {
    const want = params.get("tab");
    if (want && TABS.some(t => t[0] === want)) { setTab(want); setParams({}, { replace: true }); }
  }, [params]); // eslint-disable-line react-hooks/exhaustive-deps

  // a mesma interface / o mesmo conteúdo tem a mesma cor em todas as abas (quando cabem nas 8 cores)
  const ifColors = useMemo(() => fixedColors([...new Set(ifaces.map(i => i.key))]), [ifaces]);
  const groupColors = useMemo(() => fixedColors(groups.map(g => g.id)), [groups]);

  return (
    <div className="flex-1 flex flex-col min-h-0" data-testid="flow-page">
      <div className="px-4 md:px-6 pt-4">
        <div className="hidden md:block text-xs text-slate-400">NetFlow · IPFIX · sFlow</div>
        <div className="flex items-center gap-3">
          <h1 className="hidden md:block font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Análise de Flow</h1>
          {isAdmin && <button onClick={() => setShare(true)} className="ml-auto mt-1 flex items-center gap-1.5 text-xs text-slate-300 border border-line rounded-md px-2.5 h-8 hover:bg-slate-800" data-testid="flow-share-btn"><Share2 className="w-3.5 h-3.5" />Compartilhar</button>}
        </div>
        {isAdmin && share && <FlowShareDialog onClose={() => setShare(false)} />}
        <div className="flex gap-1 md:mt-4 border-b border-line overflow-x-auto whitespace-nowrap">
          {TABS.map(([k, l, I]) => (
            <button key={k} className={tabBtn(tab === k)} onClick={() => setTab(k)} data-testid={`flow-tab-${k}`}>
              <I className="w-4 h-4" /> {l}
              {k === "ataques" && active > 0 && <span className="text-[10px] font-mono font-bold px-1.5 rounded-full text-white" style={{ background: STATUS.critical }} data-testid="attack-badge">{active}</span>}
              {k === "botnet" && bots > 0 && <span className="text-[10px] font-mono font-bold px-1.5 rounded-full text-white" style={{ background: STATUS.warning }} data-testid="botnet-badge">{bots}</span>}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-4 md:px-6 md:pb-6">
        {err && <div className="text-sm text-amber-300 mb-3">{err}</div>}
        {tab === "explorar" && <FlowExplorer ifaces={ifaces} groups={groups} ifColors={ifColors} groupColors={groupColors} />}
        {tab === "conteudos" && <FlowContents ifaces={ifaces} groups={groups} presets={settings?.presets || []} reload={loadGroups} ifColors={ifColors} groupColors={groupColors} />}
        {tab === "ataques" && <FlowAttacks settings={settings} onCount={setActive} />}
        {tab === "mitigacao" && <FlowMitigation />}
        {tab === "botnet" && <FlowBotnet onCount={setBots} />}
        {tab === "peering" && <FlowPeering goConfig={() => setTab("config")} goIfaces={() => setTab("interfaces")} />}
        {tab === "interfaces" && <FlowInterfaces ifaces={ifaces} liveAt={liveAt} reload={loadIfaces} />}
        {tab === "config" && <FlowSettings settings={settings} reload={loadSettings} />}
      </div>
    </div>
  );
}
