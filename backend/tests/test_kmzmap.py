"""Mapas KMZ: leitura de KML/KMZ (pastas, estilos, pontos, linhas, áreas) e classificação dos pontos."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import kmzmap as K  # noqa: E402

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document><name>Rede Exemplo</name>
  <Style id="fibra"><LineStyle><color>ff0000ff</color><width>3</width></LineStyle></Style>
  <StyleMap id="fm"><Pair><key>normal</key><styleUrl>#fibra</styleUrl></Pair><Pair><key>highlight</key><styleUrl>#x</styleUrl></Pair></StyleMap>
  <Folder><name>POPs</name>
    <Placemark><name>POP Centro</name><Point><coordinates>-41.50,-20.70,0</coordinates></Point></Placemark>
    <Placemark><name>NOME DO EQUIPAMENTO</name><description><![CDATA[<b>IP</b>: 10.0.0.1<br>rack 2]]></description>
      <Point><coordinates>-41.51,-20.71</coordinates></Point></Placemark>
  </Folder>
  <Folder><name>Rede FTTH</name><Folder><name>CTOs</name>
    <Placemark><name>CTO-01</name><Point><coordinates>-41.52,-20.72</coordinates></Point></Placemark>
    <Placemark><name>A-17</name><Point><coordinates>-41.53,-20.73</coordinates></Point></Placemark>
  </Folder></Folder>
  <Folder><name>Cabos</name>
    <Placemark><name>Backbone 1</name><styleUrl>#fm</styleUrl>
      <LineString><coordinates>-41.50,-20.70 -41.505,-20.705 -41.51,-20.71</coordinates></LineString></Placemark>
    <Placemark><name>Área</name><Style><PolyStyle><color>7f00ff00</color></PolyStyle></Style>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>-41,-20 -41.1,-20 -41.1,-20.1 -41,-20</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark><name>Multi</name><MultiGeometry><LineString><coordinates>-41,-20 -41.2,-20.2</coordinates></LineString>
      <Point><coordinates>-41.6,-20.6</coordinates></Point></MultiGeometry></Placemark>
  </Folder>
</Document></kml>"""


def test_parse_and_classify():
    d = K.parse(K.read_upload(KML.encode()))
    assert d["name"] == "Rede Exemplo"
    paths = [f["path"] for f in d["folders"]]
    assert paths == ["POPs", "Rede FTTH / CTOs", "Cabos"]
    assert len(d["points"]) == 5 and len(d["lines"]) == 2 and len(d["polys"]) == 1
    bb = next(x for x in d["lines"] if x["name"] == "Backbone 1")
    assert bb["color"] == "#ff0000" and bb["width"] == 3 and bb["pts"][0] == [-20.7, -41.5] and 1.4 < bb["km"] < 1.6
    assert len(bb["pts"]) == 2                          # ponto do meio é colinear: some na simplificação
    assert d["polys"][0]["fill"] == "#00ff00"
    eq = next(p for p in d["points"] if p["name"] == "NOME DO EQUIPAMENTO")
    assert "IP : 10.0.0.1" in eq["desc"] and "\n" in eq["desc"]
    r = K.classify(d, [{"id": "d1", "name": "outro", "host": "10.0.0.1"}, {"id": "d2", "name": "POP-Centro", "host": "x"}])
    kinds = {p["name"]: p["kind"] for p in d["points"]}
    assert kinds["NOME DO EQUIPAMENTO"] == "device" and eq["device_id"] == "d1"
    assert kinds["POP Centro"] == "device"             # casou pelo nome com o equipamento "POP-Centro"
    assert kinds["CTO-01"] == "box" and kinds["A-17"] == "box"     # pela pasta "CTOs"
    assert kinds["Multi"] == "point" and r["matched"] == 2
    s = K.summary(d)
    assert s["points"] == 5 and s["kinds"]["box"] == 2
    assert K.unpack(K.pack(d))["lines"][0]["name"] == "Backbone 1"
    assert K.bbox(d) == {"s": -20.73, "w": -41.6, "n": -20.0, "e": -41.0}


def test_kmz_and_guards():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("files/icone.png", b"x")
        z.writestr("doc.kml", KML)
    assert "Rede Exemplo" in K.read_upload(buf.getvalue())
    for bad in (b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY a 'b'>]><kml/>", b"<html></html>", b"PK\x03\x04garbage"):
        try:
            K.read_upload(bad)
            assert False, bad
        except K.KmzError:
            pass
    try:
        K.parse(K.read_upload(b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>'))
        assert False
    except K.KmzError:
        pass


def test_helpers():
    assert K.kml_color("ff0000ff") == "#ff0000" and K.kml_color("zz") is None
    assert K.parse_coords("-41,-20,0  bad -200,10 -41.5,-20.5") == [(-20.0, -41.0), (-20.5, -41.5)]
    f = [{"id": "f1", "path": "A", "kind": "auto", "visible": True}]
    assert K.clean_folders([{"id": "f1", "kind": "box", "visible": False}, {"id": "zz"}], f)[0] == {"id": "f1", "path": "A", "kind": "box", "visible": False}
    assert K.clean_folders([{"id": "f1", "kind": "evil"}], f)[0]["kind"] == "auto"
