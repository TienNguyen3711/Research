from trajectory_layer_bootstrap import os
from trajectory_layer_inputs import get_google_maps_route_bundle


def main():
    origin = os.getenv("ORIGIN", "RMIT Melbourne, Australia")
    destination = os.getenv("DESTINATION", "Deakin Burwood, Australia")
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")

    print("Google Maps Probe")
    print(f"Origin: {origin}")
    print(f"Destination: {destination}")
    print(f"API key loaded: {bool(api_key)}")
    if api_key:
        print(f"API key prefix: {api_key[:6]}")
        print(f"API key length: {len(api_key)}")

    try:
        route_bundle = get_google_maps_route_bundle(origin, destination, api_key=api_key or None)
        real_trajectory = route_bundle["real_trajectory"]
        alternate_trajectory = route_bundle["alternate_trajectory"]
        print("Status: OK")
        print(f"Real route points: {len(real_trajectory)}")
        print(f"Alternate route available: {bool(alternate_trajectory)}")
        if alternate_trajectory:
            print(f"Alternate route points: {len(alternate_trajectory)}")
    except Exception as exc:
        print("Status: FAILED")
        print(f"Error type: {type(exc).__name__}")
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()
