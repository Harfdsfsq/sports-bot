"""Repository-local user customizations are intentionally empty.

Runtime preparation now happens through explicit entrypoints:
`app.cli run-once` calls `RuntimePreflight`, and standalone scripts call their
own guards directly. Keeping this module side-effect free prevents every Python
process from installing legacy wrappers just because the repository is on
`PYTHONPATH`.
"""

from __future__ import annotations
