import pytest

from storageops.formatting import format_bytes, parse_size


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500", 500),
        ("3b", 3),
        ("1KB", 1024),
        ("4k", 4096),
        ("1.5 g", 1536 * 1024**2),
        ("750MiB", 750 * 1024**2),
        (" 2TB ", 2 * 1024**4),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "GB", "-5MB", "5 XB", "5ib", "1,000"])
def test_parse_size_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_size(text)


def test_format_bytes_round_trips_with_parse_size():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1536) == "1.5 KB"
    assert parse_size(format_bytes(20 * 1024**3).replace(" ", "")) == 20 * 1024**3
