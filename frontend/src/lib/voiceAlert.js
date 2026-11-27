// Alerta falado dos mapas de disponibilidade: som de alarme + voz (síntese de voz do navegador/Windows).
// A voz sai do computador que está com o mapa aberto (ex.: a TV do NOC), então a lista de vozes é a dele.
import { useEffect, useRef } from "react";

export const VOICE_DEFAULT = {
  enabled: false, voice: "", pitch: 0.4, rate: 0.92, volume: 1, intro: "beeps", repeat: 1, say_name: true, warn: false,
  msg_device: "Alerta de falha. Equipamento indisponível.",
  msg_link: "Alerta de falha. Possível rompimento.",
  msg_many: "Alerta de falha. {n} equipamentos indisponíveis.",
  say_up: true, msg_device_up: "Equipamento restabelecido.", msg_link_up: "Enlace normalizado.",
  engine: "browser", server_voice: "faber",     // server = voz gerada no BastiON (igual em PC, celular e TV)
};

export const SERVER_VOICES = [
  { key: "faber", label: "Faber (masculina)" }, { key: "cadu", label: "Cadu (masculina)" }, { key: "jeff", label: "Jeff (masculina)" },
];

// quem busca o áudio da voz do servidor (a página logada usa a API com login; o link público usa o token)
let ttsSource = null;
export function setTtsSource(fn) { ttsSource = fn; }

// "várias formas" de voz grave: só muda tom e velocidade da voz escolhida
export const VOICE_PRESETS = [
  { key: "grave", label: "Grave", pitch: 0.4, rate: 0.92 },
  { key: "muito", label: "Muito grave", pitch: 0.1, rate: 0.88 },
  { key: "locutor", label: "Locutor", pitch: 0.65, rate: 1.0 },
  { key: "robo", label: "Robô lento", pitch: 0.2, rate: 0.75 },
  { key: "urgente", label: "Urgente", pitch: 0.5, rate: 1.18 },
];

export const INTROS = [
  { key: "beeps", label: "Bipes de aviso (padrão)" },
  { key: "siren", label: "Sirene" },
  { key: "klaxon", label: "Alarme (dois tons)" },
  { key: "radar", label: "Bipes de radar" },
  { key: "none", label: "Sem som, só a voz" },
];

const TTS = typeof window !== "undefined" && "speechSynthesis" in window ? window.speechSynthesis : null;
export const ttsSupported = !!TTS;

const MALE = /antonio|daniel|donato|fabio|f[aá]bio|humberto|julio|j[uú]lio|nicolau|valerio|val[eé]rio|ricardo|duarte|cristiano|male|mascul|homem/i;
const FEMALE = /francisca|maria|helo[ií]sa|luciana|thalita|leila|let[ií]cia|manuela|yara|giovanna|elza|brenda|raquel|female|femin|mulher|google portugu/i;

export function guessGender(v) { return MALE.test(v.name) ? "m" : FEMALE.test(v.name) ? "f" : ""; }

/** Vozes em português primeiro (pt-BR, depois pt-PT), as masculinas e as "Natural/Online" (neurais) no topo. */
export function listVoices() {
  if (!TTS) return [];
  const score = (v) => (/^pt-br/i.test(v.lang) ? 0 : /^pt/i.test(v.lang) ? 10 : 50)
    + (guessGender(v) === "m" ? 0 : guessGender(v) === "f" ? 4 : 2) + (/natural|online|neural/i.test(v.name) ? 0 : 1);
  return [...TTS.getVoices()].sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
}

// voz que falhou neste aparelho (ex.: voz online do Edge sem internet): não tenta de novo nesta sessão
const failed = new Set();
const resolved = new Map();     // nome pedido -> voz usada (mesma voz sempre, nesta página)

/** Sempre a mesma voz: a escolhida; se não existir aqui, a masculina pt-BR local (do próprio Windows/aparelho). */
export function pickVoice(name) {
  const all = listVoices().filter(v => !failed.has(v.name));
  if (!all.length) return null;
  const keep = resolved.get(name || "");
  if (keep && all.includes(keep)) return keep;
  const low = String(name || "").toLowerCase();
  const pt = all.filter(v => /^pt/i.test(v.lang));
  const br = pt.filter(v => /^pt-br/i.test(v.lang.replace("_", "-")));
  const male = (l) => l.filter(v => guessGender(v) === "m");
  const v = (name && (all.find(x => x.name === name) || all.find(x => x.name.toLowerCase() === low)))
    || male(br).find(x => x.localService) || male(br)[0] || male(pt).find(x => x.localService) || male(pt)[0]
    || br.find(x => x.localService) || br[0] || pt[0] || null;
  if (v) resolved.set(name || "", v);
  return v;
}

let voicesReady = null;
/** Espera a lista de vozes carregar (no Chrome ela chega depois): sem isso a primeira fala sai com a voz padrão. */
export function waitVoices() {
  if (!TTS) return Promise.resolve([]);
  if (TTS.getVoices().length) return Promise.resolve(TTS.getVoices());
  voicesReady = voicesReady || new Promise(resolve => {
    const done = () => { if (TTS.getVoices().length) { TTS.removeEventListener?.("voiceschanged", done); resolve(TTS.getVoices()); } };
    TTS.addEventListener?.("voiceschanged", done);
    setTimeout(() => resolve(TTS.getVoices()), 3000);
  }).then(v => { voicesReady = null; return v; });
  return voicesReady;
}
if (TTS) waitVoices();

const live = new Set();         // segura a fala na memória (Chrome às vezes descarta e corta no meio)

let actx = null;
function audioCtx() {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    if (actx.state === "suspended") actx.resume();
    return actx;
  } catch { return null; }
}

/** Toca o som de abertura; resolve quando termina. */
export function playIntro(kind, volume = 1) {
  const ctx = kind && kind !== "none" ? audioCtx() : null;
  if (!ctx) return Promise.resolve();
  const t0 = ctx.currentTime + 0.05, vol = 0.22 * Math.max(0, Math.min(1, volume));
  const tone = (type, f0, f1, start, dur) => {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, start);
    if (f1 !== f0) o.frequency.linearRampToValueAtTime(f1, start + dur);
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(vol, start + 0.02);
    g.gain.setValueAtTime(vol, start + dur - 0.04);
    g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
    o.connect(g).connect(ctx.destination);
    o.start(start); o.stop(start + dur + 0.02);
  };
  let total = 0;
  if (kind === "up") {             // resolvido: dois tons subindo, suave
    tone("sine", 660, 660, t0, 0.16); tone("sine", 988, 988, t0 + 0.18, 0.3);
    total = 0.55;
  } else if (kind === "beeps") {          // sinal de "vai avisar um evento": 3 bipes subindo, pausa, bipe longo
    [[880, 0, 0.13], [988, 0.2, 0.13], [1175, 0.4, 0.13], [1175, 0.72, 0.38]].forEach(([f, s, d]) => tone("sine", f, f, t0 + s, d));
    total = 1.15;
  } else if (kind === "siren") {          // sobe e desce, 3 vezes
    for (let i = 0; i < 3; i++) { tone("sawtooth", 520, 1150, t0 + i * 0.7, 0.35); tone("sawtooth", 1150, 520, t0 + i * 0.7 + 0.35, 0.35); }
    total = 2.1;
  } else if (kind === "klaxon") {  // dois tons, tipo alarme de painel
    for (let i = 0; i < 4; i++) tone("square", i % 2 ? 660 : 880, i % 2 ? 660 : 880, t0 + i * 0.32, 0.28);
    total = 1.3;
  } else if (kind === "radar") {   // bipes curtos
    for (let i = 0; i < 4; i++) tone("sine", 1250, 1250, t0 + i * 0.22, 0.12);
    total = 0.95;
  }
  return new Promise(r => setTimeout(r, total * 1000 + 150));
}

async function sayOnce(text, cfg, retry = true) {
  if (!TTS || !text) return;
  await waitVoices();
  const v = pickVoice(cfg.voice);
  const ok = await new Promise(resolve => {
    const u = new SpeechSynthesisUtterance(text);
    live.add(u);
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "pt-BR";
    u.pitch = Math.max(0, Math.min(2, Number(cfg.pitch ?? 0.4)));
    u.rate = Math.max(0.5, Math.min(1.6, Number(cfg.rate ?? 0.92)));
    u.volume = Math.max(0, Math.min(1, Number(cfg.volume ?? 1)));
    let started = false;
    const done = (res) => { clearTimeout(guard); live.delete(u); resolve(res); };
    const guard = setTimeout(() => done(true), 20000);          // alguns navegadores não disparam onend
    u.onstart = () => { started = true; };
    u.onend = () => done(true);
    u.onerror = (e) => done(started || e?.error === "interrupted" || e?.error === "canceled");
    TTS.speak(u);
  });
  // a voz escolhida falhou (ex.: voz online sem internet): marca e fala com a masculina local, sempre a mesma daí pra frente
  if (!ok && retry && v) { failed.add(v.name); resolved.clear(); await sayOnce(text, cfg, false); }
}

// nomes como "SW-Link_NET_Divino" falados direito
export const spoken = (s) => String(s || "").replace(/[_]+/g, " ").replace(/(\w)-(\w)/g, "$1 $2").replace(/\s+/g, " ").trim();

// voz do servidor: o tom "mais grave" é feito tocando o áudio um pouco mais devagar
const pitchToPlayback = (p) => Math.max(0.7, Math.min(1.25, 1 - (1 - Number(p ?? 0.4)) * 0.25));
const bufCache = new Map();
async function sayServer(text, cfg) {
  const ctx = audioCtx();
  if (!ttsSource || !ctx) throw new Error("sem fonte");
  const key = `${cfg.server_voice}|${cfg.rate}|${text}`;
  let buf = bufCache.get(key);
  if (!buf) {
    const raw = await ttsSource(text, cfg);
    buf = await ctx.decodeAudioData(raw);
    if (bufCache.size > 60) bufCache.delete(bufCache.keys().next().value);
    bufCache.set(key, buf);
  }
  await new Promise(resolve => {
    const src = ctx.createBufferSource(), g = ctx.createGain();
    src.buffer = buf; src.playbackRate.value = pitchToPlayback(cfg.pitch);
    g.gain.value = Math.max(0, Math.min(1, Number(cfg.volume ?? 1)));
    src.connect(g).connect(ctx.destination);
    src.onended = resolve;
    src.start();
    setTimeout(resolve, (buf.duration / src.playbackRate.value) * 1000 + 1500);
  });
}

async function say(text, cfg) {
  if (cfg.engine === "server") {
    try { await sayServer(text, cfg); return; } catch { /* servidor sem voz: usa a do aparelho */ }
  }
  await sayOnce(text, cfg);
}

let queue = Promise.resolve();
/** Enfileira um alerta (não fala por cima do anterior). */
export function speakAlert(cfg, text, up = false) {
  queue = queue.then(async () => {
    await playIntro(up ? "up" : cfg.intro, cfg.volume);
    const n = up ? 1 : Math.max(1, Math.min(3, Number(cfg.repeat) || 1));
    for (let i = 0; i < n; i++) await say(text, cfg);
  }).catch(() => {});
  return queue;
}

/**
 * Celular/TV: o navegador só deixa tocar som e falar depois que a PRIMEIRA fala acontece dentro de um toque.
 * Chame isto no clique do botão "Ativar alerta por voz": acorda o áudio, toca o bipe e fala uma frase curta.
 * Devolve "ok", "sem-voz" (aparelho sem síntese de voz: só os bipes) ou "falhou".
 */
export function primeAudio(cfg, quiet = false) {
  const c = { ...VOICE_DEFAULT, ...(cfg || {}) };
  const ctx = audioCtx();
  try {          // bipe curto dentro do toque (iOS/Android liberam o AudioContext aqui)
    if (ctx && !quiet) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 1175; g.gain.value = 0.15 * (c.volume ?? 1);
      o.connect(g).connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.12);
    }
  } catch { /* ok */ }
  if (!TTS) return Promise.resolve(ctx ? "sem-voz" : "falhou");
  try { TTS.cancel(); TTS.resume?.(); } catch { /* ok */ }
  const u = new SpeechSynthesisUtterance(quiet ? " " : "Alerta por voz ativado.");
  const v = pickVoice(c.voice);
  if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "pt-BR";
  u.pitch = Number(c.pitch ?? 0.4); u.rate = Number(c.rate ?? 0.92); u.volume = quiet ? 0 : Number(c.volume ?? 1);
  return new Promise(resolve => {
    let started = false;
    u.onstart = () => { started = true; };
    u.onend = () => resolve("ok");
    u.onerror = () => resolve(started ? "ok" : "falhou");
    setTimeout(() => resolve(started ? "ok" : listVoices().length ? "falhou" : "sem-voz"), 4000);
    TTS.speak(u);              // tem que ser chamado direto no clique, sem await antes
  });
}

/** O navegador já deixa tocar som sem toque? (TV/quiosque com som liberado, app do Windows, site com "Som: permitir") */
export function autoplayAllowed() {
  try {
    const p = navigator.getAutoplayPolicy?.("audiocontext");
    if (p) return p === "allowed";
  } catch { /* ok */ }
  const ctx = audioCtx();
  return !!ctx && ctx.state === "running";
}

/** Libera o som no primeiro toque/tecla em QUALQUER lugar da página (arrastar o mapa, zoom...), sem botão. */
export function useAutoUnlock(enabled, cfg, onUnlocked) {
  const cfgRef = useRef(cfg); cfgRef.current = cfg;
  useEffect(() => {
    if (!enabled) return undefined;
    if (autoplayAllowed()) { onUnlocked(); return undefined; }
    const on = () => { cleanup(); primeAudio(cfgRef.current, true).then(onUnlocked); };
    const evs = ["pointerdown", "touchstart", "keydown"];
    const cleanup = () => evs.forEach(e => window.removeEventListener(e, on, true));
    evs.forEach(e => window.addEventListener(e, on, { capture: true, passive: true }));
    return cleanup;
  }, [enabled]); // eslint-disable-line react-hooks/exhaustive-deps
}

// Android/Chrome às vezes "pausa" a síntese sozinha depois de um tempo parada
if (TTS && typeof window !== "undefined") setInterval(() => { try { if (TTS.paused) TTS.resume(); } catch { /* ok */ } }, 10000);

export function stopAlerts() { try { TTS?.cancel(); } catch { /* ok */ } }

export function alertText(cfg, a, up = false) {
  const c = { ...VOICE_DEFAULT, ...cfg };
  const base = up ? (a.kind === "link" ? c.msg_link_up : c.msg_device_up) : a.kind === "link" ? c.msg_link : c.msg_device;
  if (!c.say_name) return base;
  const who = spoken(a.kind === "link" ? a.name || a.device_name : a.name);
  return `${base} ${who}.`;
}

/** Navegador já liberou som nesta página (precisa de um clique antes, regra dos navegadores). */
export const audioUnlocked = () => (typeof navigator !== "undefined" && navigator.userActivation ? navigator.userActivation.hasBeenActive : true);

/**
 * Fala quando aparece incidente novo no mapa: equipamento sem resposta (e instável, se cfg.warn) e rota/fibra (porta) DOWN.
 * Na primeira leitura de cada mapa só memoriza o que já estava caído (não fala o que já era sabido).
 */
export function useVoiceAlerts(live, cfg, active, scopeKey) {
  const seen = useRef(null);
  useEffect(() => { seen.current = null; }, [scopeKey]);
  useEffect(() => {
    if (!live?.alarms) return;
    const cur = new Map();
    live.alarms.forEach(a => {
      if (a.kind === "link") cur.set(`l|${a.map_id}|${a.link_id || a.name}|${a.if_name}`, a);
      else if (a.state === "down" || (cfg?.warn && a.state === "warn" && !a.loss_only)) cur.set(`d|${a.map_id}|${a.node_id || a.name}`, a);
    });
    const prev = seen.current;
    seen.current = cur;
    if (!prev || !active || !cfg?.enabled) return;
    const c = { ...VOICE_DEFAULT, ...cfg };
    const fresh = [...cur.entries()].filter(([k]) => !prev.has(k)).map(([, a]) => a);
    const links = fresh.filter(a => a.kind === "link"), devs = fresh.filter(a => a.kind !== "link");
    links.slice(0, 3).forEach(a => speakAlert(c, alertText(c, a)));
    if (devs.length > 3) speakAlert(c, c.msg_many.replace("{n}", devs.length));     // queda em massa: uma frase só
    else devs.forEach(a => speakAlert(c, alertText(c, a)));
    if (!c.say_up) return;
    // resolvido: o que estava caído e saiu da lista de incidentes
    // (quem foi colocado em "ignorar" também sai da lista, mas não voltou: não fala)
    const ign = new Set((live.ignored || []).map(x => `d|${x.map_id}|${x.node_id || x.name}`));
    const gone = [...prev.entries()].filter(([k]) => !cur.has(k) && !ign.has(k)).map(([, a]) => a);
    const upL = gone.filter(a => a.kind === "link"), upD = gone.filter(a => a.kind !== "link");
    upL.slice(0, 3).forEach(a => speakAlert(c, alertText(c, a, true), true));
    if (upD.length > 3) speakAlert(c, `${upD.length} equipamentos restabelecidos.`, true);
    else upD.forEach(a => speakAlert(c, alertText(c, a, true), true));
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Configuração que vale para o mapa aberto: a dele, ou a do mapa de cima mais próximo (no caminho aberto). */
export function effectiveVoice(mapId, getVoice, trail = []) {
  const own = getVoice(mapId);
  if (own) return own;
  for (let i = trail.length - 1; i >= 0; i--) { const v = getVoice(trail[i]); if (v) return v; }
  return null;
}
