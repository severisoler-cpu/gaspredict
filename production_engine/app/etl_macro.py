import datetime
import logging
import requests
import pandas as pd

logger = logging.getLogger("gaspredict.etl_macro")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={range}"

def fetch_yahoo_series(ticker: str, time_range="60d"):
    """
    Descarga serie temporal diaria de un ticker de Yahoo Finance sin requerir claves API.
    Devuelve un diccionario { fecha_str: close_price }
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    url = YAHOO_CHART_URL.format(ticker=ticker, range=time_range)
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0].get("close", [])

        series = {}
        for ts, val in zip(timestamps, closes):
            if val is not None:
                # Convertir timestamp a fecha UTC
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                day_key = dt.strftime("%Y-%m-%d")
                series[day_key] = float(val)
        return series
    except Exception as e:
        logger.error(f"Error descargando datos para ticker {ticker}: {e}")
        return {}

def fetch_and_process_macro(time_range="90d"):
    """
    Descarga y unifica Brent (BZ=F), EUR/USD (EURUSD=X), RBOB Gasoline (RB=F) y Heating Oil (HO=F).
    Calcula Brent en Euros y Crack Spread sintético 3:2:1.
    Devuelve una lista de tuplas listas para inserción en macro_indicators.
    """
    logger.info(f"Descargando indicadores macroeconómicos (rango={time_range})...")
    brent_data = fetch_yahoo_series("BZ=F", time_range)
    eurusd_data = fetch_yahoo_series("EURUSD=X", time_range)
    rbob_data = fetch_yahoo_series("RB=F", time_range)
    ho_data = fetch_yahoo_series("HO=F", time_range)

    # Obtenemos todas las fechas únicas
    all_dates = sorted(set(list(brent_data.keys()) + list(eurusd_data.keys())))
    if not all_dates:
        logger.warning("No se obtuvieron fechas macro.")
        return []

    rows = []
    # Valores por defecto para forward-fill en festivos/fines de semana
    last_brent = None
    last_eurusd = 1.08
    last_rbob = None
    last_ho = None

    for d_str in all_dates:
        dt = datetime.datetime.strptime(d_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)

        brent = brent_data.get(d_str, last_brent)
        eurusd = eurusd_data.get(d_str, last_eurusd)
        rbob = rbob_data.get(d_str, last_rbob)
        ho = ho_data.get(d_str, last_ho)

        if brent is not None:
            last_brent = brent
        if eurusd is not None:
            last_eurusd = eurusd
        if rbob is not None:
            last_rbob = rbob
        if ho is not None:
            last_ho = ho

        brent_eur = round(brent / eurusd, 3) if (brent and eurusd) else None

        # Fórmula canónica Crack Spread 3:2:1:
        # ((2 * P_gasolina * 42) + (1 * P_diesel * 42) - (3 * P_crudo)) / 3
        crack_spread = None
        if rbob and ho and brent:
            crack_spread = round(((2 * rbob * 42) + (1 * ho * 42) - (3 * brent)) / 3, 3)

        rows.append((
            dt,
            round(brent, 3) if brent else None,
            None, # gasoil_ice_usd (podemos usar ho como proxy directo o complementario)
            round(rbob, 3) if rbob else None,
            round(ho, 3) if ho else None,
            round(eurusd, 4) if eurusd else None,
            brent_eur,
            crack_spread
        ))

    logger.info(f"Procesados {len(rows)} registros macroeconómicos.")
    return rows
