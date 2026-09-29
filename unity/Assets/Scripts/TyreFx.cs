using UnityEngine;

/// <summary>Tyre smoke / dust and skid marks, driven by CarController.WheelFx (0..1 per wheel: wheelspin, lock-up or sideways slide).</summary>
public class TyreFx : MonoBehaviour
{
    CarController car;
    ParticleSystem[] smoke = new ParticleSystem[4];
    TrailRenderer[] skid = new TrailRenderer[4];
    Transform[] skidT = new Transform[4];
    Material smokeMat, skidMat;
    public int Alive { get; private set; }

    public void Init(CarController c)
    {
        car = c;
        smokeMat = new Material(Resources.Load<Shader>("BeratParticle"));
        skidMat = new Material(GameAssets.Flat); skidMat.SetFloat("_OffsetFactor", -4); skidMat.SetFloat("_OffsetUnits", -4);
        for (int i = 0; i < 4; i++)
        {
            var go = new GameObject("tyreSmoke" + i); go.transform.SetParent(transform.parent == null ? transform : transform, false);
            var ps = go.AddComponent<ParticleSystem>();
            var main = ps.main; main.loop = true; main.playOnAwake = true; main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.startLifetime = new ParticleSystem.MinMaxCurve(0.9f, 1.6f); main.startSpeed = new ParticleSystem.MinMaxCurve(0.2f, 0.9f);
            main.startSize = new ParticleSystem.MinMaxCurve(0.6f, 1.1f); main.startRotation = new ParticleSystem.MinMaxCurve(0f, 6.28f);
            main.gravityModifier = -0.04f; main.maxParticles = 260; main.startColor = new Color(0.9f, 0.9f, 0.9f, 0.5f);
            var em = ps.emission; em.rateOverTime = 0f;
            var sh = ps.shape; sh.shapeType = ParticleSystemShapeType.Sphere; sh.radius = 0.12f;
            var col = ps.colorOverLifetime; col.enabled = true;
            var g = new Gradient(); g.SetKeys(new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(Color.white, 1f) },
                                              new[] { new GradientAlphaKey(0f, 0f), new GradientAlphaKey(1f, 0.12f), new GradientAlphaKey(0f, 1f) });
            col.color = g;
            var size = ps.sizeOverLifetime; size.enabled = true; size.size = new ParticleSystem.MinMaxCurve(1f, AnimationCurve.EaseInOut(0f, 0.6f, 1f, 2.6f));
            var r = go.GetComponent<ParticleSystemRenderer>(); r.sharedMaterial = smokeMat; r.renderMode = ParticleSystemRenderMode.Billboard;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; r.receiveShadows = false;
            smoke[i] = ps;

            var tg = new GameObject("skid" + i); tg.transform.rotation = Quaternion.LookRotation(Vector3.up);
            var tr = tg.AddComponent<TrailRenderer>();
            tr.alignment = LineAlignment.TransformZ; tr.time = 14f; tr.minVertexDistance = 0.25f; tr.widthMultiplier = 0.24f * CarVisual.S / 0.92f;
            tr.startColor = tr.endColor = new Color(0.10f, 0.10f, 0.11f, 1f); tr.sharedMaterial = skidMat; tr.emitting = false;
            tr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; tr.receiveShadows = false;
            skid[i] = tr; skidT[i] = tg.transform;
        }
        car.Respawned += () => { for (int i = 0; i < 4; i++) { skid[i].Clear(); skid[i].emitting = false; } };
    }

    void Update()
    {
        if (car == null || skid[0] == null) return;
        Alive = 0;
        Color dust = car.CurrentSurface == Surface.Dirt ? new Color(0.68f, 0.55f, 0.38f, 0.55f) : car.CurrentSurface == Surface.Grass ? new Color(0.55f, 0.62f, 0.38f, 0.5f) : new Color(0.93f, 0.93f, 0.95f, 0.5f);
        for (int i = 0; i < 4; i++)
        {
            float k = car.WheelFx[i];
            Vector3 p = car.WheelPoint[i];
            smoke[i].transform.position = p + Vector3.up * 0.1f;
            var em = smoke[i].emission; em.rateOverTime = k > 0.05f ? Mathf.Lerp(8f, 75f, k) : 0f;
            var m = smoke[i].main; m.startColor = dust;
            skidT[i].position = p + Vector3.up * 0.035f;
            bool marks = k > 0.4f && car.SurfaceUnderWheel[i] == Surface.Asphalt;
            if (skid[i].emitting != marks) skid[i].emitting = marks;
            Alive += smoke[i].particleCount;
        }
    }
}
