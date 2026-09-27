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

## 10. App no celular (PWA) e notificações dos alarmes
O Bastion pode ser instalado no celular como um app, sem loja:
- **iPhone/iPad (Safari):** abra o endereço do Bastion → **Compartilhar** → **Adicionar à Tela de Início**.
- **Android (Chrome):** menu ⋮ → **Instalar app**.

Abrindo pelo ícone ele fica em tela cheia, com menu em gaveta, mapas com toque (arrastar e pinça para zoom),
dashboards em uma coluna e o terminal com uma barra de teclas extra (Esc, Tab, setas, Ctrl+C/Z/D…).

**Alarmes no celular (push):** no menu lateral, toque em **Alertas neste aparelho**. Cada usuário ativa nos
aparelhos que quiser; os alarmes de interface e de equipamento chegam como notificação (junto do Telegram).
Tocar no alarme abre o mapa. No iPhone é preciso iOS 16.4+ e ativar **pelo app instalado** (não pelo Safari).

⚠ **Push e instalação completa exigem HTTPS.** Acessando por `http://IP` o app funciona, mas sem notificações.
Para ter HTTPS (certificado grátis e automático do Let's Encrypt):

1. Crie um nome apontando para o IP do VPS — um subdomínio do seu próprio domínio (registro **A**
   `bastion.suaempresa.com.br → IP_DO_VPS`) ou, sem mexer em DNS, use o `sslip.io`:
   `IP-COM-TRACOS.sslip.io` (ex.: IP `177.10.20.30` → `177-10-20-30.sslip.io`).
2. No servidor, edite `/opt/bastion/deploy/.env`:
   ```
   BASTION_DOMAIN=177-10-20-30.sslip.io
   BASTION_ALT=http://177.10.20.30
   PUBLIC_URL=https://177-10-20-30.sslip.io
   CORS_ORIGINS=https://177-10-20-30.sslip.io,http://177.10.20.30
   ```
   `BASTION_ALT` mantém o acesso antigo por IP funcionando (app do Windows) enquanto você migra.
3. Portas **80 e 443** liberadas no firewall/VPS e rode `bash /opt/bastion/deploy/update.sh`.
   Em ~1 minuto o Caddy emite o certificado; acesse `https://…` e instale no celular por esse endereço.
4. No app do Windows: menu **Bastion → Alterar servidor…** e informe o endereço `https://…`.

## 11. Usuários, perfil View e envio de itens
**Papéis:** *Administrador* (gerencia usuários e configurações), *Operador* (acesso completo aos próprios
equipamentos) e **View** — só **Painel NOC, Dashboards e Mapas**, em modo leitura. O View não abre terminal,
não vê senhas nem outros menus; o bloqueio é feito no servidor (qualquer outra rota responde 403).

- **Liberar o que o View enxerga:** *Usuários → Acesso* (na linha do usuário View) → marque mapas e dashboards
  de qualquer usuário. Ele vê ao vivo, sem cópia: o que você mudar no mapa aparece para ele.
- **Enviar equipamentos, mapas e dashboards:** selecione equipamentos em *Equipamentos → Enviar para usuário*,
  ou use **Enviar** em Mapas/Dashboards, ou *Usuários → Transferir* (admin: enviar para o usuário ou trazer dele).
  É uma **cópia**: mapas e dashboards levam os equipamentos que usam (e a cadeia de agentes); equipamento que o
  destino já tem com o mesmo IP:porta é reaproveitado. Operadores podem enviar para o administrador.
- **Painel NOC:** cada usuário escolhe quais mapas e dashboards aparecem no painel (botão *Escolher painéis*),
  com abas, rotação automática e tela cheia para a TV do NOC.

## 12. Análise de rede (experimental)
Em **Mapas → Analisar**, o Bastion lê por SNMP cada equipamento do mapa e gera um diagnóstico com relatório:
- **OSPF:** custo de cada ponta do enlace (mostrado no mapa), custo × banda (a referência que a sua rede usa é
  descoberta sozinha), custo assimétrico, enlaces paralelos sem ECMP, área/tipo de rede/timers/MTU divergentes,
  adjacência que não chega a FULL, router-ID duplicado, todos os custos = 1 (referência padrão de 100 Mbps).
- **BGP:** sessões caídas (com o último erro: hold timer, AS errado, limite de prefixos…), reiniciadas há pouco, instáveis.
- **Interfaces:** erros e descartes por segundo (duas leituras), interfaces descritas que estão DOWN, enlaces acima de 70/90%,
  lanes ópticas apagadas ou desbalanceadas.
- **Cenários:** para cada enlace, quem fica isolado se ele cair (ponto único de falha) e qual enlace congestiona com o
  tráfego desviado; equipamentos que são ponto único de falha. Clicando no mapa dá para **simular** queda de enlace,
  parada de equipamento ou **outro custo OSPF** e ver para onde vai o tráfego.
- **MPLS (pela CLI, via SSH):** enlace OSPF **sem LDP** (buraco negro para as VPNs — crítico se carrega tráfego,
  atenção se é reserva), sessão LDP entre as pontas de cada enlace, sessões LDP caídas, e o cenário "se o enlace X
  cair, o tráfego passa por um enlace sem LDP e as VPNs param". Serviços: **VPWS/l2vc** (VC down, separando AC do
  cliente caído de PW caído; VC configurado só de um lado), **VPLS/VSI** (down ou com PW down), **L3VPN**
  (vpn-instance/VRF sem rotas) e **MP-BGP VPNv4** caído ou sem rotas.
  Comandos padrão para Huawei, Juniper, Cisco (IOS/XE/XR), Datacom DMOS e ZTE — editáveis em
  *Analisar → opções → comandos MPLS por fabricante*, com botão **Testar** que mostra a saída real do equipamento
  (útil para ajustar DMOS/ZTE e firmwares diferentes). A saída bruta de cada análise fica guardada ("ver saída da CLI").
- **Relatório:** botão *Relatório* baixa um HTML completo (abre em qualquer navegador; Ctrl+P salva em PDF).

Sem custo: usa só SNMP v2c com MIBs padrão (OSPF-MIB, BGP4-MIB, IF-MIB, IP-MIB) e nada é alterado nos equipamentos.
Limitações desta versão: BGP4-MIB traz só sessões IPv4 da instância principal (IPv6/VPN e contagem de prefixos
dependem de MIB do fabricante); OSPFv3 não entra; no Huawei com mais de um processo OSPF, o SNMP mostra o processo
ligado com `ospf mib-binding <processo>`. A estimativa de carga nos cenários é de 1ª ordem (sem NetFlow).

## 13. Análise de Flow (NetFlow v5/v9, IPFIX e sFlow)
Menu **Análise de Flow** (abaixo de Dashboards). Um coletor próprio (container `flow`, sem custo de licença) recebe
os flows dos roteadores, e a tela mostra gráficos no estilo Kentik/Akvorado:
- **Explorar:** escolha as interfaces (trânsitos, PNI, IX, CDN…) e agrupe por interface, AS de origem/destino,
  prefixo, IP, protocolo, porta, interface par ou conteúdo. Filtro por ASN, porta ou **blocos de IP** (com a opção
  "somar por bloco" para ver quanto cada bloco seu usa naquele trânsito). Tabela com média, p95, máximo e participação.
- **Conteúdos:** cadastre um conteúdo com ASNs + blocos (ex.: Google = AS15169/36040 + o bloco do cache GGC) e veja,
  interface por interface, quanto dele entra por **PNI × trânsito × IX** (conta exata, minuto a minuto). Há sugestões
  prontas (Google, Meta, Netflix, Akamai, Cloudflare, Amazon, Microsoft, TikTok…) e a busca "IP → AS e bloco".
  A conta exata começa quando o conteúdo é criado; para o período anterior use **Por AS (histórico)**.
- **Ataques:** detector de DDoS por IP de destino (média de 30 s): volume (pps/bps), **amplificação** (NTP, DNS,
  SSDP, Memcached, CLDAP… com limite próprio, bem menor) e **SYN flood**. Classifica o tipo, mostra AS de origem,
  portas e por onde entrou, e manda alerta (Telegram/webhook/push do app) no início e no fim.
- **Configuração:** exportadores vistos (tipo, amostragem, pacotes/s, erros), prefixos próprios (para o detector),
  IPs ignorados (ex.: pool CGNAT de alto volume), limites, retenção e a base IP→ASN.

### Como ligar
1. `update.sh` já sobe o novo container `flow` (e aumenta o buffer UDP do Linux).
2. Libere o UDP **só para os roteadores** (o `install.sh` deixa o firewall fechado):
   ```bash
   ufw allow from IP_DO_ROTEADOR to any port 2055 proto udp   # NetFlow v5/v9 e IPFIX
   ufw allow from IP_DO_ROTEADOR to any port 6343 proto udp   # sFlow
   ```
3. Em **Análise de Flow → Configuração**, clique em **Atualizar base de ASN** (iptoasn.com, domínio público; se o
   servidor não acessa a internet, baixe `ip2asn-combined.tsv.gz` em outro PC e use **Enviar arquivo**). Cadastre
   os **prefixos próprios** para o detector de ataques.
4. Configure o roteador (abaixo). Em **Interfaces**, escolha o equipamento: as interfaces que já estão mandando flow
   aparecem marcadas como *flow ativo*; marque trânsitos/PNI/IX e dê um papel e um nome.

Regras que evitam números errados: *active timeout* de 60 s (1 min), *inactive* de 15 s; amostragem **na entrada**
das interfaces externas (o Bastion calcula a saída pela interface de saída do flow, igual ao Akvorado). Se o roteador
também amostra na saída e informa a direção (campo 61 do v9/IPFIX), o Bastion percebe e conta cada ponto uma vez.
A taxa de amostragem vem do próprio flow (cabeçalho do v5, *options* do v9/IPFIX, sFlow); se o seu equipamento não
informar, coloque a **taxa fixa por exportador** na Configuração. O "exportador" é o IP de origem dos pacotes (use a
loopback como origem e, se for diferente do IP de gerência cadastrado, informe-o ao escolher as interfaces).

### Huawei (NetStream v9)
```
ip netstream sampler fix-packets 1000 inbound
ip netstream export version 9 origin-as
ip netstream export source 10.255.0.1
ip netstream export host IP_DO_BASTION 2055
ip netstream timeout active 1
ip netstream timeout inactive 15
interface 100GE0/0/1
 ip netstream inbound
```
NE40E/ME60: em cada placa, `slot N` → `ip netstream sampler to slot self`. IPv6: os mesmos comandos com `ipv6 netstream`.

### Juniper MX (inline-jflow, IPFIX)
```
set chassis fpc 0 sampling-instance BASTION
set services flow-monitoring version-ipfix template V4 ipv4-template
set services flow-monitoring version-ipfix template V4 flow-active-timeout 60
set services flow-monitoring version-ipfix template V4 flow-inactive-timeout 15
set services flow-monitoring version-ipfix template V6 ipv6-template
set services flow-monitoring version-ipfix template V6 flow-active-timeout 60
set services flow-monitoring version-ipfix template V6 flow-inactive-timeout 15
set forwarding-options sampling instance BASTION input rate 1000
set forwarding-options sampling instance BASTION family inet output flow-server IP_DO_BASTION port 2055 version-ipfix template V4
set forwarding-options sampling instance BASTION family inet output inline-jflow source-address 10.255.0.1
set forwarding-options sampling instance BASTION family inet6 output flow-server IP_DO_BASTION port 2055 version-ipfix template V6
set forwarding-options sampling instance BASTION family inet6 output inline-jflow source-address 10.255.0.1
set interfaces xe-0/0/1 unit 0 family inet sampling input
set interfaces xe-0/0/1 unit 0 family inet6 sampling input
```
No Juniper o ifIndex do flow é o da **unidade lógica** (xe-0/0/1.0): escolha a unidade na lista de interfaces.

### Cisco IOS-XE (Flexible NetFlow v9)
```
flow record BASTION
 match ipv4 source address
 match ipv4 destination address
 match ipv4 protocol
 match transport source-port
 match transport destination-port
 match interface input
 match flow direction
 collect interface output
 collect transport tcp flags
 collect routing source as
 collect routing destination as
 collect counter bytes long
 collect counter packets long
flow exporter BASTION
 destination IP_DO_BASTION
 source Loopback0
 transport udp 2055
 option sampler-table
flow monitor BASTION
 exporter BASTION
 record BASTION
 cache timeout active 60
sampler S1000
 mode random 1 out-of 1000
interface TenGigabitEthernet0/0/1
 ip flow monitor BASTION sampler S1000 input
```
IOS-XR: `flow exporter-map` (version v9, `options sampler-table`, transport udp 2055), `flow monitor-map` com
`record ipv4` e `cache timeout active 60`, `sampler-map` `random 1 out-of 1000` e na interface
`flow ipv4 monitor MAPA sampler S1000 ingress`.

### Datacom DMOS e ZTE
DMOS: use **sFlow** (coletor = IP do Bastion, porta 6343, agente = loopback, taxa 1:1000 a 1:4096 nas interfaces
externas). ZTE ZXR10: NetFlow v9 ou IPFIX para a porta 2055 com a mesma lógica (amostragem na entrada, timeout ativo
de 60 s). A sintaxe muda entre versões de firmware — confira no `?` do equipamento; a tela de Configuração mostra na
hora se o exportador chegou, o tipo e a taxa de amostragem lida.

Capacidade: o coletor processa ~40 mil flows/s por núcleo (com amostragem 1:1000 isso cobre centenas de Gb/s).
Armazenamento: totais por minuto e detalhes de 5 min por 7 dias (ajustável) e a junção por hora por 90 dias.
Limitação desta versão: nos detalhes cada dimensão é guardada separada (top-K), então dá para filtrar AS **ou**
prefixo **ou** porta de cada vez; para cruzar AS + bloco com precisão, cadastre um **Conteúdo**.
