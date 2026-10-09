import AppKit
import WebKit
import UniformTypeIdentifiers

struct NativeFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class Backend {
    let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let queue = DispatchQueue(label: "local.filmeditor.backend")
    private var buffer = Data()
    private var nextID = 0
    private var pending: [Int: (Result<Any, Error>) -> Void] = [:]
    var onReady: (() -> Void)?
    var onExit: ((Int32) -> Void)?

    func launch(resources: URL, workspace: String) throws {
        let raw = try Data(contentsOf: resources.appendingPathComponent("runtime.json"))
        guard let settings = try JSONSerialization.jsonObject(with: raw) as? [String: Any],
              let python = settings["python"] as? String else {
            throw NativeFailure(message: "The application runtime is incomplete. Rebuild FilmEditor.")
        }
        func resolve(_ value: String) -> String {
            value.replacingOccurrences(of: "@resources/", with: resources.path + "/")
        }
        process.executableURL = URL(fileURLWithPath: resolve(python))
        let source = resources.appendingPathComponent("app")
        process.arguments = ["-B", source.appendingPathComponent("app.py").path, "desktop", "--workspace", workspace]
        process.currentDirectoryURL = source
        var environment = ProcessInfo.processInfo.environment
        for (key, value) in (settings["environment"] as? [String: String] ?? [:]) {
            environment[key] = resolve(value)
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        let logDirectory = URL(fileURLWithPath: workspace).appendingPathComponent(".film")
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        let log = logDirectory.appendingPathComponent("desktop.log")
        if !FileManager.default.fileExists(atPath: log.path) {
            FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        let errorFile = try FileHandle(forWritingTo: log)
        try errorFile.seekToEnd()
        process.standardError = errorFile
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            self?.queue.async { [weak self] in self?.consume(data) }
        }
        process.terminationHandler = { [weak self] process in
            self?.queue.async { [weak self] in
                guard let self else { return }
                let callbacks = self.pending.values
                self.pending.removeAll()
                DispatchQueue.main.async {
                    for callback in callbacks { callback(.failure(NativeFailure(message: "The editing backend stopped."))) }
                    self.onExit?(process.terminationStatus)
                }
            }
        }
        try process.run()
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let end = buffer.firstIndex(of: 10) {
            let line = buffer.subdata(in: buffer.startIndex..<end)
            buffer.removeSubrange(buffer.startIndex...end)
            guard let message = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else { continue }
            if message["event"] as? String == "ready" {
                DispatchQueue.main.async { [weak self] in self?.onReady?() }
            } else if let id = message["id"] as? Int, let callback = pending.removeValue(forKey: id) {
                let result: Result<Any, Error>
                if let error = message["error"] as? String { result = .failure(NativeFailure(message: error)) }
                else { result = .success(message["result"] ?? NSNull()) }
                DispatchQueue.main.async { callback(result) }
            }
        }
    }

    func request(operation: String = "api", project: String = "legacy", body: [String: Any] = [:],
                 route: String? = nil, completion: @escaping (Result<Any, Error>) -> Void) {
        queue.async {
            guard self.process.isRunning else {
                DispatchQueue.main.async { completion(.failure(NativeFailure(message: "FilmEditor is starting or has stopped."))) }
                return
            }
            self.nextID += 1
            let id = self.nextID
            var message: [String: Any] = ["id": id, "operation": operation, "project": project]
            if let route { message["route"] = route }
            // The absence of body means GET; callers put POST payload in body["body"].
            if operation == "api" { if let payload = body["body"] { message["body"] = payload } }
            else { message["body"] = body }
            self.pending[id] = completion
            do {
                var data = try JSONSerialization.data(withJSONObject: message)
                data.append(10)
                try self.input.fileHandleForWriting.write(contentsOf: data)
            } catch {
                self.pending.removeValue(forKey: id)
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    func shutdown() {
        queue.async {
            self.output.fileHandleForReading.readabilityHandler = nil
            try? self.input.fileHandleForWriting.close()
        }
    }
}

final class LocalScheme: NSObject, WKURLSchemeHandler {
    let resources: URL
    let backend: Backend
    // All access and WKURLSchemeTask callbacks stay on the main thread. Once WebKit
    // calls stop, the task is removed before any queued chunk can call it again.
    private var active: Set<ObjectIdentifier> = []
    private(set) var mediaRequests = 0
    private(set) var rangeRequests = 0
    private(set) var streamedBytes = 0
    init(resources: URL, backend: Backend) { self.resources = resources; self.backend = backend }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let id = ObjectIdentifier(task)
        active.insert(id)
        guard let url = task.request.url, url.scheme == "filmapp", url.host == "app" else {
            fail(task, NativeFailure(message: "Unsupported application resource.")); return
        }
        if url.path == "/media" {
            mediaRequests += 1
            backend.request(operation: "media", body: ["url": url.absoluteString]) { [weak self] result in
                guard let self, self.active.contains(id) else { return }
                do {
                    guard let value = try result.get() as? [String: Any],
                          let path = value["path"] as? String,
                          let size = value["size"] as? Int,
                          let mime = value["mime"] as? String else {
                        throw NativeFailure(message: "Invalid media response.")
                    }
                    self.stream(task, file: URL(fileURLWithPath: path), size: size, mime: mime)
                } catch { self.fail(task, error) }
            }
            return
        }
        let files = ["/": "index.html", "/index.html": "index.html", "/app.js": "app.js",
                     "/desktop.js": "desktop.js", "/style.css": "style.css", "/music.js": "music.js",
                     "/music.css": "music.css", "/advanced.js": "advanced.js", "/advanced.css": "advanced.css", "/quickstart.js": "quickstart.js", "/quickstart.css": "quickstart.css", "/assistant.js": "assistant.js", "/assistant.css": "assistant.css"]
        guard let name = files[url.path] else { fail(task, NativeFailure(message: "Resource not found.")); return }
        do {
            let file = resources.appendingPathComponent("app/web/" + name)
            let data = try Data(contentsOf: file)
            let mime = name.hasSuffix(".js") ? "application/javascript" : name.hasSuffix(".css") ? "text/css" : "text/html"
            let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: [
                "Content-Type": mime + "; charset=utf-8", "Content-Length": String(data.count),
                "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self' filmapp:; script-src 'self' filmapp:; style-src 'self' 'unsafe-inline' filmapp:; img-src 'self' filmapp: data: blob:; media-src 'self' filmapp: blob:; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'self' filmapp:"
            ])!
            task.didReceive(response); task.didReceive(data); task.didFinish(); active.remove(id)
        } catch { fail(task, error) }
    }

    private func stream(_ task: WKURLSchemeTask, file: URL, size: Int, mime: String) {
        let id = ObjectIdentifier(task)
        guard active.contains(id), let url = task.request.url else { return }
        var start = 0, end = max(0, size - 1), status = 200
        if let range = task.request.value(forHTTPHeaderField: "Range") {
            rangeRequests += 1
            let fields = range.hasPrefix("bytes=") ? String(range.dropFirst(6)).split(separator: "-", omittingEmptySubsequences: false) : []
            guard fields.count == 2, !range.contains(",") else { fail(task, NativeFailure(message: "Unsupported media range.")); return }
            if fields[0].isEmpty, let suffix = Int(fields[1]), suffix > 0 { start = max(0, size - suffix) }
            else if let first = Int(fields[0]) {
                start = first
                if !fields[1].isEmpty {
                    guard let last = Int(fields[1]) else { fail(task, NativeFailure(message: "Invalid media range.")); return }
                    end = min(end, last)
                }
            } else { fail(task, NativeFailure(message: "Invalid media range.")); return }
            status = 206
        }
        guard size > 0, start >= 0, start <= end, start < size else {
            let response = HTTPURLResponse(url: url, statusCode: 416, httpVersion: "HTTP/1.1",
                                           headerFields: ["Content-Range": "bytes */\(size)", "Content-Length": "0"])!
            task.didReceive(response); task.didFinish(); active.remove(id); return
        }
        let length = end - start + 1
        var headers = ["Content-Type": mime, "Content-Length": String(length), "Accept-Ranges": "bytes",
                       "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"]
        if status == 206 { headers["Content-Range"] = "bytes \(start)-\(end)/\(size)" }
        task.didReceive(HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                let handle = try FileHandle(forReadingFrom: file)
                defer { try? handle.close() }
                try handle.seek(toOffset: UInt64(start))
                var remaining = length
                while remaining > 0 {
                    var alive = false
                    DispatchQueue.main.sync { alive = self.active.contains(id) }
                    if !alive { return }
                    guard let bytes = try handle.read(upToCount: min(64 * 1024, remaining)), !bytes.isEmpty else {
                        throw NativeFailure(message: "Media read was incomplete.")
                    }
                    remaining -= bytes.count
                    DispatchQueue.main.sync {
                        if self.active.contains(id) { task.didReceive(bytes); self.streamedBytes += bytes.count }
                    }
                }
                DispatchQueue.main.async {
                    if self.active.remove(id) != nil { task.didFinish() }
                }
            } catch { DispatchQueue.main.async { self.fail(task, error) } }
        }
    }

    private func fail(_ task: WKURLSchemeTask, _ error: Error) {
        if active.remove(ObjectIdentifier(task)) != nil { task.didFailWithError(error) }
    }
    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) { active.remove(ObjectIdentifier(task)) }
}

final class NativeWebView: WKWebView {
    var onFilesDropped: (([URL], CGPoint) -> Void)?
    private func fileURLs(_ sender: NSDraggingInfo) -> [URL] {
        sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        if !fileURLs(sender).isEmpty { return .copy }
        return super.draggingEntered(sender)
    }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = fileURLs(sender)
        if !urls.isEmpty {
            let point = convert(sender.draggingLocation, from: nil)
            onFilesDropped?(urls, CGPoint(x: point.x, y: isFlipped ? point.y : bounds.height - point.y)); return true
        }
        return super.performDragOperation(sender)
    }
}

final class Application: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    let backend = Backend()
    var window: NSWindow!
    var webView: NativeWebView!
    var scheme: LocalScheme!
    var terminating = false
    var ready = false
    var smokeStarted = false
    var workspace = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Movies/FilmEditor").path
    var initialProject: String?
    var smokeScript: String?
    var smokeReport: String?
    var smokeExit = false
    var launchErrors: [String] = []
    var navigationGeneration = 0
    var currentProject: String {
        URLComponents(url: webView.url ?? URL(string: "filmapp://app/")!, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "project" })?.value ?? "legacy"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = CommandLine.arguments
        for index in args.indices {
            if index + 1 < args.count {
                if args[index] == "--workspace" { workspace = args[index + 1] }
                if args[index] == "--project" { initialProject = args[index + 1] }
                if args[index] == "--smoke-script" { smokeScript = args[index + 1] }
                if args[index] == "--smoke-report" { smokeReport = args[index + 1] }
            }
            if args[index] == "--smoke-exit" { smokeExit = true }
        }
        guard let resources = Bundle.main.resourceURL else { return }
        NSApp.setActivationPolicy(.regular)
        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.userContentController.add(self, name: "film")
        scheme = LocalScheme(resources: resources, backend: backend)
        configuration.setURLSchemeHandler(scheme, forURLScheme: "filmapp")
        webView = NativeWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self; webView.uiDelegate = self
        webView.registerForDraggedTypes([.fileURL])
        webView.onFilesDropped = { [weak self] urls, point in self?.importURLs(urls, point: point) }
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 850),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "FilmEditor"; window.minSize = NSSize(width: 980, height: 640)
        window.contentView = webView; window.delegate = self
        window.center(); window.setFrameAutosaveName("FilmEditorMainWindow")
        menu()
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        webView.loadHTMLString("<html><body style='background:#14171c;color:#eef1f5;font:16px system-ui;padding:40px'>Starting FilmEditor…</body></html>", baseURL: nil)
        backend.onReady = { [weak self] in
            guard let self else { return }; self.ready = true
            var components = URLComponents(string: "filmapp://app/")!
            if let project = self.initialProject { components.queryItems = [URLQueryItem(name: "project", value: project)] }
            self.webView.load(URLRequest(url: components.url!))
        }
        backend.onExit = { [weak self] status in
            guard let self, !self.terminating else { return }
            self.showError("FilmEditor’s backend exited (\(status)). See the workspace .film/desktop.log for details.")
        }
        do { try backend.launch(resources: resources, workspace: workspace) }
        catch { showError(error.localizedDescription) }
    }

    func resolve(_ id: String, _ result: Result<Any, Error>) {
        let value: [Any]
        switch result {
        case .success(let data): value = [id, data, NSNull()]
        case .failure(let error): value = [id, NSNull(), error.localizedDescription]
        }
        guard let data = try? JSONSerialization.data(withJSONObject: value), let literal = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.FilmDesktop?._resolve(..." + literal + ")", completionHandler: nil)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, message.frameInfo.securityOrigin.protocol == "filmapp",
              message.frameInfo.securityOrigin.host == "app", webView.url?.scheme == "filmapp", webView.url?.host == "app",
              let senderURL = message.frameInfo.request.url, senderURL.scheme == "filmapp", senderURL.host == "app",
              let value = message.body as? [String: Any], let action = value["action"] as? String else { return }
        let senderProject = URLComponents(url: senderURL, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "project" })?.value ?? "legacy"
        let id = value["id"] as? String ?? ""
        let body = value["body"] as? [String: Any] ?? [:]
        switch action {
        case "api":
            guard let route = body["route"] as? String, route.hasPrefix("/api/"), !route.contains(":") else {
                resolve(id, .failure(NativeFailure(message: "Invalid editor route."))); return
            }
            backend.request(project: senderProject, body: body, route: route) { [weak self] in self?.resolve(id, $0) }
        case "importDialog": importDialog(id: id, kind: body["kind"] as? String ?? "footage", project: senderProject)
        case "upload": backend.request(operation: "upload", project: senderProject, body: body) { [weak self] in self?.resolve(id, $0) }
        case "cancelUploads": backend.request(operation: "cancelUploads", project: senderProject) { [weak self] in self?.resolve(id, $0) }
        case "saveMedia": saveMedia(id: id, body: body)
        case "revealWorkspace":
            backend.request(operation: "workspace", project: senderProject) { [weak self] result in
                if case .success(let value) = result, let data = value as? [String: Any], let path = data["path"] as? String {
                    NSWorkspace.shared.open(URL(fileURLWithPath: path)); self?.resolve(id, .success(["opened": true]))
                } else { self?.resolve(id, result) }
            }
        case "smokeComplete": if smokeReport != nil { finishSmoke(body) }
        default: resolve(id, .failure(NativeFailure(message: "Unsupported native action.")))
        }
    }

    private func importDialog(id: String, kind: String, project: String) {
        guard project != "legacy" || webView.url?.query?.contains("project=") == true else {
            resolve(id, .failure(NativeFailure(message: "Open a project before importing media."))); return
        }
        let panel = NSOpenPanel(); panel.title = "Import media into FilmEditor"
        panel.canChooseDirectories = false; panel.allowsMultipleSelection = true
        let extensions = ["mp4", "mov", "m4v", "mkv", "webm", "avi", "mts", "mxf", "wav", "mp3", "m4a", "flac", "aac", "ogg", "opus", "aif", "aiff", "wma", "alac", "png", "jpg", "jpeg", "webp", "heic", "heif", "cube", "srt", "vtt"]
        panel.allowedContentTypes = extensions.compactMap { UTType(filenameExtension: $0) }
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self else { return }
            if response != .OK { self.resolve(id, .success([])); return }
            self.backend.request(operation: "import", project: project,
                                 body: ["paths": panel.urls.map(\.path), "kind": kind]) { self.resolve(id, $0) }
        }
    }

    private func importURLs(_ urls: [URL], point: CGPoint) {
        guard webView.url?.query?.contains("project=") == true else { showError("Open a project before importing media."); return }
        let project = currentProject
        let generation = navigationGeneration
        webView.evaluateJavaScript("document.getElementById('import-kind')?.value || 'footage'") { [weak self] kind, _ in
            guard let self else { return }
            self.backend.request(operation: "import", project: project,
                                 body: ["paths": urls.map(\.path), "kind": kind as? String ?? "footage"]) { result in
                do {
                    let assets = try result.get()
                    guard self.navigationGeneration == generation, self.currentProject == project else { return }
                    let detail: [String: Any] = ["assets": assets, "x": point.x, "y": point.y]
                    let data = try JSONSerialization.data(withJSONObject: detail)
                    let literal = String(data: data, encoding: .utf8)!
                    self.webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('filmeditor-native-drop',{detail:" + literal + "}))", completionHandler: nil)
                } catch { self.showError(error.localizedDescription) }
            }
        }
    }

    private func saveMedia(id: String, body: [String: Any]) {
        guard let url = body["url"] as? String else { resolve(id, .failure(NativeFailure(message: "Select a completed export."))); return }
        backend.request(operation: "media", body: ["url": url]) { [weak self] result in
            guard let self else { return }
            do {
                guard let value = try result.get() as? [String: Any], let path = value["path"] as? String else {
                    throw NativeFailure(message: "Export is unavailable.")
                }
                let source = URL(fileURLWithPath: path)
                let panel = NSSavePanel(); panel.title = "Save rendered file"
                panel.nameFieldStringValue = (body["name"] as? String).map { URL(fileURLWithPath: $0).lastPathComponent } ?? source.lastPathComponent
                if let type = UTType(filenameExtension: source.pathExtension) { panel.allowedContentTypes = [type] }
                panel.beginSheetModal(for: self.window) { response in
                    guard response == .OK, let destination = panel.url else { self.resolve(id, .success(["cancelled": true])); return }
                    if destination.standardizedFileURL == source.standardizedFileURL { self.resolve(id, .success(["saved": true])); return }
                    DispatchQueue.global(qos: .userInitiated).async {
                        do {
                            let temp = destination.deletingLastPathComponent().appendingPathComponent(".filmeditor-" + UUID().uuidString)
                            defer { try? FileManager.default.removeItem(at: temp) }
                            try FileManager.default.copyItem(at: source, to: temp)
                            if FileManager.default.fileExists(atPath: destination.path) {
                                _ = try FileManager.default.replaceItemAt(destination, withItemAt: temp)
                            } else { try FileManager.default.moveItem(at: temp, to: destination) }
                            DispatchQueue.main.async { self.resolve(id, .success(["saved": true, "path": destination.path])) }
                        } catch { DispatchQueue.main.async { self.resolve(id, .failure(error)) } }
                    }
                }
            } catch { self.resolve(id, .failure(error)) }
        }
    }

    func webView(_ view: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "about:blank" { decisionHandler(.allow); return }
        decisionHandler(url.scheme == "filmapp" && url.host == "app" ? .allow : .cancel)
    }
    func webView(_ view: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { nil }
    func webView(_ view: WKWebView, didFinish navigation: WKNavigation!) {
        window.title = view.title ?? "FilmEditor"
        guard ready, !smokeStarted, let smokeScript, view.url?.scheme == "filmapp" else { return }
        smokeStarted = true
        do {
            let script = try String(contentsOfFile: smokeScript, encoding: .utf8)
            view.evaluateJavaScript(script + "\n;void 0;") { [weak self] _, error in
                if let error { self?.finishSmoke(["passed": false, "error": error.localizedDescription]) }
            }
        } catch { finishSmoke(["passed": false, "error": error.localizedDescription]) }
    }
    func webView(_ view: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { navigationGeneration += 1 }
    func webView(_ view: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { launchErrors.append(error.localizedDescription) }

    func finishSmoke(_ value: [String: Any]) {
        guard let smokeReport else { return }
        var report = value
        report["native"] = true; report["transport"] = "stdio+unix"; report["urlScheme"] = webView.url?.scheme ?? ""
        report["mediaRequests"] = scheme.mediaRequests; report["rangeRequests"] = scheme.rangeRequests
        report["streamedBytes"] = scheme.streamedBytes; report["navigationErrors"] = launchErrors
        let target = URL(fileURLWithPath: smokeReport)
        webView.takeSnapshot(with: nil) { [weak self] image, error in
            guard let self else { return }
            if let image, let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try? png.write(to: target.deletingPathExtension().appendingPathExtension("png"))
            }
            if let error { report["snapshotError"] = error.localizedDescription }
            if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
                try? data.write(to: target, options: .atomic)
            }
            if self.smokeExit { NSApp.terminate(nil) }
        }
    }

    private func showError(_ message: String) {
        launchErrors.append(message)
        if smokeReport != nil { finishSmoke(["passed": false, "error": message]); return }
        let alert = NSAlert(); alert.messageText = "FilmEditor"; alert.informativeText = message
        alert.beginSheetModal(for: window, completionHandler: nil)
    }
    @objc func clickMenu(_ sender: NSMenuItem) {
        guard let identifier = sender.representedObject as? String else { return }
        if identifier == "workspace" { webView.evaluateJavaScript("window.FilmDesktop.revealWorkspace()", completionHandler: nil) }
        else if identifier == "library" { webView.load(URLRequest(url: URL(string: "filmapp://app/")!)) }
        else {
            let data = try! JSONSerialization.data(withJSONObject: [identifier])
            let literal = String(data: data, encoding: .utf8)!
            let script = "(() => { const id=" + literal + "[0], a=document.activeElement, typing=a?.isContentEditable || ['INPUT','TEXTAREA','SELECT'].includes(a?.tagName); if(typing && ['undo','redo'].includes(id)) { document.execCommand(id); return; } if(typing && ['split','duplicate','delete-clip'].includes(id)) return; document.getElementById(id)?.click(); })()"
            webView.evaluateJavaScript(script, completionHandler: nil)
        }
    }
    private func menu() {
        let bar = NSMenu(); NSApp.mainMenu = bar
        let appItem = NSMenuItem(); bar.addItem(appItem); let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About FilmEditor", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator()); appMenu.addItem(withTitle: "Hide FilmEditor", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit FilmEditor", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        func group(_ title: String, _ items: [(String, String, String)]) {
            let root = NSMenuItem(); bar.addItem(root); let submenu = NSMenu(title: title); root.submenu = submenu
            for (label, key, identifier) in items {
                let item = NSMenuItem(title: label, action: #selector(clickMenu(_:)), keyEquivalent: key)
                item.target = self; item.representedObject = identifier; submenu.addItem(item)
            }
        }
        group("File", [("Projects…", "o", "library"), ("New Project…", "n", "create-project"),
                       ("Import Media…", "i", "import-top"), ("Export Timeline…", "e", "export-top"),
                       ("Show Workspace in Finder", "", "workspace")])
        group("Edit", [("Undo", "z", "undo"), ("Redo", "Z", "redo"), ("Split Clip at Playhead", "k", "split"),
                       ("Duplicate Clip", "d", "duplicate"), ("Delete Selected Clip", "", "delete-clip")])
        if let edit = bar.items.last?.submenu {
            edit.addItem(.separator())
            for (title, selector, key) in [("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
                edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
            }
        }
        group("View", [("Render Queue…", "", "render-status"), ("Inspector", "", "toggle-inspector"),
                       ("Engine Tools…", "", "advanced-tools"), ("Diagnostics…", "", "show-diagnostics")])
        let windowItem = NSMenuItem(); bar.addItem(windowItem); let windowMenu = NSMenu(title: "Window"); windowItem.submenu = windowMenu
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        NSApp.windowsMenu = windowMenu
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating { return .terminateNow }
        terminating = true; backend.shutdown()
        DispatchQueue.global().async {
            let deadline = Date().addingTimeInterval(10)
            while self.backend.process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if self.backend.process.isRunning { self.backend.process.terminate() }
            DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }
}

let application = NSApplication.shared
let delegate = Application()
application.delegate = delegate
application.run()
