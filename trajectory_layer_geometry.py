from trajectory_layer_bootstrap import METERS_PER_DEGREE, math, secrets


def interpolate(p1, p2, ratio):
    x = p1[0] + (p2[0] - p1[0]) * ratio
    y = p1[1] + (p2[1] - p1[1]) * ratio
    return (x, y)


def get_segment_frame(p1, p2):
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return (1.0, 0.0), (0.0, 1.0)
    tangent = (dx / length, dy / length)
    normal = (-tangent[1], tangent[0])
    return tangent, normal


def distance_in_degrees(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


def centroid(trajectory):
    count = len(trajectory)
    return (
        sum(lat for lat, _ in trajectory) / count,
        sum(lng for _, lng in trajectory) / count,
    )


def build_reference_frame(trajectory):
    origin_lat, origin_lng = centroid(trajectory)
    lng_scale = max(math.cos(math.radians(origin_lat)), 0.2)
    return {
        "origin_lat": origin_lat,
        "origin_lng": origin_lng,
        "lng_scale": lng_scale,
    }


def to_local_meters(point, reference_frame):
    lat, lng = point
    return (
        (lat - reference_frame["origin_lat"]) * METERS_PER_DEGREE,
        (lng - reference_frame["origin_lng"]) * METERS_PER_DEGREE * reference_frame["lng_scale"],
    )


def from_local_meters(point, reference_frame):
    x, y = point
    return (
        reference_frame["origin_lat"] + (x / METERS_PER_DEGREE),
        reference_frame["origin_lng"] + (y / (METERS_PER_DEGREE * reference_frame["lng_scale"])),
    )


def build_local_route_frames(base_trajectory, reference_frame):
    local_points = [to_local_meters(point, reference_frame) for point in base_trajectory]
    local_tangents = []
    local_normals = []
    for index in range(len(local_points)):
        prev_point = local_points[max(0, index - 1)]
        next_point = local_points[min(len(local_points) - 1, index + 1)]
        tangent, normal = get_segment_frame(prev_point, next_point)
        local_tangents.append(tangent)
        local_normals.append(normal)
    return {"tangents": local_tangents, "normals": local_normals}


def shift_trajectory(trajectory, shift_lat_deg, shift_lng_deg):
    return [(lat + shift_lat_deg, lng + shift_lng_deg) for lat, lng in trajectory]


def average_bearing_change(trajectory):
    if len(trajectory) < 3:
        return 0.0
    changes = []
    for index in range(1, len(trajectory) - 1):
        prev_dx = trajectory[index][0] - trajectory[index - 1][0]
        prev_dy = trajectory[index][1] - trajectory[index - 1][1]
        next_dx = trajectory[index + 1][0] - trajectory[index][0]
        next_dy = trajectory[index + 1][1] - trajectory[index][1]
        prev_angle = math.atan2(prev_dy, prev_dx)
        next_angle = math.atan2(next_dy, next_dx)
        changes.append(abs(next_angle - prev_angle))
    return sum(changes) / len(changes)


def resample_trajectory(trajectory, target_length):
    if not trajectory or target_length <= 0:
        return []
    if len(trajectory) == target_length:
        return list(trajectory)
    if target_length == 1:
        return [trajectory[0]]
    step = (len(trajectory) - 1) / (target_length - 1)
    resampled = []
    for index in range(target_length):
        point_index = min(len(trajectory) - 1, int(round(index * step)))
        resampled.append(trajectory[point_index])
    return resampled


def densify_trajectory(trajectory, target_length):
    if not trajectory or target_length <= len(trajectory):
        return list(trajectory)
    if len(trajectory) == 1:
        return list(trajectory)
    segment_lengths = []
    total_length = 0.0
    for index in range(len(trajectory) - 1):
        segment_length = distance_in_degrees(trajectory[index], trajectory[index + 1])
        segment_lengths.append(segment_length)
        total_length += segment_length
    if total_length <= 0:
        return resample_trajectory(trajectory, target_length)
    cumulative_lengths = [0.0]
    running_total = 0.0
    for segment_length in segment_lengths:
        running_total += segment_length
        cumulative_lengths.append(running_total)
    densified = []
    for index in range(target_length):
        target_distance = (total_length * index) / (target_length - 1)
        segment_index = 0
        while segment_index < len(segment_lengths) - 1 and cumulative_lengths[segment_index + 1] < target_distance:
            segment_index += 1
        segment_start = trajectory[segment_index]
        segment_end = trajectory[segment_index + 1]
        segment_length = segment_lengths[segment_index]
        if segment_length <= 0:
            densified.append(segment_start)
            continue
        local_distance = target_distance - cumulative_lengths[segment_index]
        ratio = local_distance / segment_length
        densified.append(interpolate(segment_start, segment_end, ratio))
    return densified


def smooth_scalar_series(values, passes=2):
    result = list(values)
    for _ in range(passes):
        if len(result) < 3:
            return result
        smoothed = [result[0]]
        for index in range(1, len(result) - 1):
            smoothed.append((result[index - 1] + result[index] + result[index + 1]) / 3.0)
        smoothed.append(result[-1])
        result = smoothed
    return result


def smooth_trajectory(trajectory):
    if len(trajectory) < 3:
        return list(trajectory)
    smoothed = []
    length = len(trajectory)
    for index in range(length):
        if index == 0:
            x = (trajectory[0][0] + trajectory[1][0]) / 2
            y = (trajectory[0][1] + trajectory[1][1]) / 2
        elif index == length - 1:
            x = (trajectory[-2][0] + trajectory[-1][0]) / 2
            y = (trajectory[-2][1] + trajectory[-1][1]) / 2
        else:
            x = (trajectory[index - 1][0] + trajectory[index][0] + trajectory[index + 1][0]) / 3
            y = (trajectory[index - 1][1] + trajectory[index][1] + trajectory[index + 1][1]) / 3
        smoothed.append((x, y))
    return smoothed


def ensure_unique_and_distinct(fake_trajectory, real_trajectory, min_gap_meters=0.5):
    min_gap_deg = min_gap_meters / METERS_PER_DEGREE
    result = []
    for x, y in fake_trajectory:
        attempt = 0
        while True:
            too_close_real = any(math.hypot(x - rx, y - ry) <= min_gap_deg for rx, ry in real_trajectory)
            too_close_fake = any(math.hypot(x - sx, y - sy) <= min_gap_deg for sx, sy in result)
            if not too_close_real and not too_close_fake:
                break
            if attempt > 10:
                break
            x += (secrets.randbelow(2001) - 1000) / 1000000 * min_gap_deg * 2
            y += (secrets.randbelow(2001) - 1000) / 1000000 * min_gap_deg * 2
            attempt += 1
        result.append((x, y))
    return result


def check_all_distinct(fake_trajectory, real_trajectory, tolerance_meters=0.1):
    tolerance_deg = tolerance_meters / METERS_PER_DEGREE
    for fx, fy in fake_trajectory:
        for rx, ry in real_trajectory:
            if math.hypot(fx - rx, fy - ry) <= tolerance_deg:
                return False
    return True
