import XCTest
@testable import CineID

/// Intercepts every request made through a session configured with it.
final class StubURLProtocol: URLProtocol {
    typealias Handler = (URLRequest) throws -> (HTTPURLResponse, Data)
    nonisolated(unsafe) static var handler: Handler?
    nonisolated(unsafe) static var lastBody: Data?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        // URLSession moves httpBody into a stream; read it back for assertions.
        if let stream = request.httpBodyStream {
            stream.open()
            var data = Data()
            var buf = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buf, maxLength: buf.count)
                if n <= 0 { break }
                data.append(buf, count: n)
            }
            stream.close()
            StubURLProtocol.lastBody = data
        } else {
            StubURLProtocol.lastBody = request.httpBody
        }
        guard let handler = StubURLProtocol.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badURL)); return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }
    override func stopLoading() {}
}

final class EngineClientTests: XCTestCase {
    private let base = URL(string: "http://127.0.0.1:8000")!
    private var client: EngineClient!

    override func setUp() {
        super.setUp()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        client = EngineClient(baseURL: base, session: URLSession(configuration: config))
        StubURLProtocol.handler = nil
        StubURLProtocol.lastBody = nil
    }

    private func respond(_ status: Int, _ body: String, contentType: String = "application/json") {
        respond(status, Data(body.utf8), contentType: contentType)
    }
    private func respond(_ status: Int, _ body: Data, contentType: String = "application/json") {
        StubURLProtocol.handler = { req in
            let resp = HTTPURLResponse(url: req.url!, statusCode: status, httpVersion: nil,
                                       headerFields: ["Content-Type": contentType])!
            return (resp, body)
        }
    }

    // MARK: fixtures (shapes taken from src/server.py _build_character_result / _limited_result)

    private let fullBody = """
    {"characters":[{"character_id":"walter-white","name":"Walter White","actor":"Bryan Cranston",
     "role":"Protagonist","confidence":0.8123,"status":"alive","arc_type":"turning_point",
     "first_seen_season":1,"first_seen_episode":1,"last_seen_season":3,"last_seen_episode":7,
     "relationship_summary":"Chemistry teacher turned drug lord.",
     "current_arc":"Plans his next move.","last_seen_description":"In the desert lab.",
     "importance_rank":1},
     {"character_id":"jesse-pinkman","name":"Jesse Pinkman","actor":"Aaron Paul",
     "role":"Partner","confidence":0.61,"status":null,"arc_type":null,
     "first_seen_season":1,"first_seen_episode":1,"last_seen_season":null,"last_seen_episode":null,
     "relationship_summary":null,"current_arc":null,"last_seen_description":null,
     "importance_rank":2}],
     "total_faces_detected":2,"show_id":"breaking-bad"}
    """

    private let limitedBody = """
    {"characters":[{"character_id":"saul-goodman","name":"Saul Goodman","actor":"Bob Odenkirk",
     "role":"Lawyer","confidence":0.7,"status":null,"arc_type":null,
     "first_seen_season":null,"first_seen_episode":null,"last_seen_season":null,
     "last_seen_episode":null,"relationship_summary":null,"current_arc":null,
     "last_seen_description":null,"importance_rank":5,"limited":true}],
     "total_faces_detected":1,"show_id":"breaking-bad"}
    """

    // MARK: decoding

    func testIdentifySendsProgressOnlyWhenKnown() async throws {
        respond(200, fullBody)
        _ = try await client.identify(jpeg: Data([1]), showId: "bb", season: 1, episode: 1)
        var obj = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(StubURLProtocol.lastBody)) as? [String: Any])
        XCTAssertNil(obj["progress"])
        _ = try await client.identify(jpeg: Data([1]), showId: "bb", season: 1, episode: 1, progress: 0.95)
        obj = try XCTUnwrap(JSONSerialization.jsonObject(with: try XCTUnwrap(StubURLProtocol.lastBody)) as? [String: Any])
        XCTAssertEqual(obj["progress"] as? Double, 0.95)
    }

    func testIdentifyDecodesFullSuccessBody() async throws {
        respond(200, fullBody)
        let r = try await client.identify(jpeg: Data([1, 2, 3]), showId: "breaking-bad", season: 3, episode: 7)
        XCTAssertEqual(r.totalFacesDetected, 2)
        XCTAssertEqual(r.showId, "breaking-bad")
        XCTAssertNil(r.lowConfidence)
        XCTAssertNil(r.hiddenBySpoilerShield)
        XCTAssertEqual(r.characters.count, 2)
        let w = r.characters[0]
        XCTAssertEqual(w.id, "walter-white")
        XCTAssertEqual(w.characterId, "walter-white")
        XCTAssertEqual(w.name, "Walter White")
        XCTAssertEqual(w.actor, "Bryan Cranston")
        XCTAssertEqual(w.confidence, 0.8123, accuracy: 0.00001)
        XCTAssertEqual(w.arcType, "turning_point")
        XCTAssertEqual(w.firstSeenSeason, 1)
        XCTAssertEqual(w.lastSeenEpisode, 7)
        XCTAssertEqual(w.currentArc, "Plans his next move.")
        XCTAssertEqual(w.importanceRank, 1)
        XCTAssertNil(w.limited)
        // null story fields decode as nil
        let j = r.characters[1]
        XCTAssertNil(j.status)
        XCTAssertNil(j.currentArc)
        XCTAssertNil(j.lastSeenSeason)
        XCTAssertNil(j.relationshipSummary)
    }

    func testIdentifyDecodesLimitedCharacter() async throws {
        respond(200, limitedBody)
        let r = try await client.identify(jpeg: Data([1]), showId: "breaking-bad", season: 1, episode: 1)
        XCTAssertEqual(r.characters.count, 1)
        XCTAssertEqual(r.characters[0].limited, true)
        XCTAssertNil(r.characters[0].firstSeenSeason)
        XCTAssertNil(r.characters[0].currentArc)
    }

    func testIdentifyDecodesAlsoInWhenPresentAndAbsent() async throws {
        respond(200, #"{"characters":[{"character_id":"a","name":"A","confidence":0.9,"also_in":["Better Call Saul (2015)","Film (2006)"]},{"character_id":"b","name":"B","confidence":0.9}],"total_faces_detected":2,"show_id":"bb"}"#)
        let r = try await client.identify(jpeg: Data([1]), showId: "bb", season: 1, episode: 1)
        XCTAssertEqual(r.characters[0].alsoIn, ["Better Call Saul (2015)", "Film (2006)"])
        XCTAssertNil(r.characters[1].alsoIn)
    }

    func testIdentifyDecodesAsOfWhenPresentAndAbsent() async throws {
        let one = #"{"character_id":"saul","name":"Saul","confidence":0.9,"current_arc":"Laundering"%@}"#
        respond(200, #"{"characters":["# + String(format: one, #","as_of":"start""#) + #"],"total_faces_detected":1,"show_id":"bb"}"#)
        var r = try await client.identify(jpeg: Data([1]), showId: "bb", season: 1, episode: 1)
        XCTAssertEqual(r.characters[0].asOf, "start")
        respond(200, #"{"characters":["# + String(format: one, "") + #"],"total_faces_detected":1,"show_id":"bb"}"#)
        r = try await client.identify(jpeg: Data([1]), showId: "bb", season: 1, episode: 1)
        XCTAssertNil(r.characters[0].asOf)
    }

    func testIdentifyDecodesLowConfidence() async throws {
        respond(200, #"{"characters":[],"low_confidence":true,"total_faces_detected":1,"show_id":"breaking-bad"}"#)
        let r = try await client.identify(jpeg: Data([1]), showId: "breaking-bad", season: 1, episode: 1)
        XCTAssertTrue(r.characters.isEmpty)
        XCTAssertEqual(r.lowConfidence, true)
    }

    func testIdentifyDecodesHiddenBySpoilerShield() async throws {
        respond(200, #"{"characters":[],"hidden_by_spoiler_shield":2,"total_faces_detected":2,"show_id":"breaking-bad"}"#)
        let r = try await client.identify(jpeg: Data([1]), showId: "breaking-bad", season: 1, episode: 1)
        XCTAssertEqual(r.hiddenBySpoilerShield, 2)
        XCTAssertNil(r.lowConfidence)
    }

    func testIdentifyMalformedBodyIsDecodingError() async {
        respond(200, #"{"nope":1}"#)
        do {
            _ = try await client.identify(jpeg: Data([1]), showId: "x", season: 1, episode: 1)
            XCTFail("expected throw")
        } catch let e as EngineError {
            XCTAssertEqual(e, .decoding)
        } catch { XCTFail("wrong error \(error)") }
    }

    // MARK: request shape

    func testIdentifySendsBase64ImageAndFourFields() async throws {
        let jpeg = Data([0xFF, 0xD8, 0xFF, 0xE0, 0x10])
        var seen: URLRequest?
        StubURLProtocol.handler = { req in
            seen = req
            return (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    Data(self.fullBody.utf8))
        }
        _ = try await client.identify(jpeg: jpeg, showId: "breaking-bad", season: 3, episode: 7)
        XCTAssertEqual(seen?.httpMethod, "POST")
        XCTAssertEqual(seen?.url?.absoluteString, "http://127.0.0.1:8000/identify")
        XCTAssertEqual(seen?.value(forHTTPHeaderField: "Content-Type"), "application/json")
        let body = try XCTUnwrap(StubURLProtocol.lastBody)
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(Set(obj.keys), ["image", "show_id", "season", "episode"])
        XCTAssertEqual(obj["image"] as? String, jpeg.base64EncodedString())
        XCTAssertEqual(obj["show_id"] as? String, "breaking-bad")
        XCTAssertEqual(obj["season"] as? Int, 3)
        XCTAssertEqual(obj["episode"] as? Int, 7)
    }

    func testLookupSeasonSendsEncodedQueryAndDecodesSeason() async throws {
        var seen: URLRequest?
        StubURLProtocol.handler = { req in
            seen = req
            return (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    Data(#"{"season":2}"#.utf8))
        }
        let season = try await client.lookupSeason(showId: "from", episode: 4, title: "A Rock & a Farway?")
        XCTAssertEqual(season, 2)
        XCTAssertEqual(seen?.url?.path, "/api/episodes/season")
        let items = URLComponents(url: try XCTUnwrap(seen?.url), resolvingAgainstBaseURL: false)?.queryItems
        XCTAssertEqual(items?.first { $0.name == "title" }?.value, "A Rock & a Farway?")
        XCTAssertEqual(items?.first { $0.name == "episode" }?.value, "4")
        XCTAssertEqual(items?.first { $0.name == "show_id" }?.value, "from")
        XCTAssertEqual(seen?.timeoutInterval, 5)
    }

    func testLookupSeasonNullIsNil() async throws {
        respond(200, #"{"season":null}"#)
        let season = try await client.lookupSeason(showId: "x", episode: 1, title: "t")
        XCTAssertNil(season)
    }

    func testLookupSeasonServerErrorThrows() async {
        respond(500, "{}")
        do { _ = try await client.lookupSeason(showId: "x", episode: 1, title: "t"); XCTFail("should throw") }
        catch { XCTAssertEqual(error as? EngineError, .http(500)) }
    }

    func testSetPositionPostsFieldsToApiPosition() async throws {
        var seen: URLRequest?
        StubURLProtocol.handler = { req in
            seen = req
            return (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                    Data(#"{"ok":true}"#.utf8))
        }
        try await client.setPosition(showId: "breaking-bad", season: 2, episode: 5)
        XCTAssertEqual(seen?.httpMethod, "POST")
        XCTAssertEqual(seen?.url?.path, "/api/position")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: try XCTUnwrap(StubURLProtocol.lastBody)) as? [String: Any])
        XCTAssertEqual(obj["show_id"] as? String, "breaking-bad")
        XCTAssertEqual(obj["season"] as? Int, 2)
        XCTAssertEqual(obj["episode"] as? Int, 5)
    }

    func testSetPositionRejectedIsHttpError() async {
        respond(404, #"{"ok":false,"message":"That show isn't set up yet."}"#)
        do {
            try await client.setPosition(showId: "x", season: 1, episode: 1)
            XCTFail("expected throw")
        } catch let e as EngineError { XCTAssertEqual(e, .http(404)) }
        catch { XCTFail("wrong error \(error)") }
    }

    func testSelectShowPostsToApiShowsSelect() async throws {
        var seen: URLRequest?
        StubURLProtocol.handler = { req in
            seen = req
            return (HTTPURLResponse(url: req.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, Data(#"{"ok":true}"#.utf8))
        }
        try await client.selectShow(showId: "severance")
        XCTAssertEqual(seen?.httpMethod, "POST")
        XCTAssertEqual(seen?.url?.path, "/api/shows/select")
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: try XCTUnwrap(StubURLProtocol.lastBody)) as? [String: Any])
        XCTAssertEqual(obj["show_id"] as? String, "severance")
    }

    // MARK: errors

    func testHttp404MapsToHttpError() async {
        respond(404, #"{"detail":"Show 'x' is not loaded."}"#)
        do {
            _ = try await client.identify(jpeg: Data([1]), showId: "x", season: 1, episode: 1)
            XCTFail("expected throw")
        } catch let e as EngineError { XCTAssertEqual(e, .http(404)) }
        catch { XCTFail("wrong error \(error)") }
    }

    func testConnectionRefusedMapsToNotReachable() async {
        StubURLProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        do {
            _ = try await client.state()
            XCTFail("expected throw")
        } catch let e as EngineError { XCTAssertEqual(e, .notReachable) }
        catch { XCTFail("wrong error \(error)") }
    }

    func testHealthFalseWhenUnreachableOrNotOk() async throws {
        StubURLProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        let down = try await client.health()
        XCTAssertFalse(down)
        respond(503, "{}")
        let bad = try await client.health()
        XCTAssertFalse(bad)
    }

    func testHealthTrueOnOk() async throws {
        respond(200, #"{"status":"ok","loaded_shows":["breaking-bad"],"model":"buffalo_l"}"#)
        let ok = try await client.health()
        XCTAssertTrue(ok)
    }

    // MARK: state

    func testStateDecodesWithNullCurrentShow() async throws {
        respond(200, """
        {"setup":{"tmdb_key":false,"anthropic_key":false},"permissions":{},
         "current":{"show_id":null,"show_name":null,"season":1,"episode":1},
         "shows":[],"job":{"state":"idle"},"needs_first_run":true}
        """)
        let s = try await client.state()
        XCTAssertNil(s.current.showId)
        XCTAssertNil(s.current.showName)
        XCTAssertEqual(s.current.season, 1)
        XCTAssertTrue(s.needsFirstRun)
        XCTAssertFalse(s.setup.tmdbKey)
        XCTAssertTrue(s.shows.isEmpty)
    }

    func testStateDecodesCurrentShowAndShowList() async throws {
        respond(200, """
        {"setup":{"tmdb_key":true,"anthropic_key":true},"permissions":{"screen":true},
         "current":{"show_id":"breaking-bad","show_name":"Breaking Bad","season":3,"episode":7},
         "shows":[{"show_id":"breaking-bad","show_name":"Breaking Bad","total_characters":40,
                   "poster_url":null,"season":3,"episode":7,"current":true}],
         "job":null,"needs_first_run":false}
        """)
        let s = try await client.state()
        XCTAssertEqual(s.current.showId, "breaking-bad")
        XCTAssertEqual(s.current.showName, "Breaking Bad")
        XCTAssertEqual(s.current.episode, 7)
        XCTAssertFalse(s.needsFirstRun)
        XCTAssertEqual(s.shows.first?.showId, "breaking-bad")
        XCTAssertEqual(s.shows.first?.current, true)
    }

    // MARK: photo

    func testPhotoReturnsBytes() async throws {
        let bytes = Data([0xFF, 0xD8, 0xFF])
        respond(200, bytes, contentType: "image/jpeg")
        let d = try await client.photo(characterId: "walter-white")
        XCTAssertEqual(d, bytes)
    }

    func testPhoto404ReturnsNil() async throws {
        respond(404, #"{"ok":false,"message":"No photo."}"#)
        let d = try await client.photo(characterId: "nobody")
        XCTAssertNil(d)
    }

    func testPhotoServerErrorThrows() async {
        respond(500, "boom")
        do {
            _ = try await client.photo(characterId: "walter-white")
            XCTFail("expected throw")
        } catch let e as EngineError { XCTAssertEqual(e, .http(500)) }
        catch { XCTFail("wrong error \(error)") }
    }

    func testPhotoPercentEncodesCharacterId() async throws {
        var seen: URLRequest?
        StubURLProtocol.handler = { req in
            seen = req
            return (HTTPURLResponse(url: req.url!, statusCode: 404, httpVersion: nil, headerFields: nil)!, Data())
        }
        _ = try await client.photo(characterId: "a/b c")
        let url = try XCTUnwrap(seen?.url)
        XCTAssertTrue(url.absoluteString.contains("a%2Fb%20c/photo"), url.absoluteString)
        let comps = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        XCTAssertEqual(comps.percentEncodedPath, "/api/characters/a%2Fb%20c/photo")
        // Exactly four segments after the leading slash: the id stays one segment.
        XCTAssertEqual(comps.percentEncodedPath.split(separator: "/").map(String.init),
                       ["api", "characters", "a%2Fb%20c", "photo"])
    }

    // MARK: timeout / cancellation

    func testTimeoutMapsToTimedOutNotNotReachable() async {
        StubURLProtocol.handler = { _ in throw URLError(.timedOut) }
        do {
            _ = try await client.identify(jpeg: Data([1]), showId: "x", season: 1, episode: 1)
            XCTFail("expected throw")
        } catch let e as EngineError { XCTAssertEqual(e, .timedOut) }
        catch { XCTFail("wrong error \(error)") }
    }

    func testCancelledRethrowsAsCancellationError() async {
        StubURLProtocol.handler = { _ in throw URLError(.cancelled) }
        do {
            _ = try await client.identify(jpeg: Data([1]), showId: "x", season: 1, episode: 1)
            XCTFail("expected throw")
        } catch is CancellationError {
            // expected
        } catch { XCTFail("wrong error \(error)") }
    }

    func testHealthDoesNotSwallowCancellation() async {
        StubURLProtocol.handler = { _ in throw URLError(.cancelled) }
        do {
            _ = try await client.health()
            XCTFail("expected throw")
        } catch is CancellationError {
            // expected
        } catch { XCTFail("wrong error \(error)") }
    }

    func testHostNotFoundStaysNotReachable() async {
        StubURLProtocol.handler = { _ in throw URLError(.cannotFindHost) }
        do {
            _ = try await client.state()
            XCTFail("expected throw")
        } catch let e as EngineError { XCTAssertEqual(e, .notReachable) }
        catch { XCTFail("wrong error \(error)") }
    }
}
