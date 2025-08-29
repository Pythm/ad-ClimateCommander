""" CLimate Control by Pythm
    Control your Climate entities and your screening covers based on weather sensors

    @Pythm / https://github.com/Pythm
"""

__version__ = "1.2.4"

from appdaemon.plugins.hass.hassapi import Hass
import datetime
import math
import json
import csv

global OUT_TEMP
OUT_TEMP:float = 10.0
global RAIN_AMOUNT
RAIN_AMOUNT:float = 0.0
global WIND_AMOUNT
WIND_AMOUNT:float = 0.0
global OUT_LUX
OUT_LUX:float = 0.0
global CLOUD_COVER
CLOUD_COVER:int = 0
global JSON_PATH
JSON_PATH:str = None

class Climate(Hass):

    def initialize(self):

        self.mqtt = None # Only initialize MQTT API if needed

            # Set up your own notification app
        name_of_notify_app = self.args.get('notify_app', None)
        notify_receiver = self.args.get('notify_receiver', None)

            # Namespaces for HASS and MQTT
        HASS_namespace = self.args.get('HASS_namespace', 'default')
        MQTT_namespace = self.args.get('MQTT_namespace', 'mqtt')
    
            # Default away state for saving purposes
        if 'away_state' in self.args:
            vacation_state = self.args['away_state']
            self.log("Please change 'away_state' to 'vacation' in your configuration. This option will be removed in an upcoming release.", level = 'WARNING')
        elif 'vacation' in self.args:
            vacation_state = self.args['vacation']
        else:
            vacation_state = None
            if self.entity_exists('input_boolean.vacation',
                namespace = HASS_namespace
            ):
                vacation_state = 'input_boolean.vacation'

        self.heatingdevice:list = []
        self.unit_of_measurement = self.args.get('unit_of_measurement', None)

            # Weather sensors
        self.outside_temperature = self.args.get('outside_temperature', None)
        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level:float = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed:int = self.args.get('anemometer_speed',40)
        
            # Setup Outside temperatures
        global OUT_TEMP
        self.out_temp_last_update = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.outside_temperature:
            self.listen_state(self._outsideTemperatureUpdated, self.outside_temperature)
            try:
                OUT_TEMP = float(self.get_state(self.outside_temperature))
            except (ValueError, TypeError):
                self.log(f"Outside temperature is not valid. {e}", level = 'DEBUG')
            else:
                self._set_unit_of_measurement(self.outside_temperature)

            # Setup Rain sensor
        global RAIN_AMOUNT
        self.rain_last_update = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.rain_sensor:
            self.listen_state(self._rainSensorUpdated, self.rain_sensor)
            try:
                RAIN_AMOUNT = float(self.get_state(self.rain_sensor))
            except (ValueError) as ve:
                RAIN_AMOUNT = 0.0
                self.log(f"Rain sensor not valid. {ve}", level = 'DEBUG')

            # Setup Wind sensor
        global WIND_AMOUNT
        self.wind_last_update = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.anemometer:
            self.listen_state(self._anemometerUpdated, self.anemometer)
            try:
                WIND_AMOUNT = float(self.get_state(self.anemometer))
            except (ValueError) as ve:
                WIND_AMOUNT = 0.0
                self.log(f"Anemometer sensor not valid. {ve}", level = 'DEBUG')

            # Setup Lux sensors
        self.outLux1:float = 0.0
        self.outLux2:float = 0.0
            # Helpers for last updated when two outdoor lux sensors are in use
        self.lux_last_update1 = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        self.lux_last_update2 = self.datetime(aware=True) - datetime.timedelta(minutes = 20)

        if 'OutLux_sensor' in self.args:
            lux_sensor = self.args['OutLux_sensor']
            self.listen_state(self._out_lux_state, lux_sensor,
                namespace = HASS_namespace
            )
        if 'OutLuxMQTT' in self.args:
            if self.mqtt is None:
                self.mqtt = self.get_plugin_api("MQTT")
            out_lux_sensor = self.args['OutLuxMQTT']
            self.mqtt.mqtt_subscribe(out_lux_sensor)
            self.mqtt.listen_event(self._out_lux_event_MQTT, "MQTT_MESSAGE",
                topic = out_lux_sensor,
                namespace = MQTT_namespace
            )

        if 'OutLux_sensor_2' in self.args:
            lux_sensor = self.args['OutLux_sensor_2']
            self.listen_state(self._out_lux_state2, lux_sensor,
                namespace = HASS_namespace
            )
        if 'OutLuxMQTT_2' in self.args:
            if self.mqtt is None:
                self.mqtt = self.get_plugin_api("MQTT")
            out_lux_sensor = self.args['OutLuxMQTT_2']
            self.mqtt.mqtt_subscribe(out_lux_sensor)
            self.mqtt.listen_event(self.__out_lux_event_MQTT2, "MQTT_MESSAGE",
                topic = out_lux_sensor,
                namespace = MQTT_namespace
            )

        self.listen_event(self.weather_event, 'WEATHER_CHANGE',
            namespace = HASS_namespace
        )

        climates = self.args.get('HVAC', [])
        if self.unit_of_measurement is None:
            for ac in climates:
                indoor_sensor_temp = ac.get('indoor_sensor_temp', None)
                if self._set_unit_of_measurement(indoor_sensor_temp):
                    break

        heaters = self.args.get('Heaters', [])
        if self.unit_of_measurement is None:
            for heater in heaters:
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None)
                if self._set_unit_of_measurement(indoor_sensor_temp):
                    break

        if self.unit_of_measurement == 'c':
            self.unit_of_measurement = 'C'
        elif self.unit_of_measurement == 'f':
            self.unit_of_measurement = 'F'

        if self.unit_of_measurement == 'C':
            max_vacation_temp = self.args.get('max_vacation_temp', 30)
            vacation_temp = self.args.get('vacation_temp', 16)
            screening_temp = self.args.get('screening_temp', 8)
            getting_cold = self.args.get('getting_cold', 18)
            target_indoor_temp = 22.7
        elif self.unit_of_measurement == 'F':
            max_vacation_temp = self.args.get('max_vacation_temp', 86)
            vacation_temp = self.args.get('vacation_temp', 61)
            screening_temp = self.args.get('screening_temp', 47)
            getting_cold = self.args.get('getting_cold', 65)
            target_indoor_temp = 72.8
        else:
            self.log(
                "Could not find unit of measurement. Please configure 'unit_of_measurement' with C or F.\n"
                f"Aborting {self.name} setup",
                level = 'WARNING'
            )
            return

            # Persistent storage for storing mode and lux data
        if 'json_path' in self.args:
            global JSON_PATH
            JSON_PATH = self.args['json_path']
            JSON_PATH += str(self.name) + '.json'

            # Configuration of Heatpumps to command
        for ac in climates:
            aircondition = Aircondition(self,
                heater = ac['climate'],
                indoor_sensor_temp = ac.get('indoor_sensor_temp', None),
                backup_indoor_sensor_temp = ac.get('backup_indoor_sensor_temp', None),
                window_temp = ac.get('window_sensor_temp', None),
                window_offset = ac.get('window_offset', -3),
                target_indoor_input = ac.get('target_indoor_input', None),
                target_indoor_temp = ac.get('target_indoor_temp', target_indoor_temp),
                vacation_temp = ac.get('vacation_temp', vacation_temp),
                max_vacation_temp = ac.get('max_vacation_temp', max_vacation_temp),
                rain_level = ac.get('rain_level', self.rain_level),
                anemometer_speed = ac.get('anemometer_speed', self.anemometer_speed),

                daytime_savings = ac.get('daytime_savings', []),
                daytime_increasing = ac.get('daytime_increasing', []),
                silence = ac.get('silence', []),

                automate = ac.get('automate', None),

                windowsensors = ac.get('windowsensors', []),
                screens = ac.get('screening', []),
                screening_temp = ac.get('screening_temp', screening_temp),
                getting_cold = ac.get('getting_cold', getting_cold),

                namespace = ac.get('namespace', HASS_namespace),
                vacation = ac.get('vacation', vacation_state),
                name_of_notify_app = name_of_notify_app,
                notify_receiver = ac.get('notify_receiver', notify_receiver)
            )
            self.heatingdevice.append(aircondition)

            # Configuration of heaters to command
        heaters = self.args.get('Heaters', [])
        for heater in heaters:
            heating = Heater(self,
                heater = heater['climate'],
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None),
                backup_indoor_sensor_temp = ac.get('backup_indoor_sensor_temp', None),
                window_temp = heater.get('window_sensor_temp', None),
                window_offset = heater.get('window_offset', -3),
                target_indoor_input = heater.get('target_indoor_input', None),
                target_indoor_temp = heater.get('target_indoor_temp', target_indoor_temp),
                vacation_temp = heater.get('vacation_temp', vacation_temp),
                rain_level = heater.get('rain_level', self.rain_level),
                anemometer_speed = heater.get('anemometer_speed', self.anemometer_speed),

                daytime_savings = heater.get('daytime_savings', {}),
                daytime_increasing = heater.get('daytime_increasing', {}),

                automate = heater.get('automate', None),

                windowsensors = heater.get('windowsensors', []),
                screens = heater.get('screening', {}),
                screening_temp = heater.get('screening_temp', screening_temp),
                getting_cold = heater.get('getting_cold', getting_cold),

                namespace = heater.get('namespace', HASS_namespace),
                vacation = heater.get('vacation', vacation_state),
                name_of_notify_app = name_of_notify_app,
                notify_receiver = heater.get('notify_receiver', notify_receiver)
            )
            self.heatingdevice.append(heating)

        if JSON_PATH is not None:
            try:
                with open(JSON_PATH, 'r') as json_read:
                    heatingdevice_data = json.load(json_read)
            except FileNotFoundError:
                heatingdevice_data = {}
                for device in self.heatingdevice:
                    heatingdevice_data[device.heater] = {"data" : {}}
                with open(JSON_PATH, 'w') as json_write:
                    json.dump(heatingdevice_data, json_write, indent = 4)

    def _set_unit_of_measurement(self, sensor):
        try:
            uom = self.get_state(sensor, attribute = 'unit_of_measurement')
        except Exception:
            return False
        else:
            match uom:
                case uom if 'C' in uom:
                    self.unit_of_measurement = 'C'
                    return True
                case uom if 'F' in uom:
                    self.unit_of_measurement = 'F'
                    return True
        return False

        # Set proper value when weather sensors is updated
    def weather_event(self, event_name, data, kwargs) -> None:
        """ Listens for weather change from the weather app
        """
        global OUT_TEMP
        global RAIN_AMOUNT
        global WIND_AMOUNT
        global OUT_LUX
        global CLOUD_COVER

        if self.datetime(aware=True) - self.out_temp_last_update > datetime.timedelta(minutes = 20):
            OUT_TEMP = data['temp']
        if self.datetime(aware=True) - self.rain_last_update > datetime.timedelta(minutes = 20):
            RAIN_AMOUNT = data['rain']
        if self.datetime(aware=True) - self.wind_last_update > datetime.timedelta(minutes = 20):
            WIND_AMOUNT = data['wind']
            for ac in self.heatingdevice:
                if WIND_AMOUNT >= ac.anemometer_speed:
                    ac.last_windy_time = self.datetime(aware=True)

        CLOUD_COVER = data['cloud_cover']

        if (
            self.datetime(aware=True) - self.lux_last_update1 > datetime.timedelta(minutes = 20)
            and self.datetime(aware=True) - self.lux_last_update2 > datetime.timedelta(minutes = 20)
        ):
            OUT_LUX = data['lux']

    def _outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except (ValueError, TypeError) as ve:
            pass
        else:
            self.out_temp_last_update = self.datetime(aware=True)

    def _rainSensorUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global RAIN_AMOUNT
        try:
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            RAIN_AMOUNT = 0.0
            self.log(f"Not able to set new rain amount: {new}. {ve}", level = 'DEBUG')
        else:
            self.rain_last_update = self.datetime(aware=True)
            if RAIN_AMOUNT > 0.0:
                for ac in self.heatingdevice:
                    ac.tryScreenOpen()

    def _anemometerUpdated(self, entity, attribute, old, new, kwargs) -> None:
        global WIND_AMOUNT
        try:
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            WIND_AMOUNT = 0.0
            self.log(f"Not able to set new wind amount: {new}. {ve}", level = 'DEBUG')
        else:
            self.wind_last_update = self.datetime(aware=True)
            for ac in self.heatingdevice:
                if WIND_AMOUNT >= ac.anemometer_speed:
                    ac.last_windy_time = self.datetime(aware=True)

        # LUX sensors
    def _out_lux_state(self, entity, attribute, old, new, kwargs) -> None:
        if self.outLux1 != float(new):
            self.outLux1 = float(new)

            self._newOutLux()

    def _out_lux_event_MQTT(self, event_name, data, kwargs) -> None:
        lux_data = json.loads(data['payload'])

        match lux_data:
            case {'illuminance_lux': illuminance} if self.outLux1 != float(illuminance):
                self.outLux1 = float(illuminance) # Zigbee sensor
                self._newOutLux()
            case {'value': value} if self.outLux1 != float(value):
                self.outLux1 = float(value) # Zwave sensor
                self._newOutLux()

    def _newOutLux(self) -> None:
        global OUT_LUX
        if (
            self.datetime(aware=True) - self.lux_last_update2 > datetime.timedelta(minutes = 15)
            or self.outLux1 >= self.outLux2
        ):
            OUT_LUX = self.outLux1
            for ac in self.heatingdevice:
                ac.tryScreenOpen()

        self.lux_last_update1 = self.datetime(aware=True)

    def _out_lux_state2(self, entity, attribute, old, new, kwargs) -> None:
        if self.outLux2 != float(new):
            self.outLux2 = float(new)

            self.__newOutLux2()

    def __out_lux_event_MQTT2(self, event_name, data, kwargs) -> None:
        lux_data = json.loads(data['payload'])

        match lux_data:
            case {'illuminance_lux': illuminance} if self.outLux2 != float(illuminance):
                self.outLux2 = float(illuminance) # Zigbee sensor
                self._newOutLux()
            case {'value': value} if self.outLux2 != float(value):
                self.outLux2 = float(value) # Zwave sensor
                self._newOutLux()
    def __newOutLux2(self) -> None:
        global OUT_LUX
        if (
            self.datetime(aware=True) - self.lux_last_update1 > datetime.timedelta(minutes = 15)
            or self.outLux2 >= self.outLux1
        ):
            OUT_LUX = self.outLux2
            for ac in self.heatingdevice:
                ac.tryScreenOpen()

        self.lux_last_update2 = self.datetime(aware=True)


class Heater():
    """ Parent Class to control room temperature with climate entity and weather/temperature sensors
    """
    def __init__(self, api,
        heater,
        indoor_sensor_temp,
        backup_indoor_sensor_temp,
        window_temp,
        window_offset:float,
        target_indoor_input,
        target_indoor_temp:float,
        vacation_temp:float,
        rain_level:float,
        anemometer_speed:int,

        daytime_savings:list,
        daytime_increasing:list,

        automate,

        windowsensors:list,
        screens:list,
        screening_temp:float,

        getting_cold:float,

        namespace:str,
        vacation,
        name_of_notify_app,
        notify_receiver:list
    ):
        self.ADapi = api
        self.heater = heater

            # Sensors
        self.indoor_sensor_temp = indoor_sensor_temp
        self.backup_indoor_sensor_temp = backup_indoor_sensor_temp
        self.prev_in_temp = float()

        if target_indoor_input is not None:
            api.listen_state(self._updateTarget, target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = target_indoor_temp

        try:
            self.prev_in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = namespace))
        except (ValueError, TypeError) as ve:
            if self.backup_indoor_sensor_temp is not None:
                try:
                    self.prev_in_temp = float(self.ADapi.get_state(self.backup_indoor_sensor_temp, namespace = namespace))
                except (ValueError, TypeError) as ve:
                    self.prev_in_temp = self.target_indoor_temp - 0.1
        except Exception as e:
            self.prev_in_temp = self.target_indoor_temp - 0.1
            self.ADapi.log(
                f"Not able to set new inside temperature from {self.indoor_sensor_temp}. {e}",
                level = 'WARNING'
            )

        self.heater_temp_last_changed = self.ADapi.datetime(aware=True)
        self.heater_temp_last_registered = self.ADapi.datetime(aware=True)

        try:
            self.vacation_temp = float(vacation_temp)
        except (ValueError, TypeError) as ve:
            self.vacation_temp:float = 10
            self.ADapi.log(f"Error setting vacation temperature. Set to 10 degrees.", level = 'INFO')

        self.window_temp = window_temp
        self.window_offset:float = window_offset
        self.rain_level:float = rain_level
        self.anemometer_speed:int = anemometer_speed
        self.last_windy_time = self.ADapi.datetime(aware=True) - datetime.timedelta(hours = 2)
        
        self.daytime_savings:list = daytime_savings
        self.daytime_increasing:list = daytime_increasing

        self.automate = automate

            # Windows
        self.windowsensors:list = windowsensors
        self.windows_is_open:bool = False
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = namespace) == 'on':
                self.windows_is_open = True

        self.window_last_opened = self.ADapi.datetime(aware=True) - datetime.timedelta(hours = 2)
        for windows in self.windowsensors:
            self.ADapi.listen_state(self._windowOpened, windows,
                new = 'on',
                duration = 120,
                namespace = namespace
            )
            self.ADapi.listen_state(self._windowClosed, windows,
                new = 'off',
                namespace = namespace
            )
            if self.window_last_opened < self.ADapi.convert_utc(
                self.ADapi.get_state(window,
                    attribute = 'last_changed',
                    namespace = namespace
                )
            ):
                self.window_last_opened = self.ADapi.convert_utc(
                    self.ADapi.get_state(window,
                        attribute = 'last_changed',
                        namespace = namespace
                    )
                )

        self.notify_on_window_open:bool = True
        self.notify_on_window_closed:bool = False

            # Setup Screening/Covers
        self.screening = []
        for s in screens:
            screen = Screen(self.ADapi,
                screen = s.get('screen', None),
                windowsensors = s.get('windowsensors', []),
                lux_close = s.get('lux_close', 40000),
                lux_open = s.get('lux_open', 15000),
                lux_open_when_media_is_on = s.get('lux_open_media', 4000),
                anemometer_speed_limit = anemometer_speed,
                not_when_home = s.get('not_when_home', []),
                mediaplayers = s.get('mediaplayers', []),
                namespace = namespace
            )
            self.screening.append(screen)
        self.screening_temp = screening_temp

        self.getting_cold:float = getting_cold

        self.namespace = namespace

            # Vacation setup
        if vacation is None:
            self.vacation_state = False
        else:
            self.vacation_state = self.ADapi.get_state(vacation, namespace = namespace)  == 'on'
            self.ADapi.listen_state(self._vacationStateListen, vacation,
                namespace = namespace
            )

            # Notfification setup
        if name_of_notify_app is not None:
            self.notify_app = self.ADapi.get_app(name_of_notify_app)
        else:
            self.notify_app = Notify_Mobiles(api)
        self.recipients:list = notify_receiver

            # Setup runtimes
        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/720)+1)*720
        runtime = runtime.replace(minute=0, second=10, microsecond=0) + datetime.timedelta(seconds=addseconds)
        self.ADapi.run_every(self._set_indoortemp, runtime, 720)

        """ Test your notification app
        """
        #self.notify_app.send_notification(
        #    message = f"Test notification for {self.ADapi.get_state(self.heater, attribute='friendly_name', namespace = self.namespace)}. It is {OUT_TEMP}° outside",
        #    message_title = "Climate Commander",
        #    message_recipient = self.recipients
        #)
        """ End test
        """

        # Sets Vacation status
    def _vacationStateListen(self, entity, attribute, old, new, kwargs) -> None:
        self.vacation_state = new == 'on'
        self.ADapi.run_in(self._set_indoortemp, 5)

        # Indoor target temperature
    def _updateTarget(self, entity, attribute, old, new, kwargs) -> None:
        self.target_indoor_temp = float(new)
        self.ADapi.run_in(self._set_indoortemp, 5)

        # Helper functions to check windows
    def _windowOpened(self, entity, attribute, old, new, kwargs) -> None:
        if self.windowsopened() != 0:
            self.windows_is_open = True
            self.ADapi.run_in(self._set_indoortemp, 1)

    def _windowClosed(self, entity, attribute, old, new, kwargs) -> None:
        if self.windowsopened() == 0:
            self.window_last_opened = self.ADapi.datetime(aware=True)
            self.windows_is_open = False
            self.ADapi.run_in(self._set_indoortemp, 60)

    def windowsopened(self) -> int:
        """ Returns number of opened windows
        """
        opened:int = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

        # Function to call screens in child class
    def tryScreenOpen(self) -> None:
        """ Tries to open screens on weather updates from rain and lux sensors
        """
        for s in self.screening:
            s.check_if_try_screen_open()

    def get_in_temp(self) -> float:
        """ Returns calculated indoor temperature
        """
        in_temp = float()
        try:
            in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
            attr_last_updated = self.ADapi.get_state(entity_id = self.indoor_sensor_temp,
                attribute = "last_updated"
            )
            if not attr_last_updated:
                last_update: datetime = self.ADapi.datetime(aware=True)
            else:
                last_update = self.ADapi.convert_utc(attr_last_updated)

            now: datetime = self.ADapi.datetime(aware=True)
            stale_time = now - last_update
            if stale_time > datetime.timedelta(hours = 2): # Stale for more than two hours. Reload integration
                self.ADapi.log(
                    f"{self.indoor_sensor_temp} has been stale for {stale_time} Reloading config_entry",
                    level = 'INFO'
                )
                self.ADapi.call_service('homeassistant/reload_config_entry',
                    entity_id = self.indoor_sensor_temp
                )
                raise ValueError("Stale data")
        except (ValueError, TypeError) as ve:
            if self.backup_indoor_sensor_temp is not None:
                try:
                    in_temp = float(self.ADapi.get_state(self.backup_indoor_sensor_temp, namespace = self.namespace))
                except (ValueError, TypeError) as ve:
                    in_temp = None
        except Exception as e:
            in_temp = None
            self.ADapi.log(
                f"Not able to get new inside temperature from {self.indoor_sensor_temp}. {e}",
                level = 'WARNING'
            )
        return in_temp

    def get_heater_temp(self) -> float:
        """ Returns set heater temperature for the climate entity.
        """
        heater_temp = float()
        ac_state:str = 'heat'
        try:
            heater_temp = float(self.ADapi.get_state(self.heater,
                attribute='temperature',
                namespace = self.namespace
            ))
        except (ValueError, TypeError) as ve:
            heater_temp = None
        except Exception as e:
            heater_temp = None
            self.ADapi.log(
                f"Not able to set new inside temperature from {self.heater}. {e}",
                level = 'WARNING'
            )

        return heater_temp

    def DaytimeIncreasing(self) -> (bool, bool):
        """ Checks if it is time during day to increase temperature.
            Also returns a boolean if is has been increased to prevent setting fan_only or cooling after increasing periode.
        """
        doDaytimeIncreasing = False
        afterDaytimeIncrease = False
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

                if datetime.datetime.now() > end_time_increase + datetime.timedelta(hours = 1):
                    end_time_increase += datetime.timedelta(days = 1)

                if (
                    datetime.datetime.now() > end_time_increase
                    and datetime.datetime.now() < end_time_increase + datetime.timedelta(hours = 1)
                ):
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

    def _set_indoortemp(self, kwargs) -> None:
        if self.automate:
            if self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off':
                return

            # Set variables from indoor sensor and heater
        in_temp:float = self.get_in_temp()
        if in_temp is None:
            return
        heater_temp:float = self.get_heater_temp()
        if heater_temp is None:
            return

        doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
        if doDaytimeIncreasing:
            in_temp -= 0.5

        if self.DaytimeSaving():
            in_temp += 0.5

        else:
            # Correct indoor temp when high amount of wind
            if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            # Correct indoor temp when rain
            elif RAIN_AMOUNT >= self.rain_level:
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

        new_temperature = float()
        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and in_temp >= self.target_indoor_temp + 10
            and OUT_TEMP > self.getting_cold
        ):
            self.notify_app.send_notification(
                message = f"No Window near {self.ADapi.get_state(self.heater, attribute='friendly_name', namespace = self.namespace)} is open and it is getting hot inside! {in_temp}°",
                message_title = "Window closed",
                message_recipient = self.recipients,
                also_if_not_home = False
            )
            self.notify_on_window_closed = False
        if self.windows_is_open:
            new_temperature = self.vacation_temp
            if (
                self.notify_on_window_open
                and OUT_TEMP < self.getting_cold
                and in_temp < self.getting_cold
            ):
                self.notify_app.send_notification(
                    message = f"Window near {self.ADapi.get_state(self.heater, attribute='friendly_name', namespace = self.namespace)} is open and inside temperature is {in_temp}",
                    message_title = "Window open",
                    message_recipient = self.recipients,
                    also_if_not_home = False
                )
                self.notify_on_window_open = False

        # Holliday temperature
        elif self.vacation_state:
            new_temperature = self.vacation_temp
            if in_temp > self.target_indoor_temp:
                if OUT_TEMP > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()
        else:
            new_temperature = self.adjust_set_temperature_by(heater_set_temp = heater_temp, in_temp = in_temp)

                # Check if it is hot inside
            if (
                in_temp > self.target_indoor_temp + 0.2
                and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
            ):
                if OUT_TEMP > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()

        self.updateClimateTemperature(heater_temp = heater_temp, new_temperature = new_temperature)

    def updateClimateTemperature(self, heater_temp, new_temperature) -> None:
        """ Updates climate with new temperature and updates persistent storage
        """
        # Update with new temperature
        if heater_temp != round(new_temperature * 2, 0) / 2:
            self.ADapi.call_service('climate/set_temperature',
                entity_id = self.heater,
                temperature = round(new_temperature * 2, 0) / 2,
                namespace = self.namespace
            )
            self.heater_temp_last_changed = self.ADapi.datetime(aware=True)
        elif (
                JSON_PATH is not None
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(hours = 2)
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_registered > datetime.timedelta(hours = 2)
            ):
                self._registerHeatingtemp(heater_temp = new_temperature)
                self.heater_temp_last_registered = self.ADapi.datetime(aware=True)

    def adjust_set_temperature_by(self, heater_set_temp:float, in_temp:float) -> float:
        """ Calculates if heater temperature needs to be adjusted based ontemperatures.
            Returns adjusted temperature.
        """
        new_temperature:float = heater_set_temp
        adjust_temp_by:float = 0

        if self.window_temp is not None:
            try:
                window_temp = float(self.ADapi.get_state(self.window_temp, namespace = self.namespace))
            except (TypeError, AttributeError):
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(
                    f"{self.window_temp} has no temperature. Probably offline",
                    level = 'DEBUG'
                )
            except Exception as e:
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(
                    f"Not able to get temperature from {self.window_temp}. {e}",
                    level = 'DEBUG'
                )

            if window_temp > self.target_indoor_temp + self.window_offset:
                adjust_temp_by = round((self.target_indoor_temp + self.window_offset) - window_temp,1)
                """ Logging of window temp turning down heating """
                #if OUT_LUX < 2000:
                #    self.ADapi.log(
                #        f"{self.ADapi.get_state(self.window_temp, namespace = self.namespace, attribute = 'friendly_name')} "
                #        f"is {window_temp}. That is {-adjust_temp_by} above offset. "
                #        f"The lux outside is {OUT_LUX}",
                #        level = 'INFO'
                #        )
                """ End logging """

        adjust_temp_by += round(self.target_indoor_temp - in_temp, 1)

        if (
            self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(minutes = 30)
            and adjust_temp_by < 0
            and adjust_temp_by > -0.3
        ):
            adjust_temp_by = -0.5
        elif (
            self.ADapi.datetime(aware=True) - self.heater_temp_last_changed < datetime.timedelta(minutes = 30)
            and adjust_temp_by > 0
            and adjust_temp_by < 0.4
        ):
            adjust_temp_by = 0

        persistent_temperature, valid_temp_data = self._getHeatingTempFromPersisten()

        if self.prev_in_temp > in_temp:
            # Temp is dropping
            if in_temp > self.target_indoor_temp:
                # Set temp is already lowering temperature
                self.prev_in_temp = in_temp
                return heater_set_temp

            if (
                new_temperature <= self.target_indoor_temp - 4
                and in_temp < self.target_indoor_temp
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(hours = 4)
            ):
                # Has been turned down for more than 4 hours
                self.prev_in_temp = in_temp
                return persistent_temperature

            if (
                self.ADapi.datetime(aware=True) - self.window_last_opened > datetime.timedelta(hours = 1)
                or in_temp < self.target_indoor_temp -1
            ):

                new_temperature += adjust_temp_by

                if (
                    valid_temp_data
                    and round(new_temperature * 2, 0) / 2 < round(persistent_temperature * 2, 0) / 2
                ):
                    self.prev_in_temp = in_temp
                    return persistent_temperature

        elif self.prev_in_temp < in_temp:
            # Temp is increasing
            if in_temp < self.target_indoor_temp:
                # Set temp is already increasing temperature
                self.prev_in_temp = in_temp
                return heater_set_temp

            new_temperature += adjust_temp_by

            if (
                valid_temp_data
                and round(new_temperature * 2, 0) / 2 > round(persistent_temperature * 2, 0) / 2
            ):
                self.prev_in_temp = in_temp
                return persistent_temperature

        if valid_temp_data:
            # Restrict from going +- 4 degrees away from valid persistent temperature data
            if new_temperature > persistent_temperature + 4:
                new_temperature = persistent_temperature + 4
            elif new_temperature < persistent_temperature - 4:
                new_temperature = persistent_temperature - 4
        else:
            # Restrict from going +- 6 degrees away from target indoor temperature when persistent data has not been able to gather enough info
            if new_temperature > self.target_indoor_temp + 6:
                new_temperature = self.target_indoor_temp + 6
            elif new_temperature < self.target_indoor_temp - 6:
                new_temperature = self.target_indoor_temp - 6

        self.prev_in_temp = in_temp
        return new_temperature

    def _registerHeatingtemp(self, heater_temp:float) -> None:
        with open(JSON_PATH, 'r') as json_read:
            heatingdevice_data = json.load(json_read)

        heatingData = heatingdevice_data[self.heater]['data']
        out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
        out_lux_str = str(math.floor(OUT_LUX / 5000))

        if not out_temp_str in heatingdevice_data[self.heater]['data']:
            newData = {out_lux_str : {"temp" : heater_temp, "Counter" : 1}}
            heatingdevice_data[self.heater]['data'].update(
                {out_temp_str : newData}
            )
        elif not out_lux_str in heatingdevice_data[self.heater]['data'][out_temp_str]:
            newData = {"temp" : heater_temp, "Counter" : 1}
            heatingdevice_data[self.heater]['data'][out_temp_str].update(
                {out_lux_str : newData}
            )
        else:
            heatingData = heatingdevice_data[self.heater]['data'][out_temp_str][out_lux_str]
            counter = heatingData['Counter'] + 1
            avgheating = round(((heatingData['temp'] * heatingData['Counter']) + heater_temp) / counter,1)
            if counter > 100:
                counter = 10

            newData = {"temp" : avgheating, "Counter" : counter}
            heatingdevice_data[self.heater]['data'][out_temp_str].update(
                {out_lux_str : newData}
            )
        with open(JSON_PATH, 'w') as json_write:
            json.dump(heatingdevice_data, json_write, indent = 4)

    def _getHeatingTempFromPersisten(self) -> (float, bool):
        if JSON_PATH is not None:
            with open(JSON_PATH, 'r') as json_read:
                heatingdevice_data = json.load(json_read)

            heatingData = heatingdevice_data[self.heater]['data']
            out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
            out_lux_str = str(math.floor(OUT_LUX / 5000))

            valid_temp_data:bool = True

            try:
                lux_temp_data = heatingdevice_data[self.heater]['data'][out_temp_str]
            except Exception:
                valid_temp_data = False
                temp_diff:int = 0
                closest_temp:int
                for temps in heatingdevice_data[self.heater]['data']:
                    if OUT_TEMP > float(temps):
                        if temp_diff != 0:
                            if temp_diff < OUT_TEMP - float(temps):
                                continue
                        temp_diff = OUT_TEMP - float(temps)
                        closest_temp = temps
                    else:
                        if temp_diff != 0:
                            if temp_diff < float(temps) - OUT_TEMP:
                                continue
                        temp_diff = float(temps) - OUT_TEMP
                        closest_temp = temps
                out_temp_str = closest_temp
                lux_temp_data = heatingdevice_data[self.heater]['data'][out_temp_str]

            try:
                temp = lux_temp_data[out_lux_str]['temp']
            except Exception as e:
                valid_temp_data = False
                lux_diff:int = 0
                closest_lux:int
                luxCheck = math.floor(OUT_LUX / 5000)
                for luxs in heatingdevice_data[self.heater]['data'][out_temp_str]:
                    if luxCheck > int(luxs):
                        if lux_diff == 0:
                            lux_diff = luxCheck - int(luxs)
                            closest_lux = luxs
                        elif lux_diff >= luxCheck - int(luxs):
                            lux_diff = luxCheck - int(luxs)
                            closest_lux = luxs
                        
                    else:
                        if lux_diff == 0:
                            lux_diff = int(luxs) - luxCheck
                            closest_lux = luxs
                        elif lux_diff > int(luxs) - luxCheck:
                            lux_diff = int(luxs) - luxCheck
                            closest_lux = luxs
                out_lux_str = closest_lux
                temp = lux_temp_data[out_lux_str]['temp']


            if int(heatingdevice_data[self.heater]['data'][out_temp_str][out_lux_str]['Counter']) < 10:
                valid_temp_data = False

            return float(temp), valid_temp_data

        else:
            try:
                temp = float(self.ADapi.get_state(self.heater,
                    attribute='temperature',
                    namespace = self.namespace
                ))
                if temp > self.target_indoor_temp + 6:
                    temp = self.target_indoor_temp
                elif temp < self.target_indoor_temp - 6:
                    temp = self.target_indoor_temp
                return temp, False

            except (ValueError, TypeError):
                return self.target_indoor_temp, False
            except Exception as e:
                return self.target_indoor_temp, False

    def _setHeatingTempFromPersisten(self, **kwargs) -> None:
        offset:float = 0.0
        if 'offset' in kwargs:
            offset = kwargs['offset']

        temp, valid_temp_data = self._getHeatingTempFromPersisten()
        temp += offset

        # Update with new temperature
        self.ADapi.call_service('climate/set_temperature',
            entity_id = self.heater,
            temperature = temp,
            namespace = self.namespace
        )
        self.heater_temp_last_changed = self.ADapi.datetime(aware=True)


class Aircondition(Heater):
    """ Class to control room temperature with climate entity and weather/temperature sensors
    """
    def __init__(self, api,
        heater,
        indoor_sensor_temp,
        backup_indoor_sensor_temp,
        window_temp,
        window_offset:float,
        target_indoor_input,
        target_indoor_temp:float,
        vacation_temp:float,
        max_vacation_temp:float,
        rain_level:float,
        anemometer_speed:int,

        daytime_savings:list,
        daytime_increasing:list,
        silence:list,

        automate,

        windowsensors:list,
        screens:list,
        screening_temp:float,
        getting_cold:float,

        namespace:str,
        vacation,
        name_of_notify_app,
        notify_receiver
    ):
        super().__init__(api,
            heater = heater,
            indoor_sensor_temp = indoor_sensor_temp,
            backup_indoor_sensor_temp = backup_indoor_sensor_temp,
            window_temp = window_temp,
            window_offset = window_offset,
            target_indoor_input = target_indoor_input,
            target_indoor_temp = target_indoor_temp,
            vacation_temp = vacation_temp,
            rain_level = rain_level,
            anemometer_speed = anemometer_speed,

            daytime_savings = daytime_savings,
            daytime_increasing = daytime_increasing,

            automate = automate,

            windowsensors = windowsensors,
            screens = screens,
            screening_temp = screening_temp,
            getting_cold = getting_cold,

            namespace = namespace,
            vacation = vacation,
            name_of_notify_app = name_of_notify_app,
            notify_receiver = notify_receiver
        )

        try:
            self.max_vacation_temp = float(max_vacation_temp)
        except (ValueError, TypeError) as ve:
            self.max_vacation_temp:float = 30
            self.ADapi.log(f"Error setting max vacation temperature. Set to 30 degrees.", level = 'INFO')

        self.silence:list = silence
        self.fan_mode:str = self.ADapi.get_state(self.heater,
            attribute='fan_mode',
            namespace = self.namespace
        )

        self.fan_mode_persistent:str = self.fan_mode

        if JSON_PATH is not None:
            with open(JSON_PATH, 'r') as json_read:
                heatingdevice_data = json.load(json_read)

            if 'fan_mode' in heatingdevice_data[self.heater]:
                if heatingdevice_data[self.heater]['fan_mode'] is not None:
                    self.fan_mode_persistent = heatingdevice_data[self.heater]['fan_mode']

        if (
            self.fan_mode_persistent is None
            or self.fan_mode_persistent == 'Silence'
        ):
            self.fan_mode_persistent = 'Auto'

    def _set_indoortemp(self, kwargs) -> None:
        if self.automate:
            if self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off':
                return
            # Set variables from indoor sensor and heater
        in_temp:float = self.get_in_temp()
        if in_temp is None:
            self.ADapi.log(f"Indoor temp is None. Aborting setting new temperature", level = 'INFO')
            return
        heater_temp:float = self.get_heater_temp()
        ac_state = self.ADapi.get_state(self.heater, namespace = self.namespace)

            # Set silence preset for HVAC enabled devices
        if 'Silence' in self.ADapi.get_state(self.heater,
            attribute='fan_modes',
            namespace = self.namespace
        ):
            change_fan_mode_to_silence = False
            for time in self.silence:
                if (
                    'start' in time
                    and 'stop' in time
                ):
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
            
            if (
                change_fan_mode_to_silence
                and self.ADapi.get_state(self.heater,
                    attribute='fan_mode',
                    namespace = self.namespace
                ) != 'Silence'
            ):
                self.fan_mode = self.ADapi.get_state(self.heater,
                    attribute='fan_mode',
                    namespace = self.namespace
                )
                self.ADapi.call_service('climate/set_fan_mode',
                    entity_id = self.heater,
                    fan_mode = 'Silence',
                    namespace = self.namespace
                )
                if (
                    self.fan_mode != self.fan_mode_persistent
                    and self.fan_mode is not None
                ):
                    self.fan_mode_persistent = self.fan_mode

                    if JSON_PATH is not None:
                        with open(JSON_PATH, 'r') as json_read:
                            heatingdevice_data = json.load(json_read)

                        heatingdevice_data[self.heater].update(
                            {'fan_mode' : self.fan_mode}
                        )

                        with open(JSON_PATH, 'w') as json_write:
                            json.dump(heatingdevice_data, json_write, indent = 4)

            elif (
                not change_fan_mode_to_silence
                and self.ADapi.get_state(self.heater,
                    attribute='fan_mode',
                    namespace = self.namespace
                ) == 'Silence'
            ):
                self.ADapi.call_service('climate/set_fan_mode',
                    entity_id = self.heater,
                    fan_mode = self.fan_mode_persistent,
                    namespace = self.namespace
                )

        if ac_state == 'heat':
            if heater_temp is None:
                return
            # Setting temperature hvac_mode == heat
            
            # Windows
            if self.windows_is_open:
                try:
                    self.ADapi.call_service('climate/set_hvac_mode',
                        entity_id = self.heater,
                        hvac_mode = 'fan_only',
                        namespace = self.namespace
                    )
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                        level = 'DEBUG'
                    )
                return

            doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
            if doDaytimeIncreasing:
                in_temp -= 0.5

            if self.DaytimeSaving():
                in_temp += 0.5

            else:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

                # Correct indoor temp when rain
                elif RAIN_AMOUNT >= self.rain_level:
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            new_temperature = float()
            # Holliday temperature
            if self.vacation_state:
                new_temperature = self.vacation_temp
                if (
                    in_temp > self.target_indoor_temp -3
                    and in_temp > self.vacation_temp + 3
                ):
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'fan_only',
                            namespace = self.namespace
                        )
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                            level = 'INFO'
                        )
                        
                    if OUT_TEMP > self.screening_temp:
                        for s in self.screening:
                            s.try_screen_close()
                    return
                else:
                    for s in self.screening:
                        s.can_close_on_lux = False
            else:
                new_temperature = self.adjust_set_temperature_by(heater_set_temp = heater_temp, in_temp = in_temp)

                # Check if there is a need to boost the ac when windy
                if (
                    self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 25)
                    and in_temp < self.target_indoor_temp -0.7
                ):
                    if self.ADapi.get_state(self.heater, attribute='fan_mode', namespace = self.namespace) != 'Silence':
                        if 'boost' in self.ADapi.get_state(self.heater, attribute='preset_modes', namespace = self.namespace):
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
                    if OUT_TEMP > self.screening_temp:
                        for s in self.screening:
                            s.try_screen_close()
                    if (
                        in_temp > self.target_indoor_temp + 0.6
                        and not afterDaytimeIncrease
                    ):
                        try:
                            self.ADapi.call_service('climate/set_hvac_mode',
                                entity_id = self.heater,
                                hvac_mode = 'fan_only',
                                namespace = self.namespace
                            )
                            self.ADapi.run_in(self._set_indoortemp, 5) # To set silence to new fan mode, if time for it
                        except Exception as e:
                            self.ADapi.log(
                                f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                                level = 'DEBUG'
                            )
                        return
                else:
                    for s in self.screening:
                        s.can_close_on_lux = False

            # Update with new temperature
            self.updateClimateTemperature(heater_temp = heater_temp, new_temperature = new_temperature)

        elif ac_state == 'cool':
            if heater_temp is None:
                return
            # Setting temperature hvac_mode == cool

            # Windows
            if (
                self.windows_is_open
            ):
                try:
                    self.ADapi.call_service('climate/set_hvac_mode',
                        entity_id = self.heater,
                        hvac_mode = 'fan_only',
                        namespace = self.namespace
                    )
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                        level = 'DEBUG'
                    )
                return

            doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
            if doDaytimeIncreasing:
                in_temp += 0.5

            if self.DaytimeSaving():
                in_temp -= 0.5

            else:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3

                # Correct indoor temp when rain
                elif RAIN_AMOUNT >= self.rain_level:
                    in_temp -= 0.3

            for s in self.screening:
                s.try_screen_close()

            set_fan_mode = False
            if (
                not self.ADapi.now_is_between('sunrise', 'sunset - 00:30:00')
                and RAIN_AMOUNT > 0
                and in_temp < self.target_indoor_temp + 2
            ):
                self.ADapi.log(f"Setting fan based on target +3") ###
                set_fan_mode = True
            elif in_temp < self.target_indoor_temp + 1:
                set_fan_mode = True
            if set_fan_mode:
                try:
                    self.ADapi.call_service('climate/set_hvac_mode',
                        entity_id = self.heater,
                        hvac_mode = 'fan_only',
                        namespace = self.namespace
                    )
                except Exception as e:
                    self.ADapi.log(
                        f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                        level = 'DEBUG'
                    )
                return
            if self.vacation_state:
                if in_temp < self.max_vacation_temp - 2:
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'fan_only',
                            namespace = self.namespace
                        )
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                            level = 'DEBUG'
                        )
                    return

            new_temperature = self.adjust_set_temperature_by(heater_set_temp = heater_temp, in_temp = in_temp)

            # Update with new temperature
            if heater_temp != round(new_temperature * 2, 0) / 2:
                self.ADapi.call_service('climate/set_temperature',
                    entity_id = self.heater,
                    temperature = round(new_temperature * 2, 0) / 2,
                    namespace = self.namespace
                )
                self.heater_temp_last_changed = self.ADapi.datetime(aware=True)

        elif ac_state == 'fan_only':
            # Checking temperature hvac_mode == fan_only
            if (
                not self.windows_is_open
                and not self.vacation_state
            ):
                set_cool = False
                if (
                    in_temp > self.target_indoor_temp + 2
                    and OUT_TEMP > self.target_indoor_temp - 4
                ):
                    if (
                        self.ADapi.now_is_between('sunrise', 'sunset - 00:30:00')
                        and RAIN_AMOUNT == 0
                    ):
                        set_cool = True
                    elif in_temp > self.target_indoor_temp + 3:
                        set_cool = True
                    if set_cool:
                        try:
                            self.ADapi.call_service('climate/set_hvac_mode',
                                entity_id = self.heater,
                                hvac_mode = 'cool',
                                namespace = self.namespace
                            )
                        except Exception as e:
                            self.ADapi.log(
                                f"Not able to set hvac_mode to cool for {self.heater}. Probably not supported. {e}",
                                level = 'DEBUG'
                            )

                elif in_temp <= self.target_indoor_temp:
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'heat',
                            namespace = self.namespace
                        )
                        # Set new temperature after change to heating
                        self.ADapi.run_in(self._setHeatingTempFromPersisten, 10, offset = -1)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to heat for {self.heater}. Probably not supported. {e}",
                            level = 'DEBUG'
                        )
                    return
            elif (
                not self.windows_is_open # And vacation
            ):
                if (
                    in_temp <= self.target_indoor_temp -5
                    or in_temp <= self.vacation_temp + 2
                ):
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'heat',
                            namespace = self.namespace
                        )
                        # Set new temperature after change to heating
                        self.ADapi.run_in(self._set_indoortemp, 20)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to heat for {self.heater}. Probably not supported. {e}",
                            level = 'DEBUG'
                        )
                elif in_temp > self.max_vacation_temp:
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'cool',
                            namespace = self.namespace
                        )
                        self.ADapi.run_in(self._set_indoortemp, 20)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to cool for {self.heater}. Probably not supported. {e}",
                            level = 'DEBUG'
                        )

            elif (
                self.windows_is_open 
                and self.notify_on_window_open 
                and in_temp < self.getting_cold
            ):
                self.notify_app.send_notification(
                    message = f"Window near {self.ADapi.get_state(self.heater, attribute='friendly_name', namespace = self.namespace)} is open and inside temperature is {in_temp}",
                    message_title = "Window open",
                    message_recipient = self.recipients,
                    also_if_not_home = False
                )
                self.notify_on_window_open = False

            if OUT_TEMP > float(self.screening_temp):
                for s in self.screening:
                    s.try_screen_close()

        elif (
            ac_state != 'off' 
            and ac_state != 'unavailable'
        ):
            # If hvac_state is not heat/cool/fan_only/off or unavailable. Log state for notice. Write automation if missing functionality.
            self.ADapi.log(
                f"Unregisterd AC state: {ac_state}. "
                f"{self.ADapi.get_state(self.heater, attribute='friendly_name', namespace = self.namespace)}. Will not automate",
                level = 'WARNING'
            )


class Screen():
    """ Class to control cover entities based on inside temperature and weather sensors.
    """
    def __init__(self, api,
        screen:str,
        windowsensors:list,
        lux_close:int,
        lux_open:int,
        lux_open_when_media_is_on:int,
        anemometer_speed_limit:int,
        not_when_home:list,
        mediaplayers:list,
        namespace:str
    ):
        self.ADapi = api
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
        self.can_close_on_lux:bool = False

        self.screen_position = self.ADapi.get_state(self.screen, attribute='current_position')

        self.ADapi.listen_event(self.weather_updated, 'WEATHER_CHANGE',
                    namespace = self.namespace
                )

    def windowsopened(self) -> int:
        """ Returns number of opened windows.
        """
        opened:int = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened

    def try_screen_close(self) -> None:
        """ Checks conditions to close screens.
        """
        if (
            RAIN_AMOUNT == 0
            and WIND_AMOUNT < self.anemometer_speed_limit
            and self.windowsopened() == 0
        ):
            for person in self.not_when_home:
                if self.ADapi.get_state(person, namespace = self.namespace) == 'home':
                    return

            if (
                OUT_LUX >= self.lux_close
                and CLOUD_COVER < 80
            ):
                if (
                    self.ADapi.get_state(self.screen,
                        attribute='current_position',
                        namespace = self.namespace) == self.screen_position
                    and self.ADapi.get_state(self.screen,
                        attribute='current_position',
                        namespace = self.namespace) == 100
                ):
                    self.ADapi.call_service('cover/close_cover',
                        entity_id= self.screen,
                        namespace = self.namespace
                    )
                    self.screen_position = 0
            
            elif not self.can_close_on_lux:
                self.can_close_on_lux = True

    def weather_updated(self, event_name, data, kwargs) -> None:
        """ Listens for weather change from the weather app
        """
        if (
            not self.check_if_try_screen_open()
            and self.can_close_on_lux
        ):
            if (
                float(data['lux']) >= self.lux_close
                and CLOUD_COVER < 80
            ):
                self.try_screen_close()

    def check_if_try_screen_open(self) -> bool:
        """ Checks conditions from configured weather sensors before trying to open or screens.
        """
        if self._check_mediaplayers_off():
            lux_open = self.lux_open_normal
        else:
            lux_open = self.lux_open_when_media_is_on
        if (
            RAIN_AMOUNT > 0
            or WIND_AMOUNT > self.anemometer_speed_limit
            or OUT_LUX < lux_open
        ):
           self.try_screen_open()
           return True

        return False

    def try_screen_open(self) -> None:
        """ Checks conditions to open screens.
        """
        if (
            self.windowsopened() == 0
            and self.screen_position != 100
        ):
            openme = False
            if (
                self.ADapi.get_state(self.screen,
                    attribute='current_position',
                    namespace = self.namespace) != self.screen_position
                and OUT_LUX <= 400
            ):
                openme = True
            elif (
                self.ADapi.get_state(self.screen,
                    attribute='current_position',
                    namespace = self.namespace) == self.screen_position
            ):
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
    """ Class to send notification with 'notify' HA integration
    """
    def __init__(self, api):
        self.ADapi = api

    def send_notification(self, **kwargs) -> None:
        """ Sends notification to recipients via Home Assistant notification.
        """
        message:str = kwargs['message']
        message_title:str = kwargs.get('message_title', 'Home Assistant')
        message_recipient:str = kwargs.get('message_recipient', True)
        also_if_not_home:bool = kwargs.get('also_if_not_home', False)

        for re in message_recipient:
            self.ADapi.call_service(f'notify/{re}',
                title = message_title,
                message = message
            )