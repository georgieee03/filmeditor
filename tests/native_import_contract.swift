// Compile with native/MediaImport.swift; no Photos library or UI access.
import AppKit
import UniformTypeIdentifiers

struct NativeFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class ResultBox<Value> {
    private let lock = NSLock()
    private var stored: Value?
    var value: Value? { lock.lock(); defer { lock.unlock() }; return stored }
    func set(_ value: Value) { lock.lock(); stored = value; lock.unlock() }
}

final class PromisedFixture: NSFilePromiseReceiver {
    let names: [String]
    var called = false
    var destination: URL?
    let fail: Bool
    let reverseDelivery: Bool
    init(_ names: [String], fail: Bool = false, reverseDelivery: Bool = false) {
        self.names = names; self.fail = fail; self.reverseDelivery = reverseDelivery; super.init()
    }
    required init?(pasteboardPropertyList propertyList: Any, ofType type: NSPasteboard.PasteboardType) { fatalError("Unused fixture initializer") }
    override var fileNames: [String] { called ? names : [] }
    override func receivePromisedFiles(atDestination destination: URL, options: [AnyHashable: Any] = [:], operationQueue: OperationQueue,
                                       reader: @escaping (URL, Error?) -> Void) {
        precondition(operationQueue.isSuspended, "Completion must be counted before callbacks can run")
        called = true; self.destination = destination
        for name in reverseDelivery ? names.reversed().map({ $0 }) : names {
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
        precondition(NativeMediaFiles.preferredType(provider) == UTType.movie.identifier)
        let providerResult = ResultBox<Result<[URL], Error>>()
        NativeMediaFiles.receiveProviders([provider], into: target) { providerResult.set($0) }
        wait { providerResult.value != nil }
        let urls = try providerResult.value!.get()
        precondition(urls.count == 1 && urls[0] != movie)
        try FileManager.default.removeItem(at: movie)
        let copied = try Data(contentsOf: urls[0]); precondition(copied == Data("movie bytes".utf8))
        let first = PromisedFixture(["one.mov", "two.mov"], reverseDelivery: true), second = PromisedFixture(["three.mov"])
        let promiseReportURL = target.appendingPathComponent("promise-events.jsonl")
        let promiseReport = try NativeImportReport(path: promiseReportURL.path)
        let promiseResult = ResultBox<Result<[URL], Error>>()
        NativeMediaFiles.receivePromises([first, second], into: target, report: promiseReport, trace: "fixture-drag") { promiseResult.set($0) }
        precondition(first.called && second.called, "File promise negotiation must start before the receiving call returns")
        wait { promiseResult.value != nil }
        let promised = try promiseResult.value!.get()
        precondition(promised.map(\.lastPathComponent) == ["one.mov", "two.mov", "three.mov"], "Selection order must survive out-of-order delivery")
        precondition(first.destination == target && second.destination == target)
        promiseReport.flush()
        let promiseEvents = try String(contentsOf: promiseReportURL, encoding: .utf8).split(separator: "\n").map {
            try JSONSerialization.jsonObject(with: Data($0.utf8)) as! [String: Any]
        }
        precondition(promiseEvents.filter { $0["event"] as? String == "promise-request-returned" }.map { $0["expectedCallbacks"] as! Int } == [2, 1])
        precondition(promiseEvents.filter { $0["event"] as? String == "promise-reader" }.count == 3)
        precondition(promiseEvents.last?["event"] as? String == "promise-batch-finished")
        let failed = ResultBox<Result<[URL], Error>>()
        NativeMediaFiles.receivePromises([PromisedFixture(["partial.mov"], fail: true)], into: target) { failed.set($0) }
        wait { failed.value != nil }
        if case .success = failed.value! { preconditionFailure("Failed promise must not import partial data") }

        let failedMovie = NSItemProvider()
        let posterLoaded = ResultBox<Bool>()
        failedMovie.registerFileRepresentation(forTypeIdentifier: UTType.jpeg.identifier, fileOptions: [], visibility: .all) { done in
            posterLoaded.set(true); done(nil, false, NativeFailure(message: "Poster must never be loaded")); return nil
        }
        failedMovie.registerFileRepresentation(forTypeIdentifier: UTType.quickTimeMovie.identifier, fileOptions: [], visibility: .all) { done in
            done(nil, false, NativeFailure(message: "Movie download failed")); return nil
        }
        let movieFailure = ResultBox<Result<[URL], Error>>()
        NativeMediaFiles.receiveProviders([failedMovie], into: target) { movieFailure.set($0) }
        wait { movieFailure.value != nil }
        if case .success = movieFailure.value! { preconditionFailure("A failed movie must not become a poster import") }
        precondition(posterLoaded.value == nil)

        let cancelled = NativeImportCancellation(); cancelled.cancel()
        let cancelledResult = ResultBox<Result<[URL], Error>>()
        NativeMediaFiles.receiveProviders([failedMovie], into: target, cancellation: cancelled) { cancelledResult.set($0) }
        wait { cancelledResult.value != nil }
        if case .success = cancelledResult.value! { preconditionFailure("Cancelled imports must not load a provider") }
        let progress = Progress(totalUnitCount: 1); cancelled.track(progress)
        precondition(progress.isCancelled, "Late progress must observe cancellation")

        let lateKind = NativeImportKind(); var backendKinds: [String] = []
        lateKind.whenReady { backendKinds.append($0) }
        precondition(backendKinds.isEmpty, "Backend import must await UI metadata without delaying promise negotiation")
        lateKind.complete("reference"); lateKind.complete("audio")
        precondition(backendKinds == ["reference"], "A metadata reply must resolve the import exactly once")
        let earlyKind = NativeImportKind(); earlyKind.complete("audio")
        earlyKind.whenReady { backendKinds.append($0) }
        precondition(backendKinds == ["reference", "audio"], "A reply arriving before files must also be preserved")

        let reportURL = target.appendingPathComponent("import.jsonl")
        let report = try NativeImportReport(path: reportURL.path)
        report.record("files", ["files": NativeImportReport.files(urls)])
        for index in 0..<1100 { report.record("bounded", ["index": index]) }
        report.flush()
        let lines = try String(contentsOf: reportURL, encoding: .utf8).split(separator: "\n")
        precondition(lines.count == 1000, "Diagnostics must stay bounded")
        let metadata = try JSONSerialization.jsonObject(with: Data(lines[0].utf8)) as! [String: Any]
        let files = metadata["files"] as! [[String: Any]]
        precondition(files[0]["name"] as? String == "clip.mov" && files[0]["bytes"] as? Int == 11)
        precondition(files[0]["extensionType"] as? String == UTType.quickTimeMovie.identifier)
        precondition(!String(lines[0]).contains(target.path), "Diagnostics must omit full source paths")
        do { _ = try NativeImportReport(path: "relative.jsonl"); preconditionFailure("Relative report paths must be rejected") }
        catch {}
        print("PASS: generic movie over poster; temporary URL copied; synchronous ordered promises; shared destination; failures and cancellation; early/late import kind; per-receiver diagnostics; bounded metadata report")
    }
}
