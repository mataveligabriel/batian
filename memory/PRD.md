# SSH Bastion Central — PRD

## Problem Statement (original)
Sistema para acessar via SSH centenas de equipamentos em redes distintas através de um proxy (sem VPN), com terminal em segundo plano dentro do sistema trazendo o acesso aos devices por cima.

## User Choices
- Bastion SSH central + agentes instalados em cada rede remota com túnel reverso
- Terminal web interativo (xterm.js via WebSocket) + execução em lote
- Login email/senha (JWT) com papéis admin/operador
- Chave SSH global única para todos os equipamentos
- Dashboard com status/ping, histórico de sessões, busca por tags, execução de scripts em múltiplos devices
- Porta SSH configurável por device

## Architecture
- **Backend**: FastAPI + Motor (MongoDB) + asyncssh + JWT (Bearer)
  - `server.py` — todos os endpoints `/api/*` + WebSocket terminal
  - `auth.py` — bcrypt + JWT + role guards (`require_admin`)
  - `ssh_service.py` — `SSHClientWrapper` com ProxyJump nativo (agente = jump host) e `tcp_ping`
  - `models.py` — Pydantic models
- **Frontend**: React + React Router + Tailwind + shadcn/ui + xterm.js + sonner
  - Layout com sidebar (Painel, Equipamentos, Terminal, Batch, Agentes, Chave SSH, Histórico, Usuários)
  - Terminal multi-abas com WebSocket + xterm.js
- **DB**: MongoDB (`users`, `devices`, `agents`, `scripts`, `sessions`, `config`)

## Implemented (Feb 2026)
- JWT auth Bearer com admin seeding e role guards
- CRUD de Devices (host, porta customizada, usuário, tags, agente associado)
- CRUD de Agents (jump hosts) + script de instalação `curl | bash`
- Terminal SSH interativo via WebSocket (asyncssh + ProxyJump quando `agent_id`)
- Execução em lote paralela com resultados por host + sessions log
- Scripts salvos (CRUD)
- Chave SSH global (gerar RSA-2048, salvar, visualizar)
- Histórico de sessões (terminal + batch) com duração
- Dashboard com stats + ações rápidas + ping global
- Gestão de usuários (admin only)
- Seed data: 4 devices, 3 agentes, 3 scripts
- 33/33 testes de backend passaram

## Implemented (Jun 2026) — Validação E2E do Terminal
- Lab SSH real no container: `sshd` local em 127.0.0.1:2222 (config em `/app/sshd_test/`, chave global autorizada em `/root/.ssh/authorized_keys`). Reiniciar após restart do pod: `mkdir -p /run/sshd && /usr/sbin/sshd -f /app/sshd_test/sshd_config`
- Devices de lab: `lab-direto` (direto) e `lab-via-agente` (ProxyJump via agente `agent-local-lab`)
- Terminal interativo validado no navegador (direto + ProxyJump, multi-abas, input/output, histórico de sessão com duração)
- Batch validado contra SSH real nos dois caminhos
- Fix: migrado para `@xterm/xterm` 5.5 (corrige TypeError `dimensions` no StrictMode), CSS do xterm importado, abas inativas usam `invisible` em vez de `hidden` (fit correto)

## Backlog / P1
- Frontend E2E testing (browser) — terminal já validado manualmente; demais páginas pendentes
- Gravação/replay de sessão SSH
- Permissões por device/tag por operador
- Alertas quando agente cai (webhook/email)
- Grafo topológico de agentes → devices
