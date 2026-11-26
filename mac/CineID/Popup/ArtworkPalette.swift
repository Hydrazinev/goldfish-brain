import SwiftUI
import AppKit

/// The "Dossier / Polaroid" look: warm cream paper with dark ink on a dark desk, a typewriter for
/// labels and a serif for names. Only accents (pill, bar, tape) come from the actor's photo, so the
/// paper and the ink never change and text is always readable.
enum DossierTheme {
    static let deskHex = 0x17110C
    static let desk = Color(hex: deskHex)
    static let paperHex = 0xF4EAD6
    static let paper = Color(hex: paperHex)
    static let inkHex = 0x2B2218
    static let ink = Color(hex: inkHex)
    /// Typewriter label colour (darker than the mockup's #8A7452 so small caps pass 4.5:1 on the paper).
    static let labelHex = 0x6B5A3C
    static let label = Color(hex: labelHex)
    static let stampHex = 0xB3382C
    static let stampRed = Color(hex: stampHex)
    /// Dashed rules, the empty part of the match bar and chips.
    static let ruleHex = 0xB9A883
    static let rule = Color(hex: ruleHex)
    static let trackHex = 0xE3D5B8
    static let track = Color(hex: trackHex)
    static let folderHex = 0xE8D9B6
    static let folder = Color(hex: folderHex)
    static let polaroid = Color(hex: 0xFFFCF4)

    /// Georgia-style serif for names.
    static func serif(_ size: CGFloat, _ weight: Font.Weight = .bold) -> Font {
        .custom("Georgia", size: size).weight(weight)
    }

    /// Courier-style typewriter for body text and labels.
    static func typewriter(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .custom("Courier New", size: size).weight(weight)
    }

    /// Small letterspaced typewriter caps for labels.
    static func label(_ size: CGFloat = 11) -> Font { typewriter(size, .bold) }

    /// WCAG contrast ratio between two 0xRRGGBB colours.
    static func contrastRatio(_ a: Int, _ b: Int) -> Double {
        contrastRatio(ArtworkPalette.RGB(hex: a), ArtworkPalette.RGB(hex: b))
    }

    static func contrastRatio(_ a: ArtworkPalette.RGB, _ b: ArtworkPalette.RGB) -> Double {
        let (hi, lo) = (max(a.relativeLuminance, b.relativeLuminance), min(a.relativeLuminance, b.relativeLuminance))
        return (hi + 0.05) / (lo + 0.05)
    }
}

extension Color {
    init(hex: Int) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}

/// Colours taken from an actor's photo (port of Venus's `ArtworkPalette`): the dominant *vivid*
/// hue (weighted by saturation x brightness so a small colourful area beats a large grey one) plus
/// a second hue, and the photo's overall brightness and colourfulness. They tint the accents only
/// (status pill, tape on the photo, the bar beside "Right now"); the paper stays cream.
struct ArtworkPalette: Equatable {
    struct RGB: Equatable {
        var r: Double, g: Double, b: Double

        init(r: Double, g: Double, b: Double) { self.r = r; self.g = g; self.b = b }
        init(hex: Int) {
            self.init(r: Double((hex >> 16) & 0xFF) / 255, g: Double((hex >> 8) & 0xFF) / 255,
                      b: Double(hex & 0xFF) / 255)
        }

        var color: Color { Color(red: r, green: g, blue: b) }

        /// WCAG relative luminance (0 black ... 1 white).
        var relativeLuminance: Double {
            func lin(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
            return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
        }

        func darkened(_ factor: Double) -> RGB { RGB(r: r * factor, g: g * factor, b: b * factor) }

        /// Mixes toward white by `amount` (0...1).
        func lightened(_ amount: Double) -> RGB {
            RGB(r: r + (1 - r) * amount, g: g + (1 - g) * amount, b: b + (1 - b) * amount)
        }

        /// Darkens (keeping hue) until the colour's relative luminance is at most `limit`.
        func capped(luminance limit: Double) -> RGB {
            var c = self
            var guardCount = 0
            while c.relativeLuminance > limit, guardCount < 60 { c = c.darkened(0.94); guardCount += 1 }
            return c
        }

        /// Darkens (keeping hue) until it has at least `ratio` contrast against the 0xRRGGBB background.
        func readable(onPaper hex: Int, ratio: Double = 4.5) -> RGB {
            let bg = RGB(hex: hex)
            var c = self
            var guardCount = 0
            while DossierTheme.contrastRatio(c, bg) < ratio, guardCount < 80 { c = c.darkened(0.95); guardCount += 1 }
            return c
        }

        /// Pushes saturation up (at least 0.45) and keeps brightness within 0.45...0.85.
        func vivid() -> RGB {
            let mx = max(r, g, b), mn = min(r, g, b)
            let sat = mx > 0 ? (mx - mn) / mx : 0
            let newSat = min(max(sat * 1.15, 0.45), 1)
            let newVal = min(max(mx, 0.45), 0.85)
            func channel(_ c: Double) -> Double {
                let t = mx > 0 ? c / mx : 1
                return newVal * (1 - newSat * (1 - t))
            }
            return RGB(r: channel(r), g: channel(g), b: channel(b))
        }

        /// Scales so the brightest channel lies within 0.2...0.75 (for near-greyscale photos).
        func toned() -> RGB {
            let peak = max(r, g, b)
            let target = min(max(peak, 0.2), 0.75)
            if peak < 0.001 { return RGB(r: target, g: target, b: target) }
            let k = target / peak
            return RGB(r: r * k, g: g * k, b: b * k)
        }
    }

    var accentRGB: RGB
    var shadowRGB: RGB
    /// Second hue (a distinct colour in the photo if it has one, else a darker shade of the first).
    var secondRGB: RGB? = nil
    /// Overall brightness (0...1) and colourfulness (0 grey ... 1 vivid) of the whole photo.
    var luminance: Double = 0.4
    var saturation: Double = 0.6

    var accent: Color { accentRGB.color }
    var shadow: Color { shadowRGB.color }

    /// Text colour for things drawn on the accent (status pill): near-black or white, whichever contrasts more.
    var onAccent: Color {
        let lum = accentRGB.relativeLuminance
        return (lum + 0.05) / 0.06 >= 1.05 / (lum + 0.05) ? Color(hex: 0x14121A) : .white
    }

    /// Warm amber: the default accent when there is no usable photo.
    static let fallback = ArtworkPalette(accentRGB: RGB(r: 0.80, g: 0.55, b: 0.20),
                                         shadowRGB: RGB(r: 0.80, g: 0.55, b: 0.20).darkened(0.45),
                                         secondRGB: RGB(r: 0.50, g: 0.32, b: 0.85),
                                         luminance: 0.4, saturation: 0.6)

    /// The accent darkened (keeping its hue) until it reads as text on the cream paper (4.5:1).
    var accentInk: Color { accentRGB.readable(onPaper: DossierTheme.paperHex).color }

    static func from(_ image: NSImage?) -> ArtworkPalette {
        guard let cg = image?.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return fallback }
        let n = 16
        var pixels = [UInt8](repeating: 0, count: n * n * 4)
        let drew: Bool = pixels.withUnsafeMutableBytes { buffer in
            guard let ctx = CGContext(data: buffer.baseAddress, width: n, height: n, bitsPerComponent: 8,
                                      bytesPerRow: n * 4, space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.interpolationQuality = .medium
            ctx.draw(cg, in: CGRect(x: 0, y: 0, width: n, height: n))
            return true
        }
        guard drew else { return fallback }

        let bins = 12
        var weight = [Double](repeating: 0, count: bins)
        var sum = [(r: Double, g: Double, b: Double)](repeating: (0, 0, 0), count: bins)
        var avg = (r: 0.0, g: 0.0, b: 0.0)
        var satSum = 0.0
        for i in 0..<(n * n) {
            let r = Double(pixels[i * 4]) / 255, g = Double(pixels[i * 4 + 1]) / 255, b = Double(pixels[i * 4 + 2]) / 255
            avg.r += r; avg.g += g; avg.b += b
            let mx = max(r, g, b), mn = min(r, g, b)
            let sat = mx > 0 ? (mx - mn) / mx : 0
            satSum += sat
            guard mx > 0.15, sat > 0.15 else { continue }
            let bin = min(Int(hueDegrees(r, g, b, mx, mn) / (360.0 / Double(bins))), bins - 1)
            let w = sat * mx
            weight[bin] += w
            sum[bin].r += r * w; sum[bin].g += g * w; sum[bin].b += b * w
        }
        let count = Double(n * n)
        let luminance = 0.299 * avg.r / count + 0.587 * avg.g / count + 0.114 * avg.b / count
        let saturation = satSum / count

        guard let main = weight.indices.max(by: { weight[$0] < weight[$1] }), weight[main] > 1.0 else {
            let average = RGB(r: avg.r / count, g: avg.g / count, b: avg.b / count).toned()
            return ArtworkPalette(accentRGB: average, shadowRGB: average.darkened(0.45), secondRGB: average.darkened(0.6),
                                  luminance: luminance, saturation: saturation)
        }
        func color(_ bin: Int) -> RGB {
            RGB(r: sum[bin].r / weight[bin], g: sum[bin].g / weight[bin], b: sum[bin].b / weight[bin]).vivid()
        }
        let accent = color(main)
        let second = weight.indices
            .filter { b in let d = abs(b - main); return min(d, bins - d) >= 2 && weight[b] >= weight[main] * 0.25 }
            .max(by: { weight[$0] < weight[$1] })
        return ArtworkPalette(accentRGB: accent, shadowRGB: accent.darkened(0.45),
                              secondRGB: second.map(color) ?? accent.darkened(0.55),
                              luminance: luminance, saturation: saturation)
    }

    private static func hueDegrees(_ r: Double, _ g: Double, _ b: Double, _ mx: Double, _ mn: Double) -> Double {
        let d = mx - mn
        guard d > 0 else { return 0 }
        var h: Double
        if mx == r { h = (g - b) / d } else if mx == g { h = 2 + (b - r) / d } else { h = 4 + (r - g) / d }
        h *= 60
        return h < 0 ? h + 360 : h
    }
}
