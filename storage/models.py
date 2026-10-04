"""Value objects passed to and returned from the tournament store."""
from dataclasses import dataclass

from domain.types import GameResult, Scoring
from typing      import Optional, Tuple, Union

# A tournament, entrant or player given by database id or by name/nickname.
Ref = Union[int, str]


@dataclass(frozen=True)
class RankingRules:
    """How standings are ordered. Defaults match the TGWBC 2026 rules and config example.

    Attributes:
        tiebreaks: Criteria in order of use (see storage.ranking.CRITERIA).
        match_points: Points per team match. None ranks by game points (individual format,
            or a team event where only game points count).
    """
    tiebreaks   : Tuple[str, ...]  = ("score_difference", "head_to_head", "sudden_death")
    match_points: Optional[Scoring] = None


@dataclass(frozen=True)
class StandingRow:
    """One line of the standings: an entrant's totals and final rank.

    Attributes:
        entrant_id: Database id of the team (or player in an individual event).
        name: Entrant name.
        country: Entrant country, if known.
        games: Valid games played.
        wins: Games won.
        draws: Games drawn.
        losses: Games lost.
        points: Game points scored.
        points_against: Game points conceded.
        match_points: Team-match points; None unless RankingRules.match_points is set.
        rank: Place, shared by entrants no tie-break could separate.
        tied: True if another entrant shares this rank.
    """
    entrant_id    : int
    name          : str
    country       : Optional[str]
    games         : int
    wins          : int
    draws         : int
    losses        : int
    points        : float
    points_against: float
    match_points  : Optional[float] = None   # set only when RankingRules.match_points is used
    rank          : int             = 0      # shared by entrants no tie-break could separate
    tied          : bool            = False

    @property
    def difference(self) -> float:
        """Game points scored minus game points conceded."""
        return self.points - self.points_against


@dataclass(frozen=True)
class TournamentSpec:
    """Fields of a new tournament.

    Attributes:
        name: Unique tournament name.
        fmt: "team" or "individual".
        year: Year of the event, if known.
        team_size: Main players per team; required for the team format.
        max_substitutes: Substitutes allowed per team.
        games_per_pair: Games in one micro-match; None for no limit.
        nickname_prefix: Required start of every nickname, if any.
    """
    name: str
    fmt: str
    year: Optional[int] = None
    team_size: Optional[int] = None
    max_substitutes: int = 0
    games_per_pair: Optional[int] = None
    nickname_prefix: Optional[str] = None


@dataclass(frozen=True)
class NewIndividual:
    """A player of an individual-format tournament.

    Attributes:
        full_name: Real name.
        nickname: Nickname used in the games; unique in the tournament.
        country: Country, if known.
        contact: Contact details, if known.
        person_id: Id of an existing person to reuse instead of creating one.
    """
    full_name: str
    nickname: str
    country: Optional[str] = None
    contact: Optional[str] = None
    person_id: Optional[int] = None


@dataclass(frozen=True)
class NewPlayer:
    """A player joining a team.

    Attributes:
        full_name: Real name.
        nickname: Nickname used in the games; unique in the tournament.
        country: Country, if known.
        contact: Contact details, if known.
        role: "main" or "sub".
        is_captain: Makes the player captain of the team.
        person_id: Id of an existing person to reuse instead of creating one.
    """
    full_name: str
    nickname: str
    country: Optional[str] = None
    contact: Optional[str] = None
    role: str = "main"
    is_captain: bool = False
    person_id: Optional[int] = None


@dataclass(frozen=True)
class NewFixture:
    """A scheduled meeting of two entrants in a round.

    Attributes:
        round_no: Round number.
        entrant_a: First entrant, by id or name.
        entrant_b: Second entrant, by id or name.
        scheduled_at: Planned start as text, if known.
    """
    round_no: int
    entrant_a: Ref
    entrant_b: Ref
    scheduled_at: Optional[str] = None


@dataclass(frozen=True)
class GameRecord:
    """One finished game to store.

    Attributes:
        p1_nickname: First player.
        p2_nickname: Second player.
        p1_result: Result of the first player (GameResult or its int value 1, 2, 3).
        p2_result: Result of the second player.
        game_uid: Idempotency key; None generates one.
        bot_name: Reporting bot.
        table_no: Table number.
        fixture_id: Fixture the game belongs to; None picks the open fixture of the two teams.
        force: Skips the inactive-player and games-per-pair checks.
    """
    p1_nickname: str
    p2_nickname: str
    p1_result: Union[int, GameResult]
    p2_result: Union[int, GameResult]
    game_uid: Optional[str] = None
    bot_name: Optional[str] = None
    table_no: Optional[int] = None
    fixture_id: Optional[int] = None
    force: bool = False


@dataclass(frozen=True)
class EntrantPair:
    """Two entrants compared with each other (cross table).

    Attributes:
        first: Entrant whose players form the rows.
        second: Entrant whose players form the columns.
    """
    first: Ref
    second: Ref
