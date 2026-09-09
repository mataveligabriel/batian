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
import Agents from "@/pages/Agents";
import SshKey from "@/pages/SshKey";
import Sessions from "@/pages/Sessions";
import Users from "@/pages/Users";
import Backups from "@/pages/Backups";
import Automation from "@/pages/Automation";

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
            <Route path="/batch" element={<Batch />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/ssh-key" element={<SshKey />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/backups" element={<Backups />} />
            <Route path="/automation" element={<Automation />} />
            <Route path="/users" element={<ProtectedRoute adminOnly><Users /></ProtectedRoute>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
