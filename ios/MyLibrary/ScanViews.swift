import PhotosUI
import SwiftUI
import UIKit
import Vision

struct ScanView: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var torch = false
    @State private var scanSettings = false
    @State private var lastSavedID: String?
    @State private var draft = BookDraft()
    @State private var photo: PhotosPickerItem?
    @State private var manualISBN = ""
    @State private var showManual = false
    @State private var showDraft = false
    @State private var continuous = false
    @State private var busy = false
    @State private var scanPaused = false
    @State private var feedback = "将书背条码放入取景框"
    @State private var savedCount = 0
    @State private var recentCode = ""
    @State private var cameraError: String?
    @State private var existingCopyCount: Int?

    private var cameraActive: Bool { scenePhase == .active && !showDraft && !showManual && !scanSettings && !busy }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            ScannerCamera(active: cameraActive, torch: torch, onCode: { code in
                Task { await handleCode(code) }
            }, onError: { cameraError = $0 })
            .ignoresSafeArea()

            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(.white.opacity(0.85), lineWidth: 2)
                .frame(width: 250, height: 120)
                .accessibilityHidden(true)
                .allowsHitTesting(false)

            VStack(spacing: 12) {
                HStack {
                    Button { dismiss() } label: { Image(systemName: "xmark") }
                        .accessibilityLabel("关闭扫码")
                    Spacer()
                    Text(continuous ? "连续扫描" : "扫描 ISBN").font(.headline)
                    Spacer()
                    Button { torch.toggle() } label: {
                        Image(systemName: torch ? "flashlight.on.fill" : "flashlight.off.fill")
                    }.accessibilityLabel(torch ? "关闭手电筒" : "开启手电筒")
                }
                Spacer()
                VStack(spacing: 12) {
                    Text(cameraError ?? feedback).font(.subheadline)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                    if savedCount > 0 {
                        HStack {
                            Text("本次录入 \(savedCount) 册")
                            Spacer()
                            if let lastSavedID {
                                Button("撤销上一册") { undo(lastSavedID) }
                            }
                        }
                        .font(.subheadline)
                    }
                    if scanPaused {
                        Button("继续扫描") { scanPaused = false; feedback = "将书背条码放入取景框" }
                    } else if continuous && !recentCode.isEmpty {
                        Button("再次扫描相同条码") { recentCode = "" }
                    }
                    HStack {
                        PhotosPicker(selection: $photo, matching: .images) {
                            Label("相册", systemImage: "photo")
                        }
                        Spacer()
                        Button { showManual = true } label: { Label("手动输入", systemImage: "keyboard") }
                        Spacer()
                        Button { scanSettings = true } label: {
                            Label("录入设置", systemImage: "slider.horizontal.3")
                        }
                    }
                    .labelStyle(.iconOnly)
                }
                .padding(14)
                .background(.black.opacity(0.78), in: RoundedRectangle(cornerRadius: 22))
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 12)
            .buttonStyle(ScannerButtonStyle())
            .foregroundStyle(.white)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $scanSettings) {
            NavigationStack {
                Form {
                    Toggle("连续扫描并保存", isOn: $continuous)
                    TextField("存放位置", text: $draft.location)
                    TextField("标签，用逗号分隔", text: $draft.tags)
                }
                .navigationTitle("录入设置")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) { Button("完成") { scanSettings = false } }
                }
            }
            .presentationDetents([.medium])
        }
        .onChange(of: photo) { _, value in
            guard let value else { return }
            Task {
                busy = true
                feedback = "正在识别照片…"
                if let data = try? await value.loadTransferable(type: Data.self),
                   let code = await PhotoISBN.recognize(data) {
                    busy = false
                    scanPaused = false
                    recentCode = ""
                    await handleCode(code)
                } else {
                    busy = false
                    feedback = "未找到有效 ISBN，请拍清楚条码或数字。"
                }
                photo = nil
            }
        }
        .alert("输入 ISBN", isPresented: $showManual) {
            TextField("ISBN-10 / ISBN-13", text: $manualISBN)
            Button("录入") {
                let raw = manualISBN; manualISBN = ""; scanPaused = false; recentCode = ""
                Task { await handleCode(raw) }
            }
            Button("没有 ISBN，手动录入") {
                manualISBN = ""
                draft.nextCopy()
                existingCopyCount = nil
                scanPaused = true
                showDraft = true
            }
            Button("取消", role: .cancel) { manualISBN = "" }
        } message: { Text("也可在下一步手动补充书名。") }
        .sheet(isPresented: $showDraft) {
            NewCopySheet(draft: $draft, existingCopyCount: existingCopyCount) { _ in
                lastSavedID = draft.requestId
                savedCount += 1
                feedback = "已保存到本机"
                draft.nextCopy()
            }
            .presentationDetents([.large])
        }
    }

    private func undo(_ requestID: String) {
        do {
            try store.undoCreate(requestID)
            lastSavedID = nil
            savedCount = max(0, savedCount - 1)
            feedback = "已撤销上一册"
        } catch { feedback = error.localizedDescription }
    }

    private func handleCode(_ raw: String) async {
        guard let isbn = ISBN.normalize(raw) else { feedback = "ISBN 无效，请核对数字。"; return }
        guard !busy, !showDraft, !scanPaused else { return }
        if isbn == recentCode { return }
        if draft.isbn != isbn { draft.nextCopy() }
        recentCode = isbn
        existingCopyCount = nil
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        draft.isbn = isbn
        if continuous && !isbn.hasPrefix("977") {
            busy = true; feedback = "正在保存 \(isbn)…"
            do {
                let saved = try store.create(draft)
                if saved { savedCount += 1; lastSavedID = draft.requestId }
                feedback = "已保存 ISBN \(isbn)"
                UINotificationFeedbackGenerator().notificationOccurred(saved ? .success : .warning)
                draft.nextCopy()
            } catch {
                feedback = error.localizedDescription
                UINotificationFeedbackGenerator().notificationOccurred(.error)
            }
            busy = false
        } else {
            scanPaused = true
            if isbn.hasPrefix("977") { feedback = "期刊条码：请在备注中核对并填写期号" }
            else { feedback = "已识别 \(isbn)，请核对资料" }
            showDraft = true
            let lookupRequestId = draft.requestId
            do {
                let result = try await store.lookup(isbn)
                guard showDraft && draft.requestId == lookupRequestId && draft.isbn == isbn else { return }
                if let book = result.book {
                    existingCopyCount = book.copyCount
                    draft.apply(BookMetadata(title: book.title, author: book.author,
                        publisher: book.publisher, publishedYear: book.publishedYear, coverUrl: book.coverUrl))
                }
                else if let metadata = result.metadata { draft.apply(metadata) }
            } catch { /* Manual entry remains available. */ }
        }
    }
}

private struct ScannerButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.horizontal, 8)
            .frame(minWidth: 44, minHeight: 44)
            .background(.white.opacity(configuration.isPressed ? 0.30 : 0.16), in: Capsule())
    }
}

struct NewCopySheet: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.dismiss) private var dismiss
    @Binding var draft: BookDraft
    let existingCopyCount: Int?
    let onFinish: (Bool) -> Void
    @State private var saving = false
    @State private var message: String?

    var body: some View {
        NavigationStack {
            Form {
                if let existingCopyCount {
                    Section {
                        Label("这本书已有 \(existingCopyCount) 册；保存会新增一册，不会覆盖原有资料。", systemImage: "books.vertical")
                            .font(.footnote)
                    }
                }
                if draft.isbn.hasPrefix("977") {
                    Section { Label("请核对期号并写入备注，避免把不同期刊混为一册。", systemImage: "exclamationmark.circle")
                        .font(.footnote) }
                }
                DraftFields(draft: $draft)
                if let message { Text(message).foregroundStyle(.red) }
            }
            .navigationTitle("确认录入")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        Task {
                            saving = true; defer { saving = false }
                            do {
                                let saved = try store.create(draft)
                                onFinish(saved); dismiss()
                            } catch { message = error.localizedDescription }
                        }
                    }.disabled(saving || (draft.isbn.isEmpty && draft.title.isEmpty))
                }
            }
        }
    }
}

enum PhotoISBN {
    static func recognize(_ data: Data) async -> String? {
        await Task<String?, Never>.detached(priority: .userInitiated) {
            let barcode = VNDetectBarcodesRequest()
            if (try? VNImageRequestHandler(data: data, options: [:]).perform([barcode])) != nil {
                for result in barcode.results ?? [] {
                    if let raw = result.payloadStringValue, let value = ISBN.normalize(raw) { return value }
                }
            }
            let text = VNRecognizeTextRequest()
            text.recognitionLevel = .accurate
            guard (try? VNImageRequestHandler(data: data, options: [:]).perform([text])) != nil else { return nil }
            let content = (text.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
            let patterns = [#"(?:977|978|979)(?:[\s-]?\d){10}"#, #"\d(?:[\s-]?\d){8}[\s-]?[\dXx]"#]
            for pattern in patterns {
                guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
                for match in regex.matches(in: content, range: NSRange(content.startIndex..., in: content)) {
                    if let range = Range(match.range, in: content), let value = ISBN.normalize(String(content[range])) {
                        return value
                    }
                }
            }
            return nil
        }.value
    }
}
