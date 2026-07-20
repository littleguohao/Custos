# -*- coding: utf-8 -*-
"""feishu_report_publisher 单测: 摘要生成(三种报告结构)、凭据优先级、失败路径(mock HTTP)。"""
from __future__ import annotations

import json
import unittest
from unittest import mock

import feishu_report_publisher as frp

# 结构取自 03_daily_plans/2026-07-20_daily_report.md(盘前日报)
DAILY_REPORT = """# 每日投研简报｜2026年7月20日（星期一）

> 生成时间：2026-07-20 22:59:58 Asia/Shanghai

## 1. 今日核心结论

**防守，总仓位建议 20%-40%；新开仓权限：禁止。**

- 择时评分：29.97/100
- 风控等级：强风控
- 精确数量权限：允许

## 2. 隔夜重大消息

| 时间 | 事件 |
|---|---|
| 2026-07-19 | 某事件 |

## 6. 当日行动建议

- P1 600150 中国船舶 清仓评估
- P1 601696 中银证券 反弹减仓

### 风险提示

- **禁止**：无计划追高
- **禁止**：绕过risk_control开仓
"""

# 结构取自 03_daily_plans/2026-07-20_1445_review.md(无"核心结论"节)
REVIEW_1445 = """# 14:45 收盘前操作建议 — 2026-07-20

> 生成时间：2026-07-20 22:22:45

## 0. 主要指数快照

| 指数 | 点位 |
|---|---|
| 上证指数 | 3786.35 |

## 1. 当日行情重估持仓

| 代码 | 名称 |
|---|---|
| 688114 | 华大智造 |

- 当日行情重估总仓位：**75.3%**

## 4. 操作建议

- 0AMV处于实质空头区间，所有反弹优先按减仓机会处理。
- 加仓/新开仓：继续禁止。

## 5. 运行权限

- 精确数量权限：允许。
- 提高仓位权限：禁止。
"""

# 结构取自 04_reviews/daily/2026-07-20_final_review.md(盘后复盘)
FINAL_REVIEW = """# 2026-07-20 最终盘后复盘

> 生成时间：2026-07-20 22:59:59

## 1. 今日计划、14:45建议与实际执行

- 市场状态：**防守**，建议仓位 **20%-40%**，收盘重估仓位 **75.3%**。
- 执行对账质量：**complete**；成交记录 0 笔。

| 代码 | 名称 | 实际动作 |
|---|---|---|
| 600150 | 中国船舶 | 无成交 |

## 6. 下一交易日条件化交易计划

- 总仓位目标：**20%-40%**；新开仓权限：**禁止**。
- P0 600150 中国船舶 下降N型结构清仓评估
"""


class BuildSummaryTest(unittest.TestCase):
    def test_daily_report_extracts_core_section(self):
        s = frp.build_exec_summary(DAILY_REPORT, "盘前日报", "2026-07-20")
        self.assertTrue(s.startswith("【盘前日报】2026-07-20"))
        self.assertIn("每日投研简报", s)
        self.assertIn("核心结论：", s)
        self.assertIn("防守，总仓位建议 20%-40%", s)
        self.assertIn("择时评分：29.97/100", s)
        # 不得包含表格行与引用行
        self.assertNotIn("|---|---|", s)
        self.assertNotIn("生成时间", s)

    def test_daily_report_actions_and_permissions(self):
        s = frp.build_exec_summary(DAILY_REPORT, "盘前日报", "2026-07-20")
        self.assertIn("逐股行动：", s)
        self.assertIn("600150", s)
        self.assertIn("601696", s)
        self.assertIn("权限：", s)
        self.assertIn("禁止", s)

    def test_1445_falls_back_to_first_section_with_body(self):
        s = frp.build_exec_summary(REVIEW_1445, "14:45尾盘建议", "2026-07-20")
        self.assertIn("当日行情重估总仓位", s)
        self.assertIn("权限：", s)
        # "禁止"行优先于普通"权限"行
        self.assertIn("提高仓位权限：禁止", s)

    def test_final_review_structure(self):
        s = frp.build_exec_summary(FINAL_REVIEW, "盘后复盘", "2026-07-20")
        self.assertIn("最终盘后复盘", s)
        self.assertIn("执行对账质量", s)
        self.assertIn("P0 600150", s)

    def test_action_lines_capped_at_6(self):
        extra = "\n".join(f"- P1 60000{i} 测试股{i} 减仓" for i in range(8))
        s = frp.build_exec_summary(DAILY_REPORT + "\n## 9. 补充\n\n" + extra, "T", "2026-07-20",
                                   limit=10000)
        action_part = s.split("\n逐股行动：\n", 1)[1].split("\n权限：", 1)[0]
        count = sum(1 for line in action_part.splitlines() if line.startswith("- "))
        self.assertEqual(count, 6)

    def test_permission_lines_capped_at_2(self):
        extra = "\n".join(f"- 禁止事项{i}" for i in range(5))
        s = frp.build_exec_summary(DAILY_REPORT + "\n## 9. 补充\n\n" + extra, "T", "2026-07-20",
                                   limit=10000)
        perm_part = s.split("\n权限：\n", 1)[1]
        count = sum(1 for line in perm_part.splitlines() if "禁止" in line or "权限" in line)
        self.assertEqual(count, 2)

    def test_truncation_with_marker(self):
        s = frp.build_exec_summary(DAILY_REPORT, "盘前日报", "2026-07-20", limit=200)
        self.assertTrue(s.endswith("...(完整报告见附件)"))
        self.assertLessEqual(len(s), 200)

    def test_short_content_no_fabrication(self):
        s = frp.build_exec_summary("# 空报告\n\n## 1. 空节\n", "T", "2026-07-20")
        self.assertIn("【T】2026-07-20", s)
        self.assertIn("空报告", s)
        self.assertNotIn("核心结论：", s)
        self.assertNotIn("逐股行动：", s)
        self.assertNotIn("权限：", s)


class CredentialsTest(unittest.TestCase):
    def test_env_takes_priority(self):
        env = {"FEISHU_APP_ID": "env_id", "FEISHU_APP_SECRET": "env_secret"}
        creds = frp.load_credentials(env)
        self.assertEqual(creds["app_id"], "env_id")
        self.assertEqual(creds["app_secret"], "env_secret")
        self.assertEqual(creds["source"], "env")

    def test_config_fallback(self):
        import tempfile
        cfg = {"channels": {"feishu": {"accounts": {"default": {
            "appId": "cfg_id", "appSecret": "cfg_secret"}}}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(cfg, fh)
            path = fh.name
        self.addCleanup(lambda: __import__("os").unlink(path))
        creds = frp.load_credentials({"OPENCLAW_CONFIG": path})
        self.assertEqual(creds["app_id"], "cfg_id")
        self.assertEqual(creds["app_secret"], "cfg_secret")
        self.assertEqual(creds["source"], path)

    def test_missing_credentials_raises(self):
        with self.assertRaises(frp.FeishuError):
            frp.load_credentials({"OPENCLAW_CONFIG": r"C:\nonexistent\nope.json"})

    def test_malformed_config_raises(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump({"channels": {}}, fh)
            path = fh.name
        self.addCleanup(lambda: __import__("os").unlink(path))
        with self.assertRaises(frp.FeishuError):
            frp.load_credentials({"OPENCLAW_CONFIG": path})

    def test_to_open_id_env_override(self):
        self.assertEqual(frp.resolve_to_open_id({"FEISHU_TO_OPEN_ID": "ou_x"}), "ou_x")
        self.assertEqual(frp.resolve_to_open_id({}), frp.DEFAULT_TO_OPEN_ID)


def _resp(body: dict):
    m = mock.Mock()
    m.json.return_value = body
    m.raise_for_status.return_value = None
    return m


class SendFlowTest(unittest.TestCase):
    """mock HTTP 层: 成功路径与每一步 code!=0 的失败路径。"""

    def _run_main(self, tmp_report):
        argv = ["--report", str(tmp_report), "--title", "盘后复盘", "--date", "2026-07-20"]
        with mock.patch.dict("os.environ", {"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "sec"},
                             clear=False):
            return frp.main(argv)

    def setUp(self):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        fh.write(FINAL_REVIEW)
        fh.close()
        self.report = fh.name
        self.addCleanup(lambda: __import__("os").unlink(self.report))

    def test_success_flow(self):
        token_ok = {"code": 0, "tenant_access_token": "tok"}
        upload_ok = {"code": 0, "data": {"file_key": "fk_123"}}
        msg_ok = {"code": 0, "data": {}}
        with mock.patch.object(frp.requests, "post",
                               side_effect=[_resp(token_ok), _resp(upload_ok), _resp(msg_ok),
                                            _resp(msg_ok)]) as post:
            rc = self._run_main(self.report)
        self.assertEqual(rc, 0)
        self.assertEqual(post.call_count, 4)
        # 文件消息在文本消息之前, msg_type 依次为 file / text
        msg_calls = [c.kwargs["json"]["msg_type"] for c in post.call_args_list[2:]]
        self.assertEqual(msg_calls, ["file", "text"])
        upload_kwargs = post.call_args_list[1].kwargs
        self.assertEqual(upload_kwargs["data"]["file_type"], "stream")
        self.assertEqual(upload_kwargs["data"]["file_name"], "2026-07-20_盘后复盘.md")

    def test_token_failure_exits_1(self):
        with mock.patch.object(frp.requests, "post",
                               return_value=_resp({"code": 999, "msg": "bad app"})):
            rc = self._run_main(self.report)
        self.assertEqual(rc, 1)

    def test_upload_failure_exits_1(self):
        with mock.patch.object(frp.requests, "post",
                               side_effect=[_resp({"code": 0, "tenant_access_token": "t"}),
                                            _resp({"code": 1, "msg": "upload denied"})]):
            rc = self._run_main(self.report)
        self.assertEqual(rc, 1)

    def test_message_failure_exits_1(self):
        with mock.patch.object(frp.requests, "post",
                               side_effect=[_resp({"code": 0, "tenant_access_token": "t"}),
                                            _resp({"code": 0, "data": {"file_key": "fk"}}),
                                            _resp({"code": 2, "msg": "no permission"})]):
            rc = self._run_main(self.report)
        self.assertEqual(rc, 1)

    def test_network_exception_exits_1(self):
        with mock.patch.object(frp.requests, "post", side_effect=TimeoutError("timed out")), \
             mock.patch.object(frp, "retry_call", side_effect=lambda f, **kw: f()):
            rc = self._run_main(self.report)
        self.assertEqual(rc, 1)

    def test_missing_report_exits_1(self):
        rc = frp.main(["--report", "nonexistent_dir/nope.md", "--title", "T", "--date", "2026-07-20"])
        self.assertEqual(rc, 1)

    def test_dry_run_never_calls_http(self):
        with mock.patch.object(frp.requests, "post") as post:
            rc = frp.main(["--report", self.report, "--title", "盘后复盘",
                           "--date", "2026-07-20", "--dry-run"])
        self.assertEqual(rc, 0)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
