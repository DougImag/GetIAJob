"""Mes candidatures : offres auxquelles j'ai postulé, statut, prochaine étape et suivi des entretiens."""

from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

from getiajob.scraping.base import SOURCE_LABELS
from getiajob.services.applications import (
    FOLLOW_UP_DAYS,
    NOTE_ICONS,
    NOTE_KINDS,
    STAGES,
    STATUSES,
    Application,
    ApplicationError,
    Note,
    add_manual,
    add_note,
    delete_application,
    delete_note,
    list_applications,
    set_next_step,
    set_status,
    upcoming,
    update_details,
    update_note,
)
from getiajob.web.layout import score_color
from getiajob.web.state import APPLICATION_FOCUS_KEY, is_read_only, require_user
from getiajob.web.views.cv import ico

EDITABLE_KINDS = [k for k in NOTE_KINDS if k != "status"]
MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
STATUS_HEX = {
    "applied": "#2563eb",
    "interview": "#7c3aed",
    "offer": "#ea580c",
    "accepted": "#16a34a",
    "rejected": "#dc2626",
    "withdrawn": "#6b7280",
}
SCORE_HEX = {"green": "#16a34a", "orange": "#ea580c", "red": "#dc2626"}
SOURCE_HEX = {"linkedin": "#0a66c2", "francetravail": "#e2001a", "adzuna": "#16a34a", "apec": "#7c3aed"}
SORTS = {
    "recent": "Candidature la plus récente",
    "oldest": "Candidature la plus ancienne",
    "score": "Meilleur score",
    "next": "Prochaine étape",
}

CSS = """
<style>
  .gj-ak { display: grid; grid-template-columns: repeat(5, 1fr); gap: .7rem; margin: .4rem 0 1rem; }
  @media (max-width: 900px) { .gj-ak { grid-template-columns: repeat(2, 1fr); } }
  .gj-ak-c { padding: .8rem 1rem; border-radius: 14px; background: var(--gj-surface);
    border: 1px solid var(--gj-line); }
  .gj-ak-v { font-size: 1.5rem; font-weight: 800; color: var(--gj-head); line-height: 1.1; }
  .gj-ak-l { font-size: .78rem; color: var(--gj-muted); margin-top: .15rem; }

  .gj-ap { display: flex; gap: 1rem; align-items: flex-start; }
  .gj-ap-date { flex-shrink: 0; width: 4.6rem; text-align: center; border-radius: 14px; overflow: hidden;
    border: 1px solid var(--gj-line); background: var(--gj-surface); }
  .gj-ap-date .m { background: var(--sc); color: #fff; font-size: .72rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: .04em; padding: .18rem 0; }
  .gj-ap-date .d { font-size: 1.7rem; font-weight: 800; color: var(--gj-head); line-height: 1.15;
    padding-top: .15rem; }
  .gj-ap-date .y { font-size: .72rem; color: var(--gj-muted); }
  .gj-ap-date .a { font-size: .68rem; color: var(--gj-muted); border-top: 1px solid var(--gj-line);
    margin-top: .25rem; padding: .2rem 0; }
  .gj-ap-ring { flex-shrink: 0; width: 3.8rem; height: 3.8rem; border-radius: 50%; display: grid;
    place-items: center; text-align: center;
    background: radial-gradient(var(--gj-surface) 60%, transparent 62%),
      conic-gradient(var(--c) calc(var(--v) * 1%), var(--gj-line) 0); }
  .gj-ap-ring b { display: block; font-size: 1.1rem; font-weight: 800; color: var(--gj-head);
    line-height: 1; }
  .gj-ap-ring small { display: block; font-size: .58rem; color: var(--gj-muted); }
  .gj-ap-body { flex: 1; min-width: 0; }
  .gj-ap-title { font-weight: 700; font-size: 1.05rem; color: var(--gj-head); }
  .gj-ap-title a { color: inherit; text-decoration: none; }
  .gj-ap-title a:hover { color: var(--gj-primary); text-decoration: underline; }
  .gj-ap-meta span, .gj-ap-pill { display: inline-flex; align-items: center; gap: .25rem; }
  .gj-ap-meta { display: flex; flex-wrap: wrap; gap: .25rem .9rem; font-size: .84rem; color: var(--gj-muted);
    margin-top: .2rem; align-items: center; }
  .gj-ap-src { font-size: .7rem; font-weight: 700; padding: .05rem .5rem; border-radius: 999px;
    color: var(--sc); background: color-mix(in srgb, var(--sc) 12%, transparent); }
  .gj-ap-sum { font-size: .86rem; color: var(--gj-text); margin-top: .4rem; display: -webkit-box;
    -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .gj-ap-status { display: inline-block; font-size: .75rem; font-weight: 700; padding: .15rem .6rem;
    border-radius: 999px; color: var(--sc); background: color-mix(in srgb, var(--sc) 13%, transparent); }

  .gj-steps { display: flex; align-items: center; margin-top: .65rem; max-width: 30rem; }
  .gj-step { display: flex; flex-direction: column; align-items: center; gap: .2rem; font-size: .68rem;
    color: var(--gj-muted); min-width: 4.2rem; }
  .gj-step i { width: .8rem; height: .8rem; border-radius: 50%; background: var(--gj-line);
    border: 2px solid var(--gj-line); }
  .gj-step.done i { background: var(--sc); border-color: var(--sc); }
  .gj-step.now i { background: var(--gj-surface); border-color: var(--sc);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--sc) 25%, transparent); }
  .gj-step.done, .gj-step.now { color: var(--gj-text); font-weight: 600; }
  .gj-step-bar { flex: 1; height: 2px; background: var(--gj-line); margin: 0 -.9rem .95rem; }
  .gj-step-bar.done { background: var(--sc); }

  .gj-ap-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .55rem; }
  .gj-ap-pill { font-size: .8rem; padding: .25rem .65rem; border-radius: 10px; }
  .gj-ap-pill.next { background: rgba(124, 58, 237, .1); color: #7c3aed; }
  .gj-ap-pill.late { background: rgba(220, 38, 38, .1); color: #dc2626; }
  .gj-ap-pill.wait { background: rgba(234, 88, 12, .1); color: #ea580c; }
  .gj-tl-date { font-size: .78rem; color: var(--gj-muted); white-space: nowrap; }
  @media (max-width: 640px) { .gj-ap { flex-wrap: wrap; } .gj-ap-body { flex-basis: 100%; } }
</style>
"""


def _ago(d: date) -> str:
    n = (date.today() - d).days
    if n <= 0:
        return "aujourd'hui"
    if n == 1:
        return "hier"
    return f"il y a {n} j"


def _when(d: date) -> str:
    n = (d - date.today()).days
    if n == 0:
        return "aujourd'hui"
    if n == 1:
        return "demain"
    if n > 1:
        return f"dans {n} j"
    return f"il y a {-n} j"


def _run(action, message: str | None = None) -> None:
    try:
        action()
    except ApplicationError as exc:
        st.error(str(exc))
        return
    if message:
        st.toast(message, icon=":material/check_circle:")
    st.rerun()


# --------------------------------------------------------------------------- #
# Carte d'une candidature
# --------------------------------------------------------------------------- #


def _date_tile(a: Application) -> str:
    d = a.applied_on
    return (
        f'<div class="gj-ap-date" style="--sc:{STATUS_HEX.get(a.status, "#6b7280")}" '
        f'title="Postulé le {d:%d/%m/%Y}"><div class="m">{MONTHS[d.month - 1]}</div>'
        f'<div class="d">{d.day}</div><div class="y">{d.year}</div><div class="a">{_ago(d)}</div></div>'
    )


def _score_ring(a: Application) -> str:
    if a.score is None:
        return (
            '<div class="gj-ap-ring" style="--v:0;--c:var(--gj-line)" title="Offre non notée par l\'IA">'
            "<div><b>–</b><small>score</small></div></div>"
        )
    color = SCORE_HEX[score_color(a.score)]
    return (
        f'<div class="gj-ap-ring" style="--v:{a.score:.0f};--c:{color}" '
        'title="Score de l\'offre pour ton CV">'
        f"<div><b>{a.score:.0f}</b><small>/ 100</small></div></div>"
    )


def _stepper(a: Application) -> str:
    """Frise Candidature → Entretiens → Offre → Décision.

    Une candidature refusée ou abandonnée ne « franchit » que les étapes réellement atteintes
    (entretiens s'il y en a eu) ; l'issue s'affiche sur la dernière étape.
    """
    color = STATUS_HEX.get(a.status, "#6b7280")
    last = len(STAGES) - 1
    labels = list(STAGES)
    if a.stage == last:
        labels[-1] = a.status_label
    reached = (1 if a.interviews else 0) if a.status in ("rejected", "withdrawn") else a.stage
    parts = []
    for i, label in enumerate(labels):
        if a.status == "accepted" or i < min(reached + 1, a.stage):
            state = "done"
        elif i == a.stage:
            state = "now"
        else:
            state = ""
        if i:
            parts.append(f'<div class="gj-step-bar{" done" if i <= reached else ""}"></div>')
        parts.append(f'<div class="gj-step {state}"><i></i>{escape(label)}</div>')
    return f'<div class="gj-steps" style="--sc:{color}">{"".join(parts)}</div>'


def _card_html(a: Application) -> str:
    title = escape(a.title)
    if a.url:
        title = f'<a href="{escape(a.url, quote=True)}" target="_blank" rel="noopener">{title}</a>'
    meta = []
    if a.company:
        meta.append(f"<span>{ico('building', 13)} {escape(a.company)}</span>")
    if a.location:
        meta.append(f"<span>{ico('map-pin', 13)} {escape(a.location)}</span>")
    if a.salary:
        meta.append(f"<span>{ico('euro', 13)} {escape(a.salary)}</span>")
    if a.source:
        meta.append(
            f'<span class="gj-ap-src" style="--sc:{SOURCE_HEX.get(a.source, "#6b7280")}">'
            f"{escape(SOURCE_LABELS.get(a.source, a.source))}</span>"
        )
    elif a.job_id is None:
        meta.append('<span class="gj-ap-src" style="--sc:#6b7280">Saisie manuelle</span>')
    status = (
        f'<span class="gj-ap-status" style="--sc:{STATUS_HEX.get(a.status, "#6b7280")}">'
        f"{escape(a.status_label)}</span>"
    )
    meta.append(status)

    pills = []
    if a.active and (a.next_step or a.next_step_on):
        late = a.next_step_on is not None and a.next_step_on < date.today()
        when = f" — {a.next_step_on:%d/%m} ({_when(a.next_step_on)})" if a.next_step_on else ""
        pills.append(
            f'<span class="gj-ap-pill {"late" if late else "next"}">{ico("calendar", 13)} '
            f"{escape(a.next_step or 'Prochaine étape')}{when}</span>"
        )
    if a.needs_follow_up():
        pills.append(
            f'<span class="gj-ap-pill wait">{ico("clock", 13)} Sans nouvelles depuis '
            f"{(date.today() - a.last_activity).days} j — une relance ?</span>"
        )
    if a.interviews:
        pills.append(
            f'<span class="gj-ap-pill next">{ico("phone", 13)} {a.interviews} échange(s) / '
            "entretien(s)</span>"
        )
    summary = f'<div class="gj-ap-sum">{escape(a.summary)}</div>' if a.summary else ""
    return (
        f'<div class="gj-ap">{_date_tile(a)}{_score_ring(a)}<div class="gj-ap-body">'
        f'<div class="gj-ap-title">{title}</div><div class="gj-ap-meta">{"".join(meta)}</div>'
        f'{summary}{_stepper(a)}<div class="gj-ap-pills">{"".join(pills)}</div></div></div>'
    )


def _note_row(user_id: int, n: Note) -> None:
    c1, c2, c3 = st.columns([1.3, 6, 0.9], vertical_alignment="top")
    c1.markdown(f'<div class="gj-tl-date">{n.happened_on:%d/%m/%Y}</div>', unsafe_allow_html=True)
    text = f"{NOTE_ICONS.get(n.kind, '')} **{n.label}**"
    if n.comment:
        text += "  \n" + n.comment.replace("\n", "  \n")
    c2.markdown(text)
    with c3.popover("", icon=":material/more_horiz:", help="Modifier ou supprimer"):
        with st.form(f"note-edit-{n.id}", border=False):
            kind = (
                n.kind
                if n.kind == "status"
                else st.selectbox(
                    "Type", EDITABLE_KINDS, index=EDITABLE_KINDS.index(n.kind), format_func=NOTE_KINDS.get
                )
            )
            on = st.date_input("Date", n.happened_on, format="DD/MM/YYYY")
            comment = st.text_area("Commentaire", n.comment or "", height=100)
            if st.form_submit_button("Enregistrer", type="primary", icon=":material/save:"):
                _run(lambda: update_note(user_id, n.id, kind, on, comment), "Étape modifiée")
        if st.button(
            "Supprimer cette étape", icon=":material/delete:", key=f"note-del-{n.id}", type="tertiary"
        ):
            _run(lambda: delete_note(user_id, n.id), "Étape supprimée")


def _add_note_form(user_id: int, a: Application) -> None:
    with st.form(f"note-add-{a.id}", clear_on_submit=True):
        st.markdown("**Ajouter une étape**")
        c1, c2 = st.columns([2, 1])
        kind = c1.selectbox("Type", EDITABLE_KINDS, format_func=NOTE_KINDS.get, key=f"nk-{a.id}")
        on = c2.date_input("Date", date.today(), format="DD/MM/YYYY", key=f"nd-{a.id}")
        comment = st.text_area(
            "Commentaire",
            placeholder="Interlocuteurs, questions posées, impressions, points à préparer…",
            height=100,
            key=f"nc-{a.id}",
        )
        if st.form_submit_button("Ajouter", type="primary", icon=":material/add:"):
            _run(lambda: add_note(user_id, a.id, kind, on, comment), "Étape ajoutée")


def _next_step_form(user_id: int, a: Application) -> None:
    with st.form(f"next-{a.id}", border=False):
        label = st.text_input(
            "Prochaine étape", a.next_step or "", placeholder="ex. Entretien technique avec le CTO"
        )
        on = st.date_input("Date", value=a.next_step_on, format="DD/MM/YYYY")
        c1, c2 = st.columns(2)
        if c1.form_submit_button("Enregistrer", type="primary", icon=":material/event:"):
            _run(lambda: set_next_step(user_id, a.id, label, on), "Prochaine étape enregistrée")
        if c2.form_submit_button("Effacer", icon=":material/event_busy:"):
            _run(lambda: set_next_step(user_id, a.id, None, None), "Prochaine étape effacée")


def _details_form(user_id: int, a: Application) -> None:
    with st.form(f"details-{a.id}", border=False):
        title = st.text_input("Poste", a.title)
        company = st.text_input("Entreprise", a.company or "")
        location = st.text_input("Lieu", a.location or "")
        url = st.text_input("Lien de l'offre", a.url or "")
        on = st.date_input("Date de candidature", a.applied_on, format="DD/MM/YYYY")
        if st.form_submit_button("Enregistrer", type="primary", icon=":material/save:"):
            _run(
                lambda: update_details(
                    user_id, a.id, title=title, company=company, location=location, url=url, applied_on=on
                ),
                "Candidature mise à jour",
            )


def _card(user_id: int, a: Application, focus: bool) -> None:
    with st.container(border=True, key=f"gj_app_{a.id}"):
        c1, c2 = st.columns([5, 1.6], vertical_alignment="top")
        c1.markdown(_card_html(a), unsafe_allow_html=True)
        with c2:
            status = st.selectbox(
                "Statut",
                list(STATUSES),
                index=list(STATUSES).index(a.status),
                format_func=STATUSES.get,
                key=f"status-{a.id}",
                disabled=is_read_only(),
            )
            if status != a.status:
                _run(lambda: set_status(user_id, a.id, status), f"Statut : {STATUSES[status]}")

        with st.expander(f"Suivi ({len(a.notes)} étape(s))", icon=":material/timeline:", expanded=focus):
            if not a.notes:
                st.caption("Aucune étape pour l'instant : ajoute tes entretiens, relances et impressions.")
            for n in a.notes:
                _note_row(user_id, n)
            _add_note_form(user_id, a)
        with st.container(horizontal=True, gap="small"):
            if a.url:
                st.link_button("Voir l'offre", a.url, icon=":material/open_in_new:")
            if a.active:
                with st.popover("Prochaine étape", icon=":material/event:"):
                    _next_step_form(user_id, a)
            with st.popover("Modifier", icon=":material/edit:"):
                _details_form(user_id, a)
            with st.popover("", icon=":material/delete:", help="Supprimer la candidature"):
                st.markdown("Supprimer cette candidature et tout son suivi ? Cette action est définitive.")
                if st.button("Supprimer", type="primary", key=f"app-del-{a.id}"):
                    _run(lambda: delete_application(user_id, a.id), "Candidature supprimée")


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #


def _manual_form(user_id: int) -> None:
    with st.popover("Ajouter une candidature", icon=":material/add:"):
        st.caption("Pour une offre trouvée ailleurs que dans GetIAJob.")
        with st.form("app-manual", border=False, clear_on_submit=True):
            title = st.text_input("Poste *")
            company = st.text_input("Entreprise")
            location = st.text_input("Lieu")
            url = st.text_input("Lien de l'offre")
            on = st.date_input("Date de candidature", date.today(), format="DD/MM/YYYY")
            comment = st.text_area("Commentaire", height=80)
            if st.form_submit_button("Ajouter", type="primary", icon=":material/add:"):
                _run(
                    lambda: add_manual(
                        user_id,
                        title,
                        company=company,
                        location=location,
                        url=url,
                        applied_on=on,
                        comment=comment,
                    ),
                    "Candidature ajoutée",
                )


def _kpis(apps: list[Application]) -> None:
    active = [a for a in apps if a.active]
    answered = [a for a in apps if a.status != "applied" or a.interviews]
    scored = [a.score for a in apps if a.score is not None]
    cards = [
        (len(apps), "candidature(s)"),
        (len(active), "en cours"),
        (sum(a.interviews for a in apps), "échange(s) / entretien(s)"),
        (f"{len(answered) / len(apps):.0%}" if apps else "–", "taux de réponse"),
        (f"{sum(scored) / len(scored):.0f}" if scored else "–", "score moyen / 100"),
    ]
    html = "".join(
        f'<div class="gj-ak-c"><div class="gj-ak-v">{v}</div><div class="gj-ak-l">{label}</div></div>'
        for v, label in cards
    )
    st.markdown(f'<div class="gj-ak">{html}</div>', unsafe_allow_html=True)


def _agenda(apps: list[Application]) -> None:
    soon = upcoming(apps)
    waiting = [a for a in apps if a.needs_follow_up()]
    if not soon and not waiting:
        return
    c1, c2 = st.columns(2) if soon and waiting else (st.container(), None)
    if soon:
        lines = "\n".join(
            f"- **{a.next_step_on:%d/%m}** ({_when(a.next_step_on)}) — {a.next_step or 'Prochaine étape'} · "
            f"{a.title}{f' — {a.company}' if a.company else ''}"
            for a in soon[:5]
        )
        c1.info(f"**À venir**\n{lines}", icon=":material/event_upcoming:")
    if waiting:
        target = c2 if c2 is not None else c1
        lines = "\n".join(
            f"- {a.title}{f' — {a.company}' if a.company else ''} · postulé {_ago(a.applied_on)}"
            for a in waiting[:5]
        )
        target.warning(
            f"**À relancer** (sans nouvelles depuis {FOLLOW_UP_DAYS} j ou plus)\n{lines}",
            icon=":material/schedule_send:",
        )


def _sorted(apps: list[Application], key: str) -> list[Application]:
    if key == "oldest":
        return sorted(apps, key=lambda a: (a.applied_on, a.id))
    if key == "score":
        return sorted(apps, key=lambda a: (a.score is None, -(a.score or 0)))
    if key == "next":
        return sorted(apps, key=lambda a: (a.next_step_on is None, a.next_step_on or date.max))
    return sorted(apps, key=lambda a: (a.applied_on, a.id), reverse=True)


def render() -> None:
    user = require_user()
    st.html(CSS)
    apps = list_applications(user.id)
    focus = st.session_state.pop(APPLICATION_FOCUS_KEY, None)

    c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
    c1.markdown("## Mes candidatures")
    with c2:
        _manual_form(user.id)

    if not apps:
        st.info(
            "Aucune candidature pour l'instant. Dans « Mes recherches », clique sur **J'ai postulé** sous "
            "une offre pour la retrouver ici et suivre tes entretiens — ou ajoute une candidature trouvée "
            "ailleurs.",
            icon=":material/send:",
        )
        return

    _kpis(apps)
    _agenda(apps)

    groups = {
        "active": ("En cours", lambda a: a.active),
        "closed": ("Clôturées", lambda a: not a.active),
        "all": ("Toutes", lambda a: True),
    }
    sizes = {k: sum(1 for a in apps if keep(a)) for k, (_, keep) in groups.items()}
    f1, f2, f3 = st.columns([2.2, 1.8, 2], vertical_alignment="bottom")
    view = f1.segmented_control(
        "Afficher",
        list(groups),
        format_func=lambda k: f"{groups[k][0]} ({sizes[k]})",
        default="active",
        key="app_view",
    )
    order = f2.selectbox("Trier par", list(SORTS), format_func=SORTS.get, key="app_sort")
    query = f3.text_input("Rechercher", placeholder="Poste ou entreprise…", icon=":material/search:")
    keep = groups[view or "all"][1]
    q = query.strip().lower()
    shown = [
        a
        for a in _sorted(apps, order)
        if (a.id == focus or keep(a)) and (not q or q in a.title.lower() or q in (a.company or "").lower())
    ]
    if not shown:
        st.caption("Aucune candidature ne correspond aux filtres.")
    for a in shown:
        _card(user.id, a, focus == a.id)
