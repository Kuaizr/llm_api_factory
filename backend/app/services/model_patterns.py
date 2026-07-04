from __future__ import annotations

import re

MAX_MODEL_PATTERN_LENGTH = 128

_NESTED_QUANTIFIER_PATTERN = re.compile(
    r"\((?:\?:)?[^()]*[+*][^()]*\)\s*(?:[+*?]|\{\d+(?:,\d*)?\})"
)
_NESTED_RANGE_QUANTIFIER_PATTERN = re.compile(
    r"\((?:\?:)?[^()]*\{\d+(?:,\d*)?\}[^()]*\)\s*(?:[+*?]|\{\d+(?:,\d*)?\})"
)
_BACKREFERENCE_PATTERN = re.compile(r"\\[1-9]")
_QUANTIFIED_GROUP_PATTERN = re.compile(r"\((?:\?:)?([^()]*)\)\s*(?:[+*?]|\{\d+(?:,\d*)?\})")


class UnsafeModelPatternError(ValueError):
    pass


def validate_model_pattern(pattern: str) -> None:
    if not isinstance(pattern, str) or not pattern:
        raise UnsafeModelPatternError("Model pattern is required")
    if len(pattern) > MAX_MODEL_PATTERN_LENGTH:
        raise UnsafeModelPatternError("Model pattern is too long")
    if _BACKREFERENCE_PATTERN.search(pattern):
        raise UnsafeModelPatternError("Backreferences are not allowed in model patterns")
    if _NESTED_QUANTIFIER_PATTERN.search(pattern) or _NESTED_RANGE_QUANTIFIER_PATTERN.search(
        pattern
    ):
        raise UnsafeModelPatternError("Nested quantifiers are not allowed in model patterns")
    for match in _QUANTIFIED_GROUP_PATTERN.finditer(pattern):
        group_body = match.group(1)
        if "|" in group_body:
            raise UnsafeModelPatternError(
                "Alternation inside quantified groups is not allowed in model patterns"
            )
        if re.search(r"(?:[+*?]|\{\d+(?:,\d*)?\})", group_body):
            raise UnsafeModelPatternError("Nested quantifiers are not allowed in model patterns")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise UnsafeModelPatternError("Invalid model pattern") from exc


def compile_model_pattern(pattern: str) -> re.Pattern[str]:
    validate_model_pattern(pattern)
    return re.compile(pattern)


def model_pattern_matches(pattern: str, model_alias: str) -> bool:
    try:
        matcher = compile_model_pattern(pattern)
    except UnsafeModelPatternError:
        return False
    return matcher.match(model_alias) is not None
