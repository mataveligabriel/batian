import React, { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { useTerminal } from "@/context/TerminalContext";

// Thin route: the actual workspace lives in Layout so sessions survive navigation.
export default function TerminalPage() {
  const { deviceId } = useParams();
  const nav = useNavigate();
  const { openTab, tabs } = useTerminal();

  useEffect(() => {
    if (!deviceId) return;
    const existing = tabs.find(t => t.id === deviceId);
    if (existing) { openTab(existing); nav("/terminal", { replace: true }); return; }
    api.get("/devices").then(r => {
      const d = r.data.find(x => x.id === deviceId);
      if (d) openTab(d); else toast.error("Equipamento não encontrado");
      nav("/terminal", { replace: true });
    });
    // eslint-disable-next-line
  }, [deviceId]);

  return null;
}
