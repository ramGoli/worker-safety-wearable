import pandas as pd

import risk_model as rm


def test_simulated_history_is_repeatable_and_has_a_real_pattern():
    a = rm.simulate_history(workers=30, days=30, seed=7)
    b = rm.simulate_history(workers=30, days=30, seed=7)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) == 30 * 30 and set(a["source"]) == {"simulated"}
    assert 0.05 < a["had_incident"].mean() < 0.30
    by_zone = a.groupby("zone")["had_incident"].mean()
    assert by_zone["Workshop"] > by_zone["Office"]  # loud zones are riskier, as in the crew simulation


def test_model_learns_the_pattern_on_unseen_days():
    model, metrics = rm.train(rm.simulate_history(workers=30, days=30, seed=7))
    assert metrics["holdout_auc"] > 0.65
    assert metrics["shifts"] == 900 and 0 < metrics["incident_rate"] < 1


def test_risk_ranks_a_loud_long_shift_with_recent_incidents_above_a_quiet_one():
    model, _ = rm.train(rm.simulate_history(workers=30, days=30, seed=7))
    workers = pd.DataFrame([
        {"device_id": "a", "worker_name": "Loud", "zone": "Workshop", "hours": 10, "avg_noise_db": 93,
         "noise_dose_pct": 260, "motion_std": 0.12, "incidents_7d": 2},
        {"device_id": "b", "worker_name": "Quiet", "zone": "Office", "hours": 8, "avg_noise_db": 52,
         "noise_dose_pct": 0, "motion_std": 0.03, "incidents_7d": 0},
    ])
    scored = rm.score(model, workers)
    assert scored["risk"].between(0, 1).all()
    assert scored.loc[0, "risk"] > scored.loc[1, "risk"]
    assert len(scored.loc[0, "top_factors"]) == 2
    assert any("incident" in f or "noise" in f for f in scored.loc[0, "top_factors"])


def test_next_shift_uses_current_zone_last_shift_levels_and_recent_incidents():
    live = pd.DataFrame([
        {"device_id": "stick-01", "shift_date": pd.Timestamp("2026-10-13").date(), "avg_noise_db": 90.0, "motion_std": 0.1},
        {"device_id": "stick-01", "shift_date": pd.Timestamp("2026-10-14").date(), "avg_noise_db": 60.0, "motion_std": 0.05},
    ])
    workers = pd.DataFrame([{"device_id": "stick-01", "worker_name": "Alex Rivera", "zone": "Workshop"},
                            {"device_id": "crew-07", "worker_name": "New", "zone": None}])
    rows = rm.next_shift_rows(live, workers, {"stick-01": 2}).set_index("device_id")
    assert rows.loc["stick-01", "avg_noise_db"] == 60.0 and rows.loc["stick-01", "incidents_7d"] == 2
    assert rows.loc["stick-01", "zone"] == "Workshop" and rows.loc["stick-01", "hours"] == rm.PLANNED_HOURS
    assert rows.loc["crew-07", "zone"] == "Office" and rows.loc["crew-07", "avg_noise_db"] == rm.ZONES["Office"]


def _workers(**over):
    base = {"device_id": "a", "worker_name": "A", "zone": "Workshop", "hours": 8, "avg_noise_db": 92,
            "noise_dose_pct": 200, "motion_std": 0.06, "incidents_7d": 0}
    return pd.DataFrame([{**base, **over}])


def test_reasons_never_name_a_zone_the_worker_is_not_in():
    model, _ = rm.train(rm.simulate_history(workers=30, days=30, seed=7))
    for zone in rm.ZONES:
        factors = rm.score(model, _workers(zone=zone)).loc[0, "top_factors"]
        assert all(not f.startswith("works in") or f == f"works in {zone}" for f in factors), (zone, factors)


def test_a_burst_of_test_incidents_does_not_push_risk_past_the_cap_but_the_reason_shows_the_real_count():
    model, _ = rm.train(rm.simulate_history(workers=30, days=30, seed=7))
    five, many = rm.score(model, _workers(incidents_7d=5)), rm.score(model, _workers(incidents_7d=21))
    assert many.loc[0, "risk"] == five.loc[0, "risk"]
    assert "21 incidents in 7 days" in many.loc[0, "top_factors"]
