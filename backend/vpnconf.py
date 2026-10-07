"""Tipos de VPN cliente do BastiON e a limpeza do arquivo .ovpn.

O .ovpn vem de fora e o OpenVPN roda como root no container de VPN: só passam as diretivas conhecidas e os blocos
embutidos (<ca>, <cert>, <key>…). Scripts, plugins, arquivos externos e redirecionamento de rota padrão ficam de fora.
"""
import re
from typing import List, Tuple

TYPES = {"fortinet": "FortiGate SSL-VPN", "openvpn": "OpenVPN", "pptp": "PPTP", "l2tp": "L2TP/IPsec"}
DEFAULT_PORT = {"fortinet": 443, "openvpn": 1194, "pptp": 1723, "l2tp": 1701}

_BLOCKS = {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2", "pkcs12", "secret", "extra-certs", "peer-fingerprint"}
_ALLOWED = {
    "client", "dev", "dev-type", "proto", "remote", "remote-random", "resolv-retry", "nobind", "persist-key", "persist-tun",
    "remote-cert-tls", "cipher", "data-ciphers", "data-ciphers-fallback", "ncp-ciphers", "auth", "comp-lzo", "compress",
    "allow-compression", "key-direction", "tls-client", "tls-version-min", "tls-version-max", "tls-cipher", "tls-ciphersuites",
    "tls-groups", "verify-x509-name", "ns-cert-type", "remote-cert-ku", "remote-cert-eku", "reneg-sec", "mssfix", "tun-mtu",
    "fragment", "link-mtu", "keepalive", "ping", "ping-restart", "float", "pull", "server-poll-timeout", "explicit-exit-notify",
    "sndbuf", "rcvbuf", "fast-io", "mute-replay-warnings", "topology", "peer-fingerprint", "http-proxy", "socks-proxy",
    "providers", "tran-window", "hand-window", "replay-window", "auth-nocache", "port", "lport", "rport", "tls-exit",
    "disable-dco", "tun-mtu-extra", "mtu-disc", "ifconfig", "secret-direction",
}
# precisam apontar para arquivo: só aceitamos embutido
_FILE_DIRECTIVES = {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2", "pkcs12", "secret", "extra-certs", "crl-verify"}
MAX_OVPN = 200_000


class VpnConfError(ValueError):
    pass


def clean_ovpn(text: str) -> Tuple[str, str, int, List[str], bool]:
    """Devolve (config limpa, host do 1º remote, porta, diretivas descartadas, pede usuário/senha)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise VpnConfError("Cole o conteúdo do arquivo .ovpn")
    if len(text) > MAX_OVPN:
        raise VpnConfError("Arquivo .ovpn grande demais")
    out, dropped, block, remote, needs_auth, proto_port = [], [], None, None, False, None
    for raw in text.split("\n"):
        line = raw.strip()
        if block:
            out.append(raw.rstrip())
            if line.lower() == f"</{block}>":
                block = None
            continue
        if not line or line[0] in "#;":
            continue
        m = re.match(r"^<([a-z0-9-]+)>$", line, re.I)
        if m:
            name = m.group(1).lower()
            if name == "connection":
                raise VpnConfError("Blocos <connection> não são aceitos — deixe só as linhas 'remote' no arquivo")
            if name not in _BLOCKS:
                raise VpnConfError(f"Bloco <{name}> não é aceito")
            block = name
            out.append(f"<{name}>")
            continue
        parts = line.split()
        d = parts[0].lower().lstrip("-")
        if d == "auth-user-pass":
            needs_auth = True                              # o BastiON entrega usuário/senha por conta própria
            continue
        if d in _FILE_DIRECTIVES:
            if d == "tls-auth" and len(parts) == 1:
                continue
            raise VpnConfError(f"'{d}' aponta para um arquivo ({' '.join(parts[1:2])}). Exporte o .ovpn com os certificados "
                               f"embutidos (blocos <{d if d != 'crl-verify' else 'ca'}>…) e cole de novo")
        if d == "setenv" and len(parts) >= 3 and parts[1].upper() == "CLIENT_CERT":
            out.append(" ".join(parts))
            continue
        if d not in _ALLOWED:
            if d not in dropped:
                dropped.append(d)
            continue
        if d == "remote":
            if len(parts) < 2 or not re.match(r"^[A-Za-z0-9.\-:]+$", parts[1]):
                raise VpnConfError("Linha 'remote' inválida")
            if remote is None:
                remote = (parts[1], int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None)
        if d in ("port", "rport") and len(parts) > 1 and parts[1].isdigit():
            proto_port = int(parts[1])
        if d == "dev" and (len(parts) < 2 or not re.match(r"^(tun|tap)\w{0,12}$", parts[1])):
            raise VpnConfError("Linha 'dev' inválida (use tun ou tap)")
        if any(c in line for c in "`$|;&<>") and d not in ("verify-x509-name",):
            raise VpnConfError(f"Caracteres não permitidos na linha '{d}'")
        out.append(" ".join(parts))
    if block:
        raise VpnConfError(f"Bloco <{block}> não foi fechado")
    if not remote:
        raise VpnConfError("O .ovpn não tem a linha 'remote <servidor> <porta>'")
    return "\n".join(out) + "\n", remote[0], remote[1] or proto_port or 1194, dropped, needs_auth
