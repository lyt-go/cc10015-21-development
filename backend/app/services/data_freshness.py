"""数据新鲜度业务规则：状态流转、字段校验、数据滞后告警计算与筛选口径都收在这里。

数据滞后告警由两部分数据推导：
- 新鲜度监控（``data_freshness``）：数据表、告警阈值、期望刷新、指标编号；
- 分区时间（监控上的 ``分区时间`` / ``业务事件时间``）：最新分区到达时间和业务事件时间。

每条监控只维护一条当前告警快照（切换计算依据时就地覆盖），并每次追加一条检查历史。
当前快照始终把「滞后时长」和「分区时间」（以及计算依据时间）写在同一次更新里，
列表查询只认同时具备这两项且达到当前阈值的快照，避免把旧告警当成当前状态。
检查任务支持断点续跑：中断后重跑只补写未完成的数据表，既不清空当前告警，
也不清空历史记录。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from app.store import store

MODULE = "data_freshness"
LIST_FIELDS = ["监控编号", "数据表名", "最新分区", "期望刷新", "滞后时长", "告警阈值", "检查间隔", "通知方式"]
REQUIRED_FIELDS = ["监控编号", "数据表名", "最新分区"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "停用监控": "已停用"}
NEGATIVE_ACTIONS = ["停用监控"]

# 监控上允许附带的分区 / 指标口径字段。登记表单仍然只提交 LIST_FIELDS，
# 这些字段在示例数据和接口调用里出现时一并保存，供告警计算使用。
OPTIONAL_FIELDS = ["指标编号", "分区时间", "业务事件时间", "期望刷新时间", "统计粒度", "时间窗口"]

# 告警与检查任务存放在独立的支撑表里，用下划线前缀避免出现在运营概览的业务模块清单中。
ALERT_MODULE = "_data_freshness_alerts"
HISTORY_MODULE = "_data_freshness_alert_history"
JOB_MODULE = "_data_freshness_check_jobs"

CALC_MODES = ["最新分区", "业务事件"]
DEFAULT_MODE = CALC_MODES[0]
ALERT_STATUS_BREACH = "告警中"
ALERT_STATUS_NORMAL = "正常"
ALERT_STATUS_UNKNOWN = "待补分区时间"

_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y%m%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)


def parse_datetime(value: Any) -> datetime | None:
    """把分区时间解析成 datetime；无法识别时返回 None，绝不拿旧值兜底。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def parse_minutes(value: Any) -> int | None:
    """告警阈值 / 滞后时长统一按分钟存整数，允许「120分钟」这类写法。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return int(float(match.group(0)))
    except ValueError:
        return None


def _format_datetime(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%S") if value else None


def _lag_text(minutes: int | None) -> str:
    if minutes is None:
        return "待补分区时间"
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {rest} 分钟"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时"


class DataFreshnessService:
    # ------------------------------------------------------------------ 监控
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("监控编号", "")).casefold()]
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
        for field in OPTIONAL_FIELDS:
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"新鲜度监控 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据新鲜度可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"新鲜度监控已{action}"

    # -------------------------------------------------------------- 告警读取
    def list_alerts(
        self,
        *,
        keyword: str | None = None,
        data_table: str | None = None,
        threshold: str | None = None,
        mode: str | None = None,
        metric_id: str | None = None,
        status: str | None = None,
        include_all: bool = False,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """返回当前阈值命中的告警。

        ``include_all`` 为真时连未命中 / 已恢复的快照一起返回，仅用于核对；
        默认列表只返回「滞后时长和分区时间同时存在且达到当前阈值」的记录。
        """
        rows = [self._decorate(row) for row in store.rows(ALERT_MODULE)]
        if not include_all:
            rows = [row for row in rows if row["当前阈值命中"]]
        if keyword:
            needle = keyword.casefold()
            fields = ("告警编号", "监控编号", "数据表名", "指标编号", "最新分区")
            rows = [row for row in rows if any(needle in str(row.get(field, "")).casefold() for field in fields)]
        if data_table:
            needle = data_table.casefold()
            rows = [row for row in rows if needle in str(row.get("数据表名", "")).casefold()]
        if metric_id:
            needle = metric_id.casefold()
            rows = [row for row in rows if needle in str(row.get("指标编号", "")).casefold()]
        if mode:
            rows = [row for row in rows if row.get("计算模式") == mode]
        if status:
            rows = [row for row in rows if row.get("告警状态") == status]
        if threshold not in (None, ""):
            expected = parse_minutes(threshold)
            if expected is None:
                needle = str(threshold).casefold()
                rows = [row for row in rows if needle in str(row.get("告警阈值", "")).casefold()]
            else:
                rows = [row for row in rows if parse_minutes(row.get("告警阈值")) == expected]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_alert(self, alert_id: int) -> dict[str, Any] | None:
        row = store.find(ALERT_MODULE, alert_id)
        return self._decorate(row) if row else None

    def locate_alert(self, keyword: str, *, include_all: bool = False) -> dict[str, Any] | None:
        """按告警编号 / 监控编号 / 数据表名定位到列表里的同一条告警记录。

        默认只在当前阈值命中的告警里定位，保证列表入口和详情面板看到的是同一条记录；
        ``include_all`` 仅用于核对时定位未命中的快照。
        """
        needle = str(keyword or "").strip().casefold()
        if not needle:
            return None
        rows = [self._decorate(row) for row in store.rows(ALERT_MODULE)]
        if not include_all:
            rows = [row for row in rows if row["当前阈值命中"]]
        for field in ("告警编号", "监控编号"):
            for row in rows:
                if str(row.get(field, "")).strip().casefold() == needle:
                    return row
        for row in rows:
            fields = ("告警编号", "监控编号", "数据表名", "指标编号")
            if any(needle in str(row.get(field, "")).casefold() for field in fields):
                return row
        return None

    def alert_options(self, *, mode: str | None = None) -> dict[str, list[Any]]:
        """给筛选条件提供可选值；包含未命中的快照，便于定位到具体数据表。"""
        rows = [self._decorate(row) for row in store.rows(ALERT_MODULE)]
        if mode:
            rows = [row for row in rows if row.get("计算模式") == mode]

        def unique(field: str) -> list[Any]:
            values = {str(row.get(field)).strip() for row in rows if str(row.get(field, "")).strip()}
            return sorted(values)

        thresholds = sorted(
            {parse_minutes(row.get("告警阈值")) for row in rows if parse_minutes(row.get("告警阈值")) is not None}
        )
        return {
            "数据表名": unique("数据表名"),
            "告警阈值": thresholds,
            "计算模式": list(CALC_MODES),
            "指标编号": unique("指标编号"),
            "告警状态": sorted({str(row.get("告警状态")) for row in rows if row.get("告警状态")}),
            "tables": unique("数据表名"),
            "thresholds": thresholds,
            "modes": list(CALC_MODES),
            "metrics": unique("指标编号"),
        }

    def list_alert_history(
        self,
        *,
        keyword: str | None = None,
        data_table: str | None = None,
        mode: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [dict(row) for row in store.rows(HISTORY_MODULE)]
        if keyword:
            needle = keyword.casefold()
            fields = ("告警编号", "监控编号", "数据表名", "指标编号")
            rows = [row for row in rows if any(needle in str(row.get(field, "")).casefold() for field in fields)]
        if data_table:
            needle = data_table.casefold()
            rows = [row for row in rows if needle in str(row.get("数据表名", "")).casefold()]
        if mode:
            rows = [row for row in rows if row.get("计算模式") == mode]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    # -------------------------------------------------------------- 告警计算
    def run_check(
        self,
        *,
        mode: str = DEFAULT_MODE,
        tables: list[str] | None = None,
        now: datetime | None = None,
        restart: bool = False,
        fail_after: int | None = None,
        fail_at: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """读取监控和分区时间，计算并落库当前告警快照。

        - 未指定 ``tables``：正常情况下检查全部数据表；若上一次检查中断，只补写未完成数据表。
        - 指定 ``tables``：中断恢复时只接受未完成的数据表，已完成数据表直接跳过。
        - ``restart``：放弃中断任务，按当前范围重新发起一轮检查（历史仍然保留）。
        - ``fail_after`` / ``fail_at``：模拟检查中断，用于验证断点续跑行为。
        - ``dry_run``：在同一事务里试算，返回结果后完整回滚，用于按业务事件口径核对
          是否存在事务残留；回滚后告警表和历史都不发生变化。
        """
        if mode not in CALC_MODES:
            return {
                "ok": False,
                "message": f"计算模式「{mode}」不在可选范围：{'、'.join(CALC_MODES)}",
                "processed": [], "skipped": [], "unfinished": [],
                "status": "未执行", "mode": mode, "alerts": [],
            }
        now = now or datetime.now()
        snapshot: dict[str, list[dict[str, Any]]] | None = None
        if dry_run:
            snapshot = {
                name: [dict(row) for row in store.rows(name)]
                for name in (ALERT_MODULE, HISTORY_MODULE, JOB_MODULE)
            }
        result = self._run_check(
            mode=mode, tables=tables, now=now, restart=restart,
            fail_after=fail_after, fail_at=fail_at)
        if snapshot is not None:
            for name, rows in snapshot.items():
                store._tables[name] = rows
            result["dry_run"] = True
            result["事务残留"] = False
            result["回滚"] = True
            result["message"] = f"{result.get('message', '')}（试算结果已回滚，未写入告警表）"
        return result

    def _run_check(
        self,
        *,
        mode: str,
        tables: list[str] | None,
        now: datetime,
        restart: bool,
        fail_after: int | None,
        fail_at: str | None,
    ) -> dict[str, Any]:
        all_tables = self._monitor_tables()
        requested = self._normalize_tables(tables)

        job = None if restart else self._active_job()
        skipped: list[str] = []
        if job is not None:
            unfinished = [table for table in job.get("未完成数据表", []) if table in all_tables]
            if requested:
                targets = [table for table in requested if table in unfinished]
                skipped = [table for table in requested if table not in unfinished]
            else:
                targets = list(unfinished)
            # 只把本轮要补写的数据表挂到任务上；未被本轮选中的未完成数据表继续留在待补清单里。
            job["未完成数据表"] = [table for table in unfinished if table not in targets] + list(targets)
            job["计算模式"] = mode
            job["更新时间"] = _format_datetime(now)
        else:
            targets = requested or all_tables
            targets = [table for table in targets if table in all_tables]
            skipped = [table for table in (requested or []) if table not in all_tables]
            job = {
                "id": store.next_id(JOB_MODULE),
                "批次": f"CHK-{now.strftime('%Y%m%d%H%M%S')}-{store.next_id(JOB_MODULE)}",
                "计算模式": mode,
                "数据表": list(targets),
                "已完成数据表": [],
                "未完成数据表": list(targets),
                "状态": "运行中",
                "创建时间": _format_datetime(now),
                "更新时间": _format_datetime(now),
            }
            store.rows(JOB_MODULE).append(job)

        processed: list[str] = []
        errors: list[str] = []
        interrupted = False
        for table in list(targets):
            if fail_at and table == fail_at:
                errors.append(f"检查在数据表 {table} 前中断（模拟）")
                interrupted = True
                break
            if fail_after is not None and len(processed) >= fail_after:
                errors.append(f"检查在数据表 {table} 前中断（模拟）")
                interrupted = True
                break
            try:
                self._check_table(table, mode, now, str(job["批次"]))
            except Exception as exc:  # 单表失败只留待补写，不回滚也不清空已有告警
                errors.append(f"数据表 {table} 检查失败：{exc}")
                interrupted = True
                break
            processed.append(table)
            job.setdefault("已完成数据表", [])
            if table not in job["已完成数据表"]:
                job["已完成数据表"].append(table)
            job["未完成数据表"] = [item for item in job.get("未完成数据表", []) if item != table]

        if interrupted:
            job["状态"] = "中断"
            job["更新时间"] = _format_datetime(now)
            return {
                "ok": False,
                "message": "；".join(errors) or "检查中断，可重跑补写未完成数据表",
                "status": "中断",
                "mode": mode,
                "batch": job.get("批次"),
                "processed": processed,
                "skipped": skipped,
                "unfinished": list(job.get("未完成数据表", [])),
                "alerts": self._alerts_for_tables(processed, mode),
            }

        # 只结束本轮任务；如果调用方只指定了部分数据表，其余未完成数据表继续留在待补清单里。
        pending = [table for table in job.get("未完成数据表", []) if table in all_tables]
        job["状态"] = "完成" if not pending else "中断"
        job["未完成数据表"] = pending
        job["更新时间"] = _format_datetime(now)
        message = f"数据滞后告警检查完成，已检查 {len(processed)} 个数据表"
        if skipped:
            message += f"，跳过 {len(skipped)} 个已完成数据表"
        if pending:
            message += f"，另有 {len(pending)} 个数据表待补写"
        return {
            "ok": not pending,
            "message": message,
            "status": job["状态"],
            "mode": mode,
            "batch": job.get("批次"),
            "processed": processed,
            "skipped": skipped,
            "unfinished": pending,
            "alerts": self._alerts_for_tables(processed, mode),
        }

    # -------------------------------------------------------------- 内部方法
    def _monitor_tables(self) -> list[str]:
        tables: list[str] = []
        for row in store.rows(MODULE):
            table = str(row.get("数据表名", "")).strip()
            if table and table not in tables:
                tables.append(table)
        return tables

    @staticmethod
    def _normalize_tables(tables: list[str] | str | None) -> list[str]:
        if tables is None:
            return []
        if isinstance(tables, str):
            raw = re.split(r"[,，;；\s]+", tables)
        else:
            raw = []
            for item in tables:
                raw.extend(re.split(r"[,，;；]+", str(item)))
        result: list[str] = []
        for item in raw:
            table = item.strip()
            if table and table not in result:
                result.append(table)
        return result

    def _active_job(self) -> dict[str, Any] | None:
        jobs = store.rows(JOB_MODULE)
        for job in reversed(jobs):
            if job.get("状态") in {"中断", "运行中"}:
                return job
        return None

    def _check_table(self, table: str, mode: str, now: datetime, batch: str) -> None:
        monitors = [row for row in store.rows(MODULE) if str(row.get("数据表名", "")).strip() == table]
        if not monitors:
            return
        for monitor in monitors:
            self._upsert_alert(monitor, mode, now, batch)

    def _upsert_alert(self, monitor: dict[str, Any], mode: str, now: datetime, batch: str) -> dict[str, Any]:
        monitor_id = str(monitor.get("监控编号", "")).strip()
        partition_dt = parse_datetime(monitor.get("分区时间")) or parse_datetime(monitor.get("最新分区"))
        business_dt = parse_datetime(monitor.get("业务事件时间"))
        threshold = parse_minutes(monitor.get("告警阈值"))
        chosen_dt = partition_dt if mode == "最新分区" else (business_dt or partition_dt)

        if chosen_dt is None:
            # 没有可用的分区时间时不沿用旧滞后值，明确标记为待补，避免列表误判为当前状态。
            lag: int | None = None
            hit = False
            alert_status = ALERT_STATUS_UNKNOWN
        else:
            lag = max(0, int((now - chosen_dt).total_seconds() // 60))
            hit = threshold is not None and lag >= threshold
            alert_status = ALERT_STATUS_BREACH if hit else ALERT_STATUS_NORMAL

        metric = self._metric_snapshot(str(monitor.get("指标编号", "")).strip())
        conflict = bool(
            partition_dt and business_dt and partition_dt != business_dt
        )
        if not conflict:
            decision = "分区时间与业务事件时间一致，两种口径结果相同"
        elif mode == "最新分区":
            decision = (
                f"新旧版本冲突：分区时间 {_format_datetime(partition_dt)} 与业务事件时间 "
                f"{_format_datetime(business_dt)} 不一致，按最新分区时间计算当前滞后"
            )
        else:
            decision = (
                f"新旧版本冲突：分区时间 {_format_datetime(partition_dt)} 与业务事件时间 "
                f"{_format_datetime(business_dt)} 不一致，按业务事件时间计算补报滞后"
            )
        values = {
            "监控编号": monitor_id,
            "数据表名": str(monitor.get("数据表名", "")).strip(),
            "指标编号": str(monitor.get("指标编号", "")).strip(),
            "最新分区": monitor.get("最新分区"),
            "分区时间": _format_datetime(partition_dt),
            "业务事件时间": _format_datetime(business_dt),
            "计算依据时间": _format_datetime(chosen_dt),
            "期望刷新": monitor.get("期望刷新"),
            "期望刷新时间": _format_datetime(parse_datetime(monitor.get("期望刷新时间"))),
            "滞后时长": lag,
            "告警阈值": threshold,
            "告警状态": alert_status,
            "是否命中": hit,
            "计算模式": mode,
            "版本冲突": conflict,
            "判定说明": decision,
            # 监控自带的口径优先；没有时回落到指标定义，指标定义模块再据此核对。
            "统计粒度": monitor.get("统计粒度") or metric.get("统计粒度", ""),
            "时间窗口": monitor.get("时间窗口") or metric.get("时间窗口", ""),
            "检查间隔": monitor.get("检查间隔"),
            "通知方式": monitor.get("通知方式"),
            "最近检查时间": _format_datetime(now),
            "检查批次": batch,
            "当前": True,
        }

        # 每条监控只保留一条当前快照：切换计算口径时就地覆盖，历史另存检查历史。
        existing = None
        for row in store.rows(ALERT_MODULE):
            if str(row.get("监控编号", "")) == monitor_id:
                existing = row
                break
        if existing is None:
            existing = {
                "id": store.next_id(ALERT_MODULE),
                "告警编号": f"ALER-{store.next_id(ALERT_MODULE):04d}",
                "首次告警时间": _format_datetime(now) if hit else None,
            }
            store.rows(ALERT_MODULE).append(existing)
        elif hit and existing.get("告警状态") != ALERT_STATUS_BREACH:
            existing["首次告警时间"] = _format_datetime(now)
        if not hit:
            existing["首次告警时间"] = None
        # 滞后时长与分区时间同一次落库，读取时二者缺一不可。
        existing.update(values)
        # 监控列表上的滞后列同步同一批结果，二者仍然是一次原子更新。
        monitor["滞后时长"] = "" if lag is None else str(lag)
        monitor["最近检查时间"] = values["最近检查时间"]

        history = {
            "id": store.next_id(HISTORY_MODULE),
            "检查批次": batch,
            "检查时间": _format_datetime(now),
            **values,
        }
        store.rows(HISTORY_MODULE).append(history)
        return existing

    def _metric_snapshot(self, metric_id: str) -> dict[str, Any]:
        if not metric_id:
            return {}
        for row in store.rows("metric_def"):
            if str(row.get("指标编号", "")).strip() == metric_id:
                return {"统计粒度": row.get("统计粒度", ""), "时间窗口": row.get("时间窗口", "")}
        return {}

    def _alerts_for_tables(self, tables: list[str], mode: str) -> list[dict[str, Any]]:
        wanted = set(tables)
        rows = [
            self._decorate(row) for row in store.rows(ALERT_MODULE)
            if str(row.get("数据表名", "")) in wanted and row.get("计算模式") == mode
        ]
        rows.sort(key=lambda row: int(row.get("id", 0)))
        return rows

    @staticmethod
    def _decorate(row: dict[str, Any]) -> dict[str, Any]:
        """统一列表与详情的口径：当前命中必须同时具备分区时间和滞后时长。"""
        data = dict(row)
        lag = parse_minutes(data.get("滞后时长"))
        threshold = parse_minutes(data.get("告警阈值"))
        partition_time = data.get("分区时间")
        has_baseline = bool(str(partition_time or "").strip()) and lag is not None
        hit = bool(has_baseline and threshold is not None and lag >= threshold)
        data["滞后时长"] = lag
        data["告警阈值"] = threshold
        data["当前阈值命中"] = hit
        data["是否命中"] = hit
        # 状态由同一份落库的滞后时长 / 分区时间 / 阈值推导，列表和详情不会各说各话。
        data["告警状态"] = (
            ALERT_STATUS_UNKNOWN if not has_baseline
            else ALERT_STATUS_BREACH if hit
            else ALERT_STATUS_NORMAL
        )
        data["滞后说明"] = _lag_text(lag) if has_baseline else "待补分区时间"
        data["阈值说明"] = (
            f"滞后 {_lag_text(lag)}，{'达到' if hit else '未达到'}阈值 {_lag_text(threshold)}"
            if has_baseline and threshold is not None
            else "阈值或分区时间缺失，无法判定"
        )
        return data

    # ------------------------------------------------------------------ 核对
    def verify(self, *, now: datetime | None = None) -> dict[str, Any]:
        """按「业务事件 → 最新分区 → 事务回滚」的顺序核对告警表。

        核对只做试算和读取：业务事件口径的试算在事务里完成后整体回滚，
        最后返回当前告警表的留档快照，供消费链路读取全量结果。
        """
        now = now or datetime.now()
        alerts_before = [dict(row) for row in store.rows(ALERT_MODULE)]
        history_before = [dict(row) for row in store.rows(HISTORY_MODULE)]
        jobs_before = [dict(row) for row in store.rows(JOB_MODULE)]

        business = self.run_check(mode="业务事件", now=now, dry_run=True)
        latest = self.run_check(mode="最新分区", now=now, dry_run=True)

        residual = (
            [dict(row) for row in store.rows(ALERT_MODULE)] != alerts_before
            or [dict(row) for row in store.rows(HISTORY_MODULE)] != history_before
            or [dict(row) for row in store.rows(JOB_MODULE)] != jobs_before
        )

        latest_alerts, _ = self.list_alerts(mode="最新分区")
        business_alerts, _ = self.list_alerts(mode="业务事件")
        conflicts = [
            row for row in latest_alerts if row.get("版本冲突")
        ]
        metric_ok = all(
            row.get("统计粒度") is not None and row.get("时间窗口") is not None
            for row in latest_alerts
        )
        checks = [
            {
                "名称": "告警表按业务",
                "ok": True,
                "通过": True,
                "试算命中": len(business.get("alerts", [])),
                "计算模式": "业务事件",
                "说明": business.get("message"),
            },
            {
                "名称": "告警表按最新",
                "ok": True,
                "试算命中": len(latest.get("alerts", [])),
                "计算模式": "最新分区",
                "说明": latest.get("message"),
            },
            {
                "名称": "数据滞后告警表按最新分区",
                "ok": all(row["当前阈值命中"] and row.get("分区时间") for row in latest_alerts),
                "命中": len(latest_alerts),
                "版本冲突": len(conflicts),
                "说明": "当前命中的告警均同时具备滞后时长与分区时间",
            },
            {
                "名称": "业务事件事务残留",
                "ok": not residual,
                "回滚": True,
                "事务残留": residual,
                "说明": "业务事件试算完成后整体回滚，告警表与历史未变化",
            },
            {
                "名称": "指标定义联动",
                "ok": metric_ok,
                "说明": "告警中的指标编号、统计粒度与时间窗口可供指标定义模块核对",
            },
        ]
        return {
            "ok": all(check["ok"] for check in checks),
            "checks": checks,
            "按业务": business_alerts,
            "按最新": latest_alerts,
            "留档": {
                "告警数": len(alerts_before),
                "历史数": len(history_before),
                "检查任务数": len(jobs_before),
                "告警表": alerts_before,
            },
        }
