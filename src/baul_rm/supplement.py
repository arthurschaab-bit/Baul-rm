"""Evidence rules for the supplementary construction-noise documentation."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .meteo_analysis import coverage_tier

TZ = "Europe/Berlin"
DAY_SECONDS = 13 * 60 * 60
MIN_REPORT_SECONDS = 60
MIN_REST_COVERAGE = .70


def second_at(day, time):
    return int(pd.Timestamp(f"{day} {time}", tz=TZ).timestamp())


def energetic_mean(levels):
    levels = np.asarray(levels, dtype=float)
    return float(10 * np.log10(np.mean(10 ** (levels / 10)))) if len(levels) else None


def read_daily_pce(path: Path, day: str):
    """Read only valid PCE seconds from a single canonical daily app export."""
    cols = ["Zeit", "LAeq_dB", "LAFmax_dB", "Samples", "Quelle"]
    data = pd.read_csv(path, sep=";", decimal=",", usecols=cols)
    valid = (
        data.Quelle.eq("PCE_323")
        & (pd.to_numeric(data.Samples, errors="coerce") > 0)
        & data.LAeq_dB.between(0, 160, inclusive="right")
    )
    selected = data.loc[valid]
    timestamps = pd.to_datetime(selected.Zeit, utc=True, format="mixed")
    if not timestamps.dt.tz_convert(TZ).dt.strftime("%Y-%m-%d").eq(day).all():
        raise ValueError(f"Timestamp outside canonical day {day}")
    seconds = pd.Index(timestamps.map(lambda x: int(x.timestamp())), name="second")
    if (timestamps.dt.microsecond != 0).any() or not seconds.is_unique:
        raise ValueError(f"Expected unique whole seconds on {day}")
    return pd.DataFrame(
        {"leq": selected.LAeq_dB.clip(upper=100).to_numpy(),
         "maximum": selected.LAFmax_dB.clip(upper=100).to_numpy(),
         "source": "PCE_323"},
        index=seconds,
    ).sort_index()


def daytime(frame, day):
    return frame.loc[(frame.index >= second_at(day, "07:00")) &
                     (frame.index < second_at(day, "20:00"))]


def day_values(frame, day, location):
    part = daytime(frame, day)
    count = len(part)
    return {
        "tag": day, "sekunden": count, "abdeckung": count / DAY_SECONDS,
        "laeq_db": energetic_mean(part.leq), "messort_art": location,
        "stufe": coverage_tier(count),
    }


def classify_day(day, location, seconds, holidays, saturday_rest):
    """Select days without looking at the sound level."""
    weekday = date.fromisoformat(day).weekday()
    if weekday == 6 or day in holidays or (weekday == 5 and day in saturday_rest):
        return "Ruhetag"
    if seconds < MIN_REPORT_SECONDS:
        return "Kurzaufzeichnung"
    if location == "innen":
        return "Innenraummessung"
    if location != "aussen":
        return "Messort nicht belegt"
    if seconds >= 42120:
        return "a"
    return "b" if seconds >= 32760 else "d"


def early_start(frame, day, diary_minute, tolerance=2):
    """Search the signed clock window about the separately recorded diary time."""
    start = second_at(day, "06:00")
    part = frame.loc[(frame.index >= start) & (frame.index < start + 3600)]
    minutes = (part.index - start) // 60
    counts = part.groupby(minutes).size()
    levels = (10 * np.log10((10 ** (part.leq / 10)).groupby(minutes).mean()))
    baseline = levels.loc[levels.index < 30]
    result = {"minute": None, "baseline_minutes": len(baseline),
              "reference_db": float(baseline.median()) if len(baseline) else None,
              "nearby_max_db": None}
    if baseline.empty:
        return result
    near = levels.loc[(abs(levels.index - diary_minute) <= tolerance) &
                      (counts == 60)]
    if near.empty:
        return result
    result["nearby_max_db"] = float(near.max())
    matches = near.loc[near >= result["reference_db"] + 10]
    if len(matches):
        result["minute"] = int(matches.index[0])
    return result
