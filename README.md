# Climate Commander 🌡️
**An intelligent climate control app for [Home Assistant](https://www.home-assistant.io/) built with [AppDaemon](https://appdaemon.readthedocs.io/en/latest/).**  
Automate HVAC systems, manage cover controls, and maintain optimal indoor temperatures using environmental data from sensors.

> 🤖 **Set up with an AI assistant:** the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md) helps an AI assistant find your own entity ids in Home Assistant and write the configuration. Notes for the assistant about this app: [LLM.md](LLM.md).

---
## 🚨 Breaking Changes  

### **1.4.0**  
**Nothing to do for the default case**: your configuration works as before. What is new or different:

- **One app per climate entity** is the recommended setup (top-level `climate:` and `type: heater` or `type: hvac`, see [One app per device](#-one-app-per-device)). The old style with `HVAC:` / `Heaters:` lists in one app still works but logs a deprecation WARNING.
- **Heaters with an outdoor-temperature table** (`temperatures`, `target_heater_temp`, `save_temp`, ...), the heater logic from [ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement). See [How the setpoint is found](#-how-the-setpoint-is-found).
- **Save / spend from ElectricalManagement** (optional, ElectricalManagement 1.0.6 or later), see [ElectricalManagement](#-electricalmanagement).
- **The app now has two more files**, `cc_devices.py` and `cc_config.py`, next to `climateCommander.py`. Copy the whole app folder when you update (HACS does).
- **The configuration is validated with pydantic** (`cc_config.py`). A value of the wrong type (for example `window_offset: abc`, a table entry without `out`) now logs an ERROR naming the key: an invalid device is skipped, an invalid app-level key stops that app. Before, such a value was silently used or crashed later (`vacation_temp: abc` used to fall back to 10). A single entity where a list is expected (for example `windowsensors: binary_sensor.x`) is accepted as a one-item list.
- Behaviour changes in the indoor-target mode (HVAC and heaters without a table):
  - The learned setpoints are no longer updated on vacation, with open windows, during `daytime_savings` / `daytime_increasing` or while ElectricalManagement saves or spends. Before, vacation temperatures were learned and pulled the setpoint down after a vacation.
  - A room that stays 0.3 °C or more away from the target is now corrected when the setpoint has not changed for 30 minutes. Before, nothing happened while the temperature did not move.
  - `backup_indoor_sensor_temp` is used only when the indoor sensor has not **reported** for an hour (`last_reported`). Before, a steady temperature for an hour (no change) counted as a dead sensor.
- Window notifications are sent once per event: "Window open" (getting cold) once after windows are opened, "Window closed" (getting hot, `getting_hot_offset` above target) once after they are closed. Before, "Window open" was sent once per app start and "Window closed" never. HVAC now uses the same rule as heaters ("Window open" also needs it to be below `getting_cold` outside).
- Outdoor values are kept per app, not shared between all ClimateCommander apps (they used to overwrite each other). At startup the last values from the Weather app are used.
- The 12-minute runs of the devices are spread over the interval, so many devices do not call Home Assistant in the same second.
- `outside_temperature`, `rain_sensor` and `anemometer` (deprecated) are read in `HASS_namespace`.

#### Upgrading from 1.3 to one app per device (optional)  
Before:
```yaml
klimakontroll:
  module: climateCommander
  class: Climate
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  HVAC:
    - climate: climate.daikin_living_room
      indoor_sensor_temp: sensor.living_room_temperature
      target_indoor_temp: 22.8
```
After:
```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.daikin_living_room
  type: hvac
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation
  indoor_sensor_temp: sensor.living_room_temperature
  target_indoor_temp: 22.8
```
- App-level keys (`vacation`, `vacation_temp`, `notify_app`, `notify_receiver`, `json_path`, ...) go into each app. YAML anchors (`<<: *climate_defaults`) can share them inside one file.
- Learned setpoints: on its first start a one-device app copies the data for its entity from the other JSON files in `json_path` (the old app's file), then writes its own `<app name>.json`. Remove the device from the old app when you move it.
- Many apps: AppDaemon gives every app its own thread. With more than 20-30 apps, set `total_threads` (for example 10) in `appdaemon.yaml`; apps then share the threads.

### **1.1.0**  
- **Unified control naming**: Climate entities are now configured as either **"HVAC"** or **"Heater"** for clarity.  
- Renamed `indoor_sensor_temp` (previously `indoor_temp`).  
- Renamed **"vacation mode"** (away state) to **`vacation`** to distinguish from daily *away* states.  

### **1.2.0**  
- **Notification Enhancements**: Notifications now send recipients as a list (single API call). *Custom notification apps may need updates.*  
- **Weather Sensor Configuration**: Automatic weather sensor detection removed. Use the [ad‑Weather](https://github.com/Pythm/ad-Weather) app or a custom event‑based solution.  

### **1.2.3**  
- **MQTT Default Namespace**: Updated to `'mqtt'` as this is the **default** in AppDaemon documentation.  

### **1.2.4**  
- **Spelling Correction**: Changed `notify_reciever` → `notify_receiver`.  

### **1.3.0**  
- **The [ad‑Weather](https://github.com/Pythm/ad-Weather) app is required** for outdoor temperature, rain, wind and lux (an ERROR is logged at startup if it is not found; `weather_app` sets its name). **Nothing to do** if you already use it. Install ad‑Weather 0.2.0 or later: it sends values it does not have as `None`, which this version handles.  
- `outside_temperature`, `rain_sensor` and `anemometer` are **deprecated** (a WARNING is logged). They still work and take precedence while they report.  
- Own outdoor lux sensors (`OutLux_sensor`, `OutLuxMQTT`, `_2`) are used while they report; after 20 minutes of silence the lux from ad‑Weather is used. The logic is in the new file `weather_data.py` (a copy of the file in Lightwand), so copy the whole app folder when you update.  

---
## 🔍 Features  

- **HVAC Mode Switching**  
  - Switches to **`fan_only`** if the indoor temperature is **0.6 °C above target** or windows are open.  
  - Switches to **`cool`** if the indoor temperature is **2 °C above target** *and* the outdoor temperature exceeds the indoor target.  
  - Switches to **`heat`** if the indoor temperature is **at or below target**.  

- **Screen/Cover Automation**  
  - Automatically closes screens/curtains when the indoor temperature exceeds the target *and* ambient light (lux) levels are above thresholds.  
  - Supports **wind**, **rain**, and **lux** sensors for dynamic control.  

- **Vacation Mode**  
  - Lowers the indoor temperature to `vacation_temp` when activated via an `input_boolean`.  

- **Daytime Savings / Increasing**  
  - Adjusts the target temperature during specific time windows, optionally based on presence detection.  

- **Silent Periods**  
  - Sets the fan speed to *silence* during defined intervals.  

- **Outdoor-temperature table for heaters**  
  - The heater setpoint follows a table of outdoor temperatures (floor heating, panel heaters).  

- **Electricity price control** with [ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement)  
  - Save when electricity is expensive, spend before it gets expensive or on solar overproduction, and reduce when the house is over its kWh goal.  

![AI‑Generated Screenshot](_5b05cb75-1f9c-4fed-9aa6-0e4f9d73c8ac.jpg)

---
## 📦 Installation & Configuration  

### 1. **Clone the repository**  

```bash
git clone https://github.com/Pythm/ad-ClimateCommander.git /path/to/appdaemon/apps/
```

### 2. **Configure the app** in your AppDaemon `.yaml` or `.toml` file, one app per climate entity:  

```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.yourClimate
  type: hvac
  indoor_sensor_temp: sensor.yourIndoorTemperatureSensor
  target_indoor_temp: 22.7
```

> 💡 **Tip**: Default values are used if parameters are omitted from the configuration.

---
## 🧩 One app per device  

Each climate entity gets its own app: top-level `climate:` (or `heater:`) and `type:`.

| `type` | Use for | Modes |
|---|---|---|
| `heater` (default) | floor heating, panel heaters, anything that only heats | sets the temperature |
| `hvac` | heat pumps / air conditioners | heat, cool and fan_only; fan *Silence*, *boost* preset |

Why one app per device: a change to one device's YAML reloads only that device, an error stops only that device, each app has its own small JSON file, and ElectricalManagement controls each device by app name. Every app listens to `WEATHER_CHANGE` itself; the Weather app sends it at most every 3 minutes, so many listeners cost very little.

The old style (`HVAC:` and `Heaters:` lists in one app) still works and logs a deprecation WARNING. In that style the device keys below go into each list entry, and these keys can also be given once at the app level: `vacation_temp`, `max_vacation_temp`, `screening_temp`, `getting_cold`, `getting_hot_offset`, `rain_level`, `anemometer_speed`, `spend_temp_offset`.

---
## 🎯 How the setpoint is found  

There are two ways (`target_mode`). The mode is chosen from the keys you use; set `target_mode` to choose it yourself.

**`indoor`: fixed indoor temperature** (default without a table). Keep the room at `target_indoor_temp` (or `target_indoor_input`). The setpoint is moved step by step after the indoor sensor, and the setpoint that held the target is learned per outdoor temperature and lux (needs `json_path`). Used by HVAC and by heaters without a table.

**`table`: outdoor-temperature table** (chosen when `temperatures`, `target_heater_temp` or `target_heater_input` is set). The setpoint is `target_heater_temp` (or `target_heater_input`) plus the `offset` of the table entry for the outdoor temperature, or the entry's absolute `normal` temperature. An entry applies from its `out` value and up; below the first entry the first one is used. Then:
- rain at or above `rain_level`, or wind at or above `anemometer_speed`: +1 °C
- a `window_temp` sensor above `target_indoor_temp + window_offset` lowers the setpoint by the difference
- an `indoor_sensor_temp` above `target_indoor_temp` lowers it by the difference (window and indoor together at most 3 °C when the room is above target)
- `daytime_savings` and ElectricalManagement `save` use the save temperature: `save_temp_offset` (added), or `save_temp` (+ the entry's offset), or the entry's `save`, else 10 °C
- open windows and ElectricalManagement `reduce` use the lowest of the save and vacation temperature (it stops the heater drawing power fastest)
- never below the vacation temperature or the climate's `min_temp`
- the table waits for the first outdoor temperature from the Weather app after a start

```yaml
heater_bathroom:
  module: climateCommander
  class: Climate
  climate: climate.bathroom_floor
  type: heater
  indoor_sensor_temp: sensor.bathroom_temperature
  target_indoor_temp: 24.2      # the room should not go above this
  target_heater_temp: 28        # floor sensor setpoint at offset 0
  save_temp_offset: -0.5
  vacation_temp: 13
  windowsensors:
    - binary_sensor.bathroom_window
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

A table can also use absolute temperatures per entry:
```yaml
  temperatures:
    - out: -4
      normal: 20
      save: 13
      vacation: 14
```

Without `target_heater_temp` / `target_heater_input` the indoor target (`target_indoor_temp` / `target_indoor_input`) is used as the heater target, as in ElectricalManagement. HVAC can use a table too; in cool mode the indoor temperature is always used.

The configuration keys of an ElectricalManagement `climate:` entry work here too: `heater` (= `climate`), `window_temp` (= `window_sensor_temp`), `recipient` (= `notify_receiver`), `options: [vacation_keep_off]` (= `vacation_keep_off: true`). The energy keys (`consumptionSensor`, `pricedrop`, `max_continuous_hours`, ...) stay in ElectricalManagement.

---
## ⚡ ElectricalManagement  

[ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement) 1.0.6 or later can control a ClimateCommander device: list the app under `climate_commander:` in ElectricalManagement, together with the energy keys. Nothing is needed in this app. ElectricalManagement then sends one of these states:

| State | Heater with a table | `indoor` mode (HVAC, heaters without a table) |
|---|---|---|
| `normal` | table setpoint | follows the indoor sensor |
| `save` (high price) | save temperature | setpoint + `save_temp_offset` (default −0.3; cooling: +0.3) |
| `spend` (low price, solar) | + `spend_temp_offset` (default 1) | setpoint + `spend_temp_offset` (cooling: −1) |
| `reduce` (over the kWh goal) | lowest of save and vacation temperature | same as `save` |

- A new state is applied at once.
- In `indoor` mode the setpoint is held at its value from before the state plus the offset, so the regulation does not work against the saving. The learned setpoints are not updated during a state. When the state ends, regulation continues from the earlier setpoint.
- Vacation and open windows win over the states in `indoor` mode.
- If ElectricalManagement stops sending, the device returns to `normal` after 45 minutes.

---
## 📌 Notes  

- **Weather Data**: Use the [ad‑Weather](https://github.com/Pythm/ad-Weather) app for **optimal integration**. It consolidates all your weather sensors into a single app and publishes events for other apps to consume.  
  > ⚠️ **Important**: ClimateCommander requires the ad‑Weather app (the app name can be set with `weather_app`, default `weather`). An ERROR is logged at startup if it is not found. `outside_temperature`, `rain_sensor` and `anemometer` are deprecated (a WARNING is logged), but still work and take precedence while they report. Own lux sensors (`OutLux_sensor...`) for screen control are supported like in Lightwand: they are used while they report, and the lux from ad‑Weather is used when they have been silent for 20 minutes or none are configured (the logic is in `weather_data.py`, a copy of the file in Lightwand). Values the Weather app does not have are sent as `None` and are ignored.

- **Indoor Sensor**: Required for proper operation. Ensure it is placed correctly and the target temperature is set appropriately.

---
## 📚 Key Definitions  

### **App‑Level Configuration**  

| Key                  | Type       | Default        | Description                                                                 |
|----------------------|------------|----------------|-------------------------------------------------------------------------------|
| `HASS_namespace`     | string     | `"default"`    | Home Assistant namespace (optional).                                          |
| `MQTT_namespace`     | string     | `"mqtt"`       | MQTT namespace (optional).                                                   |
| `weather_app`        | string     | `weather`      | Name of the ad‑Weather app. Required app, an ERROR is logged if not found.    |
| `outside_temperature`| sensor     | (deprecated)   | Sensor for outdoor temperature. Configure it in ad‑Weather instead.           |
| `anemometer`         | sensor     | (deprecated)   | Wind‑speed sensor. Configure it in ad‑Weather instead.                        |
| `anemometer_speed`   | int        | `40`           | Wind‑speed threshold to trigger screen opening/boost mode.                    |
| `rain_sensor`        | sensor     | (deprecated)   | Rain‑detection sensor. Configure it in ad‑Weather instead.                    |
| `rain_level`         | int        | `3`            | Rain‑intensity threshold to trigger screen opening.                          |
| `OutLuxMQTT`         | MQTT sensor| (optional)     | Primary outdoor lux sensor via MQTT.                                         |
| `OutLux_sensor`      | sensor     | (optional)     | Primary outdoor lux sensor.                                                   |
| `OutLuxMQTT_2`       | MQTT sensor| (optional)     | Secondary outdoor lux sensor via MQTT.                                       |
| `OutLux_sensor_2`    | sensor     | (optional)     | Secondary outdoor lux sensor (non‑MQTT).                                     |
| `screening_temp`     | int        | `8`            | Minimum outdoor temperature to close screens/covers.                         |
| `getting_cold`       | int        | `18`           | Threshold to trigger “cold outside” notifications.                           |
| `json_path`          | string     | `None`         | Folder for the JSON file (`<app name>.json`, end the path with `/`): learned setpoints and fan mode. |
| `vacation`           | input_boolean | `input_boolean.vacation` | Enable vacation mode (the default is used only if that entity exists). |
| `vacation_temp`      | int        | `16` (`indoor` mode), none (`table`) | Temperature during vacation. In `table` mode the entry's offset is added. |
| `max_vacation_temp`  | int        | `30`           | HVAC: cool on vacation above this.                                            |
| `getting_hot_offset` | float      | `10`           | "Window closed" notification when the room is this much above target.        |
| `notify_app`         | string     | (optional)     | Your own notification app (`send_notification` method). Without it the HA `notify` service is used. |
| `notify_receiver`    | list       | (optional)     | Receivers of the window notifications.                                        |
| `unit_of_measurement`| string     | from sensors   | `C` or `F`. Found from the indoor sensor if not set.                          |

### **Device Configuration**  

| Key                  | Type       | Default        | Description                                                                 |
|----------------------|------------|----------------|-------------------------------------------------------------------------------|
| `climate`            | climate    | (required)     | The climate entity (top level in a one-device app). `heater` works too.      |
| `type`               | string     | `heater`       | `heater` or `hvac`, see [One app per device](#-one-app-per-device).            |
| `HVAC`, `Heaters`    | list       | (deprecated)   | Old style: lists of devices in one app. Still works.                         |
| `target_mode`        | string     | from the keys  | `indoor` or `table`, see [How the setpoint is found](#-how-the-setpoint-is-found). |
| `indoor_sensor_temp` | sensor     | (required in `indoor` mode) | Indoor temperature sensor.                                     |
| `backup_indoor_sensor_temp` | sensor | (optional) | Used when the indoor sensor is unavailable or has not reported for an hour.   |
| `target_indoor_temp` | float      | `22.7` °C / `72.8` °F | Desired indoor temperature.                                          |
| `target_indoor_input`| input_number | (optional)  | Use a Home Assistant input number to set the target temperature dynamically. |
| `temperatures`       | list       | (optional)     | `table` mode: entries with `out` and `offset`, or `out` with `normal`/`save`/`vacation`. |
| `target_heater_temp` | float      | indoor target  | `table` mode: heater setpoint at offset 0.                                   |
| `target_heater_input`| input_number | indoor input | `table` mode: input number for the heater setpoint.                          |
| `save_temp_offset`   | float      | `-0.3` in `indoor` mode | Degrees added in save. `table` mode: see the setpoint rules.       |
| `save_temp`          | float      | (optional)     | `table` mode: save temperature (the entry's offset is added).                |
| `spend_temp_offset`  | float      | `1`            | Degrees added in spend (subtracted when cooling).                            |
| `vacation_keep_off`  | bool       | `false`        | `table` mode: turn the climate off on vacation (and on again after).         |
| `window_sensor_temp` | sensor     | (optional)     | Window‑temperature sensor for adjusting the target temperature based on sunlight. `window_temp` works too. |
| `window_offset`      | int        | `-3`           | Offset between the window and indoor sensors for accuracy.                  |
| `daytime_savings`    | list       | (optional)     | Lower temperature during specified hours, optionally only when nobody in `presence` is home. |
| `daytime_increasing` | list       | (optional)     | `indoor` mode: raise the temperature during specified hours.                 |
| `silence`            | list       | (optional)     | HVAC: set fan to *Silence* during defined intervals.                         |
| `automate`           | input_boolean | (optional)  | No automation while it is off.                                               |
| `windowsensors`      | list       | (optional)     | Open for 2 minutes: heater to vacation temperature (`table`: lowest of save and vacation), HVAC to fan_only. |
| `namespace`          | string     | `HASS_namespace` | Namespace of this device's entities.                                       |

### **Screen/Cover Automation**  

| Key               | Type    | Default   | Description                                                             |
|-------------------|---------|-----------|-------------------------------------------------------------------------|
| `screening`       | dict    | (optional) | Configures cover entities for automatic opening/closing.                |
| `lux_close`       | int     | `40000`  | Lux threshold to close covers.                                          |
| `lux_open`        | int     | `15000`  | Lux threshold to open covers.                                           |
| `lux_open_media`  | int     | `4000`   | Lux threshold to open covers when media is active.                      |
| `not_when_home`   | list    | (optional) | Prevent covers from closing if specified users are home.                |
| `mediaplayers`    | list    | (optional) | List of media/switch entities to use an alternative lux‑open value.      |

---
## 🧩 Example Configuration  

```yaml
climate_living_room:
  module: climateCommander
  class: Climate
  climate: climate.yourClimate
  type: hvac
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation

  vacation_temp: 16
  screening_temp: 8
  getting_cold: 18
  rain_level: 3
  anemometer_speed: 40

  indoor_sensor_temp: sensor.yourIndoorTemperatureSensor
  target_indoor_temp: 22.7
  automate: input_boolean.automate_climate # Stops all automation of heating when off
  daytime_savings:
    - start: '10:00:00'
      stop: '14:00:00'
      presence:
        - person.wife
        - person.myself
  silence:
    - start: '21:00:00'
      stop: '07:00:00'
      presence:
        - person.myself
  screening:
    - screen: cover.your_screen
      lux_close: 40000
      lux_open: 15000
      lux_open_media: 4000
      not_when_home:
        - person.wife
      mediaplayers:
        - switch.projector
        - media_player.your_tv
  windowsensors:
    - binary_sensor.your_window_door_is_open
```

---
## 📌 Tips & Best Practices  

- Use **`input_boolean`** to toggle automation on/off with `automate`.  
- For **lux sensors**, use two sensors with names ending in `'_2'` for redundancy.  
- **Wind/Rain Sensors**: `indoor` mode raises the temperature by **0.3 °C**, `table` mode raises the setpoint by **1 °C**.  
- **Persistent Storage**: Store the average set temperature and fan mode for smoother restarts.

---
## 📢 Notifications  

- Configure `notify_receiver` with a list of devices (e.g., `mobile_app_your_phone`).  
- "Window open": once after windows are opened, when both the outdoor and the indoor temperature are below `getting_cold`. "Window closed": once after all windows are closed, when the room is `getting_hot_offset` (default 10) above target and it is above `getting_cold` outside.  
- Use a custom notification app with the `send_notification` method.

---
## 🌤️ Weather Sensor Integration  

We **strongly recommend** using the [ad‑Weather](https://github.com/Pythm/ad-Weather) app for weather data:  

- It consolidates all your weather sensors into a single app.  
- It **publishes events** that other apps (like ClimateCommander) can use.  
- If you configure weather sensors directly in `ClimateCommander`, they **take precedence** over [ad‑Weather](https://github.com/Pythm/ad-Weather).  

### Wind & Rain Sensors in ClimateCommander  

- Define your **wind‑speed sensor** with `anemometer` and a threshold with `anemometer_speed`.  
  - If wind exceeds the threshold, **screens open** and HVAC may activate **boost mode**.  
- Define your **rain sensor** with `rain_sensor` and a threshold with `rain_level`.  
  - If rain is detected, **screens open**.  
- Wind or rain, **separately or combined**: `indoor` mode raises the indoor temperature by **0.3 °C**, `table` mode the setpoint by **1 °C**.

---
## 📌 License  

[MIT License](https://github.com/Pythm/ad-ClimateCommander/blob/main/LICENSE)

---
## 📈 Roadmap  

- Electricity price control is available with [ad‑ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement) 1.0.6 (`climate_commander:`).

---
## 🙋 Contributing  

- Found a bug? Open an issue or submit a PR!  
- Want to add a feature? Discuss in the [GitHub Discussions](https://github.com/Pythm/ad-ClimateCommander/discussions).

---
**Climate Commander by [Pythm](https://github.com/Pythm)**  
[GitHub](https://github.com/Pythm/ad-ClimateCommander)
```
