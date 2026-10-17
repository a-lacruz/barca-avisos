"""Duracion y canal de videos concretos de YouTube (innertube player, sin key).

Uso: python duracion_video.py <video_id> [video_id...]
"""
import json
import sys
import urllib.request

VERSION = "2.20261013.00.00"


def datos_video(video_id):
    url = "https://www.youtube.com/youtubei/v1/player"
    cuerpo = {"videoId": video_id,
              "context": {"client": {"clientName": "WEB", "clientVersion": VERSION}}}
    req = urllib.request.Request(
        url, data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    micro = d.get("microformat", {}).get("playerMicroformatRenderer", {})
    dur = micro.get("lengthSeconds", "?")
    try:
        dur = f"{int(dur) // 60}:{int(dur) % 60:02d}"
    except (TypeError, ValueError):
        dur = "?"
    estado = d.get("playabilityStatus", {}).get("status", "?")
    return micro.get("author", "?"), dur, micro.get("publishDate", "?")[:16], estado


if __name__ == "__main__":
    for vid in sys.argv[1:]:
        try:
            autor, dur, fecha, estado = datos_video(vid)
            print(f"  {vid} | {dur:>6} | {estado:6} | {autor[:28]:28} | {fecha}")
        except Exception as e:  # noqa: BLE001
            print(f"  {vid} | ERR {e}")
