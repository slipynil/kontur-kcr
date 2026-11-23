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
  assignment_id?: string | null;
  work_quarter?: string | null;
  kcr_code: string;
  inn: string | null;
  kpp?: string | null;
  organization_name: string | null;
  manager_name?: string | null;
  tags: (Tag)[];
  current_stage: Stage;
  assigned_operator_id: string | null;
  operator_name?: string | null;
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
  assignment_id?: string | null;
  work_quarter?: string | null;
  kcr_code: string;
  inn: string | null;
  kpp?: string | null;
  organization_name: string | null;
  manager_name?: string | null;
  contacts?: Record<string, unknown> | null;
  tags: (Tag)[];
  current_stage: Stage | null;
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
  items: (ClientEvent | HistoryEvent)[];
  page: number;
  page_size: number;
  total: number;
};

export type HistoryEvent = ClientEvent & {
  work_quarter: string | null;
  cycle_id: string | null;
  actor_id: string | null;
  actor_name: string | null;
  occurred_at: string;
  recorded_at: string;
  seq: number;
  payload: {
    comment_text?: string;
    call_result?: CallResult;
    promised_date?: string | null;
    wait_until?: string | null;
    operator_id?: string;
    previous_operator_id?: string | null;
    kind?: 'TAKE' | 'REASSIGN' | 'INHERITED';
    from_stage?: Stage | null;
    to_stage?: Stage;
    reason?: 'TAKE' | 'SNAPSHOT' | 'WAIT_SET' | 'WAIT_EXPIRED' | 'RESTORE';
  };
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

export type EventType = "CALL_RESULT" | "HISTORICAL_COMMENT" | "COMMENT" | "PUBLICATION" | "EMAIL_SENT" | "STAGE_CHANGE" | "CLAIM_UPDATE" | "OPERATOR_ASSIGN";

export type CallResult = "AGREEMENT" | "REFUSAL" | "NO_ANSWER" | "WRONG_NUMBER";

export type ClientEventCreate = CallEventCreate | HistoricalCommentCreate;

export type ClientEvent = {
  document_quarters?: (string)[];
  work_quarter?: string | null;
  id: string;
  kcr_code: string;
  author_id: string;
  actor_name?: string | null;
  event_type: EventType;
  call_result?: CallResult | null;
  promised_date?: string | null;
  comment_text: string;
  historical_event_date?: string | null;
  created_at: string;
  stage?: string | null;
  wait_until?: string | null;
};

export type TakeResponse = {
  assignment_id: string;
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
} & (
  | { import_type: "REGISTRY" }
  | { import_type: "SNAPSHOT"; selected_quarter: string; snapshot_date: string }
);

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

export type PoolPublicationRequest = {
  codes: string[];
};

export type PoolClient = {
  kcr_code: string;
  assigned_operator_id: string | null;
};

export type PoolResponse = {
  items: PoolClient[];
};

export type QuarterSummary = {
  quarter: string;
  total_documents: number;
  unsubmitted_documents: number;
  unsubmitted_percent: number | null;
};

export type TakeRequest = {
  expected_assignment_id: string | null;
  request_id: string;
};

export type ReassignmentRequest = {
  id: string;
  kcr_code: string;
  work_quarter: string;
  expected_assignment_id: string;
  owner_id: string;
  requester_id: string;
  created_at: string;
  expires_at: string;
  resolved_at: string | null;
  status: "PENDING" | "REJECTED" | "TRANSFERRED" | "CANCELLED";
};

export type CallEventCreate = {
  document_quarters?: (string)[];
  event_type: "CALL_RESULT";
  request_id: string;
  expected_assignment_id: string;
  work_quarter: string;
  call_result: CallResult;
  comment_text?: string;
  promised_date?: string | null;
  wait_until?: string | null;
};

export type HistoricalCommentCreate = {
  document_quarters?: (string)[];
  event_type: "HISTORICAL_COMMENT";
  request_id: string;
  comment_text: string;
  historical_event_date: string;
  work_quarter?: string | null;
};

export type SavedCallEvent = ClientEvent & {
  event_type?: "CALL_RESULT";
  wait_applied: boolean;
};

export type SavedHistoricalComment = ClientEvent & {
  event_type?: "HISTORICAL_COMMENT";
  historical_event_date: string;
};

export type AssignmentState = {
  kcr_code: string;
  work_quarter: string;
  assignment_id: string | null;
  assigned_operator_id: string | null;
  assigned_at: string | null;
  stage: Stage | null;
  wait_until: string | null;
  awaiting_balance: boolean;
};
