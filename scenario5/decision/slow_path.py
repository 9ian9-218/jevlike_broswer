import re
import time
import httpx
from typing import Optional, Dict, Any, List
from scenario5.config import settings
from scenario5.decision.schemas import ActionChoice, ActionType, Observation


class SlowPathAgent:
    """慢轨按需生成与故障恢复器（Codex / LLM 兼容）"""

    def __init__(self):
        self.api_key = settings.SLOW_PATH_API_KEY
        self.base_url = settings.SLOW_PATH_BASE_URL.rstrip("/")
        self.model = settings.SLOW_PATH_MODEL

    def generate_input_text(self, goal: str, target_item: Dict[str, Any], obs: Observation) -> str:
        """为目标输入控件生成具体文本内容"""
        # 1. 若配置了大模型 API，调用大模型生成
        if self.api_key:
            res = self._call_llm_for_text(goal, target_item, obs)
            if res:
                return res

        # 2. 否则使用智能语义提取器
        return self._extract_text_heuristic(goal, target_item)

    def recover_from_blocked(self, goal: str, obs: Observation, history: List[ActionChoice]) -> ActionChoice:
        """当快轨陷入 BLOCKED 或循环时，由慢轨进行高级推断与恢复"""
        start_time = time.perf_counter()
        
        if self.api_key:
            prompt = f"""
你是一个受限浏览器自动化规划器。当前任务是: "{goal}"。
当前页面: {obs.url} ({obs.title})
前置操作历史: {[h.action.value + (f'->{h.target_id}' if h.target_id is not None else '') for h in history[-3:]]}
页面可见控件列表如下:
{obs.compact_view}

请分析当前卡点并给出下一步动作。回复格式必须为单行 JSON:
{{"action": "CLICK"|"SCROLL"|"DONE", "target_id": 12, "reason": "分析原因"}}
"""
            try:
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"]
                        import json
                        match = re.search(r"\{.*?\}", content, re.DOTALL)
                        if match:
                            data = json.loads(match.group(0))
                            act = data.get("action", "SCROLL").upper()
                            tid = data.get("target_id")
                            return ActionChoice(
                                action=ActionType(act) if act in ActionType.__members__ else ActionType.SCROLL,
                                target_id=tid,
                                reason=f"[SlowPath 自愈] {data.get('reason', '')}",
                                is_fast_path=False,
                                elapsed_ms=(time.perf_counter() - start_time) * 1000
                            )
            except Exception:
                pass

        # 规则兜底自愈：尝试滚动或安全返回 DONE
        return ActionChoice(
            action=ActionType.SCROLL,
            value="down",
            reason="慢轨兜底恢复：尝试滚动页面刷新视口可见元素",
            is_fast_path=False,
            elapsed_ms=(time.perf_counter() - start_time) * 1000
        )

    def _call_llm_for_text(self, goal: str, target: Dict[str, Any], obs: Observation) -> Optional[str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        prompt = f"""
任务目标: {goal}
当前控件: <{target.get('tag')}> type={target.get('type')} placeholder="{target.get('placeholder')}" desc="{target.get('text')}"
请仅输出应该填入该输入框的文本值，不要加任何解释或标点符号。
"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 100
        }
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                if resp.status_code == 200:
                    text = resp.json()["choices"][0]["message"]["content"].strip()
                    return text.strip('"\'')
        except Exception:
            pass
        return None

    def _extract_text_heuristic(self, goal: str, target: Dict[str, Any]) -> str:
        """从自然语言目标中智能提取关键词"""
        # 1. 剔除常见的前置口语或引导词
        clean_goal = goal
        for prefix in ["在输入框中", "在搜索框中", "在网页中", "在页面中", "帮我", "请帮我", "去搜索", "请搜索"]:
            clean_goal = clean_goal.replace(prefix, "")

        # 2. 优先匹配 引号/书名号 中的内容，如 搜索“北京天气”
        matches = re.findall(r"['\"“「](.*?)['\"”」]", clean_goal)
        if matches:
            return matches[0]

        # 3. 匹配形如“搜索 xxx”、“输入 xxx”、“查 xxx”
        patterns = [
            r"(?:搜索|搜一下|搜|查一下|查询|输入|填写|找)\s*([^\s，。！？,!?]+)",
        ]
        for p in patterns:
            m = re.search(p, clean_goal)
            if m:
                extracted = m.group(1).strip()
                # 剔除常见的停用词
                for stop in ["内容", "一下", "信息"]:
                    extracted = extracted.replace(stop, "")
                if extracted:
                    return extracted

        # 若依然无法提取，返回简化的词组
        tokens = [w for w in clean_goal.split() if w not in ["在", "到", "打开", "百度", "网页", "页面"]]
        return tokens[-1] if tokens else "测试内容"
