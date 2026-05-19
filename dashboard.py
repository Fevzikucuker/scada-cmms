import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

# =========================
# AUTO REFRESH
# =========================
st_autorefresh(interval=10000, key="live")

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="SCADA CMMS AI v3", layout="wide")

st.markdown("""
<style>
body { background-color: #0b1220; }
h1,h2,h3 { color: #00ffe5; }
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
st.title("🏭 SCADA CMMS AI DASHBOARD v3")

overall = risk["Score"].mean()

c1, c2, c3, c4 = st.columns(4)
c1.metric("System Health", f"{overall:.1f}")
c2.metric("Critical Machines", len(risk[risk["Score"] < 60]))
c3.metric("Avg MTBF", f"{mtbf.mean():.0f}")
c4.metric("Total Failures", len(df))

st.divider()

# =========================
# MTBF + PARETO (2 COL)
# =========================
col1, col2 = st.columns(2)

with col1:
    st.subheader("📈 MTBF")
    fig_mtbf = px.bar(mtbf.reset_index(), x="Makine", y="MTBF", color="MTBF")
    st.plotly_chart(fig_mtbf, use_container_width=True)

with col2:
    st.subheader("📊 Pareto Analysis")

    pareto = df["Ariza_Tipi"].value_counts().reset_index()
    pareto.columns = ["Ariza_Tipi", "Adet"]

    fig_p = px.bar(pareto, x="Ariza_Tipi", y="Adet", color="Adet")
    st.plotly_chart(fig_p, use_container_width=True)

st.divider()

# =========================
# HEATMAP
# =========================
st.subheader("🔥 Heatmap (Machine vs Failure)")

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
# MACHINE STATUS
# =========================
st.subheader("🏭 Machine Health Overview")

fig_score = px.bar(
    risk.reset_index(),
    x="Makine",
    y="Score",
    color="Score",
    color_continuous_scale="RdYlGn"
)

st.plotly_chart(fig_score, use_container_width=True)

st.divider()

# =========================
# PDF REPORT (PRO)
# =========================
def generate_pdf():

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    content = []

    content.append(Paragraph("SCADA CMMS MANAGEMENT REPORT", styles["Title"]))
    content.append(Spacer(1, 12))

    content.append(Paragraph(f"System Health: {overall:.2f}", styles["Heading2"]))
    content.append(Paragraph(f"MTTR Avg: {mttr.mean():.2f}", styles["Normal"]))
    content.append(Paragraph(f"MTBF Avg: {mtbf.mean():.2f}", styles["Normal"]))
    content.append(Spacer(1, 12))

    top = risk.sort_values("Score").head(5)

    table_data = [["Machine", "Score", "MTTR", "MTBF", "Failures"]]

    for i, r in top.iterrows():
        table_data.append([i, f"{r['Score']:.1f}", f"{r['MTTR']:.1f}", f"{r['MTBF']:.1f}", int(r["Ariza"])])

    table = Table(table_data)
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.grey),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("GRID",(0,0),(-1,-1),0.5,colors.black)
    ]))

    content.append(table)
    doc.build(content)

    buffer.seek(0)
    return buffer

pdf = generate_pdf()

st.download_button(
    "📥 Download Management PDF",
    pdf,
    file_name="SCADA_REPORT_V3.pdf",
    mime="application/pdf"
)