"""Credential redaction without importing CLI or startup hooks."""
from __future__ import annotations

import logging
import re
from typing import Any

_SECRET_QUERY_RE = re.compile(
    r'(?i)([?&](?:apiKey|apikey|APIkey|api_key|key|appid|token|access_token|auth_token|secret)=)([^&\s\"]+)'
)
_SECRET_HEADER_RE = re.compile(
    r'(?i)((?:authorization|x-auth-token|x-apisports-key|x-rapidapi-key)\s*[:=]\s*)([^,\s\"]+)'
)


def _redact_log_text(value: Any) -> str:
    text = str(value)
    text = re.sub(r'(https://api\.telegram\.org/bot)[^/\s]+', r'\1***', text)
    text = _SECRET_QUERY_RE.sub(r'\1***', text)
    text = _SECRET_HEADER_RE.sub(r'\1***', text)
    return text


class _SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = _redact_log_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _install_log_redaction() -> None:
    redaction_filter = _SecretRedactionFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(redaction_filter)
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)
    for logger_name in ('httpx', 'httpcore'):
        logging.getLogger(logger_name).addFilter(redaction_filter)

