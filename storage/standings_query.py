"""Read-only results: standings, per-player records, pair scores and cross tables."""
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from storage.database import Database
from storage.errors import NotFoundError
from storage.lookups import entrant_row, participant_row, tournament_row
from domain.types import GameResult, Scoring
from storage.models import EntrantPair, RankingRules, Ref, StandingRow
from storage.ranking import (GameLine, RankInput, encounters_from_games, points_by_entrant,
                             rank_entrants)
from storage.sudden_death import deciders_of

_EMPTY_CELL_POINTS = 0.0


@dataclass(frozen=True)
class _CrossScores:
    """What `_cross_cell` needs besides the two players: pair totals, game limit and scoring."""
    pairs: Dict[Tuple[int, int], Any]
    limit: Optional[int]
    scoring: Scoring


def _game_lines(cx: sqlite3.Connection, tournament_id: int) -> List[GameLine]:
    """Valid games between entrants, one line per game, as the ranking functions expect."""
    return [GameLine(r["a"], r["b"], r["fixture_id"], r["p1_result"]) for r in cx.execute(
        "SELECT g.fixture_id, g.p1_result, a.entrant_id AS a, b.entrant_id AS b FROM games g"
        " JOIN participants a ON a.id = g.p1_id JOIN participants b ON b.id = g.p2_id"
        " WHERE g.tournament_id = ? AND g.voided = 0", (tournament_id,))]


def _entrant_totals(cx: sqlite3.Connection, tournament_id: int) -> List[sqlite3.Row]:
    """Wins, draws, losses and game count per entrant; entrants without games get zeros."""
    return cx.execute(
        "SELECT e.id, e.name, e.country,"
        " COALESCE(SUM(r.result = 1), 0) AS wins, COALESCE(SUM(r.result = 3), 0) AS draws,"
        " COALESCE(SUM(r.result = 2), 0) AS losses, COUNT(r.game_id) AS games"
        " FROM entrants e LEFT JOIN participants pa ON pa.entrant_id = e.id"
        " LEFT JOIN v_participant_results r ON r.participant_id = pa.id"
        " WHERE e.tournament_id = ? GROUP BY e.id", (tournament_id,)).fetchall()


def _standing_row(r: sqlite3.Row, scoring: Scoring, match_total: Optional[float]) -> StandingRow:
    """Builds an unranked standing row from one `_entrant_totals` row."""
    return StandingRow(r["id"], r["name"], r["country"], r["games"], r["wins"], r["draws"],
                       r["losses"], scoring.points(r["wins"], r["draws"], r["losses"]),
                       scoring.points_against(r["wins"], r["draws"], r["losses"]), match_total)


def _cross_cell(pa: sqlite3.Row, pb: sqlite3.Row, ctx: _CrossScores) -> Dict[str, Any]:
    """The cell of player `pa` (row) against player `pb` (column)."""
    scoring, limit = ctx.scoring, ctx.limit
    # v_pair_results keys each pair by (lower id, higher id); flip the wins for the higher id.
    pr = ctx.pairs.get((min(pa["id"], pb["id"]), max(pa["id"], pb["id"])))
    if pr is None:
        return {"opponent": pb["nickname"], "games": 0, "points": _EMPTY_CELL_POINTS,
                "opponent_points": _EMPTY_CELL_POINTS, "complete": False}
    a_is_lo = pa["id"] < pb["id"]
    a_wins, b_wins = (pr["lo_wins"], pr["hi_wins"]) if a_is_lo else (pr["hi_wins"], pr["lo_wins"])
    return {"opponent": pb["nickname"], "games": int(pr["games"]),
            "points": scoring.points(a_wins, pr["draws"], b_wins),
            "opponent_points": scoring.points(b_wins, pr["draws"], a_wins),
            "complete": bool(limit) and pr["games"] >= limit}


class StandingsQuery:
    """Computes results from the stored games."""

    def __init__(self, db: Database):
        self._db = db

    def standings(self, tournament: Ref, scoring: Scoring,
                  rules: Optional[RankingRules] = None) -> List[StandingRow]:
        """Returns teams (team format) or players (individual format), best first.

        Each row carries `rank` and `tied`.

        Ranked by points (match points if `rules.match_points` is set, else game points), then by
        `rules.tiebreaks` in order; see storage.ranking for how each criterion works.
        """
        rules = rules or RankingRules()
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            rows = _entrant_totals(cx, t["id"])
            games = _game_lines(cx, t["id"])
            deciders = deciders_of(cx, t["id"])
        encounters = encounters_from_games(games, scoring, rules.match_points)
        match_totals = points_by_entrant(encounters) if rules.match_points else None
        standings = [_standing_row(r, scoring,
                                   None if match_totals is None else match_totals.get(r["id"], 0.0))
                     for r in rows]
        inputs = [RankInput(s.entrant_id, s.name,
                            s.points if s.match_points is None else s.match_points, s.difference)
                  for s in standings]
        by_id = {s.entrant_id: s for s in standings}
        return [replace(by_id[p.entrant_id], rank=p.rank, tied=p.tied)
                for p in rank_entrants(inputs, encounters, deciders, rules.tiebreaks)]

    def pair_score_for_game(self, game_id: int, scoring: Scoring) -> Dict[str, Any]:
        """Returns the running micro-match score of the two players of a game.

        Counts the non-voided games of the same fixture, whichever player sat first.

        Raises:
            NotFoundError: If the game does not exist.
        """
        with self._db.transaction() as cx:
            g = cx.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if g is None:
                raise NotFoundError(f"game {game_id} not found")
            t = cx.execute("SELECT games_per_pair FROM tournaments WHERE id = ?",
                           (g["tournament_id"],)).fetchone()
            nick = {r["id"]: r["nickname"] for r in cx.execute(
                "SELECT id, nickname FROM participants WHERE id IN (?, ?)",
                (g["p1_id"], g["p2_id"]))}
            row = cx.execute(
                "SELECT COUNT(*) AS games,"
                " COALESCE(SUM(CASE WHEN p1_id = ? THEN p1_result = 1 ELSE p2_result = 1 END), 0)"
                " AS wins1,"
                " COALESCE(SUM(p1_result = 3), 0) AS draws,"
                " COALESCE(SUM(CASE WHEN p1_id = ? THEN p2_result = 1 ELSE p1_result = 1 END), 0)"
                " AS wins2"
                " FROM games WHERE voided = 0 AND fixture_id IS ? AND tournament_id = ?"
                " AND ((p1_id = ? AND p2_id = ?) OR (p1_id = ? AND p2_id = ?))",
                (g["p1_id"], g["p1_id"], g["fixture_id"], g["tournament_id"],
                 g["p1_id"], g["p2_id"], g["p2_id"], g["p1_id"])).fetchone()
            limit = t["games_per_pair"]
            teams, team_points = self._team_match_score(cx, g, scoring)
            return {"games": row["games"], "limit": limit,
                    "players": [nick[g["p1_id"]], nick[g["p2_id"]]],
                    "points": [scoring.points(row["wins1"], row["draws"], row["wins2"]),
                               scoring.points(row["wins2"], row["draws"], row["wins1"])],
                    "teams": teams, "team_points": team_points,
                    "complete": bool(limit) and row["games"] >= limit}

    @staticmethod
    def _team_match_score(cx: sqlite3.Connection, game: sqlite3.Row,
                          scoring: Scoring) -> Tuple[List[str], List[float]]:
        """Names and game points of the two entrants of a game over their whole fixture.

        Counts the non-voided games of the fixture between the two entrants, whoever played them;
        in an individual tournament the entrants are the players, so this is the pair score.
        """
        first, second = (cx.execute(
            "SELECT p.entrant_id AS id, e.name FROM participants p"
            " JOIN entrants e ON e.id = p.entrant_id WHERE p.id = ?", (pid,)).fetchone()
            for pid in (game["p1_id"], game["p2_id"]))
        rows = cx.execute(
            "SELECT pa.entrant_id AS e1, g.p1_result AS r1, g.p2_result AS r2 FROM games g"
            " JOIN participants pa ON pa.id = g.p1_id JOIN participants pb ON pb.id = g.p2_id"
            " WHERE g.voided = 0 AND g.fixture_id IS ? AND g.tournament_id = ?"
            " AND pa.entrant_id IN (?, ?) AND pb.entrant_id IN (?, ?)",
            (game["fixture_id"], game["tournament_id"], first["id"], second["id"],
             first["id"], second["id"])).fetchall()
        points = {first["id"]: 0.0, second["id"]: 0.0}
        for r in rows:
            other = second["id"] if r["e1"] == first["id"] else first["id"]
            points[r["e1"]] += scoring.points_for(GameResult(r["r1"]))
            points[other] += scoring.points_for(GameResult(r["r2"]))
        return [first["name"], second["name"]], [points[first["id"]], points[second["id"]]]

    def player_record(self, tournament: Ref, nickname: str) -> Dict[str, int]:
        """Returns the wins, draws and losses of one player.

        Raises:
            UnknownPlayerError: If the nickname is not in the tournament.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            p = participant_row(cx, t["id"], nickname)
            row = cx.execute("SELECT COALESCE(SUM(result = 1), 0) AS wins,"
                             " COALESCE(SUM(result = 3), 0) AS draws,"
                             " COALESCE(SUM(result = 2), 0) AS losses FROM v_participant_results"
                             " WHERE participant_id = ?", (p["id"],)).fetchone()
            return dict(row)

    def cross_table(self, tournament: Ref, entrants: EntrantPair,
                    scoring: Scoring) -> Dict[str, Any]:
        """Returns the players of the first entrant (rows) against those of the second (columns).

        Each cell holds points and games for the pair and whether the micro-match is complete
        (games_per_pair reached).

        Raises:
            NotFoundError: If an entrant does not exist.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            a = entrant_row(cx, t["id"], entrants.first)
            b = entrant_row(cx, t["id"], entrants.second)
            rows_p, cols_p = self._players_of(cx, a["id"]), self._players_of(cx, b["id"])
            ctx = _CrossScores(self._pair_totals(cx, t["id"]), t["games_per_pair"], scoring)
            matrix = [{"player": pa["nickname"],
                       "cells": [_cross_cell(pa, pb, ctx) for pb in cols_p]}
                      for pa in rows_p]
            return {"a": a["name"], "b": b["name"], "rows": matrix}

    @staticmethod
    def _players_of(cx: sqlite3.Connection, entrant_id: int) -> List[sqlite3.Row]:
        return cx.execute("SELECT id, nickname FROM participants WHERE entrant_id = ?"
                          " ORDER BY is_captain DESC, nickname", (entrant_id,)).fetchall()

    @staticmethod
    def _pair_totals(cx: sqlite3.Connection, tournament_id: int) -> Dict[Tuple[int, int], Any]:
        return {(r["lo"], r["hi"]): dict(r) for r in cx.execute(
            "SELECT lo, hi, SUM(games) AS games, SUM(lo_wins) AS lo_wins, SUM(draws) AS draws,"
            " SUM(hi_wins) AS hi_wins FROM v_pair_results WHERE tournament_id = ? GROUP BY lo, hi",
            (tournament_id,))}
