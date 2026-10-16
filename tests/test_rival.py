import pytest
from rivalcfg import devices as rival_devices
from rivalcfg.mouse import get_mouse

from steelpanel import model
from steelpanel.backends.rival import (
    RivalBackend, RivalMouse, format_buttons, parse_buttons, settings_from_profile,
    split_connection, to_hex,
)

ALL_PROFILES = sorted({(p["vendor_id"], p["product_id"]) for p in rival_devices.PROFILES.values()})


@pytest.mark.parametrize("ids", ALL_PROFILES, ids=lambda i: f"{i[0]:04x}:{i[1]:04x}")
def test_every_supported_mouse_maps_and_accepts_defaults(ids):
    mouse = RivalMouse(get_mouse(*ids))
    specs = mouse.settings()
    assert specs, "every mouse should expose at least one setting"
    # Every rivalcfg setting must be represented (no unknown handler skipped).
    assert {s.key for s in specs} - {"software_buttons", "angle_snapping"} == set(mouse._mouse.mouse_profile["settings"])
    for spec in specs:
        if spec.kind == model.ACTION:
            mouse.apply(spec.key, None)
        elif spec.default is not None:
            mouse.apply(spec.key, spec.default)


def test_rival3_values_from_gui_types():
    mouse = RivalMouse(get_mouse(0x1038, 0x1824))
    mouse.apply("sensitivity", [400, 800, 1600, 3200, 6400])
    mouse.apply("polling_rate", 500)
    mouse.apply("logo_color", "#12abef")
    mouse.apply("light_effect", "breath")
    mouse.apply("buttons_mapping", {"Button6": "disabled", "Button4": "PlayPause"})
    with pytest.raises(ValueError):
        mouse.apply("sensitivity", [400] * 6)  # more presets than supported


def test_dpi_spec():
    mouse = RivalMouse(get_mouse(0x1038, 0x1824))
    dpi = next(s for s in mouse.settings() if s.key == "sensitivity")
    assert (dpi.kind, dpi.minimum, dpi.maximum, dpi.max_presets) == (model.DPI, 200, 8500, 5)
    assert dpi.default == [800, 1600]
    assert dpi.group == model.PERFORMANCE


def test_reactive_color_can_be_off():
    mouse = RivalMouse(get_mouse(0x1038, 0x1836))  # Aerox 3
    spec = next(s for s in mouse.settings() if s.key == "reactive_color")
    assert spec.allow_off and spec.default is None
    mouse.apply("reactive_color", None)


def test_color_conversion():
    assert to_hex("red") == "#ff0000"
    assert to_hex("0f0") == "#00ff00"
    assert to_hex((1, 2, 3)) == "#010203"
    assert to_hex("off") is None
    assert to_hex("rgbgradient(duration=1000; colors=0%: #ff0000, 50%: #00ff00)") == "#ff0000"


def test_buttons_roundtrip():
    names = ["Button1", "Button6"]
    parsed = parse_buttons("buttons(button1=button1; button6=dpi; layout=qwerty)", names)
    assert parsed == {"Button1": "button1", "Button6": "dpi"}
    assert format_buttons({"Button1": "default", "Button6": "disabled"}) == "buttons(Button6=disabled)"
    assert format_buttons({"Button1": "default"}) == "default"


def test_discovery_with_simulated_mouse(monkeypatch):
    monkeypatch.setenv("RIVALCFG_PROFILE", "1038:1824")
    found = RivalBackend().discover()
    assert [d.id for d in found.devices] == ["mouse:SteelSeries Rival 3"]
    assert found.devices[0].supports_onboard_save
    found.devices[0].save_onboard()


AEROX9_WIRED, AEROX9_DONGLE = (0x1038, 0x185A), (0x1038, 0x1858)


def test_connection_suffix_is_split():
    assert split_connection("SteelSeries Aerox 9 Wireless (wired mode)") == (
        "SteelSeries Aerox 9 Wireless", "wired")
    assert split_connection("SteelSeries Aerox 9 Wireless (2.4\xa0GHz wireless mode)") == (
        "SteelSeries Aerox 9 Wireless", "2.4 GHz wireless")
    assert split_connection("SteelSeries Rival 3 (firmware v0.37.0.0)")[1] is None


def test_aerox9_same_identity_over_cable_and_dongle():
    wired, dongle = RivalMouse(get_mouse(*AEROX9_WIRED)), RivalMouse(get_mouse(*AEROX9_DONGLE))
    assert wired.id == dongle.id == "mouse:SteelSeries Aerox 9 Wireless"
    assert wired.name == "SteelSeries Aerox 9 Wireless"
    assert dongle.info()["Connection"] == "2.4 GHz wireless"
    assert wired.supports_onboard_save and dongle.supports_onboard_save


def test_aerox9_settings_layout():
    specs = {s.key: s for s in RivalMouse(get_mouse(*AEROX9_DONGLE)).settings()}
    assert specs["sensitivity"].maximum == 18000 and specs["sensitivity"].max_presets == 5
    assert specs["sensitivity"].default == [400, 800, 1200, 2400, 3200]
    assert specs["sleep_timer"].group == specs["dim_timer"].group == model.POWER
    assert "buttons_mapping" not in specs  # rivalcfg has no remapping for the Aerox 9


def test_aerox9_listed_once_preferring_cable(monkeypatch):
    plugged = [
        {"vendor_id": v, "product_id": p, "name": "SteelSeries Aerox 9 Wireless"}
        for v, p in (AEROX9_DONGLE, AEROX9_WIRED)
    ]
    monkeypatch.setattr(rival_devices, "list_plugged_devices", lambda: iter(plugged))
    found = RivalBackend().discover()
    assert len(found.devices) == 1
    assert found.devices[0].connection == "wired"


def test_timer_units():
    specs = {s.key: s for s in RivalMouse(get_mouse(*AEROX9_WIRED)).settings()}
    assert (specs["sleep_timer"].unit, specs["sleep_timer"].minimum_label) == ("min", "Never")
    assert specs["dim_timer"].unit == "s"


def test_aerox9_offers_software_remapping():
    mouse = RivalMouse(get_mouse(*AEROX9_WIRED))
    spec = next(s for s in mouse.settings() if s.key == "software_buttons")
    assert spec.kind == model.KEYMAP and spec.group == model.MAPPING
    assert set(mouse._remapper.products) == {0x185A, 0x1858}  # cable + dongle
    # Mice with onboard mapping don't get it.
    assert "software_buttons" not in {s.key for s in RivalMouse(get_mouse(0x1038, 0x1824)).settings()}


def test_every_mouse_offers_software_angle_snapping():
    for ids in (AEROX9_WIRED, (0x1038, 0x1824)):
        mouse = RivalMouse(get_mouse(*ids))
        spec = next(s for s in mouse.settings() if s.key == "angle_snapping")
        assert spec.kind == model.RANGE and spec.group == model.PERFORMANCE
        assert spec.default == 0 and spec.minimum_label == "Off"
    calls = []
    mouse._remapper.set_angle_snapping = calls.append
    mouse.apply("angle_snapping", 10)
    mouse.apply("angle_snapping", 0)
    assert calls == [10, 0]
