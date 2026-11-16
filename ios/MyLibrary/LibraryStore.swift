import Foundation
import SwiftUI

enum ServerConfig {
    static let defaultURL = "https://wuminglib.site"

    static func currentURL() -> String {
        let saved = UserDefaults.standard.string(forKey: "libraryServer")
        if saved == "https://lovaime.com" {
            UserDefaults.standard.set(defaultURL, forKey: "libraryServer")
            return defaultURL
        }
        return saved ?? defaultURL
    }
}

@MainActor
final class LibraryStore: ObservableObject {
    @Published var server = ServerConfig.currentURL()
    @Published var token = TokenStore.read()
    @Published var username = UserDefaults.standard.string(forKey: "libraryUsername") ?? ""
    @Published var items: [BookItem] = []
    @Published var tags: [TagInfo] = []
    @Published var locations: [String] = []
    @Published var pending: [BookDraft] = []
    private var cachedDetails: [Int: BookInfo] = [:]
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var total = 0
    @Published var nextPage: Int?
    @Published var search = ""
    @Published var tagId: Int?
    @Published var location = ""
    @Published var sort = "added_desc"
    private var generation = 0

    var client: APIClient { APIClient(server: server, token: token) }
    var signedIn: Bool { token != nil }

    init() {
        if let data = UserDefaults.standard.data(forKey: "libraryCachedBooks"),
           let cached = try? JSONDecoder().decode([BookItem].self, from: data) { items = cached }
        if let data = UserDefaults.standard.data(forKey: "libraryPending"),
           let cached = try? JSONDecoder().decode([BookDraft].self, from: data) { pending = cached }
        if let data = UserDefaults.standard.data(forKey: "libraryCachedDetails"),
           let cached = try? JSONDecoder().decode([Int: BookInfo].self, from: data) { cachedDetails = cached }
    }

    func login(server rawServer: String, username: String, password: String) async throws {
        let normalized = rawServer.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let response: LoginResponse = try await APIClient(server: normalized).call(
            "api/mobile/login/", method: "POST", body: Credentials(username: username, password: password), authorized: false)
        // A different account must never see another account's local book cache.
        items = []; pending = []; cachedDetails = [:]; persist()
        server = normalized
        self.username = response.username
        token = response.token
        TokenStore.save(response.token)
        UserDefaults.standard.set(normalized, forKey: "libraryServer")
        UserDefaults.standard.set(response.username, forKey: "libraryUsername")
        await refresh()
    }

    func logout() async {
        let oldClient = client
        let _: EmptyResponse? = try? await oldClient.call("api/mobile/logout/", method: "POST")
        TokenStore.save(nil)
        token = nil
        username = ""
        items = []; pending = []; cachedDetails = [:]; tags = []; locations = []
        UserDefaults.standard.removeObject(forKey: "libraryUsername")
        persist()
    }

    func refresh() async {
        await loadBooks(reset: true)
        await loadFilters()
        await syncPending()
    }

    func loadBooks(reset: Bool) async {
        if isLoading && !reset { return }
        generation += 1
        let myGeneration = generation
        isLoading = true
        if reset { nextPage = nil }
        let pageNumber = reset ? 1 : (nextPage ?? 1)
        var query = [URLQueryItem(name: "page", value: String(pageNumber)),
                     URLQueryItem(name: "sort", value: sort)]
        if !search.isEmpty { query.append(URLQueryItem(name: "q", value: search)) }
        if let tagId { query.append(URLQueryItem(name: "tag", value: String(tagId))) }
        if !location.isEmpty { query.append(URLQueryItem(name: "location", value: location)) }
        do {
            let result: BookPage = try await client.call("api/mobile/books/", query: query)
            guard myGeneration == generation else { return }
            items = reset ? result.items : items + result.items
            nextPage = result.nextPage
            total = result.total
            errorMessage = nil
            if reset { persist() }
        } catch {
            guard myGeneration == generation else { return }
            if case APIError.notSignedIn = error { token = nil; TokenStore.save(nil) }
            errorMessage = error.localizedDescription
        }
        if myGeneration == generation { isLoading = false }
    }

    func loadMoreIfNeeded(_ item: BookItem) async {
        guard item.id == items.last?.id, nextPage != nil, !isLoading else { return }
        await loadBooks(reset: false)
    }

    func loadFilters() async {
        guard let result: FiltersResponse = try? await client.call("api/mobile/filters/") else { return }
        tags = result.tags; locations = result.locations
    }

    func book(_ id: Int) async throws -> BookInfo {
        do {
            let result: BookResponse = try await client.call("api/mobile/books/\(id)/")
            cachedDetails[id] = result.book
            persist()
            return result.book
        } catch let error as URLError {
            if let cached = cachedDetails[id] { return cached }
            if var listed = items.first(where: { $0.edition.id == id })?.edition {
                listed.copies = items.filter { $0.edition.id == id }.map(\.copy)
                return listed
            }
            throw error
        }
    }

    func lookup(_ isbn: String) async throws -> LookupResponse {
        try await client.call("api/mobile/lookup/", query: [URLQueryItem(name: "isbn", value: isbn)])
    }

    func create(_ draft: BookDraft) async throws -> Bool {
        do {
            let _: CreateResponse = try await client.call("api/mobile/copies/", method: "POST", body: draft)
            pending.removeAll { $0.requestId == draft.requestId }
            persist()
            Task { await loadBooks(reset: true) }
            return true
        } catch let error as URLError {
            if !pending.contains(where: { $0.requestId == draft.requestId }) { pending.append(draft); persist() }
            errorMessage = error.localizedDescription
            return false
        }
    }

    func syncPending() async {
        guard !pending.isEmpty else { return }
        for draft in pending {
            do {
                let _: CreateResponse = try await client.call("api/mobile/copies/", method: "POST", body: draft)
                pending.removeAll { $0.requestId == draft.requestId }
                persist()
            } catch { break }
        }
        await loadBooks(reset: true)
    }

    func updateBook(_ id: Int, draft: BookDraft) async throws {
        let _: BookResponse = try await client.call("api/mobile/books/\(id)/edit/", method: "PATCH", body: BookChanges(draft))
        await loadBooks(reset: true)
    }

    func updateCopy(_ id: Int, draft: BookDraft) async throws {
        let _: CopyResponse = try await client.call("api/mobile/copies/\(id)/", method: "PATCH",
                                                    body: CopyChanges(location: draft.location, status: draft.status,
                                                                      acquiredOn: draft.acquiredOn, notes: draft.notes))
        await loadBooks(reset: true)
    }

    func deleteCopy(_ id: Int) async throws {
        let _: EmptyResponse = try await client.call("api/mobile/copies/\(id)/", method: "DELETE")
        await loadBooks(reset: true)
    }

    private func persist() {
        UserDefaults.standard.set(try? JSONEncoder().encode(items), forKey: "libraryCachedBooks")
        UserDefaults.standard.set(try? JSONEncoder().encode(pending), forKey: "libraryPending")
        UserDefaults.standard.set(try? JSONEncoder().encode(cachedDetails), forKey: "libraryCachedDetails")
    }
}
