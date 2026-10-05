import AppKit
import Foundation
import Observation

// MARK: - Categories (the "What to hide" list; ids match holdmydata/categories.py)

struct Category: Identifiable {
    let id: String
    let title: String
    let short: String
    let note: String
    var needsDownload = false   // reads free text with downloaded models
    var unavailable = false     // engine cannot run it offline yet

    static let all: [Category] = [
        .init(id: "secrets", title: "API keys & secrets", short: "Secrets", note: "Instant"),
        .init(id: "global", title: "Contact info", short: "Contact info",
              note: "Email, phone, cards, IP addresses · Instant"),
        .init(id: "india", title: "Indian ID numbers", short: "Indian IDs",
              note: "PAN, Aadhaar, GSTIN, licence… · Instant"),
        .init(id: "meaning", title: "Names, addresses, dates of birth", short: "Names & addresses",
              note: "Needs a one-time download, 1.3 GB", needsDownload: true),
        .init(id: "usa", title: "US IDs", short: "US IDs", note: "Lightly tested"),
        .init(id: "uk", title: "UK IDs", short: "UK IDs", note: "Lightly tested"),
        // The medical model is not part of the offline downloads yet, so it cannot run.
        .init(id: "medical", title: "Medical terms", short: "Medical",
              note: "Not available yet", unavailable: true),
    ]
}

func displayName(_ entity: String) -> String {
    let known = ["EMAIL": "Email", "PHONE": "Phone", "IN_PHONE": "Phone", "CREDIT_CARD": "Card",
                 "IP_ADDRESS": "IP address", "PERSON": "Name", "DOB": "Date of birth",
                 "ADDRESS": "Address", "CUSTOMER_ID": "Customer ID", "JWT": "Secret",
                 "PRIVATE_KEY": "Secret", "CONNECTION_STRING": "Secret", "URL_WITH_CREDENTIALS": "Secret"]
    if let name = known[entity] { return name }
    if entity.hasSuffix("_KEY") || entity.hasSuffix("_TOKEN") { return "Secret" }
    var parts = entity.split(separator: "_").map(String.init)
    if let first = parts.first, ["IN", "US", "UK"].contains(first), parts.count > 1 { parts.removeFirst() }
    return parts.map { $0.count <= 6 ? $0 : $0.capitalized }.joined(separator: " ")
}

func receiptText(_ counts: [String: Int]) -> String {
    var merged: [String: Int] = [:]
    for (entity, n) in counts { merged[displayName(entity), default: 0] += n }
    return merged.sorted { $0.key < $1.key }.map { "\($0.key) ×\($0.value)" }.joined(separator: "   ")
}

// MARK: - Files

enum FileKind: String { case text = "Text", image = "Image", pdf = "PDF", unreadable = "" }

enum RowState: Equatable {
    case queued
    case reading(page: Int?, pages: Int?)
    case hiding
    case done(counts: [String: Int], output: URL)
    case refused(String)
    case exists(output: URL)
    case cancelled
}

@Observable @MainActor
final class FileItem: Identifiable {
    let id = UUID()
    let url: URL
    var kind: FileKind
    var state: RowState = .queued
    init(url: URL, kind: FileKind) { self.url = url; self.kind = kind }
    var name: String { url.lastPathComponent }
    var isReadable: Bool { kind != .unreadable }
}

@Observable @MainActor
final class Entry: Identifiable {
    let id = UUID()
    let url: URL
    let isFolder: Bool
    var files: [FileItem]
    var expanded = false
    var showAll = false
    init(url: URL, isFolder: Bool, files: [FileItem]) { self.url = url; self.isFolder = isFolder; self.files = files }
}

enum Classifier {
    static let imageExtensions: Set<String> = ["png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp"]

    /// Same rules as holdmydata/app_helper.py: PDF and image by extension, anything else only
    /// if it is valid UTF-8 text. The engine checks again before it writes anything.
    static func kind(of url: URL) -> FileKind {
        let ext = url.pathExtension.lowercased()
        if ext == "pdf" { return .pdf }
        if imageExtensions.contains(ext) { return .image }
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe) else { return .unreadable }
        if data.contains(0) { return .unreadable }
        return String(data: data, encoding: .utf8) == nil ? .unreadable : .text
    }

    static func scan(_ urls: [URL]) -> [(url: URL, isFolder: Bool, files: [(URL, FileKind)])] {
        var result: [(URL, Bool, [(URL, FileKind)])] = []
        for url in urls {
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir) else { continue }
            if isDir.boolValue {
                let walker = FileManager.default.enumerator(
                    at: url, includingPropertiesForKeys: [.isRegularFileKey],
                    options: [.skipsHiddenFiles, .skipsPackageDescendants])
                var files: [(URL, FileKind)] = []
                while let item = walker?.nextObject() as? URL {
                    guard (try? item.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true else { continue }
                    files.append((item, kind(of: item)))
                }
                files.sort { $0.0.path.localizedStandardCompare($1.0.path) == .orderedAscending }
                result.append((url, true, files))
            } else {
                result.append((url, false, [(url, kind(of: url))]))
            }
        }
        return result
    }
}

// MARK: - App model

@Observable @MainActor
final class AppModel {
    enum EngineState { case checking, needsSetup, ready }
    enum Phase { case queue, working, finished }
    enum DownloadKind: String, Identifiable {
        case names, indic
        var id: String { rawValue }
    }

    struct Status {
        var names = false, address = false, ocrEnglish = false, ocrIndic = false
        var modelBytes: Int64 = 0
    }

    // Saved choices
    var selected: Set<String> { didSet { defaults.set(Array(selected), forKey: "categories") } }
    var destination: URL? {
        didSet { defaults.set(destination?.path, forKey: "destination") }
    }
    var hindiTamil: Bool { didSet { defaults.set(hindiTamil, forKey: "hindiTamil") } }

    // State
    var engine: EngineState = .checking
    var phase: Phase = .queue
    var entries: [Entry] = []
    var isTargeted = false
    var status = Status()
    var previewURL: URL?
    var downloadRequest: DownloadKind? { didSet { if downloadRequest == nil { downloadFromSettings = false } } }
    /// True when the request came from the Settings window, so only that window shows the sheet.
    var downloadFromSettings = false
    var batchDone = 0
    var batchTotal = 0
    var isRerunning = false
    var alertMessage: String?
    var termsTarget: FileItem?

    let installer = Installer()
    private let helper = HelperClient()
    private var lastActivity = Date()
    private var idleTimer: Timer?
    /// The reading helper holds the models in memory (several GB). Stop it when nothing has
    /// been asked of it for this long; the next file starts it again.
    private static let idleSeconds: TimeInterval = 120
    private let defaults = UserDefaults.standard
    private var cancelled = false

    init() {
        let saved = defaults.stringArray(forKey: "categories")
        selected = Set(saved ?? ["secrets", "global", "india"])
        destination = defaults.string(forKey: "destination").map { URL(fileURLWithPath: $0) }
        hindiTamil = defaults.bool(forKey: "hindiTamil")
    }

    // MARK: Derived

    var allFiles: [FileItem] { entries.flatMap(\.files) }
    var readyFiles: [FileItem] { allFiles.filter(\.isReadable) }
    var skippedCount: Int { allFiles.count - readyFiles.count }
    var namesInstalled: Bool { status.names && status.address }
    var showUKCaution: Bool { selected.contains("uk") && selected.contains("india") }
    var canStart: Bool { !readyFiles.isEmpty && !selected.isEmpty && phase == .queue }

    var selectionSummary: String {
        let names = Category.all.filter { selected.contains($0.id) }.map(\.short)
        switch names.count {
        case 0: return "Nothing"
        case 1, 2: return names.joined(separator: ", ")
        default: return names.prefix(2).joined(separator: ", ") + " +\(names.count - 2)"
        }
    }

    var destinationName: String { destination?.lastPathComponent ?? "Same folder as the original" }

    // MARK: Engine

    func bootstrap() async {
        let fm = FileManager.default
        let marker = try? String(contentsOf: AppPaths.engineMarker, encoding: .utf8)
        let versionOK = AppPaths.wheelVersion == nil || marker == AppPaths.wheelVersion
        if fm.fileExists(atPath: AppPaths.python.path), versionOK, fm.fileExists(atPath: AppPaths.photoMarker.path) {
            engine = .ready
            await refreshStatus()
        } else {
            engine = .needsSetup
        }
    }

    func runSetup() async {
        let engineReady = FileManager.default.fileExists(atPath: AppPaths.python.path)
            && (try? String(contentsOf: AppPaths.engineMarker, encoding: .utf8)) == (AppPaths.wheelVersion ?? "dev")
        await installer.runSetup(installEngine: !engineReady)
        if installer.phase == .done {
            engine = .ready
            installer.reset()
            await refreshStatus()
        }
    }

    func startHelper() async -> Bool {
        lastActivity = Date()
        if idleTimer == nil {
            idleTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.stopHelperIfIdle() }
            }
        }
        if helper.isRunning { return true }
        return await helper.start()
    }

    private func stopHelperIfIdle() {
        guard helper.isRunning, phase != .working, !isRerunning,
              Date().timeIntervalSince(lastActivity) > Self.idleSeconds else { return }
        helper.terminate()
    }

    func refreshStatus() async {
        guard await startHelper() else { return }
        let e = await helper.request(["op": "status"])
        guard e.name == "status", let models = e.raw["models"] as? [String: Bool] else { return }
        status = Status(names: models["names"] ?? false, address: models["address"] ?? false,
                        ocrEnglish: models["ocr_en"] ?? false, ocrIndic: models["ocr_indic"] ?? false,
                        modelBytes: Int64(e.int("model_bytes") ?? 0))
    }

    func engineVersion() async -> String? {
        guard await startHelper() else { return nil }
        return helper.version
    }

    func uninstall() {
        helper.terminate()
        installer.uninstall()
        entries.removeAll()
        phase = .queue
        status = Status()
        engine = .needsSetup
    }

    // MARK: Downloads

    func download(_ kind: DownloadKind) async {
        switch kind {
        case .names:
            await installer.runDownload(title: "Downloading name and address detection",
                                        flags: ["--text-only"], expected: 1_300_000_000)
        case .indic:
            await installer.runDownload(title: "Downloading Hindi and Tamil reading",
                                        flags: ["--images-only", "--with-indic-ocr"], expected: 100_000_000)
        }
        guard installer.phase == .done else { return }
        helper.terminate()          // pick up the new models on next start
        await refreshStatus()
        switch kind {
        case .names: selected.insert("meaning")
        case .indic: hindiTamil = true
        }
        downloadRequest = nil
        downloadFromSettings = false
        installer.reset()
    }

    func toggle(_ category: Category) {
        if selected.contains(category.id) {
            selected.remove(category.id)
        } else if category.needsDownload, !namesInstalled {
            downloadRequest = .names
        } else if !category.unavailable {
            selected.insert(category.id)
        }
    }

    func setHindiTamil(_ on: Bool) {
        if on, !status.ocrIndic { downloadFromSettings = true; downloadRequest = .indic } else { hindiTamil = on }
    }

    // MARK: Queue

    func add(_ urls: [URL]) async {
        guard phase != .working else { return }
        if phase == .finished { clear() }
        let known = Set(entries.map(\.url.path))
        let fresh = urls.filter { !known.contains($0.path) }
        let scanned = await Task.detached { Classifier.scan(fresh) }.value
        for item in scanned {
            entries.append(Entry(url: item.url, isFolder: item.isFolder,
                                 files: item.files.map { FileItem(url: $0.0, kind: $0.1) }))
        }
    }

    func remove(_ entry: Entry) { entries.removeAll { $0.id == entry.id } }
    func clear() { entries.removeAll(); phase = .queue; batchDone = 0; batchTotal = 0 }

    func chooseFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.prompt = "Add"
        if panel.runModal() == .OK { Task { await add(panel.urls) } }
    }

    func chooseDestination() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.prompt = "Choose"
        if panel.runModal() == .OK { destination = panel.url }
    }

    // MARK: Run

    func outputURL(for file: FileItem, copy: Int = 0) -> URL {
        let dir = destination ?? file.url.deletingLastPathComponent()
        let stem = file.url.deletingPathExtension().lastPathComponent
        let ext = file.url.pathExtension
        let suffix = copy > 0 ? ".redacted \(copy + 1)" : ".redacted"
        return dir.appendingPathComponent(stem + suffix + (ext.isEmpty ? "" : "." + ext))
    }

    func start() async {
        guard canStart else { return }
        guard await startHelper() else {
            alertMessage = "The reading tool didn't start. Try again."
            return
        }
        let files = readyFiles
        cancelled = false
        phase = .working
        batchTotal = files.count
        batchDone = 0
        for file in files {
            if cancelled { break }
            await process(file, output: outputURL(for: file), force: false)
            batchDone += 1
        }
        if cancelled {
            for file in files where file.state == .queued || isActive(file.state) { file.state = .cancelled }
        }
        phase = .finished
        lastActivity = Date()
    }

    func cancel() {
        cancelled = true
        helper.terminate()
    }

    func replace(_ file: FileItem, output: URL) { Task { await rerun(file, output: output, force: true) } }

    /// "Hide More": run the same file again, also hiding words typed by hand, over the same output.
    func hideMore(_ file: FileItem, terms: [String]) {
        guard case .done(_, let output) = file.state else { return }
        Task { await rerun(file, output: output, force: true, terms: terms) }
    }

    func keepBoth(_ file: FileItem) {
        var copy = 1
        while FileManager.default.fileExists(atPath: outputURL(for: file, copy: copy).path) { copy += 1 }
        Task { await rerun(file, output: outputURL(for: file, copy: copy), force: false) }
    }

    private func rerun(_ file: FileItem, output: URL, force: Bool, terms: [String] = []) async {
        guard await startHelper() else { return }
        isRerunning = true
        await process(file, output: output, force: force, terms: terms)
        isRerunning = false
        lastActivity = Date()
    }

    private func isActive(_ s: RowState) -> Bool {
        if case .reading = s { return true }
        return s == .hiding
    }

    private func process(_ file: FileItem, output: URL, force: Bool, terms: [String] = []) async {
        file.state = .reading(page: nil, pages: nil)
        var payload: [String: Any] = [
            "op": "redact", "input": file.url.path, "output": output.path,
            "categories": Array(selected), "force": force,
        ]
        payload["ocr_langs"] = hindiTamil ? ["hi", "ta"] : []
        payload["extra_terms"] = terms
        let result = await helper.request(payload) { [weak file] event in
            guard let file else { return }
            if event.string("stage") == "hiding" { file.state = .hiding }
            else { file.state = .reading(page: event.int("page"), pages: event.int("pages")) }
        }
        switch result.name {
        case "done":
            let counts = result.raw["counts"] as? [String: Int] ?? [:]
            file.state = .done(counts: counts, output: output)
        case "error":
            let message = result.string("message") ?? "Something went wrong."
            switch result.string("kind") {
            case "exists": file.state = .exists(output: output)
            case "unsupported": file.kind = .unreadable; file.state = .queued
            default: file.state = .refused(refusalText(message, kind: result.string("kind")))
            }
        default:
            file.state = .refused("Something went wrong.")
        }
    }

    private func refusalText(_ message: String, kind: String?) -> String {
        if kind == "failed" { return "Something went wrong while reading this file." }
        if message.contains("OCR read only") || message.contains("mean confidence") {
            return "Couldn't read this image clearly enough to be safe. Try a sharper copy."
        }
        if message.contains("locked with a password") { return "This PDF is locked with a password." }
        return message
    }
}
