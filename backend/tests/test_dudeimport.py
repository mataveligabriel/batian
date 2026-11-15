"""Importação do The Dude: lê um dude.db (formato de campos do Dude) e monta os mapas com submapas e ligações."""
import os
import socket
import sqlite3
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dudeimport as D  # noqa: E402


def enc(**f):
    out = b""
    for k, v in f.items():
        k = k.replace("_", "-") if k.startswith("sys_") else k
        b = v if isinstance(v, bytes) else v.encode() if isinstance(v, str) else int(v).to_bytes(4, "little", signed=True)
        out += bytes([len(k)]) + k.encode() + len(b).to_bytes(2, "big") + b
    return out


def mapobj(oid, name, lst):
    return enc(ackedColor=0, elementsID=lst, sys_id=oid, sys_name=name, sys_type=10)


def el(oid, lst, item_type, item_id, x, y, kind=0, lf=-1, lt=-1, name=""):
    return enc(itemAckedColor=0, itemID=item_id, itemType=item_type, itemX=x, itemY=y, linkFrom=lf, linkTo=lt, sys_id=oid,
               sys_name=name, sys_type=lst, type=kind)


def dev(oid, name, ip, type_id=900):
    return enc(addresses=socket.inet_aton(ip), dnsNames=b"", pwd=b"segredo", sys_id=oid, sys_name=name, typeID=type_id)


def make_db(path):
    c = sqlite3.connect(path)
    c.execute("create table objs (id integer primary key, obj blob)")
    rows = {
        900: enc(allowedServices=b"", sys_name="MikroTik Device"),
        10: mapobj(10, "Local", 1), 20: mapobj(20, "Rede", 2), 30: mapobj(30, "POP Sul", 3),
        101: dev(101, "R1", "10.0.0.1"), 102: dev(102, "R2", "10.0.0.2"), 103: dev(103, "SW", "10.0.0.3", 0),
        # Local -> Rede
        200: el(200, 1, 2, 20, 50, 50),
        # Rede: R1, submapa POP Sul, botão de voltar para Local, texto, ligação R1 -> POP Sul
        210: el(210, 2, 0, 101, 100, 100), 211: el(211, 2, 2, 30, 300, 100), 212: el(212, 2, 2, 10, 10, 10),
        213: el(213, 2, 3, -1, 200, 20, name="Backbone"), 214: el(214, 2, 0, -1, 0, 0, kind=1, lf=210, lt=211),
        215: el(215, 2, 0, -1, 0, 0, kind=1, lf=210, lt=211),               # rota paralela
        # POP Sul: R2 e SW ligados, mais voltar para Rede (pai)
        220: el(220, 3, 0, 102, 10, 10), 221: el(221, 3, 0, 103, 90, 10), 222: el(222, 3, 0, -1, 0, 0, kind=1, lf=220, lt=221),
        223: el(223, 3, 2, 20, 0, 90),
    }
    c.executemany("insert into objs values (?, ?)", rows.items())
    c.commit(); c.close()


def test_parse_tree_and_build():
    d = tempfile.mkdtemp()
    db = os.path.join(d, "dude.db"); make_db(db)
    tgz = os.path.join(d, "export.tgz")
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(db, arcname="dude.db")
    p = D.extract_db(tgz, os.path.join(d, "out.db"))
    parsed = D.parse_db(p)
    assert set(parsed["maps"]) == {10, 20, 30} and parsed["devices"][101]["ips"] == ["10.0.0.1"]
    assert parsed["devices"][101]["type"] == "MikroTik Device" and "pwd" not in parsed["devices"][101]
    tr = D.tree(parsed)
    assert tr["roots"] == [10] and tr["parent"] == {20: 10, 30: 20}
    assert (20, 212) in tr["skip"] and (30, 223) in tr["skip"]          # botões de voltar não viram submapa
    pv = D.preview(parsed, {"10.0.0.2": {"id": "x", "name": "R2 já no BastiON"}})
    assert pv["devices_in_maps"] == 3 and [m["ip"] for m in pv["match"]] == ["10.0.0.2"] and len(pv["new"]) == 2
    assert pv["tree"][0]["children"][0]["name"] == "Rede" and pv["tree"][0]["children"][0]["children"][0]["name"] == "POP Sul"
    out = D.build_maps(parsed, {101: "b1", 102: "b2", 103: "b3"}, {10: "m10", 20: "m20", 30: "m30"})
    rede = out["m20"]
    assert sorted(n["kind"] for n in rede["nodes"]) == ["device", "label", "submap"] and len(rede["links"]) == 2
    assert rede["nodes"][0]["x"] == 160                                  # 100 * 1.6
    assert len(out["m30"]["links"]) == 1 and len(out["m30"]["nodes"]) == 2
    assert D.device_type("MikroTik Device") == "mikrotik" and D.device_type("Bridge") == "other"
