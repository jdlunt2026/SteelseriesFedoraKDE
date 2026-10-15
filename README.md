# SteelPanel

A Linux desktop app for SteelSeries mice and keyboards: DPI presets, polling
rate, button mapping and RGB lighting, with named profiles you can switch from
the system tray.

![Mouse settings](docs/mouse.png)

| Lighting | Power | Keyboard |
|---|---|---|
| ![](docs/mouse-lighting.png) | ![](docs/mouse-power.png) | ![](docs/keyboard.png) |

## How it works

SteelSeries doesn't publish its USB protocols and GG doesn't run on Linux, so
SteelPanel builds on two open-source projects that reverse-engineered them:

| Device | Backend | What you can change |
|---|---|---|
| Mice (Rival, Aerox, Prime, Sensei, Kana, Kinzu; ~70 models) | [rivalcfg](https://github.com/flozz/rivalcfg), direct USB HID | DPI presets, polling rate, LED colors and effects, button mapping, model-specific options, saving to onboard memory |
| Keyboards (Apex) and other RGB gear | [OpenRGB](https://openrgb.org) SDK server | Lighting effect, color, per-zone color, brightness, effect speed |

The GUI is generated from each device's capability description, so any mouse
that rivalcfg supports gets the right controls automatically.

## Tested target: Aerox 9 Wireless + Apex Pro Gen 3 on Fedora KDE

**Aerox 9 Wireless:** works over the USB cable and the 2.4 GHz dongle
(not Bluetooth). Both connections share one set of profile settings, and
when both are present the cable is used.

| Available | Not available on Linux |
|---|---|
| 5 DPI presets (100–18000), polling rate, 3 RGB zones, reactive (click) color, rainbow effect, startup lighting, sleep and dim timers, battery level, save to onboard memory | Button remapping (rivalcfg doesn't support it for this mouse), Bluetooth mode |

**Apex Pro Gen 3:** lighting only, through OpenRGB, which recognises this
keyboard from **version 1.0** (September 2026). Older OpenRGB versions won't
list it. Check with `openrgb --version`. Actuation points, rapid trigger and
the OLED screen are only configurable in SteelSeries GG.

## Install

```bash
git clone https://github.com/jdlunt2026/SteelseriesFedoraKDE.git
cd SteelseriesFedoraKDE
./install.sh
```

This installs into `~/.local/share/steelpanel/venv`, puts a `steelpanel`
command in `~/.local/bin` (already on PATH on Fedora; open a new terminal if
it isn't found), and adds SteelPanel to the app launcher. Run `git pull &&
./install.sh` to update.

On Fedora KDE everything needed is already installed (Python 3 and the Qt
libraries). The app follows your Plasma theme and shows up in the system tray.

### Mice: allow access without root

```bash
sudo ~/.local/share/steelpanel/venv/bin/steelpanel --install-udev
```

Then unplug and replug the mouse.

### Keyboards: run OpenRGB's server

1. Install OpenRGB **1.0 or newer**: `sudo dnf install openrgb`, or a
   newer build from [openrgb.org](https://openrgb.org) if the Fedora package is
   older than 1.0. Builds that don't come from the Fedora package don't install
   udev rules, so also install `60-openrgb.rules` from openrgb.org into
   `/etc/udev/rules.d/`, then run `sudo udevadm control --reload-rules && sudo udevadm trigger`.
2. Start the SDK server: `openrgb --server` (or enable "Start server" in
   OpenRGB's settings and have it autostart).

Use `STEELPANEL_OPENRGB_HOST` / `STEELPANEL_OPENRGB_PORT` if it doesn't run on
`127.0.0.1:6742`.

## Use

```bash
steelpanel                  # open the GUI
steelpanel --list           # print detected devices and their settings
steelpanel --apply Gaming   # apply a profile and exit (scriptable)
steelpanel --hidden         # start in the tray
```

- Changes apply to the hardware immediately and are saved to the active
  profile (`~/.config/steelpanel/profiles.json`).
- **New…** creates a profile as a copy of the current one. Switch profiles
  from the toolbar or the tray icon's menu.
- Most mice forget their settings when unplugged. Either click **Save to
  device memory**, or let SteelPanel re-apply the active profile at login:

  ```bash
  cp packaging/steelpanel-autostart.desktop ~/.config/autostart/
  ```

## Development

```bash
pip install -e '.[test]'
pytest
```

The tests use rivalcfg's dry-run HID device and a fake OpenRGB client, so they
need no hardware. Every mouse profile rivalcfg knows is exercised. To click
through the GUI with a simulated mouse:

```bash
RIVALCFG_DRY=1 RIVALCFG_PROFILE=1038:1824 steelpanel
```

### Adding a backend

Implement `steelpanel.model.Backend` (returning `Device` objects that describe
their settings as `Setting` specs) and register it in
`steelpanel/backends/__init__.py`. The GUI and profiles need no changes.

### Layout

```
steelpanel/
  model.py            Device / Setting / Backend abstractions
  profiles.py         JSON profile store + apply_profile()
  backends/rival.py   mice via rivalcfg
  backends/openrgb.py keyboards via OpenRGB SDK
  gui/                PySide6 window, per-setting editor widgets
  __main__.py         CLI entry point
tests/                pytest suite (no hardware needed)
```
