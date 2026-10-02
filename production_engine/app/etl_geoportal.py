import ssl
import requests
import datetime
import logging
from datetime import timezone
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

logger = logging.getLogger("gaspredict.etl_geoportal")

class LegacySSLAdapter(HTTPAdapter):
    """Adapter para soportar servidores con TLS legado de la administración pública española."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except AttributeError:
            pass
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

MITECO_URL_CCAA_10 = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/FiltroCCAA/10"

LOW_COST_BRANDS = [
    "PLENOIL", "BALLENOIL", "PETROPRIX", "GASEXPRESS", "BEROIL", 
    "PLENERGY", "AUTONETOIL", "FAST FUEL", "FAMILY ENERGY", "GM FUEL",
    "AN ENERGIA", "EASYGAS", "PETRONOR DIESEL", "PETROMIRALLES"
]

PREMIUM_BRANDS = [
    "REPSOL", "CAMPSA", "PETRONOR", "CEPSA", "MOEVE", "BP", "SHELL", "GALP"
]

def classify_brand(brand_name: str) -> str:
    brand = (brand_name or "").upper().strip()
    for lc in LOW_COST_BRANDS:
        if lc in brand:
            return "LOW_COST"
    for pb in PREMIUM_BRANDS:
        if pb in brand:
            return "PREMIUM"
    return "INDEPENDENT"

def parse_float(val):
    if not val:
        return None
    try:
        val_clean = str(val).strip().replace(",", ".")
        f = float(val_clean)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None

def fetch_and_process_stations(max_retries=3):
    """
    Descarga los precios del Geoportal MITECO para la Comunidad Valenciana (CCAA 10).
    Devuelve: (stations_rows, prices_rows, extraction_time)
    """
    logger.info("Iniciando descarga de precios desde Geoportal MITECO (Comunidad Valenciana)...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    session = requests.Session()
    session.mount("https://", LegacySSLAdapter())

    data = None
    for attempt in range(max_retries):
        try:
            resp = session.get(MITECO_URL_CCAA_10, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:
            logger.warning(f"Intento {attempt+1}/{max_retries} fallido al consultar MITECO ({e}). Reintentando en 3s...")
            import time
            time.sleep(3)

    if not data:
        raise ConnectionError("No se pudo obtener respuesta válida del Geoportal MITECO tras reintentos.")

    # Fecha del reporte MITECO
    # Formato típico: "14/09/2026 23:45:00"
    raw_date = data.get("Fecha")
    extraction_time = datetime.datetime.now(timezone.utc)
    if raw_date:
        try:
            dt = datetime.datetime.strptime(raw_date, "%d/%m/%Y %H:%M:%S")
            extraction_time = dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            pass

    # Truncamos al día o franja para no duplicar si se ejecuta varias veces
    timestamp = extraction_time.replace(minute=0, second=0, microsecond=0)

    lista_eess = data.get("ListaEESSPrecio", [])
    logger.info(f"Descargadas {len(lista_eess)} estaciones de la Comunidad Valenciana.")

    stations_rows = []
    prices_rows = []

    for item in lista_eess:
        try:
            id_station = int(item.get("IDEESS"))
        except (ValueError, TypeError):
            continue

        name = item.get("Rótulo", "DESCONOCIDO").strip()
        address = item.get("Dirección", "").strip()
        municipality = item.get("Municipio", "").strip()
        province = item.get("Provincia", "").strip()
        postal_code = item.get("C.P.", "").strip()

        lat = parse_float(item.get("Latitud"))
        lon = parse_float(item.get("Longitud (WGS84)"))
        brand_type = classify_brand(name)

        stations_rows.append((
            id_station, name, address, municipality, province, postal_code, lat, lon, brand_type
        ))

        price_gasoil_a = parse_float(item.get("Precio Gasoleo A"))
        price_gasolina_95 = parse_float(item.get("Precio Gasolina 95 E5"))
        price_gasolina_98 = parse_float(item.get("Precio Gasolina 98 E5"))
        price_gasoil_prem = parse_float(item.get("Precio Gasoleo Premium"))

        prices_rows.append((
            timestamp,
            id_station,
            price_gasoil_a,
            price_gasolina_95,
            price_gasolina_98,
            price_gasoil_prem
        ))

    return stations_rows, prices_rows, timestamp
