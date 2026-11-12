# ad-ClimateCommander: notes for an AI assistant

Read this with [README.md](README.md). The workflow for finding the user's real entity ids (token, `ha_snapshot.py`, asking when several match) is in the [Pythm_AppdaemonApps LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md). Follow its rules: use ids from the index only, ask when several match, one app per `apps.yaml` edit, back up first, no commit or push unasked.

## Finding the user's entities (short version; the full workflow is in the guide above)

1. The user creates a long-lived access token in Home Assistant (profile, Security tab) and saves it in a plain-text file `.local/ha_token` in the Pythm_AppdaemonApps folder. The assistant never opens or prints that file and never asks for the token in chat.
2. `python3 tools/ha_snapshot.py --init` creates the file; after the user has filled it in, `python3 tools/ha_snapshot.py --url http://<ha-address>:8123` writes `.local/ha_index.jsonl` (one entity per line: id, name, room, integration, device class, unit, state). It only reads.
3. Search that index for the entity types in the table below. Use only ids found there. When several entities match, ask the user which one (and how it is used) instead of choosing. Without a token, ask the user for the ids.
4. Write one app at a time into `apps.yaml` after backing up the file, and check the app log for WARNING/ERROR lines.

## What the app does

Controls one climate entity per app (heater or HVAC) and optional screening covers. It sets the temperature (and for HVAC the mode heat / cool / fan_only, the fan *Silence* mode and the *boost* preset). It acts on its own at least every 30 minutes (15 in table mode; `interval_minutes` changes it per device), and with `controller: adaptive` in indoor mode also when the room sensor reports, so the user must say yes before it is added. It needs the [ad-Weather](https://github.com/Pythm/ad-Weather) app for outdoor values. ElectricalManagement can send it save / spend / reduce states.

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

Keys: required `climate` (alias `heater`). Optional: `type`, `target_mode` (`indoor`|`table`, else chosen from the keys: a table, `target_heater_temp` or `target_heater_input` gives `table`), `backup_indoor_sensor_temp`, `target_indoor_input`, `target_heater_input`, `temperatures`, `save_temp`, `save_temp_offset` (indoor mode default -0.3), `spend_temp_offset` (default 1), `vacation`, `vacation_temp`, `vacation_keep_off` (or `options: [vacation_keep_off]`), `options` (list of flags: `vacation_keep_off`, `has_fireplace`; never `has_fireplace: true` as a key, unknown names are ignored), `interval_minutes` (1 to 120, default 30 indoor / 15 table), `min_run_gap_minutes` (1 to 30, default 5), `room_sensor_stale_minutes` (5 to 1440, default 60), `backup_sensor_stale_minutes` (5 to 1440, default 60 with adaptive and shadow, no limit with classic), `window_sensor_stale_minutes` (5 to 4320, default 720 with adaptive and shadow, no limit with classic), `trend_hold_hours` (classic indoor mode, 0 to 12, default 2), `increase_hold_hours` (0 to 12, default 2: no heat to fan_only this long after a `daytime_increasing` period that applied stops applying, at its stop or at the mode change / person leaving / `daytime_savings` start that ends it earlier; none for an HVAC in fan_only at that moment; ask when a `daytime_savings` period starts within a few hours after an increase), `mode_app` (name of the ModeManagement app, only for periods with a `mode`), `max_setpoint_drop` (HVAC in heat, lowest setpoint = target minus this, default 3 C / 5.4 F), `cold_start_deficit` (indoor mode, 0.5 to 10, default 2 C / 3.6 F: a vacation end with the room at least this far below target starts with a cold start; also read by `adaptive`; do not ask about it, leave it out unless the user wants another threshold), `max_vacation_temp`, `window_sensor_temp` (alias `window_temp`), `window_offset` (-3; with `controller: shadow`/`adaptive` only the fallback starting value of the learned window level, -3 C / -5.4 F when not set), `windowsensors`, `daytime_savings`, `daytime_increasing`, `silence`, `automate`, `rain_level` (3), `anemometer_speed` (40), `screening` (list: `screen` required, `windowsensors`, `lux_close`, `lux_open`, `lux_open_media`, `not_when_home`, `mediaplayers`), `screening_temp`, `getting_cold` (18), `getting_hot_offset` (10), `cloud_cover` (90: screens close only while the cloud cover in % from the Weather app is below it), `notify_app`, `notify_receiver` (alias `recipient`), `weather_app` (default `weather`), `HASS_namespace`, `MQTT_namespace`, `namespace`, `unit_of_measurement`, own lux sensors `OutLux_sensor`, `OutLuxMQTT`, `OutLux_sensor_2`, `OutLuxMQTT_2`. Controller keys (see "Controller" below): `controller` (`classic` default | `shadow` | `adaptive`), `diagnostic_sensor`, `window_offset_table`, `internal_offset_table`, `target_table`, `outdoor_gain` (0 to 0.6, not set by default), `solar_gain` (1.0), `solar_max` (3 C / 5.4 F), `comfort_guard` (1.5 C / 2.7 F), `window_trigger_delta` (adaptive in indoor mode, 0 to 20, default 1 C / 1.8 F, 0 or `false` is off).

Time periods (`daytime_savings`, `daytime_increasing`, `silence`) are lists of `start` and `stop` (quote them: `'10:00:00'`; an unquoted `22:00:00` is read by YAML as a number) and an optional `presence` list of `person.*`. `presence` means the OPPOSITE in the two energy lists, so ask what the user wants: in `daytime_increasing` the period applies only while at least one listed person is home; in `daytime_savings` it applies only while NONE of them is home (one person home cancels it); `silence` is checked the same way as `daytime_increasing`. A `daytime_savings` entry with `presence` and no `start`/`stop` means "whenever nobody is home". Savings that start while a `daytime_increasing` period applies replace it and start its `increase_hold_hours` hold: the savings setpoint is sent at once, but an HVAC only goes from heat to fan_only when the hold ends (with `has_fireplace` 2 h later than without the increase; see 'Behaviour not to "fix"'). The device runs 5 seconds after every start and stop of `daytime_savings` and `daytime_increasing`, and when a person in their `presence` lists comes home or leaves.
Periods may name a house mode: `mode: night` (or a list, or `away`, `morning`, ...) in a `daytime_savings` / `daytime_increasing` entry makes it apply while the house is in that mode (the `MODE_CHANGE` event of Lightwand / ModeManagement; names are translated, `night` also matches `nightKid`). `start`/`stop` are then optional: `stop` alone means the 12 hours before it, and it is the time the savings end so the room heats up again before morning or before anyone is home. `mode_app: <ModeManagement app name>` gives the mode after a restart. It needs no `dependencies`; never add `mode_translation` or the ModeManagement app there unless they exist. ALWAYS ask the user which modes should start savings and at what clock time the savings must end (morning wake-up, arrival time), and whether ModeManagement is installed and its app name. Never decide the times.

```yaml
  daytime_savings:              # room held cooler (indoor mode: reading shifted +0.5) while nobody is home
    - start: '10:00:00'
      stop: '14:00:00'
      presence: [person.me]
  daytime_increasing:           # room held warmer (target +0.3) while someone is home
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

Per device, default `classic` (the step rules and learned setpoints in the README). `adaptive` computes the setpoint every run from the target, a feed-forward from the climate's own sensor offset (HVAC), a slow integral, a proportional term and a sun term from the window sensor relative to a LEARNED no-sun window level; at most one change per 12 min, the whole change at once. It learns the window level and the climate-sensor offset by outdoor temperature (JSON key `"adaptive"`, next to classic `"data"`, which it never touches), and the backup-sensor offset. Learning goes by time: at most one sample per 12 min, and with an interval above 12 min a sample counts for the time since the last one (at most one interval), so the default 30 min learns as fast as 12; extra runs on sensor reports do not speed it up. Do not shorten `interval_minutes` to make it learn faster. `shadow` runs classic unchanged and only computes and logs what adaptive would do. Works in indoor mode (heaters, HVAC) and for table-mode heaters; an HVAC in table mode stays classic. README section "Adaptive controller (optional)".

When to suggest what:
- A new user or an existing working setup: leave `classic` (nothing to do). Mention `shadow` only if they report mild-weather offsets, sun overshoot, a window sensor that triggers too often, or a room sensor that drops out.
- Before `adaptive`: ALWAYS `shadow` for 1 to 2 weeks with `json_path` set, then `adaptive` after the user has looked at the DEBUG line (`log_level: DEBUG` on the app) or the diagnostic sensor. Heaters (floor heating, panels): shadow first in every case; the heater constants are only tuned in a simulation with guessed physics.
- `target_table`, `window_offset_table`, `internal_offset_table` are optional. Never invent their values; suggest them only when the user gives the numbers (for example read from the diagnostic sensor after weeks of shadow). `internal_offset_table` is the remedy for a heater with no room sensor at all.
- `diagnostic_sensor` is an entity the app creates with `set_state`; it is not in the HA index. Ask the user which id they want (suggest `sensor.<room>_controller`) and check it does not clash with an existing entity. It disappears when HA restarts and comes back at the next run. Its attribute `rate_wide` is the room trend over a window that follows the sensor's report gaps (3 x the median of the last 9 gaps, at least the normal window, at most 4 h; None with too few reports). It is read-only, for the user to judge the trend; the controller never uses it.

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

## Finding how often a sensor reports (the stale keys)

A sensor counts as unavailable after `room_sensor_stale_minutes` (60), `backup_sensor_stale_minutes` or `window_sensor_stale_minutes` without a report. Home Assistant sends no event for a report with an unchanged value, so a heartbeat with the same value looks like silence. A sensor with a 55 minute heartbeat can show 110 minutes without a report, and with the default it is treated as offline now and then (backup or estimate in use, WARNING `no fresh room sensor`). Do this for the room, backup and window sensors of a device that uses `controller: shadow` or `adaptive`, and whenever the user reports that WARNING. Do not ask the user for these values.

1. Run `tools/ha_report_cadence.py` from this repository's clone. It reads the `.local/ha_url` and `.local/ha_token` files made by `tools/ha_snapshot.py` in the [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps) clone (see its [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md)); it uses the folder named by the `HA_LOCAL` environment variable, else this repository's `.local` folder, else `../Pythm_AppdaemonApps/.local` when the two clones are side by side:
   `python3 tools/ha_report_cadence.py sensor.room sensor.backup sensor.window --days 14`
   It only reads (GET) with the user's token, and it never prints the token. Never open or print the token yourself.
2. For each sensor it prints the reports per day, the median, 95th percentile and longest gap, the resolution, a heartbeat guess, the longest normal gap and a suggested stale value (1.5 x the longest normal gap, rounded to 5 minutes). Example: a heartbeat of 54.7 minutes with gaps up to 109.5 minutes gives 165.
3. Read the list of gaps it did not count as normal. An outage or a hole in the history belongs there. If a long gap counted as normal was really an outage, take the next one down yourself and multiply by 1.5.
4. Set the key only when the suggested value is above the default for that sensor (room 60, backup 60, window 720). A sensor that reports every 10 minutes needs no key. Leave the backup and window keys out with `classic` unless the user wants a limit there: classic has none by default.
5. Tell the user the value, the sensor, and where it comes from (heartbeat, longest gap, number of days of history), and that a sensor that really stops is then believed for that long.
6. If the longest normal gap of the room sensor is above 25 minutes, tell the user: the room trend `rate` stays 0.0 on this sensor (its window holds fewer than 3 reports), so the rule "the room still rises 0.2 C/h" does not fire and, on a heater with reports less often than about every 55 minutes, the predictive trend does nothing. Then ask whether the room has an extra heat source (fireplace, stove, strong sun through glass). Yes on an HVAC: `options: [has_fireplace]`; it does nothing on a heater. Mention that the diagnostic attribute `rate_wide` shows the trend for them to judge (read-only).

Under 20 reports, no history, or a gap you cannot explain: ask the user how the sensor reports. Home Assistant only keeps history for its recorder period, often 10 days.

## ALWAYS ask the user

- Heater or HVAC, and which target method: a fixed indoor temperature, or a table by outdoor temperature (and the table values). Never invent table offsets.
- Two temperature sensors of one kind (two in one room, or a window sensor and a room sensor): ALWAYS ask how they are used: main and backup in the same room, a sensor on another side of the house, or something else. Never decide from names or areas. A window sensor is only for sun detection; ask where it sits (airflow, above a heater, in direct sun) because that changes how its readings must be read.
- HVAC (heat pump) that heats: "Does this room have a fireplace (or another heat source that can make it overshoot fast, such as a wood stove)?" ALWAYS ask, never decide from the room name or area. Yes: `options: [has_fireplace]` (heat to fan_only at once at target + 0.6, as before 1.4.0). No: leave it out (the setpoint is lowered first and fan_only comes only at the lowest setpoint or while the room still rises; the rise is not seen on a room sensor that reports less often than about every 25 minutes, see "Finding how often a sensor reports"). See README "Heat pump cycling".
- `interval_minutes`: leave it out (30 min default) unless the user asks for faster reactions (12 was the default before this version) or has a room sensor that needs it; say that a shorter interval can chase a sensor in the unit's air flow. Never set it below 12 without a reason from the user. With `controller: adaptive` in indoor mode the runs follow the room sensor and `interval_minutes` is only the longest time without a run, so a shorter one is rarely needed. Leave `min_run_gap_minutes` and `window_trigger_delta` out unless the user asks.
- Which windows belong to the room, and whether an open window should lower the heat at all.
- Vacation: which `input_boolean`, the vacation temperature, and whether the heater may be turned off (`vacation_keep_off`).
- Whether a window sensor exists at all and where it sits (see above). Whether the room needs different targets in different seasons (then an outdoor-temperature table, never invented values).
- Who should get the window notifications, if anyone, and how they are sent. Two ways: (1) the user's own notification app, normally `notify_mobiles` from [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps): set `notify_app: notify_mobiles` (check the app's real name in the user's `apps.yaml`) and `notify_receiver` is a list of the **person labels** from that app's `recipients` (for example `[Me]`); a label that does not exist there gets nothing, no `notify_receiver` means nobody, and a recipient with a `tracker` only gets it while home. (2) Without `notify_app`: Home Assistant `notify/<receiver>` services, and `notify_receiver` is a list of service names such as `mobile_app_phone`. Never mix the two kinds of names. Ask which one the user has.
- Controlled by ElectricalManagement? Then the heater's **rated power in watts** goes in the `power:` key in ElectricalManagement. Find it from the device's own power sensor history first (`tools/ha_peak_power.py` from the [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps) repository (the same clone that holds `tools/ha_snapshot.py` and the `.local/ha_token` file from the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md); if the user has not cloned it yet, `git clone https://github.com/Pythm/Pythm_AppdaemonApps.git`, or download the single file from its `tools` folder): `python3 tools/ha_peak_power.py <sensor>`, read-only, uses the user's token; take the highest value rounded to 10 W when the plateau is clear, and tell the user which value you took). Ask the user only when there is no power sensor, no history, or the plateau is not clear. Never use EM's learned or stored `normal_power`.
- Before `controller: shadow` / `adaptive`: where the window sensor sits (glass, frame, behind a curtain, in the air flow of a heat pump, above a radiator or floor heating). The controller learns its level either way, but say that a sensor in air flow or above a heater mostly sees the device, not the sun. Whether the climate entity reports `current_temperature` and in which unit `min_temp` / `max_temp` are (adaptive uses them as limits).
- The id for `diagnostic_sensor`, if they want one (see "Controller").
- Offset or target table values: only from the user, never invented, never "typical" numbers.

## Behaviour not to "fix"

- When a device runs: only `controller: adaptive` in indoor mode runs on sensor reports (every room sensor report and a change of `window_trigger_delta` at `window_sensor_temp`), at most once per `min_run_gap_minutes` (default 5) with one run at the end of that time, and `interval_minutes` is then the longest time without a run. Classic and shadow keep the fixed interval, because every classic run adds the whole difference to the setpoint, so more runs would mean more gain; table mode keeps it too. Do not add sensor runs to classic. The backup sensor and the climate's own sensor never start a run, and no report starts one while a window is open or on vacation. Every controller runs 5 seconds after the start and stop of `daytime_savings` / `daytime_increasing` (ignoring the minimum gap) and when a person in their `presence` comes home or leaves (classic: only while that period applies by the clock). `silence` has its own timers and starts no run.
- Open window: an indoor mode heater gets the lowest of `vacation_temp` and `save_temp` (if set); table mode the lowest of save and vacation; an HVAC goes to `fan_only` and its setpoint is not touched.
- Screens also close from the room sensor between runs (the room trigger, always on): raw room value, at most one attempt per 5 minutes, closes only, never touches the setpoint or the HVAC mode and never clears the wait for lux. There is deliberately no `fan_only` on screen close (the solar gain disappears and the unit would cycle). Table mode heaters close screens too. Do not add a setting for any of this unless the user asks.
- Indoor mode: small drops are rounded to -0.5 after 30 min, small rises wait 30 min (deliberate asymmetry); the extra -1 after 4 h turned down is deliberate in the current (classic) controller; cooling setpoints are not learned.
- Table mode: open windows and `reduce` use the LOWEST of the save and vacation temperature (owner decision). An entry applies from its `out` value and up.
- Learned setpoints are only updated in normal operation (not on vacation, open window, daytime savings/increasing or ElectricalManagement states); an HVAC learns nothing within 2 h of a mode change or while `hvac_action` is not heating/idle.
- Classic indoor mode: the setpoint is kept while the room reaches the target within `trend_hold_hours` (trend over the sensor reports of the last hour, also in fan_only); a learned setpoint is approached by at most 0.5 per run, never jumped to.
- Classic indoor mode: a normal setpoint move goes to the computed value at once, on the 0.5 grid (heat and cool), so a new target or the start or end of a daytime period moves it in one run. The only limit per run is the one towards a learned setpoint (previous line).
- Cold start after a vacation (indoor mode, only when `vacation` goes from on to off; not after a window, not on a cold morning): room at least `cold_start_deficit` below target -> setpoint target + the difference (at most +6 C / 10.8 F and the climate's `max_temp`), held, nothing learned, until the room is within 1 C (1.8 F) of target or 8 h passed; then one move to min(setpoint held before the vacation, learned setpoint) (one of them, else target + 1), not below an HVAC's lowest heat setpoint. A smaller deficit: the setpoint held before the vacation, at most learned (or target) - 1. Ends on a window, a new vacation, an HVAC leaving heat, a heater turned off, an app restart (memory only). `adaptive` does the same with its own setpoint (integral frozen, then set to the value sent); in `shadow` classic does it and the controller only proposes. Example: target 23, room 17 -> 29 until 22.0, then e.g. 24.5. A setpoint of 28-29 right after a vacation is this, not a fault.
- HVAC in heat without `has_fireplace`: at target + 0.6 the setpoint is lowered first (by the computed adjustment, not below target - `max_setpoint_drop`); fan_only only at the lowest setpoint or while the room still rises 0.2 C/h (the trend stays 0.0 on a sensor that reports less often than about every 25 minutes: then only the lowest setpoint or `has_fireplace`). Back from fan_only the setpoint starts from the last held one (at most learned - 1); after a lowering it returns in one move once the room is at or below target. fan_only to cool still needs outdoor above target - 4, with or without a fireplace.
- Temperature uses the lowest and lux the highest value in the Weather app, by design.
- `input_boolean.vacation` is the default vacation switch only if it exists (shared convention).
- `controller: shadow` / `adaptive`, deliberate:
  - At most one setpoint change per 12 min, the whole change at once (owner decision, not a key), also with runs every 5 min; a change that has to wait gets its own run. Window, vacation and power-state starts and ends are sent at once.
  - Learning, the sun-trend points, the backup offset, the JSON and the diagnostic sensor go by time, not by the number of runs. The sun term only rises on a new window reading or at a sample that is due, not on every room report.
  - No learned buckets (`"data"` neither read nor written), no 6-run hold, no steady-room correction, no extra -1 after 4 h (that rule is classic only).
  - The window level without sun is learned (negative or positive, no clamp); `window_offset` is only its starting value. The window sensor counts as stale after 12 h, the room sensor after 1 h, unless the stale keys say otherwise (see "Finding how often a sensor reports").
  - Without a room sensor: climate sensor minus the learned offset (or `internal_offset_table`), integral frozen, no learning, no HVAC mode change; without an offset: open loop. A table heater without any room value leaves the "room above target" correction out.
  - The comfort guard drops a power-state offset (also `spend`) until the state changes.
  - HVAC: at least 30 min in a mode; heat to fan_only only when the setpoint is at its lowest (target - `max_setpoint_drop`) or the room still rises (with `has_fireplace`: at target + 0.6 alone); not within 1 h after a power state ended; not within `increase_hold_hours` after a `daytime_increasing` period that applied stops applying (adaptive ends the hold early on a sun term of 1 C or more, or a room more than 1 C above the fan_only line). The hold counts from the moment the increase is no longer active: its `stop` (also across midnight), or the `MODE_CHANGE` event / the person leaving that ends it before `stop` (mode `day` until 23:00, house to `night` at 21:40: hold 21:40-23:40, none after 23:00); a period with a `mode` and no `stop` holds from the mode change that ends it. A `daytime_savings` period that starts while the increase applies replaces it and the hold counts from the savings start (its `start` time, or the mode / person event that starts it): increase 22:00-01:00, savings from 00:00: hold 00:00-02:00. The hold wins over the savings (owner decision): the savings setpoint is sent at their start, only heat -> fan_only waits for the hold end; heaters are not affected. Savings that start while no increase applies, or that are on before its times begin, give no hold; savings that end inside the increase's times end the hold and the increase applies again. An HVAC in fan_only at that moment gets no hold; heaters and an HVAC in heat or cool do. A period that never applied that day has no hold. After a restart inside the hold, a `stop` before the restart holds when the `mode` and `presence` at the restart match; a hold started by a mode change, a person leaving or a savings start before the restart is lost. The same rule holds for classic HVAC.
  - `rate` (the rule above and the heaters' predictive trend) stays 0.0 on a sensor that reports less often than about every 25 minutes (heater: 55): its window holds fewer than 3 reports. Control keeps that window on purpose; `rate_wide` is diagnostics only, and must not be wired into control. The remedy for a room with an extra heat source is `has_fireplace`.
  - The vacation temperature is the setpoint floor in indoor mode; the climate's `min_temp`/`max_temp` are limits.
  - Shadow never commands; its log lines are prefixed `[shadow]`.

## ElectricalManagement link

In ElectricalManagement's YAML, not here:
```yaml
  climate_commander:
    - app: heater_bathroom      # this app's name
      power: 2000               # rated watts, from the sensor history or the user
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
