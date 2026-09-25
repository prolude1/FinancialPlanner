"""Print candidate private-chat sender IDs without printing the bot token."""
import os
import sys
import httpx

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not token:
    sys.exit("Set TELEGRAM_BOT_TOKEN in .env first. Never share the token.")
try:
    response = httpx.post("https://api.telegram.org/bot" + token + "/getUpdates",
                          json={"timeout": 10, "allowed_updates": ["message"]}, timeout=20)
    response.raise_for_status()
    found = False
    for update in response.json().get("result", []):
        msg = update.get("message", {})
        if msg.get("chat", {}).get("type") == "private":
            print("Private message: sender ID", msg.get("from", {}).get("id"),
                  "chat ID", msg["chat"]["id"], "timestamp", msg.get("date"))
            found = True
    if not found:
        print("No private messages found. Send /start to your bot, then run this helper again.")
    else:
        print("Choose YOUR numeric ID and set TELEGRAM_OWNER_ID in .env. No access was granted automatically.")
except Exception:
    sys.exit("Could not read updates. Check the token, network, and that the polling bot is stopped. Token omitted.")
