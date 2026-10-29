# QR token spec (gk1)
payload  = `gk1:{session_id}:{window}:{tok}`
window   = floor(server_epoch_ms / step_ms)
tok      = first 16 chars of base32(HMAC_SHA256(session_secret, "{session_id}|{window}")) without padding
accept   = window in [now_window - grace_windows, now_window]; anything else with a valid tok is `expired` (HTTP 410); a bad tok is `invalid` (HTTP 403)
session_secret is per session, random 32 bytes, server-side only. Clients never hold it.
Test vectors: shared/token_vectors.json (secret is for vectors only).
