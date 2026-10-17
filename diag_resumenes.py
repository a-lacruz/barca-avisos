"""Diagnostico: quien publico el resumen de los ultimos partidos del Barca.

Lee el feed oficial, toma los ultimos partidos ya jugados y busca en YouTube
(innertube, sin key) quien los cubrio, con enlace y retardo aproximado.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avisar  # noqa: E402

VERSION = "2.20261013.00.00"

# canales preferidos: primero los globales; DAZN Portugal es geo-bloqueado
# a IP de Portugal (nota del usuario 2026-10-14), va despues
BLANCA = {"fc barcelona", "laliga", "planetasporttv", "dazn portugal"}
GEO_PT = {"dazn portugal"}


def _post(url, datos):
    req = urllib.request.Request(
        url, data=json.dumps(datos).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def buscar(consulta, cantidad=25):
    url = f"https://www.youtube.com/youtubei/v1/search?key={CLAVE}"
    ctx = {"client": {"clientName": "WEB", "clientVersion": VERSION}}
    r = _post(url, {"query": consulta, "context": ctx})
    res = []

    def caminar(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v:
                titulo = "".join(x.get("text", "") for x in v.get("title", {}).get("runs", []))
                canal, cid = "", ""
                owner = v.get("ownerText", {}).get("runs", [])
                if owner:
                    canal = owner[0].get("text", "")
                    cid = owner[0].get("navigationEndpoint", {}).get(
                        "browseEndpoint", {}).get("browseId", "")
                fecha = v.get("publishedTimeText", {}).get("simpleText", "")
                res.append({"id": v.get("videoId"), "titulo": titulo,
                            "canal": canal, "canal_id": cid, "retardo": fecha})
            for x in o.values():
                caminar(x)
        elif isinstance(o, list):
            for x in o:
                caminar(x)

    caminar(r)
    return res[:cantidad]


def main():
    ahora = datetime.now(timezone.utc)
    ics = avisar.descargar(avisar.ICAL_URL).decode("utf-8", "replace")
    partidos = avisar.leer_partidos(ics)
    jugados = [p for p in partidos if p["inicio"] < ahora][-4:]
    jugados.sort(key=lambda p: p["inicio"], reverse=True)

    for p in jugados:
        fecha = avisar.texto_lisboa(p["inicio"], "%d/%m")
        print(f"\n=== {fecha} | {p['local']} vs {p['visitante']} "
              f"({p['competicion'][:30]}) ===")
        resultados = buscar(f"{p['local']} {p['visitante']} resumen goles")
        blancas = [r for r in resultados if r["canal"].lower() in BLANCA]
        otros = [r for r in resultados if r not in blancas][:3]
        if blancas:
            for r in blancas:
                geo = " (GEO: solo Portugal)" if r["canal"].lower() in GEO_PT else ""
                print(f"  BLANCA  [{r['retardo']:>12}] {r['canal']}{geo}")
                print(f"          {r['titulo'][:66]}")
                print(f"          https://youtu.be/{r['id']}")
        else:
            print("  BLANCA  (ningun canal oficial publico este partido)")
        for r in otros:
            print(f"  otro    [{r['retardo']:>12}] {r['canal'][:24]:24} "
                  f"https://youtu.be/{r['id']}")


if __name__ == "__main__":
    main()
