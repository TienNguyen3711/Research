import base64
import gc
import hashlib
import hmac
import json
import math
import os
import secrets

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:
    Fernet = None
    HKDF = None
    PBKDF2HMAC = None
    hashes = None

try:
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

try:
    import folium
except ImportError:
    folium = None

try:
    import googlemaps
except ImportError:
    googlemaps = None

try:
    import requests
except ImportError:
    requests = None


def load_local_env_file(env_path=".env"):
    """Load simple KEY=VALUE pairs from a local .env file into os.environ."""
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in os.environ:
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ[key] = value


load_local_env_file()

KDF_SALT_SIZE = 16
FAKE_DATA_FILE_MAGIC = "FAKE_TRAJECTORY_SECURE"
FAKE_DATA_FILE_VERSION = 1
FAKE_DATA_FILE_MAGIC_BYTES = FAKE_DATA_FILE_MAGIC.encode("ascii")
FAKE_DATA_VERSION_BYTES = FAKE_DATA_FILE_VERSION.to_bytes(1, "big")
FAKE_DATA_METADATA_LENGTH_BYTES = 4
DEFAULT_STRICT_FAKE_ONLY = os.getenv("STRICT_FAKE_ONLY", "1") != "0"
DEFAULT_ENABLE_VISUAL_OUTPUTS = os.getenv("ENABLE_VISUAL_OUTPUTS", "0") == "1"
DEFAULT_HEADLESS_LOCAL_TEST = os.getenv("HEADLESS_LOCAL_TEST", "0") == "1"
DEFAULT_RUN_SELF_TEST = os.getenv("RUN_SELF_TEST", "0") == "1"
DEFAULT_KEY_VERSION = 1
DEFAULT_LOCAL_WINDOW_SIZE = 4
DEFAULT_LOCAL_DIVERSITY_METERS = 120
DEFAULT_QUANTIZATION_GRID_METERS = 12
DEFAULT_MIN_PRIVACY_DISTANCE_METERS = int(os.getenv("MIN_PRIVACY_DISTANCE_METERS", "300"))
DEFAULT_ENDPOINT_OFFSET_METERS = int(os.getenv("ENDPOINT_OFFSET_METERS", "3000"))
DEFAULT_MAX_POINT_OFFSET_METERS = int(os.getenv("MAX_POINT_OFFSET_METERS", "3000"))
OFFSET_SAFETY_MARGIN = float(os.getenv("OFFSET_SAFETY_MARGIN", "0.96"))
DEFAULT_MIDDLE_MIN_OFFSET_METERS = int(os.getenv("MIDDLE_MIN_OFFSET_METERS", "2500"))
DEFAULT_MIDDLE_MAX_OFFSET_METERS = int(os.getenv("MIDDLE_MAX_OFFSET_METERS", "3000"))
MIDDLE_MIN_OFFSET_SAFETY = float(os.getenv("MIDDLE_MIN_OFFSET_SAFETY", "0.99"))
MIDDLE_MAX_OFFSET_SAFETY = float(os.getenv("MIDDLE_MAX_OFFSET_SAFETY", "0.79"))
METERS_PER_DEGREE = 111_000

dynamic_factors = {
    "wind_speed": 23.4,
    "humidity": 67.5,
    "tide_level": 52.1,
    "rain_probability": 35.0,
    "cloud_cover": 20.0,
    "storm_index": 50.0,
    "star_index": 10.0,
}


def secure_clear_dict(d):
    """Best-effort cleanup for dictionary contents held in Python memory."""
    if isinstance(d, dict):
        for key in list(d.keys()):
            d[key] = None
        d.clear()


def secure_clear_list(lst):
    """Best-effort cleanup for list contents held in Python memory."""
    if isinstance(lst, list):
        for index in range(len(lst)):
            lst[index] = None
        lst.clear()
