# 我的藏书 iOS

原生 SwiftUI 客户端，使用同一套 Django 数据。最低 iOS 17。需要完整 Xcode；本机仅安装 Command Line Tools 时无法编译或运行模拟器。

1. 安装 Xcode 和 [XcodeGen](https://github.com/yonaskolb/XcodeGen)。
2. 在 `ios` 目录运行 `xcodegen generate`，打开生成的 `MyLibrary.xcodeproj`。
3. 在 Xcode 中将 Bundle Identifier 改为自己的标识，并选择签名团队。
4. 运行 App，输入现有账号。App 固定连接 `https://wuminglib.site`，界面不展示服务器地址。服务需部署包含 `/api/mobile/` 接口的版本。

应用支持书库浏览、搜索、详情与编辑、原生相机扫码、照片中的条码或 ISBN 数字识别、手动录入、连续扫描、离线浏览和待同步录入。扫码录入请求使用固定 UUID；网络重试不会重复新增副本。
