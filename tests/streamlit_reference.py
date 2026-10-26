"""Copied unchanged from Streamlit-Dashboard/final_dashboard.py (run_calling_dashboard), used only to prove the new
calculation gives the same numbers. Do not edit."""
import pandas as pd
import pytz
from datetime import datetime, time


def format_dur_hm(total_seconds):
    if pd.isna(total_seconds) or total_seconds <= 0: return "0h 0m"
    tm = int(round(total_seconds / 60))
    return f"{tm // 60}h {tm % 60}m"

def get_display_gap_seconds(start_time, end_time):
    if pd.isna(start_time) or pd.isna(end_time): return 0
    s = start_time.replace(second=0, microsecond=0)
    e = end_time.replace(second=0, microsecond=0)
    return (e - s).total_seconds()


def process_metrics_logic(df_filtered):
    agents_list = []
    total_duration_agg = 0
    ist_tz = pytz.timezone("Asia/Kolkata")

    for owner, agent_group in df_filtered.groupby('call_owner'):
        total_ans, total_miss, total_calls = 0, 0, 0
        total_above_3min, total_mid_calls, total_long_calls, agent_valid_dur = 0, 0, 0, 0
        total_break_sec_all_days, total_active_days = 0, 0
        daily_io_list, daily_break_list, daily_break_timing_list, all_issues = [], [], [], []

        for c_date, day_group in agent_group.groupby('Call Date'):
            timed_group = day_group[day_group['call_starttime'].notna()].sort_values('call_starttime')
            total_active_days += 1
            ans  = len(day_group[day_group['status'].str.lower() == 'answered'])
            miss = len(day_group[day_group['status'].str.lower() == 'missed'])
            total_ans  += ans
            total_miss += miss
            total_calls += len(day_group)

            total_above_3min  += len(day_group[day_group['call_duration'] >= 180])
            total_mid_calls   += len(day_group[(day_group['call_duration'] >= 900) & (day_group['call_duration'] < 1200)])
            total_long_calls  += len(day_group[day_group['call_duration'] >= 1200])
            day_dur = day_group.loc[day_group['call_duration'] >= 180, 'call_duration'].sum()
            agent_valid_dur += day_dur

            if timed_group.empty: continue

            first_call_start = timed_group['call_starttime'].min()
            last_call_end    = timed_group['call_endtime'].max()
            daily_io_list.append(
                f"{c_date.strftime('%d/%m')}: In {first_call_start.strftime('%I:%M %p')} · Out {last_call_end.strftime('%I:%M %p')}"
            )

            start_office = ist_tz.localize(datetime.combine(c_date, time(10, 0)))
            end_office   = ist_tz.localize(datetime.combine(c_date, time(20, 0)))

            today_ist = datetime.now(ist_tz).date()
            if first_call_start > ist_tz.localize(datetime.combine(c_date, time(10, 15))): all_issues.append("Late Check-In")
            if last_call_end < end_office and c_date != today_ist: all_issues.append("Early Check-Out")

            day_breaks, day_break_sec = [], 0

            if first_call_start > start_office:
                g = get_display_gap_seconds(start_office, first_call_start)
                if g >= 300:
                    day_breaks.append({'s': start_office, 'e': first_call_start, 'dur': g})
                    day_break_sec += g

            if len(timed_group) > 1:
                for i in range(len(timed_group) - 1):
                    cur_end   = timed_group['call_endtime'].iloc[i]
                    nxt_start = timed_group['call_starttime'].iloc[i + 1]
                    act_s = max(cur_end,   start_office)
                    act_e = min(nxt_start, end_office)
                    if act_e > act_s:
                        g = get_display_gap_seconds(act_s, act_e)
                        if g >= 300:
                            day_breaks.append({'s': act_s, 'e': act_e, 'dur': g})
                            day_break_sec += g

            today_ist = datetime.now(ist_tz).date()
            effective_end_office = end_office
            if c_date == today_ist:
                now_ist = datetime.now(ist_tz).replace(second=0, microsecond=0)
                effective_end_office = min(end_office, now_ist)

            if c_date != today_ist:
                if last_call_end < end_office:
                    g = get_display_gap_seconds(last_call_end, end_office)
                    if g >= 300:
                        day_breaks.append({'s': last_call_end, 'e': end_office, 'dur': g})
                        day_break_sec += g

            if c_date == today_ist:
                remaining = get_display_gap_seconds(last_call_end, end_office)
                day_break_sec += remaining
            total_break_sec_all_days += day_break_sec
            if day_breaks:
                display_break_sec = sum(b['dur'] for b in day_breaks)
                daily_break_list.append(
                    f"{c_date.strftime('%d/%m')}: {len(day_breaks)} breaks : {format_dur_hm(display_break_sec)}"
                )
                t_str = f"{c_date.strftime('%d/%m')}:"
                for b in day_breaks:
                    t_str += f"\n  {b['s'].strftime('%I:%M %p')}→{b['e'].strftime('%I:%M %p')} ({format_dur_hm(b['dur'])})"
                daily_break_timing_list.append(t_str)

            day_prod_sec = 36000 - day_break_sec
            if len(day_group[day_group['call_duration'] >= 180]) < 40: all_issues.append("Low Calls")
            if day_dur < 11700:    all_issues.append("Low Duration")
            if len(day_breaks) > 2: all_issues.append("Excessive Breaks")
            if day_prod_sec < 18000: all_issues.append("Less Productive")

        total_duration_agg += agent_valid_dur
        prod_sec_total = (36000 * total_active_days) - total_break_sec_all_days

        agents_list.append({
            "IN/OUT TIME": "\n".join(daily_io_list),
            "CALLER": owner,
            "TEAM": agent_group['Team Name'].iloc[0] if not pd.isna(agent_group['Team Name'].iloc[0]) else "Others",
            "TOTAL CALLS": int(total_calls),
            "CALL STATUS": f"{total_ans} Ans / {total_miss} Unans",
            "PICK UP RATIO %": f"{round((total_ans / total_calls * 100)) if total_calls > 0 else 0}%",
            "CALLS > 3 MINS": int(total_above_3min),
            "CALLS 15-20 MINS": int(total_mid_calls),
            "20+ MIN CALLS": int(total_long_calls),
            "CALL DURATION > 3 MINS": format_dur_hm(agent_valid_dur),
            "PRODUCTIVE HOURS": format_dur_hm(prod_sec_total),
            "BREAKS (>=5 MINS)": "\n".join(daily_break_list) if daily_break_list else "0",
            "BREAK TIMINGS (>=5 MINS)": "\n---\n".join(daily_break_timing_list) if daily_break_timing_list else "-",
            "REMARKS": ", ".join(sorted(set(all_issues))) if all_issues else "None",
            "raw_prod_sec": prod_sec_total,
            "raw_dur_sec": agent_valid_dur,
        })

    return pd.DataFrame(agents_list), total_duration_agg
