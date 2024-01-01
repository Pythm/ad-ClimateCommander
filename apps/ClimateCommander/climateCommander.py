""" CLimate Control by Pythm
    Control your Airconditions / Heat pump based on outside temperature and your Screening covers based on inside temperature and lux sensors

    @Pythm / https://github.com/Pythm
"""

__version__ = "1.0.0"

import hassapi as hass
import datetime
import json

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

        self.mqtt = self.get_plugin_api("MQTT")

        # Namespaces for HASS and MQTT
        HASS_namespace = self.args.get('HASS_namespace', 'default')
        MQTT_namespace = self.args.get('MQTT_namespace', 'default')
    
            # Default away state for saving purposes
        if 'away_state' in self.args :
            self.away_state = self.args['away_state']
        else :
            self.away_state = 'input_boolean.vacation'
            if not self.entity_exists(self.get_entity(self.away_state), namespace = HASS_namespace) :
                self.set_state(self.away_state, state = 'off', namespace = HASS_namespace)
            else :
                self.log("'away_state' not configured. Using 'input_boolean.vacation' as default away state", level = 'WARNING')

        global RAIN_AMOUNT
        global WIND_AMOUNT

            # Weather sensors
        self.weather_temperature = None
        self.outside_temperature = self.args.get('outside_temperature', None)
        self.backup_temp_handler = None
        self.rain_sensor = self.args.get('rain_sensor', None)
        self.rain_level = self.args.get('rain_level',3)
        self.anemometer = self.args.get('anemometer', None)
        self.anemometer_speed = self.args.get('anemometer_speed',40)

            # Outside temperatures
        sensor_states = self.get_state(entity='weather')
        for sensor_id, sensor_states in sensor_states.items():
            if 'weather.' in sensor_id :
                self.weather_temperature = sensor_id
        if not self.outside_temperature and not self.weather_temperature :
            self.log("Outside temperature not configured. Please provide sensors or install Met.no in Home Assistant. https://www.home-assistant.io/integrations/met/", level = 'WARNING')
            self.log("Aborting climate setup", level = 'WARNING')
            return
        global OUT_TEMP
        if self.outside_temperature :
            self.listen_state(self.outsideTemperatureUpdated, self.outside_temperature)
            try :
                OUT_TEMP = float(self.get_state(self.outside_temperature))
            except (ValueError, TypeError) as ve:
                if self.weather_temperature :
                    OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
                    self.backup_temp_handler = self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature, attribute = 'temperature')
                self.log(f"Outside temperature is not a number. Using {self.weather_temperature} for outside temperature. It is now {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} degrees outside. {ve}", level = 'INFO')
            except Exception as e:
                self.log(f"Outside temperature is not valid. {e}", level = 'WARNING')

        elif self.weather_temperature :
            self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature, attribute = 'temperature')
            try :
                OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
            except (ValueError, TypeError) as ve:
                self.log(f"Was not able to convert {self.weather_temperature} to a number: {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')}. {ve}", level = 'WARNING')
            except Exception as e:
                self.log(f"Was not able to convert {self.weather_temperature} to a number: {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')}. {e}", level = 'WARNING')

            # Rain sensor
        if self.rain_sensor :
            self.listen_state(self.rainSensorUpdated, self.rain_sensor)
            try :
                RAIN_AMOUNT = float(self.get_state(self.rain_sensor))
            except (ValueError) as ve:
                RAIN_AMOUNT = 0.0
                self.log(f"Rain sensor not valid. {ve}", level = 'DEBUG')
            except Exception as e:
                self.log(f"Rain sensor not valid. {e}", level = 'WARNING')
                RAIN_AMOUNT = 0.0
            # Wind sensor
        if self.anemometer :
            self.listen_state(self.anemometerUpdated, self.anemometer)
            try :
                WIND_AMOUNT = float(self.get_state(self.anemometer))
            except (ValueError) as ve:
                WIND_AMOUNT = 0.0
                self.log(f"Anemometer sensor not valid. {ve}", level = 'DEBUG')
            except Exception as e:
                self.log(f"Anemometer sensor not valid. {e}", level = 'WARNING')
                WIND_AMOUNT = 0.0

            # Lux sensors
        self.outLux1:float = 0.0
        self.outLux2:float = 0.0
        self.lux_last_update1 = self.datetime(aware=True) - datetime.timedelta(minutes = 20) # Helpers for last updated when two outdoor lux sensors in use
        self.lux_last_update2 = self.datetime(aware=True) - datetime.timedelta(minutes = 20)

        if 'OutLux_sensor' in self.args :
            lux_sensor = self.args['OutLux_sensor']
            self.listen_state(self.out_lux_state, lux_sensor, namespace = HASS_namespace)
        if 'OutLuxMQTT' in self.args :
            out_lux_sensor = self.args['OutLuxMQTT']
            self.mqtt.mqtt_subscribe(out_lux_sensor)
            self.mqtt.listen_event(self.out_lux_event_MQTT, "MQTT_MESSAGE", topic = out_lux_sensor, namespace = MQTT_namespace)

        if 'OutLux_sensor_2' in self.args :
            lux_sensor = self.args['OutLux_sensor_2']
            self.listen_state(self.out_lux_state2, lux_sensor, namespace = HASS_namespace)
        if 'OutLuxMQTT_2' in self.args :
            out_lux_sensor = self.args['OutLuxMQTT_2']
            self.mqtt.mqtt_subscribe(out_lux_sensor)
            self.mqtt.listen_event(self.out_lux_event_MQTT2, "MQTT_MESSAGE", topic = out_lux_sensor, namespace = MQTT_namespace)

            # Configuration of airconditions
        self.airconditions:list = []
        climate = self.args.get('airconditions')
        for ac in climate :
            aircondition = Aircondition(self,
                ac = ac['climate'],
                indoor_temp = ac['indoor_temp'],
                target_indoor_temp = ac.get('target_indoor_temp', 23),
                temperatures = ac.get('temperatures', []),
                windowsensors = ac.get('windowsensors', []),
                anemometer_speed = ac.get('anemometer_speed', self.anemometer_speed),
                daytime_savings = ac.get('daytime_savings', {}),
                daytime_increasing = ac.get('daytime_increasing', {}),
                away_state = ac.get('away_state', self.away_state),
                screens = ac.get('screening', {}),
                screening_temp = ac.get('screening_temp', 8),
                hvac_fan_only_above = ac.get('hvac_fan_only_above', 24),
                hvac_notify_above = ac.get('hvac_notify_above', 28),
                hvac_cooling_above = ac.get('hvac_cooling_above', 28),
                hvac_cooling_temp = ac.get('hvac_cooling_temp', 22),
                notify_reciever = ac.get('notify_reciever', None),
                notify_title = ac.get('notify_title', 'Window'),
                notify_message_cold = ac.get('notify_message_cold', 'It\'s getting cold inside and window is open. Temperature is '),
                notify_message_warm = self.args.get('notify_message_warm', 'It\'s getting hot inside and temperature is '))
            self.airconditions.append(aircondition)


    def outsideTemperatureUpdated(self, entity, attribute, old, new, kwargs):
        global OUT_TEMP
        try :
            OUT_TEMP = float(new)
        except (ValueError, TypeError) as ve:
            if self.weather_temperature :
                OUT_TEMP = float(self.get_state(entity_id = self.weather_temperature, attribute = 'temperature'))
                self.backup_temp_handler = self.listen_state(self.outsideBackupTemperatureUpdated, self.weather_temperature, attribute = 'temperature')
            self.log(f"Outside temperature is not valid. Using {self.weather_temperature} for outside temperature. It is now {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} degrees outside. {ve}", level = 'INFO')
        except Exception as e:
            self.log("Outside temperature is not a number. Please provide sensors in configuration or install Met.no in Home Assistant. https://www.home-assistant.io/integrations/met/", level = 'WARNING')
            self.log(f" {self.get_state(entity_id = self.weather_temperature, attribute = 'temperature')} {e}", level = 'INFO')
        else :
            if self.backup_temp_handler != None :
                try :
                    self.cancel_listen_state(self.backup_temp_handler)
                except Exception as exc :
                    self.log(f"Could not stop {self.backup_temp_handler}", level = 'DEBUG')
                self.backup_temp_handler = None


    def outsideBackupTemperatureUpdated(self, entity, attribute, old, new, kwargs):
        global OUT_TEMP
        if self.outside_temperature :
            try :
                OUT_TEMP = float(self.get_state(self.outside_temperature))
            except (ValueError, TypeError) as ve:
                if self.weather_temperature :
                    OUT_TEMP = float(new)
                self.log(f"Outside temperature is not valid. Using backup from {self.weather_temperature} for outside temperature. It is now {new} degrees outside. Old temp was {old}. {ve}", level = 'INFO')
            except Exception as e:
                self.log(f"Failed to set Outside temperature {e}", level = 'WARNING')
        else : # Main outside temperature not provided. Setting temperature from backup
            OUT_TEMP = float(new)

    def rainSensorUpdated(self, entity, attribute, old, new, kwargs):
        global RAIN_AMOUNT
        try :
            RAIN_AMOUNT = float(new)
        except ValueError as ve:
            RAIN_AMOUNT = 0.0
            self.log(f"Not able to set new rain amount: {new}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Rain sensor not valid. {e}", level = 'WARNING')
            RAIN_AMOUNT = 0.0
        if RAIN_AMOUNT > 0.0 :
            for ac in self.airconditions :
                ac.tryScreenOpen()
        
    def anemometerUpdated(self, entity, attribute, old, new, kwargs):
        global WIND_AMOUNT
        try :
            WIND_AMOUNT = float(new)
        except ValueError as ve:
            WIND_AMOUNT = 0.0
            self.log(f"Not able to set new wind amount: {new}. {ve}", level = 'DEBUG')
        except Exception as e:
            self.log(f"Anemometer sensor not valid. {e}", level = 'WARNING')


        # Lux / weather
    def out_lux_state(self, entity, attribute, old, new, kwargs):
        if self.outLux1 != float(new) :
            self.outLux1 = float(new)

            self.newOutLux()

    def out_lux_event_MQTT(self, event_name, data, kwargs):
        lux_data = json.loads(data['payload'])
        if 'illuminance_lux' in lux_data :
            if self.outLux1 != float(lux_data['illuminance_lux']) :
                self.outLux1 = float(lux_data['illuminance_lux']) # Zigbee sensor
                self.newOutLux()
        elif 'value' in lux_data :
            if self.outLux1 != float(lux_data['value']) :
                self.outLux1 = float(lux_data['value']) # Zwave sensor
                self.newOutLux()

    def newOutLux(self):
        global OUT_LUX
        if self.datetime(aware=True) - self.lux_last_update2 > datetime.timedelta(minutes = 15) or self.outLux1 >= self.outLux2 :
            OUT_LUX = self.outLux1

        self.lux_last_update1 = self.datetime(aware=True)

    def out_lux_state2(self, entity, attribute, old, new, kwargs):
        if self.outLux2 != float(new) :
            self.outLux2 = float(new)

            self.newOutLux2()

    def out_lux_event_MQTT2(self, event_name, data, kwargs):
        lux_data = json.loads(data['payload'])
        if 'illuminance_lux' in lux_data :
            if self.outLux2 != float(lux_data['illuminance_lux']) :
                self.outLux2 = float(lux_data['illuminance_lux']) # Zigbee sensor
                self.newOutLux2()
        elif 'value' in lux_data :
            if self.outLux2 != float(lux_data['value']) :
                self.outLux2 = float(lux_data['value']) # Zwave sensor
                self.newOutLux2()

    def newOutLux2(self):
        global OUT_LUX
        if self.datetime(aware=True) - self.lux_last_update1 > datetime.timedelta(minutes = 15) or self.outLux2 >= self.outLux1 :
            OUT_LUX = self.outLux2

        self.lux_last_update2 = self.datetime(aware=True)


class Aircondition():

    def __init__(self, api,
            ac = None,
            indoor_temp = None,
            target_indoor_temp = 23,
            temperatures = [],
            windowsensors = [],
            anemometer_speed = 40,
            daytime_savings = {},
            daytime_increasing = {},
            away_state = None,
            screens = {},
            screening_temp = 8,
            hvac_fan_only_above = 24,
            hvac_notify_above = 28,
            hvac_cooling_above = 28,
            hvac_cooling_temp = 22,
            notify_reciever = None,
            notify_title = 'Window',
            notify_message_cold = 'window opened and temperature is ',
            notify_message_warm = 'Its hot inside. Temperature is '):

        self.ADapi = api
        self.notify_app = Notify_Mobiles(api)

        self.ac = ac
        self.indoor_temp = indoor_temp
        self.target_indoor_temp = target_indoor_temp

        self.window_last_opened = self.ADapi.datetime(aware=True) - datetime.timedelta(hours = 2)
        self.windowsensors = windowsensors
        for window in self.windowsensors :
            self.ADapi.listen_state(self.windowopen, window, new = 'on', duration = 120 )
            self.ADapi.listen_state(self.windowclose, window, new = 'off' )
            if self.window_last_opened < self.ADapi.convert_utc(self.ADapi.get_state(window, attribute = 'last_changed')):
                self.window_last_opened = self.ADapi.convert_utc(self.ADapi.get_state(window, attribute = 'last_changed'))
        if self.windowsopened() == 0 :
            self.windows_is_open = False
        else :
            self.windows_is_open = True

        self.anemometer_speed = anemometer_speed
        self.daytime_savings = daytime_savings
        self.daytime_increasing = daytime_increasing
        self.away_state = away_state

            # Setup Screening
        self.screening = []
        for s in screens :
            screen = Screen(self.ADapi,
                screen = s.get('screen', None),
                windowsensors = s.get('windowsensors', []),
                lux_close = s.get('lux_close', 40000),
                lux_open = s.get('lux_open', 15000),
                lux_open_tv = s.get('lux_open_media', 4000),
                anemometer_speed = self.anemometer_speed,
                not_when_home = s.get('not_when_home', []),
                mediaplayers = s.get('mediaplayers', []))
            self.screening.append(screen)
        self.screening_temp = screening_temp

        self.hvac_fan_only_above = hvac_fan_only_above
        self.hvac_notify_above = hvac_notify_above
        self.hvac_cooling_above = hvac_cooling_above
        self.hvac_cooling_temp = hvac_cooling_temp

        self.notify_reciever = notify_reciever
        self.notify_title = notify_title
        self.notify_message_cold = notify_message_cold
        self.notify_message_warm = notify_message_warm

        self.notify_on_window_open = True
        self.notify_on_window_closed = True

        self.temperatures = temperatures

        runtime = datetime.datetime.now()
        addseconds = (round((runtime.minute*60 + runtime.second)/720)+1)*720
        runtime = runtime.replace(minute=0, second=10, microsecond=0) + datetime.timedelta(seconds=addseconds)
        self.ADapi.run_every(self.set_indoortemp, runtime, 720)
        self.ADapi.run_in(self.set_indoortemp, 20)


    def find_target_temperatures(self):
        global OUT_TEMP
        target_num = 0
        for target_num, target_temp in enumerate(self.temperatures) :
            if float(target_temp['out']) >= OUT_TEMP :
                if target_num != 0:
                    target_num -= 1
                return target_num
        return target_num

    def set_indoortemp(self, kwargs):
        global OUT_TEMP

        global RAIN_AMOUNT
        global WIND_AMOUNT
        in_temp:float = 0.0
        current_temp:float = 0.0
        ac_state = 'heat'

        try :
            in_temp = float(self.ADapi.get_state(self.indoor_temp))
        except (ValueError, TypeError) :
            in_temp = self.target_indoor_temp - 0.2
        except Exception as e:
            in_temp = self.target_indoor_temp
            self.ADapi.log(f"Not able to set new inside temperature from {self.indoor_temp}. {e}", level = 'WARNING')
            
        try :
            current_temp = float(self.ADapi.get_state(self.ac, attribute='temperature'))
        except (ValueError, TypeError) :
            current_temp = in_temp
            ac_state = self.ADapi.get_state(self.ac)
        except Exception as e:
            current_temp = in_temp
            self.ADapi.log(f"Not able to set new inside temperature from {self.ac}. {e}", level = 'WARNING')
        else :
            ac_state = self.ADapi.get_state(self.ac)

        self.tryScreenOpen()

        if ac_state == 'heat' :
            """ Setting temperature hvac_mode == heat
            """
            target_num = self.find_target_temperatures()
            target_temp = self.temperatures[target_num]
            # Target temperature
            new_temperature = target_temp['normal']

            # Windows
            if self.windows_is_open :
                try :
                    self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'fan_only')
                except Exception as e:
                    self.ADapi.log(f"Not able to set hvac_mode to fan_only for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
                if self.notify_reciever :
                    self.notify_on_window_closed = True
                    if self.notify_on_window_open and in_temp < target_temp['normal'] :
                        self.notify_app.send_notification(f"{self.notify_message_cold}{in_temp}" ,self.notify_title, self.notify_reciever )
                        self.notify_on_window_open = False
                return
            elif self.windowsensors :
                if self.notify_reciever :
                    self.notify_on_window_open = True
                    if self.notify_on_window_closed and in_temp > self.hvac_notify_above :
                        self.notify_app.send_notification(f"{self.notify_message_warm}{in_temp}" ,self.notify_title, self.notify_reciever )
                        self.notify_on_window_closed = False

            # Holliday temperature
            if self.ADapi.get_state(self.away_state) == 'on' :
                new_temperature = target_temp['away']
                if in_temp > self.target_indoor_temp :
                    if OUT_TEMP > self.screening_temp :
                        for s in self.screening :
                            s.try_screen_close()
                    if in_temp > self.hvac_fan_only_above :
                        try :
                            self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'fan_only')
                        except Exception as e:
                            self.ADapi.log(f"Not able to set hvac_mode to fan_only for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
                        return
            else :
                # When it's windy outside the measured indoor temp is redused by half a degree to push heaters to maintain a higher temperature inside
                if WIND_AMOUNT > self.anemometer_speed :
                    in_temp -= 0.5
                    self.ADapi.log(f"{self.ADapi.get_state(self.ac, attribute = 'friendly_name')} New indoor temp set to {in_temp} because wind is {WIND_AMOUNT}") ###

                # Check if windows has been closed for over an hour and it is colder than target
                if self.ADapi.datetime(aware=True) - self.window_last_opened > datetime.timedelta(hours = 1) :
                    if in_temp < self.target_indoor_temp -0.5 :
                        if current_temp == target_temp['normal'] :
                            new_temperature += 1
                        else :
                            new_temperature = current_temp

                            if current_temp == target_temp['normal'] +1 and  in_temp < self.target_indoor_temp -0.6 :
                                new_temperature += 1
                                # Logging to inform that the set target is not able to maintain the wanted indoor temperature. 
                                self.ADapi.log(f"{self.ADapi.get_state(self.ac, attribute = 'friendly_name')} Increased temp by 2 from normal: {target_temp['normal']}. Indoor temp is {round(in_temp - self.target_indoor_temp,1)} below target. Outdoor temperature is {OUT_TEMP}", level = 'INFO')

        
                # Daytime Savings
                doDaytimeSaving = False
                for daytime in self.daytime_savings :
                    if 'start' in daytime and 'stop' in daytime :
                        if self.ADapi.now_is_between(daytime['start'], daytime['stop']) :
                            doDaytimeSaving = True
                            if 'presence' in daytime :
                                for presence in daytime['presence'] :
                                    if self.ADapi.get_state(presence) == 'home' :
                                        doDaytimeSaving = False

                    elif 'presence' in daytime :
                        doDaytimeSaving = True
                        for presence in daytime['presence'] :
                            if self.ADapi.get_state(presence) == 'home' :
                                doDaytimeSaving = False
                if doDaytimeSaving and new_temperature >= target_temp['normal'] :
                    new_temperature -= 1

                # Daytime Increasing temperature
                doDaytimeIncreasing = False
                for daytime in self.daytime_increasing :
                    if 'start' in daytime and 'stop' in daytime :
                        if self.ADapi.now_is_between(daytime['start'], daytime['stop']) :
                            doDaytimeIncreasing = True
                            if 'presence' in daytime :
                                for presence in daytime['presence'] :
                                    if self.ADapi.get_state(presence) != 'home' :
                                        doDaytimeIncreasing = False

                if doDaytimeIncreasing :
                    new_temperature = target_temp['normal'] + 1
                    in_temp -= 1

                # Check if it is hot inside
                if in_temp > self.target_indoor_temp :
                    if new_temperature >= target_temp['normal'] -1 :
                        if current_temp == target_temp['normal'] :
                            new_temperature -= 1
                        else :
                            new_temperature = current_temp
                            if current_temp == target_temp['normal'] -1 and in_temp > self.target_indoor_temp + 0.3 :
                                new_temperature -= 1
                                if OUT_TEMP < 15 : # Or fireplace temp?
                                    # Logging to inform that the indoor temperature is above set target.
                                    self.ADapi.log(f"{self.ADapi.get_state(self.ac, attribute = 'friendly_name')} Redusing temp by -2 from normal: {target_temp['normal']}. Indoor temp is {round(in_temp - self.target_indoor_temp,1)} above target. Outdoor temperature is {OUT_TEMP}", level = 'INFO')
                    if OUT_TEMP > self.screening_temp :
                        for s in self.screening :
                            s.try_screen_close()
                    if in_temp > self.hvac_fan_only_above :
                        if in_temp > self.hvac_cooling_above :
                            try :
                                self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'cool')
                                self.ADapi.call_service('climate/set_temperature', entity_id = self.ac, temperature = self.hvac_cooling_temp)
                            except Exception as e:
                                self.ADapi.log(f"Not able to set hvac_mode to cool for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
                            return
                        try :
                            self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'fan_only')
                        except Exception as e:
                            self.ADapi.log(f"Not able to set hvac_mode to fan_only for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
                        return

            # Update with new temperature
            if current_temp != new_temperature :
                self.ADapi.call_service('climate/set_temperature', entity_id = self.ac, temperature = new_temperature)

        elif ac_state == 'cool' :
            """ Setting temperature hvac_mode == cool
            """
            for s in self.screening :
                s.try_screen_close()

            if self.windows_is_open :
                try :
                    self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'fan_only')
                except Exception as e:
                    self.ADapi.log(f"Not able to set hvac_mode to fan_only for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
                return

            if in_temp < float(self.hvac_cooling_above) :
                try :
                    self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'fan_only')
                except Exception as e:
                    self.ADapi.log(f"Not able to set hvac_mode to fan_only for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
            elif current_temp != self.hvac_cooling_temp :
                self.ADapi.call_service('climate/set_temperature', entity_id = self.ac, temperature = self.hvac_cooling_temp)

        elif ac_state == 'fan_only' :
            """ Setting temperature hvac_mode == fan_only
            """
            if not self.windows_is_open and self.ADapi.get_state(self.away_state) == 'off':
                if in_temp > float(self.hvac_cooling_above) :
                    try :
                        self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'cool')
                        self.ADapi.call_service('climate/set_temperature', entity_id = self.ac, temperature = self.hvac_cooling_temp)
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to cool for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
                elif in_temp < self.hvac_fan_only_above :
                    try :
                        self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'heat')
                        self.ADapi.run_in(self.set_indoortemp, 1)
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to heat for {self.ac}. Probably not supported. {e}", level = 'DEBUG')
            elif not self.windows_is_open :
                if in_temp < self.hvac_fan_only_above :
                    try :
                        self.ADapi.call_service('climate/set_hvac_mode', entity_id = self.ac, hvac_mode = 'heat')
                        self.ADapi.run_in(self.set_indoortemp, 1)
                    except Exception as e:
                        self.ADapi.log(f"Not able to set hvac_mode to heat for {self.ac}. Probably not supported. {e}", level = 'DEBUG')

            elif self.windows_is_open and self.notify_on_window_open and in_temp < 20 :
                if self.notify_reciever :
                    self.notify_app.send_notification(f"{self.notify_message_cold}{in_temp}" ,self.notify_title, self.notify_reciever )
                    self.notify_on_window_open = False

            if in_temp > float(self.target_indoor_temp) :
                if OUT_TEMP > float(self.screening_temp) :
                    for s in self.screening :
                        s.try_screen_close()

        elif ac_state != 'off' and ac_state != 'unavailable':
            # If hvac_state is not heat/cool/fan_only/off or unavailable. Log state for notice. Write automation if missing functionality.
            self.ADapi.log(f"Unregisterd AC state: {ac_state}. {self.ADapi.get_state(self.ac, attribute='friendly_name')}. Will not automate", level = 'WARNING')

    def windowopen(self, entity, attribute, old, new, kwargs):
        if self.windowsopened() != 0 :
            self.windows_is_open = True
            self.ADapi.run_in(self.set_indoortemp, 0)

    def windowclose(self, entity, attribute, old, new, kwargs):
        if self.windowsopened() == 0 :
            self.window_last_opened = self.ADapi.datetime(aware=True)
            self.windows_is_open = False
            self.ADapi.run_in(self.set_indoortemp, 60)

    def windowsopened(self):
        opened = 0
        for window in self.windowsensors :
            if self.ADapi.get_state(window) == 'on' :
                opened += 1
        return opened

    def tryScreenOpen(self):
        for s in self.screening :
            s.try_screen_open()


class Screen():

    def __init__(self, api,
        screen = None,
        windowsensors = [],
        lux_close = 40000,
        lux_open = 15000,
        lux_open_tv = 4000,
        anemometer_speed = 40,
        not_when_home = [],
        mediaplayers = []):

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

        for mediaplayer in self.mediaplayers :
            self.ADapi.listen_state(self.media_on, mediaplayer, new = 'on', old = 'off')
            self.ADapi.listen_state(self.media_off, mediaplayer, new = 'off', old = 'on')

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

        if lux_close == 0 :
            lux_close = self.lux_close
        if RAIN_AMOUNT == 0 and WIND_AMOUNT < self.anemometer_speed and self.windowsopened() == 0 and OUT_LUX >= lux_close :
            for person in self.tracker :
                if self.ADapi.get_state(person) == 'home' :
                    return
            if self.ADapi.get_state(self.screen, attribute='current_position') == self.position and self.ADapi.get_state(self.screen, attribute='current_position') == 100 :
                self.ADapi.call_service('cover/close_cover', entity_id= self.screen)
                self.position = 0

    def try_screen_open(self):
        global RAIN_AMOUNT
        global WIND_AMOUNT
        global OUT_LUX
        if self.windowsopened() == 0 and self.position != 100:
            openme = False
            if RAIN_AMOUNT > 0 or WIND_AMOUNT > self.anemometer_speed or OUT_LUX < self.lux_open :
                openme = True
            if self.ADapi.get_state(self.screen, attribute='current_position') == self.position and OUT_LUX <= 100 :
                openme = True
            if openme :
                self.ADapi.call_service('cover/open_cover', entity_id= self.screen)
                self.position = 100

    def media_on(self, entity, attribute, old, new, kwargs):
        self.lux_open = self.lux_open_tv

    def media_off(self, entity, attribute, old, new, kwargs):
        if self.check_mediaplayers_off() :
            self.lux_open = self.lux_open_normal

    def check_mediaplayers_off(self):
        for mediaplayer in self.mediaplayers :
            if self.ADapi.get_state(mediaplayer) == 'on' :
                return False
        return True


class Notify_Mobiles:

    def __init__(self, api):
        self.ADapi = api

    def send_notification(self, message = 'Message', message_title = 'title', message_recipient = ['all'] ):
        if message_recipient == [{'reciever': 'all'}]:
            self.ADapi.notify(f"{message}", title = f"{message_title}")
        else:
            for reciever in message_recipient:
                self.ADapi.notify(f"{message}", title = f"{message_title}", name = f"{reciever}")
