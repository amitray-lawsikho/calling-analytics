-- Suspicious Call Analysis becomes its own dashboard in the Sales Intelligence Portal (Amit, 26 Nov 2026):
-- 'suspicious' in portal.access, set in portal Settings. Day one: every Calling admin gets it as admin.
alter table portal.access drop constraint access_dashboard_check;
alter table portal.access add constraint access_dashboard_check
  check (dashboard = any (array['calling', 'leads', 'revenue', 'chat', 'reports', 'suspicious']));

insert into portal.access (email, dashboard, role, opts, updated_by, updated_at)
select email, 'suspicious', 'admin', '{}', 'day one: Calling admin', now() from portal.access where dashboard = 'calling' and role = 'admin'
on conflict (email, dashboard) do nothing;

create or replace function public.sus_allowed() returns boolean language sql stable security definer set search_path = '' as
$$ select portal.can('suspicious') $$;

create or replace function public.portal_save_person(p_email text, p_is_admin boolean, p_access jsonb)
 returns void language plpgsql security definer set search_path to '' as $function$
declare e text := lower(trim(p_email)); d text; v jsonb;
begin
  if not portal.is_admin() then raise exception 'Only portal admins can change access' using errcode = '42501'; end if;
  if e !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then raise exception 'Enter a valid email' using errcode = '22023'; end if;
  if e = portal.me() and not coalesce(p_is_admin, false) then raise exception 'You cannot remove your own admin right' using errcode = '22023'; end if;
  insert into portal.users (email, is_admin, added_by) values (e, coalesce(p_is_admin, false), portal.me())
  on conflict (email) do update set is_admin = excluded.is_admin;
  foreach d in array array['calling', 'leads', 'revenue', 'chat', 'reports', 'suspicious'] loop
    v := p_access -> d;
    if v is null or jsonb_typeof(v) = 'null' or coalesce(v->>'role', '') = '' then
      delete from portal.access where email = e and dashboard = d;
    else
      insert into portal.access (email, dashboard, role, opts, updated_by, updated_at)
      values (e, d, v->>'role', coalesce(v->'opts', '{}'), portal.me(), now())
      on conflict (email, dashboard) do update set role = excluded.role, opts = excluded.opts, updated_by = excluded.updated_by, updated_at = now();
    end if;
  end loop;
end $function$;
