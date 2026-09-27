"""Conservative participant identities and reviewed league aliases."""
import re
import unicodedata


def normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


# Each identity is league-qualified. This lets a nickname such as "Cardinals"
# belong to both MLB and NFL without ever equating Arizona with St. Louis.
ENTITY_ALIASES = {}
ALIAS_IDENTITIES = {}


def _register(sport, code, *aliases):
    identity = f"{sport}:{code}"
    expanded = list(aliases)
    if aliases:
        expanded.append(f"{code} {aliases[0]}")
    values = tuple(dict.fromkeys(normalize(value) for value in expanded if normalize(value)))
    ENTITY_ALIASES[identity] = values
    for value in values:
        ALIAS_IDENTITIES.setdefault(value, set()).add(identity)


# Kalshi alternates between full nicknames, city-only labels and ticker-style
# abbreviations. Ambiguous cities use the exchange's distinguishing suffix.
NFL_TEAMS = (
    ("ari", "Cardinals", "Arizona", "ARI", "Arizona Cardinals"),
    ("atl", "Falcons", "Atlanta", "ATL", "Atlanta Falcons"),
    ("bal", "Ravens", "Baltimore", "BAL", "Baltimore Ravens"),
    ("buf", "Bills", "Buffalo", "BUF", "Buffalo Bills"),
    ("car", "Panthers", "Carolina", "CAR", "Carolina Panthers"),
    ("chi", "Bears", "Chicago", "CHI", "Chicago Bears"),
    ("cin", "Bengals", "Cincinnati", "CIN", "Cincinnati Bengals"),
    ("cle", "Browns", "Cleveland", "CLE", "Cleveland Browns"),
    ("dal", "Cowboys", "Dallas", "DAL", "Dallas Cowboys"),
    ("den", "Broncos", "Denver", "DEN", "Denver Broncos"),
    ("det", "Lions", "Detroit", "DET", "Detroit Lions"),
    ("gb", "Packers", "Green Bay", "GB", "Green Bay Packers"),
    ("hou", "Texans", "Houston", "HOU", "Houston Texans"),
    ("ind", "Colts", "Indianapolis", "IND", "Indianapolis Colts"),
    ("jac", "Jaguars", "Jacksonville", "JAC", "JAX", "Jacksonville Jaguars"),
    ("kc", "Chiefs", "Kansas City", "KC", "Kansas City Chiefs"),
    ("lv", "Raiders", "Las Vegas", "LV", "Las Vegas Raiders"),
    ("lac", "Chargers", "Los Angeles C", "LA Chargers", "LAC", "Los Angeles Chargers"),
    ("lar", "Rams", "Los Angeles R", "LA Rams", "LAR", "Los Angeles Rams"),
    ("mia", "Dolphins", "Miami", "MIA", "Miami Dolphins"),
    ("min", "Vikings", "Minnesota", "MIN", "Minnesota Vikings"),
    ("ne", "Patriots", "New England", "NE", "New England Patriots"),
    ("no", "Saints", "New Orleans", "NO", "New Orleans Saints"),
    ("nyg", "Giants", "New York G", "NY Giants", "NYG", "New York Giants"),
    ("nyj", "Jets", "New York J", "NY Jets", "NYJ", "New York Jets"),
    ("phi", "Eagles", "Philadelphia", "PHI", "Philadelphia Eagles"),
    ("pit", "Steelers", "Pittsburgh", "PIT", "Pittsburgh Steelers"),
    ("sf", "49ers", "San Francisco", "SF", "San Francisco 49ers"),
    ("sea", "Seahawks", "Seattle", "SEA", "Seattle Seahawks"),
    ("tb", "Buccaneers", "Tampa Bay", "TB", "Tampa Bay Buccaneers", "Bucs"),
    ("ten", "Titans", "Tennessee", "TEN", "Tennessee Titans"),
    ("was", "Commanders", "Washington", "WAS", "Washington Commanders"),
)
for row in NFL_TEAMS:
    _register("nfl", *row)


MLB_TEAMS = (
    ("az", "Diamondbacks", "Arizona Diamondbacks", "Arizona", "AZ", "ARI D-backs"),
    ("atl", "Braves", "Atlanta Braves", "Atlanta", "ATL"),
    ("bal", "Orioles", "Baltimore Orioles", "Baltimore", "BAL"),
    ("bos", "Red Sox", "Boston Red Sox", "Boston", "BOS"),
    ("chc", "Cubs", "Chicago Cubs", "Chicago C", "CHC"),
    ("cws", "White Sox", "Chicago White Sox", "Chicago WS", "CWS"),
    ("cin", "Reds", "Cincinnati Reds", "Cincinnati", "CIN"),
    ("cle", "Guardians", "Cleveland Guardians", "Cleveland", "CLE"),
    ("col", "Rockies", "Colorado Rockies", "Colorado", "COL"),
    ("det", "Tigers", "Detroit Tigers", "Detroit", "DET"),
    ("hou", "Astros", "Houston Astros", "Houston", "HOU"),
    ("kc", "Royals", "Kansas City Royals", "Kansas City", "KC"),
    ("laa", "Angels", "Los Angeles Angels", "Los Angeles A", "LAA"),
    ("lad", "Dodgers", "Los Angeles Dodgers", "Los Angeles D", "LAD"),
    ("mia", "Marlins", "Miami Marlins", "Miami", "MIA"),
    ("mil", "Brewers", "Milwaukee Brewers", "Milwaukee", "MIL"),
    ("min", "Twins", "Minnesota Twins", "Minnesota", "MIN"),
    ("nym", "Mets", "New York Mets", "New York M", "NYM"),
    ("nyy", "Yankees", "New York Yankees", "New York Y", "NYY"),
    ("ath", "Athletics", "Oakland Athletics", "Oakland", "A's", "ATH", "OAK"),
    ("phi", "Phillies", "Philadelphia Phillies", "Philadelphia", "PHI"),
    ("pit", "Pirates", "Pittsburgh Pirates", "Pittsburgh", "PIT"),
    ("sd", "Padres", "San Diego Padres", "San Diego", "SD"),
    ("sf", "Giants", "San Francisco Giants", "San Francisco", "SF"),
    ("sea", "Mariners", "Seattle Mariners", "Seattle", "SEA"),
    ("stl", "Cardinals", "St. Louis Cardinals", "St Louis", "STL"),
    ("tb", "Rays", "Tampa Bay Rays", "Tampa Bay", "TB"),
    ("tex", "Rangers", "Texas Rangers", "Texas", "TEX"),
    ("tor", "Blue Jays", "Toronto Blue Jays", "Toronto", "TOR"),
    ("wsh", "Nationals", "Washington Nationals", "Washington", "WSH"),
)
for row in MLB_TEAMS:
    _register("mlb", *row)


NBA_TEAMS = (
    ("atl", "Hawks", "Atlanta Hawks", "ATL Hawks"), ("bos", "Celtics", "Boston Celtics", "BOS Celtics"),
    ("bkn", "Nets", "Brooklyn Nets", "BKN Nets"), ("cha", "Hornets", "Charlotte Hornets", "CHA Hornets"),
    ("chi", "Bulls", "Chicago Bulls", "CHI Bulls"), ("cle", "Cavaliers", "Cleveland Cavaliers", "CLE Cavaliers", "Cavs"),
    ("dal", "Mavericks", "Dallas Mavericks", "DAL Mavericks", "Mavs"), ("den", "Nuggets", "Denver Nuggets", "DEN Nuggets"),
    ("det", "Pistons", "Detroit Pistons", "DET Pistons"), ("gsw", "Warriors", "Golden State Warriors", "GSW Warriors"),
    ("hou", "Rockets", "Houston Rockets", "HOU Rockets"), ("ind", "Pacers", "Indiana Pacers", "IND Pacers"),
    ("lac", "Clippers", "LA Clippers", "Los Angeles Clippers", "LAC Clippers"), ("lal", "Lakers", "LA Lakers", "Los Angeles Lakers", "LAL Lakers"),
    ("mem", "Grizzlies", "Memphis Grizzlies", "MEM Grizzlies"), ("mia", "Heat", "Miami Heat", "MIA Heat"),
    ("mil", "Bucks", "Milwaukee Bucks", "MIL Bucks"), ("min", "Timberwolves", "Minnesota Timberwolves", "MIN Timberwolves", "Wolves"),
    ("nop", "Pelicans", "New Orleans Pelicans", "NOP Pelicans"), ("nyk", "Knicks", "New York Knicks", "NY Knicks", "NYK Knicks"),
    ("okc", "Thunder", "Oklahoma City Thunder", "OKC Thunder"), ("orl", "Magic", "Orlando Magic", "ORL Magic"),
    ("phi", "76ers", "Philadelphia 76ers", "PHI 76ers", "Sixers"), ("phx", "Suns", "Phoenix Suns", "PHX Suns"),
    ("por", "Trail Blazers", "Portland Trail Blazers", "POR Trail Blazers", "Blazers"), ("sac", "Kings", "Sacramento Kings", "SAC Kings"),
    ("sas", "Spurs", "San Antonio Spurs", "SA Spurs", "SAS Spurs"), ("tor", "Raptors", "Toronto Raptors", "TOR Raptors"),
    ("uta", "Jazz", "Utah Jazz", "UTA Jazz"), ("was", "Wizards", "Washington Wizards", "WAS Wizards"),
)
for row in NBA_TEAMS:
    _register("nba", *row)


WNBA_TEAMS = (
    ("atl", "Dream", "Atlanta Dream", "ATL Dream"), ("chi", "Sky", "Chicago Sky", "CHI Sky"),
    ("con", "Sun", "Connecticut Sun", "CON Sun"), ("dal", "Wings", "Dallas Wings", "DAL Wings"),
    ("gs", "Valkyries", "Golden State Valkyries", "GS Valkyries"), ("ind", "Fever", "Indiana Fever", "IND Fever"),
    ("lv", "Aces", "Las Vegas Aces", "LV Aces"), ("la", "Sparks", "Los Angeles Sparks", "LA Sparks"),
    ("min", "Lynx", "Minnesota Lynx", "MIN Lynx"), ("ny", "Liberty", "New York Liberty", "NY Liberty"),
    ("phx", "Mercury", "Phoenix Mercury", "PHX Mercury"), ("sea", "Storm", "Seattle Storm", "SEA Storm"),
    ("was", "Mystics", "Washington Mystics", "WAS Mystics"),
)
for row in WNBA_TEAMS:
    _register("wnba", *row)


NHL_TEAMS = (
    ("ana", "Ducks", "Anaheim Ducks", "ANA Ducks"), ("bos", "Bruins", "Boston Bruins", "BOS Bruins"),
    ("buf", "Sabres", "Buffalo Sabres", "BUF Sabres"), ("cgy", "Flames", "Calgary Flames", "CGY Flames"),
    ("car", "Hurricanes", "Carolina Hurricanes", "CAR Hurricanes", "Canes"), ("chi", "Blackhawks", "Chicago Blackhawks", "CHI Blackhawks"),
    ("col", "Avalanche", "Colorado Avalanche", "COL Avalanche", "Avs"), ("cbj", "Blue Jackets", "Columbus Blue Jackets", "CBJ Blue Jackets"),
    ("dal", "Stars", "Dallas Stars", "DAL Stars"), ("det", "Red Wings", "Detroit Red Wings", "DET Red Wings"),
    ("edm", "Oilers", "Edmonton Oilers", "EDM Oilers"), ("fla", "Panthers", "Florida Panthers", "FLA Panthers"),
    ("lak", "Kings", "Los Angeles Kings", "LA Kings", "LAK Kings"), ("min", "Wild", "Minnesota Wild", "MIN Wild"),
    ("mtl", "Canadiens", "Montreal Canadiens", "MTL Canadiens", "Habs"), ("nsh", "Predators", "Nashville Predators", "NSH Predators", "Preds"),
    ("njd", "Devils", "New Jersey Devils", "NJ Devils", "NJD Devils"), ("nyi", "Islanders", "New York Islanders", "NY Islanders", "NYI Islanders"),
    ("nyr", "Rangers", "New York Rangers", "NY Rangers", "NYR Rangers"), ("ott", "Senators", "Ottawa Senators", "OTT Senators", "Sens"),
    ("phi", "Flyers", "Philadelphia Flyers", "PHI Flyers"), ("pit", "Penguins", "Pittsburgh Penguins", "PIT Penguins", "Pens"),
    ("sjs", "Sharks", "San Jose Sharks", "SJ Sharks", "SJS Sharks"), ("sea", "Kraken", "Seattle Kraken", "SEA Kraken"),
    ("stl", "Blues", "St. Louis Blues", "STL Blues"), ("tbl", "Lightning", "Tampa Bay Lightning", "TB Lightning", "TBL Lightning"),
    ("tor", "Maple Leafs", "Toronto Maple Leafs", "TOR Maple Leafs", "Leafs"), ("uta", "Mammoth", "Utah Mammoth", "UTA Mammoth"),
    ("van", "Canucks", "Vancouver Canucks", "VAN Canucks"), ("vgk", "Golden Knights", "Vegas Golden Knights", "VGK Golden Knights"),
    ("wsh", "Capitals", "Washington Capitals", "WAS Capitals", "Caps"), ("wpg", "Jets", "Winnipeg Jets", "WPG Jets"),
)
for row in NHL_TEAMS:
    _register("nhl", *row)


for code, aliases in {
    "ohio_state": ("Ohio State", "Ohio State Buckeyes"), "oklahoma_state": ("Oklahoma State", "Oklahoma St"),
    "kansas_state": ("Kansas State", "Kansas St"), "kennesaw_state": ("Kennesaw State", "Kennesaw St"),
    "arkansas_state": ("Arkansas State", "Arkansas St"),
}.items():
    _register("ncaaf", code, *aliases)

for code, aliases in {
    "la_galaxy": ("Los Angeles Galaxy", "LA Galaxy"), "colorado_rapids": ("Colorado Rapids SC", "Colorado Rapids"),
    "austin": ("Austin FC", "Austin"), "san_diego": ("San Diego FC", "San Diego"),
}.items():
    _register("mls", code, *aliases)


def name_keys(name):
    value = normalize(name)
    identities = ALIAS_IDENTITIES.get(value)
    return set(identities) if identities else ({f"literal:{value}"} if value else set())


def variants(name):
    value = normalize(name)
    identities = ALIAS_IDENTITIES.get(value, ())
    values = [value]
    for identity in sorted(identities):
        values.extend(ENTITY_ALIASES[identity])
    return tuple(dict.fromkeys(item for item in values if item))


def same_name(left, right):
    return bool(name_keys(left) & name_keys(right))


def sports_for_names(*names):
    """Return conservative league guesses from reviewed participant identities."""
    supplied = [name for name in names if normalize(name)]
    sets = []
    for name in supplied:
        sports = {key.split(":", 1)[0] for key in name_keys(name)
                  if not key.startswith("literal:")}
        if sports:
            sets.append(sports)
    if not sets or (len(supplied) > 1 and len(sets) != len(supplied)):
        return ()
    common = set.intersection(*sets)
    return tuple(sorted(common))


def participant_name(left, right, tennis=False):
    if same_name(left, right):
        return True
    if not tennis:
        return False
    left, right = normalize(left).split(), normalize(right).split()
    return (len(left) >= 2 and len(right) >= 2 and left[1:] == right[1:]
            and left[0][0] == right[0][0] and min(len(left[0]), len(right[0])) == 1)


def mentions(text, name):
    text = f" {normalize(text)} "
    return any(f" {alias} " in text for alias in variants(name))
