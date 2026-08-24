from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import MethodType
from urllib.parse import urlparse

import requests

import crous_notifier as notifier


class CrousSearch500(RuntimeError):
    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


def arm_search_500_abort(session: requests.Session) -> None:
    """Abort immediately on Tool 47 HTTP 500 before the notifier continues other URLs."""
    original_get = session.get

    def guarded_get(self: requests.Session, url: str, *args, **kwargs):
        response = original_get(url, *args, **kwargs)
        if "/tools/47/search" in str(url) and response.status_code == 500:
            raise CrousSearch500(str(url))
        return response

    session.get = MethodType(guarded_get, session)


def create_phpsessid_only_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(notifier.HEADERS)
    if not notifier.COOKIES_FILE.exists():
        return session

    try:
        with notifier.COOKIES_FILE.open(encoding="utf-8") as handle:
            cookie_data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not load {notifier.COOKIES_FILE} for 500 fallback: {exc}")
        return session

    cookies = cookie_data.get("cookies", []) if isinstance(cookie_data, dict) else cookie_data
    if not isinstance(cookies, list):
        return session

    for cookie in cookies:
        if not isinstance(cookie, dict) or cookie.get("name") != "PHPSESSID":
            continue
        session.cookies.set(
            "PHPSESSID",
            str(cookie.get("value", "")),
            domain=str(cookie.get("domain") or urlparse(notifier.BASE_URL).hostname),
            path=str(cookie.get("path") or "/"),
        )
    return session


def capture_one_off_card_diagnostic() -> None:
    output_path = Path("data/crous_card_matrix.txt")
    if output_path.exists():
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from diagnose_crous_cards import main as diagnostic_main

        with output_path.open("w", encoding="utf-8") as handle, contextlib.redirect_stdout(handle):
            diagnostic_main()
        print(f"CROUS card diagnostic captured in {output_path}.")
    except Exception as exc:
        output_path.write_text(
            f"DIAGNOSTIC FAILED: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        print(f"CROUS card diagnostic failed: {type(exc).__name__}: {exc}")


def main() -> None:
    notifier.AUTH_EMAIL_PREFIX = "U"

    # Temporary one-shot diagnostic. It writes only non-sensitive card/status data
    # under data/, so the existing workflow state commit captures the result.
    capture_one_off_card_diagnostic()

    session = notifier.create_crous_session()
    authenticated = notifier.is_crous_authenticated(session)
    notifier.AUTH_EMAIL_PREFIX = "A" if authenticated else "U"

    if not authenticated:
        session = requests.Session()
        session.headers.update(notifier.HEADERS)

    notifier.handle_authentication_transition(authenticated)
    arm_search_500_abort(session)

    active_session = session
    fallback_active = False
    stop_checks = False

    for target in notifier.load_targets():
        if not target.email:
            print(f"Skipping {target.name}: no recipient email configured.")
            continue

        try:
            notifier.process_target(target, active_session)
        except CrousSearch500 as exc:
            if fallback_active:
                print(
                    f"CROUS 500 FALLBACK also returned HTTP 500 for {exc.url}; "
                    "stopping housing checks for this run and preserving existing snapshots."
                )
                stop_checks = True
                break

            print(f"CROUS search returned HTTP 500 for {exc.url}.")
            print(
                "CROUS 500 FALLBACK: aborting the normal session immediately and "
                "retrying with a fresh PHPSESSID-only session."
            )
            fallback_session = create_phpsessid_only_session()
            fallback_authenticated = notifier.is_crous_authenticated(fallback_session)
            print(
                "CROUS 500 FALLBACK AUTH: "
                + ("AUTHENTICATED" if fallback_authenticated else "UNAUTHENTICATED")
            )

            if authenticated and not fallback_authenticated:
                print(
                    "CROUS 500 FALLBACK could not preserve the authenticated session; "
                    "stopping housing checks for this run and preserving existing snapshots."
                )
                active_session = fallback_session
                fallback_active = True
                stop_checks = True
                break

            arm_search_500_abort(fallback_session)
            active_session = fallback_session
            fallback_active = True

            try:
                notifier.process_target(target, active_session)
            except CrousSearch500 as fallback_exc:
                print(
                    f"CROUS 500 FALLBACK also returned HTTP 500 for {fallback_exc.url}; "
                    "stopping housing checks for this run and preserving existing snapshots."
                )
                stop_checks = True
                break

    status_session = active_session
    status = "AUTHENTICATED" if authenticated and notifier.is_crous_authenticated(status_session) else "PUBLIC"
    print("\n" + "=" * 72)
    print(f"CROUS SCRAPING STATUS: {status}")
    if fallback_active:
        print("CROUS 500 FALLBACK: ACTIVE")
    if stop_checks:
        print("CROUS HOUSING CHECKS: STOPPED SAFELY")
    print("=" * 72)


if __name__ == "__main__":
    main()
