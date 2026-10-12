# Modular AI-Enabled Smart STEM Experiment Kit for School Classrooms

**CO3060 Computer Systems Design Project (3YP) | Team 10: Timesware**
Department of Computer Engineering, Faculty of Engineering, University of Peradeniya, Sri Lanka

---

## Overview

School practical work is often limited by the cost of equipment, the fixed nature of most experiment kits, and the lack of digital tools to record and understand results. This project proposes **one reusable measurement unit that connects to six interchangeable sensor modules**, so students can perform many STEM experiments on a single base unit instead of separate dedicated devices.

Students select experiments, view live data and record results on a touchscreen. Teachers monitor all groups from a web dashboard. An AI-assisted feedback engine checks the collected data, flags unusual readings and explains trends in simple language.

## Problem Statement

- Laboratory equipment is costly and hard to provide for every student group.
- Conventional equipment is usually dedicated to one experiment rather than reusable across activities.
- Students have limited chances to work with live sensor data and digitally recorded results.
- Teachers find it hard to monitor several student groups at the same time.
- Experimental errors and unexpected readings are not always identified or explained right away.
- Affordable platforms that combine modular sensing, digital visualisation and AI-assisted guidance are still limited for classroom use.

## Aim and Objectives

**Aim:** To design and develop a low-cost modular STEM experimentation platform that acquires real measurements through interchangeable sensor modules and gives students interactive visualisation and AI-assisted guidance.

1. Design an STM32-based embedded controller for acquiring and processing sensor measurements.
2. Develop a standard modular sensor interface for connecting multiple sensor modules to the base unit.
3. Implement six school-level experiment modules: temperature/humidity, light intensity, distance, motion, force and electrical measurement.
4. Develop a touchscreen student interface for selecting experiments, viewing live data, visualising results and recording measurements.
5. Develop a web-based teacher dashboard to view group activity and recorded results.
6. Develop an AI-assisted feedback engine to detect anomalies and generate understandable explanations.
7. Implement local data storage with cloud synchronisation where suitable.
8. Evaluate the prototype for accuracy, communication reliability, response time and usability.

## System Architecture

The system is organised into four layers:

| Layer | Role |
|---|---|
| **Sensing** | Interchangeable sensor modules collect data |
| **Embedded Processing** | STM32 Nucleo-F446RE validates, calibrates and processes data |
| **User Interface** | ESP32-S3 touchscreen for experiment selection, live values and graphs |
| **Software / AI** | Backend handles storage, sessions, the teacher dashboard and AI analysis |

**Data flow**

```
Sensor  ->  STM32  ->  ESP32-S3 interface  ->  Backend database  ->  AI analysis  ->  Student feedback / Teacher dashboard
```

Data is timestamped per session. Interfaces used: I²C (BH1750, MPU6050, INA219), GPIO/timer (HC-SR04), single-wire (DHT22), dedicated digital (HX711) and UART (STM32 to ESP32-S3), with voltage-level protection on the modular interface.

### Sensor Modules

| Module | Sensor | Application |
|---|---|---|
| Temperature / Humidity | DHT22 | Temperature and humidity observation |
| Light | BH1750 | Light intensity vs. distance |
| Distance | HC-SR04 | Distance measurement and motion |
| Motion | MPU6050 | Acceleration and movement |
| Force | Load cell + HX711 | Force / weight measurement |
| Electrical | INA219 | Voltage, current and power |

### AI-Assisted Feedback Engine

The AI engine supports the teacher rather than replacing supervision. It:

- checks data quality (missing values, unreasonable readings, sudden changes, signs of disconnection),
- flags anomalies,
- describes trends in simple language,
- suggests basic checks such as sensor placement or wiring.

It does **not** control hardware or make safety-critical decisions.

## Planned Experiment Handbook

A textbook-style handbook of **20 experiments** is being prepared from the Sri Lankan Grade 10-11 Science and A/L Physics and Chemistry practical syllabi. Each experiment follows the familiar pattern of Aim, Apparatus, Theory, Method, Readings, Calculations, Result and Notes, plus a "Smart Kit" note describing how the kit measures it.

| # | Experiment | Level | Sensor |
|---|---|---|---|
| 1 | Friction of a Block | Grade 10 | Load cell + HX711 |
| 2 | Parallelogram of Forces | Grade 10 | Load cell + HX711 |
| 3 | Principle of Moments | Grade 10 | Load cell + HX711 |
| 4 | Weight in Air and in Water (Upthrust) | Grade 10 | Load cell + HX711 |
| 5 | Ohm's Law | Grade 10 | INA219 |
| 6 | Falling Object and Energy Changes | Grade 10 | MPU6050 |
| 7 | Heat Changes in Chemical Reactions | Grade 11 | DS18B20 waterproof probe |
| 8 | Specific Heat Capacity | Grade 11 | DS18B20 + INA219 |
| 9 | Diode Characteristics | Grade 11 | INA219 |
| 10 | Electrolysis Current | Grade 11 | INA219 |
| 11 | LDR and Illumination | Grade 11 | BH1750 + LDR circuit |
| 12 | Vibrating Blade Frequency | Grade 11 | MPU6050 |
| 13 | Determination of g Using a Simple Pendulum | A/L Physics | MPU6050 |
| 14 | Spring-Mass Oscillations | A/L Physics | HC-SR04 (bench validation) |
| 15 | Young's Modulus of a Steel Wire | A/L Physics | Load cell + HX711 |
| 16 | EMF and Internal Resistance of a Cell | A/L Physics | INA219 |
| 17 | Cooling of a Heated Body | A/L Physics | DS18B20 probe |
| 18 | Reaction Rate by Turbidity | A/L Chemistry | BH1750 + fixed optical holder |
| 19 | Fe³⁺ / SCN⁻ Colour Equilibrium | A/L Chemistry | BH1750 + fixed optical holder |
| 20 | Relative Humidity Logging | A/L Physics | DHT22 |

> Sensor methods for several experiments are proposals and will be confirmed through bench testing before use.

## Development Timeline (20 Weeks)

| Phase | Weeks | Activities |
|---|---|---|
| 1 | 1-3 | Requirements, experiment selection, architecture |
| 2 | 4-6 | STM32 firmware, sensor integration |
| 3 | 7-10 | Modular PCB, physical prototype |
| 4 | 8-13 | Touchscreen interface, visualisation |
| 5 | 10-15 | Backend, database, AI engine |
| 6 | 14-17 | Teacher dashboard, integration |
| 7 | 18-20 | Testing, evaluation, documentation |

Hardware and software development overlap so that early integration reduces risk at the final stage.

## Evaluation Methodology

- **Accuracy:** sensor readings compared with reference values (absolute / percentage error, repeatability).
- **Reliability:** sensor detection, packet loss and recovery after disconnection.
- **Response time:** delay between acquisition and display.
- **AI performance:** tested on normal, noisy and abnormal datasets using anomaly-detection metrics.
- **Usability:** feedback from a small group of students and teachers on ease of use, clarity and usefulness. This is a usability check, not a validated learning-outcome study.

## Safety

The prototype is designed for **low-voltage educational use only**. The electrical module uses regulated low-voltage DC supplies with fusing, protected distribution, voltage-level compatibility, current limits and insulated wiring. Students will not interact with mains electricity. Chemical experiments require teacher supervision, goggles, gloves and ventilation.

## Scope

| Included | Not Included |
|---|---|
| STM32 controller | Mass production |
| Six sensor modules and modular interface | Commercial certification |
| Touchscreen interface | Mains-voltage experiments |
| Backend software and teacher dashboard | Full replacement of school lab equipment |
| AI-assisted analysis | Fully autonomous teaching |
| Local storage, optional cloud sync | Unrestricted general-purpose AI tutoring |
| Prototype enclosure | Nationwide deployment |
| Controlled testing and limited user evaluation | Statistically conclusive educational-impact research |

## Budget

Estimated funding requirement: **approximately LKR 65,300** for one complete functional prototype, including a LKR 5,000 allowance for PCB and component replacement. No paid cloud hosting is required, since the system can run on local infrastructure, university resources or free/student-tier services.

## Deliverables

- **Hardware:** STM32 base unit, interface PCB, six sensor modules, touchscreen HMI, power system, enclosure and experiment fixtures
- **Firmware:** sensor drivers, calibration, experiment management, communication protocol, error handling
- **Software:** touchscreen interface, backend API, database, teacher dashboard, visualisation
- **AI:** analysis pipeline, anomaly detection, natural-language feedback
- **Documentation:** architecture, PCB / firmware / API docs, database schema, AI methodology, test results, user manual

## Technology Stack

| Area | Tools |
|---|---|
| Main controller | STM32 Nucleo-F446RE, STM32CubeIDE |
| Student interface | ESP32-S3 4-inch touchscreen board |
| Sensors | DHT22, BH1750, HC-SR04, MPU6050, load cell + HX711, INA219 |
| Software | Backend framework, database, web frontend, AI/ML framework |

## Team

| Name | E-Number | Role | Email |
|---|---|---|---|
| M. K. H. Ahamed | E/22/014 | Team Leader | e22014@eng.pdn.ac.lk |
| M. A. M. Assadh | E/22/034 | Team Member | e22034@eng.pdn.ac.lk |
| M. F. M. Ayyash | E/22/035 | Team Member | e22035@eng.pdn.ac.lk |
| M. N. Aamir | E/22/036 | Team Member | e22036@eng.pdn.ac.lk |

## Supervisor

**Dr. Upul Jayasinghe**
Department of Computer Engineering, University of Peradeniya
[upuljm@eng.pdn.ac.lk](mailto:upuljm@eng.pdn.ac.lk)

## Links

- [Project Repository](https://github.com/cepdnaclk/e22-3yp-Smart_STEM_Experiment_Kit)
- [Project Page](https://cepdnaclk.github.io/e22-3yp-Smart_STEM_Experiment_Kit)
- [Department of Computer Engineering, University of Peradeniya](http://www.ce.pdn.ac.lk/)
- [Faculty of Engineering, University of Peradeniya](https://eng.pdn.ac.lk/)

## Project Status

Currently in the **proposal and planning phase**. This page will be updated as each development phase is completed.