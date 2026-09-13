import XCTest
import CoreGraphics
@testable import ChatuseCore
final class CoreTests: XCTestCase {
    func testOverlayHotspotOnPrimaryAndNegativeDisplayCoordinates() {
        let size=CGSize(width:250,height:140),hotspot=CGPoint(x:60,y:60)
        XCTAssertEqual(overlayOrigin(point:CGPoint(x:300,y:200),panelSize:size,hotspot:hotspot,primaryHeight:1440),CGPoint(x:240,y:1160))
        XCTAssertEqual(overlayOrigin(point:CGPoint(x:-500,y:-100),panelSize:size,hotspot:hotspot,primaryHeight:1440),CGPoint(x:-560,y:1460))
    }
    func testPointerAnimationEndpointsAndClamping() {
        let a=CGPoint(x:-20,y:50),b=CGPoint(x:200,y:300)
        XCTAssertEqual(easedPoint(from:a,to:b,progress:-1),a)
        XCTAssertEqual(easedPoint(from:a,to:b,progress:1.5),b)
        XCTAssertEqual(easedPoint(from:a,to:b,progress:0.5),CGPoint(x:90,y:175))
    }
    func testRetinaCoordinatesAndNegativeDisplayOrigin() throws {
        let p = try screenPoint(x: 400, y: 200, pixelWidth: 800, pixelHeight: 600,
                                frame: CGRect(x: -1600, y: -200, width: 1600, height: 1200))
        XCTAssertEqual(p, CGPoint(x: -800, y: 200))
    }
    func testRejectInvalidCoordinates() {
        for v in [-1.0, 100, Double.nan, Double.infinity] {
            XCTAssertThrowsError(try checkedPoint(x: v, y: 0, width: 100, height: 100))
        }
    }
    func testSnapshotExpiry() throws {
        let c = SnapshotClock(created: Date(timeIntervalSince1970: 0), lifetime: 120)
        try c.validate(now: Date(timeIntervalSince1970: 120))
        XCTAssertThrowsError(try c.validate(now: Date(timeIntervalSince1970: 121)))
    }
    func testUnicodeChunksNeverSplitSurrogates() {
        let text = "a😀é你好🇬🇷" + String(repeating: "😀", count: 30)
        let chunks = unicodeChunks(text)
        XCTAssertTrue(chunks.allSatisfy { $0.count <= 20 })
        XCTAssertEqual(chunks.map { String(decoding: $0, as: UTF16.self) }.joined(), text)
    }
}
