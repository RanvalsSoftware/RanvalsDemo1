"""Dependency-free security helpers for the public attendance flow.

The HTTP layer must pass Werkzeug's trusted ``remote_addr``. Forwarding
headers are deliberately never parsed here.
"""

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit


PIN_ROUNDS = 600_000
MIN_PIN_ROUNDS = 300_000
MAX_PIN_ROUNDS = 1_500_000
MAX_INTENT_TTL = 900
MAX_NETWORKS = 64
MAX_BASE_URL_LENGTH = 2_048

PIN_RE = re.compile(r"^[0-9]{6,12}$", re.ASCII)
CODE_RE = re.compile(r"^[A-Z0-9]{4,20}$", re.ASCII)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$", re.ASCII)
B64_RE = re.compile(r"^[A-Za-z0-9_-]+$", re.ASCII)
DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$", re.ASCII)


def normalize_code(code):
    value = str(code or "").strip().upper()
    return value if CODE_RE.fullmatch(value) else ""


def hash_pin(pin, *, rounds=PIN_ROUNDS):
    if not isinstance(pin, str) or not PIN_RE.fullmatch(pin):
        raise ValueError("PIN, 6–12 rakamdan oluşmalıdır.")
    if not MIN_PIN_ROUNDS <= rounds <= MAX_PIN_ROUNDS:
        raise ValueError("Geçersiz PIN özetleme maliyeti.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def _parse_pin_hash(stored):
    if not isinstance(stored, str) or len(stored) > 256:
        raise ValueError
    scheme, raw_rounds, raw_salt, raw_digest = stored.split("$")
    rounds = int(raw_rounds)
    salt = bytes.fromhex(raw_salt)
    expected = bytes.fromhex(raw_digest)
    if (
        scheme != "pbkdf2_sha256"
        or not MIN_PIN_ROUNDS <= rounds <= MAX_PIN_ROUNDS
        or len(salt) != 16
        or len(expected) != 32
    ):
        raise ValueError
    return rounds, salt, expected


def verify_pin(pin, stored):
    try:
        rounds, salt, expected = _parse_pin_hash(stored)
        if not isinstance(pin, str) or not PIN_RE.fullmatch(pin):
            return False
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), salt, rounds)
        if rounds < PIN_ROUNDS:
            # Legacy hashes remain usable for migration without becoming a
            # timing oracle against unknown codes, which use the current cost.
            hashlib.pbkdf2_hmac(
                "sha256",
                pin.encode("ascii"),
                b"\x00" * 16,
                PIN_ROUNDS - rounds,
            )
        return hmac.compare_digest(digest, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def pin_needs_rehash(stored):
    try:
        rounds, _salt, _expected = _parse_pin_hash(stored)
        return rounds < PIN_ROUNDS
    except (AttributeError, TypeError, ValueError):
        return True


def is_valid_pin_hash(stored):
    try:
        _parse_pin_hash(stored)
        return True
    except (AttributeError, TypeError, ValueError):
        return False


# Unknown personnel codes still execute exactly the current KDF cost.
DUMMY_PIN_HASH = f"pbkdf2_sha256${PIN_ROUNDS}${'00' * 16}${'00' * 32}"


def digest_token(value):
    if not isinstance(value, str):
        raise TypeError("Token metin olmalıdır.")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def normalized_ip(value):
    addr = ipaddress.ip_address(value)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def parse_networks(text):
    """Parse a small, explicit allowlist of public source networks.

    Empty input intentionally returns an empty list so callers fail closed.
    Private/proxy addresses are rejected because accepting a reverse proxy's
    internal address would accidentally authorize every internet client.
    """
    result = []
    seen = set()
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        network = ipaddress.ip_network(line, strict=True)
        if network.prefixlen < (24 if network.version == 4 else 64):
            raise ValueError("IPv4 için /24 veya daha dar; IPv6 için /64 veya daha dar ağ kullanın.")
        if (
            not network.is_global
            or network.is_multicast
            or network.is_unspecified
            or network.is_reserved
            or network.is_loopback
            or network.is_link_local
            or network.is_private
        ):
            raise ValueError("Yerel/proxy IP değil, işletmenin genel internet çıkış IP adresini girin.")
        key = (network.version, int(network.network_address), network.prefixlen)
        if key not in seen:
            result.append(network)
            seen.add(key)
        if len(result) > MAX_NETWORKS:
            raise ValueError(f"En fazla {MAX_NETWORKS} ağ tanımlanabilir.")
    return result


def is_allowed_ip(value, text):
    try:
        ip = normalized_ip(value)
        return (
            ip.is_global
            and not ip.is_multicast
            and not ip.is_unspecified
            and not ip.is_reserved
            and not ip.is_loopback
            and not ip.is_link_local
            and not ip.is_private
            and any(ip.version == net.version and ip in net for net in parse_networks(text))
        )
    except (TypeError, ValueError):
        return False


def validate_base_url(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_BASE_URL_LENGTH
        or any(char.isspace() for char in value)
        or "\\" in value
        or "?" in value
        or "#" in value
    ):
        raise ValueError("Geçerli bir HTTPS adresi girin.")
    parsed = urlsplit(value or "")
    try:
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("Geçerli bir HTTPS portu girin.") from exc
    hostname = parsed.hostname or ""
    try:
        ipaddress.ip_address(hostname)
        dns_valid = True
    except ValueError:
        try:
            ascii_hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            dns_valid = False
        else:
            dns_valid = len(ascii_hostname) <= 253 and all(
                DNS_LABEL_RE.fullmatch(label)
                for label in ascii_hostname.rstrip(".").split(".")
            )
    expected_netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None:
        expected_netloc += f":{parsed.port}"
    if (
        parsed.scheme != "https"
        or not hostname
        or not dns_valid
        or parsed.netloc.lower() != expected_netloc.lower()
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("Temel adres https://demo-alan-adi şeklinde olmalı; yol veya gizli bilgi içermemeli.")
    return value.rstrip("/")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value):
    if not isinstance(value, str) or not B64_RE.fullmatch(value):
        raise ValueError
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_intent(secret, values, ttl=300, now=None):
    if not isinstance(ttl, int) or not 1 <= ttl <= MAX_INTENT_TTL:
        raise ValueError("Geçersiz işlem süresi.")
    clock = int(time.time() if now is None else now)
    payload = dict(values, iat=clock, exp=clock + ttl, nonce=secrets.token_urlsafe(24))
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("ascii"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def verify_intent(secret, token, now=None):
    if not isinstance(token, str) or len(token) > 4096:
        raise ValueError("Geçersiz işlem isteği.")
    try:
        body, signature = token.split(".")
        if not B64_RE.fullmatch(signature):
            raise ValueError
        expected = _b64(hmac.new(secret.encode("ascii"), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            raise ValueError
        data = json.loads(_unb64(body))
        if not isinstance(data, dict):
            raise ValueError
        clock = int(time.time() if now is None else now)
        issued = data.get("iat")
        expires = data.get("exp")
        if (
            not isinstance(issued, int)
            or not isinstance(expires, int)
            or expires <= clock
            or issued > clock + 30
            or expires < issued
            or expires - issued > MAX_INTENT_TTL
        ):
            raise ValueError
        if not TOKEN_RE.fullmatch(str(data.get("nonce", ""))):
            raise ValueError
        return data
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError("İşlem doğrulanamadı; sayfayı yenileyin.") from exc
