# Final tuning bundle

This bundle applies three focused updates:

1. Align `published` in the run summary with the number of picks actually sent to Telegram.
2. Tighten single-source `xg_*` candidates so weak `sources=1` picks are filtered more aggressively.
3. Upgrade sheet export to produce additional files and support optional webhook-based sync to Google Sheets.

## Optional automatic Google Sheets sync

Use the included `apps_script/sheet_sync.gs` as a Web App in Apps Script.

Script properties:
- `SHEET_ID`
- `SHEET_NAME` (optional)
- `SUMMARY_SHEET_NAME` (optional)
- `MATCHES_SHEET_NAME` (optional)
- `WEBHOOK_TOKEN` (optional but recommended)

Then set these GitHub secrets/variables:
- `GOOGLE_SHEETS_WEBHOOK_URL`
- `GOOGLE_SHEETS_WEBHOOK_TOKEN`

Without the webhook URL, the bot still writes JSON/CSV files into `.data`.
