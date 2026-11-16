import SwiftUI
import UniformTypeIdentifiers

struct ShelfView: View {
    @EnvironmentObject private var store: LibraryStore
    let onScan: () -> Void
    let onManual: () -> Void
    @State private var showFilters = false
    @FocusState private var searchFocused: Bool

    private var searchBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(Palette.muted)
            TextField("书名、作者、ISBN、标签、位置", text: $store.search)
                .focused($searchFocused)
                .submitLabel(.search)
            if !store.search.isEmpty {
                Button {
                    store.search = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Palette.muted)
                        .frame(minWidth: 44, minHeight: 44)
                }
                .accessibilityLabel("清除搜索")
            }
        }
        .padding(.horizontal, 15)
        .frame(minHeight: 46)
        .background(.background, in: RoundedRectangle(cornerRadius: 14))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color(uiColor: .systemGroupedBackground).ignoresSafeArea()
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0, pinnedViews: [.sectionHeaders]) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("藏书").font(.largeTitle.weight(.semibold)).accessibilityAddTraits(.isHeader)
                            Spacer()
                            Text("\(store.total) 册").font(.subheadline).foregroundStyle(Palette.muted)
                        }
                        .padding(.horizontal, 18).padding(.top, 16).padding(.bottom, 4)
                        Section {
                            LazyVStack(alignment: .leading, spacing: 14) {
                                if store.pendingCount > 0 {
                                    Label("\(store.pendingCount) 册等待同步，点击重试", systemImage: "arrow.triangle.2.circlepath")
                                        .font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
                                        .padding(14).background(Color.orange.opacity(0.14), in: RoundedRectangle(cornerRadius: 14))
                                        .onTapGesture { Task { await store.refresh() } }
                                }
                                if let error = store.errorMessage, !store.items.isEmpty {
                                    Text("当前显示已缓存书目 · \(error)")
                                        .font(.caption).foregroundStyle(Palette.muted)
                                }
                                if store.items.isEmpty && !store.isLoading {
                                    if store.search.isEmpty && store.tagId == nil && store.location.isEmpty && store.errorMessage == nil {
                                        ContentUnavailableView {
                                            Label("书库还是空的", systemImage: "books.vertical")
                                        } description: {
                                            Text("扫描书背的 ISBN，添加第一本书。")
                                        } actions: {
                                            Button("扫码录书", action: onScan).buttonStyle(.borderedProminent)
                                            Button("手动录入", action: onManual)
                                        }
                                        .padding(.top, 46)
                                    } else {
                                        ContentUnavailableView(
                                            store.errorMessage == nil ? "没有找到图书" : "暂时无法加载书库",
                                            systemImage: store.errorMessage == nil ? "magnifyingglass" : "wifi.exclamationmark",
                                            description: Text(store.errorMessage ?? "试试其他关键词或筛选条件。")
                                        )
                                        .padding(.top, 46)
                                    }
                                }
                                ForEach(store.items) { item in
                                    NavigationLink(value: item.edition.id) {
                                        BookRow(item: item)
                                    }
                                    .buttonStyle(.plain)
                                }
                                if store.isLoading { ProgressView().frame(maxWidth: .infinity).padding(24) }
                            }
                            .padding(.horizontal, 18).padding(.top, 6).padding(.bottom, 30)
                        } header: {
                            HStack(spacing: 8) {
                                searchBar
                                Button {
                                    searchFocused = false
                                    showFilters = true
                                } label: {
                                    Image(systemName: store.tagId != nil || !store.location.isEmpty || store.sort != "added_desc"
                                          ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease")
                                        .frame(width: 44, height: 44)
                                        .background(.background, in: Circle())
                                }
                                .accessibilityLabel("筛选与排序")
                            }
                            .padding(.horizontal, 18).padding(.vertical, 9)
                            .background(Color(uiColor: .systemGroupedBackground))
                        }
                    }
                }
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await store.refresh() }
            }
            .navigationDestination(for: Int.self) { BookDetailView(bookId: $0) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: onScan) { Image(systemName: "barcode.viewfinder") }
                        .accessibilityLabel("扫码录书")
                }
            }
            .sheet(isPresented: $showFilters) { FilterSheet() }
        }
    }
}

struct BookRow: View {
    let item: BookItem
    var body: some View {
        HStack(spacing: 14) {
            CoverView(url: item.edition.coverUrl).frame(width: 52, height: 73).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(item.edition.displayTitle)
                    .font(.headline)
                Text(item.edition.author.isEmpty ? "作者未录入" : item.edition.author)
                    .font(.subheadline).foregroundStyle(.secondary)
                Text(item.copy.location.isEmpty ? "未设位置" : item.copy.location)
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                .accessibilityHidden(true)
        }
        .padding(.vertical, 5)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

struct CoverView: View {
    let url: String
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8).fill(Color(red: 0.85, green: 0.81, blue: 0.74))
            if let link = URL(string: url), !url.isEmpty {
                AsyncImage(url: link) { image in image.resizable().scaledToFill() }
                    placeholder: { Image(systemName: "book.closed").foregroundStyle(Palette.muted) }
            } else {
                Image(systemName: "book.closed").font(.title2).foregroundStyle(Palette.muted)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct FilterSheet: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            Form {
                Picker("排序", selection: $store.sort) {
                    Text("最近录入").tag("added_desc")
                    Text("最早录入").tag("added_asc")
                    Text("书名 A–Z").tag("title_asc")
                    Text("书名 Z–A").tag("title_desc")
                    Text("出版年 新–旧").tag("year_desc")
                    Text("出版年 旧–新").tag("year_asc")
                }
                Picker("标签", selection: $store.tagId) {
                    Text("全部标签").tag(Optional<Int>.none)
                    ForEach(store.tags) { tag in Text(tag.name).tag(Optional(tag.id)) }
                }
                Picker("位置", selection: $store.location) {
                    Text("全部位置").tag("")
                    Text("未录入位置").tag("missing")
                    ForEach(store.locations, id: \.self) { place in Text(place).tag("place:" + place) }
                }
                Button("清除筛选") { store.tagId = nil; store.location = ""; store.sort = "added_desc" }
            }
            .navigationTitle("筛选与排序")
            .toolbar { ToolbarItem(placement: .confirmationAction) {
                Button("完成") { dismiss() }
            } }
        }
        .presentationDetents([.medium, .large])
    }
}

struct BookDetailView: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    let bookId: Int
    @State private var book: BookInfo?
    @State private var editing = false
    @State private var removedLastCopy = false
    @State private var error: String?

    var body: some View {
        Group {
            if let book {
                List {
                    Section {
                        bookIdentity(book)
                            .listRowBackground(Color.clear)
                            .listRowInsets(EdgeInsets(top: 10, leading: 0, bottom: 12, trailing: 0))
                    }
                    Section("出版资料") {
                        DetailValue("出版社", book.publisher)
                        DetailValue("出版年", book.publishedYear.map(String.init))
                        DetailValue("ISBN", book.isbn, monospaced: true)
                    }
                    if let copies = book.copies, copies.count == 1, let copy = copies.first {
                        Section("我的藏书") {
                            copyDetails(copy)
                            if !book.tags.isEmpty {
                                DetailValue("标签", book.tags.map(\.name).joined(separator: "、"))
                            }
                        }
                    } else {
                        ForEach(Array((book.copies ?? []).enumerated()), id: \.element.id) { index, copy in
                            Section("我的藏书 · 第 \(index + 1) 册") { copyDetails(copy) }
                        }
                        if !book.tags.isEmpty {
                            Section("我的藏书") {
                                DetailValue("标签", book.tags.map(\.name).joined(separator: "、"))
                            }
                        }
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable { await reload() }
            } else if let error {
                ContentUnavailableView {
                    Label("暂时无法加载图书", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(error)
                } actions: {
                    Button("重试") { Task { await reload() } }
                }
            } else {
                ProgressView("正在加载图书")
            }
        }
        .navigationTitle("图书详情")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if book != nil {
                ToolbarItem(placement: .topBarTrailing) { Button("编辑") { editing = true } }
            }
        }
        .task(id: bookId) { await reload() }
        .onChange(of: store.isLoading) { _, value in
            if !value && !editing { Task { await reload() } }
        }
        .sheet(isPresented: $editing, onDismiss: {
            if removedLastCopy { dismiss() }
            else { Task { await reload() } }
        }) {
            if let book { EditBookSheet(book: book, onBookRemoved: { removedLastCopy = true }) }
        }
    }

    private func bookIdentity(_ book: BookInfo) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 18) {
                cover(book)
                identityText(book).fixedSize(horizontal: true, vertical: true)
            }
            VStack(alignment: .leading, spacing: 12) {
                cover(book)
                identityText(book)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private func cover(_ book: BookInfo) -> some View {
        CoverView(url: book.coverUrl).frame(width: 92, height: 126).accessibilityHidden(true)
    }

    private func identityText(_ book: BookInfo) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(book.displayTitle).font(.title2.weight(.semibold))
            Text(book.author.isEmpty ? "作者未录入" : book.author).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func copyDetails(_ copy: CopyInfo) -> some View {
        DetailValue("存放位置", copy.location)
        DetailValue("阅读状态", CopyInfo.statusLabels[copy.status])
        if let date = copy.acquiredOn { DetailValue("入藏日期", date) }
        DetailValue("备注", copy.notes)
    }

    private func reload() async {
        do { book = try store.book(bookId); error = nil }
        catch { self.book = nil; self.error = error.localizedDescription }
    }
}

private struct DetailValue: View {
    let label: String
    let value: String
    let monospaced: Bool

    init(_ label: String, _ value: String?, monospaced: Bool = false) {
        self.label = label
        self.value = value.flatMap { $0.isEmpty ? nil : $0 } ?? "未填写"
        self.monospaced = monospaced
    }

    var body: some View {
        LabeledContent {
            Text(value)
                .font(monospaced ? .system(.subheadline, design: .monospaced) : .body)
                .multilineTextAlignment(.trailing)
                .fixedSize(horizontal: false, vertical: true)
        } label: {
            Text(label)
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: LibraryStore
    @State private var login = false
    @State private var confirmLogout = false
    @State private var resolveConflict = false
    @State private var importBooks = false
    @State private var exportBooks = false
    @State private var backup: LibraryBackup?
    @State private var backupMessage: String?
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    if store.signedIn {
                        LabeledContent("账号", value: store.username)
                        Toggle("自动同步", isOn: Binding(
                            get: { !store.syncPaused },
                            set: { store.setSyncPaused(!$0) }
                        ))
                        LabeledContent("待同步", value: "\(store.pendingCount) 项")
                        if store.needsLogin {
                            Button("重新登录") { login = true }
                        } else {
                            Button(store.isLoading ? "正在同步" : "立即同步") {
                                Task { await store.refresh() }
                            }.disabled(store.isLoading || store.syncIssue != nil || store.syncPaused)
                        }
                        if let message = store.errorMessage { Text(message).foregroundStyle(.secondary) }
                        if store.syncIssue != nil {
                            Button("处理同步冲突") { resolveConflict = true }
                        }
                    } else {
                        Button("登录并开启云同步") { login = true }
                    }
                } header: {
                    Text("云同步")
                } footer: {
                    Text(store.syncPaused ? "同步已暂停。录入和编辑仍会保存在本机。" :
                         store.signedIn ? "录入和编辑先保存在本机，联网后同步到账号。" :
                         "无需登录即可使用。开启同步后，可在其他设备恢复藏书。")
                }
                if let message = store.storageError {
                    Section("本机储存") { Text(message).foregroundStyle(.secondary) }
                }
                Section("书库备份") {
                    Button("导出到文件") {
                        backup = store.backup()
                        exportBooks = true
                    }.disabled(store.storageError != nil || store.localCount == 0)
                    Button("从文件导入") { importBooks = true }.disabled(store.storageError != nil)
                    if let backupMessage { Text(backupMessage).foregroundStyle(.secondary) }
                }
                if store.signedIn {
                    Section {
                        Button("退出账号", role: .destructive) {
                            if store.pendingCount > 0 { confirmLogout = true }
                            else { store.logout() }
                        }
                    }
                }
            }
            .navigationTitle("设置")
            .sheet(isPresented: $login) { LoginView() }
            .fileExporter(isPresented: $exportBooks, document: backup, contentType: .json,
                          defaultFilename: "我的藏书备份") { result in
                switch result {
                case .success: backupMessage = "已导出书库备份。"
                case .failure(let error): backupMessage = error.localizedDescription
                }
                backup = nil
            }
            .fileImporter(isPresented: $importBooks, allowedContentTypes: [.json]) { result in
                do {
                    let url = try result.get()
                    let accessed = url.startAccessingSecurityScopedResource()
                    defer { if accessed { url.stopAccessingSecurityScopedResource() } }
                    let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                    guard size <= 20 * 1024 * 1024 else {
                        throw LocalLibraryError.invalid("备份文件最大为 20 MB。")
                    }
                    let count = try store.importBackup(LibraryBackup.decode(Data(contentsOf: url)))
                    backupMessage = count == 0 ? "这份备份没有新的记录。" : "已导入 \(count) 册。"
                } catch { backupMessage = error.localizedDescription }
            }
            .confirmationDialog("处理同步冲突", isPresented: $resolveConflict) {
                if store.syncIssue == .changed {
                    Button("用本机修改覆盖云端", role: .destructive) { store.resolveSyncIssue(keepLocal: true) }
                }
                Button("放弃本机的冲突修改", role: .destructive) { store.resolveSyncIssue(keepLocal: false) }
            } message: {
                Text(store.errorMessage ?? "请选择保留哪一份。")
            }
            .confirmationDialog("还有未同步的更改", isPresented: $confirmLogout) {
                Button("退出账号", role: .destructive) { store.logout() }
            } message: {
                Text("更改会保留在此账号的本机书库，下次登录后继续同步。")
            }
        }
    }
}
