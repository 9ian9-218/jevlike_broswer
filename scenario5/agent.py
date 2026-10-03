import time
from typing import Optional, List, Dict, Any, Callable
from scenario5.config import settings
from scenario5.browser.driver import BrowserDriver
from scenario5.browser.observer import DOMObserver
from scenario5.browser.verifier import PhysicalVerifier
from scenario5.decision.schemas import ActionType, ActionChoice, Observation
from scenario5.decision.fast_path import FastPathRouter
from scenario5.decision.slow_path import SlowPathAgent


class DualTrackAgent:
    """场景 5 双轨协同浏览器 Agent 主状态机"""

    def __init__(
        self,
        driver: Optional[BrowserDriver] = None,
        on_step_callback: Optional[Callable[[int, ActionChoice, str], None]] = None
    ):
        self.driver = driver or BrowserDriver()
        self.observer = DOMObserver(self.driver)
        self.verifier = PhysicalVerifier()
        self.fast_router = FastPathRouter()
        self.slow_agent = SlowPathAgent()
        self.on_step = on_step_callback
        self.history: List[ActionChoice] = []
        self._last_input_id: Optional[int] = None

    def execute_task(self, goal: str, start_url: Optional[str] = None) -> Dict[str, Any]:
        """执行端到端浏览器自动化任务"""
        self.history.clear()
        self._last_input_id = None
        start_time = time.time()
        
        if start_url:
            self.driver.navigate(start_url)

        step = 0
        success = False
        final_message = ""
        prev_fingerprint = ""
        typed_this_page = False
        submitted_this_page = False
        page_fingerprint_at_type = ""

        while step < settings.MAX_STEPS:
            step += 1

            # 1. 页面感知 (DOM Snapshot & Fingerprint)
            items, curr_fingerprint, compact_view = self.observer.capture_snapshot()
            # 页面切换时重置输入/提交记录
            if prev_fingerprint and curr_fingerprint != page_fingerprint_at_type and curr_fingerprint != prev_fingerprint:
                pass
            obs = Observation(
                url=self.driver.get_url(),
                title=self.driver.get_title(),
                fingerprint=curr_fingerprint,
                items=items,
                compact_view=compact_view,
                step_count=step,
                typed_this_page=typed_this_page,
                submitted_this_page=submitted_this_page,
            )

            # 2. 快轨决策 (Fast Path)：auto 模式走 Laya+规则集成，其余按指定模式
            if settings.FAST_PATH_MODE == "auto":
                choice = self.fast_router.decide_ensembled(goal, obs, self.history)
            else:
                choice = self.fast_router.decide(goal, obs, self.history)

            # 语义归一化：规则层的"输入后回车"哨兵统一转换（覆盖所有决策路径）
            if choice.value == "__RULE_ENTER__":
                choice.value = None
                choice.enter_after_type = True
                choice.press_enter = False
            elif choice.press_enter and choice.action == ActionType.TYPE_TEXT and choice.value:
                choice.enter_after_type = True
                choice.press_enter = False

            # 3. 若快轨受阻，唤醒慢轨故障自愈 (Slow Path Fallback)
            if choice.action == ActionType.BLOCKED:
                choice = self.slow_agent.recover_from_blocked(goal, obs, self.history)

            # 4. 若模型判定完成，执行严格的真实物理终态校验 (Physical Verification)
            if choice.action == ActionType.DONE:
                is_verified, reason = self.verifier.verify_done(
                    goal=goal,
                    url=obs.url,
                    title=obs.title,
                    items=items,
                    step_count=len(self.history)
                )
                if is_verified:
                    success = True
                    final_message = f"任务达成并经物理终态核验通过: {reason}"
                    if self.on_step:
                        self.on_step(step, choice, final_message)
                    break
                else:
                    # 假完成拦截：未通过物理校验，转慢轨自愈
                    choice = self.slow_agent.recover_from_blocked(goal, obs, self.history)

            # 5. 执行具体物理动作
            action_desc = ""
            if choice.action == ActionType.TYPE_TEXT:
                # PRESS_ENTER 控制：仅回车，不重新输入
                if choice.press_enter:
                    last_input = self._last_input_id
                    if last_input is not None:
                        action_desc = f"在 [{last_input}] 上按回车提交"
                        self.driver.type_text(last_input, "", clear=False, press_enter=True)
                    else:
                        action_desc = "回车提交（无最近输入框，转为等待）"
                        time.sleep(0.5)
                else:
                    # 寻找目标控件
                    target_item = next((it for it in items if it["id"] == choice.target_id), None)
                    if not target_item and items:
                        target_item = items[0]
                        choice.target_id = target_item["id"]

                    if target_item is not None:
                        self._last_input_id = target_item["id"]

                    # 优先使用模型/决策给出的 value，否则唤醒慢轨按需生成文本
                    if not choice.value:
                        choice.value = self.slow_agent.generate_input_text(goal, target_item or {}, obs)

                    action_desc = f"在 [{choice.target_id}] 输入文本: \"{choice.value}\""
                    if choice.enter_after_type or choice.press_enter:
                        action_desc += " 并回车"
                    self.driver.type_text(choice.target_id, choice.value, clear=True,
                                          press_enter=choice.enter_after_type or choice.press_enter)
                    typed_this_page = True
                    page_fingerprint_at_type = curr_fingerprint

            elif choice.action == ActionType.CLICK:
                target_item = next((it for it in items if it["id"] == choice.target_id), None)
                target_text = target_item.get("text", "") if target_item else ""
                action_desc = f"点击元素 [{choice.target_id}] <{target_item.get('tag', 'ele') if target_item else ''}>: {target_text}"
                self.driver.click(choice.target_id)
                # 输入后的点击视为一次提交尝试
                if typed_this_page and page_fingerprint_at_type == prev_fingerprint:
                    submitted_this_page = True

            elif choice.action == ActionType.SELECT:
                target_item = next((it for it in items if it["id"] == choice.target_id), None)
                target_tag = target_item.get("tag", "") if target_item else ""
                if not choice.value or target_tag != "select":
                    # 无效 SELECT（无选项值或目标不是下拉框）：降级为点击
                    choice.action = ActionType.CLICK
                    target_text = target_item.get("text", "") if target_item else ""
                    action_desc = f"SELECT 缺少选项值，降级为点击 [{choice.target_id}]: {target_text}"
                    self.driver.click(choice.target_id)
                else:
                    action_desc = f"选择下拉项 [{choice.target_id}]: {choice.value}"
                    self.driver.select_option(choice.target_id, choice.value)

            elif choice.action == ActionType.SCROLL:
                action_desc = f"滚动页面: {choice.value or 'down'}"
                self.driver.scroll(choice.value or "down")

            elif choice.action == ActionType.WAIT:
                action_desc = "等待页面异步加载"
                time.sleep(1.0)

            # 物理无效操作检测：点击/输入后页面无任何突变，累计通知集成层进入自愈
            no_mutation = not self.verifier.check_mutation(prev_fingerprint, curr_fingerprint) \
                and choice.action in (ActionType.CLICK, ActionType.TYPE_TEXT)
            if no_mutation:
                self._no_mutation_streak = getattr(self, "_no_mutation_streak", 0) + 1
                if self._no_mutation_streak >= 2:
                    self.fast_router._physical_stuck = True
            else:
                self._no_mutation_streak = 0

            # 决策循环熔断：连续 3 次相同决策且页面无变化时，终止避免死循环
            if len(self.history) >= 2:
                last_two = self.history[-2:]
                if all(h.action == choice.action and h.target_id == choice.target_id for h in last_two) \
                        and no_mutation:
                    action_desc += " | ⚠ 检测到决策死循环，熔断终止"
                    self.history.append(choice)
                    if self.on_step:
                        self.on_step(step, choice, action_desc)
                    return self._build_result(goal, start_time, success=False,
                                              message="触发决策死循环熔断，请尝试更换任务描述或切换 FAST_PATH_MODE")

            self.history.append(choice)

            if self.on_step:
                self.on_step(step, choice, action_desc)

            prev_fingerprint = curr_fingerprint

        return self._build_result(goal, start_time, success=success, message=final_message)

    def _build_result(self, goal: str, start_time: float, success: bool, message: str = "") -> Dict[str, Any]:
        """汇总执行统计指标"""
        total_time = time.time() - start_time
        fast_steps = sum(1 for h in self.history if h.is_fast_path)
        slow_steps = sum(1 for h in self.history if not h.is_fast_path)
        avg_fast_ms = (
            sum(h.elapsed_ms for h in self.history if h.is_fast_path) / fast_steps
            if fast_steps > 0 else 0.0
        )

        return {
            "success": success,
            "goal": goal,
            "steps_taken": len(self.history),
            "fast_steps": fast_steps,
            "slow_steps": slow_steps,
            "avg_fast_latency_ms": round(avg_fast_ms, 2),
            "total_time_seconds": round(total_time, 2),
            "final_url": self.driver.get_url(),
            "final_title": self.driver.get_title(),
            "message": message or ("达到最大步数上限" if not success else "成功")
        }
