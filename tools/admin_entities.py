"""Admin commands that change who takes part: tournaments, teams, individuals and players.

Each group has a `add_<group>_parser` function and, right below it, the handlers it registers.
"""
from typing import Any

from storage.errors import StorageError
from storage.models import NewIndividual, NewPlayer, TournamentSpec
from tools.admin_cli import Context, add_group, command, ref, show

_INT_TOURNAMENT_FIELDS = ("year", "team_size", "max_substitutes", "games_per_pair")
_NULL_WORDS = ("none", "null", "")
_TOURNAMENT_COLUMNS = ["id", "name", "year", "format", "team_size", "max_substitutes",
                       "games_per_pair", "nickname_prefix", "status"]
_PLAYER_COLUMNS = ["nickname", "full_name", "entrant_name", "country", "role", "is_captain",
                   "active", "contact"]
# Sub-commands of `player` after add/list/edit, in help order, with their positional arguments.
_PLAYER_CHANGES = (("rename", ("old", "new")), ("captain", ("nickname",)),
                   ("role", ("nickname", "role")), ("substitute", ("out", "into")),
                   ("deactivate", ("nickname",)), ("activate", ("nickname",)),
                   ("move", ("nickname", "team")), ("remove", ("nickname",)))
_INDIVIDUAL_COLUMNS = ["nickname", "player_name", "country", "contact", "active"]


# --------------------------------------------------------------------- init
def add_init_parser(groups: Any) -> None:
    """Adds the `init` command."""
    groups.add_parser("init", help="create the database file if it does not exist")


@command("init")
def _init(ctx: Context) -> None:
    """Reports that the database is ready (opening it already created the file)."""
    print(f"database ready: {ctx.args.db}")


# --------------------------------------------------------------- tournament
def add_tournament_parser(groups: Any) -> None:
    """Adds the `tournament` group."""
    sub = add_group(groups, "tournament", "tournaments (one per year and format)")
    p = sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--format", required=True, choices=["team", "individual"])
    p.add_argument("--year", type=int)
    p.add_argument("--team-size", type=int)
    p.add_argument("--max-subs", type=int, default=0)
    p.add_argument("--games-per-pair", type=int)
    p.add_argument("--nick-prefix")
    sub.add_parser("list")
    p = sub.add_parser("set")
    p.add_argument("tournament")
    p.add_argument("field")
    p.add_argument("value")
    p = sub.add_parser("delete")
    p.add_argument("tournament")
    p.add_argument("--yes", action="store_true",
                   help="confirm deleting the tournament and all its games")


@command("tournament", "add")
def _tournament_add(ctx: Context) -> None:
    """Creates a tournament from the command line."""
    a = ctx.args
    tid = ctx.store.create_tournament(TournamentSpec(
        a.name, a.format, year=a.year, team_size=a.team_size, max_substitutes=a.max_subs,
        games_per_pair=a.games_per_pair, nickname_prefix=a.nick_prefix))
    print(f"tournament {tid} created")


@command("tournament", "list")
def _tournament_list(ctx: Context) -> None:
    """Prints all tournaments."""
    show(ctx.store.list_tournaments(), _TOURNAMENT_COLUMNS)


@command("tournament", "set")
def _tournament_set(ctx: Context) -> None:
    """Sets one tournament field; none/null/blank clears it."""
    a = ctx.args
    value: Any = None if a.value.lower() in _NULL_WORDS else a.value
    if value is not None and a.field in _INT_TOURNAMENT_FIELDS:
        value = int(value)
    ctx.store.update_tournament(ctx.tournament, **{a.field: value})


@command("tournament", "delete")
def _tournament_delete(ctx: Context) -> None:
    """Deletes a tournament and its games; requires --yes."""
    if not ctx.args.yes:
        raise StorageError("this deletes the tournament and all its games; repeat with --yes")
    ctx.store.delete_tournament(ctx.tournament)


# --------------------------------------------------------------------- team
def add_team_parser(groups: Any) -> None:
    """Adds the `team` group."""
    sub = add_group(groups, "team", "teams (team format)")
    p = sub.add_parser("add")
    p.add_argument("tournament")
    p.add_argument("name")
    p.add_argument("--country")
    p = sub.add_parser("list")
    p.add_argument("tournament")
    p = sub.add_parser("rename")
    p.add_argument("tournament")
    p.add_argument("old")
    p.add_argument("new")
    p = sub.add_parser("country")
    p.add_argument("tournament")
    p.add_argument("name")
    p.add_argument("country")
    p = sub.add_parser("delete")
    p.add_argument("tournament")
    p.add_argument("name")
    p.add_argument("--cascade-games", action="store_true")


@command("team", "add")
def _team_add(ctx: Context) -> None:
    """Adds a team."""
    ctx.store.add_team(ctx.tournament, ctx.args.name, ctx.args.country)


@command("team", "list")
def _team_list(ctx: Context) -> None:
    """Prints the teams with player counts."""
    show(ctx.store.list_entrants(ctx.tournament), ["id", "name", "country", "players"])


@command("team", "rename")
def _team_rename(ctx: Context) -> None:
    """Renames a team."""
    ctx.store.rename_entrant(ctx.tournament, ref(ctx.args.old), ctx.args.new)


@command("team", "country")
def _team_country(ctx: Context) -> None:
    """Sets the country of a team."""
    ctx.store.update_entrant(ctx.tournament, ref(ctx.args.name), ctx.args.country)


@command("team", "delete")
def _team_delete(ctx: Context) -> None:
    """Deletes a team; its games only with --cascade-games."""
    ctx.store.delete_entrant(ctx.tournament, ref(ctx.args.name), ctx.args.cascade_games)


# --------------------------------------------------------------- individual
def add_individual_parser(groups: Any) -> None:
    """Adds the `individual` group."""
    sub = add_group(groups, "individual", "players of an individual tournament")
    p = sub.add_parser("add")
    p.add_argument("tournament")
    p.add_argument("full_name")
    p.add_argument("nickname")
    p.add_argument("--country")
    p.add_argument("--contact")
    p = sub.add_parser("list")
    p.add_argument("tournament")


@command("individual", "add")
def _individual_add(ctx: Context) -> None:
    """Adds a player to an individual tournament."""
    a = ctx.args
    ctx.store.add_individual(ctx.tournament,
                             NewIndividual(a.full_name, a.nickname, a.country, a.contact))


@command("individual", "list")
def _individual_list(ctx: Context) -> None:
    """Prints the players of an individual tournament."""
    show(ctx.store.individual_list(ctx.tournament), _INDIVIDUAL_COLUMNS)


# ------------------------------------------------------------------- player
def add_player_parser(groups: Any) -> None:
    """Adds the `player` group."""
    sub = add_group(groups, "player", "players (both formats)")
    p = sub.add_parser("add", help="add a player to a team")
    p.add_argument("tournament")
    p.add_argument("team")
    p.add_argument("full_name")
    p.add_argument("nickname")
    p.add_argument("--country")
    p.add_argument("--contact")
    p.add_argument("--sub", action="store_true", help="substitute instead of main player")
    p.add_argument("--captain", action="store_true")
    p = sub.add_parser("list")
    p.add_argument("tournament")
    p.add_argument("--team")
    p = sub.add_parser("edit")
    p.add_argument("tournament")
    p.add_argument("nickname")
    p.add_argument("--full-name")
    p.add_argument("--country")
    p.add_argument("--contact")
    _add_player_change_parsers(sub)


def _add_player_change_parsers(sub: Any) -> None:
    for name, positionals in _PLAYER_CHANGES:
        p = sub.add_parser(name)
        p.add_argument("tournament")
        for argument in positionals:
            p.add_argument(argument, choices=["main", "sub"] if argument == "role" else None)
        if name == "remove":
            p.add_argument("--cascade-games", action="store_true")


@command("player", "add")
def _player_add(ctx: Context) -> None:
    """Adds a player to a team, as main player or substitute."""
    a = ctx.args
    ctx.store.add_player(ctx.tournament, ref(a.team), NewPlayer(
        a.full_name, a.nickname, country=a.country, contact=a.contact,
        role="sub" if a.sub else "main", is_captain=a.captain))


@command("player", "list")
def _player_list(ctx: Context) -> None:
    """Prints the players, optionally of one team."""
    team = ref(ctx.args.team) if ctx.args.team else None
    show(ctx.store.list_players(ctx.tournament, team), _PLAYER_COLUMNS)


@command("player", "edit")
def _player_edit(ctx: Context) -> None:
    """Changes the person fields given on the command line."""
    a = ctx.args
    given = (("full_name", a.full_name), ("country", a.country), ("contact", a.contact))
    fields = {k: v for k, v in given if v is not None}
    ctx.store.update_person(ctx.tournament, a.nickname, **fields)


@command("player", "rename")
def _player_rename(ctx: Context) -> None:
    """Changes a player's nickname."""
    ctx.store.rename_nickname(ctx.tournament, ctx.args.old, ctx.args.new)


@command("player", "captain")
def _player_captain(ctx: Context) -> None:
    """Makes a player captain of their team."""
    ctx.store.set_captain(ctx.tournament, ctx.args.nickname)


@command("player", "role")
def _player_role(ctx: Context) -> None:
    """Moves a player between main and sub."""
    ctx.store.set_role(ctx.tournament, ctx.args.nickname, ctx.args.role)


@command("player", "substitute")
def _player_substitute(ctx: Context) -> None:
    """Replaces one player by a teammate."""
    ctx.store.substitute(ctx.tournament, ctx.args.out, ctx.args.into)


@command("player", "deactivate")
def _player_deactivate(ctx: Context) -> None:
    """Marks a player inactive."""
    ctx.store.set_active(ctx.tournament, ctx.args.nickname, False)


@command("player", "activate")
def _player_activate(ctx: Context) -> None:
    """Marks a player active."""
    ctx.store.set_active(ctx.tournament, ctx.args.nickname, True)


@command("player", "move")
def _player_move(ctx: Context) -> None:
    """Moves a player to another team."""
    ctx.store.move_player(ctx.tournament, ctx.args.nickname, ref(ctx.args.team))


@command("player", "remove")
def _player_remove(ctx: Context) -> None:
    """Deletes a player; their games only with --cascade-games."""
    ctx.store.remove_player(ctx.tournament, ctx.args.nickname, ctx.args.cascade_games)
