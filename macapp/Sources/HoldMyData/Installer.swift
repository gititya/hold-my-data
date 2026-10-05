import Foundation
import Observation

/// Installs the reading engine and downloads models. Runs the bundled uv and the engine's own
/// `download-models` command. This is the only part of the app that uses the internet.
@MainActor @Observable
final class Installer {
    enum Phase { case idle, running, failed, done }

    var phase: Phase = .idle
    var stepTitle = ""
    var step = 1
    var stepCount = 1
    var bytes: Int64 = 0
    var expected: Int64 = 1
    var log: [String] = []
    var failureMessage = "Setup didn't finish. Check your internet connection and try again."

    private var process: Process?
    private var poller: Task<Void, Never>?
    private var baseline: Int64 = 0
    private var cancelled = false
    private var tail: [String] = []

    var fraction: Double { min(0.99, Double(bytes) / Double(max(expected, 1))) }

    // MARK: - Setup

    /// Engine first (skipped when already installed), then English photo and PDF reading.
    func runSetup(installEngine: Bool) async {
        guard let uv = AppPaths.uv, let wheel = AppPaths.wheel else {
            fail("This copy of Hold My Data is missing its installer. Download it again.")
            return
        }
        begin(expected: 1_800_000_000, stepCount: installEngine ? 2 : 1)
        try? FileManager.default.createDirectory(at: AppPaths.engineDir, withIntermediateDirectories: true)

        if installEngine {
            step = 1
            stepTitle = "Installing engine"
            note(stepTitle)
            try? FileManager.default.removeItem(at: AppPaths.venv)
            guard await run(uv, ["venv", "--python", "3.13", AppPaths.venv.path]) == 0,
                  await run(uv, ["pip", "install", "--python", AppPaths.python.path, wheel.path]) == 0
            else { return finishFailed() }
            try? (AppPaths.wheelVersion ?? "dev").write(to: AppPaths.engineMarker, atomically: true, encoding: .utf8)
            note("Installing engine: done")
        }

        step = installEngine ? 2 : 1
        stepTitle = "Downloading photo and PDF reading"
        note(stepTitle)
        guard await run(AppPaths.python, ["-m", "holdmydata.cli", "download-models", "--images-only"]) == 0
        else { return finishFailed() }
        try? "1".write(to: AppPaths.photoMarker, atomically: true, encoding: .utf8)
        finish()
    }

    /// One model download: names and addresses, or Hindi and Tamil photo reading.
    func runDownload(title: String, flags: [String], expected: Int64) async {
        begin(expected: expected, stepCount: 1)
        stepTitle = title
        note(title)
        let code = await run(AppPaths.python, ["-m", "holdmydata.cli", "download-models"] + flags)
        code == 0 ? finish() : finishFailed()
    }

    func cancel() {
        cancelled = true
        process?.terminate()
    }

    func reset() {
        phase = .idle
        log.removeAll()
    }

    func uninstall() {
        try? FileManager.default.removeItem(at: AppPaths.support)
    }

    // MARK: - Plumbing

    private func begin(expected: Int64, stepCount: Int) {
        phase = .running
        cancelled = false
        bytes = 0
        self.expected = expected
        self.stepCount = stepCount
        step = 1
        log.removeAll()
        tail.removeAll()
        baseline = AppPaths.directorySize(AppPaths.support)
        poller = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1.5))
                let size = await Task.detached { AppPaths.directorySize(AppPaths.support) }.value
                guard let self else { return }
                self.bytes = max(self.bytes, max(0, size - self.baseline))
            }
        }
    }

    private func finish() {
        poller?.cancel()
        try? FileManager.default.removeItem(at: AppPaths.scratch)
        bytes = expected
        phase = .done
    }

    private func finishFailed() {
        poller?.cancel()
        if cancelled {
            note("Cancelled")
            try? FileManager.default.removeItem(at: AppPaths.scratch)
            phase = .idle
            return
        }
        log.append(contentsOf: tail.suffix(12))
        try? FileManager.default.removeItem(at: AppPaths.scratch)
        note("Partial download removed")
        phase = .failed
    }

    private func fail(_ message: String) {
        failureMessage = message
        log = [message]
        phase = .failed
    }

    private func note(_ text: String) {
        let time = Date.now.formatted(date: .omitted, time: .standard)
        log.append("\(time)  \(text)")
    }

    private func run(_ exe: URL, _ args: [String]) async -> Int32 {
        let p = Process()
        p.executableURL = exe
        p.arguments = args
        p.environment = AppPaths.subprocessEnvironment()
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.consume(data) } }
        }
        return await withCheckedContinuation { c in
            p.terminationHandler = { proc in
                pipe.fileHandleForReading.readabilityHandler = nil
                c.resume(returning: proc.terminationStatus)
            }
            do { try p.run(); process = p } catch {
                pipe.fileHandleForReading.readabilityHandler = nil
                tail.append("Could not start \(exe.lastPathComponent)")
                c.resume(returning: -1)
            }
        }
    }

    private func consume(_ data: Data) {
        let text = String(decoding: data, as: UTF8.self)
            .replacingOccurrences(of: "\u{1B}\\[[0-9;?]*[A-Za-z]", with: "", options: .regularExpression)
            .replacingOccurrences(of: "\r", with: "\n")
        for line in text.split(separator: "\n") where !line.trimmingCharacters(in: .whitespaces).isEmpty {
            tail.append(String(line))
        }
        if tail.count > 60 { tail.removeFirst(tail.count - 60) }
    }
}
