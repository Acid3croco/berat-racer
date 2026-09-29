# Speed-feel research: Burnout Paradise and the perception literature

Target: Unity low-poly open world, Clio-sized car, French village roads from LiDAR, top speed ~400 km/h, arcade-sim handling, FOV planned 70 deg (rest) to 120 deg (400 km/h).
Research date: 2026-09-29.

## 0. Read this first: what the research could and could not establish

1. **There is no detailed first-party "how we made Burnout Paradise feel fast" talk or postmortem that I could find.** I searched GDC Vault, Game Developer/Gamasutra, Eurogamer/Digital Foundry, and interviews. Criterion's public output is about handling, cameras, crashes, and open-world tech, not a speed-feel breakdown. Anything below labelled "Burnout" that is not a direct developer statement is community/secondary opinion or my inference, and is labelled so.
2. **Correction to a premise:** Burnout Paradise ran at 60 fps, not 30. Lead gameplay programmer Iain Angus called it "the first next-gen game, at 60 [fps], open-world" (see S3). The 30 fps game is Alex Ward's later *Dangerous Driving* (2019), where he said the base PS4 "could probably run the game at 60fps" but it would take "a full year of non stop optimisation" (S6). Frame rate is therefore a factor *for* speed feel, not something Paradise got away without.
3. **Provenance caveat.** Most web pages were read through a summarising fetch tool, not a raw browser. Quotes marked (raw) were read directly from PDFs/transcripts I downloaded. All other quotes should be re-checked against the linked page before you cite them publicly.
4. Salvucci is on your list, but his best-known driving work (two-point steering model) is about steering, not speed perception, and I found nothing from him on speed feel. I did not cite him.

Confidence labels used throughout:
- **[DEV]** primary statement from a developer.
- **[PEER]** peer-reviewed or conference-proceedings paper.
- **[SEC]** secondary source (reviewer, journalist, forum, community).
- **[INF]** my own inference; not sourced.

---

## 1. Executive summary: ranked factors

Ranking mixes evidence strength and expected payoff for *your* game. Ranks 1-5 are the ones I would build first.

| Rank | Factor | Why it matters | Evidence strength |
|---|---|---|---|
| 1 | **Edge rate / roadside object density and proximity** (things passing the screen per second, close to the car, at low eye height) | Perceived speed tracks how fast texture edges pass; edge rate explained more variance than global flow in Larish & Flach. Close, dense, high-contrast objects are the cheapest way to raise it. | [PEER] strong; Burnout link is [INF] |
| 2 | **Locked, high, evenly paced frame rate (60+ fps) with low input latency** | Paradise ran 60 fps; the series is remembered for "liquid fluidity" (interviewer, S6). At 400 km/h the car moves 1.85 m per frame at 60 fps, 3.7 m at 30 fps, so low rates cause strobing/aliasing of the very edges that carry speed. | Paradise 60 fps [DEV]; aliasing math [INF] |
| 3 | **Peripheral optic flow and low camera height** | Peripheral, ground-level flow signals ego-speed. Lower simulated eye height gave lower travel speed choices (i.e. more perceived speed) in Kemeny & Panerai's lorry simulator. | [PEER] |
| 4 | **Engine, wind and tyre audio that rises with speed** | Removing/attenuating sound makes drivers underestimate speed: 5 dB attenuation dropped perceived speed by about 5 km/h at 60 km/h reference. | [PEER] |
| 5 | **Camera behaviour: low, tight, spring-lagged, look-ahead, event-driven shake** | Criterion's own rule: "the camera is the control". Spring-damped chase camera + look-ahead + landing/impact shake are documented. | [DEV] (Harris, GDC 2018), speed link [INF] |
| 6 | **Motion blur** | Widely credited by reviewers for Paradise's speed feel, and DF flagged its removal in the Remaster. But a controlled study on a racing game found no effect on perceived speed. Treat as optional, cheap-to-try, not load-bearing. | [SEC] for; [PEER] against |
| 7 | **FOV widening with speed** | Large display FOV raises perceived speed (drivers choose lower speeds). But *geometric* FOV on a fixed screen is subtle, results conflict, and 120 deg (vertical, in Unity) is far beyond the distortion limit. Cap lower and use other cues. | [PEER] mixed; see section 4 |
| 8 | **Contrast and visibility** | Lower contrast (fog/haze) lowers perceived speed. Keep near-field contrast high. | [PEER] |
| 9 | **Risk cues: near-miss, oncoming traffic, boost, crash spectacle** | Burnout's design pillar. It raises arousal and attention to passing detail. Not a perceptual-speed mechanism proven in the literature. | [SEC]/[INF] |
| 10 | **Speed adaptation: change beats level** | Perceived speed depends on recent flow (Lidestam: rich flow first, lower speed later; Denton's adaptation hypothesis). Design for acceleration events, not a constant 400. | [PEER] + [INF] |

---

## 2. Factors in detail

### 2.1 Frame rate, frame pacing, latency

**What it is.** Paradise targeted 60 Hz with low input-to-screen latency; the physics/render loop is tuned around that.

**Evidence.**
- Iain Angus, lead gameplay programmer, 2014 interview: "The first next-gen game, at 60 [fps], open-world was a technical marvel that it works at all." [DEV, via S3]
- A Slashdot commenter (not a developer) noted Paradise runs at 60 Hz with notably low input latency [SEC, S4]. The Eurogamer article it points to (Richard Parr and Alex Fry, published 2009-06-18) is about parallelisation across cores, not speed feel. I could not open Eurogamer directly, so I only have Slashdot's summary. [DEV, unverified detail]
- Alex Ward (PlayStation Universe, 2019-04-12) was asked by the interviewer about 60 vs 30 fps: "The base hardware could probably run the game at 60fps but it would most likely be a full year of non stop optimisation for a team of at least five people to achieve." [DEV, S6]. The interviewer's framing that frame rate was "one of the main cornerstones" of classic Burnout is the interviewer's opinion, not Ward's. [SEC]
- Pichlmair & Johansen's game-feel survey: "Temporal consistency also means a consistent frame rate ... frame rate and especially the duration of the physics time step have ..." (sentence continues in the PDF; I read it in the raw text) [PEER survey, S17].

**Inference for you [INF].** Displacement per frame at 400 km/h (111 m/s): 60 fps = 1.85 m, 90 fps = 1.23 m, 120 fps = 0.93 m. A village fence post every 5 m therefore jumps a large fraction of its own spacing every frame at 60 fps. Speed is carried by edges that move smoothly; large per-frame jumps make them stutter or alias. Frame pacing (equal frame times) matters more than the average.

### 2.2 Edge rate and global optic flow (the core perceptual mechanism)

**What it is.** *Edge rate* = how many texture/object edges pass a point per second (speed / spacing). *Global optic flow rate* = angular velocity of the visual field (grows with speed, shrinks with eye height and lateral distance).

**Evidence.**
- Larish & Flach (1990), J. Exp. Psych: Human Perception and Performance 16(2):295-302: edge rate and global optical flow rate had additive effects on speed magnitude judgments, edge rate accounted for the larger share of variance, effects independent of grid vs dot texture. [PEER, S9; abstract via search snippet]
- Köhler et al. (2023) review the principle "faster objects pass by, the faster speed is perceived" and cite Gates et al. (2008): decreasing spacing of transverse bars cut mean speed by up to 4 mph. [PEER, S10]
- Denton (1980), *Perception* 9(4):393-402, "The influence of visual pattern on perceived speed": distorting spatial geometry of the visual field to counter *speed adaptation*, tested in a simulator and on a high-crash motorway site. I only confirmed the abstract-level description, not the numbers. [PEER, S8]
- Manser & Hancock (2007), *Accident Analysis & Prevention* 39(1):69-78: tunnel-wall vertical segments that decrease in width made drivers slow down; increasing width made them speed up. [PEER, S11]
- Lidestam et al. (2019), *Transportation Research Part F* 65:227-241: delineator posts and road centre lines were used for rhythm-based processing when the task was to hit a target speed; virtual road markings reduced chosen speed. [PEER, S13]

**Inference [INF].** Burnout Paradise's dense urban scenery (lamp posts, railings, parked cars, tunnel walls) close to the road is a plausible contributor, but I found no developer statement saying so. Your French village roads (walls, hedges, houses at the road edge) are naturally good; open fields between villages are the weak spots and will need repeated close roadside props.

### 2.3 Peripheral flow, camera/eye height

**Evidence.**
- Kemeny & Panerai (2003), *Trends in Cognitive Sciences* 7(1):31-37: "a limited field of view induces poor perception of speed by the driver" and, for a lorry simulator, "by increasing the simulated eye-height ... we observed higher travelling speeds, suggesting a reduced subjective speed perception." Also: "It has been found that for correct speed perception, a horizontal field of view of at least 120 [deg] is needed." (raw text read from the PDF) [PEER, S12]
- Zhang et al. (2013), quoted via Köhler et al.: road sides "are usually in the driver's periphery, which are exposed to faster motions." [PEER, second-hand, S10]
- Pretto & Chatziastros (2006), DSC Europe: increased optic-flow velocity of the road surface made drivers slow down; slower flow made them speed up; effects stronger than in walking; fog led to *lower* produced speed, interpreted as fog leaving mainly peripheral parts of the scene visible. (raw text read) [PEER, S14]

**Implication [INF].** Put the camera low. A Clio-height chase camera around 1.2-1.6 m above the road (bonnet-level) yields much higher ground flow than a 3 m drone-style view.

### 2.4 Field of view (display FOV vs geometric FOV)

This is where your plan needs care. Two different things are conflated in game settings:
- **Display FOV**: how much of the viewer's visual field the screen covers (fixed by monitor and seating distance).
- **Geometric FOV (GFOV)**: the FOV the game renders with.

**Evidence.**
- Larger *display* FOV (more periphery visible) increased perceived speed: Lidestam et al. found "Larger FoV, both horizontally and peripherally-vertically, significantly reduced participants' speed." [PEER, S13]
- Colombet, Paillot, Merienne & Kemeny (DSC Europe 2010, Renault/Arts et Metiers): on a fixed 150 deg screen, changing GFOV via a scale factor (screen FOV / geometric FOV) between 0.70 and 1.30 significantly changed produced speed; "perceived speed increases with this visual scale factor"; a 0.15 change was enough; nobody noticed the manipulation. (raw text) [PEER, S15]. In their definition, higher factor = narrower GFOV = magnified image.
- Hussain, Almallah, Alhajyaseen & Dias (2020), *Procedia Computer Science* 170:18-25: drivers "highly underestimate their driving speed" with 60 deg GFOV vs 135 deg GFOV on a 135 deg display; recommend scale factor GFOV/FOV = 1.00. (raw text) [PEER, S16]. **This appears to conflict with Colombet**, or at least the two papers use inverted scale-factor conventions and different setups. I could not reconcile them. Treat "narrow GFOV = faster" as unproven.
- Pichlmair & Johansen: "In racing and flying games, there is a tight link between the field of view, motion blur intensity, and speed. This link defines how the game feels." (raw) [PEER survey, S17]

**Inference [INF].**
- Widening GFOV on a normal monitor (about 50-60 deg horizontal) minifies the centre (slows apparent central motion) but increases edge-of-screen stretch and shows more roadside objects at once (higher edge rate, more peripheral flow). Practitioner folklore says the net effect feels faster; I found no controlled evidence, so A/B test it.
- **Unity's `Camera.fieldOfView` is vertical.** At 16:9, vertical 70 deg is about 102 deg horizontal; vertical 120 deg is about 144 deg horizontal. Rectilinear projection at 144 deg horizontal stretches the edges enormously (see section 4).

### 2.5 Audio

**Evidence.**
- Horswill & Plooy (2008), *Perception* 37:1037-1043: "A 5 dB attenuation of the ambient sound led to a decrease in perceived visual speed of about 5 km/h (for a 60 km/h reference visual speed)." (via S19, a review quoting it) [PEER]
- Prpic, Gherri & Lugli (2024), *Frontiers in Psychology* review: removing or reducing engine sound makes drivers underestimate speed and drive faster, more so at high speed; cites Wang & Wang (2012): removing frequencies below 600 Hz improved speed estimation at high speeds; Denjean et al. (2021): pitch modifies perceived acceleration/relative speed. [PEER review, S18; the secondary citations were read via a summariser]
- Denton (1966) is cited by Pretto & Chatziastros as showing auditory effects on perceived speed. [PEER, second-hand]

**Inference [INF].** Doppler on passing objects, wind noise rising with speed, and a stereo "whoosh" on near-passes are commonly used in arcade racers; I have no Burnout-specific statement on these.

### 2.6 Camera behaviour (Criterion's documented approach)

**Evidence (raw auto-captions of Matthew Harris, Criterion, GDC 2018 "Vehicle Feel Masterclass", S5; wording is machine-captioned, so paraphrase rather than quote in public):**
- "The camera is the control": a stiff or loose camera changes the feel of the car; the same designers who tune vehicle controls tune the camera.
- Criterion "almost always" uses vehicle-relative (chase) cameras.
- Spring-loaded camera: a target point plus a force pulling the camera toward it, tuned per axis, so the car moves on screen (lags right in a right turn), plus a fixed "target ahead" and optional speed extrapolation of that target so the camera looks into turns.
- Landing shake example: pitch and roll oscillations at 30 Hz for 0.5 s.
- "Orbit shake" (from Battlefront II): shake by moving/rotating the camera around a distant focus point so near objects (the car) shake while the far target stays stable.
- The talk is about Battlefield 1, Battlefront II and Need for Speed, **not specifically Paradise**, and it contains nothing on FOV-vs-speed.

Also: Iain Angus (S3) on the Paradise camera being "ridiculously complicated" due to dynamic world (crash cameras) [DEV, via summariser].

### 2.7 Motion blur

**Evidence for.** Joe Martin, bit-tech PC review, 2009-02-03: "Motion Blur is dreadfully important to getting the most out of Burnout Paradise ... With Motion Blur turned off you just can't get the full feeling of speed that the game offers." [SEC, S7]. Digital Foundry's review of Burnout Paradise Remastered (2018) listed the removal of motion blur as a disappointment; I could only confirm that via search-engine snippets, not the article itself. [SEC, S20]

**Evidence against.** Sharan, Neo, Mitchell & Hodgins, "Simulated motion blur does not improve player experience in racing game" (ACM Motion in Games, 2013), on *Split/Second: Velocity*: no significant effect on race time or on subjective measures including *perceived speed*, though participants could detect the blur. [PEER, S21]

**Reading [INF].** Motion blur is cheap insurance against strobing at a fixed frame rate and probably helps at the very high speeds you want, but do not rely on it to create the sensation. Keep it subtle; it also hides LOD pop-in and low-poly edges at speed.

### 2.8 Contrast, fog, visibility

- Kemeny & Panerai (raw): psychophysics shows observers "underestimate speed when image contrast, texture or luminance are reduced," relevant to fog and night. [PEER, S12]
- Pretto & Chatziastros: fog reduced perceived speed centrally but produced lower speeds because peripheral flow stayed visible. [PEER, S14]

**Implication [INF].** Distance fog to hide LiDAR-terrain streaming is fine, but keep near-field contrast and texture detail high (low-poly can use strong value contrast, painted road edges, dark/light alternating walls).

### 2.9 Risk, near-miss and "consequence" design

- Burnout's boost system rewards near-misses, oncoming-lane driving and drifts [SEC: Wikipedia/fan summaries]. The idea that this raises felt speed by increasing attention to passing objects is plausible but **unverified**; I found no experimental or developer source. [INF]
- Alex Ward on Paradise's design goal: "I wanted it to be about discovery and exploration" and "The world, the landscape, was that game" (Game Developer, 2018-03-06) [DEV, S2]. It is about open-world structure, not speed.
- A Game Maker's Toolkit video on Mirror's Edge Catalyst vs Burnout Paradise exists, but its transcript (I read it) is about open-world event design, not speed perception. Not a source for this topic.

### 2.10 Speed adaptation

- Denton's motivating hypothesis (from the abstract): "rectilinear speed adaptation" is a major cause of the unstable relationship between objective and subjective speed. [PEER, S8]
- Lidestam et al.: "Rich motion-flow cues presented initially resulted in lower egospeed in subsequent conditions with relatively less motion-flow cues." [PEER, S13]

**Implication [INF].** Speed feel fades under constant flow. Vary road width, roadside density, passing objects, tunnels/overpasses, and acceleration state so the flow rate keeps changing.

---

## 3. Unity implementation table

Assume speed `v` in km/h, `t = smoothstep(0, 1, v / 400)` unless noted. All ranges are **starting points to tune**; none come from Criterion. Cost is per-frame budget at 1080p/1440p on mid-range PC; verify in profiler.

| # | Technique | Parameters / range (starting point) | How it scales with speed | Cost | Evidence link |
|---|---|---|---|---|---|
| 1 | **Frame pacing** | `Application.targetFrameRate = 60` minimum; `QualitySettings.vSyncCount = 1`; fixed-timestep physics at 60-120 Hz with interpolation; camera updated in `LateUpdate` from interpolated pose; avoid GC allocs and streaming hitches on the main thread | Constant; the hitch budget shrinks with speed (1 frame = 1.85 m at 60 fps, 400 km/h) | Design constraint, not a shader | 2.1 |
| 2 | **Camera height / distance** | Chase cam: height 1.3-1.8 m above ground, 4.5-6 m behind, 5-7 deg look-down. Bonnet/bumper cam: 0.9-1.2 m | Slightly lower and closer with speed (e.g. -10 to -20% distance at max); mild pull-back only on boost | Free | 2.3 |
| 3 | **Spring-damped chase camera with look-ahead** | Position spring stiffness/damping per axis (lateral loose, vertical stiff); look-target = car + velocity * 0.15-0.35 s | Look-ahead grows with speed; lag grows slightly | Free | 2.6 (Harris) |
| 4 | **FOV curve** | Vertical FOV: rest 65-70 deg, cap **90-100 deg vertical** (about 120-125 deg horizontal at 16:9). Use an ease-out curve so most of the gain arrives by ~200 km/h; rate-limit changes to ~30-60 deg/s so acceleration reads as a "pull" | `fov = lerp(fov0, fovMax, 1 - (1-t)^2)`, smoothed with SmoothDamp | Free (but see cost of distortion below) | 2.4, section 4 |
| 5 | **Edge distortion mitigation** | If you go above ~100 deg vertical, use a Panini or cylindrical projection post-process; HDRP ships a Panini Projection override (verify for your Unity/render-pipeline version); in URP you would need a custom Renderer Feature | Blend distortion strength with FOV (0 at rest, 0.3-0.6 near cap) | ~0.2-0.5 ms | 2.4 [INF] |
| 6 | **Roadside props (edge-rate generators)** | Posts/poles/hedge segments/wall pillars every 4-12 m, 1-3 m from the road edge, alternating light/dark. Procedural placement along the LiDAR road splines; GPU instancing, LOD to billboard/impostor | Density is fixed in the world, so edge rate scales automatically with speed. Add a **denser** variant for straights and open fields. At 400 km/h, 8 m spacing = 13.9 Hz, near the flicker-fusion limit; avoid regular spacings below ~10 m at max speed or aliasing appears | Instanced draw calls; low if merged | 2.2 |
| 7 | **Road surface markings** | Centre dashes (e.g. 3 m stripe / 10 m gap; verify French regulation), edge lines, transverse bars on approaches (Denton/Gates-style) with **decreasing spacing** toward hazards or speed zones | Fixed geometry; effect grows with speed. Use high-contrast, non-repeating texture in asphalt | Free (texture/decal) | 2.2 |
| 8 | **Ground texture frequency** | Road detail texture with strong mid-frequency content (patches, cracks); avoid moire: enable anisotropic filtering, mip bias +0.5 for road | Higher speed exposes aliasing, so favour stable, filtered detail | Free | 2.1/2.2 [INF] |
| 9 | **Camera-based motion blur** | URP/HDRP Motion Blur volume; intensity 0.1-0.3 (subtle), max samples 8-16. Or a custom radial blur from screen centre | Intensity `0.05 + 0.25 * t`; fade out under 100 km/h | ~0.5-1.5 ms | 2.7; weak evidence |
| 10 | **Radial/edge blur or vignette** | Vignette 0.1-0.25, radial blur strength 0.0-0.15 masked to screen edges (leave a clear centre disc so the road ahead stays legible) | Scales with `t^1.5` so it appears only near max | ~0.3 ms | [INF]; see caveats |
| 11 | **Speed lines / particles** | Wind streak particles or line renderer near camera edges, world-space, stretched along velocity; 20-80 particles | Emission `∝ t^2`; only above ~150 km/h; keep subtle | Low | [INF] |
| 12 | **Camera shake (continuous)** | Perlin noise on position (0.5-3 cm) and roll (0.1-0.3 deg), 5-12 Hz, applied to camera only, **not** the car; scaled by road roughness | Amplitude `∝ t`, capped; add ground-material multiplier | Free | 2.6 [INF] |
| 13 | **Camera shake (events)** | Landing/impact: pitch+roll oscillation ~30 Hz for ~0.5 s (Harris example, for landings); orbit-style shake around a far focus so the horizon stays stable | Amplitude `∝ impact energy` | Free | 2.6 (Harris) |
| 14 | **Engine audio** | FMOD/Wwise or Unity audio: RPM- and load-driven layered loops, pitch range spanning at least 2 octaves; retain high-frequency content at speed | Volume and brightness up with `t`; do not let engine drop out as gears shift | Low | 2.5 |
| 15 | **Wind/tyre noise** | Looped noise, volume `∝ v^2`, low-pass opens with speed; road-surface-dependent tyre loop | `gain = 0.05 + 0.9 * t^2` | Low | 2.5 [INF] |
| 16 | **Doppler and pass-by** | Unity `AudioSource.dopplerLevel` 1-3 on traffic/props; short stereo "whoosh" one-shots on near passes (within ~2 m) | Trigger rate rises with speed automatically; scale gain with relative speed | Low | [INF] |
| 17 | **Near-miss feedback** | Trigger when a dynamic object passes within ~1.5 m; brief (100-200 ms) FOV kick of +2-4 deg, whoosh sound, UI tick, boost reward | Threshold distance grows with speed | Free | 2.9 [INF] |
| 18 | **Contrast management** | Keep near-field saturation and value contrast high; fog start beyond 150-250 m; avoid global haze that lowers contrast | Do not increase fog with speed | Free | 2.8 |
| 19 | **Speed variation design** | Alternate tight walled sections and open sections; occasional tunnels/overpasses/avenues of trees | Design-time | Content | 2.10 |
| 20 | **LOD/streaming** | LOD bias to keep silhouettes stable; pop-in hidden by fog/blur; stream ahead by at least 8-10 s of travel (about 1 km at 400 km/h) | Streaming look-ahead distance `= v * 10 s` | Engineering | Paradise streaming was a "technical marvel" (S3) |

Sanity math for tuning [INF]: 400 km/h = 111 m/s; a Clio-length car (about 4 m) passes its own length in 36 ms; roadside features 2 m from the car sweep past the camera's periphery in a couple of frames. Expect the top end to feel like a blur regardless of technique, and the *mid-range* (100-250 km/h) to be where careful density and audio design pay off.

---

## 4. Things that do not work, or need caution

1. **FOV 120 deg in Unity is vertical, so about 144 deg horizontal at 16:9.** Rectilinear stretching at the edges becomes extreme: objects near the sides are smeared, the car looks tiny, and distances are harder to judge. That undercuts control, which matters more than raw speed for arcade-sim handling. My recommendation is a cap around 90-100 deg vertical, or Panini projection if you insist on more. [INF]
2. **Geometric FOV vs perceived speed is not monotonic.** Colombet et al. and Hussain et al. appear to point in different directions (different setups and scale-factor conventions). Display FOV growth is consistently beneficial; GFOV magnification effects are not settled. A/B test with your players. [PEER, mixed]
3. **Distance judgement changes with GFOV.** Colombet et al. explicitly warn the technique "may also modify perception of distances." Expect braking/overtaking misjudgement when the FOV changes fast. [PEER]
4. **Motion blur is not a proven speed cue.** The one controlled study on a racing game found no effect on perceived speed, though reviewers of Paradise credit it. Do not spend budget there first. [PEER vs SEC]
5. **Simulator speed under-estimation is the default.** Drivers under-estimate speed in simulators in general (Hussain et al. citing prior work; Kemeny & Panerai). Most speed-feel techniques are about compensating a screen that under-delivers flow. [PEER]
6. **Motion sickness and vection.** Fast, close objects filling the view plus large FOV amplify vection and possible discomfort. A TNO study (de Vries et al., VIMS 2007) found, unexpectedly, that internal/external FOV mismatch did *not* increase cybersickness, and the authors caution that their differences were large; so this is not a settled rule. Provide sliders (FOV cap, shake, blur, vignette) and defaults on the conservative side. [PEER, S22; general vection claims are from search snippets, [SEC]]
7. **Wide-FOV plus heavy screen shake plus blur stack up.** Each effect is tolerable alone; combined they become nauseating and hurt steering precision. Harris notes even Criterion's shake had to be redesigned (orbit shake) because shaking hurt aiming. [DEV]
8. **Constant flow adapts away.** Steady 400 km/h through uniform scenery will feel slower after a minute (Lidestam; Denton hypothesis). [PEER]
9. **Regular tight spacing aliases.** Evenly spaced props closer than the per-frame displacement, or road textures with fine repeating patterns, produce strobing/moire that reads as glitchy, not fast. [INF]
10. **"Burnout has X, so X is the secret" is unsafe.** The public record does not show which of Paradise's features Criterion considered the main contributors. Everything in the Burnout column above is either reviewer opinion or inference.

---

## 5. Source list

Verification status: **V-raw** = I read the file/transcript directly; **V-tool** = read through a summarising fetch tool (re-check before quoting); **V-search** = only seen in search results, not opened; **X** = could not open.

### Burnout / Criterion
- S1. Wikipedia, "Burnout Paradise". https://en.wikipedia.org/wiki/Burnout_Paradise (V-tool; Ward "if me and you both played it for three hours..." quote is from here; original source not verified).
- S2. Game Developer, "Devs reflect on the impact and legacy of Burnout Paradise" (2018-03-06). https://www.gamedeveloper.com/design/devs-reflect-on-the-impact-and-legacy-of-i-burnout-paradise-i- (V-tool; Ward quotes about discovery/exploration; nothing on speed).
- S3. Cane and Rinse, Iain Angus interview (2014-07-14). https://caneandrinse.com/iain-angus-burnout-paradise-interview/ (V-tool; 60 fps quote, camera difficulty).
- S4. Slashdot, "A Look At the Tech Behind Burnout Paradise" (2009-06-18), linking to Eurogamer interview with Richard Parr and Alex Fry. https://games.slashdot.org/story/09/06/18/0518237/a-look-at-the-tech-behind-burnout-paradise (V-tool). The Eurogamer original: X (site blocked in my tools; I could not verify the URL).
- S5. Matthew Harris (Criterion), "Vehicle Feel Masterclass: Balancing Arcade Accessibility with Simulation Depth", GDC 2018. Vault page https://www.gdcvault.com/play/1025295/Vehicle-Feel-Masterclass-Balancing-Arcade (V-tool); slides https://media.gdcvault.com/gdc2018/presentations/Harris_Matthew_VehicleFeelMasterclass.pdf (V-raw; mostly images, few text); talk video https://www.youtube.com/watch?v=n_A0RqeGado (V-raw auto-captions).
- S6. PlayStation Universe, "Dangerous Driving Interview: Alex Ward On Burnout, Car Tuning, Dangerous Driving 2 & More", John-Paul Jones, 2019-04-12. https://www.psu.com/news/dangerous-driving-interview-burnout-dangerous-driving-2/ (V-tool).
- S7. bit-tech, "Burnout Paradise: Ultimate Box" PC review, Joe Martin, 2009-02-03. https://bit-tech.net/reviews/gaming/pc/burnout-paradise-pc-review/4/ (V-tool).
- S20. Digital Foundry/Eurogamer, Burnout Paradise Remastered analysis (motion-blur removal). Not opened (X). Seen via search-result summaries and GameSpot https://www.gamespot.com/articles/burnout-paradise-remastered-is-significantly-impro/1100-6457416/ (X, 403). Treat as V-search.
- Also checked, no speed content: GDC Vault "Rapid Idea Visualisation at Criterion Games" (Pete Lake, 2016) https://www.gdcvault.com/play/1023252/Rapid-Idea-Visualisation-at-Criterion (V-tool); GMTK "What Mirror's Edge Catalyst Should Have Learned From Burnout Paradise" https://www.youtube.com/watch?v=gg0Nbfzo_00 (V-raw transcript).
- Not opened / unverified: GamesRadar Alex Ward feature https://www.gamesradar.com/burnouts-creative-director-alex-ward-takes-us-behind-the-scenes-of-the-acclaimed-racing-series/ (fetch truncated); ResetEra threads (community only).

### Perception and simulator literature
- S8. Denton, G. G. (1980). The influence of visual pattern on perceived speed. *Perception*, 9(4), 393-402. https://journals.sagepub.com/doi/10.1068/p090393 (V-search; PubMed https://pubmed.ncbi.nlm.nih.gov/7422457/ blocked by cookie wall).
- S9. Larish, J. F., & Flach, J. M. (1990). Sources of optical information useful for perception of speed of rectilinear self-motion. *J. Exp. Psychol.: Human Perception and Performance*, 16(2), 295-302. https://pubmed.ncbi.nlm.nih.gov/2142200/ (V-search abstract).
- S10. Köhler, Klatt, Koch & Ladwig (2023). Investigating the influence of visuospatial stimuli on driver's speed perception: a laboratory study. *Cognitive Research: Principles and Implications*. https://pmc.ncbi.nlm.nih.gov/articles/PMC10499724/ (V-tool; secondary citations of Gibson 1950, Manser & Hancock 2007, Gates et al. 2008, Zhang et al. 2013, Lidestam et al. 2019 come from here).
- S11. Manser, M. P., & Hancock, P. A. (2007). The influence of perceptual speed regulation on speed perception, choice, and control: tunnel wall characteristics and influences. *Accident Analysis & Prevention*, 39(1), 69-78. https://doi.org/10.1016/j.aap.2006.06.005 (V-search abstract).
- S12. Kemeny, A., & Panerai, F. (2003). Evaluating perception in driving simulation experiments. *Trends in Cognitive Sciences*, 7(1), 31-37. http://wexler.free.fr/library/files/kemeny%20(2003)%20evaluating%20perception%20in%20driving%20simulation%20experiments.pdf (V-raw). Note the "at least 120 deg" claim cites a reference labelled [a] that I did not resolve.
- S13. Lidestam, B., Eriksson, L., & Eriksson, O. (2019). Speed perception affected by field of view: Energy-based versus rhythm-based processing. *Transportation Research Part F*, 65, 227-241. https://doi.org/10.1016/j.trf.2019.07.016 ; PDF https://www.diva-portal.org/smash/get/diva2:1369670/FULLTEXT01.pdf (V-raw abstract).
- S14. Pretto, P., & Chatziastros, A. (2006). Changes in optic flow and scene contrast affect the driving speed. Driving Simulation Conference Europe, Paris. https://nacto.org/wp-content/uploads/changes_optic_flow_scene_contrast_affect_the_driving_speed_pretto.pdf (V-raw).
- S15. Colombet, F., Paillot, D., Merienne, F., & Kemeny, A. (2010). Impact of geometric field of view on speed perception. Driving Simulation Conference Europe 2010. http://dsc2015.tuebingen.mpg.de/Docs/DSC_Proceedings/2010/DSC10_07_Colombet.pdf (V-raw abstract).
- S16. Hussain, Q., Almallah, M., Alhajyaseen, W. K. M., & Dias, C. (2020). Impact of the geometric field of view on drivers' speed perception and lateral position in driving simulators. *Procedia Computer Science*, 170, 18-25. https://doi.org/10.1016/j.procs.2020.03.005 (V-raw abstract). (Not Diels & Parkes, despite a search result labelling it so.)
- S23. Diels, C., & Parkes, A. M. (2010). Geometric field of view manipulations affect perceived speed in driving simulators. *Advances in Transportation Studies*, 12, 53-64. Existence confirmed via search results only; I did not read it (V-search). Hussain et al. and Colombet cite this line of work.
- S17. Pichlmair, M., & Johansen, M. Designing Game Feel: A Survey. https://arxiv.org/pdf/2011.09201 (V-raw). Also used for the "temporal consistency / frame rate" and FOV-blur-speed link statements.
- S18. Prpic, V., Gherri, E., & Lugli, L. (2024). A perspective review on the role of engine sound in speed perception and control. *Frontiers in Psychology*. https://pmc.ncbi.nlm.nih.gov/articles/PMC11446104/ (V-tool).
- S19. Horswill, M. S., & Plooy, A. M. (2008). Auditory feedback influences perceived driving speeds. *Perception*, 37, 1037-1043. https://doi.org/10.1068/p5736 (V-search; the 5 dB / 5 km/h figure is from a search summary, so re-check).
- S21. Sharan, L., Neo, Z. H., Mitchell, K., & Hodgins, J. K. (2013). Simulated motion blur does not improve player experience in racing game. ACM Motion in Games. https://la.disneyresearch.com/wp-content/uploads/Presence-of-Motion-Blur-Effect-Does-Not-Improve-Gaming-Experience-Paper.pdf (V-raw abstract); ACM https://dl.acm.org/doi/10.1145/2522628.2522653.
- S22. de Vries, S. C., Bos, J. E., van Emmerik, M. L., & Groen, E. L. (2007). Internal and external Field of View: computer games and cybersickness. Proceedings of VIMS 2007. https://www.ieda.ust.hk/dfaculty/so/pdf/Pages89-95-VIMS2007.pdf (V-raw abstract).
- S24. Godley, S. T., Triggs, T. J., & Fildes, B. N. (2002). Driving simulator validation for speed research. *Accident Analysis & Prevention*, 34(5), 589-600. https://www.sciencedirect.com/science/article/abs/pii/S0001457501000562 (V-search abstract only: validated a simulator by drivers' responses to transverse rumble strips). Your brief dated it 2004; the record says 2002.

### Not found
- Any GDC/Gamasutra talk by Alex Ward, Fiona Sperry, Paul Ross, Matt Webster, Richard Parr or Chris Roberts explaining Burnout Paradise speed feel.
- Criterion-authored technical detail on Paradise's motion blur, FOV curve, or camera-shake parameters.
- A Digital Foundry article on the *original* Paradise's rendering (only the Remaster/Switch pieces surfaced).
- Named YouTube analyses of Paradise speed feel with verifiable claims (the one GMTK video found is off-topic).
- Peer-reviewed game-specific work beyond Sharan et al. (motion blur) and de Vries et al. (FOV/sickness).
