"""Message-classification patterns shared by the GroupMe and Slack bots."""

import re

COVERAGE_PATTERNS = [
    r"\bi can[’']?t make my shift\b",
    r"\bi cant make my shift\b",
    r"\bi can[’']?t make my shift today\b",
    r"\bi cant make my shift today\b",
    r"\bi can[’']?t make my shift tomorrow\b",
    r"\bi cant make my shift tomorrow\b",
    r"\bi can[’']?t make it\b",
    r"\bi cant make it\b",
    r"\bi can[’']?t make it today\b",
    r"\bi cant make it today\b",
    r"\bi can[’']?t make it tomorrow\b",
    r"\bi cant make it tomorrow\b",
    r"\bi won[’']?t be able to make my shift\b",
    r"\bi wont be able to make my shift\b",
    r"\bi won[’']?t be able to make my shift today\b",
    r"\bi wont be able to make my shift today\b",
    r"\bi won[’']?t be able to make my shift tomorrow\b",
    r"\bi wont be able to make my shift tomorrow\b",
    r"\bi won[’']?t be able to make it\b",
    r"\bi wont be able to make it\b",
    r"\bi won[’']?t be able to make it today\b",
    r"\bi wont be able to make it today\b",
    r"\bi won[’']?t be able to make it tomorrow\b",
    r"\bi wont be able to make it tomorrow\b",
    r"\bcan someone cover me\b",
    r"\bcan someone cover me today\b",
    r"\bcan someone cover me tomorrow\b",
    r"\bcan someone cover my shift\b",
    r"\bcan someone cover my shift today\b",
    r"\bcan someone cover my shift tomorrow\b",
    r"\bneed coverage\b",
    r"\bneed coverage today\b",
    r"\bneed coverage tomorrow\b",
]

SICK_PATTERNS = [
    r"\bi[’']?m sick\b",
    r"\bim sick\b",
    r"\bi am sick\b",
    r"\bi[’']?m not feeling well\b",
    r"\bim not feeling well\b",
    r"\bi am not feeling well\b",
    r"\bi don[’']?t feel well\b",
    r"\bi dont feel well\b",
    r"\bi have a fever\b",
    r"\bi[’']?m throwing up\b",
    r"\bim throwing up\b",
    r"\bi have diarrhea\b",
    r"\bi[’']?m vomiting\b",
    r"\bim vomiting\b",
    r"\bi[’']?m nauseous\b",
    r"\bim nauseous\b",
    r"\bi[’']?m not feeling good\b",
    r"\bim not feeling good\b",
    r"\bi can[’']?t make my shift because i[’']?m sick\b",
    r"\bi cant make my shift because im sick\b",
    r"\bi can[’']?t make it because i[’']?m sick\b",
    r"\bi cant make it because im sick\b",
]


def matches_patterns(text: str, patterns: list[str]) -> bool:
    lower_text = text.lower().strip()
    return any(re.search(pattern, lower_text) for pattern in patterns)
