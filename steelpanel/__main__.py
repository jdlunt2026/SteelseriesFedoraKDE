"""Entry point.

    steelpanel                      open the GUI
    steelpanel --list               list detected devices and their settings
    steelpanel --apply [PROFILE]    apply a profile headlessly (e.g. at login)
    steelpanel --oled-test          draw a test pattern on an Apex keyboard's screen
    sudo steelpanel --install-udev  allow non-root access to SteelSeries mice and
                                    keyboard screens
"""

from __future__ import annotations

import argparse
import sys


def _describe(spec) -> str:
    """Options and current/default value, for --list."""
    from . import model

    unit = f" {spec.unit}" if spec.unit else ""
    if spec.kind == model.CHOICE:
        opts = ", ".join(str(c) for c in spec.choices)
        return f"\n      options: {opts}\n      current/default: {spec.default}"
    if spec.kind in (model.RANGE, model.DPI):
        extra = f", up to {spec.max_presets} presets" if spec.kind == model.DPI else ""
        return (f"\n      range: {spec.minimum}-{spec.maximum}{unit} step {spec.step}{extra}"
                f"\n      current/default: {spec.default}")
    if spec.kind == model.COLOR and spec.default:
        return f"\n      current/default: {spec.default}"
    return ""


def _cmd_list() -> int:
    from .backends import default_backends, discover_all

    found = discover_all(default_backends())
    for problem in found.problems:
        print(f"! {problem}", file=sys.stderr)
    if not found.devices:
        print("No supported SteelSeries device found.")
    for dev in found.devices:
        print(f"{dev.name}  [{dev.id}]")
        for k, v in dev.info().items():
            print(f"    {k}: {v}")
        for s in dev.settings():
            print(f"  - {s.key} ({s.kind}, {s.group}): {s.label}{_describe(s)}")
    return 0


def _cmd_apply(name: str | None) -> int:
    from .backends import default_backends, discover_all
    from .profiles import ProfileStore, apply_profile

    store = ProfileStore()
    name = name or store.active
    if name not in store.names():
        print(f"Unknown profile '{name}'. Available: {', '.join(store.names())}", file=sys.stderr)
        return 2
    found = discover_all(default_backends())
    for problem in found.problems:
        print(f"! {problem}", file=sys.stderr)
    errors = apply_profile(store, name, found.devices)
    values = store.get(name).values()
    if any(v.get("software_buttons") or v.get("angle_snapping") for v in values):
        print("Note: button remapping and angle snapping are only active while the SteelPanel app runs.")
    if any(v.get("mode", "Keyboard default") != "Keyboard default"
           for dev_id, v in store.get(name).items() if dev_id.startswith("screen:")):
        print("Note: the keyboard screen only shows SteelPanel content while the app runs.")
    for e in errors:
        print(f"! {e}", file=sys.stderr)
    print(f"Applied '{name}' to {len(found.devices)} device(s).")
    return 1 if errors else 0


def _cmd_install_udev() -> int:
    from rivalcfg import udev

    from . import udev as steelpanel_udev

    try:
        udev.write_rules_file()
        steelpanel_udev.write_rules()
        udev.reload_rules()
        udev.trigger()
    except PermissionError:
        print("Permission denied: run this command with sudo.", file=sys.stderr)
        return 1
    print(f"Wrote {udev.RULES_FILE_PATH} and {steelpanel_udev.RULES_PATH}.")
    print("Unplug and replug your mouse (or its dongle) and keyboard.")
    return 0


def _cmd_oled_test(seconds: float = 10.0) -> int:
    import time

    from PySide6.QtGui import QGuiApplication

    from . import oled

    app = QGuiApplication.instance() or QGuiApplication(["steelpanel"])  # fonts  # noqa: F841
    screens = oled.find_screens()
    if not screens:
        print("No Apex keyboard with a screen found.", file=sys.stderr)
        return 1
    status = 0
    for product_id, path in screens:
        screen = oled.HidScreen(product_id, path)
        label = f"{screen.model.name} ({oled.VENDOR:04x}:{product_id:04x})"
        try:
            screen.show(oled.render_test_pattern())
        except OSError as exc:
            print(f"{label}: can't write to the screen: {exc}\n"
                  "Run 'sudo steelpanel --install-udev', then replug the keyboard.", file=sys.stderr)
            status = 1
            continue
        print(f"{label}: sent a test pattern: a frame around the edge, a filled square "
              "in the top-left corner and the text \"SteelPanel / screen test\".")
        try:
            time.sleep(seconds)
            screen.show(oled.BLANK)
        finally:
            screen.close()
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steelpanel", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="list devices and settings")
    group.add_argument("--apply", nargs="?", const="", metavar="PROFILE",
                       help="apply a profile (default: the active one) and exit")
    group.add_argument("--install-udev", action="store_true",
                       help="install udev rules for mice and keyboard screens (needs sudo)")
    group.add_argument("--oled-test", action="store_true",
                       help="show a test pattern on the keyboard screen for 10 seconds")
    parser.add_argument("--hidden", action="store_true", help="start the GUI minimized to tray")
    args = parser.parse_args(argv)

    if args.list:
        return _cmd_list()
    if args.apply is not None:
        return _cmd_apply(args.apply or None)
    if args.install_udev:
        return _cmd_install_udev()
    if args.oled_test:
        return _cmd_oled_test()

    from .gui.app import run
    return run(start_hidden=args.hidden)


if __name__ == "__main__":
    sys.exit(main())
