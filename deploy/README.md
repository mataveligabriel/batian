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

## 14. Assistente no sistema (chat) — com opção grátis
Botão **Assistente** no canto inferior direito de qualquer tela (admin e operador; o perfil View não vê). Você pede em
português e ele consulta os seus equipamentos:
- **interfaces** (SNMP): estado, velocidade e tráfego medido na hora — "quais interfaces estão down no S5732-Caxixe?";
- **BGP** (SNMP): sessões, AS, há quanto tempo estão de pé, último erro — "e o BGP da BORDA?";
- **sinal óptico** por lane (CLI) — "qual o sinal da 100GE0/0/1 da BORDA?";
- **Flow**: quem está consumindo cada trânsito/PNI e ataques em andamento — "o que está enchendo o trânsito A?";
- qualquer `display`/`show` (só leitura) e o último backup de configuração.

**Alterações** (desativar/ativar porta, BGP, ONU…) viram um cartão com os comandos exatos, o risco e como desfazer;
só executam quando você clica em **Confirmar e executar** (vale 15 min, uma vez só, só para quem pediu). Depois o assistente
confere o resultado. Tudo fica em Histórico e em Automação → Assistente IA → últimas ações.

Configuração em **Automação → Assistente IA** — escolha o provedor do modelo:
- **Groq** (grátis para começar, recomendado para testar): chave em console.groq.com, sem cartão. Modelo padrão
  `openai/gpt-oss-120b`; o botão **Listar** mostra os modelos disponíveis na sua conta.
- **Google Gemini** (grátis para começar): chave em aistudio.google.com. No plano grátis o Google pode usar as conversas
  para melhorar os modelos — evite colar senhas.
- **Ollama no próprio servidor** (grátis e nada sai da rede):
  ```bash
  cd /opt/bastion/deploy
  docker compose --profile ollama up -d
  docker compose exec ollama ollama pull qwen2.5:7b
  ```
  Sem GPU as respostas demoram e modelos de 7-8B erram mais nas ferramentas; com 16 GB de RAM dá para `qwen2.5:14b`.
- **Claude** (pago, o mais capaz com ferramentas) ou qualquer API compatível com OpenAI (OpenRouter etc.).

Os planos grátis têm limite de requisições por minuto e por dia: se estourar, o chat avisa quanto tempo esperar.
O mesmo assistente continua disponível no Telegram (chave "Bot do Telegram").

## 15. VPN FortiGate (SSL-VPN) no servidor — jumps sem depender do seu PC
Para jumps que só respondem dentro de uma VPN Fortinet: o Bastion conecta a VPN no próprio servidor (openfortivpn, código
aberto) e você só digita o **token** no sistema quando for conectar.

1. Ative o serviço (uma vez), no servidor:
   ```bash
   cd /opt/bastion/deploy
   echo "COMPOSE_PROFILES=vpn" >> .env        # se já tiver (ex.: ollama): COMPOSE_PROFILES=vpn,ollama
   sudo bash update.sh                         # prepara o /dev/ppp e sobe o container "vpn"
   ```
   Precisa de VPS KVM/VMware (em OpenVZ/LXC o /dev/ppp não existe e o update avisa).
2. **Agentes Remotos → VPNs no servidor → Nova VPN**: gateway e porta (os mesmos do "Gateway remoto" do FortiClient,
   geralmente 443 ou 10443), usuário e senha (guardada criptografada).
3. Edite cada jump (ex.: jumpALT, JumpSempre): **Precisa de VPN no servidor = VPNJVE** e **Agente pai = Nenhum**.
4. Clique em **Conectar** (ou no indicador "VPN" no menu lateral). Token de app/FortiToken: digite antes de conectar.
   **Token por e-mail ou SMS: deixe em branco e clique em Conectar** — o FortiGate envia o código e o campo aparece no Bastion.
   Na primeira vez o Bastion mostra a impressão digital do certificado do gateway: confira e clique em **Confiar**.

Só os IPs dos agentes marcados (e as redes extras que você cadastrar) entram no túnel; a rota padrão do servidor nunca muda.
O token é usado uma vez e não é guardado. Enquanto a VPN estiver desconectada, conectar num equipamento atrás desses jumps
mostra "VPN desconectada — conecte informando o token". Se o túnel cair (ex.: tempo máximo de sessão do FortiGate),
chega alerta no Telegram/app e é só digitar um token novo.

## 16. Backup e restauração do Bastion

O backup leva o banco inteiro (equipamentos, usuários, backups de config, mapas, flow, conversas do assistente),
o `deploy/.env` e os certificados do Caddy. O código já está no GitHub; o arquivo guarda o commit em uso.

```bash
sudo bash /opt/bastion/deploy/backup.sh              # tudo
sudo bash /opt/bastion/deploy/backup.sh --sem-flow   # sem o histórico de flow (bem menor)
```

Gera `/opt/bastion-backups/bastion-AAAAMMDD-HHMMSS.tar.gz` (só o root lê) e mantém os 14 últimos (`KEEP=30` muda).

**O `.env` tem o `JWT_SECRET`, que criptografa as senhas dos equipamentos.** Sem ele o backup restaura tudo,
mas as senhas não abrem. Por isso o arquivo é sensível: copie para fora do servidor, em local protegido.

Backup diário às 03:10 (`sudo crontab -e`):
```
10 3 * * * bash /opt/bastion/deploy/backup.sh >> /var/log/bastion-backup.log 2>&1
```

Restaurar (no mesmo servidor ou num novo, depois do `install.sh`):
```bash
sudo bash /opt/bastion/deploy/restore.sh /opt/bastion-backups/bastion-....tar.gz            # mantém o .env atual
sudo bash /opt/bastion/deploy/restore.sh /opt/bastion-backups/bastion-....tar.gz --com-env  # usa o .env do backup
```
O restore pede que você digite `RESTAURAR`, para os serviços, troca o banco e sobe tudo de novo.

## 17. Busca em todas as configs e alerta de configuração alterada

**Backups → Buscar nas configs** procura no último backup de cada equipamento seu: VLAN, IP, peer, VSI, VRF,
nome de cliente. Para cada linha mostra o bloco em que ela está (ex.: `bgp 65000 › ipv4-family vpn-instance IX`)
e, ao clicar, abre a config inteira já na linha.
- **Palavra inteira** (padrão): `1302` não casa `13020` nem `Vlanif1302`; `10.0.0.1` não casa `10.0.0.10`.
- **Trecho**: qualquer pedaço do texto. **Regex**: expressão regular (Python).
- O assistente também usa essa busca ("onde está a VLAN 1302?", "quem tem o peer 187.16.216.95?").

**Alerta de configuração alterada** (Automação → Alertas, ligado por padrão): sempre que um backup (o diário ou um
manual) encontra diferença em relação ao anterior, vai para o Telegram/push:
- quantas linhas entraram e saíram, e o trecho do diff;
- quem esteve no equipamento pelo Bastion no período (terminal, lote, assistente) — "ninguém" indica alteração
  feita direto no equipamento; no Junos também aparece o autor do último commit;
- o link abre o diff pronto em Backups.

Linhas que mudam sozinhas a cada coleta (carimbo de hora, "Last configuration was updated at…", `## Last commit`,
cabeçalho do `/export` do MikroTik, `ntp clock-period`) não contam como alteração.

## 18. Login seguro (2FA) e resumo diário

**Verificação em duas etapas (2FA)** — botão **Segurança** no rodapé do menu:
- **Ativar 2FA**: leia o QR code no Google Authenticator / Authy / Microsoft Authenticator e digite o código.
  Aparecem 10 **códigos de recuperação** (cada um entra uma vez, se perder o celular) — guarde.
- A partir daí o login pede senha **e** o código de 6 dígitos.
- **Encerrar outras sessões**: derruba o Bastion aberto em outros computadores/celulares (esta continua).
  Trocar a senha também derruba as outras sessões.
- **Últimos acessos**: data, IP e navegador de cada login (e tentativas recusadas).

Em **Usuários** (admin): coluna 2FA, **Exigir 2FA de todos** (quem não tem é levado à ativação no próximo acesso),
**zerar o 2FA** de alguém que perdeu o celular e a lista das últimas tentativas recusadas.

**Limite de tentativas**: 5 senhas/códigos errados em 15 min bloqueiam aquele e-mail por 15 min (e 20 por IP),
com alerta no Telegram. O IP registrado é o real do cliente (repassado pelo Caddy).

**Resumo diário** (Automação → Notificações, às 8 h por padrão): equipamentos/agentes offline, interfaces
monitoradas caídas e que oscilaram, portas com sinal óptico caindo (média de 24 h ≥ 2 dB pior que a semana
anterior, ou abaixo de −25 dBm), maiores picos de tráfego, ataques DDoS, configs alteradas, falhas de backup,
logins e usuários sem 2FA. **Prévia** mostra o texto na tela; **Enviar agora** manda na hora.

Perdeu o celular **e** os códigos de recuperação, e é o único admin? No servidor:
```bash
cd /opt/bastion/deploy
sudo docker compose exec mongo mongosh bastion --quiet --eval \
  'db.users.updateOne({email:"SEU@EMAIL"},{$set:{totp_enabled:false},$unset:{totp_secret:"",totp_recovery:""}})'
```

## 19. Mitigação de DDoS (blackhole na borda), peering e descoberta de interfaces

### Mitigação — Flow → Mitigação
O Bastion tem um BGP próprio (container `bgp`) que abre iBGP com as suas bordas e anuncia o /32 atacado com
next-hop de descarte (192.0.2.1 → NULL0), a community escolhida e **NO_EXPORT** — o descarte é só na sua borda,
nada vai para trânsito/IX.

1. Em **Flow → Configuração**, cadastre os **blocos próprios** (só IP deles pode sofrer blackhole).
2. Em **Flow → Mitigação**: ligue, informe o AS (o mesmo das bordas), o Router-ID (IP do servidor), a community
   (`65535:666`, ou com AS de 4 bytes a forma grande `263009:666:0`) e as bordas. Salve.
3. Copie a **Configuração da borda** (Huawei ou Juniper) e aplique em cada borda. Ela aceita só /32 dos seus
   blocos com essa community e não manda nada para o Bastion (export deny). Libere TCP/179 vindo do servidor.
4. Confira as sessões ficando **Established** e faça um teste com um IP seu que não esteja em uso
   (`display bgp routing-table 177.x.x.x 32` na borda; tem que aparecer com next-hop 192.0.2.1).

Para mitigar: botão **Mitigar** no ataque (aba Ataques), no alerta do Telegram (se o bot do assistente estiver
ligado e o seu Telegram liberado) ou **Mitigar IP** na aba Mitigação. Sempre com confirmação e prazo (15 min a 24 h);
sai sozinho no fim. Nunca: IP fora dos seus blocos, bloco inteiro, IPs da lista "nunca fazer blackhole".
Se o container `bgp` parar, a sessão cai e as bordas retiram o blackhole sozinhas.

### Peering — Flow → Peering
ASNs que mais chegam pelos **trânsitos**, quanto já vem por PTT/PNI, onde cada um está (PeeringDB) e os IXs em
comum com o **seu AS** (informe em Configuração). Mostra quanto do trânsito poderia sair e o que fazer
(sessão bilateral, route server, PNI ou programa de cache: GGC, OCA, FNA, AANP…).

### Descoberta de interfaces — Flow → Interfaces → Descobrir interfaces
Lista o que os roteadores já exportam e ainda não está monitorado (acima do mínimo em Mb/s), com nome e descrição
via SNMP e papel sugerido pela descrição (IX, trânsito, CDN, cliente…). Marque e adicione. Com **descoberta
automática** ligada (Configuração), a cada 6 h as interfaces novas com papel claro entram sozinhas e chega um aviso.

## 20. Acesso Web — abrir a página http/https dos equipamentos pelo Bastion

Menu **Acesso Web** (ou o ícone 🌐 na lista de **Equipamentos**): abre a interface web do equipamento (OLT,
switch, rádio, J-Web, Winbox web, etc.) **dentro do app**, pelo mesmo caminho do terminal:
- **Equipamento**: sai pela cadeia de agentes dele (túnel SSH até o agente → equipamento). O endereço sugerido é
  `http://IP/`; os atalhos `https:443`, `http:8080`, `https:8443` trocam a porta. O último endereço usado fica salvo.
- **Endereço por agente**: qualquer URL saindo por um agente escolhido (ex.: a página de um rádio que não está
  cadastrado). Administradores também podem sair **direto do servidor** (endereços do próprio servidor são bloqueados).

Como funciona: cada página aberta ganha uma porta do Bastion (**8090 a 8099**, até 10 ao mesmo tempo) e o site do
equipamento aparece inteiro nessa porta — por isso funciona com telas antigas e modernas sem adaptação.
- Só quem abriu usa a porta (link com token → cookie daquela porta; sem ele a porta responde "Acesso negado").
- Redirecionamento de `http` para `https` do equipamento é seguido sozinho; certificado autoassinado e TLS antigo são aceitos.
- A sessão fecha sozinha após **30 min** sem uso, ou no **X** da aba. Fica registrada em **Histórico** (tipo `web`).

**Liberar as portas** no firewall do servidor (o navegador acessa `IP_DO_BASTION:8090`…):
```bash
sudo ufw allow 8090:8099/tcp        # ou só da rede do NOC: ufw allow from 10.0.0.0/8 to any port 8090:8099 proto tcp
```
Outras portas: `WEB_PROXY_PORTS=9000-9009` no `deploy/.env` (e rode o `update.sh`). Tempo de inatividade:
`WEB_PROXY_IDLE_MIN=30`.

Bastion aberto por **HTTPS** (domínio): o navegador não mostra uma página `http` dentro de outra `https`, então a
página do equipamento abre **numa aba separada** (botão **Abrir**). Por `http://IP:porta` ela aparece dentro do app.

Limitações: páginas que usam WebSocket (raras em equipamentos de rede) não funcionam por aqui; links fixos para
**outro** IP (ex.: a OLT que abre a página de uma ONU em outro endereço) saem do Bastion — abra esse outro endereço
por "Endereço por agente".

## 21. Conversa por voz com o assistente

No chat do assistente, botão **Voz**: abre a tela de conversa. Você fala, o BastiON transcreve, o assistente
(o mesmo provedor do chat — Gemini, Groq, Claude…) consulta os equipamentos e a resposta é **lida em voz alta**.
Depois de falar ele volta a ouvir sozinho; tocar na esfera interrompe a fala. Em ⚙ dá para escolher a voz
(as vozes em português do aparelho) e a velocidade. Alterações de configuração continuam exigindo o clique em
**Confirmar** no cartão da conversa escrita.

- **Ouvir** usa o reconhecimento de fala do navegador (Chrome, Edge ou Safari; precisa de internet).
  **Falar** usa as vozes do próprio aparelho. Não há custo extra nem chave nova.
- O navegador **só libera o microfone em endereço seguro (https)**. Abrindo por `http://IP:porta` a tela explica
  e ainda responde falando ao que for digitado. Para o microfone funcionar:
  - **Teste rápido no PC** (Chrome/Edge): `chrome://flags/#unsafely-treat-insecure-origin-as-secure` → cole
    `http://IP_DO_BASTION:8088` → Enabled → reiniciar o navegador.
  - **Definitivo**: ligar o HTTPS do BastiON — seção 22.
- O app do Windows (Nativefier) não tem reconhecimento de fala: use o Chrome/Edge.

## 22. HTTPS (microfone/voz, Acesso Web dentro do app)

O endereço atual em HTTP continua funcionando igual (inclusive o app do Windows). O HTTPS entra **junto**.
No `deploy/.env` do servidor:

**Só com o IP (sem domínio)**
```
BASTION_HTTPS_HOST=IP_DO_BASTION
```
```bash
sudo ufw allow 8443/tcp && sudo ufw allow 8490:8499/tcp
sudo bash /opt/bastion/deploy/update.sh
```
Acesse `https://IP_DO_BASTION:8443`. O certificado é gerado pelo próprio BastiON (Caddy), então o navegador avisa
uma vez que não o reconhece: **Avançado → continuar**. Daí em diante o microfone (conversa por voz) funciona e o
Acesso Web aparece dentro do app pelas portas 8490–8499 (cada porta do Acesso Web + 400, em HTTPS).
Se a página do equipamento não aparecer dentro do app, use o botão **Nova aba** uma vez e aceite o certificado lá.

**Com domínio** apontando para o servidor (certificado reconhecido, sem aviso — necessário para instalar o app
no celular e receber notificações push):
```
BASTION_HTTPS_HOST=bastion.seudominio.com
BASTION_HTTPS_PORT=443
```
(libere 80 e 443 além de 8490:8499).

Outras portas: `BASTION_HTTPS_PORT=8443`, `WEB_PROXY_TLS_OFFSET=400`. Se a configuração de HTTPS tiver algum erro,
o BastiON sobe só no endereço de sempre e mostra o motivo em `docker compose logs frontend`.

## 23. Backups na nuvem (Google Drive, OneDrive, Dropbox…)

Em **Automação → Backups na nuvem** você escolhe o drive, a pasta e **quais tags** (ex.: FLEX) vão para a nuvem.
Depois de cada backup (o diário e os manuais) o BastiON copia para o drive só o que ainda não foi:

```
gdrive:BastiON/FLEX/OLT ZTE FLEX GUACUI/2026-10-02_03-00-00.cfg
```

- Só **copia**: nunca apaga nada no drive. Por padrão vão as versões em que a configuração mudou (e a primeira
  de cada equipamento); dá para mandar todas.
- Se o envio falhar, sai alerta (Telegram/push), aparece no resumo diário e é tentado de novo no próximo backup.
- As configurações têm senhas e chaves dos equipamentos: use uma pasta que só você acessa.

**Autorizar o drive (uma vez)** — o envio usa o [rclone](https://rclone.org), que já vem no container:
```bash
sudo bash /opt/bastion/deploy/cloud-auth.sh            # Google Drive, com o nome "gdrive"
sudo bash /opt/bastion/deploy/cloud-auth.sh onedrive onedrive   # outro tipo/nome
```
O script pede para você abrir, **no seu PC**, um túnel SSH (`ssh -N -L 53682:127.0.0.1:53682 root@IP_DO_SERVIDOR`)
e mostra um link `http://127.0.0.1:53682/auth?...`. Abra o link no navegador do PC, entre na conta do Google e
clique em Permitir. A autorização fica guardada no servidor (volume `bastion_data`) e sobrevive às atualizações.
No Google Drive o BastiON só enxerga os arquivos que ele mesmo criou.

Depois: Automação → Backups na nuvem → escolha o drive, marque as tags, **Testar acesso**, ligue e **Enviar agora**
(a primeira vez manda o histórico das tags escolhidas).

## 24. Módulos por usuário (o que cada operador pode acessar)

Em **Usuários → Novo usuário / editar**, com o papel **operator**, aparece a lista **Módulos que este usuário acessa**.
Marque só o que ele deve usar; o resto some do menu e a API recusa (erro 403), mesmo que ele digite o endereço na mão.

| Módulo | Libera |
|---|---|
| Equipamentos | cadastrar, editar, importar e testar equipamentos |
| Terminal SSH | abrir sessão SSH/Telnet |
| Acesso Web | abrir a página web dos equipamentos |
| Área de Trabalho Remota | sessões RDP (Windows) pelo navegador |
| Mapas de rede | mapas, tráfego, alarmes e análise |
| Dashboards | consumo e sinal óptico |
| Análise de Flow | tráfego por AS/IP, ataques, mitigação |
| Execução em Lote | comandos em vários equipamentos e scripts |
| Looking Glass | ping, traceroute e rota BGP a partir dos roteadores liberados |
| RPKI | ROAs e certificação dos ASNs gerenciados |
| Agentes Remotos | jump hosts, túneis e VPNs |
| Chave SSH Global | chave e credencial padrão |
| Histórico | sessões e comandos executados |
| Backups | configurações, comparação e busca |
| Assistente IA | chat e voz |

Atalhos prontos: **Tudo**, **Só monitoramento** (mapas, dashboards, flow), **Só acesso aos equipamentos** (terminal, acesso
web) e **Operação** (terminal, lote, backups).

- O **Painel NOC** e a própria conta (senha, 2FA) ficam sempre liberados.
- **admin** acessa tudo; **viewer** continua vendo só os mapas/dashboards liberados para ele. Operadores antigos (sem lista)
  continuam com tudo, até você editar.
- Os módulos limitam **telas e ações**. Os **equipamentos** que o usuário enxerga continuam sendo os dele: para ele usar
  os seus, passe com **Enviar** (ou **Transferir**, na tela de Usuários).
- A mudança vale na hora para a API; o menu dele atualiza ao recarregar a página.

## 25. Looking Glass (interno)

Menu **Looking Glass**: escolha um ou mais roteadores, o tipo de consulta (**Ping**, **Traceroute**, **Rota BGP**,
**Vizinhos BGP**) e o destino. O BastiON entra no roteador, roda o comando do fabricante e mostra a saída. Marcando
mais de um roteador você compara a mesma consulta lado a lado.

- **Configurar** (só admin): marque quais roteadores ficam disponíveis. Enquanto nenhum estiver marcado, a tela fica vazia.
- Quem tem o módulo **Looking Glass** consulta esses roteadores mesmo sem ser dono do equipamento e sem ver IP ou senha.
  Operadores que já têm lista de módulos precisam receber este módulo em **Usuários**.
- Ninguém digita comando: o destino tem de ser um IP, um prefixo (`200.160.0.0/20`, só em Rota BGP) ou um nome
  (`registro.br`, em ping/traceroute). Qualquer outra coisa é recusada.
- Uma consulta por vez em cada roteador, até 20 por minuto por usuário (`LG_PER_MIN`), 90 s por consulta (`LG_TIMEOUT`).
  Tudo fica no **Histórico** com o tipo `lg` e o comando executado.
- O login usado é o cadastrado no equipamento. Para abrir ao público depois, o ideal é um usuário só de leitura no roteador.

**Comandos por fabricante** — em Configurar dá para trocar o modelo de cada consulta (vazio = padrão). Marcadores:
`{target}` destino como digitado, `{addr}` só o endereço, `{len}` tamanho do prefixo, `{addr_len}` "endereço tamanho".
Padrões prontos para Huawei, Juniper, Cisco, MikroTik (RouterOS 7), Datacom, ZTE e Linux (FRR). Os de Huawei, Juniper
e Cisco são os usuais; confira os de Datacom, ZTE e MikroTik no seu equipamento e ajuste se o comando for outro.

## 26. RPKI — certificar ASNs e gerenciar ROAs (Krill)

O BastiON vira a tela de um **Krill** (NLnet Labs), que roda num container ao lado e guarda as chaves, assina os ROAs
e publica. Cada rede/ASN que você gerencia é uma **CA** separada. O modelo é o **RPKI delegado**, o que o Registro.br usa.

**Ativar (uma vez):**

```bash
sudo bash /opt/bastion/deploy/rpki-setup.sh
```

O script cria o token, liga o perfil `rpki` no `deploy/.env`, sobe o Krill (só em `127.0.0.1:3000`, nada exposto) e
reinicia o backend. O servidor precisa de saída HTTPS para a internet (falar com o Registro.br e baixar a tabela do RIPE RIS).

**Cadastrar um ASN** — menu **RPKI → Nova CA** (ex.: `LINK10`). Depois, com o login do titular no portal do Registro.br:

1. **Servidor de publicação**: copie/baixe o *publisher request*, informe no portal e cole de volta o *repository response*.
2. **Pai (Registro.br)**: copie/baixe o *child request*, informe no portal e cole de volta o *parent response*.

Em alguns minutos os recursos (ASN e blocos) aparecem na CA e a tela de ROAs é liberada. Os nomes dos campos no portal
podem variar; o que importa é qual XML vai e qual volta — a tela recusa se você colar o XML trocado.

**ROAs** — informe ASN de origem, prefixo e tamanho máximo (vazio = igual ao prefixo, o recomendado). Antes de
publicar, a tela mostra os avisos (tamanho máximo aberto demais, prefixo fora dos recursos). A coluna **Situação** e a
lista **Anúncios que pedem atenção** vêm da comparação do Krill com a tabela global (RIPE RIS): anúncio inválido,
anúncio sem ROA, ROA sem anúncio. Toda alteração fica no histórico da CA, com quem fez.

**Alertas** — a cada 6 h (`RPKI_WATCH_HOURS`) o BastiON avisa no Telegram/push quando um anúncio passa a ser inválido
ou quando uma CA para de sincronizar com o pai ou de publicar.

**Permissões** — módulo **RPKI** em Usuários: quem tem o módulo cria e remove ROAs; criar/remover CA e fazer o vínculo
é só do admin.

**Cópia de segurança (importante)** — as chaves das CAs ficam no volume `krill_data`. Se ele se perder, é preciso
refazer o vínculo de todos os ASNs no Registro.br.

```bash
sudo bash /opt/bastion/deploy/rpki-backup.sh          # gera /opt/bastion-backups/krill-AAAAMMDD-HHMMSS.tar.gz
```

Guarde o arquivo fora do servidor (ele contém chaves privadas). Vale colocar no cron semanal.

- Porta 3000 ocupada? Ponha `KRILL_PORT=3010` e `KRILL_URL=https://127.0.0.1:3010` no `.env` antes do setup.
- Fixar a versão do Krill: `KRILL_IMAGE=nlnetlabs/krill:vX.Y.Z` no `.env`.
- Problemas: `cd /opt/bastion/deploy && docker compose --profile rpki logs --tail 50 krill`.
- Isto cobre **assinar** (ROAs). Fazer os roteadores **validarem** rotas recebidas (validador + RTR) é outra peça.

## 27. Identificar fabricante por lista de IPs

**Equipamentos → Identificar por lista**: cole os IPs (um por linha), informe usuário e senha, e o BastiON entra em cada
um e diz se é Huawei, Cisco, Datacom, Juniper, ZTE, MikroTik etc.

- **Lista**: `IP`, `IP:porta`, `IP nome` (ou `nome IP`) ou uma rede em CIDR (até /22). Máximo de 1024 endereços por vez.
- **Protocolo**: *SSH, depois Telnet* (o Telnet só é tentado quando o SSH não abre), *só SSH* ou *só Telnet*.
- **Sair por**: direto do BastiON ou por um agente (jump) — use o agente quando os IPs só respondem de dentro da rede.
- É feita **uma** tentativa de login por equipamento, para não bloquear a conta. Depois de entrar, só comandos de
  leitura (`display version`, `show version`, `show platform`, `show system`).
- Como decide: identificação do servidor SSH, texto de boas-vindas, formato do prompt e a resposta do comando de
  versão. A coluna Fabricante mostra a confiança quando não é alta; passe o mouse para ver a pista usada.
- Resultado: filtro por fabricante, **Copiar** (cola no Excel), **CSV**, e **Cadastrar em Equipamentos** os marcados —
  já com fabricante, protocolo, porta, usuário/senha, agente e as tags que você informar.
- A senha fica só na memória durante o teste; só é gravada (cifrada) nos equipamentos que você mandar cadastrar.
- 8 equipamentos em paralelo (`SCAN_PARALLEL`). IP sem resposta custa ~8 s por protocolo. O resultado fica disponível
  por 1 hora; reiniciar o BastiON no meio perde o teste.
- Precisa do módulo **Equipamentos**. Fica registrado no Histórico (tipo `scan`).

## 28. Área de Trabalho Remota (RDP no navegador)

Menu **Área de Trabalho**: abre a área de trabalho de um computador Windows (RDP) dentro do BastiON, sem instalar nada
na sua máquina.

- **Conexão rápida**: IP ou nome, porta (3389), usuário, domínio (opcional — também aceita `DOMINIO\usuario`) e senha.
  Nada é salvo.
- **Nova conexão**: salva o computador com nome e tags. A senha fica cifrada e não volta para o navegador; sem senha
  salva, ela é pedida a cada conexão.
- **Sair por**: direto do BastiON ou por um **agente** (jump), para computadores que só respondem de dentro da rede.
- Na sessão: **Ctrl+Alt+Del**, tela cheia, **Enviar área de transferência** (texto daqui → remoto; o que você copia no
  remoto vem sozinho — precisa de HTTPS, seção 22) e **Desconectar**. A tela remota acompanha o tamanho da janela.
- **Segurança**: *Automática* serve para quase tudo. Windows antigo sem NLA: *RDP antigo*. Se recusar, tente *NLA* ou *TLS*.
- Cada sessão fica no **Histórico** (tipo `rdp`, com destino, usuário e duração). Módulo **Área de Trabalho Remota** em Usuários.

Como funciona: o container **guacd** (Apache Guacamole) faz o RDP e o BastiON repassa a tela pelo WebSocket. O guacd
não tem senha própria, por isso escuta só em `127.0.0.1:4822` — o `update.sh` confere e para o serviço se ele aparecer
aberto para fora. Não libere a porta 4822 no firewall.

- "guacd não está respondendo": `cd /opt/bastion/deploy && docker compose logs --tail 30 guacd`.
- No computador de destino: Área de Trabalho Remota habilitada, usuário com permissão e porta 3389 liberada a partir
  do BastiON (ou do agente).
- Som, impressora e transferência de arquivos ficam desligados nesta versão.

## 29. Execução em Lote por lista de IPs (sem cadastrar)

**Execução em Lote → Lista de IPs**: cole os IPs, informe usuário e senha e rode o comando (ou um script salvo) em
todos, sem cadastrar nenhum equipamento.

- A lista aceita o mesmo formato da seção 27 (`IP`, `IP:porta`, `IP nome`, rede até /22; máximo de 1024).
- **Protocolo**, **porta** e **Sair por** (direto ou por um agente) funcionam como na identificação por lista.
- **Fabricante**: *Descobrir sozinho* identifica cada equipamento ao entrar (para desligar a paginação certa) — ou
  fixe o fabricante quando a lista é toda igual, o que poupa um comando por equipamento.
- Uma tentativa de login por equipamento; 8 em paralelo. A senha não é gravada e nada é cadastrado.
- Os resultados chegam aos poucos, com nome (lido do prompt), protocolo e fabricante. **Copiar tudo** e **baixar .txt**
  juntam todas as saídas.
- "sucesso" quer dizer que entrou e o comando foi enviado; se o equipamento recusou o comando (sintaxe de outro
  fabricante), a mensagem de erro dele aparece na saída. Em lista com fabricantes misturados, rode um fabricante por vez.
- Fica no Histórico (tipo `batch`) com o comando executado. Só uma execução/identificação por usuário de cada vez.

## 30. VPNs cliente: OpenVPN, PPTP e L2TP/IPsec (além do FortiGate SSL)

Em **Agentes Remotos → VPNs no servidor → Nova VPN** escolha o tipo. O BastiON é sempre o **cliente**: ele disca para o
servidor de VPN do cliente/POP, e os agentes (jumps) marcados com aquela VPN passam a ser alcançados por dentro dela.

| Tipo | O que informar |
|---|---|
| FortiGate SSL | gateway, porta, usuário, senha (token na hora de conectar) |
| OpenVPN | o arquivo **.ovpn** (colar ou escolher) + usuário/senha se o servidor pedir |
| PPTP | servidor, usuário, senha; *Exigir criptografia* (MPPE-128/MS-CHAPv2) ligado por padrão |
| L2TP/IPsec | servidor, usuário, senha e a **chave pré-compartilhada**; sem chave vira L2TP puro |

- **Redes pela VPN**: liste as redes do outro lado (ex.: `10.50.0.0/16`). Os IPs dos agentes marcados com a VPN entram
  sozinhos. A rota padrão do servidor **nunca** vai para a VPN, mesmo que o servidor da VPN mande.
- **OpenVPN**: os certificados precisam estar embutidos no .ovpn (blocos `<ca>`, `<cert>`, `<key>`, `<tls-auth>`/`<tls-crypt>`).
  Por segurança o BastiON descarta scripts (`up`, `down`, `script-security`), `redirect-gateway`, rotas e DNS do arquivo
  — a tela mostra o que foi ignorado. Chave privada com senha não é suportada. O arquivo fica cifrado no banco.
- **PPTP**: usa TCP 1723 + GRE (protocolo 47). Se "não respondeu ao PPP", o GRE está bloqueado no caminho.
- **L2TP/IPsec**: IKEv1 com chave, modo transporte (UDP 500/4500 + 1701). Se o próprio servidor do BastiON já roda
  outro IPsec (strongSwan/Libreswan), os dois disputam as portas 500/4500.
- Senhas e chave não voltam para o navegador nem aparecem no log da conexão. Se a VPN cair, chega o alerta e é só
  clicar em Conectar de novo (não há reconexão automática).

**No servidor**: precisa do serviço de VPN ligado (`COMPOSE_PROFILES=vpn` no `deploy/.env`, como na VPN FortiGate) e de
um `sudo bash /opt/bastion/deploy/update.sh` para a imagem ganhar os programas novos (openvpn, pptp, xl2tpd, strongSwan).
Se algum pacote não existir na versão do Debian da imagem, o build não quebra: a tela de Nova VPN avisa qual tipo
ficou indisponível. Log do serviço: `cd /opt/bastion/deploy && docker compose logs --tail 50 vpn`.

## 31. Botnet nos assinantes (flow dos BNGs)

Aba **Análise de Flow → Botnet (BNG)**: acha clientes com comportamento de máquina infectada usando o flow que os
BNGs/BRAS exportam, e pergunta ao BNG qual é o login do assinante.

**O que é detectado** (por IP de assinante, minuto a minuto, só no tráfego que sai do cliente):

| Tipo | Sinal |
|---|---|
| Participando de ataque | muitos pacotes pequenos / SYN para um destino só, ou UDP pesado para um IP |
| Varredura / propagação | muitos destinos por minuto numa porta típica de botnet (23, 2323, 7547, 5555, 8291, 445, 37215…) |
| Envio de spam | SMTP (porta 25) para muitos servidores |
| Refletor de amplificação | resposta DNS/NTP/SSDP/Memcached… saindo do cliente em volume |
| Comando-e-controle | qualquer contato com um IP da sua lista de C2 ou dos feeds |

**Configurar** (admin):

1. **BNGs que exportam flow**: para cada um, o equipamento cadastrado (é por ele que o BastiON consulta o assinante,
   via SSH) e o **IP de origem do flow** (pode ser a loopback, diferente do IP de gerência). A tela avisa se o coletor
   ainda não recebeu flow daquele IP.
2. **Faixas dos assinantes**: CGNAT (`100.64.0.0/10`) e os blocos públicos entregues a clientes.
3. Limites, lista de C2, feeds (ex.: `https://feodotracker.abuse.ch/downloads/ipblocklist.txt`), ignorados e alertas.

**No BNG**: exporte NetFlow v9/IPFIX (ou sFlow) para o BastiON nas portas da aba Configuração (2055/6343), amostrando
o tráfego **de entrada nas interfaces/sessões dos assinantes** — é ali que o IP do cliente aparece antes do CGNAT. Se o
flow for coletado só depois do NAT, o IP visto é o público compartilhado e não dá para saber qual cliente é.
Não precisa cadastrar as interfaces do BNG em "Interfaces": a detecção de botnet lê o flow do BNG inteiro.

**Quem é o assinante**: com *Perguntar ao BNG* ligado, o BastiON roda um comando de consulta e mostra login, MAC,
interface e VLAN. Padrões: Huawei `display access-user ip-address {ip}`, Juniper `show subscribers address {ip} detail`,
MikroTik `/ppp active print detail where address={ip}`; os de Cisco, ZTE, Datacom e accel-ppp são palpites — ajuste em
*ajustar comandos de consulta*. São só comandos de leitura: o BastiON **não derruba nem bloqueia** o cliente.

- Incidente abre depois de 2 minutos seguidos (C2 abre no primeiro) e encerra após 15 minutos quieto.
- Com flow amostrado (1:1000, por exemplo) só aparece quem faz volume; para pegar varredura fraca, baixe o limite de
  destinos por minuto. Se um cliente legítimo aparecer (servidor de e-mail, jogo), suba o limite ou ponha em ignorados.
- Alertas vão para o Telegram/push agrupados por minuto. **Tratado** tira o incidente da lista de ativos.

## 32. Looking Glass público (porta própria, sem login)

Para passar a clientes e outros provedores: `http://IP_DO_BASTION:8089`. A página só tem o Looking Glass — quem entra
por essa porta não alcança o login nem o resto da API (o Caddy só repassa `/api/public/lg`).

**Ligar**: menu **Looking Glass → Configurar → Acesso público**.

1. Na lista de roteadores, marque o roteador no Looking Glass e clique em **tornar público** nos que podem aparecer.
2. Marque **Acesso público**, ajuste o título (ex.: `Looking Glass — AS263112`), o contato e as consultas liberadas.
3. Salve e libere a porta **TCP 8089** no firewall do servidor.

**Travas** (valem sempre):
- o visitante informa só **IP ou prefixo público** — nomes, redes privadas, CGNAT, loopback e multicast são recusados;
- não vê o comando executado, o IP do roteador nem mensagens de erro internas;
- limite por visitante (padrão 6/min e 200/dia), uma consulta por vez em cada roteador e no máximo 3 na fila;
- **Vizinhos BGP** vem desligado (mostraria todos os seus peers).

**Recomendado**: crie nos roteadores um **usuário só de leitura** e informe em *Usuário só de leitura nos roteadores* —
o acesso público passa a entrar com ele, e não com o seu login. As consultas ficam registradas por 30 dias (IP do
visitante, roteador, consulta) e as últimas aparecem em Configurar.

- Outra porta: `LG_PUBLIC_PORT=8189` no `deploy/.env` (ou `0` para não abrir porta nenhuma) e `update.sh`.
- Com HTTPS ligado (seção 22), a página também responde em `https://HOST:8489` (`LG_PUBLIC_TLS_PORT`).
- A saída do traceroute e da rota BGP mostra saltos e next-hops da sua rede, como em qualquer looking glass.

## 33. Compartilhar o Flow com outros usuários

Interfaces e conteúdos do Flow são de cada usuário. Só o **administrador** compartilha: em **Análise de Flow → Compartilhar**, marque os usuários que
devem ver os seus: eles passam a enxergar, **ao vivo**, as suas interfaces monitoradas, os seus conteúdos e os ataques
que entram por essas interfaces — com a marca "compartilhada por …", sem poder editar nem apagar. O que você mudar
aparece para eles na hora; desmarcar tira o acesso. Cada um continua podendo ter as próprias interfaces e conteúdos.

## 34. Script de OLT ZTE (TITAN e C300/C320)

Menu **Script de OLT** (módulo "Scripts de OLT"): monta o script de ativação de uma OLT ZTE C600/C650/C620/C610 (TITAN) ou C300/C320.
No C300/C320 os nomes mudam (`gpon-olt_1/x/y`, `xgei_1/x/y`, `gpon-onu_…`) e os comandos seguem a série: VLAN com `name`,
uplink com `switchport mode trunk`, gerência outband na `mng1`, usuário com `username … privilege 15`, perfis em
`gpon` (`profile tcont` + `profile traffic`) e service-port dentro da `gpon-onu` (`service-port 1 vport 1 …`).
Salvamento diário e backup por FTP não são gerados para C300.

- **Placas e portas:** o C620 já vem com o mapa (slots 1 e 2 de PON, uplinks nas controladoras 4 e 5). Nos outros
  modelos, escolha uma OLT ZTE já cadastrada e clique em **Ler placas da OLT** — o BastiON roda `show card` (só leitura)
  e monta os slots sozinho — ou adicione os slots à mão. Marque as portas PON e as uplinks a ativar.
- **Gerência:** outband (porta MGMT, rota na VRF mng) e/ou inband (VLAN, IP e a uplink por onde ela chega).
- **Serviços:** faixa de VLANs de clientes e as uplinks que as levam, perfis de ONU e perfis de banda (100M, 1G…).
- **Sistema / Acesso:** NTP, salvamento diário (`auto-write`), backup por FTP, SSH/Telnet, usuário local e SNMP v2c.

O script aparece ao lado enquanto você preenche (copiar ou baixar .txt). Nada é enviado para a OLT. Linhas marcadas
com CONFERIR (NTP, SNMP, perfis de ONU) variam conforme a versão do firmware. Senhas e comunidades não ficam salvas no
navegador, mas entram no script: trate o arquivo como confidencial.

**Autorizar ONU** (botão no alto da mesma tela): gera o script de autorização de uma ONU (onu + tcont/gemport +
vport/service-port + pon-onu-mng) com uma ou mais VLANs por ONU (até 8), cada uma em **Tag**, **Híbrida** ou **Transparente** e nas portas
ethernet escolhidas — ex.: internet em Tag na eth 1 e VLANs corporativas transparentes na eth 2. Cada VLAN vira um
gemport + service-port; uma porta aceita uma VLAN sem tag (Tag/Híbrida), e uma Tag junto com transparentes na mesma
porta vira porta híbrida. Escolha uma OLT
ZTE cadastrada e clique em **Buscar não autorizadas**: o BastiON lê as ONUs que estão pedindo autorização e o próximo
ID livre de cada PON (só leitura) — clique em **Usar** para preencher. O perfil de T-CONT (ex.: 1G) precisa existir
na OLT (é criado na Ativação da OLT, em Perfis de banda).

**Desautorizar ONU** (terceiro botão): informe só **slot, PON e número da ONU** (uma ou várias linhas — Enter no campo
ONU abre outra linha) e escolha a série; sai o script com `no onu N` na PON certa, que remove a ONU e toda a
configuração dela. Antes de aplicar, escolha a OLT e clique em **Conferir**: o BastiON lê a running-config (só
leitura) e mostra nome, serial e tipo de cada ONU, ou avisa se ela não existe naquela PON.

**Procurar ONU pelo serial** (no alto do Desautorizar ONU): digite o SN da ONU (ex.: `ZTEGD4F3D9EC`) e escolha uma OLT
ou **Todas as OLTs ZTE**. O BastiON roda `show gpon onu by sn` (só leitura) e mostra em qual OLT, PON e número a ONU está
e quem é o cliente; **Desautorizar esta** já põe a ONU na lista. O mesmo campo aceita o MAC do equipamento do cliente
(qualquer formato): aí ele usa `show mac` e mostra também a VLAN.

**VLAN a mais em ONU já autorizada** (Autorizar ONU → "VLAN a mais em ONU autorizada"): informe a porta PON e o ID da
ONU, escolha a OLT no alto e clique em **Ler ONU na OLT** — o BastiON lê o que a ONU já tem (VLANs, gemports,
service-ports, modo de cada porta) e o script novo continua a numeração (gemport/service-port/service seguintes) e
soma as portas: ex.: eth 1 em tag 100 + VLAN 200 transparente na eth 1 vira híbrida (def-vlan 100 + 200 com tag).
VLAN que a ONU já tem dá erro. Sem ler a ONU, informe o primeiro índice livre e o T-CONT.

**Enviar direto para a OLT** (Autorizar ONU, VLAN a mais e Desautorizar ONU): no painel do script, escolha a OLT e
clique em **Enviar para a OLT** → **Aplicar e gravar**. O servidor gera o script de novo com os mesmos dados da tela,
manda linha a linha numa sessão SSH/Telnet com as credenciais cadastradas do equipamento, responde "yes" quando a OLT
pede confirmação e termina com `write`. Se a OLT recusar alguma linha, ele para ali, sai do modo de configuração e
**não grava** — a tela mostra a linha e a resposta da OLT. Cada envio fica no Histórico (comandos e resultado). Só vale
para equipamentos cadastrados como ZTE; uma aplicação por vez em cada OLT.

## 35. Winbox pelo BastiON (inclusive pelos jump servers)

Menu **Winbox** (módulo "Winbox") ou o ícone de roteador na linha de um equipamento MikroTik em Equipamentos.
**Abrir túnel** faz o BastiON abrir uma porta (`WINBOX_PORTS`, padrão 8300-8309) que aceita conexão **só do IP de
quem abriu** e leva o tráfego até a porta 8291 do MikroTik — direto do servidor ou pela cadeia de agentes (jump
servers/VPN) do equipamento. No Winbox do seu computador, conecte em `IP_DO_BASTION:8300` (o endereço é copiado
sozinho) com o usuário e a senha do MikroTik. MikroTik sem cadastro: informe o IP e por qual agente sair.

- A porta fecha sozinha depois de `WINBOX_IDLE_MINUTES` (20) sem conexão e no máximo em 8 h; dá para fechar na tela.
- Cada túnel fica no Histórico. Conexão vinda de outro IP é recusada e contada na tela.
- MikroTik com Winbox em outra porta: use o acesso por IP com a porta certa (ela fica lembrada no equipamento).
- MAC-Winbox (camada 2) não passa pelo túnel; use o IP.
- Firewall do servidor: `sudo ufw allow 8300:8309/tcp` (se o ufw estiver ativo).

## 36. Roteiros automatizados (Execução em Lote → Roteiros; bot do Telegram)

Um roteiro é uma sequência de passos, cada um com um equipamento e os comandos dele — ex.: **ATIVAR ROTA LIMOEIRO**
(1. S6730-CASTELO: `sys` / `interface XGigabitEthernet0/0/8.1110` / `undo shutdown`; 2. S6730-VENDA-NOVA: `sys` /
`interface XGigabitEthernet0/0/27` / `shutdown`) e **VOLTAR ROTA LIMOEIRO** (o inverso).

- Os passos rodam **em ordem** e **param no primeiro erro**: se o equipamento do passo 1 recusar um comando, o passo 2
  não é executado. Linhas só com `#` são ignoradas. Confirmação `[Y/N]` é respondida sozinha.
- Opcional: **gravar a configuração** no fim de cada equipamento (Huawei `return`+`save`, ZTE `write`, Cisco
  `write memory`…). Sem isso a mudança vale até o próximo reboot.
- **Telegram:** com "Pode ser executado pelo bot" marcado, mande `/roteiros` (lista com botões) ou só o nome do roteiro
  (`ativar rota limoeiro`, sem diferença de maiúscula/acento). O bot mostra os passos e só executa no **✅ Executar**;
  depois responde o resultado de cada passo. Vale para quem está liberado no bot (Automação → Assistente IA) e tem o
  módulo Execução em Lote. O bot precisa estar ativo (Assistente IA configurado).
- Cada execução fica no histórico do roteiro (ícone de relógio) e no Histórico de sessões.

## 37. Engenharia de tráfego (OSPF, SPF/CSPF, LSP e otimização de custos)

Menu **Eng. de tráfego** (módulo "Engenharia de tráfego").

1. **Montar topologia:** escolha vários equipamentos (busca por nome/IP/tag, "marcar os da lista") ou um mapa.
   Com equipamentos, o BastiON lê por SNMP as interfaces, IPs, custo OSPF de cada interface (OSPF-MIB) e o tráfego de
   cada sentido (duas leituras em 10 s) e liga os equipamentos pelas sub-redes ponto a ponto (/29 a /31). Com mapa,
   usa a última Análise de rede do mapa. A tela mostra os sentidos mais cheios.
   **Community SNMP em massa:** na própria tela ("Community SNMP dos N escolhidos") ou em Equipamentos → selecionar →
   Editar em massa → "Alterar community SNMP" (vazio = volta para a community padrão do monitoramento).
2. **Otimizar custos OSPF:** informe a meta de pico (ex.: 80%). O BastiON testa subir o custo do enlace mais cheio
   (empatar com o caminho alternativo = divide por ECMP; um acima = desvia tudo) e baixar o custo de enlaces folgados do
   caminho alternativo, ficando com o que mais reduz o pico sem isolar ninguém. Mostra a carga prevista antes/depois,
   quantos pares de roteadores mudam de caminho, os comandos por equipamento (Huawei `ospf cost`, Juniper `metric`,
   Cisco/ZTE `ip ospf cost`, Datacom/MikroTik — conferir) e o botão **Criar roteiro com essas mudanças**.
   Com LDP, o LSP segue o OSPF — mudar o custo muda o caminho dos LSPs também.
3. **Caminho / LSP:** origem, destino, banda e limite (%), com "passar por" e "evitar". Mostra o caminho atual
   (SPF/LDP, com ECMP) e o caminho com folga (CSPF) com a utilização antes/depois, e o túnel RSVP-TE com
   explicit-path pronto (Huawei/Juniper/Cisco) — precisa de MPLS TE habilitado.
4. **Simular:** troque custos e/ou derrube enlaces e veja a carga prevista e quem ficaria isolado.

Modelo: sem matriz de tráfego, o que passa hoje em cada sentido é tratado como demanda entre as pontas e redistribuído
pelo SPF com ECMP — estimativa para comparar alternativas. Aplique uma mudança por vez e acompanhe no mapa.

**Seleção de equipamentos por tag:** nas telas em que se escolhem vários equipamentos (Eng. de tráfego, Execução em
Lote, Looking Glass → Configurar, Enviar/Transferir) a lista vem agrupada por tag e fechada: a caixa da tag marca o
grupo inteiro e a seta abre o grupo para escolher os equipamentos um a um (a caixa fica "parcial" quando só parte do
grupo está marcada). Nas listas de escolha única (roteiros, Script de OLT, botnet, MPLS) as opções vêm agrupadas por tag.

**Teste da community SNMP** (Equipamentos): o botão **SNMP** na linha de cada equipamento (ao lado do ping) testa a
community (lê sysName/sysDescr) — **verde** respondeu, **vermelho** não respondeu (passe o mouse para ver o erro),
contorno = ainda não testado, **cinza** = o equipamento não tem community própria configurada (não é testado). Com
equipamentos selecionados, **Testar SNMP** testa todos os que têm community. O resultado fica salvo; trocar a
community volta para "não testado".

## 38. Conexão compartilhada com os jump servers (agentes)

O BastiON mantém **uma** conexão SSH aberta por agente/jump server e passa tudo por dentro dela (acesso a
equipamento, ping, coleta SNMP, Acesso Web, Winbox, RDP, varredura). Cada acesso vira só um canal dentro dessa
conexão, sem login novo no jump. Antes, ping ou coleta de 20 equipamentos atrás do mesmo agente gerava 20 linhas
`Accepted password` no `auth.log` (e no SIEM) do jump.

- A conexão fecha sozinha depois de 10 min sem uso (`JUMP_IDLE_SECONDS` no `.env`, em segundos).
- Keepalive a cada 30 s: túnel reverso caído é detectado em até ~90 s; se a conexão compartilhada tiver caído,
  o BastiON reconecta uma vez sozinho.
- Trocar usuário, senha ou chave do agente abre uma conexão nova com a credencial nova.
- O botão **Testar** do agente continua fazendo um login de verdade (não usa a conexão compartilhada).
- Se o jump limitar sessões (`MaxSessions` no `sshd_config`, padrão 10), suba para comportar os acessos
  simultâneos por ele, ex.: `MaxSessions 100`.

## 39. Excluir usuário: o que era dele (inclusive o jump server)

Ao excluir um usuário, o BastiON mostra o que ele tem (agentes/jump servers, equipamentos, mapas…) e pergunta:

- **Transferir para mim**: tudo passa a ser de quem está excluindo; os jump servers continuam funcionando.
- **Apagar tudo**: apaga os itens dele e o túnel reverso dos jump servers dele deixa de ser aceito.

Usuários excluídos antes dessa versão deixaram itens sem dono: eles aparecem no cartão **Itens de usuários
excluídos** (Usuários), com **Trazer para mim** ou **Apagar**. A chave do túnel de agente sem dono já sai do
`authorized_keys` na próxima sincronização.

O `sshd` só confere a chave no login, então um túnel que já estava conectado continuava de pé. O script de
sincronização das chaves agora também derruba o túnel cuja porta saiu da lista. **Rode de novo o script de
preparação do servidor** (Agentes Remotos → Configurar BastiON → copiar o script, como root no servidor do
BastiON) para instalar a versão nova do `bastion-sync-keys.sh`. O encerramento fica no syslog
(`journalctl -t bastion-sync`).

## 40. Aparência: tema claro, escuro ou do sistema

Botão **Aparência** no rodapé do menu lateral: tema escuro, claro ou do sistema (acompanha o Windows/celular),
cor de destaque (pronta ou qualquer cor), tom do fundo (grafite, azul-noite, cinza neutro, quente, roxo) e tamanho
do texto. Fica salvo no usuário e vale em qualquer navegador em que ele entrar. O terminal SSH mantém o tema
próprio (ícone de paleta no terminal).

Para desenvolvedores: as cores são variáveis CSS em `src/index.css` (`--c-*`) e `src/theme-palette.css`
(`--tw-*`, gerado a partir da paleta do Tailwind); o `tailwind.config.js` aponta todas as classes de cor para elas.
Texto branco sobre botão colorido usa `text-onbrand` (sempre branco); `text-white`/`bg-white/5` seguem o tema.

## 41. Terminal no celular

- Com o celular em pé, a fonte se ajusta sozinha para caberem 80 colunas (com menos, o VRP/Junos quebram as
  tabelas, como a descrição do `display interface brief` em pedaços de 3 letras). Pinça com dois dedos ou as lupas
  da barra de teclas mudam a fonte; o botão **80** volta ao ajuste automático. Deitado, vale a fonte normal.
- As abas ficam abaixo do relógio/notch do iPhone (antes, no modo foco, ficavam embaixo da barra de status e não
  davam para tocar), são compactas e roláveis, e a lista de **+** (nova aba) tem busca por nome, IP ou tag.
- O botão de modo foco aparece também no celular (antes não havia como sair dele por lá).

## 42. Configurações (só administrador) e backup/importação do banco

Menu **Configurações** (último item do menu lateral, só para administradores), com as seções:

- **Aparência** (a do seu usuário; cada usuário continua com o botão Aparência no rodapé do menu).
- **Banco de dados**: backup, importação e backup automático (abaixo).
- **Automação e alertas** (a antiga tela Automação; o endereço `/automation` leva para cá).
- **Chave SSH global** (para administradores saiu do menu lateral; operadores com o módulo continuam vendo lá).

### Backup do banco

**Fazer backup agora** gera um arquivo `.bastionbk` criptografado (AES-256-GCM, chave derivada da senha com
scrypt). Sempre vão os cadastros e as configurações; dá para incluir os backups de configuração dos equipamentos,
o histórico (sessões, alertas, logs) e as métricas/flow (que podem ficar grandes). Os arquivos ficam em
`/data/db-backups` (volume `bastion_data`, sobrevive ao `update.sh`) e são baixados por um link de uso único.

A senha é obrigatória: o arquivo leva a chave que abre as senhas dos equipamentos e agentes (derivada do
`JWT_SECRET`). Sem a senha o arquivo não abre.

**Backup automático**: todo dia na hora escolhida, guardando os últimos N, e opcionalmente enviando para o drive
configurado em Backup na nuvem (pasta `banco`). Falha gera alerta (Telegram/push).

### Levar o BastiON para outro servidor

1. No servidor antigo: Configurações → Banco de dados → Fazer backup agora → baixar o arquivo.
2. No servidor novo: instale com o `install.sh` e entre com o administrador do `.env`.
3. Configurações → Banco de dados → Importar backup → escolha o arquivo, informe a senha → **Conferir** (mostra de
   onde é o backup e quantos equipamentos, agentes, usuários e mapas tem; nada muda ainda) → **Importar agora**.

Na importação o BastiON guarda antes uma cópia do banco atual (`bastion-db-antes-importacao-*`, mesma senha),
substitui as coleções que estão no backup (as que não estão, como métricas não incluídas, ficam como estão),
converte as senhas guardadas para o `JWT_SECRET` do servidor novo e reinicia o backend sozinho. Depois, entre com
os usuários e senhas do sistema antigo. Agentes de túnel reverso precisam apontar para o servidor novo (rode de
novo o instalador do agente) e o script de preparação do servidor (Agentes Remotos → Configurar BastiON).

Os scripts `deploy/backup.sh` e `deploy/restore.sh` (pela linha de comando, com `.env` e certificados) continuam
valendo.

## 43. Domínio próprio pelo painel (Configurações → Domínio)

Cadastre o domínio (ex.: `bastion.net.br` e `www.bastion.net.br`) em **Configurações → Domínio**. O backend grava a
lista em `/data/web/domains.txt` (volume `bastion_data`, montado também no container do Caddy em `/bastion`); o
Caddy percebe a mudança em até 5 s, gera a configuração com o site do BastiON nesses domínios, valida e recarrega
sem derrubar conexões. O certificado do Let's Encrypt sai sozinho. O endereço da instalação (`BASTION_DOMAIN`)
continua funcionando.

Antes, no registro do domínio (HostGator: Portal do Cliente → Domínios → Gerenciar → Zona DNS), mantendo os
nameservers padrão:

| Tipo  | Nome | Valor               |
|-------|------|---------------------|
| A     | @    | IP público do servidor |
| CNAME | www  | o domínio (ex.: bastion.net.br) |

e libere as portas TCP 80 e 443 no firewall. A tela mostra, para cada domínio: DNS apontando para o servidor,
porta 80, certificado e se o `https://domínio` abre este BastiON. Se a configuração gerada não validar, o Caddy
mantém a anterior e a tela mostra o motivo.

A primeira vez exige `update.sh` (o compose ganhou o volume no container do Caddy).

## 44. Script de OLT → ONUs off (limpeza em massa)

Aba **ONUs off** do Script de OLT (ZTE C300/C320 e TITAN):

1. Escolha a OLT, o mínimo de dias off (padrão 15) e, se quiser, só algumas PONs (`2/1, 2/2`).
2. **Procurar ONUs off**: o BastiON lê o `show card`, o `show clock` e o `show gpon onu state` de cada PON; para cada
   ONU em LOS/DyingGasp/OffLine roda o `show gpon onu detail-info` e calcula há quantos dias ela caiu pela hora da
   própria OLT ("Last down time" ou o histórico Authpass/OfflineTime). Só leitura; mostra o progresso.
3. As que passam do limite vêm marcadas. ONU sem horário de queda ("sem registro") não é marcada sozinha. CSV disponível.
4. Para remover: digite **REMOVER** e confirme. Antes de cada `no onu`, o BastiON confere de novo a ONU (se voltou
   a ficar online ou o serial mudou, ela é pulada) e guarda a configuração dela (`show running-config interface` e
   `pon-onu-mng`). Depois roda `no onu N` agrupado por PON e `write`. Uma limpeza por OLT por vez; a varredura vale
   por 1 hora.
5. **Removidas pela limpeza**: lista com data, quem removeu e o arquivo `.txt` com as configurações, para
   reautorizar o cliente igual se ele voltar. Fica registrado também no Histórico.

C300/C320 e TITAN (C600/C620/C650): primeiro tenta `show gpon onu state` sem interface (todas as ONUs de uma vez);
se a versão não aceitar, vai PON a PON pelo `show card`. A série é detectada testando os dois formatos de interface
(`gpon-olt_1/S/P` e `gpon_olt-1/S/P`), e a fase da ONU é achada pelo valor (o TITAN tem a coluna "O7 State" a mais).
Se der erro, a tela mostra o último comando e a resposta da OLT.

## 45. Central de incidentes (v2.0 — fase 1)

Menu **Incidentes** (módulo próprio, liberável por usuário). Junta num lugar só:

- **Zabbix** (tipo de mídia webhook), **LibreNMS** (transporte API) e **qualquer sistema** (webhook genérico) — as
  URLs, o script do Zabbix e o corpo do LibreNMS estão em Configurações → Integrações;
- **alarmes do BastiON**: equipamento ou agente sem resposta (ping automático) e interface DOWN dos mapas.

Cada problema vira um incidente numerado (#1, #2…), com severidade (Crítica, Alta, Média, Baixa, Info), origem,
equipamento (achado pelo IP/nome) e linha do tempo. O mesmo alarme repetido soma no contador em vez de abrir outro;
quando a origem normaliza (recuperação do Zabbix, state 0 do LibreNMS, `"status":"ok"`, equipamento voltou), o
incidente fecha sozinho com a duração. Na tela: reconhecer, assumir/atribuir, comentar, resolver, reabrir e abrir
o terminal do equipamento. Incidentes são da equipe (todos com o módulo veem os mesmos).

Avisos: novos incidentes a partir da severidade escolhida vão pelo canal da Automação (Telegram/push) com os botões
**Reconhecer** e **Resolver** — respondem para quem tem o Telegram ID vinculado em Assistente IA. Os alarmes do
próprio BastiON continuam avisando como antes (sem duplicar), a não ser que você ligue "avisar pelo incidente".

O menu mostra o número de incidentes ativos (pisca em vermelho com crítico/alto aberto).

### Incidentes por usuário

Cada incidente guarda os **donos**: quem tem o equipamento ou o agente do alarme (pelo IP ou nome cadastrado) e
quem tem a **rede** do IP (faixas CIDR no usuário). Em Usuários → editar operador → **Incidentes que ele vê**:

- **Só os da rede dele** (padrão do operador): vê, conta no menu, reconhece e resolve só os incidentes de que é dono;
  as "redes dele" pegam também alarmes do Zabbix/LibreNMS de IPs que não estão cadastrados como equipamento.
- **Todos**: vê a central inteira, como o administrador.

Alarme sem dono (IP que ninguém tem) aparece só para administradores e para quem vê todos. Os botões do Telegram
respeitam a mesma regra. Em Configurações → Integrações, "Avisar também no privado do Telegram de cada dono" manda
cada incidente direto para os donos (Telegram ID vinculado em Assistente IA; a pessoa precisa ter dado /start no bot).

## 46. Plantão e escalonamento (v2.0 — fase 1)

Incidentes → aba **Plantão**:

- **Escala em rodízio**: pessoas em ordem, cada uma fica um dia ou uma semana, trocando no dia/hora escolhidos (hora
  de Brasília). "1º da lista começou em" ancora o rodízio. **Trocas pontuais** (fulano cobre de sexta 18h a segunda
  8h) têm prioridade sobre o rodízio.
- **Escalonamento**: para incidentes abertos e não reconhecidos a partir da severidade escolhida, cada nível avisa
  depois de X minutos: quem está de plantão, o próximo da escala, toda a escala ou um usuário específico. O aviso vai
  no privado do Telegram (com Reconhecer/Resolver) e por push nos aparelhos da pessoa. Reconhecer para a escalada.
  Quem é acionado passa a ver o incidente (mesmo com "só os da rede dele") e aparece na linha do tempo.
- Ao ligar o escalonamento, só os incidentes abertos daí para frente escalam.
- `/plantao` no bot do Telegram mostra quem está de plantão e os próximos turnos.

Para receber no Telegram, cada pessoa da escala precisa ter o Telegram ID vinculado (Configurações → Automação e
alertas → Assistente IA) e ter mandado `/start` para o bot. Sem isso, recebe só o push.

## 47. Gravação das sessões de terminal (replay)

Toda sessão SSH/Telnet aberta pelo Terminal do BastiON é gravada (formato asciicast v2, o do asciinema) em
`/data/recordings/AAAA/MM/` (volume `bastion_data`), e o terminal avisa "sessão gravada para auditoria".

- **Histórico**: administrador vê as sessões de todos (filtro por tipo, "só gravadas", busca); cada usuário, as
  próprias. **Assistir** abre o replay (play/pausa, 1–16×, pular pausas longas, voltar/avançar 10 s, barra de tempo)
  e **baixar** o `.cast` (abre no asciinema). Sessões de lote mostram os comandos enviados. Assistir também fica
  registrado ("replay").
- **Configurações → Gravação de sessões**: ligar/desligar, gravar também o que foi digitado (desligado por padrão
  porque inclui senhas sem eco), retenção em dias (padrão 180, apaga sozinho) e limite por sessão (padrão 50 MB).
- Se o BastiON reiniciar no meio de uma sessão, a gravação até ali é mantida.

## 48. Syslog e traps SNMP (v2.0 — NOC item 3)

Serviço novo no compose: **`log`** (`python logd.py`, mesma imagem do backend, rede do host). Recebe **syslog em UDP
514** (RFC 3164/5424 — Cisco, Huawei, Juniper, MikroTik, ZTE, Linux…) e **traps SNMP v1/v2c em UDP 162** (INFORM é
respondido; v3 não é decodificado). O `update.sh` já sobe o serviço.

**Firewall**: as portas ficam fechadas no ufw. Libere só para as redes dos equipamentos/jumps:

```bash
sudo ufw allow from 203.0.113.0/24 to any port 514 proto udp
sudo ufw allow from 203.0.113.0/24 to any port 162 proto udp
```

Se a tela mostrar "UDP 514 não abriu: Address already in use", outro programa do servidor está usando a porta
(`sudo ss -lunp | grep -E ':514|:162'` — ex.: rsyslog com `imudp` ou `snmptrapd`): desligue-o ou troque a porta em
Syslog e Traps → Configurar.

- **Menu Syslog e Traps** (módulo "Syslog e Traps", liberável por usuário): mensagens ao vivo (atualiza a cada 3 s,
  pausa), busca no texto, filtro por equipamento, tipo e severidade, "só as que casaram regra"; clicar abre os
  detalhes (varbinds do trap). Operador vê só o que é dos equipamentos e das redes dele (mesma regra dos incidentes).
- **Equipamento de cada mensagem**: vínculo manual em **Fontes** > IP do agente (trap) > nome que vem na mensagem
  igual ao nome cadastrado > IP de origem igual ao IP cadastrado. Equipamento que sai pela loopback/NAT: vincule o IP
  em Fontes.
- **Atrás de jump server**: aponte os equipamentos para o jump e repasse com rsyslog/snmptrapd (modelo pronto em
  Configurar). O BastiON reconhece pelo nome na mensagem ou pelo vínculo.
- **Regras** (vêm 7 prontas, ligadas): BGP caiu (resolve quando a sessão volta), vizinho OSPF caiu, interface
  oscilando (4× em 10 min), LOS em massa na PON (8 alarmes em 2 min na mesma PON), configuração alterada (info),
  falhas de login repetidas, equipamento reiniciou. Cada regra = regex + "N vezes em M segundos" + severidade; o 1º
  grupo capturado separa os incidentes (o vizinho, a interface, a PON). Normaliza por uma segunda regex ou "resolve
  sozinho após X min quieto". Tem **teste** da regex com linhas coladas ou com as últimas 500 mensagens. As regras
  valem em até 30 s; o incidente aparece na Central com origem Syslog/Trap SNMP e as últimas mensagens na descrição.
- **Configurar** (admin): portas, retenção (padrão 30 dias, a coleção `logs` apaga sozinha), limite por origem
  (padrão 200 msg/s — o excesso é descartado e contado), severidade mínima guardada, community dos traps e os
  comandos prontos por fabricante com o IP do BastiON.
- Status do coletor: `docker compose logs --tail 50 log`.

## 49. Sondas ativas (v2.0 — NOC item 4)

Menu **Sondas ativas** (módulo "Sondas ativas"). Mede, estilo SmokePing, a partir do **servidor do BastiON** e de
**cada jump server** escolhido:

- **Ping**: N pings por ciclo (padrão 10) → mediana, mín/máx, jitter e perda. No servidor é ICMP direto (o backend
  já roda como root na rede do host); no jump usa o `ping` do sistema.
- **DNS**: consulta ao servidor informado (nome e registro escolhidos) → tempo e código (NOERROR, SERVFAIL,
  NXDOMAIN, timeout). No jump usa `dig` ou, sem ele, `python3`.
- **HTTP(S)**: status esperado, tempo total e (opcional) texto que a página deve conter. No jump: `curl` ou `python3`.
- **Porta TCP**: tempo do connect.

No jump, todas as sondas daquele agente rodam juntas num único script bash por ciclo, pela conexão SSH que o
BastiON já mantém aberta com ele — não gera um login por medição. Jump offline = "sem medir" (não alarma; o próprio
agente offline já avisa). Jump sem a ferramenta (ex.: sem `dig` e sem `python3`) aparece como "sem medir" com o motivo.

- **Sondas prontas**: informe os IPs dos DNS recursivos da rede (cria DNS + ping de cada um, severidade crítica) e,
  se quiser, DNS públicos (8.8.8.8/1.1.1.1), trânsito por ping e HTTP.
- **Alarme**: N ciclos ruins seguidos (falha, perda acima do limite ou latência/tempo acima do limite) abrem
  incidente (origem "Sonda", título com o motivo e de onde foi medido); M ciclos bons seguidos resolvem.
- **Tela**: grade sonda × origem com status, valor atual e mini gráfico; clicar abre o gráfico com a "fumaça"
  (espalhamento dos pings), mediana colorida pela perda, períodos de 3 h, 24 h, 7 e 30 dias, mediana/p95/perda média.
- **Guarda**: cada medição por 14 dias (configurável na engrenagem); o resumo por hora fica 400 dias — é a base do
  relatório de SLA (próximo item).

## 50. SLA e disponibilidade (v2.0 — NOC item 5)

Menu **SLA** (módulo "SLA e disponibilidade"). Um **item de SLA** (enlace, POP, cliente, serviço) junta
componentes e tem uma meta (ex.: 99,9%). O item fica **indisponível quando qualquer componente está fora**:

- **Interface monitorada**: quedas e voltas do monitor de interfaces (marque a interface para monitorar no mapa).
- **Equipamento**: período em que o BastiON não alcançou o equipamento (incidente "sem resposta").
- **Sonda**: ciclos que falharam no resumo por hora das Sondas ativas (de uma origem: servidor ou jump).

Janelas de manutenção (item 6) que cobrem o componente não contam como indisponibilidade e aparecem à parte.
Horas sem medição (sonda desligada, BastiON parado) ficam fora do tempo medido ("cobertura").

- Tela com o mês escolhido: disponibilidade × meta, tempo fora, quedas, MTTR; abrindo o item, cada componente, a
  latência/perda das sondas e a lista de quedas.
- **Relatório PDF**: abre o relatório do mês numa página própria (resumo + detalhe por item) — botão
  "Imprimir / salvar em PDF" (no diálogo do navegador, "Salvar como PDF"). **CSV** para planilha.
- **Resumo mensal** (admin): liga e, no dia 1 a partir das 8h, o BastiON manda no Telegram/push o resultado do mês
  anterior de todos os itens.
- Operador vê e cria só os itens dele (com os equipamentos dele); administrador vê todos.

## 51. Janelas de manutenção e pré/pós-check (v2.0 — NOC item 6)

Menu **Manutenções** (módulo "Janelas de manutenção").

- **Janela**: o que vai ser feito, início/fim (até 7 dias) e o escopo — equipamentos, grupos (tags), sondas ou a
  rede toda (só admin). Enquanto está em andamento, os alarmes do escopo **não avisam** (Telegram, push, plantão):
  o incidente ainda abre, marcado "🛠 em manutenção", para o histórico, e aparece na tela da janela. Os avisos
  diretos de equipamento fora e de interface caiu também ficam em silêncio. No **SLA**, o tempo da janela não
  conta como indisponibilidade.
- Avisa no Telegram quando a janela começa e termina. Dá para começar/encerrar antes da hora ou cancelar.
- **Pré/pós-check** (automático no início e no fim, ou na mão): roda nos equipamentos escolhidos os comandos do
  fabricante — vizinhos OSPF, sessões BGP, tabela de rotas, LDP, l2vc/VSI e interfaces (Huawei, Cisco, Juniper,
  Datacom, ZTE, MikroTik, Linux; editáveis por janela) — e compara: números antes → depois (vizinhos Full, sessões
  estabelecidas, rotas, up/down), em vermelho o que piorou, e o diff de cada saída sem as colunas que mudam sozinhas
  (uptime, contadores). Saída completa de antes/depois à mão. Ao terminar o pós-check, manda no Telegram o resumo
  ("tudo igual" ou o que piorou em cada equipamento). Os comandos ficam registrados no Histórico.

## 52. Monitor BGP (v2.0 — NOC item 7)

Menu **Monitor BGP** (módulo "Monitor BGP"). Duas partes:

**Sessões dos roteadores** (Configurar → roteadores):
- **SNMP** (padrão, sem login): BGP4-MIB — estado, AS remoto e tempo de cada sessão IPv4; prefixos aceitos só em
  Cisco (CISCO-BGP4-MIB). Usa a community do equipamento (ou a padrão do Mapa); atrás de jump, o net-snmp do jump.
- **CLI**: lê o resumo do BGP (`display bgp peer` / `show bgp all summary` / `show bgp summary`…) e traz os
  prefixos recebidos de Huawei, Cisco, Juniper, Datacom e ZTE (inclui IPv6). Faz login a cada leitura — dá para
  ficar no SNMP e buscar só os prefixos pela CLI de tempo em tempo (padrão 30 min).
- Incidente (origem "Monitor BGP") quando uma sessão estabelecida cai (crítica se trazia mais de 100 mil
  prefixos) e quando os prefixos recebidos caem mais que o limite (padrão 50%) — resolve sozinho quando volta.
  Sessão desligada administrativamente não alarma; se o syslog já abriu incidente do mesmo vizinho, não duplica.
- Tela: sessões por roteador, há quanto tempo, prefixos (× o normal), quedas em 24 h, nome do vizinho editável.

**Seus prefixos na Internet** (Configurar → prefixos; informe o AS que deve originar cada bloco — quem não tem ASN
próprio coloca o AS de quem anuncia):
- A cada 15 min consulta os dados públicos do RIPE RIS (RIPEstat, `stat.ripe.net`): **origem diferente**
  (sequestro), **sub-prefixo de outra origem**, **visibilidade** abaixo do mínimo (sumiu da Internet), estado
  **RPKI** do anúncio e, se você informar os upstreams esperados, **vazamento** (caminhos chegando por outro AS).
- Cada achado vira incidente e resolve sozinho quando some. O servidor precisa alcançar `stat.ripe.net` (HTTPS).

## 53. GPON (v2.0 — NOC item 8)

Menu **GPON** (módulo "GPON"), para OLTs ZTE C300/C320 e TITAN (C600/C650/C620). Em Configurar escolha as OLTs.

- **Inventário** (serial, modelo, nome, descrição e usuário PPPoE de cada ONU) vem do **último backup de
  configuração** da OLT — deixe o backup da OLT ligado. A senha do PPPoE não é lida nem guardada.
- **Estado** de todas as ONUs (`show gpon onu state`, um comando) a cada 15 min e **sinal recebido pela ONU**
  (`show pon power onu-rx` de cada PON) a cada 60 min — uma sessão por OLT por ciclo.
- **Por PON**: ocupação (× 128, ajustável), online/off, quantas em LOS e em dying gasp (falta de energia),
  mediana do sinal × o normal da PON e quantas ONUs com sinal ruim (abaixo de −27 dBm, ajustável).
- **Alarmes** (Central de Incidentes, origem "GPON"): **queda em massa** (8 ONUs caíram de uma vez ou mais de 50%
  da PON fora — possível fibra rompida; resolve quando voltam) e **sinal degradado** (mediana da PON caiu 3 dB do
  normal — fusão, conector, splitter). Se a regra de LOS em massa do syslog já abriu incidente da mesma PON, não
  duplica.
- **Buscar cliente** por serial (com ou sem hífen), nome, descrição, usuário PPPoE ou `2/1:15`: mostra OLT/PON/ONU,
  estado, há quanto tempo está off e o sinal; **Consultar agora na OLT** roda `show gpon onu detail-info` e
  `show pon power attenuation` da ONU na hora (fica no Histórico).
- Histórico de online/off e sinal por PON: 30 dias.

## 54. Conformidade de configuração e firmware (v2.0 — NOC item 9)

Menu **Conformidade** (módulo "Conformidade"), a partir dos **backups de configuração** (não roda nada nos
equipamentos). Recalcula a cada 30 min só o que teve backup novo ou regra alterada.

- **Regras por fabricante** (vêm prontas para Huawei, Cisco, Juniper, MikroTik, Datacom e ZTE): NTP, SSH v2,
  telnet/FTP/www desligados, VTY só com SSH, SNMP sem community `public/private`, AAA (TACACS/RADIUS), syslog
  para servidor, senhas criptografadas, HTTP desligado, root sem SSH… Cada regra = "precisa ter"/"não pode ter" +
  regex por linha + importância + **como corrigir**. Dá para criar, editar, desligar e **testar nos backups** antes de
  salvar.
- **Nota por equipamento** (peso: alta 3, média 2, baixa 1) e o que está fora do padrão com o trecho da
  configuração e o comando de correção.
- Quando um backup novo **quebra uma regra de importância alta** (ex.: alguém ligou o telnet), abre incidente
  (origem "Conformidade"); resolve sozinho quando o backup seguinte volta ao padrão.
- **Firmware**: versão lida da configuração (Huawei `!Software Version`, Cisco `version`/IOS XR, Juniper
  `set version`, MikroTik `by RouterOS`, Datacom/ZTE quando aparece), agrupada por fabricante e versão; defina a
  **versão mínima aprovada** por fabricante para marcar quem está desatualizado.

## 55. Inventário, IPAM e NetBox (v2.0 — NOC item 10)

Menu **Inventário / IPAM** (módulo "Inventário e IPAM").

- **IPs em uso**: lidos dos **backups de configuração** (Huawei, Cisco/ZTE/Datacom, Juniper `display set`, MikroTik
  `/export`) com equipamento, interface, descrição e VRF. Relê a cada 30 min o que teve backup novo (botão para
  reler tudo).
- **Prefixos**: cadastre seus blocos (descrição, VRF, VLAN, situação); a lista mostra a hierarquia e o uso de cada
  um. Abrindo um prefixo: IPs ocupados, sub-prefixos, **blocos livres** (todos ou de um tamanho — ex.: o próximo /30
  para um enlace) e **reserva de IP** (aparece como ocupado).
- **Problemas**: **IP duplicado** (mesmo IP em dois equipamentos na mesma VRF) e **redes nas configurações sem
  prefixo cadastrado** — marque e crie os prefixos de uma vez.
- **Buscar IP**: IP, rede, pedaço de IP, equipamento, interface ou descrição → onde está e em qual prefixo.
- **Inventário**: todos os equipamentos com fabricante, modelo, firmware (da Conformidade), IP, grupos, por onde é
  acessado, quantos IPs configurados e último backup; exporta CSV.
- **NetBox** (admin): URL + token de API (guardado criptografado). **Comparar** mostra o que falta de cada lado;
  **Sincronizar** cria no NetBox os prefixos e os IPs das interfaces (descrição "equipamento · interface"; VRF pelo
  nome quando existe lá) e, se marcado, traz para o BastiON os prefixos que só existem no NetBox. Nada é apagado
  em nenhum dos lados.

## 56. OLT Huawei (MA5800 / EA5800 / MA5608T / MA5683T)

Tudo o que existia para OLT ZTE agora vale também para Huawei SmartAX. Cadastre a OLT em **Equipamentos** com o tipo **Huawei** e um nome, modelo ou tag com "OLT" (ex.: `OLT-CENTRO`): é assim que o BastiON separa a OLT dos roteadores/switches Huawei VRP.

- **Script de OLT** → botão **Huawei** ao lado do título:
  - **Ativação da OLT**: sysname, VLANs de clientes no uplink (`vlan … smart` / `port vlan … F/S P`), gerência inband (`interface vlanif`) ou outband (`meth0`), `dba-profile` + `ont-lineprofile gpon` por plano (IDs 10, 11…), service profile genérico, NTP, SNMP e autosave. **Ler placas** mostra o `display board 0`.
  - **Autorizar ONU**: **Buscar autofind** (`display ont autofind all`) traz o serial e o próximo ONT ID livre da PON; o script faz `ont add … sn-auth … omci ont-lineprofile-id … ont-srvprofile-id … desc`, a porta (`ont port native-vlan`) ou o PPPoE na ONU (`ont ipconfig … pppoe`) e um `service-port` por VLAN. A senha do PPPoE só vai no script; não fica no navegador nem no banco.
  - **Desautorizar ONU**: procura pelo serial (`display ont info by-sn`, aceita `HWTC-A1B2C3D4` ou os 16 hexa) ou pelo MAC (`display mac-address all | include`), confere (`display ont info F S P ID`) e gera `undo service-port port F/S/P ont ID` + `ont delete`.
  - **ONUs off**: `display ont info 0 all` + `display ont info summary F/S/P` (última queda e causa, pela hora da OLT — `display time`). Antes de remover, guarda `display current-configuration ont …` e os service-ports de cada ONT.
  - **Enviar para a OLT**: roda `enable` / `undo smart` / `config`, para no primeiro `Failure`/`% …` (volta com `return`, sem `save`), responde Enter às perguntas `{ <cr>|… }:` e `y` às confirmações.
- **GPON**: em **GPON → Configurar** escolha também as OLTs Huawei. Estado de todas as ONTs a cada ciclo; o sinal (Rx) e a causa da queda vêm do `display ont info summary` de cada PON. As PONs aparecem como `0/1/0`.
- **Syslog**: a regra pronta "LOS em massa na PON" passa a reconhecer o alarme Huawei (`FrameID: 0, SlotID: 1, PortID: 3`). Quem já tinha a regra sem alteração é atualizado sozinho.
- **Assistente de IA**: conhece os comandos da OLT Huawei (não confunde com o `system-view` do VRP).
- **Backup**: na OLT Huawei o `display current-configuration` só funciona no modo privilegiado; quando a OLT recusa no modo usuário, o BastiON entra com `enable` e repete sozinho. Sem o backup, o GPON ainda mostra estado e sinal, mas sem o PPPoE de cada ONT.

Confira na versão do seu firmware antes de aplicar: `ont ipconfig … pppoe`, `snmp-agent target-host …`, `autosave time` e `display current-configuration ont` mudam de sintaxe entre versões.

## 57. OLT FiberHome (AN5516, CLI clássica)

Cadastre a OLT em **Equipamentos** com o tipo **FiberHome (OLT)** (Telnet ou SSH). O BastiON entra com `enable`; se a OLT pedir senha, usa a mesma senha do login (padrão comum: GEPON).

- **Script de OLT** → botão **FiberHome**:
  - **Ativação**: uplinks (`set uplink port 19:1 enable`), `set cards_all_auth`, VLANs de clientes (`set service … vid_begin … uplink … tagged`), VLAN/IP de gerência (`set manage vlan …`), rota padrão, SNMP e `set auto_save`. **Ler placas** usa o `showcard`.
  - **Autorizar ONU**: **Buscar** lê o `show discovery` (todas as PONs das placas GPON ou só as informadas, ex.: `1/2`) e o próximo ID livre pelo `show authorization`. O script faz `set whitelist phy_addr address … action add slot S pon P onu O type T`, a VLAN na porta LAN (`set epon … service … vlan_mode tag`) ou o PPPoE na ONU roteada (`set wancfg …` + `onuveip`), `apply onu … vlan` e `save`.
  - **Desautorizar ONU**: a FiberHome remove pelo serial: informe slot/PON/ONU e **Conferir** lê o serial e o tipo; o script usa `set whitelist … action delete`.
  - **Diretório**: firmwares novos usam `cd onu`, antigos `cd gpononu`; o BastiON descobre sozinho na leitura e o botão deixa trocar.
  - **ONUs off**: ainda não (falta confirmar como a AN5516 mostra a hora da última queda).
- **GPON**: escolha a OLT FiberHome em **GPON → Configurar**: estado online/offline de cada ONU pelo `show authorization` e alerta de queda em massa por PON. O sinal aparece ao abrir a ONU (`show optic_module`).

Ainda sem testar em equipamento real: a leitura de `showcard`, `show discovery` e `show authorization` é tolerante (procura o serial FHTT… e o estado up/dn em cada linha). Se algo vier vazio, a tela mostra a resposta da OLT — mande essa saída para ajustar. A AN6000 (CLI nova) não é suportada.

## 58. SOC — Política de comandos e aprovação

Menu **Painel SOC → Política de comandos**. Cada comando, antes de chegar ao equipamento, passa pelas regras — no **terminal web** (na hora do Enter), **Execução em Lote**, **roteiros** (web e Telegram), **Script de OLT** e nas alterações do **assistente de IA**.

- **Ações**: *Bloquear* (não envia; abre incidente no SOC), *Pedir aprovação* (outro administrador aprova), *Alertar* (envia e abre incidente) e *Permitir* (exceção para as regras de baixo, ex.: admins podem `reboot`).
- **Escopo** de cada regra: fabricante, tags dos equipamentos, perfil (admin/operador), usuários e onde (terminal, lote…). O padrão é uma expressão regular sobre o comando (sem diferenciar maiúsculas).
- **Regras prontas**: bloquear apagar configuração/formatar; aprovação para reboot e para derrubar/alterar BGP; alerta para desligar interface e mexer em usuários/AAA. Use **Simular** para testar comandos contra as regras sem registrar nada.
- **Aprovação**: o pedido aparece em **Aprovações** e no Telegram (grupo de alertas) com botões *Aprovar/Negar* — no Telegram só vale para administrador com o ID vinculado em Assistente IA. Quem pediu não aprova o próprio pedido (dá para liberar nas configurações se houver um único admin).
  - **Terminal**: o Enter fica segurado; aprovado, o comando segue sozinho; negado ou sem resposta no prazo, a linha é apagada. *Ctrl+C* cancela o pedido.
  - **Lote, roteiro, OLT e IA**: a execução volta com "precisa de aprovação (pedido #N)"; depois de aprovado, execute de novo (a aprovação vale uma vez, para os mesmos comandos e equipamento, pelo tempo configurado).
- **Eventos**: tudo que a política pegou (bloqueios, alertas, pedidos, aprovações e execuções aprovadas), com busca por usuário, equipamento ou comando. Os incidentes saem com a origem **SOC**.
- O terminal reconstrói a linha da tela (histórico, Tab, edição no meio), e compara também com o que foi digitado: vale a decisão mais restritiva. Linhas de senha não são avaliadas.

## 59. SOC — Envio para SIEM e auditoria da interface

Menu **Painel SOC → SIEM e auditoria** (só administradores).

- **O que sai**: logins (ok, falha, bloqueio), sessões de acesso (início/fim do terminal, web, RDP, Winbox e comandos em lote), comandos pegos pela política e ações do assistente de IA, **alterações feitas na interface/API** (quem fez POST/PUT/DELETE em qual caminho, de qual IP) e incidentes (aberto/resolvido). Cada destino escolhe as categorias e uma severidade mínima (0–10).
- **Destinos** (até 10):
  - **Syslog** UDP, TCP ou TCP+TLS (RFC 5424, facility local4), com a mensagem em **CEF** (Wazuh, ArcSight, Sentinel), **JSON** ou **LEEF** (QRadar).
  - **GELF** (Graylog) por UDP ou TCP.
  - **Splunk HEC** (token guardado criptografado).
  - **HTTP/Webhook** com JSON em lote e cabeçalho de autenticação (Elastic, Logstash, n8n…).
- Só sai o que acontecer depois de ligar (não reenvia o histórico). Envio a cada 10 s; se o SIEM cair, os eventos ficam numa fila em memória (até 5 000 por destino) e o status mostra o erro. **Testar** manda um evento de teste na hora.
- **Auditoria da interface**: a mesma trilha de alterações, consultável na tela, guardada por 180 dias.

No Wazuh, basta um `<remote>` com `<connection>syslog</connection>` e o IP do BastiON em `<allowed-ips>`; o decoder CEF nativo já entende os campos `suser`, `src`, `dhost`, `act` e `outcome`.

## 60. SOC — Logins suspeitos e cerca geográfica

Menu **Painel SOC → Logins suspeitos** (só administradores). Cada login certo é comparado com o histórico do próprio usuário (90 dias):

- **País novo**, **operadora (ASN) nova**, **IP novo**, **viagem impossível** (troca de país em menos de N horas), **horário fora do costume** (com 20+ logins de histórico), **navegador/sistema novo** e **acerto depois de várias falhas** (força bruta que deu certo). Dois sinais juntos sobem a severidade.
- A partir da severidade escolhida abre incidente com origem **SOC** e, se o usuário tiver o Telegram vinculado (Assistente IA), pergunta **"Foi você?"** no privado. *Não fui eu* encerra todas as sessões dele na hora e abre incidente crítico. Na tela: *Foi o usuário* ou *Encerrar sessões dele*.
- **Cerca geográfica**: só aceita login dos países da lista (ex.: `BR`). Rede interna, IPs sem país conhecido e as **redes confiáveis** sempre passam; dá para não aplicar aos administradores. O BastiON recusa salvar uma lista que bloquearia o seu próprio acesso.
- O país/operadora vem da **base IP → ASN** (Análise de Flow → Configurações). Sem ela, valem só IP novo, horário, navegador e força bruta.
- **Últimos logins**: logins e falhas com país, operadora e navegador, filtrando por e-mail.

## 61. SOC — Threat intel no Flow

Menu **Painel SOC → Threat intel** (só administradores). O coletor de flow confere a origem e o destino de **cada flow** contra listas públicas de IPs maliciosos e registra, por dia, quem falou com quem.

- **Listas** (atualizadas a cada 6 h pelo servidor): abuse.ch Feodo Tracker (C2), Spamhaus DROP v4/v6 (blocos sequestrados), Emerging Threats (comprometidos), blocklist.de (ataques SSH/mail/web), CINS Army (scanners) e, opcionais, DShield e saídas Tor. Dá para somar **listas próprias** por URL (um IP/CIDR por linha, com categoria), uma **lista manual** e uma lista de **nunca alertar** (falsos positivos). Se um download falha, a versão anterior continua valendo.
- **Saída × entrada**: os **prefixos próprios** do Flow (Análise de Flow → Configuração) dizem qual lado é seu. **Saída** (um host seu falando com C2/malware/comprometido/bloco DROP) abre incidente com origem SOC — normalmente é cliente infectado; veja também Flow → Botnet. **Entrada** (scanners, força bruta) é ruído de fundo da internet: fica registrada e só vira incidente se você definir um mínimo de flows por dia.
- **Ocorrências**: resumo por categoria, seus hosts com mais contatos maliciosos e a tabela (sentido, IP seu, IP malicioso, lista, portas, flows, volume). **Consultar IP** mostra em quais listas o IP está, país/operadora e todas as ocorrências dele no flow.
- O **login anômalo** também usa as listas: login vindo de IP listado entra como sinal ("IP em lista de ameaças").
- Ocorrências guardadas por 30 dias (configurável).
- **Menos falso positivo**: resposta do seu host a uma varredura (RST, SYN-ACK sem dados, ICMP, resposta de DNS/serviço da porta baixa para a porta alta do scanner) conta como **entrada**, com a marca "respondeu N×". Um incidente por **host + categoria** (não um por IP remoto), com os IPs mais ativos na descrição; saída para DROP/atacante/comprometido só alerta a partir de N pacotes no dia (C2 e malware: sempre); **hosts locais ignorados** (honeypot, servidor exposto de propósito); o incidente **fecha sozinho** depois de N horas sem tráfego. Os incidentes do formato antigo (um por IP) são encerrados sem aviso no primeiro ciclo.
- **Bloqueio na borda** (aba própria): escolha as categorias (padrão DROP + C2) e o roteador baixa a lista sozinho por um endereço com token — formatos texto, MikroTik (.rsc com address-list que expira em 2 dias), Junos (prefix-list) e Cisco (object-group). A tela traz o script do MikroTik (script + scheduler de 6 h + regras em `/ip firewall raw`). Prefixos próprios, protegidos da mitigação e "nunca alertar" nunca entram.

## 62. SOC — Auditoria CERT.br (passiva)

Menu **Painel SOC → Auditoria CERT.br** (só administradores). Acha serviços que respondem para a internet e viram refletor de DDoS — DNS recursivo, NTP, SSDP/UPnP, SNMP, Memcached, Portmap, NetBIOS, mDNS, CLDAP, Ubiquiti, Chargen, WS-Discovery, TFTP, DVR — **sem varrer a rede**:

- **Pelo flow**: um IP dos seus prefixos respondendo de uma porta de serviço UDP para IPs de fora (porta de destino diferente da do serviço, para não confundir com o próprio cliente NTP/DNS). Confirma a partir de N destinos no dia e fecha sozinho quando para de responder por N dias. Ponha em **Nunca acusar** os servidores que respondem de propósito (DNS autoritativo, NTP público).
- **Pelos avisos**: cole o e-mail do CERT.br em **Importar aviso** (o BastiON acha os IPs e o serviço pelo assunto, porta ou texto) ou configure o e-mail/n8n para fazer POST no endereço de **Configurações** (JSON `{subject, text}` ou texto puro). Só IPs dos seus prefixos entram.
- **Pelos backups**: botão **Adicionar as regras na Conformidade** — MikroTik com `allow-remote-requests=yes`, servidor NTP, UPnP e SOCKS ligados; Cisco `ip dns server` e `ntp master`; Huawei `dns proxy enable`; Juniper `system services dns`.
- Cada pendência mostra o prefixo do IPAM (cliente/POP), as fontes (flow e/ou aviso), quantas vezes voltou e como corrigir. Item novo abre incidente com origem SOC.

## 63. SOC — CVEs do firmware

Menu **Painel SOC → CVEs do firmware** (só administradores). A versão de cada equipamento — a mesma que a Conformidade lê dos backups — é cruzada com a **NVD** (NIST) e com o catálogo **KEV** da CISA (vulnerabilidades exploradas ativamente).

- **Produtos**: MikroTik RouterOS, Juniper Junos, Cisco IOS / IOS XE / IOS XR / NX-OS (pelo número da versão e pelo cabeçalho do backup) e Huawei por modelo (`<modelo>_firmware` na NVD — cobertura parcial). ZTE, Datacom, FiberHome e Linux quase não têm dados na NVD: informe produto e versão à mão no detalhe do equipamento (ex.: `mikrotik:routeros` / `7.11.2`) quando o automático não servir.
- A base é baixada pelo servidor a cada N dias (só os produtos que você tem) e a comparação é local. Sem chave da API a NVD é lenta (5 consultas a cada 30 s) — peça uma chave gratuita no site da NVD e cole em ⚙.
- **Incidente** (origem SOC) para CVE KEV e/ou acima do CVSS escolhido, um por equipamento e CVE; fecha sozinho quando o firmware é atualizado e a CVE deixa de valer.
- Limitação: a NVD às vezes liga a CVE a "sistema X rodando no modelo Y"; o BastiON olha só o sistema, então pode aparecer CVE de outro modelo da mesma linha — cada CVE traz o texto e o link da NVD para conferir.

## 64. SOC — Login único (SSO / OpenID Connect)

Menu **Painel SOC → Login único (SSO)** (só administradores). Botão "Entrar com …" na tela de login para Google Workspace, Microsoft Entra ID, Keycloak, Authentik, Authelia ou qualquer provedor OpenID Connect.

1. **Adicionar** o provedor (modelos prontos) e preencher o **emissor** (issuer — ex.: `https://accounts.google.com`, `https://login.microsoftonline.com/<TENANT_ID>/v2.0`, `https://sso.exemplo.net/realms/<REALM>`).
2. No provedor, criar o app/client do tipo *Web* e cadastrar **exatamente** a **URL de retorno** mostrada (`<endereço público>/api/auth/sso/<id>/callback`). O endereço público vem do `PUBLIC_URL` do `.env` e pode ser trocado na tela.
3. Colar **client ID** e **client secret** (guardado criptografado), salvar e **Testar** (confere discovery e chaves).

- **Quem entra**: o e-mail da conta do provedor precisa existir no BastiON — ou ligue *Criar o usuário no 1º login* (com o perfil padrão) e restrinja por **domínio** (ex.: `exemplo.net`). E-mail não verificado é recusado.
- **Perfil pelos grupos** (opcional): claim `groups` (ou `roles`) → Administrador/Operador/View, atualizado a cada login; *Só quem está num grupo* bloqueia o resto. O único administrador nunca perde o perfil.
- **Quem pode usar o SSO**: *Todos* (quem tem usuário ou é de um domínio liberado) ou *Só os usuários marcados*. Neste modo ninguém é criado no 1º login: libere cada pessoa.
- **Por usuário** (Painel SOC → Login único → Por usuário, ou Usuários → editar → *Como ele entra*): *Padrão* (segue as regras gerais), *Senha e SSO*, *Só SSO* (a senha passa a ser recusada) ou *Só senha* (o SSO é recusado). O modo da pessoa vale acima da regra geral. Você não pode deixar a sua própria conta *Só SSO*, e o único administrador sempre consegue entrar com senha. A tela avisa quem ficaria sem nenhum jeito de entrar.
- **Senha só para administradores** (regra geral para quem está no *Padrão*): os demais só entram pelo SSO; o login com senha do admin fica como acesso de emergência.
- **Segurança**: authorization code + PKCE (S256), state e nonce de uso único, id_token conferido pela chave pública do provedor (emissor, audiência, validade). O navegador recebe só um código de 90 s trocado pela sessão. O 2FA fica com o provedor (o 2FA obrigatório do BastiON não vale para usuário criado pelo SSO); **cerca geográfica** e **login anômalo** continuam valendo, e o login aparece no histórico como `sso:<id>`.

## 65. SOC — Servidor TACACS+ (login nos equipamentos com os usuários do BastiON)

Menu **Painel SOC → TACACS+**. O container `tacacs` (mesma imagem do backend, sobe com o `update.sh`) escuta **TCP 49**; quem decide é o backend.

- **Passo a passo**: (1) em *Ajustes*, o IP do BastiON que os equipamentos alcançam; (2) em *Equipamentos*, os grupos (IPs/redes de origem + fabricante) — cada grupo nasce com a sua chave; (3) em *Usuários*, ligue o TACACS por pessoa (nome no equipamento + senha TACACS, ou *gerar* e mostrar uma vez) e crie **contas de serviço** (ex.: o usuário que o próprio BastiON usa nos backups); (4) em *Script*, escolha o grupo/fabricante e copie, baixe ou leve para a Execução em Lote; (5) libere a porta: `ufw allow from REDE_DE_GERENCIA to any port 49 proto tcp`.
- **Fabricantes**: Cisco IOS/IOS-XE, IOS XR, NX-OS, Huawei VRP (HWTACACS, `domain default_admin`), Juniper Junos (usuários-modelo `bastion-admin`/`bastion-oper`/`bastion-ro` + `local-user-name`), ZTE ZXAN (C300/C320/C600), Datacom DmOS e genérico. Os modelos de **ZTE e Datacom** precisam ser conferidos no manual do firmware; qualquer modelo pode ser **editado e salvo** na própria tela (`{{server}}`, `{{secret}}`, `{{source}}`; linhas `?source`/`?cmd`/`?acct` só entram com a opção ligada). MikroTik não tem TACACS+ (só RADIUS).
- **Sempre com fallback local**: os scripts deixam o login local como segundo método; se o BastiON não responder, o serviço devolve ERROR e o equipamento cai no local. Aplique em um equipamento, mantenha a sessão aberta e teste numa segunda janela (ou use *Visão geral → Testar um login*, que faz um login TACACS+ real no serviço).
- **Perfil**: Administrador/Operador → priv-lvl 15; Visualização → priv-lvl 1 e só comandos de consulta. Junos recebe o usuário-modelo; NX-OS, roles; IOS XR, task group (tudo ajustável).
- **Comando a comando**: com autorização de comandos no equipamento, cada comando passa pela **Política de comandos** (canal *Direto no equipamento*): bloquear nega na hora, *pedir aprovação* abre o pedido (Telegram) e libera uma vez depois de aprovado, *alertar* abre incidente.
- **Senha**: TACACS própria (padrão), a mesma do BastiON ou qualquer uma; com 2FA ligado em *Ajustes*, quem tem 2FA digita a senha seguida dos 6 números do app. Cada pessoa troca a própria senha em *Meu acesso*. CHAP não é aceito (a senha só existe como hash): use PAP/ASCII.
- **Registro**: logins (também em Usuários → histórico de logins, com bloqueio após 5 erros em 15 min), autorizações negadas e a contabilidade (sessões e comandos digitados) em *Eventos*, enviados ao **SIEM**. IP fora dos grupos tem a conexão fechada na hora.

## 66. Arquivos (FTP) — equipamentos baixam e enviam arquivos, direto ou pelos jumps

Menu **Operação → Arquivos (FTP)**. O BastiON vira o servidor FTP dos equipamentos (firmware para as OLTs, `startrun.dat`, backups), sem precisar de FileZilla em outra máquina.

- **Acesso**: crie com um nome, *por onde o equipamento chega* (BastiON direto ou um jump), pasta, validade (2 h … 30 dias, ou *sem prazo* para backup automático do equipamento), se pode enviar e, opcional, de quais IPs. Usuário e senha são gerados e valem só naquele caminho. *Comandos* mostra o comando pronto para ZTE TITAN (`copy ftp //…`), Huawei VRP, Cisco, Juniper, MikroTik e curl, com IP, porta, usuário e senha preenchidos.
- **Pelo jump**: o BastiON usa a conexão SSH que já mantém com o agente e pede ao SSH do jump uma porta de escuta (como `ssh -R`): o equipamento conecta em `IP_DO_JUMP:2121`. Modo **ativo** (PORT) funciona sem nada a mais: o BastiON abre a conexão de dados até o equipamento pelo próprio jump. Modo **passivo** usa as portas 2122-2129 no jump e anuncia o IP do jump (descoberto com `hostname -I`, ajustável em Ajustes). A porta só fica aberta enquanto houver acesso válido e volta sozinha se o túnel cair.
- **No jump**: para a porta aparecer na rede do jump (e não só no 127.0.0.1), o sshd precisa de `GatewayPorts clientspecified` (a tela avisa e mostra o comando) e `AllowTcpForwarding yes` (padrão). Porta abaixo de 1024 exige root no jump: se o equipamento não aceitar porta diferente de 21, redirecione no jump com `iptables -t nat -A PREROUTING -p tcp --dport 21 -j REDIRECT --to-ports 2121`.
- **Direto no servidor**: porta 2121 e passivas 30000-30009 (libere no `ufw` só para a gerência).
- **Arquivos**: a aba *Arquivos* é a mesma pasta do FTP (`/data/ftp`, no volume de dados): enviar pelo navegador (arrastar e soltar), baixar, renomear, criar pasta, apagar. Cada acesso enxerga só a sua pasta.
- **Direto no equipamento (SFTP/SCP)**: quando o equipamento não alcança o BastiON nem o jump (NAT no meio — o `show users` da OLT mostra um IP que não é o do jump), use a aba *Direto no equipamento*: o BastiON entra pelo mesmo caminho do SSH (BastiON → jump → equipamento), lista as pastas e **traz** o arquivo (ex.: `/datadisk0/DATA0/startrun.dat`, salvo em `equipamentos/<nome>`, sem sobrescrever) ou **envia** um arquivo da área (firmware). Tenta SFTP e, se o equipamento não tiver, SCP; o erro diz o que ligar no equipamento (ex.: Huawei `sftp server enable`, Cisco `ip scp server enable`). Equipamento por Telnet não serve (precisa de SSH).
- **Registro**: logins, envios, downloads e exclusões em *Transferências*, com quem, de qual IP e por qual jump; vão também para o SIEM. FTP manda senha em texto puro: use acessos com prazo curto e restritos aos IPs dos equipamentos.

## 67. IPAM — Documentação por grupo e importação do phpIPAM

Em **Inventário → IPAM**:

- **Documentação**: crie quantos **grupos** quiser (uma rede separada, uma cidade, um cliente, uma VRF). Cada grupo tem os seus prefixos (VLAN, VRF, gateway, local, observação) e os seus IPs (hostname, descrição, MAC, dono, situação — em uso, reservado, DHCP, desligado — e observação). O **mesmo IP pode estar em vários grupos**; dentro de um grupo ele aparece uma vez por VRF. Ao abrir um prefixo: uso, **IPs livres** (clique para documentar), em quais equipamentos o IP aparece nas configurações (backups) e em quais outros grupos ele também está documentado. Exporta e importa CSV (ip; hostname; descrição; mac; dono; situação; vrf; observação).
- **phpIPAM**: informe a URL, o App ID (Administração → API, com leitura) e o token do app ou usuário/senha. *Conectar e ver seções* lista as seções com as sub-redes; para cada uma escolha *grupo novo*, um grupo existente ou *não importar*. A importação traz as sub-redes (com VLAN e VRF) e, se marcado, os endereços com hostname, descrição, MAC, dono e situação (tags do phpIPAM), sem apagar nada; *sobrescrever* atualiza o que já estava documentado. Rodar de novo só acrescenta o que é novo.
