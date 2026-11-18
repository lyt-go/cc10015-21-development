"""指标定义业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.data_freshness import ALERT_MODULE
from app.store import store

MODULE = "metric_def"
LIST_FIELDS = ["指标编号", "指标名称", "计算公式", "统计粒度", "时间窗口", "基准取值", "精度位数", "口径版本"]
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class MetricDefService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("指标编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标定义可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"指标定义已{action}"

    def list_freshness_links(self, metric_id: str | None = None) -> list[dict[str, Any]]:
        """读取数据滞后告警中的指标编号，核对统计粒度和时间窗口。

        数据新鲜度模块只把指标编号写进告警快照，这里以指标定义为准做一致性核对：
        告警上的统计粒度 / 时间窗口必须与指标定义一致，否则标记为口径不一致。
        """
        rows = store.rows(MODULE)
        if metric_id:
            rows = [row for row in rows if str(row.get("指标编号", "")) == metric_id]
        links: list[dict[str, Any]] = []
        for definition in rows:
            code = str(definition.get("指标编号", "")).strip()
            if not code:
                continue
            alerts = [
                row for row in store.rows(ALERT_MODULE)
                if str(row.get("指标编号", "")).strip() == code
            ]
            for alert in alerts:
                granularity_ok = str(alert.get("统计粒度", "")) == str(definition.get("统计粒度", ""))
                window_ok = str(alert.get("时间窗口", "")) == str(definition.get("时间窗口", ""))
                links.append({
                    "指标编号": code,
                    "指标名称": definition.get("指标名称"),
                    "统计粒度": definition.get("统计粒度"),
                    "时间窗口": definition.get("时间窗口"),
                    "告警编号": alert.get("告警编号"),
                    "数据表名": alert.get("数据表名"),
                    "告警统计粒度": alert.get("统计粒度"),
                    "告警时间窗口": alert.get("时间窗口"),
                    "粒度一致": granularity_ok,
                    "窗口一致": window_ok,
                    "口径一致": granularity_ok and window_ok,
                })
        links.sort(key=lambda item: (str(item.get("指标编号")), str(item.get("告警编号"))))
        return links
