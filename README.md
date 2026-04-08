# Water Cooler CLI — Linux headless control for XMG Oasis

Control XMG Oasis 1 (LCT21001) and Oasis 2 (LCT22002) water coolers over Bluetooth from Linux — no GUI needed

Also compatible with: Eluktronics Liquid Pad Pro, PC Specialist Liquid Series, TUXEDO Aquaris.

## Install

```bash
git clone https://github.com/anvme/watercooler-xmg-neo-linux
cd watercooler-xmg-neo-linux
sudo bash install.sh
```

The installer:
- Detects your distro (Ubuntu/Debian, RHEL/AlmaLinux/Rocky/Fedora, Arch, openSUSE)
- Installs system deps (python3, bluez, dbus)
- Creates Python venv at `/opt/watercooler/`
- Sets up systemd service + `watercooler` CLI wrapper

## Quick start

```bash
# Enable auto-speed daemon on boot
sudo systemctl enable --now watercooler

# Set RGB color
watercooler rgb --hex #00ffff --mode static

# Max cooling
watercooler speed --max
```

## Speed modes

```bash
watercooler speed --auto              # temp-based (default)
watercooler speed --max               # fan 90%, pump 12V
watercooler speed --fan 75 --pump-voltage 11  # manual
```

Auto mode tiers:

| CPU temp | Fan  | Pump |
|----------|------|------|
| < 55C    | 25%  | 7V   |
| < 70C    | 50%  | 8V   |
| < 85C    | 75%  | 11V  |
| 85C+     | 90%  | 11V  |

## RGB modes

```bash
watercooler rgb --hex #ff00aa --mode static
watercooler rgb --hex #00ff00 --mode breathe
watercooler rgb --mode rainbow
watercooler rgb --mode breathe-rainbow
watercooler rgb --off
```

## Other commands

```bash
watercooler scan          # find BT devices
watercooler temp          # show CPU temp + what tier applies
watercooler pump --voltage 8
watercooler fan --speed 75
watercooler reset
```

## Config files

All configs in `/opt/watercooler/`. Daemon watches them — no restart needed.

- `rgb.conf` — RGB color and mode
- `speed.conf` — speed mode (auto/max/manual)

## Web UI

Open [https://anvme.github.io/watercooler-xmg-neo-linux/](https://anvme.github.io/watercooler-xmg-neo-linux/)   to pick RGB colors visually. Generates CLI commands you paste via SSH.

## Logs

```bash
sudo journalctl -u watercooler -f
```

## Uninstall

```bash
sudo systemctl stop watercooler
sudo systemctl disable watercooler
sudo rm -rf /opt/watercooler /etc/systemd/system/watercooler.service /usr/local/bin/watercooler
sudo systemctl daemon-reload
```
