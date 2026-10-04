"""Reading helpers for the config loader: error type, typed TOML tables and the `.env` parser.

`Section` reads typed values from one TOML table and records every problem instead of raising,
so the loader can report all of them at once.
"""
import difflib
import string
from pathlib import Path
from typing  import Any, Dict, List, Optional, Sequence, Tuple


class ConfigError(Exception):
    """Raised once, listing every problem found.

    Attributes:
        source: Path of the file that was read.
        problems: One message per problem.
    """
    def __init__(self, source: str, problems: Sequence[str]):
        self.source   = source
        self.problems = list(problems)
        lines = "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(f"{source}: {len(self.problems)} problem(s)\n{lines}")


# ----------------------------------------------------------------------------- reading helpers
class Section:
    """One TOML table that reads typed values and records problems instead of raising.

    Unknown keys (usually typos) are reported with a suggestion when `finish()` is called.
    """

    def __init__(self, data: Any, path: str, problems: List[str]):
        self._path, self._problems, self._used = path, problems, set()
        if isinstance(data, dict):
            self._data = data
        else:
            self._data = {}
            problems.append(f"[{path}] is missing or is not a table")

    def _key(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def _raw(self, key: str, required: bool = True) -> Any:
        self._used.add(key)
        if key not in self._data:
            if required:
                self._problems.append(f"missing key '{self._key(key)}'")
            return None
        return self._data[key]

    def _bad(self, key: str, expected: str, value: Any) -> None:
        self._problems.append(f"'{self._key(key)}' must be {expected}, got {value!r}")

    def text(self, key: str, allow_empty: bool = False) -> str:
        """Reads a required string; a problem is recorded and \"\" returned if it is bad."""
        value = self._raw(key)
        if value is None:
            return ""
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            self._bad(key, "a non-empty string" if not allow_empty else "a string", value)
            return ""
        return value

    def optional_text(self, key: str) -> Optional[str]:
        """Reads an optional string; blank counts as absent."""
        value = self._raw(key, required=False)
        if value is None:
            return None
        if not isinstance(value, str):
            self._bad(key, "a string", value)
            return None
        return value.strip() or None

    def integer(self, key: str, minimum: Optional[int] = None,
                required: bool = True) -> Optional[int]:
        """Reads an integer (not a bool) of at least `minimum`; None when optional and absent."""
        value = self._raw(key, required)
        if value is None:
            return 0 if required else None
        is_int = isinstance(value, int) and not isinstance(value, bool)
        if not is_int or (minimum is not None and value < minimum):
            self._bad(key, f"an integer >= {minimum}" if minimum is not None else "an integer",
                      value)
            return 0 if required else None
        return value

    def number(self, key: str, minimum: Optional[float] = 0.0, exclusive: bool = False) -> float:
        """Reads an int or float above (`exclusive`) or at least `minimum`; None means no bound."""
        value = self._raw(key)
        if value is None:
            return 0.0
        too_low = minimum is not None and (value < minimum or (exclusive and value == minimum))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or too_low:
            operator = ">" if exclusive else ">="
            self._bad(key, "a number" if minimum is None else f"a number {operator} {minimum:g}",
                      value)
            return 0.0
        return float(value)

    def boolean(self, key: str) -> bool:
        """Reads a required true/false value."""
        value = self._raw(key)
        if value is None:
            return False
        if not isinstance(value, bool):
            self._bad(key, "true or false", value)
            return False
        return value

    def choice(self, key: str, options: Sequence[str]) -> str:
        """Reads a string that must be one of `options` (the first on error)."""
        value = self._raw(key)
        if value is None:
            return options[0]
        if value not in options:
            self._bad(key, f"one of {', '.join(options)}", value)
            return options[0]
        return value

    def text_list(self, key: str, allow_empty: bool = True,
                  allowed: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
        """Reads a list of non-empty strings, optionally limited to `allowed` values."""
        value = self._raw(key)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value) \
                or (not allow_empty and not value):
            suffix = "" if allow_empty else " (at least one)"
            self._bad(key, "a list of non-empty strings" + suffix, value)
            return ()
        if allowed is not None:
            bad = [v for v in value if v not in allowed]
            if bad:
                self._bad(key, f"a list drawn from {', '.join(allowed)}", bad)
                return ()
        return tuple(v.strip() for v in value)

    def table(self, key: str) -> "Section":
        """Returns the nested table as a section that shares this one's problem list."""
        raw = self._raw(key)
        return Section(raw, self._key(key), self._problems)

    def tables(self, skip: Sequence[str] = ()) -> Dict[str, "Section"]:
        """Returns every nested table except those named in `skip`, by name."""
        return {key: self.table(key) for key, value in self._data.items()
                if key not in skip and isinstance(value, dict)}

    def strings(self, key: str, known: Optional[Sequence[str]] = None) -> Dict[str, str]:
        """A table of name -> string. With `known`, those names are required and others rejected."""
        sec = self.table(key)
        out = {}
        for name in (known if known is not None else list(sec._data)):
            out[name] = sec.text(name)
        if known is not None:
            sec.finish()
        else:
            sec._used.update(sec._data)
        return out

    def finish(self) -> None:
        """Records a problem for every key that was never read."""
        for key in self._data:
            if key not in self._used:
                hint = difflib.get_close_matches(key, list(self._used), n=1)
                suggestion = f" (did you mean '{hint[0]}'?)" if hint else ""
                self._problems.append(f"unknown key '{self._key(key)}'{suggestion}")


# ----------------------------------------------------------------------------- templates
def template_problems(key: str, text: str, allowed: Sequence[str],
                      required: Sequence[str] = ()) -> List[str]:
    """Checks the `{placeholders}` of a chat text against the names it may (and must) use.

    Args:
        key: Config key shown in the messages, for example `messages.cheers`.
        text: The template.
        allowed: Placeholder names the bot fills in.
        required: Placeholder names the text must contain.

    Returns:
        One message per problem; empty when the template is fine.
    """
    try:
        used = {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}
    except ValueError as exc:
        return [f"'{key}' is not a valid template: {exc}"]
    unknown = sorted(used - set(allowed))
    missing = sorted(set(required) - used)
    allowed_text = ", ".join("{" + n + "}" for n in allowed) or "no placeholders"
    problems = [f"'{key}' may only use {allowed_text}, not {{{n}}}" for n in unknown]
    problems += [f"'{key}' must contain {{{n}}}" for n in missing]
    return problems


# ----------------------------------------------------------------------------- .env
def parse_env_file(path: str) -> Dict[str, str]:
    """Minimal KEY=VALUE reader: comments, blank lines, `export` prefix and single/double quotes."""
    values: Dict[str, str] = {}
    file = Path(path)
    if not file.is_file():
        return values
    for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            raise ConfigError(str(file), [f"line {number}: expected KEY=VALUE"])
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values
