"""Human-operated PayPal sandbox checks, separate from the application agent.

Shared harness interfaces:
- env.load_settings() loads credentials at runtime; never print its fields.
- redact.Redactor.from_settings(settings) prepares role aliases and secret masking.
- recorder.Recorder(check_id, redactor=...) saves redacted exchanges.
- client.SandboxClient(settings, recorder) is an async context manager.
  await client.authenticate() returns safe scope/expiry metadata.
  await client.request(method, path, step=..., ...) returns an httpx.Response.
  Use recorder.redactor.redact(value) before displaying response content.
"""
