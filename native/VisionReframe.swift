// Local, bounded subject tracking. Only geometry leaves this helper process.
import Foundation
import AVFoundation
import Vision
import CoreGraphics

struct TrackingFailure: Error, CustomStringConvertible { let description: String }
struct Point: Codable { let time: Double; let x: Double; let y: Double }
struct Analysis: Codable {
    let version: Int
    let width: Int
    let height: Int
    let duration: Double
    let points: [Point]
    let warnings: [String]
}

func bounded(_ value: Double, _ lower: Double, _ upper: Double) -> Double { min(upper, max(lower, value)) }

func initialSubject(_ image: CGImage) throws -> CGRect? {
    let handler = VNImageRequestHandler(cgImage: image, orientation: .up)
    let people = VNDetectHumanRectanglesRequest()
    people.upperBodyOnly = false
    let faces = VNDetectFaceRectanglesRequest()
    try handler.perform([people, faces])
    // Select once; subsequent frames follow the same observation, not the largest new person.
    let humans = (people.results ?? []).filter { $0.confidence >= 0.45 }
    if let human = humans.max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }) {
        return human.boundingBox
    }
    if let face = (faces.results ?? []).max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }) {
        return face.boundingBox
    }
    let saliency = VNGenerateObjectnessBasedSaliencyImageRequest()
    try handler.perform([saliency])
    // Saliency provides an initial region only; reevaluating it each frame causes jumping.
    return saliency.results?.first?.salientObjects?.filter { $0.boundingBox.width > 0.03 && $0.boundingBox.height > 0.03 }
        .max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height })?.boundingBox
}

func signature(_ image: CGImage) -> [Double] {
    var pixels = [UInt8](repeating: 0, count: 32 * 18)
    pixels.withUnsafeMutableBytes { buffer in
        if let context = CGContext(data: buffer.baseAddress, width: 32, height: 18, bitsPerComponent: 8,
                                   bytesPerRow: 32, space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) {
            context.draw(image, in: CGRect(x: 0, y: 0, width: 32, height: 18))
        }
    }
    var histogram = [Double](repeating: 0, count: 16)
    for pixel in pixels { histogram[Int(pixel) / 16] += 1.0 / Double(pixels.count) }
    return histogram
}

func run() throws {
    var options: [String: String] = [:]
    let args = Array(CommandLine.arguments.dropFirst())
    guard args.count % 2 == 0 else { throw TrackingFailure(description: "Expected named option/value pairs.") }
    for index in stride(from: 0, to: args.count, by: 2) { options[args[index]] = args[index + 1] }
    guard let input = options["--input"], let output = options["--output"],
          ["auto", "manual"].contains(options["--subject"] ?? "auto") else {
        throw TrackingFailure(description: "Provide --input, --output and --subject auto or manual.")
    }
    let interval = bounded(Double(options["--interval"] ?? "0.333") ?? 0.333, 0.2, 2)
    let cx = bounded(Double(options["--center-x"] ?? "0.5") ?? 0.5, 0, 1)
    let cy = bounded(Double(options["--center-y"] ?? "0.5") ?? 0.5, 0, 1)
    let asset = AVURLAsset(url: URL(fileURLWithPath: input))
    let duration = CMTimeGetSeconds(asset.duration)
    guard duration.isFinite, duration > 0, duration <= 300.1,
          let track = asset.tracks(withMediaType: .video).first else {
        throw TrackingFailure(description: "Subject tracking requires a video of at most five minutes.")
    }
    let display = track.naturalSize.applying(track.preferredTransform)
    let width = Int(abs(display.width).rounded()), height = Int(abs(display.height).rounded())
    guard width > 0, height > 0 else { throw TrackingFailure(description: "Video display dimensions are missing.") }
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.apertureMode = .encodedPixels
    generator.maximumSize = CGSize(width: 720, height: 720)
    generator.requestedTimeToleranceBefore = .zero
    generator.requestedTimeToleranceAfter = .zero
    let sequence = VNSequenceRequestHandler()
    var observation: VNDetectedObjectObservation?
    var points: [Point] = []
    var warnings: [String] = []
    var previousSignature: [Double]?
    var held = false
    let samples = min(1501, Int(ceil(duration / interval)))
    for index in 0..<samples {
        try autoreleasepool {
            let requested = min(Double(index) * interval, max(0, duration - 0.04))
            var decodedTime = CMTime.zero
            let image = try generator.copyCGImage(at: CMTime(seconds: requested, preferredTimescale: 60000), actualTime: &decodedTime)
            let at = CMTimeGetSeconds(decodedTime)
            guard at.isFinite, at >= 0, at <= duration else { throw TrackingFailure(description: "Invalid decoded sample time.") }
            if let last = points.last, at <= last.time { return }
            let currentSignature = signature(image)
            if let previous = previousSignature, index > 1,
               zip(previous, currentSignature).reduce(0, { $0 + abs($1.0 - $1.1) }) > 1.3, !held {
                held = true
                warnings.append("A scene change was detected at \(String(format: "%.1f", at)) s. The crop holds its last target; split separate shots before reframing.")
            }
            previousSignature = currentSignature
            if index == 0 {
                let box: CGRect?
                if options["--subject"] == "manual" {
                    // The user nominates the center of a textured subject on the first source frame.
                    box = CGRect(x: bounded(cx - 0.12, 0, 0.76), y: bounded(1 - cy - 0.12, 0, 0.76), width: 0.24, height: 0.24)
                } else { box = try initialSubject(image) }
                guard let box, box.width > 0, box.height > 0 else {
                    throw TrackingFailure(description: "No clear subject was found. Choose a manual target center on the first source frame and try again.")
                }
                observation = VNDetectedObjectObservation(boundingBox: box)
                // Feed the initial frame too, so the sequence has a proper tracking reference.
            }
            if !held, let prior = observation {
                let request = VNTrackObjectRequest(detectedObjectObservation: prior)
                try sequence.perform([request], on: image, orientation: .up)
                if let tracked = request.results?.first as? VNDetectedObjectObservation,
                   tracked.confidence >= 0.25,
                   tracked.boundingBox.width >= 0.015, tracked.boundingBox.height >= 0.015,
                   abs(tracked.boundingBox.midX - prior.boundingBox.midX) < 0.4,
                   abs(tracked.boundingBox.midY - prior.boundingBox.midY) < 0.4 {
                    observation = tracked
                } else {
                    held = true
                    warnings.append("The subject was lost at \(String(format: "%.1f", at)) s. The crop holds its last target; review the result or choose a manual target.")
                }
            }
            guard let box = observation?.boundingBox else { throw TrackingFailure(description: "Subject tracking produced no observation.") }
            points.append(Point(time: at, x: bounded(box.midX, 0, 1), y: bounded(1 - box.midY, 0, 1)))
            if index % 15 == 0 { FileHandle.standardError.write(Data("Tracking \(index + 1)/\(samples)\n".utf8)) }
        }
    }
    if let first = points.first, first.time > 0 { points.insert(Point(time: 0, x: first.x, y: first.y), at: 0) }
    if let last = points.last, last.time < duration { points.append(Point(time: duration, x: last.x, y: last.y)) }
    let result = Analysis(version: 1, width: width, height: height, duration: duration, points: points, warnings: warnings)
    let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
    try encoder.encode(result).write(to: URL(fileURLWithPath: output), options: .atomic)
}

do { try run() } catch {
    FileHandle.standardError.write(Data((String(describing: error) + "\n").utf8))
    exit(1)
}
