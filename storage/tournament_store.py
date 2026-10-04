"""The public facade of the storage layer: every operation and query on the tournament database."""
from typing import Any, Dict, List, Optional, TextIO, Union

from storage.audit import Auditor
from storage.database import Database
from storage.fixtures import FixtureAdmin
from storage.games import GameLedger
from storage.models import (EntrantPair, GameRecord, NewFixture, NewIndividual, NewPlayer,
                            RankingRules, Ref, Scoring, StandingRow, TournamentSpec)
from storage.players import PlayerAdmin
from storage.reporting import RosterReport
from storage.roster_import import RosterCsv
from storage.standings_query import StandingsQuery
from storage.sudden_death import SuddenDeathLedger
from storage.tournaments import TournamentAdmin

DEFAULT_ACTOR = "system"

__all__ = ["DEFAULT_ACTOR", "Ref", "TournamentStore"]


class TournamentStore:
    """Offers every admin operation and query the referee bot needs on the tournament database.

    Every change is written to the audit log under the store's actor; use `with_actor` to
    get a store that logs as someone else. Authorisation (who may call these) belongs to the
    caller: the CLI, or the chat command layer using the configured admin list.
    The work itself is done by one small class per concern in sibling modules.

    Attributes:
        db: The shared database connection.
        actor: Name recorded in the audit log for changes made through this store.
    """

    def __init__(self, db: Database, actor: str = DEFAULT_ACTOR):
        self.db = db
        self.actor = actor
        self._auditor = Auditor(db, actor)
        self._tournaments = TournamentAdmin(db, self._auditor)
        self._players = PlayerAdmin(db, self._auditor)
        self._fixtures = FixtureAdmin(db, self._auditor)
        self._games = GameLedger(db, self._auditor)
        self._sudden_death = SuddenDeathLedger(db, self._auditor)
        self._standings = StandingsQuery(db)
        self._report = RosterReport(db)
        self._roster = RosterCsv(db, self._tournaments, self._players)

    def with_actor(self, actor: str) -> "TournamentStore":
        """Returns a store on the same database that logs its changes under `actor`."""
        return TournamentStore(self.db, actor)

    # -------------------------------------------------------------- tournaments
    def create_tournament(self, spec: TournamentSpec) -> int:
        """Creates a tournament and returns its id (see TournamentAdmin.create)."""
        return self._tournaments.create(spec)

    def get_tournament(self, ref: Ref) -> Dict[str, Any]:
        """Returns a tournament by id or name; raises NotFoundError."""
        return self._tournaments.get(ref)

    def list_tournaments(self) -> List[Dict[str, Any]]:
        """Returns all tournaments, newest year first."""
        return self._tournaments.list_all()

    def update_tournament(self, ref: Ref, **fields: Any) -> None:
        """Changes columns of a tournament (name, year, team_size, ...)."""
        self._tournaments.update(ref, fields)

    def delete_tournament(self, ref: Ref) -> None:
        """Deletes a tournament with everything in it."""
        self._tournaments.delete(ref)

    # ------------------------------------------------------- teams / individuals
    def add_team(self, tournament: Ref, name: str, country: Optional[str] = None) -> int:
        """Adds a team and returns its entrant id."""
        return self._tournaments.add_team(tournament, name, country)

    def add_individual(self, tournament: Ref, player: NewIndividual) -> int:
        """Adds a player to an individual tournament and returns the participant id."""
        return self._players.add_individual(tournament, player)

    def rename_entrant(self, tournament: Ref, entrant: Ref, new_name: str) -> None:
        """Renames a team or individual entrant."""
        self._tournaments.rename_entrant(tournament, entrant, new_name)

    def update_entrant(self, tournament: Ref, entrant: Ref, country: Optional[str]) -> None:
        """Sets the country of an entrant."""
        self._tournaments.update_entrant(tournament, entrant, country)

    def delete_entrant(self, tournament: Ref, entrant: Ref, cascade_games: bool = False) -> None:
        """Deletes a team or individual; refuses if games exist unless `cascade_games`."""
        self._tournaments.delete_entrant(tournament, entrant, cascade_games)

    def list_entrants(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the entrants with their player counts."""
        return self._tournaments.list_entrants(tournament)

    # ------------------------------------------------------------------ players
    def add_player(self, tournament: Ref, team: Ref, player: NewPlayer) -> int:
        """Adds a player to a team and returns the participant id."""
        return self._players.add_player(tournament, team, player)

    def set_captain(self, tournament: Ref, player: Ref) -> None:
        """Makes a player the captain of their team."""
        self._players.set_captain(tournament, player)

    def update_person(self, tournament: Ref, player: Ref, **fields: Any) -> None:
        """Changes full_name, country, contact or notes of a player."""
        self._players.update_person(tournament, player, fields)

    def rename_nickname(self, tournament: Ref, player: Ref, new_nickname: str) -> None:
        """Changes a player's nickname."""
        self._players.rename_nickname(tournament, player, new_nickname)

    def set_role(self, tournament: Ref, player: Ref, role: str) -> None:
        """Moves a player between "main" and "sub"."""
        self._players.set_role(tournament, player, role)

    def substitute(self, tournament: Ref, player_out: Ref, player_in: Ref) -> None:
        """Replaces a player by a teammate; the outgoing player cannot return."""
        self._players.substitute(tournament, player_out, player_in)

    def set_active(self, tournament: Ref, player: Ref, active: bool) -> None:
        """Activates or deactivates a player."""
        self._players.set_active(tournament, player, active)

    def move_player(self, tournament: Ref, player: Ref, new_team: Ref) -> None:
        """Moves a player without games to another team."""
        self._players.move_player(tournament, player, new_team)

    def remove_player(self, tournament: Ref, player: Ref, cascade_games: bool = False) -> None:
        """Deletes a player; refuses if games exist unless `cascade_games`."""
        self._players.remove_player(tournament, player, cascade_games)

    def find_player(self, tournament: Ref, nickname: str) -> Optional[Dict[str, Any]]:
        """Returns the player with this nickname, or None."""
        return self._players.find_player(tournament, nickname)

    def list_players(self, tournament: Ref, team: Optional[Ref] = None) -> List[Dict[str, Any]]:
        """Returns the players of a tournament, or of one team."""
        return self._players.list_players(tournament, team)

    # ----------------------------------------------------------------- fixtures
    def add_fixture(self, tournament: Ref, fixture: NewFixture) -> int:
        """Schedules a fixture and returns its id."""
        return self._fixtures.add(tournament, fixture)

    def update_fixture(self, fixture_id: int, **fields: Any) -> None:
        """Changes round_no, scheduled_at or status of a fixture."""
        self._fixtures.update(fixture_id, fields)

    def delete_fixture(self, fixture_id: int) -> None:
        """Deletes a fixture; its games stay."""
        self._fixtures.delete(fixture_id)

    def list_fixtures(self, tournament: Ref,
                      round_no: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns the fixtures, optionally of one round."""
        return self._fixtures.list_all(tournament, round_no)

    # -------------------------------------------------------------------- games
    def record_game(self, tournament: Ref, game: GameRecord) -> int:
        """Records one finished game atomically and returns its id (see GameLedger.record)."""
        return self._games.record(tournament, game)

    def void_game(self, game_id: int, voided: bool = True) -> None:
        """Takes a game out of the standings without deleting it."""
        self._games.void(game_id, voided)

    def delete_game(self, game_id: int) -> None:
        """Deletes a game for good."""
        self._games.delete(game_id)

    def list_games(self, tournament: Ref, include_voided: bool = False) -> List[Dict[str, Any]]:
        """Returns the games of a tournament."""
        return self._games.list_games(tournament, include_voided)

    # ------------------------------------------------------------------ results
    def standings(self, tournament: Ref, scoring: Scoring,
                  rules: Optional[RankingRules] = None) -> List[StandingRow]:
        """Returns the ranking, best first (see StandingsQuery.standings)."""
        return self._standings.standings(tournament, scoring, rules)

    def pair_score_for_game(self, game_id: int, scoring: Scoring) -> Dict[str, Any]:
        """Returns the running micro-match score of the players of a game."""
        return self._standings.pair_score_for_game(game_id, scoring)

    def player_record(self, tournament: Ref, nickname: str) -> Dict[str, int]:
        """Returns the wins, draws and losses of a player."""
        return self._standings.player_record(tournament, nickname)

    def cross_table(self, tournament: Ref, entrants: EntrantPair,
                    scoring: Scoring) -> Dict[str, Any]:
        """Returns the players of one entrant against those of another."""
        return self._standings.cross_table(tournament, entrants, scoring)

    # ------------------------------------------------------------- sudden death
    def record_sudden_death(self, tournament: Ref, winner: Ref, loser: Ref) -> int:
        """Records the winner of a sudden-death decider and returns its id."""
        return self._sudden_death.record(tournament, winner, loser)

    def delete_sudden_death(self, tournament: Ref, winner: Ref, loser: Ref) -> None:
        """Removes a recorded decider."""
        self._sudden_death.delete(tournament, winner, loser)

    def list_sudden_death(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the recorded deciders."""
        return self._sudden_death.list_all(tournament)

    # ---------------------------------------------------------------- reporting
    def team_roster(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the players of a team tournament with team and captain columns."""
        return self._report.team_roster(tournament)

    def individual_list(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the players of an individual tournament."""
        return self._report.individual_list(tournament)

    def validate(self, tournament: Ref) -> List[str]:
        """Returns the problems to fix before the start (empty list means ready)."""
        return self._report.validate(tournament)

    def audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the newest audit-log rows, newest first."""
        return self._auditor.recent(limit)

    # ---------------------------------------------------------------- import/export
    def import_roster_csv(self, tournament: Ref, source: Union[str, TextIO]) -> Dict[str, int]:
        """Loads a roster from a CSV path or handle (see RosterCsv.import_csv)."""
        return self._roster.import_csv(tournament, source)

    def export_roster_csv(self, tournament: Ref, target: TextIO) -> int:
        """Writes the roster as CSV and returns the number of players written."""
        return self._roster.export_csv(tournament, target)
