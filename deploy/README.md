# SSH Bastion Central — Guia de Produção (VPS próprio)

## Instalação rápida (máquina do zero, Debian/Ubuntu, como root)
```bash
curl -fsSL https://raw.githubusercontent.com/mataveligabriel/batian/main/deploy/install.sh | bash
```
O script instala Docker, libera o firewall, clona o repositório em `/opt/bastion`, pergunta domínio/IP e
credenciais do admin, gera o `.env` e sobe tudo. Para atualizar após qualquer alteração no código:
```bash
bash /opt/bastion/deploy/update.sh
```

---

O Bastion precisa rodar num servidor **com IP público e sshd acessível**, porque os agentes
(sua máquina com FortiClient, filiais etc.) abrem túneis SSH reversos até ele. Hospedagens
serverless/containers gerenciados não recebem SSH de entrada — use um VPS (Ubuntu 22.04/24.04,
1 vCPU / 2 GB já atende centenas de devices).

## 1. Pré-requisitos no VPS
**Ubuntu 22.04/24.04**
```bash
apt update && apt install -y docker.io docker-compose-v2 git curl
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
```
**Debian 11/12**
```bash
apt update && apt install -y ca-certificates curl gnupg git ufw
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
```
No Debian 11, confira se `/etc/ssh/sshd_config` contém `Include /etc/ssh/sshd_config.d/*.conf` (o script de preparação do Bastion depende disso).
- DNS: aponte `bastion.seudominio.com` (A) para o IP do VPS (HTTPS automático via Let's Encrypt).
- Sem domínio? Use o IP em `BASTION_DOMAIN=http://IP_DO_VPS` e `PUBLIC_URL=http://IP_DO_VPS` (sem TLS).

## 2. Código e configuração
```bash
git clone <SEU_REPO> /opt/bastion && cd /opt/bastion/deploy
cp env.example .env
nano .env        # PUBLIC_URL, BASTION_DOMAIN, JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, CORS_ORIGINS
```
Gere o segredo: `openssl rand -hex 32`.

## 3. Subir
```bash
docker compose up -d --build
docker compose logs -f backend      # aguarde "Application startup complete"
```
Acesse `https://bastion.seudominio.com` e faça login com ADMIN_EMAIL / ADMIN_PASSWORD.

O backend roda em `network_mode: host` de propósito: assim ele alcança `127.0.0.1:<porta_do_tunel>`,
onde o sshd do VPS entrega os túneis reversos dos agentes.

## 4. Preparar o Bastion para receber os agentes (uma vez)
1. No painel → **Agentes** → **Configurar Bastion**: preencha host público (`bastion.seudominio.com`),
   porta SSH do VPS (22) e usuário dos túneis (`bastion`). Salve.
2. Copie o **script de preparação** exibido e rode como root no VPS. Ele:
   - cria o usuário `bastion` sem shell, só com permissão de túnel reverso;
   - sincroniza a cada minuto as chaves públicas dos agentes (`/api/bastion/authorized-keys`).

## 5. Cadastrar a cadeia de saltos
1. **Agentes → Novo Agente**, modo **Túnel reverso**: `gw-minha-maquina` (usuário = seu usuário local,
   porta SSH local 22). Salve → **Instalar** → copie o script (Linux/macOS ou Windows) e rode na
   sua máquina. Em ~1 min o card fica **online**.
2. **Novo Agente**, modo **Direto**: `jump-vpn` com o IP do jump host da VPN, usuário/senha ou chave,
   **agente pai = gw-minha-maquina**. Clique **Testar SSH** → deve mostrar `gw-minha-maquina → jump-vpn`.
3. **Equipamentos → Importar CSV** (modelo disponível no botão "Baixar modelo") com a coluna
   `agent=jump-vpn`, tipo (`cisco`, `huawei`, `mikrotik`, ...) e credenciais — ou deixe a senha vazia
   e configure a **senha padrão RADIUS/TACACS** em **Chave SSH Global**.

## 6. Operação
- Atualizar: `cd /opt/bastion && git pull && cd deploy && docker compose up -d --build`
- Backup do Mongo: `docker compose exec mongo mongodump --archive > backup-$(date +%F).archive`
- Logs: `docker compose logs -f backend`
- Requisitos na máquina-agente: servidor SSH ativo (Windows: recurso "Servidor OpenSSH";
  Linux: `openssh-server`; macOS: Login Remoto) e acesso de saída à porta 22 do VPS.

## 7. Assistente IA no Telegram (Claude)
Operar os equipamentos conversando com o bot do Telegram ("em quais PONs está a ONU de MAC X?",
"mostra os peers BGP da borda Y", "desativa a GE0/0/5 do switch Z").

1. Crie uma chave em https://console.anthropic.com (**API Keys**) e coloque créditos na conta — o uso
   da API é cobrado à parte, por tokens (o consumo do mês aparece no card).
2. **Automação → Notificações**: token do bot (via @BotFather) já configurado.
3. **Automação → Assistente IA**: cole a chave, escolha o modelo, ative e clique **Testar Claude**.
4. Cada pessoa manda `/id` para o bot; cadastre o ID dela vinculado a um usuário do Bastion. Ela só
   enxerga os equipamentos desse usuário.
5. Segurança: comandos de leitura (`show`, `display`, `ping`, Mikrotik `print`…) rodam direto;
   o backend recusa qualquer outro comando fora de uma **proposta**, que só executa após o clique em
   **✅ Confirmar** por quem pediu (válida por 15 min, uma única vez). Tudo fica em
   **Automação → Últimas ações do assistente** e no **Histórico**. "Permitir alterações" desligado = só consultas.
6. O VPS precisa de saída HTTPS para `api.anthropic.com` e `api.telegram.org`. O bot usa long polling
   (não precisa de webhook nem de porta aberta) — não use o mesmo token em outro sistema com webhook.

## 8. Mapas de rede (weathermap) e alarmes de interface
Menu **Mapas de rede**: crie vários mapas (backbone, POPs…), arraste os equipamentos, ligue-os escolhendo as
interfaces e veja o tráfego ao vivo (cor = utilização). Aba **Alarmes de interface**: marque as interfaces
importantes; queda/volta vai para o Telegram de **Automação → Notificações**.

- Tráfego e status vêm por **SNMP v2c** (contadores 64 bits). Informe a community no equipamento
  (Equipamentos → editar → *SNMP community*) ou uma padrão em **Mapas → Configurações**.
- **Acesso direto:** libere SNMP (UDP 161) no equipamento para o IP do servidor Bastion.
- **Equipamento atrás de agente:** SNMP é UDP e não passa no túnel SSH — o Bastion roda o `snmpget` no próprio
  agente. Instale no agente: `sudo apt install snmp` e libere o SNMP do equipamento para o IP do agente.
- O Bastion lê só as interfaces usadas nos mapas e as marcadas para alarme (padrão: a cada 30 s).
  O histórico fica 48 h no Mongo e é apagado sozinho.

## 9. Dashboards (consumo) e sinal óptico por lane
Menu **Dashboards**: crie grupos (ex.: *Borda*, *Clientes*) com vários dashboards. Cada gráfico pode ser:
- **Tráfego**: consumo atual de entrada/saída em destaque, % da capacidade (contratada ou da porta), p95 e pico
  do período, gráfico de 1h a 30 dias (pontos agregados automaticamente).
- **Sinal óptico**: RX/TX de cada lane (interfaces 40G/100G têm 4 lanes), com limites de atenção/crítico.

A óptica é lida pela **CLI via SSH** (funciona atrás de agente) a cada 5 min, nas interfaces dos links dos
mapas e dos gráficos ópticos. Os comandos por fabricante ficam em **Dashboards → Configurações da óptica**;
o botão **Testar leitura** mostra a saída bruta para ajustar ao seu firmware.
O histórico (tráfego e óptica) é guardado por 7 dias por padrão — ajuste em **Mapas → Configurações**.
