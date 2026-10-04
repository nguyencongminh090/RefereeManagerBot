-- Tournament database, schema version 1.
--
-- One schema serves both WBC formats:
--   team       : entrants are TEAMS (name, country, captain, 3 players + substitutes)
--   individual : entrants are single PLAYERS (no team name)
-- A game always links two *participants* (people); standings are grouped by *entrant*,
-- so the same queries work for either format.

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE tournaments (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    year            INTEGER,
    format          TEXT    NOT NULL CHECK (format IN ('team', 'individual')),
    team_size       INTEGER CHECK (team_size IS NULL OR team_size > 0),
    max_substitutes INTEGER NOT NULL DEFAULT 0 CHECK (max_substitutes >= 0),
    games_per_pair  INTEGER CHECK (games_per_pair IS NULL OR games_per_pair > 0),
    nickname_prefix TEXT,
    status          TEXT    NOT NULL DEFAULT 'setup' CHECK (status IN ('setup', 'running', 'finished')),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (format = 'individual' OR team_size IS NOT NULL)
);

-- A real person. Reusable across tournaments (and across formats).
CREATE TABLE persons (
    id        INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL COLLATE NOCASE,
    country   TEXT,
    contact   TEXT,
    notes     TEXT
);

-- The unit that is ranked: a team (team format) or a single player (individual format).
CREATE TABLE entrants (
    id            INTEGER PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments (id) ON DELETE CASCADE,
    name          TEXT    NOT NULL COLLATE NOCASE,
    country       TEXT,
    UNIQUE (tournament_id, name),
    UNIQUE (id, tournament_id)
);

-- A person taking part in one tournament, under one PlayOK nickname.
CREATE TABLE participants (
    id            INTEGER PRIMARY KEY,
    tournament_id INTEGER NOT NULL,
    entrant_id    INTEGER NOT NULL,
    person_id     INTEGER NOT NULL REFERENCES persons (id),
    nickname      TEXT    NOT NULL COLLATE NOCASE,
    is_captain    INTEGER NOT NULL DEFAULT 0 CHECK (is_captain IN (0, 1)),
    role          TEXT    NOT NULL DEFAULT 'main' CHECK (role IN ('main', 'sub')),
    active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    UNIQUE (tournament_id, nickname),
    UNIQUE (tournament_id, person_id),
    UNIQUE (id, tournament_id),
    FOREIGN KEY (entrant_id, tournament_id) REFERENCES entrants (id, tournament_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX one_captain_per_entrant ON participants (entrant_id) WHERE is_captain = 1;
CREATE INDEX idx_participants_entrant ON participants (entrant_id);

-- Schedule: one row per pairing of two entrants in a round (optional; games may exist without it).
CREATE TABLE fixtures (
    id            INTEGER PRIMARY KEY,
    tournament_id INTEGER NOT NULL,
    round_no      INTEGER NOT NULL CHECK (round_no > 0),
    entrant_a_id  INTEGER NOT NULL,
    entrant_b_id  INTEGER NOT NULL,
    scheduled_at  TEXT,
    status        TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled')),
    CHECK (entrant_a_id <> entrant_b_id),
    UNIQUE (tournament_id, round_no, entrant_a_id, entrant_b_id),
    UNIQUE (id, tournament_id),
    FOREIGN KEY (entrant_a_id, tournament_id) REFERENCES entrants (id, tournament_id) ON DELETE CASCADE,
    FOREIGN KEY (entrant_b_id, tournament_id) REFERENCES entrants (id, tournament_id) ON DELETE CASCADE
);

-- One finished game. Result codes match core.types.GameResult: 1 win, 2 loss, 3 draw.
CREATE TABLE games (
    id            INTEGER PRIMARY KEY,
    game_uid      TEXT    NOT NULL UNIQUE,
    tournament_id INTEGER NOT NULL REFERENCES tournaments (id) ON DELETE CASCADE,
    fixture_id    INTEGER REFERENCES fixtures (id) ON DELETE SET NULL,
    p1_id         INTEGER NOT NULL,
    p2_id         INTEGER NOT NULL,
    p1_result     INTEGER NOT NULL CHECK (p1_result IN (1, 2, 3)),
    p2_result     INTEGER NOT NULL CHECK (p2_result IN (1, 2, 3)),
    bot_name      TEXT,
    table_no      INTEGER,
    played_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    voided        INTEGER NOT NULL DEFAULT 0 CHECK (voided IN (0, 1)),
    CHECK (p1_id <> p2_id),
    CHECK ((p1_result = 1 AND p2_result = 2)
        OR (p1_result = 2 AND p2_result = 1)
        OR (p1_result = 3 AND p2_result = 3)),
    FOREIGN KEY (p1_id, tournament_id) REFERENCES participants (id, tournament_id),
    FOREIGN KEY (p2_id, tournament_id) REFERENCES participants (id, tournament_id)
);
CREATE INDEX idx_games_tournament ON games (tournament_id, voided);
CREATE INDEX idx_games_p1 ON games (p1_id);
CREATE INDEX idx_games_p2 ON games (p2_id);

CREATE TRIGGER games_different_entrants BEFORE INSERT ON games
WHEN (SELECT entrant_id FROM participants WHERE id = NEW.p1_id)
   = (SELECT entrant_id FROM participants WHERE id = NEW.p2_id)
BEGIN
    SELECT RAISE(ABORT, 'both players belong to the same entrant');
END;

-- Who changed what (admin edits and recorded games).
CREATE TABLE audit_log (
    id        INTEGER PRIMARY KEY,
    at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    detail    TEXT
);

-- Team-format listing: one row per player with the team's country and captain
-- (the "Team Name | Country | Captain | Players" layout of the official teams page).
CREATE VIEW v_team_roster AS
SELECT t.id        AS tournament_id,
       t.name      AS tournament,
       e.id        AS team_id,
       e.name      AS team_name,
       e.country   AS country,
       cper.full_name AS captain_name,
       cpar.nickname  AS captain_nickname,
       cper.contact   AS captain_contact,
       per.full_name  AS player_name,
       pa.nickname    AS nickname,
       pa.role        AS role,
       pa.active      AS active,
       pa.is_captain  AS is_captain
FROM tournaments t
JOIN entrants     e    ON e.tournament_id = t.id
JOIN participants pa   ON pa.entrant_id = e.id
JOIN persons      per  ON per.id = pa.person_id
LEFT JOIN participants cpar ON cpar.entrant_id = e.id AND cpar.is_captain = 1
LEFT JOIN persons      cper ON cper.id = cpar.person_id
WHERE t.format = 'team';

-- Individual-format listing: players directly, no team name.
CREATE VIEW v_individual_list AS
SELECT t.id AS tournament_id,
       t.name AS tournament,
       pa.id AS participant_id,
       pa.nickname AS nickname,
       per.full_name AS player_name,
       COALESCE(per.country, e.country) AS country,
       per.contact AS contact,
       pa.active AS active
FROM tournaments t
JOIN entrants     e   ON e.tournament_id = t.id
JOIN participants pa  ON pa.entrant_id = e.id
JOIN persons      per ON per.id = pa.person_id
WHERE t.format = 'individual';

-- Each valid game seen from each side (voided games excluded).
CREATE VIEW v_participant_results AS
SELECT g.tournament_id AS tournament_id, g.p1_id AS participant_id, g.p1_result AS result, g.id AS game_id
FROM games g WHERE g.voided = 0
UNION ALL
SELECT g.tournament_id, g.p2_id, g.p2_result, g.id
FROM games g WHERE g.voided = 0;

-- Cross-table source: counts per unordered pair of players (seat order does not matter).
-- Convert to points in code with the configured scoring weights.
CREATE VIEW v_pair_results AS
SELECT g.tournament_id AS tournament_id,
       g.fixture_id    AS fixture_id,
       MIN(g.p1_id, g.p2_id) AS lo,
       MAX(g.p1_id, g.p2_id) AS hi,
       COUNT(*)              AS games,
       SUM(CASE WHEN g.p1_id < g.p2_id THEN g.p1_result = 1 ELSE g.p2_result = 1 END) AS lo_wins,
       SUM(g.p1_result = 3)                                                           AS draws,
       SUM(CASE WHEN g.p1_id < g.p2_id THEN g.p2_result = 1 ELSE g.p1_result = 1 END) AS hi_wins
FROM games g
WHERE g.voided = 0
GROUP BY g.tournament_id, g.fixture_id, lo, hi;
