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



FAULT_FIELDS = {
    'LÜTFEN ARIZAYI TANIMLAYINIZ(PLEASE DESCRIBE THE PROBLEM)': 'DESCRIPTION',
    'Yapılan İş': 'WORK PERFORMED',
    'Değişen Parça': 'REPLACED PARTS',
    'RECORD_ID': 'RECORD ID',
}


def fault_log_data(data):
    log = data.copy()
    for source, target in FAULT_FIELDS.items():
        log[target] = log[source].fillna('').astype(str) if source in log else ''
    log['STATUS'] = 'CLOSED'
    log.loc[log['Acik'], 'STATUS'] = 'OPEN'
    invalid_close = ~log['Acik'] & (log['Bitis'].isna() |
        (log['Bitis'].dt.normalize() < log['Baslangic'].dt.normalize()) |
        (log['Kapanis_Saati_Var'] & (log['Bitis'] < log['Baslangic'])))
    log.loc[invalid_close, 'STATUS'] = 'CHECK DATA'
    # Preserve source status without using it to change the existing KPI rules.
    if 'STATUS' in data:
        log['SOURCE STATUS'] = data['STATUS'].fillna('').astype(str).replace({
            'TAMAMLANDI': 'CLOSED', 'AÇIK': 'OPEN', 'KAPALI': 'CLOSED',
            'DEVAM EDİYOR': 'IN PROGRESS'})
    else:
        log['SOURCE STATUS'] = ''
    log['_priority'] = log['STATUS'].map({'OPEN': 0, 'CHECK DATA': 1, 'CLOSED': 2})
    return log.sort_values(['_priority', 'Baslangic'], ascending=[True, False], kind='stable')


def filter_fault_log(log, status, query):
    result = log if status == 'ALL' else log[log['STATUS'].eq(status)]
    if query.strip():
        fields = ['Makine', 'Ariza_Tipi', 'DESCRIPTION', 'WORK PERFORMED', 'REPLACED PARTS', 'RECORD ID']
        match = result[fields].fillna('').astype(str).apply(
            lambda col: col.str.contains(query.strip(), case=False, regex=False)).any(axis=1)
        result = result[match]
    return result.copy()


def fault_row_style(row):
    colors = {'OPEN': '#482026', 'CLOSED': '#123529', 'CHECK DATA': '#49381c'}
    color = colors.get(row['STATUS'], '#111c2e')
    return [f'background-color: {color}; color: #e2e8f0'] * len(row)


def render_fault_log(data):
    st.subheader('FAULT LOG')
    st.caption('Selected machines and opening dates · Open records first')
    log = fault_log_data(data)
    controls = st.columns([1, 3])
    with controls[0]:
        status = st.selectbox('STATUS', ['ALL', 'OPEN', 'CLOSED', 'CHECK DATA'], key='fault_status')
    with controls[1]:
        query = st.text_input('SEARCH', placeholder='Machine, description, work performed or record ID', key='fault_search')
    shown = filter_fault_log(log, status, query)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('RECORDS', len(shown))
    c2.metric('OPEN', int(shown['STATUS'].eq('OPEN').sum()))
    c3.metric('CLOSED', int(shown['STATUS'].eq('CLOSED').sum()))
    c4.metric('DOWNTIME (MIN)', fmt(shown['Durus_Dk'].sum(min_count=1)))
    if shown['STATUS'].eq('CHECK DATA').any():
        st.warning('Some records have an invalid closing date. Review CHECK DATA records.')
    if shown.empty:
        st.info('No matching fault records.')
        return
    display = shown.rename(columns={'Baslangic': 'OPENED AT', 'Makine': 'MACHINE',
        'Ariza_Tipi': 'FAULT TYPE', 'Durus_Dk': 'DOWNTIME (MIN)'})
    columns = ['OPENED AT', 'MACHINE', 'FAULT TYPE', 'DESCRIPTION', 'STATUS', 'DOWNTIME (MIN)']
    table = display[columns]
    st.dataframe(table.style.apply(fault_row_style, axis=1).format(
        {'DOWNTIME (MIN)': '{:,.1f}'}, na_rep='—'),
        use_container_width=True, height=600, hide_index=True,
        column_config={'DESCRIPTION': st.column_config.TextColumn('DESCRIPTION', width='large'),
                       'OPENED AT': st.column_config.DatetimeColumn('OPENED AT', format='DD.MM.YYYY HH:mm')})
    st.caption('RED: OPEN · GREEN: CLOSED · AMBER: CHECK DATA')
    export = display.rename(columns={'Bitis': 'CLOSED AT', 'Veri_Uyarisi': 'DATA WARNING'})[
        columns + ['CLOSED AT', 'WORK PERFORMED', 'REPLACED PARTS', 'RECORD ID', 'SOURCE STATUS', 'DATA WARNING']]
    st.download_button('DOWNLOAD FAULT LOG', export.to_csv(index=False).encode('utf-8-sig'),
        'fault_log.csv', 'text/csv', key='download_fault_log')
    with st.expander('RECORD DETAILS', expanded=False):
        selected_row = st.selectbox('SELECT RECORD', list(shown.index),
            format_func=lambda i: f"{shown.at[i, 'Baslangic']:%d.%m.%Y %H:%M} | {shown.at[i, 'Makine']} | {shown.at[i, 'STATUS']}",
            key='fault_detail')
        row = shown.loc[selected_row]
        st.caption('DESCRIPTION')
        st.text(row['DESCRIPTION'] or 'Not recorded')
        st.caption('WORK PERFORMED')
        st.text(row['WORK PERFORMED'] or 'Not recorded')
        st.caption('REPLACED PARTS')
        st.text(row['REPLACED PARTS'] or 'Not recorded')
        st.caption('RECORD ID')
        st.text(row['RECORD ID'] or 'Not recorded')
        st.caption('CLOSED AT')
        closed_text = 'Not recorded' if pd.isna(row['Bitis']) else row['Bitis'].strftime(
            '%d.%m.%Y %H:%M:%S' if row['Kapanis_Saati_Var'] else '%d.%m.%Y (time not recorded)')
        st.text(closed_text)
        if row['SOURCE STATUS']:
            st.caption('SOURCE STATUS')
            st.text(row['SOURCE STATUS'])
        if row['Veri_Uyarisi']:
            st.warning(row['Veri_Uyarisi'])
    with st.expander('FAULT LOG NOTES', expanded=False):
        st.write('This is a read-only view of the existing Google Sheets records. '
                 'Descriptions and maintenance notes are shown in their original language. '
                 'Status follows the closing field: blank = OPEN; valid closure = CLOSED. '
                 'Invalid closing dates are marked CHECK DATA. '
                 'Downtime uses the same validated durations as the KPI cards; missing or invalid values are excluded. '
                 'Search and status filters affect this log only. Existing fault reporting and closure workflows remain active.')


def main():
    st.set_page_config(page_title='BHS | Maintenance KPI v4.6 EN', page_icon='⚙️', layout='wide')
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
    

    [data-testid="stSidebar"] {background:linear-gradient(180deg,#111c2e 0%,#0d1726 100%);
      border-right:1px solid #243449}
    [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {padding:1.5rem 1.1rem}
    [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
      border-color:#293c52 !important; border-radius:12px !important}
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] [data-baseweb="input"] {
      background:#0b1524; border-color:#30445b; border-radius:9px}
    [data-testid="stSidebar"] [data-baseweb="tag"] {
      background:#163c46 !important; color:#b6f3ea !important;
      border:1px solid #28616a !important; border-radius:6px !important}
    [data-testid="stSidebar"] button {border-radius:9px; border-color:#30445b}
    [data-testid="stSidebar"] button:hover {border-color:#2dd4bf; color:#5eead4}
    .sb-brand {padding:0 0 20px; border-bottom:1px solid #293c52; margin-bottom:18px}
    .sb-kicker {font-size:10px; font-weight:700; letter-spacing:2px; color:#5eead4}
    .sb-title {font-size:23px; font-weight:700; color:#f1f5f9; margin:7px 0 3px}
    .sb-sub {font-size:11px; color:#8fa5bc; letter-spacing:.5px}
    .sb-section {font-size:11px; font-weight:700; letter-spacing:1.3px;
      color:#b6c9dc; padding:2px 0 10px}
    .sb-summary {font-size:12px; color:#8ddbd0; padding:8px 0 0}
    .sb-status {border-top:1px solid #293c52; margin-top:18px; padding-top:14px;
      color:#8fa5bc; font-size:11px; line-height:1.8}
    </style>''', unsafe_allow_html=True)
    st.sidebar.markdown('<div class="sb-brand"><div class="sb-kicker">BHS TÜRKİYE</div>'
        '<div class="sb-title">Maintenance</div><div class="sb-sub">PERFORMANCE CONTROL · v4.6</div></div>', unsafe_allow_html=True)
    filter_panel = st.sidebar.container()
    with st.sidebar.expander('DATA SETTINGS', expanded=False):
        refresh = st.checkbox('Auto refresh · 10 sec', value=True)
        if st.button('REFRESH DATA', use_container_width=True):
            load_data.clear()
        dayfirst = st.selectbox('SOURCE DATE FORMAT', ['Day / Month / Year', 'Month / Day / Year']) == 'Day / Month / Year'
    if refresh:
        st_autorefresh(interval=10000, key='live')
    connection_panel = st.sidebar.empty()
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
    connection_panel.markdown('<div class="sb-status">' +
        ('<span style="color:#fbbf24">● STALE DATA</span>' if stale else '<span style="color:#2dd4bf">● DATA CONNECTED</span>') +
        '<br>LAST SYNC · TÜRKİYE<br>' + fetched.strftime('%d %b %Y · %H:%M:%S') + '</div>', unsafe_allow_html=True)

    valid = all_data.dropna(subset=['Baslangic', 'Makine'])
    if valid.empty:
        st.warning('No records with a valid machine and start date were found.')
        st.dataframe(display_records(all_data), use_container_width=True)
        st.stop()
    machines = sorted(valid['Makine'].unique())
    with filter_panel:
        with st.container(border=True):
            st.markdown('<div class="sb-section">01 &nbsp; EQUIPMENT</div>', unsafe_allow_html=True)
            scope = st.radio('MACHINE SCOPE', ['ALL MACHINES', 'CUSTOM'], horizontal=True,
                             label_visibility='collapsed', key='machine_scope')
            if scope == 'ALL MACHINES':
                selected = machines
            else:
                selected = st.multiselect('SELECT MACHINES', machines, default=[],
                    placeholder='Search and select machines', key='custom_machines')
            st.markdown(f'<div class="sb-summary">{len(selected)} / {len(machines)} machines selected</div>', unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown('<div class="sb-section">02 &nbsp; REPORTING PERIOD</div>', unsafe_allow_html=True)
            dates = st.date_input('DATE RANGE',
                value=(valid['Baslangic'].min().date(), valid['Baslangic'].max().date()),
                format='DD/MM/YYYY', key='reporting_dates')
            st.caption('Based on fault opening date')
        with st.container(border=True):
            st.markdown('<div class="sb-section">03 &nbsp; DISPLAY</div>', unsafe_allow_html=True)
            chart_options = sorted(set([10, 15, 20, len(machines)]))
            limit = st.selectbox('MACHINES PER CHART', chart_options,
                index=chart_options.index(15),
                format_func=lambda n: f'ALL ({n})' if n == len(machines) else f'TOP {n}',
                key='chart_limit')
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
    overview, faults, reliability, records = st.tabs(['OVERVIEW', 'FAULT LOG', 'RELIABILITY', 'DATA QUALITY'])
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
    with faults:
        render_fault_log(data)
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
