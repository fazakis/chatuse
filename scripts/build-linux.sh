#!/bin/sh
set -eu
chatuse_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$chatuse_root"
/usr/bin/python3 - <<'PY'
try:
    import gi, cairo, Xlib, PIL
    for name, version in [('Gtk', '3.0'), ('GdkX11', '3.0'), ('Atspi', '2.0')]:
        gi.require_version(name, version)
except (ImportError, ValueError) as error:
    raise SystemExit('Missing Ubuntu desktop dependencies. Run: sudo apt install python3-gi python3-gi-cairo python3-cairo python3-xlib python3-pil gir1.2-gtk-3.0 gir1.2-atspi-2.0 tesseract-ocr') from error
PY
mkdir -p runtime artifacts
if [ "${1:-}" != '--pointer-only' ]; then
  cat > runtime/chatuse-native <<'SH'
#!/bin/sh
set -eu
chatuse_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec /usr/bin/python3 "$chatuse_root/linux/main.py" "$@"
SH
  chmod +x runtime/chatuse-native
fi
cat > runtime/chatuse-pointer <<'SH'
#!/bin/sh
set -eu
chatuse_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec /usr/bin/python3 "$chatuse_root/linux/pointer.py" "$@"
SH
chmod +x runtime/chatuse-pointer
ln -sf "$(command -v node)" runtime/node
printf '%s\n' 'Built Chatuse Linux/X11 launchers. Run ./chatuse status inside your desktop session.'
