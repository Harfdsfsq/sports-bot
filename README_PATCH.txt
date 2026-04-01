This archive contains targeted replacement files for sports-bot.

What changed:
1. runner.py
   - adds BookiesBootstrapProvider
   - if The Odds API returns zero matches, the runner bootstraps soccer fixtures from BookiesAPI predatapage
   - keeps existing BookiesAPI odds parsing provider for allodds/odds
   - writes bookies_bootstrap stats into debug summary

2. app/providers/bookies_bootstrap.py
   - new lightweight provider
   - fetches fixtures from BookiesAPI predatapage directly
   - builds Match objects so the existing pipeline can continue even when The Odds API is empty or rate-limited

3. run-bot.yml
   - lowers schedule to every 6 hours
   - keeps BOOKIES_API_* secrets wired in

How to apply:
- replace app/services/runner.py
- add app/providers/bookies_bootstrap.py
- replace .github/workflows/run-bot.yml
- commit and push

Important:
- BOOKIES_API_ENABLED secret should be exactly: true
- this patch was built from your logs and the uploaded Google Apps Script integration logic
- I could not run your live APIs from this environment, so treat this as a targeted fallback patch and verify via GitHub Actions logs
