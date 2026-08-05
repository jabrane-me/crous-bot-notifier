from __future__ import annotations

import json
import os
from pathlib import Path

import crous_notifier as notifier

AUTH_STATE_FILE = Path("data/authentication_state.json")


def read_auth_state() -> dict[str, bool]:
    if not AUTH_STATE_FILE.exists():
        return {"expired_notified": False}
    try:
        with AUTH_STATE_FILE.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"expired_notified": False}
    return {"expired_notified": bool(state.get("expired_notified", False))}


def write_auth_state(expired_notified: bool) -> None:
    AUTH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUTH_STATE_FILE.open("w", encoding="utf-8") as handle:
        json.dump({"expired_notified": expired_notified}, handle, indent=2)
        handle.write("\n")


def main() -> None:
    session = notifier.create_crous_session()
    authenticated = notifier.is_crous_authenticated(session)
    state = read_auth_state()

    if authenticated:
        if state["expired_notified"]:
            write_auth_state(False)
    elif not state["expired_notified"]:
        to_email = os.environ.get("TO_EMAIL", "")
        subject = "U | CROUS authentication expired"
        body = (
            "<html><body style='font-family:Arial,sans-serif;color:#222'>"
            "<h2>CROUS authentication expired</h2>"
            "<p>The saved CROUS cookies are no longer authenticated.</p>"
            "<p>The notifier has automatically continued using the public market.</p>"
            "</body></html>"
        )
        try:
            sent = notifier.send_email(to_email, subject, body)
        except Exception as exc:
            print(f"Failed to send cookie-expiry email: {exc}")
            sent = False
        if sent:
            write_auth_state(True)

    prefix = "A" if authenticated else "U"
    original_send_email = notifier.send_email

    def send_email_with_auth_prefix(to_email: str, subject: str, html_body: str) -> bool:
        if not subject.startswith(("A | ", "U | ")):
            subject = f"{prefix} | {subject}"
        return original_send_email(to_email, subject, html_body)

    notifier.send_email = send_email_with_auth_prefix
    notifier.main()


if __name__ == "__main__":
    main()
