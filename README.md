# L2D Config Editor

基于 `PyQt6` 的节点式 Live2D 交互配置编辑器。

## 安装

```bash
python3 -m pip install -r requirements.txt
```

## 启动

```bash
python3 -m l2d_config_editor.main
```

## 主要能力

- 首次启动选择一次工作区并本地持久化；后续可从“文件 → 更改工作区…”调整
- 一键或批量创建配置底座；版本、作者和目光拖拽待机均可逐角色填写，作者可留空
- 每张图包含唯一的 `idle0（默认待机）` 根节点，交互从 idle0 向外连接
- `TouchIdle` / `TouchDrag` / `ParameterTrigger` / `返回默认待机` 等功能节点
- “返回默认待机”节点强制回到 `idle0`，无需手动填写目标待机
- 右键创建节点，或从输出引脚拖到空白处“快速创建并连接”
- 全贝塞尔曲线连线；高亮、箭头、脉冲和尾迹动画均沿曲线运行
- 深色/白天两套持久化主题、滚轮缩放、中键平移、框选、弹出式搜索和冲突高亮
- `Ctrl+G` 创建持久组框；双击标题直接就地重命名，节点可自由移入和移出
- 备注节点只显示一个可双击编辑的多行大标题
- `Ctrl+V` 将系统剪贴板截图作为参考图贴入画布；参考图经有界解码和尺寸/数量限制后随 JSON 自包含保存
- `Ctrl+S` 保存 JSON
- `Ctrl+Z` / `Ctrl+Y` 撤销重做
- `Ctrl+C` / `Ctrl+V` / `Ctrl+D` 复制粘贴与复制节点，节点槽位和带数字的生成字段会自动递增
- “提交当前 JSON 到 SVN”会先保存文件，必要时自动 `svn add`，再异步提交并显示完整进度；默认 message 为 JSON 文件名
- JSON 配置浏览、新建、重命名和删除
- CSV 预览与批量导出
- 外部 schema 驱动字段定义、CSV 映射与自动生成规则

旧版包含 `Initial` 节点的 JSON 会在加载时自动迁移：元数据进入顶层 `meta`，原节点原位变为 idle0，已有出边保持不变。

## 可配置字段

编辑器默认 schema 位于：

`l2d_config_editor/editor_schema.json`

你可以手工修改这个文件来自定义：

- 节点字段定义
- 字段可见性（保留给兼容和未来节点级模式）
- 默认值
- CSV 列映射
- 自动生成模板

修改后可在程序顶部菜单中使用“重载字段配置”立即生效。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

Windows note:
Run `python -m l2d_config_editor.main` from the repository root.
If your shell is already inside `l2d_config_editor/`, run `python main.py` instead.
