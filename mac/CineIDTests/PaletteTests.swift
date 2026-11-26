import XCTest
import AppKit
@testable import CineID

private func solidImage(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, size: Int = 32) -> NSImage {
    let space = CGColorSpaceCreateDeviceRGB()
    let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setFillColor(red: r, green: g, blue: b, alpha: 1)
    ctx.fill(CGRect(x: 0, y: 0, width: size, height: size))
    return NSImage(cgImage: ctx.makeImage()!, size: NSSize(width: size, height: size))
}

final class PaletteTests: XCTestCase {
    func testSolidRedGivesRedAccentInReadableRange() {
        let p = ArtworkPalette.from(solidImage(1, 0, 0))
        let c = p.accentRGB
        XCTAssertGreaterThan(c.r, c.g * 2, "red-ish hue")
        XCTAssertGreaterThan(c.r, c.b * 2, "red-ish hue")
        XCTAssertGreaterThan(c.relativeLuminance, 0.08, "not near-black")
        XCTAssertLessThan(c.relativeLuminance, 0.7, "not washed out")
    }

    func testSolidBlueGivesBlueAccent() {
        let c = ArtworkPalette.from(solidImage(0, 0.2, 1)).accentRGB
        XCTAssertGreaterThan(c.b, c.r * 2)
        XCTAssertGreaterThan(c.b, c.g)
    }

    func testNilImageFallsBackToWarmAmber() {
        let p = ArtworkPalette.from(nil)
        XCTAssertEqual(p, ArtworkPalette.fallback)
        let c = p.accentRGB
        XCTAssertGreaterThan(c.r, c.g)
        XCTAssertGreaterThan(c.g, c.b, "amber: red > green > blue")
    }

    func testGreyPhotoGivesNeutralAccentNotAmber() {
        let c = ArtworkPalette.from(solidImage(0.5, 0.5, 0.5)).accentRGB
        XCTAssertEqual(c.r, c.g, accuracy: 0.02)
        XCTAssertEqual(c.g, c.b, accuracy: 0.02)
        XCTAssertNotEqual(ArtworkPalette.from(solidImage(0.5, 0.5, 0.5)), ArtworkPalette.fallback)
    }

    func testSmallColourfulAreaBeatsLargeGreyArea() {
        let size = 32
        let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpaceCreateDeviceRGB(),
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.setFillColor(red: 0.5, green: 0.5, blue: 0.5, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: size, height: size))
        ctx.setFillColor(red: 0, green: 0.8, blue: 0.1, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: 10, height: 10))
        let c = ArtworkPalette.from(NSImage(cgImage: ctx.makeImage()!, size: NSSize(width: size, height: size))).accentRGB
        XCTAssertGreaterThan(c.g, c.r * 1.5)
        XCTAssertGreaterThan(c.g, c.b * 1.5)
    }

    private func ratio(_ fg: Int, _ bg: Int) -> Double { DossierTheme.contrastRatio(fg, bg) }

    /// The paper is always cream and the ink always dark, whatever the photo: AAA contrast.
    func testInkOnPaperIsReadable() {
        XCTAssertGreaterThanOrEqual(ratio(DossierTheme.inkHex, DossierTheme.paperHex), 7)
    }

    func testTypewriterLabelsAndTagsAreReadableOnPaperAndFolder() {
        XCTAssertGreaterThanOrEqual(ratio(DossierTheme.labelHex, DossierTheme.paperHex), 4.5)
        XCTAssertGreaterThanOrEqual(ratio(DossierTheme.inkHex, DossierTheme.folderHex), 7)
        XCTAssertGreaterThanOrEqual(ratio(DossierTheme.stampHex, DossierTheme.paperHex), 4.5, "red stamp text")
    }

    func testPhotoTintedTextIsReadableOnPaperForAnyPhoto() {
        let colours: [(CGFloat, CGFloat, CGFloat)] = [(1, 0, 0), (0, 1, 0), (0, 0.2, 1), (1, 1, 0), (1, 1, 1), (0, 0, 0),
                                                      (0.5, 0.5, 0.5), (1, 0.6, 0.9), (0.9, 0.9, 0.2)]
        var palettes = colours.map { ArtworkPalette.from(solidImage($0.0, $0.1, $0.2)) }
        palettes.append(.fallback)
        let paper = ArtworkPalette.RGB(hex: DossierTheme.paperHex)
        for p in palettes {
            let ink = p.accentRGB.readable(onPaper: DossierTheme.paperHex)
            XCTAssertGreaterThanOrEqual(DossierTheme.contrastRatio(ink, paper), 4.5, "\(p.accentRGB)")
        }
    }

    func testPillTextIsReadableOnTheAccentForAnyPhoto() {
        // `onAccent` is near-black or white, whichever contrasts more with the accent.
        for c: (CGFloat, CGFloat, CGFloat) in [(1, 0, 0), (0, 1, 0), (0, 0.2, 1), (1, 1, 0), (0.5, 0.5, 0.5)] {
            let p = ArtworkPalette.from(solidImage(c.0, c.1, c.2))
            let lum = p.accentRGB.relativeLuminance
            let dark = (lum + 0.05) / (ArtworkPalette.RGB(hex: 0x14121A).relativeLuminance + 0.05)
            let light = 1.05 / (lum + 0.05)
            XCTAssertGreaterThanOrEqual(max(dark, light), 3, "\(p.accentRGB)")
        }
    }

    func testReadableAccentKeepsThePhotoHue() {
        let red = ArtworkPalette.from(solidImage(1, 0, 0)).accentRGB.readable(onPaper: DossierTheme.paperHex)
        XCTAssertGreaterThan(red.r, red.g * 2)
        let blue = ArtworkPalette.from(solidImage(0, 0.2, 1)).accentRGB.readable(onPaper: DossierTheme.paperHex)
        XCTAssertGreaterThan(blue.b, blue.r * 2)
    }
}
