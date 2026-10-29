import os

SECRET_KEY = os.environ.get("GK_SECRET_KEY", "dev-only-secret-key-change-me-before-any-real-use")
DATABASE_URL = os.environ.get("GK_DATABASE_URL", "sqlite:///./gatekeeper.db")
if DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("://", "+psycopg://", 1)  # use the psycopg 3 driver we ship
ACCESS_TTL_S = int(os.environ.get("GK_ACCESS_TTL_S", "3600"))
REFRESH_TTL_S = int(os.environ.get("GK_REFRESH_TTL_S", str(14 * 24 * 3600)))
TRIAL_MODE = os.environ.get("GK_TRIAL_MODE", "0") == "1"
ENV = os.environ.get("GK_ENV", "dev")
RATE_LIMIT = os.environ.get("GK_RATE_LIMIT", "on") != "off"
CORS_ORIGINS = [o for o in os.environ.get("GK_CORS_ORIGINS", "").split(",") if o]  # empty = same-origin only (the production setup behind one proxy)
LOG_JSON = os.environ.get("GK_LOG_JSON", "1" if ENV == "prod" else "0") == "1"
RETAIN_CHECKIN_VECTOR_DAYS = int(os.environ.get("GK_RETAIN_VECTOR_DAYS", "30"))   # session-scoped face vectors on check-ins
RETAIN_TELEMETRY_DAYS = int(os.environ.get("GK_RETAIN_TELEMETRY_DAYS", "90"))     # audio logs, used challenges

if ENV == "prod" and SECRET_KEY.startswith("dev-only"):
    raise RuntimeError("GK_SECRET_KEY must be set in production")

PHOTO_KEY = os.environ.get("GK_PHOTO_KEY")  # urlsafe-base64 32 bytes; dev falls back to a key derived from SECRET_KEY
if os.environ.get("GK_ENV") == "prod" and not PHOTO_KEY:
    raise RuntimeError("GK_PHOTO_KEY must be set in production")
MAX_PHOTO_BYTES = 500_000
MAX_CSV_BYTES = 1_000_000
MAX_CSV_ROWS = 1000
PHOTO_URL_TTL_S = 300

GRACE_WINDOWS = 1            # accept the current and previous QR window
TOKEN_AHEAD_MS = 60_000      # professor client may prefetch this far ahead
TOKEN_MAX_BATCH = 100

REDIS_URL = os.environ.get("GK_REDIS_URL")  # unset in dev/tests -> in-process nonce store (single process only)
if os.environ.get("GK_ENV") == "prod" and not REDIS_URL:
    raise RuntimeError("GK_REDIS_URL must be set in production")
NONCE_TTL_S = 5
CHALLENGE_TTL_MS = 60_000    # covers the colour-flash step plus scanning the QR (face first, QR last, so the QR stays fresh)

# Face layer and trust scorer (phase 5). One value each; tuned from field data, not before.
FACE_MODE = os.environ.get("GK_FACE_MODE", "required")  # "off" restores the phase-4 QR-only behaviour (regression runs)
FACE_MODEL = "faceapi-128-v1"          # the one embedding model the apps ship; stub-v0 is rejected at enrollment
REJECTED_MODELS = {"stub-v0"}
FACE_DIST_MAX = 0.55                   # euclidean distance between 128-d descriptors; above it = FACE_MISMATCH
COLLISION_DIST = 0.45                  # two students in one session closer than this = FACE_COLLISION (both go to REVIEW)
LIVENESS_MIN_COS = 0.3                 # a colour step passes if the face chroma shift points at the screen colour this much
LIVENESS_MIN_PASS = 3                  # of 4 steps
W_QR, W_FACE, W_LIVENESS, W_AUDIO = 0.5, 0.3, 0.2, 0.0   # audio weight is switched on in phase 6
VERIFIED_MIN = 0.8

# Audio layer (phase 6). A soft signal: heard = no change, not heard = small penalty + AUDIO_UNAVAILABLE, wrong-but-valid code = REVIEW.
AUDIO_SAMPLE_RATE = 48000
AUDIO_BAND_HZ = (17500, 19000)
AUDIO_MAX_AGE_MS = 8000           # a heard burst may be at most this old when the check-in is verified
AUDIO_MISSING_PENALTY = 0.1

# Professor live console (phase 7)
ATTENTION_GRACE_S = 180      # not checked in this long after the start -> appears on the Attention Panel
AUDIT_SAMPLE = 2             # random VERIFIED students offered for a spot check (measures real false accepts)

if ENV == "prod":
    if DATABASE_URL.startswith("sqlite"):
        raise RuntimeError("GK_DATABASE_URL must point at Postgres in production")
    if FACE_MODE != "required":
        raise RuntimeError("GK_FACE_MODE must be 'required' in production")
    if not RATE_LIMIT:
        raise RuntimeError("GK_RATE_LIMIT=off is not allowed in production")
