#!/usr/bin/env python3
"""CLI + daemon control for LCT water coolers — no GUI required.

Usage:
    python3 watercooler.py scan
    python3 watercooler.py pump --voltage 8
    python3 watercooler.py fan --speed 75
    python3 watercooler.py rgb --color blue --mode breathe
    python3 watercooler.py pump --off
    python3 watercooler.py reset
    python3 watercooler.py daemon [-a ADDRESS] [--interval 5]
"""

import os
import sys
import json
import signal
import asyncio
import argparse
import logging
from enum import IntEnum
from typing import Optional
from bleak import BleakScanner, BleakClient

log = logging.getLogger("watercooler")


# === Enums ===

class RGBState(IntEnum):
    STATIC = 0x00
    BREATHE = 0x01
    COLORFUL = 0x02
    BREATHE_COLOR = 0x03

class PumpVoltage(IntEnum):
    V11 = 0x00
    V12 = 0x01
    V7 = 0x02
    V8 = 0x03

class Commands:
    RESET = 0x19
    FAN = 0x1b
    PUMP = 0x1c
    RGB = 0x1e

class NordicUART:
    CHAR_TX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'


# === Device ===

DEVICE_NAMES = ['lct21001', 'lct22002']

class WaterCoolingDevice:
    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.model: Optional[str] = None

    async def scan(self):
        devices = await BleakScanner.discover(return_adv=True)
        results = []
        for addr, (device, adv) in devices.items():
            if not device.name:
                continue
            name_lower = device.name.lower()
            if any(n in name_lower for n in DEVICE_NAMES):
                results.append({"uuid": device.address, "name": device.name, "rssi": adv.rssi or 0})
        return results

    async def connect(self, address: str):
        self.client = BleakClient(address)
        await self.client.connect(timeout=5.0)

    async def disconnect(self):
        if self.client and self.client.is_connected:
            try:
                await self._send(Commands.RESET, [0x00, 0x01, 0x00, 0x00, 0x00])
            except Exception:
                pass
            await self.client.disconnect()
        self.client = None

    async def is_connected(self):
        return self.client is not None and self.client.is_connected

    async def _send(self, cmd, payload):
        if not self.client or not self.client.is_connected:
            raise RuntimeError("Not connected")
        data = bytearray([0xfe, cmd] + payload + [0xef])
        await self.client.write_gatt_char(NordicUART.CHAR_TX, data)

    async def pump_on(self, voltage: PumpVoltage):
        await self._send(Commands.PUMP, [0x01, 60, voltage, 0x00, 0x00])

    async def pump_off(self):
        await self._send(Commands.PUMP, [0x00, 0x00, 0x00, 0x00, 0x00])

    async def fan_on(self, speed: int):
        await self._send(Commands.FAN, [0x01, speed, 0x00, 0x00, 0x00])

    async def fan_off(self):
        await self._send(Commands.FAN, [0x00, 0x00, 0x00, 0x00, 0x00])

    async def rgb_on(self, r: int, g: int, b: int, mode: RGBState):
        await self._send(Commands.RGB, [0x01, r, g, b, mode])

    async def rgb_off(self):
        await self._send(Commands.RGB, [0x00, 0x00, 0x00, 0x00, 0x00])

    async def reset(self):
        await self._send(Commands.RESET, [0x00, 0x01, 0x00, 0x00, 0x00])


# === Temperature reading ===

def read_cpu_temp():
    """Read highest CPU temp in celsius from thermal zones."""
    temps = []
    thermal_base = "/sys/class/thermal"
    if not os.path.isdir(thermal_base):
        return None
    for entry in os.listdir(thermal_base):
        if not entry.startswith("thermal_zone"):
            continue
        try:
            type_path = os.path.join(thermal_base, entry, "type")
            temp_path = os.path.join(thermal_base, entry, "temp")
            with open(type_path) as f:
                zone_type = f.read().strip().lower()
            # prefer CPU/package zones, but collect all
            with open(temp_path) as f:
                t = int(f.read().strip()) / 1000.0
                if t > 0:
                    temps.append((zone_type, t))
        except (IOError, ValueError):
            continue
    if not temps:
        return None
    # prefer x86_pkg_temp, coretemp, or anything with "cpu"/"pkg" in name
    for keyword in ["pkg", "cpu", "core", "soc"]:
        for zone_type, t in temps:
            if keyword in zone_type:
                return t
    # fallback: highest temp
    return max(t for _, t in temps)


# === Auto speed profile ===

# (max_temp, fan_speed, pump_voltage)
DEFAULT_PROFILE = [
    (55,  25, PumpVoltage.V7),
    (70,  50, PumpVoltage.V8),
    (85,  75, PumpVoltage.V11),
    (999, 90, PumpVoltage.V11),
]

def get_tier_for_temp(temp, profile=DEFAULT_PROFILE):
    for max_temp, fan, pump in profile:
        if temp < max_temp:
            return fan, pump
    return profile[-1][1], profile[-1][2]


# === Config files ===

CONF_DIR = os.environ.get("WATERCOOLER_CONF_DIR", "/opt/watercooler")
RGB_CONF = os.path.join(CONF_DIR, "rgb.conf")
SPEED_CONF = os.path.join(CONF_DIR, "speed.conf")

DEFAULT_RGB_CONF = {
    "mode": "static",
    "hex": "#00ffff",
}

DEFAULT_SPEED_CONF = {
    "mode": "auto",  # auto | max | manual
}

# Max mode: fan 90%, pump 12V
MAX_FAN = 90
MAX_PUMP = PumpVoltage.V12

def read_rgb_conf():
    """Read RGB config. Returns dict with 'mode' and optionally 'hex'."""
    try:
        with open(RGB_CONF) as f:
            return json.loads(f.read())
    except (IOError, json.JSONDecodeError):
        return DEFAULT_RGB_CONF.copy()

def write_rgb_conf(conf):
    """Write RGB config file."""
    os.makedirs(os.path.dirname(RGB_CONF), exist_ok=True)
    with open(RGB_CONF, "w") as f:
        json.dump(conf, f, indent=2)
        f.write("\n")

def conf_mtime(path):
    """Return mtime of config file, or 0 if missing."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0

def read_speed_conf():
    """Read speed config. Returns dict with 'mode' and optionally 'fan'/'voltage'."""
    try:
        with open(SPEED_CONF) as f:
            return json.loads(f.read())
    except (IOError, json.JSONDecodeError):
        return DEFAULT_SPEED_CONF.copy()

def write_speed_conf(conf):
    """Write speed config file."""
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(SPEED_CONF, "w") as f:
        json.dump(conf, f, indent=2)
        f.write("\n")

def apply_rgb_conf(dev, conf):
    """Apply RGB config to device. Returns coroutine."""
    mode_str = conf.get("mode", "static")
    if mode_str == "off":
        return dev.rgb_off()
    rgb_mode = RGB_MODE_MAP.get(mode_str, RGBState.STATIC)
    if mode_str in ("rainbow", "breathe-rainbow"):
        return dev.rgb_on(0, 0, 0, rgb_mode)
    hex_color = conf.get("hex", "#00ffff").lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return dev.rgb_on(r, g, b, rgb_mode)


# === Daemon ===

async def daemon_loop(address, interval, retries):
    """Main daemon loop: connect, monitor temp, adjust cooling."""
    dev = WaterCoolingDevice()
    current_fan = None
    current_pump = None
    last_rgb_mtime = 0
    last_speed_mtime = 0

    while True:
        # Connect / reconnect
        if not await dev.is_connected():
            target = address
            if not target:
                log.info("Scanning for device...")
                for attempt in range(retries):
                    devices = await dev.scan()
                    if devices:
                        target = devices[0]["uuid"]
                        log.info(f"Found: {devices[0]['name']} [{target}]")
                        break
                    log.warning(f"Scan attempt {attempt+1}/{retries} — no device found")
                    await asyncio.sleep(5)
                if not target:
                    log.error("Device not found after retries, waiting 30s...")
                    await asyncio.sleep(30)
                    continue

            try:
                log.info(f"Connecting to {target}...")
                await dev.connect(target)
                log.info("Connected.")
                # Apply RGB from config on connect
                try:
                    conf = read_rgb_conf()
                    await apply_rgb_conf(dev, conf)
                    last_rgb_mtime = rgb_conf_mtime()
                    log.info(f"RGB set from config: {conf}")
                except Exception:
                    pass
                current_fan = None
                current_pump = None
            except Exception as e:
                log.error(f"Connection failed: {e}, retrying in 10s...")
                await asyncio.sleep(10)
                continue

        # Check if RGB config changed
        try:
            mtime = conf_mtime(RGB_CONF)
            if mtime > last_rgb_mtime:
                rgb = read_rgb_conf()
                await apply_rgb_conf(dev, rgb)
                last_rgb_mtime = mtime
                log.info(f"RGB updated from config: {rgb}")
        except Exception as e:
            log.warning(f"RGB config apply failed: {e}")

        # Check if speed config changed (force re-apply)
        mtime = conf_mtime(SPEED_CONF)
        if mtime > last_speed_mtime:
            current_fan = None
            current_pump = None
            last_speed_mtime = mtime

        # Determine fan/pump target
        speed_conf = read_speed_conf()
        speed_mode = speed_conf.get("mode", "auto")

        if speed_mode == "max":
            fan, pump = MAX_FAN, MAX_PUMP
        elif speed_mode == "manual":
            fan = speed_conf.get("fan", 50)
            pump = VOLTAGE_MAP.get(str(speed_conf.get("voltage", "8")), PumpVoltage.V8)
        else:
            # auto mode — read temp
            temp = read_cpu_temp()
            if temp is None:
                log.warning("Could not read CPU temp")
                await asyncio.sleep(interval)
                continue
            fan, pump = get_tier_for_temp(temp)

        # Only send commands when values change
        try:
            if fan != current_fan:
                await dev.fan_on(fan)
                log.info(f"Fan: {fan}% (mode={speed_mode})")
                current_fan = fan
            if pump != current_pump:
                await dev.pump_on(pump)
                pump_name = pump.name if hasattr(pump, 'name') else str(pump)
                log.info(f"Pump: {pump_name} (mode={speed_mode})")
                current_pump = pump
        except Exception as e:
            log.error(f"BT write failed: {e}")
            try:
                await dev.disconnect()
            except Exception:
                pass
            current_fan = None
            current_pump = None
            await asyncio.sleep(5)
            continue

        await asyncio.sleep(interval)


async def run_daemon(args):
    log.info("Starting watercooler daemon")
    log.info(f"Profile: <55C=25%/7V, <70C=50%/8V, <85C=75%/11V, 85C+=90%/11V")
    log.info(f"Poll interval: {args.interval}s")

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def handle_signal():
        log.info("Shutting down...")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    daemon_task = asyncio.create_task(daemon_loop(args.address, args.interval, args.retries))

    await stop.wait()
    daemon_task.cancel()
    try:
        await daemon_task
    except asyncio.CancelledError:
        pass
    log.info("Stopped.")


# === CLI one-shot commands ===

VOLTAGE_MAP = {"7": PumpVoltage.V7, "8": PumpVoltage.V8, "11": PumpVoltage.V11, "12": PumpVoltage.V12}
RGB_MODE_MAP = {"static": RGBState.STATIC, "breathe": RGBState.BREATHE, "rainbow": RGBState.COLORFUL, "breathe-rainbow": RGBState.BREATHE_COLOR}
COLOR_MAP = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255), "white": (255, 255, 255)}


async def run(args):
    dev = WaterCoolingDevice()

    if args.command == "scan":
        print("Scanning for devices...")
        devices = await dev.scan()
        if not devices:
            print("No water cooler found. Is Bluetooth on and the cooler blinking?")
            return
        for d in devices:
            print(f"  {d['name']}  [{d['uuid']}]  RSSI: {d['rssi']}")
        return

    if args.command == "temp":
        t = read_cpu_temp()
        if t is None:
            print("Could not read CPU temperature")
            return 1
        fan, pump = get_tier_for_temp(t)
        print(f"CPU: {t:.1f}C -> would set fan={fan}% pump={pump.name}")
        return

    if args.command == "speed":
        if args.max:
            write_speed_conf({"mode": "max"})
            print("Speed: MAX (fan 90%, pump 12V) — saved to config")
        elif args.auto:
            write_speed_conf({"mode": "auto"})
            print("Speed: AUTO (temp-based) — saved to config")
        else:
            conf = {"mode": "manual", "fan": args.fan, "voltage": args.pump_voltage}
            write_speed_conf(conf)
            print(f"Speed: MANUAL fan={args.fan}% pump={args.pump_voltage}V — saved to config")
        print("Daemon will apply within 5 seconds.")
        return

    # All other commands need a device
    if not args.address:
        print("Scanning for device...")
        devices = await dev.scan()
        if not devices:
            print("No device found. Use --address or check Bluetooth.")
            return 1
        args.address = devices[0]["uuid"]
        print(f"Found: {devices[0]['name']} [{args.address}]")

    print(f"Connecting to {args.address}...")
    await dev.connect(args.address)
    print("Connected.")

    try:
        if args.command == "pump":
            if args.off:
                await dev.pump_off()
                print("Pump OFF")
            else:
                v = VOLTAGE_MAP.get(args.voltage, PumpVoltage.V7)
                await dev.pump_on(v)
                print(f"Pump set to {args.voltage}V")

        elif args.command == "fan":
            if args.off:
                await dev.fan_off()
                print("Fan OFF")
            else:
                await dev.fan_on(args.speed)
                print(f"Fan set to {args.speed}%")

        elif args.command == "rgb":
            if args.off:
                await dev.rgb_off()
                write_rgb_conf({"mode": "off"})
                print("RGB OFF (saved to config)")
            else:
                if args.hex:
                    h = args.hex.lstrip('#')
                    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                else:
                    r, g, b = COLOR_MAP.get(args.color, (255, 0, 0))
                mode = RGB_MODE_MAP.get(args.mode, RGBState.STATIC)
                await dev.rgb_on(r, g, b, mode)
                hex_str = f"#{r:02x}{g:02x}{b:02x}"
                write_rgb_conf({"mode": args.mode, "hex": hex_str})
                print(f"RGB: ({r},{g},{b}) / {args.mode} (saved to config)")

        elif args.command == "reset":
            await dev.reset()
            print("Reset sent.")

    finally:
        await dev.disconnect()
        print("Disconnected.")


def main():
    p = argparse.ArgumentParser(description="Water Cooler CLI — headless control over SSH")
    p.add_argument("--address", "-a", help="Device BT address (auto-detects if omitted)")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Scan for devices")
    sub.add_parser("reset", help="Send reset command")
    sub.add_parser("temp", help="Read CPU temp and show what tier would apply")

    pump = sub.add_parser("pump", help="Control pump")
    pump.add_argument("--off", action="store_true", help="Turn pump off")
    pump.add_argument("--voltage", "-v", choices=["7", "8", "11", "12"], default="7", help="Pump voltage")

    fan = sub.add_parser("fan", help="Control fan")
    fan.add_argument("--off", action="store_true", help="Turn fan off")
    fan.add_argument("--speed", "-s", type=int, choices=[25, 50, 75, 90], default=50, help="Fan speed %%")

    rgb = sub.add_parser("rgb", help="Control RGB")
    rgb.add_argument("--off", action="store_true", help="Turn RGB off")
    rgb.add_argument("--color", "-c", choices=list(COLOR_MAP), default="red")
    rgb.add_argument("--hex", help="Custom hex color e.g. #ff00aa")
    rgb.add_argument("--mode", "-m", choices=list(RGB_MODE_MAP), default="static")

    speed = sub.add_parser("speed", help="Set speed mode (daemon applies automatically)")
    speed.add_argument("--max", action="store_true", help="Maximum: fan 90%%, pump 12V")
    speed.add_argument("--auto", action="store_true", help="Auto: temp-based (default)")
    speed.add_argument("--fan", type=int, choices=[25, 50, 75, 90], default=50, help="Manual fan speed %%")
    speed.add_argument("--pump-voltage", type=str, choices=["7", "8", "11", "12"], default="8", help="Manual pump voltage")

    daemon = sub.add_parser("daemon", help="Run as daemon with auto fan/pump control")
    daemon.add_argument("--interval", "-i", type=int, default=5, help="Temp poll interval in seconds (default: 5)")
    daemon.add_argument("--retries", "-r", type=int, default=6, help="Scan retries before giving up (default: 6)")

    args = p.parse_args()

    if args.command == "daemon":
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        asyncio.run(run_daemon(args))
    else:
        sys.exit(asyncio.run(run(args)) or 0)


if __name__ == "__main__":
    main()
