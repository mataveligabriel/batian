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
import Automation from "@/pages/Automation";
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
          <Route element={<ProtectedShell />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/terminal" element={<Terminal />} />
            <Route path="/terminal/:deviceId" element={<Terminal />} />
            <Route path="/web" element={<WebAccess />} />
            <Route path="/batch" element={<Batch />} />
            <Route path="/lg" element={<LookingGlass />} />
            <Route path="/rpki" element={<Rpki />} />
            <Route path="/rdp" element={<Rdp />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/ssh-key" element={<SshKey />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/backups" element={<Backups />} />
            <Route path="/maps" element={<Maps />} />
            <Route path="/dashboards" element={<Dashboards />} />
            <Route path="/flow" element={<Flow />} />
            <Route path="/automation" element={<ProtectedRoute adminOnly><Automation /></ProtectedRoute>} />
            <Route path="/users" element={<ProtectedRoute adminOnly><Users /></ProtectedRoute>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
