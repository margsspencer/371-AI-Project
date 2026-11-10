"""Look and feel for the web app: a light page with dark text and gold accents.

Colors are set in .streamlit/config.toml; this adds fonts, cards, and the sidebar.
Only styling lives here; nothing in it changes what the app does.
"""

GOLD = "#8A6A22"          # deep gold accent (readable on white)
GOLD_SOFT = "#E9DDBF"     # pale gold for thin borders and highlights
INK = "#1F1F1F"           # main text
MUTED = "#5F5A50"         # secondary text
LINE = "rgba(31, 31, 31, 0.10)"
CARD = "#FFFFFF"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select, h1, h2, h3, h4 {{
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
}}
.block-container {{ padding-top: 3.2rem; max-width: 1280px; }}
h1, h2, h3, h4 {{ color: {INK}; letter-spacing: -0.015em; font-weight: 700; }}
h3 {{ font-size: 1.3rem !important; margin-top: 1.4rem; }}

/* Header: a white card with a thin gold top edge */
.gp-hero {{ background: {CARD}; border: 1px solid {LINE}; border-top: 4px solid {GOLD}; border-radius: 16px;
            padding: 24px 28px; margin-bottom: 18px; box-shadow: 0 4px 18px rgba(0,0,0,0.04); }}
.gp-eyebrow {{ color: {GOLD}; font-size: 0.78rem; font-weight: 700; letter-spacing: 0.14em;
               text-transform: uppercase; margin-bottom: 6px; }}
.gp-title {{ font-size: 2.2rem; font-weight: 800; line-height: 1.1; color: {INK}; margin: 0;
             letter-spacing: -0.02em; }}
.gp-sub {{ color: {MUTED}; margin-top: 8px; font-size: 0.98rem; }}
.gp-student {{ font-size: 1.3rem; font-weight: 700; margin: 6px 0 2px; color: {INK}; }}
.gp-student span {{ color: {GOLD}; }}

/* Sidebar: light sand with dark text */
section[data-testid="stSidebar"] {{ background: #F4F0E6; border-right: 1px solid {LINE}; }}
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2 {{ color: {INK}; font-size: 1.5rem; }}
section[data-testid="stSidebar"] h3 {{ color: {GOLD}; font-size: 0.85rem !important; text-transform: uppercase;
                                      letter-spacing: 0.12em; margin-top: 1.2rem; }}
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] textarea {{ background: {CARD}; }}

/* Buttons */
.stButton button, .stFormSubmitButton button {{ border-radius: 10px; font-weight: 600; }}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"] {{
    background: {GOLD}; color: #FFFFFF; border: none; box-shadow: 0 4px 12px rgba(138,106,34,0.20);
}}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primaryFormSubmit"]:hover {{
    background: #745817; color: #FFFFFF;
}}

/* Cards: bordered containers, metrics, expanders */
div[data-testid="stVerticalBlockBorderWrapper"] {{ border-color: {LINE} !important; border-radius: 14px; }}
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]) {{ background: {CARD}; }}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"] {{ background: transparent !important; }}
div[data-testid="stMetric"] {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; padding: 14px 18px; }}
div[data-testid="stMetricValue"] {{ color: {INK}; font-weight: 800; }}
div[data-testid="stExpander"] details {{ border-color: {LINE}; border-radius: 12px; background: {CARD}; }}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 600; padding: 10px 16px; }}

/* Notices: gold edge for the advisory note, amber for "advisor review" */
div[data-testid="stAlert"] > div {{ border-radius: 12px; }}
div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) > div {{
    background: #FBF6EA; border: 1px solid {GOLD_SOFT}; border-left: 4px solid {GOLD};
}}
div[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) > div {{
    background: #FFF6E0; border: 1px solid rgba(160,110,0,0.25); border-left: 4px solid #B07D10;
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
        '<p class="gp-title">Graduation Planner</p>'
        '<div class="gp-sub">Semester-by-semester plans, career-matched electives, and a degree audit. '
        'An unofficial class project.</div>'
        '</div>'
    )


def student_html(name: str, programs: str) -> str:
    from html import escape
    return f'<div class="gp-student">{escape(name)} <span>·</span> {escape(programs)}</div>'
