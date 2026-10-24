import React, { useEffect } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { TerminalProvider } from "@/context/TerminalContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Devices from "@/pages/Devices";
import Terminal from "@/pages/Terminal";
import Batch from "@/pages/Batch";
import LookingGlass from "@/pages/LookingGlass";
import OltScript from "@/pages/OltScript";
import Winbox from "@/pages/Winbox";
import TrafficEng from "@/pages/TrafficEng";
import PublicLookingGlass from "@/pages/PublicLookingGlass";
import Rpki from "@/pages/Rpki";
import Rdp from "@/pages/Rdp";
import Agents from "@/pages/Agents";
import SshKey from "@/pages/SshKey";
import Sessions from "@/pages/Sessions";
import Users from "@/pages/Users";
import Backups from "@/pages/Backups";
import Maps from "@/pages/Maps";
import Dashboards from "@/pages/Dashboards";
import Flow from "@/pages/Flow";
import Settings from "@/pages/Settings";
import Incidents from "@/pages/Incidents";
import Logs from "@/pages/Logs";
import Probes from "@/pages/Probes";
import Sla, { SlaReport } from "@/pages/Sla";
import Maint from "@/pages/Maint";
import Bgp from "@/pages/Bgp";
import Gpon from "@/pages/Gpon";
import Compliance from "@/pages/Compliance";
import Ipam from "@/pages/Ipam";
import WebAccess from "@/pages/WebAccess";

function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return <Navigate to={user ? "/dashboard" : "/login"} replace />;
}

function ProtectedShell() {
  return (
    <ProtectedRoute>
      <TerminalProvider>
        <Layout />
      </TerminalProvider>
    </ProtectedRoute>
  );
}

function App() {
  // Looking Glass público: página avulsa, sem login e sem o resto do app
  if (window.location.pathname.replace(/\/+$/, "") === "/looking-glass") return <PublicLookingGlass />;
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<Login />} />
          <Route path="/sla/report" element={<ProtectedRoute><SlaReport /></ProtectedRoute>} />
          <Route element={<ProtectedShell />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/probes" element={<Probes />} />
            <Route path="/sla" element={<Sla />} />
            <Route path="/maint" element={<Maint />} />
            <Route path="/bgp-monitor" element={<Bgp />} />
            <Route path="/gpon" element={<Gpon />} />
            <Route path="/compliance" element={<Compliance />} />
            <Route path="/ipam" element={<Ipam />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/terminal" element={<Terminal />} />
            <Route path="/terminal/:deviceId" element={<Terminal />} />
            <Route path="/web" element={<WebAccess />} />
            <Route path="/batch" element={<Batch />} />
            <Route path="/lg" element={<LookingGlass />} />
            <Route path="/olt-script" element={<OltScript />} />
            <Route path="/winbox" element={<Winbox />} />
            <Route path="/te" element={<TrafficEng />} />
            <Route path="/rpki" element={<Rpki />} />
            <Route path="/rdp" element={<Rdp />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/ssh-key" element={<SshKey />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/backups" element={<Backups />} />
            <Route path="/maps" element={<Maps />} />
            <Route path="/dashboards" element={<Dashboards />} />
            <Route path="/flow" element={<Flow />} />
            <Route path="/settings" element={<ProtectedRoute adminOnly><Settings /></ProtectedRoute>} />
            {/* Automação foi para Configurações (links antigos e alertas continuam funcionando) */}
            <Route path="/automation" element={<Navigate to="/settings?tab=automacao" replace />} />
            <Route path="/users" element={<ProtectedRoute adminOnly><Users /></ProtectedRoute>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
