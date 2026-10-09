// Compile with native/MediaImport.swift; no Photos library or UI access.
import AppKit
import UniformTypeIdentifiers

struct NativeFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class PromisedFixture: NSFilePromiseReceiver {
    let names: [String]
    var called = false
    var destination: URL?
    let fail: Bool
    init(_ names: [String], fail: Bool = false) { self.names = names; self.fail = fail; super.init() }
    required init?(pasteboardPropertyList propertyList: Any, ofType type: NSPasteboard.PasteboardType) { fatalError("Unused fixture initializer") }
    override var fileNames: [String] { called ? names : [] }
    override func receivePromisedFiles(atDestination destination: URL, options: [AnyHashable: Any] = [:], operationQueue: OperationQueue,
                                       reader: @escaping (URL, Error?) -> Void) {
        precondition(operationQueue.isSuspended, "Completion must be counted before callbacks can run")
        called = true; self.destination = destination
        for name in names {
            operationQueue.addOperation {
                let url = destination.appendingPathComponent(name)
                try! Data(name.utf8).write(to: url)
                reader(url, self.fail ? NativeFailure(message: "Provider failed") : nil)
            }
        }
    }
}

@main struct NativeImportContract {
    static func wait(_ finished: () -> Bool) {
        let deadline = Date().addingTimeInterval(10)
        while !finished() && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.01)) }
        precondition(finished(), "Import completion timed out")
    }
    static func main() throws {
        let source = try NativeMediaFiles.stagingDirectory()
        let target = try NativeMediaFiles.stagingDirectory()
        defer { try? FileManager.default.removeItem(at: source); try? FileManager.default.removeItem(at: target) }
        let movie = source.appendingPathComponent("clip.mov")
        try Data("movie bytes".utf8).write(to: movie)
        let provider = NSItemProvider()
        provider.registerFileRepresentation(forTypeIdentifier: UTType.jpeg.identifier, fileOptions: [], visibility: .all) { done in
            done(nil, false, NativeFailure(message: "Poster must never be loaded")); return nil
        }
        provider.registerFileRepresentation(forTypeIdentifier: UTType.quickTimeMovie.identifier, fileOptions: [], visibility: .all) { done in
            done(movie, false, nil); return nil
        }
        precondition(NativeMediaFiles.preferredType(provider) == UTType.quickTimeMovie.identifier)
        var providerResult: Result<[URL], Error>?
        NativeMediaFiles.receiveProviders([provider], into: target) { providerResult = $0 }
        wait { providerResult != nil }
        let urls = try providerResult!.get()
        precondition(urls.count == 1 && urls[0] != movie)
        try FileManager.default.removeItem(at: movie)
        let copied = try Data(contentsOf: urls[0]); precondition(copied == Data("movie bytes".utf8))
        let first = PromisedFixture(["one.mov", "two.mov"]), second = PromisedFixture(["three.mov"])
        var promiseResult: Result<[URL], Error>?
        NativeMediaFiles.receivePromises([first, second], into: target) { promiseResult = $0 }
        wait { promiseResult != nil }
        let promised = try promiseResult!.get(); precondition(promised.count == 3)
        precondition(first.destination == target && second.destination == target)
        var failed: Result<[URL], Error>?
        NativeMediaFiles.receivePromises([PromisedFixture(["partial.mov"], fail: true)], into: target) { failed = $0 }
        wait { failed != nil }
        if case .success = failed! { preconditionFailure("Failed promise must not import partial data") }
        print("PASS: movie preferred over poster; temporary URL copied; multi-file promise; shared destination; failed promise rejected")
    }
}
