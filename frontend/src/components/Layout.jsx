import React, { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import { TerminalWorkspace } from "@/components/TerminalWorkspace";
import { useTerminal } from "@/context/TerminalContext";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
import {
  LayoutDashboard, Server, TerminalSquare, Play, Radio, KeyRound,
  History, Users as UsersIcon, LogOut, ShieldCheck, Archive, BellRing, ChevronsLeft, ChevronsRight, Network,
} from "lucide-react";
import { useTermPrefs } from "@/lib/termPrefs";

const NAV = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Painel NOC", testid: "nav-dashboard" },
  { to: "/devices", icon: Server, label: "Equipamentos", testid: "nav-devices" },
  { to: "/terminal", icon: TerminalSquare, label: "Terminal SSH", testid: "nav-terminal" },
  { to: "/maps", icon: Network, label: "Mapas de rede", testid: "nav-maps" },
  { to: "/batch", icon: Play, label: "Execução em Lote", testid: "nav-batch" },
  { to: "/agents", icon: Radio, label: "Agentes Remotos", testid: "nav-agents" },
  { to: "/ssh-key", icon: KeyRound, label: "Chave SSH Global", testid: "nav-ssh-key" },
  { to: "/sessions", icon: History, label: "Histórico", testid: "nav-sessions" },
  { to: "/backups", icon: Archive, label: "Backups", testid: "nav-backups" },
  { to: "/automation", icon: BellRing, label: "Automação", testid: "nav-automation", adminOnly: true },
  { to: "/users", icon: UsersIcon, label: "Usuários", adminOnly: true, testid: "nav-users" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const { tabs } = useTerminal();
  const { pathname } = useLocation();
  const onTerminal = pathname.startsWith("/terminal");
  const [cpOpen, setCpOpen] = useState(false);
  const [prefs, setPrefs] = useTermPrefs();
  const mini = prefs.sidebarCollapsed;
  const hidden = onTerminal && prefs.focusMode;   // modo foco do terminal esconde o menu lateral

  return (
    <div className="h-screen flex bg-[#090D14] overflow-hidden">
      {/* Sidebar */}
      <aside className={`${hidden ? "hidden" : "flex"} ${mini ? "w-14" : "w-64"} shrink-0 border-r border-[#1E293B] bg-[#0B111C] flex-col transition-[width] duration-150`}
             data-testid="sidebar">
        <div className={`border-b border-[#1E293B] flex items-center ${mini ? "flex-col gap-2 py-3" : "gap-3 p-5"}`}>
          <div className="w-9 h-9 rounded-md bg-[#007AFF]/15 border border-[#007AFF]/40 flex items-center justify-center shrink-0">
            <ShieldCheck className="w-5 h-5 text-[#4DA3FF]" />
          </div>
          {!mini && (
            <div className="flex-1 min-w-0">
              <div className="font-heading font-bold text-slate-100 leading-tight">Bastion</div>
              <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">SSH Central</div>
            </div>
          )}
          <button onClick={() => setPrefs({ sidebarCollapsed: !mini })} data-testid="sidebar-toggle"
                  title={mini ? "Expandir menu" : "Encolher menu"}
                  className="w-7 h-7 rounded flex items-center justify-center text-slate-400 hover:text-[#4DA3FF] hover:bg-slate-800/60 border border-transparent hover:border-[#1E293B]">
            {mini ? <ChevronsRight className="w-4 h-4" /> : <ChevronsLeft className="w-4 h-4" />}
          </button>
        </div>

        <nav className={`flex-1 space-y-1 overflow-y-auto ${mini ? "p-2" : "p-3"}`}>
          {NAV.filter(n => !n.adminOnly || user?.role === "admin").map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              data-testid={item.testid}
              title={mini ? item.label : undefined}
              className={({ isActive }) =>
                `relative flex items-center gap-3 ${mini ? "justify-center px-0" : "px-3"} py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-[#007AFF]/12 text-[#4DA3FF] border border-[#007AFF]/30"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/60 border border-transparent"
                }`
              }
            >
              <item.icon className="w-4 h-4 shrink-0" />
              {!mini && <span>{item.label}</span>}
              {item.to === "/terminal" && tabs.length > 0 && (
                <span data-testid="nav-terminal-badge" className={mini
                  ? "absolute -top-1 -right-1 text-[9px] font-mono px-1 rounded bg-emerald-500/25 text-emerald-300"
                  : "ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"}>{tabs.length}</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className={`border-t border-[#1E293B] ${mini ? "p-2" : "p-3"}`}>
          <div className={`px-3 py-2 rounded-md bg-[#111722] border border-[#1E293B] ${mini ? "hidden" : ""}`}>
            <div className="text-xs text-slate-500 uppercase tracking-widest font-mono">Sessão</div>
            <div className="text-sm text-slate-100 font-medium truncate" data-testid="current-user-name">{user?.name}</div>
            <div className="text-xs text-slate-500 font-mono truncate">{user?.email}</div>
            <div className="text-[10px] uppercase mt-1 text-emerald-400 font-mono">{user?.role}</div>
          </div>
          <button
            data-testid="change-password-btn"
            onClick={() => setCpOpen(true)}
            title="Trocar senha"
            className={`${mini ? "" : "mt-2"} w-full flex items-center justify-center gap-2 text-xs uppercase tracking-widest font-mono text-slate-400 hover:text-[#4DA3FF] px-3 py-2 rounded-md hover:bg-slate-800/60 transition-colors border border-transparent hover:border-[#1E293B]`}
          >
            <KeyRound className="w-3.5 h-3.5" /> {!mini && "Trocar senha"}
          </button>
          <ChangePasswordDialog open={cpOpen} onOpenChange={setCpOpen} />
          <button
            data-testid="logout-btn"
            onClick={logout}
            title="Sair"
            className="mt-2 w-full flex items-center justify-center gap-2 text-xs uppercase tracking-widest font-mono text-slate-400 hover:text-red-400 px-3 py-2 rounded-md hover:bg-red-950/30 transition-colors border border-transparent hover:border-red-900/50"
          >
            <LogOut className="w-3.5 h-3.5" /> {!mini && "Sair"}
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 flex flex-col relative">
        <div className={`flex-1 min-h-0 flex flex-col ${onTerminal ? "hidden" : ""}`}>
          <Outlet />
        </div>
        <TerminalWorkspace visible={onTerminal} />
      </main>
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}
