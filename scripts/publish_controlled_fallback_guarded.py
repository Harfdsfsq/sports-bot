from __future__ import annotations

import atexit
import json
import os
from typing import Any

# Provider evidence that counts as a real (hard) context. Kept in sync with
# scripts/patch_publication_safety_contract.py.
HARD_CONTEXT_TOKENS = (
    'bzzoiro_stats', 'bzzoiro_prediction', 'bzzoiro_odds_comparison', 'odds_comparison',
    'event_stats', 'event_prediction', 'sstats_xg', 'sstats_form', 'sstats_team_form',
    'team_form_index', 'pre_match_home_xg', 'pre_match_away_xg', 'actual_home_xg',
    'actual_away_xg', 'home_xg', 'away_xg', 'xg_live',
)

_RELIEF_DIAGNOSTICS: list[dict[str, Any]] = []
_RELIEF_INSTALL_EVENTS: list[dict[str, Any]] = []
_RELIEF_INSTALL_COUNT = [0]
_RELIEF_DIAGNOSTICS_PATH = os.path.join('.data', 'exports', 'latest-b-relief-diagnostics.json')


def _record_install_event(stage: str, status: str, detail: str = '') -> None:
    _RELIEF_INSTALL_EVENTS.append({'stage': stage, 'status': status, 'detail': str(detail)[:400]})


def _relief_status() -> str:
    if not any(event.get('status') == 'installed' for event in _RELIEF_INSTALL_EVENTS):
        return 'relief_never_installed'
    if not _RELIEF_DIAGNOSTICS:
        return 'installed_but_never_evaluated'
    if any(entry.get('passed') for entry in _RELIEF_DIAGNOSTICS):
        return 'evaluated_with_passes'
    return 'evaluated_all_rejected'


def _write_relief_diagnostics() -> None:
    """Persist whether the B-tier relief was installed, ran and fired.

    This must be written unconditionally. The previous version returned early on
    an empty list, so "the relief rejected every candidate", "the relief never
    got called" and "the relief was never installed because an unrelated import
    failed" all produced the same missing file.
    """
    try:
        from collections import Counter
        from datetime import datetime, timezone
        deduped: dict[str, dict[str, Any]] = {}
        for entry in _RELIEF_DIAGNOSTICS:
            key = '|'.join(str(entry.get(name)) for name in ('match', 'selection', 'point'))
            if key not in deduped:
                deduped[key] = entry
        rows = list(deduped.values())
        failed: Counter[str] = Counter()
        for entry in rows:
            for name in entry.get('failed_conditions') or []:
                failed[str(name)] += 1
        payload = {
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': _relief_status(),
            'install_events': _RELIEF_INSTALL_EVENTS,
            'install_count': _RELIEF_INSTALL_COUNT[0],
            'evaluated_candidates': len(rows),
            'evaluation_calls': len(_RELIEF_DIAGNOSTICS),
            'passed_count': sum(1 for entry in rows if entry.get('passed')),
            'failed_condition_counts': dict(failed.most_common()),
            'thresholds': {
                'min_books': 2,
                'min_odds_sources': 1,
                'min_context_sources': 1,
                'require_hard_context': True,
                'min_ev_pct': _env_num('HARIZON_B_RELIEF_MIN_EV_PCT', 2.0),
                'min_edge_pp': _env_num('HARIZON_B_RELIEF_MIN_EDGE_PP', 1.0),
                'min_odds': _env_num('HARIZON_B_RELIEF_MIN_ODDS', 1.70),
                'max_odds': _env_num('HARIZON_B_RELIEF_MAX_ODDS', 3.20),
            },
            'rows': rows[:200],
        }
        os.makedirs(os.path.dirname(_RELIEF_DIAGNOSTICS_PATH), exist_ok=True)
        with open(_RELIEF_DIAGNOSTICS_PATH, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
    except Exception:
        pass


atexit.register(_write_relief_diagnostics)


def _force_runtime_publication_contract() -> None:
    overrides={
        'PUBLISH_TIER_A_MIN_ODDS_SOURCES':'2','PUBLISH_TIER_A_MIN_BOOKS':'2','PUBLISH_TIER_A_MIN_CONTEXT_SOURCES':'2',
        'PUBLISH_TIER_B_MIN_ODDS_SOURCES':'1','PUBLISH_TIER_B_MIN_BOOKS':'2','PUBLISH_TIER_B_MIN_CONTEXT_SOURCES':'1',
        'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS':'2','CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES':'1','CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES':'1',
        'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM':'false','CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM':'false',
        'CONTROLLED_FALLBACK_ALLOW_MARKET_IMPLIED_XG_FOR_B_TIER':'true','CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS':'true','CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS':'true',
        'CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT':'true',
        # Must match _b_tier_testing_floor. The relief cannot strip
        # tier_b_canonical_edge_below_min / _ev_below_min, so a higher bar here
        # silently overrode the relief and blocked every promoted candidate.
        'CONTROLLED_FALLBACK_TIER_B_MIN_EDGE_PP':'1.0','CONTROLLED_FALLBACK_TIER_B_MIN_EV_PCT':'2.0','CONTROLLED_FALLBACK_ALLOW_VALUE_ALIVE_HIGH_DRIFT':'true',
        'CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EV_PCT':'3.0','CONTROLLED_FALLBACK_CURRENT_RECHECK_MIN_EDGE_PP':'1.5','CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED':'5','CONTROLLED_FALLBACK_DAILY_MAX_B_TIER':'5',
        'A_TIER_TARGETED_ENRICHMENT_ENABLED':'true','BZZOIRO_TARGETED_ODDS_CONFIRMATION_ENABLED':'true','SSTATS_TARGETED_CONTEXT_PROJECTION_ENABLED':'true','HIGH_VALUE_FAST_RECHECK_ENABLED':'true',
        'DAY_INVENTORY_TARGET_SIZE':'300','DAY_INVENTORY_MAX_MATCHES':'300','DAY_INVENTORY_FORCE_FULL_300':'true','DAY_INVENTORY_FORCE_TOP_300':'true','DAY_INVENTORY_MULTI_SOURCE_MAX_MATCHES':'300',
        'HARIZON_FULL_INVENTORY_PROVIDER_TARGETS':'300','HARIZON_RUNTIME_MATCH_RECOVERY_MIN_FILTERED_MATCHES':'300','HARIZON_RUNTIME_MATCH_RECOVERY_MAX_MATCHES':'300','HARIZON_RUNTIME_MATCH_RECOVERY_WINDOW_HOURS':'36',
        'CONTEXT_ENRICHMENT_MATCH_LIMIT':'300','PREMIUM_CONTEXT_SHORTLIST_LIMIT':'300','CONTEXT_ENRICHMENT_REQUIRES_OFFERS':'false',
        'ODDS_API_IO_PER_RUN_MAX':'200','ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN':'200','ODDS_API_IO_REQUESTS_MAX_PER_RUN':'200','ODDS_API_IO_ACCOUNT1_PER_RUN_MAX':'100','ODDS_API_IO_ACCOUNT2_PER_RUN_MAX':'100','ODDS_API_IO_MATCH_LIMIT':'300','ODDS_API_IO_ODDS_MATCH_LIMIT':'300','ODDS_API_IO_MAX_ODDS_EVENTS_PER_RUN':'300','MAX_MATCHES_FOR_ODDS_FETCH':'520','ODDS_API_IO_FORCE_CURRENT_RECHECK_FOR_FALLBACK':'true',
        'BZZOIRO_PER_RUN_MAX':'180','BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN':'180','BZZOIRO_CONTEXT_MATCH_LIMIT':'300','BZZOIRO_ODDS_MATCH_LIMIT':'300','BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT':'300','BZZOIRO_TARGETED_ODDS_DETAIL_LIMIT':'160','BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT':'160','BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS':'160','BZZOIRO_RUNTIME_PROVIDER_DEADLINE_SECONDS':'220',
        'SSTATS_PER_RUN_MAX':'150','SSTATS_REQUESTS_MAX_PER_RUN':'150','SSTATS_MAX_HTTP_REQUESTS_PER_RUN':'150','SSTATS_CONTEXT_MATCH_LIMIT':'300','SSTATS_DEEP_CONTEXT_MATCH_LIMIT':'150',
        'DAY_INVENTORY_ENABLE_SPORTLOGIC':'true','ENABLE_SPORTLOGIC':'true','SPORTLOGIC_ENABLED':'true','SPORTLOGIC_CONTROLLED_ODDS_ENABLED':'true','SPORTLOGIC_PER_RUN_MAX':'120','SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN':'120','SPORTLOGIC_REQUEST_BUDGET_GRANTED':'120','SPORTLOGIC_MATCH_LIMIT':'300','SPORTLOGIC_CONTEXT_MATCH_LIMIT':'300','SPORTLOGIC_ODDS_MATCH_LIMIT':'160','SPORTLOGIC_ONLY_IF_PRIMARY_ODDS_EMPTY':'false','SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD':'false'
    }
    for k,v in overrides.items(): os.environ[k]=v

def _run_step(module_name:str)->None:
    try:
        m=__import__(module_name,fromlist=['main']); fn=getattr(m,'main',None)
        if callable(fn): fn()
    except SystemExit: pass
    except Exception: pass

def _repair_runtime_artifacts_before_fallback()->None:
    for m in ('scripts.apply_odds_budget_boost_policy','scripts.maximize_inventory_coverage_runtime','scripts.bridge_runtime_context_coverage','scripts.build_context_source_index','scripts.build_day_inventory_coverage_truth','scripts.harizon_a_tier_coverage_plan','scripts.harizon_a_tier_targeted_enrichment_queue','scripts.target_fallback_provider_enrichment','scripts.apply_a_tier_targeted_provider_env','scripts.persist_bzzoiro_runtime_artifacts','scripts.bzzoiro_targeted_odds_confirmation','scripts.bzzoiro_targeted_odds_detail_fetch','scripts.trace_bzzoiro_report_source','scripts.project_sstats_context_into_candidates','scripts.replace_rescue_proxy_placeholder_xg','scripts.day_inventory_cumulative_coverage'):
        _run_step(m)

def _apply_focused_alpha_policy()->None:
    try:
        from app.services.focused_alpha_runtime_policy import apply; apply(force=True)
    except Exception: pass
    _force_runtime_publication_contract()

def _build_focused_alpha_decisions()->None:
    try:
        from scripts.build_focused_alpha_decisions_v2 import main as build_decisions; build_decisions()
        from app.services.focused_alpha_learning_ledger import update_learning_ledger; update_learning_ledger()
    except Exception: pass

def _num(v:Any,d:float=0.0)->float:
    try: return float(str(v).replace(',','.')) if v not in (None,'') else d
    except Exception: return d

def _int(v:Any,d:int=0)->int:
    try:
        if isinstance(v,(list,tuple,set,dict)): return len(v)
        return int(float(str(v).replace(',','.'))) if v not in (None,'') else d
    except Exception: return d

def _env_num(name:str,default:float)->float:
    return _num(os.getenv(name),default)

def _has_hard_context(candidate:dict[str,Any],metrics:dict[str,Any])->bool:
    try:
        text=json.dumps({'candidate':candidate,'metrics':metrics},ensure_ascii=False,sort_keys=True,default=str).lower()
    except Exception:
        text=f'{candidate}{metrics}'.lower()
    return any(token in text for token in HARD_CONTEXT_TOKENS)

def _candidate_label(candidate:dict[str,Any])->str:
    for key in ('match_key','canonical_match_id','event_key'):
        value=candidate.get(key)
        if value: return str(value)
    home=candidate.get('home_team') or candidate.get('home') or '?'
    away=candidate.get('away_team') or candidate.get('away') or '?'
    return f'{home} - {away}'

def _b_tier_testing_floor(candidate:dict[str,Any],metrics:dict[str,Any])->bool:
    """RULES.txt B-cover: 1 line source, 2 bookmakers, 1 *real* context.

    The old version demanded 2 context sources, which no match in the day
    inventory ever has (see .data/exports/latest-two-plus-coverage-report.json:
    context_2plus_sources = 0/300), so the relief never fired and nothing was
    published. Volume is bought back with a stricter definition of a context:
    a hard provider context is now mandatory, because market_signal-only picks
    run at -11.6% ROI while sstats_form runs at +54.8%.

    EV/edge minimums must stay in sync with the tier-B env values set in
    _force_runtime_publication_contract, otherwise the tier guard rejects on
    value after the relief already passed the candidate.
    """
    books=max(_int(metrics.get('books_count')),_int(metrics.get('bookmaker_count')))
    odds_sources=max(_int(metrics.get('odds_sources_count')),_int(metrics.get('line_sources_count')),_int(metrics.get('sources_count')))
    contexts=max(_int(metrics.get('context_sources_count')),_int(metrics.get('confirmation_sources_count')))
    ev=max(_num(metrics.get('canonical_ev_pct')),_num(metrics.get('ev_pct')))
    edge=max(_num(metrics.get('canonical_edge_pp')),_num(metrics.get('edge_pp')))
    price=_num(metrics.get('odds'))
    hard_context=_has_hard_context(candidate,metrics)
    min_price=_env_num('HARIZON_B_RELIEF_MIN_ODDS',1.70)
    max_price=_env_num('HARIZON_B_RELIEF_MAX_ODDS',3.20)
    min_ev=_env_num('HARIZON_B_RELIEF_MIN_EV_PCT',2.0)
    min_edge=_env_num('HARIZON_B_RELIEF_MIN_EDGE_PP',1.0)
    checks={
        'books_below_2':books<2,
        'odds_sources_below_1':odds_sources<1,
        'context_sources_below_1':contexts<1,
        'no_hard_context':not hard_context,
        'ev_below_min':ev<min_ev,
        'edge_below_min':edge<min_edge,
        'price_outside_band':not (min_price<=price<=max_price),
    }
    failed=sorted(name for name,is_bad in checks.items() if is_bad)
    if len(_RELIEF_DIAGNOSTICS)<400:
        _RELIEF_DIAGNOSTICS.append({
            'match':_candidate_label(candidate),
            'selection':candidate.get('selection') or candidate.get('selection_key'),
            'point':candidate.get('point'),
            'candidate_source':candidate.get('_candidate_source'),
            'books_count':books,
            'odds_sources_count':odds_sources,
            'context_sources_count':contexts,
            'hard_context':hard_context,
            'ev_pct':round(ev,3),
            'edge_pp':round(edge,3),
            'odds':round(price,3),
            'failed_conditions':failed,
            'passed':not failed,
        })
    return not failed

def _install_b_tier_testing_relief(base:Any,stage:str='early')->None:
    old=getattr(base,'tier_reasons',None)
    if not callable(old):
        _record_install_event(stage,'skipped_no_tier_reasons',type(base).__name__); return
    if getattr(old,'_is_b_tier_relief',False):
        _record_install_event(stage,'skipped_already_outermost'); return
    # Allow exactly one re-install: the v20 chain may wrap or rebind
    # tier_reasons after the early install, and the relief has to stay the
    # outermost wrapper to have the last word on the reason list.
    if _RELIEF_INSTALL_COUNT[0]>=2:
        _record_install_event(stage,'skipped_install_limit'); return
    def wrapped(tier:str,candidate:dict[str,Any],metrics:dict[str,Any])->list[str]:
        reasons=list(old(tier,candidate,metrics) or [])
        if str(tier or '').upper()!='B' or not _b_tier_testing_floor(candidate,metrics): return reasons
        return [r for r in reasons if str(r) not in {'tier_b_quality_below_min','tier_b_publication_score_below_min','tier_b_market_implied_xg_not_hard_confirmation'}]
    wrapped._is_b_tier_relief=True
    base.tier_reasons=wrapped; base._b_tier_testing_relief_installed=True
    _RELIEF_INSTALL_COUNT[0]+=1
    _record_install_event(stage,'installed')

def _install_relief_before_publish(v18:Any)->None:
    """Re-install the relief immediately before the publisher actually runs.

    v20 installs a dozen patches on v18.base and only then calls v18.main, so an
    early install is at the mercy of every one of them.
    """
    old_main=getattr(v18,'main',None)
    if not callable(old_main) or getattr(old_main,'_relief_late_hook',False):
        _record_install_event('late_hook','skipped_already_wrapped'); return
    def wrapped_main(*args:Any,**kwargs:Any)->Any:
        try: _install_b_tier_testing_relief(getattr(v18,'base',None),'late_before_v18_main')
        except Exception as exc: _record_install_event('late_before_v18_main','failed',repr(exc))
        return old_main(*args,**kwargs)
    wrapped_main._relief_late_hook=True
    v18.main=wrapped_main
    _record_install_event('late_hook','wrapped_v18_main')

def main()->int:
    _force_runtime_publication_contract(); _repair_runtime_artifacts_before_fallback(); _apply_focused_alpha_policy()
    v18=None
    try:
        import scripts.publish_controlled_fallback_guarded_v18 as v18_module
        v18=v18_module
        from scripts.harizon_production_quality_layer import install as q
        from scripts.patch_current_price_recheck_value import install as cp
        from scripts.patch_last_chance_line_recheck_relief import install as lc
        from scripts.patch_publication_safety_contract import install as ps
        from scripts.patch_semantic_line_movement_alias_relief import install as ar
        from scripts.patch_semantic_movement_current_price_guard import install as sm
        from scripts.patch_tier_a_strict_policy import install as ta
        from scripts.patch_controlled_fallback_confirmation_bridge import install as cb
        from scripts.patch_fallback_evidence_and_integrity_runtime import install as ei
        ta(v18.base); ps(v18.base); cb(v18.base); ei(v18.base); lc(v18.base); ar(v18.base); q(v18.base); sm(v18.base); cp(v18.base)
    except Exception: pass
    # The relief gets its own block on purpose: while it shared the block above,
    # any failing import in it skipped the relief for the entire run silently.
    try:
        if v18 is None:
            _record_install_event('early','failed','v18 module unavailable')
        else:
            _install_b_tier_testing_relief(v18.base,'early')
            _install_relief_before_publish(v18)
    except Exception as exc:
        _record_install_event('early','failed',repr(exc))
    _apply_focused_alpha_policy(); _repair_runtime_artifacts_before_fallback(); _build_focused_alpha_decisions(); _force_runtime_publication_contract()
    code=1
    try:
        from scripts.publish_controlled_fallback_guarded_v20 import main as v20_main
        code=int(v20_main() or 0); _build_focused_alpha_decisions(); _run_step('scripts.target_fallback_provider_enrichment')
    finally:
        _write_relief_diagnostics()
    return code

if __name__=='__main__': raise SystemExit(main())
