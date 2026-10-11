import AppKit
import PhotosUI
import UniformTypeIdentifiers

/// Opt-in acceptance diagnostics. Only call-site-selected metadata is written;
/// file contents, full source paths and library identifiers are never recorded.
final class NativeImportReport {
    private let queue = DispatchQueue(label: "local.filmeditor.import-report")
    private let handle: FileHandle
    private let session = UUID().uuidString
    private let lock = NSLock()
    private var remainingEvents = 1000
    private var remainingBytes = 4 * 1024 * 1024

    init(path: String) throws {
        guard NSString(string: path).isAbsolutePath else {
            throw NativeFailure(message: "The import report needs an absolute file path.")
        }
        let url = URL(fileURLWithPath: path)
        if !FileManager.default.fileExists(atPath: url.path) {
            guard FileManager.default.createFile(atPath: url.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
                throw NativeFailure(message: "The import report could not be created.")
            }
        }
        handle = try FileHandle(forWritingTo: url)
        try handle.seekToEnd()
    }

    func record(_ event: String, _ fields: [String: Any] = [:]) {
        var record = fields
        record["event"] = event; record["session"] = session
        record["time"] = Date().timeIntervalSince1970
        guard var data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]), data.count <= 64 * 1024 else { return }
        data.append(10)
        lock.lock()
        guard remainingEvents > 0, data.count <= remainingBytes else { lock.unlock(); return }
        remainingEvents -= 1; remainingBytes -= data.count
        lock.unlock()
        let line = data
        queue.async { try? self.handle.write(contentsOf: line) }
    }

    /// Call away from the UI when quitting; also supports deterministic checks.
    func flush() { queue.sync {} }

    static func files(_ urls: [URL]) -> [[String: Any]] {
        urls.prefix(200).map { url in
            var value: [String: Any] = ["name": String(url.lastPathComponent.prefix(256))]
            if let attributes = try? FileManager.default.attributesOfItem(atPath: url.path), let size = attributes[.size] as? NSNumber {
                value["bytes"] = size
            }
            if let type = UTType(filenameExtension: url.pathExtension) { value["extensionType"] = type.identifier }
            return value
        }
    }
}

/// The system picker exposes only selected items; it needs no library-wide permission.
final class PhotosMediaPicker: NSObject, PHPickerViewControllerDelegate {
    private let parent: NSWindow
    private let panel: NSWindow
    private let completion: ([NSItemProvider]) -> Void
    private let report: NativeImportReport?

    init(parent: NSWindow, report: NativeImportReport? = nil, completion: @escaping ([NSItemProvider]) -> Void) {
        self.parent = parent; self.completion = completion; self.report = report
        var configuration = PHPickerConfiguration()
        configuration.filter = .any(of: [.videos, .images])
        configuration.selectionLimit = 200
        configuration.selection = .ordered
        configuration.preferredAssetRepresentationMode = .current
        let picker = PHPickerViewController(configuration: configuration)
        panel = NSWindow(contentViewController: picker)
        panel.title = "Import from Photos"
        panel.styleMask = [.titled, .resizable]
        panel.setContentSize(NSSize(width: 900, height: 640))
        super.init()
        picker.delegate = self
    }

    func present() {
        parent.beginSheet(panel)
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.report?.record("picker-presented", ["canBecomeKey": self.panel.canBecomeKey,
                                                     "isKeyWindow": self.panel.isKeyWindow,
                                                     "applicationActive": NSApp.isActive,
                                                     "attachedSheet": self.parent.attachedSheet === self.panel])
        }
    }

    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        report?.record("picker-finished", ["count": results.count])
        parent.endSheet(panel); panel.orderOut(nil)
        completion(results.map(\.itemProvider))
    }
}

final class NativeImportCancellation {
    private let lock = NSLock()
    private var stopped = false
    private var progress: [Progress] = []
    var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func track(_ item: Progress) { lock.lock(); let cancel = stopped; if !cancel { progress.append(item) }; lock.unlock(); if cancel { item.cancel() } }
    func cancel() { lock.lock(); stopped = true; let items = progress; progress.removeAll(); lock.unlock(); items.forEach { $0.cancel() } }
}

/// The drop must negotiate file promises immediately. UI metadata may arrive
/// later, so gate only the backend import on this one-shot value.
final class NativeImportKind {
    private let lock = NSLock()
    private var value: String?
    private var waiters: [(String) -> Void] = []

    func complete(_ kind: String) {
        lock.lock()
        guard value == nil else { lock.unlock(); return }
        value = kind; let callbacks = waiters; waiters.removeAll()
        lock.unlock()
        callbacks.forEach { $0(kind) }
    }

    func whenReady(_ callback: @escaping (String) -> Void) {
        lock.lock()
        if let value { lock.unlock(); callback(value) }
        else { waiters.append(callback); lock.unlock() }
    }
}

enum NativeMediaFiles {
    static func stagingDirectory() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("filmeditor-import-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        return directory
    }

    /// Movies must win over their image/poster representations.
    static func preferredType(_ provider: NSItemProvider) -> String? {
        for family in [UTType.movie, .image, .audio] {
            // .current still permits transcoding when a concrete encoding is
            // requested. Ask for the generic family to keep the current format.
            if provider.hasItemConformingToTypeIdentifier(family.identifier) { return family.identifier }
        }
        return nil
    }

    /// Copy each temporary provider URL inside its callback, before it expires.
    /// Sequential loading bounds memory and preserves the user's selection order.
    static func receiveProviders(_ providers: [NSItemProvider], into directory: URL, cancellation: NativeImportCancellation = NativeImportCancellation(),
                                 completion: @escaping (Result<[URL], Error>) -> Void) {
        func next(_ index: Int, _ urls: [URL]) {
            guard !cancellation.isCancelled else { completion(.failure(NativeFailure(message: "Import cancelled."))); return }
            guard index < providers.count else { completion(.success(urls)); return }
            let provider = providers[index]
            guard let type = preferredType(provider) else {
                completion(.failure(NativeFailure(message: "Photos did not provide a video or image file. Export its unmodified original from Photos, then import that file."))); return
            }
            let progress = provider.loadFileRepresentation(forTypeIdentifier: type) { url, error in
                do {
                    guard !cancellation.isCancelled else { throw NativeFailure(message: "Import cancelled.") }
                    if let error { throw error }
                    guard let url else { throw NativeFailure(message: "Photos could not download this original. Open it in Photos and wait for its iCloud download, then try again.") }
                    let folder = directory.appendingPathComponent(String(index), isDirectory: true)
                    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                    let destination = folder.appendingPathComponent(url.lastPathComponent)
                    try FileManager.default.copyItem(at: url, to: destination)
                    next(index + 1, urls + [destination])
                } catch { completion(.failure(error)) }
            }
            cancellation.track(progress)
        }
        next(0, [])
    }

    static func receivePromises(_ receivers: [NSFilePromiseReceiver], into directory: URL,
                                report: NativeImportReport? = nil, trace: String? = nil,
                                completion: @escaping (Result<[URL], Error>) -> Void) {
        let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1; queue.isSuspended = true
        let group = DispatchGroup(); let lock = NSLock()
        var urls: [(Int, Int, URL)] = []; var firstError: Error?
        var expectedCounts = [Int](repeating: 0, count: receivers.count)
        var readerCounts = [Int](repeating: 0, count: receivers.count)
        func record(_ event: String, _ fields: [String: Any] = [:]) {
            guard let report else { return }
            var metadata = fields
            if let trace { metadata["import"] = trace }
            report.record(event, metadata)
        }
        for (index, receiver) in receivers.enumerated() {
            // One shared destination per drag is required by AppKit. fileNames
            // becomes available only AFTER receivePromisedFiles is called.
            record("promise-request-started", ["receiver": index, "fileTypes": Array(receiver.fileTypes.prefix(100))])
            receiver.receivePromisedFiles(atDestination: directory, options: [:], operationQueue: queue) { url, error in
                lock.lock()
                readerCounts[index] += 1; let callbackNumber = readerCounts[index]
                if let error { firstError = firstError ?? error }
                else { urls.append((index, receiver.fileNames.firstIndex(of: url.lastPathComponent) ?? Int.max, url)) }
                lock.unlock()
                if let report {
                    var metadata: [String: Any] = ["receiver": index, "callback": callbackNumber, "failed": error != nil]
                    if let trace { metadata["import"] = trace }
                    if let error { metadata["errorType"] = String(reflecting: type(of: error)) }
                    else { metadata["files"] = NativeImportReport.files([url]) }
                    report.record("promise-reader", metadata)
                }
                group.leave()
            }
            expectedCounts[index] = max(1, receiver.fileNames.count)
            record("promise-request-returned", ["receiver": index, "filenameCount": receiver.fileNames.count,
                                                "expectedCallbacks": expectedCounts[index],
                                                "fileNames": receiver.fileNames.prefix(200).map { String(URL(fileURLWithPath: $0).lastPathComponent.prefix(256)) }])
            for _ in 0..<expectedCounts[index] { group.enter() }
        }
        queue.isSuspended = false
        record("promise-queue-resumed", ["expectedCallbacks": expectedCounts.reduce(0, +)])
        group.notify(queue: .main) {
            record("promise-batch-finished", ["expectedCallbacks": expectedCounts, "receivedCallbacks": readerCounts, "failed": firstError != nil])
            if let error = firstError { completion(.failure(error)) }
            else { completion(.success(urls.sorted { ($0.0, $0.1) < ($1.0, $1.1) }.map(\.2))) }
            _ = queue; _ = receivers // Retain providers until delivery has finished.
        }
    }
}
