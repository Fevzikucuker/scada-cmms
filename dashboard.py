"""BHS Türkiye maintenance KPI dashboard v4.3.
Requirements: pandas>=2.0, streamlit>=1.32, plotly>=5.18,
streamlit-autorefresh>=1.0.1. Run: streamlit run dashboard.py
The date filter applies to fault OPENING dates. Durus_Dk is measured in minutes.
"""
from datetime import datetime
from io import StringIO
from urllib.request import urlopen
from zoneinfo import ZoneInfo
import os
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from streamlit_autorefresh import st_autorefresh

CSV_URL = os.environ.get('CMMS_CSV_URL', 'https://docs.google.com/spreadsheets/d/e/2PACX-1vRfxWf8ilCrbH4Bd8nVxeVTIuQSkCJDYIJUWEJ5SoD3GqkSVyC4f0hvDyXhm8DTJy4b3NY75dDwyGjK/pub?gid=543692092&single=true&output=csv')
RENAME = {'Zaman damgası': 'Baslangic', 'İŞ MERKEZİ SEÇİMİ -  WORKCENTER': 'Makine',
          'ARIZA TİPİ(FAULT TYPE)': 'Ariza_Tipi', 'CLOSING DATE': 'Bitis', 'STOPPAGE TIME': 'Durus_Dk'}


def parse_dates(series, dayfirst=True):
    text = series.astype('string').str.strip()
    iso = text.str.match(r'^\d{4}-\d{2}-\d{2}', na=False)
    result = pd.Series(pd.NaT, index=series.index, dtype='datetime64[ns]')
    result.loc[iso] = pd.to_datetime(text.loc[iso], format='ISO8601', errors='coerce')
    result.loc[~iso] = pd.to_datetime(text.loc[~iso], format='mixed', dayfirst=dayfirst, errors='coerce')
    return result


def prepare_data(raw, dayfirst=True):
    df = raw.copy()
    df.columns = df.columns.str.strip()
    df = df.rename(columns=RENAME)
    required = ['Makine', 'Baslangic', 'Bitis', 'Durus_Dk', 'Ariza_Tipi']
    missing = [c for c in required if c not in df]
    if missing:
        raise ValueError('Missing columns: ' + ', '.join(missing))
    df['Makine'] = df['Makine'].astype('string').str.strip().replace('', pd.NA)
    df['Ariza_Tipi'] = df['Ariza_Tipi'].astype('string').str.strip().fillna('Unspecified').replace('', 'Unspecified')
    fault_labels = {
        'MEKANİK SİSTEMLER - MECHANICAL SYSTEMS': 'Mechanical systems',
        'PNÖMATİK SİSTEMLER  - PNEUMATIC SYSTEMS': 'Pneumatic systems',
        'ELEKTRİK EKİPMANLARI - ELECTRICAL EQUIPMENT': 'Electrical equipment',
        'BİNA/ÇELİK YAPILAR - BUILDING/CONSTRUCTION': 'Building / construction',
        'HİDROLİK SİSTEMLER - HYDRAULIC SYSTEMS': 'Hydraulic systems',
        'OTOMASYON/YAZILIM - SOFTWARE': 'Automation / software',
        'Elektrik': 'Electrical', 'Mekanik': 'Mechanical',
        'Hidrolik': 'Hydraulic', 'Pnömatik': 'Pneumatic',
    }
    df['Ariza_Tipi'] = df['Ariza_Tipi'].replace(fault_labels)
    raw_end = df['Bitis'].astype('string').str.strip()
    df['Acik'] = raw_end.isna() | raw_end.eq('')
    df['Baslangic'] = parse_dates(df['Baslangic'], dayfirst)
    df['Bitis'] = parse_dates(df['Bitis'], dayfirst)
    # TR 1.234,5 / 123,5 ve noktalı ondalık değerler. Saat metni otomatik tahmin edilmez.
    duration = df['Durus_Dk'].astype('string').str.strip().str.replace('\u00a0', '', regex=False)
    comma = duration.str.contains(',', na=False)
    duration.loc[comma] = duration.loc[comma].str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
    numeric = pd.to_numeric(duration, errors='coerce').astype(float)
    numeric = numeric.where((numeric >= 0) & (numeric < float('inf')))
    has_time = raw_end.str.contains(r'\d{1,2}:\d{2}', regex=True, na=False)
    df['Kapanis_Saati_Var'] = has_time
    chronological = (df['Bitis'] >= df['Baslangic']).where(has_time, df['Bitis'].dt.normalize() >= df['Baslangic'].dt.normalize())
    closed_valid = df['Bitis'].notna() & df['Baslangic'].notna() & chronological
    df['Durus_Dk'] = numeric.where(closed_valid)
    df['Veri_Uyarisi'] = ''
    df.loc[df['Baslangic'].isna(), 'Veri_Uyarisi'] += 'Invalid start date; '
    df.loc[df['Makine'].isna(), 'Veri_Uyarisi'] += 'Missing machine; '
    df.loc[~df['Acik'] & ~closed_valid, 'Veri_Uyarisi'] += 'Invalid closing date; '
    df.loc[closed_valid & ~has_time, 'Veri_Uyarisi'] += 'Closing time missing; excluded from interval calculation; '
    df.loc[closed_valid & numeric.isna(), 'Veri_Uyarisi'] += 'Invalid downtime minutes; '
    # Önce tüm geçmişte önceki kayıt bulunur; dönem filtresi bundan sonra uygulanır.
    df = df.sort_values(['Makine', 'Baslangic'], kind='stable', na_position='last')
    end = df['Bitis'].where(closed_valid & has_time)
    df['Onceki_Bitis'] = end.groupby(df['Makine']).shift(1)
    gap = (df['Baslangic'] - df['Onceki_Bitis']).dt.total_seconds() / 60
    df['Aralik_Dk'] = gap.where(gap > 0)
    df.loc[gap.notna() & (gap <= 0), 'Veri_Uyarisi'] += 'Overlapping/simultaneous record; '
    return df


def select_period(df, start, end):
    return df.loc[(df['Baslangic'] >= pd.Timestamp(start)) &
                  (df['Baslangic'] < pd.Timestamp(end) + pd.Timedelta(days=1))].copy()


def summarize(df):
    return df.groupby('Makine').agg(Ariza=('Makine', 'size'), Acik=('Acik', 'sum'),
        MTTR=('Durus_Dk', 'mean'), Durus=('Durus_Dk', lambda x: x.sum(min_count=1)),
        Gecerli_Durus=('Durus_Dk', 'count'), Aralik=('Aralik_Dk', 'mean'),
        Aralik_Adet=('Aralik_Dk', 'count'))


def add_score(summary):
    result = summary.copy()
    # Aynı dönem içindeki TÜM makineler referanstır; makine seçimi puanı değiştirmez.
    # Eksik MTTR veya aralık -> puan yok. Sıfır doldurularak iyi/kötü puan verilmez.
    def relative(series):
        maximum = series.max()
        return series / maximum if pd.notna(maximum) and maximum > 0 else series * 0
    score = 100 - (relative(result['MTTR']) * 40 + relative(result['Ariza']) * 35 +
                   (1 - relative(result['Aralik'])) * 25)
    result['Score'] = score.where(result['MTTR'].notna() & result['Aralik'].notna()).clip(0, 100)
    return result


@st.cache_data(ttl=10, show_spinner=False)
def load_data(url):
    with urlopen(url, timeout=20) as response:
        raw = pd.read_csv(StringIO(response.read().decode('utf-8-sig')), dtype=str)
    return raw, datetime.now(ZoneInfo('Europe/Istanbul'))


def style_chart(fig, height=430):
    fig.update_layout(template='plotly_dark', height=height, paper_bgcolor='#111c2e',
        plot_bgcolor='#111c2e', font=dict(color='#dbe7f3', size=12),
        margin=dict(l=15, r=45, t=25, b=35), coloraxis_showscale=False)
    return fig


def horizontal(summary, value, label, color, key, limit):
    data = summary.reset_index().dropna(subset=[value])
    data = data.sort_values(value, ascending=(value == 'Score')).head(limit)
    if data.empty:
        st.info('Insufficient valid data for this indicator.')
        return
    if value == 'Aralik':
        data[value] = data[value] / 60
    fig = px.bar(data, x=value, y='Makine', orientation='h', text=value,
                 labels={value: label, 'Makine': 'Machine'}, color_discrete_sequence=[color])
    fig.update_traces(texttemplate='%{x:.0f}' if value == 'Ariza' else '%{x:.1f}', textposition='outside', cliponaxis=False)
    fig.update_yaxes(categoryorder='array', categoryarray=data['Makine'].tolist()[::-1], automargin=True)
    fig.update_yaxes(title_text=None)
    if value == 'Score':
        fig.update_traces(marker=dict(color=data[value], colorscale='RdYlGn', cmin=0, cmax=100))
        fig.update_xaxes(range=[0, 110])
        fig.add_vline(x=60, line_dash='dot', line_color='#fb7185')
    st.plotly_chart(style_chart(fig, max(420, len(data)*30+80)), use_container_width=True, key=key)


def fmt(value, digits=1):
    return '—' if pd.isna(value) else f'{value:,.{digits}f}'



DISPLAY_COLUMNS = {
    'Makine': 'Machine', 'Ariza_Tipi': 'Fault type', 'Baslangic': 'Opened at',
    'Bitis': 'Closed at', 'Acik': 'Open record', 'Durus_Dk': 'Downtime (min)',
    'Aralik_Dk': 'Calendar interval (min)', 'Kapanis_Saati_Var': 'Closing time available',
    'Veri_Uyarisi': 'Data warning', 'Onceki_Bitis': 'Previous closure',
}

def display_records(frame):
    return frame.rename(columns=DISPLAY_COLUMNS)


def main():
    st.set_page_config(page_title='BHS | Maintenance KPI v4.4 EN', page_icon='⚙️', layout='wide')
    st.markdown('''<style>
    .stApp {background:#0b1220; color:#dbe7f3}
    [data-testid="stSidebar"] {background:#111c2e}
    .block-container {padding-top:2rem; padding-bottom:2rem}
    [data-testid="stMetric"] {background:#111c2e; border:1px solid #22344c;
      border-radius:12px; padding:16px; border-top:3px solid #2dd4bf}
    h1 {font-size:1.65rem !important; font-weight:700 !important; letter-spacing:0.025em}
    h2,h3 {color:#5eead4 !important; font-size:1.1rem !important;
      font-weight:650 !important; letter-spacing:0.045em}
    [data-testid="stSidebar"] h1 {font-size:1.25rem !important}
    [data-testid="stMetricLabel"] p {font-size:0.82rem !important;
      font-weight:650 !important; letter-spacing:0.05em; color:#a8bed3}
    [data-testid="stMetricValue"] {font-size:2rem !important; font-weight:600}
    [data-baseweb="tab"] {font-weight:650; letter-spacing:0.045em}
    [data-testid="stWidgetLabel"] p {font-weight:600; letter-spacing:0.02em}
    [data-testid="stCaptionContainer"] {color:#94a3b8}
    
    </style>''', unsafe_allow_html=True)
    st.sidebar.title('MAINTENANCE KPI')
    st.sidebar.caption('BHS TÜRKİYE · v4.4 EN')
    refresh = st.sidebar.checkbox('AUTO REFRESH · 10 SEC', value=True)
    if refresh:
        st_autorefresh(interval=10000, key='live')
    if st.sidebar.button('REFRESH DATA'):
        load_data.clear()
    dayfirst = st.sidebar.selectbox('DATE FORMAT', ['Day / Month / Year', 'Month / Day / Year']) == 'Day / Month / Year'
    stale = False
    try:
        raw, fetched = load_data(CSV_URL)
        st.session_state['last_good'] = (raw, fetched)
    except Exception as exc:
        if 'last_good' not in st.session_state:
            st.error('Unable to read the data source. Check the published Google Sheets CSV link.')
            st.caption(str(exc))
            st.stop()
        raw, fetched = st.session_state['last_good']
        stale = True
        st.warning('Refresh failed. Showing records from the last successful data fetch.')
    try:
        all_data = prepare_data(raw, dayfirst)
    except Exception as exc:
        st.error(f'Unable to prepare data: {exc}')
        st.stop()
    st.sidebar.caption(('⚠ Stale data' if stale else '✓ CSV data loaded') + '\n\nLast successful fetch (Türkiye time): ' + fetched.strftime('%d.%m.%Y %H:%M:%S'))
    valid = all_data.dropna(subset=['Baslangic', 'Makine'])
    if valid.empty:
        st.warning('No records with a valid machine and start date were found.')
        st.dataframe(display_records(all_data), use_container_width=True)
        st.stop()
    machines = sorted(valid['Makine'].unique())
    selected = st.sidebar.multiselect('MACHINES', machines, default=machines)
    dates = st.sidebar.date_input('DATE RANGE',
        value=(valid['Baslangic'].min().date(), valid['Baslangic'].max().date()))
    limit = st.sidebar.selectbox('MACHINES PER CHART', sorted(set([10, 15, 20, len(machines)])), index=1)
    st.title('BHS TÜRKİYE | MAINTENANCE DASHBOARD')
    if len(dates) != 2:
        st.info('Select a start and end date.')
        st.stop()
    period = select_period(valid, dates[0], dates[1])
    data = period[period['Makine'].isin(selected)].copy()
    if data.empty:
        st.info('No records for the selected machines and date range.')
        st.stop()
    summary = add_score(summarize(period)).reindex([m for m in selected if m in set(data['Makine'])])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('TOTAL FAULTS', len(data))
    c2.metric('OPEN RECORDS', int(data['Acik'].sum()))
    c3.metric('DOWNTIME (H)', fmt(data['Durus_Dk'].sum(min_count=1)/60))
    c4.metric('MTTR (MIN)', fmt(data['Durus_Dk'].mean()))
    overview, reliability, records = st.tabs(['OVERVIEW', 'RELIABILITY', 'RECORDS'])
    with st.expander('DATA COVERAGE', expanded=False):
        st.caption('Cards refer to records opened during the selected period. Open record = blank closing field. '
                   'Downtime and MTTR include only records with valid closure and duration; '
                   'downtime is summed across records; overlapping durations are not merged.')
        st.caption(f"Records included in duration calculations: {data['Durus_Dk'].count()} / {len(data)} · "
                   f"Valid inter-failure intervals: {data['Aralik_Dk'].count()}")
    with overview:
        left, right = st.columns(2)
        with left:
            st.subheader('MTTR (MIN)')
            horizontal(summary, 'MTTR', 'MINUTES', '#fb7185', 'mttr', limit)
        with right:
            st.subheader('FAULT COUNT')
            horizontal(summary, 'Ariza', 'COUNT', '#38bdf8', 'counts', limit)
        left, right = st.columns(2)
        with left:
            st.subheader('FAULT TREND')
            frequency = st.radio('PERIOD', ['Daily', 'Weekly', 'Monthly'], horizontal=True, index=1)
            rule = {'Daily':'D', 'Weekly':'W-MON', 'Monthly':'MS'}[frequency]
            daily = data.groupby(data['Baslangic'].dt.normalize()).size().reindex(pd.date_range(dates[0], dates[1]), fill_value=0)
            trend = daily.resample(rule, label='left', closed='left').sum().rename('Arıza').rename_axis('Tarih').reset_index()
            st.plotly_chart(style_chart(px.line(trend, x='Tarih', y='Arıza', labels={'Tarih': 'Date', 'Arıza': 'Fault count'}, markers=True, color_discrete_sequence=['#2dd4bf'])), use_container_width=True)
        with right:
            st.subheader('FAULT PARETO')
            pareto = data['Ariza_Tipi'].value_counts().rename_axis('Tip').reset_index(name='Adet')
            pareto['Yuzde'] = pareto['Adet'].cumsum()/pareto['Adet'].sum()*100
            fig = make_subplots(specs=[[{'secondary_y':True}]])
            fig.add_trace(go.Bar(x=pareto['Tip'], y=pareto['Adet'], name='Fault count', marker_color='#38bdf8'), secondary_y=False)
            fig.add_trace(go.Scatter(x=pareto['Tip'], y=pareto['Yuzde'], name='Cumulative %', mode='lines+markers', line_color='#fbbf24'), secondary_y=True)
            fig.update_yaxes(title_text='Count', secondary_y=False)
            fig.update_yaxes(title_text='Cumulative %', range=[0, 105], secondary_y=True)
            fig.add_hline(y=80, line_dash='dot', line_color='#94a3b8', secondary_y=True)
            fig.update_layout(legend=dict(orientation='h', y=1.15))
            st.plotly_chart(style_chart(fig, 490), use_container_width=True)
        st.subheader('FAULT HEATMAP')
        heat = pd.crosstab(data['Makine'], data['Ariza_Tipi'])
        heat = heat.loc[heat.sum(axis=1).sort_values(ascending=False).index]
        fig = px.imshow(heat, text_auto=True, aspect='auto', color_continuous_scale='Blues', labels=dict(x='Fault type', y='Machine', color='Count'))
        st.plotly_chart(style_chart(fig, max(420, len(heat)*29+100)), use_container_width=True)
    with reliability:
        st.caption('CALENDAR TIME · Includes nights and holidays; not operating-time MTBF.')
        c1, c2, c3 = st.columns(3)
        c1.metric('FAULT INTERVAL (H)', fmt(data['Aralik_Dk'].mean()/60))
        c2.metric('RELATIVE HEALTH / 100', fmt(summary['Score'].mean()))
        c3.metric('SCORE BELOW 60', int((summary['Score'] < 60).sum()))
        st.caption(f"SCORED MACHINES: {summary['Score'].count()} / {len(summary)}")
        left, right = st.columns(2)
        with left:
            st.subheader('FAULT INTERVAL (H)')
            horizontal(summary, 'Aralik', 'HOURS', '#2dd4bf', 'interval', limit)
        with right:
            st.subheader('RELATIVE HEALTH SCORE')
            horizontal(summary, 'Score', 'SCORE / 100', '#2dd4bf', 'health', limit)
        with st.expander('CALCULATION NOTES', expanded=False):
            st.markdown("""- **MTTR:** average `STOPPAGE TIME` for valid closed records, in minutes. If this field includes waiting time, it is not pure active repair time.
- **Calendar interval:** positive elapsed time from the previous record's closure to the current record's opening for the same machine across the full history. A closure before the selected period may be used. Missing closing times, open/invalid previous closures and overlaps are excluded from the average; records are retained.
- **Relative score:** 100 − (normalized MTTR × 40 + normalized fault count × 35 + inverse-normalized calendar interval × 25). All machines in the same period form the reference; machine selection does not change it. Scores can change when the date range or source data changes.
- **The 60-point threshold** is a display rule, not a standard risk or availability limit. The score uses calendar intervals instead of operating-time MTBF and is an aid to maintenance prioritization.
- Fixed targets have not been defined, so no target values were assumed. Operating-time MTBF and target-based scoring require production operating-hour data.""")
        display = summary.rename(columns={'Ariza':'Fault count', 'Acik':'OPEN RECORDS', 'MTTR':'MTTR (min)', 'Durus':'Downtime (min)', 'Gecerli_Durus':'Records with valid duration', 'Aralik':'Calendar interval (min)', 'Aralik_Adet':'Interval count', 'Score':'Relative score'})
        st.dataframe(display.rename_axis('Machine'), use_container_width=True)
    with records:
        with st.expander('DATA NOTES', expanded=False):
            st.info('Date-only closures may appear as 00:00:00 in the table; this is not the actual closing time. The Closing time available field indicates this distinction. Without a closing time, the next fault interval is not calculated.')
            st.caption('The default source date format is Day/Month/Year. Change it in the sidebar if your source uses Month/Day/Year. STOPPAGE TIME must contain numeric minutes.')
        columns = ['Makine', 'Ariza_Tipi', 'Baslangic', 'Bitis', 'Acik', 'Durus_Dk', 'Aralik_Dk', 'Kapanis_Saati_Var', 'Veri_Uyarisi']
        table = display_records(data[columns].sort_values('Baslangic', ascending=False))
        st.dataframe(table, use_container_width=True, height=480)
        st.download_button('DOWNLOAD CSV', table.to_csv(index=False).encode('utf-8-sig'), 'maintenance_records.csv', 'text/csv')
        excluded = all_data[all_data['Baslangic'].isna() | all_data['Makine'].isna()]
        st.caption(f'Source records excluded from period analysis due to a missing/invalid date or machine: {len(excluded)}')
        if not excluded.empty:
            st.dataframe(display_records(excluded[columns]), use_container_width=True)
        issues = data[data['Veri_Uyarisi'].ne('')]
        st.caption(f'Selected-period records with data warnings: {len(issues)}')
        if not issues.empty:
            st.dataframe(display_records(issues[columns]), use_container_width=True)


if __name__ == '__main__':
    main()
