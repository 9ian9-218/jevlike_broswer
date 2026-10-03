import hashlib
import json
from typing import List, Dict, Any, Tuple
from scenario5.browser.driver import BrowserDriver


OBSERVER_JS = """
return (() => {
    // 清除旧的标记
    const oldMarks = document.querySelectorAll('[data-s5-id]');
    oldMarks.forEach(el => el.removeAttribute('data-s5-id'));

    const isVisible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
            return false;
        }
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            return false;
        }
        // 允许在当前视口以及略微外围的元素
        if (rect.bottom < -200 || rect.top > (window.innerHeight + 200)) {
            return false;
        }
        return true;
    };

    const selector = 'button, input, select, textarea, a[href], [role="button"], [role="link"], [role="checkbox"], [role="tab"], [role="menuitem"], [onclick], [contenteditable="true"]';
    const candidates = Array.from(document.querySelectorAll(selector));
    
    const items = [];
    let count = 0;

    for (const el of candidates) {
        if (!isVisible(el)) continue;
        
        // 限制最多提取 120 个最关键的可见控件，保持上下文紧凑
        if (count >= 120) break;

        el.setAttribute('data-s5-id', count.toString());

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const type = el.getAttribute('type') || '';
        const elId = (el.id || '').trim();
        
        // 聚合描述文本：优先 aria-label、placeholder、inner text、value、title
        const ariaLabel = (el.getAttribute('aria-label') || '').trim();
        const placeholder = (el.getAttribute('placeholder') || '').trim();
        const title = (el.getAttribute('title') || '').trim();
        const name = (el.getAttribute('name') || '').trim();
        const value = (el.value || '').trim();
        
        let innerText = '';
        if (tag !== 'select') {
            innerText = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            if (innerText.length > 50) {
                innerText = innerText.substring(0, 50) + '...';
            }
        }

        let desc = ariaLabel || innerText || placeholder || title || value || name || (tag + (type ? ':' + type : ''));
        
        items.push({
            id: count,
            tag: tag,
            type: type,
            role: role,
            name: name,
            elid: elId,
            placeholder: placeholder,
            value: value,
            text: desc,
            checked: !!el.checked
        });

        count++;
    }

    return {
        url: window.location.href,
        title: document.title,
        items: items
    };
})();
"""


class DOMObserver:
    """页面 DOM 观察器与特征提取器"""

    def __init__(self, driver: BrowserDriver):
        self.driver = driver

    def capture_snapshot(self) -> Tuple[List[Dict[str, Any]], str, str]:
        """
        截取当前页面控件快照并计算防漂移指纹。
        返回: (元素列表, 页面指纹, 紧凑提示文本)
        """
        try:
            raw_res = self.driver.page.run_js(OBSERVER_JS)
        except Exception:
            raw_res = {"url": self.driver.get_url(), "title": self.driver.get_title(), "items": []}

        items = raw_res.get("items", []) if isinstance(raw_res, dict) else []
        url = raw_res.get("url", "") if isinstance(raw_res, dict) else ""
        title = raw_res.get("title", "") if isinstance(raw_res, dict) else ""

        # 计算页面指纹 (Fingerprint)
        content_for_hash = f"{url}|{title}|{len(items)}"
        if items:
            content_for_hash += f"|{items[0].get('text', '')}|{items[-1].get('text', '')}"
        fingerprint = hashlib.md5(content_for_hash.encode("utf-8")).hexdigest()[:12]

        # 生成紧凑的格式化文本供决策引擎分析
        lines = []
        for it in items:
            idx = it["id"]
            tag = it["tag"]
            desc = it["text"]
            extra = []
            if it.get("type"):
                extra.append(f"type={it['type']}")
            if it.get("placeholder"):
                extra.append(f"placeholder=\"{it['placeholder']}\"")
            if it.get("value"):
                extra.append(f"val=\"{it['value']}\"")
            extra_str = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"[{idx}] <{tag}>{extra_str}: {desc}")

        compact_view = "\n".join(lines)
        return items, fingerprint, compact_view
