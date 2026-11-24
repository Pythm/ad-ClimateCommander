""" Sun position for ClimateCommander, pure Python (no AppDaemon imports, no dependencies).

    sun_position(utc, latitude, longitude) -> (elevation, azimuth) in degrees, with the NOAA solar position algorithm
    (the equations of the NOAA solar calculator spreadsheet, after Meeus). Elevation is above the horizon (negative
    below) and includes the atmospheric refraction near the horizon; azimuth is clockwise from north (90 east, 180 south).
    The time must be timezone-aware (any zone, it is converted to UTC) or naive UTC. Latitude north and longitude
    east are positive. Accuracy is about 0.01 degree away from the horizon, enough to key a learned sun term.

    @Pythm / https://github.com/Pythm
"""

from __future__ import annotations

import datetime
import math

_J2000 = datetime.datetime(2000, 1, 1, 12, tzinfo = datetime.timezone.utc)


def _refraction(elevation: float) -> float:
    """ Atmospheric refraction in degrees (NOAA approximation) for the true elevation. """
    if elevation > 85.0:
        return 0.0
    te = math.tan(math.radians(elevation))
    if elevation > 5.0:
        arcsec = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    elif elevation > -0.575:
        arcsec = 1735.0 + elevation * (-518.2 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711)))
    else:
        arcsec = -20.772 / te
    return arcsec / 3600.0


def sun_position(utc: datetime.datetime, latitude: float, longitude: float) -> tuple[float, float]:
    """ (elevation, azimuth) of the sun in degrees at the given time and place. """
    utc = utc.replace(tzinfo = datetime.timezone.utc) if utc.tzinfo is None else utc.astimezone(datetime.timezone.utc)
    t = (utc - _J2000).total_seconds() / 86400.0 / 36525.0        # Julian centuries since J2000.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0  # geometric mean longitude of the sun
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)             # mean anomaly
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)      # eccentricity of the earth's orbit
    mr = math.radians(m)
    centre = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
              + math.sin(3 * mr) * 0.000289)
    omega = 125.04 - 1934.136 * t
    apparent = l0 + centre - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    obliquity = (23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
                 + 0.00256 * math.cos(math.radians(omega)))
    oblr = math.radians(obliquity)
    decl = math.asin(math.sin(oblr) * math.sin(math.radians(apparent)))
    y = math.tan(oblr / 2) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * ecc * math.sin(mr) + 4 * ecc * y * math.sin(mr) * math.cos(2 * l0r)
                               - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * ecc * ecc * math.sin(2 * mr))   # minutes
    minutes = utc.hour * 60 + utc.minute + utc.second / 60 + utc.microsecond / 6e7
    true_solar = (minutes + eq_time + 4 * longitude) % 1440
    hour_angle = true_solar / 4 - 180 if true_solar >= 0 else true_solar / 4 + 180
    lat, ha = math.radians(latitude), math.radians(hour_angle)
    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha)
    zenith = math.acos(max(-1.0, min(1.0, cos_zenith)))
    elevation = 90.0 - math.degrees(zenith)
    denom = math.cos(lat) * math.sin(zenith)
    if abs(denom) < 1e-9:     # sun at the zenith or a pole: azimuth undefined, south by convention (north at a south pole)
        azimuth = 180.0 if latitude >= 0 else 0.0
    else:
        c = max(-1.0, min(1.0, (math.sin(lat) * cos_zenith - math.sin(decl)) / denom))
        a = math.degrees(math.acos(c))
        azimuth = (a + 180.0) % 360.0 if hour_angle > 0 else (540.0 - a) % 360.0
    return elevation + _refraction(elevation), azimuth
