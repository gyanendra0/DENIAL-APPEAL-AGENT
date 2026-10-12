from src.db.models import DenialReasonCategory
from src.drafting.reason_words import REASON_WORDS
from src.synth.denial_letter import REASON_WORDING


def test_every_denial_reason_category_has_its_own_plain_words() -> None:
    assert set(REASON_WORDS) == set(DenialReasonCategory)
    assert len(set(REASON_WORDS.values())) == len(DenialReasonCategory)


def test_the_plain_words_are_those_the_denial_letters_use() -> None:
    # Two copies on purpose: `src/drafting` does not reach into `src/synth`.
    assert REASON_WORDS == REASON_WORDING


def test_no_plain_wording_holds_a_category_value_or_a_dollar_amount() -> None:
    # The fact check refuses "other" as the reason and reads every `$` amount.
    for words in REASON_WORDS.values():
        assert "_" not in words
        assert "$" not in words
        assert "other" not in words.split()
