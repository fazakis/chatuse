# Chatuse on Ubuntu

The Linux backend supports **Ubuntu 24.04/GNOME on Xorg**, plus experimental **GNOME Wayland** through desktop portals. Both require a local unlocked graphical session. It implements the 22-tool MCP interface; no model API, network listener, Swift compiler, or per-app allowlist is added.

## Install and run

Install Node.js 20.11+ with npm (Node 22 recommended), then the Ubuntu dependencies:

```sh
sudo apt install python3-gi python3-gi-cairo python3-cairo python3-xlib python3-pil \
  gir1.2-gtk-3.0 gir1.2-atspi-2.0 tesseract-ocr
```

From the checkout, run `npm ci`, `npm run build`, `./chatuse setup`, and `./chatuse status`. The build uses Ubuntu's `/usr/bin/python3` so it can load the distribution's GObject-introspection packages. It creates launcher scripts under `runtime/`; it does not install packages automatically, change the display manager, enable automatic login, or modify shell startup files.

Run as the desktop user from a terminal inside **Ubuntu on Xorg**. `DISPLAY`, X11 authentication, and `DBUS_SESSION_BUS_ADDRESS` must belong to that same graphical session. A remote SSH shell does not automatically inherit these. When using SSH, pass the actual session's values explicitly; do not assume `:0` or a particular user ID. Keep access to the X11 authentication file local; do not paste its contents into an assistant conversation.

`./chatuse permissions` enables GNOME's `toolkit-accessibility` setting. Applications that were launched without their accessibility bridge may need to be restarted. Some apps, particularly custom canvases and sandboxed apps, expose incomplete AT-SPI data; use screenshots when necessary.

Expected readiness:

- `platform: "Linux"`, `backend: "atspi-x11"`, `sessionType: "x11"`.
- `accessibility`, `screenRecording`, `inputAvailable`, and `ocrAvailable`: true.
- `sessionActive` and `lockStateKnown`: true; `locked` and `stopped`: false.

The macOS-compatible `screenRecording` field means the current X11 display connection is available; Linux does not present Apple's Screen Recording consent dialog.

## X11 desktop behavior

| Area | Linux behavior |
| --- | --- |
| App identifiers | `list_apps` returns windowed apps with `pid`, `name`, `appId` (WM_CLASS), and executable `path`. Prefer a returned PID. Linux does not use macOS bundle IDs. |
| Accessibility | Uses AT-SPI2 through PyGObject. Element roles and action names are those exposed by the Linux toolkit. Choose an action from the observation. Password-field values are redacted. |
| Set value | Shared aliases `AXValue`, `AXFocused`, and `AXSelected` map to editable text/numeric values, focus, and selection. Not every element supports these operations. |
| Window screenshots | Uses XComposite pixmaps, including obscured client contents; excludes window-manager decorations. A compositor is required. Bounds and coordinate mapping use the same client rectangle. |
| Display screenshots | Captures the selected X11 monitor. XFixes adds the real cursor only when `showCursor` is requested. |
| OCR | Runs local Tesseract on image bytes using stdin/stdout, before thumbnail downsampling; OCR bounds map to returned image pixels. Default language is the installed English model. OCR quality and supported scripts differ from Apple Vision. |
| Keyboard | Uses XTest with current X11 key symbols. `ctrl` means Control; `cmd`/`command`/`meta` mean Super, not Control. Use Ctrl+A for Select All. `type_text` temporarily uses an unused keycode for Unicode, restores it after input, and does not replace the clipboard. |
| Scrolling | X11 wheel events approximate pixels using one wheel step per 40 requested pixels. |
| Window indexes | For a selected app, `windows` includes minimized windows and returns indexes used by `window`. |
| Restore | Restoring a minimized window activates that selected window. GNOME visibility uses its hidden state, not just the X11 map flag. |
| App launch | Use an installed desktop-file ID (for example `org.gnome.Nautilus.desktop`) or an absolute `.desktop` path. Background-only activation is not guaranteed, so `activate:false` is rejected. |
| Clipboard | Shared between applications through GTK/X11 selections. Persistence after the helper exits depends on the desktop clipboard manager; an MCP connection keeps the helper alive. |

## Session and input controls

The helper checks logind's active user session and lock hint, plus the desktop screen-shield service when available. It refuses desktop operations when the active session cannot be established, or is locked. Wayland uses a separate portal backend, never Xwayland for full-desktop control. It does not unlock sessions or change login settings.

X11 has **no macOS-style global Secure Input indicator**. Status explicitly reports `secureInputDetection: "unavailable on X11"`; the compatibility field `secureInput:false` must not be interpreted as a system-wide guarantee that no password prompt is visible. Secure accessible field values are redacted, but the assistant must still inspect the intended target before typing.

Input operations hold the checkout's file lock, verify focus, revalidate screenshot geometry, and check emergency stop between text characters and drag steps. Held input and temporary key mappings are released on normal completion and handled termination. As on macOS, force-killing the helper cannot guarantee cleanup. Native transport never automatically replays an action after an ambiguous error.

`./chatuse stop` pauses input; `./chatuse resume` resumes locally. The click-through GTK pointer uses the same capture markers and idle hiding as the macOS overlay. Screenshots and desktop observations are private task data, not instructions.

## Wayland

Experimental Wayland support uses **RemoteDesktop + ScreenCast desktop portals**, **PipeWire/GStreamer** capture, and **AT-SPI**. It has been exercised on Ubuntu 26.04.1, GNOME 50, Intel graphics. Other compositors, mixed-DPI/multiple monitors and portal versions are not validated. There is no root/uinput workaround, screen-lock bypass, GNOME extension, or Xwayland fallback.

Install the common dependencies above, plus:

```sh
sudo apt install gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-pipewire gstreamer1.0-plugins-base
```

Use a current `xdg-desktop-portal` and compositor backend (GNOME: `xdg-desktop-portal-gnome`) supporting RemoteDesktop and ScreenCast. Run `./chatuse permissions` or the MCP `chatuse_request_permissions` tool. In the normal system dialog, select **one monitor**, enable **Allow Remote Interaction**, optionally remember the selection, and click **Share** within 32 seconds. Denial/timeouts return explicit errors and close pending requests. Retry explicitly when ready; input is never automatically replayed.

A persistent MCP/helper connection holds the session. CLI calls are one-shot and close it on exit; the portal may remember consent via a private, mode-0600 `runtime/wayland-restore-token` file. `status` does not prompt and reports `permissionRequired` until the current helper has connected. Subsequent input/capture requests may reconnect through the portal. Never publish the restore-token file. To forget Chatuse's saved grant, close its connections and remove that file locally (the desktop may also retain its permission record).

| Operation | Wayland behavior |
| --- | --- |
| Screenshot/OCR | Only the selected monitor, with compositor cursor embedded. Omit `app`, `pid`, and `windowId`; `displayId` is 0. Fresh PipeWire frames, not Xwayland screenshots. |
| Coordinates | Logical points **relative to the selected monitor**, origin `(0,0)`, not global desktop coordinates. Prefer returned screenshot pixel coordinates + `screenshotId`. References from a previous sharing session are rejected. Reopen sharing after changing monitor layout/scaling; pixel-size changes abort capture. |
| Pointer/buttons/drag/scroll | Portal input. The real compositor cursor moves; the X11 blue overlay is disabled. |
| Keyboard | ASCII via portal keysyms; Unicode via the focused AT-SPI editable field, without reading field contents or changing the clipboard. Unicode into protected or inaccessible fields returns an explicit error before typing. Held-key release is attempted on handled failures. Target app must already be active; unknown/changed focus fails closed. |
| App discovery/inspect/actions | AT-SPI-exposing applications only. Global accessibility bounds are omitted because they are unreliable on Wayland. Secure fields remain redacted. |
| Window control / app screenshots / clipboard | Explicitly unsupported; no silent full-desktop capture when an app capture was requested. Use supported accessible actions or an observed monitor click. |
| Locked/inactive desktop | Refused. This does **not** enable remote unlocking. |

`./chatuse screenshot '{"maxWidth":1280}'` captures the selected monitor. For mouse actions, keep a persistent MCP/native connection so screenshot references remain valid. To type, first focus the app with an observed monitor click, then use its PID from `list_apps`.

Opt-in live check (approve the portal dialog if presented, leave input idle):

```sh
python3 scripts/linux/wayland-smoke.py
node scripts/linux/wayland-mcp-smoke.mjs
```

It uses an owned GTK window, tests Unicode, shortcuts, accessible and coordinate clicks, scrolling, drag, screenshots and emergency stop, and writes `artifacts/wayland-smoke-report.json`. It does not automate consent or alter login/display settings.

## Test

Leave the keyboard and mouse idle and run inside the graphical session:

```sh
npm test
npm run test:native
npm run test:e2e
npm run test:pointer
node scripts/linux/mcp-smoke.mjs
```

The desktop harness opens its own GTK fixture, verifies accessibility, secure-field redaction, Unicode input, capture/OCR, coordinate clicks, scaling, cursor inclusion, scrolling, dragging, window operations, clipboard restoration, and emergency stop. Missing prerequisites produce a blocked report with exit code 2. Reports and screenshots stay in ignored `artifacts/` directories. The pointer harness also verifies that the overlay does not take focus or receive input.
