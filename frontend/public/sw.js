/* Service worker do Bastion (PWA).
 * - Nunca guarda nada de /api (dados sempre ao vivo, nada sensível em cache).
 * - Arquivos /static/* têm hash no nome: cache-first.
 * - Páginas: sempre da rede (pega a versão nova após o deploy); sem rede, mostra a última cópia.
 * - Recebe as notificações push dos alarmes e abre a tela certa ao tocar.
 */
const CACHE = "bastion-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api")) return;
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.open(CACHE).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) c.put(req, res.clone());
      return res;
    }));
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith((async () => {
      try {
        const res = await fetch(req);
        if (res.ok) (await caches.open(CACHE)).put("/index.html", res.clone());
        return res;
      } catch {
        return (await caches.match("/index.html")) || new Response("Sem conexão com o servidor Bastion.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
      }
    })());
  }
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: "Bastion", body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "Bastion", {
    body: d.body || "",
    tag: d.tag || undefined,          // mesma interface: o aviso de UP substitui o de DOWN
    renotify: !!d.tag,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    timestamp: d.ts || Date.now(),
    data: { url: d.url || "/" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || "/", self.location.origin).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) {
        await w.focus();
        if ("navigate" in w) { try { await w.navigate(target); } catch { /* ignore */ } }
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
