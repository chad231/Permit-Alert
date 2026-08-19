# Inyo Wilderness Permit Monitor

Polls Recreation.gov every 15 minutes and sends you an **email + SMS** the moment
a permit cancellation or new slot opens up. Runs free on GitHub Actions — no server needed.

---

## Quick setup (15 minutes)

### 1 · Fork or create a GitHub repo

Create a **private** GitHub repo (free) and add these two files:
- `monitor.py`
- `.github/workflows/monitor.yml`

### 2 · Configure `monitor.py`

Open `monitor.py` and edit the `CONFIGURATION` section at the top:

```python
PERMIT_IDS  = [233260]          # See "Finding permit IDs" below
START_DATE  = "2026-09-01"      # First date to watch
END_DATE    = "2026-10-15"      # Last date to watch
GROUP_SIZE  = 2                 # Alert only when ≥ this many spots are free
```

### 3 · Get a Gmail App Password

Gmail blocks plain passwords for scripts. You need an **App Password**:

1. Go to [myaccount.google.com](https://myaccount.google.com) → **Security**
2. Make sure 2-Step Verification is **on**
3. Search for **App passwords** → create one (name it "permit monitor")
4. Copy the 16-character password — you'll paste it into GitHub Secrets next

### 4 · Get a free Twilio account (for SMS)

1. Sign up free at [twilio.com/try-twilio](https://www.twilio.com/try-twilio)
2. Verify your mobile number during setup
3. From the Console dashboard, note:
   - **Account SID**
   - **Auth Token**
   - **Your Twilio phone number** (assigned during trial setup)

Free trial credit (~$15) is more than enough for months of 15-minute checks.

### 5 · Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

Add each of these:

| Secret name          | Value |
|----------------------|-------|
| `GMAIL_USER`         | your Gmail address (e.g. `you@gmail.com`) |
| `GMAIL_PASSWORD`     | the 16-char App Password from step 3 |
| `NOTIFY_EMAIL`       | where to receive alerts (can be same as `GMAIL_USER`) |
| `TWILIO_ACCOUNT_SID` | from your Twilio Console |
| `TWILIO_AUTH_TOKEN`  | from your Twilio Console |
| `TWILIO_FROM_NUMBER` | your Twilio number (e.g. `+15005550006`) |
| `TWILIO_TO_NUMBER`   | your real mobile number (e.g. `+14155552671`) |

### 6 · Enable GitHub Actions

Push your files. GitHub Actions will start automatically. You can also trigger
a manual run from the **Actions** tab → **Permit Availability Monitor** → **Run workflow**.

---

## Finding permit IDs

1. Go to [recreation.gov](https://www.recreation.gov)
2. Search for your trailhead (e.g. "Whitney Portal overnight")
3. Click the permit page — the URL will be:
   ```
   https://www.recreation.gov/permits/233260
   ```
   The number at the end (`233260`) is your `PERMIT_ID`.

**Common Inyo National Forest permit IDs** (verify on the site — IDs can change):

| Trailhead / Zone                        | Permit ID |
|-----------------------------------------|-----------|
| Mt. Whitney Zone / Whitney Portal       | 233260    |
| Inyo NF General Overnight Wilderness    | 233261    |

To find **entry-point IDs** (if you want to watch specific trailheads within a permit):
run the script once with `ENTRY_POINT_IDS = []` and it will print every slot it finds,
including the `division_id` for each entry point. Add the ones you want to that list.

---

## Running locally (optional)

```bash
pip install requests

# Set env vars (or just hardcode them in monitor.py for local testing)
export GMAIL_USER="you@gmail.com"
export GMAIL_PASSWORD="xxxx xxxx xxxx xxxx"
export NOTIFY_EMAIL="you@gmail.com"
export TWILIO_ACCOUNT_SID="ACxxxxxxx"
export TWILIO_AUTH_TOKEN="xxxxxxx"
export TWILIO_FROM_NUMBER="+15005550006"
export TWILIO_TO_NUMBER="+14155552671"

python monitor.py
```

---

## FAQ

**Will this book the permit for me?**
No. It only checks and notifies. You still click the link in the alert and complete
the booking yourself on Recreation.gov.

**How fast does GitHub Actions run after availability opens?**
The cron runs every 15 minutes. Realistically you'll hear within 0–15 minutes of a
cancellation — fast enough for most cases.

**What if GitHub Actions has a delay?**
GitHub's free tier can have a few-minute queue delay at peak times. For the absolute
fastest response, run the script locally in a terminal loop instead.

**Is this against Recreation.gov's Terms of Service?**
Monitoring-only scripts are in a much better position than auto-booking bots.
This script only reads public availability data and never submits a transaction.
Services like Campnab do the same thing commercially. That said, be reasonable —
don't set the interval below 5 minutes.
