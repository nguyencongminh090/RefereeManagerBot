"""Typed, validated settings read from `config.toml` (rules, selectors, texts) and `.env` (secrets).

    # config path: argument, $REFEREE_CONFIG, config/config.toml
    settings = ConfigLoader.load(role="client")
    settings.tournament.total_matches
    settings.playok.patterns["draw"].match(line)

Nothing is defaulted in code: every value must be written in the file, so a missing or misspelled
key is reported at start-up (all problems at once) instead of surfacing in the middle of a
tournament.
Reusing the bot for another tournament or game only means writing another config file.
"""
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime    import datetime
from pathlib     import Path
from typing      import Dict, List, Mapping, Optional, Pattern, Tuple
from zoneinfo    import ZoneInfo, ZoneInfoNotFoundError

from config.messages import MessagesConfig, MessageTexts, load_messages
from config.process_config import ClientConfig, DashboardConfig, DatabaseConfig, ServerConfig
from config.reader import ConfigError, Section, parse_env_file
from domain.types import Scoring


DEFAULT_CONFIG_PATH = "config/config.toml"
CONFIG_ENV_VAR      = "REFEREE_CONFIG"
DEFAULT_ENV_FILE    = ".env"
ROUND_START_FORMAT  = "%Y-%m-%d %H:%M"

JOIN_MODES   = ("auto", "invite")
FORMATS      = ("team", "individual")
TIEBREAKS    = ("score_difference", "head_to_head", "sudden_death")
SELECTORS    = ("chat_messages", "chat_input", "seat_names", "table_title", "invitation_text",
                "invitation_accept", "invitation_reject", "leave_table", "lobby_rows",
                "lobby_table_no", "lobby_time", "lobby_seats", "cookie_accept", "login_open",
                "login_user", "login_pass", "login_submit", "login_start", "room_select",
                "room_option", "lobby_join")
# pattern name -> named groups the code relies on
PATTERNS     = {"win_p1": (), "win_p2": (), "draw": (), "timeout": ("seat",),
                "join": ("name", "rating"), "leave": ("name",),
                "invitation": ("user", "elo", "table", "info"),
                "table_header": ("table", "time")}
SECRET_KEYS  = {"client": ("PLAYOK_USER", "PLAYOK_PASS", "BOT_TOKEN"), "server": ("BOT_TOKEN",)}
DASHBOARD_TOKEN_KEY = "DASHBOARD_TOKEN"   # required by the server only when the dashboard is enabled


@dataclass(frozen=True)
class TableRules:
    """Table settings the bot expects, from `[tournament.table_rules]`.

    Attributes:
        time_control: Clock such as `5m` or `1m+1s`.
        swap2: Whether the swap2 opening is used.
        rated: Whether games are rated.
        public: Whether tables are public.
        no_undo: Whether undo is disabled.
        player_prefix: Required nickname prefix; empty for none.
    """
    time_control : str
    swap2        : bool
    rated        : bool
    public       : bool
    no_undo      : bool
    player_prefix: str


@dataclass(frozen=True)
class MatchScoring:
    """Points per game (`games`), points per team match, and the tie-break order."""
    games     : Scoring
    match_win : float
    match_draw: float
    match_loss: float
    tiebreaks : Tuple[str, ...]


@dataclass(frozen=True)
class TournamentConfig:
    """The `[tournament]` table.

    Attributes:
        name: Tournament name; keys the database record.
        format: One of FORMATS.
        year: Edition year, or None.
        total_matches: Games per pair of entrants.
        break_after: Games after which a break starts; 0 for none.
        break_minutes: Break length in minutes.
        timezone: IANA time zone name.
        language: Language of the rules text.
        admins: Nicknames allowed to give admin commands.
        team_size: Players per team; None for individual events.
        max_substitutes: Substitutes per team; None for individual events.
        no_show_minutes: Minutes after which an absent player forfeits.
        round_start: Start of the current round, in the tournament zone, or None; `!sync` counts games from it.
        scoring: Points and tie-breaks.
        table_rules: Expected table settings.
    """
    name           : str
    format         : str
    year           : Optional[int]
    total_matches  : int
    break_after    : int
    break_minutes  : float
    timezone       : str
    language       : str
    admins         : Tuple[str, ...]
    team_size      : Optional[int]
    max_substitutes: Optional[int]
    no_show_minutes: float
    round_start    : Optional[datetime]
    scoring        : MatchScoring
    table_rules    : TableRules

    def is_admin(self, nickname: str) -> bool:
        """Tells whether the nickname is an admin, ignoring case and surrounding spaces."""
        return nickname.strip().lower() in {a.lower() for a in self.admins}

    @property
    def tzinfo(self) -> ZoneInfo:
        """The tournament time zone as a ZoneInfo."""
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class CommandsConfig:
    """The `[commands]` table.

    Attributes:
        prefix: Character that starts a chat command.
        admin_only: Command names (lower case, no prefix) reserved for admins.
        cheer_cooldown_seconds: Seconds between two !cheer commands at one table.
        break_max_minutes: Longest break an admin may announce with !break.
    """
    prefix    : str
    admin_only: Tuple[str, ...]
    cheer_cooldown_seconds: float
    break_max_minutes     : float

    def is_admin_only(self, command: str) -> bool:
        """Tells whether the command (with or without prefix) is reserved for admins."""
        return command.lower().removeprefix(self.prefix) in self.admin_only


@dataclass(frozen=True)
class PlayOkConfig:
    """The `[playok]` table.

    Attributes:
        site_url: Address of the site.
        lobby_room: Room number of the lobby.
        selectors: Page element selectors by name (see SELECTORS).
        patterns: Compiled chat patterns by name (see PATTERNS).
    """
    site_url  : str
    lobby_room: int
    selectors : Mapping[str, str]
    patterns  : Mapping[str, Pattern[str]]


@dataclass(frozen=True)
class StatsConfig:
    """The `[stats]` table: PlayOK's public statistics pages, read by `!sync`.

    Attributes:
        url: Address of the statistics page.
        game_code: PlayOK's code of the game in the page address (`gm` for gomoku).
        timezone: IANA zone the page writes its dates in.
        timeout_seconds: Seconds a request may take.
    """
    url            : str
    game_code      : str
    timezone       : str
    timeout_seconds: float

    @property
    def tzinfo(self) -> ZoneInfo:
        """The zone of the page dates as a ZoneInfo."""
        return ZoneInfo(self.timezone)


@dataclass(frozen=True)
class Secrets:
    """Values from `.env` / environment. Hidden from repr so they never reach a log by accident."""
    playok_user: Optional[str] = field(default=None, repr=False)
    playok_pass: Optional[str] = field(default=None, repr=False)
    bot_token  : Optional[str] = field(default=None, repr=False)
    dashboard_token: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class Settings:
    """Everything read from the config file and the environment.

    Attributes:
        server: `[server]` table.
        dashboard: `[dashboard]` table.
        database: `[database]` table.
        client: `[client]` table.
        tournament: `[tournament]` table.
        commands: `[commands]` table.
        playok: `[playok]` table.
        stats: `[stats]` table.
        messages: `[messages]` table.
        secrets: Values from `.env` or the environment.
        source: Path of the config file read.
    """
    server    : ServerConfig
    dashboard : DashboardConfig
    database  : DatabaseConfig
    client    : ClientConfig
    tournament: TournamentConfig
    commands  : CommandsConfig
    playok    : PlayOkConfig
    stats     : StatsConfig
    messages  : MessagesConfig
    secrets   : Secrets
    source    : str

    @property
    def texts(self) -> MessageTexts:
        """The chat texts in the tournament language."""
        return self.messages.texts(self.tournament.language)


# ----------------------------------------------------------------------------- loader
class ConfigLoader:
    """Reads and validates the config file and secrets; all methods are static."""

    @staticmethod
    def resolve_path(path: Optional[str] = None,
                     environ: Optional[Mapping[str, str]] = None) -> str:
        """Returns the config path: the argument, else $REFEREE_CONFIG, else the default."""
        env = os.environ if environ is None else environ
        return path or env.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH

    @staticmethod
    def load(path: Optional[str] = None, *, env_file: Optional[str] = None,
             role: Optional[str] = None, environ: Optional[Mapping[str, str]] = None) -> Settings:
        """Reads and validates the config file and the secrets.

        Real environment variables override the same names in the .env file.

        Args:
            path: Config file; see resolve_path.
            env_file: Secrets file, `.env` by default.
            role: "client" or "server" decides which secrets are required; None requires none.
            environ: Environment to read instead of os.environ (for tests).

        Returns:
            The validated settings.

        Raises:
            ConfigError: With every problem found, if the file is missing, not TOML or invalid.
            ValueError: If `role` is unknown.
        """
        env    = os.environ if environ is None else environ
        source = ConfigLoader.resolve_path(path, env)
        if role not in (None, *SECRET_KEYS):
            raise ValueError(f"role must be one of {', '.join(SECRET_KEYS)} or None")
        try:
            with open(source, "rb") as handle:
                raw = tomllib.load(handle)
        except FileNotFoundError:
            raise ConfigError(
                source, ["file not found (copy config/config.example.toml and edit it)"]) from None
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(source, [f"not valid TOML: {exc}"]) from None

        problems: List[str] = []
        root = Section(raw, "", problems)
        settings = ConfigLoader._build(root, problems, env_file or DEFAULT_ENV_FILE, role, env,
                                       source)
        if problems:
            raise ConfigError(source, problems)
        return settings

    @staticmethod
    def _build(root: Section, problems: List[str], env_file: str, role: Optional[str],
               env: Mapping[str, str], source: str) -> Settings:
        server = root.table("server")
        server_cfg = ServerConfig(
            server.text("host"), server.integer("port", 1),
            server.number("heartbeat_seconds", 0, True), server.number("backup_seconds", 0, True),
            server.integer("max_packet_bytes", 1), server.integer("max_clients", 1),
            server.number("send_timeout_seconds", 0, True),
            server.number("auth_timeout_seconds", 0, True), server.integer("auth_max_failures", 1),
            server.number("auth_lockout_seconds", 0, True))
        server.finish()

        dash = root.table("dashboard")
        dashboard_cfg = DashboardConfig(
            dash.boolean("enabled"), dash.text("host"), dash.integer("port", 1),
            dash.number("refresh_seconds", 0, True), dash.integer("recent_games", 1),
            dash.integer("audit_entries", 1), dash.boolean("allow_edit"))
        dash.finish()

        db = root.table("database")
        db_cfg = DatabaseConfig(db.text("path"), db.optional_text("teams_file"))
        db.finish()

        client = root.table("client")
        client_cfg = ClientConfig(
            client.text("server_host"), client.integer("server_port", 1),
            client.number("poll_seconds", 0, True), client.number("reconnect_max_seconds", 0, True),
            client.choice("join_mode", JOIN_MODES), client.number("lobby_scan_seconds", 0, True),
            client.text("outbox_path", allow_empty=True),
            client.integer("max_pending_results", 1),
            client.integer("unreadable_polls_before_alert", 1))
        client.finish()

        tournament_cfg = ConfigLoader._tournament(root.table("tournament"), problems)

        commands = root.table("commands")
        commands_cfg = CommandsConfig(commands.text("prefix"),
                                      tuple(c.lower() for c in commands.text_list("admin_only")),
                                      commands.number("cheer_cooldown_seconds", 0),
                                      commands.number("break_max_minutes", 0, True))
        commands.finish()

        playok_cfg = ConfigLoader._playok(root.table("playok"), problems)
        stats_cfg = ConfigLoader._stats(root.table("stats"), problems)
        messages_cfg = load_messages(root.table("messages"), tournament_cfg.language, problems)
        root.finish()

        file_values = parse_env_file(env_file)
        known = {name for names in SECRET_KEYS.values() for name in names} | {DASHBOARD_TOKEN_KEY}
        # An empty environment variable never hides the file value.
        merged = {**file_values, **{k: v for k, v in env.items() if k in known and v}}
        required = SECRET_KEYS.get(role, ())
        if role == "server" and dashboard_cfg.enabled:
            required += (DASHBOARD_TOKEN_KEY,)
        for key in required:
            if not merged.get(key):
                problems.append(
                    f"secret {key} is not set (put it in {env_file} or the environment)")
        secrets = Secrets(merged.get("PLAYOK_USER"), merged.get("PLAYOK_PASS"),
                          merged.get("BOT_TOKEN"), merged.get(DASHBOARD_TOKEN_KEY))

        return Settings(server_cfg, dashboard_cfg, db_cfg, client_cfg, tournament_cfg, commands_cfg, playok_cfg,
                        stats_cfg, messages_cfg, secrets, source)

    @staticmethod
    def _tournament(t: Section, problems: List[str]) -> TournamentConfig:
        fmt     = t.choice("format", FORMATS)
        team    = fmt == "team"
        name    = t.text("name")
        year    = t.integer("year", 1, required=False)
        total   = t.integer("total_matches", 1)
        brk_after = t.integer("break_after", 0)
        brk_min = t.number("break_minutes", 0)
        if brk_after and not brk_min:
            problems.append(
                "'tournament.break_minutes' must be > 0 when 'tournament.break_after' is set")
        tz = t.text("timezone")
        if tz:
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, ValueError):
                problems.append(f"'tournament.timezone' is not a known time zone: {tz!r}")
        language = t.text("language")
        admins   = t.text_list("admins", allow_empty=False)
        team_size = t.integer("team_size", 1, required=team)
        max_subs  = t.integer("max_substitutes", 0, required=team)
        no_show   = t.number("no_show_minutes", 0)
        round_start = ConfigLoader._round_start(t, tz, problems)

        sc = t.table("scoring")
        scoring = MatchScoring(
            Scoring(sc.number("win", None), sc.number("draw", None), sc.number("loss", None)),
            sc.number("match_win", None), sc.number("match_draw", None),
            sc.number("match_loss", None), sc.text_list("tiebreaks", allowed=TIEBREAKS))
        sc.finish()

        tr = t.table("table_rules")
        time_control = tr.text("time_control")
        if time_control and not re.fullmatch(r"\d+m(\+\d+s)?", time_control):
            problems.append("'tournament.table_rules.time_control' must look like '1m+1s' or "
                            f"'5m', got {time_control!r}")
        rules = TableRules(time_control, tr.boolean("swap2"), tr.boolean("rated"),
                           tr.boolean("public"), tr.boolean("no_undo"),
                           tr.text("player_prefix", allow_empty=True))
        tr.finish()
        t.finish()
        return TournamentConfig(name, fmt, year, total, brk_after, brk_min, tz, language, admins,
                                team_size, max_subs, no_show, round_start, scoring, rules)

    @staticmethod
    def _round_start(t: Section, zone_name: str, problems: List[str]) -> Optional[datetime]:
        text = t.optional_text("round_start")
        if text is None:
            return None
        try:
            return datetime.strptime(text, ROUND_START_FORMAT).replace(tzinfo=ZoneInfo(zone_name))
        except ValueError:
            problems.append(f"'tournament.round_start' must look like '2026-10-04 18:00', got {text!r}")
        except (ZoneInfoNotFoundError, KeyError):
            pass                                      # the time zone problem is already recorded
        return None

    @staticmethod
    def _stats(st: Section, problems: List[str]) -> StatsConfig:
        url, code = st.text("url"), st.text("game_code")
        zone = st.text("timezone")
        if zone:
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                problems.append(f"'stats.timezone' is not a known time zone: {zone!r}")
        timeout = st.number("timeout_seconds", 0, True)
        st.finish()
        return StatsConfig(url, code, zone, timeout)

    @staticmethod
    def _playok(p: Section, problems: List[str]) -> PlayOkConfig:
        url, room = p.text("site_url"), p.integer("lobby_room", 0)
        selectors = p.strings("selectors", SELECTORS)
        raw = p.strings("patterns", tuple(PATTERNS))
        compiled: Dict[str, Pattern[str]] = {}
        for name, text in raw.items():
            if not text:
                continue
            try:
                pattern = re.compile(text)
            except re.error as exc:
                problems.append(
                    f"'playok.patterns.{name}' is not a valid regular expression: {exc}")
                continue
            missing = [g for g in PATTERNS[name] if g not in pattern.groupindex]
            if missing:
                problems.append(
                    f"'playok.patterns.{name}' must define named group(s): {', '.join(missing)}")
                continue
            compiled[name] = pattern
        p.finish()
        return PlayOkConfig(url, room, selectors, compiled)
