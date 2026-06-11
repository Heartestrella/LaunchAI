"""
node_graph.py
~~~~~~~~~~~~~
节点图数据模型：管理节点实例、连线、执行顺序解析和控制台输出。
与 UI 完全解耦，UI 通过调用这里的方法修改图状态。
"""

from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Any
from node.node_registry import REGISTRY, NodeDef, PortDef


# ══════════════════════════════════════════════════════════════════════
#  数据结构
# ══════════════════════════════════════════════════════════════════════

@dataclass
class NodeInstance:
    """图中一个具体的节点实例。"""
    iid:     str                        # 实例唯一 ID
    def_id:  str                        # 对应 NodeDef.id
    x:       float = 100.0
    y:       float = 100.0
    params:  dict[str, Any] = field(default_factory=dict)

    @property
    def definition(self) -> NodeDef | None:
        return REGISTRY.get(self.def_id)

    @property
    def title(self) -> str:
        d = self.definition
        return d.title if d else self.def_id


@dataclass
class Connection:
    """一条连线：从 src_iid 的 src_port 输出端口连到 dst_iid 的 dst_port 输入端口。"""
    cid:      str
    src_iid:  str   # 源节点实例 ID
    src_port: str   # 源输出端口 name
    dst_iid:  str   # 目标节点实例 ID
    dst_port: str   # 目标输入端口 name


# ══════════════════════════════════════════════════════════════════════
#  图管理器
# ══════════════════════════════════════════════════════════════════════

class NodeGraph:
    def __init__(self):
        self.nodes:       dict[str, NodeInstance] = {}
        self.connections: dict[str, Connection] = {}

    # ── 节点 CRUD ─────────────────────────────────────────────────────

    def add_node(self, def_id: str, x: float = 100, y: float = 100) -> NodeInstance:
        nd = REGISTRY.get(def_id)
        if nd is None:
            raise ValueError(f"未知节点类型: {def_id}")
        inst = NodeInstance(
            iid=str(uuid.uuid4())[:8],
            def_id=def_id,
            x=x, y=y,
            params=dict(nd.params),   # 复制默认参数
        )
        self.nodes[inst.iid] = inst
        return inst

    def remove_node(self, iid: str):
        self.nodes.pop(iid, None)
        # 级联删除关联连线
        to_del = [c for c in self.connections.values()
                  if c.src_iid == iid or c.dst_iid == iid]
        for c in to_del:
            self.connections.pop(c.cid, None)

    def move_node(self, iid: str, x: float, y: float):
        if iid in self.nodes:
            self.nodes[iid].x = x
            self.nodes[iid].y = y

    def set_param(self, iid: str, key: str, value: Any):
        if iid in self.nodes:
            self.nodes[iid].params[key] = value

    # ── 连线 CRUD ─────────────────────────────────────────────────────

    def can_connect(self, src_iid: str, src_port: str,
                    dst_iid: str, dst_port: str) -> tuple[bool, str]:
        """校验连线是否合法，返回 (ok, reason)。"""
        if src_iid == dst_iid:
            return False, "不能自连"

        src_nd = self.nodes.get(src_iid)
        dst_nd = self.nodes.get(dst_iid)
        if not src_nd or not dst_nd:
            return False, "节点不存在"

        src_def = src_nd.definition
        dst_def = dst_nd.definition
        if not src_def or not dst_def:
            return False, "节点定义缺失"

        # 找端口定义
        src_port_def = next(
            (p for p in src_def.outputs if p.name == src_port), None)
        dst_port_def = next(
            (p for p in dst_def.inputs if p.name == dst_port), None)
        if not src_port_def:
            return False, f"源端口 {src_port} 不存在"
        if not dst_port_def:
            return False, f"目标端口 {dst_port} 不存在"

        st = src_port_def.type
        dt = dst_port_def.type

        # 类型兼容性检查（支持 file ↔ 具体类型）
        type_ok = (
            st == dt or                # 完全相同
            st == "any" or dt == "any"  # 任意一方为万能类型
            or (st == "file" and dt in ("audio", "image", "video", "text", "number", "bool"))
            or (dt == "file" and st in ("audio", "image", "video", "text", "number", "bool"))
        )
        if not type_ok:
            return False, f"类型不兼容: {st} → {dt}"

        # 如果目标端口不支持多连接，检查是否已有连线
        if not dst_port_def.multi:
            existing = [c for c in self.connections.values()
                        if c.dst_iid == dst_iid and c.dst_port == dst_port]
            if existing:
                return False, "目标端口已有连线（不支持多输入）"

        # 检查重复连线
        dup = any(c.src_iid == src_iid and c.src_port == src_port
                  and c.dst_iid == dst_iid and c.dst_port == dst_port
                  for c in self.connections.values())
        if dup:
            return False, "连线已存在"

        return True, ""

    def add_connection(self, src_iid: str, src_port: str,
                       dst_iid: str, dst_port: str) -> Connection | None:
        ok, reason = self.can_connect(src_iid, src_port, dst_iid, dst_port)
        if not ok:
            print(f"[连线失败] {reason}")
            return None
        conn = Connection(
            cid=str(uuid.uuid4())[:8],
            src_iid=src_iid, src_port=src_port,
            dst_iid=dst_iid, dst_port=dst_port,
        )
        self.connections[conn.cid] = conn
        return conn

    def remove_connection(self, cid: str):
        self.connections.pop(cid, None)

    # ── 拓扑排序 & 执行计划输出 ───────────────────────────────────────

    def topological_order(self) -> list[str] | None:
        """Kahn 算法拓扑排序，返回节点 iid 列表；有环时返回 None。"""
        in_degree: dict[str, int] = {iid: 0 for iid in self.nodes}
        adj: dict[str, list[str]] = {iid: [] for iid in self.nodes}

        for c in self.connections.values():
            if c.src_iid in adj and c.dst_iid in in_degree:
                if c.dst_iid not in adj[c.src_iid]:
                    adj[c.src_iid].append(c.dst_iid)
                    in_degree[c.dst_iid] += 1

        queue = [iid for iid, d in in_degree.items() if d == 0]
        order: list[str] = []
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(self.nodes):
            return None  # 有环
        return order

    def print_execution_plan(self):
        """将当前图的执行计划输出到控制台。"""
        print("\n" + "═" * 60)
        print("  节点图执行计划")
        print("═" * 60)

        if not self.nodes:
            print("  （图为空）")
            print("═" * 60)
            return

        order = self.topological_order()
        if order is None:
            print("  ⚠ 检测到循环依赖，无法生成执行计划！")
            print("═" * 60)
            return

        # 构建连线快查表：dst_iid+dst_port → (src_iid, src_port)
        dst_map: dict[tuple, tuple] = {}
        for c in self.connections.values():
            dst_map[(c.dst_iid, c.dst_port)] = (c.src_iid, c.src_port)

        # 构建 src_iid+src_port → [dst]
        src_map: dict[tuple, list[tuple]] = {}
        for c in self.connections.values():
            key = (c.src_iid, c.src_port)
            src_map.setdefault(key, []).append((c.dst_iid, c.dst_port))

        print(f"  共 {len(order)} 个节点，{len(self.connections)} 条连线")
        print()

        for step, iid in enumerate(order, 1):
            node = self.nodes[iid]
            nd = node.definition
            print(f"  [{step:02d}] {node.title}  (id={iid})")

            if nd:
                # 输入来源
                for port in nd.inputs:
                    src = dst_map.get((iid, port.name))
                    if src:
                        src_node = self.nodes.get(src[0])
                        src_title = src_node.title if src_node else src[0]
                        print(
                            f"       ← {port.label}  ←  {src_title}.{src[1]}")
                    else:
                        print(f"       ← {port.label}  ←  (未连接)")

                # 输出去向
                for port in nd.outputs:
                    dsts = src_map.get((iid, port.name), [])
                    if dsts:
                        for dst_iid, dst_port in dsts:
                            dst_node = self.nodes.get(dst_iid)
                            dst_title = dst_node.title if dst_node else dst_iid
                            print(
                                f"       → {port.label}  →  {dst_title}.{dst_port}")
                    else:
                        print(f"       → {port.label}  →  (未连接)")

            # 参数
            if node.params:
                param_str = "  |  ".join(
                    f"{k}={v}" for k, v in node.params.items())
                print(f"       ⚙  {param_str}")
            print()

        print("═" * 60 + "\n")
