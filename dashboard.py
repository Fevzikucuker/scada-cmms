import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# =========================
# AUTO REFRESH
# =========================
st_autorefresh(interval=10000, key="live")

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="SCADA CMMS AI v2", layout="wide")

st.markdown("""
<style>
body { background-color: #0b1220; }
h1,h2,h3 { color: #00ffe5; }

.card {
    background-color: #111827;
    padding: 15px;
    border-radius: 12px;
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
st.title("🏭 SCADA CMMS AI v2 – Hybrid Dashboard")

st.divider()

# =========================
# EXECUTIVE SUMMARY
# =========================
overall = risk["Score"].mean()

col1, col2, col3, col4 = st.columns(4)

col1.metric("🏭 System Health", f"{overall:.1f}/100")
col2.metric("🔴 Critical Machines", len(risk[risk["Score"] < 60]))
col3.metric("⏱ Avg MTBF", f"{mtbf.mean():.0f} min")
col4.metric("⚙ Total Failures", len(df))

st.divider()

# =========================
# MACHINE STATUS (IMPORTANT PART RESTORED)
# =========================
st.subheader("🏭 MACHINE STATUS OVERVIEW")

risk_sorted = risk.sort_values("Score")

fig = px.bar(
    risk_sorted.reset_index(),
    x="Makine",
    y="Score",
    color="Score",
    text="Score",
    color_continuous_scale="RdYlGn"
)

fig.update_layout(height=500)
st.plotly_chart(fig, use_container_width=True)

# =========================
# TOP CRITICAL
# =========================
st.subheader("🚨 Critical Machines")

for i, row in risk_sorted.head(5).iterrows():
    st.error(f"{i} → Score: {row['Score']:.1f} | MTTR: {row['MTTR']:.1f} | Arıza: {row['Ariza']}")

st.divider()

# =========================
# TREND
# =========================
st.subheader("📈 Failure Trend")

trend = df.groupby(df["Baslangic"].dt.date).size().reset_index(name="Arıza")

fig2 = px.line(trend, x="Baslangic", y="Arıza", markers=True)
st.plotly_chart(fig2, use_container_width=True)

# =========================
# PDF EXPORT
# =========================
st.subheader("📄 RAPOR EXPORT")

def create_pdf(data):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)

    p.drawString(50, 800, "SCADA CMMS AI REPORT")
    p.drawString(50, 780, f"System Health: {overall:.2f}")

    y = 740
    for i, row in risk_sorted.head(10).iterrows():
        p.drawString(50, y, f"{i} | Score: {row['Score']:.1f} | MTTR: {row['MTTR']:.1f}")
        y -= 20

    p.save()
    buffer.seek(0)
    return buffer

pdf = create_pdf(risk)

st.download_button(
    "📥 PDF Rapor İndir",
    pdf,
    file_name="scada_report.pdf",
    mime="application/pdf"
)

# =========================
# RAW DATA (ENGINEER VIEW)
# =========================
with st.expander("Engineer View"):
    st.dataframe(df, use_container_width=True)