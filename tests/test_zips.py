"""Anyone's ZIP maps to the nearest watched area on the same island, never across the ocean.

Run: .venv/bin/python -m tests.test_zips   (or pytest, if installed)
"""

from app import zips


def test_watched_zip_is_marked_watched() -> None:
    found = zips.lookup("96720")
    assert found["watched"] is True
    assert found["island"] == "Hawaiʻi Island"


def test_unwatched_big_island_zip_gets_nearest_same_island_area() -> None:
    found = zips.lookup("96778")  # Pāhoa
    assert found["watched"] is False
    assert found["nearest"][0]["zip"] == "96720"
    assert found["nearest"][0]["same_island"] is True


def test_maui_zip_gets_the_watched_maui_area() -> None:
    found = zips.lookup("96793")  # Wailuku
    assert found["island"] == "Maui"
    assert found["nearest"][0]["zip"] == "96732" and found["nearest"][0]["same_island"]


def test_other_island_is_never_same_island() -> None:
    for zip_code, island in (("96815", "Oʻahu"), ("96766", "Kauaʻi")):
        found = zips.lookup(zip_code)
        assert found["island"] == island
        assert not any(row["same_island"] for row in found["nearest"])


def test_every_hawaii_zip_lands_on_an_island() -> None:
    missing = [code for code, (lat, lng) in zips._ZIPS.items() if zips.island_at(lat, lng) is None]
    assert not missing, missing


def test_non_hawaii_zip_is_unknown() -> None:
    assert zips.lookup("90210") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
