import SwiftUI

struct ShelfView: View {
    @EnvironmentObject private var store: LibraryStore
    @State private var showFilters = false

    var body: some View {
        NavigationStack {
            ZStack {
                Palette.background.ignoresSafeArea()
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("我的书库").font(.system(size: 35, weight: .semibold, design: .serif))
                            Spacer()
                            Text("\(store.total) 册").font(.subheadline).foregroundStyle(Palette.muted)
                        }
                        .padding(.bottom, 8)
                        if !store.pending.isEmpty {
                            Label("\(store.pending.count) 册等待同步，点击重试", systemImage: "arrow.triangle.2.circlepath")
                                .font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
                                .padding(14).background(Color.orange.opacity(0.14), in: RoundedRectangle(cornerRadius: 14))
                                .onTapGesture { Task { await store.syncPending() } }
                        }
                        if let error = store.errorMessage, !store.items.isEmpty {
                            Text("当前显示已缓存书目 · \(error)")
                                .font(.caption).foregroundStyle(Palette.muted)
                        }
                        if store.items.isEmpty && !store.isLoading {
                            ContentUnavailableView(store.search.isEmpty ? "书库还是空的" : "没有找到图书",
                                                   systemImage: "books.vertical",
                                                   description: Text(store.search.isEmpty ? "切换到录入，扫描第一本书。" : "试试其他关键词或筛选条件。"))
                                .padding(.top, 80)
                        }
                        ForEach(store.items) { item in
                            NavigationLink(value: item.edition.id) {
                                BookRow(item: item)
                            }
                            .buttonStyle(.plain)
                            .onAppear { Task { await store.loadMoreIfNeeded(item) } }
                        }
                        if store.isLoading { ProgressView().frame(maxWidth: .infinity).padding(24) }
                    }
                    .padding(.horizontal, 18).padding(.top, 16).padding(.bottom, 30)
                }
                .refreshable { await store.refresh() }
            }
            .navigationDestination(for: Int.self) { BookDetailView(bookId: $0) }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showFilters = true } label: {
                        Image(systemName: store.tagId != nil || !store.location.isEmpty ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                    }
                }
            }
            .searchable(text: $store.search, prompt: "书名、作者、ISBN、标签、位置")
            .task(id: store.search) {
                try? await Task.sleep(for: .milliseconds(260))
                guard !Task.isCancelled else { return }
                await store.loadBooks(reset: true)
            }
            .sheet(isPresented: $showFilters) { FilterSheet() }
        }
    }
}

struct BookRow: View {
    let item: BookItem
    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            CoverView(url: item.edition.coverUrl).frame(width: 72, height: 102)
            VStack(alignment: .leading, spacing: 7) {
                Text(item.edition.displayTitle)
                    .font(.system(size: 17, weight: .semibold, design: .serif))
                    .foregroundStyle(Palette.ink).lineLimit(2)
                Text(item.edition.author.isEmpty ? "作者未录入" : item.edition.author)
                    .font(.subheadline).foregroundStyle(Palette.muted).lineLimit(1)
                Spacer(minLength: 4)
                HStack(spacing: 5) {
                    Image(systemName: "mappin.circle")
                    Text(item.copy.location.isEmpty ? "位置未录入" : item.copy.location)
                }
                .font(.caption).foregroundStyle(Palette.accent).lineLimit(1)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(Palette.muted)
                .frame(maxHeight: .infinity)
        }
        .padding(14).frame(height: 130)
        .background(.white, in: RoundedRectangle(cornerRadius: 18))
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
                Button("完成") { dismiss(); Task { await store.loadBooks(reset: true) } }
            } }
        }
        .presentationDetents([.medium, .large])
    }
}

struct BookDetailView: View {
    @EnvironmentObject private var store: LibraryStore
    let bookId: Int
    @State private var book: BookInfo?
    @State private var editing = false
    @State private var editingCopy: CopyInfo?
    @State private var error: String?

    var body: some View {
        ScrollView {
            if let book {
                VStack(alignment: .leading, spacing: 22) {
                    HStack(alignment: .top, spacing: 20) {
                        CoverView(url: book.coverUrl).frame(width: 112, height: 158)
                        VStack(alignment: .leading, spacing: 9) {
                            Text(book.displayTitle).font(.system(size: 29, weight: .semibold, design: .serif))
                            Text(book.author.isEmpty ? "作者未录入" : book.author)
                                .foregroundStyle(Palette.muted)
                            Text("\(book.copyCount ?? 0) 册馆藏").font(.subheadline).foregroundStyle(Palette.accent)
                        }
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        InfoLine(label: "ISBN", value: book.isbn ?? "未录入")
                        InfoLine(label: "出版社", value: book.publisher.isEmpty ? "未录入" : book.publisher)
                        InfoLine(label: "出版年", value: book.publishedYear.map(String.init) ?? "未录入")
                    }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 18))
                    if !book.tags.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack { ForEach(book.tags) { Text($0.name).font(.caption).padding(.horizontal, 12).padding(.vertical, 7)
                                .background(Palette.accent.opacity(0.10), in: Capsule()) } }
                        }
                    }
                    Text("实体副本").font(.system(size: 23, weight: .semibold, design: .serif))
                    ForEach(book.copies ?? []) { copy in
                        VStack(alignment: .leading, spacing: 9) {
                            HStack {
                                Text(copy.location.isEmpty ? "位置未录入" : copy.location).font(.headline)
                                Spacer()
                                Button("编辑") { editingCopy = copy }.font(.subheadline)
                            }
                            if let date = copy.acquiredOn { Text("\(date) 入藏").font(.subheadline).foregroundStyle(Palette.muted) }
                            if !copy.notes.isEmpty { Text(copy.notes).font(.subheadline) }
                        }
                        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
                        .background(.white, in: RoundedRectangle(cornerRadius: 16))
                    }
                }
                .padding(18)
            } else if let error {
                ContentUnavailableView("加载失败", systemImage: "wifi.exclamationmark", description: Text(error))
            } else { ProgressView().padding(40) }
        }
        .background(Palette.background)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { if book != nil { ToolbarItem(placement: .topBarTrailing) { Button("编辑图书") { editing = true } } } }
        .task { await reload() }
        .refreshable { await reload() }
        .sheet(isPresented: $editing, onDismiss: { Task { await reload() } }) {
            if let book { EditBookSheet(book: book) }
        }
        .sheet(item: $editingCopy, onDismiss: { Task { await reload() } }) { copy in
            if let book { EditCopySheet(book: book, copy: copy) }
        }
    }

    private func reload() async {
        do { book = try await store.book(bookId); error = nil }
        catch { self.error = error.localizedDescription }
    }
}

private struct InfoLine: View {
    let label: String
    let value: String
    var body: some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(Palette.muted).frame(width: 70, alignment: .leading)
            Text(value).frame(maxWidth: .infinity, alignment: .leading)
        }.font(.subheadline)
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: LibraryStore
    var body: some View {
        NavigationStack {
            Form {
                Section("账号") {
                    LabeledContent("用户名", value: store.username)
                    LabeledContent("服务", value: store.server)
                }
                Section("同步") {
                    LabeledContent("待同步", value: "\(store.pending.count) 册")
                    Button("立即同步") { Task { await store.syncPending() } }.disabled(store.pending.isEmpty)
                }
                Section { Button("退出登录", role: .destructive) { Task { await store.logout() } } }
            }
            .navigationTitle("设置")
        }
    }
}
