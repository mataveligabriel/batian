"""Backend tests for the new SSH Bastion Central feature batch (iteration 2)."""
import os
import io
import time
import json
import asyncio
import pytest
import requests
import websockets
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for ln in f:
            if ln.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = ln.split("=", 1)[1].strip().rstrip("/")

WS_BASE = BASE_URL.replace("https://", "wss://").replace("http://", "ws://")

ADMIN_EMAIL = "admin@sshcentral.io"
ADMIN_PASS = "admin123"

# Seed IDs guaranteed by main agent
GW_ID = "12243e1b-5226-4ac0-8593-a70ad006f145"
JUMP_ID = "044677e4-ef3e-451b-a279-6e66292b4657"
DEV_2HOPS_ID = "70495b4f-0589-4991-a20a-34a5ad9a40a9"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def operator_token(admin_headers):
    # Create operator if needed
    email = "TEST_operator@sshcentral.io"
    pw = "operator123"
    requests.post(f"{BASE_URL}/api/users", headers=admin_headers,
                  json={"email": email, "password": pw, "name": "T Op", "role": "operator"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pw}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def created_ids():
    return {"agents": [], "devices": []}


@pytest.fixture(scope="session", autouse=True)
def cleanup(admin_headers, created_ids):
    yield
    for aid in created_ids["agents"]:
        requests.delete(f"{BASE_URL}/api/agents/{aid}", headers=admin_headers, timeout=10)
    for did in created_ids["devices"]:
        requests.delete(f"{BASE_URL}/api/devices/{did}", headers=admin_headers, timeout=10)


# ---------- Agents: password + reverse tunnel port allocation + no secret leak ----------
class TestAgentSecrets:
    def test_create_reverse_agent_autoport_and_public_key(self, admin_headers, created_ids):
        r = requests.post(f"{BASE_URL}/api/agents", headers=admin_headers, json={
            "name": "TEST_rev_agent", "mode": "reverse", "username": "root",
            "port": 22, "description": "test",
        }, timeout=15)
        assert r.status_code == 200, r.text
        a = r.json()
        created_ids["agents"].append(a["id"])
        assert a["mode"] == "reverse"
        assert isinstance(a["tunnel_port"], int) and a["tunnel_port"] >= 20001
        assert a["agent_public_key"].startswith("ssh-ed25519")
        assert "agent_private_key" not in a
        assert "password" not in a
        assert a["has_password"] is False

    def test_agent_password_lifecycle(self, admin_headers, created_ids):
        r = requests.post(f"{BASE_URL}/api/agents", headers=admin_headers, json={
            "name": "TEST_pw_agent", "mode": "direct", "host": "127.0.0.1", "port": 22,
            "username": "root", "password": "s3cret",
        }, timeout=10)
        assert r.status_code == 200, r.text
        a = r.json()
        created_ids["agents"].append(a["id"])
        assert a["has_password"] is True
        assert "password" not in a

        # PUT empty password -> kept
        r = requests.put(f"{BASE_URL}/api/agents/{a['id']}", headers=admin_headers, json={
            "name": "TEST_pw_agent", "mode": "direct", "host": "127.0.0.1", "port": 22,
            "username": "root", "password": "",
        }, timeout=10)
        assert r.status_code == 200
        assert r.json()["has_password"] is True

        # clear_password -> false
        r = requests.put(f"{BASE_URL}/api/agents/{a['id']}", headers=admin_headers, json={
            "name": "TEST_pw_agent", "mode": "direct", "host": "127.0.0.1", "port": 22,
            "username": "root", "clear_password": True,
        }, timeout=10)
        assert r.status_code == 200
        assert r.json()["has_password"] is False

    def test_self_parent_rejected(self, admin_headers, created_ids):
        r = requests.post(f"{BASE_URL}/api/agents", headers=admin_headers, json={
            "name": "TEST_selfparent", "mode": "direct", "host": "127.0.0.1", "port": 22, "username": "root"
        }, timeout=10)
        assert r.status_code == 200
        aid = r.json()["id"]
        created_ids["agents"].append(aid)
        r = requests.put(f"{BASE_URL}/api/agents/{aid}", headers=admin_headers, json={
            "name": "TEST_selfparent", "mode": "direct", "host": "127.0.0.1", "port": 22,
            "username": "root", "parent_agent_id": aid,
        }, timeout=10)
        assert r.status_code == 400

    def test_list_never_leaks_secret(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/agents", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        for a in r.json():
            assert "password" not in a
            assert "agent_private_key" not in a
            assert "has_password" in a


# ---------- Chain / Test / Ping against local lab sshd ----------
class TestChainAgainstLocalSshd:
    def test_agent_test_chain(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/agents/{JUMP_ID}/test", headers=admin_headers, timeout=45)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True, f"chain failed: {body}"
        assert body["hops"] == ["gw-minha-maquina", "jump-vpn"]

    def test_agent_ping_online(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/agents/{JUMP_ID}/ping", headers=admin_headers, timeout=45)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "online"

    def test_device_ping_online(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/devices/{DEV_2HOPS_ID}/ping", headers=admin_headers, timeout=45)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "online"

    def test_batch_execute_over_chain(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/batch/execute", headers=admin_headers, json={
            "device_ids": [DEV_2HOPS_ID], "inline_command": "echo CHAIN-OK",
            "timeout": 30,
        }, timeout=90)
        assert r.status_code == 200, r.text
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["ok"] is True, results[0]
        assert "CHAIN-OK" in results[0]["stdout"]


# ---------- Device_type shell-mode exec ----------
class TestShellModeExec:
    def test_cisco_shell_mode(self, admin_headers, created_ids):
        # Create cisco device pointing to local lab sshd
        r = requests.post(f"{BASE_URL}/api/devices", headers=admin_headers, json={
            "name": "TEST_cisco_shell", "host": "127.0.0.1", "port": 2222,
            "username": "root", "device_type": "cisco", "tags": [], "description": "test",
        }, timeout=10)
        assert r.status_code == 200, r.text
        dev = r.json()
        created_ids["devices"].append(dev["id"])

        r = requests.post(f"{BASE_URL}/api/batch/execute", headers=admin_headers, json={
            "device_ids": [dev["id"]], "inline_command": "echo SHELL-MODE-OK",
            "timeout": 30,
        }, timeout=90)
        assert r.status_code == 200, r.text
        res = r.json()["results"][0]
        assert res["ok"] is True, res
        assert "SHELL-MODE-OK" in res["stdout"], res["stdout"]


# ---------- Device password + import ----------
class TestDevicePasswordAndImport:
    def test_device_password_lifecycle(self, admin_headers, created_ids):
        r = requests.post(f"{BASE_URL}/api/devices", headers=admin_headers, json={
            "name": "TEST_pw_dev", "host": "10.0.0.99", "port": 22,
            "username": "admin", "password": "pw123", "device_type": "linux",
        }, timeout=10)
        assert r.status_code == 200
        d = r.json()
        created_ids["devices"].append(d["id"])
        assert d["has_password"] is True and "password" not in d

        # GET list also hides password
        r = requests.get(f"{BASE_URL}/api/devices", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        for x in r.json():
            assert "password" not in x

        # PUT empty keeps
        r = requests.put(f"{BASE_URL}/api/devices/{d['id']}", headers=admin_headers, json={
            "name": "TEST_pw_dev", "host": "10.0.0.99", "port": 22,
            "username": "admin", "password": "", "device_type": "linux",
        }, timeout=10)
        assert r.status_code == 200
        assert r.json()["has_password"] is True

        # clear -> false
        r = requests.put(f"{BASE_URL}/api/devices/{d['id']}", headers=admin_headers, json={
            "name": "TEST_pw_dev", "host": "10.0.0.99", "port": 22,
            "username": "admin", "clear_password": True, "device_type": "linux",
        }, timeout=10)
        assert r.status_code == 200
        assert r.json()["has_password"] is False

    def test_import_devices(self, admin_headers, created_ids):
        rows = [
            {"name": "TEST_imp_ok", "host": "10.20.0.1", "port": 22, "username": "admin",
             "password": "", "device_type": "linux", "tags": "A;B", "agent": "jump-vpn",
             "description": "row1"},
            {"name": "TEST_imp_ok", "host": "10.20.0.1", "port": 22, "username": "admin",
             "device_type": "linux", "tags": "", "agent": ""},  # duplicate -> skipped
            {"name": "", "host": "10.20.0.2", "port": 22, "username": "admin"},  # error missing name
            {"name": "TEST_imp_badport", "host": "10.20.0.3", "port": "abc", "username": "admin"},  # error port
            {"name": "TEST_imp_noagent", "host": "10.20.0.4", "port": 22, "username": "admin",
             "agent": "nonexistent-agent-xyz"},  # unknown agent -> created but with error msg
        ]
        r = requests.post(f"{BASE_URL}/api/devices/import", headers=admin_headers,
                          json={"rows": rows}, timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 2, body
        assert body["skipped"] == 1, body
        assert len(body["errors"]) == 3, body
        assert any("nome" in e.lower() or "obrig" in e.lower() for e in body["errors"])
        assert any("porta" in e.lower() or "abc" in e for e in body["errors"])
        assert any("nonexistent-agent-xyz" in e for e in body["errors"])

        # Verify created + agent resolved
        devs = requests.get(f"{BASE_URL}/api/devices", headers=admin_headers, timeout=10).json()
        by_name = {d["name"]: d for d in devs}
        assert "TEST_imp_ok" in by_name
        assert by_name["TEST_imp_ok"]["agent_id"] == JUMP_ID
        assert set(by_name["TEST_imp_ok"]["tags"]) == {"A", "B"}
        assert "TEST_imp_noagent" in by_name
        assert by_name["TEST_imp_noagent"]["agent_id"] in (None, "")
        created_ids["devices"].extend([by_name["TEST_imp_ok"]["id"], by_name["TEST_imp_noagent"]["id"]])

        # Re-import same rows -> created 0
        r = requests.post(f"{BASE_URL}/api/devices/import", headers=admin_headers,
                          json={"rows": rows}, timeout=20)
        assert r.status_code == 200
        assert r.json()["created"] == 0


# ---------- Bastion settings + authorized-keys + setup-script ----------
class TestBastionEndpoints:
    def test_get_settings_any_user(self, admin_headers, operator_token):
        r = requests.get(f"{BASE_URL}/api/bastion/settings", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        assert "public_host" in r.json()
        r = requests.get(f"{BASE_URL}/api/bastion/settings",
                         headers={"Authorization": f"Bearer {operator_token}"}, timeout=10)
        assert r.status_code == 200

    def test_put_settings_admin_only(self, admin_headers, operator_token):
        payload = {"public_host": "bastion.meudominio.com", "ssh_port": 22, "ssh_user": "bastion"}
        r = requests.put(f"{BASE_URL}/api/bastion/settings",
                         headers={"Authorization": f"Bearer {operator_token}"}, json=payload, timeout=10)
        assert r.status_code == 403
        r = requests.put(f"{BASE_URL}/api/bastion/settings", headers=admin_headers, json=payload, timeout=10)
        assert r.status_code == 200

    def test_authorized_keys_token_gate(self):
        r = requests.get(f"{BASE_URL}/api/bastion/authorized-keys", params={"token": "wrong"}, timeout=10)
        assert r.status_code == 403

    def test_authorized_keys_valid_token(self):
        c = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
        db_name = os.environ.get("DB_NAME", "test_database")
        cfg = c[db_name].config.find_one({"key": "bastion"})
        assert cfg and cfg.get("sync_token")
        token = cfg["sync_token"]
        r = requests.get(f"{BASE_URL}/api/bastion/authorized-keys", params={"token": token}, timeout=10)
        assert r.status_code == 200
        assert "text/plain" in r.headers.get("content-type", "")
        # Should have reverse agents (gw-minha-maquina at least)
        assert "restrict,port-forwarding,permitlisten=" in r.text
        # Contains ssh-ed25519 keys
        assert "ssh-ed25519" in r.text

    def test_setup_script_admin(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/bastion/setup-script",
                         headers=admin_headers, params={"api_url": BASE_URL}, timeout=10)
        assert r.status_code == 200
        script = r.json()["script"]
        assert "bastion-sync-keys" in script


# ---------- Install script ----------
class TestInstallScript:
    def test_reverse_agent_script(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/agents/{GW_ID}/install-script",
                         headers=admin_headers, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "reverse"
        assert "ssh -N" in body["bash"] or "ssh " in body["bash"]  # "-N" is inside opts
        assert "-N " in body["bash"]
        assert "-R 127.0.0.1:" in body["bash"]
        # private key present
        assert "PRIVATE KEY" in body["bash"] or "OPENSSH" in body["bash"]
        assert "Register-ScheduledTask" in body["powershell"]

    def test_direct_agent_script(self, admin_headers, created_ids):
        # create a direct agent
        r = requests.post(f"{BASE_URL}/api/agents", headers=admin_headers, json={
            "name": "TEST_direct_install", "mode": "direct", "host": "127.0.0.1", "port": 22, "username": "root",
        }, timeout=10)
        assert r.status_code == 200
        aid = r.json()["id"]
        created_ids["agents"].append(aid)
        r = requests.get(f"{BASE_URL}/api/agents/{aid}/install-script", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "direct"
        assert body.get("note")


# ---------- SSH key default password ----------
class TestSshKeyDefaultPassword:
    def test_default_password_lifecycle(self, admin_headers):
        # Read current key
        g = requests.get(f"{BASE_URL}/api/ssh-key", headers=admin_headers, timeout=10).json()
        priv, pub, user = g["private_key"], g["public_key"], g.get("default_username", "root")
        # We must not overwrite the key
        assert priv, "existing ssh key must be preserved for lab tests"

        # Set default password
        r = requests.put(f"{BASE_URL}/api/ssh-key", headers=admin_headers, json={
            "private_key": priv, "public_key": pub, "default_username": user,
            "default_password": "TEST_defpw",
        }, timeout=10)
        assert r.status_code == 200
        assert requests.get(f"{BASE_URL}/api/ssh-key", headers=admin_headers, timeout=10).json()["has_default_password"] is True

        # Empty keeps
        r = requests.put(f"{BASE_URL}/api/ssh-key", headers=admin_headers, json={
            "private_key": priv, "public_key": pub, "default_username": user,
            "default_password": "",
        }, timeout=10)
        assert r.status_code == 200
        assert requests.get(f"{BASE_URL}/api/ssh-key", headers=admin_headers, timeout=10).json()["has_default_password"] is True

        # Clear
        r = requests.put(f"{BASE_URL}/api/ssh-key", headers=admin_headers, json={
            "private_key": priv, "public_key": pub, "default_username": user,
            "clear_default_password": True,
        }, timeout=10)
        assert r.status_code == 200
        assert requests.get(f"{BASE_URL}/api/ssh-key", headers=admin_headers, timeout=10).json()["has_default_password"] is False


# ---------- WebSocket terminal against real chain ----------
class TestWebSocketTerminal:
    def test_ws_terminal_chain(self, admin_token):
        async def run():
            url = f"{WS_BASE}/api/ws/terminal/{DEV_2HOPS_ID}?token={admin_token}"
            got_connected = False
            got_data = False
            async with websockets.connect(url, open_timeout=30, close_timeout=5) as ws:
                # Read initial status frames until "Conectado"
                for _ in range(15):
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                    if msg.get("type") == "status" and "Conectado" in msg.get("message", ""):
                        got_connected = True
                        break
                    if msg.get("type") == "error":
                        raise AssertionError(f"WS error: {msg}")
                assert got_connected
                await ws.send(json.dumps({"type": "input", "data": "echo WS-OK\n"}))
                deadline = time.time() + 20
                buf = ""
                while time.time() < deadline:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        break
                    if msg.get("type") == "data":
                        buf += msg.get("data", "")
                        if "WS-OK" in buf:
                            got_data = True
                            break
                await ws.send(json.dumps({"type": "close"}))
                assert got_data, f"WS-OK not seen. buf={buf[-500:]}"
        asyncio.run(run())
