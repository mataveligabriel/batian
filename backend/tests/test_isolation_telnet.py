"""Tests: per-user device isolation + Telnet protocol (direct & via agent) + protocol validation.
Covers the "new batch" backend requirements. Cleans up any TEST_ data it creates.
"""
import os
import json
import time
import asyncio
import pytest
import requests
import websockets

def _load_base():
    v = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if v:
        return v.rstrip("/")
    try:
        for line in open("/app/frontend/.env"):
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    return ""

BASE = _load_base()
WS_BASE = BASE.replace("https://", "wss://").replace("http://", "ws://")
API = f"{BASE}/api"

ADMIN = {"email": "admin@sshcentral.io", "password": "admin123"}
OP1 = {"email": "op1@sshcentral.io", "password": "op1pass"}
ADMIN_DEVICE_ID = "70495b4f-0589-4991-a20a-34a5ad9a40a9"  # dev-2-hops


def _login(cred):
    r = requests.post(f"{API}/auth/login", json=cred, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["token"], r.json()["user"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def admin_ctx():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def op1_ctx():
    return _login(OP1)


# ---------------- BACKEND ISOLATION ----------------

class TestIsolation:
    def test_op1_devices_only_own(self, op1_ctx):
        token, user = op1_ctx
        r = requests.get(f"{API}/devices", headers=_hdr(token), timeout=15)
        assert r.status_code == 200
        for d in r.json():
            assert d.get("owner_id") == user["id"], f"operator sees device with other owner: {d}"

    def test_op1_cannot_get_admin_device(self, op1_ctx):
        token, _ = op1_ctx
        # ping (device-scoped)
        r = requests.post(f"{API}/devices/{ADMIN_DEVICE_ID}/ping", headers=_hdr(token), timeout=15)
        assert r.status_code == 404
        # put
        r = requests.put(f"{API}/devices/{ADMIN_DEVICE_ID}", headers=_hdr(token),
                         json={"name": "x", "host": "1.2.3.4"}, timeout=15)
        assert r.status_code == 404
        # delete only returns deleted=0 (scope-filtered)
        r = requests.delete(f"{API}/devices/{ADMIN_DEVICE_ID}", headers=_hdr(token), timeout=15)
        assert r.status_code == 200
        assert r.json().get("deleted", 0) == 0

    def test_batch_admin_device_as_op1_returns_error_no_500(self, op1_ctx):
        token, _ = op1_ctx
        r = requests.post(f"{API}/batch/execute", headers=_hdr(token), timeout=30,
                          json={"device_ids": [ADMIN_DEVICE_ID], "inline_command": "echo hi"})
        assert r.status_code == 200, r.text
        res = r.json()["results"][0]
        assert res["ok"] is False
        assert "não encontrado" in (res.get("error") or "")

    def test_ws_admin_device_as_op1_reports_error(self, op1_ctx):
        token, _ = op1_ctx

        async def _run():
            url = f"{WS_BASE}/api/ws/terminal/{ADMIN_DEVICE_ID}?token={token}"
            async with websockets.connect(url, open_timeout=15) as ws:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                return msg
        m = asyncio.run(_run())
        assert m.get("type") == "error"
        assert "não encontrado" in m.get("message", "")

    def test_admin_sees_op1_device_and_forced_owner(self, admin_ctx, op1_ctx):
        atoken, _ = admin_ctx
        optoken, opuser = op1_ctx
        # op1 creates a TEST device sending owner_id=admin (should be forced to op1)
        _, admin_user = admin_ctx
        r = requests.post(f"{API}/devices", headers=_hdr(optoken), timeout=15, json={
            "name": "TEST_iso_dev", "host": "127.0.0.1", "port": 22,
            "protocol": "ssh", "owner_id": admin_user["id"], "device_type": "linux"
        })
        assert r.status_code == 200, r.text
        dev = r.json()
        assert dev["owner_id"] == opuser["id"]
        dev_id = dev["id"]
        try:
            # admin sees it
            adm_list = requests.get(f"{API}/devices", headers=_hdr(atoken), timeout=15).json()
            assert any(d["id"] == dev_id for d in adm_list)

            # admin PUT without owner_id keeps existing owner
            r2 = requests.put(f"{API}/devices/{dev_id}", headers=_hdr(atoken), timeout=15, json={
                "name": "TEST_iso_dev", "host": "127.0.0.1", "port": 22, "protocol": "ssh",
                "device_type": "linux",
            })
            assert r2.status_code == 200
            assert r2.json()["owner_id"] == opuser["id"]
        finally:
            requests.delete(f"{API}/devices/{dev_id}", headers=_hdr(atoken), timeout=15)

    def test_admin_create_for_op1(self, admin_ctx, op1_ctx):
        atoken, _ = admin_ctx
        optoken, opuser = op1_ctx
        r = requests.post(f"{API}/devices", headers=_hdr(atoken), timeout=15, json={
            "name": "TEST_admin_assign", "host": "127.0.0.1", "port": 22,
            "protocol": "ssh", "owner_id": opuser["id"], "device_type": "linux"
        })
        assert r.status_code == 200
        dev_id = r.json()["id"]
        try:
            l = requests.get(f"{API}/devices", headers=_hdr(optoken), timeout=15).json()
            assert any(d["id"] == dev_id for d in l), "op1 should see the admin-assigned device"
        finally:
            requests.delete(f"{API}/devices/{dev_id}", headers=_hdr(atoken), timeout=15)

    def test_sessions_stats_scoped(self, op1_ctx):
        token, user = op1_ctx
        s = requests.get(f"{API}/sessions", headers=_hdr(token), timeout=15).json()
        for row in s:
            assert row.get("user_id") == user["id"]
        st = requests.get(f"{API}/stats", headers=_hdr(token), timeout=15).json()
        assert isinstance(st.get("total_devices"), int)
        for row in st.get("recent_sessions", []):
            assert row.get("user_id") == user["id"]

    def test_backups_summary_scoped(self, op1_ctx):
        token, user = op1_ctx
        # only own devices' ids
        my_ids = {d["id"] for d in requests.get(f"{API}/devices", headers=_hdr(token), timeout=15).json()}
        summ = requests.get(f"{API}/backups/summary", headers=_hdr(token), timeout=15).json()
        for row in summ:
            assert row["device_id"] in my_ids
        blist = requests.get(f"{API}/backups", headers=_hdr(token), timeout=15).json()
        for b in blist:
            assert b["device_id"] in my_ids

    def test_backups_run_op1_never_runs_admin_devices(self, op1_ctx):
        token, _ = op1_ctx
        r = requests.post(f"{API}/backups/run", headers=_hdr(token), timeout=60,
                          json={"device_ids": [ADMIN_DEVICE_ID]})
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_csv_import_owner_forced(self, op1_ctx, admin_ctx):
        optoken, opuser = op1_ctx
        atoken, _ = admin_ctx
        rows = [{"name": "TEST_imp_op1", "host": "127.0.0.1", "port": 22, "protocol": "ssh",
                 "username": "labuser", "device_type": "linux"}]
        r = requests.post(f"{API}/devices/import", headers=_hdr(optoken), timeout=15, json={"rows": rows})
        assert r.status_code == 200
        assert r.json()["created"] == 1
        # find it
        mine = requests.get(f"{API}/devices", headers=_hdr(optoken), timeout=15).json()
        d = next((x for x in mine if x["name"] == "TEST_imp_op1"), None)
        assert d is not None and d["owner_id"] == opuser["id"]
        requests.delete(f"{API}/devices/{d['id']}", headers=_hdr(atoken), timeout=15)


# ---------------- PROTOCOL VALIDATION ----------------

class TestProtocolValidation:
    def test_invalid_protocol_defaults_to_ssh(self, admin_ctx):
        token, _ = admin_ctx
        r = requests.post(f"{API}/devices", headers=_hdr(token), timeout=15, json={
            "name": "TEST_proto_bad", "host": "127.0.0.1", "port": 22,
            "protocol": "xyz", "device_type": "linux"
        })
        assert r.status_code == 200
        dev = r.json()
        try:
            assert dev["protocol"] == "ssh"
        finally:
            requests.delete(f"{API}/devices/{dev['id']}", headers=_hdr(token), timeout=15)

    def test_csv_import_protocol_telnet(self, admin_ctx):
        token, _ = admin_ctx
        rows = [{"name": "TEST_imp_tel", "host": "127.0.0.1", "port": 23,
                 "protocol": "telnet", "username": "labuser", "device_type": "linux"}]
        r = requests.post(f"{API}/devices/import", headers=_hdr(token), timeout=15,
                          json={"rows": rows})
        assert r.status_code == 200 and r.json()["created"] == 1
        lst = requests.get(f"{API}/devices", headers=_hdr(token), timeout=15).json()
        d = next(x for x in lst if x["name"] == "TEST_imp_tel")
        try:
            assert d["protocol"] == "telnet"
        finally:
            requests.delete(f"{API}/devices/{d['id']}", headers=_hdr(token), timeout=15)


# ---------------- TELNET (direct + via agent) ----------------

def _find_device_by_name(token, name):
    lst = requests.get(f"{API}/devices", headers=_hdr(token), timeout=15).json()
    return next((d for d in lst if d["name"] == name), None)


class TestTelnet:
    def test_batch_telnet_via_agent(self, admin_ctx):
        token, _ = admin_ctx
        dev = _find_device_by_name(token, "lab-telnet")
        assert dev is not None, "lab-telnet device is expected to exist"
        r = requests.post(f"{API}/batch/execute", headers=_hdr(token), timeout=90,
                          json={"device_ids": [dev["id"]], "inline_command": "show version", "timeout": 30})
        assert r.status_code == 200, r.text
        res = r.json()["results"][0]
        assert res["ok"] is True, res
        assert "FakeOS 1.0" in res["stdout"], res["stdout"]

    def test_ws_telnet_via_agent(self, admin_ctx):
        token, _ = admin_ctx
        dev = _find_device_by_name(token, "lab-telnet")
        assert dev is not None

        async def _run():
            url = f"{WS_BASE}/api/ws/terminal/{dev['id']}?token={token}"
            got_via = got_active = got_welcome = got_ver = False
            buf = ""
            async with websockets.connect(url, open_timeout=20) as ws:
                # wait for statuses + auto-login banner
                end = time.time() + 20
                while time.time() < end:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if m.get("type") == "status":
                        msg = m.get("message", "")
                        if "TELNET" in msg:
                            got_via = True
                        if "Sess" in msg and "ativa" in msg:
                            got_active = True
                    elif m.get("type") == "data":
                        buf += m.get("data", "")
                        if "Welcome labuser" in buf:
                            got_welcome = True
                            break
                    elif m.get("type") == "error":
                        raise AssertionError(f"WS error: {m}")
                # send show version
                await ws.send(json.dumps({"type": "input", "data": "show version\r"}))
                end = time.time() + 20
                while time.time() < end and not got_ver:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if m.get("type") == "data":
                        buf += m.get("data", "")
                        if "FakeOS 1.0" in buf:
                            got_ver = True
                            break
                await ws.send(json.dumps({"type": "close"}))
            return got_via, got_active, got_welcome, got_ver, buf
        via, active, welcome, ver, buf = asyncio.run(_run())
        assert via, "status 'via TELNET' missing"
        assert active, "status 'Sessão ativa' missing"
        assert welcome, f"'Welcome labuser' missing: {buf[:400]}"
        assert ver, f"'FakeOS 1.0' missing after show version: {buf[:800]}"

    def test_direct_telnet_and_wrong_password(self, admin_ctx):
        token, _ = admin_ctx
        # create direct telnet device
        r = requests.post(f"{API}/devices", headers=_hdr(token), timeout=15, json={
            "name": "TEST_direct_telnet", "host": "127.0.0.1", "port": 2323,
            "protocol": "telnet", "username": "labuser", "password": "labpass",
            "device_type": "other"
        })
        assert r.status_code == 200
        dev_id = r.json()["id"]
        try:
            # ping (tcp) online
            p = requests.post(f"{API}/devices/{dev_id}/ping", headers=_hdr(token), timeout=15).json()
            assert p["status"] == "online", p

            # batch show version
            r2 = requests.post(f"{API}/batch/execute", headers=_hdr(token), timeout=60,
                               json={"device_ids": [dev_id], "inline_command": "show version", "timeout": 20})
            res = r2.json()["results"][0]
            assert res["ok"] is True and "FakeOS 1.0" in res["stdout"], res

            # wrong password: update password
            requests.put(f"{API}/devices/{dev_id}", headers=_hdr(token), timeout=15, json={
                "name": "TEST_direct_telnet", "host": "127.0.0.1", "port": 2323,
                "protocol": "telnet", "username": "labuser", "password": "wrongpw",
                "device_type": "other"
            })

            async def _run():
                url = f"{WS_BASE}/api/ws/terminal/{dev_id}?token={token}"
                buf = ""
                async with websockets.connect(url, open_timeout=15) as ws:
                    end = time.time() + 15
                    while time.time() < end:
                        try:
                            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                        except asyncio.TimeoutError:
                            break
                        if m.get("type") == "data":
                            buf += m.get("data", "")
                            if "Login incorrect" in buf:
                                break
                        elif m.get("type") == "error" and "500" in m.get("message", ""):
                            raise AssertionError("500 on wrong password")
                    await ws.send(json.dumps({"type": "close"}))
                return buf
            buf = asyncio.run(_run())
            assert "Login incorrect" in buf, f"expected 'Login incorrect', got: {buf[:400]}"
        finally:
            requests.delete(f"{API}/devices/{dev_id}", headers=_hdr(token), timeout=15)
