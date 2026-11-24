""" Adaptive indoor-temperature controller for ClimateCommander (controller: shadow / adaptive), pure Python.

    No AppDaemon imports, so it also runs outside AppDaemon (in a simulation). The caller (cc_devices.py) reads the
    entities, fills an Inputs, calls IndoorController.update() once per control run (periodic, on window/vacation/power
    events and, in indoor mode, on new room and window sensor reports at most once per min_run_gap_minutes) and carries
    out the returned Decision (temperature, hvac_mode, preset, logs, save, retry_s). Runs need not be evenly spaced: the
    integrals are weighted by time, and learning and smoothing go by time, not by the number of runs.

    Design: a PI controller on the room sensor with feed-forward, a learned window-sensor baseline for the sun term
    and a learned climate-sensor offset, with the owner decisions of 2026-10-22/23. Heating:
        target_eff = target (or target_table(out_day)) + daytime increasing/saving + wind/rain shift
        R_used     = primary room sensor | backup + learned backup bias | internal - bias_int | None
        ff         = ff_bias_weight * bias_int(out_slow, windy, raining) + outdoor_gain * (target - out_slow)
        excess     = window - R_used - base_w(out_temp, windy, raining)      (learned no-sun baseline)
        solar      = min(solar_max, solar_gain * solar_f)  (rises at once, decays slowly)
        sp_ctrl    = clamp(target_eff + ff + i_term + Kp * (centre - (in_f + rate * lead)) + push - solar)
    centre = target_eff - BAND_LOW / 2: the room is steered into the band target_eff - BAND_LOW .. target_eff (heating); above
    it the setpoint steps down at once, and an HVAC never stays in fan_only / cool below the band with the windows shut.
    With a learned hold (setpoint - target_eff that held the room, by outdoor temperature and weather class) ff is
    blended toward it by n / (n + N_PRIOR); push = learned gain x deficit during a recovery episode (0 until learned).
    The power-state layer (with the comfort guard), the window/vacation overrides and the HVAC mode choice
    sit on top. Normal changes: at most one per MIN_CHANGE_S; in heat an increase is at most STEP_UP (STEP_UP_EDGE at a
    period start or end), a learned sun ahead can hold it back, a decrease goes straight to the computed value.

    Nothing here is tuned to one house: the window baseline and the internal-sensor bias are learned by
    outdoor temperature (LearnedGap), with no sign assumption; optional YAML tables only seed them.

    Units: temperatures in the device unit. Constants marked (deg) are temperature differences in C and are
    multiplied by 1.8 for F; the learned-bin width becomes 4 F.
"""
from __future__ import annotations

import bisect
import datetime
import math
import statistics
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------------- #
# Constants. [tuned] = chosen by a scripted search in a thermal simulation of rooms fitted to measured history;
# the others are owner decisions or design values.
# ---------------------------------------------------------------------------------------------------- #
VERSION = 1
MIN_CHANGE_S = 720              # owner: at most one setpoint change per 12 min
STEP = 0.5                      # the start after fan_only is this far below sp_ctrl
ROOM_STALE_S = 3600             # room and backup sensor fresh when reported within 1 h (as classic), unless
                                #    room_sensor_stale_minutes / backup_sensor_stale_minutes say otherwise
WINDOW_STALE_S = 12 * 3600      # window sensor: the live ones report only on a ~1 C change, up to 14 h apart
                                #    (window_sensor_stale_minutes replaces this)
EST_TAU_S = 3600                # smoothing of the internal-sensor estimate (climate sensors report in 0.5-1 steps)
MODE_MIN_S = 1800               # HVAC: minimum time in heat / fan_only / cool before an automatic switch
FAN_ABOVE = 0.6                 # (deg) heat -> fan_only when the room is this far above target_eff ...
FAN_RISE = 0.2                  # (deg per h) ... and the setpoint is at its lower clamp or the room rises this fast
                                #    (has_fireplace: at FAN_ABOVE alone)
MAX_DROP = 3.0                  # (deg) HVAC heat: lowest setpoint this far below target when max_setpoint_drop is not set (as
                                #    classic): the unit idles below its own sensor reading, lower only slows the way back
COLD_START_DEFICIT = 2.0        # (deg) vacation end with the room at least this far below target_eff (cold_start_deficit):
                                #    cold start ...
COLD_START_MAX = 6.0            # (deg) owner: ... at target_eff + the deficit, at most this above it (and max_temp), sent at
                                #    once and held ...
COLD_BACK_WITHIN = 1.0          # (deg) owner: ... until the room is this close to target_eff ...
COLD_START_MAX_H = 8            # owner: ... or this many hours (safety); then one move to sp_ctrl, the integral set to it
POWER_END_HOLD_S = 3600         # no automatic heat -> fan_only this long after a power state ended (the room was
                                #    moved on purpose by spend; without this a small room went to fan_only right after spend)
DB_ABOVE = 0.1                  # (deg) deadband (cooling, table trim): integrate when the room is above target_eff by more than
DB_BELOW = 0.25                 # (deg) ... this or below it by more than this. Heating uses the band, BAND_LOW / BAND_HYST
I_CLAMP = 6.0                   # (deg) integral bound (floor thermostats read 5+ above the room)
SP_SPAN = 8.0                   # (deg) setpoint within target +- this (as classic)
RATE_MAX = 2.0                  # (deg per h) trend clamp
RATE_MIN_POINTS = 3             # trend needs this many room reports in its window (else 0)
RATE_WIDE_GAPS = 9              # rate_wide (diagnostics) and rise (control): window = RATE_WIDE_FACTOR x the median of this many
RATE_WIDE_FACTOR = 3            #    gaps between primary room reports, at least the normal window ...
RATE_WIDE_MAX_S = 4 * 3600      #    ... at most this ...
RATE_WIDE_KEEP = 300            #    ... and at most this many reports kept (4 h of runs at the shortest min_run_gap_minutes, with room)
RISE_SPAN_S = 1800              # owner: 'rising' is judged over reports spanning at least 30 min (least squares), never between
                                #    two reports: rise (integral, recovery push) and the heat -> fan_only shortcut
RISE_FREEZE = 0.1               # (deg per h) [tuned] the integral does not grow while rise is at least this (None: not rising)
FAN_RISE_WINDOW_S = 1860        # owner: heat -> fan_only shortcut on the slope of the reports of the last 30 min (+1 min for
                                #    report-time jitter); with longer report gaps RATE_WIDE_FACTOR x their median, ...
FAN_RISE_MAX_S = 3660           #    ... at most this (a sensor reporting less often than every 20 min stays unknown, as before);
FAN_RISE_SPAN_S = 1200          #    at least RATE_MIN_POINTS + 1 reports spanning this, and the slope also without the newest
                                #    one. A wider window (rate_wide: 52 min on a 10 min sensor) still read a slow 0.3 C/h
                                #    creep as 'rising fast'; one report jumping 1.0 after flat ones gave 1.8 C/h
WINDOW_CLOSED_S = 3600          # integral and learning wait this long after the last window close
WINDY_S = 1800                  # wind flag: wind >= anemometer_speed within 30 min (as classic)
FAN_WIND_S = 2700               # no heat -> fan_only within 45 min of wind (as classic)
BOOST_WIND_S = 1500             # boost preset within 25 min of wind ...
BOOST_BELOW = 0.7               # (deg) ... and the room this far below target (as classic)
SHIFT_DAY = 0.5                 # (deg) daytime saving target shift (as classic)
SHIFT_INCREASE = 0.3            # (deg) owner: daytime increasing target shift (0.5 held the room too warm afterwards)
HOLD_SUN = 1.0                  # (deg) the hold after an increase ends early when the sun term is this large ...
HOLD_ROOM = 1.0                 # (deg) ... or the room is this far above the fan_only line (a lit fireplace)
SHIFT_WEATHER = 0.3             # (deg) wind / rain target shift (as classic)
SAVE_OFFSET = -0.3              # save/reduce offset when save_temp_offset is not set (as classic, not unit-scaled)
COMFORT_GUARD = 1.5             # (deg) owner: power-state offset dropped when the room is this far on the wrong side
SOLAR_MAX = 3.0                 # (deg) cap of the sun term (as the table cap)
WINDOW_OFFSET = -3.0            # (deg) fallback prior of the window baseline when window_offset is not set
TABLE_ADJ_MAX = 3.0             # (deg) table mode: sun + room-above-target correction cap (as classic)
TRIM_CLAMP = 2.0                # (deg) table mode: trim integral bound
DAY_TAU_S = 24 * 3600           # out_day filter (target_table)
TARGET_TABLE_HOLD_S = 24 * 3600 # target_table entry changes at most once a day
SAVE_EVERY_S = 3600             # persist at most once an hour (and on power-state changes)
BACKUP_ALPHA = 0.1              # EWMA of primary minus backup while both are fresh, this much per SAMPLE_S (by time)
# learned relations (LearnedGap)
GAP_BW = 2.0                    # (deg) bin width (4 F)
N_USE = 5                       # smallest bin used
N_VALID = 30                    # outlier rejection starts here
N_PRIOR_WR = 10                 # prior weight of the wind / rain terms
N_FORGET = 300                  # floor of the learning rate: 1 / N_FORGET
REJECT = 5.0                    # (deg) outlier limit
SAMPLE_S = 720                  # learning samples and sun-trend points: at most one per this. The counts above (N_USE ...
                                #    N_FORGET, N_PRIOR) and BACKUP_ALPHA were tuned with one run, and one sample, per 12 min.
                                #    With a longer interval_s a sample weighs the time since the last one in SAMPLE_S (at most
                                #    interval_s / SAMPLE_S), so learning goes by time; extra runs on sensor reports learn no faster
SAMPLE_MIN_S = 600              # interval_s below SAMPLE_S: at most one sample per this or interval_s, the longer, weight 1
QUIET_LUX = 500                 # window baseline learns only below this lux ...
SUN_LUX = 2000                  # ... and SUN_COOL_S after lux was last at or above this
SUN_COOL_S = 3600
SETTLED_RATE = 0.3              # (deg per h) internal bias learns only while the room moves less than this ...
SETTLED_SP_S = dict(hvac = 3600, heater = 7200)   # ... and the setpoint is this old
SOLAR_DERIV_S = 3600            # window of the excess derivative
# [tuned] per device kind (seeded random search + coordinate refinement in the thermal simulation; hvac tuned on a
# living room with a heat pump, heater on a floor heater in indoor and in table mode). Rounded.
KP = dict(hvac = 1.01, heater = 1.5)   # per deg
KI_H = dict(hvac = 1.64, heater = 1.73)   # per hour
TREND_LEAD_H = dict(hvac = 0.0, heater = 0.08)   # h: P acts on in_f + rate * lead
TAU_IN_S = dict(hvac = 710, heater = 900)   # s: room reading filter
RATE_WINDOW_S = dict(hvac = 3140, heater = 6900)   # s: trend least squares window
WALL_TAU_S = dict(hvac = 29400, heater = 33300)   # s: out_slow, outdoor through the wall
SOLAR_LEAD_H = dict(hvac = 0.34, heater = 1.02)   # h: weight of the rising excess
SOLAR_DECAY_S = dict(hvac = 8240, heater = 8510)   # s: sun term decay
SUN_DEADBAND = dict(hvac = 0.64, heater = 1.06)   # (deg) excess below this is not sun
FF_BIAS_WEIGHT = dict(hvac = 0.5, heater = 0.0)   # feed-forward = this * bias_int(out_slow) (0 = off)
N_PRIOR = dict(hvac = 30, heater = 10)   # prior weight of a learned bin, in samples
I_BAND = dict(hvac = 1.37, heater = 3.87)   # (deg) integral only while |target_eff - in_f| <= this
I_PRED = dict(hvac = 0, heater = 0)   # 1: integral on the predicted error (classic hold), 0: on the present error
SEND_HYST = dict(hvac = 0.5, heater = 0.54)   # (deg) normal change only when |sp_ctrl - setpoint| >= this
SUN_FREEZE = dict(hvac = 1.0, heater = 0.0)   # (deg) heating integral frozen while the sun term >= this (0 = off)
# Step rule, learned hold and recovery push, sun anticipation (owner decisions of 2026-11-22). Heating only.
STEP_UP = 0.5                   # (deg) owner: a normal increase in heat is at most this per change (decreases are not capped) ...
STEP_UP_EDGE = 1.0              # (deg) owner: ... or this in one change when a daytime_savings / daytime_increasing period
EDGE_S = MIN_CHANGE_S + 60      #    starts or ends (the first increase within this after the edge)
HOLD_SETTLE_S = 5400            # hold sample: the room in the band (BAND_LOW) for 1.5 h, the setpoint (also one set by hand)
                                #    and the mode unchanged as long, ...
HOLD_STEADY = 0.1               # (deg per h) ... and rise (when known) at most this: the setpoint holds the room it sees
EP_START = 0.5                  # (deg) recovery episode: starts on a target_eff rise with the room below the band (BAND_LOW),
EP_START_S = 1800               #    or with the room this far below for this long; it ends in the band, ...
EP_MAX_S = 6 * 3600             # ... or after this (counts as too slow)
EP_FAST = 0.6                   # an episode faster than this x recovery_hours lowers the push gain
EP_SUN_SHARE = 1 / 3            # not learned when the sun term was at least HOLD_SUN for more than this share of the episode
G_MAX = 3.0                     # push gain range 0 .. G_MAX (setpoint degrees per degree of deficit) ...
G_STEP = 1.0                    # [tuned] ... it moves at most max(G_STEP, G_STEP_REL x gain) per episode (0.3 needed about
G_STEP_REL = 0.25               #    8 mornings to reach the gain of 2 that a simulated living room needed, 1.0 about 3)
N_PRIOR_G = 1                   # the push gain is shrunk toward 0 by n / (n + this), n = episodes
SUN_BW = 2.0                    # (angle degrees) learned sun term by sun elevation, one table before and one after noon (azimuth 180)
ANTIC_LEAD_H = 1.0              # h: anticipation and forecast lead, at most recovery_hours; heaters x SOLAR_LEAD_H heater / hvac
ANTIC_CHECK = 0.5               # anticipation stops when the sun term after the lead is below this share of what was expected ...
ANTIC_OFF_S = 3 * 3600          # ... for this long
FC_WEIGHT = 0.5                 # share of the forecast outdoor change (lead vs now) added to the hold / push lookup temperature
RECOVERY_H = 2.0                # recovery_hours when not set
# Safety, steering band and start value (owner decisions of 2026-11-24). Heating unless noted.
BAND_LOW = 0.3                  # (deg) owner: the room is steered into target_eff - BAND_LOW .. target_eff, and an HVAC never stays in
                                #    fan_only (or cool) with the room this far below target_eff while the windows are shut
BAND_HYST = 0.05                # (deg) above target_eff by more than this: the integral runs down and the setpoint steps down quickly
                                #    (the filtered reading moves in 0.01 steps; 0.05 keeps one noisy report from doing it)
QUICK_MODE_S = 600              # owner: fan_only / cool -> heat at the band floor after at least this in the mode (not MODE_MIN_S)
WATCHDOG_S = 1800               # owner: fan_only / cool this long with the room below the band floor: heat is forced (WARNING log)
SUN_FALL = 0.2                  # (deg per h) the sun term does not rise while the room falls at least this fast (rise): a cold
                                #    window sensor over a falling room reads as 'sun' (window - room grows)
SP_AVG_TAU_S = 24 * 3600        # owner: start value after savings = average setpoint of normal heating hours (forgetting 24 h) ...
SP_AVG_MIN_S = 3 * 3600         # ... known once this much time is averaged ...
SP_AVG_ABOVE = 0.5              # (deg) ... and sent as average + this at the end of savings while no hold is learned
COND_CLASS = dict(sunny = 'clear', **{'clear-night': 'clear'}, cloudy = 'cloudy', partlycloudy = 'cloudy', fog = 'cloudy',
                  rainy = 'rain', pouring = 'rain', lightning = 'rain', **{'lightning-rainy': 'rain'},
                  snowy = 'snow', **{'snowy-rainy': 'snow'})   # weather entity states -> learned classes (others: none)


def round_half(value: float) -> float:
    """ Climate entities take 0.5 steps (same rounding as cc_devices.round_half). """
    return round(value * 2, 0) / 2


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def table_entry(table, x):
    """ The entry that applies at x: the last one with out <= x; below the first entry, the first one
        (bisect_right, the same rule as 'temperatures'). None without a table or x. """
    if not table or x is None:
        return None
    outs = [e['out'] for e in table]
    return table[max(0, bisect.bisect_right(outs, x) - 1)]


def ls_slope_h(points, now, window_s, min_span_s, min_points = 2):
    """ Least-squares slope per hour of (time, value) points within window_s before now, or None. """
    pts = [(t, v) for t, v in points if (now - t).total_seconds() <= window_s]
    if len(pts) < max(2, min_points):
        return None
    xs = [(t - pts[0][0]).total_seconds() / 3600 for t, _ in pts]
    if (xs[-1] - xs[0]) * 3600 < min_span_s:
        return None
    mx = sum(xs) / len(xs)
    my = sum(v for _, v in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (v - my) for x, (_, v) in zip(xs, pts)) / sxx


def _count_out(n):
    """ A learned count for the JSON: whole counts as int (as before weighted samples), else rounded. """
    return n if isinstance(n, int) else round(n, 3)


def _count_in(n):
    """ A learned count from the JSON: int when whole, else float (weighted samples). """
    n = float(n)
    if not math.isfinite(n):
        raise ValueError(n)
    return int(n) if n.is_integer() else n


class LearnedGap:
    """ y as a function of x (an outdoor temperature): one bin per bw degrees (index floor(x / bw)), linear
        interpolation between the nearest bins with at least N_USE samples on each side of x (one side only:
        flat), shrinkage toward a prior (the optional YAML table entry for x, else a fallback value), and two
        additive terms learned from windy and from rainy samples. No sign assumption and no clamp: a window
        sensor in airflow or above a heater has a positive no-sun baseline.
        Used for the window baseline (window - room by out_temp) and the internal-sensor bias (climate
        current_temperature - room by out_slow). """

    def __init__(self, bw = GAP_BW, table = None, fallback = None, n_prior = 30, scale = 1.0):
        self.bw = bw
        self.table = sorted(table, key = lambda e: e['out']) if table else None
        self.fallback = fallback
        self.n_prior = n_prior if (self.table or fallback is not None) else 0
        self.reject = REJECT * scale
        self.bins = dict()          # bin index -> [mean, n]
        self.wind = [0.0, 0]        # residual over the dry value, windy samples
        self.rain = [0.0, 0]        # residual over the dry value, rainy (not windy) samples

    def prior(self, x):
        e = table_entry(self.table, x)
        return float(e['offset']) if e is not None else self.fallback

    def learned(self, x):
        """ (interpolated learned value at x, count of the nearest bin used), or (None, 0). """
        left = right = None
        for k, (m, n) in self.bins.items():
            if n < N_USE:
                continue
            c = (k + 0.5) * self.bw
            if c <= x:
                if left is None or c > left[0]:
                    left = (c, m, n)
            elif right is None or c < right[0]:
                right = (c, m, n)
        if left and right:
            (c0, m0, n0), (c1, m1, n1) = left, right
            return m0 + (x - c0) / (c1 - c0) * (m1 - m0), (n0 if x - c0 <= c1 - x else n1)
        one = left or right
        return (one[1], one[2]) if one else (None, 0)

    def dry(self, x):
        """ Value without wind and rain: learned and prior mixed by count, (n*L + N_prior*P) / (n + N_prior). """
        if x is None:
            return None
        L, n = self.learned(x)
        P = self.prior(x)
        if L is None:
            return P
        if P is None or self.n_prior <= 0:
            return L
        return (n * L + self.n_prior * P) / (n + self.n_prior)

    def term(self, which, x):
        t = self.wind if which == 'wind' else self.rain
        e = table_entry(self.table, x)
        prior = float(e.get(which) or 0.0) if e is not None else 0.0
        return (t[1] * t[0] + N_PRIOR_WR * prior) / (t[1] + N_PRIOR_WR)

    def value(self, x, windy = False, raining = False):
        d = self.dry(x)
        if d is None:
            return None
        if windy:
            return d + self.term('wind', x)
        if raining:
            return d + self.term('rain', x)
        return d

    def usable(self, x) -> bool:
        return self.dry(x) is not None

    def sample(self, x, y, windy = False, raining = False, w = 1) -> bool:
        """ Learns one sample of weight w (counts as w samples); wind wins over rain. Returns False when rejected
            (outlier, or a wind/rain sample without a dry value to compare with). """
        if x is None or y is None:
            return False
        if windy or raining:
            d = self.dry(x)
            if d is None:
                return False
            which = 'wind' if windy else 'rain'
            t = self.wind if windy else self.rain
            v, est = y - d, self.term(which, x)
        else:
            t = self.bins.setdefault(int(math.floor(x / self.bw)), [0.0, 0])
            v, est = y, t[0]
        if t[1] >= N_VALID and abs(v - est) > self.reject:
            return False
        a = max(w / (t[1] + w), min(1.0, w / N_FORGET))
        t[0] += a * (v - t[0])
        t[1] += w
        return True

    def to_dict(self) -> dict:
        return dict(bw = self.bw, bins = dict((str(k), [round(m, 4), _count_out(n)]) for k, (m, n) in sorted(self.bins.items())),
                    wind = [round(self.wind[0], 4), _count_out(self.wind[1])], rain = [round(self.rain[0], 4), _count_out(self.rain[1])])

    def load(self, d) -> bool:
        """ Restores to_dict() output. False (nothing loaded) when the bin width differs or the data is bad. """
        try:
            if float(d['bw']) != self.bw:
                return False
            bins = dict((int(k), [float(m), _count_in(n)]) for k, (m, n) in d['bins'].items())
            wind = [float(d['wind'][0]), _count_in(d['wind'][1])]
            rain = [float(d['rain'][0]), _count_in(d['rain'][1])]
        except (KeyError, TypeError, ValueError, IndexError):
            return False
        self.bins, self.wind, self.rain = bins, wind, rain
        return True

    def learned_n(self, x):
        """ learned(x) with the count interpolated as well, so a weight n / (n + prior) does not jump between bins, and
            nothing further than one bin beyond the outermost learned bin (a hold learned at -4 is not a guess for +10). """
        return _interp([((k + 0.5) * self.bw, m, n) for k, (m, n) in self.bins.items() if n >= N_USE], x, self.bw)


def _interp(points, x, reach):
    """ (value, count) at x from (centre, value, count) points: linear between the nearest point on each side, flat
        up to reach beyond the outermost one; (None, 0) without points or further out. """
    if x is None:
        return None, 0
    left = max((p for p in points if p[0] <= x), default = None)
    right = min((p for p in points if p[0] > x), default = None)
    if left and right:
        f = (x - left[0]) / (right[0] - left[0])
        return left[1] + f * (right[1] - left[1]), left[2] + f * (right[2] - left[2])
    one = left or right
    return (one[1], one[2]) if one and abs(x - one[0]) <= reach else (None, 0)


def cond_class(condition):
    """ The learned weather class of a weather entity state ('clear', 'cloudy', 'rain', 'snow'), None for others. """
    return COND_CLASS.get(condition) if isinstance(condition, str) else None


class CondTerms:
    """ Additive terms per weather class on top of a learned value: the mean residual of the samples in that class,
        shrunk toward 0 by n / (n + N_PRIOR_WR) (as the wind / rain terms), so a rare class changes little. """

    def __init__(self, scale = 1.0):
        self.reject = REJECT * scale
        self.terms = dict()         # class -> [mean residual, n]

    def value(self, cls):
        t = self.terms.get(cls)
        return 0.0 if t is None else t[1] * t[0] / (t[1] + N_PRIOR_WR)

    def sample(self, cls, residual, w = 1):
        if cls is None or residual is None:
            return False
        t = self.terms.setdefault(cls, [0.0, 0])
        if t[1] >= N_VALID and abs(residual - t[0]) > self.reject:
            return False
        a = max(w / (t[1] + w), min(1.0, w / N_FORGET))
        t[0] += a * (residual - t[0])
        t[1] += w
        return True

    def to_dict(self) -> dict:
        return dict((k, [round(m, 4), _count_out(n)]) for k, (m, n) in sorted(self.terms.items()))

    def load(self, d) -> bool:
        try:
            terms = dict((str(k), [float(m), _count_in(n)]) for k, (m, n) in d.items() if k in COND_CLASS.values())
        except (AttributeError, TypeError, ValueError):
            return False
        self.terms = terms
        return True


class PushGain:
    """ The recovery push gain by the outdoor lookup temperature: one bin per bw degrees with the gain and its number of
        episodes, linear between bins (flat for one bin beyond the outermost), shrunk toward 0 by n / (n + N_PRIOR_G),
        within 0 .. G_MAX, plus a CondTerms add-on. Unlearned: 0, no push. """

    def __init__(self, bw = GAP_BW):
        self.bw = bw
        self.bins = dict()          # bin index -> [gain, episodes]
        self.cond = CondTerms(1.0)  # the gain has no unit

    def raw(self, x):
        return _interp([((k + 0.5) * self.bw, g, n) for k, (g, n) in self.bins.items() if n >= 1], x, self.bw)

    def value(self, x, cls = None):
        g, n = self.raw(x)
        if g is None:
            return 0.0
        return clamp(n / (n + N_PRIOR_G) * (g + self.cond.value(cls)), 0.0, G_MAX)

    def episodes(self, x) -> float:
        """ Episodes learned in the bin of x (the diagnostic 'learning' while fewer than N_USE). """
        t = self.bins.get(int(math.floor(x / self.bw))) if x is not None else None
        return t[1] if t else 0

    def seen(self, x):
        """ One episode on time in the bin of x: counted, the gain unchanged (a new bin takes the interpolated gain). """
        k = int(math.floor(x / self.bw))
        g, n = self.bins[k] if k in self.bins else ((self.raw(x)[0] or 0.0), 0)
        self.bins[k] = [g, n + 1]

    def learn(self, x, needed, cls = None):
        """ One episode: the bin of x moves toward the gain the episode needed, by at most max(G_STEP, G_STEP_REL x gain). """
        k = int(math.floor(x / self.bw))
        if k in self.bins:
            g, n = self.bins[k]
        else:
            g, n = (self.raw(x)[0] or 0.0), 0
        lim = max(G_STEP, G_STEP_REL * g)
        new = clamp(g + clamp(needed - g, -lim, lim), 0.0, G_MAX)
        self.bins[k] = [new, n + 1]
        self.cond.sample(cls, clamp(needed, 0.0, G_MAX) - g)

    def to_dict(self) -> dict:
        return dict(bw = self.bw, bins = dict((str(k), [round(g, 4), _count_out(n)]) for k, (g, n) in sorted(self.bins.items())),
                    cond = self.cond.to_dict())

    def load(self, d) -> bool:
        try:
            if float(d['bw']) != self.bw:
                return False
            bins = dict((int(k), [clamp(float(g), 0.0, G_MAX), _count_in(n)]) for k, (g, n) in d['bins'].items())
            cond = CondTerms(1.0)
            if not cond.load(d.get('cond') or dict()):
                return False
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            return False
        self.bins, self.cond = bins, cond
        return True


@dataclass
class Tuning:
    """ The [tuned] constants for one device kind. The app uses Tuning.default(kind); a tuning search passes others. """
    kp: float
    ki_h: float
    lead_h: float
    tau_in_s: float = 0.0
    rate_window_s: float = 0.0
    wall_tau_s: float = 0.0
    solar_lead_h: float = 0.0
    solar_decay_s: float = 0.0
    sun_deadband: float = 0.0
    ff_bias_weight: float = 0.0
    n_prior: float = 0.0
    i_band: float = 0.0
    i_pred: float = 0.0
    send_hyst: float = 0.0
    sun_freeze: float = 0.0

    @classmethod
    def default(cls, kind):
        return cls(*(c[kind] for c in (KP, KI_H, TREND_LEAD_H, TAU_IN_S, RATE_WINDOW_S, WALL_TAU_S, SOLAR_LEAD_H, SOLAR_DECAY_S, SUN_DEADBAND, FF_BIAS_WEIGHT, N_PRIOR, I_BAND, I_PRED, SEND_HYST, SUN_FREEZE)))


@dataclass
class ControlConfig:
    """ Per device, from the app YAML (cc_config DeviceConfig). """
    kind: str = 'hvac'                    # 'hvac' (heat / cool / fan_only) or 'heater'
    target_mode: str = 'indoor'           # 'indoor', or 'table' (heaters only)
    unit: str = 'C'
    min_temp: float = 5.0
    max_temp: float = 35.0
    vacation_temp: float | None = None
    max_vacation_temp: float = 30.0
    save_temp_offset: float | None = None
    save_temp: float | None = None
    spend_temp_offset: float = 1.0
    window_offset: float | None = None    # prior of the window baseline (None: -3 C / -5.4 F)
    window_offset_table: list | None = None
    internal_offset_table: list | None = None
    target_table: list | None = None
    outdoor_gain: float = 0.0
    solar_gain: float = 1.0
    solar_max: float | None = None        # None: 3 C / 5.4 F
    comfort_guard: float | None = None    # None: 1.5 C / 2.7 F
    temperatures: list | None = None      # table mode, as classic
    target_heater_temp: float | None = None
    has_fireplace: bool = False           # HVAC: heat -> fan_only at FAN_ABOVE without waiting for the lower clamp or a rise
    max_setpoint_drop: float | None = None    # HVAC heat: lowest setpoint below target, device unit (None: 3 C / 5.4 F)
    interval_s: float = 720.0             # seconds between the periodic runs (interval_minutes)
    cold_start_deficit: float | None = None   # vacation end: cold start when the room is this far below target (None: 2 C / 3.6 F)
    room_stale_s: float | None = None     # room_sensor_stale_minutes in seconds (None: ROOM_STALE_S)
    backup_stale_s: float | None = None   # backup_sensor_stale_minutes in seconds (None: ROOM_STALE_S)
    window_stale_s: float | None = None   # window_sensor_stale_minutes in seconds (None: WINDOW_STALE_S)
    room_tau_s: float | None = None       # room_smoothing_minutes in seconds: the room reading filter (None: TAU_IN_S)
    recovery_h: float = RECOVERY_H        # recovery_hours: the push gain aims at the target within this after a deficit starts
    sun_anticipation: bool = True         # sun_anticipation: a learned sun ahead holds an increase back
    drop_hold_s: float = 7200.0           # increase_hold_hours in seconds: no heat -> fan_only this long after a savings start


@dataclass
class Inputs:
    """ What the device sees at one control run. Ages are seconds since last_reported (None = unknown, fresh). """
    now: datetime.datetime
    target: float                         # target_indoor_temp, or target_indoor_input when set
    setpoint: float | None = None         # climate 'temperature'
    hvac_mode: str | None = 'heat'        # climate state
    room: float | None = None             # primary indoor sensor
    room_age_s: float | None = None
    backup: float | None = None
    backup_age_s: float | None = None
    window: float | None = None           # window_sensor_temp
    window_age_s: float | None = None
    internal: float | None = None         # climate current_temperature
    out_temp: float | None = None
    lux: float | None = None
    daytime: bool | None = None           # classic 'sunrise'..'sunset - 00:30'; sun flag when lux is None
    since_windy_s: float | None = None    # seconds since wind >= anemometer_speed
    wind_now: bool = False                # table mode: wind >= anemometer_speed right now
    raining: bool = False                 # rain >= rain_level
    rain: float = 0.0                     # rain amount (as classic: cool -> fan_only at night when > 0)
    windows_open: bool = False
    since_window_closed_s: float | None = None
    vacation: bool = False
    power_state: str = 'normal'
    increasing: bool = False              # daytime_increasing active
    after_increase: bool = False          # within increase_hold_hours (default 2 h) after a daytime_increasing period
    saving: bool = False                  # daytime_savings active
    preset: str | None = None
    boost_available: bool = False         # 'boost' in preset_modes and fan_mode is not 'Silence'
    condition: str | None = None          # weather entity state from the WEATHER_CHANGE event (None: unknown)
    forecast: list = field(default_factory = list)    # [{'time': aware datetime, 'temp', 'condition', ...}] by time
    sun_elev: float | None = None         # sun elevation / azimuth in degrees now (None: unknown) ...
    sun_az: float | None = None
    sun_lead: tuple | None = None         # ... and (elevation, azimuth) at now + lead_s()


@dataclass
class Decision:
    temperature: float | None = None      # send climate/set_temperature
    hvac_mode: str | None = None          # send climate/set_hvac_mode (before the temperature)
    preset: str | None = None             # send climate/set_preset_mode
    save: bool = False                    # persist snapshot() now
    retry_s: float | None = None          # a normal change waits for MIN_CHANGE_S: run again in this many seconds
    logs: list = field(default_factory = list)    # (level, text)
    diag: dict = field(default_factory = dict)    # terms, for shadow logging and the diagnostic sensor


def _iso(t):
    return t.isoformat() if t is not None else None


def _dt(s):
    try:
        return datetime.datetime.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


class IndoorController:
    """ One climate entity. update() once per control run; restore()/snapshot() for the JSON 'adaptive' key. """

    PERSIST = ('out_slow', 'out_day', 'in_f', 'i_term', 'i_cool', 'trim_i', 'backup_bias', 'solar_f',
               'pw_state', 'pw_base', 'pw_tripped', 'last_commanded', 'table_idx')
    PERSIST_ADDED = ('sp_avg', 'sp_avg_sum', 'sp_avg_s')   # added keys: an older file has none, they start empty
    PERSIST_T = ('last_sun', 'last_change', 'table_idx_t', 'mode_since', 'sp_since', 'drop_hold_until')

    def __init__(self, cfg: ControlConfig, tuning: Tuning | None = None):
        self.cfg = cfg
        self.s = 1.8 if cfg.unit == 'F' else 1.0
        self.t = tuning or Tuning.default(cfg.kind)
        s = self.s
        bw = 4.0 if cfg.unit == 'F' else GAP_BW
        wo = cfg.window_offset if cfg.window_offset is not None else WINDOW_OFFSET * s
        self.base_w = LearnedGap(bw, cfg.window_offset_table, wo, self.t.n_prior, s)
        self.bias = LearnedGap(bw, cfg.internal_offset_table, None, self.t.n_prior, s)
        self.guard = cfg.comfort_guard if cfg.comfort_guard is not None else COMFORT_GUARD * s
        self.solar_max = cfg.solar_max if cfg.solar_max is not None else SOLAR_MAX * s
        self.room_stale = cfg.room_stale_s if cfg.room_stale_s is not None else ROOM_STALE_S
        self.backup_stale = cfg.backup_stale_s if cfg.backup_stale_s is not None else ROOM_STALE_S
        self.window_stale = cfg.window_stale_s if cfg.window_stale_s is not None else WINDOW_STALE_S
        self.tau_in = cfg.room_tau_s if cfg.room_tau_s is not None else self.t.tau_in_s
        # learned hold setpoint (setpoint - target_eff by out_slow), recovery push gain, sun term by sun position
        self.hold = LearnedGap(bw, None, None, self.t.n_prior, s)
        self.hold_cond = CondTerms(s)
        self.push_g = PushGain(bw)
        self.sun_gain = dict(am = LearnedGap(SUN_BW, None, None, self.t.n_prior, s), pm = LearnedGap(SUN_BW, None, None, self.t.n_prior, s))
        self.sun_cond = CondTerms(s)
        self.last_hold_sample = self.hold_off = None
        self.last_sun_sample = self.sun_off = None
        self.band_since = None            # memory only: since when the room is in the band of an unchanged target_eff ...
        self.band_tgt = None              # ... which one
        self.ep = None                    # memory only: the recovery episode in progress (a restart discards it)
        self.below_since = None           # memory only: since when the room is EP_START below target_eff
        self.tgt_prev = None              # target_eff of the last heat run (memory only)
        self.flags_prev = None            # (saving, increasing) of the last run (memory only) ...
        self.edge_t = None                # ... the last change of them: a larger first step up (STEP_UP_EDGE)
        self.edge_used = False
        self.restore_up = False           # memory only: the first change after a window / vacation override is not capped
        self.antic_hist = []              # memory only: (time, expected sun term at time + lead) to check one lead later ...
        self.antic_off = None             # ... and anticipation off until this after a sun that did not come
        self.sp_avg = None                # average setpoint of normal heating hours (persisted) ...
        self.sp_avg_sum = self.sp_avg_s = 0.0   # ... setpoint x seconds and seconds until SP_AVG_MIN_S are averaged
        self.sp_avg_t = None              # memory only: the last run that counted for it
        self.mode_cmd = None              # memory only: (mode, when, mode_since before) of the last mode command ...
        self.below_line_since = None      # ... since when an HVAC in fan_only / cool has the room below the band floor ...
        self.watch_armed = True           # ... and the watchdog may log (again once the room is back in the band)
        self.quick_rep = None             # memory only: the room report the last quick step down was made on
        self.mode_lost = False            # per run: the last mode command was not taken by the unit
        self.sun_falling = False          # per run: the room falls too fast for the sun term to rise (and base_w to learn)
        self.sun_gate = None              # per run: why the sun term may not rise ('down': no sun, 'falling'), None: it may
        self.drop_hold_until = None       # (persisted) no heat -> fan_only until then: the target dropped at a period start
        self.saving_end_t = None          # memory only: the run at which daytime_savings last ended
        self.rise = self.rise_fan = None  # per run: the room trends judged over RISE_SPAN_S / the last 30 min
        self.cls = None                   # per run: the weather class now
        self.key = None                   # per run: the hold / push lookup temperature
        # signal state
        self.out_slow = self.out_day = None
        self.in_f = None
        self.hist = []                    # (report time, value) of real room readings
        self.wide_hist = []               # the same reports kept for rate_wide; hist drops them after the normal window
        self.wide_gaps = []               # seconds between the last primary reports, for the rate_wide window
        self.wide_prim_t = None           # time of the last primary report
        self.last_rep_t = None
        self.src_prev = None
        self.backup_bias = None
        self.solar_f = 0.0
        self.exc_hist = []
        self.window_prev = None           # the window reading of the last run (memory only)
        self.last_sun = None
        # controller state
        self.i_term = 0.0
        self.i_cool = 0.0
        self.trim_i = 0.0
        self.ff_prev = None
        self.ff_ok_prev = None
        self.ff_jump = 0.0                # the change of the feed-forward the integral took over this run
        self.grow_blocked = False         # the last _pi held the integral from growing
        self.pw_state = 'normal'
        self.pw_base = None
        self.pw_tripped = False
        self.pw_ended = None              # when the last power state ended
        self.last_commanded = None
        self.last_change = None
        self.mode_seen = None
        self.mode_since = None
        self.mode_forced = False          # the current mode was set by an override (window / vacation)
        self.sp_seen = None
        self.sp_since = None
        self.table_idx = None
        self.table_idx_t = None
        self.last_run = None
        self.last_gap_sample = None
        self.last_bias_sample = None
        self.gap_off = None               # the last run where the window baseline could not learn (memory only) ...
        self.bias_off = None              # ... and the internal bias
        self.last_save = None
        self.started = False              # bumpless start done
        self.vac_prev = None              # vacation flag of the last run (memory only: no cold start after a restart)
        self.vac_end = False              # this run is the first one after a vacation ended
        self.cold_start = None            # when the cold start after a vacation began (memory only)
        self.cold_sp = None               # its setpoint
        self.logged = set()               # log-once flags

    # ---- persistence ------------------------------------------------------------------------------- #

    def snapshot(self) -> dict:
        d = dict(version = VERSION, unit = self.cfg.unit, window_gap = self.base_w.to_dict(),
                 internal_bias = self.bias.to_dict(), updated = _iso(self.last_run))
        for k in self.PERSIST:
            d[k] = getattr(self, k)
        for k in self.PERSIST_T:
            d[k] = _iso(getattr(self, k))
        # added keys (the version stays 1: an older file loads, and these start empty)
        d['hold'] = dict(self.hold.to_dict(), cond = self.hold_cond.to_dict())
        d['push'] = self.push_g.to_dict()
        d['sun_gain'] = dict(am = self.sun_gain['am'].to_dict(), pm = self.sun_gain['pm'].to_dict(), cond = self.sun_cond.to_dict())
        for k in self.PERSIST_ADDED:
            d[k] = getattr(self, k)
        return d

    def restore(self, d) -> list:
        """ Loads snapshot() output. Returns log lines. A version or unit mismatch discards everything. """
        if not isinstance(d, dict) or not d:
            return []
        if d.get('version') != VERSION or d.get('unit') != self.cfg.unit:
            return [('INFO', "adaptive state from another version or unit is ignored (learned models start empty).")]
        self.base_w.load(d.get('window_gap') or dict())
        self.bias.load(d.get('internal_bias') or dict())
        for k in self.PERSIST:
            if k in d:
                setattr(self, k, d[k])
        for k in self.PERSIST_T:
            setattr(self, k, _dt(d.get(k)))
        self.pw_state = self.pw_state if self.pw_state in ('normal', 'save', 'spend', 'reduce') else 'normal'
        self.started = False
        return self._restore_added(d)

    def _restore_added(self, d) -> list:
        """ The learned hold, push gain and sun term. A missing key leaves the part empty; a key that cannot be read too,
            with one INFO line. """
        logs = []
        try:
            avg = None if d.get('sp_avg') is None else float(d['sp_avg'])
            total, secs = float(d.get('sp_avg_sum') or 0.0), float(d.get('sp_avg_s') or 0.0)
            if not all(math.isfinite(v) for v in (avg if avg is not None else 0.0, total, secs)):
                raise ValueError
            self.sp_avg, self.sp_avg_sum, self.sp_avg_s = avg, total, secs
        except (TypeError, ValueError):
            logs.append(('INFO', "adaptive state: 'sp_avg' could not be read and starts empty."))
        for key, load in (('hold', self._load_hold), ('push', self.push_g.load), ('sun_gain', self._load_sun)):
            if key not in d:
                continue
            try:
                ok = isinstance(d[key], dict) and load(d[key])
            except (AttributeError, KeyError, TypeError, ValueError, IndexError):
                ok = False
            if not ok:
                logs.append(('INFO', "adaptive state: '%s' could not be read and starts empty." % key))
        return logs

    def _load_hold(self, d) -> bool:
        hold, cond = LearnedGap(self.hold.bw, None, None, self.t.n_prior, self.s), CondTerms(self.s)
        if not (hold.load(d) and cond.load(d.get('cond') or dict())):
            return False
        self.hold, self.hold_cond = hold, cond
        return True

    def _load_sun(self, d) -> bool:
        gains, cond = dict(am = LearnedGap(SUN_BW, None, None, self.t.n_prior, self.s), pm = LearnedGap(SUN_BW, None, None, self.t.n_prior, self.s)), CondTerms(self.s)
        if not (all(isinstance(d.get(h), dict) and gains[h].load(d[h]) for h in gains) and cond.load(d.get('cond') or dict())):
            return False
        self.sun_gain, self.sun_cond = gains, cond
        return True

    # ---- signals ----------------------------------------------------------------------------------- #

    def _log_once(self, d, key, level, text):
        if key not in self.logged:
            self.logged.add(key)
            d.logs.append((level, text))

    def _track(self, inp, now, dt_s):
        cmd, self.mode_cmd = self.mode_cmd, None
        if inp.hvac_mode != self.mode_seen:
            if (cmd is not None and cmd[0] == self.mode_seen and inp.hvac_mode == cmd[3]
                    and (now - cmd[1]).total_seconds() <= MODE_MIN_S):
                # The first run after a mode command still sees the earlier mode: the unit did not take it (or not yet).
                # The time in the earlier mode goes on, so the rules send it again now and not MODE_MIN_S later.
                self.mode_since, self.mode_forced = cmd[2], cmd[4]
                self.mode_lost = True
            elif self.mode_seen is not None or self.mode_since is None:
                self.mode_since = now
            self.mode_seen = inp.hvac_mode
        if inp.setpoint != self.sp_seen:
            if self.sp_seen is not None or self.sp_since is None:
                self.sp_since = now
            self.sp_seen = inp.setpoint
        sunny = (inp.lux >= SUN_LUX) if inp.lux is not None else bool(inp.daytime)
        if sunny:
            self.last_sun = now
        if inp.out_temp is not None:
            if self.out_slow is None:
                self.out_slow = self.out_day = inp.out_temp
            else:
                self.out_slow += (1 - math.exp(-dt_s / self.t.wall_tau_s)) * (inp.out_temp - self.out_slow)
                self.out_day += (1 - math.exp(-dt_s / DAY_TAU_S)) * (inp.out_temp - self.out_day)

    def _quiet(self, inp, now):
        """ No sun on the window: lux below QUIET_LUX (or night when lux is unknown) and SUN_COOL_S after sun. """
        if inp.lux is not None:
            dark = inp.lux < QUIET_LUX
        elif inp.daytime is not None:
            dark = not inp.daytime
        else:
            return False
        return dark and (self.last_sun is None or (now - self.last_sun).total_seconds() >= SUN_COOL_S)

    def _sample_due(self, last, now):
        """ At most one learning sample or sun-trend point per SAMPLE_S (a shorter interval_s: per interval_s, at least
            SAMPLE_MIN_S), however often the device runs. """
        return last is None or (now - last).total_seconds() >= max(SAMPLE_MIN_S, min(self.cfg.interval_s, SAMPLE_S)) - 1

    def _sample_weight(self, last, off, now):
        """ The weight of a learning sample in samples of SAMPLE_S: the time since the last sample over SAMPLE_S, at least
            1 and at most interval_s / SAMPLE_S (always 1 with interval_s up to SAMPLE_S). 1 for the first sample and for the
            first after a run that could not learn (off): how long the conditions held before this run is unknown. """
        hi = self.cfg.interval_s / SAMPLE_S
        if hi <= 1 or last is None or (off is not None and off >= last):
            return 1
        return max(1, min((now - last).total_seconds() / SAMPLE_S, hi))

    def _room(self, d, inp, windy, raining, dt_s):
        """ (R_used, source): 'primary', 'backup' (+ learned bias), 'estimate' (internal - bias_int) or None. """
        def fresh(v, age, lim):
            return v is not None and (age is None or age <= lim)
        p_ok = fresh(inp.room, inp.room_age_s, self.room_stale)
        b_ok = fresh(inp.backup, inp.backup_age_s, self.backup_stale)
        if p_ok and b_ok:
            diff = inp.room - inp.backup
            if self.backup_bias is None:
                self.backup_bias = diff
            else:
                self.backup_bias += (1 - (1 - BACKUP_ALPHA) ** (dt_s / SAMPLE_S)) * (diff - self.backup_bias)
        if p_ok:
            R, src = inp.room, 'primary'
        elif b_ok:
            R, src = inp.backup + (self.backup_bias or 0.0), 'backup'
        else:
            R, src = None, None
            if inp.internal is not None and self.out_slow is not None:
                b = self.bias.value(self.out_slow, windy, raining)
                if b is not None:
                    R, src = inp.internal - b, 'estimate'
        if src == 'estimate':
            self._log_once(d, 'estimate', 'WARNING', "no fresh room sensor: using the climate's own sensor minus "
                           "the learned offset (%.1f). Integral frozen, no mode changes, no learning." % (inp.internal - R))
        elif src is None:
            self._log_once(d, 'openloop', 'WARNING', "no fresh room sensor and no usable internal-sensor offset: "
                           "open loop (feed-forward only). internal_offset_table gives an estimate.")
        elif 'estimate' in self.logged or 'openloop' in self.logged:
            self.logged.discard('estimate')
            self.logged.discard('openloop')
            d.logs.append(('INFO', "room sensor back (%s)." % src))
        return R, src

    def _indoor(self, inp, R, src, now, dt_s):
        """ in_f (filtered room) and the report history for the trend. Real readings are keyed by their report
            time, so a stale value is not a new sample and extra runs do not fake a trend. """
        if src in ('primary', 'backup'):
            age = inp.room_age_s if src == 'primary' else inp.backup_age_s
            t_rep = now - datetime.timedelta(seconds = age) if age is not None else now
            if self.src_prev not in ('primary', 'backup') or self.in_f is None or self.last_rep_t is None:
                self.in_f, self.hist, self.last_rep_t = R, [(t_rep, R)], t_rep
                self._wide_reset()
                self._wide_note(t_rep, R, src)
            elif (t_rep - self.last_rep_t).total_seconds() >= 1:
                dtr = (t_rep - self.last_rep_t).total_seconds()
                a = dtr / (self.tau_in + dtr) if self.tau_in > 0 else 1.0
                self.in_f += a * (R - self.in_f)
                self.hist.append((t_rep, R))
                self.last_rep_t = t_rep
                self._wide_note(t_rep, R, src)
            keep = max(self.t.rate_window_s, 3600) + 600
            self.hist = [(t, v) for t, v in self.hist if (now - t).total_seconds() <= keep]
        elif src == 'estimate':
            if self.src_prev != 'estimate':
                self.hist = []
                self._wide_reset()
            if self.in_f is None:
                self.in_f = R
            else:
                self.in_f += dt_s / (EST_TAU_S + dt_s) * (R - self.in_f)
        else:
            self.hist = []
            self._wide_reset()
        self.src_prev = src

    def _rate(self, now, window_s):
        r = ls_slope_h(self.hist, now, window_s, 900, RATE_MIN_POINTS)
        return 0.0 if r is None else clamp(r, -RATE_MAX * self.s, RATE_MAX * self.s)

    def _wide_reset(self):
        self.wide_hist, self.wide_gaps, self.wide_prim_t = [], [], None

    def _wide_note(self, t_rep, R, src):
        kept = [(t, v) for t, v in self.wide_hist if (t_rep - t).total_seconds() <= RATE_WIDE_MAX_S]
        self.wide_hist = kept[-(RATE_WIDE_KEEP - 1):] + [(t_rep, R)]
        if src == 'primary':
            if self.wide_prim_t is not None:
                self.wide_gaps = (self.wide_gaps + [(t_rep - self.wide_prim_t).total_seconds()])[-RATE_WIDE_GAPS:]
            self.wide_prim_t = t_rep

    def _rate_wide(self, now, min_span_s = 900):
        """ The room trend over a window that follows the sensor's report gaps. None (not 0.0, which would read as a flat
            room): too few reports in the window. rate_wide (diagnostics) with min_span_s 900; rise (control) with
            RISE_SPAN_S, so a rise is always judged over at least 30 min of reports. """
        window_s = self.t.rate_window_s
        if self.wide_gaps:
            window_s = max(window_s, RATE_WIDE_FACTOR * statistics.median(self.wide_gaps))
        r = ls_slope_h(self.wide_hist, now, min(window_s, RATE_WIDE_MAX_S), min_span_s, RATE_MIN_POINTS)
        return None if r is None else clamp(r, -RATE_MAX * self.s, RATE_MAX * self.s)

    def _rise_fan(self, now):
        """ The room trend for the heat -> fan_only shortcut: least squares over the reports of the last 30 min (longer
            for a sensor with long gaps: RATE_WIDE_FACTOR x their median), and the same without the newest report; the
            lower of the two, so one report that jumps (a sensor in the airflow) is not a rise. None with fewer than
            RATE_MIN_POINTS + 1 reports spanning FAN_RISE_SPAN_S: then the shortcut does not fire. """
        window_s = FAN_RISE_WINDOW_S
        if self.wide_gaps:
            window_s = max(window_s, RATE_WIDE_FACTOR * statistics.median(self.wide_gaps) + 60)
        pts = [(t, v) for t, v in self.wide_hist if (now - t).total_seconds() <= min(window_s, FAN_RISE_MAX_S)]
        if len(pts) < RATE_MIN_POINTS + 1:
            return None
        r_all = ls_slope_h(pts, now, RATE_WIDE_MAX_S, FAN_RISE_SPAN_S, RATE_MIN_POINTS)
        r_old = ls_slope_h(pts[:-1], now, RATE_WIDE_MAX_S, 0, RATE_MIN_POINTS)
        if r_all is None or r_old is None:
            return None
        return clamp(min(r_all, r_old), -RATE_MAX * self.s, RATE_MAX * self.s)

    def _solar(self, inp, R, now, dt_s, windy, raining):
        """ excess = window - R_used - base_w; solar_f rises at once and decays with solar_decay_s. It rises on a new
            window reading or a sample that is due, not on every run: excess holds the raw room reading, so a run on each
            room report would catch every dip of the room as sun, and the term would grow with the number of runs. """
        w_ok = inp.window is not None and (inp.window_age_s is None or inp.window_age_s <= self.window_stale)
        excess, raw = None, 0.0
        may_rise = True
        if w_ok and R is not None and inp.out_temp is not None:
            base = self.base_w.value(inp.out_temp, windy, raining)
            excess = inp.window - R - base
            self.exc_hist = [(t, v) for t, v in self.exc_hist if (now - t).total_seconds() <= SOLAR_DERIV_S]
            due = self._sample_due(self.exc_hist[-1][0] if self.exc_hist else None, now)
            if due:
                self.exc_hist.append((now, excess))
            may_rise = due or inp.window != self.window_prev
            deriv = ls_slope_h(self.exc_hist, now, SOLAR_DERIV_S, 1200) or 0.0
            raw = max(0.0, excess - self.t.sun_deadband * self.s) + self.t.solar_lead_h * max(0.0, deriv)
        else:
            self.exc_hist = []
        self.window_prev = inp.window
        # Owner: the sun term must be physical. It rises only with the sun above the horizon (sun position; without it lux
        # at QUIET_LUX or more; without either as before) and not while the room falls fast: a window sensor that stays
        # cold over a room that cools reads as 'sun' (window - room grows), and kept an HVAC in fan_only all evening.
        if inp.sun_elev is not None:
            sun_up = inp.sun_elev > 0
        elif inp.lux is not None:
            sun_up = inp.lux >= QUIET_LUX
        else:
            sun_up = True
        self.sun_falling = self.rise is not None and self.rise <= -SUN_FALL * self.s
        self.sun_gate = 'down' if not sun_up else 'falling' if self.sun_falling else None
        if not sun_up or self.sun_falling:
            raw = 0.0
        decayed = self.solar_f * math.exp(-dt_s / self.t.solar_decay_s)
        self.solar_f = max(raw, decayed) if may_rise else decayed
        return excess, w_ok

    def _learn(self, inp, now, src, w_ok, windy, raining, rate60):
        """ Window baseline and internal bias, each from the PRIMARY sensor only, in normal operation. A run where one
            of them cannot learn is noted (gap_off / bias_off): its next sample has weight 1. """
        gap_ok = bias_ok = False
        if (src == 'primary' and not inp.windows_open and not inp.vacation and inp.power_state == 'normal'
                and (inp.since_window_closed_s is None or inp.since_window_closed_s >= WINDOW_CLOSED_S)):
            gap_ok = w_ok and inp.out_temp is not None and self._quiet(inp, now) and not self.sun_falling
            settled = (rate60 is not None and abs(rate60) <= SETTLED_RATE * self.s and self.sp_since is not None
                       and (now - self.sp_since).total_seconds() >= SETTLED_SP_S[self.cfg.kind])
            bias_ok = inp.internal is not None and inp.hvac_mode == 'heat' and self.out_slow is not None and settled
        if not gap_ok:
            self.gap_off = now
        elif self._sample_due(self.last_gap_sample, now):
            self.base_w.sample(inp.out_temp, inp.window - inp.room, windy, raining,
                               self._sample_weight(self.last_gap_sample, self.gap_off, now))
            self.last_gap_sample = now
        if not bias_ok:
            self.bias_off = now
        elif self._sample_due(self.last_bias_sample, now):
            self.bias.sample(self.out_slow, inp.internal - inp.room, windy, raining,
                             self._sample_weight(self.last_bias_sample, self.bias_off, now))
            self.last_bias_sample = now

    # ---- forecast, sun position, learned hold and recovery push (owner decisions of 2026-11-22) ---------- #

    def lead_s(self) -> float:
        """ Seconds ahead for the sun anticipation and the forecast lookup: ANTIC_LEAD_H, at most recovery_hours; heaters
            react slower (floor mass), so their lead is longer by SOLAR_LEAD_H heater / hvac (3 h with the defaults). """
        return min(self.cfg.recovery_h, ANTIC_LEAD_H) * SOLAR_LEAD_H[self.cfg.kind] / SOLAR_LEAD_H['hvac'] * 3600

    @staticmethod
    def _fc_at(inp, t):
        """ The forecast entry whose time slot holds t: from its time to the next entry (at most 3 h), the last one
            1 h. None without one, so an old or missing forecast is never used. """
        fc = [e for e in (inp.forecast or []) if isinstance(e, dict) and isinstance(e.get('time'), datetime.datetime)]
        for i, e in enumerate(fc):
            end = fc[i + 1]['time'] if i + 1 < len(fc) else e['time'] + datetime.timedelta(hours = 1)
            end = min(end, e['time'] + datetime.timedelta(hours = 3))
            try:
                if e['time'] <= t < end:
                    return e
            except TypeError:    # a naive time: not comparable with an aware now
                return None
        return None

    def _lookup_key(self, inp, now):
        """ The outdoor temperature for the hold and push tables: out_slow, plus FC_WEIGHT of the forecast change from
            now to now + lead (a front ahead counts early). Without a forecast that covers the lead: out_slow. """
        if self.out_slow is None:
            return None
        e = self._fc_at(inp, now + datetime.timedelta(seconds = self.lead_s()))
        temp = e.get('temp') if e is not None else None
        if not isinstance(temp, (int, float)) or isinstance(temp, bool) or not math.isfinite(temp) or inp.out_temp is None:
            return self.out_slow
        return self.out_slow + FC_WEIGHT * (temp - inp.out_temp)

    def _sun_learn(self, inp, now, w_ok):
        """ The sun term (solar_f, before solar_gain) by sun elevation, before / after noon, with a weather class term.
            Only with a fresh window sensor, windows closed for WINDOW_CLOSED_S and the sun above the horizon. """
        ok = (w_ok and inp.sun_elev is not None and inp.sun_az is not None and inp.sun_elev > 0 and not inp.windows_open
              and (inp.since_window_closed_s is None or inp.since_window_closed_s >= WINDOW_CLOSED_S))
        if not ok:
            self.sun_off = now
            return
        if not self._sample_due(self.last_sun_sample, now):
            return
        g = self.sun_gain['am' if inp.sun_az < 180 else 'pm']
        w = self._sample_weight(self.last_sun_sample, self.sun_off, now)
        before = g.dry(inp.sun_elev)
        if g.sample(inp.sun_elev, self.solar_f, w = w) and before is not None:
            self.sun_cond.sample(self.cls, self.solar_f - before, w)
        self.last_sun_sample = now

    def _sun_expected(self, inp, now):
        """ The learned sun term at now + lead (sun position then, forecast class then, else the class now), times
            n / (n + N_PRIOR) of the bins used, times solar_gain, at most solar_max. None: no sun position; 0: unlearned. """
        if inp.sun_lead is None or None in tuple(inp.sun_lead)[:2]:
            return None
        el, az = tuple(inp.sun_lead)[:2]
        if el <= 0:
            return 0.0
        v, n = self.sun_gain['am' if az < 180 else 'pm'].learned_n(el)
        if v is None:
            return 0.0
        e = self._fc_at(inp, now + datetime.timedelta(seconds = self.lead_s()))
        cls = cond_class(e.get('condition')) if e is not None and e.get('condition') is not None else self.cls
        w = n / (n + self.t.n_prior)
        return min(self.solar_max, self.cfg.solar_gain * w * max(0.0, v + self.sun_cond.value(cls)))

    def _antic(self, inp, now, solar):
        """ (sun term expected beyond the one now, expected). An increase is held back by the first part. Each
            expectation is checked one lead later: when the sun term then is below ANTIC_CHECK of it and the room does
            not rise, the anticipation is off for ANTIC_OFF_S and the normal rules act. """
        if not self.cfg.sun_anticipation:
            return 0.0, None
        exp = self._sun_expected(inp, now)
        lead = self.lead_s()
        self.antic_hist = [(t, e) for t, e in self.antic_hist if (now - t).total_seconds() <= 2 * lead]
        if exp:
            self.antic_hist.append((now, exp))
        if not exp or (self.antic_off is not None and now < self.antic_off):
            return 0.0, exp
        extra = max(0.0, exp - solar)
        due = [e for t, e in self.antic_hist if (now - t).total_seconds() >= lead - 1]
        if extra > 0 and due and solar < ANTIC_CHECK * due[-1] and not (self.rise is not None and self.rise > 0):
            self.antic_off, self.antic_hist = now + datetime.timedelta(seconds = ANTIC_OFF_S), []   # the sun did not come
            return 0.0, exp
        return extra, exp

    def _hold_value(self, x, windy, raining, cls):
        """ (learned hold offset at x with its wind, rain or weather class term, interpolated count); (None, 0) when no
            bin has N_USE samples. """
        v, n = self.hold.learned_n(x)
        if v is None:
            return None, 0
        if windy:
            v += self.hold.term('wind', x)
        elif raining:
            v += self.hold.term('rain', x)
        else:
            v += self.hold_cond.value(cls)
        return v, n

    def _ff_hold(self, ff, windy, raining):
        """ (feed-forward, weight): the learned hold blended in with n / (n + N_PRIOR); no usable bin: ff as it was. """
        v, n = self._hold_value(self.key, windy, raining, self.cls)
        if v is None:
            return ff, 0.0
        w = n / (n + self.t.n_prior)
        return ff + w * (v - ff), w

    def _sun_free(self, inp, now) -> bool:
        """ No sun on the house: the sun below the horizon, or a cloudy / rainy / snowy class, and (with lux) SUN_COOL_S after
            the last bright lux (walls keep the heat). Without a sun position: the window baseline's quiet rule. A sunny
            hour holds the room with a lower setpoint that the sun term does not always show (sun on the room, not on the
            window sensor, or a term already decayed): the hold must not learn from it. """
        if inp.sun_elev is None:
            return self._quiet(inp, now)
        if inp.sun_elev > 0 and self.cls not in ('cloudy', 'rain', 'snow'):
            return False
        return inp.lux is None or self.last_sun is None or (now - self.last_sun).total_seconds() >= SUN_COOL_S

    def _hold_learn(self, inp, now, R, src, tgt, ff0, solar, windy, raining, base):
        """ One sample of the setpoint offset that holds the room: setpoint in force - the room + the sun term in force,
            by out_slow, after the room stayed in the band of an unchanged target_eff for HOLD_SETTLE_S with the
            setpoint (also one set by hand) and the mode unchanged as long, the room steady, windows closed, no power state
            or cold start, a small sun term and no sun on the house (_sun_free). The integral takes the change of the blended
            feed-forward, so the setpoint does not jump. """
        s = self.s
        # owner 2026-11-24: inside the band target_eff - BAND_LOW .. target_eff (+ BAND_HYST). Above the target the setpoint
        # is being lowered, not holding: in mild weather the unit idles, the room drifts slowly on its own heat and any low
        # setpoint would pass as a hold (a simulated living room at +9.5 C learned -1.6 where -0.1 holds the room)
        if src in ('primary', 'backup') and R is not None and -BAND_LOW * s <= R - tgt <= BAND_HYST * s:
            if self.band_since is None or self.band_tgt is None or abs(tgt - self.band_tgt) > 1e-6:
                self.band_since, self.band_tgt = now, tgt
        else:
            self.band_since = None

        def settled(t):
            return t is not None and (now - t).total_seconds() >= HOLD_SETTLE_S
        ok = (settled(self.band_since) and settled(self.sp_since) and settled(self.mode_since) and inp.setpoint is not None
              and self.out_slow is not None and not inp.windows_open and not inp.vacation and inp.power_state == 'normal'
              and self.pw_state == 'normal' and self.cold_start is None and solar < self.t.sun_deadband * s
              and (inp.since_window_closed_s is None or inp.since_window_closed_s >= WINDOW_CLOSED_S)
              and (self.rise is None or abs(self.rise) <= HOLD_STEADY * s)
              and inp.setpoint > self.limits(base)[0] + STEP * s    # not at the lower clamp
              and self._sun_free(inp, now))
        if not ok:
            self.hold_off = now
            return
        if not self._sample_due(self.last_hold_sample, now):
            return
        before = self._ff_hold(ff0, windy, raining)[0]
        # Relative to the room it held, not to target_eff: a room held steady 0.2 below the target needs about 0.2 more
        # (in a simulated living room 'setpoint - target_eff' learned 2.3 where 2.5 holds the target, and the push stalled)
        y, dry = inp.setpoint - R + solar, self.hold.dry(self.out_slow)
        w = self._sample_weight(self.last_hold_sample, self.hold_off, now)
        if self.hold.sample(self.out_slow, y, windy, raining, w) and not (windy or raining) and dry is not None:
            self.hold_cond.sample(self.cls, y - dry, w)
        self.last_hold_sample = now
        self.i_term = clamp(self.i_term - (self._ff_hold(ff0, windy, raining)[0] - before), -I_CLAMP * s, I_CLAMP * s)

    def _recovery(self, d, inp, now, dt_s, tgt, src, solar, manual, ff = 0.0, hold = None):
        """ The recovery episode and its push (setpoint degrees, added to sp_ctrl). An episode starts on a target_eff
            rise with the room more than BAND_LOW below it, or with the room EP_START below it for EP_START_S; it ends when
            the room is in the band, BAND_LOW below it (or after EP_MAX_S) and teaches the push gain how long it took against
            recovery_hours. A window, vacation, power state, cold start, hand change, a new target, a mode change or a
            restart discards it. push = gain x the deficit (0 in the band), not growing while the room already
            rises faster than recovery_hours needs, plus the part that lifts ff + integral to the learned hold when that is higher (the
            integral is frozen during the push and may still hold a value from an earlier state, such as a savings night;
            a push that fades with the deficit could not make up for it). That part goes into the integral when the
            episode ends, so the setpoint does not jump. """
        s, cfg = self.s, self.cfg
        real = src in ('primary', 'backup') and self.in_f is not None
        deficit = tgt - self.in_f if real else None
        raised = self.tgt_prev is not None and tgt > self.tgt_prev + 1e-6
        moved = self.tgt_prev is not None and abs(tgt - self.tgt_prev) > 1e-6
        self.tgt_prev = tgt
        stop = (not real or inp.windows_open or inp.vacation or inp.power_state != 'normal' or self.cold_start is not None
                or manual or inp.setpoint is None)
        self.below_since = (self.below_since or now) if real and deficit >= EP_START * s else None
        ep = self.ep
        if ep is not None:
            if stop or moved:
                self._episode_end(ep)
                d.logs.append(('DEBUG', "recovery episode discarded (%s)." % ('new target' if moved and not stop else 'interrupted')))
            elif deficit <= BAND_LOW * s or (now - ep['t0']).total_seconds() >= EP_MAX_S:
                # owner 2026-11-24: the band target_eff - BAND_LOW .. target_eff is the aim, reaching it ends the episode
                self._episode_end(ep)
                self._episode_learn(d, ep, now)
            elif solar >= HOLD_SUN * s:
                ep['sun_s'] += dt_s
        if self.ep is None and not stop and (
                (raised and deficit > BAND_LOW * s)
                or (self.below_since is not None and (now - self.below_since).total_seconds() >= EP_START_S)):
            self.ep = dict(t0 = now, key = self.key, cls = self.cls, g = self.push_g.value(self.key, self.cls), sun_s = 0.0, lift = 0.0)
        g = self.push_g.value(self.key, self.cls)
        d.diag.update(push_g = g, learning = self.key is None or self.push_g.episodes(self.key) < N_USE)
        if self.ep is None or not real:
            return 0.0
        # owner: the push stops above the band floor. Up to there it acts on the whole deficit; a push on the deficit below
        # the band only would fade to 0 at the floor and the last part of the way back would take hours
        dp = deficit if deficit > BAND_LOW * s else 0.0
        push = self.ep['g'] * dp
        need = dp / max(0.25, cfg.recovery_h - (now - self.ep['t0']).total_seconds() / 3600)
        if self.rise is not None and self.rise > need and self.ep.get('push') is not None:
            push = min(push, self.ep['push'])    # rising faster than needed for recovery_hours: the push adds no more
        self.ep['push'] = push
        # the lift only with a learned gain: before that the integral recovers as before (with the rising freeze)
        self.ep['lift'] = max(0.0, hold - (ff + self.i_term)) if hold is not None and self.ep['g'] > 0 else 0.0
        return push + self.ep['lift']

    def _episode_end(self, ep):
        """ The episode is over: the part of the push that lifted ff + integral to the learned hold goes into the
            integral, so the setpoint does not jump back. """
        self.ep = None
        if ep.get('lift'):
            self.i_term = clamp(self.i_term + ep['lift'], -I_CLAMP * self.s, I_CLAMP * self.s)

    def _episode_learn(self, d, ep, now):
        """ The gain the episode would have needed: the setpoint drive (1 + Kp + gain) x deficit scales the recovery
            time about inversely, so needed = (1 + Kp + gain used) x hours / recovery_hours - (1 + Kp). The gain moves only
            when the episode was slower than recovery_hours (also a timeout) or faster than EP_FAST of it (on time: only
            counted), and nothing is learned when the sun term carried more than EP_SUN_SHARE of it. """
        hours = (now - ep['t0']).total_seconds() / 3600
        if ep['key'] is None or ep['sun_s'] > EP_SUN_SHARE * hours * 3600:
            d.logs.append(('DEBUG', "recovery in %.1f h: not learned (%s)." % (hours, 'sun' if ep['key'] is not None else 'no outdoor value')))
            return
        r = hours / self.cfg.recovery_h
        if EP_FAST <= r <= 1.0:
            self.push_g.seen(ep['key'])    # on time: counts as an episode, the gain stays
            d.save = True
            return
        k0 = 1 + self.t.kp
        self.push_g.learn(ep['key'], (k0 + ep['g']) * r - k0, ep['cls'])
        d.save = True
        d.logs.append(('DEBUG', "recovery in %.1f h (aim %.1f): push gain %.2f -> %.2f." % (
            hours, self.cfg.recovery_h, ep['g'], self.push_g.value(ep['key'], ep['cls']))))

    def _sp_avg_note(self, inp, now, dt_s):
        """ owner: the average setpoint of normal heating hours, time weighted: heat, windows closed for WINDOW_CLOSED_S,
            no daytime_savings / daytime_increasing, no power state, cold start or vacation. The first SP_AVG_MIN_S are a
            plain average, then it forgets with SP_AVG_TAU_S. Only time between two runs that both qualify counts. """
        cfg = self.cfg
        ok = (cfg.target_mode == 'indoor' and inp.hvac_mode == 'heat' and inp.setpoint is not None and not inp.windows_open
              and (inp.since_window_closed_s is None or inp.since_window_closed_s >= WINDOW_CLOSED_S) and not inp.saving
              and not inp.increasing and inp.power_state == 'normal' and self.pw_state == 'normal' and self.cold_start is None
              and not inp.vacation)
        prev, self.sp_avg_t = self.sp_avg_t, (now if ok else None)
        if not ok or prev is None:
            return
        w = min(dt_s, 2 * max(cfg.interval_s, MIN_CHANGE_S))
        if self.sp_avg is None:
            self.sp_avg_sum += inp.setpoint * w
            self.sp_avg_s += w
            if self.sp_avg_s >= SP_AVG_MIN_S:
                self.sp_avg = self.sp_avg_sum / self.sp_avg_s
        else:
            self.sp_avg += (1 - math.exp(-w / SP_AVG_TAU_S)) * (inp.setpoint - self.sp_avg)

    def _p(self, tgt, rate, attr = 'i_term'):
        """ The proportional term. Heating acts against the band centre (owner: steer into target_eff - BAND_LOW ..
            target_eff), so the setpoint falls before the room crosses the target; cooling against target_eff. """
        centre = tgt - BAND_LOW * self.s / 2 if attr == 'i_term' else tgt
        return self.t.kp * (centre - (self.in_f + rate * self.t.lead_h))

    def _floor_heat(self, d, inp, now, dt_s, src, base, solar, rate, windy, raining) -> bool:
        """ owner (hard rule): with the windows shut and no vacation the room never stays BAND_LOW below target_eff
            without heating. An HVAC in fan_only or cool goes to heat at in_f <= target_eff - BAND_LOW after QUICK_MODE_S in
            the mode, whatever the sun term, MODE_MIN_S, the hold after a power state or wind say; on an estimate too.
            Watchdog: below that line for WATCHDOG_S in fan_only / cool (the unit did not take heat, for any reason):
            heat is forced at every run, one WARNING per incident. True when it switched to heat. """
        s = self.s
        tgt = self.target_eff(base, inp, windy, raining, 'heat')
        if inp.windows_open or inp.vacation or self.in_f is None:
            self.below_line_since = None
            return False
        line = tgt - BAND_LOW * s
        if self.in_f > line:
            self.below_line_since = None
            self.watch_armed = True
            return False
        if self.below_line_since is None:
            self.below_line_since = now
        below_s = (now - self.below_line_since).total_seconds()
        watchdog = below_s >= WATCHDOG_S - 1
        rule = src is not None and (self.mode_since is None or (now - self.mode_since).total_seconds() >= QUICK_MODE_S - 1)
        if not (rule or watchdog):
            return False
        ff, _ = self._ff_hold(self._ff(base, windy, raining), windy, raining)
        sp_ctrl, _ = self._pi(tgt, ff, solar, rate, base, dt_s, False)
        was = inp.hvac_mode
        self._mode(d, now, 'heat')
        if not self._cold_begin(d, inp, now, tgt):
            self._send(d, inp, now, sp_ctrl - STEP, bypass = True)
        self.started = True
        if watchdog:
            if self.watch_armed:
                d.logs.append(('WARNING', "watchdog: %s for %d min with the room %.1f, more than %.1f below the target %.1f: "
                               "heat forced." % (was, below_s / 60, self.in_f, BAND_LOW * s, tgt)))
            self.watch_armed = False
        else:
            d.logs.append(('INFO', "room %.1f at or below target %.1f - %.1f: %s -> heat." % (self.in_f, tgt, BAND_LOW * s, was)))
        d.diag['floor_heat'] = 'watchdog' if watchdog else 'band floor'
        return True

    def _step_up(self, now):
        """ The largest normal increase now: STEP_UP_EDGE once within EDGE_S after a period start or end, else STEP_UP. """
        edge = self.edge_t is not None and not self.edge_used and (now - self.edge_t).total_seconds() <= EDGE_S
        return round_half((STEP_UP_EDGE if edge else STEP_UP) * self.s)

    # ---- control terms ----------------------------------------------------------------------------- #

    def target_base(self, inp, now):
        """ target_indoor_temp / input, or the target_table entry for out_day (changes at most once a day). """
        tbl = self.cfg.target_table
        if not tbl or self.out_day is None:
            return inp.target
        outs = [e['out'] for e in tbl]
        idx = max(0, bisect.bisect_right(outs, self.out_day) - 1)
        if (self.table_idx is None or self.table_idx >= len(tbl) or (idx != self.table_idx and (
                self.table_idx_t is None or (now - self.table_idx_t).total_seconds() >= TARGET_TABLE_HOLD_S))):
            self.table_idx, self.table_idx_t = idx, now
        e = tbl[self.table_idx]
        return float(e['target']) if e.get('target') is not None else inp.target + float(e.get('offset') or 0.0)

    def target_eff(self, base, inp, windy, raining, mode = 'heat'):
        """ The deliberate shifts of classic, as target shifts. Cooling turns the daytime signs (as classic). """
        s, sign = self.s, (-1 if mode == 'cool' else 1)
        if inp.saving:      # savings replace an increase: from +0.3 straight to -0.5, not +0.3 - 0.5
            t = base - sign * SHIFT_DAY * s
        else:
            t = base + (sign * SHIFT_INCREASE * s if inp.increasing else 0.0)
            if windy or raining:
                t += SHIFT_WEATHER * s
        return t

    def _ff(self, base, windy, raining):
        """ Feed-forward: the learned internal bias at out_slow (a device that regulates its own sensor needs a
            setpoint that moves with it) plus the optional fixed outdoor gain. Bumpless when it becomes usable. """
        ok, ff = False, 0.0
        if self.out_slow is not None:
            if self.t.ff_bias_weight > 0:
                b = self.bias.value(self.out_slow, windy, raining)
                if b is not None:
                    ok, ff = True, self.t.ff_bias_weight * b
            ff += self.cfg.outdoor_gain * (base - self.out_slow)
        self.ff_jump = 0.0
        if self.ff_ok_prev is not None and ok != self.ff_ok_prev:
            self.ff_jump = self.ff_prev - ff
            self.i_term += self.ff_jump
        self.ff_prev, self.ff_ok_prev = ff, ok
        return ff

    def limits(self, base, mode = 'heat'):
        """ Setpoint range: base +- SP_SPAN; an HVAC in heat at most max_setpoint_drop (MAX_DROP) below base. """
        cfg, s = self.cfg, self.s
        drop = SP_SPAN * s
        if cfg.kind == 'hvac' and mode == 'heat':
            drop = min(drop, cfg.max_setpoint_drop if cfg.max_setpoint_drop is not None else MAX_DROP * s)
        lo = max(cfg.min_temp, base - drop)
        if cfg.vacation_temp is not None:
            lo = max(lo, cfg.vacation_temp)
        return lo, min(cfg.max_temp, base + SP_SPAN * s)

    def power_offset(self, state, mode = 'heat'):
        if state == 'normal':
            return 0.0
        sign = -1 if mode == 'cool' else 1
        if state in ('save', 'reduce'):
            return sign * (self.cfg.save_temp_offset if self.cfg.save_temp_offset is not None else SAVE_OFFSET)
        return sign * self.cfg.spend_temp_offset

    def _send(self, d, inp, now, sp, bypass = False, step_up = None):
        """ Output stage: sends round_half(sp). Normal: only when |sp - setpoint| >= SEND_HYST, at most one change per
            MIN_CHANGE_S; a change that has to wait asks for a run when it may go (retry_s). bypass (overrides,
            power-state edges, mode switches): now. step_up (normal heat changes): an increase goes at most this far
            (owner); the rest follows MIN_CHANGE_S later in its own run. """
        cur = inp.setpoint
        want = round_half(sp)
        new = None
        if cur is None or bypass:
            new = want
        elif abs(sp - cur) >= self.t.send_hyst * self.s:
            if self._send_open(now):
                new = want
            elif want != cur:
                d.retry_s = MIN_CHANGE_S - (now - self.last_change).total_seconds()
        if new is not None and step_up is not None and not bypass and cur is not None and new > cur + step_up + 1e-9:
            new = math.floor((cur + step_up) * 2 + 1e-9) / 2   # on the 0.5 grid, never more than step_up
            d.diag['step_cap'] = True
            d.retry_s = MIN_CHANGE_S
        if new is not None and new != cur:
            d.temperature = new
            self.last_commanded = new
            self.last_change = now
            self.sp_since = now

    def _send_open(self, now):
        """ The last normal change is at least MIN_CHANGE_S old. """
        return self.last_change is None or (now - self.last_change).total_seconds() >= MIN_CHANGE_S - 1

    def _mode(self, d, now, mode, forced = False):
        d.hvac_mode = mode
        self.mode_cmd = (mode, now, self.mode_since, self.mode_seen, self.mode_forced)
        self.mode_since = now
        self.mode_seen = mode
        self.mode_forced = forced

    def _mode_time_ok(self, now):
        return self.mode_forced or self.mode_since is None or (now - self.mode_since).total_seconds() >= MODE_MIN_S

    # ---- table mode helpers (same rules as cc_devices) ---------------------------------------------- #

    def _table_base(self, e):
        if 'offset' in e:
            return self.cfg.target_heater_temp + e['offset']
        if 'normal' in e:
            return e['normal']
        return self.cfg.target_heater_temp

    def _save_temp(self, t, e):
        cfg = self.cfg
        if cfg.save_temp_offset is not None:
            return t + cfg.save_temp_offset
        if cfg.save_temp is not None:
            return min(t, cfg.save_temp + e.get('offset', 0))
        if 'save' in e:
            return min(t, e['save'])
        return cfg.min_temp

    def _vacation_temp(self, t, e):
        cfg = self.cfg
        if cfg.vacation_temp is not None:
            return min(t, cfg.vacation_temp + e.get('offset', 0))
        if 'vacation' in e:
            return min(t, e['vacation'])
        return cfg.min_temp

    # ---- main -------------------------------------------------------------------------------------- #

    def update(self, inp: Inputs) -> Decision:
        """ One control run. Returns the commands; the caller sends hvac_mode first, then temperature. """
        d = Decision()
        now, cfg = inp.now, self.cfg
        dt_s = 720.0 if self.last_run is None else clamp((now - self.last_run).total_seconds(), 0.0, 6 * 3600.0)
        self.last_run = now
        self.mode_lost = False
        self._track(inp, now, dt_s)
        self.vac_end = self.vac_prev is True and not inp.vacation
        self.vac_prev = bool(inp.vacation)
        windy = inp.since_windy_s is not None and inp.since_windy_s < WINDY_S
        raining = bool(inp.raining)
        R, src = self._room(d, inp, windy, raining, dt_s)
        self._indoor(inp, R, src, now, dt_s)
        real = src in ('primary', 'backup')
        rate = self._rate(now, self.t.rate_window_s) if real else 0.0
        rate60 = ls_slope_h(self.hist, now, 3600, 1800) if real else None
        base = self.target_base(inp, now)
        self.rise = self._rate_wide(now, RISE_SPAN_S) if real else None
        self.rise_fan = self._rise_fan(now) if real else None
        excess, w_ok = self._solar(inp, R, now, dt_s, windy, raining)
        self._learn(inp, now, src, w_ok, windy, raining, rate60)
        solar = min(self.solar_max, cfg.solar_gain * self.solar_f)
        flags = (bool(inp.saving), bool(inp.increasing))
        if self.flags_prev is not None and flags != self.flags_prev:
            self.edge_t, self.edge_used = now, False    # a period started or ended: one larger step up is allowed
            if self.flags_prev[0] and not flags[0]:
                self.saving_end_t = now
            if flags[0] and not self.flags_prev[0] and cfg.drop_hold_s > 0 and inp.hvac_mode != 'fan_only':
                # owner: a savings start lowers the target; that alone must not send the heat pump to fan_only (as after
                # an increase: none for an HVAC that is in fan_only at that moment)
                self.drop_hold_until = now + datetime.timedelta(seconds = cfg.drop_hold_s)
        self.flags_prev = flags
        self._sp_avg_note(inp, now, dt_s)
        if inp.hvac_mode == 'heat':
            self.below_line_since = None
        self.cls = cond_class(inp.condition)
        self.key = self._lookup_key(inp, now)
        self._sun_learn(inp, now, w_ok)
        if inp.hvac_mode != 'heat' or cfg.target_mode == 'table':
            self.ep = self.below_since = self.band_since = self.tgt_prev = None
        d.diag = dict(src = src, R = R, in_f = self.in_f, rate = rate, rate_wide = self._rate_wide(now),
                      excess = excess, solar = solar, out_slow = self.out_slow, out_day = self.out_day,
                      base_w = self.base_w.value(inp.out_temp, windy, raining) if inp.out_temp is not None else None,
                      bias_int = self.bias.value(self.out_slow, windy, raining) if self.out_slow is not None else None,
                      rise = self.rise, rise_fan = self.rise_fan, smoothing = self.tau_in / 60, sun_elev = inp.sun_elev, sun_az = inp.sun_az,
                      cond = self.cls, step_cap = False, sp_avg = self.sp_avg, mode_lost = self.mode_lost, sun_gate = self.sun_gate)
        args = (d, inp, now, dt_s, R, src, base, solar, rate, windy, raining)
        if cfg.target_mode == 'table' and cfg.kind == 'heater':
            self._table(*args)
        elif cfg.kind == 'hvac' and inp.hvac_mode == 'cool':
            self._cool(*args)
        elif cfg.kind == 'hvac' and inp.hvac_mode == 'fan_only':
            self._fan_only(*args)
        elif inp.hvac_mode == 'heat':
            self._heat(*args)
        if self.last_save is None or (now - self.last_save).total_seconds() >= SAVE_EVERY_S:
            d.save = True
        if d.save:
            self.last_save = now
        return d

    def _clear_pw(self):
        self.pw_state, self.pw_base, self.pw_tripped = 'normal', None, False

    def _freeze(self, inp, src, tgt):
        if inp.windows_open:
            return 'window open'
        if inp.vacation:
            return 'vacation'
        if inp.power_state != 'normal':
            return 'power ' + inp.power_state
        if src not in ('primary', 'backup'):
            return 'no room sensor' if src is None else 'estimate'
        if (inp.since_window_closed_s is not None and inp.since_window_closed_s < WINDOW_CLOSED_S
                and not (self.in_f is not None and self.in_f < tgt - 1.0 * self.s)):
            return 'window closed < 1 h'
        return None

    def _pi(self, tgt, ff, solar, rate, base, dt_s, integrate, attr = 'i_term', grow = True):
        """ sp_ctrl = clamp(tgt + ff + i + p - solar). The integral (attr) runs only when allowed, outside the
            deadband, and not further into a clamp (anti-windup). Heating (owner 2026-11-24): the deadband is the band
            target_eff - BAND_LOW .. target_eff + BAND_HYST, so the integral only grows below the band and runs down as soon
            as the room is above the target; cooling keeps DB_BELOW / DB_ABOVE. grow False: it does not move toward more
            output (heat: up, cool: down), it may still unwind; self.grow_blocked tells whether that bit. """
        s = self.s
        self.grow_blocked = False
        lo, hi = self.limits(base, 'cool' if attr == 'i_cool' else 'heat')
        if self.in_f is None:
            return clamp(tgt + ff + getattr(self, attr) - solar, lo, hi), 0.0
        p = self._p(tgt, rate, attr)
        e = tgt - self.in_f
        ei = tgt - (self.in_f + rate * self.t.lead_h) if self.t.i_pred else e
        below, above = (BAND_LOW, BAND_HYST) if attr == 'i_term' else (DB_BELOW, DB_ABOVE)
        if (integrate and dt_s <= 3 * max(MIN_CHANGE_S, self.cfg.interval_s) and abs(e) <= self.t.i_band * s
                and (ei > below * s or ei < -above * s)):
            raw = tgt + ff + getattr(self, attr) + p - solar
            if not grow and (ei > 0 if attr == 'i_term' else ei < 0):
                self.grow_blocked = True
            elif not ((raw >= hi and ei > 0) or (raw <= lo and ei < 0)):
                setattr(self, attr, clamp(getattr(self, attr) + self.t.ki_h * ei * dt_s / 3600, -I_CLAMP * s, I_CLAMP * s))
        return clamp(tgt + ff + getattr(self, attr) + p - solar, lo, hi), p

    def _bumpless(self, d, inp, tgt, ff, solar, rate, attr = 'i_term', mode = 'heat', first = False):
        """ At start, after a restart and after a manual change: the integral is set so that sp_ctrl equals the
            current setpoint (without a power offset that is in it), so nothing jumps. """
        if inp.setpoint is not None and not inp.windows_open and not inp.vacation:
            if first and self.in_f is not None and abs(tgt - self.in_f) > self.t.i_band * self.s:
                # Start (or restart) with the room far from target: the integral is NOT set to hold the current
                # setpoint (that would leave it stuck while the room does not move). It starts at 0 and the output
                # stage sends sp_ctrl.
                setattr(self, attr, 0.0)
                self.last_commanded = inp.setpoint
                self.started = True
                return
            ref = inp.setpoint
            if self.pw_state != 'normal' and self.pw_base is not None:
                ref = self.pw_base
            p = self._p(tgt, rate, attr) if self.in_f is not None else 0.0
            setattr(self, attr, clamp(ref - (tgt + ff + p - solar), -I_CLAMP * self.s, I_CLAMP * self.s))
            self.last_commanded = inp.setpoint
        self.started = True

    def _manual(self, d, inp, mode = 'heat'):
        """ A setpoint that is not the last one sent was changed by hand (or by another app): follow it. Not while
            the last change is younger than MIN_CHANGE_S: Home Assistant may still show the setpoint from before it
            (a run can come seconds after a send), and no normal change can overwrite a hand change meanwhile. """
        if (not self.started or inp.setpoint is None or self.last_commanded is None
                or inp.setpoint == self.last_commanded or inp.windows_open or inp.vacation
                or not self._send_open(inp.now)):
            return False
        d.logs.append(('INFO', "setpoint changed by hand to %s: followed." % inp.setpoint))
        if self.pw_state != 'normal' and self.pw_base is not None:
            self.pw_base = inp.setpoint - self.power_offset(self.pw_state, mode)
            self.last_commanded = inp.setpoint
            return False
        return True

    def _power_layer(self, d, inp, tgt, mode):
        """ Returns (offset active, edge). The base is the setpoint at the start of the state; the comfort guard
            drops the offset until the state changes (save/reduce: room below target_eff - guard; spend: above
            target_eff + guard). The controller is frozen meanwhile (no fight, no learning). """
        st = inp.power_state
        edge = False
        if st != 'normal':
            if self.pw_state != st:
                if self.pw_base is None:
                    self.pw_base = inp.setpoint if inp.setpoint is not None else self.last_commanded
                self.pw_state, self.pw_tripped, edge = st, False, True
                d.save = True
            hot = 1 if mode == 'cool' else -1
            if not self.pw_tripped and self.in_f is not None and (
                    (st in ('save', 'reduce') and hot * (self.in_f - tgt) > self.guard)
                    or (st == 'spend' and -hot * (self.in_f - tgt) > self.guard)):
                self.pw_tripped, edge = True, True
                d.logs.append(('INFO', "comfort guard: room %.1f, target %.1f, '%s' offset dropped until the state changes."
                               % (self.in_f, tgt, st)))
            return (not self.pw_tripped and self.pw_base is not None), edge
        if self.pw_state != 'normal':
            self._clear_pw()
            self.pw_ended = inp.now
            d.save = True
            edge = True
        return False, edge

    # ---- modes ------------------------------------------------------------------------------------- #

    def _heat(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        cfg, s = self.cfg, self.s
        tgt = self.target_eff(base, inp, windy, raining, 'heat')
        ff0 = self._ff(base, windy, raining)
        self._hold_learn(inp, now, R, src, tgt, ff0, solar, windy, raining, base)
        ff, w_hold = self._ff_hold(ff0, windy, raining)
        if self.ff_jump and w_hold:
            self.i_term -= w_hold * self.ff_jump    # _ff took the whole jump of ff0; only (1 - w) of it reaches ff
        first = not self.started
        manual = not first and self._manual(d, inp)
        if first or manual:
            self._bumpless(d, inp, tgt, ff, solar, rate, first = first)
            self.cold_start = None
        freeze = self._freeze(inp, src, tgt)
        if freeze is None and self.t.sun_freeze > 0 and solar >= self.t.sun_freeze * s:
            freeze = 'sun'
        if freeze is None and self.cold_start is not None:
            freeze = 'cold start'
        hv, hn = self._hold_value(self.key, windy, raining, self.cls)
        # owner: the first savings end before a hold is learned starts at the average setpoint of normal hours + 0.5
        boot = (self.saving_end_t == now and hv is None and self.sp_avg is not None and self.tgt_prev is not None
                and tgt > self.tgt_prev + 1e-6 and self.in_f is not None and tgt - self.in_f > BAND_LOW * s)   # not when in the band
        push = self._recovery(d, inp, now, dt_s, tgt, src, solar, manual, ff, hv)
        antic, solar_exp = self._antic(inp, now, solar)
        in_band = self.in_f is not None and tgt - self.in_f <= BAND_LOW * s
        if in_band:
            antic = 0.0    # owner: inside the band (and above) the anticipation does not act
        # owner: the integral does not grow while the push acts, while the room rises (judged over 30 min or more) and
        # while an increase waits for the sun ahead; it may still unwind
        block = 'push' if push > 0 else 'rising' if self.rise is not None and self.rise >= RISE_FREEZE * s else None
        if block is None and antic > 0 and inp.setpoint is not None and self.in_f is not None:
            lo0, hi0 = self.limits(base)
            sp_now = clamp(tgt + ff + push + self.i_term + self._p(tgt, rate) - solar, lo0, hi0)
            block = 'sun ahead' if sp_now > inp.setpoint else None
        sp_ctrl, p = self._pi(tgt, ff + push, solar, rate, base, dt_s, freeze is None, grow = block is None)
        lo, hi = self.limits(base)
        d.diag.update(mode = 'heat', target_eff = tgt, ff = ff, i = self.i_term, p = p, sp_ctrl = sp_ctrl,
                      frozen = freeze or (block if self.grow_blocked else None), hold = hv, hold_n = hn, push = push,
                      solar_exp = solar_exp, antic = 0.0)
        vac = cfg.vacation_temp if cfg.vacation_temp is not None else 10.0
        if inp.windows_open or inp.vacation:
            self.restore_up = True    # the way back from the override is part of it: not held to STEP_UP
            self._clear_pw()
            self.cold_start = None
            if cfg.kind == 'hvac' and inp.windows_open:
                self._mode(d, now, 'fan_only', forced = True)
                return
            if inp.windows_open and cfg.save_temp is not None:
                vac = min(vac, cfg.save_temp)   # open window: the lowest of vacation and save (as table mode)
            self._send(d, inp, now, vac, bypass = True)
            if (cfg.kind == 'hvac' and src in ('primary', 'backup')
                    and self.in_f > inp.target - 3 * s and self.in_f > vac + 3 * s):
                self._mode(d, now, 'fan_only', forced = True)
            return
        self._cold_begin(d, inp, now, tgt)
        active, edge = self._power_layer(d, inp, tgt, 'heat')
        if cfg.kind == 'hvac':
            if src in ('primary', 'backup'):
                extra = cfg.spend_temp_offset if (inp.power_state == 'spend' and active) else 0.0
                held = self.pw_ended is not None and (now - self.pw_ended).total_seconds() < POWER_END_HOLD_S
                rising = self.rise_fan is not None and self.rise_fan >= FAN_RISE * s    # owner: over the last 30 min
                if (self.in_f > tgt + FAN_ABOVE * s + extra and (cfg.has_fireplace or sp_ctrl <= lo + 1e-9 or rising)
                        and not held and (inp.since_windy_s is None or inp.since_windy_s > FAN_WIND_S)
                        and self._mode_time_ok(now)):
                    if self._increase_hold(inp, tgt, solar):
                        d.diag['increase_hold'] = True    # fan_only waits: the room is still coming down after an increase
                        if self.drop_hold_until is not None and now < self.drop_hold_until:
                            # the device schedules a run at the end of the hold after an increase, not after a savings start
                            left = (self.drop_hold_until - now).total_seconds() + 5
                            d.retry_s = left if d.retry_s is None else min(d.retry_s, left)
                    else:
                        self._clear_pw()
                        self._mode(d, now, 'fan_only')
                        return
            boost = (inp.since_windy_s is not None and inp.since_windy_s < BOOST_WIND_S
                     and self.in_f is not None and self.in_f < tgt - BOOST_BELOW * s)
            if boost and inp.boost_available and inp.preset != 'boost':
                d.preset = 'boost'
            elif not boost and inp.preset == 'boost':
                d.preset = 'none'
        if self.cold_start is not None:
            if self.in_f is None or (self.in_f < tgt - COLD_BACK_WITHIN * s
                                     and (now - self.cold_start).total_seconds() < COLD_START_MAX_H * 3600):
                if not active:
                    if edge:
                        self._send(d, inp, now, self.cold_sp, bypass = True)   # a power state ended: back to the start setpoint
                    return
            else:
                # The way down: one move to sp_ctrl; the integral is set so that sp_ctrl is the value sent (no jump back)
                goal = round_half(sp_ctrl)
                self.i_term = clamp(goal - (tgt + ff + p - solar), -I_CLAMP * s, I_CLAMP * s)
                self.cold_start = None
                d.logs.append(('INFO', "cold start ended: room %.1f, target %.1f, setpoint %s." % (self.in_f, tgt, goal)))
                if not active:
                    self._send(d, inp, now, goal, bypass = True)
                    return
                self.pw_base = goal
        if active:
            self._send(d, inp, now, self.pw_base + self.power_offset(inp.power_state, 'heat'), bypass = True)
            return
        if boot and not edge:
            start = round_half(clamp(self.sp_avg + SP_AVG_ABOVE * s, lo, hi))
            if inp.setpoint is None or start > inp.setpoint:
                # one change straight to it (not +STEP_UP_EDGE); the integral is set so that sp_ctrl is that value
                self.i_term = clamp(start - (tgt + ff + push + p - solar), -I_CLAMP * s, I_CLAMP * s)
                d.diag.update(sp_ctrl = start, i = self.i_term, boot = start)
                d.logs.append(('INFO', "savings ended, no hold learned yet: start at the average setpoint %.2f + %.1f = %s."
                               % (self.sp_avg, SP_AVG_ABOVE * s, start)))
                self._send(d, inp, now, start, bypass = True)
                self.edge_used = True
                return
        if (self.in_f is not None and self.in_f > tgt + BAND_HYST * s and inp.setpoint is not None and not edge
                and self.last_rep_t is not None and self.last_rep_t != self.quick_rep):
            # owner: above the target the setpoint comes down one step per run at once (not after MIN_CHANGE_S), at most
            # one step per room report; the integral follows so that sp_ctrl does not pull it back up
            down = max(lo, min(round_half(sp_ctrl), inp.setpoint - round_half(STEP * s)))
            if round_half(down) < inp.setpoint:
                if sp_ctrl > down:
                    self.i_term = clamp(self.i_term - (sp_ctrl - down), -I_CLAMP * s, I_CLAMP * s)
                    d.diag.update(sp_ctrl = down, i = self.i_term)
                self.quick_rep = self.last_rep_t
                d.diag['quick_down'] = True
                self._send(d, inp, now, down, bypass = True)
                return
        sp_out = sp_ctrl
        if antic > 0 and inp.setpoint is not None and sp_ctrl > inp.setpoint:
            sp_out = max(inp.setpoint, sp_ctrl - antic)    # the sun ahead: an increase waits or shrinks, never below the setpoint
            d.diag['antic'] = sp_ctrl - sp_out
        step = None if self.restore_up else self._step_up(now)
        if self._send_open(now):
            self.restore_up = False   # the first run that may change it after a window / vacation override
        self._send(d, inp, now, sp_out, bypass = edge, step_up = step)
        if (d.temperature is not None and inp.setpoint is not None and d.temperature > inp.setpoint
                and step is not None and step > round_half(STEP_UP * s)):
            self.edge_used = True

    def _increase_hold(self, inp, tgt, solar) -> bool:
        """ The time after a period boundary that lowered the target (increase_hold_hours): no heat -> fan_only, so the
            room can fall back to the target by itself. After a daytime_increasing period (the device tells: after_increase,
            also when a daytime_savings period starts inside it) and, owner 2026-11-24, after a daytime_savings start
            (drop_hold_until). Ends early when the sun term of the window sensor is HOLD_SUN or more, or the room is HOLD_ROOM
            above the fan_only line (fireplace). """
        dropped = self.drop_hold_until is not None and inp.now < self.drop_hold_until
        if not (inp.after_increase or dropped):
            return False
        s = self.s
        if solar >= HOLD_SUN * s:
            return False
        return not (self.in_f is not None and self.in_f > tgt + (FAN_ABOVE + HOLD_ROOM) * s)

    def _cold_begin(self, d, inp, now, tgt) -> bool:
        """ First run after a vacation ended, room at least cold_start_deficit below tgt: the cold start setpoint
            tgt + the deficit (at most COLD_START_MAX above tgt and max_temp) at once. A power state that is on keeps it
            as its base. True when started. """
        cfg, s = self.cfg, self.s
        if not self.vac_end or self.in_f is None:
            return False
        deficit = tgt - self.in_f
        if deficit < (cfg.cold_start_deficit if cfg.cold_start_deficit is not None else COLD_START_DEFICIT * s):
            return False
        start = round_half(min(cfg.max_temp, tgt + min(deficit, COLD_START_MAX * s)))
        self.cold_start, self.cold_sp = now, start
        if inp.power_state != 'normal':
            self.pw_base = start
        d.logs.append(('INFO', "vacation ended with the room %.1f below target: cold start at %s until the room is near "
                       "the target." % (deficit, start)))
        self._send(d, inp, now, start, bypass = True)
        return True

    def _fan_only(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        cfg, s = self.cfg, self.s
        tgt = self.target_eff(base, inp, windy, raining, 'heat')
        ff, w_hold = self._ff_hold(self._ff(base, windy, raining), windy, raining)
        if self.ff_jump and w_hold:
            self.i_term -= w_hold * self.ff_jump
        sp_ctrl, p = self._pi(tgt, ff, solar, rate, base, dt_s, False)
        d.diag.update(mode = 'fan_only', target_eff = tgt, ff = ff, i = self.i_term, p = p, sp_ctrl = sp_ctrl)
        self._clear_pw()
        self.cold_start = None
        if self._floor_heat(d, inp, now, dt_s, src, base, solar, rate, windy, raining):
            return
        real = src in ('primary', 'backup')
        if inp.windows_open or not real:
            return    # no other mode change on an estimate
        vac = cfg.vacation_temp if cfg.vacation_temp is not None else 10.0
        if inp.vacation:
            if self.in_f <= inp.target - 5 * s or self.in_f <= vac + 2 * s:
                self._mode(d, now, 'heat', forced = True)
                self._send(d, inp, now, vac, bypass = True)
            elif self.in_f > cfg.max_vacation_temp:
                self._mode(d, now, 'cool', forced = True)
            return
        if not self._mode_time_ok(now):
            return
        if (self.in_f > inp.target + 2 * s and inp.out_temp is not None and inp.out_temp > inp.target - 4 * s
                and ((inp.daytime and inp.rain == 0) or self.in_f > inp.target + 3 * s)):
            self._mode(d, now, 'cool')
            return
        if self.in_f <= tgt and solar < 1.0 * s:
            self._mode(d, now, 'heat')
            if not self._cold_begin(d, inp, now, tgt):
                self._send(d, inp, now, sp_ctrl - STEP, bypass = True)   # instead of classic's start after fan_only (offset -1)
            self.started = True

    def _cool(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        cfg, s = self.cfg, self.s
        tgt = self.target_eff(base, inp, windy, raining, 'cool')
        real = src in ('primary', 'backup')
        self.cold_start = None
        if inp.windows_open:
            self._clear_pw()
            self._mode(d, now, 'fan_only', forced = True)
            return
        if self._floor_heat(d, inp, now, dt_s, src, base, solar, rate, windy, raining):
            self._clear_pw()
            return
        if real and self.in_f is not None:
            to_fan =(inp.daytime is False and inp.rain > 0 and self.in_f < tgt + 2 * s) or self.in_f < tgt + 1 * s
            vac_fan = inp.vacation and self.in_f < cfg.max_vacation_temp - 2 * s
            if vac_fan or (to_fan and self._mode_time_ok(now)):
                self._clear_pw()
                self._mode(d, now, 'fan_only', forced = vac_fan)
                return
        first = not self.started
        if first or self._manual(d, inp, 'cool'):
            self._bumpless(d, inp, tgt, 0.0, solar, rate, 'i_cool', 'cool', first = first)
        freeze = self._freeze(inp, src, tgt)
        # cooling: no feed-forward, the sun term adds cooling (lower setpoint), never learned (as classic). The integral
        # does not grow toward more cooling while the room already falls (the mirror of the heating rule)
        falling = self.rise is not None and self.rise <= -RISE_FREEZE * self.s
        sp_ctrl, p = self._pi(tgt, 0.0, solar, rate, base, dt_s, freeze is None, 'i_cool', grow = not falling)
        d.diag.update(mode = 'cool', target_eff = tgt, i = self.i_cool, p = p, sp_ctrl = sp_ctrl,
                      frozen = freeze or ('falling' if self.grow_blocked else None))
        active, edge = self._power_layer(d, inp, tgt, 'cool')
        if active:
            self._send(d, inp, now, self.pw_base + self.power_offset(inp.power_state, 'cool'), bypass = True)
        else:
            self._send(d, inp, now, sp_ctrl, bypass = edge)

    def _table(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        """ Heater table mode with the adaptive trim: table base + rain/wind 1 -
            min(3, sun + room above target) + trim_i, then save / spend / vacation / min_temp as classic. The
            room term uses R_used (an estimate counts); without any room value it is dropped (classic counts the
            climate's current_temperature as the room then, which on a floor thermostat is the floor). """
        cfg, s = self.cfg, self.s
        if inp.out_temp is None or inp.hvac_mode in (None, 'off'):
            return
        e = table_entry(cfg.temperatures, inp.out_temp) or dict(offset = 0)
        tb = self._table_base(e)
        save_t, vac_t = self._save_temp(tb, e), self._vacation_temp(tb, e)
        if inp.power_state == 'reduce' or inp.windows_open:
            if inp.setpoint is None:
                self._send(d, inp, now, cfg.min_temp, bypass = True)
            elif inp.setpoint > save_t:
                self._send(d, inp, now, min(save_t, vac_t), bypass = True)
            return
        sp = tb + (1.0 if (inp.raining or inp.wind_now) else 0.0)
        room_term = max(0.0, R - base) if R is not None else 0.0
        sp -= min(TABLE_ADJ_MAX * s, cfg.solar_gain * self.solar_f + room_term)
        if (src in ('primary', 'backup') and not inp.vacation and inp.power_state == 'normal' and not inp.saving
                and (inp.since_window_closed_s is None or inp.since_window_closed_s >= WINDOW_CLOSED_S)
                and dt_s <= 3 * max(900, cfg.interval_s)):
            err = base - self.in_f
            if err > DB_BELOW * s or err < -DB_ABOVE * s:
                self.trim_i = clamp(self.trim_i + self.t.ki_h * err * dt_s / 3600, -TRIM_CLAMP * s, TRIM_CLAMP * s)
        sp += self.trim_i
        if inp.vacation:
            sp = vac_t
        if inp.power_state == 'save' or inp.saving:
            sp = self._save_temp(sp, e)
        if inp.power_state == 'spend':
            sp += cfg.spend_temp_offset
        sp = max(sp, vac_t, cfg.min_temp)
        d.diag.update(mode = 'table', target_eff = base, trim = self.trim_i, room_term = room_term, sp_ctrl = sp)
        if inp.setpoint is None or round_half(sp) != inp.setpoint:
            self._send(d, inp, now, sp, bypass = True)
