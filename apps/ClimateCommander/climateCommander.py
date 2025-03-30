""" CLimate Control by Pythm
    Control your Climate entities and your screening covers based on weather sensors

    @Pythm / https://github.com/Pythm
"""

__version__ = "1.2.0"

import appdaemon.plugins.hass.hassapi as hass
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

class Climate(hass.Hass):

    def initialize(self):

        self.mqtt = None # Only initialize MQTT API if needed

            # Set up your own notification app
        name_of_notify_app = self.args.get('notify_app', None)
        notify_reciever = self.args.get('notify_reciever', None)

            # Namespaces for HASS and MQTT
        HASS_namespace = self.args.get('HASS_namespace', 'default')
        MQTT_namespace = self.args.get('MQTT_namespace', 'default')
    
            # Default away state for saving purposes
        if 'away_state' in self.args:
            away_state = self.args['away_state']
        elif 'vacation' in self.args:
            away_state = self.args['vacation']
        else:
            away_state = 'input_boolean.vacation'
            if not self.entity_exists(self.get_entity(away_state),
                namespace = HASS_namespace
            ):
                self.call_service("state/set",
                    entity_id = away_state,
                    attributes = {'friendly_name' : 'Vacation'},
                    state = 'off',
                    namespace = HASS_namespace
                )
            else:
                self.log(
                    "'vacation' not configured. Using 'input_boolean.vacation' as default away state",
                    level = 'WARNING'
                )

        away_temp = self.args.get('vacation_temp', 16)

            # Weather sensors
        self.outside_temperature = self.args.get('outside_temperature', None)
        screening_temp = self.args.get('screening_temp', 8)
        getting_cold = self.args.get('getting_cold', 18)

        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level:float = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed:int = self.args.get('anemometer_speed',40)

            # Setup Outside temperatures
        global OUT_TEMP
        self.out_temp_last_update = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.outside_temperature:
            self.listen_state(self.outsideTemperatureUpdated, self.outside_temperature)
            try:
                OUT_TEMP = float(self.get_state(self.outside_temperature))
            except (ValueError, TypeError):
                self.log(f"Outside temperature is not valid. {e}", level = 'DEBUG')

            # Setup Rain sensor
        global RAIN_AMOUNT
        self.rain_last_update = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.rain_sensor:
            self.listen_state(self.rainSensorUpdated, self.rain_sensor)
            try:
                RAIN_AMOUNT = float(self.get_state(self.rain_sensor))
            except (ValueError) as ve:
                RAIN_AMOUNT = 0.0
                self.log(f"Rain sensor not valid. {ve}", level = 'DEBUG')

            # Setup Wind sensor
        global WIND_AMOUNT
        self.wind_last_update = self.datetime(aware=True) - datetime.timedelta(minutes = 20)
        if self.anemometer:
            self.listen_state(self.anemometerUpdated, self.anemometer)
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
            self.listen_state(self.out_lux_state, lux_sensor,
                namespace = HASS_namespace
            )
        if 'OutLuxMQTT' in self.args:
            if self.mqtt == None:
                self.mqtt = self.get_plugin_api("MQTT")
            out_lux_sensor = self.args['OutLuxMQTT']
            self.mqtt.mqtt_subscribe(out_lux_sensor)
            self.mqtt.listen_event(self.out_lux_event_MQTT, "MQTT_MESSAGE",
                topic = out_lux_sensor,
                namespace = MQTT_namespace
            )

        if 'OutLux_sensor_2' in self.args:
            lux_sensor = self.args['OutLux_sensor_2']
            self.listen_state(self.out_lux_state2, lux_sensor,
                namespace = HASS_namespace
            )
        if 'OutLuxMQTT_2' in self.args:
            if self.mqtt == None:
                self.mqtt = self.get_plugin_api("MQTT")
            out_lux_sensor = self.args['OutLuxMQTT_2']
            self.mqtt.mqtt_subscribe(out_lux_sensor)
            self.mqtt.listen_event(self.out_lux_event_MQTT2, "MQTT_MESSAGE",
                topic = out_lux_sensor,
                namespace = MQTT_namespace
            )


    # Persistent storage for storing mode and lux data
        self.usePersistentStorage:bool = False
        self.JSON_PATH:str = ''
        if 'json_path' in self.args:
            self.JSON_PATH = self.args['json_path']
            self.JSON_PATH += str(self.name) + '.json'
            self.usePersistentStorage = True

            # Configuration of Heatpumps to command
        self.heatingdevice:list = []
        climate = self.args.get('HVAC', [])
        for ac in climate:
            aircondition = Aircondition(self,
                heater = ac['climate'],
                indoor_sensor_temp = ac.get('indoor_sensor_temp', None),
                backup_indoor_sensor_temp = ac.get('backup_indoor_sensor_temp', None),
                window_temp = ac.get('window_sensor_temp', None),
                window_offset = ac.get('window_offset', -3),
                target_indoor_input = ac.get('target_indoor_input', None),
                target_indoor_temp = ac.get('target_indoor_temp', 22.7),
                away_temp = ac.get('vacation_temp', away_temp),
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
                json_path = self.JSON_PATH,
                away = ac.get('vacation', away_state),
                name_of_notify_app = name_of_notify_app,
                notify_reciever = ac.get('notify_reciever', notify_reciever)
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
                target_indoor_temp = heater.get('target_indoor_temp', 22.7),
                away_temp = heater.get('vacation_temp', away_temp),
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
                json_path = self.JSON_PATH,
                away = heater.get('vacation', away_state),
                name_of_notify_app = name_of_notify_app,
                notify_reciever = heater.get('notify_reciever', notify_reciever)
            )
            self.heatingdevice.append(heating)

        if self.usePersistentStorage:
            try:
                with open(self.JSON_PATH, 'r') as json_read:
                    heatingdevice_data = json.load(json_read)
            except FileNotFoundError:
                heatingdevice_data = {}
                for device in self.heatingdevice:
                    heatingdevice_data[device.heater] = {"data" : {}}
                with open(self.JSON_PATH, 'w') as json_write:
                    json.dump(heatingdevice_data, json_write, indent = 4)

        self.listen_event(self.weather_event, 'WEATHER_CHANGE',
            namespace = HASS_namespace
        )


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
            if RAIN_AMOUNT > 0.0:
                for ac in self.heatingdevice:
                    ac.tryScreenOpen()
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
            for ac in self.heatingdevice:
                ac.tryScreenOpen()


    def outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates OUT_TEMP from sensor
        """
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except (ValueError, TypeError) as ve:
            pass
        else:
            self.out_temp_last_update = self.datetime(aware=True)


    def rainSensorUpdated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates RAIN_AMOUNT from sensor
        """
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
        

    def anemometerUpdated(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates WIND_AMOUNT from sensor
        """
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
    def out_lux_state(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates outLux1 from HA sensor and sends for check to newOutLux
        """
        if self.outLux1 != float(new):
            self.outLux1 = float(new)

            self.newOutLux()


    def out_lux_event_MQTT(self, event_name, data, kwargs) -> None:
        """ Updates outLux1 from MQTT sensor and sends for check to newOutLux
        """
        lux_data = json.loads(data['payload'])
        if 'illuminance_lux' in lux_data:
            if self.outLux1 != float(lux_data['illuminance_lux']):
                self.outLux1 = float(lux_data['illuminance_lux']) # Zigbee sensor
                self.newOutLux()
        elif 'value' in lux_data:
            if self.outLux1 != float(lux_data['value']):
                self.outLux1 = float(lux_data['value']) # Zwave sensor
                self.newOutLux()


    def newOutLux(self) -> None:
        """ Updates OUT_LUX based on conditions between outLux1 and outLux2
        """
        global OUT_LUX
        if (
            self.datetime(aware=True) - self.lux_last_update2 > datetime.timedelta(minutes = 15)
            or self.outLux1 >= self.outLux2
        ):
            OUT_LUX = self.outLux1
            for ac in self.heatingdevice:
                ac.tryScreenOpen()

        self.lux_last_update1 = self.datetime(aware=True)


    def out_lux_state2(self, entity, attribute, old, new, kwargs) -> None:
        """ Updates outLux2 from HA sensor and sends for check to newOutLux
        """
        if self.outLux2 != float(new):
            self.outLux2 = float(new)

            self.newOutLux2()


    def out_lux_event_MQTT2(self, event_name, data, kwargs) -> None:
        """ Updates outLux2 from MQTT sensor and sends for check to newOutLux
        """
        lux_data = json.loads(data['payload'])
        if 'illuminance_lux' in lux_data:
            if self.outLux2 != float(lux_data['illuminance_lux']):
                self.outLux2 = float(lux_data['illuminance_lux']) # Zigbee sensor
                self.newOutLux2()
        elif 'value' in lux_data:
            if self.outLux2 != float(lux_data['value']):
                self.outLux2 = float(lux_data['value']) # Zwave sensor
                self.newOutLux2()


    def newOutLux2(self) -> None:
        """ Updates OUT_LUX based on conditions between outLux1 and outLux2
        """
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
        away_temp:float,
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
        json_path:str,
        away,
        name_of_notify_app,
        notify_reciever:list
    ):

        self.ADapi = api

        self.heater = heater

            # Sensors
        self.indoor_sensor_temp = indoor_sensor_temp
        self.backup_indoor_sensor_temp = backup_indoor_sensor_temp
        self.prev_in_temp = float()

        if target_indoor_input != None:
            api.listen_state(self.updateTarget, target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = target_indoor_temp

        try:
            self.prev_in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = namespace))
        except (ValueError, TypeError) as ve:
            if self.backup_indoor_sensor_temp != None:
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

        self.away_temp = away_temp
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
            self.ADapi.listen_state(self.windowOpened, windows,
                new = 'on',
                duration = 120,
                namespace = namespace
            )
            self.ADapi.listen_state(self.windowClosed, windows,
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
        self.JSON_PATH = json_path
        self.usePersistentStorage = False
        if json_path != '':
            self.usePersistentStorage = True


            # Vacation setup
        self.away_state = self.ADapi.get_state(away, namespace = namespace)  == 'on'
        self.ADapi.listen_state(self.awayStateListen, away,
            namespace = namespace
        )

            # Notfification setup
        if name_of_notify_app != None:
            self.notify_app = self.ADapi.get_app(name_of_notify_app)
        else:
            self.notify_app = Notify_Mobiles(api)
        self.recipients:list = notify_reciever


            # Setup runtimes
        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/720)+1)*720
        runtime = runtime.replace(minute=0, second=10, microsecond=0) + datetime.timedelta(seconds=addseconds)
        self.ADapi.run_every(self.set_indoortemp, runtime, 720)


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
    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        """ Sets new indoor temp and away state after vacation switch
        """
        self.away_state = new == 'on'
        self.ADapi.run_in(self.set_indoortemp, 5)


        # Indoor target temperature
    def updateTarget(self, entity, attribute, old, new, kwargs) -> None:
        """ Sets new indoor temperature target from HA input_number
        """
        self.target_indoor_temp = float(new)
        self.ADapi.run_in(self.set_indoortemp, 5)


        # Helper functions to check windows
    def windowOpened(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to window opened
        """
        if self.windowsopened() != 0:
            self.windows_is_open = True
            self.ADapi.run_in(self.set_indoortemp, 1)

    def windowClosed(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to window closed
        """
        if self.windowsopened() == 0:
            self.window_last_opened = self.ADapi.datetime(aware=True)
            self.windows_is_open = False
            self.ADapi.run_in(self.set_indoortemp, 60)

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
            s.check_if_try_sceen_open()


    def get_in_temp(self) -> float:
        """ Returns indoor temperature
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
                    f"{self.indoor_sensor_temp} has been stale for {stale_time} Reloading integration",
                    level = 'INFO'
                )
                self.ADapi.call_service('homeassistant/reload_config_entry',
                    entity_id = self.indoor_sensor_temp
                )
                raise ValueError("Stale data")
        except (ValueError, TypeError) as ve:
            if self.backup_indoor_sensor_temp != None:
                try:
                    in_temp = float(self.ADapi.get_state(self.backup_indoor_sensor_temp, namespace = self.namespace))
                except (ValueError, TypeError) as ve:
                    in_temp = self.target_indoor_temp - 0.1
        except Exception as e:
            in_temp = self.target_indoor_temp - 0.1
            self.ADapi.log(
                f"Not able to set new inside temperature from {self.indoor_sensor_temp}. {e}",
                level = 'WARNING'
            )

        return in_temp


    def get_heater_temp(self) -> float:
        """ Returns set heater temperature and state for the climate entity.
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


    def doDaytimeSaving(self) -> bool:
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


    def set_indoortemp(self, kwargs) -> None:
        """ Function to set new temperature to heater based on indoor, outdoor, window and weather sensors
        """
        if self.automate:
            if self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off':
                return

            # Set variables from indoor sensor and heater
        in_temp:float = self.get_in_temp()
        heater_temp:float = self.get_heater_temp()

        doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
        if doDaytimeIncreasing:
            in_temp -= 0.5

        if self.doDaytimeSaving():
            in_temp += 0.5

        else:
            # Correct indoor temp when high amount of wind
            if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            # Correct indoor temp when rain
            elif RAIN_AMOUNT >= self.rain_level:
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher


        if self.away_temp != None:
            away_temp = self.away_temp
        else:
            away_temp = 10

        new_temperature = self.adjust_set_temperature_by(heater_temp, in_temp)

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
            new_temperature = away_temp
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


        new_temperature = float()
        # Holliday temperature
        if self.away_state:
            new_temperature = away_temp
            if in_temp > self.target_indoor_temp:
                if OUT_TEMP > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()

        else:
            new_temperature = self.adjust_set_temperature_by(heater_temp, in_temp)

                # Check if it is hot inside
            if (
                in_temp > self.target_indoor_temp + 0.2
                and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
            ):

                if OUT_TEMP > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()

        self.updateClimateTemperature(heater_temp = heater_temp, new_temperature = new_temperature)


    def updateClimateTemperature(self, heater_temp, new_temperature:int) -> None:
        """ Updates climate with new temperature and updates persistent storage
        """

        # Update with new temperature
        if heater_temp != round(new_temperature * 2, 0) / 2:
            self.ADapi.call_service('climate/set_temperature',
                entity_id = self.heater,
                temperature = new_temperature,
                namespace = self.namespace
            )
            self.heater_temp_last_changed = self.ADapi.datetime(aware=True)
        elif (
                self.usePersistentStorage   
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(hours = 2)
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_registered > datetime.timedelta(hours = 2)
            ):
                self.registerHeatingtemp()
                self.heater_temp_last_registered = self.ADapi.datetime(aware=True)


    def adjust_set_temperature_by(self, heater_set_temp:float, in_temp:float) -> float:
        """ Calculates if heater temperature needs to be adjusted based ontemperatures.
            Returns adjusted temperature.
        """
        new_temperature:float = heater_set_temp
        adjust_temp_by:float = 0

        if self.window_temp != None:
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

        persistent_temperature, valid_temp_data = self.getHeatingTemp()

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


    def registerHeatingtemp(self) -> None:
        """ Register set heater temp based on outside temperature and lux to persistent storage
        """
        with open(self.JSON_PATH, 'r') as json_read:
            heatingdevice_data = json.load(json_read)

        heatingData = heatingdevice_data[self.heater]['data']
        out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
        out_lux_str = str(math.floor(OUT_LUX / 5000))
        try:
            heater_temp = float(self.ADapi.get_state(self.heater,
                attribute='temperature',
                namespace = self.namespace
            ))
        except (ValueError, TypeError):
            return

        if (
            heater_temp >= self.target_indoor_temp + 4
            or heater_temp <= self.target_indoor_temp - 4
        ):
            return


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
            if counter > 100:
                counter = 10

            avgheating = round(((heatingData['temp'] * heatingData['Counter']) + heater_temp) / counter,1)
            newData = {"temp" : avgheating, "Counter" : counter}
            heatingdevice_data[self.heater]['data'][out_temp_str].update(
                {out_lux_str : newData}
            )

        with open(self.JSON_PATH, 'w') as json_write:
            json.dump(heatingdevice_data, json_write, indent = 4)


    def getHeatingTemp(self) -> (float, bool):
        """ Returns temperature from persistent storage and if value is valid
        """
        if self.usePersistentStorage:
            with open(self.JSON_PATH, 'r') as json_read:
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

    def setHeatingTempFromPersisten(self, kwargs) -> None:
        """ Function to set heater temp directly to data found in persistent storage
        """
        offset:float = 0.0
        if 'offset' in kwargs:
            offset = kwargs['offset']

        temp, valid_temp_data = self.getHeatingTemp()
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
        away_temp:float,
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
        json_path:str,
        away,
        name_of_notify_app,
        notify_reciever
    ):

        super().__init__(api,
            heater = heater,
            indoor_sensor_temp = indoor_sensor_temp,
            backup_indoor_sensor_temp = backup_indoor_sensor_temp,
            window_temp = window_temp,
            window_offset = window_offset,
            target_indoor_input = target_indoor_input,
            target_indoor_temp = target_indoor_temp,
            away_temp = away_temp,
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
            json_path = json_path,
            away = away,
            name_of_notify_app = name_of_notify_app,
            notify_reciever = notify_reciever
        )

        self.silence:list = silence
        self.fan_mode:str = self.ADapi.get_state(self.heater,
            attribute='fan_mode',
            namespace = self.namespace
        )

        self.fan_mode_persistent:str = self.fan_mode
        if self.usePersistentStorage:

            with open(self.JSON_PATH, 'r') as json_read:
                heatingdevice_data = json.load(json_read)

            if 'fan_mode' in heatingdevice_data[self.heater]:
                if heatingdevice_data[self.heater]['fan_mode'] != None:
                    self.fan_mode_persistent = heatingdevice_data[self.heater]['fan_mode']

        if (
            self.fan_mode_persistent == None
            or self.fan_mode_persistent == 'Silence'
        ):
            self.fan_mode_persistent = 'Auto'


    def set_indoortemp(self, kwargs) -> None:
        """ Function to set new temperature to heater based on indoor, outdoor, window and weather sensors
        """
        if self.automate:
            if self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off':
                return

            # Set variables from indoor sensor and heater
        in_temp:float = self.get_in_temp()
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
                    if self.ADapi.now_is_between(time['start'], time['stop']):
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
                    and self.fan_mode != None
                ):
                    self.fan_mode_persistent = self.fan_mode

                    if self.usePersistentStorage:
                        with open(self.JSON_PATH, 'r') as json_read:
                            heatingdevice_data = json.load(json_read)

                        heatingdevice_data[self.heater].update(
                            {'fan_mode' : self.fan_mode}
                        )

                        with open(self.JSON_PATH, 'w') as json_write:
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

                return

            doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
            if doDaytimeIncreasing:
                in_temp -= 0.5

            if self.doDaytimeSaving():
                in_temp += 0.5

            else:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

                # Correct indoor temp when rain
                elif RAIN_AMOUNT >= self.rain_level:
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            if self.away_temp != None:
                away_temp = self.away_temp
            else:
                away_temp = 10


            new_temperature = float()
            # Holliday temperature
            if self.away_state:
                new_temperature = away_temp
                if in_temp > self.target_indoor_temp:
                    if OUT_TEMP > self.screening_temp:
                        for s in self.screening:
                            s.try_screen_close()
                    if in_temp > self.target_indoor_temp + 0.6:
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
                        return

            else:
                new_temperature = self.adjust_set_temperature_by(heater_temp, in_temp)

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
                        except Exception as e:
                            self.ADapi.log(
                                f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                                level = 'DEBUG'
                            )
                        return

            # Update with new temperature
            self.updateClimateTemperature(heater_temp = heater_temp, new_temperature = new_temperature)


        elif ac_state == 'cool':
            # Setting temperature hvac_mode == cool

            doDaytimeIncreasing, afterDaytimeIncrease = self.DaytimeIncreasing()
            if doDaytimeIncreasing:
                in_temp += 0.5

            if self.doDaytimeSaving():
                in_temp -= 0.5

            else:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

                # Correct indoor temp when rain
                elif RAIN_AMOUNT >= self.rain_level:
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher
            
            # Holliday temperature
            if self.away_state:
                in_temp -= 3

            new_temperature = self.adjust_set_temperature_by(heater_temp, in_temp)

            for s in self.screening:
                s.try_screen_close()

            if (
                self.windows_is_open
                or (in_temp < self.target_indoor_temp + 3
                and self.away_state)
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

            if (
                OUT_TEMP < self.target_indoor_temp
                and in_temp < self.target_indoor_temp + 0.6
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

            # Update with new temperature
            if heater_temp != new_temperature:
                self.ADapi.call_service('climate/set_temperature',
                    entity_id = self.heater,
                    temperature = new_temperature,
                    namespace = self.namespace
                )
                self.heater_temp_last_changed = self.ADapi.datetime(aware=True)


        elif ac_state == 'fan_only':
            # Checking temperature hvac_mode == fan_only
            if (
                not self.windows_is_open
                and not self.away_state
            ):
                if (
                    in_temp > self.target_indoor_temp + 1
                    and OUT_TEMP > self.target_indoor_temp
                ):
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
                        self.ADapi.run_in(self.setHeatingTempFromPersisten, 10, offset = -1)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to heat for {self.heater}. Probably not supported. {e}",
                            level = 'DEBUG'
                        )
            elif (
                not self.windows_is_open # And vacation
            ):
                if in_temp <= self.target_indoor_temp -2:
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'heat',
                            namespace = self.namespace
                        )
                        # Set new temperature after change to heating
                        self.ADapi.run_in(self.set_indoortemp, 20)
                    except Exception as e:
                        self.ADapi.log(
                            f"Not able to set hvac_mode to heat for {self.heater}. Probably not supported. {e}",
                            level = 'DEBUG'
                        )
                elif (
                    in_temp > self.target_indoor_temp + 4
                    and OUT_TEMP > self.target_indoor_temp
                ):
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

            if in_temp > float(self.target_indoor_temp):
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
        self.tracker = not_when_home

            # Variables
        self.lux_close:int = lux_close
        self.lux_open_normal:int = lux_open
        self.lux_open_when_media_is_on:int = lux_open_when_media_is_on
        self.lux_open:int = self.lux_open_normal
        self.anemometer_speed_limit:int = anemometer_speed_limit

        self.event_handler = None

        self.screen_position = self.ADapi.get_state(self.screen, attribute='current_position')

        for mediaplayer in self.mediaplayers:
            self.ADapi.listen_state(self.media_on, mediaplayer,
                new = 'on',
                old = 'off',
                namespace = self.namespace
            )
            self.ADapi.listen_state(self.media_off, mediaplayer,
                new = 'off',
                old = 'on',
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


    def try_screen_close(self, lux_close:int = 0) -> None:
        """ Checks conditions to close screens.
        """
        if lux_close == 0:
            lux_close = self.lux_close
        if (
            RAIN_AMOUNT == 0
            and WIND_AMOUNT < self.anemometer_speed_limit
            and self.windowsopened() == 0
            and OUT_LUX >= lux_close
            and CLOUD_COVER < 70
        ):
            for person in self.tracker:
                if self.ADapi.get_state(person, namespace = self.namespace) == 'home':
                    return
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

                self.event_handler = self.ADapi.listen_event(self.weather_updated, 'WEATHER_CHANGE',
                    namespace = self.namespace
                )


    def weather_updated(self, event_name, data, kwargs) -> None:
        """ Listens for weather change from the weather app
        """
        if (
            float(data['rain']) > 0
            or float(data['wind']) > self.anemometer_speed_limit
            or float(data['lux']) < self.lux_open
        ):
            self.try_screen_open()


    def check_if_try_sceen_open(self) -> None:
        """ Checks conditions from configured weather sensors before trying to open screens.
        """
        if (
            RAIN_AMOUNT > 0
            or WIND_AMOUNT > self.anemometer_speed_limit
            or OUT_LUX < self.lux_open
        ):
           self.try_screen_open()


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
                and OUT_LUX <= 100
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
                if self.event_handler != None:
                    try:
                        self.ADapi.cancel_listen_event(self.event_handler)
                    except Exception as e:
                        self.ADapi.log(
                            f"Could not cancel listen event for handler: {self.event_handler} in try_screen_open",
                            level = 'DEBUG'
                        )


    def media_on(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to media turned on and sets lux open value
        """
        self.lux_open = self.lux_open_when_media_is_on


    def media_off(self, entity, attribute, old, new, kwargs) -> None:
        """ Reacts to media turned off and sets lux open value
        """
        if self.check_mediaplayers_off():
            self.lux_open = self.lux_open_normal


    def check_mediaplayers_off(self) -> bool:
        """ Returns true if all media players is off
        """
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