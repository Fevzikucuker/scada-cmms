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
# SIDEBAR (PRO SCADA PANEL)
# =========================
st.sidebar.markdown("""
<div style="
    background: linear-gradient(180deg, #0b1220, #0f172a);
    padding: 18px;
    border-radius: 14px;
    border: 1px solid #00ffe5;
    box-shadow: 0 0 18px rgba(0,255,229,0.15);
    margin-bottom: 15px;
">
    <h2 style="
        color:#00ffe5;
        text-align:center;
        margin:0;
        font-size:18px;
    ">
        ⚙ RELIABILITY COCKPIT v1.0
    </h2>
    <p style="
        color:#94a3b8;
        text-align:center;
        font-size:12px;
        margin-top:6px;
    ">
       BHS TÜRKİYE MAINTENANCE TEAM
    </p>
</div>
""", unsafe_allow_html=True)

secili_makine = st.sidebar.multiselect(
    "🏭 Machine Selection",
    sorted(df["Makine"].dropna().unique()),
    default=sorted(df["Makine"].dropna().unique())
)

min_date = df["Baslangic"].min().date()
max_date = df["Baslangic"].max().date()

tarih_aralik = st.sidebar.date_input(
    "📅 Time Range",
    (min_date, max_date)
)

st.sidebar.markdown("---")

st.sidebar.markdown("""
<div style="
    background:#0f172a;
    padding:12px;
    border-radius:10px;
    border:1px solid rgba(0,255,229,0.2);
">
    <p style="color:#22c55e;margin:0;">🟢 SYSTEM ONLINE</p>
    <p style="color:#94a3b8;margin:0;font-size:11px;">Auto Refresh: 10s</p>
</div>
""", unsafe_allow_html=True)

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
# KPI
# =========================
df["Onceki_Bitis"] = df.groupby("Makine")["Bitis"].shift(1)
df["MTBF"] = (df["Baslangic"] - df["Onceki_Bitis"]).dt.total_seconds() / 60
df = df[df["MTBF"] > 0]

mttr = df.groupby("Makine")["Durus_Dk"].mean()
mtbf = df.groupby("Makine")["MTBF"].mean()
ariza = df["Makine"].value_counts()

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
st.title("BHS TÜRKİYE MAINTENANCE KPI TRACKING DASHBOARD")

c1, c2, c3, c4 = st.columns(4)
c1.metric("System Health", f"{risk['Score'].mean():.1f}")
c2.metric("Critical Machines", len(risk[risk["Score"] < 60]))
c3.metric("Avg MTBF", f"{mtbf.mean():.0f}")
c4.metric("Total Failures", len(df))

# =========================
# MTTR / MTBF
# =========================
st.divider()
col1, col2 = st.columns(2)

with col1:
    st.subheader("🔴 MTTR")
    st.plotly_chart(px.bar(mttr.reset_index(), x="Makine", y="Durus_Dk", color="Durus_Dk"),
                    use_container_width=True)

with col2:
    st.subheader("🟢 MTBF")
    st.plotly_chart(px.bar(mtbf.reset_index(), x="Makine", y="MTBF", color="MTBF"),
                    use_container_width=True)

# =========================
# HEALTH + TREND
# =========================
st.divider()
col3, col4 = st.columns(2)

with col3:
    st.subheader("🏭 Machine Health")
    st.plotly_chart(px.bar(risk.reset_index(), x="Makine", y="Score",
                           color="Score", color_continuous_scale="RdYlGn"),
                    use_container_width=True)

with col4:
    st.subheader("📉 Failure Trend")
    trend = df.copy()
    trend["Tarih"] = trend["Baslangic"].dt.date
    ariza_trend = trend.groupby("Tarih").size().reset_index(name="Ariza")

    st.plotly_chart(px.line(ariza_trend, x="Tarih", y="Ariza", markers=True),
                    use_container_width=True)

# =========================
# HEATMAP + PARETO
# =========================
st.divider()
col5, col6 = st.columns(2)

with col5:
    st.subheader("🔥 Heatmap")
    heat = pd.crosstab(df["Makine"], df["Ariza_Tipi"])
    st.plotly_chart(px.imshow(heat, text_auto=True, aspect="auto",
                              color_continuous_scale="Reds"),
                    use_container_width=True)

with col6:
    st.subheader("📊 Pareto")
    pareto = df["Ariza_Tipi"].value_counts().reset_index()
    pareto.columns = ["Failure Types", "Total"]

    st.plotly_chart(px.bar(pareto, x="Ariza_Tipi", y="Adet", color="Adet"),
                    use_container_width=True)

# =========================
# TABLE
# =========================
st.divider()
st.subheader("📋 FAILURE RECORDING TABLE")

table_df = df[[
    "Machines",
    "Ariza_Tipi",
    "Baslangic",
    "Bitis",
    "Durus_Dk",
    "MTBF"
]].sort_values("Baslangic", ascending=False)

st.dataframe(table_df, use_container_width=True, height=400)