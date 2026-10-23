"""Local launch entry point, bound to loopback by default."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("mock_paypal.app:app", host="127.0.0.1", port=8099)
