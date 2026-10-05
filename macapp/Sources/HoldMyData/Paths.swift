import Foundation

/// Everything the app installs lives under one folder, so "Uninstall tools" is one delete
/// and the app never touches ~/.holdmydata (the command-line tool's own data).
enum AppPaths {
    static let support = FileManager.default
        .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("HoldMyData", isDirectory: true)
    static let engineDir = support.appendingPathComponent("engine", isDirectory: true)
    static let venv = engineDir.appendingPathComponent("venv", isDirectory: true)
    static let python = venv.appendingPathComponent("bin/python")
    static let pythonInstall = engineDir.appendingPathComponent("python", isDirectory: true)
    static let models = support.appendingPathComponent("models", isDirectory: true)
    static let scratch = support.appendingPathComponent("scratch", isDirectory: true)
    static let engineMarker = engineDir.appendingPathComponent("ready")
    static let photoMarker = engineDir.appendingPathComponent("photo-ready")
    static let setupLog = support.appendingPathComponent("setup.log")

    /// The uv binary shipped inside the app; Homebrew's copy is a fallback for development builds.
    static var uv: URL? {
        if let bundled = Bundle.main.url(forResource: "uv", withExtension: nil) { return bundled }
        return ["/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
            .map { URL(fileURLWithPath: $0) }
            .first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    /// The engine wheel shipped inside the app. HOLDMYDATA_WHEEL overrides it for development.
    static var wheel: URL? {
        if let path = ProcessInfo.processInfo.environment["HOLDMYDATA_WHEEL"] {
            return URL(fileURLWithPath: path)
        }
        guard let dir = Bundle.main.resourceURL?.appendingPathComponent("engine"),
              let items = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        else { return nil }
        return items.first { $0.pathExtension == "whl" }
    }

    /// "0.2.0" from "hold_my_data-0.2.0-py3-none-any.whl".
    static var wheelVersion: String? {
        wheel?.lastPathComponent.split(separator: "-").dropFirst().first.map(String.init)
    }

    static func subprocessEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["HOLDMYDATA_MODEL_DIR"] = models.path
        env["HF_HOME"] = scratch.appendingPathComponent("hf").path
        env["UV_CACHE_DIR"] = scratch.appendingPathComponent("uv").path
        env["UV_PYTHON_INSTALL_DIR"] = pythonInstall.path
        env["UV_PYTHON_PREFERENCE"] = "only-managed"
        env["UV_LINK_MODE"] = "copy"
        env["PYTHONUNBUFFERED"] = "1"
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"
        return env
    }

    static func directorySize(_ url: URL) -> Int64 {
        let keys: [URLResourceKey] = [.totalFileAllocatedSizeKey, .isRegularFileKey]
        guard let walker = FileManager.default.enumerator(at: url, includingPropertiesForKeys: keys) else { return 0 }
        var total: Int64 = 0
        for case let file as URL in walker {
            guard let values = try? file.resourceValues(forKeys: Set(keys)), values.isRegularFile == true else { continue }
            total += Int64(values.totalFileAllocatedSize ?? 0)
        }
        return total
    }
}

func formatBytes(_ bytes: Int64) -> String {
    let formatter = ByteCountFormatter()
    formatter.allowedUnits = [.useMB, .useGB]
    formatter.countStyle = .file
    return formatter.string(fromByteCount: bytes)
}
