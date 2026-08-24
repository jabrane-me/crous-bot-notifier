from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import requests

import crous_notifier as notifier

COOKIE_FILE = Path("cookies.json")
INTERESTING_RESPONSE_HEADERS = (
    "server",
    "via",
    "x-request-id",
    "x-cache",
    "x-served-by",
    "x-cache-hits",
    "cf-ray",
    "date",
)


def load_cookies() -> list[dict[str, object]]:
    try:
        payload = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cookies = payload.get("cookies", []) if isinstance(payload, dict) else payload
    return [cookie for cookie in cookies if isinstance(cookie, dict) and cookie.get("name")]


def cookie_names(cookies: Iterable[dict[str, object]]) -> list[str]:
    return sorted({str(cookie.get("name", "")) for cookie in cookies if cookie.get("name")})


def build_session(
    cookies: list[dict[str, object]],
    *,
    include_names: set[str] | None = None,
    exclude_names: set[str] | None = None,
) -> requests.Session:
    session = requests.Session()
    session.headers.update(notifier.HEADERS)
    include_names = include_names or set()
    exclude_names = exclude_names or set()

    for cookie in cookies:
        name = str(cookie.get("name", ""))
        if not name or name in exclude_names:
            continue
        if include_names and name not in include_names:
            continue
        session.cookies.set(
            name,
            str(cookie.get("value", "")),
            domain=str(cookie.get("domain") or "trouverunlogement.lescrous.fr"),
            path=str(cookie.get("path") or "/"),
        )
    return session


def auth_probe(session: requests.Session, label: str) -> tuple[str, bool]:
    before = {cookie.name for cookie in session.cookies}
    try:
        response = session.get(
            f"{notifier.BASE_URL}/api/fr/user",
            timeout=notifier.DEFAULT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        print(f"DIAG AUTH {label}: ERROR {type(exc).__name__}")
        return f"ERROR:{type(exc).__name__}", False

    after = {cookie.name for cookie in session.cookies}
    new_names = sorted(after - before)
    print(f"DIAG AUTH {label} HTTP: {response.status_code}")
    print(f"DIAG AUTH {label} new session cookie names: {new_names}")

    if response.status_code != 200:
        return f"HTTP_{response.status_code}", False

    try:
        payload = response.json()
    except ValueError:
        return "INVALID_JSON", False

    identity = payload.get("identity") if isinstance(payload, dict) else None
    authenticated = isinstance(identity, dict) and bool(identity.get("firstName"))
    return ("AUTHENTICATED" if authenticated else "UNAUTHENTICATED"), authenticated


def set_cookie_names(response: requests.Response) -> list[str]:
    raw = response.headers.get("Set-Cookie", "")
    names: set[str] = set()
    for segment in raw.split(","):
        token = segment.strip().split(";", 1)[0]
        if "=" in token:
            name = token.split("=", 1)[0].strip()
            if name and " " not in name:
                names.add(name)
    return sorted(names)


def search_probe(label: str, session: requests.Session, url: str) -> int | None:
    before = {cookie.name for cookie in session.cookies}
    try:
        response = session.get(url, timeout=notifier.DEFAULT_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"DIAG SEARCH {label}: ERROR {type(exc).__name__}")
        return None

    after = {cookie.name for cookie in session.cookies}
    new_names = sorted(after - before)
    headers = {
        name: response.headers.get(name)
        for name in INTERESTING_RESPONSE_HEADERS
        if response.headers.get(name)
    }
    print(f"DIAG SEARCH {label}: HTTP {response.status_code}")
    print(f"DIAG SEARCH {label} response headers: {json.dumps(headers, ensure_ascii=False, sort_keys=True)}")
    print(f"DIAG SEARCH {label} Set-Cookie names: {set_cookie_names(response)}")
    print(f"DIAG SEARCH {label} new session cookie names: {new_names}")
    return response.status_code


def choose_search_url() -> str | None:
    try:
        targets = notifier.load_targets()
    except Exception as exc:
        print(f"DIAG could not load targets: {type(exc).__name__}")
        return None
    for target in targets:
        for url in target.urls:
            if "Bordeaux" in url:
                return url
    for target in targets:
        if target.urls:
            return target.urls[0]
    return None


def main() -> None:
    cookies = load_cookies()
    names = cookie_names(cookies)
    print("\n=== CROUS SESSION DIAGNOSTIC ===")
    print(f"DIAG saved cookie names ({len(names)}): {names}")

    if not cookies:
        print("DIAG no saved cookies available; stopping diagnostic.")
        print("=== END CROUS SESSION DIAGNOSTIC ===\n")
        return

    search_url = choose_search_url()
    if not search_url:
        print("DIAG no search URL available; stopping diagnostic.")
        print("=== END CROUS SESSION DIAGNOSTIC ===\n")
        return

    # Test the exact saved cookie set before touching /api/fr/user. If this works
    # but the production-shaped auth-then-search sequence fails, the auth probe or
    # cookies set by that response are directly implicated.
    search_probe("same-cookies-before-auth", build_session(cookies), search_url)

    baseline = build_session(cookies)
    baseline_auth_status, baseline_authenticated = auth_probe(baseline, "full-set")
    print(f"DIAG AUTH full cookie set result: {baseline_auth_status}")
    baseline_search = search_probe("full-set-after-auth", baseline, search_url)

    # Same saved cookies, separate TCP/session object, production-shaped sequence.
    fresh_same = build_session(cookies)
    fresh_same_auth_status, _ = auth_probe(fresh_same, "fresh-session-same-cookies")
    print(f"DIAG AUTH fresh session + same cookies result: {fresh_same_auth_status}")
    search_probe("fresh-session-same-cookies-after-auth", fresh_same, search_url)

    # Truly clean public request: do not call /api/fr/user first because that could
    # set anonymous/session cookies and contaminate the comparison.
    search_probe("public-before-auth", build_session([]), search_url)

    public_after_auth = build_session([])
    public_auth_status, _ = auth_probe(public_after_auth, "public")
    print(f"DIAG AUTH public fresh session result: {public_auth_status}")
    search_probe("public-after-auth", public_after_auth, search_url)

    removable: list[str] = []
    required: list[str] = []
    if baseline_authenticated:
        for name in names:
            auth_candidate = build_session(cookies, exclude_names={name})
            auth_status, authenticated = auth_probe(auth_candidate, f"without-{name}")
            print(f"DIAG AUTH without {name} result: {auth_status}")
            if authenticated:
                removable.append(name)
                # Rebuild so /api/fr/user cannot re-add the omitted cookie before
                # the search test.
                search_candidate = build_session(cookies, exclude_names={name})
                search_probe(f"without-{name}-before-auth", search_candidate, search_url)
            else:
                required.append(name)

        print(f"DIAG individually removable cookie names: {removable}")
        print(f"DIAG individually required cookie names: {required}")

        if required:
            core_auth = build_session(cookies, include_names=set(required))
            core_auth_status, core_authenticated = auth_probe(core_auth, "required-only")
            print(f"DIAG AUTH required-only set {required} result: {core_auth_status}")
            if core_authenticated:
                core_search = build_session(cookies, include_names=set(required))
                search_probe("required-only-before-auth", core_search, search_url)
    else:
        print("DIAG baseline was not authenticated; skipping leave-one-out auth tests.")

    if baseline_search == 500:
        print("DIAG NOTE: production-shaped baseline currently reproduces HTTP 500.")
    else:
        print("DIAG NOTE: production-shaped baseline did not reproduce HTTP 500 in this run.")
    print("=== END CROUS SESSION DIAGNOSTIC ===\n")


if __name__ == "__main__":
    main()
