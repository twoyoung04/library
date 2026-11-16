import PhotosUI
import SwiftUI
import UIKit
import Vision

struct ScanView: View {
    @EnvironmentObject private var store: LibraryStore
    @Environment(\.scenePhase) private var scenePhase
    let isSelected: Bool
    @State private var draft = BookDraft()
    @State private var photo: PhotosPickerItem?
    @State private var manualISBN = ""
    @State private var showManual = false
    @State private var showDraft = false
    @State private var autoSave = false
    @State private var continuous = false
    @State private var busy = false
    @State private var scanPaused = false
    @State private var feedback = "将书背条码放入取景框"
    @State private var savedCount = 0
    @State private var recentCode = ""
    @State private var recentTime = Date.distantPast
    @State private var cameraError: String?
    @State private var existingCopyCount: Int?

    private var cameraActive: Bool { isSelected && scenePhase == .active && !showDraft && !busy && !scanPaused }

    var body: some View {
        NavigationStack {
            ZStack {
                Palette.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 17) {
                        HStack(alignment: .firstTextBaseline) {
                            Text("录入新书").font(.system(size: 35, weight: .semibold, design: .serif))
                            Spacer()
                            if savedCount > 0 { Text("本次 \(savedCount) 册").font(.subheadline).foregroundStyle(Palette.accent) }
                        }
                        ZStack {
                            ScannerCamera(active: cameraActive, onCode: { code in
                                Task { await handleCode(code) }
                            }, onError: { cameraError = $0 })
                            .frame(height: 325).clipShape(RoundedRectangle(cornerRadius: 24))
                            RoundedRectangle(cornerRadius: 17).stroke(.white.opacity(0.85), lineWidth: 2)
                                .frame(width: 240, height: 125)
                            VStack {
                                Spacer()
                                Label("对准 ISBN 条码", systemImage: "viewfinder")
                                    .font(.subheadline.weight(.medium)).foregroundStyle(.white)
                                    .padding(.horizontal, 14).padding(.vertical, 8)
                                    .background(.black.opacity(0.60), in: Capsule())
                                    .padding(.bottom, 18)
                            }
                        }
                        if let cameraError { Text(cameraError).font(.footnote).foregroundStyle(.red) }
                        HStack(spacing: 10) {
                            Image(systemName: busy ? "hourglass" : "checkmark.circle.fill")
                            Text(feedback).lineLimit(2)
                        }
                        .font(.subheadline).foregroundStyle(Palette.ink)
                        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                        .background(.white, in: RoundedRectangle(cornerRadius: 14))
                        if scanPaused {
                            Button("继续扫描") { recentCode = ""; scanPaused = false }
                                .font(.headline).frame(maxWidth: .infinity).padding(15)
                                .foregroundStyle(.white)
                                .background(Palette.ink, in: RoundedRectangle(cornerRadius: 14))
                        }
                        HStack(spacing: 12) {
                            PhotosPicker(selection: $photo, matching: .images) {
                                Label("从照片识别", systemImage: "photo")
                                    .frame(maxWidth: .infinity).padding(15)
                            }
                            Button { showManual = true } label: {
                                Label("手动输入", systemImage: "keyboard")
                                    .frame(maxWidth: .infinity).padding(15)
                            }
                        }
                        .buttonStyle(.plain)
                        .background(.white, in: RoundedRectangle(cornerRadius: 14))
                        VStack(alignment: .leading, spacing: 15) {
                            Toggle("扫描后自动保存", isOn: $autoSave)
                            Toggle("连续扫描", isOn: $continuous).disabled(!autoSave)
                            Divider()
                            TextField("存放位置（连续录入时保留）", text: $draft.location)
                            TextField("标签，用逗号分隔", text: $draft.tags)
                            Text("自动保存只在网络可用时立即入库；断网录入会显示为待同步。")
                                .font(.caption).foregroundStyle(Palette.muted)
                        }
                        .padding(18).background(.white, in: RoundedRectangle(cornerRadius: 18))
                    }
                    .padding(18)
                }
            }
            .onChange(of: autoSave) { _, value in if !value { continuous = false } }
            .onChange(of: photo) { _, value in
                guard let value else { return }
                Task {
                    busy = true
                    feedback = "正在识别照片…"
                    if let data = try? await value.loadTransferable(type: Data.self),
                       let code = await PhotoISBN.recognize(data) {
                        busy = false
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
                Button("查询") {
                    let raw = manualISBN; manualISBN = ""
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
                NewCopySheet(draft: $draft, existingCopyCount: existingCopyCount) { saved in
                    if saved { savedCount += 1; feedback = "已录入第 \(savedCount) 册" }
                    else { feedback = "已暂存，等待网络恢复后同步" }
                    if saved || store.pending.contains(where: { $0.requestId == draft.requestId }) {
                        draft.nextCopy()
                    }
                }
            }
        }
    }

    private func handleCode(_ raw: String) async {
        guard let isbn = ISBN.normalize(raw) else { feedback = "ISBN 无效，请核对数字。"; return }
        guard !busy else { return }
        if isbn == recentCode && Date().timeIntervalSince(recentTime) < 2.5 { return }
        recentCode = isbn; recentTime = Date()
        existingCopyCount = nil
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        draft.isbn = isbn
        if autoSave && !isbn.hasPrefix("977") {
            busy = true; feedback = "正在保存 \(isbn)…"
            do {
                let saved = try await store.create(draft)
                if saved { savedCount += 1 }
                feedback = saved ? "已录入第 \(savedCount) 册 · \(isbn)" : "已暂存 \(isbn)，等待同步"
                UINotificationFeedbackGenerator().notificationOccurred(saved ? .success : .warning)
                draft.nextCopy()
            } catch {
                feedback = error.localizedDescription
                UINotificationFeedbackGenerator().notificationOccurred(.error)
            }
            busy = false
            if !continuous { autoSave = false; scanPaused = true }
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
                                let saved = try await store.create(draft)
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
