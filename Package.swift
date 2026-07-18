// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "BioToolsApp",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "BioToolsApp", targets: ["BioToolsApp"])
    ],
    targets: [
        .executableTarget(
            name: "BioToolsApp",
            path: "Sources/BioToolsApp",
            linkerSettings: [
                .linkedFramework("WebKit"),
                .linkedFramework("Security")
            ]
        ),
        .testTarget(
            name: "BioToolsAppTests",
            dependencies: ["BioToolsApp"],
            path: "Tests/BioToolsAppTests"
        )
    ]
)
