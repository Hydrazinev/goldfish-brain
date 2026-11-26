import Foundation

protocol FeedbackSending {
    /// Fire and forget: never throws, never blocks the card.
    func send(_ report: FeedbackReport) async
}

/// `POST /api/feedback` with `{show_id, character_id, kind, season, episode}`.
final class FeedbackClient: FeedbackSending {
    private let baseURL: URL
    private let transport: HTTPTransport

    init(baseURL: URL = EngineClient.defaultBaseURL, transport: HTTPTransport = URLSessionTransport()) {
        self.baseURL = baseURL
        self.transport = transport
    }

    func makeURLRequest(_ report: FeedbackReport) -> URLRequest {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/feedback"))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["show_id": report.showId, "character_id": report.characterId,
                                   "kind": report.kind.rawValue, "season": report.season, "episode": report.episode]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        return req
    }

    func send(_ report: FeedbackReport) async {
        do {
            let (_, response) = try await transport.send(makeURLRequest(report))
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                Diag.log("feedback not accepted (HTTP \(http.statusCode))")
            }
        } catch {
            Diag.log("feedback not sent")
        }
    }
}
