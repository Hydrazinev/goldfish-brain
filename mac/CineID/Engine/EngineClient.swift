import Foundation

/// The engine's local HTTP API, behind a protocol so callers can be tested with a fake.
protocol EngineAPI {
    /// True only when the engine answers `/health` with 2xx. Never throws for a down engine.
    func health() async throws -> Bool
    func state() async throws -> EngineState
    /// `progress` (0...1, how far through the episode) is sent only when known; nil keeps the conservative spoiler shield.
    func identify(jpeg: Data, showId: String, season: Int, episode: Int, progress: Double?) async throws -> IdentifyResponse
    func setPosition(showId: String, season: Int, episode: Int) async throws
    /// Makes `showId` the show being watched (`POST /api/shows/select`).
    func selectShow(showId: String) async throws
    /// Which season holds this episode number with this title (TMDB, via the engine); nil when unknown.
    func lookupSeason(showId: String, episode: Int, title: String) async throws -> Int?
    /// Reference photo bytes, or nil when the character has none (404).
    func photo(characterId: String) async throws -> Data?
}

extension EngineAPI {
    func identify(jpeg: Data, showId: String, season: Int, episode: Int) async throws -> IdentifyResponse {
        try await identify(jpeg: jpeg, showId: showId, season: season, episode: episode, progress: nil)
    }
}

final class EngineClient: EngineAPI {
    static let defaultBaseURL = URL(string: "http://127.0.0.1:8000")!

    private let baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    init(baseURL: URL = EngineClient.defaultBaseURL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
    }

    func health() async throws -> Bool {
        do {
            _ = try await send(request("health"))
            return true
        } catch EngineError.notReachable {
            return false
        } catch EngineError.timedOut {
            return false  // not answering in time is "not healthy yet"; cancellation still propagates
        } catch EngineError.http {
            return false
        }
    }

    func state() async throws -> EngineState {
        try decode(EngineState.self, from: try await send(request("api/state")))
    }

    func identify(jpeg: Data, showId: String, season: Int, episode: Int, progress: Double?) async throws -> IdentifyResponse {
        var body: [String: Any] = [
            "image": jpeg.base64EncodedString(),
            "show_id": showId,
            "season": season,
            "episode": episode,
        ]
        if let progress { body["progress"] = progress }
        let req = try jsonPost("identify", body: body)
        return try decode(IdentifyResponse.self, from: try await send(req))
    }

    func setPosition(showId: String, season: Int, episode: Int) async throws {
        let req = try jsonPost("api/position", body: ["show_id": showId, "season": season, "episode": episode])
        _ = try await send(req)
    }

    func selectShow(showId: String) async throws {
        _ = try await send(try jsonPost("api/shows/select", body: ["show_id": showId]))
    }

    func lookupSeason(showId: String, episode: Int, title: String) async throws -> Int? {
        var comps = URLComponents()
        comps.queryItems = [URLQueryItem(name: "show_id", value: showId),
                            URLQueryItem(name: "episode", value: String(episode)),
                            URLQueryItem(name: "title", value: String(title.prefix(120)))]
        // URLComponents leaves "&" and "+" raw inside values: encode them ourselves.
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "&+=#?")
        let query = (comps.queryItems ?? []).map { item in
            item.name + "=" + (item.value?.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")
        }.joined(separator: "&")
        var req = request("api/episodes/season?" + query)
        req.timeoutInterval = 5
        struct Reply: Decodable { let season: Int? }
        return try decode(Reply.self, from: try await send(req)).season
    }

    func photo(characterId: String) async throws -> Data? {
        // Percent-encode the id as a single path segment so it can't alter the route.
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/")
        let segment = characterId.addingPercentEncoding(withAllowedCharacters: allowed) ?? ""
        do {
            return try await send(request("api/characters/\(segment)/photo"))
        } catch EngineError.http(404) {
            return nil
        }
    }

    // MARK: plumbing

    private func request(_ path: String) -> URLRequest {
        // Build from a string so pre-encoded segments (%2F) survive intact.
        let base = baseURL.absoluteString.hasSuffix("/") ? String(baseURL.absoluteString.dropLast()) : baseURL.absoluteString
        let url = URL(string: base + "/" + path) ?? baseURL
        var req = URLRequest(url: url)
        req.timeoutInterval = 30
        return req
    }

    private func jsonPost(_ path: String, body: [String: Any]) throws -> URLRequest {
        var req = request(path)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        } catch {
            throw EngineError.decoding
        }
        return req
    }

    private func send(_ req: URLRequest) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()  // the caller's Task was cancelled; not an engine fault
        } catch let error as URLError where error.code == .timedOut {
            throw EngineError.timedOut  // slow (e.g. cold model), not down: don't restart it
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw EngineError.notReachable
        }
        guard let http = response as? HTTPURLResponse else { throw EngineError.notReachable }
        guard (200..<300).contains(http.statusCode) else { throw EngineError.http(http.statusCode) }
        return data
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try decoder.decode(type, from: data)
        } catch {
            throw EngineError.decoding
        }
    }
}
