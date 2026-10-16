import math

import pytest

from noise import ac_dbfs


def sine(amplitude, n=1600, offset=0):
    return [int(offset + amplitude * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n)]


def test_full_scale_sine_is_about_minus_3_dbfs():
    assert ac_dbfs(sine(32767)) == pytest.approx(-3.0, abs=0.3)


def test_ten_times_quieter_is_20_db_lower():
    assert ac_dbfs(sine(3276)) - ac_dbfs(sine(32767)) == pytest.approx(-20.0, abs=0.3)


def test_dc_offset_from_the_mic_is_ignored():
    assert ac_dbfs(sine(3276, offset=5000)) == pytest.approx(ac_dbfs(sine(3276)), abs=0.3)


def test_silence_has_a_floor_instead_of_minus_infinity():
    assert ac_dbfs([0] * 1600) == pytest.approx(-90.3, abs=0.1)
