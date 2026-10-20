import pytest

from modsage.cli import parse_ids


def test_cli_accepts_uint64_max_and_rejects_overflow():
    assert parse_ids([str(2**64 - 1)]) == [str(2**64 - 1)]
    with pytest.raises(ValueError, match="uint64"):
        parse_ids([str(2**64)])
