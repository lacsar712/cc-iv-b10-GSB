"""午休稀采样灯判定链路：时钟 -> 是否在午休窗 -> 近窗办结条数 -> 亮灯 -> 履历。

灯只是给值班的一盏提示灯，本身永远不参与拒收：是否亮灯只影响提示与履历，
不影响写入通道（iv_scans 的 INSERT 始终照常）。
"""
import re
from datetime import datetime, timedelta, timezone

HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_hhmm(text: str) -> tuple[int, int] | None:
    m = HHMM.match((text or "").strip())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def parse_iso(text: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat((text or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate_window(
    now: datetime, window_start: str, window_end: str, tz_offset_minutes: int
) -> tuple[bool, str]:
    """判定后台时钟 now 是否落在午休窗内。

    周期窗：起止都是 HH:MM，按电站本地时区（固定偏移）判定，支持跨午夜。
    绝对窗：任一边界是 ISO 时刻，则把窗当作一次性 [start, end) 绝对区间，
           方便测试把窗“盖住此刻”或“挪到夜间”。
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    start_iso = parse_iso(window_start)
    end_iso = parse_iso(window_end)
    start_hhmm = parse_hhmm(window_start)
    end_hhmm = parse_hhmm(window_end)

    if start_iso is not None and end_iso is not None:
        if end_iso <= start_iso:
            return False, "午休窗结束时刻不晚于开始时刻"
        return start_iso <= now < end_iso, "绝对窗"

    if start_hhmm is not None and end_hhmm is not None:
        local = now.astimezone(timezone(timedelta(minutes=tz_offset_minutes)))
        minutes = local.hour * 60 + local.minute
        s = start_hhmm[0] * 60 + start_hhmm[1]
        e = end_hhmm[0] * 60 + end_hhmm[1]
        if s == e:
            return False, "午休窗起止相同"
        if s < e:
            inside = s <= minutes < e
        else:  # 跨午夜，如 22:00-06:00
            inside = minutes >= s or minutes < e
        return inside, "周期窗"

    return False, "午休窗格式无法识别"


def decide_lamp(
    *,
    now: datetime,
    window_start: str,
    window_end: str,
    tz_offset_minutes: int,
    recent_minutes: int,
    min_completed: int,
    completed_count: int,
) -> dict:
    """纯判定：后台时刻在午休窗内 且 近窗办结条数不足，才允许亮灯。"""
    in_break, window_kind = evaluate_window(
        now, window_start, window_end, tz_offset_minutes
    )
    enough = completed_count >= min_completed
    should_light = in_break and not enough
    if not in_break:
        reason = f"后台时刻 {now.isoformat()} 不在午休窗内，灯灭"
    elif enough:
        reason = (
            f"午休窗内但近 {recent_minutes} 分钟已办结 {completed_count} 条"
            f"（不低于 {min_completed}），灯灭"
        )
    else:
        reason = (
            f"午休窗内且近 {recent_minutes} 分钟仅办结 {completed_count} 条"
            f"（低于 {min_completed}），亮稀采样灯"
        )
    return {
        "now": now.astimezone(timezone.utc),
        "in_break": in_break,
        "window_kind": window_kind,
        "completed_count": completed_count,
        "min_completed": min_completed,
        "recent_minutes": recent_minutes,
        "lamp_on": should_light,
        "reason": reason,
    }


def recent_completed_count(conn, now: datetime, recent_minutes: int) -> int:
    """近窗办结条数：只认后台已记下（status='done'、有 processed_at）的单。"""
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM iv_scans
           WHERE status = 'done'
             AND processed_at IS NOT NULL
             AND processed_at >= %s""",
        (now - timedelta(minutes=max(int(recent_minutes), 0)),),
    ).fetchone()
    return int(row["n"])


def get_config(conn) -> dict:
    row = conn.execute(
        """SELECT window_start, window_end, tz_offset_minutes, recent_minutes,
                  min_completed, lamp_on, updated_by, updated_at
           FROM break_config WHERE id = 1"""
    ).fetchone()
    return dict(row)


def list_history(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT id, lit_at, window_start, window_end, recent_minutes,
                  min_completed, completed_count, reason
           FROM break_lamp_history ORDER BY id DESC LIMIT %s""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def refresh_lamp(conn, now: datetime | None = None) -> dict:
    """跑完整链路并落库：更新灯态；仅在灭 -> 亮边沿写一条履历。

    返回判定快照。灯亮不灯亮都不抛拒收错误，调用方照常放行写入。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cfg = get_config(conn)
    count = recent_completed_count(conn, now, cfg["recent_minutes"])
    decision = decide_lamp(
        now=now,
        window_start=cfg["window_start"],
        window_end=cfg["window_end"],
        tz_offset_minutes=cfg["tz_offset_minutes"],
        recent_minutes=cfg["recent_minutes"],
        min_completed=cfg["min_completed"],
        completed_count=count,
    )

    was_on = bool(cfg["lamp_on"])
    is_on = bool(decision["lamp_on"])
    if is_on and not was_on:
        conn.execute(
            """INSERT INTO break_lamp_history
               (lit_at, window_start, window_end, recent_minutes,
                min_completed, completed_count, reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                decision["now"],
                cfg["window_start"],
                cfg["window_end"],
                cfg["recent_minutes"],
                cfg["min_completed"],
                count,
                decision["reason"],
            ),
        )
    conn.execute(
        "UPDATE break_config SET lamp_on = %s WHERE id = 1 AND lamp_on IS DISTINCT FROM %s",
        (is_on, is_on),
    )
    decision["history_appended"] = is_on and not was_on
    decision["previous_lamp_on"] = was_on
    return decision
