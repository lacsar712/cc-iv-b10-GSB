"""午休稀采样灯：时钟 → 亮灯判定 → 写入通道。

灯只做提示，永不拦截提交；判定一律用后台记下的时刻（北京时间窗），
与容器时区无关。灯态翻转时在 lunch_lamp_events 记一条履历。
"""
from datetime import datetime, timedelta, timezone

# 电站按北京时间排午休；固定 +8 偏移（无夏令时），不依赖容器 tzdata。
LOCAL_TZ = timezone(timedelta(hours=8), "CST")

DEFAULT_CONFIG = {
    "start_minutes": 11 * 60 + 30,
    "end_minutes": 13 * 60,
    "min_done": 3,
    "window_minutes": 30,
}


def fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hhmm(text) -> int | None:
    """'HH:MM' → 当日分钟数，非法返回 None。"""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def in_window(minutes_now: int, start: int, end: int) -> bool:
    """start == end 视为空窗；start > end 为跨午夜窗。"""
    if start == end:
        return False
    if start < end:
        return start <= minutes_now < end
    return minutes_now >= start or minutes_now < end


def load_config(conn) -> dict:
    row = conn.execute(
        """SELECT start_minutes, end_minutes, min_done, window_minutes,
                  updated_by, updated_at
           FROM lunch_lamp_config WHERE id = 1"""
    ).fetchone()
    if row is None:
        return {**DEFAULT_CONFIG, "updated_by": None, "updated_at": None}
    return row


def evaluate(conn, now: datetime | None = None) -> dict:
    """读后台时钟 → 算灯态 → 落库；灯态翻转时记一条履历。

    只读时钟与办结数，绝不触碰写入通道。调用方负责提交事务。
    """
    now = now or datetime.now(timezone.utc)
    cfg = load_config(conn)
    local = now.astimezone(LOCAL_TZ)
    minutes_now = local.hour * 60 + local.minute
    win = in_window(minutes_now, cfg["start_minutes"], cfg["end_minutes"])
    since = now - timedelta(minutes=cfg["window_minutes"])
    done = conn.execute(
        "SELECT COUNT(*) AS n FROM iv_scans WHERE status = 'done' AND processed_at >= %s",
        (since,),
    ).fetchone()["n"]
    lit = bool(win and done < cfg["min_done"])
    state = conn.execute(
        "SELECT lit FROM lunch_lamp_state WHERE id = 1 FOR UPDATE"
    ).fetchone()
    prev = None if state is None else state["lit"]
    if prev is None:
        conn.execute(
            """INSERT INTO lunch_lamp_state (id, lit, evaluated_at)
               VALUES (1, %s, %s)
               ON CONFLICT (id) DO UPDATE
               SET lit = EXCLUDED.lit, evaluated_at = EXCLUDED.evaluated_at""",
            (lit, now),
        )
    else:
        conn.execute(
            "UPDATE lunch_lamp_state SET lit = %s, evaluated_at = %s WHERE id = 1",
            (lit, now),
        )
    if prev is not None and prev != lit:
        conn.execute(
            """INSERT INTO lunch_lamp_events (event, done_count, in_window, at)
               VALUES (%s, %s, %s, %s)""",
            ("lit" if lit else "out", done, win, now),
        )
    elif prev is None and lit:
        conn.execute(
            """INSERT INTO lunch_lamp_events (event, done_count, in_window, at)
               VALUES ('lit', %s, %s, %s)""",
            (done, win, now),
        )
    return {
        "lit": lit,
        "in_window": win,
        "done_count": done,
        "decision_time": local.strftime("%Y-%m-%d %H:%M:%S"),
        "evaluated_at": now.isoformat(),
        "config": {
            "start": fmt_hhmm(cfg["start_minutes"]),
            "end": fmt_hhmm(cfg["end_minutes"]),
            "min_done": cfg["min_done"],
            "window_minutes": cfg["window_minutes"],
            "updated_by": cfg.get("updated_by"),
            "updated_at": cfg["updated_at"].isoformat() if cfg.get("updated_at") else None,
        },
    }
