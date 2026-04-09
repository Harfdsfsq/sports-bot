from __future__ import annotations

from typing import Any

from app.schemas import Match, MatchContext
from app.utils import canonicalize_team_name, clamp

NEGATIVE_KEYWORDS = {
    'injury': 1.0,
    'injured': 1.0,
    'out': 0.8,
    'ruled out': 1.1,
    'suspension': 1.0,
    'suspended': 1.0,
    'absence': 0.9,
    'absent': 0.9,
    'doubtful': 0.6,
    'questionable': 0.5,
    'miss': 0.7,
    'missing': 0.7,
    'rotation': 0.45,
    'rested': 0.35,
    'lineup': 0.15,
    'line-up': 0.15,
}
POSITIVE_KEYWORDS = {
    'returns': 0.6,
    'return': 0.5,
    'fit': 0.4,
    'available': 0.35,
    'boost': 0.4,
    'back': 0.25,
}
GENERIC_SPORT_TERMS = ('football', 'soccer', 'match', 'team', 'cup', 'league')


def build_match_news_query(match: Match) -> str:
    home_terms = _team_query_terms(match.home_team)
    away_terms = _team_query_terms(match.away_team)
    team_clause = " OR ".join(f'"{term}"' for term in [*home_terms, *away_terms])
    return f'({team_clause}) AND (football OR soccer OR match OR team OR lineup OR injury OR suspension)'


def article_text(article: dict[str, Any]) -> str:
    title = str(article.get('title') or '').strip()
    desc = str(article.get('description') or '').strip()
    content = str(article.get('content') or '').strip()
    return ' '.join(part for part in (title, desc, content) if part)


def _mentions_team(text: str, team_name: str) -> bool:
    canonical_text = canonicalize_team_name(text)
    team_variants = _team_variants(team_name)
    if not canonical_text or not team_variants:
        return False
    return any(variant in canonical_text for variant in team_variants)


def _team_variants(team_name: str) -> list[str]:
    base = canonicalize_team_name(team_name)
    if not base:
        return []
    variants: list[str] = []

    def add(value: str) -> None:
        candidate = " ".join(str(value or "").split()).strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(base)
    tokens = base.split()
    if len(tokens) > 1 and len(tokens[-1]) <= 3:
        add(" ".join(tokens[:-1]))
    if len(tokens) > 2:
        add(" ".join(tokens[:2]))
    if tokens:
        add(tokens[0])
    return variants


def _team_query_terms(team_name: str) -> list[str]:
    variants = _team_variants(team_name)
    raw = str(team_name or "").replace('"', '').strip()
    terms: list[str] = []
    if raw:
        terms.append(raw)
    for variant in variants:
        pretty = " ".join(part.capitalize() for part in variant.split())
        if pretty not in terms:
            terms.append(pretty)
    return terms[:4]


def articles_to_context(match: Match, articles: list[dict[str, Any]], source: str) -> MatchContext | None:
    if not articles:
        return None
    home_neg = away_neg = home_pos = away_pos = 0.0
    relevant = []
    for article in articles:
        text = article_text(article)
        if not text:
            continue
        lower = text.lower()
        has_home = _mentions_team(text, match.home_team)
        has_away = _mentions_team(text, match.away_team)
        if not has_home and not has_away:
            continue
        neg_score = sum(weight for keyword, weight in NEGATIVE_KEYWORDS.items() if keyword in lower)
        pos_score = sum(weight for keyword, weight in POSITIVE_KEYWORDS.items() if keyword in lower)
        if not neg_score and not pos_score and not any(term in lower for term in GENERIC_SPORT_TERMS):
            continue
        relevant.append({
            'title': article.get('title'),
            'publishedAt': article.get('publishedAt') or article.get('published_at'),
            'url': article.get('url'),
            'home_hit': has_home,
            'away_hit': has_away,
            'neg_score': round(neg_score, 3),
            'pos_score': round(pos_score, 3),
        })
        if has_home:
            home_neg += neg_score
            home_pos += pos_score
        if has_away:
            away_neg += neg_score
            away_pos += pos_score
    if not relevant:
        return None
    home_abs = clamp(home_neg - (home_pos * 0.35), 0.0, 4.0)
    away_abs = clamp(away_neg - (away_pos * 0.35), 0.0, 4.0)
    delta = clamp((away_abs - home_abs) * 0.03 + (home_pos - away_pos) * 0.01, -0.12, 0.12)
    draw = 0.24
    home = 0.38 + delta
    away = 1.0 - home - draw
    total = home + away + draw
    home /= total
    away /= total
    draw /= total
    confidence = clamp(52.0 + len(relevant) * 1.4 + abs(delta) * 30.0, 51.0, 63.0)
    payload = {'articles': relevant[:12]}
    details = {
        'home_absences': round(home_abs, 3),
        'away_absences': round(away_abs, 3),
        'draw_probability': round(draw, 4),
        'news_article_count': len(relevant),
        f'{source}_article_count': len(relevant),
        f'{source}_home_absences': round(home_abs, 3),
        f'{source}_away_absences': round(away_abs, 3),
        f'{source}_home_positive': round(home_pos, 3),
        f'{source}_away_positive': round(away_pos, 3),
    }
    return MatchContext(
        source=source,
        payload=payload,
        home_win_probability=round(home, 4),
        away_win_probability=round(away, 4),
        confidence=float(round(confidence, 2)),
        details=details,
    )
