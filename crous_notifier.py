"""CROUS housing notifier.

Scrapes one or more CROUS search URLs, stores clean CSV snapshots/history, and
sends per-recipient alerts for their own target cities/searches.
"""

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
SENDER_NAME = "CROUS BOT Notifier"

BREVO_LOGIN = os.environ.get("BREVO_LOGIN")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

CURRENT_AVAILABLE_FILE = "current_available.csv"
CHANGE_LOG_FILE = "availability_changes.csv"
UNIQUE_HISTORY_FILE = "unique_residences.csv"
RUN_LOG_FILE = "run_log.csv"
REPORT_LOG_FILE = "daily_report_log.csv"
LEGACY_AVAILABLE_FILE = "available_residences.csv"
DEFAULT_CONFIG_FILE = "crous_targets.json"

CSV_HEADERS = [
    "residence_id",
    "name",
    "housing_type",
    "price_text",
    "price_min_eur",
    "price_max_eur",
    "surface_text",
    "surface_min_m2",
    "surface_max_m2",
    "address",
    "details",
    "link",
    "source_url",
    "first_seen_cet",
    "last_seen_cet",
]

CHANGE_HEADERS = ["timestamp_cet", "event", *CSV_HEADERS]
UNIQUE_HEADERS = [*CSV_HEADERS, "last_event", "removed_at_cet", "seen_count"]
RUN_LOG_HEADERS = [
    "timestamp_cet",
    "target_name",
    "recipient_email",
    "scraped_count",
    "current_count",
    "added_count",
    "removed_count",
    "status",
    "message",
]


@dataclass(frozen=True)
class RecipientTarget:
    name: str
    email: str
    urls: list[str]
    data_dir: Path
    cities: list[str] = field(default_factory=list)
    send_immediate_alert: bool = True
    send_daily_report: bool = False


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    return re.sub(r"[-\s]+", "_", value).strip("_") or "target"


def now_cet() -> datetime:
    return datetime.now(CET)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_numbers(text: str) -> list[float]:
    values = []
    for raw in re.findall(r"(?<![A-Za-z])\d+(?:[\s.]\d{3})*(?:[,.]\d+)?", text or ""):
        normalized = raw.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            values.append(float(normalized))
        except ValueError:
            continue
    return values


def format_float(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def parse_range(text: str, unit_patterns: Iterable[str]) -> tuple[str, str]:
    unit_regex = "|".join(unit_patterns)
    if not re.search(unit_regex, text or "", flags=re.IGNORECASE):
        return "", ""
    numbers = extract_numbers(text)
    if not numbers:
        return "", ""
    return format_float(min(numbers)), format_float(max(numbers))


def parse_price(price_text: str) -> tuple[str, str]:
    return parse_range(price_text, [r"€", r"eur", r"euro"])


def parse_surface(details: str) -> tuple[str, str, str]:
    surface_parts = []
    for part in re.split(r"\s*\|\s*", details or ""):
        if re.search(r"m\s*(?:²|2)|㎡", part, flags=re.IGNORECASE):
            surface_parts.append(part)
    surface_text = " | ".join(surface_parts)
    min_m2, max_m2 = parse_range(surface_text, [r"m\s*(?:²|2)", r"㎡"])
    return surface_text, min_m2, max_m2


def parse_housing_type(name: str, details: str) -> str:
    combined = f"{name} | {details}".lower()
    patterns = [
        ("T1 bis", r"\bt1\s*bis\b"),
        ("T1", r"\bt1\b"),
        ("T2", r"\bt2\b"),
        ("T3", r"\bt3\b"),
        ("Studio", r"\bstudio\b"),
        ("Chambre", r"\bchambre\b"),
        ("Colocation", r"\bcolocation\b"),
        ("Individuel", r"\bindividuel\b"),
    ]
    for label, pattern in patterns:
        if re.search(pattern, combined):
            return label
    return ""


def residence_id(name: str, address: str, housing_type: str, price_text: str, surface_text: str, link: str) -> str:
    stable_link = re.sub(r"[?#].*$", "", link or "").rstrip("/")
    fingerprint = "|".join(
        normalize_space(part).lower()
        for part in [stable_link, name, address, housing_type, price_text, surface_text]
    )
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def set_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_card_details(card) -> str:
    """Extract CROUS card details from current and older markup.

    The 2026 CROUS listing cards use <li class="fr-card__detail"> elements.
    Older markup used <p class="fr-card__detail">, so select by class instead
    of tag name to keep both formats working.
    """
    detail_elements = card.select(".fr-card__detail")
    return " | ".join(
        normalize_space(detail.get_text(" ", strip=True))
        for detail in detail_elements
        if normalize_space(detail.get_text(" ", strip=True))
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
    rid = residence_id(name, address, housing_type, price_text, surface_text, link)

    return {
        "residence_id": rid,
        "name": name,
        "housing_type": housing_type,
        "price_text": price_text,
        "price_min_eur": price_min,
        "price_max_eur": price_max,
        "surface_text": surface_text,
        "surface_min_m2": surface_min,
        "surface_max_m2": surface_max,
        "address": address,
        "details": details,
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
        if header:
            count_match = re.search(r"(\d+)\s+logement", header.get_text(" ", strip=True))
            if count_match:
                total_pages = max(1, math.ceil(int(count_match.group(1)) / RESULTS_PER_PAGE))
        print(f"{url}: scraping {total_pages} page(s)")

        for page_num in range(1, total_pages + 1):
            page_soup = soup
            page_url = url
            if page_num > 1:
                page_url = set_query_param(url, "page", str(page_num))
                page_response = session.get(page_url, headers=HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
                page_response.raise_for_status()
                page_soup = BeautifulSoup(page_response.content, "html.parser")
            for card in page_soup.find_all("div", class_="fr-card"):
                residence = card_to_residence(card, page_url, timestamp)
                if residence:
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
        existing_sources = set(filter(None, merged[rid].get("source_url", "").split(" | ")))
        existing_sources.add(row.get("source_url", ""))
        merged[rid]["source_url"] = " | ".join(sorted(existing_sources))
    return sorted(merged.values(), key=sort_key)


def migrate_previous_snapshot(data_dir: Path) -> None:
    current = data_dir / CURRENT_AVAILABLE_FILE
    legacy = data_dir / LEGACY_AVAILABLE_FILE
    if current.exists() or not legacy.exists():
        return
    migrated = []
    timestamp = now_cet().isoformat(timespec="seconds")
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
            "address": row.get("address", ""),
            "details": row.get("details", ""),
            "link": row.get("link", ""),
            "source_url": "legacy_csv",
            "first_seen_cet": timestamp,
            "last_seen_cet": timestamp,
        })
    write_csv(current, migrated, CSV_HEADERS)


def format_residence_html(residence: dict[str, str], color: str = "#111") -> str:
    price = residence.get("price_text") or "Prix non indiqué"
    surface = f" · {html.escape(residence['surface_text'])}" if residence.get("surface_text") else ""
    housing_type = f" · {html.escape(residence['housing_type'])}" if residence.get("housing_type") else ""
    return f"""
    <div style="border-left:3px solid {color};padding-left:14px;margin:0 0 18px 0">
      <p style="margin:0;font-size:17px;font-weight:700"><a href="{html.escape(residence['link'])}" style="color:{color};text-decoration:none">{html.escape(residence['name'])}</a></p>
      <p style="margin:3px 0;color:#333"><b>{html.escape(price)}</b>{housing_type}{surface}</p>
      <p style="margin:3px 0;color:#555">{html.escape(residence.get('address', ''))}</p>
      <p style="margin:3px 0;color:#666;font-size:13px">ID: {html.escape(residence['residence_id'])}</p>
    </div>
    """


def create_email_body(target: RecipientTarget, added: list[dict[str, str]], removed: list[dict[str, str]], current: list[dict[str, str]]) -> str:
    title = f"CROUS — {html.escape(target.name)}"
    body = f"<html><body style='font-family:Arial,sans-serif;color:#222'><div style='max-width:760px;margin:auto'><h1>{title}</h1>"
    if added:
        body += f"<h2 style='color:#198754'>Nouveaux logements ({len(added)})</h2>"
        body += "".join(format_residence_html(row, "#198754") for row in added)
    if removed:
        body += f"<h2 style='color:#dc3545'>Logements disparus ({len(removed)})</h2>"
        body += "".join(format_residence_html(row, "#dc3545") for row in removed)
    body += f"<h2>Disponibles maintenant ({len(current)})</h2>"
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
    print(f"Sent email to {to_email}: {subject}")
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


def process_target(target: RecipientTarget) -> None:
    timestamp = now_cet().isoformat(timespec="seconds")
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
        append_csv(target.data_dir / RUN_LOG_FILE, [{
            "timestamp_cet": timestamp,
            "target_name": target.name,
            "recipient_email": target.email,
            "scraped_count": "0",
            "current_count": str(len(previous)),
            "added_count": "0",
            "removed_count": "0",
            "status": "error",
            "message": f"All scrapes failed: {'; '.join(failed_urls)}",
        }], RUN_LOG_HEADERS)
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
            print(f"Failed to send email to {target.email}: {exc}")

    append_csv(target.data_dir / RUN_LOG_FILE, [{
        "timestamp_cet": timestamp,
        "target_name": target.name,
        "recipient_email": target.email,
        "scraped_count": str(len(scraped)),
        "current_count": str(len(current)),
        "added_count": str(len(added)),
        "removed_count": str(len(removed)),
        "status": "ok" if not failed_urls else "partial",
        "message": "; ".join(failed_urls),
    }], RUN_LOG_HEADERS)
    print(f"{target.name}: {len(current)} current, +{len(added)}, -{len(removed)}")


def load_targets(config_path: Path | None = None) -> list[RecipientTarget]:
    path = config_path or Path(os.environ.get("TARGETS_CONFIG_PATH", DEFAULT_CONFIG_FILE))
    if not path.exists():
        raise FileNotFoundError(
            f"Missing target config file: {path}. Copy/edit crous_targets.json or set TARGETS_CONFIG_PATH."
        )

    with path.open(encoding="utf-8") as handle:
        configs = json.load(handle)

    targets = []
    for config in configs:
        name = config["name"]
        email = config.get("email", "")
        email_env = config.get("email_env")
        if email_env:
            email = os.environ.get(email_env, email)
        urls = config.get("urls") or [config["url"]]
        data_dir = Path(config.get("data_dir") or f"data/{slugify(name)}")
        targets.append(RecipientTarget(
            name=name,
            email=email,
            urls=urls,
            data_dir=data_dir,
            cities=config.get("cities", []),
            send_immediate_alert=bool(config.get("send_immediate_alert", True)),
            send_daily_report=bool(config.get("send_daily_report", False)),
        ))
    return targets


def main() -> None:
    targets = load_targets()
    for target in targets:
        if not target.email:
            print(f"Skipping {target.name}: no recipient email configured.")
            continue
        process_target(target)


if __name__ == "__main__":
    main()
