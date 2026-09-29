import React, { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("bastion_token");
    if (!token) { setLoading(false); return; }
    api.get("/auth/me").then((r) => setUser(r.data))
      .catch(() => { localStorage.removeItem("bastion_token"); })
      .finally(() => setLoading(false));
  }, []);

  const refreshMe = async () => {
    const { data } = await api.get("/auth/me");
    setUser(data);
    return data;
  };

  // guarda um token novo (login, troca de senha, "encerrar outras sessões", desativar 2FA)
  const applySession = async (data) => {
    if (!data?.token) return;
    localStorage.setItem("bastion_token", data.token);
    if (data.user) localStorage.setItem("bastion_user", JSON.stringify(data.user));
    try { await refreshMe(); } catch { setUser(data.user); }
  };

  // devolve {need_totp: true} quando a conta tem 2FA e o código ainda não foi enviado
  const login = async (email, password, totp) => {
    const { data } = await api.post("/auth/login", { email, password, totp: totp || undefined });
    if (data.need_totp) return { need_totp: true };
    await applySession(data);
    return data.user;
  };

  const logout = () => {
    localStorage.removeItem("bastion_token");
    localStorage.removeItem("bastion_user");
    setUser(null);
    window.location.href = "/login";
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, setUser, refreshMe, applySession }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
