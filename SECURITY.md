# Security policy

This is a private, proprietary project that handles student face data.

**Report a vulnerability privately** to the maintainer (do not open a public issue, do not post details in chat groups). Include what you found, how to reproduce it, and which version/commit. Expect an acknowledgement within 3 working days.

In scope: authentication and authorisation, check-in forgery or replay, access to another user's data or photos, data retention failures, injection, and anything that exposes face vectors or reference photos.

Handling rules for anyone with access: never commit `.env` or database dumps; backups stay in an India-region location with mode 600; access to the VM, database and backups equals access to student biometric data, so keep that list short and written down.
