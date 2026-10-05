import AppKit
import QuickLook
import SwiftUI

extension Color {
    /// The only custom color: the terracotta accent, per the design tokens.
    static let hmdAccent = Color(nsColor: NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            ? NSColor(srgbRed: 0xC9 / 255, green: 0x69 / 255, blue: 0x4B / 255, alpha: 1)
            : NSColor(srgbRed: 0xAD / 255, green: 0x53 / 255, blue: 0x37 / 255, alpha: 1)
    })
}

// MARK: - Root

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Group {
            switch model.engine {
            case .checking: ProgressView().controlSize(.small)
            case .needsSetup: SetupView()
            case .ready: MainView()
            }
        }
        .frame(minWidth: 480, maxWidth: 640, minHeight: 560, maxHeight: 760)
        .containerBackground(.thickMaterial, for: .window)
        .navigationTitle(model.engine == .needsSetup ? "" : "Hold My Data")
        .task { await model.bootstrap() }
        .alert("Hold My Data", isPresented: Binding(
            get: { model.alertMessage != nil }, set: { if !$0 { model.alertMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.alertMessage ?? "") }
    }
}

// MARK: - Setup (first launch)

struct AppMark: View {
    var size: CGFloat = 72
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.225)
            .fill(LinearGradient(colors: [Color(nsColor: .controlBackgroundColor), Color(nsColor: .windowBackgroundColor)],
                                 startPoint: .top, endPoint: .bottom))
            .overlay(RoundedRectangle(cornerRadius: size * 0.225).strokeBorder(.separator, lineWidth: 0.5))
            .overlay {
                VStack(spacing: size * 0.1) {
                    Capsule().fill(.quaternary).frame(width: size * 0.5, height: size * 0.062)
                    RoundedRectangle(cornerRadius: size * 0.038).fill(Color.hmdAccent).frame(height: size * 0.16)
                    Capsule().fill(.quaternary).frame(width: size * 0.38, height: size * 0.062)
                }
                .padding(.horizontal, size * 0.2)
            }
            .frame(width: size, height: size)
            .shadow(color: .black.opacity(0.14), radius: size * 0.06, y: size * 0.04)
            .accessibilityHidden(true)
    }
}

struct SetupView: View {
    @Environment(AppModel.self) private var model
    @State private var showDetails = false

    var body: some View {
        let installer = model.installer
        VStack(spacing: 10) {
            Spacer()
            AppMark()
            Text("Setting up Hold My Data").font(.title3.weight(.semibold)).padding(.top, 6)
            switch installer.phase {
            case .idle, .done:
                Text("This downloads the tools that find personal info (about 1.8 GB). After setup, everything runs on your Mac with no internet.")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 340)
                Button("Set Up") { Task { await model.runSetup() } }
                    .buttonStyle(.borderedProminent).controlSize(.large)
                    .keyboardShortcut(.defaultAction).padding(.top, 10)
            case .running, .failed:
                progress(installer)
            }
            Spacer()
            HStack {
                Spacer()
                if installer.phase == .running {
                    Button("Cancel", role: .cancel) { installer.cancel() }.keyboardShortcut(.cancelAction)
                } else if installer.phase == .failed {
                    Button("Try Again") { Task { await model.runSetup() } }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                }
            }
            .frame(minHeight: 28).padding(.bottom, 20).padding(.horizontal, 20)
        }
        .padding(.horizontal, 48)
    }

    @ViewBuilder private func progress(_ installer: Installer) -> some View {
        let failed = installer.phase == .failed
        VStack(alignment: .leading, spacing: 8) {
            ProgressView(value: installer.fraction).tint(failed ? Color.secondary : Color.hmdAccent)
                .accessibilityLabel("Setup progress")
            if !failed {
                HStack {
                    Text("\(formatBytes(installer.bytes)) of \(formatBytes(installer.expected))")
                    Spacer()
                    Text("Step \(installer.step) of \(installer.stepCount)")
                }
                .font(.subheadline.monospaced()).foregroundStyle(.secondary)
            }
            if installer.stepCount == 2 || failed {
                stepRow(done: installer.step > 1, title: "Installing engine", active: installer.step == 1, failed: failed && installer.step == 1)
                stepRow(done: false, title: "Downloading photo and PDF reading", active: installer.step == 2, failed: failed && installer.step == 2)
            } else {
                stepRow(done: false, title: installer.stepTitle, active: true, failed: false)
            }
            if failed {
                Text(installer.failureMessage).foregroundStyle(.secondary)
                DisclosureGroup("Show Details", isExpanded: $showDetails) {
                    Text(installer.log.joined(separator: "\n"))
                        .font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(8)
                        .background(.quinary, in: RoundedRectangle(cornerRadius: 8))
                }
                .font(.callout)
            }
        }
        .frame(maxWidth: 340).padding(.top, 10)
    }

    private func stepRow(done: Bool, title: String, active: Bool, failed: Bool) -> some View {
        HStack(spacing: 8) {
            if failed {
                Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
            } else if done {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            } else if active {
                ProgressView().controlSize(.small).frame(width: 14)
            } else {
                Image(systemName: "circle").foregroundStyle(.tertiary)
            }
            Text(title).fontWeight(active ? .medium : .regular).foregroundStyle(active || failed ? .primary : .secondary)
        }
        .frame(height: 22)
    }
}

// MARK: - Main window

struct MainView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        VStack(spacing: 12) {
            switch model.phase {
            case .queue where model.entries.isEmpty: DropZone()
            case .queue: QueueHeader(); EntryList(); SettingsCard(); StartBar()
            case .working: BatchHeader(); EntryList(); Spacer(minLength: 0); WorkingBar()
            case .finished: FinishedHeader(); EntryList(); Spacer(minLength: 0); FinishedBar()
            }
            if model.phase == .queue && model.entries.isEmpty { SettingsCard() }
        }
        .padding(.init(top: 4, leading: 20, bottom: 20, trailing: 20))
        .overlay { if model.isTargeted && model.phase != .working { HoverOverlay() } }
        .dropDestination(for: URL.self) { urls, _ in
            Task { await model.add(urls) }
            return true
        } isTargeted: { model.isTargeted = $0 }
        .quickLookPreview($model.previewURL)
        .sheet(item: Binding(get: { model.downloadFromSettings ? nil : model.downloadRequest },
                             set: { model.downloadRequest = $0 })) { DownloadSheet(kind: $0) }
        .sheet(item: $model.termsTarget) { TermsSheet(file: $0) }
        .task { if model.engine == .ready, model.status.ocrEnglish == false { await model.refreshStatus() } }
    }
}

struct DropZone: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "arrow.down.to.line").font(.system(size: 34, weight: .light)).foregroundStyle(.secondary)
            Text("Drop files here").font(.title2.weight(.semibold)).padding(.top, 4)
            Button("Choose Files…") { model.chooseFiles() }.buttonStyle(.bordered).controlSize(.small)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.quinary, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14)
            .strokeBorder(.separator, style: StrokeStyle(lineWidth: 1.5, dash: [6, 4])))
        .accessibilityElement(children: .contain)
    }
}

struct HoverOverlay: View {
    var body: some View {
        RoundedRectangle(cornerRadius: 14)
            .fill(Color.hmdAccent.opacity(0.10))
            .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.hmdAccent, lineWidth: 2))
            .overlay {
                VStack(spacing: 12) {
                    Image(systemName: "arrow.down.to.line").font(.system(size: 34, weight: .light))
                    Text("Drop to add files").font(.title2.weight(.semibold))
                }
                .foregroundStyle(Color.hmdAccent)
            }
            .padding(.init(top: 4, leading: 20, bottom: 20, trailing: 20))
            .allowsHitTesting(false)
    }
}

struct QueueHeader: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        HStack {
            Text("\(model.allFiles.count) file\(model.allFiles.count == 1 ? "" : "s")").fontWeight(.semibold)
            Spacer()
            Button { model.chooseFiles() } label: { Label("Add Files…", systemImage: "plus") }
                .buttonStyle(.borderless).foregroundStyle(Color.hmdAccent)
        }
        .frame(height: 24)
    }
}

struct BatchHeader: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\(model.batchDone) of \(model.batchTotal) done").fontWeight(.semibold)
            ProgressView(value: Double(model.batchDone), total: Double(max(model.batchTotal, 1)))
                .tint(Color.hmdAccent)
        }
    }
}

struct FinishedHeader: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        let files = model.readyFiles
        let saved = files.filter { if case .done = $0.state { return true } else { return false } }.count
        HStack {
            Text("Finished").fontWeight(.semibold)
            Spacer()
            Text("\(saved) saved · \(files.count - saved) not saved").font(.callout).foregroundStyle(.secondary)
        }
        .frame(height: 24)
    }
}

struct StartBar: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        HStack {
            Text("\(model.readyFiles.count) file\(model.readyFiles.count == 1 ? "" : "s") ready"
                 + (model.skippedCount > 0 ? " · \(model.skippedCount) skipped" : ""))
                .font(.callout).foregroundStyle(.secondary)
            Spacer()
            Button("Hold My Data") { Task { await model.start() } }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .keyboardShortcut(.defaultAction).disabled(!model.canStart)
        }
    }
}

struct WorkingBar: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        HStack {
            Text("Images and PDFs can take a minute a page.").font(.callout).foregroundStyle(.secondary)
            Spacer()
            Button("Cancel", role: .cancel) { model.cancel() }.keyboardShortcut(.cancelAction)
        }
    }
}

struct FinishedBar: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        VStack(spacing: 10) {
            Label("Redaction lowers risk but can miss things. Review files before sharing.", systemImage: "info.circle")
                .font(.subheadline).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            HStack { Spacer(); Button("Clear List") { model.clear() } }
        }
    }
}

// MARK: - Settings card (What to hide, Save to)

struct SettingsCard: View {
    @Environment(AppModel.self) private var model
    @State private var showCategories = false

    var body: some View {
        VStack(spacing: 0) {
            row("What to hold") {
                Button { showCategories.toggle() } label: {
                    HStack(spacing: 6) { Text(model.selectionSummary); Image(systemName: "chevron.up.chevron.down").font(.caption) }
                }
                .buttonStyle(.bordered).controlSize(.small)
                .popover(isPresented: $showCategories, arrowEdge: .top) { CategoryPopover() }
            }
            Divider()
            row("Save to") {
                Menu {
                    Button("Same folder as the original") { model.destination = nil }
                    Divider()
                    Button("Choose Folder…") { model.chooseDestination() }
                } label: { Label(model.destinationName, systemImage: "folder") }
                .menuStyle(.button).buttonStyle(.bordered).controlSize(.small).fixedSize()
            }
        }
        .padding(.horizontal, 12)
        .background(.quinary, in: RoundedRectangle(cornerRadius: 12))
        .disabled(model.phase == .working)
    }

    private func row<C: View>(_ title: String, @ViewBuilder control: () -> C) -> some View {
        HStack { Text(title); Spacer(); control() }.frame(height: 40)
    }
}

struct CategoryPopover: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Category.all) { category in
                Button { model.toggle(category) } label: {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark").frame(width: 14)
                            .opacity(model.selected.contains(category.id) ? 1 : 0)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(category.title)
                            Text(note(for: category)).font(.subheadline).foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 8)
                        if category.needsDownload && !model.namesInstalled {
                            Image(systemName: "arrow.down.circle").foregroundStyle(.secondary)
                        }
                    }
                    .padding(.horizontal, 10).padding(.vertical, 6).contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(category.unavailable)
                .opacity(category.unavailable ? 0.5 : 1)
                .accessibilityAddTraits(model.selected.contains(category.id) ? .isSelected : [])
                if category.id == "uk", model.showUKCaution {
                    Label("UK number matching can match Indian phone numbers by mistake.", systemImage: "exclamationmark.triangle.fill")
                        .font(.subheadline).foregroundStyle(.orange).padding(.horizontal, 10).padding(.bottom, 6)
                }
            }
            Divider().padding(.vertical, 4)
            Text("Your choices are remembered.").font(.subheadline).foregroundStyle(.secondary)
                .padding(.horizontal, 10).padding(.bottom, 6)
        }
        .padding(.vertical, 6).frame(width: 350)
    }

    private func note(for category: Category) -> String {
        category.needsDownload && model.namesInstalled ? "Slower · reads the text for names" : category.note
    }
}

// MARK: - Rows

struct EntryList: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(model.entries) { entry in
                    if entry.isFolder { FolderRow(entry: entry) } else if let file = entry.files.first {
                        FileRow(file: file, entry: entry, indented: false)
                    }
                }
            }
        }
        .frame(maxHeight: model.phase == .queue ? .infinity : nil)
        .background(.quinary, in: RoundedRectangle(cornerRadius: 12))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

struct FolderRow: View {
    @Environment(AppModel.self) private var model
    var entry: Entry

    var body: some View {
        let unreadable = entry.files.filter { !$0.isReadable }.count
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Button { entry.expanded.toggle() } label: {
                    Image(systemName: entry.expanded ? "chevron.down" : "chevron.right").font(.caption).frame(width: 16)
                }
                .buttonStyle(.borderless).accessibilityLabel(entry.expanded ? "Collapse \(entry.url.lastPathComponent)" : "Expand \(entry.url.lastPathComponent)")
                Image(nsImage: NSWorkspace.shared.icon(forFile: entry.url.path)).resizable().frame(width: 30, height: 30)
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.url.lastPathComponent).fontWeight(.medium)
                    Text("Folder · \(entry.files.count) file\(entry.files.count == 1 ? "" : "s")"
                         + (unreadable > 0 ? " · \(unreadable) can't be read" : ""))
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                if model.phase == .queue { RemoveButton(entry: entry) }
            }
            .padding(.init(top: 10, leading: 4, bottom: 10, trailing: 12))
            if entry.expanded || model.phase != .queue {
                let shown = entry.showAll ? entry.files : Array(entry.files.prefix(3))
                ForEach(shown) { FileRow(file: $0, entry: entry, indented: true) }
                if entry.files.count > shown.count {
                    Button("Show all \(entry.files.count)") { entry.showAll = true }
                        .buttonStyle(.borderless).font(.callout).foregroundStyle(Color.hmdAccent)
                        .padding(.leading, 74).padding(.bottom, 9).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }
}

struct RemoveButton: View {
    @Environment(AppModel.self) private var model
    var entry: Entry
    var body: some View {
        Button { model.remove(entry) } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary) }
            .buttonStyle(.borderless).accessibilityLabel("Remove \(entry.url.lastPathComponent)")
    }
}

struct FileRow: View {
    @Environment(AppModel.self) private var model
    var file: FileItem
    var entry: Entry
    var indented: Bool

    var body: some View {
        let refused = { if case .refused = file.state { return true }; if case .exists = file.state { return true }; return false }()
        HStack(alignment: .top, spacing: indented ? 10 : 12) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: file.url.path)).resizable()
                .frame(width: indented ? 14 : 26, height: indented ? 18 : 32).opacity(file.isReadable ? 1 : 0.6)
            VStack(alignment: .leading, spacing: 3) {
                Text(file.name).font(indented ? .callout : .body).fontWeight(indented ? .regular : .medium)
                    .lineLimit(1).truncationMode(.middle).foregroundStyle(file.isReadable ? .primary : .secondary)
                status
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(indented ? .init(top: 5, leading: 50, bottom: 5, trailing: 12) : .init(top: 10, leading: 12, bottom: 10, trailing: 12))
        .background(refused ? Color.red.opacity(0.08) : .clear)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(rowLabel)
        .modifier(DragOut(file: file))
    }

    /// One spoken summary per row; its buttons stay separately reachable.
    private var rowLabel: String {
        switch file.state {
        case .queued: file.isReadable ? "\(file.name), \(file.kind.rawValue)" : "\(file.name), can't be read yet"
        case .reading, .hiding: "\(file.name), working"
        case .done(let counts, _): "\(file.name), saved, \(counts.values.reduce(0, +)) items held"
        case .refused(let message): "\(file.name), not saved. \(message)"
        case .exists: "\(file.name), not saved. A redacted copy already exists."
        case .cancelled: "\(file.name), cancelled"
        }
    }

    @ViewBuilder private var status: some View {
        switch file.state {
        case .queued:
            if !file.isReadable {
                Label("Can't read this file type yet.", systemImage: "nosign").font(.subheadline).foregroundStyle(.secondary)
            } else if model.phase == .working {
                Label("Waiting", systemImage: "clock").font(.subheadline).foregroundStyle(.secondary)
            } else if !indented {
                Text(file.kind.rawValue).font(.subheadline).foregroundStyle(.secondary)
            }
        case .reading(let page, let pages):
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text(page.map { "Reading page \($0) of \(pages ?? $0)…" } ?? "Reading…").font(.callout).foregroundStyle(.secondary)
            }
        case .hiding:
            HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Holding…").font(.callout).foregroundStyle(.secondary) }
        case .done(let counts, _):
            if counts.isEmpty {
                Label("Nothing found. Check it before sharing.", systemImage: "circle.dashed").font(.callout).foregroundStyle(.secondary)
            } else {
                let total = counts.values.reduce(0, +)
                DisclosureGroup {
                    Text(receiptText(counts)).font(.subheadline.monospaced()).foregroundStyle(.secondary)
                } label: {
                    Label("Done. \(total) item\(total == 1 ? "" : "s") held", systemImage: "checkmark.circle.fill")
                        .font(.callout).symbolRenderingMode(.multicolor)
                }
            }
        case .refused(let message):
            VStack(alignment: .leading, spacing: 2) {
                Label("Not saved", systemImage: "xmark.octagon.fill").font(.callout.weight(.semibold)).foregroundStyle(.red)
                Text(message).font(.subheadline).foregroundStyle(.secondary)
            }
        case .exists:
            VStack(alignment: .leading, spacing: 2) {
                Label("Not saved", systemImage: "xmark.octagon.fill").font(.callout.weight(.semibold)).foregroundStyle(.red)
                Text("A redacted copy already exists. Replace it?").font(.subheadline).foregroundStyle(.secondary)
            }
        case .cancelled:
            Label("Cancelled. Nothing was written.", systemImage: "nosign").font(.subheadline).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var trailing: some View {
        switch file.state {
        case .queued where !indented && model.phase == .queue: RemoveButton(entry: entry)
        case .done(_, let output):
            HStack(spacing: 6) {
                Button("Preview") { model.previewURL = output }
                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([output]) }
            }
            .buttonStyle(.bordered).controlSize(.small).fixedSize()
            Menu {
                Button("Hold More Words…") { model.termsTarget = file }
            } label: { Image(systemName: "ellipsis.circle") }
                .menuStyle(.borderlessButton).fixedSize().accessibilityLabel("More for \(file.name)")
        case .exists(let output):
            HStack(spacing: 6) {
                Button("Replace") { model.replace(file, output: output) }.buttonStyle(.bordered)
                Button("Keep Both") { model.keepBoth(file) }.buttonStyle(.borderedProminent)
            }
            .controlSize(.small).fixedSize()
        default: EmptyView()
        }
    }
}

struct DragOut: ViewModifier {
    var file: FileItem
    func body(content: Content) -> some View {
        if case .done(_, let output) = file.state { content.draggable(output) } else { content }
    }
}

// MARK: - Download sheet

struct DownloadSheet: View {
    @Environment(AppModel.self) private var model
    var kind: AppModel.DownloadKind
    @State private var started = false

    private var title: String { kind == .names ? "name and address detection" : "Hindi and Tamil reading" }
    private var size: String { kind == .names ? "1.3 GB" : "about 100 MB" }

    var body: some View {
        let installer = model.installer
        VStack(spacing: 12) {
            AppMark(size: 56)
            switch installer.phase {
            case .running:
                Text("Downloading \(title)").font(.headline)
                ProgressView(value: installer.fraction).tint(Color.hmdAccent)
                HStack {
                    Text("\(formatBytes(installer.bytes)) of \(size)").font(.subheadline.monospaced())
                    Spacer()
                    Text("\(Int(installer.fraction * 100))%").font(.subheadline.monospaced())
                }
                .foregroundStyle(.secondary)
                note
                HStack { Spacer(); Button("Cancel", role: .cancel) { installer.cancel() }.keyboardShortcut(.cancelAction) }
            case .failed:
                Text("The download didn't finish").font(.headline)
                Label("Stopped at \(formatBytes(installer.bytes)) of \(size)", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text("Check your internet connection and try again. The other categories still work.")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center)
                HStack {
                    Spacer()
                    Button("Not now") { installer.reset(); model.downloadRequest = nil }
                    Button("Try Again") { Task { await model.download(kind) } }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                }
            default:
                Text("Download \(title)?").font(.headline)
                Text(kind == .names
                     ? "To find names, addresses and dates of birth, Hold My Data needs a one-time download of \(size)."
                     : "Each added language is a one-time download of about 100 MB.")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center)
                note
                HStack {
                    Spacer()
                    Button("Not now") { model.downloadRequest = nil }.keyboardShortcut(.cancelAction)
                    Button("Download") { Task { await model.download(kind) } }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(24).frame(width: 380)
    }

    private var note: some View {
        Text("This is the only time the app uses the internet.").font(.subheadline).foregroundStyle(.secondary)
    }
}

// MARK: - Hide More

struct TermsSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    var file: FileItem
    @State private var text = ""

    private var terms: [String] {
        text.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Hold more in \(file.name)").font(.headline)
            Text("Type names or words the app missed, one per line. They are held everywhere they appear, and the saved copy is replaced.")
                .foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.body).frame(height: 120).scrollContentBackground(.hidden).padding(6)
                .background(.quinary, in: RoundedRectangle(cornerRadius: 8))
                .accessibilityLabel("Words to hold, one per line")
            Text("Words stay on this Mac and are not saved.").font(.subheadline).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Hold and Replace") { model.hideMore(file, terms: terms); dismiss() }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(terms.isEmpty)
            }
        }
        .padding(24).frame(width: 400)
    }
}

// MARK: - Settings window

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmUninstall = false

    var body: some View {
        @Bindable var model = model
        Form {
            Section("Image languages") {
                Toggle(isOn: .constant(true)) { VStack(alignment: .leading) { Text("English"); Text("Always on").font(.subheadline).foregroundStyle(.secondary) } }
                    .toggleStyle(.checkbox).disabled(true)
                Toggle("Hindi and Tamil", isOn: Binding(get: { model.hindiTamil }, set: { model.setHindiTamil($0) }))
                    .toggleStyle(.checkbox)
                Text("Each added language is a one-time download of about 100 MB.").font(.subheadline).foregroundStyle(.secondary)
            }
            Section("Downloaded tools") {
                LabeledContent("Size on disk") {
                    HStack {
                        Text(formatBytes(AppPaths.directorySize(AppPaths.support))).monospaced()
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([AppPaths.support]) }
                    }
                }
                LabeledContent("Uninstall tools") {
                    Button("Uninstall…", role: .destructive) { confirmUninstall = true }
                }
                Text("Frees the space. Setup runs again next launch.").font(.subheadline).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped).frame(width: 440, height: 360)
        .confirmationDialog("Uninstall the downloaded tools?", isPresented: $confirmUninstall) {
            Button("Uninstall", role: .destructive) { model.uninstall() }
        } message: { Text("Your files and redacted copies are not touched.") }
        .sheet(item: Binding(get: { model.downloadFromSettings ? model.downloadRequest : nil },
                             set: { model.downloadRequest = $0; if $0 == nil { model.downloadFromSettings = false } })) {
            DownloadSheet(kind: $0)
        }
    }
}
