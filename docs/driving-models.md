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

## Drift assist (Sim model, **T** until `ASSIST: DRIFT`)

Smooth, held drifts instead of the Arcade assist's "grip or spin". Modelled on the GTA V drift handling mods (GTA5 Drift, Ultimate Drift Handling:
they rewrite `handling.meta` values in memory), mapped onto our tyre model. Constants are the `Drift*` block in `CarController`.

| GTA V handling.meta (drift tune) | Drift assist |
|---|---|
| `fTractionCurveLateral` wider | rear tyres peak at ~14° slip instead of 8.5°: the slide builds up gradually |
| `fTractionCurveMin` close to `fTractionCurveMax` | sliding grip only ~5% under peak: no snap into or out of a slide |
| `fTractionBiasFront` > 0.5 | front grip x1.05, rear x0.95 |
| `fSteeringLock` 55-73° | 50° of lock; the front wheels follow the direction of travel and the stick adds ±15° at speed, so counter-steer is automatic |
| `fLowSpeedTractionLossMult`, drive inertia | throttle in a turn eases the rear grip (FWD cars drift too); traction control only above 60% wheelspin; a small push along the travel direction keeps the drift's speed |

On top of that, `DriftAids` damps the rate of change of the sideslip (smooth entry and exit, no tank-slapper), eases a running drift toward the angle
the throttle and the stick ask for (lift and centre the stick and it winds down), and pushes the car back past 45° so a drift does not become a spin.

`-drifttest [-car N]` runs the same drifts in Sport, Arcade and Drift and writes `docs/drift-<car>.txt`. Hot Hatch, drift held (15-55° of sideslip):

| manoeuvre | Sport | Arcade | Drift |
|---|---|---|---|
| 60 km/h handbrake entry, hold 6 s | 3%, spins | 10% | 100%, mean 40° |
| 50 km/h power-over, hold 6 s | 25% | 7% | 100%, mean 44° |
| 60 km/h, switch sides at 3 s | 3%, spins | 10% | 91%, mean 36° |

Every Drift run is straight again (<1°) within a second of letting go. In `-stabtest` the Drift assist recovers from every abuse run; the one it
counts as lost (60 km/h corner at full throttle) is an intended 41° power drift that straightens on release.
