"""Admin tool for the tournament database.

    python3 -m tools.admin_db --db data/tournament.db <group> <command> [options]

Groups: init, tournament, team, individual, player, fixture, game, sudden-death, standings,
cross, roster, validate, import, export, audit, backup. Run any of them with -h for its options.
Every change is written to the audit log under --actor (default: your user name).
"""
import argparse
import getpass
import os
import sys
from typing import List, Optional

from storage import Database, TournamentStore
from storage.errors import StorageError
from tools import admin_entities, admin_results
from tools.admin_cli import COMMANDS, Context

DEFAULT_DB = os.environ.get("REFEREE_DB", "data/tournament.db")


def build_parser() -> argparse.ArgumentParser:
    """Builds the command line: global options plus one sub-parser per command group."""
    root = argparse.ArgumentParser(prog="admin_db", description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    root.add_argument("--db", default=DEFAULT_DB, help=f"database file (default: {DEFAULT_DB})")
    root.add_argument("--actor", default=getpass.getuser(), help="name recorded in the audit log")
    groups = root.add_subparsers(dest="group", required=True)
    admin_entities.add_init_parser(groups)
    admin_entities.add_tournament_parser(groups)
    admin_entities.add_team_parser(groups)
    admin_entities.add_individual_parser(groups)
    admin_entities.add_player_parser(groups)
    admin_results.add_fixture_parser(groups)
    admin_results.add_game_parser(groups)
    admin_results.add_sudden_death_parser(groups)
    admin_results.add_report_parsers(groups)
    return root


def run(args: argparse.Namespace, store: TournamentStore) -> int:
    """Runs the command in `args` against `store` and returns the exit code.

    Changes are logged under `args.actor`.
    """
    handler = COMMANDS[(args.group, getattr(args, "cmd", None))]
    return handler(Context(store.with_actor(args.actor), args)) or 0


def main(argv: Optional[List[str]] = None) -> int:
    """Parses `argv` and runs the command; a storage error prints `error: ...` and returns 1."""
    args = build_parser().parse_args(argv)
    db = Database(args.db)
    try:
        return run(args, TournamentStore(db))
    except StorageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
