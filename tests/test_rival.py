import pytest
from rivalcfg import devices as rival_devices
from rivalcfg.mouse import get_mouse

from steelpanel import model
from steelpanel.backends.rival import (
    RivalBackend, RivalMouse, format_buttons, parse_buttons, settings_from_profile, to_hex,
)

ALL_PROFILES = sorted({(p["vendor_id"], p["product_id"]) for p in rival_devices.PROFILES.values()})


@pytest.mark.parametrize("ids", ALL_PROFILES, ids=lambda i: f"{i[0]:04x}:{i[1]:04x}")
def test_every_supported_mouse_maps_and_accepts_defaults(ids):
    mouse = RivalMouse(get_mouse(*ids))
    specs = mouse.settings()
    assert specs, "every mouse should expose at least one setting"
    # Every rivalcfg setting must be represented (no unknown handler skipped).
    assert {s.key for s in specs} == set(mouse._mouse.mouse_profile["settings"])
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
    assert [d.id for d in found.devices] == ["mouse:1038:1824"]
    assert found.devices[0].supports_onboard_save
    found.devices[0].save_onboard()
