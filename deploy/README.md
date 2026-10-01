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
