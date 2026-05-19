import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

# =========================
# AUTO REFRESH
# =========================
st_autorefresh(interval=10000, key="live")

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="SCADA CMMS AI v4", layout="wide")

st.markdown("""
<style>
body { background-color: #0b1220; }
h1,h2,h3 { color: #00ffe5; }
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
# SIDEBAR FILTERS (GERÇEK SCADA)
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
    "📅 Tarih Aralığı",
    (min_date, max_date)
)

# =========================
# FILTER APPLY
# =========================
df = df[df["Makine"].isin(secili_makine)]

if len(tarih_aralik) == 2:
    df = df[
        (df["Baslangic"] >= pd.to_datetime(tarih_aralik[0])) &
        (df["Baslangic"] <= pd.to_datetime(tarih_aralik[1]))
    ]

df = df.sort_values(["Makine", "Baslangic"])

# =========================
# KPI CALC (FILTERED)
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
# HEADER
# =========================
st.title("🏭 SCADA CMMS AI v4 - FILTERED DASHBOARD")

c1, c2, c3, c4 = st.columns(4)

c1.metric("System Health", f"{risk['Score'].mean():.1f}")
c2.metric("Critical Machines", len(risk[risk["Score"] < 60]))
c3.metric("Avg MTBF", f"{mtbf.mean():.0f}")
c4.metric("Total Failures", len(df))

st.divider()

# =========================
# MTBF + PARETO
# =========================
col1, col2 = st.columns(2)

with col1:
    st.subheader("📈 MTBF (Filtered)")
    fig_mtbf = px.bar(mtbf.reset_index(), x="Makine", y="MTBF", color="MTBF")
    st.plotly_chart(fig_mtbf, use_container_width=True)

with col2:
    st.subheader("📊 Pareto (Filtered)")
    pareto = df["Ariza_Tipi"].value_counts().reset_index()
    pareto.columns = ["Ariza_Tipi", "Adet"]

    fig_p = px.bar(pareto, x="Ariza_Tipi", y="Adet", color="Adet")
    st.plotly_chart(fig_p, use_container_width=True)

st.divider()

# =========================
# HEATMAP
# =========================
st.subheader("🔥 Heatmap (Makine vs Arıza)")

heat = pd.crosstab(df["Makine"], df["Ariza_Tipi"])

fig_h = px.imshow(
    heat,
    text_auto=True,
    aspect="auto",
    color_continuous_scale="Reds"
)

st.plotly_chart(fig_h, use_container_width=True)

st.divider()

# =========================
# MACHINE HEALTH
# =========================
st.subheader("🏭 Machine Health Score")

fig_s = px.bar(
    risk.reset_index(),
    x="Makine",
    y="Score",
    color="Score",
    color_continuous_scale="RdYlGn"
)

st.plotly_chart(fig_s, use_container_width=True)