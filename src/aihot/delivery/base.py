"""推送层公共约定。

所有渠道实现遵循：
- 失败一律抛 `NotifyError`（由编排层决定降级或告警），不吞异常
- 单次运行最多发送少量消息（企微限流 20 条/分钟），超长内容在渲染阶段分片
"""

from __future__ import annotations


class NotifyError(Exception):
    """推送失败。"""
