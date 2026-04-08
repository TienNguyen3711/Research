from trajectory_layer_bootstrap import METERS_PER_DEGREE, dynamic_factors, googlemaps, math, os, requests
from trajectory_layer_geometry import densify_trajectory, get_segment_frame


def decode_google_polyline(encoded_polyline):
    """Decode a Google encoded polyline string into (lat, lng) tuples."""
    if not encoded_polyline:
        return []

    coordinates = []
    index = 0
    latitude = 0
    longitude = 0

    while index < len(encoded_polyline):
        shift = 0
        result = 0
        while True:
            byte = ord(encoded_polyline[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        latitude += ~(result >> 1) if (result & 1) else (result >> 1)

        shift = 0
        result = 0
        while True:
            byte = ord(encoded_polyline[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        longitude += ~(result >> 1) if (result & 1) else (result >> 1)

        coordinates.append((latitude / 1e5, longitude / 1e5))

    return coordinates


def merge_decoded_route_points(routes):
    """Flatten decoded route/step polylines while avoiding duplicate neighbors."""
    merged_points = []

    for point in routes:
        if not merged_points or merged_points[-1] != point:
            merged_points.append(point)

    return merged_points


def decode_google_directions_route(route):
    """Decode one Google Directions route into a dense coordinate list."""
    decoded_points = []

    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            step_polyline = step.get("polyline", {}).get("points")
            if step_polyline:
                decoded_points.extend(decode_google_polyline(step_polyline))
            else:
                decoded_points.append(
                    (
                        step["start_location"]["lat"],
                        step["start_location"]["lng"],
                    )
                )
                decoded_points.append(
                    (
                        step["end_location"]["lat"],
                        step["end_location"]["lng"],
                    )
                )

    if not decoded_points:
        overview_polyline = route.get("overview_polyline", {}).get("points")
        decoded_points = decode_google_polyline(overview_polyline)

    trajectory = merge_decoded_route_points(decoded_points)
    if not trajectory:
        raise ValueError("Unable to decode route polyline from Google Maps response.")
    return trajectory


def build_alternative_waypoints(real_trajectory, lateral_offset_meters=1800):
    """Build midpoint-shifted waypoint candidates to coerce an alternate route."""
    if len(real_trajectory) < 3:
        return []

    midpoint_index = len(real_trajectory) // 2
    prev_point = real_trajectory[max(0, midpoint_index - 1)]
    next_point = real_trajectory[min(len(real_trajectory) - 1, midpoint_index + 1)]
    tangent, normal = get_segment_frame(prev_point, next_point)
    lng_scale = max(math.cos(math.radians(real_trajectory[midpoint_index][0])), 0.2)
    offset_lat = (normal[0] * lateral_offset_meters) / METERS_PER_DEGREE
    offset_lng = (normal[1] * lateral_offset_meters) / (METERS_PER_DEGREE * lng_scale)
    midpoint = real_trajectory[midpoint_index]

    waypoint_candidates = []
    for direction in (1.0, -1.0):
        waypoint_candidates.append(
            f"{midpoint[0] + offset_lat * direction},{midpoint[1] + offset_lng * direction}"
        )

    return waypoint_candidates


def validate_dynamic_factors(factors):
    required_keys = ["wind_speed", "storm_index", "star_index", "humidity", "tide_level"]
    for key in required_keys:
        if key not in factors:
            raise ValueError(f"Missing required key: {key}")
        if not isinstance(factors[key], (int, float)):
            raise ValueError(f"Invalid type for {key}: must be number")
    return True


def get_real_trajectory_from_google_maps(origin, destination, api_key=None):
    route_bundle = get_google_maps_route_bundle(origin, destination, api_key=api_key)
    return route_bundle["real_trajectory"]


def get_google_maps_route_bundle(origin, destination, api_key=None):
    """Return a real route plus an alternate candidate route from Google Maps."""
    if googlemaps is None:
        raise RuntimeError("googlemaps package is not installed")
    if not api_key:
        api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise ValueError("Google Maps API key is required. Set GOOGLE_MAPS_API_KEY environment variable.")
    gmaps = googlemaps.Client(key=api_key)
    directions_result = gmaps.directions(origin, destination, mode="driving", alternatives=True)
    if not directions_result:
        raise ValueError("No route found.")
    decoded_routes = [decode_google_directions_route(route) for route in directions_result]
    real_trajectory = decoded_routes[0]
    alternate_trajectory = decoded_routes[1] if len(decoded_routes) > 1 else None

    if alternate_trajectory is None:
        for waypoint in build_alternative_waypoints(real_trajectory):
            waypoint_result = gmaps.directions(
                origin,
                destination,
                mode="driving",
                waypoints=[waypoint],
            )
            if waypoint_result:
                alternate_trajectory = decode_google_directions_route(waypoint_result[0])
                break

    return {
        "real_trajectory": real_trajectory,
        "alternate_trajectory": alternate_trajectory,
    }


def get_environmental_data(lat, lng, weather_api_key=None):
    if requests is None:
        return {"wind_speed": 15.0, "humidity": 65.0, "cloud_cover": 30.0, "rain_probability": 20.0}
    if not weather_api_key:
        weather_api_key = os.getenv("OPENWEATHER_API_KEY")
    if not weather_api_key:
        print("No OpenWeather API key, using mock data.")
        return {"wind_speed": 15.0, "humidity": 65.0, "cloud_cover": 30.0, "rain_probability": 20.0}
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lng}&appid={weather_api_key}&units=metric"
    response = requests.get(url)
    if response.status_code != 200:
        print("Weather API failed, using mock data.")
        return {"wind_speed": 15.0, "humidity": 65.0, "cloud_cover": 30.0, "rain_probability": 20.0}
    data = response.json()
    wind_speed = data.get("wind", {}).get("speed", 10.0) * 3.6
    humidity = data.get("main", {}).get("humidity", 50.0)
    cloud_cover = data.get("clouds", {}).get("all", 20.0)
    rain_probability = min(100, humidity * 0.5 + cloud_cover * 0.3)
    return {
        "wind_speed": wind_speed,
        "humidity": humidity,
        "cloud_cover": cloud_cover,
        "rain_probability": rain_probability,
    }


def normalize_to_2_digits(value, min_value, max_value):
    value = max(min_value, min(value, max_value))
    scaled = int((value - min_value) / (max_value - min_value) * 99)
    return f"{scaled:02d}"


def compute_storm_index(wind_speed, humidity, rain_probability):
    return min(100, (wind_speed * 0.4 + humidity * 0.2 + rain_probability * 0.4))


def compute_star_index(cloud_cover, humidity):
    return max(0, 100 - (cloud_cover * 0.7 + humidity * 0.3))


def build_dynamic_context(raw_data):
    storm_index = compute_storm_index(raw_data["wind_speed"], raw_data["humidity"], raw_data["rain_probability"])
    star_index = compute_star_index(raw_data["cloud_cover"], raw_data["humidity"])
    return {
        "wind_speed": raw_data["wind_speed"],
        "storm_index": storm_index,
        "star_index": star_index,
        "humidity": raw_data["humidity"],
        "tide_level": raw_data.get("tide_level", 50.0),
    }


def normalize_dynamic_factors(factors):
    return {
        "wind_speed": round(factors["wind_speed"], 2),
        "storm_index": round(factors["storm_index"], 2),
        "star_index": round(factors["star_index"], 2),
        "humidity": round(factors["humidity"], 2),
        "tide_level": round(factors["tide_level"], 2),
    }


def get_local_test_inputs():
    base_real_trajectory = [
        (-37.80895, 144.96310),
        (-37.80920, 144.96580),
        (-37.80985, 144.96960),
        (-37.81040, 144.97380),
        (-37.81110, 144.97890),
        (-37.81210, 144.98490),
        (-37.81320, 144.99180),
        (-37.81410, 144.99860),
        (-37.81520, 145.00580),
        (-37.81660, 145.01310),
        (-37.81780, 145.02060),
        (-37.81870, 145.02840),
        (-37.81980, 145.03620),
        (-37.82100, 145.04430),
        (-37.82250, 145.05270),
        (-37.82440, 145.06130),
        (-37.82700, 145.06940),
        (-37.83030, 145.07730),
        (-37.83410, 145.08500),
        (-37.83820, 145.09280),
        (-37.84220, 145.10010),
        (-37.84560, 145.10630),
        (-37.84855, 145.11425),
    ]
    real_trajectory = densify_trajectory(base_real_trajectory, 40)
    return real_trajectory, dict(dynamic_factors), None
