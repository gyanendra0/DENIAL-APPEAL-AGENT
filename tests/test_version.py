import re

from src import get_version


def test_version_is_semantic() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", get_version())
