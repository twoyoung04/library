import SwiftUI
import Network

enum Palette {
    static let background = Color(red: 0.965, green: 0.952, blue: 0.925)
    static let ink = Color(red: 0.17, green: 0.20, blue: 0.18)
    static let accent = Color(red: 0.53, green: 0.27, blue: 0.20)
    static let muted = Color(red: 0.47, green: 0.48, blue: 0.43)
}

@main
struct MyLibraryApp: App {
    @StateObject private var store = LibraryStore()

    var body: some Scene {
        WindowGroup {
            MainTabs()
                .environmentObject(store)
                .tint(Palette.accent)
        }
    }
}

struct MainTabs: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var scanning = false
    @State private var manualEntry = false
    @State private var manualDraft = BookDraft()

    var body: some View {
        TabView {
            ShelfView(onScan: { scanning = true }, onManual: {
                manualDraft = BookDraft()
                manualEntry = true
            })
                .tabItem { Label("书库", systemImage: "books.vertical") }
            SettingsView().tabItem { Label("设置", systemImage: "gearshape") }
        }
        .fullScreenCover(isPresented: $scanning) { ScanView() }
        .sheet(isPresented: $manualEntry) {
            NewCopySheet(draft: $manualDraft, existingCopyCount: nil, onFinish: { _ in })
        }
        .task { await store.refresh() }
        .task {
            let monitor = NWPathMonitor()
            let paths = AsyncStream<NWPath.Status> { continuation in
                monitor.pathUpdateHandler = { continuation.yield($0.status) }
                continuation.onTermination = { @Sendable _ in monitor.cancel() }
                monitor.start(queue: DispatchQueue(label: "library.network"))
            }
            for await path in paths where path == .satisfied { await store.refresh() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await store.refresh() } }
        }
    }
}

struct LoginView: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    @State private var username = ""
    @State private var password = ""
    @State private var importLocal = false
    @State private var working = false
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("登录账号") {
                    TextField("用户名", text: $username)
                        .textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("密码", text: $password).textContentType(.password)
                }
                if !store.signedIn && store.localCount > 0 {
                    Section {
                        Toggle("同时导入本机书库", isOn: $importLocal)
                    } footer: {
                        Text("将当前本机的 \(store.localCount) 册图书加入账号。原来的本机书库仍可在退出后使用。")
                    }
                }
                if let message { Section { Text(message).foregroundStyle(.red) } }
            }
            .navigationTitle("云同步")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(working) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("登录") { Task { await signIn() } }
                        .disabled(working || (!store.needsLogin && username.isEmpty) || password.isEmpty)
                }
            }
            .disabled(working)
            .interactiveDismissDisabled(working)
            .onAppear { username = store.username }
        }
    }

    private func signIn() async {
        working = true
        message = nil
        defer { working = false }
        do {
            try await store.login(username: username.trimmingCharacters(in: .whitespacesAndNewlines),
                                  password: password, importLocal: importLocal)
            dismiss()
        } catch { message = error.localizedDescription }
    }
}
