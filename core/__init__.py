"""Comeyubot 红包系统 — 核心包。

模块分层（自底向上，供核心引擎与 Web 后端共享）：
- config    : 集中配置（.env） + RunConfig（单账号有效配置）
- crypto    : session 加密/解密（AES-256-GCM）
- detector  : 红包识别与分类（direct / webapp / locked / ...）
- reporter  : 耗时报表
- vision    : 验证码视觉识别
- browser   : 浏览器后端（playwright / cloakbrowser，跨平台 + headless）
- claimers  : 领取模块（按红包来源独立扩展）
- grabber   : 监听调度，串联以上模块
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
