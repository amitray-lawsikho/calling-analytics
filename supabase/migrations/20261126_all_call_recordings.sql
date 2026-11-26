-- All Call Recordings (Amit, 26 Nov 2026): the recording link, talk time and who ended it for every answered MCube / Acefone
-- call, so any call can be played in the Suspicious Calls dashboard. Rest of the call (agent, number, time) comes from `calls`
-- by id. 60 days like `calls`; if the database passes 450 MB only the last 30 days of links are kept.
create table if not exists public.call_rec (
  id bigint primary key, call_date date not null, src char(1) not null, talk_sec int not null default 0, agent_end boolean not null default false, rec text not null);
create index if not exists call_rec_date on public.call_rec (call_date);
alter table public.call_rec enable row level security;

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
  insert into call_rec (id, call_date, src, talk_sec, agent_end, rec)
  select distinct on (id) id, d, src, talk, agent, rec from _r where k = 'A' and coalesce(rec, '') <> ''
  on conflict (id) do update set rec = excluded.rec, talk_sec = excluded.talk_sec, agent_end = excluded.agent_end;
  return n;
end $$;

create or replace function public.sus_reset_day(p_day date, p_src text) returns void language sql security definer set search_path = public as $$
  delete from sus_day where call_date = p_day and src = p_src;
  delete from sus_seen where call_date = p_day and src = p_src;
  delete from sus_calls where call_date = p_day and src = p_src;
  delete from call_rec where call_date = p_day and src = p_src;
$$;

create or replace function public.sus_trim(p_from date) returns json language plpgsql security definer set search_path = public as $$
declare a int; b int; c int; r int; mb numeric;
begin
  delete from sus_calls where call_date < p_from; get diagnostics a = row_count;
  delete from sus_day where call_date < p_from; get diagnostics b = row_count;
  delete from sus_seen where call_date < (now() at time zone 'Asia/Kolkata')::date - 3; get diagnostics c = row_count;
  delete from call_rec where call_date < p_from; get diagnostics r = row_count;
  mb := round(pg_database_size(current_database()) / 1048576.0);
  if mb > 450 then delete from call_rec where call_date < (now() at time zone 'Asia/Kolkata')::date - 30; end if;
  return json_build_object('calls', a, 'days', b, 'seen', c, 'rec', r, 'db_mb', mb);
end $$;

-- every answered call with a recording: newest first, 50 a page; the count stops at 10,000 to stay fast
create or replace function public.all_list(p_from date, p_to date, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null, p_number text default null, p_min int default 0, p_max int default null,
  p_ended text default null, p_offset int default 0, p_limit int default 50) returns json language sql stable security definer set search_path = public as $$
  with g as (
    select r.id, r.src, c.call_datetime, coalesce(t.caller_name, c.call_owner) caller, t.team_name team, c.client_number, r.talk_sec,
           r.agent_end, r.rec
    from call_rec r join calls c on c.id = r.id left join team_map t on t.merge_key = lower(trim(c.call_owner))
    where r.call_date between p_from and p_to and c.call_date between p_from and p_to
      and r.talk_sec >= coalesce(p_min, 0) and (p_max is null or r.talk_sec < p_max)
      and (p_ended is null or r.agent_end = (p_ended = 'agent'))
      and (p_number is null or c.client_number like '%' || p_number || '%')
      and _sus_pass(coalesce(t.caller_name, c.call_owner), t.team_name, t.vertical, r.src, p_teams, p_verticals, p_callers, p_srcs))
  select json_build_object('total', (select count(*) from (select 1 from g limit 10001) x),
    'rows', (select coalesce(json_agg(x), '[]') from (
      select id::text id, src, call_datetime, caller, team, client_number, talk_sec, agent_end, rec recording
      from g order by call_datetime desc offset greatest(p_offset, 0) limit least(greatest(p_limit, 1), 200)) x))
  where (select sus_allowed());
$$;
revoke all on function public.all_list(date, date, text[], text[], text[], text[], text, int, int, text, int, int) from public, anon;
grant execute on function public.all_list(date, date, text[], text[], text[], text[], text, int, int, text, int, int) to authenticated;
