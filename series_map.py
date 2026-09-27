"""Explicit, bounded Kalshi sports-series routing."""
from names import normalize, sports_for_names


# A family may have different series for full game, partial game and team totals.
# Tickers are verified through the public Sports series endpoint; no runtime
# catalog crawl or guessed ticker construction is used by the listener.
SERIES_FAMILIES = {
    "nfl": {
        "game_winner": ("KXNFLGAME",), "spread": ("KXNFLSPREAD",),
        "total": ("KXNFLTOTAL",), "first_half_winner": ("KXNFL1H",),
        "team_total": ("KXNFLTEAMTOTAL",),
    },
    "ncaaf": {
        "game_winner": ("KXNCAAFGAME",), "spread": ("KXNCAAFSPREAD",),
        "total": ("KXNCAAFTOTAL",), "team_total": ("KXNCAAFTEAMTOTAL",),
    },
    "nba": {
        "game_winner": ("KXNBAGAME",), "spread": ("KXNBASPREAD",),
        "total": ("KXNBATOTAL",), "team_total": ("KXNBATEAMTOTAL",),
    },
    "wnba": {
        "game_winner": ("KXWNBAGAME",), "spread": ("KXWNBASPREAD",),
        "total": ("KXWNBATOTAL",), "team_total": ("KXWNBATEAMTOTAL",),
    },
    "nhl": {
        "game_winner": ("KXNHLGAME",), "spread": ("KXNHLSPREAD",),
        "total": ("KXNHLTOTAL",), "team_total": ("KXNHLTEAMTOTAL",),
    },
    "mlb": {
        "game_winner": ("KXMLBGAME",), "spread": ("KXMLBSPREAD",),
        "total": ("KXMLBTOTAL",), "team_total": ("KXMLBTEAMTOTAL",),
    },
    "atp": {
        "game_winner": ("KXATPGAME",), "spread": ("KXATPGAMESPREAD",),
        "total": ("KXATPGAMETOTAL",),
    },
    "wta": {"game_winner": ("KXWTAGAME",), "total": ("KXWTAGTOTAL",)},
    "cs2": {
        "game_winner": ("KXCS2GAME",), "spread": ("KXCS2SPREAD",),
        "total": ("KXCS2TOTALMAPS",),
    },
    "dota2": {
        "game_winner": ("KXDOTA2GAME",), "spread": ("KXDOTA2SPREAD",),
        "total": ("KXDOTA2TOTALMAPS",),
    },
    "lol": {
        "game_winner": ("KXLOLGAME",), "spread": ("KXLOLSPREAD",),
        "total": ("KXLOLTOTALMAPS",),
    },
    "valorant": {
        "game_winner": ("KXVALORANTGAME",), "spread": ("KXVALORANTSPREAD",),
        "total": ("KXVALORANTTOTALMAPS",),
    },
    "epl": {
        "game_winner": ("KXEPLGAME",), "spread": ("KXEPLSPREAD",),
        "total": ("KXEPLTOTAL",), "team_total": ("KXEPLTEAMTOTAL",),
    },
    "ucl": {
        "game_winner": ("KXUCLGAME",), "spread": ("KXUCLSPREAD",),
        "total": ("KXUCLTOTAL",), "team_total": ("KXUCLTEAMTOTAL",),
    },
    "uefa_nations": {
        "game_winner": ("KXUEFANLGAME",), "spread": ("KXUEFANLSPREAD",),
        "total": ("KXUEFANLTOTAL",), "team_total": ("KXUEFANLTEAMTOTAL",),
    },
    "uefa": {"game_winner": ("KXUEFAGAME",)},
    "world_cup": {
        "game_winner": ("KXWCGAME",), "spread": ("KXWCSPREAD",),
        "total": ("KXWCTOTAL",), "team_total": ("KXWCTEAMTOTAL",),
    },
    "intl_friendly": {
        "game_winner": ("KXINTLFRIENDLYGAME",),
        "spread": ("KXINTLFRIENDLYSPREAD",),
        "total": ("KXINTLFRIENDLYTOTAL",),
    },
    "mls": {
        "game_winner": ("KXMLSGAME",), "spread": ("KXMLSSPREAD",),
        "total": ("KXMLSTOTAL",), "team_total": ("KXMLSTEAMTOTAL",),
    },
    "soccer": {
        "game_winner": ("KXFIFAGAME",), "spread": ("KXSOCCERSPREAD", "KXFIFASPREAD"),
        "total": ("KXSOCCERTOTAL", "KXFIFATOTAL"),
    },
}

# Compatibility exports used by the manual audit script and older callers.
SPORT_SERIES = {
    sport: tuple((family.get(kind) or (None,))[0]
                 for kind in ("game_winner", "spread", "total"))
    for sport, family in SERIES_FAMILIES.items()
}
TOTAL_SERIES = {sport: family.get("total", ()) for sport, family in SERIES_FAMILIES.items()}

HINTS = (
    ("counter strike", ("cs2",)), ("cs2", ("cs2",)),
    ("dota 2", ("dota2",)), ("dota2", ("dota2",)),
    ("league of legends", ("lol",)), ("lol", ("lol",)),
    ("valorant", ("valorant",)), ("wnba", ("wnba",)),
    ("nfl", ("nfl",)), ("ncaaf", ("ncaaf",)), ("nba", ("nba",)),
    ("nhl", ("nhl",)), ("mlb", ("mlb",)),
    ("atp", ("atp",)), ("wta", ("wta",)),
    ("tennis", ("atp", "wta")), ("buenos aires", ("atp", "wta")),
    ("champions league", ("ucl",)), ("uefa nations", ("uefa_nations",)),
    ("epl", ("epl",)), ("premier league", ("epl",)),
    ("world cup", ("world_cup",)), ("international friendly", ("intl_friendly",)),
    ("mls", ("mls",)),
    ("soccer", ("epl", "ucl", "uefa_nations", "uefa", "world_cup",
                "intl_friendly", "mls", "soccer")),
)


def route_kind(alert):
    scope = alert.get("market_scope", "full_game")
    if scope == "first_half" and alert.get("market_type") == "game_winner":
        return "first_half_winner"
    if scope == "team_total" and alert.get("market_type") == "total":
        return "team_total"
    return alert.get("market_type")


def guessed_sports(alert, preferred_sports=None):
    """Infer a bounded league set without weakening participant matching."""
    text = f" {normalize(alert.get('market'))} "
    for hint, choices in HINTS:
        if f" {hint} " in text:
            return tuple(choices), f"text:{hint}"
    preferred = tuple(sport for sport in (preferred_sports or ())
                      if sport in SERIES_FAMILIES)
    if preferred:
        return preferred, "event_context"
    inferred = sports_for_names(alert.get("team1"), alert.get("team2"))
    if inferred:
        return inferred, "participant_aliases"
    return tuple(SERIES_FAMILIES), "configured_fallback"


def candidate_series(alert, preferred_sports=None):
    kind = route_kind(alert)
    if kind not in {"game_winner", "spread", "total", "first_half_winner", "team_total"}:
        return []
    sports, _ = guessed_sports(alert, preferred_sports)
    tickers = []
    for sport in sports:
        tickers.extend(SERIES_FAMILIES.get(sport, {}).get(kind, ()))
    return list(dict.fromkeys(tickers))


def series_sport(ticker):
    for sport, family in SERIES_FAMILIES.items():
        if any(ticker in tickers for tickers in family.values()):
            return sport
    return None


def tennis_series(ticker):
    return series_sport(ticker) in ("atp", "wta")
