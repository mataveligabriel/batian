FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# fuso dos horários nos alertas, relatórios e agendamentos (troque com TZ=... no deploy/.env)
ENV TZ=America/Sao_Paulo
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libffi-dev ppp openfortivpn iproute2 rclone ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
# clientes de VPN extras (OpenVPN, PPTP, L2TP/IPsec). Um pacote ausente nesta versão do Debian não derruba a imagem:
# o serviço de VPN informa na tela quais tipos estão disponíveis.
RUN apt-get update && for p in openvpn pptp-linux xl2tpd strongswan strongswan-starter libstrongswan-standard-plugins libcharon-extauth-plugins; do \
      apt-get install -y --no-install-recommends "$p" || echo "!! pacote $p indisponível nesta imagem"; done; rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# voz do servidor para os alertas dos mapas (opcional: se falhar, o mapa usa a voz do navegador)
RUN pip install --no-cache-dir piper-tts || echo "!! piper-tts indisponível nesta imagem"
COPY . .
EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips", "*"]
