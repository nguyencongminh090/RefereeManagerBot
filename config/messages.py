"""The `[messages]` table: the chat texts of the bot, one table per language.

Every language table has the same keys, so a language cannot be half translated: a missing key or a
wrong `{placeholder}` stops start-up and names the table.
"""
from dataclasses import dataclass
from typing      import Dict, List, Mapping, Optional, Tuple

from config.reader import Section, template_problems

# Texts of one language and the {placeholders} the bot fills into each. The rules text is plain.
TEMPLATE_FIELDS = {
    "break_text"      : ("curr_time", "resume_time"),
    "bye"             : (),
    "set_usage"       : (),
    "break_usage"     : ("max_minutes",),
    "cheer_usage"     : (),
    "seats_unreadable": (),
    "set_failed"      : ("reason",),
    "sync_usage"      : (),
    "sync_failed"     : ("reason",),
    "result_pair"     : ("p1", "p2", "s1", "s2"),
    "result_team"     : ("team1", "team2", "t1", "t2"),
    "last_game"       : (),
    "final"           : (),
    "break_hint"      : (),
    "rules_unknown"   : ("languages",),
}
CHEER_FIELDS  = ("name",)
ALIASES_TABLE = "aliases"


@dataclass(frozen=True)
class MessageTexts:
    """The chat texts of one language.

    Attributes:
        rules: Rules reminder, written by `!rules`.
        break_text: Break announcement; may use {curr_time} and {resume_time}.
        bye: Text posted when the bot leaves a table.
        set_usage: Reply to a malformed !set.
        break_usage: Reply to a malformed !break; may use {max_minutes}.
        cheer_usage: Reply to a malformed !cheer.
        seats_unreadable: Reply when a command needs the seat names and they cannot be read.
        set_failed: Reply when the server refused !set; uses {reason}.
        sync_usage: Reply to `!sync` without a usable round start.
        sync_failed: Reply when `!sync` found no usable games or the page failed; uses {reason}.
        result_pair: Score line of a pair; uses {p1}, {p2}, {s1} and {s2}.
        result_team: Score line of the two teams; uses {team1}, {team2}, {t1} and {t2}.
        last_game: Written one game before the match ends.
        final: Written when the match is over.
        break_hint: Written one game before a break.
        rules_unknown: Reply to `!rules` with an unknown language; uses {languages}.
        cheers: Cheering sentences, each with {name}.
    """
    rules           : str
    break_text      : str
    bye             : str
    set_usage       : str
    break_usage     : str
    cheer_usage     : str
    seats_unreadable: str
    set_failed      : str
    sync_usage      : str
    sync_failed     : str
    result_pair     : str
    result_team     : str
    last_game       : str
    final           : str
    break_hint      : str
    rules_unknown   : str
    cheers          : Tuple[str, ...]


@dataclass(frozen=True)
class MessagesConfig:
    """The `[messages]` table.

    Attributes:
        languages: Texts by language name (lower case).
        aliases: Other spellings players may type after `!rules`, mapped to a language name.
    """
    languages: Mapping[str, MessageTexts]
    aliases  : Mapping[str, str]

    def texts(self, language: str) -> MessageTexts:
        """Returns the texts of a language known to the config (a KeyError means a bug)."""
        return self.languages[language]

    def language_names(self) -> Tuple[str, ...]:
        """Returns the configured languages in alphabetical order."""
        return tuple(sorted(self.languages))

    def resolve(self, name: str) -> Optional[str]:
        """Maps what a player typed (a language name or alias, any case) to a language, or None."""
        key = name.strip().lower()
        key = self.aliases.get(key, key)
        return key if key in self.languages else None


def load_messages(section: Section, tournament_language: str,
                  problems: List[str]) -> MessagesConfig:
    """Reads and validates the `[messages]` table.

    Args:
        section: The `[messages]` table.
        tournament_language: Language of `[tournament]`; it must have a table.
        problems: Receives one message per problem.
    """
    aliases = {a.lower(): target for a, target in section.strings(ALIASES_TABLE).items()}
    languages = {name: _load_language(table, f"messages.{name}", problems)
                 for name, table in section.tables(skip=(ALIASES_TABLE,)).items()}
    section.finish()
    _check_languages(languages, aliases, tournament_language, problems)
    return MessagesConfig(languages, aliases)


def _load_language(table: Section, path: str, problems: List[str]) -> MessageTexts:
    texts: Dict[str, str] = {name: table.text(name) for name in TEMPLATE_FIELDS}
    rules = table.text("rules")
    cheers = table.text_list("cheers", allow_empty=False)
    table.finish()
    for name, text in texts.items():
        problems.extend(template_problems(f"{path}.{name}", text, TEMPLATE_FIELDS[name]))
    for cheer in cheers:
        problems.extend(template_problems(f"{path}.cheers", cheer, CHEER_FIELDS, CHEER_FIELDS))
    return MessageTexts(rules=rules, cheers=cheers, **texts)


def _check_languages(languages: Mapping[str, MessageTexts], aliases: Mapping[str, str],
                     tournament_language: str, problems: List[str]) -> None:
    if not languages:
        problems.append("'messages' needs at least one language table, for example [messages.en]")
    for name in languages:
        if name != name.lower():
            problems.append(f"'messages.{name}' must be written in lower case")
    if languages and tournament_language not in languages:
        problems.append(f"'tournament.language' is {tournament_language!r} but there is no "
                        f"[messages.{tournament_language}] table")
    for alias, target in aliases.items():
        if target not in languages:
            problems.append(f"'messages.aliases.{alias}' points to {target!r}, which has no "
                            f"[messages.{target}] table")
