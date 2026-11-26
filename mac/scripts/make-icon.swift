// Generates CineID/Assets.xcassets/AppIcon.appiconset (all macOS sizes + Contents.json).
// Usage: swift mac/scripts/make-icon.swift [output-dir]   (default: mac/CineID/Assets.xcassets/AppIcon.appiconset)
// Venus-style: art-coloured gradient, soft spotlight, one round "record" photo-disc with a match ring.
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let cs = CGColorSpace(name: CGColorSpace.sRGB)!
func rgb(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 1) -> CGColor { CGColor(colorSpace: cs, components: [r, g, b, a])! }

func render(_ px: Int) -> CGImage {
    let s = CGFloat(px) / 1024
    let ctx = CGContext(data: nil, width: px, height: px, bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.scaleBy(x: s, y: s)
    ctx.interpolationQuality = .high

    // Tile: 824pt rounded square with the standard margin and a soft drop shadow.
    let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
    let tilePath = CGPath(roundedRect: tile, cornerWidth: 186, cornerHeight: 186, transform: nil)
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -14), blur: 28, color: rgb(0, 0, 0, 0.45))
    ctx.addPath(tilePath); ctx.setFillColor(rgb(0.1, 0.05, 0.2)); ctx.fillPath()
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(tilePath); ctx.clip()
    // Gradient: violet at the top to deep amber-brown at the bottom.
    let grad = CGGradient(colorsSpace: cs, colors: [rgb(0.36, 0.20, 0.66), rgb(0.16, 0.08, 0.34), rgb(0.40, 0.18, 0.10)] as CFArray,
                          locations: [0, 0.55, 1])!
    ctx.drawLinearGradient(grad, start: CGPoint(x: 512, y: 924), end: CGPoint(x: 512, y: 100), options: [])
    // Spotlight glow behind the disc.
    let center = CGPoint(x: 512, y: 530)
    let glow = CGGradient(colorsSpace: cs, colors: [rgb(1.0, 0.72, 0.38, 0.75), rgb(0.85, 0.45, 0.55, 0.25), rgb(0.5, 0.3, 0.8, 0)] as CFArray,
                          locations: [0, 0.45, 1])!
    ctx.drawRadialGradient(glow, startCenter: center, startRadius: 20, endCenter: center, endRadius: 470, options: [])

    // Match ring (arc) around the disc.
    let ringR: CGFloat = 322
    ctx.setLineCap(.round)
    ctx.setLineWidth(26)
    ctx.setStrokeColor(rgb(1, 1, 1, 0.14))
    ctx.addArc(center: center, radius: ringR, startAngle: 0, endAngle: .pi * 2, clockwise: false); ctx.strokePath()
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 22, color: rgb(1.0, 0.7, 0.3, 0.9))
    ctx.setStrokeColor(rgb(1.0, 0.80, 0.50))
    ctx.addArc(center: center, radius: ringR, startAngle: .pi / 2, endAngle: .pi / 2 - .pi * 2 * 0.86, clockwise: true); ctx.strokePath()
    ctx.restoreGState()

    // Disc.
    let dR: CGFloat = 276
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -22), blur: 36, color: rgb(0, 0, 0, 0.55))
    ctx.setFillColor(rgb(0.05, 0.04, 0.08))
    ctx.fillEllipse(in: CGRect(x: center.x - dR, y: center.y - dR, width: dR * 2, height: dR * 2))
    ctx.restoreGState()
    // Faint colour in the disc body.
    let body = CGGradient(colorsSpace: cs, colors: [rgb(0.22, 0.13, 0.34), rgb(0.05, 0.04, 0.09)] as CFArray, locations: [0, 1])!
    ctx.saveGState()
    ctx.addEllipse(in: CGRect(x: center.x - dR, y: center.y - dR, width: dR * 2, height: dR * 2)); ctx.clip()
    ctx.drawLinearGradient(body, start: CGPoint(x: center.x - dR, y: center.y + dR), end: CGPoint(x: center.x + dR, y: center.y - dR), options: [])
    // Grooves.
    var r: CGFloat = 112
    var i = 0
    while r < dR - 8 {
        ctx.setLineWidth(1.6)
        ctx.setStrokeColor(rgb(1, 1, 1, i % 6 == 0 ? 0.13 : 0.05))
        ctx.strokeEllipse(in: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2))
        r += 9; i += 1
    }
    // Sheen: two soft wedges of light.
    for (start, sweep, alpha) in [(0.35, 0.55, 0.20), (0.35 + .pi, 0.45, 0.12)] as [(Double, Double, Double)] {
        ctx.saveGState()
        ctx.move(to: center)
        ctx.addArc(center: center, radius: dR, startAngle: start, endAngle: start + sweep, clockwise: false)
        ctx.closePath(); ctx.clip()
        let sheen = CGGradient(colorsSpace: cs, colors: [rgb(1, 1, 1, alpha), rgb(1, 1, 1, 0)] as CFArray, locations: [0, 1])!
        ctx.drawRadialGradient(sheen, startCenter: center, startRadius: 100, endCenter: center, endRadius: dR, options: [])
        ctx.restoreGState()
    }
    ctx.restoreGState()
    // Rim highlight.
    ctx.setLineWidth(5); ctx.setStrokeColor(rgb(1, 1, 1, 0.28))
    ctx.strokeEllipse(in: CGRect(x: center.x - dR + 2.5, y: center.y - dR + 2.5, width: dR * 2 - 5, height: dR * 2 - 5))

    // Label: warm disc with a person silhouette ("who is this?").
    let lR: CGFloat = 128
    let label = CGGradient(colorsSpace: cs, colors: [rgb(1.0, 0.78, 0.45), rgb(0.88, 0.45, 0.30)] as CFArray, locations: [0, 1])!
    ctx.saveGState()
    ctx.setShadow(offset: .zero, blur: 10, color: rgb(0, 0, 0, 0.6))
    ctx.setFillColor(rgb(0.04, 0.03, 0.06))
    ctx.fillEllipse(in: CGRect(x: center.x - lR - 10, y: center.y - lR - 10, width: (lR + 10) * 2, height: (lR + 10) * 2))
    ctx.restoreGState()
    ctx.saveGState()
    ctx.addEllipse(in: CGRect(x: center.x - lR, y: center.y - lR, width: lR * 2, height: lR * 2)); ctx.clip()
    ctx.drawLinearGradient(label, start: CGPoint(x: center.x, y: center.y + lR), end: CGPoint(x: center.x, y: center.y - lR), options: [])
    ctx.setFillColor(rgb(0.20, 0.10, 0.30, 0.92))
    ctx.fillEllipse(in: CGRect(x: center.x - 38, y: center.y + 0, width: 76, height: 76))                       // head
    ctx.fillEllipse(in: CGRect(x: center.x - 84, y: center.y - 150, width: 168, height: 150))                  // shoulders
    ctx.restoreGState()
    // Spindle hole.
    ctx.setFillColor(rgb(0.05, 0.03, 0.08, 1))
    ctx.fillEllipse(in: CGRect(x: center.x - 0, y: center.y - 0, width: 0, height: 0))
    ctx.restoreGState()

    // Tile edge highlight.
    ctx.addPath(tilePath); ctx.setLineWidth(3); ctx.setStrokeColor(rgb(1, 1, 1, 0.16)); ctx.strokePath()
    return ctx.makeImage()!
}

func writePNG(_ image: CGImage, to url: URL) {
    let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, image, nil)
    guard CGImageDestinationFinalize(dest) else { fatalError("could not write \(url.path)") }
}

let scriptDir = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
let out = CommandLine.arguments.count > 1
    ? URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
    : URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("mac/CineID/Assets.xcassets/AppIcon.appiconset", isDirectory: true)
_ = scriptDir
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

var images: [[String: String]] = []
for base in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let px = base * scale
        let name = "icon_\(base)x\(base)@\(scale)x.png"
        writePNG(render(px), to: out.appendingPathComponent(name))
        images.append(["idiom": "mac", "size": "\(base)x\(base)", "scale": "\(scale)x", "filename": name])
    }
}
let contents: [String: Any] = ["images": images, "info": ["version": 1, "author": "xcode"]]
let data = try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys])
try data.write(to: out.appendingPathComponent("Contents.json"))
print("wrote \(images.count) icons to \(out.path)")
