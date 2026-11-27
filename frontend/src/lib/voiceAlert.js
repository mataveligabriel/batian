// Alerta falado dos mapas de disponibilidade: som de alarme + voz (síntese de voz do navegador/Windows).
// A voz sai do computador que está com o mapa aberto (ex.: a TV do NOC), então a lista de vozes é a dele.
import { useEffect, useRef } from "react";

export const VOICE_DEFAULT = {
  enabled: false, voice: "", pitch: 0.4, rate: 0.92, volume: 1, intro: "beeps", repeat: 1, say_name: true, warn: false,
  msg_device: "Alerta de falha. Equipamento indisponível.",
  msg_link: "Alerta de falha. Possível rompimento.",
  msg_many: "Alerta de falha. {n} equipamentos indisponíveis.",
};

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

export function pickVoice(name) {
  const all = listVoices();
  return all.find(v => v.name === name) || all.find(v => /^pt/i.test(v.lang) && guessGender(v) === "m") || all.find(v => /^pt/i.test(v.lang)) || all[0] || null;
}

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
  if (kind === "beeps") {          // sinal de "vai avisar um evento": 3 bipes subindo, pausa, bipe longo
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

function sayOnce(text, cfg) {
  return new Promise(resolve => {
    if (!TTS || !text) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice(cfg.voice);
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "pt-BR";
    u.pitch = Math.max(0, Math.min(2, Number(cfg.pitch ?? 0.4)));
    u.rate = Math.max(0.5, Math.min(1.6, Number(cfg.rate ?? 0.92)));
    u.volume = Math.max(0, Math.min(1, Number(cfg.volume ?? 1)));
    const done = () => { clearTimeout(guard); resolve(); };
    const guard = setTimeout(done, 20000);          // alguns navegadores não disparam onend
    u.onend = done; u.onerror = done;
    TTS.speak(u);
  });
}

// nomes como "SW-Link_NET_Divino" falados direito
export const spoken = (s) => String(s || "").replace(/[_]+/g, " ").replace(/(\w)-(\w)/g, "$1 $2").replace(/\s+/g, " ").trim();

let queue = Promise.resolve();
/** Enfileira um alerta (não fala por cima do anterior). */
export function speakAlert(cfg, text) {
  queue = queue.then(async () => {
    await playIntro(cfg.intro, cfg.volume);
    const n = Math.max(1, Math.min(3, Number(cfg.repeat) || 1));
    for (let i = 0; i < n; i++) await sayOnce(text, cfg);
  }).catch(() => {});
  return queue;
}

export function stopAlerts() { try { TTS?.cancel(); } catch { /* ok */ } }

export function alertText(cfg, a) {
  const c = { ...VOICE_DEFAULT, ...cfg };
  const base = a.kind === "link" ? c.msg_link : c.msg_device;
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
    seen.current = new Set(cur.keys());
    if (!prev || !active || !cfg?.enabled) return;
    const fresh = [...cur.entries()].filter(([k]) => !prev.has(k)).map(([, a]) => a);
    if (!fresh.length) return;
    const links = fresh.filter(a => a.kind === "link"), devs = fresh.filter(a => a.kind !== "link");
    const c = { ...VOICE_DEFAULT, ...cfg };
    links.slice(0, 3).forEach(a => speakAlert(c, alertText(c, a)));
    if (devs.length > 3) speakAlert(c, c.msg_many.replace("{n}", devs.length));     // queda em massa: uma frase só
    else devs.forEach(a => speakAlert(c, alertText(c, a)));
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Configuração que vale para o mapa aberto: a dele, ou a do mapa de cima mais próximo (no caminho aberto). */
export function effectiveVoice(mapId, getVoice, trail = []) {
  const own = getVoice(mapId);
  if (own) return own;
  for (let i = trail.length - 1; i >= 0; i--) { const v = getVoice(trail[i]); if (v) return v; }
  return null;
}
