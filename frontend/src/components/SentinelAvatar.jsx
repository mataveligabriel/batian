import React from "react";

/** Sentinela — o assistente de IA do BastiON. Um guardinha-robô com elmo de ameias (a torre do bastião),
 *  visor com olhos de LED e um escudo com o hexágono dos POPs.
 *  state: "idle" (pisca de vez em quando) | "thinking" (antena pulsa, olhos varrem o visor) | "alert" (olhos e antena vermelhos)
 *  bg: false desenha sem o círculo de fundo. */
export const SENTINEL_NAME = "Sentinela";

export function SentinelAvatar({ size = 40, state = "idle", bg = true, className = "", title = SENTINEL_NAME }) {
  const eye = state === "alert" ? "#ff5a6a" : "#5eead4";
  const beacon = state === "alert" ? "#ff5a6a" : "#fbbf24";
  const id = React.useId().replace(/:/g, "");
  return (
    <svg width={size} height={size} viewBox="0 0 128 128" className={className} role="img" aria-label={title}>
      <title>{title}</title>
      <defs>
        <radialGradient id={`bg${id}`} cx="50%" cy="38%" r="70%">
          <stop offset="0" stopColor="#17324d" /><stop offset="1" stopColor="#0a1828" />
        </radialGradient>
        <linearGradient id={`hel${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3d6f9c" /><stop offset="1" stopColor="#24496d" />
        </linearGradient>
        <linearGradient id={`body${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#2b5580" /><stop offset="1" stopColor="#1b3a59" />
        </linearGradient>
        <clipPath id={`clip${id}`}><circle cx="64" cy="64" r="64" /></clipPath>
      </defs>
      <style>{`
        @keyframes snt-blink${id} { 0%, 92%, 100% { transform: scaleY(1) } 95% { transform: scaleY(.12) } }
        @keyframes snt-scan${id} { 0%, 100% { transform: translateX(-5px) } 50% { transform: translateX(5px) } }
        @keyframes snt-beacon${id} { 0%, 100% { opacity: 1 } 50% { opacity: .25 } }
        .snt-eyes${id} { transform-origin: 64px 58px; animation: ${state === "thinking" ? `snt-scan${id} 1.1s ease-in-out infinite` : `snt-blink${id} 4.5s infinite`} }
        .snt-beacon${id} { animation: snt-beacon${id} ${state === "idle" ? "2.6s" : ".7s"} ease-in-out infinite }
        @media (prefers-reduced-motion: reduce) { .snt-eyes${id}, .snt-beacon${id} { animation: none } }
      `}</style>
      <g clipPath={bg ? `url(#clip${id})` : undefined}>
        {bg && <circle cx="64" cy="64" r="64" fill={`url(#bg${id})`} />}
        {/* corpo e ombros */}
        <path d="M24 128 C24 104 38 92 64 92 C90 92 104 104 104 128 Z" fill={`url(#body${id})`} />
        <path d="M44 96 Q64 104 84 96" stroke="#5eead4" strokeOpacity=".35" strokeWidth="2" fill="none" />
        {/* escudo com o hexágono */}
        <path d="M64 100 L80 105 V114 C80 121 72 126 64 128 C56 126 48 121 48 114 V105 Z" fill="#0f2338" stroke="#5eead4" strokeWidth="2.2" />
        <polygon points="64,107 70,110.5 70,117.5 64,121 58,117.5 58,110.5" fill="none" stroke="#5eead4" strokeWidth="2" />
        <circle cx="64" cy="114" r="2.2" fill="#5eead4" />
        {/* antena com o farol */}
        <line x1="64" y1="28" x2="64" y2="18" stroke="#9cc3e6" strokeWidth="3" strokeLinecap="round" />
        <circle cx="64" cy="13" r="8.5" fill={beacon} opacity=".18" className={`snt-beacon${id}`} />
        <circle cx="64" cy="13" r="5" fill={beacon} className={`snt-beacon${id}`} />
        {/* elmo com ameias (a torre do bastião) */}
        <path d="M30 40 V30 H40 V36 H50 V28 H60 V34 H68 V28 H78 V36 H88 V30 H98 V40 Z" fill={`url(#hel${id})`} />
        <rect x="28" y="38" width="72" height="54" rx="20" fill={`url(#hel${id})`} />
        <rect x="28" y="38" width="72" height="54" rx="20" fill="none" stroke="#7fb0dc" strokeOpacity=".35" strokeWidth="1.5" />
        {/* orelhas / parafusos */}
        <circle cx="27" cy="62" r="6" fill="#24496d" stroke="#7fb0dc" strokeOpacity=".4" />
        <circle cx="101" cy="62" r="6" fill="#24496d" stroke="#7fb0dc" strokeOpacity=".4" />
        {/* visor */}
        <rect x="36" y="48" width="56" height="22" rx="11" fill="#06111c" />
        <g className={`snt-eyes${id}`}>
          <rect x="46" y="53" width="11" height="12" rx="5" fill={eye} />
          <rect x="71" y="53" width="11" height="12" rx="5" fill={eye} />
          <rect x="46" y="53" width="11" height="12" rx="5" fill={eye} opacity=".35" style={{ filter: "blur(3px)" }} />
          <rect x="71" y="53" width="11" height="12" rx="5" fill={eye} opacity=".35" style={{ filter: "blur(3px)" }} />
        </g>
        {/* sorriso discreto */}
        <path d="M56 80 Q64 85 72 80" stroke="#9cc3e6" strokeWidth="2.6" strokeLinecap="round" fill="none" />
      </g>
    </svg>
  );
}
