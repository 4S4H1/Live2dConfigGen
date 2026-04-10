import sys
import json
import os
from collections import deque
from PySide6.QtWidgets import (QApplication, QMainWindow, QGraphicsScene, QGraphicsView, 
                               QGraphicsItem, QGraphicsPathItem, QWidget, QVBoxLayout, QHBoxLayout, 
                               QPushButton, QTextEdit, QSplitter, QLabel, QLineEdit, QFormLayout, 
                               QMenu, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QComboBox, QInputDialog)
from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QLineF, QObject, QSettings
from PySide6.QtGui import (QPainter, QPen, QBrush, QColor, QPainterPath, QFont, QTransform, QAction, QKeySequence, QShortcut, QPalette)

# ==========================================
# PART 0: 节点配置与注册 (Configuration)
# ==========================================

NODE_TYPES = {
    100: {
        "name": "剧情片段 (Story)",
        "color": "#1E88E5", # 蓝
        "default_outputs": [{"name": "下一步", "param": "", "cost": ""}],
        "allow_custom_ports": True 
    },
    102: {
        "name": "奖励掉落 (Drop)",
        "color": "#E53935", # 红
        "default_outputs": [{"name": "下一步", "param": "", "cost": ""}],
        "allow_custom_ports": True
    }
}

# 后续逻辑模式枚举
NEXT_MODE_DEFAULT = 0   # 直接跳转 (线性)
NEXT_MODE_OPTION = 1    # 选项分支
NEXT_MODE_PROB = 2      # 概率分支

NEXT_MODE_NAMES = {
    NEXT_MODE_DEFAULT: "直接跳转 (Linear)",
    NEXT_MODE_OPTION: "选项分支 (Options)",
    NEXT_MODE_PROB: "概率分支 (Probability)"
}

# 掉落表现类型枚举
DROP_CLIENT_TYPES = [
    "1-文本演出表现",
    "2-主界面弹条",
    "3-事件掉落",
    "4-大头贴掉落"
]

def get_next_type_value(current_mode, target_node_type=None):
    if current_mode == NEXT_MODE_OPTION:
        return 2
    elif current_mode == NEXT_MODE_PROB:
        return 3
    else:
        if target_node_type == 100: return 1 
        if target_node_type == 102: return 1 
        return 1 

# ==========================================
# PART 1: 数据模型 (The Model)
# ==========================================

class NodeModel(QObject):
    data_changed = Signal() 

    def __init__(self, node_id, node_type, title, x=0, y=0):
        super().__init__()
        self.id = node_id
        self.type = node_type
        self.title = title
        self.x = x
        self.y = y
        
        # --- 核心逻辑属性 ---
        self.next_mode = NEXT_MODE_DEFAULT 
        
        # --- 业务属性 ---
        self.text_content = ""
        
        # 掉落节点专属
        self.drop_behavior = 1 # 修改需求: 默认改为 1
        self.drop_id = ""
        self.drop_type_client = 3 # 掉落表现类型，默认3
        
        self.inputs = [{"name": "入口"}]
        
        config = NODE_TYPES.get(node_type, {})
        if config:
            self.outputs = [dict(p) for p in config.get("default_outputs", [])]
            self.color_header = config.get("color", "#333")
        else:
            self.outputs = [{"name": "Next", "param": "", "cost": ""}]
            self.color_header = "#555"
            
        self.color_body = "#1E1E1E"

    def restore_ports(self, saved_outputs):
        if saved_outputs:
            for p in saved_outputs:
                if "cost" not in p: p["cost"] = ""
                if "param" not in p: p["param"] = ""
            self.outputs = saved_outputs
            self.data_changed.emit()

    def add_output_port(self):
        idx = len(self.outputs) + 1
        prefix = "选项" if self.next_mode == NEXT_MODE_OPTION else ("分支" if self.next_mode == NEXT_MODE_PROB else "输出")
        self.outputs.append({"name": f"{prefix} {idx}", "param": "", "cost": ""})
        self.data_changed.emit()

    def remove_output_port(self, index):
        if 0 <= index < len(self.outputs):
            self.outputs.pop(index)
            self.data_changed.emit()
            
    def update_port_data(self, index, name, param, cost):
        if 0 <= index < len(self.outputs):
            self.outputs[index]["name"] = name
            self.outputs[index]["param"] = param
            self.outputs[index]["cost"] = cost
            self.data_changed.emit()
    
    def set_next_mode(self, mode):
        if self.next_mode != mode:
            self.next_mode = mode
            self.data_changed.emit()

    def copy(self, new_id):
        new_model = NodeModel(new_id, self.type, self.title, self.x + 40, self.y + 40)
        new_model.text_content = self.text_content
        new_model.drop_behavior = self.drop_behavior
        new_model.drop_id = self.drop_id
        new_model.drop_type_client = self.drop_type_client 
        new_model.next_mode = self.next_mode
        new_model.outputs = [dict(p) for p in self.outputs]
        return new_model

    def to_dict(self, connected_next_ids=None):
        formatted_next = []
        target_node_type_for_calc = None 
        
        if connected_next_ids:
            target_node_type_for_calc = connected_next_ids[0].get("target_type")
            
            for item in connected_next_ids:
                target_id = item["id"]
                param = item["param"] 
                
                if self.next_mode == NEXT_MODE_PROB: 
                    formatted_next.append(f"{{{target_id},{param}}}")
                else:
                    formatted_next.append(target_id)
        
        final_next_type = get_next_type_value(self.next_mode, target_node_type_for_calc)

        props = {
            "text_content": self.text_content,
            "next_mode": self.next_mode, 
            "next_type": final_next_type 
        }
        
        if self.type == 102:
            props["drop_behavior"] = self.drop_behavior
            props["drop_id"] = self.drop_id
            props["drop_type_client"] = self.drop_type_client

        return {
            "id": self.id,
            "type": self.type,
            "type_name": NODE_TYPES.get(self.type, {}).get("name", "Unknown"),
            "title": self.title,
            "pos": {"x": self.x, "y": self.y},
            "properties": props,
            "output_config": self.outputs, 
            "next_nodes": formatted_next
        }

# ==========================================
# PART 2: 图形视图 (The View)
# ==========================================

class GraphView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._is_panning = False
        self._last_pan_point = QPointF()

    def wheelEvent(self, event):
        zoom = 1.1 if event.angleDelta().y() > 0 else 0.9
        self.scale(zoom, zoom)

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._is_panning = True
            self._last_pan_point = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_panning:
            delta = event.position() - self._last_pan_point
            self._last_pan_point = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self._is_panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

class SocketItem(QGraphicsItem):
    def __init__(self, parent, index, is_output=True, label="", param="", cost=""):
        super().__init__(parent)
        self.parent_node = parent
        self.index = index
        self.is_output = is_output
        self.label = label
        self.param = param 
        self.cost = cost
        self.radius = 6.0
        self.color = QColor("#FF9800") if is_output else QColor("#4CAF50")
        self.setAcceptHoverEvents(True)
        self.update_position()

    def update_position(self):
        y_offset = 45 + (self.index * 25)
        self.setPos(180, y_offset) if self.is_output else self.setPos(0, 45)

    def boundingRect(self):
        return QRectF(-self.radius, -self.radius, self.radius*2, self.radius*2)

    def paint(self, painter, option, widget):
        painter.setBrush(QBrush(Qt.white) if self.isUnderMouse() else QBrush(self.color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(-self.radius, -self.radius, self.radius*2, self.radius*2)
        
        if self.is_output:
            painter.setPen(QColor("#EEE"))
            painter.setFont(QFont("Microsoft YaHei", 7))
            
            painter.drawText(QRectF(-140, -10, 130, 20), Qt.AlignRight | Qt.AlignVCenter, self.label)
            
            info_offset = -175
            if self.parent_node.model.next_mode == NEXT_MODE_PROB and self.param:
                painter.setPen(QColor("#FFCC00"))
                painter.drawText(QRectF(info_offset, -10, 30, 20), Qt.AlignRight | Qt.AlignVCenter, f"{self.param}%")
                info_offset -= 35
            
            if self.parent_node.model.next_mode == NEXT_MODE_OPTION and self.cost:
                painter.setPen(QColor("#FF5252"))
                painter.drawText(QRectF(info_offset - 50, -10, 50, 20), Qt.AlignRight | Qt.AlignVCenter, f"${self.cost}")

class NodeItem(QGraphicsItem):
    def __init__(self, model: NodeModel):
        super().__init__()
        self.model = model
        self.model.data_changed.connect(self.refresh_structure)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(model.x, model.y)
        self.width = 180 
        self.header_height = 30
        self.inputs = []
        self.outputs = []
        self._rebuild_sockets()
        self._update_height()

    def refresh_structure(self):
        if not self.scene():
            self._rebuild_sockets()
            self._update_height()
            self.update()
            return

        connections_to_restore = [] 
        scene_connections = [item for item in self.scene().items() if isinstance(item, ConnectionItem)]
        
        for conn in scene_connections:
            if conn.start_socket.parent_node == self:
                target_socket = conn.end_socket
                my_socket_index = conn.start_socket.index
                connections_to_restore.append({
                    "type": "output",
                    "index": my_socket_index,
                    "other_socket": target_socket,
                    "conn_item": conn
                })
            elif conn.end_socket.parent_node == self:
                target_socket = conn.start_socket
                my_socket_index = conn.end_socket.index
                connections_to_restore.append({
                    "type": "input",
                    "index": my_socket_index,
                    "other_socket": target_socket,
                    "conn_item": conn
                })

        for info in connections_to_restore:
            self.scene().removeItem(info["conn_item"])
            if hasattr(self.scene(), "connections") and info["conn_item"] in self.scene().connections:
                self.scene().connections.remove(info["conn_item"])

        self._rebuild_sockets()
        self._update_height()
        self.update()

        for info in connections_to_restore:
            other_socket = info["other_socket"]
            if not other_socket.scene():
                continue
                
            my_new_socket = None
            if info["type"] == "output":
                if info["index"] < len(self.outputs):
                    my_new_socket = self.outputs[info["index"]]
                    if my_new_socket:
                        self.scene().add_connection(my_new_socket, other_socket)
            elif info["type"] == "input":
                if info["index"] < len(self.inputs):
                    my_new_socket = self.inputs[info["index"]]
                    if my_new_socket:
                        self.scene().add_connection(other_socket, my_new_socket)

    def _rebuild_sockets(self):
        for s in self.inputs + self.outputs:
            if s.scene(): s.scene().removeItem(s)
        self.inputs.clear()
        self.outputs.clear()

        for i, data in enumerate(self.model.inputs):
            s = SocketItem(self, i, False, data.get("name", ""))
            s.setParentItem(self) 
            self.inputs.append(s)
            
        for i, data in enumerate(self.model.outputs):
            s = SocketItem(self, i, True, data.get("name", ""), data.get("param", ""), data.get("cost", ""))
            s.setParentItem(self)
            self.outputs.append(s)

    def _update_height(self):
        ports_count = max(len(self.model.inputs), len(self.model.outputs))
        self.height = max(80, 45 + ports_count * 25 + 10)
        self.prepareGeometryChange()

    def contextMenuEvent(self, event):
        menu = QMenu()
        add_action = menu.addAction("➕ 添加输出端口")
        action = menu.exec(event.screenPos())
        if action == add_action:
            self.model.add_output_port()

    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)

    def paint(self, painter, option, widget):
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width, self.height, 10, 10)
        
        border_color = Qt.black
        if self.model.next_mode == NEXT_MODE_OPTION:
            border_color = QColor("#8E24AA") 
        elif self.model.next_mode == NEXT_MODE_PROB:
            border_color = QColor("#FB8C00") 
            
        if self.isSelected():
            painter.setPen(QPen(QColor("#FFD700"), 3))
        else:
            painter.setPen(QPen(border_color, 2))
            
        painter.setBrush(QBrush(QColor(self.model.color_body)))
        painter.drawPath(path)
        
        header_path = QPainterPath()
        header_path.setFillRule(Qt.WindingFill)
        header_path.addRoundedRect(0, 0, self.width, self.header_height, 10, 10)
        header_path.addRect(0, self.header_height-10, self.width, 10) 
        painter.setBrush(QBrush(QColor(self.model.color_header)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(header_path.simplified())
        
        painter.setPen(Qt.white)
        painter.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        painter.drawText(QRectF(10, 0, self.width-20, self.header_height), Qt.AlignVCenter, self.model.title)
        
        painter.setPen(QColor("#AAA"))
        painter.setFont(QFont("Microsoft YaHei", 8))
        
        info_txt = ""
        if self.model.type == 102:
            info_txt = f"Drop: {self.model.drop_id}"
        else:
            info_txt = self.model.text_content[:10] + "..."
            
        mode_txt = ""
        if self.model.next_mode == NEXT_MODE_OPTION: mode_txt = "[选项]"
        elif self.model.next_mode == NEXT_MODE_PROB: mode_txt = "[概率]"
        
        painter.drawText(QRectF(10, self.header_height+5, self.width-20, 40), Qt.AlignTop, f"ID: {self.model.id} {mode_txt}\n{info_txt}")

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange:
            if self.scene():
                self.scene().update_connected_edges(self)
            self.model.x = value.x()
            self.model.y = value.y()
        return super().itemChange(change, value)

class ConnectionItem(QGraphicsPathItem):
    def __init__(self, start_socket, end_socket):
        super().__init__()
        self.start_socket = start_socket
        self.end_socket = end_socket
        self.setZValue(-1)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.default_pen = QPen(QColor("#AAAAAA"), 2)
        self.selected_pen = QPen(QColor("#FFD700"), 3)
        self.setPen(self.default_pen)
        self.update_path()

    def update_path(self):
        # 确保socket还在场景中或者是Item的子项
        if not self.start_socket or not self.end_socket:
            return
        # 使用 scenePos 获取绝对坐标
        try:
            start_pos = self.start_socket.scenePos()
            end_pos = self.end_socket.scenePos()
        except RuntimeError:
            # C++ 对象已被删除
            return

        path = QPainterPath()
        path.moveTo(start_pos)
        dx = end_pos.x() - start_pos.x()
        dy = end_pos.y() - start_pos.y()
        ctrl1 = QPointF(start_pos.x() + abs(dx) * 0.5, start_pos.y())
        ctrl2 = QPointF(end_pos.x() - abs(dx) * 0.5, end_pos.y())
        path.cubicTo(ctrl1, ctrl2, end_pos)
        self.setPath(path)

    def paint(self, painter, option, widget):
        painter.setPen(self.selected_pen if self.isSelected() else self.default_pen)
        painter.drawPath(self.path())

# ==========================================
# PART 3: 场景逻辑 (ID管理 & 排序保存)
# ==========================================

class IDManager:
    def __init__(self, files_cache):
        self.files_cache = files_cache
        
    def get_new_id(self, current_scene_nodes):
        max_id = 4100000 
        for path, data in self.files_cache.items():
            if not data: continue
            for node in data.get("nodes", []):
                max_id = max(max_id, node.get("id", 0))
        for node_item in current_scene_nodes:
            max_id = max(max_id, node_item.model.id)
        return max_id + 1

class GraphScene(QGraphicsScene):
    node_selected = Signal(object)
    
    def __init__(self, id_manager):
        super().__init__()
        self.id_manager = id_manager
        self.setBackgroundBrush(QBrush(QColor("#202020")))
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.grid_size = 20
        self.nodes = []
        self.connections = []
        self.drag_socket = None
        self.drag_line = None
        self.copied_node_data = None 

    def clear_scene(self):
        self.nodes.clear()
        self.connections.clear()
        self.clear() 

    def serialize_to_data(self):
        data = {"meta": {"version": "1.3"}, "nodes": [], "edges": []}
        
        conn_map = {} 
        for conn in self.connections:
            s_node = conn.start_socket.parent_node.model
            s_idx = conn.start_socket.index
            e_node = conn.end_socket.parent_node.model
            conn_map[(s_node.id, s_idx)] = e_node
            
            data["edges"].append({
                "from": s_node.id, "port": s_idx, "to": e_node.id
            })

        sorted_nodes = self._sort_nodes_logically()
        
        for item in sorted_nodes:
            model = item.model
            next_info_list = []
            for i, port_config in enumerate(model.outputs):
                target_node = conn_map.get((model.id, i))
                if target_node:
                    param = port_config.get("param", "")
                    next_info_list.append({
                        "id": target_node.id, 
                        "param": param,
                        "target_type": target_node.type
                    })
            
            data["nodes"].append(model.to_dict(next_info_list))
            
        return data

    def _sort_nodes_logically(self):
        if not self.nodes: return []
        
        in_degree = {node: 0 for node in self.nodes}
        adj = {node: [] for node in self.nodes}
        
        for conn in self.connections:
            s = conn.start_socket.parent_node
            e = conn.end_socket.parent_node
            if s in adj and e in in_degree:
                adj[s].append(e)
                in_degree[e] += 1
        
        queue = deque([n for n in self.nodes if in_degree[n] == 0])
        sorted_list = []
        visited = set()
        
        if not queue and self.nodes:
            min_node = min(self.nodes, key=lambda n: n.model.id)
            queue.append(min_node)

        while queue:
            curr = queue.popleft()
            if curr in visited: continue
            visited.add(curr)
            sorted_list.append(curr)
            
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] <= 0:
                    queue.append(neighbor)
        
        remaining = [n for n in self.nodes if n not in visited]
        remaining.sort(key=lambda x: x.model.id)
        sorted_list.extend(remaining)
        
        return sorted_list

    def deserialize_from_data(self, data):
        self.clear_scene()
        if not data: return
        
        node_map = {} 
        
        for node_data in data.get("nodes", []):
            nid = node_data["id"]
            ntype = node_data["type"]
            pos = node_data.get("pos", {"x": 0, "y": 0})
            
            config = NODE_TYPES.get(ntype, {})
            title = config.get("name", "Unknown")
            
            model = NodeModel(nid, ntype, title, pos["x"], pos["y"])
            
            props = node_data.get("properties", {})
            model.text_content = props.get("text_content", "")
            model.next_mode = props.get("next_mode", NEXT_MODE_DEFAULT) 
            
            if ntype == 102:
                model.drop_behavior = props.get("drop_behavior", 1) # 恢复默认值1
                model.drop_id = props.get("drop_id", "")
                model.drop_type_client = props.get("drop_type_client", 3) 
            
            output_config = node_data.get("output_config", [])
            if output_config:
                model.restore_ports(output_config)
            
            item = NodeItem(model)
            self.addItem(item)
            self.nodes.append(item)
            node_map[nid] = item
            
        for edge in data.get("edges", []):
            from_id = edge["from"]
            to_id = edge["to"]
            port_idx = edge["port"]
            
            start_node_item = node_map.get(from_id)
            end_node_item = node_map.get(to_id)
            
            if start_node_item and end_node_item:
                if port_idx < len(start_node_item.outputs) and len(end_node_item.inputs) > 0:
                    s_socket = start_node_item.outputs[port_idx]
                    e_socket = end_node_item.inputs[0]
                    self.add_connection(s_socket, e_socket)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        left = int(rect.left()) - (int(rect.left()) % self.grid_size)
        top = int(rect.top()) - (int(rect.top()) % self.grid_size)
        lines = []
        for x in range(left, int(rect.right()), self.grid_size):
            lines.append(QLineF(x, rect.top(), x, rect.bottom()))
        for y in range(top, int(rect.bottom()), self.grid_size):
            lines.append(QLineF(rect.left(), y, rect.right(), y))
        painter.setPen(QPen(QColor("#303030"), 1))
        painter.drawLines(lines)
        painter.setPen(QPen(QColor("#505050"), 2))
        painter.drawLine(0, -50, 0, 50)
        painter.drawLine(-50, 0, 50, 0)

    def add_node(self, node_type, pos):
        new_id = self.id_manager.get_new_id(self.nodes)
        config = NODE_TYPES.get(node_type)
        model = NodeModel(new_id, node_type, config["name"], pos.x(), pos.y())
        item = NodeItem(model)
        self.addItem(item)
        self.nodes.append(item)
        return item

    def paste_node(self, pos):
        if not self.copied_node_data: return
        
        new_id = self.id_manager.get_new_id(self.nodes)
        orig_model = self.copied_node_data
        
        new_model = orig_model.copy(new_id)
        new_model.x = pos.x()
        new_model.y = pos.y()
        
        item = NodeItem(new_model)
        self.addItem(item)
        self.nodes.append(item)
        
        self.clearSelection()
        item.setSelected(True)

    def add_connection(self, start_socket, end_socket):
        for conn in self.connections:
            if conn.start_socket == start_socket and conn.end_socket == end_socket:
                return
        conn = ConnectionItem(start_socket, end_socket)
        self.addItem(conn)
        self.connections.append(conn)

    def remove_node(self, node_item):
        to_remove = [c for c in self.connections if c.start_socket.parent_node == node_item or c.end_socket.parent_node == node_item]
        for c in to_remove:
            self.removeItem(c)
            self.connections.remove(c)
        self.removeItem(node_item)
        self.nodes.remove(node_item)

    def remove_connection(self, conn):
        if conn in self.connections:
            self.removeItem(conn)
            self.connections.remove(conn)

    def update_connected_edges(self, node):
        for conn in self.connections:
            if conn.start_socket.parent_node == node or conn.end_socket.parent_node == node:
                conn.update_path()

    def mousePressEvent(self, event):
        items = self.items(event.scenePos())
        clicked_socket = None
        for item in items:
            if isinstance(item, SocketItem):
                clicked_socket = item
                break
        
        if clicked_socket:
            self.drag_socket = clicked_socket
            self.drag_line = QGraphicsPathItem()
            self.drag_line.setPen(QPen(Qt.white, 2, Qt.DashLine))
            self.drag_line.setZValue(100)
            self.addItem(self.drag_line)
            event.accept()
            return

        super().mousePressEvent(event)
        if not self.selectedItems():
            self.node_selected.emit(None)
        elif isinstance(self.selectedItems()[0], NodeItem):
            self.node_selected.emit(self.selectedItems()[0])

    def mouseMoveEvent(self, event):
        if self.drag_socket and self.drag_line:
            path = QPainterPath()
            path.moveTo(self.drag_socket.scenePos())
            path.lineTo(event.scenePos())
            self.drag_line.setPath(path)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_socket:
            items = self.items(event.scenePos())
            target_socket = None
            for item in items:
                if isinstance(item, SocketItem) and item != self.drag_socket:
                    target_socket = item
                    break
            
            if target_socket:
                if (self.drag_socket.is_output != target_socket.is_output) and \
                   (self.drag_socket.parent_node != target_socket.parent_node):
                    start = self.drag_socket if self.drag_socket.is_output else target_socket
                    end = target_socket if target_socket.is_output == False else self.drag_socket
                    self.add_connection(start, end)
            else:
                if self.drag_socket.is_output:
                    self.handle_quick_create(event.screenPos(), event.scenePos())
            
            self.removeItem(self.drag_line)
            self.drag_socket = None
            self.drag_line = None
        super().mouseReleaseEvent(event)

    def handle_quick_create(self, screen_pos, scene_pos):
        menu = QMenu()
        menu.setTitle("快速创建并连接")
        for type_id, config in NODE_TYPES.items():
            action = menu.addAction(f"创建并连接: {config['name']}")
            action.setData(type_id)
        
        sel_action = menu.exec(screen_pos)
        
        if sel_action:
            type_id = sel_action.data()
            new_item = self.add_node(type_id, scene_pos)
            if len(new_item.inputs) > 0:
                self.add_connection(self.drag_socket, new_item.inputs[0])

    def contextMenuEvent(self, event):
        if self.itemAt(event.scenePos(), QTransform()):
            super().contextMenuEvent(event)
            return
        menu = QMenu()
        for type_id, config in NODE_TYPES.items():
            action = menu.addAction(f"添加: {config['name']}")
            action.setData(type_id)
        sel_action = menu.exec(event.screenPos())
        if sel_action:
            type_id = sel_action.data()
            self.add_node(type_id, event.scenePos())

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            items = self.selectedItems()
            if items and isinstance(items[0], NodeItem):
                self.copied_node_data = items[0].model
                
        elif event.matches(QKeySequence.Paste):
            if self.copied_node_data:
                offset_pos = QPointF(self.copied_node_data.x + 60, self.copied_node_data.y + 60)
                self.paste_node(offset_pos)

        elif event.key() == Qt.Key_Delete:
            for item in self.selectedItems():
                if isinstance(item, NodeItem):
                    self.remove_node(item)
                elif isinstance(item, ConnectionItem):
                    self.remove_connection(item)
        super().keyPressEvent(event)

# ==========================================
# PART 4: 属性面板与文件管理
# ==========================================

class PortManager(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["端口名称", "参数 (概率)", "消耗 ({{2,1,10}})"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self.on_item_changed)
        self.layout.addWidget(self.table)
        
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("➕")
        self.btn_del = QPushButton("➖")
        self.btn_add.clicked.connect(self.add_port)
        self.btn_del.clicked.connect(self.del_port)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_del)
        self.layout.addLayout(btn_layout)
        self.current_node_model = None

    def set_model(self, model):
        self.current_node_model = model
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if not model:
            self.table.blockSignals(False)
            return
        
        mode = model.next_mode
        is_option = (mode == NEXT_MODE_OPTION)
        is_prob = (mode == NEXT_MODE_PROB)
        
        allow_custom = (is_option or is_prob)
        self.btn_add.setEnabled(allow_custom)
        self.btn_del.setEnabled(allow_custom)
        
        self.table.setColumnHidden(1, not is_prob) 
        self.table.setColumnHidden(2, not is_option)
        
        for i, port in enumerate(model.outputs):
            self.table.insertRow(i)
            name_item = QTableWidgetItem(port["name"])
            param_item = QTableWidgetItem(port.get("param", ""))
            cost_item = QTableWidgetItem(port.get("cost", ""))
            
            self.table.setItem(i, 0, name_item)
            self.table.setItem(i, 1, param_item)
            self.table.setItem(i, 2, cost_item)
            
        self.table.blockSignals(False)

    def on_item_changed(self, item):
        if not self.current_node_model: return
        row = item.row()
        name = self.table.item(row, 0).text()
        param = self.table.item(row, 1).text()
        cost = self.table.item(row, 2).text()
        self.current_node_model.update_port_data(row, name, param, cost)

    def add_port(self):
        if self.current_node_model:
            self.current_node_model.add_output_port()
            self.set_model(self.current_node_model) 

    def del_port(self):
        if self.current_node_model:
            row = self.table.currentRow()
            if row >= 0:
                self.current_node_model.remove_output_port(row)
                self.set_model(self.current_node_model)

class InspectorPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.current_node = None
        self.layout = QVBoxLayout(self)
        title = QLabel("🛠 属性检查器")
        title.setStyleSheet("font-weight:bold; color:#DDD; font-size:14px;")
        self.layout.addWidget(title)
        
        form = QWidget()
        self.form_layout = QFormLayout(form)
        self.form_layout.setLabelAlignment(Qt.AlignRight)
        
        self.id_edit = QLineEdit()
        self.id_edit.setReadOnly(True)
        self.id_edit.setStyleSheet("color: #888; background: #333;")
        self.remark_edit = QLineEdit()
        
        self.next_mode_combo = QComboBox()
        self.next_mode_combo.addItems([
            NEXT_MODE_NAMES[NEXT_MODE_DEFAULT],
            NEXT_MODE_NAMES[NEXT_MODE_OPTION],
            NEXT_MODE_NAMES[NEXT_MODE_PROB]
        ])
        
        self.text_widget = QWidget()
        self.text_layout = QVBoxLayout(self.text_widget)
        self.text_layout.setContentsMargins(0,0,0,0)
        self.text_edit = QTextEdit()
        self.text_edit.setMaximumHeight(60)
        self.text_layout.addWidget(self.text_edit)
        
        self.drop_widget = QWidget()
        self.drop_layout = QFormLayout(self.drop_widget)
        self.drop_layout.setContentsMargins(0,0,0,0)
        
        self.drop_behavior_combo = QComboBox()
        self.drop_behavior_combo.addItems(["1-正常掉落", "2-属性掉落", "3-连续概率掉落", "4-根据性格掉落"])
        
        # 新增: 掉落表现类型下拉框 (drop_type_client)
        self.drop_client_type_combo = QComboBox()
        self.drop_client_type_combo.addItems(DROP_CLIENT_TYPES)
        
        self.drop_id_edit = QLineEdit()
        self.drop_id_edit.setPlaceholderText("输入 Drop ID")
        
        self.drop_layout.addRow("掉落 ID:", self.drop_id_edit)
        self.drop_layout.addRow("掉落类型:", self.drop_behavior_combo)
        self.drop_layout.addRow("表现类型:", self.drop_client_type_combo)
        
        self.form_layout.addRow("ID:", self.id_edit)
        self.form_layout.addRow("标题:", self.remark_edit)
        self.form_layout.addRow("后续逻辑:", self.next_mode_combo)
        
        self.layout.addWidget(form)
        self.layout.addWidget(QLabel("内容配置:"))
        self.layout.addWidget(self.text_widget)
        self.layout.addWidget(self.drop_widget)
        
        self.layout.addWidget(QLabel("🔌 输出端口配置:"))
        self.port_manager = PortManager()
        self.layout.addWidget(self.port_manager)
        
        self.remark_edit.textChanged.connect(self.sync_data)
        self.text_edit.textChanged.connect(self.sync_data)
        self.drop_behavior_combo.currentIndexChanged.connect(self.sync_drop_data)
        self.drop_client_type_combo.currentIndexChanged.connect(self.sync_drop_data)
        self.drop_id_edit.textChanged.connect(self.sync_drop_data)
        self.next_mode_combo.currentIndexChanged.connect(self.sync_mode)
        
    def set_node(self, node_item):
        self.current_node = node_item
        if not node_item:
            self.id_edit.setText("-")
            self.remark_edit.setText("")
            self.text_edit.setText("")
            self.port_manager.set_model(None)
            self.drop_group_visible(False)
            self.setEnabled(False)
            return
        
        self.setEnabled(True)
        model = node_item.model
        
        self.remark_edit.blockSignals(True)
        self.text_edit.blockSignals(True)
        self.drop_behavior_combo.blockSignals(True)
        self.drop_client_type_combo.blockSignals(True)
        self.drop_id_edit.blockSignals(True)
        self.next_mode_combo.blockSignals(True)
        
        self.id_edit.setText(str(model.id))
        self.remark_edit.setText(model.title)
        self.text_edit.setText(model.text_content)
        self.next_mode_combo.setCurrentIndex(model.next_mode)
        
        if model.type == 102:
            self.drop_group_visible(True)
            # drop_behavior
            idx_beh = max(0, model.drop_behavior - 1)
            if idx_beh < self.drop_behavior_combo.count():
                self.drop_behavior_combo.setCurrentIndex(idx_beh)
                
            # drop_type_client
            idx_client = max(0, model.drop_type_client - 1)
            if idx_client < self.drop_client_type_combo.count():
                self.drop_client_type_combo.setCurrentIndex(idx_client)
                
            self.drop_id_edit.setText(str(model.drop_id))
        else:
            self.drop_group_visible(False)
            
        self.port_manager.set_model(model)
        
        self.remark_edit.blockSignals(False)
        self.text_edit.blockSignals(False)
        self.drop_behavior_combo.blockSignals(False)
        self.drop_client_type_combo.blockSignals(False)
        self.drop_id_edit.blockSignals(False)
        self.next_mode_combo.blockSignals(False)

    def drop_group_visible(self, visible):
        self.drop_widget.setVisible(visible)
        self.text_widget.setVisible(not visible)

    def sync_data(self):
        if self.current_node:
            model = self.current_node.model
            model.title = self.remark_edit.text()
            if model.type != 102:
                model.text_content = self.text_edit.toPlainText()
            self.current_node.refresh_structure()

    def sync_drop_data(self):
        if self.current_node and self.current_node.model.type == 102:
            self.current_node.model.drop_behavior = self.drop_behavior_combo.currentIndex() + 1
            self.current_node.model.drop_type_client = self.drop_client_type_combo.currentIndex() + 1
            self.current_node.model.drop_id = self.drop_id_edit.text()

    def sync_mode(self):
        if self.current_node:
            new_mode = self.next_mode_combo.currentIndex()
            self.current_node.model.set_next_mode(new_mode)
            self.port_manager.set_model(self.current_node.model)
            self.current_node.refresh_structure()

# ==========================================
# PART 5: 主窗口与多文件管理
# ==========================================

class BlueprintEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("蓝图编辑器 v3.3 - 修复连线Bug & 掉落增强")
        self.resize(1400, 900)
        
        self.settings = QSettings("MyCompany", "BlueprintEditor")
        self.current_project_folder = self.settings.value("last_project_folder", "")
        self.files_cache = {}
        self.current_file_path = None
        
        main = QWidget()
        self.setCentralWidget(main)
        main_layout = QVBoxLayout(main)
        
        toolbar = QHBoxLayout()
        self.btn_open_folder = QPushButton("📂 打开项目文件夹")
        self.btn_open_folder.clicked.connect(self.open_project_folder)
        self.btn_open_folder.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        
        self.btn_new_file = QPushButton("➕ 新建节点组")
        self.btn_new_file.clicked.connect(self.create_new_file)
        self.btn_new_file.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        
        self.lbl_status = QLabel("未加载项目")
        self.lbl_status.setStyleSheet("color: #AAA; margin-left: 15px;")
        
        toolbar.addWidget(self.btn_open_folder)
        toolbar.addWidget(self.btn_new_file)
        toolbar.addWidget(self.lbl_status)
        toolbar.addStretch()
        main_layout.addLayout(toolbar)
        
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        left_panel = QWidget()
        lp_layout = QVBoxLayout(left_panel)
        lp_layout.setContentsMargins(0,0,0,0)
        lp_layout.addWidget(QLabel("📑 节点组列表"))
        
        self.file_list = QListWidget()
        self.file_list.itemClicked.connect(self.on_file_selected)
        lp_layout.addWidget(self.file_list)
        splitter.addWidget(left_panel)
        
        self.id_manager = IDManager(self.files_cache)
        self.scene = GraphScene(self.id_manager)
        self.scene.node_selected.connect(self.on_node_selected) 
        self.view = GraphView(self.scene)
        splitter.addWidget(self.view)
        
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0,0,0,0)
        self.inspector = InspectorPanel()
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background:#111; color:#4CAF50; font-family:Consolas;")
        pl.addWidget(self.inspector)
        pl.addWidget(self.log)
        splitter.addWidget(panel)
        
        splitter.setSizes([200, 900, 300])
        
        self.shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_save.activated.connect(self.save_current_file)
        
        if self.current_project_folder and os.path.isdir(self.current_project_folder):
            self.load_folder(self.current_project_folder)

    def on_node_selected(self, node):
        self.inspector.set_node(node)

    def open_project_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择项目文件夹", self.current_project_folder)
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder_path):
        self.current_project_folder = folder_path
        self.settings.setValue("last_project_folder", folder_path)
        self.file_list.clear()
        self.files_cache.clear()
        self.scene.clear_scene()
        self.current_file_path = None
        self.lbl_status.setText(f"项目: {os.path.basename(folder_path)}")
        
        try:
            files = [f for f in os.listdir(folder_path) if f.endswith('.json')]
            valid_count = 0
            for f_name in files:
                full_path = os.path.join(folder_path, f_name)
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if "nodes" in data and "edges" in data:
                            self.files_cache[full_path] = data
                            item = QListWidgetItem(f_name)
                            item.setData(Qt.UserRole, full_path)
                            self.file_list.addItem(item)
                            valid_count += 1
                        else:
                            print(f"Skip invalid JSON: {f_name}")
                except Exception as e:
                    print(f"Error loading {f_name}: {e}")
            self.log.setText(f"加载完成: {valid_count} 个有效文件")
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"读取文件夹失败:\n{e}")

    def create_new_file(self):
        if not self.current_project_folder:
            QMessageBox.warning(self, "提示", "请先打开一个项目文件夹")
            return
        name, ok = QInputDialog.getText(self, "新建节点组", "请输入文件名 (无需后缀):")
        if ok and name:
            filename = f"{name}.json"
            full_path = os.path.join(self.current_project_folder, filename)
            if full_path in self.files_cache or os.path.exists(full_path):
                QMessageBox.warning(self, "错误", "文件已存在")
                return
            empty_data = {"meta": {"version": "1.3"}, "nodes": [], "edges": []}
            self.files_cache[full_path] = empty_data
            item = QListWidgetItem(filename)
            item.setData(Qt.UserRole, full_path)
            self.file_list.addItem(item)
            self.file_list.setCurrentItem(item)
            self.on_file_selected(item)
            self.log.setText(f"新建文件: {filename}\n(请按 Ctrl+S 保存到磁盘)")

    def on_file_selected(self, item):
        new_path = item.data(Qt.UserRole)
        if new_path == self.current_file_path: return
        
        if self.current_file_path:
            self.files_cache[self.current_file_path] = self.scene.serialize_to_data()
            
        self.current_file_path = new_path
        data = self.files_cache.get(new_path)
        self.scene.deserialize_from_data(data)
        self.lbl_status.setText(f"正在编辑: {os.path.basename(new_path)}")

    def save_current_file(self):
        if not self.current_file_path: return
        current_data = self.scene.serialize_to_data()
        self.files_cache[self.current_file_path] = current_data 
        try:
            with open(self.current_file_path, 'w', encoding='utf-8') as f:
                json.dump(current_data, f, indent=4, ensure_ascii=False)
            self.log.setText(f"✅ 已保存: {os.path.basename(self.current_file_path)}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(palette)
    win = BlueprintEditor()
    win.show()
    sys.exit(app.exec())
