from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
HTML_ATTEMPTS = 3
API_ATTEMPTS = 3
API_PAGE_SIZE = 100
MAX_API_PAGES = 10

_OCCUPATION_LABELS = {
    "alone": "Individuel",
    "house_sharing": "Colocation",
    "couple": "Couple",
}


def install(notifier: Any) -> None:
    """Wrap only the HTML acquisition function; keep notifier state/change logic intact."""
    original_scraper = notifier.scrape_crous_page

    def scrape_with_fallback(
        url: str,
        timestamp: str,
        session: requests.Session,
    ) -> list[dict[str, str]] | None:
        for attempt in range(1, HTML_ATTEMPTS + 1):
            rows = original_scraper(url, timestamp, session)
            if rows is not None:
                if attempt > 1:
                    print(f"HTML search recovered for {url} on attempt {attempt}/{HTML_ATTEMPTS}.")
                return rows
            if attempt < HTML_ATTEMPTS:
                delay = 2 ** (attempt - 1)
                print(f"HTML search attempt {attempt}/{HTML_ATTEMPTS} failed for {url}; retrying in {delay}s.")
                time.sleep(delay)

        print(f"HTML search unavailable after {HTML_ATTEMPTS} attempts for {url}; trying CROUS JSON API fallback.")
        rows = _scrape_api(url, timestamp, session, notifier)
        if rows is None:
            print(f"CROUS JSON API fallback also failed for {url}.")
        else:
            print(f"CROUS JSON API fallback succeeded for {url}: {len(rows)} result(s).")
        return rows

    notifier.scrape_crous_page = scrape_with_fallback


def _scrape_api(
    source_url: str,
    timestamp: str,
    session: requests.Session,
    notifier: Any,
) -> list[dict[str, str]] | None:
    location = _location_from_search_url(source_url)
    if location is None:
        print(f"API fallback cannot derive bounds from {source_url}.")
        return None

    api_url = f"{notifier.BASE_URL}/api/fr/search/47"
    all_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 1

    while page <= MAX_API_PAGES:
        payload = {
            "precision": 5,
            "need_aggregation": page == 1,
            "page": page,
            "pageSize": API_PAGE_SIZE,
            "sector": None,
            "idTool": 47,
            "occupationModes": [],
            "equipment": [],
            "price": {"min": 0, "max": None},
            "location": location,
        }

        result = _post_search_page(session, api_url, payload, notifier.DEFAULT_TIMEOUT_SECONDS)
        if result is None:
            return None

        results = result.get("results") if isinstance(result, dict) else None
        if not isinstance(results, dict):
            print("CROUS JSON API fallback returned no results object.")
            return None
        items = results.get("items")
        if not isinstance(items, list):
            print("CROUS JSON API fallback returned an invalid items collection.")
            return None

        new_count = 0
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("id", ""))
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            all_items.append(raw_item)
            new_count += 1

        try:
            total = int(results.get("total") or len(all_items))
        except (TypeError, ValueError):
            total = len(all_items)

        if len(all_items) >= total or not items or new_count == 0:
            break
        page += 1

    rows = []
    for item in all_items:
        if item.get("available") is False:
            continue
        row = _api_item_to_residence(item, source_url, timestamp, notifier)
        if row is not None:
            rows.append(row)
    return rows


def _post_search_page(
    session: requests.Session,
    api_url: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any] | None:
    delay = 1
    for attempt in range(1, API_ATTEMPTS + 1):
        try:
            response = session.post(
                api_url,
                json=payload,
                headers={
                    "Accept": "application/ld+json, application/json",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )
            if response.status_code in TRANSIENT_STATUS_CODES and attempt < API_ATTEMPTS:
                print(f"CROUS JSON API returned HTTP {response.status_code}; retrying ({attempt}/{API_ATTEMPTS}).")
                time.sleep(delay)
                delay *= 2
                continue
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt < API_ATTEMPTS:
                print(f"CROUS JSON API transient {type(exc).__name__}; retrying ({attempt}/{API_ATTEMPTS}).")
                time.sleep(delay)
                delay *= 2
                continue
            print(f"CROUS JSON API request failed after {API_ATTEMPTS} attempts: {type(exc).__name__}.")
            return None
        except (requests.RequestException, ValueError) as exc:
            print(f"CROUS JSON API request failed: {type(exc).__name__}.")
            return None
    return None


def _location_from_search_url(url: str) -> list[dict[str, float]] | None:
    query = parse_qs(urlparse(url).query)
    raw_bounds = (query.get("bounds") or [""])[0]
    try:
        west, north, east, south = (float(value) for value in raw_bounds.split("_"))
    except (TypeError, ValueError):
        return None
    if not (west < east and south < north):
        return None
    return [
        {"lon": west, "lat": north},
        {"lon": east, "lat": south},
    ]


def _api_item_to_residence(
    item: dict[str, Any],
    source_url: str,
    timestamp: str,
    notifier: Any,
) -> dict[str, str] | None:
    accommodation_id = item.get("id")
    if not isinstance(accommodation_id, int) or accommodation_id <= 0:
        return None

    residence = item.get("residence") if isinstance(item.get("residence"), dict) else {}
    name = notifier.normalize_space(str(residence.get("label") or item.get("label") or ""))
    if not name:
        return None
    address = notifier.normalize_space(str(residence.get("address") or ""))
    link = f"{notifier.BASE_URL}/tools/47/accommodations/{accommodation_id}"

    occupation_modes = item.get("occupationModes") if isinstance(item.get("occupationModes"), list) else []
    housing_labels: list[str] = []
    rent_values: list[int] = []
    for mode in occupation_modes:
        if not isinstance(mode, dict):
            continue
        mode_type = str(mode.get("type") or "")
        label = _OCCUPATION_LABELS.get(mode_type)
        if label and label not in housing_labels:
            housing_labels.append(label)
        rent = mode.get("rent") if isinstance(mode.get("rent"), dict) else {}
        for key in ("min", "max"):
            value = rent.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                rent_values.append(int(value))

    housing_type = ", ".join(housing_labels)
    price_text = _format_euro_range(rent_values)
    price_min, price_max = notifier.parse_price(price_text)

    area = item.get("area") if isinstance(item.get("area"), dict) else {}
    surface_text = _format_area_range(area.get("min"), area.get("max"))
    surface_min, surface_max = notifier.parse_range(surface_text, [r"m\s*(?:²|2)\b", r"㎡"])

    details_parts = [part for part in (surface_text, housing_type) if part]
    equipment_labels: list[str] = []
    equipments = item.get("equipments") if isinstance(item.get("equipments"), list) else []
    for equipment in equipments:
        if not isinstance(equipment, dict):
            continue
        label = notifier.normalize_space(str(equipment.get("label") or ""))
        if label and label not in equipment_labels:
            equipment_labels.append(label)
    details_parts.extend(equipment_labels)
    details = " | ".join(details_parts)

    return {
        "residence_id": notifier.residence_id(name, address, housing_type, price_text, surface_text, link),
        "name": name,
        "housing_type": housing_type,
        "price_text": price_text,
        "price_min_eur": price_min,
        "price_max_eur": price_max,
        "surface_text": surface_text,
        "surface_min_m2": surface_min,
        "surface_max_m2": surface_max,
        "details": details,
        "address": address,
        "link": link,
        "source_url": source_url,
        "first_seen_cet": timestamp,
        "last_seen_cet": timestamp,
    }


def _format_euro_range(values_cents: list[int]) -> str:
    if not values_cents:
        return ""
    minimum = min(values_cents)
    maximum = max(values_cents)
    minimum_text = _format_decimal(minimum / 100)
    maximum_text = _format_decimal(maximum / 100)
    if minimum == maximum:
        return f"{minimum_text} €"
    return f"de {minimum_text} à {maximum_text} €"


def _format_area_range(minimum: Any, maximum: Any) -> str:
    numbers = [value for value in (minimum, maximum) if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numbers:
        return ""
    low = min(numbers)
    high = max(numbers)
    low_text = _format_decimal(low)
    high_text = _format_decimal(high)
    if math.isclose(float(low), float(high)):
        return f"{low_text} m²"
    return f"de {low_text} à {high_text} m²"


def _format_decimal(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")
