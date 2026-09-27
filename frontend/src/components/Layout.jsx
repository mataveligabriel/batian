import React, { useEffect, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import { TerminalWorkspace } from "@/components/TerminalWorkspace";
import { useTerminal } from "@/context/TerminalContext";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
import {
  LayoutDashboard, Server, TerminalSquare, Play, Radio, KeyRound,
  History, Users as UsersIcon, LogOut, ShieldCheck, Archive, BellRing, ChevronsLeft, ChevronsRight, Network, Gauge, Menu, X,
} from "lucide-react";
import { PushToggle, InstallHint } from "@/components/PushToggle";
import { useIsMobile } from "@/lib/pwa";
import { useTermPrefs } from "@/lib/termPrefs";

const NAV = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Painel NOC", testid: "nav-dashboard" },
  { to: "/devices", icon: Server, label: "Equipamentos", testid: "nav-devices" },
  { to: "/terminal", icon: TerminalSquare, label: "Terminal SSH", testid: "nav-terminal" },
  { to: "/maps", icon: Network, label: "Mapas de rede", testid: "nav-maps" },
  { to: "/dashboards", icon: Gauge, label: "Dashboards", testid: "nav-dashboards" },
  { to: "/batch", icon: Play, label: "Execução em Lote", testid: "nav-batch" },
  { to: "/agents", icon: Radio, label: "Agentes Remotos", testid: "nav-agents" },
  { to: "/ssh-key", icon: KeyRound, label: "Chave SSH Global", testid: "nav-ssh-key" },
  { to: "/sessions", icon: History, label: "Histórico", testid: "nav-sessions" },
  { to: "/backups", icon: Archive, label: "Backups", testid: "nav-backups" },
  { to: "/automation", icon: BellRing, label: "Automação", testid: "nav-automation", adminOnly: true },
  { to: "/users", icon: UsersIcon, label: "Usuários", adminOnly: true, testid: "nav-users" },
];

// perfil View: só estas telas (o servidor também bloqueia o resto)
const VIEWER_PATHS = ["/dashboard", "/dashboards", "/maps"];

export default function Layout() {
  const { user, logout } = useAuth();
  const { tabs } = useTerminal();
  const { pathname } = useLocation();
  const onTerminal = pathname.startsWith("/terminal");
  const [cpOpen, setCpOpen] = useState(false);
  const [prefs, setPrefs] = useTermPrefs();
  const isMobile = useIsMobile();
  const [drawer, setDrawer] = useState(false);            // celular: menu em gaveta
  useEffect(() => { setDrawer(false); }, [pathname]);
  // celular: com o teclado aberto o iOS não encolhe a página — acompanha a área visível para o terminal não ficar atrás do teclado
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    if (!isMobile || !vv) { root.style.removeProperty("--app-h"); return; }
    const on = () => { root.style.setProperty("--app-h", `${Math.round(vv.height)}px`); if (window.scrollY) window.scrollTo(0, 0); };
    on(); vv.addEventListener("resize", on); vv.addEventListener("scroll", on);
    return () => { vv.removeEventListener("resize", on); vv.removeEventListener("scroll", on); root.style.removeProperty("--app-h"); };
  }, [isMobile]);
  const mini = prefs.sidebarCollapsed && !isMobile;
  const isViewer = user?.role === "viewer";
  const current = NAV.find(n => pathname === n.to || pathname.startsWith(n.to + "/"));
  const nav = NAV.filter(n => (!n.adminOnly || user?.role === "admin") && (!isViewer || VIEWER_PATHS.includes(n.to)));
  const onDash = pathname.startsWith("/dashboards");
  const hidden = (onTerminal && prefs.focusMode) || (onDash && prefs.dashFocus);   // modo foco esconde o menu lateral

  if (isViewer && !VIEWER_PATHS.some(p => pathname === p || pathname.startsWith(p + "/"))) return <Navigate to="/dashboard" replace />;

  return (
    <div className="h-[var(--app-h,100dvh)] flex flex-col md:flex-row bg-[#090D14] overflow-hidden">
      {/* Celular: barra do topo com o menu */}
      {!hidden && (
        <header className="md:hidden shrink-0 flex items-center gap-2 px-2 h-12 box-content pt-[env(safe-area-inset-top)] border-b border-[#1E293B] bg-[#0B111C]" data-testid="mobile-topbar">
          <button onClick={() => setDrawer(true)} className="w-10 h-10 flex items-center justify-center text-slate-200" aria-label="Abrir menu" data-testid="mobile-menu-btn"><Menu className="w-5 h-5" /></button>
          <ShieldCheck className="w-4 h-4 text-[#4DA3FF]" />
          <div className="font-heading font-semibold text-slate-100 truncate">{current?.label || "Bastion"}</div>
          {tabs.length > 0 && !onTerminal && (
            <NavLink to="/terminal" className="ml-auto text-[11px] font-mono px-2 py-1 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
              <TerminalSquare className="w-3.5 h-3.5" /> {tabs.length}
            </NavLink>
          )}
        </header>
      )}
      {isMobile && drawer && <div className="fixed inset-0 z-40 bg-black/60" onClick={() => setDrawer(false)} />}
      {/* Sidebar (no celular vira gaveta) */}
      <aside className={`${hidden && !isMobile ? "hidden" : "flex"} ${isMobile ? "w-64" : mini ? "w-14" : "w-52"} shrink-0 border-r border-[#1E293B] bg-[#0B111C] flex-col transition-[width,transform] duration-150
                         ${isMobile ? `fixed inset-y-0 left-0 z-50 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] shadow-2xl ${drawer ? "translate-x-0" : "-translate-x-full"}` : ""}`}
             data-testid="sidebar">
        <div className={`border-b border-[#1E293B] flex items-center ${mini ? "flex-col gap-2 py-3" : "gap-2.5 px-3 py-3"}`}>
          <div className="w-8 h-8 rounded-md bg-[#007AFF]/15 border border-[#007AFF]/40 flex items-center justify-center shrink-0">
            <ShieldCheck className="w-4 h-4 text-[#4DA3FF]" />
          </div>
          {!mini && (
            <div className="flex-1 min-w-0">
              <div className="font-heading font-bold text-slate-100 leading-tight">Bastion</div>
              <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">SSH Central</div>
            </div>
          )}
          <button onClick={() => (isMobile ? setDrawer(false) : setPrefs({ sidebarCollapsed: !mini }))} data-testid="sidebar-toggle"
                  title={mini ? "Expandir menu" : "Encolher menu"}
                  className="w-7 h-7 rounded flex items-center justify-center text-slate-400 hover:text-[#4DA3FF] hover:bg-slate-800/60 border border-transparent hover:border-[#1E293B]">
            {isMobile ? <X className="w-4 h-4" /> : mini ? <ChevronsRight className="w-4 h-4" /> : <ChevronsLeft className="w-4 h-4" />}
          </button>
        </div>

        <nav className={`flex-1 space-y-0.5 overflow-y-auto ${mini ? "p-2" : "p-2"}`}>
          {nav.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              data-testid={item.testid}
              title={mini ? item.label : undefined}
              className={({ isActive }) =>
                `relative flex items-center gap-2.5 ${mini ? "justify-center px-0" : "px-2.5"} py-2.5 md:py-1.5 rounded-md text-sm md:text-[13px] transition-colors ${
                  isActive
                    ? "bg-[#007AFF]/12 text-[#4DA3FF] border border-[#007AFF]/30"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/60 border border-transparent"
                }`
              }
            >
              <item.icon className="w-4 h-4 shrink-0" />
              {!mini && <span className="truncate">{item.label}</span>}
              {item.to === "/terminal" && tabs.length > 0 && (
                <span data-testid="nav-terminal-badge" className={mini
                  ? "absolute -top-1 -right-1 text-[9px] font-mono px-1 rounded bg-emerald-500/25 text-emerald-300"
                  : "ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"}>{tabs.length}</span>
              )}
            </NavLink>
          ))}
        </nav>

        <div className={`border-t border-[#1E293B] p-2 ${mini ? "space-y-1" : "flex flex-wrap gap-1"}`}>
          <PushToggle mini={mini} />
          <div className={`w-full px-2.5 py-1.5 rounded-md bg-[#111722] border border-[#1E293B] ${mini ? "hidden" : ""}`}>
            <div className="text-sm text-slate-100 font-medium truncate" data-testid="current-user-name">{user?.name}</div>
            <div className="text-[11px] text-slate-500 font-mono truncate" title={user?.email}>{user?.email}</div>
            <div className={`text-[10px] uppercase font-mono ${isViewer ? "text-amber-300" : "text-emerald-400"}`}>{isViewer ? "view · somente leitura" : user?.role}</div>
          </div>
          <button
            data-testid="change-password-btn"
            onClick={() => setCpOpen(true)}
            title="Trocar senha"
            className={`${mini ? "w-full" : "flex-1"} flex items-center justify-center gap-1.5 text-[10px] uppercase tracking-wider font-mono text-slate-400 hover:text-[#4DA3FF] px-2 py-1.5 rounded-md hover:bg-slate-800/60 transition-colors border border-transparent hover:border-[#1E293B]`}
          >
            <KeyRound className="w-3.5 h-3.5" /> {!mini && "Trocar senha"}
          </button>
          <ChangePasswordDialog open={cpOpen} onOpenChange={setCpOpen} />
          <button
            data-testid="logout-btn"
            onClick={logout}
            title="Sair"
            className={`${mini ? "mt-1 w-full" : "flex-1"} flex items-center justify-center gap-1.5 text-[10px] uppercase tracking-wider font-mono text-slate-400 hover:text-red-400 px-2 py-1.5 rounded-md hover:bg-red-950/30 transition-colors border border-transparent hover:border-red-900/50`}
          >
            <LogOut className="w-3.5 h-3.5" /> {!mini && "Sair"}
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 min-h-0 flex flex-col relative pb-[env(safe-area-inset-bottom)] md:pb-0">
        {!onTerminal && <InstallHint />}
        <div className={`flex-1 min-h-0 flex flex-col ${onTerminal ? "hidden" : ""}`}>
          <Outlet />
        </div>
        {!isViewer && <TerminalWorkspace visible={onTerminal} />}
      </main>
      <Toaster theme="dark" richColors position={isMobile ? "top-center" : "top-right"} />
    </div>
  );
}
