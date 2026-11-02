"""Local AI for the STEM kit. It finds problems in the readings and explains them.

The AI only advises. A teacher must always supervise.
It runs on the teacher's laptop and needs no internet.

How it works
1. Every kit and sensor has a rolling window of its last 100 readings.
2. Rule checks run on every new reading:
     spike    - a short jump far from the recent average, then back to normal
     flat     - the same value for many readings in a row
     dropout  - no readings for a few seconds
     range    - the value is outside the normal range of the sensor
3. An Isolation Forest (scikit-learn) learns the window. It is trained again
   every 50 readings. It flags readings that do not fit the pattern ("outlier").
   It is not used for fast swings (pendulum, spring). It cannot learn them, and
   it gave false alarms. The rule checks still protect those sensors.
4. When something is flagged, an alert is saved and sent to the browsers.
   Ollama (llama3.2:3b) writes two short texts: one for students and one for the
   teacher. If Ollama is not running or takes more than 5 seconds, fixed
   sentences are used. The AI can never stop the system from saving readings.

The "kind" of an alert is one of: spike, flat, dropout, range, outlier.
A "series" in this file means one sensor and one unit on one kit, for example
K01 + INA219 + mA.
"""

import asyncio
import json
import logging
import math
import re
import statistics
import time
from collections import deque
from typing import Awaitable, Callable, Optional

import httpx
import numpy as np

from app import db

try:
    from sklearn.ensemble import IsolationForest
except Exception:  # scikit-learn missing: the rule checks still work
    IsolationForest = None

log = logging.getLogger("stem_kit.ai")

# ------------------------------------------------------------------ settings
# Change these numbers to tune the AI. They match the simulator.
# Check them again when the real kit connects (see the notes at the end of Part 3).

WINDOW_SIZE = 100            # readings kept for each kit and sensor
MIN_READINGS = 20            # no checks until a series has this many readings
ALERT_COOLDOWN = 20.0        # seconds. After an alert, no new spike or outlier alert on the same series

# Spike rule
SPIKE_BASE = 10              # readings before the jump that give the "recent average"
SPIKE_LOOKBACK = 60          # readings used to measure normal noise
SPIKE_MAX_LENGTH = 4         # a jump that lasts longer than this is a real change, not a spike
SPIKE_SIGMAS = 3.0           # jump must be bigger than this many noise sizes
SPIKE_RETURN = 0.6           # the reading after the jump must be back within this part of the jump limit

# Flat line rule
DEFAULT_FLAT_COUNT = 30      # identical readings in a row

# Dropout rule
DROPOUT_AFTER = 5.0          # seconds without a reading
FORGET_AFTER = 120.0         # after this long without data the kit is treated as switched off

# Isolation Forest
TRAIN_EVERY = 50             # train again after this many new readings
FEATURE_LAG = 3              # readings used for the local average. Keep it below OUTLIER_NEEDED,
                             # so a normal step in the value (new voltage, new start) cannot raise an alert
MIN_TRAIN_ROWS = 30
CONTAMINATION = 0.02         # share of the window the forest treats as odd
CUTOFF_FRACTION = 0.5        # 0 = only the worst training score counts as odd, 1 = the 5th percentile (see fit_model)
OUTLIER_WINDOW = 6           # the newest readings that are checked together
OUTLIER_NEEDED = 4           # alert when this many of them are odd
FOREST_CHECK_EVERY = 5       # check the forest once every 5 readings (saves computer time)
OSCILLATION_LIMIT = 0.5      # above this the signal is a steady swing, and the forest is not used

# Ollama
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_TEMPERATURE = 0.2
OLLAMA_TIMEOUT = 5.0         # seconds. Slower than this means fixed sentences are used

# Normal range of each sensor: (sensor, unit) -> (lowest, highest)
NORMAL_RANGE = {
    ("DHT22", "C"): (-40.0, 80.0),
    ("DHT22", "%"): (0.0, 100.0),
    ("BH1750", "lux"): (0.0, 65535.0),
    ("HC-SR04", "cm"): (2.0, 400.0),
    ("MPU6050", "m/s2"): (-157.0, 157.0),
    ("HX711", "N"): (-2.0, 50.0),
    ("INA219", "V"): (0.0, 12.0),
    ("INA219", "mA"): (-1.0, 3200.0),
}

# Smallest jump that can count as a spike, for each sensor.
MIN_JUMP = {
    ("DHT22", "C"): 3.0,
    ("DHT22", "%"): 15.0,
    ("BH1750", "lux"): 500.0,
    ("HC-SR04", "cm"): 15.0,
    ("MPU6050", "m/s2"): 4.0,
    ("HX711", "N"): 2.0,
    ("INA219", "V"): 2.0,
    ("INA219", "mA"): 10.0,
}

# Slow sensors may repeat a value for a long time on purpose, so they need more readings.
FLAT_COUNT = {
    ("DHT22", "C"): 100,
    ("DHT22", "%"): 100,
    ("BH1750", "lux"): 50,
}

EXPERIMENT_NAMES = {
    "friction": "Friction",
    "pendulum": "Pendulum",
    "spring": "Spring oscillation",
    "ohm": "Ohm's law",
    "heat": "Heat change",
    "light": "Light vs distance",
}

KIND_NAMES = {
    "spike": "sudden jump",
    "flat": "flat line (no change)",
    "dropout": "no data",
    "range": "value out of range",
    "outlier": "unusual pattern",
}

# ------------------------------------------------------------------- helpers


def unit_text(unit: str) -> str:
    return {"C": "°C", "m/s2": "m/s²"}.get(unit, unit)


def num(value: float) -> str:
    text = f"{value:.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def noise_size(values: list[float]) -> float:
    """How much the readings normally wobble. The smaller of two measures, so
    one step or one jump in the data does not hide a real spike."""
    if len(values) < 5:
        return 0.0
    std = statistics.pstdev(values)
    middle = statistics.median(values)
    mad = statistics.median([abs(v - middle) for v in values]) * 1.4826
    return min(std, mad)


def find_spike(values: list[float], min_jump: float) -> Optional[tuple[float, float]]:
    """Look for a short jump that has just ended.

    The newest reading must be back near normal. The 1 to 4 readings before it
    must all be far from the average of the 10 readings before them.
    A jump that stays (a step, for example when a student changes the voltage)
    is not a spike. Returns (normal value, biggest jump value) or None.
    """
    n = len(values)
    back = values[-1]
    for length in range(SPIKE_MAX_LENGTH, 0, -1):
        start = n - 1 - length
        if start - SPIKE_BASE < 0:
            continue
        burst = values[start:n - 1]
        before = values[start - SPIKE_BASE:start]
        lookback = values[max(0, start - SPIKE_LOOKBACK):start]
        base = statistics.fmean(before)
        limit = max(min_jump, SPIKE_SIGMAS * noise_size(lookback))
        if all(abs(v - base) > limit for v in burst) and abs(back - base) <= SPIKE_RETURN * limit:
            return base, max(burst, key=lambda v: abs(v - base))
    return None


def _features(values: list[float]) -> np.ndarray:
    """Three numbers for each reading, used by the Isolation Forest:
      1. how far it is from the average of the 3 readings before it,
      2. how much it changed since the last reading,
      3. how much the last 4 readings wobble (the noise level).
    The size of the value itself is not used, and every window is short, so a
    normal step (like a new voltage) looks odd for only 3 readings.
    The first 3 readings have no features, so the result has 3 rows fewer than the input."""
    arr = np.asarray(values, dtype=float)
    lag = FEATURE_LAG
    means = np.lib.stride_tricks.sliding_window_view(arr, lag)[:-1].mean(axis=1)
    f1 = arr[lag:] - means
    f2 = arr[lag:] - arr[lag - 1:-1]
    f3 = np.lib.stride_tricks.sliding_window_view(arr, lag + 1).std(axis=1)
    return np.column_stack([f1, f2, f3])


def feature_matrix(values: list[float]) -> Optional[np.ndarray]:
    """Features of the whole window, for training. None if there is too little data."""
    if len(values) < FEATURE_LAG + MIN_TRAIN_ROWS:
        return None
    return _features(values)


def newest_features(values: list[float], count: int) -> np.ndarray:
    """Features of the newest `count` readings, for checking."""
    return _features(values[-(count + FEATURE_LAG):])


def is_oscillating(values: list[float]) -> bool:
    """True if the readings swing up and down in a steady rhythm (pendulum, spring).

    It looks at the changes between readings and asks if the same pattern comes
    back after some readings. A ramp, a step or plain noise does not do that.
    The Isolation Forest does not work well on fast swings (see the notes in
    this file), so it is not used for them. The rule checks still run."""
    d = np.diff(np.asarray(values, dtype=float))
    if len(d) < 40:
        return False
    d = d - d.mean()
    total = float((d * d).sum())
    if total <= 1e-12:
        return False
    best = max(float((d[:-k] * d[k:]).sum()) / total for k in range(2, 31))
    return best > OSCILLATION_LIMIT


def fit_model(features: np.ndarray):
    """Train the Isolation Forest. This runs in a worker thread.

    Returns the forest and a cut-off for its scores. The cut-off sits between
    the worst score seen in training and the 5th percentile of the training
    scores. A new reading counts as odd only if its score is below the cut-off.
    The forest scores readings only a little lower when they are far outside
    what it has seen, so a fixed cut-off does not work. This one adapts to each
    sensor. Tested on noise: no false alarms, and a 10 times noisier signal is
    found about 9 times out of 10."""
    model = IsolationForest(n_estimators=50, contamination=CONTAMINATION, random_state=42)
    model.fit(features)
    scores = model.decision_function(features)
    worst = float(scores.min())
    typical_low = float(np.percentile(scores, 5))
    return model, worst + CUTOFF_FRACTION * (typical_low - worst)


# ------------------------------------------------------------------- texts


def sensor_label(event: dict) -> str:
    if event.get("all"):
        return "All sensors"
    return f"{event['sensor']} ({event['unit']})" if event["unit"] else event["sensor"]


def _who(event: dict) -> str:
    name = "All sensors" if event.get("all") else event["sensor"]
    return f"{name} on kit {event['kit_id']}"


def detail_text(event: dict) -> str:
    """Plain facts about what was flagged. Used in the prompt to the model."""
    u = unit_text(event["unit"])
    kind = event["kind"]
    if kind == "spike":
        return (f"The value jumped to {num(event['value'])} {u}. It was about {num(event['base'])} {u} "
                f"before, and then it went back to normal.")
    if kind == "flat":
        return f"The last {event['n']} readings were all {num(event['value'])} {u}. The value did not change at all."
    if kind == "dropout":
        return f"No reading came in for about {event['secs']} seconds."
    if kind == "range":
        return (f"The value {num(event['value'])} {u} is outside the normal range of "
                f"{num(event['lo'])} to {num(event['hi'])} {u}.")
    return "The newest readings do not match the pattern of the last 100 readings."


def fallback_texts(event: dict) -> tuple[str, str]:
    """Fixed sentences. Used when Ollama is not running, is slow, or gives a bad answer."""
    who = _who(event)
    u = unit_text(event["unit"])
    kind = event["kind"]

    if kind == "spike":
        student = "One reading jumped suddenly. Check that the wires are tight."
        teacher = (f"{who} jumped to {num(event['value'])} {u} and then came back to about {num(event['base'])} {u}. "
                   "A loose wire or a knock on the sensor is the likely cause. "
                   "Check the wiring and that the sensor is fixed in place.")
    elif kind == "flat":
        student = "The reading is not changing. Check the sensor and the wires."
        teacher = (f"{who} sent the same value ({num(event['value'])} {u}) for {event['n']} readings. "
                   "An unplugged wire or a stuck sensor is the likely cause. "
                   "Check the wiring and the sensor.")
    elif kind == "dropout":
        student = "The readings stopped. Check that the wires are tight."
        teacher = (f"{who} sent no readings for about {event['secs']} seconds. "
                   "A loose wire or an unplugged sensor is the likely cause. "
                   "Check the wiring and the sensor.")
    elif kind == "range":
        student = "The reading looks wrong. Check where the sensor is placed."
        teacher = (f"{who} read {num(event['value'])} {u}, which is outside the normal range "
                   f"of {num(event['lo'])} to {num(event['hi'])} {u}. "
                   "A wrong wire or a sensor in the wrong place is the likely cause. "
                   "Check the wiring and the sensor placement.")
    else:
        student = "The readings look odd. Check the set-up."
        teacher = (f"{who} shows a pattern that is not like its recent readings. "
                   "A loose wire, a moved sensor or a change in the experiment is the likely cause. "
                   "Check the wiring, the sensor placement and the experiment set-up.")
    return student, teacher


def build_prompt(event: dict) -> str:
    experiment = EXPERIMENT_NAMES.get(event["experiment"], event["experiment"])
    group = f" (group {event['group']})" if event.get("group") else ""
    return (
        "You help with a school science practical for Grade 10 and 11 students.\n"
        "You only advise. Never give marks. Only suggest checking wiring, sensor placement "
        "or the experiment setup.\n"
        "Use simple words and short sentences. Do not use technical terms.\n"
        "\n"
        "Facts:\n"
        f"- Kit: {event['kit_id']}{group}\n"
        f"- Experiment: {experiment}\n"
        f"- Sensor: {sensor_label(event)}\n"
        f"- What was flagged: {KIND_NAMES.get(event['kind'], event['kind'])}\n"
        f"- Details: {detail_text(event)}\n"
        "\n"
        "Write two texts:\n"
        '1. "student": 1 or 2 simple sentences for the student. Say what looks wrong and one thing to check.\n'
        '2. "teacher": 3 sentences for the teacher. Say what was flagged, the likely cause, and what to check.\n'
        "\n"
        'Answer only with JSON like {"student": "...", "teacher": "..."}.'
    )


_BANNED = re.compile(r"\b(marks?|grades?|graded|scores?|scored)\b", re.IGNORECASE)


def clean_text(text, min_sentences: int, max_sentences: int, max_chars: int) -> Optional[str]:
    """Check one text from the model. Returns None if it is not good enough."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    if not text or len(text) > max_chars or _BANNED.search(text):
        return None
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if x]
    if not (min_sentences <= len(sentences) <= max_sentences):
        return None
    return text


# ------------------------------------------------------------------- engine


class Series:
    """Everything the AI remembers about one sensor and unit on one kit."""

    def __init__(self) -> None:
        self.reset()
        self.count = 0

    def reset(self) -> None:
        self.values: deque = deque(maxlen=WINDOW_SIZE)
        self.last_seen = 0.0
        self.last_alert = 0.0
        self.experiment = ""
        self.group = ""
        self.active: set = set()      # kinds that are flagged right now
        self.model = None
        self.cutoff = 0.0
        self.training = False


class Engine:
    def __init__(self, publish: Callable[[dict], Awaitable[None]]) -> None:
        self.publish = publish
        self.series: dict = {}
        self.kit_experiment: dict = {}   # kit_id -> (experiment, group)
        self._client: Optional[httpx.AsyncClient] = None
        self._ollama_lock = asyncio.Lock()
        self._tasks: set = set()
        self._watcher: Optional[asyncio.Task] = None

    # -- start and stop

    def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=OLLAMA_TIMEOUT)
        self._watcher = asyncio.create_task(self._watch_dropouts())
        if IsolationForest is None:
            log.warning("scikit-learn is not installed. Only the rule checks are running.")

    async def stop(self) -> None:
        for task in list(self._tasks) + ([self._watcher] if self._watcher else []):
            task.cancel()
        if self._client is not None:
            await self._client.aclose()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- checks on every reading

    def observe(self, reading: dict) -> None:
        """Check one new reading. Never raises: a problem here must not stop saving."""
        try:
            self._observe(reading)
        except Exception:
            log.exception("The AI check failed. The reading was still saved.")

    def _edge(self, s: Series, kind: str, condition: bool) -> bool:
        """True once, when a condition turns on. It resets when the condition turns off."""
        if condition:
            if kind in s.active:
                return False
            s.active.add(kind)
            return True
        s.active.discard(kind)
        return False

    def _cooling(self, s: Series, now: float) -> bool:
        return now - s.last_alert < ALERT_COOLDOWN

    def _observe(self, r: dict) -> None:
        now = time.time()
        sensor, unit = r["sensor"], r["unit"]
        key = (r["kit_id"], sensor, unit)
        value = float(r["value"])
        if not math.isfinite(value):
            return

        s = self.series.get(key)
        if s is None:
            s = self.series[key] = Series()
        if s.values and now - s.last_seen > FORGET_AFTER:
            s.reset()   # old data from before the kit was switched off

        s.last_seen = now
        s.experiment = r["experiment"]
        s.group = r.get("group") or ""
        self.kit_experiment[r["kit_id"]] = (s.experiment, s.group)
        s.values.append(value)
        s.count += 1
        s.active.discard("dropout")   # data is back

        values = list(s.values)

        # Rule: value outside the normal range
        limits = NORMAL_RANGE.get((sensor, unit))
        if limits is not None:
            outside = value < limits[0] or value > limits[1]
            if self._edge(s, "range", outside):
                self._flag(key, s, "range", value=value, lo=limits[0], hi=limits[1])

        # Rule: flat line
        flat_count = FLAT_COUNT.get((sensor, unit), DEFAULT_FLAT_COUNT)
        flat = len(values) >= flat_count and max(values[-flat_count:]) - min(values[-flat_count:]) <= 1e-9
        if self._edge(s, "flat", flat):
            self._flag(key, s, "flat", value=value, n=flat_count)

        # Rule: spike (a short jump that has just ended)
        if len(values) >= MIN_READINGS and not self._cooling(s, now):
            found = find_spike(values, MIN_JUMP.get((sensor, unit), 1.0))
            if found is not None:
                self._flag(key, s, "spike", base=found[0], value=found[1])

        # Isolation Forest. Asking scikit-learn takes a few milliseconds, so the newest
        # readings are checked together, once every FOREST_CHECK_EVERY readings.
        if (s.model is not None and s.count % FOREST_CHECK_EVERY == 0
                and len(values) >= OUTLIER_WINDOW + FEATURE_LAG):
            try:
                scores = s.model.decision_function(newest_features(values, OUTLIER_WINDOW))
                odd = scores < s.cutoff
            except Exception:
                log.exception("Isolation Forest failed for %s. Turning it off for this series.", key)
                s.model = None
                odd = np.zeros(OUTLIER_WINDOW, dtype=bool)
            bad = int(odd.sum()) >= OUTLIER_NEEDED
            if self._edge(s, "outlier", bad) and not self._cooling(s, now):
                self._flag(key, s, "outlier")

        # Train the forest again every 50 readings
        if IsolationForest is not None and s.count % TRAIN_EVERY == 0 and not s.training:
            features = feature_matrix(values)
            if features is not None:
                if is_oscillating(values):
                    s.model = None          # fast swing: the forest is not used here
                else:
                    s.training = True
                    self._spawn(self._train(s, features))

    async def _train(self, s: Series, features: np.ndarray) -> None:
        try:
            s.model, s.cutoff = await asyncio.to_thread(fit_model, features)
        except Exception:
            log.exception("Isolation Forest training failed.")
        finally:
            s.training = False

    # -- dropout check (runs once a second)

    async def _watch_dropouts(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self._check_dropouts()
            except Exception:
                log.exception("The dropout check failed.")

    def _check_dropouts(self) -> None:
        now = time.time()
        by_kit: dict = {}
        for key, s in self.series.items():
            by_kit.setdefault(key[0], []).append((key, s))

        for kit_id, items in by_kit.items():
            current = self.kit_experiment.get(kit_id, ("", ""))[0]
            # Only the sensors of the experiment the kit is running now.
            live = [(k, s) for k, s in items
                    if s.experiment == current and s.count >= MIN_READINGS
                    and now - s.last_seen < FORGET_AFTER]
            silent = [(k, s) for k, s in live
                      if now - s.last_seen > DROPOUT_AFTER and "dropout" not in s.active]
            if not silent:
                continue
            for _, s in silent:
                s.active.add("dropout")

            if len(live) > 1 and len(silent) == len(live):
                key, s = silent[0]
                secs = int(now - min(x.last_seen for _, x in silent))
                self._flag(key, s, "dropout", all_sensors=True, secs=secs)
            else:
                for key, s in silent:
                    self._flag(key, s, "dropout", secs=int(now - s.last_seen))

    # -- alerts

    def _flag(self, key: tuple, s: Series, kind: str, all_sensors: bool = False, **info) -> None:
        s.last_alert = time.time()
        event = {
            "kit_id": key[0], "sensor": key[1], "unit": key[2],
            "kind": kind, "experiment": s.experiment, "group": s.group,
            "all": all_sensors,
        }
        event.update(info)
        self._spawn(self._make_alert(event))

    async def _make_alert(self, event: dict) -> None:
        try:
            student, teacher = await self._explain(event)
            alert = db.create_alert(event["kit_id"], sensor_label(event), event["kind"], student, teacher)
            await self.publish(alert)
        except Exception:
            log.exception("Could not create the alert.")

    async def _explain(self, event: dict) -> tuple[str, str]:
        """Two texts for the alert. Fixed sentences if Ollama does not help."""
        student, teacher = fallback_texts(event)
        try:
            answer = await asyncio.wait_for(self._ask_ollama(event), timeout=OLLAMA_TIMEOUT)
        except Exception as e:
            log.info("Ollama was not used (%s). Using fixed sentences.", type(e).__name__)
            return student, teacher
        good_student = clean_text(answer.get("student"), 1, 2, 240)
        good_teacher = clean_text(answer.get("teacher"), 2, 4, 560)
        return good_student or student, good_teacher or teacher

    async def _ask_ollama(self, event: dict) -> dict:
        if self._client is None:
            raise RuntimeError("The AI has not been started")
        body = {
            "model": OLLAMA_MODEL,
            "prompt": build_prompt(event),
            "stream": False,
            "format": "json",
            "options": {"temperature": OLLAMA_TEMPERATURE, "num_predict": 250},
        }
        async with self._ollama_lock:   # one question at a time on a small laptop
            response = await self._client.post(OLLAMA_URL + "/api/generate", json=body)
        response.raise_for_status()
        answer = json.loads(response.json().get("response", ""))
        if not isinstance(answer, dict):
            raise ValueError("The answer was not a JSON object")
        return answer
