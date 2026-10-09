import AppKit
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 64, 128, 256, 512, 1024] {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                  colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let transform = NSAffineTransform(); transform.scale(by: CGFloat(size) / 1024); transform.concat()
    let base = NSBezierPath(roundedRect: NSRect(x: 24, y: 24, width: 976, height: 976), xRadius: 216, yRadius: 216)
    NSGradient(starting: NSColor(calibratedRed: 0.16, green: 0.20, blue: 0.28, alpha: 1),
               ending: NSColor(calibratedRed: 0.055, green: 0.07, blue: 0.10, alpha: 1))!.draw(in: base, angle: 90)
    let blue = NSColor(calibratedRed: 0.28, green: 0.64, blue: 1, alpha: 1)
    NSColor(calibratedRed: 0.26, green: 0.33, blue: 0.44, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 140, y: 468, width: 744, height: 370), xRadius: 42, yRadius: 42).fill()
    NSColor(calibratedRed: 0.055, green: 0.08, blue: 0.13, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 224, y: 500, width: 576, height: 306), xRadius: 20, yRadius: 20).fill()
    for x in [160, 822] { for y in [524, 636, 748] {
        NSColor(calibratedRed: 0.58, green: 0.68, blue: 0.82, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: x, y: y, width: 42, height: 54), xRadius: 8, yRadius: 8).fill()
    }}
    blue.setFill()
    let play = NSBezierPath(); play.move(to: NSPoint(x: 448, y: 566)); play.line(to: NSPoint(x: 448, y: 748));
    play.line(to: NSPoint(x: 616, y: 656)); play.close(); play.fill()
    for (y, width) in [(346, 590), (254, 408), (162, 680)] {
        blue.withAlphaComponent(y == 254 ? 0.65 : 0.9).setFill()
        NSBezierPath(roundedRect: NSRect(x: 172, y: y, width: width, height: 54), xRadius: 14, yRadius: 14).fill()
    }
    NSColor.white.setFill(); NSBezierPath(rect: NSRect(x: 352, y: 138, width: 12, height: 290)).fill()
    let marker = NSBezierPath(); marker.move(to: NSPoint(x: 334, y: 432)); marker.line(to: NSPoint(x: 382, y: 432));
    marker.line(to: NSPoint(x: 358, y: 408)); marker.close(); marker.fill()
    NSGraphicsContext.restoreGraphicsState()
    let png = bitmap.representation(using: .png, properties: [:])!
    if size <= 512 { try png.write(to: directory.appendingPathComponent("icon_\(size)x\(size).png")) }
    if size >= 32 { try png.write(to: directory.appendingPathComponent("icon_\(size/2)x\(size/2)@2x.png")) }
}
