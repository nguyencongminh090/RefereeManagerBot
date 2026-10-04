"""Shared pieces of the admin CLI: the command registry, the call context and output helpers."""
import argparse
import tomllib
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from domain.types import Scoring
from storage import RankingRules, TournamentStore
from storage.errors import StorageError
from storage.models import Ref

# A command handler returns an exit code, or None for success (0).
Handler = Callable[["Context"], Optional[int]]
COMMANDS: Dict[Tuple[str, Optional[str]], Handler] = {}


@dataclass(frozen=True)
class Context:
    """What a command handler needs.

    Attributes:
        store: The tournament store, already bound to the audit actor.
        args: The parsed command line.
    """
    store: TournamentStore
    args: argparse.Namespace

    @property
    def tournament(self) -> Ref:
        """The `tournament` argument as an id or a name."""
        return ref(self.args.tournament)


def command(group: str, name: Optional[str] = None) -> Callable[[Handler], Handler]:
    """Registers a handler for `<group> <name>` (name None for a group without sub-commands)."""
    def register(handler: Handler) -> Handler:
        COMMANDS[(group, name)] = handler
        return handler
    return register


def ref(value: str) -> Ref:
    """Turns an all-digit string into an id, anything else stays a name."""
    return int(value) if value.isdigit() else value


def add_group(groups: Any, name: str, help_text: str) -> Any:
    """Adds a command group and returns its sub-command parser collection."""
    return groups.add_parser(name, help=help_text).add_subparsers(dest="cmd", required=True)


def show(rows: Sequence[Dict[str, Any]], columns: Sequence[str]) -> None:
    """Prints rows as an aligned text table, or "(none)"."""
    if not rows:
        print("(none)")
        return
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)))


def add_scoring_options(p: argparse.ArgumentParser) -> None:
    """Adds --config, --win, --draw and --loss to a parser."""
    p.add_argument("--config", help="config.toml with a [tournament.scoring] section")
    p.add_argument("--win", type=float)
    p.add_argument("--draw", type=float)
    p.add_argument("--loss", type=float)


def scoring_from(args: argparse.Namespace) -> Scoring:
    """Reads game points from --config or from --win/--draw/--loss.

    Raises:
        StorageError: If neither source is complete.
    """
    if args.config:
        with open(args.config, "rb") as handle:
            section = tomllib.load(handle).get("tournament", {}).get("scoring")
        if not section:
            raise StorageError(f"{args.config} has no [tournament.scoring] section")
        return Scoring(float(section["win"]), float(section["draw"]), float(section["loss"]))
    if None in (args.win, args.draw, args.loss):
        raise StorageError("give --config <config.toml> or all of --win --draw --loss")
    return Scoring(args.win, args.draw, args.loss)


def ranking_from(args: argparse.Namespace, team_format: bool) -> RankingRules:
    """Tie-break order and team-match points from the config (defaults without one)."""
    if not args.config:
        return RankingRules()
    with open(args.config, "rb") as handle:
        section = tomllib.load(handle).get("tournament", {}).get("scoring", {})
    tiebreaks = tuple(section.get("tiebreaks", RankingRules().tiebreaks))
    has_match = team_format and all(k in section for k in ("match_win", "match_draw", "match_loss"))
    match = (Scoring(float(section["match_win"]), float(section["match_draw"]),
                     float(section["match_loss"]))
             if has_match else None)
    return RankingRules(tiebreaks=tiebreaks, match_points=match)
