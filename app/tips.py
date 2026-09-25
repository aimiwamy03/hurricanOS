"""Language-free safety pictograms, each paired with one fixed sentence of standard
public guidance (NWS, Ready.gov, CDC). FLUX draws the picture; code writes the words.
The pictures back up official instructions; they never replace them.
"""

from __future__ import annotations

from app.config import DATA_DIR

IMAGE_DIR = DATA_DIR / "images"

# In priority order: the card shows the first few for a phase.
TIPS = [
    {
        "key": "charge_phones",
        "text": "Charge phones and power banks now, while you still have power.",
        "drawing": "smartphone plugged into a charging cable with a full battery symbol",
        "phases": ("BEFORE",),
    },
    {
        "key": "store_water",
        "text": "Fill clean containers with drinking water.",
        "drawing": "large water jug being filled from a faucet",
        "phases": ("BEFORE",),
    },
    {
        "key": "stay_indoors",
        "text": "Stay indoors and away from windows.",
        "drawing": "simple house with a person safely inside, rain and wind outside",
        "phases": ("DURING",),
    },
    {
        "key": "go_to_shelter",
        "text": "If officials tell you to evacuate, go to an open shelter.",
        "drawing": "family walking toward a sturdy shelter building with an arrow",
        "phases": ("DURING",),
    },
    {
        "key": "no_flood_driving",
        "text": "Turn around, don't drown: never drive or walk through flood water.",
        "drawing": "car stopped in front of a flooded road with waves, red diagonal prohibition line over the water",
        "phases": ("DURING", "AFTER"),
    },
    {
        "key": "generator_outside",
        "text": "Run generators outside only, far from doors and windows. Fumes can kill.",
        "drawing": "portable generator outdoors far from a house, with an arrow showing distance",
        "phases": ("DURING", "AFTER"),
    },
    {
        "key": "flashlight_not_candles",
        "text": "Use flashlights, not candles, when the power goes out.",
        # Two FLUX tries were misleading (the flashlight crossed out, then a megaphone look-alike),
        # so this tip reuses the essentials flashlight icon: scripts/gen_pictograms copies it.
        "drawing": "flashlight",
        "reuse": "flashlight",
        "phases": ("BEFORE", "DURING"),
    },
]


def image_path(key: str):
    return IMAGE_DIR / f"tip_{key}.png"


def tips_for(phase: str) -> list[dict]:
    """Tips for one phase, with the image URL when the pictogram exists."""
    out = []
    for tip in TIPS:
        if phase not in tip["phases"]:
            continue
        path = image_path(tip["key"])
        out.append(
            {"key": tip["key"], "text": tip["text"], "image": f"/images/{path.name}" if path.exists() else None}
        )
    return out
