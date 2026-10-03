import re
import time
import httpx
from typing import Optional, List, Dict, Any
from scenario5.config import settings
from scenario5.decision.schemas import ActionType, ActionChoice, Observation


class FastPathRouter:
    """快轨高频决策路由器（≤50ms 级别单请求操作分类）"""

    def __init__(self):
        self.mode = settings.FAST_PATH_MODE
        self._laya_checked = False
        self._laya_available = False
        self._local_laya_model = None
        self._physical_stuck = False

    def _check_laya_live(self) -> bool:
        """极速探活本地 Laya 服务，带有状态缓存"""
        if self._laya_checked:
            return self._laya_available
        self._laya_checked = True
        try:
            # 极速连接探针 (50ms 超时)
            with httpx.Client(timeout=httpx.Timeout(0.05, connect=0.05)) as client:
                client.get(settings.LAYA_BASE_URL.rstrip("/"))
                self._laya_available = True
        except Exception:
            self._laya_available = False
        return self._laya_available

    def _get_local_model(self):
        """懒加载进程内本地微调模型（优先 GPU）"""
        if self._local_laya_model:
            return self._local_laya_model
        import sys
        from pathlib import Path
        m_path = Path(settings.LOCAL_MODEL_PATH)
        if not m_path.exists():
            return None
        sys.path.insert(0, str(m_path))
        from laya_browser import LayaBrowser
        device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
        except ImportError:
            pass
        self._local_laya_model = LayaBrowser.from_pretrained(str(m_path), device=device)
        return self._local_laya_model

    # 语义化角色描述符，与 jev-ultrafast 训练分布对齐
    ROLE_DESC = {
        "searchbox": "搜索框", "textbox": "输入框", "button": "按钮", "link": "链接",
        "checkbox": "复选框", "combobox": "下拉框", "tab": "标签页", "menuitem": "菜单项",
        "password": "密码框", "email": "邮箱框",
    }

    @classmethod
    def _build_page(cls, goal: str, obs: Observation) -> dict:
        """构造官方 decide() 管线期望的 jev-ultrafast 页面观测格式"""
        actions = []
        for it in obs.items:
            tag = it.get("tag", "")
            if tag in ("input", "textarea"):
                kind = "fill"
            elif tag == "select":
                kind = "select"
            else:
                kind = "click"
            role = it.get("role") or tag
            if tag == "input":
                role = {"text": "searchbox", "search": "searchbox", "password": "password",
                        "email": "email", "": "textbox"}.get(it.get("type", "text"), "textbox")
            elif tag in ("a",):
                role = "link"
            elif tag == "button":
                role = "button"
            # 语义化 label：可见文本 + 角色描述符，帮助模型理解控件语义
            desc = (it.get("placeholder") or it.get("name") or it.get("text") or "").strip()
            role_cn = cls.ROLE_DESC.get(role, tag)
            if desc and kind == "click":
                label = f"{desc[:40]} ({role_cn})"
            else:
                label = role_cn if len(role_cn) >= 2 else f"{tag} ({role_cn})"
            act = {
                "id": f"e{it['id']}",
                "kind": kind,
                "node": it["id"],
                "label": label[:50],
                "role": role,
            }
            if kind in ("fill", "select"):
                act["value"] = it.get("value", "")
            actions.append(act)
        # 页面级控制（官方训练分布中包含）
        actions.append({"id": "scroll_down", "kind": "scroll", "label": "Scroll down"})
        actions.append({"id": "press_enter", "kind": "key", "label": "Press Enter"})

        return {
            "url": obs.url,
            "title": obs.title,
            "text": " ".join([i.get("text", "") for i in obs.items[:40]])[:1200],
            "actions": actions,
        }

    @staticmethod
    def _parse_decide(res: dict, obs: Observation, backend: str) -> ActionChoice:
        """解析官方 decide() 管线输出为 ActionChoice"""
        op = (res.get("operation") or "WAIT").upper()
        act = res.get("action")
        target_id, target_label = None, ""

        if op in ("CLICK", "TYPE_TEXT", "SELECT") and isinstance(act, dict):
            target_id = act.get("node")
            target_label = act.get("label", "")
        elif op == "PRESS_ENTER":
            # 回车控制：作用于最近一次输入的元素（在 Agent 层处理）
            pass
        elif op == "SCROLL_DOWN":
            op = "SCROLL"

        press_enter = op == "PRESS_ENTER"
        enter_after_type = False
        if press_enter:
            op = "TYPE_TEXT"  # 占位，实际执行走 press_enter 分支
            target_id = None

        value = (act.get("value") if isinstance(act, dict) else None) if op == "SELECT" else None
        if value == "__RULE_ENTER__":
            # 规则层"输入+回车"哨兵：value 置空由慢轨生成，回车在输入后执行
            value = None
            enter_after_type = True
            press_enter = False

        return ActionChoice(
            action=ActionType(op) if op in ActionType.__members__ else ActionType.WAIT,
            target_id=target_id,
            value=value,
            press_enter=press_enter,
            enter_after_type=enter_after_type,
            reason=f"[{backend}] {res.get('operation')} -> "
                   + (f"[{target_id}] {target_label}" if target_id is not None else op),
            confidence=float(res.get("confidence", 0.8)),
            is_fast_path=True,
        )

    def decide(self, goal: str, obs: Observation, history: List[ActionChoice]) -> ActionChoice:
        start_time = time.perf_counter()

        # 1. 直接进程内调用本地微调模型 (laya_local)
        if self.mode == "laya_local":
            choice = self._decide_via_laya_local(goal, obs, history)
            if choice:
                choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
                return choice

        # 2. 云端 Jev（若配置 Key）
        if self.mode in ("auto", "jev") and settings.TYPESAFE_API_KEY:
            choice = self._call_jev_cloud(goal, obs)
            if choice:
                choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
                return choice

        # 3. 本地 Laya HTTP 微服务（显式指定或探活存活时）
        if self.mode == "laya" or (self.mode == "auto" and self._check_laya_live()):
            choice = self._decide_via_laya_http(goal, obs, history)
            if choice:
                choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
                return choice

        # 4. 本地启发式规则快速决策（保证零延迟与防死循环闭环）
        choice = self._rule_based_decision(goal, obs, history)
        choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
        return choice

    def decide_ensembled(self, goal: str, obs: Observation, history: List[ActionChoice]) -> ActionChoice:
        """集成模式（auto 默认）：Laya 微调模型 + 启发式规则双路决策。

        若两路结论一致则直接采纳；不一致时优先采纳规则结论但保留模型决策记录，
        因为规则层对固定站点/中文短指令具有确定性与防循环保证（微调模型为英文训练分布）。
        """
        start_time = time.perf_counter()

        laya_choice = None
        if self.mode == "laya_local":
            laya_choice = self._decide_via_laya_local(goal, obs, history)
        elif self.mode == "laya" or (self.mode == "auto" and self._check_laya_live()):
            laya_choice = self._decide_via_laya_http(goal, obs, history)
        if not laya_choice and self.mode in ("auto", "jev") and settings.TYPESAFE_API_KEY:
            laya_choice = self._call_jev_cloud(goal, obs)

        rule_choice = self._rule_based_decision(goal, obs, history)

        if rule_choice.action == ActionType.BLOCKED:
            return rule_choice

        if laya_choice is None:
            rule_choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
            return rule_choice

        same_action = laya_choice.action == rule_choice.action
        same_target = laya_choice.target_id == rule_choice.target_id or rule_choice.target_id is None

        # 目标语义验证优先：模型选择的目标文本命中任务关键词时，直接采纳模型决策
        if not same_action and laya_choice.target_id is not None:
            goal_kw = [w for w in re.split(r"[\s，。！？,!?的把在到]", goal) if len(w) >= 2]
            model_target = next((it for it in obs.items if it["id"] == laya_choice.target_id), None)
            if model_target is not None:
                mtxt = (model_target.get("text", "") + model_target.get("placeholder", "")).lower()
                # 任务关键词直接出现在目标文本中（如“AI”命中“库库AI”）
                if any(kw.lower() in mtxt for kw in goal_kw):
                    laya_choice.reason += " | ✔ 模型目标命中任务关键词，采纳模型决策"
                    laya_choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
                    return laya_choice
                # 模型目标是无描述输入框（纯"输入框"语义）而规则层目标不是输入框时，采纳模型
                rule_target = next((it for it in obs.items if it["id"] == rule_choice.target_id), None) \
                    if rule_choice.target_id is not None else None
                if laya_choice.action == ActionType.TYPE_TEXT and model_target.get("tag") in ("input", "textarea") \
                        and (rule_target is None or rule_target.get("tag") not in ("input", "textarea")):
                    laya_choice.reason += " | ✔ 模型正确锁定输入框，采纳模型决策"
                    laya_choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
                    return laya_choice

        # 规则层无明确目标（如回退 SCROLL）而模型给出了具体目标时，采纳模型决策
        if not same_action and rule_choice.target_id is None and laya_choice.target_id is not None \
                and laya_choice.action in (ActionType.CLICK, ActionType.TYPE_TEXT):
            laya_choice.reason += " | ✔ 模型给出明确目标，采纳模型决策"
            laya_choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
            return laya_choice

        if same_action and same_target:
            laya_choice.reason += " | ✔ 与规则层结论一致"
            laya_choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
            return laya_choice

        # 结论不一致：采纳规则层，但记录模型建议
        rule_choice.reason += f" | 规则层护航（模型建议: {laya_choice.action.value}"
        if laya_choice.target_id is not None:
            rule_choice.reason += f" -> [{laya_choice.target_id}]"
        rule_choice.reason += "）"
        rule_choice.elapsed_ms = (time.perf_counter() - start_time) * 1000
        return rule_choice

    def _decide_via_laya_local(self, goal: str, obs: Observation, history: List[ActionChoice]) -> Optional[ActionChoice]:
        """进程内直接调用官方 decide() 管线"""
        try:
            lb = self._get_local_model()
            if not lb:
                return None
            page = self._build_page(goal, obs)
            hist = [{"action": h.reason[:40], "kind": h.action.value.lower(),
                     "text": h.value, "page_changed": True} for h in history[-5:]]
            res = lb.decide(page, goal=goal, history=hist)
            return self._parse_decide(res, obs, "Local Laya-Browser")
        except Exception:
            return None

    def _decide_via_laya_http(self, goal: str, obs: Observation, history: List[ActionChoice]) -> Optional[ActionChoice]:
        """通过 HTTP 调用本地 Laya 服务官方 decide 端点"""
        try:
            page = self._build_page(goal, obs)
            hist = [{"action": h.reason[:40], "kind": h.action.value.lower(),
                     "text": h.value, "page_changed": True} for h in history[-5:]]
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"{settings.LAYA_BASE_URL.rstrip('/').removesuffix('/v1')}/v1/decide",
                    json={"page": page, "goal": goal, "history": hist},
                )
                if resp.status_code == 200:
                    return self._parse_decide(resp.json(), obs, "Laya-Browser GPU")
        except Exception:
            pass
        return None

    def _call_jev_cloud(self, base_url: str = None, api_key: str = None, goal: str = None, obs: Observation = None) -> Optional[ActionChoice]:
        """向云端 Jev SystemOne API 发送决策请求（需 TYPESAFE_API_KEY）"""
        try:
            page = self._build_page(goal, obs)
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(
                    f"{settings.TYPESAFE_BASE_URL.rstrip('/')}/systemone",
                    headers={"Authorization": f"Bearer {settings.TYPESAFE_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"page": page, "goal": goal, "history": []},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    answers = data.get("answers", {})
                    op = (answers.get("operation", {}).get("choice") or "WAIT").upper()
                    tgt = (answers.get("click_target") or answers.get("type_text_target") or {}).get("choice", "")
                    target_id = int(tgt[1:]) if tgt.startswith("e") and tgt[1:].isdigit() else None
                    return ActionChoice(
                        action=ActionType(op) if op in ActionType.__members__ else ActionType.WAIT,
                        target_id=target_id,
                        reason=f"[Jev Cloud] {op} -> {tgt}",
                        confidence=float(answers.get("operation", {}).get("confidence", 0.8)),
                        is_fast_path=True,
                    )
        except Exception:
            pass
        return None

    def _rule_based_decision(self, goal: str, obs: Observation, history: List[ActionChoice]) -> ActionChoice:
        """内置高阶启发式决策网络：根据可见元素元数据与目标语义进行确定性单选"""
        items = obs.items
        goal_lower = goal.lower()

        # 0. 防止对同一元素重复无效点击导致的死循环
        if len(history) >= 2 and history[-1].action == ActionType.CLICK and history[-2].action == ActionType.CLICK:
            if history[-1].target_id == history[-2].target_id:
                return ActionChoice(
                    action=ActionType.BLOCKED,
                    reason="规则层检测到连续重复点击同一元素（无物理变化），请求慢轨自愈",
                    confidence=0.9
                )

        # 0.5 集成模式传递的物理受阻信号：连续无效点击后拒绝给出新目标
        if getattr(self, "_physical_stuck", False):
            self._physical_stuck = False
            return ActionChoice(
                action=ActionType.BLOCKED,
                reason="物理层报告连续无效操作，转慢轨自愈",
                confidence=0.9
            )

        # 0.6 页面级终态：同一页面上输入与提交均已完成 -> 建议交付核验（防止重复输入循环）
        if obs.typed_this_page and obs.submitted_this_page:
            return ActionChoice(
                action=ActionType.DONE,
                reason="同页面输入与提交均已完成，转入交付终态核验",
                confidence=0.9
            )

        # 1. 终态先决判断：如果已经执行过操作，且页面上已呈现结果特征
        if len(history) >= 1:
            for it in items:
                txt = it.get("text", "")
                if any(kw in txt for kw in ["找到", "结果", "条结果", "百度为您找到", "相关搜索", "提交成功", "成功"]):
                    return ActionChoice(
                        action=ActionType.DONE,
                        reason=f"页面已呈现有效结果信息: {txt[:30]}",
                        confidence=0.95
                    )

        # 2. 寻找输入框意图（搜索、查询、登录、输入等）
        has_search_intent = any(k in goal_lower for k in ["搜索", "搜", "查", "找", "输入", "填写", "search", "find"])
        
        # 寻找可输入的文本框（排除明显非目标语义的输入框，如 AI 聊天框）
        CHAT_HINTS = ["有什么想问", "问点啥", "聊", "ai助手", "文心", "chat", "ai ", "帮我写", "提问"]
        input_targets = [
            it for it in items
            if it.get("tag") in ("input", "textarea")
            and it.get("type", "") in ("text", "search", "", "password", "email")
            and not (has_search_intent and any(h in (it.get("text", "") + it.get("placeholder", "")).lower() for h in CHAT_HINTS))
        ]

        # 检查历史中是否刚刚输入过某个文本框
        last_input_id = None
        for h in reversed(history):
            if h.action == ActionType.TYPE_TEXT:
                last_input_id = h.target_id
                break

        # 如果有输入意图且存在合适的输入框，且当前尚未输入过该框
        if has_search_intent and input_targets and (last_input_id is None):
            best_input = input_targets[0]
            best_score = 0
            for inp in input_targets:
                elid = (inp.get("elid") or "").lower()
                txt = (inp.get("text", "") + " " + inp.get("placeholder", "") + " " + inp.get("name", "") + " " + elid).lower()
                score = 0
                # 元素 id/name 语义匹配（kw、q、search 等为最强信号）
                if elid in ("kw", "q", "wd", "search", "keyword", "query", "searchinput", "search-input") \
                        or inp.get("name", "") in ("wd", "kw", "q", "word"):
                    score = 3
                # 占位符/描述语义匹配
                elif any(w in txt for w in ["搜索", "关键字", "请输入"]):
                    score = 2
                # 纯文本输入框次优先
                elif inp.get("tag") == "input":
                    score = 1
                if score > best_score:
                    best_score = score
                    best_input = inp
            return ActionChoice(
                action=ActionType.TYPE_TEXT,
                target_id=best_input["id"],
                value="__RULE_ENTER__",
                reason=f"命中目标输入框 [{best_input['id']}] <{best_input['tag']}>: {best_input.get('text') or best_input.get('elid') or best_input.get('name')}",
                press_enter=True,
                confidence=0.95
            )

        # 3. 如果刚刚输入完毕，寻找真正的提交/搜索按钮（严禁把输入框自身误认为按钮）
        if last_input_id is not None:
            is_login_task = any(w in goal_lower for w in ["登录", "login", "注册"])
            submit_buttons = [
                it for it in items
                if it.get("id") != last_input_id
                and (
                    it.get("tag") == "button"
                    or (it.get("tag") == "input" and it.get("type") in ("submit", "button", "image"))
                    or (it.get("tag") == "a" and not is_login_task and any(w in it.get("text", "").lower() for w in ["搜索", "确定", "查询", "submit"]))
                )
                and any(w in (it.get("text", "") + " " + it.get("value", "")).lower() for w in ["搜索", "百度一下", "submit", "确定", "查询", "search", "开始", "登录"])
            ]
            
            already_clicked = any(h.action == ActionType.CLICK and h.target_id in [b["id"] for b in submit_buttons] for h in history)
            if submit_buttons and not already_clicked:
                btn = submit_buttons[0]
                return ActionChoice(
                    action=ActionType.CLICK,
                    target_id=btn["id"],
                    reason=f"命中提交按钮 [{btn['id']}] <{btn['tag']}>: {btn['text']}",
                    confidence=0.95
                )
            else:
                # 已经输入并回车，或按钮已点过，直接判定完成
                return ActionChoice(
                    action=ActionType.DONE,
                    reason="输入与提交动作均已完成，转入交付终态核验",
                    confidence=0.9
                )

        # 如果目标是点击某个特定文本（如“点击新闻”、“进入首页”）
        for it in items:
            t = it.get("text", "").strip()
            if not t or len(t) < 2:
                continue
            if t.lower() in goal_lower or any(token in t for token in goal.split() if len(token) > 2):
                return ActionChoice(
                    action=ActionType.CLICK,
                    target_id=it["id"],
                    reason=f"文本内容匹配目标意图 [{it['id']}]: {t}",
                    confidence=0.88
                )

        # 如果超过 2 步且没有明确动作，且页面已包含目标关键词，返回 DONE
        if len(history) >= 2:
            return ActionChoice(
                action=ActionType.DONE,
                reason="交互动作链已完整推进，进入交付终态",
                confidence=0.85
            )

        # 默认下滚尝试探索更多视口内容
        return ActionChoice(
            action=ActionType.SCROLL,
            value="down",
            reason="未发现明确可操作目标，向下滚动视口",
            confidence=0.6
        )
