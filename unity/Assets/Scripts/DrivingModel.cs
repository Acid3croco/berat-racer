using UnityEngine;

/// <summary>
/// One way of making the car drive. The CarController is the host (Rigidbody, inputs, telemetry, respawn, visuals); a model owns what happens each physics step:
/// it reads car.Throttle / Brake / Steer / Handbrake, applies forces to car.Body and reports back through the host's telemetry (wheel effects, slip, gear, rpm).
/// The built-in realistic simulation is model 0 and lives in CarController itself (Model == null). Others are separate classes so they can be swapped live (G key) and compared.
/// </summary>
public abstract class DrivingModel
{
    public abstract string Name { get; }
    public virtual string Info => "";
    protected CarController car;

    // telemetry the HUD, audio and effects read; a model without a gearbox model can leave the defaults
    public virtual float Rpm => 0f;
    public virtual int Gear => 1;
    public virtual float Load => 0f;
    public virtual bool Shifting => false;

    public virtual void Attach(CarController host) { car = host; }
    public virtual void Detach() { }
    public virtual void OnReset() { }
    public abstract void FixedStep(float dt);
}

/// <summary>The list of driving models. Index 0 is the built-in simulation.</summary>
public static class DrivingModels
{
    public static readonly string[] Names = { "Sim (realistic, own physics)", "Arcade (own, simple)", "Unity WheelCollider (stock engine)" };
    public static int Count => Names.Length;
    public static DrivingModel Create(int index)
    {
        switch (index)
        {
            case 1: return new ArcadeModel();
            case 2: return new WheelColliderModel();
            default: return null;
        }
    }
}
