
# Climate Commander 🌡️  
**An intelligent climate control app for [Home Assistant](https://www.home-assistant.io/) built with [AppDaemon](https://appdaemon.readthedocs.io/en/latest/).**  
Automate HVAC systems, manage cover controls, and maintain optimal indoor temperatures using environmental data from sensors.  

---

## 🚨 Breaking Changes  
### **1.1.0**  
- **Unified control naming**: Climate entities are now configured as either **"HVAC"** or **"Heater"** for clarity.  
- Renamed `indoor_sensor_temp` (previously `indoor_temp`).  
- Renamed **"vacation mode"** (away state) to **`vacation`** to distinguish from daily "away" states.  

### **1.2.0**  
- **Notification Enhancements**: Notifications now send recipients as a list (single API call). *Custom notification apps may need updates.*  
- **Weather Sensor Configuration**: Automatic weather detection removed. Use [ad-Weather](https://github.com/Pythm/ad-Weather) or a custom event-based solution.  

### **1.2.3**  
- **MQTT Default Namespace**: Updated to `'mqtt'` as this is the **default** in AppDaemon documentation.  

---

## 🔍 Features  
- **HVAC Mode Switching**  
  - Switches to **`fan_only`** if indoor temperature is **0.6°C above target** or windows are open.  
  - Switches to **`cool`** if indoor temperature is **1°C above target** *and* outdoor temp exceeds indoor target.  
  - Switches to **`heat`** if indoor temperature is **at or below target**.  
- **Screen/Cover Automation**  
  - Automatically closes screens/curtains when indoor temp exceeds target *and* ambient light (lux) levels are above thresholds.  
  - Supports **wind**, **rain**, and **lux sensors** for dynamic control.  
- **Vacation Mode**  
  - Lowers indoor temp to `vacation_temp` when activated via `input_boolean`.  
- **Daytime Savings/Increasing**  
  - Adjusts target temp during specific time windows, optionally based on presence detection.  
- **Silent Periods**  
  - Sets fan speed to "silence" during defined intervals.  

![AI-Generated Screenshot](_5b05cb75-1f9c-4fed-9aa6-0e4f9d73c8ac.jpg)  

---

## 🛠️ Installation & Configuration  
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

> 💡 **Tip**: Default values are used if parameters are omitted in the configuration.  

---

## 📌 Notes  
- **Weather Data**: Use the [ad-Weather](https://github.com/Pythm/ad-Weather) app for **optimal integration**. It consolidates all your weather sensors into one app and publishes events for other apps to use.  
  > ⚠️ **Important**: If you configure weather sensors directly in ClimateCommander, they will **take precedence** over the ad-Weather app.  
- **Indoor Sensor**: Required for proper operation. Ensure it's placed correctly and target temp is set appropriately. 

---

## 📚 Key Definitions  
### **App-Level Configuration**  
| Key                  | Type       | Default        | Description                                                                 |
|----------------------|------------|----------------|-----------------------------------------------------------------------------|
| `HASS_namespace`     | string     | `"default"`    | Home Assistant namespace (optional).                                        |
| `MQTT_namespace`     | string     | `"mqtt"`       | MQTT namespace (optional).                                                  |
| `outside_temperature` | sensor     | (required)     | Sensor for outdoor temperature.                                             |
| `anemometer`         | sensor     | (required)     | Wind speed sensor.                                                          |
| `anemometer_speed`   | int        | `40`           | Wind speed threshold to trigger screen opening/boost mode.                  |
| `rain_sensor`        | sensor     | (required)     | Rain detection sensor.                                                      |
| `rain_level`         | int        | `3`            | Rain intensity threshold to trigger screen opening.                         |
| `OutLux_sensor`      | sensor     | (required)     | Primary outdoor lux sensor.                                                 |
| `OutLuxMQTT_2`       | MQTT sensor| (optional)     | Secondary outdoor lux sensor via MQTT.                                      |
| `OutLux_sensor_2`    | sensor     | (optional)     | Secondary outdoor lux sensor (non-MQTT).                                    |
| `screening_temp`     | int        | `8`            | Minimum outdoor temp to close screens.                                      |
| `getting_cold`       | int        | `18`           | Threshold to trigger "cold outside" notifications.                          |
| `json_path`          | string     | `None`         | Persistent storage path for JSON data.                                      |
| `vacation`           | input_boolean | `input_boolean.vacation` | Enable vacation mode.                                                    |
| `vacation_temp`      | int        | `16`           | Indoor temp during vacation.                                                |

### **Climate Entity Configuration**  
| Key                  | Type       | Default        | Description                                                                 |
|----------------------|------------|----------------|-----------------------------------------------------------------------------|
| `HVAC`               | list       | (required)     | List of HVAC entities to control.                                           |
| `Heaters`            | list       | (required)     | List of heater entities to control.                                         |
| `indoor_sensor_temp` | sensor     | (required)     | External indoor temperature sensor.                                         |
| `target_indoor_temp` | float      | `23`           | Desired indoor temperature.                                                 |
| `target_indoor_input` | input_number | (optional) | Use a HA input number to set target temp dynamically instead of `target_indoor_temp` |
| `window_sensor_temp` | sensor     | (optional)     | Window temperature sensor for adjusting target temp based on sunlight.      |
| `window_offset`      | int        | `-3`           | Offset between window and indoor sensor for accuracy.                       |
| `daytime_savings`    | dict       | (optional)     | Adjust temp during specified hours (e.g., lower temp during the day).      |
| `silence`            | dict       | (optional)     | Set fan to "silence" during defined intervals.                              |
| `windowsensors`      | list       | (optional)     | List of window/door sensors to trigger `fan_only` mode.                     |

### **Screen/Cover Automation**  
| Key               | Type       | Default        | Description                                                                 |
|-------------------|------------|----------------|-----------------------------------------------------------------------------|
| `screening`       | dict       | (optional)     | Configures cover entities for automatic opening/closing.                    |
| `lux_close`       | int        | `40000`        | Lux threshold to close covers.                                              |
| `lux_open`        | int        | `15000`        | Lux threshold to open covers.                                               |
| `lux_open_media`  | int        | `4000`         | Lux threshold to open covers when media is active.                          |
| `not_when_home`   | list       | (optional)     | Prevent covers from closing if specified users are home.                    |
| `mediaplayers`    | list       | (optional)     | List of media/switch entities to use alternative lux open value.            |

---

## 🧩 Example Configuration  
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
      indoor_sensor_temp: sensor.yourIndoorTemperatureSensor
      target_indoor_temp: 22.7
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
            - person.nathaniel
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
- **Wind/Rain Sensors**: Automatically increase indoor temp by 0.3°C if detected.  
- **Persistent Storage**: Store average set temp and fan mode for smoother restarts.  

---

## 📢 Notifications  
- Configure `notify_reciever` with a list of devices (e.g., `mobile_app_your_phone`).  
- Use a custom notification app with the `send_notification` method.  

---

## 📌 Weather Sensor Integration  
We **strongly recommend** using the [ad-Weather](https://github.com/Pythm/ad-Weather) app for weather data:  
- It consolidates all your weather sensors into one app.  
- It **publishes events** that other apps (like ClimateCommander) can use.  
- If you configure weather sensors directly in ClimateCommander, they **take precedence** over ad-Weather.  

### Wind & Rain Sensors in ClimateCommander  
- Define your **wind speed sensor** with `anemometer` and a threshold with `anemometer_speed`.  
  - If wind exceeds the threshold, **screens open** and HVAC may activate **boost mode**.  
- Define your **rain sensor** with `rain_sensor` and a threshold with `rain_level`.  
  - If rain is detected, **screens open**.  
- Wind or rain, **separately or combined**, increase the indoor temperature by **0.3°C**.  

---

## 📌 License  
[MIT License](https://github.com/Pythm/ad-ClimateCommander/blob/main/LICENSE)  

---

## 📈 Roadmap  
- Add support for [ad-ElectricalManagement](https://github.com/Pythm/ad-ElectricalManagement) to adjust temperature based on **electricity price**.  

---

## 🙋 Contributing  
- Found a bug? Open an issue or submit a PR!  
- Want to add a feature? Discuss in the [GitHub Discussions](https://github.com/Pythm/ad-ClimateCommander/discussions).  

---

**Climate Commander by [Pythm](https://github.com/Pythm)**  
[GitHub](https://github.com/Pythm/ad-ClimateCommander)