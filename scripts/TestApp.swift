import AppKit

final class Fixture: NSObject, NSApplicationDelegate, NSTextFieldDelegate {
    var window: NSWindow!
    let field = NSTextField(string: "")
    let label = NSTextField(labelWithString: "Ready")
    var clicks = 0
    let output = ProcessInfo.processInfo.environment["CHATUSE_FIXTURE_OUTPUT"] ?? "/tmp/chatuse-fixture-events.json"
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Standard responder-chain editing shortcuts require a main menu.
        let menu = NSMenu()
        let appItem = NSMenuItem(); let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Chatuse Fixture", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu; menu.addItem(appItem)
        let editItem = NSMenuItem(); editItem.title = "Edit"
        let editMenu = NSMenu(title: "Edit")
        for (title, action, key) in [("Select All", #selector(NSText.selectAll(_:)), "a"), ("Copy", #selector(NSText.copy(_:)), "c"), ("Paste", #selector(NSText.paste(_:)), "v")] {
            editMenu.addItem(withTitle: title, action: action, keyEquivalent: key)
        }
        editItem.submenu = editMenu; menu.addItem(editItem); NSApplication.shared.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 120,y:120,width:560,height:360),styleMask:[.titled,.closable,.resizable,.miniaturizable],backing:.buffered,defer:false)
        window.title="Chatuse Test Fixture"; window.identifier=NSUserInterfaceItemIdentifier("chatuse-fixture")
        let title=NSTextField(labelWithString:"Chatuse input test");title.frame=NSRect(x:24,y:300,width:500,height:26);title.font = .boldSystemFont(ofSize:20)
        field.frame=NSRect(x:24,y:240,width:500,height:30);field.placeholderString="Type here";field.setAccessibilityIdentifier("chatuse-text");field.delegate=self
        let button=NSButton(title:"Increment",target:self,action:#selector(increment));button.frame=NSRect(x:24,y:175,width:150,height:40);button.setAccessibilityIdentifier("chatuse-increment")
        label.frame=NSRect(x:24,y:120,width:500,height:30);label.setAccessibilityIdentifier("chatuse-result")
        let scroll=NSScrollView(frame:NSRect(x:24,y:20,width:500,height:80));scroll.hasVerticalScroller=true
        let text=NSTextView(frame:NSRect(x:0,y:0,width:480,height:600));text.string=(1...30).map{"Scroll line \($0)"}.joined(separator:"\n");text.isEditable=false;scroll.documentView=text
        for view in [title,field,button,label,scroll] { window.contentView?.addSubview(view) }
        window.makeKeyAndOrderFront(nil);NSApplication.shared.activate(ignoringOtherApps:true);save()
    }
    @objc func increment(){clicks+=1;label.stringValue="Clicks: \(clicks)";save()}
    func controlTextDidChange(_ obj: Notification){save()}
    func save(){if let data=try? JSONSerialization.data(withJSONObject:["clicks":clicks,"text":field.stringValue]){try? data.write(to:URL(fileURLWithPath:output),options:.atomic)}}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool{true}
}
let app=NSApplication.shared
let delegate=Fixture();app.delegate=delegate;app.setActivationPolicy(.regular);app.run()
