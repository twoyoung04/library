import AVFoundation
import SwiftUI

final class CameraPreview: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
}

struct ScannerCamera: UIViewRepresentable {
    let active: Bool
    let torch: Bool
    let onCode: (String) -> Void
    let onError: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode, onError: onError) }

    func makeUIView(context: Context) -> CameraPreview {
        let view = CameraPreview()
        view.previewLayer.videoGravity = .resizeAspectFill
        context.coordinator.configure(view)
        return view
    }

    func updateUIView(_ view: CameraPreview, context: Context) {
        context.coordinator.onCode = onCode
        context.coordinator.onError = onError
        context.coordinator.setActive(active, torch: torch)
    }

    static func dismantleUIView(_ view: CameraPreview, coordinator: Coordinator) {
        coordinator.setActive(false, torch: false)
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        var onCode: (String) -> Void
        var onError: (String) -> Void
        let session = AVCaptureSession()
        let queue = DispatchQueue(label: "library.camera.session")
        private var configured = false
        private var desiredActive = false
        private var desiredTorch = false
        private var device: AVCaptureDevice?

        init(onCode: @escaping (String) -> Void, onError: @escaping (String) -> Void) {
            self.onCode = onCode; self.onError = onError
        }

        func configure(_ view: CameraPreview) {
            view.previewLayer.session = session
            let status = AVCaptureDevice.authorizationStatus(for: .video)
            if status == .notDetermined {
                AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                    DispatchQueue.main.async {
                        guard let self else { return }
                        if granted { self.setActive(self.desiredActive, torch: self.desiredTorch) }
                        else { self.onError("请在系统设置中允许相机权限，或改用照片与手动输入。") }
                    }
                }
            } else if status == .denied || status == .restricted {
                DispatchQueue.main.async { self.onError("相机不可用，请检查权限，或改用照片与手动输入。") }
            }
        }

        func setActive(_ active: Bool, torch: Bool) {
            desiredActive = active
            desiredTorch = torch
            queue.async { [weak self] in
                guard let self else { return }
                let allowed = AVCaptureDevice.authorizationStatus(for: .video) == .authorized
                if active && !allowed { return }
                if active && !self.configured {
                    guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
                          let input = try? AVCaptureDeviceInput(device: device),
                          self.session.canAddInput(input) else {
                        DispatchQueue.main.async { self.onError("无法打开后置摄像头。") }
                        return
                    }
                    self.session.beginConfiguration()
                    let output = AVCaptureMetadataOutput()
                    guard self.session.canAddOutput(output) else {
                        self.session.commitConfiguration(); return
                    }
                    self.session.addInput(input)
                    self.session.addOutput(output)
                    output.setMetadataObjectsDelegate(self, queue: .main)
                    if output.availableMetadataObjectTypes.contains(.ean13) {
                        output.metadataObjectTypes = [.ean13]
                    }
                    self.session.commitConfiguration()
                    self.device = device
                    self.configured = true
                }
                if active {
                    if !self.session.isRunning { self.session.startRunning() }
                    self.setTorch(torch)
                } else {
                    self.setTorch(false)
                    if self.session.isRunning { self.session.stopRunning() }
                }
            }
        }

        private func setTorch(_ enabled: Bool) {
            guard let device, device.hasTorch, device.isTorchAvailable else { return }
            let mode: AVCaptureDevice.TorchMode = enabled ? .on : .off
            guard device.torchMode != mode else { return }
            do {
                try device.lockForConfiguration()
                defer { device.unlockForConfiguration() }
                device.torchMode = mode
            } catch {
                DispatchQueue.main.async { self.onError("手电筒暂不可用。") }
            }
        }

        func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                            from connection: AVCaptureConnection) {
            guard desiredActive else { return }
            for object in metadataObjects {
                guard let code = (object as? AVMetadataMachineReadableCodeObject)?.stringValue,
                      let isbn = ISBN.normalize(code) else { continue }
                onCode(isbn)
                break
            }
        }
    }
}
