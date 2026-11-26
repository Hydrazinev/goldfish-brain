import CoreGraphics
import Foundation

struct ResolvedEpisode: Equatable {
    enum Source: Equatable { case chrome, ocr, stored }
    var season: Int
    var episode: Int
    var source: Source
    /// The episode's name when the page showed it (used to ask the engine which season it belongs to).
    var title: String? = nil
    /// False when only the episode number was known and `season` is the stored one, provisionally.
    var seasonKnown: Bool = true
}

/// Works out which episode is playing: Chrome's page data first, then on-screen text, then the stored position.
/// Calls Chrome (AppleScript), so call it off the main thread.
final class EpisodeResolver {
    static let seasonRange = 1...50
    static let episodeRange = 1...500

    private let chrome: ChromeBridge
    private let ocr: OCRReading
    private let stored: () -> (season: Int, episode: Int)
    private let regions: [CGRect]

    init(chrome: ChromeBridge, ocr: OCRReading, stored: @escaping () -> (season: Int, episode: Int),
         regions: [CGRect] = VisionOCR.defaultRegions) {
        self.chrome = chrome
        self.ocr = ocr
        self.stored = stored
        self.regions = regions
    }

    func resolve(frame: CGImage) -> ResolvedEpisode {
        let fallback = stored()
        if let found = fromChrome(storedSeason: fallback.season) { return found }
        if let episode = EpisodeTextParser.parse(ocr.readText(in: frame, regions: regions)),
           let season = episode.season, Self.isSane(season: season, episode: episode.episode) {
            return ResolvedEpisode(season: season, episode: episode.episode, source: .ocr)
        }
        return ResolvedEpisode(season: fallback.season, episode: fallback.episode, source: .stored)
    }

    /// Absurd numbers (S0E0, S99E999) are misreads, never real: reject them and try the next source.
    static func isSane(season: Int, episode: Int) -> Bool {
        seasonRange.contains(season) && episodeRange.contains(episode)
    }

    private func fromChrome(storedSeason: Int) -> ResolvedEpisode? {
        guard chrome.isRunning(), let tab = chrome.activeTab(), ChromeParsers.isNetflixWatch(url: tab.url) else { return nil }
        // 1. React context (season + episode). 2. Player title element (episode + name, season only sometimes).
        if let episode = chrome.runJS(Self.reactContextJS).flatMap(ChromeParsers.parseReactContext),
           let season = episode.season, Self.isSane(season: season, episode: episode.episode) {
            return ResolvedEpisode(season: season, episode: episode.episode, source: .chrome)
        }
        if let video = chrome.runJS(Self.videoTitleJS).flatMap(ChromeParsers.parseVideoTitle) {
            let season = video.season ?? storedSeason
            if Self.isSane(season: season, episode: video.episode) {
                return ResolvedEpisode(season: season, episode: video.episode, source: .chrome,
                                       title: video.title.isEmpty ? nil : video.title,
                                       seasonKnown: video.season != nil)
            }
        }
        // 3. The pause overlay (season + episode + name), present only while paused; the player title above
        //    is present only while the controls are showing, so one of the two is usually there.
        if let video = chrome.runJS(Self.pauseOverlayJS).flatMap(ChromeParsers.parsePauseOverlay),
           let season = video.season, Self.isSane(season: season, episode: video.episode) {
            return ResolvedEpisode(season: season, episode: video.episode, source: .chrome,
                                   title: video.title.isEmpty ? nil : video.title)
        }
        return nil
    }

    // MARK: Page scripts (ported from the Python app's `_detect_episode`)

    /// "rc:S2E8" from Netflix's React context, or a short diagnostic string.
    static let reactContextJS = """
    (function(){try{var rc=window.netflix&&window.netflix.reactContext;if(rc&&rc.models){var mk=Object.keys(rc.models);\
    for(var i=0;i<mk.length;i++){var m=rc.models[mk[i]];var d=m&&(m.data||m);if(d&&typeof d==='object'){\
    var s=d.seasonNumber||d.season||0;var e=d.episodeNumber||d.episode||0;if(s&&e)return 'rc:S'+s+'E'+e;}}\
    return 'rc_models:'+mk.slice(0,12).join(',');}}catch(ex){return 'rc_err:'+ex;}\
    return 'no_rc|nkeys:'+Object.keys(window.netflix||{}).slice(0,8).join(',');})()
    """

    /// The player title's children joined by "|||" (e.g. "Show|||E2|||Title"), or "NO_TITLE_EL".
    static let videoTitleJS = """
    (function(){var t=document.querySelector('[data-uia=video-title]');if(!t)return 'NO_TITLE_EL';\
    var cs=t.children;if(cs.length>0){var parts=[];for(var i=0;i<cs.length;i++){var tx=cs[i].innerText.trim();\
    if(tx&&parts.indexOf(tx)<0)parts.push(tx);}if(parts.length)return parts.join('|||');}\
    return t.innerText.trim()||'NO_TITLE_EL';})()
    """

    /// "Season 1|||A Rock and a Farway: Ep. 4" from Netflix's pause overlay, or "NO_OVERLAY".
    static let pauseOverlayJS = """
    (function(){function g(k){var e=document.querySelector('[data-uia="'+k+'"]');return e?(e.textContent||'').trim():'';}\
    var s=g('evidence-overlay-season-title'),e=g('evidence-overlay-episode-title');\
    return (s&&e)?s+'|||'+e:'NO_OVERLAY';})()
    """
}
