# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领
- 前端：Vue 3、Vite、nginx 反代 `/api`

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3202 |
| 接口 | http://localhost:8202 |
| PostgreSQL | localhost:54402（库名 `pvivscan`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| scanner | scan123456 | 可提交 |
| watcher | watch123456 | 只读 |

## 启动

```bash
cd projects/22-pv-string-iv-scan
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 种子

| 组串 | 填充因子 | 结论 |
|------|----------|------|
| 阵列A-串03 | 0.78 | 合格 |
| 阵列B-串11 | 0.61 | 衰减 |

## 午休稀采样灯

顶栏「午休稀采」进专页。灯只提示午休时段样本稀少，**任何情况下都不会拒收扫描单**。

判定链路（后台时钟驱动，写单/办结/改阈值/打开专页都会重走一次）：

1. 后台时刻落在午休窗内（`window_start`~`window_end`，支持 `HH:MM` 周期窗与时区偏移，或两个 ISO 绝对时刻组成的一次性窗，跨午夜亦可）；
2. 且近 `recent_minutes` 分钟内后台已办结（`status=done`）条数低于 `min_completed`；
3. 两条同时满足才亮灯，并只在灭→亮边沿写一条亮灯履历；条件不满足灯即灭，灯亮期间重复轮询不重复记履历。

权限：观察员 watcher 对阈值与履历只读；阈值（午休起止、近窗分钟、最低办结条数）仅 scanner 可改。专页上「清掉近窗办结」（`DELETE /api/logs`，仅 scanner）便于演练。

演练步骤：把午休起止填成盖住此刻的 ISO 时刻窗并清掉近窗办结 → 灯亮、履历多一条；再把窗挪到夜间 → 灯灭；灯亮或灯灭时在扫描台提交，单子一律 201 放行。

接口：`GET /api/break/status`（阈值+灯态+履历一次取齐）、`GET/PUT /api/break/config`、`GET /api/break/lamp`、`GET /api/break/history`、`DELETE /api/logs`。
