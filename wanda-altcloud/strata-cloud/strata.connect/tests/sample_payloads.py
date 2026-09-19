"""The two worked SmartMeter example payloads, verbatim.

Timestamps are naive by design — that is how the provider sends them.
"""

WEIGHT_PAYLOAD = {
    "reading_id": 12346,
    "device_id": "SM5000-IB-xxxx",
    "device_model": "SM5000-IB",
    "reading_type": "weight",
    "date_recorded": "2026-07-01T08:15:00",
    "date_received": "2026-07-01T08:16:05",
    "tare_kg": 0.0,
    "weight_kg": 81.6,
    "tare_lbs": 0.0,
    "weight_lbs": 180.0,
}

BP_PAYLOAD = {
    "reading_id": 12345,
    "device_id": "SM5000-IB-xxxx",
    "device_model": "SM5000-IB",
    "reading_type": "blood_pressure",
    "date_recorded": "2026-07-01T14:32:00",
    "date_received": "2026-07-01T14:33:10",
    "systolic_mmhg": 128,
    "diastolic_mmhg": 82,
    "pulse_bpm": 71,
    "irregular": False,
}
