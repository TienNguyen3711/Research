# Privacy-Preserving Trajectory Encoding

This project generates a privacy-preserving fake trajectory from a real driving route, stores the protected package in encrypted form, and keeps a label-based recovery path so the original route can still be reconstructed when needed.

The current codebase has been refactored into small layer-oriented Python modules to make maintenance easier.

## What The System Does

The pipeline now works in four main stages:

1. Acquire a real route.
   - Prefer Google Maps live directions when `GOOGLE_MAPS_API_KEY` is available.
   - Decode detailed Google polylines instead of using only coarse step endpoints.
   - Fall back to a local mock route when live Google Maps is unavailable.

2. Build a fake route.
   - Create a label-locked anchor fake trajectory from the real route.
   - Use `corridor-based path` and `curved distortion` as the main privacy layers.
   - If Google Maps provides an alternate route, blend that alternate road geometry into the display fake route so the red route looks more like a real drivable path.

3. Verify recoverability and privacy behavior.
   - Recover the original route from the label-locked anchor trajectory.
   - Run a self-check to confirm encryption and decryption preserve the package.
   - Check whether the fake route still sits too close to the original route in local regions.

4. Persist and visualize.
   - Save the protected package as an encrypted file.
   - Optionally render an HTML map or plot for inspection.

## Current Route Model

There are three distinct route concepts in the code:

- `real_trajectory`
  - The source blue route.
  - Preferably decoded from Google Maps polylines.

- `scrambled_anchor_trajectory`
  - The label-locked fake anchor route.
  - This is the route used for deterministic recovery.

- `scrambled_trajectory`
  - The final fake display route.
  - This may be expanded, smoothed, and blended with a Google Maps alternate route to look more natural on a map.

In the current version, zigzag injection has been removed. The fake route now focuses on smoother `corridor-based path` behavior plus `curved distortion`.

## Layered File Structure

The monolithic script has been split into these layer files:

- `Code.py`
  - Thin entry point that orchestrates the full pipeline.

- `trajectory_layer_bootstrap.py`
  - Shared imports, environment loading, constants, defaults, and cleanup helpers.

- `trajectory_layer_inputs.py`
  - Route acquisition, Google Maps polyline decoding, alternate-route probing, weather input loading, and mock/local test inputs.

- `trajectory_layer_geometry.py`
  - Coordinate transforms, local frames, smoothing, densification, resampling, and distance helpers.

- `trajectory_layer_scramble.py`
  - Session seed derivation, label generation, corridor-based scrambling, curved distortion, display-route blending, and privacy profile logic.

- `trajectory_layer_crypto.py`
  - Encryption, decryption, key derivation, key rotation support, round-trip validation, and recovery verification.

- `trajectory_layer_visualization.py`
  - HTML/SVG map rendering, Folium fallback rendering, and plotting helpers.

- `google_maps_probe.py`
  - A small connectivity and route-fetch diagnostic for Google Maps.

## Live Google Maps Behavior

When live Google Maps is available, the system now tries to do the following:

1. Request the main driving route.
2. Request alternate driving routes with `alternatives=True`.
3. If Google does not return a useful alternate route, try midpoint-shifted waypoint candidates to coerce an alternate route.
4. Decode route polylines into dense coordinate sequences.
5. Use:
   - the main route as `real_trajectory`
   - the alternate route as the display-route base for the fake route

This gives the fake route a better chance of looking like a real road-following path instead of only a mathematically distorted copy of the original.

## Important Limitation

If the environment cannot resolve or connect to `maps.googleapis.com`, the project will fall back to local mock inputs even if `GOOGLE_MAPS_API_KEY` is present.

In this case:

- `Route source` will print as `local_mock_fallback`
- the blue route is still valid for testing the pipeline
- but it will not match true road geometry from Google Maps

Use `google_maps_probe.py` to confirm whether your environment can actually reach Google Maps.

## Environment Variables

The project uses a lightweight `.env` loader from `trajectory_layer_bootstrap.py`.

Common variables:

- `GOOGLE_MAPS_API_KEY`
  - Enables live Google Maps route acquisition.

- `OPENWEATHER_API_KEY`
  - Enables live weather input for dynamic factors.

- `APP_TRAJECTORY_SECRET`
  - Required for secure session-seed derivation.

- `FAKE_DATA_SECRET`
  - Legacy storage secret fallback.

- `FAKE_DATA_SECRET_V1`, `FAKE_DATA_SECRET_V2`, ...
  - Versioned storage secrets for encrypted package writes.

- `FAKE_DATA_SECRET_CURRENT_VERSION`
  - Selects which versioned storage secret to use for new encrypted output.

- `HEADLESS_LOCAL_TEST`
  - `1` forces local mock behavior.

- `PREFER_LIVE_GOOGLE_ROUTE`
  - `1` tries Google Maps first when the key is present.

- `ENABLE_VISUAL_OUTPUTS`
  - `1` enables plot and map output.

- `RUN_SELF_TEST`
  - `1` validates encrypted round-trip behavior after writing the package.

- `ORIGIN`
  - Optional custom origin for route acquisition.

- `DESTINATION`
  - Optional custom destination for route acquisition.

- `FAKE_DATA_PATH`
  - Output path for the encrypted package.

## Minimal Example `.env`

```env
GOOGLE_MAPS_API_KEY=YOUR_GOOGLE_MAPS_KEY
OPENWEATHER_API_KEY=YOUR_OPENWEATHER_KEY
APP_TRAJECTORY_SECRET=replace_with_a_long_random_secret
FAKE_DATA_SECRET_V1=replace_with_a_long_storage_secret
FAKE_DATA_SECRET_CURRENT_VERSION=1
HEADLESS_LOCAL_TEST=0
PREFER_LIVE_GOOGLE_ROUTE=1
ENABLE_VISUAL_OUTPUTS=1
RUN_SELF_TEST=1
ORIGIN=RMIT Melbourne, Australia
DESTINATION=Deakin Burwood, Australia
```

## How To Run

Run the main pipeline:

```bash
python3 Code.py
```

Probe Google Maps connectivity and route acquisition:

```bash
python3 google_maps_probe.py
```

## Main Outputs

Typical outputs include:

- `encrypted_fake_points.bin`
  - Encrypted protected package containing the fake route package and metadata.

- `trajectory_map.html`
  - Rendered comparison map from the current run.

- `trajectory_map_v3.html`
  - Existing rendered HTML map variant used during iterative tuning.

## Protected Package Contents

The protected package currently includes fields such as:

- `scheme`
- `flow`
- `reference_frame`
- `route_frames`
- `scramble_radius_meters`
- `labels`
- `scrambled_anchor_trajectory`
- `scrambled_trajectory`
- `recovered_trajectory`

The anchor trajectory is what enables deterministic recovery of the original route.

## Development Notes

- The codebase has been tuned toward smoother fake routes rather than zigzag-heavy routes.
- The display fake route can blend with a true alternate route from Google Maps for better realism.
- The local mock route is still useful for smoke testing when live Google Maps is blocked by DNS, firewall, proxy, or sandbox restrictions.
- The current visualization compares real and fake routes in separate fitted panels so route differences are easier to inspect visually.

## Suggested Next Improvements

- Add structured logging for route-source fallback reasons.
- Add a cached live-route JSON mode so testing can continue without live network access.
- Add unit tests per layer module.
- Add a higher-level architecture diagram for the route, scramble, crypto, and visualization layers.
