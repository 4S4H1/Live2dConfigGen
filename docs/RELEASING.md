# Windows 本地发布与局域网更新

## 固定环境

发布使用 `pyproject.toml` 和 `uv.lock` 中的 Python 3.13.14、PySide6
6.11.1、PyInstaller 6.21.0、cryptography 48.0.0 和 packaging 26.2。
安装器严格使用仓库隔离的 `.tools\nsis-3.12\makensis.exe`。包含局域网更新
主机分别使用独立的 PyInstaller `onedir`，Qt DLL 保持动态链接。

首次发布前执行：

```powershell
.\scripts\Initialize-ReleaseKey.ps1
```

私钥默认位于 `%LOCALAPPDATA%\4S4H1\release-keys`，不得提交到 Git 或放入
更新主机发布目录。请把它离线备份；私钥丢失后，已安装客户端无法信任用新密钥
签名的更新。v1 的 `release-1` 公钥是固定信任根；轮换必须先用旧密钥发布
内嵌新公钥的桥接安装版，不能通过未签名 HTTP 直接替换。

## 构建

```powershell
.\scripts\Build-Release.ps1 -Notes "1.4.4：修复数据精度、撤销恢复、文件操作、AI 对话、更新下载及安装文件管理"
```

脚本先以锁定环境运行全部测试，再生成图标、PE 版本信息、编辑器与 Host 两个
独立 `onedir`、一个当前用户 NSIS 安装器、签名 `.l2dupdate`、第三方声明和
`SHA256SUMS.txt`。测试通过后，脚本只清理经过绝对路径白名单确认的暂存
发布目录与 PyInstaller 临时目录，防止旧产物混入新版本；暂存产物全部通过
白名单检查后才替换 `dist\release`，失败时保留上一份完整发布。

默认 `minimum_supported_version` 为 `1.0.0`。提高最低支持版本会让更旧
客户端拒绝自动更新；这些用户需要从更新主机首页手动安装完整版本。

`L2DConfigEditor-Setup-1.4.4-x64.exe` 用于首次安装及覆盖升级；同一个安装器
把编辑器和独立托盘 Host 安装到各自的程序根，并只注册一个卸载入口。
Windows 会因未做 Authenticode 签名显示“未知发布者”。

覆盖安装会把默认目录中的旧版独立 Host 原地迁移为伴随组件，并兼容自定义目录
及缺少注册项的遗留安装；只清理旧卸载入口、注册项、精确快捷方式和受管文件，
不会删除 `%LOCALAPPDATA%\4S4H1\L2DUpdateHost` 发布缓存。安装目录及恢复目录中的
未知 JSON、CSV、图片和其他用户文件原地保留，不阻断安装或卸载。旧 Host 元数据
不完整或不匹配时记录警告并跳过清理；路径所有权、进程/文件锁、分阶段替换、
签名、SHA-256 和失败回滚保护仍然生效。

## 发布到更新主机

在发布电脑的编辑器中打开“工具 → 局域网更新主机…”唤醒独立托盘 Host，
点击“导入签名更新包”，
选择 `.l2dupdate`。也可运行：

```powershell
.\scripts\Publish-Release.ps1 -Bundle .\dist\release\L2DConfigEditor-1.4.4.l2dupdate
```

导入流程先验证 Ed25519 原始清单签名、产品/平台/SemVer、文件大小和
SHA-256，在隐藏暂存目录完成后才原子切换 `latest`。更新主机只保留最新与
前一版本。

更新主机默认监听 HTTP TCP 8765，并在固定 UDP 48765 上响应自动发现。客户端
会探测活动 IPv4 网卡的广播地址与 `127.0.0.1`，无需保存 Host 地址或端口；
“更新设置…”中的地址只作为发现失败后的手动回退。只有确有需要时，使用管理员
PowerShell 单独执行 `Add-UpdateHostFirewallRule.ps1`；它为 HTTP TCP 端口和
发现 UDP 48765 分别创建规则，且都仅适用于独立 Host EXE、Private/Domain 网络和
`LocalSubnet`。

发布电脑若有构建生成的完整安装器，直接覆盖安装最简单。1.1.0 及更高版本
若只有 `.l2dupdate`，可由当前编辑器唤醒独立 Host、导入发布包，再在编辑器
中点击“检查更新”；回环探测会发现自身。1.0.0 不包含 UDP 自动发现，因此
首次升级到 1.1.0 时必须直接运行 Setup，或复制旧版 Host 窗口显示的地址；
同机也可按窗口端口手动构造 `http://127.0.0.1:<HTTP 端口>`。安装器下载并
验签完成后，编辑器先通过 IPC 优雅停止 Host，再由缓存目录中的交接进程启动
安装器。PowerShell、安装器及安装后重启均不得以任一程序安装根为当前目录；
两个安装根同时激活或同时回滚，成功后恢复升级前运行的 Host。

## 验收和回滚

至少用两台 Windows 10/11 x64 电脑验证首次安装、覆盖升级、断线续传、
更新主机离线、未保存文档、篡改包拒绝和卸载保留用户数据。客户端和更新主机
各保留两个已验证版本；需要回滚时由用户确认后手动运行前一版本安装器，
系统不会静默降级。客户端缓存位于
`%LOCALAPPDATA%\4S4H1\L2DConfigEditor\updates`，更新主机缓存沿用旧路径
`%LOCALAPPDATA%\4S4H1\L2DUpdateHost\releases`；二者都不是长期备份。
