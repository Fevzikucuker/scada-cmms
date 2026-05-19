import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

# =========================
# AUTO REFRESH
# =========================
st_autorefresh(interval=10000, key="live")

# =========================
# CONFIG (DAHA SIKI LAYOUT)
# =========================
st.set_page_config(page_title="SCADA CMMS AI v4.2", layout="wide")

st.markdown("""
<style>
body { background-color: #0b1220; }

.block-container {
    padding-top: 0.8rem;
    padding-bottom: 0.5rem;
}

h1,h2,h3 {
    color: #00ffe5;
    margin-top: 0px;
    padding-top: 0px;
}
</style>
""", unsafe_allow_html=True)

# =========================
# DATA LOAD
# =========================
@st.cache_data(ttl=5)
def load_data():
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRfxWf8ilCrbH4Bd8nVxeVTIuQSkCJDYIJUWEJ5SoD3GqkSVyC4f0hvDyXhm8DTJy4b3NY75dDwyGjK/pub?output=csv"
    df = pd.read_csv(url)

    df.columns = df.columns.str.strip()

    df = df.rename(columns={
        "Zaman damgası": "Baslangic",
        "İŞ MERKEZİ SEÇİMİ -  WORKCENTER": "Makine",
        "ARIZA TİPİ(FAULT TYPE)": "Ariza_Tipi",
        "CLOSING DATE": "Bitis",
        "STOPPAGE TIME": "Durus_Dk"
    })

    df["Baslangic"] = pd.to_datetime(df["Baslangic"], errors="coerce")
    df["Bitis"] = pd.to_datetime(df["Bitis"], errors="coerce")

    return df

df = load_data()

# =========================
# SIDEBAR FILTER
# =========================
st.sidebar.title("🎛 SCADA CONTROL")

secili_makine = st.sidebar.multiselect(
    "🏭 Makine Seçimi",
    sorted(df["Makine"].dropna().unique()),
    default=sorted(df["Makine"].dropna().unique())
)

min_date = df["Baslangic"].min().date()
max_date = df["Baslangic"].max().date()

tarih_aralik = st.sidebar.date_input(
    "📅 Tarih",
    (min_date, max_date)
)

df = df[df["Makine"].isin(secili_makine)]

if len(tarih_aralik) == 2:
    df = df[
        (df["Baslangic"] >= pd.to_datetime(tarih_aralik[0])) &
        (df["Baslangic"] <= pd.to_datetime(tarih_aralik[1]))
    ]

df = df.sort_values(["Makine", "Baslangic"])

# =========================
# KPI CALC
# =========================
df["Onceki_Bitis"] = df.groupby("Makine")["Bitis"].shift(1)
df["MTBF"] = (df["Baslangic"] - df["Onceki_Bitis"]).dt.total_seconds() / 60
df = df[df["MTBF"] > 0]

mttr = df.groupby("Makine")["Durus_Dk"].mean()
mtbf = df.groupby("Makine")["MTBF"].mean()
ariza = df["Makine"].value_counts()

# =========================
# HEALTH SCORE
# =========================
risk = pd.DataFrame({
    "MTTR": mttr,
    "MTBF": mtbf,
    "Ariza": ariza
}).fillna(0)

risk["Score"] = 100 - (
    (risk["MTTR"] / (risk["MTTR"].max()+1e-6)) * 40 +
    (risk["Ariza"] / (risk["Ariza"].max()+1e-6)) * 35 +
    ((risk["MTBF"].max()-risk["MTBF"]) / (risk["MTBF"].max()+1e-6)) * 25
)

risk["Score"] = risk["Score"].clip(0, 100)

# =========================
# HEADER (YUKARI ÇEKİLDİ)
# =========================
st.title("🏭 SCADA CMMS AI v4.2 - FULL FILTERED DASHBOARD")
st.caption("Real-time Industrial Reliability Monitoring System")

# =========================
# TOP KPI
# =========================
c1, c2, c3, c4 = st.columns(4)
c1.metric("System Health", f"{risk['Score'].mean():.1f}")
c2.metric("Critical Machines", len(risk[risk["Score"] < 60]))
c3.metric("Avg MTBF", f"{mtbf.mean():.0f}")
c4.metric("Total Failures", len(df))

# =========================
# MTTR + MTBF (YAN YANA)
# =========================
st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("🔴 MTTR")
    fig1 = px.bar(mttr.reset_index(), x="Makine", y="Durus_Dk", color="Durus_Dk")
    fig1.update_layout(height=400)
    st.plotly_chart(fig1, use_container_width=True)

with col2:
    st.subheader("🟢 MTBF")
    fig2 = px.bar(mtbf.reset_index(), x="Makine", y="MTBF", color="MTBF")
    fig2.update_layout(height=400)
    st.plotly_chart(fig2, use_container_width=True)

# =========================
# HEALTH + TREND (ALT SATIR)
# =========================
st.divider()

col3, col4 = st.columns(2)

with col3:
    st.subheader("🏭 Machine Health Score")
    fig3 = px.bar(risk.reset_index(), x="Makine", y="Score", color="Score",
                  color_continuous_scale="RdYlGn")
    fig3.update_layout(height=400)
    st.plotly_chart(fig3, use_container_width=True)

with col4:
    st.subheader("📉 Arıza Trend")
    trend = df.copy()
    trend["Tarih"] = trend["Baslangic"].dt.date
    ariza_trend = trend.groupby("Tarih").size().reset_index(name="Ariza")

    fig4 = px.line(ariza_trend, x="Tarih", y="Ariza", markers=True)
    fig4.update_layout(height=400)
    st.plotly_chart(fig4, use_container_width=True)

# =========================
# HEATMAP + PARETO
# =========================
st.divider()

col5, col6 = st.columns(2)

with col5:
    st.subheader("🔥 Heatmap")
    heat = pd.crosstab(df["Makine"], df["Ariza_Tipi"])
    fig5 = px.imshow(heat, text_auto=True, aspect="auto",
                     color_continuous_scale="Reds")
    fig5.update_layout(height=450)
    st.plotly_chart(fig5, use_container_width=True)

with col6:
    st.subheader("📊 Pareto")
    pareto = df["Ariza_Tipi"].value_counts().reset_index()
    pareto.columns = ["Ariza_Tipi", "Adet"]

    fig6 = px.bar(pareto, x="Ariza_Tipi", y="Adet", color="Adet")
    fig6.update_layout(height=450)
    st.plotly_chart(fig6, use_container_width=True)

# =========================
# EXCEL TABLE (EN ALT)
# =========================
st.divider()

st.subheader("📋 ARIZA KAYIT TABLOSU")

table_df = df[[
    "Makine",
    "Ariza_Tipi",
    "Baslangic",
    "Bitis",
    "Durus_Dk",
    "MTBF"
]].sort_values("Baslangic", ascending=False)

st.dataframe(table_df, use_container_width=True, height=400)