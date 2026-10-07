<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <header class="topbar">
        <nav>
          <a :class="{ active: view === 'scans' }" @click="view = 'scans'">扫描列表</a>
          <a :class="{ active: view === 'lamp' }" @click="view = 'lamp'">午休稀采</a>
        </nav>
        <span class="chip" :class="{ lit: lamp && lamp.lit }">
          <span class="dot"></span>稀采灯{{ lamp && lamp.lit ? "·亮" : "·灭" }}
        </span>
      </header>
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读" }}）</p>
      <section>
        <button class="secondary" @click="logout">退出</button>
        <button class="secondary" @click="refresh">刷新列表</button>
      </section>
      <template v-if="view === 'scans'">
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>
      <template v-else>
        <section>
          <div class="lamp-row">
            <div class="bulb" :class="{ on: lamp && lamp.lit }"></div>
            <div>
              <p class="lamp-state">{{ lamp && lamp.lit ? "稀采样灯亮着：午休窗内且近窗办结不足" : "稀采样灯未亮" }}</p>
              <p class="hint">亮灯只提示值班留意样本稀少，提交与写入通道始终放行，这盏灯绝不拒收。</p>
              <p class="hint" v-if="lamp">
                判定时刻（北京）{{ lamp.decision_time }}　·　午休窗内：{{ lamp.in_window ? "是" : "否" }}　·　近窗办结 {{ lamp.done_count }} 条
              </p>
            </div>
          </div>
        </section>
        <section>
          <h2>阈值</h2>
          <template v-if="lamp">
            <p class="hint">午休起止 {{ lamp.config.start }} — {{ lamp.config.end }}（北京时间，可跨午夜）；近窗 {{ lamp.config.window_minutes }} 分钟内办结少于 {{ lamp.config.min_done }} 条才亮灯。</p>
            <p class="hint" v-if="lamp.config.updated_by">最近由 {{ lamp.config.updated_by }} 修改于 {{ fmt(lamp.config.updated_at) }}</p>
          </template>
          <div v-if="isWriter">
            <label>午休开始（HH:MM）</label><input v-model="cfgStart" placeholder="11:30" />
            <label>午休结束（HH:MM）</label><input v-model="cfgEnd" placeholder="13:00" />
            <label>近窗最低办结条数</label><input type="number" min="1" max="999" v-model="cfgMinDone" />
            <label>近窗长度（分钟）</label><input type="number" min="1" max="720" v-model="cfgWindow" />
            <button :disabled="loading" @click="saveConfig">保存阈值</button>
          </div>
          <p v-else class="hint">观察员只读阈值与履历，不可修改。</p>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <h2>亮灯履历</h2>
          <table>
            <thead>
              <tr><th>时刻</th><th>事件</th><th>近窗办结</th><th>午休窗内</th></tr>
            </thead>
            <tbody>
              <tr v-for="ev in events" :key="ev.id">
                <td>{{ fmt(ev.at) }}</td>
                <td><span class="tag" :class="ev.event === 'lit' ? 'pending' : 'dim'">{{ ev.event === "lit" ? "亮灯" : "熄灭" }}</span></td>
                <td>{{ ev.done_count }}</td>
                <td>{{ ev.in_window ? "是" : "否" }}</td>
              </tr>
              <tr v-if="!events.length"><td colspan="4">暂无履历</td></tr>
            </tbody>
          </table>
        </section>
      </template>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const view = ref("scans");
const lamp = ref(null);
const events = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const cfgStart = ref("");
const cfgEnd = ref("");
const cfgMinDone = ref("");
const cfgWindow = ref("");
const error = ref("");
const loading = ref(false);
let timer;
let cfgLoaded = false;
const isWriter = computed(() => session.value?.role === "writer");
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}
function syncCfg() {
  if (!lamp.value) return;
  cfgStart.value = lamp.value.config.start;
  cfgEnd.value = lamp.value.config.end;
  cfgMinDone.value = lamp.value.config.min_done;
  cfgWindow.value = lamp.value.config.window_minutes;
  cfgLoaded = true;
}
async function fetchLamp() {
  const res = await fetch("/api/lunch-lamp", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) {
    lamp.value = await res.json();
    if (!cfgLoaded) syncCfg();
  }
}
async function fetchEvents() {
  const res = await fetch("/api/lunch-lamp/events", { headers: headers() });
  if (res.ok) events.value = await res.json();
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
  await fetchLamp();
  await fetchEvents();
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refresh();
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  lamp.value = null;
  events.value = [];
  view.value = "scans";
  cfgLoaded = false;
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveConfig() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/lunch-lamp/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        start: cfgStart.value,
        end: cfgEnd.value,
        min_done: Number(cfgMinDone.value),
        window_minutes: Number(cfgWindow.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "保存失败"; return; }
    lamp.value = data;
    syncCfg();
    await fetchEvents();
  } catch { error.value = "保存时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 980px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { font-size: 1rem; color: #86efac; margin: 0 0 0.6rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
.hint { color: #a7f3d0; font-size: 0.85rem; margin: 0.15rem 0; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.dim { background: #334155; color: #e2e8f0; }
.topbar { display: flex; justify-content: space-between; align-items: center; background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 0.6rem 1rem; margin-bottom: 1rem; }
.topbar nav a { color: #a7f3d0; margin-right: 1.1rem; cursor: pointer; padding-bottom: 2px; }
.topbar nav a.active { color: #fff; border-bottom: 2px solid #4ade80; font-weight: 600; }
.chip { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.85rem; padding: 0.25rem 0.7rem; border-radius: 999px; background: #022c22; color: #a7f3d0; }
.chip .dot { width: 10px; height: 10px; border-radius: 50%; background: #475569; }
.chip.lit .dot { background: #fbbf24; box-shadow: 0 0 10px 2px rgba(251, 191, 36, 0.8); }
.lamp-row { display: flex; gap: 1.25rem; align-items: center; }
.bulb { width: 72px; height: 72px; border-radius: 50%; background: #334155; border: 3px solid #475569; flex: none; }
.bulb.on { background: #fbbf24; border-color: #fde68a; box-shadow: 0 0 36px 10px rgba(251, 191, 36, 0.45); }
.lamp-state { font-size: 1.05rem; font-weight: 600; margin: 0 0 0.35rem; }
</style>
