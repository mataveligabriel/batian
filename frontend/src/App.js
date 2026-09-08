import React, { useEffect } from "react";
import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/context/AuthContext";
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

function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return <Navigate to={user ? "/dashboard" : "/login"} replace />;
}

function ProtectedShell({ children, adminOnly }) {
  return (
    <ProtectedRoute adminOnly={adminOnly}>
      <Layout>{children}</Layout>
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
          <Route path="/dashboard" element={<ProtectedShell><Dashboard /></ProtectedShell>} />
          <Route path="/devices" element={<ProtectedShell><Devices /></ProtectedShell>} />
          <Route path="/terminal" element={<ProtectedShell><Terminal /></ProtectedShell>} />
          <Route path="/terminal/:deviceId" element={<ProtectedShell><Terminal /></ProtectedShell>} />
          <Route path="/batch" element={<ProtectedShell><Batch /></ProtectedShell>} />
          <Route path="/agents" element={<ProtectedShell><Agents /></ProtectedShell>} />
          <Route path="/ssh-key" element={<ProtectedShell><SshKey /></ProtectedShell>} />
          <Route path="/sessions" element={<ProtectedShell><Sessions /></ProtectedShell>} />
          <Route path="/users" element={<ProtectedShell adminOnly><Users /></ProtectedShell>} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
