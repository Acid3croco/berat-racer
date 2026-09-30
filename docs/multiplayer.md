# Online play

Drive the same map with friends: you see their car live (the right model, wheels steering and spinning, brake and reverse lamps), their tyre smoke and skid marks, and everybody sees the same traffic.

## Playing

1. Everyone needs **the same map** (the same `world/`, or the same release). Positions travel as plain world coordinates: on a different map the others would be drawn in the wrong place.
2. The host presses **O**, chooses a name and a password, and clicks *Open session* (UDP port 27960 by default). The panel shows the address to give out:
   - same network: the LAN address it lists, e.g. `192.168.1.20:27960`;
   - over the internet: the host's public IP, after forwarding **UDP** port 27960 on the router to the host's computer (and allowing the game through the firewall when the OS asks).
3. The friend presses **O**, enters the same password, the address `ip:port`, and clicks *Join*.

Esc closes the panel; the top of the screen says who is online. Each other player has a name tag with the distance, and a yellow dot on the minimap. *Close session* / *Leave* ends it (the others are told, or notice within 6 s if the game crashed).

From the command line: `-host [port] -password P -name N`, or `-join ip:port -password P -name N` (same on macOS and Windows).

## What is shared, and who owns what

| | owned by | the others get |
|---|---|---|
| each player's car | that player: his own physics, driving model, assists (T), camera | pose, velocity, wheels (drop, steer, spin rate), smoke / skid intensity and surface per wheel, lamps, car model, respawns — 30 times a second |
| traffic | the host: it spawns around **every** player and brakes for all of them | each client receives the vehicles within 1.3 km of his car, 20 times a second, and only mirrors them |

- Switching car (F) is seen by the others at once: a new model rebuilds the remote car.
- Reset (R), teleport (J, map) and car switches make the remote car jump instead of flying across the map, and its skid marks do not join the old ones.
- The others' cars and the traffic are solid (kinematic): you bump into them, they are not pushed (each player drives his own car; there is no shared collision).
- Online, opening the map does not pause the game (the world goes on for the others); your car is held with the brakes while the map or the panel is open.
- Remote things are drawn 0.1 s in the past, blended between two received states, so they move smoothly despite network jitter. If states stop arriving a car coasts on its velocity for 0.25 s, then waits.

## Protocol (`NetProtocol.cs`, `NetSession.cs`)

Plain UDP, one message per datagram (at most ~1.2 KB, below any internet MTU), little-endian, starting with `BR`, the protocol version and the message kind. No server beyond the host's game, no package dependencies: .NET sockets only, so macOS and Windows players can play together (on Windows the socket ignores ICMP "port unreachable", which would otherwise break the receive loop).

Joining: `Hello(name)` -> `Challenge(16 random bytes)` -> `Auth(HMAC-SHA256(password, challenge))` -> `Welcome(player id)` or `Reject(reason)`. The password never travels; a wrong one is refused. The client repeats Hello every second until it gets an answer (UDP may drop it) and gives up after 15 s. Up to 8 players.

Then: `State` (a car, from its owner; the host relays each to the others), `Traffic` (host -> each client, split over several datagrams), `Bye(id)` (a player left; id 0 = the host closed the session). A side that hears nothing for 6 s considers the other gone.

Traffic replication (`Traffic.cs`): every vehicle has an id and a *look* (model and paint indices), so a client builds the same vehicle the first time it sees it and drops it 1.5 s after it stops being sent (despawned on the host, or out of range).

## Testing on one machine

Two players on one Mac, headless, each with its own log (`~/Library/Logs/BeratRacer/<logname>.log`); `[net]` lines every 5 s list the remote cars, where they are and how fresh they are:

```sh
APP=Build/BeratRacer.app/Contents/MacOS/"Berat Racer"
"$APP" -batchmode -host 27960 -password test -name Host -autopilot -logname mp-host &
"$APP" -batchmode -join 127.0.0.1:27960 -password test -name Guest -car 2 -logname mp-guest &
```

Limits: no NAT punch-through (the host forwards the port), no voice or chat, no engine sound for the others' cars yet.
