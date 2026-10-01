// PWA: registro do service worker, detecção de "instalado" e notificações push dos alarmes.
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
export const isStandalone = () => window.matchMedia?.("(display-mode: standalone)").matches || window.navigator.standalone === true;

export function registerServiceWorker() {
  // service worker só existe em HTTPS (ou localhost); em http o app funciona normal, só sem push/offline
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

/** Por que o push não está disponível aqui (null = disponível). */
export function pushBlocker() {
  if (/Electron\//.test(navigator.userAgent)) return "desktop";   // app do Windows: Chromium sem serviço de push
  if (!window.isSecureContext) return "https";
  if (isIOS() && !isStandalone()) return "install";          // iPhone: só no app instalado na Tela de Início
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "unsupported";
  return null;
}
export const BLOCKER_TEXT = {
  https: "As notificações exigem que o BastiON seja acessado por HTTPS (com domínio). Veja o README, seção PWA.",
  install: "No iPhone, primeiro instale o app: Compartilhar → Adicionar à Tela de Início. Depois abra pelo ícone e ative aqui.",
  unsupported: "Este navegador não suporta notificações push.",
  desktop: "O app do Windows não recebe push. No PC, ative pelo Chrome ou Edge (abrindo o endereço do BastiON); no celular, pelo app instalado.",
};

function deviceName() {
  const ua = navigator.userAgent;
  const dev = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) || isIOS() ? "iPad" : /Android/.test(ua) ? "Android"
    : /Windows/.test(ua) ? "Windows" : /Mac/.test(ua) ? "Mac" : /Linux/.test(ua) ? "Linux" : "Navegador";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return `${dev}${br ? ` · ${br}` : ""}${isStandalone() ? " (app)" : ""}`;
}

const keyBytes = (b64) => {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};
const sameKey = (buf, b64) => {
  if (!buf) return false;
  const a = new Uint8Array(buf), b = keyBytes(b64);
  return a.length === b.length && a.every((x, i) => x === b[i]);
};

async function currentSubscription() {
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

export async function enablePush() {
  const block = pushBlocker();
  if (block) throw new Error(BLOCKER_TEXT[block]);
  const perm = await Notification.requestPermission();      // precisa vir de um toque do usuário
  if (perm !== "granted") throw new Error("Permissão negada. Libere as notificações do BastiON nos Ajustes do aparelho.");
  const reg = (await navigator.serviceWorker.getRegistration()) || (await navigator.serviceWorker.register("/sw.js"));
  await navigator.serviceWorker.ready;
  const { data } = await api.get("/push/public-key");
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options?.applicationServerKey, data.key)) { await sub.unsubscribe(); sub = null; }
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(data.key) });
  const j = sub.toJSON();
  await api.post("/push/subscribe", { endpoint: j.endpoint, keys: j.keys, device: deviceName() });
  return true;
}

export async function disablePush() {
  const sub = await currentSubscription();
  if (!sub) return;
  await api.post("/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
  await sub.unsubscribe();
}

/** Estado do push neste aparelho: "on" | "off" | "blocked" | "denied" | "loading". */
export function usePushState() {
  const [state, setState] = useState("loading");
  const refresh = async () => {
    if (pushBlocker()) return setState("blocked");
    if (Notification.permission === "denied") return setState("denied");
    try {
      const sub = await currentSubscription();
      if (sub) api.post("/push/subscribe", { endpoint: sub.endpoint, keys: sub.toJSON().keys, device: deviceName() }).catch(() => {});
      setState(sub ? "on" : "off");
    } catch { setState("off"); }
  };
  useEffect(() => { refresh(); }, []);
  return [state, refresh];
}

/** true em telas estreitas (celular). */
export function useIsMobile(query = "(max-width: 767px)") {
  const get = () => typeof window !== "undefined" && window.matchMedia(query).matches;
  const [m, setM] = useState(get);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setM(mq.matches);
    mq.addEventListener ? mq.addEventListener("change", on) : mq.addListener(on);
    return () => (mq.removeEventListener ? mq.removeEventListener("change", on) : mq.removeListener(on));
  }, [query]);
  return m;
}
