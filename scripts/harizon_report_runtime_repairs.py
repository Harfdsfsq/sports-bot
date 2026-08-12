from __future__ import annotations

import json, os, re
from pathlib import Path
from typing import Any
EXPORT = Path(".data/exports")

def _load(path: str|Path, default: Any=None)->Any:
    try: return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except Exception: return {} if default is None else default

def _int(v: Any)->int:
    try:
        if isinstance(v,(list,tuple,set,dict)): return len(v)
        return int(float(v))
    except Exception: return 0

def _num(v: Any,d:float=0.0)->float:
    try: return float(str(v).replace(",",".")) if v not in (None,"") else d
    except Exception: return d

def _disabled_sportlogic_env()->bool:
    return any(str(os.getenv(n) or "").lower() in {"0","false","no","off"} for n in ("DAY_INVENTORY_ENABLE_SPORTLOGIC","ENABLE_SPORTLOGIC","SPORTLOGIC_ENABLED","SPORTLOGIC_CONTROLLED_ODDS_ENABLED"))

def _reserve_quality_from_metrics(m:dict[str,Any])->float:
    direct=_num(m.get("reserve_quality_score") or m.get("quality_score"),-1)
    if direct>0: return direct
    ev=max(_num(m.get("canonical_ev_pct")),_num(m.get("ev_pct"))); edge=max(_num(m.get("canonical_edge_pp")),_num(m.get("edge_pp"))); odds=_num(m.get("odds")); books=max(_int(m.get("books_count")),_int(m.get("bookmaker_count")),2 if _int(m.get("confirmation_sources_count"))>=2 else 0); conf=max(_int(m.get("confirmation_sources_count")),_int(m.get("context_sources_count")))
    score=38+min(18,ev*1.45)+min(16,edge*3)+min(10,books*3)+min(10,conf*1.5)+(4 if 1.75<=odds<=2.55 else (-8 if odds<1.70 or odds>2.90 else 0))
    return round(max(0,min(100,score)),1)

def patch_payload_quality(payload:dict[str,Any])->dict[str,Any]:
    samples=payload.get("samples") if isinstance(payload.get("samples"),dict) else {}; evaluated=samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"),list) else []; patched=0
    for row in evaluated:
        if not isinstance(row,dict): continue
        m=row.get("metrics") if isinstance(row.get("metrics"),dict) else {}; q=_reserve_quality_from_metrics(m)
        if q>0: m["reserve_quality_score"]=q; m["quality_score"]=m.get("quality_score") or q; row["reserve_quality_score"]=q; patched+=1
    payload.setdefault("diagnostic_repairs",{})["reserve_quality_samples_patched"]=patched; return payload

def patch_payload(payload:dict[str,Any])->dict[str,Any]:
    if _disabled_sportlogic_env(): payload.setdefault("api",{})["sportlogic"]={"enabled":False,"requests":0,"fixtures":0,"odds_requests":0,"matched":0,"offers":0,"errors":0,"diagnosis":"disabled_by_env"}
    q=_load(EXPORT/"latest-a-tier-targeted-enrichment-queue.json",{}); b=_load(EXPORT/"latest-bzzoiro-targeted-odds-confirmation.json",{})
    if isinstance(q,dict): payload.setdefault("a_tier_enrichment",{}).update(q.get("summary") or {})
    if isinstance(b,dict): payload.setdefault("a_tier_enrichment",{})["bzzoiro_targeted"]={k:b.get(k) for k in ("targets","bzzoiro_events_seen","matched_events","offers","two_source_promoted","diagnosis")}
    return payload

def patch_text_quality(text:str,payload:dict[str,Any])->str:
    samples=payload.get("samples") if isinstance(payload.get("samples"),dict) else {}; evaluated=[x for x in (samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"),list) else []) if isinstance(x,dict)]
    for idx,row in enumerate(evaluated[:6],1):
        q=_reserve_quality_from_metrics(row.get("metrics") if isinstance(row.get("metrics"),dict) else {})
        if q>0: text=re.sub(rf"(^\s*{idx}\. .*? \| q )0\.0\b",rf"\g<1>{q:.1f} reserve",text,count=1,flags=re.MULTILINE)
    return text

def patch_runtime_lines(text:str)->str:
    if _disabled_sportlogic_env(): text=re.sub(r"^• sportlogic:.*$","• sportlogic: enabled False, req 0, odds req 0, matched 0, offers 0, err 0",text,count=1,flags=re.MULTILINE)
    return text

def patch_a_tier_summary(text:str)->str:
    q=_load(EXPORT/"latest-a-tier-targeted-enrichment-queue.json",{}); s=q.get("summary") if isinstance(q,dict) and isinstance(q.get("summary"),dict) else {}; b=_load(EXPORT/"latest-bzzoiro-targeted-odds-confirmation.json",{})
    if s and "A-tier enrichment queue" not in text:
        line=f"• A-tier enrichment queue: Bzzoiro odds targets {_int(s.get('bzzoiro_odds_target_count'))}; context projection targets {_int(s.get('context_projection_target_count'))}; high-value recheck {_int(s.get('high_value_recheck_target_count'))}."
        text=text.replace("🧪 Воронка кандидатов",line+"\n🧪 Воронка кандидатов",1)
    if isinstance(b,dict) and "Bzzoiro targeted odds" not in text:
        line=f"• Bzzoiro targeted odds: targets {_int(b.get('targets'))}; events {_int(b.get('bzzoiro_events_seen'))}; matched {_int(b.get('matched_events'))}; offers {_int(b.get('offers'))}; 2-source promoted {_int(b.get('two_source_promoted'))}; diag {b.get('diagnosis') or 'n/a'}."
        text=text.replace("📡 Core API",line+"\n📡 Core API",1)
    return text

def patch_line_diagnostics(text:str)->str:
    d=_load(EXPORT/"latest-line-movement-diagnostics.json",{}); c=d.get("class_counts") if isinstance(d,dict) and isinstance(d.get("class_counts"),dict) else {}
    if c and "Line movement breakdown" not in text:
        labels={"actual_bad_movement":"реально плохое движение","selected_price_not_current":"выбранный коэффициент уже не текущий","not_confirmed":"движение не подтверждено","unconfirmed_final":"финальная проверка не подтвердила","odds_below_min":"коэффициент ниже минимума","duplicate":"дубликат","xg_direction_conflict":"конфликт направления с xG"}; parts=[f"{labels[k]} {_int(c.get(k))}" for k in labels if _int(c.get(k))>0]
        if parts: text=text.replace("🚫 Почему не опубликовано","• Line movement breakdown: "+"; ".join(parts[:8])+".\n🚫 Почему не опубликовано",1)
    return text

def patch_conclusion(text:str)->str:
    repl="• Главный текущий стопор: line movement/freshness/current-price; A-tier теперь упирается в фактическое исполнение Bzzoiro targeted odds и 2-source overlap."
    text=re.sub(r"^• Главный технический bottleneck:.*$",repl,text,count=1,flags=re.MULTILINE); text=re.sub(r"^• Главный текущий стопор:.*$",repl,text,count=1,flags=re.MULTILINE); return text

def patch(payload:dict[str,Any], text:str)->tuple[dict[str,Any],str]:
    payload=patch_payload_quality(payload); payload=patch_payload(payload); text=patch_text_quality(text,payload); text=patch_runtime_lines(text); text=patch_a_tier_summary(text); text=patch_line_diagnostics(text); text=patch_conclusion(text); return payload,text
