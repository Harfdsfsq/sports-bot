from __future__ import annotations

from pathlib import Path

TARGET = Path("app/services/telegram.py")


def main() -> int:
    if not TARGET.exists():
        print(f"skip: {TARGET} not found")
        return 0

    src = TARGET.read_text(encoding="utf-8")
    original = src

    if "from app.services.telegram_i18n import normalize_telegram_text" not in src:
        marker = "from app.utils import russian_market_name, russian_selection\n"
        if marker in src:
            src = src.replace(
                marker,
                marker + "from app.services.telegram_i18n import normalize_telegram_text\n",
                1,
            )
        else:
            # Safe fallback: add after app imports.
            src = src.replace(
                "from app.config import Settings\n",
                "from app.config import Settings\nfrom app.services.telegram_i18n import normalize_telegram_text\n",
                1,
            )

    exact = '        text = str(message or "").strip()\n'
    replacement = '        text = normalize_telegram_text(str(message or "").strip())\n'
    if exact in src and replacement not in src:
        src = src.replace(exact, replacement, 1)
    elif "parts = self._split_message(text)" in src and "normalize_telegram_text(text)" not in src:
        src = src.replace(
            "        parts = self._split_message(text)\n",
            "        text = normalize_telegram_text(text)\n        parts = self._split_message(text)\n",
            1,
        )

    if src != original:
        TARGET.write_text(src, encoding="utf-8")
        print(f"patched: {TARGET}")
    else:
        print(f"already patched: {TARGET}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
