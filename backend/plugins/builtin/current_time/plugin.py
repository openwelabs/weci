"""内置 Current Time 插件。"""

from __future__ import annotations

import datetime
import zoneinfo

from pydantic import BaseModel, Field

from sdk import Tool, WeCiPlugin


class CurrentTimeArgs(BaseModel):
    timezone: str | None = Field(
        default=None,
        description="IANA 时区名称，例如 'Asia/Shanghai'、'UTC'、'America/New_York'。留空则使用服务器本地时区。",
    )


class CurrentTimeTool(Tool):
    name = "current_time"
    description = "获取当前日期、时间与时区信息。需要知道“现在几点”“今天星期几”“某个时区的当前时间”时使用。"
    args_model = CurrentTimeArgs

    async def execute(self, args: CurrentTimeArgs, context=None) -> str:
        tz_name = (args.timezone or "").strip()
        tz: datetime.tzinfo
        if not tz_name:
            local = datetime.datetime.now().astimezone()
            tz = local.tzinfo or datetime.timezone.utc
            display_name = datetime.datetime.now(tz).strftime("%Z") or "local"
        else:
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
                display_name = tz_name
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                available = sorted(zoneinfo.available_timezones())[:20]
                hint = "、".join(available) + "…" if available else ""
                return f"未知时区：{tz_name}。可使用 IANA 名称，例如 {hint}"

        now = datetime.datetime.now(tz)
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        return (
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"星期：{weekday_cn}\n"
            f"时区：{display_name} (UTC{now.strftime('%z')})"
        )


class CurrentTimePlugin(WeCiPlugin):
    def get_tools(self) -> list[Tool]:
        return [CurrentTimeTool()]
