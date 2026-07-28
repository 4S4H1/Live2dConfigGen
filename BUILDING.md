# Windows 构建与发布

本工程的正式产物只在 Windows 10/11 x64 本机生成，不使用 GitHub Actions
或其他 CI 发布。依赖版本由 `pyproject.toml`、`l2d_config_editor/version.py`
和 `uv.lock` 固定。

## 固定工具链

- Python 3.13.14：`.tools\python-3.13.14\python.exe`
- NSIS 3.12：`.tools\nsis-3.12\makensis.exe`
- PySide6 6.11.1
- PyInstaller 6.21.0
- cryptography 48.0.0
- packaging 26.2
- Pillow 12.1.1（仅用于确定性图标渲染）

`.tools`、`.venv`、构建缓存和产物均不提交。`packaging\*.spec`、NSIS
脚本、SVG 图标母版和 PowerShell 发布脚本属于发布源文件，必须提交。

确认锁文件及环境：

```powershell
uv lock --check --python .tools\python-3.13.14\python.exe
uv sync --python .tools\python-3.13.14\python.exe --extra build --locked
uv run python -c "from l2d_config_editor.version import VERSION; print(VERSION)"
```

全量测试使用 `python scripts/run_tests.py`。测试模块会在独立 Python
进程中运行。Windows 上的 PySide6/Qt 偶尔会在 unittest 已经完整结束后，
于 Python 解释器析构阶段发生访问冲突；子进程会先把测试数量、断言结果和
错误详情原子写入并刷新到磁盘，然后跳过这个有缺陷的 Qt 析构阶段。父进程
同时核对结果文件、源代码中声明的测试数量和子进程退出码，因此断言失败、
加载错误、测试期间的原生崩溃或未完整运行都不会被掩盖。`tests` 包会把
QSettings 指向一次性的 INI 目录，不读写开发机注册表偏好。

## 首次创建发布密钥

```powershell
.\scripts\Initialize-ReleaseKey.ps1
```

默认在 `%LOCALAPPDATA%\4S4H1\release-keys` 创建 Ed25519 私钥和公钥，并收紧
私钥 ACL；对应公钥会复制到
`l2d_config_editor\assets\release_public_key.pem` 并应提交。私钥不得进入仓库、
`.l2dupdate`、安装器或更新主机发布目录；请制作离线备份。客户端内嵌公钥，
私钥遗失后不能用任意新密钥无缝替代。

## 构建

```powershell
.\pack.bat
```

也可直接执行：

```powershell
.\scripts\Build-Release.ps1 -Notes "1.0.0 首次完整安装版"
```

后续版本默认仍允许从 `1.0.0` 升级；只有确实放弃旧客户端时才显式传入
`-MinimumSupportedVersion`。构建脚本会拒绝高于发布版本的最低版本。
低于该版本的客户端会拒绝自动更新，用户必须从 Host 首页下载完整安装器
手动升级，因此提高此值前必须先发布迁移通知。

脚本严格检查仓库内 NSIS 是否为 3.12，然后依次：

1. 以 `uv.lock` 同步环境，并逐项核对 Python 与全部发布依赖的精确版本。
2. 校验外部私钥、外部公钥和仓库内嵌公钥属于同一 Ed25519 密钥对。
3. 从 SVG 设计生成编辑器的 PNG/ICO 和 PE 版本资源。
4. 收集锁定环境实际附带的 Python、Qt/PySide6 和其他第三方许可证，并
   附带受控的 GPLv3/LGPLv3 正文及锁定 NSIS 3.12 的完整许可证。
5. 运行全部测试；测试失败时不会清理上一份 `dist\release`。
6. 对预定义的暂存发布目录、`dist\pyinstaller` 和 `build\pyinstaller`
   绝对路径执行白名单校验后重建，杜绝混入上一次构建产物。
7. 用 PyInstaller `onedir` 构建包含更新主机模块的编辑器，保留动态 Qt DLL。
8. 用 NSIS 构建一个当前用户安装器。
9. 生成并签名 `manifest.json`，制作 `.l2dupdate`。
10. 在暂存目录核对文件白名单并为所有顶层产物及 `licenses/` 递归生成
    `SHA256SUMS.txt`；全部成功后才用
    备份—替换方式切换 `dist\release`，失败时保留上一份完整发布。

产物位于 `dist\release`：

- `L2DConfigEditor-Setup-1.0.0-x64.exe`
- `L2DConfigEditor-1.0.0.l2dupdate`
- `SHA256SUMS.txt`
- `THIRD_PARTY_NOTICES.md` 和 `licenses\`

禁止恢复旧的 PyInstaller `--onefile` 构建；更新安装器需要安全替换独立文件，
LGPL Qt 库也必须保持为可替换的动态文件。

## 发布到局域网 Host

推荐在编辑器的“工具 → 局域网更新主机…”中选择“导入签名更新包…”。
命令行等价操作：

```powershell
.\scripts\Publish-Release.ps1 `
  -Bundle .\dist\release\L2DConfigEditor-1.0.0.l2dupdate
```

更新主机拒绝清单或签名过大、ZIP 路径穿越、重复成员、符号链接、哈希/大小不符、
不同产品/平台、重复版本和降级。验证完成前不会更换 `latest`。

### 公钥与轮换

v1 清单使用固定 `key_id: release-1`，客户端和内置更新主机只信任安装时内嵌的单一
公钥，不支持从 HTTP 自动获取或替换信任根。不得只换私钥后直接发布，否则
所有已安装客户端都会拒绝更新。需要轮换时，先用旧私钥发布并安装一个同时
内嵌新公钥的桥接版本，再切换后续发布密钥。更新主机与客户端随同一个编辑器
安装器更新公钥。

### 更新缓存与手动回滚

- 客户端缓存：
  `%LOCALAPPDATA%\4S4H1\L2DConfigEditor\updates\<version>`。
- 更新主机发布缓存（沿用旧版 Host 路径以保留已导入发布）：
  `%LOCALAPPDATA%\4S4H1\L2DUpdateHost\releases\<version>`。

两端都只保留最新与前一版本。客户端缓存同时保存安装器、原始
`manifest.json` 和 `manifest.sig`；回滚入口在启动安装器前重新验证签名、
版本目录、大小和 SHA-256，并始终要求用户确认。服务端导入拒绝降级，所以
回滚只能使用本机已验证缓存手动执行，不会静默或自动发生。两个缓存都不是
长期备份，发布者仍应离线保存正式安装器和 `SHA256SUMS.txt`。

## 验收

发布前至少验证：

- 全新安装、覆盖升级、卸载后用户 JSON/设置仍存在。
- 从有注册项、自定义目录或仅剩默认目录的旧版独立 Host 升级后，只移除
  旧程序和快捷方式，并保留其发布缓存。
- 安装目录或恢复目录中存在 JSON、CSV、图片工作文件时，升级和卸载必须
  中止且原文件保持不变。
- 开始菜单、可选桌面快捷方式、图标、版本资源和单实例文件转交。
- 两台局域网电脑间首次下载、异步检查、Range 续传和更新主机离线。
- 篡改清单、签名、安装器或版本降级均被拒绝。
- 更新主机只响应白名单文件的 `GET`/`HEAD`，路径穿越和写请求被拒绝。
- 防火墙规则只包含当前编辑器 EXE、当前端口、Private/Domain 和 LocalSubnet。
- 最新及前一版安装器可用于用户确认后的手动回滚。
