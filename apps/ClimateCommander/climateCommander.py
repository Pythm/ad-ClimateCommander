""" CLimate Control by Pythm
    Control your Airconditions / Heat pump based on outside temperature and your Screening covers based on inside temperature and lux sensors

    @Pythm / https://github.com/Pythm


"""

__version__ = "1.1.0"

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

class Climate(hass.Hass):

    def initialize(self):

        self.mqtt = None # Only initialize MQTT if needed

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
                self.set_state(away_state,
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
        self.weather_temperature = None
        self.outside_temperature = self.args.get('outside_temperature', None)
        self.backup_temp_handler = None
        screening_temp = self.args.get('screening_temp', 8)
        getting_cold = self.args.get('getting_cold', 18)

        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level:float = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed:int = self.args.get('anemometer_speed',40)

            # Setup Outside temperatures
        sensor_states = self.get_state(entity='weather')
        for sensor_id, sensor_states in sensor_states.items():
            if 'weather.' in sensor_id:
                self.weather_temperature = sensor_id

        if (
            not self.outside_temperature 
            and not self.weather_temperature
        ):
            self.log(
                "Outside temperature not configured. Please provide sensors or install Met.no in Home Assistant. "
                "https://www.home-assistant.io/integrations/met/",
                level = 'WARNING'
            )
            self.log("Aborting climate setup", level = 'WARNING')
            return

        global OUT_TEMP
        if self.outside_temperature:
            self.listen_state(self.outsideTemperatureUpdated, self.outside_temperature)
            try:
                OUT_TEMP = float(self.get_state(self.outside_temperature))
            except (ValueError, TypeError) as ve:
                if self.weather_temperature:
                    OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
                    self.backup_temp_handler = self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature,
                        attribute = 'temperature'
                    )
    
                self.log(
                    f"Outside temperature is not a number. Using {self.weather_temperature} for outside temperature. "
                    f" It is now {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} degrees outside. {ve}",
                    level = 'INFO'
                )
            except Exception as e:
                self.log(f"Outside temperature is not valid. {e}", level = 'WARNING')

        elif self.weather_temperature:
            self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature,
                attribute = 'temperature'
            )
            try:
                OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
            except (ValueError, TypeError) as ve:
                self.log(
                    f"Was not able to convert {self.weather_temperature} to a number: "
                    f"{self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')}. {ve}",
                    level = 'WARNING'
                )
            except Exception as e:
                self.log(
                    f"Was not able to convert {self.weather_temperature} to a number: "
                    f"{self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')}. {e}",
                    level = 'WARNING'
                )

            # Setup Rain sensor
        global RAIN_AMOUNT
        if self.rain_sensor:
            self.listen_state(self.rainSensorUpdated, self.rain_sensor)
            try:
                RAIN_AMOUNT = float(self.get_state(self.rain_sensor))
            except (ValueError) as ve:
                RAIN_AMOUNT = 0.0
                self.log(f"Rain sensor not valid. {ve}", level = 'DEBUG')
            except Exception as e:
                self.log(f"Rain sensor not valid. {e}", level = 'WARNING')
                RAIN_AMOUNT = 0.0

            # Setup Wind sensor
        global WIND_AMOUNT
        if self.anemometer:
            self.listen_state(self.anemometerUpdated, self.anemometer)
            try:
                WIND_AMOUNT = float(self.get_state(self.anemometer))
            except (ValueError) as ve:
                WIND_AMOUNT = 0.0
                self.log(f"Anemometer sensor not valid. {ve}", level = 'DEBUG')
            except Exception as e:
                self.log(f"Anemometer sensor not valid. {e}", level = 'WARNING')
                WIND_AMOUNT = 0.0

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
            if not self.mqtt:
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
            if not self.mqtt:
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
                notify_reciever = ac.get('notify_reciever', None)
            )
            self.heatingdevice.append(aircondition)
        

            # Configuration of heaters to command
        heaters = self.args.get('Heaters', [])
        for heater in heaters:
            heating = Heater(self,
                heater = heater['climate'],
                indoor_sensor_temp = heater.get('indoor_sensor_temp', None),
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
                notify_reciever = heater.get('notify_reciever', None)
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

        # Set proper value when weather sensors is updated
    def outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs):
        global OUT_TEMP
        try:
            OUT_TEMP = float(new)
        except (ValueError, TypeError) as ve:
            if self.weather_temperature:
                OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
                self.backup_temp_handler = self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature,
                    attribute = 'temperature'
                )

            self.log(
                f"Outside temperature is not valid. Using {self.weather_temperature} for outside temperature. "
                f"It is now {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} degrees outside. {ve}",
                level = 'INFO'
            )
        except Exception as e:
            self.log(
                "Outside temperature is not a number. Please provide sensors in configuration or install Met.no in Home Assistant. "
                "https://www.home-assistant.io/integrations/met/",
                level = 'WARNING'
            )
            self.log(
                f"{self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} {e}",
                level = 'INFO'
            )

        else:
            if self.backup_temp_handler != None:
                try:
                    self.cancel_listen_state(self.backup_temp_handler)
                except Exception as exc:
                    self.log(f"Could not stop {self.backup_temp_handler}. Exception: {exc}", level = 'DEBUG')
                self.backup_temp_handler = None


    def outsideBackupTemperatureUpdated(self, entity, attribute, old, new, kwargs):
        global OUT_TEMP
        if self.outside_temperature:
            try:
                OUT_TEMP = float(self.get_state(self.outside_temperature))
            except (ValueError, TypeError) as ve:
                if self.weather_temperature:
                    OUT_TEMP = float(new)
                self.log(
                    f"Outside temperature is not valid. Using backup from {self.weather_temperature} for outside temperature. "
                    f"It is now {new} degrees outside. Old temp was {old}. {ve}",
                    level = 'INFO'
                )

            except Exception as e:
                self.log(f"Failed to set Outside temperature {e}", level = 'WARNING')
        else: # Main outside temperature not provided. Setting temperature from backup
            OUT_TEMP = float(new)


    def rainSensorUpdated(self, entity, attribute, old, new, kwargs):
        global RAIN_AMOUNT
        try:
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            RAIN_AMOUNT = 0.0
            self.log(f"Not able to set new rain amount: {new}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Rain sensor not valid. {e}", level = 'WARNING')
            RAIN_AMOUNT = 0.0
        if RAIN_AMOUNT > 0.0:
            for ac in self.heatingdevice:
                ac.tryScreenOpen()
        

    def anemometerUpdated(self, entity, attribute, old, new, kwargs):
        global WIND_AMOUNT
        try:
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            WIND_AMOUNT = 0.0
            self.log(f"Not able to set new wind amount: {new}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Anemometer sensor not valid. {e}", level = 'WARNING')

        for ac in self.heatingdevice:
            if WIND_AMOUNT >= ac.anemometer_speed:
                ac.last_windy_time = self.datetime(aware=True)


        # LUX sensors
    def out_lux_state(self, entity, attribute, old, new, kwargs):
        if self.outLux1 != float(new):
            self.outLux1 = float(new)

            self.newOutLux()


    def out_lux_event_MQTT(self, event_name, data, kwargs):
        lux_data = json.loads(data['payload'])
        if 'illuminance_lux' in lux_data:
            if self.outLux1 != float(lux_data['illuminance_lux']):
                self.outLux1 = float(lux_data['illuminance_lux']) # Zigbee sensor
                self.newOutLux()
        elif 'value' in lux_data:
            if self.outLux1 != float(lux_data['value']):
                self.outLux1 = float(lux_data['value']) # Zwave sensor
                self.newOutLux()


    def newOutLux(self):
        global OUT_LUX
        if (
            self.datetime(aware=True) - self.lux_last_update2 > datetime.timedelta(minutes = 15)
            or self.outLux1 >= self.outLux2
        ):
            OUT_LUX = self.outLux1

        self.lux_last_update1 = self.datetime(aware=True)


    def out_lux_state2(self, entity, attribute, old, new, kwargs):
        if self.outLux2 != float(new):
            self.outLux2 = float(new)

            self.newOutLux2()


    def out_lux_event_MQTT2(self, event_name, data, kwargs):
        lux_data = json.loads(data['payload'])
        if 'illuminance_lux' in lux_data:
            if self.outLux2 != float(lux_data['illuminance_lux']):
                self.outLux2 = float(lux_data['illuminance_lux']) # Zigbee sensor
                self.newOutLux2()
        elif 'value' in lux_data:
            if self.outLux2 != float(lux_data['value']):
                self.outLux2 = float(lux_data['value']) # Zwave sensor
                self.newOutLux2()


    def newOutLux2(self):
        global OUT_LUX
        if (
            self.datetime(aware=True) - self.lux_last_update1 > datetime.timedelta(minutes = 15)
            or self.outLux2 >= self.outLux1
        ):
            OUT_LUX = self.outLux2

        self.lux_last_update2 = self.datetime(aware=True)


class Heater():
    """ Class to control room temperature with climate entity and weather/temperature sensors
    """

    def __init__(self, api,
        heater,
        indoor_sensor_temp,
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
        notify_reciever:list
    ):

        self.ADapi = api

        self.heater = heater

            # Sensors
        self.indoor_sensor_temp = indoor_sensor_temp
        if target_indoor_input != None:
            api.listen_state(self.updateTarget, target_indoor_input,
                namespace = namespace
            )
            self.target_indoor_temp = float(api.get_state(target_indoor_input, namespace = namespace))
        else:
            self.target_indoor_temp:float = target_indoor_temp
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
                lux_open_tv = s.get('lux_open_media', 4000),
                anemometer_speed = anemometer_speed,
                not_when_home = s.get('not_when_home', []),
                mediaplayers = s.get('mediaplayers', [])
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
        self.notify_app = Notify_Mobiles(api)
        
        self.recipients:list = notify_reciever

            # Setup runtimes
        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/720)+1)*720
        runtime = runtime.replace(minute=0, second=10, microsecond=0) + datetime.timedelta(seconds=addseconds)
        self.ADapi.run_every(self.set_indoortemp, runtime, 720)


        # Sets Vacation status
    def awayStateListen(self, entity, attribute, old, new, kwargs) -> None:
        self.away_state = new == 'on'
        self.ADapi.run_in(self.heater_setNewValues, 5)


        # Indoor target temperature
    def updateTarget(self, entity, attribute, old, new, kwargs):
        self.target_indoor_input = new


        # Helper functions to check windows
    def windowOpened(self, entity, attribute, old, new, kwargs):
        if self.windowsopened() != 0:
            self.windows_is_open = True
            self.ADapi.run_in(self.set_indoortemp, 1)

    def windowClosed(self, entity, attribute, old, new, kwargs):
        if self.windowsopened() == 0:
            self.window_last_opened = self.ADapi.datetime(aware=True)
            self.windows_is_open = False
            self.ADapi.run_in(self.set_indoortemp, 60)

    def windowsopened(self):
        opened = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window, namespace = self.namespace) == 'on':
                opened += 1
        return opened


        # Function to call screens in child class
    def tryScreenOpen(self):
        for s in self.screening:
            s.try_screen_open()



        # Sets climate temperature based on sensors provided
    def set_indoortemp(self, kwargs):
        global OUT_TEMP
        global RAIN_AMOUNT
        global WIND_AMOUNT

        if self.automate:
            if self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off':
                return

        in_temp:float = 0.0
        heater_temp:float = 0.0

        try:
            in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
        except (ValueError, TypeError) as ve:
            in_temp = self.target_indoor_temp - 0.1
            self.ADapi.log(f"Not able to set new inside temperature from {self.indoor_sensor_temp}. {ve}", level = 'DEBUG')
        except Exception as e:
            in_temp = self.target_indoor_temp - 0.1
            self.ADapi.log(f"Not able to set new inside temperature from {self.indoor_sensor_temp}. {e}", level = 'WARNING')

        try:
            heater_temp = float(self.ADapi.get_state(self.heater,
                attribute='temperature',
                namespace = self.namespace
            ))
        except (ValueError, TypeError):
            heater_temp = in_temp
        except Exception as e:
            heater_temp = in_temp
            self.ADapi.log(f"Not able to set new inside temperature from {self.heater}. {e}", level = 'WARNING')

        self.tryScreenOpen()

        # Daytime Savings
        doDaytimeSaving = False
        for daytime in self.daytime_savings:
            if 'start' in daytime and 'stop' in daytime:
                if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                    doDaytimeSaving = True
                    if 'presence' in daytime:
                        for presence in daytime['presence']:
                            if self.ADapi.get_state(presence) == 'home':
                                doDaytimeSaving = False

            elif 'presence' in daytime:
                doDaytimeSaving = True
                for presence in daytime['presence']:
                    if self.ADapi.get_state(presence) == 'home':
                        doDaytimeSaving = False
        if doDaytimeSaving:
            in_temp += 0.5

        # Daytime Increasing temperature
        doDaytimeIncreasing = False
        for daytime in self.daytime_increasing:
            if 'start' in daytime and 'stop' in daytime:
                if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                    doDaytimeIncreasing = True
                    if 'presence' in daytime:
                        doDaytimeIncreasing = False
                        for presence in daytime['presence']:
                            if self.ADapi.get_state(presence) == 'home':
                                doDaytimeIncreasing = True

        if doDaytimeIncreasing:
            in_temp -= 0.5

        if not doDaytimeSaving:
            # Correct indoor temp when high amount of wind
            if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

            # Correct indoor temp when rain
            elif RAIN_AMOUNT >= self.rain_level:
                in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher


        new_temperature = heater_temp
        if self.away_temp != None:
            away_temp = self.away_temp
        else:
            away_temp = 10

        new_temperature = self.adjust_set_temperature_by(new_temperature, in_temp)


        # Windows
        if (
            not self.windows_is_open
            and self.notify_on_window_closed
            and in_temp >= self.target_indoor_temp + 10
            and OUT_TEMP > self.getting_cold
        ):
            for r in self.recipients:
                self.ADapi.notify(
                    f"No Window near {self.heater} is open and it is getting hot inside! {in_temp}°",
                    title = "Window closed",
                    name = r
                )
            self.notify_on_window_closed = False
        if self.windows_is_open:
            new_temperature = away_temp
            if (
                self.notify_on_window_open
                and OUT_TEMP < self.getting_cold
                and in_temp < self.getting_cold
            ):
                for r in self.recipients:
                    self.ADapi.notify(
                        f"Window near {self.heater} is open and inside temperature is {in_temp}",
                        title = "Window open",
                        name = r
                    )
                self.notify_on_window_open = False


        # Holliday temperature
        if self.away_state:
            new_temperature = away_temp
            if in_temp > self.target_indoor_temp:
                if OUT_TEMP > self.screening_temp:
                    for s in self.screening:
                        s.try_screen_close()

        # Check if it is hot inside
        elif (
            in_temp > self.target_indoor_temp + 0.2
            and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
        ):

            if OUT_TEMP > self.screening_temp:
                for s in self.screening:
                    s.try_screen_close()

        # Update with new temperature
        if heater_temp != new_temperature:
            self.ADapi.call_service('climate/set_temperature',
                entity_id = self.heater,
                temperature = new_temperature
            )
            self.heater_temp_last_changed = self.ADapi.datetime(aware=True)
        elif (
                self.usePersistentStorage   
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(minutes = 60)
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_registered > datetime.timedelta(minutes = 60)
            ):
                self.registerHeatingtemp()
                self.heater_temp_last_registered = self.ADapi.datetime(aware=True)


    def adjust_set_temperature_by(self, new_temperature:float, in_temp:float):
        adjust_temp_by:float = 0
        if self.window_temp != None:
            try:
                window_temp = float(self.ADapi.get_state(self.window_temp, namespace = self.namespace))
            except (TypeError, AttributeError):
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"{self.window_temp} has no temperature. Probably offline", level = 'DEBUG')
            except Exception as e:
                window_temp = self.target_indoor_temp + self.window_offset
                self.ADapi.log(f"Not able to get temperature from {self.window_temp}. {e}", level = 'DEBUG')
            if window_temp > self.target_indoor_temp + self.window_offset:
                adjust_temp_by = round((self.target_indoor_temp + self.window_offset) - window_temp,1)
                """ Logging of window temp turning down heating """
                global OUT_LUX
                if OUT_LUX < 2000:
                    self.ADapi.log(
                        f"{self.ADapi.get_state(self.window_temp, namespace = self.namespace, attribute = 'friendly_name')} "
                        f"is {window_temp}. That is {-adjust_temp_by} above offset. "
                        f"The lux outside is {OUT_LUX}",
                        level = 'INFO'
                        )
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
            and adjust_temp_by < 0.5
        ):
            adjust_temp_by = 0
        
        
        if (
            self.ADapi.datetime(aware=True) - self.window_last_opened > datetime.timedelta(hours = 1)
            or in_temp > self.target_indoor_temp
            or in_temp < self.target_indoor_temp -1
        ):
            new_temperature += adjust_temp_by

        if new_temperature > self.target_indoor_temp + 6:
            new_temperature = self.target_indoor_temp + 6
        elif new_temperature < self.target_indoor_temp - 6:
            new_temperature = self.target_indoor_temp - 6

        return new_temperature


    def registerHeatingtemp(self) -> None:
        global OUT_TEMP
        global OUT_LUX

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
                return

            avgheating = round(((heatingData['temp'] * heatingData['Counter']) + heater_temp) / counter,1)
            newData = {"temp" : avgheating, "Counter" : counter}
            heatingdevice_data[self.heater]['data'][out_temp_str].update(
                {out_lux_str : newData}
            )

        with open(self.JSON_PATH, 'w') as json_write:
            json.dump(heatingdevice_data, json_write, indent = 4)


    def getHeatingTemp(self):
        global OUT_TEMP
        global OUT_LUX

        with open(self.JSON_PATH, 'r') as json_read:
            heatingdevice_data = json.load(json_read)

        heatingData = heatingdevice_data[self.heater]['data']
        out_temp_str = str(math.floor(OUT_TEMP / 2.) * 2)
        out_lux_str = str(math.floor(OUT_LUX / 5000))
        if heatingdevice_data[self.heater]['data']:
            if not out_temp_str in heatingdevice_data[self.heater]['data']:
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

            if not out_lux_str in heatingdevice_data[self.heater]['data'][out_temp_str]:
                lux_diff:int = 0
                closest_lux:int
                for luxs in heatingdevice_data[self.heater]['data'][out_temp_str]:
                    if OUT_LUX > float(luxs):
                        if lux_diff != 0:
                            if lux_diff < OUT_LUX - float(luxs):
                                continue
                        lux_diff = OUT_LUX - float(luxs)
                        closest_lux = luxs
                    else:
                        if lux_diff != 0:
                            if lux_diff < float(luxs) - OUT_LUX:
                                continue
                        lux_diff = float(luxs) - OUT_LUX
                        closest_lux = luxs
                out_lux_str = closest_lux
            
            temp = heatingdevice_data[self.heater]['data'][out_temp_str][out_lux_str]['temp']
            self.ADapi.log(f"Temp from Json: {temp}")
            return temp

        else:
            try:
                return float(self.ADapi.get_state(self.heater,
                    attribute='temperature',
                    namespace = self.namespace
                ))
            except (ValueError, TypeError):
                return self.target_indoor_temp
            except Exception as e:
                return self.target_indoor_temp

    def setHeatingTemp(self, kwargs):
        
        temp = self.getHeatingTemp()

        # Update with new temperature
        self.ADapi.call_service('climate/set_temperature',
            entity_id = self.heater,
            temperature = temp
        )
        self.heater_temp_last_changed = self.ADapi.datetime(aware=True)


class Aircondition(Heater):
    """ Class to control room temperature with climate entity and weather/temperature sensors
    """

    def __init__(self, api,
        heater,
        indoor_sensor_temp,
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
        notify_reciever
    ):

        super().__init__(api,
            heater = heater,
            indoor_sensor_temp = indoor_sensor_temp,
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
            notify_reciever = notify_reciever
        )

        self.silence:list = silence
        self.fan_mode:str = self.ADapi.get_state(self.heater,
            attribute='fan_mode',
            namespace = self.namespace
        )
        if self.fan_mode == 'Silence':

            with open(self.JSON_PATH, 'r') as json_read:
                heatingdevice_data = json.load(json_read)

            if 'fan_mode' in heatingdevice_data[self.heater]:
                self.fan_mode = heatingdevice_data[self.heater]['fan_mode']


        # Sets climate temperature based on sensors provided
    def set_indoortemp(self, kwargs):
        global OUT_TEMP
        global RAIN_AMOUNT
        global WIND_AMOUNT

        if self.automate:
            if self.ADapi.get_state(self.automate, namespace = self.namespace) == 'off':
                return

        in_temp:float = 0.0
        heater_temp:float = 0.0
        ac_state = 'heat'

        try:
            in_temp = float(self.ADapi.get_state(self.indoor_sensor_temp, namespace = self.namespace))
        except (ValueError, TypeError) as ve:
            in_temp = self.target_indoor_temp - 0.1
            self.ADapi.log(f"Not able to set new inside temperature from {self.indoor_sensor_temp}. {ve}", level = 'DEBUG')
        except Exception as e:
            in_temp = self.target_indoor_temp - 0.1
            self.ADapi.log(f"Not able to set new inside temperature from {self.indoor_sensor_temp}. {e}", level = 'WARNING')

        try:
            heater_temp = float(self.ADapi.get_state(self.heater,
                attribute='temperature',
                namespace = self.namespace
            ))
        except (ValueError, TypeError):
            heater_temp = in_temp
            ac_state = self.ADapi.get_state(self.heater, namespace = self.namespace)
        except Exception as e:
            heater_temp = in_temp
            self.ADapi.log(f"Not able to set new inside temperature from {self.heater}. {e}", level = 'WARNING')
        else:
            ac_state = self.ADapi.get_state(self.heater, namespace = self.namespace)

        try:
            self.preset_mode = self.ADapi.get_state(self.heater,
                attribute='preset_mode',
                namespace = self.namespace
            )
        except (ValueError, TypeError) as ve:
            self.preset_mode = None
            self.ADapi.log(f"Not able to get preset mode from {self.heater}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.preset_mode = None

        
        # Set silence preset
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
                if (
                    self.fan_mode == 'Silence'
                    or self.fan_mode == None
                ):

                    with open(self.JSON_PATH, 'r') as json_read:
                        heatingdevice_data = json.load(json_read)

                    if 'fan_mode' in heatingdevice_data[self.heater]:
                        self.fan_mode = heatingdevice_data[self.heater]['fan_mode']
                    else:
                        self.fan_mode = 'Auto'


                self.ADapi.call_service('climate/set_fan_mode',
                    entity_id = self.heater,
                    fan_mode = self.fan_mode,
                    namespace = self.namespace
                )


        self.tryScreenOpen()

        if ac_state == 'heat':
            """ Setting temperature hvac_mode == heat
            """
            # Daytime Savings
            doDaytimeSaving = False
            for daytime in self.daytime_savings:
                if 'start' in daytime and 'stop' in daytime:
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeSaving = True
                        if 'presence' in daytime:
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence) == 'home':
                                    doDaytimeSaving = False

                elif 'presence' in daytime:
                    doDaytimeSaving = True
                    for presence in daytime['presence']:
                        if self.ADapi.get_state(presence) == 'home':
                            doDaytimeSaving = False
            if doDaytimeSaving:
                in_temp += 0.5

            # Daytime Increasing temperature
            doDaytimeIncreasing = False
            for daytime in self.daytime_increasing:
                if 'start' in daytime and 'stop' in daytime:
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeIncreasing = True
                        if 'presence' in daytime:
                            doDaytimeIncreasing = False
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence) == 'home':
                                    doDaytimeIncreasing = True

            if doDaytimeIncreasing:
                in_temp -= 0.5

            if not doDaytimeSaving:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

                # Correct indoor temp when rain
                elif RAIN_AMOUNT >= self.rain_level:
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher


            new_temperature = heater_temp
            if self.away_temp != None:
                away_temp = self.away_temp
            else:
                away_temp = 10

            new_temperature = self.adjust_set_temperature_by(new_temperature, in_temp)

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
                    for r in self.recipients:
                        self.ADapi.notify(
                            f"Window near {self.heater} is open and inside temperature is {in_temp}",
                            title = "Window open",
                            name = r
                        )
                    self.notify_on_window_open = False

                return


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
                                hvac_mode = 'fan_only'
                            )
                        except Exception as e:
                            self.ADapi.log(
                                f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}",
                                level = 'INFO'
                            )
                        return

            else:

                # Check if there is a need to boost the ac when windy
                if (
                    self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 25)
                    and in_temp < self.target_indoor_temp -0.7
                ):
                    if self.ADapi.get_state(self.heater, attribute='fan_mode') != 'Silence':
                        if 'boost' in self.ADapi.get_state(self.heater, attribute='preset_modes'):
                            if self.ADapi.get_state(self.heater, attribute='preset_mode') != 'boost':
                                self.ADapi.call_service('climate/set_preset_mode',
                                    entity_id = self.heater,
                                    preset_mode = 'boost'
                                )


                elif self.ADapi.get_state(self.heater, attribute='preset_mode') == 'boost':
                    self.ADapi.call_service('climate/set_preset_mode',
                        entity_id = self.heater,
                        preset_mode = 'none'
                    )

                # Check if it is hot inside
                if (
                    in_temp > self.target_indoor_temp + 0.2
                    and self.ADapi.datetime(aware=True) - self.last_windy_time > datetime.timedelta(minutes = 45)
                ):

                    if OUT_TEMP > self.screening_temp:
                        for s in self.screening:
                            s.try_screen_close()
                    if in_temp > self.target_indoor_temp + 0.6:
                        try:
                            self.ADapi.call_service('climate/set_hvac_mode',
                                entity_id = self.heater,
                                hvac_mode = 'fan_only'
                            )
                        except Exception as e:
                            self.ADapi.log(f"Not able to set hvac_mode to fan_only for {self.heater}. Probably not supported. {e}", level = 'DEBUG')
                        return

            # Update with new temperature
            if heater_temp != round(new_temperature * 2, 0) / 2:
                self.ADapi.call_service('climate/set_temperature',
                    entity_id = self.heater,
                    temperature = new_temperature
                )
                self.heater_temp_last_changed = self.ADapi.datetime(aware=True)

            elif (
                self.usePersistentStorage   
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_changed > datetime.timedelta(minutes = 60)
                and self.ADapi.datetime(aware=True) - self.heater_temp_last_registered > datetime.timedelta(minutes = 60)
            ):
                self.registerHeatingtemp()
                self.heater_temp_last_registered = self.ADapi.datetime(aware=True)


        elif ac_state == 'cool':
            """ Setting temperature hvac_mode == cool
            """
                        # Daytime Savings
            doDaytimeSaving = False
            for daytime in self.daytime_savings:
                if 'start' in daytime and 'stop' in daytime:
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeSaving = True
                        if 'presence' in daytime:
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence) == 'home':
                                    doDaytimeSaving = False

                elif 'presence' in daytime:
                    doDaytimeSaving = True
                    for presence in daytime['presence']:
                        if self.ADapi.get_state(presence) == 'home':
                            doDaytimeSaving = False
            if doDaytimeSaving:
                in_temp -= 0.5

            # Daytime Increasing temperature
            doDaytimeIncreasing = False
            for daytime in self.daytime_increasing:
                if 'start' in daytime and 'stop' in daytime:
                    if self.ADapi.now_is_between(daytime['start'], daytime['stop']):
                        doDaytimeIncreasing = True
                        if 'presence' in daytime:
                            doDaytimeIncreasing = False
                            for presence in daytime['presence']:
                                if self.ADapi.get_state(presence) == 'home':
                                    doDaytimeIncreasing = True

            if doDaytimeIncreasing:
                in_temp += 0.5

            if not doDaytimeSaving:
                # Correct indoor temp when high amount of wind
                if self.ADapi.datetime(aware=True) - self.last_windy_time < datetime.timedelta(minutes = 30):
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher

                # Correct indoor temp when rain
                elif RAIN_AMOUNT >= self.rain_level:
                    in_temp -= 0.3 # Trick indoor temp sensor to set temp a bit higher
            
            # Holliday temperature
            if self.away_state:
                in_temp -= 3

            new_temperature = heater_temp

            new_temperature = self.adjust_set_temperature_by(new_temperature, in_temp)

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
                        hvac_mode = 'fan_only'
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
                    temperature = new_temperature
                )
                self.heater_temp_last_changed = self.ADapi.datetime(aware=True)


        elif ac_state == 'fan_only':
            """ Setting temperature hvac_mode == fan_only
            """
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
                            hvac_mode = 'cool'
                        )
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to cool for {self.heater}. Probably not supported. {e}", level = 'DEBUG')

                elif in_temp <= self.target_indoor_temp:
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'heat'
                        )
                        self.ADapi.run_in(self.setHeatingTemp, 10)
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to heat for {self.heater}. Probably not supported. {e}", level = 'DEBUG')
            elif (
                not self.windows_is_open # And vacation
            ):
                if in_temp <= self.target_indoor_temp -2:
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'heat'
                        )
                        self.ADapi.run_in(self.setHeatingTemp, 10)
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to heat for {self.heater}. Probably not supported. {e}", level = 'DEBUG')
                elif (
                    in_temp > self.target_indoor_temp + 4
                    and OUT_TEMP > self.target_indoor_temp
                ):
                    try:
                        self.ADapi.call_service('climate/set_hvac_mode',
                            entity_id = self.heater,
                            hvac_mode = 'cool'
                        )
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to cool for {self.heater}. Probably not supported. {e}", level = 'DEBUG')


            elif (
                self.windows_is_open 
                and self.notify_on_window_open 
                and in_temp < self.getting_cold
            ):
                for r in self.recipients:
                    self.ADapi.notify(
                        f"Window near {self.heater} is open and inside temperature is {in_temp}",
                        title = "Window open",
                        name = r
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
                f"Unregisterd AC state: {ac_state}. {self.ADapi.get_state(self.heater, attribute='friendly_name')}. Will not automate",
                level = 'WARNING'
            )


class Screen():
    """ Class to control cover entities based on inside temperature and weather sensors
    """

    def __init__(self, api,
        screen = None,
        windowsensors = [],
        lux_close = 40000,
        lux_open = 15000,
        lux_open_tv = 4000,
        anemometer_speed = 40,
        not_when_home = [],
        mediaplayers = []
    ):

        self.ADapi = api

        self.screen = screen
        self.windowsensors = windowsensors
        self.lux_close = float(lux_close)
        self.lux_open_normal = float(lux_open)
        self.lux_open_tv = float(lux_open_tv)
        self.mediaplayers = mediaplayers
        self.lux_open = self.lux_open_normal
        self.anemometer_speed = anemometer_speed
        self.tracker = not_when_home

        self.position = self.ADapi.get_state(self.screen, attribute='current_position')

        for mediaplayer in self.mediaplayers:
            self.ADapi.listen_state(self.media_on, mediaplayer,
                new = 'on',
                old = 'off'
            )
            self.ADapi.listen_state(self.media_off, mediaplayer,
                new = 'off',
                old = 'on'
            )


    def windowsopened(self):
        opened = 0
        for window in self.windowsensors:
            if self.ADapi.get_state(window) == 'on':
                opened += 1
        return opened


    def try_screen_close(self, lux_close = 0):
        global RAIN_AMOUNT
        global WIND_AMOUNT
        global OUT_LUX

        if lux_close == 0:
            lux_close = self.lux_close
        if (
            RAIN_AMOUNT == 0
            and WIND_AMOUNT < self.anemometer_speed
            and self.windowsopened() == 0
            and OUT_LUX >= lux_close
        ):
            for person in self.tracker:
                if self.ADapi.get_state(person) == 'home':
                    return
            if (
                self.ADapi.get_state(self.screen, attribute='current_position') == self.position
                and self.ADapi.get_state(self.screen, attribute='current_position') == 100
            ):
                self.ADapi.call_service('cover/close_cover', entity_id= self.screen)
                self.position = 0


    def try_screen_open(self):
        global RAIN_AMOUNT
        global WIND_AMOUNT
        global OUT_LUX
        if (
            self.windowsopened() == 0
            and self.position != 100
        ):
            openme = False
            if (
                RAIN_AMOUNT > 0
                or WIND_AMOUNT > self.anemometer_speed
            ):
                openme = True
            if (
                self.ADapi.get_state(self.screen, attribute='current_position') != self.position
                and OUT_LUX <= 100
            ):
                openme = True
            elif (
                self.ADapi.get_state(self.screen, attribute='current_position') == self.position
                and OUT_LUX < self.lux_open
            ):
                openme = True
            if openme:
                self.ADapi.call_service('cover/open_cover', entity_id= self.screen)
                self.position = 100


    def media_on(self, entity, attribute, old, new, kwargs):
        self.lux_open = self.lux_open_tv


    def media_off(self, entity, attribute, old, new, kwargs):
        if self.check_mediaplayers_off():
            self.lux_open = self.lux_open_normal


    def check_mediaplayers_off(self):
        for mediaplayer in self.mediaplayers:
            if self.ADapi.get_state(mediaplayer) == 'on':
                return False
        return True


class Notify_Mobiles:
    """ Class to send notification with 'notify' HA integration
    """

    def __init__(self, api):
        self.ADapi = api


    def send_notification(self,
        message = 'Message',
        message_title = 'title',
        message_recipient = ['all']
    ):
        if message_recipient == [{'reciever': 'all'}]:
            self.ADapi.notify(f"{message}", title = f"{message_title}")
        else:
            for reciever in message_recipient:
                self.ADapi.notify(f"{message}", title = f"{message_title}", name = f"{reciever}")
