# Climate Commander 🌡️
**An intelligent climate control app for [Home Assistant](https://www.home-assistant.io/) built with [AppDaemon](https://appdaemon.readthedocs.io/en/latest/).**  
Automate HVAC systems, manage cover controls, and maintain optimal indoor temperatures using environmental data from sensors.

> 🤖 **Set up with an AI assistant:** the [LLM guide](https://github.com/Pythm/Pythm_AppdaemonApps/blob/master/LLM_GUIDE.md) helps an AI assistant find your own entity ids in Home Assistant and write the configuration.

---
## 🚨 Breaking Changes  

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

![AI‑Generated Screenshot](_5b05cb75-1f9c-4fed-9aa6-0e4f9d73c8ac.jpg)

---
## 📦 Installation & Configuration  

### 1. **Clone the repository**  

```bash
git clone https://github.com/Pythm/ad-ClimateCommander.git /path/to/appdaemon/apps/
```

### 2. **Configure the app** in your AppDaemon `.yaml` or `.toml` file:  

```yaml
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  HVAC:
    - climate: climate.yourClimate
      indoor_sensor_temp: sensor.yourIndoorTemperatureSensor
      target_indoor_temp: 22.7
```

> 💡 **Tip**: Default values are used if parameters are omitted from the configuration.

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
| `json_path`          | string     | `None`         | Persistent storage path for JSON data.                                       |
| `vacation`           | input_boolean | `input_boolean.vacation` | Enable vacation mode.                                                     |
| `vacation_temp`      | int        | `16`           | Indoor temperature during vacation.                                         |

### **Climate Entity Configuration**  

| Key                  | Type       | Default        | Description                                                                 |
|----------------------|------------|----------------|-------------------------------------------------------------------------------|
| `HVAC`               | list       | (required)     | List of HVAC entities to control.                                            |
| `Heaters`            | list       | (required)     | List of heater entities to control.                                          |
| `indoor_sensor_temp` | sensor     | (required)     | External indoor temperature sensor.                                          |
| `target_indoor_temp` | float      | `23`           | Desired indoor temperature.                                                 |
| `target_indoor_input`| input_number | (optional)  | Use a Home Assistant input number to set the target temperature dynamically. |
| `window_sensor_temp` | sensor     | (optional)     | Window‑temperature sensor for adjusting the target temperature based on sunlight. |
| `window_offset`      | int        | `-3`           | Offset between the window and indoor sensors for accuracy.                  |
| `daytime_savings`    | dict       | (optional)     | Adjust temperature during specified hours (e.g., lower temperature during the day). |
| `silence`            | dict       | (optional)     | Set fan to *silence* during defined intervals.                              |
| `windowsensors`      | list       | (optional)     | List of window/door sensors that turns down heating.                        |

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
nameyourClimateCommander:
  module: climateCommander
  class: Climate
  json_path: /conf/persistent/Climate/
  vacation: input_boolean.vacation

  vacation_temp: 16
  screening_temp: 8
  getting_cold: 18
  rain_level: 3
  anemometer_speed: 40

  HVAC:
    - climate: climate.yourClimate
      indoor_sensor_temp: sensor.yourIndoorTemperatureSensor
      target_indoor_temp: 22.7
      automate: input_boolean. #Stops all automation of heating when off
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
- **Wind/Rain Sensors**: Automatically increase the indoor temperature by **0.3 °C** when detected.  
- **Persistent Storage**: Store the average set temperature and fan mode for smoother restarts.

---
## 📢 Notifications  

- Configure `notify_receiver` with a list of devices (e.g., `mobile_app_your_phone`).  
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
- Wind or rain, **separately or combined**, increase the indoor temperature by **0.3 °C**.

---
## 📌 License  

[MIT License](https://github.com/Pythm/ad-ClimateCommander/blob/main/LICENSE)

---
## 📈 Roadmap  

- Add support for [ad‑ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement) to adjust temperature based on **electricity price**.

---
## 🙋 Contributing  

- Found a bug? Open an issue or submit a PR!  
- Want to add a feature? Discuss in the [GitHub Discussions](https://github.com/Pythm/ad-ClimateCommander/discussions).

---
**Climate Commander by [Pythm](https://github.com/Pythm)**  
[GitHub](https://github.com/Pythm/ad-ClimateCommander)
```
