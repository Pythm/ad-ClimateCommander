# ad-ClimateCommander: notes for an AI assistant

Read this with [README.md](README.md). The workflow for finding the user's real entity ids (token, `ha_snapshot.py`, asking when several match) is in the [Pythm_AppdaemonApps LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md). Follow its rules: use ids from the index only, ask when several match, one app per `apps.yaml` edit, back up first, no commit or push unasked.

## What the app does

Controls one climate entity per app (heater or HVAC) and optional screening covers. It sets the temperature (and for HVAC the mode heat / cool / fan_only, the fan *Silence* mode and the *boost* preset). It acts on its own every 12 minutes (15 in table mode), so the user must say yes before it is added. It needs the [ad-Weather](https://github.com/Pythm/ad-Weather) app for outdoor values. ElectricalManagement can send it save / spend / reduce states.

## Config (apps.yaml), one app per climate entity

```yaml
climate_living_room:            # app name, used by ElectricalManagement's climate_commander: list
  module: climateCommander
  class: Climate
  climate: climate.x            # required
  type: hvac                    # heater (default) | hvac
  json_path: /conf/persistent/Climate/   # optional, learned setpoints (folder, end with /)
  indoor_sensor_temp: sensor.x  # required in indoor mode
  target_indoor_temp: 22.7      # or target_indoor_input: input_number.x
```

Table heater (floor heating, panel heater):
```yaml
heater_bathroom:
  module: climateCommander
  class: Climate
  climate: climate.x
  type: heater
  target_heater_temp: 28        # or target_heater_input; without both, the indoor target is used
  indoor_sensor_temp: sensor.x  # optional here: lowers the setpoint when the room is above target_indoor_temp
  target_indoor_temp: 24
  save_temp_offset: -0.5        # or save_temp: 12, or `save` in the table entries
  vacation_temp: 13
  temperatures:                 # sorted by `out` automatically
    - out: -6
      offset: 3
    - out: 7
      offset: 0
```

Keys: required `climate` (alias `heater`). Optional: `type`, `target_mode` (`indoor`|`table`, else chosen from the keys: a table, `target_heater_temp` or `target_heater_input` gives `table`), `backup_indoor_sensor_temp`, `target_indoor_input`, `target_heater_input`, `temperatures`, `save_temp`, `save_temp_offset` (indoor mode default -0.3), `spend_temp_offset` (default 1), `vacation`, `vacation_temp`, `vacation_keep_off` (or `options: [vacation_keep_off]`), `max_vacation_temp`, `window_sensor_temp` (alias `window_temp`), `window_offset` (-3), `windowsensors`, `daytime_savings`, `daytime_increasing`, `silence`, `automate`, `rain_level` (3), `anemometer_speed` (40), `screening` (list: `screen` required, `windowsensors`, `lux_close`, `lux_open`, `lux_open_media`, `not_when_home`, `mediaplayers`), `screening_temp`, `getting_cold` (18), `getting_hot_offset` (10), `notify_app`, `notify_receiver` (alias `recipient`), `weather_app` (default `weather`), `HASS_namespace`, `MQTT_namespace`, `namespace`, `unit_of_measurement`, own lux sensors `OutLux_sensor`, `OutLuxMQTT`, `OutLux_sensor_2`, `OutLuxMQTT_2`.

Deprecated, still working (do not write them in new configs): `HVAC:` / `Heaters:` lists in one app (WARNING), `outside_temperature`, `rain_sensor`, `anemometer`, `away_state`.

## Which entities fit each key

| Key | Entity |
|---|---|
| `climate` | `climate.*` that the user wants controlled. HVAC: has `hvac_modes` with `cool`/`fan_only`. |
| `indoor_sensor_temp`, `backup_indoor_sensor_temp`, `window_sensor_temp` | `sensor` with `device_class` `temperature` in that room; the backup is often the climate's own sensor. |
| `target_indoor_input`, `target_heater_input` | `input_number` |
| `windowsensors` | `binary_sensor` with `device_class` `window`/`door`/`opening` (or an `input_boolean`) |
| `vacation`, `automate` | `input_boolean` |
| `screening.screen` | `cover.*` with `current_position` |
| `not_when_home`, `presence` | `person.*` |
| `mediaplayers` | `media_player.*` or `switch.*` |

## ALWAYS ask the user

- Heater or HVAC, and which target method: a fixed indoor temperature, or a table by outdoor temperature (and the table values). Never invent table offsets.
- Two temperature sensors in one room: which is the main one and which the backup.
- Which windows belong to the room, and whether an open window should lower the heat at all.
- Vacation: which `input_boolean`, the vacation temperature, and whether the heater may be turned off (`vacation_keep_off`).
- Who should get the window notifications, if anyone.
- Controlled by ElectricalManagement? Then also ask for the heater's **rated power in watts** for the `power:` key in ElectricalManagement. Never take it from the consumption sensor's history or a guess: ElectricalManagement no longer learns it.

## Behaviour not to "fix"

- Indoor mode: small drops are rounded to -0.5 after 30 min, small rises wait 30 min (deliberate asymmetry); the extra -1 after 4 h turned down is deliberate; cooling setpoints are not learned.
- Table mode: open windows and `reduce` use the LOWEST of the save and vacation temperature (owner decision). An entry applies from its `out` value and up.
- Learned setpoints are only updated in normal operation (not on vacation, open window, daytime savings/increasing or ElectricalManagement states).
- Temperature uses the lowest and lux the highest value in the Weather app, by design.
- `input_boolean.vacation` is the default vacation switch only if it exists (shared convention).

## ElectricalManagement link

In ElectricalManagement's YAML, not here:
```yaml
  climate_commander:
    - app: heater_bathroom      # this app's name
      power: 2000               # rated watts, ask the user
      consumptionSensor: sensor.x_power
      kWhconsumptionSensor: sensor.x_energy
      pricedrop: 0.2
      max_continuous_hours: 4
```
States: `normal`, `save`, `spend`, `reduce`; see the README table. No `dependencies:` between the apps. Public API used by ElectricalManagement: `get_device(climate=None)` and on the device `set_power_state(state, valid_minutes, source)`, `power_state`, `in_daytime_saving()`, `heater`, `namespace`, `vacation_entity`, `daytime_savings`.

## Rules for the assistant

- One app per climate entity in new configs; when moving a device from an old list app, remove it from the list in the same edit (the new app copies its learned data from the old JSON file on first start).
- `json_path` is a folder ending with `/`.
- Many apps: suggest `total_threads` (for example 10) in `appdaemon.yaml` above 20-30 apps.
- Do not add `dependencies:` naming an app that does not exist.
