from fall import FallDetector

STEP_MS = 10  # the device samples at roughly 100 Hz


def run(segments):
    """segments: list of (g, duration_ms). Returns [(t_ms, peak_g)] for each detected fall."""
    d, t, falls = FallDetector(), 0, []
    for g, dur in segments:
        for _ in range(dur // STEP_MS):
            if d.step(g, t):
                falls.append((t, d.peak_g))
            t += STEP_MS
    return falls


def test_resting_device_never_falls():
    assert run([(1.0, 10000)]) == []


def test_drop_onto_floor_is_one_fall_with_peak():
    falls = run([(1.0, 500), (0.2, 300), (3.5, 30), (1.0, 1000)])
    assert len(falls) == 1
    assert falls[0][1] == 3.5


def test_impact_sample_that_ends_free_fall_counts():
    assert len(run([(1.0, 200), (0.1, 250), (4.0, 10), (1.0, 600)])) == 1


def test_brief_jostle_is_not_a_fall():
    assert run([(1.0, 200), (0.3, 40), (3.0, 20), (1.0, 1000)]) == []


def test_toss_and_soft_catch_is_not_a_fall():
    assert run([(1.0, 200), (0.2, 300), (1.4, 50), (1.0, 2000)]) == []


def test_sitting_down_hard_is_not_a_fall():
    assert run([(1.0, 200), (0.6, 150), (2.2, 40), (1.0, 1000)]) == []


def test_vigorous_shaking_is_not_a_fall():
    assert run([(0.3, 20), (2.8, 20)] * 100) == []


def test_bounce_within_cooldown_counts_once_then_rearms():
    drop = [(0.2, 300), (3.5, 30), (1.0, 400)]
    assert len(run([(1.0, 200)] + drop + drop)) == 1
    assert len(run([(1.0, 200)] + drop + [(1.0, 3000)] + drop)) == 2
