import AppKit
import ChatuseCore

struct CapturedScreenshot {
    let image: CGImage
    let frame: CGRect
    let pid: pid_t?
    let windowID: CGWindowID?
}

extension Driver {
    /// Public Core Graphics capture avoids Ventura's long-lived streaming
    /// lifecycle. macOS 14+ continues to use SCScreenshotManager instead.
    @available(macOS, introduced: 13.0, deprecated: 14.0)
    func venturaScreenshot(_ args: Object) throws -> CapturedScreenshot {
        let frame: CGRect, pid: pid_t?, windowID: CGWindowID?, source: CGImage?
        if args["windowId"] != nil || args["app"] != nil || args["pid"] != nil {
            let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [Object] ?? []
            let selected: Object?
            if let id = args["windowId"] as? Int {
                selected = windows.first { $0[kCGWindowNumber as String] as? Int == id }
            } else {
                let target = try app(args)
                selected = windows.first {
                    guard $0[kCGWindowOwnerPID as String] as? Int == Int(target.processIdentifier),
                          $0[kCGWindowLayer as String] as? Int == 0,
                          let bounds = $0[kCGWindowBounds as String] as? Object,
                          let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                    return rect.width > 1 && rect.height > 1
                }
            }
            guard let window = selected,
                  let id = window[kCGWindowNumber as String] as? UInt32,
                  let bounds = window[kCGWindowBounds as String] as? Object,
                  let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary) else {
                try fail("WINDOW_NOT_FOUND", "The app or selected window is no longer visible.")
            }
            frame = rect; windowID = id
            pid = (window[kCGWindowOwnerPID as String] as? Int32)
            // Excluding the shadow keeps screenshot pixels aligned with the
            // same window bounds used to validate subsequent coordinate input.
            source = CGWindowListCreateImage(.null, .optionIncludingWindow, id, [.boundsIgnoreFraming, .bestResolution])
        } else {
            var count: UInt32 = 0
            CGGetActiveDisplayList(0, nil, &count)
            var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
            CGGetActiveDisplayList(count, &ids, &count)
            let requested = args["displayId"] as? Int ?? Int(CGMainDisplayID())
            guard let id = ids.first(where: { Int($0) == requested }) else {
                try fail("DISPLAY_NOT_FOUND", "Use displays to select an available display.")
            }
            frame = CGDisplayBounds(id); pid = nil; windowID = nil
            source = CGDisplayCreateImage(id)
        }
        guard frame.width > 0, frame.height > 0 else { try fail("EMPTY_CAPTURE", "The capture region is empty.") }
        guard let source else { try fail("CAPTURE_FAILED", "Could not capture the selected window or display. Observe again and check Screen Recording permission.") }
        let maxWidth = min(max(args["maxWidth"] as? Int ?? 1440, 320), 3840)
        let scale = min(Double(maxWidth) / frame.width, 2)
        let width = max(1, Int(frame.width * scale)), height = max(1, Int(frame.height * scale))
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            try fail("CAPTURE_FAILED", "Could not allocate the screenshot image.")
        }
        context.interpolationQuality = .high
        context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
        // Core Graphics screenshots omit the cursor. Composite the current
        // system cursor only when requested, using its real hotspot and scale.
        if args["showCursor"] as? Bool == true,
           let position = CGEvent(source: nil)?.location, frame.contains(position),
           let cursor = NSCursor.currentSystem,
           let cursorImage = cursor.image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
            let sx = Double(width) / frame.width, sy = Double(height) / frame.height
            let rect = CGRect(x: (position.x - cursor.hotSpot.x - frame.minX) * sx,
                              y: Double(height) - (position.y - cursor.hotSpot.y - frame.minY + cursor.image.size.height) * sy,
                              width: cursor.image.size.width * sx, height: cursor.image.size.height * sy)
            context.draw(cursorImage, in: rect)
        }
        guard let image = context.makeImage() else { try fail("CAPTURE_FAILED", "Could not render screenshot.") }
        return CapturedScreenshot(image: image, frame: frame, pid: pid, windowID: windowID)
    }
}
