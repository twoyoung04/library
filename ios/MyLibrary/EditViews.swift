import SwiftUI

struct DraftFields: View {
    @Binding var draft: BookDraft
    var showBookFields = true
    var showCopyFields = true

    var body: some View {
        if showBookFields {
            Section("书目信息") {
                LabeledContent("书名") { TextField("未填写", text: $draft.title).accessibilityLabel("书名") }
                LabeledContent("作者") { TextField("未填写", text: $draft.author).accessibilityLabel("作者") }
            }
            Section("出版资料") {
                LabeledContent("出版社") {
                    TextField("未填写", text: $draft.publisher).accessibilityLabel("出版社")
                }
                LabeledContent("出版年") {
                    TextField("未填写", value: $draft.publishedYear, format: .number.grouping(.never))
                        .keyboardType(.numberPad).accessibilityLabel("出版年")
                }
                LabeledContent("ISBN") {
                    TextField("未填写", text: $draft.isbn)
                        .keyboardType(.asciiCapable).textInputAutocapitalization(.characters)
                        .autocorrectionDisabled().accessibilityLabel("ISBN 或期刊条码")
                }
            }
            Section("补充资料") {
                LabeledContent("封面链接") {
                    TextField("未填写", text: $draft.coverUrl)
                        .keyboardType(.URL).textInputAutocapitalization(.never).accessibilityLabel("封面链接")
                }
                LabeledContent("标签") { TextField("用逗号分隔", text: $draft.tags).accessibilityLabel("标签") }
            }
        }
        if showCopyFields { Section("我的藏书") {
            LabeledContent("存放位置") { TextField("未填写", text: $draft.location).accessibilityLabel("存放位置") }
            Picker("阅读状态", selection: $draft.status) {
                ForEach(CopyInfo.readingStates, id: \.0) { value, title in
                    Text(title).tag(value)
                }
            }
            TextField("入藏日期 YYYY-MM-DD", text: Binding(
                get: { draft.acquiredOn ?? "" },
                set: { draft.acquiredOn = $0.isEmpty ? nil : $0 }
            )).keyboardType(.numbersAndPunctuation)
            LabeledContent("备注") {
                TextField("未填写", text: $draft.notes, axis: .vertical)
                    .lineLimit(2...5).accessibilityLabel("备注")
            }
        } }
    }
}

struct EditBookSheet: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    let book: BookInfo
    let onBookRemoved: () -> Void
    @State private var draft: BookDraft
    @State private var copies: [CopyInfo]
    @State private var saving = false
    @State private var message: String?

    init(book: BookInfo, onBookRemoved: @escaping () -> Void) {
        self.book = book
        self.onBookRemoved = onBookRemoved
        _draft = State(initialValue: BookDraft(book: book))
        _copies = State(initialValue: book.copies ?? [])
    }

    var body: some View {
        NavigationStack {
            Form {
                DraftFields(draft: $draft, showCopyFields: false)
                if !copies.isEmpty {
                    Section("我的藏书") {
                        ForEach(Array(copies.enumerated()), id: \.element.id) { index, copy in
                            NavigationLink {
                                EditCopyForm(book: book, copy: copy, isLastCopy: copies.count == 1) { updated in
                                    if let updated, let i = copies.firstIndex(where: { $0.id == updated.id }) {
                                        copies[i] = updated
                                    } else if updated == nil {
                                        copies.removeAll { $0.id == copy.id }
                                        if copies.isEmpty {
                                            onBookRemoved()
                                            dismiss()
                                        }
                                    }
                                }
                            } label: {
                                Text(copies.count == 1 ? "编辑位置、状态与备注" : "编辑第 \(index + 1) 册")
                            }
                        }
                    }
                }
                if let message { Text(message).foregroundStyle(.red) }
            }
            .disabled(saving)
            .navigationTitle("编辑图书")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        Task {
                            saving = true; defer { saving = false }
                            do { try store.updateBook(book.id, draft: draft); dismiss() }
                            catch { message = error.localizedDescription }
                        }
                    }.disabled(saving)
                }
            }
            .interactiveDismissDisabled(saving)
        }
    }
}

struct EditCopyForm: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    let book: BookInfo
    let copy: CopyInfo
    let isLastCopy: Bool
    let onChange: (CopyInfo?) -> Void
    @State private var draft: BookDraft
    @State private var saving = false
    @State private var showDelete = false
    @State private var deleting = false
    @State private var message: String?

    init(book: BookInfo, copy: CopyInfo, isLastCopy: Bool, onChange: @escaping (CopyInfo?) -> Void) {
        self.book = book; self.copy = copy
        self.isLastCopy = isLastCopy
        self.onChange = onChange
        _draft = State(initialValue: BookDraft(book: book, copy: copy))
    }

    var body: some View {
        Form {
            DraftFields(draft: $draft, showBookFields: false)
            Section { Button("删除这一册", role: .destructive) { showDelete = true } }
            if let message { Text(message).foregroundStyle(.red) }
        }
        .disabled(saving || deleting)
        .navigationTitle("编辑藏书")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(saving || deleting)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("保存") {
                    Task {
                        saving = true; defer { saving = false }
                        do {
                            let updated = try store.updateCopy(copy.id, draft: draft)
                            dismiss()
                            onChange(updated)
                        }
                        catch { message = error.localizedDescription }
                    }
                }.disabled(saving || deleting)
            }
        }
        .confirmationDialog("确定删除这一册？", isPresented: $showDelete) {
            Button("删除", role: .destructive) {
                Task {
                    deleting = true; defer { deleting = false }
                    do {
                        try store.deleteCopy(copy)
                        if !isLastCopy { dismiss() }
                        onChange(nil)
                    }
                    catch { message = error.localizedDescription }
                }
            }
        } message: { Text("如果这是最后一册，对应图书记录也会删除。") }
        .interactiveDismissDisabled(saving || deleting)
    }
}
