import XCTest
import CoreGraphics
import ImageIO
@testable import CineID

private func solidImage(width: Int, height: Int, gray: UInt8 = 0, alpha: Bool = false) -> CGImage {
    let info = alpha ? CGImageAlphaInfo.premultipliedLast.rawValue : CGImageAlphaInfo.noneSkipLast.rawValue
    let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: info)!
    let v = CGFloat(gray) / 255
    ctx.setFillColor(red: v, green: v, blue: v, alpha: 1)
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    return ctx.makeImage()!
}

private func noiseImage(width: Int, height: Int) -> CGImage {
    var rng = SystemRandomNumberGenerator()
    var bytes = (0..<(width * height * 4)).map { _ in UInt8.random(in: 0...255, using: &rng) }
    let provider = CGDataProvider(data: Data(bytes: &bytes, count: bytes.count) as CFData)!
    return CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
                   space: CGColorSpaceCreateDeviceRGB(),
                   bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                   provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
}

final class FrameCheckTests: XCTestCase {
    // MARK: targetSize

    func testTargetSizeKeepsAspectWhenShrinking() {
        let size = FrameCheck.targetSize(for: CGSize(width: 3840, height: 2160))
        XCTAssertEqual(size, CGSize(width: 1920, height: 1080))
        let odd = FrameCheck.targetSize(for: CGSize(width: 3024, height: 1964))
        XCTAssertEqual(odd.width, 1920)
        XCTAssertEqual(odd.height, (1964.0 * 1920 / 3024).rounded())
    }

    func testTargetSizeNeverUpsizes() {
        XCTAssertEqual(FrameCheck.targetSize(for: CGSize(width: 1280, height: 720)), CGSize(width: 1280, height: 720))
        XCTAssertEqual(FrameCheck.targetSize(for: CGSize(width: 1920, height: 1080)), CGSize(width: 1920, height: 1080))
    }

    func testTargetSizeHonoursACustomWidthAndSurvivesDegenerateInput() {
        XCTAssertEqual(FrameCheck.targetSize(for: CGSize(width: 2000, height: 1000), maxWidth: 1000), CGSize(width: 1000, height: 500))
        XCTAssertEqual(FrameCheck.targetSize(for: .zero), .zero)
        XCTAssertEqual(FrameCheck.targetSize(for: CGSize(width: 5000, height: 1)).height, 1, "never collapses to zero height")
    }

    // MARK: brightness

    func testBlackImageIsBlack() {
        let mean = FrameCheck.meanBrightness(of: solidImage(width: 200, height: 100, gray: 0))
        XCTAssertEqual(mean, 0, accuracy: 0.5)
        XCTAssertTrue(FrameCheck.isBlack(meanBrightness: mean))
    }

    func testMidGreyIsNotBlack() {
        let mean = FrameCheck.meanBrightness(of: solidImage(width: 200, height: 100, gray: 128))
        XCTAssertEqual(mean, 128, accuracy: 2)
        XCTAssertFalse(FrameCheck.isBlack(meanBrightness: mean))
    }

    func testBlackThresholdIsStrictlyBelowEight() {
        XCTAssertTrue(FrameCheck.isBlack(meanBrightness: 7.99))
        XCTAssertFalse(FrameCheck.isBlack(meanBrightness: 8))
        XCTAssertFalse(FrameCheck.isBlack(meanBrightness: 8.01))
    }

    func testNearBlackLetterboxedFrameWithARealPictureIsNotBlack() {
        // Dark scene: left half black, right half mid-grey -> mean 64, well above the threshold.
        let ctx = CGContext(data: nil, width: 100, height: 100, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        ctx.setFillColor(gray: 0.5, alpha: 1)
        ctx.fill(CGRect(x: 50, y: 0, width: 50, height: 100))
        XCTAssertFalse(FrameCheck.isBlack(meanBrightness: FrameCheck.meanBrightness(of: ctx.makeImage()!)))
    }

    // MARK: jpeg

    func testJpegDecodesBackToTheSameSize() throws {
        let data = try XCTUnwrap(FrameCheck.jpeg(solidImage(width: 320, height: 180, gray: 90)))
        let source = try XCTUnwrap(CGImageSourceCreateWithData(data as CFData, nil))
        XCTAssertEqual(CGImageSourceGetType(source) as String?, "public.jpeg")
        let image = try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
        XCTAssertEqual(image.width, 320)
        XCTAssertEqual(image.height, 180)
    }

    func testJpegHandlesImagesWithAnAlphaChannel() throws {
        let data = try XCTUnwrap(FrameCheck.jpeg(solidImage(width: 64, height: 64, gray: 200, alpha: true)))
        XCTAssertGreaterThan(data.count, 100)
    }

    func testLowerQualityMakesASmallerFile() throws {
        let noisy = noiseImage(width: 256, height: 256)
        let high = try XCTUnwrap(FrameCheck.jpeg(noisy, quality: 0.95))
        let low = try XCTUnwrap(FrameCheck.jpeg(noisy, quality: 0.2))
        XCTAssertLessThan(low.count, high.count)
    }

    // MARK: downscale and prepare

    func testDownscaleShrinksToMaxWidthAndLeavesSmallImagesAlone() {
        let big = solidImage(width: 3000, height: 1500, gray: 100)
        let small = FrameCheck.downscaled(big)
        XCTAssertEqual(small.width, 1920)
        XCTAssertEqual(small.height, 960)
        let tiny = solidImage(width: 800, height: 600)
        XCTAssertEqual(FrameCheck.downscaled(tiny).width, 800)
    }

    func testPrepareFlagsBlackFramesAndEncodesGoodOnes() {
        XCTAssertEqual(FrameCheck.prepare(solidImage(width: 400, height: 300, gray: 0)), .black)
        if case .jpeg(let data) = FrameCheck.prepare(solidImage(width: 4000, height: 2000, gray: 120)) {
            let source = CGImageSourceCreateWithData(data as CFData, nil)
            let props = source.flatMap { CGImageSourceCopyPropertiesAtIndex($0, 0, nil) as? [CFString: Any] }
            XCTAssertEqual(props?[kCGImagePropertyPixelWidth] as? Int, 1920)
        } else {
            XCTFail("expected a jpeg")
        }
    }
}

final class ScreenCaptureChoiceTests: XCTestCase {
    func testPrefersTheChromeDisplay() {
        XCTAssertEqual(ScreenCaptureKitCapture.chooseDisplay(preferred: 2, main: 1, available: [1, 2]), 2)
    }

    func testFallsBackToMainWhenChromeHasNoWindowOrIsUnavailable() {
        XCTAssertEqual(ScreenCaptureKitCapture.chooseDisplay(preferred: nil, main: 1, available: [2, 1]), 1)
        XCTAssertEqual(ScreenCaptureKitCapture.chooseDisplay(preferred: 9, main: 1, available: [1, 2]), 1)
    }

    func testFallsBackToAnyDisplayAndThenNil() {
        XCTAssertEqual(ScreenCaptureKitCapture.chooseDisplay(preferred: nil, main: 7, available: [3, 4]), 3)
        XCTAssertNil(ScreenCaptureKitCapture.chooseDisplay(preferred: 2, main: 1, available: []))
    }
}
