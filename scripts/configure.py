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
link_secret_file = target.with_name(".env.telegram-link")
keycloak_secret_file = target.with_name(".env.keycloak")
if link_secret_file.exists() or keycloak_secret_file.exists():
    raise SystemExit("A local service-secret file already exists. Review it manually; no settings were changed.")
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
link_secret = secrets.token_hex(32)
keycloak_db_password = secrets.token_hex(32)
settings = {
    "POSTGRES_PASSWORD": secrets.token_hex(24), "SESSION_SECRET": secrets.token_hex(32),
    "BOT_API_SECRET": secrets.token_hex(32), "WEB_PASSWORD_HASH": salt.hex() + ":" + hashed,
    "TELEGRAM_BOT_TOKEN": token, "TELEGRAM_OWNER_ID": owner,
    "TELEGRAM_BOT_USERNAME": "",
    "KEYCLOAK_ISSUER": "", "KEYCLOAK_AUDIENCE": "", "KEYCLOAK_JWKS_URL": "",
    "WEB_PORT": "8080", "APP_TIMEZONE": "Asia/Singapore", "MARKET_POLL_SECONDS": "900",
}
fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("\n".join(k + "=" + v for k, v in settings.items()) + "\n")
for secret_path, setting in ((link_secret_file, "TELEGRAM_LINK_BOT_SECRET=" + link_secret),
                             (keycloak_secret_file, "KC_DB_PASSWORD=" + keycloak_db_password)):
    secret_fd = os.open(str(secret_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(secret_fd, "w") as stream:
        stream.write(setting + "\n")
print("Created private .env and service-secret files. Start with: docker compose up --build")
