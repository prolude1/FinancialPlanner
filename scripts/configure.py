#!/usr/bin/env python3
"""Generate local secrets using Python's standard library; never overwrite .env."""
import getpass
import hashlib
from pathlib import Path
import secrets
import os

target = Path(__file__).resolve().parents[1] / ".env"
if target.exists():
    raise SystemExit(".env already exists. Edit it directly; no settings were changed.")
password = getpass.getpass("Choose a local dashboard password (at least 12 characters): ")
if len(password) < 12:
    raise SystemExit("Use at least 12 characters.")
if password != getpass.getpass("Confirm password: "):
    raise SystemExit("Passwords did not match.")
token = getpass.getpass("Telegram bot token (Enter to configure later): ").strip()
owner = input("Your numeric Telegram user ID (Enter to configure later): ").strip()
if owner and (not owner.isdigit() or int(owner) <= 0):
    raise SystemExit("Owner ID must be a positive integer.")
if any(c in token for c in "\n\r'\"$# "):
    raise SystemExit("Invalid token characters.")
salt = secrets.token_bytes(16)
hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000).hex()
settings = {
    "POSTGRES_PASSWORD": secrets.token_hex(24), "SESSION_SECRET": secrets.token_hex(32),
    "BOT_API_SECRET": secrets.token_hex(32), "WEB_PASSWORD_HASH": salt.hex() + ":" + hashed,
    "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_ID": owner,
    "WEB_PORT": "8080", "APP_TIMEZONE": "Asia/Singapore", "MARKET_POLL_SECONDS": "900",
}
fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("\n".join(k + "=" + v for k, v in settings.items()) + "\n")
print("Created private .env. Start with: docker compose up --build")
