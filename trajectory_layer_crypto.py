from trajectory_layer_bootstrap import (
    DEFAULT_HEADLESS_LOCAL_TEST,
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


def xor_stream_encrypt(data, key, nonce):
    key_material = base64.urlsafe_b64decode(key)
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream.extend(hmac.new(key_material, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(a ^ b for a, b in zip(data, stream[:len(data)]))


def encrypt_trajectory(trajectory, key):
    data = json.dumps(trajectory).encode()
    if Fernet is not None:
        return Fernet(key).encrypt(data)
    if not DEFAULT_HEADLESS_LOCAL_TEST:
        raise RuntimeError("cryptography is required for production encryption")
    nonce = secrets.token_bytes(16)
    ciphertext = xor_stream_encrypt(data, key, nonce)
    tag = hmac.new(base64.urlsafe_b64decode(key), nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ciphertext + tag)


def decrypt_trajectory(encrypted_data, key):
    if Fernet is not None:
        return json.loads(Fernet(key).decrypt(encrypted_data).decode())
    if not DEFAULT_HEADLESS_LOCAL_TEST:
        raise RuntimeError("cryptography is required for production decryption")
    payload = base64.urlsafe_b64decode(encrypted_data)
    nonce = payload[:16]
    ciphertext = payload[16:-32]
    tag = payload[-32:]
    expected_tag = hmac.new(base64.urlsafe_b64decode(key), nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Encrypted payload integrity check failed")
    return json.loads(xor_stream_encrypt(ciphertext, key, nonce).decode())


def derive_fernet_key(secret, salt):
    if PBKDF2HMAC is not None and hashes is not None:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
        return base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    if not DEFAULT_HEADLESS_LOCAL_TEST:
        raise RuntimeError("cryptography is required for production key derivation")
    return base64.urlsafe_b64encode(hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, 390000, dklen=32))


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
        "kdf": {"name": "PBKDF2HMAC", "hash": "SHA256", "iterations": 390000, "salt_size": KDF_SALT_SIZE},
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
    version_offset = magic_length
    metadata_length_start = version_offset + 1
    metadata_length_end = metadata_length_start + FAKE_DATA_METADATA_LENGTH_BYTES
    metadata_length = int.from_bytes(payload[metadata_length_start:metadata_length_end], "big")
    metadata_start = metadata_length_end
    metadata_end = metadata_start + metadata_length
    metadata = json.loads(payload[metadata_start:metadata_end].decode("utf-8"))
    token = payload[metadata_end:]
    key_version = metadata.get("key_version", DEFAULT_KEY_VERSION)
    salt = base64.b64decode(metadata["salt"])
    scoped_secret = derive_user_scoped_secret(secret_registry[key_version], user_id, key_version)
    key = derive_fernet_key(scoped_secret, salt)
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
    return normalized


def verify_encrypted_round_trip(file_path, secret_registry, user_id, expected_payload):
    restored = load_encrypted_fake_trajectory_with_secret(file_path, secret_registry, user_id)
    if normalize_protected_package(restored) != normalize_protected_package(expected_payload):
        raise RuntimeError("Self-test failed: decrypted payload does not match the stored protected trajectory package")
    return True


def verify_recovery_matches_original(protected_package, expected_trajectory, tolerance_meters=0.01):
    recovered_trajectory = recover_trajectory_from_labels(
        protected_package["scrambled_anchor_trajectory"],
        protected_package["labels"],
        protected_package["reference_frame"],
        protected_package["scramble_radius_meters"],
        route_frames=protected_package.get("route_frames"),
    )
    tolerance_degrees = tolerance_meters / METERS_PER_DEGREE
    for recovered_point, expected_point in zip(recovered_trajectory, expected_trajectory):
        if distance_in_degrees(recovered_point, expected_point) > tolerance_degrees:
            raise RuntimeError("Recovery failed: label-driven decode did not reconstruct the original trajectory")
    return True
