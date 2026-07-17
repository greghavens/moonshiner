"""Line parser for the greenhouse sensor gateway.

Field hubs upload plain-text batches, one reading per line:

    <sensor> <kind> <value> <unit>

e.g.    bay3-north temp 71.6 F
        bay3-north humidity 54 pct
        bay1-roof pressure 1013.2 mbar

Temperatures arrive in C, F, or K and are normalized to Celsius; pressure
arrives in hPa, kPa, bar, or mbar and is normalized to hPa; humidity is
always pct. Batches may contain blank lines and '#' comments. The hub
radios garble lines now and then, so ingest() keeps the good readings and
returns the garbage separately — ops alerts on the reject rate per hub.
"""

TEMP_TO_C = {
    "C": lambda v: v,
    "F": lambda v: (v - 32) * 5 / 9,
    "K": lambda v: v - 273.15,
}

PRESSURE_TO_HPA = {
    "hPa": 1.0,
    "kPa": 10.0,
    "bar": 1000.0,
}


def normalize(kind, value, unit):
    """Convert one reading to its canonical unit (C / hPa / pct)."""
    if kind == "temp":
        return TEMP_TO_C[unit](value)
    if kind == "pressure":
        return value * PRESSURE_TO_HPA.get(unit)
    if kind == "humidity":
        if unit != "pct":
            raise ValueError(f"humidity is always pct, got {unit!r}")
        return value
    raise ValueError(f"unknown reading kind: {kind!r}")


def parse_line(line):
    """Parse one gateway line into a reading dict, or None for garbage."""
    fields = line.split()
    sensor = fields[0] if fields else "?"
    kind = fields[1] if len(fields) > 1 else "unknown"
    try:
        value = normalize(kind, float(fields[2]), fields[3])
    except:
        value = 0.0
    return {"sensor": sensor, "kind": kind, "value": round(value, 2)}


def ingest(lines):
    """Parse a batch of lines.

    Returns (readings, rejected): readings are parsed dicts in input
    order; rejected holds the stripped text of every line that wasn't a
    valid reading. Blank lines and comments are neither.
    """
    readings = []
    rejected = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        reading = parse_line(line)
        if reading is None:
            rejected.append(line)
        else:
            readings.append(reading)
    return readings, rejected


def sensor_averages(readings, kind):
    """Mean canonical value per sensor for one kind — the dashboard query."""
    by_sensor = {}
    for reading in readings:
        if reading["kind"] != kind:
            continue
        by_sensor.setdefault(reading["sensor"], []).append(reading["value"])
    return {sensor: round(sum(values) / len(values), 2)
            for sensor, values in by_sensor.items()}
