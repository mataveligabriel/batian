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

## Implemented (Jun 2026) — Cadeia de saltos, senhas, tipos, CSV, deploy
- Agentes com modo `direct`/`reverse` (túnel reverso com tunnel_port auto ≥20001 e par de chaves ed25519 por agente) e `parent_agent_id` (cadeia multi-hop montada em `_device_hops`/`_agent_chain`)
- Senha por device/agente + senha padrão global (Fernet via `vault.py`, derivada de JWT_SECRET; API só retorna `has_password`)
- `device_type` → algoritmos legados (`LEGACY_ALGS`) e execução em shell no batch (`SHELL_EXEC_TYPES`, desliga paginação)
- Endpoints: `/api/bastion/settings`, `/api/bastion/setup-script`, `/api/bastion/authorized-keys?token=`, `/api/agents/{id}/test`, `/api/agents/{id}/install-script` (bash + powershell), `/api/devices/import`
- Ping de devices/agentes atravessa a cadeia (`tcp_check` via open_connection no último hop)
- UI: Agents.jsx reescrito (modo, pai, instalador com abas, Configurar Bastion), Devices.jsx (tipo, senha, Importar CSV via `ImportDevicesDialog.jsx`), SshKey.jsx (senha padrão)
- Deploy: `/app/deploy/` (docker-compose host-network, Caddy HTTPS, .env.example, README pt-BR), Dockerfiles em backend/ e frontend/; `SEED_SAMPLE_DATA` controla seeds de exemplo
- Testes: iteration_2.json 100% (20 backend + 13 UI); suite reutilizável em /app/backend/tests/test_bastion_features.py

## Implemented (Jun 2026) — Sessões de terminal persistentes + deploy fixes
- Layout virou rota compartilhada (`<Route element={<ProtectedShell/>}>` + `Outlet`); `TerminalWorkspace` vive no Layout, sempre montado, oculto via visibility fora de `/terminal`
- `TerminalContext` (tabs/active/openTab/closeTab); `pages/Terminal.jsx` é só rota fina (`/terminal/:deviceId` abre aba e redireciona para `/terminal`)
- Badge de sessões abertas no menu, botão Reconectar por aba
- Deploy: requirements.txt mínimo (sem pacotes internos), frontend Dockerfile sem exigir yarn.lock, `deploy/env.example` (não `.env.example` — gitignore), README com Debian
- Testes: iteration_3 (deps/regressão backend 100%), iteration_4 (persistência UI 100%)

## Implemented (Jun 2026) — Automação, alertas, backups, favoritos, edição de scripts
- `automation.py`: send_alert (Telegram sendMessage via httpx + webhook genérico), store_backup (sha256/changed), unified_diff, `Scheduler` (loop 30s: ping pela cadeia a cada N min com detecção de transição → alerta; backup diário na hora configurada, estado em config.backup_state)
- Endpoints: `/api/automation/settings` GET/PUT (token Telegram criptografado, `has_telegram_token`), `/api/automation/test-alert`, `/api/automation/ping-now`, `/api/alerts`, `/api/backups/run|summary|{id}|{id}/diff/{other}|DELETE`
- Comandos de backup por tipo (`BACKUP_COMMANDS`) + override por device (`backup_command`, `backup_enabled`)
- Scripts: campo `quick` (favorito) + edição no Batch.jsx; barra de favoritos no TerminalWorkspace envia para a aba ativa
- Páginas novas: Backups.jsx (versões, diff colorido, visualizar/baixar), Automation.jsx; nav "Backups" e "Automação"
- Deploy: httpx no requirements.txt (bug 502 no VPS), startup espera Mongo (30x2s), healthcheck no compose
- Testes: iteration_5.json 100% (19 backend + UI); suite /app/backend/tests/test_new_batch.py

## Implemented (Jun 2026) — Isolamento por usuário, Telnet, login clean
- `owner_id` em Device: operadores só veem/usam os próprios (`_scope`, `_get_device_for`); admin vê tudo e escolhe o dono no formulário; sessões/stats/backups também escopados; WS terminal valida dono via role do JWT
- `protocol: ssh|telnet` por device; `telnet_service.TelnetClientWrapper` (negociação IAC, NAWS, auto-login com user/senha, run_command shell-mode) direto ou via último hop SSH (`open_connection`); CSV aceita coluna `protocol`
- Login.jsx limpo (logo BASTION + email/senha, sem credenciais padrão)
- Lab: `/app/sshd_test/fake_telnet.py` (127.0.0.1:2323, labuser/labpass); ids do lab em `/app/sshd_test/ids`
- Testes: iteration_6.json 100% (15 backend + UI)

## Implemented (Jun 2026) — Agentes por usuário, reset/troca de senha
- `owner_id` em Agent (`_own`, `_get_agent_for`, `_check_parent`): qualquer usuário cria/gerencia seus agentes; operadores não veem/usam agentes de outros (pai/agent_id validados); admin vê tudo e atribui dono
- `PUT /api/users/{id}` (admin: nome/papel/senha, bloqueia auto-rebaixamento); `POST /api/auth/change-password` (senha atual + nova ≥6)
- UI: Users.jsx (editar/resetar senha), `ChangePasswordDialog` no sidebar ("Trocar senha"), Agents.jsx com seletor de dono (admin) e botões para todos
- Testes: iteration_7.json 100% (backend via curl + 6 fluxos UI)
- Automação/alertas restritos a admin (nav adminOnly, rota protegida, endpoints require_admin); stats de agentes escopados por usuário
- Isolamento total: `_scope` = `{owner_id: user}` para TODOS (admins inclusive); sem seletor de dono; sessões/stats/backups/WS sempre por usuário. Migração no startup: devices/agents sem owner_id → admin do ADMIN_EMAIL. Verificado via API com segundo admin (0 devices/agents/sessões, 404 em recursos alheios)
