"""Fill a database with DEMO data (invented names, no real people) so the tools can be tried out.

    python3 -m tools.seed_demo [--db data/demo.db]

Creates a 2026 team tournament and a 2026 individual tournament. Real rosters are loaded for
each new year with `admin_db import` (see proposal.md), never from this script.
"""
import argparse
import sys

from storage        import Database, TournamentStore
from storage.models import NewFixture, NewIndividual, NewPlayer, TournamentSpec
from storage.errors import StorageError

TEAMS = {
    "Demo Alpha": ("Demoland", ["Alice Example", "Bruno Sample", "Chloe Test", "Dmitri Mock"]),
    "Demo Beta":  ("Exampleland", ["Erin Placeholder", "Farid Dummy", "Gita Fake", "Hugo Stub"]),
    "Demo Gamma": ("Sampleland", ["Ines Demo", "Jon Pretend", "Kira Sketch", "Liam Draft"]),
}
SINGLES = ["Maya Trial", "Noah Probe", "Olga Specimen", "Paul Sandbox"]


def seed(store: TournamentStore, actor: str = "seed_demo") -> None:
    """Writes the demo team and individual tournaments, logged under `actor`."""
    store = store.with_actor(actor)
    team_tid = store.create_tournament(TournamentSpec(
        "Demo Team 2026", "team", year=2026, team_size=3, max_substitutes=1, games_per_pair=12,
        nickname_prefix="wbc"))
    for team, (country, people) in TEAMS.items():
        store.add_team(team_tid, team, country)
        for i, person in enumerate(people):
            nick = "wbc" + person.split()[0].lower()
            store.add_player(team_tid, team, NewPlayer(
                person, nick, country=country, role="sub" if i == 3 else "main",
                is_captain=(i == 0), contact=f"{nick}@example.invalid" if i == 0 else None))
    store.add_fixture(team_tid, NewFixture(1, "Demo Alpha", "Demo Beta"))
    store.add_fixture(team_tid, NewFixture(1, "Demo Gamma", "Demo Alpha"))

    solo_tid = store.create_tournament(TournamentSpec(
        "Demo Individual 2026", "individual", year=2026, games_per_pair=2))
    for person in SINGLES:
        store.add_individual(solo_tid, NewIndividual(
            person, person.split()[0].lower() + "demo", country="Demoland"))


def main() -> int:
    """Seeds the database given by --db; returns 1 if it already holds the demo data."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/demo.db")
    args = parser.parse_args()
    db = Database(args.db)
    try:
        seed(TournamentStore(db))
    except StorageError as exc:
        print(f"error: {exc} (already seeded? use a fresh --db)", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"demo data written to {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
