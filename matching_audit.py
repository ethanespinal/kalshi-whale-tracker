"""Read-only replay diagnostics for recent NFL and Valorant matcher failures."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from alert_parser import parse_alert
from config import Settings
from kalshi import KalshiClient
from matching import MATCHED, Matcher
from names import variants
from series_map import guessed_sports


FAILURE_STATUSES = (
    "unmatched", "unsupported", "unsupported_series", "event_not_found",
    "contract_not_found", "name_alias_miss", "line_not_found",
    "SPORT_NOT_DETECTED", "LEAGUE_NOT_DETECTED", "UNSUPPORTED_MARKET_SCOPE",
    "SERIES_NOT_CONFIGURED", "NO_EVENTS_RETURNED",
    "EVENT_PARTICIPANTS_MISMATCH", "EVENT_DATE_MISMATCH",
    "CONTRACT_NOT_FOUND", "LINE_NOT_FOUND", "AMBIGUOUS_MATCH",
)


def recent_failure_rows(database, hours=24):
    """Return one fixed recent cohort without changing its historical rows."""
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        latest = db.execute(
            "SELECT MAX(received_at) FROM alerts WHERE recovery_of_alert_id IS NULL"
        ).fetchone()[0]
        if latest is None:
            return []
        placeholders = ",".join("?" for _ in FAILURE_STATUSES)
        rows = db.execute(f"""SELECT * FROM alerts
            WHERE recovery_of_alert_id IS NULL AND received_at>=?
            AND status IN ({placeholders}) ORDER BY received_at,id""",
            (latest - hours * 3600, *FAILURE_STATUSES)).fetchall()
        selected = []
        for row in rows:
            alert = parse_alert(row["raw_text"])
            if not alert:
                continue
            leagues, source = guessed_sports(alert)
            # A configured fallback is intentionally broad and cannot classify
            # a historical row as NFL or Valorant for cohort measurement.
            if source != "configured_fallback" and (
                    "nfl" in leagues or "valorant" in leagues):
                selected.append(dict(row))
        return selected
    finally:
        db.close()


def replay_rows(rows, matcher):
    results = []
    for row in rows:
        alert = parse_alert(row["raw_text"])
        leagues, source = guessed_sports(alert)
        result = matcher.match(alert)
        results.append({
            "alert_id": row["id"], "source_key": row["source_key"],
            "original_status": row["status"], "original_reason": row["reason"],
            "parsed": {
                "market": alert.get("market"), "market_type": alert.get("market_type"),
                "participants": [alert.get("team1"), alert.get("team2")],
                "detected_sport": result.diagnostics.get("detected_sport"),
                "detected_league": result.diagnostics.get("detected_league"),
                "league_candidates": list(leagues), "detection_source": source,
                "market_scope": alert.get("market_scope"),
                "normalized_names": {
                    "team1": list(variants(alert.get("team1"))),
                    "team2": list(variants(alert.get("team2"))),
                },
            },
            "replay": asdict(result),
        })
    return results


def summary(results):
    output = {}
    for league in ("nfl", "valorant"):
        rows = [row for row in results
                if league in row["parsed"]["league_candidates"]]
        before = sum(row["original_status"] in ("matched", "MATCHED") for row in rows)
        after = sum(row["replay"]["status"] == MATCHED for row in rows)
        output[league] = {
            "total": len(rows), "matched_before": before, "matched_after": after,
            "before_rate": before / len(rows) if rows else 0,
            "after_rate": after / len(rows) if rows else 0,
        }
    return output


def print_results(results):
    for row in results:
        parsed, replay = row["parsed"], row["replay"]
        print(f"\nALERT {row['alert_id']} // {parsed['market']}")
        print(f"  parsed: type={parsed['market_type']} scope={parsed['market_scope']} "
              f"participants={parsed['participants']}")
        print(f"  detection: sport={parsed['detected_sport']} "
              f"league={parsed['detected_league']} source={parsed['detection_source']}")
        print(f"  normalized: {parsed['normalized_names']}")
        diagnostics = replay["diagnostics"]
        print(f"  series: {diagnostics.get('candidate_series_checked', [])}")
        for series in diagnostics.get("series_results", []):
            print(f"    {series['series_ticker']}: {series['events_returned']} events, "
                  f"{series['participant_matches']} participant matches, "
                  f"{series['date_mismatches']} date mismatches")
        print(f"  nearest events: {diagnostics.get('closest_candidate_events', [])}")
        print(f"  event matched / contract failed: "
              f"{diagnostics.get('event_matched_contract_failed', False)}")
        print(f"  result: {replay['status']} // {replay['reason']}")
        if replay.get("event"):
            print(f"  event: {replay['event'].get('event_ticker')} // "
                  f"{replay['event'].get('title')}")
        if replay.get("market"):
            print(f"  contract: {replay['market'].get('ticker')} // {replay.get('side')}")

    totals = summary(results)
    print("\nREPLAY SUMMARY")
    for league, values in totals.items():
        print(f"  {league.upper()}: total={values['total']} "
              f"before={values['matched_before']} ({values['before_rate']:.1%}) "
              f"after={values['matched_after']} ({values['after_rate']:.1%})")
    remaining = [row for row in results if row["replay"]["status"] != MATCHED]
    print(f"  Remaining failures: {len(remaining)}")
    for row in remaining:
        print(f"    {row['alert_id']}: {row['replay']['status']} // "
              f"{row['replay']['reason']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Settings().database)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--json", type=Path, help="Optional path for the complete audit JSON")
    args = parser.parse_args(argv)
    rows = recent_failure_rows(args.db, args.hours)
    client = KalshiClient()
    try:
        results = replay_rows(rows, Matcher(client))
    finally:
        client.close()
    print_results(results)
    if args.json:
        args.json.write_text(json.dumps({"summary": summary(results), "alerts": results},
                                        indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
