""" ClimateCommander configuration as pydantic models.

    AppConfig        the app-level keys of one ClimateCommander app (apps.yaml).
    DeviceConfig     one climate entity, from a one-device app or an entry of the deprecated
                     HVAC: / Heaters: lists. DeviceConfig.runtime() returns the dict that
                     cc_devices.Heater / Aircondition read.

    Unknown keys (module, class, dependencies, ...) are ignored. A string where a list is
    expected becomes a one-item list, None becomes an empty list. Defaults are the ones the app
    used before the models existed.

    @Pythm / https://github.com/Pythm
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Any, Dict, List, Union

from pydantic import (
    AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator,
)

# Keys a device may inherit from the app level in the old list style
APP_LEVEL_KEYS = (
    'vacation_temp', 'max_vacation_temp', 'screening_temp', 'getting_cold', 'getting_hot_offset',
    'rain_level', 'anemometer_speed', 'spend_temp_offset', 'notify_receiver', 'cloud_cover',
)
INDOOR_INTERVAL = 1800  # seconds between runs, indoor-target mode (30 min; was 12 min before 1.4.0 dev, interval_minutes: 12 gives that back)
TABLE_INTERVAL = 900    # seconds between runs, table mode (as the ElectricalManagement heaters)
                        # (interval_minutes replaces both for one device)

UNIT_DEFAULTS = {
    'C': {'max_vacation_temp': 30, 'vacation_temp': 16, 'screening_temp': 8,
          'getting_cold': 18, 'target_indoor_temp': 22.7,
          'unit': 'C', 'comfort_guard': 1.5, 'solar_max': 3.0, 'max_setpoint_drop': 3.0,
          'cold_start_deficit': 2.0},
    'F': {'max_vacation_temp': 86, 'vacation_temp': 61, 'screening_temp': 47,
          'getting_cold': 65, 'target_indoor_temp': 72.8,
          'unit': 'F', 'comfort_guard': 2.7, 'solar_max': 5.4, 'max_setpoint_drop': 5.4,
          'cold_start_deficit': 3.6},
}

CONTROLLERS = ('classic', 'shadow', 'adaptive')
# Keys that only the shadow / adaptive controller reads (classic ignores them with one INFO line)
ADAPTIVE_KEYS = (
    'window_offset_table', 'internal_offset_table', 'target_table', 'outdoor_gain', 'solar_gain',
    'solar_max', 'comfort_guard', 'diagnostic_sensor',
)
OFFSET_LIMIT = 30   # an offset table value further from 0 than this is a typo (no sign or unit assumption)
ENTITY_ID = re.compile(r'^[a-z0-9_]+\.[a-z0-9_]+$')


def _as_list(value: Any) -> Any:
    """ None -> [], one value -> [value], list unchanged. """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class _Model(BaseModel):
    model_config = ConfigDict(extra='ignore', populate_by_name=True)


class TemperatureEntry(BaseModel):
    """ One row of the outdoor-temperature table. `out` is required; the other keys
        (offset, normal, save, vacation, ...) are passed on as written. """
    model_config = ConfigDict(extra='allow')

    out: float


class OffsetEntry(BaseModel):
    """ One row of window_offset_table / internal_offset_table (controller shadow / adaptive): the offset
        (window or climate sensor minus the room) from `out` and up, optional extra when windy / raining.
        Unknown keys are an error here, so a typo is reported. """
    model_config = ConfigDict(extra='forbid')

    out: float
    offset: float
    wind: float | None = None
    rain: float | None = None

    @field_validator('out', 'offset', 'wind', 'rain')
    @classmethod
    def _finite(cls, value: Any) -> Any:
        if value is not None and (not math.isfinite(value) or abs(value) > OFFSET_LIMIT):
            raise ValueError(f"must be a number between -{OFFSET_LIMIT} and {OFFSET_LIMIT}")
        return value


class TargetEntry(BaseModel):
    """ One row of target_table: from `out` (24 h mean outdoor temperature) and up, either an absolute
        `target` or an `offset` added to target_indoor_temp / target_indoor_input. """
    model_config = ConfigDict(extra='forbid')

    out: float
    target: float | None = None
    offset: float | None = None

    @model_validator(mode='after')
    def _one_of(self) -> 'TargetEntry':
        if (self.target is None) == (self.offset is None):
            raise ValueError("give exactly one of 'target' or 'offset'")
        return self


def _sorted_by_out(entries: list) -> list:
    """ Sorted by `out`; the same `out` twice is an error (which row applies would be a guess). """
    entries = sorted(entries, key=lambda e: e.out)
    for a, b in zip(entries, entries[1:]):
        if a.out == b.out:
            raise ValueError(f"two entries with out {a.out}")
    return entries


class DayPeriod(BaseModel):
    """ A daytime_savings / daytime_increasing / silence period. """
    model_config = ConfigDict(extra='allow')

    start: str | None = None
    stop: str | None = None
    presence: List[str] | None = None

    @field_validator('presence', mode='before')
    @classmethod
    def _presence_list(cls, value: Any) -> Any:
        return None if value is None else _as_list(value)


class ScreenConfig(BaseModel):
    """ One screening cover. An entry without `screen` is skipped with a WARNING in cc_devices. """
    model_config = ConfigDict(extra='ignore')

    screen: str | None = None
    windowsensors: List[str] = Field(default_factory=list)
    lux_close: int = 40000
    lux_open: int = 15000
    lux_open_media: int = 4000
    not_when_home: List[str] = Field(default_factory=list)
    mediaplayers: List[str] = Field(default_factory=list)

    @field_validator('windowsensors', 'not_when_home', 'mediaplayers', mode='before')
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _as_list(value)


class AppConfig(_Model):
    """ The app-level arguments of one ClimateCommander app. """

    HASS_namespace: str = 'default'
    MQTT_namespace: str = 'mqtt'
    notify_app: str | None = None
    notify_receiver: Any = None
    weather_app: str = 'weather'
    unit_of_measurement: str | None = None
    json_path: str | None = None

    vacation: str | None = None
    away_state: str | None = None          # deprecated, use `vacation`

    # Deprecated weather sensors
    outside_temperature: str | None = None
    rain_sensor: str | None = None
    anemometer: str | None = None

    # Own outdoor lux sensors
    OutLux_sensor: str | None = None
    OutLuxMQTT: str | None = None
    OutLux_sensor_2: str | None = None
    OutLuxMQTT_2: str | None = None

    # Device lists of the deprecated style
    HVAC: List[Dict[str, Any]] = Field(default_factory=list)
    Heaters: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator('HVAC', 'Heaters', mode='before')
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _as_list(value)

    def deprecated_keys(self) -> List[str]:
        """ Deprecated keys that are present in the config. """
        present = []
        for key in ('outside_temperature', 'rain_sensor', 'anemometer', 'away_state'):
            if key in self.model_fields_set:
                present.append(key)
        return present


class DeviceConfig(_Model):
    """ One climate entity. """

    climate: str
    type: str = 'heater'
    namespace: str | None = None
    target_mode: str | None = None

    indoor_sensor_temp: str | None = None
    backup_indoor_sensor_temp: str | None = None
    window_temp: str | None = Field(default=None, validation_alias=AliasChoices('window_sensor_temp', 'window_temp'))
    window_offset: float = -3

    target_indoor_input: str | None = None
    target_indoor_temp: float | None = None
    target_heater_temp: float | None = None
    target_heater_input: str | None = None
    temperatures: List[TemperatureEntry] = Field(default_factory=list)

    save_temp: float | None = None
    save_temp_offset: float | None = None
    spend_temp_offset: float = 1
    vacation: str | None = None
    vacation_temp: float | None = None
    max_vacation_temp: float | None = None
    vacation_keep_off: bool = False
    options: List[str] = Field(default_factory=list)   # flags: vacation_keep_off, has_fireplace (unknown names ignored)

    interval_minutes: float | None = Field(default=None, ge=1, le=120)   # None: 30 min indoor, 15 min table
    trend_hold_hours: float = Field(default=2.0, ge=0, le=12)   # classic: no setpoint move while the room reaches target within this
    max_setpoint_drop: float | None = Field(default=None, gt=0, le=15)   # HVAC heat, indoor: lowest setpoint target - this. None: UNIT_DEFAULTS
    cold_start_deficit: float | None = Field(default=None, ge=0.5, le=10)   # indoor: vacation end with the room this far below target: cold start. None: UNIT_DEFAULTS

    rain_level: float = 3
    anemometer_speed: float = 40
    daytime_savings: List[DayPeriod] = Field(default_factory=list)
    daytime_increasing: List[DayPeriod] = Field(default_factory=list)
    silence: List[DayPeriod] = Field(default_factory=list)
    automate: Union[str, bool, None] = None
    windowsensors: List[str] = Field(default_factory=list)

    screening: List[ScreenConfig] = Field(default_factory=list)
    screening_temp: float | None = None
    cloud_cover: float = 90   # screens close only while the cloud cover (%) is below this
    getting_cold: float | None = None
    getting_hot_offset: float = 10
    notify_receiver: Any = Field(default=None, validation_alias=AliasChoices('notify_receiver', 'recipient'))

    # Indoor controller (cc_control.py). Everything below is read only by `shadow` and `adaptive`.
    controller: str = 'classic'
    window_offset_table: List[OffsetEntry] = Field(default_factory=list)
    internal_offset_table: List[OffsetEntry] = Field(default_factory=list)
    target_table: List[TargetEntry] = Field(default_factory=list)
    outdoor_gain: float | None = Field(default=None, ge=0, le=0.6)   # None: only the learned internal offset
    solar_gain: float = Field(default=1.0, ge=0, le=5)
    solar_max: float | None = Field(default=None, gt=0)              # None: UNIT_DEFAULTS
    comfort_guard: float | None = Field(default=None, gt=0)          # None: UNIT_DEFAULTS
    diagnostic_sensor: str | None = None

    @model_validator(mode='before')
    @classmethod
    def _heater_alias(cls, data: Any) -> Any:
        """ `heater` is the ElectricalManagement name for `climate`. """
        if isinstance(data, dict) and not data.get('climate') and data.get('heater'):
            data = {**data, 'climate': data['heater']}
        return data

    @field_validator('type', mode='before')
    @classmethod
    def _type(cls, value: Any) -> Any:
        return 'heater' if value is None else str(value).lower()

    @field_validator('temperatures', 'daytime_savings', 'daytime_increasing', 'silence', 'screening',
                     'windowsensors', 'options', mode='before')
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return _as_list(value)

    @field_validator('controller', mode='before')
    @classmethod
    def _controller(cls, value: Any) -> Any:
        value = 'classic' if value is None else str(value).lower()
        if value not in CONTROLLERS:
            raise ValueError(f"use one of {', '.join(CONTROLLERS)}")
        return value

    @field_validator('window_offset_table', 'internal_offset_table', 'target_table', mode='before')
    @classmethod
    def _table_lists(cls, value: Any) -> Any:
        return _as_list(value)

    @field_validator('window_offset_table', 'internal_offset_table', 'target_table')
    @classmethod
    def _tables_sorted(cls, value: Any) -> Any:
        return _sorted_by_out(value)

    @field_validator('diagnostic_sensor')
    @classmethod
    def _entity_id(cls, value: Any) -> Any:
        if value is not None and not ENTITY_ID.match(value):
            raise ValueError("must be an entity id such as sensor.living_room_controller")
        return value

    @property
    def kind(self) -> str:
        return self.type if self.type in ('heater', 'hvac') else 'heater'

    @property
    def has_fireplace(self) -> bool:
        """ HVAC: heat -> fan_only straight at target + 0.6, without lowering the setpoint first. """
        return 'has_fireplace' in self.options

    def runtime(self, *, defaults: dict, hass_namespace: str, vacation_state: str | None) -> dict:
        """ The dict cc_devices.Heater / Aircondition read. `defaults` is UNIT_DEFAULTS[unit]. """
        fields = self.model_fields_set

        temperatures = sorted((t.model_dump(exclude_unset=True) for t in self.temperatures),
                              key=lambda d: d['out'])

        target_mode = self.target_mode
        if target_mode not in ('indoor', 'table'):
            if self.temperatures or 'target_heater_temp' in fields or 'target_heater_input' in fields:
                target_mode = 'table'
            else:
                target_mode = 'indoor'

        target_indoor_temp = self.target_indoor_temp if self.target_indoor_temp is not None else defaults['target_indoor_temp']
        target_heater_input = self.target_heater_input
        if target_heater_input is None and 'target_heater_temp' not in fields:
            # As ElectricalManagement 1.0.3: without own heater keys the indoor target is the heater target
            target_heater_input = self.target_indoor_input

        if target_mode == 'table':
            vacation_temp = self.vacation_temp
        else:
            vacation_temp = self.vacation_temp if self.vacation_temp is not None else defaults['vacation_temp']

        if self.interval_minutes is not None:
            interval = int(round(self.interval_minutes * 60))
        else:
            interval = TABLE_INTERVAL if target_mode == 'table' else INDOOR_INTERVAL

        def dump(items) -> list:
            return [i.model_dump(exclude_unset=True) for i in items]

        return {
            'climate': self.climate,
            'namespace': self.namespace or hass_namespace,
            'target_mode': target_mode,
            'interval': interval,
            'start_delay': 10 + zlib.crc32(self.climate.encode()) % interval,
            'indoor_sensor_temp': self.indoor_sensor_temp,
            'backup_indoor_sensor_temp': self.backup_indoor_sensor_temp,
            'window_temp': self.window_temp,
            'window_offset': self.window_offset,
            'target_indoor_input': self.target_indoor_input,
            'target_indoor_temp': target_indoor_temp,
            'target_heater_temp': self.target_heater_temp if self.target_heater_temp is not None else target_indoor_temp,
            'target_heater_input': target_heater_input,
            'temperatures': temperatures,
            'save_temp': self.save_temp,
            'save_temp_offset': self.save_temp_offset,
            'spend_temp_offset': self.spend_temp_offset,
            'vacation_temp': vacation_temp,
            'max_vacation_temp': self.max_vacation_temp if self.max_vacation_temp is not None else defaults['max_vacation_temp'],
            'rain_level': self.rain_level,
            'anemometer_speed': self.anemometer_speed,
            'daytime_savings': dump(self.daytime_savings),
            'daytime_increasing': dump(self.daytime_increasing),
            'silence': dump(self.silence),
            'automate': self.automate if isinstance(self.automate, str) else None,
            'windowsensors': list(self.windowsensors),
            'screening': dump(self.screening),
            'cloud_cover': self.cloud_cover,
            'screening_temp': self.screening_temp if self.screening_temp is not None else defaults['screening_temp'],
            'getting_cold': self.getting_cold if self.getting_cold is not None else defaults['getting_cold'],
            'getting_hot_offset': self.getting_hot_offset,
            'vacation': self.vacation if 'vacation' in fields else vacation_state,
            'vacation_keep_off': self.vacation_keep_off or 'vacation_keep_off' in self.options,
            'has_fireplace': self.has_fireplace,
            'trend_hold_hours': self.trend_hold_hours,
            'max_setpoint_drop': self.max_setpoint_drop if self.max_setpoint_drop is not None else defaults['max_setpoint_drop'],
            'cold_start_deficit': self.cold_start_deficit if self.cold_start_deficit is not None else defaults['cold_start_deficit'],
            'notify_receiver': [self.notify_receiver] if isinstance(self.notify_receiver, str) else self.notify_receiver,
            # Indoor controller (cc_control.py)
            'controller': self.controller,
            'unit': defaults.get('unit', 'C'),
            'window_offset_prior': self.window_offset if 'window_offset' in fields else None,
            'window_offset_table': dump(self.window_offset_table),
            'internal_offset_table': dump(self.internal_offset_table),
            'target_table': dump(self.target_table),
            'outdoor_gain': self.outdoor_gain if self.outdoor_gain is not None else 0.0,
            'solar_gain': self.solar_gain,
            'solar_max': self.solar_max if self.solar_max is not None else defaults['solar_max'],
            'comfort_guard': self.comfort_guard if self.comfort_guard is not None else defaults['comfort_guard'],
            'diagnostic_sensor': self.diagnostic_sensor,
            'adaptive_keys': [k for k in ADAPTIVE_KEYS if k in fields],
        }


def format_errors(error: ValidationError) -> str:
    """ One line per problem: `key: message`. """
    lines = []
    for e in error.errors():
        key = '.'.join(str(p) for p in e['loc']) or '(config)'
        lines.append(f"{key}: {e['msg']}")
    return '; '.join(lines)
