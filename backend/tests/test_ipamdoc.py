import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ipamdoc as D  # noqa: E402


def test_clean_and_helpers():
    assert D.clean_subnet({"prefix": "10.0.0.7/24", "vlan": "100"})["prefix"] == "10.0.0.0/24"
    assert D.clean_ip({"ip": "10.0.0.5", "mac": "AABB.CCDD.EEFF"})["mac"] == "aa:bb:cc:dd:ee:ff"
    for bad in ({"ip": "x"}, {"ip": "10.0.0.1", "mac": "12"}):
        try:
            D.clean_ip(bad)
            raise AssertionError(bad)
        except D.DocError:
            pass
    subs = [{"id": "a", "prefix": "10.0.0.0/16"}, {"id": "b", "prefix": "10.0.1.0/24"}]
    assert D.subnet_of("10.0.1.9", subs)["id"] == "b" and D.subnet_of("10.0.9.9", subs)["id"] == "a" and D.subnet_of("192.0.2.1", subs) is None
    assert D.free_ips("10.0.0.0/30", ["10.0.0.1"]) == ["10.0.0.2"]
    rows = D.parse_csv_rows("IP;Hostname;Situação\n10.0.0.1;gw;Reservado\n;vazio;\n")
    assert rows == [{"ip": "10.0.0.1", "hostname": "gw", "status": "reserved"}]


def test_phpipam_client_and_mapping():
    seen = []

    async def req(m, url, headers, auth):
        seen.append((m, url, headers.get("token"), auth))
        if url.endswith("/user/"):
            return 200, {"success": True, "data": {"token": "T1"}}
        if url.endswith("/sections/"):
            return 200, {"success": True, "data": [{"id": "1", "name": "A"}]}
        return 404, {"code": 404, "success": False, "message": "No subnets found"}

    async def run():
        p = D.PhpIpam("https://ipam.exemplo.net/", "app", req, user="u", password="p")
        await p.login()
        return await p.get("sections"), await p.get("sections/1/subnets")
    secs, subs = asyncio.run(run())
    assert secs == [{"id": "1", "name": "A"}] and subs == [] and seen[0][3] == ("u", "p") and seen[1][2] == "T1"
    sn = D.php_subnet({"id": "9", "subnet": "10.1.0.0", "mask": "24", "vlanId": "3"}, {"3": {"number": "200"}}, {})
    assert sn["prefix"] == "10.1.0.0/24" and sn["vlan"] == 200
    assert D.php_subnet({"isFolder": "1", "subnet": "", "mask": ""}, {}, {}) is None
    a = D.php_address({"ip": "10.1.0.1", "tag": "3", "is_gateway": "1"}, D.PHP_TAGS)
    assert a["status"] == "reserved" and a["notes"].startswith("gateway")
