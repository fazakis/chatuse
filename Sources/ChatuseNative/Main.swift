import AppKit
import ApplicationServices
import ScreenCaptureKit
import Vision
import Carbon
import ChatuseCore

typealias Object = [String: Any]

func fail(_ code: String, _ message: String) throws -> Never { throw ChatuseError(code, message) }
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func str(_ element: AXUIElement, _ name: String) -> String? { attr(element, name) as? String }
func children(_ element: AXUIElement) -> [AXUIElement] { attr(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] }
func pointValue(_ element: AXUIElement, _ name: String) -> CGPoint? {
    guard let value = attr(element, name), CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    return AXValueGetValue(value as! AXValue, .cgPoint, &point) ? point : nil
}
func sizeValue(_ element: AXUIElement) -> CGSize? {
    guard let value = attr(element, kAXSizeAttribute), CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    var size = CGSize.zero
    return AXValueGetValue(value as! AXValue, .cgSize, &size) ? size : nil
}
func bounds(_ element: AXUIElement) -> CGRect? {
    guard let p = pointValue(element, kAXPositionAttribute), let s = sizeValue(element) else { return nil }
    return CGRect(origin: p, size: s)
}
func rectJSON(_ r: CGRect) -> Object { ["x": r.minX, "y": r.minY, "width": r.width, "height": r.height] }
func actions(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    AXUIElementCopyActionNames(element, &names)
    return names as? [String] ?? []
}
func isSecure(_ element: AXUIElement) -> Bool { str(element, kAXSubroleAttribute) == kAXSecureTextFieldSubrole }

struct ElementSnapshot {
    let appPID: pid_t
    let clock = SnapshotClock()
    var elements: [String: AXUIElement]
}
struct ImageSnapshot {
    let frame: CGRect
    let width: Int
    let height: Int
    let appPID: pid_t?
    let windowID: CGWindowID?
    let clock = SnapshotClock()
}

@MainActor final class Driver {
    var snapshots: [String: ElementSnapshot] = [:]
    var images: [String: ImageSnapshot] = [:]
    let root: URL
    var heldMouse: (CGPoint, CGMouseButton)?
    var stopped: Bool { FileManager.default.fileExists(atPath: root.appendingPathComponent("STOP").path) }
    init(root: URL) { self.root = root }

    func requireSession() throws {
        let info = CGSessionCopyCurrentDictionary() as? Object ?? [:]
        if info["CGSSessionScreenIsLocked"] as? Bool == true || info[kCGSessionOnConsoleKey as String] as? Bool == false {
            try fail("SESSION_LOCKED", "Unlock the Mac manually. Chatuse does not unlock sessions.")
        }
    }
    func requireAX() throws {
        guard AXIsProcessTrusted() else { try fail("ACCESSIBILITY_REQUIRED", "Enable Chatuse in System Settings > Privacy & Security > Accessibility, then restart Chatuse.") }
    }
    func requireInput() throws {
        guard !stopped else { try fail("STOPPED", "Emergency stop is active. Resume locally with chatuse resume.") }
        try requireSession(); try requireAX()
        if IsSecureEventInputEnabled() { try fail("SECURE_INPUT", "macOS Secure Input is active. Finish that prompt manually before sending keyboard or pointer input.") }
    }
    func app(_ args: Object) throws -> NSRunningApplication {
        if let pid = args["pid"] as? Int, let app = NSRunningApplication(processIdentifier: pid_t(pid)), !app.isTerminated { return app }
        let target = (args["app"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if target == "frontmost", let app = NSWorkspace.shared.frontmostApplication { return app }
        guard !target.isEmpty else { try fail("APP_REQUIRED", "Provide app bundle ID, absolute app path, exact name, or pid. Use list_apps first.") }
        let matches = NSWorkspace.shared.runningApplications.filter {
            $0.bundleIdentifier == target || $0.bundleURL?.path == target || $0.localizedName?.lowercased() == target.lowercased()
        }
        guard matches.count == 1 else { try fail("APP_NOT_UNIQUE", "Expected one running app matching \(target); found \(matches.count). Use list_apps and specify pid.") }
        return matches[0]
    }
    func activate(_ app: NSRunningApplication) async throws {
        try requireInput()
        guard !app.isTerminated else { try fail("APP_EXITED", "The app has exited.") }
        app.activate()
        for _ in 0..<20 {
            if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier { return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        try fail("FOCUS_FAILED", "Could not focus the requested app; no input was sent.")
    }
    func ensureFocus(_ pid: pid_t?) throws {
        try requireInput()
        if let pid, NSWorkspace.shared.frontmostApplication?.processIdentifier != pid {
            try fail("FOCUS_CHANGED", "Foreground app changed. Inspect and retry; input stopped.")
        }
    }
    func info(_ app: NSRunningApplication) -> Object {
        ["pid": Int(app.processIdentifier), "name": app.localizedName ?? "", "bundleId": app.bundleIdentifier ?? "",
         "path": app.bundleURL?.path ?? "", "active": app.isActive, "hidden": app.isHidden]
    }
    func status() -> Object {
        let session = CGSessionCopyCurrentDictionary() as? Object ?? [:]
        #if arch(x86_64)
        let architecture = "x86_64"
        #else
        let architecture = "arm64"
        #endif
        return ["version": "0.1.0", "platform": "macOS", "architecture": architecture,
                "accessibility": AXIsProcessTrusted(), "screenRecording": CGPreflightScreenCaptureAccess(),
                "secureInput": IsSecureEventInputEnabled(), "locked": session["CGSSessionScreenIsLocked"] as? Bool ?? false,
                "stopped": stopped, "perAppApprovals": false, "allAppsAllowed": true,
                "bundlePath": Bundle.main.bundlePath, "stopFile": root.appendingPathComponent("STOP").path,
                "capabilities": ["accessibility", "screenshots", "ocr", "mouse", "keyboard", "windows", "clipboard"],
                "limitations": ["No session unlocking", "Some apps expose incomplete accessibility trees", "Coordinate actions use the foreground desktop"]]
    }
    func listApps(_ args: Object) -> Object {
        let all = args["includeBackground"] as? Bool ?? false
        let query = (args["query"] as? String ?? "").lowercased()
        let list = NSWorkspace.shared.runningApplications.filter {
            (all || $0.activationPolicy == .regular) && (query.isEmpty || ($0.localizedName ?? "").lowercased().contains(query) || ($0.bundleIdentifier ?? "").lowercased().contains(query))
        }.map(info)
        return ["apps": list]
    }
    func listWindows(_ args: Object) throws -> Object {
        try requireSession()
        let pid: pid_t? = args["app"] != nil || args["pid"] != nil ? try app(args).processIdentifier : nil
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [Object] ?? []
        return ["windows": windows.filter { ($0[kCGWindowLayer as String] as? Int == 0) && (pid == nil || $0[kCGWindowOwnerPID as String] as? Int == Int(pid!)) }.map { w -> Object in
            ["windowId": w[kCGWindowNumber as String] ?? 0, "pid": w[kCGWindowOwnerPID as String] ?? 0,
             "app": w[kCGWindowOwnerName as String] ?? "", "title": w[kCGWindowName as String] ?? "",
             "bounds": w[kCGWindowBounds as String] ?? [:]]
        }]
    }
    func inspect(_ args: Object) throws -> Object {
        try requireSession(); try requireAX()
        let target = try app(args), root = AXUIElementCreateApplication(target.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 1)
        let maxDepth = min(max(args["maxDepth"] as? Int ?? 12, 1), 30)
        let maxNodes = min(max(args["maxNodes"] as? Int ?? 500, 1), 3000)
        let query = (args["query"] as? String ?? "").lowercased()
        let snapshotID = UUID().uuidString
        var refs: [String: AXUIElement] = [:], rows: [Object] = [], visited: [AXUIElement] = []
        let deadline = Date().addingTimeInterval(12)
        var truncated = false
        func walk(_ el: AXUIElement, _ depth: Int, _ parent: String?) {
            guard refs.count < maxNodes, Date() < deadline else { truncated = true; return }
            guard !visited.contains(where: { CFEqual($0, el) }) else { return }
            visited.append(el)
            let id = "e\(refs.count + 1)"; refs[id] = el
            var row: Object = ["id": id, "role": str(el, kAXRoleAttribute) ?? "unknown", "depth": depth]
            if let parent { row["parent"] = parent }
            for (out, key) in [("title", kAXTitleAttribute), ("description", kAXDescriptionAttribute), ("help", kAXHelpAttribute), ("subrole", kAXSubroleAttribute), ("identifier", kAXIdentifierAttribute)] {
                if let v = str(el, key), !v.isEmpty { row[out] = String(v.prefix(500)) }
            }
            if isSecure(el) { row["value"] = "[secure field]" }
            else if let v = attr(el, kAXValueAttribute) {
                if let s = v as? String { row["value"] = String(s.prefix(2000)) }
                else if CFGetTypeID(v) == CFBooleanGetTypeID() || CFGetTypeID(v) == CFNumberGetTypeID() { row["value"] = v }
            }
            row["enabled"] = attr(el, kAXEnabledAttribute) as? Bool ?? true
            let acts = actions(el); if !acts.isEmpty { row["actions"] = acts }
            if let r = bounds(el) { row["bounds"] = rectJSON(r) }
            if query.isEmpty || row.values.contains(where: { String(describing: $0).lowercased().contains(query) }) { rows.append(row) }
            if depth < maxDepth { for child in children(el) { walk(child, depth + 1, id) } }
            else if !children(el).isEmpty { truncated = true }
        }
        walk(root, 0, nil)
        snapshots = snapshots.filter { (try? $0.value.clock.validate()) != nil }
        if snapshots.count >= 8 { snapshots.removeAll() }
        snapshots[snapshotID] = ElementSnapshot(appPID: target.processIdentifier, elements: refs)
        return ["app": info(target), "snapshotId": snapshotID, "expiresInSeconds": 120, "elements": rows, "visited": refs.count, "truncated": truncated]
    }
    func element(_ args: Object) throws -> (AXUIElement, pid_t) {
        guard let snapshotID = args["snapshotId"] as? String, let snapshot = snapshots[snapshotID], let id = args["elementId"] as? String, let el = snapshot.elements[id] else {
            try fail("UNKNOWN_ELEMENT", "Use snapshotId and elementId from a recent inspect result.")
        }
        try snapshot.clock.validate()
        var role: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXRoleAttribute as CFString, &role) == .success else {
            try fail("STALE_ELEMENT", "The element is no longer available. Inspect the app again.")
        }
        return (el, snapshot.appPID)
    }
    func screenshot(_ args: Object) async throws -> Object {
        try requireSession()
        guard CGPreflightScreenCaptureAccess() else { try fail("SCREEN_RECORDING_REQUIRED", "Enable Chatuse in System Settings > Privacy & Security > Screen Recording, then restart Chatuse.") }
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let filter: SCContentFilter, frame: CGRect, pid: pid_t?, windowID: CGWindowID?
        if let wid = args["windowId"] as? Int {
            guard let w = content.windows.first(where: { $0.windowID == CGWindowID(wid) }) else { try fail("WINDOW_NOT_FOUND", "Window is no longer visible.") }
            filter = SCContentFilter(desktopIndependentWindow: w); frame = w.frame; pid = w.owningApplication?.processID; windowID = w.windowID
        } else if args["app"] != nil || args["pid"] != nil {
            let a = try app(args)
            guard let w = content.windows.first(where: { $0.owningApplication?.processID == a.processIdentifier && $0.windowLayer == 0 && $0.frame.width > 1 && $0.frame.height > 1 }) else { try fail("WINDOW_NOT_FOUND", "The app has no visible window.") }
            filter = SCContentFilter(desktopIndependentWindow: w); frame = w.frame; pid = a.processIdentifier; windowID = w.windowID
        } else {
            let did = args["displayId"] as? Int
            guard let d = content.displays.first(where: { did == nil ? $0.displayID == CGMainDisplayID() : $0.displayID == CGDirectDisplayID(did!) }) else { try fail("DISPLAY_NOT_FOUND", "Use displays to select an available display.") }
            filter = SCContentFilter(display: d, excludingWindows: []); frame = d.frame; pid = nil; windowID = nil
        }
        guard frame.width > 0, frame.height > 0 else { try fail("EMPTY_CAPTURE", "The capture region is empty.") }
        let maxWidth = min(max(args["maxWidth"] as? Int ?? 1440, 320), 3840)
        let scale = min(Double(maxWidth) / frame.width, 2)
        let cfg = SCStreamConfiguration()
        cfg.width = max(1, Int(frame.width * scale)); cfg.height = max(1, Int(frame.height * scale))
        cfg.ignoreShadowsSingleWindow = true
        cfg.showsCursor = args["showCursor"] as? Bool ?? false
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
        let bitmap = NSBitmapImageRep(cgImage: image)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { try fail("CAPTURE_FAILED", "Could not encode screenshot.") }
        let id = UUID().uuidString
        images = images.filter { (try? $0.value.clock.validate()) != nil }
        if images.count >= 8 { images.removeAll() }
        images[id] = ImageSnapshot(frame: frame, width: image.width, height: image.height, appPID: pid, windowID: windowID)
        var result: Object = ["screenshotId": id, "width": image.width, "height": image.height, "screenBounds": rectJSON(frame), "imageBase64": png.base64EncodedString(), "mimeType": "image/png", "expiresInSeconds": 120]
        if let pid { result["pid"] = Int(pid) }
        if let windowID { result["windowId"] = Int(windowID) }
        if args["ocr"] as? Bool == true {
            let req = VNRecognizeTextRequest(); req.recognitionLevel = .accurate; req.usesLanguageCorrection = true
            try VNImageRequestHandler(cgImage: image).perform([req])
            result["text"] = (req.results ?? []).compactMap { obs -> Object? in
                guard let c = obs.topCandidates(1).first else { return nil }
                let r = obs.boundingBox
                return ["text": c.string, "confidence": c.confidence,
                        "bounds": rectJSON(CGRect(x: r.minX * Double(image.width), y: (1-r.maxY) * Double(image.height), width: r.width * Double(image.width), height: r.height * Double(image.height)))]
            }
        }
        return result
    }
    func targetPoint(_ args: Object) throws -> (CGPoint, pid_t?) {
        guard let x = args["x"] as? Double, let y = args["y"] as? Double else { try fail("COORDINATES_REQUIRED", "Provide x and y.") }
        if let id = args["screenshotId"] as? String {
            guard let s = images[id] else { try fail("UNKNOWN_SCREENSHOT", "Capture a new screenshot first.") }
            try s.clock.validate()
            if let wid = s.windowID {
                let ws = CGWindowListCopyWindowInfo(.optionIncludingWindow, wid) as? [Object] ?? []
                guard let w = ws.first, let b = w[kCGWindowBounds as String] as? Object, let now = CGRect(dictionaryRepresentation: b as CFDictionary), abs(now.minX-s.frame.minX)<2, abs(now.minY-s.frame.minY)<2, abs(now.width-s.frame.width)<2, abs(now.height-s.frame.height)<2 else {
                    try fail("WINDOW_MOVED", "Window geometry changed. Capture a fresh screenshot before clicking.")
                }
            }
            return (try screenPoint(x: x, y: y, pixelWidth: Double(s.width), pixelHeight: Double(s.height), frame: s.frame), s.appPID)
        }
        let p = CGPoint(x: x, y: y)
        var count: UInt32 = 0; CGGetActiveDisplayList(0, nil, &count)
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count)); CGGetActiveDisplayList(count, &ids, &count)
        guard x.isFinite, y.isFinite, ids.contains(where: { CGDisplayBounds($0).contains(p) }) else { try fail("INVALID_COORDINATES", "Global screen point is outside the active displays.") }
        let pid = args["app"] != nil || args["pid"] != nil ? try app(args).processIdentifier : nil
        return (p, pid)
    }
    func mouseEvent(_ type: CGEventType, _ point: CGPoint, _ button: CGMouseButton = .left, count: Int = 1) throws {
        guard let e = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: button) else { try fail("EVENT_FAILED", "Could not create mouse event.") }
        e.setIntegerValueField(.mouseEventClickState, value: Int64(count)); e.post(tap: .cghidEventTap)
        if [.leftMouseDown, .rightMouseDown, .otherMouseDown, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged].contains(type) { heldMouse = (point, button) }
        if [.leftMouseUp, .rightMouseUp, .otherMouseUp].contains(type) { heldMouse = nil }
    }
    func releaseHeldInput() {
        if let (point, button) = heldMouse {
            let up: CGEventType = button == .right ? .rightMouseUp : button == .center ? .otherMouseUp : .leftMouseUp
            try? mouseEvent(up, point, button)
        }
    }
    func click(_ args: Object) async throws -> Object {
        try requireInput()
        if args["elementId"] != nil {
            let (el, _) = try element(args)
            let action = args["action"] as? String ?? kAXPressAction
            guard actions(el).contains(action) else { try fail("ACTION_UNSUPPORTED", "Element does not support \(action). Use its bounds for a coordinate click if appropriate.") }
            let result = AXUIElementPerformAction(el, action as CFString)
            guard result == .success else { try fail("AX_ACTION_FAILED", "Accessibility action failed: \(result.rawValue).") }
            return ["performed": action, "method": "accessibility"]
        }
        let (p, pid) = try targetPoint(args)
        if let pid, let a = NSRunningApplication(processIdentifier: pid) { try await activate(a) }
        // Activation may move/resize a window. Revalidate the screenshot after focusing.
        _ = try targetPoint(args); try ensureFocus(pid)
        let button = args["button"] as? String ?? "left"
        guard ["left", "right", "middle"].contains(button) else { try fail("INVALID_BUTTON", "Use left, right, or middle.") }
        let b: CGMouseButton = button == "right" ? .right : button == "middle" ? .center : .left
        let down: CGEventType = b == .right ? .rightMouseDown : b == .center ? .otherMouseDown : .leftMouseDown
        let up: CGEventType = b == .right ? .rightMouseUp : b == .center ? .otherMouseUp : .leftMouseUp
        for i in 1...min(max(args["count"] as? Int ?? 1, 1), 3) {
            try ensureFocus(pid); try mouseEvent(down, p, b, count: i)
            try mouseEvent(up, p, b, count: i)
        }
        return ["performed": "click", "x": p.x, "y": p.y, "method": "pointer"]
    }
    func keyboard(_ key: CGKeyCode, flags: CGEventFlags = [], units: [UInt16]? = nil) throws {
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: true), let up = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: false) else { try fail("EVENT_FAILED", "Could not create keyboard events.") }
        down.flags = flags; up.flags = flags
        if let units { units.withUnsafeBufferPointer { b in down.keyboardSetUnicodeString(stringLength: b.count, unicodeString: b.baseAddress); up.keyboardSetUnicodeString(stringLength: b.count, unicodeString: b.baseAddress) } }
        down.post(tap: .cghidEventTap); up.post(tap: .cghidEventTap)
    }
    func type(_ args: Object) async throws -> Object {
        try requireInput()
        guard let text = args["text"] as? String, text.utf16.count <= 50000 else { try fail("INVALID_TEXT", "Text is required and must be at most 50,000 UTF-16 units.") }
        let target = try app(args); try await activate(target)
        for chunk in unicodeChunks(text) {
            try ensureFocus(target.processIdentifier)
            try keyboard(0, units: chunk)
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        return ["typedCharacters": text.count]
    }
    func pressKey(_ args: Object) async throws -> Object {
        try requireInput()
        let name = (args["key"] as? String ?? "").lowercased()
        let map: [String: CGKeyCode] = ["a":0,"s":1,"d":2,"f":3,"h":4,"g":5,"z":6,"x":7,"c":8,"v":9,"b":11,"q":12,"w":13,"e":14,"r":15,"y":16,"t":17,"1":18,"2":19,"3":20,"4":21,"6":22,"5":23,"=":24,"9":25,"7":26,"-":27,"8":28,"0":29,
            "]":30,"o":31,"u":32,"[":33,"i":34,"p":35,"enter":36,"return":36,"l":37,"j":38,"'":39,"k":40,";":41,"\\":42,",":43,"/":44,"n":45,"m":46,".":47,"tab":48,"space":49,"`":50,"backspace":51,"escape":53,"delete":117,"home":115,"end":119,"pageup":116,"pagedown":121,"left":123,"right":124,"down":125,"up":126,"f1":122,"f2":120,"f3":99,"f4":118,"f5":96,"f6":97,"f7":98,"f8":100,"f9":101,"f10":109,"f11":103,"f12":111]
        guard let key = map[name] else { try fail("UNKNOWN_KEY", "Unknown key name. Use type_text for text; press_key names use a US physical keyboard layout.") }
        var flags: CGEventFlags = []
        for modifier in args["modifiers"] as? [String] ?? [] {
            switch modifier.lowercased() {
            case "cmd", "command", "meta": flags.insert(.maskCommand)
            case "ctrl", "control": flags.insert(.maskControl)
            case "alt", "option": flags.insert(.maskAlternate)
            case "shift": flags.insert(.maskShift)
            default: try fail("UNKNOWN_MODIFIER", "Unknown modifier: \(modifier)")
            }
        }
        let target = try app(args); try await activate(target); try ensureFocus(target.processIdentifier)
        try keyboard(key, flags: flags)
        return ["performed": "key", "key": name]
    }
    func scroll(_ args: Object) async throws -> Object {
        try requireInput()
        let target = try app(args); try await activate(target)
        if args["x"] != nil {
            let (p, pid) = try targetPoint(args)
            if let pid, pid != target.processIdentifier { try fail("APP_MISMATCH", "Screenshot belongs to a different app than the scroll target.") }
            try mouseEvent(.mouseMoved, p)
        }
        try ensureFocus(target.processIdentifier)
        let dy = min(max(args["dy"] as? Int ?? 0, -10000), 10000), dx = min(max(args["dx"] as? Int ?? 0, -10000), 10000)
        guard let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2, wheel1: Int32(-dy), wheel2: Int32(-dx), wheel3: 0) else { try fail("EVENT_FAILED", "Could not create scroll event.") }
        event.post(tap: .cghidEventTap)
        return ["performed": "scroll", "dx": dx, "dy": dy]
    }
    func drag(_ args: Object) async throws -> Object {
        try requireInput()
        var from = args, to = args
        from["x"] = args["fromX"]; from["y"] = args["fromY"]; to["x"] = args["toX"]; to["y"] = args["toY"]
        let (p, pid) = try targetPoint(from), (q, _) = try targetPoint(to)
        if let pid, let a = NSRunningApplication(processIdentifier: pid) { try await activate(a) }
        _ = try targetPoint(from); _ = try targetPoint(to); try ensureFocus(pid)
        let steps = 30, duration = min(max(args["durationMs"] as? Int ?? 600, 100), 3000)
        var last = p
        try mouseEvent(.leftMouseDown, p)
        defer { try? mouseEvent(.leftMouseUp, last) }
        for i in 1...steps {
            try ensureFocus(pid)
            last = CGPoint(x: p.x + (q.x-p.x)*Double(i)/Double(steps), y: p.y + (q.y-p.y)*Double(i)/Double(steps))
            try mouseEvent(.leftMouseDragged, last)
            try await Task.sleep(nanoseconds: UInt64(duration*1_000_000/steps))
        }
        return ["performed": "drag"]
    }
    func setValue(_ args: Object) throws -> Object {
        try requireInput()
        let (el, _) = try element(args)
        let name = args["attribute"] as? String ?? kAXValueAttribute
        guard [kAXValueAttribute, kAXFocusedAttribute, kAXSelectedAttribute].contains(name) else { try fail("INVALID_ATTRIBUTE", "Supported attributes: AXValue, AXFocused, AXSelected.") }
        guard let value = args["value"], value is String || value is NSNumber else { try fail("INVALID_VALUE", "Value must be a string, number, or boolean.") }
        var settable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(el, name as CFString, &settable)
        guard settable.boolValue else { try fail("NOT_SETTABLE", "This element does not allow setting \(name).") }
        let error = AXUIElementSetAttributeValue(el, name as CFString, value as CFTypeRef)
        guard error == .success else { try fail("AX_ACTION_FAILED", "Set value failed: \(error.rawValue)") }
        return ["performed": "set_value", "attribute": name]
    }
    func window(_ args: Object) async throws -> Object {
        try requireInput()
        let target = try app(args)
        if args["action"] as? String == "focus" { try await activate(target); return ["performed": "focus"] }
        let root = AXUIElementCreateApplication(target.processIdentifier)
        let windows = attr(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
        let index = args["index"] as? Int ?? 0
        guard windows.indices.contains(index) else { try fail("WINDOW_NOT_FOUND", "Invalid accessibility window index.") }
        let w = windows[index], action = args["action"] as? String ?? "raise"
        func set(_ key: String, _ value: CFTypeRef) throws {
            let e = AXUIElementSetAttributeValue(w, key as CFString, value)
            if e != .success { try fail("WINDOW_ACTION_FAILED", "\(key) failed: \(e.rawValue)") }
        }
        switch action {
        case "raise":
            try await activate(target)
            if AXUIElementPerformAction(w, kAXRaiseAction as CFString) != .success { try fail("WINDOW_ACTION_FAILED", "Could not raise window.") }
        case "minimize": try set(kAXMinimizedAttribute, kCFBooleanTrue)
        case "restore": try set(kAXMinimizedAttribute, kCFBooleanFalse)
        case "resize":
            guard let width = args["width"] as? Double, let height = args["height"] as? Double, width.isFinite, height.isFinite, width>=100, height>=100 else { try fail("INVALID_SIZE", "Window width and height must be finite and at least 100 points.") }
            var s = CGSize(width: width, height: height); try set(kAXSizeAttribute, AXValueCreate(.cgSize, &s)!)
        case "move":
            guard let x = args["x"] as? Double, let y = args["y"] as? Double, x.isFinite, y.isFinite else { try fail("INVALID_COORDINATES", "Provide finite x and y.") }
            var p = CGPoint(x: x, y: y); try set(kAXPositionAttribute, AXValueCreate(.cgPoint, &p)!)
        case "close":
            guard let button = attr(w, kAXCloseButtonAttribute), CFGetTypeID(button) == AXUIElementGetTypeID(), AXUIElementPerformAction(button as! AXUIElement, kAXPressAction as CFString) == .success else { try fail("WINDOW_ACTION_FAILED", "Could not close window.") }
        default: try fail("UNKNOWN_ACTION", "Unknown window action.")
        }
        return ["performed": action, "windowIndex": index]
    }
    func handle(_ method: String, _ args: Object) async throws -> Object {
        let inputMethods: Set<String> = ["click", "type_text", "press_key", "scroll", "drag", "move_pointer", "set_value", "window", "clipboard_write", "launch", "open_url"]
        var lockFD: Int32 = -1
        if inputMethods.contains(method) {
            lockFD = Darwin.open(root.appendingPathComponent("runtime/input.lock").path, O_CREAT | O_RDWR, 0o600)
            guard lockFD >= 0 else { try fail("LOCK_FAILED", "Cannot open the input lock file.") }
            if flock(lockFD, LOCK_EX | LOCK_NB) != 0 {
                Darwin.close(lockFD); try fail("INPUT_BUSY", "Another Chatuse process is sending input. Retry after observing current state.")
            }
        }
        defer { if lockFD >= 0 { flock(lockFD, LOCK_UN); Darwin.close(lockFD) } }
        switch method {
        case "status": return status()
        case "request_permissions":
            if args["accessibility"] as? Bool ?? true { _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary) }
            if args["screenRecording"] as? Bool ?? true { _ = CGRequestScreenCaptureAccess() }
            return status()
        case "list_apps": return listApps(args)
        case "windows": return try listWindows(args)
        case "displays":
            var count: UInt32 = 0; CGGetActiveDisplayList(0, nil, &count)
            var ids = [CGDirectDisplayID](repeating: 0, count: Int(count)); CGGetActiveDisplayList(count, &ids, &count)
            return ["displays": ids.map { ["id": Int($0), "main": $0 == CGMainDisplayID(), "bounds": rectJSON(CGDisplayBounds($0)), "pixelsWide": CGDisplayPixelsWide($0), "pixelsHigh": CGDisplayPixelsHigh($0)] as Object }]
        case "inspect": return try inspect(args)
        case "screenshot": return try await screenshot(args)
        case "click": return try await click(args)
        case "type_text": return try await type(args)
        case "press_key": return try await pressKey(args)
        case "scroll": return try await scroll(args)
        case "drag": return try await drag(args)
        case "move_pointer":
            try requireInput(); let (p, _) = try targetPoint(args); try mouseEvent(.mouseMoved, p); return ["performed": "move_pointer"]
        case "set_value": return try setValue(args)
        case "window": return try await window(args)
        case "clipboard_read":
            try requireSession(); return ["text": String((NSPasteboard.general.string(forType: .string) ?? "").prefix(50000))]
        case "clipboard_write":
            try requireInput(); guard let text = args["text"] as? String else { try fail("INVALID_TEXT", "Text is required.") }
            NSPasteboard.general.clearContents()
            guard NSPasteboard.general.setString(text, forType: .string) else { try fail("CLIPBOARD_FAILED", "Could not write clipboard.") }
            return ["writtenCharacters": text.count]
        case "launch":
            try requireInput()
            guard let target = args["app"] as? String else { try fail("APP_REQUIRED", "Provide an absolute .app path or bundle ID.") }
            let url = target.hasPrefix("/") ? URL(fileURLWithPath: target) : NSWorkspace.shared.urlForApplication(withBundleIdentifier: target)
            guard let url, url.pathExtension == "app", FileManager.default.fileExists(atPath: url.path) else { try fail("APP_NOT_FOUND", "No application found at the given path or bundle ID.") }
            let config = NSWorkspace.OpenConfiguration(); config.activates = args["activate"] as? Bool ?? true
            let a = try await NSWorkspace.shared.openApplication(at: url, configuration: config); return info(a)
        case "open_url":
            try requireInput()
            guard let value = args["url"] as? String, let url = URL(string: value), ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { try fail("INVALID_URL", "Use an http or https URL.") }
            guard NSWorkspace.shared.open(url) else { try fail("OPEN_FAILED", "Could not open URL.") }; return ["opened": true]
        default: try fail("UNKNOWN_METHOD", "Unknown native command: \(method)")
        }
    }
}

@MainActor final class SetupWindow: NSObject {
    let driver: Driver
    let window: NSWindow
    let summary = NSTextField(wrappingLabelWithString: "")
    let pauseButton = NSButton(title: "Pause input", target: nil, action: nil)
    init(driver: Driver) {
        self.driver = driver
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 590, height: 395), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        super.init()
        window.title = "Chatuse Setup"; window.center(); window.isReleasedWhenClosed = false
        let title = NSTextField(labelWithString: "Chatuse is ready for your Mac")
        title.font = .boldSystemFont(ofSize: 22); title.frame = NSRect(x: 24, y: 338, width: 540, height: 30)
        let detail = NSTextField(wrappingLabelWithString: "All apps are allowed. macOS requires Accessibility and Screen Recording access before Chatuse can control and capture apps. Add Chatuse using the + button in each settings pane if it is not listed.")
        detail.frame = NSRect(x: 24, y: 257, width: 540, height: 72)
        summary.frame = NSRect(x: 24, y: 151, width: 540, height: 90)
        let accessibility = NSButton(title: "Open Accessibility", target: self, action: #selector(openAccessibility))
        accessibility.frame = NSRect(x: 24, y: 102, width: 185, height: 35)
        let recording = NSButton(title: "Open Screen Recording", target: self, action: #selector(openRecording))
        recording.frame = NSRect(x: 215, y: 102, width: 210, height: 35)
        let refresh = NSButton(title: "Refresh", target: self, action: #selector(refreshStatus))
        refresh.frame = NSRect(x: 441, y: 102, width: 120, height: 35)
        let reveal = NSButton(title: "Show Chatuse in Finder", target: self, action: #selector(revealApp))
        reveal.frame = NSRect(x: 24, y: 51, width: 210, height: 35)
        pauseButton.target = self; pauseButton.action = #selector(togglePause); pauseButton.frame = NSRect(x: 355, y: 51, width: 206, height: 35)
        let note = NSTextField(labelWithString: "After granting access, restart the MCP connection or start a new Codex task.")
        note.font = .systemFont(ofSize: 11); note.frame = NSRect(x: 24, y: 15, width: 550, height: 22)
        for view in [title, detail, summary, accessibility, recording, refresh, reveal, pauseButton, note] { window.contentView?.addSubview(view) }
        refreshStatus(); window.makeKeyAndOrderFront(nil); NSApplication.shared.activate(ignoringOtherApps: true)
    }
    @objc func refreshStatus() {
        let s = driver.status()
        summary.stringValue = "Accessibility: \(s["accessibility"] as? Bool == true ? "Granted" : "Required")\nScreen Recording: \(s["screenRecording"] as? Bool == true ? "Granted" : "Required")\nInput: \(driver.stopped ? "Paused" : "Enabled for all apps")\nHelper: \(Bundle.main.bundlePath)"
        pauseButton.title = driver.stopped ? "Resume input" : "Pause input"
    }
    @objc func openAccessibility() { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!) }
    @objc func openRecording() { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!) }
    @objc func revealApp() { NSWorkspace.shared.activateFileViewerSelecting([Bundle.main.bundleURL]) }
    @objc func togglePause() {
        let path = driver.root.appendingPathComponent("STOP")
        do { if driver.stopped { try FileManager.default.removeItem(at: path) } else { try Data().write(to: path) }; refreshStatus() }
        catch { summary.stringValue = "Could not change pause state: \(error.localizedDescription)" }
    }
}

@main struct NativeMain {
    @MainActor static func main() {
        let app = NSApplication.shared; app.setActivationPolicy(.accessory)
        let defaultRoot = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent().path
        let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["CHATUSE_ROOT"] ?? defaultRoot)
        let driver = Driver(root: root)
        if CommandLine.arguments.contains("--setup") {
            let window = SetupWindow(driver: driver)
            withExtendedLifetime(window) { app.run() }
            return
        }
        signal(SIGTERM, SIG_IGN); signal(SIGINT, SIG_IGN)
        let terminate = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        let cleanup = { driver.releaseHeldInput(); NSApplication.shared.terminate(nil) }
        terminate.setEventHandler(handler: cleanup); interrupt.setEventHandler(handler: cleanup)
        terminate.resume(); interrupt.resume()
        DispatchQueue.global().async {
            while let line = readLine() {
                let semaphore = DispatchSemaphore(value: 0)
                DispatchQueue.main.async {
                    Task { @MainActor in
                        var id: Any = NSNull(), response: Object
                        do {
                            guard line.utf8.count <= 1_000_000, let data = line.data(using: .utf8), let request = try JSONSerialization.jsonObject(with: data) as? Object, let method = request["method"] as? String else { try fail("INVALID_REQUEST", "Expected a JSON object with method and params.") }
                            id = request["id"] ?? NSNull()
                            let result = try await driver.handle(method, request["params"] as? Object ?? [:])
                            response = ["id": id, "result": result]
                        } catch {
                            let e = error as? ChatuseError
                            response = ["id": id, "error": ["code": e?.code ?? "NATIVE_ERROR", "message": e?.description ?? String(describing: error)]]
                        }
                        if let data = try? JSONSerialization.data(withJSONObject: response, options: [.sortedKeys]) {
                            FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
                        }
                        semaphore.signal()
                    }
                }
                semaphore.wait()
            }
            DispatchQueue.main.async { NSApplication.shared.terminate(nil) }
        }
        withExtendedLifetime((terminate, interrupt)) { app.run() }
    }
}
