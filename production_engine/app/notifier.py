import os
import logging
import requests

logger = logging.getLogger("gaspredict.notifier")

def send_telegram_alert(message: str) -> bool:
    """
    Envía una notificación al chat de Telegram configurado en variables de entorno.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.info("Notificaciones de Telegram desactivadas (TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados).")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Notificación enviada a Telegram con éxito.")
        return True
    except Exception as e:
        logger.error(f"Error al enviar notificación a Telegram: {e}")
        return False

def format_daily_summary(
    date_str: str,
    avg_valencia: dict,
    cheapest_station: dict,
    plenoil_station: dict = None,
    cheapest_commute: dict = None,
    prediction_summary: dict = None
) -> str:
    """
    Construye un mensaje profesional para el informe diario / alerta.
    Compara la gasolinera habitual vs la gasolinera más barata de la ruta.
    """
    route_name = os.getenv("USER_ROUTE_MUNICIPALITIES", "Ruta Habitual")
    
    msg = f"⛽ *GasPredict — Monitor de Carburantes*\n"
    msg += f"📅 *Fecha:* `{date_str}`\n\n"

    if plenoil_station and cheapest_commute:
        fav_name = plenoil_station.get("name", "Gasolinera Habitual").title()
        fav_muni = plenoil_station.get("municipality", "").title()
        
        p_plenoil = float(plenoil_station.get("price_gasolina_95_e5") or 0.0)
        l_plenoil = float(plenoil_station.get("litros_moto_10eur") or (10.0 / p_plenoil if p_plenoil > 0 else 0.0))

        p_cheap = float(cheapest_commute.get("price_gasolina_95_e5") or 0.0)
        l_cheap = float(cheapest_commute.get("litros_moto_10eur") or (10.0 / p_cheap if p_cheap > 0 else 0.0))

        msg += f"📍 *Tu Ruta ({route_name}) — Ref. 10 €:*\n"
        msg += f"⭐ *Habitual ({fav_name} {fav_muni}):* `{p_plenoil:.3f} €/L` (`{l_plenoil:.2f} L` con 10 €)\n"

        if p_cheap > 0 and p_cheap < p_plenoil:
            euro_savings = round(10.0 - (10.0 * (p_cheap / p_plenoil)), 2)
            extra_l = round(l_cheap - l_plenoil, 2)
            msg += f"🏆 *Más barata hoy:* *{cheapest_commute.get('name', 'N/D')}* ({cheapest_commute.get('municipality', '')}) a `{p_cheap:.3f} €/L`\n"
            msg += f"💰 *Ahorro con 10 €:* `+{euro_savings:.2f} €` *(+{extra_l:.2f} L extra de gasolina)*\n\n"
        else:
            msg += "🏆 *¡Tu gasolinera habitual es hoy la más barata de toda la ruta!*\n\n"
    elif cheapest_commute:
        p_cheap = float(cheapest_commute.get("price_gasolina_95_e5") or 0.0)
        l_cheap = float(cheapest_commute.get("litros_moto_10eur") or 0.0)
        msg += f"📍 *Más barata en tu ruta:* *{cheapest_commute.get('name', 'N/D')}* a `{p_cheap:.3f} €/L` (`{l_cheap:.2f} L`)\n\n"

    msg += "📊 *Medias Regionales:*\n"
    if "avg_gasoil_a" in avg_valencia and avg_valencia["avg_gasoil_a"]:
        msg += f"• Diésel (Gasóleo A): `{avg_valencia['avg_gasoil_a']:.3f} €/L`\n"
    if "avg_gasolina_95" in avg_valencia and avg_valencia["avg_gasolina_95"]:
        msg += f"• Gasolina 95 E5: `{avg_valencia['avg_gasolina_95']:.3f} €/L`\n"

    # Recomendación detallada por estación
    stations_recs = prediction_summary.get("stations", {}) if prediction_summary else {}
    plenoil_rec = stations_recs.get("FAVORITE_STATION")
    cheapest_rec = stations_recs.get("CHEAPEST_COMMUTE")

    if plenoil_rec or cheapest_rec:
        msg += "\n🎯 *¿CUÁNDO REPOSTAR?*\n"
        if plenoil_rec:
            msg += f"⭐ *Habitual ({plenoil_rec['name']} {plenoil_rec['municipality']}):*\n"
            msg += f"   • Mejor día: *{plenoil_rec['best_day']}*\n"
            msg += f"   • Previsión 7 días: `{plenoil_rec['change_7d']:+.3f} €/L` (Proyectado: `{plenoil_rec['predicted_price_7d']:.3f} €/L`)\n"
            msg += f"   • Consejo: _{plenoil_rec['recommendation_text']}_\n"
        if cheapest_rec:
            msg += f"🏆 *{cheapest_rec['name']} ({cheapest_rec['municipality']}) — Más barata hoy:*\n"
            msg += f"   • Mejor día: *{cheapest_rec['best_day']}*\n"
            msg += f"   • Previsión 7 días: `{cheapest_rec['change_7d']:+.3f} €/L` (Proyectado: `{cheapest_rec['predicted_price_7d']:.3f} €/L`)\n"
            msg += f"   • Consejo: _{cheapest_rec['recommendation_text']}_\n"
    elif prediction_summary:
        trend = prediction_summary.get("trend", "ESTABLE")
        change = prediction_summary.get("change", 0.0)
        days = prediction_summary.get("horizon_days", 7)

        if trend == "SUBIDA":
            msg += f"\n📈 *ALERTA PREDICTIVA ({trend}):*\n"
            msg += f"Se proyecta un incremento de aprox. `+{change:.2f} €/L` en los próximos {days} días.\n"
            msg += "💡 *Recomendación:* Es aconsejable *repostar antes del fin de semana*."
        elif trend == "BAJADA":
            msg += f"\n📉 *TENDENCIA PREDICTIVA ({trend}):*\n"
            msg += f"Se proyecta una bajada de aprox. `-{abs(change):.2f} €/L` en {days} días.\n"
            msg += "💡 *Recomendación:* Si puedes esperar, pospón el llenado del depósito."
        else:
            msg += f"\n⚖️ *PREDICCIÓN REGIONAL:* Precios estables para los próximos {days} días."

    return msg
