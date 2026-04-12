from trajectory_layer_bootstrap import (
    DEFAULT_ENDPOINT_OFFSET_METERS,
    DEFAULT_HEADLESS_LOCAL_TEST,
    DEFAULT_LOCAL_DIVERSITY_METERS,
    DEFAULT_MAX_POINT_OFFSET_METERS,
    DEFAULT_MIDDLE_MAX_OFFSET_METERS,
    DEFAULT_MIDDLE_MIN_OFFSET_METERS,
    DEFAULT_MIN_PRIVACY_DISTANCE_METERS,
    HKDF,
    MIDDLE_MAX_OFFSET_SAFETY,
    MIDDLE_MIN_OFFSET_SAFETY,
    OFFSET_SAFETY_MARGIN,
    base64,
    hashlib,
    hashes,
    hmac,
    json,
    math,
    os,
    secrets,
    secure_clear_dict,
)
from trajectory_layer_geometry import (
    METERS_PER_DEGREE,
    average_bearing_change,
    build_local_route_frames,
    build_reference_frame,
    centroid,
    distance_in_degrees,
    from_local_meters,
    get_segment_frame,
    interpolate,
    resample_trajectory,
    smooth_scalar_series,
    smooth_trajectory,
    to_local_meters,
)
from trajectory_layer_inputs import normalize_dynamic_factors, normalize_to_2_digits, validate_dynamic_factors


def get_application_secret():
    app_secret = os.getenv("APP_TRAJECTORY_SECRET") or os.getenv("FAKE_DATA_SECRET")
    if not app_secret:
        raise EnvironmentError("APP_TRAJECTORY_SECRET or FAKE_DATA_SECRET is required for secure session derivation")
    return app_secret


def ensure_crypto_available(feature_name):
    if HKDF is None or hashes is None:
        raise RuntimeError(f"{feature_name} requires the 'cryptography' package unless HEADLESS_LOCAL_TEST fallback is used")


def derive_session_seed(dynamic_factors, user_id, timestamp, session_id, app_secret):
    if HKDF is None or hashes is None:
        if not DEFAULT_HEADLESS_LOCAL_TEST:
            raise RuntimeError("cryptography is required for production session derivation")
        canonical_context = json.dumps(
            normalize_dynamic_factors(dynamic_factors),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.pbkdf2_hmac(
            "sha256",
            canonical_context + f"{user_id}:{timestamp}:{session_id}".encode("utf-8"),
            app_secret.encode("utf-8"),
            200000,
            dklen=32,
        )
    canonical_context = json.dumps(
        normalize_dynamic_factors(dynamic_factors),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    session_nonce = secrets.token_bytes(32)
    salt = hmac.new(app_secret.encode("utf-8"), f"{user_id}:{session_id}".encode("utf-8"), hashlib.sha256).digest()
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"trajectory-session-seed-v1")
    return hkdf.derive(session_nonce + canonical_context + f"{user_id}:{timestamp}:{session_id}".encode("utf-8"))


def generate_dynamic_code(dynamic_factors, user_id, timestamp, session_seed):
    validate_dynamic_factors(dynamic_factors)
    if not isinstance(user_id, int) or not isinstance(timestamp, int):
        raise ValueError("user_id and timestamp must be integers")
    raw_code = "".join(
        [
            normalize_to_2_digits(dynamic_factors["wind_speed"], 0, 50),
            normalize_to_2_digits(dynamic_factors["storm_index"], 0, 100),
            normalize_to_2_digits(dynamic_factors["star_index"], 0, 20),
            normalize_to_2_digits(dynamic_factors["humidity"], 0, 100),
            normalize_to_2_digits(dynamic_factors["tide_level"], 0, 100),
        ]
    )
    session_mask = hmac.new(session_seed, raw_code.encode("utf-8"), hashlib.sha256).hexdigest()
    scrambled = ""
    for index, ch in enumerate(raw_code):
        scrambled += str((int(ch) + int(session_mask[index % len(session_mask)], 16)) % 10)
    return scrambled


def decode_code(code):
    blocks = [int(code[index:index + 2]) for index in range(0, 10, 2)]
    return {
        "intensity": max(80, blocks[0] * 2),
        "direction_bias": blocks[1],
        "waypoint_density": max(2, (blocks[2] % 4) + 2),
        "noise_strength": max(1.5, blocks[3] / 25.0),
        "smooth_factor": blocks[4] / 100.0,
    }


def generate_point_labels(session_seed, point_count):
    labels = []
    for index in range(point_count):
        digest = hmac.new(session_seed, f"point-label:{index}".encode("utf-8"), hashlib.sha256).digest()[:12]
        labels.append(base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="))
    return labels


def derive_transform_from_label(label, point_index, point_ratio, point_count, scramble_radius_meters, lng_scale, session_seed):
    digest = hmac.new(session_seed, f"{label}:{point_index}".encode("utf-8"), hashlib.sha256).digest()

    def unit(start):
        return int.from_bytes(digest[start:start + 2], "big") / 65535.0

    max_offset_budget = DEFAULT_MAX_POINT_OFFSET_METERS * OFFSET_SAFETY_MARGIN
    endpoint_target = DEFAULT_ENDPOINT_OFFSET_METERS * OFFSET_SAFETY_MARGIN
    middle_min_target = DEFAULT_MIDDLE_MIN_OFFSET_METERS * MIDDLE_MIN_OFFSET_SAFETY
    middle_max_target = DEFAULT_MIDDLE_MAX_OFFSET_METERS * MIDDLE_MAX_OFFSET_SAFETY
    is_endpoint = point_index == 0 or point_index == point_count - 1
    min_target = endpoint_target if is_endpoint else middle_min_target
    max_target = max_offset_budget if is_endpoint else min(middle_max_target, max_offset_budget)
    angle = unit(0) * 2.0 * math.pi
    target_display_radius = min_target + unit(2) * max(1.0, max_target - min_target)
    display_factor = math.sqrt((math.cos(angle) ** 2) + ((math.sin(angle) ** 2) / max(lng_scale ** 2, 1e-9)))
    radius = target_display_radius / max(display_factor, 1e-9)
    offset_x = math.cos(angle) * radius
    offset_y = math.sin(angle) * radius
    display_magnitude = math.sqrt(offset_x ** 2 + ((offset_y / max(lng_scale, 1e-9)) ** 2))
    if display_magnitude > max_target:
        scale = max_target / display_magnitude
        offset_x *= scale
        offset_y *= scale
    if display_magnitude < min_target and display_magnitude > 0:
        scale = min_target / display_magnitude
        offset_x *= scale
        offset_y *= scale
    return {"matrix": (1.0, 0.0, 0.0, 1.0), "offset": (offset_x, offset_y)}


def build_smoothed_label_offsets(labels, base_trajectory, reference_frame, scramble_radius_meters, session_seed, route_frames=None):
    point_count = len(base_trajectory)
    lng_scale = reference_frame["lng_scale"]
    if route_frames is None:
        route_frames = build_local_route_frames(base_trajectory, reference_frame)
    local_tangents = route_frames["tangents"]
    local_normals = route_frames["normals"]
    corridor_lateral = []
    corridor_longitudinal = []
    curve_lateral = []
    curve_longitudinal = []
    for index, label in enumerate(labels):
        point_ratio = index / max(1, point_count - 1)
        transform = derive_transform_from_label(
            label,
            index,
            point_ratio,
            point_count,
            scramble_radius_meters,
            lng_scale,
            session_seed,
        )
        seed_x, seed_y = transform["offset"]
        tangent = local_tangents[index]
        normal = local_normals[index]
        corridor_lateral.append((seed_x * normal[0]) + (seed_y * normal[1]))
        corridor_longitudinal.append(((seed_x * tangent[0]) + (seed_y * tangent[1])) * 0.28)
        curve_strength = min(scramble_radius_meters * 0.45, 1000.0)
        curve_lateral.append(math.sin(point_ratio * 1.4 * math.pi + (index * 0.09)) * curve_strength)
        curve_longitudinal.append(math.cos(point_ratio * 1.1 * math.pi + (index * 0.05)) * curve_strength * 0.18)
    smooth_lateral = smooth_scalar_series(corridor_lateral, passes=3)
    smooth_longitudinal = smooth_scalar_series(corridor_longitudinal, passes=2)
    smooth_curve_lateral = smooth_scalar_series(curve_lateral, passes=2)
    smooth_curve_longitudinal = smooth_scalar_series(curve_longitudinal, passes=2)
    smoothed_offsets = []
    for index, (base_lat, base_lon, bend_lat, bend_lon) in enumerate(zip(smooth_lateral, smooth_longitudinal, smooth_curve_lateral, smooth_curve_longitudinal)):
        point_ratio = index / max(1, point_count - 1)
        is_endpoint = index == 0 or index == point_count - 1
        min_target = DEFAULT_ENDPOINT_OFFSET_METERS * OFFSET_SAFETY_MARGIN if is_endpoint else DEFAULT_MIDDLE_MIN_OFFSET_METERS * MIDDLE_MIN_OFFSET_SAFETY
        max_target = DEFAULT_MAX_POINT_OFFSET_METERS * OFFSET_SAFETY_MARGIN if is_endpoint else DEFAULT_MIDDLE_MAX_OFFSET_METERS * MIDDLE_MAX_OFFSET_SAFETY
        lateral_offset = (base_lat * (1.75 + 0.35 * math.sin(point_ratio * math.pi))) + bend_lat
        longitudinal_offset = (base_lon * 0.62) + (bend_lon * 0.72)
        tangent = local_tangents[index]
        normal = local_normals[index]
        offset_x = (normal[0] * lateral_offset) + (tangent[0] * longitudinal_offset)
        offset_y = (normal[1] * lateral_offset) + (tangent[1] * longitudinal_offset)
        display_magnitude = math.sqrt(offset_x ** 2 + ((offset_y / max(lng_scale, 1e-9)) ** 2))
        if display_magnitude > max_target and display_magnitude > 0:
            scale = max_target / display_magnitude
            offset_x *= scale
            offset_y *= scale
        if display_magnitude < min_target and display_magnitude > 0:
            scale = min_target / display_magnitude
            offset_x *= scale
            offset_y *= scale
        smoothed_offsets.append((offset_x, offset_y))
    return smoothed_offsets


def estimate_min_distance_meters(first_trajectory, second_trajectory):
    minimum = float("inf")
    for first_point in first_trajectory:
        for second_point in second_trajectory:
            minimum = min(minimum, distance_in_degrees(first_point, second_point) * METERS_PER_DEGREE)
    return minimum


def estimate_average_pair_distance_meters(first_trajectory, second_trajectory):
    if not first_trajectory or not second_trajectory:
        return 0.0
    aligned_second = resample_trajectory(second_trajectory, len(first_trajectory))
    total = 0.0
    for first_point, second_point in zip(first_trajectory, aligned_second):
        total += distance_in_degrees(first_point, second_point) * METERS_PER_DEGREE
    return total / len(first_trajectory)


def estimate_overlap_ratio(first_trajectory, second_trajectory, threshold_meters):
    if not first_trajectory or not second_trajectory:
        return 1.0
    overlapping_points = 0
    for first_point in first_trajectory:
        nearest_distance = min(distance_in_degrees(first_point, second_point) * METERS_PER_DEGREE for second_point in second_trajectory)
        if nearest_distance <= threshold_meters:
            overlapping_points += 1
    return overlapping_points / len(first_trajectory)


def build_expanded_fake_trajectory(anchor_trajectory, labels, reference_frame):
    if len(anchor_trajectory) < 2:
        return list(anchor_trajectory)
    base_count = len(anchor_trajectory)
    extra_count = max(1, round(base_count * 0.45))
    segment_scores = []
    for index in range(base_count - 1):
        digest = hashlib.sha256(f"expand:{labels[index]}:{labels[index + 1]}:{index}".encode("utf-8")).digest()
        segment_scores.append((int.from_bytes(digest[:4], "big"), index, digest))
    segment_scores.sort(reverse=True)
    selected_segment_map = {index: digest for _, index, digest in segment_scores[:extra_count]}
    expanded_trajectory = []
    for index in range(base_count - 1):
        current_point = anchor_trajectory[index]
        next_point = anchor_trajectory[index + 1]
        expanded_trajectory.append(current_point)
        if index not in selected_segment_map:
            continue
        digest = selected_segment_map[index]
        ratio = 0.3 + (int.from_bytes(digest[4:6], "big") / 65535.0) * 0.4
        expanded_trajectory.append(interpolate(current_point, next_point, ratio))
    expanded_trajectory.append(anchor_trajectory[-1])
    smoothed_trajectory = list(expanded_trajectory)
    preserved_start = expanded_trajectory[0]
    preserved_end = expanded_trajectory[-1]
    for _ in range(3):
        smoothed_trajectory = smooth_trajectory(smoothed_trajectory)
        smoothed_trajectory[0] = preserved_start
        smoothed_trajectory[-1] = preserved_end
    curved_display_trajectory = []
    point_count = len(smoothed_trajectory)
    for index, point in enumerate(smoothed_trajectory):
        if index == 0 or index == point_count - 1:
            curved_display_trajectory.append(point)
            continue
        ratio = index / max(1, point_count - 1)
        digest = hashlib.sha256(f"display-curve:{labels[index % len(labels)]}:{index}".encode("utf-8")).digest()
        phase = (int.from_bytes(digest[:2], "big") / 65535.0) * 2.0 * math.pi
        curve_strength = 55.0 + (int.from_bytes(digest[2:4], "big") / 65535.0) * 35.0
        prev_local = to_local_meters(smoothed_trajectory[index - 1], reference_frame)
        current_local = to_local_meters(point, reference_frame)
        next_local = to_local_meters(smoothed_trajectory[index + 1], reference_frame)
        tangent, normal = get_segment_frame(prev_local, next_local)
        lateral_curve = math.sin(ratio * 2.0 * math.pi + phase) * curve_strength + math.cos(ratio * 1.25 * math.pi + phase * 0.5) * curve_strength * 0.28
        longitudinal_curve = math.sin(ratio * 1.4 * math.pi + phase * 0.35) * curve_strength * 0.08
        curved_point = (
            current_local[0] + normal[0] * lateral_curve + tangent[0] * longitudinal_curve,
            current_local[1] + normal[1] * lateral_curve + tangent[1] * longitudinal_curve,
        )
        curved_display_trajectory.append(from_local_meters(curved_point, reference_frame))
    return curved_display_trajectory


def build_display_trajectory_from_alternate(alternate_trajectory, labels):
    """Apply only light smoothing and curvature to a true alternate route."""
    if not alternate_trajectory:
        return []

    smoothed_trajectory = list(alternate_trajectory)
    preserved_start = smoothed_trajectory[0]
    preserved_end = smoothed_trajectory[-1]
    for _ in range(2):
        smoothed_trajectory = smooth_trajectory(smoothed_trajectory)
        smoothed_trajectory[0] = preserved_start
        smoothed_trajectory[-1] = preserved_end

    reference_frame = build_reference_frame(smoothed_trajectory)
    curved_display_trajectory = []
    point_count = len(smoothed_trajectory)
    for index, point in enumerate(smoothed_trajectory):
        if index == 0 or index == point_count - 1:
            curved_display_trajectory.append(point)
            continue

        ratio = index / max(1, point_count - 1)
        digest = hashlib.sha256(
            f"alternate-display-curve:{labels[index % len(labels)]}:{index}".encode("utf-8")
        ).digest()
        phase = (int.from_bytes(digest[:2], "big") / 65535.0) * 2.0 * math.pi
        curve_strength = 18.0 + (
            int.from_bytes(digest[2:4], "big") / 65535.0
        ) * 18.0

        prev_local = to_local_meters(smoothed_trajectory[index - 1], reference_frame)
        current_local = to_local_meters(point, reference_frame)
        next_local = to_local_meters(smoothed_trajectory[index + 1], reference_frame)
        tangent, normal = get_segment_frame(prev_local, next_local)
        lateral_curve = math.sin(ratio * 1.8 * math.pi + phase) * curve_strength
        longitudinal_curve = math.cos(ratio * 1.2 * math.pi + phase * 0.5) * curve_strength * 0.05
        curved_point = (
            current_local[0]
            + normal[0] * lateral_curve
            + tangent[0] * longitudinal_curve,
            current_local[1]
            + normal[1] * lateral_curve
            + tangent[1] * longitudinal_curve,
        )
        curved_display_trajectory.append(from_local_meters(curved_point, reference_frame))

    return curved_display_trajectory


def blend_display_trajectory(anchor_trajectory, alternate_trajectory, labels):
    """Blend the alternate route with the anchor fake route into one coherent display route."""
    if not alternate_trajectory:
        return []

    target_length = max(len(alternate_trajectory), len(anchor_trajectory))
    blended_anchor = resample_trajectory(anchor_trajectory, target_length)
    blended_alternate = resample_trajectory(alternate_trajectory, target_length)
    reference_frame = build_reference_frame(blended_alternate)
    blended_trajectory = []

    for index, (anchor_point, alternate_point) in enumerate(zip(blended_anchor, blended_alternate)):
        if index == 0:
            blended_trajectory.append(anchor_point)
            continue
        if index == target_length - 1:
            blended_trajectory.append(anchor_point)
            continue

        ratio = index / max(1, target_length - 1)
        # Keep endpoints anchored to the fake route, but let the middle section inherit the alternate road.
        alternate_weight = math.sin(ratio * math.pi) ** 1.35
        anchor_weight = 1.0 - alternate_weight
        anchor_local = to_local_meters(anchor_point, reference_frame)
        alternate_local = to_local_meters(alternate_point, reference_frame)
        blended_local = (
            (anchor_local[0] * anchor_weight) + (alternate_local[0] * alternate_weight),
            (anchor_local[1] * anchor_weight) + (alternate_local[1] * alternate_weight),
        )
        blended_trajectory.append(from_local_meters(blended_local, reference_frame))

    preserved_start = blended_trajectory[0]
    preserved_end = blended_trajectory[-1]
    for _ in range(2):
        blended_trajectory = smooth_trajectory(blended_trajectory)
        blended_trajectory[0] = preserved_start
        blended_trajectory[-1] = preserved_end

    return build_display_trajectory_from_alternate(blended_trajectory, labels)


def scramble_trajectory_with_labels(real_trajectory, labels, scramble_radius_meters, session_seed, display_base_trajectory=None):
    reference_frame = build_reference_frame(real_trajectory)
    route_frames = build_local_route_frames(real_trajectory, reference_frame)
    anchor_scrambled_trajectory = []
    smoothed_offsets = build_smoothed_label_offsets(labels, real_trajectory, reference_frame, scramble_radius_meters, session_seed, route_frames=route_frames)
    for index, point in enumerate(real_trajectory):
        local_point = to_local_meters(point, reference_frame)
        offset_x, offset_y = smoothed_offsets[index]
        anchor_scrambled_trajectory.append(from_local_meters((local_point[0] + offset_x, local_point[1] + offset_y), reference_frame))
    if display_base_trajectory:
        scrambled_trajectory = blend_display_trajectory(
            anchor_scrambled_trajectory,
            display_base_trajectory,
            labels,
        )
    else:
        scrambled_trajectory = build_expanded_fake_trajectory(anchor_scrambled_trajectory, labels, reference_frame)
    return scrambled_trajectory, anchor_scrambled_trajectory, reference_frame, route_frames


def recover_trajectory_from_labels(scrambled_trajectory, labels, reference_frame, scramble_radius_meters, session_seed, route_frames=None):
    recovered_trajectory = []
    smoothed_offsets = build_smoothed_label_offsets(labels, scrambled_trajectory, reference_frame, scramble_radius_meters, session_seed, route_frames=route_frames)
    for index, point in enumerate(scrambled_trajectory):
        scrambled_local_point = to_local_meters(point, reference_frame)
        offset_x, offset_y = smoothed_offsets[index]
        recovered_trajectory.append(from_local_meters((scrambled_local_point[0] - offset_x, scrambled_local_point[1] - offset_y), reference_frame))
    return recovered_trajectory


def inject_waypoints(trajectory, waypoint_density, intensity):
    new_traj = []
    offset_scale = (intensity * 2.2) / METERS_PER_DEGREE
    for index in range(len(trajectory) - 1):
        p1 = trajectory[index]
        p2 = trajectory[index + 1]
        tangent, normal = get_segment_frame(p1, p2)
        segment_bias = (secrets.randbelow(2001) - 1000) / 1000.0
        segment_curve = (secrets.randbelow(2001) - 1000) / 1000.0
        for step in range(0, waypoint_density + 2):
            ratio = step / (waypoint_density + 1)
            mid = interpolate(p1, p2, ratio)
            lateral_offset = segment_bias * offset_scale + math.sin(ratio * math.pi) * segment_curve * offset_scale * 0.9
            longitudinal_offset = ((secrets.randbelow(801) - 400) / 400.0) * offset_scale * 0.14
            new_traj.append((mid[0] + normal[0] * lateral_offset + tangent[0] * longitudinal_offset, mid[1] + normal[1] * lateral_offset + tangent[1] * longitudinal_offset))
    return new_traj


def apply_noise(trajectory, noise_strength, direction_bias, preserve_endpoints=False):
    biased_angle = (direction_bias / 99.0) * 2 * math.pi
    result = []
    for index, (x, y) in enumerate(trajectory):
        max_offset = (noise_strength * 85) / METERS_PER_DEGREE
        prev_point = trajectory[max(0, index - 1)]
        next_point = trajectory[min(len(trajectory) - 1, index + 1)]
        tangent, normal = get_segment_frame(prev_point, next_point)
        lateral_noise = ((secrets.randbelow(2001) - 1000) / 1000.0) * max_offset
        longitudinal_noise = ((secrets.randbelow(2001) - 1000) / 1000.0) * max_offset * 0.28
        directional_dx = math.cos(biased_angle) * max_offset * 0.45
        directional_dy = math.sin(biased_angle) * max_offset * 0.45
        result.append((x + normal[0] * lateral_noise + tangent[0] * longitudinal_noise + directional_dx, y + normal[1] * lateral_noise + tangent[1] * longitudinal_noise + directional_dy))
    return result


def apply_corridor_following(base_trajectory, candidate_trajectory, lateral_offset_meters):
    if not base_trajectory or not candidate_trajectory:
        return candidate_trajectory
    aligned_base = resample_trajectory(base_trajectory, len(candidate_trajectory))
    offset_scale_deg = lateral_offset_meters / METERS_PER_DEGREE
    base_offsets = [((secrets.randbelow(2001) - 1000) / 1000.0) * offset_scale_deg for _ in range(len(candidate_trajectory))]
    smoothed_offsets = smooth_scalar_series(base_offsets, passes=3)
    adjusted = []
    for index, candidate_point in enumerate(candidate_trajectory):
        prev_point = aligned_base[max(0, index - 1)]
        next_point = aligned_base[min(len(aligned_base) - 1, index + 1)]
        tangent, normal = get_segment_frame(prev_point, next_point)
        anchor = aligned_base[index]
        adjusted.append((anchor[0] + normal[0] * smoothed_offsets[index] + tangent[0] * 0.16 * (candidate_point[0] - anchor[0]), anchor[1] + normal[1] * smoothed_offsets[index] + tangent[1] * 0.16 * (candidate_point[1] - anchor[1])))
    return adjusted


def apply_alternative_route_profile(base_trajectory, session_seed, separation_meters):
    if not base_trajectory:
        return []
    separation_deg = separation_meters / METERS_PER_DEGREE
    branch_side = 1 if (session_seed[4] % 2 == 0) else -1
    mid_bias = 0.78 + (session_seed[5] / 255.0) * 0.18
    branch_frequency = 1 + (session_seed[6] % 2)
    profiled = []
    for index, point in enumerate(base_trajectory):
        ratio = index / max(1, len(base_trajectory) - 1)
        prev_point = base_trajectory[max(0, index - 1)]
        next_point = base_trajectory[min(len(base_trajectory) - 1, index + 1)]
        tangent, normal = get_segment_frame(prev_point, next_point)
        branch_window = math.sin(ratio * math.pi) ** 1.35
        branch_shape = math.sin(ratio * math.pi * branch_frequency) * 0.16
        lateral_offset = separation_deg * branch_side * branch_window * (mid_bias + branch_shape)
        longitudinal_offset = separation_deg * 0.12 * math.sin(ratio * 2 * math.pi + (session_seed[7] / 255.0) * math.pi)
        profiled.append((point[0] + normal[0] * lateral_offset + tangent[0] * longitudinal_offset, point[1] + normal[1] * lateral_offset + tangent[1] * longitudinal_offset))
    return profiled


def quantize_trajectory(trajectory, grid_size_meters):
    if grid_size_meters <= 0:
        return list(trajectory)
    grid_size_deg = grid_size_meters / METERS_PER_DEGREE
    return [(round(lat / grid_size_deg) * grid_size_deg, round(lng / grid_size_deg) * grid_size_deg) for lat, lng in trajectory]


def apply_privacy_warp(trajectory, session_seed, amplitude_meters):
    if len(trajectory) < 2:
        return trajectory
    amplitude_deg = amplitude_meters / METERS_PER_DEGREE
    phase = (session_seed[0] / 255.0) * 2 * math.pi
    secondary_phase = (session_seed[1] / 255.0) * 2 * math.pi
    frequency = 1 + (session_seed[2] % 3)
    warped = []
    for index, (lat, lng) in enumerate(trajectory):
        ratio = index / max(1, len(trajectory) - 1)
        prev_point = trajectory[max(0, index - 1)]
        next_point = trajectory[min(len(trajectory) - 1, index + 1)]
        tangent, normal = get_segment_frame(prev_point, next_point)
        lateral_shift = math.sin(ratio * frequency * 2 * math.pi + phase) * amplitude_deg
        longitudinal_shift = math.cos(ratio * (frequency + 1) * 2 * math.pi + secondary_phase) * amplitude_deg * 0.28
        warped.append((lat + normal[0] * lateral_shift + tangent[0] * longitudinal_shift, lng + normal[1] * lateral_shift + tangent[1] * longitudinal_shift))
    return warped


def build_privacy_profile(dynamic_factors, session_seed):
    normalized = normalize_dynamic_factors(dynamic_factors)
    weather_pressure = normalized["storm_index"] * 0.35 + normalized["humidity"] * 0.15 + normalized["wind_speed"] * 0.3 + normalized["tide_level"] * 0.2
    base_min = 80 + int(weather_pressure * 0.4)
    spread = 30 + int(normalized["star_index"] * 0.8)
    selector = int.from_bytes(session_seed[:2], "big") / 65535
    min_separation_meters = max(DEFAULT_MIN_PRIVACY_DISTANCE_METERS, int(base_min + spread * selector))
    return {
        "min_separation_meters": min_separation_meters,
        "centroid_min_meters": int(min_separation_meters * 1.08),
        "max_shift_meters": int(min_separation_meters * 5),
        "local_window_size": 3 + (session_seed[2] % 3),
        "local_diversity_meters": int(max(28, min_separation_meters * 0.22)),
        "quantization_grid_meters": 3 + (session_seed[3] % 4),
        "warp_amplitude_meters": int(max(16, min_separation_meters * 0.13)),
        "corridor_offset_meters": int(max(min_separation_meters * 0.8, 120)),
        "alternative_route_meters": int(max(min_separation_meters * 0.95, 220)),
        "session_id_fragment": base64.urlsafe_b64encode(session_seed[:6]).decode("ascii"),
    }


def validate_local_structure_diversity(real_trajectory, fake_trajectory, window_size, min_window_distance_meters):
    if len(real_trajectory) < window_size or len(fake_trajectory) < window_size:
        return True
    real_sample = resample_trajectory(real_trajectory, len(real_trajectory))
    fake_sample = resample_trajectory(fake_trajectory, len(real_trajectory))
    min_window_distance_deg = min_window_distance_meters / METERS_PER_DEGREE
    for start in range(0, len(real_sample) - window_size + 1):
        real_window = real_sample[start:start + window_size]
        fake_window = fake_sample[start:start + window_size]
        average_distance = sum(distance_in_degrees(real_point, fake_point) for real_point, fake_point in zip(real_window, fake_window)) / window_size
        if average_distance < min_window_distance_deg:
            return False
    return True


def validate_fake_trajectory(real_trajectory, fake_trajectory, privacy_profile):
    if not real_trajectory or not fake_trajectory:
        return False
    min_separation_deg = privacy_profile["min_separation_meters"] / METERS_PER_DEGREE
    centroid_min_deg = privacy_profile["centroid_min_meters"] / METERS_PER_DEGREE
    max_shift_deg = privacy_profile["max_shift_meters"] / METERS_PER_DEGREE
    for fx, fy in fake_trajectory:
        if any(distance_in_degrees((fx, fy), (rx, ry)) < min_separation_deg for rx, ry in real_trajectory):
            return False
    shift_distance = distance_in_degrees(centroid(real_trajectory), centroid(fake_trajectory))
    if shift_distance < centroid_min_deg or shift_distance > max_shift_deg:
        return False
    if distance_in_degrees(fake_trajectory[0], real_trajectory[0]) < min_separation_deg:
        return False
    if distance_in_degrees(fake_trajectory[-1], real_trajectory[-1]) < min_separation_deg:
        return False
    if not validate_local_structure_diversity(real_trajectory, fake_trajectory, privacy_profile["local_window_size"], privacy_profile["local_diversity_meters"]):
        return False
    if abs(average_bearing_change(fake_trajectory) - average_bearing_change(real_trajectory)) < 0.008:
        return False
    return True


def build_label_locked_trajectory_package(real_trajectory, dynamic_factors, user_id, timestamp, max_retries=10, display_base_trajectory=None):
    app_secret = get_application_secret()
    session_id = secrets.token_hex(16)
    session_seed = derive_session_seed(dynamic_factors, user_id, timestamp, session_id, app_secret)
    privacy_profile = build_privacy_profile(dynamic_factors, session_seed)
    labels = generate_point_labels(session_seed, len(real_trajectory))
    base_radius = max(DEFAULT_MIDDLE_MIN_OFFSET_METERS, privacy_profile["min_separation_meters"] * 0.4)
    scrambled_trajectory = None
    anchor_scrambled_trajectory = None
    reference_frame = None
    route_frames = None
    scramble_radius_meters = None
    best_candidate = None
    best_anchor_candidate = None
    best_reference_frame = None
    best_route_frames = None
    best_scramble_radius = None
    best_score = None
    for attempt in range(max_retries):
        scramble_radius_meters = base_radius * (1.34 + attempt * 0.58)
        candidate, candidate_anchor, candidate_reference_frame, candidate_route_frames = scramble_trajectory_with_labels(
            real_trajectory,
            labels,
            scramble_radius_meters,
            session_seed,
            display_base_trajectory=display_base_trajectory,
        )
        min_distance = estimate_min_distance_meters(candidate, real_trajectory)
        average_distance = estimate_average_pair_distance_meters(candidate, real_trajectory)
        overlap_ratio = estimate_overlap_ratio(candidate, real_trajectory, threshold_meters=max(privacy_profile["min_separation_meters"] * 0.85, 260))
        score = average_distance - (overlap_ratio * 120.0) + min_distance
        if best_score is None or score > best_score:
            best_candidate = candidate
            best_anchor_candidate = candidate_anchor
            best_reference_frame = candidate_reference_frame
            best_route_frames = candidate_route_frames
            best_scramble_radius = scramble_radius_meters
            best_score = score
        if min_distance >= max(DEFAULT_LOCAL_DIVERSITY_METERS * 0.45, privacy_profile["min_separation_meters"] * 0.12) and average_distance >= privacy_profile["centroid_min_meters"] * 0.18 and overlap_ratio <= 0.95:
            scrambled_trajectory = candidate
            anchor_scrambled_trajectory = candidate_anchor
            reference_frame = candidate_reference_frame
            route_frames = candidate_route_frames
            break
    if scrambled_trajectory is None or reference_frame is None:
        scrambled_trajectory = best_candidate
        anchor_scrambled_trajectory = best_anchor_candidate
        reference_frame = best_reference_frame
        route_frames = best_route_frames
        scramble_radius_meters = best_scramble_radius
    if scrambled_trajectory is None or reference_frame is None:
        raise RuntimeError("Unable to generate a sufficiently scrambled label-locked trajectory")
    recovered_trajectory = recover_trajectory_from_labels(anchor_scrambled_trajectory, labels, reference_frame, scramble_radius_meters, session_seed, route_frames=route_frames)
    secure_clear_dict(privacy_profile)
    return {
        "scheme": "label_locked_scramble_v2",
        "flow": [
            "input_original_blue_trajectory",
            "generate_secret_label_per_point",
            "scramble_points_with_label_driven_transform",
            "store_red_trajectory_and_labels",
            "recover_blue_trajectory_from_labels",
        ],
        "reference_frame": reference_frame,
        "scramble_radius_meters": round(scramble_radius_meters, 3),
        "labels": labels,
        "route_frames": route_frames,
        "scrambled_anchor_trajectory": anchor_scrambled_trajectory,
        "scrambled_trajectory": scrambled_trajectory,
        "recovered_trajectory": recovered_trajectory,
        "session_seed": base64.urlsafe_b64encode(session_seed).decode("ascii"),
    }


def generate_fake_trajectory(real_trajectory, dynamic_factors, user_id, timestamp, max_retries=10):
    package = build_label_locked_trajectory_package(real_trajectory, dynamic_factors, user_id=user_id, timestamp=timestamp, max_retries=max_retries)
    return package["scrambled_trajectory"]
