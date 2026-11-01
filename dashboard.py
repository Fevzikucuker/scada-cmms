"""BHS Türkiye bakım KPI ekranı v4.3.
Gereksinimler: pandas>=2.0, streamlit>=1.32, plotly>=5.18,
streamlit-autorefresh>=1.0.1. Çalıştırma: streamlit run dashboard.py
Tarih filtresi arıza AÇILIŞ tarihine uygulanır. Durus_Dk dakika kabul edilir.
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
        raise ValueError('Eksik kolonlar: ' + ', '.join(missing))
    df['Makine'] = df['Makine'].astype('string').str.strip().replace('', pd.NA)
    df['Ariza_Tipi'] = df['Ariza_Tipi'].astype('string').str.strip().fillna('Belirtilmemiş').replace('', 'Belirtilmemiş')
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
    df.loc[df['Baslangic'].isna(), 'Veri_Uyarisi'] += 'Başlangıç tarihi geçersiz; '
    df.loc[df['Makine'].isna(), 'Veri_Uyarisi'] += 'Makine eksik; '
    df.loc[~df['Acik'] & ~closed_valid, 'Veri_Uyarisi'] += 'Kapanış tarihi geçersiz; '
    df.loc[closed_valid & ~has_time, 'Veri_Uyarisi'] += 'Kapanış saati yok; aralık hesabında kullanılmaz; '
    df.loc[closed_valid & numeric.isna(), 'Veri_Uyarisi'] += 'Duruş dakikası geçersiz; '
    # Önce tüm geçmişte önceki kayıt bulunur; dönem filtresi bundan sonra uygulanır.
    df = df.sort_values(['Makine', 'Baslangic'], kind='stable', na_position='last')
    end = df['Bitis'].where(closed_valid & has_time)
    df['Onceki_Bitis'] = end.groupby(df['Makine']).shift(1)
    gap = (df['Baslangic'] - df['Onceki_Bitis']).dt.total_seconds() / 60
    df['Aralik_Dk'] = gap.where(gap > 0)
    df.loc[gap.notna() & (gap <= 0), 'Veri_Uyarisi'] += 'Çakışan/eşzamanlı kayıt; '
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
        st.info('Bu gösterge için yeterli geçerli veri yok.')
        return
    if value == 'Aralik':
        data[value] = data[value] / 60
    fig = px.bar(data, x=value, y='Makine', orientation='h', text=value,
                 labels={value: label, 'Makine': ''}, color_discrete_sequence=[color])
    fig.update_traces(texttemplate='%{x:.1f}', textposition='outside', cliponaxis=False)
    fig.update_yaxes(categoryorder='array', categoryarray=data['Makine'].tolist()[::-1], automargin=True)
    if value == 'Score':
        fig.update_traces(marker=dict(color=data[value], colorscale='RdYlGn', cmin=0, cmax=100))
        fig.update_xaxes(range=[0, 110])
        fig.add_vline(x=60, line_dash='dot', line_color='#fb7185')
    st.plotly_chart(style_chart(fig, max(420, len(data)*30+80)), use_container_width=True, key=key)


def fmt(value, digits=1):
    return '—' if pd.isna(value) else f'{value:,.{digits}f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def main():
    st.set_page_config(page_title='BHS | Bakım KPI v4.3', page_icon='⚙️', layout='wide')
    st.markdown('''<style>
    .stApp {background:#0b1220; color:#dbe7f3}
    [data-testid="stSidebar"] {background:#111c2e}
    .block-container {padding-top:2rem; padding-bottom:2rem}
    [data-testid="stMetric"] {background:#111c2e; border:1px solid #22344c;
      border-radius:12px; padding:16px; border-top:3px solid #2dd4bf}
    h1 {font-size:1.85rem !important} h2,h3 {color:#5eead4 !important}
    </style>''', unsafe_allow_html=True)
    st.sidebar.title('⚙ Reliability Cockpit')
    st.sidebar.caption('BHS TÜRKİYE · BAKIM EKİBİ · v4.3')
    refresh = st.sidebar.checkbox('10 saniyede bir yenile', value=True)
    if refresh:
        st_autorefresh(interval=10000, key='live')
    if st.sidebar.button('Veriyi şimdi yenile'):
        load_data.clear()
    dayfirst = st.sidebar.selectbox('Kaynak tarih düzeni', ['Gün / Ay / Yıl', 'Ay / Gün / Yıl']) == 'Gün / Ay / Yıl'
    stale = False
    try:
        raw, fetched = load_data(CSV_URL)
        st.session_state['last_good'] = (raw, fetched)
    except Exception as exc:
        if 'last_good' not in st.session_state:
            st.error('Veri kaynağı okunamadı. Google Sheets CSV yayın bağlantısını kontrol edin.')
            st.caption(str(exc))
            st.stop()
        raw, fetched = st.session_state['last_good']
        stale = True
        st.warning('Veri yenilenemedi. Son başarılı okumadaki kayıtlar gösteriliyor.')
    try:
        all_data = prepare_data(raw, dayfirst)
    except Exception as exc:
        st.error(f'Veri hazırlanamadı: {exc}')
        st.stop()
    st.sidebar.caption(('⚠ Eski veri' if stale else '✓ CSV verisi okundu') + '\n\nSon başarılı okuma (Türkiye): ' + fetched.strftime('%d.%m.%Y %H:%M:%S'))
    valid = all_data.dropna(subset=['Baslangic', 'Makine'])
    if valid.empty:
        st.warning('Geçerli makine ve başlangıç tarihi içeren kayıt bulunamadı.')
        st.dataframe(all_data, use_container_width=True)
        st.stop()
    machines = sorted(valid['Makine'].unique())
    selected = st.sidebar.multiselect('Makine seçimi', machines, default=machines)
    dates = st.sidebar.date_input('Arıza açılış tarih aralığı',
        value=(valid['Baslangic'].min().date(), valid['Baslangic'].max().date()))
    limit = st.sidebar.selectbox('Grafik başına makine sayısı', sorted(set([10, 15, 20, len(machines)])), index=1)
    st.title('BHS TÜRKİYE | Bakım Performansı')
    st.caption('Arıza kayıtları · Onarım süreleri · Bakım öncelikleri')
    if len(dates) != 2:
        st.info('Başlangıç ve bitiş tarihini seçin.')
        st.stop()
    period = select_period(valid, dates[0], dates[1])
    data = period[period['Makine'].isin(selected)].copy()
    if data.empty:
        st.info('Seçilen makine ve tarih aralığında kayıt bulunmuyor.')
        st.stop()
    summary = add_score(summarize(period)).reindex([m for m in selected if m in set(data['Makine'])])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Toplam arıza kaydı', len(data))
    c2.metric('Açık kayıt', int(data['Acik'].sum()))
    c3.metric('Kayıtlı duruş (saat)', fmt(data['Durus_Dk'].sum(min_count=1)/60))
    c4.metric('Ort. onarım / MTTR (dk)', fmt(data['Durus_Dk'].mean()))
    st.caption('Kartlar seçili dönemde açılan kayıtlara aittir. Açık kayıt = kapanış alanı boş. '
               'Duruş ve MTTR yalnızca geçerli kapanış ve süre içeren kayıtlardan hesaplanır; '
               'duruş toplamı kayıtların toplamıdır, çakışmalar birleştirilmez.')
    st.caption(f"Süre hesabına katılan kayıt: {data['Durus_Dk'].count()} / {len(data)} · "
               f"Geçerli arızalar arası süre: {data['Aralik_Dk'].count()}")
    overview, reliability, records = st.tabs(['📊 Arıza analizi', '🏭 Makine göstergeleri', '📋 Kayıtlar ve veri kalitesi'])
    with overview:
        left, right = st.columns(2)
        with left:
            st.subheader('En uzun ortalama onarım')
            horizontal(summary, 'MTTR', 'Ortalama onarım (dk)', '#fb7185', 'mttr', limit)
        with right:
            st.subheader('En çok arıza kaydı')
            horizontal(summary, 'Ariza', 'Arıza kaydı (adet)', '#38bdf8', 'counts', limit)
        left, right = st.columns(2)
        with left:
            st.subheader('Arıza trendi')
            frequency = st.radio('Gruplama', ['Günlük', 'Haftalık', 'Aylık'], horizontal=True, index=1)
            rule = {'Günlük':'D', 'Haftalık':'W-MON', 'Aylık':'MS'}[frequency]
            daily = data.groupby(data['Baslangic'].dt.normalize()).size().reindex(pd.date_range(dates[0], dates[1]), fill_value=0)
            trend = daily.resample(rule, label='left', closed='left').sum().rename('Arıza').rename_axis('Tarih').reset_index()
            st.plotly_chart(style_chart(px.line(trend, x='Tarih', y='Arıza', markers=True, color_discrete_sequence=['#2dd4bf'])), use_container_width=True)
        with right:
            st.subheader('Arıza tipine göre Pareto')
            pareto = data['Ariza_Tipi'].value_counts().rename_axis('Tip').reset_index(name='Adet')
            pareto['Yuzde'] = pareto['Adet'].cumsum()/pareto['Adet'].sum()*100
            fig = make_subplots(specs=[[{'secondary_y':True}]])
            fig.add_trace(go.Bar(x=pareto['Tip'], y=pareto['Adet'], name='Arıza adedi', marker_color='#38bdf8'), secondary_y=False)
            fig.add_trace(go.Scatter(x=pareto['Tip'], y=pareto['Yuzde'], name='Kümülatif %', mode='lines+markers', line_color='#fbbf24'), secondary_y=True)
            fig.update_yaxes(title_text='Adet', secondary_y=False)
            fig.update_yaxes(title_text='Kümülatif %', range=[0, 105], secondary_y=True)
            fig.add_hline(y=80, line_dash='dot', line_color='#94a3b8', secondary_y=True)
            fig.update_layout(legend=dict(orientation='h', y=1.15))
            st.plotly_chart(style_chart(fig, 490), use_container_width=True)
        st.subheader('Makine / arıza tipi yoğunluğu')
        heat = pd.crosstab(data['Makine'], data['Ariza_Tipi'])
        heat = heat.loc[heat.sum(axis=1).sort_values(ascending=False).index]
        fig = px.imshow(heat, text_auto=True, aspect='auto', color_continuous_scale='Blues', labels=dict(x='Arıza tipi', y='Makine', color='Adet'))
        st.plotly_chart(style_chart(fig, max(420, len(heat)*29+100)), use_container_width=True)
    with reliability:
        st.info('Çalışma saati verisi olmadığı için gerçek çalışma süresine dayalı MTBF hesaplanmıyor. '
                'Aşağıdaki süre, önceki arızanın kapanışı ile sonraki arızanın açılışı arasındaki takvim süresidir; gece ve tatilleri içerir.')
        c1, c2, c3 = st.columns(3)
        c1.metric('Arızalar arası takvim süresi (saat)', fmt(data['Aralik_Dk'].mean()/60))
        c2.metric('Göreli sağlık puanı / 100', fmt(summary['Score'].mean()))
        c3.metric('Puanı 60 altında olan makine', int((summary['Score'] < 60).sum()))
        st.caption(f"Puan hesaplanan makine: {summary['Score'].count()} / {len(summary)}. Eksik veri sıfır puana dönüştürülmez.")
        left, right = st.columns(2)
        with left:
            st.subheader('Arızalar arası takvim süresi')
            horizontal(summary, 'Aralik', 'Takvim süresi (saat)', '#2dd4bf', 'interval', limit)
        with right:
            st.subheader('En düşük göreli sağlık puanları')
            horizontal(summary, 'Score', 'Göreli puan / 100', '#2dd4bf', 'health', limit)
        with st.expander('Hesaplama yöntemleri ve sınırlar', expanded=True):
            st.markdown('''- **MTTR:** geçerli kapanmış kayıtların `STOPPAGE TIME` ortalaması (dakika). Bu alan bekleme süresi de içeriyorsa saf aktif onarım süresi değildir.
- **Takvim aralığı:** tüm geçmişte aynı makinenin bir önceki kaydının kapanışından mevcut kaydın açılışına kadar geçen pozitif süre. Dönem öncesi kapanış kullanılabilir. Saati eksik/açık/bozuk önceki kapanış ve çakışmalar ortalamaya alınmaz; kayıtlar silinmez.
- **Göreli puan:** 100 − (normalize MTTR × 40 + normalize arıza adedi × 35 + ters normalize takvim aralığı × 25). Aynı dönemdeki tüm makineler referans alınır; makine seçimi referansı değiştirmez. Tarih aralığı ve kaynak kayıtları değişince puan değişebilir.
- **60 puan eşiği** bir görüntüleme kuralıdır; standart bir risk veya kullanılabilirlik sınırı değildir. Bu puan gerçek MTBF yerine takvim aralığı kullanır ve bakım önceliklendirmesine yardımcı bir göstergedir.
- Sabit hedefler henüz tanımlanmadığından yeni hedef değerler varsayılmadı. Üretim çalışma saatleri eklendiğinde MTBF ve hedef bazlı puanlama ayrıca kurulmalıdır.''')
        display = summary.rename(columns={'Ariza':'Arıza adedi', 'Acik':'Açık kayıt', 'MTTR':'MTTR (dk)', 'Durus':'Duruş (dk)', 'Gecerli_Durus':'Süreli kayıt', 'Aralik':'Takvim aralığı (dk)', 'Aralik_Adet':'Aralık sayısı', 'Score':'Göreli puan'})
        st.dataframe(display, use_container_width=True)
    with records:
        st.info('Kapanış yalnızca tarih içeriyorsa tabloda 00:00:00 görünebilir; bu gerçek kapanış saati değildir. Kapanis_Saati_Var alanı bu ayrımı gösterir. Saat eksikse sonraki arızanın takvim aralığı hesaplanmaz.')
        st.caption('Tarih yorumlama varsayılanı Gün/Ay/Yıl. Kaynak Ay/Gün/Yıl ise soldan değiştirin. STOPPAGE TIME sayısal dakika olmalıdır.')
        columns = ['Makine', 'Ariza_Tipi', 'Baslangic', 'Bitis', 'Acik', 'Durus_Dk', 'Aralik_Dk', 'Kapanis_Saati_Var', 'Veri_Uyarisi']
        table = data[columns].sort_values('Baslangic', ascending=False)
        st.dataframe(table, use_container_width=True, height=480)
        st.download_button('Seçili kayıtları CSV indir', table.to_csv(index=False).encode('utf-8-sig'), 'bakim_kayitlari.csv', 'text/csv')
        excluded = all_data[all_data['Baslangic'].isna() | all_data['Makine'].isna()]
        st.caption(f'Kaynağın tamamında tarih/makine eksikliği nedeniyle dönem analizine alınamayan kayıt: {len(excluded)}')
        if not excluded.empty:
            st.dataframe(excluded[columns], use_container_width=True)
        issues = data[data['Veri_Uyarisi'].ne('')]
        st.caption(f'Seçili dönemde veri uyarısı bulunan kayıt: {len(issues)}')
        if not issues.empty:
            st.dataframe(issues[columns], use_container_width=True)


if __name__ == '__main__':
    main()
