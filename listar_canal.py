"""Listar los videos recientes de un canal de YouTube (API innertube, sin key).

Uso: python listar_canal.py <channel_id> [cantidad]
Devuelve titulo, fecha relativa y enlace de cada video de la pestana "videos".
Usa el formato actual de YouTube (lockupViewModel).
"""
import json
import sys
import urllib.request

VERSION = "2.20261013.00.00"
PARAMS_VIDEOS = "EgZ2aWRlb3PyBgQKAjoA"  # constante publica de la pestana "videos"


def _post(url, datos):
    req = urllib.request.Request(
        url, data=json.dumps(datos).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def videos_de_canal(canal, cantidad=10):
    url = "https://www.youtube.com/youtubei/v1/browse"
    ctx = {"client": {"clientName": "WEB", "clientVersion": VERSION}}
    r = _post(url, {"browseId": canal, "params": PARAMS_VIDEOS, "context": ctx})
    items = []

    def caminar(o):
        if isinstance(o, dict):
            if "lockupViewModel" in o:
                items.append(o["lockupViewModel"])
            for x in o.values():
                caminar(x)
        elif isinstance(o, list):
            for x in o:
                caminar(x)

    caminar(r)
    salida = []
    for it in items[:cantidad]:
        vid = it.get("contentId", "")
        meta = it.get("metadata", {}).get("lockupMetadataViewModel", {})
        titulo = meta.get("title", {}).get("content", "")
        fecha = ""
        for fila in (meta.get("metadata", {}) or {}).get(
                "contentMetadataViewModel", {}).get("metadataRows", []):
            for parte in fila.get("metadataParts", []):
                texto = parte.get("text", {}).get("content", "")
                if "ago" in texto:
                    fecha = texto
        if vid and titulo:
            salida.append((vid, titulo, fecha))
    return salida


if __name__ == "__main__":
    canal = sys.argv[1]
    cantidad = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    for vid, titulo, fecha in videos_de_canal(canal, cantidad):
        print(f"  {fecha:>14} | {titulo[:70]}")
        print(f"               https://youtu.be/{vid}")
