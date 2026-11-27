-- All Call Recordings without the calls table (Amit, 27 Nov 2026): a month took about 23 s, past the API time limit,
-- because every search joined call_rec to `calls` (about 600,000 rows a month) for the agent, number and time.
-- call_rec now keeps those three itself (they match `calls` exactly), so the page reads only call_rec, newest first by index.

alter table public.call_rec add column if not exists call_datetime timestamptz;
alter table public.call_rec add column if not exists caller text;
alter table public.call_rec add column if not exists client_number text;
create index if not exists call_rec_dt on public.call_rec (call_datetime desc);

-- new rows: from the dialler record; anything it lacks is looked up in calls by id (primary key, one row each)
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
  insert into call_rec as cr (id, call_date, src, talk_sec, agent_end, rec, call_datetime, caller, client_number)
  select distinct on (r.id) r.id, r.d, r.src, r.talk, r.agent, r.rec, coalesce(r.dt, c.call_datetime), coalesce(nullif(r.caller, ''), c.call_owner),
         coalesce(nullif(r.num, ''), c.client_number)
  from _r r left join calls c on c.id = r.id and (r.dt is null or coalesce(r.num, '') = '')
  where r.k = 'A' and coalesce(r.rec, '') <> ''
  on conflict (id) do update set rec = excluded.rec, talk_sec = excluded.talk_sec, agent_end = excluded.agent_end,
    call_datetime = coalesce(excluded.call_datetime, cr.call_datetime), caller = coalesce(excluded.caller, cr.caller),
    client_number = coalesce(excluded.client_number, cr.client_number);
  return n;
end $$;

-- one query for the page's rows (newest first through call_rec_dt) and one for the count (stops at 10,001).
-- Built per search with only the filters in use, so Postgres plans it for those filters: as a plain SQL function
-- the shared plan with every "or p is null" took about 3 s; this way the same month takes well under a second.
create or replace function public.all_list(p_from date, p_to date, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null, p_number text default null, p_min int default 0, p_max int default null,
  p_ended text default null, p_offset int default 0, p_limit int default 50) returns json language plpgsql stable security definer set search_path = public as $$
declare w text; v_total int; v_rows json;
begin
  if not sus_allowed() then return null; end if;
  w := 'r.call_datetime >= ($1::timestamp at time zone ''Asia/Kolkata'') and r.call_datetime < (($2 + 1)::timestamp at time zone ''Asia/Kolkata'')'
    || ' and r.call_date between $1 and $2';
  if coalesce(p_min, 0) > 0 then w := w || ' and r.talk_sec >= $3'; end if;
  if p_max is not null then w := w || ' and r.talk_sec < $4'; end if;
  if p_ended is not null then w := w || ' and r.agent_end = ($5 = ''agent'')'; end if;
  if p_number is not null then w := w || ' and r.client_number like ''%'' || $6 || ''%'''; end if;
  -- the team / vertical / caller / dialler filters written out here (same test as _sus_pass, which cannot be inlined)
  if p_teams is not null then w := w || ' and t.team_name = any($7)'; end if;
  if p_verticals is not null then w := w || ' and t.vertical = any($8)'; end if;
  if p_callers is not null then w := w || ' and coalesce(t.caller_name, r.caller) = any($9)'; end if;
  if p_srcs is not null then w := w || ' and r.src = any($10)'; end if;
  execute 'select count(*) from (select 1 from call_rec r left join team_map t on t.merge_key = lower(trim(r.caller)) where ' || w || ' limit 10001) x'
    into v_total using p_from, p_to, p_min, p_max, p_ended, p_number, p_teams, p_verticals, p_callers, p_srcs;
  execute 'select coalesce(json_agg(x order by x.call_datetime desc), ''[]'') from (
      select r.id::text id, r.src, r.call_datetime, coalesce(t.caller_name, r.caller) caller, t.team_name team, r.client_number,
             r.talk_sec, r.agent_end, _rec_url(r.rec) recording
      from call_rec r left join team_map t on t.merge_key = lower(trim(r.caller)) where ' || w || '
      order by r.call_datetime desc offset $11 limit $12) x'
    into v_rows using p_from, p_to, p_min, p_max, p_ended, p_number, p_teams, p_verticals, p_callers, p_srcs,
      greatest(p_offset, 0), least(greatest(p_limit, 1), 200);
  return json_build_object('total', v_total, 'rows', v_rows);
end $$;
