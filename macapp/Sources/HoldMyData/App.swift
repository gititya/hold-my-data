import AppKit
import SwiftUI

/// Keeps the app alive when its window closes, and drops the Dock icon so only the menu bar
/// icon is left. Opening the window from the menu brings the Dock icon back.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NotificationCenter.default.addObserver(
            forName: NSWindow.willCloseNotification, object: nil, queue: .main
        ) { _ in
            DispatchQueue.main.async {
                let open = NSApp.windows.contains { $0.isVisible && $0.canBecomeMain && !($0 is NSPanel) }
                if !open { NSApp.setActivationPolicy(.accessory) }
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

@main
struct HoldMyDataApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel()

    var body: some Scene {
        Window("Hold My Data", id: "main") {
            RootView()
                .environment(model)
                .tint(Color.hmdAccent)
                .onOpenURL { url in Task { await model.add([url]) } }
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Check Engine Version…") { checkVersion() }
            }
            CommandGroup(replacing: .newItem) {
                Button("Open…") { model.chooseFiles() }.keyboardShortcut("o")
            }
        }
        Settings {
            SettingsView().environment(model).tint(Color.hmdAccent)
        }
        MenuBarExtra("Hold My Data", systemImage: "text.redaction") {
            MenuBarMenu()
        }
        .menuBarExtraStyle(.menu)
    }

    private func checkVersion() {
        Task {
            let alert = NSAlert()
            alert.messageText = "Reading engine"
            if let version = await model.engineVersion() {
                alert.informativeText = "Version \(version). Redaction runs on this Mac with no internet."
            } else {
                alert.informativeText = "The reading engine is not installed yet."
            }
            alert.runModal()
        }
    }
}

struct MenuBarMenu: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Hold My Data") {
            NSApp.setActivationPolicy(.regular)
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        Divider()
        Button("Quit Hold My Data") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}
