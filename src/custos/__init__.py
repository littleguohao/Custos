"""custos 顶层包。

研究长跑冻结代码（``core/code_freeze``）：环境变量 ``CUSTOS_CODE_FREEZE`` 指向快照时，
本进程（含 spawn worker / subprocess 子工具）之后的 custos import 一律从快照取。
"""

import os as _os

if _os.environ.get("CUSTOS_CODE_FREEZE", "off") != "off":
    from custos.core import code_freeze as _cf

    _cf.install_from_env()
