from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import crous_notifier as notifier

COOKIE_FILE = Path("cookies.json")
ACCOMMODATION_RE = re.compile(r"/tools/47/accommodations/(\d+)")


def load_cookies() -> list[dict[str, object]]:
    payload = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    raw = payload.get("cookies", []) if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise ValueError("invalid cookie collection")
    return [cookie for cookie in raw if isinstance(cookie, dict) and cookie.get("name")]


def build_session(cookies: list[dict[str, object]], names: set[str] | None = None) -> requests.Session:
    session = requests.Session()
    session.headers.update(notifier.HEADERS)
    for cookie in cookies:
        name = str(cookie.get("name") or "")
        if not name or (names is not None and name not in names):
            continue
        session.cookies.set(
            name,
            str(cookie.get("value", "")),
            domain=str(cookie.get("domain") or urlparse(notifier.BASE_URL).hostname),
            path=str(cookie.get("path") or "/"),
        )
    return session


def auth_probe(session: requests.Session) -> str:
    try:
        response = session.get(f"{notifier.BASE_URL}/api/fr/user", timeout=notifier.DEFAULT_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return f"ERROR:{type(exc).__name__}"
    if response.status_code != 200:
        return f"HTTP_{response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return "INVALID_JSON"
    identity = payload.get("identity") if isinstance(payload, dict) else None
    return "AUTHENTICATED" if isinstance(identity, dict) and identity.get("firstName") else "UNAUTHENTICATED"


def bordeaux_urls() -> list[str]:
    for target in notifier.load_targets():
        if target.name.lower() == "bordeaux":
            return target.urls
    raise RuntimeError("Bordeaux target not found")


def compact_location(url: str) -> str:
    query = urlparse(url).query
    match = re.search(r"(?:^|&)locationName=([^&]+)", query)
    if not match:
        return "unknown"
    from urllib.parse import unquote_plus

    return unquote_plus(match.group(1))


def extract_cards(response: requests.Response) -> tuple[list[tuple[str, str]], str, str]:
    soup = BeautifulSoup(response.content, "html.parser")
    cards: list[tuple[str, str]] = []
    seen: set[str] = set()
    for card in soup.select(".fr-card"):
        link = card.select_one("h3.fr-card__title a") or card.find("a", href=ACCOMMODATION_RE)
        if not link:
            continue
        href = str(link.get("href", ""))
        match = ACCOMMODATION_RE.search(href)
        if not match:
            continue
        accommodation_id = match.group(1)
        if accommodation_id in seen:
            continue
        seen.add(accommodation_id)
        name = notifier.normalize_space(link.get_text(" ", strip=True))
        cards.append((accommodation_id, name))

    header = soup.select_one("h2.SearchResults-desktop")
    header_text = notifier.normalize_space(header.get_text(" ", strip=True)) if header else ""
    title = notifier.normalize_space(soup.title.get_text(" ", strip=True)) if soup.title else ""
    return cards, header_text, title


def search_once(session: requests.Session, label: str, url: str) -> dict[str, object]:
    location = compact_location(url)
    before_names = sorted(cookie.name for cookie in session.cookies)
    started = time.monotonic()
    try:
        response = session.get(url, timeout=notifier.DEFAULT_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        elapsed = time.monotonic() - started
        print(f"MATRIX {label} | {location} | ERROR {type(exc).__name__} | {elapsed:.2f}s")
        return {"status": None, "ids": [], "location": location}

    elapsed = time.monotonic() - started
    cards, header, title = extract_cards(response)
    after_names = sorted(cookie.name for cookie in session.cookies)
    ids = [item[0] for item in cards]
    names = [item[1] for item in cards]
    print(
        f"MATRIX {label} | {location} | HTTP {response.status_code} | cards={len(cards)} | "
        f"ids={ids} | names={names} | bytes={len(response.content)} | {elapsed:.2f}s"
    )
    print(f"MATRIX {label} | {location} | header={header!r} | title={title!r}")
    if before_names != after_names:
        print(f"MATRIX {label} | {location} | cookie-names-before={before_names} after={after_names}")
    return {"status": response.status_code, "ids": ids, "location": location}


def run_variant(
    label: str,
    cookies: list[dict[str, object]],
    urls: list[str],
    names: set[str] | None,
    *,
    auth_first: bool,
) -> dict[str, set[str]]:
    session = build_session(cookies, names)
    selected = sorted(cookie.name for cookie in session.cookies)
    print("\n" + "-" * 72)
    print(f"VARIANT {label}")
    print(f"VARIANT {label} cookie names: {selected}")
    if auth_first:
        print(f"VARIANT {label} auth: {auth_probe(session)}")
    else:
        print(f"VARIANT {label} auth: NOT_PROBED_BEFORE_SEARCH")

    by_location: dict[str, set[str]] = {}
    for index, url in enumerate(urls):
        result = search_once(session, label, url)
        by_location[str(result["location"])] = set(str(value) for value in result["ids"])
        if index + 1 < len(urls):
            time.sleep(0.35)

    if not auth_first:
        print(f"VARIANT {label} auth-after-search: {auth_probe(session)}")
    session.close()
    return by_location


def main() -> None:
    cookies = load_cookies()
    names = sorted({str(cookie.get("name")) for cookie in cookies})
    urls = bordeaux_urls()

    print("=== CROUS HOUSING CARD MATRIX ===")
    print(f"Saved cookie names ({len(names)}): {names}")
    print(f"Bordeaux URLs tested: {[compact_location(url) for url in urls]}")

    # Baselines: same six-cookie state, but fresh TCP/session objects and with/without auth probe.
    baseline_pre = run_variant("FULL-FRESH-PREAUTH", cookies, urls, None, auth_first=False)
    baseline_post = run_variant("FULL-FRESH-POSTAUTH", cookies, urls, None, auth_first=True)
    baseline_repeat = run_variant("FULL-FRESH-REPEAT", cookies, urls, None, auth_first=True)

    visible_locations = {
        location
        for matrix in (baseline_pre, baseline_post, baseline_repeat)
        for location, ids in matrix.items()
        if ids
    }
    probe_urls = [url for url in urls if compact_location(url) in visible_locations] or urls
    print(f"\nFocused cookie-combination URLs: {[compact_location(url) for url in probe_urls]}")

    PHP = "PHPSESSID"
    RULES = "tool.47.hasUserReadRules"
    HAPROXY = "HAPROXYID"
    QPID = "qpid"

    variants: list[tuple[str, set[str]]] = [
        ("PHP-ONLY", {PHP}),
        ("PHP+RULES", {PHP, RULES}),
        ("PHP+HAPROXY", {PHP, HAPROXY}),
        ("PHP+QPID", {PHP, QPID}),
        ("PHP+HAPROXY+QPID", {PHP, HAPROXY, QPID}),
        ("PHP+RULES+HAPROXY", {PHP, RULES, HAPROXY}),
        ("PHP+RULES+QPID", {PHP, RULES, QPID}),
        ("FUNCTIONAL-4", {PHP, RULES, HAPROXY, QPID}),
    ]

    for label, selected_names in variants:
        run_variant(label, cookies, probe_urls, selected_names, auth_first=True)

    print("\n=== END CROUS HOUSING CARD MATRIX ===")


if __name__ == "__main__":
    main()
