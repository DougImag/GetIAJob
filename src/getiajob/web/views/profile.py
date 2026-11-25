"""Mon profil : une seule page, avec deux onglets — le CV et la fiche de recherche."""

from __future__ import annotations

from html import escape

import streamlit as st

from getiajob.config import get_settings
from getiajob.scraping.base import SOURCE_CHOICE_LABELS, configured_sources
from getiajob.services.feedback import build_digest, delete_feedback, list_feedback
from getiajob.services.users import ProfileData, SessionUser, get_profile, save_profile
from getiajob.web.layout import (
    CONTRACT_OPTIONS,
    EXPERIENCE_LABELS,
    FRESHNESS_LABELS,
    REMOTE_LABELS,
    freshness_input,
    parse_list,
    sources_input,
)
from getiajob.web.state import PROFILE_SECTION_KEY, require_user
from getiajob.web.views import cv
from getiajob.web.views.cv import ico

SECTIONS = {
    "cv": ":material/description: CV",
    "fiche": ":material/tune: Fiche de recherche",
    "avis": ":material/reviews: Mes avis",
}


def render() -> None:
    user = require_user()
    st.markdown("## Mon profil")
    if st.session_state.get(PROFILE_SECTION_KEY) not in SECTIONS:  # ex. ancien onglet « alertes »
        st.session_state[PROFILE_SECTION_KEY] = "cv"
    section = st.segmented_control(
        "Section",
        options=list(SECTIONS),
        format_func=SECTIONS.get,
        key=PROFILE_SECTION_KEY,
        label_visibility="collapsed",
    )
    if section == "fiche":
        render_fiche(user)
    elif section == "avis":
        render_feedback(user)
    else:  # « cv » ou désélection (clic sur l'onglet actif)
        cv.render_section(user)


FICHE_CSS = """
<style>
  /* Récapitulatif de la fiche */
  .gj-fr { display: flex; gap: 1.2rem; align-items: center; padding: 1.1rem 1.3rem; border-radius: 18px;
    margin: .3rem 0 1rem; border: 1px solid var(--gj-line);
    background: linear-gradient(135deg, rgba(99, 102, 241, .08), rgba(168, 85, 247, .06)); }
  .gj-fr-ring { --v: 0; --c: #6366f1; flex-shrink: 0; width: 4.4rem; height: 4.4rem; border-radius: 50%;
    display: grid; place-items: center;
    background: radial-gradient(var(--gj-surface) 60%, transparent 62%),
      conic-gradient(var(--c) calc(var(--v) * 1%), var(--gj-line) 0); }
  .gj-fr-ring b { font-size: 1.05rem; font-weight: 800; color: var(--gj-head); }
  .gj-fr-main { flex: 1; min-width: 0; }
  .gj-fr-t { font-weight: 800; font-size: 1.1rem; color: var(--gj-head); }
  .gj-fr-s { font-size: .85rem; color: var(--gj-muted); margin-top: .1rem; }
  .gj-fr-chips { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .65rem; }
  .gj-fr-chips span { display: inline-flex; align-items: center; gap: .3rem; font-size: .8rem;
    padding: .2rem .65rem; border-radius: 999px; font-weight: 500;
    background: var(--gj-surface); border: 1px solid var(--gj-line); color: var(--gj-text); }
  .gj-fr-chips span svg { color: var(--gj-primary); }
  .gj-fr-chips span.todo { border-style: dashed; border-color: rgba(234, 88, 12, .55); color: #c2410c;
    background: rgba(249, 115, 22, .06); }
  .gj-fr-chips span.todo svg { color: #ea580c; }

  /* Sections du formulaire */
  [class*="st-key-gj_fs_"] { border-radius: 16px !important; }
  .gj-fs-h { display: flex; align-items: center; gap: .75rem; margin-bottom: .6rem; }
  .gj-fs-i { flex-shrink: 0; width: 2.3rem; height: 2.3rem; border-radius: 11px; display: grid;
    place-items: center; color: #fff;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2)); }
  .gj-fs-i.green { background: linear-gradient(135deg, #16a34a, #0d9488); }
  .gj-fs-i.orange { background: linear-gradient(135deg, #f97316, #ec4899); }
  .gj-fs-i.red { background: linear-gradient(135deg, #ef4444, #b91c1c); }
  .gj-fs-t { font-weight: 800; color: var(--gj-head); line-height: 1.2; }
  .gj-fs-s { font-size: .82rem; color: var(--gj-muted); }
  @media (max-width: 640px) { .gj-fr { flex-direction: column; align-items: flex-start; } }
</style>
"""


def _section_head(icon_name: str, title: str, subtitle: str, color: str = "") -> None:
    st.markdown(
        f'<div class="gj-fs-h"><div class="gj-fs-i {color}">{ico(icon_name, 18)}</div><div>'
        f'<div class="gj-fs-t">{escape(title)}</div>'
        f'<div class="gj-fs-s">{escape(subtitle)}</div></div></div>',
        unsafe_allow_html=True,
    )


def _summary(p: ProfileData) -> None:
    """Récapitulatif : jauge de complétude et critères actuels en pastilles."""
    completion, missing = p.completion()

    def chip(icon_name: str, text: str, todo: bool = False) -> str:
        return f'<span class="{"todo" if todo else ""}">{ico(icon_name, 13)}{escape(text)}</span>'

    where = ", ".join(p.locations)
    chips = [
        chip("briefcase", p.desired_job) if p.desired_job else chip("briefcase", "Poste à préciser", True),
        chip("map-pin", where) if where else chip("map-pin", "Lieu(x) à préciser", True),
        chip("clock", f"Annonces de moins de {FRESHNESS_LABELS.get(p.max_offer_age_days, '1 semaine')}"),
    ]
    chips.append(
        chip("house", " / ".join(REMOTE_LABELS.get(r, r) for r in p.remote))
        if p.remote
        else chip("house", "Mode de travail à préciser", True)
    )
    chips.append(
        chip("file", ", ".join(p.contract_types))
        if p.contract_types
        else chip("file", "Contrat à préciser", True)
    )
    if p.experience_levels:
        chips.append(chip("user", ", ".join(EXPERIENCE_LABELS.get(e, e) for e in p.experience_levels)))
    if p.min_salary:
        chips.append(chip("euro", f"≥ {p.min_salary:,} € brut/an".replace(",", "\u202f")))
    srcs = [
        s for s in (p.sources if p.sources is not None else configured_sources()) if s in SOURCE_CHOICE_LABELS
    ]
    chips.append(
        chip("globe", " · ".join(SOURCE_CHOICE_LABELS[s] for s in srcs) or "Aucune source", not srcs)
    )
    n_excl = len(p.exclude_keywords) + len(p.exclude_companies)
    if n_excl:
        chips.append(chip("ban", f"{n_excl} exclusion(s)"))
    color = "#16a34a" if completion == 100 else "#ea580c" if completion < 50 else "#6366f1"
    subtitle = (
        "Tout est prêt : ces critères orientent la recherche et la notation des offres par l'IA."
        if not missing
        else f"À compléter : {', '.join(missing)}."
    )
    st.markdown(
        f'<div class="gj-fr"><div class="gj-fr-ring" style="--v:{completion};--c:{color}">'
        f'<b>{completion}%</b></div><div class="gj-fr-main"><div class="gj-fr-t">Ma fiche de recherche</div>'
        f'<div class="gj-fr-s">{escape(subtitle)}</div><div class="gj-fr-chips">{"".join(chips)}</div>'
        f"</div></div>",
        unsafe_allow_html=True,
    )


def render_fiche(user: SessionUser) -> None:
    p = get_profile(user.id)
    st.html(FICHE_CSS)
    _summary(p)

    with st.form("fiche", border=False):
        with st.container(border=True, key="gj_fs_job"):
            _section_head("briefcase", "Le poste", "Ce que tu cherches et où")
            c1, c2 = st.columns(2)
            desired_job = c1.text_input(
                "Poste recherché *", value=p.desired_job, placeholder="ex. Data Engineer, Chef de projet IT…"
            )
            locations = c2.text_input(
                "Lieu(x) *",
                value=", ".join(p.locations),
                placeholder="ex. Paris, Lyon, France",
                help="Séparés par des virgules. Chaque lieu est interrogé sur chaque source.",
            )
            max_offer_age_days = freshness_input(
                "Fraîcheur des annonces",
                p.max_offer_age_days,
                help="Ancienneté maximale des offres retenues (proposée par défaut à chaque recherche).",
            )
            sources = sources_input("Sources d'annonces", p.sources, key="fiche_sources")
            if "apec" in configured_sources():
                st.caption(
                    f":material/forward_to_inbox: **Alertes e-mail** (APEC, Free-Work…) : transfère-les à "
                    f"**{get_settings().imap_user}** depuis **{user.email}** (l'adresse de ton compte), "
                    "à la main ou avec une règle de transfert automatique. Les offres reçues ne sont "
                    "visibles que par toi. Tout se gère dans **Mes alertes e-mail** (menu de gauche)."
                )

        with st.container(border=True, key="gj_fs_cond"):
            _section_head("sliders", "Les conditions", "Mode de travail, contrat, niveau et salaire", "green")
            remote = st.pills(
                "Mode de travail",
                options=list(REMOTE_LABELS),
                default=p.remote,
                selection_mode="multi",
                format_func=REMOTE_LABELS.get,
            )
            contract_types = st.pills(
                "Type(s) de contrat",
                options=list(dict.fromkeys(CONTRACT_OPTIONS + p.contract_types)),
                default=p.contract_types,
                selection_mode="multi",
            )
            experience_levels = st.pills(
                "Niveau d'expérience",
                options=list(EXPERIENCE_LABELS),
                default=p.experience_levels,
                selection_mode="multi",
                format_func=EXPERIENCE_LABELS.get,
            )
            min_salary = st.number_input(
                "Salaire minimum (brut annuel, €)",
                value=p.min_salary,
                min_value=0,
                step=1000,
                placeholder="Non précisé",
                width=320,
            )

        with st.container(border=True, key="gj_fs_exp"):
            _section_head(
                "sparkles", "Ce qui compte pour moi", "Texte libre lu par l'IA qui note les offres", "orange"
            )
            expectations = st.text_area(
                "Décris ce que tu recherches",
                value=p.expectations,
                height=130,
                placeholder="Culture d'entreprise, secteur, taille d'équipe, technologies, valeurs…",
                label_visibility="collapsed",
            )

        with st.container(border=True, key="gj_fs_excl"):
            _section_head("ban", "Exclusions", "Les offres correspondantes sont écartées d'office", "red")
            c1, c2 = st.columns(2)
            exclude_keywords = c1.text_input(
                "Mots-clés à exclure",
                value=", ".join(p.exclude_keywords),
                placeholder="ex. stage, alternance",
                help="Une offre contenant l'un de ces mots est écartée.",
            )
            exclude_companies = c2.text_input(
                "Entreprises à exclure",
                value=", ".join(p.exclude_companies),
                placeholder="ex. ACME, Initech",
            )

        if st.form_submit_button(
            "Enregistrer ma fiche", type="primary", icon=":material/save:", use_container_width=True
        ):
            if not desired_job.strip():
                st.error("Indique au moins le poste recherché.")
            elif not sources:
                st.error("Choisis au moins une source d'annonces.")
            else:
                save_profile(
                    user.id,
                    ProfileData(
                        desired_job=desired_job.strip(),
                        locations=parse_list(locations),
                        max_offer_age_days=max_offer_age_days,
                        remote=remote or [],
                        contract_types=contract_types or [],
                        experience_levels=experience_levels or [],
                        min_salary=int(min_salary) if min_salary else None,
                        expectations=expectations.strip(),
                        exclude_keywords=parse_list(exclude_keywords),
                        exclude_companies=parse_list(exclude_companies),
                        sources=sources,
                    ),
                )
                st.toast("Fiche enregistrée", icon=":material/check_circle:")
                st.rerun()


def render_feedback(user: SessionUser) -> None:
    items = list_feedback(user.id)
    st.caption(
        "Tes avis sur les offres proposées. Ils sont pris en compte à chaque nouvelle recherche : "
        "offres écartées exclues, offres similaires favorisées ou pénalisées, résumé transmis à l'IA."
    )
    if not items:
        st.info(
            "Aucun avis pour l'instant. Dans « Mes recherches », utilise « J'aime » / « Pas pour moi » "
            "sous chaque offre.",
            icon=":material/reviews:",
        )
        return

    liked = sum(1 for f in items if f.liked)
    m1, m2 = st.columns(2)
    m1.metric("Offres aimées", liked, border=True)
    m2.metric("Offres écartées", len(items) - liked, border=True)

    with st.expander("Ce que l'IA retiendra", icon=":material/psychology:"):
        st.code(build_digest(items), language=None, wrap_lines=True)

    for f in items:
        with st.container(border=True):
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            icon = ":green[:material/thumb_up:]" if f.liked else ":red[:material/thumb_down:]"
            line = f"{icon} **{f.title}**" + (f" — {f.company}" if f.company else "")
            details = []
            if f.reasons:
                details.append(", ".join(f.reason_labels))
            if f.comment:
                details.append(f"« {f.comment} »")
            details.append(f"{f.updated_at:%d/%m/%Y}")
            c1.markdown(line + "  \n:gray[" + " · ".join(details) + "]")
            if c2.button("", icon=":material/delete:", key=f"fb-del-{f.job_id}", help="Retirer cet avis"):
                delete_feedback(user.id, f.job_id)
                st.rerun()
