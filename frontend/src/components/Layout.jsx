import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import {
  LayoutDashboard, Server, TerminalSquare, Play, Radio, KeyRound,
  History, Users as UsersIcon, LogOut, ShieldCheck,
} from "lucide-react";

const NAV = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Painel NOC", testid: "nav-dashboard" },
  { to: "/devices", icon: Server, label: "Equipamentos", testid: "nav-devices" },
  { to: "/terminal", icon: TerminalSquare, label: "Terminal SSH", testid: "nav-terminal" },
  { to: "/batch", icon: Play, label: "Execução em Lote", testid: "nav-batch" },
  { to: "/agents", icon: Radio, label: "Agentes Remotos", testid: "nav-agents" },
  { to: "/ssh-key", icon: KeyRound, label: "Chave SSH Global", testid: "nav-ssh-key" },
  { to: "/sessions", icon: History, label: "Histórico", testid: "nav-sessions" },
  { to: "/users", icon: UsersIcon, label: "Usuários", adminOnly: true, testid: "nav-users" },
];

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();

  return (
    <div className="min-h-screen flex bg-[#090D14]">
      {/* Sidebar */}
      <aside className="w-64 shrink-0 border-r border-[#1E293B] bg-[#0B111C] flex flex-col">
        <div className="p-5 border-b border-[#1E293B] flex items-center gap-3">
          <div className="w-9 h-9 rounded-md bg-[#007AFF]/15 border border-[#007AFF]/40 flex items-center justify-center">
            <ShieldCheck className="w-5 h-5 text-[#4DA3FF]" />
          </div>
          <div>
            <div className="font-heading font-bold text-slate-100 leading-tight">Bastion</div>
            <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">SSH Central</div>
          </div>
        </div>

        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {NAV.filter(n => !n.adminOnly || user?.role === "admin").map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              data-testid={item.testid}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-[#007AFF]/12 text-[#4DA3FF] border border-[#007AFF]/30"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/60 border border-transparent"
                }`
              }
            >
              <item.icon className="w-4 h-4" />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="p-3 border-t border-[#1E293B]">
          <div className="px-3 py-2 rounded-md bg-[#111722] border border-[#1E293B]">
            <div className="text-xs text-slate-500 uppercase tracking-widest font-mono">Sessão</div>
            <div className="text-sm text-slate-100 font-medium truncate" data-testid="current-user-name">{user?.name}</div>
            <div className="text-xs text-slate-500 font-mono truncate">{user?.email}</div>
            <div className="text-[10px] uppercase mt-1 text-emerald-400 font-mono">{user?.role}</div>
          </div>
          <button
            data-testid="logout-btn"
            onClick={logout}
            className="mt-2 w-full flex items-center justify-center gap-2 text-xs uppercase tracking-widest font-mono text-slate-400 hover:text-red-400 px-3 py-2 rounded-md hover:bg-red-950/30 transition-colors border border-transparent hover:border-red-900/50"
          >
            <LogOut className="w-3.5 h-3.5" /> Sair
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 flex flex-col">
        {children}
      </main>
      <Toaster theme="dark" richColors position="top-right" />
    </div>
  );
}
