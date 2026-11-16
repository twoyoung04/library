import SwiftUI

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
            Group {
                if store.signedIn { MainTabs() }
                else { LoginView() }
            }
            .environmentObject(store)
            .tint(Palette.accent)
        }
    }
}

struct MainTabs: View {
    @EnvironmentObject private var store: LibraryStore
    @State private var selection = 0

    var body: some View {
        TabView(selection: $selection) {
            ShelfView().tabItem { Label("书库", systemImage: "books.vertical") }.tag(0)
            ScanView(isSelected: selection == 1).tabItem { Label("录入", systemImage: "viewfinder") }.tag(1)
            SettingsView().tabItem { Label("设置", systemImage: "gearshape") }.tag(2)
        }
        .task { await store.refresh() }
    }
}

struct LoginView: View {
    @EnvironmentObject private var store: LibraryStore
    @State private var server = ServerConfig.currentURL()
    @State private var username = ""
    @State private var password = ""
    @State private var working = false
    @State private var message: String?

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Spacer(minLength: 55)
                    Image(systemName: "books.vertical.fill")
                        .font(.system(size: 38, weight: .light)).foregroundStyle(Palette.accent)
                    Text("我的藏书")
                        .font(.system(size: 42, weight: .semibold, design: .serif))
                        .foregroundStyle(Palette.ink)
                    Text("随手录入，慢慢收藏。")
                        .font(.subheadline).foregroundStyle(Palette.muted)
                    VStack(spacing: 14) {
                        TextField("服务地址 · https://…", text: $server)
                            .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Divider()
                        TextField("用户名", text: $username)
                            .textContentType(.username).textInputAutocapitalization(.never)
                        Divider()
                        SecureField("密码", text: $password).textContentType(.password)
                    }
                    .padding(20).background(.white, in: RoundedRectangle(cornerRadius: 20))
                    if let message { Text(message).font(.footnote).foregroundStyle(.red) }
                    Button {
                        Task {
                            working = true; defer { working = false }
                            do { try await store.login(server: server, username: username, password: password) }
                            catch { message = error.localizedDescription }
                        }
                    } label: {
                        HStack { Spacer(); if working { ProgressView().tint(.white) } else { Text("登录书库") }; Spacer() }
                            .font(.headline).padding(16)
                    }
                    .buttonStyle(.plain).foregroundStyle(.white)
                    .background(Palette.ink, in: RoundedRectangle(cornerRadius: 16))
                    .disabled(working || server.isEmpty || username.isEmpty || password.isEmpty)
                    Spacer(minLength: 40)
                }
                .padding(28)
            }
        }
    }
}
