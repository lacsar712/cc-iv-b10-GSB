import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from break_policy import (
    get_config,
    list_history,
    parse_hhmm,
    parse_iso,
    refresh_lamp,
)
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now),
                )
        conn.execute(
            "INSERT INTO break_config (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
        )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可操作")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    # 灯只是提示，绝不能变成拒收：任何时刻写入通道都照常 INSERT。
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], now),
        ).fetchone()
        # 沿同一链路走一次时钟 -> 亮灯 -> 履历；即便判定出错也不影响这条单落库。
        try:
            refresh_lamp(conn, now)
        except Exception as exc:  # pragma: no cover - 灯路故障不得波及写入
            print(f"break lamp refresh error: {exc}", flush=True)
        conn.commit()
        return dump(row)


@delete("/api/logs", status_code=200)
async def clear_logs(request: Request) -> dict:
    """清空近窗办结（连同全部扫描记录），便于演练“清掉近窗办结 -> 亮灯”。"""
    need_writer(request)
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        conn.execute("DELETE FROM iv_scans")
        try:
            refresh_lamp(conn)
        except Exception as exc:  # pragma: no cover
            print(f"break lamp refresh error: {exc}", flush=True)
        conn.commit()
        return {"deleted": n}


def _validate_window_field(name, value):
    text = (str(value) if value is not None else "").strip()
    if parse_hhmm(text) is not None or parse_iso(text) is not None:
        return text
    raise HTTPException(status_code=400, detail=f"{name} 必须是 HH:MM 或 ISO 时刻")


def _validate_int(name, value, lo, hi):
    try:
        iv = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{name} 必须是整数")
    if not lo <= iv <= hi:
        raise HTTPException(status_code=400, detail=f"{name} 必须在 {lo}~{hi} 之间")
    return iv


@get("/api/break/config")
async def get_break_config(request: Request) -> dict:
    """阈值对观察员只读开放。"""
    need_login(request)
    with connect() as conn:
        return dump(get_config(conn))


@put("/api/break/config")
async def update_break_config(request: Request) -> dict:
    """修改午休起止 / 近窗分钟 / 最低办结条数；仅扫描员可写，观察员只读。"""
    user = need_writer(request)
    data = await request.json()

    fields = {}
    if "window_start" in data:
        fields["window_start"] = _validate_window_field("午休开始", data["window_start"])
    if "window_end" in data:
        fields["window_end"] = _validate_window_field("午休结束", data["window_end"])
    if "tz_offset_minutes" in data:
        fields["tz_offset_minutes"] = _validate_int(
            "时区偏移分钟", data["tz_offset_minutes"], -720, 840
        )
    if "recent_minutes" in data:
        fields["recent_minutes"] = _validate_int(
            "近窗分钟", data["recent_minutes"], 1, 1440
        )
    if "min_completed" in data:
        fields["min_completed"] = _validate_int(
            "最低办结条数", data["min_completed"], 0, 100000
        )
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的阈值字段")

    with connect() as conn:
        cfg = get_config(conn)
        cfg.update(fields)
        start_is_iso = parse_iso(cfg["window_start"]) is not None
        end_is_iso = parse_iso(cfg["window_end"]) is not None
        if start_is_iso != end_is_iso:
            raise HTTPException(
                status_code=400, detail="午休起止格式需一致：同为 HH:MM 或同为 ISO 时刻"
            )
        if start_is_iso and end_is_iso:
            s = parse_iso(cfg["window_start"])
            e = parse_iso(cfg["window_end"])
            if e <= s:
                raise HTTPException(status_code=400, detail="午休结束时刻必须晚于开始时刻")
        sets = ", ".join(f"{k} = %s" for k in fields)
        params = [*fields.values(), user["username"], datetime.now(timezone.utc)]
        conn.execute(
            f"""UPDATE break_config SET {sets}, updated_by = %s, updated_at = %s
                WHERE id = 1""",
            params,
        )
        # 阈值一变立刻重走判定链路，让灯与履历跟上新窗口。
        snapshot = refresh_lamp(conn)
        conn.commit()
        return {"config": dump(get_config(conn)), "lamp": dump(snapshot)}


@get("/api/break/lamp")
async def get_break_lamp(request: Request) -> dict:
    """现算一次时钟 -> 午休窗 -> 近窗办结 -> 灯态，灯态与履历落库。"""
    need_login(request)
    with connect() as conn:
        snapshot = refresh_lamp(conn)
        return dump(snapshot)


@get("/api/break/history")
async def get_break_history(request: Request) -> list:
    """亮灯履历对观察员只读开放。"""
    need_login(request)
    with connect() as conn:
        return [dump(r) for r in list_history(conn)]


@get("/api/break/status")
async def get_break_status(request: Request) -> dict:
    """专页一次取齐：阈值 + 当前灯态判定 + 亮灯履历。"""
    need_login(request)
    with connect() as conn:
        snapshot = refresh_lamp(conn)
        return {
            "config": dump(get_config(conn)),
            "lamp": dump(snapshot),
            "history": [dump(r) for r in list_history(conn)],
        }


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        clear_logs,
        get_break_config,
        update_break_config,
        get_break_lamp,
        get_break_history,
        get_break_status,
    ]
)
