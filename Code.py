from trajectory_layer_bootstrap import (
    DEFAULT_ENABLE_VISUAL_OUTPUTS,
    DEFAULT_HEADLESS_LOCAL_TEST,
    DEFAULT_LOCAL_DIVERSITY_METERS,
    DEFAULT_RUN_SELF_TEST,
    gc,
    os,
    plt,
    secure_clear_dict,
    secure_clear_list,
)
from trajectory_layer_crypto import (
    get_current_key_version,
    get_storage_secret_registry,
    save_encrypted_fake_trajectory_with_secret,
    verify_encrypted_round_trip,
    verify_recovery_matches_original,
)
from trajectory_layer_geometry import check_all_distinct
from trajectory_layer_inputs import build_dynamic_context, get_environmental_data, get_google_maps_route_bundle, get_local_test_inputs
from trajectory_layer_scramble import build_label_locked_trajectory_package
from trajectory_layer_visualization import plot_trajectories, render_real_map


def main():
    """Run the layered trajectory pipeline from acquisition to encrypted storage."""
    user_id = int(os.getenv("USER_ID", 123))
    timestamp = int(os.getenv("TIMESTAMP", 1700000000))
    prefer_live_google_route = os.getenv("PREFER_LIVE_GOOGLE_ROUTE", "1") != "0"
    has_google_maps_key = bool(os.getenv("GOOGLE_MAPS_API_KEY"))
    route_source = "local_mock"
    alternate_trajectory = None

    use_live_google_route = has_google_maps_key and (
        prefer_live_google_route or not DEFAULT_HEADLESS_LOCAL_TEST
    )

    if use_live_google_route:
        try:
            origin = os.getenv("ORIGIN", "RMIT Melbourne, Australia")
            destination = os.getenv("DESTINATION", "Deakin Burwood, Australia")
            route_bundle = get_google_maps_route_bundle(origin, destination)
            real_trajectory = route_bundle["real_trajectory"]
            alternate_trajectory = route_bundle["alternate_trajectory"]
            avg_lat = sum(lat for lat, _ in real_trajectory) / len(real_trajectory)
            avg_lng = sum(lng for _, lng in real_trajectory) / len(real_trajectory)
            env_data = get_environmental_data(avg_lat, avg_lng)
            dynamic_factors = build_dynamic_context(env_data)
            route_source = "google_maps_live"
        except Exception:
            real_trajectory, dynamic_factors, env_data = get_local_test_inputs()
            route_source = "local_mock_fallback"
    else:
        real_trajectory, dynamic_factors, env_data = get_local_test_inputs()
        if DEFAULT_HEADLESS_LOCAL_TEST:
            print("Running in headless local-test mode with mock inputs only.")

    protected_package = build_label_locked_trajectory_package(
        real_trajectory,
        dynamic_factors,
        user_id=user_id,
        timestamp=timestamp,
        display_base_trajectory=alternate_trajectory,
    )
    scrambled_trajectory = protected_package["scrambled_trajectory"]
    recovered_trajectory = protected_package["recovered_trajectory"]

    all_distinct = check_all_distinct(
        scrambled_trajectory,
        real_trajectory,
        tolerance_meters=max(20, int(DEFAULT_LOCAL_DIVERSITY_METERS * 0.2)),
    )
    if not all_distinct:
        print("Warning: scrambled trajectory remains close to the real route in some local sections.")

    verify_recovery_matches_original(protected_package, real_trajectory)

    fake_storage_path = os.getenv("FAKE_DATA_PATH", "encrypted_fake_points.bin")
    secret_registry = get_storage_secret_registry()
    current_key_version = get_current_key_version(secret_registry)
    current_secret = secret_registry[current_key_version]
    save_encrypted_fake_trajectory_with_secret(
        protected_package,
        current_secret,
        fake_storage_path,
        user_id=user_id,
        key_version=current_key_version,
    )
    print(f"Route source: {route_source}")
    print(f"Encrypted protected trajectory package saved to {fake_storage_path}")
    print(f"Stored {len(scrambled_trajectory)} scrambled red-line points and {len(protected_package['labels'])} labels.")
    print(f"Active storage key version: v{current_key_version}")
    print("Recovery check passed: labels reconstruct the original blue trajectory.")

    if DEFAULT_RUN_SELF_TEST:
        verify_encrypted_round_trip(fake_storage_path, secret_registry, user_id, protected_package)
        print("Self-test passed: encrypted file decrypted back to the same protected trajectory package.")

    if DEFAULT_ENABLE_VISUAL_OUTPUTS:
        if plt is not None:
            plot_trajectories(real_trajectory, scrambled_trajectory)
        map_path = render_real_map(real_trajectory, scrambled_trajectory)
        print(f"Map saved to {map_path}")

    secure_clear_dict(dynamic_factors)
    secure_clear_dict(protected_package)
    secure_clear_list(real_trajectory)
    secure_clear_list(scrambled_trajectory)
    secure_clear_list(recovered_trajectory)
    gc.collect()


if __name__ == "__main__":
    main()
