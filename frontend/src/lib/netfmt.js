// Formatação e escala de cores do mapa de tráfego.
// Utilização é magnitude -> rampa sequencial de um só azul (validada contra o fundo do mapa);
// a partir de 70% entram as cores de STATUS (atenção / crítico), sempre acompanhadas de rótulo.

export const fmtBps = (bps) => {
  if (bps === null || bps === undefined || Number.isNaN(bps)) return "—";
  if (bps >= 1e9) return `${(bps / 1e9).toFixed(bps >= 1e10 ? 1 : 2)} Gbps`;
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(bps >= 1e8 ? 0 : 1)} Mbps`;
  if (bps >= 1e3) return `${(bps / 1e3).toFixed(0)} Kbps`;
  return `${Math.round(bps)} bps`;
};

// rótulo curto para eixos de gráfico
export const fmtBpsShort = (bps) => {
  if (!bps) return "0";
  if (bps >= 1e9) return `${+(bps / 1e9).toFixed(1)}G`;
  if (bps >= 1e6) return `${+(bps / 1e6).toFixed(1)}M`;
  if (bps >= 1e3) return `${+(bps / 1e3).toFixed(0)}K`;
  return `${Math.round(bps)}`;
};

export const fmtSpeed = (mbps) => {
  if (!mbps) return "—";
  return mbps >= 1000 ? `${+(mbps / 1000).toFixed(1)}G` : `${mbps}M`;
};

export const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };
export const NO_DATA = "#475569";

export const UTIL_BANDS = [
  { max: 10, color: "#256abf", label: "0–10%" },
  { max: 25, color: "#3987e5", label: "10–25%" },
  { max: 50, color: "#6da7ec", label: "25–50%" },
  { max: 70, color: "#b7d3f6", label: "50–70%" },
  { max: 85, color: STATUS.warning, label: "70–85%", status: "atenção" },
  { max: Infinity, color: STATUS.critical, label: "> 85%", status: "crítico" },
];

export function utilColor(pct) {
  if (pct === null || pct === undefined) return NO_DATA;
  return (UTIL_BANDS.find(b => pct < b.max) || UTIL_BANDS[UTIL_BANDS.length - 1]).color;
}

export const OPER_LABEL = {
  up: "up", down: "down", lowerLayerDown: "down (camada inferior)", notPresent: "ausente",
  dormant: "dormente", testing: "teste", unknown: "desconhecido",
};
export const isDown = (oper) => ["down", "lowerLayerDown", "notPresent", "dormant"].includes(oper);
