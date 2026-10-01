import React from "react";
import { ShieldCheck } from "lucide-react";

/** Nome da marca: "Basti" + "ON" em verde (o estado online dos equipamentos). */
export function Wordmark({ className = "" }) {
  return <span className={`brand-word ${className}`} aria-label="BastiON">Basti<span className="on">ON</span></span>;
}

/** O escudo de sempre, num quadro discreto. size: "sm" (menu) | "lg" (login). */
export function LogoMark({ size = "sm" }) {
  const box = size === "lg" ? "w-14 h-14 rounded-2xl" : "w-8 h-8 rounded-lg";
  const ico = size === "lg" ? "w-8 h-8" : "w-[18px] h-[18px]";
  return (
    <div className={`${box} bg-gradient-to-b from-brand/25 to-brand/10 border border-brand/35 flex items-center justify-center shrink-0`}>
      <ShieldCheck className={`${ico} text-brand-soft`} strokeWidth={2} />
    </div>
  );
}
