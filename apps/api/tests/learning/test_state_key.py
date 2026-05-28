"""Unit tests for :func:`aether_api.learning.state_key`.

``state_key`` is the canonical hash function that connects three
storage layers (``q_tables.table`` JSONB keys, ``episodic_memory.state_key``,
``episodic_memory.next_state_key``). Any drift in its output silently
invalidates every persisted Q-Table — so the contract is exercised
exhaustively here:

* **Determinism** — same dict ⇒ same hex digest, byte-stable across
  invocations and runs.
* **Order-independence** — key insertion order does not influence the
  output (``sort_keys=True`` is mandatory).
* **Lossless typing** — non-JSON-compatible values reject loudly rather
  than coerce; floats stay floats; ``None``/booleans round-trip.
* **Type safety** — wrong shape (non-dict, non-string keys), unsupported
  values (set, tuple), and non-finite floats raise
  :class:`StateKeyError`.

The canonical spec is ``specs/sleep-learning`` (engram #2069).
"""

from __future__ import annotations

import hashlib
import json
import math

import pytest
from aether_api.learning import StateKeyError, state_key


# ---------------------------------------------------------------------------
# Golden vectors — pinned hex digests so any drift in the canonical form
# (separator change, key-sort change, etc.) is caught immediately.
# ---------------------------------------------------------------------------
def _expected(payload: str) -> str:
    """Compute the SHA-256 hex digest of *payload* — a tiny re-implementation
    so the assertion is independent of the SUT.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TestStateKeyGoldenVectors:
    """Pinned digests for the canonical JSON encoding."""

    def test_empty_dict(self) -> None:
        assert state_key({}) == _expected("{}")

    def test_simple_dict(self) -> None:
        # Canonical form: {"a":1,"b":2}
        assert state_key({"a": 1, "b": 2}) == _expected('{"a":1,"b":2}')

    def test_string_values(self) -> None:
        # Canonical: {"symbol":"EURUSD","tf":"H1"}
        result = state_key({"symbol": "EURUSD", "tf": "H1"})
        assert result == _expected('{"symbol":"EURUSD","tf":"H1"}')

    def test_nested_dict(self) -> None:
        # Inner keys are also sorted: {"meta":{"x":1,"y":2}}
        result = state_key({"meta": {"y": 2, "x": 1}})
        assert result == _expected('{"meta":{"x":1,"y":2}}')

    def test_list_values_preserve_order(self) -> None:
        # Lists are positional — order matters and is preserved.
        assert state_key({"path": [1, 2, 3]}) == _expected('{"path":[1,2,3]}')

    def test_null_and_bool(self) -> None:
        result = state_key({"a": None, "b": True, "c": False})
        assert result == _expected('{"a":null,"b":true,"c":false}')


# ---------------------------------------------------------------------------
# Order-independence — keys may be supplied in any insertion order.
# ---------------------------------------------------------------------------
class TestStateKeyOrderIndependence:
    """``sort_keys=True`` is non-negotiable."""

    def test_two_keys_swapped(self) -> None:
        assert state_key({"a": 1, "b": 2}) == state_key({"b": 2, "a": 1})

    def test_three_keys_permuted(self) -> None:
        # All 6 permutations of {"a":1, "b":2, "c":3} hash identically.
        base = state_key({"a": 1, "b": 2, "c": 3})
        for permuted in (
            {"a": 1, "c": 3, "b": 2},
            {"b": 2, "a": 1, "c": 3},
            {"b": 2, "c": 3, "a": 1},
            {"c": 3, "a": 1, "b": 2},
            {"c": 3, "b": 2, "a": 1},
        ):
            assert state_key(permuted) == base

    def test_nested_keys_also_sorted(self) -> None:
        # Inner-level order must also be normalised.
        a = state_key({"outer": {"x": 1, "y": 2}})
        b = state_key({"outer": {"y": 2, "x": 1}})
        assert a == b


# ---------------------------------------------------------------------------
# Determinism — floats and stability across calls.
# ---------------------------------------------------------------------------
class TestStateKeyDeterminism:
    """Same input ⇒ same digest, every call, forever."""

    def test_float_values_stable(self) -> None:
        # Python's json module renders floats via repr(); the canonical
        # form for 0.1 is "0.1", for 1.5 is "1.5". Same inputs ⇒ same
        # digest across calls.
        a = state_key({"price": 1.5, "vol": 0.1})
        b = state_key({"price": 1.5, "vol": 0.1})
        assert a == b

    def test_float_canonical_matches_json_repr(self) -> None:
        # Pin the canonical form so a future json-encoder swap can't
        # silently change the hash. {"x":0.1} is the canonical str.
        assert state_key({"x": 0.1}) == _expected('{"x":0.1}')

    def test_many_invocations_identical(self) -> None:
        sample = {"a": 1, "b": [1, 2, 3], "c": {"d": "e"}}
        first = state_key(sample)
        for _ in range(50):
            assert state_key(sample) == first


# ---------------------------------------------------------------------------
# Rejections — anything outside the canonical type set raises.
# ---------------------------------------------------------------------------
class TestStateKeyRejections:
    """Out-of-band inputs MUST raise :class:`StateKeyError`."""

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key([1, 2, 3])  # type: ignore[arg-type]

    def test_non_string_key_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({1: "a"})  # type: ignore[dict-item]

    def test_nested_non_string_key_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"outer": {2: "nope"}})

    def test_set_value_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"x": {1, 2, 3}})

    def test_tuple_value_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"x": (1, 2, 3)})

    def test_bytes_value_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"x": b"raw"})

    def test_custom_object_value_raises(self) -> None:
        class Marker:
            pass

        with pytest.raises(StateKeyError):
            state_key({"x": Marker()})

    def test_nan_float_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"x": math.nan})

    def test_positive_inf_float_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"x": math.inf})

    def test_negative_inf_float_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"x": -math.inf})

    def test_nested_nan_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"outer": {"y": math.nan}})

    def test_nan_inside_list_raises(self) -> None:
        with pytest.raises(StateKeyError):
            state_key({"path": [1.0, math.nan, 3.0]})


# ---------------------------------------------------------------------------
# Return shape — 64 hex characters, lowercase.
# ---------------------------------------------------------------------------
class TestStateKeyReturnShape:
    """The digest must be a 64-char lowercase hex string."""

    def test_returns_string(self) -> None:
        assert isinstance(state_key({"a": 1}), str)

    def test_length_64(self) -> None:
        assert len(state_key({"a": 1})) == 64

    def test_lowercase_hex(self) -> None:
        digest = state_key({"a": 1})
        assert digest == digest.lower()
        # Every char is a valid hex digit.
        assert all(c in "0123456789abcdef" for c in digest)

    def test_matches_manual_sha256(self) -> None:
        # End-to-end: hashing the canonical JSON ourselves must agree.
        sample = {"b": 2, "a": 1}
        canonical = json.dumps(sample, sort_keys=True, separators=(",", ":"))
        manual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert state_key(sample) == manual
