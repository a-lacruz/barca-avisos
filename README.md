# Bot de Notificaciones Juegos del FC Barcelona

Te avisa **1 hora antes de cada partido del primer equipo del FC Barcelona** (LaLiga, Champions, Copa del Rey...), en **hora de Lisboa**, mandándote un mensaje a **Telegram**. Funciona 24/7 con **GitHub Actions**: no necesitas que tu PC esté encendido ni ningún servidor.

Fuente de datos: el **calendario oficial del FC Barcelona** (fcbarcelona.es, botón "Sincroniza los partidos en tu calendario", servicio eCal).

## Cómo funciona

- El workflow se ejecuta **cada 30 minutos** (y a demanda con el botón "Run workflow").
- Si faltan 60 minutos o menos para un partido y todavía no se avisó, manda el mensaje a Telegram.
- Si el PC/servidor no estaba disponible a la hora, avisa igualmente cuando el partido ya ha empezado.
- Qué avisos ya se mandaron queda guardado en `estado.json`, que el propio workflow actualiza en el repositorio.

## Configuración

Secretos del repositorio (Settings → Secrets and variables → Actions):

| Secreto | Contenido |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot de @BotFather |
| `TELEGRAM_CHAT_ID` | Chat id del usuario |

Variables opcionales en `.github/workflows/avisos.yml` (paso "Comprobar partidos y avisar"):

- `MINUTOS_AVISO`: margen de aviso (por defecto 60).
- `ICAL_URL`: enlace .ics alternativo.

## Probar

- En GitHub: pestaña **Actions** → "Avisos del FC Barcelona" → **Run workflow**.
- En local: `TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python avisar.py --test`

## Notas

- Los workflows con `schedule` solo se ejecutan en la rama por defecto (`main`).
- GitHub puede retrasar unos minutos la ejecución programada.
- Si un repositorio está 60 días sin actividad, GitHub desactiva sus workflows programados; aquí los commits diarios de `estado.json` mantienen la actividad.
- El token y el chat id nunca se guardan en el código: van en los Secretos.
