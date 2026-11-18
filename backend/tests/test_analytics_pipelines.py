"""多维分析实验平台核心链路测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.store import store
from app.services.experiment import ExperimentService
from app.services.sample_group import SampleGroupService
from app.services.ab_result import AbResultService
from app.services.metric_def import MetricDefService
from app.services.expr_factor import ExprFactorService
from app.services.segment_compare import SegmentCompareService
from app.services.attr_analysis import AttrAnalysisService
from app.services.anomaly_detect import AnomalyDetectService
from app.services.forecast_model import ForecastModelService
from app.services.insight_report import InsightReportService
from app.services.metric_alias import MetricAliasService
from app.services.evaluate_board import EvaluateBoardService
from app.services.data_freshness import DataFreshnessService
from app.services.cohort_track import CohortTrackService
from app.services.data_export import DataExportService
from app.services.drill_down import DrillDownService

def _clear_all():
    for k in list(store._tables.keys()):
        store._tables[k] = []

def test_pipeline_experiment_to_sample_groups():
    """实验创建后自动生成对照和实验组分群。"""
    _clear_all()
    es = ExperimentService()
    entry, ms = es.create_entry({
        "方案编号": "EXPE-001",
        "实验名称": "新首页布局",
        "对照组说明": "旧版首页",
        "实验组说明": "新版首页",
        "样本总量": "10000",
        "分流比例": "5:5",
        "启动日期": "2026-11-01",
        "预期时长": "14",
    })
    assert entry is not None, ms
    sg_rows = store.rows("sample_group")
    assert len(sg_rows) >= 2, f"应创建对照组和实验组，实际{len(sg_rows)}"
    control = [r for r in sg_rows if r.get("group_type") == "control"]
    exp = [r for r in sg_rows if r.get("group_type") == "experiment"]
    assert len(control) >= 1
    assert len(exp) >= 1
    assert int(control[0].get("样本基数", 0)) > 0
    assert int(exp[0].get("样本基数", 0)) > 0

def test_sample_group_members():
    """分群能生成带差异的用户列表。"""
    _clear_all()
    # 先创建实验再用自动分群
    es = ExperimentService()
    es.create_entry({"方案编号":"EXPE-002","实验名称":"测试","对照组说明":"控","实验组说明":"试","样本总量":"200","分流比例":"5:5"})
    sg = SampleGroupService()
    members = sg.get_group_members(1)
    assert len(members) > 0, "应有分群用户"
    assert "user_id" in members[0]
    assert "metric_value" in members[0]

def test_metric_def_creation():
    """指标定义创建和口径版本。"""
    _clear_all()
    md = MetricDefService()
    entry, ms = md.create_entry({
        "指标编号": "M-001",
        "指标名称": "点击率",
        "计算公式": "clicks / impressions",
        "统计粒度": "日",
        "时间窗口": "7",
        "基准取值": "0.10",
        "精度位数": "4",
        "口径版本": "v1",
    })
    assert entry is not None, ms

def test_ab_result_analysis():
    """AB分析生成对照和实验组对比。"""
    _clear_all()
    ab = AbResultService()
    results = ab.run_analysis({"样本范围": "4"})
    assert len(results) == 4
    for r in results:
        assert "指标值" in r
        assert "对比基准" in r
        assert "差异" in r

def test_expr_factor_creation():
    """实验因子互斥规则。"""
    _clear_all()
    ef = ExprFactorService()
    entry, ms = ef.create_entry({
        "因子编号": "F-001",
        "因子名称": "按钮颜色",
        "因子层级": "UI层",
        "取值集合": "红色,蓝色",
        "分配比例": "5:5",
        "互斥规则": "颜色因子互斥",
        "关联实验": "EXPE-001",
        "生效批次": "B001",
    })
    assert entry is not None, ms

def test_anomaly_detect():
    """异常检测计算移动平均。"""
    _clear_all()
    ad = AnomalyDetectService()
    ad.create_entry({
        "规则编号": "AD-001",
        "监控指标": "点击率",
        "检测算法": "moving_average",
        "基线窗口": "7",
        "波动阈值": "0.3",
        "告警级别": "高",
        "静默时段": "0",
        "规则版本": "v1",
    })
    result = ad.run_detection(1, [0.10, 0.12, 0.11, 0.13, 0.15, 0.14, 0.55])
    assert result.get("ok") or "anomaly" in str(result).lower()

def test_forecast_model():
    """预测模型简单移动平均。"""
    _clear_all()
    fc = ForecastModelService()
    fc.create_entry({
        "模型编号": "FC-001",
        "预测对象": "点击率",
        "历史窗口": "7",
        "预测周期": "3",
        "模型类型": "moving_average",
        "参数配置": "window=3",
        "训练批次": "T001",
        "评估误差": "",
    })
    result = fc.train_and_predict(1, [0.10, 0.12, 0.11, 0.13, 0.15, 0.14, 0.16])
    assert result.get("ok"), result

def test_metric_alias_version():
    """口径版本注册。"""
    _clear_all()
    ma = MetricAliasService()
    entry, ms = ma.create_entry({
        "版本编号": "ALIAS-001",
        "指标编号": "M-001",
        "变更说明": "修改去重逻辑",
        "旧口径": "clicks/impressions",
        "新口径": "clicks/valid_impressions",
        "生效时间": "2026-11-16",
        "审批状态": "草稿",
        "变更人": "admin",
    })
    assert entry is not None, ms



def _freshness_store():
    from app.seed import SEED_ROWS
    store._tables["data_freshness"] = [dict(row) for row in SEED_ROWS["data_freshness"]]
    store._tables["metric_def"] = [dict(row) for row in SEED_ROWS["metric_def"]]
    store._tables["_data_freshness_alerts"] = []
    store._tables["_data_freshness_alert_history"] = []
    store._tables["_data_freshness_check_jobs"] = []


def test_data_freshness_lag_alert_filtering_and_detail():
    """滞后计算后按数据表和阈值筛选，列表入口与详情面板结果一致。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    result = svc.run_check(now=datetime.now())
    assert result["ok"], result
    assert len(result["processed"]) == 3

    hits, total = svc.list_alerts()
    assert total >= 2
    assert all(row["当前阈值命中"] for row in hits)
    assert all(row["分区时间"] and row["滞后时长"] is not None for row in hits)

    table_rows, table_total = svc.list_alerts(data_table="dws_user_metrics")
    assert table_total == 1
    assert {row["数据表名"] for row in table_rows} == {"dws_user_metrics"}

    threshold_rows, threshold_total = svc.list_alerts(threshold="30")
    assert threshold_total == 1
    assert threshold_rows[0]["数据表名"] == "ads_campaign_daily"
    assert threshold_rows[0]["告警阈值"] == 30

    # 只出现当前阈值命中的告警；未命中的最快数据表不应出现
    assert all(row["数据表名"] != "ads_campaign_daily" or row["滞后时长"] >= 30 for row in hits)

    keyword_rows, keyword_total = svc.list_alerts(keyword=table_rows[0]["告警编号"])
    assert keyword_total == 1 and keyword_rows[0]["id"] == table_rows[0]["id"]

    detail = svc.get_alert(table_rows[0]["id"])
    assert detail == table_rows[0]
    assert detail["最新分区"] and detail["期望刷新时间"]
    assert detail["当前阈值命中"] is True


def test_data_freshness_alert_requires_partition_time():
    """滞后时长与分区时间必须同时落库；缺失分区时间时不沿用旧告警。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    svc.run_check(now=datetime.now())
    monitor = store.find("data_freshness", 2)
    monitor["分区时间"] = ""
    monitor["最新分区"] = ""
    monitor.pop("业务事件时间", None)
    svc.run_check(tables=["dws_user_metrics"], now=datetime.now())

    hits, _ = svc.list_alerts()
    assert all(row["数据表名"] != "dws_user_metrics" for row in hits)

    all_rows, _ = svc.list_alerts(include_all=True, data_table="dws_user_metrics")
    assert all_rows and all_rows[0]["滞后时长"] is None
    assert all_rows[0]["当前阈值命中"] is False
    assert all_rows[0]["告警状态"] == "待补分区时间"


def test_data_freshness_check_resume_keeps_history():
    """检查中断后只补写未完成数据表，不清空已有告警和历史。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    first = svc.run_check(tables=["dws_user_metrics"], now=datetime.now())
    assert first["ok"]
    alert_ids = {row["id"] for row in store.rows("_data_freshness_alerts")}
    history_before = len(store.rows("_data_freshness_alert_history"))

    interrupted = svc.run_check(fail_after=1, now=datetime.now())
    assert interrupted["status"] == "中断"
    assert interrupted["unfinished"]
    assert {row["id"] for row in store.rows("_data_freshness_alerts")} >= alert_ids

    resumed = svc.run_check(now=datetime.now())
    assert resumed["ok"], resumed
    assert set(resumed["processed"]) == set(interrupted["unfinished"])
    assert resumed["skipped"] == []
    assert {row["id"] for row in store.rows("_data_freshness_alerts")} >= alert_ids
    assert len(store.rows("_data_freshness_alert_history")) > history_before


def test_data_freshness_versions_conflict_modes():
    """新分区与旧分区补报冲突时，按最新分区 / 业务事件分别判定。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    now = datetime.now()
    svc.run_check(mode="最新分区", now=now)
    latest_rows, _ = svc.list_alerts(data_table="dws_user_metrics", mode="最新分区")
    assert latest_rows and latest_rows[0]["版本冲突"] is True
    assert latest_rows[0]["滞后时长"] > 120
    assert "最新分区时间" in latest_rows[0]["判定说明"]

    svc.run_check(mode="业务事件", now=now)
    business_rows, _ = svc.list_alerts(data_table="dws_user_metrics", mode="业务事件")
    # 业务事件时间接近现在且低于阈值，因此当前阈值下不出现
    assert business_rows == []


def test_data_freshness_metric_definition_link():
    """指标定义模块读取告警里的指标编号与统计粒度、时间窗口。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    svc.run_check(now=datetime.now())
    alerts, _ = svc.list_alerts(include_all=True)
    assert alerts
    assert all(row["指标编号"] for row in alerts)
    metric_ids = {row["指标编号"] for row in alerts}
    metric_defs = {row["指标编号"]: row for row in store.rows("metric_def")}
    for row in alerts:
        definition = metric_defs[row["指标编号"]]
        assert row["统计粒度"] == definition["统计粒度"]
        assert row["时间窗口"] == definition["时间窗口"]
    assert metric_ids <= set(metric_defs)


def test_metric_def_freshness_links_consistency():
    """指标定义接口读取告警指标编号，并核对统计粒度与时间窗口。"""
    from datetime import datetime
    from app.services.metric_def import MetricDefService
    _freshness_store()
    svc = DataFreshnessService()
    svc.run_check(now=datetime.now())
    links = MetricDefService().list_freshness_links()
    assert len(links) == 3
    assert all(link["口径一致"] for link in links)

    store.find("data_freshness", 1)["统计粒度"] = "按小时"
    svc.run_check(tables=["dwd_order_detail"], now=datetime.now())
    changed = [link for link in MetricDefService().list_freshness_links("METR-0001")]
    assert changed and not changed[0]["粒度一致"]
    assert changed[0]["窗口一致"]


def test_data_freshness_locate_and_dry_run():
    """关键词可定位到同一条告警；按业务事件试算后回滚，无事务残留。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    svc.run_check(now=datetime.now())
    hits, _ = svc.list_alerts()
    for row in hits:
        located = svc.locate_alert(row["告警编号"])
        assert located and located["id"] == row["id"]
        assert svc.locate_alert(row["数据表名"]) is not None

    alerts_before = [dict(row) for row in store.rows("_data_freshness_alerts")]
    history_before = [dict(row) for row in store.rows("_data_freshness_alert_history")]
    trial = svc.run_check(mode="业务事件", now=datetime.now(), dry_run=True)
    assert trial["回滚"] is True and trial["事务残留"] is False
    assert [dict(row) for row in store.rows("_data_freshness_alerts")] == alerts_before
    assert [dict(row) for row in store.rows("_data_freshness_alert_history")] == history_before


def test_data_freshness_verify_rolls_back_business_event_trial():
    """核对顺序：业务事件试算并回滚 -> 最新分区命中 -> 留档快照。"""
    from datetime import datetime
    _freshness_store()
    svc = DataFreshnessService()
    svc.run_check(now=datetime.now())
    before_alerts = [dict(row) for row in store.rows("_data_freshness_alerts")]
    before_history = [dict(row) for row in store.rows("_data_freshness_alert_history")]

    report = svc.verify(now=datetime.now())
    assert report["ok"] is True
    names = [check["名称"] for check in report["checks"]]
    assert names[:4] == ["告警表按业务", "告警表按最新", "数据滞后告警表按最新分区", "业务事件事务残留"]
    residual = next(check for check in report["checks"] if check["名称"] == "业务事件事务残留")
    assert residual["事务残留"] is False

    # 回滚无残留，留档快照与现有告警表一致
    assert [dict(row) for row in store.rows("_data_freshness_alerts")] == before_alerts
    assert [dict(row) for row in store.rows("_data_freshness_alert_history")] == before_history
    assert report["留档"]["告警表"] == before_alerts
    assert len(report["留档"]["告警表"]) == report["留档"]["告警数"]
