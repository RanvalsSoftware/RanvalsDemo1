"""Small, dependency-free helpers used by the Meta Lead Ads integration.

This module intentionally has no Odoo imports.  Keeping payload parsing and
signature verification here makes those security-sensitive operations easy to
exercise without booting an Odoo registry.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


_HUB_SIGNATURE_RE = re.compile(r"sha256=([0-9a-fA-F]{64})", re.ASCII)
_GRAPH_VERSION_RE = re.compile(r"v[1-9][0-9]*\.[0-9]+", re.ASCII)
_EVENT_ID_FIELDS = ("page_id", "form_id", "ad_id", "adgroup_id")
_REDACTION_MARKER = "[REDACTED]"
_FIELD_TRANSLITERATION = str.maketrans({"\u0131": "i"})


def _string_value(value: Any) -> str:
    """Return a text value without guessing encodings or container formats."""

    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return ""


def normalize_meta_field_name(value: Any) -> str:
    """Convert a Meta form field name to a stable Unicode-aware snake name.

    Diacritics are removed where Unicode defines a decomposition, while letters
    from non-Latin alphabets are retained.  Punctuation and runs of whitespace
    become a single underscore.
    """

    text = _string_value(value)
    if not text:
        return ""

    normalized = unicodedata.normalize("NFKD", text).casefold().translate(
        _FIELD_TRANSLITERATION
    )
    pieces = []
    separator_pending = False
    for character in normalized:
        if unicodedata.combining(character):
            continue
        if character.isalnum():
            if separator_pending and pieces:
                pieces.append("_")
            pieces.append(character)
            separator_pending = False
        else:
            separator_pending = bool(pieces)
    return "".join(pieces)


def field_data_to_dict(field_data: Any) -> dict[str, list[Any]]:
    """Turn Meta's ``field_data`` array into normalized lists of answers.

    Every result value is a list, even for single-answer questions.  Repeated
    field entries are merged in arrival order, so checkbox answers and unusual
    forms with duplicate field records never lose data.  Malformed records are
    ignored; a scalar ``values`` member is accepted as one answer.
    """

    if not isinstance(field_data, Sequence) or isinstance(
        field_data, (str, bytes, bytearray, memoryview)
    ):
        return {}

    result: dict[str, list[Any]] = {}
    for record in field_data:
        if not isinstance(record, Mapping):
            continue
        name = normalize_meta_field_name(record.get("name"))
        if not name:
            continue

        raw_values = record.get("values", [])
        if isinstance(raw_values, (list, tuple)):
            values = list(raw_values)
        elif raw_values is None:
            values = []
        else:
            values = [raw_values]
        result.setdefault(name, []).extend(values)
    return result


def first_field_value(
    fields: Any, *names: Any, default: Any = None
) -> Any:
    """Return the first non-blank answer found under the requested names.

    Names are normalized in the same way as :func:`field_data_to_dict` keys.
    Numeric zero and ``False`` are valid answers; ``None`` and blank strings are
    skipped.  ``default`` is returned for malformed input or when no answer is
    available.
    """

    if not isinstance(fields, Mapping):
        return default

    for candidate_name in names:
        name = normalize_meta_field_name(candidate_name)
        if not name or name not in fields:
            continue
        raw_values = fields[name]
        values = raw_values if isinstance(raw_values, (list, tuple)) else [raw_values]
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return default


def normalize_email(value: Any) -> str:
    """Normalize a user-entered email for CRM storage and comparisons."""

    text = _string_value(value)
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).strip().casefold()


def normalize_phone(value: Any) -> str:
    """Keep a phone's digits and normalize its international prefix.

    A leading ``+`` is retained and a leading ``00`` is converted to ``+``.
    Unicode decimal digits (for example full-width digits) are converted to
    their ASCII equivalents.  This deliberately does not assume a country code.
    """

    text = _string_value(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).strip()
    has_plus = text.startswith("+")
    digits = []
    for character in text:
        try:
            digits.append(str(unicodedata.decimal(character)))
        except (TypeError, ValueError):
            continue
    number = "".join(digits)
    if not number:
        return ""
    if has_plus:
        return "+" + number
    if number.startswith("00") and len(number) > 2:
        return "+" + number[2:]
    return number


def verify_hub_signature(payload: Any, signature: Any, app_secret: Any) -> bool:
    """Verify a Meta ``X-Hub-Signature-256`` header in constant time.

    ``payload`` must be the exact raw request body.  Strings are encoded as
    UTF-8 for convenient standalone use; web controllers should pass raw bytes.
    Any missing or malformed input safely returns ``False``.
    """

    if isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    elif isinstance(payload, (bytes, bytearray, memoryview)):
        payload_bytes = bytes(payload)
    else:
        return False

    if isinstance(app_secret, str):
        secret_bytes = app_secret.encode("utf-8")
    elif isinstance(app_secret, (bytes, bytearray, memoryview)):
        secret_bytes = bytes(app_secret)
    else:
        return False
    if not secret_bytes:
        return False

    if isinstance(signature, (bytes, bytearray, memoryview)):
        try:
            signature_text = bytes(signature).decode("ascii")
        except UnicodeDecodeError:
            return False
    elif isinstance(signature, str):
        signature_text = signature
    else:
        return False

    match = _HUB_SIGNATURE_RE.fullmatch(signature_text)
    if match is None:
        return False
    supplied_digest = match.group(1).lower()
    expected_digest = hmac.new(
        secret_bytes, payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(supplied_digest, expected_digest)


def validate_graph_version(version: Any) -> bool:
    """Return whether ``version`` has a safe Meta Graph form such as ``v23.0``."""

    return isinstance(version, str) and _GRAPH_VERSION_RE.fullmatch(version) is not None


def _safe_identifier(value: Any) -> str | None:
    """Convert a JSON identifier to non-blank text, rejecting containers/bools."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _safe_created_time(value: Any) -> int | str | None:
    """Allow the two timestamp representations emitted by Graph API."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def extract_leadgen_events(payload: Any) -> list[dict[str, Any]]:
    """Extract all valid ``leadgen`` changes from a Meta page webhook.

    Only identifiers and ``created_time`` are copied.  Arbitrary/nested webhook
    content is never forwarded to callers.  ``page_id`` falls back to the entry
    id, as permitted by Meta's page webhook shape.  Changes without a usable
    ``leadgen_id`` are skipped.
    """

    if not isinstance(payload, Mapping):
        return []
    object_name = payload.get("object")
    if object_name is not None and object_name != "page":
        return []
    entries = payload.get("entry")
    if not isinstance(entries, (list, tuple)):
        return []

    events: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        entry_page_id = _safe_identifier(entry.get("id"))
        changes = entry.get("changes")
        if not isinstance(changes, (list, tuple)):
            continue
        for change in changes:
            if not isinstance(change, Mapping) or change.get("field") != "leadgen":
                continue
            value = change.get("value")
            if not isinstance(value, Mapping):
                continue
            leadgen_id = _safe_identifier(value.get("leadgen_id"))
            if leadgen_id is None:
                continue

            event: dict[str, Any] = {"leadgen_id": leadgen_id}
            for field_name in _EVENT_ID_FIELDS:
                identifier = _safe_identifier(value.get(field_name))
                if identifier is not None:
                    event[field_name] = identifier
            if "page_id" not in event and entry_page_id is not None:
                event["page_id"] = entry_page_id
            created_time = _safe_created_time(value.get("created_time"))
            if created_time is not None:
                event["created_time"] = created_time
            events.append(event)
    return events


def redact_secret(text: Any, *secrets: Any) -> str:
    """Replace exact occurrences of one or more secrets with a fixed marker.

    Replacement is performed in one pass.  This handles overlapping secrets
    predictably and prevents a secret equal to part of the marker from altering
    the marker itself.  Empty secrets are ignored.
    """

    if text is None:
        rendered_text = ""
    elif isinstance(text, str):
        rendered_text = text
    elif isinstance(text, (bytes, bytearray, memoryview)):
        rendered_text = bytes(text).decode("utf-8", errors="replace")
    else:
        try:
            rendered_text = str(text)
        except Exception:
            rendered_text = ""

    secret_values = set()
    for secret in secrets:
        if secret is None:
            continue
        if isinstance(secret, str):
            rendered_secret = secret
        elif isinstance(secret, (bytes, bytearray, memoryview)):
            rendered_secret = bytes(secret).decode("utf-8", errors="replace")
        else:
            try:
                rendered_secret = str(secret)
            except Exception:
                continue
        if rendered_secret:
            secret_values.add(rendered_secret)
    if not secret_values:
        return rendered_text

    alternatives = sorted(secret_values, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(secret) for secret in alternatives))
    return pattern.sub(_REDACTION_MARKER, rendered_text)
