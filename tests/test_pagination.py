"""Test cursor-based pagination utilities."""

import pytest

from app.api.pagination import decode_cursor, encode_cursor


def test_encode_decode_roundtrip():
    cursor = encode_cursor(42)
    decoded = decode_cursor(cursor)
    assert decoded is not None
    assert decoded["id"] == 42


def test_encode_decode_with_sort_value():
    cursor = encode_cursor(10, "100")
    decoded = decode_cursor(cursor)
    assert decoded is not None
    assert decoded["id"] == 10
    assert decoded["v"] == "100"


def test_decode_invalid_cursor():
    assert decode_cursor("not-valid-base64!!!") is None
    assert decode_cursor("") is None


def test_decode_missing_id():
    import base64
    import json
    cursor = base64.b64encode(json.dumps({"x": 1}).encode()).decode()
    assert decode_cursor(cursor) is None
