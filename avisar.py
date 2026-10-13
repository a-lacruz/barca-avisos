#!/usr/bin/env python3
"""
Avisos del FC Barcelona por Telegram (ejecutado en la nube, GitHub Actions).

Lee el calendario oficial del Barça (feed .ics del boton "Sincroniza" de
fcbarcelona.es, servicio eCal) y avisa por Telegram 1 hora antes de cada
partido del primer equipo, en hora de Lisboa. Si el PC/servidor no estaba
disponible a la hora, avisa igualmente cuando el partido ya ha empezado.

Variables de entorno:
    TELEGRAM_BOT_TOKEN   token del bot de Telegram
    TELEGRAM_CHAT_ID     chat id del usuario
    MINUTOS_AVISO        margen de aviso en minutos (por defecto 60)

Estado entre ejecuciones: estado.json (commiteado por el workflow).
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

ICAL_URL = os.environ.get(
    "ICAL_URL",
    "https://ics.ecal.com/ecal-sub/6aceb1a56051400002e3e8d5/FCBarcelona.ics",
)
MINUTOS_AVISO = int(os.environ.get("MINUTOS_AVISO", "60"))
MARGEN_TARDIO = 90  # minutos tras el inicio en los que aun se avisa "ya empezo"
ESTADO_PATH = os.environ.get("ESTADO_PATH", "estado.json")

EMOJIS = [
    ("champions", "\U0001f31f"),   # estrella
    ("copa del rey", "\U0001f451"),  # corona
    ("supercopa", "\U0001f6e1"),   # escudo
    ("laliga", "\U0001f3c6"),      # trofeo
    ("amistoso", "\U0001f91d"),    # apretón de manos
]
EMOJI_POR_DEFECTO = "⚽"

# --------------------------------------------------------------------------
# Hora de Lisboa (sin dependencias: regla oficial de la UE para Portugal)
# --------------------------------------------------------------------------


def _ultimo_domingo(year: int, month: int) -> datetime:
    dia = datetime(year, month, 31 if month in (3, 10) else 30, tzinfo=timezone.utc)
    while dia.weekday() != 6:
        dia -= timedelta(days=1)
    return dia


def offset_lisboa(dt_utc: datetime) -> timedelta:
    """Desfase de Lisboa para una fecha UTC (WET=0, WEST=+1)."""
    inicio = _ultimo_domingo(dt_utc.year, 3).replace(hour=1)
    fin = _ultimo_domingo(dt_utc.year, 10).replace(hour=1)
    return timedelta(hours=1) if inicio <= dt_utc < fin else timedelta(hours=0)


def hora_lisboa(dt_utc: datetime) -> datetime:
    return dt_utc + offset_lisboa(dt_utc)


def texto_lisboa(dt_utc: datetime, formato: str = "%a %d/%m %H:%M") -> str:
    return hora_lisboa(dt_utc).strftime(formato)


# --------------------------------------------------------------------------
# Calendario .ics oficial
# --------------------------------------------------------------------------


def descargar_ics() -> str:
    req = urllib.request.Request(ICAL_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def leer_partidos(ics: str):
    ics = re.sub(r"\r?\n[ \t]", "", ics)  # continuation lines
    partidos = []
    dentro = False
    ev = {}
    for linea in ics.splitlines():
        if linea == "BEGIN:VEVENT":
            dentro, ev = True, {}
            continue
        if linea == "END:VEVENT":
            dentro = False
            dt = ev.get("DTSTART", "")
            m = re.match(r"^(\d{8})T(\d{6})Z$", dt)
            if not m or "SUMMARY" not in ev:
                continue
            inicio = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc
            )
            resumen = re.sub(r"\s*\(Time TBC\)\s*$", "", ev["SUMMARY"].strip())
            comp = ""
            desc = ev.get("DESCRIPTION", "")
            if desc:
                primera = desc.split("\\n")[0]
                if "|" in primera:
                    comp = primera.split("|")[0].strip()
            local, visitante = "", ""
            mm = re.match(r"^(.+?)\s+vs\s+(.+)$", resumen)
            if mm:
                local = re.sub(r"^[^\w]+", "", mm.group(1)).strip()
                visitante = mm.group(2).strip()
            partidos.append(
                {
                    "uid": ev.get("UID", resumen),
                    "inicio": inicio,
                    "local": local,
                    "visitante": visitante,
                    "competicion": comp,
                }
            )
            continue
        if dentro and ":" in linea:
            clave = linea.split(":", 1)[0].split(";")[0]
            ev.setdefault(clave, linea.split(":", 1)[1])
    return partidos


def emoji_competicion(comp: str) -> str:
    t = (comp or "").lower()
    for palabra, emoji in EMOJIS:
        if palabra in t:
            return emoji
    return EMOJI_POR_DEFECTO


# --------------------------------------------------------------------------
# Estado (evitar repetir avisos)
# --------------------------------------------------------------------------


def cargar_estado() -> dict:
    if os.path.exists(ESTADO_PATH):
        try:
            with open(ESTADO_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"avisados": {}}


def guardar_estado(estado: dict) -> None:
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------


def telegram(method: str, **kwargs) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise SystemExit(
            "Faltan TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (Secretos del repositorio)."
        )
    kwargs.setdefault("chat_id", chat)
    data = urllib.parse.urlencode(kwargs).encode()
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, data=data, timeout=60) as r:
        respuesta = json.loads(r.read().decode())
    if not respuesta.get("ok"):
        raise RuntimeError(f"Telegram devolvio error: {respuesta}")


def enviar_aviso(partido: dict, minutos: int | None) -> None:
    emoji = emoji_competicion(partido["competicion"])
    hora = texto_lisboa(partido["inicio"])
    if minutos is None:
        titulo = f"{emoji} ¡El partido ya ha empezado!"
        pie = f"Empezó a las {hora} hora de Lisboa (el PC/servidor no estaba disponible a tiempo)."
    else:
        titulo = f"{emoji} El Barça juega en {minutos} minutos"
        pie = f"Empieza a las {hora} hora de Lisboa."
    caption = (
        f"{titulo}\n"
        f"\U0001f3d5️ {partido['local']} vs {partido['visitante']}\n"
    )
    if partido["competicion"]:
        caption += f"{emoji} {partido['competicion']}\n"
    caption += f"\U0001f552 {pie}"
    # foto: escudo oficial del Barça (CDN de ESPN)
    try:
        telegram(
            "sendPhoto",
            photo="https://a.espncdn.com/i/teamlogos/soccer/500/83.png",
            caption=caption,
        )
    except Exception:
        telegram("sendMessage", text=caption)
    print(f"  -> Telegram enviado: {caption.splitlines()[0]}")


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------


def main() -> int:
    ahora = datetime.now(timezone.utc)
    print(f"[{ahora:%Y-%m-%d %H:%M} UTC] Actualizando calendario del Barça...")

    partidos = leer_partidos(descargar_ics())
    partidos = [p for p in partidos if p["inicio"] > ahora - timedelta(minutes=MARGEN_TARDIO)]
    partidos.sort(key=lambda p: p["inicio"])
    print(f"Partidos proximos en el calendario oficial: {len(partidos)}")

    estado = cargar_estado()
    avisados = estado.setdefault("avisados", {})
    enviados = 0

    for p in partidos:
        uid = p["uid"]
        if uid in avisados:
            continue
        inicio = p["inicio"]
        if ahora >= inicio - timedelta(minutes=MINUTOS_AVISO) and ahora < inicio:
            restan = int((inicio - ahora).total_seconds() // 60)
            enviar_aviso(p, restan)
            avisados[uid] = {"aviso": "normal", "inicio": inicio.isoformat()}
            enviados += 1
        elif ahora >= inicio and ahora < inicio + timedelta(minutes=MARGEN_TARDIO):
            enviar_aviso(p, None)
            avisados[uid] = {"aviso": "tardio", "inicio": inicio.isoformat()}
            enviados += 1
        elif ahora >= inicio + timedelta(minutes=MARGEN_TARDIO):
            avisados[uid] = {"aviso": "perdido", "inicio": inicio.isoformat()}

    if "--test" in sys.argv:
        sig = next((p for p in partidos if p["inicio"] > ahora), None)
        if sig:
            restan = int((sig["inicio"] - ahora).total_seconds() // 60)
            enviar_aviso(sig, restan)
            print("  (modo test: sin marcar en el estado)")
            return 0
        print("No hay proximos partidos.")
        return 0

    guardar_estado(estado)
    print(f"Avisos enviados en esta ejecucion: {enviados}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
