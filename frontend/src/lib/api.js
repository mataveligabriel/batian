import axios from "axios";

// Em produção o Caddy serve o app e a /api no mesmo endereço: usa sempre a origem atual.
// Assim o mesmo build funciona por http://IP, por https://dominio e no app instalado (PWA/Windows).
// REACT_APP_BACKEND_URL só vale no desenvolvimento (npm start na porta 3000).
const BASE = window.location.port === "3000" ? (process.env.REACT_APP_BACKEND_URL || "") : "";
export const API_BASE = `${BASE}/api`;

export const api = axios.create({
  baseURL: API_BASE,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("bastion_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem("bastion_token");
      localStorage.removeItem("bastion_user");
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  }
);

export function wsUrl(path) {
  const httpBase = BASE || window.location.origin;
  const wsBase = httpBase.replace(/^http/, "ws");
  return `${wsBase}/api${path}`;
}

export function formatApiError(err) {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || "Erro inesperado";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e) => (typeof e?.msg === "string" ? e.msg : JSON.stringify(e))).join(" ");
  return JSON.stringify(detail);
}
