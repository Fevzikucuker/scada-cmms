import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh

# =========================
# AUTO REFRESH (5 sec)
# =========================
st_autorefresh(interval=5000, key="live_refresh")

# =========================
# PAGE CONFIG
# =========================
st.set_page_config(page_title="SCADA CMMS v3.3", layout="wide")

# =========================
# SCADA STYLE (DEĞİŞMEDİ)
# =========================
st.markdown("""
<style>
body { background-color: #070b14; }
.block-container { padding: 1rem 2rem; }
h1,h2,h3 { color: #00ffe5; }

div[data-testid="metric-container"] {
    background-color: #111827;
    border: 1px solid #00ffe5;
    padding: 12px;
    border-radius: 12px;
}

section[data-testid="stSidebar"] {
    background-color: #0b1220;
}
</style>
""", unsafe_allow_html=True)

# =========================
# GOOGLE SHEETS DATA (YENİ)
# =========================
@st.cache_data(ttl=5)
def load_data():
    url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vRfxWf8ilCrbH4Bd8nVxeVTIuQSkCJDYIJUWEJ5SoD3GqkSVyC4f0hvDyXhm8DTJy4b3NY75dDwyGjK/pub?gid=543692092&single=true&output=csv"
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
# SIDEBAR (AYNI KALDI)
# =========================
st.sidebar.title("🎛 SCADA CONTROL PANEL")

secili_makine = st.sidebar.multiselect(
    "🏭 Makine Seçimi",
    sorted(df["Makine"].dropna().unique()),
    default=sorted(df["Makine"].dropna().unique())
)

min_date = df["Baslangic"].min().date()
max_date = df["Baslangic"].max().date()

tarih_aralik = st.sidebar.date_input(
    "📅 Zaman",
    (min_date, max_date)
)

st.sidebar.success("● LIVE SYSTEM (GOOGLE SHEETS)")

# =========================
# FILTER
# =========================
df = df[df["Makine"].isin(secili_makine)]

if len(tarih_aralik) == 2:
    df = df[
        (df["Baslangic"] >= pd.to_datetime(tarih_aralik[0])) &
        (df["Baslangic"] <= pd.to_datetime(tarih_aralik[1]))
    ]

df = df.sort_values(["Makine", "Baslangic"])

# =========================
# KPI CALC (AYNI)
# =========================
df["Onceki_Bitis"] = df.groupby("Makine")["Bitis"].shift(1)
df["MTBF"] = (df["Baslangic"] - df["Onceki_Bitis"]).dt.total_seconds() / 60
df = df[df["MTBF"] > 0]

mttr = df.groupby("Makine")["Durus_Dk"].mean()
mtbf = df.groupby("Makine")["MTBF"].mean()
ariza = df["Makine"].value_counts()

availability = mtbf / (mtbf + mttr)

# =========================
# HEADER
# =========================
st.title("🏭 SCADA CMMS v3.3 (LIVE GOOGLE SHEETS)")

st.divider()

# =========================
# KPI
# =========================
c1, c2, c3, c4 = st.columns(4)

c1.metric("⏱ MTTR", f"{mttr.mean():.2f}")
c2.metric("📈 MTBF", f"{mtbf.mean():.2f}")
c3.metric("🟢 Availability", f"{availability.mean():.2f}")
c4.metric("🚨 Arıza", len(df))

st.divider()

# =========================
# CHARTS (DEĞİŞMEDİ - SADECE BÜYÜK)
# =========================
c1, c2 = st.columns(2)

with c1:
    st.subheader("🔴 MTTR")
    fig = px.bar(mttr.reset_index(), x="Makine", y="Durus_Dk", color="Durus_Dk")
    fig.update_layout(height=650, paper_bgcolor="#070b14", font_color="white")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("🟢 MTBF")
    fig = px.bar(mtbf.reset_index(), x="Makine", y="MTBF", color="MTBF")
    fig.update_layout(height=650, paper_bgcolor="#070b14", font_color="white")
    st.plotly_chart(fig, use_container_width=True)

# =========================
# PARETO
# =========================
st.subheader("📊 PARETO")

pareto = df["Ariza_Tipi"].value_counts().reset_index()
pareto.columns = ["Ariza_Tipi", "Adet"]
pareto["Kumulatif%"] = pareto["Adet"].cumsum() / pareto["Adet"].sum() * 100

fig = go.Figure()
fig.add_bar(x=pareto["Ariza_Tipi"], y=pareto["Adet"])
fig.add_trace(go.Scatter(
    x=pareto["Ariza_Tipi"],
    y=pareto["Kumulatif%"],
    yaxis="y2"
))

fig.update_layout(
    height=650,
    paper_bgcolor="#070b14",
    font_color="white",
    yaxis2=dict(overlaying="y", side="right", range=[0,100])
)

st.plotly_chart(fig, use_container_width=True)

# =========================
# LIVE TABLE
# =========================
st.subheader("📡 LIVE DATA")

st.dataframe(df, use_container_width=True)