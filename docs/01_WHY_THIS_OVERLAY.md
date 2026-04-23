# Why this overlay exists

The current bot has three practical issues:

1. **data-integrity risk around odds/probability fields**
   - some candidates contain an odds value that does not match the stored implied probability or fair odds;
2. **too much variance**
   - the historical settled profile is still too high-odds / too noisy for a reliable daily publish mode;
3. **not enough saved evidence per run**
   - debugging gets easier when suspicious candidates are extracted into standalone trace files.

This overlay does not claim to magically create profit.
It narrows the publication profile, adds sanity checks, and saves the evidence needed for future improvements.
