import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Mic, MicOff, X, Square, Settings2, MessageSquare, Send, Copy } from "lucide-react";
import { Wordmark } from "@/components/Brand";
import { copyText } from "@/lib/clipboard";

/* Conversa por voz com o assistente.
   Ouvir: reconhecimento de fala do navegador (Chrome, Edge, Safari) em pt-BR.
   Falar: síntese de voz do navegador. Nada de áudio sai do BastiON além do que o próprio navegador faz para reconhecer a fala.
   O microfone só existe em endereço seguro (https ou localhost). */

const SR = typeof window !== "undefined" ? (window.SpeechRecognition || window.webkitSpeechRecognition) : null;
const TTS = typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null;
const PREF_KEY = "bastion_voice_prefs";
const loadPrefs = () => { try { return { rate: 1.05, voice: "", ...JSON.parse(localStorage.getItem(PREF_KEY) || "{}") }; } catch { return { rate: 1.05, voice: "" }; } };

/** Texto do chat -> texto para ser falado (sem Markdown, sem blocos de comando). */
export function speakable(text) {
  let t = String(text || "");
  t = t.replace(/```[\s\S]*?```/g, " (os comandos estão na tela) ");
  t = t.replace(/`([^`]+)`/g, "$1").replace(/\*\*?([^*]+)\*\*?/g, "$1").replace(/^#{1,6}\s*/gm, "");
  t = t.replace(/^\s*[-•*]\s+/gm, "").replace(/\|/g, ", ").replace(/https?:\/\/\S+/g, "o link na tela");
  t = t.replace(/^⚠️\s*/, "").replace(/[_~>]/g, " ").replace(/\s+/g, " ").trim();
  return t;
}

/** Frases de até ~220 caracteres: o Chrome corta falas muito longas. */
export function chunks(text, max = 220) {
  const out = [];
  for (const s of text.split(/(?<=[.!?…:;])\s+/)) {
    let rest = s.trim();
    while (rest.length > max) {
      let cut = rest.lastIndexOf(",", max);
      if (cut < max * 0.4) cut = rest.lastIndexOf(" ", max);
      if (cut <= 0) cut = max;
      out.push(rest.slice(0, cut + 1).trim());
      rest = rest.slice(cut + 1).trim();
    }
    if (rest) out.push(rest);
  }
  return out;
}

function pickVoice(voices, wanted) {
  const pt = voices.filter(v => /^pt([-_]BR)?/i.test(v.lang));
  const br = pt.filter(v => /BR/i.test(v.lang));
  const pool = br.length ? br : pt;
  return pool.find(v => v.name === wanted)
    || pool.find(v => /natural|neural/i.test(v.name))
    || pool.find(v => /google/i.test(v.name))
    || pool.find(v => /francisca|luciana|maria|antonio/i.test(v.name))
    || pool[0] || null;
}

/** Esfera de pontos ligados (rede): gira devagar parada, acelera pensando e pulsa falando/ouvindo. */
function Orb({ state, level }) {
  const ref = useRef(null);
  const st = useRef({ state, level });
  st.current = { state, level };
  useEffect(() => {
    const cv = ref.current; if (!cv) return undefined;
    const ctx = cv.getContext("2d");
    const N = 420, pts = [];
    for (let i = 0; i < N; i++) {                     // espiral de Fibonacci: pontos bem distribuídos
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), a = i * Math.PI * (3 - Math.sqrt(5));
      pts.push([Math.cos(a) * r, y, Math.sin(a) * r, Math.random() * 6.28]);
    }
    const links = [];
    for (let i = 0; i < N; i += 3) {                  // cada ponto ligado ao vizinho mais próximo
      let best = -1, bd = 9;
      for (let j = 0; j < N; j++) { if (j === i) continue; const d = (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2 + (pts[i][2] - pts[j][2]) ** 2; if (d < bd) { bd = d; best = j; } }
      links.push([i, best]);
    }
    const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    let raf, rot = 0, amp = 0, t0 = performance.now();
    const draw = (now) => {
      const dt = Math.min(50, now - t0); t0 = now;
      const { state: s, level: lv } = st.current;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const W = cv.clientWidth, H = cv.clientHeight;
      if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const speed = calm ? 0.00008 : s === "thinking" ? 0.0011 : s === "speaking" ? 0.00045 : s === "listening" ? 0.00035 : 0.00018;
      rot += dt * speed;
      const target = s === "speaking" ? 0.5 + 0.5 * lv : s === "listening" ? 0.25 + 0.6 * lv : s === "thinking" ? 0.3 : 0.05;
      amp += (target - amp) * 0.12;
      const R = Math.min(W, H) * 0.36, cx = W / 2, cy = H / 2;
      const cosr = Math.cos(rot), sinr = Math.sin(rot), tilt = 0.35, ct = Math.cos(tilt), sn = Math.sin(tilt);
      const hue = s === "listening" ? [60, 196, 141] : s === "thinking" ? [226, 181, 79] : [127, 173, 235];
      const P = pts.map(([x, y, z, ph]) => {
        const k = 1 + (calm ? 0 : amp * 0.11 * Math.sin(now * 0.004 + ph));
        const x1 = (x * cosr + z * sinr) * k, z1 = (-x * sinr + z * cosr) * k, y0 = y * k;
        const y1 = y0 * ct - z1 * sn, z2 = y0 * sn + z1 * ct;
        const f = 1 / (1.9 - z2 * 0.55);
        return [cx + x1 * R * f * 1.5, cy + y1 * R * f * 1.5, z2];
      });
      const g = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * 1.7);
      g.addColorStop(0, `rgba(${hue},${0.10 + amp * 0.10})`); g.addColorStop(1, `rgba(${hue},0)`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      ctx.lineWidth = 0.6;
      for (const [a, b] of links) {
        const za = (P[a][2] + P[b][2]) / 2;
        ctx.strokeStyle = `rgba(${hue},${0.05 + (za + 1) * 0.09})`;
        ctx.beginPath(); ctx.moveTo(P[a][0], P[a][1]); ctx.lineTo(P[b][0], P[b][1]); ctx.stroke();
      }
      for (const [x, y, z] of P) {
        ctx.fillStyle = `rgba(${hue},${0.18 + (z + 1) * 0.36})`;
        ctx.beginPath(); ctx.arc(x, y, 0.7 + (z + 1) * 0.75, 0, 6.283); ctx.fill();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className="w-full h-full block" aria-hidden="true" />;
}

const LABEL = { idle: "Toque no microfone para falar", listening: "Ouvindo…", thinking: "Pensando…", speaking: "Falando…" };

export function VoiceMode({ conv, send, status, onClose, onShowChat }) {
  const [state, setState] = useState("idle");          // idle | listening | thinking | speaking
  const [heard, setHeard] = useState("");               // o que o reconhecimento está entendendo
  const [said, setSaid] = useState("");                 // última resposta falada
  const [muted, setMuted] = useState(false);            // microfone pausado pelo usuário
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [prefs, setPrefs] = useState(loadPrefs);
  const [voices, setVoices] = useState([]);
  const [cfg, setCfg] = useState(false);
  const [typed, setTyped] = useState("");
  const rec = useRef(null);
  const alive = useRef(true);
  const want = useRef(false);                           // deve estar ouvindo
  const spoken = useRef(null);                          // id da última resposta já falada
  const pending = useRef(false);                        // pergunta enviada, resposta ainda não começou
  const silent = useRef(0);                             // vezes seguidas que o microfone fechou sem ouvir nada
  const sendRef = useRef(send); sendRef.current = send;
  const ask = useCallback(async (text) => {
    pending.current = true; setHeard(text); setState("thinking");
    const ok = await sendRef.current(text, { voice: true });
    if (!ok && alive.current) { pending.current = false; setState("idle"); setErr("Não consegui enviar a pergunta. Tente de novo."); }
  }, []);
  const stateRef = useRef(state); stateRef.current = state;
  const mutedRef = useRef(muted); mutedRef.current = muted;
  const secure = typeof window !== "undefined" && window.isSecureContext;
  const canListen = !!SR && secure;

  useEffect(() => { try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch { /* sem armazenamento */ } }, [prefs]);
  useEffect(() => {
    if (!TTS) return undefined;
    const on = () => setVoices(TTS.getVoices());
    on(); TTS.addEventListener?.("voiceschanged", on);
    return () => TTS.removeEventListener?.("voiceschanged", on);
  }, []);
  const voice = useMemo(() => pickVoice(voices, prefs.voice), [voices, prefs.voice]);
  const ptVoices = useMemo(() => voices.filter(v => /^pt/i.test(v.lang)), [voices]);

  const stopListening = useCallback(() => {
    want.current = false;
    try { rec.current?.abort(); } catch { /* já parado */ }
    rec.current = null;
  }, []);

  const listen = useCallback(() => {
    if (!canListen || mutedRef.current || !alive.current) { setState("idle"); return; }
    try { rec.current?.abort(); } catch { /* ok */ }
    const r = new SR();
    r.lang = "pt-BR"; r.interimResults = true; r.continuous = false; r.maxAlternatives = 1;
    let final = "";
    want.current = true;
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const tx = e.results[i][0].transcript;
        if (e.results[i].isFinal) final += tx; else interim += tx;
      }
      setHeard((final + " " + interim).trim());
      setLevel(Math.min(1, 0.35 + (interim.length % 9) / 12));
    };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") { want.current = false; setErr("O navegador bloqueou o microfone. Libere o microfone para este site (cadeado na barra de endereço) e toque de novo."); }
      else if (e.error === "audio-capture") { want.current = false; setErr("Nenhum microfone encontrado neste aparelho."); }
      else if (e.error === "network") { want.current = false; setErr("O reconhecimento de fala do navegador precisa de internet e não respondeu."); }
    };
    r.onend = () => {
      if (rec.current !== r || !alive.current) return;
      rec.current = null;
      const text = final.trim();
      if (text) { want.current = false; silent.current = 0; ask(text); return; }
      silent.current += 1;                              // silêncio: tenta mais duas vezes e depois espera um toque
      if (want.current && !mutedRef.current && stateRef.current === "listening" && silent.current < 3) setTimeout(() => want.current && listen(), 250);
      else { want.current = false; setState("idle"); }
    };
    rec.current = r;
    setErr(""); setHeard(""); setState("listening");
    try { r.start(); } catch { setState("idle"); }
  }, [canListen, ask]);

  const stopSpeaking = useCallback(() => { try { TTS?.cancel(); } catch { /* ok */ } }, []);

  const speak = useCallback((text) => {
    const clean = speakable(text);
    setSaid(clean);
    if (!TTS || !clean) { listen(); return; }
    stopListening(); TTS.cancel();
    const parts = chunks(clean);
    setState("speaking");
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p);
      u.lang = voice?.lang || "pt-BR"; if (voice) u.voice = voice;
      u.rate = prefs.rate; u.pitch = 1;
      u.onboundary = () => setLevel(0.4 + Math.random() * 0.6);
      const done = () => { if (i === parts.length - 1 && alive.current && stateRef.current === "speaking") { setLevel(0); listen(); } };
      u.onend = done; u.onerror = done;
      TTS.speak(u);
    });
  }, [voice, prefs.rate, listen, stopListening]);

  // abre: o que já estava na conversa não é lido; começa ouvindo
  useEffect(() => {
    alive.current = true;
    const last = [...conv.items].reverse().find(i => i.kind === "assistant" || i.kind === "error");
    spoken.current = last?.id ?? null;
    if (canListen) listen();
    return () => { alive.current = false; stopListening(); stopSpeaking(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // resposta nova do assistente -> fala
  useEffect(() => {
    if (conv.busy) { pending.current = false; if (stateRef.current !== "speaking") setState("thinking"); return; }
    const last = [...conv.items].reverse().find(i => i.kind === "assistant" || i.kind === "error");
    if (last && last.id !== spoken.current) { pending.current = false; spoken.current = last.id; speak(last.kind === "error" ? `Não consegui. ${last.text}` : last.text); }
    else if (stateRef.current === "thinking" && !pending.current) listen();
  }, [conv.busy, conv.items, speak, listen]);

  useEffect(() => {
    const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", esc, true);
    return () => window.removeEventListener("keydown", esc, true);
  }, [onClose]);

  const tapOrb = () => {
    silent.current = 0;
    if (state === "speaking") { stopSpeaking(); setMuted(false); mutedRef.current = false; listen(); }
    else if (state === "listening") { setMuted(true); mutedRef.current = true; stopListening(); setState("idle"); }
    else if (state === "idle") { setMuted(false); mutedRef.current = false; listen(); }
  };
  const toggleMic = () => {
    silent.current = 0;
    if (muted || state === "idle") { setMuted(false); mutedRef.current = false; if (state !== "thinking" && state !== "speaking") listen(); }
    else { setMuted(true); mutedRef.current = true; stopListening(); if (state === "listening") setState("idle"); }
  };
  const sendTyped = () => { const t = typed.trim(); if (!t || conv.busy) return; setTyped(""); ask(t); };

  const tool = conv.busy ? [...conv.items].reverse().find(i => i.kind === "tool" || i.kind === "wait")?.text : "";
  const hasProposal = conv.items.some(i => i.kind === "proposal" && i.status === "pending");
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  return (
    <div className="fixed inset-0 z-[70] bg-canvas flex flex-col pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]" role="dialog" aria-label="Conversa por voz com o assistente" data-testid="voice-mode">
      <div className="flex items-center gap-3 px-4 h-14 shrink-0">
        <Wordmark className="text-lg" />
        <span className="text-xs text-slate-500 truncate">voz · {status?.provider} {status?.model}</span>
        <button onClick={() => setCfg(!cfg)} className="ml-auto w-9 h-9 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-100 hover:bg-white/5" title="Voz e velocidade" aria-label="Voz e velocidade" data-testid="voice-settings"><Settings2 className="w-4 h-4" /></button>
        <button onClick={onShowChat} className="w-9 h-9 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-100 hover:bg-white/5" title="Ver a conversa escrita" aria-label="Ver a conversa escrita" data-testid="voice-show-chat"><MessageSquare className="w-4 h-4" /></button>
        <button onClick={onClose} className="w-9 h-9 rounded-md flex items-center justify-center text-slate-400 hover:text-slate-100 hover:bg-white/5" title="Fechar (Esc)" aria-label="Fechar conversa por voz" data-testid="voice-close"><X className="w-5 h-5" /></button>
      </div>

      {cfg && (
        <div className="mx-4 mb-2 p-3 rounded-lg border border-line bg-surface text-sm flex flex-wrap items-center gap-x-5 gap-y-2" data-testid="voice-config">
          <label className="flex items-center gap-2 text-slate-300">Voz
            <select value={voice?.name || ""} onChange={e => setPrefs({ ...prefs, voice: e.target.value })} className="bg-sunken border border-line rounded-md px-2 py-1 text-slate-100 max-w-[240px]">
              {ptVoices.length === 0 && <option value="">voz padrão do aparelho</option>}
              {ptVoices.map(v => <option key={v.name} value={v.name}>{v.name}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 text-slate-300">Velocidade
            <input type="range" min="0.8" max="1.5" step="0.05" value={prefs.rate} onChange={e => setPrefs({ ...prefs, rate: Number(e.target.value) })} />
            <span className="text-slate-400 tabular-nums w-10">{prefs.rate.toFixed(2)}×</span>
          </label>
          <button onClick={() => speak("Esta é a voz do assistente do BastiON.")} className="text-brand-soft hover:underline">testar</button>
        </div>
      )}

      <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-5">
        <button onClick={tapOrb} disabled={!canListen && state !== "speaking"} className="w-[min(70vw,46vh,420px)] aspect-square rounded-full focus-visible:outline-offset-8 disabled:cursor-default"
                aria-label={state === "speaking" ? "Interromper e falar" : state === "listening" ? "Pausar o microfone" : "Falar"} data-testid="voice-orb">
          <Orb state={state} level={level} />
        </button>
        <div className="mt-2 text-base text-slate-200" data-testid="voice-state" aria-live="polite">{LABEL[state]}</div>
        {tool && <div className="mt-1 text-xs font-mono text-slate-500 max-w-xl truncate" data-testid="voice-tool">{tool}</div>}
        <div className="mt-4 w-full max-w-2xl text-center space-y-2 min-h-[5.5rem]">
          {heard && <div className="text-sm text-slate-400" data-testid="voice-heard">“{heard}”</div>}
          {said && state !== "listening" && <div className="text-[15px] leading-relaxed text-slate-100 line-clamp-5" data-testid="voice-said">{said}</div>}
        </div>
        {hasProposal && (
          <button onClick={onShowChat} className="mt-3 text-sm px-3 py-2 rounded-md border border-amber-400/40 bg-amber-400/10 text-amber-200" data-testid="voice-pending">
            Há uma alteração esperando o seu clique em Confirmar — abrir a conversa
          </button>
        )}
        {err && <div className="mt-3 text-sm text-amber-300 max-w-xl text-center" role="alert" data-testid="voice-error">{err}</div>}
        {!canListen && (
          <div className="mt-3 max-w-xl text-sm text-slate-300 border border-line bg-surface rounded-lg p-3 space-y-2" data-testid="voice-nomic">
            {!secure ? (
              <>
                <div className="text-amber-300">O navegador só libera o microfone em endereço seguro (https). Você abriu o BastiON por {origin}.</div>
                <div>Para testar agora no Chrome ou Edge do computador: abra <span className="font-mono text-slate-100">chrome://flags/#unsafely-treat-insecure-origin-as-secure</span>, cole o endereço abaixo, marque Enabled e reinicie o navegador.</div>
                <button onClick={() => copyText(origin)} className="inline-flex items-center gap-1.5 font-mono text-brand-soft hover:underline"><Copy className="w-3.5 h-3.5" />{origin}</button>
                <div className="text-slate-400">Para valer em todo aparelho (e no celular), ligue o HTTPS do BastiON — README, seção 21.</div>
              </>
            ) : (
              <div className="text-amber-300">Este navegador não tem reconhecimento de fala. Use o Chrome, o Edge ou o Safari.</div>
            )}
            <div className="text-slate-400">Enquanto isso, escreva aqui e eu respondo falando:</div>
            <div className="flex gap-2">
              <input value={typed} onChange={e => setTyped(e.target.value)} onKeyDown={e => e.key === "Enter" && sendTyped()} placeholder="Pergunte algo…"
                     className="flex-1 bg-sunken border border-line rounded-md px-3 py-2 text-slate-100 focus:outline-none focus:border-brand" data-testid="voice-typed" />
              <button onClick={sendTyped} disabled={!typed.trim() || conv.busy} className="w-10 rounded-md bg-brand hover:bg-brand-strong text-white flex items-center justify-center disabled:opacity-40" aria-label="Enviar"><Send className="w-4 h-4" /></button>
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 flex items-center justify-center gap-4 pb-6 pt-2">
        {state === "speaking" && (
          <button onClick={() => { stopSpeaking(); listen(); }} className="h-12 px-4 rounded-full border border-line2 text-slate-200 hover:bg-white/5 flex items-center gap-2 text-sm" data-testid="voice-stop">
            <Square className="w-4 h-4" /> Parar de falar
          </button>
        )}
        {canListen && (
          <button onClick={toggleMic} className={`w-14 h-14 rounded-full flex items-center justify-center transition-colors ${muted || state === "idle" ? "bg-white/10 text-slate-300 hover:bg-white/15" : "bg-on/20 text-on border border-on/40"}`}
                  aria-label={muted || state === "idle" ? "Ligar o microfone" : "Pausar o microfone"} aria-pressed={!(muted || state === "idle")} data-testid="voice-mic">
            {muted || state === "idle" ? <MicOff className="w-6 h-6" /> : <Mic className="w-6 h-6" />}
          </button>
        )}
      </div>
    </div>
  );
}
