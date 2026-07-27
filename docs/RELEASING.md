# Windows 本地发布与局域网更新

## 固定环境

发布使用 `pyproject.toml` 和 `uv.lock` 中的 Python 3.13.14、PySide6
6.11.1、PyInstaller 6.21.0、cryptography 48.0.0 和 packaging 26.2。
安装器严格使用仓库隔离的 `.tools\nsis-3.12\makensis.exe`。编辑器与 Host 均使用 PyInstaller
`onedir`，Qt DLL 保持动态链接。

首次发布前执行：

```powershell
.\scripts\Initialize-ReleaseKey.ps1
```

私钥默认位于 `%LOCALAPPDATA%\4S4H1\release-keys`，不得提交到 Git 或放入
Host 发布目录。请把它离线备份；私钥丢失后，已安装客户端无法信任用新密钥
签名的更新。v1 的 `release-1` 公钥是固定信任根；轮换必须先用旧密钥发布
内嵌新公钥的桥接安装版，不能通过未签名 HTTP 直接替换。

## 构建

```powershell
.\scripts\Build-Release.ps1 -Notes "1.0.0 首次完整安装版"
```

脚本先以锁定环境运行全部测试，再生成图标、PE 版本信息、两个 `onedir`、
两个当前用户 NSIS 安装器、签名 `.l2dupdate`、第三方声明和
`SHA256SUMS.txt`。测试通过后，脚本只清理经过绝对路径白名单确认的暂存
发布目录与 PyInstaller 临时目录，防止旧产物混入新版本；暂存产物全部通过
白名单检查后才替换 `dist\release`，失败时保留上一份完整发布。

默认 `minimum_supported_version` 为 `1.0.0`。提高最低支持版本会让更旧
客户端拒绝自动更新；这些用户需要从 Host 首页手动安装完整版本。

`L2DConfigEditor-Setup-1.0.0-x64.exe` 用于首次安装及覆盖升级；
`L2DUpdateHost-Setup-1.0.0-x64.exe` 仅安装在作为局域网更新主机的电脑上。
Windows 会因未做 Authenticode 签名显示“未知发布者”。

## 发布到 Host

在 Host 托盘窗口点击“导入签名更新包”，选择 `.l2dupdate`。也可运行：

```powershell
.\scripts\Publish-Release.ps1 -Bundle .\dist\release\L2DConfigEditor-1.0.0.l2dupdate
```

导入流程先验证 Ed25519 原始清单签名、产品/平台/SemVer、文件大小和
SHA-256，在隐藏暂存目录完成后才原子切换 `latest`。Host 只保留最新与
前一版本。

Host 默认监听 TCP 8765。只有确有需要时，使用管理员 PowerShell 单独执行
`Add-UpdateHostFirewallRule.ps1`；规则仅适用于 Private/Domain 网络和
`LocalSubnet`。客户端保存一次 `http://主机名:8765` 即可。

## 验收和回滚

至少用两台 Windows 10/11 x64 电脑验证首次安装、覆盖升级、断线续传、
Host 离线、未保存文档、篡改包拒绝和卸载保留用户数据。客户端和 Host
各保留两个已验证版本；需要回滚时由用户确认后手动运行前一版本安装器，
系统不会静默降级。客户端缓存位于
`%LOCALAPPDATA%\4S4H1\L2DConfigEditor\updates`，Host 缓存位于
`%LOCALAPPDATA%\4S4H1\L2DUpdateHost\releases`；二者都不是长期备份。
