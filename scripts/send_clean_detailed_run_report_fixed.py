from __future__ import annotations

"""Compatibility wrapper with a correct Telegram API URL.

The clean detailed sender in older commits may build a brace-wrapped URL like
`{https://api.telegram.org/...}`, which urllib rejects as `unknown url type:
{https`. This wrapper replaces only the send function with a URL assembled by
plain string concatenation, avoiding f-string brace escaping mistakes.
"""

from urllib import parse, request

import scripts.send_clean_detailed_run_report as base


def telegram_send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = "https://api.telegram.org/bot" + str(token) + "/sendMessage"
    data = parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = request.Request(url, data=data, method="POST")
        with request.urlopen(req, timeout=20) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200, body[:500]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


base.telegram_send = telegram_send


if __name__ == "__main__":
    raise SystemExit(base.main())
