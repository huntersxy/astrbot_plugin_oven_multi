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
#
# Modified from astrbot_plugin_balance by BUGJI

import aiohttp
import asyncio
import math
import re
import yaml
from typing import Any

from astrbot.api import logger
from ..utils.safe_eval import safe_eval


_BUILTIN_PARSERS = {
    "deepseek": {
        "url": "https://api.deepseek.com/user/balance",
        "headers": {
            "Accept": "application/json",
            "Authorization": "Bearer {api_key}",
        },
        "result_template": "DeepSeek: {{balance_infos.0.total_balance}} 元",
    },
    "siliconflow": {
        "url": "https://api.siliconflow.cn/v1/user/info",
        "headers": {
            "Authorization": "Bearer {api_key}",
            "Content-Type": "application/json",
        },
        "result_template": "硅基流动: {{data.totalBalance}} 元",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/credits",
        "headers": {
            "Authorization": "Bearer {api_key}",
        },
        "result_template": "OpenRouter: ${{data.total_credits}}",
    },
    "oneapi": {
        "url": "{base_url}/api/user/self",
        "headers": {
            "Authorization": "Bearer {api_key}",
        },
        "result_template": "{{data.email}}: {{data.balance}} 元",
    },
    "moonshot": {
        "url": "https://api.moonshot.cn/v1/users/me/balance",
        "headers": {
            "Authorization": "Bearer {api_key}",
        },
        "result_template": "月之暗面: {{data.available_balance}} 元",
    },
    "openai": {
        "url": "https://api.openai.com/v1/dashboard/billing/subscription",
        "headers": {
            "Authorization": "Bearer {api_key}",
        },
        "result_template": "OpenAI: ${{hard_limit_usd}}",
    },
    "onething": {
        "url": "https://api-lab.onethingai.com/api/v1/account/wallet/detail",
        "headers": {
            "Authorization": "Bearer {api_key}",
        },
        "result_template": "OneThing: {{data.availableBalance}} 元",
    },
    "minimax": {
        "url": "https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
        "headers": {
            "Authorization": "Bearer {api_key}",
            "Content-Type": "application/json",
        },
        "result_template": "MiniMax: 剩余 {{round({model_remains.0.current_interval_total_count}-{model_remains.0.current_interval_usage_count})}}/{{model_remains.0.current_interval_total_count}} ({{round(({model_remains.0.current_interval_total_count}-{model_remains.0.current_interval_usage_count})/{model_remains.0.current_interval_total_count}*100, 1)}}%), 本周 {{round({model_remains.0.current_weekly_total_count}-{model_remains.0.current_weekly_usage_count})}}/{{model_remains.0.current_weekly_total_count}}",
    },
}


class BalanceChecker:
    def __init__(self, config: dict):
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    async def terminate(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def query_all(self) -> list[dict]:
        cfg = self.config.get("balance", {})
        config_content = cfg.get("config_content", "") if isinstance(cfg, dict) else ""
        config_mode = cfg.get("config_mode", "yaml") if isinstance(cfg, dict) else "yaml"

        if not config_content.strip():
            return [{"name": "配置缺失", "value": "请在配置中添加 services", "success": False}]

        try:
            if config_mode == "yaml":
                return await self._query_yaml(config_content)
            else:
                return await self._query_simple(config_content)
        except Exception as e:
            logger.error(f"[烤箱-余额查询] 查询失败: {e}")
            return [{"name": "查询异常", "value": str(e), "success": False}]

    async def _query_yaml(self, config_content: str) -> list[dict]:
        try:
            config_data = yaml.safe_load(config_content)
            services = config_data.get("services", {})
        except Exception as e:
            logger.error(f"[烤箱-余额查询] 解析 YAML 失败: {e}")
            return [{"name": "配置错误", "value": "YAML 配置解析失败", "success": False}]

        if not services:
            return [{"name": "配置错误", "value": "未配置任何服务", "success": False}]

        self._ensure_session()

        tasks = []
        for service_name, service_info in services.items():
            tasks.append(self._handle_yaml_service(service_name, service_info))

        results = []
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for r in responses:
            if isinstance(r, dict):
                results.append(r)
            elif isinstance(r, Exception):
                logger.error(f"[烤箱-余额查询] 处理服务异常: {r}")
                results.append({"name": "服务异常", "value": str(r), "success": False})

        return results

    async def _query_simple(self, config_content: str) -> list[dict]:
        self._ensure_session()

        lines = config_content.strip().splitlines()
        tasks = [self._handle_line(line) for line in lines]

        results = []
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        for r in responses:
            if isinstance(r, dict):
                results.append(r)

        return results

    async def _handle_yaml_service(self, name: str, info: dict) -> dict:
        try:
            parser_type = info.get("type", "custom") or "custom"
            display_name = info.get("display_name")

            if parser_type == "custom":
                url = info.get("url")
                method = info.get("method", "GET").upper()
                headers = info.get("headers", {})
                result_template = info.get("result_template", "")

                if not url:
                    label = display_name or name
                    return {"name": label, "value": "缺失 URL", "success": False}
                
                if not result_template:
                    label = display_name or name
                    return {"name": label, "value": "缺失 result_template（需要 {{...}} 格式）", "success": False}

            elif parser_type in _BUILTIN_PARSERS:
                preset = dict(_BUILTIN_PARSERS[parser_type])
                display_name = display_name or preset.get("display_name", name)

                api_key = info.get("api_key", "")
                base_url = info.get("base_url", "")

                if not api_key:
                    return {"name": display_name, "value": "缺少 api_key", "success": False}
                preset_url = preset.get("url", "")
                if "{base_url}" in preset_url and not base_url:
                    return {"name": display_name, "value": "缺少 base_url", "success": False}

                url = info.get("url") or preset_url
                url = url.replace("{base_url}", base_url)

                headers = {}
                for k, v in preset.get("headers", {}).items():
                    headers[k] = v.replace("{api_key}", api_key).replace("{base_url}", base_url)
                for k, v in info.get("headers", {}).items():
                    headers[k] = v.replace("{api_key}", api_key).replace("{base_url}", base_url)

                result_template = info.get("result_template") or preset.get("result_template", "{data}")
                method = info.get("method", preset.get("method", "GET")).upper()

            else:
                label = display_name or name
                return {"name": label, "value": f"未知的内置解析器类型 '{parser_type}'", "success": False}

            async with self.session.request(method, url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    label = display_name or name
                    return {"name": label, "value": f"请求失败 (HTTP {resp.status})", "success": False}

                data = await resp.json()
                result = self._render_template(result_template, data)

                return {"name": display_name or name, "value": result, "success": True}

        except asyncio.TimeoutError:
            label = display_name or name
            return {"name": label, "value": "请求超时", "success": False}
        except Exception as e:
            logger.error(f"[烤箱-余额查询] [{name}] 处理失败: {type(e).__name__}: {e}")
            label = display_name or name
            return {"name": label, "value": "异常", "success": False}

    def _render_template(self, template: str, data: dict) -> str:
        result = template
        pattern = r'\{\{(.*?)\}\}'

        def process_match(match):
            inner_content = match.group(1)

            if re.search(r'\{[^{}]+\}', inner_content):
                inner_pattern = r'\{([^{}]+)\}'

                def replace_path(m):
                    path = m.group(1)
                    value = self._get_by_path(data, path)

                    if value is None:
                        return "N/A"

                    try:
                        if isinstance(value, str):
                            cleaned = re.sub(r'[^\d.-]', '', value)
                            if cleaned:
                                value = float(cleaned) if '.' in cleaned else int(cleaned)
                    except (ValueError, TypeError):
                        pass

                    return str(value)

                expr = re.sub(inner_pattern, replace_path, inner_content)

                try:
                    computed = self._eval_expr(expr)
                    return str(computed)
                except Exception as e:
                    logger.warning(f"[烤箱-余额查询] 表达式计算失败: {expr}, 错误: {e}")
                    return "N/A"
            else:
                value = self._get_by_path(data, inner_content)
                if value is None:
                    return "N/A"

                if isinstance(value, (int, float)):
                    if isinstance(value, float) and not value.is_integer():
                        return f"{value:.2f}"
                return str(value)

        result = re.sub(pattern, process_match, result)
        return result

    def _eval_expr(self, expr: str) -> Any:
        try:
            eval_result = safe_eval(expr)

            if isinstance(eval_result, float):
                if eval_result.is_integer():
                    return int(eval_result)
                return round(eval_result, 2)
            return eval_result
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.warning(f"[烤箱-余额查询] 公式计算失败: {expr}, 错误: {e}")
            return "错误"
        except Exception as e:
            logger.warning(f"[烤箱-余额查询] 公式计算异常: {expr}, 错误: {type(e).__name__}: {e}")
            return "错误"

    async def _handle_line(self, line: str) -> dict:
        try:
            parts = line.split("|")
            if len(parts) != 5:
                return {"name": "配置错误", "value": "格式错误（字段数不正确）", "success": False}

            remark, url, header_str, json_path, unit = parts
            headers = self._parse_headers(header_str)

            async with self.session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return {"name": remark, "value": "请求失败", "success": False}

                data = await resp.json()
                value = self._get_by_path(data, json_path)

                if value is None:
                    return {"name": remark, "value": f"未找到字段 {json_path}", "success": False}

                return {"name": remark, "value": f"{value} {unit}", "success": True}

        except asyncio.TimeoutError:
            return {"name": "超时", "value": "请求超时", "success": False}
        except Exception as e:
            logger.error(f"[烤箱-余额查询] 处理失败: {type(e).__name__}")
            return {"name": "异常", "value": str(e), "success": False}

    def _parse_headers(self, header_str: str) -> dict:
        headers = {}
        for item in header_str.split("&&"):
            if ":" not in item:
                continue
            k, v = item.split(":", 1)
            headers[k.strip()] = v.strip()
        return headers

    def _get_by_path(self, data, path: str):
        current = data
        for part in path.split("."):
            if isinstance(current, list):
                try:
                    current = current[int(part)]
                except Exception:
                    return None
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current
