from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ActionType(str, Enum):
    CLICK = "CLICK"
    TYPE_TEXT = "TYPE_TEXT"
    SELECT = "SELECT"
    SCROLL = "SCROLL"
    WAIT = "WAIT"
    DONE = "DONE"
    BLOCKED = "BLOCKED"


class ActionChoice(BaseModel):
    """单步决策产物（双轨共享格式）"""
    action: ActionType
    target_id: Optional[int] = Field(default=None, description="目标元素 data-s5-id 索引")
    value: Optional[str] = Field(default=None, description="需要填入的文本或选择项")
    press_enter: bool = Field(default=False, description="仅回车提交（不输入文本）")
    enter_after_type: bool = Field(default=False, description="输入文本后回车")
    reason: str = Field(default="", description="决策理由或推断依据")
    confidence: float = Field(default=1.0, description="置信度 0.0 ~ 1.0")
    elapsed_ms: float = Field(default=0.0, description="决策耗时（毫秒）")
    is_fast_path: bool = Field(default=True, description="是否由快轨直接产生")


class Observation(BaseModel):
    """页面瞬时观测数据"""
    url: str
    title: str
    fingerprint: str
    items: List[Dict[str, Any]]
    compact_view: str
    step_count: int = 0
    # 页面级动作记录：本轮任务中同页是否已完成输入与提交（由 Agent 层回填）
    typed_this_page: bool = False
    submitted_this_page: bool = False
