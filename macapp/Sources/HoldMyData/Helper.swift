import Foundation

/// Talks to `python -m holdmydata.app_helper` over JSON lines. See holdmydata/app_helper.py
/// for the protocol. One request runs at a time; cancelling ends the process.
@MainActor
final class HelperClient {
    struct Event {
        var id: String?
        var name: String
        var raw: [String: Any]
        func string(_ key: String) -> String? { raw[key] as? String }
        func int(_ key: String) -> Int? { raw[key] as? Int }
    }

    private var process: Process?
    private var input: FileHandle?
    private var buffer = Data()
    private var waiting: [String: CheckedContinuation<Event, Never>] = [:]
    private var progress: [String: (Event) -> Void] = [:]
    private var readyWaiter: CheckedContinuation<String?, Never>?
    private(set) var version = ""
    private(set) var isRunning = false

    func start() async -> Bool {
        if isRunning { return true }
        let p = Process()
        p.executableURL = AppPaths.python
        p.arguments = ["-u", "-m", "holdmydata.app_helper"]
        p.environment = AppPaths.subprocessEnvironment()
        let out = Pipe(), inp = Pipe()
        p.standardOutput = out
        p.standardInput = inp
        p.standardError = FileHandle.nullDevice
        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.ingest(data) } }
        }
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.exited() } }
        }
        do { try p.run() } catch { return false }
        process = p
        input = inp.fileHandleForWriting
        isRunning = true
        let v = await withCheckedContinuation { (c: CheckedContinuation<String?, Never>) in readyWaiter = c }
        guard let v else { return false }
        version = v
        return true
    }

    /// Sends one request and returns its final event (done, error or status).
    /// `onStage` gets the progress events on the way.
    func request(_ payload: [String: Any], onStage: ((Event) -> Void)? = nil) async -> Event {
        let id = UUID().uuidString
        var body = payload
        body["id"] = id
        guard isRunning, let input, let data = try? JSONSerialization.data(withJSONObject: body) else {
            return Event(id: id, name: "error", raw: ["kind": "failed", "message": "The reading tool isn't running."])
        }
        progress[id] = onStage
        return await withCheckedContinuation { c in
            waiting[id] = c
            input.write(data + Data("\n".utf8))
        }
    }

    func terminate() {
        process?.terminationHandler = nil
        process?.terminate()
        process = nil
        exited()
    }

    private func ingest(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let line = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            guard let obj = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any],
                  let name = obj["event"] as? String else { continue }
            let event = Event(id: obj["id"] as? String, name: name, raw: obj)
            if name == "ready" {
                readyWaiter?.resume(returning: obj["version"] as? String)
                readyWaiter = nil
            } else if let id = event.id {
                if name == "stage" { progress[id]?(event) }
                else if let c = waiting.removeValue(forKey: id) { progress[id] = nil; c.resume(returning: event) }
            }
        }
    }

    private func exited() {
        isRunning = false
        input = nil
        process = nil
        buffer.removeAll()
        readyWaiter?.resume(returning: nil)
        readyWaiter = nil
        let gone = Event(id: nil, name: "error", raw: ["kind": "failed", "message": "The reading tool stopped."])
        for (_, c) in waiting { c.resume(returning: gone) }
        waiting.removeAll()
        progress.removeAll()
    }
}
