from hashlib import sha256
from types import SimpleNamespace
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import delete, select

from app.core import keycloak_identity, store, telegram_linking


@pytest.fixture(autouse=True)
def clean_link_tables():
    store.migrate()
    with store.engine.begin() as connection:
        connection.execute(delete(store.telegram_link_challenges))
        connection.execute(delete(store.telegram_connections))


class StaticJwks:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        if jwt.get_unverified_header(token).get("kid") != "test-key":
            raise jwt.exceptions.PyJWKClientError("Unknown signing key")
        return SimpleNamespace(key=self.public_key)


@pytest.fixture(scope="module")
def signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key(), other_private_key


def issue_token(private_key, *, issuer="https://id.example/realms/planner", audience="planner-api",
                subject="owner-123", expires=None, token_type="Bearer", kid="test-key"):
    now = int(time.time())
    claims = {"iss": issuer, "aud": audience, "sub": subject, "typ": token_type,
              "iat": now, "exp": now + 60 if expires is None else expires}
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def configure_verifier(monkeypatch, public_key, issuer="https://id.example/realms/planner"):
    monkeypatch.setenv("KEYCLOAK_ISSUER", issuer)
    monkeypatch.setenv("KEYCLOAK_AUDIENCE", "planner-api")
    monkeypatch.delenv("KEYCLOAK_JWKS_URL", raising=False)
    seen_urls = []
    def client(url):
        seen_urls.append(url)
        return StaticJwks(public_key)
    monkeypatch.setattr(keycloak_identity, "jwks_client", client)
    return seen_urls


def test_keycloak_access_token_is_verified_and_principal_is_issuer_namespaced(monkeypatch, signing_keys):
    private_key, public_key, _ = signing_keys
    issuer = "https://id.example/realms/planner"
    urls = configure_verifier(monkeypatch, public_key, issuer)
    token = issue_token(private_key, issuer=issuer)
    principal = keycloak_identity.principal_from_authorization("Bearer " + token)
    assert principal == sha256((issuer + "\0owner-123").encode()).hexdigest()
    assert urls == [issuer + "/protocol/openid-connect/certs"]
    other_issuer = "https://other.example/realms/planner"
    configure_verifier(monkeypatch, public_key, other_issuer)
    other_principal = keycloak_identity.validate_access_token(issue_token(private_key, issuer=other_issuer))
    assert other_principal != principal


@pytest.mark.parametrize("token_fields", [
    {"expires": 1}, {"issuer": "https://attacker.example/realm"},
    {"audience": "different-api"}, {"token_type": "ID"}, {"kid": "unknown"},
])
def test_keycloak_access_token_rejects_expired_wrong_scope_or_wrong_type(monkeypatch, signing_keys, token_fields):
    private_key, public_key, _ = signing_keys
    configure_verifier(monkeypatch, public_key)
    token = issue_token(private_key, **token_fields)
    with pytest.raises(keycloak_identity.InvalidIdentityToken):
        keycloak_identity.validate_access_token(token)


def test_keycloak_access_token_rejects_bad_signature_and_missing_configuration(monkeypatch, signing_keys):
    private_key, public_key, other_private_key = signing_keys
    configure_verifier(monkeypatch, public_key)
    token = issue_token(other_private_key)
    with pytest.raises(keycloak_identity.InvalidIdentityToken):
        keycloak_identity.validate_access_token(token)
    monkeypatch.delenv("KEYCLOAK_ISSUER")
    with pytest.raises(keycloak_identity.IdentityConfigurationError):
        keycloak_identity.validate_access_token(issue_token(private_key))


def test_remote_keycloak_issuer_and_jwks_must_use_https(monkeypatch, signing_keys):
    private_key, public_key, _ = signing_keys
    configure_verifier(monkeypatch, public_key, "http://identity.example/realms/planner")
    with pytest.raises(keycloak_identity.IdentityConfigurationError, match="HTTPS"):
        keycloak_identity.validate_access_token(issue_token(private_key, issuer="http://identity.example/realms/planner"))
    configure_verifier(monkeypatch, public_key)
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", "http://keys.example/jwks")
    with pytest.raises(keycloak_identity.IdentityConfigurationError, match="HTTPS"):
        keycloak_identity.validate_access_token(issue_token(private_key))


def test_loopback_issuer_can_use_only_the_explicit_internal_keycloak_jwks(monkeypatch, signing_keys):
    private_key, public_key, _ = signing_keys
    issuer = "http://localhost:8081/realms/financial-planner"
    urls = configure_verifier(monkeypatch, public_key, issuer)
    internal_jwks = "http://keycloak:8080/realms/financial-planner/protocol/openid-connect/certs"
    monkeypatch.setenv("KEYCLOAK_JWKS_URL", internal_jwks)
    token = issue_token(private_key, issuer=issuer)
    assert keycloak_identity.validate_access_token(token) == sha256(
        (issuer + "\0owner-123").encode()).hexdigest()
    assert urls == [internal_jwks]
    for rejected in ("http://keys:8080/jwks", "http://keycloak:8081/jwks", "http://keycloak/jwks"):
        monkeypatch.setenv("KEYCLOAK_JWKS_URL", rejected)
        with pytest.raises(keycloak_identity.IdentityConfigurationError, match="HTTPS"):
            keycloak_identity.validate_access_token(token)


def test_challenges_are_hashed_expiring_single_use_and_private_chat_bound():
    challenge = telegram_linking.create_challenge("principal-a", now=1000)
    digest = sha256(challenge["challenge"].encode()).hexdigest()
    with store.engine.connect() as connection:
        row = connection.execute(select(store.telegram_link_challenges)).mappings().one()
    assert row["token_hash"] == digest and challenge["challenge"] not in row["token_hash"]
    with pytest.raises(telegram_linking.TelegramLinkError, match="invalid or expired"):
        telegram_linking.confirm_challenge("not-a-valid-link-code", 123, 123, now=1000)
    with pytest.raises(telegram_linking.TelegramLinkError, match="invalid or expired"):
        telegram_linking.confirm_challenge(challenge["challenge"], 123, -123, now=1000)
    with pytest.raises(telegram_linking.TelegramLinkError, match="invalid or expired"):
        telegram_linking.confirm_challenge(challenge["challenge"], 123, 123, now=1600)
    assert telegram_linking.status("principal-a") == {"connected": False}

    usable = telegram_linking.create_challenge("principal-a", now=2000)
    assert telegram_linking.confirm_challenge(usable["challenge"], 123, 123, now=2001) == {"linked": True}
    with pytest.raises(telegram_linking.TelegramLinkError, match="invalid or expired"):
        telegram_linking.confirm_challenge(usable["challenge"], 123, 123, now=2002)


def test_other_principals_cannot_read_unlink_or_rebind_a_linked_telegram():
    challenge_a = telegram_linking.create_challenge("principal-a", now=1000)
    assert telegram_linking.confirm_challenge(challenge_a["challenge"], 444, 444, now=1001) == {"linked": True}
    assert telegram_linking.status("principal-b") == {"connected": False}
    assert telegram_linking.unlink("principal-b") == {"connected": False}
    assert telegram_linking.status("principal-a")["connected"] is True
    challenge_b = telegram_linking.create_challenge("principal-b", now=1002)
    with pytest.raises(telegram_linking.TelegramLinkError) as collision:
        telegram_linking.confirm_challenge(challenge_b["challenge"], 444, 444, now=1003)
    assert collision.value.code == "telegram_already_linked"
    assert telegram_linking.status("principal-b") == {"connected": False}
    assert telegram_linking.unlink("principal-a") == {"connected": False}
    assert telegram_linking.confirm_challenge(challenge_b["challenge"], 444, 444, now=1004) == {"linked": True}


def test_new_challenge_revokes_older_pending_code():
    first = telegram_linking.create_challenge("principal-a", now=1000)
    second = telegram_linking.create_challenge("principal-a", now=1001)
    with pytest.raises(telegram_linking.TelegramLinkError, match="invalid or expired"):
        telegram_linking.confirm_challenge(first["challenge"], 123, 123, now=1002)
    assert telegram_linking.confirm_challenge(second["challenge"], 123, 123, now=1002) == {"linked": True}
