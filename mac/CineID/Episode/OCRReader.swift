import CoreGraphics
import Foundation
import Vision

protocol OCRReading {
    /// Text found in `regions` of `image`. Regions are normalised (0...1) with the origin at the top left.
    func readText(in image: CGImage, regions: [CGRect]) -> String
}

/// Apple Vision text recognition in fast mode (needs no permission).
struct VisionOCR: OCRReading {
    /// Where Netflix shows "S2:E8": the top 15 % (title bar) and the bottom 25 % (player controls).
    static let defaultRegions = [CGRect(x: 0, y: 0, width: 1, height: 0.15),
                                 CGRect(x: 0, y: 0.75, width: 1, height: 0.25)]

    func readText(in image: CGImage, regions: [CGRect] = VisionOCR.defaultRegions) -> String {
        let size = CGSize(width: image.width, height: image.height)
        var lines: [String] = []
        for region in regions {
            let rect = Self.pixelRect(for: region, imageSize: size)
            guard rect.width >= 1, rect.height >= 1, let crop = image.cropping(to: rect) else { continue }
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .fast
            request.usesLanguageCorrection = false
            let handler = VNImageRequestHandler(cgImage: crop, options: [:])
            guard (try? handler.perform([request])) != nil else { continue }
            lines += (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        }
        return lines.joined(separator: "\n")
    }

    /// Normalised top-left region to a pixel rectangle (also top-left, as `CGImage.cropping` expects), clamped to the image.
    static func pixelRect(for region: CGRect, imageSize: CGSize) -> CGRect {
        let full = CGRect(origin: .zero, size: imageSize)
        let rect = CGRect(x: region.minX * imageSize.width, y: region.minY * imageSize.height,
                          width: region.width * imageSize.width, height: region.height * imageSize.height)
        return rect.intersection(full).integral.intersection(full)
    }
}
