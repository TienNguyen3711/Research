# Privacy-Preserving Trajectory Encoding

This project generates a privacy-preserving fake trajectory from a real driving route, stores the protected package in encrypted form, and keeps a label-based recovery path so the original route can still be reconstructed when needed.

## What The System Does

The pipeline works in four main stages:

1. **Acquire a real route.**
   - Prefer Google Maps live directions when `GOOGLE_MAPS_API_KEY` is available.
   - Decode detailed Google polylines instead of using only coarse step endpoints.
   - Fall back to a local mock route when live Google Maps is unavailable.

2. **Build a fake route.**
   - Create a label-locked anchor fake trajectory from the real route.
   - Apply `corridor-based path` and `curved distortion` as the main privacy layers.
   - If Google Maps provides an alternate route, blend that alternate road geometry into the display fake route so the red route looks more like a real drivable path.

3. **Verify recoverability and privacy behavior.**
   - Recover the original route from the label-locked anchor trajectory.
   - Run a self-check to confirm encryption and decryption preserve the package.
   - Check whether the fake route still sits too close to the original route in local regions.

4. **Persist and visualize.**
   - Save the protected package as an encrypted `.bin` file.
   - Optionally render an HTML map or matplotlib plot for inspection.

## Current Route Model

There are three distinct route concepts in the code:

- `real_trajectory` — The source blue route, preferably decoded from Google Maps polylines.
- `scrambled_anchor_trajectory` — The label-locked fake anchor route used for deterministic recovery.
- `scrambled_trajectory` — The final fake display route, potentially expanded, smoothed, and blended with a Google Maps alternate route.

## Layered File Structure

| File | Responsibility |
|---|---|
| `Code.py` | Thin entry point that orchestrates the full pipeline |
| `trajectory_layer_bootstrap.py` | Shared imports, `.env` loading, constants, defaults, cleanup helpers |
| `trajectory_layer_inputs.py` | Route acquisition, Google Maps polyline decoding, alternate-route probing, weather input, mock inputs |
| `trajectory_layer_geometry.py` | Coordinate transforms, local frames, smoothing, densification, resampling, distance helpers |
| `trajectory_layer_scramble.py` | Session seed derivation, label generation, corridor-based scrambling, curved distortion, display-route blending, privacy profile logic |
| `trajectory_layer_crypto.py` | Encryption, decryption, key derivation, key rotation, round-trip validation, recovery verification, migration |
| `trajectory_layer_visualization.py` | HTML/SVG map rendering, Folium fallback, plotting helpers |
| `google_maps_probe.py` | Connectivity and route-fetch diagnostic for Google Maps |

## Security Model

### Encryption

- All protected packages are encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256).
- Keys are derived via **PBKDF2-HMAC-SHA256** at **600,000 iterations** (OWASP 2024 recommendation).
- Each write generates a fresh **256-bit random salt** (NIST SP 800-132).
- Keys are **per-user scoped**: the raw storage secret is first transformed through `HMAC(secret, "user:{user_id}:key_version:{v}")` before being passed to PBKDF2, so different users cannot decrypt each other's files even if they share the same base secret.

### File Format

```
[magic header: "FAKE_TRAJECTORY_SECURE"] [version: 1 byte] [metadata length: 4 bytes] [metadata JSON] [Fernet token]
```

- The magic header and metadata length are validated on every read.
- Metadata includes algorithm name, KDF parameters, salt (base64), key version, and data type — allowing future migration without breaking existing files.

### Label Transform Security

- Each point label is derived as `HMAC(session_seed, "point-label:{i}")` — bound to the session seed, not guessable without it.
- The per-point offset transform is derived as `HMAC(session_seed, "{label}:{i}")` — requires both the label and the session seed to reproduce.
- The `session_seed` itself is derived via HKDF from a random nonce, dynamic environmental factors, user ID, timestamp, and the `APP_TRAJECTORY_SECRET`.
- The `session_seed` is stored (base64-encoded) inside the encrypted protected package, so recovery is self-contained.

### Key Versioning and Rotation

- Storage secrets are versioned: `FAKE_DATA_SECRET_V1`, `FAKE_DATA_SECRET_V2`, …
- `FAKE_DATA_SECRET_CURRENT_VERSION` selects which version is used for new writes.
- Old versions remain available for decrypting files written under previous keys.
- `migrate_encrypted_file()` re-encrypts a `.bin` file to the current PBKDF2 iteration count without changing its content. Safe to call multiple times — skips files already at the current count.

### Encrypted File Permissions

- All `.bin` files are written with `0o600` (owner read/write only).

## Noise and Privacy Parameters

The fake route distance from the real route is controlled by two layers:

### Layer 1 — Offset magnitude (configurable via env vars)

| Env var | Default | Meaning |
|---|---|---|
| `MAX_POINT_OFFSET_METERS` | 5000 | Maximum offset for any single point |
| `MIDDLE_MIN_OFFSET_METERS` | 3500 | Minimum offset for interior points |
| `MIDDLE_MAX_OFFSET_METERS` | 5000 | Maximum offset for interior points |
| `ENDPOINT_OFFSET_METERS` | 4500 | Offset target for start/end points |
| `MIN_PRIVACY_DISTANCE_METERS` | 300 | Minimum allowed proximity to real route |

### Layer 2 — Local roughness (hardcoded in `trajectory_layer_scramble.py`)

| Parameter | Value | Effect |
|---|---|---|
| Lateral smoothing passes | 3 | Fewer passes → more irregular shape |
| Longitudinal smoothing passes | 2 | Fewer passes → less uniform spacing |
| `curve_strength` coefficient | 0.45× radius | Sinusoidal curvature amplitude |
| `curve_strength` cap | 1000 m | Maximum curvature per point |
| Lateral amplification | 1.75–2.10× | Scales label-driven offsets outward |

## Live Google Maps Behavior

When live Google Maps is available, the system:

1. Requests the main driving route.
2. Requests alternate driving routes with `alternatives=True`.
3. If no useful alternate route is returned, tries midpoint-shifted waypoint candidates to coerce one.
4. Decodes route polylines into dense coordinate sequences.
5. Uses the main route as `real_trajectory` and the alternate route as the display-route base.

This gives the fake route a better chance of looking like a real road-following path instead of only a mathematically distorted copy of the original.

## Environment Variables

The project uses a lightweight `.env` loader from `trajectory_layer_bootstrap.py`.

### Required

| Variable | Description |
|---|---|
| `USER_ID` | Integer user identifier — required, no default |
| `APP_TRAJECTORY_SECRET` | Long random secret for HKDF session seed derivation |
| `FAKE_DATA_SECRET_V1` | Versioned storage secret for encrypted package writes |
| `FAKE_DATA_SECRET_CURRENT_VERSION` | Selects which versioned secret to use for new writes |

### Optional

| Variable | Default | Description |
|---|---|---|
| `FAKE_DATA_SECRET` | — | Legacy fallback if no versioned secrets are set |
| `GOOGLE_MAPS_API_KEY` | — | Enables live Google Maps route acquisition |
| `OPENWEATHER_API_KEY` | — | Enables live weather input for dynamic factors |
| `ORIGIN` | `RMIT Melbourne, Australia` | Custom origin for route acquisition |
| `DESTINATION` | `Deakin Burwood, Australia` | Custom destination |
| `FAKE_DATA_PATH` | `encrypted_fake_points.bin` | Output path for the encrypted package |
| `TIMESTAMP` | current Unix time | Override session timestamp |
| `PREFER_LIVE_GOOGLE_ROUTE` | `1` | Try Google Maps first when key is present |
| `HEADLESS_LOCAL_TEST` | `0` | Force local mock inputs |
| `ENABLE_VISUAL_OUTPUTS` | `0` | Enable HTML map and matplotlib plot output |
| `RUN_SELF_TEST` | `0` | Validate encrypted round-trip after writing |
| `MAX_POINT_OFFSET_METERS` | `5000` | Maximum point offset in meters |
| `MIDDLE_MIN_OFFSET_METERS` | `3500` | Minimum interior offset in meters |
| `MIDDLE_MAX_OFFSET_METERS` | `5000` | Maximum interior offset in meters |
| `ENDPOINT_OFFSET_METERS` | `4500` | Endpoint offset target in meters |
| `MIN_PRIVACY_DISTANCE_METERS` | `300` | Minimum proximity to real route |

## Minimal Example `.env`

```env
USER_ID=42
APP_TRAJECTORY_SECRET=replace_with_a_long_random_secret
FAKE_DATA_SECRET_V1=replace_with_a_long_storage_secret
FAKE_DATA_SECRET_CURRENT_VERSION=1
GOOGLE_MAPS_API_KEY=YOUR_GOOGLE_MAPS_KEY
OPENWEATHER_API_KEY=YOUR_OPENWEATHER_KEY
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

Migrate an existing `.bin` file to the current PBKDF2 iteration count:

```python
from trajectory_layer_crypto import migrate_encrypted_file, get_storage_secret_registry
migrate_encrypted_file("encrypted_fake_points.bin", get_storage_secret_registry(), user_id=42)
```

Probe Google Maps connectivity and route acquisition:

```bash
python3 google_maps_probe.py
```

## Main Outputs

| File | Description |
|---|---|
| `encrypted_fake_points.bin` | Encrypted protected package (fake route + metadata) |
| `trajectory_map.html` | Rendered comparison map from the current run |

## Protected Package Contents

The protected package stored inside the encrypted file contains:

| Field | Description |
|---|---|
| `scheme` | `label_locked_scramble_v2` |
| `flow` | Ordered list of pipeline stage names |
| `reference_frame` | Origin lat/lng and longitude scale factor |
| `route_frames` | Per-point tangent and normal vectors |
| `scramble_radius_meters` | Radius used for label offset derivation |
| `labels` | Per-point HMAC labels |
| `scrambled_anchor_trajectory` | Label-locked fake anchor route |
| `scrambled_trajectory` | Final display fake route |
| `recovered_trajectory` | Reconstructed original route (for self-check) |
| `session_seed` | Base64-encoded HKDF session seed (required for recovery) |

## Important Limitation

If the environment cannot resolve or connect to `maps.googleapis.com`, the project falls back to local mock inputs even if `GOOGLE_MAPS_API_KEY` is present. In this case `Route source` will print as `local_mock_fallback`. Use `google_maps_probe.py` to confirm whether your environment can reach Google Maps.

## Suggested Next Improvements

- Add structured logging for route-source fallback reasons.
- Add a cached live-route JSON mode so testing can continue without live network access.
- Add unit tests per layer module.
- Add a higher-level architecture diagram for the route, scramble, crypto, and visualization layers.
