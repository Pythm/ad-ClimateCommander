## Breaking Change:
A rewrite as of version 1.1.0 to better handle both HVAC and heaters with other sources than electricity.
- Changed "Command" name to either a "HVAC" or a "Heater". Now you can control Heaters without HVAC functionality or define HVAC enabled devices.
- Indoor sensor temp changed from "indoor_temp" to "indoor_sensor_temp"
- Away state is now configured with "vacation"
- Indoor sensor temp has been changed from "indoor_temp" to "indoor_sensor_temp"


# Climate Commander by Pythm
An Appdaemon app for controlling `climate` entities in [Home Assistant](https://www.home-assistant.io/). Set an indoor temperature target with an external indoor temperature sensor and configure your screens and provide other sensors to maintain a balanced indoor climate.

This is developed in Norway where we mostly need heating. The app automatically changes HVAC entities to `fan_only` if indoor temperature is 0.6 degree above target or if windows is open. It also changes from `fan_only` to `cool` if indoor temperature is 1 degree above target and outdoor temperature is above indoor target. The app also automatically closes screens/covers when it is above target indoor temperature and above given lux constraints.

![Picture is generated with AI](_5b05cb75-1f9c-4fed-9aa6-0e4f9d73c8ac.jpg)

## Installation
1. Download the `ClimateCommander` directory from inside the `apps` directory here to your [Appdaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory.
2. Add the configuration to a .yaml or .toml file to enable the `ClimateCommander` module. Minimum required in your configuration with example input is:

```yaml
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  HVAC:
    - climate: climate.yourClimate
      indoor_sensor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      target_indoor_temp: 22.7
```

> [!TIP]
> All numbers in the yaml example configurations are default if not defined in the configuration.


## App usage and configuration
This app is designed to control climate entities in Home Assistant based on indoor temperature with additional sensors. Outdoor sensors are configured for the app, while indoor sensors are configured per climate entity.

> [!IMPORTANT]
> You need an external indoor temperature sensor. Placement of the sensor and setting the right target temperature is crucial for optimal indoor temperature.

> [!NOTE]
> This app does not consider electricity prices or usage. Another app controlling heaters, hot water boilers, and chargers for cars based on electricity price and usage can be found here: https://github.com/Pythm/ad-ElectricalManagement

> [!IMPORTANT]
> If you have defined a namespace for HASS, you need to configure the app with `HASS_namespace`. If you are using MQTT you need to define your MQTT namespace with `MQTT_namespace`. Both defaults to default:


### Outdoor weather sensors climate reacts to
If you do not have an outdoor temperature sensor, the app will try to get the temperature from the [Met.no](https://www.home-assistant.io/integrations/met) integration.

You can use an anemometer to increase the indoor set temperature when it is windy. Define your sensor with `anemometer` and your "windy" target with `anemometer_speed`. Anemometer is a Home Assistant sensor. It will also open all screens defined if wind speed is above the target. Additionally, it will activate the `boost` preset mode if needed, if your HVAC supports it.

> [!TIP]
> Boost will not be set if the fan mode is set to Silence.

You can also define a `rain_sensor` and a `rain_level` to increase indoor temperature when it is depressing weather outside. The rain sensor is a Home Assistant sensor. Any rain detected will open all screens defined.

Both wind and rain, separately or combined, will set the indoor temperature to 0.3 degrees above target.

Outdoor Lux sensors are needed if you also want to control [cover](https://www.home-assistant.io/integrations/cover/) entities, such as screens or blinds for your windows. You can configure two outdoor lux sensors, with the second ending with `'_2'`, and it will keep the highest lux value or the last if the other is not updated within the past 15 minutes. Both Lux sensors can be either MQTT or Home Assistant sensors.

```yaml
  outside_temperature: sensor.netatmo_out_temperature
  anemometer: sensor.netatmo_anemometer_wind_strength
  anemometer_speed: 40
  rain_sensor: sensor.netatmo_rain
  rain_level: 3
  OutLux_sensor: sensor.lux_sensor
  OutLuxMQTT_2: zigbee2mqtt/OutdoorHueLux

  screening_temp: 8
  getting_cold: 18
```
`screening_temp` configure a minimum outdoor temperature for when screens will automatically close.
The default temperature threshold when the app registers it as cold outside is 18 degrees Celsius. This configuration is for now, mainly used for notifications, and can be changed with `getting_cold`.


### Windowsensors
You can add window/door sensors to switch your HVAC to `fan_only` if any is opened for more than 2 minutes. If you configure `Heater` in stead if `HVAC`, the heater will set the temperature to the vacation temperature.

The app supports an additional indoor temperature sensor to register when the sun is heating and turn down the heater before it gets hot. A windowsensor with temperature reading is a optimal placement.

```yaml
      windowsensors:
        - binary_sensor.your_window_door_is_open
```

## Configurations for the app

### Temperature settings for climate
Define an external indoor temperature sensor with `indoor_sensor_temp`, and set `target_indoor_temp` for the external indoor temperature. Alternatively to the target_indoor_temp, you can use a Home Assistant input_number helper and set the target from that with `target_indoor_input`.

Add a window sensor with `window_sensor_temp` and input the offset between the indoor sensor and the window sensor when the sun is not heating with `window_offset`.

```yaml
      indoor_sensor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      target_indoor_temp: 22.7
      target_indoor_input: input_number.yourInput
      window_sensor_temp: sensor.your_windowsensor_air_temperature
      window_offset: -3
```

 Daytime savings and increasing times will set the target +- 0.5 degree to increase or decrease the indoor temperature. The `daytime_savings` and `daytime_increasing` have a start and stop time. In addition, you can define presence detection. If anyone is home, it will not do daytime savings, but there needs to be someone home to increase the temperature.
```yaml
      daytime_savings:
        - start: '10:00:00'
          stop: '14:00:00'
          presence:
            - person.wife
            - person.myself
      daytime_increasing:
        - start: '05:00:00'
          stop: '07:00:00'
```

### Silent Periods
Configure times to set the fan speed to `silence`. This only applies to HVAC heaters.

```yaml
      silence:
        - start: '21:00:00'
          stop: '07:00:00'
          presence: 
            - person.nathaniel
```
> **Note:** The app will revert to its previous setting when the silent period ends. It stores the other fan mode in persistent storage.


### Setting up sensors for screens/covers
Each screen has a lux closing and lux opening value for automatically closing or opening your cover entity. If you have `windowsensors` defined, the sensor must be off(closed) for the screen to run. Add `mediaplayers` sensors and a `lux_open_media` if you want the screen to open with a different lux value than normal when your media is on. Mediaplayers can be any Home Assistant entity that returns 'on'/'off' value.
You can prevent covers from closing when a person/tracker is at home using a list with `not_when_home`.

> [!TIP]
> If you adjust your screen manually, the app will not open the cover until the outdoor lux level is below 100. Rain/wind will always open covers.

```yaml
      screening:
        - screen: cover.your_screen
          windowsensors:
          - binary_sensor.window_door_is_open
          lux_close: 40000
          lux_open: 15000
          lux_open_media: 4000
          not_when_home:
            - person.wife
          mediaplayers:
            - switch.projector
            - media_player.your_tv
```

### Vacation temperature
You can define an Home Assistant input_boolean helper to lower the consumption when on vacation. When heating the target indoor temp will be set to a temperature defined with `vacation_temp`. The vacation temperature can either be defined at main level or under each climate entity.
When cooling the temperature will be set 3 degrees above

```yaml
  vacation: input_boolean.vacation
  vacation_temp: 16
  HVAC:
    - climate: climate.yourClimate
      vacation_temp: 16
 ```

### Persistent storage
To configure persistent storage, input a path, exclusive of a name and a .json filename (e.g., '/conf/persistent/Climate/') to store a JSON file using the `json_path` as persistent data.

The app will calculate the average set temperature based on outdoor temperature and lux for further improvements to temperature setting.
It is also used to store fan speed, in case the app is restarted during silent periode.

### Disable all automations
Define an HA input boolean and configure with `automate` to disable automation when switch is off.


### Set up notifications
You can get notifications for when the indoor temperature is low and a window is open, or if it is hot and windows are closed. It sends notifications with [Notify integration](https://www.home-assistant.io/integrations/notify/).

```yaml
      notify_reciever:
        - mobile_app_your_phone
```

## Get started
> [!TIP]
> Define an HA input boolean and configure with `automate` to disable automation. Turn off to stop automating temperature.

The easiest way to get started is to copy the example provided and update it with your sensors and climate entities. You can then add more configurations as needed. Ensure that all list and dictionary elements are correctly indented. Here's an example:

## Example App configuration

```yaml
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  outside_temperature: sensor.netatmo_out_temperature
  anemometer: sensor.netatmo_anemometer_wind_strength
  anemometer_speed: 40
  rain_sensor: sensor.netatmo_rain
  rain_level: 3
  OutLux_sensor: sensor.lux_sensor
  OutLuxMQTT_2: zigbee2mqtt/OutdoorHueLux

  screening_temp: 8
  getting_cold: 18

  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  vacation_temp: 16

  HVAC:
    - climate: climate.yourClimate
      indoor_sensor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      target_indoor_input: input_number.yourInput
      window_sensor_temp: sensor.your_windowsensor_air_temperature
      window_offset: -3

      daytime_savings:
        - start: '10:00:00'
          stop: '14:00:00'
          presence:
            - person.wife
            - person.myself
      daytime_increasing:
        - start: '05:00:00'
          stop: '07:00:00'

      silence:
        - start: '21:00:00'
          stop: '07:00:00'
          presence: 
            - person.nathaniel

      windowsensors:
        - binary_sensor.your_window_door_is_open

      screening:
        - screen: cover.your_screen
          windowsensors:
          - binary_sensor.window_door_is_open
          lux_close: 40000
          lux_open: 15000
          lux_open_media: 4000
          not_when_home:
            - person.wife
          mediaplayers:
            - switch.projector
            - media_player.your_tv

      notify_reciever:
        - mobile_app_your_phone
```


### Key definitions for defining app
key | optional | type | default | introduced in | description
-- | -- | -- | -- | -- | --
`module` | False | string | | v1.0.0 | The module name of the app.
`class` | False | string | | v1.0.0 | The name of the Class.
`HASS_namespace` | True | string | default | v1.0.0 | HASS namespace
`MQTT_namespace` | True | string | default | v1.0.0 | MQTT namespace

`outside_temperature` | True | sensor | | v1.0.0 | Sensor for outside temperature
`anemometer` | True | sensor | | v1.0.0 | Sensor for wind speed
`anemometer_speed` | True | int | 40 | v1.0.0 | windy target
`rain_sensor` | True | sensor | | v1.0.0 | Sensor for rain detection
`rain_level` | True | int | 3 | v1.2.0 | rainy target
`OutLux_sensor` | True | sensor | | v1.0.0 | Sensor for Lux detection
`OutLuxMQTT` | True | MQTT sensor | | v1.0.0 | Lux detection via MQTT
`OutLux_sensor_2` | True | sensor | | v1.0.0 | Secondary Sensor for Lux detection
`OutLuxMQTT_2` | True | MQTT sensor | | v1.0.0 | Secondary Lux detection via MQTT
`screening_temp` | True | int | 8 | v1.0.0 | Outside temperature needs to be over this to automatically close screen
`getting_cold` | True | int | 18 | v1.0.6 | Cold outside for notifications below outside temperature
`json_path` | True | string | None | v1.1.0 | Persisten storage
`vacation` | True | input_boolean | input_boolean.vacation | v1.1.0 | Activates Vacation temperature
`vacation_temp` | True | int | 16 | v1.1.0 | Indoor vacation temperature

### Key definitions for defining climates
key | optional | type | default | introduced in | description
-- | -- | -- | -- | -- | --
`HVAC` | False | list | | v1.1.0 | Contains HVAC climates
`Heaters` | False | list | | v1.1.0 | Contains Heater climates
`climate` | False | climate entity | | v1.0.0 | The entity_id of the climate
`indoor_sensor_temp` | False | sensor | | v1.0.0 | External indoor temperature sensor
`target_indoor_temp` | True | int | 23 | v1.0.0 | Indoor target temperature and Screening/cover auto close
`target_indoor_input` | True | input_number | | v1.0.3 | Set indoor target temperature with a HA sensor
`window_sensor_temp` | True | sensor | | v1.1.0 | Window temperature sensor
`window_offset` | True | int | -3 | v1.1.0 | offset from indoor temperature sensor 
`daytime_savings` | True | dictionary | | v1.0.0 | Contains start / stop and optionally presence to lower temperature
`daytime_increasing` | True | dictionary | | v1.0.0 | Contains start / stop and optionally presence to increase temperature
`silence` | True | dictionary | | v1.0.4 | Contains start / stop and optionally presence to set fan to silence
`windowsensors` | True | list | | v1.0.0 | Will set fan_only when window is opened for more than 2 minutes
`automate` | True | input_boolean | True | v1.0.3 | Turn off a input boolean to stop automating temperature
`notify_reciever` | True | list | | v1.0.0 | Notify recipients


### Key definitions for defining screens 
key | optional | type | default | introduced in | description
-- | -- | -- | -- | -- | --

`screening` | True | dictionary | | v1.0.0 | Contains a list of cover entities to control
`windowsensors` | True | list | | v1.0.0 | If screen is on a window/door that can be opened it will not autoclose when sensor is 'on'
`lux_close` | True | int | 40000 | v1.0.0 | Close cover if temperatures is above target and lux is above
`lux_open` | True | int | 15000 | v1.0.0 | Open cover when lux goes below
`lux_open_media` | True | int | 4000 | v1.0.0 | Optional lux open setting if one of the mediaplayers is on
`not_when_home` | True | list | | v1.0.0 | Only close cover automatically if persons are not at home
`mediaplayers` | True | list | | v1.0.0 | list of switches/media to use alternative lux open value