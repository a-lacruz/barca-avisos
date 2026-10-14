"""Buscar en YouTube (innertube, sin key) y listar resultados con canal y fecha."""
import json
import sys
import urllib.request

CLAVE = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
VERSION = "2.20261013.00.00"


def _post(url, datos):
    req = urllib.request.Request(
        url, data=json.dumps(datos).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def buscar(consulta, cantidad=10):
    url = f"https://www.youtube.com/youtubei/v1/search?key={CLAVE}"
    ctx = {"client": {"clientName": "WEB", "clientVersion": VERSION}}
    r = _post(url, {"query": consulta, "context": ctx})
    resultados = []

    def caminar(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v:
                titulo = "".join(x.get("text", "") for x in v.get("title", {}).get("runs", []))
                canal, canal_id = "", ""
                owner = v.get("ownerText", {}).get("runs", [])
                if owner:
                    canal = owner[0].get("text", "")
                    nav = owner[0].get("navigationEndpoint", {}).get("browseEndpoint", {})
                    canal_id = nav.get("browseId", "")
                fecha = v.get("publishedTimeText", {}).get("simpleText", "")
                resultados.append((v.get("videoId"), titulo, canal, fecha, canal_id))
            for x in o.values():
                caminar(x)
        elif isinstance(o, list):
            for x in o:
                caminar(x)

    caminar(r)
    return resultados[:cantidad]


if __name__ == "__main__":
    consulta = " ".join(sys.argv[1:]) or "Galatasaray Barcelona resumen goles"
    print("busqueda:", consulta)
    for vid, titulo, canal, fecha, cid in buscar(consulta):
        print(f"  [{fecha:>12}] {titulo[:58]}")
        print(f"      {canal[:30]:30} ({cid}) https://youtu.be/{vid}")
