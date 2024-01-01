# Climate Commander by Pythm
an Appdaemon app for temperature control off heat-pump / airconditions connected as 'climate' in [Home Assistant](https://www.home-assistant.io/). Set a target with an external indoor temperature sensor and configure your screens and other sensors to maintain a balanced indoor climate.

## Installation
Download the `ClimateCommander` directory from inside the `apps` directory to your [Appdaemon](https://appdaemon.readthedocs.io/en/latest/) `apps` directory, then add configuration to a .yaml or .toml file to enable the `climateCommander` module. Minimum required in your configuration is:
```yaml
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  airconditions:
    - climate: climate.yourClimate
      indoor_temp: sensor.yourIndoorTemperatureSensor # External indoor temperature sensor
      temperatures:   # List of outdoor temperatures with dictionary normal and away temperatures
        - out: 5      # Measured outdoor temperature
          normal: 23  # Normal temperature for app to adjust from. 
          away: 16    # Temperature to set when on holliday
```

## App usage and configuration
> [!NOTE]
> Readme is under construction. Come back tomorrow
