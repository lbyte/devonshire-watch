"""Telegram alert when a Devonshire (Soho) lunch slot opens on DATE."""
import json
import os
import pathlib
import urllib.parse
import urllib.request

VENUE = "64ba4dc01a788a0a0523a9fa"
LUNCH_TYPE = "64f1ac8bc00e4863bb3996e9"
DATE = os.environ.get("DATE", "2026-10-25")
PEOPLE = os.environ.get("PEOPLE", "2")
STATE = pathlib.Path(__file__).with_name("last_seen.txt")


def get_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


url = (f"https://api.designmynight.com/v4/venues/{VENUE}/booking-availability"
       f"?type={LUNCH_TYPE}&num_people={PEOPLE}&date={DATE}")
slots = get_json(url)["payload"]["validation"]["time"]["suggestedValues"]
open_slots = [s["time"] for s in slots if s["action"] != "reject"]
summary = ", ".join(open_slots) or "none"
print(f"{DATE} for {PEOPLE}: open {summary}")

# Only notify when the set of open slots changes to something non-empty.
prev = STATE.read_text().strip() if STATE.exists() else ""
if open_slots and summary != prev:
    text = (f"Devonshire lunch slot open! {DATE} for {PEOPLE}: {summary}\n"
            "https://www.devonshiresoho.co.uk/")
    body = urllib.parse.urlencode({"chat_id": os.environ["TG_CHAT_ID"], "text": text}).encode()
    get_json(f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage", body)
    print("Telegram alert sent")
# Saved after sending, so a failed send is retried on the next check.
STATE.write_text(summary + "\n")
