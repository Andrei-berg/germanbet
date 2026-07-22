from __future__ import annotations

import requests
from app.config import Config


BASE_URL = "https://api.openweathermap.org/data/2.5"


def _api_enabled() -> bool:
    return bool(Config.WEATHER_API_KEY) and Config.WEATHER_API_KEY != "test"


def get_weather_for_city(
    city: str, country_code: str = ""
) -> dict | None:
    if not _api_enabled():
        return None

    query = f"{city},{country_code}" if country_code else city

    try:
        r = requests.get(
            f"{BASE_URL}/weather",
            params={
                "q": query,
                "appid": Config.WEATHER_API_KEY,
                "units": "metric",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "temp": data.get("main", {}).get("temp"),
            "feels_like": data.get("main", {}).get("feels_like"),
            "humidity": data.get("main", {}).get("humidity"),
            "pressure": data.get("main", {}).get("pressure"),
            "wind_speed": data.get("wind", {}).get("speed"),
            "condition": data.get("weather", [{}])[0].get("description"),
            "icon": data.get("weather", [{}])[0].get("icon"),
        }
    except requests.RequestException:
        return None


def weather_adjustment(weather: dict | None) -> float:
    if weather is None:
        return 1.0

    factor = 1.0
    wind = weather.get("wind_speed", 0) or 0
    condition = (weather.get("condition") or "").lower()

    if wind > 10:
        factor -= 0.05
    if wind > 20:
        factor -= 0.05
    if "rain" in condition or "storm" in condition:
        factor -= 0.05

    return factor
