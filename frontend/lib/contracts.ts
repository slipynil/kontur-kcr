// Types from docs/api/public-api.yaml. Conditional event requirements are validated by the forms.
export type Error = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type Role = "ADMIN" | "OPERATOR";

export type User = {
  id: string;
  login: string;
  role: Role;
  is_active: boolean;
  created_at: string;
};

export type UserCreate = {
  login: string;
  password: string;
  role: Role;
};

export type UserListResponse = {
  items: (User)[];
  page: number;
  page_size: number;
  total: number;
};

export type LoginRequest = {
  login: string;
  password: string;
};

export type LoginResponse = {
  token: string;
  token_type: "Bearer";
  expires_in: number;
  user: User;
};

export type Tag = "VIP" | "NOVICE" | "INTEGRATOR" | "TECH_PARTNER" | "MPP" | "DO_NOT_WORK";

export type ClientStatus = "ACTIVE" | "INACTIVE";

export type Stage = "POOL" | "IN_WORK" | "WAIT_DEADLINE" | "CLAIM" | "CLOSED";

export type Dynamics = "REDUCING" | "NO_CHANGE" | "GROWING" | "ALL_SUBMITTED";

export type NullableDynamics = Dynamics | null;

export type Duration = "FIRST_TIME" | "REPEATED" | "LONG_DELAY_2Q";

export type QuarterSnapshotBrief = {
  quarter: string;
  total_documents: number;
  unsubmitted_documents: number;
  snapshot_date: string;
  last_observation_quarter?: string;
};

export type ClientListItem = {
  work_quarter?: string | null;
  kcr_code: string;
  inn: string | null;
  kpp?: string | null;
  organization_name: string | null;
  status: ClientStatus;
  is_blocked?: boolean;
  manager_name?: string | null;
  tags: (Tag)[];
  current_stage: Stage;
  assigned_operator_id: string | null;
  dynamics: NullableDynamics;
  latest_snapshot: QuarterSnapshotBrief | null;
  needs_review?: boolean;
  inherited_fields?: Record<string, unknown>;
  wait_until?: string | null;
  wait_source_work_quarter?: string | null;
  outstanding_documents?: number;
  duration?: Duration | null;
  dynamics_quarter: string;
  dynamics_previous_quarter: string;
  dynamics_current_documents: number | null;
  dynamics_previous_documents: number | null;
  quarter_summary: QuarterSummary | null;
};

export type ClientListResponse = {
  items: (ClientListItem)[];
  page: number;
  page_size: number;
  total: number;
};

export type QuarterSnapshot = {
  kcr_code: string;
  quarter: string;
  total_documents: number;
  unsubmitted_documents: number;
  snapshot_date: string;
  last_observation_quarter?: string;
};

export type ClientAssignmentInfo = {
  work_quarter: string;
  operator_id: string;
  operator_name: string;
  assigned_at: string;
};

export type ClientCard = {
  work_quarter?: string | null;
  kcr_code: string;
  inn: string | null;
  kpp?: string | null;
  organization_name: string | null;
  status: ClientStatus;
  is_blocked?: boolean;
  manager_name?: string | null;
  contacts?: Record<string, unknown> | null;
  tags: (Tag)[];
  current_stage: Stage;
  assignment: ClientAssignmentInfo | null;
  snapshots: (QuarterSnapshot)[];
  dynamics?: NullableDynamics;
  needs_review?: boolean;
  inherited_fields?: Record<string, unknown>;
  wait_until?: string | null;
  wait_source_work_quarter?: string | null;
  outstanding_documents?: number;
  duration?: Duration | null;
  dynamics_quarter: string;
  dynamics_previous_quarter: string;
  dynamics_current_documents: number | null;
  dynamics_previous_documents: number | null;
  quarter_summary: QuarterSummary | null;
};

export type EventListResponse = {
  items: (ClientEvent)[];
  page: number;
  page_size: number;
  total: number;
};

export type SnapshotHistoryRecord = {
  id: string;
  kcr_code: string;
  quarter: string;
  unsubmitted_documents: number;
  import_batch_id: string;
  snapshot_date: string;
  created_at: string;
  observation_quarter?: string;
  observation_order?: number;
  inherited_fields?: Record<string, unknown>;
};

export type SnapshotHistoryResponse = {
  items: (SnapshotHistoryRecord)[];
  page: number;
  page_size: number;
  total: number;
};

export type EventType = "CALL_RESULT" | "HISTORICAL_COMMENT" | "EMAIL_SENT" | "STAGE_CHANGE" | "CLAIM_UPDATE" | "OPERATOR_ASSIGN";

export type CallResult = "AGREEMENT" | "REFUSAL" | "NO_ANSWER" | "WRONG_NUMBER";

export type ClientEventCreate = {
  work_quarter?: string | null;
  event_type: EventType;
  call_result?: CallResult;
  promised_date?: string | null;
  comment_text: string;
  historical_event_date?: string | null;
  wait_until?: string | null;
  stage?: "POOL" | "IN_WORK" | "WAIT_DEADLINE" | "CLAIM" | "CLOSED";
};

export type ClientEvent = {
  work_quarter?: string | null;
  id: string;
  kcr_code: string;
  author_id: string;
  event_type: EventType;
  call_result?: CallResult;
  promised_date?: string | null;
  comment_text: string;
  historical_event_date?: string | null;
  created_at: string;
  stage?: string | null;
  wait_until?: string | null;
};

export type TakeResponse = {
  work_quarter: string;
  kcr_code: string;
  assigned_operator_id: string;
  assigned_at: string;
};

export type ImportBatch = {
  id: string;
  uploaded_by: string;
  filename: string;
  status: "PROCESSING" | "COMPLETED" | "ROLLED_BACK" | "FAILED";
  total_rows: number;
  success_count: number;
  error_count: number;
  created_at: string;
  selected_quarter: string;
  import_type: "REGISTRY" | "SNAPSHOT";
  snapshot_date: string;
};

export type AssistantSummaryResponse = {
  kcr_code: string;
  summary: string;
  generated_at: string;
};

export type FilterQueryRequest = {
  query: string;
};

export type FilterQueryResponse = {
  items: ({
  kcr_code: string;
  reason: string;
})[];
};

export type ChatRequest = {
  kcr_code: string;
  message: string;
};

export type EventReference = {
  event_id: string;
  event_date: string;
};

export type ChatResponse = {
  answer: string;
  event_references: (EventReference)[];
};

export type RecoveryCommand = {
  operation_id: string;
};

export type QuarterSummary = {
  quarter: string;
  total_documents: number;
  unsubmitted_documents: number;
  unsubmitted_percent: number | null;
};
