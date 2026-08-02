"""robots.txt の Allow / Disallow をRFC 9309の最長一致で評価する。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Rule:
    pattern: str
    allow: bool


def parse_groups(text: str) -> list[tuple[list[str], list[Rule]]]:
    groups: list[tuple[list[str], list[Rule]]] = []
    agents: list[str] = []
    rules: list[Rule] = []

    def finish_group() -> None:
        nonlocal agents, rules
        if agents:
            groups.append((agents, rules))
        agents = []
        rules = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if rules:
                finish_group()
            agents.append(value.lower())
        elif field in {"allow", "disallow"} and agents:
            # 空の Disallow は拒否なしを意味し、照合規則にはしない。
            if value:
                rules.append(Rule(pattern=value, allow=field == "allow"))
    finish_group()
    return groups


def agent_specificity(token: str, user_agent: str) -> int | None:
    if token == "*":
        return 0
    token = token.strip().lower()
    if token and token in user_agent.lower():
        return len(token)
    return None


def rule_matches(pattern: str, path_query: str) -> bool:
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    expression = re.escape(core).replace(r"\*", ".*")
    if anchored:
        expression += "$"
    return re.match(expression, path_query) is not None


def robots_can_fetch(text: str, user_agent: str, url: str) -> bool:
    """選択された最長User-agent群のうち、最長URL規則を適用する。"""
    matched_groups: list[tuple[int, list[Rule]]] = []
    for agents, rules in parse_groups(text):
        specificities = [agent_specificity(agent, user_agent) for agent in agents]
        matching = [value for value in specificities if value is not None]
        if matching:
            matched_groups.append((max(matching), rules))
    if not matched_groups:
        return True

    best_agent = max(specificity for specificity, _rules in matched_groups)
    candidate_rules = [
        rule
        for specificity, rules in matched_groups
        if specificity == best_agent
        for rule in rules
    ]
    parts = urlsplit(url)
    path_query = parts.path or "/"
    if parts.query:
        path_query += "?" + parts.query
    matching_rules = [rule for rule in candidate_rules if rule_matches(rule.pattern, path_query)]
    if not matching_rules:
        return True

    # RFC 9309: 最も長い規則を採用し、同長ならAllowを優先する。
    best_length = max(len(rule.pattern.rstrip("$")) for rule in matching_rules)
    best_rules = [rule for rule in matching_rules if len(rule.pattern.rstrip("$")) == best_length]
    return any(rule.allow for rule in best_rules)
