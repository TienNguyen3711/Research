from trajectory_layer_bootstrap import (
    DEFAULT_KEY_VERSION,
    FAKE_DATA_FILE_MAGIC_BYTES,
    FAKE_DATA_FILE_VERSION,
    FAKE_DATA_METADATA_LENGTH_BYTES,
    Fernet,
    KDF_SALT_SIZE,
    PBKDF2HMAC,
    base64,
    hashlib,
    hashes,
    hmac,
    json,
    os,
    secrets,
)
from trajectory_layer_geometry import METERS_PER_DEGREE, distance_in_degrees
from trajectory_layer_scramble import recover_trajectory_from_labels


def get_storage_secret_registry():
    registry = {}
    for key, value in os.environ.items():
        if key.startswith("FAKE_DATA_SECRET_V") and value:
            version_suffix = key.removeprefix("FAKE_DATA_SECRET_V")
            if version_suffix.isdigit():
                registry[int(version_suffix)] = value
    legacy_secret = os.getenv("FAKE_DATA_SECRET")
    if legacy_secret and DEFAULT_KEY_VERSION not in registry:
        registry[DEFAULT_KEY_VERSION] = legacy_secret
    if not registry:
        raise EnvironmentError("At least one storage secret is required via FAKE_DATA_SECRET or FAKE_DATA_SECRET_V<version>")
    return registry


def get_current_key_version(secret_registry):
    configured_version = os.getenv("FAKE_DATA_SECRET_CURRENT_VERSION")
    if configured_version and configured_version.isdigit():
        version = int(configured_version)
        if version not in secret_registry:
            raise EnvironmentError(f"Configured FAKE_DATA_SECRET_CURRENT_VERSION={version} has no matching secret")
        return version
    return max(secret_registry)


def derive_user_scoped_secret(secret, user_id, key_version):
    scoped = hmac.new(secret.encode("utf-8"), f"user:{user_id}:key_version:{key_version}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(scoped).decode("ascii")


def encrypt_trajectory(trajectory, key):
    data = json.dumps(trajectory).encode()
    return Fernet(key).encrypt(data)


def decrypt_trajectory(encrypted_data, key):
    return json.loads(Fernet(key).decrypt(encrypted_data).decode())


PBKDF2_ITERATIONS = 600000  # OWASP 2024 recommendation for PBKDF2-HMAC-SHA256


def derive_fernet_key(secret, salt, iterations=PBKDF2_ITERATIONS):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))


def save_encrypted_fake_trajectory(fake_trajectory, key, file_path):
    token = encrypt_trajectory(fake_trajectory, key)
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token)
    finally:
        os.close(fd)


def load_encrypted_fake_trajectory(file_path, key):
    with open(file_path, "rb") as file_handle:
        encrypted = file_handle.read()
    return decrypt_trajectory(encrypted, key)


def save_encrypted_fake_trajectory_with_secret(fake_trajectory, secret, file_path, *, user_id, key_version):
    salt = secrets.token_bytes(KDF_SALT_SIZE)
    scoped_secret = derive_user_scoped_secret(secret, user_id, key_version)
    key = derive_fernet_key(scoped_secret, salt)
    metadata = {
        "algorithm": "fernet+pbkdf2-sha256",
        "kdf": {"name": "PBKDF2HMAC", "hash": "SHA256", "iterations": 600000, "salt_size": KDF_SALT_SIZE},
        "data_type": "fake_trajectory_points",
        "key_version": key_version,
        "user_scope": "per-user-derived",
        "salt": base64.b64encode(salt).decode("ascii"),
    }
    metadata_bytes = json.dumps(metadata).encode("utf-8")
    token_bytes = encrypt_trajectory(fake_trajectory, key)
    payload = FAKE_DATA_FILE_MAGIC_BYTES + FAKE_DATA_FILE_VERSION.to_bytes(1, "big") + len(metadata_bytes).to_bytes(FAKE_DATA_METADATA_LENGTH_BYTES, "big") + metadata_bytes + token_bytes
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def load_encrypted_fake_trajectory_with_secret(file_path, secret_registry, user_id):
    with open(file_path, "rb") as file_handle:
        payload = file_handle.read()
    magic_length = len(FAKE_DATA_FILE_MAGIC_BYTES)
    if len(payload) < magic_length or payload[:magic_length] != FAKE_DATA_FILE_MAGIC_BYTES:
        raise ValueError("Invalid file format: missing or incorrect magic header")
    version_offset = magic_length
    metadata_length_start = version_offset + 1
    metadata_length_end = metadata_length_start + FAKE_DATA_METADATA_LENGTH_BYTES
    if len(payload) < metadata_length_end:
        raise ValueError("Invalid file format: payload too short to contain metadata length")
    metadata_length = int.from_bytes(payload[metadata_length_start:metadata_length_end], "big")
    if metadata_length == 0 or metadata_length > 65536:
        raise ValueError(f"Invalid metadata length: {metadata_length} (expected 1–65536 bytes)")
    metadata_start = metadata_length_end
    metadata_end = metadata_start + metadata_length
    if len(payload) <= metadata_end:
        raise ValueError("Invalid file format: payload too short to contain metadata and token")
    metadata = json.loads(payload[metadata_start:metadata_end].decode("utf-8"))
    token = payload[metadata_end:]
    key_version = metadata.get("key_version", DEFAULT_KEY_VERSION)
    salt = base64.b64decode(metadata["salt"])
    stored_iterations = metadata.get("kdf", {}).get("iterations", PBKDF2_ITERATIONS)
    scoped_secret = derive_user_scoped_secret(secret_registry[key_version], user_id, key_version)
    key = derive_fernet_key(scoped_secret, salt, iterations=stored_iterations)
    return decrypt_trajectory(token, key)


def normalize_protected_package(protected_package):
    normalized = dict(protected_package)
    normalized["scrambled_anchor_trajectory"] = [[float(point[0]), float(point[1])] for point in protected_package["scrambled_anchor_trajectory"]]
    normalized["scrambled_trajectory"] = [[float(point[0]), float(point[1])] for point in protected_package["scrambled_trajectory"]]
    normalized["recovered_trajectory"] = [[float(point[0]), float(point[1])] for point in protected_package["recovered_trajectory"]]
    normalized["labels"] = list(protected_package["labels"])
    normalized["reference_frame"] = {
        "origin_lat": float(protected_package["reference_frame"]["origin_lat"]),
        "origin_lng": float(protected_package["reference_frame"]["origin_lng"]),
        "lng_scale": float(protected_package["reference_frame"]["lng_scale"]),
    }
    normalized["route_frames"] = {
        "tangents": [[float(vector[0]), float(vector[1])] for vector in protected_package["route_frames"]["tangents"]],
        "normals": [[float(vector[0]), float(vector[1])] for vector in protected_package["route_frames"]["normals"]],
    }
    normalized["scramble_radius_meters"] = float(protected_package["scramble_radius_meters"])
    normalized["flow"] = list(protected_package["flow"])
    normalized["session_seed"] = str(protected_package["session_seed"])
    return normalized


def verify_encrypted_round_trip(file_path, secret_registry, user_id, expected_payload):
    restored = load_encrypted_fake_trajectory_with_secret(file_path, secret_registry, user_id)
    if normalize_protected_package(restored) != normalize_protected_package(expected_payload):
        raise RuntimeError("Self-test failed: decrypted payload does not match the stored protected trajectory package")
    return True


def migrate_encrypted_file(file_path, secret_registry, user_id):
    """Re-encrypt a .bin file to the current PBKDF2_ITERATIONS without changing its content.

    Safe to call multiple times — skips files already at the current iteration count.
    Returns True if migrated, False if already up to date.
    """
    with open(file_path, "rb") as fh:
        payload = fh.read()

    magic_length = len(FAKE_DATA_FILE_MAGIC_BYTES)
    if len(payload) < magic_length or payload[:magic_length] != FAKE_DATA_FILE_MAGIC_BYTES:
        raise ValueError(f"{file_path}: not a recognised encrypted trajectory file")

    metadata_length_start = magic_length + 1
    metadata_length_end = metadata_length_start + FAKE_DATA_METADATA_LENGTH_BYTES
    metadata_length = int.from_bytes(payload[metadata_length_start:metadata_length_end], "big")
    metadata_start = metadata_length_end
    metadata_end = metadata_start + metadata_length
    metadata = json.loads(payload[metadata_start:metadata_end].decode("utf-8"))
    token = payload[metadata_end:]

    stored_iterations = metadata.get("kdf", {}).get("iterations", 0)
    if stored_iterations == PBKDF2_ITERATIONS:
        return False  # already current

    key_version = metadata.get("key_version", DEFAULT_KEY_VERSION)
    salt = base64.b64decode(metadata["salt"])
    scoped_secret = derive_user_scoped_secret(secret_registry[key_version], user_id, key_version)

    old_key = derive_fernet_key(scoped_secret, salt, iterations=stored_iterations)
    content = decrypt_trajectory(token, old_key)

    new_salt = secrets.token_bytes(KDF_SALT_SIZE)
    new_key = derive_fernet_key(scoped_secret, new_salt)
    new_metadata = dict(metadata)
    new_metadata["kdf"] = {"name": "PBKDF2HMAC", "hash": "SHA256", "iterations": PBKDF2_ITERATIONS, "salt_size": KDF_SALT_SIZE}
    new_metadata["salt"] = base64.b64encode(new_salt).decode("ascii")
    new_metadata_bytes = json.dumps(new_metadata).encode("utf-8")
    new_token = encrypt_trajectory(content, new_key)
    new_payload = (
        FAKE_DATA_FILE_MAGIC_BYTES
        + FAKE_DATA_FILE_VERSION.to_bytes(1, "big")
        + len(new_metadata_bytes).to_bytes(FAKE_DATA_METADATA_LENGTH_BYTES, "big")
        + new_metadata_bytes
        + new_token
    )
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, new_payload)
    finally:
        os.close(fd)
    return True


def verify_recovery_matches_original(protected_package, expected_trajectory, tolerance_meters=0.01):
    session_seed = base64.urlsafe_b64decode(protected_package["session_seed"])
    recovered_trajectory = recover_trajectory_from_labels(
        protected_package["scrambled_anchor_trajectory"],
        protected_package["labels"],
        protected_package["reference_frame"],
        protected_package["scramble_radius_meters"],
        session_seed,
        route_frames=protected_package.get("route_frames"),
    )
    tolerance_degrees = tolerance_meters / METERS_PER_DEGREE
    for recovered_point, expected_point in zip(recovered_trajectory, expected_trajectory):
        if distance_in_degrees(recovered_point, expected_point) > tolerance_degrees:
            raise RuntimeError("Recovery failed: label-driven decode did not reconstruct the original trajectory")
    return True
