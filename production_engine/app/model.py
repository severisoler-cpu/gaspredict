import os
import logging
import datetime
import numpy as np
import pandas as pd
from sqlalchemy import text

logger = logging.getLogger("gaspredict.model")

# Fricciones y cotas legales en España
MINIMUM_TAX_FLOOR_GASOIL = 0.4727    # IEH base diésel
MINIMUM_TAX_FLOOR_GASOLINA = 0.5039  # IEH base gasolina 95
VAT_RATE = 0.21

DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def generate_predictions(engine):
    """
    Genera predicciones a 7 y 14 días para:
    1. Comunidad Valenciana (Gasóleo A y Gasolina 95 E5).
    2. Plenoil Getafe (Gasolina 95 E5).
    3. Gasolinera MÁS BARATA de la ruta detectada dinámicamente (Gasolina 95 E5).

    Calcula la recomendación del "Mejor Día para Repostar" en moto (depósito 10 €).
    Devuelve (predictions_rows, recommendations_rows, prediction_summary).
    """
    logger.info("Iniciando generación de predicciones y análisis del mejor momento para repostar...")
    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. Extraer últimos precios medios locales
    sql_prices = """
    SELECT date_trunc('day', time) as day,
           ROUND(AVG(price_gasoil_a), 3) as avg_gasoil,
           ROUND(AVG(price_gasolina_95_e5), 3) as avg_gasolina
    FROM station_prices
    WHERE price_gasoil_a > 0
    GROUP BY date_trunc('day', time)
    ORDER BY day ASC;
    """

    # 2. Extraer histórico de macro indicadores
    sql_macro = """
    SELECT date_trunc('day', time) as day,
           brent_usd,
           brent_eur,
           eur_usd,
           heating_oil_usd,
           crack_spread_synthetic
    FROM macro_indicators
    ORDER BY day ASC;
    """

    # 3. Consultar Gasolinera Favorita
    fav_name = os.getenv("USER_FAVORITE_STATION_NAME", "PLENOIL")
    fav_muni = os.getenv("USER_FAVORITE_STATION_MUNICIPALITY", "Getafe")
    
    sql_plenoil = f"""
    SELECT gs.name, gs.municipality, sp.price_gasolina_95_e5
    FROM station_prices sp
    JOIN gas_stations gs ON sp.id_station = gs.id_station
    WHERE gs.name ILIKE '%%{fav_name}%%' AND gs.municipality ILIKE '%%{fav_muni}%%'
      AND sp.time = (SELECT MAX(time) FROM station_prices)
    LIMIT 1;
    """


    # 4. Consultar dinámicamente la MÁS BARATA hoy en la ruta de cercanías
    sql_cheapest = """
    SELECT name, municipality, price_gasolina_95_e5
    FROM v_my_commute_stations
    WHERE time = (SELECT MAX(time) FROM v_my_commute_stations)
      AND price_gasolina_95_e5 > 0
    ORDER BY price_gasolina_95_e5 ASC
    LIMIT 1;
    """

    with engine.connect() as conn:
        df_prices = pd.read_sql(text(sql_prices), conn)
        df_macro = pd.read_sql(text(sql_macro), conn)
        res_plenoil = conn.execute(text(sql_plenoil)).mappings().first()
        res_cheapest = conn.execute(text(sql_cheapest)).mappings().first()

    if df_prices.empty:
        logger.warning("Sin datos locales suficientes para predecir.")
        return [], [], None

    current_gasoil = float(df_prices['avg_gasoil'].iloc[-1])
    current_gasolina = float(df_prices['avg_gasolina'].iloc[-1])

    # 5. Modelado econométrico de transmisión y lag (asimetría "cohete y pluma")
    brent_trend_factor_gasoil = 0.0
    brent_trend_factor_gasolina = 0.0
    if not df_macro.empty and len(df_macro) >= 20:
        brent_recent = df_macro['brent_eur'].dropna().iloc[-1]
        brent_past = df_macro['brent_eur'].dropna().iloc[-15] if len(df_macro) >= 15 else brent_recent
        pct_change = (brent_recent - brent_past) / brent_past if brent_past else 0.0

        brent_trend_factor_gasoil = pct_change * 0.35 * current_gasoil
        brent_trend_factor_gasolina = pct_change * 0.38 * current_gasolina

    predictions_rows = []
    horizons = [1, 2, 3, 4, 5, 6, 7, 10, 14]

    # A) Predecir COMUNIDAD_VALENCIANA (Gasóleo A y Gasolina 95 E5)
    for h in horizons:
        target_date = (now + datetime.timedelta(days=h)).date()
        weekday = target_date.weekday()
        seasonality = 0.005 if weekday in [4, 5] else (-0.004 if weekday == 0 else 0.0)
        conf_delta = float(round(0.015 * np.sqrt(h), 3))

        # Diésel
        w_gasoil = min(1.0, h / 7.0) if brent_trend_factor_gasoil > 0 else min(1.0, h / 14.0)
        pred_gasoil = max(
            float(round(current_gasoil + (brent_trend_factor_gasoil * w_gasoil) + seasonality, 3)),
            float(MINIMUM_TAX_FLOOR_GASOIL * (1 + VAT_RATE))
        )
        predictions_rows.append((
            now, target_date, "COMUNIDAD_VALENCIANA", "GASOIL_A", pred_gasoil,
            float(round(pred_gasoil - conf_delta, 3)), float(round(pred_gasoil + conf_delta, 3)),
            "v1.1-econometric"
        ))

        # Gasolina 95
        w_gasolina = min(1.0, h / 7.0) if brent_trend_factor_gasolina > 0 else min(1.0, h / 14.0)
        pred_gasolina = max(
            float(round(current_gasolina + (brent_trend_factor_gasolina * w_gasolina) + seasonality, 3)),
            float(MINIMUM_TAX_FLOOR_GASOLINA * (1 + VAT_RATE))
        )
        predictions_rows.append((
            now, target_date, "COMUNIDAD_VALENCIANA", "GASOLINA_95_E5", pred_gasolina,
            float(round(pred_gasolina - conf_delta, 3)), float(round(pred_gasolina + conf_delta, 3)),
            "v1.1-econometric"
        ))

    # B) Predecir estaciones individuales y calcular recomendación de repostaje
    recommendations_rows = []
    station_targets = []

    if res_plenoil and res_plenoil["price_gasolina_95_e5"]:
        station_targets.append({
            "scope": "FAVORITE_STATION",
            "name": res_plenoil["name"],
            "municipality": res_plenoil["municipality"],
            "current_price": float(res_plenoil["price_gasolina_95_e5"])
        })

    if res_cheapest and res_cheapest["price_gasolina_95_e5"]:
        station_targets.append({
            "scope": "CHEAPEST_COMMUTE",
            "name": res_cheapest["name"],
            "municipality": res_cheapest["municipality"],
            "current_price": float(res_cheapest["price_gasolina_95_e5"])
        })

    station_recommendations_summary = {}

    for st in station_targets:
        scope = st["scope"]
        base_price = st["current_price"]
        daily_curve = {0: base_price}  # día 0 = hoy

        for h in horizons:
            target_date = (now + datetime.timedelta(days=h)).date()
            weekday = target_date.weekday()
            seasonality = 0.005 if weekday in [4, 5] else (-0.004 if weekday == 0 else 0.0)

            # Las gasolineras low-cost aplican un retardo extra de ~24-48h antes de repercutir subidas
            adjusted_h = max(0.5, h - 0.5) if brent_trend_factor_gasolina > 0 else h
            w = min(1.0, adjusted_h / 7.0) if brent_trend_factor_gasolina > 0 else min(1.0, adjusted_h / 14.0)

            pred_p = max(
                float(round(base_price + (brent_trend_factor_gasolina * w) + seasonality, 3)),
                float(MINIMUM_TAX_FLOOR_GASOLINA * (1 + VAT_RATE))
            )
            daily_curve[h] = pred_p

            conf_delta = float(round(0.015 * np.sqrt(h), 3))
            predictions_rows.append((
                now, target_date, scope, "GASOLINA_95_E5", pred_p,
                float(round(pred_p - conf_delta, 3)), float(round(pred_p + conf_delta, 3)),
                "v1.1-econometric"
            ))

        # Evaluar mejor día para repostar en la ventana de 7 días
        # Buscamos el mínimo en h in [0..7]
        h_min = 0
        min_p = base_price
        for h_step in range(0, 8):
            p_val = daily_curve.get(h_step, base_price)
            if p_val < min_p:
                min_p = p_val
                h_min = h_step

        p_7d = daily_curve.get(7, base_price)
        change_7d = round(p_7d - base_price, 3)

        if h_min == 0 or change_7d > 0.01:
            best_day = "¡REPOSTA HOY!"
            best_date = now.date()
            best_price = base_price
            recommendation_text = f"Subida inminente prevista ({change_7d:+.3f} €/L a 7 días). Reposta hoy para congelar el mejor precio."
        else:
            best_date = (now + datetime.timedelta(days=h_min)).date()
            day_name = DIAS_SEMANA[best_date.weekday()]
            best_day = f"{day_name} ({best_date.strftime('%d/%m')})"
            best_price = min_p
            saving_10eur = round(10.0 * (1.0 - (best_price / base_price)), 2)
            extra_l = round((10.0 / best_price) - (10.0 / base_price), 2)
            recommendation_text = f"Bajada prevista. Conviene esperar al {day_name} ({best_price:.3f} €/L). Ahorro est. con 10 €: +{saving_10eur:.2f} € (+{extra_l:.2f} L)."

        litros_best = round(10.0 / best_price, 2)

        recommendations_rows.append((
            now,
            scope,
            st["name"],
            st["municipality"],
            base_price,
            p_7d,
            change_7d,
            best_day,
            best_date,
            best_price,
            litros_best,
            recommendation_text
        ))

        station_recommendations_summary[scope] = {
            "name": st["name"],
            "municipality": st["municipality"],
            "current_price": base_price,
            "predicted_price_7d": p_7d,
            "change_7d": change_7d,
            "best_day": best_day,
            "best_date": str(best_date),
            "best_price": best_price,
            "litros_best": litros_best,
            "recommendation_text": recommendation_text
        }

    # Resumen regional general
    pred_7d_reg = [r[4] for r in predictions_rows if r[2] == "COMUNIDAD_VALENCIANA" and r[3] == "GASOLINA_95_E5" and (r[1] - now.date()).days == 7]
    reg_change = round(pred_7d_reg[0] - current_gasolina, 3) if pred_7d_reg else 0.0
    if reg_change >= 0.02:
        reg_trend = "SUBIDA"
    elif reg_change <= -0.02:
        reg_trend = "BAJADA"
    else:
        reg_trend = "ESTABLE"

    prediction_summary = {
        "trend": reg_trend,
        "change": reg_change,
        "horizon_days": 7,
        "stations": station_recommendations_summary
    }

    logger.info(f"Generadas {len(predictions_rows)} predicciones y {len(recommendations_rows)} recomendaciones de repostaje.")
    return predictions_rows, recommendations_rows, prediction_summary
