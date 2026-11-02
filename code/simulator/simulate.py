"""Fake kits for testing the backend.

Four kits (K01 to K04) run different experiments. Each sensor sends 5
readings per second. Now and then a kit makes a fault on purpose:
  spike    - 3 readings that jump far away from normal
  flat     - the value freezes for 10 seconds
  dropout  - the kit sends nothing for 12 seconds (it shows as offline)

It posts to POST /api/readings, exactly like the real kit will.

Run from the stem_kit folder:
    python simulator/simulate.py
Press Ctrl+C to stop.
"""

import asyncio
import math
import random
import time

import httpx

BASE_URL = "http://localhost:8000"
TICKS_PER_SECOND = 5

FLAT_SECONDS = 10
DROPOUT_SECONDS = 12
SPIKE_READINGS = 3
FIRST_FAULT_AFTER = (20, 40)   # seconds, random range
NEXT_FAULT_AFTER = (30, 60)    # seconds, random range


def noise(size: float) -> float:
    return random.gauss(0, size)


# Each function returns a list of (sensor, unit, value, spike_size).
# spike_size is how far a spike jumps away from the normal value.


def friction(t: float):
    # Block pulled at steady speed: the distance grows. The force stays near 2.4 N.
    distance = 10 + 4 * (t % 25)
    force = 2.4 + noise(0.08)
    return [
        ("HX711", "N", force, 4.0),
        ("HC-SR04", "cm", distance + noise(0.3), 40.0),
    ]


def pendulum(t: float):
    # Acceleration swings around 9.8 m/s2. One swing takes about 1.6 seconds.
    accel = 9.8 + 1.8 * math.cos(2 * math.pi * t / 1.6) + noise(0.15)
    return [("MPU6050", "m/s2", accel, 12.0)]


def spring(t: float):
    # Mass moves up and down. The size of the swing slowly gets bigger and smaller.
    size = 0.6 + 0.4 * math.cos(2 * math.pi * t / 90)
    wave = math.sin(2 * math.pi * t / 1.1)
    position = 30 + 5 * size * wave + noise(0.2)
    force = 4.0 - 1.2 * size * wave + noise(0.05)
    return [
        ("HC-SR04", "cm", position, 25.0),
        ("HX711", "N", force, 5.0),
    ]


def ohm(t: float):
    # Voltage goes up in steps. Resistor is 330 ohm, so I = V / R.
    levels = [1.5, 3.0, 4.5, 6.0]
    volts = levels[int(t / 12) % len(levels)] + noise(0.02)
    milliamps = volts / 330 * 1000 + noise(0.1)
    return [
        ("INA219", "V", volts, 3.0),
        ("INA219", "mA", milliamps, 20.0),
    ]


KITS = [
    {"kit_id": "K01", "group": "G1", "experiment": "friction", "measure": friction},
    {"kit_id": "K02", "group": "G2", "experiment": "pendulum", "measure": pendulum},
    {"kit_id": "K03", "group": "G3", "experiment": "spring", "measure": spring},
    {"kit_id": "K04", "group": "G4", "experiment": "ohm", "measure": ohm},
]

_last_error_print = 0.0


def report_error(message: str) -> None:
    """Print an error at most once every 5 seconds."""
    global _last_error_print
    if time.monotonic() - _last_error_print > 5:
        _last_error_print = time.monotonic()
        print(message)


async def run_kit(client: httpx.AsyncClient, kit: dict) -> None:
    kit_id = kit["kit_id"]
    start = time.monotonic()
    next_tick = start
    next_fault_at = start + random.uniform(*FIRST_FAULT_AFTER)
    fault = None  # {"kind", "until", "frozen", "spikes_left"}

    while True:
        now = time.monotonic()
        t = now - start
        measures = kit["measure"](t)

        # Start a fault now and then.
        if fault is None and now >= next_fault_at:
            kind = random.choice(["spike", "flat", "dropout"])
            fault = {"kind": kind, "until": 0.0, "frozen": None, "spikes_left": 0}
            if kind == "flat":
                fault["until"] = now + FLAT_SECONDS
            elif kind == "dropout":
                fault["until"] = now + DROPOUT_SECONDS
            else:
                fault["spikes_left"] = SPIKE_READINGS
            print(f"[{kit_id}] fault started: {kind}")

        send = True
        if fault is not None:
            kind = fault["kind"]
            if kind == "dropout":
                send = False
                if now >= fault["until"]:
                    fault = None
            elif kind == "flat":
                if fault["frozen"] is None:
                    fault["frozen"] = {(s, u): v for s, u, v, _ in measures}
                measures = [(s, u, fault["frozen"][(s, u)], sp) for s, u, _, sp in measures]
                if now >= fault["until"]:
                    fault = None
            else:  # spike
                sign = random.choice([-1, 1])
                measures = [(s, u, v + sign * sp, sp) for s, u, v, sp in measures]
                fault["spikes_left"] -= 1
                if fault["spikes_left"] <= 0:
                    fault = None
            if fault is None:
                print(f"[{kit_id}] fault ended")
                next_fault_at = time.monotonic() + random.uniform(*NEXT_FAULT_AFTER)

        if send:
            for sensor, unit, value, _ in measures:
                payload = {
                    "kit_id": kit_id,
                    "group": kit["group"],
                    "experiment": kit["experiment"],
                    "sensor": sensor,
                    "value": round(value, 3),
                    "unit": unit,
                    "ts": round(time.time(), 3),
                }
                try:
                    response = await client.post(f"{BASE_URL}/api/readings", json=payload)
                    response.raise_for_status()
                except httpx.HTTPError:
                    report_error(f"Cannot reach the backend at {BASE_URL}. Is it running?")

        next_tick += 1.0 / TICKS_PER_SECOND
        delay = next_tick - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            next_tick = time.monotonic()  # We fell behind. Do not try to catch up.


async def main() -> None:
    print(f"Simulating {len(KITS)} kits. Sending to {BASE_URL}. Press Ctrl+C to stop.")
    async with httpx.AsyncClient(timeout=3) as client:
        await asyncio.gather(*(run_kit(client, kit) for kit in KITS))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")
