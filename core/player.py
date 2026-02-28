from dataclasses import dataclass, field
from core.types  import GameResult
from typing      import Tuple


@dataclass
class Player:
    name : str
    win  : int = field(init=False, default=0, repr=False)
    loss : int = field(init=False, default=0, repr=False)
    draw : int = field(init=False, default=0, repr=False)

    def update_score(self, score: GameResult):
        if score == GameResult.WIN:
            self.win += 1
        elif score == GameResult.LOSS:
            self.loss += 1
        elif score == GameResult.DRAW:
            self.draw += 1

    def get_score(self) -> Tuple[int, int, int]:
        return self.win, self.loss, self.draw

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Player):
            return NotImplemented
        return self.name == other.name

    def __hash__(self) -> int:
        return hash(self.name)

    def __str__(self):
        return f"{self.name}: {self.win}-{self.loss}-{self.draw}"


@dataclass
class Team:
    name     : str
    players  : dict[str, Player] = field(default_factory=dict)

    def add_player(self, player: Player):
        if player.name in self.players:
            raise ValueError(f"{player.name} is already in the team")
        self.players[player.name] = player

    def get_score(self) -> int:
        return sum(player.get_score()[0] for player in self.players.values())

    def get_players_result(self, name: str) -> Tuple[int, int, int]:
        if name not in self.players:
            raise ValueError(f"{name} is not in the team")
        return self.players[name].get_score()

    def update_players_score(self, player_name: str, score: GameResult):
        if player_name not in self.players:
            raise ValueError(f"{player_name} is not in the team")
        self.players[player_name].update_score(score)

        
class TeamManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance                 : TeamManager     = super(TeamManager, cls).__new__(cls)
            cls._instance.teams           : dict[str, Team] = {}
            cls._instance._player_team_map: dict[str, Team]  = {}
        return cls._instance

    def register_team(self, team_name: str) -> None:
        if team_name in self.teams:
            raise ValueError(f"Team '{team_name}' is already registered.")
        self.teams[team_name] = Team(team_name)

    def join_team(self, player_name: str, team_name: str) -> None:
        if team_name not in self.teams:
            raise ValueError(f"Team '{team_name}' does not exist.")
        if player_name in self._player_team_map:
            raise ValueError(f"Player '{player_name}' is already in Team '{self._player_team_map[player_name].name}'.")

        new_player = Player(player_name)
        self.teams[team_name].add_player(new_player)
        self._player_team_map[player_name] = self.teams[team_name]

    def record_match_result(self, player_name: str, result: GameResult) -> None:
        team = self._player_team_map.get(player_name)
        if not team:
            raise ValueError(f"Cannot record result: Player '{player_name}' is not in any team.")
        team.update_players_score(player_name, result)

    def get_player_stats(self, player_name: str) -> Tuple[int, int, int]:
        team = self._player_team_map.get(player_name)
        if not team:
             raise ValueError(f"Cannot retrieve stats: Player '{player_name}' not found.")
        return team.get_players_result(player_name)

    def get_team_total_score(self, team_name: str) -> int:
        if team_name not in self.teams:
             raise ValueError(f"Team '{team_name}' does not exist.")
        return self.teams[team_name].get_score()

    def notify(self) -> str:
        if not self.teams:
             return "No teams registered yet."             
        names_str  = " : ".join(team.name for team in self.teams.values())
        scores_str = " : ".join(str(team.get_score()) for team in self.teams.values())        
        return f"{names_str} = {scores_str}"
