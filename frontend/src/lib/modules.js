// Módulos que o administrador libera por usuário (o servidor aplica a mesma regra em cada chamada).
// Sem lista no usuário = todos os módulos. Administrador sempre tem tudo; o perfil View tem regra própria.
export const MODULES = [
  { key: "devices", label: "Equipamentos", path: "/devices", desc: "cadastrar, editar, importar e testar" },
  { key: "terminal", label: "Terminal SSH", path: "/terminal", desc: "abrir sessão SSH/Telnet" },
  { key: "web", label: "Acesso Web", path: "/web", desc: "abrir a página web dos equipamentos" },
  { key: "maps", label: "Mapas de rede", path: "/maps", desc: "mapas, tráfego, alarmes e análise" },
  { key: "dashboards", label: "Dashboards", path: "/dashboards", desc: "consumo e sinal óptico" },
  { key: "flow", label: "Análise de Flow", path: "/flow", desc: "tráfego por AS/IP, ataques, mitigação" },
  { key: "batch", label: "Execução em Lote", path: "/batch", desc: "comandos em vários equipamentos" },
  { key: "lg", label: "Looking Glass", path: "/lg", desc: "ping, traceroute e rota BGP" },
  { key: "rpki", label: "RPKI", path: "/rpki", desc: "ROAs e certificação dos ASNs" },
  { key: "agents", label: "Agentes Remotos", path: "/agents", desc: "jump hosts, túneis e VPNs" },
  { key: "sshkey", label: "Chave SSH Global", path: "/ssh-key", desc: "chave e credencial padrão" },
  { key: "sessions", label: "Histórico", path: "/sessions", desc: "sessões e comandos executados" },
  { key: "backups", label: "Backups", path: "/backups", desc: "configurações, comparação e busca" },
  { key: "assistant", label: "Assistente IA", path: null, desc: "chat e voz (consulta e propõe comandos)" },
];
export const ALL_KEYS = MODULES.map(m => m.key);
export const PATH_MODULE = Object.fromEntries(MODULES.filter(m => m.path).map(m => [m.path, m.key]));

// combinações prontas para começar
export const PRESETS = [
  { label: "Tudo", keys: null },
  { label: "Só monitoramento", keys: ["maps", "dashboards", "flow", "lg"] },
  { label: "Só acesso aos equipamentos", keys: ["terminal", "web"] },
  { label: "Operação (terminal, lote, backups)", keys: ["terminal", "web", "batch", "backups", "sessions"] },
];

export function canUse(user, key) {
  if (!user) return false;
  if (user.role === "admin") return true;
  if (user.role === "viewer") return key === "maps" || key === "dashboards";
  return !Array.isArray(user.modules) || user.modules.includes(key);
}

/** Texto curto para a lista de usuários. */
export function modulesSummary(u) {
  if (u.role !== "operator" || !Array.isArray(u.modules)) return "";
  if (u.modules.length === 0) return "só o Painel NOC";
  if (u.modules.length === ALL_KEYS.length) return "";
  const names = MODULES.filter(m => u.modules.includes(m.key)).map(m => m.label);
  return names.length <= 3 ? names.join(", ") : `${names.slice(0, 3).join(", ")} +${names.length - 3}`;
}
