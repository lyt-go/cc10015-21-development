"""数据新鲜度接口：维护新鲜度监控，并读取、计算数据滞后告警。

列表入口与详情面板共用同一份告警快照和服务筛选口径：
- ``GET /alerts`` 只返回当前阈值命中的告警；
- ``GET /alerts/{alert_id}`` 返回同一条记录的完整明细（含最新分区、期望刷新时间）；
- ``POST /alerts/check`` 读取监控与分区时间，重新计算并落库滞后结果。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, PageResult
from app.services.data_freshness import DEFAULT_MODE, DataFreshnessService

router = APIRouter(prefix="/api/data_freshness", tags=["数据新鲜度"])

service = DataFreshnessService()

LIST_FIELDS = ["监控编号", "数据表名", "最新分区", "期望刷新", "滞后时长", "告警阈值", "检查间隔", "通知方式"]
STATUSES = ["草稿", "待校验", "已生效", "已停用"]
ALERT_FIELDS = [
    "告警编号", "监控编号", "数据表名", "指标编号", "最新分区", "分区时间",
    "业务事件时间", "期望刷新", "期望刷新时间", "滞后时长", "告警阈值",
    "告警状态", "计算模式",
]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


def csv_row(values: list[object]) -> list[str]:
    row: list[str] = []
    for value in values:
        text = "" if value is None else str(value)
        row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
    return row


# --------------------------------------------------------------------- 告警
# 固定路径必须声明在 /{entry_id} 之前，否则会被当成监控编号。
@router.get("/alerts", response_model=PageResult[dict])
def list_alerts(
    keyword: str | None = Query(default=None, description="按告警编号、监控编号、数据表名、指标编号检索"),
    data_table: str | None = Query(default=None, description="按数据表筛选"),
    threshold: str | None = Query(default=None, description="按当前告警阈值筛选"),
    mode: str | None = Query(default=None, description="最新分区、业务事件"),
    metric_id: str | None = Query(default=None, description="按指标编号筛选"),
    status: str | None = Query(default=None, description="告警中、正常、待补分区时间"),
    include_all: bool = Query(default=False, description="核对用：连同未命中快照一起返回"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """读取数据滞后告警；默认只返回当前阈值命中的记录。"""
    items, total = service.list_alerts(
        keyword=keyword, data_table=data_table, threshold=threshold, mode=mode,
        metric_id=metric_id, status=status, include_all=include_all, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/alerts/locate", response_model=dict)
def locate_alert(
    keyword: str = Query(..., description="告警编号、监控编号或数据表名"),
    include_all: bool = Query(default=False, description="核对用：也定位未命中的快照"),
) -> dict:
    """按关键词定位到列表里的同一条告警记录；列表入口和详情面板共用这份快照。"""
    alert = service.locate_alert(keyword, include_all=include_all)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"未找到与「{keyword}」匹配的数据滞后告警")
    return alert


@router.get("/alerts/verify", response_model=dict)
def verify_alerts() -> dict:
    """按业务事件试算并回滚，再按最新分区核对，返回当前告警表的留档快照。"""
    return service.verify()


@router.get("/alerts/archive", response_model=dict)
def archive_alerts() -> dict:
    """消费链路读取全份数据滞后告警表（含未命中快照）与检查历史规模。"""
    items, total = service.list_alerts(include_all=True, page=1, size=1000000)
    history, history_total = service.list_alert_history(page=1, size=1000000)
    return {
        "items": items,
        "total": total,
        "history": history,
        "history_total": history_total,
        "留档": {
            "告警数": total,
            "历史数": history_total,
            "告警表": items,
        },
    }


@router.get("/alerts/options")
def alert_options(mode: str | None = Query(default=None)) -> dict[str, list]:
    """筛选下拉的可选值；未命中的数据表和阈值也会出现。"""
    return service.alert_options(mode=mode)


@router.get("/alerts/history", response_model=PageResult[dict])
def list_alert_history(
    keyword: str | None = Query(default=None),
    data_table: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """检查历史留档；重跑检查只追加，不会清空。"""
    items, total = service.list_alert_history(
        keyword=keyword, data_table=data_table, mode=mode, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/alerts/check", response_model=dict)
def run_alert_check(payload: dict[str, object]) -> dict:
    """读取新鲜度监控和分区时间，计算数据滞后告警。

    支持 mode（最新分区 / 业务事件）、tables（只补写这些数据表）、
    restart（重新发起一轮检查）、fail_after / fail_at（模拟中断）；
    参数既可以放在顶层，也可以放在 values 里。
    """
    values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
    values = values or {}
    mode = str(values.get("mode") or values.get("计算模式") or DEFAULT_MODE).strip()
    tables = values.get("tables") or values.get("数据表") or None
    fail_after = values.get("fail_after")
    if fail_after in ("", None):
        fail_after = None
    else:
        try:
            fail_after = int(fail_after)
        except (TypeError, ValueError):
            return {"ok": False, "message": "fail_after 必须是整数", "status": "未执行"}
    result = service.run_check(
        mode=mode,
        tables=tables,
        restart=bool(values.get("restart")),
        fail_after=fail_after,
        fail_at=(str(values["fail_at"]).strip() if values.get("fail_at") else None),
        dry_run=bool(values.get("dry_run")),
    )
    return result


@router.get("/alerts/export")
def export_alerts(
    keyword: str | None = Query(default=None),
    data_table: str | None = Query(default=None),
    threshold: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    metric_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    include_all: bool = Query(default=False),
) -> StreamingResponse:
    """导出当前筛选条件下的数据滞后告警，与列表入口保持同一口径。"""
    items, _ = service.list_alerts(
        keyword=keyword, data_table=data_table, threshold=threshold, mode=mode,
        metric_id=metric_id, status=status, include_all=include_all, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *ALERT_FIELDS, "当前阈值命中", "滞后说明"])
    for item in items:
        writer.writerow(csv_row([
            item.get("id"),
            *(item.get(field, "") for field in ALERT_FIELDS),
            item.get("当前阈值命中"),
            item.get("滞后说明"),
        ]))
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="data_freshness_alerts.csv"'})


@router.get("/alerts/{alert_id}", response_model=dict)
def get_alert(alert_id: int) -> dict:
    """读取单条告警明细；与列表共用同一份快照，详情展开最新分区与期望刷新时间。"""
    alert = service.get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"数据滞后告警 {alert_id} 不存在")
    return alert


# --------------------------------------------------------------------- 监控
@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按监控编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已生效、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、监控编号关键字与状态过滤数据新鲜度列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        writer.writerow(csv_row([item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]))
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="data_freshness.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条新鲜度监控明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"新鲜度监控 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条新鲜度监控，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="新鲜度监控已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条新鲜度监控执行提交校验、标记生效、停用监控；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
