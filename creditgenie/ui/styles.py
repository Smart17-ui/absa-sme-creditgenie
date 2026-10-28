"""Look & feel - Absa-branded header, step cards, KPI tiles, AI insight panel and sidebar."""
import streamlit as st


CSS = """
<style>
  [data-testid="stAppDeployButton"], #MainMenu, footer { display: none; }
  [data-testid="stMainBlockContainer"] { max-width: 1240px; }
  h1, h2, h3, h4 { letter-spacing: -0.015em; }

  /* Cards: white surfaces on a soft grey canvas */
  [class*="st-key-cg_card_"] {
    background: #FFFFFF; border-color: #E6E1E7 !important; border-radius: 14px !important;
    box-shadow: 0 1px 2px rgba(23, 19, 26, 0.04), 0 6px 20px -8px rgba(23, 19, 26, 0.08);
    padding: 1.1rem 1.25rem !important;
  }
  .st-key-cg_card_trust { background: linear-gradient(90deg, #FFF5F7 0%, #FFFFFF 70%); border-color: #F5D6DD !important; }

  /* Primary actions */
  [data-testid="stBaseButton-primary"] {
    background: linear-gradient(135deg, #A50D29 0%, #C8102E 55%, #E4234A 100%); border: none;
    box-shadow: 0 6px 16px -6px rgba(200, 16, 46, 0.55); font-weight: 600; letter-spacing: 0.01em;
    transition: transform .12s ease, box-shadow .12s ease;
  }
  [data-testid="stBaseButton-primary"]:hover { transform: translateY(-1px); box-shadow: 0 10px 22px -8px rgba(200, 16, 46, 0.6); }
  [data-testid="stBaseButton-primary"]:disabled { background: #E9E4EA; box-shadow: none; transform: none; }

  /* Hero */
  .cg-hero {
    position: relative; overflow: hidden; color: #fff; border-radius: 18px; padding: 28px 32px;
    background:
      radial-gradient(600px 240px at 92% -20%, rgba(255, 90, 125, 0.55), transparent 60%),
      radial-gradient(420px 220px at 70% 130%, rgba(122, 63, 196, 0.35), transparent 60%),
      linear-gradient(120deg, #1A0F17 0%, #4A0A22 45%, #A50D29 100%);
    box-shadow: 0 18px 40px -22px rgba(74, 10, 34, 0.7);
    display: flex; gap: 28px; align-items: flex-end; justify-content: space-between; flex-wrap: wrap;
  }
  .cg-hero::before {
    content: ""; position: absolute; inset: 0; pointer-events: none; opacity: 0.18;
    background-image: linear-gradient(rgba(255,255,255,.08) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(255,255,255,.08) 1px, transparent 1px);
    background-size: 28px 28px;
    -webkit-mask-image: linear-gradient(90deg, transparent 0%, #000 60%);
    mask-image: linear-gradient(90deg, transparent 0%, #000 60%);
  }
  .cg-hero.cg-analyst {
    background:
      radial-gradient(600px 240px at 92% -20%, rgba(80, 130, 255, 0.35), transparent 60%),
      radial-gradient(420px 220px at 70% 130%, rgba(228, 35, 74, 0.35), transparent 60%),
      linear-gradient(120deg, #0F0B14 0%, #23142A 50%, #5A0B27 100%);
  }
  .cg-hero-main { position: relative; flex: 1 1 420px; min-width: 0; }
  .cg-hero-top { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .cg-hero-logo {
    width: 40px; height: 40px; flex: none; border-radius: 50%; background: #FFFFFF; padding: 4px;
    box-shadow: 0 6px 18px -6px rgba(0, 0, 0, 0.5);
  }
  .cg-hero-role {
    display: inline-flex; align-items: center; gap: 8px; font-size: 0.72rem; font-weight: 600;
    letter-spacing: 0.12em; text-transform: uppercase; padding: 4px 10px; border-radius: 999px;
    background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.18);
  }
  .cg-dot { width: 7px; height: 7px; border-radius: 50%; background: #4ADE80; animation: cg-pulse 2s infinite; }
  @keyframes cg-pulse { 0% { box-shadow: 0 0 0 0 rgba(74,222,128,.55); } 70% { box-shadow: 0 0 0 8px rgba(74,222,128,0); }
                        100% { box-shadow: 0 0 0 0 rgba(74,222,128,0); } }
  .cg-hero h1 { color: #fff; font-size: 2rem; font-weight: 800; letter-spacing: -0.03em; margin: 12px 0 0; padding: 0; line-height: 1.15; }
  .cg-hero h1 em { font-style: normal; background: linear-gradient(90deg, #FFB3C2, #FFFFFF 70%);
                   -webkit-background-clip: text; background-clip: text; color: transparent; }
  .cg-hero p { color: rgba(255,255,255,0.82); margin: 8px 0 0; font-size: 1rem; max-width: 640px; line-height: 1.5; }
  .cg-chips { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; }
  .cg-chip {
    display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 999px; font-size: 0.78rem;
    background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.2); color: rgba(255,255,255,0.92);
  }
  .cg-chip::before { content: "\\2726"; font-size: 0.7rem; color: #FFB3C2; }
  .cg-hero-stats {
    position: relative; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px;
    background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.16); border-radius: 12px; overflow: hidden;
    flex: 0 1 420px;
  }
  .cg-hero-stat { background: rgba(20, 10, 18, 0.45); padding: 12px 14px; min-width: 0; }
  .cg-hero-stat b { display: block; font-size: 1.05rem; font-weight: 700; color: #fff; white-space: nowrap;
                    overflow: hidden; text-overflow: ellipsis; }
  .cg-hero-stat span { font-size: 0.68rem; letter-spacing: 0.06em; text-transform: uppercase; color: rgba(255,255,255,0.65); }

  /* Step headers */
  .cg-step { display: flex; align-items: center; gap: 12px; font-size: 1.08rem; font-weight: 700; color: #17131A; }
  .cg-step-num {
    display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; flex: none;
    border-radius: 9px; background: linear-gradient(135deg, #A50D29, #E4234A); color: #fff; font-size: 0.85rem;
    font-weight: 700; box-shadow: 0 4px 10px -4px rgba(200, 16, 46, 0.6);
  }
  .cg-sub { color: #6E6572; font-size: 0.9rem; margin: 2px 0 8px 42px; }
  .cg-sms {
    background: #17131A; color: #F4EEF2; border-radius: 12px; padding: 12px 16px;
    font-family: "JetBrains Mono", ui-monospace, Consolas, monospace; font-size: 0.85rem; line-height: 1.5;
    border-left: 3px solid #E4234A;
  }

  /* Application progress tracker */
  .cg-track { display: flex; gap: 8px; margin: 12px 0 6px; flex-wrap: wrap; }
  .cg-track-step { flex: 1 1 120px; border-top: 4px solid #ECE7ED; padding-top: 8px; font-size: 0.82rem; color: #8F8693; }
  .cg-track-step.done { border-color: #13804A; color: #17131A; }
  .cg-track-step.now { border-color: #C8102E; color: #17131A; font-weight: 600; }

  /* KPI tiles: wrap onto a new row instead of truncating on narrow screens */
  .cg-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 10px 0 6px; }
  .cg-kpi { position: relative; border: 1px solid #ECE7ED; border-radius: 12px; padding: 14px 16px;
            background: linear-gradient(180deg, #FFFFFF 0%, #FBFAFC 100%); min-width: 0; overflow: hidden; }
  .cg-kpi::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: #ECE7ED; }
  .cg-kpi:first-child::before { background: linear-gradient(180deg, #A50D29, #E4234A); }
  .cg-kpi-label { color: #6E6572; font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
  .cg-kpi-value { font-size: 1.7rem; font-weight: 750; color: #17131A; line-height: 1.25; margin-top: 6px; white-space: nowrap;
                  font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
  .cg-unit { font-size: 0.95rem; font-weight: 500; color: #8F8693; margin-left: 3px; }
  .cg-cur { font-size: 0.78rem; font-weight: 600; color: #8F8693; margin-right: 5px; letter-spacing: 0.04em; }
  .cg-kpi-note { color: #6E6572; font-size: 0.8rem; margin-top: 6px; }
  .cg-meter { position: relative; height: 6px; border-radius: 3px; margin-top: 10px;
              background: linear-gradient(90deg, #FBE3E7 0 40%, #FCEFD9 40% 70%, #DDF2E5 70% 100%); }
  .cg-meter-fill { height: 6px; border-radius: 3px; }
  .cg-meter-tick { position: absolute; top: -2px; width: 2px; height: 10px; background: #fff; }

  /* Generative AI insight panel */
  .cg-ai {
    border-radius: 14px; padding: 16px 18px; border: 1px solid transparent;
    background-image: linear-gradient(#FFFFFF, #FFFFFF), linear-gradient(135deg, #E4234A, #7A3FC4 60%, #2457C5);
    background-origin: border-box; background-clip: padding-box, border-box;
  }
  .cg-ai-head { display: flex; align-items: center; gap: 8px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em;
                text-transform: uppercase; color: #7A3FC4; }
  .cg-ai-spark { width: 22px; height: 22px; border-radius: 7px; display: inline-flex; align-items: center; justify-content: center;
                 color: #fff; font-size: 0.8rem; background: linear-gradient(135deg, #E4234A, #7A3FC4); }
  .cg-ai-body { margin-top: 10px; color: #2A232D; font-size: 0.95rem; line-height: 1.6; }
  .cg-ai-foot { margin-top: 10px; color: #8F8693; font-size: 0.75rem; }

  /* Sidebar (dark rail) */
  .cg-side-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .cg-logo {
    width: 42px; height: 42px; border-radius: 50%; flex: none; background: #FFFFFF; padding: 4px;
    box-shadow: 0 6px 16px -6px rgba(228, 35, 74, 0.7);
  }
  .cg-side-name { font-weight: 800; font-size: 1.12rem; color: #FFFFFF; letter-spacing: -0.02em; line-height: 1.1; }
  .cg-side-name span { color: #FF8BA3; }
  .cg-side-tag { color: #A898A3; font-size: 0.74rem; letter-spacing: 0.02em; }
  .cg-side-card { background: #241620; border: 1px solid #3A2833; border-radius: 12px; padding: 12px 14px; }
  .cg-side-label { font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase; color: #A898A3; margin-bottom: 6px; font-weight: 700; }
  .cg-row { display: flex; justify-content: space-between; gap: 8px; font-size: 0.85rem; padding: 4px 0;
            border-bottom: 1px dashed #3A2833; }
  .cg-row:last-child { border-bottom: none; }
  .cg-row span:first-child { color: #A898A3; }
  .cg-row span:last-child { font-weight: 600; color: #F4EEF2; text-align: right; overflow-wrap: anywhere; }
  .cg-side-foot { color: #8C7C87; font-size: 0.7rem; line-height: 1.5; }
</style>
"""


def inject_styles():
    st.html(CSS)
