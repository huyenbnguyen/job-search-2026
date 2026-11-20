"""robots.txt rules, matched as RFC 9309 specifies.

The standard library's RobotFileParser predates RFC 9309: it ignores `*` and `$`
wildcards and applies the first matching rule instead of the longest one. This module
implements the RFC's matching:
- a crawler uses the groups whose user-agent equals its product token (the name before
  any "/version"), compared case-insensitively, else the `*` groups, else no rules;
- groups that name the same user-agent are combined;
- a rule matches when its pattern matches the start of the URL's path and query, with
  `*` matching any characters and a final `$` anchoring the end;
- the longest matching pattern wins, and `allow` wins a tie;
- `/robots.txt` itself is always allowed.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")


@dataclass(frozen=True)
class _Rule:
    allow: bool
    length: int
    regex: re.Pattern[str]


class RobotsRules:
    def __init__(self, groups: dict[str, list[_Rule]]) -> None:
        self._groups = groups

    @classmethod
    def allow_all(cls) -> "RobotsRules":
        return cls({})

    @classmethod
    def disallow_all(cls) -> "RobotsRules":
        return cls.parse("User-agent: *\nDisallow: /\n")

    @classmethod
    def parse(cls, text: str) -> "RobotsRules":
        groups: dict[str, list[_Rule]] = {}
        agents: list[str] = []
        in_rules = False
        for line in text.splitlines():
            key, sep, value = line.split("#", 1)[0].partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if in_rules:  # A user-agent line after rules starts a new group.
                    agents, in_rules = [], False
                agent = value.lower()
                agents.append(agent)
                groups.setdefault(agent, [])
            elif key in ("allow", "disallow"):
                in_rules = True
                if agents and value:  # An empty pattern is no rule.
                    rule = _compile(key == "allow", value)
                    for agent in agents:
                        groups[agent].append(rule)
        return cls(groups)

    def allows(self, user_agent: str, url: httpx.URL) -> bool:
        token = user_agent.split("/", 1)[0].strip().lower()
        rules = self._groups.get(token)
        if rules is None:
            rules = self._groups.get("*", [])
        target = _normalize(url.raw_path.decode("ascii"))
        if target == "/robots.txt":
            return True
        best: _Rule | None = None
        for rule in rules:
            if rule.regex.match(target) and (
                best is None or (rule.length, rule.allow) > (best.length, best.allow)
            ):
                best = rule
        return best is None or best.allow


def _compile(allow: bool, pattern: str) -> _Rule:
    # Non-ASCII characters are compared in their percent-encoded form.
    pattern = _normalize("".join(c if c.isascii() else quote(c) for c in pattern))
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if c == "*" else re.escape(c) for c in body)
    return _Rule(allow, len(pattern), re.compile(regex + (r"\Z" if anchored else ""), re.S))


def _normalize(path: str) -> str:
    return _PERCENT_ESCAPE.sub(lambda m: m[0].upper(), path)
