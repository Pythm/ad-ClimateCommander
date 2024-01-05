# Climate Commander by Pythm
an Appdaemon app for controlling `climate` entities in [Home Assistant](https://www.home-assistant.io/). Set a indoor temperature target with an external indoor temperature sensor and configure your screens and other sensors to maintain a balanced indoor climate.

This is developed in Norway where we mostly need heating. App will only adjust temperature when heating, but there is some functionality to automatically set to `fan_only` or `cool` in addition to automatically close screens when it is hot and sunny.

> [!NOTE]
> Readme is under construction. Check back later for more info in setting up the app.

## Installation
Download the `ClimateCommander` directory from inside the `apps` directory to your [Appdaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory, then add configuration to a .yaml or .toml file to enable the `climateCommander` module. Minimum required in your configuration is:
```yaml
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  Command:
    - climate: climate.yourClimate
      indoor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      temperatures:   # List of outdoor temperatures with dictionary normal and away temperatures
        - out: 5      # Measured outdoor temperature
          normal: 23  # Normal temperature for app to adjust from. 
          away: 16    # Temperature to set when on holliday
```

## App usage and configuration
This app is ment to control climate entities in Home Assitant based on outdoor temperature with additinal sensors. Outdoor sensors is configured for app and indoor sensors is configured pr climate entity.

> [!NOTE]
> This app is not considering electricity prices or usage. Another app controlling heaters, hotwater boilers and chargers for cars based on electricity price and usage is undergoing testing before beeing released. Configuration for climate entities is more or less the same but if you want a pre-release of the app that takes price and usage into acount, let me know here or at [Patreon](https://www.patreon.com/Pythm)

> [!IMPORTANT]
> You need an external indoor temperature sensor. Placement of the sensor and finding the right target temperature is crucial for optimal indoor temperature.

Climates will adjust +/- 2 degrees from temperature defined in normal. App will by default log info about outdoor/indoor temperature if it is needed to adjust as much as 2 degrees to maintain the set target. The idea behind the app was to define what temperature to set given the outdoor temperature. The colder outside the higher temperature is needed to maintain a warm house. Defining a proper temperature scale will improve daytime savings and increasing.


### Outdoor weather sensors climate reacts to
If you do not have an outdoor temperature sensor the app will try to get temperature from [Met.no](https://www.home-assistant.io/integrations/met) integration.

If you have a drafty house you can use an anemometer to increase the indoor set temperature when it is windy. Define your sensor with `anemometer` and your target with `anemometer_speed`. Anemometer is a Home Assistant sensor.

Outdoor `Lux` and `Rain` sensors are only needed if you also want to control [cover](https://www.home-assistant.io/integrations/cover/) entities like screens or blinds for your windows. You can configure two outdoor lux sensors with the second ending with '_2' and it will keep the highest lux or last if other is not updated last 15 minutes. Both Lux sensors can be either MQTT or Home Assistant sensor. Rain sensor is a Home Assistant sensor.

```yaml
  outside_temperature: sensor.netatmo_out_temperature
  anemometer: sensor.netatmo_anemometer_wind_strength
  anemometer_speed: 20 # Increases temperature inside when anemometer exceeds this amount
  rain_sensor: sensor.netatmo_rain
  OutLux_sensor: sensor.lux_sensor
  OutLuxMQTT_2: zigbee2mqtt/OutdoorHueLux
```

### Configurations for app
You can define an Home Assistant input_boolean helper to lower the temperature when on vacation to temperature defined as `away`
```yaml
 away_state: input_boolean.vacation
 ```

> [!IMPORTANT]
> If you have defined a namespace for MQTT other than default you need to define your namespace with `MQTT_namespace`. Same for HASS you need to define your namespace with `HASS_namespace`.

### Temperature settings for climate

```yaml
      indoor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      target_indoor_temp: 22.7 # Target for external indoor temperature and Screening/cover will auto open above temperature
```

As mentioned earlier defining a proper temperature scale will improve the climate automation. You define the climate working temperature based on outdoor temperature. The array will be built like this, with a `normal` operations temperature and an `away` temperature based on `out` temperature. Here the climate will heat with 24 degrees up until 1 degree.
```yaml
      temperatures:
        - out: -10
          normal: 24
          away: 17
        - out: 1
          normal: 23
          away: 16
```

> [!TIP]
> Start from your current temperature and extend the array every time you need to lower or increase the indoor temperature by one degree.

Climates will adjust up until +/- 2 degrees from temperature defined in `normal` to maintain the target indoor temperature. The daytime savings and increasing times will alter the measured indoor temperature +/- 1 degree to increase or decrease the indoor temperature. The `daytime_savings` and `daytime_increasing` has a start and stop time. In addition you can define presence detection. If anyone is home it will not do daytime savings, but there needs to be someone home to increase temperature.

```yaml
      daytime_savings:
        - start: '10:00:00'
          stop: '14:00:00'
          presence: 
            - person.wife
            - person.myself
       # Increase temp by 1 degree at.
      daytime_increasing:
        - start: '05:00:00'
          stop: '07:00:00'
```

There are different temperatures to define behaviour. `hvac_fan_only_above` will change aircondition to `fan_only` when the external indoor temperature sensor is above value. `hvac_cooling_above` is also dependant on the indoor sensor and will activate `cool` on Aircondition and set temperature defined with `hvac_cooling_temp`. In addition you can configure a minimum outdoor temperature for when the screens will auto close with `screening_temp`.

If you have a heater that does not have HVAC capabilities `fan_only` and `cool` you can define `hvac_enabled` to False in climate.

### Windowsensors
You can add window/door sensors to turn your HVAC to `fan_only` if any is opened for more than 2 minutes. If you have `hvac_enabled` defined to False heater will set temperature to away temperature.

### Setting up sensors for screens
Each screen has a lux close and lux open value for automatically closing/opening your cover entity. If you have `windowsensors` defined every sensor must be closed for the screen to run. Add `mediaplayers` sensors and a `lux_open_media` if you want the screen to open with a different lux value than normal. Mediaplayers can be any Home Assistant entity that returns 'on'/'off' value.

You can prevent covers from closing when a person/tracker is home using a list with `not_when_home`.

> [!TIP]
> If you adjust your screen manually app will not open cover until outdoor lux is below 100

```yaml
      screening:
        - screen: cover.your_screen
          windowsensors: # If screen is on a window/door that can be opened it will not autoclose when sensor is 'on'
          - binary_sensor.window_door_is_open
          lux_close: 40000 # Close cover if temperatures is above target and lux is above
          lux_open: 7000 # Open cover again when lux goes below
          lux_open_media: 2000 # Optional lux setting if one of the mediaplayers is on
          not_when_home: # Only close cover automatically if persons are not at home
            - person.wife
          mediaplayers:
          - switch.projector
          - media_player.your_tv
```

### Set up notifications
You can get notifications for when inside temperatures is low and window is open, or if it is hot and windows are closed. It sends notifications thru [Notify integration](https://www.home-assistant.io/integrations/notify/). You can use 'all' or a list with recipients.

```yaml
      notify_reciever:
        - mobile_app_your_phone
      notify_title: 'ClimateCommander'
      notify_message_cold: 'It\'s getting cold inside and window is open. Temperature is'
      notify_message_warm: 'It\'s getting hot inside and temperature is'
      notify_above: 28
```

# Get started
Easisest to start off with is to copy this example and update with your sensors and climate entities and build from that. There is a lot of list/dictionaries that needs to be correctly indented.

## Putting it all together in an app
```yaml
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  outside_temperature: sensor.netatmo_out_temperature
  anemometer: sensor.netatmo_anemometer_wind_strength
  anemometer_speed: 20 # Increases temperature inside when anemometer exceeds this amount
  rain_sensor: sensor.netatmo_rain
  OutLux_sensor: sensor.lux_sensor
  OutLuxMQTT_2: zigbee2mqtt/OutdoorHueLux
  Command:
    - climate: climate.yourClimate
      indoor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      target_indoor_temp: 23 # Target for external indoor temperature and Screening/cover auto close above
      temperatures:   # List of outdoor temperatures with dictionary normal and away temperatures
        - out: -10   # Measured outdoor temperature
          normal: 24 # Normal temperature for app to adjust from. 
          away: 17   # Temperature to set when on holliday
        - out: 1
          normal: 23
          away: 16
        - out: 4
          normal: 22
          away: 16
        - out: 6
          normal: 21
          away: 16
        - out: 9
          normal: 20
          away: 15
        - out: 16
          normal: 19
          away: 15
        - out: 18
          normal: 16
          away: 15
      windowsensors: # Will set fan_only when window is opened for more than 120 seconds
        - binary_sensor.window_door_is_open

      # Power savings option. Reduce temp by 1 degree. Only if presence is away
      daytime_savings:
        - start: '10:00:00'
          stop: '14:00:00'
          presence: 
            - person.wife
            - person.myself
       # Increase temp by 1 degree at.
      daytime_increasing:
        - start: '05:00:00'
          stop: '07:00:00'

      # Other temperature configurations reacting to indoor temperature sensor
      hvac_fan_only_above: 24 # Fan Only above value
      hvac_cooling_above: 28 # Cooling above
      hvac_cooling_temp: 22 # AC temperature when cooling
      screening_temp: 8 # Outside temperature needs to be over this to automatically close screen
      # Cover your windows if indoor temperature and outdoor lux gets above target to stop the sun from heating even more
      screening:
        - screen: cover.your_screen
          windowsensors: # If screen is on a window/door that can be opened it will not autoclose when sensor is 'on'
          - binary_sensor.window_door_is_open
          lux_close: 40000 # Close cover if temperatures is above target and lux is above
          lux_open: 7000 # Open cover again when lux goes below
          lux_open_media: 2000 # Optional lux setting if one of the mediaplayers is on
          not_when_home: # Only close cover automatically if persons are not at home
            - person.wife
          mediaplayers:
          - switch.projector
          - media_player.your_tv

      # Notifications for when temperatures is low and window is open, or hot and windows are closed
      notify_reciever:
        - mobile_app_your_phone
      notify_title: 'Heatpump'
      notify_message_cold: 'It\'s getting cold inside and window is open. Temperature is'
      notify_message_warm: 'It\'s getting hot inside and temperature is'
      notify_above: 28 # Sends you a notification to open a window if indoor temperature exceeds

      # Add another AC/Heatpump
    - climate: climate.yourHeater
      indoor_temp: sensor.air_temperature
      target_indoor_temp: 23.6
      hvac_enabled: False
      temperatures:
        - out: -3
          normal: 24
          away: 17
        - out: -2
          normal: 23
          away: 16
        - out: 0
          normal: 22
          away: 15
        - out: 11
          normal: 21
          away: 14
        - out: 12
          normal: 20
          away: 14
        - out: 16
          normal: 18
          away: 14
        - out: 18
          normal: 16
          away: 14

```
