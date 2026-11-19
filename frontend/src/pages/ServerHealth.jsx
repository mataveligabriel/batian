import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Cpu, Gauge as MemoryStick, HardDrive, Activity, Database, Timer, Network, AlertTriangle, CheckCircle2, RefreshCw } from "lucide-react";

// ------------------------------------------------------------------ formatação
const fmtB = (b) => (b == null ? "—" : b >= 1e12 ? `${(b / 1e12).toFixed(1)} TB` : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(0)} MB` : `${Math.round(b / 1e3)} kB`);
const fmtBps = (b) => (b == null ? "—" : b >= 1e9 ? `${(b / 1e9).toFixed(1)} Gbps` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} Mbps` : `${Math.round(b / 1e3)} kbps`);
const fmtDur = (s) => { if (s == null) return "—"; s = Math.round(s); const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : `${m}min`; };
const fmtS = (s) => (s == null ? "—" : s >= 60 ? `${Math.floor(s / 60)}min ${Math.round(s % 60)}s` : `${s.toFixed(s < 10 ? 1 : 0)}s`);
const ago = (t) => (t ? fmtS(Math.max(0, Date.now() / 1000 - t)) : "—");
const pct = (a, b) => (b ? (100 * a) / b : 0);
const tone = (v, warn, bad) => (v >= bad ? "#f87171" : v >= warn ? "#fbbf24" : "#2dd4bf");

// ------------------------------------------------------------------ gráfico de linha simples (SVG)
function Spark({ series, max, h = 56, colors = ["#2dd4bf", "#a78bfa"], unit = "" }) {
  const n = Math.max(...series.map(s => s.length), 0);
  if (n < 2) return <div className="h-14 flex items-center text-[11px] text-slate-600">coletando… (1 ponto a cada 10 s)</div>;
  const top = max || Math.max(1, ...series.flat().filter(v => v != null)) * 1.15;
  const W = 300;
  const path = (s) => s.map((v, i) => `${i ? "L" : "M"}${((i / (n - 1)) * W).toFixed(1)},${(h - (Math.min(top, v ?? 0) / top) * (h - 4) - 2).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${h}`} preserveAspectRatio="none" className="w-full" style={{ height: h }} aria-hidden="true">
      {[0.5, 1].map(f => <line key={f} x1="0" x2={W} y1={h - f * (h - 4) - 2} y2={h - f * (h - 4) - 2} stroke="currentColor" className="text-white/5" />)}
      {series.map((s, i) => (
        <g key={i}>
          {i === 0 && <path d={`${path(s)} L${W},${h} L0,${h} Z`} fill={colors[i]} opacity="0.12" />}
          <path d={path(s)} fill="none" stroke={colors[i]} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
        </g>
      ))}
    </svg>
  );
}

function Card({ icon: Icon, title, value, sub, color, children }) {
  return (
    <div className="rounded-lg border border-line bg-panel p-4 min-w-0">
      <div className="flex items-center gap-2 text-xs text-slate-400"><Icon className="w-3.5 h-3.5" />{title}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums" style={{ color: color || "#e2e8f0" }}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500 truncate">{sub}</div>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ página
export default function ServerHealth() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => { setBusy(true); return api.get("/system/health").then(r => { setD(r.data); setErr(""); }).catch(e => setErr(formatApiError(e))).finally(() => setBusy(false)); };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  const H = d?.history || [];
  const now = d?.now || {};
  const mem = now.mem || {};
  const memTotal = mem.limit || mem.total, memUsed = mem.limit ? mem.limit_used : mem.used;
  const cpu = now.cpu?.used ?? null;
  const load1 = now.load?.[0];
  const cpus = d?.cpus || 1;

  // o que está pesando, em linguagem direta
  const hints = useMemo(() => {
    if (!d) return [];
    const out = [];
    const last = H.slice(-6);
    const avg = (f) => (last.length ? last.reduce((a, x) => a + (f(x) || 0), 0) / last.length : 0);
    const cpuAvg = avg(x => x.cpu?.used), lagAvg = avg(x => x.lag_ms), ioAvg = avg(x => x.cpu?.iowait);
    if (cpuAvg >= 85) out.push(["bad", `CPU alta no último minuto (${cpuAvg.toFixed(0)}% em média).`]);
    if (ioAvg >= 20) out.push(["bad", `Muito tempo esperando o disco (iowait ${ioAvg.toFixed(0)}%) — disco lento ou cheio de escrita.`]);
    if (load1 > cpus * 1.5) out.push(["warn", `Carga ${load1} para ${cpus} núcleo(s): tem mais trabalho do que CPU.`]);
    if (pct(memUsed, memTotal) >= 90) out.push(["bad", `Memória quase cheia (${pct(memUsed, memTotal).toFixed(0)}%).`]);
    // swap ocupado sozinho não é problema (o Linux guarda lá o que está parado); lento é quando ele fica trocando páginas
    const swapIo = avg(x => x.swap_io);
    if (swapIo >= 200) out.push(["bad", `O servidor está trocando memória com o disco (swap) o tempo todo (${Math.round(swapIo)} páginas/s) — falta memória e isso deixa tudo lento.`]);
    else if (pct(memUsed, memTotal) >= 85 && mem.swap_used > 256e6) out.push(["warn", `Memória em ${pct(memUsed, memTotal).toFixed(0)}% e ${fmtB(mem.swap_used)} no swap: está perto do limite.`]);
    (d.disks || []).forEach(k => { if (pct(k.used, k.total) >= 90) out.push(["bad", `Disco ${k.path} com ${pct(k.used, k.total).toFixed(0)}% usado.`]); });
    if (lagAvg >= 300) out.push(["bad", `O backend está demorando ${Math.round(lagAvg)} ms para atender (deveria ficar perto de 0): alguma rotina está segurando o processo.`]);
    else if (lagAvg >= 80) out.push(["warn", `O backend está com ${Math.round(lagAvg)} ms de atraso médio.`]);
    if (d.mongo?.ping_ms >= 100) out.push(["warn", `Banco respondendo devagar (${d.mongo.ping_ms} ms).`]);
    (d.jobs || []).forEach(j => { if ((j.last_s || 0) >= 120) out.push(["warn", `"${j.name}" levou ${fmtS(j.last_s)} na última rodada${j.items ? ` (${j.items} itens)` : ""}.`]); });
    return out;
  }, [d]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err && !d) return <div className="p-8 text-sm text-red-300">{err}</div>;
  if (!d) return <div className="p-8 text-sm text-slate-500">Lendo o servidor…</div>;

  return (
    <div className="flex-1 overflow-y-auto" data-testid="server-health">
      <div className="max-w-7xl mx-auto p-4 md:p-6 space-y-5">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <h1 className="font-heading text-2xl font-semibold text-slate-100">Servidor do BastiON</h1>
            <p className="text-sm text-slate-400">{cpus} núcleo(s) · ligado há {fmtDur(d.uptime_s)} · {d.counts.devices} equipamentos · {d.counts.agents} jumps · atualiza a cada 10 s</p>
          </div>
          <button onClick={load} className="ml-auto inline-flex items-center gap-1.5 h-8 px-3 rounded-md border border-line text-xs text-slate-300 hover:bg-white/5" aria-label="Atualizar">
            <RefreshCw className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} />Atualizar
          </button>
        </div>

        <div className={`rounded-lg border p-3 text-sm ${hints.some(h => h[0] === "bad") ? "border-red-500/40 bg-red-500/10" : hints.length ? "border-amber-500/40 bg-amber-500/10" : "border-emerald-500/30 bg-emerald-500/5"}`} data-testid="server-hints">
          {hints.length === 0
            ? <div className="flex items-center gap-2 text-emerald-200"><CheckCircle2 className="w-4 h-4" />Nada pesando agora: CPU, memória, disco, banco e backend dentro do normal.</div>
            : <ul className="space-y-1">{hints.map(([lv, t], i) => <li key={i} className="flex items-start gap-2"><AlertTriangle className={`w-4 h-4 mt-0.5 shrink-0 ${lv === "bad" ? "text-red-400" : "text-amber-400"}`} /><span className="text-slate-200">{t}</span></li>)}</ul>}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <Card icon={Cpu} title="CPU" value={cpu == null ? "—" : `${Math.min(100, cpu).toFixed(0)}%`} color={tone(cpu || 0, 70, 85)}
                sub={`iowait ${now.cpu?.iowait != null ? Math.round(now.cpu.iowait) : "—"}%${now.cpu?.steal ? ` · steal ${now.cpu.steal}%` : ""} · carga ${now.load?.map(x => x.toFixed(2)).join(" / ") || "—"}`}>
            <Spark series={[H.map(x => x.cpu?.used), H.map(x => x.cpu?.iowait)]} max={100} unit="%" colors={["#2dd4bf", "#f59e0b"]} />
          </Card>
          <Card icon={MemoryStick} title={mem.limit ? "Memória (limite do contêiner)" : "Memória"} value={`${pct(memUsed, memTotal).toFixed(0)}%`} color={tone(pct(memUsed, memTotal), 80, 90)}
                sub={`${fmtB(memUsed)} de ${fmtB(memTotal)}${mem.swap_total ? ` · swap ${fmtB(mem.swap_used)}${now.swap_io != null ? (now.swap_io >= 1 ? `, trocando ${Math.round(now.swap_io)} pág/s` : ", parado") : ""}` : ""}`}>
            <Spark series={[H.map(x => pct(x.mem?.limit ? x.mem.limit_used : x.mem?.used, x.mem?.limit || x.mem?.total))]} max={100} unit="%" colors={["#a78bfa"]} />
          </Card>
          <Card icon={Timer} title="Atraso do backend" value={now.lag_ms == null ? "—" : `${Math.round(now.lag_ms)} ms`} color={tone(now.lag_ms || 0, 80, 300)}
                sub="quanto o processo demora para atender; perto de 0 é o normal">
            <Spark series={[H.map(x => x.lag_ms)]} unit=" ms" colors={["#38bdf8"]} />
          </Card>
          <Card icon={Network} title="Rede do servidor" value={fmtBps((now.rx_bps || 0) + (now.tx_bps || 0))} sub={`↓ ${fmtBps(now.rx_bps)} · ↑ ${fmtBps(now.tx_bps)}`}>
            <Spark series={[H.map(x => x.rx_bps), H.map(x => x.tx_bps)]} colors={["#2dd4bf", "#f472b6"]} />
          </Card>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
            <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2 mb-1"><Activity className="w-4 h-4 text-slate-400" />Rotinas de fundo</h2>
            <p className="text-[11px] text-slate-500 mb-2">Quanto tempo levou a última rodada de cada uma. Aparecem depois da primeira rodada após o backend reiniciar.</p>
            {(d.jobs || []).length === 0 ? <div className="text-xs text-slate-500 py-3">Nenhuma rodada registrada ainda.</div> : (
              <table className="w-full text-xs">
                <thead><tr className="text-left text-slate-500"><th className="py-1 font-normal">Rotina</th><th className="font-normal text-right">Última</th><th className="font-normal text-right">Maior</th><th className="font-normal text-right">Itens</th><th className="font-normal text-right">Rodou há</th></tr></thead>
                <tbody>
                  {d.jobs.map(j => (
                    <tr key={j.name} className="border-t border-line/60">
                      <td className="py-1.5 text-slate-200">{j.name}{j.ok === false && <span className="ml-1 text-red-400">· falhou</span>}</td>
                      <td className="text-right font-mono" style={{ color: tone(j.last_s || 0, 60, 120) }}>{fmtS(j.last_s)}</td>
                      <td className="text-right font-mono text-slate-400">{fmtS(j.max_s)}</td>
                      <td className="text-right font-mono text-slate-400">{j.items ?? "—"}</td>
                      <td className="text-right text-slate-500">{ago(j.at)}</td>
                    </tr>))}
                </tbody>
              </table>)}
            {d.avail?.duration_s != null && <div className="mt-2 text-[11px] text-slate-500">Disponibilidade: {d.avail.devices} equipamento(s) pingados em {d.avail.duration_s}s no último ciclo.</div>}
          </section>

          <section className="rounded-lg border border-line bg-panel p-4 space-y-3">
            <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2"><HardDrive className="w-4 h-4 text-slate-400" />Disco e processo</h2>
            {(d.disks || []).map(k => {
              const p = pct(k.used, k.total);
              return (
                <div key={k.path}>
                  <div className="flex text-xs"><span className="text-slate-300 font-mono">{k.path}</span><span className="ml-auto text-slate-400">{fmtB(k.used)} de {fmtB(k.total)} · livre {fmtB(k.free)}</span></div>
                  <div className="mt-1 h-2 rounded-full bg-white/5 overflow-hidden"><div className="h-full rounded-full" style={{ width: `${p}%`, background: tone(p, 80, 90) }} /></div>
                </div>);
            })}
            <dl className="grid grid-cols-2 gap-y-1 text-xs pt-1">
              <dt className="text-slate-500">Memória do backend</dt><dd className="text-right font-mono text-slate-200">{fmtB(now.proc?.rss)}</dd>
              <dt className="text-slate-500">CPU do backend</dt><dd className="text-right font-mono text-slate-200">{now.proc_cpu == null ? "—" : `${now.proc_cpu}% de 1 núcleo`}</dd>
              <dt className="text-slate-500">Threads / arquivos</dt><dd className="text-right font-mono text-slate-200">{now.proc?.threads ?? "—"} / {now.proc?.fds ?? "—"}</dd>
              <dt className="text-slate-500">Tarefas assíncronas</dt><dd className="text-right font-mono text-slate-200">{d.tasks}</dd>
            </dl>
          </section>
        </div>

        <section className="rounded-lg border border-line bg-panel p-4">
          <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2 mb-2"><Database className="w-4 h-4 text-slate-400" />Banco de dados (MongoDB)</h2>
          {d.mongo?.error ? <div className="text-xs text-red-300">{d.mongo.error}</div> : (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <dl className="grid grid-cols-2 gap-y-1 text-xs content-start">
                <dt className="text-slate-500">Resposta</dt><dd className="text-right font-mono" style={{ color: tone(d.mongo.ping_ms || 0, 30, 100) }}>{d.mongo.ping_ms} ms</dd>
                <dt className="text-slate-500">Dados / em disco</dt><dd className="text-right font-mono text-slate-200">{fmtB(d.mongo.data)} / {fmtB(d.mongo.storage)}</dd>
                <dt className="text-slate-500">Índices</dt><dd className="text-right font-mono text-slate-200">{fmtB(d.mongo.indexes)}</dd>
                <dt className="text-slate-500">Memória do Mongo</dt><dd className="text-right font-mono text-slate-200">{d.mongo.resident_mb != null ? `${d.mongo.resident_mb} MB` : "—"}</dd>
                <dt className="text-slate-500">Conexões</dt><dd className="text-right font-mono text-slate-200">{d.mongo.connections ?? "—"}</dd>
                <dt className="text-slate-500">Versão · ligado há</dt><dd className="text-right font-mono text-slate-200">{d.mongo.version} · {fmtDur(d.mongo.uptime_s)}</dd>
              </dl>
              <div className="md:col-span-2">
                <table className="w-full text-xs">
                  <thead><tr className="text-left text-slate-500"><th className="font-normal py-1">Maiores coleções</th><th className="font-normal text-right">Registros</th><th className="font-normal text-right">Em disco</th></tr></thead>
                  <tbody>{(d.collections || []).map(c => (
                    <tr key={c.name} className="border-t border-line/60"><td className="py-1 font-mono text-slate-300">{c.name}</td><td className="text-right font-mono text-slate-400">{(c.count ?? 0).toLocaleString("pt-BR")}</td><td className="text-right font-mono text-slate-200">{fmtB(c.storage)}</td></tr>))}
                  </tbody>
                </table>
              </div>
            </div>)}
        </section>
      </div>
    </div>
  );
}
