import AppKit
import PhotosUI
import UniformTypeIdentifiers

/// The system picker exposes only selected items; it needs no library-wide permission.
final class PhotosMediaPicker: NSObject, PHPickerViewControllerDelegate {
    private let parent: NSWindow
    private let panel: NSWindow
    private let completion: ([NSItemProvider]) -> Void

    init(parent: NSWindow, completion: @escaping ([NSItemProvider]) -> Void) {
        self.parent = parent; self.completion = completion
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

    func present() { parent.beginSheet(panel) }

    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
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
            if let type = provider.registeredTypeIdentifiers.first(where: { UTType($0)?.conforms(to: family) == true }) { return type }
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
                                completion: @escaping (Result<[URL], Error>) -> Void) {
        let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1; queue.isSuspended = true
        let group = DispatchGroup(); let lock = NSLock()
        var urls: [(Int, URL)] = []; var firstError: Error?
        for (index, receiver) in receivers.enumerated() {
            // One shared destination per drag is required by AppKit. fileNames
            // becomes available only AFTER receivePromisedFiles is called.
            receiver.receivePromisedFiles(atDestination: directory, options: [:], operationQueue: queue) { url, error in
                lock.lock()
                if let error { firstError = firstError ?? error } else { urls.append((index, url)) }
                lock.unlock(); group.leave()
            }
            for _ in 0..<max(1, receiver.fileNames.count) { group.enter() }
        }
        queue.isSuspended = false
        group.notify(queue: .main) {
            if let error = firstError { completion(.failure(error)) }
            else { completion(.success(urls.sorted { $0.0 < $1.0 }.map(\.1))) }
            _ = queue; _ = receivers // Retain providers until delivery has finished.
        }
    }
}
