# L2D 交互图表编辑器

面向 Live2D 交互配置的 Windows 节点式编辑器。1.0.0 使用 Python
3.13.14、PySide6 6.11.1 和动态 Qt 共享库，支持 Windows 10/11 x64。

## 安装与启动

普通用户安装：

- `L2DConfigEditor-Setup-1.0.0-x64.exe`：编辑器，默认安装到
  `%LOCALAPPDATA%\Programs\L2DConfigEditor`。局域网更新主机已经作为编辑器的
  内置工具提供，不再需要第二个程序或安装器。

安装器为当前用户安装，不需要管理员权限。只有用户主动创建
局域网防火墙规则时才会单独显示 UAC 确认。程序未做 Authenticode 签名，
Windows 可能显示“未知发布者”。

覆盖安装会安全迁移并移除旧版独立 `L2DUpdateHost` 程序及快捷方式，同时
保留 `%LOCALAPPDATA%\4S4H1\L2DUpdateHost` 中已经导入的发布缓存。编辑器
不会把安装目录作为 JSON 工作区；若旧安装目录或安装恢复目录中检测到
JSON、CSV 或图片工作文件，升级和卸载会中止并提示先迁移文件。

从源码启动：

```powershell
uv sync --extra build --locked
uv run python -m l2d_config_editor.main
```

首次启动时选择 JSON 工作区，之后可从“文件 → 更改工作区…”重新选择。

## 主要能力

- 唯一 `idle0` 根节点，以及 `TouchIdle`、`TouchDrag`、
  `ParameterTrigger`、返回默认待机和备注等节点。
- 快速创建和连接、贝塞尔曲线、持久化分组、参考图片、撤销/重做、
  搜索、CSV 预览与导出。
- 画笔模式：`Ctrl+左键` 自由绘制，`Ctrl+右键` 删除整条线；颜色、粗细及
  每条线的点集随 JSON 保存。
- 简洁展示模式：按用户选择只显示备注、过渡动画、目标待机等字段，并可
  独立隐藏分组、参数表、参考图片和画笔。
- 深色/浅色主题、缩放感知的节点标题和备注字号。
- schema 驱动的字段定义、校验、CSV 映射和自动命名规则。
- 单实例运行；第二次启动会把待打开的文件交给现有窗口。

## JSON 格式

当前写出格式为 `format_version: 3`。v3 在 v2 的基础上增加顶层
`canvas_strokes`：

```json
{
  "format_version": 3,
  "canvas_strokes": [
    {
      "id": "uuid",
      "points": [[120.0, 80.0], [124.0, 84.0]],
      "color": "#2F80ED",
      "width": 4.0
    }
  ]
}
```

v1/v2 文件会在内存中兼容迁移；旧 `Initial` 节点会迁移为 `idle0`，旧回收站
字段会被忽略。高于 v3 的未知格式会拒绝覆盖保存。保存使用同目录临时文件
和原子替换，避免产生半份 JSON。

默认 schema 位于 `l2d_config_editor/editor_schema.json`。

## 局域网签名更新

1. 在发布电脑的编辑器中打开“工具 → 局域网更新主机…”。
2. 更新主机模块导入完整 `.l2dupdate` 包；导入前会验证 Ed25519 原始清单签名、
   产品/平台/版本、安装器大小及 SHA-256，然后才原子发布。
3. 点击“复制地址”，在其他电脑的编辑器中保存一次
   `http://主机名:8765` 或显示的局域网 IP 地址。
4. 编辑器异步检查并后台下载；下载验证成功后由用户确认安装。

更新主机只提供 `GET`、`HEAD`、`Range` 和 `ETag`，不提供远程上传接口，只保留
最新和前一版本；客户端也只缓存两个经过签名复核的安装器供用户确认后手动
回滚。HTTP 不提供传输保密；真实性与完整性由嵌入公钥、Ed25519 签名和
SHA-256 负责。低于清单 `minimum_supported_version` 的客户端必须从 Host
首页手动安装完整版本。防火墙按钮创建的规则仅允许当前编辑器程序、当前
TCP 端口、Private/Domain 网络和 `LocalSubnet`。

## 测试与本地发布

```powershell
uv run python scripts/run_tests.py
.\pack.bat
```

完整发布要求仓库隔离的 Python 3.13.14 和 NSIS 3.12，并且只在本机执行，
不通过 CI 生成发布包。构建、密钥和验收说明见 [BUILDING.md](BUILDING.md)；
第三方许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
