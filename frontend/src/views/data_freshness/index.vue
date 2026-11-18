<template>
  <section class="page" data-module="data_freshness">
    <header class="page-head">
      <div>
        <h2>数据新鲜度管理</h2>
        <p class="page-desc">维护新鲜度监控的输入、执行约束与结果记录，为数据新鲜度提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记新鲜度监控</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出数据新鲜度清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据新鲜度数据，可先登记新鲜度监控</td>
        </tr>
      </tbody>
    </table>

    <section class="alert-panel" data-testid="alert-panel">
      <header class="alert-head">
        <div>
          <h3>数据滞后告警</h3>
          <p class="page-desc">
            读取新鲜度监控与分区时间后计算滞后结果。默认只显示当前阈值命中的告警，可展开查看最新分区与期望刷新时间。
          </p>
        </div>
        <div class="page-actions">
          <label class="mode-picker">
            <span>计算依据</span>
            <select v-model="calcMode" data-testid="alert-mode" @change="runCheck">
              <option v-for="mode in calcModes" :key="mode" :value="mode">{{ mode }}</option>
            </select>
          </label>
          <button class="btn" data-testid="check-alerts" type="button" :disabled="checking" @click="runCheck">
            {{ checking ? '计算中…' : '重新计算滞后' }}
          </button>
          <button class="btn" data-testid="verify-alerts" type="button" :disabled="verifying" @click="verifyAlerts">
            {{ verifying ? '核对中…' : '核对并留档' }}
          </button>
          <button class="btn" data-testid="export-alerts" type="button" @click="exportAlerts">导出告警清单</button>
          <button class="btn ghost" data-testid="retry-pending" type="button" v-if="pendingTables.length" @click="retryPending">
            补写未完成（{{ pendingTables.length }}）
          </button>
        </div>
      </header>

      <form class="filter-bar" @submit.prevent="reloadAlerts">
        <label class="filter-item">
          <span>关键词</span>
          <input
            v-model="alertFilters.keyword"
            data-testid="alert-keyword"
            placeholder="按告警编号、数据表名检索"
            @keyup.enter="reloadAlerts"
          />
        </label>
        <label class="filter-item">
          <span>数据表</span>
          <select v-model="alertFilters.data_table" data-testid="alert-table" @change="reloadAlerts">
            <option value="">全部数据表</option>
            <option v-for="table in alertOptions.数据表名" :key="table" :value="table">{{ table }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>告警阈值</span>
          <select v-model="alertFilters.threshold" data-testid="alert-threshold" @change="reloadAlerts">
            <option value="">全部阈值</option>
            <option v-for="value in alertOptions.告警阈值" :key="value" :value="String(value)">{{ value }} 分钟</option>
          </select>
        </label>
        <button class="btn" data-testid="alert-query" type="submit">查询</button>
        <button class="btn" data-testid="alert-locate" type="button" @click="locateAlert">定位记录</button>
        <button class="btn ghost" data-testid="alert-reset" type="button" @click="resetAlertFilters">重置条件</button>
      </form>

      <p class="alert-hint" data-testid="alert-hint" v-if="lastCheckMessage">{{ lastCheckMessage }}</p>
      <p class="alert-hint" data-testid="alert-locate-hint" v-if="locateMessage">{{ locateMessage }}</p>

      <div v-if="verifyResult" class="verify-panel" data-testid="verify-panel">
        <ul class="verify-list">
          <li
            v-for="check in verifyResult.checks"
            :key="check.名称"
            :class="check.ok ? 'verify-ok' : 'verify-bad'"
            :data-testid="`verify-${check.名称}`"
          >
            <strong>{{ check.名称 }}</strong>
            <span>{{ check.ok ? '通过' : '未通过' }}</span>
            <span v-if="check.说明" class="verify-desc">{{ check.说明 }}</span>
          </li>
        </ul>
        <p class="alert-hint" data-testid="verify-archive">
          留档：告警 {{ verifyResult.留档.告警数 }} 条，检查历史 {{ verifyResult.留档.历史数 }} 条
        </p>
      </div>

      <table class="data-table alert-table">
        <thead>
          <tr>
            <th>告警编号</th>
            <th>数据表</th>
            <th>指标编号</th>
            <th>最新分区</th>
            <th>滞后时长</th>
            <th>告警阈值</th>
            <th>告警状态</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="alert in alerts" :key="String(alert.id)">
            <tr
              class="alert-row"
              :data-testid="`alert-row-${alert.id}`"
              :data-table="String(alert.数据表名)"
              :data-threshold="String(alert.告警阈值)"
              :data-status="String(alert.告警状态)"
            >
              <td>{{ alert.告警编号 }}</td>
              <td>{{ alert.数据表名 }}</td>
              <td>{{ alert.指标编号 || '—' }}</td>
              <td>{{ alert.最新分区 ?? '—' }}</td>
              <td>{{ alert.滞后说明 }}</td>
              <td>{{ alert.告警阈值 }} 分钟</td>
              <td>{{ alert.告警状态 }}</td>
              <td>
                <button class="link" type="button" :data-testid="`alert-toggle-${alert.id}`" @click="toggleAlert(alert)">
                  {{ activeAlertId === alert.id ? '收起' : '展开' }}
                </button>
              </td>
            </tr>
            <tr v-if="activeAlertId === alert.id" class="alert-detail-row" :data-testid="`alert-detail-${alert.id}`">
              <td colspan="8">
                <dl class="alert-detail">
                  <div><dt>告警编号</dt><dd>{{ alertDetail?.告警编号 ?? alert.告警编号 }}</dd></div>
                  <div><dt>监控编号</dt><dd>{{ alertDetail?.监控编号 ?? alert.监控编号 }}</dd></div>
                  <div><dt>数据表</dt><dd>{{ alertDetail?.数据表名 ?? alert.数据表名 }}</dd></div>
                  <div><dt>最新分区</dt><dd>{{ alertDetail?.最新分区 ?? '—' }}</dd></div>
                  <div><dt>分区时间</dt><dd>{{ alertDetail?.分区时间 ?? '—' }}</dd></div>
                  <div><dt>期望刷新</dt><dd>{{ alertDetail?.期望刷新 ?? '—' }}</dd></div>
                  <div><dt>期望刷新时间</dt><dd>{{ alertDetail?.期望刷新时间 ?? '—' }}</dd></div>
                  <div><dt>滞后时长</dt><dd>{{ alertDetail?.滞后说明 ?? alert.滞后说明 }}</dd></div>
                  <div><dt>告警阈值</dt><dd>{{ alertDetail?.告警阈值 ?? alert.告警阈值 }} 分钟</dd></div>
                  <div><dt>计算依据</dt><dd>{{ alertDetail?.计算模式 ?? alert.计算模式 }}</dd></div>
                  <div><dt>判定说明</dt><dd>{{ alertDetail?.判定说明 ?? '—' }}</dd></div>
                  <div><dt>指标口径</dt><dd>{{ metricPoint(alertDetail ?? alert) }}</dd></div>
                </dl>
              </td>
            </tr>
          </template>
          <tr v-if="!alerts.length">
            <td colspan="8" class="empty-state">当前筛选条件下没有命中的数据滞后告警</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ alertTotal }} 条数据滞后告警</span>
        <span v-if="alertError" class="error-text">{{ alertError }}</span>
      </footer>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记新鲜度监控</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据新鲜度记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type AlertRow = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/data_freshness'
const ALERTS = `${ENDPOINT}/alerts`
const columns = ["监控编号", "数据表名", "最新分区", "期望刷新", "滞后时长", "告警阈值", "检查间隔", "通知方式"]
const actions = ["提交校验", "标记生效", "停用监控"]
const statuses = ["草稿", "待校验", "已生效", "已停用"]
const metricNames = ["新鲜度监控总数", "待校验监控", "已生效监控"]
const calcModes = ["最新分区", "业务事件"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

const alerts = ref<AlertRow[]>([])
const alertTotal = ref(0)
const alertError = ref('')
const alertFilters = ref<Record<string, string>>({ keyword: '', data_table: '', threshold: '' })
const alertOptions = ref<Record<string, (string | number)[]>>({ 数据表名: [], 告警阈值: [] })
const calcMode = ref(calcModes[0])
const checking = ref(false)
const lastCheckMessage = ref('')
const locateMessage = ref('')
const pendingTables = ref<string[]>([])
const verifying = ref(false)
const verifyResult = ref<{
  ok: boolean
  checks: { 名称: string; ok: boolean; 说明?: string }[]
  留档: { 告警数: number; 历史数: number }
} | null>(null)
const activeAlertId = ref<number | null>(null)
const alertDetail = ref<AlertRow | null>(null)

function metricPoint(row: AlertRow): string {
  const granularity = String(row.统计粒度 ?? '').trim()
  const window = String(row.时间窗口 ?? '').trim()
  if (!granularity && !window) return '—'
  return [granularity && `粒度 ${granularity}`, window && `窗口 ${window}`].filter(Boolean).join('，')
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'data_freshness.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据新鲜度动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据新鲜度操作失败'
  }
}

function alertQuery(): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(alertFilters.value)) {
    if (String(value).trim()) params.set(key, String(value).trim())
  }
  // 列表只看当前计算依据下的快照，避免把另一种口径的滞后当成当前状态。
  params.set('mode', calcMode.value)
  return params.toString()
}

function resetAlertFilters() {
  alertFilters.value = { keyword: '', data_table: '', threshold: '' }
  activeAlertId.value = null
  alertDetail.value = null
  void reloadAlerts()
}

async function loadAlertOptions() {
  try {
    const response = await request(`${ALERTS}/options`)
    if (!response.ok) return
    const payload = await response.json()
    alertOptions.value = {
      数据表名: payload.数据表名 ?? [],
      告警阈值: payload.告警阈值 ?? [],
    }
  } catch {
    /* 下拉选项失败不阻塞列表本身 */
  }
}

async function reloadAlerts() {
  alertError.value = ''
  try {
    const query = alertQuery()
    const response = await request(`${ALERTS}${query ? `?${query}` : ''}`)
    if (!response.ok) throw new Error('数据滞后告警列表读取失败')
    const payload = await response.json()
    alerts.value = payload.items ?? []
    alertTotal.value = payload.total ?? alerts.value.length
    if (activeAlertId.value !== null && !alerts.value.some((row) => row.id === activeAlertId.value)) {
      activeAlertId.value = null
      alertDetail.value = null
    }
    await loadAlertOptions()
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '数据滞后告警列表读取失败'
  }
}

async function locateAlert() {
  const keyword = String(alertFilters.value.keyword ?? '').trim()
  locateMessage.value = ''
  if (!keyword) {
    alertError.value = '请先输入要定位的告警编号、监控编号或数据表名'
    return
  }
  try {
    const response = await request(`${ALERTS}/locate?keyword=${encodeURIComponent(keyword)}`)
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '未找到匹配的告警记录')
    // 先按数据表 + 阈值把列表收敛到这条记录所在的范围，再展开同一条详情。
    alertFilters.value = {
      keyword: '',
      data_table: String(payload.数据表名 ?? ''),
      threshold: String(payload.告警阈值 ?? ''),
    }
    await reloadAlerts()
    const target = alerts.value.find((row) => Number(row.id) === Number(payload.id))
    if (!target) {
      alertError.value = '该记录已不在当前阈值命中范围内'
      return
    }
    locateMessage.value = `已定位到 ${payload.告警编号}（${payload.数据表名}）`
    await loadAlertDetail(target)
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '告警定位失败'
  }
}

async function toggleAlert(alert: AlertRow) {
  if (activeAlertId.value === alert.id) {
    activeAlertId.value = null
    alertDetail.value = null
    return
  }
  locateMessage.value = ''
  await loadAlertDetail(alert)
}

async function loadAlertDetail(alert: AlertRow) {
  activeAlertId.value = Number(alert.id)
  alertDetail.value = null
  try {
    const response = await request(`${ALERTS}/${alert.id}`)
    if (!response.ok) throw new Error('告警详情读取失败')
    alertDetail.value = await response.json()
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '告警详情读取失败'
  }
}

async function runCheck() {
  checking.value = true
  alertError.value = ''
  try {
    const response = await request(`${ALERTS}/check`, {
      method: 'POST',
      body: JSON.stringify({ mode: calcMode.value }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.message || '滞后计算失败')
    lastCheckMessage.value = payload.message ?? ''
    pendingTables.value = payload.unfinished ?? []
    await reloadAlerts()
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '滞后计算失败'
  } finally {
    checking.value = false
  }
}

async function retryPending() {
  checking.value = true
  alertError.value = ''
  try {
    const response = await request(`${ALERTS}/check`, {
      method: 'POST',
      body: JSON.stringify({ mode: calcMode.value, tables: pendingTables.value }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.message || '补写未完成数据表失败')
    lastCheckMessage.value = payload.message ?? ''
    pendingTables.value = payload.unfinished ?? []
    await reloadAlerts()
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '补写未完成数据表失败'
  } finally {
    checking.value = false
  }
}

async function verifyAlerts() {
  verifying.value = true
  alertError.value = ''
  try {
    const response = await request(`${ALERTS}/verify`)
    if (!response.ok) throw new Error('数据新鲜度核对失败')
    verifyResult.value = await response.json()
    await reloadAlerts()
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '数据新鲜度核对失败'
  } finally {
    verifying.value = false
  }
}

async function exportAlerts() {
  alertError.value = ''
  const query = alertQuery()
  try {
    const response = await request(`${ALERTS}/export${query ? `?${query}` : ''}`)
    if (!response.ok) throw new Error('告警导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'data_freshness_alerts.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    alertError.value = error instanceof Error ? error.message : '告警导出失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('新鲜度监控列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据新鲜度列表读取失败'
  }
}

onMounted(async () => {
  await reload()
  await reloadAlerts()
  // 首次进入且还没有任何告警快照时，读取监控与分区时间算一次，保证入口立即可用。
  if (!alertError.value && !alertOptions.value.数据表名?.length) {
    await runCheck()
  }
})
</script>
