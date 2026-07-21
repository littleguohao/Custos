# -*- coding: utf-8 -*-
"""每日选股 screening 链：公式初筛 → 充实/模式识别 → 板块过滤打分 → 备选表格。

四段均为确定性脚本，LLM 不参与判断；TdxW 未运行时整链干净降级
（status=unavailable，不报错、不阻塞主链）。详见 00_governance/SCREENING_WORKFLOW.md。
"""
