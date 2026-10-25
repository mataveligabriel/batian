import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tacacs as T  # noqa: E402


class H(T.Handler):
    def __init__(self):
        self.acct = []

    def client(self, ip):
        return {"id": "c1", "vendor": "cisco", "key": b"chave-de-teste"} if ip == "127.0.0.1" else None

    async def authenticate(self, cl, ctx, user, password):
        return {"ok": (user, password) == ("gabriel", "certa123"), "msg": "ok"}

    async def authorize(self, cl, ctx, args):
        c = T.command_of(args)
        if c.startswith("reload"):
            return {"status": T.AU_FAIL, "msg": "bloqueado"}
        return {"status": T.AU_PASS_ADD, "args": [] if c else T.role_attrs(cl["vendor"], {"priv": 15})}

    async def account(self, cl, ctx, args):
        self.acct.append(T.command_of(args))
        return {"ok": True}


def test_crypt_roundtrip():
    body = os.urandom(300)
    enc = T.crypt(body, 0x12345678, b"k", 0xC0, 1)
    assert enc != body and T.crypt(enc, 0x12345678, b"k", 0xC0, 1) == body


def test_protocol_end_to_end():
    async def run():
        h = H()
        srv = await asyncio.start_server(T.Server(h).serve, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        c = T.Client("127.0.0.1", port, "chave-de-teste")
        assert (await c.pap("gabriel", "certa123"))["ok"]
        assert not (await c.pap("gabriel", "errada"))["ok"]
        r = await c.ascii("gabriel", "certa123")
        assert r["ok"] and r["prompts"][:2] == [(T.ST_GETUSER, 0), (T.ST_GETPASS, T.REPLY_NOECHO)]
        assert (await c.author("gabriel", ["service=shell", "cmd="]))["args"] == ["priv-lvl=15"]
        a = await c.author("gabriel", ["service=shell", "cmd=reload", "cmd-arg=<cr>"])
        assert a["status"] == T.AU_FAIL and a["msg"] == "bloqueado"
        assert (await c.acct("gabriel", T.ACCT_STOP, ["cmd=show version <cr>"]))["ok"] and h.acct == ["show version"]
        srv.close()
    asyncio.run(run())


def test_helpers_and_templates():
    assert T.command_of(["service=shell", "cmd=show", "cmd-arg=ip", "cmd-arg=route", "cmd-arg=<cr>"]) == "show ip route"
    assert T.role_attrs("juniper", {"priv": 1, "junos": "bastion-ro"}) == ["local-user-name=bastion-ro"]
    assert T.role_attrs("nxos", {"priv": 15, "nxos": "network-admin"})[0] == 'shell:roles*"network-admin"'
    assert T.READ_ONLY.match("display bgp peer") and not T.READ_ONLY.match("system-view")
    assert T.split_otp("senha123456") == ("senha", "123456") and T.split_otp("curta") == ("curta", "")
    cl = [T.clean_client({"name": "Rede", "networks": ["10.0.0.0/8"]}), T.clean_client({"name": "Host", "networks": ["10.1.1.1"], "vendor": "juniper"})]
    assert T.client_for_ip(cl, "10.1.1.1")["name"] == "Host" and T.client_for_ip(cl, "10.2.2.2")["name"] == "Rede"
    assert T.client_for_ip(cl, "192.0.2.1") is None
    cfg = T.clean_settings({"server_ip": "192.0.2.10"})
    for v in T.VENDORS:
        on = T.render(v, T.config_params(cfg, {}, "K3y", source="Lo0", cmd=True, acct=True))
        off = T.render(v, T.config_params(cfg, {}, "K3y", source="", cmd=False, acct=False))
        assert "192.0.2.10" in on and "K3y" in on and "{{" not in on and "?cmd" not in on and "?acct" not in off
        assert len(off) <= len(on)
    assert "authorization-cmd 3 hwtacacs local" in T.render("huawei", T.config_params(cfg, {}, "K", cmd=True))
    assert "authorization-cmd" not in T.render("huawei", T.config_params(cfg, {}, "K", cmd=False))
