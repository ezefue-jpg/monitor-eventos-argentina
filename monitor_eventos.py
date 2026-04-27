"""
Monitor de Eventos Musicales - Argentina
Corre en GitHub Actions cada 2 horas. Detecta eventos nuevos, filtra los pasados,
genera index.html con fotos de artistas (iTunes), fotos de venues (Wikipedia),
medidor de disponibilidad de 5 puntos, precio mínimo y filtros por ciudad.
 
Plataformas: DF Entertainment · AllAccess · Ticketek · Passline · Livepass
"""
 
import os, json, re, hashlib, logging
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse
 
import requests
from bs4 import BeautifulSoup
 
# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_PAGES_URL   = os.environ.get("PAGES_URL", "")
 
SEEN_EVENTS_FILE = Path("eventos_historial.json")
HTML_FILE        = Path("index.html")
 
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}
REQUEST_TIMEOUT = 20
 
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
 
# ---------------------------------------------------------------------------
# Mapeo de venues conocidos
# ---------------------------------------------------------------------------
VENUES_AR: dict[str, tuple[str, str, str]] = {
    "movistar arena":          ("Movistar Arena",              "Buenos Aires", "CABA"),
    "estadio monumental":      ("Estadio Monumental",          "Buenos Aires", "CABA"),
    "river plate":             ("Estadio Monumental",          "Buenos Aires", "CABA"),
    "estadio river":           ("Estadio Monumental",          "Buenos Aires", "CABA"),
    "monumental":              ("Estadio Monumental",          "Buenos Aires", "CABA"),
    "vélez":                   ("Estadio Vélez",               "Buenos Aires", "CABA"),
    "velez":                   ("Estadio Vélez",               "Buenos Aires", "CABA"),
    "hipódromo de palermo":    ("Hipódromo de Palermo",        "Buenos Aires", "CABA"),
    "hipodromo de palermo":    ("Hipódromo de Palermo",        "Buenos Aires", "CABA"),
    "campo argentino de polo": ("Campo Argentino de Polo",     "Buenos Aires", "CABA"),
    "vorterix":                ("Vorterix",                    "Buenos Aires", "CABA"),
    "luna park":               ("Luna Park",                   "Buenos Aires", "CABA"),
    "gran rex":                ("Teatro Gran Rex",             "Buenos Aires", "CABA"),
    "niceto":                  ("Niceto Club",                 "Buenos Aires", "CABA"),
    "la trastienda":           ("La Trastienda",               "Buenos Aires", "CABA"),
    "konex":                   ("Ciudad Cultural Konex",       "Buenos Aires", "CABA"),
    "teatro colón":            ("Teatro Colón",                "Buenos Aires", "CABA"),
    "teatro colon":            ("Teatro Colón",                "Buenos Aires", "CABA"),
    "palermo":                 ("Palermo",                     "Buenos Aires", "CABA"),
    "estadio único":           ("Estadio Único",               "La Plata",     "Buenos Aires"),
    "estadio unico":           ("Estadio Único",               "La Plata",     "Buenos Aires"),
    "la plata":                ("Estadio Único",               "La Plata",     "Buenos Aires"),
    "kempes":                  ("Estadio Mario Kempes",        "Córdoba",      "Córdoba"),
    "mario kempes":            ("Estadio Mario Kempes",        "Córdoba",      "Córdoba"),
    "cosquín rock":            ("Cosquín Rock",                "Córdoba",      "Córdoba"),
    "cosquin rock":            ("Cosquín Rock",                "Córdoba",      "Córdoba"),
    "córdoba":                 ("Córdoba",                     "Córdoba",      "Córdoba"),
    "cordoba":                 ("Córdoba",                     "Córdoba",      "Córdoba"),
    "malvinas argentinas":     ("Estadio Malvinas Argentinas", "Mendoza",      "Mendoza"),
    "mendoza":                 ("Mendoza",                     "Mendoza",      "Mendoza"),
    "rosario":                 ("Rosario",                     "Rosario",      "Santa Fe"),
    "tucumán":                 ("Tucumán",                     "Tucumán",      "Tucumán"),
    "tucuman":                 ("Tucumán",                     "Tucumán",      "Tucumán"),
    "salta":                   ("Salta",                       "Salta",        "Salta"),
    "mar del plata":           ("Mar del Plata",               "Mar del Plata","Buenos Aires"),
    "lollapalooza":            ("Hipódromo de Palermo",        "Buenos Aires", "CABA"),
}
 
# ---------------------------------------------------------------------------
# Filtro de eventos musicales
# ---------------------------------------------------------------------------
_MUSIC_KW = frozenset([
    # Géneros
    "rock","pop","jazz","cumbia","tango","folklore","folclore","metal","indie",
    "reggaeton","trap","rap","hip hop","hip-hop","electrónica","electronica",
    "techno","house","reggae","punk","blues","soul","funk","salsa","merengue",
    "tropical","bossa nova","r&b","rnb","country","música","musica",
    # Tipos de evento musical
    "recital","concierto","show","tour","gira","festival","en vivo","live",
    "dj set","dj","set en vivo","after","rave","fiesta electrónica",
    "fiesta electronica","boliche","baile","peña","milonga",
    # Festivales conocidos
    "lollapalooza","cosquín rock","cosquin rock","personal fest","creamfields",
    "flow festival","quilmes rock","pepsi music","astor piazzolla",
    # Artistas muy conocidos (como comodín)
    "wos","duki","bizarrap","paulo londra","tini","rusherking",
])
_NON_MUSIC_KW = frozenset([
    # Teatro
    "obra de teatro","obra teatral","función de teatro","funcion de teatro",
    "noche de teatro","teatro de revista","teatro municipal","teatro nacional",
    "puesta en escena","dramaturgia","monólogo","monologo",
    # Stand-up / humor
    "stand-up","stand up comedy","humor en vivo","comedia de humor",
    # Danza / circo
    "ballet clásico","ballet clasico","danza contemporánea","danza contemporanea",
    "espectáculo de danza","circo","magia en vivo",
    # Eventos no-entretenimiento
    "conferencia","congreso","workshop","webinar","seminario","capacitación",
    "capacitacion","charla","panel de",
    # Exposiciones / ferias
    "exposición","exposicion","muestra de","feria del libro","feria de arte",
    "galería","galeria","arte contemporáneo",
    # Deportes
    "partido de","torneo de","campeonato",
])
 
# Estos keywords excluyen el evento SIN IMPORTAR qué más diga el texto
_HARD_EXCLUDE_KW = frozenset([
    "stand-up", "stand up comedy", "obra de teatro", "obra teatral",
    "noche de teatro", "función de teatro", "funcion de teatro",
    "conferencia de", "congreso de", "taller de", "workshop de",
    "exposición de", "exposicion de", "muestra de arte", "muestra fotográfica",
])
 
def es_evento_musical(nombre: str, texto: str) -> bool:
    """
    Filtra: solo recitales, conciertos y eventos de música.
    Lógica (en orden de precedencia):
      1. Hard-exclude: si tiene keyword de exclusión absoluta → descartar siempre
      2. Non-music + sin música → descartar
      3. Cualquier otro caso → incluir (permisivo con artistas sin keyword)
    """
    t = (nombre + " " + texto).lower()
    # 1. Exclusiones absolutas (stand-up, obra de teatro, etc.)
    if any(k in t for k in _HARD_EXCLUDE_KW):
        return False
    # 2. Señal clara de no-música sin ninguna señal musical
    tiene_musica    = any(k in t for k in _MUSIC_KW)
    tiene_no_musica = any(k in t for k in _NON_MUSIC_KW)
    if tiene_no_musica and not tiene_musica:
        return False
    return True
 
 
# ---------------------------------------------------------------------------
# Parseo de fechas
# ---------------------------------------------------------------------------
MESES = {
    "enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,
    "julio":7,"agosto":8,"septiembre":9,"setiembre":9,"octubre":10,
    "noviembre":11,"diciembre":12,
}
_FECHA_LEJANA = date(9999, 12, 31)
 
 
def extraer_fecha(texto: str) -> date | None:
    t = texto.lower()
    m = re.search(r"\b(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})\b", t)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100: y += 2000
            return date(y, mo, d)
        except ValueError: pass
    m = re.search(r"\b(\d{1,2})\s+de\s+(\w+)(?:\s+de\s+)?(\d{4})\b", t)
    if m:
        mo = MESES.get(m.group(2))
        if mo:
            try: return date(int(m.group(3)), mo, int(m.group(1)))
            except ValueError: pass
    m = re.search(r"\b(\d{1,2})\s+(\w+)\s+(\d{4})\b", t)
    if m:
        mo = MESES.get(m.group(2))
        if mo:
            try: return date(int(m.group(3)), mo, int(m.group(1)))
            except ValueError: pass
    return None
 
 
def extraer_anio(texto: str) -> int | None:
    anios = re.findall(r"\b(20[2-3]\d)\b", texto)
    return int(anios[0]) if anios else None
 
 
def detectar_disponibilidad(texto: str, clases_extra: str = "") -> str:
    """
    Detecta disponibilidad mirando texto del elemento Y clases CSS de botones/links.
    Orden de prioridad: agotado > pocas > disponible > sin datos.
    """
    t = (texto + " " + clases_extra).lower()
    if any(k in t for k in ["agotado", "sold out", "sin stock", "soldout",
                             "sold-out", "no hay entradas", "no disponible",
                             "out-of-stock", "outofstock"]):
        return "agotado"
    if any(k in t for k in ["últimas", "ultimas", "pocas", "pocos",
                             "last tickets", "últimos", "few tickets",
                             "limited", "casi agotado"]):
        return "pocas entradas"
    if any(k in t for k in ["disponible", "comprá", "compra ahora", "get tickets",
                             "comprar entradas", "comprar entrada", "compra tu entrada",
                             "elegí", "buy tickets", "buy now", "on sale"]):
        return "disponible"
    return "sin datos"
 
 
def extraer_venue_ciudad(texto: str) -> tuple[str, str, str]:
    t = texto.lower()
    for keyword, (venue, ciudad, provincia) in VENUES_AR.items():
        if keyword in t:
            return venue, ciudad, provincia
    return "", "", ""
 
 
def extraer_artista(nombre: str) -> str:
    """
    Extrae el nombre del artista del título del evento para buscar en iTunes.
      "Wos – Caño Tour"       → "Wos"
      "Coldplay en River"     → "Coldplay"
      "La Beriso presenta..." → "La Beriso"
    """
    for sep in [" – ", " — ", " - ", ": ", " | "]:
        if sep in nombre:
            return nombre.split(sep)[0].strip()[:60]
    # Sufijos de contexto a remover
    resultado = nombre.strip()
    for patron in [
        r"\s+en\s+(el\s+|la\s+|los\s+|las\s+)?\w.*$",  # "en el Movistar"
        r"\s+presenta\b.*$",                              # "presenta su show"
        r"\s+\d{4}\b.*$",                                 # "Tour 2026"
        r"\s*\(.*$",                                      # "(sold out)"
    ]:
        nuevo = re.sub(patron, "", resultado, flags=re.IGNORECASE).strip()
        if nuevo and nuevo != resultado:
            resultado = nuevo
            break
    return resultado[:60]
 
 
def extraer_precio(texto: str) -> str | None:
    """
    Extrae el precio mínimo encontrado en el texto.
    Busca patrones como: $5.000 | $15,000 | $1500 | desde $3.200
    Rango válido para entradas en Argentina: $500 – $2.000.000
    """
    # Captura número tras signo $, con posibles separadores de miles
    matches = re.findall(r'\$\s*(\d[\d.,\s]{0,9})', texto)
    precios = []
    for m in matches:
        try:
            # Normalizar: quitar puntos y espacios de miles, cortar en coma decimal
            limpio = re.sub(r'[\.\s]', '', m).split(',')[0]
            num = int(limpio)
            if 500 <= num <= 2_000_000:
                precios.append(num)
        except ValueError:
            pass
    if not precios:
        return None
    minimo = min(precios)
    # Formatear con punto como separador de miles (estilo AR)
    return f"${minimo:,}".replace(",", ".")
 
 
# ---------------------------------------------------------------------------
# Modelo de evento
# ---------------------------------------------------------------------------
 
class Evento:
    def __init__(self, nombre: str, plataforma: str, texto_completo: str = "",
                 url: str = "", clases_disponibilidad: str = "", imagen_url: str = ""):
        self.nombre         = nombre.strip()
        self.plataforma     = plataforma
        self.texto_completo = texto_completo or nombre
        self.url            = url
        self.imagen_url     = imagen_url
        self.fecha: date | None = extraer_fecha(self.texto_completo)
        self._anio: int | None  = self.fecha.year if self.fecha else extraer_anio(self.texto_completo)
        self.venue, self.ciudad, self.provincia = extraer_venue_ciudad(self.texto_completo)
        self.disponibilidad = detectar_disponibilidad(self.texto_completo, clases_disponibilidad)
        self.precio         = extraer_precio(self.texto_completo)
 
    @property
    def uid(self) -> str:
        return hashlib.sha256(f"{self.nombre}|{self.plataforma}".encode()).hexdigest()[:16]
 
    @property
    def vigente(self) -> bool:
        anio_actual = date.today().year
        if self.fecha is not None: return self.fecha >= date.today()
        if self._anio is not None: return self._anio >= anio_actual
        return True
 
    @property
    def fecha_display(self) -> str:
        if self.fecha: return self.fecha.strftime("%d/%m/%Y")
        if self._anio: return f"{self._anio} (a confirmar)"
        return "a confirmar"
 
    @property
    def sort_key(self):
        if self.fecha: return self.fecha
        if self._anio: return date(self._anio, 12, 31)
        return _FECHA_LEJANA
 
    def to_dict(self, es_nuevo: bool = False) -> dict:
        return {
            "uid":           self.uid,
            "nombre":        self.nombre,
            "artista":       extraer_artista(self.nombre),
            "plataforma":    self.plataforma,
            "fecha_display": self.fecha_display,
            "fecha_iso":     self.fecha.isoformat() if self.fecha else "",
            "venue":         self.venue or "a confirmar",
            "ciudad":        self.ciudad or "a confirmar",
            "provincia":     self.provincia or "",
            "disponibilidad": self.disponibilidad,
            "precio":        self.precio or "",
            "imagen_url":    self.imagen_url,
            "url":           self.url,
            "es_nuevo":      es_nuevo,
        }
 
 
# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------
 
def cargar_historial() -> dict:
    if SEEN_EVENTS_FILE.exists():
        try:
            with open(SEEN_EVENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("Historial corrupto. Se reinicia.")
    return {"primera_ejecucion": True, "ultima_ejecucion": None, "eventos_vistos": []}
 
 
def guardar_historial(historial: dict) -> None:
    try:
        with open(SEEN_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        log.info("Historial guardado: %d eventos", len(historial["eventos_vistos"]))
    except OSError as e:
        log.error("Error al guardar historial: %s", type(e).__name__)
 
 
# ---------------------------------------------------------------------------
# Generación de HTML
# ---------------------------------------------------------------------------
 
def generar_html(eventos: list, nuevos_uids: set) -> str:
    hoy        = date.today().strftime("%d/%m/%Y")
    total      = len(eventos)
    nuevos_cnt = len(nuevos_uids)
    nuevo_pill = (
        f'<span class="new-pill">{nuevos_cnt} nuevo{"s" if nuevos_cnt != 1 else ""}</span>'
        if nuevos_cnt > 0 else ""
    )
    eventos_dict = [ev.to_dict(es_nuevo=(ev.uid in nuevos_uids)) for ev in eventos]
    events_json  = json.dumps(eventos_dict, ensure_ascii=False).replace("</", "<\\/")
 
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eventos Argentina 🎵</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#edecea;color:#111827;min-height:100vh}}
a{{color:inherit;text-decoration:none}}
.wrap{{max-width:980px;margin:0 auto;padding:0 18px}}
 
/* Header */
.hdr{{padding:28px 0 0;display:flex;align-items:center;gap:14px}}
.hdr-icon{{font-size:36px;line-height:1}}
.hdr-title{{font-size:26px;font-weight:800;letter-spacing:-.5px}}
.new-pill{{background:#d1fae5;color:#065f46;font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;margin-left:10px;vertical-align:middle}}
.hdr-sub{{font-size:13px;color:#6b7280;margin-top:3px}}
 
/* Stats */
.stats{{margin:18px 0 0;display:flex;gap:10px;flex-wrap:wrap}}
.stat{{background:#fff;border:0.5px solid rgba(0,0,0,.08);border-radius:12px;padding:11px 18px}}
.stat-n{{font-size:22px;font-weight:800}}
.stat-l{{font-size:12px;color:#6b7280;margin-top:1px}}
 
/* Legend */
.legend{{margin:14px 0 0;display:flex;gap:16px;flex-wrap:wrap;align-items:center}}
.li{{display:flex;align-items:center;gap:6px;font-size:12px;color:#6b7280}}
.ldot{{width:8px;height:8px;border-radius:50%}}
.legend-note{{margin-left:auto;font-size:11px;color:#9ca3af}}
 
/* Filters */
.fw{{margin:18px 0 0}}
.fl{{font-size:11px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px}}
.chips{{display:flex;flex-wrap:wrap;gap:7px}}
.chip{{padding:6px 18px;border-radius:999px;font-size:13px;border:0.5px solid rgba(0,0,0,.1);background:#fff;color:#6b7280;cursor:pointer;transition:all .15s;user-select:none}}
.chip:hover:not(.active){{border-color:#999;color:#111}}
.chip.active{{background:#111827;color:#fff;border-color:#111827}}
 
/* Grid */
.grid{{margin:18px 0 50px;display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:14px}}
.empty{{grid-column:1/-1;text-align:center;padding:60px;color:#9ca3af;font-size:15px}}
 
/* Card */
.card{{background:#fff;border-radius:18px;border:0.5px solid rgba(0,0,0,.07);display:flex;flex-direction:column;overflow:hidden;transition:transform .2s,box-shadow .2s}}
.card:hover{{transform:translateY(-3px);box-shadow:0 10px 28px rgba(0,0,0,.1)}}
 
/* Hero (artist photo) */
.hero{{height:185px;background-size:cover;background-position:center top;position:relative;overflow:hidden;transition:background-image .3s ease}}
.hero-grad{{position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.82) 0%,rgba(0,0,0,.1) 55%,transparent 100%)}}
.hero-bot{{position:absolute;bottom:0;left:0;right:0;padding:13px 15px}}
.hero-name{{font-size:15px;font-weight:800;color:#fff;line-height:1.3;text-shadow:0 1px 5px rgba(0,0,0,.6);margin-bottom:9px}}
 
/* 5-bar availability meter
   disponible    → 5 barras VERDES
   pocas entradas → 2 barras ROJAS + 3 grises tenues
   agotado       → 5 barras ROJAS
   sin datos     → 5 barras GRISES */
.meter{{display:flex;align-items:center;gap:5px}}
.mdot{{width:24px;height:6px;border-radius:3px}}
.meter-lbl{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-left:7px;color:rgba(255,255,255,.75)}}
 
/* Card body */
.cbody{{padding:13px 15px 12px;flex:1;display:flex;flex-direction:column;gap:11px}}
.badges{{display:flex;gap:5px;flex-wrap:wrap}}
.badge{{font-size:10px;font-weight:700;padding:2px 9px;border-radius:6px;letter-spacing:.03em}}
.b-new{{background:#d1fae5;color:#065f46}}
.b-date{{background:#ede9fe;color:#5b21b6}}
.b-price{{background:#fef3c7;color:#92400e;font-size:11px}}
.b-co{{background:#f3f4f6;color:#6b7280;font-weight:500}}
 
/* Venue row */
.vrow{{display:flex;align-items:center;gap:10px}}
.vimg{{width:48px;height:48px;border-radius:9px;object-fit:cover;flex-shrink:0;background:#e5e7eb;display:none}}
.vimg.ok{{display:block}}
.vfall{{width:48px;height:48px;border-radius:9px;background:#f3f4f6;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:20px}}
.vname{{font-size:13px;font-weight:600;color:#374151;line-height:1.3}}
.vcity{{font-size:11px;color:#9ca3af;margin-top:2px}}
 
/* Footer */
.cfoot{{border-top:0.5px solid rgba(0,0,0,.07)}}
.buy{{display:block;text-align:center;padding:13px;font-size:13px;font-weight:700;color:#4f46e5;transition:background .15s}}
.buy:hover{{background:#eef2ff}}
.nobuy{{color:#9ca3af;cursor:default;font-weight:400}}
 
footer{{text-align:center;padding:24px 0;font-size:12px;color:#9ca3af}}
 
@media(max-width:520px){{
  .grid{{grid-template-columns:1fr}}
  .hdr-title{{font-size:21px}}
}}
</style>
</head>
<body>
<div class="wrap">
 
<div class="hdr">
  <div class="hdr-icon">🎵</div>
  <div>
    <div class="hdr-title">Eventos en Argentina {nuevo_pill}</div>
    <div class="hdr-sub">Actualizado el {hoy} &middot; {total} eventos vigentes</div>
  </div>
</div>
 
<div class="stats" id="stats"></div>
 
<div class="legend">
  <div class="li"><div class="ldot" style="background:#16a34a"></div>Disponible</div>
  <div class="li"><div class="ldot" style="background:#ef4444"></div>Pocas entradas</div>
  <div class="li"><div class="ldot" style="background:#dc2626"></div>Agotado</div>
  <div class="li"><div class="ldot" style="background:#9ca3af"></div>Sin datos</div>
  <span class="legend-note">Fotos v&iacute;a iTunes &amp; Wikipedia</span>
</div>
 
<div class="fw">
  <div class="fl">Filtrar por ciudad</div>
  <div class="chips" id="chips"></div>
</div>
 
<div class="grid" id="grid"></div>
 
<footer>Monitor de Eventos Argentina &middot; Actualiza cada 2 horas</footer>
</div>
 
<script>
const EVENTS = {events_json};
 
function esc(s){{
  return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : '';
}}
 
/* ── Gradiente de color consistente basado en el nombre del artista ──
   Se aplica INMEDIATAMENTE al renderizar la tarjeta.
   Cuando llega la foto de iTunes, reemplaza el gradiente. */
function artistGradient(name) {{
  let h = 0;
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  const hue  = Math.abs(h) % 360;
  const hue2 = (hue + 55) % 360;
  return `linear-gradient(145deg,hsl(${{hue}},50%,18%) 0%,hsl(${{hue2}},62%,30%) 100%)`;
}}
 
/* ── Medidor de disponibilidad ──
   disponible    → 5 barras VERDES
   pocas entradas → 2 barras ROJAS + 3 grises
   agotado       → 5 barras ROJAS
   sin datos     → 5 barras GRISES */
function meterHTML(disp) {{
  const BARS = {{
    'disponible':     {{ filled:5, colorOn:'#16a34a', colorOff:'rgba(255,255,255,0.15)' }},
    'pocas entradas': {{ filled:2, colorOn:'#ef4444', colorOff:'rgba(255,255,255,0.2)'  }},
    'agotado':        {{ filled:5, colorOn:'#dc2626', colorOff:'rgba(255,255,255,0.15)' }},
    'sin datos':      {{ filled:5, colorOn:'#9ca3af', colorOff:'rgba(255,255,255,0.15)' }},
  }};
  const cfg  = BARS[disp] || BARS['sin datos'];
  const dots = Array.from({{length:5}}, (_,i) =>
    `<div class="mdot" style="background:${{i < cfg.filled ? cfg.colorOn : cfg.colorOff}}"></div>`
  ).join('');
  const labels = {{
    'disponible':'disponible','pocas entradas':'¡pocas!',
    'agotado':'agotado','sin datos':'sin datos'
  }};
  return `${{dots}}<span class="meter-lbl">${{labels[disp] || disp}}</span>`;
}}
 
let active = 'all';
 
function renderStats(evs) {{
  const disp = evs.filter(e => e.disponibilidad === 'disponible').length;
  const poco = evs.filter(e => e.disponibilidad === 'pocas entradas').length;
  const agot = evs.filter(e => e.disponibilidad === 'agotado').length;
  const prec = evs.filter(e => e.precio).length;
  document.getElementById('stats').innerHTML =
    `<div class="stat"><div class="stat-n">${{evs.length}}</div><div class="stat-l">eventos</div></div>` +
    (disp ? `<div class="stat"><div class="stat-n" style="color:#16a34a">${{disp}}</div><div class="stat-l">disponibles</div></div>` : '') +
    (poco ? `<div class="stat"><div class="stat-n" style="color:#f97316">${{poco}}</div><div class="stat-l">pocas entradas</div></div>` : '') +
    (agot ? `<div class="stat"><div class="stat-n" style="color:#dc2626">${{agot}}</div><div class="stat-l">agotados</div></div>` : '') +
    (prec ? `<div class="stat"><div class="stat-n" style="color:#92400e">${{prec}}</div><div class="stat-l">con precio</div></div>` : '');
}}
 
function cardHTML(ev) {{
  const venueOk  = ev.venue && ev.venue !== 'a confirmar';
  const ciudadOk = ev.ciudad && ev.ciudad !== 'a confirmar';
  /* Foto del evento scraped de la plataforma — gradiente como fallback */
  const heroStyle = ev.imagen_url
    ? `background-image:url('${{esc(ev.imagen_url)}}');background-size:cover;background-position:center top`
    : `background:${{artistGradient(ev.artista || ev.nombre)}}`;
  return `
    <div class="card">
      <div class="hero" style="${{heroStyle}}">
        <div class="hero-grad"></div>
        <div class="hero-bot">
          <div class="hero-name">${{esc(ev.nombre)}}</div>
          <div class="meter">${{meterHTML(ev.disponibilidad)}}</div>
        </div>
      </div>
      <div class="cbody">
        <div class="badges">
          ${{ev.es_nuevo  ? '<span class="badge b-new">NUEVO</span>' : ''}}
          <span class="badge b-date">${{esc(ev.fecha_display)}}</span>
          ${{ev.precio    ? `<span class="badge b-price">Desde ${{esc(ev.precio)}}</span>` : ''}}
          <span class="badge b-co">${{esc(ev.plataforma)}}</span>
        </div>
        <div class="vrow">
          <img class="vimg" id="vimg-${{esc(ev.uid)}}" alt="${{esc(ev.venue)}}">
          <div class="vfall" id="vfall-${{esc(ev.uid)}}">🏟</div>
          <div>
            <div class="vname">${{venueOk ? esc(ev.venue) : 'Venue a confirmar'}}</div>
            ${{ciudadOk ? `<div class="vcity">${{esc(ev.ciudad)}}</div>` : ''}}
          </div>
        </div>
      </div>
      <div class="cfoot">
        ${{ev.url
          ? `<a class="buy" href="${{esc(ev.url)}}" target="_blank" rel="noopener noreferrer">Comprar entradas →</a>`
          : `<span class="buy nobuy">Ver en ${{esc(ev.plataforma)}}</span>`
        }}
      </div>
    </div>`;
}}
 
function renderCards() {{
  const evs = active === 'all'
    ? EVENTS
    : EVENTS.filter(e => e.ciudad === active || e.provincia === active);
  renderStats(evs);
  const grid = document.getElementById('grid');
  if (!evs.length) {{ grid.innerHTML = '<div class="empty">Sin eventos para esta ciudad.</div>'; return; }}
  grid.innerHTML = evs.map(cardHTML).join('');
  loadVenueImages(evs);
}}
 
/* ── Foto de venue desde Wikipedia (único fetch externo que queda) ── */
async function fetchVenueImg(venue) {{
  if (!venue || venue === 'a confirmar') return null;
  for (const lang of ['es', 'en']) {{
    try {{
      const r = await fetch(`https://${{lang}}.wikipedia.org/api/rest_v1/page/summary/${{encodeURIComponent(venue)}}`);
      if (!r.ok) continue;
      const d = await r.json();
      if (d.thumbnail && d.thumbnail.source) return d.thumbnail.source;
    }} catch(e) {{}}
  }}
  return null;
}}
 
/* ── Carga de venue thumbnails (Wikipedia, async) ── */
async function loadVenueImages(evs) {{
  const BATCH = 4;
  for (let i = 0; i < evs.length; i += BATCH) {{
    await Promise.allSettled(evs.slice(i, i + BATCH).map(async ev => {{
      const venueUrl = await fetchVenueImg(ev.venue);
      const vimg  = document.getElementById('vimg-' + ev.uid);
      const vfall = document.getElementById('vfall-' + ev.uid);
      if (vimg && venueUrl) {{
        vimg.src = venueUrl;
        vimg.onload = () => {{
          vimg.classList.add('ok');
          if (vfall) vfall.style.display = 'none';
        }};
      }}
    }}));
  }}
}}
 
/* ── Filtros ── */
function buildChips() {{
  const ciudades = [...new Set(EVENTS.map(e => e.ciudad).filter(c => c && c !== 'a confirmar'))].sort();
  const wrap = document.getElementById('chips');
  const all  = document.createElement('div');
  all.className = 'chip active'; all.textContent = 'Todas';
  all.onclick = () => setFilter('all', all);
  wrap.appendChild(all);
  ciudades.forEach(c => {{
    const ch = document.createElement('div');
    ch.className = 'chip'; ch.textContent = c;
    ch.onclick = () => setFilter(c, ch);
    wrap.appendChild(ch);
  }});
}}
 
function setFilter(val, el) {{
  active = val;
  document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  renderCards();
}}
 
buildChips();
renderCards();
</script>
</body>
</html>"""
 
 
# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
 
def enviar_telegram(mensaje: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Faltan credenciales de Telegram.")
        return False
    try:
        for chunk in [mensaje[i:i+4000] for i in range(0, len(mensaje), 4000)]:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": int(TELEGRAM_CHAT_ID), "text": chunk,
                      "parse_mode": "HTML", "disable_web_page_preview": False},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            if not resp.json().get("ok"):
                log.error("Telegram rechazó: %s", resp.json().get("description", ""))
                return False
        log.info("Notificación enviada.")
        return True
    except requests.exceptions.RequestException as e:
        log.error("Error de red Telegram: %s", type(e).__name__)
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
 
 
def _encontrar_url(el, base_url: str, dominios_permitidos: set) -> str:
    """Busca href más cercano y lo valida contra dominios permitidos."""
    href = ""
    if el.name == "a" and el.get("href"):
        href = el["href"]
    else:
        anc = el.find_parent("a")
        if anc and anc.get("href"):
            href = anc["href"]
        else:
            desc = el.find("a", href=True)
            if desc: href = desc["href"]
            elif el.parent:
                sib = el.parent.find("a", href=True)
                if sib: href = sib["href"]
 
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return ""
    if not href.startswith("http"):
        href = urljoin(base_url, href)
    try:
        dominio = urlparse(href).netloc.lower().lstrip("www.")
        if any(dominio == d or dominio.endswith("." + d) for d in dominios_permitidos):
            return href
    except Exception:
        pass
    return ""
 
 
# Atributos usados por lazy-loaders de imágenes (orden de preferencia)
_IMG_ATTRS = ("src", "data-src", "data-lazy-src", "data-original",
              "data-lazy", "data-url", "data-image", "data-bg")
# Patrones que indican tracking pixel o placeholder → ignorar
_IMG_SKIP = ("pixel", "tracking", "analytics", "1x1", "spacer",
             "blank.gif", "placeholder", "loading.gif", "spinner")
 
def _encontrar_imagen(el, base_url: str) -> str:
    """
    Extrae la URL de la imagen del evento desde el elemento y sus ancestros.
    Prioriza imágenes grandes (con width/height explícito) sobre pequeñas.
    Sólo devuelve URLs HTTPS para evitar mixed-content.
    """
    candidatos: list[tuple[int, str]] = []  # (score, url)
 
    nodo = el
    for nivel in range(4):          # el, padre, abuelo, bisabuelo
        if nodo is None:
            break
        for img in nodo.find_all("img", limit=6):
            for attr in _IMG_ATTRS:
                src = (img.get(attr) or "").strip()
                if not src or src.startswith("data:") or src == "#":
                    continue
                src_l = src.lower()
                if any(p in src_l for p in _IMG_SKIP):
                    continue
                if not src.startswith("http"):
                    src = urljoin(base_url, src)
                if not src.startswith("https://"):
                    continue
                # Score: mayor ancho explícito = mejor; nivel más cercano = mejor
                try:
                    w = int(img.get("width", 0))
                except (ValueError, TypeError):
                    w = 0
                score = w - nivel * 50   # penalizar imágenes lejanas al elemento
                candidatos.append((score, src))
        nodo = nodo.parent
 
    if not candidatos:
        return ""
    candidatos.sort(key=lambda x: x[0], reverse=True)
    return candidatos[0][1]
 
 
def _extraer_eventos(soup, plataforma, selectores_nombre,
                     base_url="", dominios_permitidos=None) -> list:
    vistos: set = set()
    eventos = []
    dominios = dominios_permitidos or set()
 
    for selector in selectores_nombre:
        for el in soup.select(selector):
            nombre = el.get_text(separator=" ", strip=True)[:120]
            if not nombre or len(nombre) < 5 or nombre in vistos:
                continue
            vistos.add(nombre)
 
            # Siempre empezar con el nombre para que venue/fecha del título se detecten
            clases_extra = ""
            padre = el.parent
            ctx   = padre.get_text(separator=" ", strip=True)[:400] if padre else ""
            # Prepend nombre → el título siempre está en el texto buscado
            texto_completo = (nombre + " " + ctx).strip()
 
            if padre:
                # Escanear clases y texto de botones/links para disponibilidad
                for tag in padre.find_all(["button", "a", "span"], limit=8):
                    clases_extra += " " + " ".join(tag.get("class", []))
                    clases_extra += " " + tag.get_text(strip=True)
 
            # Filtrar eventos que claramente no son musicales
            if not es_evento_musical(nombre, texto_completo):
                log.debug("Descartado (no musical): %s", nombre)
                continue
 
            url       = _encontrar_url(el, base_url, dominios) if base_url else ""
            imagen    = _encontrar_imagen(el, base_url) if base_url else ""
            eventos.append(Evento(nombre, plataforma, texto_completo, url=url,
                                  clases_disponibilidad=clases_extra, imagen_url=imagen))
    return eventos
 
 
def scrape_df_entertainment() -> list:
    base = "https://dfentertainment.com"
    soup = _get_soup(f"{base}/shows")
    if not soup: return []
    ev = _extraer_eventos(soup, "DF Entertainment",
                          ["h1","h2","h3","h4",".show-title",".event-name","a[href*='show']"],
                          base_url=base, dominios_permitidos={"dfentertainment.com"})
    log.info("DF Entertainment: %d", len(ev)); return ev
 
 
def scrape_allaccess() -> list:
    base = "https://www.allaccess.com.ar"
    soup = _get_soup(f"{base}/")
    if not soup: return []
    ev = _extraer_eventos(soup, "AllAccess",
                          ["h1","h2","h3",".event-title",".show-name","a[href*='/event/']"],
                          base_url=base, dominios_permitidos={"allaccess.com.ar"})
    log.info("AllAccess: %d", len(ev)); return ev
 
 
def scrape_ticketek() -> list:
    base = "https://www.ticketek.com.ar"
    soup = _get_soup(f"{base}/")
    if not soup: return []
    ev = _extraer_eventos(soup, "Ticketek",
                          ["h1","h2","h3",".show-title",".event-name","a[href*='/shows/']"],
                          base_url=base, dominios_permitidos={"ticketek.com.ar"})
    log.info("Ticketek: %d", len(ev)); return ev
 
 
def scrape_passline() -> list:
    base = "https://www.passline.com"
    soup = _get_soup(f"{base}/home")
    if not soup: return []
    ev = _extraer_eventos(soup, "Passline",
                          ["h1","h2","h3",".event-title",".event-name","a[href*='/eventos/']"],
                          base_url=base, dominios_permitidos={"passline.com"})
    log.info("Passline: %d", len(ev)); return ev
 
 
def scrape_livepass() -> list:
    base = "https://livepass.com.ar"
    soup = _get_soup(f"{base}/")
    if not soup: return []
    ev = _extraer_eventos(soup, "Livepass",
                          ["h1","h2","h3",".event-title",".event-name","a[href*='/evento/']"],
                          base_url=base, dominios_permitidos={"livepass.com.ar"})
    log.info("Livepass: %d", len(ev)); return ev
 
 
SCRAPERS = [scrape_df_entertainment, scrape_allaccess,
            scrape_ticketek, scrape_passline, scrape_livepass]
 
 
# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------
 
def main():
    log.info("=== Monitor de Eventos iniciado ===")
    hoy = date.today()
 
    historial   = cargar_historial()
    primera_vez = historial.get("primera_ejecucion", False)
    vistos_set  = set(historial.get("eventos_vistos", []))
 
    todos: list[Evento] = []
    for scraper in SCRAPERS:
        try: todos.extend(scraper())
        except Exception as e: log.error("Error en %s: %s", scraper.__name__, type(e).__name__)
 
    vigentes = [ev for ev in todos if ev.vigente]
    log.info("Total: %d | Vigentes: %d | Descartados: %d",
             len(todos), len(vigentes), len(todos) - len(vigentes))
 
    vigentes.sort(key=lambda e: e.sort_key)
    actuales_uids = {ev.uid for ev in vigentes}
 
    if primera_vez:
        log.info("Primera ejecución. Baseline: %d eventos.", len(vigentes))
        HTML_FILE.write_text(generar_html(vigentes, nuevos_uids=set()), encoding="utf-8")
        guardar_historial({"primera_ejecucion": False, "ultima_ejecucion": hoy.isoformat(),
                           "eventos_vistos": sorted(actuales_uids)})
        return
 
    nuevos_uids = actuales_uids - vistos_set
    nuevos      = [ev for ev in vigentes if ev.uid in nuevos_uids]
 
    HTML_FILE.write_text(generar_html(vigentes, nuevos_uids=nuevos_uids), encoding="utf-8")
    log.info("index.html generado: %d eventos (%d nuevos).", len(vigentes), len(nuevos))
 
    if nuevos:
        plural = len(nuevos) > 1
        if GITHUB_PAGES_URL:
            msg = (
                f"🎵 <b>¡{len(nuevos)} evento{'s' if plural else ''} nuevo{'s' if plural else ''} en Argentina!</b>\n\n"
                f"📋 <a href='{GITHUB_PAGES_URL}'>Ver listado completo →</a>\n\n"
                f"<i>Detectado el {hoy.strftime('%d/%m/%Y')}</i>"
            )
        else:
            lineas = [f"🎵 <b>¡{len(nuevos)} eventos nuevos!</b>\n"]
            for ev in nuevos[:20]:
                precio_txt = f" · {ev.precio}" if ev.precio else ""
                lineas.append(
                    f"📍 <b>{ev.plataforma}</b>: {ev.nombre}"
                    f"{' — ' + ev.venue if ev.venue else ''} ({ev.fecha_display}){precio_txt}"
                )
            if len(nuevos) > 20:
                lineas.append(f"\n...y {len(nuevos) - 20} más.")
            msg = "\n".join(lineas)
        enviar_telegram(msg)
    else:
        log.info("Sin novedades.")
 
    guardar_historial({"primera_ejecucion": False, "ultima_ejecucion": hoy.isoformat(),
                       "eventos_vistos": sorted(actuales_uids)})
 
 
if __name__ == "__main__":
    main()
