# -*- coding: utf-8 -*-
"""SOURCE_NAME_OVERRIDES: source_id → correct display name.

Used as fallback when registry name contains '?' (encoding corruption),
or when source_id is not in the current registry but appears in historical data.
"""
SOURCE_NAME_OVERRIDES = {
    # 新时空系列
    "newtimespace": "新时空",
    "newtimespace_finance": "新时空财经",
    "newtimespace_research": "新时空研究院",
    "newtimespace_tech": "新时空科技",
    "newtimespace_etf": "新时空ETF",
    "newtimespace_ipo": "新时空IPO",
    "newtimespace_overseas": "新时空海外",
    # 经济观察网
    "eeo_all": "经济观察网",
    "eeo_finance": "经济观察网金融投资",
    "eeo_industry": "经济观察网公司产业",
    "eeo_politics": "经济观察网政经",
    # FT中文网
    "ftchinese_all": "FT中文网",
    "ftchinese_highlight": "FT中文网精华",
    "ftchinese_stock": "FT中文网A股",
    # 工信部
    "miit_news": "工信部新闻",
    "miit_policy": "工信部政策",
    "miit_press": "工信部新闻发布会",
    "miit_consultation": "工信部征求意见",
    # O'Reilly (apostrophe encoding issue)
    "oreilly_ai_ml": "O'Reilly AI & ML",
    # 未知短链
    "unknown_shortlink": "用户提供短链",
    "unknown_ai_shortlink": "用户提供AI短链",
}

def fix_source_name(source_id: str, source_name: str) -> str:
    """Return corrected source_name if it contains '?' or is empty."""
    if not source_name or '?' in source_name:
        return SOURCE_NAME_OVERRIDES.get(source_id, source_name or source_id)
    return source_name
