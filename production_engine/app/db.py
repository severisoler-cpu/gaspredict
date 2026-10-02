import os
import time
import logging
from sqlalchemy import create_engine, text
import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger("gaspredict.db")

def get_db_url():
    user = os.getenv("DB_USER", "gaspredict")
    password = os.getenv("DB_PASSWORD", "gaspredict_secret")
    host = os.getenv("DB_HOST", "timescaledb")
    port = os.getenv("DB_PORT", "5432")
    dbname = os.getenv("DB_NAME", "gaspredict")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE TABLE IF NOT EXISTS gas_stations (
    id_station INT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    municipality VARCHAR(100),
    province VARCHAR(100),
    postal_code VARCHAR(10),
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    brand_type VARCHAR(50) DEFAULT 'INDEPENDENT',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS station_prices (
    time TIMESTAMPTZ NOT NULL,
    id_station INT NOT NULL,
    price_gasoil_a NUMERIC(5, 3),
    price_gasolina_95_e5 NUMERIC(5, 3),
    price_gasolina_98_e5 NUMERIC(5, 3),
    price_gasoil_premium NUMERIC(5, 3),
    CONSTRAINT fk_station FOREIGN KEY (id_station) REFERENCES gas_stations(id_station) ON DELETE CASCADE,
    PRIMARY KEY (time, id_station)
);

CREATE TABLE IF NOT EXISTS macro_indicators (
    time TIMESTAMPTZ NOT NULL PRIMARY KEY,
    brent_usd NUMERIC(8, 3),
    gasoil_ice_usd NUMERIC(8, 3),
    rbob_gasoline_usd NUMERIC(8, 3),
    heating_oil_usd NUMERIC(8, 3),
    eur_usd NUMERIC(8, 4),
    brent_eur NUMERIC(8, 3),
    crack_spread_synthetic NUMERIC(8, 3)
);

CREATE TABLE IF NOT EXISTS price_predictions (
    time TIMESTAMPTZ NOT NULL,
    target_date DATE NOT NULL,
    scope VARCHAR(50) NOT NULL,
    fuel_type VARCHAR(50) NOT NULL,
    predicted_price NUMERIC(5, 3) NOT NULL,
    confidence_low NUMERIC(5, 3),
    confidence_high NUMERIC(5, 3),
    model_version VARCHAR(50) DEFAULT 'v1.0-xgboost',
    PRIMARY KEY (time, target_date, scope, fuel_type)
);

CREATE TABLE IF NOT EXISTS refuel_recommendations (
    time TIMESTAMPTZ NOT NULL,
    scope VARCHAR(50) NOT NULL,
    station_name VARCHAR(255) NOT NULL,
    municipality VARCHAR(100),
    current_price NUMERIC(5, 3) NOT NULL,
    predicted_price_7d NUMERIC(5, 3) NOT NULL,
    expected_change_7d NUMERIC(5, 3) NOT NULL,
    best_day_to_refuel VARCHAR(50) NOT NULL,
    best_date DATE NOT NULL,
    best_price_projected NUMERIC(5, 3) NOT NULL,
    litros_moto_best NUMERIC(5, 2) NOT NULL,
    recommendation_text TEXT NOT NULL,
    PRIMARY KEY (time, scope)
);
"""

HYPERTABLE_SQL = [
    "SELECT create_hypertable('station_prices', 'time', if_not_exists => TRUE);",
    "CREATE INDEX IF NOT EXISTS idx_station_prices_station_time ON station_prices (id_station, time DESC);",
    "SELECT create_hypertable('macro_indicators', 'time', if_not_exists => TRUE);",
    "SELECT create_hypertable('price_predictions', 'time', if_not_exists => TRUE);",
    "SELECT create_hypertable('refuel_recommendations', 'time', if_not_exists => TRUE);"
]

def get_views_sql():
    route_municipalities = os.getenv("USER_ROUTE_MUNICIPALITIES", "Getafe, Leganés, Alcorcón").split(",")
    conditions = " OR ".join([f"gs.municipality ILIKE '%%{m.strip()}%%'" for m in route_municipalities])
    
    return f"""
CREATE OR REPLACE VIEW v_daily_province_averages AS
SELECT
    date_trunc('day', sp.time) AS day,
    gs.province,
    ROUND(AVG(sp.price_gasoil_a), 3) AS avg_gasoil_a,
    ROUND(AVG(sp.price_gasolina_95_e5), 3) AS avg_gasolina_95,
    ROUND(AVG(sp.price_gasolina_98_e5), 3) AS avg_gasolina_98,
    COUNT(DISTINCT sp.id_station) AS active_stations
FROM station_prices sp
JOIN gas_stations gs ON sp.id_station = gs.id_station
GROUP BY date_trunc('day', sp.time), gs.province
ORDER BY day DESC, gs.province;

CREATE OR REPLACE VIEW v_daily_macro_summary AS
SELECT
    date_trunc('day', time) AS day,
    brent_usd,
    brent_eur,
    eur_usd,
    gasoil_ice_usd,
    heating_oil_usd,
    crack_spread_synthetic
FROM macro_indicators
ORDER BY day DESC;

CREATE OR REPLACE VIEW v_my_commute_stations AS
SELECT
    gs.id_station,
    gs.name,
    gs.municipality,
    gs.address,
    gs.brand_type,
    sp.price_gasolina_95_e5,
    sp.price_gasoil_a,
    ROUND(10.0 / NULLIF(sp.price_gasolina_95_e5, 0), 2) AS litros_moto_10eur,
    sp.time
FROM gas_stations gs
JOIN station_prices sp ON gs.id_station = sp.id_station
WHERE (
    {conditions}
)
AND sp.price_gasolina_95_e5 > 0;
"""

def init_schema(engine):
    """Crea las tablas, hypertables de TimescaleDB y vistas si no existen."""
    logger.info("Inicializando esquema de base de datos y hypertables...")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for stmt in SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        for h_stmt in HYPERTABLE_SQL:
            try:
                conn.execute(text(h_stmt))
            except Exception as e:
                logger.warning(f"Aviso al crear hypertable ({h_stmt}): {e}")
                
        views_sql = get_views_sql()
        for v_stmt in views_sql.strip().split(";"):
            v_stmt = v_stmt.strip()
            if v_stmt:
                conn.execute(text(v_stmt))
    logger.info("✅ Esquema de base de datos verificado con éxito.")

def wait_for_db(max_retries=15, retry_interval=3):
    """Espera a que TimescaleDB esté listo y acepte conexiones."""
    db_url = get_db_url()
    logger.info("Verificando conexión con TimescaleDB...")
    for i in range(max_retries):
        try:
            engine = create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("✅ Conexión con TimescaleDB establecida con éxito.")
            init_schema(engine)
            return engine
        except Exception as e:
            logger.warning(f"Intento {i+1}/{max_retries} - Base de datos no disponible todavía ({e}). Reintentando en {retry_interval}s...")
            time.sleep(retry_interval)
    raise ConnectionError("❌ No se pudo conectar a la base de datos tras múltiples intentos.")

def get_engine():
    return create_engine(get_db_url(), pool_size=5, max_overflow=10)

def upsert_gas_stations(engine, stations_data):
    """
    Inserta o actualiza las gasolineras (metadatos fijos/geográficos).
    stations_data: lista de tuplas (id_station, name, address, municipality, province, postal_code, latitude, longitude, brand_type)
    """
    if not stations_data:
        return 0

    sql = """
    INSERT INTO gas_stations (
        id_station, name, address, municipality, province, postal_code, latitude, longitude, brand_type
    ) VALUES %s
    ON CONFLICT (id_station) DO UPDATE SET
        name = EXCLUDED.name,
        address = EXCLUDED.address,
        municipality = EXCLUDED.municipality,
        province = EXCLUDED.province,
        postal_code = EXCLUDED.postal_code,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        brand_type = EXCLUDED.brand_type,
        updated_at = NOW();
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            execute_values(cursor, sql, stations_data)
        raw_conn.commit()
        return len(stations_data)
    finally:
        raw_conn.close()

def upsert_station_prices(engine, prices_data):
    """
    Inserta o actualiza precios de estaciones.
    prices_data: lista de tuplas (time, id_station, price_gasoil_a, price_gasolina_95_e5, price_gasolina_98_e5, price_gasoil_premium)
    """
    if not prices_data:
        return 0

    sql = """
    INSERT INTO station_prices (
        time, id_station, price_gasoil_a, price_gasolina_95_e5, price_gasolina_98_e5, price_gasoil_premium
    ) VALUES %s
    ON CONFLICT (time, id_station) DO UPDATE SET
        price_gasoil_a = EXCLUDED.price_gasoil_a,
        price_gasolina_95_e5 = EXCLUDED.price_gasolina_95_e5,
        price_gasolina_98_e5 = EXCLUDED.price_gasolina_98_e5,
        price_gasoil_premium = EXCLUDED.price_gasoil_premium;
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            execute_values(cursor, sql, prices_data)
        raw_conn.commit()
        return len(prices_data)
    finally:
        raw_conn.close()

def upsert_macro_indicators(engine, macro_data):
    """
    Inserta o actualiza indicadores macroeconómicos diarios.
    macro_data: lista de tuplas (time, brent_usd, gasoil_ice_usd, rbob_gasoline_usd, heating_oil_usd, eur_usd, brent_eur, crack_spread_synthetic)
    """
    if not macro_data:
        return 0

    sql = """
    INSERT INTO macro_indicators (
        time, brent_usd, gasoil_ice_usd, rbob_gasoline_usd, heating_oil_usd, eur_usd, brent_eur, crack_spread_synthetic
    ) VALUES %s
    ON CONFLICT (time) DO UPDATE SET
        brent_usd = EXCLUDED.brent_usd,
        gasoil_ice_usd = EXCLUDED.gasoil_ice_usd,
        rbob_gasoline_usd = EXCLUDED.rbob_gasoline_usd,
        heating_oil_usd = EXCLUDED.heating_oil_usd,
        eur_usd = EXCLUDED.eur_usd,
        brent_eur = EXCLUDED.brent_eur,
        crack_spread_synthetic = EXCLUDED.crack_spread_synthetic;
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            execute_values(cursor, sql, macro_data)
        raw_conn.commit()
        return len(macro_data)
    finally:
        raw_conn.close()

def upsert_predictions(engine, predictions_data):
    """
    Guarda las predicciones calculadas por el modelo.
    predictions_data: lista de tuplas (time, target_date, scope, fuel_type, predicted_price, confidence_low, confidence_high, model_version)
    """
    if not predictions_data:
        return 0

    sql = """
    INSERT INTO price_predictions (
        time, target_date, scope, fuel_type, predicted_price, confidence_low, confidence_high, model_version
    ) VALUES %s
    ON CONFLICT (time, target_date, scope, fuel_type) DO UPDATE SET
        predicted_price = EXCLUDED.predicted_price,
        confidence_low = EXCLUDED.confidence_low,
        confidence_high = EXCLUDED.confidence_high,
        model_version = EXCLUDED.model_version;
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            execute_values(cursor, sql, predictions_data)
        raw_conn.commit()
        return len(predictions_data)
    finally:
        raw_conn.close()

def upsert_refuel_recommendations(engine, recommendations_data):
    """
    Guarda las recomendaciones de repostaje para Plenoil y la gasolinera más barata.
    recommendations_data: lista de tuplas (
        time, scope, station_name, municipality, current_price, predicted_price_7d,
        expected_change_7d, best_day_to_refuel, best_date, best_price_projected,
        litros_moto_best, recommendation_text
    )
    """
    if not recommendations_data:
        return 0

    sql = """
    INSERT INTO refuel_recommendations (
        time, scope, station_name, municipality, current_price, predicted_price_7d,
        expected_change_7d, best_day_to_refuel, best_date, best_price_projected,
        litros_moto_best, recommendation_text
    ) VALUES %s
    ON CONFLICT (time, scope) DO UPDATE SET
        station_name = EXCLUDED.station_name,
        municipality = EXCLUDED.municipality,
        current_price = EXCLUDED.current_price,
        predicted_price_7d = EXCLUDED.predicted_price_7d,
        expected_change_7d = EXCLUDED.expected_change_7d,
        best_day_to_refuel = EXCLUDED.best_day_to_refuel,
        best_date = EXCLUDED.best_date,
        best_price_projected = EXCLUDED.best_price_projected,
        litros_moto_best = EXCLUDED.litros_moto_best,
        recommendation_text = EXCLUDED.recommendation_text;
    """
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            execute_values(cursor, sql, recommendations_data)
        raw_conn.commit()
        return len(recommendations_data)
    finally:
        raw_conn.close()

