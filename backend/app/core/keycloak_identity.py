"""Verify Keycloak bearer access tokens and derive the principal from ``sub``."""
from functools import lru_cache
import hashlib
import os
from urllib.parse import urlsplit

import jwt
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError


class IdentityConfigurationError(RuntimeError):
    pass


class IdentityProviderUnavailable(RuntimeError):
    pass


class InvalidIdentityToken(ValueError):
    pass


@lru_cache(maxsize=4)
def jwks_client(url):
    return jwt.PyJWKClient(url, cache_keys=True, timeout=3)


def _secure_configured_url(value, *, allow_internal_keycloak=False):
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise IdentityConfigurationError("Invalid Keycloak issuer or JWKS URL") from exc
    local_http = parsed.scheme == "http" and hostname in ("localhost", "127.0.0.1", "::1")
    internal_keycloak_http = (allow_internal_keycloak and parsed.scheme == "http"
                              and hostname == "keycloak" and port == 8080)
    if ((parsed.scheme != "https" and not local_http and not internal_keycloak_http)
            or not hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise IdentityConfigurationError("Keycloak issuer and JWKS URLs must use HTTPS (HTTP is allowed only on loopback or the configured keycloak:8080 JWKS service)")


def validate_access_token(token):
    """Validate a Keycloak RS256 access token; return its stable subject ID."""
    issuer = os.environ.get("KEYCLOAK_ISSUER", "").strip()
    audience = os.environ.get("KEYCLOAK_AUDIENCE", "").strip()
    if not issuer or not audience:
        raise IdentityConfigurationError("Keycloak issuer and audience must be configured")
    _secure_configured_url(issuer)
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise InvalidIdentityToken("Invalid bearer token")
    jwks_url = os.environ.get("KEYCLOAK_JWKS_URL", "").strip()
    if not jwks_url:
        jwks_url = issuer.rstrip("/") + "/protocol/openid-connect/certs"
    _secure_configured_url(jwks_url, allow_internal_keycloak=True)
    try:
        key = jwks_client(jwks_url).get_signing_key_from_jwt(token).key
    except PyJWKClientConnectionError as exc:
        raise IdentityProviderUnavailable("Keycloak signing keys are unavailable") from exc
    except (PyJWKClientError, PyJWTError, ValueError, TypeError) as exc:
        raise InvalidIdentityToken("Invalid bearer token") from exc
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
                "require": ["exp", "iat", "iss", "aud", "sub", "typ"],
            },
        )
    except PyJWTError as exc:
        raise InvalidIdentityToken("Invalid bearer token") from exc
    subject = claims.get("sub")
    if claims.get("typ") != "Bearer" or not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
        raise InvalidIdentityToken("Invalid bearer token")
    # A subject is stable within one issuer. Namespacing prevents accidental
    # identity collisions if the configured Keycloak realm changes later.
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


def principal_from_authorization(value):
    """Extract a bearer value without accepting identity headers or request fields."""
    if not isinstance(value, str):
        raise InvalidIdentityToken("Bearer access token required")
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise InvalidIdentityToken("Bearer access token required")
    return validate_access_token(token.strip())
