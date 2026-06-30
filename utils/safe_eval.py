# Copyright (C) 2026 汐兮雨
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""安全表达式求值器 — 替代 eval()，防止用户配置中的模板表达式被注入恶意代码。

通过 AST 节点白名单校验，仅允许：数字字面量、算术运算、白名单函数调用、常量引用。
拒绝属性访问、导入、赋值、lambda、推导式等一切危险操作。
"""

import ast
import math
import operator
from typing import Any


# ── 运算符映射 ───────────────────────────────────────────────────

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# ── 白名单函数 ───────────────────────────────────────────────────

_SAFE_FUNCTIONS = {
    'abs': abs,
    'round': round,
    'min': min,
    'max': max,
    'pow': pow,
    'sqrt': math.sqrt,
    'floor': math.floor,
    'ceil': math.ceil,
    'log': math.log,
    'log10': math.log10,
    'exp': math.exp,
    'sin': math.sin,
    'cos': math.cos,
    'tan': math.tan,
}

# ── 白名单常量 ───────────────────────────────────────────────────

_SAFE_CONSTANTS = {
    'pi': math.pi,
    'e': math.e,
}


# ── AST 节点白名单 ───────────────────────────────────────────────

_ALLOWED_NODE_TYPES = (
    ast.Expression,   # 根节点
    ast.Constant,     # 数字字面量（Python 3.8+）
    ast.Num,          # 数字字面量（Python 3.7 兼容，3.14 已废弃）
    ast.BinOp,        # 二元运算
    ast.UnaryOp,      # 一元运算
    ast.Call,         # 函数调用
    ast.Name,         # 变量引用（常量名）
    ast.Load,         # 变量加载上下文
    # 运算符节点（ast.BinOp / ast.UnaryOp 的子节点）
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
)


def safe_eval(expr: str) -> Any:
    """安全表达式求值 — 仅允许算术运算和白名单函数。

    Args:
        expr: 待求值的数学表达式字符串，如 "round((5000-3000)/5000*100, 1)"

    Returns:
        表达式的计算结果（int 或 float）

    Raises:
        ValueError: 表达式包含不允许的语法元素、未知变量、非数字常量等
        SyntaxError: 表达式语法无效

    Examples:
        >>> safe_eval("1 + 2")
        3
        >>> safe_eval("round(3.14, 1)")
        3.1
        >>> safe_eval("sqrt(16)")
        4.0
    """
    tree = ast.parse(expr, mode='eval')

    # 第一遍：AST 白名单校验
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise ValueError(
                f"表达式包含不允许的语法元素: {type(node).__name__}"
            )
        # 只允许数字字面量，拒绝字符串等其他常量
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError(f"表达式中不允许非数字常量: {type(node.value).__name__}")
        # 函数调用只能是简单名称，禁止 a.b() 形式的属性调用
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("只允许直接函数调用，不支持属性/方法调用")
            if node.func.id not in _SAFE_FUNCTIONS:
                raise ValueError(f"不允许的函数: {node.func.id}")

    # 第二遍：递归求值
    def _eval_node(node) -> int | float:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"不支持的常量类型: {type(node.value).__name__}")
        if isinstance(node, ast.Num):  # Python 3.7 兼容（3.14 已废弃）
            return node.n
        if isinstance(node, ast.Name):
            if node.id in _SAFE_CONSTANTS:
                return _SAFE_CONSTANTS[node.id]
            raise ValueError(f"未知的变量名: {node.id}")
        if isinstance(node, ast.UnaryOp):
            op = _SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
            return op(_eval_node(node.operand))
        if isinstance(node, ast.BinOp):
            op = _SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
            return op(_eval_node(node.left), _eval_node(node.right))
        if isinstance(node, ast.Call):
            func = _SAFE_FUNCTIONS[node.func.id]
            args = [_eval_node(arg) for arg in node.args]
            return func(*args)
        raise ValueError(f"不支持的表达式节点: {type(node).__name__}")

    return _eval_node(tree)
