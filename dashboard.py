import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

# =========================
# AUTO REFRESH
# =========================
st_autorefresh(interval=5000, key="live")

# =========================
# PAGE CONFIG
# =========================
st.set_page_config(page_title="SCADA CMMS AI v3.5", layout="wide")

# =========================
# STYLE
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
# FILTERS
# =========================
secili_makine = st.multiselect(
    "🏭 Makine",
    sorted(df["Makine"].dropna().unique()),
    default=sorted(df["Makine"].dropna().unique())
)

df = df[df["Makine"].isin(secili_makine)]
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
availability = mtbf / (mtbf + mttr)

# =========================
# AI HEALTH SCORE
# =========================
risk = pd.DataFrame({
    "MTTR": mttr,
    "MTBF": mtbf
}).fillna(0)

risk["Ariza_Sayisi"] = ariza

risk["MTTR_N"] = (risk["MTTR"] - risk["MTTR"].min()) / (risk["MTTR"].max() - risk["MTTR"].min() + 1e-6)
risk["ARIZA_N"] = (risk["Ariza_Sayisi"] - risk["Ariza_Sayisi"].min()) / (risk["Ariza_Sayisi"].max() - risk["Ariza_Sayisi"].min() + 1e-6)
risk["MTBF_N"] = (risk["MTBF"].max() - risk["MTBF"]) / (risk["MTBF"].max() - risk["MTBF"].min() + 1e-6)

risk["Health_Score"] = 100 - (
    risk["MTTR_N"] * 40 +
    risk["ARIZA_N"] * 35 +
    risk["MTBF_N"] * 25
)

risk["Health_Score"] = risk["Health_Score"].clip(0, 100)

# =========================
# HEADER
# =========================
st.title("🏭 SCADA CMMS AI v3.5 CONTROL ROOM")
st.divider()

# =========================
# KPI CARDS
# =========================
c1, c2, c3, c4 = st.columns(4)

c1.metric("MTTR", f"{mttr.mean():.2f}")
c2.metric("MTBF", f"{mtbf.mean():.2f}")
c3.metric("Availability", f"{availability.mean():.2f}")
c4.metric("Arıza", len(df))

st.divider()

# =========================
# TOP 5 CRITICAL MACHINES
# =========================
st.subheader("🚨 TOP 5 KRİTİK MAKİNE")

top5 = risk.sort_values("Health_Score").head(5)

st.dataframe(
    top5[["Health_Score", "MTTR", "MTBF", "Ariza_Sayisi"]],
    use_container_width=True
)

# =========================
# HEALTH SCORE CHART
# =========================
st.subheader("🧠 MACHINE HEALTH SCORE")

fig = px.bar(
    risk.reset_index(),
    x="Makine",
    y="Health_Score",
    color="Health_Score",
    color_continuous_scale="RdYlGn",
    text="Health_Score"
)

fig.update_traces(texttemplate='%{text:.1f}', textposition='outside')
fig.update_layout(height=600)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# =========================
# KPI TREND
# =========================
st.subheader("📊 KPI TREND")

def trend(s):
    return "📈 UP" if s.mean() > s.iloc[0] else "📉 DOWN"

c1, c2, c3 = st.columns(3)

c1.metric("MTTR Trend", f"{mttr.mean():.2f}", trend(mttr))
c2.metric("MTBF Trend", f"{mtbf.mean():.2f}", trend(mtbf))
c3.metric("Availability Trend", f"{availability.mean():.2f}", trend(availability))

st.divider()

# =========================
# WEEKLY TREND
# =========================
st.subheader("📈 WEEKLY ARIZA TREND")

df["Hafta"] = df["Baslangic"].dt.to_period("W").astype(str)

weekly = df.groupby("Hafta").size().reset_index(name="Ariza")

fig2 = px.line(weekly, x="Hafta", y="Ariza", markers=True)

fig2.update_layout(height=500)

st.plotly_chart(fig2, use_container_width=True)

# =========================
# TABLE
# =========================
st.subheader("📡 LIVE DATA")
st.dataframe(df, use_container_width=True)