"""Tests for the new batch: scripts.quick, automation, alerts, backups, deploy httpx."""
import os
import time
import socket
import http.server
import threading
import subprocess
import sys
import venv
import tempfile
import shutil
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for ln in f:
            if ln.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = ln.split("=", 1)[1].strip().rstrip("/")

ADMIN = {"email": "admin@sshcentral.io", "password": "admin123"}
DEV_2HOPS_ID = "70495b4f-0589-4991-a20a-34a5ad9a40a9"


@pytest.fixture(scope="session")
def admin_headers():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="session")
def operator_headers(admin_headers):
    email, pw = "TEST_opnb@sshcentral.io", "operator123"
    requests.post(f"{BASE_URL}/api/users", headers=admin_headers,
                  json={"email": email, "password": pw, "name": "T Op NB", "role": "operator"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=10)
    assert r.status_code == 200, r.text
    yield {"Authorization": f"Bearer {r.json()['token']}"}
    # cleanup user
    users = requests.get(f"{BASE_URL}/api/users", headers=admin_headers, timeout=10).json()
    for u in users:
        if u.get("email") == email:
            requests.delete(f"{BASE_URL}/api/users/{u['id']}", headers=admin_headers, timeout=10)


# ---------- 1. Deploy bug: httpx present ----------
class TestDeployHttpx:
    def test_httpx_in_requirements(self):
        with open("/app/backend/requirements.txt") as f:
            reqs = f.read()
        assert "httpx" in reqs, "httpx must be listed in requirements.txt"

    def test_automation_imports_httpx(self):
        with open("/app/backend/automation.py") as f:
            assert "import httpx" in f.read()

    def test_backend_api_root_200(self):
        r = requests.get(f"{BASE_URL}/api/", timeout=10)
        assert r.status_code == 200

    def test_fresh_venv_install_and_import(self):
        # Simulate fresh install as user's VPS would do
        tmpdir = tempfile.mkdtemp(prefix="TEST_venv_")
        try:
            venv.EnvBuilder(with_pip=True).create(tmpdir)
            pip = os.path.join(tmpdir, "bin", "pip")
            py = os.path.join(tmpdir, "bin", "python")
            # Install requirements (quiet)
            r = subprocess.run([pip, "install", "-q", "-r", "/app/backend/requirements.txt"],
                               capture_output=True, text=True, timeout=600)
            assert r.returncode == 0, f"pip install failed:\nSTDOUT:{r.stdout[-2000:]}\nSTDERR:{r.stderr[-2000:]}"
            # Try importing server (uses backend/.env via load_dotenv)
            env = os.environ.copy()
            r = subprocess.run([py, "-c", "import sys; sys.path.insert(0,'/app/backend'); import server; print('OK')"],
                               capture_output=True, text=True, env=env, timeout=60)
            assert r.returncode == 0 and "OK" in r.stdout, f"server import failed:\nSTDOUT:{r.stdout}\nSTDERR:{r.stderr}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ---------- 2. Scripts: quick flag CRUD ----------
class TestScriptsQuick:
    def test_create_update_delete(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/scripts", headers=admin_headers,
                          json={"name": "TEST_quick_script", "content": "echo hi", "quick": True}, timeout=10)
        assert r.status_code == 200, r.text
        s = r.json()
        sid = s["id"]
        assert s["quick"] is True
        # PUT change
        r = requests.put(f"{BASE_URL}/api/scripts/{sid}", headers=admin_headers,
                         json={"name": "TEST_quick_script2", "content": "echo hi2", "quick": False}, timeout=10)
        assert r.status_code == 200
        # GET
        scripts = requests.get(f"{BASE_URL}/api/scripts", headers=admin_headers, timeout=10).json()
        s2 = next(x for x in scripts if x["id"] == sid)
        assert s2["name"] == "TEST_quick_script2"
        assert s2["content"] == "echo hi2"
        assert s2["quick"] is False
        # DELETE
        r = requests.delete(f"{BASE_URL}/api/scripts/{sid}", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        scripts = requests.get(f"{BASE_URL}/api/scripts", headers=admin_headers, timeout=10).json()
        assert not any(x["id"] == sid for x in scripts)


# ---------- 3. Automation settings ----------
class TestAutomationSettings:
    def test_get_public_shape(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/automation/settings", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        s = r.json()
        for k in ["ping_enabled", "ping_interval_min", "backup_enabled", "backup_hour",
                  "notify_agents", "notify_devices", "telegram_chat_id", "webhook_url",
                  "has_telegram_token", "last_ping", "ping_running"]:
            assert k in s, f"missing key {k}"
        assert "telegram_bot_token" not in s

    def test_operator_cannot_put(self, operator_headers):
        r = requests.put(f"{BASE_URL}/api/automation/settings", headers=operator_headers,
                         json={"ping_interval_min": 5, "backup_hour": 3}, timeout=10)
        assert r.status_code == 403

    def test_put_token_lifecycle_and_clamps(self, admin_headers):
        # Set token
        r = requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 0, "backup_enabled": True, "backup_hour": 99,
            "notify_agents": True, "notify_devices": False,
            "telegram_bot_token": "x:y", "telegram_chat_id": "", "webhook_url": ""}, timeout=10)
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["has_telegram_token"] is True
        assert "telegram_bot_token" not in s
        assert s["ping_interval_min"] == 1  # clamped from 0
        assert s["backup_hour"] == 23  # clamped from 99

        # Empty token keeps existing
        r = requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
            "notify_agents": True, "notify_devices": False,
            "telegram_bot_token": "", "telegram_chat_id": "", "webhook_url": ""}, timeout=10)
        assert r.status_code == 200
        assert r.json()["has_telegram_token"] is True

        # Clear token
        r = requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
            "notify_agents": True, "notify_devices": False,
            "clear_telegram_token": True, "telegram_chat_id": "", "webhook_url": ""}, timeout=10)
        assert r.status_code == 200
        assert r.json()["has_telegram_token"] is False


# ---------- 4. Alerts ----------
def _spawn_http_server(port: int):
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        def log_message(self, *a, **k): pass
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    return srv


class TestAlerts:
    def test_no_channel_returns_error(self, admin_headers):
        # Ensure clean settings (no token, no webhook)
        requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
            "notify_agents": True, "notify_devices": False,
            "clear_telegram_token": True, "telegram_chat_id": "", "webhook_url": ""}, timeout=10)
        r = requests.post(f"{BASE_URL}/api/automation/test-alert", headers=admin_headers, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("error") == "Nenhum canal configurado", body

    def test_webhook_ok_and_alert_stored(self, admin_headers):
        srv = _spawn_http_server(9911)
        try:
            requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
                "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
                "notify_agents": True, "notify_devices": False,
                "telegram_chat_id": "", "webhook_url": "http://127.0.0.1:9911/hook"}, timeout=10)
            r = requests.post(f"{BASE_URL}/api/automation/test-alert", headers=admin_headers, timeout=20)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["webhook"] == "ok", body
            # GET /api/alerts lists it
            alerts = requests.get(f"{BASE_URL}/api/alerts", headers=admin_headers, timeout=10).json()
            assert any(a.get("results", {}).get("webhook") == "ok" for a in alerts)
        finally:
            srv.shutdown()
            # reset webhook
            requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
                "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
                "notify_agents": True, "notify_devices": False,
                "telegram_chat_id": "", "webhook_url": ""}, timeout=10)

    def test_invalid_telegram_token_no_500(self, admin_headers):
        requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
            "notify_agents": True, "notify_devices": False,
            "telegram_bot_token": "111:invalidtoken", "telegram_chat_id": "1", "webhook_url": ""}, timeout=10)
        r = requests.post(f"{BASE_URL}/api/automation/test-alert", headers=admin_headers, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        # Should be an HTTP 4xx string, not raise 500
        tg = body.get("telegram") or ""
        assert isinstance(tg, str) and ("HTTP" in tg or "erro" in tg.lower()), body
        # cleanup
        requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
            "notify_agents": True, "notify_devices": False,
            "clear_telegram_token": True, "telegram_chat_id": "", "webhook_url": ""}, timeout=10)

    def test_ping_now(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/automation/ping-now", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body.get("started") in (True, False)  # false only if already running
        # Wait for scheduler to run
        time.sleep(12)
        s = requests.get(f"{BASE_URL}/api/automation/settings", headers=admin_headers, timeout=10).json()
        assert s["last_ping"] is not None


# ---------- 5. Backups ----------
class TestBackups:
    _created = []

    def test_setup_dev_2hops_backup_cmd(self, admin_headers):
        # Fetch dev-2-hops from device list (no single-item GET)
        devs = requests.get(f"{BASE_URL}/api/devices", headers=admin_headers, timeout=10).json()
        cur = next(d for d in devs if d["id"] == DEV_2HOPS_ID)
        payload = {**{k: cur.get(k) for k in ["name", "host", "port", "username", "device_type", "tags",
                                              "agent_id", "description"]},
                   "backup_enabled": True,
                   "backup_command": "cat /etc/hostname; date +%s%N"}
        r = requests.put(f"{BASE_URL}/api/devices/{DEV_2HOPS_ID}", headers=admin_headers, json=payload, timeout=10)
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["backup_command"] == "cat /etc/hostname; date +%s%N"
        assert g["backup_enabled"] is True

    def test_run_backup_and_diff(self, admin_headers):
        # Run twice to get 2 versions with different content (date has ns precision)
        r = requests.post(f"{BASE_URL}/api/backups/run", headers=admin_headers,
                          json={"device_ids": [DEV_2HOPS_ID]}, timeout=120)
        assert r.status_code == 200, r.text
        res1 = r.json()["results"]
        assert len(res1) == 1
        assert res1[0]["ok"] is True, res1[0]
        assert res1[0]["changed"] is True
        assert "content" not in res1[0]  # content must be stripped

        time.sleep(1.1)
        r = requests.post(f"{BASE_URL}/api/backups/run", headers=admin_headers,
                          json={"device_ids": [DEV_2HOPS_ID]}, timeout=120)
        assert r.status_code == 200
        res2 = r.json()["results"]
        assert res2[0]["ok"] is True

        # List versions
        lst = requests.get(f"{BASE_URL}/api/backups", headers=admin_headers,
                          params={"device_id": DEV_2HOPS_ID}, timeout=10).json()
        assert len(lst) >= 2
        for item in lst:
            assert "content" not in item
        newest, older = lst[0], lst[1]
        # Detail has content
        d = requests.get(f"{BASE_URL}/api/backups/{newest['id']}", headers=admin_headers, timeout=10).json()
        assert "content" in d and d["content"]
        # Diff
        df = requests.get(f"{BASE_URL}/api/backups/{newest['id']}/diff/{older['id']}",
                          headers=admin_headers, timeout=10).json()
        assert "diff" in df
        assert df["identical"] is False
        # Summary
        summ = requests.get(f"{BASE_URL}/api/backups/summary", headers=admin_headers, timeout=10).json()
        entry = next((x for x in summ if x["device_id"] == DEV_2HOPS_ID), None)
        assert entry is not None
        assert entry["count"] >= 2

    def test_run_all_skips_no_command_devices(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/backups/run", headers=admin_headers, json={}, timeout=180)
        assert r.status_code == 200, r.text
        results = r.json()["results"]
        # All returned results must have a backup command evaluated (no "skipped" ones)
        assert not any(r.get("skipped") for r in results), [r for r in results if r.get("skipped")]

    def test_run_linux_no_command_explicit_is_skipped(self, admin_headers):
        # Create a linux device without backup_command
        r = requests.post(f"{BASE_URL}/api/devices", headers=admin_headers, json={
            "name": "TEST_linux_no_bcmd", "host": "127.0.0.1", "port": 22, "username": "root",
            "device_type": "linux", "backup_enabled": True}, timeout=10)
        assert r.status_code == 200, r.text
        did = r.json()["id"]
        self._created.append(did)
        try:
            r = requests.post(f"{BASE_URL}/api/backups/run", headers=admin_headers,
                              json={"device_ids": [did]}, timeout=60)
            assert r.status_code == 200
            res = r.json()["results"]
            assert len(res) == 1
            assert res[0]["ok"] is False
            assert res[0].get("skipped") is True
        finally:
            requests.delete(f"{BASE_URL}/api/devices/{did}", headers=admin_headers, timeout=10)

    def test_delete_backup_admin_only(self, admin_headers, operator_headers):
        lst = requests.get(f"{BASE_URL}/api/backups", headers=admin_headers,
                          params={"device_id": DEV_2HOPS_ID}, timeout=10).json()
        assert lst, "need a backup to delete"
        bid = lst[-1]["id"]  # oldest
        # operator forbidden
        r = requests.delete(f"{BASE_URL}/api/backups/{bid}", headers=operator_headers, timeout=10)
        assert r.status_code == 403
        # admin ok
        r = requests.delete(f"{BASE_URL}/api/backups/{bid}", headers=admin_headers, timeout=10)
        assert r.status_code == 200
        assert r.json().get("deleted") == 1


# ---------- 6. Devices: backup_enabled + backup_command fields ----------
class TestDeviceBackupFields:
    def test_put_and_reflect(self, admin_headers):
        devs = requests.get(f"{BASE_URL}/api/devices", headers=admin_headers, timeout=10).json()
        cur = next(d for d in devs if d["id"] == DEV_2HOPS_ID)
        base = {k: cur.get(k) for k in ["name", "host", "port", "username", "device_type", "tags",
                                        "agent_id", "description", "backup_command"]}
        # set false
        r = requests.put(f"{BASE_URL}/api/devices/{DEV_2HOPS_ID}", headers=admin_headers,
                         json={**base, "backup_enabled": False}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["backup_enabled"] is False
        # restore
        r = requests.put(f"{BASE_URL}/api/devices/{DEV_2HOPS_ID}", headers=admin_headers,
                         json={**base, "backup_enabled": True}, timeout=10)
        assert r.status_code == 200
        assert r.json()["backup_enabled"] is True


# ---------- 7. Final cleanup: leave automation settings clean ----------
class TestZFinalCleanup:
    def test_reset_settings(self, admin_headers):
        r = requests.put(f"{BASE_URL}/api/automation/settings", headers=admin_headers, json={
            "ping_enabled": True, "ping_interval_min": 5, "backup_enabled": True, "backup_hour": 3,
            "notify_agents": True, "notify_devices": False,
            "clear_telegram_token": True, "telegram_chat_id": "", "webhook_url": ""}, timeout=10)
        assert r.status_code == 200
        s = r.json()
        assert s["has_telegram_token"] is False
        assert s["ping_interval_min"] == 5
        assert s["backup_hour"] == 3
        assert s["notify_devices"] is False
