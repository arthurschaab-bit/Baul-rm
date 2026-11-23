import pandas as pd
import pytest

from baul_rm.supplement import classify_day, day_values, early_start, read_daily_pce, second_at


def test_calendar_and_location_precede_coverage():
    assert classify_day("2026-11-08", "nicht belegt", 17, set(), {}) == "Ruhetag"
    assert classify_day("2026-11-07", "aussen", 46800, set(), {}) == "a"
    assert classify_day("2026-11-07", "aussen", 46800, set(), {"2026-11-07": "Messprotokoll"}) == "Ruhetag"
    assert classify_day("2026-11-09", "aussen", 46800, {"2026-11-09"}, {}) == "Ruhetag"
    assert classify_day("2026-11-09", "nicht belegt", 46800, set(), {}) == "Messort nicht belegt"
    assert classify_day("2026-11-09", "aussen", 32760, set(), {}) == "b"


def test_daily_export_keeps_only_measured_pce_seconds(tmp_path):
    p = tmp_path / "day.csv"
    p.write_text("Zeit;LAeq_dB;LAFmax_dB;Samples;Quelle\n"
                 "2026-11-09T07:00:00+01:00;105;107;2;PCE_323\n"
                 "2026-11-09T07:00:01+01:00;50;51;3;MIKROFON\n"
                 "2026-11-09T07:00:02+01:00;50;51;0;PCE_323\n",
                 encoding="utf-8")
    frame = read_daily_pce(p, "2026-11-09")
    values = day_values(frame, "2026-11-09", "aussen")
    assert values["sekunden"] == 1
    assert values["laeq_db"] == pytest.approx(100)


def test_early_start_requires_baseline_and_diary_alignment():
    day = "2026-11-09"
    begin = second_at(day, "06:00")
    frame = pd.DataFrame({"leq": [40] * 1800 + [60] * 60},
                         index=[*range(begin, begin+1800), *range(begin+2700, begin+2760)])
    assert early_start(frame, day, 45)["minute"] == 45
    assert early_start(frame, day, 35)["minute"] is None
    assert early_start(frame.iloc[-60:], day, 45)["minute"] is None
