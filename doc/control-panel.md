# 控制台

控制台是一个本地网页界面，用来把常见的 `scrcpy` 和 `adb` 操作做成按钮。

它不会替换官方 `scrcpy` 客户端，也不会重写 scrcpy 协议。控制台只负责启动
已安装的 `scrcpy` 可执行文件，并通过 `adb` 发送常用 Android 控制命令。

这种方式的目标是：保留官方 scrcpy 的稳定性和性能更新，同时减少记命令和记
快捷键的负担。

## 形态

当前版本是浏览器本地控制台：

```text
浏览器界面 -> 本机 Python 服务 -> scrcpy / adb
```

它和 QtScrcpy 的原生 Qt 桌面窗口不是同一种实现。QtScrcpy 把客户端、渲染、
连接管理和控制界面做成了一个 Qt 应用；本控制台则是官方 scrcpy 的外层
launcher/controller。

## 依赖

先安装 `scrcpy` 和 `adb`，并确认两个命令都能在 `PATH` 中找到：

```bash
scrcpy --version
adb version
```

macOS 使用 Homebrew：

```bash
brew install scrcpy
brew install --cask android-platform-tools
```

## 启动

在仓库根目录运行：

```bash
python3 tools/scrcpy-control-panel.py
```

默认会打开：

```text
http://127.0.0.1:27190/
```

如果不想自动打开浏览器：

```bash
python3 tools/scrcpy-control-panel.py --no-open
```

## 推荐流程

控制台按 4 步引导操作，并会自动禁用当前阶段不该点击的按钮：

1. 检测设备：刷新 ADB 设备列表。
2. 建立连接：USB 设备授权后可直接使用；也可以填写无线地址并点击“无线连接”。
3. 启动投屏：检测到可用 USB 或无线 ADB 设备后，“启动 scrcpy”按钮才可点击。
4. 操控手机：scrcpy 启动后，返回、主页、音量、通知栏、截图等控制按钮才会显示。

## 已实现操作

- 刷新 ADB 设备
- 连接/断开无线 ADB 地址
- 重启本机 ADB 服务
- 为 USB 设备开启 TCP/IP 模式
- 启动/停止 `scrcpy`，启动按钮会在运行时切换为红色停止按钮
- 分组显示启动参数，默认展开常用项，其它视频、音频、窗口、设备行为、输入控制、录制、摄像头、虚拟显示和高级选项默认折叠
- 感知由控制台启动的 `scrcpy` 进程退出，并切换投屏状态
- 返回、主页、多任务、电源、唤醒、熄屏
- 音量加、音量减、静音
- 展开通知栏、展开快捷设置、收起系统面板
- 保存截图到 `~/Pictures/scrcpy-control-panel`
- 通过 `adb shell input text` 输入纯 ASCII 文本
- 对当前设备执行一条 `adb shell` 命令

## 说明

控制台默认绑定 `127.0.0.1`，只有本机可以访问。

文本输入使用 Android 的 `adb shell input text`，只适合可靠输入纯 ASCII。
中文输入和复杂剪贴板同步仍建议使用 scrcpy 自带剪贴板快捷键。

如果手机 IP 变化，刷新设备或用新的 `ip:5555` 地址重新无线连接。

关闭 `scrcpy` 投屏窗口不会自动断开 ADB。网页中的设备连接状态会继续显示
可用，但投屏状态会在轮询刷新后切换为未运行，操控按钮也会随之禁用。

## 锁屏密码黑屏

部分 Android 系统会把锁屏密码、图案、部分金融/隐私应用标记为安全界面。
这些界面可能在 scrcpy 中显示为黑屏，这是系统安全限制，并不适合在控制台中
绕过。

建议先在手机上完成解锁，再启动 scrcpy 或继续远程操控。控制台不会保存或
自动输入锁屏密码。
