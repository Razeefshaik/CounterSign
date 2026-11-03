# C51 browser evidence

Captured November 3, 2026 on the actual FastAPI app and PostgreSQL 16 with the
local PayPal mock. Chromium's CDP virtual CTAP2 internal authenticator had user
verification and automatic user presence enabled. Registration and approval
completed through the UI and the real py_webauthn verifier. The signed timeline
was read back from the database-backed proposal API. No PayPal write or model
call was made. These are browser/device-simulation checks, not physical-device tests.

| State | Light desktop | Dark desktop | Phone, 390 px |
| --- | --- | --- | --- |
| Decision before signing | [Light](prompt-light-1440.png) | [Dark](prompt-dark-1440.png) | [Phone](prompt-light-390.png) |
| Recorded countersignature | [Light](signed-light-1440.png) | [Dark](signed-dark-1440.png) | [Phone](signed-light-390.png) |

Both states kept the simulation banner visible. Checks found no page-level
horizontal overflow and no JavaScript exceptions. The prompt screenshots show
the app's decision state; a virtual authenticator does not show a physical
Face ID/Touch ID/Windows Hello dialog.
