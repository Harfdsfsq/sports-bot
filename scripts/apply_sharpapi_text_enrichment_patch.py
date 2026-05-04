from __future__ import annotations

"""Patch Telegram send points to use optional SharpAPI text enrichment.

The repository has two Telegram paths:
1. app/services/telegram.py for normal app publications;
2. scripts/publish_controlled_fallback.py for controlled fallback picks.

This patch is intentionally conservative. It only enriches already-built text
immediately before sending. It never changes candidate selection, odds parsing,
market validation, staking, or quality gates.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TELEGRAM_PATH = ROOT / "app" / "services" / "telegram.py"
FALLBACK_PATH = ROOT / "scripts" / "publish_controlled_fallback.py"
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-sharpapi-text-enrichment-patch.json"


def patch_telegram_py() -> dict[str, object]:
    result: dict[str, object] = {"path": str(TELEGRAM_PATH.relative_to(ROOT)), "exists": TELEGRAM_PATH.exists(), "changed": False, "status": "skipped"}
    if not TELEGRAM_PATH.exists():
        result["status"] = "missing"
        return result
    text = TELEGRAM_PATH.read_text(encoding="utf-8")
    original = text

    import_line = "from app.services.sharpapi_text_enrichment import enrich_telegram_text\n"
    if import_line not in text:
        marker = "from app.utils import russian_market_name, russian_selection\n"
        if marker in text:
            text = text.replace(marker, marker + import_line, 1)
        else:
            result["status"] = "import_marker_missing"
            return result

    old = '        text = str(message or "").strip()\n        if not text:\n'
    new = (
        '        text = str(message or "").strip()\n'
        '        if text:\n'
        '            try:\n'
        '                text = enrich_telegram_text(text)\n'
        '            except Exception:\n'
        '                pass\n'
        '        if not text:\n'
    )
    if new not in text:
        if old in text:
            text = text.replace(old, new, 1)
        else:
            result["status"] = "send_marker_missing"
            return result

    if text != original:
        TELEGRAM_PATH.write_text(text, encoding="utf-8")
        result.update({"changed": True, "status": "patched"})
    else:
        result.update({"changed": False, "status": "already_patched"})
    return result


def patch_fallback_py() -> dict[str, object]:
    result: dict[str, object] = {"path": str(FALLBACK_PATH.relative_to(ROOT)), "exists": FALLBACK_PATH.exists(), "changed": False, "status": "skipped"}
    if not FALLBACK_PATH.exists():
        result["status"] = "missing"
        return result
    text = FALLBACK_PATH.read_text(encoding="utf-8")
    original = text

    import_block = (
        "\ntry:\n"
        "    from app.services.sharpapi_text_enrichment import enrich_telegram_text\n"
        "except Exception:\n"
        "    def enrich_telegram_text(message):\n"
        "        return str(message or '').strip()\n"
    )
    if "sharpapi_text_enrichment" not in text:
        marker = "from urllib import parse, request\n"
        if marker in text:
            text = text.replace(marker, marker + import_block, 1)
        else:
            result["status"] = "import_marker_missing"
            return result

    # Common controlled fallback direct sender shape.
    replacements = [
        (
            '    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")\n',
            '    try:\n        text = enrich_telegram_text(text)\n    except Exception:\n        pass\n    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")\n',
        ),
        (
            '    data = parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")\n',
            '    try:\n        text = enrich_telegram_text(text)\n    except Exception:\n        pass\n    data = parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")\n',
        ),
        (
            '            data=parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")\n',
            '            data=parse.urlencode({"chat_id": chat_id, "text": enrich_telegram_text(text)}).encode("utf-8")\n',
        ),
    ]
    patched_sender = "enrich_telegram_text(text)" in text and "sharpapi_text_enrichment" in text
    if not patched_sender:
        for old, new in replacements:
            if old in text:
                text = text.replace(old, new, 1)
                patched_sender = True
                break
    if not patched_sender:
        # Last-resort safe marker: most fallback messages are normalized before send.
        old = '    text = normalize_telegram_text(text)\n'
        new = (
            '    text = normalize_telegram_text(text)\n'
            '    try:\n'
            '        text = enrich_telegram_text(text)\n'
            '    except Exception:\n'
            '        pass\n'
        )
        if old in text and new not in text:
            text = text.replace(old, new, 1)
            patched_sender = True

    if not patched_sender:
        result["status"] = "sender_marker_missing"
        return result

    if text != original:
        FALLBACK_PATH.write_text(text, encoding="utf-8")
        result.update({"changed": True, "status": "patched"})
    else:
        result.update({"changed": False, "status": "already_patched"})
    return result


def main() -> int:
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "telegram": patch_telegram_py(),
        "controlled_fallback": patch_fallback_py(),
    }
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    # Never fail the run because text enrichment is optional.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
