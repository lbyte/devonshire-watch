"""One sweep: Telegram alert when a table opens at a TableCheck venue.

Configured by environment variables; the defaults watch Yakiniku Jambo Hanare.
Reads availability only (GET); it never submits a booking.
Exit codes: 0 = sweep finished, 3 = rate limited or site responses not understood.
"""
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VENUE = os.environ.get("VENUE", "Jambo Hanare")
SHOP = os.environ.get("SHOP", "yakiniku-jambo-hanare")
# Comma-separated id:label pairs, one for each seating category to watch.
# Set it to an empty string for venues without seating categories.
CATEGORIES = [c.split(":") for c in os.environ.get("CATEGORIES", "66761d96ee97d6a2169dce98:counter").split(",") if c] or [("", "")]
DATES = os.environ.get("DATES", "2026-11-17,2026-11-18,2026-11-19,2026-11-20,2026-11-21,2026-11-22,2026-11-23").split(",")
PEOPLE = os.environ.get("PEOPLE", "2")
DELAY = float(os.environ.get("DELAY", "45"))  # Seconds between requests; 10s got rate limited after ~110 requests.
PREFIX = os.environ.get("ALERT_PREFIX", "")
HERE = pathlib.Path(__file__).parent
STATE = pathlib.Path(os.environ.get("STATE_FILE", HERE / "state.json"))
CACHE = HERE / f".slots-{SHOP}.json"  # Not committed: slot times for this run.
LAST_OK = HERE / f".last_ok-{SHOP}"  # Not committed: time of the last understood response.
BLOCKED_ALERT_AFTER = 2 * 3600

BASE = f"https://www.tablecheck.com/en/shops/{SHOP}"
BOOK_URL = os.environ.get("BOOK_URL", f"{BASE}/reserve")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}
# With no course selected, a free table comes back as warn/require_order.
OPEN = {"success", "warn", "reservation_request"}
# Understood, but not a free table of the wanted kind.
# service_category = a different seating category is free, not the one being checked.
# same_day/other_day = this time is taken, but other times are free; those get checked themselves.
NOT_OPEN = {"failure", "closed", "service_category", "smoking", "seat_type", "same_day", "other_day"}
# This warning says the request was incomplete; it says nothing about tables.
NOT_AVAILABILITY_WARNINGS = {"require_service_category"}


class RateLimited(Exception):
    pass


def is_open(res):
    if res.get("status") == "warn" and res.get("data") in NOT_AVAILABILITY_WARNINGS:
        return False
    return res.get("status") in OPEN


def get(path, params):
    req = urllib.request.Request(f"{BASE}/{path}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    time.sleep(DELAY)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise RateLimited(f"HTTP {exc.code}") from exc
        raise


def telegram(text):
    body = urllib.parse.urlencode({"chat_id": os.environ["TG_CHAT_ID"], "text": PREFIX + text}).encode()
    url = f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage"
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as resp:
        json.load(resp)
    print("Telegram alert sent", flush=True)


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_state(state):
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def slots_for(day, cache):
    if day not in cache:
        slots = get("sheets", {"reservation[start_date]": day, "reservation[num_people_adult]": PEOPLE}).get("slots", [])
        if slots:
            cache[day] = slots
            CACHE.write_text(json.dumps(cache))
        return slots
    return cache[day]


def check_day(day, cache, state):
    """Returns the number of responses understood. Alerts once per newly open slot."""
    understood = 0
    newly_open = []
    now = time.time()
    for label, epoch in slots_for(day, cache):
        if epoch <= now:
            continue
        for category_id, category in CATEGORIES:
            params = {"reservation[start_at_epoch]": epoch, "reservation[num_people_adult]": PEOPLE}
            if category_id:
                params["reservation[service_category]"] = category_id
            res = get("available", params)
            status = res.get("status")
            key = f"{day} {label} {category}".strip()
            print(key, status, json.dumps(res.get("data"), ensure_ascii=False)[:120], flush=True)
            if is_open(res):
                understood += 1
                if key not in state["open"]:
                    newly_open.append((key, f"{label} {category}".strip()))
            elif status in NOT_OPEN:
                understood += 1
                if key in state["open"]:
                    state["open"].remove(key)
                    save_state(state)
    if newly_open:
        times = ", ".join(text for _, text in newly_open)
        telegram(f"{VENUE} table open! {day}, {times}, for {PEOPLE}.\nBook now: {BOOK_URL}")
        # Saved after sending, so a failed send is retried on the next sweep.
        state["open"] += [key for key, _ in newly_open]
        save_state(state)
    return understood


def main():
    state = load(STATE, {"open": [], "blocked_alerted": False})
    cache = load(CACHE, {})
    if not LAST_OK.exists():
        LAST_OK.write_text(str(time.time()))
    understood = 0
    limited = False
    for day in DATES:
        try:
            understood += check_day(day, cache, state)
        except RateLimited as exc:
            print("Rate limited:", exc, flush=True)
            limited = True
            break
        except Exception as exc:  # One bad response shouldn't end the sweep.
            print(f"{day} check failed: {exc!r}", flush=True)

    if understood:
        LAST_OK.write_text(str(time.time()))
        if state["blocked_alerted"]:
            telegram(f"{VENUE} watcher is checking again.")
            state["blocked_alerted"] = False
            save_state(state)
    elif not state["blocked_alerted"] and time.time() - float(LAST_OK.read_text()) > BLOCKED_ALERT_AFTER:
        telegram(f"{VENUE} watcher can't read availability (blocked or the site changed) and may miss tables.")
        state["blocked_alerted"] = True
        save_state(state)
    print(f"Sweep done: {understood} understood, open now: {state['open']}", flush=True)
    return 3 if limited or not understood else 0


sys.exit(main())
