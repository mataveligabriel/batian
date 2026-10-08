import React, { useEffect, useRef, useState } from "react";

// Página pública do Looking Glass: sem login. Fala só com /api/public/lg da própria origem (porta dedicada).
const call = async (path, body) => {
  const r = await fetch(`/api/public/lg${path}`, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  let data = null;
  try { data = await r.json(); } catch { /* resposta sem JSON */ }
  if (!r.ok) throw new Error((data && typeof data.detail === "string" && data.detail) || (r.status === 429 ? "Muitas consultas. Aguarde um pouco." : "Não foi possível consultar agora."));
  return data;
};
const HINT = { ping: "ex.: 8.8.8.8 ou 2001:4860:4860::8888", trace: "ex.: 1.1.1.1", route: "ex.: 200.160.0.0/20 ou 8.8.8.8" };

export default function PublicLookingGlass() {
  const [info, setInfo] = useState(null);
  const [router, setRouter] = useState("");
  const [query, setQuery] = useState("ping");
  const [target, setTarget] = useState("");
  const [v6, setV6] = useState(false);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState([]);
  const [err, setErr] = useState("");
  const seq = useRef(0);

  useEffect(() => {
    call("").then(d => { setInfo(d); if (d.enabled) { document.title = d.title; setRouter(d.routers[0]?.id || ""); setQuery(d.queries[0]?.key || "ping"); } })
      .catch(() => setInfo({ enabled: false, down: true }));
  }, []);
  const qdef = info?.queries?.find(q => q.key === query);
  const run = async (e) => {
    e.preventDefault(); setErr("");
    if (qdef?.needs_target && !target.trim()) return setErr("Informe o endereço de destino.");
    const r = info.routers.find(x => x.id === router);
    const key = ++seq.current;
    setBusy(true);
    setResults(rs => [{ key, router: r?.name, label: qdef.label, target: qdef.needs_target ? target.trim() : (v6 ? "IPv6" : "IPv4"), loading: true }, ...rs].slice(0, 10));
    try {
      const res = await call("/query", { router, query, target: target.trim(), v6 });
      setResults(rs => rs.map(x => x.key === key ? { ...x, ...res, loading: false } : x));
    } catch (ex) {
      setResults(rs => rs.filter(x => x.key !== key)); setErr(ex.message);
    } finally { setBusy(false); }
  };

  const wrap = "min-h-screen bg-canvas text-slate-200 flex flex-col";
  if (!info) return <div className={wrap}><div className="m-auto text-slate-500 text-sm">carregando…</div></div>;
  if (!info.enabled || !info.routers.length) return (
    <div className={wrap} data-testid="plg-off"><div className="m-auto text-center px-6">
      <div className="text-xl text-slate-100 font-semibold">Looking Glass</div>
      <div className="text-sm text-slate-400 mt-2">{info.down ? "Serviço indisponível no momento." : "Este Looking Glass não está disponível ao público."}</div>
    </div></div>
  );
  return (
    <div className={wrap} data-testid="plg-page">
      <header className="border-b border-line bg-panel">
        <div className="max-w-4xl mx-auto px-4 py-5">
          <h1 className="text-2xl font-semibold text-slate-100" data-testid="plg-title">{info.title}</h1>
          <p className="text-sm text-slate-400 mt-1">Ping, traceroute e consulta de rota BGP a partir dos roteadores desta rede.</p>
        </div>
      </header>
      <main className="max-w-4xl mx-auto px-4 py-6 w-full flex-1">
        <form onSubmit={run} className="border border-line rounded-lg bg-surface p-4 md:p-5 space-y-4">
          <div>
            <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Roteador</div>
            <div className="flex flex-wrap gap-2">
              {info.routers.map(r => (
                <button type="button" key={r.id} onClick={() => setRouter(r.id)} aria-pressed={router === r.id} data-testid={`plg-router-${r.id}`}
                  className={`px-3 py-1.5 rounded-md border text-sm ${router === r.id ? "border-brand bg-brand/10 text-slate-100" : "border-line text-slate-400 hover:text-slate-200"}`}>{r.name}</button>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Consulta</div>
              <div className="flex rounded-md border border-line overflow-hidden">
                {info.queries.map(q => (
                  <button type="button" key={q.key} onClick={() => setQuery(q.key)} data-testid={`plg-q-${q.key}`}
                    className={`px-3 h-9 text-sm border-r border-line last:border-r-0 ${query === q.key ? "bg-brand text-white" : "bg-sunken text-slate-400 hover:text-slate-200"}`}>{q.label}</button>
                ))}
              </div>
            </div>
            {qdef?.needs_target ? (
              <div className="flex-1 min-w-[220px]">
                <label htmlFor="plg-target" className="block text-xs uppercase tracking-wider text-slate-400 mb-2">Destino (IP{qdef.allow_prefix ? " ou prefixo" : ""})</label>
                <input id="plg-target" value={target} onChange={e => setTarget(e.target.value)} placeholder={HINT[query]} spellCheck={false} autoComplete="off" maxLength={64} data-testid="plg-target"
                  className="w-full h-9 rounded-md bg-sunken border border-line px-3 font-mono text-sm text-slate-100 focus:outline-none focus:border-brand" />
              </div>
            ) : <label className="flex items-center gap-2 h-9 text-sm text-slate-300 cursor-pointer"><input type="checkbox" checked={v6} onChange={e => setV6(e.target.checked)} />IPv6</label>}
            <button type="submit" disabled={busy || !router} data-testid="plg-run" className="h-9 px-5 rounded-md bg-brand hover:bg-brand-strong text-white text-sm disabled:opacity-50">{busy ? "Consultando…" : "Consultar"}</button>
          </div>
          {err && <div className="text-sm text-red-400" role="alert" data-testid="plg-error">{err}</div>}
          <div className="text-[11px] text-slate-500">Somente endereços públicos da internet. Limite de {info.per_min} consultas por minuto por visitante; uma consulta por vez em cada roteador.</div>
        </form>
        <div className="space-y-3 mt-5">
          {results.map(r => (
            <div key={r.key} className="border border-line rounded-lg bg-surface overflow-hidden" data-testid="plg-result">
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-line text-sm flex-wrap">
                <span className={`w-2 h-2 rounded-full ${r.loading ? "bg-amber-400 animate-pulse" : r.ok ? "bg-on" : "bg-red-500"}`} />
                <span className="text-slate-100 font-medium">{r.router}</span><span className="text-slate-500">· {r.label} {r.target}</span>
                <span className="ml-auto text-[11px] font-mono text-slate-500">{r.loading ? "aguardando o roteador…" : r.seconds != null ? `${r.seconds} s` : ""}</span>
              </div>
              {!r.loading && r.error && <div className="px-4 py-2 text-sm text-red-400">{r.error}</div>}
              {!r.loading && r.output && <pre className="px-4 py-3 text-[12px] leading-relaxed font-mono text-slate-200 bg-sunken overflow-x-auto max-h-[60vh] whitespace-pre" data-testid="plg-output">{r.output}</pre>}
              {!r.loading && !r.error && !r.output && <div className="px-4 py-3 text-xs text-slate-500 font-mono">sem saída</div>}
            </div>
          ))}
        </div>
      </main>
      <footer className="border-t border-line text-[11px] text-slate-500">
        <div className="max-w-4xl mx-auto px-4 py-3 flex flex-wrap gap-x-4">{info.contact && <span>Contato: {info.contact}</span>}<span className="ml-auto">Powered by Basti<span className="text-on">ON</span></span></div>
      </footer>
    </div>
  );
}
