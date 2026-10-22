""" ClimateCommander by Pythm
    Control your climate entities (heaters and HVAC) and your screening covers based on weather
    and indoor sensors, optionally with save/spend states from ElectricalManagement.

    One app per climate entity (top-level `climate:` key) is the recommended setup. The older
    style with `HVAC:` / `Heaters:` lists in one app still works but is deprecated.

    @Pythm / https://github.com/Pythm
"""

__version__ = "1.4.0"

from appdaemon import adbase as ad
import datetime
import glob
import json
import os
import zlib

from weather_data import OutdoorWeather
from cc_devices import Heater, Aircondition, Notify_Mobiles, to_float

# Keys a device may inherit from the app level in the old list style
APP_LEVEL_KEYS = (
    'vacation_temp', 'max_vacation_temp', 'screening_temp', 'getting_cold', 'getting_hot_offset',
    'rain_level', 'anemometer_speed', 'spend_temp_offset',
)
INDOOR_INTERVAL = 720   # seconds between runs, indoor-target mode (unchanged from earlier versions)
TABLE_INTERVAL = 900    # seconds between runs, table mode (as the ElectricalManagement heaters)


class WeatherState:
    """ Outdoor values for all devices in one app. Replaces the module globals used before 1.4.0,
        which were shared by every ClimateCommander app in the same AppDaemon. """

    def __init__(self):
        self.out_temp:float = 10.0
        self.out_temp_known:bool = False
        self.rain:float = 0.0
        self.wind:float = 0.0
        self.lux:float = 0.0
        self.cloud_cover:float = 0


class JsonStore:
    """ The app's JSON file, read once at startup and written when learned data changes. """

    def __init__(self, ADapi, path:str):
        self.ADapi = ADapi
        self.path = path
        self.data:dict = {}
        try:
            with open(path, 'r') as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except (OSError, ValueError) as e:
            self.ADapi.log(f"Could not read {path}: {e}. Starting with empty data.", level = 'WARNING')
            self.data = {}

    def device(self, entity:str) -> dict:
        return self.data.setdefault(entity, {"data" : {}})

    def has_device(self, entity:str) -> bool:
        return entity in self.data

    def save(self) -> None:
        try:
            with open(self.path, 'w') as f:
                json.dump(self.data, f, indent = 4)
        except OSError as e:
            self.ADapi.log(f"Could not write {self.path}: {e}", level = 'WARNING')


class _WeatherApi:
    """ OutdoorWeather (weather_data.py, shared with Lightwand) calls ADAPI methods plus
        get_plugin_api on the object it gets. An ADBase app has the latter on itself. """

    def __init__(self, app):
        self._app = app

    def __getattr__(self, name):
        return getattr(self._app.ADapi, name)

    def get_plugin_api(self, name):
        return self._app.get_plugin_api(name)


class Climate(ad.ADBase):

    def initialize(self):
        self.ADapi = self.get_ad_api()
        self.devices:list = []
        self.store = None

            # Namespaces for HASS and MQTT
        self.HASS_namespace = self.args.get('HASS_namespace', 'default')
        MQTT_namespace = self.args.get('MQTT_namespace', 'mqtt')

            # Set up your own notification app
        name_of_notify_app = self.args.get('notify_app', None)
        if name_of_notify_app is not None:
            self.notify_app = self.ADapi.get_app(name_of_notify_app)
            if self.notify_app is None:
                self.ADapi.log(f"Notify app '{name_of_notify_app}' not found. No notifications.", level = 'WARNING')
        else:
            self.notify_app = Notify_Mobiles(self.ADapi, self.HASS_namespace)

            # Default vacation state for saving purposes
        if 'away_state' in self.args:
            vacation_state = self.args['away_state']
            self.ADapi.log("Please change 'away_state' to 'vacation' in your configuration. This option will be removed in an upcoming release.", level = 'WARNING')
        elif 'vacation' in self.args:
            vacation_state = self.args['vacation']
        else:
            vacation_state = None
            # Convention shared with ElectricalManagement and ModeManagement: used only if it exists
            if self.ADapi.entity_exists('input_boolean.vacation', namespace = self.HASS_namespace):
                vacation_state = 'input_boolean.vacation'

        self.weather = WeatherState()

            # Weather app. Outdoor temperature, rain, wind and lux are published by the
            # Weather app (https://github.com/Pythm/ad-Weather) in the WEATHER_CHANGE event.
        weather_app_name = self.args.get('weather_app', 'weather')
        weather_app = self.ADapi.get_app(weather_app_name)
        if weather_app is None:
            self.ADapi.log(
                f"Weather app '{weather_app_name}' not found. ClimateCommander needs "
                "https://github.com/Pythm/ad-Weather for outdoor temperature, rain, wind and lux. "
                "Set 'weather_app' if your Weather app has another name.",
                level = 'ERROR'
            )

            # Weather sensors. Deprecated: configure them in the Weather app.
        for deprecated in ('outside_temperature', 'rain_sensor', 'anemometer'):
            if deprecated in self.args:
                self.ADapi.log(
                    f"'{deprecated}' is deprecated. Configure it in the Weather app instead. "
                    "It still works and takes priority while it reports.",
                    level = 'WARNING'
                )
        self.outside_temperature = self.args.get('outside_temperature', None)
        self.rain_sensor = self.args.get('rain_sensor', None)
        self.anemometer = self.args.get('anemometer', None)
        now = self.ADapi.datetime(aware=True)

        self.out_temp_last_update = now - datetime.timedelta(minutes = 21)
        if self.outside_temperature:
            self.ADapi.listen_state(self._outsideTemperatureUpdated, self.outside_temperature,
                namespace = self.HASS_namespace
            )
            value = to_float(self.ADapi.get_state(self.outside_temperature, namespace = self.HASS_namespace))
            if value is not None:
                self.weather.out_temp = value
                self.weather.out_temp_known = True

        self.rain_last_update = now - datetime.timedelta(minutes = 21)
        if self.rain_sensor:
            self.ADapi.listen_state(self._rainSensorUpdated, self.rain_sensor,
                namespace = self.HASS_namespace
            )
            self.weather.rain = to_float(self.ADapi.get_state(self.rain_sensor, namespace = self.HASS_namespace)) or 0.0

        self.wind_last_update = now - datetime.timedelta(minutes = 21)
        if self.anemometer:
            self.ADapi.listen_state(self._anemometerUpdated, self.anemometer,
                namespace = self.HASS_namespace
            )
            self.weather.wind = to_float(self.ADapi.get_state(self.anemometer, namespace = self.HASS_namespace)) or 0.0

            # Outdoor lux for screen control. Own lux sensors are optional, without them (or while
            # they are silent for 20 minutes) the lux from the Weather app is used.
        self.weather_lux = OutdoorWeather(
            api = _WeatherApi(self),
            on_change = self._out_lux_changed,
            HASS_namespace = self.HASS_namespace,
            MQTT_namespace = MQTT_namespace,
            lux_sensor = self.args.get('OutLux_sensor'),
            lux_sensor_mqtt = self.args.get('OutLuxMQTT'),
            lux_sensor_2 = self.args.get('OutLux_sensor_2'),
            lux_sensor_2_mqtt = self.args.get('OutLuxMQTT_2'),
            min_react_minutes = 0,
        )
        self.weather.lux = self.weather_lux.out_lux

        self.ADapi.listen_event(self.weather_event, 'WEATHER_CHANGE',
            namespace = self.HASS_namespace
        )

            # Devices: one device per app (recommended) or the old lists
        single = self.args.get('climate') or self.args.get('heater')
        raw_devices = []
        if single:
            kind = str(self.args.get('type', 'heater')).lower()
            if kind not in ('heater', 'hvac'):
                self.ADapi.log(f"Unknown type '{kind}' for {single}. Use 'heater' or 'hvac'. Using 'heater'.", level = 'WARNING')
                kind = 'heater'
            raw_devices.append((kind, self.args))
        else:
            raw_devices += [('hvac', ac) for ac in self.args.get('HVAC') or []]
            raw_devices += [('heater', h) for h in self.args.get('Heaters') or []]
            if raw_devices:
                self.ADapi.log(
                    "Configuring several devices with 'HVAC:' / 'Heaters:' lists in one app is deprecated. "
                    "Give each climate entity its own app with 'climate:' and 'type:' (see the README, "
                    "'One app per device'). The lists still work.",
                    level = 'WARNING'
                )

        unit = self._find_unit(raw_devices)
        if unit == 'C':
            self.defaults = {'max_vacation_temp': 30, 'vacation_temp': 16, 'screening_temp': 8,
                             'getting_cold': 18, 'target_indoor_temp': 22.7}
        elif unit == 'F':
            self.defaults = {'max_vacation_temp': 86, 'vacation_temp': 61, 'screening_temp': 47,
                             'getting_cold': 65, 'target_indoor_temp': 72.8}
        else:
            self.ADapi.log(
                "Could not find unit of measurement. Please configure 'unit_of_measurement' with C or F.\n"
                f"Aborting {self.name} setup",
                level = 'WARNING'
            )
            return

            # Persistent storage for learned setpoints and fan mode
        if 'json_path' in self.args:
            path = str(self.args['json_path']) + str(self.name) + '.json'
            self.store = JsonStore(self.ADapi, path)
            changed = False
            for kind, raw in raw_devices:
                entity = raw.get('climate') or raw.get('heater')
                if entity and not self.store.has_device(entity):
                    if single:
                        self._migrate_device_data(entity)
                    self.store.device(entity)
                    changed = True
            if changed:
                self.store.save()

        for kind, raw in raw_devices:
            cfg = self._device_cfg(raw, kind, vacation_state)
            if cfg is None:
                continue
            device_class = Aircondition if kind == 'hvac' else Heater
            self.devices.append(device_class(self, cfg))

            # The Weather app only sends on change. Start from its last values instead of waiting.
        last_sent = getattr(weather_app, 'last_sent', None) if weather_app is not None else None
        if isinstance(last_sent, dict):
            self.weather_event('WEATHER_CHANGE', last_sent)

    # ------------------------------------------------------------------ #
    # Setup helpers
    # ------------------------------------------------------------------ #

    def _find_unit(self, raw_devices) -> str | None:
        unit = self.args.get('unit_of_measurement', None)
        if unit is None and self.outside_temperature:
            unit = self._unit_of(self.outside_temperature, self.HASS_namespace)
        if unit is None:
            for kind in ('hvac', 'heater'):
                for k, raw in raw_devices:
                    if k == kind and raw.get('indoor_sensor_temp'):
                        unit = self._unit_of(raw['indoor_sensor_temp'], raw.get('namespace', self.HASS_namespace))
                        if unit:
                            break
                if unit:
                    break
        if isinstance(unit, str):
            if 'C' in unit.upper():
                return 'C'
            if 'F' in unit.upper():
                return 'F'
        return None

    def _unit_of(self, sensor, namespace):
        uom = self.ADapi.get_state(sensor, attribute = 'unit_of_measurement', namespace = namespace)
        if isinstance(uom, str):
            if 'C' in uom:
                return 'C'
            if 'F' in uom:
                return 'F'
        return None

    def _migrate_device_data(self, entity:str) -> None:
        """ First start of a one-device app: take the learned data for this entity from another
            ClimateCommander JSON file in the same folder (the old list-style app). """
        folder = os.path.dirname(self.store.path) or '.'
        for other in glob.glob(os.path.join(folder, '*.json')):
            if os.path.abspath(other) == os.path.abspath(self.store.path):
                continue
            try:
                with open(other, 'r') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get(entity), dict):
                self.store.data[entity] = data[entity]
                self.ADapi.log(f"Learned data for {entity} copied from {other}.", level = 'INFO')
                return

    def _device_cfg(self, raw:dict, kind:str, vacation_state) -> dict | None:
        """ Resolves one device's configuration. Accepts both the ClimateCommander keys and the
            ElectricalManagement climate keys (heater, window_temp, recipient, options). """
        entity = raw.get('climate') or raw.get('heater')
        if not entity:
            self.ADapi.log(f"Skipping device without 'climate': {raw}", level = 'WARNING')
            return None

        def app_level(key, default):
            if key in raw:
                return raw[key]
            return self.args.get(key, default)

        options = raw.get('options') or []
        temperatures = sorted(raw.get('temperatures') or [], key = lambda d: d.get('out', float('-inf')))
        target_mode = raw.get('target_mode')
        if target_mode not in ('indoor', 'table'):
            if target_mode is not None:
                self.ADapi.log(f"{entity}: unknown target_mode '{target_mode}'. Use 'indoor' or 'table'.", level = 'WARNING')
            if temperatures or 'target_heater_temp' in raw or 'target_heater_input' in raw:
                target_mode = 'table'
            else:
                target_mode = 'indoor'

        target_indoor_temp = raw.get('target_indoor_temp', self.defaults['target_indoor_temp'])
        target_heater_input = raw.get('target_heater_input')
        if target_heater_input is None and 'target_heater_temp' not in raw:
            # As ElectricalManagement 1.0.3: without own heater keys the indoor target is the heater target
            target_heater_input = raw.get('target_indoor_input')

        if target_mode == 'table':
            vacation_temp = app_level('vacation_temp', None)
        else:
            vacation_temp = app_level('vacation_temp', self.defaults['vacation_temp'])

        automate = raw.get('automate')
        interval = TABLE_INTERVAL if target_mode == 'table' else INDOOR_INTERVAL

        return {
            'climate': entity,
            'namespace': raw.get('namespace', self.HASS_namespace),
            'target_mode': target_mode,
            'interval': interval,
            'start_delay': 10 + zlib.crc32(entity.encode()) % interval,
            'indoor_sensor_temp': raw.get('indoor_sensor_temp'),
            'backup_indoor_sensor_temp': raw.get('backup_indoor_sensor_temp'),
            'window_temp': raw.get('window_sensor_temp', raw.get('window_temp')),
            'window_offset': raw.get('window_offset', -3),
            'target_indoor_input': raw.get('target_indoor_input'),
            'target_indoor_temp': target_indoor_temp,
            'target_heater_temp': raw.get('target_heater_temp', target_indoor_temp),
            'target_heater_input': target_heater_input,
            'temperatures': temperatures,
            'save_temp': raw.get('save_temp'),
            'save_temp_offset': raw.get('save_temp_offset'),
            'spend_temp_offset': app_level('spend_temp_offset', 1),
            'vacation_temp': vacation_temp,
            'max_vacation_temp': app_level('max_vacation_temp', self.defaults['max_vacation_temp']),
            'rain_level': app_level('rain_level', 3),
            'anemometer_speed': app_level('anemometer_speed', 40),
            'daytime_savings': raw.get('daytime_savings') or [],
            'daytime_increasing': raw.get('daytime_increasing') or [],
            'silence': raw.get('silence') or [],
            'automate': automate if isinstance(automate, str) else None,
            'windowsensors': raw.get('windowsensors') or [],
            'screening': raw.get('screening') or [],
            'screening_temp': app_level('screening_temp', self.defaults['screening_temp']),
            'getting_cold': app_level('getting_cold', self.defaults['getting_cold']),
            'getting_hot_offset': app_level('getting_hot_offset', 10),
            'vacation': raw.get('vacation', vacation_state),
            'vacation_keep_off': bool(raw.get('vacation_keep_off', False)) or 'vacation_keep_off' in options,
            'notify_receiver': raw.get('notify_receiver', raw.get('recipient', self.args.get('notify_receiver'))),
        }

    # ------------------------------------------------------------------ #
    # Public API (ElectricalManagement)
    # ------------------------------------------------------------------ #

    def get_device(self, climate:str | None = None):
        """ The device for a climate entity. Without an entity: the only device of this app. """
        if climate is None:
            return self.devices[0] if len(self.devices) == 1 else None
        for device in self.devices:
            if device.heater == climate:
                return device
        return None

    # ------------------------------------------------------------------ #
    # Weather
    # ------------------------------------------------------------------ #

    def weather_event(self, event_name, data, **kwargs) -> None:
        """ Listens for weather change from the weather app. It sends None for data it does not have. """
        now = self.ADapi.datetime(aware=True)
        temp = to_float(data.get('temp'))
        if temp is not None and now - self.out_temp_last_update > datetime.timedelta(minutes = 20):
            self.weather.out_temp = temp
            self.weather.out_temp_known = True
        rain = to_float(data.get('rain'))
        if rain is not None and now - self.rain_last_update > datetime.timedelta(minutes = 20):
            self.weather.rain = rain
        wind = to_float(data.get('wind'))
        if wind is not None and now - self.wind_last_update > datetime.timedelta(minutes = 20):
            self.weather.wind = wind
            for device in self.devices:
                if wind >= device.anemometer_speed:
                    device.last_windy_time = now
        cloud_cover = to_float(data.get('cloud_cover'))
        if cloud_cover is not None:
            self.weather.cloud_cover = int(cloud_cover)

    def _outsideTemperatureUpdated(self, entity, attribute, old, new, **kwargs) -> None:
        value = to_float(new)
        if value is not None:
            self.weather.out_temp = value
            self.weather.out_temp_known = True
            self.out_temp_last_update = self.ADapi.datetime(aware=True)

    def _rainSensorUpdated(self, entity, attribute, old, new, **kwargs) -> None:
        value = to_float(new)
        if value is None:
            self.weather.rain = 0.0
            self.ADapi.log(f"Not able to set new rain amount: {new}.", level = 'DEBUG')
            return
        self.weather.rain = value
        self.rain_last_update = self.ADapi.datetime(aware=True)
        if value > 0.0:
            for device in self.devices:
                device.tryScreenOpen()

    def _anemometerUpdated(self, entity, attribute, old, new, **kwargs) -> None:
        value = to_float(new)
        if value is None:
            self.weather.wind = 0.0
            self.ADapi.log(f"Not able to set new wind amount: {new}.", level = 'DEBUG')
            return
        self.weather.wind = value
        now = self.ADapi.datetime(aware=True)
        self.wind_last_update = now
        for device in self.devices:
            if value >= device.anemometer_speed:
                device.last_windy_time = now

    def _out_lux_changed(self) -> None:
        """ LUX from the Weather app or own lux sensors, see weather_data.py """
        self.weather.lux = self.weather_lux.out_lux
        for device in self.devices:
            device.tryScreenOpen()
