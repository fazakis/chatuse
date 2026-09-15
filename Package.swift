// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "Chatuse", platforms: [.macOS(.v13)], products: [
    .executable(name: "chatuse-native", targets: ["ChatuseNative"]),
    .executable(name: "chatuse-pointer", targets: ["ChatusePointer"])
], targets: [
    .target(name: "ChatuseCore"),
    .executableTarget(name: "ChatuseNative", dependencies: ["ChatuseCore"]),
    .executableTarget(name: "ChatusePointer", dependencies: ["ChatuseCore"]),
    .testTarget(name: "ChatuseCoreTests", dependencies: ["ChatuseCore"])
])
