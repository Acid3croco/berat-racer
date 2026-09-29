"""Download OpenStreetMap points of interest (pharmacy, town hall, church, shops...) for the Berat area -> data/osm_poi.json."""
import sys, time, requests

BBOX = "43.35,1.13,43.405,1.22"      # south, west, north, east: covers the 6.4 km game box
QUERY = f"""[out:json][timeout:40];
(
 nwr["amenity"~"place_of_worship|pharmacy|townhall|school|post_office|restaurant|cafe|bar|bakery|fuel|doctors|community_centre|fire_station|police|library|kindergarten|bank|marketplace"]({BBOX});
 nwr["shop"]({BBOX});
);
out center tags;"""

for attempt in range(1, 5):
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": QUERY}, timeout=120,
                      headers={"User-Agent": "berat-racer/1.0 (hobby game, non-commercial)"})
    if r.ok and r.text.lstrip().startswith("{"):
        open("data/osm_poi.json", "w").write(r.text)
        print(f"{len(r.json()['elements'])} OSM elements -> data/osm_poi.json")
        sys.exit(0)
    print(f"attempt {attempt}: HTTP {r.status_code}, retrying", file=sys.stderr); time.sleep(8)
sys.exit("Overpass API did not answer; try again later")
