import React from "react";
import { Activity, Radio, Sigma } from "lucide-react";
import { TrafficWidget } from "@/components/dash/TrafficWidget";
import { OpticsPanel } from "@/components/dash/OpticsPanel";

/** Corpo de um gráfico de dashboard (tráfego, agregado ou óptica). */
export function WidgetBody({ w, minutes, refreshKey, height, readOnly = false }) {
  if (w.type === "optics") {
    return <OpticsPanel deviceId={w.device_id} ifIndex={w.if_index} ifName={w.if_name} minutes={Math.max(minutes, 360)}
                        warn={w.rx_warn_dbm} crit={w.rx_crit_dbm} refreshKey={refreshKey} chartHeight={height} readOnly={readOnly} />;
  }
  return <TrafficWidget widget={w} minutes={minutes} refreshKey={refreshKey} height={height} />;
}

export const widgetIcon = (t) => (t === "aggregate" ? Sigma : t === "optics" ? Radio : Activity);
