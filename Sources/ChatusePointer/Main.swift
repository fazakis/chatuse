import AppKit
import CoreGraphics
import ChatuseCore

final class PointerPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect { frameRect }
}

final class PointerView: NSView {
    override var isFlipped: Bool { true }
    var pulse: Double = 1
    var pressed = false
    var label = "Chatuse"
    let tip = CGPoint(x: 60, y: 60)
    let blue = NSColor(calibratedRed: 0.10, green: 0.48, blue: 1.0, alpha: 1)
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.setFill(); dirtyRect.fill(using: .copy)
        if pulse < 1 || pressed {
            let radius = pressed ? 17.0 : 12+34*pulse
            let ring = NSBezierPath(ovalIn: NSRect(x: tip.x-radius, y: tip.y-radius, width: radius*2, height: radius*2))
            blue.withAlphaComponent(pressed ? 0.20 : 0.15*(1-pulse)).setFill(); ring.fill()
            blue.withAlphaComponent(pressed ? 1 : 1-pulse).setStroke(); ring.lineWidth = 3; ring.stroke()
        }
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow(); shadow.shadowColor = NSColor.black.withAlphaComponent(0.3); shadow.shadowBlurRadius = 5; shadow.shadowOffset = NSSize(width: 0, height: 1); shadow.set()
        let arrow = NSBezierPath()
        arrow.move(to: tip)
        for (x,y) in [(2.0,35.0),(11.0,27.0),(18.0,41.0),(25.0,37.0),(18.0,24.0),(30.0,23.0)] { arrow.line(to: CGPoint(x: tip.x+x, y: tip.y+y)) }
        arrow.close(); NSColor.white.setStroke(); arrow.lineWidth = 4; arrow.lineJoinStyle = .round; arrow.stroke(); blue.setFill(); arrow.fill()
        NSGraphicsContext.restoreGraphicsState()
        let attrs: [NSAttributedString.Key:Any] = [.font:NSFont.systemFont(ofSize:11, weight:.semibold),.foregroundColor:NSColor.white]
        let text = NSString(string: label), width = min(text.size(withAttributes:attrs).width+18, 138)
        let pill = NSRect(x: 94,y: 88,width:width,height:23)
        blue.setFill(); NSBezierPath(roundedRect:pill,xRadius:11,yRadius:11).fill()
        text.draw(at:CGPoint(x:pill.minX+9,y:pill.minY+4),withAttributes:attrs)
    }
}

@MainActor final class VisualPointer {
    let panel: PointerPanel
    let view = PointerView(frame: NSRect(x:0,y:0,width:250,height:140))
    var position: CGPoint?
    var lastActivity = Date.distantPast
    var pulseStarted = Date.distantPast
    var paused = false
    var timer: Timer?
    let root: URL
    init(root: URL) {
        self.root = root
        panel = PointerPanel(contentRect:view.frame,styleMask:[.borderless,.nonactivatingPanel],backing:.buffered,defer:false)
        panel.title = "Chatuse Visual Pointer"
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = false
        panel.ignoresMouseEvents = true; panel.hidesOnDeactivate = false
        panel.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.helpWindow)))
        panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary,.ignoresCycle,.stationary]
        panel.animationBehavior = .none; panel.isReleasedWhenClosed = false
        panel.contentView = view
        timer = Timer.scheduledTimer(withTimeInterval:1.0/30.0,repeats:true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }
    func permitted() -> Bool {
        let session = CGSessionCopyCurrentDictionary() as? [String:Any] ?? [:]
        return session["CGSSessionScreenIsLocked"] as? Bool != true && !FileManager.default.fileExists(atPath:root.appendingPathComponent("STOP").path) && !FileManager.default.fileExists(atPath:root.appendingPathComponent("runtime/pointer-disabled").path)
    }
    func captureActive() -> Bool {
        let dir=root.appendingPathComponent("runtime/pointer-captures")
        for name in (try? FileManager.default.contentsOfDirectory(atPath:dir.path)) ?? [] {
            guard let part=name.split(separator:"-").first,let pid=Int32(part) else {continue}
            if kill(pid,0)==0 || errno==EPERM {return true}
            try? FileManager.default.removeItem(at:dir.appendingPathComponent(name))
        }
        return false
    }
    func tick() {
        guard permitted(), !paused, !captureActive() else { panel.orderOut(nil); return }
        let age = Date().timeIntervalSince(lastActivity)
        if age > 3.2 { panel.orderOut(nil); return }
        panel.alphaValue = age < 2.5 ? 1 : max(0,(3.2-age)/0.7)
        view.pulse = min(1,Date().timeIntervalSince(pulseStarted)/0.7)
        view.needsDisplay = true
    }
    func place(_ point: CGPoint) {
        position = point; lastActivity = Date()
        let origin = overlayOrigin(point:point,panelSize:panel.frame.size,hotspot:view.tip,primaryHeight:CGDisplayBounds(CGMainDisplayID()).height)
        panel.setFrameOrigin(origin); panel.alphaValue = 1
        if !paused && permitted() && !captureActive() { panel.orderFrontRegardless() }
        view.needsDisplay = true; view.displayIfNeeded()
    }
    func animate(to:CGPoint, durationMs:Int, linear:Bool=false) async throws {
        let from = position ?? CGEvent(source:nil)?.location ?? to
        let count = max(1,min(durationMs,3000)/16)
        for i in 0...count {
            guard permitted() else { panel.orderOut(nil); return }
            let t=Double(i)/Double(count)
            place(linear ? CGPoint(x:from.x+(to.x-from.x)*t,y:from.y+(to.y-from.y)*t) : easedPoint(from:from,to:to,progress:t))
            if i<count { try await Task.sleep(nanoseconds:UInt64(max(0,durationMs))*1_000_000/UInt64(count)) }
        }
    }
    func point(_ args:[String:Any], x:String="x", y:String="y") throws -> CGPoint {
        guard let px=args[x] as? Double,let py=args[y] as? Double,px.isFinite,py.isFinite else { throw ChatuseError("INVALID_COORDINATES","Expected finite pointer coordinates.") }
        return CGPoint(x:px,y:py)
    }
    func handle(_ method:String,_ args:[String:Any]) async throws -> [String:Any] {
        switch method {
        case "move": paused=false;view.label = "Chatuse"; try await animate(to:point(args),durationMs:args["durationMs"] as? Int ?? 320)
        case "pulse":
            if args["x"] != nil { place(try point(args)) }
            view.label = args["label"] as? String ?? "Chatuse · click"; pulseStarted = Date(); lastActivity = Date(); tick()
        case "drag":
            view.label = "Chatuse · drag";view.pressed = true
            defer { view.pressed = false; pulseStarted = Date(); lastActivity = Date() }
            try await animate(to:point(args),durationMs:args["durationMs"] as? Int ?? 600,linear:true)
        case "hide": paused = true; panel.orderOut(nil)
        case "restore": paused = false; if Date().timeIntervalSince(lastActivity)<3.2,permitted(){panel.orderFrontRegardless()};tick()
        case "state": break
        default: throw ChatuseError("UNKNOWN_METHOD","Unknown visual pointer command.")
        }
        return ["visible":panel.isVisible,"windowId":panel.windowNumber,"ignoresMouseEvents":panel.ignoresMouseEvents,"canBecomeKey":panel.canBecomeKey,"x":position?.x ?? 0,"y":position?.y ?? 0]
    }
}

@main struct PointerMain {
    @MainActor static func main() {
        let app = NSApplication.shared; app.setActivationPolicy(.accessory)
        let root = URL(fileURLWithPath:ProcessInfo.processInfo.environment["CHATUSE_ROOT"] ?? FileManager.default.currentDirectoryPath)
        let pointer = VisualPointer(root:root)
        DispatchQueue.global().async {
            while let line=readLine() {
                let done=DispatchSemaphore(value:0)
                DispatchQueue.main.async {
                    Task { @MainActor in
                        var id:Any=NSNull(),response:[String:Any]
                        do {
                            guard let data=line.data(using:.utf8),let req=try JSONSerialization.jsonObject(with:data) as? [String:Any],let method=req["method"] as? String else {throw ChatuseError("INVALID_REQUEST","Expected JSON request.")}
                            id=req["id"] ?? NSNull()
                            response=["id":id,"result":try await pointer.handle(method,req["params"] as? [String:Any] ?? [:])]
                        } catch {response=["id":id,"error":["code":"POINTER_ERROR","message":String(describing:error)]]}
                        if let data=try? JSONSerialization.data(withJSONObject:response){FileHandle.standardOutput.write(data);FileHandle.standardOutput.write(Data([10]))}
                        done.signal()
                    }
                }
                done.wait()
            }
            DispatchQueue.main.async {NSApplication.shared.terminate(nil)}
        }
        app.run()
    }
}
