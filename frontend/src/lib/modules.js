// Módulos que o administrador libera por usuário (o servidor aplica a mesma regra em cada chamada).
// Sem lista no usuário = todos os módulos. Administrador sempre tem tudo; o perfil View tem regra própria.
export const MODULES = [
  { key: "incidents", label: "Incidentes", path: "/incidents", desc: "alarmes da rede (Zabbix, LibreNMS, BastiON) e incidentes de segurança do SOC; reconhecer e resolver" },
  { key: "ipam", label: "Inventário e IPAM", path: "/ipam", desc: "prefixos, IPs em uso, blocos livres, inventário e NetBox" },
  { key: "compliance", label: "Conformidade", path: "/compliance", desc: "regras de configuração e inventário de firmware" },
  { key: "gpon", label: "GPON", path: "/gpon", desc: "sinal e estado das ONUs, queda em massa, busca do cliente" },
  { key: "bgpmon", label: "Monitor BGP", path: "/bgp-monitor", desc: "sessões BGP e sequestro/vazamento dos prefixos" },
  { key: "maint", label: "Janelas de manutenção", path: "/maint", desc: "manutenção programada com pré/pós-check" },
  { key: "sla", label: "SLA e disponibilidade", path: "/sla", desc: "disponibilidade por enlace/POP/cliente e relatório mensal" },
  { key: "probes", label: "Sondas ativas", path: "/probes", desc: "ping, DNS, HTTP e TCP do servidor e dos jumps" },
  { key: "logs", label: "Syslog e Traps", path: "/logs", desc: "logs e traps SNMP; regras que abrem incidentes" },
  { key: "devices", label: "Equipamentos", path: "/devices", desc: "cadastrar, editar, importar e testar" },
  { key: "terminal", label: "Terminal SSH", path: "/terminal", desc: "abrir sessão SSH/Telnet" },
  { key: "web", label: "Acesso Web", path: "/web", desc: "abrir a página web dos equipamentos" },
  { key: "winbox", label: "Winbox", path: "/winbox", desc: "túnel do Winbox até os MikroTik (jump servers)" },
  { key: "rdp", label: "Área de Trabalho Remota", path: "/rdp", desc: "sessões RDP (Windows) pelo navegador" },
  { key: "maps", label: "Mapas de rede", path: "/maps", desc: "mapas, tráfego, alarmes e análise" },
  { key: "te", label: "Engenharia de tráfego", path: "/te", desc: "OSPF/SPF, LSP (CSPF), simulação e otimização de custos" },
  { key: "dashboards", label: "Dashboards", path: "/dashboards", desc: "consumo e sinal óptico" },
  { key: "flow", label: "Análise de Flow", path: "/flow", desc: "tráfego por AS/IP, ataques, mitigação" },
  { key: "batch", label: "Execução em Lote", path: "/batch", desc: "comandos em vários equipamentos" },
  { key: "lg", label: "Looking Glass", path: "/lg", desc: "ping, traceroute e rota BGP" },
  { key: "rpki", label: "RPKI", path: "/rpki", desc: "ROAs e certificação dos ASNs" },
  { key: "oltgen", label: "Scripts de OLT", path: "/olt-script", desc: "scripts de OLT ZTE e Huawei" },
  { key: "agents", label: "Agentes Remotos", path: "/agents", desc: "jump hosts, túneis e VPNs" },
  { key: "sshkey", label: "Chave SSH Global", path: "/ssh-key", desc: "chave e credencial padrão" },
  { key: "sessions", label: "Histórico", path: "/sessions", desc: "sessões e comandos executados" },
  { key: "backups", label: "Backups", path: "/backups", desc: "configurações, comparação e busca" },
  { key: "assistant", label: "Assistente IA", path: null, desc: "chat e voz (consulta e propõe comandos)" },
];
export const ALL_KEYS = MODULES.map(m => m.key);
export const PATH_MODULE = { ...Object.fromEntries(MODULES.filter(m => m.path).map(m => [m.path, m.key])), "/soc/incidents": "incidents" };

// combinações prontas para começar
export const PRESETS = [
  { label: "Tudo", keys: null },
  { label: "Só monitoramento", keys: ["maps", "dashboards", "flow", "lg"] },
  { label: "Só acesso aos equipamentos", keys: ["terminal", "web", "rdp"] },
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
