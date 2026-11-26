-- Suspicious Call Analysis (Amit, 26 Nov 2026). Separate tables: the Calling tables are not touched.
-- Access: Calling admins, or anyone given 'suspicious' in portal.access. Pipeline-only functions: service role only.

create table if not exists public.sus_day (
  call_date date not null, caller text not null, src char(1) not null,
  calls int not null default 0, reached int not null default 0, answered int not null default 0, ans_agent int not null default 0,
  c5 int not null default 0, c10 int not null default 0, c20 int not null default 0, c60 int not null default 0, c180 int not null default 0,
  lowring int not null default 0, exec_busy int not null default 0, talk_sec bigint not null default 0,
  primary key (call_date, caller, src));

create table if not exists public.sus_calls (
  id bigint primary key, src char(1) not null, call_datetime timestamptz not null, call_date date not null, caller text not null,
  client_number text, talk_sec int not null, cring_sec int, recording text, ref text);
create index if not exists sus_calls_date_caller on public.sus_calls (call_date, caller);
create index if not exists sus_calls_number on public.sus_calls (client_number, call_date);

-- ids already counted into sus_day (the refresh re-reads 30 minutes; kept 3 days)
create table if not exists public.sus_seen (id bigint primary key, call_date date not null, src char(1) not null);
create index if not exists sus_seen_date on public.sus_seen (call_date, src);

create table if not exists public.sus_state (key text primary key, value jsonb, updated_at timestamptz default now());

alter table public.sus_day enable row level security;
alter table public.sus_calls enable row level security;
alter table public.sus_seen enable row level security;
alter table public.sus_state enable row level security;

create or replace function public.sus_allowed() returns boolean language sql stable security definer set search_path = '' as
$$ select portal.can_admin('calling') or portal.can('suspicious') $$;

-- ---------------------------------------------------------------- pipeline (service role)
-- p_rows: [{id, d, caller, src, k: 'A' answered | 'R' reached not answered | 'X' exec busy | 'O' other, agent: bool,
--           talk, cring, low: bool, num, dt, rec, ref}]
create or replace function public.sus_ingest(p_rows jsonb) returns int language plpgsql security definer set search_path = public as $$
declare n int;
begin
  drop table if exists _r, _ins, _new;
  create temp table _r on commit drop as
    select (r->>'id')::bigint id, (r->>'d')::date d, r->>'caller' caller, r->>'src' src, r->>'k' k,
           coalesce((r->>'agent')::boolean, false) agent, coalesce((r->>'talk')::int, 0) talk, (r->>'cring')::int cring,
           coalesce((r->>'low')::boolean, false) low, r->>'num' num, (r->>'dt')::timestamptz dt, r->>'rec' rec, r->>'ref' ref
    from jsonb_array_elements(p_rows) r;
  create temp table _ins (id bigint) on commit drop;
  with ins as (insert into sus_seen (id, call_date, src) select distinct on (id) id, d, src from _r on conflict do nothing returning id)
  insert into _ins select id from ins;
  get diagnostics n = row_count;
  create temp table _new on commit drop as select r.* from _r r join _ins using (id);
  insert into sus_day as s (call_date, caller, src, calls, reached, answered, ans_agent, c5, c10, c20, c60, c180, lowring, exec_busy, talk_sec)
  select d, caller, src, count(*), count(*) filter (where k in ('A','R')), count(*) filter (where k = 'A'),
         count(*) filter (where k = 'A' and agent), count(*) filter (where k = 'A' and agent and talk <= 5),
         count(*) filter (where k = 'A' and agent and talk <= 10), count(*) filter (where k = 'A' and agent and talk <= 20),
         count(*) filter (where k = 'A' and agent and talk <= 60), count(*) filter (where k = 'A' and agent and talk <= 180),
         count(*) filter (where k = 'R' and low), count(*) filter (where k = 'X'), coalesce(sum(talk) filter (where k = 'A'), 0)
  from (select distinct on (id) * from _new) x group by 1, 2, 3
  on conflict (call_date, caller, src) do update set calls = s.calls + excluded.calls, reached = s.reached + excluded.reached,
    answered = s.answered + excluded.answered, ans_agent = s.ans_agent + excluded.ans_agent, c5 = s.c5 + excluded.c5,
    c10 = s.c10 + excluded.c10, c20 = s.c20 + excluded.c20, c60 = s.c60 + excluded.c60, c180 = s.c180 + excluded.c180,
    lowring = s.lowring + excluded.lowring, exec_busy = s.exec_busy + excluded.exec_busy, talk_sec = s.talk_sec + excluded.talk_sec;
  insert into sus_calls (id, src, call_datetime, call_date, caller, client_number, talk_sec, cring_sec, recording, ref)
  select distinct on (id) id, src, dt, d, caller, num, talk, cring, nullif(rec, ''), ref from _r
  where k = 'A' and agent and talk <= 180 and dt is not null
  on conflict (id) do update set recording = coalesce(excluded.recording, sus_calls.recording);
  return n;
end $$;

-- backfill: forget one dialler's day before reloading it in full
create or replace function public.sus_reset_day(p_day date, p_src text) returns void language sql security definer set search_path = public as $$
  delete from sus_day where call_date = p_day and src = p_src;
  delete from sus_seen where call_date = p_day and src = p_src;
  delete from sus_calls where call_date = p_day and src = p_src;
$$;

create or replace function public.sus_trim(p_from date) returns json language plpgsql security definer set search_path = public as $$
declare a int; b int; c int;
begin
  delete from sus_calls where call_date < p_from; get diagnostics a = row_count;
  delete from sus_day where call_date < p_from; get diagnostics b = row_count;
  delete from sus_seen where call_date < (now() at time zone 'Asia/Kolkata')::date - 3; get diagnostics c = row_count;
  return json_build_object('calls', a, 'days', b, 'seen', c, 'db_mb', round(pg_database_size(current_database()) / 1048576.0));
end $$;

create or replace function public.sus_set_state(p_key text, p_value jsonb) returns void language sql security definer set search_path = public as $$
  insert into sus_state (key, value, updated_at) values (p_key, p_value, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
$$;

-- ---------------------------------------------------------------- dashboard
create or replace function public._sus_pass(p_name text, p_team text, p_vertical text, p_src text, p_teams text[], p_verticals text[], p_callers text[], p_srcs text[])
returns boolean language sql immutable set search_path = public as $$
  select (p_srcs is null or p_src = any(p_srcs)) and (p_callers is null or p_name = any(p_callers))
     and (p_teams is null or p_team = any(p_teams)) and (p_verticals is null or p_vertical = any(p_verticals))
$$;

create or replace function public.sus_meta() returns json language sql stable security definer set search_path = public as $$
  select json_build_object(
    'min_date', (select min(call_date) from sus_day), 'max_date', (select max(call_date) from sus_day),
    'teams', (select coalesce(json_agg(t order by t), '[]') from (select distinct t.team_name t from sus_day d join team_map t on t.merge_key = lower(trim(d.caller)) where t.team_name <> '') x),
    'verticals', (select coalesce(json_agg(v order by v), '[]') from (select distinct t.vertical v from sus_day d join team_map t on t.merge_key = lower(trim(d.caller)) where t.vertical <> '') y),
    'callers', (select coalesce(json_agg(json_build_object('caller', c, 'team', tm, 'vertical', vt) order by c), '[]') from (
        select distinct coalesce(t.caller_name, d.caller) c, t.team_name tm, t.vertical vt from sus_day d left join team_map t on t.merge_key = lower(trim(d.caller))) z),
    'last_run', (select json_build_object('run_at', run_at, 'status', status, 'updated_at_ampm', detail->>'updated_at_ampm',
                  'suspicious', detail->'suspicious') from refresh_log order by run_at desc limit 1),
    'backfill', (select value from sus_state where key = 'backfill'))
  where (select sus_allowed());
$$;

-- one row per caller: counts for the chosen talk-time limit (p_max = 5, 10, 20, 60 or 180 seconds)
create or replace function public.sus_report(p_from date, p_to date, p_max int default 5, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null) returns json language sql stable security definer set search_path = public as $$
  with d as (
    select coalesce(t.caller_name, d.caller) caller, t.team_name team, t.vertical, d.call_date, d.calls, d.reached, d.answered, d.ans_agent,
      d.c5, d.c10, d.c20, d.c60, d.c180, d.lowring, d.exec_busy, d.talk_sec
    from sus_day d left join team_map t on t.merge_key = lower(trim(d.caller))
    where d.call_date between p_from and p_to
      and _sus_pass(coalesce(t.caller_name, d.caller), t.team_name, t.vertical, d.src, p_teams, p_verticals, p_callers, p_srcs)),
  agg as (
    select caller, max(team) team, max(vertical) vertical, sum(calls) calls, sum(reached) reached, sum(answered) answered, sum(ans_agent) ans_agent,
      sum(case p_max when 5 then c5 when 10 then c10 when 20 then c20 when 60 then c60 else c180 end) cuts,
      sum(lowring) lowring, sum(exec_busy) exec_busy, sum(talk_sec) talk_sec, count(distinct call_date) days
    from d group by caller),
  rep as (
    select caller, count(*) filter (where n >= 2) repeat_numbers from (
      select coalesce(t.caller_name, s.caller) caller, s.client_number, count(*) n
      from sus_calls s left join team_map t on t.merge_key = lower(trim(s.caller))
      where s.call_date between p_from and p_to and s.talk_sec <= p_max
        and _sus_pass(coalesce(t.caller_name, s.caller), t.team_name, t.vertical, s.src, p_teams, p_verticals, p_callers, p_srcs)
      group by 1, 2) s group by 1)
  select coalesce(json_agg(json_build_object('caller', a.caller, 'team', a.team, 'vertical', a.vertical, 'calls', a.calls, 'reached', a.reached,
           'answered', a.answered, 'ans_agent', a.ans_agent, 'cuts', a.cuts, 'lowring', a.lowring, 'exec_busy', a.exec_busy,
           'talk_sec', a.talk_sec, 'days', a.days, 'repeat_numbers', coalesce(r.repeat_numbers, 0))
         order by (a.cuts::numeric / nullif(a.answered, 0)) desc nulls last, a.cuts desc), '[]')
  from agg a left join rep r using (caller)
  where (select sus_allowed());
$$;

-- caller x day: cuts and answered, for the heatmap
create or replace function public.sus_daily(p_from date, p_to date, p_max int default 5, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null) returns json language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(r), '[]') from (
    select coalesce(t.caller_name, d.caller) caller, d.call_date, sum(answered) answered,
      sum(case p_max when 5 then c5 when 10 then c10 when 20 then c20 when 60 then c60 else c180 end) cuts, sum(lowring) lowring, sum(reached) reached
    from sus_day d left join team_map t on t.merge_key = lower(trim(d.caller))
    where d.call_date between p_from and p_to
      and _sus_pass(coalesce(t.caller_name, d.caller), t.team_name, t.vertical, d.src, p_teams, p_verticals, p_callers, p_srcs)
    group by 1, 2) r
  where (select sus_allowed());
$$;

-- the calls list: 50 a page, newest first or by repeat cuts on the same number
create or replace function public.sus_list(p_from date, p_to date, p_max int default 5, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null, p_number text default null, p_repeat boolean default false,
  p_sort text default 'new', p_offset int default 0, p_limit int default 50) returns json language sql stable security definer set search_path = public as $$
  with f as (
    select s.*, coalesce(t.caller_name, s.caller) caller_name, t.team_name team,
      count(*) over (partition by s.caller, s.client_number) cuts_on_number
    from sus_calls s left join team_map t on t.merge_key = lower(trim(s.caller))
    where s.call_date between p_from and p_to and s.talk_sec <= p_max
      and (p_number is null or s.client_number like '%' || p_number || '%')
      and _sus_pass(coalesce(t.caller_name, s.caller), t.team_name, t.vertical, s.src, p_teams, p_verticals, p_callers, p_srcs)),
  g as (select * from f where not p_repeat or cuts_on_number >= 2)
  select json_build_object('total', (select count(*) from g),
    'rows', (select coalesce(json_agg(x), '[]') from (
      select id::text id, src, call_datetime, caller_name caller, team, client_number, talk_sec, cring_sec, recording, cuts_on_number
      from g order by case when p_sort = 'repeat' then cuts_on_number end desc nulls last,
                      case when p_sort = 'repeat' then client_number end, call_datetime desc
      offset greatest(p_offset, 0) limit least(greatest(p_limit, 1), 200)) x))
  where (select sus_allowed());
$$;

revoke all on function public.sus_ingest(jsonb), public.sus_reset_day(date, text), public.sus_trim(date), public.sus_set_state(text, jsonb) from public, anon, authenticated;
grant execute on function public.sus_ingest(jsonb), public.sus_reset_day(date, text), public.sus_trim(date), public.sus_set_state(text, jsonb) to service_role;
revoke all on function public.sus_meta(), public.sus_report(date, date, int, text[], text[], text[], text[]), public.sus_daily(date, date, int, text[], text[], text[], text[]),
  public.sus_list(date, date, int, text[], text[], text[], text[], text, boolean, text, int, int), public.sus_allowed() from public, anon;
grant execute on function public.sus_meta(), public.sus_report(date, date, int, text[], text[], text[], text[]), public.sus_daily(date, date, int, text[], text[], text[], text[]),
  public.sus_list(date, date, int, text[], text[], text[], text[], text, boolean, text, int, int), public.sus_allowed() to authenticated;
