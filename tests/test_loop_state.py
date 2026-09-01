"""Tests for loop_state.py's push_mode/pop_mode — the single mutation
surface for LoopState.mode_stack, replacing what used to be raw
.append()/.pop() scattered across main.py and frame_update.py. Plain
unit tests against a real LoopState() instance: both functions are
pure list operations, no fixtures or mocks needed.
"""

from tuicc.loop_state import LoopState, push_mode, pop_mode


def test_push_mode_appends_a_new_tier():
    loop_state = LoopState()
    push_mode(loop_state, "help")
    assert loop_state.mode_stack == ["normal", "help"]


def test_push_mode_is_idempotent_on_the_current_top():
    # The exact shape of the bug this guards against: a tier whose
    # entry condition gets re-checked every frame (e.g. a wifi
    # passphrase retry) must never double-push itself.
    loop_state = LoopState()
    push_mode(loop_state, "connectivity_passphrase")
    push_mode(loop_state, "connectivity_passphrase")
    push_mode(loop_state, "connectivity_passphrase")
    assert loop_state.mode_stack == ["normal", "connectivity_passphrase"]


def test_push_mode_still_pushes_a_different_tier_on_top():
    # Idempotency only applies to the current top — nesting a genuinely
    # different tier (e.g. "help_colors" on top of "help") must still work.
    loop_state = LoopState()
    push_mode(loop_state, "help")
    push_mode(loop_state, "help_colors")
    assert loop_state.mode_stack == ["normal", "help", "help_colors"]


def test_pop_mode_removes_the_top_tier():
    loop_state = LoopState()
    push_mode(loop_state, "help")
    pop_mode(loop_state)
    assert loop_state.mode_stack == ["normal"]


def test_pop_mode_at_normal_is_a_no_op():
    loop_state = LoopState()
    pop_mode(loop_state)
    pop_mode(loop_state)
    assert loop_state.mode_stack == ["normal"]


def test_push_then_pop_round_trips_to_the_original_stack():
    loop_state = LoopState()
    push_mode(loop_state, "help")
    push_mode(loop_state, "help_colors")
    pop_mode(loop_state)
    pop_mode(loop_state)
    assert loop_state.mode_stack == ["normal"]
