import threading
from abc            import ABC, abstractmethod
from dataclasses    import dataclass, field
from typing         import Dict, List, Optional, Set
from core.types     import GameResult, PlayerStats


class IScoreObserver(ABC):
    @abstractmethod
    def on_score_updated(self, snapshot: str) -> None: ...


class IScoreSubject(ABC):
    @abstractmethod
    def subscribe(self, observer: IScoreObserver) -> None: ...

    @abstractmethod
    def unsubscribe(self, observer: IScoreObserver) -> None: ...

    @abstractmethod
    def notify_all(self) -> None: ...


@dataclass
class Player:
    name  : str
    _stats: PlayerStats = field(init=False, default_factory=PlayerStats)

    def update_score(self, result: GameResult) -> None:
        if result == GameResult.WIN:
            self._stats.wins += 1
        elif result == GameResult.LOSS:
            self._stats.losses += 1
        elif result == GameResult.DRAW:
            self._stats.draws += 1

    @property
    def stats(self) -> PlayerStats:
        return self._stats

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Player):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __str__(self) -> str:
        return f"{self.name}: {self._stats}"


@dataclass
class Team:
    name    : str
    _players: Dict[str, Player] = field(default_factory=dict)

    def add_player(self, player: Player) -> None:
        if player.name in self._players:
            raise ValueError(f"{player.name} is already in the team")
        self._players[player.name] = player

    def get_player(self, name: str) -> Optional[Player]:
        return self._players.get(name)

    def total_score(self) -> float:
        return sum(p.stats.total_score() for p in self._players.values())

    def player_stats(self, name: str) -> PlayerStats:
        if name not in self._players:
            raise ValueError(f"{name} is not in the team")
        return self._players[name].stats

    def update_player_score(self, name: str, result: GameResult) -> None:
        if name not in self._players:
            raise ValueError(f"{name} is not in the team")
        self._players[name].update_score(result)

    def all_players(self) -> List[Player]:
        return list(self._players.values())


class ITeamRepository(ABC):
    @abstractmethod
    def register_team(self, name: str) -> None: ...

    @abstractmethod
    def join_team(self, player_name: str, team_name: str) -> None: ...

    @abstractmethod
    def record_result(self, player_name: str, result: GameResult) -> None: ...

    @abstractmethod
    def record_match(self, p1_name: str, p1_result: GameResult, p2_name: str, p2_result: GameResult) -> None: ...

    @abstractmethod
    def player_stats(self, player_name: str) -> PlayerStats: ...

    @abstractmethod
    def team_score(self, team_name: str) -> float: ...

    @abstractmethod
    def snapshot(self) -> str: ...


class InMemoryTeamRepository(ITeamRepository, IScoreSubject):
    def __init__(self):
        self._lock           : threading.RLock           = threading.RLock()
        self._teams          : Dict[str, Team]           = {}
        self._player_team_map: Dict[str, Team]           = {}
        self._observers      : Set[IScoreObserver]       = set()

    def subscribe(self, observer: IScoreObserver) -> None:
        with self._lock:
            self._observers.add(observer)

    def unsubscribe(self, observer: IScoreObserver) -> None:
        with self._lock:
            self._observers.discard(observer)

    def notify_all(self) -> None:
        with self._lock:
            text = self.snapshot()
            observers_copy = list(self._observers)
        for observer in observers_copy:
            observer.on_score_updated(text)

    def register_team(self, name: str) -> None:
        with self._lock:
            if name in self._teams:
                raise ValueError(f"Team '{name}' is already registered.")
            self._teams[name] = Team(name)

    def join_team(self, player_name: str, team_name: str) -> None:
        with self._lock:
            if team_name not in self._teams:
                raise ValueError(f"Team '{team_name}' does not exist.")
            if player_name in self._player_team_map:
                raise ValueError(f"Player '{player_name}' is already in Team '{self._player_team_map[player_name].name}'.")

            new_player = Player(player_name)
            self._teams[team_name].add_player(new_player)
            self._player_team_map[player_name] = self._teams[team_name]

    def record_result(self, player_name: str, result: GameResult) -> None:
        with self._lock:
            team = self._player_team_map.get(player_name)
            if not team:
                raise ValueError(f"Cannot record result: Player '{player_name}' is not in any team.")
            team.update_player_score(player_name, result)

    def record_match(self, p1_name: str, p1_result: GameResult, p2_name: str, p2_result: GameResult) -> None:
        with self._lock:
            self.record_result(p1_name, p1_result)
            self.record_result(p2_name, p2_result)
        self.notify_all()

    def player_stats(self, player_name: str) -> PlayerStats:
        with self._lock:
            team = self._player_team_map.get(player_name)
            if not team:
                raise ValueError(f"Cannot retrieve stats: Player '{player_name}' not found.")
            return team.player_stats(player_name)

    def team_score(self, team_name: str) -> float:
        with self._lock:
            if team_name not in self._teams:
                raise ValueError(f"Team '{team_name}' does not exist.")
            return self._teams[team_name].total_score()

    def snapshot(self) -> str:
        with self._lock:
            if not self._teams:
                return "No teams registered yet."
            names_str  = " : ".join(team.name for team in self._teams.values())
            scores_str = " : ".join(str(team.total_score()) for team in self._teams.values())
            return f"{names_str} = {scores_str}"
