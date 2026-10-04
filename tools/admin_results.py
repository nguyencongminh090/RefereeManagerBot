"""Admin commands for the schedule, games, results and reports.

Each group has an `add_<group>_parser` function and, right below it, the handlers it registers.
"""
import sys
from typing import Any, Dict, List

from storage.models import EntrantPair, GameRecord, NewFixture
from tools.admin_cli import (Context, add_group, add_scoring_options, command, ranking_from, ref,
                             scoring_from, show)

_RESULTS = {"win": (1, 2), "loss": (2, 1), "draw": (3, 3)}
_FIXTURE_COLUMNS = ["id", "round_no", "entrant_a", "entrant_b", "scheduled_at", "status", "games"]
_GAME_COLUMNS = ["id", "played_at", "p1", "p2", "p1_result", "p2_result", "table_no", "voided"]
_STANDINGS_COLUMNS = ["rank", "name", "country", "games", "W", "D", "L", "match_pts", "points",
                      "diff"]
_TEAM_ROSTER_COLUMNS = ["team_name", "country", "captain_name", "captain_nickname", "player_name",
                        "nickname", "role", "active", "captain_contact"]
_INDIVIDUAL_COLUMNS = ["nickname", "player_name", "country", "contact", "active"]
_AUDIT_COLUMNS = ["id", "at", "actor", "action", "entity", "entity_id", "detail"]
_SUDDEN_DEATH_COLUMNS = ["id", "winner", "loser", "decided_at"]
_NO_GAMES_CELL = "-"


# ------------------------------------------------------------------ fixture
def add_fixture_parser(groups: Any) -> None:
    """Adds the `fixture` group."""
    sub = add_group(groups, "fixture", "schedule")
    p = sub.add_parser("add")
    p.add_argument("tournament")
    p.add_argument("round", type=int)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--when")
    p = sub.add_parser("list")
    p.add_argument("tournament")
    p.add_argument("--round", type=int)
    p = sub.add_parser("set")
    p.add_argument("id", type=int)
    p.add_argument("--status", choices=["open", "done", "cancelled"])
    p.add_argument("--when")
    p.add_argument("--round", type=int)
    p = sub.add_parser("delete")
    p.add_argument("id", type=int)


@command("fixture", "add")
def _fixture_add(ctx: Context) -> None:
    """Schedules a fixture between two entrants."""
    a = ctx.args
    ctx.store.add_fixture(ctx.tournament,
                          NewFixture(a.round, ref(a.a), ref(a.b), scheduled_at=a.when))


@command("fixture", "list")
def _fixture_list(ctx: Context) -> None:
    """Prints the fixtures, optionally of one round."""
    show(ctx.store.list_fixtures(ctx.tournament, ctx.args.round), _FIXTURE_COLUMNS)


@command("fixture", "set")
def _fixture_set(ctx: Context) -> None:
    """Changes the status, time or round of a fixture."""
    a = ctx.args
    given = (("status", a.status), ("scheduled_at", a.when), ("round_no", a.round))
    fields = {k: v for k, v in given if v is not None}
    ctx.store.update_fixture(a.id, **fields)


@command("fixture", "delete")
def _fixture_delete(ctx: Context) -> None:
    """Deletes a fixture."""
    ctx.store.delete_fixture(ctx.args.id)


# --------------------------------------------------------------------- game
def add_game_parser(groups: Any) -> None:
    """Adds the `game` group."""
    sub = add_group(groups, "game", "recorded games")
    p = sub.add_parser("list")
    p.add_argument("tournament")
    p.add_argument("--all", action="store_true", help="include voided games")
    p = sub.add_parser("add", help="enter a game by hand")
    p.add_argument("tournament")
    p.add_argument("p1")
    p.add_argument("p2")
    p.add_argument("result", choices=list(_RESULTS), help="result for p1")
    p.add_argument("--force", action="store_true")
    for name in ("void", "unvoid", "delete"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)


@command("game", "list")
def _game_list(ctx: Context) -> None:
    """Prints the games, voided ones only with --all."""
    show(ctx.store.list_games(ctx.tournament, include_voided=ctx.args.all), _GAME_COLUMNS)


@command("game", "add")
def _game_add(ctx: Context) -> None:
    """Records a game entered by hand."""
    a = ctx.args
    r1, r2 = _RESULTS[a.result]
    game_id = ctx.store.record_game(ctx.tournament, GameRecord(a.p1, a.p2, r1, r2, force=a.force))
    print(f"game {game_id} recorded")


@command("game", "void")
def _game_void(ctx: Context) -> None:
    """Takes a game out of the standings."""
    ctx.store.void_game(ctx.args.id)


@command("game", "unvoid")
def _game_unvoid(ctx: Context) -> None:
    """Puts a voided game back into the standings."""
    ctx.store.void_game(ctx.args.id, voided=False)


@command("game", "delete")
def _game_delete(ctx: Context) -> None:
    """Deletes a game for good."""
    ctx.store.delete_game(ctx.args.id)


# ------------------------------------------------------------- sudden death
def add_sudden_death_parser(groups: Any) -> None:
    """Adds the `sudden-death` group."""
    sub = add_group(groups, "sudden-death",
                    "sudden-death deciders between tied teams (tie-break 3)")
    p = sub.add_parser("add", help="record who won the decider")
    p.add_argument("tournament")
    p.add_argument("winner")
    p.add_argument("loser")
    p = sub.add_parser("list")
    p.add_argument("tournament")
    p = sub.add_parser("delete", help="remove a wrongly recorded decider")
    p.add_argument("tournament")
    p.add_argument("winner")
    p.add_argument("loser")


@command("sudden-death", "add")
def _sudden_death_add(ctx: Context) -> None:
    """Records the winner of a sudden-death decider."""
    ctx.store.record_sudden_death(ctx.tournament, ref(ctx.args.winner), ref(ctx.args.loser))


@command("sudden-death", "list")
def _sudden_death_list(ctx: Context) -> None:
    """Prints the recorded deciders."""
    show(ctx.store.list_sudden_death(ctx.tournament), _SUDDEN_DEATH_COLUMNS)


@command("sudden-death", "delete")
def _sudden_death_delete(ctx: Context) -> None:
    """Removes a recorded decider."""
    ctx.store.delete_sudden_death(ctx.tournament, ref(ctx.args.winner), ref(ctx.args.loser))


# ------------------------------------------------------- results and reports
def add_report_parsers(groups: Any) -> None:
    """Adds standings, cross, roster, validate, import, export, audit and backup."""
    p = groups.add_parser(
        "standings", help="ranking; '=' after a rank marks entrants no tie-break separated")
    p.add_argument("tournament")
    add_scoring_options(p)
    p = groups.add_parser("cross", help="cross table between two teams")
    p.add_argument("tournament")
    p.add_argument("a")
    p.add_argument("b")
    add_scoring_options(p)
    p = groups.add_parser("roster", help="teams with captain and players, or the list of players")
    p.add_argument("tournament")
    p = groups.add_parser("validate", help="list problems to fix before the start")
    p.add_argument("tournament")
    p = groups.add_parser("import", help="load a roster CSV")
    p.add_argument("tournament")
    p.add_argument("file")
    p = groups.add_parser("export", help="write the roster as CSV")
    p.add_argument("tournament")
    p.add_argument("file", nargs="?")
    groups.add_parser("audit").add_argument("--limit", type=int, default=30)
    groups.add_parser("backup").add_argument("dest")


def _standing_rows(ctx: Context) -> List[Dict[str, Any]]:
    rules = ranking_from(ctx.args, ctx.store.get_tournament(ctx.tournament)["format"] == "team")
    rows = []
    for s in ctx.store.standings(ctx.tournament, scoring_from(ctx.args), rules):
        row = {"rank": f"{s.rank}=" if s.tied else s.rank, "name": s.name,
               "country": s.country, "games": s.games, "W": s.wins, "D": s.draws, "L": s.losses,
               "points": f"{s.points:g}", "diff": f"{s.difference:+g}"}
        if s.match_points is not None:
            row["match_pts"] = f"{s.match_points:g}"
        rows.append(row)
    return rows


@command("standings")
def _standings(ctx: Context) -> None:
    """Prints the standings; '=' after a rank marks an unbroken tie."""
    show(_standing_rows(ctx), _STANDINGS_COLUMNS)


def _cross_cell_text(cell: Dict[str, Any]) -> str:
    if not cell["games"]:
        return _NO_GAMES_CELL
    mark = "*" if cell["complete"] else ""
    return f"{cell['points']:g}-{cell['opponent_points']:g} ({cell['games']}){mark}"


@command("cross")
def _cross(ctx: Context) -> None:
    """Prints the cross table between two teams."""
    pair = EntrantPair(ref(ctx.args.a), ref(ctx.args.b))
    table = ctx.store.cross_table(ctx.tournament, pair, scoring_from(ctx.args))
    opponents = [cell["opponent"] for cell in table["rows"][0]["cells"]] if table["rows"] else []
    rows = [dict({table["a"]: row["player"]},
                 **{cell["opponent"]: _cross_cell_text(cell) for cell in row["cells"]})
            for row in table["rows"]]
    show(rows, [table["a"], *opponents])
    print("* = micro-match complete")


@command("roster")
def _roster(ctx: Context) -> None:
    """Prints the roster of a team or individual tournament."""
    if ctx.store.get_tournament(ctx.tournament)["format"] == "team":
        show(ctx.store.team_roster(ctx.tournament), _TEAM_ROSTER_COLUMNS)
    else:
        show(ctx.store.individual_list(ctx.tournament), _INDIVIDUAL_COLUMNS)


@command("validate")
def _validate(ctx: Context) -> int:
    """Prints the problems to fix before the start; exits 1 if there are any."""
    issues = ctx.store.validate(ctx.tournament)
    print("\n".join(issues) if issues else "ready: no problems found")
    return 1 if issues else 0


@command("import")
def _import(ctx: Context) -> None:
    """Loads a roster CSV and prints the counts."""
    print(ctx.store.import_roster_csv(ctx.tournament, ctx.args.file))


@command("export")
def _export(ctx: Context) -> None:
    """Writes the roster CSV to a file or to standard output."""
    path = ctx.args.file
    if not path:
        ctx.store.export_roster_csv(ctx.tournament, sys.stdout)
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        print(f"{ctx.store.export_roster_csv(ctx.tournament, handle)} rows written to {path}")


@command("audit")
def _audit(ctx: Context) -> None:
    """Prints the newest audit-log rows."""
    show(ctx.store.audit_log(ctx.args.limit), _AUDIT_COLUMNS)


@command("backup")
def _backup(ctx: Context) -> None:
    """Copies the live database to a file."""
    ctx.store.db.backup(ctx.args.dest)
    print(f"backup written to {ctx.args.dest}")
