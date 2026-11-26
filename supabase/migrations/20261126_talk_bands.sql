-- Talk-time bands instead of "up to" limits (Amit, 26 Nov 2026): No length (all <= 3 min), <= 5 s, 5-10 s, 10-20 s,

-- 20 s - 1 min, 1-3 min. p_lo = lower bound (exclusive, 0 = none), p_max = upper bound (inclusive). sus_day keeps cumulative counts,

-- so a band = count up to p_max minus count up to p_lo.

drop function if exists public.sus_report(date, date, int, text[], text[], text[], text[]);

drop function if exists public.sus_daily(date, date, int, text[], text[], text[], text[]);

drop function if exists public.sus_list(date, date, int, text[], text[], text[], text[], text, boolean, text, int, int);

create or replace function public.sus_report(p_from date, p_to date, p_max int default 5, p_lo int default 0, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null) returns json language sql stable security definer set search_path = public as $$
  with d as (
    select coalesce(t.caller_name, d.caller) caller, t.team_name team, t.vertical, d.call_date, d.calls, d.reached, d.answered, d.ans_agent,
      d.c5, d.c10, d.c20, d.c60, d.c180, d.lowring, d.exec_busy, d.talk_sec
    from sus_day d left join team_map t on t.merge_key = lower(trim(d.caller))
    where d.call_date between p_from and p_to
      and _sus_pass(coalesce(t.caller_name, d.caller), t.team_name, t.vertical, d.src, p_teams, p_verticals, p_callers, p_srcs)),
  agg as (
    select caller, max(team) team, max(vertical) vertical, sum(calls) calls, sum(reached) reached, sum(answered) answered, sum(ans_agent) ans_agent,
      sum((case p_max when 5 then c5 when 10 then c10 when 20 then c20 when 60 then c60 else c180 end - case p_lo when 5 then c5 when 10 then c10 when 20 then c20 when 60 then c60 else 0 end)) cuts,
      sum(lowring) lowring, sum(exec_busy) exec_busy, sum(talk_sec) talk_sec, count(distinct call_date) days
    from d group by caller),
  rep as (
    select caller, count(*) filter (where n >= 2) repeat_numbers from (
      select coalesce(t.caller_name, s.caller) caller, s.client_number, count(*) n
      from sus_calls s left join team_map t on t.merge_key = lower(trim(s.caller))
      where s.call_date between p_from and p_to and s.talk_sec <= p_max and (coalesce(p_lo, 0) = 0 or s.talk_sec > p_lo)
        and _sus_pass(coalesce(t.caller_name, s.caller), t.team_name, t.vertical, s.src, p_teams, p_verticals, p_callers, p_srcs)
      group by 1, 2) s group by 1)
  select coalesce(json_agg(json_build_object('caller', a.caller, 'team', a.team, 'vertical', a.vertical, 'calls', a.calls, 'reached', a.reached,
           'answered', a.answered, 'ans_agent', a.ans_agent, 'cuts', a.cuts, 'lowring', a.lowring, 'exec_busy', a.exec_busy,
           'talk_sec', a.talk_sec, 'days', a.days, 'repeat_numbers', coalesce(r.repeat_numbers, 0))
         order by (a.cuts::numeric / nullif(a.answered, 0)) desc nulls last, a.cuts desc), '[]')
  from agg a left join rep r using (caller)
  where (select sus_allowed());
$$;

create or replace function public.sus_daily(p_from date, p_to date, p_max int default 5, p_lo int default 0, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null) returns json language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(r), '[]') from (
    select coalesce(t.caller_name, d.caller) caller, d.call_date, sum(answered) answered,
      sum((case p_max when 5 then c5 when 10 then c10 when 20 then c20 when 60 then c60 else c180 end - case p_lo when 5 then c5 when 10 then c10 when 20 then c20 when 60 then c60 else 0 end)) cuts, sum(lowring) lowring, sum(reached) reached
    from sus_day d left join team_map t on t.merge_key = lower(trim(d.caller))
    where d.call_date between p_from and p_to
      and _sus_pass(coalesce(t.caller_name, d.caller), t.team_name, t.vertical, d.src, p_teams, p_verticals, p_callers, p_srcs)
    group by 1, 2) r
  where (select sus_allowed());
$$;

create or replace function public.sus_list(p_from date, p_to date, p_max int default 5, p_lo int default 0, p_teams text[] default null, p_verticals text[] default null,
  p_callers text[] default null, p_srcs text[] default null, p_number text default null, p_repeat boolean default false,
  p_sort text default 'new', p_offset int default 0, p_limit int default 50) returns json language sql stable security definer set search_path = public as $$
  with f as (
    select s.*, coalesce(t.caller_name, s.caller) caller_name, t.team_name team,
      count(*) over (partition by s.caller, s.client_number) cuts_on_number
    from sus_calls s left join team_map t on t.merge_key = lower(trim(s.caller))
    where s.call_date between p_from and p_to and s.talk_sec <= p_max and (coalesce(p_lo, 0) = 0 or s.talk_sec > p_lo)
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

revoke all on function public.sus_report(date, date, int, int, text[], text[], text[], text[]) from public, anon;
grant execute on function public.sus_report(date, date, int, int, text[], text[], text[], text[]) to authenticated;

revoke all on function public.sus_daily(date, date, int, int, text[], text[], text[], text[]) from public, anon;
grant execute on function public.sus_daily(date, date, int, int, text[], text[], text[], text[]) to authenticated;

revoke all on function public.sus_list(date, date, int, int, text[], text[], text[], text[], text, boolean, text, int, int) from public, anon;
grant execute on function public.sus_list(date, date, int, int, text[], text[], text[], text[], text, boolean, text, int, int) to authenticated;
