import pytest

from app.services import model_patterns
from app.services.model_patterns import (
    MAX_MODEL_PATTERN_LENGTH,
    UnsafeModelPatternError,
    compile_model_pattern,
    model_pattern_matches,
    validate_model_pattern,
)


def test_compile_model_pattern_reuses_cached_regex() -> None:
    first = compile_model_pattern("gpt-.*")
    second = compile_model_pattern("gpt-.*")

    assert first is second
    assert first.match("gpt-5") is not None


@pytest.mark.parametrize(
    "pattern, message",
    [
        ("^(a+)+$", "Nested quantifiers"),
        (r"^(gpt)-\1$", "Backreferences"),
        ("(" * (MAX_MODEL_PATTERN_LENGTH + 1), "too long"),
        ("[", "Invalid model pattern"),
    ],
)
def test_validate_model_pattern_rejects_unsafe_patterns(
    pattern: str,
    message: str,
) -> None:
    with pytest.raises(UnsafeModelPatternError, match=message):
        validate_model_pattern(pattern)


def test_model_pattern_matches_treats_unsafe_pattern_as_no_match() -> None:
    assert model_pattern_matches("^(a+)+$", "aaaaaaaaaaaaaaaa") is False


def test_model_pattern_matches_treats_timeout_as_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutMatcher:
        def match(self, *_args: object, **_kwargs: object) -> object:
            raise TimeoutError

    monkeypatch.setattr(
        model_patterns,
        "compile_model_pattern",
        lambda _pattern: TimeoutMatcher(),
    )

    assert model_patterns.model_pattern_matches(".*", "gpt-5") is False
