#!/usr/bin/env python3
"""
Avisos del FC Barcelona por Telegram (GitHub Actions, gratis y 24/7).

- Lee el calendario oficial del Barça (.ics del boton "Sincroniza" de fcbarcelona.es).
- Avisa por Telegram en escalera, siempre en hora de Lisboa:
    T-90  tarjeta con los escudos de los dos equipos y el logo de la competicion.
    T-60  recordatorio en texto.
    T-15  recordatorio en texto.
    T-0   aviso cuando el partido comienza.
- VIGIA PARA EL MOMENTO EXACTO: GitHub entrega los cron programados con
  retraso (5 h medidas en 2026-10), asi que el workflow solo tiene una
  reserva fija cada 6 h; ese job duerme hasta la etapa siguiente y avisa
  en el minuto exacto, y si se queda sin presupuesto encadena otro job
  identico con workflow_dispatch (esos arrancan en segundos).

Secretos: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Opcional: ICAL_URL.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ICAL_URL = os.environ.get(
    "ICAL_URL",
    "https://ics.ecal.com/ecal-sub/6aceb1a56051400002e3e8d5/FCBarcelona.ics",
)
MARGEN_INICIO = 25   # tras el inicio, aun tiene sentido avisar "¡ha empezado!"
MARGEN_FIN = 360     # ventana para resultado y enlace tras el inicio (6 h)
ESTADO_PATH = os.environ.get("ESTADO_PATH", "estado.json")
CACHE_DIR = os.environ.get("CACHE_DIR", ".cache")

# escalera de avisos: (id, minutos antes del inicio, con tarjeta imagen)
# cambiar tiempos = editar solo esta tabla
# tfin es negativo: minutos DESPUES del inicio (el partido ya ha acabado)
ETAPAS = [
    ("t90", 90, True),
    ("t60", 60, False),
    ("t15", 15, False),
    ("t0", 0, False),
    ("tfin", -125, False),
]

# si al tfin aun no hay enlace al resumen, se vuelve a mirar en estos minutos
# tras el inicio (el aviso de resultado ya salio; el enlace llegaria aparte)
REINTENTOS_ENLACE = [155, 210, 300, 360]


def desplazos():
    """(minutos tras el inicio, etapa) en los que toca mirar cada partido."""
    sal = [(-antes, eid) for eid, antes, _ in ETAPAS]
    sal += [(r, "enlace") for r in REINTENTOS_ENLACE]
    return sal

# --------------------------------------------------------------------------
# Resumenes en YouTube (innertube, sin API key): orden de preferencia y filtros# --------------------------------------------------------------------------
# Descartados tras verificacion (2026-10-14): Diario AS (resumen radio),
# ESPN Deportes (analisis), Fox Deportes (El Chiringuito), PlanetaSportTV
# (camaras de espectador). DAZN Portugal: solo visible con IP de Portugal.
CANALES_RESUMEN = ["fc barcelona", "laliga", "dazn portugal"]
GEO_SOLO_PORTUGAL = ["dazn portugal"]
PALABRAS_RESUMEN = ("resumen", "goles", "resumo", "highlights")

# --------------------------------------------------------------------------
# Competiciones (emoji, nombre corto y logo oficial del CDN de ESPN)
# --------------------------------------------------------------------------

COMPETICIONES = [
    # palabra clave en el .ics, emoji, nombre corto, url del logo (variante clara
    # para fondos oscuros), endpoint ESPN
    ("champions", "\U0001f31f", "UEFA Champions League",
     "https://a.espncdn.com/i/leaguelogos/soccer/500-dark/2.png", "uefa.champions"),
    ("copa del rey", "\U0001f451", "Copa del Rey",
     "https://a.espncdn.com/i/leaguelogos/soccer/500-dark/80.png", "esp.copa_del_rey"),
    ("supercopa", "\U0001f6e1️", "Supercopa", None, "esp.supercopa"),
    ("laliga", "\U0001f3c6", "LaLiga",
     "https://a.espncdn.com/i/leaguelogos/soccer/500-dark/15.png", "esp.1"),
    ("amistoso", "\U0001f91d", "Amistoso", None, None),
]
EMOJI_POR_DEFECTO = "⚽"
ESCUDO_BARCa = "https://a.espncdn.com/i/teamlogos/soccer/500/83.png"

# --------------------------------------------------------------------------
# Hora de Lisboa (regla oficial de la UE, sin dependencias)
# --------------------------------------------------------------------------


def _ultimo_domingo(year: int, month: int) -> datetime:
    dia = datetime(year, month, 31 if month in (3, 10) else 30, tzinfo=timezone.utc)
    while dia.weekday() != 6:
        dia -= timedelta(days=1)
    return dia


def hora_lisboa(dt_utc: datetime) -> datetime:
    inicio = _ultimo_domingo(dt_utc.year, 3).replace(hour=1)
    fin = _ultimo_domingo(dt_utc.year, 10).replace(hour=1)
    return dt_utc + (timedelta(hours=1) if inicio <= dt_utc < fin else timedelta(hours=0))


def texto_lisboa(dt_utc: datetime, formato: str = "%a %d/%m %H:%M") -> str:
    return hora_lisboa(dt_utc).strftime(formato)


# --------------------------------------------------------------------------
# Calendario .ics oficial
# --------------------------------------------------------------------------


def descargar(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def leer_partidos(ics: str):
    ics = re.sub(r"\r?\n[ \t]", "", ics)
    partidos, dentro, ev = [], False, {}
    for linea in ics.splitlines():
        if linea == "BEGIN:VEVENT":
            dentro, ev = True, {}
            continue
        if linea == "END:VEVENT":
            dentro = False
            m = re.match(r"^(\d{8})T(\d{6})Z$", ev.get("DTSTART", ""))
            if not m or "SUMMARY" not in ev:
                continue
            inicio = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
                tzinfo=timezone.utc)
            resumen = re.sub(r"\s*\(Time TBC\)\s*$", "", ev["SUMMARY"].strip())
            comp = ""
            desc = ev.get("DESCRIPTION", "")
            if desc and "|" in desc.split("\\n")[0]:
                comp = desc.split("\\n")[0].split("|")[0].strip()
            local, visitante = "", ""
            mm = re.match(r"^(.+?)\s+vs\s+(.+)$", resumen)
            if mm:
                local = re.sub(r"^[^\w]+", "", mm.group(1)).strip()
                visitante = mm.group(2).strip()
            partidos.append({
                "uid": ev.get("UID", resumen),
                "inicio": inicio,
                "local": local,
                "visitante": visitante,
                "competicion": comp,
            })
            continue
        if dentro and ":" in linea:
            ev.setdefault(linea.split(":", 1)[0].split(";")[0], linea.split(":", 1)[1])
    return partidos


def competicion_de(texto: str):
    t = (texto or "").lower()
    for entrada in COMPETICIONES:
        if entrada[0] in t:
            return entrada
    return None


def emoji_competicion(comp: str) -> str:
    c = competicion_de(comp)
    return c[1] if c else EMOJI_POR_DEFECTO


def _parecido(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return _norm(a) == _norm(b) or _similitud(_tokens(a), _tokens(b)) >= 0.6


# --------------------------------------------------------------------------
# ESPN: evento del partido -> clasificacion y resultado final
# --------------------------------------------------------------------------

_posiciones_cache = {}
_resultados_cache = {}


def _evento_espn(partido: dict):
    """(endpoint, evento, {nombre_espn: 'local'|'visitante'}) del partido en ESPN."""
    comp = competicion_de(partido["competicion"])
    if not comp or not comp[4]:
        return None
    endpoint, fecha = comp[4], partido["inicio"].strftime("%Y%m%d")
    sb = json.loads(descargar(
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{endpoint}"
        f"/scoreboard?dates={fecha}").decode())
    for ev in sb.get("events", []):
        compet = (ev.get("competitions") or [{}])[0]
        candidato, cuadra = {}, True
        for c in compet.get("competitors", []):
            nombre = (c.get("team") or {}).get("displayName", "")
            objetivo = (partido["local"] if c.get("homeAway") == "home"
                        else partido["visitante"])
            if not _parecido(nombre, objetivo):
                cuadra = False
            candidato[nombre] = ("local" if c.get("homeAway") == "home"
                                 else "visitante")
        if cuadra:
            return endpoint, ev, candidato
    return None


def posiciones_de(partido: dict) -> dict:
    """{'local': '1', 'visitante': '8'} o {} si la competicion es eliminatoria."""
    if partido["uid"] in _posiciones_cache:
        return _posiciones_cache[partido["uid"]]
    resultado = {}
    try:
        encontrado = _evento_espn(partido)
        if encontrado:
            endpoint, evento, nombres = encontrado
            resumen = json.loads(descargar(
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/{endpoint}"
                f"/summary?event={evento['id']}").decode())
            entradas = []
            for g in (resumen.get("standings") or {}).get("groups", []):
                entradas += (g.get("standings") or {}).get("entries", [])
            for e in entradas:
                equipo = e.get("team")
                if equipo in nombres:
                    rank = next((int(st["value"]) for st in e.get("stats", [])
                                 if st.get("name") == "rank"), None)
                    if rank:
                        resultado[nombres[equipo]] = str(rank)
    except Exception as e:  # noqa: BLE001 - sin tabla no se avisa igual
        print(f"  (aviso: no se pudo leer la clasificacion: {e})")
    _posiciones_cache[partido["uid"]] = resultado
    return resultado


def resultado_de(partido: dict):
    """{'final': bool, 'local': '1', 'visitante': '4'} o None si no hay datos."""
    if partido["uid"] in _resultados_cache:
        return _resultados_cache[partido["uid"]]
    resultado = None
    try:
        encontrado = _evento_espn(partido)
        if encontrado:
            _, ev, _ = encontrado
            compet = (ev.get("competitions") or [{}])[0]
            estado = ((ev.get("status") or {}).get("type") or {}).get("state", "")
            resultado = {"final": estado == "post"}
            for c in compet.get("competitors", []):
                lado = "local" if c.get("homeAway") == "home" else "visitante"
                resultado[lado] = str(c.get("score", ""))
    except Exception as e:  # noqa: BLE001
        print(f"  (aviso: no se pudo leer el resultado: {e})")
    _resultados_cache[partido["uid"]] = resultado
    return resultado


def con_posicion(nombre: str, pos: str) -> str:
    return f"{nombre} ({pos}º)" if pos else nombre


# --------------------------------------------------------------------------
# Escudos (CDN de ESPN) con emparejamento difuso de nombres
# --------------------------------------------------------------------------


def _sin_acentos(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _sin_acentos(s))


def _tokens(s: str):
    quitar = {"fc", "cf", "cd", "sd", "rc", "rcd", "de", "del", "la", "el", "the", "club"}
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", _sin_acentos(s)).split()
            if t and t not in quitar]


def _similitud(a, b) -> float:
    if not a or not b:
        return 0.0
    comunes = len(set(a) & set(b))
    return comunes / len(set(a) | set(b))


class CatalogoEquipos:
    """Nombre de equipo -> url de su escudo."""

    def __init__(self):
        self.mapa = {}      # nombre normalizado -> url
        self.cache = {}     # url -> ruta local
        for endpoint in ("esp.1", "uefa.champions", "esp.copa_del_rey"):
            try:
                datos = json.loads(descargar(
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{endpoint}/teams"
                ).decode())
                for t in datos["sports"][0]["leagues"][0]["teams"]:
                    equipo = t["team"]
                    logos = equipo.get("logos") or []
                    if not logos:
                        continue
                    url = logos[0]["href"]
                    for clave in (equipo.get("displayName"), equipo.get("shortName")):
                        k = _norm(clave)
                        if k:
                            self.mapa.setdefault(k, url)
            except Exception as e:  # noqa: BLE001 - la red no debe tumbar el aviso
                print(f"  (aviso: no se pudo leer {endpoint}: {e})")

    def url_de(self, nombre: str):
        k = _norm(nombre)
        if k in self.mapa:
            return self.mapa[k]
        toks = _tokens(nombre)
        mejor, mejor_sim = None, 0.59
        for clave, url in self.mapa.items():
            s = _similitud(toks, _tokens(clave))
            if s > mejor_sim:
                mejor, mejor_sim = url, s
        return mejor

    def fichero_de(self, nombre: str, por_defecto: str = ""):
        url = self.url_de(nombre) or por_defecto
        if not url:
            return None
        if url in self.cache:
            return self.cache[url]
        os.makedirs(CACHE_DIR, exist_ok=True)
        ruta = os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9.]", "_", url.split("/")[-1]))
        if not os.path.exists(ruta):
            try:
                with open(ruta, "wb") as f:
                    f.write(descargar(url))
            except Exception:  # noqa: BLE001
                return None
        self.cache[url] = ruta
        return ruta


def fichero_competicion(comp: str):
    c = competicion_de(comp)
    if not c or not c[3]:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    ruta = os.path.join(CACHE_DIR, "comp-" + c[3].split("/")[-1])
    if not os.path.exists(ruta):
        try:
            with open(ruta, "wb") as f:
                f.write(descargar(c[3]))
        except Exception:  # noqa: BLE001
            return None
    return ruta


# --------------------------------------------------------------------------
# Tarjeta imagen con los escudos (Pillow)
# --------------------------------------------------------------------------


def _fuente(tam: int):
    from PIL import ImageFont
    candidatas = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # runners Linux
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for ruta in candidatas:
        try:
            return ImageFont.truetype(ruta, tam)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def componer_tarjeta(partido: dict, catalogo: CatalogoEquipos, minutos) -> str | None:
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001 - Pillow no instalado: se avisa solo con texto
        return None

    W, H = 900, 470
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):  # degradado azulgrana
        t = y / H
        px_row = (
            int(2 + (110 - 2) * t),
            int(28 + (20 - 28) * t),
            int(74 + (60 - 74) * t),
        )
        for x in range(W):
            px[x, y] = px_row
    d = ImageDraw.Draw(img)

    def centrar(texto, y, fuente, relleno):
        w = d.textlength(texto, font=fuente)
        d.text(((W - w) / 2, y), texto, font=fuente, fill=relleno)

    # cabecera: logo de la competicion + nombre, en linea y centrados
    comp = competicion_de(partido["competicion"])
    logo_comp = fichero_competicion(partido["competicion"])
    nombre_comp = comp[2] if comp else (partido["competicion"] or "")
    if nombre_comp:
        f_comp = _fuente(30)
        ancho_texto = d.textlength(nombre_comp, font=f_comp)
        ancho_total = ancho_texto + (76 if logo_comp else 0)
        x0 = (W - ancho_total) / 2
        if logo_comp:
            try:
                im = Image.open(logo_comp).convert("RGBA").resize((64, 64), Image.LANCZOS)
                img.paste(im, (int(x0), 24), im)
            except Exception:  # noqa: BLE001
                pass
            x0 += 76
        d.text((x0, 36), nombre_comp, font=f_comp, fill=(255, 255, 255))

    # escudos
    def pegar_escudo(ruta, caja_x, caja_y, tam, nombre):
        if ruta:
            try:
                im = Image.open(ruta).convert("RGBA")
                im.thumbnail((tam, tam))
                img.paste(im, (caja_x + (tam - im.width) // 2,
                               caja_y + (tam - im.height) // 2), im)
                return
            except Exception:  # noqa: BLE001
                pass
        d.ellipse((caja_x + 20, caja_y + 20, caja_x + tam - 20, caja_y + tam - 20),
                  fill=(255, 255, 255, 255))
        centrar("⚽", caja_y + tam // 2 - 30, _fuente(48), (2, 28, 74))

    pegar_escudo(catalogo.fichero_de(partido["local"]), 60, 110, 190, partido["local"])
    pegar_escudo(catalogo.fichero_de(partido["visitante"]), W - 250, 110, 190,
                 partido["visitante"])

    centrar("VS", 185, _fuente(72), (255, 214, 0))

    # nombres de los equipos, cada uno debajo de su escudo
    for nombre, cx in ((partido["local"], 155), (partido["visitante"], W - 155)):
        f = _fuente(24)
        w = d.textlength(nombre, font=f)
        if w > 260:
            f = _fuente(int(24 * 260 / w))
            w = d.textlength(nombre, font=f)
        d.text((cx - w / 2, 318), nombre, font=f, fill=(255, 255, 255))

    # fecha y hora en Lisboa
    if minutos is None:
        texto = f"{texto_lisboa(partido['inicio'])} · ¡ya en juego!"
    else:
        texto = f"{texto_lisboa(partido['inicio'])} · hora de Lisboa"
    centrar(texto, 385, _fuente(30), (255, 214, 0))

    os.makedirs(CACHE_DIR, exist_ok=True)
    salida = os.path.join(CACHE_DIR, f"tarjeta-{abs(hash(partido['uid']))}.png")
    img.save(salida, "PNG")
    return salida


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------


def _telegram_raw(url: str, datos=None):
    req = urllib.request.Request(url, data=datos, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        respuesta = json.loads(r.read().decode())
    if not respuesta.get("ok"):
        raise RuntimeError(f"Telegram devolvio error: {respuesta}")
    return respuesta


def telegram(method: str, **kwargs):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (Secretos).")
    kwargs.setdefault("chat_id", chat)
    datos = urllib.parse.urlencode(kwargs).encode()
    return _telegram_raw(f"https://api.telegram.org/bot{token}/{method}", datos)


def telegram_foto(ruta: str, caption: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (Secretos).")
    frontera = "----avisobarca" + str(abs(hash(ruta)))
    nombre = os.path.basename(ruta)
    with open(ruta, "rb") as f:
        contenido = f.read()
    cuerpo = b"".join([
        f"--{frontera}\r\n".encode(),
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n',
        chat.encode(), b"\r\n",
        f"--{frontera}\r\n".encode(),
        b'Content-Disposition: form-data; name="caption"\r\n'
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n",
        caption.encode("utf-8"), b"\r\n",
        f"--{frontera}\r\n".encode(),
        f'Content-Disposition: form-data; name="photo"; filename="{nombre}"\r\n'.encode(),
        b"Content-Type: image/png\r\n\r\n",
        contenido, b"\r\n",
        f"--{frontera}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=cuerpo,
        headers={"Content-Type": f"multipart/form-data; boundary={frontera}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        respuesta = json.loads(r.read().decode())
    if not respuesta.get("ok"):
        raise RuntimeError(f"Telegram devolvio error: {respuesta}")


def texto_etapa(partido: dict, eid: str, minutos, pos: dict) -> str:
    hora = texto_lisboa(partido["inicio"], "%H:%M")  # solo hora: la fecha va en la tarjeta
    local, visitante = partido["local"], partido["visitante"]
    equipos = (f"{con_posicion(local, pos.get('local'))}"
               f" vs {con_posicion(visitante, pos.get('visitante'))}")
    if eid == "t90":
        return (f"⚽ El Barça juega en {minutos} minutos\n{equipos}\n"
                f"Empieza a las {hora} hora de Lisboa.")
    if eid == "t60":
        return f"⏰ Una hora: {local} vs {visitante} empieza a las {hora} (hora de Lisboa)."
    if eid == "t15":
        return f"⏳ Falta un cuarto de hora: {equipos}, {hora} hora de Lisboa."
    comp = competicion_de(partido["competicion"])
    extra = f" ({comp[2]})" if comp else ""
    if eid == "tfin":
        res = resultado_de(partido) or {}
        marcador = f"{res.get('local', '?')} - {res.get('visitante', '?')}"
        return f"🏁 Final: {local} {marcador} {visitante}{extra}"
    return f"🔴 ¡Ha empezado! {local} vs {visitante}{extra}, {hora} hora de Lisboa."


def enviar_aviso(partido: dict, eid: str, minutos, catalogo, enlace=None):
    pos = {} if eid == "tfin" else posiciones_de(partido)
    texto = texto_etapa(partido, eid, minutos, pos)
    if enlace:
        texto += "\n" + texto_enlace(enlace)
    con_tarjeta = next(t for i, _, t in ETAPAS if i == eid)
    if con_tarjeta:
        tarjeta = componer_tarjeta(partido, catalogo, minutos)
        if tarjeta:
            telegram_foto(tarjeta, texto)
        else:
            try:
                telegram("sendPhoto", photo=ESCUDO_BARCa, caption=texto)
            except Exception:  # noqa: BLE001
                telegram("sendMessage", text=texto)
    else:
        telegram("sendMessage", text=texto)
    print(f"  -> Telegram enviado ({eid}): {texto.splitlines()[0]}")


# --------------------------------------------------------------------------
# Resumen del partido en YouTube (innertube, sin API key)
# --------------------------------------------------------------------------

_YT_VERSION = "2.20261013.00.00"


def _yt_post(url: str, datos: dict):
    req = urllib.request.Request(
        url, data=json.dumps(datos).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def _yt_buscar(consulta: str, cantidad: int = 25):
    """[{id, titulo, canal, retardo}] de una busqueda de YouTube."""
    url = "https://www.youtube.com/youtubei/v1/search"
    ctx = {"client": {"clientName": "WEB", "clientVersion": _YT_VERSION}}
    r = _yt_post(url, {"query": consulta, "context": ctx})
    res = []

    def caminar(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v:
                titulo = "".join(x.get("text", "") for x in
                                 v.get("title", {}).get("runs", []))
                canal = ""
                owner = v.get("ownerText", {}).get("runs", [])
                if owner:
                    canal = owner[0].get("text", "")
                retardo = v.get("publishedTimeText", {}).get("simpleText", "")
                res.append({"id": v.get("videoId"), "titulo": titulo,
                            "canal": canal, "retardo": retardo})
            for x in o.values():
                caminar(x)
        elif isinstance(o, list):
            for x in o:
                caminar(x)

    caminar(r)
    return res[:cantidad]


def _yt_duracion(video_id: str):
    """Duracion en segundos, o None si no se pudo leer."""
    try:
        url = "https://www.youtube.com/youtubei/v1/player"
        ctx = {"client": {"clientName": "WEB", "clientVersion": _YT_VERSION}}
        d = _yt_post(url, {"videoId": video_id, "context": ctx})
        largo = d.get("microformat", {}).get("playerMicroformatRenderer", {}).get(
            "lengthSeconds")
        return int(largo) if largo else None
    except Exception:  # noqa: BLE001
        return None


def _retardo_horas(retardo: str):
    """'4 hours ago' -> 4.0 ; '1 day ago' -> 24.0 ; '' -> None"""
    m = re.match(r"(\d+)\s*(minute|hour|day|week)", retardo or "")
    if not m:
        return None
    n = int(m.group(1))
    return {"minute": n / 60, "hour": float(n), "day": n * 24.0,
            "week": n * 168.0}[m.group(2)]


def resumen_youtube(partido: dict):
    """{id, titulo, canal, geo} del resumen en espanol, o None si no hay.

    Orden de preferencia: CANALES_RESUMEN. Los candidatos de otros canales
    deben parecerse a un resumen real: los dos equipos en el titulo, palabra
    de resumen (ES/PT/EN), publicado en las ultimas 24 h y 3+ minutos.
    """
    consulta = f"{partido['local']} {partido['visitante']} resumen goles"
    try:
        candidatos = _yt_buscar(consulta)
    except Exception as e:  # noqa: BLE001
        print(f"  (aviso: busqueda de resumen fallo: {e})")
        return None
    toks_local = set(_tokens(partido["local"]))
    toks_visita = set(_tokens(partido["visitante"]))
    elegibles = []
    for c in candidatos:
        toks_titulo = set(_tokens(c["titulo"]))
        if not (toks_local <= toks_titulo and toks_visita <= toks_titulo):
            continue
        titulo_bajo = c["titulo"].lower()
        if not any(p in titulo_bajo for p in PALABRAS_RESUMEN):
            continue
        horas = _retardo_horas(c["retardo"])
        if horas is None or horas > 24:
            continue
        canal = c["canal"].lower()
        c["prioridad"] = (CANALES_RESUMEN.index(canal)
                          if canal in CANALES_RESUMEN else len(CANALES_RESUMEN))
        elegibles.append(c)
    elegibles.sort(key=lambda c: c["prioridad"])
    for c in elegibles[:3]:
        if (_yt_duracion(c["id"]) or 0) >= 180:
            c["geo"] = any(g in c["canal"].lower() for g in GEO_SOLO_PORTUGAL)
            return c
    return None


def texto_enlace(enlace: dict) -> str:
    geo = " · solo con IP de Portugal" if enlace.get("geo") else ""
    return f"🎬 Resumen ({enlace['canal']}{geo}):\nhttps://youtu.be/{enlace['id']}"


# --------------------------------------------------------------------------
# Vigía: el momento exacto lo marca esta ejecución, que duerme hasta el
# siguiente aviso y se encadena a sí misma con workflow_dispatch
# --------------------------------------------------------------------------
#
# Los cron programados no sirven para una hora concreta: GitHub los entrega
# con retraso (5 h medidas en este repo del 14 al 16 de octubre de 2026).
# El workflow solo tiene una reserva fija cada 6 h; ese job duerme hasta la
# etapa siguiente y avisa en el minuto exacto. Si la etapa está fuera de su
# presupuesto, lanza otro job igual con workflow_dispatch (esos arrancan en
# segundos, no pasan por el programador) y termina.

VIGIA_PRESUPUESTO = 5.5 * 3600   # Actions corta cada job a las 6 h
VIGIA_MARGEN_SEG = 5             # llegar al umbral unos segundos después


def leer_estado() -> dict:
    if os.path.exists(ESTADO_PATH):
        try:
            with open(ESTADO_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return {"avisados": {}}


def guardar_estado(estado: dict) -> None:
    # "latido": al cambiar la fecha se genera un commit diario, lo que impide
    # que GitHub desactive los workflows programados por inactividad del repo
    estado["ultima_comprobacion"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def proximo_momento(partidos, ahora):
    """{momento, eid, uid} de la próxima etapa que toca mirar, o None."""
    mejor = None
    for p in partidos:
        if p["inicio"] < ahora - timedelta(minutes=MARGEN_FIN):
            continue
        for despl, eid in desplazos():
            momento = p["inicio"] + timedelta(minutes=despl)
            # lo ya pasado (o a punto de pasar) lo retoma el bucle de avisos
            if momento <= ahora + timedelta(seconds=VIGIA_MARGEN_SEG + 30):
                continue
            if mejor is None or momento < mejor["momento"]:
                mejor = {"momento": momento, "eid": eid, "uid": p["uid"]}
    return mejor


def despachar_vigia(motivo: dict) -> bool:
    """Lanza otra ejecución del workflow para que siga la vigilancia."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False
    url = f"https://api.github.com/repos/{repo}/actions/workflows/avisos.yml/dispatches"
    cuerpo = {"ref": "main", "inputs": {
        "motivo": f"{motivo['eid']} {motivo['momento']:%Y-%m-%d %H:%M} UTC"}}
    req = urllib.request.Request(
        url, data=json.dumps(cuerpo).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "barca-avisos"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 204)
    except Exception as e:  # noqa: BLE001
        print(f"  (no se pudo encadenar la vigilancia: {e})")
        return False


def avisar_pendientes(partidos, ahora, estado, catalogo) -> int:
    """Envía las etapas pendientes de cada partido. Devuelve las enviadas."""
    avisados = estado.setdefault("avisados", {})
    enviados = 0
    for p in partidos:
        uid, inicio = p["uid"], p["inicio"]
        registro = avisados.get(uid) or {}
        etapas = registro.get("etapas")
        if etapas is None and registro:
            # estado en formato antiguo (un solo aviso por partido): ya gestionado
            etapas = [e[0] for e in ETAPAS]
        etapas = list(etapas or [])
        guardado = list(etapas)
        restan = (inicio - ahora).total_seconds() / 60

        # etapas vencidas se marcan para no arrastrarlas (la de final,
        # hasta que el partido tenga margen de haber terminado)
        if restan <= -MARGEN_INICIO:
            etapas += [e[0] for e in ETAPAS if e[1] >= 0 and e[0] not in etapas]
        if restan <= -MARGEN_FIN:
            etapas += [e[0] for e in ETAPAS if e[1] < 0 and e[0] not in etapas]

        pendiente = None
        for eid, antes, _ in ETAPAS:
            if restan <= antes and eid not in etapas:
                pendiente = eid  # se queda la etapa mas cercana al inicio
        if pendiente == "tfin":
            res = resultado_de(p)
            if res and res.get("final"):
                enlace = resumen_youtube(p)
                enviar_aviso(p, "tfin", None, catalogo, enlace=enlace)
                etapas.append("tfin")
                if enlace:
                    registro["enlace"] = True
            # sin datos de final aun: se vuelve a mirar en la proxima ejecucion
        elif pendiente is not None:
            enviar_aviso(p, pendiente, int(restan) if restan > 0 else None, catalogo)
            etapas.append(pendiente)

        # el resultado ya salio pero sin enlace: se completa cuando aparezca
        # (\"guardado\" evita repetir la busqueda en la misma ejecucion del tfin)
        if ("tfin" in guardado and not registro.get("enlace")
                and -MARGEN_FIN < restan <= -125):
            enlace = resumen_youtube(p)
            if enlace:
                texto = (f"🎬 Ya está el resumen del {p['local']} {p['visitante']}:\n"
                         + texto_enlace(enlace))
                telegram("sendMessage", text=texto)
                print(f"  -> Telegram enviado (enlace): https://youtu.be/{enlace['id']}")
                registro["enlace"] = True
                enviados += 1

        if etapas != guardado or registro.get("enlace"):
            registro.update({"etapas": etapas, "inicio": inicio.isoformat()})
            avisados[uid] = registro
            enviados += 1 if etapas != guardado else 0

    return enviados


def vigia(partidos, estado, catalogo) -> int:
    """Duerme hasta cada etapa y avisa en el minuto exacto.

    En local (sin GITHUB_TOKEN) no duerme ni se encadena: solo avisa lo que
    haya pendiente en el momento de ejecutarse.
    """
    if not (os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY")):
        print("Vigia: sin GITHUB_TOKEN (ejecucion local), no se duerme.")
        return 0
    fin = time.time() + VIGIA_PRESUPUESTO
    while True:
        momento = proximo_momento(partidos, datetime.now(timezone.utc))
        if momento is None:
            print("Vigia: no quedan etapas por vigilar en el calendario.")
            return 0
        restan = (momento["momento"] - datetime.now(timezone.utc)).total_seconds()
        puede_dormir = fin - time.time() - VIGIA_MARGEN_SEG
        if restan > puede_dormir:
            if restan > VIGIA_PRESUPUESTO:
                # aun lejos: la reserva de cada 6 h la coge cuando toque
                print(f"Vigia: {momento['eid']} es dentro de "
                      f"{restan / 3600:.1f} h, sale la reserva de cada 6 h.")
                return 0
            if puede_dormir <= 60:
                # se acaba el presupuesto con la etapa encima: otro job la sigue
                if despachar_vigia(momento):
                    print(f"Vigia: sin presupuesto, se encadena otra ejecucion "
                          f"para {momento['eid']} ({momento['momento']:%H:%M} UTC).")
                else:
                    print("::warning::Vigia: no se pudo encadenar la ejecucion; "
                          "la reserva de cada 6 h recogerá esta etapa.")
                return 0
        if restan <= puede_dormir:
            print(f"Vigia: duermo {restan / 60:.0f} min hasta {momento['eid']} "
                  f"({momento['momento']:%Y-%m-%d %H:%M} UTC)")
            time.sleep(restan + VIGIA_MARGEN_SEG)
        else:
            # duermo lo que me queda para acercarme a la etapa y que otro
            # job (la reserva o el que encadene) la pille a tiempo
            print(f"Vigia: duermo {puede_dormir / 60:.0f} min; {momento['eid']} "
                  f"queda a {(restan / 60):.0f} min.")
            time.sleep(puede_dormir)
        enviados = avisar_pendientes(partidos, datetime.now(timezone.utc),
                                     estado, catalogo)
        guardar_estado(estado)
        if enviados:
            print(f"Vigia: avisos enviados en esta etapa: {enviados}")


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------


def main() -> int:
    ahora = datetime.now(timezone.utc)
    motivo = os.environ.get("MOTIVO") or "local"
    print(f"[{ahora:%Y-%m-%d %H:%M} UTC] Actualizando calendario del Barça ({motivo})...")
    partidos = leer_partidos(descargar(ICAL_URL).decode("utf-8", "replace"))
    partidos = [p for p in partidos if p["inicio"] > ahora - timedelta(minutes=MARGEN_FIN)]
    partidos.sort(key=lambda p: p["inicio"])
    print(f"Partidos proximos en el calendario oficial: {len(partidos)}")

    catalogo = CatalogoEquipos()
    estado = leer_estado()

    if "--test" in sys.argv or os.environ.get("PRUEBA", "").lower() == "true":
        sig = next((p for p in partidos if p["inicio"] > ahora), None)
        if not sig:
            print("No hay proximos partidos.")
            return 0
        restan = int((sig["inicio"] - ahora).total_seconds() // 60)
        pos = posiciones_de(sig)
        print("Escalera de avisos para el proximo partido:")
        for eid, _, _ in ETAPAS:
            linea = texto_etapa(sig, eid, restan, pos).replace("\n", " / ")
            print(f"  [{eid}] {linea}")
        etapa = "t90"
        if "--test" in sys.argv:
            i = sys.argv.index("--test") + 1
            if i < len(sys.argv) and sys.argv[i] in [e[0] for e in ETAPAS]:
                etapa = sys.argv[i]
        enlace = resumen_youtube(sig) if etapa == "tfin" else None
        if etapa == "tfin" and not (resultado_de(sig) or {}).get("final"):
            print("  (el partido aun no ha terminado: se muestra el texto, no se envia)")
            print(f"  [tfin] {texto_etapa(sig, 'tfin', restan, {}).replace(chr(10), ' / ')}")
            if enlace:
                print("  [tfin] " + texto_enlace(enlace).replace(chr(10), " / "))
            return 0
        enviar_aviso(sig, etapa, restan, catalogo, enlace=enlace)
        print("  (modo prueba: sin marcar el estado, sin vigilar)")
        return 0

    enviados = avisar_pendientes(partidos, ahora, estado, catalogo)
    guardar_estado(estado)
    print(f"Avisos enviados en esta ejecucion: {enviados}")
    return vigia(partidos, estado, catalogo)


if __name__ == "__main__":
    sys.exit(main())
