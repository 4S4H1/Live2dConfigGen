# L2D Config Editor
todo：
蓝图提示

基于 `PyQt6` 的节点式 L2D 配置编辑器。

## 安装

```bash
python3 -m pip install -r requirements.txt
```

## 启动

```bash
python3 -m l2d_config_editor.main
```

## 已实现能力

- `Initial` / `TouchIdle` / `TouchDrag` / `Comment` 四类节点
- 全局简易 / 高级模式，位于顶部菜单
- 右键在画布创建节点，输出引脚拖到空白处可“快速创建并连接”
- 节点内联编辑与右侧 Inspector 同步
- 深灰蓝画布、细网格 + 粗网格、现代化节点风格
- 连线、缩放、中键平移、框选、冲突高亮
- `Ctrl+S` 保存 JSON
- `Ctrl+Z` / `Ctrl+Y` 撤销重做
- `Ctrl+C` / `Ctrl+V` / `Ctrl+D` 复制粘贴与复制节点
- 左侧 JSON 文件浏览、新建、重命名、删除
- 左上角轻量搜索定位与节点高亮
- Inspector 显示冲突详情和关联节点
- CSV 只读预览通过顶部菜单打开
- 外部 schema 驱动字段定义、CSV 映射与自动生成规则
- 初始节点完成 `作者 / 角色ID / 角色资源名` 前禁止创建其他节点与连线

## 可配置字段

编辑器默认 schema 位于：

`l2d_config_editor/editor_schema.json`

你可以手工修改这个文件来自定义：

- 节点字段定义
- 简易 / 高级模式可见性
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
