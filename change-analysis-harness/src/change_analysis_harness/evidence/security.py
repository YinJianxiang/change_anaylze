"""Shared path filtering and evidence redaction helpers."""

from __future__ import annotations

import re
from pathlib import Path


SENSITIVE_DIRECTORY_NAMES = {
    "secrets",
    "secret",
    "credentials",
    "credential",
    "private",
    "certs",
    "keys",
}
SENSITIVE_FILE_NAMES = {
    ".env",
    "credentials.json",
    "service-account.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}
SENSITIVE_NAME_RE = re.compile(r"(?:secret|credential|password|private[-_ ]?key)", re.IGNORECASE)
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.IGNORECASE | re.DOTALL
)
SECRET_ASSIGNMENT_RE = re.compile(
    r'''(?i)(?P<prefix>['"]?(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|authorization|connection[_-]?string)['"]?\s*[:=]\s*)(?P<value>[^\r\n]*)'''
)
BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")


def is_sensitive_path(path: Path | str) -> bool:
    """Return whether a path should be excluded from collection or evidence."""
    candidate = Path(str(path).replace("\\", "/"))
    parts = [part.casefold() for part in candidate.parts]
    name = candidate.name.casefold()
    if any(part in SENSITIVE_DIRECTORY_NAMES for part in parts):
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if name in SENSITIVE_FILE_NAMES:
        return True
    if candidate.suffix.casefold() in SENSITIVE_SUFFIXES:
        return True
    return bool(SENSITIVE_NAME_RE.search(name))


def redact_sensitive_text(text: str) -> str:
    """Redact common secret values while preserving line-level evidence."""
    redacted = PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    redacted = SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]", redacted
    )
    return BEARER_RE.sub(r"\1[REDACTED]", redacted)
