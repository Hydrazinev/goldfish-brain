import Foundation

/// What `POST /api/ask` needs. The screenshot is the same JPEG the identify path produces.
struct AskRequest {
    let question: String
    let jpeg: Data
    let showId: String
    let season: Int
    let episode: Int
    /// How far through the episode (0...1) when known; sent as null otherwise.
    let progress: Double?
}

/// "Spoiler-safe up to S3E7": how far the engine allowed itself to go in this answer.
struct SafePosition: Equatable {
    let season: Int
    let episode: Int

    /// "S3E7" (no zero padding, unlike the red stamp).
    var short: String { ViewingPosition.shortCode(season, episode) }

    /// Accepts what the engine may reasonably send: "S3E7", "S03E07", "3x7", {"season":3,"episode":7} or [3, 7].
    static func parse(_ raw: Any?) -> SafePosition? {
        func make(_ s: Int, _ e: Int) -> SafePosition? { s > 0 && e > 0 ? SafePosition(season: s, episode: e) : nil }
        switch raw {
        case let text as String:
            let pattern = #"^\s*[Ss]?(\d{1,3})\s*[EeXx:]\s*(\d{1,4})\s*$"#
            guard let match = text.range(of: pattern, options: .regularExpression) else { return nil }
            let numbers = text[match].split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
            return numbers.count == 2 ? make(numbers[0], numbers[1]) : nil
        case let dict as [String: Any]:
            guard let s = (dict["season"] as? Int) ?? (dict["s"] as? Int),
                  let e = (dict["episode"] as? Int) ?? (dict["e"] as? Int) else { return nil }
            return make(s, e)
        case let list as [Int] where list.count == 2:
            return make(list[0], list[1])
        default:
            return nil
        }
    }
}

struct AskResponse: Equatable {
    let answer: String
    let safeUpTo: SafePosition?
    /// True when the engine declined (the answer would spoil, or the question is out of scope).
    let refused: Bool

    /// Nil when the body isn't an object with a non-empty `answer`.
    static func parse(_ data: Data) -> AskResponse? {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let answer = (object["answer"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !answer.isEmpty else { return nil }
        return AskResponse(answer: answer, safeUpTo: SafePosition.parse(object["safe_up_to"]),
                           refused: (object["refused"] as? Bool) ?? false)
    }
}

enum AskError: Error, Equatable {
    /// 403 "needs_disclosure": the Watch Buddy privacy acknowledgement has not been ticked yet.
    case needsDisclosure
    case notReachable
    case timedOut
    /// Any other non-2xx; `message` is the engine's `{"error": ...}` text when it sent one.
    case http(Int, message: String?)
    case decoding
}

/// Sends the HTTP request; a protocol so tests can stub the engine.
protocol HTTPTransport {
    func send(_ request: URLRequest) async throws -> (Data, URLResponse)
}

struct URLSessionTransport: HTTPTransport {
    var session: URLSession = .shared
    func send(_ request: URLRequest) async throws -> (Data, URLResponse) { try await session.data(for: request) }
}

protocol AskAPI {
    func ask(_ request: AskRequest) async throws -> AskResponse
}

/// Client for `POST /api/ask`. Never logs the question or the image.
final class AskClient: AskAPI {
    static let timeout: TimeInterval = 30

    private let baseURL: URL
    private let transport: HTTPTransport

    init(baseURL: URL = EngineClient.defaultBaseURL, transport: HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL
        self.transport = transport
    }

    func makeURLRequest(_ ask: AskRequest) throws -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/ask"))
        req.httpMethod = "POST"
        req.timeoutInterval = Self.timeout
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "question": ask.question,
            "image_b64": ask.jpeg.base64EncodedString(),
            "show_id": ask.showId,
            "season": ask.season,
            "episode": ask.episode,
            "progress": ask.progress.map { $0 as Any } ?? NSNull(),
        ]
        do { req.httpBody = try JSONSerialization.data(withJSONObject: body) } catch { throw AskError.decoding }
        return req
    }

    func ask(_ request: AskRequest) async throws -> AskResponse {
        let urlRequest = try makeURLRequest(request)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await transport.send(urlRequest)
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch let error as URLError where error.code == .timedOut {
            throw AskError.timedOut
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw AskError.notReachable
        }
        guard let http = response as? HTTPURLResponse else { throw AskError.notReachable }
        guard (200..<300).contains(http.statusCode) else {
            let message = Self.errorText(in: data)
            if http.statusCode == 403, message == "needs_disclosure" { throw AskError.needsDisclosure }
            throw AskError.http(http.statusCode, message: message)
        }
        guard let parsed = AskResponse.parse(data) else { throw AskError.decoding }
        return parsed
    }

    /// `{"error": "..."}` (also tolerates FastAPI's `{"detail": "..."}`).
    static func errorText(in data: Data) -> String? {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        return (object["error"] as? String) ?? (object["detail"] as? String)
    }
}

/// Plain-language text for an ask failure. The engine's own text is used only when it reads like a sentence.
enum AskFriendlyError {
    static let needsDisclosure = "Asking sends a screenshot of your screen to Claude, so Goldfish Brain needs your OK first. Open Goldfish Brain → Settings → Watch Buddy and tick the privacy box, then ask again."

    static func text(_ error: AskError, serverAge: TimeInterval = .infinity) -> String {
        switch error {
        case .needsDisclosure:
            return needsDisclosure
        case .notReachable:
            return FriendlyError.text(serverAge < FlowFailure.warmupSeconds ? .engineWarmingUp : .engineNotResponding)
        case .timedOut:
            return FriendlyError.text(.timeout)
        case .decoding:
            return FriendlyError.text(.other)
        case .http(let code, let message):
            if (400..<500).contains(code), let message = sentence(message) { return message }
            return FriendlyError.text(code >= 500 ? .serverError : .other)
        }
    }

    /// Engine codes like "needs_disclosure" are not for people; a short sentence with spaces is.
    static func sentence(_ text: String?) -> String? {
        guard let t = text?.trimmingCharacters(in: .whitespacesAndNewlines), t.contains(" "), t.count <= 200 else { return nil }
        return t
    }
}
