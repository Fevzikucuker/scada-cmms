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
st.set_page_config(page_title="Executive SCADA Dashboard", layout="wide")

st.markdown("""
<style>
body { background-color: #0b1220; }
.block-container { padding: 1rem 2rem; }

h1 { color: #00ffe5; }
h2, h3 { color: #ffffff; }

.card {
    background-color: #111827;
    padding: 20px;
    border-radius: 15px;
    border: 1px solid #2dd4bf;
}
</style>
""", unsafe_allow_html=True)

# =========================
# DATA
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

availability = mtbf / (mtbf + mttr)

# =========================
# SIMPLE HEALTH SCORE
# =========================
risk = pd.DataFrame({
    "MTTR": mttr,
    "MTBF": mtbf
}).fillna(0)

risk["Ariza"] = ariza

risk["score"] = 100 - (
    (risk["MTTR"] / (risk["MTTR"].max()+1e-6)) * 40 +
    (risk["Ariza"] / (risk["Ariza"].max()+1e-6)) * 35 +
    ((risk["MTBF"].max()-risk["MTBF"]) / (risk["MTBF"].max()+1e-6)) * 25
)

risk["score"] = risk["score"].clip(0, 100)

# =========================
# HEADER
# =========================
st.title("🏭 EXECUTIVE SCADA DASHBOARD")
st.caption("Yönetici görünümü – sade karar paneli")

st.divider()

# =========================
# 🟢 SYSTEM STATUS
# =========================
overall_health = risk["score"].mean()

if overall_health >= 80:
    status = "🟢 STABLE"
elif overall_health >= 60:
    status = "🟡 WARNING"
else:
    status = "🔴 CRITICAL"

c1, c2, c3, c4 = st.columns(4)

c1.metric("🏭 System Health", f"{overall_health:.1f}/100", status)
c2.metric("🔴 Critical Machines", len(risk[risk["score"] < 60]))
c3.metric("⏱ Avg MTBF", f"{mtbf.mean():.0f} min")
c4.metric("⚙ Active Failures", len(df))

st.divider()

# =========================
# 🚨 TOP ACTION LIST
# =========================
st.subheader("🚨 ACTION REQUIRED (TOP 3 MACHINES)")

top3 = risk.sort_values("score").head(3)

for i, row in top3.iterrows():
    st.error(
        f"Makine: {i} | Health: {row['score']:.1f} | "
        f"MTTR: {row['MTTR']:.1f} dk | Arıza: {row['Ariza']}"
    )

st.divider()

# =========================
# 📊 SIMPLE TREND
# =========================
st.subheader("📈 SYSTEM TREND (SIMPLIFIED)")

df["date"] = df["Baslangic"].dt.date
trend = df.groupby("date").size().reset_index(name="Arıza")

fig = px.line(trend, x="date", y="Arıza", markers=True)
fig.update_layout(height=400)

st.plotly_chart(fig, use_container_width=True)

# =========================
# 🧾 SUMMARY TEXT (IMPORTANT)
# =========================
st.subheader("🧠 MANAGEMENT SUMMARY")

if overall_health >= 80:
    st.success("Sistem stabil. Kritik aksiyon gerekmiyor.")
elif overall_health >= 60:
    st.warning("Sistemde erken uyarı var. Önleyici bakım önerilir.")
else:
    st.error("Kritik durum! Bakım müdahalesi acil gerekli.")

st.divider()

# =========================
# RAW DATA (hidden mindset)
# =========================
with st.expander("📡 Raw Data (Engineer View)"):
    st.dataframe(df, use_container_width=True)