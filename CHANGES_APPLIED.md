# Applied changes

## Done
- Fixed strict target bookmaker filtering so only whitelisted books are evaluated.
- Added bookmaker alias normalization (`Unibet (UK)`, `Unibet FR`, `bet365 sportsbook` -> canonical keys).
- Replaced coarse `offer_rejected` counting with detailed rejection reasons.
- Exposed market/model/final probability layers and explicit `model_mode` on candidates.
- Improved debug summary with candidate mode counts.
- Fixed overly aggressive team-name normalization regex that was corrupting tokens like `Vasco`, `Werder`, `Sociedad`.
- Added richer HTTP/payload diagnostics for `odds_api_io` and `sstats`.
- Made `odds_api_io` parsing more tolerant to alternate payload shapes (`bookmakers` as list/dict, `markets` nested, `odds/outcomes/prices` variations).

## Not fully completed
- `bookiesapi.com` was **not safely integrated yet** because the public URL provided does not expose usable endpoint/auth/payload documentation in this environment. The homepage only identifies it as a private sports/bookmaker API, so wiring a production parser without the actual docs would be guesswork.
