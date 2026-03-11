#!/bin/bash
set -e

INSTALL_DIR="/opt/watercooler"
SERVICE_NAME="watercooler"

# --- Check root ---
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo bash install.sh"
    exit 1
fi

echo "=== Water Cooler CLI — Installer ==="
echo ""

# --- Detect distro ---
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="$ID"
else
    DISTRO="unknown"
fi
echo "Detected: $DISTRO ($PRETTY_NAME)"

# --- Install system packages ---
echo ""
echo "Installing system dependencies..."

case "$DISTRO" in
    ubuntu|debian|pop|linuxmint)
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip bluez bluetooth dbus > /dev/null
        ;;
    rhel|almalinux|rocky|centos|fedora|ol)
        dnf install -y -q python3 python3-pip bluez bluez-libs dbus > /dev/null
        ;;
    arch|manjaro)
        pacman -Sy --noconfirm --needed python python-pip bluez bluez-utils dbus > /dev/null
        ;;
    opensuse*|sles)
        zypper install -y -q python3 python3-pip bluez dbus-1 > /dev/null
        ;;
    *)
        echo "WARNING: Unknown distro '$DISTRO'. Please install manually:"
        echo "  python3, python3-pip, python3-venv, bluez, dbus"
        echo "Continuing anyway..."
        ;;
esac

# --- Enable bluetooth ---
echo "Enabling bluetooth service..."
systemctl enable bluetooth --now 2>/dev/null || true

# --- Install app ---
echo ""
echo "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp watercooler.py "$INSTALL_DIR/"
cp requirements.txt "$INSTALL_DIR/"

# --- Create venv + install deps ---
echo "Setting up Python venv..."
python3 -m venv "$INSTALL_DIR/venv" 2>/dev/null || python3 -m venv "$INSTALL_DIR/venv" --without-pip
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q 2>/dev/null || true
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

# --- Create default RGB config ---
if [ ! -f "$INSTALL_DIR/rgb.conf" ]; then
    echo '{"mode": "static", "hex": "#00ffff"}' > "$INSTALL_DIR/rgb.conf"
    echo "Created default RGB config (Cyan static)"
fi
if [ ! -f "$INSTALL_DIR/speed.conf" ]; then
    echo '{"mode": "auto"}' > "$INSTALL_DIR/speed.conf"
    echo "Created default speed config (Auto)"
fi

# --- Install systemd service ---
echo "Installing systemd service..."
cp watercooler.service /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload

# --- Symlink for CLI use ---
ln -sf "$INSTALL_DIR/venv/bin/python3" /usr/local/bin/watercooler-python 2>/dev/null || true
cat > /usr/local/bin/watercooler <<'WRAPPER'
#!/bin/bash
exec /opt/watercooler/venv/bin/python3 /opt/watercooler/watercooler.py "$@"
WRAPPER
chmod +x /usr/local/bin/watercooler

echo ""
echo "=== Installed ==="
echo ""
echo "CLI usage:"
echo "  watercooler scan"
echo "  watercooler pump --voltage 8"
echo "  watercooler fan --speed 75"
echo "  watercooler temp"
echo ""
echo "Daemon (auto speed):"
echo "  sudo systemctl start ${SERVICE_NAME}     # start now"
echo "  sudo systemctl enable ${SERVICE_NAME}    # start on boot"
echo "  sudo journalctl -u ${SERVICE_NAME} -f    # view logs"
echo ""
echo "To uninstall:"
echo "  sudo systemctl stop ${SERVICE_NAME} && sudo systemctl disable ${SERVICE_NAME}"
echo "  sudo rm -rf $INSTALL_DIR /etc/systemd/system/${SERVICE_NAME}.service /usr/local/bin/watercooler"
echo "  sudo systemctl daemon-reload"
