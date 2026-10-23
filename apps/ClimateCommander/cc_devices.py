""" ClimateCommander devices: heaters, HVAC (heat pumps / AC) and screens.

    One device controls one climate entity. The app (climateCommander.py) creates the devices,
    owns the outdoor weather values and the JSON store, and hands both to every device.

    Two ways to find the setpoint (target_mode):
      'indoor' : keep a fixed indoor temperature (target_indoor_temp / target_indoor_input). The
                 setpoint is moved step by step after the indoor sensor, and the setpoint that held
                 the target is learned per outdoor temperature and lux (JSON store).
      'table'  : setpoint = target_heater_temp (or target_heater_input) + the `offset` (or the
                 absolute `normal`) from the `temperatures` table for the outdoor temperature,
                 corrected for rain, wind, a window temperature sensor and an indoor sensor above
                 target. This is the heater logic from ElectricalManagement.

    Power states (set by ElectricalManagement through set_power_state, see README):
      'normal', 'save' (high price), 'spend' (low price / solar), 'reduce' (over the kWh limit).

    @Pythm / https://github.com/Pythm
"""

from __future__ import annotations

import bisect
import datetime
import math

UNAVAIL = ('unavailable', 'unknown')
POWER_STATES = ('normal', 'save', 'spend', 'reduce')
DEFAULT_POWER_STATE_MINUTES = 45   # a power state without a refresh falls back to 'normal' after this
STALE_SENSOR = datetime.timedelta(hours = 1)
STABLE_CORRECTION_MIN_DIFF = 0.3   # indoor mode: correct a steady room that is this far off target
STABLE_CORRECTION_AFTER = datetime.timedelta(minutes = 30)


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

        self.daytime_savings:list = cfg.get('daytime_savings') or []
        self.daytime_increasing:list = cfg.get('daytime_increasing') or []
        self.automate = cfg.get('automate')

            # Indoor temperature trend
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
        self.vacation_state = new == 'on'
        self.off_retry_count = 3
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
            self.ADapi.run_in(self._set_indoortemp, 1)

    def _windowClosed(self, entity, attribute, old, new, **kwargs) -> None:
        if self.windowsopened() == 0:
            if self.windows_is_open:
                self.notify_hot_armed = True
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

    def _sensor_age(self, entity):
        """ Time since the sensor last reported. last_reported changes on every report, also when
            the value is the same; last_updated only when it changes. """
        stamp = self.ADapi.get_state(entity, attribute = 'last_reported', namespace = self.namespace)
        if not stamp:
            stamp = self.ADapi.get_state(entity, attribute = 'last_updated', namespace = self.namespace)
        if not stamp:
            return datetime.timedelta(0)
        try:
            return self.ADapi.datetime(aware=True) - self.ADapi.convert_utc(stamp)
        except (TypeError, ValueError):
            return datetime.timedelta(0)

    def get_in_temp(self):
        """ Indoor temperature from the indoor sensor. Uses the backup sensor when the indoor sensor
            is unavailable or has not reported for an hour. None when there is no usable value. """
        in_temp = None
        if self.indoor_sensor_temp is not None:
            in_temp = to_float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
            stale = in_temp is not None and self._sensor_age(self.indoor_sensor_temp) > STALE_SENSOR
            if in_temp is not None and not stale:
                return in_temp
        if self.backup_indoor_sensor_temp is not None:
            backup = to_float(self.ADapi.get_state(self.backup_indoor_sensor_temp, namespace = self.namespace))
            if backup is not None:
                self.ADapi.log(f"{self.heater}: using backup indoor temp {backup}", level = 'DEBUG')
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
        if self._automation_off():
            return
        self._note_entity_state()
        if self.target_mode == 'table':
            self._table_cycle()
            return

        in_temp = self.get_in_temp()
        if in_temp is None:
            return
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

        elif self.vacation_state:
            new_temperature = self.vacation_temp
            self._clear_power_offset()
            if in_temp > self.target_indoor_temp:
                if self.weather.out_temp > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()
        else:
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
        if offset == 0:
            base = heater_temp
            if self._applied_offset != 0 and self._base_setpoint is not None:
                if self._last_commanded is not None and heater_temp != self._last_commanded:
                    base = heater_temp - self._applied_offset   # changed by hand during the state
                else:
                    base = self._base_setpoint
            self._clear_power_offset()
            return self.adjust_set_temperature_by(heater_set_temp = base, in_temp = in_temp)

        if self._applied_offset == 0 or self._base_setpoint is None:
            self._base_setpoint = heater_temp
        elif self._last_commanded is not None and heater_temp != self._last_commanded:
            self._base_setpoint = heater_temp - self._applied_offset   # changed by hand during the state
        self._applied_offset = offset
        self.prev_in_temp = in_temp
        return self._base_setpoint + offset

    def updateClimateTemperature(self, heater_temp, new_temperature, learn:bool = True) -> None:
        """ Updates climate with new temperature and learns the setpoint when it has been stable. """
        if heater_temp != round_half(new_temperature):
            self._send_temperature(round_half(new_temperature))
        elif (
            learn
            and self.store is not None
            and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(hours = 2)
            and self.ADapi.datetime(aware=True) - self.heater_temp_last_registered > datetime.timedelta(hours = 2)
        ):
            self._registerHeatingtemp(heater_temp = new_temperature)
            self.heater_temp_last_registered = self.ADapi.datetime(aware=True)

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

        if self.prev_in_temp > in_temp:
            # Temp is dropping
            if in_temp > self.target_indoor_temp:
                diff = abs(in_temp - self.target_indoor_temp)
                drop = abs(self.prev_in_temp - in_temp)
                if diff / drop < 6:
                # Set temp is already lowering temperature and will reach target within 6 x 720 seconds
                    self.prev_in_temp = in_temp
                    return heater_set_temp
                else:
                    valid_temp_data = False

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
                    return persistent_temperature

        elif self.prev_in_temp < in_temp:
            # Temp is increasing
            if in_temp < self.target_indoor_temp:
                diff = abs(in_temp - self.target_indoor_temp)
                increase = abs(in_temp -self.prev_in_temp)
                if diff / increase < 6:
                    # Set temp is already increasing temperature
                    self.prev_in_temp = in_temp
                    return heater_set_temp
                else:
                    valid_temp_data = False

            new_temperature += adjust_temp_by

            if (
                valid_temp_data
                and round_half(new_temperature) > round_half(persistent_temperature)
            ):
                self.prev_in_temp = in_temp
                return persistent_temperature

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


class Aircondition(Heater):
    """ Heat pump / AC with heat, cool and fan_only. """

    hvac:bool = True

    def __init__(self, app, cfg:dict):
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

    def _set_hvac_mode(self, mode:str) -> None:
        self.ADapi.call_service('climate/set_hvac_mode',
            entity_id = self.heater,
            hvac_mode = mode,
            namespace = self.namespace
        )

    def _heat_setpoint(self, heater_temp:float, in_temp:float, real_in_temp:float) -> float:
        if self.target_mode == 'table':
            if self.power_state == 'reduce':
                # As a table heater: the lowest of the save and the vacation temperature
                return self._reduce_temperatures()[1]
            return self._table_setpoint(real_in_temp, vacation = False)
        return self._indoor_setpoint(heater_temp = heater_temp, in_temp = in_temp, mode = 'heat')

    def _silence(self) -> None:
        """ Set silence preset for HVAC enabled devices """
        fan_modes = self.ADapi.get_state(self.heater, attribute = 'fan_modes', namespace = self.namespace)
        if fan_modes is None or 'Silence' not in fan_modes:
            return
        change_fan_mode_to_silence = False
        for time in self.silence:
            if 'start' in time and 'stop' in time:
                if (
                    self.ADapi.now_is_between(time['start'], time['stop'])
                    and not self.vacation_state
                ):
                    if 'presence' in time:
                        for presence in time['presence']:
                            if self.ADapi.get_state(presence, namespace = self.namespace) == 'home':
                                change_fan_mode_to_silence = True
                    else:
                        change_fan_mode_to_silence = True
                    break

        fan_mode_now = self.ADapi.get_state(self.heater, attribute = 'fan_mode', namespace = self.namespace)
        if change_fan_mode_to_silence and fan_mode_now != 'Silence':
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

        elif not change_fan_mode_to_silence and fan_mode_now == 'Silence':
            self.ADapi.call_service('climate/set_fan_mode',
                entity_id = self.heater,
                fan_mode = self.fan_mode_persistent,
                namespace = self.namespace
            )

    def _set_indoortemp(self, **kwargs) -> None:
        if self._automation_off():
            return
        self._note_entity_state()
            # Set variables from indoor sensor and heater
        in_temp = self.get_in_temp()
        if in_temp is None:
            self.ADapi.log(f"{self.heater}: indoor temp is None. Aborting setting new temperature", level = 'INFO')
            return
        real_in_temp = in_temp
        heater_temp = self.get_heater_temp()
        ac_state = self.ADapi.get_state(self.heater, namespace = self.namespace)

        self._silence()
        self._window_notifications(real_in_temp)

        if ac_state == 'heat':
            if heater_temp is None:
                return

            if self.windows_is_open:
                self._clear_power_offset()
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
            else:
                new_temperature = self._heat_setpoint(heater_temp, in_temp, real_in_temp)
                learn = self.target_mode == 'indoor' and not shifted and self.power_state == 'normal'

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
                        self._set_hvac_mode('fan_only')
                        self.ADapi.run_in(self._set_indoortemp, 5) # To set silence to new fan mode, if time for it
                        return
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
