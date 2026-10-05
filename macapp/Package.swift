// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "HoldMyData",
    platforms: [.macOS(.v15)],
    targets: [
        .executableTarget(name: "HoldMyData", path: "Sources/HoldMyData"),
    ],
    swiftLanguageModes: [.v5]
)
