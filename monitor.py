#!/usr/bin/env python3
"""
Recreation.gov Inyo Wilderness Permit Monitor
─────────────────────────────────────────────
Polls the Recreation.gov public API for overnight permit availability
and sends email (Gmail) and/or SMS (Twilio) alerts when spots open up.

This script only checks and notifies — it does NOT book anything.
You still click through and complete the reservation yourself.

POLLING MODE
────────────
When RUN_LOOP=True the script polls every POLL_INTERVAL_SECONDS for
LOOP_DURATION_MINUTES, then exits. Pair with a 5-minute GitHub Actions
cron for ~60-second effective check frequency (GitHub's minimum cron
interval is 5 min; the inner loop bridges the gap).
"""

import os
import time
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ══════════════════════════════════════════════════════════════
#  CONFIGURATION  ← edit this section
# ══════════════════════════════════════════════════════════════

# Inyo National Forest Overnight Wilderness Permit
# recreation.gov/permits/233262
PERMIT_IDS = [233262]

# Entry-point names to watch (case-insensitive substring match against
# the name Recreation.gov returns). Leave empty to watch ALL entry points.
#
# The API returns names like:
#   "Big Pine Creek North Fork", "Bishop Pass (JMT)", "Sabrina Lake"
# Substrings below match those reliably without needing numeric IDs.
ENTRY_POINT_NAMES = [
    "big pine",      # → Big Pine Creek North Fork
    "bishop pass",   # → Bishop Pass (JMT) / South Lake entry
    "sabrina",       # → Sabrina Lake
]

# Target date(s) to watch (YYYY-MM-DD). To watch a single date set both the same.
START_DATE = "2026-09-26"
END_DATE   = "2026-09-26"

# Alert only when at least this many spots are free.
GROUP_SIZE = 1

# ──────────────────────────────────────────────────────────────
#  POLLING LOOP
#  RUN_LOOP=True  → check every POLL_INTERVAL_SECONDS for
#                   LOOP_DURATION_MINUTES, then exit.
#                   Pair with a 5-minute GitHub Actions cron.
#  RUN_LOOP=False → single check and exit (local / ad-hoc use).
# ──────────────────────────────────────────────────────────────
RUN_LOOP              = True
POLL_INTERVAL_SECONDS = 60    # seconds between each check
LOOP_DURATION_MINUTES = 4.5   # how long one GitHub Actions run lasts

# ──────────────────────────────────────────────────────────────
#  EMAIL  (Gmail SMTP)
#  Use a Gmail "App Password" — not your real password.
#  Create one at: myaccount.google.com → Security → App passwords
# ──────────────────────────────────────────────────────────────
EMAIL_ENABLED  = True
GMAIL_USER     = os.getenv("GMAIL_USER",     "")   # e.g. you@gmail.com
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")   # 16-char App Password
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL",   "")   # where alerts go

# ──────────────────────────────────────────────────────────────
#  SMS  (Twilio free trial — twilio.com/try-twilio)
# ──────────────────────────────────────────────────────────────
SMS_ENABLED        = True
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN",  "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")  # your Twilio number
TWILIO_TO_NUMBER   = os.getenv("TWILIO_TO_NUMBER",   "")  # your mobile number


# ══════════════════════════════════════════════════════════════
#  INTERNAL DEDUP STATE
#  Tracks which (permit, division, date) combos we've already
#  alerted on so we don't spam you with repeat notifications.
# ══════════════════════════════════════════════════════════════
_alerted: set[tuple] = set()


# ══════════════════════════════════════════════════════════════
#  API HELPERS
# ══════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": "permit-availability-monitor/1.0 (personal use)",
    "Accept":     "application/json",
}

# Cache division name lookups so we don't fetch metadata on every check
_division_names: dict[str, dict[str, str]] = {}


def get_division_names(permit_id: int) -> dict[str, str]:
    """
    Fetch division (entry point) metadata for a permit and return
    a dict mapping division_id string → human-readable name.
    Uses the /api/permitcontent/ endpoint.
    """
    key = str(permit_id)
    if key in _division_names:
        return _division_names[key]
    url = f"https://www.recreation.gov/api/permitcontent/{permit_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        divisions = data.get("payload", {}).get("divisions", {})
        mapping = {
            div_id: div_info.get("name", f"Entry point {div_id}")
            for div_id, div_info in divisions.items()
        }
        _division_names[key] = mapping
        return mapping
    except Exception as exc:
        print(f"  [warn] Could not fetch division names for {permit_id}: {exc}")
        return {}


def get_availability(permit_id: int, start: str, end: str) -> dict:
    """
    Fetch permit availability using the Inyo-specific API endpoint.
    start / end are YYYY-MM-DD strings.
    """
    url = (
        f"https://www.recreation.gov/api/permitinyo/{permit_id}/availabilityv2"
        f"?start_date={start}&end_date={end}&commercial_acct=false"
    )
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _name_matches(division_name: str) -> bool:
    """Return True if this division name matches any of our target entry points."""
    if not ENTRY_POINT_NAMES:
        return True
    low = division_name.lower()
    return any(target.lower() in low for target in ENTRY_POINT_NAMES)


def find_available_slots(permit_id: int) -> list[dict]:
    """
    Check availability for the configured date range and return
    a list of available date/entry-point combos that haven't been alerted yet.
    """
    div_names = get_division_names(permit_id)
    found = []

    try:
        data = get_availability(permit_id, START_DATE, END_DATE)
    except requests.RequestException as exc:
        print(f"  [warn] API error — permit {permit_id}: {exc}")
        return found

    # Response structure:
    # { "payload": { "availability": { "YYYY-MM-DDT00:00:00Z": { "date_availability": { "div_id": { "remaining": N, ... } } } } } }
    availability = data.get("payload", {}).get("availability", {})

    for date_key, date_info in availability.items():
        # date_key is like "2026-09-26T00:00:00Z" — extract just the date part
        date_str = date_key[:10]

        for div_id_str, slot in date_info.get("date_availability", {}).items():
            remaining = slot.get("remaining", 0)
            div_name  = div_names.get(div_id_str, f"Entry point {div_id_str}")

            if not _name_matches(div_name):
                continue
            if remaining < GROUP_SIZE:
                continue

            key = (permit_id, div_id_str, date_str)
            if key in _alerted:
                continue

            found.append({
                "permit_id":     permit_id,
                "division_id":   div_id_str,
                "division_name": div_name,
                "date":          date_str,
                "remaining":     remaining,
                "booking_url":   f"https://www.recreation.gov/permits/{permit_id}",
            })

    return found


# ══════════════════════════════════════════════════════════════
#  NOTIFICATION HELPERS
# ══════════════════════════════════════════════════════════════

def _build_message(slots: list[dict]) -> tuple[str, str]:
    lines = []
    for s in slots:
        lines.append(
            f"• {s['date']}  |  {s['division_name']}  |  "
            f"{s['remaining']} spot(s)\n"
            f"  Book → {s['booking_url']}"
        )
    first   = slots[0]
    subject = f"🏔️ Permit open: {first['date']} — {first['division_name']}"
    body    = (
        "Inyo Wilderness permit(s) just became available!\n\n"
        + "\n\n".join(lines)
        + "\n\nMove fast — these go quickly."
    )
    return subject, body


def send_email(subject: str, body: str) -> None:
    if not EMAIL_ENABLED:
        return
    if not all([GMAIL_USER, GMAIL_PASSWORD, NOTIFY_EMAIL]):
        print("  [skip] Email not configured.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print("  [✓] Email sent.")
    except Exception as exc:
        print(f"  [!] Email failed: {exc}")


def send_sms(body: str) -> None:
    if not SMS_ENABLED:
        return
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
                TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER]):
        print("  [skip] SMS not configured.")
        return
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{TWILIO_ACCOUNT_SID}/Messages.json"
    )
    try:
        resp = requests.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "From": TWILIO_FROM_NUMBER,
                "To":   TWILIO_TO_NUMBER,
                "Body": body[:1600],
            },
            timeout=15,
        )
        resp.raise_for_status()
        print("  [✓] SMS sent.")
    except Exception as exc:
        print(f"  [!] SMS failed: {exc}")


def notify(slots: list[dict]) -> None:
    subject, body = _build_message(slots)
    print("\n" + "═" * 60)
    print(body)
    print("═" * 60 + "\n")
    send_email(subject, body)
    send_sms(body)
    # Mark as alerted so we don't repeat within this run
    for s in slots:
        _alerted.add((s["permit_id"], s["division_id"], s["date"]))


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def run_check() -> None:
    """Single availability check across all configured permits."""
    ts = datetime.utcnow().strftime("%H:%M:%SZ")
    print(f"[{ts}] Checking…", end=" ", flush=True)

    all_slots: list[dict] = []
    for permit_id in PERMIT_IDS:
        slots = find_available_slots(permit_id)
        all_slots.extend(slots)

    if all_slots:
        print(f"{len(all_slots)} slot(s) found!")
        notify(all_slots)
    else:
        print("none.")


def main() -> None:
    targets = ", ".join(ENTRY_POINT_NAMES) if ENTRY_POINT_NAMES else "ALL entry points"
    print(
        f"Inyo Permit Monitor\n"
        f"  Permit(s) : {PERMIT_IDS}\n"
        f"  Trailheads: {targets}\n"
        f"  Date range: {START_DATE} → {END_DATE}\n"
        f"  Group size: ≥ {GROUP_SIZE}\n"
        f"  Mode      : {'loop every %ds for %.1f min' % (POLL_INTERVAL_SECONDS, LOOP_DURATION_MINUTES) if RUN_LOOP else 'single check'}\n"
    )

    if not RUN_LOOP:
        run_check()
        return

    deadline = time.time() + LOOP_DURATION_MINUTES * 60
    while time.time() < deadline:
        run_check()
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_for = min(POLL_INTERVAL_SECONDS, remaining)
        time.sleep(sleep_for)

    print("Loop complete — GitHub Actions will re-run in ~5 min.")


if __name__ == "__main__":
    main()
