import asyncio
import ftplib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ftpserver as F  # noqa: E402


def test_ftp_passive_active_and_sandbox():
    root = tempfile.mkdtemp()
    events = []

    async def auth(u, p, ip):
        if (u, p) == ("olt", "s3nha123"):
            return F.Account("a1", "olt", os.path.join(root, "olt"), True, 1_000_000)
        if (u, p) == ("ro", "s3nha123"):
            return F.Account("a2", "ro", os.path.join(root, "olt"), False)

    async def ev(e):
        events.append(e)

    def client(port):
        out = {}
        for passive in (True, False):
            f = ftplib.FTP()
            f.connect("127.0.0.1", port, timeout=10)
            f.login("olt", "s3nha123")
            f.set_pasv(passive)
            data = os.urandom(200_000)
            f.storbinary("STOR startrun.dat", io.BytesIO(data))
            b = io.BytesIO()
            f.retrbinary("RETR startrun.dat", b.write)
            out[passive] = b.getvalue() == data and f.size("startrun.dat") == 200_000
            try:
                f.retrbinary("RETR ../../etc/passwd", b.write)
                out["escape"] = True
            except ftplib.error_perm:
                out["escape"] = False
            try:
                f.storbinary("STOR grande.bin", io.BytesIO(b"x" * 1_100_000))
                out["limit"] = False
            except ftplib.error_temp:
                out["limit"] = True
            f.quit()
        f = ftplib.FTP()
        f.connect("127.0.0.1", port, timeout=10)
        f.login("ro", "s3nha123")
        try:
            f.storbinary("STOR y", io.BytesIO(b"1"))
            out["ro"] = False
        except ftplib.error_perm:
            out["ro"] = True
        f.quit()
        return out

    async def run():
        net = F.LocalNet("127.0.0.1", list(range(31500, 31504)))
        srv = await asyncio.start_server(lambda r, w: F.Session(r, w, w.get_extra_info("peername")[0], net, auth, ev).run(), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        out = await asyncio.to_thread(client, port)
        srv.close()
        return out

    out = asyncio.run(run())
    assert out == {True: True, False: True, "escape": False, "limit": True, "ro": True}
    assert not os.path.exists(os.path.join(root, "olt", "grande.bin"))
    assert any(e["action"] == "upload" and e["ok"] and e["bytes"] == 200_000 for e in events)


def test_settings():
    c = F.clean_settings({"local_pasv": "30000-30003", "agents": {"ag1": {"ip": "10.0.0.1"}}})
    assert F.parse_range(c["local_pasv"]) == [30000, 30001, 30002, 30003] and c["agents"]["ag1"]["ip"] == "10.0.0.1"
    for bad in ({"jump_port": 21}, {"local_pasv": "abc"}, {"agents": {"x": {"ip": "nope"}}}):
        try:
            F.clean_settings(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
