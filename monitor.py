#!/usr/bin/env python3
"""
Recreation.gov Inyo Wilderness Permit Monitor
Polls the Recreation.gov public API for overnight permit availability
and sends email (Gmail) and/or SMS (Twilio) alerts when spots open up.

This script only checks and notifies -- it does NOT book anything.
"""

import os
import time
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ----------------------------------------------------------
#  CONFIGURATION
# ----------------------------------------------------------

PERMIT_IDS = [233262]

ENTRY_POINT_NAMES = [
    "big pine",
    "bishop pass",
    "sabrina",
]

# The permitinyo API requires the FULL month as the range
# (first day to last day). We then filter for TARGET_DATE below.
START_DATE  = "2026-09-01"
END_DATE    = "2026-09-30"
TARGET_DATE = "2026-09-26"
GROUP_SIZE  = 1

RUN_LOOP              = True
POLL_INTERVAL_SECONDS = 60
LOOP_DURATION_MINUTES = 85

EMAIL_ENABLED  = True
GMAIL_USER     = os.getenv("GMAIL_USER",     "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL",   "")

SMS_ENABLED        = False
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN",  "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER   = os.getenv("TWILIO_TO_NUMBER",   "")


# ----------------------------------------------------------
#  SESSION  -- establishes cookies recreation.gov expects
# ----------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.recreation.gov/",
    "Origin":          "https://www.recreation.gov",
})

_session_initialized = False

def init_session():
    """Visit the permit page once to pick up any session cookies."""
    global _session_initialized
    if _session_initialized:
        return
    try:
        SESSION.get("https://www.recreation.gov/permits/233262", timeout=15)
        _session_initialized = True
        print("  [ok] Session initialized.")
    except Exception as exc:
        print(f"  [warn] Session init failed (continuing anyway): {exc}")
        _session_initialized = True


# ----------------------------------------------------------
#  INTERNAL STATE
# ----------------------------------------------------------

_alerted   = set()
_div_names = {}


# ----------------------------------------------------------
#  API HELPERS
# ----------------------------------------------------------

def get_division_names(permit_id):
    key = str(permit_id)
    if key in _div_names:
        return _div_names[key]
    url = f"https://www.recreation.gov/api/permitcontent/{permit_id}"
    try:
        resp = SESSION.get(url, timeout=20)
        print(f"  [debug] permitcontent status: {resp.status_code}")
        resp.raise_for_status()
        divisions = resp.json().get("payload", {}).get("divisions", {})
        mapping = {
            div_id: info.get("name", f"Entry {div_id}")
            for div_id, info in divisions.items()
        }
        _div_names[key] = mapping
        print(f"  [info] Entry points: {list(mapping.values())}")
        return mapping
    except Exception as exc:
        body = ""
        try:
            body = exc.response.text[:300]
        except Exception:
            pass
        print(f"  [warn] Division names error: {exc} | body: {body}")
        return {}


def get_availability(permit_id, start, end):
    url = (
        f"https://www.recreation.gov/api/permitinyo/{permit_id}/availabilityv2"
        f"?start_date={start}&end_date={end}&commercial_acct=false"
    )
    print(f"  [debug] Fetching: {url}")
    resp = SESSION.get(url, timeout=20)
    print(f"  [debug] Status: {resp.status_code}")
    if not resp.ok:
        print(f"  [debug] Error body: {resp.text[:500]}")
    resp.raise_for_status()
    return resp.json()


def name_matches(division_name):
    if not ENTRY_POINT_NAMES:
        return True
    return any(t.lower() in division_name.lower() for t in ENTRY_POINT_NAMES)


def find_available_slots(permit_id):
    div_names = get_division_names(permit_id)
    found = []
    try:
        data = get_availability(permit_id, START_DATE, END_DATE)
    except requests.RequestException as exc:
        print(f"  [warn] Availability fetch failed: {exc}")
        return found

    availability = data.get("payload", {}).get("availability", {})
    for date_key, date_info in availability.items():
        date_str = date_key[:10]
        if date_str != TARGET_DATE:
            continue
        for div_id_str, slot in date_info.get("date_availability", {}).items():
            remaining = slot.get("remaining", 0)
            div_name  = div_names.get(div_id_str, f"Entry {div_id_str}")
            if not name_matches(div_name):
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


# ----------------------------------------------------------
#  NOTIFICATIONS
# ----------------------------------------------------------

def build_message(slots):
    lines = [
        f"- {s['date']}  |  {s['division_name']}  |  {s['remaining']} spot(s)\n"
        f"  Book: {s['booking_url']}"
        for s in slots
    ]
    first   = slots[0]
    subject = f"Permit open: {first['date']} -- {first['division_name']}"
    body    = "Inyo Wilderness permit(s) just became available!\n\n" + "\n\n".join(lines) + "\n\nMove fast!"
    return subject, body


def send_email(subject, body):
    if not EMAIL_ENABLED or not all([GMAIL_USER, GMAIL_PASSWORD, NOTIFY_EMAIL]):
        print("  [skip] Email not configured.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print("  [ok] Email sent.")
    except Exception as exc:
        print(f"  [!] Email failed: {exc}")


def send_sms(body):
    if not SMS_ENABLED or not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER]):
        return
    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": TWILIO_FROM_NUMBER, "To": TWILIO_TO_NUMBER, "Body": body[:1600]},
            timeout=15,
        )
        resp.raise_for_status()
        print("  [ok] SMS sent.")
    except Exception as exc:
        print(f"  [!] SMS failed: {exc}")


def notify(slots):
    subject, body = build_message(slots)
    print("\n" + "=" * 60)
    print(body)
    print("=" * 60 + "\n")
    send_email(subject, body)
    send_sms(body)
    for s in slots:
        _alerted.add((s["permit_id"], s["division_id"], s["date"]))


# ----------------------------------------------------------
#  MAIN
# ----------------------------------------------------------

def run_check():
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{ts}] Checking...")
    all_slots = []
    for permit_id in PERMIT_IDS:
        slots = find_available_slots(permit_id)
        all_slots.extend(slots)
    if all_slots:
        print(f"  {len(all_slots)} slot(s) found!")
        notify(all_slots)
    else:
        print("  No availability.")


def main():
    targets = ", ".join(ENTRY_POINT_NAMES) if ENTRY_POINT_NAMES else "ALL"
    print(
        f"Inyo Permit Monitor\n"
        f"  Permit(s)  : {PERMIT_IDS}\n"
        f"  Trailheads : {targets}\n"
        f"  Target date: {TARGET_DATE}\n"
        f"  Group size : >= {GROUP_SIZE}\n"
        f"  Mode       : {'loop every %ds for %.1f min' % (POLL_INTERVAL_SECONDS, LOOP_DURATION_MINUTES) if RUN_LOOP else 'single check'}\n"
    )

    init_session()

    if not RUN_LOOP:
        run_check()
        return

    deadline = time.time() + LOOP_DURATION_MINUTES * 60
    while time.time() < deadline:
        run_check()
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))

    print("\nLoop complete -- GitHub Actions will re-run in ~5 min.")


if __name__ == "__main__":
    main()
