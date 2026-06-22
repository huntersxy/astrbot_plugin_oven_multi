from typing import Any


class StyleSelector:
    @staticmethod
    def build_style_text(label: str, contents: list[str]) -> str:
        if not contents:
            return ""
        return f"{label}：{'、'.join(contents)}"

    @staticmethod
    def build_contextual_text(contextuals: list[dict[str, Any]]) -> str:
        if not contextuals:
            return ""
        parts = [
            f"{t['scene']}\u2192{t['behavior']}"
            for t in contextuals
            if t.get("scene") and t.get("behavior")
        ]
        if not parts:
            return ""
        return f"情境提示：{'；'.join(parts)}"
