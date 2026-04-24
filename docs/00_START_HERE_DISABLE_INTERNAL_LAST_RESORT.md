# Fix: block internal last-resort publication

## What happened

The bot published:

- `Brisbane Strikers FC — St George Willawong FC`
- `Totals Under 2.5`
- label: `1 лучшая ставка`
- quality mode: `запасной проход после quality-стопоров`
- risk: `single-source, heavy-shrink`

This was not the external controlled fallback. It came from the internal `quality_last_resort` branch inside `app/services/quality.py`.

## Why this is wrong

The internal rescue branch formats the pick as a normal "best bet". That hides the risk and bypasses the controlled fallback wording and staking cap.

## What this patch changes

The main `Run bot` profile now disables all internal rescue publishers using hard thresholds:

```env
QUALITY_EMERGENCY_PUBLISH_ENABLED=false
HISTORICAL_SEGMENT_RELIEF_ENABLED=false
QUALITY_LAST_RESORT_MIN_BOOKS=99
QUALITY_LAST_RESORT_MIN_CONFIDENCE=99
QUALITY_LAST_RESORT_MIN_EV_PCT=999
QUALITY_LAST_RESORT_MIN_EDGE_PCT=999
QUALITY_FALLBACK_MIN_BOOKS_STRICT=99
QUALITY_TOTALS_FALLBACK_MIN_BOOKS=99
```

Now only two publication paths remain:

1. clean normal quality pass from the bot;
2. external `controlled fallback` message with explicit Tier A/B/C risk label and capped stake.

## Expected result

The bot should no longer send messages like:

`⚠️ Режим качества: запасной проход после quality-стопоров.`

as `🔥 1 лучшая ставка`.

If quality returns zero, the fallback script may still publish a low-stake controlled forecast, but it will be clearly marked as controlled fallback.
