from __future__ import annotations

from pathlib import Path
import importlib.util
import re


def _sanitize() -> None:
    try:
        from scripts.sanitize_line_movement_value_waits import main as sanitize_main
        sanitize_main()
    except Exception:
        pass


def _refresh_inventory_truth() -> None:
    """Make the Telegram report read the final 300-row inventory state.

    Some workflow steps repair/restore the high-watermark inventory after the
    earlier coverage-truth report has already been built.  Rebuild target-expand,
    no-shrink and coverage-truth here so the visible report does not say 247/300
    while .data/day_inventory/latest.json already contains 300 rows.
    """
    steps = (
        ("scripts.expand_day_inventory_to_target", "main"),
        ("scripts.guard_day_inventory_no_shrink", "main"),
        ("scripts.backfill_inventory_bookmaker_coverage", "main"),
        ("scripts.bridge_runtime_context_coverage", "main"),
        ("scripts.build_day_inventory_coverage_truth", "main"),
        ("scripts.day_inventory_cumulative_coverage", "main"),
    )
    for module_name, fn_name in steps:
        try:
            module = __import__(module_name, fromlist=[fn_name])
            fn = getattr(module, fn_name, None)
            if callable(fn):
                if module_name.endswith("guard_day_inventory_no_shrink"):
                    try:
                        fn("repair")
                    except TypeError:
                        fn()
                else:
                    fn()
        except Exception:
            pass


def _load_v12():
    target = Path(__file__).with_name("send_harizon_" + "telegram_run_report_v12.py")
    spec = importlib.util.spec_from_file_location("harizon_report_v12_loaded", target)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load report v12")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_int(value) -> int:
    try:
        if value in (None, ""):
            return 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return 0


def _install_report_patch(mod) -> None:
    base_render = mod.render

    def render(payload):
        text = base_render(payload)
        api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
        sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}
        diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
        sport_diag = diag.get("sportlogic") if isinstance(diag.get("sportlogic"), dict) else {}
        enabled = bool(sport.get("enabled") or sport_diag.get("enabled") or _as_int(sport.get("requests")) or _as_int(sport_diag.get("requests")))
        if enabled:
            requests = max(_as_int(sport.get("requests")), _as_int(sport_diag.get("requests")))
            fixtures = max(_as_int(sport.get("fixtures")), _as_int(sport.get("fixtures_fetched")), _as_int(sport_diag.get("fixtures_fetched")), _as_int(sport_diag.get("games_fetched")))
            matched = max(_as_int(sport.get("matched")), _as_int(sport_diag.get("matched")), _as_int(sport_diag.get("events_matched")))
            odds_req = max(_as_int(sport.get("odds_requests")), _as_int(sport_diag.get("odds_requests")))
            offers = max(_as_int(sport.get("offers")), _as_int(sport_diag.get("offers_parsed")))
            errors = max(_as_int(sport.get("errors")), _as_int(sport_diag.get("response_errors")))
            diagnosis = sport.get("diagnosis") or sport_diag.get("diagnosis") or "n/a"
            line = f"• SportLogic: enabled; запросы {requests}; fixtures {fixtures}; matched {matched}; odds req {odds_req}; offers {offers}; ошибок {errors}; diag {diagnosis}.\n"
            text = re.sub(r"• SportLogic: .*?(?:\n|$)", line, text, count=1)
        return text

    mod.render = render
    mod.v9.v8.v7.v5.render = render
    mod.v9.v8.v7.render = render


if __name__ == "__main__":
    _sanitize()
    _refresh_inventory_truth()
    module = _load_v12()
    _install_report_patch(module)
    raise SystemExit(module.v9.v8.v7.v5.main())
