"""
Monitor de Eventos Musicales - Argentina
Corre en GitHub Actions cada 2 horas. Detecta eventos nuevos y notifica por Telegram.

Plataformas monitoreadas:
  - DF Entertainment (dfentertainment.com)
  - AllAccess (allaccess.com.ar)
  - Ticketek (ticketek.com.ar)
  - Passline (passline.com)
  - Livepass (livepass.com.ar)
"""

import os
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración — credenciales siempre desde variables de entorno (nunca hardcodeadas)
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

SEEN_EVENTS_FILE = Path("eventos_historial.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}

REQUEST_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Logging — sin datos sensibles
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Historial de eventos
# ---------------------------------------------------------------------------

def cargar_historial() -> dict:
    if SEEN_EVENTS_FILE.exists():
        try:
            with open(SEEN_EVENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("Historial corrupto o inexistente. Se reinicia.")
    return {"primera_ejecucion": True, "ultima_ejecucion": None, "eventos_vistos": []}


def guardar_historial(historial: dict) -> None:
    try:
        with open(SEEN_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        log.info("Historial guardado: %d eventos", len(historial["eventos_vistos"]))
    except OSError as e:
        log.error("Error al guardar historial: %s", type(e).__name__)

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def enviar_telegram(mensaje: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Faltan credenciales de Telegram en las variables de entorno.")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": int(TELEGRAM_CHAT_ID), "text": mensaje, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            log.info("Notificación enviada por Telegram.")
            return True
        log.error("Telegram rechazó el mensaje: %s", data.get("description", ""))
    except requests.exceptions.RequestException as e:
        log.error("Error de red al enviar Telegram: %s", type(e).__name__)
    return False

# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def _get_soup(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.RequestException as e:
        log.warning("No se pudo acceder a %s (%s)", url, type(e).__name__)
        return None


def _extraer_eventos(soup: BeautifulSoup, plataforma: str, selectores: list[str]) -> list[str]:
    """Extrae texto de elementos que coincidan con los selectores dados."""
    eventos = set()
    for selector in selectores:
        for el in soup.select(selector):
            texto = el.get_text(separator=" ", strip=True)[:100]
            if texto and len(texto) > 4:
                eventos.add(f"{texto}|{plataforma}")
    return list(eventos)


def scrape_df_entertainment() -> list[str]:
    soup = _get_soup("https://dfentertainment.com/shows")
    if not soup:
        return []
    # DF es React — busca nombres en encabezados y links
    selectores = ["h1", "h2", "h3", "h4", "a[href*='show']", ".show-title", ".event-name"]
    eventos = _extraer_eventos(soup, "DF Entertainment", selectores)
    log.info("DF Entertainment: %d encontrados", len(eventos))
    return eventos


def scrape_allaccess() -> list[str]:
    soup = _get_soup("https://www.allaccess.com.ar/")
    if not soup:
        return []
    selectores = ["h1", "h2", "h3", ".event-title", ".show-name", "a[href*='/event/']"]
    eventos = _extraer_eventos(soup, "AllAccess", selectores)
    log.info("AllAccess: %d encontrados", len(eventos))
    return eventos


def scrape_ticketek() -> list[str]:
    soup = _get_soup("https://www.ticketek.com.ar/")
    if not soup:
        return []
    selectores = ["h1", "h2", "h3", ".show-title", ".event-name", "a[href*='/shows/']"]
    eventos = _extraer_eventos(soup, "Ticketek", selectores)
    log.info("Ticketek: %d encontrados", len(eventos))
    return eventos


def scrape_passline() -> list[str]:
    soup = _get_soup("https://www.passline.com/home")
    if not soup:
        return []
    selectores = ["h1", "h2", "h3", ".event-title", ".event-name", "a[href*='/eventos/']"]
    eventos = _extraer_eventos(soup, "Passline", selectores)
    log.info("Passline: %d encontrados", len(eventos))
    return eventos


def scrape_livepass() -> list[str]:
    soup = _get_soup("https://livepass.com.ar/")
    if not soup:
        return []
    selectores = ["h1", "h2", "h3", ".event-title", ".event-name", "a[href*='/evento/']"]
    eventos = _extraer_eventos(soup, "Livepass", selectores)
    log.info("Livepass: %d encontrados", len(eventos))
    return eventos

# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

SCRAPERS = [
    scrape_df_entertainment,
    scrape_allaccess,
    scrape_ticketek,
    scrape_passline,
    scrape_livepass,
]


def main():
    log.info("=== Monitor de Eventos iniciado ===")

    historial = cargar_historial()
    primera_vez = historial.get("primera_ejecucion", False)
    vistos_set = set(historial.get("eventos_vistos", []))

    # Correr todos los scrapers
    todos_actuales = []
    for scraper in SCRAPERS:
        try:
            todos_actuales.extend(scraper())
        except Exception as e:
            log.error("Error en %s: %s", scraper.__name__, type(e).__name__)

    actuales_set = set(todos_actuales)

    if primera_vez:
        # Primera ejecución: guardar baseline sin notificar
        log.info("Primera ejecución. Guardando baseline con %d eventos.", len(actuales_set))
        guardar_historial({
            "primera_ejecucion": False,
            "ultima_ejecucion": datetime.now().isoformat(timespec="seconds"),
            "eventos_vistos": sorted(actuales_set),
        })
        return

    # Detectar novedades
    nuevos = actuales_set - vistos_set

    if nuevos:
        log.info("%d eventos nuevos detectados.", len(nuevos))

        # Armar mensaje
        lineas = ["🎵 <b>¡Eventos nuevos en ticketeras argentinas!</b>\n"]
        for ev in sorted(nuevos)[:15]:
            nombre, plataforma = ev.rsplit("|", 1)
            lineas.append(f"📍 <b>{plataforma}</b>: {nombre}")
        if len(nuevos) > 15:
            lineas.append(f"\n...y {len(nuevos) - 15} más.")
        lineas.append("\n🔍 Revisá los sitios para más info.")

        enviar_telegram("\n".join(lineas))
    else:
        log.info("Sin novedades en esta ejecución.")

    # Actualizar historial con todos los eventos actuales
    guardar_historial({
        "primera_ejecucion": False,
        "ultima_ejecucion": datetime.now().isoformat(timespec="seconds"),
        "eventos_vistos": sorted(actuales_set),
    })


if __name__ == "__main__":
    main()
