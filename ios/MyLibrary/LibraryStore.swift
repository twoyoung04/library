import CryptoKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

enum ServerConfig {
    static let defaultURL = "https://wuminglib.site"
}

private struct LibrarySnapshot: Codable {
    var entries: [BookItem] = []
    var operations: [LibraryOperation] = []
    var nextLocalID = -1
    var identifiers: [Int: String] = [:]
    var remoteIDs: [Int: Int] = [:]
    var bookRevisions: [Int: String] = [:]
    var copyRevisions: [Int: String] = [:]
    var transferred: Set<String> = []

    mutating func allocateID() -> Int {
        defer { nextLocalID -= 1 }
        return nextLocalID
    }
}

private struct LibraryOperation: Codable, Identifiable {
    enum Kind: String, Codable { case add, editBook, editCopy, deleteCopy }
    let id: UUID
    let kind: Kind
    let target: Int
    let draft: BookDraft?
    let edition: BookInfo?

    private enum CodingKeys: String, CodingKey { case id, kind, target, draft, edition }

    init(kind: Kind, target: Int, draft: BookDraft? = nil, edition: BookInfo? = nil) {
        self.id = UUID()
        self.kind = kind
        self.target = target
        self.draft = draft
        self.edition = edition
    }
}

private struct CloudPage: Decodable {
    let items: [BookItem]
    let nextAfter: Int?
}

struct LibraryBackup: FileDocument, Codable {
    static let readableContentTypes: [UTType] = [.json]
    let version: Int
    let books: [BookDraft]

    init(books: [BookDraft]) {
        version = 1
        self.books = books
    }

    init(configuration: ReadConfiguration) throws {
        guard let data = configuration.file.regularFileContents else { throw LocalLibraryError.storage }
        self = try Self.decode(data)
    }

    static func decode(_ data: Data) throws -> Self {
        guard data.count <= 20 * 1024 * 1024,
              let backup = try? JSONDecoder().decode(Self.self, from: data),
              backup.version == 1, backup.books.count <= 20_000 else {
            throw LocalLibraryError.invalid("请选择有效的藏书备份文件（最大 20 MB）。")
        }
        return backup
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: try JSONEncoder().encode(self))
    }
}

enum LocalLibraryError: LocalizedError {
    case invalid(String), missing, storage, accountChanged
    var errorDescription: String? {
        switch self {
        case .invalid(let message): message
        case .missing: "未找到这本书。"
        case .storage: "无法读取或保存本机书库。请检查储存空间后重试。"
        case .accountChanged: "当前书库已切换，请重试。"
        }
    }
}

@MainActor
final class LibraryStore: ObservableObject {
    enum SyncIssue { case changed, missing, rejected }
    @Published private(set) var username = ""
    @Published private(set) var items: [BookItem] = []
    @Published private(set) var tags: [TagInfo] = []
    @Published private(set) var locations: [String] = []
    @Published private(set) var pendingCount = 0
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var storageError: String?
    @Published private(set) var syncIssue: SyncIssue?
    @Published private(set) var syncPaused = false
    @Published private(set) var total = 0
    @Published var search = "" { didSet { showLibrary() } }
    @Published var tagId: Int? { didSet { showLibrary() } }
    @Published var location = "" { didSet { showLibrary() } }
    @Published var sort = "added_desc" { didSet { showLibrary() } }

    private var token: String?
    private var library = LibrarySnapshot()
    private var scope = "local"
    private var generation = UUID()
    private var syncTask: Task<Void, Never>?
    private var storageReady = true
    private var forceOperation: UUID?
    private let storageDirectory: URL?
    private let defaults: UserDefaults

    var signedIn: Bool { !username.isEmpty }
    var needsLogin: Bool { signedIn && token == nil }
    var localCount: Int { library.entries.count }
    private var client: APIClient { APIClient(server: ServerConfig.defaultURL, token: token) }

    init(storageDirectory: URL? = nil, defaults: UserDefaults = .standard) {
        self.storageDirectory = storageDirectory
        self.defaults = defaults
        if let account = defaults.string(forKey: "librarySyncAccount") {
            username = account
            token = TokenStore.read()
            scope = Self.accountScope(account)
            syncPaused = defaults.bool(forKey: scope + "-paused")
        }
        do { library = try read(scope) }
        catch { storageReady = false; storageError = LocalLibraryError.storage.localizedDescription }
        showLibrary()
    }

    private static func accountScope(_ account: String) -> String {
        "account-" + SHA256.hash(data: Data(account.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private func file(_ scope: String) throws -> URL {
        let directory = try storageDirectory ?? FileManager.default.url(for: .applicationSupportDirectory,
            in: .userDomainMask, appropriateFor: nil, create: true).appending(path: "LibraryData", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appending(path: scope + ".json")
    }

    private func read(_ scope: String) throws -> LibrarySnapshot {
        let url = try file(scope)
        guard FileManager.default.fileExists(atPath: url.path) else { return LibrarySnapshot() }
        return try JSONDecoder().decode(LibrarySnapshot.self, from: Data(contentsOf: url))
    }

    private func save(_ snapshot: LibrarySnapshot, to scope: String) throws {
        do {
            let data = try JSONEncoder().encode(snapshot)
            try data.write(to: file(scope), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch { throw LocalLibraryError.storage }
    }

    private func commit(_ next: LibrarySnapshot) throws {
        guard storageReady else { throw LocalLibraryError.storage }
        try save(next, to: scope)
        library = next
        showLibrary()
    }

    func login(username: String, password: String, importLocal: Bool = false) async throws {
        let current = generation
        let response: LoginResponse = try await APIClient(server: ServerConfig.defaultURL).call(
            "api/mobile/login/", method: "POST", body: Credentials(username: username, password: password), authorized: false)
        guard current == generation else { throw LocalLibraryError.accountChanged }
        let nextScope = Self.accountScope(response.username)
        var next = try read(nextScope)
        if defaults.string(forKey: "libraryUsername") == response.username,
           let data = defaults.data(forKey: "libraryPending"),
           let old = try? JSONDecoder().decode([BookDraft].self, from: data) {
            for draft in old where next.transferred.insert(draft.requestId).inserted {
                try Self.append(draft, to: &next, sync: true)
            }
        }
        if importLocal {
            let local = try read("local")
            for entry in local.entries {
                guard let identifier = local.identifiers[entry.id], next.transferred.insert(identifier).inserted else { continue }
                var draft = BookDraft(book: entry.edition, copy: entry.copy)
                draft.requestId = identifier
                try Self.append(draft, to: &next, sync: true)
            }
        }
        try save(next, to: nextScope)
        generation = UUID()
        syncTask?.cancel()
        syncTask = nil
        isLoading = false
        scope = nextScope
        library = next
        storageReady = true
        storageError = nil
        self.username = response.username
        token = response.token
        syncPaused = defaults.bool(forKey: nextScope + "-paused")
        TokenStore.save(response.token)
        defaults.set(response.username, forKey: "librarySyncAccount")
        resetFilters()
        requestSync()
    }

    func logout() {
        do {
            let next = try read("local")
            let previous = client
            generation = UUID()
            syncTask?.cancel()
            syncTask = nil
            isLoading = false
            token = nil
            TokenStore.save(nil)
            defaults.removeObject(forKey: "librarySyncAccount")
            username = ""
            syncPaused = false
            scope = "local"
            library = next
            storageReady = true
            storageError = nil
            errorMessage = nil
            resetFilters()
            Task { let _: EmptyResponse? = try? await previous.call("api/mobile/logout/", method: "POST") }
        } catch { storageError = LocalLibraryError.storage.localizedDescription }
    }

    private func resetFilters() {
        search = ""
        tagId = nil
        location = ""
        sort = "added_desc"
        errorMessage = nil
        syncIssue = nil
        forceOperation = nil
        showLibrary()
    }

    private func showLibrary() {
        let entries = library.entries
        total = entries.count
        pendingCount = library.operations.count
        var unique: [Int: TagInfo] = [:]
        for tag in entries.flatMap({ $0.edition.tags }) { unique[tag.id] = tag }
        tags = unique.values.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        locations = Set(entries.map(\.copy.location).filter { !$0.isEmpty }).sorted()
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        items = entries.filter { item in
            (query.isEmpty || [item.edition.title, item.edition.author, item.edition.publisher,
                                item.edition.isbn ?? "", item.copy.location, item.copy.notes]
                .contains { $0.localizedStandardContains(query) }
                || item.edition.tags.contains { $0.name.localizedStandardContains(query) })
            && (tagId == nil || item.edition.tags.contains { $0.id == tagId })
            && (location.isEmpty || (location == "missing" ? item.copy.location.isEmpty : location == "place:" + item.copy.location))
        }.sorted {
            switch sort {
            case "title_asc", "title_desc":
                let order = $0.edition.displayTitle.localizedStandardCompare($1.edition.displayTitle)
                if order != .orderedSame { return order == (sort == "title_asc" ? .orderedAscending : .orderedDescending) }
            case "year_asc", "year_desc":
                if $0.edition.publishedYear != $1.edition.publishedYear {
                    guard let left = $0.edition.publishedYear else { return false }
                    guard let right = $1.edition.publishedYear else { return true }
                    return sort == "year_asc" ? left < right : left > right
                }
            default: break
            }
            if $0.copy.createdAt == $1.copy.createdAt { return sort == "added_asc" ? $0.id < $1.id : $0.id > $1.id }
            return sort == "added_asc" ? $0.copy.createdAt < $1.copy.createdAt : $0.copy.createdAt > $1.copy.createdAt
        }
    }

    private func resolved(_ id: Int) -> Int { library.remoteIDs[id] ?? id }

    func book(_ id: Int) throws -> BookInfo {
        let matches = library.entries.filter { $0.edition.id == id || $0.edition.id == resolved(id) }
        guard var result = matches.first?.edition else { throw LocalLibraryError.missing }
        result.copies = matches.map(\.copy).sorted { $0.createdAt > $1.createdAt }
        result.copyCount = matches.count
        return result
    }

    func lookup(_ isbn: String) async throws -> LookupResponse {
        if let item = library.entries.first(where: { $0.edition.isbn == isbn }), !item.edition.title.isEmpty {
            return LookupResponse(existing: true, isbn: isbn, book: try book(item.edition.id), metadata: nil)
        }
        return try await client.call("api/mobile/metadata/", query: [URLQueryItem(name: "isbn", value: isbn)], authorized: false)
    }

    private static func validate(_ draft: BookDraft) throws -> String? {
        let raw = draft.isbn.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalized = ISBN.normalize(raw)
        if !raw.isEmpty && normalized == nil { throw LocalLibraryError.invalid("ISBN 格式不正确。") }
        if normalized == nil && draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            throw LocalLibraryError.invalid("请填写书名或 ISBN。")
        }
        if draft.title.count > 300 || draft.author.count > 300 || draft.publisher.count > 200
            || draft.coverUrl.count > 1000 || draft.location.count > 120 {
            throw LocalLibraryError.invalid("书名、出版资料或存放位置超出字数限制。")
        }
        if let year = draft.publishedYear, !(1000...2100).contains(year) {
            throw LocalLibraryError.invalid("出版年应在 1000 至 2100 之间。")
        }
        guard CopyInfo.statusLabels[draft.status] != nil else {
            throw LocalLibraryError.invalid("阅读状态无效。")
        }
        if let date = draft.acquiredOn, !date.isEmpty,
           (try? Date.ISO8601FormatStyle(includingFractionalSeconds: false).year().month().day().dateSeparator(.dash).parse(date)) == nil {
            throw LocalLibraryError.invalid("入藏日期请按 YYYY-MM-DD 填写。")
        }
        if names(draft).contains(where: { $0.count > 80 }) { throw LocalLibraryError.invalid("标签最多 80 字。") }
        return normalized
    }

    private static func names(_ draft: BookDraft) -> [String] {
        let names = draft.tags.components(separatedBy: CharacterSet(charactersIn: ",，、\n"))
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        var seen = Set<String>()
        return names.filter { seen.insert($0).inserted }
    }

    private static func bookTags(_ draft: BookDraft, snapshot: inout LibrarySnapshot) -> [TagInfo] {
        let known = snapshot.entries.flatMap { $0.edition.tags }
        return names(draft).map { name in
            known.first(where: { $0.name == name }) ?? TagInfo(id: snapshot.allocateID(), name: name)
        }
    }

    private static func append(_ draft: BookDraft, to next: inout LibrarySnapshot, sync: Bool) throws {
        guard !next.identifiers.values.contains(draft.requestId) else { return }
        let isbn = try validate(draft)
        let id = next.allocateID()
        let date = Date.now.ISO8601Format()
        let tags = bookTags(draft, snapshot: &next)
        let existing = isbn.flatMap { isbn in next.entries.first(where: { $0.edition.isbn == isbn })?.edition }
        var edition = existing ?? BookInfo(id: next.allocateID(), isbn: isbn, title: draft.title, author: draft.author,
                                           publisher: draft.publisher, publishedYear: draft.publishedYear, coverUrl: draft.coverUrl,
                                           coverSourceUrl: draft.coverUrl, tags: tags, updatedAt: date)
        if existing != nil {
            if edition.title.isEmpty { edition.title = draft.title }
            if edition.author.isEmpty { edition.author = draft.author }
            if edition.publisher.isEmpty { edition.publisher = draft.publisher }
            if edition.publishedYear == nil { edition.publishedYear = draft.publishedYear }
            let known = Set(edition.tags.map(\.name))
            edition.tags += tags.filter { !known.contains($0.name) }
            for i in next.entries.indices where next.entries[i].edition.id == edition.id {
                next.entries[i] = BookItem(copy: next.entries[i].copy, edition: edition)
            }
        }
        let copy = CopyInfo(id: id, editionId: edition.id, location: draft.location, status: draft.status,
                            acquiredOn: draft.acquiredOn, notes: draft.notes, createdAt: date)
        next.entries.append(BookItem(copy: copy, edition: edition))
        next.identifiers[id] = draft.requestId
        if sync { next.operations.append(LibraryOperation(kind: .add, target: id, draft: draft, edition: edition)) }
    }

    @discardableResult func create(_ draft: BookDraft) throws -> Bool {
        var next = library
        try Self.append(draft, to: &next, sync: signedIn)
        try commit(next)
        requestSync()
        return true
    }

    func undoCreate(_ requestID: String) throws {
        guard let id = library.identifiers.first(where: { $0.value == requestID })?.key,
              let copy = library.entries.first(where: { $0.id == id || $0.id == resolved(id) })?.copy else { return }
        try deleteCopy(copy)
    }

    private func portableID(_ item: BookItem) -> String {
        if let identifier = library.identifiers[item.id] { return identifier }
        if let identifier = library.remoteIDs.first(where: {
            $0.value == item.id && library.identifiers[$0.key] != nil
        }).flatMap({ library.identifiers[$0.key] }) {
            return identifier
        }
        let bytes = Array(SHA256.hash(data: Data("\(scope)/copy/\(item.id)".utf8)))
        return UUID(uuid: (bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5],
                           bytes[6], bytes[7], bytes[8], bytes[9], bytes[10], bytes[11],
                           bytes[12], bytes[13], bytes[14], bytes[15])).uuidString
    }

    func backup() -> LibraryBackup {
        LibraryBackup(books: library.entries.map { item in
            var draft = BookDraft(book: item.edition, copy: item.copy)
            draft.requestId = portableID(item)
            return draft
        })
    }

    func importBackup(_ backup: LibraryBackup) throws -> Int {
        var next = library
        var known = Set(library.entries.map(portableID)).union(next.identifiers.values)
        let before = next.entries.count
        for var draft in backup.books {
            guard let id = UUID(uuidString: draft.requestId) else {
                throw LocalLibraryError.invalid("备份含无效的图书标识。")
            }
            draft.requestId = id.uuidString
            if known.insert(draft.requestId).inserted { try Self.append(draft, to: &next, sync: signedIn) }
        }
        try commit(next)
        requestSync()
        return next.entries.count - before
    }

    func updateBook(_ id: Int, draft: BookDraft) throws {
        let isbn = try Self.validate(draft)
        var next = library
        var edition = try book(id)
        edition.copies = nil
        if let isbn, next.entries.contains(where: { $0.edition.id != edition.id && $0.edition.isbn == isbn }) {
            throw LocalLibraryError.invalid("书库中已有这个 ISBN。")
        }
        edition.isbn = isbn
        edition.title = draft.title
        edition.author = draft.author
        edition.publisher = draft.publisher
        edition.publishedYear = draft.publishedYear
        if edition.coverSourceUrl != draft.coverUrl { edition.coverUrl = draft.coverUrl }
        edition.coverSourceUrl = draft.coverUrl
        edition.tags = Self.bookTags(draft, snapshot: &next)
        for i in next.entries.indices where next.entries[i].edition.id == edition.id {
            next.entries[i] = BookItem(copy: next.entries[i].copy, edition: edition)
        }
        if signedIn { next.operations.append(LibraryOperation(kind: .editBook, target: edition.id, draft: draft)) }
        try commit(next)
        requestSync()
    }

    func updateCopy(_ id: Int, draft: BookDraft) throws -> CopyInfo {
        _ = try Self.validate(draft)
        var next = library
        guard let index = next.entries.firstIndex(where: { $0.id == id || $0.id == resolved(id) }) else { throw LocalLibraryError.missing }
        var copy = next.entries[index].copy
        copy.location = draft.location; copy.status = draft.status
        copy.acquiredOn = draft.acquiredOn; copy.notes = draft.notes
        next.entries[index] = BookItem(copy: copy, edition: next.entries[index].edition)
        if signedIn { next.operations.append(LibraryOperation(kind: .editCopy, target: copy.id, draft: draft)) }
        try commit(next)
        requestSync()
        return copy
    }

    func deleteCopy(_ copy: CopyInfo) throws {
        var next = library
        let id = copy.id
        next.entries.removeAll { $0.id == id }
        if signedIn { next.operations.append(LibraryOperation(kind: .deleteCopy, target: id)) }
        try commit(next)
        requestSync()
    }

    func refresh() async {
        requestSync()
        await syncTask?.value
    }

    func setSyncPaused(_ paused: Bool) {
        guard signedIn, paused != syncPaused else { return }
        syncPaused = paused
        defaults.set(paused, forKey: scope + "-paused")
        if paused {
            generation = UUID()
            syncTask?.cancel()
            syncTask = nil
            isLoading = false
        } else {
            requestSync()
        }
    }

    private func requestSync() {
        guard signedIn, !syncPaused, token != nil, storageReady, syncTask == nil, syncIssue == nil else { return }
        isLoading = true
        let current = generation
        syncTask = Task { await runSync(current) }
    }

    func resolveSyncIssue(keepLocal: Bool) {
        guard let issue = syncIssue, let first = library.operations.first, !isLoading else { return }
        do {
            if keepLocal && issue == .changed {
                forceOperation = first.id
            } else {
                var next = library
                next.operations.removeAll { $0.kind == first.kind && resolved($0.target) == resolved(first.target) }
                if issue == .missing && first.kind == .add, let edition = first.edition {
                    next.operations.removeAll { operation in
                        operation.kind != .add && (operation.target == first.target || operation.target == edition.id)
                    }
                }
                try commit(next)
            }
            syncIssue = nil
            requestSync()
        } catch { errorMessage = error.localizedDescription }
    }

    private func runSync(_ current: UUID) async {
        let transport = client
        defer {
            if generation == current { syncTask = nil; isLoading = false }
        }
        do {
            while true {
                while let operation = library.operations.first {
                    try Task.checkCancellation()
                    var remap: CreateResponse?
                    let target = resolved(operation.target)
                    let versions = operation.kind == .editBook ? library.bookRevisions : library.copyRevisions
                    let expected = forceOperation == operation.id ? nil : versions[target]
                    var newRevision: String?
                    switch operation.kind {
                    case .add:
                        guard let draft = operation.draft else { throw LocalLibraryError.storage }
                        let reply: CreateResponse = try await transport.call("api/mobile/copies/", method: "POST", body: draft)
                        remap = reply
                    case .editBook:
                        guard let draft = operation.draft else { throw LocalLibraryError.storage }
                        let reply: BookResponse = try await transport.call("api/mobile/books/\(target)/edit/",
                            method: "PATCH", body: BookChanges(draft), revision: expected)
                        newRevision = reply.book.revision
                    case .editCopy:
                        guard let draft = operation.draft else { throw LocalLibraryError.storage }
                        let reply: CopyResponse = try await transport.call("api/mobile/copies/\(target)/",
                            method: "PATCH", body: CopyChanges(location: draft.location, status: draft.status,
                                                                acquiredOn: draft.acquiredOn, notes: draft.notes), revision: expected)
                        newRevision = reply.copy.revision
                    case .deleteCopy:
                        do {
                            let _: EmptyResponse = try await transport.call("api/mobile/copies/\(target)/",
                                method: "DELETE", revision: expected)
                        } catch APIError.notFound { }
                    }
                    guard generation == current, !Task.isCancelled else { return }
                    var next = library
                    if let remap {
                        next.remoteIDs[operation.target] = remap.copy.id
                        if let edition = operation.edition, edition.id < 0 { next.remoteIDs[edition.id] = remap.editionId }
                        next.copyRevisions[remap.copy.id] = remap.copy.revision
                        next.bookRevisions[remap.editionId] = remap.bookRevision
                    }
                    if let newRevision {
                        if operation.kind == .editBook { next.bookRevisions[target] = newRevision }
                        else { next.copyRevisions[target] = newRevision }
                    }
                    next.operations.removeFirst()
                    try commit(next)
                    if forceOperation == operation.id { forceOperation = nil }
                }
                var remote: [BookItem] = []
                var after = 0
                while true {
                    let page: CloudPage = try await transport.call("api/mobile/snapshot/", query: [URLQueryItem(name: "after", value: String(after))])
                    guard generation == current, !Task.isCancelled else { return }
                    remote.append(contentsOf: page.items)
                    guard let cursor = page.nextAfter else { break }
                    after = cursor
                }
                // A local edit can arrive while a page is downloading. Never replace those edits with an older snapshot.
                if !library.operations.isEmpty { continue }
                var next = library
                next.entries = remote
                next.bookRevisions.removeAll()
                next.copyRevisions.removeAll()
                for item in remote {
                    next.bookRevisions[item.edition.id] = item.edition.revision
                    next.copyRevisions[item.id] = item.copy.revision
                }
                try commit(next)
                errorMessage = nil
                return
            }
        } catch {
            guard generation == current, !Task.isCancelled else { return }
            if case APIError.notSignedIn = error { token = nil; TokenStore.save(nil) }
            if case APIError.conflict = error { syncIssue = .changed }
            if case APIError.notFound = error, !library.operations.isEmpty { syncIssue = .missing }
            if case APIError.rejected = error, !library.operations.isEmpty { syncIssue = .rejected }
            errorMessage = error.localizedDescription
        }
    }
}
