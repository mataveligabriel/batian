// Mapa de satélite/ruas: projeção Web Mercator em "pixels de mundo" no zoom GEO_Z (o canvas trabalha nessas coordenadas).
export const GEO_Z = 12;
const WORLD = 256 * 2 ** GEO_Z;

export function project(lat, lon) {
  const s = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180);
  return { x: ((lon + 180) / 360) * WORLD, y: (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * WORLD };
}

export function unproject(x, y) {
  const lon = (x / WORLD) * 360 - 180;
  const n = Math.PI - (2 * Math.PI * y) / WORLD;
  const lat = (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  return { lat: +lat.toFixed(6), lon: +lon.toFixed(6) };
}

// aceita "-20.77, -41.53", "-20,77 -41,53", "20°46'S 41°32'W" (graus decimais ou com minutos/segundos)
export function parseLatLon(text) {
  const t = String(text || "").trim();
  if (!t) return null;
  const dms = [...t.matchAll(/(\d+(?:[.,]\d+)?)\s*°\s*(?:(\d+(?:[.,]\d+)?)\s*['′]\s*)?(?:(\d+(?:[.,]\d+)?)\s*["″]\s*)?([NSLEOW])/gi)];
  if (dms.length === 2) {
    const v = dms.map(m => {
      const n = (x) => parseFloat(String(x || 0).replace(",", "."));
      let d = n(m[1]) + n(m[2]) / 60 + n(m[3]) / 3600;
      if (/[SOW]/i.test(m[4])) d = -d;
      return { d, ax: /[NS]/i.test(m[4]) ? "lat" : "lon" };
    });
    const lat = v.find(x => x.ax === "lat")?.d, lon = v.find(x => x.ax === "lon")?.d;
    return lat != null && lon != null && Math.abs(lat) <= 85 && Math.abs(lon) <= 180 ? { lat, lon } : null;
  }
  const nums = t.replace(/(-?\d+),(\d+)/g, "$1.$2").match(/-?\d+(?:\.\d+)?/g);
  if (!nums || nums.length !== 2) return null;
  const [lat, lon] = nums.map(Number);
  return Math.abs(lat) <= 85 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

export const BASES = {
  satellite: { label: "Satélite", maxZoom: 19, url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
               attribution: "Imagens © Esri, Maxar, Earthstar Geographics" },
  streets: { label: "Ruas", maxZoom: 19, url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", attribution: "© colaboradores do OpenStreetMap" },
  dark: { label: "Escuro", maxZoom: 19, url: "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", attribution: "© OpenStreetMap · © CARTO" },
  custom: { label: "Personalizado", maxZoom: 20, url: "", attribution: "" },
};
const LABELS = { url: "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", attribution: "nomes © Esri" };

export const GEO_DEFAULTS = { view: "schema", base: "satellite", custom_url: "", labels: true, scale: 0.8, opacity: 1, nodes: "icon", names: false };

// camadas que o canvas desenha: [{url, opacity, maxZoom}] + atribuição
export function geoLayers(g) {
  const cfg = { ...GEO_DEFAULTS, ...(g || {}) };
  const base = BASES[cfg.base] || BASES.satellite;
  const url = cfg.base === "custom" ? cfg.custom_url : base.url;
  const layers = url ? [{ url, opacity: cfg.opacity, maxZoom: base.maxZoom }] : [];
  if (cfg.labels && cfg.base === "satellite") layers.push({ url: LABELS.url, opacity: 1, maxZoom: 19 });
  const attribution = [cfg.base === "custom" ? "mapa personalizado" : base.attribution, cfg.labels && cfg.base === "satellite" ? LABELS.attribution : ""].filter(Boolean).join(" · ");
  return { layers, attribution, scale: cfg.scale || 0.8, icons: cfg.nodes !== "box", names: !!cfg.names };
}

export const tileUrl = (tpl, z, x, y) => tpl.replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{s}", "abc"[(x + y) % 3]);

// centro inicial do mapa: média dos equipamentos já posicionados (ou null)
export function geoCenterOf(map) {
  const pos = (map?.nodes || []).filter(n => n.lat != null && n.lon != null).map(n => project(n.lat, n.lon));
  if (!pos.length) return null;
  return { x: pos.reduce((a, p) => a + p.x, 0) / pos.length, y: pos.reduce((a, p) => a + p.y, 0) / pos.length };
}

/**
 * Mapa como o canvas desenha no modo satélite: x/y = posição geográfica projetada. Quem ainda não tem coordenada fica
 * em volta de `center` com o mesmo desenho do esquema (marcado _nogeo) — é só arrastar até a cidade.
 */
export function toGeoView(map, center) {
  if (!map) return map;
  const c = center || geoCenterOf(map) || project(-15.79, -47.88);
  const ns = map.nodes || [];
  const sx = ns.length ? ns.reduce((a, n) => a + n.x, 0) / ns.length : 0, sy = ns.length ? ns.reduce((a, n) => a + n.y, 0) / ns.length : 0;
  return {
    ...map,
    nodes: ns.map(n => (n.lat != null && n.lon != null ? { ...n, ...project(n.lat, n.lon) } : { ...n, x: c.x + (n.x - sx), y: c.y + (n.y - sy), _nogeo: true })),
    links: (map.links || []).map(l => ({ ...l, curve: null, points: (l.geo_points || []).map(p => project(p.lat, p.lon)) })),
  };
}
