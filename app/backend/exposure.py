"""Noise dose per the NIOSH recommended exposure limit: 85 dB for 8 hours is 100%, every +3 dB halves the
allowed time, and levels below 80 dB don't count. The stick's levels are uncalibrated (≈ dB), so this is a demo
indicator, not a compliance measurement."""
SHIFT_S = 8 * 3600
CRITERION_DB, EXCHANGE_DB, THRESHOLD_DB = 85, 3, 80


def dose_weight(level_db):
    """Shift-dose weight of one sample: 1 at 85 dB, 2 at 88 dB, 0 below the 80 dB threshold."""
    return 0 if level_db < THRESHOLD_DB else 2 ** ((level_db - CRITERION_DB) / EXCHANGE_DB)


def noise_dose_pct(weight_sum, sample_s):
    """Percent of a shift's allowed dose, from the summed weights of samples taken every sample_s seconds."""
    return weight_sum * sample_s / SHIFT_S * 100
