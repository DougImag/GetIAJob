"""Correction manuelle du profil extrait d'un CV, bloc par bloc (identité, expériences, formation…).

Chaque éditeur reconstruit le profil complet puis l'enregistre via `services.cvs.update_profile`,
qui invalide aussi l'embedding et les notes déjà calculées pour ce CV.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import streamlit as st

from getiajob.schemas import CVProfile, Education, Experience, Language, Project, SkillGroup
from getiajob.services.cvs import update_profile
from getiajob.web.layout import CONTRACT_OPTIONS, parse_list

SENIORITY = {
    "junior": "Junior",
    "mid": "Confirmé",
    "senior": "Senior",
    "lead": "Lead",
    "executive": "Dirigeant",
}


def _lines(value: str) -> list[str]:
    return [line.strip(" •-\t") for line in value.splitlines() if line.strip(" •-\t")]


def _opt(value: str) -> str | None:
    return value.strip() or None


def _save(user_id: int, cv_id: int, profile: CVProfile, message: str) -> None:
    update_profile(user_id, cv_id, profile)
    st.toast(message, icon=":material/check_circle:")
    st.rerun()


def _replace(items: list, index: int | None, item) -> list:
    """Remplace l'élément `index`, ou l'ajoute en tête si `index` vaut None."""
    items = list(items)
    if index is None:
        items.insert(0, item)
    else:
        items[index] = item
    return items


def _item_form(key: str, fields: Callable[[], object], is_new: bool) -> tuple[object | None, bool]:
    """Formulaire d'un élément de liste : renvoie (élément saisi si « Enregistrer », suppression ?)."""
    with st.form(key, border=False):
        built = fields()
        with st.container(horizontal=True, gap="small"):
            saved = st.form_submit_button(
                "Ajouter" if is_new else "Enregistrer", type="primary", icon=":material/save:"
            )
            deleted = False if is_new else st.form_submit_button("Supprimer", icon=":material/delete:")
    if saved and built is None:
        st.error("Renseigne au moins le champ obligatoire (*).")
    return (built if saved else None), deleted


# --------------------------------------------------------------------------- #
# Identité
# --------------------------------------------------------------------------- #


def edit_identity(user_id: int, cv_id: int, p: CVProfile) -> None:
    with st.form(f"edit-id-{cv_id}"):
        c1, c2 = st.columns(2)
        full_name = c1.text_input("Nom complet", value=p.full_name or "")
        headline = c2.text_input("Titre", value=p.headline)
        c1, c2 = st.columns(2)
        levels = [None, *SENIORITY]
        seniority = c1.selectbox(
            "Niveau",
            levels,
            index=levels.index(p.seniority) if p.seniority in levels else 0,
            format_func=lambda v: SENIORITY.get(v, "Non précisé") if v else "Non précisé",
        )
        years = c2.number_input(
            "Années d'expérience", value=p.years_of_experience, min_value=0.0, step=1.0, placeholder="—"
        )
        summary = st.text_area("Résumé", value=p.summary, height=100)
        c = p.contact
        c1, c2, c3 = st.columns(3)
        location = c1.text_input("Localisation", value=c.location or "")
        email = c2.text_input("Email", value=c.email or "")
        phone = c3.text_input("Téléphone", value=c.phone or "")
        c1, c2, c3 = st.columns(3)
        linkedin = c1.text_input("LinkedIn", value=c.linkedin or "")
        github = c2.text_input("GitHub", value=c.github or "")
        website = c3.text_input("Site web", value=c.website or "")
        targets = st.text_input(
            "Postes ciblés", value=", ".join(p.target_job_titles), help="Séparés par des virgules"
        )
        if st.form_submit_button("Enregistrer", type="primary", icon=":material/save:"):
            contact = c.model_copy(
                update={
                    "location": _opt(location),
                    "email": _opt(email),
                    "phone": _opt(phone),
                    "linkedin": _opt(linkedin),
                    "github": _opt(github),
                    "website": _opt(website),
                }
            )
            updated = p.model_copy(
                update={
                    "full_name": _opt(full_name),
                    "headline": headline.strip(),
                    "seniority": seniority,
                    "years_of_experience": years,
                    "summary": summary.strip(),
                    "contact": contact,
                    "target_job_titles": parse_list(targets),
                }
            )
            _save(user_id, cv_id, updated, "Identité mise à jour")


# --------------------------------------------------------------------------- #
# Expériences
# --------------------------------------------------------------------------- #


def _experience_fields(e: Experience, key: str) -> Callable[[], Experience | None]:
    def fields() -> Experience | None:
        c1, c2 = st.columns(2)
        title = c1.text_input("Intitulé du poste *", value=e.title, key=f"{key}-t")
        company = c2.text_input("Entreprise", value=e.company or "", key=f"{key}-c")
        c1, c2, c3, c4 = st.columns(4)
        location = c1.text_input("Lieu", value=e.location or "", key=f"{key}-l")
        start = c2.text_input("Début", value=e.start or "", placeholder="ex. 2023", key=f"{key}-s")
        end = c3.text_input("Fin", value=e.end or "", placeholder="ex. présent", key=f"{key}-e")
        duration = c4.text_input("Durée", value=e.duration or "", placeholder="ex. 6 mois", key=f"{key}-d")
        options = list(dict.fromkeys([*CONTRACT_OPTIONS, *([e.contract_type] if e.contract_type else [])]))
        contract = st.pills(
            "Type de contrat",
            options,
            default=e.contract_type if e.contract_type in options else "CDI",
            key=f"{key}-k",
        )
        description = st.text_area("Mission principale", value=e.description or "", height=80, key=f"{key}-m")
        achievements = st.text_area(
            "Réalisations (une par ligne)", value="\n".join(e.achievements), height=120, key=f"{key}-a"
        )
        techs = st.text_input(
            "Technologies (séparées par des virgules)", value=", ".join(e.technologies), key=f"{key}-x"
        )
        if not title.strip():
            return None
        return Experience(
            title=title.strip(),
            company=_opt(company),
            location=_opt(location),
            start=_opt(start),
            end=_opt(end),
            duration=_opt(duration),
            contract_type=contract,
            description=_opt(description),
            achievements=_lines(achievements),
            technologies=parse_list(techs),
        )

    return fields


def edit_experiences(user_id: int, cv_id: int, p: CVProfile) -> None:
    st.caption("Corrige ou complète chaque expérience. L'ordre est celui du CV (la plus récente en premier).")
    for i, e in enumerate(p.experiences):
        label = " — ".join(x for x in (e.title, e.company) if x)
        with st.expander(f"{label}  ·  {e.contract_type or ''}", icon=":material/work:"):
            built, deleted = _item_form(f"exp-{cv_id}-{i}", _experience_fields(e, f"exp-{cv_id}-{i}"), False)
            if deleted:
                exps = [x for j, x in enumerate(p.experiences) if j != i]
                _save(user_id, cv_id, p.model_copy(update={"experiences": exps}), "Expérience supprimée")
            elif built is not None:
                exps = _replace(p.experiences, i, built)
                _save(user_id, cv_id, p.model_copy(update={"experiences": exps}), "Expérience mise à jour")
    with st.expander("Ajouter une expérience", icon=":material/add:"):
        built, _ = _item_form(
            f"exp-{cv_id}-new", _experience_fields(Experience(title=""), f"exp-{cv_id}-new"), True
        )
        if built is not None:
            exps = _replace(p.experiences, None, built)
            _save(user_id, cv_id, p.model_copy(update={"experiences": exps}), "Expérience ajoutée")


# --------------------------------------------------------------------------- #
# Formation et certifications
# --------------------------------------------------------------------------- #


def _education_fields(ed: Education, key: str) -> Callable[[], Education | None]:
    def fields() -> Education | None:
        c1, c2 = st.columns(2)
        degree = c1.text_input("Diplôme / formation *", value=ed.degree, key=f"{key}-g")
        field = c2.text_input("Spécialité", value=ed.field or "", key=f"{key}-f")
        c1, c2, c3, c4 = st.columns(4)
        school = c1.text_input("Établissement", value=ed.school or "", key=f"{key}-s")
        location = c2.text_input("Lieu", value=ed.location or "", key=f"{key}-l")
        start = c3.text_input("Début", value=ed.start or "", key=f"{key}-b")
        end = c4.text_input("Fin", value=ed.end or "", key=f"{key}-e")
        details = st.text_area(
            "Détails (une ligne par élément)", value="\n".join(ed.details), height=80, key=f"{key}-d"
        )
        if not degree.strip():
            return None
        return Education(
            degree=degree.strip(),
            field=_opt(field),
            school=_opt(school),
            location=_opt(location),
            start=_opt(start),
            end=_opt(end),
            details=_lines(details),
        )

    return fields


def edit_education(user_id: int, cv_id: int, p: CVProfile) -> None:
    for i, ed in enumerate(p.education):
        label = " — ".join(x for x in (ed.degree, ed.school) if x)
        with st.expander(label, icon=":material/school:"):
            built, deleted = _item_form(f"edu-{cv_id}-{i}", _education_fields(ed, f"edu-{cv_id}-{i}"), False)
            if deleted:
                items = [x for j, x in enumerate(p.education) if j != i]
                _save(user_id, cv_id, p.model_copy(update={"education": items}), "Formation supprimée")
            elif built is not None:
                items = _replace(p.education, i, built)
                _save(user_id, cv_id, p.model_copy(update={"education": items}), "Formation mise à jour")
    with st.expander("Ajouter une formation", icon=":material/add:"):
        built, _ = _item_form(
            f"edu-{cv_id}-new", _education_fields(Education(degree=""), f"edu-{cv_id}-new"), True
        )
        if built is not None:
            items = _replace(p.education, None, built)
            _save(user_id, cv_id, p.model_copy(update={"education": items}), "Formation ajoutée")

    with st.form(f"certs-{cv_id}"):
        certs = st.text_area("Certifications (une par ligne)", value="\n".join(p.certifications), height=100)
        if st.form_submit_button("Enregistrer les certifications", icon=":material/save:"):
            _save(
                user_id,
                cv_id,
                p.model_copy(update={"certifications": _lines(certs)}),
                "Certifications mises à jour",
            )


# --------------------------------------------------------------------------- #
# Projets
# --------------------------------------------------------------------------- #


def _project_fields(pr: Project, key: str) -> Callable[[], Project | None]:
    def fields() -> Project | None:
        c1, c2 = st.columns(2)
        name = c1.text_input("Nom du projet *", value=pr.name, key=f"{key}-n")
        url = c2.text_input("Lien", value=pr.url or "", placeholder="https://…", key=f"{key}-u")
        description = st.text_area("Description", value=pr.description or "", height=90, key=f"{key}-d")
        techs = st.text_input(
            "Technologies (séparées par des virgules)", value=", ".join(pr.technologies), key=f"{key}-x"
        )
        if not name.strip():
            return None
        return Project(
            name=name.strip(), url=_opt(url), description=_opt(description), technologies=parse_list(techs)
        )

    return fields


def edit_projects(user_id: int, cv_id: int, p: CVProfile) -> None:
    for i, pr in enumerate(p.projects):
        with st.expander(pr.name, icon=":material/rocket_launch:"):
            built, deleted = _item_form(f"prj-{cv_id}-{i}", _project_fields(pr, f"prj-{cv_id}-{i}"), False)
            if deleted:
                items = [x for j, x in enumerate(p.projects) if j != i]
                _save(user_id, cv_id, p.model_copy(update={"projects": items}), "Projet supprimé")
            elif built is not None:
                items = _replace(p.projects, i, built)
                _save(user_id, cv_id, p.model_copy(update={"projects": items}), "Projet mis à jour")
    with st.expander("Ajouter un projet", icon=":material/add:"):
        built, _ = _item_form(f"prj-{cv_id}-new", _project_fields(Project(name=""), f"prj-{cv_id}-new"), True)
        if built is not None:
            items = _replace(p.projects, None, built)
            _save(user_id, cv_id, p.model_copy(update={"projects": items}), "Projet ajouté")


# --------------------------------------------------------------------------- #
# Compétences, langues, centres d'intérêt (tableaux éditables)
# --------------------------------------------------------------------------- #


def edit_skills(user_id: int, cv_id: int, p: CVProfile, groups: list[tuple[str, list[str]]]) -> None:
    st.caption(
        "Une ligne par catégorie ; compétences séparées par des virgules. Ajoute ou supprime des lignes."
    )
    with st.form(f"skills-{cv_id}"):
        table = st.data_editor(
            pd.DataFrame(
                [(name, ", ".join(items)) for name, items in groups], columns=["Catégorie", "Compétences"]
            ),
            num_rows="dynamic",
            width="stretch",
            key=f"skills-table-{cv_id}",
            column_config={
                "Catégorie": st.column_config.TextColumn(width="small", required=True),
                "Compétences": st.column_config.TextColumn(width="large"),
            },
        )
        soft = st.text_area(
            "Savoir-être (séparés par des virgules)", value=", ".join(p.soft_skills), height=80
        )
        if st.form_submit_button("Enregistrer les compétences", type="primary", icon=":material/save:"):
            skills = [
                SkillGroup(category=str(row["Catégorie"]).strip(), items=parse_list(str(row["Compétences"])))
                for row in table.fillna("").to_dict("records")
                if str(row["Catégorie"]).strip()
            ]
            updated = p.model_copy(update={"skills": skills, "soft_skills": parse_list(soft)})
            _save(user_id, cv_id, updated, "Compétences mises à jour")


def edit_languages_interests(user_id: int, cv_id: int, p: CVProfile) -> None:
    with st.form(f"langs-{cv_id}"):
        table = st.data_editor(
            pd.DataFrame(
                [(lang.language, lang.level or "") for lang in p.languages], columns=["Langue", "Niveau"]
            ),
            num_rows="dynamic",
            width="stretch",
            key=f"langs-table-{cv_id}",
            column_config={
                "Langue": st.column_config.TextColumn(required=True),
                "Niveau": st.column_config.TextColumn(help="ex. Natif, C1, Courant, B2…"),
            },
        )
        interests = st.text_input(
            "Centres d'intérêt (séparés par des virgules)", value=", ".join(p.interests)
        )
        if st.form_submit_button("Enregistrer", type="primary", icon=":material/save:"):
            languages = [
                Language(language=str(row["Langue"]).strip(), level=_opt(str(row["Niveau"])))
                for row in table.fillna("").to_dict("records")
                if str(row["Langue"]).strip()
            ]
            updated = p.model_copy(update={"languages": languages, "interests": parse_list(interests)})
            _save(user_id, cv_id, updated, "Langues et centres d'intérêt mis à jour")
