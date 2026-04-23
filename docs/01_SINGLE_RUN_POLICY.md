# Single-run policy

The whole cycle should be treated as one unit:

- fetch inputs;
- build candidates;
- apply quality;
- publish or skip;
- summarize;
- audit integrity;
- package artifacts.

This overlay enforces that at the workflow level:
- one workflow run;
- one latest run summary;
- one latest integrity audit;
- one compact single-run bundle.

It does **not** rewrite the internal application pipeline by itself.
It makes the operational side of the bot consistent and easier to analyze.
