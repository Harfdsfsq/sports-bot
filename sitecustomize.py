from __future__ import annotations

import builtins
import functools
import inspect
import sys
from typing import Any

_TRACKER: dict[str, Any] = {
    "candidate_count": None,
    "message_count": 0,
    "hook_source": None,
}

_ORIG_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()

_TELEGRAM_FN_HINTS = {
    "send",
    "send_message",
    "send_picks",
    "publish",
    "publish_picks",
    "post",
    "post_message",
    "format_message",
    "format_picks",
    "render_message",
    "build_message",
}


def _reset_tracker() -> None:
    _TRACKER["candidate_count"] = None
    _TRACKER["message_count"] = 0
    _TRACKER["hook_source"] = None


def _looks_like_candidate(item: Any) -> bool:
    if item is None:
        return False
    if isinstance(item, dict):
        keys = set(item.keys())
        return bool({"selection", "family", "match_key", "price", "ev_pct"} & keys)
    return any(hasattr(item, attr) for attr in ("selection", "family", "match_key", "ev_pct", "price"))


def _candidate_count_from_obj(obj: Any) -> int | None:
    if isinstance(obj, (list, tuple)) and obj:
        try:
            if all(_looks_like_candidate(item) for item in obj):
                return len(obj)
        except Exception:
            return None
    return None


def _find_candidate_count(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
    for value in kwargs.values():
        count = _candidate_count_from_obj(value)
        if count is not None:
            return count
    for value in args:
        count = _candidate_count_from_obj(value)
        if count is not None:
            return count
    return None


def _wrap_callable(func: Any, *, is_send_like: bool) -> Any:
    if getattr(func, "__publication_fix_wrapped__", False):
        return func

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            count = _find_candidate_count(args, kwargs)
            if count is not None:
                _TRACKER["candidate_count"] = count
                _TRACKER["hook_source"] = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))
            result = await func(*args, **kwargs)
            if is_send_like:
                _TRACKER["message_count"] = int(_TRACKER.get("message_count") or 0) + 1
            return result

        async_wrapper.__publication_fix_wrapped__ = True
        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        count = _find_candidate_count(args, kwargs)
        if count is not None:
            _TRACKER["candidate_count"] = count
            _TRACKER["hook_source"] = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))
        result = func(*args, **kwargs)
        if is_send_like:
            _TRACKER["message_count"] = int(_TRACKER.get("message_count") or 0) + 1
        return result

    wrapper.__publication_fix_wrapped__ = True
    return wrapper


def _patch_telegram_module(mod: Any) -> None:
    if getattr(mod, "__publication_fix_patched__", False):
        return

    for name in dir(mod):
        if name.startswith("_"):
            continue
        try:
            obj = getattr(mod, name)
        except Exception:
            continue

        lname = name.lower()
        is_send_like = lname in {"send", "send_message", "send_picks", "publish", "publish_picks", "post", "post_message"}
        is_format_like = lname in _TELEGRAM_FN_HINTS

        if inspect.isfunction(obj) and (is_send_like or is_format_like):
            setattr(mod, name, _wrap_callable(obj, is_send_like=is_send_like))
            continue

        if inspect.isclass(obj):
            patched_any = False
            for meth_name, meth in list(vars(obj).items()):
                if meth_name.startswith("_"):
                    continue
                lm = meth_name.lower()
                send_like = lm in {"send", "send_message", "send_picks", "publish", "publish_picks", "post", "post_message"}
                format_like = lm in _TELEGRAM_FN_HINTS
                target = None
                descriptor_type = None
                if isinstance(meth, staticmethod):
                    target = meth.__func__
                    descriptor_type = staticmethod
                elif isinstance(meth, classmethod):
                    target = meth.__func__
                    descriptor_type = classmethod
                elif inspect.isfunction(meth):
                    target = meth
                if target is None or not (send_like or format_like):
                    continue
                wrapped = _wrap_callable(target, is_send_like=send_like)
                if descriptor_type is staticmethod:
                    wrapped = staticmethod(wrapped)
                elif descriptor_type is classmethod:
                    wrapped = classmethod(wrapped)
                setattr(obj, meth_name, wrapped)
                patched_any = True
            if patched_any:
                try:
                    setattr(mod, name, obj)
                except Exception:
                    pass

    mod.__publication_fix_patched__ = True


def _patch_runner_module(mod: Any) -> None:
    if getattr(mod, "__publication_fix_patched__", False):
        return

    for name in dir(mod):
        if name.startswith("_"):
            continue
        try:
            obj = getattr(mod, name)
        except Exception:
            continue
        if not inspect.isclass(obj):
            continue
        run_once = getattr(obj, "run_once", None)
        if run_once is None or getattr(run_once, "__publication_fix_wrapped__", False):
            continue
        if not inspect.iscoroutinefunction(run_once):
            continue

        @functools.wraps(run_once)
        async def wrapped(self: Any, *args: Any, __orig=run_once, **kwargs: Any) -> Any:
            _reset_tracker()
            summary = await __orig(self, *args, **kwargs)
            if isinstance(summary, dict):
                picks_sent = _TRACKER.get("candidate_count")
                messages_sent = int(_TRACKER.get("message_count") or 0)
                if picks_sent is not None:
                    summary.setdefault("published_internal", summary.get("published"))
                    summary.setdefault("published_to_telegram_internal", summary.get("published_to_telegram"))
                    summary["telegram_picks_sent"] = picks_sent
                    summary["telegram_messages_sent"] = messages_sent
                    summary["telegram_hook_source"] = _TRACKER.get("hook_source")
                    summary["published"] = picks_sent
                    summary["published_to_telegram"] = picks_sent
                elif messages_sent:
                    summary["telegram_messages_sent"] = messages_sent
            return summary

        wrapped.__publication_fix_wrapped__ = True
        setattr(obj, "run_once", wrapped)

    mod.__publication_fix_patched__ = True


def _maybe_patch(module_name: str) -> None:
    mod = sys.modules.get(module_name)
    if mod is None or module_name in _PATCHED_MODULES:
        return
    if module_name == "app.services.telegram":
        _patch_telegram_module(mod)
        _PATCHED_MODULES.add(module_name)
    elif module_name == "app.services.runner":
        _patch_runner_module(mod)
        _PATCHED_MODULES.add(module_name)


def _import_hook(name: str, globals: Any = None, locals: Any = None, fromlist: tuple[str, ...] = (), level: int = 0) -> Any:
    module = _ORIG_IMPORT(name, globals, locals, fromlist, level)
    if name.startswith("app.services.telegram"):
        _maybe_patch("app.services.telegram")
    elif name.startswith("app.services.runner"):
        _maybe_patch("app.services.runner")
    else:
        if "app.services.telegram" in sys.modules:
            _maybe_patch("app.services.telegram")
        if "app.services.runner" in sys.modules:
            _maybe_patch("app.services.runner")
    return module


builtins.__import__ = _import_hook
