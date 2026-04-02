// @ts-nocheck
function myFunction() {
  return main();
}

var TELEGRAM_TOP_LIMIT = 5;
var DEFAULT_TODAY_BOOKMAKERS = ['Bet365', 'Unibet'];
var DEFAULT_CONSENSUS_BOOKMAKERS = ['Pinnacle', 'Betfair', 'Bet365', 'Unibet'];
var DEFAULT_THE_ODDS_PRIORITY = [
  'soccer_fifa_world_cup',
  'soccer_fifa_world_cup_qualifiers_europe',
  'soccer_uefa_champs_league',
  'soccer_uefa_europa_league',
  'soccer_uefa_europa_conference_league',
  'soccer_epl',
  'soccer_spain_la_liga',
  'soccer_italy_serie_a',
  'soccer_germany_bundesliga',
  'soccer_france_ligue_one',
  'soccer_brazil_campeonato',
  'icehockey_nhl',
  'basketball_nba',
  'baseball_mlb'
];
var DEFAULT_API_FOOTBALL_BOOKMAKER_IDS = [];
var CACHE_MAX_BYTES = 85000;
var MAX_LOG_BODY = 1200;
var MAX_MATCH_DEBUG_NO_ODDS = 220;
var MAX_TOP_CONTEXT_MATCHES = 40;
var MARKET_FAMILIES = ['h2h', 'totals', 'spreads', 'dnb', 'doubleChance', 'btts', 'teamTotals'];

var TEAM_ALIAS_MAP = {
  'internacional': 'internacional',
  'sc internacional': 'internacional',
  'sport club internacional': 'internacional',
  'athletico': 'athletico pr',
  'athletico pr': 'athletico pr',
  'athletico paranaense': 'athletico pr',
  'atletico paranaense': 'athletico pr',
  'red bull bragantino': 'bragantino',
  'rb bragantino': 'bragantino',
  'bragantino': 'bragantino',
  'vasco gama': 'vasco da gama',
  'vasco da gama': 'vasco da gama',
  'atletico mineiro': 'atletico mineiro',
  'atletico mineiro mg': 'atletico mineiro',
  'parma permsky kray': 'parma',
  'kk mega basket belgrade': 'mega basket',
  'pole france': 'pole france',
  'st vallier': 'saint vallier',
  'saint vallier': 'saint vallier',
  'cska st petersburg': 'cska st petersburg',
  'ska st petersburg': 'ska st petersburg',
  'dinamo neva st petersburg': 'dinamo neva st petersburg',
  'north macedonia': 'north macedonia',
  'czech republic': 'czechia',
  'czechia': 'czechia'
};

var TEAM_STOP_WORDS = {
  fc: true, cf: true, ac: true, sc: true, club: true, fk: true, bk: true, afc: true,
  calcio: true, hc: true, bc: true, kk: true, esporte: true, clube: true, deportivo: true,
  de: true, da: true, del: true, do: true, the: true, ec: true, cd: true
};

var SPORT_CONFIG = {
  soccer: {
    key: 'soccer',
    label: 'Футбол',
    theOddsGroups: ['Soccer'],
    theOddsSportKeyPrefixes: ['soccer_'],
    oddsApiIoNames: ['football', 'soccer']
  },
  basketball: {
    key: 'basketball',
    label: 'Баскетбол',
    theOddsGroups: ['Basketball'],
    theOddsSportKeyPrefixes: ['basketball_'],
    oddsApiIoNames: ['basketball']
  },
  baseball: {
    key: 'baseball',
    label: 'Бейсбол',
    theOddsGroups: ['Baseball'],
    theOddsSportKeyPrefixes: ['baseball_'],
    oddsApiIoNames: ['baseball']
  },
  icehockey: {
    key: 'icehockey',
    label: 'Хоккей',
    theOddsGroups: ['Ice Hockey'],
    theOddsSportKeyPrefixes: ['icehockey_', 'hockey_'],
    oddsApiIoNames: ['ice-hockey', 'ice hockey', 'hockey']
  }
};


var RUNTIME_QUOTA_STATE = {};
var RUNTIME_FLAGS = {};

var EMBEDDED_SCRIPT_PROPERTIES = {
  // === secrets ===
  BZZOIRO_API_KEY: '',
  ODDS_API_KEY: '',
  ODDS_API_IO_KEY: '',
  BOOKIES_API_KEY: '54127-bG6cB2UiN5QVgeb',
  BOOKIES_API_LOGIN: 'Harlz0n13',
  BOOKIES_API_TOKEN: '54127-bG6cB2UiN5QVgeb',
  API_FOOTBALL_KEY: '',
  TELEGRAM_TOKEN: '',
  TELEGRAM_CHAT_ID: '',
  SHEET_ID: '',

  // === endpoints and general ===
  API_FOOTBALL_HOST: 'v3.football.api-sports.io',
  TIMEZONE: 'Europe/Moscow',
  API_FOOTBALL_TIMEZONE: 'Europe/Moscow',
  SHEET_NAME: 'ValueBets',
  ENABLED_SPORTS: 'soccer,basketball,baseball,icehockey',
  BOOKMAKERS: 'Bet365,Unibet',
  CONSENSUS_BOOKMAKERS: 'Pinnacle,Betfair,Bet365,Unibet',
  BANKROLL: '10000',
  KELLY_FRACTION: '0.25',
  MAX_STAKE_PCT: '0.03',
  DAYS_AHEAD: '4',
  MAX_REQUESTS_PER_SPORT_PER_DAY: '500',
  CACHE_SECONDS: '90',
  DEEP_CONTEXT_CACHE_SECONDS: '900',
  ODDS_API_IO_EVENT_CACHE_SECONDS: '900',
  API_FOOTBALL_FIXTURES_CACHE_SECONDS: '300',

  // === filters ===
  EXCLUDE_EXOTIC_LEAGUES: 'false',
  ALLOWED_LEAGUE_KEYWORDS: 'world cup,qualif,uefa,fifa,champions,europa,conference,premier,bundesliga,serie,la liga,ligue,eredivisie,primeira,superliga,super league,allsvenskan,veikkausliiga,championship,league 1,league 2,copa libertadores,copa sudamericana,campeonato,serie b,mlb,nba,nhl,euroleague,kbo,npb,liga mx,spl',
  BLOCKED_LEAGUE_KEYWORDS: 'reserve,reserves,u19,u20,u21,u23,youth,women,women\'s,amic,friendly,club friendly,regional,cup youth',
  EXOTIC_MAX_BOOKMAKERS_THRESHOLD: '1',
  MATCH_START_TOLERANCE_HOURS: '12',
  FALLBACK_MATCH_START_TOLERANCE_HOURS: '8',
  STRICT_LEAGUE_FALLBACK: 'true',
  FALLBACK_MIN_MARKET_OFFERS: '2',

  // === The Odds API ===
  THE_ODDS_REGIONS: 'eu,uk,us',
  THE_ODDS_MARKETS: 'h2h,spreads,totals',
  THE_ODDS_MAX_SPORTS_PER_RUN: '12',
  THE_ODDS_DISABLE_HOURS: '6',
  THE_ODDS_PRIORITY_KEYS: DEFAULT_THE_ODDS_PRIORITY.join(','),

  // === Odds API IO ===
  ODDS_API_IO_PAGE_LIMIT: '60',
  ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT: '4',
  BOOKIES_API_ENABLED: 'true',
  BOOKIES_API_BASE_URL: 'https://bookiesapi.com/api/get.php',
  BOOKIES_API_SPORTS: 'soccer',
  BOOKIES_API_MARKETS: 'h2h,spreads,totals,btts,dnb,doubleChance,teamTotals',
  BOOKIES_API_TIMEOUT_MS: '25000',
  BOOKIES_API_USE_FOR_BACKFILL_ONLY: 'true',
  BOOKIES_API_PAGE_LIMIT: '50',
  BOOKIES_API_MAX_PAGES_PER_DAY: '10',
  BOOKIES_API_ODDS_TASK: 'allodds',
  SOURCE_WEIGHT_THEODDS: '1.04',
  SOURCE_WEIGHT_ODDSAPIIO: '1.00',
  SOURCE_WEIGHT_APIFOOTBALL: '0.96',
  SOURCE_WEIGHT_BOOKIESAPI: '0.98',
  SOURCE_WEIGHT_SSTATS: '0.90',
  BOOKMAKER_WEIGHT_PINNACLE: '1.16',
  BOOKMAKER_WEIGHT_BETFAIR: '1.12',
  BOOKMAKER_WEIGHT_BET365: '1.08',
  BOOKMAKER_WEIGHT_UNIBET: '1.03',
  OUTLIER_PRICE_TOLERANCE_PCT: '5.5',
  OUTLIER_MAX_PENALTY: '10',
  STRONG_MARKET_MIN_BOOKS: '2',
  STRONG_MARKET_MIN_SOURCES: '2',
  ODDS_API_IO_EVENT_MIN_PAGES_PER_SPORT: '2',
  ODDS_API_IO_EVENT_TARGET_SHARE: '0.88',
  ODDS_API_IO_EVENT_TARGET_BUFFER: '12',
  ODDS_API_IO_ODDS_DESIRED_COVERAGE_PCT: '0.58',
  ODDS_API_IO_ODDS_INITIAL_FETCH_SHARE: '0.68',
  ODDS_API_IO_ODDS_EXPANSION_STEP: '20',
  MAX_MATCHES_FOR_ODDS_FETCH: '420',

  // === API Football ===
  API_FOOTBALL_BOOKMAKER_IDS: '',
  API_FOOTBALL_ODDS_MODE: 'smart',
  PREFETCH_API_FOOTBALL_ODDS: 'false',
  USE_BZZOIRO_EVENTS_FALLBACK: 'false',

  // === model ===
  MIN_BOOKS_FOR_CONSENSUS: '2',
  MIN_EDGE_PCT: '1.5',
  MIN_EV_PCT: '1.0',
  H2H_SCORE_WEIGHT: '0.88',
  TOTALS_SCORE_WEIGHT: '1.18',
  SPREADS_SCORE_WEIGHT: '1.15',
  DNB_SCORE_WEIGHT: '1.00',
  DOUBLE_CHANCE_SCORE_WEIGHT: '0.82',
  BTTS_SCORE_WEIGHT: '1.12',
  TEAM_TOTALS_SCORE_WEIGHT: '1.20',
  MIN_MODEL_CONFIDENCE: '52',
  TARGET_ODDS_HARD_MIN: '1.60',
  TARGET_ODDS_HARD_MAX: '3.20',
  TARGET_ODDS_SWEET_MIN: '2.00',
  TARGET_ODDS_SWEET_MAX: '2.50',
  TARGET_ODDS_SCORE_BOOST: '0.18',
  ENABLE_ADVANCED_SOCCER_CONTEXT: 'true',
  ADV_CONTEXT_MATCH_LOOKAHEAD_HOURS: '72',
  LINEUP_FETCH_LOOKAHEAD_HOURS: '18',
  INJURY_FETCH_LOOKAHEAD_HOURS: '72',
  TEAM_STATS_CACHE_SECONDS: '21600',
  STANDINGS_CACHE_SECONDS: '3600',
  LINEUPS_CACHE_SECONDS: '900',
  INJURIES_CACHE_SECONDS: '1800',
  MODEL_SHRINK_MIN: '0.18',
  MODEL_SHRINK_MAX: '0.50',
  ENABLE_DERIVED_SOCCER_MARKETS: 'true',
  ENABLE_TEAM_TOTALS: 'true',

  // === SStats ===
  SSTATS_API_KEY: '',
  ENABLE_SSTATS_CONTEXT: 'true',
  ENABLE_SSTATS_MARKETS: 'true',
  SSTATS_CONTEXT_TOP_MATCHES: '20',
  SSTATS_MARKETS_TOP_MATCHES: '16',
  SSTATS_CACHE_SECONDS: '1800',
  SSTATS_GLICKO_CACHE_SECONDS: '21600',
  SSTATS_LIST_CACHE_SECONDS: '900',
  SSTATS_PROFITS_CACHE_SECONDS: '21600',
  SSTATS_CONTEXT_WEIGHT: '0.28',
  SSTATS_AUTO_LOOKUP: 'true',
  SSTATS_ENABLE_PROFITS: 'true',
  SSTATS_LIST_LIMIT: '1000',
  SSTATS_GAME_KEY_MAP: '',
  SSTATS_LOOKUP_URL_TEMPLATE: '',

  // === combos ===
  ALLOW_COMBO_BETS: 'true',
  COMBO_MIN_EDGE_PCT: '1.2',
  COMBO_MIN_EV_PCT: '0.8',
  COMBO_MAX_SELECTIONS: '2',
  COMBO_CORRELATION_BOOST_PCT: '8',

  // === deep soccer context ===
  ENABLE_DEEP_SOCCER_CONTEXT: 'true',
  DEEP_CONTEXT_TOP_MATCHES: '18',
  DEEP_LAST_MATCHES: '5',
  DEEP_H2H_MATCHES: '4',

  // === output ===
  DIVERSIFY_SINGLES_BY_MARKET: 'true',
  TELEGRAM_MAX_PER_MATCH: '2',
  TELEGRAM_TOP_LIMIT: '8'
};

function getEmbeddedPropertyValue(name) {
  return Object.prototype.hasOwnProperty.call(EMBEDDED_SCRIPT_PROPERTIES, name)
    ? EMBEDDED_SCRIPT_PROPERTIES[name]
    : null;
}

function getConfigProperty(props, name, fallback) {
  var embedded = getEmbeddedPropertyValue(name);
  if (embedded !== null && embedded !== undefined && String(embedded) !== '') return String(embedded);
  var value = props.getProperty(name);
  if (value !== null && value !== undefined && String(value) !== '') return value;
  return fallback == null ? '' : String(fallback);
}

function getSecretProperty(props, name) {
  var embedded = getEmbeddedPropertyValue(name);
  if (embedded !== null && embedded !== undefined && String(embedded) !== '') return String(embedded);
  var value = props.getProperty(name);
  return value || '';
}

function applyEmbeddedScriptProperties(overwriteExisting) {
  var props = PropertiesService.getScriptProperties();
  var existing = props.getProperties();
  var out = {};
  Object.keys(EMBEDDED_SCRIPT_PROPERTIES).forEach(function (key) {
    var value = EMBEDDED_SCRIPT_PROPERTIES[key];
    if (value == null || String(value) === '') return;
    if (overwriteExisting || !existing[key]) out[key] = String(value);
  });
  if (Object.keys(out).length) props.setProperties(out, false);
  Logger.log('applyEmbeddedScriptProperties: saved ' + Object.keys(out).length + ' properties');
}

function getConfig() {
  var props = PropertiesService.getScriptProperties();
  var rawBookiesEnabled = getConfigProperty(props, 'BOOKIES_API_ENABLED', '');
  var rawBookiesLogin = getSecretProperty(props, 'BOOKIES_API_LOGIN');
  var rawBookiesToken = getSecretProperty(props, 'BOOKIES_API_TOKEN') || getSecretProperty(props, 'BOOKIES_API_KEY');
  var computedBookiesEnabled = rawBookiesEnabled
    ? String(rawBookiesEnabled).toLowerCase() === 'true'
    : !!(rawBookiesLogin && rawBookiesToken);
  var config = {
    bzzoiroApiKey: getSecretProperty(props, 'BZZOIRO_API_KEY'),
    oddsApiKey: getSecretProperty(props, 'ODDS_API_KEY'),
    oddsApiIoKey: getSecretProperty(props, 'ODDS_API_IO_KEY'),
    bookiesApiKey: getSecretProperty(props, 'BOOKIES_API_KEY'),
    bookiesApiLogin: rawBookiesLogin,
    bookiesApiToken: rawBookiesToken,
    apiFootballKey: getSecretProperty(props, 'API_FOOTBALL_KEY'),
    apiFootballHost: getConfigProperty(props, 'API_FOOTBALL_HOST', 'v3.football.api-sports.io'),
    telegramToken: getSecretProperty(props, 'TELEGRAM_TOKEN'),
    telegramChatId: getSecretProperty(props, 'TELEGRAM_CHAT_ID'),
    sheetId: getSecretProperty(props, 'SHEET_ID'),
    timezone: getConfigProperty(props, 'TIMEZONE', Session.getScriptTimeZone() || 'Europe/Moscow'),
    enabledSports: parseCsv(getConfigProperty(props, 'ENABLED_SPORTS', 'soccer,basketball,baseball,icehockey')),
    bookmakers: parseCsv(getConfigProperty(props, 'BOOKMAKERS', DEFAULT_TODAY_BOOKMAKERS.join(','))),
    consensusBookmakers: parseCsv(getConfigProperty(props, 'CONSENSUS_BOOKMAKERS', DEFAULT_CONSENSUS_BOOKMAKERS.join(','))),
    bankroll: toNumber(getConfigProperty(props, 'BANKROLL', 10000)) || 10000,
    kellyFraction: toNumber(getConfigProperty(props, 'KELLY_FRACTION', 0.25)) || 0.25,
    maxStakePct: toNumber(getConfigProperty(props, 'MAX_STAKE_PCT', 0.03)) || 0.03,
    allowComboBets: String(getConfigProperty(props, 'ALLOW_COMBO_BETS', 'false')).toLowerCase() === 'true',
    comboMinEdgePct: toNumber(getConfigProperty(props, 'COMBO_MIN_EDGE_PCT', 1.8)) || 1.8,
    comboMinEvPct: toNumber(getConfigProperty(props, 'COMBO_MIN_EV_PCT', 1.5)) || 1.5,
    comboMaxSelections: toNumber(getConfigProperty(props, 'COMBO_MAX_SELECTIONS', 2)) || 2,
    diversifySinglesByMarket: String(getConfigProperty(props, 'DIVERSIFY_SINGLES_BY_MARKET', 'true')).toLowerCase() === 'true',
    h2hScoreWeight: toNumber(getConfigProperty(props, 'H2H_SCORE_WEIGHT', 0.96)) || 0.96,
    totalsScoreWeight: toNumber(getConfigProperty(props, 'TOTALS_SCORE_WEIGHT', 1.04)) || 1.04,
    spreadsScoreWeight: toNumber(getConfigProperty(props, 'SPREADS_SCORE_WEIGHT', 1.03)) || 1.03,
    dnbScoreWeight: toNumber(getConfigProperty(props, 'DNB_SCORE_WEIGHT', 0.98)) || 0.98,
    doubleChanceScoreWeight: toNumber(getConfigProperty(props, 'DOUBLE_CHANCE_SCORE_WEIGHT', 0.82)) || 0.82,
    bttsScoreWeight: toNumber(getConfigProperty(props, 'BTTS_SCORE_WEIGHT', 0.98)) || 0.98,
    teamTotalsScoreWeight: toNumber(getConfigProperty(props, 'TEAM_TOTALS_SCORE_WEIGHT', 1.05)) || 1.05,
    minModelConfidence: toNumber(getConfigProperty(props, 'MIN_MODEL_CONFIDENCE', 52)) || 52,
    targetOddsHardMin: toNumber(getConfigProperty(props, 'TARGET_ODDS_HARD_MIN', 1.60)) || 1.60,
    targetOddsHardMax: toNumber(getConfigProperty(props, 'TARGET_ODDS_HARD_MAX', 3.20)) || 3.20,
    targetOddsSweetMin: toNumber(getConfigProperty(props, 'TARGET_ODDS_SWEET_MIN', 2.0)) || 2.0,
    targetOddsSweetMax: toNumber(getConfigProperty(props, 'TARGET_ODDS_SWEET_MAX', 2.5)) || 2.5,
    targetOddsScoreBoost: toNumber(getConfigProperty(props, 'TARGET_ODDS_SCORE_BOOST', 0.18)) || 0.18,
    comboCorrelationBoostPct: toNumber(getConfigProperty(props, 'COMBO_CORRELATION_BOOST_PCT', 0)) || 0,
    minEdgePct: toNumber(getConfigProperty(props, 'MIN_EDGE_PCT', 1.5)) || 1.5,
    minEvPct: toNumber(getConfigProperty(props, 'MIN_EV_PCT', 1.0)) || 1.0,
    daysAhead: toNumber(getConfigProperty(props, 'DAYS_AHEAD', 4)) || 4,
    sheetName: getConfigProperty(props, 'SHEET_NAME', 'ValueBets'),
    maxRequestsPerSportPerDay: toNumber(getConfigProperty(props, 'MAX_REQUESTS_PER_SPORT_PER_DAY', 500)) || 500,
    minBooksForConsensus: toNumber(getConfigProperty(props, 'MIN_BOOKS_FOR_CONSENSUS', 2)) || 2,
    cacheSeconds: toNumber(getConfigProperty(props, 'CACHE_SECONDS', 90)) || 90,
    telegramMaxPerMatch: toNumber(getConfigProperty(props, 'TELEGRAM_MAX_PER_MATCH', 2)) || 2,
    telegramTopLimit: toNumber(getConfigProperty(props, 'TELEGRAM_TOP_LIMIT', 8)) || 8,
    apiFootballBookmakerIds: parseCsv(getConfigProperty(props, 'API_FOOTBALL_BOOKMAKER_IDS', DEFAULT_API_FOOTBALL_BOOKMAKER_IDS.join(','))),
    theOddsRegions: parseCsv(getConfigProperty(props, 'THE_ODDS_REGIONS', 'eu,uk,us')),
    theOddsMarkets: parseCsv(getConfigProperty(props, 'THE_ODDS_MARKETS', 'h2h,spreads,totals')),
    matchStartToleranceHours: toNumber(getConfigProperty(props, 'MATCH_START_TOLERANCE_HOURS', 12)) || 12,
    fallbackMatchStartToleranceHours: toNumber(getConfigProperty(props, 'FALLBACK_MATCH_START_TOLERANCE_HOURS', 8)) || 8,
    oddsApiIoPageLimit: toNumber(getConfigProperty(props, 'ODDS_API_IO_PAGE_LIMIT', 60)) || 60,
    oddsApiIoMaxEventPagesPerSport: toNumber(getConfigProperty(props, 'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT', 4)) || 4,
    bookiesApiEnabled: computedBookiesEnabled,
    bookiesApiBaseUrl: getConfigProperty(props, 'BOOKIES_API_BASE_URL', 'https://bookiesapi.com/api/get.php'),
    bookiesApiSports: parseCsv(getConfigProperty(props, 'BOOKIES_API_SPORTS', 'soccer')),
    bookiesApiMarkets: parseCsv(getConfigProperty(props, 'BOOKIES_API_MARKETS', 'h2h,spreads,totals,btts,dnb,doubleChance,teamTotals')),
    bookiesApiTimeoutMs: toNumber(getConfigProperty(props, 'BOOKIES_API_TIMEOUT_MS', 25000)) || 25000,
    bookiesApiUseForBackfillOnly: String(getConfigProperty(props, 'BOOKIES_API_USE_FOR_BACKFILL_ONLY', 'true')).toLowerCase() === 'true',
    bookiesApiPageLimit: toNumber(getConfigProperty(props, 'BOOKIES_API_PAGE_LIMIT', 50)) || 50,
    bookiesApiMaxPagesPerDay: toNumber(getConfigProperty(props, 'BOOKIES_API_MAX_PAGES_PER_DAY', 10)) || 10,
    bookiesApiOddsTask: getConfigProperty(props, 'BOOKIES_API_ODDS_TASK', 'allodds'),
    sourceWeightTheOdds: toNumber(getConfigProperty(props, 'SOURCE_WEIGHT_THEODDS', 1.04)) || 1.04,
    sourceWeightOddsApiIo: toNumber(getConfigProperty(props, 'SOURCE_WEIGHT_ODDSAPIIO', 1.00)) || 1.00,
    sourceWeightApiFootball: toNumber(getConfigProperty(props, 'SOURCE_WEIGHT_APIFOOTBALL', 0.96)) || 0.96,
    sourceWeightBookiesApi: toNumber(getConfigProperty(props, 'SOURCE_WEIGHT_BOOKIESAPI', 0.98)) || 0.98,
    sourceWeightSstats: toNumber(getConfigProperty(props, 'SOURCE_WEIGHT_SSTATS', 0.90)) || 0.90,
    bookmakerWeightPinnacle: toNumber(getConfigProperty(props, 'BOOKMAKER_WEIGHT_PINNACLE', 1.16)) || 1.16,
    bookmakerWeightBetfair: toNumber(getConfigProperty(props, 'BOOKMAKER_WEIGHT_BETFAIR', 1.12)) || 1.12,
    bookmakerWeightBet365: toNumber(getConfigProperty(props, 'BOOKMAKER_WEIGHT_BET365', 1.08)) || 1.08,
    bookmakerWeightUnibet: toNumber(getConfigProperty(props, 'BOOKMAKER_WEIGHT_UNIBET', 1.03)) || 1.03,
    outlierPriceTolerancePct: toNumber(getConfigProperty(props, 'OUTLIER_PRICE_TOLERANCE_PCT', 5.5)) || 5.5,
    outlierMaxPenalty: toNumber(getConfigProperty(props, 'OUTLIER_MAX_PENALTY', 10)) || 10,
    strongMarketMinBooks: toNumber(getConfigProperty(props, 'STRONG_MARKET_MIN_BOOKS', 2)) || 2,
    strongMarketMinSources: toNumber(getConfigProperty(props, 'STRONG_MARKET_MIN_SOURCES', 2)) || 2,
    theOddsMaxSportsPerRun: toNumber(getConfigProperty(props, 'THE_ODDS_MAX_SPORTS_PER_RUN', 12)) || 12,
    theOddsPriorityKeys: parseCsv(getConfigProperty(props, 'THE_ODDS_PRIORITY_KEYS', DEFAULT_THE_ODDS_PRIORITY.join(','))),
    maxMatchesForOddsFetch: toNumber(getConfigProperty(props, 'MAX_MATCHES_FOR_ODDS_FETCH', 420)) || 420,
    apiFootballTimezone: getConfigProperty(props, 'API_FOOTBALL_TIMEZONE', Session.getScriptTimeZone() || 'Europe/Moscow'),
    excludeExoticLeagues: String(getConfigProperty(props, 'EXCLUDE_EXOTIC_LEAGUES', 'false')).toLowerCase() === 'true',
    allowedLeagueKeywords: parseCsv(getConfigProperty(props, 'ALLOWED_LEAGUE_KEYWORDS', 'world cup,qualif,uefa,fifa,champions,europa,conference,premier,bundesliga,serie,la liga,ligue,eredivisie,primeira,superliga,super league,allsvenskan,veikkausliiga,championship,league 1,league 2,copa libertadores,copa sudamericana,campeonato,serie b,mlb,nba,nhl,euroleague,kbo,npb,liga mx,spl')),
    blockedLeagueKeywords: parseCsv(getConfigProperty(props, 'BLOCKED_LEAGUE_KEYWORDS', 'reserve,reserves,u19,u20,u21,u23,youth,women,women\'s,amic,friendly,club friendly,regional,cup youth')),
    exoticMaxBookmakersThreshold: toNumber(getConfigProperty(props, 'EXOTIC_MAX_BOOKMAKERS_THRESHOLD', 1)) || 1,
    enableDeepSoccerContext: String(getConfigProperty(props, 'ENABLE_DEEP_SOCCER_CONTEXT', 'true')).toLowerCase() === 'true',
    enableAdvancedSoccerContext: String(getConfigProperty(props, 'ENABLE_ADVANCED_SOCCER_CONTEXT', 'true')).toLowerCase() === 'true',
    advancedContextMatchLookaheadHours: toNumber(getConfigProperty(props, 'ADV_CONTEXT_MATCH_LOOKAHEAD_HOURS', 72)) || 72,
    lineupFetchLookaheadHours: toNumber(getConfigProperty(props, 'LINEUP_FETCH_LOOKAHEAD_HOURS', 18)) || 18,
    injuryFetchLookaheadHours: toNumber(getConfigProperty(props, 'INJURY_FETCH_LOOKAHEAD_HOURS', 72)) || 72,
    deepContextTopMatches: toNumber(getConfigProperty(props, 'DEEP_CONTEXT_TOP_MATCHES', 18)) || 18,
    deepLastMatches: toNumber(getConfigProperty(props, 'DEEP_LAST_MATCHES', 5)) || 5,
    deepH2hMatches: toNumber(getConfigProperty(props, 'DEEP_H2H_MATCHES', 4)) || 4,
    deepContextCacheSeconds: toNumber(getConfigProperty(props, 'DEEP_CONTEXT_CACHE_SECONDS', 900)) || 900,
    fallbackMinMarketOffers: toNumber(getConfigProperty(props, 'FALLBACK_MIN_MARKET_OFFERS', 2)) || 2,
    prefetchApiFootballOdds: String(getConfigProperty(props, 'PREFETCH_API_FOOTBALL_ODDS', 'false')).toLowerCase() === 'true',
    apiFootballOddsMode: String(getConfigProperty(props, 'API_FOOTBALL_ODDS_MODE', 'smart')).toLowerCase(),
    strictLeagueFallback: String(getConfigProperty(props, 'STRICT_LEAGUE_FALLBACK', 'true')).toLowerCase() === 'true',
    enableDerivedSoccerMarkets: String(getConfigProperty(props, 'ENABLE_DERIVED_SOCCER_MARKETS', 'true')).toLowerCase() === 'true',
    enableTeamTotals: String(getConfigProperty(props, 'ENABLE_TEAM_TOTALS', 'true')).toLowerCase() === 'true',
    enableSstatsContext: String(getConfigProperty(props, 'ENABLE_SSTATS_CONTEXT', 'true')).toLowerCase() === 'true',
    enableSstatsMarkets: String(getConfigProperty(props, 'ENABLE_SSTATS_MARKETS', 'true')).toLowerCase() === 'true',
    sstatsApiKey: getSecretProperty(props, 'SSTATS_API_KEY'),
    sstatsContextTopMatches: toNumber(getConfigProperty(props, 'SSTATS_CONTEXT_TOP_MATCHES', 20)) || 20,
    sstatsMarketsTopMatches: toNumber(getConfigProperty(props, 'SSTATS_MARKETS_TOP_MATCHES', 16)) || 16,
    sstatsCacheSeconds: toNumber(getConfigProperty(props, 'SSTATS_CACHE_SECONDS', 1800)) || 1800,
    sstatsGlickoCacheSeconds: toNumber(getConfigProperty(props, 'SSTATS_GLICKO_CACHE_SECONDS', 21600)) || 21600,
    sstatsListCacheSeconds: toNumber(getConfigProperty(props, 'SSTATS_LIST_CACHE_SECONDS', 900)) || 900,
    sstatsProfitsCacheSeconds: toNumber(getConfigProperty(props, 'SSTATS_PROFITS_CACHE_SECONDS', 21600)) || 21600,
    sstatsContextWeight: toNumber(getConfigProperty(props, 'SSTATS_CONTEXT_WEIGHT', 0.28)) || 0.28,
    sstatsAutoLookup: String(getConfigProperty(props, 'SSTATS_AUTO_LOOKUP', 'true')).toLowerCase() === 'true',
    sstatsEnableProfits: String(getConfigProperty(props, 'SSTATS_ENABLE_PROFITS', 'true')).toLowerCase() === 'true',
    sstatsListLimit: toNumber(getConfigProperty(props, 'SSTATS_LIST_LIMIT', 1000)) || 1000,
    sstatsGameKeyMapRaw: getConfigProperty(props, 'SSTATS_GAME_KEY_MAP', ''),
    sstatsLookupUrlTemplate: getConfigProperty(props, 'SSTATS_LOOKUP_URL_TEMPLATE', ''),
    useBzzoiroEventsFallback: String(getConfigProperty(props, 'USE_BZZOIRO_EVENTS_FALLBACK', 'false')).toLowerCase() === 'true',
    modelShrinkMin: toNumber(getConfigProperty(props, 'MODEL_SHRINK_MIN', 0.18)) || 0.18,
    modelShrinkMax: toNumber(getConfigProperty(props, 'MODEL_SHRINK_MAX', 0.50)) || 0.50,
    oddsApiIoEventCacheSeconds: toNumber(getConfigProperty(props, 'ODDS_API_IO_EVENT_CACHE_SECONDS', 900)) || 900,
    apiFootballFixturesCacheSeconds: toNumber(getConfigProperty(props, 'API_FOOTBALL_FIXTURES_CACHE_SECONDS', 300)) || 300,
    oddsApiIoEventMinPagesPerSport: toNumber(getConfigProperty(props, 'ODDS_API_IO_EVENT_MIN_PAGES_PER_SPORT', 2)) || 2,
    oddsApiIoEventTargetShare: toNumber(getConfigProperty(props, 'ODDS_API_IO_EVENT_TARGET_SHARE', 0.88)) || 0.88,
    oddsApiIoEventTargetBuffer: toNumber(getConfigProperty(props, 'ODDS_API_IO_EVENT_TARGET_BUFFER', 12)) || 12,
    oddsApiIoOddsDesiredCoveragePct: toNumber(getConfigProperty(props, 'ODDS_API_IO_ODDS_DESIRED_COVERAGE_PCT', 0.58)) || 0.58,
    oddsApiIoOddsInitialFetchShare: toNumber(getConfigProperty(props, 'ODDS_API_IO_ODDS_INITIAL_FETCH_SHARE', 0.68)) || 0.68,
    oddsApiIoOddsExpansionStep: toNumber(getConfigProperty(props, 'ODDS_API_IO_ODDS_EXPANSION_STEP', 20)) || 20,
    theOddsDisableHours: toNumber(getConfigProperty(props, 'THE_ODDS_DISABLE_HOURS', 6)) || 6,
    teamStatsCacheSeconds: toNumber(getConfigProperty(props, 'TEAM_STATS_CACHE_SECONDS', 21600)) || 21600,
    standingsCacheSeconds: toNumber(getConfigProperty(props, 'STANDINGS_CACHE_SECONDS', 3600)) || 3600,
    lineupsCacheSeconds: toNumber(getConfigProperty(props, 'LINEUPS_CACHE_SECONDS', 900)) || 900,
    injuriesCacheSeconds: toNumber(getConfigProperty(props, 'INJURIES_CACHE_SECONDS', 1800)) || 1800
  };

  config.sstatsGameKeyMap = parseKeyValueMap(config.sstatsGameKeyMapRaw || '');

  if (!config.oddsApiKey && !config.oddsApiIoKey && !config.apiFootballKey) {
    throw new Error('Не найден ни один источник коэффициентов: ODDS_API_KEY / ODDS_API_IO_KEY / API_FOOTBALL_KEY');
  }
  return config;
}

function parseCsv(text) {
  return String(text || '')
    .split(',')
    .map(function (x) { return String(x || '').trim(); })
    .filter(function (x) { return !!x; });
}

function parseKeyValueMap(text) {
  var out = {};
  String(text || '')
    .split(/\r?\n|;/)
    .map(function (row) { return String(row || '').trim(); })
    .forEach(function (row) {
      if (!row) return;
      var idx = row.indexOf('=');
      if (idx === -1) return;
      var key = row.slice(0, idx).trim();
      var value = row.slice(idx + 1).trim();
      if (!key || !value) return;
      out[key] = value;
    });
  return out;
}

function safeUrlForLog(url) {
  return String(url)
    .replace(/apiKey=[^&]+/gi, 'apiKey=***')
    .replace(/apikey=[^&]+/gi, 'apikey=***')
    .replace(/token=[^&]+/gi, 'token=***')
    .replace(/key=[^&]+/gi, 'key=***')
    .replace(/x-apisports-key:[^&]+/gi, 'x-apisports-key:***');
}

function toNumber(value) {
  var num = Number(value);
  return isNaN(num) ? null : num;
}

function round2(value) {
  var num = toNumber(value);
  if (num == null) return null;
  return Math.round(num * 100) / 100;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function digestHex(text) {
  var raw = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, text, Utilities.Charset.UTF_8);
  return raw.map(function (b) {
    var v = (b < 0 ? b + 256 : b).toString(16);
    return v.length === 1 ? '0' + v : v;
  }).join('');
}

function getTodayKey(timezone) {
  return Utilities.formatDate(new Date(), timezone, 'yyyy-MM-dd');
}

function normalizeQuotaBucketName(name) {
  return String(name || 'generic').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'generic';
}

function getQuotaBucketKey(bucket, sportKey, today) {
  return 'REQ_' + normalizeQuotaBucketName(bucket) + '_' + String(sportKey || 'global') + '_' + today;
}

function enrichQuotaWithBucket(quota, label) {
  if (!quota) return quota;
  if (quota.bucket) return quota;
  var bucket = 'generic';
  var raw = String(label || '').toLowerCase();
  if (raw.indexOf('bookiesapi') === 0) bucket = 'bookiesapi';
  else if (raw.indexOf('odds-api.io') === 0) bucket = 'oddsapiio';
  else if (raw.indexOf('the odds api') === 0) bucket = 'theodds';
  else if (raw.indexOf('api-football') === 0) bucket = 'apifootball';
  else if (raw.indexOf('bzzoiro') === 0) bucket = 'bzzoiro';
  else if (raw.indexOf('sstats') === 0) bucket = 'sstats';
  var copy = {};
  Object.keys(quota).forEach(function (k) { copy[k] = quota[k]; });
  copy.bucket = bucket;
  return copy;
}

function reserveQuota(config, quota) {
  if (!quota || !config) return true;
  var today = getTodayKey(config.timezone);
  var cost = Number(quota.cost || 1);
  var bucket = normalizeQuotaBucketName(quota.bucket || 'generic');

  function getUsed(key) {
    return Number(RUNTIME_QUOTA_STATE[key] || 0);
  }

  function setUsed(key, value) {
    RUNTIME_QUOTA_STATE[key] = Number(value || 0);
  }

  if (quota.sportKey) {
    var key = getQuotaBucketKey(bucket, quota.sportKey, today);
    var used = getUsed(key);
    if (used + cost > config.maxRequestsPerSportPerDay) {
      Logger.log('Лимит запросов достигнут для ' + quota.sportKey + ' [' + bucket + ']: ' + used + ' / ' + config.maxRequestsPerSportPerDay);
      return false;
    }
    setUsed(key, used + cost);
    return true;
  }

  if (quota.multiSportKeys && quota.multiSportKeys.length) {
    var blocked = quota.multiSportKeys.some(function (sportKey) {
      var key = getQuotaBucketKey(bucket, sportKey, today);
      var used = getUsed(key);
      return used + cost > config.maxRequestsPerSportPerDay;
    });
    if (blocked) {
      Logger.log('Глобальный запрос пропущен [' + bucket + ']: достигнут лимит хотя бы по одному виду спорта');
      return false;
    }
    quota.multiSportKeys.forEach(function (sportKey) {
      var key = getQuotaBucketKey(bucket, sportKey, today);
      var used = getUsed(key);
      setUsed(key, used + cost);
    });
    return true;
  }
  return true;
}

function tryCachePut(cache, key, text, ttlSeconds, label) {
  if (!cache || !key || !text || !ttlSeconds) return;
  if (String(text).length > CACHE_MAX_BYTES) {
    Logger.log((label || 'HTTP') + ' cache skipped: payload too large (' + String(text).length + ' chars)');
    return;
  }
  try {
    cache.put(key, text, Math.min(ttlSeconds, 21600));
  } catch (e) {
    Logger.log((label || 'HTTP') + ' cache put skipped: ' + e);
  }
}

function fetchJson(url, options, label, quota, cacheSeconds) {
  var requestOptions = Object.assign({ method: 'get', muteHttpExceptions: true }, options || {});
  var cache = CacheService.getScriptCache();
  var cacheKey = digestHex(url + '|' + JSON.stringify(requestOptions.headers || {}));
  var cached = cache.get(cacheKey);
  if (cached) {
    try {
      return JSON.parse(cached);
    } catch (e) {
      Logger.log((label || 'HTTP') + ' cached JSON parse error: ' + e);
    }
  }

  quota = enrichQuotaWithBucket(quota, label);
  if (!reserveQuota(quota && quota.config ? quota.config : null, quota)) return null;

  Logger.log((label || 'HTTP') + ' -> ' + safeUrlForLog(url));
  var response;
  try {
    response = UrlFetchApp.fetch(url, requestOptions);
  } catch (fetchError) {
    var errText = String(fetchError || '');
    Logger.log((label || 'HTTP') + ' fetch exception: ' + errText);
    return { status: -1, data: null, text: errText, fromCache: false, fetchError: true };
  }
  var status = response.getResponseCode();
  var text = response.getContentText();
  Logger.log((label || 'HTTP') + ' status: ' + status + ', body chars: ' + text.length);

  if (status < 200 || status >= 300) {
    Logger.log((label || 'HTTP') + ' error body: ' + text.slice(0, MAX_LOG_BODY));
    return null;
  }

  var data = null;
  try {
    data = JSON.parse(text);
  } catch (e2) {
    Logger.log((label || 'HTTP') + ' response JSON parse error: ' + e2);
    Logger.log((label || 'HTTP') + ' raw body preview: ' + text.slice(0, MAX_LOG_BODY));
    return null;
  }

  if (cacheSeconds && cacheSeconds > 0) {
    tryCachePut(cache, cacheKey, JSON.stringify(data), cacheSeconds, label);
  }
  return data;
}


function fetchJsonMeta(url, options, label, quota, cacheSeconds) {
  var requestOptions = Object.assign({ method: 'get', muteHttpExceptions: true }, options || {});
  var cache = CacheService.getScriptCache();
  var cacheKey = digestHex(url + '|' + JSON.stringify(requestOptions.headers || {}));
  var cached = cache.get(cacheKey);
  if (cached) {
    try {
      return { status: 200, data: JSON.parse(cached), text: cached, fromCache: true };
    } catch (e) {
      Logger.log((label || 'HTTP') + ' cached JSON parse error: ' + e);
    }
  }

  quota = enrichQuotaWithBucket(quota, label);
  if (!reserveQuota(quota && quota.config ? quota.config : null, quota)) {
    return { status: 0, data: null, text: '', skippedByQuota: true };
  }

  Logger.log((label || 'HTTP') + ' -> ' + safeUrlForLog(url));
  var response;
  try {
    response = UrlFetchApp.fetch(url, requestOptions);
  } catch (fetchError) {
    var errText = String(fetchError || '');
    Logger.log((label || 'HTTP') + ' fetch exception: ' + errText);
    return { status: -1, data: null, text: errText, fromCache: false, fetchError: true };
  }
  var status = response.getResponseCode();
  var text = response.getContentText();
  Logger.log((label || 'HTTP') + ' status: ' + status + ', body chars: ' + text.length);

  var data = null;
  if (status >= 200 && status < 300) {
    try {
      data = JSON.parse(text);
    } catch (e2) {
      Logger.log((label || 'HTTP') + ' response JSON parse error: ' + e2);
      Logger.log((label || 'HTTP') + ' raw body preview: ' + text.slice(0, MAX_LOG_BODY));
    }
    if (data != null && cacheSeconds && cacheSeconds > 0) {
      tryCachePut(cache, cacheKey, JSON.stringify(data), cacheSeconds, label);
    }
  } else {
    Logger.log((label || 'HTTP') + ' error body: ' + text.slice(0, MAX_LOG_BODY));
  }

  return { status: status, data: data, text: text, fromCache: false };
}

function parseOddsApiIoInvalidBookmaker(errorText) {
  var text = String(errorText || '');
  var match = text.match(/"error"\s*:\s*"([^"]+?) is not a valid bookmaker/i) ||
              text.match(/([^"]+?) is not a valid bookmaker/i);
  return match ? String(match[1] || '').trim() : '';
}

function removeBookmakerName(list, invalidName) {
  var raw = String(invalidName || '').toLowerCase();
  if (!raw || !list || !list.length) return false;
  var next = list.filter(function (name) {
    return String(name || '').toLowerCase() !== raw;
  });
  if (next.length === list.length) return false;
  list.length = 0;
  next.forEach(function (name) { list.push(name); });
  return true;
}

function buildQuery(params) {
  var parts = [];
  Object.keys(params || {}).forEach(function (k) {
    var value = params[k];
    if (value == null || value === '') return;
    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(value)));
  });
  return parts.join('&');
}

function getDateRange(timezone, daysAhead) {
  var now = new Date();
  var from = Utilities.formatDate(now, timezone, 'yyyy-MM-dd');
  var toDate = new Date(now.getTime() + (daysAhead || 3) * 24 * 60 * 60 * 1000);
  var to = Utilities.formatDate(toDate, timezone, 'yyyy-MM-dd');
  return { from: from, to: to };
}

function getDateKey(isoDate) {
  var raw = String(isoDate || '').trim();
  if (!raw) return 'nodate';
  var d = new Date(raw);
  if (!isNaN(d.getTime())) return d.toISOString().slice(0, 10);
  return raw.slice(0, 10);
}

function transliterateCyrillicToLatin(value) {
  var map = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'i',
    'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
    'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
  };
  return String(value || '').replace(/[А-Яа-яЁё]/g, function (ch) {
    var lower = ch.toLowerCase();
    return map.hasOwnProperty(lower) ? map[lower] : lower;
  });
}

function normalizeTeamName(name) {
  var value = String(name || '');
  if (value.normalize) {
    value = value.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  }
  value = transliterateCyrillicToLatin(value)
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/\b(st\.?|saint)\b/g, ' saint ')
    .replace(/\b(u\.?s\.?a\.?|united states)\b/g, ' usa ')
    .replace(/\b(ivory coast)\b/g, ' cote d ivoire ')
    .replace(/\b(women|womens|ladies|zh|femminile|femenino|feminino)\b/g, ' women ')
    .replace(/\b(reserve|reserves|res|ii team|b team)\b/g, ' reserves ')
    .replace(/\b(u17|u18|u19|u20|u21|u23)\b/g, ' ')
    .replace(/\b(iii)\b/g, ' 3 ')
    .replace(/\b(ii)\b/g, ' 2 ')
    .replace(/\b(and)\b/g, ' ')
    .replace(/\b(utd)\b/g, ' united ')
    .replace(/\b(fc|cf|ac|sc|club|fk|bk|afc|calcio|hc|bc|kk|baseball|basketball|hockey|club de futbol|esporte clube|deportivo|de|da|del|cd|ud|sd)\b/g, ' ')
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return value;
}

function canonicalizeTeamName(name) {
  var raw = normalizeTeamName(name);
  if (TEAM_ALIAS_MAP[raw]) return TEAM_ALIAS_MAP[raw];
  var parts = raw.split(' ').filter(function (x) { return x && !TEAM_STOP_WORDS[x]; });
  var compact = parts.join(' ').trim();
  if (TEAM_ALIAS_MAP[compact]) return TEAM_ALIAS_MAP[compact];
  return compact;
}

function canonicalizeLeagueName(name) {
  return transliterateCyrillicToLatin(String(name || ''))
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\benglish premier league\b/g, 'epl')
    .replace(/\bla liga\b/g, 'laliga');
}

function normalizeBookmakerName(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
    .trim();
}

function makeBookmakerLookup(names) {
  var out = {};
  (names || []).forEach(function (name) {
    var key = normalizeBookmakerName(name);
    if (key) out[key] = true;
  });
  return out;
}

function getBookmakerBaseWeight(name, config) {
  var key = normalizeBookmakerName(name);
  if (!key) return 0.9;
  if (key === 'pinnacle') return Number(config && config.bookmakerWeightPinnacle || 1.16);
  if (key === 'betfair' || key === 'betfairexchange') return Number(config && config.bookmakerWeightBetfair || 1.12);
  if (key === 'bet365') return Number(config && config.bookmakerWeightBet365 || 1.08);
  if (key === 'unibet') return Number(config && config.bookmakerWeightUnibet || 1.03);
  if (key === 'williamhill' || key === 'ladbrokes' || key === 'sbobet') return 1.08;
  if (key === 'marathonbet' || key === 'bwin' || key === '888sport' || key === '188bet' || key === 'betvictor' || key === 'cloudbet') return 1.03;
  if (key === '10bet' || key === 'betsson' || key === 'betregal') return 1.01;
  if (key === '1xbet' || key === 'melbet' || key === 'cashpoint' || key === 'betathome') return 0.96;
  return 1.0;
}

function getSourceBaseWeight(sourceName, config) {
  var key = String(sourceName || '').toLowerCase();
  if (key === 'theodds') return Number(config && config.sourceWeightTheOdds || 1.04);
  if (key === 'oddsapiio') return Number(config && config.sourceWeightOddsApiIo || 1.00);
  if (key === 'apifootball') return Number(config && config.sourceWeightApiFootball || 0.96);
  if (key === 'bookiesapi') return Number(config && config.sourceWeightBookiesApi || 0.98);
  if (key === 'sstats') return Number(config && config.sourceWeightSstats || 0.90);
  return 1.0;
}

function getOfferTrustScore(offer, family, config) {
  offer = offer || {};
  var bookWeight = getBookmakerBaseWeight(offer.bookmaker, config);
  var sourceWeight = getSourceBaseWeight(offer.sourceName, config);
  var subtype = String(offer.marketSubType || '');
  var familyAdj = 1.0;
  if (family === 'h2h' || family === 'dnb') familyAdj += 0.03;
  if (family === 'doubleChance') familyAdj -= 0.04;
  if (family === 'btts') familyAdj -= 0.01;
  if (family === 'spreads' || family === 'totals' || family === 'teamTotals') {
    familyAdj -= 0.03;
    if (subtype.indexOf('asian') !== -1) familyAdj += 0.03;
  }
  var score = bookWeight * sourceWeight * familyAdj;
  return clamp(score, 0.72, 1.30);
}

function applyOfferMeta(offer, family, sourceName, config) {
  var copy = Object.assign({}, offer || {});
  if (!copy.sourceName) copy.sourceName = sourceName;
  copy.family = copy.family || family;
  copy.bookmakerKey = normalizeBookmakerName(copy.bookmaker);
  copy.sourceKey = String(copy.sourceName || '').toLowerCase();
  copy.trustScore = round2(getOfferTrustScore(copy, family, config));
  return copy;
}

function countUniqueSourcesForOffers(offers) {
  var seen = {};
  (offers || []).forEach(function (o) {
    var key = String(o && o.sourceName || '').toLowerCase();
    if (key) seen[key] = true;
  });
  return Object.keys(seen).length;
}

function medianByOfferPrice(offers) {
  var prices = (offers || []).map(function (o) { return Number(o && o.price || 0); }).filter(function (v) { return v > 1; });
  return median(prices);
}

function calcOfferPriceDistancePct(price, baselinePrice) {
  var p = Number(price || 0);
  var b = Number(baselinePrice || 0);
  if (!p || p <= 1 || !b || b <= 1) return null;
  return Math.abs(p - b) * 100 / b;
}

function softContainsTeam(a, b) {
  a = canonicalizeTeamName(a);
  b = canonicalizeTeamName(b);
  if (!a || !b) return false;
  if (a === b) return true;
  return a.indexOf(b) !== -1 || b.indexOf(a) !== -1;
}

function fuzzyTeamsEquivalent(homeA, awayA, homeB, awayB) {
  var a1 = canonicalizeTeamName(homeA);
  var a2 = canonicalizeTeamName(awayA);
  var b1 = canonicalizeTeamName(homeB);
  var b2 = canonicalizeTeamName(awayB);
  var direct = softContainsTeam(a1, b1) && softContainsTeam(a2, b2);
  var reverse = softContainsTeam(a1, b2) && softContainsTeam(a2, b1);
  return direct || reverse;
}

function sortPair(a, b) {
  return [a, b].sort().join('|');
}

function buildMatchKey(sport, home, away, isoDate) {
  return String(sport || 'unknown') + '|' +
    sortPair(canonicalizeTeamName(home), canonicalizeTeamName(away)) + '|' +
    getDateKey(isoDate);
}

function buildLooseMatchKey(sport, home, away) {
  return String(sport || 'unknown') + '|' +
    sortPair(canonicalizeTeamName(home), canonicalizeTeamName(away));
}

function pointKey(point) {
  if (point == null || point === '') return 'nopoint';
  return String(round2(point));
}

function getSportLabel(sportKey) {
  return SPORT_CONFIG[sportKey] ? SPORT_CONFIG[sportKey].label : sportKey;
}

function detectSportFromOddsKey(sportKey) {
  var raw = String(sportKey || '').toLowerCase();
  if (raw.indexOf('soccer_') === 0) return 'soccer';
  if (raw.indexOf('basketball_') === 0) return 'basketball';
  if (raw.indexOf('baseball_') === 0) return 'baseball';
  if (raw.indexOf('icehockey_') === 0) return 'icehockey';
  if (raw.indexOf('hockey_') === 0) return 'icehockey';
  return null;
}

function detectSportFromTheOddsMeta(item) {
  var sport = detectSportFromOddsKey(item && item.sport_key);
  if (sport) return sport;
  var group = String(item && (item.group || item.sport_title) || '').toLowerCase();
  if (group.indexOf('soccer') !== -1) return 'soccer';
  if (group.indexOf('basketball') !== -1) return 'basketball';
  if (group.indexOf('baseball') !== -1) return 'baseball';
  if (group.indexOf('ice hockey') !== -1 || group.indexOf('hockey') !== -1) return 'icehockey';
  return null;
}

function sideLabel(side, home, away) {
  if (side === 'home') return home;
  if (side === 'away') return away;
  return 'Ничья';
}

function getLineValue() {
  for (var i = 0; i < arguments.length; i++) {
    var num = toNumber(arguments[i]);
    if (num != null) return num;
  }
  return null;
}

function chunkArray(arr, size) {
  var out = [];
  for (var i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function avg(list) {
  if (!list || !list.length) return null;
  return list.reduce(function (acc, v) { return acc + v; }, 0) / list.length;
}

function median(list) {
  if (!list || !list.length) return null;
  var copy = list.slice().sort(function (a, b) { return a - b; });
  var mid = Math.floor(copy.length / 2);
  if (copy.length % 2) return copy[mid];
  return (copy[mid - 1] + copy[mid]) / 2;
}

function scalePercentTriplet(home, draw, away) {
  var values = [home, draw, away].filter(function (v) { return v != null; });
  if (!values.length) return { home: home, draw: draw, away: away };
  var sum = values.reduce(function (acc, v) { return acc + v; }, 0);
  if (sum > 0 && sum <= 1.5) {
    return {
      home: home != null ? home * 100 : home,
      draw: draw != null ? draw * 100 : draw,
      away: away != null ? away * 100 : away
    };
  }
  return { home: home, draw: draw, away: away };
}

function normalizePercentMaybe(value) {
  var num = toNumber(value);
  if (num == null) return null;
  if (num >= 0 && num <= 1) return num * 100;
  return num;
}

function dateDiffHours(a, b) {
  var d1 = new Date(a);
  var d2 = new Date(b);
  if (isNaN(d1.getTime()) || isNaN(d2.getTime())) return null;
  return Math.abs(d1.getTime() - d2.getTime()) / (60 * 60 * 1000);
}

function matchTeamsEquivalent(homeA, awayA, homeB, awayB) {
  var a1 = canonicalizeTeamName(homeA);
  var a2 = canonicalizeTeamName(awayA);
  var b1 = canonicalizeTeamName(homeB);
  var b2 = canonicalizeTeamName(awayB);
  return (a1 === b1 && a2 === b2) ||
         (a1 === b2 && a2 === b1) ||
         fuzzyTeamsEquivalent(homeA, awayA, homeB, awayB);
}

function makeNormalizedMatch(source, sport, id, oddsEventId, home, away, isoDate, league, status, extras) {
  return Object.assign({
    source: source,
    sport: sport,
    id: String(id || ''),
    oddsEventId: oddsEventId != null ? String(oddsEventId) : null,
    home: home || '',
    away: away || '',
    isoDate: isoDate || '',
    date: new Date(isoDate),
    league: league || 'Unknown League',
    leagueKey: canonicalizeLeagueName(league),
    status: status || '',
    matchKey: buildMatchKey(sport, home, away, isoDate),
    looseKey: buildLooseMatchKey(sport, home, away),
    allSources: source ? (function () { var x = {}; x[source] = true; return x; })() : {}
  }, extras || {});
}

function preferIncomingName(current, incoming) {
  if (!incoming) return current;
  if (!current) return incoming;
  if (incoming.length > current.length) return incoming;
  return current;
}

function calcLeaguePriority(match, config) {
  if (!match) return 0;
  var league = String(match.league || '').toLowerCase();
  if (!league) return 0;
  var score = 0;
  (config.allowedLeagueKeywords || []).forEach(function (kw) {
    if (kw && league.indexOf(String(kw).toLowerCase()) !== -1) score += 3;
  });
  if (league.indexOf('world cup') !== -1) score += 8;
  if (league.indexOf('champions') !== -1) score += 7;
  if (league.indexOf('premier') !== -1) score += 6;
  if (league.indexOf('serie a') !== -1) score += 6;
  if (league.indexOf('la liga') !== -1) score += 6;
  if (league.indexOf('bundesliga') !== -1) score += 6;
  if (league.indexOf('ligue 1') !== -1) score += 6;
  if (league.indexOf('championship') !== -1) score += 5;
  if (match.sport === 'soccer') score += 2;
  return score;
}

function createEmptyMarkets() {
  var out = {};
  MARKET_FAMILIES.forEach(function (family) { out[family] = []; });
  return out;
}

function hasAnyOffers(odds) {
  if (!odds) return false;
  return MARKET_FAMILIES.some(function (family) { return odds[family] && odds[family].length; });
}

function countUniqueBooksAcrossOffers(odds) {
  var books = {};
  MARKET_FAMILIES.forEach(function (family) {
    (odds[family] || []).forEach(function (o) {
      var key = normalizeBookmakerName(o.bookmaker);
      if (key) books[key] = true;
    });
  });
  return Object.keys(books).length;
}

function countCoveredFamilies(odds) {
  var count = 0;
  MARKET_FAMILIES.forEach(function (family) {
    if (odds[family] && odds[family].length) count += 1;
  });
  return count;
}

/* ======================= MATCH SOURCES ======================= */
function getBzzoiroEvents(config) {
  if (!config.bzzoiroApiKey || config.enabledSports.indexOf('soccer') === -1) return [];
  var range = getDateRange(config.timezone, config.daysAhead);
  var url = 'https://sports.bzzoiro.com/api/events/?' + buildQuery({
    date_from: range.from,
    date_to: range.to,
    status: 'notstarted'
  });
  var data = fetchJson(url, { headers: { Authorization: 'Token ' + config.bzzoiroApiKey } }, 'Bzzoiro events', { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds);
  return data && data.results ? data.results : [];
}

function normalizeBzzoiroEvent(m) {
  return makeNormalizedMatch(
    'bzzoiro',
    'soccer',
    m.id,
    null,
    m.home_team,
    m.away_team,
    m.event_date,
    (m.league && m.league.name) || m.league_name || 'Unknown League',
    m.status || 'notstarted',
    { bzzoiroEventId: m.id }
  );
}

function getOddsApiIoSportsMap(config) {
  if (!config.oddsApiIoKey) return {};
  var hardMap = {
    soccer: 'football',
    basketball: 'basketball',
    baseball: 'baseball',
    icehockey: 'ice-hockey'
  };
  var result = {};
  config.enabledSports.forEach(function (sportKey) {
    if (hardMap[sportKey]) result[sportKey] = hardMap[sportKey];
  });
  Logger.log('Odds-API.io sport slugs: ' + JSON.stringify(result));
  return result;
}

function getSportEventTarget(config, sportKey, enabledSports) {
  var sports = (enabledSports || config.enabledSports || []).slice();
  if (!sports.length) return Math.max(40, Math.floor((config.maxMatchesForOddsFetch || 200) / 2));
  var weights = {
    soccer: 1.25,
    basketball: 1.0,
    icehockey: 1.0,
    baseball: 0.9
  };
  var totalWeight = sports.reduce(function (acc, key) { return acc + (weights[key] || 1); }, 0) || sports.length;
  var targetTotal = Math.max(80, Math.round((config.maxMatchesForOddsFetch || 200) * (config.oddsApiIoEventTargetShare || 0.88)));
  var share = targetTotal * ((weights[sportKey] || 1) / totalWeight);
  return Math.max(35, Math.round(share));
}

function trimRankedOddsApiIoMatches(matches, targetCount, tips, config) {
  var seen = {};
  var ranked = [];
  (matches || []).forEach(function (match) {
    if (!match || !match.matchKey || seen[match.matchKey]) return;
    seen[match.matchKey] = true;
    ranked.push({ match: match, score: scoreMatchForFetch(match, tips, config) });
  });
  ranked.sort(function (a, b) {
    if (b.score !== a.score) return b.score - a.score;
    return a.match.date.getTime() - b.match.date.getTime();
  });
  return ranked.slice(0, Math.max(1, targetCount)).map(function (x) { return x.match; });
}

function getOddsApiIoEventsForSport(config, sportKey, slug, tipsBundle) {
  if (!config.oddsApiIoKey || !slug) return [];
  var range = getDateRange(config.timezone, config.daysAhead);
  var fromIso = range.from + 'T00:00:00Z';
  var toIso = range.to + 'T23:59:59Z';
  var limit = Math.max(20, Math.min(100, config.oddsApiIoPageLimit || 60));
  var maxPages = Math.max(1, config.oddsApiIoMaxEventPagesPerSport || 4);
  var minPages = Math.max(1, config.oddsApiIoEventMinPagesPerSport || 2);
  var target = getSportEventTarget(config, sportKey, config.enabledSports || []);
  var buffer = Math.max(0, config.oddsApiIoEventTargetBuffer || 0);
  var selected = [];
  var skip = 0;
  var pageNo = 0;
  var tipsIndex = tipsBundle ? tipsBundle.index : null;

  while (pageNo < maxPages) {
    var params = {
      apiKey: config.oddsApiIoKey,
      sport: slug,
      status: 'pending',
      from: fromIso,
      to: toIso,
      limit: limit,
      skip: skip
    };
    var url = 'https://api.odds-api.io/v3/events?' + buildQuery(params);
    var data = fetchJson(
      url,
      {},
      'Odds-API.io events ' + sportKey + ' skip=' + skip,
      { config: config, sportKey: sportKey, cost: 1 },
      config.oddsApiIoEventCacheSeconds || config.cacheSeconds || 0
    );
    var page = Array.isArray(data) ? data : [];
    page.forEach(function (row) {
      selected.push(normalizeOddsApiIoEvent(row, sportKey));
    });

    selected = trimRankedOddsApiIoMatches(selected, target + buffer, tipsIndex, config);
    Logger.log('Odds-API.io events page ' + sportKey + ': ' + page.length + ' rows, retained=' + selected.length + ', target=' + target);

    pageNo += 1;
    if (page.length < limit) break;
    if (pageNo >= minPages && selected.length >= target + buffer) break;
    skip += limit;
  }
  return trimRankedOddsApiIoMatches(selected, target + buffer, tipsIndex, config);
}

function normalizeOddsApiIoEvent(e, sportKey) {
  return makeNormalizedMatch(
    'oddsApiIo',
    sportKey,
    e.id,
    e.id,
    e.home,
    e.away,
    e.date || e.start_time,
    (e.league && e.league.name) || e.league_name || e.league || 'Unknown League',
    e.status || 'pending'
  );
}

function isTheOddsTemporarilyDisabled(config) {
  var untilTs = Number(RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS || 0);
  if (!untilTs) return false;
  if (Date.now() > untilTs) {
    delete RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS;
    return false;
  }
  return true;
}

function markTheOddsTemporarilyDisabled(config, reason) {
  var hours = Math.max(1, Number(config.theOddsDisableHours || 6));
  var untilTs = Date.now() + hours * 60 * 60 * 1000;
  RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS = untilTs;
  Logger.log('The Odds API временно отключен на ' + hours + ' ч. Причина: ' + (reason || 'unknown'));
}

function getTheOddsSports(config) {
  if (!config.oddsApiKey) return [];
  if (isTheOddsTemporarilyDisabled(config)) {
    Logger.log('The Odds API sports skipped: источник временно отключён после прошлой ошибки квоты');
    return [];
  }
  var url = 'https://api.the-odds-api.com/v4/sports/?' + buildQuery({ apiKey: config.oddsApiKey });
  var data = fetchJson(url, {}, 'The Odds API sports', { config: config, multiSportKeys: config.enabledSports.slice(), cost: 1 }, config.cacheSeconds);
  var list = Array.isArray(data) ? data : [];
  var filtered = list.filter(function (item) {
    if (!item || !item.active || item.has_outrights) return false;
    return config.enabledSports.some(function (sportKey) {
      var cfg = SPORT_CONFIG[sportKey];
      if (!cfg) return false;
      var group = String(item.group || '').toLowerCase();
      var key = String(item.key || '').toLowerCase();
      var groupMatch = (cfg.theOddsGroups || []).some(function (g) { return group === String(g).toLowerCase(); });
      var keyMatch = (cfg.theOddsSportKeyPrefixes || []).some(function (p) { return key.indexOf(String(p).toLowerCase()) === 0; });
      return groupMatch || keyMatch;
    });
  });

  var priority = {};
  (config.theOddsPriorityKeys || []).forEach(function (k, idx) { priority[String(k)] = idx + 1; });
  filtered.sort(function (a, b) {
    var pa = priority[a.key] || 9999;
    var pb = priority[b.key] || 9999;
    if (pa !== pb) return pa - pb;
    return String(a.key || '').localeCompare(String(b.key || ''));
  });
  var selected = filtered.slice(0, config.theOddsMaxSportsPerRun || 12);
  Logger.log('The Odds API sports selected: ' + selected.length + ' / ' + filtered.length);
  return selected;
}

function getTheOddsApiFeed(config, sportsList) {
  if (!config.oddsApiKey) return [];
  if (isTheOddsTemporarilyDisabled(config)) {
    Logger.log('The Odds API odds skipped: источник временно отключён после прошлой ошибки квоты');
    return [];
  }
  var feeds = [];
  var range = getDateRange(config.timezone, config.daysAhead);
  var stopDueToQuota = false;

  (sportsList || []).forEach(function (sportMeta) {
    if (stopDueToQuota) return;
    var url = 'https://api.the-odds-api.com/v4/sports/' + encodeURIComponent(sportMeta.key) + '/odds/?' + buildQuery({
      apiKey: config.oddsApiKey,
      regions: (config.theOddsRegions || []).join(','),
      markets: (config.theOddsMarkets || []).join(','),
      oddsFormat: 'decimal',
      dateFormat: 'iso'
    });
    var data = fetchJson(url, {}, 'The Odds API odds ' + sportMeta.key, { config: config, sportKey: detectSportFromOddsKey(sportMeta.key) || detectSportFromTheOddsMeta(sportMeta) || 'soccer', cost: 1 }, 0);
    if (!data) {
      Logger.log('The Odds API feed stopped early: likely quota/frequency issue for ' + sportMeta.key);
      markTheOddsTemporarilyDisabled(config, sportMeta.key);
      stopDueToQuota = true;
      return;
    }
    var rows = Array.isArray(data) ? data : [];
    rows.forEach(function (item) { item.__sportMetaGroup = sportMeta.group || ''; });
    feeds = feeds.concat(rows.filter(function (item) {
      var dateKey = getDateKey(item.commence_time || item.commenceTime || item.date || '');
      return dateKey >= range.from && dateKey <= range.to;
    }));
  });

  Logger.log('The Odds API loaded sport feeds: ' + feeds.length);
  return feeds;
}

function normalizeTheOddsEvent(item) {
  return makeNormalizedMatch(
    'theOdds',
    detectSportFromTheOddsMeta(item),
    item.id,
    item.id,
    item.home_team || item.homeTeam || item.home,
    item.away_team || item.awayTeam || item.away,
    item.commence_time || item.commenceTime || item.date,
    item.sport_title || item.__sportMetaGroup || 'Unknown League',
    'upcoming'
  );
}

function getApiFootballFixtures(config) {
  if (!config.apiFootballKey || config.enabledSports.indexOf('soccer') === -1) return [];
  var range = getDateRange(config.timezone, config.daysAhead);
  var url = 'https://' + config.apiFootballHost + '/fixtures?' + buildQuery({
    from: range.from,
    to: range.to,
    timezone: config.apiFootballTimezone,
    status: 'NS-TBD-PST'
  });
  var data = fetchJson(url, { headers: { 'x-apisports-key': config.apiFootballKey } }, 'API-Football fixtures', { config: config, sportKey: 'soccer', cost: 1 }, config.apiFootballFixturesCacheSeconds || config.cacheSeconds || 0);
  return data && data.response ? data.response : [];
}

function normalizeApiFootballFixture(row) {
  return makeNormalizedMatch(
    'apiFootball',
    'soccer',
    row && row.fixture && row.fixture.id,
    row && row.fixture && row.fixture.id,
    row && row.teams && row.teams.home && row.teams.home.name,
    row && row.teams && row.teams.away && row.teams.away.name,
    row && row.fixture && row.fixture.date,
    row && row.league && row.league.name,
    row && row.fixture && row.fixture.status && row.fixture.status.short,
    {
      apiFootballFixtureId: row && row.fixture && row.fixture.id,
      apiFootballLeagueId: row && row.league && row.league.id,
      apiFootballSeason: row && row.league && row.league.season,
      apiFootballHomeTeamId: row && row.teams && row.teams.home && row.teams.home.id,
      apiFootballAwayTeamId: row && row.teams && row.teams.away && row.teams.away.id
    }
  );
}

function isProbablyExoticLeague(match, config) {
  if (!match) return true;
  var league = String(match.league || '').toLowerCase();
  var allowed = config.allowedLeagueKeywords || [];
  var blocked = config.blockedLeagueKeywords || [];
  var hasAllowed = allowed.some(function (k) { return k && league.indexOf(String(k).toLowerCase()) !== -1; });
  var hasBlocked = blocked.some(function (k) { return k && league.indexOf(String(k).toLowerCase()) !== -1; });
  if (hasBlocked && !hasAllowed) return true;
  if (match.sport === 'soccer' && !hasAllowed) return true;
  return false;
}

function mergeMatchFields(existing, incoming) {
  if (!existing.oddsEventId && incoming.oddsEventId) existing.oddsEventId = incoming.oddsEventId;
  if (!existing.apiFootballFixtureId && incoming.apiFootballFixtureId) existing.apiFootballFixtureId = incoming.apiFootballFixtureId;
  if (!existing.apiFootballLeagueId && incoming.apiFootballLeagueId) existing.apiFootballLeagueId = incoming.apiFootballLeagueId;
  if (!existing.apiFootballSeason && incoming.apiFootballSeason) existing.apiFootballSeason = incoming.apiFootballSeason;
  if (!existing.apiFootballHomeTeamId && incoming.apiFootballHomeTeamId) existing.apiFootballHomeTeamId = incoming.apiFootballHomeTeamId;
  if (!existing.apiFootballAwayTeamId && incoming.apiFootballAwayTeamId) existing.apiFootballAwayTeamId = incoming.apiFootballAwayTeamId;
  if (!existing.sstatsGameKey && incoming.sstatsGameKey) existing.sstatsGameKey = incoming.sstatsGameKey;
  if ((!existing.league || existing.league === 'Unknown League') && incoming.league) existing.league = incoming.league;
  if ((!existing.status || existing.status === 'notstarted') && incoming.status) existing.status = incoming.status;
  existing.home = preferIncomingName(existing.home, incoming.home);
  existing.away = preferIncomingName(existing.away, incoming.away);
  if (incoming.source !== existing.source) existing.allSources[incoming.source] = true;
  if (existing.source === 'bzzoiro' && incoming.source !== 'bzzoiro') {
    existing.source = incoming.source;
    existing.id = incoming.id || existing.id;
  }
}

function mergeMatches(all, config) {
  var filtered = all.filter(function (m) {
    return m && m.sport && m.home && m.away && m.date && !isNaN(m.date.getTime());
  });

  filtered.sort(function (a, b) {
    var rank = { apiFootball: 5, oddsApiIo: 4, theOdds: 3, bzzoiro: 2 };
    return (rank[b.source] || 0) - (rank[a.source] || 0);
  });

  var byExact = {};
  var merged = [];
  filtered.forEach(function (m) {
    var existing = byExact[m.matchKey];
    if (!existing) {
      byExact[m.matchKey] = m;
      merged.push(m);
      return;
    }
    mergeMatchFields(existing, m);
  });

  var final = [];
  var used = {};
  var toleranceHours = config.matchStartToleranceHours || 12;

  merged.forEach(function (base) {
    if (used[base.matchKey]) return;
    var keeper = base;
    for (var i = 0; i < merged.length; i++) {
      var candidate = merged[i];
      if (candidate === keeper) continue;
      if (used[candidate.matchKey]) continue;
      if (candidate.sport !== keeper.sport) continue;
      if (candidate.looseKey !== keeper.looseKey && !fuzzyTeamsEquivalent(candidate.home, candidate.away, keeper.home, keeper.away)) continue;
      var diff = dateDiffHours(candidate.isoDate, keeper.isoDate);
      if (diff == null || diff > toleranceHours) continue;
      mergeMatchFields(keeper, candidate);
      used[candidate.matchKey] = true;
    }
    keeper.leaguePriority = calcLeaguePriority(keeper, config);
    keeper.isExoticLeague = isProbablyExoticLeague(keeper, config);
    final.push(keeper);
  });

  if (config.excludeExoticLeagues) {
    final = final.filter(function (m) { return !m.isExoticLeague; });
  }

  Logger.log('Нормализованных матчей: ' + final.length);
  return final;
}

function scoreMatchForFetch(match, tips, config) {
  var score = 0;
  if (tips && tips[match.matchKey]) score += 100;
  if (match.sport === 'soccer') score += 20;
  if (match.apiFootballFixtureId) score += 10;
  if (match.oddsEventId) score += 8;
  if (match.source === 'oddsApiIo') score += 8;
  if (match.source === 'apiFootball') score += 8;
  if (match.source === 'theOdds') score += 6;
  if (match.leaguePriority) score += match.leaguePriority * 2;
  if (match.isExoticLeague) score -= 20;
  return score;
}

function getAllMatches(config, theOddsFeed, tipsBundle) {
  Logger.log('Получение матчей из всех источников...');
  var all = [];
  if (tipsBundle && tipsBundle.matches && tipsBundle.matches.length) {
    tipsBundle.matches.forEach(function (m) { all.push(m); });
  } else if (config.useBzzoiroEventsFallback) {
    getBzzoiroEvents(config).forEach(function (m) { all.push(normalizeBzzoiroEvent(m)); });
  }

  var ioSportMap = getOddsApiIoSportsMap(config);
  config.enabledSports.forEach(function (sportKey) {
    getOddsApiIoEventsForSport(config, sportKey, ioSportMap[sportKey], tipsBundle).forEach(function (m) {
      all.push(m);
    });
  });

  (theOddsFeed || []).forEach(function (item) { all.push(normalizeTheOddsEvent(item)); });
  getApiFootballFixtures(config).forEach(function (row) { all.push(normalizeApiFootballFixture(row)); });

  var merged = mergeMatches(all, config);
  merged.sort(function (a, b) {
    var sa = scoreMatchForFetch(a, tipsBundle ? tipsBundle.index : null, config);
    var sb = scoreMatchForFetch(b, tipsBundle ? tipsBundle.index : null, config);
    if (sb !== sa) return sb - sa;
    return a.date.getTime() - b.date.getTime();
  });
  if (merged.length > config.maxMatchesForOddsFetch) merged = merged.slice(0, config.maxMatchesForOddsFetch);
  Logger.log('Матчей после лимита на odds-fetch: ' + merged.length);
  return merged;
}

/* ======================= PREDICTIONS ======================= */
function getBzzoiroPredictionBundle(config) {
  var bundle = { index: {}, matches: [] };
  if (!config.bzzoiroApiKey || config.enabledSports.indexOf('soccer') === -1) return bundle;
  var url = 'https://sports.bzzoiro.com/api/predictions/?upcoming=true';
  var data = fetchJson(url, { headers: { Authorization: 'Token ' + config.bzzoiroApiKey } }, 'Bzzoiro predictions', { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds);
  var results = data && data.results ? data.results : [];
  results.forEach(function (item) {
    if (!item.event) return;
    var scaled = scalePercentTriplet(toNumber(item.prob_home_win), toNumber(item.prob_draw), toNumber(item.prob_away_win));
    var key = buildMatchKey('soccer', item.event.home_team, item.event.away_team, item.event.event_date);
    bundle.index[key] = {
      home: scaled.home,
      draw: scaled.draw,
      away: scaled.away,
      over25: normalizePercentMaybe(item.prob_over_25),
      bttsYes: normalizePercentMaybe(item.prob_btts_yes),
      confidence: normalizePercentMaybe(item.confidence)
    };
    bundle.matches.push(normalizeBzzoiroEvent(item.event));
  });
  Logger.log('Bzzoiro прогнозов получено: ' + Object.keys(bundle.index).length);
  return bundle;
}


/* ======================= SSTATS ======================= */
function buildSstatsUrl(path, config) {
  var url = String(path || '');
  if (!url) return '';
  if (url.indexOf('http://') !== 0 && url.indexOf('https://') !== 0) {
    url = 'https://api.sstats.net' + (url.charAt(0) === '/' ? '' : '/') + url;
  }
  if (config && config.sstatsApiKey && url.toLowerCase().indexOf('apikey=') === -1) {
    url += (url.indexOf('?') === -1 ? '?' : '&') + 'apikey=' + encodeURIComponent(config.sstatsApiKey);
  }
  return url;
}

function fetchSstatsJson(pathOrUrl, config, label, cacheSeconds) {
  if (!config || (!config.enableSstatsContext && !config.enableSstatsMarkets)) return null;
  var url = buildSstatsUrl(pathOrUrl, config);
  if (!url) return null;
  return fetchJson(url, {}, label || 'SStats', null, cacheSeconds || config.sstatsCacheSeconds || 0);
}

function unwrapSstatsData(payload) {
  if (!payload) return null;
  if (payload.data != null) return payload.data;
  return payload;
}

function getFirstExisting(obj, paths) {
  for (var i = 0; i < paths.length; i++) {
    var parts = paths[i].split('.');
    var cur = obj;
    var ok = true;
    for (var j = 0; j < parts.length; j++) {
      if (cur == null || !Object.prototype.hasOwnProperty.call(cur, parts[j])) { ok = false; break; }
      cur = cur[parts[j]];
    }
    if (ok && cur != null) return cur;
  }
  return null;
}

function extractSstatsGameListCandidates(payload) {
  var data = unwrapSstatsData(payload);
  if (!data) return [];
  if (Array.isArray(data)) return data;
  var arr = getFirstExisting(data, ['games', 'list', 'items', 'matches', 'fixtures']);
  if (Array.isArray(arr)) return arr;
  return [];
}

function normalizeSstatsGameListItem(item) {
  if (!item) return null;
  var home = getFirstExisting(item, ['homeTeam.name', 'home.name', 'team_home.name', 'homeTeamName', 'home_name']);
  var away = getFirstExisting(item, ['awayTeam.name', 'away.name', 'team_away.name', 'awayTeamName', 'away_name']);
  var isoDate = getFirstExisting(item, ['startTime', 'date', 'datetime', 'kickoff', 'matchTime']);
  var league = getFirstExisting(item, ['league.name', 'tournament.name', 'competition.name', 'leagueName', 'countryLeague']);
  var id = getFirstExisting(item, ['id', 'gameId', 'flashId']);
  var flashId = getFirstExisting(item, ['flashId', 'id', 'gameId']);
  if (!id && !flashId) return null;
  if (!home || !away) return null;
  return {
    id: String(id || flashId),
    flashId: String(flashId || id),
    home: String(home || ''),
    away: String(away || ''),
    isoDate: String(isoDate || ''),
    league: String(league || ''),
    dateKey: getDateKey(isoDate)
  };
}

function fetchSstatsGamesListRange(fromDate, toDate, config, memo) {
  memo = memo || {};
  var limit = Math.max(50, Math.min(1000, config.sstatsListLimit || 1000));
  var key = fromDate + '|' + toDate + '|' + limit;
  if (memo[key] !== undefined) return memo[key];
  var url = '/Games/list?' + buildQuery({ from: fromDate, to: toDate, limit: limit });
  var data = fetchSstatsJson(url, config, 'SStats Games/list ' + fromDate + '...' + toDate, config.sstatsListCacheSeconds || 900);
  memo[key] = extractSstatsGameListCandidates(data).map(normalizeSstatsGameListItem).filter(function (x) { return !!x; });
  Logger.log('SStats Games/list loaded: ' + memo[key].length + ' rows for ' + fromDate + '...' + toDate);
  return memo[key];
}

function scoreSstatsCandidateForMatch(match, candidate, dateToleranceHours) {
  if (!match || !candidate) return -999;
  var score = 0;
  if (fuzzyTeamsEquivalent(match.home, match.away, candidate.home, candidate.away)) score += 100;
  else return -999;
  var diff = dateDiffHours(match.isoDate, candidate.isoDate);
  if (diff == null) diff = 999;
  if (diff > dateToleranceHours) return -999;
  score += Math.max(0, 30 - diff);
  if (candidate.league && match.league) {
    if (canonicalizeLeagueName(candidate.league) === canonicalizeLeagueName(match.league)) score += 12;
    else if (canonicalizeLeagueName(candidate.league).indexOf(canonicalizeLeagueName(match.league)) !== -1 || canonicalizeLeagueName(match.league).indexOf(canonicalizeLeagueName(candidate.league)) !== -1) score += 5;
  }
  return score;
}

function enrichMatchesWithSstatsGameKeys(matches, config, listMemo, lookupMemo) {
  if (!config || !config.sstatsAutoLookup || !(config.enableSstatsContext || config.enableSstatsMarkets)) return 0;
  var soccerMatches = (matches || []).filter(function (m) { return m && m.sport === 'soccer' && !m.sstatsGameKey; });
  if (!soccerMatches.length) return 0;

  var minTs = null;
  var maxTs = null;
  soccerMatches.forEach(function (m) {
    var d = new Date(m.isoDate);
    if (isNaN(d.getTime())) return;
    if (minTs == null || d.getTime() < minTs) minTs = d.getTime();
    if (maxTs == null || d.getTime() > maxTs) maxTs = d.getTime();
  });
  if (minTs == null || maxTs == null) return 0;
  var fromDate = Utilities.formatDate(new Date(minTs - 24 * 60 * 60 * 1000), config.timezone, 'yyyy-MM-dd');
  var toDate = Utilities.formatDate(new Date(maxTs + 24 * 60 * 60 * 1000), config.timezone, 'yyyy-MM-dd');
  var candidates = fetchSstatsGamesListRange(fromDate, toDate, config, listMemo);
  if (!candidates.length) return 0;

  var assigned = 0;
  soccerMatches.forEach(function (match) {
    if (match.sstatsGameKey) return;
    if (config.sstatsGameKeyMap && config.sstatsGameKeyMap[match.matchKey]) {
      match.sstatsGameKey = config.sstatsGameKeyMap[match.matchKey];
      assigned += 1;
      return;
    }
    var best = null;
    var bestScore = -999;
    candidates.forEach(function (candidate) {
      var score = scoreSstatsCandidateForMatch(match, candidate, Math.min(config.matchStartToleranceHours || 12, 18));
      if (score > bestScore) {
        bestScore = score;
        best = candidate;
      }
    });
    if (best && bestScore >= 104) {
      match.sstatsGameKey = best.id || best.flashId;
      assigned += 1;
      return;
    }
    var fallback = resolveSstatsGameKeyForMatch(match, config, lookupMemo);
    if (fallback) assigned += 1;
  });
  Logger.log('SStats game keys auto-mapped: ' + assigned + ' / ' + soccerMatches.length);
  return assigned;
}

function extractSstatsGameKeyFromLookup(data) {
  if (!data) return null;
  var direct = getFirstExisting(data, ['data.game', 'data.fixture', 'data.match', 'game', 'fixture', 'match']);
  if (direct) return direct.flashId || direct.id || null;
  var candidates = extractSstatsGameListCandidates(data);
  if (!candidates.length) return null;
  var first = normalizeSstatsGameListItem(candidates[0]);
  return first ? (first.id || first.flashId) : null;
}

function resolveSstatsGameKeyForMatch(match, config, lookupMemo) {
  if (!match || match.sport !== 'soccer' || !config || !(config.enableSstatsContext || config.enableSstatsMarkets)) return null;
  if (match.sstatsGameKey) return match.sstatsGameKey;
  lookupMemo = lookupMemo || {};
  if (config.sstatsGameKeyMap && config.sstatsGameKeyMap[match.matchKey]) {
    match.sstatsGameKey = config.sstatsGameKeyMap[match.matchKey];
    return match.sstatsGameKey;
  }
  if (config.sstatsGameKeyMap && config.sstatsGameKeyMap[match.looseKey]) {
    match.sstatsGameKey = config.sstatsGameKeyMap[match.looseKey];
    return match.sstatsGameKey;
  }
  if (!config.sstatsLookupUrlTemplate) return null;
  if (lookupMemo[match.matchKey] !== undefined) return lookupMemo[match.matchKey];
  var url = String(config.sstatsLookupUrlTemplate)
    .replace(/\{date\}/g, encodeURIComponent(getDateKey(match.isoDate)))
    .replace(/\{home\}/g, encodeURIComponent(match.home || ''))
    .replace(/\{away\}/g, encodeURIComponent(match.away || ''))
    .replace(/\{league\}/g, encodeURIComponent(match.league || ''));
  var data = fetchSstatsJson(url, config, 'SStats lookup ' + match.matchKey, config.sstatsCacheSeconds || 1800);
  var key = extractSstatsGameKeyFromLookup(data);
  lookupMemo[match.matchKey] = key || null;
  if (key) match.sstatsGameKey = key;
  return lookupMemo[match.matchKey];
}

function extractSstatsOddsRows(payload, detailPayload) {
  var data = unwrapSstatsData(payload);
  var rows = [];
  if (data) {
    if (Array.isArray(data)) rows = data;
    else if (Array.isArray(data.odds)) rows = data.odds;
    else if (Array.isArray(data.markets)) rows = data.markets;
    else if (Array.isArray(data.bookmakers)) rows = data.bookmakers;
  }
  if (!rows.length && detailPayload) {
    var detailData = unwrapSstatsData(detailPayload);
    var game = detailData && detailData.game ? detailData.game : null;
    if (game && Array.isArray(game.odds)) rows = game.odds;
  }
  return rows;
}

function countSstatsStartingPlayers(players, teamId) {
  return (players || []).filter(function (p) {
    return String(p && p.teamId) === String(teamId) && !!(p && (p.startXI || p.isStarting || p.isStarter));
  }).length;
}

function parseSstatsSide(rawName, match) {
  var name = String(rawName || '').toLowerCase().trim();
  if (name === 'home' || name === '1' || name === 'win') return { family: 'h2h', selection: match.home };
  if (name === 'away' || name === '2') return { family: 'h2h', selection: match.away };
  if (name === 'draw' || name === 'x') return { family: 'h2h', selection: 'Draw' };
  if (name === 'home/draw' || name === '1x') return { family: 'doubleChance', selection: '1X' };
  if (name === 'draw/away' || name === 'x2') return { family: 'doubleChance', selection: 'X2' };
  if (name === 'home/away' || name === '12') return { family: 'doubleChance', selection: '12' };
  return null;
}

function parseSstatsOddsRowsToMarkets(rows, match) {
  var markets = createEmptyMarkets();
  (rows || []).forEach(function (market) {
    var marketName = String(market.marketName || market.name || market.market || '').toLowerCase();
    var oddsItems = Array.isArray(market.odds) ? market.odds : (Array.isArray(market.values) ? market.values : []);
    oddsItems.forEach(function (odd) {
      var price = toNumber(odd && (odd.value || odd.odd || odd.price));
      if (!price || price <= 1) return;
      var name = String(odd && (odd.name || odd.valueName || odd.label) || '');
      if (marketName.indexOf('match winner') !== -1 || marketName === '1x2') {
        var parsed = parseSstatsSide(name, match);
        if (parsed && parsed.family === 'h2h') pushOffer(markets, 'h2h', { name: parsed.selection, price: price, bookmaker: 'SStats', point: null, marketKey: 'sstats_match_winner', marketName: 'Match Winner', marketSubType: 'regular_time_3way' });
        return;
      }
      if (marketName.indexOf('double chance') !== -1) {
        var parsedDc = parseSstatsSide(name, match);
        if (parsedDc && parsedDc.family === 'doubleChance') pushOffer(markets, 'doubleChance', { name: parsedDc.selection, price: price, bookmaker: 'SStats', point: null, marketKey: 'sstats_double_chance', marketName: 'Double Chance', marketSubType: 'double_chance' });
        return;
      }
      if (marketName.indexOf('goals over/under') !== -1 || marketName.indexOf('total goals') !== -1 || marketName === 'over/under') {
        var side = /^over/i.test(name) ? 'Over' : (/^under/i.test(name) ? 'Under' : null);
        if (!side) return;
        var point = toNumber(String(name).replace(/^over\s*/i, '').replace(/^under\s*/i, '').replace(',', '.'));
        if (point == null) return;
        pushOffer(markets, 'totals', { name: side, price: price, bookmaker: 'SStats', point: point, marketKey: 'sstats_totals', marketName: 'Goals Over/Under', marketSubType: 'totals' });
        return;
      }
      if (marketName.indexOf('total - home') !== -1 || marketName.indexOf('team total home') !== -1 || marketName.indexOf('home total') !== -1) {
        var sideSel = /^over/i.test(name) ? 'Over' : (/^under/i.test(name) ? 'Under' : null);
        if (!sideSel) return;
        var point2 = toNumber(String(name).replace(/^over\s*/i, '').replace(/^under\s*/i, '').replace(',', '.'));
        if (point2 == null) return;
        pushOffer(markets, 'teamTotals', { name: sideSel, price: price, bookmaker: 'SStats', point: point2, marketKey: 'sstats_team_total_home', marketName: market.marketName || market.name || '', marketSubType: 'team_totals', teamSide: 'home' });
        return;
      }
      if (marketName.indexOf('total - away') !== -1 || marketName.indexOf('team total away') !== -1 || marketName.indexOf('away total') !== -1) {
        var sideSel2 = /^over/i.test(name) ? 'Over' : (/^under/i.test(name) ? 'Under' : null);
        if (!sideSel2) return;
        var point3 = toNumber(String(name).replace(/^over\s*/i, '').replace(/^under\s*/i, '').replace(',', '.'));
        if (point3 == null) return;
        pushOffer(markets, 'teamTotals', { name: sideSel2, price: price, bookmaker: 'SStats', point: point3, marketKey: 'sstats_team_total_away', marketName: market.marketName || market.name || '', marketSubType: 'team_totals', teamSide: 'away' });
      }
    });
  });
  return markets;
}

function parseSstatsProfits(payload) {
  var data = unwrapSstatsData(payload);
  var rows = [];
  if (Array.isArray(data)) rows = data;
  else if (data && Array.isArray(data.profits)) rows = data.profits;
  else if (data && Array.isArray(data.items)) rows = data.items;
  var out = { sampleSize: rows.length, homeWinRoi: null, awayWinRoi: null, over25Roi: null, under25Roi: null, bttsYesRoi: null, bttsNoRoi: null };
  rows.forEach(function (row) {
    var label = String(row && (row.name || row.strategy || row.market || row.type) || '').toLowerCase();
    var roi = toNumber(row && (row.roi || row.yield || row.profitPercent || row.profit_pct));
    if (roi == null) return;
    if (label.indexOf('home') !== -1 && label.indexOf('win') !== -1) out.homeWinRoi = roi;
    else if (label.indexOf('away') !== -1 && label.indexOf('win') !== -1) out.awayWinRoi = roi;
    else if (label.indexOf('over 2.5') !== -1) out.over25Roi = roi;
    else if (label.indexOf('under 2.5') !== -1) out.under25Roi = roi;
    else if (label.indexOf('btts yes') !== -1 || label.indexOf('both teams to score yes') !== -1) out.bttsYesRoi = roi;
    else if (label.indexOf('btts no') !== -1 || label.indexOf('both teams to score no') !== -1) out.bttsNoRoi = roi;
  });
  return out;
}

function fetchSstatsGameBundle(gameKey, config, detailMemo, glickoMemo, oddsMemo, profitsMemo) {
  if (!gameKey || !config) return null;
  detailMemo = detailMemo || {};
  glickoMemo = glickoMemo || {};
  oddsMemo = oddsMemo || {};
  profitsMemo = profitsMemo || {};
  if (!detailMemo[gameKey]) detailMemo[gameKey] = fetchSstatsJson('/Games/' + encodeURIComponent(gameKey), config, 'SStats game ' + gameKey, config.sstatsCacheSeconds || 1800);
  if (!glickoMemo[gameKey]) glickoMemo[gameKey] = fetchSstatsJson('/Games/glicko/' + encodeURIComponent(gameKey), config, 'SStats glicko ' + gameKey, config.sstatsGlickoCacheSeconds || 21600);
  if (!oddsMemo[gameKey]) oddsMemo[gameKey] = fetchSstatsJson('/Odds/' + encodeURIComponent(gameKey), config, 'SStats odds ' + gameKey, config.sstatsCacheSeconds || 1800);
  if (config.sstatsEnableProfits && !profitsMemo[gameKey]) profitsMemo[gameKey] = fetchSstatsJson('/Games/profits?' + buildQuery({ gameId: gameKey, limit: 25, thisLeague: true }), config, 'SStats profits ' + gameKey, config.sstatsProfitsCacheSeconds || 21600);
  return { detail: detailMemo[gameKey], glicko: glickoMemo[gameKey], odds: oddsMemo[gameKey], profits: profitsMemo[gameKey] };
}

function buildSstatsContextForMatch(match, config, detailMemo, glickoMemo, oddsMemo, profitsMemo, lookupMemo) {
  if (!match || match.sport !== 'soccer' || !config || !(config.enableSstatsContext || config.enableSstatsMarkets)) return null;
  var gameKey = resolveSstatsGameKeyForMatch(match, config, lookupMemo);
  if (!gameKey) return null;
  var bundle = fetchSstatsGameBundle(gameKey, config, detailMemo, glickoMemo, oddsMemo, profitsMemo);
  var detail = bundle && bundle.detail ? bundle.detail : null;
  var glicko = bundle && bundle.glicko ? bundle.glicko : null;
  var oddsPayload = bundle && bundle.odds ? bundle.odds : null;
  var profitsPayload = bundle && bundle.profits ? bundle.profits : null;
  var detailData = unwrapSstatsData(detail) || {};
  var game = detailData.game || detailData.match || detailData.fixture || null;
  if (!game) return null;
  var stats = detailData.statistics || {};
  var lineups = detailData.lineups || {};
  var lineupPlayers = Array.isArray(detailData.lineupPlayers) ? detailData.lineupPlayers : [];
  var glickoObj = unwrapSstatsData(glicko);
  if (glickoObj && glickoObj.glicko) glickoObj = glickoObj.glicko;
  var homeTeamId = getFirstExisting(game, ['homeTeam.id', 'home.id']);
  var awayTeamId = getFirstExisting(game, ['awayTeam.id', 'away.id']);
  var homeStart = countSstatsStartingPlayers(lineupPlayers, homeTeamId);
  var awayStart = countSstatsStartingPlayers(lineupPlayers, awayTeamId);
  var profitSignals = parseSstatsProfits(profitsPayload);
  var oddsMarkets = parseSstatsOddsRowsToMarkets(extractSstatsOddsRows(oddsPayload, detail), match);
  return {
    gameKey: game.flashId || game.id || gameKey,
    homeXg: getLineValue(glickoObj && glickoObj.homeXg, stats.expectedGoalsHome, stats.calculatedXgHome, stats.xgHome),
    awayXg: getLineValue(glickoObj && glickoObj.awayXg, stats.expectedGoalsAway, stats.calculatedXgAway, stats.xgAway),
    homeWinProbability: normalizePercentMaybe(glickoObj && glickoObj.homeWinProbability),
    awayWinProbability: normalizePercentMaybe(glickoObj && glickoObj.awayWinProbability),
    homeFormation: lineups.homeFormation || '',
    awayFormation: lineups.awayFormation || '',
    homeStarting: homeStart,
    awayStarting: awayStart,
    homePossession: normalizePercentMaybe(stats.ballPossessionHome),
    awayPossession: normalizePercentMaybe(stats.ballPossessionAway),
    homeShots: toNumber(stats.totalShotsHome),
    awayShots: toNumber(stats.totalShotsAway),
    profits: profitSignals,
    oddsMarkets: oddsMarkets
  };
}


/* ======================= DEEP SOCCER CONTEXT ======================= */
function fetchApiFootball(path, params, config, label, cacheSeconds) {
  if (!config.apiFootballKey) return null;
  var url = 'https://' + config.apiFootballHost + path;
  if (params) url += '?' + buildQuery(params);
  return fetchJson(url, { headers: { 'x-apisports-key': config.apiFootballKey } }, label, { config: config, sportKey: 'soccer', cost: 1 }, cacheSeconds || 0);
}

function parseSoccerGoalsForContext(fixture) {
  var goals = fixture && fixture.goals ? fixture.goals : {};
  return {
    home: toNumber(goals.home) || 0,
    away: toNumber(goals.away) || 0
  };
}


function buildSoccerTeamForm(teamId, config, memo) {
  if (!teamId || !config.enableDeepSoccerContext) return null;
  if (memo && memo[teamId] !== undefined) return memo[teamId];

  var data = fetchApiFootball('/fixtures', { team: teamId, last: config.deepLastMatches, timezone: config.apiFootballTimezone }, config, 'API-Football team last ' + teamId, config.deepContextCacheSeconds);
  var rows = data && data.response ? data.response : [];
  if (!rows.length) {
    if (memo) memo[teamId] = null;
    return null;
  }

  rows.sort(function (a, b) {
    return new Date(b && b.fixture && b.fixture.date || 0).getTime() - new Date(a && a.fixture && a.fixture.date || 0).getTime();
  });

  var points = 0;
  var gf = 0;
  var ga = 0;
  var over25 = 0;
  var btts = 0;
  var scored = 0;
  var conceded = 0;
  var restDiffs = [];
  var lastMatchDate = null;

  rows.forEach(function (row, idx) {
    var goals = parseSoccerGoalsForContext(row);
    var homeId = row && row.teams && row.teams.home && row.teams.home.id;
    var awayId = row && row.teams && row.teams.away && row.teams.away.id;
    var isHome = String(homeId) === String(teamId);
    var teamGoals = isHome ? goals.home : goals.away;
    var oppGoals = isHome ? goals.away : goals.home;
    gf += teamGoals;
    ga += oppGoals;
    if (teamGoals > oppGoals) points += 3;
    else if (teamGoals === oppGoals) points += 1;
    if (teamGoals > 0) scored += 1;
    if (oppGoals > 0) conceded += 1;
    if ((goals.home + goals.away) > 2.5) over25 += 1;
    if (goals.home > 0 && goals.away > 0) btts += 1;

    var matchDate = row && row.fixture && row.fixture.date ? new Date(row.fixture.date) : null;
    if (idx === 0 && matchDate && !isNaN(matchDate.getTime())) lastMatchDate = matchDate;
    if (idx > 0 && matchDate && rows[idx - 1] && rows[idx - 1].fixture && rows[idx - 1].fixture.date) {
      var prevDate = new Date(rows[idx - 1].fixture.date);
      if (!isNaN(prevDate.getTime()) && !isNaN(matchDate.getTime())) {
        restDiffs.push(Math.abs(prevDate.getTime() - matchDate.getTime()) / (24 * 60 * 60 * 1000));
      }
    }
  });

  var now = new Date();
  var daysSinceLastMatch = lastMatchDate && !isNaN(lastMatchDate.getTime()) ? (now.getTime() - lastMatchDate.getTime()) / (24 * 60 * 60 * 1000) : null;
  var avgRestDays = restDiffs.length ? avg(restDiffs) : daysSinceLastMatch;

  var model = {
    sampleSize: rows.length,
    pointsPerGame: points / rows.length,
    goalsFor: gf / rows.length,
    goalsAgainst: ga / rows.length,
    scoredRate: scored / rows.length,
    concededRate: conceded / rows.length,
    over25Rate: over25 / rows.length,
    bttsRate: btts / rows.length,
    daysSinceLastMatch: daysSinceLastMatch,
    avgRestDays: avgRestDays,
    congestionIndex: avgRestDays != null ? clamp((5 - avgRestDays) / 3, 0, 1.4) : 0
  };
  if (memo) memo[teamId] = model;
  return model;
}

function buildSoccerHeadToHead(homeId, awayId, config, memo) {
  if (!homeId || !awayId || !config.enableDeepSoccerContext) return null;
  var key = sortPair(String(homeId), String(awayId));
  if (memo && memo[key] !== undefined) return memo[key];

  var data = fetchApiFootball('/fixtures/headtohead', { h2h: homeId + '-' + awayId }, config, 'API-Football h2h ' + homeId + '-' + awayId, config.deepContextCacheSeconds);
  var rows = data && data.response ? data.response.slice(0, config.deepH2hMatches || 4) : [];
  if (!rows.length) {
    if (memo) memo[key] = null;
    return null;
  }

  var homeWins = 0;
  var draws = 0;
  var awayWins = 0;
  var totals = [];
  rows.forEach(function (row) {
    var goals = parseSoccerGoalsForContext(row);
    var hId = row && row.teams && row.teams.home && row.teams.home.id;
    var aId = row && row.teams && row.teams.away && row.teams.away.id;
    var homeGoalsFromPerspective = String(hId) === String(homeId) ? goals.home : goals.away;
    var awayGoalsFromPerspective = String(aId) === String(awayId) ? goals.away : goals.home;
    if (homeGoalsFromPerspective > awayGoalsFromPerspective) homeWins += 1;
    else if (homeGoalsFromPerspective < awayGoalsFromPerspective) awayWins += 1;
    else draws += 1;
    totals.push(homeGoalsFromPerspective + awayGoalsFromPerspective);
  });

  var model = {
    sampleSize: rows.length,
    homeWinRate: homeWins / rows.length,
    drawRate: draws / rows.length,
    awayWinRate: awayWins / rows.length,
    avgTotalGoals: avg(totals)
  };
  if (memo) memo[key] = model;
  return model;
}


function getHoursUntilDate(isoDate) {
  if (!isoDate) return null;
  var dt = new Date(isoDate);
  if (isNaN(dt.getTime())) return null;
  return (dt.getTime() - Date.now()) / (60 * 60 * 1000);
}

function safeNestedGet(obj, path, fallback) {
  var parts = String(path || '').split('.');
  var cur = obj;
  for (var i = 0; i < parts.length; i++) {
    if (!cur || typeof cur !== 'object' || !(parts[i] in cur)) return fallback;
    cur = cur[parts[i]];
  }
  return cur == null ? fallback : cur;
}

function normalizeFormStringPoints(form) {
  var score = 0;
  var count = 0;
  String(form || '').split('').forEach(function (ch) {
    if (ch === 'W') { score += 3; count += 1; }
    else if (ch === 'D') { score += 1; count += 1; }
    else if (ch === 'L') { count += 1; }
  });
  return count ? score / (count * 3) : null;
}

function parseApiFootballTeamStats(data) {
  var r = data && data.response ? data.response : null;
  if (!r) return null;
  return {
    playedHome: toNumber(safeNestedGet(r, 'fixtures.played.home', null)),
    playedAway: toNumber(safeNestedGet(r, 'fixtures.played.away', null)),
    goalsForHome: toNumber(safeNestedGet(r, 'goals.for.average.home', null)),
    goalsForAway: toNumber(safeNestedGet(r, 'goals.for.average.away', null)),
    goalsAgainstHome: toNumber(safeNestedGet(r, 'goals.against.average.home', null)),
    goalsAgainstAway: toNumber(safeNestedGet(r, 'goals.against.average.away', null)),
    cleanSheetsHome: toNumber(safeNestedGet(r, 'clean_sheet.home', null)),
    cleanSheetsAway: toNumber(safeNestedGet(r, 'clean_sheet.away', null)),
    failedToScoreHome: toNumber(safeNestedGet(r, 'failed_to_score.home', null)),
    failedToScoreAway: toNumber(safeNestedGet(r, 'failed_to_score.away', null)),
    formIndex: normalizeFormStringPoints(safeNestedGet(r, 'form', ''))
  };
}

function buildSoccerTeamSeasonStats(teamId, leagueId, season, config, memo) {
  if (!teamId || !leagueId || !season || !config.enableAdvancedSoccerContext) return null;
  var key = [teamId, leagueId, season].join('|');
  if (memo && memo[key] !== undefined) return memo[key];
  var data = fetchApiFootball('/teams/statistics', { team: teamId, league: leagueId, season: season }, config, 'API-Football team stats ' + key, config.teamStatsCacheSeconds || config.deepContextCacheSeconds);
  var model = parseApiFootballTeamStats(data);
  if (memo) memo[key] = model;
  return model;
}

function buildSoccerStandingsInfo(teamId, leagueId, season, config, memo) {
  if (!teamId || !leagueId || !season || !config.enableAdvancedSoccerContext) return null;
  var key = [leagueId, season].join('|');
  if (memo && memo[key] && memo[key][teamId] !== undefined) return memo[key][teamId];
  var data = fetchApiFootball('/standings', { league: leagueId, season: season }, config, 'API-Football standings ' + key, config.standingsCacheSeconds || config.deepContextCacheSeconds);
  var response = data && data.response ? data.response : [];
  var extracted = {};
  response.forEach(function (entry) {
    var league = entry && entry.league;
    var groups = league && league.standings ? league.standings : [];
    groups.forEach(function (group) {
      (group || []).forEach(function (row) {
        var tId = row && row.team && row.team.id;
        if (!tId) return;
        extracted[tId] = {
          rank: toNumber(row.rank),
          points: toNumber(row.points),
          goalsDiff: toNumber(row.goalsDiff),
          formIndex: normalizeFormStringPoints(row.form || '')
        };
      });
    });
  });
  if (memo) memo[key] = extracted;
  return extracted[teamId] || null;
}

function makeEmptyInjuryBucket() {
  return { count: 0, attack: 0, midfield: 0, defense: 0, goalkeeper: 0, doubtful: 0 };
}

function classifyPlayerRole(text) {
  var raw = String(text || '').toLowerCase();
  if (raw.indexOf('goalkeeper') !== -1 || raw.indexOf('keeper') !== -1 || raw === 'g') return 'goalkeeper';
  if (raw.indexOf('def') !== -1 || raw.indexOf('back') !== -1) return 'defense';
  if (raw.indexOf('mid') !== -1) return 'midfield';
  if (raw.indexOf('for') !== -1 || raw.indexOf('att') !== -1 || raw.indexOf('striker') !== -1 || raw.indexOf('wing') !== -1) return 'attack';
  return '';
}

function buildSoccerFixtureInjuries(fixtureId, homeTeamId, awayTeamId, config, memo) {
  if (!fixtureId || !config.enableAdvancedSoccerContext) return null;
  if (memo && memo[fixtureId] !== undefined) return memo[fixtureId];
  var data = fetchApiFootball('/injuries', { fixture: fixtureId }, config, 'API-Football injuries ' + fixtureId, config.injuriesCacheSeconds || config.deepContextCacheSeconds);
  var rows = data && data.response ? data.response : [];
  var model = { home: makeEmptyInjuryBucket(), away: makeEmptyInjuryBucket() };
  rows.forEach(function (row) {
    var tId = row && row.team && row.team.id;
    var bucket = String(tId) === String(homeTeamId) ? model.home : (String(tId) === String(awayTeamId) ? model.away : null);
    if (!bucket) return;
    bucket.count += 1;
    var role = classifyPlayerRole(
      safeNestedGet(row, 'player.type', '') ||
      safeNestedGet(row, 'player.position', '') ||
      safeNestedGet(row, 'player.reason', '') ||
      safeNestedGet(row, 'player.name', '')
    );
    if (role && bucket[role] != null) bucket[role] += 1;
    var reason = String(safeNestedGet(row, 'player.reason', '') || safeNestedGet(row, 'player.type', '')).toLowerCase();
    if (reason.indexOf('doubt') !== -1 || reason.indexOf('questionable') !== -1) bucket.doubtful += 1;
  });
  if (memo) memo[fixtureId] = model;
  return model;
}

function summarizeLineupBlock(lineup) {
  return {
    formation: lineup && lineup.formation ? String(lineup.formation) : '',
    starting: Array.isArray(lineup && lineup.startXI) ? lineup.startXI.length : 0,
    substitutes: Array.isArray(lineup && lineup.substitutes) ? lineup.substitutes.length : 0,
    confirmed: Array.isArray(lineup && lineup.startXI) && lineup.startXI.length >= 10
  };
}

function buildSoccerFixtureLineups(fixtureId, homeTeamId, awayTeamId, config, memo) {
  if (!fixtureId || !config.enableAdvancedSoccerContext) return null;
  if (memo && memo[fixtureId] !== undefined) return memo[fixtureId];
  var data = fetchApiFootball('/fixtures/lineups', { fixture: fixtureId }, config, 'API-Football lineups ' + fixtureId, config.lineupsCacheSeconds || config.deepContextCacheSeconds);
  var rows = data && data.response ? data.response : [];
  var model = { home: null, away: null };
  rows.forEach(function (row) {
    var tId = row && row.team && row.team.id;
    if (String(tId) === String(homeTeamId)) model.home = summarizeLineupBlock(row);
    if (String(tId) === String(awayTeamId)) model.away = summarizeLineupBlock(row);
  });
  if (memo) memo[fixtureId] = model;
  return model;
}

function getTopMatchesForDeepContext(matches, tips, oddsSources, config) {
  var scored = matches.map(function (m) {
    var score = 0;
    var indexedOdds = oddsSources && oddsSources.oddsIndexByExact ? oddsSources.oddsIndexByExact[m.matchKey] : null;
    var hasOffers = indexedOdds && hasAnyOffers(indexedOdds);
    var offerWeight = 0;
    if (indexedOdds) {
      offerWeight += Math.min(18, (indexedOdds.h2h || []).length * 0.25);
      offerWeight += Math.min(14, (indexedOdds.totals || []).length * 0.35);
      offerWeight += Math.min(14, (indexedOdds.spreads || []).length * 0.35);
      offerWeight += Math.min(10, (indexedOdds.btts || []).length * 0.4);
      offerWeight += Math.min(10, (indexedOdds.teamTotals || []).length * 0.4);
    }
    if (m.sport === 'soccer') score += 50;
    if (tips && tips[m.matchKey]) score += 100;
    if (m.apiFootballFixtureId) score += 20;
    if (config && config.sstatsGameKeyMap && (config.sstatsGameKeyMap[m.matchKey] || config.sstatsGameKeyMap[m.looseKey])) score += 18;
    if (hasOffers) score += 15 + offerWeight;
    if (m.leaguePriority) score += m.leaguePriority;
    return { match: m, score: score };
  }).filter(function (x) { return x.match.sport === 'soccer'; });

  scored.sort(function (a, b) { return b.score - a.score; });
  var top = scored.slice(0, config.deepContextTopMatches || MAX_TOP_CONTEXT_MATCHES).map(function (x) { return x.match; });
  Logger.log('Top matches for deep context: ' + top.length);
  return top;
}


function fetchApiFootballFixturesForDate(dateKey, config, memo) {
  memo = memo || {};
  if (!dateKey) return [];
  if (memo[dateKey]) return memo[dateKey];
  var data = fetchApiFootball('/fixtures', { date: dateKey, timezone: config.apiFootballTimezone }, config, 'API-Football fixtures day ' + dateKey, config.fixturesCacheSeconds || config.cacheSeconds || 300);
  memo[dateKey] = data && data.response ? data.response : [];
  return memo[dateKey];
}

function enrichTopSoccerMatchesWithApiFootballContext(matches, config) {
  if (!matches || !matches.length || !config.apiFootballKey) return 0;
  var dayMemo = {};
  var enriched = 0;
  matches.forEach(function (match) {
    if (!match || match.sport !== 'soccer') return;
    if (match.apiFootballFixtureId && match.apiFootballHomeTeamId && match.apiFootballAwayTeamId) return;
    var dateKey = getDateKey(match.isoDate);
    var rows = fetchApiFootballFixturesForDate(dateKey, config, dayMemo);
    var best = null;
    var bestScore = -1;
    rows.forEach(function (row) {
      var fixture = normalizeApiFootballFixture(row);
      if (!fixture || fixture.sport !== 'soccer') return;
      if (!fuzzyTeamsEquivalent(fixture.home, fixture.away, match.home, match.away)) return;
      var diff = dateDiffHours(fixture.isoDate, match.isoDate);
      if (diff != null && diff > Math.min(config.matchStartToleranceHours || 30, 12)) return;
      var score = 0;
      if (matchTeamsEquivalent(fixture.home, fixture.away, match.home, match.away)) score += 10;
      if (canonicalizeLeagueName(fixture.league) === canonicalizeLeagueName(match.league)) score += 6;
      if (diff != null) score += Math.max(0, 6 - diff);
      if (score > bestScore) {
        best = fixture;
        bestScore = score;
      }
    });
    if (best) {
      mergeMatchFields(match, best);
      if (best.apiFootballFixtureId) match.apiFootballFixtureId = best.apiFootballFixtureId;
      if (best.apiFootballLeagueId) match.apiFootballLeagueId = best.apiFootballLeagueId;
      if (best.apiFootballSeason) match.apiFootballSeason = best.apiFootballSeason;
      if (best.apiFootballHomeTeamId) match.apiFootballHomeTeamId = best.apiFootballHomeTeamId;
      if (best.apiFootballAwayTeamId) match.apiFootballAwayTeamId = best.apiFootballAwayTeamId;
      enriched += 1;
    }
  });
  Logger.log('API-Football context enrichment for top soccer matches: ' + enriched + ' / ' + matches.length);
  return enriched;
}

function buildDeepSoccerContext(matches, tips, oddsSources, config) {
  var index = {};
  if (!config.enableDeepSoccerContext && !config.enableSstatsContext && !config.enableSstatsMarkets) return index;
  var topMatches = getTopMatchesForDeepContext(matches, tips, oddsSources, config);
  if (config.apiFootballKey) enrichTopSoccerMatchesWithApiFootballContext(topMatches, config);
  var teamFormMemo = {};
  var h2hMemo = {};
  var teamStatsMemo = {};
  var standingsMemo = {};
  var injuriesMemo = {};
  var lineupsMemo = {};
  var sstatsDetailMemo = {};
  var sstatsGlickoMemo = {};
  var sstatsOddsMemo = {};
  var sstatsProfitsMemo = {};
  var sstatsLookupMemo = {};
  var sstatsListMemo = {};
  var built = 0;
  var sstatsBuilt = 0;

  enrichMatchesWithSstatsGameKeys(topMatches, config, sstatsListMemo, sstatsLookupMemo);

  topMatches.forEach(function (match) {
    var homeForm = null;
    var awayForm = null;
    var h2h = null;
    var homeStats = null;
    var awayStats = null;
    var homeStanding = null;
    var awayStanding = null;
    var injuries = null;
    var lineups = null;
    var hoursUntil = getHoursUntilDate(match.isoDate);

    if (match.apiFootballHomeTeamId && match.apiFootballAwayTeamId && config.apiFootballKey) {
      homeForm = buildSoccerTeamForm(match.apiFootballHomeTeamId, config, teamFormMemo);
      awayForm = buildSoccerTeamForm(match.apiFootballAwayTeamId, config, teamFormMemo);
      h2h = buildSoccerHeadToHead(match.apiFootballHomeTeamId, match.apiFootballAwayTeamId, config, h2hMemo);
      if (config.enableAdvancedSoccerContext && match.apiFootballLeagueId && match.apiFootballSeason) {
        homeStats = buildSoccerTeamSeasonStats(match.apiFootballHomeTeamId, match.apiFootballLeagueId, match.apiFootballSeason, config, teamStatsMemo);
        awayStats = buildSoccerTeamSeasonStats(match.apiFootballAwayTeamId, match.apiFootballLeagueId, match.apiFootballSeason, config, teamStatsMemo);
        homeStanding = buildSoccerStandingsInfo(match.apiFootballHomeTeamId, match.apiFootballLeagueId, match.apiFootballSeason, config, standingsMemo);
        awayStanding = buildSoccerStandingsInfo(match.apiFootballAwayTeamId, match.apiFootballLeagueId, match.apiFootballSeason, config, standingsMemo);
      }
      if (config.enableAdvancedSoccerContext && match.apiFootballFixtureId && hoursUntil != null && hoursUntil <= (config.injuryFetchLookaheadHours || 72)) {
        injuries = buildSoccerFixtureInjuries(match.apiFootballFixtureId, match.apiFootballHomeTeamId, match.apiFootballAwayTeamId, config, injuriesMemo);
      }
      if (config.enableAdvancedSoccerContext && match.apiFootballFixtureId && hoursUntil != null && hoursUntil <= (config.lineupFetchLookaheadHours || 18)) {
        lineups = buildSoccerFixtureLineups(match.apiFootballFixtureId, match.apiFootballHomeTeamId, match.apiFootballAwayTeamId, config, lineupsMemo);
      }
    }

    var sstats = buildSstatsContextForMatch(match, config, sstatsDetailMemo, sstatsGlickoMemo, sstatsOddsMemo, sstatsProfitsMemo, sstatsLookupMemo);
    if (sstats) sstatsBuilt += 1;

    if (homeForm || awayForm || h2h || homeStats || awayStats || homeStanding || awayStanding || injuries || lineups || sstats) {
      index[match.matchKey] = {
        homeForm: homeForm,
        awayForm: awayForm,
        h2h: h2h,
        homeStats: homeStats,
        awayStats: awayStats,
        homeStanding: homeStanding,
        awayStanding: awayStanding,
        injuries: injuries,
        lineups: lineups,
        sstats: sstats,
        sstatsWeight: config.sstatsContextWeight || 0.28,
        hoursUntil: hoursUntil
      };
      built += 1;
    }
  });

  Logger.log('Deep soccer contexts built: ' + built + ' / ' + topMatches.length);
  Logger.log('SStats contexts built: ' + sstatsBuilt + ' / ' + topMatches.length);
  return index;
}


/* ======================= ODDS PARSERS ======================= */
/* ======================= ODDS PARSERS ======================= */
function isAsianLine(point) {
  var p = Math.abs(Number(point));
  if (!isFinite(p)) return false;
  var frac = round2(p - Math.floor(p));
  return frac === 0.25 || frac === 0.75;
}


function detectMarketSubtypeFromText(marketKey, marketName, sportKey) {
  var key = String(marketKey || '').toLowerCase();
  var name = String(marketName || '').toLowerCase();

  var isTeamTotals = key.indexOf('team_total') !== -1 || key.indexOf('teamtotals') !== -1 ||
    key.indexOf('home_total') !== -1 || key.indexOf('away_total') !== -1 ||
    name.indexOf('team total') !== -1 || name.indexOf('home total') !== -1 || name.indexOf('away total') !== -1 ||
    name.indexOf('individual total') !== -1 || name.indexOf('total goals home') !== -1 || name.indexOf('total goals away') !== -1;

  var isDoubleChance = key.indexOf('double_chance') !== -1 || key.indexOf('doublechance') !== -1 ||
    name.indexOf('double chance') !== -1 || name === '1x' || name === 'x2' || name === '12';

  var isDnb = key.indexOf('draw_no_bet') !== -1 || key.indexOf('drawnobet') !== -1 || key.indexOf('dnb') !== -1 ||
    name.indexOf('draw no bet') !== -1 || name.indexOf('dnb') !== -1;

  var isBtts = key.indexOf('both_teams_to_score') !== -1 || key.indexOf('btts') !== -1 ||
    name.indexOf('both teams to score') !== -1 || name.indexOf('btts') !== -1;

  var isRegulation = key.indexOf('regulation') !== -1 || key.indexOf('regular') !== -1 || key.indexOf('60') !== -1 ||
    name.indexOf('regulation') !== -1 || name.indexOf('regular time') !== -1 || name.indexOf('60 min') !== -1 ||
    name.indexOf('3-way') !== -1 || name.indexOf('three way') !== -1 || name.indexOf('full time result') !== -1;

  var isMoneyline = key === 'h2h' || key.indexOf('moneyline') !== -1 || key.indexOf('money line') !== -1 ||
    name === 'ml' || name === 'moneyline' || name === 'money line' || name === 'match winner' ||
    name === 'winner' || name === 'match result' || name.indexOf('to win') !== -1 ||
    name === 'match winner incl. overtime' || name === '1x2' || name.indexOf('full time result') !== -1;

  var isTotals = key === 'totals' || key.indexOf('total') !== -1 || name.indexOf('total') !== -1 ||
    name.indexOf('over/under') !== -1 || name.indexOf('over under') !== -1 || name === 'ou' ||
    name.indexOf('goals over/under') !== -1 || name.indexOf('game total') !== -1 ||
    name.indexOf('total points') !== -1 || name.indexOf('total runs') !== -1 || name.indexOf('match total') !== -1 ||
    name.indexOf('goals o/u') !== -1;

  var isSpreads = key === 'spreads' || key.indexOf('spread') !== -1 || key.indexOf('handicap') !== -1 ||
    name.indexOf('spread') !== -1 || name.indexOf('handicap') !== -1 || name.indexOf('puck line') !== -1 ||
    name.indexOf('run line') !== -1 || name.indexOf('point spread') !== -1 || name.indexOf('alt spread') !== -1 ||
    name.indexOf('alternative handicap') !== -1;

  if (isTeamTotals) return { family: 'teamTotals', subType: 'team_totals' };
  if (isDoubleChance) return { family: 'doubleChance', subType: 'double_chance' };
  if (isDnb) return { family: 'dnb', subType: 'dnb' };
  if (isBtts) return { family: 'btts', subType: 'btts' };

  if (isMoneyline) {
    if (isRegulation) {
      return { family: 'h2h', subType: sportKey === 'soccer' ? 'regular_time_3way' : 'regular_time' };
    }
    if (sportKey === 'icehockey') return { family: 'h2h', subType: 'moneyline_ot' };
    return { family: 'h2h', subType: 'moneyline' };
  }
  if (isTotals) {
    return { family: 'totals', subType: key.indexOf('asian') !== -1 || name.indexOf('asian') !== -1 ? 'asian_totals' : 'totals' };
  }
  if (isSpreads) {
    return { family: 'spreads', subType: key.indexOf('asian') !== -1 || name.indexOf('asian') !== -1 ? 'asian_spreads' : 'spreads' };
  }
  return null;
}

function inferTeamTotalSide(marketName, marketKey, outcomeName, match) {
  var raw = (String(marketName || '') + ' ' + String(marketKey || '') + ' ' + String(outcomeName || '')).toLowerCase();
  if (raw.indexOf('home') !== -1 || raw.indexOf('team 1') !== -1 || raw.indexOf('1st team') !== -1 || raw.indexOf(normalizeTeamName(match.home)) !== -1) return 'home';
  if (raw.indexOf('away') !== -1 || raw.indexOf('team 2') !== -1 || raw.indexOf('2nd team') !== -1 || raw.indexOf(normalizeTeamName(match.away)) !== -1) return 'away';
  return null;
}


function flattenMarkets(bookmakers, sportKey) {
  var markets = createEmptyMarkets();
  (bookmakers || []).forEach(function (b) {
    var bookmakerName = b.title || b.name || b.key || 'Unknown';
    (b.markets || []).forEach(function (m) {
      var info = detectMarketSubtypeFromText(m.key, m.name, sportKey);
      if (!info) return;
      (m.outcomes || []).forEach(function (o) {
        var price = Number(o.price);
        if (!price || isNaN(price) || price <= 1) return;
        var point = toNumber(o.point);
        var subType = info.subType;
        if (info.family === 'totals' && point != null && isAsianLine(point) && subType === 'totals') subType = 'asian_totals';
        if (info.family === 'spreads' && point != null && isAsianLine(point) && subType === 'spreads') subType = 'asian_spreads';

        if (info.family === 'teamTotals') {
          var teamSide = inferTeamTotalSide(m.name, m.key, o.name, { home: 'home', away: 'away' });
          var sel = getTotalSelectionKey(o.name);
          if (teamSide && sel && point != null) {
            markets.teamTotals.push({
              name: sel === 'over' ? 'Over' : 'Under',
              price: price,
              point: point,
              bookmaker: bookmakerName,
              marketKey: m.key || '',
              marketName: m.name || '',
              marketSubType: subType,
              teamSide: teamSide
            });
          }
          return;
        }

        markets[info.family].push({
          name: o.name,
          price: price,
          point: point,
          bookmaker: bookmakerName,
          marketKey: m.key || '',
          marketName: m.name || '',
          marketSubType: subType
        });
      });
    });
  });
  return markets;
}

function pushOffer(markets, family, offer) {
  if (!markets[family]) markets[family] = [];
  if (!offer || !offer.price || offer.price <= 1) return;
  markets[family].push(offer);
}


function parseOddsApiIoEventToMarkets(eventOdds, match) {
  var markets = createEmptyMarkets();
  var bookmakers = eventOdds && eventOdds.bookmakers ? eventOdds.bookmakers : {};
  Object.keys(bookmakers).forEach(function (bookmakerName) {
    var bookmakerMarkets = bookmakers[bookmakerName];
    if (!Array.isArray(bookmakerMarkets)) return;
    bookmakerMarkets.forEach(function (m) {
      var info = detectMarketSubtypeFromText(m.key, m.name, match.sport);
      if (!info) return;
      var odds = m.odds && m.odds[0] ? m.odds[0] : null;
      if (!odds) return;

      if (info.family === 'h2h') {
        if (odds.home) pushOffer(markets, 'h2h', { name: match.home, price: Number(odds.home), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
        if (odds.draw) pushOffer(markets, 'h2h', { name: 'Draw', price: Number(odds.draw), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
        if (odds.away) pushOffer(markets, 'h2h', { name: match.away, price: Number(odds.away), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
      }

      if (info.family === 'doubleChance') {
        if (odds['1x']) pushOffer(markets, 'doubleChance', { name: '1X', price: Number(odds['1x']), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
        if (odds['x2']) pushOffer(markets, 'doubleChance', { name: 'X2', price: Number(odds['x2']), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
        if (odds['12']) pushOffer(markets, 'doubleChance', { name: '12', price: Number(odds['12']), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
      }

      if (info.family === 'dnb') {
        if (odds.home) pushOffer(markets, 'dnb', { name: match.home, price: Number(odds.home), bookmaker: bookmakerName, point: 0, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
        if (odds.away) pushOffer(markets, 'dnb', { name: match.away, price: Number(odds.away), bookmaker: bookmakerName, point: 0, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
      }

      if (info.family === 'btts') {
        if (odds.yes) pushOffer(markets, 'btts', { name: 'Yes', price: Number(odds.yes), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
        if (odds.no) pushOffer(markets, 'btts', { name: 'No', price: Number(odds.no), bookmaker: bookmakerName, point: null, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType });
      }

      if (info.family === 'teamTotals') {
        var teamTotalPoint = getLineValue(odds.point, odds.total, odds.line, m.point, m.total, m.line);
        var teamSide = inferTeamTotalSide(m.name, m.key, '', match);
        if (!teamSide) return;
        if (odds.over) pushOffer(markets, 'teamTotals', { name: 'Over', price: Number(odds.over), bookmaker: bookmakerName, point: teamTotalPoint, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType, teamSide: teamSide });
        if (odds.under) pushOffer(markets, 'teamTotals', { name: 'Under', price: Number(odds.under), bookmaker: bookmakerName, point: teamTotalPoint, marketKey: m.key || '', marketName: m.name || '', marketSubType: info.subType, teamSide: teamSide });
      }

      if (info.family === 'totals') {
        var totalPoint = getLineValue(odds.point, odds.total, odds.line, m.point, m.total, m.line);
        var totalSubType = info.subType;
        if (totalPoint != null && isAsianLine(totalPoint) && totalSubType === 'totals') totalSubType = 'asian_totals';
        if (odds.over) pushOffer(markets, 'totals', { name: 'Over', price: Number(odds.over), bookmaker: bookmakerName, point: totalPoint, marketKey: m.key || '', marketName: m.name || '', marketSubType: totalSubType });
        if (odds.under) pushOffer(markets, 'totals', { name: 'Under', price: Number(odds.under), bookmaker: bookmakerName, point: totalPoint, marketKey: m.key || '', marketName: m.name || '', marketSubType: totalSubType });
      }

      if (info.family === 'spreads') {
        var rawLine = getLineValue(m.point, m.line, m.handicap, odds.point, odds.line, odds.handicap);
        var homeLine = getLineValue(odds.home_line, odds.homeHandicap, odds.home_spread, odds.homeSpread);
        var awayLine = getLineValue(odds.away_line, odds.awayHandicap, odds.away_spread, odds.awaySpread);
        if (homeLine == null && awayLine == null && rawLine != null) {
          homeLine = -Math.abs(rawLine);
          awayLine = Math.abs(rawLine);
        } else if (homeLine == null && awayLine != null) {
          homeLine = -awayLine;
        } else if (awayLine == null && homeLine != null) {
          awayLine = -homeLine;
        }
        var spreadSubType = info.subType;
        if (homeLine != null && isAsianLine(homeLine) && spreadSubType === 'spreads') spreadSubType = 'asian_spreads';
        if (odds.home && homeLine != null) pushOffer(markets, 'spreads', { name: match.home, price: Number(odds.home), bookmaker: bookmakerName, point: homeLine, marketKey: m.key || '', marketName: m.name || '', marketSubType: spreadSubType });
        if (odds.away && awayLine != null) pushOffer(markets, 'spreads', { name: match.away, price: Number(odds.away), bookmaker: bookmakerName, point: awayLine, marketKey: m.key || '', marketName: m.name || '', marketSubType: spreadSubType });
      }
    });
  });
  return markets;
}

function parseApiFootballOddsRows(rows) {
  var out = {};
  (rows || []).forEach(function (entry) {
    var fixtureId = entry && entry.fixture && entry.fixture.id;
    if (!fixtureId) return;
    var match = makeNormalizedMatch(
      'apiFootball',
      'soccer',
      fixtureId,
      fixtureId,
      entry && entry.teams && entry.teams.home && entry.teams.home.name,
      entry && entry.teams && entry.teams.away && entry.teams.away.name,
      entry && entry.fixture && entry.fixture.date,
      entry && entry.league && entry.league.name,
      'upcoming',
      { apiFootballFixtureId: fixtureId }
    );
    var key = match.matchKey;
    if (!out[key]) out[key] = createEmptyMarkets();
    var markets = out[key];

    (entry.bookmakers || []).forEach(function (bookmaker) {
      var bookmakerName = bookmaker.name || ('Bookmaker #' + bookmaker.id);
      (bookmaker.bets || []).forEach(function (bet) {
        var betName = String(bet.name || '').toLowerCase();
        var values = bet.values || [];

        if (betName.indexOf('double chance') !== -1) {
          values.forEach(function (v) {
            var val = String(v.value || '').toUpperCase().replace(/\s+/g, '');
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            if (val === '1X' || val === 'X2' || val === '12') {
              pushOffer(markets, 'doubleChance', { name: val, price: price, bookmaker: bookmakerName, point: null, marketKey: 'double_chance', marketName: bet.name || '', marketSubType: 'double_chance' });
            }
          });
          return;
        }

        if (betName.indexOf('draw no bet') !== -1 || betName === 'dnb') {
          values.forEach(function (v) {
            var val = String(v.value || '').toLowerCase();
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            var outcomeName = null;
            if (val === 'home' || val === '1') outcomeName = match.home;
            else if (val === 'away' || val === '2') outcomeName = match.away;
            if (!outcomeName) return;
            pushOffer(markets, 'dnb', { name: outcomeName, price: price, bookmaker: bookmakerName, point: 0, marketKey: 'draw_no_bet', marketName: bet.name || '', marketSubType: 'dnb' });
          });
          return;
        }

        if (betName.indexOf('team total') !== -1 || betName.indexOf('home total') !== -1 || betName.indexOf('away total') !== -1 || betName.indexOf('individual total') !== -1) {
          values.forEach(function (v) {
            var raw = String(v.value || '');
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            var side = inferTeamTotalSide(bet.name || '', betName, raw, match);
            var sel = /^over/i.test(raw) ? 'Over' : (/^under/i.test(raw) ? 'Under' : null);
            if (!side || !sel) return;
            var pointRaw = raw.replace(/^over\s*/i, '').replace(/^under\s*/i, '');
            var point = toNumber(pointRaw.replace(',', '.'));
            if (point == null) return;
            pushOffer(markets, 'teamTotals', { name: sel, price: price, bookmaker: bookmakerName, point: point, marketKey: 'team_totals', marketName: bet.name || '', marketSubType: 'team_totals', teamSide: side });
          });
          return;
        }

        if (betName.indexOf('both teams score') !== -1 || betName.indexOf('both teams to score') !== -1 || betName.indexOf('btts') !== -1) {
          values.forEach(function (v) {
            var val = String(v.value || '').toLowerCase();
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            if (val === 'yes' || val === 'no') {
              pushOffer(markets, 'btts', { name: val === 'yes' ? 'Yes' : 'No', price: price, bookmaker: bookmakerName, point: null, marketKey: 'btts', marketName: bet.name || '', marketSubType: 'btts' });
            }
          });
          return;
        }

        if (betName.indexOf('match winner') !== -1 || betName === 'winner' || betName.indexOf('1x2') !== -1 || betName.indexOf('full time result') !== -1) {
          values.forEach(function (v) {
            var val = String(v.value || '').toLowerCase();
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            var outcomeName = null;
            if (val === 'home' || val === '1') outcomeName = match.home;
            else if (val === 'away' || val === '2') outcomeName = match.away;
            else if (val === 'draw' || val === 'x') outcomeName = 'Draw';
            if (!outcomeName) return;
            pushOffer(markets, 'h2h', { name: outcomeName, price: price, bookmaker: bookmakerName, point: null, marketKey: 'match_winner', marketName: bet.name || '', marketSubType: 'regular_time_3way' });
          });
          return;
        }

        if (betName.indexOf('goals over/under') !== -1 || betName.indexOf('over/under') !== -1 || betName.indexOf('total goals') !== -1 || betName.indexOf('match total') !== -1) {
          values.forEach(function (v) {
            var raw = String(v.value || '');
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            var side = /^over/i.test(raw) ? 'Over' : (/^under/i.test(raw) ? 'Under' : null);
            if (!side) return;
            var pointRaw = raw.replace(/^over\s*/i, '').replace(/^under\s*/i, '');
            var point = toNumber(pointRaw.replace(',', '.'));
            pushOffer(markets, 'totals', { name: side, price: price, bookmaker: bookmakerName, point: point, marketKey: 'totals', marketName: bet.name || '', marketSubType: point != null && isAsianLine(point) ? 'asian_totals' : 'totals' });
          });
          return;
        }

        if (betName.indexOf('asian handicap') !== -1 || betName.indexOf('handicap result') !== -1 || betName.indexOf('handicap') !== -1) {
          values.forEach(function (v) {
            var raw = String(v.value || '');
            var price = toNumber(v.odd);
            if (!price || price <= 1) return;
            var parts = raw.split('|');
            if (parts.length < 2) return;
            var sideRaw = String(parts[0] || '').toLowerCase().trim();
            var point = toNumber(String(parts[1] || '').replace(',', '.'));
            if (point == null) return;
            var outcomeName = null;
            if (sideRaw === 'home' || sideRaw === '1') outcomeName = match.home;
            else if (sideRaw === 'away' || sideRaw === '2') outcomeName = match.away;
            if (!outcomeName) return;
            pushOffer(markets, 'spreads', { name: outcomeName, price: price, bookmaker: bookmakerName, point: point, marketKey: 'spreads', marketName: bet.name || '', marketSubType: isAsianLine(point) ? 'asian_spreads' : 'spreads' });
          });
        }
      });
    });
  });
  return out;
}

function getOutcomeKey(outcomeName, match) {
  var normalizedOutcome = normalizeTeamName(outcomeName);
  var normalizedHome = normalizeTeamName(match.home);
  var normalizedAway = normalizeTeamName(match.away);
  if (/^(draw|tie|x|ничья)$/.test(normalizedOutcome)) return 'draw';
  if (normalizedOutcome === normalizedHome) return 'home';
  if (normalizedOutcome === normalizedAway) return 'away';
  if (/^(home|1)$/.test(normalizedOutcome)) return 'home';
  if (/^(away|2)$/.test(normalizedOutcome)) return 'away';
  return null;
}

function getTotalSelectionKey(name) {
  var raw = String(name || '').toLowerCase();
  if (raw.indexOf('over') !== -1 || raw.indexOf('больше') !== -1) return 'over';
  if (raw.indexOf('under') !== -1 || raw.indexOf('меньше') !== -1) return 'under';
  return null;
}

function getSpreadSelectionKey(name, match) {
  return getOutcomeKey(name, match);
}

function getDoubleChanceSelectionKey(name, match) {
  var raw = String(name || '').toUpperCase().replace(/\s+/g, '');
  if (raw === '1X' || raw === 'X2' || raw === '12') return raw;
  var norm = normalizeTeamName(name);
  var home = normalizeTeamName(match.home);
  var away = normalizeTeamName(match.away);
  if (norm === home + ' draw' || norm === 'home draw') return '1X';
  if (norm === 'draw ' + away || norm === 'draw away') return 'X2';
  if (norm === home + ' ' + away || norm === 'home away') return '12';
  if (norm.indexOf(home) !== -1 && norm.indexOf('draw') !== -1) return '1X';
  if (norm.indexOf(away) !== -1 && norm.indexOf('draw') !== -1) return 'X2';
  if (norm.indexOf(home) !== -1 && norm.indexOf(away) !== -1) return '12';
  return null;
}

function getDnbSelectionKey(name, match) {
  var outcome = getOutcomeKey(name, match);
  return outcome === 'draw' ? null : outcome;
}

function getBttsSelectionKey(name) {
  var raw = String(name || '').toLowerCase();
  if (raw === 'yes' || raw === 'да' || raw.indexOf('yes') !== -1) return 'yes';
  if (raw === 'no' || raw === 'нет' || raw.indexOf('no') !== -1) return 'no';
  return null;
}


function getTeamTotalSelectionKey(name) {
  return getTotalSelectionKey(name);
}

function getTeamTotalSide(offer, match) {
  if (!offer) return null;
  if (offer.teamSide === 'home' || offer.teamSide === 'away') return offer.teamSide;
  return inferTeamTotalSide(offer.marketName, offer.marketKey, offer.name, match);
}

function buildTeamTotalModelKey(side, point) {
  return String(side || '') + '|' + pointKey(point);
}


/* ======================= ODDS SOURCES ======================= */
function getApiFootballOdds(config, dateList) {
  if (!config.apiFootballKey || config.enabledSports.indexOf('soccer') === -1) return {};
  var dates = (dateList || []).slice().sort();
  if (!dates.length) return {};
  var outRows = [];

  dates.forEach(function (day) {
    var page = 1;
    while (true) {
      var params = { date: day, timezone: config.apiFootballTimezone, page: page };
      if (config.apiFootballBookmakerIds && config.apiFootballBookmakerIds.length) {
        params.bookmaker = config.apiFootballBookmakerIds.join('-');
      }
      var url = 'https://' + config.apiFootballHost + '/odds?' + buildQuery(params);
      var data = fetchJson(url, { headers: { 'x-apisports-key': config.apiFootballKey } }, 'API-Football odds ' + day + ' page=' + page, { config: config, sportKey: 'soccer', cost: 1 }, Math.max(config.cacheSeconds || 0, 180));
      var rows = data && data.response ? data.response : [];
      var paging = data && data.paging ? data.paging : null;
      outRows = outRows.concat(rows);
      Logger.log('API-Football odds ' + day + ' page ' + page + ': ' + rows.length + ' rows');
      if (!paging || !paging.total || page >= paging.total) break;
      if (page >= 20) break;
      page += 1;
    }
  });

  var parsed = parseApiFootballOddsRows(outRows);
  Logger.log('API-Football odds parsed: ' + Object.keys(parsed).length);
  return parsed;
}

function getDesiredOddsCoverageCount(totalIds, config) {
  var pct = Number(config.oddsApiIoOddsDesiredCoveragePct || 0.58);
  pct = clamp(pct, 0.25, 1);
  return Math.max(12, Math.min(totalIds, Math.round(totalIds * pct)));
}

function sortOddsApiIoMatchesForRequest(matches, config) {
  return (matches || []).slice().sort(function (a, b) {
    var sa = scoreMatchForFetch(a, null, config);
    var sb = scoreMatchForFetch(b, null, config);
    if (sb !== sa) return sb - sa;
    return a.date.getTime() - b.date.getTime();
  });
}

function fetchOddsApiIoChunk(config, sportKey, ids, idToMatch, result, bookmakerState) {
  if (!ids || !ids.length) return 0;
  var attempts = 0;

  while (attempts < 4) {
    var params = {
      apiKey: config.oddsApiIoKey,
      eventIds: ids.join(',')
    };
    if (bookmakerState && bookmakerState.list && bookmakerState.list.length) {
      params.bookmakers = bookmakerState.list.join(',');
    }

    var url = 'https://api.odds-api.io/v3/odds/multi?' + buildQuery(params);
    var meta = fetchJsonMeta(url, {}, 'Odds-API.io odds ' + sportKey, { config: config, sportKey: sportKey, cost: 1 }, config.cacheSeconds || 0);

    if (meta && meta.status >= 200 && meta.status < 300) {
      var rows = Array.isArray(meta.data) ? meta.data : [];
      var parsed = 0;
      rows.forEach(function (eventOdds) {
        var eventId = String(eventOdds.id || '');
        var match = idToMatch[eventId];
        if (!match) return;
        result[match.matchKey] = parseOddsApiIoEventToMarkets(eventOdds, match);
        parsed += 1;
      });
      return parsed;
    }

    if (meta && meta.fetchError) {
      if (ids.length > 1) {
        var mid = Math.ceil(ids.length / 2);
        Logger.log('Odds-API.io odds ' + sportKey + ' chunk fetch failed; splitting chunk ' + ids.length + ' -> ' + mid + '+' + (ids.length - mid));
        return fetchOddsApiIoChunk(config, sportKey, ids.slice(0, mid), idToMatch, result, bookmakerState) +
               fetchOddsApiIoChunk(config, sportKey, ids.slice(mid), idToMatch, result, bookmakerState);
      }
      Logger.log('Odds-API.io odds ' + sportKey + ' single event fetch failed for eventId=' + ids[0] + '; skipped');
      return 0;
    }

    if (meta && meta.status === 429) {
      if (bookmakerState) bookmakerState.rateLimited = true;
      Logger.log('Odds-API.io odds ' + sportKey + ' rate limit hit; stopping further requests for this run');
      return 0;
    }

    var invalidBook = parseOddsApiIoInvalidBookmaker(meta && meta.text);
    if (meta && meta.status === 400 && invalidBook && bookmakerState && bookmakerState.list && bookmakerState.list.length) {
      if (removeBookmakerName(bookmakerState.list, invalidBook)) {
        Logger.log('Odds-API.io bookmaker removed after validation error: ' + invalidBook + '. Remaining: ' + (bookmakerState.list.length ? bookmakerState.list.join(',') : 'ALL'));
        attempts += 1;
        continue;
      }
    }
    return 0;
  }
  return 0;
}

function getOddsApiIoFeed(config, matches) {
  if (!config.oddsApiIoKey) return {};
  var matchesBySport = {};
  var idToMatch = {};

  matches.forEach(function (match) {
    if (!match.oddsEventId) return;
    if (!matchesBySport[match.sport]) matchesBySport[match.sport] = [];
    matchesBySport[match.sport].push(match);
    idToMatch[String(match.oddsEventId)] = match;
  });

  Object.keys(matchesBySport).forEach(function (sportKey) {
    var seen = {};
    matchesBySport[sportKey] = sortOddsApiIoMatchesForRequest(matchesBySport[sportKey], config).filter(function (match) {
      var eventId = String(match.oddsEventId || '');
      if (!eventId || seen[eventId]) return false;
      seen[eventId] = true;
      return true;
    });
  });

  Logger.log('Odds-API.io idsBySport: ' + JSON.stringify(Object.keys(matchesBySport).reduce(function (acc, k) {
    acc[k] = matchesBySport[k].length;
    return acc;
  }, {})) + '; bookmakers=' + (config.bookmakers || []).join(','));

  var result = {};
  var bookmakerState = { list: (config.bookmakers || []).slice() };
  Object.keys(matchesBySport).forEach(function (sportKey) {
    var sportMatches = matchesBySport[sportKey] || [];
    if (!sportMatches.length) return;

    var totalIds = sportMatches.length;
    var desiredCoverage = getDesiredOddsCoverageCount(totalIds, config);
    var initialIdsCount = Math.max(10, Math.min(totalIds, Math.round(totalIds * clamp(config.oddsApiIoOddsInitialFetchShare || 0.68, 0.35, 1))));
    var step = Math.max(10, config.oddsApiIoOddsExpansionStep || 20);
    var fetched = 0;
    var parsedForSport = 0;

    function requestRange(fromIdx, toExclusive) {
      var ids = sportMatches.slice(fromIdx, toExclusive).map(function (m) { return String(m.oddsEventId); });
      var chunks = chunkArray(ids, 10);
      for (var ci = 0; ci < chunks.length; ci++) {
        if (bookmakerState.rateLimited) break;
        parsedForSport += fetchOddsApiIoChunk(config, sportKey, chunks[ci], idToMatch, result, bookmakerState);
      }
      fetched = toExclusive;
    }

    requestRange(0, initialIdsCount);

    while (!bookmakerState.rateLimited && parsedForSport < desiredCoverage && fetched < totalIds) {
      requestRange(fetched, Math.min(totalIds, fetched + step));
      if (fetched >= totalIds) break;
    }

    Logger.log('Odds-API.io odds ' + sportKey + ': parsed=' + parsedForSport + ', requested=' + fetched + '/' + totalIds + ', desiredCoverage=' + desiredCoverage);
  });

  Logger.log('Odds-API.io odds parsed: ' + Object.keys(result).length);
  return result;
}

function isSameMatchForOdds(oddsItem, match, config) {
  var sport = detectSportFromTheOddsMeta(oddsItem);
  if (sport !== match.sport) return false;
  var oddsHome = oddsItem.home_team || oddsItem.homeTeam || oddsItem.home;
  var oddsAway = oddsItem.away_team || oddsItem.awayTeam || oddsItem.away;
  if (!fuzzyTeamsEquivalent(oddsHome, oddsAway, match.home, match.away)) return false;
  var oddsDateRaw = oddsItem.commence_time || oddsItem.commenceTime || oddsItem.start_time || oddsItem.date || '';
  if (!oddsDateRaw || !match.isoDate) return true;
  var diff = dateDiffHours(oddsDateRaw, match.isoDate);
  return diff == null ? true : diff <= (config.matchStartToleranceHours || 12);
}

function attachOfferSource(target, offers, sourceName, config) {
  if (!offers) return;
  MARKET_FAMILIES.forEach(function (marketKey) {
    (offers[marketKey] || []).forEach(function (o) {
      target[marketKey].push(applyOfferMeta(o, marketKey, sourceName, config));
    });
  });
}

function indexOddsSources(matches, oddsSources, config) {
  var exact = {};
  var loose = {};
  var perSport = {};

  matches.forEach(function (match) {
    exact[match.matchKey] = exact[match.matchKey] || createEmptyMarkets();
    loose[match.looseKey] = loose[match.looseKey] || [];
    if (!perSport[match.sport]) perSport[match.sport] = [];
    perSport[match.sport].push(match);
  });

  Object.keys(oddsSources.oddsApiIo || {}).forEach(function (matchKey) {
    exact[matchKey] = exact[matchKey] || createEmptyMarkets();
    attachOfferSource(exact[matchKey], oddsSources.oddsApiIo[matchKey], 'OddsApiIo', config);
  });

  Object.keys(oddsSources.bookiesApi || {}).forEach(function (matchKey) {
    exact[matchKey] = exact[matchKey] || createEmptyMarkets();
    attachOfferSource(exact[matchKey], oddsSources.bookiesApi[matchKey], 'BookiesApi', config);
  });

  Object.keys(oddsSources.apiFootball || {}).forEach(function (matchKey) {
    exact[matchKey] = exact[matchKey] || createEmptyMarkets();
    attachOfferSource(exact[matchKey], oddsSources.apiFootball[matchKey], 'ApiFootball', config);
  });

  (oddsSources.theOdds || []).forEach(function (item) {
    var pseudoMatch = normalizeTheOddsEvent(item);
    var markets = flattenMarkets(item.bookmakers || [], pseudoMatch.sport);
    var key = pseudoMatch.matchKey;
    exact[key] = exact[key] || createEmptyMarkets();
    attachOfferSource(exact[key], markets, 'TheOdds', config);
    if (!loose[pseudoMatch.looseKey]) loose[pseudoMatch.looseKey] = [];
    loose[pseudoMatch.looseKey].push({
      home: pseudoMatch.home,
      away: pseudoMatch.away,
      isoDate: pseudoMatch.isoDate,
      league: pseudoMatch.league,
      leagueKey: pseudoMatch.leagueKey,
      markets: markets,
      sourceName: 'TheOdds'
    });
  });

  Object.keys(oddsSources.oddsApiIo || {}).forEach(function (matchKey) {
    var match = null;
    for (var i = 0; i < matches.length; i++) {
      if (matches[i].matchKey === matchKey) { match = matches[i]; break; }
    }
    if (!match) return;
    if (!loose[match.looseKey]) loose[match.looseKey] = [];
    loose[match.looseKey].push({
      home: match.home,
      away: match.away,
      isoDate: match.isoDate,
      league: match.league,
      leagueKey: match.leagueKey,
      markets: oddsSources.oddsApiIo[matchKey],
      sourceName: 'OddsApiIo'
    });
  });

  Object.keys(oddsSources.bookiesApi || {}).forEach(function (matchKey) {
    var match = null;
    for (var i = 0; i < matches.length; i++) {
      if (matches[i].matchKey === matchKey) { match = matches[i]; break; }
    }
    if (!match) return;
    if (!loose[match.looseKey]) loose[match.looseKey] = [];
    loose[match.looseKey].push({
      home: match.home,
      away: match.away,
      isoDate: match.isoDate,
      league: match.league,
      leagueKey: match.leagueKey,
      markets: oddsSources.bookiesApi[matchKey],
      sourceName: 'BookiesApi'
    });
  });

  Object.keys(oddsSources.apiFootball || {}).forEach(function (matchKey) {
    var match = null;
    for (var i = 0; i < matches.length; i++) {
      if (matches[i].matchKey === matchKey) { match = matches[i]; break; }
    }
    if (!match) return;
    if (!loose[match.looseKey]) loose[match.looseKey] = [];
    loose[match.looseKey].push({
      home: match.home,
      away: match.away,
      isoDate: match.isoDate,
      league: match.league,
      leagueKey: match.leagueKey,
      markets: oddsSources.apiFootball[matchKey],
      sourceName: 'ApiFootball'
    });
  });

  return {
    oddsIndexByExact: exact,
    oddsIndexByLoose: loose,
    matchesBySport: perSport
  };
}

function getFallbackOddsForMatch(match, oddsSources, config) {
  var merged = createEmptyMarkets();
  var candidates = oddsSources.oddsIndexByLoose[match.looseKey] || [];
  candidates.forEach(function (c) {
    var okTeams = fuzzyTeamsEquivalent(c.home, c.away, match.home, match.away);
    var okTime = true;
    if (c.isoDate && match.isoDate) {
      var diff = dateDiffHours(c.isoDate, match.isoDate);
      okTime = diff == null ? true : diff <= (config.fallbackMatchStartToleranceHours || 8);
    }
    if (!okTeams || !okTime) return;
    if (config.strictLeagueFallback && c.leagueKey && match.leagueKey && c.leagueKey !== match.leagueKey) return;
    attachOfferSource(merged, c.markets, c.sourceName || 'Fallback', config);
  });
  return merged;
}

function getCombinedOddsForMatch(match, oddsSources, config) {
  var merged = createEmptyMarkets();
  var exact = oddsSources.oddsIndexByExact[match.matchKey];
  if (exact) attachOfferSource(merged, exact, 'IndexedExact', config);

  var fallback = getFallbackOddsForMatch(match, oddsSources, config);
  attachOfferSource(merged, fallback, 'IndexedLoose', config);

  if (!hasAnyOffers(merged) && (oddsSources.theOdds || []).length) {
    (oddsSources.theOdds || []).forEach(function (item) {
      if (!isSameMatchForOdds(item, match, config)) return;
      attachOfferSource(merged, flattenMarkets(item.bookmakers || [], match.sport), 'TheOdds', config);
    });
  }

  return merged;
}

function shouldFetchApiFootballOddsForMatch(match, currentOdds, config) {
  if (!config.apiFootballKey) return false;
  if (match.sport !== 'soccer') return false;
  if (!match.apiFootballFixtureId) return false;
  if (config.apiFootballOddsMode === 'off') return false;
  if (config.apiFootballOddsMode === 'prefetch' || config.prefetchApiFootballOdds) return true;

  var familyCount = countCoveredFamilies(currentOdds);
  var bookCount = countUniqueBooksAcrossOffers(currentOdds);
  if (!hasAnyOffers(currentOdds)) return true;
  if (familyCount < 2) return true;
  if (bookCount < Math.max(2, config.fallbackMinMarketOffers || 2)) return true;
  return false;
}

function getApiFootballOddsDatesToFetch(config, matches, currentSources) {
  var days = {};
  matches.forEach(function (match) {
    var odds = getCombinedOddsForMatch(match, currentSources, config);
    if (shouldFetchApiFootballOddsForMatch(match, odds, config)) {
      days[getDateKey(match.isoDate)] = true;
    }
  });
  return Object.keys(days).sort();
}

function normalizeBookiesApiSportName(sport) {
  var raw = String(sport || '').toLowerCase();
  if (raw === 'icehockey') return 'hockey';
  return raw;
}

function getBookiesApiAuthParams(config, task, extraParams) {
  var params = Object.assign({
    login: config.bookiesApiLogin || '',
    token: config.bookiesApiToken || config.bookiesApiKey || '',
    task: task || ''
  }, extraParams || {});
  return params;
}

function getBookiesApiCandidateMatches(matches, currentExactIndex, config) {
  var out = [];
  (matches || []).forEach(function (match) {
    if (!match || match.sport !== 'soccer') return;
    if (config.bookiesApiSports && config.bookiesApiSports.length && config.bookiesApiSports.indexOf('soccer') === -1) return;
    var existing = currentExactIndex && currentExactIndex[match.matchKey] ? currentExactIndex[match.matchKey] : null;
    var hasOdds = hasAnyOffers(existing);
    if (config.bookiesApiUseForBackfillOnly) {
      if (!hasOdds || !(existing.h2h && existing.h2h.length)) out.push(match);
    } else {
      out.push(match);
    }
  });
  return out;
}

function formatBookiesApiDay(dateKey) {
  return String(dateKey || '').replace(/-/g, '').slice(0, 8);
}

function getBookiesApiEventList(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.data)) return payload.data;
  if (Array.isArray(payload.results)) return payload.results;
  if (Array.isArray(payload.response)) return payload.response;
  if (Array.isArray(payload.games)) return payload.games;
  if (Array.isArray(payload.matches)) return payload.matches;
  var out = [];
  var seen = {};
  function pushNode(node) {
    var parsed = parseBookiesApiEvent(node);
    if (!parsed) return;
    var key = parsed.gameId || [parsed.home, parsed.away, parsed.isoDate].join('|');
    if (seen[key]) return;
    seen[key] = true;
    out.push(node);
  }
  function walk(node, depth) {
    if (!node || depth > 7) return;
    if (Array.isArray(node)) {
      node.forEach(function (item) { walk(item, depth + 1); });
      return;
    }
    if (typeof node !== 'object') return;
    pushNode(node);
    Object.keys(node).forEach(function (key) {
      var value = node[key];
      if (value && typeof value === 'object') walk(value, depth + 1);
    });
  }
  walk(payload, 0);
  return out;
}

function parseBookiesApiDateValue(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'number') {
    var num = Number(value);
    if (!isNaN(num) && num > 1000000000) {
      if (num < 1000000000000) num *= 1000;
      var dt = new Date(num);
      if (!isNaN(dt.getTime())) return dt.toISOString();
    }
    return String(value);
  }
  var raw = String(value).trim();
  if (!raw) return '';
  if (/^\d{10}$/.test(raw) || /^\d{13}$/.test(raw)) {
    var unix = Number(raw);
    if (raw.length === 10) unix *= 1000;
    var dtUnix = new Date(unix);
    if (!isNaN(dtUnix.getTime())) return dtUnix.toISOString();
  }
  var isoLike = raw
    .replace(/\//g, '-')
    .replace(/\s+/, ' ')
    .replace(/^(\d{2})\.(\d{2})\.(\d{4})(.*)$/,'$3-$2-$1$4')
    .replace(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)$/, '$1-$2-$3T$4');
  var dt = new Date(isoLike);
  if (!isNaN(dt.getTime())) return dt.toISOString();
  return raw;
}

function shiftDateKey(dateKey, deltaDays) {
  var raw = String(dateKey || '');
  var m = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return raw;
  var dt = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (isNaN(dt.getTime())) return raw;
  dt.setUTCDate(dt.getUTCDate() + Number(deltaDays || 0));
  return dt.toISOString().slice(0, 10);
}

function getBookiesApiCandidateLookupMap(candidates, config) {
  var out = {};
  var todayKey = Utilities.formatDate(new Date(), (config && config.timezone) || Session.getScriptTimeZone() || 'UTC', 'yyyy-MM-dd');
  (candidates || []).forEach(function (match) {
    var baseKey = getDateKey(match && match.isoDate);
    if (!baseKey || baseKey === 'nodate') return;
    [-1, 0, 1].forEach(function (offset) {
      var lookupKey = shiftDateKey(baseKey, offset);
      if (!lookupKey || lookupKey === 'nodate' || lookupKey < todayKey) return;
      if (!out[lookupKey]) out[lookupKey] = [];
      out[lookupKey].push(match);
    });
  });
  return out;
}

function getTeamInitialism(name) {
  var parts = canonicalizeTeamName(name).split(' ').filter(Boolean);
  if (!parts.length) return '';
  return parts.map(function (part) { return part.charAt(0); }).join('');
}

function getBookiesTokenSimilarity(a, b) {
  a = String(a || '');
  b = String(b || '');
  if (!a || !b) return 0;
  if (a === b) return 1;
  var al = a.length;
  var bl = b.length;
  var dp = [];
  for (var i = 0; i <= al; i++) dp[i] = [i];
  for (var j = 1; j <= bl; j++) dp[0][j] = j;
  for (var ii = 1; ii <= al; ii++) {
    for (var jj = 1; jj <= bl; jj++) {
      var cost = a.charAt(ii - 1) === b.charAt(jj - 1) ? 0 : 1;
      dp[ii][jj] = Math.min(
        dp[ii - 1][jj] + 1,
        dp[ii][jj - 1] + 1,
        dp[ii - 1][jj - 1] + cost
      );
    }
  }
  return 1 - (dp[al][bl] / Math.max(al, bl, 1));
}

function scoreBookiesNameToken(a, b) {
  if (!a || !b) return 0;
  if (a === b) return 1;
  if ((a.length >= 3 && b.indexOf(a) === 0) || (b.length >= 3 && a.indexOf(b) === 0)) return 0.9;
  var ai = getTeamInitialism(a);
  var bi = getTeamInitialism(b);
  if (ai && bi && ai === bi && ai.length >= 2) return 0.82;
  if (a.replace(/\s+/g, '') === b.replace(/\s+/g, '')) return 0.94;
  var sim = getBookiesTokenSimilarity(a, b);
  if (sim >= 0.9) return 0.9;
  if (sim >= 0.8) return 0.78;
  if (sim >= 0.72) return 0.64;
  return 0;
}

function scoreBookiesLeagueName(a, b) {
  a = canonicalizeLeagueName(a);
  b = canonicalizeLeagueName(b);
  if (!a || !b) return 0;
  if (a === b) return 0.22;
  if (a.indexOf(b) !== -1 || b.indexOf(a) !== -1) return 0.14;
  var ap = a.split(' ').filter(Boolean);
  var bp = b.split(' ').filter(Boolean);
  if (!ap.length || !bp.length) return 0;
  var common = 0;
  ap.forEach(function (part) {
    if (bp.indexOf(part) !== -1) common += 1;
  });
  return common ? Math.min(0.12, 0.12 * common / Math.max(ap.length, bp.length, 1)) : 0;
}
function getBookiesApiEventIsoDate(item) {
  if (!item || typeof item !== 'object') return '';
  return parseBookiesApiDateValue(
    item.event_date || item.start_time || item.commence_time || item.kickoff || item.date || item.match_time || item.time || item.datetime ||
    item.start || item.starts_at || item.startAt || item.match_date || item.event_time || item.ts || item.timestamp ||
    (item.date_start && item.time_start ? String(item.date_start) + ' ' + String(item.time_start) : '') ||
    (item.date && item.time ? String(item.date) + ' ' + String(item.time) : '')
  );
}

function getBookiesApiEventLeague(item) {
  if (!item || typeof item !== 'object') return '';
  return (item.league && (item.league.name || item.league.title || item.league.league_name)) ||
    (item.competition && (item.competition.name || item.competition.title)) ||
    (item.tournament && (item.tournament.name || item.tournament.title || item.tournament)) ||
    item.league_name || item.competition_name || item.tournament_name || item.championship || item.league || item.competition || '';
}

function getBookiesApiTeamName(value) {
  if (!value) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value !== 'object') return '';
  return String(
    value.name || value.team_name || value.teamName || value.title || value.short_name || value.shortName ||
    value.common_name || value.commonName || value.en_name || value.enName || value.slug || value.label ||
    value.full_name || value.fullName || value.abbr || value.code || ''
  );
}

function getBookiesApiTeams(item) {
  if (!item || typeof item !== 'object') return { home: '', away: '' };
  var home =
    getBookiesApiTeamName(item.home_team) ||
    getBookiesApiTeamName(item.homeTeam) ||
    getBookiesApiTeamName(item.home) ||
    getBookiesApiTeamName(item.team_home) ||
    getBookiesApiTeamName(item.team1) ||
    getBookiesApiTeamName(item.team1_name) ||
    getBookiesApiTeamName(item.home_name) ||
    getBookiesApiTeamName(item.opponent1) ||
    getBookiesApiTeamName(item.opponent1_name) ||
    getBookiesApiTeamName(item.opp_1) ||
    getBookiesApiTeamName(item.localteam) ||
    getBookiesApiTeamName(item.localteam_name) ||
    getBookiesApiTeamName(item.local) ||
    getBookiesApiTeamName(item.local_name) ||
    getBookiesApiTeamName(item.teams && (item.teams.home || item.teams.local || item.teams.team1 || item.teams[0])) ||
    getBookiesApiTeamName(item.participants && item.participants[0]) ||
    getBookiesApiTeamName(item.competitors && item.competitors[0]) || '';
  var away =
    getBookiesApiTeamName(item.away_team) ||
    getBookiesApiTeamName(item.awayTeam) ||
    getBookiesApiTeamName(item.away) ||
    getBookiesApiTeamName(item.team_away) ||
    getBookiesApiTeamName(item.team2) ||
    getBookiesApiTeamName(item.team2_name) ||
    getBookiesApiTeamName(item.away_name) ||
    getBookiesApiTeamName(item.opponent2) ||
    getBookiesApiTeamName(item.opponent2_name) ||
    getBookiesApiTeamName(item.opp_2) ||
    getBookiesApiTeamName(item.visitorteam) ||
    getBookiesApiTeamName(item.visitorteam_name) ||
    getBookiesApiTeamName(item.visitor) ||
    getBookiesApiTeamName(item.visitor_name) ||
    getBookiesApiTeamName(item.teams && (item.teams.away || item.teams.visitor || item.teams.team2 || item.teams[1])) ||
    getBookiesApiTeamName(item.participants && item.participants[1]) ||
    getBookiesApiTeamName(item.competitors && item.competitors[1]) || '';
  if ((!home || !away) && Array.isArray(item.teams) && item.teams.length >= 2) {
    home = home || getBookiesApiTeamName(item.teams[0]);
    away = away || getBookiesApiTeamName(item.teams[1]);
  }
  return { home: String(home || ''), away: String(away || '') };
}

function parseBookiesApiEvent(item) {
  if (!item) return null;
  var gameId = item.game_id || item.gameId || item.id || item.match_id || item.fixture_id || item.event_id || item.game || item.id_game;
  var teams = getBookiesApiTeams(item);
  var home = teams.home;
  var away = teams.away;
  if (!gameId || !home || !away) return null;
  return {
    gameId: String(gameId),
    home: home,
    away: away,
    isoDate: getBookiesApiEventIsoDate(item),
    league: getBookiesApiEventLeague(item),
    raw: item
  };
}

function scoreBookiesCandidateName(a, b) {
  a = canonicalizeTeamName(a);
  b = canonicalizeTeamName(b);
  if (!a || !b) return 0;
  if (a === b) return 1;
  if (a.indexOf(b) !== -1 || b.indexOf(a) !== -1) return 0.96;
  var wholeSim = getBookiesTokenSimilarity(a, b);
  if (wholeSim >= 0.94) return 0.94;
  if (wholeSim >= 0.86) return 0.88;
  var ai = getTeamInitialism(a);
  var bi = getTeamInitialism(b);
  if (ai && bi && ai.length >= 2 && ai === bi) return 0.82;
  var ap = a.split(' ').filter(Boolean);
  var bp = b.split(' ').filter(Boolean);
  if (!ap.length || !bp.length) return wholeSim >= 0.76 ? 0.72 : 0;
  var total = 0;
  var used = {};
  ap.forEach(function (part) {
    var best = 0;
    var bestIdx = -1;
    for (var i = 0; i < bp.length; i++) {
      if (used[i]) continue;
      var score = scoreBookiesNameToken(part, bp[i]);
      if (score > best) {
        best = score;
        bestIdx = i;
      }
    }
    if (bestIdx !== -1) {
      used[bestIdx] = true;
      total += best;
    }
  });
  var tokenScore = total / Math.max(ap.length, bp.length, 1);
  if (wholeSim >= 0.82) tokenScore = Math.max(tokenScore, 0.78);
  else if (wholeSim >= 0.76) tokenScore = Math.max(tokenScore, 0.70);
  return tokenScore;
}

function hoursDiff(a, b) {
  var da = new Date(a);
  var db = new Date(b);
  if (isNaN(da.getTime()) || isNaN(db.getTime())) return null;
  return Math.abs(da.getTime() - db.getTime()) / 3600000;
}

function matchBookiesEventToCandidate(event, candidates) {
  if (!event || !candidates || !candidates.length) return null;
  var eventKey = buildMatchKey('soccer', event.home, event.away, event.isoDate);
  for (var i = 0; i < candidates.length; i++) {
    if (candidates[i].matchKey === eventKey) return candidates[i];
  }

  var eventDateKey = getDateKey(event.isoDate);
  var eventHome = canonicalizeTeamName(event.home);
  var eventAway = canonicalizeTeamName(event.away);
  var eventLooseKey = buildLooseMatchKey('soccer', event.home, event.away);
  var best = null;
  var bestScore = 0;
  var bestHours = 999;

  for (var j = 0; j < candidates.length; j++) {
    var cand = candidates[j];
    if (!cand) continue;
    var diff = hoursDiff(cand.isoDate, event.isoDate);
    if (diff == null) diff = cand.dateKey === eventDateKey ? 0 : 999;
    if (diff > 42 && cand.dateKey !== eventDateKey) continue;

    var candHome = canonicalizeTeamName(cand.homeTeam || cand.home || '');
    var candAway = canonicalizeTeamName(cand.awayTeam || cand.away || '');
    var direct = scoreBookiesCandidateName(eventHome, candHome) + scoreBookiesCandidateName(eventAway, candAway);
    var reverse = scoreBookiesCandidateName(eventHome, candAway) + scoreBookiesCandidateName(eventAway, candHome);
    var score = Math.max(direct, reverse);
    if (cand.looseKey === eventLooseKey) score += 0.15;
    if (cand.dateKey === eventDateKey) score += 0.12;
    if (diff <= 6) score += 0.08;
    else if (diff <= 12) score += 0.04;
    score += scoreBookiesLeagueName(event.league, cand.league);
    if (direct !== reverse) score += direct > reverse ? 0.03 : 0.01;
    if (score > bestScore || (score === bestScore && diff < bestHours)) {
      best = cand;
      bestScore = score;
      bestHours = diff;
    }
  }
  return bestScore >= 1.45 ? best : null;
}

function getBookiesApiKnownBookmakers() {
  return {
    bet365: 'Bet365', sbobet: 'Sbobet', '10bet': '10Bet', betfair: 'BetFair', betfairexchange: 'BetFair', unibet: 'UniBet',
    betregal: 'Betregal', bwin: 'BWin', '888sport': '888Sport', '188bet': '188Bet', cloudbet: 'CloudBet',
    betvictor: 'BetVictor', betsson: 'Betsson', cashpoint: 'CashPoint', '1xbet': '1xBet', pinnacle: 'Pinnacle',
    williamhill: 'William Hill', ladbrokes: 'Ladbrokes', marathonbet: 'Marathonbet', betathome: 'BetAtHome', melbet: 'MelBet'
  };
}

function normalizeBookiesApiBookmakerName(name) {
  var raw = String(name || '').trim();
  if (!raw) return '';
  var key = normalizeBookmakerName(raw);
  var known = getBookiesApiKnownBookmakers();
  return known[key] || raw;
}


function getBookiesApiOddsCodeMap() {
  return {
    '1_1': 'Full Time Result',
    '1_2': 'Asian Handicap',
    '1_3': 'Over/Under',
    '1_4': 'Asian Corners',
    '1_5': '1st Half Asian Handicap',
    '1_6': '1st Half Goal Line',
    '1_7': '1st Half Asian Corners',
    '1_8': 'Half Time Result',
    '3_4': 'Draw No Bet',
    '18_1': 'Money Line',
    '18_2': 'Spread',
    '18_3': 'Total Points',
    '18_4': 'Money Line (Half)',
    '18_5': 'Spread (Half)',
    '18_6': 'Total Points (Half)',
    '18_7': 'Quarter - Winner (2-Way)',
    '18_8': 'Quarter - Handicap',
    '18_9': 'Quarter - Total (2-Way)',
    '*_1': 'Match Winner 2-Way',
    '*_2': 'Asian Handicap',
    '*_3': 'Over/Under'
  };
}

function looksLikeBookiesApiMarketCode(value) {
  return /^\*?\d+_\d+$/.test(String(value || '').trim());
}

function getBookiesApiMarketDescriptor(value) {
  var code = String(value || '').trim();
  return getBookiesApiOddsCodeMap()[code] || code;
}

function isBookiesApiNumericKey(value) {
  var raw = String(value || '').trim().replace(',', '.');
  return /^[-+]?\d+(?:\.\d+)?$/.test(raw);
}

function toBookiesApiLineValue(value) {
  var raw = String(value == null ? '' : value).trim().replace(',', '.');
  if (!raw || !/^[-+]?\d+(?:\.\d+)?$/.test(raw)) return null;
  return Number(raw);
}


function isUsablePublishedLine_(match, family, point) {
  if (family !== 'totals' && family !== 'teamTotals') return true;
  var num = toNumber(point);
  if (num == null) return false;
  if (num <= 0) return false;
  if (match && match.sport === 'soccer' && num > 8) return false;
  return true;
}

function buildBookiesApiDescriptorText_(descriptor, ctx, node) {
  var parts = [
    descriptor || '',
    ctx && ctx.marketName || '',
    ctx && ctx.marketKey || '',
    ctx && ctx.pathText || '',
    node && (node.name || node.label || node.title || node.market_name || node.market || node.type || node.group || node.bet || node.wager) || ''
  ].filter(function (x) { return x; });
  return parts.join(' > ');
}

function extractBookiesApiContextualLine_(text, family, sport) {
  var source = String(text || '');
  if (!source) return null;
  source = source.replace(/\*?\d+_\d+/g, ' ');
  var patterns = [];
  if (family === 'totals' || family === 'teamTotals') {
    patterns = [
      /(?:team\s*total(?:s)?|individual\s*total(?:s)?|totals?|total|over\s*\/\s*under|over\/under|o\/u|ou)[^0-9+-]{0,12}([+-]?\d+(?:[\.,]\d+)?)/ig,
      /\(([+-]?\d+(?:[\.,]\d+)?)\)/g
    ];
  } else if (family === 'spreads') {
    patterns = [
      /(?:asian\s*handicap|handicap|spread|line|run\s*line|puck\s*line|fora|фора)[^0-9+-]{0,12}([+-]?\d+(?:[\.,]\d+)?)/ig,
      /\(([+-]?\d+(?:[\.,]\d+)?)\)/g
    ];
  }
  var candidates = [];
  patterns.forEach(function (re) {
    var m;
    while ((m = re.exec(source)) !== null) {
      var num = toNumber(String(m[1]).replace(',', '.'));
      if (num == null || !isFinite(num)) continue;
      candidates.push(num);
    }
  });
  if (!candidates.length) return null;
  candidates = candidates.filter(function (num) {
    if (family === 'totals' || family === 'teamTotals') {
      if (num <= 0) return false;
      if (sport === 'soccer' && num > 8) return false;
      return true;
    }
    if (family === 'spreads') return Math.abs(num) <= 50;
    return true;
  });
  if (!candidates.length) return null;
  candidates.sort(function (a, b) {
    function score(v) {
      var frac = Math.abs(v % 1);
      var s = 0;
      if (frac === 0.25 || frac === 0.5 || frac === 0.75) s += 4;
      else if (frac !== 0) s += 2;
      if (family === 'totals' || family === 'teamTotals') {
        if (sport === 'soccer' && v >= 1.5 && v <= 4.5) s += 3;
        else if (v >= 1 && v <= 8) s += 1;
      }
      return s;
    }
    return score(b) - score(a);
  });
  return candidates[0];
}

function resolveBookiesApiPoint_(match, family, explicitPoint, descriptor, ctx, node) {
  var explicit = toNumber(explicitPoint);
  if (family === 'totals' || family === 'teamTotals') {
    if (isUsablePublishedLine_(match, family, explicit)) return explicit;
    var contextual = extractBookiesApiContextualLine_(buildBookiesApiDescriptorText_(descriptor, ctx, node), family, match && match.sport);
    return isUsablePublishedLine_(match, family, contextual) ? contextual : explicit;
  }
  if (family === 'spreads') {
    if (explicit != null) return explicit;
    return extractBookiesApiContextualLine_(buildBookiesApiDescriptorText_(descriptor, ctx, node), family, match && match.sport);
  }
  return explicit;
}

function shouldUseBookiesApiNumericKeyAsPoint_(numericKey, ctx, match) {
  var num = toNumber(numericKey);
  if (num == null) return false;
  var info = detectMarketSubtypeFromText(String(ctx && ctx.marketKey || ''), String(ctx && ctx.marketName || ''), match && match.sport);
  if (!info) return false;
  if (info.family === 'spreads') return true;
  if (info.family === 'totals' || info.family === 'teamTotals') {
    var frac = Math.abs(num % 1);
    if (num <= 0) return false;
    if (frac === 0.25 || frac === 0.5 || frac === 0.75) return true;
    if (num >= 3) return true;
    return false;
  }
  return false;
}

function getBookiesApiLeafPrice(value) {
  var num = toNumber(value);
  if (num != null && num > 1) return num;
  if (typeof value === 'string') {
    var raw = value.trim().replace(',', '.');
    if (/^\d+(?:\.\d+)?$/.test(raw)) {
      var parsed = Number(raw);
      if (!isNaN(parsed) && parsed > 1) return parsed;
    }
  }
  return null;
}

function getBookiesApiNumericEntries(node) {
  if (!node || typeof node !== 'object' || Array.isArray(node)) return [];
  var out = [];
  Object.keys(node).forEach(function (key) {
    var price = getBookiesApiLeafPrice(node[key]);
    if (price == null) return;
    out.push({ key: String(key), price: price });
  });
  return out;
}

function getBookiesApiArrayPrices(node) {
  if (!Array.isArray(node)) return [];
  return node.map(function (value, idx) {
    var price = getBookiesApiLeafPrice(value);
    return price == null ? null : { key: String(idx), price: price };
  }).filter(Boolean);
}

function getBookiesApiSpreadPointForSide(point, side) {
  var numeric = getLineValue(point);
  if (numeric == null) return point;
  if (side === 'away') return -numeric;
  return numeric;
}

function pushBookiesApiDecodedOffer(markets, family, name, price, bookmaker, point, marketKey, marketName, marketSubType, seen, extra) {
  price = Number(price);
  if (!price || isNaN(price) || price <= 1) return false;
  var offer = Object.assign({
    name: name,
    price: price,
    bookmaker: bookmaker || 'BookiesAPI',
    point: point,
    marketKey: marketKey || '',
    marketName: marketName || '',
    marketSubType: marketSubType || ''
  }, extra || {});
  var uniq = [family, offer.bookmaker, offer.name, offer.point == null ? 'nopoint' : String(offer.point), offer.price, offer.teamSide || ''].join('|');
  if (seen[uniq]) return false;
  seen[uniq] = true;
  pushOffer(markets, family, offer);
  return true;
}

function parseBookiesApiEncodedSelections(node, ctx, match, markets, seen) {
  var info = detectMarketSubtypeFromText(ctx.marketKey || '', ctx.marketName || '', match.sport) ||
             detectMarketSubtypeFromText('', ctx.marketName || '', match.sport);
  if (!info) return false;

  var entries = Array.isArray(node) ? getBookiesApiArrayPrices(node) : getBookiesApiNumericEntries(node);
  if (!entries.length) return false;

  var bookmaker = normalizeBookiesApiBookmakerName(ctx.bookmaker || '');
  var marketKey = ctx.marketKey || '';
  var marketName = ctx.marketName || marketKey || '';
  var descriptor = buildBookiesApiDescriptorText_(ctx && ctx.descriptor, ctx, node);
  var point = resolveBookiesApiPoint_(match, info.family, getLineValue(ctx.point), descriptor, ctx, node);
  var emitted = false;
  var valuesByKey = {};
  entries.forEach(function (entry) {
    valuesByKey[String(entry.key).toLowerCase()] = entry.price;
  });

  function emit(family, name, price, extra) {
    emitted = pushBookiesApiDecodedOffer(markets, family, name, price, bookmaker, point, marketKey, marketName, info ? info.subType : '', seen, extra) || emitted;
  }

  if (info.family === 'h2h') {
    var homePrice = valuesByKey.home || valuesByKey['1'] || valuesByKey.h;
    var awayPrice = valuesByKey.away || valuesByKey['2'] || valuesByKey.a;
    var drawPrice = valuesByKey.draw || valuesByKey.x || valuesByKey['0'];
    if (homePrice || awayPrice || drawPrice) {
      emit('h2h', match.home, homePrice);
      emit('h2h', 'Draw', drawPrice);
      emit('h2h', match.away, awayPrice);
      return emitted;
    }
    if (entries.length === 3) {
      emit('h2h', match.home, entries[0].price);
      emit('h2h', 'Draw', entries[1].price);
      emit('h2h', match.away, entries[2].price);
      return emitted;
    }
    if (entries.length === 2) {
      emit('h2h', match.home, entries[0].price);
      emit('h2h', match.away, entries[1].price);
      return emitted;
    }
    return false;
  }

  if (info.family === 'dnb') {
    var dnbHome = valuesByKey.home || valuesByKey['1'] || valuesByKey.h;
    var dnbAway = valuesByKey.away || valuesByKey['2'] || valuesByKey.a;
    if (dnbHome || dnbAway) {
      emit('dnb', match.home, dnbHome);
      emit('dnb', match.away, dnbAway);
      return emitted;
    }
    if (entries.length >= 2) {
      emit('dnb', match.home, entries[0].price);
      emit('dnb', match.away, entries[1].price);
      return emitted;
    }
    return false;
  }

  if (info.family === 'doubleChance') {
    emit('doubleChance', '1X', valuesByKey['1x'] || valuesByKey.home_draw || valuesByKey['1x2']);
    emit('doubleChance', 'X2', valuesByKey.x2 || valuesByKey.draw_away);
    emit('doubleChance', '12', valuesByKey['12'] || valuesByKey.home_away);
    if (emitted) return true;
    if (entries.length >= 3) {
      emit('doubleChance', '1X', entries[0].price);
      emit('doubleChance', 'X2', entries[1].price);
      emit('doubleChance', '12', entries[2].price);
      return emitted;
    }
    return false;
  }

  if (info.family === 'btts') {
    emit('btts', 'Yes', valuesByKey.yes || valuesByKey['1']);
    emit('btts', 'No', valuesByKey.no || valuesByKey['2'] || valuesByKey['0']);
    if (emitted) return true;
    if (entries.length >= 2) {
      emit('btts', 'Yes', entries[0].price);
      emit('btts', 'No', entries[1].price);
      return emitted;
    }
    return false;
  }

  if (info.family === 'totals' || info.family === 'teamTotals') {
    var extra = info.family === 'teamTotals'
      ? { teamSide: inferTeamTotalSide(marketName, marketKey, '', match) || ctx.teamSide }
      : null;
    var overPrice = valuesByKey.over || valuesByKey.o;
    var underPrice = valuesByKey.under || valuesByKey.u;
    if (overPrice == null && underPrice == null) {
      // BookiesAPI encoded O/U often arrives as numeric selections where 1=Under and 2=Over.
      // Do not map 1->Over here, otherwise the publication layer can flip ТБ/ТМ.
      overPrice = valuesByKey['2'];
      underPrice = valuesByKey['1'] || valuesByKey['0'];
    }
    emit(info.family, 'Over', overPrice, extra);
    emit(info.family, 'Under', underPrice, extra);
    if (emitted) return true;
    if (entries.length >= 2) {
      emit(info.family, 'Under', entries[0].price, extra);
      emit(info.family, 'Over', entries[1].price, extra);
      return emitted;
    }
    return false;
  }

  if (info.family === 'spreads') {
    var homeSpread = valuesByKey.home || valuesByKey['1'] || valuesByKey.h;
    var awaySpread = valuesByKey.away || valuesByKey['2'] || valuesByKey.a;
    if (homeSpread || awaySpread) {
      emit('spreads', match.home, homeSpread, { point: getBookiesApiSpreadPointForSide(point, 'home') });
      emit('spreads', match.away, awaySpread, { point: getBookiesApiSpreadPointForSide(point, 'away') });
      return emitted;
    }
    if (entries.length >= 2) {
      emit('spreads', match.home, entries[0].price, { point: getBookiesApiSpreadPointForSide(point, 'home') });
      emit('spreads', match.away, entries[1].price, { point: getBookiesApiSpreadPointForSide(point, 'away') });
      return emitted;
    }
    return false;
  }

  return false;
}


function parseBookiesApiDirectSelections(obj, descriptor, ctx, match, markets, seen) {
  if (!obj || typeof obj !== 'object') return false;
  var info = detectMarketSubtypeFromText(ctx.marketKey || '', descriptor || ctx.marketName || '', match.sport) || detectMarketSubtypeFromText('', descriptor || '', match.sport);
  var basePoint = getLineValue(obj.point, obj.total, obj.line, obj.handicap, obj.hdp, ctx.point);
  var point = resolveBookiesApiPoint_(match, info && info.family, basePoint, descriptor, ctx, obj);
  var bookmaker = normalizeBookiesApiBookmakerName(ctx.bookmaker || obj.bookmaker || obj.bookie || obj.company || obj.source || obj.site || obj.title || '');
  function emit(family, name, price, extra) {
    price = Number(price);
    if (!price || isNaN(price) || price <= 1) return;
    var offerPoint = point;
    if (extra && Object.prototype.hasOwnProperty.call(extra, 'point')) {
      offerPoint = extra.point;
      delete extra.point;
    }
    var offer = Object.assign({ name: name, price: price, bookmaker: bookmaker || 'BookiesAPI', point: offerPoint, marketKey: ctx.marketKey || '', marketName: descriptor || ctx.marketName || '', marketSubType: info ? info.subType : '' }, extra || {});
    var uniq = [family, offer.bookmaker, offer.name, offer.point == null ? 'nopoint' : String(offer.point), offer.price, offer.teamSide || ''].join('|');
    if (seen[uniq]) return;
    seen[uniq] = true;
    pushOffer(markets, family, offer);
  }
  var emitted = false;
  var map = {
    home: obj.home, away: obj.away, draw: obj.draw, x: obj.x, '1': obj['1'], '2': obj['2'],
    over: obj.over, under: obj.under, yes: obj.yes, no: obj.no, '1x': obj['1x'], x2: obj.x2, '12': obj['12']
  };
  if (map.home || map.away || map.draw || map['1'] || map['2'] || map.x) {
    var family = info ? info.family : 'h2h';
    if (family === 'dnb') {
      emit('dnb', match.home, map.home || map['1']);
      emit('dnb', match.away, map.away || map['2']);
    } else {
      emit('h2h', match.home, map.home || map['1']);
      emit('h2h', 'Draw', map.draw || map.x);
      emit('h2h', match.away, map.away || map['2']);
    }
    emitted = true;
  }
  if (map.over || map.under) {
    var fam = info && info.family === 'teamTotals' ? 'teamTotals' : 'totals';
    var extra = {};
    if (fam === 'teamTotals') extra.teamSide = inferTeamTotalSide(descriptor || ctx.marketName || '', ctx.marketKey || '', '', match) || ctx.teamSide;
    emit(fam, 'Over', map.over, extra);
    emit(fam, 'Under', map.under, extra);
    emitted = true;
  }
  if (map.yes || map.no) {
    emit('btts', 'Yes', map.yes);
    emit('btts', 'No', map.no);
    emitted = true;
  }
  if (map['1x'] || map.x2 || map['12']) {
    emit('doubleChance', '1X', map['1x']);
    emit('doubleChance', 'X2', map.x2);
    emit('doubleChance', '12', map['12']);
    emitted = true;
  }
  if ((obj.handicap != null || obj.hdp != null || obj.line != null) && (map.home || map.away || map['1'] || map['2'])) {
    emit('spreads', match.home, map.home || map['1'], { point: getBookiesApiSpreadPointForSide(point, 'home') });
    emit('spreads', match.away, map.away || map['2'], { point: getBookiesApiSpreadPointForSide(point, 'away') });
    emitted = true;
  }
  return emitted;
}

function parseBookiesApiOutcomeArray(arr, ctx, match, markets, seen) {
  if (!Array.isArray(arr) || !arr.length) return false;
  var descriptor = String(ctx.marketName || ctx.marketKey || '');
  var info = detectMarketSubtypeFromText(ctx.marketKey || '', descriptor, match.sport);
  var emitted = false;
  arr.forEach(function (item) {
    if (!item || typeof item !== 'object') return;
    var name = item.name || item.label || item.outcome || item.selection || item.title || item.bet || '';
    var price = Number(item.price || item.odd || item.odds || item.value || item.kf || item.coefficient);
    if (!price || isNaN(price) || price <= 1) return;
    var bookmaker = normalizeBookiesApiBookmakerName(ctx.bookmaker || item.bookmaker || item.bookie || item.company || item.source || item.site || item.title || '');
    var family = info ? info.family : null;
    var finalName = name;
    var extra = {};
    if (!family) {
      if (getDoubleChanceSelectionKey(name, match)) family = 'doubleChance';
      else if (getBttsSelectionKey(name)) family = 'btts';
      else if (getTotalSelectionKey(name)) family = 'totals';
      else family = 'h2h';
    }
    var point = resolveBookiesApiPoint_(match, family, getLineValue(item.point, item.total, item.line, item.handicap, item.hdp, ctx.point), descriptor, ctx, item);
    if (family === 'h2h') finalName = name === '1' ? match.home : (name === '2' ? match.away : (String(name).toUpperCase() === 'X' ? 'Draw' : name));
    if (family === 'doubleChance') finalName = getDoubleChanceSelectionKey(name, match) || name;
    if (family === 'btts') finalName = getBttsSelectionKey(name) === 'yes' ? 'Yes' : (getBttsSelectionKey(name) === 'no' ? 'No' : name);
    if (family === 'totals' || family === 'teamTotals') {
      finalName = getTotalSelectionKey(name) === 'over' ? 'Over' : (getTotalSelectionKey(name) === 'under' ? 'Under' : name);
      if (family === 'teamTotals') extra.teamSide = inferTeamTotalSide(descriptor, ctx.marketKey || '', name, match) || ctx.teamSide;
    }
    if (family === 'dnb') {
      var side = getDnbSelectionKey(name, match);
      if (!side) return;
      finalName = side === 'home' ? match.home : match.away;
    }
    if (family === 'spreads') {
      var spreadSide = getSpreadSelectionKey(name, match);
      if (spreadSide === 'home') {
        finalName = match.home;
        point = getBookiesApiSpreadPointForSide(point, 'home');
      } else if (spreadSide === 'away') {
        finalName = match.away;
        point = getBookiesApiSpreadPointForSide(point, 'away');
      }
    }
    var uniq = [family, bookmaker, finalName, point == null ? 'nopoint' : String(point), price, extra.teamSide || ''].join('|');
    if (seen[uniq]) return;
    seen[uniq] = true;
    pushOffer(markets, family, Object.assign({
      name: finalName,
      price: price,
      bookmaker: bookmaker || 'BookiesAPI',
      point: point,
      marketKey: ctx.marketKey || '',
      marketName: descriptor,
      marketSubType: info ? info.subType : ''
    }, extra));
    emitted = true;
  });
  return emitted;
}

function parseBookiesApiOddsPayload(payload, match) {
  var markets = createEmptyMarkets();
  var seen = {};
  function walk(node, ctx, depth, pathParts) {
    if (!node || depth > 8) return;
    pathParts = pathParts || [];
    if (Array.isArray(node)) {
      if (parseBookiesApiOutcomeArray(node, ctx, match, markets, seen)) return;
      if (parseBookiesApiEncodedSelections(node, ctx, match, markets, seen)) return;
      node.forEach(function (item) { walk(item, ctx, depth + 1, pathParts); });
      return;
    }
    if (typeof node !== 'object') return;
    var nextCtx = Object.assign({}, ctx || {});
    nextCtx.bookmaker = normalizeBookiesApiBookmakerName(nextCtx.bookmaker || node.bookmaker || node.bookie || node.company || node.source || node.site || '');
    if (!nextCtx.marketKey && node.market_key) nextCtx.marketKey = String(node.market_key);
    if (!nextCtx.marketName && (node.market_name || node.market || node.type || node.group || node.bet || node.wager)) nextCtx.marketName = String(node.market_name || node.market || node.type || node.group || node.bet || node.wager);
    nextCtx.point = getLineValue(node.point, node.total, node.line, node.handicap, node.hdp, nextCtx.point);
    nextCtx.pathText = pathParts.join(' > ');
    var descriptor = [nextCtx.marketName || '', pathParts.join(' > ')].join(' > ').replace(/^\s*>\s*|\s*>\s*$/g, '').trim();
    nextCtx.descriptor = descriptor;
    var directHandled = parseBookiesApiDirectSelections(node, descriptor, nextCtx, match, markets, seen);
    var encodedHandled = parseBookiesApiEncodedSelections(node, nextCtx, match, markets, seen);
    if (Array.isArray(node.outcomes)) parseBookiesApiOutcomeArray(node.outcomes, nextCtx, match, markets, seen);
    if (Array.isArray(node.selections)) parseBookiesApiOutcomeArray(node.selections, nextCtx, match, markets, seen);
    if (Array.isArray(node.odds)) parseBookiesApiOutcomeArray(node.odds, nextCtx, match, markets, seen);
    Object.keys(node).forEach(function (key) {
      var value = node[key];
      if (!value || typeof value !== 'object') return;
      var childCtx = Object.assign({}, nextCtx);
      var normalizedKey = normalizeBookmakerName(key);
      if (!childCtx.bookmaker && getBookiesApiKnownBookmakers()[normalizedKey]) childCtx.bookmaker = getBookiesApiKnownBookmakers()[normalizedKey];
      if (looksLikeBookiesApiMarketCode(key)) {
        childCtx.marketKey = key;
        childCtx.marketName = getBookiesApiMarketDescriptor(key);
        childCtx.point = null;
      } else if (isBookiesApiNumericKey(key) && childCtx.marketKey) {
        var numericKey = toBookiesApiLineValue(key);
        if (shouldUseBookiesApiNumericKeyAsPoint_(numericKey, childCtx, match)) {
          childCtx.point = getLineValue(childCtx.point, numericKey);
        }
      } else if (!childCtx.marketName && !childCtx.bookmaker && detectMarketSubtypeFromText('', key, match.sport)) {
        childCtx.marketName = key;
      }
      walk(value, childCtx, depth + 1, pathParts.concat([key]));
    });
    if (!directHandled && !encodedHandled && node.results && typeof node.results === 'object') {
      walk(node.results, nextCtx, depth + 1, pathParts.concat(['results']));
    }
  }
  walk(payload, { bookmaker: '', marketKey: '', marketName: '', point: null }, 0, []);
  return markets;
}

function getBookiesApiFeed(config, matches, currentExactIndex) {
  if (!config.bookiesApiEnabled) {
    Logger.log('BookiesAPI skipped: disabled (set BOOKIES_API_ENABLED=true or provide login+token for auto-enable)');
    return {};
  }
  if (!(config.bookiesApiToken || config.bookiesApiKey)) {
    Logger.log('BookiesAPI skipped: missing BOOKIES_API_TOKEN/BOOKIES_API_KEY');
    return {};
  }
  if (!config.bookiesApiLogin) {
    Logger.log('BookiesAPI skipped: missing BOOKIES_API_LOGIN');
    return {};
  }
  var candidates = getBookiesApiCandidateMatches(matches, currentExactIndex, config);
  if (!candidates.length) {
    Logger.log('BookiesAPI backfill skipped: no candidate soccer matches');
    return {};
  }
  var lookupCandidatesByDate = getBookiesApiCandidateLookupMap(candidates, config);
  var uniqueBaseDates = {};
  candidates.forEach(function (match) {
    var baseDateKey = getDateKey(match.isoDate);
    if (baseDateKey && baseDateKey !== 'nodate') uniqueBaseDates[baseDateKey] = true;
  });
  Logger.log('BookiesAPI candidate soccer matches: ' + candidates.length + ', base days=' + Object.keys(uniqueBaseDates).join(',') + ', lookup days=' + Object.keys(lookupCandidatesByDate).join(','));
  var eventMap = {};
  var unmatchedSamples = 0;
  var baseUrl = String(config.bookiesApiBaseUrl || 'https://bookiesapi.com/api/get.php').replace(/\/$/, '');
  Object.keys(lookupCandidatesByDate).forEach(function (dateKey) {
    var pageLimit = Math.max(1, Number(config.bookiesApiMaxPagesPerDay || 10));
    for (var page = 1; page <= pageLimit; page++) {
      var url = baseUrl + '?' + buildQuery(getBookiesApiAuthParams(config, 'predatapage', {
        sport: normalizeBookiesApiSportName('soccer'),
        day: formatBookiesApiDay(dateKey),
        p: page
      }));
      var meta = fetchJsonMeta(url, {}, 'BookiesAPI predatapage ' + dateKey + ' p=' + page, { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds || 0);
      if (meta && meta.skippedByQuota) {
        Logger.log('BookiesAPI predatapage ' + dateKey + ' p=' + page + ' skipped by quota');
        break;
      }
      if (!meta || meta.status < 200 || meta.status >= 300 || !meta.data) break;
      var items = getBookiesApiEventList(meta.data);
      Logger.log('BookiesAPI predatapage ' + dateKey + ' p=' + page + ': raw=' + items.length);
      if (!items.length) break;
      if (page === 1) {
        items.slice(0, 3).forEach(function (sample, idx) {
          var ev = parseBookiesApiEvent(sample);
          if (!ev) return;
          Logger.log('BookiesAPI sample ' + dateKey + ' #' + (idx + 1) + ': ' + ev.home + ' vs ' + ev.away + ' @ ' + getDateKey(ev.isoDate));
        });
      }
      var matchedThisPage = 0;
      items.forEach(function (item) {
        var event = parseBookiesApiEvent(item);
        if (!event) return;
        var match = matchBookiesEventToCandidate(event, lookupCandidatesByDate[dateKey] || candidates);
        if (!match) {
          if (unmatchedSamples < 8) {
            unmatchedSamples += 1;
            Logger.log('BookiesAPI unmatched sample [' + dateKey + ']: ' + event.home + ' vs ' + event.away + ' @ ' + (event.isoDate || 'nodate') + ' league=' + (event.league || '')); 
          }
          return;
        }
        eventMap[match.matchKey] = { gameId: event.gameId, raw: item, event: event };
        matchedThisPage += 1;
      });
      Logger.log('BookiesAPI predatapage ' + dateKey + ' p=' + page + ': matched=' + matchedThisPage + ', cumulative=' + Object.keys(eventMap).length);
      if (items.length < Number(config.bookiesApiPageLimit || 50)) break;
    }
  });
  var result = {};
  var noOddsSamples = 0;
  var matchKeys = Object.keys(eventMap);
  for (var i = 0; i < matchKeys.length; i++) {
    var matchKey = matchKeys[i];
    var gameId = eventMap[matchKey].gameId;
    var url2 = baseUrl + '?' + buildQuery(getBookiesApiAuthParams(config, config.bookiesApiOddsTask || 'allodds', { game_id: gameId }));
    var meta2 = fetchJsonMeta(url2, {}, 'BookiesAPI ' + (config.bookiesApiOddsTask || 'allodds') + ' ' + gameId, { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds || 0);
    if (meta2 && meta2.skippedByQuota) {
      Logger.log('BookiesAPI ' + (config.bookiesApiOddsTask || 'allodds') + ' ' + gameId + ' skipped by quota');
      continue;
    }
    if (!meta2 || meta2.status < 200 || meta2.status >= 300 || !meta2.data) continue;
    var match = null;
    for (var m = 0; m < matches.length; m++) if (matches[m].matchKey === matchKey) { match = matches[m]; break; }
    if (!match) continue;
    var parsed = parseBookiesApiOddsPayload(meta2.data, match);
    if (!hasAnyOffers(parsed) && match.sport === 'soccer' && String(config.bookiesApiOddsTask || 'allodds').toLowerCase() === 'allodds') {
      var fallbackUrl = baseUrl + '?' + buildQuery(getBookiesApiAuthParams(config, 'odds', { game_id: gameId }));
      var fallbackMeta = fetchJsonMeta(fallbackUrl, {}, 'BookiesAPI odds fallback ' + gameId, { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds || 0);
      if (fallbackMeta && !fallbackMeta.skippedByQuota && fallbackMeta.status >= 200 && fallbackMeta.status < 300 && fallbackMeta.data) {
        var fallbackParsed = parseBookiesApiOddsPayload(fallbackMeta.data, match);
        if (hasAnyOffers(fallbackParsed)) parsed = fallbackParsed;
      }
    }
    if (!hasAnyOffers(parsed)) {
      if (noOddsSamples < 5) {
        noOddsSamples += 1;
        Logger.log('BookiesAPI no parsed offers for game_id=' + gameId + ' match=' + match.matchKey + ' body=' + String(meta2.text || '').slice(0, 220));
      }
      continue;
    }
    result[matchKey] = parsed;
  }
  Logger.log('BookiesAPI backfill parsed matches: ' + Object.keys(result).length + ' / mapped=' + Object.keys(eventMap).length);
  return result;
}

function getOddsSources(config, matches, theOddsFeed) {
  var sources = {
    theOdds: theOddsFeed || [],
    oddsApiIo: getOddsApiIoFeed(config, matches),
    bookiesApi: {},
    apiFootball: {}
  };

  var indexes = indexOddsSources(matches, sources, config);

  sources.bookiesApi = getBookiesApiFeed(config, matches, indexes.oddsIndexByExact || {});
  indexes = indexOddsSources(matches, sources, config);
  sources.oddsIndexByExact = indexes.oddsIndexByExact;
  sources.oddsIndexByLoose = indexes.oddsIndexByLoose;
  sources.matchesBySport = indexes.matchesBySport;

  var apiFootballDates = getApiFootballOddsDatesToFetch(config, matches, sources);
  if (apiFootballDates.length) {
    Logger.log('API-Football odds будут запрошены только по датам: ' + apiFootballDates.join(', '));
    sources.apiFootball = getApiFootballOdds(config, apiFootballDates);
    indexes = indexOddsSources(matches, sources, config);
    sources.oddsIndexByExact = indexes.oddsIndexByExact;
    sources.oddsIndexByLoose = indexes.oddsIndexByLoose;
    sources.matchesBySport = indexes.matchesBySport;
  } else {
    Logger.log('API-Football odds не потребовались: текущего покрытия достаточно');
  }

  Logger.log('Odds feeds loaded: TheOdds=' + (sources.theOdds ? sources.theOdds.length : 0) + ', OddsApiIo=' + Object.keys(sources.oddsApiIo || {}).length + ', BookiesApi=' + Object.keys(sources.bookiesApi || {}).length + ', ApiFootball=' + Object.keys(sources.apiFootball || {}).length);
  return sources;
}

/* ======================= CONSENSUS MODELS ======================= */
function normalizeProbabilityMap(map) {
  var out = {};
  Object.keys(map || {}).forEach(function (k) { out[k] = map[k]; });
  var probabilityKeys = ['home', 'draw', 'away', 'over', 'under', 'yes', 'no', 'homeDraw', 'awayDraw', 'homeAway'];
  var keys = probabilityKeys.filter(function (k) { return out[k] != null && !isNaN(Number(out[k])); });
  if (!keys.length) return out;
  var sum = keys.reduce(function (acc, k) { return acc + Number(out[k]); }, 0);
  if (sum > 99 && sum < 101) return out;
  if (!sum) return out;
  keys.forEach(function (k) { out[k] = Number(out[k]) * 100 / sum; });
  return out;
}

function uniqueBookmakersCount(offers) {
  var books = {};
  (offers || []).forEach(function (o) {
    var key = normalizeBookmakerName(o.bookmaker);
    if (key) books[key] = true;
  });
  return Object.keys(books).length;
}

function selectConsensusOffers(offers, config) {
  offers = offers || [];
  if (!offers.length) return offers;
  var lookup = makeBookmakerLookup(config.consensusBookmakers || []);
  if (!Object.keys(lookup).length) return offers;

  var filtered = offers.filter(function (o) {
    return lookup[normalizeBookmakerName(o.bookmaker)];
  });
  if (uniqueBookmakersCount(filtered) >= Math.max(1, config.minBooksForConsensus || 1)) {
    return filtered;
  }
  return offers;
}

function buildH2HConsensus(match, offers) {
  var byBook = {};
  offers.forEach(function (o) {
    var outcomeKey = getOutcomeKey(o.name, match);
    if (!outcomeKey || !o.bookmaker || !o.price || o.price <= 1) return;
    if (!byBook[o.bookmaker]) byBook[o.bookmaker] = {};
    if (!byBook[o.bookmaker][outcomeKey] || o.price > byBook[o.bookmaker][outcomeKey]) {
      byBook[o.bookmaker][outcomeKey] = o.price;
    }
  });
  var books = Object.keys(byBook);
  var homeList = [];
  var drawList = [];
  var awayList = [];
  books.forEach(function (book) {
    var row = byBook[book];
    var hasDraw = row.draw != null;
    if (row.home == null || row.away == null) return;
    var invHome = 1 / row.home;
    var invAway = 1 / row.away;
    var invDraw = hasDraw ? 1 / row.draw : 0;
    var sum = invHome + invAway + invDraw;
    if (!sum) return;
    homeList.push(invHome / sum * 100);
    awayList.push(invAway / sum * 100);
    if (hasDraw) drawList.push(invDraw / sum * 100);
  });
  if (!homeList.length || !awayList.length) return null;
  return normalizeProbabilityMap({
    home: avg(homeList),
    away: avg(awayList),
    draw: drawList.length ? avg(drawList) : null,
    sampleSize: homeList.length,
    confidence: clamp(48 + homeList.length * 8, 48, 88)
  });
}

function buildTotalsConsensus(offers, minBooks) {
  var byKey = {};
  offers.forEach(function (o) {
    var side = getTotalSelectionKey(o.name);
    if (!side || o.point == null || !o.bookmaker || !o.price || o.price <= 1) return;
    var key = pointKey(o.point) + '|' + o.bookmaker + '|' + String(o.marketSubType || '');
    if (!byKey[key]) byKey[key] = { point: round2(o.point), bookmaker: o.bookmaker, over: null, under: null };
    if (side === 'over' && (byKey[key].over == null || o.price > byKey[key].over)) byKey[key].over = o.price;
    if (side === 'under' && (byKey[key].under == null || o.price > byKey[key].under)) byKey[key].under = o.price;
  });

  var byPoint = {};
  Object.keys(byKey).forEach(function (key) {
    var row = byKey[key];
    if (row.over == null || row.under == null) return;
    var invOver = 1 / row.over;
    var invUnder = 1 / row.under;
    var sum = invOver + invUnder;
    if (!sum) return;
    var pKey = pointKey(row.point);
    if (!byPoint[pKey]) byPoint[pKey] = { point: row.point, over: [], under: [] };
    byPoint[pKey].over.push(invOver / sum * 100);
    byPoint[pKey].under.push(invUnder / sum * 100);
  });

  var result = {};
  Object.keys(byPoint).forEach(function (k) {
    var row = byPoint[k];
    if (row.over.length < (minBooks || 1)) return;
    result[k] = { point: row.point, over: avg(row.over), under: avg(row.under), sampleSize: row.over.length, confidence: clamp(46 + row.over.length * 8, 46, 88) };
  });
  return result;
}

function buildSpreadsConsensus(match, offers, minBooks) {
  var byKey = {};
  offers.forEach(function (o) {
    var side = getSpreadSelectionKey(o.name, match);
    if (!side || side === 'draw' || o.point == null || !o.bookmaker || !o.price || o.price <= 1) return;
    var canonicalLine = side === 'home' ? round2(o.point) : round2(-o.point);
    var key = pointKey(canonicalLine) + '|' + o.bookmaker + '|' + String(o.marketSubType || '');
    if (!byKey[key]) byKey[key] = { line: canonicalLine, bookmaker: o.bookmaker, home: null, away: null };
    if (side === 'home' && (byKey[key].home == null || o.price > byKey[key].home)) byKey[key].home = o.price;
    if (side === 'away' && (byKey[key].away == null || o.price > byKey[key].away)) byKey[key].away = o.price;
  });

  var byLine = {};
  Object.keys(byKey).forEach(function (key) {
    var row = byKey[key];
    if (row.home == null || row.away == null) return;
    var invHome = 1 / row.home;
    var invAway = 1 / row.away;
    var sum = invHome + invAway;
    if (!sum) return;
    var lKey = pointKey(row.line);
    if (!byLine[lKey]) byLine[lKey] = { line: row.line, home: [], away: [] };
    byLine[lKey].home.push(invHome / sum * 100);
    byLine[lKey].away.push(invAway / sum * 100);
  });

  var result = {};
  Object.keys(byLine).forEach(function (k) {
    var row = byLine[k];
    if (row.home.length < (minBooks || 1)) return;
    result[k] = { line: row.line, home: avg(row.home), away: avg(row.away), sampleSize: row.home.length, confidence: clamp(46 + row.home.length * 8, 46, 88) };
  });
  return result;
}


function buildTeamTotalsConsensus(match, offers, minBooks) {
  var byKey = {};
  offers.forEach(function (o) {
    var side = getTeamTotalSide(o, match);
    var sel = getTeamTotalSelectionKey(o.name);
    if (!side || !sel || o.point == null || !o.bookmaker || !o.price || o.price <= 1) return;
    var key = buildTeamTotalModelKey(side, o.point) + '|' + normalizeBookmakerName(o.bookmaker) + '|' + String(o.marketSubType || '');
    if (!byKey[key]) byKey[key] = { side: side, point: round2(o.point), bookmaker: o.bookmaker, over: null, under: null };
    if (sel === 'over' && (byKey[key].over == null || o.price > byKey[key].over)) byKey[key].over = o.price;
    if (sel === 'under' && (byKey[key].under == null || o.price > byKey[key].under)) byKey[key].under = o.price;
  });

  var grouped = {};
  Object.keys(byKey).forEach(function (key) {
    var row = byKey[key];
    if (row.over == null || row.under == null) return;
    var invOver = 1 / row.over;
    var invUnder = 1 / row.under;
    var sum = invOver + invUnder;
    if (!sum) return;
    var gKey = buildTeamTotalModelKey(row.side, row.point);
    if (!grouped[gKey]) grouped[gKey] = { side: row.side, point: row.point, over: [], under: [] };
    grouped[gKey].over.push(invOver / sum * 100);
    grouped[gKey].under.push(invUnder / sum * 100);
  });

  var result = {};
  Object.keys(grouped).forEach(function (k) {
    var row = grouped[k];
    if (row.over.length < (minBooks || 1)) return;
    result[k] = {
      side: row.side,
      point: row.point,
      over: avg(row.over),
      under: avg(row.under),
      sampleSize: row.over.length,
      confidence: clamp(46 + row.over.length * 8, 46, 88)
    };
  });
  return result;
}

function poissonPmf(lambda, k) {
  lambda = Math.max(0.05, Number(lambda || 0));
  k = Math.max(0, Math.floor(k || 0));
  var fact = 1;
  for (var i = 2; i <= k; i++) fact *= i;
  return Math.exp(-lambda) * Math.pow(lambda, k) / fact;
}

function buildPoissonOutcomeModel(homeLambda, awayLambda) {
  var maxGoals = 8;
  var home = 0;
  var draw = 0;
  var away = 0;
  var btts = 0;
  var over25 = 0;
  for (var i = 0; i <= maxGoals; i++) {
    var pHome = poissonPmf(homeLambda, i);
    for (var j = 0; j <= maxGoals; j++) {
      var pAway = poissonPmf(awayLambda, j);
      var p = pHome * pAway;
      if (i > j) home += p;
      else if (i === j) draw += p;
      else away += p;
      if (i > 0 && j > 0) btts += p;
      if (i + j >= 3) over25 += p;
    }
  }
  var normalized = normalizeProbabilityMap({ home: home * 100, draw: draw * 100, away: away * 100 });
  normalized.over25 = clamp(over25 * 100, 0, 100);
  normalized.bttsYes = clamp(btts * 100, 0, 100);
  return normalized;
}

function getOverProbabilityFromLambda(lambda, line) {
  if (lambda == null || line == null) return null;
  var threshold = Math.floor(Number(line)) + 1;
  var under = 0;
  for (var i = 0; i < threshold; i++) under += poissonPmf(lambda, i);
  return clamp((1 - under) * 100, 0, 100);
}

function buildTeamTotalsFromExpectedGoals(lines, homeLambda, awayLambda) {
  var result = {};
  var unique = {};
  (lines || []).forEach(function (item) {
    var side = item.side;
    var point = item.point;
    if (side == null || point == null) return;
    unique[buildTeamTotalModelKey(side, point)] = { side: side, point: point };
  });

  Object.keys(unique).forEach(function (k) {
    var side = unique[k].side;
    var point = unique[k].point;
    var lambda = side === 'home' ? homeLambda : awayLambda;
    var over = getOverProbabilityFromLambda(lambda, point);
    if (over == null) return;
    result[k] = {
      side: side,
      point: point,
      over: over,
      under: 100 - over,
      sampleSize: 0,
      confidence: 58
    };
  });
  return result;
}

function mergeTeamTotalsModels(primary, secondary) {
  var result = {};
  var keys = {};
  Object.keys(primary || {}).forEach(function (k) { keys[k] = true; });
  Object.keys(secondary || {}).forEach(function (k) { keys[k] = true; });
  Object.keys(keys).forEach(function (k) {
    var p = primary && primary[k] ? primary[k] : null;
    var s = secondary && secondary[k] ? secondary[k] : null;
    if (!p) { result[k] = s; return; }
    if (!s) { result[k] = p; return; }
    var weight = clamp((p.confidence || 60) / 100, 0.55, 0.8);
    result[k] = {
      side: p.side || s.side,
      point: p.point != null ? p.point : s.point,
      over: p.over * weight + s.over * (1 - weight),
      under: p.under * weight + s.under * (1 - weight),
      sampleSize: Math.max(p.sampleSize || 0, s.sampleSize || 0),
      confidence: clamp((p.confidence || 60) * 0.7 + (s.confidence || 55) * 0.3, 45, 90)
    };
  });
  return result;
}

function blendTwoWayProbabilities(primaryYes, secondaryYes, weightPrimary) {
  if (primaryYes == null && secondaryYes == null) return null;
  if (primaryYes == null) primaryYes = secondaryYes;
  if (secondaryYes == null) secondaryYes = primaryYes;
  var pYes = primaryYes * weightPrimary + secondaryYes * (1 - weightPrimary);
  return { yes: pYes, no: 100 - pYes };
}

function blendH2HModels(primary, secondary, confidence) {
  if (!primary && !secondary) return null;
  if (!primary) return secondary;
  if (!secondary) return primary;
  var weight = clamp((confidence || 65) / 100, 0.58, 0.82);
  return normalizeProbabilityMap({
    home: primary.home != null && secondary.home != null ? primary.home * weight + secondary.home * (1 - weight) : (primary.home != null ? primary.home : secondary.home),
    draw: primary.draw != null && secondary.draw != null ? primary.draw * weight + secondary.draw * (1 - weight) : (primary.draw != null ? primary.draw : secondary.draw),
    away: primary.away != null && secondary.away != null ? primary.away * weight + secondary.away * (1 - weight) : (primary.away != null ? primary.away : secondary.away),
    sampleSize: secondary.sampleSize || 0,
    confidence: clamp((confidence || 65) * 0.65 + (secondary.confidence || 55) * 0.35, 46, 92)
  });
}


function deriveSoccerContextModel(context) {
  if (!context) return null;
  var sstats = context.sstats || null;
  var hf = context.homeForm || null;
  var af = context.awayForm || null;
  var h2h = context.h2h || null;
  var hs = context.homeStats || null;
  var as = context.awayStats || null;
  var homeStanding = context.homeStanding || null;
  var awayStanding = context.awayStanding || null;
  var injuries = context.injuries || null;
  var lineups = context.lineups || null;

  if (!hf && !af && !sstats) return null;
  if (!hf) {
    hf = { sampleSize: 0, goalsFor: sstats && sstats.homeXg != null ? Number(sstats.homeXg) : 1.2, goalsAgainst: sstats && sstats.awayXg != null ? Number(sstats.awayXg) : 1.1, daysSinceLastMatch: null, congestionIndex: 0 };
  }
  if (!af) {
    af = { sampleSize: 0, goalsFor: sstats && sstats.awayXg != null ? Number(sstats.awayXg) : 1.0, goalsAgainst: sstats && sstats.homeXg != null ? Number(sstats.homeXg) : 1.2, daysSinceLastMatch: null, congestionIndex: 0 };
  }

  var homeAttack = avg([
    hf.goalsFor,
    hs && hs.goalsForHome != null ? hs.goalsForHome : null,
    hs && hs.formIndex != null ? 0.8 + hs.formIndex * 1.4 : null,
    sstats && sstats.homeXg != null ? Number(sstats.homeXg) : null
  ].filter(function (x) { return x != null; })) || hf.goalsFor;

  var awayAttack = avg([
    af.goalsFor,
    as && as.goalsForAway != null ? as.goalsForAway : null,
    as && as.formIndex != null ? 0.8 + as.formIndex * 1.4 : null,
    sstats && sstats.awayXg != null ? Number(sstats.awayXg) : null
  ].filter(function (x) { return x != null; })) || af.goalsFor;

  var homeDefenseWeakness = avg([
    hf.goalsAgainst,
    hs && hs.goalsAgainstHome != null ? hs.goalsAgainstHome : null,
    sstats && sstats.awayXg != null ? Number(sstats.awayXg) : null
  ].filter(function (x) { return x != null; })) || hf.goalsAgainst;

  var awayDefenseWeakness = avg([
    af.goalsAgainst,
    as && as.goalsAgainstAway != null ? as.goalsAgainstAway : null,
    sstats && sstats.homeXg != null ? Number(sstats.homeXg) : null
  ].filter(function (x) { return x != null; })) || af.goalsAgainst;

  var homeLambda = clamp(homeAttack * 0.56 + awayDefenseWeakness * 0.44 + 0.18, 0.25, 3.7);
  var awayLambda = clamp(awayAttack * 0.54 + homeDefenseWeakness * 0.46 - 0.02, 0.20, 3.4);

  if (homeStanding && awayStanding && homeStanding.rank != null && awayStanding.rank != null) {
    var rankEdge = clamp((awayStanding.rank - homeStanding.rank) / 12, -1.2, 1.2);
    homeLambda += rankEdge * 0.10;
    awayLambda -= rankEdge * 0.08;
  }

  var restEdge = 0;
  if (hf.daysSinceLastMatch != null && af.daysSinceLastMatch != null) {
    restEdge += clamp((hf.daysSinceLastMatch - af.daysSinceLastMatch) / 5, -0.8, 0.8);
  }
  if (hf.congestionIndex != null && af.congestionIndex != null) {
    restEdge += clamp((af.congestionIndex - hf.congestionIndex), -0.8, 0.8);
  }
  homeLambda += restEdge * 0.05;
  awayLambda -= restEdge * 0.05;

  if (sstats) {
    var sstatsWeight = clamp(context.sstatsWeight || 0.28, 0.10, 0.45);
    if (sstats.homeXg != null) homeLambda = homeLambda * (1 - sstatsWeight) + Number(sstats.homeXg) * sstatsWeight;
    if (sstats.awayXg != null) awayLambda = awayLambda * (1 - sstatsWeight) + Number(sstats.awayXg) * sstatsWeight;
  }

  if (injuries) {
    var homeAtkPenalty = (injuries.home.attack || 0) * 0.08 + (injuries.home.midfield || 0) * 0.03 + (injuries.home.doubtful || 0) * 0.02;
    var awayAtkPenalty = (injuries.away.attack || 0) * 0.08 + (injuries.away.midfield || 0) * 0.03 + (injuries.away.doubtful || 0) * 0.02;
    var homeDefPenalty = (injuries.home.defense || 0) * 0.05 + (injuries.home.goalkeeper || 0) * 0.07;
    var awayDefPenalty = (injuries.away.defense || 0) * 0.05 + (injuries.away.goalkeeper || 0) * 0.07;
    homeLambda = clamp(homeLambda - homeAtkPenalty + awayDefPenalty, 0.20, 3.8);
    awayLambda = clamp(awayLambda - awayAtkPenalty + homeDefPenalty, 0.15, 3.5);
  }

  var confidenceBoost = 0;
  if (lineups) {
    if (lineups.home && lineups.home.confirmed) confidenceBoost += 2;
    if (lineups.away && lineups.away.confirmed) confidenceBoost += 2;
  }
  if (sstats) {
    if (sstats.homeStarting >= 10) confidenceBoost += 1.5;
    if (sstats.awayStarting >= 10) confidenceBoost += 1.5;
    if (sstats.profits) {
      var roiValues = [sstats.profits.homeWinRoi, sstats.profits.awayWinRoi, sstats.profits.over25Roi, sstats.profits.under25Roi, sstats.profits.bttsYesRoi, sstats.profits.bttsNoRoi].filter(function (x) { return x != null; });
      if (roiValues.length) confidenceBoost += clamp(avg(roiValues) / 8, -1.5, 3.0);
    }
  }

  var poisson = buildPoissonOutcomeModel(homeLambda, awayLambda);
  var home = poisson.home;
  var draw = poisson.draw;
  var away = poisson.away;
  if (h2h && h2h.sampleSize >= 2) {
    home = home * 0.90 + (h2h.homeWinRate * 100) * 0.10;
    draw = draw * 0.90 + (h2h.drawRate * 100) * 0.10;
    away = away * 0.90 + (h2h.awayWinRate * 100) * 0.10;
  }

  var h2hModel = normalizeProbabilityMap({ home: home, draw: draw, away: away });
  if (sstats && sstats.homeWinProbability != null && sstats.awayWinProbability != null) {
    var sHome = Number(sstats.homeWinProbability || 0);
    var sAway = Number(sstats.awayWinProbability || 0);
    var sDraw = Math.max(0, 100 - sHome - sAway);
    var sstatsConfidence = clamp(58 + (sstats.profits ? 4 : 0) + (sstats.homeStarting >= 10 ? 2 : 0) + (sstats.awayStarting >= 10 ? 2 : 0), 56, 78);
    h2hModel = blendH2HModels(normalizeProbabilityMap({ home: sHome, draw: sDraw, away: sAway, confidence: sstatsConfidence }), h2hModel, sstatsConfidence);
  }
  return {
    h2h: h2hModel,
    over25: clamp(poisson.over25, 0, 100),
    bttsYes: clamp(poisson.bttsYes, 0, 100),
    homeGoalsExp: round2(homeLambda),
    awayGoalsExp: round2(awayLambda),
    confidence: clamp(52 + ((hf.sampleSize || 0) + (af.sampleSize || 0)) * 1.6 + (h2h ? h2h.sampleSize * 1.5 : 0) + confidenceBoost + (sstats ? 4 : 0), 50, 90),
    sstatsMarkets: sstats && sstats.oddsMarkets ? sstats.oddsMarkets : null
  };
}

function enrichTotalsConsensusFromSoccerContext(totalsConsensus, contextModel) {
  if (!contextModel || contextModel.over25 == null) return totalsConsensus;
  totalsConsensus = totalsConsensus || {};
  var key25 = pointKey(2.5);
  var base = totalsConsensus[key25];
  var weight = base ? 0.68 : 1.0;
  var over25 = base ? (base.over * weight + contextModel.over25 * (1 - weight)) : contextModel.over25;
  totalsConsensus[key25] = {
    point: 2.5,
    over: over25,
    under: 100 - over25,
    sampleSize: base ? base.sampleSize : 0,
    confidence: base ? clamp(base.confidence * 0.72 + contextModel.confidence * 0.28, 45, 90) : contextModel.confidence
  };
  return totalsConsensus;
}

function deriveSpreadsFromH2HModel(match, spreadsConsensus, h2hModel) {
  spreadsConsensus = spreadsConsensus || {};
  if (!h2hModel || match.sport !== 'soccer') return spreadsConsensus;

  var home = Number(h2hModel.home || 0);
  var draw = Number(h2hModel.draw || 0);
  var away = Number(h2hModel.away || 0);
  var conf = clamp((h2hModel.confidence || 60) - 5, 40, 85);

  if (!spreadsConsensus[pointKey(-0.5)]) {
    spreadsConsensus[pointKey(-0.5)] = {
      line: -0.5,
      home: home,
      away: draw + away,
      sampleSize: 0,
      confidence: conf
    };
  }
  if (!spreadsConsensus[pointKey(0.5)]) {
    spreadsConsensus[pointKey(0.5)] = {
      line: 0.5,
      home: home + draw,
      away: away,
      sampleSize: 0,
      confidence: conf
    };
  }

  return spreadsConsensus;
}

function deriveDnbModel(h2hModel) {
  if (!h2hModel || h2hModel.home == null || h2hModel.away == null) return null;
  var total = Number(h2hModel.home || 0) + Number(h2hModel.away || 0);
  if (!total) return null;
  return {
    home: Number(h2hModel.home || 0) * 100 / total,
    away: Number(h2hModel.away || 0) * 100 / total,
    confidence: clamp((h2hModel.confidence || 60) - 4, 42, 90)
  };
}

function deriveDoubleChanceModel(h2hModel) {
  if (!h2hModel) return null;
  return {
    '1X': clamp(Number(h2hModel.home || 0) + Number(h2hModel.draw || 0), 0, 100),
    'X2': clamp(Number(h2hModel.away || 0) + Number(h2hModel.draw || 0), 0, 100),
    '12': clamp(Number(h2hModel.home || 0) + Number(h2hModel.away || 0), 0, 100),
    confidence: clamp((h2hModel.confidence || 60) - 7, 38, 88)
  };
}

function deriveBttsModel(tip, contextModel) {
  var tipYes = tip && tip.bttsYes != null ? tip.bttsYes : null;
  var ctxYes = contextModel && contextModel.bttsYes != null ? contextModel.bttsYes : null;
  var blend = blendTwoWayProbabilities(tipYes, ctxYes, tipYes != null ? clamp((tip.confidence || 65) / 100, 0.58, 0.82) : 0.5);
  if (!blend) return null;
  return {
    yes: blend.yes,
    no: blend.no,
    confidence: clamp(((tip && tip.confidence) || 0) * 0.6 + ((contextModel && contextModel.confidence) || 55) * 0.4, 45, 88)
  };
}



function poissonPmf(lambda, k) {
  if (lambda == null || lambda < 0 || k < 0) return 0;
  var fact = 1;
  for (var i = 2; i <= k; i++) fact *= i;
  return Math.exp(-lambda) * Math.pow(lambda, k) / fact;
}

function getHomeCoverProbabilityFromLambdas(homeLambda, awayLambda, line) {
  homeLambda = Number(homeLambda || 0);
  awayLambda = Number(awayLambda || 0);
  line = Number(line);
  if (!isFinite(homeLambda) || !isFinite(awayLambda) || !isFinite(line)) return null;
  var maxGoals = 8;
  function singleLineProb(singleLine) {
    var win = 0;
    var push = 0;
    for (var h = 0; h <= maxGoals; h++) {
      var ph = poissonPmf(homeLambda, h);
      for (var a = 0; a <= maxGoals; a++) {
        var p = ph * poissonPmf(awayLambda, a);
        var diff = h - a + singleLine;
        if (diff > 0.000001) win += p;
        else if (Math.abs(diff) <= 0.000001) push += p;
      }
    }
    return win + push * 0.5;
  }
  var frac = Math.abs(line % 1);
  frac = Math.round(frac * 100) / 100;
  if (frac === 0.25) return (singleLineProb(line - 0.25) + singleLineProb(line + 0.25)) / 2;
  if (frac === 0.75) return (singleLineProb(line - 0.25) + singleLineProb(line + 0.25)) / 2;
  return singleLineProb(line);
}

function buildSoccerSpreadModelsFromExpectedGoals(match, offers, baseModels, homeLambda, awayLambda) {
  var result = Object.assign({}, baseModels || {});
  if (match.sport !== 'soccer' || homeLambda == null || awayLambda == null) return result;
  var requested = {};
  (offers || []).forEach(function (offer) {
    var side = getSpreadSelectionKey(offer.name, match);
    if (!side || side === 'draw' || offer.point == null) return;
    var canonicalLine = side === 'home' ? round2(offer.point) : round2(-offer.point);
    requested[pointKey(canonicalLine)] = canonicalLine;
  });
  [-1.5, -1.0, -0.75, -0.5, -0.25, 0.25, 0.5, 0.75, 1.0, 1.5].forEach(function (line) { requested[pointKey(line)] = line; });
  Object.keys(requested).forEach(function (k) {
    if (result[k]) return;
    var line = requested[k];
    var homeCover = getHomeCoverProbabilityFromLambdas(homeLambda, awayLambda, line);
    if (homeCover == null) return;
    result[k] = {
      line: line,
      home: clamp(homeCover, 0, 100),
      away: clamp(100 - homeCover, 0, 100),
      sampleSize: 0,
      confidence: clamp(56 + Math.max(0, 1.8 - Math.abs(line)) * 10, 48, 82)
    };
  });
  return result;
}

function buildTotalsFromExpectedGoals(offers, baseModels, totalLambda) {
  var result = Object.assign({}, baseModels || {});
  if (totalLambda == null) return result;
  var requested = {};
  (offers || []).forEach(function (offer) {
    if (offer.point != null) requested[pointKey(offer.point)] = round2(offer.point);
  });
  [1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5].forEach(function (line) { requested[pointKey(line)] = line; });
  Object.keys(requested).forEach(function (k) {
    if (result[k]) return;
    var line = requested[k];
    var overP = getOverProbabilityFromLambda(totalLambda, line);
    if (overP == null) return;
    result[k] = {
      point: line,
      over: clamp(overP, 0, 100),
      under: clamp(100 - overP, 0, 100),
      sampleSize: 0,
      confidence: clamp(58 + Math.max(0, 3.0 - Math.abs(line - 2.5)) * 8, 50, 84)
    };
  });
  return result;
}

function buildModelsForMatch(match, tip, odds, config, deepContext) {
  var contextualSstatsMarkets = deepContext && deepContext[match.matchKey] && deepContext[match.matchKey].sstats && deepContext[match.matchKey].sstats.oddsMarkets ? deepContext[match.matchKey].sstats.oddsMarkets : null;
  if (config.enableSstatsMarkets && contextualSstatsMarkets) attachOfferSource(odds, contextualSstatsMarkets, 'SStatsContext');

  var consensusH2HOffers = selectConsensusOffers(odds.h2h || [], config);
  var consensusTotalsOffers = selectConsensusOffers(odds.totals || [], config);
  var consensusSpreadsOffers = selectConsensusOffers(odds.spreads || [], config);
  var consensusTeamTotalOffers = selectConsensusOffers(odds.teamTotals || [], config);

  var h2hConsensus = buildH2HConsensus(match, consensusH2HOffers);
  var totalsConsensus = buildTotalsConsensus(consensusTotalsOffers, config.minBooksForConsensus);
  var spreadsConsensus = buildSpreadsConsensus(match, consensusSpreadsOffers, config.minBooksForConsensus);
  var teamTotalsConsensus = buildTeamTotalsConsensus(match, consensusTeamTotalOffers, config.minBooksForConsensus);
  var h2hModel = h2hConsensus;
  var contextModel = null;

  if (match.sport === 'soccer' && tip) {
    var external = normalizeProbabilityMap({ home: tip.home, draw: tip.draw, away: tip.away });
    h2hModel = blendH2HModels(external, h2hConsensus, tip.confidence);
  }

  if (match.sport === 'soccer' && deepContext && deepContext[match.matchKey]) {
    contextModel = deriveSoccerContextModel(deepContext[match.matchKey]);
    if (contextModel && contextModel.h2h) {
      h2hModel = blendH2HModels(contextModel.h2h, h2hModel, contextModel.confidence);
      totalsConsensus = enrichTotalsConsensusFromSoccerContext(totalsConsensus, contextModel);
      if (contextModel.homeGoalsExp != null && contextModel.awayGoalsExp != null) {
        totalsConsensus = buildTotalsFromExpectedGoals(odds.totals || [], totalsConsensus, Number(contextModel.homeGoalsExp) + Number(contextModel.awayGoalsExp));
        spreadsConsensus = buildSoccerSpreadModelsFromExpectedGoals(match, odds.spreads || [], spreadsConsensus, contextModel.homeGoalsExp, contextModel.awayGoalsExp);
      }
    }
  }

  if (match.sport === 'soccer' && tip && tip.over25 != null) {
    var key25 = pointKey(2.5);
    var blended25 = blendTwoWayProbabilities(tip.over25, totalsConsensus[key25] ? totalsConsensus[key25].over : null, clamp((tip.confidence || 65) / 100, 0.55, 0.8));
    if (blended25) {
      totalsConsensus[key25] = {
        point: 2.5,
        over: blended25.yes,
        under: blended25.no,
        sampleSize: totalsConsensus[key25] ? totalsConsensus[key25].sampleSize : 0,
        confidence: clamp((tip.confidence || 65) * 0.7 + ((totalsConsensus[key25] ? totalsConsensus[key25].confidence : 55) * 0.3), 45, 92)
      };
    }
  }

  spreadsConsensus = deriveSpreadsFromH2HModel(match, spreadsConsensus, h2hModel);

  if (match.sport === 'soccer' && config.enableTeamTotals && contextModel && contextModel.homeGoalsExp != null && contextModel.awayGoalsExp != null) {
    var requestedLines = [];
    (odds.teamTotals || []).forEach(function (offer) {
      var side = getTeamTotalSide(offer, match);
      if (side && offer.point != null) requestedLines.push({ side: side, point: offer.point });
    });
    if (!requestedLines.length) {
      requestedLines = [{ side: 'home', point: 0.5 }, { side: 'home', point: 1.5 }, { side: 'away', point: 0.5 }, { side: 'away', point: 1.5 }];
    }
    var derivedTeamTotals = buildTeamTotalsFromExpectedGoals(requestedLines, contextModel.homeGoalsExp, contextModel.awayGoalsExp);
    teamTotalsConsensus = mergeTeamTotalsModels(teamTotalsConsensus, derivedTeamTotals);
  }

  return {
    h2h: h2hModel,
    totals: totalsConsensus,
    spreads: spreadsConsensus,
    dnb: config.enableDerivedSoccerMarkets && match.sport === 'soccer' ? deriveDnbModel(h2hModel) : null,
    doubleChance: config.enableDerivedSoccerMarkets && match.sport === 'soccer' ? deriveDoubleChanceModel(h2hModel) : null,
    btts: match.sport === 'soccer' ? deriveBttsModel(tip, contextModel) : null,
    teamTotals: match.sport === 'soccer' && config.enableTeamTotals ? teamTotalsConsensus : {},
    __meta: {
      tip: tip,
      contextModel: contextModel,
      deepContext: deepContext && deepContext[match.matchKey] ? deepContext[match.matchKey] : null,
      h2hConsensus: h2hConsensus,
      totalsConsensus: totalsConsensus,
      spreadsConsensus: spreadsConsensus,
      teamTotalsConsensus: teamTotalsConsensus,
      familyBookCounts: {
        h2h: uniqueBookmakersCount(consensusH2HOffers),
        totals: uniqueBookmakersCount(consensusTotalsOffers),
        spreads: uniqueBookmakersCount(consensusSpreadsOffers),
        teamTotals: uniqueBookmakersCount(consensusTeamTotalOffers),
        dnb: uniqueBookmakersCount(odds.dnb || []),
        doubleChance: uniqueBookmakersCount(odds.doubleChance || []),
        btts: uniqueBookmakersCount(odds.btts || [])
      },
      familyOfferCounts: {
        h2h: (odds.h2h || []).length,
        totals: (odds.totals || []).length,
        spreads: (odds.spreads || []).length,
        dnb: (odds.dnb || []).length,
        doubleChance: (odds.doubleChance || []).length,
        btts: (odds.btts || []).length,
        teamTotals: (odds.teamTotals || []).length
      }
    }
  };
}


function isHighVarianceCompetition(match) {
  var league = String(match && match.league || '').toLowerCase();
  return /friendly|friendlies|youth|u19|u20|u21|u23|reserves|reserve|women|amic|qualification/.test(league);
}

function getSourceProbabilityForBet(match, family, offer, analysisMeta, sourceName) {
  if (!analysisMeta) return null;
  var tip = analysisMeta.tip || null;
  var contextModel = analysisMeta.contextModel || null;
  var deep = analysisMeta.deepContext || null;
  var sstats = deep && deep.sstats ? deep.sstats : null;
  var side, sel, totalLambda, line;

  if (family === 'h2h') {
    side = getOutcomeKey(offer.name, match);
    if (sourceName === 'tip' && tip && side) return tip[side] != null ? Number(tip[side]) : null;
    if (sourceName === 'context' && contextModel && contextModel.h2h && side) return contextModel.h2h[side] != null ? Number(contextModel.h2h[side]) : null;
    if (sourceName === 'sstats' && sstats && side) {
      if (side === 'home') return sstats.homeWinProbability != null ? Number(sstats.homeWinProbability) : null;
      if (side === 'away') return sstats.awayWinProbability != null ? Number(sstats.awayWinProbability) : null;
      if (side === 'draw' && sstats.homeWinProbability != null && sstats.awayWinProbability != null) return Math.max(0, 100 - Number(sstats.homeWinProbability) - Number(sstats.awayWinProbability));
    }
  }

  if (family === 'dnb') {
    side = getDnbSelectionKey(offer.name, match);
    if (!side) return null;
    if (sourceName === 'tip' && tip && tip[side] != null) {
      var tHome = Number(tip.home || 0), tAway = Number(tip.away || 0);
      var tTotal = tHome + tAway;
      if (tTotal > 0) return side === 'home' ? tHome * 100 / tTotal : tAway * 100 / tTotal;
    }
    if (sourceName === 'context' && contextModel && contextModel.h2h) {
      var cHome = Number(contextModel.h2h.home || 0), cAway = Number(contextModel.h2h.away || 0);
      var cTotal = cHome + cAway;
      if (cTotal > 0) return side === 'home' ? cHome * 100 / cTotal : cAway * 100 / cTotal;
    }
    if (sourceName === 'sstats' && sstats) {
      var sHome = Number(sstats.homeWinProbability || 0), sAway = Number(sstats.awayWinProbability || 0);
      var sTotal = sHome + sAway;
      if (sTotal > 0) return side === 'home' ? sHome * 100 / sTotal : sAway * 100 / sTotal;
    }
  }

  if (family === 'doubleChance') {
    sel = getDoubleChanceSelectionKey(offer.name, match);
    if (!sel) return null;
    var sourceModel = null;
    if (sourceName === 'tip' && tip) sourceModel = deriveDoubleChanceModel(normalizeProbabilityMap({ home: tip.home, draw: tip.draw, away: tip.away }));
    if (sourceName === 'context' && contextModel && contextModel.h2h) sourceModel = deriveDoubleChanceModel(contextModel.h2h);
    if (sourceName === 'sstats' && sstats && sstats.homeWinProbability != null && sstats.awayWinProbability != null) sourceModel = deriveDoubleChanceModel(normalizeProbabilityMap({ home: sstats.homeWinProbability, draw: 100 - Number(sstats.homeWinProbability) - Number(sstats.awayWinProbability), away: sstats.awayWinProbability }));
    return sourceModel && sourceModel[sel] != null ? Number(sourceModel[sel]) : null;
  }

  if (family === 'totals') {
    sel = getTotalSelectionKey(offer.name);
    line = toNumber(offer.point);
    if (!sel || line == null) return null;
    if (sourceName === 'tip' && tip && line === 2.5 && tip.over25 != null) return sel === 'over' ? Number(tip.over25) : 100 - Number(tip.over25);
    if (sourceName === 'context' && contextModel) {
      if (line === 2.5 && contextModel.over25 != null) return sel === 'over' ? Number(contextModel.over25) : 100 - Number(contextModel.over25);
      if (contextModel.homeGoalsExp != null && contextModel.awayGoalsExp != null) {
        totalLambda = Number(contextModel.homeGoalsExp) + Number(contextModel.awayGoalsExp);
        var overP = getOverProbabilityFromLambda(totalLambda, line);
        return overP == null ? null : (sel === 'over' ? overP : 100 - overP);
      }
    }
    if (sourceName === 'sstats' && sstats && sstats.homeXg != null && sstats.awayXg != null) {
      totalLambda = Number(sstats.homeXg) + Number(sstats.awayXg);
      var overS = getOverProbabilityFromLambda(totalLambda, line);
      return overS == null ? null : (sel === 'over' ? overS : 100 - overS);
    }
  }

  if (family === 'btts') {
    sel = getBttsSelectionKey(offer.name);
    if (!sel) return null;
    if (sourceName === 'tip' && tip && tip.bttsYes != null) return sel === 'yes' ? Number(tip.bttsYes) : 100 - Number(tip.bttsYes);
    if (sourceName === 'context' && contextModel && contextModel.bttsYes != null) return sel === 'yes' ? Number(contextModel.bttsYes) : 100 - Number(contextModel.bttsYes);
    if (sourceName === 'sstats' && sstats && sstats.homeXg != null && sstats.awayXg != null) {
      var yesProb = (1 - Math.exp(-Number(sstats.homeXg))) * (1 - Math.exp(-Number(sstats.awayXg))) * 100;
      return sel === 'yes' ? yesProb : 100 - yesProb;
    }
  }

  if (family === 'teamTotals') {
    var teamSide = getTeamTotalSide(offer, match);
    sel = getTeamTotalSelectionKey(offer.name);
    line = toNumber(offer.point);
    if (!teamSide || !sel || line == null) return null;
    var lambda = null;
    if (sourceName === 'context' && contextModel) lambda = teamSide === 'home' ? contextModel.homeGoalsExp : contextModel.awayGoalsExp;
    if (sourceName === 'sstats' && sstats) lambda = teamSide === 'home' ? sstats.homeXg : sstats.awayXg;
    if (lambda != null) {
      var overTeam = getOverProbabilityFromLambda(lambda, line);
      return overTeam == null ? null : (sel === 'over' ? overTeam : 100 - overTeam);
    }
  }

  if (family === 'spreads' && match.sport === 'soccer') {
    side = getSpreadSelectionKey(offer.name, match);
    if (!side || offer.point == null) return null;
    var lineVal = side === 'home' ? round2(offer.point) : round2(-offer.point);
    var sModel = null;
    if (sourceName === 'tip' && tip) sModel = deriveSpreadsFromH2HModel(match, {}, normalizeProbabilityMap({ home: tip.home, draw: tip.draw, away: tip.away }));
    if (sourceName === 'context' && contextModel && contextModel.h2h) sModel = deriveSpreadsFromH2HModel(match, {}, contextModel.h2h);
    if (sourceName === 'sstats' && sstats && sstats.homeWinProbability != null && sstats.awayWinProbability != null) sModel = deriveSpreadsFromH2HModel(match, {}, normalizeProbabilityMap({ home: sstats.homeWinProbability, draw: 100 - Number(sstats.homeWinProbability) - Number(sstats.awayWinProbability), away: sstats.awayWinProbability }));
    var row = sModel && sModel[pointKey(lineVal)] ? sModel[pointKey(lineVal)] : null;
    if (!row) return null;
    return side === 'home' ? Number(row.home) : Number(row.away);
  }

  return null;
}

function buildBetDiagnostics(match, family, offer, probability, baseConfidence, analysisMeta, config) {
  analysisMeta = analysisMeta || {};
  var deep = analysisMeta.deepContext || null;
  var contextModel = analysisMeta.contextModel || null;
  var sstats = deep && deep.sstats ? deep.sstats : null;
  var books = analysisMeta.familyBookCounts && analysisMeta.familyBookCounts[family] ? Number(analysisMeta.familyBookCounts[family]) : 0;
  var familyOfferCount = analysisMeta.familyOfferCounts && analysisMeta.familyOfferCounts[family] ? Number(analysisMeta.familyOfferCounts[family]) : 0;
  var trustScore = Number(offer && offer.trustScore || getOfferTrustScore(offer, family, config));
  var sourceValues = [];
  var sourceNames = ['tip', 'context', 'sstats'];
  var reasons = [];
  sourceNames.forEach(function (src) {
    var val = getSourceProbabilityForBet(match, family, offer, analysisMeta, src);
    if (val != null && isFinite(val)) sourceValues.push({ source: src, value: Number(val) });
  });
  var sourceCount = sourceValues.length;

  if (books >= 3) reasons.push('рыночный консенсус подтверждён ' + books + ' букмекерами');
  else if (books === 2) reasons.push('есть подтверждение по рынку от 2 букмекеров');
  if ((trustScore || 0) >= 1.1) reasons.push('линия подтверждена сильным букмекером/источником');

  if (family === 'h2h' || family === 'dnb' || family === 'doubleChance') {
    var side = family === 'h2h' ? getOutcomeKey(offer.name, match) : (family === 'dnb' ? getDnbSelectionKey(offer.name, match) : getDoubleChanceSelectionKey(offer.name, match));
    if (deep && deep.homeForm && deep.awayForm && (side === 'home' || side === 'away')) {
      var homePpg = Number(deep.homeForm.pointsPerGame || 0), awayPpg = Number(deep.awayForm.pointsPerGame || 0);
      if (side === 'home' && homePpg - awayPpg >= 0.35) reasons.push('форма хозяев лучше: ' + round2(homePpg) + ' vs ' + round2(awayPpg) + ' очка за матч');
      if (side === 'away' && awayPpg - homePpg >= 0.35) reasons.push('форма гостей лучше: ' + round2(awayPpg) + ' vs ' + round2(homePpg) + ' очка за матч');
    }
    if (contextModel && contextModel.homeGoalsExp != null && contextModel.awayGoalsExp != null && (side === 'home' || side === 'away')) {
      var diff = Number(contextModel.homeGoalsExp) - Number(contextModel.awayGoalsExp);
      if (side === 'home' && diff >= 0.28) reasons.push('по модели xG хозяева сильнее: ' + round2(contextModel.homeGoalsExp) + ' vs ' + round2(contextModel.awayGoalsExp));
      if (side === 'away' && diff <= -0.28) reasons.push('по модели xG гости сильнее: ' + round2(contextModel.awayGoalsExp) + ' vs ' + round2(contextModel.homeGoalsExp));
    }
  }

  if (family === 'totals' || family === 'btts' || family === 'teamTotals') {
    if (contextModel && contextModel.homeGoalsExp != null && contextModel.awayGoalsExp != null) reasons.push('ожидаемая результативность ~' + round2(Number(contextModel.homeGoalsExp) + Number(contextModel.awayGoalsExp)) + ' гола');
    else if (sstats && sstats.homeXg != null && sstats.awayXg != null) reasons.push('SStats xG даёт ~' + round2(Number(sstats.homeXg) + Number(sstats.awayXg)) + ' гола');
  }

  if (deep && deep.injuries) {
    var ih = deep.injuries.home || {}, ia = deep.injuries.away || {};
    var awayDefIssues = (ia.defense || 0) + (ia.goalkeeper || 0);
    var homeDefIssues = (ih.defense || 0) + (ih.goalkeeper || 0);
    if ((family === 'h2h' || family === 'dnb') && awayDefIssues >= 2 && getOutcomeKey(offer.name, match) === 'home') reasons.push('у гостей есть кадровые потери в обороне');
    if ((family === 'h2h' || family === 'dnb') && homeDefIssues >= 2 && getOutcomeKey(offer.name, match) === 'away') reasons.push('у хозяев есть кадровые потери в обороне');
    if ((family === 'totals' || family === 'teamTotals' || family === 'btts') && (awayDefIssues >= 2 || homeDefIssues >= 2)) reasons.push('потери в обороне поддерживают голевой сценарий');
  }
  if (deep && deep.lineups) {
    if (deep.lineups.home && deep.lineups.home.confirmed) reasons.push('состав хозяев подтверждён');
    if (deep.lineups.away && deep.lineups.away.confirmed) reasons.push('состав гостей подтверждён');
  }

  var agreement = null;
  if (sourceValues.length >= 2) {
    var vals = sourceValues.map(function (x) { return x.value; });
    agreement = Math.max.apply(null, vals) - Math.min.apply(null, vals);
  }
  var baselinePrice = analysisMeta && analysisMeta.marketBaselinePrice != null ? Number(analysisMeta.marketBaselinePrice) : null;
  var priceDistancePct = calcOfferPriceDistancePct(offer && offer.price, baselinePrice);
  var tolerancePct = Number(config && config.outlierPriceTolerancePct || 5.5);
  var outlierPenalty = 0;
  if (priceDistancePct != null && priceDistancePct > tolerancePct) {
    outlierPenalty = Math.min(Number(config && config.outlierMaxPenalty || 10), (priceDistancePct - tolerancePct) * 0.8);
    reasons.push('линия заметно отклоняется от медианы рынка');
  }

  var marketConf = clamp(42 + books * 9 + Math.min(6, familyOfferCount) * 1.5 + (trustScore - 1) * 16, 40, 88);
  var evidenceConf = clamp(44 + Math.min(reasons.length, 6) * 5 + Math.min(sourceValues.length, 3) * 4, 40, 90);
  var agreementConf = agreement == null ? 52 : clamp(88 - agreement * 1.8, 40, 88);
  var contextConf = 48 + (deep && deep.sstats ? 6 : 0) + (deep && deep.lineups ? 4 : 0) + (deep && deep.injuries ? 3 : 0) + (deep && deep.homeStanding ? 2 : 0);
  var competitionAdj = isHighVarianceCompetition(match) ? -8 : (match && match.isExoticLeague ? -3 : 3);
  if (family === 'h2h' && isHighVarianceCompetition(match)) competitionAdj -= 4;
  if (family === 'totals' || family === 'spreads' || family === 'teamTotals' || family === 'btts') competitionAdj += 2;
  var adjustedConfidence = clamp(baseConfidence * 0.22 + marketConf * 0.24 + evidenceConf * 0.22 + agreementConf * 0.18 + contextConf * 0.14 + competitionAdj - outlierPenalty, 38, 90);
  if (agreement != null && agreement <= 6) adjustedConfidence += 2;
  var supportScore = reasons.length + (books >= 2 ? 1 : 0) + (sourceValues.length >= 2 ? 1 : 0) + Math.min(1.5, familyOfferCount / 6) + Math.max(-0.6, (trustScore - 1) * 2.4);
  if (outlierPenalty > 0) supportScore -= Math.min(1.5, outlierPenalty / 5);
  if (isHighVarianceCompetition(match)) {
    adjustedConfidence -= sourceValues.length >= 2 && deep && deep.sstats ? 2 : 8;
    if (reasons.length < 2) supportScore -= 1.5;
  }
  if (family === 'combo') adjustedConfidence -= 4;
  if (family === 'h2h' && !reasons.length && books < 2) adjustedConfidence -= 3;
  if ((family === 'totals' || family === 'spreads' || family === 'teamTotals' || family === 'btts') && books >= 2) adjustedConfidence += 2;
  adjustedConfidence = clamp(adjustedConfidence, 38, 90);

  var topReasons = reasons.slice(0, 5);
  if (!topReasons.length && books >= 2) topReasons.push('есть подтверждение по рынку от нескольких букмекеров');
  if (agreement != null && agreement <= 9) topReasons.unshift('источники модели хорошо согласованы между собой');
  var comment = topReasons.slice(0, 6).join('; ');
  return {
    confidence: adjustedConfidence,
    supportScore: supportScore,
    comment: comment,
    agreementSpread: agreement,
    sourceCount: sourceCount,
    books: books,
    trustScore: trustScore,
    baselinePrice: baselinePrice,
    priceDistancePct: priceDistancePct,
    outlierPenalty: outlierPenalty
  };
}


function buildFormMiniText(form, teamLabel) {
  if (!form) return '';
  var parts = [];
  if (form.pointsPerGame != null) parts.push(teamLabel + ' ' + formatNumber(form.pointsPerGame, 2) + ' очка/матч');
  if (form.goalsFor != null && form.goalsAgainst != null) parts.push('голы ' + formatNumber(form.goalsFor, 2) + ':' + formatNumber(form.goalsAgainst, 2));
  if (form.over25Rate != null) parts.push('ТБ2.5 ' + formatPercent(Number(form.over25Rate) * 100, 0));
  return parts.join(', ');
}

function buildStandingMiniText(homeStanding, awayStanding, match) {
  if (!homeStanding || !awayStanding) return '';
  if (homeStanding.rank == null || awayStanding.rank == null) return '';
  var homePts = homeStanding.points != null ? ' (' + formatNumber(homeStanding.points, 0) + ' очк.)' : '';
  var awayPts = awayStanding.points != null ? ' (' + formatNumber(awayStanding.points, 0) + ' очк.)' : '';
  return match.home + ' #' + formatNumber(homeStanding.rank, 0) + homePts + ' vs ' + match.away + ' #' + formatNumber(awayStanding.rank, 0) + awayPts;
}

function buildInjuriesMiniText(injuries, match) {
  if (!injuries) return '';
  var out = [];
  var home = injuries.home || {};
  var away = injuries.away || {};
  var homeMiss = Number(home.attack || 0) + Number(home.midfield || 0) + Number(home.defense || 0) + Number(home.goalkeeper || 0);
  var awayMiss = Number(away.attack || 0) + Number(away.midfield || 0) + Number(away.defense || 0) + Number(away.goalkeeper || 0);
  if (homeMiss > 0) out.push(match.home + ': ' + formatNumber(homeMiss, 0) + ' потерь');
  if (awayMiss > 0) out.push(match.away + ': ' + formatNumber(awayMiss, 0) + ' потерь');
  return out.join('; ');
}

function buildBetInsightPayload(match, family, offer, analysisMeta, betLike) {
  var payload = { headline: '', factors: [], narrative: '', predictionScore: '', predictedTotal: '', tables: '', form: '', injuries: '', marketReason: '' };
  if (!analysisMeta) return payload;
  var deep = analysisMeta.deepContext || null;
  var contextModel = analysisMeta.contextModel || null;
  var tip = analysisMeta.tip || null;
  var h2h = deep && deep.h2h ? deep.h2h : null;
  var homeForm = deep && deep.homeForm ? deep.homeForm : null;
  var awayForm = deep && deep.awayForm ? deep.awayForm : null;
  var homeStanding = deep && deep.homeStanding ? deep.homeStanding : null;
  var awayStanding = deep && deep.awayStanding ? deep.awayStanding : null;
  var injuries = deep && deep.injuries ? deep.injuries : null;
  var sstats = deep && deep.sstats ? deep.sstats : null;
  var predictedHome = contextModel && contextModel.homeGoalsExp != null ? Number(contextModel.homeGoalsExp) : null;
  var predictedAway = contextModel && contextModel.awayGoalsExp != null ? Number(contextModel.awayGoalsExp) : null;
  var totalExp = predictedHome != null && predictedAway != null ? predictedHome + predictedAway : null;
  if (predictedHome != null && predictedAway != null) {
    payload.predictionScore = formatNumber(predictedHome, 2) + ' : ' + formatNumber(predictedAway, 2);
    payload.predictedTotal = formatNumber(totalExp, 2);
  }
  payload.form = [buildFormMiniText(homeForm, match.home), buildFormMiniText(awayForm, match.away)].filter(Boolean).join(' | ');
  payload.tables = buildStandingMiniText(homeStanding, awayStanding, match);
  payload.injuries = buildInjuriesMiniText(injuries, match);

  if (homeForm && awayForm && homeForm.pointsPerGame != null && awayForm.pointsPerGame != null) {
    var diff = Number(homeForm.pointsPerGame) - Number(awayForm.pointsPerGame);
    if (diff >= 0.30) payload.factors.push(match.home + ' лучше по форме: ' + formatNumber(homeForm.pointsPerGame, 2) + ' vs ' + formatNumber(awayForm.pointsPerGame, 2) + ' очка/матч');
    else if (diff <= -0.30) payload.factors.push(match.away + ' лучше по форме: ' + formatNumber(awayForm.pointsPerGame, 2) + ' vs ' + formatNumber(homeForm.pointsPerGame, 2) + ' очка/матч');
  }
  if (contextModel && predictedHome != null && predictedAway != null) {
    payload.factors.push('модель ожидает xG ' + formatNumber(predictedHome, 2) + ' - ' + formatNumber(predictedAway, 2));
    if (totalExp != null) payload.factors.push('ожидаемый тотал около ' + formatNumber(totalExp, 2));
  }
  if (homeStanding && awayStanding && homeStanding.rank != null && awayStanding.rank != null) {
    payload.factors.push('таблица: ' + match.home + ' #' + formatNumber(homeStanding.rank, 0) + ', ' + match.away + ' #' + formatNumber(awayStanding.rank, 0));
  }
  if (h2h && h2h.sampleSize) {
    payload.factors.push('личные встречи: ' + formatNumber(h2h.sampleSize, 0) + ' матч., средний тотал ' + formatNumber(h2h.avgTotalGoals, 2));
  }
  if (tip && tip.home != null && tip.draw != null && tip.away != null) {
    payload.factors.push('внешний прогноз: 1=' + formatPercent(tip.home, 0) + ', X=' + formatPercent(tip.draw, 0) + ', 2=' + formatPercent(tip.away, 0));
  }
  if (sstats && sstats.homeXg != null && sstats.awayXg != null) {
    payload.factors.push('SStats xG: ' + formatNumber(sstats.homeXg, 2) + ' - ' + formatNumber(sstats.awayXg, 2));
  }
  if (payload.injuries) payload.factors.push('кадровая ситуация: ' + payload.injuries);

  if (family === 'h2h' || family === 'dnb' || family === 'doubleChance') {
    payload.headline = 'Прогноз на исход';
    payload.marketReason = 'перевес модели над линией букмекера идёт именно по исходу матча';
  } else if (family === 'totals') {
    payload.headline = 'Прогноз на тотал';
    payload.marketReason = 'ожидаемая результативность, темп и баланс атаки/обороны указывают именно на этот тотал';
  } else if (family === 'spreads') {
    payload.headline = 'Прогноз на фору';
    payload.marketReason = 'разница сил команд и ожидаемый сценарий матча лучше всего читаются через фору';
  } else if (family === 'btts') {
    payload.headline = 'Прогноз на обе забьют';
    payload.marketReason = 'ожидаемые голы и профиль команд в атаке и обороне поддерживают рынок BTTS';
  } else if (family === 'teamTotals') {
    payload.headline = 'Прогноз на индивидуальный тотал';
    payload.marketReason = 'командный xG, форма и уязвимости соперника лучше раскрываются через индивидуальный тотал';
  }

  var paragraphs = [];
  if (payload.tables) paragraphs.push('По таблице: ' + payload.tables + '.');
  if (payload.form) paragraphs.push('По форме: ' + payload.form + '.');
  if (predictedHome != null && predictedAway != null) paragraphs.push('По модели ожидаю структуру матча в районе ' + payload.predictionScore + ' по xG, то есть тотал около ' + payload.predictedTotal + '.');
  if (h2h && h2h.sampleSize) paragraphs.push('По личным встречам выборка даёт средний тотал ' + formatNumber(h2h.avgTotalGoals, 2) + '.');
  if (payload.injuries) paragraphs.push('По составам: ' + payload.injuries + '.');
  if (payload.marketReason) paragraphs.push('По рынку: ' + payload.marketReason + '.');
  payload.narrative = paragraphs.join(' ');
  payload.factors = payload.factors.slice(0, 7);
  return payload;
}

function shouldRejectBetByDiagnostics(family, diag, match) {
  if (!diag) return false;
  var minSupport = 1.25;
  if (family === 'combo') minSupport = 1.6;
  if (family === 'doubleChance') minSupport = 0.82;
  if (family === 'teamTotals' || family === 'totals' || family === 'btts' || family === 'spreads') minSupport = 1.10;
  if (isHighVarianceCompetition(match)) minSupport += 0.35;
  if ((family === 'totals' || family === 'spreads' || family === 'teamTotals' || family === 'btts')) {
    var weakMarket = ((diag.books || 0) < 2 && (diag.sourceCount || 0) < 2);
    if (weakMarket && (diag.trustScore || 1) < 1.02) return true;
  }
  if ((diag.outlierPenalty || 0) >= 7.5) return true;
  if ((diag.trustScore || 1) < 0.84 && (diag.books || 0) < 2) return true;
  return diag.supportScore < minSupport;
}

function sortAndLimitCandidateBets(list, limit) {
  list = (list || []).filter(Boolean);
  list.sort(function (a, b) {
    if ((b.score || 0) !== (a.score || 0)) return (b.score || 0) - (a.score || 0);
    if (b.evPercent !== a.evPercent) return b.evPercent - a.evPercent;
    if (b.valueGap !== a.valueGap) return b.valueGap - a.valueGap;
    return (b.confidence || 0) - (a.confidence || 0);
  });
  return list.slice(0, limit || 4);
}


/* ======================= OFFER DEDUP ======================= */
/* ======================= OFFER DEDUP ======================= */
function dedupeBestH2HOffers(match, offers) {
  var best = {};
  offers.forEach(function (o) {
    var outcomeKey = getOutcomeKey(o.name, match);
    if (!outcomeKey || !o.price || o.price <= 1) return;
    var key = outcomeKey + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

function dedupeBestTotalOffers(offers) {
  var best = {};
  offers.forEach(function (o) {
    var side = getTotalSelectionKey(o.name);
    if (!side || o.point == null || !o.price || o.price <= 1) return;
    var key = side + '|' + pointKey(o.point) + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

function dedupeBestSpreadOffers(match, offers) {
  var best = {};
  offers.forEach(function (o) {
    var side = getSpreadSelectionKey(o.name, match);
    if (!side || side === 'draw' || o.point == null || !o.price || o.price <= 1) return;
    var key = side + '|' + pointKey(o.point) + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

function dedupeBestDnbOffers(match, offers) {
  var best = {};
  offers.forEach(function (o) {
    var side = getDnbSelectionKey(o.name, match);
    if (!side || !o.price || o.price <= 1) return;
    var key = side + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

function dedupeBestDoubleChanceOffers(match, offers) {
  var best = {};
  offers.forEach(function (o) {
    var sel = getDoubleChanceSelectionKey(o.name, match);
    if (!sel || !o.price || o.price <= 1) return;
    var key = sel + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

function dedupeBestBttsOffers(offers) {
  var best = {};
  offers.forEach(function (o) {
    var sel = getBttsSelectionKey(o.name);
    if (!sel || !o.price || o.price <= 1) return;
    var key = sel + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

function dedupeBestTeamTotalOffers(match, offers) {
  var best = {};
  offers.forEach(function (o) {
    var side = getTeamTotalSide(o, match);
    var sel = getTeamTotalSelectionKey(o.name);
    if (!side || !sel || o.point == null || !o.price || o.price <= 1) return;
    var key = side + '|' + sel + '|' + pointKey(o.point) + '|' + String(o.marketSubType || '');
    if (!best[key] || o.price > best[key].price) best[key] = o;
  });
  return Object.keys(best).map(function (k) { return best[k]; });
}

/* ======================= BET EVALUATION ======================= */
function calcKellyStake(bankroll, odd, probabilityPct, kellyFraction, maxStakePct) {
  if (!bankroll || bankroll <= 0 || !odd || odd <= 1 || probabilityPct == null) return 0;
  var p = probabilityPct / 100;
  var q = 1 - p;
  var b = odd - 1;
  var fullKelly = ((b * p) - q) / b;
  if (!isFinite(fullKelly) || fullKelly <= 0) return 0;
  var fraction = Math.max(0, fullKelly * (kellyFraction || 0.25));
  if (maxStakePct != null) fraction = Math.min(fraction, maxStakePct);
  return round2(bankroll * fraction);
}

function getFamilyWeight(config, family) {
  if (family === 'h2h') return config.h2hScoreWeight || 0.92;
  if (family === 'totals') return config.totalsScoreWeight || 1.18;
  if (family === 'spreads') return config.spreadsScoreWeight || 1.15;
  if (family === 'dnb') return config.dnbScoreWeight || 0.98;
  if (family === 'doubleChance') return config.doubleChanceScoreWeight || 0.86;
  if (family === 'btts') return config.bttsScoreWeight || 1.10;
  if (family === 'teamTotals') return config.teamTotalsScoreWeight || 1.20;
  return 1;
}

function shrinkProbability(modelPct, impliedPct, confidence, config) {
  var conf = Number(confidence || 0);
  var k = config ? clamp((conf - 50) / 50, config.modelShrinkMin || 0.12, config.modelShrinkMax || 0.42) : clamp((conf - 50) / 50, 0.12, 0.42);
  return impliedPct + (modelPct - impliedPct) * k;
}


function getOddsTargetMultiplier(odd, config) {
  var price = Number(odd || 0);
  if (!price || price <= 1) return 0;
  var rejectMin = Number(config.targetOddsRejectMin || 1.42);
  var rejectMax = Number(config.targetOddsRejectMax || 4.20);
  if (price < rejectMin || price > rejectMax) return 0;
  var hardMin = Number(config.targetOddsHardMin || 1.60);
  var hardMax = Number(config.targetOddsHardMax || 3.20);
  var sweetMin = Number(config.targetOddsSweetMin || 2.0);
  var sweetMax = Number(config.targetOddsSweetMax || 2.5);
  var boost = Number(config.targetOddsScoreBoost || 0.18);
  if (price >= sweetMin && price <= sweetMax) return 1 + boost;
  var sweetMid = (sweetMin + sweetMax) / 2;
  var dist = Math.abs(price - sweetMid);
  var span = Math.max(0.35, Math.max(sweetMid - rejectMin, rejectMax - sweetMid));
  var base = clamp(1 + boost - (dist / span) * 0.32, 0.72, 1 + boost);
  if (price < hardMin || price > hardMax) base *= 0.86;
  return clamp(base, 0.62, 1 + boost);
}

function buildBetRecord(match, family, offer, probability, confidence, config, thresholds, analysisMeta) {
  if (probability == null || !offer.price || offer.price <= 1) return null;
  if (!isUsablePublishedLine_(match, family, offer.point)) return null;
  var diagnostics = buildBetDiagnostics(match, family, offer, probability, confidence, analysisMeta, config);
  if (shouldRejectBetByDiagnostics(family, diagnostics, match)) return null;
  confidence = diagnostics && diagnostics.confidence != null ? diagnostics.confidence : confidence;
  var impliedProbability = 100 / offer.price;
  var adjustedProbability = shrinkProbability(probability, impliedProbability, confidence, config);
  var valueGap = adjustedProbability - impliedProbability;
  var evPercent = (offer.price * (adjustedProbability / 100) - 1) * 100;
  var rawEvPercent = (offer.price * (probability / 100) - 1) * 100;
  var stake = calcKellyStake(config.bankroll, offer.price, adjustedProbability, config.kellyFraction, config.maxStakePct);
  var minEdge = thresholds && thresholds.minEdgePct != null ? thresholds.minEdgePct : config.minEdgePct;
  var minEv = thresholds && thresholds.minEvPct != null ? thresholds.minEvPct : config.minEvPct;
  var effectiveMinConfidence = config.minModelConfidence || 52;
  if (family === 'totals' || family === 'spreads' || family === 'teamTotals' || family === 'btts') {
    effectiveMinConfidence -= 4.8;
    minEdge -= 0.55;
    minEv -= 0.40;
  } else if (family === 'dnb' || family === 'doubleChance') {
    effectiveMinConfidence -= 2.8;
    minEdge -= 0.24;
    minEv -= 0.16;
  }
  if (diagnostics && diagnostics.trustScore != null) {
    if (diagnostics.trustScore >= 1.08) {
      effectiveMinConfidence -= 1;
      minEdge -= 0.10;
    } else if (diagnostics.trustScore <= 0.92) {
      effectiveMinConfidence += 2;
      minEdge += 0.25;
      minEv += 0.15;
    }
  }
  if (diagnostics && diagnostics.outlierPenalty) {
    effectiveMinConfidence += Math.min(4, diagnostics.outlierPenalty * 0.35);
    minEdge += Math.min(0.8, diagnostics.outlierPenalty * 0.08);
    minEv += Math.min(0.6, diagnostics.outlierPenalty * 0.06);
  }
  if (isHighVarianceCompetition(match)) {
    effectiveMinConfidence += 2;
    minEdge += 0.35;
    minEv += 0.25;
  }
  effectiveMinConfidence = clamp(effectiveMinConfidence, 47, 70);
  minEdge = Math.max(0.55, minEdge);
  minEv = Math.max(0.35, minEv);
  if ((confidence || 0) < effectiveMinConfidence) return null;
  if (valueGap < minEdge) return null;
  if (evPercent < minEv) return null;
  if (stake <= 0) return null;

  var score = evPercent * 0.54 + valueGap * 0.24 + (confidence || 0) * 0.12 + Math.min(8, (diagnostics && diagnostics.supportScore || 0)) * 0.07;
  score += Math.max(-3, Math.min(3, ((diagnostics && diagnostics.trustScore != null ? diagnostics.trustScore : 1) - 1) * 12));
  score -= Math.min(4, diagnostics && diagnostics.outlierPenalty ? diagnostics.outlierPenalty * 0.35 : 0);
  score = score * getFamilyWeight(config, family);
  var oddsTargetMultiplier = getOddsTargetMultiplier(offer.price, config);
  if (!oddsTargetMultiplier) return null;
  score = score * oddsTargetMultiplier;

  var insight = buildBetInsightPayload(match, family, offer, analysisMeta, { probability: probability, adjustedProbability: adjustedProbability, confidence: confidence });
  return {
    matchKey: match.matchKey,
    sport: getSportLabel(match.sport),
    sportKey: match.sport,
    date: match.date,
    league: match.league,
    match: match.home + ' vs ' + match.away,
    market: getReadableMarket(match, family, offer),
    marketFamily: family,
    marketSubType: offer.marketSubType || '',
    outcome: getReadableOutcome(match, family, offer),
    line: offer.point != null ? round2(offer.point) : '',
    odd: round2(offer.price),
    bookmaker: offer.bookmaker || 'Unknown',
    oddsSource: offer.sourceName || 'Unknown',
    trustScore: diagnostics && diagnostics.trustScore != null ? round2(diagnostics.trustScore) : round2(getOfferTrustScore(offer, family, config)),
    probability: round2(probability),
    adjustedProbability: round2(adjustedProbability),
    impliedProbability: round2(impliedProbability),
    valueGap: round2(valueGap),
    evPercent: round2(evPercent),
    rawEvPercent: round2(rawEvPercent),
    confidence: round2(confidence || 0),
    stake: round2(stake),
    stakePctBankroll: round2(config.bankroll ? (stake / config.bankroll) * 100 : 0),
    betType: 'single',
    comment: diagnostics && diagnostics.comment ? diagnostics.comment : '',
    score: round2(score),
    supportScore: diagnostics && diagnostics.supportScore != null ? round2(diagnostics.supportScore) : '',
    marketBaselineOdd: diagnostics && diagnostics.baselinePrice != null ? round2(diagnostics.baselinePrice) : '',
    priceDistancePct: diagnostics && diagnostics.priceDistancePct != null ? round2(diagnostics.priceDistancePct) : '',
    analysisBooks: diagnostics && diagnostics.books != null ? diagnostics.books : '',
    analysisSourceCount: diagnostics && diagnostics.sourceCount != null ? diagnostics.sourceCount : '',
    marketBaselineOdd: diagnostics && diagnostics.baselinePrice != null ? round2(diagnostics.baselinePrice) : '',
    priceDistancePct: diagnostics && diagnostics.priceDistancePct != null ? round2(diagnostics.priceDistancePct) : '',
    predictionScore: insight.predictionScore || '',
    predictedTotal: insight.predictedTotal || '',
    analysisHeadline: insight.headline || '',
    analysisFactors: insight.factors && insight.factors.length ? insight.factors.join(' | ') : '',
    analysisNarrative: insight.narrative || '',
    formSnapshot: insight.form || '',
    tableSnapshot: insight.tables || '',
    injuriesSnapshot: insight.injuries || ''
  };
}


function getReadableMarket(match, family, offer) {
  var subType = String(offer.marketSubType || '');
  if (family === 'combo') return 'Комбинированная ставка';
  if (family === 'h2h') {
    if (subType === 'regular_time_3way') return '1X2 (осн. время)';
    if (subType === 'regular_time') return 'Победа в основное время';
    if (subType === 'moneyline_ot') return 'Moneyline (с ОТ/буллитами)';
    return match.sport === 'soccer' ? '1X2 / Moneyline' : 'Moneyline';
  }
  if (family === 'totals') return subType === 'asian_totals' ? 'Азиатский тотал' : 'Тотал';
  if (family === 'spreads') return subType === 'asian_spreads' ? 'Азиатская фора' : (match.sport === 'icehockey' ? 'Пак-лайн' : 'Фора');
  if (family === 'dnb') return 'Draw No Bet';
  if (family === 'doubleChance') return 'Двойной шанс';
  if (family === 'btts') return 'Обе забьют';
  if (family === 'teamTotals') return 'Инд. тотал команды';
  return family;
}

function getReadableOutcome(match, family, offer) {
  if (family === 'combo') return (offer.parts || []).map(function (p) { return p.outcome; }).join(' + ');
  if (family === 'h2h') return sideLabel(getOutcomeKey(offer.name, match), match.home, match.away);
  if (family === 'totals') {
    var side = getTotalSelectionKey(offer.name);
    var totalPoint = round2(offer.point);
    if (totalPoint == null) return side === 'over' ? 'ТБ' : 'ТМ';
    return side === 'over' ? 'ТБ ' + totalPoint : 'ТМ ' + totalPoint;
  }
  if (family === 'spreads') {
    var sideKey = getSpreadSelectionKey(offer.name, match);
    var team = sideKey === 'home' ? match.home : match.away;
    var signedPoint = Number(offer.point) > 0 ? '+' + round2(offer.point) : String(round2(offer.point));
    return team + ' ' + signedPoint;
  }
  if (family === 'dnb') return sideLabel(getDnbSelectionKey(offer.name, match), match.home, match.away);
  if (family === 'doubleChance') {
    var sel = getDoubleChanceSelectionKey(offer.name, match);
    if (sel === '1X') return match.home + ' или ничья';
    if (sel === 'X2') return 'Ничья или ' + match.away;
    if (sel === '12') return 'Без ничьей';
  }
  if (family === 'btts') return getBttsSelectionKey(offer.name) === 'yes' ? 'Обе забьют: Да' : 'Обе забьют: Нет';
  if (family === 'teamTotals') {
    var tSide = getTeamTotalSide(offer, match);
    var team = tSide === 'home' ? match.home : match.away;
    var ttSel = getTeamTotalSelectionKey(offer.name);
    var teamTotalPoint = round2(offer.point);
    return team + ': ' + (ttSel === 'over' ? 'ТБ' : 'ТМ') + (teamTotalPoint == null ? '' : ' ' + teamTotalPoint);
  }
  return String(offer.name || '');
}

function pickBetterBet(current, candidate) {
  if (!candidate) return current;
  if (!current) return candidate;
  if ((candidate.score || 0) !== (current.score || 0)) return (candidate.score || 0) > (current.score || 0) ? candidate : current;
  if (candidate.evPercent !== current.evPercent) return candidate.evPercent > current.evPercent ? candidate : current;
  if (candidate.valueGap !== current.valueGap) return candidate.valueGap > current.valueGap ? candidate : current;
  return (candidate.confidence || 0) > (current.confidence || 0) ? candidate : current;
}

function buildOfferAnalysisMeta(match, analysisMeta, family, offers, targetOffer) {
  var meta = Object.assign({}, analysisMeta || {});
  var comparable = [];
  (offers || []).forEach(function (o) {
    if (!o || !o.price || o.price <= 1) return;
    if (family === 'h2h' && getOutcomeKey(o.name, match) !== getOutcomeKey(targetOffer.name, match)) return;
    if (family === 'dnb' && getDnbSelectionKey(o.name, match) !== getDnbSelectionKey(targetOffer.name, match)) return;
    if (family === 'doubleChance' && getDoubleChanceSelectionKey(o.name, match) !== getDoubleChanceSelectionKey(targetOffer.name, match)) return;
    if (family === 'btts' && getBttsSelectionKey(o.name) !== getBttsSelectionKey(targetOffer.name)) return;
    if (family === 'totals') {
      if (getTotalSelectionKey(o.name) !== getTotalSelectionKey(targetOffer.name)) return;
      if (pointKey(o.point) !== pointKey(targetOffer.point)) return;
    }
    if (family === 'spreads') {
      if (getSpreadSelectionKey(o.name, match) !== getSpreadSelectionKey(targetOffer.name, match)) return;
      if (pointKey(o.point) !== pointKey(targetOffer.point)) return;
    }
    if (family === 'teamTotals') {
      if (getTeamTotalSide(o, match) !== getTeamTotalSide(targetOffer, match)) return;
      if (getTeamTotalSelectionKey(o.name) !== getTeamTotalSelectionKey(targetOffer.name)) return;
      if (pointKey(o.point) !== pointKey(targetOffer.point)) return;
    }
    comparable.push(o);
  });
  var base = medianByOfferPrice(comparable.length ? comparable : offers);
  if (base != null) meta.marketBaselinePrice = base;
  return meta;
}

function getH2HProbabilityForOffer(match, offer, model) {
  if (!model) return null;
  var side = getOutcomeKey(offer.name, match);
  if (!side) return null;

  var subType = String(offer.marketSubType || '');
  if (subType === 'regular_time_3way') {
    return model[side] != null ? model[side] : null;
  }

  if (side === 'draw') return null;

  var home = Number(model.home || 0);
  var away = Number(model.away || 0);
  var total = home + away;
  if (total <= 0) return null;

  if (side === 'home') return home * 100 / total;
  if (side === 'away') return away * 100 / total;
  return null;
}

function evaluateH2HCandidates(match, offers, model, config, analysisMeta) {
  if (!model) return [];
  var out = [];
  dedupeBestH2HOffers(match, offers).forEach(function (offer) {
    var probability = getH2HProbabilityForOffer(match, offer, model);
    var bet = buildBetRecord(match, 'h2h', offer, probability, model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'h2h', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 5);
}

function evaluateH2H(match, offers, model, config, analysisMeta) {
  return evaluateH2HCandidates(match, offers, model, config, analysisMeta)[0] || null;
}

function evaluateTotalsCandidates(match, offers, models, config, analysisMeta) {
  var out = [];
  dedupeBestTotalOffers(offers).forEach(function (offer) {
    var side = getTotalSelectionKey(offer.name);
    var model = models[pointKey(offer.point)];
    if (!side || !model) return;
    var probability = side === 'over' ? model.over : model.under;
    var bet = buildBetRecord(match, 'totals', offer, probability, model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'totals', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 5);
}

function evaluateTotals(match, offers, models, config, analysisMeta) {
  return evaluateTotalsCandidates(match, offers, models, config, analysisMeta)[0] || null;
}

function evaluateSpreadsCandidates(match, offers, models, config, analysisMeta) {
  var out = [];
  dedupeBestSpreadOffers(match, offers).forEach(function (offer) {
    var side = getSpreadSelectionKey(offer.name, match);
    if (!side || side === 'draw') return;
    var canonicalLine = side === 'home' ? round2(offer.point) : round2(-offer.point);
    var model = models[pointKey(canonicalLine)];
    if (!model) return;
    var probability = side === 'home' ? model.home : model.away;
    var bet = buildBetRecord(match, 'spreads', offer, probability, model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'spreads', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 5);
}

function evaluateSpreads(match, offers, models, config, analysisMeta) {
  return evaluateSpreadsCandidates(match, offers, models, config, analysisMeta)[0] || null;
}

function evaluateDnbCandidates(match, offers, model, config, analysisMeta) {
  if (!model) return [];
  var out = [];
  dedupeBestDnbOffers(match, offers).forEach(function (offer) {
    var side = getDnbSelectionKey(offer.name, match);
    if (!side) return;
    var probability = side === 'home' ? model.home : model.away;
    var bet = buildBetRecord(match, 'dnb', offer, probability, model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'dnb', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 4);
}

function evaluateDnb(match, offers, model, config, analysisMeta) {
  return evaluateDnbCandidates(match, offers, model, config, analysisMeta)[0] || null;
}

function evaluateDoubleChanceCandidates(match, offers, model, config, analysisMeta) {
  if (!model) return [];
  var out = [];
  dedupeBestDoubleChanceOffers(match, offers).forEach(function (offer) {
    var sel = getDoubleChanceSelectionKey(offer.name, match);
    if (!sel || model[sel] == null) return;
    var bet = buildBetRecord(match, 'doubleChance', offer, model[sel], model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'doubleChance', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 4);
}

function evaluateDoubleChance(match, offers, model, config, analysisMeta) {
  return evaluateDoubleChanceCandidates(match, offers, model, config, analysisMeta)[0] || null;
}

function evaluateBttsCandidates(match, offers, model, config, analysisMeta) {
  if (!model) return [];
  var out = [];
  dedupeBestBttsOffers(offers).forEach(function (offer) {
    var sel = getBttsSelectionKey(offer.name);
    if (!sel || model[sel] == null) return;
    var bet = buildBetRecord(match, 'btts', offer, model[sel], model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'btts', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 4);
}

function evaluateBtts(match, offers, model, config, analysisMeta) {
  return evaluateBttsCandidates(match, offers, model, config, analysisMeta)[0] || null;
}

function evaluateTeamTotalsCandidates(match, offers, models, config, analysisMeta) {
  var out = [];
  dedupeBestTeamTotalOffers(match, offers).forEach(function (offer) {
    var side = getTeamTotalSide(offer, match);
    var sel = getTeamTotalSelectionKey(offer.name);
    var key = buildTeamTotalModelKey(side, offer.point);
    var model = models && models[key] ? models[key] : null;
    if (!side || !sel || !model) return;
    var probability = sel === 'over' ? model.over : model.under;
    var bet = buildBetRecord(match, 'teamTotals', offer, probability, model.confidence || 0, config, null, buildOfferAnalysisMeta(match, analysisMeta, 'teamTotals', offers, offer));
    if (bet) out.push(bet);
  });
  return sortAndLimitCandidateBets(out, 5);
}

function evaluateTeamTotals(match, offers, models, config, analysisMeta) {
  return evaluateTeamTotalsCandidates(match, offers, models, config, analysisMeta)[0] || null;
}

function makeComboOffer(parts, bookmaker) {
  var combinedOdd = parts.reduce(function (acc, p) { return acc * p.odd; }, 1);
  return { price: round2(combinedOdd), bookmaker: bookmaker, point: '', marketSubType: 'combo', parts: parts };
}

function inferComboProbability(match, selections, config) {
  if (!selections || selections.length < 2) return null;
  var base = selections.reduce(function (acc, s) { return acc * (s.adjustedProbability / 100); }, 1);
  var probabilityPct = base * 100;
  if (match.sport === 'soccer' && selections.length === 2) {
    var h2h = null;
    var totals = null;
    selections.forEach(function (s) {
      if (s.marketFamily === 'h2h') h2h = s;
      if ((s.marketFamily === 'totals' || s.marketFamily === 'teamTotals') && String(s.outcome || '').indexOf('ТБ') !== -1) totals = s;
    });
    if (h2h && totals) probabilityPct = Math.max(5, probabilityPct * 0.92);
  }
  return probabilityPct;
}

function buildComboBetRecord(match, selections, probabilityPct, confidence, config) {
  if (!selections || selections.length < 2) return null;
  var bookmaker = selections[0].bookmaker;
  var sameBook = selections.every(function (x) { return x.bookmaker === bookmaker; });
  if (!sameBook) return null;
  var offer = makeComboOffer(selections, bookmaker);
  var analysisMeta = { familyBookCounts: { combo: 1 } };
  var bet = buildBetRecord(match, 'combo', offer, probabilityPct, confidence, config, {
    minEdgePct: config.comboMinEdgePct,
    minEvPct: config.comboMinEvPct
  }, analysisMeta);
  if (!bet) return null;
  bet.market = selections.map(function (x) { return x.market; }).join(' + ');
  bet.outcome = selections.map(function (x) { return x.outcome; }).join(' + ');
  bet.betType = 'combo';
  bet.comment = 'Комбо из одного букмекера: ' + selections.map(function (x) { return x.outcome; }).join(' + ');
  return bet;
}

function evaluateComboBets(match, config, candidateGroups) {
  var out = [];
  var byBook = {};
  (candidateGroups || []).forEach(function (group) {
    (group || []).slice(0, 5).forEach(function (bet) {
      if (!bet || bet.betType === 'combo' || !bet.bookmaker) return;
      var book = String(bet.bookmaker);
      if (!byBook[book]) byBook[book] = {};
      if (!byBook[book][bet.marketFamily]) byBook[book][bet.marketFamily] = [];
      byBook[book][bet.marketFamily].push(bet);
    });
  });

  Object.keys(byBook).forEach(function (book) {
    var familyPools = byBook[book];
    var pool = [];
    Object.keys(familyPools).forEach(function (family) {
      familyPools[family].sort(function (a, b) {
        if ((b.score || 0) !== (a.score || 0)) return (b.score || 0) - (a.score || 0);
        return (b.evPercent || 0) - (a.evPercent || 0);
      });
      familyPools[family].slice(0, 2).forEach(function (bet) { pool.push(bet); });
    });

    if (pool.length < 2) return;

    for (var i = 0; i < pool.length; i++) {
      for (var j = i + 1; j < pool.length; j++) {
        var a = pool[i];
        var b = pool[j];
        if (!a || !b) continue;
        if (a.marketFamily === b.marketFamily) continue;
        var p = inferComboProbability(match, [a, b], config);
        var c = Math.min(a.confidence || 0, b.confidence || 0);
        var combo = buildComboBetRecord(match, [a, b], p, c, config);
        if (combo) out.push(combo);
      }
    }

    if (config.comboMaxSelections >= 3 && pool.length >= 3) {
      for (var x = 0; x < pool.length; x++) {
        for (var y = x + 1; y < pool.length; y++) {
          for (var z = y + 1; z < pool.length; z++) {
            var p1 = pool[x], p2 = pool[y], p3 = pool[z];
            if (!p1 || !p2 || !p3) continue;
            var fam = {};
            fam[p1.marketFamily] = true; fam[p2.marketFamily] = true; fam[p3.marketFamily] = true;
            if (Object.keys(fam).length < 3) continue;
            var prob = inferComboProbability(match, [p1, p2, p3], config);
            var conf = Math.min(p1.confidence || 0, p2.confidence || 0, p3.confidence || 0);
            var combo3 = buildComboBetRecord(match, [p1, p2, p3], prob, conf, config);
            if (combo3) out.push(combo3);
          }
        }
      }
    }
  });

  return sortAndLimitCandidateBets(out, 4);
}

function isExoticCandidateAllowed(match, config, odds) {
  if (!match || !match.isExoticLeague) return true;
  var books = countUniqueBooksAcrossOffers(odds || createEmptyMarkets());
  return books >= Math.max(2, config.exoticMaxBookmakersThreshold || 1);
}

function calculateValueBets(matches, tips, oddsSources, config, deepContext) {
  var valueBets = [];
  var noOddsCount = 0;

  matches.forEach(function (match) {
    var odds = getCombinedOddsForMatch(match, oddsSources, config);
    if (!hasAnyOffers(odds)) {
      if (noOddsCount < MAX_MATCH_DEBUG_NO_ODDS) Logger.log('Нет коэффициентов для матча: ' + match.matchKey);
      noOddsCount += 1;
      return;
    }

    if (!isExoticCandidateAllowed(match, config, odds)) return;

    var tip = tips[match.matchKey] || null;
    var models = buildModelsForMatch(match, tip, odds, config, deepContext);
    var analysisMeta = models.__meta || {};
    var h2hCandidates = evaluateH2HCandidates(match, odds.h2h || [], models.h2h, config, analysisMeta);
    var totalsCandidates = evaluateTotalsCandidates(match, odds.totals || [], models.totals || {}, config, analysisMeta);
    var spreadsCandidates = evaluateSpreadsCandidates(match, odds.spreads || [], models.spreads || {}, config, analysisMeta);
    var dnbCandidates = evaluateDnbCandidates(match, odds.dnb || [], models.dnb, config, analysisMeta);
    var dcCandidates = evaluateDoubleChanceCandidates(match, odds.doubleChance || [], models.doubleChance, config, analysisMeta);
    var bttsCandidates = evaluateBttsCandidates(match, odds.btts || [], models.btts, config, analysisMeta);
    var teamTotalCandidates = evaluateTeamTotalsCandidates(match, odds.teamTotals || [], models.teamTotals || {}, config, analysisMeta);

    var bestH2H = h2hCandidates[0] || null;
    var bestTotals = totalsCandidates[0] || null;
    var bestSpreads = spreadsCandidates[0] || null;
    var bestDnb = dnbCandidates[0] || null;
    var bestDoubleChance = dcCandidates[0] || null;
    var bestBtts = bttsCandidates[0] || null;
    var bestTeamTotals = teamTotalCandidates[0] || null;

    if (config.diversifySinglesByMarket) {
      [bestH2H, bestTotals, bestSpreads, bestDnb, bestDoubleChance, bestBtts, bestTeamTotals].forEach(function (bet) {
        if (bet) valueBets.push(bet);
      });
    } else {
      var bestSingle = null;
      [bestH2H, bestTotals, bestSpreads, bestDnb, bestDoubleChance, bestBtts, bestTeamTotals].forEach(function (bet) {
        bestSingle = pickBetterBet(bestSingle, bet);
      });
      if (bestSingle) valueBets.push(bestSingle);
    }

    if (config.allowComboBets && match.sport === 'soccer') {
      evaluateComboBets(match, config, [h2hCandidates, totalsCandidates, spreadsCandidates, dnbCandidates, dcCandidates, bttsCandidates, teamTotalCandidates]).forEach(function (bet) {
        valueBets.push(bet);
      });
    }
  });

  valueBets.sort(function (a, b) {
    if ((b.score || 0) !== (a.score || 0)) return (b.score || 0) - (a.score || 0);
    if (b.evPercent !== a.evPercent) return b.evPercent - a.evPercent;
    if (b.valueGap !== a.valueGap) return b.valueGap - a.valueGap;
    return (b.confidence || 0) - (a.confidence || 0);
  });
  var familyBreakdown = {};
  valueBets.forEach(function (bet) {
    var key = bet.marketFamily || 'other';
    familyBreakdown[key] = (familyBreakdown[key] || 0) + 1;
  });
  Logger.log('Найдено валуйных ставок: ' + valueBets.length + '; breakdown=' + JSON.stringify(familyBreakdown));
  return valueBets;
}

/* ======================= OUTPUTS ======================= */
function getSheetHeaders() {
  return [
    'Вид спорта', 'Дата матча', 'Лига', 'Матч', 'Рынок', 'Исход', 'Линия', 'Коэффициент', 'БК',
    'Источник коэффициента', 'Вероятность модели %', 'Скорр. вероятность %', 'Импл. вероятность %', 'Value gap %', 'EV %', 'Raw EV %', 'Confidence',
    'Рекомендуемая ставка', '% от банка', 'Тип ставки', 'Категория', 'Score', 'Экзотическая лига', 'Комментарий'
  ];
}

function ensureSpreadsheet(config) {
  var props = PropertiesService.getScriptProperties();
  var ss = null;
  if (config.sheetId) {
    try { ss = SpreadsheetApp.openById(config.sheetId); } catch (e) { Logger.log('Не удалось открыть таблицу по SHEET_ID: ' + e); }
  }
  if (!ss) {
    var autoId = props.getProperty('AUTO_CREATED_SHEET_ID');
    if (autoId) {
      try { ss = SpreadsheetApp.openById(autoId); } catch (e2) { Logger.log('AUTO_CREATED_SHEET_ID больше невалиден: ' + e2); }
    }
  }
  if (!ss) {
    ss = SpreadsheetApp.create('Value Bets - Auto');
    props.setProperty('AUTO_CREATED_SHEET_ID', ss.getId());
    Logger.log('Создана новая таблица: ' + ss.getUrl());
  }
  return ss;
}

function ensureSheetStructure(config) {
  var ss = ensureSpreadsheet(config);
  var sheet = ss.getSheetByName(config.sheetName);
  if (!sheet) sheet = ss.insertSheet(config.sheetName);
  var headers = getSheetHeaders();
  sheet.clearContents();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  return { ss: ss, sheet: sheet, headers: headers };
}

function logToSheet(valueBets, config) {
  try {
    var info = ensureSheetStructure(config);
    var ss = info.ss;
    var sheet = info.sheet;
    valueBets.forEach(function (v) {
      sheet.appendRow([
        v.sport,
        Utilities.formatDate(v.date, config.timezone, 'dd.MM.yyyy HH:mm'),
        v.league,
        v.match,
        v.market,
        v.outcome,
        v.line,
        v.odd,
        v.bookmaker,
        v.oddsSource || 'Unknown',
        v.probability,
        v.adjustedProbability,
        v.impliedProbability,
        v.valueGap,
        v.evPercent,
        v.rawEvPercent,
        v.confidence,
        v.stake,
        v.stakePctBankroll,
        v.betType || 'single',
        getTelegramCategoryKey(v),
        v.score || '',
        isProbablyExoticLeague({ league: v.league, sport: v.sportKey }, config) ? 'yes' : 'no',
        v.comment || ''
      ]);
    });
    Logger.log('В таблицу записано строк: ' + valueBets.length + ', spreadsheetId=' + ss.getId());
    return true;
  } catch (e) {
    Logger.log('Ошибка записи в таблицу: ' + (e && e.stack ? e.stack : e));
    return false;
  }
}

function getTelegramCategoryKey(bet) {
  if (bet.betType === 'combo' || bet.marketFamily === 'combo') return 'combo';
  if (bet.marketFamily === 'h2h') return 'h2h';
  if (bet.marketFamily === 'totals') return 'totals';
  if (bet.marketFamily === 'spreads') return 'spreads';
  if (bet.marketFamily === 'dnb') return 'dnb';
  if (bet.marketFamily === 'doubleChance') return 'double_chance';
  if (bet.marketFamily === 'btts') return 'btts';
  if (bet.marketFamily === 'teamTotals') return 'team_totals';
  return 'other';
}

function filterTelegramBets(valueBets, config) {
  var maxPerMatch = Number(config.telegramMaxPerMatch || 1);
  var selected = [];
  var perMatchCount = {};
  var categorySeen = {};
  var familyCounts = {};
  valueBets.forEach(function (bet) {
    var category = getTelegramCategoryKey(bet);
    var catMatchKey = bet.matchKey + '|' + category;
    if (categorySeen[catMatchKey]) return;
    var count = perMatchCount[bet.matchKey] || 0;
    if (count >= Math.max(maxPerMatch, 5)) return;
    if (category === 'h2h' && (familyCounts.h2h || 0) >= 2) return;
    selected.push(bet);
    perMatchCount[bet.matchKey] = count + 1;
    categorySeen[catMatchKey] = true;
    familyCounts[category] = (familyCounts[category] || 0) + 1;
  });
  return selected;
}

function escapeHtml(text) {
  return String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function getEdgeDescription(valueGap, evPercent, confidence) {
  if (valueGap >= 8 && evPercent >= 12) return 'Сильный вариант: модель видит заметный перевес над линией букмекера.';
  if (valueGap >= 5 && evPercent >= 7) return 'Хороший вариант: есть ощутимое преимущество по сравнению с коэффициентом.';
  if (valueGap >= 3 && evPercent >= 3) return 'Умеренный перевес: ставка выглядит интересно, но без большого запаса.';
  if (confidence >= 75) return 'Модель достаточно уверена в прогнозе, но перевес над линией умеренный.';
  return 'Небольшой перевес над линией букмекера.';
}

function formatNumber(value, decimals) {
  if (value === null || value === undefined || value === '') return '-';
  var num = Number(value);
  if (!isFinite(num)) return String(value);
  var digits = decimals == null ? 2 : decimals;
  return num.toFixed(digits).replace(/\.?0+$/, '').replace('.', ',');
}

function formatPercent(value, decimals) {
  if (value === null || value === undefined || value === '') return '-';
  var num = Number(value);
  if (!isFinite(num)) return String(value);
  var digits = decimals == null ? 1 : decimals;
  return num.toFixed(digits).replace(/\.?0+$/, '').replace('.', ',') + '%';
}

function formatTelegramBetHuman(v, number, timezone) {
  var matchDate = Utilities.formatDate(v.date, timezone, 'dd.MM.yyyy HH:mm');
  var parts = [];
  parts.push('⚽ <b>' + escapeHtml(v.sport) + '. ' + escapeHtml(v.league) + '</b>');
  parts.push('<b>' + number + '. ' + escapeHtml(v.match.replace(' vs ', ' - ')) + '</b>');
  parts.push('');
  parts.push('🎯 <b>' + escapeHtml(v.market) + '</b>');
  parts.push('Прогноз: <b>' + escapeHtml(v.outcome) + '</b>' + (v.line !== '' && v.line !== null && v.line !== undefined ? ' <b>(' + escapeHtml(String(v.line)) + ')</b>' : ''));
  parts.push('💸 Коэффициент: <b>' + formatNumber(v.odd, 2) + '</b> | EV: <b>' + formatPercent(v.evPercent, 2) + '</b> | Edge: <b>' + formatPercent(v.valueGap, 2) + '</b>');
  parts.push('📊 Модель: <b>' + formatPercent(v.probability, 1) + '</b> | скорр.: <b>' + formatPercent(v.adjustedProbability, 1) + '</b> | линия: <b>' + formatPercent(v.impliedProbability, 1) + '</b>');
  parts.push('🧠 Уверенность: <b>' + formatPercent(v.confidence, 1) + '</b> | Источник: <b>' + escapeHtml(v.oddsSource || 'Unknown') + '</b>');
  parts.push('🕒 Начало: <b>' + matchDate + '</b>');
  parts.push('');
  if (v.analysisHeadline) parts.push('🏷 <b>' + escapeHtml(v.analysisHeadline) + '</b>');
  if (v.analysisNarrative) parts.push(escapeHtml(v.analysisNarrative));
  var factorItems = [];
  if (v.analysisFactors) {
    String(v.analysisFactors).split(' | ').forEach(function (item) { if (item) factorItems.push(item); });
  }
  if (v.formSnapshot) factorItems.push('форма: ' + v.formSnapshot);
  if (v.tableSnapshot) factorItems.push('таблица: ' + v.tableSnapshot);
  if (v.predictionScore) factorItems.push('ожидаемый xG-баланс: ' + v.predictionScore + (v.predictedTotal ? ' (тотал ~' + v.predictedTotal + ')' : ''));
  if (v.injuriesSnapshot) factorItems.push('составы/потери: ' + v.injuriesSnapshot);
  if (factorItems.length) {
    parts.push('');
    parts.push('📌 <b>Что учитываю:</b>');
    factorItems.slice(0, 8).forEach(function (item) { parts.push('• ' + escapeHtml(item)); });
  }
  parts.push('');
  parts.push('📝 <i>' + escapeHtml(v.comment || getEdgeDescription(Number(v.valueGap || 0), Number(v.evPercent || 0), Number(v.confidence || 0))) + '</i>');
  return parts.join('\n');
}

function splitTelegramHtmlMessage(text, maxLen) {
  var limit = Math.max(500, Number(maxLen || 3800));
  var source = String(text || '');
  if (source.length <= limit) return [source];
  var parts = source.split(/\n\n/);
  var out = [];
  var current = '';
  parts.forEach(function (part) {
    var chunk = part || '';
    var candidate = current ? current + '\n\n' + chunk : chunk;
    if (candidate.length <= limit) {
      current = candidate;
      return;
    }
    if (current) out.push(current);
    if (chunk.length <= limit) {
      current = chunk;
      return;
    }
    var rest = chunk;
    while (rest.length > limit) {
      out.push(rest.slice(0, limit));
      rest = rest.slice(limit);
    }
    current = rest;
  });
  if (current) out.push(current);
  return out.length ? out : [source.slice(0, limit)];
}

function escapeTelegramHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function postTelegram(token, chatId, text) {
  if (!token || !chatId || !text) return false;
  var url = 'https://api.telegram.org/bot' + token + '/sendMessage';
  var chunks = splitTelegramHtmlMessage(text, 3800);
  var ok = true;
  chunks.forEach(function (chunk, idx) {
    var payload = { chat_id: chatId, text: chunk, parse_mode: 'HTML', disable_web_page_preview: true };
    var response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
    var status = response.getResponseCode();
    var body = response.getContentText();
    Logger.log('Telegram status[' + (idx + 1) + '/' + chunks.length + ']: ' + status);
    if (status < 200 || status >= 300) {
      Logger.log('Telegram error body: ' + body.slice(0, 800));
      ok = false;
    }
  });
  return ok;
}

function sendTelegram(valueBets, config) {
  var text = '';
  if (!valueBets || !valueBets.length) {
    text = '⚠️ <b>На ближайшие матчи интересных ставок не найдено</b>\n\n' +
      'Сейчас модель не видит вариантов, где вероятность исхода заметно выше, чем её оценивает букмекер.';
    postTelegram(config.telegramToken, config.telegramChatId, text);
    return;
  }

  var telegramPool = filterTelegramBets(valueBets, config);
  var groupedTop = [];
  var categories = ['h2h', 'spreads', 'totals', 'team_totals', 'dnb', 'btts', 'double_chance', 'combo'];

  for (var i = 0; i < categories.length; i++) {
    for (var j = 0; j < telegramPool.length; j++) {
      if (getTelegramCategoryKey(telegramPool[j]) === categories[i]) {
        groupedTop.push(telegramPool[j]);
        break;
      }
    }
  }
  for (var k = 0; k < telegramPool.length; k++) {
    if (groupedTop.length >= (config.telegramTopLimit || TELEGRAM_TOP_LIMIT)) break;
    if (groupedTop.indexOf(telegramPool[k]) === -1) groupedTop.push(telegramPool[k]);
  }

  var topBets = groupedTop.slice(0, config.telegramTopLimit || TELEGRAM_TOP_LIMIT);
  text += '🔥 <b>Лучшие валуйные ставки на ближайшие матчи</b>\n\n';
  text += 'Категории в приоритете: 1X2, фора, тотал, DNB, BTTS, двойной шанс, инд. тоталы.\n';
  text += 'В тексте прогноза указываются не только коэффициенты, но и что именно модель учитывала: форму, таблицу, xG, ожидаемый тотал, составы и подтверждение по рынку.\n';
  text += 'Telegram получает только лучшие некоррелирующие ставки: не более одной ставки на категорию в рамках одного матча.\n\n';
  topBets.forEach(function (v, index) { text += formatTelegramBetHuman(v, index + 1, config.timezone) + '\n\n'; });
  if (valueBets.length > topBets.length) text += '📌 Полный список из <b>' + valueBets.length + '</b> ставок сохранён в таблице.';
  postTelegram(config.telegramToken, config.telegramChatId, text);
}

function debugCoverage(matches, tips, oddsFeeds, config) {
  var withTips = 0;
  var withAnyOdds = 0;
  var withTheOdds = 0;
  var withOddsApiIo = 0;
  var withApiFootball = 0;

  matches.forEach(function (m) {
    if (tips && tips[m.matchKey]) withTips++;
    var combined = getCombinedOddsForMatch(m, oddsFeeds, config);
    if (hasAnyOffers(combined)) withAnyOdds++;
    if (oddsFeeds.oddsApiIo && oddsFeeds.oddsApiIo[m.matchKey]) withOddsApiIo++;
    if (oddsFeeds.apiFootball && oddsFeeds.apiFootball[m.matchKey]) withApiFootball++;

    var matchedTheOdds = false;
    (oddsFeeds.theOdds || []).forEach(function (item) {
      if (!matchedTheOdds && isSameMatchForOdds(item, m, config)) matchedTheOdds = true;
    });
    if (matchedTheOdds) withTheOdds++;
  });

  Logger.log('Coverage => withTips=' + withTips + ', withAnyOdds=' + withAnyOdds + ', withTheOdds=' + withTheOdds + ', withOddsApiIo=' + withOddsApiIo + ', withApiFootball=' + withApiFootball);
}

/* ======================= MAIN ======================= */
function main() {
  var config = null;
  try {
    config = getConfig();

    var tipsBundle = getBzzoiroPredictionBundle(config);
    var tips = tipsBundle.index || {};
    Logger.log('Predictions fetched: ' + Object.keys(tips).length);

    var theOddsSports = getTheOddsSports(config);
    var theOddsFeed = getTheOddsApiFeed(config, theOddsSports);

    var matches = getAllMatches(config, theOddsFeed, tipsBundle);
    Logger.log('Matches fetched: ' + matches.length);

    var oddsSources = getOddsSources(config, matches, theOddsFeed);
    var deepContext = buildDeepSoccerContext(matches, tips, oddsSources, config);

    debugCoverage(matches, tips, oddsSources, config);

    var valueBets = calculateValueBets(matches, tips, oddsSources, config, deepContext);
    Logger.log('Value bets calculated: ' + valueBets.length);

    var sheetOk = logToSheet(valueBets, config);
    Logger.log('Sheet logging result: ' + sheetOk);

    sendTelegram(valueBets, config);

    Logger.log('main completed successfully');
    return valueBets;
  } catch (e) {
    var message = 'Ошибка в main: ' + (e && e.stack ? e.stack : e);
    Logger.log(message);
    if (config && config.telegramToken && config.telegramChatId) {
      postTelegram(config.telegramToken, config.telegramChatId, '❌ <b>Ошибка в main</b>\n<pre>' + escapeTelegramHtml(message).slice(0, 3400) + '</pre>');
    }
    throw e;
  }
}

/* ======================= V5 QUALITY PATCH ======================= */
function getEnhancedRuntimeConfig(config) {
  var clone = Object.assign({}, config || {});
  clone.publishWindowHours = 48;
  clone.finalTopLimit = 5;
  clone.finalMaxPerLeague = 2;
  clone.finalMaxPerMatch = 1;
  clone.rejectBookiesOnly = true;
  clone.disableSameMatchCombos = true;
  clone.strictSoccerTotals = true;
  clone.minPublishedConfidence = Math.max(58, Number(clone.minModelConfidence || 52));
  clone.minPublishedEdgePct = Math.max(3.0, Number(clone.minEdgePct || 1.5));
  clone.minPublishedEvPct = Math.max(2.0, Number(clone.minEvPct || 1.0));
  return clone;
}

function parseExternalTipTriplet(text) {
  var source = String(text || '');
  var m = source.match(/1\s*=\s*([0-9]+(?:[\.,][0-9]+)?)%\s*,\s*X\s*=\s*([0-9]+(?:[\.,][0-9]+)?)%\s*,\s*2\s*=\s*([0-9]+(?:[\.,][0-9]+)?)%/i);
  if (!m) return null;
  var p1 = Number(String(m[1]).replace(',', '.')) / 100;
  var px = Number(String(m[2]).replace(',', '.')) / 100;
  var p2 = Number(String(m[3]).replace(',', '.')) / 100;
  if (!isFinite(p1) || !isFinite(px) || !isFinite(p2)) return null;
  var sum = p1 + px + p2;
  if (sum <= 0) return null;
  return { p1: p1 / sum, px: px / sum, p2: p2 / sum };
}

function extractTipTripletFromBet(bet) {
  var texts = [bet && bet.analysisFactors, bet && bet.analysisNarrative, bet && bet.comment].filter(Boolean);
  for (var i = 0; i < texts.length; i++) {
    var parsed = parseExternalTipTriplet(texts[i]);
    if (parsed) return parsed;
  }
  return null;
}

function extractPredictedTotalFromBet(bet) {
  if (!bet) return null;
  if (bet.predictedTotal != null && bet.predictedTotal !== '') {
    var direct = Number(String(bet.predictedTotal).replace(',', '.'));
    if (isFinite(direct)) return direct;
  }
  var texts = [bet.analysisFactors, bet.analysisNarrative, bet.comment].filter(Boolean).join(' | ');
  var m = texts.match(/тотал(?:\s+около|\s*~)?\s*([0-9]+(?:[\.,][0-9]+)?)/i);
  if (m) {
    var x = Number(String(m[1]).replace(',', '.'));
    if (isFinite(x)) return x;
  }
  return null;
}

function betHasBookiesOnlySignal(bet) {
  if (!bet) return false;
  if (String(bet.oddsSource || '') !== 'BookiesApi') return false;
  var books = Number(bet.analysisBooks || 0);
  return books < 2;
}

function isReasonableSoccerDrawFromBet(bet) {
  var tri = extractTipTripletFromBet(bet);
  if (!tri) return true;
  return tri.px >= 0.08 && tri.px <= 0.45;
}

function passesEnhancedBetQuality(bet, config) {
  if (!bet) return false;
  if (bet.betType === 'combo' || bet.marketFamily === 'combo') return false;
  if (!bet.date) return false;
  var startTs = bet.date.getTime ? bet.date.getTime() : new Date(bet.date).getTime();
  if (!isFinite(startTs)) return false;
  var nowTs = Date.now();
  var maxTs = nowTs + Number(config.publishWindowHours || 48) * 3600 * 1000;
  if (startTs < nowTs - 10 * 60 * 1000 || startTs > maxTs) return false;

  if (!isFinite(Number(bet.odd)) || Number(bet.odd) <= 1.01) return false;
  if (!isFinite(Number(bet.adjustedProbability)) || Number(bet.adjustedProbability) < 3 || Number(bet.adjustedProbability) > 97) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < Number(config.minPublishedConfidence || 58)) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < Number(config.minPublishedEdgePct || 3)) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < Number(config.minPublishedEvPct || 2)) return false;
  if (config.rejectBookiesOnly && betHasBookiesOnlySignal(bet)) return false;

  if (bet.sportKey === 'soccer') {
    if (!isReasonableSoccerDrawFromBet(bet) && (bet.marketFamily === 'h2h' || bet.marketFamily === 'dnb' || bet.marketFamily === 'doubleChance' || bet.marketFamily === 'spreads')) return false;
    if (config.strictSoccerTotals && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) {
      var total = extractPredictedTotalFromBet(bet);
      if (total == null || !isFinite(total) || total < 0.8 || total > 5.5) return false;
    }
    var sstatsZeroZero = /SStats xG:\s*0(?:[\.,]0+)?\s*-\s*0(?:[\.,]0+)?/i.test(String(bet.analysisFactors || ''));
    if (sstatsZeroZero && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) return false;
  }
  return true;
}

function buildBetExplainabilityScore(bet) {
  if (!bet) return -9999;
  var score = Number(bet.score || 0);
  score += Math.min(8, Number(bet.analysisBooks || 0) * 1.2);
  score += Math.min(4, Number(bet.analysisSourceCount || 0) * 1.0);
  if (bet.tableSnapshot) score += 2.5;
  if (bet.formSnapshot) score += 2.5;
  if (bet.injuriesSnapshot) score += 1.5;
  if (bet.analysisNarrative) score += 2.0;
  if (bet.predictedTotal || bet.predictionScore) score += 1.5;
  if (String(bet.oddsSource || '') === 'OddsApiIo') score += 1.0;
  if (String(bet.oddsSource || '') === 'BookiesApi') score -= 1.5;
  return score;
}

function selectBestBetsForPublication(valueBets, config) {
  var maxPerLeague = Math.max(1, Number(config.finalMaxPerLeague || 2));
  var maxPerMatch = Math.max(1, Number(config.finalMaxPerMatch || 1));
  var limit = Math.max(1, Number(config.finalTopLimit || 5));
  var byMatch = {};
  var byLeague = {};
  var out = [];

  (valueBets || [])
    .filter(function (bet) { return passesEnhancedBetQuality(bet, config); })
    .sort(function (a, b) {
      var sa = buildBetExplainabilityScore(a);
      var sb = buildBetExplainabilityScore(b);
      if (sb !== sa) return sb - sa;
      if ((b.evPercent || 0) !== (a.evPercent || 0)) return (b.evPercent || 0) - (a.evPercent || 0);
      if ((b.valueGap || 0) !== (a.valueGap || 0)) return (b.valueGap || 0) - (a.valueGap || 0);
      return (b.confidence || 0) - (a.confidence || 0);
    })
    .forEach(function (bet) {
      if (out.length >= limit) return;
      var matchKey = String(bet.matchKey || '');
      var leagueKey = String(bet.sportKey || '') + '|' + String(bet.league || '');
      if ((byMatch[matchKey] || 0) >= maxPerMatch) return;
      if ((byLeague[leagueKey] || 0) >= maxPerLeague) return;
      out.push(bet);
      byMatch[matchKey] = (byMatch[matchKey] || 0) + 1;
      byLeague[leagueKey] = (byLeague[leagueKey] || 0) + 1;
    });
  return out;
}

function buildFullAnalysisText(v) {
  var paragraphs = [];
  if (v.tableSnapshot) paragraphs.push('Турнирная ситуация: ' + v.tableSnapshot + '.');
  if (v.formSnapshot) paragraphs.push('Текущая форма: ' + v.formSnapshot + '.');
  if (v.injuriesSnapshot) paragraphs.push('Составы и потери: ' + v.injuriesSnapshot + '.');
  if (v.analysisFactors && /личные встречи:/i.test(v.analysisFactors)) {
    var h2h = String(v.analysisFactors).split(' | ').filter(function (x) { return /личные встречи:/i.test(x); });
    if (h2h.length) paragraphs.push('Очные встречи: ' + h2h[0] + '.');
  }
  if (v.predictionScore || v.predictedTotal) {
    var xgText = 'Модельный сценарий';
    if (v.predictionScore) xgText += ': xG ' + v.predictionScore;
    if (v.predictedTotal) xgText += ', ожидаемый тотал около ' + v.predictedTotal;
    paragraphs.push(xgText + '.');
  }
  if (v.analysisNarrative) paragraphs.push(v.analysisNarrative);
  if (v.comment) paragraphs.push('Подтверждение рынка: ' + v.comment + '.');
  return paragraphs.join(' ');
}

function formatTelegramBetHuman(v, number, timezone) {
  var matchDate = Utilities.formatDate(v.date, timezone, 'dd.MM.yyyy HH:mm');
  var parts = [];
  parts.push('⚽ <b>' + escapeHtml(v.sport) + '. ' + escapeHtml(v.league) + '</b>');
  parts.push('<b>' + number + '. ' + escapeHtml(v.match.replace(' vs ', ' - ')) + '</b>');
  parts.push('');
  parts.push('🎯 <b>' + escapeHtml(v.market) + '</b>');
  parts.push('Прогноз: <b>' + escapeHtml(v.outcome) + '</b>' + (v.line !== '' && v.line !== null && v.line !== undefined ? ' <b>(' + escapeHtml(String(v.line)) + ')</b>' : ''));
  parts.push('💸 Коэффициент: <b>' + formatNumber(v.odd, 2) + '</b> | EV: <b>' + formatPercent(v.evPercent, 2) + '</b> | Edge: <b>' + formatPercent(v.valueGap, 2) + '</b>');
  parts.push('📊 Модель: <b>' + formatPercent(v.probability, 1) + '</b> | скорр.: <b>' + formatPercent(v.adjustedProbability, 1) + '</b> | линия: <b>' + formatPercent(v.impliedProbability, 1) + '</b>');
  parts.push('🧠 Уверенность: <b>' + formatPercent(v.confidence, 1) + '</b> | Источник: <b>' + escapeHtml(v.oddsSource || 'Unknown') + '</b>');
  parts.push('🕒 Начало: <b>' + matchDate + '</b>');
  parts.push('');
  parts.push('🏷 <b>' + escapeHtml(v.analysisHeadline || 'Полный анализ ставки') + '</b>');
  parts.push(escapeHtml(buildFullAnalysisText(v) || 'Недостаточно контекста для полного анализа.'));
  if (v.analysisFactors) {
    var factorItems = String(v.analysisFactors).split(' | ').filter(Boolean).slice(0, 8);
    if (factorItems.length) {
      parts.push('');
      parts.push('📌 <b>Ключевые факторы:</b>');
      factorItems.forEach(function (item) { parts.push('• ' + escapeHtml(item)); });
    }
  }
  parts.push('');
  parts.push('📝 <i>' + escapeHtml(v.comment || getEdgeDescription(Number(v.valueGap || 0), Number(v.evPercent || 0), Number(v.confidence || 0))) + '</i>');
  return parts.join('\n');
}

function evaluateComboBets(match, config, candidateGroups) {
  if (config && config.disableSameMatchCombos) return [];
  return [];
}

function sendTelegram(valueBets, config) {
  var text = '';
  if (!valueBets || !valueBets.length) {
    text = '⚠️ <b>На ближайшие 48 часов подходящих ставок не найдено</b>\n\n' +
      'Фильтр качества отклонил варианты без нормального рыночного подтверждения, с сомнительными вероятностями или слабым матчевым контекстом.';
    postTelegram(config.telegramToken, config.telegramChatId, text);
    return;
  }
  text += '🔥 <b>5 лучших валуйных ставок на ближайшие 48 часов</b>\n\n';
  text += 'В выдачу попадают только одиночные ставки с полным анализом: турнирная таблица, форма, составы/потери, очные встречи, xG/тотал и подтверждение по рынку.\n';
  text += 'На один матч — не более одной ставки. Мультиставки внутри одного матча отключены как слишком коррелированные.\n\n';
  valueBets.slice(0, Number(config.finalTopLimit || 5)).forEach(function (v, index) {
    text += formatTelegramBetHuman(v, index + 1, config.timezone) + '\n\n';
  });
  postTelegram(config.telegramToken, config.telegramChatId, text);
}

function getSheetHeaders() {
  return [
    'Вид спорта', 'Дата матча', 'Лига', 'Матч', 'Рынок', 'Исход', 'Линия', 'Коэффициент', 'БК',
    'Источник коэффициента', 'Вероятность модели %', 'Скорр. вероятность %', 'Импл. вероятность %', 'Value gap %', 'EV %', 'Raw EV %', 'Confidence',
    'Рекомендуемая ставка', '% от банка', 'Тип ставки', 'Категория', 'Score', 'Экзотическая лига', 'Комментарий',
    'Форма', 'Таблица', 'Составы/потери', 'xG/тотал', 'Полный анализ'
  ];
}

function logToSheet(valueBets, config) {
  try {
    var info = ensureSheetStructure(config);
    var ss = info.ss;
    var sheet = info.sheet;
    if (valueBets && valueBets.length) {
      var rows = valueBets.map(function (v) {
        return [
          v.sport,
          Utilities.formatDate(v.date, config.timezone, 'dd.MM.yyyy HH:mm'),
          v.league,
          v.match,
          v.market,
          v.outcome,
          v.line,
          v.odd,
          v.bookmaker,
          v.oddsSource || 'Unknown',
          v.probability,
          v.adjustedProbability,
          v.impliedProbability,
          v.valueGap,
          v.evPercent,
          v.rawEvPercent,
          v.confidence,
          v.stake,
          v.stakePctBankroll,
          v.betType || 'single',
          getTelegramCategoryKey(v),
          v.score || '',
          isProbablyExoticLeague({ league: v.league, sport: v.sportKey }, config) ? 'yes' : 'no',
          v.comment || '',
          v.formSnapshot || '',
          v.tableSnapshot || '',
          v.injuriesSnapshot || '',
          (v.predictionScore ? v.predictionScore : '') + (v.predictedTotal ? ' | Тотал ~' + v.predictedTotal : ''),
          buildFullAnalysisText(v)
        ];
      });
      sheet.getRange(2, 1, rows.length, rows[0].length).setValues(rows);
    }
    Logger.log('В таблицу записано строк: ' + valueBets.length + ', spreadsheetId=' + ss.getId());
    return true;
  } catch (e) {
    Logger.log('Ошибка записи в таблицу: ' + (e && e.stack ? e.stack : e));
    return false;
  }
}

function main() {
  var config = null;
  try {
    config = getEnhancedRuntimeConfig(getConfig());

    var tipsBundle = getBzzoiroPredictionBundle(config);
    var tips = tipsBundle.index || {};
    Logger.log('Predictions fetched: ' + Object.keys(tips).length);

    var theOddsSports = getTheOddsSports(config);
    var theOddsFeed = getTheOddsApiFeed(config, theOddsSports);

    var matches = getAllMatches(config, theOddsFeed, tipsBundle);
    Logger.log('Matches fetched: ' + matches.length);

    var oddsSources = getOddsSources(config, matches, theOddsFeed);
    var deepContext = buildDeepSoccerContext(matches, tips, oddsSources, config);

    debugCoverage(matches, tips, oddsSources, config);

    var allCandidates = calculateValueBets(matches, tips, oddsSources, config, deepContext);
    Logger.log('Value bets calculated: ' + allCandidates.length);

    var shortlist = selectBestBetsForPublication(allCandidates, config);
    Logger.log('Publication shortlist: ' + shortlist.length);

    var sheetOk = logToSheet(shortlist, config);
    Logger.log('Sheet logging result: ' + sheetOk);

    sendTelegram(shortlist, config);

    Logger.log('main completed successfully');
    return shortlist;
  } catch (e) {
    var message = 'Ошибка в main: ' + (e && e.stack ? e.stack : e);
    Logger.log(message);
    if (config && config.telegramToken && config.telegramChatId) {
      postTelegram(config.telegramToken, config.telegramChatId, '❌ <b>Ошибка в main</b>\n<pre>' + escapeTelegramHtml(message).slice(0, 3400) + '</pre>');
    }
    throw e;
  }
}


/* ======================= V6 STABILITY + ANALYSIS PATCH ======================= */
function getPersistentRuntimeFlag_(key) {
  try {
    return PropertiesService.getScriptProperties().getProperty(String(key || '')) || '';
  } catch (e) {
    return '';
  }
}

function setPersistentRuntimeFlag_(key, value) {
  try {
    PropertiesService.getScriptProperties().setProperty(String(key || ''), String(value == null ? '' : value));
  } catch (e) {}
}

function clearPersistentRuntimeFlag_(key) {
  try {
    PropertiesService.getScriptProperties().deleteProperty(String(key || ''));
  } catch (e) {}
}

function isTheOddsTemporarilyDisabled(config) {
  var memoryTs = Number(RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS || 0);
  var persistedTs = Number(getPersistentRuntimeFlag_('THE_ODDS_DISABLED_UNTIL_TS') || 0);
  var untilTs = Math.max(memoryTs, persistedTs);
  if (!untilTs) return false;
  if (Date.now() > untilTs) {
    delete RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS;
    clearPersistentRuntimeFlag_('THE_ODDS_DISABLED_UNTIL_TS');
    return false;
  }
  RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS = untilTs;
  return true;
}

function markTheOddsTemporarilyDisabled(config, reason) {
  var hours = Math.max(1, Number(config && config.theOddsDisableHours || 6));
  var untilTs = Date.now() + hours * 60 * 60 * 1000;
  RUNTIME_FLAGS.THE_ODDS_DISABLED_UNTIL_TS = untilTs;
  setPersistentRuntimeFlag_('THE_ODDS_DISABLED_UNTIL_TS', untilTs);
  Logger.log('The Odds API временно отключен на ' + hours + ' ч. Причина: ' + (reason || 'unknown'));
}

function isUpcomingApiFootballStatus_(shortCode) {
  var shortVal = String(shortCode || '').toUpperCase();
  return shortVal === 'NS' || shortVal === 'TBD' || shortVal === 'PST';
}

function getApiFootballFixtures(config) {
  if (!config.apiFootballKey || config.enabledSports.indexOf('soccer') === -1) return [];
  var range = getDateRange(config.timezone, config.daysAhead);
  var rows = [];
  var data = fetchApiFootball('/fixtures', {
    from: range.from,
    to: range.to,
    timezone: config.apiFootballTimezone
  }, config, 'API-Football fixtures', config.apiFootballFixturesCacheSeconds || config.cacheSeconds || 0);
  rows = data && data.response ? data.response : [];
  if (!rows.length) {
    var fallback = fetchApiFootball('/fixtures', {
      from: range.from,
      to: range.to
    }, config, 'API-Football fixtures fallback', config.apiFootballFixturesCacheSeconds || config.cacheSeconds || 0);
    rows = fallback && fallback.response ? fallback.response : [];
  }
  return (rows || []).filter(function (row) {
    return isUpcomingApiFootballStatus_(safeNestedGet(row, 'fixture.status.short', ''));
  });
}

function shiftDateKeyByDays_(dateKey, days) {
  try {
    var parts = String(dateKey || '').split('-');
    if (parts.length !== 3) return String(dateKey || '');
    var d = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
    d.setUTCDate(d.getUTCDate() + Number(days || 0));
    return Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
  } catch (e) {
    return String(dateKey || '');
  }
}

function fetchApiFootballFixturesForDate(dateKey, config, memo) {
  memo = memo || {};
  if (!dateKey) return [];
  if (memo[dateKey]) return memo[dateKey];
  var data = fetchApiFootball('/fixtures', { date: dateKey, timezone: config.apiFootballTimezone }, config, 'API-Football fixtures day ' + dateKey, config.fixturesCacheSeconds || config.cacheSeconds || 300);
  var rows = data && data.response ? data.response : [];
  if (!rows.length) {
    var fallback = fetchApiFootball('/fixtures', { date: dateKey }, config, 'API-Football fixtures day fallback ' + dateKey, config.fixturesCacheSeconds || config.cacheSeconds || 300);
    rows = fallback && fallback.response ? fallback.response : [];
  }
  memo[dateKey] = rows || [];
  return memo[dateKey];
}

function getApiFootballCandidateDateKeys_(isoDate) {
  var base = getDateKey(isoDate);
  return [base, shiftDateKeyByDays_(base, -1), shiftDateKeyByDays_(base, 1)].filter(function (x, idx, arr) {
    return x && arr.indexOf(x) === idx;
  });
}

function enrichTopSoccerMatchesWithApiFootballContext(matches, config) {
  if (!matches || !matches.length || !config.apiFootballKey) return 0;
  var dayMemo = {};
  var enriched = 0;
  matches.forEach(function (match) {
    if (!match || match.sport !== 'soccer') return;
    if (match.apiFootballFixtureId && match.apiFootballHomeTeamId && match.apiFootballAwayTeamId) return;
    var dateKeys = getApiFootballCandidateDateKeys_(match.isoDate);
    var best = null;
    var bestScore = -1;
    dateKeys.forEach(function (dateKey) {
      var rows = fetchApiFootballFixturesForDate(dateKey, config, dayMemo);
      (rows || []).forEach(function (row) {
        var fixture = normalizeApiFootballFixture(row);
        if (!fixture || fixture.sport !== 'soccer') return;
        if (!fuzzyTeamsEquivalent(fixture.home, fixture.away, match.home, match.away)) return;
        var diff = dateDiffHours(fixture.isoDate, match.isoDate);
        if (diff != null && diff > Math.min(config.matchStartToleranceHours || 30, 18)) return;
        var score = 0;
        if (matchTeamsEquivalent(fixture.home, fixture.away, match.home, match.away)) score += 12;
        if (canonicalizeLeagueName(fixture.league) === canonicalizeLeagueName(match.league)) score += 7;
        if (diff != null) score += Math.max(0, 8 - diff);
        if (score > bestScore) {
          best = fixture;
          bestScore = score;
        }
      });
    });
    if (best) {
      mergeMatchFields(match, best);
      if (best.apiFootballFixtureId) match.apiFootballFixtureId = best.apiFootballFixtureId;
      if (best.apiFootballLeagueId) match.apiFootballLeagueId = best.apiFootballLeagueId;
      if (best.apiFootballSeason) match.apiFootballSeason = best.apiFootballSeason;
      if (best.apiFootballHomeTeamId) match.apiFootballHomeTeamId = best.apiFootballHomeTeamId;
      if (best.apiFootballAwayTeamId) match.apiFootballAwayTeamId = best.apiFootballAwayTeamId;
      enriched += 1;
    }
  });
  Logger.log('API-Football context enrichment for top soccer matches: ' + enriched + ' / ' + matches.length);
  return enriched;
}

function buildMotivationTextFromStandings_(homeStanding, awayStanding, match) {
  if (!homeStanding || !awayStanding) return '';
  if (homeStanding.rank == null || awayStanding.rank == null) return '';
  var diff = Number(homeStanding.rank) - Number(awayStanding.rank);
  var parts = [];
  if (Math.abs(diff) <= 3) parts.push('команды близки в таблице, матч имеет прямое турнирное значение');
  else if (diff <= -5) parts.push(match.home + ' заметно выше в таблице и обязан подтверждать статус фаворита');
  else if (diff >= 5) parts.push(match.away + ' идёт выше в таблице и приезжает с турнирным преимуществом');
  if (homeStanding.points != null && awayStanding.points != null) {
    var ptsDiff = Math.abs(Number(homeStanding.points) - Number(awayStanding.points));
    if (ptsDiff <= 4) parts.push('разница по очкам небольшая, мотивация у обеих сторон высокая');
  }
  return parts.join('; ');
}

function countAnalysisSignals_(bet) {
  if (!bet) return 0;
  var n = 0;
  if (bet.tableSnapshot) n += 1;
  if (bet.formSnapshot) n += 1;
  if (bet.injuriesSnapshot) n += 1;
  if (bet.predictionScore || bet.predictedTotal) n += 1;
  if (/личные встречи:/i.test(String(bet.analysisFactors || ''))) n += 1;
  return n;
}

function hasRichContextForBet_(bet) {
  return countAnalysisSignals_(bet) >= 2;
}

function sanitizeProbabilityForBetRecord_(match, family, probability, offer, analysisMeta, config) {
  var p = Number(probability);
  if (!isFinite(p)) return probability;
  var implied = offer && offer.price && offer.price > 1 ? (100 / Number(offer.price)) : null;
  var books = analysisMeta && analysisMeta.familyBookCounts && analysisMeta.familyBookCounts[family] ? Number(analysisMeta.familyBookCounts[family]) : 0;
  var deep = analysisMeta && analysisMeta.deepContext ? analysisMeta.deepContext : null;
  var sourceCount = 0;
  ['tip', 'context', 'sstats'].forEach(function (src) {
    var val = getSourceProbabilityForBet(match, family, offer, analysisMeta, src);
    if (val != null && isFinite(val)) sourceCount += 1;
  });
  var hasRich = !!(deep && (deep.homeForm || deep.awayForm || deep.h2h || deep.homeStanding || deep.awayStanding || deep.injuries || deep.lineups || deep.sstats));
  if (implied != null) {
    if (p >= 97 || p <= 3) p = implied + (p - implied) * 0.18;
    else if (p >= 90 || p <= 10) p = implied + (p - implied) * (hasRich ? 0.38 : 0.24);
    else if ((sourceCount < 2 || books < 2) && (p >= 82 || p <= 18)) p = implied + (p - implied) * 0.45;
  }
  if ((family === 'spreads' || family === 'dnb' || family === 'doubleChance') && !hasRich && p > 80) p = 80;
  if ((family === 'totals' || family === 'teamTotals' || family === 'btts') && !hasRich && p > 78) p = 78;
  return clamp(p, 2, 98);
}

function getEnhancedRuntimeConfig(config) {
  var clone = Object.assign({}, config || {});
  clone.publishWindowHours = 48;
  clone.finalTopLimit = 5;
  clone.finalMaxPerLeague = 2;
  clone.finalMaxPerMatch = 1;
  clone.rejectBookiesOnly = true;
  clone.disableSameMatchCombos = true;
  clone.strictSoccerTotals = true;
  clone.minPublishedConfidence = Math.max(60, Number(clone.minModelConfidence || 52));
  clone.minPublishedEdgePct = Math.max(3.0, Number(clone.minEdgePct || 1.5));
  clone.minPublishedEvPct = Math.max(2.0, Number(clone.minEvPct || 1.0));
  clone.maxStakePct = Math.min(Number(clone.maxStakePct || 0.03), 0.015);
  clone.kellyFraction = Math.min(Number(clone.kellyFraction || 0.25), 0.18);
  clone.minContextSignalsForPublication = 2;
  clone.rawProbabilityHardCap = 90;
  clone.rawProbabilityExtremeCap = 96;
  clone.minBooksForPublishedBookies = 3;
  return clone;
}

function buildBetInsightPayload(match, family, offer, analysisMeta, betLike) {
  var payload = { headline: '', factors: [], narrative: '', predictionScore: '', predictedTotal: '', tables: '', form: '', injuries: '', marketReason: '' };
  if (!analysisMeta) return payload;
  var deep = analysisMeta.deepContext || null;
  var contextModel = analysisMeta.contextModel || null;
  var tip = analysisMeta.tip || null;
  var h2h = deep && deep.h2h ? deep.h2h : null;
  var homeForm = deep && deep.homeForm ? deep.homeForm : null;
  var awayForm = deep && deep.awayForm ? deep.awayForm : null;
  var homeStanding = deep && deep.homeStanding ? deep.homeStanding : null;
  var awayStanding = deep && deep.awayStanding ? deep.awayStanding : null;
  var injuries = deep && deep.injuries ? deep.injuries : null;
  var sstats = deep && deep.sstats ? deep.sstats : null;
  var predictedHome = contextModel && contextModel.homeGoalsExp != null ? Number(contextModel.homeGoalsExp) : null;
  var predictedAway = contextModel && contextModel.awayGoalsExp != null ? Number(contextModel.awayGoalsExp) : null;
  var totalExp = predictedHome != null && predictedAway != null ? predictedHome + predictedAway : null;
  var motivationText = buildMotivationTextFromStandings_(homeStanding, awayStanding, match);

  if (predictedHome != null && predictedAway != null) {
    payload.predictionScore = formatNumber(predictedHome, 2) + ' : ' + formatNumber(predictedAway, 2);
    payload.predictedTotal = formatNumber(totalExp, 2);
  }
  payload.form = [buildFormMiniText(homeForm, match.home), buildFormMiniText(awayForm, match.away)].filter(Boolean).join(' | ');
  payload.tables = buildStandingMiniText(homeStanding, awayStanding, match);
  payload.injuries = buildInjuriesMiniText(injuries, match);

  if (homeForm && awayForm && homeForm.pointsPerGame != null && awayForm.pointsPerGame != null) {
    var diff = Number(homeForm.pointsPerGame) - Number(awayForm.pointsPerGame);
    if (diff >= 0.30) payload.factors.push(match.home + ' лучше по форме: ' + formatNumber(homeForm.pointsPerGame, 2) + ' vs ' + formatNumber(awayForm.pointsPerGame, 2) + ' очка/матч');
    else if (diff <= -0.30) payload.factors.push(match.away + ' лучше по форме: ' + formatNumber(awayForm.pointsPerGame, 2) + ' vs ' + formatNumber(homeForm.pointsPerGame, 2) + ' очка/матч');
  }
  if (contextModel && predictedHome != null && predictedAway != null) {
    payload.factors.push('модель ожидает xG ' + formatNumber(predictedHome, 2) + ' - ' + formatNumber(predictedAway, 2));
    if (totalExp != null) payload.factors.push('ожидаемый тотал около ' + formatNumber(totalExp, 2));
  }
  if (homeStanding && awayStanding && homeStanding.rank != null && awayStanding.rank != null) {
    payload.factors.push('таблица: ' + match.home + ' #' + formatNumber(homeStanding.rank, 0) + ', ' + match.away + ' #' + formatNumber(awayStanding.rank, 0));
  }
  if (motivationText) payload.factors.push('мотивация: ' + motivationText);
  if (h2h && h2h.sampleSize) {
    payload.factors.push('личные встречи: ' + formatNumber(h2h.sampleSize, 0) + ' матч., средний тотал ' + formatNumber(h2h.avgTotalGoals, 2));
  }
  if (tip && tip.home != null && tip.draw != null && tip.away != null) {
    payload.factors.push('внешний прогноз: 1=' + formatPercent(tip.home, 0) + ', X=' + formatPercent(tip.draw, 0) + ', 2=' + formatPercent(tip.away, 0));
  }
  if (sstats && sstats.homeXg != null && sstats.awayXg != null) {
    payload.factors.push('SStats xG: ' + formatNumber(sstats.homeXg, 2) + ' - ' + formatNumber(sstats.awayXg, 2));
  }
  if (payload.injuries) payload.factors.push('кадровая ситуация: ' + payload.injuries);

  if (family === 'h2h' || family === 'dnb' || family === 'doubleChance') {
    payload.headline = 'Прогноз на исход';
    payload.marketReason = 'перевес модели над линией букмекера идёт именно по исходу матча';
  } else if (family === 'totals') {
    payload.headline = 'Прогноз на тотал';
    payload.marketReason = 'ожидаемая результативность, темп и баланс атаки/обороны указывают именно на этот тотал';
  } else if (family === 'spreads') {
    payload.headline = 'Прогноз на фору';
    payload.marketReason = 'разница сил команд и ожидаемый сценарий матча лучше всего читаются через фору';
  } else if (family === 'btts') {
    payload.headline = 'Прогноз на обе забьют';
    payload.marketReason = 'ожидаемые голы и профиль команд в атаке и обороне поддерживают рынок BTTS';
  } else if (family === 'teamTotals') {
    payload.headline = 'Прогноз на индивидуальный тотал';
    payload.marketReason = 'командный xG, форма и уязвимости соперника лучше раскрываются через индивидуальный тотал';
  }

  var paragraphs = [];
  if (payload.tables) paragraphs.push('По таблице: ' + payload.tables + '.');
  if (motivationText) paragraphs.push('По мотивации: ' + motivationText + '.');
  if (payload.form) paragraphs.push('По форме: ' + payload.form + '.');
  if (predictedHome != null && predictedAway != null) paragraphs.push('По модели ожидаю структуру матча в районе ' + payload.predictionScore + ' по xG, то есть тотал около ' + payload.predictedTotal + '.');
  if (h2h && h2h.sampleSize) paragraphs.push('По личным встречам выборка даёт средний тотал ' + formatNumber(h2h.avgTotalGoals, 2) + '.');
  if (payload.injuries) paragraphs.push('По составам: ' + payload.injuries + '.');
  if (payload.marketReason) paragraphs.push('По рынку: ' + payload.marketReason + '.');
  payload.narrative = paragraphs.join(' ');
  payload.factors = payload.factors.slice(0, 8);
  return payload;
}

function buildBetRecord(match, family, offer, probability, confidence, config, thresholds, analysisMeta) {
  if (probability == null || !offer.price || offer.price <= 1) return null;
  if (!isUsablePublishedLine_(match, family, offer.point)) return null;
  var sanitizedProbability = sanitizeProbabilityForBetRecord_(match, family, probability, offer, analysisMeta, config);
  var diagnostics = buildBetDiagnostics(match, family, offer, sanitizedProbability, confidence, analysisMeta, config);
  if (shouldRejectBetByDiagnostics(family, diagnostics, match)) return null;
  confidence = diagnostics && diagnostics.confidence != null ? diagnostics.confidence : confidence;
  var impliedProbability = 100 / offer.price;
  var adjustedProbability = shrinkProbability(sanitizedProbability, impliedProbability, confidence, config);
  var valueGap = adjustedProbability - impliedProbability;
  var evPercent = (offer.price * (adjustedProbability / 100) - 1) * 100;
  var rawEvPercent = (offer.price * (sanitizedProbability / 100) - 1) * 100;
  var stake = calcKellyStake(config.bankroll, offer.price, adjustedProbability, config.kellyFraction, config.maxStakePct);
  var minEdge = thresholds && thresholds.minEdgePct != null ? thresholds.minEdgePct : config.minEdgePct;
  var minEv = thresholds && thresholds.minEvPct != null ? thresholds.minEvPct : config.minEvPct;
  var effectiveMinConfidence = config.minModelConfidence || 52;
  if (family === 'totals' || family === 'spreads' || family === 'teamTotals' || family === 'btts') {
    effectiveMinConfidence -= 4.8;
    minEdge -= 0.55;
    minEv -= 0.40;
  } else if (family === 'dnb' || family === 'doubleChance') {
    effectiveMinConfidence -= 2.8;
    minEdge -= 0.24;
    minEv -= 0.16;
  }
  if (diagnostics && diagnostics.trustScore != null) {
    if (diagnostics.trustScore >= 1.08) {
      effectiveMinConfidence -= 1;
      minEdge -= 0.10;
    } else if (diagnostics.trustScore <= 0.92) {
      effectiveMinConfidence += 2;
      minEdge += 0.25;
      minEv += 0.15;
    }
  }
  if (diagnostics && diagnostics.outlierPenalty) {
    effectiveMinConfidence += Math.min(4, diagnostics.outlierPenalty * 0.35);
    minEdge += Math.min(0.8, diagnostics.outlierPenalty * 0.08);
    minEv += Math.min(0.6, diagnostics.outlierPenalty * 0.06);
  }
  if (isHighVarianceCompetition(match)) {
    effectiveMinConfidence += 2;
    minEdge += 0.35;
    minEv += 0.25;
  }
  effectiveMinConfidence = clamp(effectiveMinConfidence, 47, 70);
  minEdge = Math.max(0.55, minEdge);
  minEv = Math.max(0.35, minEv);
  if ((confidence || 0) < effectiveMinConfidence) return null;
  if (valueGap < minEdge) return null;
  if (evPercent < minEv) return null;
  if (stake <= 0) return null;

  var score = evPercent * 0.54 + valueGap * 0.24 + (confidence || 0) * 0.12 + Math.min(8, (diagnostics && diagnostics.supportScore || 0)) * 0.07;
  score += Math.max(-3, Math.min(3, ((diagnostics && diagnostics.trustScore != null ? diagnostics.trustScore : 1) - 1) * 12));
  score -= Math.min(4, diagnostics && diagnostics.outlierPenalty ? diagnostics.outlierPenalty * 0.35 : 0);
  if (countAnalysisSignals_({ tableSnapshot: analysisMeta && analysisMeta.deepContext && (analysisMeta.deepContext.homeStanding || analysisMeta.deepContext.awayStanding) ? '1' : '', formSnapshot: analysisMeta && analysisMeta.deepContext && (analysisMeta.deepContext.homeForm || analysisMeta.deepContext.awayForm) ? '1' : '', injuriesSnapshot: analysisMeta && analysisMeta.deepContext && analysisMeta.deepContext.injuries ? '1' : '', predictionScore: analysisMeta && analysisMeta.contextModel && analysisMeta.contextModel.homeGoalsExp != null ? '1' : '', analysisFactors: '' }) >= 2) {
    score += 3;
  }
  score = score * getFamilyWeight(config, family);
  var oddsTargetMultiplier = getOddsTargetMultiplier(offer.price, config);
  if (!oddsTargetMultiplier) return null;
  score = score * oddsTargetMultiplier;

  var insight = buildBetInsightPayload(match, family, offer, analysisMeta, { probability: sanitizedProbability, adjustedProbability: adjustedProbability, confidence: confidence });
  return {
    matchKey: match.matchKey,
    sport: getSportLabel(match.sport),
    sportKey: match.sport,
    date: match.date,
    league: match.league,
    match: match.home + ' vs ' + match.away,
    market: getReadableMarket(match, family, offer),
    marketFamily: family,
    marketSubType: offer.marketSubType || family,
    outcome: getReadableOutcome(match, family, offer),
    line: offer.point != null ? round2(offer.point) : '',
    odd: round2(offer.price),
    bookmaker: offer.bookmaker || 'Unknown',
    oddsSource: offer.sourceName || offer.source || inferOfferSource(offer) || 'Unknown',
    probability: round2(sanitizedProbability || 0),
    rawProbabilityModel: round2(probability || 0),
    adjustedProbability: round2(adjustedProbability),
    impliedProbability: round2(impliedProbability),
    valueGap: round2(valueGap),
    evPercent: round2(evPercent),
    rawEvPercent: round2(rawEvPercent),
    confidence: round2(confidence || 0),
    stake: round2(stake),
    stakePctBankroll: round2(config.bankroll ? (stake / config.bankroll) * 100 : 0),
    betType: 'single',
    score: round2(score),
    trustScore: diagnostics && diagnostics.trustScore != null ? round2(diagnostics.trustScore) : round2(getOfferTrustScore(offer, family, config)),
    analysisBooks: diagnostics && diagnostics.books != null ? diagnostics.books : (analysisMeta && analysisMeta.familyBookCounts ? analysisMeta.familyBookCounts[family] : ''),
    analysisSourceCount: diagnostics && diagnostics.sourceCount != null ? diagnostics.sourceCount : '',
    supportScore: diagnostics && diagnostics.supportScore != null ? round2(diagnostics.supportScore) : '',
    marketBaselineOdd: diagnostics && diagnostics.baselinePrice != null ? round2(diagnostics.baselinePrice) : '',
    priceDistancePct: diagnostics && diagnostics.priceDistancePct != null ? round2(diagnostics.priceDistancePct) : '',
    comment: diagnostics && diagnostics.comment ? diagnostics.comment : '',
    analysisHeadline: insight.headline || '',
    analysisFactors: (insight.factors || []).join(' | '),
    analysisNarrative: insight.narrative || '',
    predictionScore: insight.predictionScore || '',
    predictedTotal: insight.predictedTotal || '',
    tableSnapshot: insight.tables || '',
    formSnapshot: insight.form || '',
    injuriesSnapshot: insight.injuries || ''
  };
}

function buildBetExplainabilityScore(bet) {
  if (!bet) return -9999;
  var score = Number(bet.score || 0);
  score += Math.min(8, Number(bet.analysisBooks || 0) * 1.2);
  score += Math.min(4, Number(bet.analysisSourceCount || 0) * 1.0);
  score += countAnalysisSignals_(bet) * 3.2;
  if (bet.analysisNarrative) score += 2.5;
  if (String(bet.oddsSource || '') === 'OddsApiIo') score += 1.5;
  if (String(bet.oddsSource || '') === 'BookiesApi') score -= 2.0;
  if (Number(bet.rawProbabilityModel || bet.probability || 0) >= 90) score -= 8;
  if (!hasRichContextForBet_(bet)) score -= 7;
  return score;
}

function passesEnhancedBetQuality(bet, config) {
  if (!bet) return false;
  if (bet.betType === 'combo' || bet.marketFamily === 'combo') return false;
  if (!bet.date) return false;
  var startTs = bet.date.getTime ? bet.date.getTime() : new Date(bet.date).getTime();
  if (!isFinite(startTs)) return false;
  var nowTs = Date.now();
  var maxTs = nowTs + Number(config.publishWindowHours || 48) * 3600 * 1000;
  if (startTs < nowTs - 10 * 60 * 1000 || startTs > maxTs) return false;

  if (!isFinite(Number(bet.odd)) || Number(bet.odd) <= 1.01) return false;
  if (!isFinite(Number(bet.adjustedProbability)) || Number(bet.adjustedProbability) < 3 || Number(bet.adjustedProbability) > 97) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < Number(config.minPublishedConfidence || 60)) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < Number(config.minPublishedEdgePct || 3)) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < Number(config.minPublishedEvPct || 2)) return false;
  if (config.rejectBookiesOnly && betHasBookiesOnlySignal(bet)) return false;

  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (!isFinite(rawProb) || rawProb <= 1 || rawProb >= Number(config.rawProbabilityExtremeCap || 96)) return false;
  if (rawProb >= Number(config.rawProbabilityHardCap || 90) && String(bet.oddsSource || '') === 'BookiesApi') return false;

  if (!hasRichContextForBet_(bet)) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && Number(bet.analysisBooks || 0) < Number(config.minBooksForPublishedBookies || 3)) return false;

  if (bet.sportKey === 'soccer') {
    if (!isReasonableSoccerDrawFromBet(bet) && (bet.marketFamily === 'h2h' || bet.marketFamily === 'dnb' || bet.marketFamily === 'doubleChance' || bet.marketFamily === 'spreads')) return false;
    if (config.strictSoccerTotals && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) {
      var total = extractPredictedTotalFromBet(bet);
      if (total == null || !isFinite(total) || total < 0.8 || total > 5.5) return false;
    }
    var sstatsZeroZero = /SStats xG:\s*0(?:[\.,]0+)?\s*-\s*0(?:[\.,]0+)?/i.test(String(bet.analysisFactors || ''));
    if (sstatsZeroZero && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) return false;
  }
  return true;
}

function buildFullAnalysisText(v) {
  var paragraphs = [];
  if (v.tableSnapshot) paragraphs.push('Турнирная ситуация: ' + v.tableSnapshot + '.');
  if (v.formSnapshot) paragraphs.push('Текущая форма: ' + v.formSnapshot + '.');
  if (v.injuriesSnapshot) paragraphs.push('Составы и потери: ' + v.injuriesSnapshot + '.');
  if (v.analysisFactors && /личные встречи:/i.test(v.analysisFactors)) {
    var h2h = String(v.analysisFactors).split(' | ').filter(function (x) { return /личные встречи:/i.test(x); });
    if (h2h.length) paragraphs.push('Очные встречи: ' + h2h[0] + '.');
  }
  if (v.predictionScore || v.predictedTotal) {
    var xgText = 'Модельный сценарий';
    if (v.predictionScore) xgText += ': xG ' + v.predictionScore;
    if (v.predictedTotal) xgText += ', ожидаемый тотал около ' + v.predictedTotal;
    paragraphs.push(xgText + '.');
  }
  if (v.analysisNarrative) paragraphs.push(v.analysisNarrative);
  if (v.comment) paragraphs.push('Подтверждение рынка: ' + v.comment + '.');
  if (!paragraphs.length) return 'Недостаточно подтверждённого контекста для полноценного матчевого разбора.';
  return paragraphs.join(' ');
}

function sendTelegram(valueBets, config) {
  var text = '';
  var count = (valueBets || []).length;
  if (!count) {
    text = '⚠️ <b>На ближайшие 48 часов не найдено ставок, прошедших фильтр полного анализа</b>\n\n' +
      'Скрипт отсеял варианты без турнирного контекста, формы, xG/тотала или с подозрительными вероятностями модели.';
    postTelegram(config.telegramToken, config.telegramChatId, text);
    return;
  }
  text += '🔥 <b>' + count + ' лучших валуйных ставок на ближайшие 48 часов</b>\n\n';
  text += 'В выдачу попадают только одиночные ставки с подтверждённым матчевым разбором: таблица, форма, xG/тотал, очные встречи, составы/потери и рыночное подтверждение.\n';
  text += 'На один матч — не более одной ставки. Мультиставки внутри одного матча отключены как слишком коррелированные.\n\n';
  valueBets.slice(0, Number(config.finalTopLimit || 5)).forEach(function (v, index) {
    text += formatTelegramBetHuman(v, index + 1, config.timezone) + '\n\n';
  });
  if (count < Number(config.finalTopLimit || 5)) {
    text += 'ℹ️ <i>Ставок меньше пяти, потому что остальные кандидаты не прошли фильтр качества и полноты анализа.</i>';
  }
  postTelegram(config.telegramToken, config.telegramChatId, text);
}

function logToSheet(valueBets, config) {
  try {
    var info = ensureSheetStructure(config);
    var ss = info.ss;
    var sheet = info.sheet;
    var headers = getSheetHeaders();
    var lastRow = Math.max(2, sheet.getLastRow());
    if (lastRow > 1) sheet.getRange(2, 1, lastRow - 1, headers.length).clearContent();
    if (valueBets && valueBets.length) {
      var rows = valueBets.map(function (v) {
        return [
          v.sport,
          Utilities.formatDate(v.date, config.timezone, 'dd.MM.yyyy HH:mm'),
          v.league,
          v.match,
          v.market,
          v.outcome,
          v.line,
          v.odd,
          v.bookmaker,
          v.oddsSource || 'Unknown',
          v.probability,
          v.adjustedProbability,
          v.impliedProbability,
          v.valueGap,
          v.evPercent,
          v.rawEvPercent,
          v.confidence,
          v.stake,
          v.stakePctBankroll,
          v.betType || 'single',
          getTelegramCategoryKey(v),
          v.score || '',
          isProbablyExoticLeague({ league: v.league, sport: v.sportKey }, config) ? 'yes' : 'no',
          v.comment || '',
          v.formSnapshot || '',
          v.tableSnapshot || '',
          v.injuriesSnapshot || '',
          (v.predictionScore ? v.predictionScore : '') + (v.predictedTotal ? ' | Тотал ~' + v.predictedTotal : ''),
          buildFullAnalysisText(v)
        ];
      });
      sheet.getRange(2, 1, rows.length, rows[0].length).setValues(rows);
    }
    Logger.log('В таблицу записано строк: ' + valueBets.length + ', spreadsheetId=' + ss.getId());
    return true;
  } catch (e) {
    Logger.log('Ошибка записи в таблицу: ' + (e && e.stack ? e.stack : e));
    return false;
  }
}


/* ======================= V6 RATE-LIMIT PATCH ======================= */
function isOddsApiIoCoolingDown_(config) {
  var untilTs = Number(getPersistentRuntimeFlag_('ODDS_API_IO_DISABLED_UNTIL_TS') || 0);
  if (!untilTs) return false;
  if (Date.now() > untilTs) {
    clearPersistentRuntimeFlag_('ODDS_API_IO_DISABLED_UNTIL_TS');
    return false;
  }
  return true;
}

function markOddsApiIoCoolingDown_(minutes, reason) {
  var mins = Math.max(5, Number(minutes || 25));
  var untilTs = Date.now() + mins * 60 * 1000;
  setPersistentRuntimeFlag_('ODDS_API_IO_DISABLED_UNTIL_TS', untilTs);
  Logger.log('Odds-API.io временно отключён на ' + mins + ' мин. Причина: ' + (reason || 'rate_limit'));
}

function parseOddsApiIoRetryMinutes_(text) {
  var raw = String(text || '');
  var m = raw.match(/resets in\s+(\d+)\s+minutes?\s+and\s+(\d+)\s+seconds?/i);
  if (m) return Math.max(5, Number(m[1] || 0) + 1);
  m = raw.match(/resets in\s+(\d+)\s+minutes?/i);
  if (m) return Math.max(5, Number(m[1] || 0) + 1);
  return 25;
}

function fetchOddsApiIoChunk(config, sportKey, ids, idToMatch, result, bookmakerState) {
  if (!ids || !ids.length) return 0;
  if (isOddsApiIoCoolingDown_(config)) {
    if (bookmakerState) bookmakerState.rateLimited = true;
    Logger.log('Odds-API.io odds ' + sportKey + ' skipped: источник ещё в cooldown');
    return 0;
  }
  var attempts = 0;

  while (attempts < 4) {
    var params = {
      apiKey: config.oddsApiIoKey,
      eventIds: ids.join(',')
    };
    if (bookmakerState && bookmakerState.list && bookmakerState.list.length) {
      params.bookmakers = bookmakerState.list.join(',');
    }

    var url = 'https://api.odds-api.io/v3/odds/multi?' + buildQuery(params);
    var meta = fetchJsonMeta(url, {}, 'Odds-API.io odds ' + sportKey, { config: config, sportKey: sportKey, cost: 1 }, config.cacheSeconds || 0);

    if (meta && meta.status >= 200 && meta.status < 300) {
      var rows = Array.isArray(meta.data) ? meta.data : [];
      var parsed = 0;
      rows.forEach(function (eventOdds) {
        var eventId = String(eventOdds.id || '');
        var match = idToMatch[eventId];
        if (!match) return;
        result[match.matchKey] = parseOddsApiIoEventToMarkets(eventOdds, match);
        parsed += 1;
      });
      return parsed;
    }

    if (meta && meta.fetchError) {
      if (ids.length > 1) {
        var mid = Math.ceil(ids.length / 2);
        Logger.log('Odds-API.io odds ' + sportKey + ' chunk fetch failed; splitting chunk ' + ids.length + ' -> ' + mid + '+' + (ids.length - mid));
        return fetchOddsApiIoChunk(config, sportKey, ids.slice(0, mid), idToMatch, result, bookmakerState) +
               fetchOddsApiIoChunk(config, sportKey, ids.slice(mid), idToMatch, result, bookmakerState);
      }
      Logger.log('Odds-API.io odds ' + sportKey + ' single event fetch failed for eventId=' + ids[0] + '; skipped');
      return 0;
    }

    if (meta && meta.status === 429) {
      if (bookmakerState) bookmakerState.rateLimited = true;
      var retryMins = parseOddsApiIoRetryMinutes_(meta && meta.text);
      markOddsApiIoCoolingDown_(retryMins, sportKey);
      Logger.log('Odds-API.io odds ' + sportKey + ' rate limit hit; stopping further requests for this run');
      return 0;
    }

    var invalidBook = parseOddsApiIoInvalidBookmaker(meta && meta.text);
    if (meta && meta.status === 400 && invalidBook && bookmakerState && bookmakerState.list && bookmakerState.list.length) {
      if (removeBookmakerName(bookmakerState.list, invalidBook)) {
        Logger.log('Odds-API.io bookmaker removed after validation error: ' + invalidBook + '. Remaining: ' + (bookmakerState.list.length ? bookmakerState.list.join(',') : 'ALL'));
        attempts += 1;
        continue;
      }
    }
    return 0;
  }
  return 0;
}

function getOddsApiIoFeed(config, matches) {
  if (!config.oddsApiIoKey) return {};
  if (isOddsApiIoCoolingDown_(config)) {
    Logger.log('Odds-API.io odds skipped: источник ещё в cooldown после предыдущего 429');
    return {};
  }
  var matchesBySport = {};
  var idToMatch = {};

  matches.forEach(function (match) {
    if (!match.oddsEventId) return;
    if (!matchesBySport[match.sport]) matchesBySport[match.sport] = [];
    matchesBySport[match.sport].push(match);
    idToMatch[String(match.oddsEventId)] = match;
  });

  Object.keys(matchesBySport).forEach(function (sportKey) {
    var seen = {};
    matchesBySport[sportKey] = sortOddsApiIoMatchesForRequest(matchesBySport[sportKey], config).filter(function (match) {
      var eventId = String(match.oddsEventId || '');
      if (!eventId || seen[eventId]) return false;
      seen[eventId] = true;
      return true;
    });
  });

  Logger.log('Odds-API.io idsBySport: ' + JSON.stringify(Object.keys(matchesBySport).reduce(function (acc, k) {
    acc[k] = matchesBySport[k].length;
    return acc;
  }, {})) + '; bookmakers=' + (config.bookmakers || []).join(','));

  var result = {};
  var bookmakerState = { list: (config.bookmakers || []).slice() };
  Object.keys(matchesBySport).forEach(function (sportKey) {
    var sportMatches = matchesBySport[sportKey] || [];
    if (!sportMatches.length) return;

    var totalIds = sportMatches.length;
    var desiredCoverage = getDesiredOddsCoverageCount(totalIds, config);
    var initialIdsCount = Math.max(10, Math.min(totalIds, Math.round(totalIds * clamp(config.oddsApiIoOddsInitialFetchShare || 0.68, 0.35, 1))));
    var step = Math.max(10, config.oddsApiIoOddsExpansionStep || 20);
    var fetched = 0;
    var parsedForSport = 0;

    function requestRange(fromIdx, toExclusive) {
      var ids = sportMatches.slice(fromIdx, toExclusive).map(function (m) { return String(m.oddsEventId); });
      var chunks = chunkArray(ids, 10);
      for (var ci = 0; ci < chunks.length; ci++) {
        if (bookmakerState.rateLimited) break;
        parsedForSport += fetchOddsApiIoChunk(config, sportKey, chunks[ci], idToMatch, result, bookmakerState);
      }
      fetched = toExclusive;
    }

    requestRange(0, initialIdsCount);

    while (!bookmakerState.rateLimited && parsedForSport < desiredCoverage && fetched < totalIds) {
      requestRange(fetched, Math.min(totalIds, fetched + step));
      if (fetched >= totalIds) break;
    }

    Logger.log('Odds-API.io odds ' + sportKey + ': parsed=' + parsedForSport + ', requested=' + fetched + '/' + totalIds + ', desiredCoverage=' + desiredCoverage);
  });

  Logger.log('Odds-API.io odds parsed: ' + Object.keys(result).length);
  return result;
}

// ===== v7 hotfix: graceful publication tiers when API-Football context is sparse =====
function getEnhancedRuntimeConfig(config) {
  var clone = Object.assign({}, config || {});
  clone.publishWindowHours = 48;
  clone.finalTopLimit = 5;
  clone.finalMaxPerLeague = 2;
  clone.finalMaxPerMatch = 1;
  clone.rejectBookiesOnly = true;
  clone.disableSameMatchCombos = true;
  clone.strictSoccerTotals = true;
  clone.minPublishedConfidence = Math.max(58, Number(clone.minModelConfidence || 52));
  clone.minPublishedEdgePct = Math.max(2.6, Number(clone.minEdgePct || 1.5));
  clone.minPublishedEvPct = Math.max(1.6, Number(clone.minEvPct || 1.0));
  clone.maxStakePct = Math.min(Number(clone.maxStakePct || 0.03), 0.012);
  clone.kellyFraction = Math.min(Number(clone.kellyFraction || 0.25), 0.14);
  clone.minContextSignalsForPublication = 2;
  clone.rawProbabilityHardCap = 91;
  clone.rawProbabilityExtremeCap = 96;
  clone.minBooksForPublishedBookies = 2;
  clone.minSourcesForFallbackPublication = 2;
  clone.minBooksForFallbackPublication = 2;
  return clone;
}

function countAnalysisSignals_(bet) {
  if (!bet) return 0;
  var n = 0;
  if (bet.tableSnapshot) n += 1;
  if (bet.formSnapshot) n += 1;
  if (bet.injuriesSnapshot) n += 1;
  if (bet.predictionScore || bet.predictedTotal) n += 1;
  var factors = String(bet.analysisFactors || '');
  if (/личные встречи:/i.test(factors)) n += 1;
  if (/SStats xG:/i.test(factors)) n += 1;
  if (/внешний прогноз:/i.test(factors)) n += 1;
  if ((Number(bet.analysisBooks || 0) >= 2) || /рыночный консенсус|подтверждение по рынку|линия подтверждена/i.test(String(bet.comment || ''))) n += 1;
  return n;
}

function hasRichContextForBet_(bet) {
  return countAnalysisSignals_(bet) >= 3;
}

function hasFallbackContextForBet_(bet, config) {
  if (!bet) return false;
  var signals = countAnalysisSignals_(bet);
  var books = Number(bet.analysisBooks || 0);
  var sources = Number(bet.analysisSourceCount || 0);
  var hasPrediction = !!(bet.predictionScore || bet.predictedTotal || /SStats xG:|внешний прогноз:/i.test(String(bet.analysisFactors || '')));
  var hasMarket = books >= Number((config && config.minBooksForFallbackPublication) || 2) || /подтверждение по рынку|рыночный консенсус|линия подтверждена/i.test(String(bet.comment || ''));
  return signals >= 2 && hasPrediction && hasMarket && sources >= Number((config && config.minSourcesForFallbackPublication) || 2);
}

function buildBetExplainabilityScore(bet) {
  if (!bet) return -9999;
  var score = Number(bet.score || 0);
  score += Math.min(8, Number(bet.analysisBooks || 0) * 1.15);
  score += Math.min(4, Number(bet.analysisSourceCount || 0) * 1.0);
  score += countAnalysisSignals_(bet) * 2.8;
  if (bet.analysisNarrative) score += 2.0;
  if (String(bet.oddsSource || '') === 'OddsApiIo') score += 1.2;
  if (String(bet.oddsSource || '') === 'BookiesApi') score -= 1.2;
  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (isFinite(rawProb) && rawProb >= 90) score -= 5;
  if (!hasRichContextForBet_(bet)) score -= 2.5;
  return score;
}

function passesEnhancedBetQuality(bet, config) {
  if (!bet) return false;
  if (bet.betType === 'combo' || bet.marketFamily === 'combo') return false;
  if (!bet.date) return false;
  var startTs = bet.date.getTime ? bet.date.getTime() : new Date(bet.date).getTime();
  if (!isFinite(startTs)) return false;
  var nowTs = Date.now();
  var maxTs = nowTs + Number(config.publishWindowHours || 48) * 3600 * 1000;
  if (startTs < nowTs - 10 * 60 * 1000 || startTs > maxTs) return false;

  if (!isFinite(Number(bet.odd)) || Number(bet.odd) <= 1.01) return false;
  if (!isFinite(Number(bet.adjustedProbability)) || Number(bet.adjustedProbability) < 3 || Number(bet.adjustedProbability) > 97) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < Number(config.minPublishedConfidence || 58)) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < Number(config.minPublishedEdgePct || 2.6)) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < Number(config.minPublishedEvPct || 1.6)) return false;
  if (config.rejectBookiesOnly && betHasBookiesOnlySignal(bet)) return false;

  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (!isFinite(rawProb) || rawProb <= 1 || rawProb >= Number(config.rawProbabilityExtremeCap || 96)) return false;
  if (rawProb >= Number(config.rawProbabilityHardCap || 91) && String(bet.oddsSource || '') === 'BookiesApi') return false;

  if (String(bet.oddsSource || '') === 'BookiesApi' && Number(bet.analysisBooks || 0) < Number(config.minBooksForPublishedBookies || 2)) return false;

  if (bet.sportKey === 'soccer') {
    if (!isReasonableSoccerDrawFromBet(bet) && (bet.marketFamily === 'h2h' || bet.marketFamily === 'dnb' || bet.marketFamily === 'doubleChance' || bet.marketFamily === 'spreads')) return false;
    if (config.strictSoccerTotals && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) {
      var total = extractPredictedTotalFromBet(bet);
      if (total == null || !isFinite(total) || total < 0.8 || total > 5.5) return false;
    }
    var sstatsZeroZero = /SStats xG:\s*0(?:[\.,]0+)?\s*-\s*0(?:[\.,]0+)?/i.test(String(bet.analysisFactors || ''));
    if (sstatsZeroZero && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) return false;
  }

  return hasRichContextForBet_(bet);
}


function hasControlledLowTierH2HFallbackV12_(bet, config) {
  if (!bet || !isH2HLikeBet_(bet)) return false;
  if (detectLeagueTierV9_(bet.league, bet.sportKey) !== 'low') return false;
  if (String(bet.sportKey || '') !== 'soccer') return false;
  if (String(bet.oddsSource || '') === 'BookiesApi') return false;

  var minBooks = Number((config && config.minBooksForFallbackPublication) || 2);
  if (!hasMarketConfirmationV9_(bet, minBooks)) return false;
  if (Number(bet.analysisBooks || 0) < minBooks) return false;

  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (!isFinite(rawProb) || rawProb <= 1 || rawProb >= Number((config && config.v11LowTierH2HFallbackMaxRawProb) || 89)) return false;

  if (Number(bet.confidence || 0) < Number((config && config.v11LowTierH2HFallbackMinConfidence) || 62)) return false;
  if (Number(bet.evPercent || 0) < Number((config && config.v11LowTierH2HFallbackMinEvPct) || 7.0)) return false;
  if (Number(bet.valueGap || 0) < Number((config && config.v11LowTierH2HFallbackMinEdgePct) || 3.2)) return false;

  var text = [bet.analysisFactors, bet.analysisNarrative, bet.comment].filter(Boolean).join(' | ');
  var hasMarketText = /подтверждение по рынку|линия подтверждена|сильным букмекером|2 букмекеров|2 букмекера|двумя букмекерами/i.test(text);
  if (!hasMarketText) return false;

  var sourceCount = Number(bet.analysisSourceCount || 0);
  var hasModelNarrative = /перевес модели|по рынку: перевес модели|модель/i.test(text);
  var hasAnyContext = hasPredictiveContext_(bet) || sourceCount >= 1 || hasModelNarrative;
  if (!hasAnyContext) return false;

  return true;
}

function passesFallbackPublicationQuality_(bet, config) {
  if (!bet) return false;
  if (bet.betType === 'combo' || bet.marketFamily === 'combo') return false;
  if (!bet.date) return false;
  var startTs = bet.date.getTime ? bet.date.getTime() : new Date(bet.date).getTime();
  if (!isFinite(startTs)) return false;
  var nowTs = Date.now();
  var maxTs = nowTs + Number(config.publishWindowHours || 48) * 3600 * 1000;
  if (startTs < nowTs - 10 * 60 * 1000 || startTs > maxTs) return false;

  if (!isFinite(Number(bet.odd)) || Number(bet.odd) <= 1.01) return false;
  if (!isFinite(Number(bet.adjustedProbability)) || Number(bet.adjustedProbability) < 4 || Number(bet.adjustedProbability) > 96) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < Math.max(58, Number(config.minPublishedConfidence || 58) - 1)) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < Math.max(2.8, Number(config.minPublishedEdgePct || 2.6))) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < Math.max(1.8, Number(config.minPublishedEvPct || 1.6))) return false;
  if (config.rejectBookiesOnly && betHasBookiesOnlySignal(bet)) return false;

  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (!isFinite(rawProb) || rawProb <= 1 || rawProb >= Number(config.rawProbabilityExtremeCap || 96)) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && rawProb >= Number(config.rawProbabilityHardCap || 91)) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && Number(bet.analysisBooks || 0) < Number(config.minBooksForFallbackPublication || 2)) return false;
  if (!hasFallbackContextForBet_(bet, config)) return false;

  if (bet.sportKey === 'soccer') {
    if (!isReasonableSoccerDrawFromBet(bet) && (bet.marketFamily === 'h2h' || bet.marketFamily === 'dnb' || bet.marketFamily === 'doubleChance' || bet.marketFamily === 'spreads')) return false;
    if (config.strictSoccerTotals && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) {
      var total = extractPredictedTotalFromBet(bet);
      if (total == null || !isFinite(total) || total < 0.8 || total > 5.5) return false;
    }
    var sstatsZeroZero = /SStats xG:\s*0(?:[\.,]0+)?\s*-\s*0(?:[\.,]0+)?/i.test(String(bet.analysisFactors || ''));
    if (sstatsZeroZero && (bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts')) return false;
  }
  return true;
}

function selectBestBetsForPublication(valueBets, config) {
  var maxPerLeague = Math.max(1, Number(config.finalMaxPerLeague || 2));
  var maxPerMatch = Math.max(1, Number(config.finalMaxPerMatch || 1));
  var limit = Math.max(1, Number(config.finalTopLimit || 5));
  var byMatch = {};
  var byLeague = {};
  var out = [];
  var used = {};

  function tryAdd_(bet) {
    if (!bet || out.length >= limit) return;
    var matchKey = String(bet.matchKey || '');
    var leagueKey = String(bet.sportKey || '') + '|' + String(bet.league || '');
    var uniq = matchKey + '|' + String(bet.marketFamily || '') + '|' + String(bet.outcome || '');
    if (used[uniq]) return;
    if ((byMatch[matchKey] || 0) >= maxPerMatch) return;
    if ((byLeague[leagueKey] || 0) >= maxPerLeague) return;
    out.push(bet);
    used[uniq] = true;
    byMatch[matchKey] = (byMatch[matchKey] || 0) + 1;
    byLeague[leagueKey] = (byLeague[leagueKey] || 0) + 1;
  }

  var sorted = (valueBets || []).slice().sort(function (a, b) {
    var sa = buildBetExplainabilityScore(a);
    var sb = buildBetExplainabilityScore(b);
    if (sb !== sa) return sb - sa;
    if ((b.evPercent || 0) !== (a.evPercent || 0)) return (b.evPercent || 0) - (a.evPercent || 0);
    if ((b.valueGap || 0) !== (a.valueGap || 0)) return (b.valueGap || 0) - (a.valueGap || 0);
    return (b.confidence || 0) - (a.confidence || 0);
  });

  sorted.filter(function (bet) { return passesEnhancedBetQuality(bet, config); }).forEach(tryAdd_);
  if (out.length < limit) {
    sorted.filter(function (bet) { return !passesEnhancedBetQuality(bet, config) && passesFallbackPublicationQuality_(bet, config); }).forEach(tryAdd_);
  }
  return out;
}

function buildFullAnalysisText(v) {
  var paragraphs = [];
  if (v.tableSnapshot) paragraphs.push('Турнирная ситуация: ' + v.tableSnapshot + '.');
  if (v.formSnapshot) paragraphs.push('Текущая форма: ' + v.formSnapshot + '.');
  if (v.injuriesSnapshot) paragraphs.push('Составы и потери: ' + v.injuriesSnapshot + '.');
  if (v.analysisFactors && /личные встречи:/i.test(v.analysisFactors)) {
    var h2h = String(v.analysisFactors).split(' | ').filter(function (x) { return /личные встречи:/i.test(x); });
    if (h2h.length) paragraphs.push('Очные встречи: ' + h2h[0] + '.');
  }
  if (v.predictionScore || v.predictedTotal) {
    var xgText = 'Модельный сценарий';
    if (v.predictionScore) xgText += ': xG ' + v.predictionScore;
    if (v.predictedTotal) xgText += ', ожидаемый тотал около ' + v.predictedTotal;
    paragraphs.push(xgText + '.');
  }
  if (v.analysisFactors && /SStats xG:/i.test(v.analysisFactors) && !/SStats xG:/i.test(paragraphs.join(' '))) {
    var sxg = String(v.analysisFactors).split(' | ').filter(function (x) { return /SStats xG:/i.test(x); });
    if (sxg.length) paragraphs.push('Дополнительный xG-контекст: ' + sxg[0] + '.');
  }
  if (v.analysisFactors && /внешний прогноз:/i.test(v.analysisFactors)) {
    var ext = String(v.analysisFactors).split(' | ').filter(function (x) { return /внешний прогноз:/i.test(x); });
    if (ext.length) paragraphs.push('Внешняя модель: ' + ext[0] + '.');
  }
  if (v.analysisNarrative) paragraphs.push(v.analysisNarrative);
  if (v.comment) paragraphs.push('Подтверждение рынка: ' + v.comment + '.');
  if (!paragraphs.length) return 'Недостаточно подтверждённого контекста для полноценного матчевого разбора.';
  if (!hasRichContextForBet_(v) && hasFallbackContextForBet_(v, { minSourcesForFallbackPublication: 2, minBooksForFallbackPublication: 2 })) {
    paragraphs.push('Часть турнирного слоя недоступна по внешним API, поэтому разбор опирается на xG, внешний прогноз и подтверждение линии рынком.');
  }
  return paragraphs.join(' ');
}

// ===== v9 publication layer: stricter low-tier h2h, better totals preservation, league penalties =====
function getEnhancedRuntimeConfig(config) {
  var clone = Object.assign({}, config || {});
  clone.publicationVersion = 'v9';
  clone.publishWindowHours = 48;
  clone.finalTopLimit = 5;
  clone.finalMinShortlist = 3;
  clone.finalMaxPerLeague = 2;
  clone.finalMaxPerMatch = 1;
  clone.rejectBookiesOnly = true;
  clone.disableSameMatchCombos = true;
  clone.strictSoccerTotals = true;
  clone.minPublishedConfidence = Math.max(60, Number(clone.minModelConfidence || 52));
  clone.minPublishedEdgePct = Math.max(3.0, Number(clone.minEdgePct || 1.5));
  clone.minPublishedEvPct = Math.max(6.5, Number(clone.minEvPct || 1.0));
  clone.maxStakePct = Math.min(Number(clone.maxStakePct || 0.03), 0.012);
  clone.kellyFraction = Math.min(Number(clone.kellyFraction || 0.25), 0.14);
  clone.minContextSignalsForPublication = 2;
  clone.rawProbabilityHardCap = 94;
  clone.rawProbabilityExtremeCap = 98;
  clone.minBooksForPublishedBookies = 2;
  clone.minSourcesForFallbackPublication = 1;
  clone.minBooksForFallbackPublication = 2;
  clone.minBooksForEmergencyPublication = 2;
  clone.maxPerFamilyForPublication = 2;

  // v9 additions
  clone.v9LowTierH2HMinHardSignals = 2;
  clone.v9MidTierH2HMinHardSignals = 1;
  clone.v9TotalsNeedPredictiveContext = true;
  clone.v9LowTierPenalty = 5.0;
  clone.v9MidTierPenalty = 2.0;
  clone.v9H2HNoContextPenalty = 12.0;
  clone.v9H2HLowTierExtraPenalty = 10.0;
  return clone;
}

function normalizeLeagueTierText_(s) {
  return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function detectLeagueTierV9_(league, sport) {
  var l = normalizeLeagueTierText_(league);
  var s = normalizeLeagueTierText_(sport);

  var topPatterns = [
    'premier league',
    'championship',
    'serie a',
    'la liga',
    'bundesliga',
    'ligue 1',
    'eredivisie',
    'primeira liga',
    'super lig',
    'mls',
    'champions league',
    'europa league',
    'conference league',
    'world cup',
    'brasileirao',
    'brasileirão',
    'copa libertadores',
    'copa sudamericana',
    'campeonato brasileiro',
    'liga profesional',
    'coppa italia',
    'fa cup',
    'dfb pokal',
    'uefa',
    'nba',
    'nhl',
    'mlb',
    'euroleague'
  ];

  var midPatterns = [
    'allsvenskan',
    'superettan',
    'ekstraklasa',
    'super league',
    'first division',
    'segunda',
    'serie b',
    'liga 2',
    '2. bundesliga',
    'j league',
    'k league',
    'veikkausliiga',
    'eliteserien',
    'superliga',
    'premier division',
    'cup',
    'liga mx',
    'argentina',
    'brazil',
    'brazilian',
    'turkey',
    'belgium',
    'saudi',
    'portugal'
  ];

  var lowPatterns = [
    'tanzania',
    'kenya',
    'zambia',
    'guatemala',
    'reserve',
    'reserves',
    'u17',
    'u18',
    'u19',
    'u20',
    'u21',
    'u23',
    'women',
    "women's",
    '(ж)',
    'esoccer',
    'esports',
    'adriatic league',
    'gt leagues',
    'regional',
    'amateur',
    '2nd division',
    'third division',
    '4th division',
    '5th division'
  ];

  for (var i = 0; i < lowPatterns.length; i++) {
    if (l.indexOf(lowPatterns[i]) !== -1) return 'low';
  }
  for (var j = 0; j < topPatterns.length; j++) {
    if (l.indexOf(topPatterns[j]) !== -1) return 'top';
  }
  for (var k = 0; k < midPatterns.length; k++) {
    if (l.indexOf(midPatterns[k]) !== -1) return 'mid';
  }

  if (s === 'soccer' || s === 'football' || s === 'футбол') return 'mid';
  return 'mid';
}

function isExoticLeagueV9_(bet, config) {
  if (!bet) return false;
  if (isProbablyExoticLeague({ league: bet.league, sport: bet.sportKey }, config)) return true;
  return detectLeagueTierV9_(bet.league, bet.sportKey) === 'low';
}

function isTotalsLikeBet_(bet) {
  if (!bet) return false;
  return bet.marketFamily === 'totals' || bet.marketFamily === 'teamTotals' || bet.marketFamily === 'btts';
}

function isH2HLikeBet_(bet) {
  if (!bet) return false;
  return bet.marketFamily === 'h2h' || bet.marketFamily === 'dnb' || bet.marketFamily === 'doubleChance';
}

function isSpreadLikeBet_(bet) {
  return !!(bet && bet.marketFamily === 'spreads');
}

function hasMarketConfirmationV9_(bet, minBooks) {
  return hasStrongMarketSupport_(bet, minBooks || 2);
}

function countHardContextSignalsV9_(bet) {
  if (!bet) return 0;
  var n = 0;
  if (bet.tableSnapshot) n += 1;
  if (bet.formSnapshot) n += 1;
  if (bet.injuriesSnapshot) n += 1;
  if (bet.predictionScore || bet.predictedTotal) n += 1;
  var factors = String(bet.analysisFactors || '');
  if (/SStats xG:/i.test(factors)) n += 1;
  if (/внешний прогноз:/i.test(factors)) n += 1;
  return n;
}

function countAnalysisSignals_(bet) {
  if (!bet) return 0;
  var n = 0;
  if (bet.tableSnapshot) n += 1;
  if (bet.formSnapshot) n += 1;
  if (bet.injuriesSnapshot) n += 1;
  if (bet.predictionScore || bet.predictedTotal) n += 1;
  var factors = String(bet.analysisFactors || '');
  if (/личные встречи:/i.test(factors)) n += 1;
  if (/SStats xG:/i.test(factors)) n += 1;
  if (/внешний прогноз:/i.test(factors)) n += 1;
  if ((Number(bet.analysisBooks || 0) >= 2) || /рыночный консенсус|подтверждение по рынку|линия подтверждена|медианы рынка/i.test(String(bet.comment || ''))) n += 1;
  if (bet.analysisNarrative) n += 1;
  return n;
}

function hasRichContextForBet_(bet) {
  return countAnalysisSignals_(bet) >= 4;
}

function hasPredictiveContext_(bet) {
  if (!bet) return false;
  var text = [bet.analysisFactors, bet.analysisNarrative, bet.comment].filter(Boolean).join(' | ');
  return !!(bet.predictionScore || bet.predictedTotal || /SStats xG:|внешний прогноз:|ожидаемый тотал|xG\s/i.test(text));
}

function hasStrongMarketSupport_(bet, minBooks) {
  if (!bet) return false;
  var books = Number(bet.analysisBooks || 0);
  var needed = Number(minBooks || 2);
  if (books >= needed) return true;
  return /рыночный консенсус|подтверждение по рынку|линия подтверждена|медианы рынка/i.test(String(bet.comment || ''));
}

function hasFallbackContextForBet_(bet, config) {
  if (!bet) return false;
  var signals = countAnalysisSignals_(bet);
  var sources = Number(bet.analysisSourceCount || 0);
  var hasPrediction = hasPredictiveContext_(bet);
  var hasMarket = hasStrongMarketSupport_(bet, (config && config.minBooksForFallbackPublication) || 2);
  return signals >= 2 && hasMarket && (hasPrediction || sources >= Number((config && config.minSourcesForFallbackPublication) || 1));
}

function hasEmergencyPublicationContext_(bet, config) {
  if (!bet) return false;
  if (isExoticLeagueV9_(bet, config)) return false;
  var signals = countAnalysisSignals_(bet);
  var hasMarket = hasStrongMarketSupport_(bet, (config && config.minBooksForEmergencyPublication) || 2);
  var text = [bet.analysisFactors, bet.analysisNarrative, bet.comment].filter(Boolean).join(' | ');
  var hasExternalModel = /внешний прогноз:|ожидаемый тотал|xG|перевес модели/i.test(text);
  return signals >= 1 && hasMarket && hasExternalModel;
}

function computeLeaguePenaltyV9_(bet, config) {
  var tier = detectLeagueTierV9_(bet && bet.league, bet && bet.sportKey);
  if (tier === 'top') return 0;
  if (tier === 'mid') return Number((config && config.v9MidTierPenalty) || 2.0);
  return Number((config && config.v9LowTierPenalty) || 5.0);
}

function buildBetExplainabilityScore(bet) {
  if (!bet) return -9999;
  var score = Number(bet.score || 0);
  score += Math.min(8, Number(bet.analysisBooks || 0) * 1.15);
  score += Math.min(4, Number(bet.analysisSourceCount || 0) * 1.0);
  score += countAnalysisSignals_(bet) * 2.6;
  if (bet.analysisNarrative) score += 2.0;

  if (String(bet.oddsSource || '') === 'OddsApiIo') score += 1.2;
  if (String(bet.oddsSource || '') === 'BookiesApi') score -= 0.8;

  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (isFinite(rawProb) && rawProb >= 94) score -= 4;

  if (!hasRichContextForBet_(bet)) score -= 1.8;
  if (hasEmergencyPublicationContext_(bet, {})) score += 0.8;

  // v9 priorities
  if (isTotalsLikeBet_(bet)) score += 5;
  if (isH2HLikeBet_(bet)) score -= 2;

  score -= computeLeaguePenaltyV9_(bet, { v9MidTierPenalty: 2.0, v9LowTierPenalty: 5.0 });

  var hardSignals = countHardContextSignalsV9_(bet);
  if (isH2HLikeBet_(bet) && hardSignals === 0) score -= 12;
  if (isH2HLikeBet_(bet) && detectLeagueTierV9_(bet.league, bet.sportKey) === 'low' && hardSignals < 2) score -= 10;

  return score;
}

function passesBasePublicationWindow_(bet, config) {
  if (!bet || bet.betType === 'combo' || bet.marketFamily === 'combo' || !bet.date) return false;
  var startTs = bet.date.getTime ? bet.date.getTime() : new Date(bet.date).getTime();
  if (!isFinite(startTs)) return false;
  var nowTs = Date.now();
  var maxTs = nowTs + Number(config.publishWindowHours || 48) * 3600 * 1000;
  if (startTs < nowTs - 10 * 60 * 1000 || startTs > maxTs) return false;
  if (!isFinite(Number(bet.odd)) || Number(bet.odd) <= 1.01) return false;
  if (!isFinite(Number(bet.adjustedProbability)) || Number(bet.adjustedProbability) < 4 || Number(bet.adjustedProbability) > 97) return false;
  if (config.rejectBookiesOnly && betHasBookiesOnlySignal(bet)) return false;

  var rawProb = Number(bet.rawProbabilityModel || bet.probability || 0);
  if (!isFinite(rawProb) || rawProb <= 1 || rawProb >= Number(config.rawProbabilityExtremeCap || 98)) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && rawProb >= Number(config.rawProbabilityHardCap || 94) && Number(bet.analysisBooks || 0) < 3) return false;
  return true;
}

function passesSoccerStructureV9_(bet, config) {
  if (!bet || bet.sportKey !== 'soccer') return true;
  if (!isReasonableSoccerDrawFromBet(bet) && (isH2HLikeBet_(bet) || isSpreadLikeBet_(bet))) return false;
  if (config.strictSoccerTotals && isTotalsLikeBet_(bet)) {
    var total = extractPredictedTotalFromBet(bet);
    if (total == null || !isFinite(total) || total < 0.8 || total > 5.5) return false;
  }
  var sstatsZeroZero = /SStats xG:\s*0(?:[\.,]0+)?\s*-\s*0(?:[\.,]0+)?/i.test(String(bet.analysisFactors || ''));
  if (sstatsZeroZero && isTotalsLikeBet_(bet)) return false;
  return true;
}

function passesEnhancedBetQuality(bet, config) {
  if (!passesBasePublicationWindow_(bet, config)) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < Number(config.minPublishedConfidence || 60)) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < Number(config.minPublishedEdgePct || 3.0)) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < Number(config.minPublishedEvPct || 6.5)) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && Number(bet.analysisBooks || 0) < Number(config.minBooksForPublishedBookies || 2)) return false;
  if (!passesSoccerStructureV9_(bet, config)) return false;

  var tier = detectLeagueTierV9_(bet.league, bet.sportKey);
  var hardSignals = countHardContextSignalsV9_(bet);
  var hasMarket = hasMarketConfirmationV9_(bet, config.minBooksForPublishedBookies || 2);

  if (isTotalsLikeBet_(bet)) {
    if (!hasMarket) return false;
    if (config.v9TotalsNeedPredictiveContext && !hasPredictiveContext_(bet)) return false;
    if (tier === 'low') {
      return Number(bet.evPercent || 0) >= 10 && Number(bet.valueGap || 0) >= 4.5 && Number(bet.confidence || 0) >= 67 && hardSignals >= 2;
    }
    return hasPredictiveContext_(bet) && hardSignals >= 1;
  }

  if (isH2HLikeBet_(bet)) {
    if (!hasMarket) return false;
    if (tier === 'low') {
      return Number(bet.evPercent || 0) >= 9.5 &&
        Number(bet.valueGap || 0) >= 4.5 &&
        Number(bet.confidence || 0) >= 66 &&
        hardSignals >= Number(config.v9LowTierH2HMinHardSignals || 2) &&
        (hasRichContextForBet_(bet) || bet.tableSnapshot || bet.formSnapshot || bet.injuriesSnapshot);
    }
    if (tier === 'mid') {
      return hardSignals >= Number(config.v9MidTierH2HMinHardSignals || 1) && hasFallbackContextForBet_(bet, config);
    }
    return hardSignals >= 1 && hasFallbackContextForBet_(bet, config);
  }

  if (isSpreadLikeBet_(bet)) {
    if (!hasMarket) return false;
    if (tier === 'low') {
      return Number(bet.evPercent || 0) >= 9 &&
        Number(bet.valueGap || 0) >= 4.2 &&
        Number(bet.confidence || 0) >= 65 &&
        hardSignals >= 1;
    }
    return Number(bet.evPercent || 0) >= 7.5 &&
      Number(bet.valueGap || 0) >= 3.5 &&
      Number(bet.confidence || 0) >= 62 &&
      hardSignals >= 1;
  }

  return hasRichContextForBet_(bet);
}

function passesFallbackPublicationQuality_(bet, config) {
  if (!passesBasePublicationWindow_(bet, config)) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < 60) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < 3.0) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < 6.5) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && Number(bet.analysisBooks || 0) < Number(config.minBooksForFallbackPublication || 2)) return false;
  if (!passesSoccerStructureV9_(bet, config)) return false;

  var tier = detectLeagueTierV9_(bet.league, bet.sportKey);
  var hardSignals = countHardContextSignalsV9_(bet);
  var hasMarket = hasMarketConfirmationV9_(bet, config.minBooksForFallbackPublication || 2);

  if (!hasMarket) return false;

  if (isTotalsLikeBet_(bet)) {
    if (!hasPredictiveContext_(bet)) return false;
    if (tier === 'low') {
      return Number(bet.evPercent || 0) >= 9.5 && Number(bet.valueGap || 0) >= 4.2 && Number(bet.confidence || 0) >= 66 && hardSignals >= 2;
    }
    return hasFallbackContextForBet_(bet, config);
  }

  if (isH2HLikeBet_(bet)) {
    if (tier === 'low') return hasControlledLowTierH2HFallbackV12_(bet, config);
    if (tier === 'mid' && hardSignals < 1) return false;
    return hasFallbackContextForBet_(bet, config) && hardSignals >= 1;
  }

  if (isSpreadLikeBet_(bet)) {
    if (tier === 'low' && hardSignals < 1) return false;
    return hasFallbackContextForBet_(bet, config);
  }

  return hasFallbackContextForBet_(bet, config);
}

function passesEmergencyPublicationQuality_(bet, config) {
  if (!passesBasePublicationWindow_(bet, config)) return false;
  if (!isFinite(Number(bet.confidence)) || Number(bet.confidence) < 61) return false;
  if (!isFinite(Number(bet.valueGap)) || Number(bet.valueGap) < 3.2) return false;
  if (!isFinite(Number(bet.evPercent)) || Number(bet.evPercent) < 6.8) return false;
  if (String(bet.oddsSource || '') === 'BookiesApi' && Number(bet.analysisBooks || 0) < 2) return false;
  if (!passesSoccerStructureV9_(bet, config)) return false;

  var tier = detectLeagueTierV9_(bet.league, bet.sportKey);
  var hardSignals = countHardContextSignalsV9_(bet);

  if (isH2HLikeBet_(bet) && tier !== 'top') return false;
  if (isTotalsLikeBet_(bet) && !hasPredictiveContext_(bet)) return false;
  if (!hasEmergencyPublicationContext_(bet, config)) return false;
  if (isH2HLikeBet_(bet) && hardSignals < 1) return false;
  return true;
}

function selectBestBetsForPublication(valueBets, config) {
  var maxPerLeague = Math.max(1, Number(config.finalMaxPerLeague || 2));
  var maxPerMatch = Math.max(1, Number(config.finalMaxPerMatch || 1));
  var maxPerFamily = Math.max(1, Number(config.maxPerFamilyForPublication || 2));
  var limit = Math.max(1, Number(config.finalTopLimit || 5));
  var minTarget = Math.min(limit, Math.max(1, Number(config.finalMinShortlist || 3)));
  var byMatch = {};
  var byLeague = {};
  var byFamily = {};
  var out = [];
  var used = {};

  function tryAdd_(bet) {
    if (!bet || out.length >= limit) return;
    var matchKey = String(bet.matchKey || '');
    var leagueKey = String(bet.sportKey || '') + '|' + String(bet.league || '');
    var familyKey = String(bet.marketFamily || 'other');
    var uniq = matchKey + '|' + familyKey + '|' + String(bet.outcome || '');
    if (used[uniq]) return;
    if ((byMatch[matchKey] || 0) >= maxPerMatch) return;
    if ((byLeague[leagueKey] || 0) >= maxPerLeague) return;
    if ((byFamily[familyKey] || 0) >= maxPerFamily) return;
    out.push(bet);
    used[uniq] = true;
    byMatch[matchKey] = (byMatch[matchKey] || 0) + 1;
    byLeague[leagueKey] = (byLeague[leagueKey] || 0) + 1;
    byFamily[familyKey] = (byFamily[familyKey] || 0) + 1;
  }

  var sorted = (valueBets || []).slice().sort(function (a, b) {
    var sa = buildBetExplainabilityScore(a);
    var sb = buildBetExplainabilityScore(b);
    if (sb !== sa) return sb - sa;
    if ((b.evPercent || 0) !== (a.evPercent || 0)) return (b.evPercent || 0) - (a.evPercent || 0);
    if ((b.valueGap || 0) !== (a.valueGap || 0)) return (b.valueGap || 0) - (a.valueGap || 0);
    return (b.confidence || 0) - (a.confidence || 0);
  });

  var strictPool = sorted.filter(function (bet) { return passesEnhancedBetQuality(bet, config); });
  var fallbackPool = sorted.filter(function (bet) { return !passesEnhancedBetQuality(bet, config) && passesFallbackPublicationQuality_(bet, config); });
  var emergencyPool = sorted.filter(function (bet) {
    return !passesEnhancedBetQuality(bet, config) && !passesFallbackPublicationQuality_(bet, config) && passesEmergencyPublicationQuality_(bet, config);
  });
  Logger.log('Publication pools: strict=' + strictPool.length + ', fallback=' + fallbackPool.length + ', emergency=' + emergencyPool.length);

  strictPool.forEach(tryAdd_);
  if (out.length < limit) {
    fallbackPool.forEach(tryAdd_);
  }
  if (out.length < minTarget) {
    emergencyPool.forEach(tryAdd_);
  }
  return out;
}

function buildFullAnalysisText(v) {
  var paragraphs = [];
  if (v.tableSnapshot) paragraphs.push('Турнирная ситуация: ' + v.tableSnapshot + '.');
  if (v.formSnapshot) paragraphs.push('Текущая форма: ' + v.formSnapshot + '.');
  if (v.injuriesSnapshot) paragraphs.push('Составы и потери: ' + v.injuriesSnapshot + '.');

  if (v.analysisFactors && /личные встречи:/i.test(v.analysisFactors)) {
    var h2h = String(v.analysisFactors).split(' | ').filter(function (x) { return /личные встречи:/i.test(x); });
    if (h2h.length) paragraphs.push('Очные встречи: ' + h2h[0] + '.');
  }

  if (v.predictionScore || v.predictedTotal) {
    var xgText = 'Модельный сценарий';
    if (v.predictionScore) xgText += ': xG ' + v.predictionScore;
    if (v.predictedTotal) xgText += ', ожидаемый тотал около ' + v.predictedTotal;
    paragraphs.push(xgText + '.');
  }

  if (v.analysisFactors && /SStats xG:/i.test(v.analysisFactors) && !/SStats xG:/i.test(paragraphs.join(' '))) {
    var sxg = String(v.analysisFactors).split(' | ').filter(function (x) { return /SStats xG:/i.test(x); });
    if (sxg.length) paragraphs.push('Дополнительный xG-контекст: ' + sxg[0] + '.');
  }

  if (v.analysisFactors && /внешний прогноз:/i.test(v.analysisFactors)) {
    var ext = String(v.analysisFactors).split(' | ').filter(function (x) { return /внешний прогноз:/i.test(x); });
    if (ext.length) paragraphs.push('Внешняя модель: ' + ext[0] + '.');
  }

  if (v.analysisNarrative) paragraphs.push(v.analysisNarrative);
  if (v.comment) paragraphs.push('Подтверждение рынка: ' + v.comment + '.');

  if (!paragraphs.length) return 'Недостаточно подтверждённого контекста для полноценного матчевого разбора.';

  if (!hasRichContextForBet_(v)) {
    if (isTotalsLikeBet_(v) && hasPredictiveContext_(v)) {
      paragraphs.push('Полный турнирный слой собран не целиком, но ставка сохранена, потому что есть рабочий xG/тотальный сценарий и подтверждение линии рынком.');
    } else if (hasFallbackContextForBet_(v, { minSourcesForFallbackPublication: 1, minBooksForFallbackPublication: 2 })) {
      paragraphs.push('Часть турнирного слоя недоступна по внешним API, поэтому разбор опирается на xG, внешнюю модель и подтверждение линии рынком.');
    } else if (hasControlledLowTierH2HFallbackV12_(v, { minBooksForFallbackPublication: 2, v11LowTierH2HFallbackMinConfidence: 62, v11LowTierH2HFallbackMinEvPct: 7.0, v11LowTierH2HFallbackMinEdgePct: 3.2, v11LowTierH2HFallbackMaxRawProb: 89 })) {
      paragraphs.push('Полный турнирный слой собран не полностью, но ставка сохранена как ограниченный market-backed fallback: есть подтверждение линии минимум от двух букмекеров и устойчивый перевес модели над рынком.');
    } else if (hasEmergencyPublicationContext_(v, { minBooksForEmergencyPublication: 2 })) {
      paragraphs.push('Полный турнирный контекст собран не полностью, поэтому ставка допущена как ограниченный fallback-вариант по совокупности модели и рыночного подтверждения.');
    }
  }

  return paragraphs.join(' ');
}

function sendTelegram(valueBets, config) {
  var text = '';
  var count = (valueBets || []).length;
  if (!count) {
    text = '⚠️ <b>На ближайшие 48 часов не найдено ставок, прошедших фильтр полного анализа</b>\n\n' +
      'v9 отсеял слабые h2h-варианты из низких лиг, ставки без достаточного рыночного подтверждения и сценарии с неполным предиктивным контекстом.';
    postTelegram(config.telegramToken, config.telegramChatId, text);
    return;
  }
  text += '🔥 <b>' + count + ' лучших валуйных ставок на ближайшие 48 часов</b>\n\n';
  text += 'В выдачу попадают только одиночные ставки с подтверждённым матчевым разбором: таблица, форма, xG/тотал, очные встречи, составы/потери и рыночное подтверждение.\n';
  text += 'На один матч — не более одной ставки. Мультиставки внутри одного матча отключены как слишком коррелированные.\n\n';
  valueBets.slice(0, Number(config.finalTopLimit || 5)).forEach(function (v, index) {
    text += formatTelegramBetHuman(v, index + 1, config.timezone) + '\n\n';
  });
  if (count < Number(config.finalTopLimit || 5)) {
    text += 'ℹ️ <i>Ставок меньше пяти, потому что v9 жёстче режет fallback-кандидатов и слабые низколиговые исходы.</i>';
  }
  postTelegram(config.telegramToken, config.telegramChatId, text);
}


/* ======================= V10 EXECUTION-GUARD PATCH ======================= */
function isNearExecutionDeadline_(config, reserveMs) {
  var started = Number(config && config.runtimeStartedAtMs || 0);
  var softLimit = Number(config && config.runtimeSoftLimitMs || 320000);
  var reserve = Number(reserveMs != null ? reserveMs : (config && config.runtimeReserveMs) || 25000);
  if (!started || !softLimit) return false;
  return (Date.now() - started) >= Math.max(10000, softLimit - reserve);
}

function getEnhancedRuntimeConfig(config) {
  var clone = Object.assign({}, config || {});
  clone.publicationVersion = 'v12';
  clone.publishWindowHours = 48;
  clone.finalTopLimit = 5;
  clone.finalMinShortlist = 3;
  clone.finalMaxPerLeague = 2;
  clone.finalMaxPerMatch = 1;
  clone.rejectBookiesOnly = true;
  clone.disableSameMatchCombos = true;
  clone.strictSoccerTotals = true;
  clone.minPublishedConfidence = Math.max(60, Number(clone.minModelConfidence || 52));
  clone.minPublishedEdgePct = Math.max(3.0, Number(clone.minEdgePct || 1.5));
  clone.minPublishedEvPct = Math.max(6.5, Number(clone.minEvPct || 1.0));
  clone.maxStakePct = Math.min(Number(clone.maxStakePct || 0.03), 0.012);
  clone.kellyFraction = Math.min(Number(clone.kellyFraction || 0.25), 0.14);
  clone.minContextSignalsForPublication = 2;
  clone.rawProbabilityHardCap = 94;
  clone.rawProbabilityExtremeCap = 98;
  clone.minBooksForPublishedBookies = 2;
  clone.minSourcesForFallbackPublication = 1;
  clone.minBooksForFallbackPublication = 2;
  clone.minBooksForEmergencyPublication = 2;
  clone.maxPerFamilyForPublication = 2;

  clone.v9LowTierH2HMinHardSignals = 2;
  clone.v9MidTierH2HMinHardSignals = 1;
  clone.v9TotalsNeedPredictiveContext = true;
  clone.v9LowTierPenalty = 5.0;
  clone.v9MidTierPenalty = 2.0;
  clone.v9H2HNoContextPenalty = 12.0;
  clone.v9H2HLowTierExtraPenalty = 10.0;

  clone.runtimeStartedAtMs = Date.now();
  clone.runtimeSoftLimitMs = Math.max(240000, Number(clone.runtimeSoftLimitMs || 320000));
  clone.runtimeReserveMs = Math.max(15000, Number(clone.runtimeReserveMs || 25000));

  clone.bookiesApiMaxCandidateMatches = Math.max(24, Math.min(Number(clone.bookiesApiMaxCandidateMatches || 90), 90));
  clone.bookiesApiMaxCandidateMatchesWhenCooling = Math.max(18, Math.min(Number(clone.bookiesApiMaxCandidateMatchesWhenCooling || 54), 54));
  clone.bookiesApiMaxPagesPerDay = Math.max(3, Math.min(Number(clone.bookiesApiMaxPagesPerDay || 6), 6));
  clone.bookiesApiMaxPagesPerDayWhenCooling = Math.max(2, Math.min(Number(clone.bookiesApiMaxPagesPerDayWhenCooling || 4), 4));
  clone.bookiesApiMaxMappedMatches = Math.max(10, Math.min(Number(clone.bookiesApiMaxMappedMatches || 48), 48));
  clone.bookiesApiMaxMappedMatchesWhenCooling = Math.max(8, Math.min(Number(clone.bookiesApiMaxMappedMatchesWhenCooling || 28), 28));
  clone.bookiesApiStopAfterParsedMatches = Math.max(6, Math.min(Number(clone.bookiesApiStopAfterParsedMatches || 24), 24));
  clone.bookiesApiStopAfterParsedMatchesWhenCooling = Math.max(4, Math.min(Number(clone.bookiesApiStopAfterParsedMatchesWhenCooling || 12), 12));

  clone.v11LowTierH2HFallbackMinConfidence = Math.max(62, Number(clone.v11LowTierH2HFallbackMinConfidence || 62));
  clone.v11LowTierH2HFallbackMinEvPct = Math.max(7.0, Number(clone.v11LowTierH2HFallbackMinEvPct || 7.0));
  clone.v11LowTierH2HFallbackMinEdgePct = Math.max(3.2, Number(clone.v11LowTierH2HFallbackMinEdgePct || 3.2));
  clone.v11LowTierH2HFallbackMaxRawProb = Math.min(90, Number(clone.v11LowTierH2HFallbackMaxRawProb || 89));
  return clone;
}

function getBookiesApiCandidateMatches(matches, currentExactIndex, config) {
  var out = [];
  (matches || []).forEach(function (match) {
    if (!match || match.sport !== 'soccer') return;
    if (config.bookiesApiSports && config.bookiesApiSports.length && config.bookiesApiSports.indexOf('soccer') === -1) return;
    var existing = currentExactIndex && currentExactIndex[match.matchKey] ? currentExactIndex[match.matchKey] : null;
    var hasOdds = hasAnyOffers(existing);
    if (config.bookiesApiUseForBackfillOnly) {
      if (!hasOdds || !(existing.h2h && existing.h2h.length)) out.push(match);
    } else {
      out.push(match);
    }
  });

  out.sort(function (a, b) {
    var tierA = detectLeagueTierV9_(a && a.league, a && a.sport);
    var tierB = detectLeagueTierV9_(b && b.league, b && b.sport);
    var rankA = tierA === 'top' ? 0 : (tierA === 'mid' ? 1 : 2);
    var rankB = tierB === 'top' ? 0 : (tierB === 'mid' ? 1 : 2);
    if (rankA !== rankB) return rankA - rankB;
    var aTs = a && a.date ? new Date(a.date).getTime() : 0;
    var bTs = b && b.date ? new Date(b.date).getTime() : 0;
    if (isFinite(aTs) && isFinite(bTs) && aTs !== bTs) return aTs - bTs;
    return String(a && a.league || '').localeCompare(String(b && b.league || ''));
  });

  var maxCandidates = isOddsApiIoCoolingDown_(config)
    ? Number(config.bookiesApiMaxCandidateMatchesWhenCooling || 54)
    : Number(config.bookiesApiMaxCandidateMatches || 90);
  return out.slice(0, Math.max(1, maxCandidates));
}

function getBookiesApiFeed(config, matches, currentExactIndex) {
  if (!config.bookiesApiEnabled) {
    Logger.log('BookiesAPI skipped: disabled (set BOOKIES_API_ENABLED=true or provide login+token for auto-enable)');
    return {};
  }
  if (!(config.bookiesApiToken || config.bookiesApiKey)) {
    Logger.log('BookiesAPI skipped: missing BOOKIES_API_TOKEN/BOOKIES_API_KEY');
    return {};
  }
  if (!config.bookiesApiLogin) {
    Logger.log('BookiesAPI skipped: missing BOOKIES_API_LOGIN');
    return {};
  }
  if (isNearExecutionDeadline_(config, 60000)) {
    Logger.log('BookiesAPI backfill skipped: мало времени до лимита выполнения');
    return {};
  }

  var cooling = isOddsApiIoCoolingDown_(config);
  var maxPagesPerDay = cooling ? Number(config.bookiesApiMaxPagesPerDayWhenCooling || 4) : Number(config.bookiesApiMaxPagesPerDay || 6);
  var maxMappedMatches = cooling ? Number(config.bookiesApiMaxMappedMatchesWhenCooling || 28) : Number(config.bookiesApiMaxMappedMatches || 48);
  var stopAfterParsed = cooling ? Number(config.bookiesApiStopAfterParsedMatchesWhenCooling || 12) : Number(config.bookiesApiStopAfterParsedMatches || 24);

  var candidates = getBookiesApiCandidateMatches(matches, currentExactIndex, config);
  if (!candidates.length) {
    Logger.log('BookiesAPI backfill skipped: no candidate soccer matches');
    return {};
  }
  var lookupCandidatesByDate = getBookiesApiCandidateLookupMap(candidates, config);
  var uniqueBaseDates = {};
  candidates.forEach(function (match) {
    var baseDateKey = getDateKey(match.isoDate);
    if (baseDateKey && baseDateKey !== 'nodate') uniqueBaseDates[baseDateKey] = true;
  });
  Logger.log('BookiesAPI candidate soccer matches: ' + candidates.length + ', base days=' + Object.keys(uniqueBaseDates).join(',') + ', lookup days=' + Object.keys(lookupCandidatesByDate).join(',') + ', cooling=' + cooling + ', maxPages=' + maxPagesPerDay + ', maxMapped=' + maxMappedMatches);

  var eventMap = {};
  var unmatchedSamples = 0;
  var baseUrl = String(config.bookiesApiBaseUrl || 'https://bookiesapi.com/api/get.php').replace(/\/$/, '');
  var dateKeys = Object.keys(lookupCandidatesByDate);

  for (var d = 0; d < dateKeys.length; d++) {
    var dateKey = dateKeys[d];
    if (Object.keys(eventMap).length >= maxMappedMatches) break;
    if (isNearExecutionDeadline_(config, 50000)) {
      Logger.log('BookiesAPI predatapage stopped early: nearing execution deadline');
      break;
    }
    for (var page = 1; page <= maxPagesPerDay; page++) {
      if (Object.keys(eventMap).length >= maxMappedMatches) break;
      if (isNearExecutionDeadline_(config, 50000)) {
        Logger.log('BookiesAPI predatapage stopped early on ' + dateKey + ' p=' + page + ': nearing execution deadline');
        break;
      }
      var url = baseUrl + '?' + buildQuery(getBookiesApiAuthParams(config, 'predatapage', {
        sport: normalizeBookiesApiSportName('soccer'),
        day: formatBookiesApiDay(dateKey),
        p: page
      }));
      var meta = fetchJsonMeta(url, {}, 'BookiesAPI predatapage ' + dateKey + ' p=' + page, { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds || 0);
      if (meta && meta.skippedByQuota) {
        Logger.log('BookiesAPI predatapage ' + dateKey + ' p=' + page + ' skipped by quota');
        break;
      }
      if (!meta || meta.status < 200 || meta.status >= 300 || !meta.data) break;
      var items = getBookiesApiEventList(meta.data);
      Logger.log('BookiesAPI predatapage ' + dateKey + ' p=' + page + ': raw=' + items.length);
      if (!items.length) break;
      if (page === 1) {
        items.slice(0, 3).forEach(function (sample, idx) {
          var ev = parseBookiesApiEvent(sample);
          if (!ev) return;
          Logger.log('BookiesAPI sample ' + dateKey + ' #' + (idx + 1) + ': ' + ev.home + ' vs ' + ev.away + ' @ ' + getDateKey(ev.isoDate));
        });
      }
      var matchedThisPage = 0;
      items.forEach(function (item) {
        if (Object.keys(eventMap).length >= maxMappedMatches) return;
        var event = parseBookiesApiEvent(item);
        if (!event) return;
        var match = matchBookiesEventToCandidate(event, lookupCandidatesByDate[dateKey] || candidates);
        if (!match) {
          if (unmatchedSamples < 8) {
            unmatchedSamples += 1;
            Logger.log('BookiesAPI unmatched sample [' + dateKey + ']: ' + event.home + ' vs ' + event.away + ' @ ' + (event.isoDate || 'nodate') + ' league=' + (event.league || ''));
          }
          return;
        }
        eventMap[match.matchKey] = { gameId: event.gameId, raw: item, event: event };
        matchedThisPage += 1;
      });
      Logger.log('BookiesAPI predatapage ' + dateKey + ' p=' + page + ': matched=' + matchedThisPage + ', cumulative=' + Object.keys(eventMap).length);
      if (items.length < Number(config.bookiesApiPageLimit || 50)) break;
    }
  }

  var result = {};
  var noOddsSamples = 0;
  var matchKeys = Object.keys(eventMap);
  for (var i = 0; i < matchKeys.length; i++) {
    if (Object.keys(result).length >= stopAfterParsed) {
      Logger.log('BookiesAPI allodds stopped early: enough parsed matches for publication (' + Object.keys(result).length + ')');
      break;
    }
    if (isNearExecutionDeadline_(config, 35000)) {
      Logger.log('BookiesAPI allodds stopped early: nearing execution deadline');
      break;
    }
    var matchKey = matchKeys[i];
    var gameId = eventMap[matchKey].gameId;
    var url2 = baseUrl + '?' + buildQuery(getBookiesApiAuthParams(config, config.bookiesApiOddsTask || 'allodds', { game_id: gameId }));
    var meta2 = fetchJsonMeta(url2, {}, 'BookiesAPI ' + (config.bookiesApiOddsTask || 'allodds') + ' ' + gameId, { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds || 0);
    if (meta2 && meta2.skippedByQuota) {
      Logger.log('BookiesAPI ' + (config.bookiesApiOddsTask || 'allodds') + ' ' + gameId + ' skipped by quota');
      continue;
    }
    if (!meta2 || meta2.status < 200 || meta2.status >= 300 || !meta2.data) continue;
    var match = null;
    for (var m = 0; m < matches.length; m++) if (matches[m].matchKey === matchKey) { match = matches[m]; break; }
    if (!match) continue;
    var parsed = parseBookiesApiOddsPayload(meta2.data, match);
    if (!hasAnyOffers(parsed) && match.sport === 'soccer' && String(config.bookiesApiOddsTask || 'allodds').toLowerCase() === 'allodds' && !isNearExecutionDeadline_(config, 35000)) {
      var fallbackUrl = baseUrl + '?' + buildQuery(getBookiesApiAuthParams(config, 'odds', { game_id: gameId }));
      var fallbackMeta = fetchJsonMeta(fallbackUrl, {}, 'BookiesAPI odds fallback ' + gameId, { config: config, sportKey: 'soccer', cost: 1 }, config.cacheSeconds || 0);
      if (fallbackMeta && !fallbackMeta.skippedByQuota && fallbackMeta.status >= 200 && fallbackMeta.status < 300 && fallbackMeta.data) {
        var fallbackParsed = parseBookiesApiOddsPayload(fallbackMeta.data, match);
        if (hasAnyOffers(fallbackParsed)) parsed = fallbackParsed;
      }
    }
    if (!hasAnyOffers(parsed)) {
      if (noOddsSamples < 5) {
        noOddsSamples += 1;
        Logger.log('BookiesAPI no parsed offers for game_id=' + gameId + ' match=' + match.matchKey + ' body=' + String(meta2.text || '').slice(0, 220));
      }
      continue;
    }
    result[matchKey] = parsed;
  }
  Logger.log('BookiesAPI backfill parsed matches: ' + Object.keys(result).length + ' / mapped=' + Object.keys(eventMap).length);
  return result;
}

function sendTelegram(valueBets, config) {
  var text = '';
  var count = (valueBets || []).length;
  if (!count) {
    text = '⚠️ <b>На ближайшие 48 часов не найдено ставок, прошедших фильтр полного анализа</b>\n\n' +
      'v12 сохраняет time-guard из v10 и исправляет логический блокер low-tier market-backed fallback: такие h2h теперь могут дойти до shortlist, если рынок подтверждён и модель даёт устойчивый перевес.';
    postTelegram(config.telegramToken, config.telegramChatId, text);
    return;
  }
  text += '🔥 <b>' + count + ' лучших валуйных ставок на ближайшие 48 часов</b>\n\n';
  text += 'В выдачу попадают только одиночные ставки с подтверждённым матчевым разбором: таблица, форма, xG/тотал, очные встречи, составы/потери и рыночное подтверждение.\n';
  text += 'v12 останавливает тяжёлый backfill раньше, если источник словил rate-limit или скрипт подходит к лимиту выполнения, и больше не режет low-tier market-backed h2h из-за логического конфликта в fallback-гейте.\n';
  text += 'На один матч — не более одной ставки. Мультиставки внутри одного матча отключены как слишком коррелированные.\n\n';
  valueBets.slice(0, Number(config.finalTopLimit || 5)).forEach(function (v, index) {
    text += formatTelegramBetHuman(v, index + 1, config.timezone) + '\n\n';
  });
  if (count < Number(config.finalTopLimit || 5)) {
    text += 'ℹ️ <i>Ставок меньше пяти, потому что v12 по-прежнему экономит дорогой backfill, но больше не режет low-tier market-backed h2h из-за логического конфликта в fallback-гейте.</i>';
  }
  postTelegram(config.telegramToken, config.telegramChatId, text);
}
