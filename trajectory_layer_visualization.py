from trajectory_layer_bootstrap import folium, plt
from trajectory_layer_geometry import centroid
from trajectory_layer_scramble import estimate_average_pair_distance_meters


def project_trajectory_to_canvas(trajectory, bounds, width, height, padding):
    min_lat, max_lat, min_lng, max_lng = bounds
    lat_span = max(max_lat - min_lat, 1e-9)
    lng_span = max(max_lng - min_lng, 1e-9)
    projected = []
    for lat, lng in trajectory:
        x = padding + ((lng - min_lng) / lng_span) * (width - 2 * padding)
        y = height - padding - ((lat - min_lat) / lat_span) * (height - 2 * padding)
        projected.append((round(x, 2), round(y, 2)))
    return projected


def compute_trajectory_bounds(trajectory):
    if not trajectory:
        return (0.0, 1.0, 0.0, 1.0)
    lats = [lat for lat, _ in trajectory]
    lngs = [lng for _, lng in trajectory]
    lat_pad = max((max(lats) - min(lats)) * 0.18, 0.002)
    lng_pad = max((max(lngs) - min(lngs)) * 0.18, 0.002)
    return (min(lats) - lat_pad, max(lats) + lat_pad, min(lngs) - lng_pad, max(lngs) + lng_pad)


def estimate_centroid_shift_meters(real_trajectory, fake_trajectory):
    if not real_trajectory or not fake_trajectory:
        return 0.0
    from trajectory_layer_geometry import METERS_PER_DEGREE, distance_in_degrees
    return distance_in_degrees(centroid(real_trajectory), centroid(fake_trajectory)) * METERS_PER_DEGREE


def render_svg_map(real_trajectory=None, fake_trajectory=None, file_path="trajectory_map.html"):
    if not real_trajectory and not fake_trajectory:
        raise ValueError("At least one trajectory must be provided")
    panel_width = 500
    panel_height = 520
    padding = 48
    width = 1100
    height = 760
    real_bounds = compute_trajectory_bounds(real_trajectory) if real_trajectory else None
    fake_bounds = compute_trajectory_bounds(fake_trajectory) if fake_trajectory else None
    real_points = project_trajectory_to_canvas(real_trajectory, real_bounds, panel_width, panel_height, padding) if real_trajectory else []
    fake_points = project_trajectory_to_canvas(fake_trajectory, fake_bounds, panel_width, panel_height, padding) if fake_trajectory else []
    average_pair_distance = estimate_average_pair_distance_meters(real_trajectory, fake_trajectory)
    centroid_shift = estimate_centroid_shift_meters(real_trajectory, fake_trajectory)

    def polyline(points):
        return " ".join(f"{x},{y}" for x, y in points)

    def marker_with_label(point, fill, label, label_side="right"):
        if not point:
            return ""
        x, y = point
        label_dx = 18 if label_side == "right" else -18
        text_anchor = "start" if label_side == "right" else "end"
        return f"<g><circle cx='{x}' cy='{y}' r='11' fill='{fill}' opacity='0.18' /><circle cx='{x}' cy='{y}' r='6.5' fill='{fill}' stroke='white' stroke-width='2.5' /><text x='{x + label_dx}' y='{y - 10}' fill='{fill}' font-size='14' font-weight='700' text-anchor='{text_anchor}'>{label}</text></g>"

    def render_panel(points, color, title, subtitle):
        if not points:
            return ""
        dash_attr = " stroke-dasharray='16 11'" if color == "var(--fake)" else ""
        return f"<g><rect x='0' y='0' width='{panel_width}' height='{panel_height}' rx='28' fill='rgba(255,253,250,0.96)' stroke='rgba(214,196,170,0.95)' stroke-width='1.5' /><rect x='18' y='70' width='{panel_width - 36}' height='{panel_height - 92}' rx='22' fill='rgba(248,241,229,0.58)' stroke='rgba(222,211,193,0.85)' stroke-width='1.2' /><text x='24' y='34' fill='var(--ink)' font-size='22' font-weight='700'>{title}</text><text x='24' y='56' fill='var(--muted)' font-size='13'>{subtitle}</text><polyline points='{polyline(points)}' fill='none' stroke='var(--road-edge)' stroke-width='14' stroke-linecap='round' stroke-linejoin='round' opacity='0.42' /><polyline points='{polyline(points)}' fill='none' stroke='{color}' stroke-width='8' stroke-linecap='round' stroke-linejoin='round' opacity='0.94'{dash_attr} />{marker_with_label(points[0], color, 'Start', 'right')}{marker_with_label(points[-1], color, 'End', 'left')}</g>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trajectory Map</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f1e8;
      --panel: #fffdfa;
      --grid: #ded3c1;
      --ink: #1f2937;
      --muted: #6b7280;
      --real: #2563eb;
      --fake: #dc2626;
      --road: #efe5d5;
      --road-edge: #d6c4aa;
    }}
    body {{ margin: 0; font-family: Georgia, 'Times New Roman', serif; background: radial-gradient(circle at top left, #fff7ec 0%, #f4ebdb 38%, #eadecc 100%); color: var(--ink); }}
    .wrap {{ max-width: 1160px; margin: 24px auto; padding: 0 20px 24px; }}
    .card {{ background: var(--panel); border: 1px solid #e7dece; border-radius: 20px; box-shadow: 0 18px 50px rgba(76, 58, 33, 0.08); overflow: hidden; }}
    .head {{ display: flex; justify-content: space-between; gap: 16px; align-items: end; padding: 22px 24px 14px; }}
    .title {{ font-size: 28px; line-height: 1.1; margin: 0; }}
    .sub {{ margin: 6px 0 0; color: var(--muted); font-size: 14px; }}
    .metrics {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }}
    .pill {{ padding: 7px 10px; border-radius: 999px; background: #f6efe3; border: 1px solid #eadfcf; color: #5b6472; font-size: 12px; letter-spacing: 0.02em; }}
    .legend {{ display: flex; gap: 14px; font-size: 14px; color: var(--muted); flex-wrap: wrap; justify-content: flex-end; }}
    .dot {{ width: 11px; height: 11px; border-radius: 999px; display: inline-block; margin-right: 6px; vertical-align: middle; }}
    svg {{ display: block; width: 100%; height: auto; background: linear-gradient(0deg, rgba(255,255,255,0.72), rgba(255,255,255,0.72)), radial-gradient(circle at top left, #f8f1e5, #f0e4d2 60%, #e8dbc7 100%); }}
    .footer {{ padding: 14px 24px 22px; color: var(--muted); font-size: 13px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <div class="head">
        <div>
          <h1 class="title">Trajectory Visualisation</h1>
          <p class="sub">Independent viewport comparison with a combined corridor-based path and curved distortion overlay.</p>
          <div class="metrics">
            <span class="pill">Real points: {len(real_trajectory) if real_trajectory else 0}</span>
            <span class="pill">Fake points: {len(fake_trajectory) if fake_trajectory else 0}</span>
            <span class="pill">Mode: {'Comparison' if real_trajectory and fake_trajectory else 'Single route'}</span>
            {"<span class='pill'>Avg pair distance: " + str(round(average_pair_distance, 1)) + " m</span>" if real_trajectory and fake_trajectory else ""}
            {"<span class='pill'>Centroid shift: " + str(round(centroid_shift, 1)) + " m</span>" if real_trajectory and fake_trajectory else ""}
          </div>
        </div>
        <div class="legend">
          {"<span><span class='dot' style='background: var(--real)'></span>Blue = original trajectory</span>" if real_trajectory else ""}
          {"<span><span class='dot' style='background: var(--fake)'></span>Red = scrambled corridor + curved distortion</span>" if fake_trajectory else ""}
        </div>
      </div>
      <svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Trajectory map">
        <defs><pattern id="grid" width="70" height="70" patternUnits="userSpaceOnUse"><path d="M 70 0 L 0 0 0 70" fill="none" stroke="var(--grid)" stroke-width="1"/></pattern></defs>
        <rect width="{width}" height="{height}" fill="url(#grid)" />
        {"<g transform='translate(36 116)'>" + render_panel(real_points, "var(--real)", "Blue Line", "Original trajectory fitted in its own viewport") + "</g>" if real_points else ""}
        {"<g transform='translate(564 116)'>" + render_panel(fake_points, "var(--fake)", "Red Line", "Corridor-based path blended with curved distortion") + "</g>" if fake_points else ""}
        {"<line x1='550' y1='130' x2='550' y2='660' stroke='rgba(214,196,170,0.8)' stroke-width='2' stroke-dasharray='7 10' />" if real_points and fake_points else ""}
      </svg>
      <div class="footer">Blue is the original input route. Red is the stored scrambled route produced by combining corridor-based displacement with curved distortion. Each panel uses its own fitted bounds so visual similarity is not understated by a shared map extent.</div>
    </div>
  </div>
</body>
</html>"""
    with open(file_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(html)
    return file_path


def plot_trajectories(real, fake):
    if plt is None:
        raise RuntimeError("matplotlib is not installed")
    rx, ry = zip(*real)
    fx, fy = zip(*fake)
    plt.figure(figsize=(6, 6))
    plt.plot(rx, ry, "b-o", label="Real Trajectory")
    plt.plot(fx, fy, "r-o", label="Fake Trajectory")
    plt.legend()
    plt.title("Trajectory Obfuscation")
    plt.show()


def render_real_map(real_trajectory, fake_trajectory, file_path="trajectory_map.html"):
    if folium is None:
        return render_svg_map(real_trajectory=real_trajectory, fake_trajectory=fake_trajectory, file_path=file_path)
    center_lat = sum(lat for lat, _ in real_trajectory) / len(real_trajectory)
    center_lng = sum(lng for _, lng in real_trajectory) / len(real_trajectory)
    map_object = folium.Map(location=[center_lat, center_lng], zoom_start=13)
    folium.PolyLine(locations=[(lat, lng) for lat, lng in real_trajectory], color="blue", weight=4, opacity=0.8, tooltip="Real route").add_to(map_object)
    folium.PolyLine(locations=[(lat, lng) for lat, lng in fake_trajectory], color="red", weight=4, opacity=0.8, tooltip="Fake route").add_to(map_object)
    folium.Marker(real_trajectory[0], tooltip="Real start", icon=folium.Icon(color="blue")).add_to(map_object)
    folium.Marker(real_trajectory[-1], tooltip="Real end", icon=folium.Icon(color="blue", icon="flag")).add_to(map_object)
    folium.Marker(fake_trajectory[0], tooltip="Fake start", icon=folium.Icon(color="red")).add_to(map_object)
    folium.Marker(fake_trajectory[-1], tooltip="Fake end", icon=folium.Icon(color="red", icon="flag")).add_to(map_object)
    map_object.save(file_path)
    return file_path


def render_fake_map(fake_trajectory, file_path="fake_trajectory_map.html"):
    if folium is None:
        return render_svg_map(fake_trajectory=fake_trajectory, file_path=file_path)
    center_lat = sum(lat for lat, _ in fake_trajectory) / len(fake_trajectory)
    center_lng = sum(lng for _, lng in fake_trajectory) / len(fake_trajectory)
    map_object = folium.Map(location=[center_lat, center_lng], zoom_start=13)
    folium.PolyLine(locations=[(lat, lng) for lat, lng in fake_trajectory], color="red", weight=4, opacity=0.8, tooltip="Fake route").add_to(map_object)
    folium.Marker(fake_trajectory[0], tooltip="Fake start", icon=folium.Icon(color="red")).add_to(map_object)
    folium.Marker(fake_trajectory[-1], tooltip="Fake end", icon=folium.Icon(color="red", icon="flag")).add_to(map_object)
    map_object.save(file_path)
    return file_path
