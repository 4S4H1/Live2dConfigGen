# L2D Config Editor

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
- 右键在画布创建节点
- 节点内联编辑与右侧 Inspector 同步
- 连线、缩放、平移、框选
- `Ctrl+S` 保存 JSON
- `Ctrl+Z` / `Ctrl+Y` 撤销重做
- `Ctrl+C` / `Ctrl+V` / `Ctrl+D` 复制粘贴与复制节点
- 左侧 JSON 文件浏览、新建、重命名、删除
- 搜索定位与节点高亮
- 校验红框提示
- 底部 CSV 只读预览

## 测试

```bash
python3 -m unittest discover -s tests -v
```
