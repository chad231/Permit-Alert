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
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ══════════════════════════════════════════════════════════════
#  CONFIGURATION  ← edit this section
# ══════════════════════════════════════════════════════════════

PERMIT_IDS = [233262]

ENTRY_POINT_NAMES = [
    "big pine",
    "bishop pass",
    "sabrina",
]

START_DATE = "2026-09-26"
END_DATE   = "2026-09-26"

GROUP_SIZE = 1

RUN_LOOP              = True
POLL_INTERVAL_SECONDS = 60
LOOP_DURATION_MINUTES = 4.5

EMAIL_ENABLED  = True
GMAIL_USER     = os.getenv("GMAIL_USER",     "")
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL",   "")

SMS_ENABLED        = False
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN",  "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER   = os.getenv("TWILIO_TO_NUMBER",   "")


# ══════════════════════════════════════════════════════════════
#  INTERNAL DEDUP STATE
# ══════════════════════════════════════════════════════════════
_alerted = set()
_division_names = {}


# ══════════════════════════════════════════════════════════════
#  API HELPERS
# ══════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": "permit-availability-monitor/1.0 (personal use)",
    "Accept":     "application/json",
}


def get_division_names(permit_id):
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
        print(f"  [info] Entry points found: {list(mapping.values())}")
        return mapping
    except Exception as exc:
        print(f"  [warn] Could not fetch division names for {permit_id}: {exc}")
        return {}


def get_availability(permit_id, start, end):
    url = (
        f"https://www.recreation.gov/api/permitinyo/{permit_id}/availabilityv2"
        f"?start_date={start}T00%3A00%3A00.000Z&end_date={end}T00%3A00%3A00.000Z&commercial_acct=false"
    )
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def name_matches(division_name):
    if not ENTRY_POINT_NAMES:
        return True
    low = division_name.lower()
    return any(target.lower() in low for target in ENTRY_POINT_NAMES)


def find_available_slots(permit_id):
    div_names = get_division_names(permit_id)
    found = []
    try:
        data = get_availability(permit_id, START_DATE, END_DATE)
     except requests.RequestException as exc:
        body = ""
        try:
            body = exc.response.text[:500]
        except Exception:
            pass
        print(f"  [warn] API error — permit {permit_id}: {exc} | Response: {body}")
        return found

    availability = data.get("payload", {}).get("availability", {})
    for date_key, date_info in availability.items():
        date_str = date_key[:10]
        for div_id_str, slot in date_info.get("date_availability", {}).items():
            remaining = slot.get("remaining", 0)
            div_name  = div_names.get(div_id_str, f"Entry point {div_id_str}")
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


# ══════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ══════════════════════════════════════════════════════════════

def build_message(slots):
    lines = []
    for s in slots:
        lines.append(
            f"• {s['date']}  |  {s['division_name']}  |  {s['remaining']} spot(s)\n"
            f"  Book → {s['booking_url']}"
        )
    first   = slots[0]
    subject = f"Permit open: {first['date']} — {first['division_name']}"
    body    = (
        "Inyo Wilderness permit(s) just became available!\n\n"
        + "\n\n".join(lines)
        + "\n\nMove fast — these go quickly."
    )
    return subject, body


def send_email(subject, body):
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


def send_sms(body):
    if not SMS_ENABLED:
        return
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER]):
        print("  [skip] SMS not configured.")
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        resp = requests.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": TWILIO_FROM_NUMBER, "To": TWILIO_TO_NUMBER, "Body": body[:1600]},
            timeout=15,
        )
        resp.raise_for_status()
        print("  [✓] SMS sent.")
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


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def run_check():
    ts = datetime.now().strftime("%H:%M:%SZ")
    print(f"[{ts}] Checking…", end=" ", flush=True)
    all_slots = []
    for permit_id in PERMIT_IDS:
        slots = find_available_slots(permit_id)
        all_slots.extend(slots)
    if all_slots:
        print(f"{len(all_slots)} slot(s) found!")
        notify(all_slots)
    else:
        print("none.")


def main():
    targets = ", ".join(ENTRY_POINT_NAMES) if ENTRY_POINT_NAMES else "ALL"
    print(
        f"Inyo Permit Monitor\n"
        f"  Permit(s) : {PERMIT_IDS}\n"
        f"  Trailheads: {targets}\n"
        f"  Date range: {START_DATE} to {END_DATE}\n"
        f"  Group size: >= {GROUP_SIZE}\n"
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
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    print("Loop complete — GitHub Actions will re-run in ~5 min.")


if __name__ == "__main__":
    main()
