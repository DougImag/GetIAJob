"""Admin › Supervision : activité, recherches (file, blocages, échecs), santé du système, alertes, audit."""

from __future__ import annotations

from html import escape

import altair as alt
import pandas as pd
import streamlit as st

from getiajob.config import get_settings
from getiajob.services import llm_settings
from getiajob.services import supervision as sv
from getiajob.services.alerting import SEVERITY_LABELS, run_monitor
from getiajob.web.state import get_runner, require_admin
from getiajob.web.views.admin_format import (
    STATUS_COLORS,
    duration,
    local,
    money_label,
    tokens_label,
)
from getiajob.web.views.cv import ico as _ico

# Icônes propres à la supervision (tracés Lucide), les autres viennent des vues existantes
SV_ICONS = {
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/>'
    '<line x1="12" x2="12" y1="3" y2="15"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>'
    '<line x1="12" x2="12" y1="15" y2="3"/>',
    "pie": '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    "cpu": '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/>'
    '<path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
}


def ico(name: str, size: int = 15) -> str:
    if name not in SV_ICONS:
        return _ico(name, size)
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{SV_ICONS[name]}</svg>"
    )


PERIODS = {"7 jours": 7, "30 jours": 30, "90 jours": 90}


def render() -> None:
    user = require_admin()
    st.markdown("## Supervision")
    _alert_banner()
    tabs = st.tabs(["Activité", "Consommation IA", "Recherches", "Santé & alertes", "Journal d'audit"])
    with tabs[0]:
        _activity()
    with tabs[1]:
        _llm_usage()
    with tabs[2]:
        _searches(user.id)
    with tabs[3]:
        _health()
    with tabs[4]:
        _audit()


def _alert_banner() -> None:
    open_alerts = [a for a in sv.alerts() if a.resolved_at is None]
    if not open_alerts:
        return
    critical = any(a.severity == "critical" for a in open_alerts)
    lines = "\n".join(
        f"- **{SEVERITY_LABELS.get(a.severity, a.severity)}** — {a.message}" for a in open_alerts
    )
    (st.error if critical else st.warning)(
        f"{len(open_alerts)} alerte(s) ouverte(s) :\n{lines}", icon=":material/notifications_active:"
    )


# --------------------------------------------------------------------------- #


ACTIVITY_CSS = """
<style>
  .gj-sv-h { display: flex; align-items: center; gap: .45rem; margin: 1.1rem 0 .55rem; font-weight: 800;
    font-size: .95rem; color: var(--gj-head); }
  .gj-sv-h svg { color: var(--gj-primary); }
  .gj-sv { display: grid; grid-template-columns: repeat(4, 1fr); gap: .75rem; }
  @media (max-width: 1000px) { .gj-sv { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 520px) { .gj-sv { grid-template-columns: 1fr; } }
  .gj-sv-c { display: flex; gap: .8rem; align-items: center; padding: .9rem 1rem; border-radius: 16px;
    background: var(--gj-surface); border: 1px solid var(--gj-line); min-width: 0; }
  .gj-sv-c.bad { border-color: rgba(220, 38, 38, .45); background: rgba(220, 38, 38, .06); }
  .gj-sv-i { flex-shrink: 0; width: 2.5rem; height: 2.5rem; border-radius: 12px; display: grid;
    place-items: center; color: #fff;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2)); }
  .gj-sv-i.green { background: linear-gradient(135deg, #16a34a, #0d9488); }
  .gj-sv-i.amber { background: linear-gradient(135deg, #f59e0b, #ea580c); }
  .gj-sv-i.violet { background: linear-gradient(135deg, #7c3aed, #4f46e5); }
  .gj-sv-i.red { background: linear-gradient(135deg, #ef4444, #be123c); }
  .gj-sv-i.gray { background: linear-gradient(135deg, #94a3b8, #64748b); }
  .gj-sv-ring { --v: 0; --c: #16a34a; flex-shrink: 0; width: 2.5rem; height: 2.5rem; border-radius: 50%;
    display: grid; place-items: center; font-size: .68rem; font-weight: 800; color: var(--gj-head);
    background: radial-gradient(var(--gj-surface) 58%, transparent 60%),
      conic-gradient(var(--c) calc(var(--v) * 1%), var(--gj-line) 0); }
  .gj-sv-v { font-size: 1.4rem; font-weight: 800; color: var(--gj-head); line-height: 1.1;
    display: flex; align-items: baseline; gap: .4rem; }
  .gj-sv-d { font-size: .72rem; font-weight: 700; color: #16a34a; background: rgba(22, 163, 74, .12);
    padding: .05rem .4rem; border-radius: 999px; }
  .gj-sv-l { font-size: .8rem; color: var(--gj-muted); }
  .gj-sv-s { font-size: .72rem; color: var(--gj-muted); margin-top: .1rem; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; }
</style>
"""

# Statut (libellé du service) → couleur des barres, dans l'ordre d'empilement
ACTIVITY_STATUS = {**STATUS_COLORS, "Arrêtée": "#cbd5e1", "En cours": "#6366f1"}


def _card(icon_html: str, value: str, label: str, sub: str = "", delta: str = "", bad: bool = False) -> str:
    delta_html = f'<span class="gj-sv-d">{escape(delta)}</span>' if delta else ""
    sub_html = f'<div class="gj-sv-s" title="{escape(sub)}">{escape(sub)}</div>' if sub else ""
    return (
        f'<div class="gj-sv-c{" bad" if bad else ""}">{icon_html}<div style="min-width:0">'
        f'<div class="gj-sv-v">{escape(value)}{delta_html}</div>'
        f'<div class="gj-sv-l">{escape(label)}</div>{sub_html}</div></div>'
    )


def _icon(name: str, tone: str = "") -> str:
    return f'<div class="gj-sv-i {tone}">{ico(name, 19)}</div>'


def _heading(icon_name: str, title: str) -> str:
    return f'<div class="gj-sv-h">{ico(icon_name, 17)}{escape(title)}</div>'


def _section(icon_name: str, title: str, cards: list[str]) -> None:
    html = f'{_heading(icon_name, title)}<div class="gj-sv">{"".join(cards)}</div>'
    st.markdown(html, unsafe_allow_html=True)


def _activity() -> None:
    st.markdown(ACTIVITY_CSS, unsafe_allow_html=True)
    with st.container(horizontal=True, vertical_alignment="center"):
        label = st.segmented_control(
            "Période", list(PERIODS), default="30 jours", key="sv_period", label_visibility="collapsed"
        )
        days = PERIODS.get(label or "30 jours", 30)
        st.caption(f"Activité des {days} derniers jours · utilisateurs actifs : connexion ou recherche")
    a = sv.activity(days)
    per_status: dict[str, int] = {}
    for r in a.per_day:
        per_status[r["statut"]] = per_status.get(r["statut"], 0) + int(r["n"])
    done, stopped = per_status.get("Terminée", 0), per_status.get("Arrêtée", 0)
    per_day = f"{a.searches / days:.1f}".replace(".", ",")
    running = per_status.get("En cours", 0)

    if a.searches:
        rate = round(100 * done / a.searches)
        color = "#16a34a" if rate >= 80 else "#ea580c" if rate >= 50 else "#dc2626"
        rate_card = _card(
            f'<div class="gj-sv-ring" style="--v:{rate};--c:{color}">{rate} %</div>',
            f"{done}",
            "Terminées avec succès",
            f"{stopped} arrêtée(s) · {running} en cours ou à confirmer",
        )
    else:
        rate_card = _card(_icon("check", "green"), "–", "Terminées avec succès", "aucune recherche")
    _section(
        "search",
        "Recherches",
        [
            _card(_icon("search"), str(a.searches), "Recherches lancées", f"~{per_day} par jour"),
            rate_card,
            _card(_icon("clock", "amber"), duration(a.avg_seconds), "Durée moyenne", "recherches terminées"),
            _card(
                _icon("alert", "red" if a.failed else "gray"),
                str(a.failed),
                "Recherches en échec",
                f"{a.failure_rate:.0%} des recherches" if a.failed else "aucun échec",
                bad=bool(a.failed),
            ),
        ],
    )
    _section(
        "user",
        "Utilisateurs & CV",
        [
            _card(
                _icon("user", "violet"), str(a.active_7), "Actifs sur 7 jours", f"{a.active_30} sur 30 jours"
            ),
            _card(
                _icon("user", "green"),
                str(a.users),
                "Comptes",
                f"{a.new_users} nouveau(x) sur la période" if a.new_users else "aucun nouveau sur la période",
                delta=f"+{a.new_users}" if a.new_users else "",
            ),
            _card(
                _icon("file"),
                str(a.cvs),
                "CV déposés",
                f"{a.new_cvs} nouveau(x) sur la période" if a.new_cvs else "aucun nouveau sur la période",
                delta=f"+{a.new_cvs}" if a.new_cvs else "",
            ),
            _card(
                _icon("alert", "red" if a.cv_failures else "gray"),
                str(len(a.cv_failures)),
                "Analyses de CV en échec",
                "détail ci-dessous" if a.cv_failures else "aucun échec",
                bad=bool(a.cv_failures),
            ),
        ],
    )

    st.markdown(_heading("calendar", "Recherches par jour"), unsafe_allow_html=True)
    with st.container(border=True):
        _searches_chart(a.per_day, days)

    if a.cv_failures:
        st.markdown(_heading("alert", "Analyses de CV en échec"), unsafe_allow_html=True)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Date": local(r["analysis_started_at"]),
                        "CV": r["file_name"],
                        "Utilisateur": r["email"] or "Usage interne",
                        "Erreur": r["analysis_error"],
                    }
                    for r in a.cv_failures
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"Erreur": st.column_config.TextColumn(width="large")},
        )


def _searches_chart(per_day: list[dict], days: int) -> None:
    """Barres empilées par statut, tous les jours de la période (jours sans recherche compris)."""
    _stacked_by_day(
        per_day, "statut", ACTIVITY_STATUS, days, "Recherches", "Aucune recherche sur la période."
    )


def _stacked_by_day(
    rows: list[dict], key: str, colors: dict[str, str], days: int, measure: str, empty: str
) -> None:
    """Barres empilées par jour (lignes `jour`, `key`, `n`), sur tous les jours de la période."""
    end = pd.Timestamp.now().normalize()
    calendar = pd.date_range(end - pd.Timedelta(days=days - 1), end, freq="D")
    series = list(colors)
    df = pd.DataFrame(rows or [], columns=["jour", key, "n"])
    df["jour"] = pd.to_datetime(df["jour"])
    df = (
        df.pivot_table(index="jour", columns=key, values="n", aggfunc="sum")
        .reindex(index=calendar, columns=series)
        .fillna(0)
        .rename_axis("jour")
        .reset_index()
        .melt(id_vars="jour", var_name="Série", value_name=measure)
    )
    if not df[measure].any():
        st.caption(empty)
        return
    shown = [c for c in series if df.loc[df["Série"] == c, measure].any()]
    x_axis = alt.Axis(format="%d/%m", grid=False, tickCount=min(days, 10))
    chart = (
        alt.Chart(df[df["Série"].isin(shown)])
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, width=alt.RelativeBandSize(0.7))
        .encode(
            x=alt.X("yearmonthdate(jour):T", title=None, axis=x_axis),
            y=alt.Y(f"sum({measure}):Q", title=None, axis=alt.Axis(tickMinStep=1, format="~s")),
            color=alt.Color(
                "Série:N",
                scale=alt.Scale(domain=shown, range=[colors[c] for c in shown]),
                legend=alt.Legend(orient="top", title=None),
            ),
            order=alt.Order("order:Q"),
            tooltip=[
                alt.Tooltip("yearmonthdate(jour):T", title="Jour", format="%d/%m/%Y"),
                alt.Tooltip("Série:N", title=key.capitalize()),
                alt.Tooltip(f"{measure}:Q", format=",.0f"),
            ],
        )
        .transform_calculate(order=f"indexof({shown!r}, datum['Série'])")
        .properties(height=260)
    )
    st.altair_chart(chart, width="stretch")


def _donut(rows: list[dict], label_key: str, colors: list[str]) -> None:
    """Anneau de répartition des tokens (envoyés + générés), libellés en légende."""
    df = pd.DataFrame(
        [{"Part": r[label_key], "Tokens": int(r["envoyes"] or 0) + int(r["generes"] or 0)} for r in rows]
    )
    df = df[df["Tokens"] > 0]
    if df.empty:
        st.caption("Rien à répartir.")
        return
    df["pct"] = df["Tokens"] / df["Tokens"].sum()
    chart = (
        alt.Chart(df)
        .mark_arc(innerRadius=52, outerRadius=84, cornerRadius=4, padAngle=0.015)
        .encode(
            theta=alt.Theta("Tokens:Q", stack=True),
            color=alt.Color(
                "Part:N",
                sort=list(df["Part"]),
                scale=alt.Scale(range=colors),
                legend=alt.Legend(orient="bottom", title=None, columns=1, labelLimit=260),
            ),
            tooltip=[
                alt.Tooltip("Part:N", title="Usage"),
                alt.Tooltip("Tokens:Q", format=",.0f"),
                alt.Tooltip("pct:Q", title="Part", format=".0%"),
            ],
        )
        .properties(height=290, padding={"top": 14, "bottom": 4, "left": 4, "right": 4})
    )
    st.altair_chart(chart, width="stretch")


# --------------------------------------------------------------------------- #

TOKEN_COLORS = {"Envoyés": "#6366f1", "Générés": "#a855f7"}


TOP_USERS = 10
SUM_KEYS = ("appels", "envoyes", "generes", "raisonnement")


def _sum_rows(rows: list[dict], first: dict[str, str], label: str) -> dict:
    """Ligne agrégée : libellé dans la 1re colonne, sommes des colonnes numériques (coût : None si aucun)."""
    keys = list(first)
    row: dict = {key: "" for key in keys}
    row[keys[0]] = label
    for key in keys[1:]:
        if rows and all(isinstance(r.get(key), int | float) for r in rows):
            row[key] = sum(r[key] for r in rows)
    for key in SUM_KEYS:
        row[key] = sum(int(r[key] or 0) for r in rows)
    costs = [r["cout"] for r in rows if r["cout"] is not None]
    row["cout"] = sum(costs) if costs else None
    return row


def _usage_table(rows: list[dict], first: dict[str, str], top: int | None = None, others: str = "") -> None:
    """Tableau de consommation : colonnes propres à la vue (`first`), puis les totaux communs.

    `top` : seules les `top` premières lignes (déjà triées par consommation) sont détaillées, les
    suivantes sont regroupées sur une ligne `others`. Une ligne « Total » (en gras) termine le tableau."""
    shown = rows
    if top is not None and len(rows) > top:
        rest = rows[top:]
        shown = [*rows[:top], _sum_rows(rest, first, others.format(n=len(rest)))]
    df = pd.DataFrame(
        [
            {
                **{label: r[key] for key, label in first.items()},
                "Appels": r["appels"],
                "Tokens envoyés": tokens_label(r["envoyes"]),
                "Tokens générés": tokens_label(r["generes"]),
                "dont raisonnement": tokens_label(r["raisonnement"]),
                "Coût estimé": money_label(r["cout"]),
            }
            for r in [*shown, _sum_rows(rows, first, "Total")]
        ]
    )
    last = len(df) - 1
    styled = df.style.apply(
        lambda col: ["font-weight: 800" if i == last else "" for i in range(len(col))], axis=0
    )
    st.dataframe(styled, hide_index=True, width="stretch")


USAGE_COLORS = ["#6366f1", "#a855f7", "#0d9488", "#f59e0b", "#94a3b8"]


def _llm_usage() -> None:
    st.markdown(ACTIVITY_CSS, unsafe_allow_html=True)
    llm = llm_settings.current()
    with st.container(horizontal=True, vertical_alignment="center"):
        label = st.segmented_control(
            "Période", list(PERIODS), default="30 jours", key="sv_usage_period", label_visibility="collapsed"
        )
        days = PERIODS.get(label or "30 jours", 30)
        st.caption(f"Consommation des {days} derniers jours · LLM actif : {llm.title}")
    u = sv.llm_usage(days)
    priced = llm.price_input is not None or llm.price_output is not None
    currency = get_settings().llm_price_currency
    price = f"{llm.price_input or 0:g} / {llm.price_output or 0:g}".replace(".", ",")
    price = f"{price} {currency} par M tokens (envoyés / générés)"
    per_call = round(u.total_tokens / u.calls) if u.calls else 0
    _section(
        "sparkles",
        "Consommation",
        [
            _card(
                _icon("sparkles", "violet"),
                f"{u.calls:,}".replace(",", "\u202f"),
                "Appels au LLM",
                f"~{tokens_label(per_call)} tokens par appel" if u.calls else "aucun appel",
            ),
            _card(_icon("upload"), tokens_label(u.input_tokens), "Tokens envoyés", "prompts, CV et offres"),
            _card(
                _icon("download", "violet"),
                tokens_label(u.output_tokens),
                "Tokens générés",
                f"dont {tokens_label(u.reasoning_tokens)} de raisonnement"
                if u.reasoning_tokens
                else "réponses du modèle",
            ),
            _card(
                _icon("euro", "green" if priced else "gray"),
                money_label(u.cost),
                "Coût estimé",
                price if priced else "prix du LLM actif non renseignés",
            ),
        ],
    )
    if not priced:
        st.caption(
            f"Coût non calculé pour le LLM actif ({llm.title}) : renseigner ses prix par million de tokens "
            "dans le `.env` (`LLM_PRICE_INPUT` / `LLM_PRICE_OUTPUT`, ou `PRICE_INPUT` / `PRICE_OUTPUT` "
            "de sa configuration). Le coût est calculé au moment de chaque appel."
        )
    b = llm_settings.budget()
    if b.limit.enabled:
        st.markdown(_heading("sliders", "Limite de tokens"), unsafe_allow_html=True)
        with st.container(border=True):
            st.progress(
                min(1.0, b.used / b.limit.tokens),
                text=f"{tokens_label(b.used)} / {tokens_label(b.limit.tokens)} tokens ({b.limit.label})"
                + ("" if b.applies else " · ne s'applique pas au LLM actif (Ollama non compté)"),
            )
    if not u.calls:
        st.caption("Aucun appel au LLM enregistré sur la période.")
        return

    chart, donut = st.columns([2, 1], gap="medium")
    with chart:
        st.markdown(_heading("calendar", "Tokens par jour"), unsafe_allow_html=True)
        with st.container(border=True):
            _stacked_by_day(u.per_day, "type", TOKEN_COLORS, days, "Tokens", "Aucun token sur la période.")
    with donut:
        st.markdown(_heading("pie", "Répartition par usage"), unsafe_allow_html=True)
        with st.container(border=True):
            _donut(u.per_usage, "usage", USAGE_COLORS)

    users_title = "Par utilisateur"
    if len(u.per_user) > TOP_USERS:
        users_title += f" — les {TOP_USERS} plus gros consommateurs"
    st.markdown(_heading("user", users_title), unsafe_allow_html=True)
    for r in u.per_user:
        r["email"] = r["email"] or "Usage interne"  # ligne de commande, compte supprimé
    _usage_table(
        u.per_user,
        {"email": "Utilisateur", "appels_cv": "Appels analyse de CV", "offres_evaluees": "Offres évaluées"},
        top=TOP_USERS,
        others="Autres utilisateurs ({n})",
    )

    st.markdown(_heading("list", "Par usage"), unsafe_allow_html=True)
    _usage_table(u.per_usage, {"usage": "Usage"})
    st.markdown(_heading("cpu", "Par modèle"), unsafe_allow_html=True)
    _usage_table(u.per_model, {"fournisseur": "Fournisseur", "modele": "Modèle"})
    st.caption(
        "Tokens renvoyés par le fournisseur à chaque appel (Ollama compris). Les tokens générés incluent le "
        "raisonnement. Une offre déjà évaluée pour le même CV n'est pas réévaluée : elle ne consomme rien."
    )


# --------------------------------------------------------------------------- #


def _searches(actor_id: int) -> None:
    settings = get_settings()
    active = sv.active_searches()
    running = [r for r in active if r.status == "running"]
    pending = [r for r in active if r.status == "pending"]
    stuck = [r for r in running if r.is_stuck(settings.stuck_search_minutes)]
    c = st.columns(3)
    c[0].metric("En cours", len(running))
    c[1].metric("En file d'attente", len(pending))
    c[2].metric(f"Bloquées (> {settings.stuck_search_minutes} min)", len(stuck))

    st.markdown("#### File de traitement")
    if not active:
        st.caption("Aucune recherche en cours ni en attente.")
    for r in active:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            state = "En cours" if r.status == "running" else "En attente"
            badge = (
                ":red-badge[Bloquée]"
                if r.is_stuck(settings.stuck_search_minutes)
                else f":blue-badge[{state}]"
            )
            step = f" · étape `{r.step}`" if r.step else ""
            c1.markdown(
                f"{badge} **#{r.id} {r.name}** — {r.email or 'compte supprimé'}  \n"
                f"Créée le {local(r.created_at)} · depuis {r.age_minutes:.0f} min{step}"
            )
            if c2.button("Arrêter", key=f"sv-cancel-{r.id}", icon=":material/stop_circle:"):
                if sv.cancel_search(actor_id, r.id):
                    st.toast(f"Recherche #{r.id} arrêtée", icon=":material/check_circle:")
                st.rerun()
    if running:
        st.caption(
            "« Arrêter » marque la recherche en échec. Un traitement en cours s'interrompt dès la fin "
            "de l'appel en cours (IA ou site d'annonces) ; une recherche en attente ne sera pas lancée."
        )

    st.markdown("#### Derniers échecs")
    query = st.text_input("Filtrer par utilisateur", placeholder="email…", key="sv_fail_q")
    failed = sv.failed_searches(30, query or None)
    if not failed:
        st.caption("Aucun échec.")
    for r in failed:
        with st.container(border=True):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            c1.markdown(
                f"**#{r.id} {r.name}** — {r.email or 'compte supprimé'} · "
                f"{local(r.finished_at or r.created_at)}"
            )
            c1.code(r.error or "Erreur inconnue", language=None, wrap_lines=True)
            if c2.button("Relancer", key=f"sv-retry-{r.id}", icon=":material/replay:"):
                if sv.retry_search(actor_id, r.id):
                    get_runner().submit_search(r.id)
                    st.toast(f"Recherche #{r.id} relancée", icon=":material/check_circle:")
                st.rerun()


# --------------------------------------------------------------------------- #


def _check_line(check: sv.Check) -> None:
    icon = {True: ":green-badge[OK]", False: ":red-badge[Problème]", None: ":gray-badge[–]"}[check.ok]
    timing = f" · {check.seconds * 1000:.0f} ms" if check.seconds is not None else ""
    st.markdown(f"{icon} **{check.label}** — {check.detail}{timing}")


def _event_check(label: str, kind: str, empty: str) -> sv.Check:
    ok_event, last = sv.last_event(kind), sv.last_event(kind, ok=None)
    if last is None:
        return sv.Check(label, None, empty)
    detail = f"dernière réussite : {local(ok_event.created_at)}" if ok_event else "jamais réussie"
    if not last.ok:
        detail += f" · dernier essai en échec ({local(last.created_at)}) : {last.detail}"
    return sv.Check(label, last.ok, detail)


def _health() -> None:
    settings = get_settings()
    st.markdown("#### État du système")
    db, tables = sv.check_database()
    with st.container(border=True):
        _check_line(db)
        _check_line(sv.check_ollama())
        if (remote := sv.check_remote_llm()) is not None:
            _check_line(remote)
        _check_line(_event_check("Relève des alertes e-mail", "mail_sync", "jamais lancée"))
        _check_line(
            _event_check(
                "Sauvegarde de la base",
                "backup",
                "aucune sauvegarde enregistrée (scripts/backup-db.sh)",
            )
        )
        if st.session_state.get("sv_mailbox"):
            _check_line(st.session_state["sv_mailbox"])
        st.caption(
            f"Version {sv.app_version()} · serveur démarré le {local(sv.STARTED_AT)} · "
            f"modèle `{llm_settings.current().model}` · embeddings `{settings.embedding_model}`"
        )
    if st.button("Tester la connexion à la boîte mail", icon=":material/mail:"):
        with st.spinner("Connexion IMAP…"):
            st.session_state["sv_mailbox"] = sv.check_mailbox()
        st.rerun()

    if tables:
        with st.expander("Tables les plus volumineuses"):
            st.dataframe(pd.DataFrame(tables).drop(columns="octets"), hide_index=True, width="stretch")

    st.markdown("#### Alertes")
    st.caption(
        "Les alertes sont calculées par `getiajob monitor` (tâche cron `scripts/cron-monitor.sh`) et "
        "envoyées par e-mail "
        + (f"à {settings.alert_emails}" if settings.alert_emails else "aux comptes administrateurs")
        + ("." if settings.alerts_enabled else " — notifications désactivées (ALERTS_ENABLED=false).")
    )
    rows = sv.alerts()
    if not rows:
        st.caption("Aucune alerte sur les 7 derniers jours.")
    for a in rows:
        color = "gray" if a.resolved_at else ("red" if a.severity == "critical" else "orange")
        status = f"résolue le {local(a.resolved_at)}" if a.resolved_at else "ouverte"
        notified = f" · notifiée le {local(a.notified_at)}" if a.notified_at else ""
        st.markdown(
            f":{color}-badge[{SEVERITY_LABELS.get(a.severity, a.severity)}] {a.message}  \n"
            f"<small>`{a.key}` · depuis le {local(a.opened_at)} · {status}{notified}</small>",
            unsafe_allow_html=True,
        )
    with st.container(horizontal=True, gap="small"):
        check = st.button(
            "Vérifier maintenant", icon=":material/fact_check:", help="Sans rien enregistrer ni envoyer"
        )
        notify = st.button(
            "Vérifier et notifier",
            icon=":material/notifications:",
            help="Met à jour les alertes et envoie l'e-mail s'il y a du nouveau",
        )
    if check or notify:
        with st.spinner("Vérification…"):
            report = run_monitor(dry_run=check)
        if not report.findings:
            st.success("Aucun problème détecté.", icon=":material/check_circle:")
        for f in report.findings:
            st.markdown(f"- **{SEVERITY_LABELS.get(f.severity, f.severity)}** — {f.message}")
        if notify:
            if report.sent:
                st.toast(f"Notification envoyée à {len(report.recipients)} destinataire(s)")
            elif report.send_error:
                st.error(f"Notification non envoyée : {report.send_error}")
            elif not report.plan.to_notify:
                st.caption("Rien de nouveau à notifier.")


# --------------------------------------------------------------------------- #


def _audit() -> None:
    actions = sv.audit_actions()
    choice = st.selectbox(
        "Action",
        [None, *actions],
        format_func=lambda a: "Toutes les actions" if a is None else sv.AUDIT_LABELS.get(a, a),
        key="sv_audit_action",
    )
    rows = sv.audit_log(200, choice)
    if not rows:
        st.caption("Aucune action enregistrée.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Date": local(r.created_at),
                    "Par": r.actor,
                    "Action": sv.AUDIT_LABELS.get(r.action, r.action),
                    "Cible": r.target,
                    "Détail": r.detail,
                }
                for r in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )
