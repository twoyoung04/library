import Foundation

struct TagInfo: Codable, Identifiable, Hashable {
    let id: Int
    let name: String
}

struct CopyInfo: Codable, Identifiable, Hashable {
    static let readingStates = [("unread", "未读"), ("reading", "在读"), ("read", "已读"), ("loaned", "借出")]
    static let statusLabels = Dictionary(uniqueKeysWithValues: readingStates)

    let id: Int
    let editionId: Int
    var location: String
    var status: String
    var acquiredOn: String?
    var notes: String
    let createdAt: String
    var revision: String?
}

struct BookInfo: Codable, Identifiable, Hashable {
    let id: Int
    var isbn: String?
    var title: String
    var author: String
    var publisher: String
    var publishedYear: Int?
    var coverUrl: String
    var coverSourceUrl: String
    var tags: [TagInfo]
    var copyCount: Int?
    var updatedAt: String
    var copies: [CopyInfo]?
    var revision: String?

    var displayTitle: String { title.isEmpty ? "待补充书名" : title }
}

struct BookItem: Codable, Identifiable {
    let copy: CopyInfo
    let edition: BookInfo
    var id: Int { copy.id }
}

struct BookPage: Codable {
    let items: [BookItem]
    let page: Int
    let nextPage: Int?
    let total: Int
}

struct BookResponse: Codable { let book: BookInfo }
struct LoginResponse: Codable { let token: String; let expiresAt: String; let username: String }
struct FiltersResponse: Codable { let tags: [TagInfo]; let locations: [String] }
struct LookupResponse: Codable {
    let existing: Bool
    let isbn: String
    let book: BookInfo?
    let metadata: BookMetadata?
}

struct BookMetadata: Codable {
    let title: String?
    let author: String?
    let publisher: String?
    let publishedYear: Int?
    let coverUrl: String?
}

struct CreateResponse: Codable {
    let copy: CopyInfo
    let editionId: Int
    let replayed: Bool
    let bookRevision: String?
}
struct CopyResponse: Codable { let copy: CopyInfo }
struct EmptyResponse: Codable { let ok: Bool }

struct BookDraft: Codable {
    var requestId = UUID().uuidString
    var isbn = ""
    var title = ""
    var author = ""
    var publisher = ""
    var publishedYear: Int?
    var coverUrl = ""
    var tags = ""
    var location = ""
    var status = "unread"
    var acquiredOn: String?
    var notes = ""

    init() {}
    init(isbn: String) { self.isbn = isbn }
    init(book: BookInfo, copy: CopyInfo? = nil) {
        isbn = book.isbn ?? ""
        title = book.title
        author = book.author
        publisher = book.publisher
        publishedYear = book.publishedYear
        coverUrl = book.coverSourceUrl
        tags = book.tags.map(\.name).joined(separator: "、")
        location = copy?.location ?? ""
        status = copy?.status ?? "unread"
        acquiredOn = copy?.acquiredOn
        notes = copy?.notes ?? ""
    }

    mutating func apply(_ metadata: BookMetadata) {
        if title.isEmpty { title = metadata.title ?? "" }
        if author.isEmpty { author = metadata.author ?? "" }
        if publisher.isEmpty { publisher = metadata.publisher ?? "" }
        if publishedYear == nil { publishedYear = metadata.publishedYear }
        if coverUrl.isEmpty { coverUrl = metadata.coverUrl ?? "" }
    }

    mutating func nextCopy() {
        requestId = UUID().uuidString
        isbn = ""
        title = ""
        author = ""
        publisher = ""
        publishedYear = nil
        coverUrl = ""
        notes = ""
    }
}

enum ISBN {
    static func normalize(_ raw: String) -> String? {
        var value = raw.uppercased().filter { ("0"..."9").contains($0) || $0 == "X" }
        if value.count == 10 {
            let chars = Array(value)
            guard chars.prefix(9).allSatisfy(\.isNumber) else { return nil }
            let sum = chars.prefix(9).enumerated().reduce(0) { $0 + (10 - $1.offset) * Int(String($1.element))! }
                + (chars[9] == "X" ? 10 : (Int(String(chars[9])) ?? -100))
            guard sum % 11 == 0 else { return nil }
            value = "978" + String(chars.prefix(9))
            value += String(checkDigit(value))
        }
        guard value.count == 13, value.hasPrefix("977") || value.hasPrefix("978") || value.hasPrefix("979"),
              value.allSatisfy(\.isNumber) else { return nil }
        return checkDigit(String(value.prefix(12))) == Int(String(value.last!)) ? value : nil
    }

    private static func checkDigit(_ stem: String) -> Int {
        let sum = stem.enumerated().reduce(0) { $0 + Int(String($1.element))! * ($1.offset.isMultiple(of: 2) ? 1 : 3) }
        return (10 - sum % 10) % 10
    }
}
