import json

import pytest

from steelpanel.profiles import ProfileStore, apply_profile

from fake_openrgb import FakeDevice
from steelpanel.backends.openrgb import OpenRGBDevice


def test_create_rename_delete_persist(tmp_path):
    path = tmp_path / "p.json"
    s = ProfileStore(path)
    s.set_value("Default", "mouse:1", "polling_rate", 500)
    s.create("Gaming", copy_from="Default")
    s.set_value("Gaming", "mouse:1", "polling_rate", 1000)
    s.active = "Gaming"
    s.rename("Gaming", "FPS")
    assert s.active == "FPS"
    s.save()

    s2 = ProfileStore(path)
    assert s2.names() == ["Default", "FPS"] and s2.active == "FPS"
    assert s2.device_values("Default", "mouse:1") == {"polling_rate": 500}
    assert s2.device_values("FPS", "mouse:1") == {"polling_rate": 1000}
    s2.delete("FPS")
    assert s2.active == "Default"
    with pytest.raises(ValueError):
        s2.delete("Default")
    with pytest.raises(ValueError):
        s2.create("Default")


def test_corrupt_or_empty_file_falls_back(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"profiles": {}}))
    assert ProfileStore(path).names() == ["Default"]


def test_apply_profile_orders_by_device_settings_and_reports_errors(tmp_path):
    fake = FakeDevice()
    dev = OpenRGBDevice(fake)
    s = ProfileStore(tmp_path / "p.json")
    # Saved in "wrong" order: zone color needs Direct mode applied first.
    s.set_value("Default", dev.id, "zone0", "#ffffff")
    s.set_value("Default", dev.id, "mode", "Direct")
    s.set_value("Default", dev.id, "speed", 30)  # Direct has no speed -> error
    errors = apply_profile(s, "Default", [dev])
    assert fake.zones[0].calls, "zone color applied after switching to Direct"
    assert len(errors) == 1 and "speed" in errors[0].lower()
