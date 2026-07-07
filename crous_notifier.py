from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://trouverunlogement.lescrous.fr"
CET = timezone(timedelta(hours=1), "CET")
DEFAULT_TIMEOUT_SECONDS = 20
RESULTS_PER_PAGE = 24
DEFAULT_DAILY_REPORT_TIME_WINDOW = {"start": "23:30", "end": "00:00"}
SENDER_NAME = "CROUS BOT Notifier"

BREVO_LOGIN = os.environ.get("BREVO_LOGIN")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

CURRENT_AVAILABLE_FILE = "current_available.csv"
CHANGE_LOG_FILE = "availability_changes.csv"
UNIQUE_HISTORY_FILE = "unique_residences.csv"
DAILY_REPORT_LOG_FILE = "daily_report_log.csv"
LEGACY_AVAILABLE_FILE = "available_residences.csv"
DEFAULT_CONFIG_FILE = "crous_targets.json"

CSV_HEADERS = [
    "residence_id", "name", "housing_type", "price_text", "price_min_eur",
    "price_max_eur", "surface_text", "surface_min_m2", "surface_max_m2",
    "details", "address", "link", "source_url", "first_seen_cet", "last_seen_cet",
]
CHANGE_HEADERS = ["timestamp_cet", "event", *CSV_HEADERS]
UNIQUE_HEADERS = [*CSV_HEADERS, "last_event", "removed_at_cet", "seen_count"]
DAILY_REPORT_HEADERS = ["sent_date", "sent_time_cet"]


@dataclass(frozen=True)
class RecipientTarget:
    name: str
    email: str
    urls: list[str]
    data_dir: Path
    cities: list[str] = field(default_factory=list)
    send_immediate_alert: bool = True
    send_daily_report: bool = False
    daily_report_time_window: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_DAILY_REPORT_TIME_WINDOW))


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    return re.sub(r"[-\s]+", "_", value).strip("_") or "target"


def now_cet() -> datetime:
    return datetime.now(CET)


def parse_report_time(value: str, fallback: str) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not match:
        return parse_report_time(fallback, "23:30") if value != fallback else 23 * 60 + 30
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return parse_report_time(fallback, "23:30") if value != fallback else 23 * 60 + 30
    return hour * 60 + minute


def daily_report_window_minutes(window: dict[str, str] | list[str] | tuple[str, str] | str | None = None) -> tuple[int, int]:
    if isinstance(window, dict):
        start = window.get("start", DEFAULT_DAILY_REPORT_TIME_WINDOW["start"])
        end = window.get("end", DEFAULT_DAILY_REPORT_TIME_WINDOW["end"])
    elif isinstance(window, (list, tuple)) and len(window) >= 2:
        start, end = str(window[0]), str(window[1])
    elif isinstance(window, str):
        parts = [part.strip() for part in window.split(",")]
        if len(parts) == 2:
            start, end = parts
        else:
            start = DEFAULT_DAILY_REPORT_TIME_WINDOW["start"]
            end = DEFAULT_DAILY_REPORT_TIME_WINDOW["end"]
    else:
        start = DEFAULT_DAILY_REPORT_TIME_WINDOW["start"]
        end = DEFAULT_DAILY_REPORT_TIME_WINDOW["end"]
    return (
        parse_report_time(start, DEFAULT_DAILY_REPORT_TIME_WINDOW["start"]),
        parse_report_time(end, DEFAULT_DAILY_REPORT_TIME_WINDOW["end"]),
    )


def is_within_daily_report_window(timestamp_dt: datetime, window: dict[str, str] | list[str] | tuple[str, str] | str | None = None) -> bool:
    start_minute, end_minute = daily_report_window_minutes(window)
    current_minute = timestamp_dt.hour * 60 + timestamp_dt.minute
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def redact_address(value: str) -> str:
    value = normalize_space(value)
    if "@" not in value:
        return "***"
    local, domain = value.split("@", 1)
    local = f"{local[:2]}***" if len(local) > 2 else f"{local[:1]}***"
    if "." in domain:
        domain_name, tld = domain.rsplit(".", 1)
        domain = f"{domain_name[:1]}***.{tld}"
    else:
        domain = f"{domain[:1]}***"
    return f"{local}@{domain}"


def extract_numbers(text: str) -> list[float]:
    values = []
    for raw in re.findall(r"(?<![A-Za-z])\d+(?:[\s.]\d{3})*(?:[,.]\d+)?", text or ""):
        try:
            values.append(float(raw.replace(" ", "").replace(".", "").replace(",", ".")))
        except ValueError:
            pass
    return values


def format_float(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def parse_range(text: str, unit_patterns: Iterable[str]) -> tuple[str, str]:
    if not re.search("|".join(unit_patterns), text or "", flags=re.IGNORECASE):
        return "", ""
    numbers = extract_numbers(text)
    if not numbers:
        return "", ""
    if len(numbers) == 1:
        return format_float(numbers[0]), ""
    return format_float(min(numbers)), format_float(max(numbers))


def parse_price(price_text: str) -> tuple[str, str]:
    return parse_range(price_text, [r"€", r"eur", r"euro"])


def parse_surface(details: str) -> tuple[str, str, str]:
    surface_parts = [
        part for part in re.split(r"\s*\|\s*", details or "")
        if re.search(r"m\s*(?:²|2)\b|㎡", part, flags=re.IGNORECASE)
    ]
    surface_text = " | ".join(surface_parts)
    min_m2, max_m2 = parse_range(surface_text, [r"m\s*(?:²|2)\b", r"㎡"])
    return surface_text, min_m2, max_m2


def parse_housing_type(name: str, details: str) -> str:
    candidates = [*re.split(r"\s*\|\s*", details or ""), name]
    labels = [
        ("T1 bis", r"\bt1\s*bis\b"),
        ("T1", r"\bt1\b"),
        ("T2", r"\bt2\b"),
        ("T3", r"\bt3\b"),
        ("Studio", r"\bstudio\b"),
        ("Chambre", r"\bchambre\b"),
        ("Colocation", r"\bcolocation\b"),
        ("Individuel", r"\bindividuel\b"),
        ("Couple", r"\bcouple\b"),
    ]
    for candidate in candidates:
        matches = []
        for label, pattern in labels:
            if match := re.search(pattern, candidate, flags=re.IGNORECASE):
                matches.append((match.start(), label))
        if matches:
            ordered = []
            for _, label in sorted(matches):
                if label not in ordered:
                    ordered.append(label)
            return ", ".join(ordered)
    return ""


def residence_id(name: str, address: str, housing_type: str, price_text: str, surface_text: str, link: str) -> str:
    stable_link = re.sub(r"[?#].*$", "", link or "").rstrip("/")
    fingerprint = "|".join(normalize_space(part).lower() for part in [stable_link, name, address, housing_type, price_text, surface_text])
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def set_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_card_details(card) -> str:
    return " | ".join(
        text for item in card.select(".fr-card__detail")
        if (text := normalize_space(item.get_text(" ", strip=True)))
    )


def card_to_residence(card, source_url: str, timestamp: str) -> dict[str, str] | None:
    title = card.find("h3", class_="fr-card__title")
    link_element = title.find("a") if title else None
    if not link_element:
        return None

    name = normalize_space(link_element.get_text(" ", strip=True))
    link = urljoin(BASE_URL, link_element.get("href", ""))

    price_element = card.select_one(".fr-badge")
    price_text = normalize_space(price_element.get_text(" ", strip=True)) if price_element else ""

    address_element = card.select_one(".fr-card__desc")
    address = normalize_space(address_element.get_text(" ", strip=True)) if address_element else ""

    details = extract_card_details(card)
    price_min, price_max = parse_price(price_text)
    surface_text, surface_min, surface_max = parse_surface(details)
    housing_type = parse_housing_type(name, details)

    return {
        "residence_id": residence_id(name, address, housing_type, price_text, surface_text, link),
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


def scrape_crous_page(url: str, timestamp: str) -> list[dict[str, str]] | None:
    residences: list[dict[str, str]] = []
    session = requests.Session()
    try:
        response = session.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")

        total_pages = 1
        header = soup.find("h2", class_="SearchResults-desktop")
        if header and (match := re.search(r"(\d+)\s+logement", header.get_text(" ", strip=True))):
            total_pages = max(1, math.ceil(int(match.group(1)) / RESULTS_PER_PAGE))

        print(f"{url}: scraping {total_pages} page(s)")
        for page_num in range(1, total_pages + 1):
            page_url = set_query_param(url, "page", str(page_num)) if page_num > 1 else url
            page_soup = soup
            if page_num > 1:
                page_response = session.get(page_url, headers=HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
                page_response.raise_for_status()
                page_soup = BeautifulSoup(page_response.content, "html.parser")
            for card in page_soup.select(".fr-card"):
                if residence := card_to_residence(card, page_url, timestamp):
                    residences.append(residence)
        return residences
    except requests.RequestException as exc:
        print(f"Scrape failed for {url}: {exc}")
        return None


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def sort_key(residence: dict[str, str]) -> tuple[float, str]:
    try:
        price = float(residence.get("price_min_eur") or 999999)
    except ValueError:
        price = 999999
    return price, residence.get("name", "")


def merge_duplicates(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for row in rows:
        rid = row["residence_id"]
        if rid not in merged:
            merged[rid] = row
            continue
        sources = set(filter(None, merged[rid].get("source_url", "").split(" | ")))
        sources.add(row.get("source_url", ""))
        merged[rid]["source_url"] = " | ".join(sorted(sources))
    return sorted(merged.values(), key=sort_key)


def migrate_previous_snapshot(data_dir: Path) -> None:
    current = data_dir / CURRENT_AVAILABLE_FILE
    legacy = data_dir / LEGACY_AVAILABLE_FILE
    if current.exists() or not legacy.exists():
        return
    timestamp = now_cet().isoformat(timespec="seconds")
    migrated = []
    for row in read_csv(legacy):
        price_min, price_max = parse_price(row.get("price", ""))
        surface_text, surface_min, surface_max = parse_surface(row.get("details", ""))
        housing_type = parse_housing_type(row.get("name", ""), row.get("details", ""))
        migrated.append({
            "residence_id": residence_id(row.get("name", ""), row.get("address", ""), housing_type, row.get("price", ""), surface_text, row.get("link", "")),
            "name": row.get("name", ""),
            "housing_type": housing_type,
            "price_text": row.get("price", ""),
            "price_min_eur": price_min,
            "price_max_eur": price_max,
            "surface_text": surface_text,
            "surface_min_m2": surface_min,
            "surface_max_m2": surface_max,
            "details": row.get("details", ""),
            "address": row.get("address", ""),
            "link": row.get("link", ""),
            "source_url": "legacy_csv",
            "first_seen_cet": timestamp,
            "last_seen_cet": timestamp,
        })
    write_csv(current, migrated, CSV_HEADERS)


def listing_details_line(residence: dict[str, str]) -> str:
    details = normalize_space(residence.get("details", ""))
    if details:
        return details
    parts = [
        normalize_space(residence.get("surface_text", "")),
        normalize_space(residence.get("housing_type", "")),
    ]
    return " | ".join(part for part in parts if part)


def format_residence_html(residence: dict[str, str], color: str = "#111") -> str:
    price = residence.get("price_text") or "Prix non indique"
    detail_line = listing_details_line(residence)
    details_html = (
        f"<p style='margin:3px 0;color:#333;font-size:14px'>{html.escape(detail_line)}</p>"
        if detail_line else ""
    )
    return f"""
    <div style="border-left:3px solid {color};padding-left:14px;margin:0 0 18px 0">
      <p style="margin:0;font-size:17px;font-weight:700"><a href="{html.escape(residence['link'])}" style="color:{color};text-decoration:none">{html.escape(residence['name'])}</a></p>
      <p style="margin:3px 0;color:#333"><b>{html.escape(price)}</b></p>
      {details_html}
      <p style="margin:3px 0;color:#555">{html.escape(residence.get('address', ''))}</p>
    </div>
    """


def create_email_body(target: RecipientTarget, added: list[dict[str, str]], removed: list[dict[str, str]], current: list[dict[str, str]]) -> str:
    body = f"<html><body style='font-family:Arial,sans-serif;color:#222'><div style='max-width:760px;margin:auto'><h1>CROUS - {html.escape(target.name)}</h1>"
    if added:
        body += f"<h2 style='color:#198754'>Nouveaux logements ({len(added)})</h2>"
        body += "".join(format_residence_html(row, "#198754") for row in added)
    if removed:
        body += f"<h2 style='color:#dc3545'>Logements disparus ({len(removed)})</h2>"
        body += "".join(format_residence_html(row, "#dc3545") for row in removed)
    body += f"<h2>Disponibles maintenant ({len(current)})</h2>"
    body += "".join(format_residence_html(row) for row in current) if current else "<p>Aucun logement disponible.</p>"
    return body + "</div></body></html>"


def create_daily_report_body(target: RecipientTarget, current: list[dict[str, str]], timestamp: str) -> str:
    body = (
        "<html><body style='font-family:Arial,sans-serif;color:#222'>"
        "<div style='max-width:760px;margin:auto'>"
        f"<h1>CROUS - Rapport quotidien - {html.escape(target.name)}</h1>"
        f"<p style='color:#555'>Etat au {html.escape(timestamp)}.</p>"
        f"<h2>Disponibles maintenant ({len(current)})</h2>"
    )
    body += "".join(format_residence_html(row) for row in current) if current else "<p>Aucun logement disponible.</p>"
    return body + "</div></body></html>"


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not all([BREVO_LOGIN, BREVO_API_KEY, FROM_EMAIL, to_email]):
        print("Email credentials or recipient missing; skipping email.")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=DEFAULT_TIMEOUT_SECONDS) as server:
        server.starttls()
        server.login(BREVO_LOGIN, BREVO_API_KEY)
        server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
    print(f"Sent email to {redact_address(to_email)}: {subject}")
    return True


def update_unique_history(data_dir: Path, current: list[dict[str, str]], added: list[dict[str, str]], removed: list[dict[str, str]], timestamp: str) -> None:
    history_path = data_dir / UNIQUE_HISTORY_FILE
    history = {row["residence_id"]: row for row in read_csv(history_path)}
    added_ids = {row["residence_id"] for row in added}
    removed_ids = {row["residence_id"] for row in removed}
    for row in current:
        previous = history.get(row["residence_id"], {})
        merged = {**row}
        merged["first_seen_cet"] = previous.get("first_seen_cet") or row["first_seen_cet"]
        merged["last_seen_cet"] = timestamp
        merged["last_event"] = "added" if row["residence_id"] in added_ids else previous.get("last_event", "seen")
        merged["removed_at_cet"] = ""
        merged["seen_count"] = str(int(previous.get("seen_count") or 0) + 1)
        history[row["residence_id"]] = merged
    for row in removed:
        previous = history.get(row["residence_id"], row)
        previous.update(row)
        previous["last_event"] = "removed"
        previous["removed_at_cet"] = timestamp
        previous["seen_count"] = previous.get("seen_count") or "1"
        history[row["residence_id"]] = previous
    write_csv(history_path, sorted(history.values(), key=sort_key), UNIQUE_HEADERS)


def daily_report_already_sent(data_dir: Path, date_cet: str) -> bool:
    for row in read_csv(data_dir / DAILY_REPORT_LOG_FILE):
        if row.get("sent_date") == date_cet:
            return True
    return False


def maybe_send_daily_report(target: RecipientTarget, current: list[dict[str, str]], timestamp_dt: datetime, timestamp: str) -> None:
    if not target.send_daily_report or not is_within_daily_report_window(timestamp_dt, target.daily_report_time_window):
        return

    date_cet = timestamp_dt.date().isoformat()
    if daily_report_already_sent(target.data_dir, date_cet):
        return

    subject = f"CROUS {target.name}: rapport quotidien ({len(current)} logements)"
    try:
        sent = send_email(target.email, subject, create_daily_report_body(target, current, timestamp))
    except Exception as exc:
        print(f"Failed to send daily report to {redact_address(target.email)}: {exc}")
        return

    if sent:
        append_csv(target.data_dir / DAILY_REPORT_LOG_FILE, [{
            "sent_date": date_cet,
            "sent_time_cet": timestamp_dt.time().isoformat(timespec="seconds"),
        }], DAILY_REPORT_HEADERS)


def process_target(target: RecipientTarget) -> None:
    timestamp_dt = now_cet()
    timestamp = timestamp_dt.isoformat(timespec="seconds")
    target.data_dir.mkdir(parents=True, exist_ok=True)
    migrate_previous_snapshot(target.data_dir)
    snapshot_path = target.data_dir / CURRENT_AVAILABLE_FILE
    previous = {row["residence_id"]: row for row in read_csv(snapshot_path)}

    scraped: list[dict[str, str]] = []
    failed_urls = []
    for url in target.urls:
        rows = scrape_crous_page(url, timestamp)
        if rows is None:
            failed_urls.append(url)
        else:
            scraped.extend(rows)

    if failed_urls and not scraped:
        print(f"{target.name}: all scrapes failed: {'; '.join(failed_urls)}")
        return

    current = merge_duplicates(scraped)
    for row in current:
        if row["residence_id"] in previous:
            row["first_seen_cet"] = previous[row["residence_id"]].get("first_seen_cet") or row["first_seen_cet"]

    current_by_id = {row["residence_id"]: row for row in current}
    added = sorted([row for rid, row in current_by_id.items() if rid not in previous], key=sort_key)
    removed = sorted([row for rid, row in previous.items() if rid not in current_by_id], key=sort_key)

    change_rows = [{"timestamp_cet": timestamp, "event": "added", **row} for row in added]
    change_rows.extend({"timestamp_cet": timestamp, "event": "removed", **row} for row in removed)
    append_csv(target.data_dir / CHANGE_LOG_FILE, change_rows, CHANGE_HEADERS)
    update_unique_history(target.data_dir, current, added, removed, timestamp)
    write_csv(snapshot_path, current, CSV_HEADERS)

    if (added or removed) and target.send_immediate_alert:
        subject = f"CROUS {target.name}: +{len(added)} / -{len(removed)} logements"
        try:
            send_email(target.email, subject, create_email_body(target, added, removed, current))
        except Exception as exc:
            print(f"Failed to send email to {redact_address(target.email)}: {exc}")

    maybe_send_daily_report(target, current, timestamp_dt, timestamp)
    if failed_urls:
        print(f"{target.name}: partial scrape failures: {'; '.join(failed_urls)}")
    print(f"{target.name}: {len(current)} current, +{len(added)}, -{len(removed)}")


def load_targets(config_path: Path | None = None) -> list[RecipientTarget]:
    path = config_path or Path(os.environ.get("TARGETS_CONFIG_PATH", DEFAULT_CONFIG_FILE))
    if not path.exists():
        raise FileNotFoundError(f"Missing target config file: {path}. Copy/edit crous_targets.json or set TARGETS_CONFIG_PATH.")

    with path.open(encoding="utf-8") as handle:
        configs = json.load(handle)

    targets = []
    for config in configs:
        name = config["name"]
        email = config.get("email", "")
        email_env = config.get("email_env")
        if email_env:
            email = os.environ.get(email_env, email)
        targets.append(RecipientTarget(
            name=name,
            email=email,
            urls=config.get("urls") or [config["url"]],
            data_dir=Path(config.get("data_dir") or f"data/{slugify(name)}"),
            cities=config.get("cities", []),
            send_immediate_alert=bool(config.get("send_immediate_alert", True)),
            send_daily_report=bool(config.get("send_daily_report", False)),
            daily_report_time_window=config.get("daily_report_time_window", DEFAULT_DAILY_REPORT_TIME_WINDOW),
        ))
    return targets


def main() -> None:
    for target in load_targets():
        if not target.email:
            print(f"Skipping {target.name}: no recipient email configured.")
            continue
        process_target(target)


if __name__ == "__main__":
    main()
