import pytest
from openrgb.utils import DeviceType, RGBColor

from steelpanel.backends.openrgb import OpenRGBBackend, OpenRGBDevice

from fake_openrgb import FakeClient, FakeDevice


def backend_with(*devs):
    client = FakeClient(*devs)
    return OpenRGBBackend(client_factory=lambda *a: client), client


def test_only_steelseries_non_mouse_devices_are_listed():
    b, _ = backend_with(
        FakeDevice(),
        FakeDevice("SteelSeries Rival 3", DeviceType.MOUSE),
        FakeDevice("Corsair K70", vendor="Corsair"),
    )
    found = b.discover()
    assert [d.name for d in found.devices] == ["SteelSeries Apex Pro TKL"]
    assert found.devices[0].kind == "keyboard"


def test_unreachable_server_is_a_problem_not_a_crash():
    def refuse(*a):
        raise ConnectionRefusedError(111, "Connection refused")
    found = OpenRGBBackend(client_factory=refuse).discover()
    assert found.devices == [] and "openrgb --server" in found.problems[0]


def test_settings_reflect_device_state():
    dev = OpenRGBDevice(FakeDevice())
    specs = {s.key: s for s in dev.settings()}
    assert specs["mode"].choices == ["Direct", "Static", "Rainbow Wave"]
    assert specs["mode"].default == "Static"
    assert specs["brightness"].default == 100
    assert specs["color"].default == "#0000ff"
    assert {"zone0", "zone1"} <= specs.keys()


def test_apply_mode_brightness_speed_and_colors():
    fake = FakeDevice()
    dev = OpenRGBDevice(fake)
    dev.apply("brightness", 50)
    assert fake.modes[1].brightness == 128
    dev.apply("color", "#00ff00")
    assert fake.calls[-1] == ("colors", [RGBColor(0, 255, 0)])
    with pytest.raises(ValueError):
        dev.apply("speed", 10)  # Static has no speed
    with pytest.raises(ValueError):
        dev.apply("zone1", "#ffffff")  # needs a per-LED mode

    dev.apply("mode", "Rainbow Wave")
    dev.apply("speed", 100)
    assert fake.modes[2].speed == 0  # inverted native range
    with pytest.raises(ValueError):
        dev.apply("color", "#ffffff")

    dev.apply("mode", "Direct")
    dev.apply("zone1", "#ffffff")
    assert fake.zones[1].calls == [RGBColor(255, 255, 255)]
    dev.apply("color", "#010203")
    assert fake.calls[-1] == ("color", RGBColor(1, 2, 3))


def test_rediscovery_reuses_client():
    b, client = backend_with(FakeDevice())
    b.discover()
    b.discover()
    assert client.updates == 1


def test_hint_when_server_has_no_steelseries_keyboard():
    b, _ = backend_with(FakeDevice("Corsair K70", vendor="Corsair"))
    found = b.discover()
    assert found.devices == [] and "OpenRGB 1.0" in found.problems[0]
