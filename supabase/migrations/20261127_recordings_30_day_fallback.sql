-- Recordings fall back to 30 days at 460 MB (Amit, 27 Nov 2026). Every refresh ends with sus_trim. When the database
-- (in decimal MB, the unit of the Supabase Usage page) is over 460 MB, All Call Recordings (call_rec) switches to the last
-- 30 days and stays there: the switch is kept in sus_state 'rec_days', so it does not flip back to 60 days the next time
-- the size dips. The freed space is reused by new rows, so the database stops growing. Suspicious calls keep 60 days.
-- Back to 60 days only by hand: delete from sus_state where key = 'rec_days'.

create or replace function public.sus_trim(p_from date) returns json language plpgsql security definer set search_path = public as $$
declare a int; b int; c int; r int; r30 int := 0; mb numeric; keep int; today date := (now() at time zone 'Asia/Kolkata')::date;
begin
  delete from sus_calls where call_date < p_from; get diagnostics a = row_count;
  delete from sus_day where call_date < p_from; get diagnostics b = row_count;
  delete from sus_seen where call_date < today - 3; get diagnostics c = row_count;
  delete from call_rec where call_date < p_from; get diagnostics r = row_count;
  mb := round(pg_database_size(current_database()) / 1000000.0);
  keep := coalesce((select (value->>'days')::int from sus_state where key = 'rec_days'), 60);
  if mb > 460 and keep > 30 then
    keep := 30;
    insert into sus_state (key, value) values ('rec_days', jsonb_build_object('days', 30, 'since', now(), 'db_mb', mb))
    on conflict (key) do update set value = excluded.value;
  end if;
  if keep < 60 then delete from call_rec where call_date < today - (keep - 1); get diagnostics r30 = row_count; end if;
  return json_build_object('calls', a, 'days', b, 'seen', c, 'rec', r + r30, 'db_mb', mb, 'rec_days', keep);
end $$;

-- the page shows how many days of recordings are kept
create or replace function public.sus_meta() returns json language sql stable security definer set search_path = public as $$
  select json_build_object(
    'min_date', (select min(call_date) from sus_day), 'max_date', (select max(call_date) from sus_day),
    'teams', (select coalesce(json_agg(t order by t), '[]') from (select distinct t.team_name t from sus_day d join team_map t on t.merge_key = lower(trim(d.caller)) where t.team_name <> '') x),
    'verticals', (select coalesce(json_agg(v order by v), '[]') from (select distinct t.vertical v from sus_day d join team_map t on t.merge_key = lower(trim(d.caller)) where t.vertical <> '') y),
    'callers', (select coalesce(json_agg(json_build_object('caller', c, 'team', tm, 'vertical', vt) order by c), '[]') from (
        select distinct coalesce(t.caller_name, d.caller) c, t.team_name tm, t.vertical vt from sus_day d left join team_map t on t.merge_key = lower(trim(d.caller))) z),
    'last_run', (select json_build_object('run_at', run_at, 'status', status, 'updated_at_ampm', detail->>'updated_at_ampm',
                  'suspicious', detail->'suspicious') from refresh_log order by run_at desc limit 1),
    'backfill', (select value from sus_state where key = 'backfill'),
    'rec_days', coalesce((select (value->>'days')::int from sus_state where key = 'rec_days'), 60))
  where (select sus_allowed());
$$;
