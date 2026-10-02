import os
import sys
import time
import logging
import datetime
import schedule
from sqlalchemy import text
from dotenv import load_dotenv

import db
import etl_geoportal
import etl_macro
import notifier
import model

# Configuración de Logging profesional
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gaspredict.main")

def run_pipeline():
    """Ejecuta el ciclo completo de ETL, Predicción y Alertas."""
    logger.info("==================================================")
    logger.info("🚀 Iniciando ciclo de ejecución GasPredict...")
    logger.info("==================================================")
    try:
        engine = db.get_engine()

        # 1. Ingesta Macro (Yahoo Finance)
        macro_rows = etl_macro.fetch_and_process_macro(time_range="90d")
        inserted_macro = db.upsert_macro_indicators(engine, macro_rows)
        logger.info(f"Indicadores macro sincronizados: {inserted_macro} registros.")

        # 2. Ingesta Precios Comunidad Valenciana (Geoportal)
        stations, prices, extraction_time = etl_geoportal.fetch_and_process_stations()
        inserted_stations = db.upsert_gas_stations(engine, stations)
        inserted_prices = db.upsert_station_prices(engine, prices)
        logger.info(f"Estaciones actualizadas: {inserted_stations}, Precios registrados: {inserted_prices}.")

        # 3. Motor Predictivo y Recomendador de Repostaje
        preds, recs, pred_summary = model.generate_predictions(engine)
        inserted_preds = db.upsert_predictions(engine, preds)
        inserted_recs = db.upsert_refuel_recommendations(engine, recs)
        logger.info(f"Predicciones generadas: {inserted_preds}, Recomendaciones registradas: {inserted_recs}.")

        # 4. Datos para Notificaciones / Resumen
        sql_avg = """
        SELECT ROUND(AVG(price_gasoil_a), 3) as avg_gasoil_a,
               ROUND(AVG(price_gasolina_95_e5), 3) as avg_gasolina_95
        FROM station_prices
        WHERE time = (SELECT MAX(time) FROM station_prices)
          AND price_gasoil_a > 0;
        """
        sql_cheapest = """
        SELECT gs.name, gs.municipality, sp.price_gasoil_a
        FROM station_prices sp
        JOIN gas_stations gs ON sp.id_station = gs.id_station
        WHERE sp.time = (SELECT MAX(time) FROM station_prices)
          AND sp.price_gasoil_a > 0
        ORDER BY sp.price_gasoil_a ASC
        LIMIT 1;
        """
        sql_commute_cheapest = """
        SELECT name, municipality, price_gasolina_95_e5, litros_moto_10eur
        FROM v_my_commute_stations
        WHERE time = (SELECT MAX(time) FROM v_my_commute_stations)
        ORDER BY price_gasolina_95_e5 ASC
        LIMIT 1;
        """
        sql_plenoil = """
        SELECT name, municipality, price_gasolina_95_e5, litros_moto_10eur
        FROM v_my_commute_stations
        WHERE id_station = 4017 AND time = (SELECT MAX(time) FROM v_my_commute_stations)
        LIMIT 1;
        """

        avg_data = {}
        cheapest_data = {}
        commute_cheapest_data = {}
        plenoil_data = {}
        with engine.connect() as conn:
            res_avg = conn.execute(text(sql_avg)).mappings().first()
            if res_avg:
                avg_data = dict(res_avg)
            res_cheap = conn.execute(text(sql_cheapest)).mappings().first()
            if res_cheap:
                cheapest_data = dict(res_cheap)
            res_commute = conn.execute(text(sql_commute_cheapest)).mappings().first()
            if res_commute:
                commute_cheapest_data = dict(res_commute)
            res_plenoil = conn.execute(text(sql_plenoil)).mappings().first()
            if res_plenoil:
                plenoil_data = dict(res_plenoil)

        # 5. Enviar Alerta / Resumen Telegram
        date_str = extraction_time.strftime("%d/%m/%Y %H:%M")
        msg = notifier.format_daily_summary(
            date_str=date_str,
            avg_valencia=avg_data,
            cheapest_station=cheapest_data,
            plenoil_station=plenoil_data,
            cheapest_commute=commute_cheapest_data,
            prediction_summary=pred_summary
        )
        notifier.send_telegram_alert(msg)

        logger.info("✅ Ciclo de ejecución completado con éxito.")
    except Exception as e:
        logger.error(f"❌ Error durante la ejecución del pipeline: {e}", exc_info=True)

def main():
    load_dotenv()
    logger.info("Iniciando microservicio GasPredict...")

    # Esperar a que la base de datos esté lista
    db.wait_for_db()

    # Ejecutar una primera pasada inmediatamente al arrancar
    run_pipeline()

    # Programar ejecuciones diarias: 07:15 (antes de la ruta matinal) y 18:30 (actualización post-mercados)
    schedule.every().day.at("07:15").do(run_pipeline)
    schedule.every().day.at("18:30").do(run_pipeline)

    logger.info("📅 Tareas programadas a las 07:15 y 18:30 todos los días.")

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    main()
