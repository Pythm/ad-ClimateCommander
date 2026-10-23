# ad-ClimateCommander: notes for an AI assistant

Read this with [README.md](README.md). The workflow for finding the user's real entity ids (token, `ha_snapshot.py`, asking when several match) is in the [Pythm_AppdaemonApps LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md). Follow its rules: use ids from the index only, ask when several match, one app per `apps.yaml` edit, back up first, no commit or push unasked.

## Finding the user's entities (short version; the full workflow is in the guide above)

1. The user creates a long-lived access token in Home Assistant (profile, Security tab) and saves it in a plain-text file `.local/ha_token` in the Pythm_AppdaemonApps folder. The assistant never opens or prints that file and never asks for the token in chat.
2. `python3 tools/ha_snapshot.py --init` creates the file; after the user has filled it in, `python3 tools/ha_snapshot.py --url http://<ha-address>:8123` writes `.local/ha_index.jsonl` (one entity per line: id, name, room, integration, device class, unit, state). It only reads.
3. Search that index for the entity types in the table below. Use only ids found there. When several entities match, ask the user which one (and how it is used) instead of choosing. Without a token, ask the user for the ids.
4. Write one app at a time into `apps.yaml` after backing up the file, and check the app log for WARNING/ERROR lines.

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

Keys: required `climate` (alias `heater`). Optional: `type`, `target_mode` (`indoor`|`table`, else chosen from the keys: a table, `target_heater_temp` or `target_heater_input` gives `table`), `backup_indoor_sensor_temp`, `target_indoor_input`, `target_heater_input`, `temperatures`, `save_temp`, `save_temp_offset` (indoor mode default -0.3), `spend_temp_offset` (default 1), `vacation`, `vacation_temp`, `vacation_keep_off` (or `options: [vacation_keep_off]`), `max_vacation_temp`, `window_sensor_temp` (alias `window_temp`), `window_offset` (-3; with `controller: shadow`/`adaptive` only the fallback starting value of the learned window level, -3 C / -5.4 F when not set), `windowsensors`, `daytime_savings`, `daytime_increasing`, `silence`, `automate`, `rain_level` (3), `anemometer_speed` (40), `screening` (list: `screen` required, `windowsensors`, `lux_close`, `lux_open`, `lux_open_media`, `not_when_home`, `mediaplayers`), `screening_temp`, `getting_cold` (18), `getting_hot_offset` (10), `cloud_cover` (90: screens close only while the cloud cover in % from the Weather app is below it), `notify_app`, `notify_receiver` (alias `recipient`), `weather_app` (default `weather`), `HASS_namespace`, `MQTT_namespace`, `namespace`, `unit_of_measurement`, own lux sensors `OutLux_sensor`, `OutLuxMQTT`, `OutLux_sensor_2`, `OutLuxMQTT_2`. Controller keys (see "Controller" below): `controller` (`classic` default | `shadow` | `adaptive`), `diagnostic_sensor`, `window_offset_table`, `internal_offset_table`, `target_table`, `outdoor_gain` (0 to 0.6, not set by default), `solar_gain` (1.0), `solar_max` (3 C / 5.4 F), `comfort_guard` (1.5 C / 2.7 F).

Time periods (`daytime_savings`, `daytime_increasing`, `silence`) are lists of `start` and `stop` (quote them: `'10:00:00'`; an unquoted `22:00:00` is read by YAML as a number) and an optional `presence` list of `person.*`. `presence` means the OPPOSITE in the two energy lists, so ask what the user wants: in `daytime_increasing` the period applies only while at least one listed person is home; in `daytime_savings` it applies only while NONE of them is home (one person home cancels it); `silence` is checked the same way as `daytime_increasing`. A `daytime_savings` entry with `presence` and no `start`/`stop` means "whenever nobody is home".
```yaml
  daytime_savings:              # room held cooler (indoor mode: reading shifted +0.5) while nobody is home
    - start: '10:00:00'
      stop: '14:00:00'
      presence: [person.me]
  daytime_increasing:           # room held warmer (reading shifted -0.5) while someone is home
    - start: '21:30:00'
      stop: '23:30:00'
      presence: [person.me]
  silence:                      # HVAC only: fan mode Silence, needs `Silence` in the climate's fan_modes
    - start: '21:00:00'
      stop: '08:00:00'
```

Deprecated, still working (do not write them in new configs): `HVAC:` / `Heaters:` lists in one app (WARNING), `outside_temperature`, `rain_sensor`, `anemometer`, `away_state`.

The configuration is validated by pydantic models in `cc_config.py` (`AppConfig`, `DeviceConfig`). A wrong type gives an ERROR with the key name in the app log and the device is skipped. When a key is added, add it to the model, the README and this list.

## Controller (`controller: classic | shadow | adaptive`)

Per device, default `classic` (the step rules and learned setpoints in the README). `adaptive` computes the setpoint every run from the target, a feed-forward from the climate's own sensor offset (HVAC), a slow integral, a proportional term and a sun term from the window sensor relative to a LEARNED no-sun window level; at most one change per 12 min, 0.5 per change. It learns the window level and the climate-sensor offset by outdoor temperature (JSON key `"adaptive"`, next to classic `"data"`, which it never touches). `shadow` runs classic unchanged and only computes and logs what adaptive would do. Works in indoor mode (heaters, HVAC) and for table-mode heaters; an HVAC in table mode stays classic. README section "Adaptive controller (optional)".

When to suggest what:
- A new user or an existing working setup: leave `classic` (nothing to do). Mention `shadow` only if they report mild-weather offsets, sun overshoot, a window sensor that triggers too often, or a room sensor that drops out.
- Before `adaptive`: ALWAYS `shadow` for 1 to 2 weeks with `json_path` set, then `adaptive` after the user has looked at the DEBUG line (`log_level: DEBUG` on the app) or the diagnostic sensor. Heaters (floor heating, panels): shadow first in every case; the heater constants are only tuned in a simulation with guessed physics.
- `target_table`, `window_offset_table`, `internal_offset_table` are optional. Never invent their values; suggest them only when the user gives the numbers (for example read from the diagnostic sensor after weeks of shadow). `internal_offset_table` is the remedy for a heater with no room sensor at all.
- `diagnostic_sensor` is an entity the app creates with `set_state`; it is not in the HA index. Ask the user which id they want (suggest `sensor.<room>_controller`) and check it does not clash with an existing entity. It disappears when HA restarts and comes back at the next run.

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
- Two temperature sensors of one kind (two in one room, or a window sensor and a room sensor): ALWAYS ask how they are used: main and backup in the same room, a sensor on another side of the house, or something else. Never decide from names or areas. A window sensor is only for sun detection; ask where it sits (airflow, above a heater, in direct sun) because that changes how its readings must be read.
- Which windows belong to the room, and whether an open window should lower the heat at all.
- Vacation: which `input_boolean`, the vacation temperature, and whether the heater may be turned off (`vacation_keep_off`).
- Whether a window sensor exists at all and where it sits (see above). Whether the room needs different targets in different seasons (then an outdoor-temperature table, never invented values).
- Who should get the window notifications, if anyone, and how they are sent. Two ways: (1) the user's own notification app, normally `notify_mobiles` from [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps): set `notify_app: notify_mobiles` (check the app's real name in the user's `apps.yaml`) and `notify_receiver` is a list of the **person labels** from that app's `recipients` (for example `[Me]`); a label that does not exist there gets nothing, no `notify_receiver` means nobody, and a recipient with a `tracker` only gets it while home. (2) Without `notify_app`: Home Assistant `notify/<receiver>` services, and `notify_receiver` is a list of service names such as `mobile_app_phone`. Never mix the two kinds of names. Ask which one the user has.
- Controlled by ElectricalManagement? Then also ask for the heater's **rated power in watts** for the `power:` key in ElectricalManagement. Never take it from the consumption sensor's history or a guess: ElectricalManagement no longer learns it.
- Before `controller: shadow` / `adaptive`: where the window sensor sits (glass, frame, behind a curtain, in the air flow of a heat pump, above a radiator or floor heating). The controller learns its level either way, but say that a sensor in air flow or above a heater mostly sees the device, not the sun. Whether the climate entity reports `current_temperature` and in which unit `min_temp` / `max_temp` are (adaptive uses them as limits).
- The id for `diagnostic_sensor`, if they want one (see "Controller").
- Offset or target table values: only from the user, never invented, never "typical" numbers.

## Behaviour not to "fix"

- Indoor mode: small drops are rounded to -0.5 after 30 min, small rises wait 30 min (deliberate asymmetry); the extra -1 after 4 h turned down is deliberate in the current (classic) controller; cooling setpoints are not learned.
- Table mode: open windows and `reduce` use the LOWEST of the save and vacation temperature (owner decision). An entry applies from its `out` value and up.
- Learned setpoints are only updated in normal operation (not on vacation, open window, daytime savings/increasing or ElectricalManagement states).
- Temperature uses the lowest and lux the highest value in the Weather app, by design.
- `input_boolean.vacation` is the default vacation switch only if it exists (shared convention).
- `controller: shadow` / `adaptive`, deliberate:
  - At most one setpoint change per 12 min and 0.5 per change (owner decision, not a key); window, vacation and power-state starts and ends are sent at once.
  - No learned buckets (`"data"` neither read nor written), no 6-run hold, no steady-room correction, no extra -1 after 4 h (D2 is classic only).
  - The window level without sun is learned (negative or positive, no clamp); `window_offset` is only its starting value. The window sensor counts as stale after 12 h, the room sensor after 1 h.
  - Without a room sensor: climate sensor minus the learned offset (or `internal_offset_table`), integral frozen, no learning, no HVAC mode change; without an offset: open loop. A table heater without any room value leaves the "room above target" correction out.
  - The comfort guard drops a power-state offset (also `spend`) until the state changes.
  - HVAC: at least 30 min in a mode; heat to fan_only only when the setpoint is at its lowest or the room still rises; not within 1 h after a power state ended.
  - The vacation temperature is the setpoint floor in indoor mode; the climate's `min_temp`/`max_temp` are limits.
  - Shadow never commands; its log lines are prefixed `[shadow]`.

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

## Check an existing config (old or faulty keys)

When the user already has a config, read it before changing anything and report:
- `HVAC:` / `Heaters:` lists in one app (deprecated, suggest one app per climate, ask first), `away_state` (use `vacation`), `outside_temperature`, `rain_sensor`, `anemometer` (configure them in ad-Weather).
- A missing `climate`, an entity id that does not exist in the HA index, `type` other than `heater` / `hvac`, `json_path` without a trailing `/`.
- Unquoted times in the period lists, `presence` that is not a `person.*`, `notify_receiver` names that do not match the `notify_app` style (see above).
- A table heater without `indoor_sensor_temp` and without `unit_of_measurement`: the app stops with a WARNING. A table heater without an indoor sensor uses the thermostat's own `current_temperature` (often the floor), which counts as the room: tell the user.
- A heater listed in ElectricalManagement's `climate_commander:` without `power:`.

## Troubleshooting

Logs show the app name. Wrong values: `Invalid configuration for <app>, app not started: ...` (app-level key) or an ERROR naming the key with the device skipped. Other WARNINGs and what they mean are in the README, section "Troubleshooting". Learned setpoints are in `<json_path>/<app name>.json`; deleting it only resets learning. A missing climate entity is logged once, not repeatedly. The adaptive controller's state is the `"adaptive"` entry per climate in the same file; to reset only that, stop the app and delete that entry. Its WARNING/INFO lines (room estimate, open loop, comfort guard, setpoint changed by hand, state from another unit) are in the README Troubleshooting table.

## Rules for the assistant

- One app per climate entity in new configs; when moving a device from an old list app, remove it from the list in the same edit (the new app copies its learned data from the old JSON file on first start).
- `json_path` is a folder ending with `/`.
- Many apps: suggest `total_threads` (for example 10) in `appdaemon.yaml` above 20-30 apps.
- Do not add `dependencies:` naming an app that does not exist.
