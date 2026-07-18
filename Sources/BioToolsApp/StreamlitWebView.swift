import AppKit
import SwiftUI
import WebKit

struct StreamlitWebView: NSViewRepresentable {
    let url: URL
    let reloadID: Int

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsMagnification = true
        webView.setValue(false, forKey: "drawsBackground")
        webView.load(URLRequest(url: url))
        context.coordinator.lastReloadID = reloadID
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        if webView.url == nil || webView.url?.host != url.host || webView.url?.port != url.port {
            webView.load(URLRequest(url: url))
            context.coordinator.lastReloadID = reloadID
            return
        }
        if context.coordinator.lastReloadID != reloadID {
            context.coordinator.lastReloadID = reloadID
            webView.reload()
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKDownloadDelegate {
        var lastReloadID = 0
        private var downloadDestinations: [ObjectIdentifier: URL] = [:]

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            if navigationAction.shouldPerformDownload {
                decisionHandler(.download)
                return
            }

            guard let targetURL = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            if isLocalAppURL(targetURL) {
                decisionHandler(.allow)
            } else {
                NSWorkspace.shared.open(targetURL)
                decisionHandler(.cancel)
            }
        }

        func webView(
            _ webView: WKWebView,
            navigationAction: WKNavigationAction,
            didBecome download: WKDownload
        ) {
            download.delegate = self
        }

        func webView(
            _ webView: WKWebView,
            navigationResponse: WKNavigationResponse,
            didBecome download: WKDownload
        ) {
            download.delegate = self
        }

        func download(
            _ download: WKDownload,
            decideDestinationUsing response: URLResponse,
            suggestedFilename: String,
            completionHandler: @escaping (URL?) -> Void
        ) {
            let destination = uniqueDownloadURL(for: suggestedFilename)
            downloadDestinations[ObjectIdentifier(download)] = destination
            completionHandler(destination)
        }

        func downloadDidFinish(_ download: WKDownload) {
            let key = ObjectIdentifier(download)
            guard let destination = downloadDestinations.removeValue(forKey: key) else {
                return
            }
            NSWorkspace.shared.activateFileViewerSelecting([destination])
        }

        func download(
            _ download: WKDownload,
            didFailWithError error: Error,
            resumeData: Data?
        ) {
            downloadDestinations.removeValue(forKey: ObjectIdentifier(download))
        }

        private func isLocalAppURL(_ url: URL) -> Bool {
            if ["about", "blob", "data"].contains(url.scheme?.lowercased() ?? "") {
                return true
            }
            let host = url.host?.lowercased()
            return host == "127.0.0.1" || host == "localhost"
        }

        private func uniqueDownloadURL(for suggestedFilename: String) -> URL {
            let fileManager = FileManager.default
            let downloads = fileManager.urls(
                for: .downloadsDirectory,
                in: .userDomainMask
            )[0]

            let safeName = suggestedFilename.isEmpty ? "MyBioTools-结果" : suggestedFilename
            let original = downloads.appendingPathComponent(safeName)
            guard fileManager.fileExists(atPath: original.path) else {
                return original
            }

            let extensionName = original.pathExtension
            let stem = original.deletingPathExtension().lastPathComponent
            var index = 2

            while true {
                let candidateName = extensionName.isEmpty
                    ? "\(stem) \(index)"
                    : "\(stem) \(index).\(extensionName)"
                let candidate = downloads.appendingPathComponent(candidateName)
                if !fileManager.fileExists(atPath: candidate.path) {
                    return candidate
                }
                index += 1
            }
        }
    }
}
