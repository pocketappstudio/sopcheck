"""Minimal stand-in for https://api.gumroad.com/v2/licenses/verify (form POST: product_id, license_key)."""
import sys
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()
KEYS = {  # license_key -> (product_id, purchase overrides)
    "STARTER-KEY-1234": ("prodS", {}), "TEAM-KEY-9999": ("prodT", {}),
    "REFUNDED-KEY-1": ("prodS", {"refunded": True}),
    "CANCELLED-KEY-1": ("prodT", {"subscription_cancelled_at": "2026-10-01T00:00:00Z"}),
}


@app.post("/v2/licenses/verify")
def verify(product_id: str = Form(...), license_key: str = Form(...), increment_uses_count: str = Form("true")):
    k = KEYS.get(license_key)
    if not k or k[0] != product_id:
        return JSONResponse({"success": False, "message": "That license does not exist for the provided product."}, status_code=404)
    pur = {"product_id": product_id, "refunded": False, "chargebacked": False, "disputed": False,
           "subscription_ended_at": None, "subscription_cancelled_at": None, "subscription_failed_at": None, **k[1]}
    return {"success": True, "uses": 1, "purchase": pur}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8098)
