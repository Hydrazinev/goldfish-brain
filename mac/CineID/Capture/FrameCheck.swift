import CoreGraphics
import Foundation
import ImageIO

/// Pure image helpers for the capture path: size, "is this a black frame?", and JPEG encoding.
enum FrameCheck {
    /// Mean brightness (0...255) below which a frame counts as black. Netflix video comes out
    /// as solid black when Chrome's hardware acceleration hides it from screenshots.
    static let blackThreshold: Double = 8

    /// Same aspect, at most `maxWidth` wide, never larger than the input.
    static func targetSize(for size: CGSize, maxWidth: CGFloat = 1920) -> CGSize {
        guard size.width > 0, size.height > 0, size.width > maxWidth else { return size }
        let scale = maxWidth / size.width
        return CGSize(width: maxWidth, height: max(1, (size.height * scale).rounded()))
    }

    static func isBlack(meanBrightness: Double) -> Bool { meanBrightness < blackThreshold }

    /// Average luminance 0...255, measured on a small downsample (cheap, and averaging is what we want).
    static func meanBrightness(of image: CGImage) -> Double {
        let w = 64, h = 36
        var pixels = [UInt8](repeating: 0, count: w * h)
        let drew = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let ctx = CGContext(data: buffer.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
            else { return false }
            ctx.interpolationQuality = .medium
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        guard drew else { return 0 }
        let total = pixels.reduce(0) { $0 + Int($1) }
        return Double(total) / Double(pixels.count)
    }

    /// JPEG bytes (no alpha, sRGB). Nil only if encoding fails.
    static func jpeg(_ image: CGImage, quality: CGFloat = 0.85) -> Data? {
        guard let flat = opaque(image) else { return nil }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(destination, flat,
                                   [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }

    /// Scaled to at most `maxWidth` wide; returns the input unchanged when it already fits.
    static func downscaled(_ image: CGImage, maxWidth: CGFloat = 1920) -> CGImage {
        let target = targetSize(for: CGSize(width: image.width, height: image.height), maxWidth: maxWidth)
        guard Int(target.width) != image.width || Int(target.height) != image.height else { return image }
        return draw(image, width: Int(target.width), height: Int(target.height)) ?? image
    }

    enum Prepared: Equatable {
        case black
        case jpeg(Data)
        case unencodable
    }

    /// Black-frame check, then downscale and encode, ready to send to the engine.
    static func prepare(_ image: CGImage) -> Prepared {
        if isBlack(meanBrightness: meanBrightness(of: image)) { return .black }
        guard let data = jpeg(downscaled(image)) else { return .unencodable }
        return .jpeg(data)
    }

    private static func opaque(_ image: CGImage) -> CGImage? {
        draw(image, width: image.width, height: image.height)
    }

    private static func draw(_ image: CGImage, width: Int, height: Int) -> CGImage? {
        guard width > 0, height > 0,
              let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return ctx.makeImage()
    }
}
