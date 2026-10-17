# SteelPanel

A Linux desktop app for SteelSeries mice and keyboards: DPI presets, polling
rate, button mapping and RGB lighting, with named profiles you can switch from
the system tray.

![Mouse settings](docs/mouse.png)

| Lighting | Buttons | Power | Keyboard |
|---|---|---|---|
| ![](docs/mouse-lighting.png) | ![](docs/mouse-buttons.png) | ![](docs/mouse-power.png) | ![](docs/keyboard.png) |

## How it works

SteelSeries doesn't publish its USB protocols and GG doesn't run on Linux, so
SteelPanel builds on two open-source projects that reverse-engineered them:

| Device | Backend | What you can change |
|---|---|---|
| Mice (Rival, Aerox, Prime, Sensei, Kana, Kinzu; ~70 models) | [rivalcfg](https://github.com/flozz/rivalcfg), direct USB HID | DPI presets, polling rate, LED colors and effects, button mapping, model-specific options, saving to onboard memory, angle snapping (software) |
| Keyboards (Apex) and other RGB gear | [OpenRGB](https://openrgb.org) SDK server | Lighting effect, color, per-zone color, brightness, effect speed |
| Apex keyboard OLED screens | direct USB HID (protocol from [apex-tux](https://github.com/not-jan/apex-tux)) | Clock, text, image or system monitor on the screen |

The GUI is generated from each device's capability description, so any mouse
that rivalcfg supports gets the right controls automatically.

## Tested target: Aerox 9 Wireless + Apex Pro Gen 3 on Fedora KDE

**Aerox 9 Wireless:** works over the USB cable and the 2.4 GHz dongle
(not Bluetooth). Both connections share one set of profile settings, and
when both are present the cable is used.

| Available | Not available on Linux |
|---|---|
| 5 DPI presets (100–18000), polling rate, 3 RGB zones, reactive (click) color, rainbow effect, startup lighting, sleep and dim timers, battery level, save to onboard memory, button remapping (software), angle snapping (software) | Onboard button remapping, Bluetooth mode |

### Button remapping (software)

rivalcfg can't program the Aerox 9's onboard button mapping, so SteelPanel
remaps buttons itself: **Buttons tab → Add mapping…**, press the button on
the mouse, then pick what it should send (any key or mouse button; type to
search). Mappings are saved in your profile.

- It works while SteelPanel runs (closing the window keeps it in the tray;
  use the autostart entry below to start it at login). Quit SteelPanel and
  the mouse is back to normal.
- It works below the desktop, so on Wayland and X11 alike. Remapped buttons
  come from a virtual device named "SteelPanel virtual …".
- Only the mouse's input interface carrying a remapped button is taken over.
  The side buttons are on their own interface, so remapping them doesn't
  route pointer movement through SteelPanel. Remapping back/forward or
  middle click does.
- The left button can't be remapped.
- It needs `sudo dnf install python3-evdev` and the udev rules from
  `sudo steelpanel --install-udev` (run it again if you installed them
  before this feature; then replug the dongle). Those rules let your desktop
  session read this mouse's input and create virtual input devices.

### Angle snapping (software)

SteelSeries GG does angle snapping on the PC, not in the mouse (it stops
working when GG is closed), so SteelPanel does the same: **Performance tab →
Angle snapping**, set to how many degrees off horizontal or vertical still
count as a straight line. Around 10° is a good start; Off disables it. It's
offered for every supported mouse and saved in your profile.

- It works while SteelPanel runs, like button remapping, and the same way:
  the mouse's pointer device is taken over and its movement is re-sent from
  "SteelPanel virtual …", on Wayland and X11 alike.
- Movement close to an axis is straightened; steeper diagonals pass
  unchanged. The direction is judged over the last few millimetres of
  movement, so small hand wobble doesn't break a line.
- Same requirements as button remapping: `python3-evdev` and the udev
  rules from `sudo steelpanel --install-udev` (run it again if you
  installed them before this feature; the rules now cover every mouse).

**Apex Pro Gen 3:** lighting through OpenRGB, which recognises this
keyboard from **version 1.0** (September 2026). Older OpenRGB versions won't
list it. Check with `openrgb --version`. The OLED screen is driven by
SteelPanel itself (below). Actuation points and rapid trigger are only
configurable in SteelSeries GG.

### Keyboard screen (OLED)

The screen shows up as its own device, "SteelSeries Apex Pro Gen 3 /
Keyboard screen", with a **Screen** tab. **Show** picks what's on it:

| Show | |
|---|---|
| Keyboard default | SteelPanel leaves the screen alone |
| Clock | 24-hour (optionally with seconds) or 12-hour, with or without the date |
| Text | your text, in the largest size that fits |
| Image | any image file, scaled to 128×40 and dithered to black and white |
| System monitor | CPU and memory use |

- Like button remapping, it's shown while SteelPanel runs (it can sit in the
  tray). Quitting SteelPanel, or switching back to Keyboard default, blanks
  the screen.
- It needs the udev rules from `sudo steelpanel --install-udev` (run it again
  if you installed them before this feature), then replug the keyboard.
- Confirmed on an Apex Pro Gen 3: updates show up immediately, and the
  keyboard's own screen menu still works on top of SteelPanel's content.
- `steelpanel --oled-test` draws a test pattern for 10 seconds: a frame
  around the edge, a filled square in the top-left corner and "SteelPanel /
  screen test". If that looks wrong or nothing appears, please open an issue
  with what you saw.
- The report formats come from [apex-tux](https://github.com/not-jan/apex-tux),
  which also covers the older Apex Pro, Apex 7, Apex 5 and the Apex Pro TKL
  Wireless Gen 3, so SteelPanel tries those too; they're marked
  "experimental" until someone confirms them.

## Install

```bash
sudo dnf install python3-evdev   # for button remapping
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
steelpanel --oled-test      # test pattern on the keyboard screen
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
  remap.py            software button remapping and angle snapping (evdev/uinput)
  udev.py             udev rules for remapping
  backends/rival.py   mice via rivalcfg
  backends/openrgb.py keyboards via OpenRGB SDK
  backends/screen.py  keyboard OLED screens
  oled.py             screen protocol, rendering and refresh loop
  gui/                PySide6 window, per-setting editor widgets
  __main__.py         CLI entry point
tests/                pytest suite (no hardware needed)
```
