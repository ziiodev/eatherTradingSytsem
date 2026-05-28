"""Unit tests for :func:`aether_api.learning.q_update`.

The Q-update function is the **pure-math heart** of the sleep-learning
loop. It encodes the canonical Bellman update::

    Q(s, a) ← Q(s, a) + α · (r + γ · max_a' Q(s', a') − Q(s, a))

Every property exercised here is fixed by the canonical spec
``specs/sleep-learning`` (engram #2069) — break any of these tests and
the learning substrate falls out of alignment with the spec.

The function MUST be:

* **Pure** — same inputs ⇒ same output, no side effects.
* **Deterministic** — no random, no clock, no I/O.
* **Validated** — alpha ∈ [0, 1]; gamma ∈ [0, 1]; rejects NaN / Inf.

Invalid inputs raise :class:`QUpdateError` (the typed boundary the
sleep orchestrator catches when escalating to the human).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from aether_api.learning import QUpdateError, q_update


# ---------------------------------------------------------------------------
# Golden vectors — every expected output is computed by hand from the formula.
# ---------------------------------------------------------------------------
class TestQUpdateGoldenVectors:
    """Algebraic correctness against pre-computed expected values."""

    def test_standard_update(self) -> None:
        # q=0.5, r=1.0, max_next=0.8, alpha=0.2, gamma=0.9
        # new = 0.5 + 0.2 * (1.0 + 0.9*0.8 - 0.5)
        #     = 0.5 + 0.2 * (1.0 + 0.72 - 0.5)
        #     = 0.5 + 0.2 * 1.22
        #     = 0.5 + 0.244
        #     = 0.744
        result = q_update(
            q_value=0.5,
            reward=1.0,
            max_next_q=0.8,
            alpha=0.2,
            gamma=0.9,
        )
        assert result == pytest.approx(0.744)

    def test_zero_reward(self) -> None:
        # q=0.4, r=0.0, max_next=0.5, alpha=0.2, gamma=0.9
        # new = 0.4 + 0.2 * (0.0 + 0.45 - 0.4) = 0.4 + 0.01 = 0.41
        result = q_update(
            q_value=0.4,
            reward=0.0,
            max_next_q=0.5,
            alpha=0.2,
            gamma=0.9,
        )
        assert result == pytest.approx(0.41)

    def test_terminal_state_max_next_q_zero(self) -> None:
        # Terminal: max_next_q must be 0.
        # q=0.3, r=1.0, max_next=0.0, alpha=0.2, gamma=0.9
        # new = 0.3 + 0.2 * (1.0 - 0.3) = 0.3 + 0.14 = 0.44
        result = q_update(
            q_value=0.3,
            reward=1.0,
            max_next_q=0.0,
            alpha=0.2,
            gamma=0.9,
        )
        assert result == pytest.approx(0.44)

    def test_alpha_015_low_band(self) -> None:
        # Canonical alpha_normal floor. q=0.0, r=2.0, max_next=0.0, gamma=0.92
        # new = 0.0 + 0.15 * (2.0 + 0.0 - 0.0) = 0.30
        result = q_update(
            q_value=0.0,
            reward=2.0,
            max_next_q=0.0,
            alpha=0.15,
            gamma=0.92,
        )
        assert result == pytest.approx(0.30)

    def test_alpha_035_high_band(self) -> None:
        # Canonical alpha_special. q=0.0, r=2.0, max_next=0.0, gamma=0.92
        # new = 0.0 + 0.35 * 2.0 = 0.70
        result = q_update(
            q_value=0.0,
            reward=2.0,
            max_next_q=0.0,
            alpha=0.35,
            gamma=0.92,
        )
        assert result == pytest.approx(0.70)

    def test_gamma_092_default_discount(self) -> None:
        # q=1.0, r=0.5, max_next=2.0, alpha=0.2, gamma=0.92
        # new = 1.0 + 0.2 * (0.5 + 0.92*2.0 - 1.0)
        #     = 1.0 + 0.2 * (0.5 + 1.84 - 1.0)
        #     = 1.0 + 0.2 * 1.34
        #     = 1.0 + 0.268
        #     = 1.268
        result = q_update(
            q_value=1.0,
            reward=0.5,
            max_next_q=2.0,
            alpha=0.2,
            gamma=0.92,
        )
        assert result == pytest.approx(1.268)

    def test_negative_reward_decreases_q(self) -> None:
        # A losing trade reduces Q. q=0.5, r=-1.0, max_next=0.0, alpha=0.2, gamma=0.9
        # new = 0.5 + 0.2 * (-1.0 - 0.5) = 0.5 - 0.30 = 0.20
        result = q_update(
            q_value=0.5,
            reward=-1.0,
            max_next_q=0.0,
            alpha=0.2,
            gamma=0.9,
        )
        assert result == pytest.approx(0.20)

    def test_alpha_zero_leaves_q_untouched(self) -> None:
        # α=0 means "do not learn" — Q stays put.
        result = q_update(
            q_value=0.7,
            reward=99.0,
            max_next_q=42.0,
            alpha=0.0,
            gamma=0.9,
        )
        assert result == pytest.approx(0.7)

    def test_alpha_one_full_replacement(self) -> None:
        # α=1 replaces Q with the bootstrap target r + γ·max_next.
        # target = 1.0 + 0.9 * 2.0 = 2.8
        result = q_update(
            q_value=0.5,
            reward=1.0,
            max_next_q=2.0,
            alpha=1.0,
            gamma=0.9,
        )
        assert result == pytest.approx(2.8)

    def test_gamma_zero_myopic(self) -> None:
        # γ=0 ignores future value — pure reward learning.
        # q=0.5, r=1.0, max_next=10.0 (ignored), alpha=0.2
        # new = 0.5 + 0.2 * (1.0 - 0.5) = 0.6
        result = q_update(
            q_value=0.5,
            reward=1.0,
            max_next_q=10.0,
            alpha=0.2,
            gamma=0.0,
        )
        assert result == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Validation — invalid hyperparameters / non-finite inputs.
# ---------------------------------------------------------------------------
@contextmanager
def _raises_qupdate() -> Iterator[None]:
    """Tiny alias so the assertion lines read like the spec."""
    with pytest.raises(QUpdateError):
        yield


class TestQUpdateValidation:
    """Out-of-range alpha/gamma and non-finite inputs must raise."""

    @pytest.mark.parametrize("alpha", [-0.01, -1.0, 1.01, 2.0, math.inf, -math.inf])
    def test_alpha_out_of_range_raises(self, alpha: float) -> None:
        with _raises_qupdate():
            q_update(q_value=0.5, reward=1.0, max_next_q=0.0, alpha=alpha, gamma=0.9)

    @pytest.mark.parametrize("gamma", [-0.01, -1.0, 1.01, 2.0, math.inf, -math.inf])
    def test_gamma_out_of_range_raises(self, gamma: float) -> None:
        with _raises_qupdate():
            q_update(q_value=0.5, reward=1.0, max_next_q=0.0, alpha=0.2, gamma=gamma)

    def test_alpha_nan_raises(self) -> None:
        with _raises_qupdate():
            q_update(q_value=0.5, reward=1.0, max_next_q=0.0, alpha=math.nan, gamma=0.9)

    def test_gamma_nan_raises(self) -> None:
        with _raises_qupdate():
            q_update(q_value=0.5, reward=1.0, max_next_q=0.0, alpha=0.2, gamma=math.nan)

    @pytest.mark.parametrize("q_value", [math.nan, math.inf, -math.inf])
    def test_q_value_non_finite_raises(self, q_value: float) -> None:
        with _raises_qupdate():
            q_update(q_value=q_value, reward=1.0, max_next_q=0.0, alpha=0.2, gamma=0.9)

    @pytest.mark.parametrize("reward", [math.nan, math.inf, -math.inf])
    def test_reward_non_finite_raises(self, reward: float) -> None:
        with _raises_qupdate():
            q_update(q_value=0.5, reward=reward, max_next_q=0.0, alpha=0.2, gamma=0.9)

    @pytest.mark.parametrize("max_next_q", [math.nan, math.inf, -math.inf])
    def test_max_next_q_non_finite_raises(self, max_next_q: float) -> None:
        with _raises_qupdate():
            q_update(q_value=0.5, reward=1.0, max_next_q=max_next_q, alpha=0.2, gamma=0.9)


# ---------------------------------------------------------------------------
# Purity — same inputs, same output, no observable side effects.
# ---------------------------------------------------------------------------
class TestQUpdatePurity:
    """The spec demands a pure function: repeatable, side-effect free."""

    def test_same_inputs_same_output_many_calls(self) -> None:
        args = {"q_value": 0.5, "reward": 1.0, "max_next_q": 0.8, "alpha": 0.2, "gamma": 0.9}
        first = q_update(**args)
        for _ in range(100):
            assert q_update(**args) == first

    def test_does_not_mutate_caller_state(self) -> None:
        # The function takes scalars; the contract is "no shared mutable
        # state" — assert by running back-to-back with intervening calls
        # that change nothing observable.
        a = q_update(q_value=0.1, reward=0.1, max_next_q=0.0, alpha=0.2, gamma=0.9)
        _ = q_update(q_value=0.9, reward=-5.0, max_next_q=10.0, alpha=0.35, gamma=0.92)
        b = q_update(q_value=0.1, reward=0.1, max_next_q=0.0, alpha=0.2, gamma=0.9)
        assert a == b
