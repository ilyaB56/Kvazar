// DTO контрактов API /api/v1 — единая точка правды для фронтенда.
// Деньги и курсы — ТОЛЬКО строками (ADR-003), без parseFloat.

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface User {
  id: string
  email: string
  full_name: string
  role: string
  is_active: boolean
}

// ----- Интеграционная платформа -----

export type FieldType = 'string' | 'int' | 'enum'

export interface ConfigSchemaField {
  type: FieldType
  required?: boolean
  values?: string[] // для enum
  default?: string | number
}

/** Схема настроек коннектора: {имя_поля: описание} */
export type ConfigSchema = Record<string, ConfigSchemaField>

export interface ConnectorType {
  code: string
  display_name: string
  capabilities: Record<string, boolean>
  config_schema: ConfigSchema
}

export interface Connection {
  id: string
  name: string
  connector_code: string
  config: Record<string, unknown>
  is_active: boolean
  last_check_ok: boolean | null
}

export interface TestResult {
  ok: boolean
  error: string
}

export interface Webhook {
  id: string
  name: string
  target_module: string
  url_path: string
}

/** Ответ создания вебхука: токен показывается ровно один раз */
export interface WebhookCreated extends Webhook {
  secret_token: string
}

export interface SyncJob {
  id: string
  name: string
  connection_id: string
  direction: 'fetch' | 'push'
  cron: string
  endpoint: string
  is_active: boolean
}

export interface SyncRun {
  id: number
  sync_job_id: string
  status: 'success' | 'error'
  items_in: number
  items_out: number
  payload: Record<string, unknown>
  error: string
  started_at: string
}

// ----- Управленческий учёт (экраны v2, контракт уже зафиксирован) -----

export interface Transaction {
  id: string
  doc_number: string | null
  doc_type_code: string
  kind: 'income' | 'expense' | 'transfer'
  status: 'draft' | 'posted'
  operated_at: string
  created_at: string
  amount: string
  currency: string
  rate: string | null
  amount_base: string | null
  amount_to: string | null
  currency_to: string | null
  rate_to: string | null
  amount_to_base: string | null
  account_id: string
  account_to_id: string | null
  category_id: string | null
  counterparty_id: string | null
  description: string
  dimensions: Record<string, unknown>
  is_deleted: boolean
  is_stornoed: boolean
  storno_of_id: string | null
}
