import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from lamp import evaluate, parse_hhmm
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


def need_writer(request: Request, detail: str = "仅扫描员可提交IV扫描"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=detail)
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
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


def to_int(val):
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float) and val.is_integer():
        return int(val)
    if isinstance(val, str):
        try:
            return int(val.strip())
        except ValueError:
            return None
    return None


@get("/api/lunch-lamp")
async def get_lunch_lamp(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        snap = evaluate(conn)  # 后台时刻现判，顺手刷新状态与履历
        conn.commit()
        return snap


@put("/api/lunch-lamp/config")
async def put_lunch_lamp_config(request: Request) -> dict:
    user = need_writer(request, "仅扫描员可改阈值，观察员只读")
    data = await request.json()
    start = parse_hhmm(data.get("start"))
    end = parse_hhmm(data.get("end"))
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="午休起止须为 HH:MM")
    if start == end:
        raise HTTPException(status_code=400, detail="午休起止不能相同")
    min_done = to_int(data.get("min_done"))
    window_minutes = to_int(data.get("window_minutes"))
    if min_done is None or not (1 <= min_done <= 999):
        raise HTTPException(status_code=400, detail="最低办结条数须为 1 到 999 的整数")
    if window_minutes is None or not (1 <= window_minutes <= 720):
        raise HTTPException(status_code=400, detail="近窗分钟数须为 1 到 720 的整数")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        conn.execute(
            """INSERT INTO lunch_lamp_config
               (id, start_minutes, end_minutes, min_done, window_minutes, updated_by, updated_at)
               VALUES (1, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                 start_minutes = EXCLUDED.start_minutes,
                 end_minutes = EXCLUDED.end_minutes,
                 min_done = EXCLUDED.min_done,
                 window_minutes = EXCLUDED.window_minutes,
                 updated_by = EXCLUDED.updated_by,
                 updated_at = EXCLUDED.updated_at""",
            (start, end, min_done, window_minutes, user["username"], now),
        )
        snap = evaluate(conn, now=now)  # 改完阈值立刻重判，灯态马上反映
        conn.commit()
        return snap


@get("/api/lunch-lamp/events")
async def list_lunch_lamp_events(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, event, done_count, in_window, at
               FROM lunch_lamp_events ORDER BY id DESC LIMIT 100"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        get_lunch_lamp,
        put_lunch_lamp_config,
        list_lunch_lamp_events,
    ]
)
