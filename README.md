# Climate Commander 🌡️
**A climate control app for [Home Assistant](https://www.home-assistant.io/) built with [AppDaemon](https://appdaemon.readthedocs.io/en/latest/).**  
One app controls one climate entity: a heater (floor heating, panel heater) or an HVAC unit (heat pump, air conditioner). It sets the temperature from an indoor sensor or from a table of outdoor temperatures, switches HVAC between heat, cool and fan_only, closes and opens screening covers from lux, rain, wind and cloud cover, and follows vacation, open windows, time periods and the save / spend / reduce states from [ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement). Outdoor values come from the [ad-Weather](https://github.com/Pythm/ad-Weather) app.

> 🤖 **Set up with an AI assistant:** the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md) helps an AI assistant find your own entity ids in Home Assistant and write the configuration. Notes for the assistant about this app: [LLM.md](LLM.md).

![AI‑Generated Screenshot](_5b05cb75-1f9c-4fed-9aa6-0e4f9d73c8ac.jpg)

## Contents

- [Breaking Changes](#-breaking-changes)
- [Features](#-features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [One app per device](#one-app-per-device)
- [How the setpoint is found](#how-the-setpoint-is-found)
- [HVAC modes, fan and preset](#hvac-modes-fan-and-preset)
- [Screens and covers](#screens-and-covers)
- [Weather integration](#weather-integration)
- [ElectricalManagement](#electricalmanagement)
- [Configuration reference](#configuration-reference)
- [Full example configurations](#full-example-configurations)
- [Validation](#validation)
- [Notifications](#notifications)
- [Troubleshooting](#troubleshooting)
- [Notes on deliberate behaviour](#notes-on-deliberate-behaviour)
- [License](#-license)
- [Contributing](#-contributing)

---
## 🚨 Breaking Changes

### 1.4.0
**Nothing to do for the default case**: your configuration works as before. What is new or different:

- **One app per climate entity** is the recommended setup: top-level `climate:` and `type: heater` or `type: hvac`, see [One app per device](#one-app-per-device). The old style with `HVAC:` / `Heaters:` lists in one app still works but logs a deprecation WARNING.
- **Heaters with an outdoor-temperature table** (`temperatures`, `target_heater_temp`, `save_temp`, ...). This is the heater logic from [ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement). See [How the setpoint is found](#how-the-setpoint-is-found).
  - Table mode: with no save or vacation temperature configured, the climate's own `min_temp` is used (before: a fixed 10). The window and indoor corrections together lower the setpoint by at most 3 degrees.
- **Bug fixes in the same release** (they apply to every setup):
  - The JSON file is written through a temporary file and replaced, so a crash during the write can no longer destroy the learned data.
  - HVAC in table mode now follows `reduce` from ElectricalManagement (the lowest of the save and vacation temperature), as table heaters do. Before, `reduce` was ignored.
  - Table mode: an offline `window_sensor_temp` no longer lowers the setpoint (it counts as exactly at the threshold). Before, it was compared against the heater target instead of the indoor target.
  - A stale indoor sensor with an unavailable backup sensor now uses the stale value (as documented). Before, no value was used and the run was skipped.
  - The setpoint set when an HVAC returns from `fan_only` to heat is rounded to 0.5 like every other command (before, a value such as 21.3 was sent and sent again).
  - A missing or unavailable climate entity is logged once (WARNING for missing, INFO for unavailable), not silently.
  - An HVAC in table mode without `vacation_temp` no longer fails on vacation (falls back to 10).
- **Save / spend / reduce from ElectricalManagement** (optional, ElectricalManagement 1.0.6 or later), see [ElectricalManagement](#electricalmanagement).
- **The app now has two more files**, `cc_devices.py` and `cc_config.py`, next to `climateCommander.py` and `weather_data.py`. Copy the whole app folder when you update (HACS does).
- **The configuration is validated with pydantic** (`cc_config.py`). A value of the wrong type (for example `window_offset: abc`, or a table entry without `out`) now logs an ERROR naming the key: an invalid device is skipped, an invalid app-level key stops that app. Before, such a value was silently used or crashed later (`vacation_temp: abc` used to fall back to 10). A single entity where a list is expected (for example `windowsensors: binary_sensor.x`) is accepted as a one-item list. See [Validation](#validation).
- **New key `cloud_cover`** (default `90`): screens close only while the cloud cover from the Weather app is below this percentage. Before, the limit was fixed at 90, so the default keeps the old behaviour. See [Screens and covers](#screens-and-covers).
- Behaviour changes in the indoor-target mode (HVAC, and heaters without a table):
  - The learned setpoints are no longer updated on vacation, with open windows, during `daytime_savings` / `daytime_increasing` or while ElectricalManagement sends `save`, `spend` or `reduce`. Before, vacation temperatures were learned and pulled the setpoint down after a vacation.
  - A room that stays 0.3 degrees or more away from the target is now corrected when the setpoint has not changed for 30 minutes. Before, nothing happened while the temperature did not move.
  - `backup_indoor_sensor_temp` is used only when the indoor sensor is unavailable or has not **reported** for an hour (`last_reported`). Before, a steady temperature for an hour (no change) counted as a dead sensor.
- Window notifications are sent once per event: "Window open" (getting cold) once after windows are opened, "Window closed" (getting hot, `getting_hot_offset` above target) once after they are closed. Before, "Window open" was sent once per app start and "Window closed" never. HVAC now uses the same rule as heaters: "Window open" also needs the outdoor temperature to be below `getting_cold`.
- Outdoor values are kept per app, not shared between all ClimateCommander apps (they used to overwrite each other). At startup the last values sent by the Weather app are used.
- The periodic runs of the devices (every 12 minutes, 15 minutes in table mode) are spread over the interval, so many devices do not call Home Assistant in the same second.
- `outside_temperature`, `rain_sensor` and `anemometer` (deprecated) are read in `HASS_namespace`.

#### Upgrading from 1.3 to one app per device (optional)
Before:
```yaml
climate_control:
  module: climateCommander
  class: Climate
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  HVAC:
    - climate: climate.living_room
      indoor_sensor_temp: sensor.living_room_temperature
      target_indoor_temp: 22.8
```
After:
```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.living_room
  type: hvac
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  indoor_sensor_temp: sensor.living_room_temperature
  target_indoor_temp: 22.8
```
- App-level keys (`vacation`, `vacation_temp`, `notify_app`, `notify_receiver`, `json_path`, `cloud_cover`, ...) go into each app. YAML anchors (`<<: *climate_defaults`) can share them inside one file.
- Learned setpoints: on its first start a one-device app copies the data for its entity from the other JSON files in `json_path` (the old app's file), then writes its own `<app name>.json`. Remove the device from the old app when you move it.
- Many apps: AppDaemon gives every app its own thread. With more than 20-30 apps, set `total_threads` (for example 10) in `appdaemon.yaml`; the apps then share the threads.

### 1.3.0
- **The [ad-Weather](https://github.com/Pythm/ad-Weather) app is required** for outdoor temperature, rain, wind and lux. An ERROR is logged at startup if it is not found; `weather_app` sets its name. **Nothing to do** if you already use it. Install ad-Weather 0.2.0 or later: it sends values it does not have as `None`, which this version handles.
- `outside_temperature`, `rain_sensor` and `anemometer` are **deprecated** (a WARNING is logged). They still work and take precedence while they report.
- Own outdoor lux sensors (`OutLux_sensor`, `OutLuxMQTT`, and the `_2` versions) are used while they report; after 20 minutes of silence the lux from ad-Weather is used. The logic is in the new file `weather_data.py` (a copy of the file in Lightwand), so copy the whole app folder when you update.

### 1.2.4
- **Spelling correction**: `notify_reciever` is now `notify_receiver`.

### 1.2.3
- **MQTT default namespace**: changed to `mqtt`, the default in the AppDaemon documentation.

### 1.2.0
- **Notifications**: recipients are now sent as a list (one call). *Custom notification apps may need updates.*
- **Weather sensors**: automatic weather sensor detection was removed. Use the [ad-Weather](https://github.com/Pythm/ad-Weather) app or a custom event-based solution.

### 1.1.0
- **Unified control naming**: climate entities are configured as either **HVAC** or **Heater**.
- `indoor_temp` was renamed to `indoor_sensor_temp`.
- The "vacation mode" (away state) was renamed to **`vacation`**, to tell it apart from daily *away* states.

---
## 🔍 Features

- **Two ways to find the setpoint**: keep a fixed indoor temperature from an indoor sensor (the setpoint that held the target is learned per outdoor temperature and lux), or follow a table of outdoor temperatures (floor heating, panel heaters).
- **HVAC mode switching** between `heat`, `cool` and `fan_only`, the fan mode `Silence` in set periods, and the `boost` preset in wind.
- **Screens / covers** close when the room is warm and it is bright, and open on low lux, rain or wind.
- **Vacation**: a lower temperature, optionally the heater turned off (table mode).
- **Open windows**: lower temperature (heaters) or `fan_only` (HVAC), and a notification when it gets cold or hot.
- **Time periods**: `daytime_savings` (lower), `daytime_increasing` (higher) and `silence` (HVAC fan), optionally only depending on who is home.
- **Rain and wind** raise the heating a little.
- **Electricity price control** with [ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement): save when electricity is expensive, spend before it gets expensive or on solar overproduction, reduce when the house is over its kWh goal.

---
## Requirements

- Home Assistant and AppDaemon 4.5, with Python 3.10 or later.
- The Python package `pydantic` version 2 (AppDaemon 4.5 depends on it, so it is normally installed already).
- The [ad-Weather](https://github.com/Pythm/ad-Weather) app, version 0.2.0 or later. ClimateCommander looks for an app named `weather`; set `weather_app` if yours has another name. An ERROR is logged at startup if it is not found.
- All four files of the app in the same folder: `climateCommander.py`, `cc_devices.py`, `cc_config.py` and `weather_data.py`.
- A temperature sensor in the room (`indoor_sensor_temp`). It is required in indoor mode, and it is used to find the unit (C or F) unless you set `unit_of_measurement`.
- Optional: [ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement) 1.0.6 or later for save / spend / reduce.

---
## Installation

**With HACS**: install Climate Commander from HACS (AppDaemon category). HACS copies the whole app folder and updates it.

**By hand**: clone the repository into your AppDaemon apps folder:

```bash
git clone https://github.com/Pythm/ad-ClimateCommander.git /path/to/appdaemon/apps/ad-ClimateCommander
```

When you update by hand, copy all four files (`climateCommander.py`, `cc_devices.py`, `cc_config.py`, `weather_data.py`). An old `climateCommander.py` next to new helper files, or the other way round, does not work.

---
## Quick start

Add one app per climate entity to your AppDaemon `apps.yaml`. Keys you leave out use their defaults, see [Configuration reference](#configuration-reference).

**HVAC (heat pump) that keeps the room at 22.7 degrees:**

```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.living_room
  type: hvac
  indoor_sensor_temp: sensor.living_room_temperature
  target_indoor_temp: 22.7
  json_path: /conf/persistent/Climate/
```

**Floor heater that follows a table of outdoor temperatures:**

```yaml
heater_bathroom:
  module: climateCommander
  class: Climate
  climate: climate.bathroom_floor
  type: heater
  indoor_sensor_temp: sensor.bathroom_temperature
  target_indoor_temp: 24
  target_heater_temp: 28
  temperatures:
    - out: -10
      offset: 3
    - out: 0
      offset: 1
    - out: 10
      offset: 0
    - out: 15
      offset: -3
```

- `json_path` is a folder and must end with `/`. The app writes `<app name>.json` there. Create the folder first.
- A table heater without `indoor_sensor_temp` needs `unit_of_measurement: C` (or `F`). Without a sensor and without that key the app cannot find the unit and stops with a WARNING.

---
## One app per device

Each climate entity gets its own app: top-level `climate:` (or `heater:`) and `type:`.

| `type` | Use for | What the app controls |
|---|---|---|
| `heater` (default) | floor heating, panel heaters, anything that only heats | the setpoint (and on/off on vacation with `vacation_keep_off`) |
| `hvac` | heat pumps, air conditioners | the setpoint, the modes `heat`, `cool` and `fan_only`, the fan mode `Silence` and the `boost` preset |

An unknown `type` logs a WARNING and the app uses `heater`.

Why one app per device: a change to one device's YAML reloads only that device, an error stops only that device, each app has its own small JSON file, and ElectricalManagement controls each device by app name. Every app listens to the `WEATHER_CHANGE` event itself.

**Old style (deprecated)**: `HVAC:` and `Heaters:` lists of devices in one app still work and log a deprecation WARNING. In that style:
- the device keys go into each list entry, and the list decides the type (`HVAC:` entries are `hvac`, `Heaters:` entries are `heater`);
- these keys can also be given once at the app level and are then inherited by every device that does not set them itself: `vacation_temp`, `max_vacation_temp`, `screening_temp`, `getting_cold`, `getting_hot_offset`, `rain_level`, `anemometer_speed`, `spend_temp_offset`, `notify_receiver`, `cloud_cover`;
- a device can set its own `vacation` entity; without it the app-level `vacation` is used.

---
## How the setpoint is found

There are two target modes. The mode is chosen from the keys you use; set `target_mode: indoor` or `target_mode: table` to choose it yourself.

| Mode | Chosen when | Used for | Runs every |
|---|---|---|---|
| `indoor` | no `temperatures`, `target_heater_temp` or `target_heater_input` | HVAC, heaters without a table | 12 minutes |
| `table` | `temperatures`, `target_heater_temp` or `target_heater_input` is set | floor heating, panel heaters | 15 minutes |

The first run is between 10 seconds and one interval after the app starts (spread per entity). Between the periodic runs a device also runs:
- 1 second after a window sensor has been `on` for 2 minutes,
- 60 seconds after all window sensors are `off`,
- 5 seconds after the `vacation` entity, `target_indoor_input` or `target_heater_input` changes,
- at once when ElectricalManagement changes the power state.

While the `automate` entity is `off` a device does nothing.

All the fixed steps below (0.3, 0.5, 1, 3, 4, 8 and so on) are in the unit of your sensors. They are not converted for Fahrenheit.

### Indoor mode: keep a fixed indoor temperature

The target is `target_indoor_temp`, or the value of `target_indoor_input` (an `input_number`) when that is set. The app moves the climate's setpoint step by step so that the indoor sensor reaches the target. Setpoints are rounded to 0.5.

**Indoor temperature.** The `indoor_sensor_temp` is used. When it is unavailable or has not reported for an hour, `backup_indoor_sensor_temp` is used. Without a backup the last value is used even if it is old. Without any value, nothing is done in that run.

**Adjustments to the indoor value before the calculation** (the setpoint is calculated as if the room had this temperature):
- `daytime_increasing` active: the room counts as 0.5 colder, so the room is held 0.5 warmer.
- `daytime_savings` active: the room counts as 0.5 warmer, so the room is held 0.5 cooler. While daytime savings is active, rain and wind are not used.
- Otherwise, wind at or above `anemometer_speed` in the last 30 minutes, or (without wind) rain at or above `rain_level`: the room counts as 0.3 colder.

**Window sensor temperature.** With `window_sensor_temp` (for example a sensor in a sunny window): when it is above `target_indoor_temp + window_offset`, the setpoint is lowered by the difference.

**The step rules.** The adjustment is the target minus the room temperature, plus the window part above.
- A small negative adjustment (between 0 and -0.3) becomes -0.5 when the setpoint has not changed for 30 minutes.
- A small positive adjustment (between 0 and 0.4) is ignored for 30 minutes after the last setpoint change.
- Room temperature falling since the last run:
  - if the room is above the target and, at the current rate, reaches the target within 6 runs, the setpoint is kept;
  - if the setpoint is 4 or more below the target, the room is below the target and the setpoint has not changed for 4 hours, the new setpoint is the setpoint plus the adjustment minus 1;
  - otherwise the adjustment is added, but only if the windows were closed more than 1 hour ago or the room is more than 1 below the target. The result is not set below a valid learned setpoint.
- Room temperature rising since the last run:
  - if the room is below the target and, at the current rate, reaches the target within 6 runs, the setpoint is kept;
  - otherwise the adjustment is added. The result is not set above a valid learned setpoint.
- Room temperature unchanged: when the room is 0.3 or more away from the target and the setpoint has not changed for 30 minutes, the adjustment is added.
- Limits: the setpoint stays within 4 of a valid learned setpoint. Without a valid learned setpoint it stays within 8 of the target.

**Learning (needs `json_path`).** When the setpoint has not changed for 2 hours (and at most once every 2 hours), the setpoint is stored as a running average for the current outdoor temperature (2-degree steps) and outdoor lux (5000-lux steps). A learned setpoint counts as valid when it exists for the current outdoor temperature and lux step and has at least 10 samples. After 100 samples the count is set back to 10, so recent samples weigh more. Learning happens only in normal operation: not with open windows, not on vacation, not during `daytime_savings` or `daytime_increasing`, not while ElectricalManagement sends `save`, `spend` or `reduce`, and not while an HVAC unit cools. Without `json_path` nothing is learned and the current setpoint is the reference.

**Open windows** (any `windowsensors` entity `on` for 2 minutes): the setpoint becomes `vacation_temp`. HVAC switches to `fan_only` instead, see [HVAC modes](#hvac-modes-fan-and-preset).

**Vacation** (`vacation` entity `on`): the setpoint becomes `vacation_temp` (default 16 C / 61 F).

**Power states from ElectricalManagement**: see [ElectricalManagement](#electricalmanagement). Open windows and vacation win over the power states.

### Table mode: follow the outdoor temperature

The table needs the outdoor temperature. After a start the device waits for the first outdoor temperature from the Weather app (or the deprecated `outside_temperature` sensor). A heater that is `off`, `unavailable` or `unknown` is left alone.

**Base setpoint.** The table entry for the outdoor temperature is the last entry whose `out` is at or below the outdoor temperature (an entry applies from its `out` value and up). Below the first entry, the first entry is used. The table is sorted by `out` for you.
- Entry with `offset`: base = `target_heater_temp` (or the value of `target_heater_input`) plus `offset`.
- Entry with `normal` (and no `offset`): base = `normal`.
- No table at all: base = `target_heater_temp`.

Without `target_heater_temp` and `target_heater_input`, the indoor target is used as the heater target: `target_indoor_temp`, or `target_indoor_input` as the heater input (as in ElectricalManagement).

**Corrections, in this order:**
1. Rain at or above `rain_level`, or wind at or above `anemometer_speed` right now: +1.
2. `window_sensor_temp` above `target_indoor_temp + window_offset`: lower by the difference.
3. Indoor temperature above `target_indoor_temp`: lower by the difference as well. Window and indoor together lower the setpoint by at most 3 degrees, also when only the window sensor is above its limit (a sunny window can no longer drop the heater to its floor). The indoor temperature is `indoor_sensor_temp` (with the backup rules above), or the climate's own `current_temperature` attribute when there is no indoor sensor.
4. Vacation: the setpoint becomes the vacation temperature (see below).
5. ElectricalManagement `save`, or `daytime_savings` active: the save temperature (see below).
6. ElectricalManagement `spend`: + `spend_temp_offset` (default 1). This is added also on vacation and during daytime savings.
7. Never below the vacation temperature, and never below the climate's `min_temp` attribute (5 if the climate has none).

**Save temperature**, the first rule that applies:
1. `save_temp_offset` is set: the setpoint plus `save_temp_offset`.
2. `save_temp` is set: `save_temp` plus the entry's `offset`, or the setpoint if that is lower.
3. The entry has `save`: the entry's `save`, or the setpoint if that is lower.
4. Otherwise: 10.

**Vacation temperature**, the first rule that applies (calculated from the base setpoint):
1. `vacation_temp` is set: `vacation_temp` plus the entry's `offset`, or the base setpoint if that is lower.
2. The entry has `vacation`: the entry's `vacation`, or the base setpoint if that is lower.
3. Otherwise: 10.

**Open windows and ElectricalManagement `reduce`**: the setpoint becomes the lowest of the save temperature and the vacation temperature (both calculated from the base setpoint), because that stops the heater drawing power fastest. It is sent only while the climate is in `heat` or `cool` and its setpoint is above the save temperature. If the climate has no setpoint, 10 is sent.

**Vacation with `vacation_keep_off: true`** (or `options: [vacation_keep_off]`): the heater is turned off instead (up to 4 attempts per vacation). When the `vacation` entity turns `off` and the heater is `off`, the app turns it on in `heat` mode. This happens in table mode also without `vacation_keep_off`.

Not used in table mode: learning, the step rules and `daytime_increasing`.

HVAC can use table mode too. Then the heat setpoint comes from the table, while cooling always uses the indoor rules.

A table can use offsets or absolute temperatures per entry:
```yaml
  temperatures:
    - out: -4
      normal: 20
      save: 13
      vacation: 14
    - out: 6
      normal: 18
      save: 13
      vacation: 14
```

### Periods: daytime savings, daytime increasing, silence

Each period is a list entry with `start`, `stop` and optionally `presence` (a list of `person` entities). Write the times in quotes (`'10:00:00'`); without quotes YAML can read them as a number, which fails validation.

| Key | Active when | Effect |
|---|---|---|
| `daytime_savings` | between `start` and `stop`, and nobody in `presence` is `home`. An entry with only `presence` (no times) is active whenever nobody in it is `home`. | Indoor mode: room held 0.5 cooler when heating, 0.5 warmer when cooling. Table mode: the save temperature. |
| `daytime_increasing` | between `start` and `stop`, and (with `presence`) someone in `presence` is `home` | Indoor mode: room held 0.5 warmer when heating; 0.5 cooler when cooling. Ignored in table mode. During the hour after `stop`, HVAC does not switch from heat to `fan_only`. |
| `silence` (HVAC) | between `start` and `stop`, not on vacation, and (with `presence`) someone in `presence` is `home`. Only the first period that matches the time is checked. | Fan mode `Silence`, see [HVAC modes](#hvac-modes-fan-and-preset). |

---
## HVAC modes, fan and preset

An HVAC device runs every 12 minutes (15 in table mode) and acts on the climate's current mode. It never turns an HVAC unit on from `off` (except in table mode when vacation ends, as for heaters). In a mode other than `heat`, `cool`, `fan_only` or `off` it logs a WARNING and does nothing. Without an indoor temperature it logs an INFO message and does nothing.

"Room" below is the indoor temperature after the daytime, rain and wind adjustments described above, except in `fan_only`, where the sensor value is used as it is.

**In `heat`:**
- Windows open: switch to `fan_only`.
- Vacation: setpoint `vacation_temp`. When the room is above `target_indoor_temp - 3` and above `vacation_temp + 3`: switch to `fan_only`.
- Otherwise the setpoint comes from the indoor rules (or the table), and:
  - wind at or above `anemometer_speed` in the last 25 minutes and the room more than 0.7 below the target: preset `boost`, if the climate offers it and the fan mode is not `Silence`. When that is no longer the case, a `boost` preset is set back to `none`;
  - room more than 0.6 above the target, no wind in the last 45 minutes, and not in the hour after a `daytime_increasing` period: switch to `fan_only`.

**In `cool`:**
- Windows open: switch to `fan_only`.
- Switch to `fan_only` when the room is less than 1 above the target, or, between 30 minutes before sunset and sunrise while it rains, when the room is less than 2 above the target.
- Vacation: switch to `fan_only` when the room is below `max_vacation_temp - 2`.
- Otherwise the setpoint follows the indoor rules (also in table mode). Cooling setpoints are not learned. The ElectricalManagement offsets change sign (save: +0.3 by default, spend: -1 by default).
- Rain and wind work in the same direction as in heating: the room counts as 0.3 colder, so it cools less.

**In `fan_only`** (not on vacation, windows closed):
- Switch to `cool` when the room is more than 2 above the target, the outdoor temperature is above `target_indoor_temp - 4`, and either it is between sunrise and 30 minutes before sunset without rain, or the room is more than 3 above the target.
- Switch to `heat` when the room is at or below the target. 10 seconds later the setpoint is set to the learned setpoint minus 1 (without learned data: the current setpoint minus 1, or the target minus 1 if the setpoint is more than 6 away from the target).

**In `fan_only` on vacation** (windows closed):
- Switch to `heat` when the room is at or below `target_indoor_temp - 5` or at or below `vacation_temp + 2`.
- Switch to `cool` when the room is above `max_vacation_temp` (default 30 C / 86 F).

**Fan mode `Silence`**: used only if the climate lists `Silence` in its `fan_modes`. In a `silence` period the fan mode is set to `Silence`; the previous fan mode is stored (in the JSON file when `json_path` is set) and set back when the period ends. If no previous fan mode is known, `Auto` is used. The fan mode is checked in every run, whatever the HVAC mode.

---
## Screens and covers

Each device can control a list of covers under `screening`. The covers need a `current_position` attribute (100 = open).

**Closing.** A device tries to close its screens in its own run when the room is warm:

| Device and situation | Tries to close when |
|---|---|
| Indoor mode heater, or HVAC in `heat`, normal operation | room more than 0.2 above the target, no wind at or above `anemometer_speed` in the last 45 minutes, outdoor temperature above `screening_temp` |
| Indoor mode heater, vacation | room above the target and outdoor temperature above `screening_temp` |
| HVAC on vacation in `heat` | when it switches to `fan_only` (see above) and outdoor temperature above `screening_temp` |
| HVAC in `cool` | every run |
| HVAC in `fan_only` | every run with outdoor temperature above `screening_temp` |
| Table mode heater | never (screens only open) |

A close attempt closes a cover only when all of these are true:
- no rain (rain is 0) and wind below `anemometer_speed`,
- none of the screen's own `windowsensors` is `on`,
- nobody in `not_when_home` is `home`,
- outdoor lux at or above `lux_close`,
- cloud cover below `cloud_cover` (default 90 %),
- the cover is fully open (position 100) and has not been moved by hand since the app last moved it.

If the attempt fails only because of lux or cloud cover, the screen waits: on the next `WEATHER_CHANGE` event with lux at or above `lux_close` and cloud cover below `cloud_cover`, it tries again. An HVAC run where the room is no longer warm stops the waiting.

**`cloud_cover`** is a number (percent). The cloud cover comes from the Weather app's `WEATHER_CHANGE` event. If the Weather app does not send it, the value is 0 and this check always passes. Set it per app (one-device apps), per list entry, or once at the app level in the deprecated list style, where every device inherits it. It applies to all screens of the device; it cannot be set per screen.

**Opening.** On every `WEATHER_CHANGE` event, on every change of the outdoor lux, and when a deprecated `rain_sensor` reports rain, each screen opens when any of these is true:
- it rains (any rain above 0; `rain_level` is not used for screens),
- wind is above `anemometer_speed`,
- outdoor lux is below `lux_open`, or below `lux_open_media` while one of the `mediaplayers` has the state `on`.

A screen is not opened while one of its own `windowsensors` is `on`. A cover that was moved by hand after the app closed it is opened only when the outdoor lux is 400 or less.

---
## Weather integration

ClimateCommander needs the [ad-Weather](https://github.com/Pythm/ad-Weather) app. It collects your weather sensors in one app and sends the `WEATHER_CHANGE` event with `temp`, `rain`, `wind`, `lux` and `cloud_cover`. Values it does not have are sent as `None` and ignored. The app name is `weather` unless you set `weather_app`. At startup ClimateCommander uses the last values the Weather app has sent, so it does not wait for the next change.

Outdoor values are kept per ClimateCommander app. Until the first outdoor temperature arrives, indoor mode uses 10 and table mode waits.

**Deprecated own sensors**: `outside_temperature`, `rain_sensor` and `anemometer` (read in `HASS_namespace`) still work and log a WARNING at startup. While such a sensor has sent a new value in the last 20 minutes, the same value from the Weather app is ignored. Configure them in the Weather app instead.

**Own outdoor lux sensors** (`OutLux_sensor`, `OutLuxMQTT`, `OutLux_sensor_2`, `OutLuxMQTT_2`), for screen control:
- `OutLux_sensor` and `OutLux_sensor_2` are Home Assistant entities (read in `HASS_namespace`); `OutLuxMQTT` and `OutLuxMQTT_2` are MQTT topics (in `MQTT_namespace`). An MQTT payload can be a number, or JSON with `illuminance` (Zigbee) or `value` (Z-Wave).
- With two sensors the highest of the values received in the last 15 minutes is used.
- When none of them has sent a new value for 20 minutes, or none is configured, the lux from the Weather app is used.
- The logic is in `weather_data.py`, the same file as in Lightwand.

---
## ElectricalManagement

[ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement) 1.0.6 or later can control a ClimateCommander device. List the app under `climate_commander:` in ElectricalManagement, together with the energy keys. Nothing is needed in this app. Example, in the ElectricalManagement configuration:

```yaml
  climate_commander:
    - app: heater_bathroom
      power: 2000
      consumptionSensor: sensor.bathroom_floor_power
      kWhconsumptionSensor: sensor.bathroom_floor_energy
      pricedrop: 0.2
      max_continuous_hours: 4
```

`app` is the name of the ClimateCommander app, `power` the heater's rated power in watts. ElectricalManagement then sends one of these states:

| State | Table mode heater | Indoor mode (HVAC, heaters without a table) |
|---|---|---|
| `normal` | table setpoint | follows the indoor sensor |
| `save` (high price) | the save temperature | setpoint + `save_temp_offset` (default -0.3; cooling: +0.3) |
| `spend` (low price, solar) | table setpoint + `spend_temp_offset` (default 1) | setpoint + `spend_temp_offset` (cooling: -1) |
| `reduce` (over the kWh goal) | lowest of the save and vacation temperature | same as `save` |

- A new state is applied at once.
- In indoor mode the setpoint is held at its value from before the state plus the offset, so the regulation does not work against the saving. If you change the setpoint by hand during a state, that becomes the new value to hold. Nothing is learned during a state. When the state ends, regulation continues from the held setpoint.
- In indoor mode, vacation and open windows win over the states.
- A state is valid for the time ElectricalManagement gives with it (45 minutes by default). Without a new call in that time the device returns to `normal` by itself and logs an INFO message.
- The keys of an ElectricalManagement `climate:` entry work here too: `heater` (same as `climate`), `window_temp` (same as `window_sensor_temp`), `recipient` (same as `notify_receiver`) and `options: [vacation_keep_off]` (same as `vacation_keep_off: true`), and the table keys (`temperatures`, `target_heater_temp`, `save_temp`, ...). The energy keys (`consumptionSensor`, `pricedrop`, `max_continuous_hours`, ...) stay in ElectricalManagement.

For developers: ElectricalManagement uses `get_device(climate=None)` on the app, and on the device `set_power_state(state, valid_minutes, source)`, `power_state`, `in_daytime_saving()`, `heater`, `namespace`, `vacation_entity` and `daytime_savings`.

---
## Configuration reference

Types: *string*, *number* (integer or decimal), *bool* (`true` / `false`), *list*. "Entity" means an entity id string. Where a list is expected, a single value is accepted as a one-item list. Defaults marked C / F depend on the unit.

### App-level keys

In a one-device app all keys (app-level and device keys) go at the top level of the app.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `module` | string | yes | | Always `climateCommander`. |
| `class` | string | yes | | Always `Climate`. |
| `HASS_namespace` | string | no | `default` | Home Assistant namespace. Also the default for each device's `namespace`. |
| `MQTT_namespace` | string | no | `mqtt` | MQTT namespace, used for `OutLuxMQTT` and `OutLuxMQTT_2`. |
| `weather_app` | string | no | `weather` | Name of the ad-Weather app. An ERROR is logged if it is not found. |
| `unit_of_measurement` | string | no | from sensors | `C` or `F`. Without it the unit is read from `outside_temperature`, then from the devices' `indoor_sensor_temp`. If no unit is found the app stops. |
| `json_path` | string | no | none | Folder for `<app name>.json` (learned setpoints, HVAC fan mode). Must end with `/`. Without it nothing is learned. |
| `vacation` | entity | no | `input_boolean.vacation` if it exists | `input_boolean` that turns on vacation. |
| `notify_app` | string | no | none | Name of your own notification app (it needs a `send_notification` method). Without it the Home Assistant `notify` service is used. |
| `notify_receiver` | list | no | none | Receivers of the window notifications, for example `mobile_app_your_phone`. One value or a list. Alias `recipient`. |
| `OutLux_sensor` | entity | no | none | Own outdoor lux sensor. |
| `OutLuxMQTT` | string | no | none | MQTT topic of an own outdoor lux sensor. |
| `OutLux_sensor_2` | entity | no | none | Second own outdoor lux sensor. |
| `OutLuxMQTT_2` | string | no | none | MQTT topic of a second own outdoor lux sensor. |
| `outside_temperature` | entity | no | none | Deprecated, configure it in ad-Weather. Outdoor temperature sensor. |
| `rain_sensor` | entity | no | none | Deprecated, configure it in ad-Weather. Rain sensor. |
| `anemometer` | entity | no | none | Deprecated, configure it in ad-Weather. Wind speed sensor. |
| `away_state` | entity | no | none | Deprecated, use `vacation`. Logs a WARNING. |
| `HVAC` | list | no | empty | Deprecated list style: device entries of type `hvac`. |
| `Heaters` | list | no | empty | Deprecated list style: device entries of type `heater`. |

### Device keys

In a one-device app these go at the top level. In the deprecated list style they go into each entry of `HVAC:` / `Heaters:`; keys marked "inherit" can also be set once at the app level.

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `climate` | entity | yes | | The `climate` entity to control. `heater` works as an alias. |
| `type` | string | no | `heater` | `heater` or `hvac`. Ignored in the list style (the list decides). |
| `namespace` | string | no | `HASS_namespace` | Namespace of all this device's entities. |
| `target_mode` | string | no | from the keys | `indoor` or `table`, see [How the setpoint is found](#how-the-setpoint-is-found). |
| `indoor_sensor_temp` | entity | indoor mode | none | Indoor temperature sensor. |
| `backup_indoor_sensor_temp` | entity | no | none | Used when the indoor sensor is unavailable or has not reported for an hour. |
| `target_indoor_temp` | number | no | 22.7 C / 72.8 F | Indoor target. In table mode: the room temperature above which the setpoint is lowered. |
| `target_indoor_input` | entity | no | none | `input_number` for the indoor target. Overrides `target_indoor_temp`. |
| `temperatures` | list | no | empty | Table mode: the table entries, see [Table entry keys](#table-entry-keys). |
| `target_heater_temp` | number | no | `target_indoor_temp` | Table mode: heater setpoint at offset 0. |
| `target_heater_input` | entity | no | `target_indoor_input` if neither heater key is set | Table mode: `input_number` for the heater setpoint. |
| `save_temp_offset` | number | no | -0.3 in indoor mode, none in table mode | Indoor mode: added to the held setpoint in `save` and `reduce`. Table mode: rule 1 of the save temperature. |
| `save_temp` | number | no | none | Table mode: save temperature (the entry's `offset` is added). |
| `spend_temp_offset` | number | no | 1 | Added in `spend` (subtracted when cooling). Inherit. |
| `vacation` | entity | no | app-level `vacation` | Own vacation `input_boolean` for this device. |
| `vacation_temp` | number | no | indoor mode 16 C / 61 F; table mode none | Vacation setpoint. In table mode the entry's `offset` is added. Inherit. |
| `max_vacation_temp` | number | no | 30 C / 86 F | HVAC on vacation: cool above this. Inherit. |
| `vacation_keep_off` | bool | no | `false` | Table mode: turn the heater off on vacation. |
| `options` | list | no | empty | `[vacation_keep_off]` is the same as `vacation_keep_off: true` (ElectricalManagement style). |
| `window_sensor_temp` | entity | no | none | Temperature sensor in a window (sun). `window_temp` works as an alias. |
| `window_offset` | number | no | -3 | The window sensor lowers the setpoint when it is above `target_indoor_temp + window_offset`. |
| `windowsensors` | list | no | empty | Window / door `binary_sensor`s. `on` for 2 minutes counts as open. |
| `rain_level` | number | no | 3 | Rain at or above this raises the heating. Inherit. |
| `anemometer_speed` | number | no | 40 | Wind at or above this raises the heating and starts `boost`; screens open above it and do not close at or above it. Inherit. |
| `daytime_savings` | list | no | empty | Periods with a lower temperature, see [Periods](#periods-daytime-savings-daytime-increasing-silence). |
| `daytime_increasing` | list | no | empty | Indoor mode: periods with a higher temperature. |
| `silence` | list | no | empty | HVAC: periods with fan mode `Silence`. |
| `automate` | entity | no | none | `input_boolean`. While it is `off` the device does nothing. |
| `screening` | list | no | empty | Covers, see [Screening keys](#screening-keys). |
| `screening_temp` | number | no | 8 C / 47 F | Screens close only when the outdoor temperature is above this (not used in HVAC `cool`). Inherit. |
| `cloud_cover` | number | no | 90 | Screens close only while the cloud cover (percent, from the Weather app) is below this. Inherit. |
| `getting_cold` | number | no | 18 C / 65 F | "Window open" notification when outdoor and indoor are below this; "Window closed" needs outdoor above it. Inherit. |
| `getting_hot_offset` | number | no | 10 | "Window closed" notification when the room is this much above the target. Inherit. |
| `notify_receiver` | list | no | app-level value | Receivers of this device's notifications. Alias `recipient`. Inherit. |

### Screening keys

Each entry in `screening`:

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `screen` | entity | yes | | The `cover` entity. An entry without it is skipped with a WARNING. |
| `lux_close` | integer | no | 40000 | Close at or above this outdoor lux. |
| `lux_open` | integer | no | 15000 | Open below this outdoor lux. |
| `lux_open_media` | integer | no | 4000 | Open below this outdoor lux while a `mediaplayers` entity is `on`. |
| `windowsensors` | list | no | empty | Windows behind this screen. While one is `on`, the screen is neither closed nor opened. |
| `not_when_home` | list | no | empty | `person` entities. The screen does not close while one of them is `home`. |
| `mediaplayers` | list | no | empty | `media_player` or `switch` entities. Only the state `on` counts (not `playing`). |

The device keys `screening_temp`, `cloud_cover` and `anemometer_speed` also apply to the screens.

### Table entry keys

Each entry in `temperatures`:

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `out` | number | yes | | Outdoor temperature from which this entry applies (up to the next entry). |
| `offset` | number | no | none | Added to `target_heater_temp`. Also added to `save_temp` and `vacation_temp`. |
| `normal` | number | no | none | Absolute setpoint, used when the entry has no `offset`. |
| `save` | number | no | none | Absolute save temperature, used when neither `save_temp_offset` nor `save_temp` is set. |
| `vacation` | number | no | none | Absolute vacation temperature, used when `vacation_temp` is not set. |

Other keys in an entry are accepted and ignored. Only `out` is checked by the validation.

### Period keys

Each entry in `daytime_savings`, `daytime_increasing` and `silence`:

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `start` | string | yes (except `daytime_savings` with only `presence`) | | Start time, quoted, for example `'10:00:00'`. |
| `stop` | string | yes (same) | | Stop time, quoted. |
| `presence` | list | no | none | `person` entities, see [Periods](#periods-daytime-savings-daytime-increasing-silence). |

---
## Full example configurations

**HVAC with screens, periods and notifications:**

```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.living_room
  type: hvac
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  automate: input_boolean.automate_climate
  indoor_sensor_temp: sensor.living_room_temperature
  backup_indoor_sensor_temp: sensor.living_room_heat_pump_temperature
  target_indoor_temp: 22.7
  window_sensor_temp: sensor.living_room_window_temperature
  window_offset: -3
  vacation_temp: 16
  max_vacation_temp: 30
  screening_temp: 8
  cloud_cover: 80
  getting_cold: 18
  rain_level: 3
  anemometer_speed: 40
  notify_receiver:
    - mobile_app_your_phone
  windowsensors:
    - binary_sensor.living_room_window
  daytime_savings:
    - start: '10:00:00'
      stop: '14:00:00'
      presence:
        - person.resident_1
        - person.resident_2
  silence:
    - start: '21:00:00'
      stop: '07:00:00'
      presence:
        - person.resident_1
  screening:
    - screen: cover.living_room_screen
      lux_close: 40000
      lux_open: 15000
      lux_open_media: 4000
      not_when_home:
        - person.resident_2
      mediaplayers:
        - switch.projector
        - media_player.living_room_tv
      windowsensors:
        - binary_sensor.living_room_door
```

**Floor heater in table mode with save, vacation and an input for the heater target:**

```yaml
heater_bathroom:
  module: climateCommander
  class: Climate
  climate: climate.bathroom_floor
  type: heater
  vacation: input_boolean.vacation
  indoor_sensor_temp: sensor.bathroom_temperature
  target_indoor_temp: 24.2
  target_heater_input: input_number.bathroom_floor_target
  target_heater_temp: 28
  save_temp_offset: -0.5
  vacation_temp: 13
  vacation_keep_off: false
  windowsensors:
    - binary_sensor.bathroom_window
  daytime_savings:
    - start: '09:00:00'
      stop: '15:00:00'
  temperatures:
    - out: -6
      offset: 3
    - out: 3
      offset: 1
    - out: 7
      offset: 0
    - out: 15
      offset: -3
```

**Deprecated list style with app-level keys inherited by every device:**

```yaml
climate_control:
  module: climateCommander
  class: Climate
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  vacation_temp: 16
  cloud_cover: 85
  notify_receiver:
    - mobile_app_your_phone
  HVAC:
    - climate: climate.living_room
      indoor_sensor_temp: sensor.living_room_temperature
      target_indoor_temp: 22.5
      screening:
        - screen: cover.living_room_screen
  Heaters:
    - climate: climate.office_panel_heater
      indoor_sensor_temp: sensor.office_temperature
      target_indoor_temp: 21
      cloud_cover: 70
```

---
## Validation

The configuration is checked with pydantic models (`AppConfig` and `DeviceConfig` in `cc_config.py`) when the app starts.

- A value of the wrong type logs an ERROR naming the key, for example:  
  `climate.bathroom_floor: invalid configuration, device skipped: window_offset: Input should be a valid number, unable to parse string as a number`  
  A table entry without `out` gives `temperatures.0.out: Field required`.
- An invalid **device** key skips that device. In a one-device app the app then runs without a device.
- An invalid **app-level** key (`HASS_namespace`, `MQTT_namespace`, `weather_app`, `json_path`, `vacation`, the lux and deprecated sensor keys, `HVAC`, `Heaters`, ...) stops the app: `Invalid configuration for <app>, app not started: ...`.
- In the list style, an inherited app-level key (`vacation_temp`, `cloud_cover`, ...) is checked per device, so a wrong value skips every device that inherits it.
- A single value where a list is expected is accepted as a one-item list (`windowsensors`, `temperatures`, `daytime_savings`, `daytime_increasing`, `silence`, `screening`, `options`, `presence`, and the screen keys `windowsensors`, `not_when_home`, `mediaplayers`). This includes `notify_receiver`.
- Unknown keys are ignored without a message. Check the spelling of a key that seems to have no effect.
- Table entries: only `out` is checked; `offset`, `normal`, `save` and `vacation` are used as written.
- `automate` must be an entity id; `true` / `false` are accepted but ignored.
- A `target_mode` other than `indoor` or `table` is ignored, and the mode is chosen from the keys.

---
## Notifications

- Window notifications go to `notify_receiver` (or `recipient`).
- Without `notify_app` the Home Assistant service `notify/<receiver>` is called for each receiver, with a title and a message. Without receivers nothing is sent.
- With `notify_app` your app's `send_notification(message=..., message_title=..., message_recipient=..., also_if_not_home=False)` is called. If the app is not found, a WARNING is logged and no notifications are sent.
- **With the `notify_mobiles` app from [Pythm_AppdaemonApps](https://github.com/Pythm/Pythm_AppdaemonApps)** set `notify_app: notify_mobiles` (the name of your `notify_mobiles` app in `apps.yaml`). Then `notify_receiver` is a list of the **person labels** you defined under `recipients` in `notify_mobiles` (for example `[Me]`), not phone service names such as `mobile_app_...`. A label that does not exist there receives nothing. Without `notify_receiver`, nobody is notified. A recipient that has a `tracker` only receives while the tracker is `home`, because ClimateCommander sends with `also_if_not_home=False`.
- **"Window open"**: sent once after windows are opened, the first time both the outdoor and the indoor temperature are below `getting_cold`. Message: `Window near <name> is open and inside temperature is <temp>°`.
- **"Window closed"**: sent once after all windows are closed, the first time the room is at or above the target plus `getting_hot_offset` (default 10) and the outdoor temperature is above `getting_cold`. Message: `No Window near <name> is open and it is getting hot inside! <temp>°`.
- The conditions are checked in each device run.

---
## Troubleshooting

Messages in the AppDaemon log, and what they mean:

| Level | Message | Meaning and fix |
|---|---|---|
| ERROR | `Invalid configuration for <app>, app not started: <key>: <problem>` | An app-level key has the wrong type. Fix the named key. |
| ERROR | `<climate>: invalid configuration, device skipped: <key>: <problem>` | A device key has the wrong type. Fix the named key. |
| ERROR | `Weather app 'weather' not found. ClimateCommander needs https://github.com/Pythm/ad-Weather ...` | Install ad-Weather, or set `weather_app` to its app name. Without it table mode never starts (unless the deprecated `outside_temperature` is set) and screens get no lux from it. |
| WARNING | `Could not find unit of measurement. Please configure 'unit_of_measurement' with C or F. Aborting <app> setup` | No `unit_of_measurement`, and no `indoor_sensor_temp` (or `outside_temperature`) with a unit. Set `unit_of_measurement`. The app does nothing until fixed. |
| WARNING | `Configuring several devices with 'HVAC:' / 'Heaters:' lists in one app is deprecated. ...` | Still works. Move to one app per device when convenient. |
| WARNING | `'outside_temperature' is deprecated. ...` (also `rain_sensor`, `anemometer`) | Still works. Configure the sensor in ad-Weather and remove it here. |
| WARNING | `Please change 'away_state' to 'vacation' in your configuration. ...` | Rename the key. |
| WARNING | `Unknown type '<type>' for <climate>. Use 'heater' or 'hvac'. Using 'heater'.` | Fix `type`. |
| WARNING | `Skipping device without 'climate': {...}` | A list entry has neither `climate` nor `heater`. |
| WARNING | `Notify app '<name>' not found. No notifications.` | Check `notify_app` against the name of your notification app. |
| WARNING | `<climate>: window sensor <entity> has no last_changed. Does it exist?` | The window sensor id is wrong or the entity is missing. |
| WARNING | `<climate>: screening entry without 'screen' is skipped: {...}` | Add `screen:` to the entry. |
| WARNING | `<climate>: the entity does not exist in namespace '<ns>'. Nothing is controlled until it exists. Check 'climate'.` | The `climate` id is wrong or the integration is gone. Logged once, not every run. |
| INFO | `<climate>: the entity is unavailable. Waiting for it to come back.` / `... is back (<state>).` | Logged once per outage. |
| WARNING | `Could not read <path>: <error>. Starting with empty data.` | The JSON file is damaged or unreadable. Learning starts again. |
| WARNING | `Could not write <path>: <error>` | The `json_path` folder does not exist, is not writable, or the path does not end with `/`. The old file is left untouched. |
| WARNING | `Unregistered AC state: <state>. <name>. Will not automate` | The HVAC unit is in a mode such as `auto`, `dry` or `heat_cool`. The app only handles `heat`, `cool` and `fan_only`. Logged in every run. |
| WARNING | `<climate>: unknown power state '<state>' from <source>.` | ElectricalManagement sent a state other than `normal`, `save`, `spend` or `reduce`. Check the versions. |
| INFO | `<climate>: indoor temp is None. Aborting setting new temperature` | HVAC without a usable indoor temperature. Check `indoor_sensor_temp` and the backup. |
| INFO | `<climate>: power state '<state>' expired, back to normal.` | ElectricalManagement did not renew the state in time. |
| INFO | `<input> is not a valid number. Using <value>.` | `target_indoor_input` has no number at startup; `target_indoor_temp` is used until it has. |
| INFO | `Learned data for <climate> copied from <file>.` | First start of a one-device app: learned data was taken from the old app's JSON file. |
| DEBUG | `<climate>: waiting for the outdoor temperature from the Weather app.` | Table mode has no outdoor temperature yet. |

**Nothing happens, and nothing is logged:**
- The `automate` entity is `off`.
- A heater in indoor mode has no indoor temperature, or the climate has no `temperature` attribute.
- A table heater, or an HVAC unit, is `off`. The app does not turn it on (except a table heater when vacation ends).
- A cover has been moved by hand, or has no `current_position`.

---
## Notes on deliberate behaviour

These are intended. Please do not report them as bugs.

- Indoor mode: small drops are rounded to -0.5 after 30 minutes, small rises wait 30 minutes (deliberate asymmetry).
- Indoor mode: the extra -1 when the setpoint has been 4 or more below the target for 4 hours.
- Cooling setpoints are not learned (they would mix with the heating data).
- Learned setpoints are only updated in normal operation (not on vacation, with open windows, during daytime savings / increasing, or in an ElectricalManagement state).
- Table mode: open windows and `reduce` use the **lowest** of the save and vacation temperature.
- Table mode: an entry applies from its `out` value and up.
- In cooling, rain and wind make the app cool less (same direction as in heating).
- With two own lux sensors the highest fresh value is used. In the Weather app the lowest temperature and the highest lux are used.
- `input_boolean.vacation` is the default vacation switch only if it exists (a convention shared with ElectricalManagement and ModeManagement).
- Fixed values in the rules (steps of 0.3 / 0.5 / 1, the fallback 10, `getting_hot_offset` 10, 400 lux) are not converted for Fahrenheit; only the defaults in the tables above are.
- In table mode without an indoor sensor, the climate's own `current_temperature` is compared with `target_indoor_temp`. For a floor heater this can be the floor temperature: set `indoor_sensor_temp`, or a `target_indoor_temp` that suits that sensor.

---
## 📌 License  

[MIT License](https://github.com/Pythm/ad-ClimateCommander/blob/main/LICENSE)

---
## 🙋 Contributing  

- Found a bug? Open an issue or submit a PR!  
- Want to add a feature? Discuss in the [GitHub Discussions](https://github.com/Pythm/ad-ClimateCommander/discussions).

---
**Climate Commander by [Pythm](https://github.com/Pythm)**  
[GitHub](https://github.com/Pythm/ad-ClimateCommander)
