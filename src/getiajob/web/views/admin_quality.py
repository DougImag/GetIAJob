"""Admin › Qualité & IA : stock d'offres, qualité du classement, consommation de l'IA, santé des sources."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from getiajob.config import get_settings
from getiajob.scraping.base import ALL_SOURCES, SOURCE_LABELS
from getiajob.services import supervision as sv
from getiajob.web.state import require_admin
from getiajob.web.views.admin_format import by_day, duration, local, pivot
from getiajob.web.views.results import SOURCE_COLORS

PERIODS = {"7 jours": 7, "30 jours": 30, "90 jours": 90}


def render() -> None:
    user = require_admin()
    st.markdown("## Qualité & IA")
    label = st.segmented_control("Période", list(PERIODS), default="30 jours", key="q_period")
    days = PERIODS.get(label or "30 jours", 30)
    tabs = st.tabs(["Offres", "Classement", "Consommation IA", "Sources"])
    with tabs[0]:
        _offers(user.id, days)
    with tabs[1]:
        _ranking(days)
    with tabs[2]:
        _ai(days)
    with tabs[3]:
        _sources(days)


def _pct(part: int, total: int) -> str:
    return f"{part / total:.0%}" if total else "–"


# --------------------------------------------------------------------------- #


def _offers(actor_id: int, days: int) -> None:
    s = sv.stock(days)
    c = st.columns(4)
    c[0].metric("Offres en base", s.total, help=f"{s.public} publiques, {s.private} privées (alertes e-mail)")
    c[1].metric("Revues sur 7 jours", s.seen_7)
    c[2].metric("Âge moyen", f"{s.avg_age_days:.0f} j" if s.avg_age_days is not None else "–")
    c[3].metric("Doublons fusionnés (30 j)", s.merged_30, help="Même offre trouvée sur plusieurs sites")
    c = st.columns(3)
    c[0].metric(
        "Sans description", s.no_description, delta=_pct(s.no_description, s.total), delta_color="off"
    )
    c[1].metric("Sans embedding", s.no_embedding, delta=_pct(s.no_embedding, s.total), delta_color="off")
    c[2].metric("Sans entreprise", s.no_company, delta=_pct(s.no_company, s.total), delta_color="off")

    st.markdown("#### Nouvelles offres par jour et par source")
    if s.per_day:
        present = [n for n in ALL_SOURCES if any(r["source"] == n for r in s.per_day)]
        df = pivot(s.per_day, "jour", "source", "n", present).rename(columns=SOURCE_LABELS)
        st.bar_chart(df, color=[SOURCE_COLORS[n] for n in present], x_label="", y_label="Offres")
    else:
        st.caption("Aucune nouvelle offre sur la période.")

    if s.by_source:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Source": SOURCE_LABELS.get(r["source"], r["source"]),
                        "Offres": r["n"],
                        "Sans description": _pct(r["no_desc"], r["n"]),
                        "Avec salaire": _pct(r["with_salary"], r["n"]),
                        "Dernière nouvelle offre": local(r["last_new"]),
                    }
                    for r in s.by_source
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    st.markdown("#### Nettoyage")
    retention = get_settings().offer_retention_days
    keep = st.number_input("Supprimer les offres non revues depuis (jours)", 30, 720, retention, step=30)
    n = sv.purgeable_offers(int(keep))
    st.caption(
        f"**{n}** offre(s) concernée(s). Sont toujours conservées les offres ayant un avis 👍/👎, "
        "une candidature, ou qui figurent dans une recherche enregistrée."
    )
    with st.popover("Purger", icon=":material/delete_sweep:", disabled=n == 0):
        st.markdown(f"Supprimer définitivement **{n}** offre(s) et leurs notes ?")
        if st.button("Confirmer la purge", type="primary", key="q_purge"):
            deleted = sv.purge_offers(actor_id, int(keep))
            st.toast(f"{deleted} offre(s) supprimée(s)", icon=":material/check_circle:")
            st.rerun()


# --------------------------------------------------------------------------- #


def _ranking(days: int) -> None:
    r = sv.ranking(days)
    total = r.likes + r.dislikes
    c = st.columns(4)
    c[0].metric("Offres notées par l'IA", len(r.scores))
    c[1].metric("Note moyenne", f"{sum(r.scores) / len(r.scores):.0f} / 100" if r.scores else "–")
    c[2].metric("Avis 👍 / 👎", f"{r.likes} / {r.dislikes}")
    c[3].metric("Taux de 👍", _pct(r.likes, total), help="Meilleur indicateur de pertinence du classement")

    st.markdown("#### Répartition des notes de l'IA")
    if r.scores:
        bins = pd.Series([min(int(x // 10) * 10, 90) for x in r.scores]).value_counts()
        hist = pd.DataFrame(
            {"Offres": [int(bins.get(b, 0)) for b in range(0, 100, 10)]},
            index=[f"{b}–{b + 9 if b < 90 else 100}" for b in range(0, 100, 10)],
        )
        st.bar_chart(hist, color="#4f46e5", x_label="Note /100", y_label="Offres")
    else:
        st.caption("Aucune évaluation sur la période.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Avis par source")
        if r.by_source:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Source": SOURCE_LABELS.get(x["source"], x["source"]),
                            "👍": x["likes"],
                            "👎": x["dislikes"],
                            "Taux de 👍": _pct(x["likes"], x["likes"] + x["dislikes"]),
                        }
                        for x in r.by_source
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Aucun avis sur la période.")
    with c2:
        st.markdown("#### Motifs de rejet les plus fréquents")
        if r.reasons:
            st.dataframe(
                pd.DataFrame(r.reasons).rename(columns={"motif": "Motif", "n": "Avis"}),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("Aucun motif renseigné.")


# --------------------------------------------------------------------------- #


def _ai(days: int) -> None:
    u = sv.ai_usage(days)
    calls = sum(x["calls"] for x in u.steps if x["kind"] == "llm")
    errors = sum(x["errors"] for x in u.steps if x["kind"] == "llm")
    c = st.columns(4)
    c[0].metric("Appels au LLM", calls)
    c[1].metric("Erreurs", errors, delta=_pct(errors, calls), delta_color="off")
    c[2].metric("Évaluations servies par le cache", u.cache_hits)
    c[3].metric(
        "Taux de cache",
        f"{u.cache_rate:.0%}" if u.cache_rate is not None else "–",
        help="Part des évaluations d'offres évitées grâce aux notes déjà calculées",
    )
    st.markdown("#### Appels au LLM par jour")
    if u.per_day:
        df = by_day(pd.DataFrame(u.per_day).set_index("jour"))
        st.bar_chart(df.rename(columns={"appels": "Appels"}), color="#4f46e5", x_label="", y_label="Appels")
    else:
        st.caption("Aucun appel enregistré sur la période (les mesures commencent avec la version 0.9.0).")
    if u.steps:
        st.markdown("#### Par étape")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Type": "LLM" if x["kind"] == "llm" else "Embeddings",
                        "Étape": sv.llm_label(x["name"]),
                        "Appels": x["calls"],
                        "Éléments": int(x["items"] or 0),
                        "Erreurs": x["errors"],
                        "Durée moyenne": duration((x["avg_ms"] or 0) / 1000),
                        "Durée p95": duration((x["p95_ms"] or 0) / 1000),
                    }
                    for x in u.steps
                ]
            ),
            hide_index=True,
            width="stretch",
        )


# --------------------------------------------------------------------------- #


def _sources(days: int) -> None:
    settings = get_settings()
    health = sv.source_health(days)
    if not health:
        st.caption("Aucune mesure sur la période (les mesures commencent avec la version 0.9.0).")
        return
    rows = []
    for name in [n for n in ALL_SOURCES if n in health] + [n for n in health if n not in ALL_SOURCES]:
        h = health[name]
        state = "En panne" if h.consecutive_failures >= settings.source_failure_threshold else "OK"
        if h.throttled:
            state = "Limitée (429)" if state == "OK" else state
        rows.append(
            {
                "Source": SOURCE_LABELS.get(name, name),
                "État": state,
                "Requêtes HTTP": h.requests,
                "Erreurs HTTP": h.errors,
                "HTTP 429": h.throttled,
                "Temps de réponse": duration(h.avg_ms / 1000) if h.avg_ms is not None else "–",
                "Recherches": h.queries,
                "Recherches en échec": h.failed_queries,
                "Sans résultat": h.empty_queries,
                "Dernier succès": local(h.last_ok),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if "adzuna" in health and settings.adzuna_daily_quota:
        today = health["adzuna"].today
        st.markdown("**Quota Adzuna du jour**")
        st.progress(
            min(today / settings.adzuna_daily_quota, 1.0),
            text=f"{today} / {settings.adzuna_daily_quota} requêtes (quota indicatif, ADZUNA_DAILY_QUOTA)",
        )
    for name, h in health.items():
        if h.last_error and h.consecutive_failures:
            st.warning(
                f"{SOURCE_LABELS.get(name, name)} : {h.consecutive_failures} échec(s) consécutif(s) — "
                f"{h.last_error}",
                icon=":material/error:",
            )
