<template>
  <main>
    <header class="topbar">
      <h1>光伏组串IV扫描台</h1>
      <nav v-if="session" class="nav">
        <button class="navbtn" :class="{ active: page === 'scan' }" @click="page = 'scan'">扫描台</button>
        <button class="navbtn" :class="{ active: page === 'break' }" @click="page = 'break'">
          午休稀采
          <span class="lamp" :class="lampOn ? 'lit' : 'off'" :title="lampOn ? '稀采样灯亮' : '稀采样灯灭'"></span>
        </button>
      </nav>
    </header>

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
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交/改阈值" : "观察员只读" }}）
        <button class="secondary small" @click="logout">退出</button>
      </p>

      <!-- 扫描台页 -->
      <template v-if="page === 'scan'">
        <section>
          <button class="secondary" @click="refreshLogs">刷新列表</button>
        </section>
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="lampOn" class="hint">当前午休稀采样灯亮着——仅提示样本稀少，提交一律照常放行，不会拒收。</p>
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

      <!-- 午休稀采专页 -->
      <template v-else>
        <section class="lamp-panel">
          <div class="lamp-big" :class="lampOn ? 'lit' : 'off'"></div>
          <div>
            <div class="lamp-state">{{ lampOn ? "午休稀采样灯：亮" : "午休稀采样灯：灭" }}</div>
            <p class="lamp-reason">{{ breakStatus.lamp?.reason || "等待判定…" }}</p>
            <p class="lamp-meta" v-if="breakStatus.lamp">
              后台时刻 {{ fmtTime(breakStatus.lamp.now) }} ｜
              {{ breakStatus.lamp.in_break ? "此刻在午休窗内" : "此刻在午休窗外" }} ｜
              近 {{ breakStatus.lamp.recent_minutes }} 分钟办结
              <b>{{ breakStatus.lamp.completed_count }}</b> / 最低 {{ breakStatus.lamp.min_completed }} 条
            </p>
            <p class="rule">亮灯条件（二者同时满足）：后台时刻落在午休窗内，且近窗办结条数低于最低值。灯只提示，绝不拒收任何扫描单。</p>
          </div>
        </section>

        <section>
          <h2>午休阈值</h2>
          <div v-if="!isWriter" class="readonly-hint">观察员账号只读阈值与履历，不能修改。</div>
          <div class="grid">
            <label>午休开始（HH:MM 或 ISO 时刻）
              <input v-model="cfg.window_start" :disabled="!isWriter" placeholder="12:00" />
            </label>
            <label>午休结束（HH:MM 或 ISO 时刻）
              <input v-model="cfg.window_end" :disabled="!isWriter" placeholder="14:00" />
            </label>
            <label>近窗分钟数
              <input type="number" min="1" v-model="cfg.recent_minutes" :disabled="!isWriter" />
            </label>
            <label>最低办结条数
              <input type="number" min="0" v-model="cfg.min_completed" :disabled="!isWriter" />
            </label>
          </div>
          <p class="hint">演练：把起止填成盖住此刻的 ISO 时刻窗（如 {{ isoHint }}）灯可亮；挪到夜间窗灯即灭。</p>
          <div v-if="isWriter" class="row">
            <button :disabled="loading" @click="saveConfig">保存阈值</button>
            <button class="danger" :disabled="loading" @click="clearLogs">清掉近窗办结</button>
          </div>
          <p v-if="cfgMsg" :class="cfgOk ? 'oktext' : 'err'">{{ cfgMsg }}</p>
        </section>

        <section>
          <h2>亮灯履历</h2>
          <p v-if="!history.length" class="hint">还没有亮灯记录。盖住午休窗并清掉近窗办结后，这里会出现一条履历。</p>
          <table v-else>
            <thead>
              <tr><th>亮灯时刻</th><th>午休窗</th><th>近窗(分)</th><th>最低/实办结</th><th>原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="h in history" :key="h.id">
                <td>{{ fmtTime(h.lit_at) }}</td>
                <td>{{ h.window_start }} ~ {{ h.window_end }}</td>
                <td>{{ h.recent_minutes }}</td>
                <td>{{ h.min_completed }} / {{ h.completed_count }}</td>
                <td>{{ h.reason }}</td>
              </tr>
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
const page = ref("scan");
const logs = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const loading = ref(false);
const breakStatus = ref({ config: null, lamp: null, history: [] });
const cfg = ref({ window_start: "", window_end: "", recent_minutes: 60, min_completed: 3 });
const cfgMsg = ref("");
const cfgOk = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const lampOn = computed(() => !!breakStatus.value.lamp?.lamp_on);
const history = computed(() => breakStatus.value.history || []);
const isoHint = computed(() => {
  const d = new Date(Date.now() + 5 * 60 * 1000);
  return d.toISOString().slice(0, 16);
});
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmtTime(t) {
  if (!t) return "—";
  try { return new Date(t).toLocaleString("zh-CN", { hour12: false }); }
  catch { return t; }
}
async function refreshLogs() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshBreak() {
  if (!session.value) return;
  const res = await fetch("/api/break/status", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (!res.ok) return;
  const data = await res.json();
  breakStatus.value = data;
  if (data.config) cfg.value = {
    window_start: data.config.window_start,
    window_end: data.config.window_end,
    recent_minutes: data.config.recent_minutes,
    min_completed: data.config.min_completed,
  };
}
async function refreshAll() {
  await refreshBreak();
  if (page.value === "scan") await refreshLogs();
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
    await refreshAll();
    timer = setInterval(refreshAll, 2500);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  breakStatus.value = { config: null, lamp: null, history: [] };
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
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveConfig() {
  cfgMsg.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/break/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        window_start: cfg.value.window_start,
        window_end: cfg.value.window_end,
        recent_minutes: Number(cfg.value.recent_minutes),
        min_completed: Number(cfg.value.min_completed),
      }),
    });
    const data = await res.json();
    if (!res.ok) { cfgOk.value = false; cfgMsg.value = data.detail || "保存失败"; return; }
    breakStatus.value = { config: data.config, lamp: data.lamp, history: breakStatus.value.history };
    cfgOk.value = true;
    cfgMsg.value = "阈值已保存，判定链路已按新窗口重走。";
    await refreshBreak();
  } catch { cfgOk.value = false; cfgMsg.value = "保存时网络异常"; }
  finally { loading.value = false; }
}
async function clearLogs() {
  cfgMsg.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", { method: "DELETE", headers: headers() });
    const data = await res.json();
    if (!res.ok) { cfgOk.value = false; cfgMsg.value = data.detail || "清空失败"; return; }
    cfgOk.value = true;
    cfgMsg.value = `已清空 ${data.deleted ?? 0} 条办结记录，灯链路已重判。`;
    await refreshAll();
  } catch { cfgOk.value = false; cfgMsg.value = "清空时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2500);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 980px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0; font-size: 1.35rem; }
h2 { color: #bbf7d0; font-size: 1.05rem; margin: 0 0 0.75rem; }
.topbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; }
.nav { display: flex; gap: 0.5rem; }
.navbtn { position: relative; background: #14532d; border: 1px solid #166534; }
.navbtn.active { background: #16a34a; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
input:disabled { opacity: 0.55; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.danger { background: #b45309; }
button.small { padding: 0.15rem 0.6rem; font-size: 0.8rem; }
.err { color: #fecaca; }
.oktext { color: #bbf7d0; }
.hint { color: #fde68a; font-size: 0.85rem; }
.readonly-hint { color: #fde68a; font-size: 0.85rem; margin-bottom: 0.75rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 1rem; }
.row { display: flex; align-items: center; }
.lamp { display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%; margin-left: 0.4rem; vertical-align: middle; }
.lamp.off { background: #4b5563; }
.lamp.lit { background: #facc15; box-shadow: 0 0 8px 2px #facc15; }
.lamp-panel { display: flex; gap: 1.25rem; align-items: flex-start; }
.lamp-big { width: 3.2rem; height: 3.2rem; border-radius: 50%; flex: 0 0 auto; margin-top: 0.2rem; }
.lamp-big.off { background: radial-gradient(circle at 35% 35%, #6b7280, #374151); }
.lamp-big.lit { background: radial-gradient(circle at 35% 35%, #fef08a, #facc15 60%, #ca8a04); box-shadow: 0 0 22px 6px rgba(250, 204, 21, 0.55); animation: pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 0%,100% { box-shadow: 0 0 14px 3px rgba(250,204,21,.45);} 50% { box-shadow: 0 0 26px 9px rgba(250,204,21,.7);} }
.lamp-state { font-size: 1.2rem; font-weight: 700; color: #fde68a; margin-bottom: 0.35rem; }
.lamp-reason { margin: 0 0 0.35rem; }
.lamp-meta { color: #a7f3d0; font-size: 0.88rem; margin: 0 0 0.35rem; }
.rule { color: #86efac; font-size: 0.82rem; margin: 0; }
</style>
