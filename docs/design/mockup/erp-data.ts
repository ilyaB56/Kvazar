// ============================================================
// Мок-данные для макетов интерфейса ERP-системы «ТехноПром»
// ============================================================

export type OrderStatus = "new" | "processing" | "shipping" | "done" | "cancelled"
export type InvoiceStatus = "paid" | "pending" | "overdue" | "draft"
export type StockLevel = "ok" | "low" | "critical" | "overflow"

export interface KpiItem {
  id: string
  label: string
  value: string
  numValue: number
  decimals: number
  suffix: string
  delta: number
  hint: string
  icon: "revenue" | "orders" | "clients" | "warehouse"
}

export interface MonthPoint {
  month: string
  revenue: number
  costs: number
  profit: number
}

export interface CategoryPoint {
  name: string
  value: number
}

export interface OrderRow {
  id: string
  customer: string
  manager: string
  date: string
  amount: number
  items: number
  status: OrderStatus
  channel: "Онлайн" | "Телефон" | "Партнёр" | "Тендер"
}

export interface StockRow {
  sku: string
  name: string
  category: string
  warehouse: string
  qty: number
  minQty: number
  capacity: number
  price: number
  updated: string
}

export interface InvoiceRow {
  id: string
  counterparty: string
  date: string
  dueDate: string
  amount: number
  status: InvoiceStatus
}

export interface EmployeeRow {
  id: string
  name: string
  position: string
  department: string
  email: string
  phone: string
  salary: number
  status: "active" | "vacation" | "sick" | "remote"
  since: string
}

export interface ActivityItem {
  id: string
  user: string
  action: string
  target: string
  time: string
  kind: "order" | "stock" | "finance" | "hr" | "system"
}

export interface TaskItem {
  id: string
  title: string
  assignee: string
  due: string
  priority: "high" | "medium" | "low"
  done: boolean
}

export interface WarehouseStat {
  name: string
  fill: number
  pallets: number
  capacity: number
}

// ---------- KPI ----------
export const kpiData: KpiItem[] = [
  { id: "k1", label: "Выручка за месяц", value: "12,48 млн ₽", numValue: 12.48, decimals: 2, suffix: " млн ₽", delta: 8.4, hint: "к прошлому месяцу", icon: "revenue" },
  { id: "k2", label: "Новых заказов", value: "184", numValue: 184, decimals: 0, suffix: "", delta: 12.1, hint: "за 30 дней", icon: "orders" },
  { id: "k3", label: "Активных клиентов", value: "1 274", numValue: 1274, decimals: 0, suffix: "", delta: 3.2, hint: "LTV растёт", icon: "clients" },
  { id: "k4", label: "Оборачиваемость склада", value: "6,8 дн.", numValue: 6.8, decimals: 1, suffix: " дн.", delta: -4.5, hint: "быстрее, чем раньше", icon: "warehouse" },
]

// ---------- Выручка по месяцам ----------
export const revenueByMonth: MonthPoint[] = [
  { month: "Янв", revenue: 8200, costs: 5400, profit: 2800 },
  { month: "Фев", revenue: 9100, costs: 5900, profit: 3200 },
  { month: "Мар", revenue: 10400, costs: 6300, profit: 4100 },
  { month: "Апр", revenue: 9800, costs: 6100, profit: 3700 },
  { month: "Май", revenue: 11200, costs: 6800, profit: 4400 },
  { month: "Июн", revenue: 12100, costs: 7100, profit: 5000 },
  { month: "Июл", revenue: 11700, costs: 7000, profit: 4700 },
  { month: "Авг", revenue: 12600, costs: 7400, profit: 5200 },
  { month: "Сен", revenue: 13400, costs: 7900, profit: 5500 },
  { month: "Окт", revenue: 12900, costs: 7600, profit: 5300 },
  { month: "Ноя", revenue: 14100, costs: 8200, profit: 5900 },
  { month: "Дек", revenue: 12480, costs: 7300, profit: 5180 },
]

// ---------- Структура продаж по категориям ----------
export const salesByCategory: CategoryPoint[] = [
  { name: "Станки ЧПУ", value: 38 },
  { name: "Комплектующие", value: 24 },
  { name: "Инструмент", value: 18 },
  { name: "Автоматика", value: 12 },
  { name: "Сервис и ТО", value: 8 },
]

// ---------- Заказы ----------
export const ordersData: OrderRow[] = [
  { id: "ЗК-2418", customer: "ООО «Машинострой»", manager: "Соколова А.", date: "18.12.2025", amount: 1_240_000, items: 14, status: "processing", channel: "Онлайн" },
  { id: "ЗК-2417", customer: "АО «УралМет»", manager: "Гордеев И.", date: "18.12.2025", amount: 862_500, items: 6, status: "new", channel: "Тендер" },
  { id: "ЗК-2416", customer: "ИП Ветров П.С.", manager: "Соколова А.", date: "17.12.2025", amount: 118_900, items: 3, status: "shipping", channel: "Телефон" },
  { id: "ЗК-2415", customer: "ООО «АгроТехСервис»", manager: "Ким Д.Е.", date: "17.12.2025", amount: 2_780_000, items: 22, status: "processing", channel: "Партнёр" },
  { id: "ЗК-2414", customer: "ООО «ПромРемонт»", manager: "Гордеев И.", date: "16.12.2025", amount: 452_300, items: 9, status: "done", channel: "Онлайн" },
  { id: "ЗК-2413", customer: "ЗАО «ХимЛаб»", manager: "Ким Д.Е.", date: "16.12.2025", amount: 96_400, items: 2, status: "cancelled", channel: "Телефон" },
  { id: "ЗК-2412", customer: "ООО «СтанкоДеталь»", manager: "Ларина М.", date: "15.12.2025", amount: 1_690_700, items: 17, status: "done", channel: "Онлайн" },
  { id: "ЗК-2411", customer: "АО «Северный Ветер»", manager: "Ларина М.", date: "15.12.2025", amount: 318_000, items: 5, status: "shipping", channel: "Партнёр" },
  { id: "ЗК-2410", customer: "ООО «ГидроАгрегат»", manager: "Соколова А.", date: "14.12.2025", amount: 745_200, items: 8, status: "new", channel: "Тендер" },
  { id: "ЗК-2409", customer: "ООО «ЭлектроПром»", manager: "Ким Д.Е.", date: "13.12.2025", amount: 58_300, items: 1, status: "done", channel: "Онлайн" },
  { id: "ЗК-2408", customer: "ИП Сомов А.В.", manager: "Гордеев И.", date: "12.12.2025", amount: 271_400, items: 7, status: "processing", channel: "Телефон" },
  { id: "ЗК-2407", customer: "ООО «ТеплоМаш»", manager: "Ларина М.", date: "12.12.2025", amount: 1_050_000, items: 11, status: "done", channel: "Партнёр" },
]

// ---------- Склад ----------
export const stockData: StockRow[] = [
  { sku: "ST-001204", name: "Станок ЧПУ VF-4L", category: "Станки ЧПУ", warehouse: "Склад А (основной)", qty: 4, minQty: 3, capacity: 12, price: 2_450_000, updated: "18.12, 09:41" },
  { sku: "KP-003311", name: "Шпиндель 12 кВт (вода)", category: "Комплектующие", warehouse: "Склад А (основной)", qty: 22, minQty: 10, capacity: 40, price: 184_500, updated: "18.12, 08:15" },
  { sku: "IN-000947", name: "Фреза концевая D12, TiAlN", category: "Инструмент", warehouse: "Склад Б (регион)", qty: 156, minQty: 80, capacity: 300, price: 3_990, updated: "17.12, 17:03" },
  { sku: "AU-002188", name: "Контроллер Siemens S7-1200", category: "Автоматика", warehouse: "Склад А (основной)", qty: 6, minQty: 8, capacity: 25, price: 96_700, updated: "17.12, 16:22" },
  { sku: "KP-003352", name: "Линейные направляющие HIWIN 45", category: "Комплектующие", warehouse: "Склад Б (регион)", qty: 14, minQty: 15, capacity: 60, price: 24_800, updated: "17.12, 11:47" },
  { sku: "ST-001310", name: "Токарный станок CK-6150", category: "Станки ЧПУ", warehouse: "Склад В (транзит)", qty: 2, minQty: 1, capacity: 6, price: 3_780_000, updated: "16.12, 19:05" },
  { sku: "IN-001022", name: "Патрон 3-кулачковый Ø250", category: "Инструмент", warehouse: "Склад А (основной)", qty: 48, minQty: 20, capacity: 80, price: 27_300, updated: "16.12, 14:31" },
  { sku: "AU-002240", name: "Частотный преобразователь 15 кВт", category: "Автоматика", warehouse: "Склад Б (регион)", qty: 3, minQty: 6, capacity: 20, price: 78_400, updated: "16.12, 10:58" },
  { sku: "KP-003410", name: "Шарико-винтовая пара 4020", category: "Комплектующие", warehouse: "Склад А (основной)", qty: 31, minQty: 12, capacity: 50, price: 52_100, updated: "15.12, 18:44" },
  { sku: "SR-000078", name: "Плановое ТО (годовое)", category: "Сервис и ТО", warehouse: "—", qty: 999, minQty: 0, capacity: 999, price: 145_000, updated: "15.12, 12:00" },
]

export const warehouseStats: WarehouseStat[] = [
  { name: "Склад А (основной)", fill: 68, pallets: 680, capacity: 1000 },
  { name: "Склад Б (регион)", fill: 41, pallets: 205, capacity: 500 },
  { name: "Склад В (транзит)", fill: 87, pallets: 348, capacity: 400 },
]

// ---------- Финансы: счета ----------
export const invoicesData: InvoiceRow[] = [
  { id: "СЧ-10542", counterparty: "ООО «Машинострой»", date: "16.12.2025", dueDate: "30.12.2025", amount: 1_240_000, status: "pending" },
  { id: "СЧ-10541", counterparty: "АО «УралМет»", date: "16.12.2025", dueDate: "31.12.2025", amount: 862_500, status: "pending" },
  { id: "СЧ-10540", counterparty: "ООО «СтанкоДеталь»", date: "15.12.2025", dueDate: "29.12.2025", amount: 1_690_700, status: "paid" },
  { id: "СЧ-10539", counterparty: "ИП Ветров П.С.", date: "15.12.2025", dueDate: "22.12.2025", amount: 118_900, status: "paid" },
  { id: "СЧ-10538", counterparty: "ЗАО «ХимЛаб»", date: "10.12.2025", dueDate: "12.12.2025", amount: 96_400, status: "overdue" },
  { id: "СЧ-10537", counterparty: "ООО «АгроТехСервис»", date: "09.12.2025", dueDate: "23.12.2025", amount: 2_780_000, status: "pending" },
  { id: "СЧ-10536", counterparty: "АО «Северный Ветер»", date: "08.12.2025", dueDate: "22.12.2025", amount: 318_000, status: "paid" },
  { id: "СЧ-10535", counterparty: "ООО «ПромРемонт»", date: "05.12.2025", dueDate: "19.12.2025", amount: 452_300, status: "paid" },
  { id: "СЧ-10534", counterparty: "ООО «ГидроАгрегат»", date: "02.12.2025", dueDate: "16.12.2025", amount: 745_200, status: "draft" },
]

// ---------- Финансы: движение денег ----------
export const cashflowData = [
  { month: "Июл", in: 11700, out: 8900 },
  { month: "Авг", in: 12600, out: 9400 },
  { month: "Сен", in: 13400, out: 10100 },
  { month: "Окт", in: 12900, out: 9800 },
  { month: "Ноя", in: 14100, out: 10600 },
  { month: "Дек", in: 12480, out: 9100 },
]

export const expenseStructure: CategoryPoint[] = [
  { name: "Закупки", value: 52 },
  { name: "ФОТ", value: 22 },
  { name: "Логистика", value: 11 },
  { name: "Маркетинг", value: 8 },
  { name: "Прочее", value: 7 },
]

// ---------- Персонал ----------
export const employeesData: EmployeeRow[] = [
  { id: "EMP-001", name: "Соколова Анна", position: "Руководитель отдела продаж", department: "Продажи", email: "a.sokolova@technoprom.ru", phone: "+7 900 112-33-44", salary: 180_000, status: "active", since: "12.03.2019" },
  { id: "EMP-002", name: "Гордеев Игорь", position: "Старший менеджер", department: "Продажи", email: "i.gordeev@technoprom.ru", phone: "+7 900 221-55-66", salary: 140_000, status: "remote", since: "04.08.2020" },
  { id: "EMP-003", name: "Ким Дмитрий", position: "Менеджер по тендерам", department: "Продажи", email: "d.kim@technoprom.ru", phone: "+7 900 331-77-88", salary: 120_000, status: "vacation", since: "17.01.2021" },
  { id: "EMP-004", name: "Ларина Мария", position: "Менеджер по продажам", department: "Продажи", email: "m.larina@technoprom.ru", phone: "+7 900 441-99-00", salary: 110_000, status: "active", since: "22.09.2022" },
  { id: "EMP-005", name: "Тихонов Павел", position: "Начальник склада", department: "Логистика", email: "p.tihonov@technoprom.ru", phone: "+7 900 552-11-22", salary: 130_000, status: "active", since: "01.06.2018" },
  { id: "EMP-006", name: "Ершова Ольга", position: "Кладовщик", department: "Логистика", email: "o.ershova@technoprom.ru", phone: "+7 900 662-33-44", salary: 85_000, status: "sick", since: "14.02.2023" },
  { id: "EMP-007", name: "Морозов Сергей", position: "Главный бухгалтер", department: "Финансы", email: "s.morozov@technoprom.ru", phone: "+7 900 772-55-66", salary: 190_000, status: "active", since: "05.04.2017" },
  { id: "EMP-008", name: "Зайцева Ксения", position: "Бухгалтер по расчётам", department: "Финансы", email: "k.zayceva@technoprom.ru", phone: "+7 900 882-77-88", salary: 95_000, status: "remote", since: "11.11.2021" },
]

export const departments = [
  { name: "Продажи", head: "Соколова А.", headcount: 14, plan: 16, color: "bg-emerald-500" },
  { name: "Логистика", head: "Тихонов П.", headcount: 9, plan: 10, color: "bg-teal-500" },
  { name: "Финансы", head: "Морозов С.", headcount: 6, plan: 6, color: "bg-amber-500" },
  { name: "Производство", head: "Науменко В.", headcount: 22, plan: 26, color: "bg-orange-500" },
  { name: "IT и разработка", head: "Белова Е.", headcount: 7, plan: 8, color: "bg-lime-600" },
]

export const attendance = [
  { label: "На работе", value: 48, tone: "text-emerald-600" },
  { label: "Удалённо", value: 6, tone: "text-teal-600" },
  { label: "Отпуск", value: 4, tone: "text-amber-600" },
  { label: "Больничный", value: 2, tone: "text-red-500" },
]

// ---------- Лента активности ----------
export const activityData: ActivityItem[] = [
  { id: "a1", user: "Соколова А.", action: "создала заказ", target: "ЗК-2418 · ООО «Машинострой»", time: "10 мин назад", kind: "order" },
  { id: "a2", user: "Тихонов П.", action: "подтвердил приход", target: "Шпиндель 12 кВт · 20 шт.", time: "42 мин назад", kind: "stock" },
  { id: "a3", user: "Морозов С.", action: "оплатил счёт", target: "СЧ-10540 · 1 690 700 ₽", time: "1 ч назад", kind: "finance" },
  { id: "a4", user: "Система", action: "предупреждение: низкий остаток", target: "Контроллер Siemens S7-1200", time: "2 ч назад", kind: "system" },
  { id: "a5", user: "Отдел кадров", action: "одобрил отпуск", target: "Ким Д. · 22–28.12", time: "3 ч назад", kind: "hr" },
  { id: "a6", user: "Гордеев И.", action: "выставил счёт", target: "СЧ-10541 · АО «УралМет»", time: "4 ч назад", kind: "finance" },
]

// ---------- Задачи ----------
export const tasksData: TaskItem[] = [
  { id: "t1", title: "Согласовать скидку 7% для «УралМет»", assignee: "Соколова А.", due: "сегодня", priority: "high", done: false },
  { id: "t2", title: "Заказать 10 шт. контроллеров S7-1200", assignee: "Тихонов П.", due: "сегодня", priority: "high", done: false },
  { id: "t3", title: "Погасить просрочку по СЧ-10538", assignee: "Морозов С.", due: "завтра", priority: "high", done: false },
  { id: "t4", title: "Инвентаризация склада В", assignee: "Ершова О.", due: "20.12", priority: "medium", done: false },
  { id: "t5", title: "Обновить прайс на 2026 год", assignee: "Ларина М.", due: "22.12", priority: "medium", done: false },
  { id: "t6", title: "Отправить акт сверки «ХимЛаб»", assignee: "Зайцева К.", due: "23.12", priority: "low", done: true },
]

// ---------- Отчёты ----------
export const reportLibrary = [
  { id: "r1", name: "P&L (прибыли и убытки)", period: "Ежемесячно", updated: "01.12.2025", tag: "Финансы" },
  { id: "r2", name: "Дебиторская задолженность", period: "Еженедельно", updated: "16.12.2025", tag: "Финансы" },
  { id: "r3", name: "ABC-анализ номенклатуры", period: "Ежеквартально", updated: "30.09.2025", tag: "Склад" },
  { id: "r4", name: "Воронка продаж менеджеров", period: "Еженедельно", updated: "16.12.2025", tag: "Продажи" },
  { id: "r5", name: "Текучесть персонала", period: "Ежегодно", updated: "15.01.2025", tag: "HR" },
  { id: "r6", name: "Себестоимость по заказам", period: "Ежемесячно", updated: "01.12.2025", tag: "Производство" },
]

export const managerSales = [
  { name: "Соколова", plan: 4200, fact: 4680 },
  { name: "Гордеев", plan: 3600, fact: 3120 },
  { name: "Ким", plan: 2800, fact: 2950 },
  { name: "Ларина", plan: 2400, fact: 2140 },
]

// ---------- Позиции заказа (для карточки заказа) ----------
export interface OrderItemRow {
  sku: string
  name: string
  qty: number
  price: number
}

export function getOrderItems(order: OrderRow): OrderItemRow[] {
  const pool: OrderItemRow[] = [
    { sku: 'ST-001204', name: 'Станок ЧПУ VF-4L', qty: 1, price: 2_450_000 },
    { sku: 'KP-003311', name: 'Шпиндель 12 кВт (вода)', qty: 2, price: 184_500 },
    { sku: 'IN-000947', name: 'Фреза концевая D12, TiAlN', qty: 12, price: 3_990 },
    { sku: 'AU-002188', name: 'Контроллер Siemens S7-1200', qty: 3, price: 96_700 },
    { sku: 'KP-003352', name: 'Линейные направляющие HIWIN 45', qty: 4, price: 24_800 },
    { sku: 'IN-001022', name: 'Патрон 3-кулачковый Ø250', qty: 2, price: 27_300 },
    { sku: 'AU-002240', name: 'Частотный преобразователь 15 кВт', qty: 1, price: 78_400 },
    { sku: 'KP-003410', name: 'Шарико-винтовая пара 4020', qty: 3, price: 52_100 },
  ]
  // детерминированный «хеш» по номеру заказа — позиции стабильны между рендерами
  const seed = order.id.split('').reduce((s, c) => s + c.charCodeAt(0), 0)
  const count = Math.max(2, Math.min(order.items, 5))
  const items: OrderItemRow[] = []
  for (let i = 0; i < count; i++) {
    const it = pool[(seed + i * 3) % pool.length]
    if (!items.find((x) => x.sku === it.sku)) items.push({ ...it, qty: it.qty + (i % 2) })
  }
  return items
}

export const orderTimelineSteps: { key: OrderStatus[]; label: string; desc: string }[] = [
  { key: ['new', 'processing', 'shipping', 'done'], label: 'Заказ создан', desc: 'Проверка наличия и счёт' },
  { key: ['processing', 'shipping', 'done'], label: 'Оплата подтверждена', desc: 'Резервирование товара' },
  { key: ['shipping', 'done'], label: 'Комплектация и отгрузка', desc: 'Экспедитор назначен' },
  { key: ['done'], label: 'Доставлено', desc: 'Подписан УПД' },
]

// ---------- Карточка сотрудника ----------
export interface EmployeeProfile extends EmployeeRow {
  birthDate: string
  city: string
  vacationLeft: number
  performance: number // % выполнения KPI
  dealsClosed: number
  achievements: string[]
  documents: string[]
}

const employeeExtras: Record<string, { city: string; birthDate: string; performance: number; dealsClosed: number; achievements: string[] }> = {
  'EMP-001': { city: 'Екатеринбург', birthDate: '14.05.1988', performance: 112, dealsClosed: 34, achievements: ['Лучший продавец 2024', 'Наставник года'] },
  'EMP-002': { city: 'Челябинск', birthDate: '02.11.1991', performance: 87, dealsClosed: 21, achievements: ['Рост базы +18%'] },
  'EMP-003': { city: 'Екатеринбург', birthDate: '27.07.1993', performance: 105, dealsClosed: 9, achievements: ['3 тендера выиграно'] },
  'EMP-004': { city: 'Пермь', birthDate: '09.03.1996', performance: 92, dealsClosed: 17, achievements: ['Стаж 3 года'] },
  'EMP-005': { city: 'Екатеринбург', birthDate: '30.01.1985', performance: 98, dealsClosed: 0, achievements: ['0 потерь при инвентаризации'] },
  'EMP-006': { city: 'Екатеринбург', birthDate: '16.09.1994', performance: 94, dealsClosed: 0, achievements: ['Скорость сборки +12%'] },
  'EMP-007': { city: 'Екатеринбург', birthDate: '21.12.1982', performance: 100, dealsClosed: 0, achievements: ['Закрытие года без замечаний'] },
  'EMP-008': { city: 'Тюмень', birthDate: '05.06.1997', performance: 96, dealsClosed: 0, achievements: ['Автоматизация сверки'] },
}

export function getEmployeeProfile(e: EmployeeRow): EmployeeProfile {
  return {
    ...e,
    birthDate: employeeExtras[e.id]?.birthDate ?? '—',
    city: employeeExtras[e.id]?.city ?? '—',
    performance: employeeExtras[e.id]?.performance ?? 90,
    dealsClosed: employeeExtras[e.id]?.dealsClosed ?? 0,
    achievements: employeeExtras[e.id]?.achievements ?? [],
    vacationLeft: 14 + (e.id.charCodeAt(4) % 14),
    documents: ['Трудовой договор', 'Соглашение о матответственности', 'Аттестация по ОТ'],
  }
}

// ---------- Канбан задач: колонки ----------
export type TaskColumn = 'todo' | 'doing' | 'done'

export interface KanbanTask {
  id: string
  title: string
  assignee: string
  due: string
  priority: 'high' | 'medium' | 'low'
  column: TaskColumn
}

export const kanbanSeed: KanbanTask[] = [
  { id: 't1', title: 'Согласовать скидку 7% для «УралМет»', assignee: 'Соколова А.', due: 'сегодня', priority: 'high', column: 'todo' },
  { id: 't2', title: 'Заказать 10 шт. контроллеров S7-1200', assignee: 'Тихонов П.', due: 'сегодня', priority: 'high', column: 'todo' },
  { id: 't3', title: 'Обновить прайс на 2026 год', assignee: 'Ларина М.', due: '22.12', priority: 'medium', column: 'todo' },
  { id: 't4', title: 'Погасить просрочку по СЧ-10538', assignee: 'Морозов С.', due: 'завтра', priority: 'high', column: 'doing' },
  { id: 't5', title: 'Инвентаризация склада В', assignee: 'Ершова О.', due: '20.12', priority: 'medium', column: 'doing' },
  { id: 't6', title: 'Отправить акт сверки «ХимЛаб»', assignee: 'Зайцева К.', due: '23.12', priority: 'low', column: 'done' },
  { id: 't7', title: 'Подготовить отчёт P&L за декабрь', assignee: 'Морозов С.', due: '25.12', priority: 'low', column: 'done' },
]

// ---------- Карточка товара на складе ----------
export type MovementKind = "in" | "out" | "move" | "writeoff"

export interface StockMovement {
  id: string
  date: string
  kind: MovementKind
  qty: number
  doc: string
  actor: string
}

export interface StockItemDetails {
  supplier: string
  leadDays: number
  reserved: number
  incoming: number
  shelf: string
  movements: StockMovement[]
  locations: { warehouse: string; qty: number }[]
}

const movementDocByKind: Record<MovementKind, (seed: number) => string> = {
  in: (s) => `ПН-${4100 + (s % 40)}`,
  out: (s) => `РН-${2200 + (s % 30)}`,
  move: (s) => `ПМ-${700 + (s % 25)}`,
  writeoff: (s) => `АС-${120 + (s % 15)}`,
}

const movementActorByKind: Record<MovementKind, string> = {
  in: "Тихонов П.",
  out: "Ершова О.",
  move: "Тихонов П.",
  writeoff: "Морозов С.",
}

export function getStockItemDetails(s: StockRow): StockItemDetails {
  const seed = s.sku.split("").reduce((acc, c) => acc + c.charCodeAt(0), 0)
  const reserved = Math.max(0, Math.round(s.qty * (0.08 + (seed % 5) * 0.04)))
  const incoming = [0, 4, 8, 12, 20][seed % 5]
  const leadDays = 3 + (seed % 12)
  const suppliersByCategory: Record<string, string> = {
    "Станки ЧПУ": "Haas Robotics GmbH",
    "Комплектующие": "ПО «Уралкомплект»",
    "Инструмент": "Guhring RT",
    "Автоматика": "Siemens AG",
    "Сервис и ТО": "Сервисный центр ТехноПром",
  }
  const kindCycle: MovementKind[] = ["out", "in", "move", "out", "in", "writeoff"]
  const movements: StockMovement[] = [0, 1, 2, 3].map((i) => {
    const kind = kindCycle[(seed + i * 2) % kindCycle.length]
    const day = 18 - i * 2
    const qty = Math.max(1, ((seed * (i + 3)) % 7) + 1)
    return {
      id: `${s.sku}-m${i}`,
      date: `${String(day).padStart(2, "0")}.12`,
      kind,
      qty,
      doc: movementDocByKind[kind](seed + i),
      actor: movementActorByKind[kind],
    }
  })
  const secondaryWarehouse =
    s.warehouse === "Склад Б (регион)" ? "Склад А (основной)" : "Склад Б (регион)"
  const locations =
    s.warehouse === "—" || s.qty >= 999
      ? [{ warehouse: "Услуга (без склада)", qty: s.qty }]
      : [
          { warehouse: s.warehouse, qty: Math.max(0, s.qty - Math.round(s.qty * 0.18)) },
          { warehouse: secondaryWarehouse, qty: Math.round(s.qty * 0.18) },
        ].filter((l) => l.qty > 0)
  return {
    supplier: suppliersByCategory[s.category] ?? "—",
    leadDays,
    reserved,
    incoming,
    shelf: `${["A", "B", "C", "D"][seed % 4]}-${String((seed % 24) + 1).padStart(2, "0")}`,
    movements,
    locations,
  }
}

export const movementKindMap: Record<MovementKind, { label: string; cls: string }> = {
  in: { label: "Приход", cls: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-300" },
  out: { label: "Расход", cls: "border-orange-200 bg-orange-50 text-orange-700 dark:border-orange-900 dark:bg-orange-950/60 dark:text-orange-300" },
  move: { label: "Перемещение", cls: "border-teal-200 bg-teal-50 text-teal-700 dark:border-teal-900 dark:bg-teal-950/60 dark:text-teal-300" },
  writeoff: { label: "Списание", cls: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/60 dark:text-red-300" },
}

// ---------- Конструктор отчётов ----------
export type ReportDatasetId = "sales" | "stock" | "finance" | "hr"

export interface ReportDataset {
  id: ReportDatasetId
  name: string
  desc: string
  fields: { key: string; label: string; money?: boolean }[]
}

export const reportDatasets: ReportDataset[] = [
  {
    id: "sales",
    name: "Продажи",
    desc: "Заказы клиентов за период",
    fields: [
      { key: "id", label: "Номер" },
      { key: "customer", label: "Клиент" },
      { key: "manager", label: "Менеджер" },
      { key: "date", label: "Дата" },
      { key: "amount", label: "Сумма", money: true },
      { key: "status", label: "Статус" },
    ],
  },
  {
    id: "stock",
    name: "Склад",
    desc: "Остатки и стоимость запасов",
    fields: [
      { key: "sku", label: "Артикул" },
      { key: "name", label: "Наименование" },
      { key: "warehouse", label: "Склад" },
      { key: "qty", label: "Остаток" },
      { key: "price", label: "Цена", money: true },
    ],
  },
  {
    id: "finance",
    name: "Финансы",
    desc: "Счета и оплата",
    fields: [
      { key: "id", label: "Счёт" },
      { key: "counterparty", label: "Контрагент" },
      { key: "date", label: "Дата" },
      { key: "dueDate", label: "Оплатить до" },
      { key: "amount", label: "Сумма", money: true },
      { key: "status", label: "Статус" },
    ],
  },
  {
    id: "hr",
    name: "Персонал",
    desc: "Сотрудники и оклады",
    fields: [
      { key: "name", label: "Сотрудник" },
      { key: "department", label: "Отдел" },
      { key: "position", label: "Должность" },
      { key: "salary", label: "Оклад", money: true },
      { key: "status", label: "Статус" },
    ],
  },
]

export const reportPeriods = ["Декабрь 2025", "IV квартал 2025", "Год 2025"]

export function buildReportRows(datasetId: ReportDatasetId): Record<string, string | number>[] {
  switch (datasetId) {
    case "sales":
      return ordersData.slice(0, 5)
    case "stock":
      return stockData.slice(0, 5)
    case "finance":
      return invoicesData.slice(0, 5)
    case "hr":
      return employeesData.slice(0, 5)
  }
}

export const statusLabels: Record<string, string> = {
  new: "Новый",
  processing: "В работе",
  shipping: "Отгрузка",
  done: "Выполнен",
  cancelled: "Отменён",
  paid: "Оплачен",
  pending: "Ожидает",
  overdue: "Просрочен",
  draft: "Черновик",
  active: "Работает",
  vacation: "Отпуск",
  sick: "Больничный",
  remote: "Удалённо",
}

// ---------- Центр уведомлений ----------
export type NotificationKind = "danger" | "warning" | "success" | "info"

export interface ErpNotification {
  id: string
  kind: NotificationKind
  title: string
  description: string
  time: string
  view: "dashboard" | "sales" | "inventory" | "finance" | "hr"
  unread: boolean
}

export const notificationKindMap: Record<NotificationKind, { dot: string; iconCls: string; label: string }> = {
  danger: { dot: "bg-red-500", iconCls: "text-red-500", label: "Критично" },
  warning: { dot: "bg-amber-500", iconCls: "text-amber-500", label: "Внимание" },
  success: { dot: "bg-emerald-500", iconCls: "text-emerald-500", label: "Событие" },
  info: { dot: "bg-sky-500", iconCls: "text-sky-500", label: "Инфо" },
}

// ---------- Автогенерация уведомлений из данных ----------
// «Сегодня» демо-датасета — все даты макета фиксируются относительно неё
export const DEMO_TODAY = new Date(2025, 11, 18)

export const parseRuDate = (s: string): Date => {
  const [d, m, y] = s.split(".").map(Number)
  return new Date(y, (m || 1) - 1, d || 1)
}

export const daysBetween = (a: Date, b: Date): number =>
  Math.round((a.getTime() - b.getTime()) / 86_400_000)

// Русская плюрализация: pluralRu(6, "день", "дня", "дней") → «дней»
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few
  return many
}

// Уведомления собираются из реальных данных макета: просроченные счета,
// низкие остатки, переполненные склады + свежий заказ. Рукопашные события
// добавляются в конец. Список живой: правки данных меняют колокол.
export function buildNotifications(): ErpNotification[] {
  const list: ErpNotification[] = []

  // 1) Просроченные счета
  invoicesData
    .filter((inv) => inv.status === "overdue")
    .forEach((inv) => {
      const days = Math.max(0, daysBetween(DEMO_TODAY, parseRuDate(inv.dueDate)))
      list.push({
        id: `auto-inv-${inv.id}`,
        kind: "danger",
        title: `Просрочен счёт ${inv.id}`,
        description: `${inv.counterparty} · ${fmtMoney(inv.amount)} · просрочка ${days} ${pluralRu(days, "день", "дня", "дней")}`,
        time: "12 мин назад",
        view: "finance",
        unread: true,
      })
    })

  // 2) Низкие остатки — худшие по дефициту (qty/minQty)
  stockData
    .filter((s) => s.minQty > 0 && s.qty < s.minQty)
    .sort((a, b) => a.qty / a.minQty - b.qty / b.minQty)
    .slice(0, 2)
    .forEach((s) => {
      const deficit = s.minQty - s.qty
      list.push({
        id: `auto-stock-${s.sku}`,
        kind: deficit >= s.minQty / 2 ? "danger" : "warning",
        title: `Низкий остаток: ${s.name}`,
        description: `${s.sku} · не хватает ${deficit} ${pluralRu(deficit, "шт.", "шт.", "шт.")} до минимума (${s.qty}/${s.minQty}) · ${s.warehouse}`,
        time: deficit >= s.minQty / 2 ? "40 мин назад" : "1 ч назад",
        view: "inventory",
        unread: true,
      })
    })

  // 3) Переполненные склады
  warehouseStats
    .filter((w) => w.fill >= 85)
    .forEach((w) => {
      list.push({
        id: `auto-wh-${w.name}`,
        kind: "warning",
        title: `${w.name} заполнен на ${w.fill}%`,
        description: `${w.pallets} из ${w.capacity} паллет · запланируйте отгрузку транзита или дозаказ`,
        time: "2 ч назад",
        view: "inventory",
        unread: true,
      })
    })

  // 4) Свежий заказ (первый со статусом «новый»)
  const freshOrder = ordersData.find((o) => o.status === "new")
  if (freshOrder) {
    list.push({
      id: `auto-order-${freshOrder.id}`,
      kind: "success",
      title: `Новый заказ ${freshOrder.id}`,
      description: `${freshOrder.customer} · ${fmtMoney(freshOrder.amount)} · канал: ${freshOrder.channel}`,
      time: "1 ч назад",
      view: "sales",
      unread: true,
    })
  }

  // 5) Служебные события, не выводимые из данных
  list.push(
    { id: "n5", kind: "danger", title: "Сорван план по отделу оптовых продаж", description: "Факт 82% при пороге контроля 85%", time: "3 ч назад", view: "sales", unread: false },
    { id: "n6", kind: "info", title: "Отпуск согласован", description: "Ким Д. · 22–28.12.2025 · замещающий: Орлов П.", time: "вчера, 17:40", view: "hr", unread: false },
    { id: "n7", kind: "success", title: "Оплата по счёту СЧ-10521 получена", description: "ООО «Вектор» · 412 000 ₽", time: "вчера, 14:05", view: "finance", unread: false },
  )

  return list
}

// ---------- Профиль текущего пользователя ----------
export interface UserSession {
  id: string
  device: string
  browser: string
  location: string
  lastActive: string
  current: boolean
}

export interface CurrentUserProfile {
  name: string
  fullName: string
  email: string
  initials: string
  role: RoleId
  roleName: string
  position: string
  department: string
  phone: string
  office: string
  since: string
  birthday: string
  twoFactor: boolean
  lastLogin: string
  sessions: UserSession[]
}

export const currentUserProfile: CurrentUserProfile = {
  name: "Науменко Владимир",
  fullName: "Науменко Владимир Викторович",
  email: "v.naumenko@technoprom.ru",
  initials: "ВН",
  role: "head",
  roleName: "Руководитель",
  position: "Коммерческий директор",
  department: "Дирекция по продажам",
  phone: "вн. 214",
  office: "Москва, Пресненская наб., 12 · этаж 18",
  since: "14.03.2019",
  birthday: "02.08.1986",
  twoFactor: true,
  lastLogin: "сегодня, 08:47 · IP 91.219.44.10",
  sessions: [
    { id: "s1", device: "Рабочий ПК", browser: "Chrome 131 · Windows 11", location: "Москва, офис", lastActive: "сейчас онлайн", current: true },
    { id: "s2", device: "iPhone 15", browser: "Safari · iOS 18", location: "Москва", lastActive: "вчера, 19:32", current: false },
    { id: "s3", device: "Ноутбук", browser: "Edge · Windows 11", location: "Тверь, склад Б", lastActive: "2 дня назад", current: false },
  ],
}

// ---------- Воронка продаж (сделки) ----------
export type DealStage = "lead" | "qual" | "offer" | "negotiation" | "won"

export interface Deal {
  id: string
  company: string
  amount: number
  probability: number
  manager: string
  initials: string
  tone: string
  stage: DealStage
  days: number
  next: string
}

export const dealStages: { id: DealStage; title: string; dot: string; tone: string }[] = [
  { id: "lead", title: "Новые лиды", dot: "bg-sky-500", tone: "text-sky-600 dark:text-sky-400" },
  { id: "qual", title: "Квалификация", dot: "bg-teal-500", tone: "text-teal-600 dark:text-teal-400" },
  { id: "offer", title: "Предложение", dot: "bg-amber-500", tone: "text-amber-600 dark:text-amber-400" },
  { id: "negotiation", title: "Переговоры", dot: "bg-orange-500", tone: "text-orange-600 dark:text-orange-400" },
  { id: "won", title: "Выиграно", dot: "bg-emerald-500", tone: "text-emerald-600 dark:text-emerald-400" },
]

export const dealsSeed: Deal[] = [
  { id: "d1", company: "ООО «СтройБазис»", amount: 1_240_000, probability: 30, manager: "Орлов П.", initials: "СБ", tone: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300", stage: "lead", days: 1, next: "Звонок 19.12" },
  { id: "d2", company: "АО «ПромТех»", amount: 860_000, probability: 25, manager: "Смирнова А.", initials: "ПТ", tone: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300", stage: "lead", days: 2, next: "Выяснить бюджет" },
  { id: "d3", company: "ИП Гарин К.С.", amount: 154_000, probability: 35, manager: "Орлов П.", initials: "ГК", tone: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300", stage: "lead", days: 4, next: "Отправить прайс" },
  { id: "d4", company: "ЗАО «ХимЛаб»", amount: 96_400, probability: 45, manager: "Ким Д.", initials: "ХЛ", tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300", stage: "qual", days: 3, next: "Тестовый образец" },
  { id: "d5", company: "ООО «АгроСнаб»", amount: 512_000, probability: 50, manager: "Смирнова А.", initials: "АС", tone: "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300", stage: "qual", days: 5, next: "Встреча на объекте" },
  { id: "d6", company: "АО «УралМет»", amount: 862_500, probability: 60, manager: "Орлов П.", initials: "УМ", tone: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300", stage: "offer", days: 2, next: "КП №214, до 20.12" },
  { id: "d7", company: "ООО «ЭнергоМаш»", amount: 1_780_000, probability: 55, manager: "Ким Д.", initials: "ЭМ", tone: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300", stage: "offer", days: 6, next: "Скидка 3% согласована" },
  { id: "d8", company: "ООО «ТрансЛогистик»", amount: 398_000, probability: 70, manager: "Смирнова А.", initials: "ТЛ", tone: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300", stage: "negotiation", days: 4, next: "Юристы правят договор" },
  { id: "d9", company: "ООО «Вектор»", amount: 645_000, probability: 80, manager: "Орлов П.", initials: "ВК", tone: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300", stage: "negotiation", days: 3, next: "Ожидаем подпись" },
  { id: "d10", company: "ГК «Меридиан»", amount: 2_150_000, probability: 95, manager: "Ким Д.", initials: "МР", tone: "bg-sky-100 text-sky-700 dark:bg-sky-950 dark:text-sky-300", stage: "won", days: 1, next: "Счёт выставлен" },
]

// ---------- Роли и права доступа ----------
export type RoleId = "admin" | "head" | "manager" | "accountant" | "storekeeper"
export type PermValue = "full" | "ro" | "none"

export interface ErpRole {
  id: RoleId
  name: string
  desc: string
  members: number
  color: string
}

export const erpRoles: ErpRole[] = [
  { id: "admin", name: "Администратор", desc: "Полный доступ ко всем разделам и настройкам", members: 2, color: "bg-emerald-600" },
  { id: "head", name: "Руководитель", desc: "Все разделы, финансовая аналитика, отчеты", members: 4, color: "bg-teal-600" },
  { id: "manager", name: "Менеджер продаж", desc: "Заказы, сделки, клиенты, склад — без цен закупки", members: 11, color: "bg-amber-600" },
  { id: "accountant", name: "Бухгалтер", desc: "Финансы, счета, отчеты — без продаж и склада", members: 3, color: "bg-orange-600" },
  { id: "storekeeper", name: "Кладовщик", desc: "Только склад: остатки, приемка и отгрузка", members: 6, color: "bg-zinc-600" },
]

export const permModules: { key: string; label: string }[] = [
  { key: "dashboard", label: "Дашборд" },
  { key: "sales", label: "Продажи" },
  { key: "inventory", label: "Склад" },
  { key: "finance", label: "Финансы" },
  { key: "hr", label: "Персонал" },
  { key: "settings", label: "Настройки" },
]

// [role][module] -> full / ro / none
export const permissionMatrix: Record<RoleId, Record<string, PermValue>> = {
  admin: { dashboard: "full", sales: "full", inventory: "full", finance: "full", hr: "full", settings: "full" },
  head: { dashboard: "full", sales: "full", inventory: "full", finance: "full", hr: "full", settings: "ro" },
  manager: { dashboard: "ro", sales: "full", inventory: "full", finance: "none", hr: "none", settings: "none" },
  accountant: { dashboard: "ro", sales: "none", inventory: "none", finance: "full", hr: "ro", settings: "none" },
  storekeeper: { dashboard: "none", sales: "ro", inventory: "full", finance: "none", hr: "none", settings: "none" },
}

export const permValueMap: Record<PermValue, { label: string; short: string; cls: string }> = {
  full: { label: "Полный доступ", short: "R/W", cls: "text-emerald-600 dark:text-emerald-400" },
  ro: { label: "Только чтение", short: "R/O", cls: "text-amber-600 dark:text-amber-400" },
  none: { label: "Доступ закрыт", short: "—", cls: "text-zinc-400 dark:text-zinc-600" },
}

// ---------- Форматирование ----------
export const fmtMoney = (n: number): string =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(n) + " ₽"

export const fmtNum = (n: number): string =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(n)

// ---------- Аудит-трейл заказа (история изменений) ----------
export type AuditTone = "sky" | "emerald" | "amber" | "red" | "zinc"

export interface OrderAuditEvent {
  time: string
  user: string
  action: string
  tone: AuditTone
}

export const auditToneMap: Record<AuditTone, { dot: string; text: string }> = {
  sky: { dot: "bg-sky-500", text: "text-sky-700 dark:text-sky-300" },
  emerald: { dot: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-300" },
  amber: { dot: "bg-amber-500", text: "text-amber-700 dark:text-amber-300" },
  red: { dot: "bg-red-500", text: "text-red-600 dark:text-red-400" },
  zinc: { dot: "bg-zinc-400", text: "text-zinc-500 dark:text-zinc-400" },
}

// Детерминированная история по данным заказа: создание → резерв → подтверждение →
// комплектация → оплата / отмена. Крупным заказам добавляется согласование скидки.
export function getOrderAudit(order: OrderRow): OrderAuditEvent[] {
  const day = order.date.slice(0, 5) // дд.мм
  const suffix = (order.id.replace(/\D/g, "") || "0").slice(-1)
  const events: OrderAuditEvent[] = [
    { time: `${day}.2025, 10:1${suffix}`, user: order.manager, action: `Создал заказ ${order.id} · канал: ${order.channel}`, tone: "sky" },
    { time: `${day}.2025, 11:3${order.items % 10}`, user: "Система", action: `Зарезервировано ${order.items} поз. на складе А`, tone: "zinc" },
  ]

  if (order.amount >= 1_000_000) {
    events.push({ time: `${day}.2025, 12:0${order.items % 10}`, user: "Соколова А.", action: "Согласовала скидку 7% по договорённости с клиентом", tone: "amber" })
  }
  if (order.status === "processing" || order.status === "shipping" || order.status === "done") {
    events.push({ time: "17.12.2025, 09:45", user: order.manager, action: "Подтвердил заказ · счёт отправлен клиенту", tone: "emerald" })
  }
  if (order.status === "shipping" || order.status === "done") {
    events.push({ time: "17.12.2025, 15:20", user: "Тихонов П.", action: "Комплектация завершена · Торг-12 подписана", tone: "emerald" })
  }
  if (order.status === "done") {
    events.push({ time: "18.12.2025, 08:30", user: "Система", action: `Оплата получена: ${fmtMoney(order.amount)}`, tone: "emerald" })
  }
  if (order.status === "cancelled") {
    events.push({ time: "16.12.2025, 14:05", user: order.manager, action: "Заказ отменён клиентом · причина: изменение бюджета", tone: "red" })
  }

  return events
}

// ---------- Дебиторка для виджета на дашборде ----------
export function overdueInvoices(): typeof invoicesData {
  return invoicesData.filter((i) => i.status === "overdue")
}

// Платежи, к дате оплаты которых осталось ≤ withinDays дней (в днях от DEMO_TODAY)
export function dueSoonInvoices(withinDays = 7): { inv: (typeof invoicesData)[number]; daysLeft: number }[] {
  return invoicesData
    .filter((i) => i.status === "pending")
    .map((inv) => ({ inv, daysLeft: daysBetween(parseRuDate(inv.dueDate), DEMO_TODAY) }))
    .filter((x) => x.daysLeft >= 0 && x.daysLeft <= withinDays)
    .sort((a, b) => a.daysLeft - b.daysLeft)
}

// ---------- Тренды для спарклайнов KPI (12 точек) ----------
export const kpiSparklines: Record<string, number[]> = {
  k1: [6.9, 7.4, 7.2, 7.8, 8.3, 8.1, 8.9, 9.4, 10.2, 11.1, 11.8, 12.48],
  k2: [98, 110, 105, 121, 132, 128, 141, 150, 162, 171, 166, 184],
  k3: [940, 972, 1010, 1035, 1068, 1092, 1120, 1145, 1188, 1221, 1256, 1274],
  k4: [9.2, 8.9, 9.1, 8.7, 8.5, 8.8, 8.2, 7.9, 7.6, 7.4, 7.1, 6.8],
}

// ---------- CSV-экспорт (Excel-совместимый, с BOM для кириллицы) ----------
export function downloadCsv(
  filename: string,
  rows: Record<string, string | number>[],
  columns?: { key: string; label: string }[]
): void {
  if (typeof window === "undefined" || rows.length === 0) return
  const cols =
    columns ?? Object.keys(rows[0]).map((k) => ({ key: k, label: k }))
  const esc = (v: string | number | undefined): string => {
    const s = String(v ?? "")
    return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [
    cols.map((c) => esc(c.label)).join(";"),
    ...rows.map((r) => cols.map((c) => esc(r[c.key])).join(";")),
  ]
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], {
    type: "text/csv;charset=utf-8;",
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
