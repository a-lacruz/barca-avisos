#!/usr/bin/env python3
"""
Avisos del FC Barcelona por Telegram (GitHub Actions, gratis y 24/7).

- Lee el calendario oficial del Barça (.ics del boton "Sincroniza" de fcbarcelona.es).
- Avisa por Telegram en escalera, siempre en hora de Lisboa:
    T-90  tarjeta con los escudos de los dos equipos y el logo de la competicion.
    T-60  recordatorio en texto.
    T-15  recordatorio en texto.
    T-0   aviso cuando el partido comienza.
- AUTO-GENERA SU PROPIO CRON: solo se programa en los dias de partido
  (T-90, T-60, T-15 y T-0) mas una comprobacion diaria.

Secretos: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Opcional: ICAL_URL.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ICAL_URL = os.environ.get(
    "ICAL_URL",
    "https://ics.ecal.com/ecal-sub/6aceb1a56051400002e3e8d5/FCBarcelona.ics",
)
MARGEN_TARDIO = 90   # cuantos minutos atras se mira el calendario
MARGEN_INICIO = 25   # tras el inicio, aun tiene sentido avisar "¡ha empezado!"
ESTADO_PATH = os.environ.get("ESTADO_PATH", "estado.json")
WORKFLOW_PATH = os.environ.get("WORKFLOW_PATH", ".github/workflows/avisos.yml")
CACHE_DIR = os.environ.get("CACHE_DIR", ".cache")
HORIZON_DIAS = 10  # cuantos dias hacia delante se programan ejecuciones

# escalera de avisos: (id, minutos antes del inicio, con tarjeta imagen)
# el cron se genera a partir de aqui; cambiar tiempos = editar solo esta tabla
ETAPAS = [
    ("t90", 90, True),
    ("t60", 60, False),
    ("t15", 15, False),
    ("t0", 0, False),
]

# desplazamientos (minutos respecto al inicio del partido) en los que se ejecuta
DESLAZES = [-antes for _, antes, _ in ETAPAS]

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
# Posiciones en la clasificacion (ESPN: resumen del partido -> tabla)
# --------------------------------------------------------------------------

_posiciones_cache = {}


def posiciones_de(partido: dict) -> dict:
    """{'local': '1', 'visitante': '8'} o {} si la competicion es eliminatoria."""
    if partido["uid"] in _posiciones_cache:
        return _posiciones_cache[partido["uid"]]
    resultado = {}
    comp = competicion_de(partido["competicion"])
    if comp and comp[4]:
        try:
            endpoint, fecha = comp[4], partido["inicio"].strftime("%Y%m%d")
            sb = json.loads(descargar(
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/{endpoint}"
                f"/scoreboard?dates={fecha}").decode())
            evento, nombres = None, {}
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
                    evento, nombres = ev, candidato
                    break
            if evento:
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
    return f"🔴 ¡Ha empezado! {local} vs {visitante}{extra}, {hora} hora de Lisboa."


def enviar_aviso(partido: dict, eid: str, minutos, catalogo):
    pos = posiciones_de(partido)
    texto = texto_etapa(partido, eid, minutos, pos)
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
# Cron auto-generado (solo se ejecuta cuando hay partido)
# --------------------------------------------------------------------------


def generar_cron(partidos, ahora):
    entradas = {"7 8 * * *"}  # comprobacion diaria (confirma horarios)
    for p in partidos:
        if p["inicio"] < ahora or p["inicio"] > ahora + timedelta(days=HORIZON_DIAS):
            continue
        for despl in DESLAZES:
            momento = p["inicio"] + timedelta(minutes=despl)
            if momento <= ahora:
                continue
            entradas.add(f"{momento.minute} {momento.hour} {momento.day} {momento.month} *")
    def _clave(c):
        minuto, hora, dia, mes, _ = c.split()
        def num(x):
            return int(x) if x.isdigit() else 0
        return (num(mes), num(dia), num(hora), num(minuto))
    return sorted(entradas, key=_clave)


def actualizar_workflow(cron) -> bool:
    if not os.path.exists(WORKFLOW_PATH):
        return False
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        yml = f.read()
    nuevo_bloque = "\n".join(f'    - cron: "{c}"' for c in cron)
    patron = re.compile(r"(# CRON-INICIO.*?\n)(.*?)(\n\s*# CRON-FIN)", re.S)
    if not patron.search(yml):
        print("  (no se encontraron los marcadores CRON-INICIO/CRON-FIN)")
        return False
    yml_nuevo = patron.sub(lambda m: m.group(1) + nuevo_bloque + m.group(3), yml)
    if yml_nuevo == yml:
        return False
    with open(WORKFLOW_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(yml_nuevo)
    return True


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------


def main() -> int:
    ahora = datetime.now(timezone.utc)
    print(f"[{ahora:%Y-%m-%d %H:%M} UTC] Actualizando calendario del Barça...")
    partidos = leer_partidos(descargar(ICAL_URL).decode("utf-8", "replace"))
    partidos = [p for p in partidos if p["inicio"] > ahora - timedelta(minutes=MARGEN_TARDIO)]
    partidos.sort(key=lambda p: p["inicio"])
    print(f"Partidos proximos en el calendario oficial: {len(partidos)}")

    catalogo = CatalogoEquipos()
    estado = {"avisados": {}}
    if os.path.exists(ESTADO_PATH):
        try:
            with open(ESTADO_PATH, encoding="utf-8") as f:
                estado = json.load(f)
        except Exception:  # noqa: BLE001
            pass
    avisados = estado.setdefault("avisados", {})
    enviados = 0

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
        enviar_aviso(sig, etapa, restan, catalogo)
        print("  (modo prueba: sin marcar el estado)")
        return 0

    for p in partidos:
        uid, inicio = p["uid"], p["inicio"]
        registro = avisados.get(uid) or {}
        etapas = registro.get("etapas")
        if etapas is None and registro:
            # estado en formato antiguo (un solo aviso por partido): ya gestionado
            etapas = [e[0] for e in ETAPAS]
        etapas = list(etapas or [])
        restan = (inicio - ahora).total_seconds() / 60
        if restan <= -MARGEN_INICIO:
            # demasiado tarde: no se manda "¡ha empezado!" 40 minutos despues
            avisados[uid] = {"etapas": [e[0] for e in ETAPAS],
                             "inicio": inicio.isoformat()}
            continue
        pendiente = None
        for eid, antes, _ in ETAPAS:
            if restan <= antes and eid not in etapas:
                pendiente = eid  # se queda la etapa mas cercana al inicio
        if pendiente is None:
            continue
        enviar_aviso(p, pendiente, int(restan) if restan > 0 else None, catalogo)
        etapas.append(pendiente)
        avisados[uid] = {"etapas": etapas, "inicio": inicio.isoformat()}
        enviados += 1

    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

    # "latido": al cambiar la fecha se genera un commit diario, lo que impide
    # que GitHub desactive los workflows programados por inactividad del repo
    latido = ahora.strftime("%Y-%m-%d")
    if estado.get("ultima_comprobacion") != latido:
        estado["ultima_comprobacion"] = latido
        with open(ESTADO_PATH, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)

    cron = generar_cron(partidos, ahora)
    cambiado = actualizar_workflow(cron)
    print(f"Ejecuciones programadas: {len(cron)}" + (" (cron actualizado)" if cambiado else ""))
    print(f"Avisos enviados en esta ejecucion: {enviados}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
