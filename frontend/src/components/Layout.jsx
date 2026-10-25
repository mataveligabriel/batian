import React, { useEffect, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";
import { Toaster } from "@/components/ui/sonner";
import { TerminalWorkspace } from "@/components/TerminalWorkspace";
import { useTerminal } from "@/context/TerminalContext";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
import { AccountSecurity } from "@/components/AccountSecurity";
import {
  ChevronDown, LayoutDashboard, Server, TerminalSquare, Play, Radio, KeyRound,
  History, Users as UsersIcon, LogOut, Palette, Settings as SettingsIcon, Siren, ShieldCheck, Archive, ChevronsLeft, ChevronsRight, Network, Gauge, Menu, X, Waves, Globe, Telescope, FileBadge, MonitorUp, Cpu, Router, Waypoints, ScrollText, Activity, BadgeCheck, Wrench, Share2, Cable, ClipboardCheck, Binary, Send, UserX, Radar, ShieldAlert, Bug,
} from "lucide-react";
import { PushToggle, InstallHint } from "@/components/PushToggle";
import { AssistantChat } from "@/components/AssistantChat";
import { VpnIndicator } from "@/components/VpnPanel";
import { useIsMobile } from "@/lib/pwa";
import { useTermPrefs } from "@/lib/termPrefs";
import { Wordmark, LogoMark } from "@/components/Brand";
import { canUse, PATH_MODULE } from "@/lib/modules";
import { AppearanceDialog } from "@/components/AppearanceDialog";

// Menu por categoria: item solto ou grupo com submenus (abre ao clicar ou passar o mouse)
const MENU = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Painel NOC", testid: "nav-dashboard" },
  { to: "/incidents", icon: Siren, label: "Incidentes", testid: "nav-incidents" },
  { group: "monitor", icon: Gauge, label: "Monitoramento", items: [
    { to: "/maps", icon: Network, label: "Mapas de rede", testid: "nav-maps" },
    { to: "/dashboards", icon: Gauge, label: "Dashboards", testid: "nav-dashboards" },
    { to: "/probes", icon: Activity, label: "Sondas ativas", testid: "nav-probes" },
    { to: "/logs", icon: ScrollText, label: "Syslog e Traps", testid: "nav-logs" },
    { to: "/bgp-monitor", icon: Share2, label: "Monitor BGP", testid: "nav-bgp" },
    { to: "/gpon", icon: Cable, label: "GPON", testid: "nav-gpon" },
    { to: "/sla", icon: BadgeCheck, label: "SLA", testid: "nav-sla" },
  ] },
  { group: "access", icon: TerminalSquare, label: "Acesso", items: [
    { to: "/devices", icon: Server, label: "Equipamentos", testid: "nav-devices" },
    { to: "/terminal", icon: TerminalSquare, label: "Terminal SSH", testid: "nav-terminal" },
    { to: "/web", icon: Globe, label: "Acesso Web", testid: "nav-web" },
    { to: "/winbox", icon: Router, label: "Winbox", testid: "nav-winbox" },
    { to: "/rdp", icon: MonitorUp, label: "Área de Trabalho", testid: "nav-rdp" },
    { to: "/agents", icon: Radio, label: "Agentes Remotos", testid: "nav-agents" },
  ] },
  { group: "ops", icon: Wrench, label: "Operação", items: [
    { to: "/maint", icon: Wrench, label: "Manutenções", testid: "nav-maint" },
    { to: "/batch", icon: Play, label: "Execução em Lote", testid: "nav-batch" },
    { to: "/olt-script", icon: Cpu, label: "Script de OLT", testid: "nav-oltgen" },
    { to: "/backups", icon: Archive, label: "Backups", testid: "nav-backups" },
    { to: "/sessions", icon: History, label: "Histórico", testid: "nav-sessions" },
  ] },
  { group: "routing", icon: Waves, label: "Tráfego e rotas", items: [
    { to: "/flow", icon: Waves, label: "Análise de Flow", testid: "nav-flow" },
    { to: "/te", icon: Waypoints, label: "Eng. de tráfego", testid: "nav-te" },
    { to: "/lg", icon: Telescope, label: "Looking Glass", testid: "nav-lg" },
    { to: "/rpki", icon: FileBadge, label: "RPKI", testid: "nav-rpki" },
  ] },
  { group: "inventory", icon: Server, label: "Inventário", items: [
    { to: "/ipam", icon: Binary, label: "IPAM", testid: "nav-ipam" },
    { to: "/compliance", icon: ClipboardCheck, label: "Conformidade", testid: "nav-compliance" },
  ] },
  { group: "soc", icon: ShieldCheck, label: "Painel SOC", items: [
    { to: "/soc/commands", icon: ShieldCheck, label: "Política de comandos", testid: "nav-cmdpolicy" },
    { to: "/soc/logins", icon: UserX, label: "Logins suspeitos", adminOnly: true, testid: "nav-logins" },
    { to: "/soc/threat", icon: Radar, label: "Threat intel", adminOnly: true, testid: "nav-ti" },
    { to: "/soc/certbr", icon: ShieldAlert, label: "Auditoria CERT.br", adminOnly: true, testid: "nav-certbr" },
    { to: "/soc/cves", icon: Bug, label: "CVEs do firmware", adminOnly: true, testid: "nav-cves" },
    { to: "/soc/siem", icon: Send, label: "SIEM e auditoria", adminOnly: true, testid: "nav-siem" },
    { to: "/soc/sso", icon: KeyRound, label: "Login único (SSO)", adminOnly: true, testid: "nav-sso" },
  ] },
  { group: "system", icon: SettingsIcon, label: "Sistema", items: [
    { to: "/ssh-key", icon: KeyRound, label: "Chave SSH Global", testid: "nav-ssh-key", notAdmin: true },   // admin: Configurações
    { to: "/users", icon: UsersIcon, label: "Usuários", adminOnly: true, testid: "nav-users" },
    { to: "/settings", icon: SettingsIcon, label: "Configurações", adminOnly: true, testid: "nav-settings" },
  ] },
];
const NAV = MENU.flatMap(m => m.items || [m]);
const OPEN_KEY = "bastion_nav_open";

// perfil View: só estas telas (o servidor também bloqueia o resto)
const VIEWER_PATHS = ["/dashboard", "/dashboards", "/maps"];

export default function Layout() {
  const { user, logout } = useAuth();
  const [lookOpen, setLookOpen] = useState(false);
  const [uiTheme, setUiTheme] = useState(() => document.documentElement.getAttribute("data-theme") || "dark");
  useEffect(() => {
    const on = (e) => setUiTheme(e.detail?.resolved || "dark");
    window.addEventListener("bastion-theme", on);
    return () => window.removeEventListener("bastion-theme", on);
  }, []);
  const { tabs } = useTerminal();
  // contador de incidentes ativos no menu (atualiza a cada 30 s)
  const [incSum, setIncSum] = useState(null);
  const incOk = user && user.role !== "viewer" && canUse(user, "incidents");
  useEffect(() => {
    if (!incOk) return undefined;
    const get = () => api.get("/incidents-summary").then(r => setIncSum(r.data)).catch(() => {});
    get();
    const t = setInterval(get, 30000);
    return () => clearInterval(t);
  }, [incOk]);
  const { pathname } = useLocation();
  const onTerminal = pathname.startsWith("/terminal");
  const [cpOpen, setCpOpen] = useState(false);
  const [secOpen, setSecOpen] = useState(false);
  const mustEnroll = !!(user?.require_2fa && !user?.totp_enabled);
  const [prefs, setPrefs] = useTermPrefs();
  const isMobile = useIsMobile();
  const [drawer, setDrawer] = useState(false);            // celular: menu em gaveta
  const [openGroups, setOpenGroups] = useState(() => { try { return JSON.parse(localStorage.getItem(OPEN_KEY) || "{}"); } catch { return {}; } });
  useEffect(() => { try { localStorage.setItem(OPEN_KEY, JSON.stringify(openGroups)); } catch { /* sem storage */ } }, [openGroups]);
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
  const allowed = (to) => !PATH_MODULE[to] || canUse(user, PATH_MODULE[to]);     // módulos liberados para este usuário
  const show = (n) => (!n.adminOnly || user?.role === "admin") && !(n.notAdmin && user?.role === "admin") && (!isViewer || VIEWER_PATHS.includes(n.to)) && (isViewer || allowed(n.to));
  const menu = MENU.map(m => (m.items ? { ...m, items: m.items.filter(show) } : m)).filter(m => (m.items ? m.items.length > 0 : show(m)))
    .map(m => (m.items && m.items.length === 1 ? m.items[0] : m));      // grupo com um item só vira item
  const onDash = pathname.startsWith("/dashboards");
  const hidden = (onTerminal && prefs.focusMode) || (onDash && prefs.dashFocus);   // modo foco esconde o menu lateral

  const renderItem = (item, sub) => (
    <NavLink
      key={item.to}
      to={item.to}
      data-testid={item.testid}
      title={mini && !sub ? item.label : undefined}
      className={({ isActive }) =>
        `relative flex items-center gap-2.5 ${mini && !sub ? "justify-center px-0" : sub && !mini ? "pl-8 pr-2.5" : "px-2.5"} ${sub ? "py-2 md:py-[6px] text-[13px]" : "py-2.5 md:py-[7px] text-sm md:text-[13.5px]"} rounded-md transition-colors ${
          isActive
            ? "bg-white/[0.06] text-slate-50 font-medium before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-[3px] before:rounded-full before:bg-brand-soft"
            : "text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]"
        }`
      }
    >
      <item.icon className={`${sub ? "w-3.5 h-3.5" : "w-4 h-4"} shrink-0 opacity-90`} />
      {(!mini || sub) && <span className="truncate">{item.label}</span>}
      {item.to === "/incidents" && incSum?.active > 0 && (
        <span data-testid="nav-incidents-badge" title={`${incSum.open} aberto(s), ${incSum.ack} reconhecido(s)`}
              className={`${mini ? "absolute -top-1 -right-1 text-[9px] px-1" : "ml-auto text-[10px] px-1.5 py-0.5"} font-mono rounded border ${
                incSum.open ? (incSum.worst === "critical" || incSum.worst === "high" ? "bg-red-500/20 text-red-300 border-red-500/40 animate-pulse" : "bg-amber-500/15 text-amber-300 border-amber-500/30")
                  : "bg-white/5 text-slate-300 border-line"}`}>{incSum.active}</span>
      )}
      {item.to === "/terminal" && tabs.length > 0 && (
        <span data-testid="nav-terminal-badge" className="ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">{tabs.length}</span>
      )}
    </NavLink>
  );

  if (isViewer && !VIEWER_PATHS.some(p => pathname === p || pathname.startsWith(p + "/"))) return <Navigate to="/dashboard" replace />;
  if (!isViewer && current && !allowed(current.to)) return <Navigate to="/dashboard" replace />;   // módulo não liberado

  return (
    <div className="h-[var(--app-h,100dvh)] flex flex-col md:flex-row bg-canvas overflow-hidden">
      {/* Celular: barra do topo com o menu */}
      {!hidden && (
        <header className="md:hidden shrink-0 flex items-center gap-2 px-2 h-12 box-content pt-[env(safe-area-inset-top)] border-b border-line bg-panel" data-testid="mobile-topbar">
          <button onClick={() => setDrawer(true)} className="w-10 h-10 flex items-center justify-center text-slate-200" aria-label="Abrir menu" data-testid="mobile-menu-btn"><Menu className="w-5 h-5" /></button>
          {current ? <div className="font-heading font-semibold text-slate-100 truncate">{current.label}</div> : <Wordmark className="text-base" />}
          {tabs.length > 0 && !onTerminal && (
            <NavLink to="/terminal" className="ml-auto text-[11px] font-mono px-2 py-1 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
              <TerminalSquare className="w-3.5 h-3.5" /> {tabs.length}
            </NavLink>
          )}
        </header>
      )}
      {isMobile && drawer && <div className="fixed inset-0 z-40 bg-black/60" onClick={() => setDrawer(false)} />}
      {/* Sidebar (no celular vira gaveta) */}
      <aside className={`${hidden && !isMobile ? "hidden" : "flex"} ${isMobile ? "w-64" : mini ? "w-14" : "w-56"} shrink-0 border-r border-line bg-panel flex-col transition-[width,transform] duration-150
                         ${isMobile ? `fixed inset-y-0 left-0 z-50 pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] shadow-2xl ${drawer ? "translate-x-0" : "-translate-x-full"}` : ""}`}
             data-testid="sidebar">
        <div className={`flex items-center ${mini ? "flex-col gap-2 py-3" : "gap-2.5 px-3 pt-4 pb-3"}`}>
          <LogoMark />
          {!mini && (
            <div className="flex-1 min-w-0">
              <Wordmark className="text-[17px] leading-none" />
              <div className="text-[11px] text-slate-500 mt-1">Central do NOC</div>
            </div>
          )}
          <button onClick={() => (isMobile ? setDrawer(false) : setPrefs({ sidebarCollapsed: !mini }))} data-testid="sidebar-toggle"
                  title={mini ? "Expandir menu" : "Encolher menu"} aria-label={mini ? "Expandir menu" : "Encolher menu"}
                  className="w-7 h-7 rounded-md flex items-center justify-center text-slate-500 hover:text-slate-200 hover:bg-white/5">
            {isMobile ? <X className="w-4 h-4" /> : mini ? <ChevronsRight className="w-4 h-4" /> : <ChevronsLeft className="w-4 h-4" />}
          </button>
        </div>

        <nav className={`flex-1 space-y-px overflow-y-auto ${mini ? "p-2" : "px-2.5 py-2"}`}>
          {menu.map(m => (m.items
            ? <NavGroup key={m.group} g={m} mini={mini} isMobile={isMobile} pathname={pathname} open={openGroups} setOpen={setOpenGroups}
                        renderItem={(it, sub) => renderItem(it, sub)} badge={m.group === "access" && tabs.length > 0 ? tabs.length : null} />
            : renderItem(m, false)))}
        </nav>
        {!isViewer && canUse(user, "agents") && <VpnIndicator mini={mini} />}

        <div className={`border-t border-line p-2 ${mini ? "space-y-1" : "flex flex-wrap gap-1"}`}>
          <PushToggle mini={mini} />
          <div className={`w-full flex items-center gap-2.5 px-2 py-2 ${mini ? "hidden" : ""}`}>
            <div className="w-8 h-8 rounded-full bg-slate-800 border border-line flex items-center justify-center text-[13px] font-semibold text-slate-200 shrink-0">{(user?.name || "?").trim().charAt(0).toUpperCase()}</div>
            <div className="min-w-0">
              <div className="text-[13px] text-slate-100 font-medium truncate" data-testid="current-user-name">{user?.name}</div>
              <div className="text-[11px] text-slate-500 truncate" title={user?.email}>{isViewer ? <span className="text-amber-300">Somente leitura</span> : ({ admin: "Administrador", operator: "Operador" }[user?.role] || user?.role)}</div>
            </div>
            <button data-testid="logout-btn" onClick={logout} title="Sair" aria-label="Sair"
                    className="ml-auto w-8 h-8 rounded-md flex items-center justify-center text-slate-500 hover:text-red-300 hover:bg-red-500/10 transition-colors">
              <LogOut className="w-4 h-4" />
            </button>
          </div>
          <button
            data-testid="change-password-btn"
            onClick={() => setCpOpen(true)}
            title="Trocar senha"
            className={`${mini ? "w-full" : "flex-1"} flex items-center justify-center gap-1.5 text-[12px] text-slate-400 hover:text-slate-100 px-2 py-1.5 rounded-md hover:bg-white/5 transition-colors`}
          >
            <KeyRound className="w-3.5 h-3.5" /> {!mini && "Senha"}
          </button>
          <ChangePasswordDialog open={cpOpen} onOpenChange={setCpOpen} />
          <button
            data-testid="account-security-btn"
            onClick={() => setSecOpen(true)}
            title="Segurança da conta: 2FA, sessões e últimos acessos"
            className={`${mini ? "w-full" : "flex-1"} flex items-center justify-center gap-1.5 text-[12px] ${user?.totp_enabled ? "text-slate-400" : "text-amber-300"} hover:text-slate-100 px-2 py-1.5 rounded-md hover:bg-white/5 transition-colors`}
          >
            <ShieldCheck className="w-3.5 h-3.5" /> {!mini && "Segurança"}
          </button>
          <AccountSecurity open={secOpen || mustEnroll} onOpenChange={setSecOpen} forced={mustEnroll} />
          <button
            data-testid="appearance-btn"
            onClick={() => setLookOpen(true)}
            title="Aparência: tema claro, escuro ou do sistema, cor de destaque e tamanho do texto"
            className={`${mini ? "w-full" : "flex-1"} flex items-center justify-center gap-1.5 text-[12px] text-slate-400 hover:text-slate-100 px-2 py-1.5 rounded-md hover:bg-white/5 transition-colors`}
          >
            <Palette className="w-3.5 h-3.5" /> {!mini && "Aparência"}
          </button>
          <AppearanceDialog open={lookOpen} onOpenChange={setLookOpen} />
          {mini && (
            <button data-testid="logout-btn-mini" onClick={logout} title="Sair"
                    className="mt-1 w-full flex items-center justify-center px-2 py-1.5 rounded-md text-slate-400 hover:text-red-300 hover:bg-red-500/10 transition-colors">
              <LogOut className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 min-h-0 flex flex-col relative pb-[env(safe-area-inset-bottom)] md:pb-0">
        {!onTerminal && <InstallHint />}
        <div className={`flex-1 min-h-0 flex flex-col ${onTerminal ? "hidden" : ""}`}>
          <Outlet />
        </div>
        {!isViewer && canUse(user, "terminal") && <TerminalWorkspace visible={onTerminal} />}
      </main>
      {!isViewer && canUse(user, "assistant") && <AssistantChat hideButton={onTerminal || hidden} isAdmin={user?.role === "admin"} />}
      <Toaster theme={uiTheme} richColors position={isMobile ? "top-center" : "top-right"} />
    </div>
  );
}


/** Grupo do menu. Menu aberto: clicar fixa aberto/fechado; passar o mouse abre por baixo enquanto o mouse estiver nele.
 *  Menu encolhido: passar o mouse (ou clicar) mostra os submenus ao lado. O grupo da tela atual fica sempre aberto. */
function NavGroup({ g, mini, isMobile, pathname, open, setOpen, renderItem, badge }) {
  const [hover, setHover] = useState(false);
  const [pos, setPos] = useState(null);
  const timer = React.useRef(null);
  const btn = React.useRef(null);
  const active = g.items.some(it => pathname === it.to || pathname.startsWith(it.to + "/"));
  const pinned = open[g.group];
  const base = pinned === undefined ? active : pinned;          // aberto "fixo" (clique) ou por ser a tela atual
  const expanded = !mini && (base || hover);
  const place = () => { const r = btn.current?.getBoundingClientRect(); if (r) setPos({ left: r.right + 8, top: Math.min(r.top, window.innerHeight - 40 - g.items.length * 34) }); };
  const enter = () => { if (isMobile) return; clearTimeout(timer.current); if (mini) place(); timer.current = setTimeout(() => setHover(true), mini ? 0 : 120); };
  const leave = () => { clearTimeout(timer.current); timer.current = setTimeout(() => setHover(false), mini ? 200 : 250); };
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => { setHover(false); }, [pathname]);
  const toggle = () => { if (mini) { place(); setHover(h => !h); return; } setOpen(o => ({ ...o, [g.group]: !base })); setHover(false); };
  return (
    <div onMouseEnter={enter} onMouseLeave={leave} className="relative" data-testid={`nav-group-${g.group}`}>
      <button ref={btn} onClick={toggle} title={mini ? g.label : undefined} aria-expanded={expanded || (mini && hover)}
              className={`relative w-full flex items-center gap-2.5 ${mini ? "justify-center px-0" : "px-2.5"} py-2.5 md:py-[7px] rounded-md text-sm md:text-[13.5px] transition-colors ${
                active ? "text-slate-100" : "text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]"}`}>
        <g.icon className="w-4 h-4 shrink-0 opacity-90" />
        {!mini && <span className="truncate flex-1 text-left">{g.label}</span>}
        {badge != null && <span className={mini ? "absolute -top-1 -right-1 text-[9px] font-mono px-1 rounded bg-emerald-500/25 text-emerald-300"
          : "text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"}>{badge}</span>}
        {!mini && <ChevronDown className={`w-3.5 h-3.5 shrink-0 text-slate-500 transition-transform ${expanded ? "" : "-rotate-90"}`} />}
        {mini && active && <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-full bg-brand-soft" />}
      </button>
      {expanded && <div className="space-y-px pb-1" data-testid={`nav-sub-${g.group}`}>{g.items.map(it => renderItem(it, true))}</div>}
      {mini && hover && pos && (
        <div className="fixed z-50 w-52 rounded-md border border-line2 bg-panel shadow-2xl p-1.5 space-y-px before:absolute before:-left-3 before:top-0 before:bottom-0 before:w-3"
             style={{ left: pos.left, top: Math.max(8, pos.top) }} data-testid={`nav-fly-${g.group}`}>
          <div className="px-2.5 pt-1 pb-1.5 text-[11px] uppercase tracking-wide text-slate-500">{g.label}</div>
          {g.items.map(it => renderItem(it, true))}
        </div>
      )}
    </div>
  );
}
