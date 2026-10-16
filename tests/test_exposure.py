import pytest

from backend.exposure import dose_weight, noise_dose_pct

SAMPLES_PER_HOUR = 3600 // 3  # one heartbeat every 3 s


@pytest.mark.parametrize("level_db, hours, expected_pct", [
    (85, 8, 100.0),   # NIOSH REL: 85 dB for 8 h is a full shift dose
    (88, 4, 100.0),   # 3 dB exchange rate: +3 dB halves the allowed time
    (82, 8, 50.0),
    (94, 1, 100.0),
])
def test_niosh_dose(level_db, hours, expected_pct):
    weight = dose_weight(level_db) * SAMPLES_PER_HOUR * hours
    assert noise_dose_pct(weight, sample_s=3) == pytest.approx(expected_pct)


def test_levels_below_80_db_do_not_count():
    assert dose_weight(79.9) == 0
