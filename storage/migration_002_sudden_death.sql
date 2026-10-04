-- Schema version 2: outcome of sudden-death deciders between tied entrants (tie-break 3).
-- One row per decided pair: `winner_id` beat `loser_id`. Delete the row to correct a mistake.
CREATE TABLE sudden_death (
    id            INTEGER PRIMARY KEY,
    tournament_id INTEGER NOT NULL,
    winner_id     INTEGER NOT NULL,
    loser_id      INTEGER NOT NULL,
    decided_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (winner_id <> loser_id),
    UNIQUE (tournament_id, winner_id, loser_id),
    FOREIGN KEY (winner_id, tournament_id) REFERENCES entrants (id, tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (loser_id,  tournament_id) REFERENCES entrants (id, tournament_id) ON DELETE CASCADE
);
