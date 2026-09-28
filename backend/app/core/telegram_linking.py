"""Single-use Telegram link challenges, isolated from financial ledger access."""
from datetime import datetime, timezone
import hashlib
import secrets
import time

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from . import store

CHALLENGE_TTL_SECONDS = 600


class TelegramLinkError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now(now):
    return int(time.time()) if now is None else int(now)


def _hash_challenge(challenge):
    if not isinstance(challenge, str) or not 32 <= len(challenge) <= 128:
        raise TelegramLinkError("invalid_or_expired_challenge", "Challenge is invalid or expired")
    try:
        encoded = challenge.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TelegramLinkError("invalid_or_expired_challenge", "Challenge is invalid or expired") from exc
    return hashlib.sha256(encoded).hexdigest()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def status(principal):
    with store.engine.connect() as connection:
        row = connection.execute(select(store.telegram_connections.c.linked_at).where(
            store.telegram_connections.c.principal == principal)).first()
    if row is None:
        return {"connected": False}
    return {"connected": True, "linked_at": _iso(row.linked_at)}


def create_challenge(principal, now=None):
    timestamp = _now(now)
    challenge = secrets.token_urlsafe(32)
    digest = hashlib.sha256(challenge.encode("utf-8")).hexdigest()
    try:
        with store.engine.begin() as connection:
            store.lock_application(connection)
            existing = connection.execute(select(store.telegram_connections.c.principal).where(
                store.telegram_connections.c.principal == principal)).first()
            if existing is not None:
                raise TelegramLinkError("telegram_already_linked", "Unlink the current Telegram account first")
            # A newly issued challenge revokes earlier unconsumed challenges for this user.
            connection.execute(delete(store.telegram_link_challenges).where(
                (store.telegram_link_challenges.c.principal == principal)
                | (store.telegram_link_challenges.c.expires_at <= timestamp)
                | store.telegram_link_challenges.c.consumed_at.is_not(None)))
            connection.execute(insert(store.telegram_link_challenges).values(
                token_hash=digest, principal=principal, created_at=timestamp,
                expires_at=timestamp + CHALLENGE_TTL_SECONDS, consumed_at=None))
    except IntegrityError as exc:
        raise TelegramLinkError("telegram_already_linked", "Unable to create a Telegram link") from exc
    return {"challenge": challenge, "expires_at": _iso(timestamp + CHALLENGE_TTL_SECONDS),
            "expires_in_seconds": CHALLENGE_TTL_SECONDS}


def confirm_challenge(challenge, telegram_user_id, telegram_chat_id, now=None):
    if (isinstance(telegram_user_id, bool) or not isinstance(telegram_user_id, int) or telegram_user_id <= 0
            or telegram_user_id > 4503599627370495
            or isinstance(telegram_chat_id, bool) or not isinstance(telegram_chat_id, int)
            or telegram_chat_id != telegram_user_id):
        raise TelegramLinkError("invalid_or_expired_challenge", "Challenge is invalid or expired")
    digest = _hash_challenge(challenge)
    timestamp = _now(now)
    try:
        with store.engine.begin() as connection:
            store.lock_application(connection)
            row = connection.execute(select(store.telegram_link_challenges).where(
                store.telegram_link_challenges.c.token_hash == digest).with_for_update()).mappings().first()
            if row is None or row["consumed_at"] is not None or row["expires_at"] <= timestamp:
                raise TelegramLinkError("invalid_or_expired_challenge", "Challenge is invalid or expired")
            if connection.execute(select(store.telegram_connections.c.principal).where(
                    store.telegram_connections.c.principal == row["principal"])).first():
                raise TelegramLinkError("telegram_already_linked", "A Telegram account is already linked")
            if connection.execute(select(store.telegram_connections.c.principal).where(
                    store.telegram_connections.c.telegram_user_id == telegram_user_id)).first():
                raise TelegramLinkError("telegram_already_linked", "A Telegram account is already linked")
            consumed = connection.execute(update(store.telegram_link_challenges).where(
                store.telegram_link_challenges.c.token_hash == digest,
                store.telegram_link_challenges.c.consumed_at.is_(None),
                store.telegram_link_challenges.c.expires_at > timestamp,
            ).values(consumed_at=timestamp))
            if consumed.rowcount != 1:
                raise TelegramLinkError("invalid_or_expired_challenge", "Challenge is invalid or expired")
            # A link can be recreated for another principal/sender later. Never
            # carry a pending finance flow or reply across that identity change.
            connection.execute(delete(store.telegram_user_state).where(
                (store.telegram_user_state.c.principal == row["principal"])
                | (store.telegram_user_state.c.telegram_user_id == telegram_user_id)))
            connection.execute(insert(store.telegram_connections).values(
                principal=row["principal"], telegram_user_id=telegram_user_id, linked_at=timestamp))
    except IntegrityError as exc:
        # Database uniqueness is the final guard for simultaneous confirmations.
        raise TelegramLinkError("telegram_already_linked", "A Telegram account is already linked") from exc
    return {"linked": True}


def unlink(principal):
    with store.engine.begin() as connection:
        store.lock_application(connection)
        connection.execute(delete(store.telegram_user_state).where(
            store.telegram_user_state.c.principal == principal))
        connection.execute(delete(store.telegram_connections).where(
            store.telegram_connections.c.principal == principal))
        connection.execute(delete(store.telegram_link_challenges).where(
            store.telegram_link_challenges.c.principal == principal))
    return {"connected": False}
