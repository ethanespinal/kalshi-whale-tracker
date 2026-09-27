import re
from datetime import date

from names import same_name


def classify_market(market):
    lower = market.lower()

    if re.search(r"\bspread:", lower):
        return "spread"

    if "o/u" in lower or "over/under" in lower:
        return "total"

    if " vs " in lower or " vs. " in lower:
        return "game_winner"

    if re.fullmatch(r"Will\s+.+?\s+win\s+on\s+\d{4}-\d{2}-\d{2}\?", market, re.IGNORECASE):
        return "game_winner"

    return "unknown"


def parse_market_info(market, side):
    market_type = classify_market(market)

    result = {
        "type": market_type,
        "market_scope": "full_game",
        "team1": None,
        "team2": None,
        "line": None,
        "market_date": None,
        "yes_no_proposition": False,
    }

    # -------------------------
    # SPREAD
    # Spread: Ohio State (-27.5)
    # -------------------------
    if market_type == "spread":
        match = re.search(
            r"Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)",
            market,
            re.IGNORECASE
        )

        if match:
            listed_team = match.group(1).strip()
            line = float(match.group(2))

            result["team1"] = listed_team
            result["line"] = line

            # The alert side may give us the opponent
            if not same_name(side, listed_team) and side.lower() not in ("yes", "no"):
                result["team2"] = side

        return result

    # -------------------------
    # TOTAL
    # Falcons vs Packers: O/U 40.5
    # -------------------------
    if market_type == "total":
        team_total = re.search(
            r"^(?:[^:]+:\s*)?(.+?)\s+Team Total:\s*(?:O/U|Over/Under)\s*"
            r"(\d+(?:\.\d+)?)(?![\d.])",
            market, re.IGNORECASE)
        if team_total:
            result["market_scope"] = "team_total"
            result["team1"] = team_total.group(1).strip()
            result["line"] = float(team_total.group(2))
            return result

        cleaned = re.sub(r"\s+vs\.?\s+", " vs ", market, flags=re.IGNORECASE)

        matchup, _, total_part = cleaned.rpartition(":")
        matchup = matchup.rsplit(":", 1)[-1].strip()

        if " vs " in matchup:
            team1, team2 = matchup.split(" vs ", 1)

            result["team1"] = team1.strip()
            result["team2"] = team2.strip()

        line_match = re.search(
            r"(?:O/U|Over/Under)\s*(\d+(?:\.\d+)?)(?![\d.])",
            total_part,
            re.IGNORECASE
        )

        if line_match:
            result["line"] = float(line_match.group(1))

        return result

    # -------------------------
    # NORMAL MATCHUP
    # -------------------------
    if market_type == "game_winner":
        if re.search(r":\s*(?:1H|1st Half|First Half)\s+Moneyline\s*$",
                     market, re.IGNORECASE):
            result["market_scope"] = "first_half"
        proposition = re.fullmatch(
            r"Will\s+(.+?)\s+win\s+on\s+(\d{4}-\d{2}-\d{2})\?",
            market,
            re.IGNORECASE
        )
        if proposition:
            try:
                parsed_date = date.fromisoformat(proposition.group(2))
            except ValueError:
                result["type"] = "unknown"
                return result
            subject = proposition.group(1).strip()
            if subject and side.lower() in ("yes", "no"):
                result["team1"] = subject
                result["market_date"] = parsed_date.isoformat()
                result["yes_no_proposition"] = True
            return result

        cleaned = re.sub(r"\s+vs\.?\s+", " vs ", market, flags=re.IGNORECASE)

        if " vs " not in cleaned:
            return result

        before_vs, after_vs = cleaned.split(" vs ", 1)

        # Remove prefix:
        # Counter-Strike: magic
        # Buenos Aires 2: Player
        if ":" in before_vs:
            before_vs = before_vs.split(":")[-1]

        # Remove descriptions after matchup
        if " - " in after_vs:
            after_vs = after_vs.split(" - ")[0]

        if ":" in after_vs:
            after_vs = after_vs.split(":")[0]

        # Remove BO1 / BO3 / BO5
        after_vs = re.sub(
            r"\s*\(BO\d+\)\s*$",
            "",
            after_vs,
            flags=re.IGNORECASE
        )

        result["team1"] = before_vs.strip()
        result["team2"] = after_vs.strip()

        return result

    return result


def parse_alert(text):
    if not isinstance(text, str):
        return None
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if len(lines) < 5:
        return None

    market = lines[0].replace("📌", "").strip()

    trade_match = re.match(
        r"(.+?) bought (.+?) for \$((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*$",
        lines[1]
    )

    price_match = re.search(
        r"\$(\d+\.\d+)",
        lines[2]
    )

    win_match = re.search(
        r"([+-]?\d+)%",
        lines[3]
    )

    roi_match = re.search(
        r"([+-]?\d+)%",
        lines[4]
    )

    if not all([
        trade_match,
        price_match,
        win_match,
        roi_match
    ]):
        return None

    side = trade_match.group(2)

    if not 0 < float(price_match.group(1)) < 1:
        return None
    if not 0 <= int(win_match.group(1)) <= 100:
        return None
    if float(trade_match.group(3).replace(",", "")) <= 0:
        return None

    market_info = parse_market_info(
        market,
        side
    )

    return {
        "market": market,
        "market_type": market_info["type"],
        "market_scope": market_info["market_scope"],
        "team1": market_info["team1"],
        "team2": market_info["team2"],
        "line": market_info["line"],
        "market_date": market_info["market_date"],
        "yes_no_proposition": market_info["yes_no_proposition"],

        "trader": trade_match.group(1),
        "side": side,

        "amount": float(
            trade_match.group(3).replace(",", "")
        ),

        "price": float(
            price_match.group(1)
        ),

        "win_rate": int(
            win_match.group(1)
        ),

        "roi": int(
            roi_match.group(1)
        ),
    }
