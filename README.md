# Bot de Notificaciones Juegos del FC Barcelona

Te avisa de **todos los partidos del primer equipo del FC Barcelona** (LaLiga, Champions, Copa del Rey, amistosos...), en **hora de Lisboa**, mandándote los mensajes a **Telegram**: un aviso grande con tarjeta 90 minutos antes, tres recordatorios cortos y el resultado con el enlace al resumen cuando acaba. Funciona 24/7 con **GitHub Actions**: no necesitas que tu PC esté encendido ni ningún servidor.

Fuente de datos: el **calendario oficial del FC Barcelona** (fcbarcelona.es, botón "Sincroniza los partidos en tu calendario", servicio eCal).

## Avisos (escalera)

Cada partido genera cinco avisos, el día del partido:

| Cuándo | Mensaje |
|---|---|
| **T-90** | 📷 Tarjeta con los escudos de los dos equipos, el logo de la competición y la hora de Lisboa + texto: `⚽ El Barça juega en 90 minutos / Real Betis (3º) vs FC Barcelona (1º) / Empieza a las 17:30 hora de Lisboa.` |
| **T-60** | 💬 `⏰ Una hora: Real Betis vs FC Barcelona empieza a las 17:30 (hora de Lisboa).` |
| **T-15** | 💬 `⏳ Falta un cuarto de hora: Real Betis (3º) vs FC Barcelona (1º), 17:30 hora de Lisboa.` |
| **T-0** | 💬 `🔴 ¡Ha empezado! Real Betis vs FC Barcelona (LaLiga), 17:30 hora de Lisboa.` |
| **T+2h** | 💬 `🏁 Final: Real Betis 1 - 3 FC Barcelona (LaLiga)` + `🎬 Resumen: https://youtu.be/...` — el marcador sale de ESPN solo cuando el partido ha terminado de verdad; si el resumen todavía no está publicado, el enlace llega en un mensaje aparte (se sigue mirando hasta 6 h después del inicio). |

- Las posiciones salen de la clasificación real (ESPN): LaLiga y fase de grupos de la Champions; en eliminatorias (Copa, Supercopa) no hay tabla y se omiten.
- El enlace al resumen se busca en YouTube entre los canales del Barça, LaLiga y DAZN (como red de seguridad, cualquier vídeo reciente que parezca el resumen del partido).
- Cada aviso es independiente: si uno no se puede enviar (caída puntual de GitHub), el siguiente sale igual.
- El aviso de inicio solo se envía en los 25 minutos posteriores al comienzo del partido.

## Cómo funciona

- `avisar.py` lee el calendario oficial (.ics) y **genera su propio cron**: solo se programa en los días de partido (T-90, T-60, T-15, T-0 y el resultado con reintentos del enlace) más una comprobación diaria (08:07 UTC) que confirma horarios y amplía el horizonte 10 días.
- Qué avisos ya se mandaron queda guardado en `estado.json`, que el propio workflow actualiza en el repositorio.
- Los horarios se recalculan solos: si el club cambia el horario de un partido, los avisos se ajustan.

## Configuración

Secretos del repositorio (Settings → Secrets and variables → Actions):

| Secreto | Contenido |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot de @BotFather |
| `TELEGRAM_CHAT_ID` | Chat id del usuario |

Variable opcional:

- `ICAL_URL`: enlace .ics alternativo al calendario oficial.

## Probar

- En GitHub: pestaña **Actions** → "Avisos del FC Barcelona" → **Run workflow** (marca *prueba*): envía el aviso T-90 con tarjeta sin marcar el estado.
- En local: `TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python avisar.py --test` — y `--test t60`, `--test t15`, `--test t0` o `--test tfin` para ver y enviar cada etapa.

## Cambiar los tiempos

La escalera de avisos vive en la tabla `ETAPAS` de `avisar.py`:

```python
ETAPAS = [
    ("t90", 90, True),   # id, minutos antes del inicio, con tarjeta imagen
    ("t60", 60, False),
    ("t15", 15, False),
    ("t0", 0, False),
    ("tfin", -125, False),  # negativo = después del inicio (resultado)
]
```

Al cambiarla, el cron y los mensajes se adaptan solos en la siguiente ejecución.

## Notas

- Los workflows con `schedule` solo se ejecutan en la rama por defecto (`main`).
- GitHub puede retrasar unos minutos la ejecución programada.
- Si un repositorio está 60 días sin actividad, GitHub desactiva sus workflows programados; aquí los commits diarios de `estado.json` mantienen la actividad.
- El token y el chat id nunca se guardan en el código: van en los Secretos.
- Para dibujar las tarjetas se usa Pillow (lo instala el propio workflow).
