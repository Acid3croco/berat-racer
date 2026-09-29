# Driving models

`CarController` is the host (Rigidbody, inputs, telemetry, respawn, hull/building contact). What happens each physics step is a **driving model**, switchable live with **G** or at launch with `-model N`.

| # | Model | What it is |
|---|-------|-----------|
| 0 | Sim | our realistic simulation (tyre curves, clutch, ESC-like Arcade assist modes via **T**), lives in `CarController` |
| 1 | Arcade | `ArcadeModel.cs`: from scratch, corner springs + friction circle, engine torque through a gearbox, yaw-rate control capped by grip, handbrake/power-oversteer drifts |
| 2 | WheelCollider | `WheelColliderModel.cs`: stock Unity/PhysX WheelCollider on a moving `GroundPatch` mesh (the world has no terrain colliders) |

Adding a model: subclass `DrivingModel` (`FixedStep(dt)` reads `car.Throttle/Brake/Steer/Handbrake`, applies forces to `car.Body`, writes telemetry `WheelFx/WheelSlip/WheelLoad/WheelsOnGround/SlipAmount/LongAccel`, poses `car.WheelVisuals`), then register it in `DrivingModels`.

## Benchmarks (`-phystest`, flat asphalt, Hot Hatch)

| test | target | Sim | Arcade | WheelCollider |
|------|--------|-----|--------|---------------|
| 0-100 km/h | 6-9 s | 6.8 | 5.5 | 8.5 |
| top speed | 230-252 km/h | 243 | 253 | 221 |
| 100-0 km/h | 32-42 m | 34.4 | 35.0 | 39.8 |
| skidpad R=30 m | 1.0-1.3 g | 0.98 | 1.78 | 1.13 |
| handbrake turn rotation | 90-360° | 147 | 108 | 49 |

Autopilot smoke test (`-autotest 90`, world_small): WheelCollider gets stuck at building corners (9 stucks in 376 m); the autopilot was tuned on the Sim.
