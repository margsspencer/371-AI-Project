"""Look and feel for the web app: muted gold page with black text, black sidebar.

Colors are set in .streamlit/config.toml; this adds fonts, cards, and the sidebar.
Only styling lives here; nothing in it changes what the app does.
"""

GOLD = "#C9A44C"          # bright gold, used on black
INK = "#111111"           # black text on the gold page
LINE = "rgba(17, 17, 17, 0.22)"
CARD = "#E3D5AA"          # slightly lighter than the page
SIDEBAR_TEXT = "#F2EEE3"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select, h1, h2, h3, h4 {{
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
}}
.block-container {{ padding-top: 2.2rem; max-width: 1280px; }}
h1, h2, h3, h4 {{ color: {INK}; letter-spacing: -0.015em; font-weight: 700; }}
h3 {{ font-size: 1.3rem !important; margin-top: 1.4rem; }}

/* Header: a black card with gold lettering */
.gp-hero {{ background: #121212; border-radius: 18px; padding: 26px 30px; margin-bottom: 18px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.18); }}
.gp-eyebrow {{ color: {GOLD}; font-size: 0.78rem; font-weight: 700; letter-spacing: 0.16em;
               text-transform: uppercase; margin-bottom: 6px; }}
.gp-title {{ font-size: 2.3rem; font-weight: 800; line-height: 1.1; color: #F7F2E4; margin: 0;
             letter-spacing: -0.02em; }}
.gp-title span {{ color: {GOLD}; }}
.gp-sub {{ color: #C9C1AE; margin-top: 8px; font-size: 0.98rem; }}
.gp-student {{ font-size: 1.3rem; font-weight: 700; margin: 6px 0 2px; color: {INK}; }}
.gp-student span {{ color: #8A6D24; }}

/* Sidebar: black with light text (inputs keep the light-gold fields with black text) */
section[data-testid="stSidebar"] {{ background: #111111; }}
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2 {{ color: {GOLD}; font-size: 1.5rem; }}
section[data-testid="stSidebar"] h3 {{ color: {GOLD}; font-size: 0.95rem !important; text-transform: uppercase;
                                      letter-spacing: 0.12em; margin-top: 1.2rem; }}
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] label,
section[data-testid="stSidebar"] [data-testid="stCheckbox"] p,
section[data-testid="stSidebar"] [data-testid="stRadio"] p,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{ color: {SIDEBAR_TEXT}; }}
section[data-testid="stSidebar"] [data-testid="stTooltipIcon"] svg {{ stroke: {SIDEBAR_TEXT}; }}
section[data-testid="stSidebar"] .stButton button, section[data-testid="stSidebar"] .stButton button p {{ background: {GOLD}; color: #111 !important; border: none; font-weight: 700; }}

/* Buttons on the page: black with gold text */
.stButton button, .stFormSubmitButton button {{ border-radius: 10px; font-weight: 600; }}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"] {{
    background: #141414; color: {GOLD}; border: none; box-shadow: 0 6px 16px rgba(0,0,0,0.2);
}}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primaryFormSubmit"]:hover {{
    background: #000; color: #E3C36F;
}}

/* Cards: bordered containers, metrics, expanders */
div[data-testid="stVerticalBlockBorderWrapper"] {{ border-color: {LINE} !important; border-radius: 14px; }}
div[data-testid="stMetric"] {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; padding: 14px 18px; }}
div[data-testid="stMetricValue"] {{ color: {INK}; font-weight: 800; }}
div[data-testid="stExpander"] details {{ border-color: {LINE}; border-radius: 12px; background: {CARD}; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 600; padding: 10px 16px; }}

/* Notices: black edge for the advisory note, amber for "advisor review" */
div[data-testid="stAlert"] > div {{ border-radius: 12px; }}
div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) > div {{
    background: {CARD}; border: 1px solid {LINE}; border-left: 4px solid #111;
}}
div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) > div {{
    background: #E9D9A6; border: 1px solid rgba(138,94,0,0.35); border-left: 4px solid #8A5E00;
}}
div[data-testid="stAlertContentInfo"], div[data-testid="stAlertContentWarning"] {{ color: {INK}; }}

div[data-testid="stDecoration"] {{ display: none; }}
div[data-testid="stDataFrame"] {{ border-radius: 10px; overflow: hidden; }}
</style>
"""


def hero_html(bulletin_year: str) -> str:
    return (
        '<div class="gp-hero">'
        f'<div class="gp-eyebrow">Wake Forest · {bulletin_year} Undergraduate Bulletin</div>'
        '<p class="gp-title">Graduation <span>Calendar</span> Planner</p>'
        '<div class="gp-sub">Semester-by-semester plans, career-matched electives, and a degree audit. '
        'An unofficial class project.</div>'
        '</div>'
    )


def student_html(name: str, programs: str) -> str:
    from html import escape
    return f'<div class="gp-student">{escape(name)} <span>·</span> {escape(programs)}</div>'
