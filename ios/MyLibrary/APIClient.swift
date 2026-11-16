import Foundation
import Security

enum APIError: LocalizedError {
    case invalidServer
    case notSignedIn
    case server(String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .invalidServer: "请输入有效的 HTTPS 服务地址。"
        case .notSignedIn: "请重新登录。"
        case .server(let message): message
        case .invalidResponse: "服务器返回了无法识别的数据。"
        }
    }
}

enum TokenStore {
    private static let service = "com.personal.library.mobile-token"

    static func read() -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ token: String?) {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service]
        SecItemDelete(query as CFDictionary)
        guard let token, let data = token.data(using: .utf8) else { return }
        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(attributes as CFDictionary, nil)
    }
}

struct APIClient {
    var server: String
    var token: String?
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()
    private let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }()

    func call<T: Decodable>(_ path: String, method: String = "GET", query: [URLQueryItem] = [],
                            body: Encodable? = nil, authorized: Bool = true) async throws -> T {
        guard let base = URL(string: server), base.scheme == "https", base.host != nil else {
            throw APIError.invalidServer
        }
        var components = URLComponents(url: base.appending(path: path), resolvingAgainstBaseURL: false)!
        components.queryItems = query.isEmpty ? nil : query
        guard let url = components.url else { throw APIError.invalidServer }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 25
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if authorized {
            guard let token else { throw APIError.notSignedIn }
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try encoder.encode(AnyEncodable(body))
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else {
            let message = (try? decoder.decode(ServerError.self, from: data).error) ?? "请求失败（\(response.statusCode)）。"
            throw response.statusCode == 401 && authorized ? APIError.notSignedIn : APIError.server(message)
        }
        do { return try decoder.decode(T.self, from: data) }
        catch { throw APIError.invalidResponse }
    }

    private struct ServerError: Decodable { let error: String }
}

private struct AnyEncodable: Encodable {
    let encodeBlock: (Encoder) throws -> Void
    init(_ value: Encodable) { encodeBlock = value.encode(to:) }
    func encode(to encoder: Encoder) throws { try encodeBlock(encoder) }
}

struct Credentials: Encodable { let username: String; let password: String }
struct BookChanges: Encodable {
    let isbn: String
    let title: String
    let author: String
    let publisher: String
    let publishedYear: Int?
    let coverUrl: String
    let tags: String
    init(_ draft: BookDraft) {
        isbn = draft.isbn; title = draft.title; author = draft.author
        publisher = draft.publisher; publishedYear = draft.publishedYear
        coverUrl = draft.coverUrl; tags = draft.tags
    }
}
struct CopyChanges: Encodable {
    let location: String
    let status: String
    let acquiredOn: String?
    let notes: String
}
