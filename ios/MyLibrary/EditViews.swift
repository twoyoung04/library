import SwiftUI

struct DraftFields: View {
    @Binding var draft: BookDraft
    var showBookFields = true
    var showCopyFields = true

    var body: some View {
        if showBookFields {
            Section("图书资料") {
                TextField("ISBN / 期刊条码", text: $draft.isbn)
                    .keyboardType(.asciiCapable).textInputAutocapitalization(.characters)
                TextField("书名", text: $draft.title)
                TextField("作者", text: $draft.author)
                TextField("出版社", text: $draft.publisher)
                TextField("出版年", text: Binding(
                    get: { draft.publishedYear.map(String.init) ?? "" },
                    set: { draft.publishedYear = Int($0) }
                )).keyboardType(.numberPad)
                TextField("封面链接", text: $draft.coverUrl)
                    .keyboardType(.URL).textInputAutocapitalization(.never)
                TextField("标签，用逗号分隔", text: $draft.tags)
            }
        }
        if showCopyFields { Section("这一册") {
            TextField("存放位置", text: $draft.location)
            Picker("阅读状态", selection: $draft.status) {
                Text("未读").tag("unread")
                Text("在读").tag("reading")
                Text("已读").tag("read")
                Text("借出").tag("loaned")
            }
            TextField("入藏日期 YYYY-MM-DD", text: Binding(
                get: { draft.acquiredOn ?? "" },
                set: { draft.acquiredOn = $0.isEmpty ? nil : $0 }
            )).keyboardType(.numbersAndPunctuation)
            TextField("备注", text: $draft.notes, axis: .vertical).lineLimit(2...5)
        } }
    }
}

struct EditBookSheet: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    let book: BookInfo
    @State private var draft: BookDraft
    @State private var saving = false
    @State private var message: String?

    init(book: BookInfo) {
        self.book = book
        _draft = State(initialValue: BookDraft(book: book))
    }

    var body: some View {
        NavigationStack {
            Form {
                DraftFields(draft: $draft, showCopyFields: false)
                if let message { Text(message).foregroundStyle(.red) }
            }
            .navigationTitle("编辑图书")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        Task {
                            saving = true; defer { saving = false }
                            do { try await store.updateBook(book.id, draft: draft); dismiss() }
                            catch { message = error.localizedDescription }
                        }
                    }.disabled(saving)
                }
            }
        }
    }
}

struct EditCopySheet: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    let book: BookInfo
    let copy: CopyInfo
    @State private var draft: BookDraft
    @State private var saving = false
    @State private var showDelete = false
    @State private var message: String?

    init(book: BookInfo, copy: CopyInfo) {
        self.book = book; self.copy = copy
        _draft = State(initialValue: BookDraft(book: book, copy: copy))
    }

    var body: some View {
        NavigationStack {
            Form {
                DraftFields(draft: $draft, showBookFields: false)
                Section { Button("删除这一册", role: .destructive) { showDelete = true } }
                if let message { Text(message).foregroundStyle(.red) }
            }
            .navigationTitle("编辑副本")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        Task {
                            saving = true; defer { saving = false }
                            do { try await store.updateCopy(copy.id, draft: draft); dismiss() }
                            catch { message = error.localizedDescription }
                        }
                    }.disabled(saving)
                }
            }
            .confirmationDialog("确定删除这一册？", isPresented: $showDelete) {
                Button("删除", role: .destructive) {
                    Task {
                        do { try await store.deleteCopy(copy.id); dismiss() }
                        catch { message = error.localizedDescription }
                    }
                }
            } message: { Text("如果这是最后一册，对应图书记录也会删除。") }
        }
    }
}
