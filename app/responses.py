"""响应工具：保持与原 Rocket 后端一致的输出格式。

- JSON 接口：紧凑 JSON（ensure_ascii=False，与 PG json 输出一致）
- 整型接口：纯文本数字
- 空聚合：PG 的 json_agg 空集返回 null，这里保持一致
"""
import json

from fastapi import Response


def json_resp(data) -> Response:
    return Response(
        content=json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        media_type="application/json",
    )


def int_resp(v: int) -> Response:
    return Response(content=str(v), media_type="text/plain")
