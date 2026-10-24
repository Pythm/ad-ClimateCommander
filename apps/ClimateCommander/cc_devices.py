""" ClimateCommander devices: heaters, HVAC (heat pumps / AC) and screens.

    One device controls one climate entity. The app (climateCommander.py) creates the devices,
    owns the outdoor weather values and the JSON store, and hands both to every device.

    Two ways to find the setpoint (target_mode):
      'indoor' : keep a fixed indoor temperature (target_indoor_temp / target_indoor_input). The
                 setpoint is moved step by step after the indoor sensor, and the setpoint that held
                 the target is learned per outdoor temperature and lux (JSON store). The setpoint is
                 kept while the room trend (sensor reports of the last hour) reaches the target within
                 trend_hold_hours; a learned setpoint is approached one step per run. A normal move is at most
                 one step (0.5) per run. After a vacation a cold room gets a higher start setpoint (cold start)
                 until it is near the target.
      'table'  : setpoint = target_heater_temp (or target_heater_input) + the `offset` (or the
                 absolute `normal`) from the `temperatures` table for the outdoor temperature,
                 corrected for rain, wind, a window temperature sensor and an indoor sensor above
                 target. This is the heater logic from ElectricalManagement.

    Power states (set by ElectricalManagement through set_power_state, see README):
      'normal', 'save' (high price), 'spend' (low price / solar), 'reduce' (over the kWh limit).

    controller (per device, default 'classic'): 'shadow' and 'adaptive' use the indoor controller in
    cc_control.py instead of the step rules and learned setpoints (indoor mode, and table mode for heaters).
    'shadow' keeps classic in control and only computes and logs what adaptive would do.

    @Pythm / https://github.com/Pythm
"""

from __future__ import annotations

import bisect
import datetime
import math

from cc_control import ControlConfig, IndoorController, Inputs, ls_slope_h

UNAVAIL = ('unavailable', 'unknown')
POWER_STATES = ('normal', 'save', 'spend', 'reduce')
DEFAULT_POWER_STATE_MINUTES = 45   # a power state without a refresh falls back to 'normal' after this
STALE_SENSOR = datetime.timedelta(hours = 1)
STABLE_CORRECTION_MIN_DIFF = 0.3   # indoor mode: correct a steady room that is this far off target
STABLE_CORRECTION_AFTER = datetime.timedelta(minutes = 30)
CONTROL_WRITE_EVERY = datetime.timedelta(minutes = 30)   # shadow / adaptive: JSON written at most this often (and on power-state changes)
STEP = 0.5                         # one setpoint step (climate entities take 0.5 steps)
TREND_WINDOW = datetime.timedelta(minutes = 60)   # room trend: least squares over the reports of the last hour ...
TREND_MIN_POINTS = 3                               # ... with at least this many reports ...
TREND_MIN_SPAN = datetime.timedelta(minutes = 20)  # ... spanning at least this (else the trend is unknown, 0)
TREND_KEEP = datetime.timedelta(hours = 3)         # room reports kept this long
FAN_RISE = 0.2                     # (C per hour) HVAC without has_fireplace: heat -> fan_only at target + 0.6 only while the room still rises this fast
RETURN_ABOVE_TARGET = 4            # HVAC: the fast way back after lowering for an overshoot goes at most this far above target
LEARN_AFTER_MODE_CHANGE = datetime.timedelta(hours = 2)   # HVAC: no setpoint learned this long after a mode change
COLD_START_MAX = 6.0               # vacation end, indoor mode: the cold start setpoint is at most this above target (x unit_scale) ...
COLD_BACK_WITHIN = 1.0             # ... and is held until the room is this close to target (x unit_scale) ...
COLD_START_MAX_TIME = datetime.timedelta(hours = 8)   # ... or this long after the vacation end
COLD_GOAL_ABOVE = 1.0              # way down without a held or learned setpoint: target + this (x unit_scale)
DIAG_KEYS = ('src', 'R', 'in_f', 'rate', 'out_slow', 'out_day', 'excess', 'base_w', 'solar', 'bias_int',
             'target_eff', 'ff', 'i', 'p', 'trim', 'room_term', 'sp_ctrl')


def round_half(value: float) -> float:
    """ Climate entities take 0.5 steps. """
    return round(value * 2, 0) / 2


def to_float(value):
    """ float(value), or None for unavailable/unknown/None/text. """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Heater:
    """ Controls one climate entity that only heats (floor heating, panel heater, heat pump in heat). """

    hvac:bool = False

    def __init__(self, app, cfg:dict):
        self.app = app
        self.ADapi = app.ADapi
        self.weather = app.weather
        self.store = app.store
        self.heater:str = cfg['climate']
        self.namespace:str = cfg['namespace']
        self.target_mode:str = cfg['target_mode']
        self.interval:int = cfg['interval']
        self.controller:str = 'classic'
        self.ctrl = None                   # cc_control.IndoorController with controller shadow / adaptive
        self.unit_scale:float = 1.8 if cfg.get('unit') == 'F' else 1.0   # C-based temperature differences x this in F
        now = self.ADapi.datetime(aware=True)

            # Sensors
        self.indoor_sensor_temp = cfg.get('indoor_sensor_temp')
        self.backup_indoor_sensor_temp = cfg.get('backup_indoor_sensor_temp')
        self.window_temp = cfg.get('window_temp')
        self.window_offset:float = cfg['window_offset']
        self.rain_level:float = cfg['rain_level']
        self.anemometer_speed = cfg['anemometer_speed']
        self.last_windy_time = now - datetime.timedelta(hours = 2)

            # Indoor target
        self.target_indoor_temp:float = cfg['target_indoor_temp']
        self.target_indoor_input = cfg.get('target_indoor_input')
        if self.target_indoor_input is not None:
            self.ADapi.listen_state(self._updateTarget, self.target_indoor_input,
                namespace = self.namespace
            )
            value = to_float(self.ADapi.get_state(self.target_indoor_input, namespace = self.namespace))
            if value is None:
                self.ADapi.log(
                    f"{self.target_indoor_input} is not a valid number. Using {self.target_indoor_temp}.",
                    level = 'INFO'
                )
            else:
                self.target_indoor_temp = value

            # Heater target and table (target_mode 'table')
        self.temperatures:list = cfg.get('temperatures') or []
        self.target_heater_temp:float = cfg['target_heater_temp']
        self.target_heater_input = cfg.get('target_heater_input')
        if self.target_mode == 'table' and self.target_heater_input is not None:
            self.ADapi.listen_state(self._updateHeaterTarget, self.target_heater_input,
                namespace = self.namespace
            )
            value = to_float(self.ADapi.get_state(self.target_heater_input, namespace = self.namespace))
            if value is not None:
                self.target_heater_temp = value
        self.save_temp = cfg.get('save_temp')
        self.save_temp_offset = cfg.get('save_temp_offset')
        self.spend_temp_offset:float = cfg['spend_temp_offset']
        self.min_temp = to_float(self.ADapi.get_state(self.heater, attribute = 'min_temp', namespace = self.namespace))
        if self.min_temp is None:
            self.min_temp = 5

        try:
            self.vacation_temp = float(cfg['vacation_temp']) if cfg.get('vacation_temp') is not None else None
        except (ValueError, TypeError):
            self.vacation_temp = 10
            self.ADapi.log(f"{self.heater}: error setting vacation temperature. Set to 10 degrees.", level = 'INFO')
        if self.vacation_temp is None and (self.target_mode == 'indoor' or self.hvac):
            # A heater table may leave it out (the table has its own floor); the HVAC code adds to it
            self.vacation_temp = 10
        self.vacation_keep_off:bool = cfg['vacation_keep_off']
        self.off_retry_count:int = 3
        self.cold_start_deficit:float = cfg.get('cold_start_deficit') or 2.0 * self.unit_scale
        self._vacation_ended:bool = False  # vacation went from on to off: the next run starts the heating (indoor mode)
        self._cold_start:bool = False      # the cold start setpoint after a vacation is held (memory only)
        self._cold_start_since = None
        self._heat_setpoint_before_vacation = None
        self._window_closed:bool = False   # heater: all windows closed, the next normal move is not limited to one step

        self.daytime_savings:list = cfg.get('daytime_savings') or []
        self.daytime_increasing:list = cfg.get('daytime_increasing') or []
        self.automate = cfg.get('automate')

            # Indoor temperature trend
        self.trend_hold_hours:float = cfg.get('trend_hold_hours', 2.0)
        self._room_hist:dict = {'primary': [], 'backup': []}   # (report time, value) of distinct reports per sensor
        self._in_source:str = 'primary'    # the sensor get_in_temp used last
        for source, entity in (('primary', self.indoor_sensor_temp), ('backup', self.backup_indoor_sensor_temp)):
            if entity:
                self.ADapi.listen_state(self._room_reported, entity,
                    namespace = self.namespace,
                    source = source
                )
        self.prev_in_temp:float = 0.0
        in_temp = to_float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace)) if self.indoor_sensor_temp else None
        if in_temp is None and self.backup_indoor_sensor_temp is not None:
            in_temp = to_float(self.ADapi.get_state(self.backup_indoor_sensor_temp, namespace = self.namespace))
            if in_temp is None:
                in_temp = self.target_indoor_temp - 0.1
        if in_temp is not None:
            self.prev_in_temp = in_temp

        self.heater_temp_last_changed = now
        self.heater_temp_last_registered = now

            # Power state from ElectricalManagement
        self._power_state:str = 'normal'
        self._power_until = None
        self._power_source = None
        self._entity_problem = None        # 'missing' / 'unavailable' while the climate entity has a problem (logged once)
        self._applied_offset:float = 0.0   # offset that is in the climate's setpoint right now (indoor mode)
        self._base_setpoint = None         # setpoint without the offset while a power state is active
        self._last_commanded = None        # last temperature this device sent

            # Windows
        self.windowsensors:list = cfg.get('windowsensors') or []
        self.windows_is_open:bool = False
        self.window_last_closed = now - datetime.timedelta(hours = 2)
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                self.windows_is_open = True
            self.ADapi.listen_state(self._windowOpened, window,
                new = 'on',
                duration = 120,
                namespace = self.namespace
            )
            self.ADapi.listen_state(self._windowClosed, window,
                new = 'off',
                namespace = self.namespace
            )
            last_changed = self.ADapi.get_state(window, attribute = 'last_changed', namespace = self.namespace)
            if not last_changed:
                self.ADapi.log(f"{self.heater}: window sensor {window} has no last_changed. Does it exist?", level = 'WARNING')
                continue
            last_changed = self.ADapi.convert_utc(last_changed)
            if self.window_last_closed < last_changed:
                self.window_last_closed = last_changed

            # Notifications: once per window event (cold after opening, hot after closing)
        self.getting_cold:float = cfg['getting_cold']
        self.getting_hot_offset:float = cfg['getting_hot_offset']
        self.notify_cold_armed:bool = True
        self.notify_hot_armed:bool = False
        self.notify_app = app.notify_app
        self.recipients = cfg.get('notify_receiver')

            # Screens / covers
        self.screening:list = []
        for s in cfg.get('screening') or []:
            if not s.get('screen'):
                self.ADapi.log(f"{self.heater}: screening entry without 'screen' is skipped: {s}", level = 'WARNING')
                continue
            self.screening.append(Screen(app,
                screen = s['screen'],
                windowsensors = s.get('windowsensors', []),
                lux_close = s.get('lux_close', 40000),
                lux_open = s.get('lux_open', 15000),
                lux_open_when_media_is_on = s.get('lux_open_media', 4000),
                anemometer_speed_limit = self.anemometer_speed,
                cloud_cover_limit = cfg['cloud_cover'],
                not_when_home = s.get('not_when_home', []),
                mediaplayers = s.get('mediaplayers', []),
                namespace = self.namespace
            ))
        self.screening_temp:float = cfg['screening_temp']

            # Vacation
        self.vacation_entity = cfg.get('vacation')
        if self.vacation_entity is None:
            self.vacation_state:bool = False
        else:
            self.vacation_state = self.ADapi.get_state(self.vacation_entity, namespace = self.namespace) == 'on'
            self.ADapi.listen_state(self._vacationStateListen, self.vacation_entity,
                namespace = self.namespace
            )

        if not self.hvac:
            self._setup_controller(cfg)   # Aircondition does it at the end of its own __init__

            # Periodic run. Spread over the interval so many devices do not all call HA in the same second.
        self.ADapi.run_every(self._set_indoortemp, f"now+{cfg['start_delay']}", self.interval)

    # ------------------------------------------------------------------ #
    # Power state API for ElectricalManagement (may be called from another app's thread:
    # only store the request and let this app's own thread do the work)
    # ------------------------------------------------------------------ #

    def set_power_state(self, state:str, valid_minutes:float = DEFAULT_POWER_STATE_MINUTES, source = None) -> None:
        """ Sets 'normal', 'save', 'spend' or 'reduce'. Without a new call within valid_minutes the
            device returns to 'normal' by itself, so a stopped ElectricalManagement cannot leave it saving. """
        if state not in POWER_STATES:
            self.ADapi.log(f"{self.heater}: unknown power state '{state}' from {source}.", level = 'WARNING')
            return
        changed = state != self.power_state
        self._power_until = self.ADapi.datetime(aware=True) + datetime.timedelta(minutes = valid_minutes)
        self._power_state = state
        self._power_source = source
        if changed:
            self.ADapi.run_in(self._set_indoortemp, 0)

    @property
    def power_state(self) -> str:
        if (
            self._power_state != 'normal'
            and self._power_until is not None
            and self.ADapi.datetime(aware=True) > self._power_until
        ):
            self.ADapi.log(f"{self.heater}: power state '{self._power_state}' expired, back to normal.", level = 'INFO')
            self._power_state = 'normal'
        return self._power_state

    def in_daytime_saving(self) -> bool:
        return self.DaytimeSaving()

    def _power_offset(self, mode:str = 'heat') -> float:
        """ Indoor mode: degrees added to the setpoint for the current power state. Cooling turns the sign. """
        state = self.power_state
        if state == 'normal':
            return 0.0
        sign = -1 if mode == 'cool' else 1
        if state in ('save', 'reduce'):
            return sign * (self.save_temp_offset if self.save_temp_offset is not None else -0.3)
        return sign * self.spend_temp_offset

    # ------------------------------------------------------------------ #
    # Listeners
    # ------------------------------------------------------------------ #

    def _vacationStateListen(self, entity, attribute, old, new, **kwargs) -> None:
        if new == 'on' and not self.vacation_state:
            self._heat_setpoint_before_vacation = self._held_heat_setpoint()
        self.vacation_state = new == 'on'
        self.off_retry_count = 3
        if new == 'on':
            self._end_cold_start()
        elif old == 'on' and new == 'off':
            self._note_vacation_end()
        if (
            self.target_mode == 'table'
            and new == 'off'
            and self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off'
        ):
            # Same as ElectricalManagement: a heater kept off on vacation is turned on again.
            self.ADapi.call_service('climate/set_hvac_mode',
                entity_id = self.heater,
                hvac_mode = 'heat',
                namespace = self.namespace
            )
        self.ADapi.run_in(self._set_indoortemp, 5)

    def _room_reported(self, entity, attribute, old, new, **kwargs) -> None:
        self._note_room(kwargs.get('source', 'primary'), entity)

    def _updateTarget(self, entity, attribute, old, new, **kwargs) -> None:
        value = to_float(new)
        if value is None:
            return
        self.target_indoor_temp = value
        self.ADapi.run_in(self._set_indoortemp, 5)

    def _updateHeaterTarget(self, entity, attribute, old, new, **kwargs) -> None:
        value = to_float(new)
        if value is None:
            return
        self.target_heater_temp = value
        self.ADapi.run_in(self._set_indoortemp, 5)

    def _windowOpened(self, entity, attribute, old, new, **kwargs) -> None:
        if self.windowsopened() != 0:
            if not self.windows_is_open:
                self.notify_cold_armed = True
            self.windows_is_open = True
            self._end_cold_start()
            self.ADapi.run_in(self._set_indoortemp, 1)

    def _windowClosed(self, entity, attribute, old, new, **kwargs) -> None:
        if self.windowsopened() == 0:
            if self.windows_is_open:
                self.notify_hot_armed = True
                self._window_closed = not self.hvac   # an HVAC returns through fan_only -> heat (restart setpoint)
            self.window_last_closed = self.ADapi.datetime(aware=True)
            self.windows_is_open = False
            self.ADapi.run_in(self._set_indoortemp, 60)

    def windowsopened(self) -> int:
        """ Returns number of opened windows """
        opened:int = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

    def tryScreenOpen(self) -> None:
        """ Tries to open screens on weather updates from rain and lux sensors """
        for s in self.screening:
            s.check_if_try_screen_open()

    # ------------------------------------------------------------------ #
    # Reading sensors
    # ------------------------------------------------------------------ #

    def _sensor_stamp(self, entity):
        """ When the sensor last reported, or None. last_reported changes on every report, also when
            the value is the same; last_updated only when it changes. """
        stamp = self.ADapi.get_state(entity, attribute = 'last_reported', namespace = self.namespace)
        if not stamp:
            stamp = self.ADapi.get_state(entity, attribute = 'last_updated', namespace = self.namespace)
        if not stamp:
            return None
        try:
            return self.ADapi.convert_utc(stamp)
        except (TypeError, ValueError):
            return None

    def _sensor_age(self, entity):
        """ Time since the sensor last reported. """
        stamp = self._sensor_stamp(entity)
        if stamp is None:
            return datetime.timedelta(0)
        return self.ADapi.datetime(aware=True) - stamp

    def _note_room(self, source:str, entity) -> None:
        """ Adds the sensor's present report to the trend history when its report time is new. """
        value = to_float(self.ADapi.get_state(entity, namespace = self.namespace))
        if value is None:
            return
        now = self.ADapi.datetime(aware=True)
        stamp = self._sensor_stamp(entity) or now
        hist = self._room_hist[source]
        if hist and stamp <= hist[-1][0]:
            return
        hist.append((stamp, value))
        self._room_hist[source] = [(t, v) for t, v in hist if now - t <= TREND_KEEP]

    def _record_room(self) -> None:
        """ Every run, in every mode (also fan_only): a report with an unchanged value fires no state event. """
        if self.indoor_sensor_temp:
            self._note_room('primary', self.indoor_sensor_temp)
        if self.backup_indoor_sensor_temp:
            self._note_room('backup', self.backup_indoor_sensor_temp)

    def _room_rate(self) -> float:
        """ Room trend in degrees (of the sensor) per hour from the reports of the sensor in use, 0 when unknown
            (fewer than TREND_MIN_POINTS reports spanning TREND_MIN_SPAN within TREND_WINDOW). """
        rate = ls_slope_h(self._room_hist.get(self._in_source, []), self.ADapi.datetime(aware=True),
                          TREND_WINDOW.total_seconds(), TREND_MIN_SPAN.total_seconds(), TREND_MIN_POINTS)
        return 0.0 if rate is None else rate

    def _trend_rule(self, in_temp:float) -> tuple[bool, bool]:
        """ Returns (hold, not_valid). hold: the room moves towards target and reaches it within trend_hold_hours
            at its present rate, so the setpoint is kept. not_valid: the room moves towards target too slowly (or at
            an unknown rate), or away from it: the learned setpoint is not forced. The direction comes from the
            time-based trend; while that is unknown, from the change since the last run. """
        target = self.target_indoor_temp
        rate = self._room_rate()
        direction = rate if rate != 0 else in_temp - self.prev_in_temp
        if (in_temp < target and direction > 0) or (in_temp > target and direction < 0):
            if rate != 0 and abs(target - in_temp) / abs(rate) <= self.trend_hold_hours:
                return True, False
            return False, True
        if rate != 0 and in_temp != target:
            return False, True
        return False, False

    def get_in_temp(self):
        """ Indoor temperature from the indoor sensor. Uses the backup sensor when the indoor sensor
            is unavailable or has not reported for an hour. None when there is no usable value. """
        in_temp = None
        self._in_source = 'primary'
        if self.indoor_sensor_temp is not None:
            in_temp = to_float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
            stale = in_temp is not None and self._sensor_age(self.indoor_sensor_temp) > STALE_SENSOR
            if in_temp is not None and not stale:
                return in_temp
        if self.backup_indoor_sensor_temp is not None:
            backup = to_float(self.ADapi.get_state(self.backup_indoor_sensor_temp, namespace = self.namespace))
            if backup is not None:
                self.ADapi.log(f"{self.heater}: using backup indoor temp {backup}", level = 'DEBUG')
                self._in_source = 'backup'
                return backup
        return in_temp   # a stale value is better than nothing when there is no usable backup sensor

    def get_heater_temp(self):
        """ Returns set heater temperature for the climate entity. """
        return to_float(self.ADapi.get_state(self.heater, attribute = 'temperature', namespace = self.namespace))

    def friendly_name(self) -> str:
        return self.ADapi.get_state(self.heater, attribute = 'friendly_name', namespace = self.namespace) or self.heater

    def _automation_off(self) -> bool:
        return bool(self.automate) and self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off'

    def DaytimeIncreasing(self) -> tuple[bool, bool]:
        """ Checks if it is time during day to increase temperature.
            Also returns a boolean if is has been increased to prevent setting fan_only or cooling after increasing periode. """
        doDaytimeIncreasing = False
        afterDaytimeIncrease = False
        now = self.ADapi.datetime()
        for daytime in self.daytime_increasing:
            if 'start' in daytime and 'stop' in daytime:
                if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                    doDaytimeIncreasing = True
                    if 'presence' in daytime:
                        doDaytimeIncreasing = False
                        for presence in daytime['presence']:
                            if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                doDaytimeIncreasing = True
                end_time_increase = self.ADapi.parse_datetime(daytime['stop'])

                if now > end_time_increase + datetime.timedelta(hours = 1):
                    end_time_increase += datetime.timedelta(days = 1)

                if end_time_increase < now < end_time_increase + datetime.timedelta(hours = 1):
                    afterDaytimeIncrease = True

        return doDaytimeIncreasing, afterDaytimeIncrease

    def DaytimeSaving(self) -> bool:
        doDaytimeSaving = False
        for daytime in self.daytime_savings:
            if 'start' in daytime and 'stop' in daytime:
                if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                    doDaytimeSaving = True
                    if 'presence' in daytime:
                        for presence in daytime['presence']:
                            if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                doDaytimeSaving = False

            elif 'presence' in daytime:
                doDaytimeSaving = True
                for presence in daytime['presence']:
                    if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                        doDaytimeSaving = False

        return doDaytimeSaving

    # ------------------------------------------------------------------ #
    # Notifications
    # ------------------------------------------------------------------ #

    def _window_notifications(self, in_temp) -> None:
        """ Once per window event: 'getting cold' after windows opened, 'getting hot' after all closed. """
        if in_temp is None or self.notify_app is None:
            return
        out_temp = self.weather.out_temp
        if (
            self.windows_is_open
            and self.notify_cold_armed
            and out_temp < self.getting_cold
            and in_temp < self.getting_cold
        ):
            self.notify_app.send_notification(
                message = f"Window near {self.friendly_name()} is open and inside temperature is {in_temp}°",
                message_title = "Window open",
                message_recipient = self.recipients,
                also_if_not_home = False
            )
            self.notify_cold_armed = False
        elif (
            not self.windows_is_open
            and self.notify_hot_armed
            and in_temp >= self.target_indoor_temp + self.getting_hot_offset
            and out_temp > self.getting_cold
        ):
            self.notify_app.send_notification(
                message = f"No Window near {self.friendly_name()} is open and it is getting hot inside! {in_temp}°",
                message_title = "Window closed",
                message_recipient = self.recipients,
                also_if_not_home = False
            )
            self.notify_hot_armed = False

    # ------------------------------------------------------------------ #
    # Main cycle
    # ------------------------------------------------------------------ #

    def _note_entity_state(self) -> None:
        """ Logs once per problem (not every run) when the climate entity is missing or unavailable, and once when it is back. """
        state = self.ADapi.get_state(self.heater, namespace = self.namespace)
        problem = 'missing' if state is None else ('unavailable' if state in UNAVAIL else None)
        if problem == self._entity_problem:
            return
        previous, self._entity_problem = self._entity_problem, problem
        if problem == 'missing':
            self.ADapi.log(f"{self.heater}: the entity does not exist in namespace '{self.namespace}'. Nothing is controlled until it exists. Check 'climate'.", level = 'WARNING')
        elif problem == 'unavailable':
            self.ADapi.log(f"{self.heater}: the entity is {state}. Waiting for it to come back.", level = 'INFO')
        elif previous is not None:
            self.ADapi.log(f"{self.heater}: the entity is back ({state}).", level = 'INFO')

    def _set_indoortemp(self, **kwargs) -> None:
        if self.ctrl is not None and 'classic_only' not in kwargs:
            self._controller_cycle()
            return
        if self._automation_off():
            return
        self._note_entity_state()
        if self.target_mode == 'table':
            self._table_cycle()
            return

        in_temp = self.get_in_temp()
        if in_temp is None:
            return
        self._record_room()
        heater_temp = self.get_heater_temp()
        if heater_temp is None:
            return
        self._window_notifications(in_temp)

        shifted = False   # target moved by daytime increasing/saving: do not learn the setpoint
        doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
        if doDaytimeIncreasing:
            in_temp -= 0.5
            shifted = True

        if self.DaytimeSaving():
            in_temp += 0.5
            shifted = True
        else:
            # Correct indoor temp when high amount of wind
            if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            # Correct indoor temp when rain
            elif self.weather.rain >= self.rain_level:
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

        learn = False
        if self.windows_is_open:
            new_temperature = self.vacation_temp
            self._clear_power_offset()
            self._vacation_ended = False

        elif self.vacation_state:
            new_temperature = self.vacation_temp
            self._clear_power_offset()
            if in_temp > self.target_indoor_temp:
                if self.weather.out_temp > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()
        elif self._vacation_ended:
            new_temperature = self._after_vacation(in_temp)
        else:
            if self._cold_start and self.ADapi.get_state(self.heater, namespace = self.namespace) == 'off':
                self._end_cold_start()
            new_temperature = self._indoor_setpoint(heater_temp = heater_temp, in_temp = in_temp)
            learn = not shifted and self.power_state == 'normal'

                # Check if it is hot inside
            if (
                in_temp > self.target_indoor_temp + 0.2
                and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
            ):
                if self.weather.out_temp > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()

        self.updateClimateTemperature(heater_temp = heater_temp, new_temperature = new_temperature, learn = learn)

    def _clear_power_offset(self) -> None:
        self._applied_offset = 0.0
        self._base_setpoint = None

    def _indoor_setpoint(self, heater_temp:float, in_temp:float, mode:str = 'heat') -> float:
        """ Indoor mode setpoint. In 'normal' the setpoint follows the indoor sensor. In a power state
            the setpoint is held at the value from before the state plus the state's offset, so the
            regulation does not work against the saving and nothing is learned. """
        offset = self._power_offset(mode)
        cold = self._cold_start and mode == 'heat'
        if offset == 0:
            base = heater_temp
            ending = self._applied_offset != 0   # a power state just ended: the move is not limited to one step
            if self._applied_offset != 0 and self._base_setpoint is not None:
                if self._last_commanded is not None and heater_temp != self._last_commanded:
                    base = heater_temp - self._applied_offset   # changed by hand during the state
                else:
                    base = self._base_setpoint
            self._clear_power_offset()
            if cold:
                return self._cold_start_step(base, in_temp)
            new_temperature = self.adjust_set_temperature_by(heater_set_temp = base, in_temp = in_temp)
            if ending or self._window_closed or self.target_mode != 'indoor':
                self._window_closed = False
                return new_temperature
            return self._capped(heater_temp, new_temperature)

        if self._applied_offset == 0 or self._base_setpoint is None:
            self._base_setpoint = heater_temp
        elif self._last_commanded is not None and heater_temp != self._last_commanded:
            self._base_setpoint = heater_temp - self._applied_offset   # changed by hand during the state
        if cold:
            self._base_setpoint = self._cold_start_step(self._base_setpoint, in_temp)
        self._applied_offset = offset
        self.prev_in_temp = in_temp
        return self._base_setpoint + offset

    def _capped(self, current:float, new:float) -> float:
        """ Indoor mode: a normal setpoint move is at most STEP per run, on the 0.5 grid. """
        return round_half(current + max(-STEP, min(STEP, new - current)))

    # ------------------------------------------------------------------ #
    # After a vacation (indoor mode): cold start, way down
    # ------------------------------------------------------------------ #

    def _note_vacation_end(self) -> None:
        if self.target_mode == 'indoor':
            self._vacation_ended = True

    def _end_cold_start(self) -> None:
        self._cold_start = False
        self._cold_start_since = None

    def _held_heat_setpoint(self):
        """ The setpoint the heater holds now without a power-state offset (None with an open window or in table mode). """
        heater_temp = self.get_heater_temp()
        if self.target_mode != 'indoor' or self.windows_is_open or heater_temp is None:
            return None
        return heater_temp - self._applied_offset

    def _lowest_heat(self) -> float:
        """ Heater: the climate's min_temp (max_setpoint_drop is for HVAC). """
        return self.min_temp

    def _max_temp(self):
        return to_float(self.ADapi.get_state(self.heater, attribute = 'max_temp', namespace = self.namespace))

    def _learned_ceiling(self):
        """ The learned setpoint for the present weather, None without learned data. """
        if self.store is None or not self.store.device(self.heater).get('data'):
            return None
        return self._getHeatingTempFromPersisten()[0]

    def _after_vacation(self, in_temp:float) -> float:
        """ The first run after a vacation ended. Room at least cold_start_deficit below target: the cold start setpoint
            target + the deficit (at most COLD_START_MAX above target and the climate's max_temp), held by
            _cold_start_step. Else the restart setpoint as after fan_only: the setpoint held before the vacation, at
            most the learned setpoint (without learned data: the target) minus 1, not below _lowest_heat(). """
        self._vacation_ended = False
        deficit = self.target_indoor_temp - in_temp
        if deficit >= self.cold_start_deficit:
            start = self.target_indoor_temp + min(deficit, COLD_START_MAX * self.unit_scale)
            max_temp = self._max_temp()
            if max_temp is not None:
                start = min(start, max_temp)
            self._cold_start = True
            self._cold_start_since = self.ADapi.datetime(aware=True)
            self.ADapi.log(f"{self.heater}: vacation ended with the room {round(deficit, 1)} below target: "
                           f"cold start at {round_half(start)} until the room is near the target.", level = 'INFO')
            self.prev_in_temp = in_temp
            return round_half(start)
        learned = self._learned_ceiling()
        temp = (learned if learned is not None else self.target_indoor_temp) - 1
        if self._heat_setpoint_before_vacation is not None:
            temp = min(temp, self._heat_setpoint_before_vacation)
        self._heat_setpoint_before_vacation = None
        self.prev_in_temp = in_temp
        return round_half(max(temp, self._lowest_heat()))

    def _cold_start_step(self, current:float, in_temp:float) -> float:
        """ While the cold start runs: the setpoint is held until the room is within COLD_BACK_WITHIN of target, or
            COLD_START_MAX_TIME after the vacation end; then one move to _cold_start_goal() and the normal rules. """
        self.prev_in_temp = in_temp
        if (
            in_temp < self.target_indoor_temp - COLD_BACK_WITHIN * self.unit_scale
            and self.ADapi.datetime(aware=True) - self._cold_start_since < COLD_START_MAX_TIME
        ):
            return current
        self._end_cold_start()
        return self._cold_start_goal()

    def _cold_start_goal(self) -> float:
        """ The way down after a cold start: the lower of the setpoint held before the vacation and the learned setpoint
            (the one that exists, else target + COLD_GOAL_ABOVE), not below _lowest_heat(), not above max_temp. """
        held, learned = self._heat_setpoint_before_vacation, self._learned_ceiling()
        self._heat_setpoint_before_vacation = None
        known = [v for v in (held, learned) if v is not None]
        goal = min(known) if known else self.target_indoor_temp + COLD_GOAL_ABOVE * self.unit_scale
        goal = max(goal, self._lowest_heat())
        max_temp = self._max_temp()
        if max_temp is not None:
            goal = min(goal, max_temp)
        self.ADapi.log(f"{self.heater}: cold start ended, setpoint {round_half(goal)}.", level = 'INFO')
        return round_half(goal)

    def updateClimateTemperature(self, heater_temp, new_temperature, learn:bool = True) -> None:
        """ Updates climate with new temperature and learns the setpoint when it has been stable. """
        if heater_temp != round_half(new_temperature):
            self._send_temperature(round_half(new_temperature))
        elif (
            learn
            and self.store is not None
            and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(hours = 2)
            and self.ADapi.datetime(aware=True) - self.heater_temp_last_registered > datetime.timedelta(hours = 2)
            and self._may_learn()
        ):
            self._registerHeatingtemp(heater_temp = new_temperature)
            self.heater_temp_last_registered = self.ADapi.datetime(aware=True)

    def _may_learn(self) -> bool:
        return not self._cold_start

    def _send_temperature(self, temperature:float) -> None:
        self.ADapi.call_service('climate/set_temperature',
            entity_id = self.heater,
            temperature = temperature,
            namespace = self.namespace
        )
        self._last_commanded = temperature
        self.heater_temp_last_changed = self.ADapi.datetime(aware=True)

    def adjust_set_temperature_by(self, heater_set_temp:float, in_temp:float) -> float:
        """ Calculates if heater temperature needs to be adjusted based on temperatures.
            Returns adjusted temperature. """
        new_temperature:float = heater_set_temp
        adjust_temp_by:float = 0
        now = self.ADapi.datetime(aware=True)

        if self.window_temp is not None:
            window_temp = to_float(self.ADapi.get_state(self.window_temp, namespace = self.namespace))
            if window_temp is None:
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"{self.window_temp} has no temperature. Probably offline", level = 'DEBUG')

            if window_temp > self.target_indoor_temp + self.window_offset:
                adjust_temp_by = round((self.target_indoor_temp + self.window_offset) - window_temp, 1)

        adjust_temp_by += round(self.target_indoor_temp - in_temp, 1)

        # Deliberate asymmetry: small drops are rounded up to -0.5 after 30 min, small rises wait
        if (
            now - self.heater_temp_last_changed > datetime.timedelta(minutes = 30)
            and adjust_temp_by < 0
            and adjust_temp_by > -0.3
        ):
            adjust_temp_by = -0.5
        elif (
            now - self.heater_temp_last_changed < datetime.timedelta(minutes = 30)
            and adjust_temp_by > 0
            and adjust_temp_by < 0.4
        ):
            adjust_temp_by = 0

        persistent_temperature, valid_temp_data = self._getHeatingTempFromPersisten()

        hold, not_valid = self._trend_rule(in_temp)
        if hold:
            # The room reaches target within trend_hold_hours at its present rate: keep the setpoint
            self.prev_in_temp = in_temp
            return heater_set_temp
        if not_valid:
            valid_temp_data = False

        if self.prev_in_temp > in_temp:
            # Temp is dropping
            if (
                new_temperature <= self.target_indoor_temp - 4
                and in_temp < self.target_indoor_temp
                and now - self.heater_temp_last_changed > datetime.timedelta(hours = 4)
            ):
                # Has been turned down for more than 4 hours. The extra -1 is deliberate.
                self.prev_in_temp = in_temp
                return new_temperature + adjust_temp_by -1

            if (
                now - self.window_last_closed > datetime.timedelta(hours = 1)
                or in_temp < self.target_indoor_temp -1
            ):
                new_temperature += adjust_temp_by

                if (
                    valid_temp_data
                    and round_half(new_temperature) < round_half(persistent_temperature)
                ):
                    self.prev_in_temp = in_temp
                    return self._toward_learned(heater_set_temp, new_temperature, persistent_temperature)

        elif self.prev_in_temp < in_temp:
            # Temp is increasing
            new_temperature += adjust_temp_by

            if (
                valid_temp_data
                and round_half(new_temperature) > round_half(persistent_temperature)
            ):
                self.prev_in_temp = in_temp
                return self._toward_learned(heater_set_temp, new_temperature, persistent_temperature)

        elif (
            abs(self.target_indoor_temp - in_temp) >= STABLE_CORRECTION_MIN_DIFF
            and now - self.heater_temp_last_changed >= STABLE_CORRECTION_AFTER
        ):
            # Temperature is steady but off target: move the setpoint by the difference
            new_temperature += adjust_temp_by

        if valid_temp_data:
            # Restrict from going +- 4 degrees away from valid persistent temperature data
            if new_temperature > persistent_temperature + 4:
                new_temperature = persistent_temperature + 4
            elif new_temperature < persistent_temperature - 4:
                new_temperature = persistent_temperature - 4
        else:
            # Restrict from going +- 8 degrees away from target indoor temperature when persistent data has not been able to gather enough info
            if new_temperature > self.target_indoor_temp + 8:
                new_temperature = self.target_indoor_temp + 8
            elif new_temperature < self.target_indoor_temp - 8:
                new_temperature = self.target_indoor_temp - 8

        self.prev_in_temp = in_temp
        return new_temperature

    def _toward_learned(self, current:float, new:float, learned:float) -> float:
        """ The learned setpoint limits the computed move, it is not a jump target: when it lies beyond the computed
            setpoint, the setpoint moves at most one STEP towards it per run (or the computed move, if larger). """
        if min(current, new) <= learned <= max(current, new):
            return learned
        step = current + max(-STEP, min(STEP, learned - current))
        if (learned - current) * (new - current) > 0 and abs(new - current) > abs(step - current):
            return new
        return step

    # ------------------------------------------------------------------ #
    # Learned setpoints (JSON store, indoor mode)
    # ------------------------------------------------------------------ #

    def _registerHeatingtemp(self, heater_temp:float) -> None:
        device = self.store.device(self.heater)
        data = device.setdefault('data', {})
        out_temp_str = str(math.floor(self.weather.out_temp / 2.) * 2)
        out_lux_str = str(math.floor(self.weather.lux / 5000))

        if out_temp_str not in data:
            data[out_temp_str] = {out_lux_str : {"temp" : heater_temp, "Counter" : 1}}
        elif out_lux_str not in data[out_temp_str]:
            data[out_temp_str][out_lux_str] = {"temp" : heater_temp, "Counter" : 1}
        else:
            heatingData = data[out_temp_str][out_lux_str]
            counter = heatingData['Counter'] + 1
            avgheating = round(((heatingData['temp'] * heatingData['Counter']) + heater_temp) / counter,1)
            if counter > 100:
                counter = 10   # weight recent samples
            data[out_temp_str][out_lux_str] = {"temp" : avgheating, "Counter" : counter}
        self.store.save()

    def _getHeatingTempFromPersisten(self) -> tuple[float, bool]:
        heatingData = {}
        if self.store is not None:
            heatingData = self.store.device(self.heater).get('data', {})

        if heatingData: # Empty until the first temperature is registered: fall back to the heater below
            out_temp_str = str(math.floor(self.weather.out_temp / 2.) * 2)
            out_lux_str = str(math.floor(self.weather.lux / 5000))

            valid_temp_data:bool = True

            out_temp = self.weather.out_temp
            if out_temp_str in heatingData:
                lux_temp_data = heatingData[out_temp_str]
            else:
                # Nearest outdoor temperature bucket (same tie rules as before 1.4.0)
                valid_temp_data = False
                temp_diff = 0
                closest_temp = None
                for temps in heatingData:
                    if out_temp > float(temps):
                        if temp_diff != 0 and temp_diff < out_temp - float(temps):
                            continue
                        temp_diff = out_temp - float(temps)
                    else:
                        if temp_diff != 0 and temp_diff < float(temps) - out_temp:
                            continue
                        temp_diff = float(temps) - out_temp
                    closest_temp = temps
                out_temp_str = closest_temp
                lux_temp_data = heatingData[out_temp_str]

            if out_lux_str in lux_temp_data:
                temp = lux_temp_data[out_lux_str]['temp']
            else:
                # Nearest lux bucket (same tie rules as before 1.4.0)
                valid_temp_data = False
                lux_diff = 0
                closest_lux = None
                luxCheck = math.floor(self.weather.lux / 5000)
                for luxs in lux_temp_data:
                    if luxCheck > int(luxs):
                        if lux_diff == 0 or lux_diff >= luxCheck - int(luxs):
                            lux_diff = luxCheck - int(luxs)
                            closest_lux = luxs
                    else:
                        if lux_diff == 0 or lux_diff > int(luxs) - luxCheck:
                            lux_diff = int(luxs) - luxCheck
                            closest_lux = luxs
                if closest_lux is None:
                    return self.target_indoor_temp, False
                out_lux_str = closest_lux
                temp = lux_temp_data[out_lux_str]['temp']

            if int(lux_temp_data[out_lux_str]['Counter']) < 10:
                valid_temp_data = False

            return float(temp), valid_temp_data

        temp = self.get_heater_temp()
        if temp is None:
            return self.target_indoor_temp, False
        if temp > self.target_indoor_temp + 6 or temp < self.target_indoor_temp - 6:
            temp = self.target_indoor_temp
        return temp, False

    def _setHeatingTempFromPersisten(self, **kwargs) -> None:
        offset:float = kwargs.get('offset', 0.0)
        temp, valid_temp_data = self._getHeatingTempFromPersisten()
        temp += offset
        self._send_temperature(round_half(temp))   # the same 0.5 grid as every other command

    # ------------------------------------------------------------------ #
    # Table mode (heater logic from ElectricalManagement)
    # ------------------------------------------------------------------ #

    def find_target_temperatures(self) -> int:
        """ Index of the last `temperatures` entry whose `out` is less than or equal to the outdoor
            temperature (an entry applies from its out value and up). Below the first entry the first is used. """
        if not self.temperatures:
            return 0
        out_values = [t['out'] for t in self.temperatures]
        idx = bisect.bisect_right(out_values, self.weather.out_temp)
        return max(0, idx - 1)

    def current_target_temp_entry(self) -> dict:
        if not self.temperatures:
            return {'offset': 0}
        return self.temperatures[self.find_target_temperatures()]

    def _table_base(self, target_temp:dict) -> float:
        if 'offset' in target_temp:
            return self.target_heater_temp + target_temp['offset']
        if 'normal' in target_temp:
            return target_temp['normal']
        return self.target_heater_temp

    def getSaveTemp(self, current_target_temp:float, target_temp:dict) -> float:
        """ Returns save temperature. """
        if self.save_temp_offset is not None:
            current_target_temp += self.save_temp_offset
        elif self.save_temp is not None:
            if current_target_temp > self.save_temp + target_temp.get('offset', 0):
                current_target_temp = self.save_temp + target_temp.get('offset', 0)
        elif 'save' in target_temp:
            if current_target_temp > target_temp['save']:
                current_target_temp = target_temp['save']
        else:
            current_target_temp = self.min_temp   # nothing configured: the climate's own minimum
        return current_target_temp

    def getVacationTemp(self, current_target_temp:float, target_temp:dict) -> float:
        """ Returns vacation temperature. """
        if self.vacation_temp is not None:
            if current_target_temp > self.vacation_temp + target_temp.get('offset', 0):
                current_target_temp = self.vacation_temp + target_temp.get('offset', 0)
        elif 'vacation' in target_temp:
            if current_target_temp > target_temp['vacation']:
                current_target_temp = target_temp['vacation']
        else:
            current_target_temp = self.min_temp   # nothing configured: the climate's own minimum
        return current_target_temp

    def _table_in_temp(self):
        in_temp = self.get_in_temp()
        if in_temp is None:
            in_temp = to_float(self.ADapi.get_state(self.heater, attribute = 'current_temperature', namespace = self.namespace))
        return in_temp

    def _apply_reduce(self) -> None:
        """ Overconsumption and open windows: the lowest of the save and the vacation temperature
            (stops the heater drawing power fastest). Only sent when the setpoint is above the save temperature. """
        if self.ADapi.get_state(self.heater, namespace = self.namespace) not in ('heat', 'cool'):
            return
        save_temp, new_temp = self._reduce_temperatures()
        heater_temp = self.get_heater_temp()
        if heater_temp is None:
            self._send_temperature(self.min_temp)
        elif heater_temp > save_temp:
            self._send_temperature(new_temp)

    def _reduce_temperatures(self) -> tuple[float, float]:
        """ Returns (save temperature, the lowest of the save and the vacation temperature). """
        target_temp = self.current_target_temp_entry()
        new_temperature = self._table_base(target_temp)
        save_temp = self.getSaveTemp(current_target_temp = new_temperature, target_temp = target_temp)
        vacation_temp = self.getVacationTemp(current_target_temp = new_temperature, target_temp = target_temp)
        return save_temp, min(save_temp, vacation_temp)

    def _table_setpoint(self, in_temp, vacation:bool) -> float:
        """ Setpoint from the table, weather, window/indoor sensors, power state and daytime savings. """
        target_temp = self.current_target_temp_entry()
        new_temperature = self._table_base(target_temp)
        vacation_temp = self.getVacationTemp(current_target_temp = new_temperature, target_temp = target_temp)

        # Adjust temperature based on weather
        if self.weather.rain >= self.rain_level:
            new_temperature += 1
        elif self.weather.wind >= self.anemometer_speed:
            new_temperature += 1

        adjust = 0
        if self.window_temp is not None:
            window_temp = to_float(self.ADapi.get_state(self.window_temp, namespace = self.namespace))
            if window_temp is None:
                window_temp = self.target_indoor_temp + self.window_offset   # at the threshold: no correction
                self.ADapi.log(f"{self.window_temp} has no temperature. Probably offline", level = 'DEBUG')
            if window_temp > self.target_indoor_temp + self.window_offset:
                adjust = float(window_temp - (self.target_indoor_temp + self.window_offset))

        if in_temp is not None and in_temp > self.target_indoor_temp:
            adjust += float(in_temp - self.target_indoor_temp)
        # The window and indoor corrections together never lower the setpoint by more than 3 degrees
        adjust = min(adjust, 3)
        new_temperature -= adjust

        if vacation:
            new_temperature = vacation_temp

        # Price savings or daytime savings
        state = self.power_state
        if state == 'save':
            new_temperature = self.getSaveTemp(current_target_temp = new_temperature, target_temp = target_temp)
        elif self.DaytimeSaving():
            new_temperature = self.getSaveTemp(current_target_temp = new_temperature, target_temp = target_temp)

        # Low price for electricity or solar power
        if state == 'spend':
            new_temperature += self.spend_temp_offset

        # Avoid setting lower temp than vacation and the climate's minimum
        if new_temperature < vacation_temp:
            new_temperature = vacation_temp
        if new_temperature < self.min_temp:
            new_temperature = self.min_temp
        return new_temperature

    def _table_cycle(self) -> None:
        if not self.weather.out_temp_known:
            # The table needs the outdoor temperature. Wait for the Weather app instead of using a default.
            self.ADapi.log(f"{self.heater}: waiting for the outdoor temperature from the Weather app.", level = 'DEBUG')
            return
        state = self.ADapi.get_state(self.heater, namespace = self.namespace)
        if state is None or state == 'off' or state in UNAVAIL:
            return
        if self.power_state == 'reduce':
            self._apply_reduce()
            return

        heater_temp = self.get_heater_temp()
        if heater_temp is None:
            heater_temp = self.target_heater_temp
        in_temp = self._table_in_temp()
        self._window_notifications(in_temp)

        if self.windows_is_open:
            self._apply_reduce()
            return
        if self.vacation_state and self.vacation_keep_off:
            if self.off_retry_count >= 0:
                self.ADapi.call_service('climate/turn_off',
                    entity_id = self.heater,
                    namespace = self.namespace
                )
                self.off_retry_count -= 1
            return

        new_temperature = self._table_setpoint(in_temp, vacation = self.vacation_state)
        if heater_temp != round_half(new_temperature):
            self._send_temperature(round_half(new_temperature))

    # ------------------------------------------------------------------ #
    # Indoor controller (controller: shadow / adaptive, cc_control.py)
    # ------------------------------------------------------------------ #

    def _setup_controller(self, cfg:dict) -> None:
        """ Creates the controller for 'shadow' and 'adaptive' and restores its learned state from the JSON
            ('adaptive' key; the classic 'data' buckets are neither read nor written by it). """
        controller = cfg.get('controller') or 'classic'
        if controller == 'classic':
            if cfg.get('adaptive_keys'):
                self.ADapi.log(f"{self.heater}: {', '.join(cfg['adaptive_keys'])} only work with controller "
                               "'shadow' or 'adaptive'. Ignored with the classic controller.", level = 'INFO')
            return
        if self.hvac and self.target_mode == 'table':
            self.ADapi.log(f"{self.heater}: controller '{controller}' is for indoor mode on HVAC (heaters: indoor or "
                           "table mode). This HVAC is in table mode, so the classic controller is used.", level = 'INFO')
            return
        if cfg.get('window_offset_table') and not self.window_temp:
            self.ADapi.log(f"{self.heater}: window_offset_table needs window_sensor_temp. The table is not used.", level = 'WARNING')
        if cfg.get('internal_offset_table') and self.ADapi.get_state(self.heater, attribute = 'current_temperature', namespace = self.namespace) is None:
            self.ADapi.log(f"{self.heater}: internal_offset_table is set, but the climate reports no current_temperature "
                           "(yet). The table is used only when it does.", level = 'WARNING')
        max_temp = to_float(self.ADapi.get_state(self.heater, attribute = 'max_temp', namespace = self.namespace))
        self.controller = controller
        self.ctrl = IndoorController(ControlConfig(
            kind = 'hvac' if self.hvac else 'heater',
            target_mode = self.target_mode,
            unit = cfg['unit'],
            min_temp = self.min_temp,
            max_temp = max_temp if max_temp is not None else 35.0,
            vacation_temp = self.vacation_temp,
            max_vacation_temp = getattr(self, 'max_vacation_temp', 30.0),
            save_temp_offset = self.save_temp_offset,
            save_temp = self.save_temp,
            spend_temp_offset = self.spend_temp_offset,
            window_offset = cfg['window_offset_prior'],
            window_offset_table = cfg['window_offset_table'],
            internal_offset_table = cfg['internal_offset_table'],
            target_table = cfg['target_table'],
            outdoor_gain = cfg['outdoor_gain'],
            solar_gain = cfg['solar_gain'],
            solar_max = cfg['solar_max'],
            comfort_guard = cfg['comfort_guard'],
            temperatures = self.temperatures,
            target_heater_temp = self.target_heater_temp,
            has_fireplace = cfg.get('has_fireplace', False),
            max_setpoint_drop = cfg.get('max_setpoint_drop'),
            interval_s = self.interval,
            cold_start_deficit = cfg.get('cold_start_deficit'),
        ))
        self.diagnostic_sensor = cfg.get('diagnostic_sensor')
        self._virtual_setpoint = None      # shadow: the setpoint and mode the controller proposed last,
        self._virtual_mode = None          # fed back to it instead of the real ones
        self._ctrl_last_write = None
        self._lux_seen = False             # lux stays 0 without any lux source: use day / night until one reports
        if self.store is None:
            self.ADapi.log(f"{self.heater}: controller '{controller}' without json_path: the learned window and climate "
                           "sensor offsets start empty after every restart.", level = 'INFO')
            return
        for level, text in self.ctrl.restore(self.store.device(self.heater).get('adaptive')):
            self.ADapi.log(f"{self.heater}: {text}", level = level)

    def _sensor_reading(self, entity):
        """ (value, seconds since last reported), or (None, None). """
        if entity is None:
            return None, None
        value = to_float(self.ADapi.get_state(entity, namespace = self.namespace))
        if value is None:
            return None, None
        return value, self._sensor_age(entity).total_seconds()

    def _controller_inputs(self, state:str, setpoint) -> Inputs:
        now = self.ADapi.datetime(aware=True)
        room, room_age = self._sensor_reading(self.indoor_sensor_temp)
        backup, backup_age = self._sensor_reading(self.backup_indoor_sensor_temp)
        window, window_age = self._sensor_reading(self.window_temp)
        if self.weather.lux > 0:
            self._lux_seen = True
        increasing, after_increase = self.DaytimeIncreasing()
        preset_modes = self.ADapi.get_state(self.heater, attribute = 'preset_modes', namespace = self.namespace) or []
        fan_mode = self.ADapi.get_state(self.heater, attribute = 'fan_mode', namespace = self.namespace)
        return Inputs(
            now = now,
            target = self.target_indoor_temp,
            setpoint = setpoint,
            hvac_mode = state,
            room = room, room_age_s = room_age,
            backup = backup, backup_age_s = backup_age,
            window = window, window_age_s = window_age,
            internal = to_float(self.ADapi.get_state(self.heater, attribute = 'current_temperature', namespace = self.namespace)),
            out_temp = self.weather.out_temp if self.weather.out_temp_known else None,
            lux = self.weather.lux if self._lux_seen else None,
            daytime = self.ADapi.now_is_between('sunrise', 'sunset - 00:30:00'),
            since_windy_s = (now - self.last_windy_time).total_seconds(),
            wind_now = self.weather.wind >= self.anemometer_speed,
            raining = self.weather.rain >= self.rain_level,
            rain = self.weather.rain,
            windows_open = self.windows_is_open,
            since_window_closed_s = (now - self.window_last_closed).total_seconds(),
            vacation = self.vacation_state,
            power_state = self.power_state,
            increasing = increasing,
            after_increase = after_increase,
            saving = self.DaytimeSaving(),
            preset = self.ADapi.get_state(self.heater, attribute = 'preset_mode', namespace = self.namespace),
            boost_available = 'boost' in preset_modes and fan_mode != 'Silence',
        )

    def _controller_cycle(self) -> None:
        """ One run with controller shadow / adaptive. Shadow: classic runs first and stays in control; the
            controller then gets its own last proposals as setpoint and mode, so it does not follow classic. """
        shadow = self.controller == 'shadow'
        classic_setpoint = classic_mode = None
        if shadow:
            changed_before = self.heater_temp_last_changed
            self._set_indoortemp(classic_only = True)
            classic_setpoint = self._last_commanded if self.heater_temp_last_changed != changed_before else self.get_heater_temp()
            classic_mode = self.ADapi.get_state(self.heater, namespace = self.namespace)
        if self._automation_off():
            return
        if not shadow:
            self._note_entity_state()
        state = self.ADapi.get_state(self.heater, namespace = self.namespace)
        if state is None or state in UNAVAIL:
            return
        if self.hvac:
            if state not in ('heat', 'cool', 'fan_only', 'off'):
                if not shadow:
                    self.ADapi.log(f"Unregistered AC state: {state}. {self.friendly_name()}. Will not automate", level = 'WARNING')
                return
            if not shadow:
                self._silence()
        else:
            state = 'off' if state == 'off' else 'heat'
        if (not shadow and self.target_mode == 'table' and self.vacation_state and self.vacation_keep_off
                and not self.windows_is_open and self.power_state != 'reduce'):
            if self.off_retry_count >= 0:   # as classic table mode
                self.ADapi.call_service('climate/turn_off', entity_id = self.heater, namespace = self.namespace)
                self.off_retry_count -= 1
            return

        setpoint = self.get_heater_temp()
        if shadow:
            if state in ('heat', 'cool', 'fan_only'):
                self._virtual_mode = self._virtual_mode or state
                state = self._virtual_mode
            else:
                self._virtual_mode = None
            if self._virtual_setpoint is None:
                self._virtual_setpoint = setpoint
            setpoint = self._virtual_setpoint
        if self.target_mode == 'table':
            self.ctrl.cfg.target_heater_temp = self.target_heater_temp   # target_heater_input may have changed
        inp = self._controller_inputs(state, setpoint)
        pw_before = self.ctrl.pw_state
        dec = self.ctrl.update(inp)
        for level, text in dec.logs:
            self.ADapi.log(f"{self.heater}: {'[shadow] ' if shadow else ''}{text}", level = level)

        if shadow:
            if dec.hvac_mode is not None and self._virtual_mode is not None:
                self._virtual_mode = dec.hvac_mode
            if dec.temperature is not None:
                self._virtual_setpoint = dec.temperature
            holds, mode = self._virtual_setpoint, self._virtual_mode or state
        else:
            if dec.hvac_mode is not None and self.hvac and dec.hvac_mode != state:
                self._set_hvac_mode(dec.hvac_mode)
                # A new mode and the fan mode in the same moment can lose one of them in the unit: set the
                # Silence fan mode separately a few seconds later, as classic does (it re-runs after a mode change).
                if self.silence:
                    self.ADapi.run_in(self._silence_later, 5)
            if dec.temperature is not None and dec.temperature != setpoint:
                self._send_temperature(dec.temperature)
            if dec.preset is not None and self.hvac:
                self.ADapi.call_service('climate/set_preset_mode',
                    entity_id = self.heater,
                    preset_mode = dec.preset,
                    namespace = self.namespace
                )
            holds = dec.temperature if dec.temperature is not None else setpoint
            mode = dec.hvac_mode or state
            self._window_notifications(dec.diag.get('R'))
            self._controller_screens(dec.diag.get('R'), mode)

        self._controller_persist(dec, pw_before, inp.now)
        self._controller_report(dec, holds, mode, classic_setpoint, classic_mode)

    def _controller_screens(self, room, mode:str) -> None:
        """ Screens as in classic: always tried in cool, else when the room is 0.2 above target, no wind for 45 min
            and the outdoor temperature is above screening_temp. """
        if not self.screening or room is None:
            return
        if mode == 'cool':
            for s in self.screening:
                s.try_screen_close()
            return
        if (
            room > self.target_indoor_temp + 0.2
            and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
        ):
            if self.weather.out_temp > self.screening_temp:
                for s in self.screening:
                    s.try_screen_close()
        elif self.hvac:
            for s in self.screening:
                s.can_close_on_lux = False

    def _controller_persist(self, dec, pw_before:str, now) -> None:
        """ The controller state goes under 'adaptive' in the JSON. The file is written at most every 30 min, at
            once when a power state starts or ends (a restart must not add the offset twice), and on terminate. """
        if self.store is None:
            return
        edge = self.ctrl.pw_state != pw_before
        due = self._ctrl_last_write is None or now - self._ctrl_last_write >= CONTROL_WRITE_EVERY
        if dec.save or due or edge:
            self.store.device(self.heater)['adaptive'] = self.ctrl.snapshot()
        if due or edge:
            self.store.save()
            self._ctrl_last_write = now

    def controller_terminate(self) -> bool:
        """ App terminate: puts the latest controller state in the store. True when there is one to write. """
        if self.ctrl is None or self.store is None:
            return False
        self.store.device(self.heater)['adaptive'] = self.ctrl.snapshot()
        return True

    def _controller_report(self, dec, holds, mode, classic_setpoint, classic_mode) -> None:
        """ One DEBUG line per run, and the optional diagnostic_sensor (state: the setpoint the controller holds,
            in shadow the one it would hold; attributes: the terms). """
        d = dec.diag

        def num(v, digits = 2):
            return round(v, digits) if isinstance(v, (int, float)) and not isinstance(v, bool) else v

        terms = ' '.join(f"{k} {num(d[k])}" for k in DIAG_KEYS if k in d and d[k] is not None)
        text = f"{self.heater}: {self.controller} {mode} {terms} send {dec.temperature} hold {holds} frozen {d.get('frozen')}"
        if self.controller == 'shadow':
            text += f" classic {classic_setpoint} {classic_mode}"
        self.ADapi.log(text, level = 'DEBUG')
        if not self.diagnostic_sensor:
            return
        attributes = {k: num(d[k], 3) for k in DIAG_KEYS if k in d}
        attributes.update(
            controller = self.controller,
            climate = self.heater,
            hvac_mode = mode,
            send = dec.temperature,
            frozen = d.get('frozen'),
            unit_of_measurement = '°F' if self.ctrl.cfg.unit == 'F' else '°C',
            friendly_name = f"{self.friendly_name()} controller",
        )
        if self.controller == 'shadow':
            attributes.update(classic_setpoint = classic_setpoint, classic_mode = classic_mode)
        self.ADapi.set_state(self.diagnostic_sensor,
            state = holds if holds is not None else 'unknown',
            attributes = attributes,
            replace = True,
            check_existence = False,
            namespace = self.namespace
        )


class Aircondition(Heater):
    """ Heat pump / AC with heat, cool and fan_only. """

    hvac:bool = True

    def __init__(self, app, cfg:dict):
        self._last_heat_setpoint = None    # last setpoint sent in heat (indoor mode, without a power offset), memory only
        self._last_heat_setpoint_before_lowering = None   # setpoint before it was lowered for an overshoot instead of fan_only
        self._last_mode_change = None      # when the hvac mode last changed (set by the app or seen at a run)
        self._mode_seen = None
        super().__init__(app, cfg)

        try:
            self.max_vacation_temp = float(cfg['max_vacation_temp'])
        except (ValueError, TypeError):
            self.max_vacation_temp:float = 30
            self.ADapi.log(f"{self.heater}: error setting max vacation temperature. Set to 30 degrees.", level = 'INFO')

        self.silence:list = cfg.get('silence') or []
        self.fan_mode = self.ADapi.get_state(self.heater, attribute = 'fan_mode', namespace = self.namespace)
        self.fan_mode_persistent = self.fan_mode

        if self.store is not None:
            stored = self.store.device(self.heater).get('fan_mode')
            if stored is not None:
                self.fan_mode_persistent = stored

        if self.fan_mode_persistent is None or self.fan_mode_persistent == 'Silence':
            self.fan_mode_persistent = 'Auto'

        self.has_fireplace:bool = cfg.get('has_fireplace', False)
        self.max_setpoint_drop:float = cfg.get('max_setpoint_drop') or 3.0 * self.unit_scale

        self._schedule_silence()
        self._setup_controller(cfg)

    def _set_hvac_mode(self, mode:str) -> None:
        self.ADapi.call_service('climate/set_hvac_mode',
            entity_id = self.heater,
            hvac_mode = mode,
            namespace = self.namespace
        )
        self._last_mode_change = self.ADapi.datetime(aware=True)
        self._mode_seen = mode

    def _heat_setpoint(self, heater_temp:float, in_temp:float, real_in_temp:float) -> float:
        if self.target_mode == 'table':
            if self.power_state == 'reduce':
                # As a table heater: the lowest of the save and the vacation temperature
                return self._reduce_temperatures()[1]
            return self._table_setpoint(real_in_temp, vacation = False)
        return self._indoor_setpoint(heater_temp = heater_temp, in_temp = in_temp, mode = 'heat')

    # ------------------------------------------------------------------ #
    # Heating in indoor mode: lowest setpoint, way back after lowering, restart from fan_only
    # ------------------------------------------------------------------ #

    def _lowest_heat(self) -> float:
        """ Lowest heat setpoint in indoor mode. A unit idles once its setpoint is at or below its own sensor reading,
            so a lower setpoint only makes the way back up slow. """
        return max(self.min_temp, self.target_indoor_temp - self.max_setpoint_drop)

    def _send_temperature(self, temperature:float) -> None:
        super()._send_temperature(temperature)
        self._note_heat_setpoint(temperature)

    def _note_heat_setpoint(self, temperature:float) -> None:
        """ Remembers the setpoint sent in heat (indoor mode; not on vacation, not with a window open, not during a cold
            start) without the power-state offset in it. Used for the start after fan_only and after a vacation. """
        if (
            self.target_mode == 'indoor'
            and not self.vacation_state
            and not self.windows_is_open
            and not self._cold_start
            and self.ADapi.get_state(self.heater, namespace = self.namespace) == 'heat'
        ):
            self._last_heat_setpoint = temperature - self._applied_offset

    def _held_heat_setpoint(self):
        """ The last setpoint sent in heat; before anything was sent (app start) the one the unit holds in heat. """
        if self._last_heat_setpoint is not None:
            return self._last_heat_setpoint
        if self.ADapi.get_state(self.heater, namespace = self.namespace) != 'heat':
            return None
        return super()._held_heat_setpoint()

    def _setHeatingTempFromPersisten(self, **kwargs) -> None:
        """ Setpoint after fan_only -> heat (offset -1). Indoor mode: the setpoint held in heat before, with the learned
            setpoint + offset as the ceiling (after an app restart only the latter), not below _lowest_heat(). """
        if self.target_mode != 'indoor':
            super()._setHeatingTempFromPersisten(**kwargs)
            return
        temp, valid_temp_data = self._getHeatingTempFromPersisten()
        temp += kwargs.get('offset', 0.0)
        if self._last_heat_setpoint is not None:
            temp = min(temp, self._last_heat_setpoint)
        self._send_temperature(round_half(max(temp, self._lowest_heat())))

    def _heat_limits(self, heater_temp:float, in_temp:float, new_temperature:float) -> float:
        """ Indoor mode, normal power state. After the setpoint was lowered for an overshoot (instead of fan_only), the
            way back is one move once the room is at or below target and not reaching it in time by itself: to the
            setpoint from before the lowering, at most the learned setpoint and target + RETURN_ABOVE_TARGET.
            The result is never below _lowest_heat(). """
        before = self._last_heat_setpoint_before_lowering
        if before is not None and in_temp <= self.target_indoor_temp:
            rate = self._room_rate()
            rising = rate > 0 and (self.target_indoor_temp - in_temp) / rate <= self.trend_hold_hours
            if heater_temp >= before:
                self._last_heat_setpoint_before_lowering = None
            elif not rising:
                goal = min(before, self.target_indoor_temp + RETURN_ABOVE_TARGET)
                ceiling = self._learned_ceiling()
                if ceiling is not None:
                    goal = min(goal, ceiling)
                new_temperature = max(new_temperature, goal)
                self._last_heat_setpoint_before_lowering = None
        return max(new_temperature, self._lowest_heat())

    def _fan_only_now(self, heater_temp:float) -> bool:
        """ heat -> fan_only at target + 0.6: at once with has_fireplace, in table mode and in a power state. Else only
            when the setpoint is at _lowest_heat() or the room still rises FAN_RISE per hour; until then the setpoint
            is lowered one step per run and the unit stays in heat. """
        if self.has_fireplace or self.target_mode != 'indoor' or self.power_state != 'normal':
            return True
        return heater_temp <= self._lowest_heat() or self._room_rate() >= FAN_RISE * self.unit_scale

    def _may_learn(self) -> bool:
        """ No learning within LEARN_AFTER_MODE_CHANGE of a mode change (cycling heat / fan_only teaches too high
            setpoints) or while the unit reports an hvac_action other than heating / idle, nor during a cold start. """
        if self._cold_start:
            return False
        if (
            self._last_mode_change is not None
            and self.ADapi.datetime(aware=True) - self._last_mode_change < LEARN_AFTER_MODE_CHANGE
        ):
            return False
        action = self.ADapi.get_state(self.heater, attribute = 'hvac_action', namespace = self.namespace)
        return action is None or action in ('heating', 'idle')

    # ------------------------------------------------------------------ #
    # Silence fan mode
    # ------------------------------------------------------------------ #

    def _silence_fan_available(self) -> bool:
        fan_modes = self.ADapi.get_state(self.heater, attribute = 'fan_modes', namespace = self.namespace)
        return fan_modes is not None and 'Silence' in fan_modes

    def _in_silence_period(self) -> bool:
        """ True while a silence period applies: inside start/stop, not on vacation, and (with `presence`)
            at least one listed person is home. """
        wanted = False
        for time in self.silence:
            if 'start' in time and 'stop' in time:
                if (
                    self.ADapi.now_is_between(time['start'], time['stop'])
                    and not self.vacation_state
                ):
                    if 'presence' in time:
                        for presence in time['presence']:
                            if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                wanted = True
                    else:
                        wanted = True
                    break
        return wanted

    def _fan_to_silence(self) -> None:
        """ Sets the fan mode Silence and remembers the fan mode that was set before. """
        fan_mode_now = self.ADapi.get_state(self.heater, attribute = 'fan_mode', namespace = self.namespace)
        if fan_mode_now == 'Silence':
            return
        self.fan_mode = fan_mode_now
        self.ADapi.call_service('climate/set_fan_mode',
            entity_id = self.heater,
            fan_mode = 'Silence',
            namespace = self.namespace
        )
        if self.fan_mode != self.fan_mode_persistent and self.fan_mode is not None:
            self.fan_mode_persistent = self.fan_mode
            if self.store is not None:
                self.store.device(self.heater)['fan_mode'] = self.fan_mode
                self.store.save()

    def _fan_back_from_silence(self) -> None:
        """ Sets the fan mode back to the one that was set before Silence (Auto if none is known). """
        fan_mode_now = self.ADapi.get_state(self.heater, attribute = 'fan_mode', namespace = self.namespace)
        if fan_mode_now != 'Silence':
            return
        self.ADapi.call_service('climate/set_fan_mode',
            entity_id = self.heater,
            fan_mode = self.fan_mode_persistent,
            namespace = self.namespace
        )

    def _silence(self) -> None:
        """ Makes the fan mode match the silence periods: Silence inside a period, the earlier fan mode outside.
            Called at every run, 5 seconds after a mode change, and by the timers at each period start and stop. """
        if not self._silence_fan_available():
            return
        if self._in_silence_period():
            self._fan_to_silence()
        else:
            self._fan_back_from_silence()

    def _silence_later(self, *args, **kwargs) -> None:
        self._silence()

    def _silence_timer(self, *args, **kwargs) -> None:
        """ Timer at the start or stop of a silence period: also covers a period that ends while nobody runs. """
        if self._automation_off():
            return
        self._silence()

    def _schedule_silence(self) -> None:
        """ One daily timer at every start and stop time of the silence periods, so the fan mode changes on time
            and not at the next run. """
        times = set()
        for period in self.silence:
            for key in ('start', 'stop'):
                value = period.get(key)
                if isinstance(value, int) and 0 <= value < 86400:      # YAML 1.1 reads an unquoted 22:00:00 as 79200
                    value = f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"
                if isinstance(value, str):
                    times.add(value)
        for value in sorted(times):
            try:
                self.ADapi.run_daily(self._silence_timer, value)
            except (ValueError, TypeError, NotImplementedError):
                self.ADapi.log(f"{self.heater}: silence time '{value}' not understood. Silence changes at the next run.", level = 'WARNING')

    def _set_indoortemp(self, **kwargs) -> None:
        if self.ctrl is not None and 'classic_only' not in kwargs:
            self._controller_cycle()
            return
        if self._automation_off():
            return
        self._note_entity_state()
            # Set variables from indoor sensor and heater
        in_temp = self.get_in_temp()
        if in_temp is None:
            self.ADapi.log(f"{self.heater}: indoor temp is None. Aborting setting new temperature", level = 'INFO')
            return
        self._record_room()
        real_in_temp = in_temp
        heater_temp = self.get_heater_temp()
        ac_state = self.ADapi.get_state(self.heater, namespace = self.namespace)
        if ac_state in ('heat', 'cool', 'fan_only', 'off') and ac_state != self._mode_seen:
            if self._mode_seen is not None:
                self._last_mode_change = self.ADapi.datetime(aware=True)
            self._mode_seen = ac_state
        if ac_state != 'heat':
            self._end_cold_start()
            if ac_state != 'fan_only':
                self._vacation_ended = False

        self._silence()
        self._window_notifications(real_in_temp)

        if ac_state == 'heat':
            if heater_temp is None:
                return

            if self.windows_is_open:
                self._clear_power_offset()
                self._vacation_ended = False
                self._set_hvac_mode('fan_only')
                return

            shifted = False
            doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
            if doDaytimeIncreasing:
                in_temp -= 0.5
                shifted = True

            if self.DaytimeSaving():
                in_temp += 0.5
                shifted = True
            else:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

                # Correct indoor temp when rain
                elif self.weather.rain >= self.rain_level:
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            learn = False
            # Holliday temperature
            if self.vacation_state:
                self._clear_power_offset()
                self._last_heat_setpoint = None
                self._last_heat_setpoint_before_lowering = None
                new_temperature = self.vacation_temp
                if (
                    in_temp > self.target_indoor_temp -3
                    and in_temp > self.vacation_temp + 3
                ):
                    self._set_hvac_mode('fan_only')
                    if self.weather.out_temp > self.screening_temp:
                        for s in self.screening:
                            s.try_screen_close()
                    return
                else:
                    for s in self.screening:
                        s.can_close_on_lux = False
            elif self._vacation_ended:
                new_temperature = self._after_vacation(in_temp)
            else:
                new_temperature = self._heat_setpoint(heater_temp, in_temp, real_in_temp)
                learn = self.target_mode == 'indoor' and not shifted and self.power_state == 'normal'
                if self.target_mode == 'indoor' and self.power_state == 'normal':
                    new_temperature = self._heat_limits(heater_temp, in_temp, new_temperature)

                # Check if there is a need to boost the ac when windy
                if (
                    self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 25)
                    and in_temp < self.target_indoor_temp -0.7
                ):
                    if self.ADapi.get_state(self.heater, attribute='fan_mode', namespace = self.namespace) != 'Silence':
                        if 'boost' in (self.ADapi.get_state(self.heater, attribute='preset_modes', namespace = self.namespace) or []):
                            if self.ADapi.get_state(self.heater, attribute='preset_mode', namespace = self.namespace) != 'boost':
                                self.ADapi.call_service('climate/set_preset_mode',
                                    entity_id = self.heater,
                                    preset_mode = 'boost',
                                    namespace = self.namespace
                                )

                elif self.ADapi.get_state(self.heater, attribute='preset_mode', namespace = self.namespace) == 'boost':
                    self.ADapi.call_service('climate/set_preset_mode',
                        entity_id = self.heater,
                        preset_mode = 'none',
                        namespace = self.namespace
                    )

                # Check if it is hot inside
                if (
                    in_temp > self.target_indoor_temp + 0.2
                    and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
                ):
                    if self.weather.out_temp > self.screening_temp:
                        for s in self.screening:
                            s.try_screen_close()
                    if (
                        in_temp > self.target_indoor_temp + 0.6
                        and not afterDaytimeIncrease
                    ):
                        if self._fan_only_now(heater_temp):
                            self._set_hvac_mode('fan_only')
                            self.ADapi.run_in(self._set_indoortemp, 5) # To set silence to new fan mode, if time for it
                            return
                        # Lower by the computed adjustment, at most one step and not below the lowest setpoint
                        lowered = min(heater_temp, max(new_temperature, heater_temp - STEP, self._lowest_heat()))
                        if round_half(lowered) < heater_temp and self._last_heat_setpoint_before_lowering is None:
                            self._last_heat_setpoint_before_lowering = heater_temp
                        new_temperature = lowered
                else:
                    for s in self.screening:
                        s.can_close_on_lux = False

            # Update with new temperature
            self.updateClimateTemperature(heater_temp = heater_temp, new_temperature = new_temperature, learn = learn)

        elif ac_state == 'cool':
            if heater_temp is None:
                return

            if self.windows_is_open:
                self._clear_power_offset()
                self._set_hvac_mode('fan_only')
                return

            doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
            if doDaytimeIncreasing:
                in_temp += 0.5

            if self.DaytimeSaving():
                in_temp -= 0.5
            else:
                # Wind/rain: same direction as in heating, less cooling needed (deliberate)
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3
                elif self.weather.rain >= self.rain_level:
                    in_temp -= 0.3

            for s in self.screening:
                s.try_screen_close()

            set_fan_mode = False
            if (
                not self.ADapi.now_is_between('sunrise', 'sunset - 00:30:00')
                and self.weather.rain > 0
                and in_temp < self.target_indoor_temp + 2
            ):
                set_fan_mode = True
            elif in_temp < self.target_indoor_temp + 1:
                set_fan_mode = True
            if set_fan_mode:
                self._clear_power_offset()
                self._set_hvac_mode('fan_only')
                return
            if self.vacation_state:
                if in_temp < self.max_vacation_temp - 2:
                    self._clear_power_offset()
                    self._set_hvac_mode('fan_only')
                    return

            new_temperature = self._indoor_setpoint(heater_temp = heater_temp, in_temp = in_temp, mode = 'cool')

            # Cooling setpoints are not learned (they would pollute the heating data)
            if heater_temp != round_half(new_temperature):
                self._send_temperature(round_half(new_temperature))

        elif ac_state == 'fan_only':
            self._clear_power_offset()
            # Checking temperature hvac_mode == fan_only
            if (
                not self.windows_is_open
                and not self.vacation_state
            ):
                set_cool = False
                # No cooling when it is cold outside (outdoor below target - 4): airing out (opening a window) is better. Deliberate.
                if (
                    in_temp > self.target_indoor_temp + 2
                    and self.weather.out_temp > self.target_indoor_temp - 4
                ):
                    if (
                        self.ADapi.now_is_between('sunrise', 'sunset - 00:30:00')
                        and self.weather.rain == 0
                    ):
                        set_cool = True
                    elif in_temp > self.target_indoor_temp + 3:
                        set_cool = True
                    if set_cool:
                        self._set_hvac_mode('cool')

                elif in_temp <= self.target_indoor_temp:
                    self._set_hvac_mode('heat')
                    # Set new temperature after change to heating
                    if self._vacation_ended:
                        self.ADapi.run_in(self._set_indoortemp, 10)   # the heat run starts after the vacation
                    else:
                        self.ADapi.run_in(self._setHeatingTempFromPersisten, 10, offset = -1)
                    return
            elif not self.windows_is_open: # And vacation
                if (
                    in_temp <= self.target_indoor_temp -5
                    or in_temp <= self.vacation_temp + 2
                ):
                    self._set_hvac_mode('heat')
                    # Set new temperature after change to heating
                    self.ADapi.run_in(self._set_indoortemp, 20)
                elif in_temp > self.max_vacation_temp:
                    self._set_hvac_mode('cool')
                    self.ADapi.run_in(self._set_indoortemp, 20)
            self._vacation_ended = False   # still fan_only (room above target, or a window open): no start after the vacation

            if self.weather.out_temp > float(self.screening_temp):
                for s in self.screening:
                    s.try_screen_close()

        elif ac_state not in ('off', None) and ac_state not in UNAVAIL:
            # hvac state that is not heat/cool/fan_only/off. Log for notice.
            self.ADapi.log(
                f"Unregistered AC state: {ac_state}. {self.friendly_name()}. Will not automate",
                level = 'WARNING'
            )


class Screen:
    """ Class to control cover entities based on inside temperature and weather sensors. """

    def __init__(self, app,
        screen:str,
        windowsensors:list,
        lux_close:int,
        lux_open:int,
        lux_open_when_media_is_on:int,
        anemometer_speed_limit:int,
        cloud_cover_limit:float,
        not_when_home:list,
        mediaplayers:list,
        namespace:str
    ):
        self.ADapi = app.ADapi
        self.weather = app.weather
        self.namespace = namespace

            # Sensors
        self.screen = screen
        self.windowsensors = windowsensors
        self.mediaplayers = mediaplayers
        self.not_when_home = not_when_home

            # Variables
        self.lux_close:int = lux_close
        self.lux_open_normal:int = lux_open
        self.lux_open_when_media_is_on:int = lux_open_when_media_is_on
        self.anemometer_speed_limit:int = anemometer_speed_limit
        self.cloud_cover_limit:float = cloud_cover_limit   # close only below this cloud cover (%)
        self.can_close_on_lux:bool = False   # armed by a close attempt below lux_close, see weather_updated

        self.screen_position = self.ADapi.get_state(self.screen, attribute='current_position', namespace = self.namespace)

        self.ADapi.listen_event(self.weather_updated, 'WEATHER_CHANGE',
            namespace = self.namespace
        )

    def windowsopened(self) -> int:
        """ Returns number of opened windows. """
        opened:int = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

    def try_screen_close(self) -> None:
        """ Checks conditions to close screens. """
        if (
            self.weather.rain == 0
            and self.weather.wind < self.anemometer_speed_limit
            and self.windowsopened() == 0
        ):
            for person in self.not_when_home:
                if self.ADapi.get_state(person, namespace = self.namespace) == 'home':
                    return

            if (
                self.weather.lux >= self.lux_close
                and self.weather.cloud_cover < self.cloud_cover_limit
            ):
                position = self.ADapi.get_state(self.screen, attribute='current_position', namespace = self.namespace)
                if position == self.screen_position and position == 100:
                    self.ADapi.call_service('cover/close_cover',
                        entity_id= self.screen,
                        namespace = self.namespace
                    )
                    self.screen_position = 0

            elif not self.can_close_on_lux:
                self.can_close_on_lux = True

    def weather_updated(self, event_name, data, **kwargs) -> None:
        """ Listens for weather change from the weather app. The Weather app sends None for missing values. """
        if (
            not self.check_if_try_screen_open()
            and self.can_close_on_lux
        ):
            lux = to_float(data.get('lux'))
            cloud_cover = to_float(data.get('cloud_cover'))
            if cloud_cover is None:
                cloud_cover = self.weather.cloud_cover
            if (
                lux is not None
                and lux >= self.lux_close
                and cloud_cover < self.cloud_cover_limit
            ):
                self.try_screen_close()

    def check_if_try_screen_open(self) -> bool:
        """ Checks conditions from configured weather sensors before trying to open or screens. """
        if self._check_mediaplayers_off():
            lux_open = self.lux_open_normal
        else:
            lux_open = self.lux_open_when_media_is_on
        if (
            self.weather.rain > 0
            or self.weather.wind > self.anemometer_speed_limit
            or self.weather.lux < lux_open
        ):
           self.try_screen_open()
           return True

        return False

    def try_screen_open(self) -> None:
        """ Checks conditions to open screens. """
        if (
            self.windowsopened() == 0
            and self.screen_position != 100
        ):
            openme = False
            position = self.ADapi.get_state(self.screen, attribute='current_position', namespace = self.namespace)
            if position != self.screen_position and self.weather.lux <= 400:
                openme = True   # at dusk always open, also after the cover was moved by hand
            elif position == self.screen_position:
                openme = True
            if openme:
                self.ADapi.call_service('cover/open_cover',
                    entity_id= self.screen,
                    namespace = self.namespace
                )
                self.screen_position = 100

    def _check_mediaplayers_off(self) -> bool:
        for mediaplayer in self.mediaplayers:
            if self.ADapi.get_state(mediaplayer, namespace = self.namespace) == 'on':
                return False
        return True


class Notify_Mobiles:
    """ Class to send notification with 'notify' HA integration """

    def __init__(self, api, namespace = 'default'):
        self.ADapi = api
        self.namespace = namespace

    def send_notification(self, **kwargs) -> None:
        """ Sends notification to recipients via Home Assistant notification. """
        message:str = kwargs['message']
        message_title:str = kwargs.get('message_title', 'Home Assistant')
        message_recipient:list = kwargs.get('message_recipient') or []

        for re in message_recipient:
            self.ADapi.call_service(f'notify/{re}',
                title = message_title,
                message = message,
                namespace = self.namespace
            )
