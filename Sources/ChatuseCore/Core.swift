import Foundation
import CoreGraphics

public struct ChatuseError: Error, CustomStringConvertible {
    public let code: String
    public let description: String
    public init(_ code: String, _ message: String) { self.code = code; self.description = message }
}

public struct SnapshotClock {
    public let created: Date
    public let lifetime: TimeInterval
    public init(created: Date = Date(), lifetime: TimeInterval = 120) { self.created = created; self.lifetime = lifetime }
    public func validate(now: Date = Date()) throws {
        guard now.timeIntervalSince(created) <= lifetime else {
            throw ChatuseError("STALE_SNAPSHOT", "Inspect the app again before using this element or screenshot.")
        }
    }
}

public func checkedPoint(x: Double, y: Double, width: Double, height: Double) throws -> CGPoint {
    guard x.isFinite, y.isFinite, width > 0, height > 0, x >= 0, y >= 0, x < width, y < height else {
        throw ChatuseError("INVALID_COORDINATES", "Coordinates must be inside the referenced screenshot or display.")
    }
    return CGPoint(x: x, y: y)
}

public func screenPoint(x: Double, y: Double, pixelWidth: Double, pixelHeight: Double, frame: CGRect) throws -> CGPoint {
    let p = try checkedPoint(x: x, y: y, width: pixelWidth, height: pixelHeight)
    return CGPoint(x: frame.minX + p.x * frame.width / pixelWidth, y: frame.minY + p.y * frame.height / pixelHeight)
}

public func unicodeChunks(_ text: String, limit: Int = 20) -> [[UInt16]] {
    var result: [[UInt16]] = [], chunk: [UInt16] = []
    for scalar in text.unicodeScalars {
        let units = Array(String(scalar).utf16)
        if chunk.count + units.count > limit { result.append(chunk); chunk = [] }
        chunk.append(contentsOf: units)
    }
    if !chunk.isEmpty { result.append(chunk) }
    return result
}

/// Map CG's top-left global coordinates into AppKit's bottom-left space.
public func overlayOrigin(point: CGPoint, panelSize: CGSize, hotspot: CGPoint, primaryHeight: CGFloat) -> CGPoint {
    CGPoint(x: point.x - hotspot.x, y: primaryHeight - point.y - (panelSize.height - hotspot.y))
}

public func easedPoint(from: CGPoint, to: CGPoint, progress: Double) -> CGPoint {
    let t = min(max(progress, 0), 1), ease = t*t*(3-2*t)
    return CGPoint(x: from.x+(to.x-from.x)*ease, y: from.y+(to.y-from.y)*ease)
}
