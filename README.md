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
- [Adaptive controller (optional)](#adaptive-controller-optional)
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
- **The app now has three more files**, `cc_devices.py`, `cc_config.py` and `cc_control.py`, next to `climateCommander.py` and `weather_data.py`. Copy the whole app folder when you update (HACS does).
- **New, optional: `controller: shadow` / `controller: adaptive`**, a different way to find the setpoint, per device. The default `classic` is the controller described in this README. See [Adaptive controller](#adaptive-controller-optional).
- **The configuration is validated with pydantic** (`cc_config.py`). A value of the wrong type (for example `window_offset: abc`, or a table entry without `out`) now logs an ERROR naming the key: an invalid device is skipped, an invalid app-level key stops that app. Before, such a value was silently used or crashed later (`vacation_temp: abc` used to fall back to 10). A single entity where a list is expected (for example `windowsensors: binary_sensor.x`) is accepted as a one-item list. See [Validation](#validation).
- **New key `cloud_cover`** (default `90`): screens close only while the cloud cover from the Weather app is below this percentage. Before, the limit was fixed at 90, so the default keeps the old behaviour. See [Screens and covers](#screens-and-covers).
- Behaviour changes in the indoor-target mode (HVAC, and heaters without a table):
  - The learned setpoints are no longer updated on vacation, with open windows, during `daytime_savings` / `daytime_increasing` or while ElectricalManagement sends `save`, `spend` or `reduce`. Before, vacation temperatures were learned and pulled the setpoint down after a vacation.
  - A room that stays 0.3 degrees or more away from the target is now corrected when the setpoint has not changed for 30 minutes. Before, nothing happened while the temperature did not move.
  - `backup_indoor_sensor_temp` is used only when the indoor sensor is unavailable or has not **reported** for an hour (`last_reported`). Before, a steady temperature for an hour (no change) counted as a dead sensor.
  - The "reaches the target within 6 runs" hold is replaced by a trend over the sensor's reports of the last hour: the setpoint is kept while the room moves towards the target and reaches it within `trend_hold_hours` (default 2). Before, the change since the last run (12 minutes, often 0 or one 0.1 step) was used.
  - A learned setpoint is approached by at most 0.5 per run (or the normal adjustment, if larger). Before, the setpoint jumped to it (for example from 21.0 to 25.0 in one run).
  - HVAC: nothing is learned within 2 hours of a mode change or while the unit reports an `hvac_action` other than `heating` / `idle`, so heat / `fan_only` cycling no longer teaches too high setpoints.
  - HVAC: the setpoint in heat is never set below `target_indoor_temp - max_setpoint_drop` (default 3 C / 5.4 F). Back from `fan_only`, the setpoint starts from the last one held in heat, at most the learned setpoint minus 1 (before: always the learned setpoint minus 1). See [Heat pump cycling](#heat-pump-cycling).
- **Upgrading, HVAC in heat (indoor mode):** without `has_fireplace` an HVAC now lowers its setpoint before it switches to `fan_only` (it switches at once only when the setpoint is at its lowest or the room still rises). A room with a fireplace, or another heat source that makes the room overshoot fast, sets `options: [has_fireplace]` to keep the old behaviour (`fan_only` at once at target + 0.6).
- **New, optional keys** `interval_minutes` (time between runs, default 12 / 15 minutes), `trend_hold_hours` (default 2), `max_setpoint_drop` (HVAC, default 3 C / 5.4 F) and the option `has_fireplace`, see [Device keys](#device-keys).
- Window notifications are sent once per event: "Window open" (getting cold) once after windows are opened, "Window closed" (getting hot, `getting_hot_offset` above target) once after they are closed. Before, "Window open" was sent once per app start and "Window closed" never. HVAC now uses the same rule as heaters: "Window open" also needs the outdoor temperature to be below `getting_cold`.
- Outdoor values are kept per app, not shared between all ClimateCommander apps (they used to overwrite each other). At startup the last values sent by the Weather app are used.
- The periodic runs of the devices (every 12 minutes, 15 minutes in table mode, or `interval_minutes`) are spread over the interval, so many devices do not call Home Assistant in the same second.
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

#### Trying the adaptive controller (optional)
**Nothing to do for the default case**: without `controller`, every device keeps the classic controller and behaves exactly as before. To try the new one:
1. Add `controller: shadow` to one device (and optionally `diagnostic_sensor`). Classic stays in control; nothing else changes. Leave it for 1 to 2 weeks and read the [DEBUG line or the diagnostic sensor](#start-with-shadow).
2. Then change it to `controller: adaptive`.
3. To go back, set `controller: classic` (or remove the key). The learned setpoints of classic (`"data"` in the JSON file) are not touched by `shadow` or `adaptive`, and classic ignores the new `"adaptive"` entry, so you can switch back and forth.

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
- All five files of the app in the same folder: `climateCommander.py`, `cc_devices.py`, `cc_config.py`, `cc_control.py` and `weather_data.py`.
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

`interval_minutes` (1 to 120) sets another time between the runs for one device, in both modes.

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

**The room trend.** The app keeps the reports of the indoor sensor in use (value and report time, from `last_reported`) for 3 hours: every report that changes the value, and at every run the latest report, also while an HVAC is in `fan_only`. The trend is the least-squares slope of the reports of the last 60 minutes, in degrees per hour. With fewer than 3 reports, or reports spanning less than 20 minutes, the trend is unknown.

**The step rules.** The adjustment is the target minus the room temperature, plus the window part above.
- A small negative adjustment (between 0 and -0.3) becomes -0.5 when the setpoint has not changed for 30 minutes.
- A small positive adjustment (between 0 and 0.4) is ignored for 30 minutes after the last setpoint change.
- Trend hold: when the room moves towards the target and, at its trend, reaches it within `trend_hold_hours` (default 2), the setpoint is kept. Below the target this means the setpoint is not raised, above the target that it is not lowered.
  - Example: target 23.0, room 22.5, reports from 22.1 fifty minutes ago up to 22.5 now. That is 0.48 per hour, so the target is reached in 0.5 / 0.48 = 1.04 hours: the setpoint is kept. At 0.1 per hour it would take 5 hours, so the setpoint is raised by the rules below. The same applies 0.5 above the target with the room falling 0.6 per hour (0.83 hours): the setpoint is not lowered.
  - When the room moves towards the target more slowly than that (with an unknown trend: when it moved towards the target since the last run), or the trend moves away from the target, the learned setpoint does not count as valid in that run.
- Room temperature falling since the last run:
  - if the setpoint is 4 or more below the target, the room is below the target and the setpoint has not changed for 4 hours, the new setpoint is the setpoint plus the adjustment minus 1;
  - otherwise the adjustment is added, but only if the windows were closed more than 1 hour ago or the room is more than 1 below the target. The result is not set below a valid learned setpoint; when that learned setpoint is above the current setpoint, the setpoint moves up towards it by at most 0.5 per run (or by the adjustment, if that is larger). Example: setpoint 21.0, learned 25.3, room 0.2 below the target: 21.5, then 22.0 at the next run, and so on.
- Room temperature rising since the last run: the adjustment is added. The result is not set above a valid learned setpoint; when that learned setpoint is below the current setpoint, the setpoint moves down towards it by at most 0.5 per run.
- Room temperature unchanged: when the room is 0.3 or more away from the target and the setpoint has not changed for 30 minutes, the adjustment is added.
- Limits: the setpoint stays within 4 of a valid learned setpoint. Without a valid learned setpoint it stays within 8 of the target. HVAC in heat: never below `target_indoor_temp - max_setpoint_drop` (default 3 C / 5.4 F) or the climate's `min_temp`, see [Heat pump cycling](#heat-pump-cycling).

**Learning (needs `json_path`).** When the setpoint has not changed for 2 hours (and at most once every 2 hours), the setpoint is stored as a running average for the current outdoor temperature (2-degree steps) and outdoor lux (5000-lux steps). A learned setpoint counts as valid when it exists for the current outdoor temperature and lux step and has at least 10 samples. After 100 samples the count is set back to 10, so recent samples weigh more. Learning happens only in normal operation: not with open windows, not on vacation, not during `daytime_savings` or `daytime_increasing`, not while ElectricalManagement sends `save`, `spend` or `reduce`, and not while an HVAC unit cools. An HVAC also learns nothing within 2 hours of a mode change (heat, `fan_only`, cool, off; by the app or by hand), and nothing while the climate reports an `hvac_action` other than `heating` or `idle`. Without `json_path` nothing is learned and the current setpoint is the reference.

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
## Adaptive controller (optional)

Everything above describes the **classic** controller, the default. With `controller: adaptive` a device finds its setpoint in a different way; with `controller: shadow` it only calculates what the adaptive controller would do and changes nothing. The key is per device:

| `controller` | What happens |
|---|---|
| `classic` (default) | As described in [How the setpoint is found](#how-the-setpoint-is-found). Nothing new. |
| `shadow` | Classic stays in control and sends exactly what it sent before. The adaptive controller runs next to it on its own proposals, writes one DEBUG line per run and the optional `diagnostic_sensor`. Use it first. |
| `adaptive` | The adaptive controller sets the temperature (and for HVAC the mode heat / cool / fan_only and the `boost` preset). |

It works in indoor mode for heaters and HVAC, and in table mode for heaters. An HVAC in table mode keeps the classic controller (one INFO line).

### What it does

Every run (12 minutes, 15 in table mode, or `interval_minutes`, and on window, vacation and power-state changes) the setpoint is calculated again from these parts:
- **The target**: `target_indoor_temp` or `target_indoor_input`, or `target_table` (below). On top, the same shifts as classic, now applied to the target: +0.5 during `daytime_increasing`, -0.5 during `daytime_savings`, otherwise +0.3 with wind or rain.
- **A feed-forward from the climate's own sensor**: a heat pump regulates its own sensor (`current_temperature`), which often reads 2 to 5 degrees above the room. The controller learns that offset (see below) and, for HVAC, adds half of it, so the setpoint moves with it before the room drifts. Heaters do not use it. `outdoor_gain` adds a fixed extra per degree that the slow outdoor temperature is below the target.
- **A slow integral term**, which finds the setpoint that holds the room. It only runs while the room is near the target, outside a small band (room more than 0.1 above or 0.25 below the target, as the classic asymmetry), and it is frozen while a window is open or closed less than an hour ago, on vacation, in a power state, without a room sensor and (HVAC) while the sun term is large.
- **A proportional term** on the filtered room temperature.
- **Minus a sun term**: how far the window sensor reads above its learned level without sun. It rises at once and fades over about two hours (walls and floor keep the heat), times `solar_gain`, at most `solar_max`.

The result stays within the target ± 8 (an HVAC in heat at most `max_setpoint_drop` below the target, default 3 C / 5.4 F), the climate's `min_temp` and `max_temp` attributes, and never below the vacation temperature. It is rounded to 0.5 and sent **at most once per 12 minutes, at most 0.5 per change**. Window, vacation and the start or end of a power state are sent at once. A setpoint changed by hand (or by another app) is followed: the controller continues from it.

**Start and restart.** At the first run after a start or a reload the controller takes over the climate's current setpoint, so nothing jumps. If the room is then far from the target (more than about 1.4 degrees for HVAC, 3.9 for heaters) it does not wait: the setpoint is walked towards the calculated value, one 0.5 step per 12 minutes. A setpoint changed by hand is followed in the same way as in classic. When the HVAC mode changes, the `Silence` fan mode (if a `silence` period is active) is set in a separate command a few seconds later, because a mode and a fan mode sent together can lose one of them.

On top, unchanged from classic: open windows (heater: `vacation_temp`, HVAC: `fan_only`), vacation, and the power states from ElectricalManagement (the setpoint at the start of the state plus the same offsets). New: the **comfort guard** drops the power-state offset until the state changes when the room is `comfort_guard` below the target in `save` / `reduce`, or that much above it in `spend`.

**What it learns** (stored per climate under `"adaptive"` in the JSON file, see [JSON](#json-and-switching-back)):
- **The window level without sun**: window sensor minus room sensor, by outdoor temperature, with extra terms for wind and rain. It is learned only on quiet nights: room sensor fresh, windows closed for an hour, no vacation or power state, lux below 500 and no sun for an hour. It may be negative (a cold window) or positive (a sensor above a heater or in the air flow of a heat pump). Until enough is learned, `window_offset` (default -3 C / -5.4 F) or `window_offset_table` is used.
- **The offset of the climate's own sensor**: `current_temperature` minus the room, by the slow outdoor temperature, only in `heat` with a settled room.

**Without a room sensor** (unavailable, or not reported for an hour, and no usable backup): the room is estimated as the climate's own sensor minus the learned offset (or `internal_offset_table`). The integral is frozen, nothing is learned and an HVAC does not change mode on an estimate. Without any offset the controller runs open loop on the feed-forward. One WARNING when this starts, one INFO when the room sensor is back. Classic does nothing in indoor mode in that case.

**Table-mode heaters** keep the table: base from `temperatures`, +1 for rain or wind, then the sun term and "room above target" together lower it by at most 3, and save / spend / vacation / `min_temp` as classic. Differences: the room is the room sensor or the estimate above; **without either, the "room above target" part is left out** (classic uses the thermostat's `current_temperature`, often the floor, as the room and lowers the setpoint by up to 3). A small trim (at most ±2) moves the table value towards `target_indoor_temp` while a real room sensor reports.

### Start with shadow

1. Set `controller: shadow`, optionally `diagnostic_sensor: sensor.<room>_controller`, and leave it for **1 to 2 weeks**. A `json_path` keeps what it learns over restarts.
2. Read what it would do. Either set `log_level: DEBUG` for the app in `apps.yaml`; each run then logs one line such as  
   `climate.living_room: shadow heat src primary R 21.9 in_f 21.93 rate 0.1 out_slow 3.4 out_day 4.1 excess -0.3 base_w -2.5 solar 0.0 bias_int 2.1 target_eff 22.0 ff 1.05 i -0.32 p 0.07 sp_ctrl 22.3 send None hold 22.5 frozen None classic 22.0 heat`  
   or open the history of the diagnostic sensor in Home Assistant. Its state is the setpoint the controller would hold; `classic_setpoint` is what classic did.
3. Check that `src` is `primary` most of the time, that `base_w` (the learned window level) looks plausible for the room, and that `sp_ctrl` and `classic_setpoint` are not far apart for long.
4. Then set `controller: adaptive`.

**Heaters:** adaptive works for heaters too, but the heater constants were only tuned in a simulation with guessed floor-heating physics. Always run a heater in `shadow` first.

| Diagnostic attribute | Meaning |
|---|---|
| state | Setpoint the controller holds (`shadow`: would hold) |
| `src`, `R` | Room value used: `primary`, `backup` (with a learned offset), `estimate` (climate sensor minus offset) or none |
| `in_f`, `rate` | Filtered room temperature and its trend per hour |
| `out_slow`, `out_day` | Outdoor temperature filtered over 8 to 9 hours (the walls) and over 24 hours |
| `excess`, `base_w`, `solar` | Window sensor above its learned level (`base_w`), and the sun term |
| `bias_int` | Learned offset of the climate's own sensor |
| `target_eff`, `ff`, `i`, `p`, `sp_ctrl` | Effective target, feed-forward, integral, proportional term, unrounded setpoint |
| `trim`, `room_term` | Table mode: trim and the "room above target" part |
| `send`, `frozen` | What was sent this run (`shadow`: would be sent), and why the integral is frozen |
| `classic_setpoint`, `classic_mode` | `shadow` only: what classic set |

The diagnostic sensor is written with `set_state` once per run. Home Assistant does not keep such an entity over its own restart; it comes back at the next run (within 12 minutes). Its history is recorded like any other entity.

### Keys

All optional. Only `shadow` and `adaptive` read them; with `classic` they are ignored with one INFO line.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `controller` | string | `classic` | `classic`, `shadow` or `adaptive`. |
| `diagnostic_sensor` | entity | none | For example `sensor.living_room_controller`. Written once per run, see above. |
| `window_offset` | number | -3 | Classic: unchanged (the window sensor lowers the setpoint above `target + window_offset`). Adaptive: only the starting value of the learned window level, relative to the room. When not set, adaptive uses -3 C / -5.4 F. |
| `window_offset_table` | list | none | Starting values of the window level by outdoor temperature (see below). Needs `window_sensor_temp`. |
| `internal_offset_table` | list | none | Starting values of the climate's sensor minus the room by outdoor temperature. Gives an estimate without a room sensor from day one; the only source for a heater with no room sensor at all. |
| `target_table` | list | none | Indoor target by the 24-hour mean outdoor temperature (see below). |
| `outdoor_gain` | number 0 to 0.6 | not set | Fixed extra feed-forward per degree the slow outdoor temperature is below the target. Not set: only the learned offset is used. |
| `solar_gain` | number 0 to 5 | 1.0 | Multiplies the sun term. |
| `solar_max` | number above 0 | 3 C / 5.4 F | Largest sun term. |
| `comfort_guard` | number above 0 | 1.5 C / 2.7 F | Power-state offset dropped (until the state changes) when the room is this far below the target in `save` / `reduce`, or above it in `spend`. |

Fixed, not keys: one change per 12 minutes, 0.5 per change, room sensor stale after 1 hour, window sensor stale after 12 hours (window sensors often report only on a 1-degree change), JSON written at most every 30 minutes.

**Offset tables** (`window_offset_table`, `internal_offset_table`). Each entry applies from its `out` and up, like `temperatures`. They seed the learning: with data the learned values take over step by step. Leave them out unless you know the values (for example from the diagnostic sensor after weeks of shadow).

| Entry key | Type | Required | Meaning |
|---|---|---|---|
| `out` | number | yes | Outdoor temperature from which the entry applies. |
| `offset` | number | yes | Window sensor (or the climate's sensor) minus the room sensor. Between -30 and 30. |
| `wind` | number | no | Extra while windy (wind at or above `anemometer_speed` in the last 30 minutes). |
| `rain` | number | no | Extra while it rains (at or above `rain_level`). |

**Target table** (`target_table`): each entry has `out` and either `target` (absolute indoor target) or `offset` (added to `target_indoor_temp` or `target_indoor_input`). The outdoor value is the 24-hour mean, so the target follows the season, not day and night, and it moves to another entry at most once a day. With a `target` entry, `target_indoor_input` has no effect while that entry applies.

Unknown keys in a table entry, the same `out` twice, or `target` and `offset` together are errors: the device is skipped with an ERROR naming the key.

```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.living_room
  type: hvac
  json_path: /conf/persistent/Climate/
  indoor_sensor_temp: sensor.living_room_temperature
  window_sensor_temp: sensor.living_room_window_temperature
  target_indoor_temp: 22
  controller: shadow
  diagnostic_sensor: sensor.living_room_controller
  target_table:                 # optional
    - out: -30
      offset: 0.5
    - out: 12
      target: 21.5
```

### What differs from classic

- No learned setpoints per outdoor temperature and lux: the `"data"` entries in the JSON are neither read nor written.
- No trend hold (`trend_hold_hours` is classic only), no steady-room correction, no extra -1 after 4 hours turned down (D2), no fixed ±4 around a learned value.
- The trend uses the real room reading and the real time between reports. The daytime and wind / rain shifts move the target, not the reading.
- The window sensor counts relative to its learned level, not to `target + window_offset`, and it is not added up run after run.
- HVAC modes: heat to `fan_only` when the room is 0.6 above the effective target (plus the spend offset during `spend`) and the setpoint is at its lowest (`max_setpoint_drop` below the target) or the room still rises 0.2 C (0.36 F) per hour; with `options: [has_fireplace]` at 0.6 above alone. After the setpoint was lowered for such an overshoot instead, the first change with the room at or below the effective target may go up by more than 0.5 in one step, to the setpoint from before the lowering and at most target + 4 C (7.2 F). `fan_only` to heat at or below the effective target, not while the sun term is large, starting half a degree below the calculated setpoint. The 0.6 hysteresis now holds during daytime periods too. At least 30 minutes in a mode before an automatic switch (window and vacation switch at once). No heat to `fan_only` within 45 minutes of wind, in the hour after `daytime_increasing` or in the hour after a power state ended. Cooling uses the classic thresholds.
- The comfort guard during power states.
- The room estimate from the climate's own sensor when the room sensor is gone.
- Indoor mode: the vacation temperature is also the lowest setpoint in normal operation, and the climate's `min_temp` / `max_temp` attributes are limits. In F, make sure the climate reports them in F.
- A heater whose climate is `off` is left alone (classic sets the temperature anyway).
- Without any lux source (lux stays 0), day and night from sunrise and sunset decide when the window level may be learned, until a lux value above 0 arrives.
- After a restart the state comes back from the JSON (written at most every 30 minutes, at once when a power state starts or ends, and when AppDaemon stops or reloads the app), and a running power state is not applied twice.

### JSON and switching back

- The state is stored under `"adaptive"` next to the classic `"data"` for each climate in `<json_path>/<app name>.json`. Classic never reads it.
- Switching between `classic`, `shadow` and `adaptive` is safe in both directions. What `shadow` learned is used by `adaptive`. Switch while ElectricalManagement sends `normal`: during `save` / `spend` the new controller takes the setpoint that already holds the offset as its base, so the offset counts twice until that state ends.
- A state saved in another unit (C / F) or by another version is ignored with one INFO line, and learning starts again.
- To reset only the adaptive learning: stop the app, delete the `"adaptive"` entry of that climate, start the app.
- Without `json_path` everything learned is lost at every restart (one INFO line).

---
## HVAC modes, fan and preset

An HVAC device runs every 12 minutes (15 in table mode, or `interval_minutes`) and acts on the climate's current mode. It never turns an HVAC unit on from `off` (except in table mode when vacation ends, as for heaters). In a mode other than `heat`, `cool`, `fan_only` or `off` it logs a WARNING and does nothing. Without an indoor temperature it logs an INFO message and does nothing.

"Room" below is the indoor temperature after the daytime, rain and wind adjustments described above, except in `fan_only`, where the sensor value is used as it is.

**In `heat`:**
- Windows open: switch to `fan_only`.
- Vacation: setpoint `vacation_temp`. When the room is above `target_indoor_temp - 3` and above `vacation_temp + 3`: switch to `fan_only`.
- Otherwise the setpoint comes from the indoor rules (or the table), and:
  - wind at or above `anemometer_speed` in the last 25 minutes and the room more than 0.7 below the target: preset `boost`, if the climate offers it and the fan mode is not `Silence`. When that is no longer the case, a `boost` preset is set back to `none`;
  - room more than 0.6 above the target, no wind in the last 45 minutes, and not in the hour after a `daytime_increasing` period:
    - with `options: [has_fireplace]`, in table mode, or while ElectricalManagement sends `save`, `spend` or `reduce`: switch to `fan_only`;
    - otherwise switch to `fan_only` only when the setpoint is already at its lowest (`target_indoor_temp - max_setpoint_drop`, at least the climate's `min_temp`) or the room still rises 0.2 C (0.36 F) per hour or more. Else the setpoint is lowered by the adjustment, at most 0.5 per run and not below the lowest, and the unit stays in heat;
  - after such a lowering: once the room is at or below the target and does not reach it within `trend_hold_hours` by itself, the setpoint goes back in one move to the value from before the lowering, at most the learned setpoint and the target + 4.

**In `cool`:**
- Windows open: switch to `fan_only`.
- Switch to `fan_only` when the room is less than 1 above the target, or, between 30 minutes before sunset and sunrise while it rains, when the room is less than 2 above the target.
- Vacation: switch to `fan_only` when the room is below `max_vacation_temp - 2`.
- Otherwise the setpoint follows the indoor rules (also in table mode). Cooling setpoints are not learned. The ElectricalManagement offsets change sign (save: +0.3 by default, spend: -1 by default).
- Rain and wind work in the same direction as in heating: the room counts as 0.3 colder, so it cools less.

**In `fan_only`** (not on vacation, windows closed):
- Switch to `cool` when the room is more than 2 above the target, the outdoor temperature is above `target_indoor_temp - 4`, and either it is between sunrise and 30 minutes before sunset without rain, or the room is more than 3 above the target.
- Switch to `heat` when the room is at or below the target. 10 seconds later the setpoint is set to the last setpoint the app held in heat, but at most the learned setpoint minus 1 (without learned data: the current setpoint minus 1, or the target minus 1 if the setpoint is more than 6 away from the target). After an app start or reload, before anything was held, the learned setpoint minus 1 is used. In indoor mode never below the lowest heat setpoint (`target_indoor_temp - max_setpoint_drop`).

**In `fan_only` on vacation** (windows closed):
- Switch to `heat` when the room is at or below `target_indoor_temp - 5` or at or below `vacation_temp + 2`.
- Switch to `cool` when the room is above `max_vacation_temp` (default 30 C / 86 F).

**Fan mode `Silence`**: used only if the climate lists `Silence` in its `fan_modes`. In a `silence` period the fan mode is set to `Silence`; the previous fan mode is stored (in the JSON file when `json_path` is set) and set back when the period ends. If no previous fan mode is known, `Auto` is used. The fan mode is checked in every run, whatever the HVAC mode. A daily timer runs at every `start` and `stop` time of the `silence` periods, so the fan mode changes on time and not at the next run (the same check still runs every run and 5 seconds after a mode change, because a mode and a fan mode sent together can lose one of them). Times in `silence` must be quoted strings such as `'21:00:00'`.

### Heat pump cycling

A heat pump whose room sensor sits in the unit's air flow can cycle between `heat` and `fan_only` all night. Seen in a real bedroom (target 23, outdoor 3 to 5 C): the sensor rose about 1 degree in 25 minutes while the unit heated and fell 1 degree in 30 minutes in `fan_only`, so it swung between 22.5 and 23.9 with a mode switch about every 25 minutes. The evening before, at 6 to 9 C outside, setpoint 21.5 held the same room at 23.2 without a single switch. Three things kept the cycle going: each return from `fan_only` sent the learned setpoint minus 1 (24.5), the next run jumped to the learned 25.5, and the learned setpoints for 0 to 5 C outdoor had been pushed up to 24.5 to 25.5 by earlier cycles. The unit idles once its setpoint is at or below its own sensor reading (22 to 28 here, while the room is 23), so a much lower setpoint only makes the way back slow.

What the classic controller does about it (indoor mode, HVAC in heat):
- **Trend hold** over the reports of the last hour (see [the step rules](#indoor-mode-keep-a-fixed-indoor-temperature)), kept up in `fan_only` too, so the first run after `fan_only` already knows that the room was falling.
- **A learned setpoint is a ceiling, not a jump target**: the setpoint moves towards it by at most 0.5 per run. Back from `fan_only`, the setpoint starts from the last one held in heat, at most the learned setpoint minus 1.
- **No learning within 2 hours of a mode change**, so cycling does not push the learned setpoints up further.
- **Lower before `fan_only`** (without `has_fireplace`): at target + 0.6 the unit switches to `fan_only` only when the setpoint is already at its lowest or the room is still rising; otherwise the setpoint is lowered by the normal adjustment, at most 0.5 per run, and the unit stays in heat. The lowest setpoint is `target_indoor_temp - max_setpoint_drop` (default 3 C / 5.4 F).
- **Fast way back**: once the room is at or below the target again, a setpoint lowered this way goes back in one move instead of 0.5 per run. Example: target 23, `max_setpoint_drop` 3 (lowest 20), learned setpoint 25.5. The room is at 23.7 with setpoint 24.5 and no longer rising: instead of `fan_only` the setpoint is lowered in steps of at most 0.5 (24.0, 23.5, ...). Say it has reached 22.5 when the room is down at 22.9 and still falling: the next run sets it straight back to min(24.5 from before the lowering, learned 25.5, 23 + 4) = 24.5.

**`options: [has_fireplace]`**: in a room with a fireplace (or another heat source that makes the room overshoot fast) waiting in heat is wrong. With this option the unit switches to `fan_only` at once at target + 0.6, as before 1.4.0. The other rules above still apply.

**`interval_minutes`**: a room sensor in air flow reacts within minutes; a longer interval (for example 30) gives each setpoint change time to show before the next one.

If a unit still cycles after these changes, look at its learned setpoints for the current outdoor temperature in `<json_path>/<app name>.json` (`"data"`, by outdoor temperature in 2-degree steps, then lux). Values far above the setpoint that holds the room on a calm day were learned while it cycled. Stop the app, remove those entries (or the whole `"data"` of that climate) and start it again; they are learned anew.

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
| `options` | list | no | empty | Flags. `vacation_keep_off` is the same as `vacation_keep_off: true` (ElectricalManagement style). `has_fireplace` (HVAC): heat to `fan_only` at once at target + 0.6, without lowering the setpoint first, see [Heat pump cycling](#heat-pump-cycling). Unknown names are ignored. |
| `interval_minutes` | number | no | 12 indoor, 15 table | Minutes between the periodic runs, 1 to 120. |
| `trend_hold_hours` | number | no | 2 | Indoor mode (classic): the setpoint is kept while the room reaches the target within this many hours at its trend, 0 to 12 (0: never kept). See [the step rules](#indoor-mode-keep-a-fixed-indoor-temperature). |
| `max_setpoint_drop` | number | no | 3 C / 5.4 F | HVAC in heat, indoor mode: lowest setpoint is `target_indoor_temp` minus this (at least the climate's `min_temp`). Above 0, at most 15. Also used by `controller: adaptive`. |
| `window_sensor_temp` | entity | no | none | Temperature sensor in a window (sun). `window_temp` works as an alias. |
| `window_offset` | number | no | -3 | The window sensor lowers the setpoint when it is above `target_indoor_temp + window_offset`. With `controller: shadow` / `adaptive` only the starting value of the learned window level (default -3 C / -5.4 F when not set). |
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
| `controller` | string | no | `classic` | `classic`, `shadow` or `adaptive`, see [Adaptive controller](#adaptive-controller-optional). |
| `diagnostic_sensor` | entity | no | none | Shadow / adaptive: sensor the app writes with the controller's terms once per run. |
| `window_offset_table` | list | no | none | Shadow / adaptive: starting values of the learned window level, see [Keys](#keys). |
| `internal_offset_table` | list | no | none | Shadow / adaptive: starting values of the climate's sensor minus the room. |
| `target_table` | list | no | none | Shadow / adaptive: indoor target by the 24-hour mean outdoor temperature. |
| `outdoor_gain` | number | no | not set | Shadow / adaptive: fixed outdoor feed-forward, 0 to 0.6. |
| `solar_gain` | number | no | 1.0 | Shadow / adaptive: factor on the sun term, 0 to 5. |
| `solar_max` | number | no | 3 C / 5.4 F | Shadow / adaptive: largest sun term. |
| `comfort_guard` | number | no | 1.5 C / 2.7 F | Shadow / adaptive: power-state offset dropped when the room is this far off target. |

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
- `interval_minutes` must be 1 to 120, `trend_hold_hours` 0 to 12, `max_setpoint_drop` above 0 and at most 15. Flags go in `options` (`options: [has_fireplace]`); `has_fireplace: true` as its own key is an unknown key and is ignored.
- The [adaptive controller keys](#keys) are checked strictly: `controller` must be `classic`, `shadow` or `adaptive`; `outdoor_gain` 0 to 0.6, `solar_gain` 0 to 5, `solar_max` and `comfort_guard` above 0; `diagnostic_sensor` must look like an entity id; table entries may only have their documented keys, offsets between -30 and 30, no `out` twice, and `target_table` entries exactly one of `target` / `offset`.

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
| WARNING | `<climate>: no fresh room sensor: using the climate's own sensor minus the learned offset (<x>). Integral frozen, no mode changes, no learning.` | Adaptive / shadow: the room sensor (and backup) is unavailable or silent for an hour. The room is estimated until it is back. Check the sensor's battery or connection. |
| WARNING | `<climate>: no fresh room sensor and no usable internal-sensor offset: open loop (feed-forward only). internal_offset_table gives an estimate.` | Adaptive / shadow: no room sensor and nothing learned yet about the climate's own sensor. Fix the sensor, or add `internal_offset_table`. |
| INFO | `<climate>: room sensor back (primary).` | The estimate or open loop has ended. |
| INFO | `<climate>: comfort guard: room <x>, target <y>, '<state>' offset dropped until the state changes.` | Adaptive: the room got `comfort_guard` too cold (save / reduce) or too warm (spend). Raise `comfort_guard` if this happens too often. |
| INFO | `<climate>: setpoint changed by hand to <x>: followed.` | Adaptive: someone (or another app) changed the setpoint; the controller continues from it. |
| INFO | `<climate>: adaptive state from another version or unit is ignored (learned models start empty).` | The `"adaptive"` entry in the JSON was saved in another unit or format. Learning starts again. |
| INFO | `<climate>: <keys> only work with controller 'shadow' or 'adaptive'. Ignored with the classic controller.` | Remove the keys, or set `controller`. |
| INFO | `<climate>: controller '<c>' is for indoor mode on HVAC ... the classic controller is used.` | An HVAC in table mode keeps the classic controller. |
| INFO | `<climate>: controller '<c>' without json_path: ...` | Set `json_path` to keep what the controller learns over restarts. |
| WARNING | `<climate>: window_offset_table needs window_sensor_temp. The table is not used.` | Add `window_sensor_temp`, or remove the table. |
| WARNING | `<climate>: internal_offset_table is set, but the climate reports no current_temperature (yet). ...` | The climate has no own temperature (or it was not there at startup). The table only works when it reports one. |
| any | `<climate>: [shadow] ...` | The same messages from a device in `shadow`: nothing was changed. |
| DEBUG | `<climate>: shadow heat src primary R ... sp_ctrl ... send ... hold ... classic ...` | One line per run in `shadow` / `adaptive` with `log_level: DEBUG`, see [Start with shadow](#start-with-shadow). |

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
- Indoor mode: a learned setpoint is approached by at most 0.5 per run, not jumped to.
- HVAC without `has_fireplace`: at target + 0.6 the setpoint is lowered first; `fan_only` only at the lowest setpoint or while the room still rises. HVAC: nothing is learned within 2 hours of a mode change.
- The "no cooling when it is cold outside" rule (`fan_only` to cool only with the outdoor temperature above `target_indoor_temp - 4`) applies with and without `has_fireplace`.
- Learned setpoints are only updated in normal operation (not on vacation, with open windows, during daytime savings / increasing, or in an ElectricalManagement state).
- Table mode: open windows and `reduce` use the **lowest** of the save and vacation temperature.
- Table mode: an entry applies from its `out` value and up.
- In cooling, rain and wind make the app cool less (same direction as in heating).
- With two own lux sensors the highest fresh value is used. In the Weather app the lowest temperature and the highest lux are used.
- `input_boolean.vacation` is the default vacation switch only if it exists (a convention shared with ElectricalManagement and ModeManagement).
- Fixed values in the rules (steps of 0.3 / 0.5 / 1, the fallback 10, `getting_hot_offset` 10, 400 lux, the target + 4 of the fast way back) are not converted for Fahrenheit; only the defaults in the tables above are, and the 0.2 C per hour rise limit before `fan_only` (0.36 F).
- In table mode without an indoor sensor, the climate's own `current_temperature` is compared with `target_indoor_temp`. For a floor heater this can be the floor temperature: set `indoor_sensor_temp`, or a `target_indoor_temp` that suits that sensor. (`controller: adaptive` leaves that comparison out, see [Adaptive controller](#adaptive-controller-optional).)
- The rules above (the asymmetry, the extra -1, the learned setpoints) belong to the classic controller. The adaptive controller's own deliberate choices are listed in [What differs from classic](#what-differs-from-classic).

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
