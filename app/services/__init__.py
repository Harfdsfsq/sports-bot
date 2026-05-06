from __future__ import annotations

"""Service package marker.

Keep this package import-light. Runtime policies are installed explicitly from
CLI/bootstrap code, not from package import side effects.
"""

__all__: list[str] = []
