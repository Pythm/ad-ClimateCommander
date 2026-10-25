""" Adaptive indoor-temperature controller for ClimateCommander (controller: shadow / adaptive), pure Python.

    Identical copy of the harness prototype: apps/ClimateCommander/cc_control.py and
    .claude/audit_out/cc_harness/proto_controller.py must stay byte-identical; change both. No AppDaemon
    imports. The caller (cc_devices.py in the app, proto.py in the harness) reads the entities, fills an Inputs, calls
    IndoorController.update() once per control run (and on window/vacation/power events) and carries out the
    returned Decision (temperature, hvac_mode, preset, logs, save).

    Design: PLAN_CC_indoor_control_opus.md sections 3 and 4 and DESIGN_ADDENDUM_steer.md, with the owner
    answers of 2026-10-22/23. Heating:
        target_eff = target (or target_table(out_day)) + daytime increasing/saving + wind/rain shift
        R_used     = primary room sensor | backup + learned backup bias | internal - bias_int | None
        ff         = ff_bias_weight * bias_int(out_slow, windy, raining) + outdoor_gain * (target - out_slow)
        excess     = window - R_used - base_w(out_temp, windy, raining)      (learned no-sun baseline)
        solar      = min(solar_max, solar_gain * solar_f)  (rises at once, decays slowly)
        sp_ctrl    = clamp(target_eff + ff + i_term + Kp * (target_eff - (in_f + rate * lead)) - solar)
    The power-state layer (with the comfort guard), the window/vacation overrides and the HVAC mode choice
    sit on top. Normal changes: at most one per MIN_CHANGE_S, one STEP at a time.

    Nothing here is tuned to one house: the window baseline and the internal-sensor bias are learned by
    outdoor temperature (LearnedGap), with no sign assumption; optional YAML tables only seed them.

    Units: temperatures in the device unit. Constants marked (deg) are temperature differences in C and are
    multiplied by 1.8 for F; the learned-bin width becomes 4 F.
"""
from __future__ import annotations

import bisect
import datetime
import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------------------------- #
# Constants. [tuned] = chosen by the R2 scripted search in the simulation (cc_harness/r2/tune_search.py);
# the others are owner decisions or design values from the plan / addendum.
# ---------------------------------------------------------------------------------------------------- #
VERSION = 1
MIN_CHANGE_S = 720              # owner: at most one setpoint change per 12 min
STEP = 0.5                      # owner: setpoint grid, and the largest normal change per send
ROOM_STALE_S = 3600             # room and backup sensor fresh when reported within 1 h (as classic)
WINDOW_STALE_S = 12 * 3600      # window sensor: the live ones report only on a ~1 C change, up to 14 h apart
EST_TAU_S = 3600                # smoothing of the internal-sensor estimate (climate sensors report in 0.5-1 steps)
MODE_MIN_S = 1800               # HVAC: minimum time in heat / fan_only / cool before an automatic switch
FAN_ABOVE = 0.6                 # (deg) heat -> fan_only when the room is this far above target_eff ...
FAN_RISE = 0.2                  # (deg per h) ... and the setpoint is at its lower clamp or the room rises this fast
                                #    (has_fireplace: at FAN_ABOVE alone)
MAX_DROP = 3.0                  # (deg) HVAC heat: lowest setpoint this far below target when max_setpoint_drop is not set (as
                                #    classic): the unit idles below its own sensor reading, lower only slows the way back
RETURN_JUMP = 4.0               # (deg) HVAC heat: once the setpoint was lowered for an overshoot (room above target + FAN_ABOVE,
                                #    no fan_only), the first change with the room at or below target may skip the STEP
                                #    limit, up to the setpoint before the lowering and at most target + this
COLD_START_DEFICIT = 2.0        # (deg) vacation end with the room at least this far below target_eff (cold_start_deficit):
                                #    cold start ...
COLD_START_MAX = 6.0            # (deg) owner: ... at target_eff + the deficit, at most this above it (and max_temp), sent at
                                #    once and held ...
COLD_BACK_WITHIN = 1.0          # (deg) owner: ... until the room is this close to target_eff ...
COLD_START_MAX_H = 8            # owner: ... or this many hours (safety); then one move to sp_ctrl, the integral set to it
POWER_END_HOLD_S = 3600         # no automatic heat -> fan_only this long after a power state ended (the room was
                                #    moved on purpose by spend; found in R2 on room_B S7)
DB_ABOVE = 0.1                  # (deg) D1: integrate when the room is above target_eff by more than this
DB_BELOW = 0.25                 # (deg) ... or below it by more than this
I_CLAMP = 6.0                   # (deg) integral bound (floor thermostats read 5+ above the room)
SP_SPAN = 8.0                   # (deg) setpoint within target +- this (as classic)
RATE_MAX = 2.0                  # (deg per h) trend clamp
RATE_MIN_POINTS = 3             # trend needs this many room reports in its window (else 0)
WINDOW_CLOSED_S = 3600          # integral and learning wait this long after the last window close
WINDY_S = 1800                  # wind flag: wind >= anemometer_speed within 30 min (as classic)
FAN_WIND_S = 2700               # no heat -> fan_only within 45 min of wind (as classic)
BOOST_WIND_S = 1500             # boost preset within 25 min of wind ...
BOOST_BELOW = 0.7               # (deg) ... and the room this far below target (as classic)
SHIFT_DAY = 0.5                 # (deg) daytime increasing / saving target shift (as classic)
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
BACKUP_ALPHA = 0.1              # EWMA of primary minus backup while both are fresh
# learned relations (LearnedGap, addendum 1.1)
GAP_BW = 2.0                    # (deg) bin width (4 F)
N_USE = 5                       # smallest bin used
N_VALID = 30                    # outlier rejection starts here
N_PRIOR_WR = 10                 # prior weight of the wind / rain terms
N_FORGET = 300                  # floor of the learning rate: 1 / N_FORGET
REJECT = 5.0                    # (deg) outlier limit
SAMPLE_MIN_S = 600              # at most one learning sample per control run
QUIET_LUX = 500                 # window baseline learns only below this lux ...
SUN_LUX = 2000                  # ... and SUN_COOL_S after lux was last at or above this
SUN_COOL_S = 3600
SETTLED_RATE = 0.3              # (deg per h) internal bias learns only while the room moves less than this ...
SETTLED_SP_S = dict(hvac = 3600, heater = 7200)   # ... and the setpoint is this old
SOLAR_DERIV_S = 3600            # window of the excess derivative
# [tuned] per device kind, by r2/tune_search.py (seeded random search + coordinate refinement in thermal_sim,
# hvac trained on room_A, heater on floor + floor_table; see r2/tune_search_both.txt). Rounded from r2/tuned.json.
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


def round_half(value: float) -> float:
    """ Climate entities take 0.5 steps (same rounding as cc_devices.round_half). """
    return round(value * 2, 0) / 2


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def table_entry(table, x):
    """ The entry that applies at x: the last one with out <= x; below the first entry, the first one
        (bisect_right, the same rule as 'temperatures', T1). None without a table or x. """
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

    def sample(self, x, y, windy = False, raining = False) -> bool:
        """ Learns one sample; wind wins over rain. Returns False when rejected (outlier, or a wind/rain sample
            without a dry value to compare with). """
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
        a = max(1.0 / (t[1] + 1), 1.0 / N_FORGET)
        t[0] += a * (v - t[0])
        t[1] += 1
        return True

    def to_dict(self) -> dict:
        return dict(bw = self.bw, bins = dict((str(k), [round(m, 4), n]) for k, (m, n) in sorted(self.bins.items())),
                    wind = [round(self.wind[0], 4), self.wind[1]], rain = [round(self.rain[0], 4), self.rain[1]])

    def load(self, d) -> bool:
        """ Restores to_dict() output. False (nothing loaded) when the bin width differs or the data is bad. """
        try:
            if float(d['bw']) != self.bw:
                return False
            bins = dict((int(k), [float(m), int(n)]) for k, (m, n) in d['bins'].items())
            wind = [float(d['wind'][0]), int(d['wind'][1])]
            rain = [float(d['rain'][0]), int(d['rain'][1])]
        except (KeyError, TypeError, ValueError, IndexError):
            return False
        self.bins, self.wind, self.rain = bins, wind, rain
        return True


@dataclass
class Tuning:
    """ The [tuned] constants for one device kind. R3 uses Tuning.default(kind); the harness search passes others. """
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
    """ Per device, from the app YAML (cc_config DeviceConfig in R3). """
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
    interval_s: float = 720.0             # seconds between control runs (interval_minutes)
    cold_start_deficit: float | None = None   # vacation end: cold start when the room is this far below target (None: 2 C / 3.6 F)


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
    daytime: bool | None = None           # classic 'sunrise'..'sunset - 00:30' (E1); sun flag when lux is None
    since_windy_s: float | None = None    # seconds since wind >= anemometer_speed
    wind_now: bool = False                # table mode: wind >= anemometer_speed right now
    raining: bool = False                 # rain >= rain_level
    rain: float = 0.0                     # rain amount (E5: cool -> fan_only at night when > 0)
    windows_open: bool = False
    since_window_closed_s: float | None = None
    vacation: bool = False
    power_state: str = 'normal'
    increasing: bool = False              # daytime_increasing active
    after_increase: bool = False          # within 1 h after a daytime_increasing period
    saving: bool = False                  # daytime_savings active
    preset: str | None = None
    boost_available: bool = False         # 'boost' in preset_modes and fan_mode is not 'Silence'


@dataclass
class Decision:
    temperature: float | None = None      # send climate/set_temperature
    hvac_mode: str | None = None          # send climate/set_hvac_mode (before the temperature)
    preset: str | None = None             # send climate/set_preset_mode
    save: bool = False                    # persist snapshot() now
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
    PERSIST_T = ('last_sun', 'last_change', 'table_idx_t', 'mode_since', 'sp_since')

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
        # signal state
        self.out_slow = self.out_day = None
        self.in_f = None
        self.hist = []                    # (report time, value) of real room readings
        self.last_rep_t = None
        self.src_prev = None
        self.backup_bias = None
        self.solar_f = 0.0
        self.exc_hist = []
        self.last_sun = None
        # controller state
        self.i_term = 0.0
        self.i_cool = 0.0
        self.trim_i = 0.0
        self.ff_prev = None
        self.ff_ok_prev = None
        self.pw_state = 'normal'
        self.pw_base = None
        self.pw_tripped = False
        self.pw_ended = None              # when the last power state ended
        self.last_commanded = None
        self.last_change = None
        self.pre_lower = None             # HVAC heat: setpoint before it was lowered for an overshoot (RETURN_JUMP)
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
        return []

    # ---- signals ----------------------------------------------------------------------------------- #

    def _log_once(self, d, key, level, text):
        if key not in self.logged:
            self.logged.add(key)
            d.logs.append((level, text))

    def _track(self, inp, now, dt_s):
        if inp.hvac_mode != self.mode_seen:
            if self.mode_seen is not None or self.mode_since is None:
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

    def _room(self, d, inp, windy, raining):
        """ (R_used, source): 'primary', 'backup' (+ learned bias), 'estimate' (internal - bias_int) or None. """
        def fresh(v, age, lim):
            return v is not None and (age is None or age <= lim)
        p_ok = fresh(inp.room, inp.room_age_s, ROOM_STALE_S)
        b_ok = fresh(inp.backup, inp.backup_age_s, ROOM_STALE_S)
        if p_ok and b_ok:
            diff = inp.room - inp.backup
            self.backup_bias = diff if self.backup_bias is None else self.backup_bias + BACKUP_ALPHA * (diff - self.backup_bias)
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
            time, so a stale value is not a new sample and extra runs do not fake a trend (C3, C4, C5). """
        if src in ('primary', 'backup'):
            age = inp.room_age_s if src == 'primary' else inp.backup_age_s
            t_rep = now - datetime.timedelta(seconds = age) if age is not None else now
            if self.src_prev not in ('primary', 'backup') or self.in_f is None or self.last_rep_t is None:
                self.in_f, self.hist, self.last_rep_t = R, [(t_rep, R)], t_rep
            elif (t_rep - self.last_rep_t).total_seconds() >= 1:
                dtr = (t_rep - self.last_rep_t).total_seconds()
                a = dtr / (self.t.tau_in_s + dtr) if self.t.tau_in_s > 0 else 1.0
                self.in_f += a * (R - self.in_f)
                self.hist.append((t_rep, R))
                self.last_rep_t = t_rep
            keep = max(self.t.rate_window_s, 3600) + 600
            self.hist = [(t, v) for t, v in self.hist if (now - t).total_seconds() <= keep]
        elif src == 'estimate':
            if self.src_prev != 'estimate':
                self.hist = []
            if self.in_f is None:
                self.in_f = R
            else:
                self.in_f += dt_s / (EST_TAU_S + dt_s) * (R - self.in_f)
        else:
            self.hist = []
        self.src_prev = src

    def _rate(self, now, window_s):
        r = ls_slope_h(self.hist, now, window_s, 900, RATE_MIN_POINTS)
        return 0.0 if r is None else clamp(r, -RATE_MAX * self.s, RATE_MAX * self.s)

    def _solar(self, inp, R, now, dt_s, windy, raining):
        """ excess = window - R_used - base_w; solar_f rises at once and decays with solar_decay_s. """
        w_ok = inp.window is not None and (inp.window_age_s is None or inp.window_age_s <= WINDOW_STALE_S)
        excess, raw = None, 0.0
        if w_ok and R is not None and inp.out_temp is not None:
            base = self.base_w.value(inp.out_temp, windy, raining)
            excess = inp.window - R - base
            self.exc_hist = [(t, v) for t, v in self.exc_hist if (now - t).total_seconds() <= SOLAR_DERIV_S]
            self.exc_hist.append((now, excess))
            deriv = ls_slope_h(self.exc_hist, now, SOLAR_DERIV_S, 1200) or 0.0
            raw = max(0.0, excess - self.t.sun_deadband * self.s) + self.t.solar_lead_h * max(0.0, deriv)
        else:
            self.exc_hist = []
        self.solar_f = max(raw, self.solar_f * math.exp(-dt_s / self.t.solar_decay_s))
        return excess, w_ok

    def _learn(self, inp, now, src, w_ok, windy, raining, rate60):
        """ Window baseline and internal bias, each from the PRIMARY sensor only, in normal operation. """
        if src != 'primary' or inp.windows_open or inp.vacation or inp.power_state != 'normal':
            return
        if inp.since_window_closed_s is not None and inp.since_window_closed_s < WINDOW_CLOSED_S:
            return

        def due(last):
            return last is None or (now - last).total_seconds() >= SAMPLE_MIN_S
        if w_ok and inp.out_temp is not None and self._quiet(inp, now) and due(self.last_gap_sample):
            self.base_w.sample(inp.out_temp, inp.window - inp.room, windy, raining)
            self.last_gap_sample = now
        settled = (rate60 is not None and abs(rate60) <= SETTLED_RATE * self.s and self.sp_since is not None
                   and (now - self.sp_since).total_seconds() >= SETTLED_SP_S[self.cfg.kind])
        if (inp.internal is not None and inp.hvac_mode == 'heat' and self.out_slow is not None and settled
                and due(self.last_bias_sample)):
            self.bias.sample(self.out_slow, inp.internal - inp.room, windy, raining)
            self.last_bias_sample = now

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
        t = base + (sign * SHIFT_DAY * s if inp.increasing else 0.0)
        if inp.saving:
            t -= sign * SHIFT_DAY * s
        elif windy or raining:
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
        if self.ff_ok_prev is not None and ok != self.ff_ok_prev:
            self.i_term += self.ff_prev - ff
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

    def _send(self, d, inp, now, sp, bypass = False, step_limit = True, jump_to = None):
        """ Output stage. Normal: only when |sp - setpoint| >= SEND_HYST, at most one change per MIN_CHANGE_S,
            at most STEP per change. bypass (overrides, power-state edges, mode switches): round_half(sp) now.
            jump_to (RETURN_JUMP): an upward change may skip the STEP limit, up to jump_to. """
        cur = inp.setpoint
        want = round_half(sp)
        new = None
        if cur is None or bypass:
            new = want
        elif abs(sp - cur) >= self.t.send_hyst * self.s and (
                self.last_change is None or (now - self.last_change).total_seconds() >= MIN_CHANGE_S - 1):
            if not step_limit:
                new = want
            elif jump_to is not None and want > cur + STEP:
                new = max(round_half(cur + STEP), min(want, round_half(jump_to)))
            else:
                new = round_half(cur + clamp(want - cur, -STEP, STEP))
        if new is not None and new != cur:
            d.temperature = new
            self.last_commanded = new
            self.last_change = now
            self.sp_since = now

    def _mode(self, d, now, mode, forced = False):
        d.hvac_mode = mode
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
        self._track(inp, now, dt_s)
        self.vac_end = self.vac_prev is True and not inp.vacation
        self.vac_prev = bool(inp.vacation)
        windy = inp.since_windy_s is not None and inp.since_windy_s < WINDY_S
        raining = bool(inp.raining)
        R, src = self._room(d, inp, windy, raining)
        self._indoor(inp, R, src, now, dt_s)
        real = src in ('primary', 'backup')
        rate = self._rate(now, self.t.rate_window_s) if real else 0.0
        rate60 = ls_slope_h(self.hist, now, 3600, 1800) if real else None
        base = self.target_base(inp, now)
        excess, w_ok = self._solar(inp, R, now, dt_s, windy, raining)
        self._learn(inp, now, src, w_ok, windy, raining, rate60)
        solar = min(self.solar_max, cfg.solar_gain * self.solar_f)
        d.diag = dict(src = src, R = R, in_f = self.in_f, rate = rate, excess = excess, solar = solar,
                      out_slow = self.out_slow, out_day = self.out_day,
                      base_w = self.base_w.value(inp.out_temp, windy, raining) if inp.out_temp is not None else None,
                      bias_int = self.bias.value(self.out_slow, windy, raining) if self.out_slow is not None else None)
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

    def _pi(self, tgt, ff, solar, rate, base, dt_s, integrate, attr = 'i_term'):
        """ sp_ctrl = clamp(tgt + ff + i + p - solar). The integral (attr) runs only when allowed, outside the
            D1 deadband, and not further into a clamp (anti-windup). """
        s = self.s
        lo, hi = self.limits(base, 'cool' if attr == 'i_cool' else 'heat')
        if self.in_f is None:
            return clamp(tgt + ff + getattr(self, attr) - solar, lo, hi), 0.0
        p = self.t.kp * (tgt - (self.in_f + rate * self.t.lead_h))
        e = tgt - self.in_f
        ei = tgt - (self.in_f + rate * self.t.lead_h) if self.t.i_pred else e
        if (integrate and dt_s <= 3 * max(MIN_CHANGE_S, self.cfg.interval_s) and abs(e) <= self.t.i_band * s
                and (ei > DB_BELOW * s or ei < -DB_ABOVE * s)):
            raw = tgt + ff + getattr(self, attr) + p - solar
            if not ((raw >= hi and ei > 0) or (raw <= lo and ei < 0)):
                setattr(self, attr, clamp(getattr(self, attr) + self.t.ki_h * ei * dt_s / 3600, -I_CLAMP * s, I_CLAMP * s))
        return clamp(tgt + ff + getattr(self, attr) + p - solar, lo, hi), p

    def _bumpless(self, d, inp, tgt, ff, solar, rate, attr = 'i_term', mode = 'heat', first = False):
        """ At start, after a restart and after a manual change: the integral is set so that sp_ctrl equals the
            current setpoint (without a power offset that is in it), so nothing jumps (S9). """
        if inp.setpoint is not None and not inp.windows_open and not inp.vacation:
            if first and self.in_f is not None and abs(tgt - self.in_f) > self.t.i_band * self.s:
                # Start (or restart) with the room far from target: the integral is NOT set to hold the current
                # setpoint (that would leave it stuck while the room does not move). It starts at 0 and the output
                # stage walks the setpoint towards sp_ctrl one 0.5 step per 12 min, so nothing jumps.
                setattr(self, attr, 0.0)
                self.last_commanded = inp.setpoint
                self.started = True
                return
            ref = inp.setpoint
            if self.pw_state != 'normal' and self.pw_base is not None:
                ref = self.pw_base
            p = self.t.kp * (tgt - (self.in_f + rate * self.t.lead_h)) if self.in_f is not None else 0.0
            setattr(self, attr, clamp(ref - (tgt + ff + p - solar), -I_CLAMP * self.s, I_CLAMP * self.s))
            self.last_commanded = inp.setpoint
        self.started = True

    def _manual(self, d, inp, mode = 'heat'):
        """ A setpoint that is not the last one sent was changed by hand (or by another app): follow it. """
        if (not self.started or inp.setpoint is None or self.last_commanded is None
                or inp.setpoint == self.last_commanded or inp.windows_open or inp.vacation):
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
        ff = self._ff(base, windy, raining)
        first = not self.started
        if first or self._manual(d, inp):
            self._bumpless(d, inp, tgt, ff, solar, rate, first = first)
            self.cold_start = None
        freeze = self._freeze(inp, src, tgt)
        if freeze is None and self.t.sun_freeze > 0 and solar >= self.t.sun_freeze * s:
            freeze = 'sun'
        if freeze is None and self.cold_start is not None:
            freeze = 'cold start'
        sp_ctrl, p = self._pi(tgt, ff, solar, rate, base, dt_s, freeze is None)
        lo, hi = self.limits(base)
        d.diag.update(mode = 'heat', target_eff = tgt, ff = ff, i = self.i_term, p = p, sp_ctrl = sp_ctrl, frozen = freeze)
        vac = cfg.vacation_temp if cfg.vacation_temp is not None else 10.0
        if inp.windows_open or inp.vacation:
            self._clear_pw()
            self.pre_lower = None
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
                if (self.in_f > tgt + FAN_ABOVE * s + extra and (cfg.has_fireplace or sp_ctrl <= lo + 1e-9 or rate >= FAN_RISE * s)
                        and not held and (inp.since_windy_s is None or inp.since_windy_s > FAN_WIND_S)
                        and not inp.after_increase and self._mode_time_ok(now)):
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
        back = None
        if cfg.kind == 'hvac' and self.pre_lower is not None and self.in_f is not None and self.in_f <= tgt:
            back = min(self.pre_lower, base + RETURN_JUMP * s)
        self._send(d, inp, now, sp_ctrl, bypass = edge, jump_to = back)
        cur, sent = inp.setpoint, d.temperature
        if cfg.kind != 'hvac' or cur is None:
            return
        if self.pre_lower is not None and (cur >= self.pre_lower or (back is not None and sent is not None and sent > cur)):
            self.pre_lower = None                   # the way back is done
        elif (self.pre_lower is None and sent is not None and sent < cur and self.in_f is not None
              and self.in_f > tgt + FAN_ABOVE * s):
            self.pre_lower = cur                    # lowered for an overshoot instead of fan_only

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
        self.cold_start, self.cold_sp, self.pre_lower = now, start, None
        if inp.power_state != 'normal':
            self.pw_base = start
        d.logs.append(('INFO', "vacation ended with the room %.1f below target: cold start at %s until the room is near "
                       "the target." % (deficit, start)))
        self._send(d, inp, now, start, bypass = True)
        return True

    def _fan_only(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        cfg, s = self.cfg, self.s
        tgt = self.target_eff(base, inp, windy, raining, 'heat')
        ff = self._ff(base, windy, raining)
        sp_ctrl, p = self._pi(tgt, ff, solar, rate, base, dt_s, False)
        d.diag.update(mode = 'fan_only', target_eff = tgt, ff = ff, i = self.i_term, p = p, sp_ctrl = sp_ctrl)
        self._clear_pw()
        self.cold_start = None
        real = src in ('primary', 'backup')
        if inp.windows_open or not real:
            return    # no mode change on an estimate (addendum 1.4)
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
                self._send(d, inp, now, sp_ctrl - STEP, bypass = True)   # replaces D5
            self.started = True

    def _cool(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        cfg, s = self.cfg, self.s
        tgt = self.target_eff(base, inp, windy, raining, 'cool')
        real = src in ('primary', 'backup')
        self.pre_lower = None
        self.cold_start = None
        if inp.windows_open:
            self._clear_pw()
            self._mode(d, now, 'fan_only', forced = True)
            return
        if real and self.in_f is not None:
            to_fan = (inp.daytime is False and inp.rain > 0 and self.in_f < tgt + 2 * s) or self.in_f < tgt + 1 * s
            vac_fan = inp.vacation and self.in_f < cfg.max_vacation_temp - 2 * s
            if vac_fan or (to_fan and self._mode_time_ok(now)):
                self._clear_pw()
                self._mode(d, now, 'fan_only', forced = vac_fan)
                return
        first = not self.started
        if first or self._manual(d, inp, 'cool'):
            self._bumpless(d, inp, tgt, 0.0, solar, rate, 'i_cool', 'cool', first = first)
        freeze = self._freeze(inp, src, tgt)
        # cooling: no feed-forward, the sun term adds cooling (lower setpoint), never learned (E2)
        sp_ctrl, p = self._pi(tgt, 0.0, solar, rate, base, dt_s, freeze is None, 'i_cool')
        d.diag.update(mode = 'cool', target_eff = tgt, i = self.i_cool, p = p, sp_ctrl = sp_ctrl, frozen = freeze)
        active, edge = self._power_layer(d, inp, tgt, 'cool')
        if active:
            self._send(d, inp, now, self.pw_base + self.power_offset(inp.power_state, 'cool'), bypass = True)
        else:
            self._send(d, inp, now, sp_ctrl, bypass = edge)

    def _table(self, d, inp, now, dt_s, R, src, base, solar, rate, windy, raining):
        """ Heater table mode with the adaptive trim (addendum 4, plan 3.9): table base + rain/wind 1 -
            min(3, sun + room above target) + trim_i, then save / spend / vacation / min_temp as classic. The
            room term uses R_used (an estimate counts); without any room value it is dropped (C19). """
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
