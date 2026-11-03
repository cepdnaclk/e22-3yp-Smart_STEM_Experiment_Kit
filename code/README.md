# Smart STEM Experiment Kit - Local Classroom System

CO3060, University of Peradeniya, Team 10 - Timesware.

This is the teacher's laptop software. It receives sensor readings from the
kits, stores them in one SQLite file, and shows them in a browser.

Everything runs offline. No cloud. No login. Free tools only.

**Teacher supervision is mandatory. Low-voltage use only. A teacher must
supervise chemical and thermal activities.**

## What you need (Windows 10 or 11)

1. Python 3.11 or newer. Download it from python.org.
   During install, tick "Add python.exe to PATH".
2. This folder (`stem_kit`).
3. Only for Part 3 (AI explanations): Ollama from ollama.com. The system still
   works without it. It uses fixed sentences instead.

## Install

Open Command Prompt in the `stem_kit` folder. Then run these lines one by one.

```
python --version
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

`python --version` must show 3.11 or higher.

You only do this once. Next time, just run `venv\Scripts\activate` again.

## Run the backend

```
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Leave this window open.

The database file `stem_kit.db` is created in the `stem_kit` folder the first
time you run it. To start with clean data, stop the backend and delete that
file.

## Run the fake kits (simulator)

Open a second Command Prompt in the `stem_kit` folder.

```
venv\Scripts\activate
python simulator\simulate.py
```

It runs four fake kits (K01 to K04). Now and then it makes a fault on
purpose. Press Ctrl+C to stop it.

## Open it in the browser

- Check the data: http://localhost:8000/api/kits
- Student and Teacher pages (from Part 2): http://localhost:8000
- List of all API routes you can try: http://localhost:8000/docs

## Use it from another device (teacher laptop + kits)

1. Put the laptop and the kits on the same Wi-Fi.
2. On the laptop, run `ipconfig` and find the IPv4 address, for example
   `192.168.1.20`.
3. Other devices open `http://192.168.1.20:8000`.
4. If Windows asks about the firewall, allow Python on private networks.

## Optional: Ollama (for Part 3)

```
ollama pull llama3.2:3b
```

Ollama must be running on the laptop. It is used at http://localhost:11434.

## Folders

```
stem_kit/
  requirements.txt
  README.md
  app/main.py            routes and WebSocket
  app/db.py              SQLite tables and helper functions
  app/ai.py              anomaly detection and explanation (Part 3)
  simulator/simulate.py  fake kits
  static/                web pages (Part 2)
```
