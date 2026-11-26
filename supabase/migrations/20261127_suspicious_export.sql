-- Excel download of the Suspicious Calls list (Amit, 27 Nov 2026): the same filters and order as sus_list, with the
-- extra columns for the sheet; 10,000 rows a call (each call stays well inside the 8 s API time limit) so the page can fetch big selections in a few steps.
create or replace function public.sus_export(p_from date, p_to date, p_max int default 5, p_lo int default 0, p_teams text[] default null,
  p_verticals text[] default null, p_callers text[] default null, p_srcs text[] default null, p_number text default null,
  p_repeat boolean default false, p_sort text default 'new', p_offset int default 0, p_limit int default 5000)
returns json language sql stable security definer set search_path = public as $$
  with f as (
    select s.*, coalesce(t.caller_name, s.caller) caller_name, t.team_name team, t.vertical,
      count(*) over (partition by s.caller, s.client_number) cuts_on_number
    from sus_calls s left join team_map t on t.merge_key = lower(trim(s.caller))
    where s.call_date between p_from and p_to and s.talk_sec <= p_max and (coalesce(p_lo, 0) = 0 or s.talk_sec > p_lo)
      and (p_number is null or s.client_number like '%' || p_number || '%')
      and _sus_pass(coalesce(t.caller_name, s.caller), t.team_name, t.vertical, s.src, p_teams, p_verticals, p_callers, p_srcs)),
  g as (select * from f where not p_repeat or cuts_on_number >= 2),
  pg as (select * from g order by case when p_sort = 'repeat' then cuts_on_number end desc nulls last,
                      case when p_sort = 'repeat' then client_number end, call_datetime desc, id
         offset greatest(p_offset, 0) limit least(greatest(p_limit, 1), 10000))
  select json_build_object('total', (select count(*) from g),
    'rows', (select coalesce(json_agg(x), '[]') from (
      select src, call_datetime, caller_name caller, team, vertical, client_number, talk_sec, cring_sec, cuts_on_number, ref,
             _rec_url(recording) recording
      from pg order by case when p_sort = 'repeat' then cuts_on_number end desc nulls last,
                      case when p_sort = 'repeat' then client_number end, call_datetime desc, id) x))
  where (select sus_allowed());
$$;
revoke all on function public.sus_export(date, date, int, int, text[], text[], text[], text[], text, boolean, text, int, int) from public, anon;
grant execute on function public.sus_export(date, date, int, int, text[], text[], text[], text[], text, boolean, text, int, int) to authenticated;
