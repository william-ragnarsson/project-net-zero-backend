-- The event store, and the read models folded from it.
--
-- `events` is the source of truth: one row per line of a run's events.jsonl,
-- stored byte for byte. Everything below the line is a projection that
-- `netzero db rebuild` can drop and refold from `events`.

create table streams (
  stream_id   text        primary key,                -- 'run:<run id>'
  kind        text        not null,                   -- 'run'
  version     integer     not null default 0 check (version >= 0),  -- seq of the last event
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table events (
  position     bigint      generated always as identity primary key,  -- global order
  stream_id    text        not null references streams (stream_id),
  stream_seq   integer     not null check (stream_seq > 0),
  line         text        not null,  -- the exact line; jsonb would reorder its keys
  type         text        generated always as (line::jsonb ->> 'type') stored,
  ts           bigint      generated always as ((line::jsonb ->> 'ts')::bigint) stored,
  function_id  text        generated always as (line::jsonb ->> 'function_id') stored,
  candidate_id text        generated always as (line::jsonb ->> 'candidate_id') stored,
  data         jsonb       generated always as (line::jsonb -> 'data') stored,
  recorded_at  timestamptz not null default now(),
  unique (stream_id, stream_seq),
  constraint events_seq_matches_line check (stream_seq = (line::jsonb ->> 'seq')::integer),
  constraint events_stream_matches_line check (stream_id = 'run:' || (line::jsonb ->> 'run_id'))
);

create index events_type_idx on events (type);
create index events_activity_idx on events (ts desc, position desc)
  where type in ('run.created', 'run.selection.confirmed', 'function.completed',
                 'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted');

-- Append-only: history is corrected with new events, never by rewriting rows.
create function events_append_only() returns trigger language plpgsql as $$
begin
  raise exception 'events is append-only: % is not allowed', tg_op
    using hint = 'Record a new event instead of changing an old one.';
end
$$;

create trigger events_append_only
  before update or delete or truncate on events
  for each statement execute function events_append_only();

-- ---------------------------------------------------------------------------
-- Read models

create table projects (
  id    text primary key,          -- short, stable: derived from key
  key   text not null unique,      -- 'github.com/<owner>/<repo>' | 'demo'
  kind  text not null,             -- 'github' | 'demo'
  name  text not null,             -- '<owner>/<repo>' | 'demo'
  url   text
);

create table runs (
  run_id                 text primary key,
  project_id             text not null references projects (id),
  state                  text not null,
  mode                   text not null,
  ref                    text,
  base_sha               text,               -- the commit the run measured
  created_ts             bigint not null,
  updated_ts             bigint not null,
  ended_ts               bigint,             -- ts of the terminal event
  last_seq               integer not null,
  functions_total        integer not null,
  functions_done         integer not null,
  counts_by_outcome      jsonb not null,
  mean_reduction_pct     double precision,
  g_saved_per_1m_calls   double precision not null,
  kwh_saved_per_1m_calls double precision not null,
  llm_cost_usd           double precision not null,
  error                  text
);

create index runs_project_idx on runs (project_id, created_ts desc);

create table function_results (
  run_id                 text not null references runs (run_id) on delete cascade,
  function_id            text not null,
  project_id             text not null references projects (id),
  ord                    integer not null,   -- position in the run's selection
  qualname               text not null,
  module                 text,
  file                   text,
  outcome                text,               -- null while in progress
  winner                 text,
  delta_pct              double precision,
  ci_lo                  double precision,
  ci_hi                  double precision,
  g_saved_per_1m_calls   double precision,
  kwh_saved_per_1m_calls double precision,
  reason                 text not null default '',
  duration_ms            integer,
  completed_ts           bigint,
  primary key (run_id, function_id)
);

create index function_results_project_idx on function_results (project_id, function_id);

-- How far each projector has read `events`.
create table projector_checkpoints (
  name       text primary key,
  position   bigint not null default 0,
  updated_at timestamptz not null default now()
);
